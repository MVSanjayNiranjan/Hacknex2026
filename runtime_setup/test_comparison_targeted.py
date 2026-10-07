import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from services import answer_service as a
from services.calculation_service import CalculationError

def rows():
    base=dict(document_id='d',page_number=1,section='1.2 Table 1',content_type='text',document_name='report')
    return [dict(base,id='header',text='Metric\nQ2\nQ3\nQ4',bbox=[0,0,100,10]),
            dict(base,id='row',text='Production efficiency\n72%\n78%\n84%',bbox=[0,20,100,30])]

def test_native_pairs_and_calculations(monkeypatch):
    es,facts,_=a._question_records('Compare production efficiency Q2 and Q4',rows())
    monkeypatch.setattr(a,'_page_context_text',lambda el:'')
    valid={f['id']:f for f in facts};parsed={}
    assert a._complete_comparison(parsed,'Compare production efficiency Q2 and Q4',facts)==[]
    results=[]
    for req in parsed['calculations']:
        ops=[a._validate_operand(o,valid)[0] for o in req['operands']]
        results.append(a.calc.run(req['operation'],ops).value)
    assert results==[12,16.6667]
    assert {f['fact']['period']:f['fact']['value'] for f in facts}=={'Q2':72,'Q4':84}
    assert facts[0]['source_element_ids']==['row','header']

def test_wrong_cell_rejected(monkeypatch):
    _,facts,_=a._question_records('Compare production efficiency Q2 and Q4',rows())
    monkeypatch.setattr(a,'_page_context_text',lambda el:'')
    with pytest.raises(CalculationError):
        a._validate_operand(dict(facts[0]['fact'],value=84,evidence_id=facts[0]['id']),{facts[0]['id']:facts[0]})

def test_commentary_is_incomplete():
    parsed={'claims':[{'text':'Management says 12 points'}]}
    assert a._complete_comparison(parsed,'Compare production efficiency Q2 and Q4',[])
    assert 'calculations' not in parsed

def test_duplicate_header_rejected():
    rs=rows();rs.append(dict(rs[0],id='ambiguous'))
    assert a._question_records('Compare production efficiency Q2 and Q4',rs)[1]==[]

def test_yield_not_efficiency():
    assert a._question_records('Compare first-pass yield Q2 and Q4',rows())[1]==[]

def test_no_silent_character_cut():
    row=rows()[1];row['text']='x'*2000+'END_MARKER'
    assert 'END_MARKER' in a._build_synthesis_prompt('question',[row],[])
