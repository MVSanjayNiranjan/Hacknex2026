"""Qwen2.5-VL inference via the official llama-cpp-python API.

Design rules implemented here:
- One lazily-created model instance, reused for the process lifetime.
- A single lock serializes all generation (no concurrent GPU work).
- Images are resized and processed one page at a time.
- The model is reported "ready" ONLY after a real load attempt succeeded —
  never merely because the GGUF files exist on disk.
- Failures (missing files, missing/incompatible Qwen25VLChatHandler,
  CPU-only build with GPU layers requested, context overflow) surface as
  ModelUnavailableError with an actionable message; callers must not fall
  back to fabricated answers.

llama_cpp is imported lazily inside functions so the web app, uploads and the
test-suite run on machines without the package installed.
"""
import base64
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from config import Config

log = logging.getLogger(__name__)


class ModelUnavailableError(Exception):
    """The model cannot serve this request; message is user-actionable."""


_lock = threading.Lock()
_llm = None
_load_error: Optional[str] = None
_gpu_offload_supported_build: Optional[bool] = None  # build capability only
_visual_inference_tested = False
_load_seconds: Optional[float] = None
_last_inference_seconds: Optional[float] = None


def _check_files() -> None:
    if not Path(Config.MODEL_PATH).is_file():
        raise ModelUnavailableError(
            f"Model file not found at {Config.MODEL_PATH}. Download "
            "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf from "
            "unsloth/Qwen2.5-VL-7B-Instruct-GGUF and set MODEL_PATH in .env.")
    if not Path(Config.MMPROJ_PATH).is_file():
        raise ModelUnavailableError(
            f"Vision projector not found at {Config.MMPROJ_PATH}. Download "
            "mmproj-F16.gguf from the SAME unsloth/Qwen2.5-VL-7B-Instruct-GGUF "
            "revision as the model (a mismatched projector produces garbage "
            "or crashes) and set MMPROJ_PATH in .env.")


def _load():
    """Load the model once. Raises ModelUnavailableError on any failure."""
    global _llm, _load_error, _gpu_offload_supported_build, _load_seconds
    if _llm is not None:
        return _llm
    if _load_error is not None:
        raise ModelUnavailableError(_load_error)
    try:
        started = time.monotonic()
        _check_files()
        try:
            from llama_cpp import Llama
        except ImportError:
            raise ModelUnavailableError(
                "llama-cpp-python is not installed. See README section 3 for the "
                "CUDA-enabled install command.")
        try:
            from llama_cpp.llama_chat_format import Qwen25VLChatHandler
        except ImportError:
            raise ModelUnavailableError(
                "The installed llama-cpp-python does not provide "
                "Qwen25VLChatHandler. Upgrade with the CUDA build command in the "
                "README (pip install -U llama-cpp-python ...). Do NOT substitute "
                "a text-only chat format — vision questions would silently lose "
                "the image.")

        import llama_cpp
        _gpu_offload_supported_build = bool(
            getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)())
        if Config.N_GPU_LAYERS > 0 and not _gpu_offload_supported_build:
            raise ModelUnavailableError(
                "GPU layers were requested, but this llama-cpp-python runtime "
                "reports no GPU offload support. Use the verified CUDA runtime "
                "interpreter, or explicitly set N_GPU_LAYERS=0 for CPU inference.")
        verbose = os.environ.get("MODEL_VERBOSE", "0") == "1"
        handler = Qwen25VLChatHandler(
            clip_model_path=Config.MMPROJ_PATH, verbose=verbose)
        try:
            llm = Llama(
                model_path=Config.MODEL_PATH,
                chat_handler=handler,
                n_ctx=Config.N_CTX,
                n_batch=Config.N_BATCH,
                n_gpu_layers=Config.N_GPU_LAYERS,
                verbose=verbose,
            )
        except Exception as exc:
            msg = str(exc)
            if "context" in msg.lower() and "size" in msg.lower():
                raise ModelUnavailableError(
                    f"Context/batch configuration rejected: {msg}. Lower N_CTX or "
                    "N_BATCH in .env.")
            raise ModelUnavailableError(
                f"Model load failed: {msg}. If this mentions CUDA/cublas DLLs, your "
                "llama-cpp-python build is CPU-only or mismatched — reinstall per "
                "README section 3. If it mentions memory, lower N_GPU_LAYERS.")

        # Record build-level GPU capability. Note: llama.cpp does not expose
        # per-layer placement, so actual GPU placement of this instance is
        # reported as unverifiable rather than assumed.
        try:
            import llama_cpp
            _gpu_offload_supported_build = bool(
                getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)())
        except Exception:
            _gpu_offload_supported_build = None
        _llm = llm
        _load_seconds = round(time.monotonic() - started, 3)
        return _llm
    except ModelUnavailableError as exc:
        _load_error = str(exc)
        raise
    except Exception as exc:  # defensive: never cache a cryptic failure
        _load_error = f"Unexpected model load failure: {exc}"
        raise ModelUnavailableError(_load_error)


