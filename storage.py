"""SQLite persistence layer.

Stores notebooks, documents, per-page metadata, evidence elements,
FTS5-searchable chunks, background jobs, and chat history.

Connection lifecycle: every function uses the ``db()`` context manager, which
commits on success, rolls back on exception, and ALWAYS closes the connection
in ``finally``. sqlite3.Connection's own context manager only handles
transactions — it never closes — so it is not relied upon anywhere.
"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    name TEXT NOT NULL,            -- original filename (display only, never a path)
    stored_path TEXT NOT NULL,     -- internal safe path
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|indexing|ready|error
    error TEXT,
    selected INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    UNIQUE (notebook_id, sha256)
);

CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,          -- 1-based PDF page position
    printed_page_label TEXT,               -- label from the PDF, may differ
    image_path TEXT,                       -- rendered page image
    image_width INTEGER,                   -- rendered image pixels
    image_height INTEGER,
    page_width_pts REAL,                   -- PDF page size in points
    page_height_pts REAL,
    rotation INTEGER NOT NULL DEFAULT 0,   -- page rotation in degrees
    has_drawings INTEGER NOT NULL DEFAULT 0,  -- vector graphics present
    crop_x0 REAL NOT NULL DEFAULT 0,       -- CropBox origin (unrotated pts)
    crop_y0 REAL NOT NULL DEFAULT 0,
    is_scanned INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0,
    extraction_warnings TEXT,              -- JSON list
    UNIQUE (document_id, page_number)
);

CREATE TABLE IF NOT EXISTS elements (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    section TEXT,                          -- nearest visible heading, or NULL
    content_type TEXT NOT NULL,            -- text|table|image_region|ocr|visual
    element_index INTEGER NOT NULL,        -- order within page
    text TEXT NOT NULL,
    bbox TEXT,                             -- JSON [x0,y0,x1,y1] in PDF points or NULL
    extraction_method TEXT NOT NULL,       -- pymupdf_text|pymupdf_table|qwen_visual|none
    warnings TEXT                          -- JSON list
);
CREATE INDEX IF NOT EXISTS idx_elements_doc ON elements(document_id, page_number);

CREATE VIRTUAL TABLE IF NOT EXISTS elements_fts USING fts5(
    text, content='elements', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS elements_ai AFTER INSERT ON elements BEGIN
    INSERT INTO elements_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS elements_ad AFTER DELETE ON elements BEGIN
    INSERT INTO elements_fts(elements_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                -- index_document|answer
    notebook_id TEXT,
    document_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|error
    owner TEXT,                        -- worker token holding a running job
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    payload TEXT,                      -- JSON input (incl. source snapshot)
    result TEXT,                       -- JSON output
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                -- user|assistant
    content TEXT NOT NULL,             -- JSON for assistant (structured answer)
    job_id TEXT,                       -- set for job-produced messages (dedup)
    created_at REAL NOT NULL
);
"""

# Objects that depend on MIGRATED columns must be created only after
# migrations ran (a legacy DB lacks chat_messages.job_id until v2).
POST_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_job_role
    ON chat_messages(job_id, role) WHERE job_id IS NOT NULL;
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# --- schema versioning ---------------------------------------------------------
# v1: original schema (no page dims, no job owner, no chat job_id)
# v2: + pages.image_width/height, page_width/height_pts; jobs.owner;
#     chat_messages.job_id (+ unique job/role index)
# v3: + pages.rotation, pages.has_drawings
# v4: + pages.crop_x0, pages.crop_y0 (CropBox origin for highlight mapping)
SCHEMA_VERSION = 4

MIGRATIONS: dict[int, list[str]] = {
    2: [
        "ALTER TABLE pages ADD COLUMN image_width INTEGER",
        "ALTER TABLE pages ADD COLUMN image_height INTEGER",
        "ALTER TABLE pages ADD COLUMN page_width_pts REAL",
        "ALTER TABLE pages ADD COLUMN page_height_pts REAL",
        "ALTER TABLE jobs ADD COLUMN owner TEXT",
        "ALTER TABLE chat_messages ADD COLUMN job_id TEXT",
    ],
    3: [
        "ALTER TABLE pages ADD COLUMN rotation INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE pages ADD COLUMN has_drawings INTEGER NOT NULL DEFAULT 0",
    ],
    4: [
        "ALTER TABLE pages ADD COLUMN crop_x0 REAL NOT NULL DEFAULT 0",
        "ALTER TABLE pages ADD COLUMN crop_y0 REAL NOT NULL DEFAULT 0",
    ],
}


