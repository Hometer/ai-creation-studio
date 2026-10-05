import copy
import json
import pytest
from fastapi.testclient import TestClient
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library
from api.schemas.content_revisions import RevisionInput,SaveRevision
from api.impact_review_demo import create_impact_demo
from core.content_revisions import ContentRevisions
from core.impact_reviews import ImpactReviews
from core.orchestrator import WorkflowEngine

PROJECT='impact-review-demo';URL=f'/api/project/{PROJECT}/content-revisions/reviews'
@pytest.fixture
def setup(tmp_path):
    app=create_impact_demo(tmp_path);return TestClient(app,raise_server_exceptions=False),app.dependency_overrides[get_workflow_engine](),app.dependency_overrides[get_asset_library](),tmp_path

def read(c):
    r=c.get(URL);assert r.status_code==200,r.text;return r.json()
def payload(c,decision='retain',rid='a'*32):
    w=read(c);return dict(expected_token=w['token'],item_id=w['items'][0]['item_id'],decision=decision,note='人工核对：继续沿用现有素材。',request_id=rid,acknowledged=True)
def write(c,p):
    r=c.post(URL,json=p);assert r.status_code==201,r.text;return r.json()
def modify(e,stage='storyboard'):
    revisions=ContentRevisions(e);w=revisions.read(PROJECT);field=w.fields[stage][0];p=RevisionInput(expected_token=w.token,stage=stage,path=field.path,value=field.value+' 再次人工修改')
    preview=revisions.preview(PROJECT,p);return revisions.save(PROJECT,SaveRevision(**p.model_dump(),preview_token=preview.preview_token,request_id='c'*32,acknowledged=True))

@pytest.mark.parametrize('decision',['retain','follow_up'])
def test_real_read_append_only_decision_restart_and_idempotency(setup,decision):
    c,e,lib,d=setup;path=d/f'sessions/{PROJECT}.json';before=path.read_bytes();w=read(c)
    assert len(w['items'])==3 and not w['records'] and path.read_bytes()==before
    original=copy.deepcopy(e.get_state(PROJECT).artifacts);p=payload(c,decision);data=write(c,p)
    assert data['workspace']['items'][0]['status']==('retained' if decision=='retain' else 'follow_up')
    assert e.get_state(PROJECT).artifacts==original
    restarted=ImpactReviews(WorkflowEngine(str(d/'sessions')),lib)
    assert restarted.read(PROJECT).model_dump()==read(c)
    assert write(c,p)['review_id']==data['review_id'] and len(read(c)['records'])==1
    assert c.post(URL,json={**p,'note':'change'}).status_code==409
    p2=payload(c,'follow_up','b'*32);write(c,p2);assert len(read(c)['records'])==2

@pytest.mark.parametrize('stage',['storyboard','script_generation'])
def test_content_change_invalidates_retain_and_replay_cannot_revive(setup,stage):
    c,e,_,_=setup;p=payload(c);write(c,p);modify(e,stage);w=read(c)
    assert w['items'][0]['status']=='stale' and not w['records'][0]['applicable']
    assert write(c,p)['workspace']['items'][0]['status']=='stale'
    assert c.post(URL,json={**p,'request_id':'d'*32}).status_code==409
    write(c,payload(c,rid='d'*32));assert read(c)['items'][0]['status']=='retained'

@pytest.mark.parametrize('key',['reference_generation','video_generation','video_editing'])
def test_downstream_artifact_changes_invalidate(setup,key):
    c,e,_,_=setup;write(c,payload(c));e.get_state(PROJECT).artifacts[key]={'selected':'different'};e.save_session_to_disk(PROJECT)
    assert read(c)['items'][0]['status']=='stale'

