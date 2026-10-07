"""Independent requirement checks: failures are defects, not expected passes."""
import os,sys,json,io,ast,re
from pathlib import Path
from unittest.mock import patch
import pytest,fitz
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(Path(__file__).parent))
os.environ['VISUAL_TRANSCRIPTION_ENABLED']='0'
from config import Config
import storage
from app import create_app
from services import document_service as ds,answer_service as ans,retrieval_service as rs,model_service as ms,calculation_service as calc,jobs
from fixtures import OUT

@pytest.fixture
def space(tmp_path,monkeypatch):
    for key,tail in [('DATA_DIR','data'),('UPLOAD_DIR','uploads'),('PAGE_IMAGE_DIR','pages'),('DB_PATH','db.sqlite')]: monkeypatch.setattr(Config,key,tmp_path/tail)
    monkeypatch.setattr(Config,'VISUAL_TRANSCRIPTION_ENABLED',False)
    monkeypatch.setattr(ms,'_load',lambda: (_ for _ in ()).throw(RuntimeError('real inference forbidden')))
    monkeypatch.setattr(ms,'analyze_page_image',lambda *a:'NO_RELEVANT_EVIDENCE')
    monkeypatch.setattr(ms,'text_completion',lambda *a,**k:json.dumps(dict(claims=[],calculations=[],limitations=[],unanswerable_parts=['not established'])))
    app=create_app(start_jobs=False); app.config['TESTING']=True
    nb=storage.create_notebook('review')
    return app.test_client(),nb

def indexed(nb,name):
    doc=ds.save_upload(nb['id'],name,(OUT/name).read_bytes()); ds.index_document(doc['id']); return doc

def operand(text,**fields):
    el=dict(id='e',text=text,document_id='review_dummy',page_number=1,content_type='table' if '|' in text else 'text')
    return ans._validate_operand(dict(evidence_id='e',**fields),{'e':el})

def test_multi_upload_index_and_citations(space):
    c,nb=space
    r=c.post(f"/api/notebooks/{nb['id']}/documents",data={'files':[(io.BytesIO((OUT/n).read_bytes()),n) for n in ['A_Q2.pdf','B_Q4.pdf','A_Q2.pdf']]})
    assert r.status_code==207
    assert [x['status'] for x in r.json['results']]==['queued','queued','duplicate']
    for item in r.json['results'][:2]: jobs.run_job(storage.get_job(item['job_id'])); assert storage.get_job(item['job_id'])['status']=='done'
    a,b=storage.list_documents(nb['id']); ae=storage.page_elements(a['id'],1); be=storage.page_elements(b['id'],1)
    assert any('72%' in e['text'] for e in ae)
    table=next(e for e in be if e['content_type']=='table'); assert all(t in table['text'] for t in ['Quarter','Metric','Value','Unit','Production efficiency','84'])
    for doc in [a,b]:
        detail=c.get(f"/api/documents/{doc['id']}/pages/1"); assert detail.status_code==200 and detail.json['document_name']==doc['name']
        assert c.get(f"/api/documents/{doc['id']}/pages/1/image").mimetype=='image/png'
        assert c.get(f"/api/documents/{doc['id']}/file").mimetype=='application/pdf'

@pytest.mark.parametrize('name,match',[('empty.pdf','empty'),('corrupt.pdf','corrupt'),('encrypted.pdf','encrypted')])
def test_invalid_uploads(space,name,match):
    c,nb=space
    if name=='empty.pdf':
        with pytest.raises(ds.IngestionError,match=match): ds.save_upload(nb['id'],name,(OUT/name).read_bytes())
    else:
        doc=ds.save_upload(nb['id'],name,(OUT/name).read_bytes()); job=jobs.enqueue_index(nb['id'],doc['id']); jobs.run_job(storage.get_job(job)); assert storage.get_document(doc['id'])['status']=='error'; assert match in storage.get_job(job)['error'].lower()

