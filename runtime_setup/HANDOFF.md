# Runtime handoff to Kimi

Ownership: runtime_setup/, services/model_service.py and scripts/smoke_test_model.py
were changed by the runtime agent. Do not edit these concurrently. Config.py,
README.md, application code and user data were not changed by this agent.

## Prerequisites and environment

The existing global Python 3.12.10 has llama-cpp-python 0.3.36 with a Qwen handler,
but its GPU-offload capability is false. Existing packages are recorded in
packages-before.txt. NVIDIA reports RTX 5060 Laptop, compute capability 12.0,
driver 617.42, 8151 MiB total VRAM, initially 1492 MiB used.

The pinned model SHA256 matches the repository. The projector is missing.
bootstrap.py downloads the matching projector and official v0.3.36-cu130 Windows
wheel in parallel, verifies SHA256, creates .venv with existing ordinary
dependencies inherited, and installs only the CUDA wheel locally. Global
packages are preserved. No compilation or third-party wheel is used.

This agent's command network is restricted and escalation was rejected.
Bootstrap must run in a network-enabled terminal unless session access changes:

```powershell
cd C:\Users\sanja\Sonion
python -B runtime_setup/bootstrap.py
```

## Smoke and startup commands (conditional until setup is completed)

First confirm no other project model process is running. The smoke command
owns one inference child and enforces a 180-second timeout. It uses context
2048, batch 128, 20 GPU layers, max image dimension 512 and 160 output tokens.
It checks exact JSON quarter/value/percent associations and Q4 higher, and
records verbose layer logs and whole-device NVIDIA memory/activity. Whole-device
memory is not per-model allocation. CPU-only execution cannot pass this smoke.

```powershell
.\.venv\Scripts\python.exe -B scripts/smoke_test_model.py --timeout 180
```

After the smoke child has exited, launch ONE Flask process:

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

These are proposed conservative runtime settings, not proven VRAM-fit settings.
No config/README change is applied. Coordinate before incorporating these
commands into shared README or changing defaults. Preserve existing .env/data.

## Owned service changes

- Reject requested GPU offload when the runtime capability API is false,
  before creating a model instance.
- Optional MODEL_VERBOSE=1 records backend/layer logs.
- Model status includes load_seconds and last_inference_seconds while retaining
  gpu_placement_observed=null (actual evidence belongs in smoke GPU records).
- Existing serialized singleton and lazy loading remain.
- Smoke results live under runtime_setup/smoke; no application data is opened.

Runtime code guards/tests are mocked regression checks, not proof of inference.
The setup and chart/GPU results must be reported separately once real runs occur.

Sources: https://github.com/abetlen/llama-cpp-python/releases/tag/v0.3.36-cu130
and https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/tree/68bb8bc4b7df5289c143aaec0ab477a7d4051aab
