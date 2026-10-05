"""Real HTTP/files for atomic confirmed intent edits, with no model calls."""
import copy,json,uuid,os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from api.co_creation_demo import create_co_creation_demo
from api.dependencies import get_workflow_engine
from core.co_creation import CoCreation
from core.orchestrator import WorkflowEngine
P='comparison-demo';BASE=f'/api/project/{P}/co-creation';URL=BASE+'/applications'
@pytest.fixture
def setup(tmp_path):
    app=create_co_creation_demo(tmp_path)
    return TestClient(app,raise_server_exceptions=False),app.dependency_overrides[get_workflow_engine](),tmp_path

def files(d):return {str(p.relative_to(d)):p.read_bytes() for p in d.rglob('*') if p.is_file()}
def view(c):
    r=c.get(URL);assert r.status_code==200,r.text;return r.json()
def ready(c,stage='script_generation',confirm=True):
    w=view(c)['workspace'];field=w['fields'][stage][0]
    saved=c.post(BASE,json=dict(expected_token=w['token'],request_id=uuid.uuid4().hex,stage=stage,path=field['path'],message='人工修改本字段，保持其他文字。',preserve='其他字段保持。')).json()
    if confirm:
        r=c.post(BASE+'/decision',json=dict(expected_token=saved['workspace']['token'],request_id=uuid.uuid4().hex,intent_id=saved['intent_id'],decision='confirm',acknowledged=True));assert r.status_code==201,r.text
    return dict(expected_token=view(c)['workspace']['token'],intent_id=saved['intent_id'],value='人工填写的新文字\n<script>也只是文字</script>')
def preview(c,b):
    r=c.post(URL+'/preview',json=b);assert r.status_code==200,r.text;return r.json()
def save_body(c,b):return b|dict(preview_token=preview(c,b)['preview_token'],request_id=uuid.uuid4().hex,acknowledged=True)
def apply(c,b):
    request=save_body(c,b);r=c.post(URL,json=request);assert r.status_code==201,r.text;return request,r.json()

def test_read_old_data_without_writes(setup):
    c,e,d=setup;before=files(d);v=view(c)
    assert v['applications']==[] and v['workspace']==c.get(BASE).json() and v['revisions']['token']==v['workspace']['text_token']
    assert files(d)==before and e.get_state(P).co_creation is None

@pytest.mark.parametrize('stage',['script_generation','storyboard'])
def test_atomic_single_field_idempotence_restart_withdraw_and_legacy_write(setup,stage):
    c,e,d=setup;b=ready(c,stage);state=e.get_state(P);artifacts=copy.deepcopy(state.artifacts);n=len(state.content_history['versions']);before=files(d)
    pv=preview(c,b);assert files(d)==before and len(pv['revision']['changes'])==1
    req,result=apply(c,b);data=result['data'];a=data['applications'][0];v=data['revisions']['versions'][-1]
    assert a['version_id']==result['version_id']==v['version_id'] and a['request_id']==req['request_id'] and 'request_digest' not in a
    assert len(state.content_history['versions'])==n+1
    expected=copy.deepcopy(artifacts);target=expected[stage];path=data['workspace']['records'][0]['target']['path']
    for part in path[:-1]:target=target[part]
    target[path[-1]]=b['value'];assert state.artifacts==expected
    before=files(d);assert c.post(URL,json=req).json()==result and files(d)==before
    assert c.post(URL,json=req|{'value':'另一文字'}).status_code==409
    assert c.post(URL+'/preview',json=b|{'expected_token':data['workspace']['token']}).status_code==422
    assert CoCreation(WorkflowEngine(str(d/'sessions'))).applications(P).model_dump()==data
    assert c.post(BASE+'/decision',json=dict(expected_token=data['workspace']['token'],intent_id=b['intent_id'],request_id=uuid.uuid4().hex,decision='withdraw',acknowledged=True)).status_code==201
    assert view(c)['applications']==data['applications'] and state.artifacts==expected
    script=copy.deepcopy(state.artifacts['script_generation']);script['logline']='另一次人工旧入口修改';e.update_artifact(P,'script_generation',script)
    assert view(c)['applications']==data['applications']

