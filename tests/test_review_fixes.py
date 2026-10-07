"""Regression tests for Codex review findings.

All model-dependent tests here use the MOCKED model (see conftest.py);
they verify pipeline behavior, not real vision inference.
"""
import io
import json
import sqlite3

import pytest

import storage
from services import answer_service, document_service as ds, jobs
from services import model_service, retrieval_service as rs
from tests.conftest import make_pdf


# --- 1. scanned pages with zero text hits reach visual analysis ---------------

def _scanned_pdf() -> bytes:
    import fitz
    d = fitz.open()
    d.new_page()  # no text at all
    data = d.tobytes()
    d.close()
    return data


def test_scanned_page_reaches_visual_analysis_without_text_hits(workspace, mock_model):
    """ROUTING TEST (mocked vision): verifies scanned pages reach the vision
    stage with zero text hits. Does not test real image understanding."""
    nb = storage.create_notebook("scan-nb")
    doc = ds.save_upload(nb["id"], "scan.pdf", _scanned_pdf())
    ds.index_document(doc["id"])

    # Query terms that match NOTHING in any text layer.
    result = answer_service.answer_question(nb["id"], "zzqq unknown terms here")
    assert mock_model["vision"], "scanned page must reach visual analysis"
    assert result["pages_analyzed"], "analyzed pages must be exposed"
    # The mocked vision observation becomes citable visual evidence.
    vis_ids = [e for e in result["citations"].values()
               if e["content_type"] == "visual"]
    assert vis_ids, "visual observation should be registered as evidence"


def test_visual_fallback_adds_graphics_pages_missed_by_text(workspace, mock_model):
    """ROUTING TEST (mocked vision): graphics pages missed by text retrieval
    must still be routed to visual analysis."""
    nb = storage.create_notebook("gfx")
    import fitz
    d = fitz.open()
    p1 = d.new_page()
    p1.insert_text((72, 72), "alpha zebra topic in plain text", fontsize=12)
    p2 = d.new_page()
    p2.insert_text((72, 72), "unrelated page", fontsize=12)
    # embedded image on page 2 -> visual candidate even though text missed it
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10))
    p2.insert_image(fitz.Rect(100, 100, 200, 200), pixmap=pix)
    data = d.tobytes()
    d.close()
    doc = ds.save_upload(nb["id"], "mixed.pdf", data)
    ds.index_document(doc["id"])

    result = answer_service.answer_question(nb["id"], "alpha zebra topic")
    analyzed = {(p["document_id"], p["page_number"]) for p in result["pages_analyzed"]}
    assert (doc["id"], 2) in analyzed, "graphics page missed by text must still be analyzed"
    assert "visual_coverage" in result


def test_coverage_truncation_disclosed(workspace, mock_model, monkeypatch):
    monkeypatch.setattr(answer_service.Config, "MAX_EVIDENCE_PAGES", 1)
    nb = storage.create_notebook("trunc")
    import fitz
    d = fitz.open()
    for _ in range(4):
        d.new_page()  # 4 scanned pages, limit allows 1
    data = d.tobytes()
    d.close()
    doc = ds.save_upload(nb["id"], "scans.pdf", data)
    ds.index_document(doc["id"])

    result = answer_service.answer_question(nb["id"], "zzqq nothing matches")
    assert result["visual_coverage"]["complete"] is False
    assert result["visual_coverage"]["candidate_visual_pages"] == 4
    assert any("candidate page image" in l for l in result["limitations"])
    # Must NOT claim "no evidence exists" without disclosing truncation:
    assert result["limitations"], "truncation must be disclosed"


# --- 4. source selection snapshots ----------------------------------------------

def test_empty_selection_searches_nothing(workspace, mock_model):
    nb = storage.create_notebook("empty-sel")
    doc = ds.save_upload(nb["id"], "a.pdf", make_pdf(None, ["keyword content here"]))
    ds.index_document(doc["id"])
    result = answer_service.answer_question(nb["id"], "keyword", document_ids=[])
    assert result["claims"] == []
    assert "No sources were selected" in result["message"]
    assert not mock_model["text"], "synthesis must not run with zero sources"


