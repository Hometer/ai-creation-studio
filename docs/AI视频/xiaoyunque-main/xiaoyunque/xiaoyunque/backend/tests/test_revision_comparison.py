"""Historical snapshot comparison through real local HTTP, never provider calls."""
import copy
import json
import pytest
from fastapi.testclient import TestClient
from api.revision_demo import create_revision_demo
from api.dependencies import get_workflow_engine
from core.orchestrator import WorkflowEngine
from core.content_revisions import ContentRevisions

P='revision-demo';URL=f'/api/project/{P}/content-revisions'
@pytest.fixture
def setup(tmp_path):
    app=create_revision_demo(tmp_path);e=app.dependency_overrides[get_workflow_engine]()
    e.update_artifact(P,'script_generation',{'title':'第一轮标题'})
    e.update_artifact(P,'script_generation',{'logline':'隔了一轮的新梗概'})
    return TestClient(app,raise_server_exceptions=False),e,tmp_path

def workspace(c):
    r=c.get(URL);assert r.status_code==200,r.text;return r.json()
def params(c,before=None,after=None):
    w=workspace(c);return dict(expected_token=w['token'],before_version_id=before or w['versions'][0]['version_id'],after_version_id=after or w['versions'][-1]['version_id'])
def compare(c,p=None):
    r=c.get(URL+'/compare',params=p or params(c));assert r.status_code==200,r.text;return r.json()
def file(d):return d/f'sessions/{P}.json'
def replace(e,stage,content):
    s=e.get_state(P);old=copy.deepcopy(s.artifacts);s.artifacts[stage]=copy.deepcopy(content)
    e.save_session_to_disk(P,archive_text=True,expected_text=old)

def test_non_adjacent_readonly_and_reverse_binding(setup,monkeypatch):
    c,e,d=setup;before=file(d).read_bytes();state=copy.deepcopy(e.get_state(P).__dict__)
    monkeypatch.setattr(e,'save_session_to_disk',lambda *a,**k:pytest.fail('comparison wrote'))
    p=params(c);data=compare(c,p)
    assert [ch['path'] for ch in data['changes']]==[['logline'],['title']] and len(data['impacts'])==3
    assert data['before_version_id']==p['before_version_id'] and data['after_version_id']==p['after_version_id']
    reverse=compare(c,{**p,'before_version_id':p['after_version_id'],'after_version_id':p['before_version_id']})
    for original,back in zip(data['changes'],reverse['changes']):assert original['before']==back['after'] and original['after']==back['before']
    assert file(d).read_bytes()==before and e.get_state(P).__dict__==state

def test_same_version_and_same_content_distinct_history(setup):
    c,e,_=setup;p=params(c);same=compare(c,{**p,'after_version_id':p['before_version_id']});assert same['changes']==same['impacts']==[]
    original=copy.deepcopy(e.get_state(P).content_history['versions'][0]['content']);replace(e,'script_generation',original)
    data=compare(c);assert data['before_version_id']!=data['after_version_id'] and data['changes']==data['impacts']==[]

@pytest.mark.parametrize('left,right,types',[
 ('1',1,('text','number')),('null',None,('text','null')),('true',True,('text','boolean')),
 (None,{},('null','object')),([1],{'value':1},('array','object'))])
def test_type_changes_are_not_erased_by_identical_rendered_values(setup,left,right,types):
    c,e,_=setup;s=e.get_state(P);content=copy.deepcopy(s.artifacts['script_generation']);content['extra']=left;replace(e,'script_generation',content)
    before=workspace(c)['versions'][-1]['version_id'];content['extra']=right;replace(e,'script_generation',content)
    data=compare(c,params(c,before=before));change=data['changes'][0]
    assert change['path']==['extra'] and (change['before_type'],change['after_type'])==types
    assert change['before_present'] and change['after_present']

def test_missing_null_empty_and_deep_metadata(setup):
    c,e,_=setup;content=copy.deepcopy(e.get_state(P).artifacts['script_generation']);content.update(extra=None,empty='')
    deep=content
    for _ in range(12):deep['nested']={};deep=deep['nested']
    deep['value']='旧值';replace(e,'script_generation',content);before=workspace(c)['versions'][-1]['version_id']
    del content['extra'];content['added']=None;deep['value']='新值';replace(e,'script_generation',content)
    changes=compare(c,params(c,before=before))['changes'];by_path={tuple(ch['path']):ch for ch in changes}
    removed=by_path[('extra',)];assert removed['before_type']=='null' and removed['after_type']=='missing' and not removed['after_present']
    added=by_path[('added',)];assert added['before_type']=='missing' and added['after_type']=='null' and not added['before_present']
    assert not any(ch['path']==['empty'] for ch in changes)
    assert any(len(ch['path'])>10 for ch in changes)

