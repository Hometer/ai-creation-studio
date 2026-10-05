import copy
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from api.dependencies import get_workflow_engine
from api.revision_demo import create_revision_demo
from api.schemas.content_revisions import SaveRevision
from core.content_revisions import ContentRevisions
from core.orchestrator import WorkflowEngine

PROJECT='revision-demo';URL='/api/project/revision-demo/content-revisions'
PATH=['episodes',0,'segments',0,'shots',0,'dialogue']
@pytest.fixture
def setup(tmp_path):
    app=create_revision_demo(tmp_path)
    return TestClient(app,raise_server_exceptions=False),app.dependency_overrides[get_workflow_engine](),tmp_path

def workspace(c):
    r=c.get(URL);assert r.status_code==200,r.text;return r.json()
def edit(c,stage='storyboard',path=PATH,value='渡船来了。'):
    return dict(expected_token=workspace(c)['token'],stage=stage,path=path,value=value)
def prepared(c,p=None,rid='a'*32):
    p=p or edit(c);r=c.post(URL+'/preview',json=p);assert r.status_code==200,r.text
    return dict(**p,preview_token=r.json()['preview_token'],request_id=rid,acknowledged=True)
def saved(c,p=None,rid='a'*32):
    r=c.post(URL,json=prepared(c,p,rid));assert r.status_code==201,r.text;return r.json()
def disk(d):return (d/'sessions/revision-demo.json').read_bytes()

def test_read_preview_no_writes(setup,monkeypatch):
    c,e,d=setup;before=disk(d)
    monkeypatch.setattr(e,'save_session_to_disk',lambda *a:pytest.fail('readonly'))
    assert workspace(c)['versions']==[]
    p=c.post(URL+'/preview',json=edit(c)).json()
    assert [(i['segment_id'],i['shot_number']) for i in p['impacts']]==[('seg-1',1)]
    assert disk(d)==before

def test_directed_edit_preserves_all_other_fields_and_media(setup):
    c,e,_=setup;before=copy.deepcopy(e.get_state(PROJECT).artifacts);r=saved(c)
    after=copy.deepcopy(e.get_state(PROJECT).artifacts)
    after['storyboard']['episodes'][0]['segments'][0]['shots'][0]['dialogue']=before['storyboard']['episodes'][0]['segments'][0]['shots'][0]['dialogue']
    assert before==after
    vs=r['workspace']['versions'];assert [v['source'] for v in vs]==['baseline','baseline','edit']
    assert vs[-1]['current'] and not vs[1]['current']

def test_script_conservative_impacts(setup):
    c,e,_=setup;before=copy.deepcopy(e.get_state(PROJECT).artifacts['storyboard'])
    r=saved(c,edit(c,'script_generation',['episodes',0,'content'],'新的台词'))
    assert [(i['segment_id'],i['shot_number']) for i in r['workspace']['versions'][-1]['impacts']]==[('seg-1',1),('seg-1',2),('seg-2',1)]
    assert e.get_state(PROJECT).artifacts['storyboard']==before

def test_restore_append_and_other_stage_kept(setup):
    c,e,_=setup;r=saved(c);old=r['workspace']['versions'][1]
    saved(c,edit(c,'script_generation',['title'],'新标题'),'b'*32)
    r=saved(c,dict(expected_token=workspace(c)['token'],stage='storyboard',restore_version_id=old['version_id']),'c'*32)
    assert r['workspace']['versions'][-1]['restored_from']==old['version_id']
    assert len(r['workspace']['versions'])==5
    assert e.get_state(PROJECT).artifacts['script_generation']['title']=='新标题'
    assert e.get_state(PROJECT).artifacts['storyboard']['episodes'][0]['segments'][0]['shots'][0]['dialogue']=='我会等最后一班渡船。'