def test_snapshot_immune_to_later_checkbox_changes(workspace, mock_model):
    """A queued job's source snapshot is immutable: deselecting the source
    AFTER enqueueing must not remove it from that job's search scope."""
    nb = storage.create_notebook("snap")
    doc = ds.save_upload(nb["id"], "snap.pdf",
                         make_pdf(None, ["snapshot keyword evidence 72 percent"]))
    ds.index_document(doc["id"])

    job_id = jobs.enqueue_answer(nb["id"], "snapshot keyword")
    payload = json.loads(storage.get_job(job_id)["payload"])
    assert payload["document_ids"] == [doc["id"]]  # frozen at enqueue time

    storage.set_document_selected(doc["id"], False)  # user deselects afterwards
    result = answer_service.answer_question(
        nb["id"], payload["question"], document_ids=payload["document_ids"],
        job_id=job_id)
    # The snapshot still governs: the document IS searched, nothing is
    # reported unavailable, and evidence from it appears in the answer.
    assert result["unavailable_sources"] == []
    assert result["documents_searched"] == ["snap.pdf"]
    assert result["claims"], "snapshot document must still provide evidence"

    # ...while a NEW question with document_ids=None sees the live selection.
    scope = rs.resolve_scope(nb["id"], None)
    assert scope["documents"] == [], "live default must respect deselection"


def test_foreign_notebook_ids_rejected(workspace, mock_model):
    nb1 = storage.create_notebook("n1")
    nb2 = storage.create_notebook("n2")
    doc = ds.save_upload(nb2["id"], "other.pdf", make_pdf(None, ["foreign data"]))
    ds.index_document(doc["id"])
    scope = rs.resolve_scope(nb1["id"], [doc["id"]])
    assert scope["documents"] == []
    assert scope["unavailable"][0]["reason"] == "not in this notebook"


# --- 2. calculation provenance ----------------------------------------------------

def test_uncited_operand_rejected(workspace, mock_model, monkeypatch):
    nb = storage.create_notebook("calc")
    doc = ds.save_upload(nb["id"], "c.pdf", make_pdf(None, ["value 72 percent noted"]))
    ds.index_document(doc["id"])

    def uncited(system, prompt, max_tokens=None):
        return json.dumps({
            "claims": [],
            "calculations": [{"operation": "total",
                              "operands": [{"value": 72, "unit": "%"}]}],  # no evidence_id
            "limitations": [], "unanswerable_parts": []})
    monkeypatch.setattr(model_service, "text_completion", uncited)
    result = answer_service.answer_question(nb["id"], "value")
    assert result["calculations"] == []
    assert any("rejected" in l for l in result["limitations"])


def test_operand_value_not_in_evidence_rejected(workspace, mock_model, monkeypatch):
    nb = storage.create_notebook("calc2")
    doc = ds.save_upload(nb["id"], "c2.pdf", make_pdf(None, ["value 72 percent noted"]))
    ds.index_document(doc["id"])

    def wrong_value(system, prompt, max_tokens=None):
        import re
        eid = re.search(r"\[(el_[a-f0-9]+)\]", prompt).group(1)
        return json.dumps({
            "claims": [],
            "calculations": [{"operation": "total",
                              "operands": [{"value": 9999, "unit": "%",
                                            "evidence_id": eid}]}],
            "limitations": [], "unanswerable_parts": []})
    monkeypatch.setattr(model_service, "text_completion", wrong_value)
    result = answer_service.answer_question(nb["id"], "value")
    assert result["calculations"] == []
    assert any("does not appear" in l for l in result["limitations"])


# --- 3. quote verification ---------------------------------------------------------

def test_short_fabricated_quote_flagged():
    from services.answer_service import _quote_supported
    assert _quote_supported("xyz", ["the real evidence text"]) is False
    assert _quote_supported("", ["anything"]) is False


def test_numeric_boundaries():
    from services.answer_service import _quote_supported
    # "999" must not match inside "1999"; must match standalone.
    assert _quote_supported("999", ["the count was 1999 units"]) is False
    assert _quote_supported("999", ["the count was 999 units"]) is True
    # whitespace normalization: newlines/tabs collapse
    assert _quote_supported("count was 999", ["the count\n  was\t999 units"]) is True


# --- 5. connection lifecycle ---------------------------------------------------------

def test_connections_explicitly_closed(workspace):
    """If any storage function leaked a connection, deleting the SQLite files
    on Windows would raise PermissionError."""
    nb = storage.create_notebook("lifecycle")
    doc = ds.save_upload(nb["id"], "x.pdf", make_pdf(None, ["text"]))
    ds.index_document(doc["id"])
    storage.list_documents(nb["id"])
    storage.list_chat_messages(nb["id"])
    for suffix in ("", "-wal", "-shm"):
        p = storage.Config.DB_PATH.parent / (storage.Config.DB_PATH.name + suffix)
        if p.exists():
            p.unlink()  # raises PermissionError if a handle is still open


