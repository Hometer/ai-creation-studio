import copy
import json
import pytest
from fastapi.testclient import TestClient
from api.dependencies import get_workflow_engine
from api.revision_demo import create_revision_demo
from core.content_revisions import ContentRevisions
from core.orchestrator import WorkflowEngine

URL='/api/project/revision-demo/content-revisions'
@pytest.fixture
def setup(tmp_path):
    app=create_revision_demo(tmp_path);e=app.dependency_overrides[get_workflow_engine]()
    state=e.get_state('revision-demo')
    state.artifacts['storyboard'].update(session_id='revision-demo',extra={'preserve':'metadata'})
    ep=state.artifacts['storyboard']['episodes'][0];ep['unknown']='episode metadata'
    ep['segments'][0]['shots'][0]['unknown']={'binding':'keep'}
    e.save_session_to_disk('revision-demo')
    return TestClient(app,raise_server_exceptions=False),e,tmp_path

def editor(c,stage='storyboard'):
    r=c.get(URL+'/editor/'+stage);assert r.status_code==200,r.text;return r.json()
def input_for(c,stage='storyboard'):
    data=editor(c,stage);target=copy.deepcopy(data['artifact'])
    if stage=='storyboard':
        target['episodes'][0]['segments'][0]['shots'][0]['content']='手动改写'
        target['episodes'][0]['segments'][0]['shots'][0]['duration']=7
    else: target.update(title='新的标题',mood='温暖')
    return dict(expected_token=data['workspace']['token'],stage=stage,replacement=target)
def prepare(c,p,rid='a'*32):
    r=c.post(URL+'/preview',json=p);assert r.status_code==200,r.text
    return dict(**p,preview_token=r.json()['preview_token'],request_id=rid,acknowledged=True)

@pytest.mark.parametrize('stage',['script_generation','storyboard'])
def test_manual_atomic_history_restore_and_restart(setup,stage):
    c,e,d=setup;old=copy.deepcopy(e.get_state('revision-demo').artifacts);before=(d/'sessions/revision-demo.json').read_bytes()
    p=input_for(c,stage);request=prepare(c,p)
    assert (d/'sessions/revision-demo.json').read_bytes()==before
    result=c.post(URL,json=request);assert result.status_code==201,result.text;data=result.json()
    assert data['artifact']==p['replacement']
    for key in old:
        if key!=stage:assert e.get_state('revision-demo').artifacts[key]==old[key]
    assert len(data['workspace']['versions'])==3
    fresh=ContentRevisions(WorkflowEngine(str(d/'sessions')))
    assert fresh.editor('revision-demo',stage).model_dump()==editor(c,stage)
    assert c.post(URL,json=request).json()['version_id']==data['version_id']
    assert len(editor(c,stage)['workspace']['versions'])==3
    baseline=next(v for v in data['workspace']['versions'] if v['stage']==stage and v['source']=='baseline')
    restore=dict(stage=stage,expected_token=data['workspace']['token'],restore_version_id=baseline['version_id'])
    restored=c.post(URL,json=prepare(c,restore,'b'*32))
    assert restored.status_code==201 and restored.json()['artifact']==old[stage]
    assert len(restored.json()['workspace']['versions'])==4

def test_structure_add_delete_preserves_metadata_and_union_impacts(setup):
    c,e,_=setup;p=input_for(c);segment=p['replacement']['episodes'][0]['segments'][0]
    segment['shots'].pop();segment['shots'].append({'shot_number':3,'duration':5,'content':'新增镜头'})
    data=c.post(URL,json=prepare(c,p)).json()
    assert data['artifact']['extra']=={'preserve':'metadata'}
    assert data['artifact']['episodes'][0]['unknown']=='episode metadata'
    assert segment['shots'][0]['unknown']=={'binding':'keep'}
    impact=data['workspace']['versions'][-1]['impacts']
    assert {i['shot_number'] for i in impact if i['segment_id']=='seg-1'}=={1,2,3}

@pytest.mark.parametrize('damage',[
    lambda p:p.update(path=['title'],value='ambiguous'),
    lambda p:p.update(restore_version_id='f'*32),
    lambda p:p.update(replacement=[]),
    lambda p:p['replacement'].update(session_id='foreign'),
    lambda p:p['replacement'].update(extra={}),
    lambda p:p['replacement']['episodes'][0].update(segments='bad'),
    lambda p:p['replacement']['episodes'][0]['segments'][0].update(shots=['bad']),
    lambda p:p['replacement']['episodes'][0]['segments'][0]['shots'][0].update(duration=-1),
    lambda p:p['replacement']['episodes'][0]['segments'][0]['shots'][0].update(shot_number=True),
    lambda p:p['replacement']['episodes'][0]['segments'][0]['shots'][1].update(shot_number=1),
    lambda p:p['replacement']['episodes'][0]['segments'][1].update(segment_id='seg-1'),
    lambda p:p['replacement']['episodes'][0]['segments'][0]['shots'][0].update(content={}),
    lambda p:p['replacement'].update(__proto__={}),
])
def test_invalid_manual_content_never_changes_data(setup,damage):
    c,_,d=setup;p=input_for(c);damage(p);path=d/'sessions/revision-demo.json';before=path.read_bytes()
    r=c.post(URL+'/preview',json=p);assert r.status_code==422,r.text
    assert path.read_bytes()==before