def test_restart_external_update_archive(setup):
    c,e,d=setup;saved(c);before=workspace(c)
    fresh=WorkflowEngine(str(d/'sessions'));assert ContentRevisions(fresh).read(PROJECT).model_dump()==before
    fresh.update_artifact(PROJECT,'script_generation',{'title':'原页面修改'})
    assert ContentRevisions(fresh).read(PROJECT).untracked_stages==[]
    assert ContentRevisions(fresh).read(PROJECT).versions[-1].source=='external'
    c=TestClient(create_revision_demo(d));saved(c,edit(c,'script_generation',['title'],'再次修改'),'b'*32)
    assert [v['source'] for v in workspace(c)['versions']][-2:]==['external','edit']
    assert workspace(c)['untracked_stages']==[]

def test_idempotency_after_restart_and_later_write(setup):
    c,_,d=setup;p=prepared(c);first=c.post(URL,json=p).json()
    saved(c,edit(c,value='第二次'),'b'*32);c=TestClient(create_revision_demo(d));r=c.post(URL,json=p)
    assert r.status_code==201 and r.json()['version_id']==first['version_id'] and len(r.json()['workspace']['versions'])==4
    assert c.post(URL,json={**p,'value':'other'}).status_code==409

@pytest.mark.parametrize('change',[lambda p:p.update(expected_token='0'*64),lambda p:p.update(preview_token='0'*64),lambda p:p.update(value='偷偷换')])
def test_preview_cannot_be_changed(setup,change):
    c,_,d=setup;p=prepared(c);change(p);before=disk(d)
    assert c.post(URL,json=p).status_code==409 and disk(d)==before

def test_aba_restore_cannot_reuse_old_preview(setup):
    c,_,_=setup;old=prepared(c);r=saved(c,edit(c,value='中间版'),'b'*32)
    saved(c,dict(expected_token=workspace(c)['token'],stage='storyboard',restore_version_id=r['workspace']['versions'][1]['version_id']),'c'*32)
    assert c.post(URL,json=old).status_code==409

def test_two_writers_only_one_commits(setup):
    c,e,_=setup;p=prepared(c);ps=[p,{**p,'request_id':'b'*32}];service=ContentRevisions(e)
    def run(p):
        try:service.save(PROJECT,SaveRevision.model_validate(p));return 201
        except Exception as exc:return getattr(exc,'status',500)
    with ThreadPoolExecutor(2) as pool:r=list(pool.map(run,ps))
    assert sorted(r)==[201,409] and len(workspace(c)['versions'])==3

@pytest.mark.parametrize('active',[True,False])
def test_running_refused(setup,active):
    c,e,_=setup;p=edit(c)
    if active:e._active_sessions.add(PROJECT)
    else:e.get_state(PROJECT).status['video_generation']='running'
    assert workspace(c)['running'] and c.post(URL+'/preview',json=p).status_code==409

@pytest.mark.parametrize('change',[lambda p:p.update(path=['episodes',0,'segments',0,'shots',0,'duration'],value='7'),lambda p:p.update(path=['__proto__'],value='x'),lambda p:p.update(path=['episodes',False,'content']),lambda p:p.update(value='x'*10001),lambda p:p.update(restore_version_id='a'*32),lambda p:p.update(extra='private')])
def test_protocol_and_allowed_field_validation(setup,change):
    c,_,_=setup;p=edit(c);change(p);assert c.post(URL+'/preview',json=p).status_code==422

def test_no_change_or_unconfirmed(setup):
    c,_,_=setup;assert c.post(URL+'/preview',json=edit(c,value='我会等最后一班渡船。')).status_code==422
    p=prepared(c);p['acknowledged']=False;assert c.post(URL,json=p).status_code==422

def test_foreign_and_other_stage_restore(setup):
    c,_,_=setup;r=saved(c)
    for v in (r['workspace']['versions'][0]['version_id'],'f'*32):
        assert c.post(URL+'/preview',json=dict(expected_token=workspace(c)['token'],stage='storyboard',restore_version_id=v)).status_code==404

