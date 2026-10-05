"""Real local requests, persisted decisions and compatibility; no model calls."""
import copy
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from api.co_creation_demo import create_co_creation_demo
from api.dependencies import get_workflow_engine
from core.co_creation import CoCreation, validate_ledger
from core.orchestrator import WorkflowEngine

P='comparison-demo'
URL=f'/api/project/{P}/co-creation'

@pytest.fixture
def setup(tmp_path):
    app=create_co_creation_demo(tmp_path)
    return TestClient(app,raise_server_exceptions=False),app.dependency_overrides[get_workflow_engine](),tmp_path

def read(c,project=P):
    r=c.get(f'/api/project/{project}/co-creation')
    assert r.status_code==200,r.text
    return r.json()

def payload(c,stage='script_generation',**changes):
    w=read(c)
    return dict(expected_token=w['token'],request_id=uuid.uuid4().hex,stage=stage,path=w['fields'][stage][0]['path'],
                message='让这处文字更坚定。\n只改变语气。',preserve='人物关系和其他已确认文字保持。')|changes

def create(c,**changes):
    body=payload(c,**changes)
    r=c.post(URL,json=body)
    assert r.status_code==201,r.text
    return body,r.json()

def decision(c,intent,kind='confirm',**changes):
    return dict(expected_token=read(c)['token'],request_id=uuid.uuid4().hex,intent_id=intent,decision=kind,acknowledged=True)|changes

def files(d):
    return {str(p.relative_to(d)):p.read_bytes() for p in d.rglob('*') if p.is_file()}

def test_read_no_ledger_does_not_write_old_project(setup):
    c,e,d=setup;before=files(d)
    w=read(c)
    assert w['records']==w['decisions']==[] and w['model_enabled'] is False and not w['running']
    assert read(c)==w and files(d)==before and e.get_state(P).co_creation is None

@pytest.mark.parametrize('stage',['script_generation','storyboard'])
def test_create_confirm_withdraw_and_real_restart_preserve_all_other_business(setup,stage):
    c,e,d=setup
    state=e.get_state(P);artifacts=copy.deepcopy(state.artifacts);history=copy.deepcopy(state.content_history)
    original=json.loads((d/'sessions'/f'{P}.json').read_text())
    body,saved=create(c,stage=stage)
    intent=saved['intent_id'];w=saved['workspace'];r=w['records'][0]
    assert r['status']=='pending' and r['message']==body['message'] and r['target']==w['fields'][stage][0]
    assert r['text_token']==read(c)['text_token'] and 'request_digest' not in r
    response=c.post(URL+'/decision',json=decision(c,intent))
    assert response.status_code==201,response.text
    assert response.json()['workspace']['records'][0]['status']=='confirmed'
    response=c.post(URL+'/decision',json=decision(c,intent,'withdraw'))
    assert response.status_code==201 and response.json()['workspace']['records'][0]['status']=='withdrawn'
    after=read(c)
    fresh=WorkflowEngine(str(d/'sessions'))
    assert CoCreation(fresh).read(P).model_dump()==after
    durable=json.loads((d/'sessions'/f'{P}.json').read_text())
    assert state.artifacts==artifacts and state.content_history==history
    for key in original:
        if key!='updated_at':assert durable[key]==original[key]
    assert len(after['decisions'])==2 and 'request_digest' not in after['decisions'][0]

def test_idempotent_create_and_decisions_reject_changed_payload_and_cross_operation(setup):
    c,_,d=setup;body,result=create(c);before=files(d)
    assert c.post(URL,json=body).json()==result and files(d)==before
    assert c.post(URL,json=body|{'message':'另一件事'}).status_code==409
    action=decision(c,result['intent_id'])
    first=c.post(URL+'/decision',json=action)
    assert first.status_code==201
    before=files(d)
    assert c.post(URL+'/decision',json=action).json()==first.json() and files(d)==before
    assert c.post(URL+'/decision',json=action|{'decision':'withdraw'}).status_code==409
    assert c.post(URL+'/decision',json=decision(c,result['intent_id'],request_id=body['request_id'])).status_code==409

