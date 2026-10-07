"""Entry point. Run:  python main.py

The app and its background worker are created HERE, explicitly - never at
import time (importing app.py must be side-effect free so tests and tools can
import it against temporary storage).

use_reloader=False is deliberate: Flask's debug reloader spawns a second
process, which would load the 7B model twice and exhaust VRAM.
"""
from app import create_app
from config import Config
from services import model_service

if __name__ == "__main__":
    print("Loading Qwen2.5-VL model...", flush=True)
    model_service.load_model()
    print("Model loaded. Starting application...", flush=True)
    app = create_app()  # initializes storage and starts the job worker
    app.run(host=Config.HOST, port=Config.PORT, debug=False, use_reloader=False,
            threaded=True)
