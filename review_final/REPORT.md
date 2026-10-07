# Final implementation verification — 7 October 2026

## A. Overall verdict

The Flask server, native-PDF ingestion, SQLite persistence, source selection,
and background indexing actually run against isolated data. The application
is **not verified as a working multimodal document assistant**.

- UI/upload demonstration: **limited backend demonstration only**. Native
  uploads/indexing work; concurrent publication and notebook-switching defects
  remain. A reliable browser demonstration is not yet verified.
- Local Qwen vision inference: **not ready**. Configured projector absent;
  llama-cpp-python reports GPU-offload support false. No weights were loaded.
- Basic multimodal demo: **not ready**. Real visual understanding, degraded-scan
  uncertainty, conflict disclosure, causal restraint, and citation-supported
  model answers have not been demonstrated.
- Remaining requirements: reliable concurrent uploads, unambiguous operand
  provenance, cropped-page highlighting, simple-vector discovery, rejection
  of unsupported factual answers, trustworthy smoke acceptance, actual browser
  interaction and actual Qwen inference.

No application code was changed and no dependencies were installed. Review
files are confined to `review_final/`; harnesses used isolated data. The owned
HTTP server and worker stopped and their temporary storage was removed.
Production source/test hashes matched the previous pass. Existing production
data was not opened by the harnesses. No .env contents or production logs were
printed or copied.

## Environment and collection safety

README intends Python 3.12 in `.venv`. No `.venv/Scripts/python.exe`, active
virtual environment, or `.env` exists here. Tests used the available matching
interpreter:

`C:\Users\sanja\AppData\Local\Programs\Python\Python312\python.exe`

Python 3.12.10; Flask 3.1.3; PyMuPDF 1.28.2; Pillow 12.3.0;
python-dotenv 1.2.2; pytest 9.0.2; llama-cpp-python 0.3.36. These satisfy the
ordinary declared minimum versions. llama-cpp-python is a separate runtime
dependency in the README.

Configured model: `Model/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf`, present,
4,683,072,384 bytes. Configured projector: `Model/mmproj-F16.gguf`, absent.
`Qwen25VLChatHandler` imports successfully. Actual llama-cpp-python capability
query reports `gpu_offload_supported=false`; loaded=false and
visual_inference_tested=false. This is a runtime API result, not a conclusion
from PyTorch or DLL filenames. GPU placement, peak VRAM, load latency, inference
latency and context fit remain unmeasured.

Effective safe settings: N_GPU_LAYERS=20, N_CTX=8192, N_BATCH=256,
MAX_IMAGE_DIM=1024, MAX_TOKENS=1024, MAX_EVIDENCE_PAGES=6;
index-time visual transcription disabled. README documents separate CUDA
installation and obtaining a matching projector. Neither action was taken.

Inspection of app.py/main.py and test-module top levels found no module-level
app creation, database initialization, job recovery or weight loading. Config
reads configuration without making data directories; services initialize only
in-memory state at import. Main creates the app only under its main guard.
Before collection the harness redirected all data paths to temporary storage
and installed guards against init_db, recovery, worker startup and model load.
Collection succeeded without triggering them. Runtime tests then restored the
required database operations; real weight loading remained forbidden.

## B. Actual test results

These results are new reviewer executions, not builder-log results. Commands
ran from `C:\Users\sanja\Sonion`. `python` below is the absolute interpreter
identified above. Test runners set PYTHONDONTWRITEBYTECODE=1,
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 and VISUAL_TRANSCRIPTION_ENABLED=0.

1. `python -B review_final/environment.py` — exit 0, environment check.
2. `python -B review_final/run_suite.py --collect-only` — exit 0;
   62 selected of 64, 2 deselected, no executed tests.
3. `python -B review_final/run_suite.py` — exit 0;
   **62 passed, 0 failed, 0 skipped, 2 deselected**, 4.71 seconds in the saved run.
4. `python -B review_final/fixtures.py` — exit 0; fixture generation.
5. `python -B -m pytest review_final/test_functional.py -q -p no:cacheprovider --tb=short --basetemp review_final/functional_tmp --junitxml review_final/functional-results.xml`
   — exit 1; **17 passed, 19 failed, 0 skipped**, 4.29 seconds in the final saved run.