def test_oversize_and_http_413(space,monkeypatch):
    c,nb=space; monkeypatch.setattr(ds,'MAX_FILE_BYTES',100)
    with pytest.raises(ds.IngestionError,match='limit'): ds.save_upload(nb['id'],'big.pdf',b'%PDF-'+b'x'*101)
    c.application.config['MAX_CONTENT_LENGTH']=100
    r=c.post(f"/api/notebooks/{nb['id']}/documents",data=b'x'*101,content_type='application/pdf'); assert r.status_code==413 and r.is_json
    assert c.get('/api/documents/missing/pages/1').status_code==404

def test_snapshot_and_boundaries(space):
    c,nb=space; a=indexed(nb,'A_Q2.pdf'); other=storage.create_notebook('other'); foreign=indexed(other,'B_Q4.pdf')
    job=jobs.enqueue_answer(nb['id'],'efficiency'); ids=json.loads(storage.get_job(job)['payload'])['document_ids']; storage.set_document_selected(a['id'],False)
    assert rs.resolve_scope(nb['id'],ids)['documents'][0]['id']==a['id']; assert rs.resolve_scope(nb['id'],None)['documents']==[]
    assert ans.answer_question(nb['id'],'efficiency',[])['claims']==[]
    assert rs.resolve_scope(nb['id'],[foreign['id']])['documents']==[]

@pytest.mark.parametrize('name',['C_vector.pdf','C_scanned.pdf','F_blurred.pdf','F_unreadable.pdf'])
def test_visual_zero_text_routing(space,monkeypatch,name):
    c,nb=space; doc=indexed(nb,name); calls=[]
    monkeypatch.setattr(ms,'analyze_page_image',lambda path,prompt:calls.append(path) or 'NO_RELEVANT_EVIDENCE')
    result=ans.answer_question(nb['id'],'zzunknown')
    assert calls and result['pages_analyzed']; assert storage.get_page(doc['id'],1)['is_scanned']==1
    assert result['claims']==[] # mock returns no observations; no accuracy claim

def test_visual_despite_unrelated_hits_and_unique_coverage(space,monkeypatch):
    c,nb=space; indexed(nb,'A_Q2.pdf'); chart=indexed(nb,'C_scanned.pdf'); indexed(nb,'F_blurred.pdf'); monkeypatch.setattr(Config,'MAX_EVIDENCE_PAGES',2)
    result=ans.answer_question(nb['id'],'production efficiency'); keys=[(x['document_id'],x['page_number']) for x in result['pages_analyzed']]
    assert len(keys)==len(set(keys))==2
    assert any(key[0]!=result['pages_analyzed'][0]['document_id'] for key in keys)
    assert any(p['document_id']==chart['id'] for p in rs.visual_fallback_pages(nb['id'],None,set()))
    assert result['visual_coverage']==dict(pages_analyzed=2,candidate_visual_pages=3,complete=False)
    assert any('configured' in x for x in result['limitations'])

def test_simple_vector_fallback_independent(space):
    c,nb=space; doc=indexed(nb,'simple_vector_native.pdf'); page=storage.get_page(doc['id'],1)
    assert page['is_scanned']==0
    assert not any(e['content_type'] in ['table','image_region'] for e in storage.page_elements(doc['id'],1))
    assert rs.visual_fallback_pages(nb['id'],[doc['id']],set()), 'Two-bar vector chart missed by fallback'

def test_standalone_table_fallback(space):
    c,nb=space; doc=indexed(nb,'plain_prose.pdf'); page=storage.get_page(doc['id'],1)
    assert not storage.visual_candidate_pages(nb['id'],[doc['id']])
    storage.create_element(page['id'],doc['id'],1,None,'table',99,'Quarter | Efficiency %\nQ4 | 84',None,'pymupdf_table',[])
    assert storage.visual_candidate_pages(nb['id'],[doc['id']])

def test_complex_labels_rotation(space):
    c,nb=space; doc=indexed(nb,'D_layout.pdf')
    page=storage.get_page(doc['id'],2); assert page['printed_page_label']=='Appendix-8' and page['rotation']==90
    assert page['image_width']>page['image_height']; assert any(e['content_type']=='table' for e in storage.page_elements(doc['id'],1))