@pytest.mark.parametrize('status',['pending','withdrawn','stale','running'])
def test_confirmation_basis_and_running_gates(setup,status):
    c,e,d=setup;b=ready(c,confirm=status!='pending')
    if status=='withdrawn':c.post(BASE+'/decision',json=dict(expected_token=b['expected_token'],intent_id=b['intent_id'],request_id=uuid.uuid4().hex,decision='withdraw',acknowledged=True))
    elif status=='stale':
        s=copy.deepcopy(e.get_state(P).artifacts['script_generation']);s['logline']='依据变化';e.update_artifact(P,'script_generation',s)
    elif status=='running':e._active_sessions.add(P)
    b['expected_token']=view(c)['workspace']['token'];before=files(d);expected=409 if status=='running' else 422
    assert c.post(URL+'/preview',json=b).status_code==expected
    assert c.post(URL,json=b|dict(preview_token='a'*64,request_id=uuid.uuid4().hex,acknowledged=True)).status_code==expected and files(d)==before

@pytest.mark.parametrize('change',[{'acknowledged':False},{'acknowledged':1},{'acknowledged':'true'},{'acknowledged':None},{'value':'字'*10001},{'value':None},{'value':1},{'stage':'storyboard'},{'path':['title']},{'replacement':{}},{'request_id':'bad'},{'preview_token':'bad'}])
def test_strict_inputs(change,setup):
    c,_,d=setup;req=save_body(c,ready(c));before=files(d);r=c.post(URL,json=req|change)
    assert r.status_code==422 and set(r.json()['error'])=={'code','message'} and files(d)==before

def test_clear_no_change_foreign_unknown_and_safe_project(setup):
    c,_,d=setup;b=ready(c);before=files(d)
    assert c.post(URL+'/preview',json=b|{'value':view(c)['workspace']['records'][0]['target']['value']}).status_code==422
    assert c.post(URL+'/preview',json=b|{'intent_id':uuid.uuid4().hex}).status_code==404
    assert c.post(URL+'/preview',json=b|{'expected_token':c.get('/api/project/manual-edit-demo/co-creation').json()['token']}).status_code==409
    assert c.get('/api/project/.unsafe/co-creation/applications').status_code==422 and c.get('/api/project/missing/co-creation/applications').status_code==404 and files(d)==before
    assert apply(c,b|{'value':''})[1]['data']['revisions']['versions'][-1]['changes'][0]['after']==''

def test_tampered_preview_concurrent_update_and_cross_operation_ids(setup):
    c,_,d=setup;b=ready(c);req=save_body(c,b);w=view(c)['workspace'];before=files(d)
    for change in [{'value':'另一文字'},{'preview_token':'b'*64},{'expected_token':'0'*64},{'request_id':w['records'][0]['request_id']},{'request_id':w['decisions'][0]['request_id']}]:assert c.post(URL,json=req|change).status_code==409
    assert files(d)==before
    ready(c,'storyboard');before=files(d);assert c.post(URL,json=req).status_code==409 and files(d)==before

def test_cross_operation_application_id_rejected(setup):
    c,_,_=setup;req,result=apply(c,ready(c));w=result['data']['workspace'];a=result['data']['applications'][0]
    assert c.post(BASE,json=dict(expected_token=w['token'],request_id=a['request_id'],stage='script_generation',path=['title'],message='复用')).status_code==409
    assert c.post(BASE+'/decision',json=dict(expected_token=w['token'],request_id=a['request_id'],intent_id=a['intent_id'],decision='withdraw',acknowledged=True)).status_code==409

def test_atomic_failure_rolls_back_all_state_and_file(setup,monkeypatch):
    c,e,d=setup;req=save_body(c,ready(c));before=files(d);state=copy.deepcopy(e.get_state(P).__dict__);calls=[]
    import core.orchestrator as module
    def broken(*args):calls.append(1);raise OSError('private-content-secret')
    with monkeypatch.context() as patch:
        patch.setattr(module,'atomic_write_session',broken);r=c.post(URL,json=req);assert r.status_code==503 and 'private-content-secret' not in r.text
    assert calls==[1] and files(d)==before and e.get_state(P).__dict__==state and view(c)['applications']==[]
    assert c.post(URL,json=req).status_code==201

