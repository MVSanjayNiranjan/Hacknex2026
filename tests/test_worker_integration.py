"""WORKER INTEGRATION TESTS — start the real background worker thread.

Run separately:  pytest tests/test_worker_integration.py -m worker -v
Ordinary suites exclude these via -m "not worker". Every test guarantees
worker shutdown and lock release in a finally block, and all data lives in
the per-test temporary directory.
"""
import json

import pytest

import storage
from services import jobs

pytestmark = pytest.mark.worker


@pytest.fixture()
def worker_cleanup(workspace, monkeypatch):
    """Reset worker module state and ALWAYS shut down + release the lock."""
    monkeypatch.setattr(jobs, "_process_lock_handle", None)
    monkeypatch.setattr(jobs, "_worker_thread", None)
    jobs._stop.clear()
    yield
    try:
        jobs.stop_worker()
    finally:
        jobs._process_lock_handle = None
        jobs._worker_thread = None


def test_lock_holder_recovers_crashed_jobs(worker_cleanup, monkeypatch):
    """With the interprocess lock held by THIS process, foreign-owned
    'running' jobs are crash leftovers and get requeued."""
    monkeypatch.setattr(jobs, "run_job", lambda job: None)  # don't execute
    nb = storage.create_notebook("lock-nb2")
    j1 = storage.create_job("answer", {"question": "a"}, notebook_id=nb["id"])
    storage.claim_next_queued_job("crashed-process")
    try:
        assert jobs.start_worker() is True
        import time
        time.sleep(0.4)  # worker may re-claim the requeued job
        job = storage.get_job(j1)
        assert job["owner"] != "crashed-process"
        assert job["status"] in ("queued", "running")
    finally:
        jobs.stop_worker()


def test_worker_processes_index_job_end_to_end(worker_cleanup):
    """Real worker drains a real indexing job against temp storage."""
    from services import document_service as ds
    from tests.conftest import make_pdf
    nb = storage.create_notebook("worker-nb")
    doc = ds.save_upload(nb["id"], "w.pdf", make_pdf(None, ["worker text 42"]))
    job_id = jobs.enqueue_index(nb["id"], doc["id"])
    try:
        assert jobs.start_worker() is True
        import time
        deadline = time.time() + 30
        while time.time() < deadline:
            status = storage.get_job(job_id)["status"]
            if status in ("done", "error"):
                break
            time.sleep(0.2)
        job = storage.get_job(job_id)
        assert job["status"] == "done", job["error"]
        assert storage.get_document(doc["id"])["status"] == "ready"
    finally:
        jobs.stop_worker()
