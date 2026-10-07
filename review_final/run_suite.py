"""Run existing worker-free tests with isolated storage and runtime guards."""
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['VISUAL_TRANSCRIPTION_ENABLED'] = '0'

with tempfile.TemporaryDirectory(prefix='sonion-final-suite-') as tmp:
    for key, tail in [('DATA_DIR','data'), ('DB_PATH','data/app.db'),
                      ('UPLOAD_DIR','data/uploads'), ('PAGE_IMAGE_DIR','data/pages')]:
        os.environ[key] = str(Path(tmp)/tail)
    import storage
    from services import jobs, model_service
    # Collection must not initialize/recover data, start a worker, or load weights.
    def forbidden(*args, **kwargs):
        raise RuntimeError('Forbidden collection side effect')
    saved = [(storage, 'init_db', storage.init_db),
             (storage, 'recover_abandoned_jobs', storage.recover_abandoned_jobs),
             (jobs, 'start_worker', jobs.start_worker)]
    for module, name, fn in saved:
        setattr(module, name, forbidden)
    model_service._load = forbidden
    original_start = threading.Thread.start
    def guarded_start(thread):
        if thread.name.startswith('job-worker-'):
            raise RuntimeError('Worker forbidden in worker-free suite')
        return original_start(thread)
    threading.Thread.start = guarded_start
    import pytest
    args = [str(ROOT/'tests'), '-q', '-m', 'not worker', '-p', 'no:cacheprovider', '--tb=short']
    if '--collect-only' in sys.argv:
        args.append('--collect-only')
    else:
        for module, name, fn in saved:
            setattr(module, name, fn)
    args += ['--basetemp', str(Path(tmp)/'pytest')]
    result = pytest.main(args)
raise SystemExit(result)