@pytest.mark.parametrize('race',['text','history','ledger'])
def test_disk_race_before_commit_preserved(setup,monkeypatch,race):
    c,e,d=setup;req=save_body(c,ready(c));state=copy.deepcopy(e.get_state(P).__dict__);original=e.save_session_to_disk;p=d/'sessions'/f'{P}.json';raced=[]
    def write(*args,**kw):
        doc=json.loads(p.read_text())
        if race=='text':doc['artifacts']['script_generation']['title']='外部正文'
        elif race=='history':doc['content_history']['versions'][-1]['created_at']='2020-01-01T00:00:00+00:00'
        else:doc['co_creation']['records'][0]['message']='外部请求'
        p.write_text(json.dumps(doc,ensure_ascii=False));raced.append(p.read_bytes());return original(*args,**kw)
    monkeypatch.setattr(e,'save_session_to_disk',write)
    assert c.post(URL,json=req).status_code==503 and p.read_bytes()==raced[0] and e.get_state(P).__dict__==state

@pytest.mark.parametrize('damage',['unknown_version','foreign_intent','duplicate','request_reuse','before','hidden_change','missing_confirm','chronology'])
def test_corrupt_application_refused_after_restart(setup,damage):
    c,_,d=setup;apply(c,ready(c));p=d/'sessions'/f'{P}.json';doc=json.loads(p.read_text());a=doc['co_creation']['applications'][0];v=doc['content_history']['versions'][-1]
    if damage=='unknown_version':a['version_id']=uuid.uuid4().hex
    elif damage=='foreign_intent':a['intent_id']=uuid.uuid4().hex
    elif damage=='duplicate':doc['co_creation']['applications'].append(copy.deepcopy(a))
    elif damage=='request_reuse':a['request_id']=doc['co_creation']['records'][0]['request_id']
    elif damage=='before':v['changes'][0]['before']='伪造旧值'
    elif damage=='hidden_change':
        from core.content_revisions import digest
        v['content']['logline']='隐藏其他变化';v['digest']=digest(v['content']);doc['artifacts']['script_generation']=copy.deepcopy(v['content'])
    elif damage=='missing_confirm':doc['co_creation']['decisions']=[]
    else:a['created_at']='2000-01-01T00:00:00+00:00'
    p.write_text(json.dumps(doc,ensure_ascii=False));before=p.read_bytes()
    with pytest.raises(Exception):CoCreation(WorkflowEngine(str(d/'sessions'))).applications(P)
    assert p.read_bytes()==before

@pytest.mark.parametrize('limit',['ledger','history'])
def test_limits_reject_without_truncation(setup,monkeypatch,limit):
    c,_,d=setup;req=save_body(c,ready(c));before=files(d)
    if limit=='ledger':
        import core.co_creation as module
        from core.content_revisions import encoded
        raw=json.loads((d/'sessions'/f'{P}.json').read_text())['co_creation'];monkeypatch.setattr(module,'MAX_LEDGER_BYTES',len(encoded(raw))+10)
    else:
        import core.content_revisions as module
        monkeypatch.setattr(module,'MAX_HISTORY',1)
    assert c.post(URL,json=req).status_code==422 and files(d)==before

def test_fixture_actual_local_http(setup):
    c,_,_=setup;empty=view(c);b=ready(c);confirmed=view(c);pv=preview(c,b);req,saved=apply(c,b)
    if os.environ.get('WRITE_APPLY_FIXTURE')=='1':
        (Path(__file__).parents[2]/'frontend/tests/fixtures/co-creation-applications.json').write_text(json.dumps(dict(empty=empty,confirmed=confirmed,input=b,preview=pv,request=req,saved=saved),ensure_ascii=False,indent=2)+'\n')
    assert saved['data']==view(c)
