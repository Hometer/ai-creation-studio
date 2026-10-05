"""Real engine persistence, artificial agents; no provider calls."""
import asyncio
import copy
import json
import pytest
from api.revision_demo import create_revision_demo
from api.dependencies import get_workflow_engine
from api.schemas.content_revisions import RevisionInput, SaveRevision
from core.asset_library import AssetLibraryError
from core.content_revisions import ContentRevisions
from core.orchestrator import WorkflowEngine, WorkflowStage

P='revision-demo'
@pytest.fixture
def setup(tmp_path):
    app=create_revision_demo(tmp_path)
    e=app.dependency_overrides[get_workflow_engine]()
    return e,tmp_path/'sessions'/f'{P}.json'
def versions(e):return ContentRevisions(e).read(P).versions

def test_patch_baselines_changes_impacts_no_duplicate_and_restart(setup):
    e,path=setup;old=copy.deepcopy(e.get_state(P).artifacts)
    e.update_artifact(P,'script_generation',{'title':'旧入口改标题'})
    vs=versions(e);assert [v.source for v in vs]==['baseline','baseline','external']
    assert vs[-1].changes[0].path==['title'] and len(vs[-1].impacts)==3
    assert e.get_state(P).artifacts['video_generation']==old['video_generation']
    e.update_artifact(P,'script_generation',{'title':'旧入口改标题'})
    assert len(versions(e))==3
    assert ContentRevisions(WorkflowEngine(str(path.parent))).read(P)==ContentRevisions(e).read(P)

def test_automatic_version_can_be_restored_without_double_archive(setup):
    e,_=setup;e.update_artifact(P,'script_generation',{'title':'第一版'})
    old=versions(e)[-1];e.update_artifact(P,'script_generation',{'title':'第二版'})
    r=ContentRevisions(e);w=r.read(P);p=RevisionInput(stage='script_generation',expected_token=w.token,restore_version_id=old.version_id)
    preview=r.preview(P,p);r.save(P,SaveRevision(**p.model_dump(),preview_token=preview.preview_token,request_id='a'*32,acknowledged=True))
    assert len(versions(e))==5 and versions(e)[-1].source=='restore'
    assert e.get_state(P).artifacts['script_generation']['title']=='第一版'

class ArtificialAgent:
    def __init__(self,payload,waiting=False):self.payload=payload;self.waiting=waiting
    def set_cancellation_check(self,c):pass
    def set_progress_callback(self,c):self.progress=c
    async def process(self,data,intervention=None):
        if hasattr(self,'progress'):self.progress('人工','仅进度',50)
        return dict(payload=copy.deepcopy(self.payload),requires_intervention=self.waiting,stage_completed=not self.waiting)

@pytest.mark.parametrize('intervention',[None,{'modified_script':{'title':'人工介入'}}])
@pytest.mark.parametrize('waiting',[False,True])
def test_model_result_and_intervention_archive_real_engine(setup,intervention,waiting):
    e,_=setup;s=e.get_state(P);payload=copy.deepcopy(s.artifacts['script_generation']);payload['title']='离线代理结果'
    e.agent_factories[WorkflowStage.SCRIPT_GENERATION]=lambda:ArtificialAgent(payload,waiting)
    asyncio.run(e.execute_stage(s,WorkflowStage.SCRIPT_GENERATION,{},intervention=intervention,progress_callback=lambda *a:None))
    assert versions(e)[-1].source=='external' and len(versions(e))==3
    assert not ContentRevisions(e).read(P).untracked_stages
    assert s.status['script_generation']==('waiting' if waiting else 'completed')

def test_cross_stage_result_is_one_atomic_archive(setup):
    e,path=setup;s=e.get_state(P);payload=copy.deepcopy(s.artifacts['script_generation'])
    payload['new_episodes']=[{'episode_number':3,'act_title':'人工续写'}];payload['episodes'].append({'episode_number':3,'content':'人工正文'})
    e.agent_factories[WorkflowStage.SCRIPT_GENERATION]=lambda:ArtificialAgent(payload)
    asyncio.run(e.execute_stage(s,WorkflowStage.SCRIPT_GENERATION,{}))
    assert [(v.stage,v.source) for v in versions(e)][-2:]==[('script_generation','external'),('storyboard','external')]
    data=json.loads(path.read_text());assert 'new_episodes' not in data['artifacts']['script_generation']
    assert data['artifacts']['storyboard']['episodes'][-1]['episode_number']==3
    assert not ContentRevisions(e).read(P).untracked_stages