def test_removed_or_duplicate_target_cannot_be_retained(setup):
    c,e,_,_=setup;s=e.get_state(PROJECT);s.artifacts['storyboard']['episodes'][0]['segments'][0]['shots'].pop(0);e.save_session_to_disk(PROJECT)
    w=read(c);assert not w['items'][0]['target_exists'];assert c.post(URL,json=payload(c)).status_code==409
    write(c,payload(c,'follow_up'));assert read(c)['items'][0]['status']=='follow_up'

def test_same_text_restoration_still_requires_review(setup):
    c,e,_,_=setup;write(c,payload(c));saved=modify(e);r=ContentRevisions(e);w=r.read(PROJECT);baseline=next(v for v in w.versions if v.stage=='storyboard' and v.source=='baseline')
    p=RevisionInput(stage='storyboard',expected_token=w.token,restore_version_id=baseline.version_id);preview=r.preview(PROJECT,p)
    r.save(PROJECT,SaveRevision(**p.model_dump(),preview_token=preview.preview_token,request_id='f'*32,acknowledged=True))
    assert read(c)['items'][0]['status']=='stale'

@pytest.mark.parametrize('damage',[
 lambda p:p.update(acknowledged=False),lambda p:p.update(acknowledged=1),lambda p:p.update(note=' '),lambda p:p.update(note='x'*1001),
 lambda p:p.update(item_id='bad'),lambda p:p.update(request_id='../unsafe'),lambda p:p.update(decision='generate'),lambda p:p.update(extra='bad')])
def test_invalid_protocol_never_writes(setup,damage):
    c,_,_,d=setup;p=payload(c);damage(p);path=d/f'sessions/{PROJECT}.json';before=path.read_bytes()
    assert c.post(URL,json=p).status_code==422 and path.read_bytes()==before

def test_running_missing_and_atomic_failure(setup,monkeypatch):
    c,e,_,d=setup;p=payload(c);e._active_sessions.add(PROJECT);assert read(c)['running'];assert c.post(URL,json=p).status_code==409;e._active_sessions.clear()
    assert c.post(URL,json={**p,'item_id':'f'*64}).status_code==404
    assert c.get('/api/project/missing/content-revisions/reviews').status_code==404
    before=(d/f'sessions/{PROJECT}.json').read_bytes();old=copy.deepcopy(e.get_state(PROJECT).__dict__)
    def broken(*a):raise OSError('private location')
    monkeypatch.setattr('core.orchestrator.atomic_write_session',broken)
    r=c.post(URL,json=p);assert r.status_code==503 and 'private location' not in r.text
    assert (d/f'sessions/{PROJECT}.json').read_bytes()==before and e.get_state(PROJECT).__dict__==old

def test_external_disk_update_refuses_cached_overwrite(setup):
    c,_,_,d=setup;p=payload(c);path=d/f'sessions/{PROJECT}.json';data=json.loads(path.read_text());data['artifacts']['video_generation']={'new':'external'};path.write_text(json.dumps(data));before=path.read_bytes()
    assert c.get(URL).status_code==503 and c.post(URL,json=p).status_code==503 and path.read_bytes()==before

def test_corrupt_review_history_blocks_all_rewrites(setup):
    c,_,_,d=setup;write(c,payload(c));path=d/f'sessions/{PROJECT}.json';data=json.loads(path.read_text());data['content_reviews']['records'][0]['number']=3;path.write_text(json.dumps(data));before=path.read_bytes()
    assert c.get(URL).status_code==503 and c.get(f'/api/project/{PROJECT}/content-revisions').status_code==503 and path.read_bytes()==before

def test_limits_fail_without_truncation(setup,monkeypatch):
    c,_,_,d=setup;p=payload(c);before=(d/f'sessions/{PROJECT}.json').read_bytes();monkeypatch.setattr('core.impact_reviews.MAX_REVIEWS',1)
    assert c.post(URL,json=p).status_code==422 and (d/f'sessions/{PROJECT}.json').read_bytes()==before