def test_storyboard_removed_and_added_targets_both_in_impacts(setup):
    c,e,_=setup;s=e.get_state(P);old=workspace(c)['versions'][1]['version_id'];board=copy.deepcopy(s.artifacts['storyboard'])
    board['episodes'][0]['segments'][0]={'segment_id':'new-segment','shots':[{'shot_number':1,'dialogue':'新的镜头'}]}
    replace(e,'storyboard',board);data=compare(c,params(c,before=old))
    assert data['stage']=='storyboard' and {(i['segment_id'],i['shot_number']) for i in data['impacts']}=={('seg-1',1),('seg-1',2),('new-segment',1)}

def test_current_storyboard_context_is_guarded_but_no_writes(setup):
    c,e,d=setup;p=params(c);e.update_artifact(P,'storyboard',{'note':'当前分镜依据变化'})
    before=file(d).read_bytes();r=c.get(URL+'/compare',params=p)
    assert r.status_code==409 and r.json()['error']['code']=='REVISION_CHANGED' and file(d).read_bytes()==before

@pytest.mark.parametrize('bad',['cross_stage','foreign','missing','no_token','bad_id','bad_token'])
def test_project_stage_and_query_boundaries(setup,bad):
    c,e,d=setup;p=params(c);w=workspace(c)
    if bad=='cross_stage':p['after_version_id']=w['versions'][1]['version_id'];status=422
    elif bad=='foreign':
        e.update_artifact('revision-empty','script_generation',{'title':'另一项目'})
        p['after_version_id']=ContentRevisions(e).read('revision-empty').versions[0].version_id;status=404
    elif bad=='missing':p['after_version_id']='f'*32;status=404
    elif bad=='no_token':del p['expected_token'];status=422
    elif bad=='bad_id':p['before_version_id']='../private';status=422
    else:p['expected_token']='private';status=422
    before=file(d).read_bytes();r=c.get(URL+'/compare',params=p);assert r.status_code==status and 'private' not in r.text and file(d).read_bytes()==before

@pytest.mark.parametrize('damage',['json','history','future','external'])
def test_unreadable_or_external_change_refuses_cached_comparison(setup,damage):
    c,e,d=setup;p=params(c);data=json.loads(file(d).read_text())
    if damage=='json':file(d).write_text('{private path')
    else:
        if damage=='history':data['content_history']['versions'][0]['digest']='0'*64
        if damage=='future':data['schema_version']=99
        if damage=='external':data['artifacts']['script_generation']['title']='外部改写'
        file(d).write_text(json.dumps(data))
    before=file(d).read_bytes();r=c.get(URL+'/compare',params=p);assert r.status_code==503 and 'private path' not in r.text and file(d).read_bytes()==before

@pytest.mark.parametrize('limit',['MAX_CONTENT','MAX_COMPARISON_CHANGES','MAX_COMPARISON_RESPONSE'])
def test_limits_are_explicit_and_do_not_truncate_or_write(setup,monkeypatch,limit):
    c,_,d=setup;p=params(c);before=file(d).read_bytes();monkeypatch.setattr('core.content_revisions.'+limit,1)
    r=c.get(URL+'/compare',params=p);assert r.status_code==422 and r.json()['error']['code'] in ('CONTENT_LIMIT','COMPARISON_LIMIT') and file(d).read_bytes()==before

def test_restart_and_running_comparison_remain_readonly(setup):
    c,e,d=setup;p=params(c);expected=compare(c,p);fresh=WorkflowEngine(str(d/'sessions'))
    from api.schemas.content_revisions import ComparisonInput
    fresh._active_sessions.add(P);fresh.get_state(P).status['script_generation']='running'
    before=file(d).read_bytes();assert ContentRevisions(fresh).compare(P,ComparisonInput(**p)).model_dump()==expected and file(d).read_bytes()==before
