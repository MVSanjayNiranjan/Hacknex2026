# Runtime result — 7 October 2026

Flask is running at http://127.0.0.1:5057 using real Qwen2.5-VL inference on CPU. This is a limited smoke-tested fallback, not completion of the entire project or GPU verification.

## Startup

From C:\Users\sanja\Sonion:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\runtime_setup\start_cpu_demo.ps1
```

The script runs the documented main.py entry point with the global Python 3.12 interpreter, no reloader, serialized inference, N_GPU_LAYERS=0, N_CTX=4096, N_BATCH=128, MAX_IMAGE_DIM=384, and MAX_EVIDENCE_PAGES=2. It sets the actual complete model/projector paths. Storage is isolated under runtime_setup/demo_data; existing application data was not opened or changed. The currently running instance should be used, rather than launching a second instance.

## Actual commands and outcomes

- `.\.venv\Scripts\python.exe -m pip install --ignore-installed --no-deps --no-index .\runtime_setup\llama_cpp_python-0.3.36-py3-none-win_amd64.whl`: exit 0. Local CUDA 13 wheel installed once. Existing package versions were recorded previously in packages-before.txt and bootstrap-packages-before.txt; global package versions were unchanged.
- CUDA interpreter import: exit 1. llama.dll exists, but its dependency chain cannot load. ggml-cuda.dll imports cublas64_13.dll and nvcudart_hybrid64.dll; neither was found in the checked user, Program Files, and System32 locations. PyTorch contains CUDA 12 libraries, which are not substituted for CUDA 13 DLLs. Thus llama_supports_gpu_offload() could not be evaluated in the newly installed wheel. The functioning global runtime reports False.
- `python -B scripts/smoke_test_model.py --cpu --timeout 100`: final run exit 0, all 3 recognition checks passed (Q2=72%, Q4=84%, Q4 higher). Real image data was supplied to the Qwen handler; no mock. Load 11.062 seconds, inference 21.672 seconds, total 32.750 seconds. Earlier CPU attempt rejected an unsupported use_gpu handler argument; that temporary integration change was removed before the passing run.
- `python -B runtime_setup/app_smoke.py`: exit 0. One multipart request uploaded both synthetic PDFs; both indexing jobs completed. Real answer returned Q2 72% and Q4 84%, each with the correct document ID and physical page 1 citation. Two page-detail, image, and PDF endpoint sets all returned HTTP 200. Actual page images were analyzed and synthesis was real model inference.
- `python -B runtime_setup/chart_app_smoke.py`: exit 0. Its PDF has no native text layer. The actual application produced all 3 correct chart claims and cited the image-only document at page 1 through qwen_visual evidence.
- Flask `/`: HTTP 200. Browser interaction was not tested in this deadline run.

The model and projector SHA256 checks passed inside the real smoke child against pinned repository metadata. The local wheel hash also matches download-manifest.json.

## GPU evidence and blocker

CPU smoke logs contain no layer-offload matches. Whole-device memory peaked at 1464 MiB during that smoke; this is background/total device usage, not model allocation or evidence of GPU inference. A later query showed 1367 MiB and 1% utilization. No GPU success is claimed.

Shortest next action: supply the missing matching CUDA 13 runtime libraries, then re-run `.\.venv\Scripts\python.exe -B scripts/smoke_test_model.py --timeout 180`. Alternatively obtain a compatible CUDA 12 prebuilt runtime matching the existing libraries. Neither download nor installation beyond the already downloaded wheel was attempted in this deadline run. Stop the CPU app before any new inference smoke so only one model instance runs.

## Evidence

- smoke/result.json, smoke/gpu-evidence.json, smoke/stdout.txt, smoke/stderr.txt
- app-smoke-result.json, citation-endpoint-results.json
- chart-app-result.json
- Q2-runtime.pdf, Q4-runtime.pdf, image-only-chart.pdf, smoke/known-chart.png
- start_cpu_demo.ps1, app-stdout.txt, app-stderr.txt, app-launch-pid.txt

No new downloads, source compilation, broad application refactoring, or full regression suite were performed. The full challenge requirements remain outside this smoke test.