# --- 6. upload errors ---------------------------------------------------------------

def test_upload_error_responses_are_json(client_app):
    client, nb = client_app
    r = client.post(f"/api/notebooks/{nb}/documents", data={})
    assert r.status_code == 400
    assert r.is_json and "error" in r.get_json()
    r = client.post(f"/api/notebooks/{nb}/chat", json={"question": ""})
    assert r.status_code == 400 and "error" in r.get_json()


# --- 7. coordinate mapping at different render scales ---------------------------------

@pytest.mark.parametrize("dpi", [72, 150, 300])
def test_bbox_mapped_with_real_dimensions(workspace, client_app, monkeypatch, dpi):
    monkeypatch.setattr(ds.Config, "PAGE_RENDER_DPI", dpi)
    client, _ = client_app
    nb = storage.create_notebook(f"dpi{dpi}")
    doc = ds.save_upload(nb["id"], "coords.pdf", make_pdf(None, ["coordinate test text"]))
    ds.index_document(doc["id"])

    r = client.get(f"/api/documents/{doc['id']}/pages/1")
    detail = r.get_json()
    page = detail["page"]
    assert page["image_width"] and page["page_width_pts"]
    # rendered pixel width must correspond to points * dpi/72
    assert page["image_width"] == pytest.approx(
        page["page_width_pts"] * dpi / 72, abs=2)
    el_with_box = next(e for e in detail["elements"] if e["bbox_px"])
    # bbox_px == bbox_pts * image_width / page_width_pts
    ratio = page["image_width"] / page["page_width_pts"]
    for got, want in zip(el_with_box["bbox_px"], el_with_box["bbox"]):
        assert got == pytest.approx(want * ratio, abs=0.2)


# --- 8. printed page labels ------------------------------------------------------------

def test_printed_page_labels(workspace):
    import fitz
    d = fitz.open()
    d.new_page().insert_text((72, 72), "front matter", fontsize=12)
    d.new_page().insert_text((72, 72), "body page", fontsize=12)
    d.set_page_labels([
        {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},  # i, ii
        {"startpage": 1, "prefix": "", "style": "D", "firstpagenum": 1},  # 1, 2
    ])
    data = d.tobytes()
    d.close()
    nb = storage.create_notebook("labels")
    doc = ds.save_upload(nb["id"], "labeled.pdf", data)
    ds.index_document(doc["id"])

    p1 = storage.get_page(doc["id"], 1)
    p2 = storage.get_page(doc["id"], 2)
    assert p1["page_number"] == 1 and p1["printed_page_label"] == "i"
    assert p2["page_number"] == 2 and p2["printed_page_label"] == "1"

    # Unlabeled PDF -> clear fallback: label is None, position still correct.
    doc2 = ds.save_upload(nb["id"], "plain.pdf", make_pdf(None, ["plain"]))
    ds.index_document(doc2["id"])
    p = storage.get_page(doc2["id"], 1)
    assert p["printed_page_label"] in (None, "", "1")
    assert p["page_number"] == 1


# --- 9. competing job claims and recovery ----------------------------------------------

def test_atomic_job_claim(workspace):
    nb = storage.create_notebook("jobs")
    job_id = storage.create_job("answer", {"question": "q", "document_ids": []},
                                notebook_id=nb["id"])
    first = storage.claim_next_queued_job("worker-A")
    assert first and first["id"] == job_id and first["owner"] == "worker-A"
    # A competing worker cannot claim the same job.
    assert storage.claim_next_queued_job("worker-B") is None


def test_recovery_only_requeues_foreign_owned_jobs(workspace):
    nb = storage.create_notebook("jobs2")
    j1 = storage.create_job("answer", {"question": "a"}, notebook_id=nb["id"])
    j2 = storage.create_job("answer", {"question": "b"}, notebook_id=nb["id"])
    storage.claim_next_queued_job("old-process")   # claims j1
    storage.claim_next_queued_job("current")       # claims j2

    n = storage.recover_abandoned_jobs("current")
    assert n == 1  # only the foreign-owned job
    assert storage.get_job(j1)["status"] == "queued"
    assert storage.get_job(j2)["status"] == "running"  # ours untouched