def load_model() -> None:
    """Warm up the shared model before serving requests."""
    with _lock:
        _load()


def _handler_available() -> bool:
    try:
        from llama_cpp.llama_chat_format import Qwen25VLChatHandler  # noqa: F401
        return True
    except Exception:
        return False


def _gpu_offload_supported() -> Optional[bool]:
    """Build-level capability check; does NOT prove this model runs on GPU."""
    global _gpu_offload_supported_build
    if _gpu_offload_supported_build is not None:
        return _gpu_offload_supported_build
    try:
        import llama_cpp
        return bool(getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)())
    except Exception:
        return None


def model_status() -> dict:
    """Granular, honest status. Each capability is reported separately:
    files present, handler available, build supports GPU offload, model
    loaded, GPU placement observed (unverifiable via llama.cpp -> null),
    and whether a real visual inference has succeeded in this process."""
    model_file = Path(Config.MODEL_PATH).is_file()
    projector_file = Path(Config.MMPROJ_PATH).is_file()
    if _llm is not None:
        state = "loaded"
    elif _load_error:
        state = "error"
    elif model_file and projector_file:
        state = "not_loaded"      # files exist; inference unverified
    else:
        state = "missing_files"
    return {
        "state": state,
        "model_file_present": model_file,
        "projector_file_present": projector_file,
        "handler_available": _handler_available(),
        "gpu_offload_supported": _gpu_offload_supported(),
        "loaded": _llm is not None,
        "gpu_placement_observed": None,  # not verifiable through llama-cpp API
        "visual_inference_tested": _visual_inference_tested,
        "load_seconds": _load_seconds,
        "last_inference_seconds": _last_inference_seconds,
        "error": _load_error,
        "model_path": Config.MODEL_PATH,
        "mmproj_path": Config.MMPROJ_PATH,
    }


def reset_for_tests() -> None:
    global _llm, _load_error, _gpu_offload_supported_build, _visual_inference_tested
    global _load_seconds, _last_inference_seconds
    _llm, _load_error = None, None
    _gpu_offload_supported_build, _visual_inference_tested = None, False
    _load_seconds, _last_inference_seconds = None, None


def _image_data_uri(image_path: str) -> str:
    """Resize (if needed) and base64-encode a page image for the chat handler."""
    from PIL import Image
    img = Image.open(image_path).convert("RGB")
    m = Config.MAX_IMAGE_DIM
    if max(img.size) > m:
        img.thumbnail((m, m))
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _unload_locked() -> None:
    """Drop the model reference so its memory can be reclaimed.

    Called only while holding _lock, so no in-flight generation can race the
    unload. Python frees the weights once llama_cpp destroys the object.
    """
    global _llm, _load_error
    if _llm is not None:
        llm, _llm = _llm, None
        _load_error = None  # allow a fresh load attempt next time
        try:
            del llm
        except Exception:  # pragma: no cover - defensive
            pass


def _chat(messages, max_tokens: Optional[int] = None, temperature: float = 0.1) -> str:
    global _last_inference_seconds
    with _lock:  # one generation at a time — shared 8 GB GPU
        llm = _load()
        started = time.monotonic()
        try:
            resp = llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens or Config.MAX_TOKENS,
                temperature=temperature,
            )
            _last_inference_seconds = round(time.monotonic() - started, 3)
        except Exception as exc:
            msg = str(exc).lower()
            if "context" in msg and ("exceed" in msg or "size" in msg or "full" in msg):
                raise ModelUnavailableError(
                    "The prompt exceeded the model context window. Reduce the "
                    "number of evidence pages or raise N_CTX in .env. Evidence was "
                    "NOT silently dropped to fit.")
            raise ModelUnavailableError(f"Inference failed: {exc}")
        finally:
            if Config.MODEL_IDLE_UNLOAD:
                _unload_locked()
    return resp["choices"][0]["message"]["content"]


def analyze_page_image(image_path: str, instruction: str) -> str:
    """Run one vision question against ONE page image (serial by design)."""
    global _visual_inference_tested
    if not Path(image_path).is_file():
        raise ModelUnavailableError(f"Page image missing: {image_path}")
    result = _chat([{
        "role": "user",
        "content": [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": _image_data_uri(image_path)}},
        ],
    }])
    _visual_inference_tested = True  # a real image round-trip succeeded
    return result


def text_completion(system: str, prompt: str, max_tokens: Optional[int] = None) -> str:
    return _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": prompt}],
        max_tokens=max_tokens)


def transcribe_page(image_path: str) -> str:
    """Visual transcription of a scanned page (indexing-time OCR substitute)."""
    return analyze_page_image(
        image_path,
        "Transcribe ALL legible text on this page verbatim, preserving reading "
        "order and table rows. If a region is unreadable, write [unreadable]. "
        "Do not invent or complete missing text.",
    )
