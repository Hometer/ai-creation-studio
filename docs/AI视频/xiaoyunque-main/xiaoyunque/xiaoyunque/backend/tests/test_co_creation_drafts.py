"""Real local HTTP/files: recoverable drafts never imply approval or text writes."""
import copy,json,os,uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from api.co_creation_draft_demo import create_draft_demo
from api.dependencies import get_workflow_engine
from core.co_creation import CoCreation
from core.orchestrator import WorkflowEngine
from core.asset_library import AssetLibraryError
P='comparison-demo';BASE=f'/api/project/{P}/co-creation';URL=BASE+'/drafts'
@pytest.fixture
def setup(tmp_path):
    app=create_draft_demo(tmp_path)
    return TestClient(app,raise_server_exceptions=False),app.dependency_overrides[get_workflow_engine](),tmp_path

def files(d):return {str(p.relative_to(d)):p.read_bytes() for p in d.rglob('*') if p.is_file()}
def view(c):
    r=c.get(URL);assert r.status_code==200,r.text;return r.json()
def body(c,stage='script_generation',**changes):
    w=view(c)['data']['workspace'];intent=next(r for r in w['records'] if r['stage']==stage)
    return dict(expected_token=w['token'],intent_id=intent['intent_id'],value='人工草稿\n<script>只作为文字</script>',request_id=uuid.uuid4().hex)|changes
def save(c,b):
    r=c.post(URL,json=b);assert r.status_code==201,r.text;return r.json()

def test_old_workspace_read_no_migration_or_write(setup):
    c,e,d=setup;before=files(d);v=view(c)
    assert v['drafts']==[] and v['data']==c.get(BASE+'/applications').json() and files(d)==before
    # A file from the previous slice lacks the new optional array.
    p=d/'sessions'/f'{P}.json';raw=json.loads(p.read_text());raw['co_creation'].pop('drafts',None);p.write_text(json.dumps(raw));before=p.read_bytes()
    fresh=WorkflowEngine(str(d/'sessions'));assert CoCreation(fresh).drafts(P).drafts==[] and p.read_bytes()==before

@pytest.mark.parametrize('stage',['script_generation','storyboard'])
def test_multiline_history_restore_idempotence_and_text_untouched(setup,stage):
    c,e,d=setup;state=e.get_state(P);original=copy.deepcopy(state.__dict__);b=body(c,stage);first=save(c,b);before=files(d)
    assert c.post(URL,json=b).json()==first and files(d)==before
    assert c.post(URL,json=b|{'value':'其他文字'}).status_code==409
    second=save(c,body(c,stage,value='第二次已保存草稿'))
    rows=second['result']['drafts'];assert [r['number'] for r in rows]==[1,2] and rows[0]['value']==b['value'] and rows[1]['value']=='第二次已保存草稿'
    assert all('request_digest' not in r for r in rows)
    for key,value in original.items():
        if key not in ('updated_at','co_creation'):assert state.__dict__[key]==value
    assert second['result']['data']['revisions']==first['result']['data']['revisions']
    fresh=WorkflowEngine(str(d/'sessions'));assert CoCreation(fresh).drafts(P).model_dump()==second['result']
    # Older successful request can be explicitly looked up without replacing newer draft.
    retry=c.post(URL,json=b);assert retry.status_code==201 and retry.json()['draft_id']==first['draft_id'] and len(retry.json()['result']['drafts'])==2

@pytest.mark.parametrize('value',['','   ','字'*10000])
def test_valid_clear_original_and_maximum(setup,value):
    c,_,_=setup;assert save(c,body(c,value=value))['result']['drafts'][0]['value']==value
    original=view(c)['data']['workspace']['records'][0]['target']['value'];assert save(c,body(c,value=original))['result']['drafts'][-1]['value']==original

@pytest.mark.parametrize('change',[{'value':'字'*10001},{'value':None},{'value':1},{'value':True},{'request_id':'bad'},{'expected_token':'bad'},{'intent_id':'bad'},{'stage':'storyboard'},{'path':['title']},{'replacement':{}},{'acknowledged':True}])
def test_strict_input_no_writes(setup,change):
    c,_,d=setup;before=files(d);r=c.post(URL,json=body(c)|change)
    assert r.status_code==422 and files(d)==before and set(r.json()['error'])=={'code','message'}
    assert 'traceback' not in r.text.lower()

