# Deadline result: real inference not completed

Started 2026-10-07 08:45:26 UTC (14:15:26 IST).
Hard deadline 09:05:26 UTC (14:35:26 IST).
Last state check 09:04:33 UTC.

## Actual result

No real Qwen image inference occurred. No model was loaded. No GPU layer
offload was observed. Chart accuracy is untested. This setup is not working yet.

Projector download is incomplete: Model/mmproj-F16.gguf.part was 297,795,584
bytes. Official CUDA wheel download is incomplete:
runtime_setup/llama_cpp_python-0.3.36-py3-none-win_amd64.whl.part was 20,971,520
bytes. Neither partial file was published or considered usable; SHA256 checks
for completed downloads have not run. No .venv interpreter was present at the
last check. Do not rename .part files to their final names.

This session's external command network was blocked (WinError 10013); the
PowerShell request also failed. An explicit elevated download request was
rejected by the platform because sandbox_approval is disabled. Partial files
appeared after the bootstrap was offered for a separate network-enabled
terminal; completion could not be established before the deadline.

## Verified existing environment

Python: C:/Users/sanja/AppData/Local/Programs/Python/Python312/python.exe,
3.12.10. Existing llama-cpp-python 0.3.36; Qwen25VLChatHandler imports, but
llama_supports_gpu_offload() reports false.

NVIDIA query: RTX 5060 Laptop GPU, compute capability 12.0, driver 617.42,
8151 MiB VRAM. Initially 1492 MiB whole-device memory was in use; no Python or
llama GPU inference process was listed. Hardware capability is not observed
model offload.

Existing model SHA256 matched the repository:
d16776dcd9a28d42758c2958ed3a752aabf20a305252cd64ff2be72b4a78c503.
Matching projector SHA256 from repository metadata:
987dd0733033fb5dd9b124d1ca926ae865572e432384eee7618b2eec3e735e17.
Pinned repository revision: 68bb8bc4b7df5289c143aaec0ab477a7d4051aab.

Official Windows CUDA 13.0 wheel identified:
https://github.com/abetlen/llama-cpp-python/releases/tag/v0.3.36-cu130
Wheel SHA256: 2db1d93d452244cbb400d32c64c2467f2f2d373f8014a60b778b5b67973ed9db.
Official documentation supports Python 3.12, CUDA 13 wheels and the Qwen2.5-VL
handler. Actual compatibility on this machine remains unverified until install
and inference succeed. No source compilation or incompatible-package loop was
attempted.

## Changes and tests

- Recorded global packages in packages-before.txt and bootstrap snapshot in
  bootstrap-packages-before.txt. No global package change was performed.
- bootstrap.py: pinned concurrent downloads, SHA256 validation, local .venv
  install without source builds. The CUDA wheel must be installed even when
  the inherited CPU package has the same version.
- services/model_service.py: fail explicitly when GPU layers are requested
  from a runtime reporting no GPU support; optional verbose backend logs and
  load/inference latency fields; existing serialized singleton retained.
- scripts/smoke_test_model.py: strict JSON associations for Q2=72%, Q4=84%,
  Q4 higher; pinned file hashes; one owned inference child, bounded timeout,
  GPU telemetry and layer-log capture. Uses runtime_setup/smoke, not live data.
- runtime_setup/test_runtime.py: 10 regression tests passed, exit 0. These
  are mocked/parser checks, not real Qwen inference.
- `python -B scripts/smoke_test_model.py --timeout 30`: inference child exit 1,
  preflight error "Required file missing: .../Model/mmproj-F16.gguf", elapsed
  4.24 seconds. No inference attempted. Whole-device memory peak during this
  preflight was 1429 MiB; it must not be reported as model VRAM usage.

No shared config/README edits were applied; no application data was changed.
Original owned files are backed up under runtime_setup/. Kimi's files were not
edited. Proposed shared settings are in HANDOFF.md.

## Commands to continue (not yet a verified working startup)

In a network-enabled PowerShell terminal, ensure no earlier bootstrap is still
running before restarting it. Current bootstrap restarts incomplete downloads.

```powershell
cd C:\Users\sanja\Sonion
python -B runtime_setup/bootstrap.py
.\.venv\Scripts\python.exe -B scripts/smoke_test_model.py --timeout 180
```

Only after the smoke completes successfully and its child has exited, start
one Flask instance:

```powershell
$env:N_CTX='2048'
$env:N_BATCH='128'
$env:N_GPU_LAYERS='20'
$env:MAX_IMAGE_DIM='512'
$env:MAX_TOKENS='512'
$env:MAX_EVIDENCE_PAGES='2'
$env:VISUAL_TRANSCRIPTION_ENABLED='0'
$env:MODEL_VERBOSE='1'
.\.venv\Scripts\python.exe -B main.py
```

The smoke default bound is 180 seconds. Its image values appear only in the
image and evaluator, never in the question prompt. Successful recognition,
CUDA capability, layer-log offload and observed device memory must be reported
separately. These conservative settings are not a promise of VRAM fit.

Remaining blocker: finish and verify the projector and CUDA wheel downloads,
install the local runtime, then perform the actual image test.
