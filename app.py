"""Flask application: notebook UI, uploads, jobs, chat, and source viewer.

Long-running work (indexing, answering) runs in the background worker; routes
only enqueue jobs and expose progress. Uploaded file names are treated as
untrusted display strings — storage uses generated internal paths.
"""
import json
import logging
import os
from pathlib import Path

from flask import (Flask, abort, jsonify, render_template, request,
                   send_file)

from config import Config
import storage
from services import document_service, highlight_service, jobs, model_service

log = logging.getLogger(__name__)


def create_app(db_path=None, start_jobs=True) -> Flask:
    Config.ensure_dirs()
    if db_path:
        Config.DB_PATH = Path(db_path)
    storage.init_db(Config.DB_PATH)

    app = Flask(__name__)
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = (Config.MAX_FILE_MB *
                                        Config.MAX_FILES_PER_UPLOAD) * 1024 * 1024

    if start_jobs:
        jobs.start_worker()

    # --- pages ------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    # --- notebooks ----------------------------------------------------------
    @app.get("/api/notebooks")
    def list_notebooks():
        return jsonify(storage.list_notebooks())

    @app.post("/api/notebooks")
    def create_notebook():
        name = (request.json or {}).get("name", "").strip()
        if not name:
            return jsonify({"error": "Notebook name is required."}), 400
        return jsonify(storage.create_notebook(name)), 201

    # --- documents ----------------------------------------------------------
    @app.get("/api/notebooks/<nb_id>/documents")
    def list_documents(nb_id):
        if not storage.get_notebook(nb_id):
            abort(404)
        return jsonify(storage.list_documents(nb_id))

    @app.post("/api/notebooks/<nb_id>/documents")
    def upload_documents(nb_id):
        if not storage.get_notebook(nb_id):
            abort(404)
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "No files were included in the request."}), 400
        if len(files) > Config.MAX_FILES_PER_UPLOAD:
            return jsonify({"error": f"Too many files at once; the limit is "
                                     f"{Config.MAX_FILES_PER_UPLOAD}."}), 400
        results = []
        for f in files:
            try:
                doc = document_service.save_upload(nb_id, f.filename or "", f.read())
                if doc.get("duplicate"):
                    results.append({"document": doc, "status": "duplicate",
                                    "message": "Identical file already exists in this notebook."})
                else:
                    job_id = jobs.enqueue_index(nb_id, doc["id"])
                    results.append({"document": doc, "status": "queued", "job_id": job_id})
            except document_service.IngestionError as exc:
                results.append({"filename": f.filename, "status": "rejected",
                                "message": str(exc)})
        return jsonify({"results": results}), 207

    @app.post("/api/documents/<doc_id>/retry")
    def retry_document(doc_id):
        doc = storage.get_document(doc_id)
        if not doc:
            abort(404)
        job_id = jobs.enqueue_index(doc["notebook_id"], doc_id)
        return jsonify({"job_id": job_id, "status": "queued"})

    @app.post("/api/documents/<doc_id>/select")
    def select_document(doc_id):
        doc = storage.get_document(doc_id)
        if not doc:
            abort(404)
        selected = bool((request.json or {}).get("selected"))
        storage.set_document_selected(doc_id, selected)
        return jsonify({"id": doc_id, "selected": selected})

    # --- chat / answering ------------------------------------------------------
    @app.get("/api/notebooks/<nb_id>/chat")
    def chat_history(nb_id):
        if not storage.get_notebook(nb_id):
            abort(404)
        return jsonify(storage.list_chat_messages(nb_id))

    @app.post("/api/notebooks/<nb_id>/chat")
    def ask(nb_id):
        if not storage.get_notebook(nb_id):
            abort(404)
        question = ((request.json or {}).get("question") or "").strip()
        if not question:
            return jsonify({"error": "Question is required."}), 400
        docs = [d for d in storage.list_documents(nb_id)
                if d["selected"] and d["status"] == "ready"]
        if not docs:
            return jsonify({"error": "No indexed sources are selected. Upload and "
                                     "select at least one document first."}), 400
        job_id = jobs.enqueue_answer(nb_id, question)
        return jsonify({"job_id": job_id}), 202

    # --- jobs --------------------------------------------------------------------
    @app.get("/api/jobs/<job_id>")
    def job_status(job_id):
        job = storage.get_job(job_id)
        if not job:
            abort(404)
        out = {k: job[k] for k in ("id", "kind", "status", "progress",
                                   "message", "error", "document_id")}
        if job["status"] == "done" and job["result"]:
            out["result"] = json.loads(job["result"])
        return jsonify(out)

    @app.get("/api/notebooks/<nb_id>/jobs")
    def recent_jobs(nb_id):
        return jsonify(storage.list_recent_jobs(nb_id))

    # --- source viewer -------------------------------------------------------------
    @app.get("/api/documents/<doc_id>/pages/<int:page_number>/image")
    def page_image(doc_id, page_number):
        page = storage.get_page(doc_id, page_number)
        if not page or not page["image_path"] or not Path(page["image_path"]).is_file():
            abort(404)
        return send_file(page["image_path"], mimetype="image/png")

    @app.get("/api/documents/<doc_id>/pages/<int:page_number>")
    def page_detail(doc_id, page_number):
        page = storage.get_page(doc_id, page_number)
        if not page:
            abort(404)
        els = storage.page_elements(doc_id, page_number)
        # Pre-map extractor coordinates (PDF points, unrotated) into rendered
        # image pixels via the rotation-aware transform. Elements without real
        # coordinates keep bbox_px=null and get NO highlight — never a guess.
        for el in els:
            bbox = json.loads(el["bbox"]) if el.get("bbox") else None
            el["bbox"] = bbox
            el["bbox_px"] = highlight_service.bbox_to_image_px(
                bbox, page.get("page_width_pts"), page.get("page_height_pts"),
                page.get("rotation") or 0,
                page.get("image_width"), page.get("image_height"),
                page.get("crop_x0") or 0.0, page.get("crop_y0") or 0.0)
            el["warnings"] = json.loads(el.get("warnings") or "[]")
        doc = storage.get_document(doc_id)
        return jsonify({"page": page, "elements": els,
                        "document_name": doc["name"] if doc else None})

    @app.get("/api/documents/<doc_id>/file")
    def document_file(doc_id):
        doc = storage.get_document(doc_id)
        if not doc or not Path(doc["stored_path"]).is_file():
            abort(404)
        return send_file(doc["stored_path"], mimetype="application/pdf",
                         as_attachment=False, download_name=doc["name"])

    # --- model status ------------------------------------------------------------------
    @app.get("/api/model/status")
    def model_status():
        return jsonify(model_service.model_status())

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": "Upload too large."}), 413

    return app


# NOTE: no module-level app creation on purpose. Importing this module must
# have no side effects (no DB init, no worker start) so tests can configure
# temporary storage first. main.py calls create_app() explicitly.
