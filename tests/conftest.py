"""Shared fixtures. All tests run with a MOCKED model — no GGUF, GPU, or
llama-cpp-python installation is required. See scripts/smoke_test_model.py
for the real-model test."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # noqa: E402

from config import Config  # noqa: E402
import storage  # noqa: E402
from services import model_service  # noqa: E402


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolated data dir + DB per test; model fully mocked."""
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "data" / "uploads")
    monkeypatch.setattr(Config, "PAGE_IMAGE_DIR", tmp_path / "data" / "pages")
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "data" / "app.db")
    Config.ensure_dirs()
    storage.init_db()
    yield tmp_path


@pytest.fixture()
def mock_model(monkeypatch):
    """Replace model_service inference with deterministic fakes."""
    calls = {"vision": [], "text": []}

    def fake_analyze(image_path, instruction):
        calls["vision"].append({"image": image_path, "instruction": instruction})
        return "Chart observed: efficiency 72% in Q2, 84% in Q4 (approximate)."

    def fake_text(system, prompt, max_tokens=None):
        calls["text"].append(prompt)
        import json, re
        # Map evidence IDs to their text blocks so fake operands cite the
        # element that ACTUALLY contains each value (mirrors provenance rules).
        blocks = dict(re.findall(
            r"\[((?:el|vis)_[a-f0-9]+)\][^\n]*\n(.*?)(?=\n\[(?:el|vis)_|\Z)",
            prompt, re.S))
        def holder(token):
            for eid, text in blocks.items():
                if token in text:
                    return eid
            return next(iter(blocks), "el_nonexistent")
        e72, e84 = holder("72"), holder("84")
        return json.dumps({
            "claims": [{"text": "Efficiency rose from Q2 to Q4.",
                        "evidence_ids": [e72, e84], "quote": ""}],
            "calculations": [{
                "operation": "percentage_point_change",
                "operands": [
                    {"value": 72, "unit": "%", "metric": "efficiency",
                     "period": "Q2", "evidence_id": e72},
                    {"value": 84, "unit": "%", "metric": "efficiency",
                     "period": "Q4", "evidence_id": e84},
                ]}],
            "limitations": [], "unanswerable_parts": [],
        })

    monkeypatch.setattr(model_service, "analyze_page_image", fake_analyze)
    monkeypatch.setattr(model_service, "text_completion", fake_text)
    return calls


@pytest.fixture()
def client_app(workspace, mock_model):
    """Flask test client with mocked model; returns (client, notebook_id)."""
    from app import create_app
    app = create_app(db_path=Config.DB_PATH, start_jobs=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.post("/api/notebooks", json={"name": "T"})
        yield c, r.get_json()["id"]


def make_pdf(path: Path, pages: list[str], fontsize: float = 12) -> bytes:
    """Create a real, valid multi-page PDF with the given page texts."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=fontsize)
    data = doc.tobytes()
    doc.close()
    if path:
        path.write_bytes(data)
    return data