@pytest.mark.parametrize('damage',['json','history','future','identity'])
def test_damaged_store_safe_and_not_overwritten(setup,damage):
    c,_,d=setup;saved(c);path=d/'sessions/revision-demo.json';data=json.loads(path.read_text())
    if damage=='json':path.write_text('{private-internal')
    else:
        if damage=='history':data['content_history']['versions'][0]['digest']='0'*64
        if damage=='future':data['schema_version']=99
        if damage=='identity':data['session_id']='other'
        path.write_text(json.dumps(data))
    before=disk(d);r=c.get(URL);assert r.status_code==503 and 'private-internal' not in r.text and disk(d)==before

def test_failed_atomic_save_restores_memory(setup,monkeypatch):
    c,e,d=setup;p=prepared(c);before=copy.deepcopy(e.get_state(PROJECT).artifacts);old=disk(d)
    def broken(*a):raise OSError('private path')
    monkeypatch.setattr('core.orchestrator.atomic_write_session',broken)
    r=c.post(URL,json=p);assert r.status_code==503 and 'private path' not in r.text
    assert e.get_state(PROJECT).artifacts==before and getattr(e.get_state(PROJECT),'content_history',None) is None and disk(d)==old

def test_history_limit_does_not_discard(setup,monkeypatch):
    c,_,d=setup;p=prepared(c);before=disk(d);monkeypatch.setattr('core.content_revisions.MAX_HISTORY',1)
    assert c.post(URL,json=p).status_code==422 and disk(d)==before

def test_demo_no_paid_routes_and_missing_project(setup):
    c,_,_=setup;assert c.get('/api/project/missing/content-revisions').status_code==404
    for path in ('/api/project/revision-demo/execute/storyboard','/api/project/revision-demo/intervene','/api/generation-tasks','/api/config'):
        assert c.post(path,json={}).status_code==404

def test_legacy_payload_and_prompt_scope(setup):
    c,e,_=setup;s=e.get_state(PROJECT);s.artifacts['storyboard']={'payload':s.artifacts['storyboard']};e.save_session_to_disk(PROJECT)
    r=c.post(URL+'/preview',json=edit(c,path=['payload','episodes',0,'segments',0,'visual_prompt'],value='清晨渡口。'))
    assert r.status_code==200 and [(i['segment_id'],i['shot_number']) for i in r.json()['impacts']]==[('seg-1',1),('seg-1',2)]


def test_deleted_cached_project_is_not_resurrected(setup):
    c,e,d=setup;p=prepared(c);path=d/'sessions/revision-demo.json';path.unlink()
    assert c.get(URL).status_code==404 and c.post(URL,json=p).status_code==404 and not path.exists()

def test_empty_stage_and_legacy_save_refuses_damaged_revision_metadata(setup):
    c,e,d=setup
    w=c.get('/api/project/revision-empty/content-revisions').json()
    assert w['fields']=={'script_generation':[],'storyboard':[]} and w['versions']==[]
    saved(c);path=d/'sessions/revision-demo.json';data=json.loads(path.read_text());data['content_history']['schema_version']=2;path.write_text(json.dumps(data));before=disk(d)
    old_artifacts=copy.deepcopy(e.get_state(PROJECT).artifacts);old_status=copy.deepcopy(e.get_state(PROJECT).status)
    with pytest.raises(ValueError):e.update_artifact(PROJECT,'script_generation',{'title':'cannot replace history'})
    assert disk(d)==before and e.get_state(PROJECT).artifacts==old_artifacts and e.get_state(PROJECT).status==old_status


@pytest.mark.parametrize('ack',[1,'true'])
def test_acknowledgment_must_be_explicit_boolean(setup,ack):
    c,_,_=setup;p=prepared(c);p['acknowledged']=ack
    assert c.post(URL,json=p).status_code==422 and workspace(c)['versions']==[]