def test_concurrent_decisions_reject_old_basis_without_erasing_history(setup):
    c,_,_,_=setup;p=payload(c);write(c,p)
    assert c.post(URL,json={**p,'decision':'follow_up','request_id':'b'*32}).status_code==409
    assert len(read(c)['records'])==1

def test_project_asset_references_and_pinned_versions_invalidate(setup):
    from api.schemas.assets import AssetInput,VersionInput,ReferenceInput
    c,_,lib,_=setup
    asset=lib.create(AssetInput(kind='character',name='人工角色',definition='人工文字资产'))
    reference=lib.create_reference(ReferenceInput(project_id=PROJECT,segment_id='seg-1',asset_id=asset.asset_id,version_id=asset.versions[0].version_id))
    write(c,payload(c));lib.add_version(asset.asset_id,VersionInput(name='人工角色第二版',definition='附加定义'))
    assert read(c)['items'][0]['status']=='stale'

def test_corrupt_plan_document_is_not_silently_ignored(setup):
    c,_,lib,_=setup;read(c);(lib.directory/'generation-plans.json').write_text('{"schema_version":true,"plans":{}}')
    assert c.get(URL).status_code==503

def test_follow_up_stays_open_when_dependencies_change(setup):
    c,e,_,_=setup;write(c,payload(c,'follow_up'));modify(e)
    w=read(c);assert w['items'][0]['status']=='follow_up' and not w['records'][0]['applicable']

def test_duplicate_target_refuses_keep_and_new_versions_keep_independent_items(setup):
    c,e,_,_=setup;modify(e,'script_generation');w=read(c);assert len(w['items'])==6 and len({i['item_id'] for i in w['items']})==6
    s=e.get_state(PROJECT);segments=s.artifacts['storyboard']['episodes'][0]['segments'];segments.append(copy.deepcopy(segments[0]));e.save_session_to_disk(PROJECT)
    assert not read(c)['items'][0]['target_exists'] and c.post(URL,json=payload(c)).status_code==409

@pytest.mark.parametrize('change',['plan','reference'])
def test_plan_or_reference_change_invalidates_saved_review(setup,change):
    from pathlib import Path
    from api.schemas.generation_plans import Plan,PlanStoreDocument,Confirmation,digest
    from api.schemas.assets import ReferenceUpdate,AssetInput,VersionInput,ReferenceInput
    c,_,lib,d=setup
    if change=='plan':
        raw=json.loads((Path(__file__).resolve().parents[2]/'frontend/tests/fixtures/readiness.json').read_text())['plan']
        raw={k:v for k,v in raw.items() if k in Plan.model_fields};raw['content']['project_id']=PROJECT
        for target in raw['content']['targets']:
            for binding in target['bindings']:binding['target']['project_id']=PROJECT
        raw['content_hash']=digest(raw['content']);raw['confirmation']=None
        plan=Plan.model_validate(raw);document=PlanStoreDocument(plans={plan.plan_id:plan})
        lib.directory.mkdir(exist_ok=True);path=lib.directory/'generation-plans.json';path.write_text(document.model_dump_json())
        write(c,payload(c));document.plans[plan.plan_id].confirmation=Confirmation(confirmed_at='2026-10-05T00:00:00+00:00',content_hash=plan.content_hash)
        document=PlanStoreDocument.model_validate(document.model_dump());path.write_text(document.model_dump_json())
    else:
        asset=lib.create(AssetInput(kind='character',name='人工角色',definition='文字'))
        ref=lib.create_reference(ReferenceInput(project_id=PROJECT,asset_id=asset.asset_id,version_id=asset.versions[0].version_id))
        updated=lib.add_version(asset.asset_id,VersionInput(name='下一版',definition='新的文字'))
        write(c,payload(c));lib.update_reference(ref.reference_id,ReferenceUpdate(expected_revision=ref.revision,version_id=updated.versions[-1].version_id))
    assert read(c)['items'][0]['status']=='stale'