def get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or Config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db(db_path: Optional[Path] = None):
    """Yield a connection; commit on success, roll back on error, always close."""
    conn = get_conn(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """Create or migrate the database. Existing user data is never deleted:
    migrations only ADD columns/indexes. A pre-existing DB is upgraded from
    its recorded PRAGMA user_version; a fresh DB is stamped at the current
    version so migrations never re-run against it."""
    with db(db_path) as conn:
        existed = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notebooks'"
        ).fetchone() is not None
        conn.executescript(SCHEMA)
        if not existed:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        else:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            while version < SCHEMA_VERSION:
                version += 1
                for stmt in MIGRATIONS.get(version, []):
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as exc:
                        # Tolerate partially-migrated DBs (column exists).
                        if "duplicate column" not in str(exc).lower():
                            raise
                conn.execute(f"PRAGMA user_version = {version}")
        conn.executescript(POST_SCHEMA)


# --- notebooks ---------------------------------------------------------------

def create_notebook(name: str, db_path=None) -> dict:
    nb_id = new_id("nb")
    with db(db_path) as conn:
        conn.execute(
            "INSERT INTO notebooks (id, name, created_at) VALUES (?,?,?)",
            (nb_id, name.strip() or "Untitled notebook", time.time()),
        )
    return get_notebook(nb_id, db_path)


def list_notebooks(db_path=None) -> list[dict]:
    with db(db_path) as conn:
        rows = conn.execute(
            """SELECT n.*, (SELECT COUNT(*) FROM documents d WHERE d.notebook_id = n.id)
               AS document_count FROM notebooks n ORDER BY n.created_at"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_notebook(nb_id: str, db_path=None) -> Optional[dict]:
    with db(db_path) as conn:
        row = conn.execute("SELECT * FROM notebooks WHERE id=?", (nb_id,)).fetchone()
    return dict(row) if row else None


# --- documents ---------------------------------------------------------------

def find_document_by_hash(notebook_id: str, sha256: str, db_path=None) -> Optional[dict]:
    with db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE notebook_id=? AND sha256=?",
            (notebook_id, sha256),
        ).fetchone()
    return dict(row) if row else None


def create_document(notebook_id: str, name: str, stored_path: str, sha256: str,
                    size_bytes: int, db_path=None) -> dict:
    """Insert a document row. Raises sqlite3.IntegrityError on the
    (notebook_id, sha256) uniqueness boundary — callers handle the race."""
    doc_id = new_id("doc")
    with db(db_path) as conn:
        conn.execute(
            """INSERT INTO documents (id, notebook_id, name, stored_path, sha256,
               size_bytes, status, created_at) VALUES (?,?,?,?,?,?, 'pending', ?)""",
            (doc_id, notebook_id, name, stored_path, sha256, size_bytes, time.time()),
        )
    return get_document(doc_id, db_path)


def get_document(doc_id: str, db_path=None) -> Optional[dict]:
    with db(db_path) as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return dict(row) if row else None


def list_documents(notebook_id: str, db_path=None) -> list[dict]:
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE notebook_id=? ORDER BY created_at",
            (notebook_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_document(doc_id: str, db_path=None, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with db(db_path) as conn:
        conn.execute(f"UPDATE documents SET {cols} WHERE id=?",
                     (*fields.values(), doc_id))


def delete_document_data(doc_id: str, db_path=None) -> None:
    """Remove pages/elements for re-indexing; keeps the document row."""
    with db(db_path) as conn:
        conn.execute("DELETE FROM elements WHERE document_id=?", (doc_id,))
        conn.execute("DELETE FROM pages WHERE document_id=?", (doc_id,))


def set_document_selected(doc_id: str, selected: bool, db_path=None) -> None:
    update_document(doc_id, db_path, selected=1 if selected else 0)


def delete_document(doc_id: str, db_path=None) -> None:
    """Remove the document row entirely (cascades to pages/elements, whose
    FTS entries are removed by the delete trigger). Used to recover from
    incomplete upload publication."""
    with db(db_path) as conn:
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))


def selected_ready_document_ids(notebook_id: str, db_path=None) -> list[str]:
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM documents WHERE notebook_id=? AND selected=1 AND status='ready'",
            (notebook_id,),
        ).fetchall()
    return [r["id"] for r in rows]


# --- pages & elements ----------------------------------------------------------

def create_page(document_id: str, page_number: int, printed_label: Optional[str],
                image_path: Optional[str], image_width: Optional[int],
                image_height: Optional[int], page_width_pts: Optional[float],
                page_height_pts: Optional[float], rotation: int,
                has_drawings: bool, is_scanned: bool,
                char_count: int, warnings: Iterable[str],
                crop_x0: float = 0.0, crop_y0: float = 0.0, db_path=None) -> str:
    page_id = new_id("pg")
    with db(db_path) as conn:
        conn.execute(
            """INSERT INTO pages (id, document_id, page_number, printed_page_label,
               image_path, image_width, image_height, page_width_pts,
               page_height_pts, rotation, has_drawings, crop_x0, crop_y0,
               is_scanned, char_count, extraction_warnings)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (page_id, document_id, page_number, printed_label, image_path,
             image_width, image_height, page_width_pts, page_height_pts,
             rotation, 1 if has_drawings else 0, crop_x0, crop_y0,
             1 if is_scanned else 0, char_count, json.dumps(list(warnings))),
        )
    return page_id


