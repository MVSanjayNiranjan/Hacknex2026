"""Document ingestion: validation, storage, extraction, rendering.

PyMuPDF (fitz) is used for text/layout extraction, table detection, and page
rendering. Docling was considered for richer layout analysis, but it pulls in
large ML dependencies that conflict with a 16 GB-RAM laptop already running a
7B vision model; PyMuPDF's native block/table extraction is the honest
fallback and its limitations are listed in the README.

Scanned pages (little or no native text) are rendered and flagged. If
VISUAL_TRANSCRIPTION_ENABLED=1 and the model loads, they are transcribed with
Qwen2.5-VL during indexing; otherwise they stay searchable only through any
embedded text and remain available as visual evidence at answer time.
"""
import hashlib
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from config import Config
import storage

log = logging.getLogger(__name__)

MAX_FILE_BYTES = Config.MAX_FILE_MB * 1024 * 1024

# Spans at/above this size are heading candidates when tracking sections.
_HEADING_FONT_MIN = 11.5


class IngestionError(Exception):
    """User-facing ingestion failure (bad file, encrypted, too large...)."""


def validate_upload(filename: str, data: bytes) -> None:
    """Raise IngestionError with a clear message when the upload is invalid."""
    if not filename or not filename.lower().endswith(".pdf"):
        raise IngestionError(f"'{filename}' is not a PDF file. Only .pdf is supported.")
    if len(data) == 0:
        raise IngestionError(f"'{filename}' is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise IngestionError(
            f"'{filename}' is {len(data) / 1e6:.1f} MB; the limit is {Config.MAX_FILE_MB} MB.")
    if data[:5] != b"%PDF-":
        raise IngestionError(
            f"'{filename}' has a .pdf extension but is not a valid PDF (bad header).")


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wait_for_published(path: Path, timeout: float = 5.0) -> bool:
    """Poll until another request's atomic publish lands (bounded)."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return True
        time.sleep(0.05)
    return False


def save_upload(notebook_id: str, filename: str, data: bytes) -> dict:
    """Validate, deduplicate, and persist an uploaded PDF. Returns the doc row.

    Publication protocol: the PDF is written to a UNIQUE temp file, the DB row
    is inserted (uniqueness boundary), and only then is the file published via
    atomic rename. A document is therefore never reported available while its
    file is missing or partially written:
    - insert race loser: temp deleted; waits (bounded) for the winner's file
      and errors clearly instead of returning an unusable document;
    - rename failure: the DB row and temp file are both removed;
    - pre-existing row whose file is missing (crashed earlier upload): the
      stale row is deleted and the upload proceeds fresh.
    """
    validate_upload(filename, data)
    digest = sha256_of(data)
    existing = storage.find_document_by_hash(notebook_id, digest)
    if existing:
        if Path(existing["stored_path"]).is_file():
            existing["duplicate"] = True
            return existing
        # File missing. An ACTIVE publisher sits between insert and atomic
        # rename for milliseconds — never delete their row. Only rows older
        # than the grace period can be crash leftovers eligible for recovery.
        import time as _time
        age = _time.time() - existing.get("created_at", 0)
        if age < 30:
            if not _wait_for_published(Path(existing["stored_path"])):
                raise IngestionError(
                    f"A concurrent upload of '{filename}' is still completing; "
                    "please retry in a few seconds.")
            existing["duplicate"] = True
            return existing
        # Abandoned publication (crashed upload): recover by removing the
        # unusable row and continuing as a fresh upload.
        log.warning("recovering incomplete upload of document %s", existing["id"])
        storage.delete_document(existing["id"])

    # Internal filename is generated; the user-supplied name is display-only.
    doc_id_hint = hashlib.sha256(digest.encode()).hexdigest()[:12]
    stored = Path(Config.UPLOAD_DIR) / notebook_id / f"{doc_id_hint}.pdf"
    stored.parent.mkdir(parents=True, exist_ok=True)
    tmp = stored.parent / f".{uuid.uuid4().hex}.tmp"

    try:
        tmp.write_bytes(data)
        try:
            doc = storage.create_document(
                notebook_id=notebook_id,
                name=Path(filename).name,  # strip client-side path components
                stored_path=str(stored),
                sha256=digest,
                size_bytes=len(data),
            )
        except sqlite3.IntegrityError:
            # Concurrent upload of identical content won the insert race.
            winner = storage.find_document_by_hash(notebook_id, digest)
            if winner is None:  # pragma: no cover - defensive
                raise
            if not _wait_for_published(Path(winner["stored_path"])):
                raise IngestionError(
                    f"A concurrent upload of '{filename}' is still completing; "
                    "please retry in a few seconds.")
            winner["duplicate"] = True
            return winner
        os.replace(tmp, stored)  # atomic publish
    except IngestionError:
        raise
    except Exception as exc:
        # Publish/insert failure: leave NO usable document behind.
        if "doc" in locals():
            try:
                storage.delete_document(doc["id"])
            except Exception:
                pass
        raise IngestionError(f"Could not store '{filename}': {exc}")
    finally:
        tmp.unlink(missing_ok=True)  # no-op after a successful rename

    doc["duplicate"] = False
    return doc


def _page_label(page: fitz.Page) -> Optional[str]:
    """Printed page label via PyMuPDF's Page.get_label(); None when absent.

    Kept strictly separate from the 1-based PDF page position.
    """
    try:
        label = page.get_label()
        return label if label else None
    except Exception:
        return None


def _block_section_guess(block: dict, current: Optional[str]) -> Optional[str]:
    """Track the most recent large-font span as the visible section heading."""
    section = current
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "").strip()
            if not text:
                continue
            size = span.get("size", 0)
            is_bold = bool(span.get("flags", 0) & 16)
            if (size >= _HEADING_FONT_MIN or (is_bold and size >= 10.5)) and len(text) <= 120:
                section = text
    return section


def _render_page(page: fitz.Page, out_path: Path) -> tuple[int, int]:
    """Render a page to PNG; returns actual (width, height) in pixels so
    coordinate mapping never assumes a hardcoded DPI."""
    zoom = Config.PAGE_RENDER_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    return pix.width, pix.height


def index_document(doc_id: str, progress_cb=None) -> dict:
    """Extract text/tables and render page images for one document.

    Raises IngestionError for encrypted/corrupt files so the job layer can
    record a user-actionable error on the document row.
    """
    doc = storage.get_document(doc_id)
    if not doc:
        raise IngestionError("Document not found.")
    storage.update_document(doc_id, status="indexing", error=None)
    storage.delete_document_data(doc_id)

    try:
        pdf = fitz.open(doc["stored_path"])
    except Exception as exc:
        raise IngestionError(
            f"Could not open '{doc['name']}': corrupt or unsupported PDF ({exc}).")

    try:
        if pdf.needs_pass or pdf.is_encrypted:
            raise IngestionError(
                f"'{doc['name']}' is password-protected/encrypted and cannot be indexed.")
        page_count = pdf.page_count
        if page_count == 0:
            raise IngestionError(f"'{doc['name']}' has no pages.")
        if page_count > Config.MAX_PAGES:
            raise IngestionError(
                f"'{doc['name']}' has {page_count} pages; the limit is {Config.MAX_PAGES}.")

        model_service = None
        if Config.VISUAL_TRANSCRIPTION_ENABLED:
            from services import model_service as ms  # lazy; optional at index time
            model_service = ms

        for i in range(page_count):
            page = pdf.load_page(i)
            page_number = i + 1
            label = _page_label(page)
            warnings: list[str] = []

            img_path = Path(Config.PAGE_IMAGE_DIR) / doc_id / f"page_{page_number:04d}.png"
            img_w = img_h = None
            try:
                img_w, img_h = _render_page(page, img_path)
            except Exception as exc:
                warnings.append(f"page render failed: {exc}")
                img_path = None

            raw = page.get_text("dict")
            blocks = [b for b in raw.get("blocks", []) if b.get("type") == 0]
            native_text = "".join(
                span.get("text", "")
                for b in blocks for line in b.get("lines", []) for span in line.get("spans", [])
            )
            char_count = len(native_text.strip())
            is_scanned = char_count < Config.SCANNED_CHAR_THRESHOLD

            # Vector graphics (charts drawn as paths) need visual analysis
            # even when the page has a text layer and no raster images.
            try:
                # >=2 paths: a simple two-bar chart must qualify; single
                # underline/border artifacts alone do not.
                has_drawings = len(page.get_drawings()) >= 2
            except Exception:
                has_drawings = False
                warnings.append("vector graphics detection failed")

            page_id = storage.create_page(
                document_id=doc_id, page_number=page_number, printed_label=label,
                image_path=str(img_path) if img_path else None,
                image_width=img_w, image_height=img_h,
                # cropbox = the actual rendered region in unrotated points,
                # matching get_text bboxes; origin stored for the transform.
                page_width_pts=page.cropbox.width, page_height_pts=page.cropbox.height,
                crop_x0=page.cropbox.x0, crop_y0=page.cropbox.y0,
                rotation=page.rotation, has_drawings=has_drawings,
                is_scanned=is_scanned, char_count=char_count, warnings=[],
            )

            section: Optional[str] = None
            el_index = 0

            # Text blocks in reading order (PyMuPDF returns blocks unsorted).
            for b in sorted(blocks, key=lambda x: (round(x["bbox"][1], 1), x["bbox"][0])):
                section = _block_section_guess(b, section)
                text = "\n".join(
                    "".join(s.get("text", "") for s in line.get("spans", []))
                    for line in b.get("lines", [])
                ).strip()
                if not text:
                    continue
                storage.create_element(
                    page_id=page_id, document_id=doc_id, page_number=page_number,
                    section=section, content_type="text", element_index=el_index,
                    text=text, bbox=[round(v, 2) for v in b["bbox"]],
                    extraction_method="pymupdf_text", warnings=[],
                )
                el_index += 1

            # Native tables with headers/units preserved as pipe-delimited
            # rows. Borderless tables get a text-layout strategy pass.
            try:
                tables = page.find_tables().tables
                if not tables:
                    try:
                        tables = page.find_tables(strategy="text").tables
                    except TypeError:
                        tables = []  # older PyMuPDF without strategy support
                for table in tables:
                    rows = table.extract()
                    if not rows:
                        continue
                    lines = [" | ".join("" if c is None else str(c) for c in row) for row in rows]
                    storage.create_element(
                        page_id=page_id, document_id=doc_id, page_number=page_number,
                        section=section, content_type="table", element_index=el_index,
                        text="\n".join(lines), bbox=[round(v, 2) for v in table.bbox],
                        extraction_method="pymupdf_table",
                        warnings=["table structure inferred from ruling lines; verify merged cells"],
                    )
                    el_index += 1
            except Exception as exc:
                warnings.append(f"table detection failed: {exc}")

            images = page.get_images(full=True)
            if images and not is_scanned:
                # Caption/label text is already in the text blocks; record the
                # presence of graphics so retrieval can route this page to the
                # vision model at answer time.
                storage.create_element(
                    page_id=page_id, document_id=doc_id, page_number=page_number,
                    section=section, content_type="image_region", element_index=el_index,
                    text=f"[page contains {len(images)} embedded image(s)/graphic(s); visual inspection required]",
                    bbox=None, extraction_method="pymupdf_text",
                    warnings=["graphics present; not described by text extraction"],
                )
                el_index += 1

            if is_scanned:
                if model_service and img_path:
                    try:
                        transcript = model_service.transcribe_page(str(img_path))
                        if transcript:
                            storage.create_element(
                                page_id=page_id, document_id=doc_id, page_number=page_number,
                                section=None, content_type="ocr", element_index=el_index,
                                text=transcript, bbox=None,
                                extraction_method="qwen_visual",
                                warnings=["vision transcription; verify against the page image"],
                            )
                            el_index += 1
                            is_scanned = False
                        else:
                            warnings.append("visual transcription returned no text")
                    except Exception as exc:  # model unavailable/failed
                        warnings.append(f"visual transcription unavailable: {exc}")
                if is_scanned:
                    warnings.append(
                        "scanned/image-only page; little or no machine-readable text. "
                        "It can still be used as visual evidence when answering.")

            if is_scanned:
                # Searchable placeholder so scanned pages are discoverable by
                # retrieval and routed to visual analysis even with
                # transcription disabled. Content is explicitly marked absent.
                storage.create_element(
                    page_id=page_id, document_id=doc_id, page_number=page_number,
                    section=None, content_type="image_region", element_index=el_index,
                    text=("[image-only/scanned page: no machine-readable text "
                          "extracted; content requires visual analysis]"),
                    bbox=None, extraction_method="none",
                    warnings=["unreadable by text extraction; nothing invented"],
                )
                el_index += 1

            if warnings:
                storage.set_page_warnings(page_id, warnings)

            if progress_cb:
                progress_cb(page_number / page_count,
                            f"Indexed page {page_number}/{page_count}")

        storage.update_document(doc_id, status="ready", page_count=page_count)
        return {"document_id": doc_id, "pages": page_count}
    finally:
        pdf.close()
