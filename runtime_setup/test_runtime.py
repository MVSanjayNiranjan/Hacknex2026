"""Regression checks for runtime guards and strict smoke acceptance; no inference."""
import importlib.util,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('qwen_smoke',ROOT/'scripts/smoke_test_model.py')
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)

@pytest.mark.parametrize('answer',[
    'Q2: 84% and Q4: 72%. Q2 is higher than Q4.',
    'Q2 has 72 units. Q4 has 84 units. Q4 is not higher.',
    json.dumps({'Q2':{'value':84,'unit':'%'},'Q4':{'value':72,'unit':'%'},'higher_quarter':'Q2'}),
    json.dumps({'Q2':{'value':72,'unit':'units'},'Q4':{'value':84,'unit':'units'},'higher_quarter':'Q4'}),
    json.dumps({'Q2':{'value':72,'unit':'%'},'Q4':{'value':84,'unit':'%'},'higher_quarter':'Q2'}),
    '[]', '{}', 'nonempty but inaccurate',
])
def test_rejects_incorrect_or_unstructured(answer):
    assert not all(smoke.recognition_checks(answer).values())

def test_accepts_exact_chart_associations():
    answer=json.dumps({'Q2':{'value':72,'unit':'%'},'Q4':{'value':84,'unit':'%'},'higher_quarter':'Q4'})
    assert all(smoke.recognition_checks(answer).values())

def test_cuda_required_before_creating_model(monkeypatch):
    from services import model_service as ms
    import llama_cpp
    ms.reset_for_tests();calls=[]
    monkeypatch.setattr(ms,'_check_files',lambda:None)
    monkeypatch.setattr(ms.Config,'N_GPU_LAYERS',20)
    monkeypatch.setattr(llama_cpp,'llama_supports_gpu_offload',lambda:False)
    monkeypatch.setattr(llama_cpp,'Llama',lambda **kwargs:calls.append(kwargs))
    try:
        with pytest.raises(ms.ModelUnavailableError,match='no GPU offload support'):ms._load()
        assert calls==[]
    finally:ms.reset_for_tests()