def test_no_duplicate_chat_messages_on_retry(workspace, mock_model):
    nb = storage.create_notebook("dedup")
    doc = ds.save_upload(nb["id"], "d.pdf", make_pdf(None, ["retry test content 72 percent"]))
    ds.index_document(doc["id"])
    job_id = jobs.enqueue_answer(nb["id"], "retry test")
    payload = json.loads(storage.get_job(job_id)["payload"])
    for _ in range(2):  # run the same job twice (e.g. after crash recovery)
        answer_service.answer_question(nb["id"], payload["question"],
                                       document_ids=payload["document_ids"],
                                       job_id=job_id)
    msgs = storage.list_chat_messages(nb["id"])
    assert len([m for m in msgs if m["role"] == "user"]) == 1
    assert len([m for m in msgs if m["role"] == "assistant"]) == 1


# --- 3. process-safe job recovery -----------------------------------------------------------

def test_second_process_cannot_steal_active_job(workspace, monkeypatch):
    """While the worker lock is held (simulating a live process), another
    'process' must not start a worker or requeue the running job."""
    import msvcrt
    from pathlib import Path

    nb = storage.create_notebook("lock-nb")
    job_id = storage.create_job("answer", {"question": "q"}, notebook_id=nb["id"])
    claimed = storage.claim_next_queued_job("live-process-token")
    assert claimed["id"] == job_id

    # Simulate the live process holding the OS-level lock (canonical DB path).
    lock_path = Path(storage.Config.DB_PATH).resolve().with_suffix(".worker.lock")
    fh = open(lock_path, "a+b")
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    monkeypatch.setattr(jobs, "_process_lock_handle", None)
    try:
        assert jobs.start_worker() is False, "second process must not start a worker"
        assert storage.get_job(job_id)["status"] == "running", \
            "active job must not be requeued by another process"
    finally:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        fh.close()
        monkeypatch.setattr(jobs, "_process_lock_handle", None)


# --- 4. genuine concurrent duplicate uploads ---------------------------------------------------

def test_concurrent_duplicate_uploads(workspace):
    """Real threads racing to upload identical content: exactly one document
    row, exactly one winner, and a complete valid PDF on disk."""
    import threading

    nb = storage.create_notebook("conc")
    data = make_pdf(None, ["concurrent content"])
    results, errors = [], []
    barrier = threading.Barrier(4)

    def upload(i):
        try:
            barrier.wait(timeout=10)
            results.append(ds.save_upload(nb["id"], f"file{i}.pdf", data))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=upload, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    docs = storage.list_documents(nb["id"])
    assert len(docs) == 1
    assert sum(1 for r in results if not r["duplicate"]) == 1
    assert sum(1 for r in results if r["duplicate"]) == 3
    assert all(r["id"] == docs[0]["id"] for r in results)
    # The published file is complete, not a partial write.
    import fitz
    pdf = fitz.open(docs[0]["stored_path"])
    assert pdf.page_count == 1
    pdf.close()
    # No orphan temp files left behind.
    leftovers = list((storage.Config.UPLOAD_DIR / nb["id"]).glob("*.tmp"))
    assert leftovers == []


