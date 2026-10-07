"""Print only approved configuration fields; never read/print secrets."""
import sys, importlib.metadata as meta, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from config import Config
from services import model_service
packages={}
for name in ['Flask','PyMuPDF','Pillow','python-dotenv','pytest','llama-cpp-python']:
    try: packages[name]=meta.version(name)
    except meta.PackageNotFoundError: packages[name]='MISSING'
result=dict(interpreter=sys.executable,python=sys.version,venv=sys.prefix!=sys.base_prefix,
            local_venv=(ROOT/'.venv/Scripts/python.exe').is_file(),env_exists=(ROOT/'.env').is_file(),packages=packages,
            settings={k:getattr(Config,k) for k in ['N_GPU_LAYERS','N_CTX','N_BATCH','MAX_IMAGE_DIM','MAX_TOKENS','MAX_EVIDENCE_PAGES','VISUAL_TRANSCRIPTION_ENABLED']},
            model_status=model_service.model_status())
result['model_bytes']=Path(Config.MODEL_PATH).stat().st_size if Path(Config.MODEL_PATH).is_file() else None
print(json.dumps(result,indent=2))