@pytest.mark.parametrize('damage',[lambda p:p['replacement'].update(characters='bad'),lambda p:p['replacement'].update(title=[]),lambda p:p['replacement'].update(episodes=[{'content':[]}])])
def test_invalid_script_shapes(setup,damage):
    c,_,_=setup;p=input_for(c,'script_generation');damage(p);assert c.post(URL+'/preview',json=p).status_code==422

def test_wrapped_legacy_episode_edit_preserves_outer_and_inner_metadata(setup):
    c,e,_=setup;s=e.get_state('revision-demo');original=copy.deepcopy(s.artifacts['storyboard'])
    s.artifacts['storyboard']={'payload':original,'outer':'keep'};e.save_session_to_disk('revision-demo')
    data=editor(c);target=copy.deepcopy(data['artifact']);target['payload']['episodes'][0]['segments'][0]['location']='渡口'
    p=dict(stage='storyboard',replacement=target,expected_token=data['workspace']['token'])
    r=c.post(URL,json=prepare(c,p));assert r.status_code==201,r.text
    assert r.json()['artifact']['outer']=='keep' and r.json()['artifact']['payload']['extra']==original['extra']

def test_manual_conflict_tamper_running_and_save_failure(setup,monkeypatch):
    c,e,d=setup;p=input_for(c);req=prepare(c,p)
    assert c.post(URL,json={**req,'replacement':{**p['replacement'],'episodes':[]}}).status_code==409
    e._active_sessions.add('revision-demo');assert c.post(URL,json=req).status_code==409;e._active_sessions.clear()
    old=copy.deepcopy(e.get_state('revision-demo').artifacts);before=(d/'sessions/revision-demo.json').read_bytes()
    def broken(*a):raise OSError('private path')
    monkeypatch.setattr('core.orchestrator.atomic_write_session',broken)
    r=c.post(URL,json=req);assert r.status_code==503 and 'private path' not in r.text
    assert e.get_state('revision-demo').artifacts==old and (d/'sessions/revision-demo.json').read_bytes()==before

def test_manual_editor_limits_and_no_provider_routes(setup,monkeypatch):
    c,e,_=setup;assert c.get(URL+'/editor/video_generation').status_code==422
    monkeypatch.setattr('core.content_revisions.MAX_CONTENT',1);assert c.get(URL+'/editor/storyboard').status_code==422
    for route in ['/execute','/intervene','/confirm']:assert c.post('/api/project/revision-demo'+route,json={}).status_code==404

@pytest.mark.parametrize('deleting',[False,True])
def test_null_field_addition_removal_is_visible_in_preview(setup,deleting):
    c,e,_=setup;s=e.get_state('revision-demo')
    if deleting:
        s.artifacts['storyboard']['episodes'][0]['nullable']=None;e.save_session_to_disk('revision-demo')
    data=editor(c);target=copy.deepcopy(data['artifact'])
    if deleting:del target['episodes'][0]['nullable']
    else:target['episodes'][0]['nullable']=None
    p=dict(stage='storyboard',expected_token=data['workspace']['token'],replacement=target)
    preview=c.post(URL+'/preview',json=p);assert preview.status_code==200,preview.text
    change=preview.json()['changes'][0]
    assert change['before_present']==deleting and change['after_present']!=deleting
    assert c.post(URL,json=prepare(c,p)).status_code==201

def test_reserved_metadata_boolean_is_not_equivalent_to_number(setup):
    c,e,_=setup;s=e.get_state('revision-demo');s.artifacts['storyboard']['extra']=1;e.save_session_to_disk('revision-demo')
    p=input_for(c);p['replacement']['extra']=True
    assert c.post(URL+'/preview',json=p).status_code==422

def test_replacement_field_limit_refuses_before_write(setup):
    c,_,d=setup;p=input_for(c,'script_generation');p['replacement']['episodes']=[{'content':'a'} for _ in range(501)]
    before=(d/'sessions/revision-demo.json').read_bytes()
    assert c.post(URL+'/preview',json=p).status_code==422
    assert (d/'sessions/revision-demo.json').read_bytes()==before

def test_other_process_legacy_content_change_cannot_be_overwritten_from_cache(setup):
    c,_,d=setup;request=prepare(c,input_for(c));path=d/'sessions/revision-demo.json'
    data=json.loads(path.read_text());data['artifacts']['storyboard']['episodes'][0]['unknown']='另一进程更新'
    path.write_text(json.dumps(data));before=path.read_bytes()
    assert c.post(URL,json=request).status_code==503 and c.get(URL+'/editor/storyboard').status_code==503
    assert path.read_bytes()==before
