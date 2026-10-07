"""Evidence retrieval across all selected documents of a notebook.

Keyword retrieval uses SQLite FTS5 (BM25 ranking). Embedding/semantic and
visual-embedding retrieval are NOT implemented in this prototype; that gap is
documented in the README rather than advertised.

Complex questions are split into "evidence needs" (one per detected period /
metric / comparison cue), each need is searched separately, and the merged
results are deduplicated. Nearby pages can be pulled in so headings, footnotes
and continued tables adjacent to a hit are not lost.
"""
import json
import re
from typing import Optional

from config import Config
import storage

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "is", "are",
    "was", "were", "be", "been", "what", "which", "how", "why", "show", "me",
    "compare", "between", "with", "from", "that", "this", "it", "its", "by",
}

# Cues that split a complex question into separate evidence needs.
_SPLIT_RE = re.compile(r"\b(?:and|versus|vs\.?|compared to|between)\b", re.I)
_PERIOD_RE = re.compile(
    r"\b(Q[1-4](?:\s*(?:19|20)\d{2})?|FY\s*(?:19|20)?\d{2}|(?:19|20)\d{2}|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.I)


def decompose_question(question: str) -> list[str]:
    """Break a question into evidence needs.

    Splits on comparison/coordination cues and always keeps the full question
    as a fallback need, so nothing is searched *less* than a naive approach.
    """
    needs: list[str] = []
    parts = [p.strip(" ?.") for p in _SPLIT_RE.split(question) if len(p.strip()) > 12]
    needs.extend(parts[:4])
    periods = _PERIOD_RE.findall(question)
    if len(periods) >= 2 and not parts:
        # e.g. "Compare efficiency between Q2 and Q4" -> one need per period.
        base = _PERIOD_RE.sub("", question)
        for p in periods[:4]:
            needs.append(f"{base} {p}")
    if question.strip() not in needs:
        needs.append(question.strip())
    # Deduplicate while preserving order.
    seen, out = set(), []
    for n in needs:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out[:5]


def _fts_query(text: str) -> str:
    """Build a safe FTS5 MATCH expression (OR of quoted terms)."""
    terms = [t for t in re.findall(r"[A-Za-z0-9%$.]+", text)
             if t.lower() not in _STOPWORDS and len(t) > 1]
    if not terms:
        return '""'
    return " OR ".join(f'"{t}"' for t in terms[:12])


def resolve_scope(notebook_id: str,
                  document_ids: Optional[list[str]]) -> dict:
    """Resolve which documents a job may search.

    Semantics (fixed contract):
    - document_ids=None  -> default behavior: all currently selected, indexed
      documents of the notebook (live checkbox state).
    - document_ids=[]    -> explicitly no sources; search NOTHING (never
      fall back to all documents).
    - document_ids=[...] -> an immutable snapshot (taken when the job was
      queued). Current checkbox state is IGNORED: deselecting a source after
      enqueueing does not remove it from the job. Each ID is still validated
      for notebook ownership and availability (deleted / failed-indexing
      documents are reported, never substituted with unselected ones).
    """
    docs = storage.list_documents(notebook_id)
    by_id = {d["id"]: d for d in docs}
    if document_ids is None:
        usable = [d for d in docs if d["selected"] and d["status"] == "ready"]
        return {"documents": usable, "unavailable": []}
    if not document_ids:
        return {"documents": [], "unavailable": []}
    usable, unavailable = [], []
    for did in document_ids:
        d = by_id.get(did)
        if d is None:
            unavailable.append({"id": did, "reason": "not in this notebook"})
        elif d["status"] != "ready":
            unavailable.append({"id": did, "name": d["name"],
                                "reason": f"indexing status: {d['status']}"})
        else:
            usable.append(d)  # snapshot: current selection flag ignored
    return {"documents": usable, "unavailable": unavailable}


def retrieve(question: str, notebook_id: str,
             document_ids: Optional[list[str]] = None,
             top_k: Optional[int] = None) -> dict:
    """Retrieve candidate evidence elements for a question.

    Returns {"needs": [...], "elements": [...]} where each element carries full
    provenance (document, page, section, bbox, extraction method).
    """
    top_k = top_k or Config.RETRIEVAL_TOP_K
    scope = resolve_scope(notebook_id, document_ids)
    selected = scope["documents"]
    selected_ids = [d["id"] for d in selected]
    if not selected_ids:
        return {"needs": [], "elements": [], "documents_searched": [],
                "unavailable_sources": scope["unavailable"],
                "selected_ids": selected_ids}

    needs = decompose_question(question)
    per_need = max(3, top_k // max(1, len(needs)) + 1)

    merged: dict[str, dict] = {}
    for need in needs:
        for hit in storage.search_elements(notebook_id, _fts_query(need),
                                           selected_ids, per_need):
            el = merged.setdefault(hit["id"], hit)
            el["matched_needs"] = el.get("matched_needs", []) + [need]

    elements = sorted(merged.values(), key=lambda e: e.get("rank", 0))[: top_k * 2]

    # Pull in adjacent pages for context (continued tables, footnotes) when an
    # element sits near a page boundary. Metadata only; text is attached below.
    window = Config.ADJACENT_PAGE_WINDOW
    if window > 0 and elements:
        for el in list(elements):
            for pn in range(max(1, el["page_number"] - window),
                            el["page_number"] + window + 1):
                if pn == el["page_number"]:
                    continue
                for r in storage.page_elements(el["document_id"], pn, limit=3):
                    if r["id"] not in merged:
                        r["matched_needs"] = ["adjacent-page-context"]
                        merged[r["id"]] = r
        elements = sorted(merged.values(), key=lambda e: e.get("rank", 99))

    # Expand to complete relevant pages. Short reports expose every page as a
    # candidate; the single five-page budget is shared across documents.
    candidates = list(dict.fromkeys((e['document_id'], e['page_number']) for e in elements))
    for d in selected:
        if d.get('page_count', 0) <= 5:
            candidates.extend((d['id'], pn) for pn in range(1, d.get('page_count', 0)+1))
    candidates = list(dict.fromkeys(candidates))
    metric_words = [w.lower() for w in re.findall(r'[A-Za-z]+', question)
                    if w.lower() not in _STOPWORDS and w.lower() not in {'cite','sources','your'}]
    page_rows = {key: storage.page_elements(*key, limit=1000000) for key in candidates}
    def relevance(key):
        return max((sum(w in r['text'].lower() for w in metric_words)
                    for r in page_rows[key]), default=0)
    candidates.sort(key=lambda key: -relevance(key))
    selected_pages = candidates[:5]
    elements = [r for key in selected_pages for r in page_rows[key]]
    for el in elements:
        el["warnings"] = json.loads(el.get("warnings") or "[]")
        el["bbox"] = json.loads(el["bbox"]) if el.get("bbox") else None

    return {
        "needs": needs,
        "elements": elements,
        "page_coverage": {"candidate_pages": len(candidates), "gathered_pages": len(selected_pages),
                          "complete": len(candidates) <= 5},
        "documents_searched": [d["name"] for d in selected],
        "unavailable_sources": scope["unavailable"],
        "selected_ids": selected_ids,
    }


def visual_fallback_pages(notebook_id: str, document_ids: Optional[list[str]],
                          exclude: set[tuple[str, int]]) -> list[dict]:
    """ALL candidate pages for visual analysis beyond the given pages:
    scanned pages, pages with raster images, vector graphics, or detected
    tables — from the job's selected documents only, deduplicated against
    ``exclude``. The caller applies the page limit to the union."""
    scope = resolve_scope(notebook_id, document_ids)
    doc_ids = [d["id"] for d in scope["documents"]]
    candidates = storage.visual_candidate_pages(notebook_id, doc_ids)
    return [p for p in candidates
            if (p["document_id"], p["page_number"]) not in exclude]


def pages_for_visual_analysis(elements: list[dict]) -> list[dict]:
    """All distinct pages hit by retrieval whose rendered images exist,
    priority-sorted (scanned > graphics/tables > plain). The caller applies
    the page limit to the deduplicated UNION of these and the fallback
    candidates, so coverage counts never double-count a page."""
    seen: set[tuple[str, int]] = set()
    picked: list[dict] = []
    for el in elements:
        key = (el["document_id"], el["page_number"])
        if key in seen:
            continue
        seen.add(key)
        page = storage.get_page(*key)
        if not page or not page["image_path"]:
            continue
        priority = 0
        if page["is_scanned"]:
            priority += 2
        if el["content_type"] in ("image_region", "table", "ocr"):
            priority += 1
        page["_priority"] = priority
        page["document_name"] = el.get("document_name")
        picked.append(page)
    picked.sort(key=lambda p: -p["_priority"])
    return picked
