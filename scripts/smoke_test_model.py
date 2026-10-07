"""Bounded real Qwen image test. No application data is opened.

Run: .venv/Scripts/python.exe -B scripts/smoke_test_model.py
One owned inference child; results and GPU telemetry go to runtime_setup/smoke.
"""
import argparse,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
MODEL_SHA='d16776dcd9a28d42758c2958ed3a752aabf20a305252cd64ff2be72b4a78c503'
PROJECTOR_SHA='987dd0733033fb5dd9b124d1ca926ae865572e432384eee7618b2eec3e735e17'

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()

def recognition_checks(answer):
    try:data=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',answer.strip()))
    except (ValueError,TypeError):return {'Q2=72%':False,'Q4=84%':False,'Q4 higher':False}
    if not isinstance(data,dict):return {'Q2=72%':False,'Q4=84%':False,'Q4 higher':False}
    def exact(q,value):
        item=data.get(q)
        return (isinstance(item,dict) and type(item.get('value')) in (int,float)
                and item['value']==value and item.get('unit')=='%')
    return {'Q2=72%':exact('Q2',72),'Q4=84%':exact('Q4',84),'Q4 higher':data.get('higher_quarter')=='Q4'}

def child(out):
    from config import Config
    from services import model_service
    result={'inference_success':False,'recognition_passed':False}
    try:
        for path,expected in [(Path(Config.MODEL_PATH),MODEL_SHA),(Path(Config.MMPROJ_PATH),PROJECTOR_SHA)]:
            if not path.is_file():raise RuntimeError(f'Required file missing: {path}')
            if sha256(path)!=expected:raise RuntimeError(f'Pinned repository SHA256 differs: {path.name}')
        result['preflight']=model_service.model_status()
        result['runtime_mode']='CPU fallback' if Config.N_GPU_LAYERS == 0 else 'GPU requested'
        if not result['preflight']['handler_available'] or (Config.N_GPU_LAYERS > 0 and not result['preflight']['gpu_offload_supported']):
            raise RuntimeError('CUDA runtime and Qwen25VLChatHandler must both be available')
        from PIL import Image,ImageDraw,ImageFont
        image=Image.new('RGB',(640,400),'white');draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',28)
        draw.rectangle([80,190,220,350],fill='steelblue');draw.rectangle([340,160,480,350],fill='darkorange')
        draw.text((75,145),'Q2: 72%',font=font,fill='black');draw.text((335,115),'Q4: 84%',font=font,fill='black')
        draw.text((60,360),'Production efficiency by quarter',font=font,fill='black')
        path=out/'known-chart.png';image.save(path);started=time.monotonic()
        answer=model_service.analyze_page_image(str(path),
            'Read the quarter labels and percentages directly from this chart image. '
            'Return JSON only with keys Q2 and Q4, each containing value (number) '
            'and unit (string), and higher_quarter (the quarter with the larger value). '
            'If any value is unreadable, use null. Do not guess.')
        result.update(answer=answer,total_seconds=round(time.monotonic()-started,3),inference_success=True,
                      checks=recognition_checks(answer),status=model_service.model_status())
        result['recognition_passed']=all(result['checks'].values())
        print(json.dumps(result,indent=2),flush=True)
        return 0 if result['recognition_passed'] else 1
    except Exception as exc:
        result['error']=str(exc);print(json.dumps(result,indent=2),flush=True);return 1
    finally:(out/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--child',action='store_true');parser.add_argument('--timeout',type=int,default=180);parser.add_argument('--cpu',action='store_true')
    args=parser.parse_args();out=ROOT/'runtime_setup/smoke';out.mkdir(parents=True,exist_ok=True)
    if args.child:return child(out)
    active=subprocess.run(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv'],capture_output=True,text=True,timeout=5)
    (out/'gpu-before.txt').write_text(active.stdout+active.stderr)
    if re.search(r'(?i)(python(?:w)?\.exe|llama-(?:server|cli)\.exe)',active.stdout):
        print('BLOCKED: another Python/llama GPU process is active');return 1
    env=os.environ.copy();env.update(N_CTX='2048',N_BATCH='128',N_GPU_LAYERS='20',MAX_IMAGE_DIM='512',MAX_TOKENS='160',MODEL_VERBOSE='1',VISUAL_TRANSCRIPTION_ENABLED='0',PYTHONDONTWRITEBYTECODE='1')
    if args.cpu:env['N_GPU_LAYERS']='0'
    telemetry=[];started=time.monotonic()
    with (out/'stdout.txt').open('w',encoding='utf-8') as stdout,(out/'stderr.txt').open('w',encoding='utf-8') as stderr:
        process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--child'],cwd=ROOT,env=env,stdout=stdout,stderr=stderr)
        try:
            while process.poll() is None:
                if time.monotonic()-started>args.timeout:
                    process.kill();process.wait(timeout=5);print('FAIL: owned inference child timed out');break
                sample=subprocess.run(['nvidia-smi','--query-gpu=timestamp,memory.used,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5)
                telemetry.append(dict(seconds=round(time.monotonic()-started,2),gpu=sample.stdout.strip()));time.sleep(1)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
    logs=(out/'stderr.txt').read_text(encoding='utf-8')
    memory=[]
    for sample in telemetry:
        try:memory.append(int(sample['gpu'].split(',')[-2].strip()))
        except (ValueError,IndexError):pass
    evidence=dict(child_pid=process.pid,exit_code=process.returncode,elapsed_seconds=round(time.monotonic()-started,2),
                  offload_log_matches=re.findall(r'offloaded\s+(\d+)/(\d+)\s+layers',logs),
                  peak_total_gpu_memory_mib=max(memory,default=None),telemetry=telemetry,
                  interpretation='Memory is whole-device total, not per-process allocation; offload and image accuracy are separate.')
    (out/'gpu-evidence.json').write_text(json.dumps(evidence,indent=2))
    print((out/'stdout.txt').read_text(encoding='utf-8'));print(json.dumps({k:v for k,v in evidence.items() if k!='telemetry'},indent=2))
    return process.returncode

if __name__=='__main__':raise SystemExit(main())