@pytest.mark.parametrize('stage',['script_generation','storyboard'])
def test_terminal_result_write_failure_never_leaves_new_text(setup,monkeypatch,stage):
    e,path=setup;s=e.get_state(P);old=copy.deepcopy(s.artifacts);payload=copy.deepcopy(old[stage]);payload['title']='未成功结果'
    e.agent_factories[WorkflowStage(stage)]=lambda:ArtificialAgent(payload)
    from core.orchestrator import atomic_write_session
    def write(p,data):
        if data.get('content_history'):raise OSError('private storage')
        atomic_write_session(p,data)
    monkeypatch.setattr('core.orchestrator.atomic_write_session',write)
    with pytest.raises(OSError):asyncio.run(e.execute_stage(s,WorkflowStage(stage),{}))
    assert s.artifacts==old and s.content_history is None
    assert json.loads(path.read_text())['artifacts']==old and not versions(e)
    assert s.status[stage]=='error'

def test_patch_atomic_failure_rolls_back_history_artifacts_status(setup,monkeypatch):
    e,path=setup;s=e.get_state(P);old=copy.deepcopy(s.__dict__);data=path.read_bytes()
    monkeypatch.setattr('core.orchestrator.atomic_write_session',lambda *a:(_ for _ in ()).throw(OSError('private')))
    with pytest.raises(OSError):e.update_artifact(P,'script_generation',{'title':'保存失败'})
    assert path.read_bytes()==data and s.__dict__==old

@pytest.mark.parametrize('limit',['MAX_CONTENT','MAX_HISTORY'])
def test_archive_limits_refuse_entire_patch(setup,monkeypatch,limit):
    e,path=setup;old=copy.deepcopy(e.get_state(P).__dict__);data=path.read_bytes()
    monkeypatch.setattr('core.content_revisions.'+limit,10)
    with pytest.raises(AssetLibraryError) as exc:e.update_artifact(P,'script_generation',{'title':'新内容'})
    assert exc.value.status==422 and path.read_bytes()==data and e.get_state(P).__dict__==old

def test_projection_limit_preserves_existing_data(setup):
    e,path=setup;data=path.read_bytes();old=copy.deepcopy(e.get_state(P).artifacts)
    with pytest.raises(AssetLibraryError):e.update_artifact(P,'script_generation',{'episodes':[{'content':'多字段'}]*501})
    assert path.read_bytes()==data and e.get_state(P).artifacts==old

@pytest.mark.parametrize('damage',['json','future','history'])
def test_corrupt_data_not_overwritten(setup,damage):
    e,path=setup;e.update_artifact(P,'script_generation',{'title':'已归档'})
    data=json.loads(path.read_text())
    if damage=='json':path.write_text('{broken private')
    else:
        if damage=='future':data['schema_version']=99
        else:data['content_history']['versions'][0]['digest']='0'*64
        path.write_text(json.dumps(data))
    before=path.read_bytes()
    with pytest.raises(Exception):e.update_artifact(P,'script_generation',{'title':'不可覆盖'})
    assert path.read_bytes()==before

def test_external_history_change_conflict(setup):
    e,path=setup;other=WorkflowEngine(str(path.parent));other.update_artifact(P,'script_generation',{'title':'外部实例'})
    before=path.read_bytes()
    with pytest.raises(AssetLibraryError) as exc:e.update_artifact(P,'script_generation',{'title':'旧实例覆盖'})
    assert exc.value.status==409 and path.read_bytes()==before

def test_legacy_untracked_content_captured_before_next_patch(setup):
    e,_=setup;e.update_artifact(P,'script_generation',{'title':'有历史'})
    e.get_state(P).artifacts['script_generation']['title']='接线前未追踪';e.save_session_to_disk(P)
    assert ContentRevisions(e).read(P).untracked_stages==['script_generation']
    e.update_artifact(P,'script_generation',{'title':'本次修改'})
    vs=versions(e);assert vs[-2].fields[0].value=='接线前未追踪' and vs[-1].changes[0].before=='接线前未追踪'

def test_status_meta_other_media_writes_do_not_create_versions(setup):
    e,_=setup;e.update_session_meta(P,{'idea':'换想法'},('idea',));e.persist_session_snapshot(P)
    e.update_artifact(P,'video_generation',{'unrelated':'媒体元数据'})
    assert not versions(e)