def set_page_warnings(page_id: str, warnings: Iterable[str], db_path=None) -> None:
    with db(db_path) as conn:
        conn.execute("UPDATE pages SET extraction_warnings=? WHERE id=?",
                     (json.dumps(list(warnings)), page_id))


def create_element(page_id: str, document_id: str, page_number: int,
                   section: Optional[str], content_type: str, element_index: int,
                   text: str, bbox: Optional[list], extraction_method: str,
                   warnings: Iterable[str], db_path=None) -> str:
    el_id = new_id("el")
    with db(db_path) as conn:
        conn.execute(
            """INSERT INTO elements (id, document_id, page_id, page_number, section,
               content_type, element_index, text, bbox, extraction_method, warnings)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (el_id, document_id, page_id, page_number, section, content_type,
             element_index, text, json.dumps(bbox) if bbox else None,
             extraction_method, json.dumps(list(warnings))),
        )
    return el_id


def get_page(document_id: str, page_number: int, db_path=None) -> Optional[dict]:
    with db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM pages WHERE document_id=? AND page_number=?",
            (document_id, page_number),
        ).fetchone()
    return dict(row) if row else None


def get_element(el_id: str, db_path=None) -> Optional[dict]:
    with db(db_path) as conn:
        row = conn.execute(
            """SELECT e.*, d.notebook_id, d.name AS document_name
               FROM elements e JOIN documents d ON d.id = e.document_id
               WHERE e.id=?""",
            (el_id,),
        ).fetchone()
    return dict(row) if row else None


def get_elements_by_ids(el_ids: list[str], db_path=None) -> list[dict]:
    if not el_ids:
        return []
    placeholders = ",".join("?" for _ in el_ids)
    with db(db_path) as conn:
        rows = conn.execute(
            f"""SELECT e.*, d.notebook_id, d.name AS document_name
                FROM elements e JOIN documents d ON d.id = e.document_id
                WHERE e.id IN ({placeholders})""",
            el_ids,
        ).fetchall()
    return [dict(r) for r in rows]


def search_elements(notebook_id: str, query: str, document_ids: list[str],
                    limit: int, db_path=None) -> list[dict]:
    """FTS5 keyword search restricted to the given documents of one notebook."""
    if not document_ids:
        return []
    placeholders = ",".join("?" for _ in document_ids)
    with db(db_path) as conn:
        rows = conn.execute(
            f"""SELECT e.*, d.name AS document_name,
                       bm25(elements_fts) AS rank
                FROM elements_fts f
                JOIN elements e ON e.rowid = f.rowid
                JOIN documents d ON d.id = e.document_id
                WHERE elements_fts MATCH ?
                  AND d.notebook_id = ?
                  AND e.document_id IN ({placeholders})
                ORDER BY rank LIMIT ?""",
            (query, notebook_id, *document_ids, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def page_elements(document_id: str, page_number: int, limit: int = 50,
                  db_path=None) -> list[dict]:
    with db(db_path) as conn:
        rows = conn.execute(
            """SELECT e.*, d.name AS document_name FROM elements e
               JOIN documents d ON d.id=e.document_id
               WHERE e.document_id=? AND e.page_number=?
               ORDER BY e.element_index LIMIT ?""",
            (document_id, page_number, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def visual_candidate_pages(notebook_id: str, document_ids: list[str],
                           db_path=None) -> list[dict]:
    """Pages likely to need image analysis: scanned or containing graphics.

    Restricted to the given documents of the given notebook — never widened.
    """
    if not document_ids:
        return []
    placeholders = ",".join("?" for _ in document_ids)
    with db(db_path) as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT p.*, d.name AS document_name FROM pages p
                JOIN documents d ON d.id = p.document_id
                WHERE d.notebook_id=? AND p.document_id IN ({placeholders})
                  AND p.image_path IS NOT NULL
                  AND (p.is_scanned = 1 OR p.has_drawings = 1 OR EXISTS (
                        SELECT 1 FROM elements e
                        WHERE e.document_id = p.document_id
                          AND e.page_number = p.page_number
                          AND e.content_type IN ('image_region', 'table')))
                ORDER BY p.document_id, p.page_number""",
            (notebook_id, *document_ids),
        ).fetchall()
    return [dict(r) for r in rows]


