"""Cross-document retrieval, source selection, notebook isolation."""
import storage
from services import document_service as ds
from services import retrieval_service as rs
from tests.conftest import make_pdf


def _indexed_doc(nb_id, name, pages, fontsize=12):
    doc = ds.save_upload(nb_id, name, make_pdf(None, pages, fontsize=fontsize))
    ds.index_document(doc["id"])
    return doc


def test_retrieves_across_multiple_documents(workspace):
    nb = storage.create_notebook("multi")
    _indexed_doc(nb["id"], "q2.pdf", ["Production efficiency in Q2 was 72 percent."])
    _indexed_doc(nb["id"], "q4.pdf", ["Production efficiency in Q4 was 84 percent."])

    result = rs.retrieve("Compare production efficiency between Q2 and Q4", nb["id"])
    docs_hit = {e["document_name"] for e in result["elements"]}
    assert docs_hit == {"q2.pdf", "q4.pdf"}
    assert len(result["needs"]) >= 2  # question was decomposed


def test_unselected_documents_excluded(workspace):
    nb = storage.create_notebook("sel")
    a = _indexed_doc(nb["id"], "a.pdf", ["alpha budget variance twelve percent"])
    _indexed_doc(nb["id"], "b.pdf", ["alpha budget variance fifteen percent"])
    storage.set_document_selected(a["id"], False)

    result = rs.retrieve("alpha budget variance", nb["id"])
    assert {e["document_name"] for e in result["elements"]} == {"b.pdf"}


def test_notebook_isolation(workspace):
    nb1 = storage.create_notebook("one")
    nb2 = storage.create_notebook("two")
    _indexed_doc(nb1["id"], "secret.pdf", ["unique zebra keyword in notebook one"])
    _indexed_doc(nb2["id"], "other.pdf", ["unrelated content entirely"])

    result = rs.retrieve("unique zebra keyword", nb2["id"])
    assert result["elements"] == []


def test_section_falls_back_to_not_identified(workspace):
    """Small body text with no heading must yield section=None (rendered as
    'section not identified') — never an invented heading."""
    nb = storage.create_notebook("sec")
    doc = _indexed_doc(nb["id"], "plain.pdf", ["small body text with no headings"],
                       fontsize=9)  # below heading threshold -> section stays None
    result = rs.retrieve("small body text", nb["id"])
    assert result["elements"]
    for el in result["elements"]:
        assert el["section"] is None, f"invented section: {el['section']!r}"


def test_visible_heading_detected_as_section(workspace):
    """A large/bold heading above body text is tracked as the section."""
    import fitz
    from services import document_service as ds
    d = fitz.open()
    p = d.new_page()
    p.insert_text((72, 72), "Quarterly Efficiency Report", fontsize=18)
    p.insert_text((72, 200), "the efficiency metric reached 72 percent", fontsize=9)
    data = d.tobytes()
    d.close()
    nb = storage.create_notebook("sec2")
    doc = ds.save_upload(nb["id"], "headed.pdf", data)
    ds.index_document(doc["id"])
    result = rs.retrieve("efficiency metric", nb["id"])
    body = [e for e in result["elements"] if "72 percent" in e["text"]]
    assert body and body[0]["section"] == "Quarterly Efficiency Report"