@pytest.mark.parametrize('change',[
    {'message':''},{'message':' \n\t'},{'message':'字'*10001},{'preserve':'字'*2001},
    {'path':[]},{'path':[True]},{'path':['title',-1]},{'path':['does_not_exist']},
    {'stage':'video_generation'},{'expected_token':'bad'},{'request_id':'bad'},{'replacement':{'title':'bad'}},
])
def test_input_boundaries_reject_without_writes(setup,change):
    c,_,d=setup;body=payload(c)|change;before=files(d)
    r=c.post(URL,json=body)
    assert r.status_code==422 and set(r.json())=={'error','detail'} and files(d)==before
    assert set(r.json()['error'])=={'code','message'} and r.json()['detail']==r.json()['error']['message']
    assert body.get('message','') not in r.text or len(body.get('message',''))<2

def test_valid_maximum_message_blank_preserve_and_safe_html_are_stored_as_text(setup):
    c,_,_=setup
    _,result=create(c,message='<script>alert(1)</script>\n'+'字'*(10000-26),preserve='')
    r=result['workspace']['records'][0]
    assert '<script>' in r['message'] and r['preserve']==''

def test_concurrent_old_token_foreign_project_and_unknown_intent(setup):
    c,_,d=setup;body=payload(c)
    create(c)
    before=files(d)
    assert c.post(URL,json=body).status_code==409
    foreign=read(c,'manual-edit-demo')['token']
    assert c.post(URL,json=payload(c,expected_token=foreign)).status_code==409
    assert c.post(URL+'/decision',json=decision(c,uuid.uuid4().hex)).status_code==404
    assert c.get('/api/project/missing/co-creation').status_code==404
    assert c.get('/api/project/.unsafe/co-creation').status_code==422
    assert files(d)==before

def test_text_change_invalidates_pending_confirmed_intent_and_old_writers_preserve_ledger(setup):
    c,e,d=setup
    _,first=create(c);_,second=create(c)
    assert c.post(URL+'/decision',json=decision(c,first['intent_id'])).status_code==201
    ledger=copy.deepcopy(e.get_state(P).co_creation)
    board=copy.deepcopy(e.get_state(P).artifacts['storyboard'])
    board['episodes'][0]['segments'][0]['shots'][0]['dialogue']='真实本地新台词，不调用模型。'
    e.update_artifact(P,'storyboard',board)
    assert e.get_state(P).co_creation==ledger
    assert json.loads((d/'sessions'/f'{P}.json').read_text())['co_creation']==ledger
    w=read(c)
    assert [r['status'] for r in w['records']]==['stale','stale']
    assert c.post(URL+'/decision',json=decision(c,second['intent_id'])).status_code==422
    assert c.post(URL+'/decision',json=decision(c,first['intent_id'],'withdraw')).status_code==201
    assert read(c)['records'][0]['status']=='withdrawn'

def test_running_blocks_create_confirm_but_withdraw_does_not_cancel_workflow(setup):
    c,e,_=setup;_,result=create(c)
    e._active_sessions.add(P)
    try:
        assert read(c)['running'] is True
        assert c.post(URL,json=payload(c)).status_code==409
        assert c.post(URL+'/decision',json=decision(c,result['intent_id'])).status_code==409
        assert c.post(URL+'/decision',json=decision(c,result['intent_id'],'withdraw')).status_code==201
        assert P in e._active_sessions
    finally:e._active_sessions.discard(P)

@pytest.mark.parametrize('ack',[False,1,'true',None])
def test_decision_requires_explicit_boolean(setup,ack):
    c,_,d=setup;_,result=create(c);before=files(d)
    assert c.post(URL+'/decision',json=decision(c,result['intent_id'],acknowledged=ack)).status_code==422
    assert files(d)==before

def test_invalid_decision_transitions_leave_history_untouched(setup):
    c,_,d=setup;_,result=create(c);intent=result['intent_id']
    assert c.post(URL+'/decision',json=decision(c,intent)).status_code==201
    assert c.post(URL+'/decision',json=decision(c,intent)).status_code==422
    assert c.post(URL+'/decision',json=decision(c,intent,'withdraw')).status_code==201
    before=files(d)
    for kind in ['withdraw','confirm']:
        assert c.post(URL+'/decision',json=decision(c,intent,kind)).status_code==422
    assert files(d)==before

