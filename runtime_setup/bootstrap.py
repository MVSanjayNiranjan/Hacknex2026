"""Pinned, hash-verified setup. Run in a terminal with permitted network access."""
import concurrent.futures,hashlib,json,os,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=Path(__file__).resolve().parent
REV='68bb8bc4b7df5289c143aaec0ab477a7d4051aab'
MODEL_SHA='d16776dcd9a28d42758c2958ed3a752aabf20a305252cd64ff2be72b4a78c503'
FILES=[dict(name='mmproj-F16.gguf',path=str(ROOT/'Model/mmproj-F16.gguf'),sha256='987dd0733033fb5dd9b124d1ca926ae865572e432384eee7618b2eec3e735e17',url=f'https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/{REV}/mmproj-F16.gguf?download=true'),
       dict(name='llama_cpp_python-0.3.36-py3-none-win_amd64.whl',path=str(OUT/'llama_cpp_python-0.3.36-py3-none-win_amd64.whl'),sha256='2db1d93d452244cbb400d32c64c2467f2f2d373f8014a60b778b5b67973ed9db',url='https://github.com/abetlen/llama-cpp-python/releases/download/v0.3.36-cu130/llama_cpp_python-0.3.36-py3-none-win_amd64.whl')]

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

def download(item):
    target=Path(item['path']); target.parent.mkdir(exist_ok=True)
    if target.exists():
        if digest(target)!=item['sha256']:raise RuntimeError(f'Existing {target.name} hash differs; refusing overwrite')
        print(target.name,'already verified',flush=True);return
    part=target.with_suffix(target.suffix+'.part')
    # No unattended retries: fail with a concrete network error.
    req=urllib.request.Request(item['url'],headers={'User-Agent':'FolioMind-Runtime-Setup'})
    started=time.monotonic(); count=0; last=0
    with urllib.request.urlopen(req,timeout=60) as response,part.open('wb') as f:
        total=int(response.headers.get('Content-Length','0'))
        while True:
            block=response.read(4*1024*1024)
            if not block:break
            f.write(block);count+=len(block)
            if time.monotonic()-last>10:print(target.name,count,'/',total,'bytes',flush=True);last=time.monotonic()
    actual=digest(part)
    if actual!=item['sha256']:raise RuntimeError(f'{target.name} SHA256 mismatch: {actual}')
    part.replace(target)
    item.update(bytes=target.stat().st_size,verified=True,seconds=round(time.monotonic()-started,2))
    print(target.name,'SHA256 VERIFIED',flush=True)

def main():
    OUT.mkdir(exist_ok=True)
    before=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True,check=True)
    (OUT/'bootstrap-packages-before.txt').write_text(before.stdout)
    model=ROOT/'Model/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf'
    if digest(model)!=MODEL_SHA:raise RuntimeError('Existing model does not match pinned repository SHA256')
    (OUT/'download-manifest.json').write_text(json.dumps(dict(repository_revision=REV,model_sha256=MODEL_SHA,files=FILES),indent=2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(download,FILES))
    (OUT/'download-manifest.json').write_text(json.dumps(dict(repository_revision=REV,model_sha256=MODEL_SHA,files=FILES),indent=2))
    venv=ROOT/'.venv'; python=venv/'Scripts/python.exe'
    if not python.exists():subprocess.run([sys.executable,'-m','venv','--system-site-packages',str(venv)],check=True)
    # CUDA and CPU releases share a version: never let the inherited CPU
    # distribution satisfy this install. Only the venv receives these files.
    subprocess.run([str(python),'-m','pip','install','--ignore-installed','--no-deps','--no-index',FILES[1]['path']],check=True)
    subprocess.run([str(python),'-B','-c','import llama_cpp; from llama_cpp.llama_chat_format import Qwen25VLChatHandler; print(llama_cpp.__version__); print("GPU offload:", llama_cpp.llama_supports_gpu_offload()); assert llama_cpp.llama_supports_gpu_offload(), "CUDA wheel loaded but GPU offload unavailable"'],check=True)
    print('SETUP COMPLETE; use .venv/Scripts/python.exe. No model was loaded.',flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:print('SETUP FAILED:',exc,flush=True);raise SystemExit(1)
