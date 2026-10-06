"""Persistent read summaries never submit, select, render or expose documents."""
import copy,json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from api.initial_draft_demo import create_initial_demo
from api.composition_demo import create_composition_demo
from api.schemas.initial_drafts import InitialPrepare,InitialConfirm
from core.release_tasks import ReleaseTasksReader
from core.orchestrator import WorkflowEngine
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library,get_media_archive
from test_compositions import prepared,confirm,U,P

def test_initial_summary_waiting_success_unknown_billing_and_read_only_restart(tmp_path):
    a,s=create_initial_demo(tmp_path);c=TestClient(a,raise_server_exceptions=False);w=s.workspace('initial-demo')
    j=s.prepare('initial-demo',InitialPrepare(request_id=uuid4().hex,expected_workspace=w.expected_workspace,kind='script_generation',prompt='private-complete-document'))
    path=s.round.path;before=path.read_bytes();r=c.get('/api/first-release-tasks');assert r.status_code==200,r.text
    row=r.json()['tasks'][0];assert row['status']=='awaiting_confirmation' and row['estimate_fen']>0 and row['reserved_fen']==0 and row['actual_billed_fen'] is None
    assert row['href'].endswith('?job='+j.job_id) and row['simulation_only'] and s.provider.calls==0 and path.read_bytes()==before
    assert 'private-complete-document' not in r.text and '人工样例：两位朋友' not in r.text
    s.confirm('initial-demo',j.job_id,InitialConfirm(request_id=uuid4().hex,plan_hash=j.plan_hash,acknowledged=True,material_reviewed=True));s.advance('initial-demo',j.job_id)
    before=path.read_bytes();result=c.get('/api/first-release-tasks').json();assert result['tasks'][0]['status']=='succeeded' and s.provider.calls==1 and path.read_bytes()==before
    library=a.dependency_overrides[get_asset_library]();archive=a.dependency_overrides[get_media_archive]()
    fresh=ReleaseTasksReader(WorkflowEngine(s.engine._session_dir),library,archive);assert fresh.read().model_dump()==result
    assert not s.engine.get_state('initial-demo').artifacts

def test_composition_summary_free_scoped_download_and_no_rerender(tmp_path):
    a,s=create_composition_demo(tmp_path);c=TestClient(a,raise_server_exceptions=False);_,j=prepared(c);confirm(c,j)
    before=s.path.read_bytes();r=c.get('/api/first-release-tasks');assert r.status_code==200,r.text
    row=r.json()['tasks'][0];assert row['kind']=='composition' and row['status']=='queued' and not row['model_call'] and row['estimate_fen']==row['reserved_fen']==0 and s.path.read_bytes()==before
    assert s.advance(P,j['job_id']).status=='succeeded';before=s.path.read_bytes();r=c.get('/api/first-release-tasks').json();row=r['tasks'][0]
    assert row['result_url']==U+'/'+j['job_id']+'/media' and c.get(row['result_url']).headers['content-type']=='video/mp4' and s.path.read_bytes()==before

def test_missing_project_and_corrupt_store_are_not_false_empty(tmp_path):
    a,s=create_initial_demo(tmp_path);c=TestClient(a,raise_server_exceptions=False);w=s.workspace('initial-demo');s.prepare('initial-demo',InitialPrepare(request_id=uuid4().hex,expected_workspace=w.expected_workspace,kind='script_generation',prompt='fixture'))
    s.round.path.write_text('{bad');r=c.get('/api/first-release-tasks');assert r.status_code==503 and r.json()['error']['code']=='CANDIDATE_STORE_UNAVAILABLE' and s.round.path.read_text()=='{bad'

def test_summary_does_not_recover_unknown_checkpoint_or_mutate_store(tmp_path):
    a,s=create_initial_demo(tmp_path);c=TestClient(a,raise_server_exceptions=False);w=s.workspace('initial-demo');j=s.prepare('initial-demo',InitialPrepare(request_id=uuid4().hex,expected_workspace=w.expected_workspace,kind='script_generation',prompt='fixture'))
    s.confirm('initial-demo',j.job_id,InitialConfirm(request_id=uuid4().hex,plan_hash=j.plan_hash,acknowledged=True,material_reviewed=True))
    with s.round.transaction() as ledger:
        job=ledger.initial_jobs[0];job.status='submitting';job.attempts=1;job.lease_until=0;s.round.save(ledger)
    before=s.round.path.read_bytes();r=c.get('/api/first-release-tasks');assert r.status_code==200 and r.json()['tasks'][0]['status']=='submitting' and s.provider.calls==0 and s.round.path.read_bytes()==before


def test_missing_project_is_explicit_and_no_foreign_document_is_loaded(tmp_path):
    a,s=create_initial_demo(tmp_path);c=TestClient(a);w=s.workspace('initial-demo');s.prepare('initial-demo',InitialPrepare(request_id=uuid4().hex,expected_workspace=w.expected_workspace,kind='script_generation',prompt='fixture'))
    # Move only this test-owned session; retain its task record and confirm visibility.
    from pathlib import Path
    path=Path(s.engine._session_dir)/'initial-demo.json';path.rename(path.with_suffix('.test-backup'))
    s.engine.sessions.pop('initial-demo')
    r=c.get('/api/first-release-tasks');assert r.status_code==200 and r.json()['tasks'][0]['project_exists'] is False and s.provider.calls==0


def test_all_three_native_candidate_sources_have_scoped_links_and_external_id(tmp_path,monkeypatch):
    from api.release_media_demo import create_release_demo
    from test_release_media import prepare,confirm
    from core.co_creation import CoCreation
    from core.creation_candidates import CreationCandidates
    from api.creation_candidate_demo import ArtificialCandidateProvider
    from api.schemas.co_creation import RequestInput,DecisionInput
    from api.schemas.creation_candidates import CandidatePrepare
    clock=[1791259200];monkeypatch.setattr('core.release_media.time.time',lambda:clock[0])
    a,s=create_release_demo(tmp_path);c=TestClient(a);image,_=prepare(c,s);video,_=prepare(c,s,'video');done,_=confirm(c,video)
    s.engine.update_artifact('media-demo','script_generation',{'title':'人工剧本'})
    co=CoCreation(s.engine);w=co.read('media-demo');r=co.save('media-demo',RequestInput(expected_token=w.token,request_id=uuid4().hex,stage='script_generation',path=['title'],message='标题改温暖',preserve='灯塔'))
    co.save('media-demo',DecisionInput(expected_token=r.workspace.token,intent_id=r.intent_id,request_id=uuid4().hex,decision='confirm',acknowledged=True))
    w=co.read('media-demo');candidates=CreationCandidates(s.engine,ArtificialCandidateProvider());candidate=candidates.prepare('media-demo',CandidatePrepare(expected_token=w.token,intent_id=r.intent_id,request_id=uuid4().hex))
    result=c.get('/api/first-release-tasks');assert result.status_code==200,result.text
    rows=result.json()['tasks'];assert {r['kind'] for r in rows}=={'creation','image','video'}
    v=next(r for r in rows if r['kind']=='video');assert v['provider_task_id']==done['provider_task_id'] and v['model_call'] and v['actual_billed_fen'] is None
    text=next(r for r in rows if r['kind']=='creation');assert text['href'].endswith('intent='+r.intent_id+'&candidate='+candidate.job_id) and text['status']=='awaiting_confirmation'