def test_atomic_failure_rolls_back_memory_file_and_allows_same_explicit_request(setup,monkeypatch):
    c,e,d=setup;body=payload(c);before=files(d);state=copy.deepcopy(e.get_state(P).__dict__)
    import core.orchestrator as orchestrator
    with monkeypatch.context() as patch:
        patch.setattr(orchestrator,'atomic_write_session',lambda *a:(_ for _ in ()).throw(OSError('private-path-content')))
        r=c.post(URL,json=body)
        assert r.status_code==503 and 'private-path' not in r.text
    assert files(d)==before and e.get_state(P).__dict__==state and not read(c)['records']
    assert c.post(URL,json=body).status_code==201

@pytest.mark.parametrize('damage',['schema','duplicate','decision_reference','blank','bad_path','external','json'])
def test_damaged_or_external_ledger_refused_even_with_cache_without_overwrite(setup,damage):
    c,e,d=setup;_,result=create(c);body=payload(c)
    p=d/'sessions'/f'{P}.json';doc=json.loads(p.read_text());raw=doc['co_creation']
    if damage=='schema':raw['schema_version']=2
    elif damage=='duplicate':raw['records'].append(copy.deepcopy(raw['records'][0]))
    elif damage=='decision_reference':
        record=raw['records'][0]
        raw['decisions']=[{'decision_id':uuid.uuid4().hex,'number':1,'intent_id':uuid.uuid4().hex,'decision':'confirm','created_at':record['created_at'],'request_id':uuid.uuid4().hex,'request_digest':'a'*64}]
    elif damage=='blank':raw['records'][0]['message']=' '
    elif damage=='bad_path':raw['records'][0]['target']['path']=[True]
    elif damage=='external':raw['records'][0]['message']='外部更新'
    p.write_text('{bad' if damage=='json' else json.dumps(doc,ensure_ascii=False))
    broken=p.read_bytes()
    assert c.get(URL).status_code==503
    assert c.post(URL,json=body).status_code==503 and p.read_bytes()==broken

def test_limits_reject_without_truncating_and_do_not_overwrite_bad_future_ledger(setup,monkeypatch):
    c,_,d=setup;body=payload(c);before=files(d)
    import core.co_creation as module
    monkeypatch.setattr(module,'MAX_LEDGER_BYTES',1)
    assert c.post(URL,json=body).status_code==422 and files(d)==before

def test_record_count_limit_real_hundred_requests_then_reject(setup):
    c,_,d=setup
    for i in range(100):create(c,message=f'第{i+1}轮意图')
    before=files(d)
    assert c.post(URL,json=payload(c)).status_code==422
    assert len(read(c)['records'])==100 and files(d)==before

def test_no_fields_empty_project_cannot_create_and_read_is_stable(setup):
    c,_,d=setup;w=read(c,'revision-empty');before=files(d)
    assert w['fields']=={'script_generation':[],'storyboard':[]}
    body=payload(c,expected_token=w['token'],path=['title'])
    assert c.post('/api/project/revision-empty/co-creation',json=body).status_code==422
    assert files(d)==before

def test_old_write_rejects_valid_external_ledger_and_restores_artifact(setup):
    c,e,d=setup;create(c)
    state=e.get_state(P);original=copy.deepcopy(state.artifacts)
    p=d/'sessions'/f'{P}.json';doc=json.loads(p.read_text())
    doc['co_creation']['records'][0]['message']='磁盘上较新的人工意图'
    p.write_text(json.dumps(doc,ensure_ascii=False));before=p.read_bytes()
    board=copy.deepcopy(state.artifacts['storyboard'])
    board['episodes'][0]['segments'][0]['shots'][0]['dialogue']='不要覆盖外部意图'
    with pytest.raises(ValueError):
        e.update_artifact(P,'storyboard',board)
    assert p.read_bytes()==before and state.artifacts==original