@pytest.mark.parametrize('state_name',['pending','withdrawn','stale','running','applied'])
def test_state_gates_keep_existing_drafts(setup,state_name):
    c,e,d=setup;save(c,body(c));w=view(c)['data']['workspace'];intent=w['records'][0];rows=view(c)['drafts']
    if state_name=='pending':
        r=c.post(BASE,json=dict(expected_token=w['token'],request_id=uuid.uuid4().hex,stage='script_generation',path=['title'],message='待确认新请求'));intent=r.json()['workspace']['records'][-1]
    elif state_name=='withdrawn':c.post(BASE+'/decision',json=dict(expected_token=w['token'],request_id=uuid.uuid4().hex,intent_id=intent['intent_id'],decision='withdraw',acknowledged=True))
    elif state_name=='stale':
        script=copy.deepcopy(e.get_state(P).artifacts['script_generation']);script['title']='人工正文已变化';e.update_artifact(P,'script_generation',script)
    elif state_name=='running':e._active_sessions.add(P)
    elif state_name=='applied':
        b=dict(expected_token=w['token'],intent_id=intent['intent_id'],value='人工正式版本');pv=c.post(BASE+'/applications/preview',json=b).json();r=c.post(BASE+'/applications',json=b|dict(preview_token=pv['preview_token'],request_id=uuid.uuid4().hex,acknowledged=True));assert r.status_code==201,r.text
    before=files(d);b=body(c,intent_id=intent['intent_id']);r=c.post(URL,json=b)
    assert r.status_code==(409 if state_name=='running' else 422) and files(d)==before and view(c)['drafts']==rows

def test_concurrent_updates_invalidate_old_draft_and_old_preview(setup):
    c,_,d=setup;b=body(c);pv=c.post(BASE+'/applications/preview',json={k:v for k,v in b.items() if k!='request_id'}).json()
    save(c,b);before=files(d)
    assert c.post(URL,json=b|{'request_id':uuid.uuid4().hex}).status_code==409
    assert c.post(BASE+'/applications',json=b|dict(preview_token=pv['preview_token'],request_id=uuid.uuid4().hex,acknowledged=True)).status_code==409 and files(d)==before

def test_cross_namespace_request_ids_refused(setup):
    c,_,d=setup;w=view(c)['data']['workspace'];b=body(c);before=files(d)
    for request in [w['records'][0]['request_id'],w['decisions'][0]['request_id']]:assert c.post(URL,json=b|{'request_id':request}).status_code==409
    assert files(d)==before
    saved=save(c,b);w=saved['result']['data']['workspace']
    assert c.post(BASE,json=dict(expected_token=w['token'],request_id=b['request_id'],stage='script_generation',path=['title'],message='禁止复用草稿编号')).status_code==409
    assert c.post(BASE+'/decision',json=dict(expected_token=w['token'],request_id=b['request_id'],intent_id=b['intent_id'],decision='withdraw',acknowledged=True)).status_code==409
    pv=c.post(BASE+'/applications/preview',json=dict(expected_token=w['token'],intent_id=b['intent_id'],value=b['value'])).json()
    assert c.post(BASE+'/applications',json=dict(expected_token=w['token'],intent_id=b['intent_id'],value=b['value'],preview_token=pv['preview_token'],request_id=b['request_id'],acknowledged=True)).status_code==409

def test_foreign_unknown_empty_project_and_unsafe_id(setup):
    c,_,d=setup;before=files(d)
    assert c.post(URL,json=body(c,expected_token=c.get('/api/project/manual-edit-demo/co-creation').json()['token'])).status_code==409
    assert c.post(URL,json=body(c,intent_id=uuid.uuid4().hex)).status_code==404
    assert c.get('/api/project/.unsafe/co-creation/drafts').status_code==422 and c.get('/api/project/missing/co-creation/drafts').status_code==404
    assert c.get('/api/project/revision-empty/co-creation/drafts').json()['drafts']==[] and files(d)==before

def test_atomic_failure_restores_all_memory_files_and_explicit_retry(setup,monkeypatch):
    c,e,d=setup;b=body(c);before=files(d);state=copy.deepcopy(e.get_state(P).__dict__);calls=[]
    import core.orchestrator as module
    def broken(*a):calls.append(1);raise OSError('private-document-secret')
    with monkeypatch.context() as patch:
        patch.setattr(module,'atomic_write_session',broken);r=c.post(URL,json=b);assert r.status_code==503 and 'private-document-secret' not in r.text
    assert calls==[1] and e.get_state(P).__dict__==state and files(d)==before and view(c)['drafts']==[]
    assert save(c,b)['result']['drafts'][0]['value']==b['value']