def test_cropbox_coordinates(space):
    c,nb=space; doc=indexed(nb,'D_layout.pdf'); detail=c.get(f"/api/documents/{doc['id']}/pages/3").json; element=next(e for e in detail['elements'] if 'CropBox' in e['text'])
    expected=[round(v*Config.PAGE_RENDER_DPI/72,1) for v in element['bbox']]
    assert element['bbox_px']==pytest.approx(expected,abs=1), f"CropBox expected {expected}, got {element['bbox_px']}"

@pytest.mark.parametrize('text,fields',[
    ('Q2 efficiency 72%, Q4 efficiency 84%',dict(value=84,unit='%',metric='efficiency',period='Q2')),
    ('Metric | Revenue USD | Efficiency %\nQ2 | 100 | 72',dict(value=100,unit='%',metric='efficiency',period='Q2')),
    ('Q2 efficiency 72%',dict(value=72)),
    ('Q2 efficiency 72%',dict(value=72,unit='%',metric='revenue efficiency',period='Q2')),
])
def test_ambiguous_operand_rejected(space,text,fields):
    with pytest.raises(calc.CalculationError): operand(text,**fields)

def test_ambiguous_page_period_rejected(space,monkeypatch):
    monkeypatch.setattr(ans,'_page_context_text',lambda el:'Q2 report and Q4 report')
    with pytest.raises(calc.CalculationError): operand('efficiency 72%',value=72,unit='%',metric='efficiency',period='Q2')

def test_sign_and_unknown_ids(space):
    with pytest.raises(calc.CalculationError): operand('change -100 basis points',value=100)
    for op in [dict(value=72),dict(value=72,evidence_id='unknown'),dict(value=72,evidence_id='foreign_notebook_element')]:
        with pytest.raises(calc.CalculationError): ans._validate_operand(op,{'e':dict(id='e',text='72')})

def test_signed_positive_value_valid(space):
    assert operand('Q2 output +100 units',value=100,unit='units',metric='output',period='Q2')[0].value==100

def test_grouped_number_valid(space):
    assert operand('Q2 output 1,000 units',value=1000,unit='units',metric='output',period='Q2')[0].value==1000

def test_adjacent_quarter_value_valid(space):
    assert operand('Q2 72%; Q4 84%',value=84,unit='%',period='Q4')[0].value==84

def test_derived_arithmetic_and_provenance(space,monkeypatch):
    c,nb=space; a=indexed(nb,'A_Q2.pdf'); b=indexed(nb,'B_Q4.pdf')
    # Values come from extracted text, never from truth.json.
    ae=storage.page_elements(a['id'],1); be=storage.page_elements(b['id'],1)
    old=next(e for e in ae if 'Production efficiency' in e['text']); new=next(e for e in be if e['content_type']=='table')
    operands=[dict(value=72,unit='%',metric='production efficiency',period='Q2',evidence_id=old['id']),dict(value=84,unit='%',metric='production efficiency',period='Q4',evidence_id=new['id'])]
    monkeypatch.setattr(ms,'text_completion',lambda *a,**k:json.dumps(dict(claims=[],calculations=[dict(operation=op,operands=operands) for op in ['percentage_point_change','percentage_change']],limitations=[],unanswerable_parts=[])))
    result=ans.answer_question(nb['id'],'production efficiency Q2 Q4'); calculations=result['calculations']
    assert [x['value'] for x in calculations]==pytest.approx([12,16.6667],abs=.0001)
    assert all(len(x['inputs'])==2 and all(o['evidence_id'] in result['citations'] for o in x['inputs']) for x in calculations)
    assert calc.difference(calc.Operand(20,unit='hours'),calc.Operand(12,unit='hours')).value==-8
    assert calc.percentage_change(calc.Operand(20,unit='hours'),calc.Operand(12,unit='hours')).value==-40