6. `python -B review_final/runtime_checks.py` — exit 0;
   **17 checks passed, 0 failed, 0 skipped**, isolated HTTP and actual worker
   behavior. This is a bounded custom harness, not the builder worker suite.
7. `python -B -m pytest review_final/test_migrations.py -q -p no:cacheprovider --tb=short --basetemp review_final/migration_tmp --junitxml review_final/migration-results.xml`
   — exit 0; **3 passed, 0 failed, 0 skipped**, 0.21 seconds.
8. `Get-Content -Raw review_final/frontend_checks.cjs | node -` — exit 1;
   **6 passed, 1 failed, 0 skipped**, deterministic JS/DOM simulations.
   This is explicitly **not actual browser testing**.

Commands 1–6 were captured by `python -B review_final/run_all.py` (exit 1
because requirements failed). Final functional/migration/frontend records were
captured by `python -B review_final/finalize_runs.py` (exit 0; it records child
failures rather than using its own exit as a test verdict). Exact executable
arguments, outputs and exit codes are in execution.json and command-N.txt.

Fixture development history is excluded from final defect counts: first
functional run was 16 passed/18 failed; three operand checks lacked a document
ID, and the standalone-table fixture already had an inferred table. Correcting
those review fixtures produced 17 passed/17 failed. Two additional legitimate
number-format cases were then added. A 16 passed/20 failed run contained an
over-specific assumption about which of two equivalent fallback pages must
fit under the page cap; this review assertion was corrected. Final result is
17 passed/19 failed. The same functional command was also run with `--tb=line`
and produced 17 passed/19 failed. No production code changed during these
iterations.

The initial `node review_final/frontend_checks.cjs` invocation failed before
execution with EPERM resolving the parent user directory. Supplying the script
through stdin worked. An intermediate simulation produced 5 passed/2 failed;
one failure was a review harness access to a lexical const. Correcting the
harness left 6 passed/1 genuine race failure. Two exploratory SVG commands
included one quoting error followed by a successful probe; these were fixture
development, not application tests.

### What passed and what the tests actually establish

- Fresh initialization and v1 migrations precede dependent index creation.
  Additional tests preserved notebook, document, page, element, job and chat
  rows plus FTS search across unversioned/v1/partially migrated databases;
  repeated initialization remained safe. Unlinking database files verified
  connection closure on Windows.
- Notebook/source boundaries, immutable queued source snapshots, explicit
  empty selection, atomic claiming, foreign-owner crash recovery logic and
  deduplicated chat messages passed the builder regressions.
- Multi-upload API returned queued/queued/duplicate. Real indexing produced
  native text, the explicit Q4 table, correct physical page positions and
  printed labels. Document/image/detail routes returned actual files.
- Empty and oversized inputs were rejected; corrupt and encrypted documents
  reached error status through jobs. HTTP 413 was JSON. Missing citation routes
  returned 404; frontend error parsing handled a simulated non-JSON 413.
- Chart-only vector, raster scan, blurred scan and partially unreadable scan
  reached visual routing with zero native text. **Mock vision returned no
  observations**: these passes establish routing and no fabricated fallback,
  not recognition or uncertainty from a real model.
- Visual pages remained discoverable alongside text hits. A cap of two over
  three candidates disclosed incomplete coverage; unique document/page counts
  did not double count overlap. A standalone stored table independently
  qualified a native prose page after confirming it had no other qualifying
  branch.
- Controlled, evidence-ID-backed calculation requests gave +12 percentage
  points and +16.6667% relative efficiency increase, retaining both input IDs.
  Direct arithmetic gave -8 hours and -40% downtime change. Synthesis was
  mocked: these are validation/arithmetic checks, not generated-answer accuracy.
- Missing/unknown/foreign evidence IDs and +100 against -100 were rejected.
- Rotated page rendering/coordinate tests passed. Cropped-page coordinates
  failed independently.
- HTTP template/static serving, notebook creation, selection, chat queueing,
  persisted mocked chat and a real background indexing job worked on port
  56685. The worker was denied in an actual second process while the first
  process held ownership. A controlled blocking job survived the 10-second
  shutdown join, retained the lock, and released it after actual exit.