def test_non_dict_legacy_remains_compatible(setup):
    e,path=setup;e.get_state(P).artifacts['script_generation']=['旧结构'];e.save_session_to_disk(P)
    e.update_artifact(P,'script_generation',{'title':'原接口兼容更新'})
    assert json.loads(path.read_text())['artifacts']['script_generation']=={'title':'原接口兼容更新'}
    assert not versions(e)

def test_unknown_root_metadata_and_review_records_preserved(setup):
    e,path=setup;data=json.loads(path.read_text());data['unknown']={'retained':True};path.write_text(json.dumps(data))
    e.update_artifact(P,'script_generation',{'title':'保留附加字段'})
    assert json.loads(path.read_text())['unknown']=={'retained':True}

@pytest.mark.parametrize('failed',[False,True])
def test_original_intervention_http_stream_terminal_and_archive(setup,monkeypatch,failed):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import workflow
    from api.errors import register_error_handlers
    e,path=setup;s=e.get_state(P);old=copy.deepcopy(s.artifacts)
    payload=copy.deepcopy(old['script_generation']);payload['title']='人工 HTTP 介入'
    e.agent_factories[WorkflowStage.SCRIPT_GENERATION]=lambda:ArtificialAgent(payload)
    monkeypatch.setattr(workflow,'workflow_engine',e)
    if failed:
        from core.orchestrator import atomic_write_session
        def write(p,data):
            if data.get('content_history'):raise OSError('private provider token')
            atomic_write_session(p,data)
        monkeypatch.setattr('core.orchestrator.atomic_write_session',write)
    app=FastAPI();register_error_handlers(app);app.include_router(workflow.router)
    c=TestClient(app,raise_server_exceptions=False)
    r=c.post(f'/api/project/{P}/intervene',json={'stage':'script_generation','modifications':{'modified_script':payload}})
    assert r.status_code==200 and 'private provider token' not in r.text
    events=[json.loads(line) for line in r.text.splitlines() if line]
    assert events[-1]['type']==('error' if failed else 'done')
    assert sum(event['type'] in ('done','error') for event in events)==1
    if failed:assert s.artifacts==old and not versions(e)
    else:assert len(versions(e))==3 and versions(e)[-1].changes[0].after=='人工 HTTP 介入'


