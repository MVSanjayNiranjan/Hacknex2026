"""Record safe commands, outputs and return codes; no production logs are read."""
import os,sys,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=Path(__file__).parent
env=os.environ.copy(); env.update(PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',VISUAL_TRANSCRIPTION_ENABLED='0')
commands=[
    [sys.executable,'-B','review_final/environment.py'],
    [sys.executable,'-B','review_final/run_suite.py','--collect-only'],
    [sys.executable,'-B','review_final/run_suite.py'],
    [sys.executable,'-B','review_final/fixtures.py'],
    [sys.executable,'-B','-m','pytest','review_final/test_functional.py','-q','-p','no:cacheprovider','--tb=short','--basetemp','review_final/functional_tmp','--junitxml','review_final/functional-results.xml'],
    [sys.executable,'-B','review_final/runtime_checks.py'],
]
results=[]
for i,cmd in enumerate(commands):
    try:
        result=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
        item=dict(command=cmd,exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
    except subprocess.TimeoutExpired as exc:
        item=dict(command=cmd,exit_code='TIMEOUT',stdout=str(exc.stdout),stderr=str(exc.stderr))
    (OUT/f'command-{i+1}.txt').write_text(json.dumps(item,indent=2),encoding='utf-8'); results.append(item)
    print('COMMAND',i+1,'EXIT',item['exit_code']); print(item['stdout'][-650:])
(OUT/'execution.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
raise SystemExit(1 if any(r['exit_code']!=0 for r in results) else 0)