# --- jobs ----------------------------------------------------------------------

def create_job(kind: str, payload: dict, notebook_id=None, document_id=None,
               db_path=None) -> str:
    job_id = new_id("job")
    now = time.time()
    with db(db_path) as conn:
        conn.execute(
            """INSERT INTO jobs (id, kind, notebook_id, document_id, status,
               payload, created_at, updated_at) VALUES (?,?,?,?, 'queued', ?,?,?)""",
            (job_id, kind, notebook_id, document_id, json.dumps(payload), now, now),
        )
    return job_id


def claim_next_queued_job(owner: str, db_path=None) -> Optional[dict]:
    """Atomically claim the oldest queued job for ``owner``.

    The UPDATE ... WHERE status='queued' guard means two competing workers
    cannot both claim the same job: the loser's rowcount is 0.
    """
    with db(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE jobs SET status='running', owner=?, updated_at=? "
            "WHERE id=? AND status='queued'",
            (owner, time.time(), row["id"]),
        )
        if cur.rowcount != 1:
            return None  # lost the race to another worker
        return dict(conn.execute("SELECT * FROM jobs WHERE id=?",
                                 (row["id"],)).fetchone())


def recover_abandoned_jobs(owner: str, db_path=None) -> int:
    """Requeue 'running' jobs NOT owned by this worker.

    Ownership semantics for this single-process prototype: each app process
    generates one worker token at startup. Jobs still 'running' under a
    different token belong to a crashed earlier process (this prototype
    enforces one app process; see README) and are safely requeued. Jobs owned
    by the current token are never touched.
    """
    with db(db_path) as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='queued', owner=NULL, updated_at=?, "
            "message='Requeued after restart' "
            "WHERE status='running' AND (owner IS NULL OR owner != ?)",
            (time.time(), owner),
        )
        return cur.rowcount


def update_job(job_id: str, db_path=None, **fields) -> None:
    fields["updated_at"] = time.time()
    for key in ("payload", "result"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    cols = ", ".join(f"{k}=?" for k in fields)
    with db(db_path) as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def get_job(job_id: str, db_path=None) -> Optional[dict]:
    with db(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def next_queued_job(db_path=None) -> Optional[dict]:
    with db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def list_recent_jobs(notebook_id: str, limit: int = 20, db_path=None) -> list[dict]:
    with db(db_path) as conn:
        rows = conn.execute(
            """SELECT id, kind, document_id, status, progress, message, error, created_at
               FROM jobs WHERE notebook_id=? ORDER BY created_at DESC LIMIT ?""",
            (notebook_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --- chat -----------------------------------------------------------------------

def add_chat_message(notebook_id: str, role: str, content: Any, job_id=None,
                     db_path=None) -> dict:
    """Insert a chat message. When ``job_id`` is given, INSERT OR IGNORE on the
    (job_id, role) unique index prevents duplicate messages on job retries."""
    msg_id = new_id("msg")
    with db(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO chat_messages
               (id, notebook_id, role, content, job_id, created_at)
               VALUES (?,?,?,?,?,?)""",
            (msg_id, notebook_id, role,
             content if isinstance(content, str) else json.dumps(content),
             job_id, time.time()),
        )
    return {"id": msg_id, "role": role, "notebook_id": notebook_id}


def list_chat_messages(notebook_id: str, limit: int = 100, db_path=None) -> list[dict]:
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE notebook_id=? ORDER BY created_at, rowid LIMIT ?",
            (notebook_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
