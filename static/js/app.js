/* FolioMind frontend. All rendering uses textContent / createElement — no
 * untrusted HTML is ever injected via innerHTML. */
"use strict";

const state = {
  notebookId: null,
  documents: [],
  activeJobs: new Map(), // job_id -> {kind, onDone}
  pollTimer: null,
};

const $ = (sel) => document.querySelector(sel);
const displayText = (value) => String(value).replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (whole, entity) => {
  const names = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' '};
  if (entity[0] !== '#') return names[entity.toLowerCase()] || whole;
  const point = entity[1].toLowerCase() === 'x' ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10);
  return point > 0 && point <= 0x10ffff && !(point >= 0xd800 && point <= 0xdfff) ? String.fromCodePoint(point) : whole;
});
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = displayText(text); // decoded text remains safe text
  return n;
};

async function api(path, opts = {}) {
  const resp = await fetch(path, opts);
  let body = null;
  try { body = await resp.json(); } catch { /* non-JSON (e.g. 413 page) */ }
  if (!resp.ok) {
    throw new Error((body && body.error) || `Request failed (${resp.status})`);
  }
  return body;
}

function toast(message, isError = false) {
  const t = el("div", "toast" + (isError ? " error" : ""), message);
  $("#toast-area").appendChild(t);
  setTimeout(() => t.remove(), 6000);
}

/* ---------------- notebooks ---------------- */

async function loadNotebooks() {
  const nbs = await api("/api/notebooks");
  const list = $("#notebook-list");
  list.textContent = "";
  for (const nb of nbs) {
    const li = el("li", nb.id === state.notebookId ? "active" : "",
      `${nb.name} (${nb.document_count})`);
    li.addEventListener("click", () => selectNotebook(nb.id));
    list.appendChild(li);
  }
  if (!state.notebookId && nbs.length) return selectNotebook(nbs[0].id);
  if (!nbs.length) {
    const nb = await api("/api/notebooks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "My first notebook" }),
    });
    return selectNotebook(nb.id);
  }
}

async function selectNotebook(id) {
  state.notebookId = id;
  await loadNotebookListOnly();
  await Promise.all([loadDocuments(), loadChat()]);
}

async function loadNotebookListOnly() {
  const nbs = await api("/api/notebooks");
  const list = $("#notebook-list");
  list.textContent = "";
  for (const nb of nbs) {
    const li = el("li", nb.id === state.notebookId ? "active" : "",
      `${nb.name} (${nb.document_count})`);
    li.addEventListener("click", () => selectNotebook(nb.id));
    list.appendChild(li);
  }
}

/* ---------------- documents ---------------- */

async function loadDocuments() {
  // Snapshot the notebook; discard the response if the user switched away
  // before it arrived (prevents a stale list overwriting current sources).
  const nb = state.notebookId;
  if (!nb) return;
  const docs = await api(`/api/notebooks/${nb}/documents`);
  if (state.notebookId !== nb) return;
  state.documents = docs;
  renderSources();
  updateCounts();
}

function updateCounts() {
  $("#source-count").textContent = `${state.documents.length} source${state.documents.length === 1 ? "" : "s"}`;
  const sel = state.documents.filter((d) => d.selected).length;
  $("#selected-count").textContent = `${sel} selected`;
}

function renderSources() {
  const list = $("#source-list");
  list.textContent = "";
  $("#source-empty").classList.toggle("hidden", state.documents.length > 0);

  for (const doc of state.documents) {
    const li = el("li");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = !!doc.selected;
    cb.disabled = doc.status !== "ready";
    cb.title = doc.status !== "ready" ? "Available once indexing completes" : "Include in answers";
    cb.addEventListener("change", async () => {
      await api(`/api/documents/${doc.id}/select`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected: cb.checked }),
      });
      doc.selected = cb.checked ? 1 : 0;
      updateCounts();
    });

    const info = el("div", "source-info");
    info.appendChild(el("div", "source-name", doc.name));
    info.appendChild(el("div", "source-meta",
      `${doc.page_count || "?"} pages · ${(doc.size_bytes / 1e6).toFixed(1)} MB`));

    const status = el("div", `source-status status-${doc.status}`);
    const labels = { ready: "indexed", pending: "queued…", indexing: "indexing…", error: "error" };
    status.textContent = labels[doc.status] || doc.status;
    info.appendChild(status);

    if (doc.status === "error") {
      info.appendChild(el("div", "source-status status-error", doc.error || "indexing failed"));
      const retry = el("button", "btn btn-small retry-btn", "Retry");
      retry.addEventListener("click", async () => {
        const r = await api(`/api/documents/${doc.id}/retry`, { method: "POST" });
        trackJob(r.job_id, "index");
        toast("Re-indexing queued");
      });
      info.appendChild(retry);
    }
    if (doc.status === "indexing" || doc.status === "pending") {
      const bar = el("div", "progress-bar");
      bar.appendChild(el("div"));
      bar.firstChild.style.width = "5%";
      bar.dataset.docId = doc.id;
      info.appendChild(bar);
    }

    li.appendChild(cb);
    li.appendChild(info);
    list.appendChild(li);
  }
}