@pytest.mark.parametrize('race',['text','history','ledger'])
def test_changed_disk_between_prepare_and_save_preserved(setup,monkeypatch,race):
    c,e,d=setup;b=body(c);old=e.save_session_to_disk;state=copy.deepcopy(e.get_state(P).__dict__);p=d/'sessions'/f'{P}.json';saved=[]
    def write(*a,**kw):
        raw=json.loads(p.read_text())
        if race=='text':raw['artifacts']['script_generation']['title']='外部新正文'
        elif race=='history':raw['content_history']['versions'][-1]['created_at']='2020-01-01T00:00:00+00:00'
        else:raw['co_creation']['records'][0]['message']='外部新请求'
        p.write_text(json.dumps(raw,ensure_ascii=False));saved.append(p.read_bytes());return old(*a,**kw)
    monkeypatch.setattr(e,'save_session_to_disk',write)
    assert c.post(URL,json=b).status_code==503 and p.read_bytes()==saved[0] and e.get_state(P).__dict__==state

@pytest.mark.parametrize('damage',['duplicate','number','foreign','text_token','request_reuse','value','date','chronology','unconfirmed'])
def test_corrupt_draft_refused_after_restart_and_never_overwritten(setup,damage):
    c,_,d=setup;save(c,body(c));p=d/'sessions'/f'{P}.json';raw=json.loads(p.read_text());ledger=raw['co_creation'];draft=ledger['drafts'][0]
    if damage=='duplicate':ledger['drafts'].append(copy.deepcopy(draft))
    elif damage=='number':draft['number']=2
    elif damage=='foreign':draft['intent_id']=uuid.uuid4().hex
    elif damage=='text_token':draft['text_token']='0'*64
    elif damage=='request_reuse':draft['request_id']=ledger['records'][0]['request_id']
    elif damage=='value':draft['value']=5
    elif damage=='date':draft['created_at']='bad'
    elif damage=='chronology':draft['created_at']='2000-01-01T00:00:00+00:00'
    else:ledger['decisions']=[]
    p.write_text(json.dumps(raw,ensure_ascii=False));before=p.read_bytes()
    with pytest.raises(AssetLibraryError) as err:CoCreation(WorkflowEngine(str(d/'sessions'))).drafts(P)
    assert err.value.status==503 and p.read_bytes()==before

def test_draft_limits_fail_without_truncating(setup,monkeypatch):
    c,_,d=setup;b=body(c);before=files(d)
    import core.co_creation as module
    from core.content_revisions import encoded
    raw=json.loads((d/'sessions'/f'{P}.json').read_text())['co_creation'];monkeypatch.setattr(module,'MAX_LEDGER_BYTES',len(encoded(raw))+20)
    assert c.post(URL,json=b).status_code==422 and files(d)==before

def test_real_200_drafts_count_limit(setup):
    c,_,d=setup
    for n in range(200):save(c,body(c,value=f'第 {n+1} 次人工草稿'))
    before=files(d);assert c.post(URL,json=body(c)).status_code==422 and len(view(c)['drafts'])==200 and files(d)==before

def test_saved_draft_to_formal_version_keeps_original_draft_and_old_editor(setup):
    c,e,_=setup;saved=save(c,body(c));w=saved['result']['data']['workspace'];b=dict(expected_token=w['token'],intent_id=w['records'][0]['intent_id'],value=saved['result']['drafts'][0]['value']);pv=c.post(BASE+'/applications/preview',json=b).json()
    r=c.post(BASE+'/applications',json=b|dict(preview_token=pv['preview_token'],request_id=uuid.uuid4().hex,acknowledged=True));assert r.status_code==201
    assert view(c)['drafts']==saved['result']['drafts'] and len(r.json()['data']['revisions']['versions'])==9
    script=copy.deepcopy(e.get_state(P).artifacts['script_generation']);script['logline']='其他人工编辑';e.update_artifact(P,'script_generation',script)
    assert view(c)['drafts']==saved['result']['drafts']

def test_actual_http_contract_fixture(setup):
    c,_,_=setup;empty=view(c);request=body(c);saved=save(c,request);second=save(c,body(c,value='第二版草稿'))
    if os.environ.get('WRITE_DRAFT_FIXTURE')=='1':
        (Path(__file__).parents[2]/'frontend/tests/fixtures/co-creation-drafts.json').write_text(json.dumps(dict(empty=empty,request=request,saved=saved,second=second),ensure_ascii=False,indent=2)+'\n')
    assert second['result']==view(c)
