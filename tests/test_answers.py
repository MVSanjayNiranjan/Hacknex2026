"""Answer pipeline with a mocked model: citation validation, invented-ID
rejection, citation resolution to real page metadata, calculation execution."""
import json
import re

import pytest

import storage
from services import answer_service, document_service as ds
from services import model_service
from tests.conftest import make_pdf


def _setup_notebook(workspace):
    nb = storage.create_notebook("answers")
    doc = ds.save_upload(nb["id"], "ops.pdf", make_pdf(None, [
        "Efficiency in Q2 was 72 percent.",
        "Efficiency in Q4 reached 84 percent.",
    ]))
    ds.index_document(doc["id"])
    return nb, doc


def test_answer_cites_real_metadata(workspace, mock_model):
    nb, doc = _setup_notebook(workspace)
    result = answer_service.answer_question(nb["id"], "How did efficiency change?")
    assert result["claims"], "expected at least one claim"
    for claim in result["claims"]:
        for eid in claim["evidence_ids"]:
            cit = result["citations"][eid]
            assert cit["document_name"] == "ops.pdf"
            assert cit["page_number"] in (1, 2)
    # Percentage-point calculation executed in Python, not trusted from text.
    calc = result["calculations"][0]
    assert calc["operation"] == "percentage_point_change"
    assert calc["value"] == 12.0
    assert calc["formula"] == "84.0 - 72.0"
    # Vision pass ran on retrieved page images.
    assert mock_model["vision"], "expected page-image analysis calls"
    assert result["pages_analyzed"]


def test_invented_evidence_ids_removed(workspace, mock_model, monkeypatch):
    nb, doc = _setup_notebook(workspace)

    def hallucinating(system, prompt, max_tokens=None):
        return json.dumps({
            "claims": [
                {"text": "Invented claim.", "evidence_ids": ["el_fake123"], "quote": "x"},
                {"text": "Mixed claim.", "evidence_ids": ["el_fake123"], "quote": "y"},
            ],
            "calculations": [], "limitations": [], "unanswerable_parts": [],
        })
    monkeypatch.setattr(model_service, "text_completion", hallucinating)

    result = answer_service.answer_question(nb["id"], "efficiency change")
    assert result["claims"] == []  # both cited nonexistent evidence -> removed
    assert len(result["dropped_claims"]) == 2
    assert any("removed" in l for l in result["limitations"])


def test_calculation_with_invalid_evidence_id_rejected(workspace, mock_model, monkeypatch):
    nb, doc = _setup_notebook(workspace)

    def bad_calc(system, prompt, max_tokens=None):
        return json.dumps({
            "claims": [],
            "calculations": [{
                "operation": "percentage_change",
                "operands": [
                    {"value": 72, "unit": "%", "evidence_id": "el_fake"},
                    {"value": 84, "unit": "%", "evidence_id": "el_fake"},
                ]}],
            "limitations": [], "unanswerable_parts": [],
        })
    monkeypatch.setattr(model_service, "text_completion", bad_calc)

    result = answer_service.answer_question(nb["id"], "efficiency")
    assert result["calculations"] == []
    assert any("rejected" in l for l in result["limitations"])


def test_no_evidence_returns_actionable_message(workspace, mock_model):
    nb = storage.create_notebook("empty-nb")
    result = answer_service.answer_question(nb["id"], "what is the revenue?")
    assert result["claims"] == []
    assert "No matching evidence" in result["message"]


def test_model_failure_propagates_no_fabrication(workspace, mock_model, monkeypatch):
    nb, doc = _setup_notebook(workspace)

    def broken(*a, **k):
        raise model_service.ModelUnavailableError("Model file not found at X")
    monkeypatch.setattr(model_service, "text_completion", broken)
    monkeypatch.setattr(model_service, "analyze_page_image", broken)

    import pytest
    with pytest.raises(model_service.ModelUnavailableError):
        answer_service.answer_question(nb["id"], "efficiency")


def test_summary_aliases_resolve_and_calculations_are_disabled(workspace, mock_model, monkeypatch):
    nb, doc = _setup_notebook(workspace)

    def summarize(system, prompt, max_tokens=None):
        assert 'calculation operations' in system
        assert '[el_' not in prompt and '[vis_' not in prompt
        alias = re.search(r'\[(E\d+)\][^\n]*\nEfficiency in Q2', prompt)[1]
        return json.dumps({
            'claims': [{'text': 'The report covers quarterly efficiency.',
                        'evidence_ids': [alias], 'quote': 'Efficiency in Q2 was 72 percent.'}],
            'calculations': [{'operation': 'total', 'operands': []}],
            'limitations': [],
        })

    monkeypatch.setattr(model_service, 'text_completion', summarize)
    result = answer_service.answer_question(nb['id'], 'Summarize this report')
    assert len(result['claims']) == 1
    claim = result['claims'][0]
    assert claim['support_check'] == 'quote_found_in_evidence'
    assert result['citations'][claim['evidence_ids'][0]]['document_id'] == doc['id']
    assert result['calculations'] == []
    assert result['limitations'] == []


@pytest.mark.parametrize('bad_support', ['unknown_alias', 'paraphrased_quote', 'missing_quote', 'no_claims'])
def test_summary_with_no_valid_claims_fails(workspace, mock_model, monkeypatch, bad_support):
    nb, doc = _setup_notebook(workspace)

    def summarize(system, prompt, max_tokens=None):
        alias = re.search(r'\[(E\d+)\][^\n]*\nEfficiency in Q2', prompt)[1]
        claim = {'text': 'Efficiency improved.', 'evidence_ids': [alias],
                 'quote': 'Efficiency in Q2 was 72 percent.'}
        if bad_support == 'unknown_alias':
            claim['evidence_ids'] = ['E9999']
        elif bad_support == 'paraphrased_quote':
            claim['quote'] = 'Quarter two efficiency was seventy-two percent.'
        elif bad_support == 'missing_quote':
            claim['quote'] = ''
        return json.dumps({'claims': [] if bad_support == 'no_claims' else [claim],
                           'limitations': ['Some limitations.']})

    monkeypatch.setattr(model_service, 'text_completion', summarize)
    with pytest.raises(ValueError, match='Summary failed: no claims passed citation validation'):
        answer_service.answer_question(nb['id'], 'Summarize this report')
    assert not any(m['role'] == 'assistant' for m in storage.list_chat_messages(nb['id']))