- JS simulations passed cached-image highlighting, stale citation-response
  suppression, clearing highlights for coordinate-free citations, resizing,
  literal HTML-like text rendering and non-JSON error handling. They do not
  establish real browser event timing, layout or security.

### Skipped stages and limits

The builder's two worker integration tests were **not run**. Their fixture
clears `_worker_thread` and `_process_lock_handle` even if shutdown returns
while the thread is alive, so cleanup cannot be guaranteed. The separate
runtime harness used an owned child process with a 45-second parent timeout,
bounded jobs, explicit release events, shutdown assertions and isolated
storage. No unrelated process was killed.

Browser inventory returned no browsers. `cua.createBrowserTab('iab',
'about:blank', {visible:true})` failed with "Browser is not available: iab".
Picker/drag uploads, visible progress/failure display, real mobile layout,
actual cached-image events, rapid switching in a browser, interactive
rotation/CropBox display and screenshots are therefore untested. No browser
screenshots exist.

Real-model smoke, chart inference and application Qwen answers were skipped
because the projector is absent. The smoke script was inspected, and only its
recognition predicates were executed in isolation. No CPU inference or GPU
inference occurred. Instruction resistance, scan accuracy, unreadable-value
uncertainty, conflicting-source interpretation and actual causal/ranking
restraint remain unverified with Qwen.

## C. Confirmed defects and narrow fixes

### High: Upload publication can return a deleted document ID

Location: services/document_service.py:84–92.
Reproduction: pause A after row insertion but before os.replace; upload the
same bytes as B; B interprets A's missing file as a crashed publication and
deletes A's row. Resume A. Both return a fresh successful result, but A's ID is
absent. `test_publication_interleaving` independently fails with
`Successful upload returned deleted document ID`.
Fix: distinguish active publication from abandoned publication with an atomic
ownership/state protocol; prevent recovery from deleting an active publisher.
Keep uniqueness and wait-for-winner handling. Partial-write and rename-failure
cleanup independently passed in this review; they do not cover this race.

### High: Operand association is broader than a cell or unambiguous sentence

Location: services/answer_service.py:133–243.
Reproduction: `Q2 efficiency 72%, Q4 efficiency 84%` accepts Q2=84.
`Metric | Revenue USD | Efficiency %\nQ2 | 100 | 72` accepts efficiency=100%.
Missing association fields pass; an operand named `revenue efficiency` passes
against efficiency alone; page context containing both Q2 and Q4 can supply
either period. Five independent rejection tests fail with DID NOT RAISE.
Fix: bind table values to their column headers/cells, require sufficient metric,
unit and period provenance, and reject unresolved competing associations.
The existing semicolon-separated regression passes through a narrower branch
and therefore does not disprove the comma/same-sentence defect.

### Medium: Valid number formats and adjacent periods are rejected

Locations: services/answer_service.py:102,108,203.
Reproduction: +100 with an explicit positive sign and 1,000 for value=1000 are
rejected as absent. `Q2 72%; Q4 84%` rejects the correct Q4=84 because the period
regex consumes the following value as a year suffix.
Fix: normalize allowed signed/grouped numeric formats and distinguish quarter
tokens/year suffixes from adjacent measurement values. Keep negative-sign
mismatch rejection.

### Medium: CropBox highlight mapping uses MediaBox dimensions

Location: services/document_service.py:247; services/highlight_service.py.
Reproduction: D_layout physical page 3 expects image bbox
`[41.7,79.5,284.9,111.0]`, but the API returns
`[27.3,50.2,186.4,70.1]` at 150 DPI. Text coordinates are crop-relative while
the stored dimensions describe MediaBox.
Fix: store/map the actual unrotated rendered CropBox coordinate space with
rotation applied consistently; test nonzero crop origins as well as rotations.

### Medium: Simple vector charts miss fallback

Location: services/document_service.py:236.
Reproduction: simple_vector_native.pdf has enough native prose, two drawn bars,
no scan flag, no table and no raster image. Fallback returns []. The builder
regression uses eight paths and does not independently exercise a simple chart.
Fix: make simple meaningful drawings discoverable without requiring >5 paths,
retaining the existing bounded visual-analysis limit.

### Medium: Smoke checks falsely certify wrong answers

