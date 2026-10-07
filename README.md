# FolioMind — Multimodal Document Intelligence

Hackathon repo: [https://github.com/MVSanjayNiranjan/Hacknex2026](https://github.com/MVSanjayNiranjan/Hacknex2026)

A fully local NotebookLM-inspired prototype: upload multiple PDFs into a
notebook, ask questions across text, tables, charts, and scanned pages, and
get answers whose every claim cites a document and page you can open in one
click.

- **Backend:** Python 3.12 + Flask
- **Frontend:** HTML / CSS / vanilla JS (Flask templates, no React/Node)
- **Inference:** `llama-cpp-python` with `Qwen25VLChatHandler` +
  `Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf` + matching `mmproj-F16.gguf`
- **Storage:** SQLite (notebooks, documents, pages, elements, FTS5 index,
  jobs, chat) + local files for PDFs and rendered page images

## What is (and is not) in this repository

This GitHub repo contains application source, tests, templates, and docs.
These are **not** committed (too large, machine-local, or generated):

| Path | Why it is missing | What to do |
|------|-------------------|------------|
| `Model/*.gguf` | ~6 GB of weights | Download both files in section 4 |
| `runtime_setup/*.whl` | CUDA wheel exceeds GitHub's 100 MB limit | Install `llama-cpp-python` in section 3 |
| `.venv/` | Local Python environment | Create one in section 1 |
| `data/` | Local SQLite DB, uploads, page images | Created automatically on first run |
| `.env` | Machine-specific config | Copy from `.env.example` in section 5 |

---

## 1. Clone and create a virtual environment

```powershell
git clone https://github.com/MVSanjayNiranjan/Hacknex2026.git
cd Hacknex2026
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

On macOS/Linux, use `python3.12 -m venv .venv` and `source .venv/bin/activate`.

## 2. Install ordinary dependencies

```powershell
pip install -r requirements.txt
```

## 3. Install a CUDA-enabled llama-cpp-python

The plain `pip install llama-cpp-python` wheel is **CPU-only** — it will work
but slowly. PyTorch's bundled CUDA does **not** help: llama.cpp is compiled
natively and needs its own CUDA build. A prebuilt CUDA `.whl` is **not** in
this repo.

**Option A — prebuilt CUDA wheel (try first):**

```powershell
pip install llama-cpp-python --upgrade --force-reinstall --no-cache-dir `
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
```

(Use the `cu121`/`cu124`/`cu126` index matching your installed NVIDIA driver /
CUDA runtime — check with `nvidia-smi`.)

**Option B — build from source** (required when no wheel matches):

Prerequisites — install BOTH first:
- [Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with the "Desktop development with C++" workload
- [CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) matching your driver (e.g. 12.4)

```powershell
$env:CMAKE_ARGS = "-DGGML_CUDA=on"
pip install llama-cpp-python --upgrade --force-reinstall --no-cache-dir
```

Verify:

```powershell
python -c "import llama_cpp; print(llama_cpp.__version__); print('GPU offload:', llama_cpp.llama_supports_gpu_offload())"
python -c "from llama_cpp.llama_chat_format import Qwen25VLChatHandler; print('Qwen25VLChatHandler OK')"
```

Both must print success. If `Qwen25VLChatHandler` is missing, your
llama-cpp-python is too old — upgrade it; do **not** substitute a text-only
chat handler (image inputs would be silently dropped).

## 4. Model files

Weights are **not** in GitHub. Create a `Model` folder in the project root and
download both files from
[unsloth/Qwen2.5-VL-7B-Instruct-GGUF](https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF)
(same revision for both):

- `Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf` (~4.7 GB) → `Model/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf`
- `mmproj-F16.gguf` (~1.3 GB) → `Model/mmproj-F16.gguf`

A mismatched projector produces garbage output or crashes; the app checks for
the file but cannot verify the pairing — download both from the same repo page.

## 5. Configure

```powershell
Copy-Item .env.example .env
```

Defaults are conservative for 8 GB VRAM (`N_GPU_LAYERS=20`, `N_CTX=8192`).
**No configuration is guaranteed to fit** — if you see CUDA out-of-memory,
lower `N_GPU_LAYERS` (try 12, then 8, then 0 for CPU).

## 6. Run

```powershell
python main.py
# open http://127.0.0.1:5000
```

## 7. First use

1. A default notebook is created (or click **+**).
2. Drag PDFs into the sidebar dropzone (or **Upload**); wait for "indexed".
3. Ask a question. Click any citation chip to open the exact page.
4. Scanned pages are indexed as visual evidence; enable
   `VISUAL_TRANSCRIPTION_ENABLED=1` in `.env` to also transcribe them with the
   vision model during indexing (slow).

## 8. Tests

```powershell
pytest tests/ -m "not worker" -v               # main suite (mocked model, no GPU/GGUF)
pytest tests/test_worker_integration.py -m worker -v   # real background-worker tests
python scripts/smoke_test_model.py             # REAL model + image smoke test
```

- The main suite never starts the background worker; worker integration tests
  are marked `worker`, use temporary storage, and always shut the worker down.
- The smoke test requires BOTH GGUF files and verifies the model actually
  reads chart labels (Q2→72, Q4→84, Q4 higher) — it does not run under pytest.

## Operational notes

- **One process only.** The job worker holds an OS-level lock at
  `<data>/app.worker.lock` (keyed by the canonical DB path). A second process
  serves HTTP but will not run jobs, and after a crash only the lock holder
  requeues abandoned `running` jobs.
- **Migrations.** `storage.init_db()` upgrades old databases in place
  (`PRAGMA user_version`, currently v3) — existing notebooks are preserved.

### Manual evaluation checklist

| # | Type | Try asking | Expect |
|---|------|-----------|--------|
| 1 | Text | a fact stated in one document | claim + clickable citation to the right page |
| 2 | Table | a value from a table (with units) | cited table element; correct unit preserved |
| 3 | Chart | "what do the charts show about X" | page image listed under "Page images analyzed"; values marked approximate |
| 4 | Scanned page | a question only a scan can answer | page analyzed visually; warning notes vision extraction |
| 5 | Cross-document | "compare A in doc1 with B in doc2" | citations from both documents |
| 6 | Unanswerable | something absent from all sources | "No matching evidence" or explicit unanswerable note — not a guess |
| 7 | Numeric | "percentage change from X% to Y%" | both percentage-point and relative results, formula shown |

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `model: files missing` badge | GGUF files not downloaded into `Model/`, or `.env` paths wrong |
| CUDA/cuBLAS DLL errors on load | CPU-only llama-cpp-python build — redo README section 3 |
| Out-of-memory on load or answer | Lower `N_GPU_LAYERS`; close other GPU apps; as last resort `N_GPU_LAYERS=0` |
| "prompt exceeded the model context window" | Lower `MAX_EVIDENCE_PAGES`/`RETRIEVAL_TOP_K` or raise `N_CTX` |
| Garbage vision output | Projector/model mismatch — re-download both from the same unsloth revision |
| Scanned page not searchable | Expected unless `VISUAL_TRANSCRIPTION_ENABLED=1`; it is still usable as visual evidence |
| Port 5000 already in use | Change `PORT` in `.env` |
| Slow first answer | First question loads ~6 GB of weights; subsequent answers are faster |

## Known limitations

- **Retrieval** is keyword/BM25 (SQLite FTS5) with question decomposition.
  Semantic embedding retrieval and visual embeddings are **not implemented** —
  paraphrased questions may miss evidence. To compensate, answering also
  visually scans a bounded set of scanned/graphics pages from the selected
  documents that text retrieval missed; when `MAX_EVIDENCE_PAGES` truncates
  that set, the answer discloses it instead of claiming nothing exists.
- **Layout extraction** uses PyMuPDF, not Docling (dependency weight). Complex
  multi-column layouts and borderless tables may be imperfect; table cells are
  joined with `|` and merged cells may misalign.
- **Citation support checking** is heuristic (whitespace-normalized quote
  containment with numeric-token boundaries, applied to short and long quotes
  alike). It removes claims with invented evidence IDs and flags unmatched
  quotes, but a verified quote does **not** prove the source supports the
  whole claim.
- **Calculation provenance**: every operand must cite a stored evidence ID
  whose text literally contains the value (numeric-boundary match). Unit and
  period mismatches are surfaced as notes; uncited or unverifiable numbers are
  rejected. Arithmetic runs only in whitelisted Python functions.
- **Region highlighting** uses real extractor bounding boxes mapped through
  the stored page/image dimensions (no hardcoded DPI); elements without
  coordinates open the full page with no highlight.
- **Jobs**: exactly ONE app process is supported. Jobs are claimed atomically
  and owned by a per-process token; after a crash, jobs left "running" by a
  dead process are requeued on startup. Answer jobs snapshot the selected
  sources at queue time, and chat messages are deduplicated per job.
- **Damaged documents**: corrupt/encrypted PDFs are rejected with a visible
  error and a retry control; partially-damaged pages that render but extract
  poorly are flagged with warnings, not silently fixed.
- Instructions inside uploaded documents are treated as untrusted content
  (prompt-injection isolation is by prompt design, not a hard sandbox).
