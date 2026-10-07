"""Bounded background job worker.

A single daemon thread drains the SQLite jobs queue, so indexing and answer
generation never block Flask request threads.

Concurrency semantics (documented for this local prototype):
- Exactly ONE application process is supported. Each process creates one
  worker token (pid + random); jobs are claimed atomically via
  UPDATE ... WHERE status='queued', so even competing threads/processes
  cannot execute the same job twice — the loser's update matches 0 rows.
- On startup, jobs left 'running' under a DIFFERENT token are requeued
  (crash recovery). Jobs owned by this process's token are never touched.
- Answer jobs snapshot the selected document IDs at enqueue time; sidebar
  checkbox changes afterwards cannot alter a queued job's sources.
- Chat messages carry job_id with a unique (job_id, role) index, so a
  retried/re-run job never duplicates chat messages.
"""
import json
import logging
import os
import threading
import time
import traceback
import uuid
from pathlib import Path

from config import Config
import storage
from services import answer_service, document_service

log = logging.getLogger(__name__)

_WORKER_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_worker_thread: threading.Thread | None = None
_start_lock = threading.Lock()
_stop = threading.Event()
_process_lock_handle = None  # held for the process lifetime


def acquire_process_lock():
    """Interprocess lock ensuring ONE active worker process.

    Uses an OS-level byte-range lock on data/worker.lock. A second process
    (or a second holder in tests) fails to acquire it and must NOT start a
    worker or requeue jobs — so an active job can never be stolen. Returns
    the open handle (keep it alive) or None when another process holds it.
    """
    global _process_lock_handle
    if _process_lock_handle is not None:
        return _process_lock_handle
    # Lock identity derives from the CANONICAL database path, so two processes
    # pointed at the same DB via different DATA_DIR spellings still contend
    # for one lock, and two different databases never block each other.
    lock_path = Path(Config.DB_PATH).resolve().with_suffix(".worker.lock")
    fh = open(lock_path, "a+b")
    fh.seek(0)  # byte-range lock anchors at the current position
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    _process_lock_handle = fh
    return fh


def enqueue_index(notebook_id: str, document_id: str) -> str:
    return storage.create_job(
        "index_document", {"document_id": document_id},
        notebook_id=notebook_id, document_id=document_id)


def enqueue_answer(notebook_id: str, question: str, document_ids=None) -> str:
    """Queue an answer job.

    document_ids semantics:
    - None: snapshot the CURRENT selected+ready documents (default behavior,
      frozen for this job).
    - []:   explicitly no sources; the job will report that, never search all.
    - [...]: used as-is (validated against the notebook at run time).
    """
    if document_ids is None:
        document_ids = storage.selected_ready_document_ids(notebook_id)
    return storage.create_job(
        "answer",
        {"question": question, "document_ids": document_ids},
        notebook_id=notebook_id)


def run_job(job: dict) -> None:
    """Execute one claimed job (also called synchronously by tests)."""
    job_id = job["id"]
    payload = json.loads(job["payload"] or "{}")
    if job["status"] != "running":
        storage.update_job(job_id, status="running", progress=0.0,
                           message="Starting", owner=_WORKER_TOKEN)
    try:
        if job["kind"] == "index_document":
            def cb(frac, msg):
                storage.update_job(job_id, progress=round(frac, 3), message=msg)
            result = document_service.index_document(payload["document_id"], cb)
        elif job["kind"] == "answer":
            def cb(msg):
                storage.update_job(job_id, message=msg)
            result = answer_service.answer_question(
                notebook_id=job["notebook_id"],
                question=payload["question"],
                document_ids=payload.get("document_ids"),
                job_id=job_id,
                progress_cb=cb,
            )
        else:
            raise ValueError(f"Unknown job kind {job['kind']}")
        storage.update_job(job_id, status="done", progress=1.0,
                           message="Complete", result=result)
    except Exception as exc:
        log.error("job %s failed:\n%s", job_id, traceback.format_exc())
        storage.update_job(job_id, status="error", error=str(exc), message="Failed")
        if job["kind"] == "index_document":
            storage.update_document(job["document_id"], status="error", error=str(exc))


def _loop(poll_seconds: float = 0.5) -> None:
    while not _stop.is_set():
        job = storage.claim_next_queued_job(_WORKER_TOKEN)
        if job is None:
            time.sleep(poll_seconds)
            continue
        run_job(job)


def start_worker(recover: bool = True) -> bool:
    """Start the single worker thread exactly once (synchronized).

    Returns False without starting anything when another live process holds
    the interprocess worker lock — in that case recovery is also skipped, so
    jobs owned by the live worker are never requeued or stolen.
    """
    global _worker_thread
    with _start_lock:
        if _worker_thread and _worker_thread.is_alive():
            return True
        if acquire_process_lock() is None:
            log.warning("another process holds the worker lock; this process "
                        "will serve HTTP only and will not run jobs")
            return False
        if recover:
            # Safe: we hold the process lock, so any 'running' job owned by a
            # different token belongs to a crashed process, not a live one.
            n = storage.recover_abandoned_jobs(_WORKER_TOKEN)
            if n:
                log.info("requeued %d abandoned job(s) from a previous process", n)
        _stop.clear()
        _worker_thread = threading.Thread(target=_loop, daemon=True,
                                          name=f"job-worker-{_WORKER_TOKEN}")
        _worker_thread.start()
        return True


def stop_worker() -> None:
    """Stop the worker; release the interprocess lock ONLY after the worker
    thread has actually exited. If the thread is still alive (stuck job), the
    lock is kept so no other process can steal the queue mid-job."""
    global _process_lock_handle
    _stop.set()
    if _worker_thread:
        _worker_thread.join(timeout=10)
    if _worker_thread and _worker_thread.is_alive():
        log.warning("worker thread still running; keeping the process lock")
        return
    if _process_lock_handle is not None:
        try:
            _process_lock_handle.close()  # releases the OS lock
        finally:
            _process_lock_handle = None
