import json,time,urllib.request,uuid
from pathlib import Path
import fitz
ROOT=Path(__file__).resolve().parent
BASE='http://127.0.0.1:5057'
def request(path,data=None,ctype='application/json'):
    req=urllib.request.Request(BASE+path,data=data,headers={'Content-Type':ctype})
    with urllib.request.urlopen(req,timeout=15) as response:
        return json.load(response)
def post(path,data):return request(path,json.dumps(data).encode())
def wait(job,limit=180):
    end=time.monotonic()+limit
    while time.monotonic()<end:
        result=request('/api/jobs/'+job)
        if result['status'] in ('done','error'):return result
        time.sleep(1)
    return result
def main():
    out={}
    nb=post('/api/notebooks',{'name':'CPU runtime verification'})
    out['notebook']=nb
    boundary=uuid.uuid4().hex; body=b''
    for quarter,value in [('Q2',72),('Q4',84)]:
        doc=fitz.open();page=doc.new_page(width=400,height=200)
        page.insert_text((30,50),f'Factory Alpha {quarter} report',fontsize=18)
        page.insert_text((30,95),f'Production efficiency in {quarter} was {value}%.',fontsize=16)
        content=doc.tobytes();doc.close()
        (ROOT/f'{quarter}-runtime.pdf').write_bytes(content)
        body+=f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{quarter}-runtime.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode()+content+b'\r\n'
    body+=f'--{boundary}--\r\n'.encode()
    upload=request('/api/notebooks/'+nb['id']+'/documents',body,'multipart/form-data; boundary='+boundary)
    out['upload']=upload
    out['index_results']=[wait(item['job_id'],30) for item in upload['results'] if 'job_id' in item]
    (ROOT/'app-smoke-result.json').write_text(json.dumps(out,indent=2))
    question='What production efficiency did Factory Alpha report in Q2 and in Q4? Cite both reports.'
    answer=post('/api/notebooks/'+nb['id']+'/chat',{'question':question})
    out['answer_job']=answer; (ROOT/'app-smoke-result.json').write_text(json.dumps(out,indent=2))
    out['answer']=wait(answer['job_id'],180)
    out['model_status']=request('/api/model/status')
    (ROOT/'app-smoke-result.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2),flush=True)
if __name__=='__main__':main()