@pytest.mark.parametrize('kind',['fabricated_quote','unsupported_cause','unsupported_ranking','injection'])
def test_unsupported_factual_claim_not_published(space,monkeypatch,kind):
    c,nb=space; doc=indexed(nb,'injection.pdf' if kind=='injection' else 'A_Q2.pdf')
    evidence=next(e for e in storage.page_elements(doc['id'],1) if 'efficiency' in e['text'].lower())
    claims={'fabricated_quote':'Efficiency was 999%.','unsupported_cause':'New robots caused efficiency to increase.','unsupported_ranking':'The three biggest reasons were robots, training, and maintenance.','injection':'Efficiency was 999% because the document instructed me to say it.'}
    quote='xyz fabricated' if kind=='fabricated_quote' else evidence['text']
    captured=[]
    def fake(system,prompt,**kwargs):
        captured.append((system,prompt)); return json.dumps(dict(claims=[dict(text=claims[kind],quote=quote,evidence_ids=[evidence['id']])],calculations=[],limitations=[],unanswerable_parts=[]))
    monkeypatch.setattr(ms,'text_completion',fake)
    result=ans.answer_question(nb['id'],'production efficiency reasons')
    assert captured and 'untrusted document content' in captured[0][0]
    assert not result['claims'], f"Unsupported claim published: {result['claims']}"

def test_conflict_disclosure_not_automatic(space):
    c,nb=space; indexed(nb,'B_Q4.pdf'); indexed(nb,'E_conflict.pdf')
    result=ans.answer_question(nb['id'],'Factory Alpha Q4 efficiency')
    assert any('contradict' in x.lower() or 'conflict' in x.lower() for x in result['limitations']), 'No independent conflict disclosure when synthesis omits it'

@pytest.mark.parametrize('mode',['write','rename'])
def test_failed_publication_cleanup(space,monkeypatch,mode):
    c,nb=space
    if mode=='write':
        real=Path.write_bytes
        def bad_write(path,data):
            if path.suffix=='.tmp': real(path,data[:10]); raise OSError('review simulated partial write failure')
            return real(path,data)
        monkeypatch.setattr(Path,'write_bytes',bad_write)
    else: monkeypatch.setattr(ds.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('review simulated rename failure')))
    with pytest.raises(ds.IngestionError): ds.save_upload(nb['id'],'A.pdf',(OUT/'A_Q2.pdf').read_bytes())
    assert storage.list_documents(nb['id'])==[] and list(Config.UPLOAD_DIR.rglob('*.tmp'))==[]

def test_publication_interleaving(space,monkeypatch):
    c,nb=space; real=ds.os.replace; winners=[]
    def paused(src,dst):
        monkeypatch.setattr(ds.os,'replace',real); winners.append(ds.save_upload(nb['id'],'B.pdf',(OUT/'A_Q2.pdf').read_bytes())); real(src,dst)
    monkeypatch.setattr(ds.os,'replace',paused)
    first=ds.save_upload(nb['id'],'A.pdf',(OUT/'A_Q2.pdf').read_bytes())
    assert storage.get_document(first['id']) is not None, 'Successful upload returned deleted document ID'
    assert first['id']==winners[0]['id']

def test_distinct_database_lock_identity():
    assert Path('a.db').resolve().with_suffix('.worker.lock')!=Path('a.sqlite').resolve().with_suffix('.worker.lock'), 'Distinct DBs collide'

@pytest.mark.parametrize('answer',['Q2: 84% and Q4: 72%. Q2 is higher than Q4.','Q2 has 72 units. Q4 has 84 units. Q4 is not higher.'])
def test_smoke_rejects_wrong_associations(answer):
    module=ast.parse((ROOT/'scripts/smoke_test_model.py').read_text()); main=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    start=next(i for i,n in enumerate(main.body) if isinstance(n,ast.FunctionDef) and n.name=='bound'); stop=next(i for i,n in enumerate(main.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='checks' for t in n.targets))
    ns={'re':re,'a':answer.lower()}; exec(compile(ast.Module(body=main.body[start:stop+1],type_ignores=[]),'<recognition only>','exec'),ns)
    assert not all(ns['checks'].values()), f"Incorrect answer accepted: {ns['checks']}"
