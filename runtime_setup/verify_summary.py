"""One real summary request against the selected Meridian report."""
import json
import time
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:5057'
NOTEBOOK = 'nb_8fc6ed4a801a4823'


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.load(response)


docs = get(f'/api/notebooks/{NOTEBOOK}/documents')
selected = [doc for doc in docs if doc['selected'] and doc['status'] == 'ready']
assert len(selected) == 1 and selected[0]['name'] == 'Meridian_Operations_Test.pdf', selected
request = urllib.request.Request(
    BASE + f'/api/notebooks/{NOTEBOOK}/chat',
    json.dumps({'question': 'Summarize this report'}).encode(),
    {'Content-Type': 'application/json'},
)
with urllib.request.urlopen(request, timeout=10) as response:
    job_id = json.load(response)['job_id']
print('Real summary job:', job_id, flush=True)
started = time.monotonic()
last_message = None
while time.monotonic() - started < 900:
    job = get('/api/jobs/' + job_id)
    if job.get('message') != last_message:
        print(job['status'], job.get('message'), flush=True)
        last_message = job.get('message')
    if job['status'] in ('done', 'error'):
        break
    time.sleep(2)
else:
    raise RuntimeError('Summary request did not finish within 15 minutes')
Path(__file__).with_name('meridian-summary-actual.json').write_text(
    json.dumps({'elapsed_seconds': round(time.monotonic() - started, 2), 'job': job}, indent=2),
    encoding='utf-8',
)
assert job['status'] == 'done', job.get('error')
answer = job['result']
assert answer['claims'] and not answer['calculations']
assert all(c['support_check'] == 'quote_found_in_evidence' for c in answer['claims'])
print('FINAL DISPLAYED CONTENT:', flush=True)
for claim in answer['claims']:
    print(claim['text'])
    for eid in claim['evidence_ids']:
        citation = answer['citations'][eid]
        print(f"  {citation['document_name']} · p.{citation['page_number']}")
    if claim['quote']:
        print('  Quote:', claim['quote'])
if answer['pages_analyzed']:
    print('Page images analyzed: ' + ', '.join(
        f"{p['document_name']} p.{p['page_number']}" for p in answer['pages_analyzed']))
if answer['unanswerable_parts']:
    print('Could not answer from the sources:', '; '.join(answer['unanswerable_parts']))
if answer['limitations']:
    print('Limitations:')
    for limitation in answer['limitations']:
        print('•', limitation)