def test_original_patch_http_contract_and_safe_limit_error(setup,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import workflow
    from api.errors import register_error_handlers
    e,path=setup;monkeypatch.setattr(workflow,'workflow_engine',e)
    app=FastAPI();register_error_handlers(app);app.include_router(workflow.router);c=TestClient(app,raise_server_exceptions=False)
    r=c.patch(f'/api/project/{P}/artifact/script_generation',json={'title':'原 PATCH'})
    assert r.status_code==200 and r.json()['status']=='ok' and r.json()['artifact']['title']=='原 PATCH'
    assert len(versions(e))==3
    before=path.read_bytes();monkeypatch.setattr('core.content_revisions.MAX_HISTORY',10)
    r=c.patch(f'/api/project/{P}/artifact/script_generation',json={'title':'超限'})
    assert r.status_code==422 and r.json()['error']['code']=='HISTORY_LIMIT' and path.read_bytes()==before


def test_auto_archive_keeps_review_history_and_marks_old_retain_stale(tmp_path):
    from fastapi.testclient import TestClient
    from api.impact_review_demo import create_impact_demo
    app=create_impact_demo(tmp_path);c=TestClient(app);e=app.dependency_overrides[get_workflow_engine]()
    project='impact-review-demo';url=f'/api/project/{project}/content-revisions/reviews';w=c.get(url).json()
    p=dict(expected_token=w['token'],item_id=w['items'][0]['item_id'],decision='retain',note='人工已核对',request_id='a'*32,acknowledged=True)
    assert c.post(url,json=p).status_code==201
    before=copy.deepcopy(e.get_state(project).content_reviews)
    e.update_artifact(project,'script_generation',{'title':'原入口再次变化'})
    w=c.get(url).json();assert w['items'][0]['status']=='stale' and not w['records'][0]['applicable']
    assert e.get_state(project).content_reviews==before and len(w['items'])>3 and not w['untracked_stages']

def test_untracked_external_disk_text_rejects_cached_patch(setup):
    e,path=setup;data=json.loads(path.read_text());data['artifacts']['script_generation']['title']='外部修改尚无历史';path.write_text(json.dumps(data));before=path.read_bytes()
    with pytest.raises(AssetLibraryError) as exc:e.update_artifact(P,'script_generation',{'title':'旧缓存'})
    assert exc.value.status==409 and path.read_bytes()==before


def test_external_text_changed_while_model_running_is_preserved(setup):
    e,path=setup;s=e.get_state(P);payload=copy.deepcopy(s.artifacts['script_generation']);payload['title']='不能覆盖外部文字'
    class ConcurrentAgent(ArtificialAgent):
        async def process(self,data,intervention=None):
            disk=json.loads(path.read_text());disk['artifacts']['script_generation']['title']='人工外部修改';path.write_text(json.dumps(disk))
            data.pop('_session_artifacts',None)  # Agent input mutation cannot alter the expected snapshot.
            return await super().process(data,intervention)
    e.agent_factories[WorkflowStage.SCRIPT_GENERATION]=lambda:ConcurrentAgent(payload)
    with pytest.raises(AssetLibraryError) as exc:asyncio.run(e.execute_stage(s,WorkflowStage.SCRIPT_GENERATION,{}))
    assert exc.value.status==409 and s.artifacts['script_generation']['title']=='人工外部修改'
    assert json.loads(path.read_text())['artifacts']['script_generation']['title']=='人工外部修改' and not versions(e)

def test_persisted_storyboard_progress_is_archived_and_terminal_uses_latest_snapshot(setup):
    e,path=setup;s=e.get_state(P);original=copy.deepcopy(s.artifacts['storyboard']);partial=copy.deepcopy(original)
    partial['episodes'][0]['segments'][0]['shots'][0]['dialogue']='分集保存文字';final=copy.deepcopy(partial);final['episodes'][0]['segments'][0]['shots'][0]['dialogue']='最终结果文字'
    class ProgressAgent(ArtificialAgent):
        async def process(self,data,intervention=None):
            self.progress('人工','分集持久化',50,{'assets_preview':partial,'persist':True})
            saved=json.loads(path.read_text());assert saved['content_history']['versions'][-1]['content']==partial
            self.progress('人工','相同快照',60,{'assets_preview':partial,'persist':True})
            assert len(json.loads(path.read_text())['content_history']['versions'])==3
            return await super().process(data,intervention)
    e.agent_factories[WorkflowStage.STORYBOARD]=lambda:ProgressAgent(final)
    asyncio.run(e.execute_stage(s,WorkflowStage.STORYBOARD,{},progress_callback=lambda *a:None))
    assert len(versions(e))==4 and versions(e)[-1].changes[0].before=='分集保存文字'
    assert not ContentRevisions(e).read(P).untracked_stages

@pytest.mark.parametrize('damage',['json','future','history'])
def test_disk_becomes_unreadable_during_result_commit_rolls_back_artifacts(setup,damage):
    e,path=setup;s=e.get_state(P);old=copy.deepcopy(s.artifacts);payload=copy.deepcopy(old['script_generation']);payload['title']='未提交结果'
    damaged=[]
    class DamagingAgent(ArtificialAgent):
        async def process(self,data,intervention=None):
            disk=json.loads(path.read_text())
            if damage=='json':path.write_text('{damaged private')
            else:
                if damage=='future':disk['schema_version']=99
                else:disk['content_history']={'schema_version':99,'versions':[]}
                path.write_text(json.dumps(disk))
            damaged.append(path.read_bytes())
            return await super().process(data,intervention)
    e.agent_factories[WorkflowStage.SCRIPT_GENERATION]=lambda:DamagingAgent(payload)
    with pytest.raises(Exception):asyncio.run(e.execute_stage(s,WorkflowStage.SCRIPT_GENERATION,{}))
    assert s.artifacts==old and s.content_history is None and path.read_bytes()==damaged[0]


def test_missing_disk_during_result_commit_cannot_persist_uncommitted_new_text(setup):
    e,path=setup;s=e.get_state(P);old=copy.deepcopy(s.artifacts);payload=copy.deepcopy(old['script_generation']);payload['title']='不能保存的新文字'
    class MissingFileAgent(ArtificialAgent):
        async def process(self,data,intervention=None):
            path.unlink()  # Only this test's isolated temporary session.
            return await super().process(data,intervention)
    e.agent_factories[WorkflowStage.SCRIPT_GENERATION]=lambda:MissingFileAgent(payload)
    with pytest.raises(AssetLibraryError):asyncio.run(e.execute_stage(s,WorkflowStage.SCRIPT_GENERATION,{}))
    assert s.artifacts==old and s.content_history is None
    # Existing failure recovery may recreate the session, but only with the last accepted text.
    assert json.loads(path.read_text())['artifacts']==old and not versions(e)
