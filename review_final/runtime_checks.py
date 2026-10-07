"""Owned-process worker and HTTP checks. Parent imposes 45-second execution bound."""
import os,sys,tempfile,json,time,threading,subprocess,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))

if '--contender' in sys.argv:
    from services import jobs
    assert jobs.start_worker() is False, 'contender acquired active lock'
    print('CONTENDER_DENIED')
    raise SystemExit(0)

if '--child' not in sys.argv:
    result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,capture_output=True,text=True,timeout=45)
    print(result.stdout); print(result.stderr); raise SystemExit(result.returncode)

with tempfile.TemporaryDirectory(prefix='sonion-final-runtime-') as tmp:
    for key,tail in [('DATA_DIR','data'),('DB_PATH','data/app.db'),('UPLOAD_DIR','data/uploads'),('PAGE_IMAGE_DIR','data/pages')]: os.environ[key]=str(Path(tmp)/tail)
    os.environ['VISUAL_TRANSCRIPTION_ENABLED']='0'
    from config import Config
    import storage
    from services import jobs,model_service as ms,document_service as ds
    from app import create_app
    from werkzeug.serving import make_server
    app=create_app(start_jobs=False); nb=storage.create_notebook('HTTP review')
    server=make_server('127.0.0.1',0,app,threaded=True); thread=threading.Thread(target=server.serve_forever,name='review-http'); thread.start()
    base=f'http://127.0.0.1:{server.server_port}'; checks=[]
    def check(name,condition):
        assert condition,name; checks.append(name)
    def request(path,data=None):
        body=json.dumps(data).encode() if data is not None else None
        req=urllib.request.Request(base+path,data=body,headers={'Content-Type':'application/json'} if body else {})
        try:
            with urllib.request.urlopen(req,timeout=3) as r:
                b=r.read(); return r.status,json.loads(b) if 'application/json' in r.headers.get('Content-Type','') else b
        except urllib.error.HTTPError as exc: return exc.code,exc.read()
    try:
        check('HTTP template',request('/')[0]==200); check('HTTP JS asset',request('/static/js/app.js')[0]==200)
        check('HTTP notebook creation',request('/api/notebooks',{'name':'second'})[0]==201)
        doc=ds.save_upload(nb['id'],'Q2.pdf',(ROOT/'review_final/fixtures/A_Q2.pdf').read_bytes()); job=jobs.enqueue_index(nb['id'],doc['id'])
        check('worker start',jobs.start_worker())
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and storage.get_job(job)['status'] not in ['done','error']: time.sleep(.05)
        check('worker real indexing',storage.get_job(job)['status']=='done')
        check('HTTP indexed status',request('/api/jobs/'+job)[1]['status']=='done')
        check('HTTP citation image',request(f"/api/documents/{doc['id']}/pages/1/image")[0]==200)
        check('HTTP selection',request('/api/documents/'+doc['id']+'/select',{'selected':False})[0]==200)
        check('HTTP empty selected chat',request(f"/api/notebooks/{nb['id']}/chat",{'question':'efficiency'})[0]==400)
        request('/api/documents/'+doc['id']+'/select',{'selected':True})
        ms.analyze_page_image=lambda *a:'NO_RELEVANT_EVIDENCE'
        ms.text_completion=lambda *a,**k:json.dumps(dict(claims=[],calculations=[],limitations=[],unanswerable_parts=['review mock; no accuracy test']))
        status,queued=request(f"/api/notebooks/{nb['id']}/chat",{'question':'efficiency'})
        check('HTTP queued chat',status==202)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and storage.get_job(queued['job_id'])['status'] not in ['done','error']: time.sleep(.05)
        check('worker mocked answer job',storage.get_job(queued['job_id'])['status']=='done')
        check('HTTP persistent chat',len(request(f"/api/notebooks/{nb['id']}/chat")[1])==2)
        # Lock denial is an actual separate process, not a mocked acquire path.
        contender=subprocess.run([sys.executable,'-B',__file__,'--contender'],env=os.environ.copy(),capture_output=True,text=True,timeout=5)
        check('separate process active ownership',contender.returncode==0 and 'CONTENDER_DENIED' in contender.stdout)
        jobs.stop_worker(); check('worker shutdown',not jobs._worker_thread.is_alive() and jobs._process_lock_handle is None)
        # Bounded job proves that join timeout does not release ownership.
        entered=threading.Event(); release=threading.Event()
        original=jobs.run_job
        def blocking(job): entered.set(); release.wait(13)
        jobs.run_job=blocking
        storage.create_job('answer',{},notebook_id=nb['id']); jobs.start_worker(recover=False)
        check('blocking job entered',entered.wait(2)); handle=jobs._process_lock_handle; jobs.stop_worker()
        check('lock retained while alive',jobs._worker_thread.is_alive() and jobs._process_lock_handle is handle)
        release.set(); jobs.stop_worker(); jobs.run_job=original
        check('lock released after exit',not jobs._worker_thread.is_alive() and jobs._process_lock_handle is None)
        print(json.dumps(dict(passed=len(checks),failed=0,skipped=0,checks=checks,server_port=server.server_port),indent=2))
    finally:
        if 'release' in locals(): release.set()
        jobs.stop_worker()
        server.shutdown(); server.server_close(); thread.join(3)
        assert not thread.is_alive() and (jobs._worker_thread is None or not jobs._worker_thread.is_alive())
print('OWNED SERVER STOPPED; TEMP DATA CLEANED')