Location: scripts/smoke_test_model.py:84–93.
Reproduction: both `Q2: 84% and Q4: 72%. Q2 is higher than Q4.` and
`Q2 has 72 units. Q4 has 84 units. Q4 is not higher.` pass all three checks.
Fix: parse explicit quarter/value/unit associations and an affirmative
comparison; reject negation and cross-clause label borrowing. Predicate tests
must include these adversarial outputs before treating smoke success as proof.

### Medium: Rapid notebook responses can overwrite the active notebook's sources

Location: static/js/app.js:79–83; similar unguarded history path at 233.
Reproduction: start old notebook's loadDocuments, switch to new notebook, finish
new response, then finish old response. state.notebookId is new while
state.documents contains old-doc. The JS simulation fails `'old-doc' ==
'new-doc'`. This is a deterministic source-list race; the history race is a
related static risk, not separately exercised.
Fix: snapshot notebook ID/request generation and discard responses whose
notebook no longer matches before assigning state or rendering.

### Medium: Worker-test cleanup can discard live ownership

Location: tests/test_worker_integration.py:19–29.
The fixture unconditionally clears worker and handle globals after stop_worker,
even though production stop_worker intentionally retains them for a live job.
Fix: assert successful termination before teardown/restoration, or place those
tests in explicitly bounded owned subprocesses. pytest.ini also defines only
a marker: plain pytest is not automatically worker-free.

### Low: Different database suffixes collide on lock identity

Location: services/jobs.py:54.
Reproduction: canonical a.db and a.sqlite both map to a.worker.lock.
Fix: append a lock suffix to the full canonical filename, or hash the entire
canonical identity. The current path resolution correctly handles spelling
aliases of the same database, but suffix replacement is not one-to-one.

### High requirement gap: Unsupported claims remain visible

Location: services/answer_service.py:470–488; static/js/app.js:284.
An injected response containing a valid evidence ID, fabricated quote and
"Efficiency was 999%" remains a factual claim, marked quote_not_found rather
than rejected. A matched quote "Production efficiency 72%" can also accompany
an unrelated robot-caused claim labelled quote_verified.
Fix: reject or withhold claims with invalid required support; keep quote
existence distinct from semantic support. Do not label a quote check as factual
verification. Add the smallest grounded safeguards for unsupported numeric,
causal and ranking claims, with explicit uncertainty.

Conditional/model-dependent behavior: injected unsupported causation, ranking
and instruction-following responses were published, and a model omission of
conflict disclosure was not independently corrected. These four tests show the
absence of independent guarantees; **they do not prove Qwen hallucinated or
obeyed prompt injection**. System prompts do explicitly treat evidence
instructions as untrusted. Actual Qwen behavior and semantic claim accuracy
cannot be assessed until inference prerequisites exist.

## D. Artifacts

- REPORT.md: this report.
- execution.json and command-1.txt through command-8.txt: exact final commands,
  exit codes, captured reviewer outputs and diagnostic traces.
- functional-results.xml: 36 independent cases; migration-results.xml:
  three additional legacy cases.
- fixtures/: Q2/Q4, entirely vector chart, raster chart, multi-column/table/
  footnote/label/rotation/CropBox document, distractor/conflict, blurred and
  unreadable scans, injection, encrypted/corrupt/empty and independent simple
  vector/prose fixtures. truth.json was used only as an evaluation manifest,
  never provided to a model or synthesis prompt.
- fixtures/chart.png: rendered synthetic chart, visually inspected; it is
  **not a browser screenshot**.
- functional_tmp/: isolated per-test databases/uploads/rendered page images.
  migration_tmp/: isolated legacy-test storage. These are review artifacts,
  not production data.
- run_suite.py, test_functional.py, test_migrations.py, runtime_checks.py,
  frontend_checks.cjs: reproducible guards and checks.

## E. Smallest prioritized next actions

1. Fix active-publication recovery and source-list stale responses; repair
   worker-suite cleanup. Re-run their independent reproductions.
2. Fix operand cell/period/number handling, unsupported-claim publication,
   CropBox mapping, simple-vector detection and smoke predicates. Re-run the
   existing and independent tests without changing their ground truth.
3. Establish the intended environment and matching projector explicitly, then
   run bounded real Qwen image/application tests and actual browser checks.
   Measure offload, VRAM and latency rather than inferring them from model size.
