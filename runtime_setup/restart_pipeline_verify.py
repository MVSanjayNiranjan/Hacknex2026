import os,sys,time,json,subprocess,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:5057'
def get(path):
    with urllib.request.urlopen(BASE+path,timeout=10) as r:return json.load(r)
def wait(j,limit):
    end=time.monotonic()+limit
    while time.monotonic()<end:
        x=get('/api/jobs/'+j)
        if x['status'] in ('done','error'):return x
        time.sleep(1)
    return x
# Finish the current user answer before restarting only our owned Flask PID.
active=[x for x in get('/api/notebooks/nb_f1595f83d58047f5/jobs') if x['status']=='running']
for x in active:
    result=wait(x['id'],100)
    if result['status']=='running':raise SystemExit('Deferred restart: active answer has not completed')
owned=23536
import psutil
p=psutil.Process(owned)
assert p.name().lower()=='python.exe' and 'main.py' in p.cmdline()
p.terminate();p.wait(timeout=10)
env={};seen=set()
for k,v in os.environ.items():
    if k.upper() not in seen:env[k]=v;seen.add(k.upper())
launcher=subprocess.Popen(['C:/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'runtime_setup/start_cpu_demo.ps1')],cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW,stdout=open(ROOT/'runtime_setup/pipeline-app-stdout.txt','w'),stderr=open(ROOT/'runtime_setup/pipeline-app-stderr.txt','w'))
(ROOT/'runtime_setup/pipeline-launch-pid.txt').write_text(str(launcher.pid))
for i in range(30):
    try:get('/api/model/status');break
    except Exception:time.sleep(.3)
for name,question in [('efficiency','Compare production efficiency between Q2 and Q4 and cite your sources.'),('yield','Compare first-pass yield between Q2 and Q4 in Figure 1 and cite your sources.')]:
    start=time.monotonic()
    req=urllib.request.Request(BASE+'/api/notebooks/nb_f1595f83d58047f5/chat',json.dumps({'question':question}).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as r:job=json.load(r)['job_id']
    result=wait(job,150)
    out={'latency_seconds':round(time.monotonic()-start,3),'question':question,'job':result}
    (ROOT/f'runtime_setup/meridian-{name}-actual.json').write_text(json.dumps(out,indent=2))
    print(name,json.dumps(out),flush=True)
    if result['status']!='done':break
