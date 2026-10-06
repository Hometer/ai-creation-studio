"""Project naming/target persistence, compatibility and lost-write recovery."""
import copy
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.dependencies import get_workflow_engine
from api.routers.project_goals import router
from api.errors import register_error_handlers
from api.schemas.project_goals import GoalInput
from api.schemas.project import ProjectStartRequest
from core.orchestrator import WorkflowEngine
from core.project_goals import ProjectGoals
from core.asset_library import AssetLibraryError
P='goal-demo';U='/api/project/'+P+'/goal'
@pytest.fixture
def setup(tmp_path):
    engine=WorkflowEngine(tmp_path);engine.create_session(P,{'idea':'人工故事','style':'realistic','video_ratio':'9:16'})
    app=FastAPI();register_error_handlers(app);app.include_router(router);app.dependency_overrides[get_workflow_engine]=lambda:engine
    return TestClient(app,raise_server_exceptions=False),engine,ProjectGoals(engine)
def payload(c,**changes):return {'project_name':'人工作品','target_duration':45,'request_id':uuid4().hex,'expected_token':c.get(U).json()['expected_token']}|changes

def test_goal_write_isolated_from_artifacts_and_restart(setup):
    c,e,s=setup;before=copy.deepcopy(e.get_status_snapshot(P)['artifacts']);w=c.get(U).json();assert w['project_name'] is None and w['target_duration'] is None
    body=payload(c);r=c.post(U,json=body);assert r.status_code==200,r.text
    assert r.json()['project_name']=='人工作品' and r.json()['target_duration']==45
    assert e.get_status_snapshot(P)['artifacts']==before
    assert e.list_saved_sessions()[0]['title']=='人工作品'
    again=ProjectGoals(WorkflowEngine(e._session_dir)).read(P);assert again.model_dump()==r.json()
    assert c.post(U,json=body).json()==r.json() and len(e.get_state(P).meta['_goal_records'])==1

def test_goal_old_replay_never_replaces_new_name(setup):
    c,e,s=setup;first=payload(c);assert c.post(U,json=first).status_code==200
    second=payload(c,project_name='新名称');r=c.post(U,json=second);assert r.status_code==200
    assert c.post(U,json=first).json()['project_name']=='新名称'
    assert c.post(U,json=first|{'project_name':'另一名称'}).status_code==409

def test_conflict_running_and_foreign_project_refused(setup):
    c,e,s=setup;body=payload(c);e.update_session_meta(P,{'style':'anime'},('style',))
    assert c.post(U,json=body).status_code==409
    body=payload(c);e._active_sessions.add(P);assert c.post(U,json=body).status_code==409;e._active_sessions.remove(P)
    assert c.post('/api/project/foreign/goal',json=body).status_code==404
    assert e.get_state(P).meta.get('project_name') is None

@pytest.mark.parametrize('change',[{'project_name':' '},{'project_name':'字'*101},{'project_name':1},{'target_duration':True},{'target_duration':'45'},{'target_duration':0},{'target_duration':601},{'extra':True}])
def test_strict_goal_contract(setup,change):
    c,e,s=setup;assert c.post(U,json=payload(c,**change)).status_code==422
    assert e.get_state(P).meta.get('project_name') is None

def test_failed_atomic_goal_save_rolls_back_and_no_private_error(setup,monkeypatch):
    c,e,s=setup;before=copy.deepcopy(e.get_status_snapshot(P));body=payload(c)
    with monkeypatch.context() as m:
        m.setattr(e,'save_session_to_disk',lambda *a:(_ for _ in ()).throw(OSError('private-artificial-detail')))
        r=c.post(U,json=body);assert r.status_code==503 and 'private-artificial-detail' not in r.text
    assert e.get_status_snapshot(P)==before and c.post(U,json=body).status_code==200

def test_corrupted_goal_history_is_read_only_and_does_not_overwrite(setup):
    c,e,s=setup;e.get_state(P).meta['_goal_records']=[{'request_id':'broken'}]
    before=copy.deepcopy(e.get_state(P).meta)
    assert c.get(U).status_code==503 and e.get_state(P).meta==before

@pytest.mark.parametrize('values',[{}, {'project_name':'名字','target_duration':60}])
def test_optional_creation_fields_compatible(values):
    request=ProjectStartRequest(idea='故事',**values);assert request.project_name==values.get('project_name')

@pytest.mark.parametrize('values',[{'project_name':' '},{'project_name':1},{'target_duration':'60'},{'target_duration':True},{'target_duration':601}])
def test_creation_new_fields_have_strict_bounds(values):
    with pytest.raises(ValueError):ProjectStartRequest(idea='故事',**values)

def test_real_creation_route_saves_named_goal_without_execution(setup,monkeypatch):
    c,e,s=setup
    from api.routers import workflow
    monkeypatch.setattr(workflow,'workflow_engine',e);c.app.include_router(workflow.router)
    body={'idea':'从零创作人工故事','project_name':'独立作品名称','target_duration':60,'style':'anime','video_ratio':'16:9','llm_model':'fixture','vlm_model':'fixture','image_t2i_model':'fixture','image_it2i_model':'fixture','video_model':'fixture'}
    r=c.post('/api/project/start',json=body);assert r.status_code==200,r.text
    project=r.json()['session_id'];assert r.json()['params']['target_duration']==60
    goal=c.get('/api/project/'+project+'/goal').json();assert goal['project_name']=='独立作品名称' and goal['target_duration']==60 and goal['style']=='anime' and goal['video_ratio']=='16:9'
    restored=WorkflowEngine(e._session_dir);assert restored.get_status_snapshot(project)['artifacts']=={}
    assert next(item for item in restored.list_saved_sessions() if item['id']==project)['title']=='独立作品名称'

def test_lifespan_worker_uses_route_dependency_stores(setup, monkeypatch, tmp_path):
    """An isolated HTTP server must not recover jobs in the main user's store."""
    from api.app import app
    from api.routers.assets import get_asset_library, get_media_archive
    from core.asset_library import AssetLibrary
    from core.media_archive import MediaArchive
    from core.release_media_worker import MediaWorker
    _, engine, _ = setup
    library = AssetLibrary(tmp_path / 'isolated-library')
    archive = MediaArchive(tmp_path / 'isolated-library', tmp_path / 'results', tmp_path)
    captured = []
    monkeypatch.setattr(MediaWorker, 'start', lambda worker: captured.append(worker.media))
    monkeypatch.setattr(MediaWorker, 'stop', lambda worker: None)
    previous = dict(app.dependency_overrides)
    try:
        app.dependency_overrides.update({get_workflow_engine: lambda: engine,
            get_asset_library: lambda: library, get_media_archive: lambda: archive})
        with TestClient(app):
            assert len(captured) == 1
            service = captured[0]
            assert service.engine is engine and service.library is library and service.archive is archive
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
