"""Upload validation, duplicate handling, corrupt-PDF handling, indexing."""
import pytest

import storage
from services import document_service as ds
from tests.conftest import make_pdf


def test_rejects_non_pdf_extension(workspace):
    with pytest.raises(ds.IngestionError, match="not a PDF"):
        ds.save_upload("nb", "notes.txt", b"%PDF-1.4 fake")


def test_rejects_bad_pdf_content(workspace):
    with pytest.raises(ds.IngestionError, match="not a valid PDF"):
        ds.save_upload("nb", "fake.pdf", b"not actually a pdf at all")


def test_rejects_empty_file(workspace):
    with pytest.raises(ds.IngestionError, match="empty"):
        ds.save_upload("nb", "empty.pdf", b"")


def test_rejects_oversized_file(workspace, monkeypatch):
    monkeypatch.setattr(ds, "MAX_FILE_BYTES", 100)
    with pytest.raises(ds.IngestionError, match="limit"):
        ds.save_upload("nb", "big.pdf", b"%PDF-" + b"0" * 200)


def test_duplicate_upload_detected(workspace):
    nb = storage.create_notebook("dupes")
    data = make_pdf(None, ["hello world"])
    first = ds.save_upload(nb["id"], "report.pdf", data)
    second = ds.save_upload(nb["id"], "report-copy.pdf", data)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    assert len(storage.list_documents(nb["id"])) == 1


def test_index_document_extracts_text_and_pages(workspace):
    nb = storage.create_notebook("main")
    data = make_pdf(None, ["Q2 efficiency was 72 percent.", "Q4 efficiency reached 84 percent."])
    doc = ds.save_upload(nb["id"], "ops.pdf", data)
    result = ds.index_document(doc["id"])
    assert result["pages"] == 2

    updated = storage.get_document(doc["id"])
    assert updated["status"] == "ready"
    assert updated["page_count"] == 2

    page = storage.get_page(doc["id"], 1)
    assert page is not None and page["image_path"]
    assert page["page_number"] == 1  # PDF position; label may differ

    with storage.db() as conn:
        els = conn.execute("SELECT * FROM elements WHERE document_id=?",
                           (doc["id"],)).fetchall()
    texts = " ".join(e["text"] for e in els)
    assert "72 percent" in texts and "84 percent" in texts


def test_corrupt_pdf_fails_cleanly(workspace):
    nb = storage.create_notebook("corrupt")
    # Valid header but truncated body passes validation, fails at open/index.
    doc = ds.save_upload(nb["id"], "broken.pdf", b"%PDF-1.7\n%garbage truncated")
    with pytest.raises(ds.IngestionError):
        ds.index_document(doc["id"])
    assert storage.get_document(doc["id"])["status"] == "indexing"  # caller (job) sets error


def test_scanned_page_flagged(workspace):
    nb = storage.create_notebook("scan")
    doc = fitz_doc = None
    import fitz
    d = fitz.open()
    d.new_page()  # page with NO text at all -> scanned
    data = d.tobytes()
    d.close()
    doc = ds.save_upload(nb["id"], "scan.pdf", data)
    ds.index_document(doc["id"])
    page = storage.get_page(doc["id"], 1)
    assert page["is_scanned"] == 1
    assert "scanned" in " ".join(__import__("json").loads(page["extraction_warnings"]))
