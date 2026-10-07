"""Application configuration loaded from environment / .env file.

All model, retrieval, and ingestion limits are configurable here so the
prototype can be tuned for the 8 GB VRAM target without code changes.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # --- Paths -------------------------------------------------------------
    DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", DATA_DIR / "uploads"))
    PAGE_IMAGE_DIR = Path(os.environ.get("PAGE_IMAGE_DIR", DATA_DIR / "pages"))
    DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "app.db"))

    # The workspace already ships the model under Model\ (capital M).
    MODEL_PATH = os.environ.get(
        "MODEL_PATH", str(BASE_DIR / "Model" / "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf")
    )
    MMPROJ_PATH = os.environ.get(
        "MMPROJ_PATH", str(BASE_DIR / "Model" / "mmproj-F16.gguf")
    )

    # --- Model runtime (conservative for 8 GB VRAM) -------------------------
    N_GPU_LAYERS = _int("N_GPU_LAYERS", 20)      # partial offload; raise after testing
    N_CTX = _int("N_CTX", 8192)
    N_BATCH = _int("N_BATCH", 256)
    MAX_IMAGE_DIM = _int("MAX_IMAGE_DIM", 1024)  # page images resized to fit
    MAX_TOKENS = _int("MAX_TOKENS", 1024)
    MODEL_IDLE_UNLOAD = os.environ.get("MODEL_IDLE_UNLOAD", "0") == "1"

    # --- Retrieval -----------------------------------------------------------
    RETRIEVAL_TOP_K = _int("RETRIEVAL_TOP_K", 8)
    MAX_EVIDENCE_PAGES = _int("MAX_EVIDENCE_PAGES", 6)  # images analyzed per question
    ADJACENT_PAGE_WINDOW = _int("ADJACENT_PAGE_WINDOW", 1)

    # --- Ingestion -----------------------------------------------------------
    MAX_FILE_MB = _int("MAX_FILE_MB", 100)
    MAX_FILES_PER_UPLOAD = _int("MAX_FILES_PER_UPLOAD", 10)
    MAX_PAGES = _int("MAX_PAGES", 500)
    PAGE_RENDER_DPI = _int("PAGE_RENDER_DPI", 150)
    # Pages with fewer native chars than this are treated as scanned.
    SCANNED_CHAR_THRESHOLD = _int("SCANNED_CHAR_THRESHOLD", 30)
    # Transcribe scanned pages with the vision model during indexing (slow).
    VISUAL_TRANSCRIPTION_ENABLED = os.environ.get("VISUAL_TRANSCRIPTION_ENABLED", "0") == "1"

    # --- Server ---------------------------------------------------------------
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = _int("PORT", 5000)
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")

    @classmethod
    def ensure_dirs(cls) -> None:
        for d in (cls.DATA_DIR, cls.UPLOAD_DIR, cls.PAGE_IMAGE_DIR):
            Path(d).mkdir(parents=True, exist_ok=True)
