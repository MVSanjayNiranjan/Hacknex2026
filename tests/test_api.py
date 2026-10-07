"""Flask API: multi-upload, jobs, failure handling, model status honesty."""
import io
import json
import time

import pytest

import storage
from app import create_app
from services import document_service as ds
from tests.conftest import make_pdf


@pytest.fixture()
def client(workspace, mock_model):
    app = create_app(db_path=storage.Config.DB_PATH, start_jobs=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _nb(client) -> str:
    r = client.post("/api/notebooks", json={"name": "T"})
    return r.get_json()["id"]


def test_multi_upload_and_duplicate(client):
    nb = _nb(client)
    pdf = make_pdf(None, ["page one text"])
    data = {"files": [(io.BytesIO(pdf), "a.pdf"), (io.BytesIO(pdf), "a-copy.pdf"),
                      (io.BytesIO(b"junk"), "b.pdf")]}
    r = client.post(f"/api/notebooks/{nb}/documents", data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 207
    results = r.get_json()["results"]
    statuses = [x["status"] for x in results]
    assert statuses.count("queued") == 1
    assert statuses.count("duplicate") == 1
    assert statuses.count("rejected") == 1


def test_too_many_files_rejected(client):
    nb = _nb(client)
    pdf = make_pdf(None, ["x"])
    data = {"files": [(io.BytesIO(pdf), f"f{i}.pdf") for i in range(11)]}
    r = client.post(f"/api/notebooks/{nb}/documents", data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 400


def test_chat_requires_selected_indexed_sources(client):
    nb = _nb(client)
    r = client.post(f"/api/notebooks/{nb}/chat", json={"question": "hi"})
    assert r.status_code == 400
    assert "No indexed sources" in r.get_json()["error"]


def test_job_error_status_and_document_error(client):
    nb = _nb(client)
    # Corrupt-but-valid-header PDF indexes into a failure.
    r = client.post(f"/api/notebooks/{nb}/documents",
                    data={"files": [(io.BytesIO(b"%PDF-1.7\ntruncated"), "bad.pdf")]},
                    content_type="multipart/form-data")
    job_id = r.get_json()["results"][0]["job_id"]

    from services import jobs
    job = storage.get_job(job_id)
    jobs.run_job(job)  # run synchronously instead of the worker

    failed = storage.get_job(job_id)
    assert failed["status"] == "error"
    assert failed["error"]
    doc = storage.list_documents(nb)[0]
    assert doc["status"] == "error"
    assert doc["error"]

    # Retry endpoint queues a new job.
    r2 = client.post(f"/api/documents/{doc['id']}/retry")
    assert r2.status_code == 200


def test_model_status_never_claims_ready_from_files(client, monkeypatch):
    from services import model_service as ms
    ms.reset_for_tests()
    r = client.get("/api/model/status")
    s = r.get_json()
    # Model files are absent in the test workspace -> must not say loaded.
    assert s["state"] in ("missing_files", "error", "not_loaded")
    assert s["loaded"] is False
    assert s["visual_inference_tested"] is False
    assert s["gpu_placement_observed"] is None  # never claimed unverifiably

    # Even with files present but not loaded, state must not be 'loaded'.
    monkeypatch.setattr(ms.Config, "MODEL_PATH", __file__)
    monkeypatch.setattr(ms.Config, "MMPROJ_PATH", __file__)
    ms.reset_for_tests()
    s = client.get("/api/model/status").get_json()
    assert s["state"] == "not_loaded"
    assert s["loaded"] is False
    assert s["model_file_present"] is True
    assert s["projector_file_present"] is True
