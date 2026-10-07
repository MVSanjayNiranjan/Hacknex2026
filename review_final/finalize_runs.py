import sys,os,subprocess,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=Path(__file__).parent
env=os.environ.copy();env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1',VISUAL_TRANSCRIPTION_ENABLED='0')
results=json.loads((OUT/'execution.json').read_text())
commands=[(4,[sys.executable,'-B','-m','pytest','review_final/test_functional.py','-q','-p','no:cacheprovider','--tb=short','--basetemp','review_final/functional_tmp','--junitxml','review_final/functional-results.xml'],None),
          (6,[sys.executable,'-B','-m','pytest','review_final/test_migrations.py','-q','-p','no:cacheprovider','--tb=short','--basetemp','review_final/migration_tmp','--junitxml','review_final/migration-results.xml'],None),
          (7,['node','-'],(OUT/'frontend_checks.cjs').read_text())]
for index,cmd,payload in commands:
    result=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,input=payload,timeout=30)
    item=dict(command=cmd,stdin_script='review_final/frontend_checks.cjs' if payload else None,exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
    (OUT/f'command-{index+1}.txt').write_text(json.dumps(item,indent=2),encoding='utf-8')
    if index<len(results):results[index]=item
    else:results.append(item)
    print('COMMAND',index+1,'EXIT',result.returncode);print(result.stdout[-1400:])
(OUT/'execution.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