def test_concurrent_job_claims(workspace):
    """Two threads racing to claim one queued job: exactly one winner."""
    import threading

    nb = storage.create_notebook("claims")
    job_id = storage.create_job("answer", {"question": "q"}, notebook_id=nb["id"])
    winners = []
    barrier = threading.Barrier(2)

    def claim(token):
        barrier.wait(timeout=10)
        j = storage.claim_next_queued_job(token)
        if j:
            winners.append((token, j["id"]))

    threads = [threading.Thread(target=claim, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(winners) == 1
    assert winners[0][1] == job_id


# --- 5. schema migrations ------------------------------------------------------------------------

_V1_SCHEMA = """
CREATE TABLE notebooks (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE documents (
    id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL, name TEXT NOT NULL,
    stored_path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
    error TEXT, selected INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
    UNIQUE (notebook_id, sha256));
CREATE TABLE pages (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, page_number INTEGER NOT NULL,
    printed_page_label TEXT, image_path TEXT, is_scanned INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0, extraction_warnings TEXT,
    UNIQUE (document_id, page_number));
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, notebook_id TEXT, document_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
    message TEXT, payload TEXT, result TEXT, error TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE chat_messages (
    id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, created_at REAL NOT NULL);
"""


def test_migration_from_v1_preserves_data(workspace):
    """A database created with the original schema is upgraded in place to
    the current version; existing rows survive untouched."""
    import sqlite3 as sq
    dbp = storage.Config.DB_PATH
    # Replace the fixture's fresh v-current DB with a simulated v1 legacy DB.
    for suffix in ("", "-wal", "-shm"):
        p = dbp.parent / (dbp.name + suffix)
        if p.exists():
            p.unlink()
    conn = sq.connect(str(dbp))
    conn.executescript(_V1_SCHEMA)
    conn.execute("INSERT INTO notebooks VALUES ('nb_old', 'Legacy', 1.0)")
    conn.execute(
        "INSERT INTO documents VALUES ('doc_old','nb_old','old.pdf','/x',"
        "'h',10,2,'ready',NULL,1,1.0)")
    conn.execute("INSERT INTO jobs (id,kind,status,created_at,updated_at) "
                 "VALUES ('job_old','answer','done',1.0,1.0)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    storage.init_db()  # must migrate, not recreate

    with storage.db() as conn:
        page_cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
        job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        chat_cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        nb = conn.execute("SELECT * FROM notebooks WHERE id='nb_old'").fetchone()
        doc = conn.execute("SELECT * FROM documents WHERE id='doc_old'").fetchone()

    assert {"image_width", "page_width_pts", "rotation", "has_drawings"} <= page_cols
    assert "owner" in job_cols
    assert "job_id" in chat_cols
    assert version == storage.SCHEMA_VERSION
    assert nb["name"] == "Legacy" and doc["page_count"] == 2


# --- 6. calculation binding against real indexed evidence ---------------------------------------

def _index_single(workspace, name, blocks):
    """Index a one-page PDF; blocks = [(point, text, fontsize)]. Returns
    (notebook, doc, elements)."""
    import fitz
    d = fitz.open()
    p = d.new_page()
    for (x, y), text, size in blocks:
        p.insert_text((x, y), text, fontsize=size)
    data = d.tobytes()
    d.close()
    nb = storage.create_notebook(name)
    doc = ds.save_upload(nb["id"], f"{name}.pdf", data)
    ds.index_document(doc["id"])
    return nb, doc, storage.page_elements(doc["id"], 1)


def test_operand_binding_validates_metric_unit_period(workspace):
    from services.answer_service import _validate_operand
    from services import calculation_service as calc

    nb, doc, els = _index_single(workspace, "bind", [
        ((72, 72), "Q2 quarterly report", 14),
        ((72, 200), "production efficiency was 72 percent", 10),
    ])
    value_el = next(e for e in els if "72" in e["text"])
    valid = {e["id"]: dict(e) for e in els}

    # Fully bound operand passes (period comes from page-context heading).
    op, notes = _validate_operand(
        {"value": 72, "unit": "%", "metric": "production efficiency",
         "period": "Q2", "evidence_id": value_el["id"]}, valid)
    assert op.value == 72
    assert any("page context" in n for n in notes)

    # Wrong metric -> rejected.
    with pytest.raises(calc.CalculationError, match="metric"):
        _validate_operand({"value": 72, "unit": "%", "metric": "revenue",
                           "evidence_id": value_el["id"]}, valid)
    # Uncorroborated unit -> rejected as ambiguous.
    with pytest.raises(calc.CalculationError, match="unit"):
        _validate_operand({"value": 72, "unit": "USD", "metric": "efficiency",
                           "evidence_id": value_el["id"]}, valid)
    # Period nowhere on the page -> rejected.
    with pytest.raises(calc.CalculationError, match="period"):
        _validate_operand({"value": 72, "unit": "%", "period": "Q9",
                           "evidence_id": value_el["id"]}, valid)
    # Value not in the cited element -> rejected.
    with pytest.raises(calc.CalculationError, match="does not appear"):
        _validate_operand({"value": 9999, "unit": "%", "metric": "efficiency",
                           "period": "Q2", "evidence_id": value_el["id"]}, valid)
    # Missing binding fields -> rejected (unverified calculations disabled).
    with pytest.raises(calc.CalculationError, match="missing required binding"):
        _validate_operand({"value": 72, "unit": "%",
                           "evidence_id": value_el["id"]}, valid)


@pytest.mark.parametrize("sep", [";", ", "])
def test_binding_rejects_wrong_period_value_pair(workspace, sep):
    """'Q2 efficiency 72%<sep>Q4 efficiency 84%' — operand Q2=84 must be
    REJECTED: the value 84 sits in Q4's clause, not Q2's."""
    from services.answer_service import _validate_operand
    from services import calculation_service as calc

    nb, doc, els = _index_single(workspace, "perbind", [
        ((72, 72), f"Q2 efficiency 72%{sep}Q4 efficiency 84%", 10),
    ])
    el = dict(els[0])
    valid = {el["id"]: el}

    # Correctly bound operand passes.
    op, _ = _validate_operand(
        {"value": 84, "unit": "%", "metric": "efficiency", "period": "Q4",
         "evidence_id": el["id"]}, valid)
    assert op.value == 84
    # Misbound operand (Q2=84) is rejected.
    with pytest.raises(calc.CalculationError, match="period"):
        _validate_operand({"value": 84, "unit": "%", "metric": "efficiency",
                           "period": "Q2", "evidence_id": el["id"]}, valid)


def test_binding_table_column_cell(workspace):
    """'Metric | Revenue USD | Efficiency % / Q2 | 100 | 72': efficiency=100
    must be REJECTED (100 belongs to the Revenue column); efficiency=72 OK."""
    from services.answer_service import _validate_operand
    from services import calculation_service as calc

    nb = storage.create_notebook("tblbind")
    doc = ds.save_upload(nb["id"], "t.pdf", make_pdf(None, ["filler text block"]))
    ds.index_document(doc["id"])
    page = storage.get_page(doc["id"], 1)
    eid = storage.create_element(
        page_id=page["id"], document_id=doc["id"], page_number=1, section=None,
        content_type="table", element_index=50,
        text="Metric | Revenue USD | Efficiency %\nQ2 | 100 | 72",
        bbox=None, extraction_method="pymupdf_table", warnings=[])
    el = dict(storage.page_elements(doc["id"], 1)[-1])
    valid = {eid: el}

    op, _ = _validate_operand({"value": 72, "unit": "%", "metric": "efficiency",
                               "period": "Q2", "evidence_id": eid}, valid)
    assert op.value == 72
    with pytest.raises(calc.CalculationError, match="metric"):
        _validate_operand({"value": 100, "unit": "%", "metric": "efficiency",
                           "period": "Q2", "evidence_id": eid}, valid)


def test_binding_number_formats(workspace):
    """Grouped ('1,000') and explicitly signed ('+100') values verify."""
    from services.answer_service import _validate_operand

    nb, doc, els = _index_single(workspace, "fmt", [
        ((72, 72), "in Q2 revenue reached +1,000 USD this quarter", 10),
    ])
    el = dict(els[0])
    valid = {el["id"]: el}
    op, _ = _validate_operand({"value": 1000, "unit": "USD", "metric": "revenue",
                               "period": "Q2", "evidence_id": el["id"]}, valid)
    assert op.value == 1000


def test_binding_rejects_sign_mismatch(workspace):
    """A positive operand must never be verified against a negative number."""
    from services.answer_service import _validate_operand
    from services import calculation_service as calc

    nb, doc, els = _index_single(workspace, "sign", [
        ((72, 72), "in Q3 the efficiency change was -100 basis points", 10),
    ])
    el = dict(els[0])
    valid = {el["id"]: el}
    base = {"unit": "basis points", "metric": "efficiency", "period": "Q3",
            "evidence_id": el["id"]}
    with pytest.raises(calc.CalculationError, match="signed value"):
        _validate_operand({"value": 100, **base}, valid)
    # The actual signed value binds fine.
    op, _ = _validate_operand({"value": -100, **base}, valid)
    assert op.value == -100


def test_binding_rejects_ambiguous_occurrence(workspace):
    """Same value in two different period contexts, no period given ->
    rejected as unverifiable (fail closed)."""
    from services.answer_service import _validate_operand
    from services import calculation_service as calc

    nb, doc, els = _index_single(workspace, "amb", [
        ((72, 72), "Q1 defect count 100 units; Q3 defect count 100 units", 10),
    ])
    el = dict(els[0])
    valid = {el["id"]: el}
    with pytest.raises(calc.CalculationError, match="missing required binding"):
        _validate_operand({"value": 100, "unit": "units", "metric": "defect",
                           "evidence_id": el["id"]}, valid)
    # Specifying the period disambiguates.
    op, _ = _validate_operand({"value": 100, "unit": "units", "metric": "defect",
                               "period": "Q3", "evidence_id": el["id"]}, valid)
    assert op.period == "Q3"


def test_upload_publish_failure_cleans_up(workspace, monkeypatch):
    """If the atomic publish fails, no usable document row and no temp file
    may remain."""
    nb = storage.create_notebook("pubfail")
    data = make_pdf(None, ["publish failure test"])
    monkeypatch.setattr(ds.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(ds.IngestionError, match="Could not store"):
        ds.save_upload(nb["id"], "f.pdf", data)
    assert storage.list_documents(nb["id"]) == []
    leftovers = list((storage.Config.UPLOAD_DIR / nb["id"]).glob("*.tmp"))
    assert leftovers == []


def test_upload_recovers_incomplete_publication(workspace):
    """A pre-existing document row whose file is missing (crashed upload) is
    removed and the upload proceeds fresh."""
    nb = storage.create_notebook("recover")
    data = make_pdf(None, ["recovery content"])
    doc = ds.save_upload(nb["id"], "r.pdf", data)
    # Simulate crash after insert but before publish; recovery only applies
    # to rows older than the active-publisher grace period.
    from pathlib import Path
    Path(doc["stored_path"]).unlink()
    storage.update_document(doc["id"], created_at=doc["created_at"] - 60)
    fresh = ds.save_upload(nb["id"], "r.pdf", data)
    assert fresh["duplicate"] is False
    assert Path(fresh["stored_path"]).is_file()
    assert len(storage.list_documents(nb["id"])) == 1


# --- 7. tables and vector graphics are visual candidates -------------------------------------------

def test_table_and_vector_pages_are_visual_candidates(workspace):
    import fitz
    d = fitz.open()
    p = d.new_page()
    p.insert_text((72, 72), "enough native text to avoid the scanned flag "
                            "being set on this page of the document", fontsize=12)
    for i in range(8):  # vector drawing -> chart-like page
        p.draw_line(fitz.Point(50, 400 + i * 10), fitz.Point(400, 400 + i * 10))
    p2 = d.new_page()
    p2.insert_text((72, 72), "plain prose page with no graphics at all, "
                             "enough text to avoid the scanned flag", fontsize=12)
    data = d.tobytes()
    d.close()
    nb = storage.create_notebook("cand")
    doc = ds.save_upload(nb["id"], "vec.pdf", data)
    ds.index_document(doc["id"])
    page1 = storage.get_page(doc["id"], 1)
    assert page1["has_drawings"] == 1
    cands = storage.visual_candidate_pages(nb["id"], [doc["id"]])
    assert any(c["page_number"] == 1 for c in cands)

    # Page 2 has no scan flag, no raster image, and no drawings — only a
    # stored table element. Table discovery alone must qualify the page.
    page2 = storage.get_page(doc["id"], 2)
    assert page2["has_drawings"] == 0 and page2["is_scanned"] == 0
    assert not any(c["page_number"] == 2 for c in cands)
    storage.create_element(
        page_id=page2["id"], document_id=doc["id"], page_number=2,
        section=None, content_type="table", element_index=99,
        text="h1 | h2\n1 | 2", bbox=None,
        extraction_method="pymupdf_table", warnings=[])
    cands = storage.visual_candidate_pages(nb["id"], [doc["id"]])
    assert any(c["page_number"] == 2 for c in cands), \
        "a table-bearing page must be a visual candidate by itself"


# --- 8. rotation-aware coordinate mapping -----------------------------------------------------------

def test_bbox_mapping_rotation_math():
    from services.highlight_service import bbox_to_image_px, rotated_page_dims
    # A4 portrait: 612x792 pts, rendered 2x -> 1224x1584 px (unrotated).
    box = [72.0, 72.0, 200.0, 100.0]
    px = bbox_to_image_px(box, 612, 792, 0, 1224, 1584)
    assert px == [144.0, 144.0, 400.0, 200.0]
    # 90° clockwise: displayed dims swap; point maps to (h - y, x).
    assert rotated_page_dims(612, 792, 90) == (792, 612)
    px = bbox_to_image_px(box, 612, 792, 90, 1584, 1224)
    # corners (72,72)->(720,72), (200,100)->(692,200); scale x=2, y=2
    assert px == [1384.0, 144.0, 1440.0, 400.0]
    # 270°: (x,y) -> (y, w - x)
    px = bbox_to_image_px(box, 612, 792, 270, 1584, 1224)
    assert px == [144.0, 824.0, 200.0, 1080.0]
    # Missing dims -> None (no highlight, never a guess).
    assert bbox_to_image_px(box, None, 792, 0, 1224, 1584) is None
    assert bbox_to_image_px(None, 612, 792, 0, 1224, 1584) is None


def test_bbox_mapping_cropbox_origin_and_fail_closed():
    from services.highlight_service import bbox_to_image_px
    # CropBox origin (100,100): bbox coords are page-space; origin subtracted.
    px = bbox_to_image_px([172.0, 172.0, 300.0, 200.0], 612, 792, 0,
                          1224, 1584, crop_x0=100.0, crop_y0=100.0)
    assert px == [144.0, 144.0, 400.0, 200.0]  # same as origin-0 box (72,72)
    # CropBox origin + 90° rotation.
    px = bbox_to_image_px([172.0, 172.0, 300.0, 200.0], 612, 792, 90,
                          1584, 1224, crop_x0=100.0, crop_y0=100.0)
    assert px == [1384.0, 144.0, 1440.0, 400.0]
    # Completely outside the rendered region -> None, NOT a wrong highlight.
    assert bbox_to_image_px([5000.0, 5000.0, 5100.0, 5100.0], 612, 792, 0,
                            1224, 1584) is None
    # Degenerate/empty box -> None.
    assert bbox_to_image_px([10.0, 10.0, 10.0, 10.0], 612, 792, 0,
                            1224, 1584) is None


def test_unsupported_claim_withheld(workspace, mock_model, monkeypatch):
    """A claim with a valid evidence ID but a FABRICATED quote is withheld."""
    nb = storage.create_notebook("unsupp")
    doc = ds.save_upload(nb["id"], "u.pdf",
                         make_pdf(None, ["production efficiency was 72 percent"]))
    ds.index_document(doc["id"])

    def fabricated(system, prompt, max_tokens=None):
        import re
        eid = re.search(r"\[(el_[a-f0-9]+)\]", prompt).group(1)
        return json.dumps({
            "claims": [{"text": "Efficiency was 999 percent.",
                        "evidence_ids": [eid], "quote": "efficiency 999%"}],
            "calculations": [], "limitations": [], "unanswerable_parts": []})
    monkeypatch.setattr(model_service, "text_completion", fabricated)
    result = answer_service.answer_question(nb["id"], "efficiency")
    assert result["claims"] == [], "fabricated-quote claim must be withheld"
    assert result["dropped_claims"][0]["reason"] == "quote not found in cited evidence"


def test_rotated_page_indexes_with_swapped_image_dims(workspace):
    import fitz
    d = fitz.open()
    p = d.new_page()  # 612x792 portrait
    p.insert_text((72, 72), "rotated page text", fontsize=12)
    p.set_rotation(90)
    data = d.tobytes()
    d.close()
    nb = storage.create_notebook("rot")
    doc = ds.save_upload(nb["id"], "rot.pdf", data)
    ds.index_document(doc["id"])
    page = storage.get_page(doc["id"], 1)
    assert page["rotation"] == 90
    # Rendered image follows rotation: width ~ height_pts * dpi/72.
    assert page["image_width"] == pytest.approx(
        page["page_height_pts"] * ds.Config.PAGE_RENDER_DPI / 72, abs=2)
    els = storage.page_elements(doc["id"], 1)
    from services.highlight_service import bbox_to_image_px
    px = bbox_to_image_px(json.loads(els[0]["bbox"]), page["page_width_pts"],
                          page["page_height_pts"], page["rotation"],
                          page["image_width"], page["image_height"])
    assert px and 0 <= px[0] < px[2] <= page["image_width"]
    assert 0 <= px[1] < px[3] <= page["image_height"]


# --- 10. duplicate upload race ------------------------------------------------------------

def test_duplicate_upload_race_returns_existing(workspace, monkeypatch):
    nb = storage.create_notebook("race")
    data = make_pdf(None, ["identical content"])
    first = ds.save_upload(nb["id"], "a.pdf", data)
    # Simulate the race: the pre-insert hash check finds nothing (the other
    # request won in between), then the INSERT hits the uniqueness boundary
    # and save_upload must return the winner instead of raising a 500.
    real_find = storage.find_document_by_hash
    seen = {"n": 0}

    def find_once_missing(*a, **k):
        seen["n"] += 1
        return None if seen["n"] == 1 else real_find(*a, **k)

    def racy_create(*args, **kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(storage, "find_document_by_hash", find_once_missing)
    monkeypatch.setattr(storage, "create_document", racy_create)

    second = ds.save_upload(nb["id"], "a-again.pdf", data)
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    assert len(storage.list_documents(nb["id"])) == 1