async function uploadFiles(files) {
  if (!state.notebookId || !files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  let resp, body = null;
  try {
    resp = await fetch(`/api/notebooks/${state.notebookId}/documents`, { method: "POST", body: fd });
    try { body = await resp.json(); } catch { body = null; } // e.g. 413 HTML page
  } catch (e) {
    toast(`Upload failed: ${e.message}`, true);
    await loadDocuments();  // restore consistent state after failure
    return;
  }
  if (!resp.ok) {
    const msg = (body && body.error) ||
      (resp.status === 413 ? "Upload too large for the configured limit." :
       `Upload failed (HTTP ${resp.status}).`);
    toast(msg, true);
    await loadDocuments();
    return;
  }
  for (const r of body.results || []) {
    if (r.status === "queued") {
      toast(`Indexing "${r.document.name}"…`);
      trackJob(r.job_id, "index");
    } else if (r.status === "duplicate") {
      toast(`"${r.document.name}" is a duplicate — already in this notebook.`);
    } else {
      toast(`${r.filename}: ${r.message}`, true);
    }
  }
  await loadDocuments();
}

/* ---------------- jobs ---------------- */

function trackJob(jobId, kind) {
  state.activeJobs.set(jobId, { kind });
  if (!state.pollTimer) {
    state.pollTimer = setInterval(pollJobs, 1200);
  }
  if (kind === "answer") showChatStatus("Queued…");
}

async function pollJobs() {
  for (const [jobId, meta] of state.activeJobs) {
    let job;
    try { job = await api(`/api/jobs/${jobId}`); }
    catch { state.activeJobs.delete(jobId); continue; }

    if (meta.kind === "index") {
      const bar = document.querySelector(`.progress-bar[data-doc-id="${job.document_id}"] > div`);
      if (bar) bar.style.width = `${Math.max(5, (job.progress || 0) * 100)}%`;
    }
    if (meta.kind === "answer") showChatStatus(job.message || "Working…");

    if (job.status === "done") {
      state.activeJobs.delete(jobId);
      if (meta.kind === "index") { await loadDocuments(); await loadNotebookListOnly(); toast("Indexing complete"); }
      if (meta.kind === "answer") { hideChatStatus(); await loadChat(); }
    } else if (job.status === "error") {
      state.activeJobs.delete(jobId);
      if (meta.kind === "index") { await loadDocuments(); toast(job.error || "Indexing failed", true); }
      if (meta.kind === "answer") { hideChatStatus(); toast(job.error || "Answering failed", true); }
    }
  }
  if (!state.activeJobs.size && state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

/* ---------------- chat ---------------- */

function showChatStatus(msg) {
  const s = $("#chat-status");
  s.textContent = msg;
  s.classList.remove("hidden");
  $("#ask-btn").disabled = true;
}
function hideChatStatus() {
  $("#chat-status").classList.add("hidden");
  $("#ask-btn").disabled = false;
}

async function loadChat() {
  const nb = state.notebookId;
  if (!nb) return;
  const msgs = await api(`/api/notebooks/${nb}/chat`);
  if (state.notebookId !== nb) return;  // stale response from old notebook
  const box = $("#chat-history");
  box.textContent = "";
  $("#chat-empty") && $("#chat-empty").remove();
  if (!msgs.length) {
    box.appendChild(buildEmptyChat());
    return;
  }
  for (const m of msgs) {
    box.appendChild(m.role === "user" ? buildUserMsg(m) : buildAssistantMsg(m));
  }
  box.scrollTop = box.scrollHeight;
}

function buildEmptyChat() {
  const div = el("div", "empty-state chat-empty");
  div.id = "chat-empty";
  div.appendChild(el("h3", "", "Ask across your documents"));
  div.appendChild(el("p", "", "Answers cite exact documents and pages. Visual questions are checked against the actual page images."));
  return div;
}

function buildUserMsg(m) {
  return el("div", "msg msg-user", m.content);
}

function buildAssistantMsg(m) {
  let data;
  try { data = JSON.parse(m.content); } catch { data = { message: m.content }; }
  const div = el("div", "msg msg-assistant");

  if (data.message && !(data.claims || []).length) {
    div.appendChild(el("p", "", data.message));
  }

  for (const claim of data.claims || []) {
    const c = el("div", "claim");
    c.appendChild(el("span", "", claim.text + " "));
    for (const eid of claim.evidence_ids || []) {
      const cit = (data.citations || {})[eid];
      if (!cit) continue;
      const pageLabel = cit.printed_page_label && cit.printed_page_label !== String(cit.page_number)
        ? `${cit.page_number} (label ${cit.printed_page_label})` : `${cit.page_number}`;
      const btn = el("button", "cite-btn", `${cit.document_name} · p.${pageLabel}`);
      btn.title = `${cit.section} · ${cit.content_type}`;
      btn.addEventListener("click", () => openSource(cit));
      c.appendChild(btn);
    }
    if (claim.quote) c.appendChild(el("div", "quote", `“${claim.quote}”`));
    if (claim.support_check === "unverified_no_quote") {
      c.appendChild(el("div", "support-flag",
        "⚠ no verifiable quote supplied — check the cited page before relying on this claim"));
    }
    div.appendChild(c);
  }

  for (const calc of data.calculations || []) {
    const box = el("div", "calc-box");
    const unit = calc.unit ? ` ${calc.unit}` : "";
    box.appendChild(el("div", "", `${calc.operation}: ${calc.value}${unit}`));
    box.appendChild(el("div", "formula", `formula: ${calc.formula}`));
    const inputs = el("div", "formula");
    inputs.textContent = "inputs: " + (calc.inputs || []).map((i) => {
      let s = `${i.value}${i.unit || ""}`;
      if (i.period) s += ` [${i.period}]`;
      if (i.approximate) s += " (approx., from chart)";
      const cit = (data.citations || {})[i.evidence_id];
      if (cit) s += ` — ${cit.document_name} p.${cit.page_number}`;
      return s;
    }).join("; ");
    box.appendChild(inputs);
    for (const note of calc.notes || []) box.appendChild(el("div", "formula", `note: ${note}`));
    div.appendChild(box);
  }

  if ((data.pages_analyzed || []).length) {
    const pa = el("div", "source-meta");
    pa.textContent = "Page images analyzed: " + data.pages_analyzed
      .map((p) => `${p.document_name || "document"} p.${p.page_number}`).join(", ");
    div.appendChild(pa);
  }

  if ((data.unanswerable_parts || []).length) {
    const ua = el("div", "limitations");
    ua.textContent = "Could not answer from the sources: " + data.unanswerable_parts.join("; ");
    div.appendChild(ua);
  }
  if ((data.limitations || []).length) {
    const h = el("h4", "", "Limitations");
    div.appendChild(h);
    for (const l of data.limitations) div.appendChild(el("div", "limitations", `• ${l}`));
  }
  return div;
}

async function ask(question) {
  showChatStatus("Queued…");
  try {
    const r = await api(`/api/notebooks/${state.notebookId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    trackJob(r.job_id, "answer");
  } catch (e) {
    hideChatStatus();
    toast(e.message, true);
  }
}

/* ---------------- source viewer ---------------- */

async function openSource(cit) {
  const viewer = $("#viewer");
  viewer.classList.add("open");
  $("#viewer-empty").classList.add("hidden");
  $("#viewer-content").classList.remove("hidden");

  // Request token: any response arriving after a NEWER openSource call is
  // stale and discarded (covers rapid citation switching).
  state._srcReq = (state._srcReq || 0) + 1;
  const req = state._srcReq;
  const alive = () => req === state._srcReq;

  // Clear the PREVIOUS citation's handlers/highlight first — including when
  // the new citation has no coordinates at all.
  const img = $("#viewer-image");
  const hl = $("#viewer-highlight");
  if (state._hlResize) {
    window.removeEventListener("resize", state._hlResize);
    state._hlResize = null;
  }
  hl.classList.add("hidden");

  const meta = $("#viewer-meta");
  const pageLabel = cit.printed_page_label && cit.printed_page_label !== String(cit.page_number)
    ? `PDF page ${cit.page_number} · printed label ${cit.printed_page_label}`
    : `PDF page ${cit.page_number}`;
  meta.textContent = displayText(`${cit.document_name} — ${pageLabel} — ${cit.section}`);
  const txt = $("#viewer-text");
  txt.textContent = "";

  // Register the load handler BEFORE setting src so no load event is ever
  // missed; the token guards against painting onto a superseded image.
  let pendingBox = null;
  const paint = () => {
    if (!alive() || !pendingBox || !img.naturalWidth) return;
    // Independent x/y scales from the CURRENT displayed size; stays aligned
    // after window resizes and with rotated/cropped pages (backend already
    // mapped points -> rendered pixels with rotation applied).
    const sx = img.clientWidth / img.naturalWidth;
    const sy = img.clientHeight / img.naturalHeight;
    hl.style.left = `${img.offsetLeft + pendingBox[0] * sx}px`;
    hl.style.top = `${img.offsetTop + pendingBox[1] * sy}px`;
    hl.style.width = `${(pendingBox[2] - pendingBox[0]) * sx}px`;
    hl.style.height = `${(pendingBox[3] - pendingBox[1]) * sy}px`;
    hl.classList.remove("hidden");
  };
  img.onload = paint;
  img.src = `/api/documents/${cit.document_id}/pages/${cit.page_number}/image`;
  // Cached images may not fire onload again in some browsers.
  if (img.complete && img.naturalWidth) setTimeout(paint, 0);

  try {
    const detail = await api(`/api/documents/${cit.document_id}/pages/${cit.page_number}`);
    if (!alive()) return;  // user already opened another citation
    txt.textContent = displayText(detail.elements.map((e) => e.text).join("\n\n").slice(0, 4000));

    // Highlight only with backend-mapped image-pixel coordinates (bbox_px).
    // No coordinates -> the page opens WITHOUT any highlight, never a guess.
    const match = detail.elements.find((e) => e.id === cit.evidence_id);
    const box = match && match.bbox_px;
    if (box && detail.page.image_width) {
      pendingBox = box;
      state._hlResize = paint;
      window.addEventListener("resize", paint);
      paint();  // image may already be loaded
    }
  } catch { /* viewer still shows the page image */ }
}

/* ---------------- model status ---------------- */

async function refreshModelStatus() {
  const badge = $("#model-status");
  try {
    const s = await api("/api/model/status");
    const labels = {
      loaded: s.visual_inference_tested ? "model: loaded, vision tested"
        : "model: loaded, vision untested",
      not_loaded: "model: files present, not yet loaded",
      missing_files: !s.model_file_present ? "model: model file missing"
        : "model: projector file missing",
      error: "model: error",
    };
    badge.textContent = labels[s.state] || s.state;
    badge.className = "badge " + (
      s.state === "loaded" && s.visual_inference_tested ? "badge-ok"
      : s.state === "error" || s.state === "missing_files" ? "badge-err"
      : "badge-warn");
    badge.title = [
      `model file: ${s.model_file_present ? "found" : "MISSING"}`,
      `projector file: ${s.projector_file_present ? "found" : "MISSING"}`,
      `vision handler: ${s.handler_available ? "available" : "MISSING"}`,
      `GPU offload build support: ${s.gpu_offload_supported === null ? "unknown" : s.gpu_offload_supported}`,
      `loaded: ${s.loaded}`,
      `GPU placement observed: ${s.gpu_placement_observed === null ? "not verifiable" : s.gpu_placement_observed}`,
      `visual inference tested: ${s.visual_inference_tested}`,
      s.error || "",
    ].join("\n");
  } catch {
    badge.textContent = "model: status unavailable";
    badge.className = "badge badge-err";
  }
}

/* ---------------- wiring ---------------- */

function init() {
  $("#new-notebook-btn").addEventListener("click", async () => {
    const name = prompt("Notebook name:");
    if (!name) return;
    const nb = await api("/api/notebooks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await loadNotebooks();
    await selectNotebook(nb.id);
  });

  const fi = $("#file-input");
  $("#upload-btn").addEventListener("click", () => fi.click());
  $("#dropzone-browse").addEventListener("click", () => fi.click());
  fi.addEventListener("change", () => { uploadFiles(fi.files); fi.value = ""; });

  const dz = $("#dropzone");
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    uploadFiles(e.dataTransfer.files);
  });

  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const ta = $("#chat-question");
    const q = ta.value.trim();
    if (!q) return;
    ta.value = "";
    ask(q);
  });

  document.querySelectorAll(".suggestion").forEach((b) =>
    b.addEventListener("click", () => ask(b.textContent)));

  $("#viewer-close").addEventListener("click", () => $("#viewer").classList.remove("open"));

  loadNotebooks().catch((e) => toast(e.message, true));
  refreshModelStatus();
  setInterval(refreshModelStatus, 30000);
}

document.addEventListener("DOMContentLoaded", init);
