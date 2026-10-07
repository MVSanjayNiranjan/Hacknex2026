import json,uuid
from pathlib import Path
import pymupdf as fitz
from app_smoke import post,request,wait,ROOT
nb=post('/api/notebooks',{'name':'Real image-only chart verification'})
doc=fitz.open();page=doc.new_page(width=640,height=400)
page.insert_image(page.rect,filename=str(ROOT/'smoke/known-chart.png'))
assert page.get_text()==''
data=doc.tobytes();doc.close();(ROOT/'image-only-chart.pdf').write_bytes(data)
boundary=uuid.uuid4().hex
body=f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="image-only-chart.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode()+data+f'\r\n--{boundary}--\r\n'.encode()
upload=request('/api/notebooks/'+nb['id']+'/documents',body,'multipart/form-data; boundary='+boundary)
index=wait(upload['results'][0]['job_id'],30)
answer=post('/api/notebooks/'+nb['id']+'/chat',{'question':'Read the production efficiency chart. What are the Q2 and Q4 percentages, and which quarter is higher? Cite the chart.'})
out={'notebook':nb,'upload':upload,'index':index,'answer_job':answer}
(ROOT/'chart-app-result.json').write_text(json.dumps(out,indent=2))
out['answer']=wait(answer['job_id'],120)
(ROOT/'chart-app-result.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2),flush=True)
