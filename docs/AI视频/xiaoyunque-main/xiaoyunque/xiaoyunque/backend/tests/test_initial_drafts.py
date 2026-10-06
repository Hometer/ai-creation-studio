"""Explicit fixtures; persistent real API/document/approval boundaries, no paid AI."""
import copy,json,time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from api.initial_draft_demo import create_initial_demo,SCRIPT
from api.schemas.initial_drafts import InitialPrepare,InitialConfirm,InitialSelect
from core.initial_drafts import InitialDrafts
from core.orchestrator import WorkflowEngine
from core.content_revisions import digest
from core.generation_plans import shot_content
P='initial-demo';URL=f'/api/project/{P}/initial-drafts'
@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr('core.initial_drafts.time.time',lambda:1791259200)
    app,s=create_initial_demo(tmp_path);return TestClient(app,raise_server_exceptions=False),s,s.provider

def prepare(c,kind='script_generation'):
    w=c.get(URL);assert w.status_code==200,w.text
    body=dict(request_id=uuid4().hex,expected_workspace=w.json()['expected_workspace'],kind=kind,prompt='请按目标形成简短候选',preserve='保留灯塔与朋友')
    r=c.post(URL,json=body);assert r.status_code==201,r.text
    return r.json(),body

def confirm_body(j):return dict(request_id=uuid4().hex,plan_hash=j['plan_hash'],acknowledged=True,material_reviewed=True)
def generate(c,kind='script_generation'):
    j,_=prepare(c,kind);body=confirm_body(j);r=c.post(URL+'/'+j['job_id']+'/confirm',json=body);assert r.status_code==200,r.text
    result=c.get(URL+'/'+j['job_id']);assert result.status_code==200,result.text
    return result.json(),body

def select_body(c,j):return dict(request_id=uuid4().hex,expected_workspace=c.get(URL).json()['expected_workspace'],result_digest=j['result_digest'],acknowledged=True)
def select(c,j):
    b=select_body(c,j);r=c.post(URL+'/'+j['job_id']+'/select',json=b);assert r.status_code==200,r.text
    return r.json(),b

def test_from_zero_two_separate_documents_and_restart(setup):
    c,s,p=setup;state=s.engine.get_state(P);before=copy.deepcopy(state.artifacts)
    job,body=prepare(c);assert p.calls==0 and c.post(URL,json=body).json()==job
    done,fee=generate(c);assert done['status']=='succeeded' and state.artifacts==before and done['simulation_only']
    selected,save=select(c,done);assert selected['selected'] and state.artifacts['script_generation']==SCRIPT and 'storyboard' not in state.artifacts
    assert c.post(URL+'/'+done['job_id']+'/select',json=save).json()==selected
    board,_=generate(c,'storyboard');assert state.artifacts.get('storyboard') is None
    chosen,_=select(c,board);assert chosen['selected'] and len(state.artifacts['storyboard']['episodes'][0]['segments'])==2
    restored=InitialDrafts(WorkflowEngine(s.engine._session_dir),p)
    assert restored.workspace(P).model_dump()==c.get(URL).json()
    assert restored.engine.get_state(P).content_history==state.content_history and p.calls==2
    versions=state.content_history['versions'];assert len(versions)==2 and {v['stage'] for v in versions}=={'script_generation','storyboard'}

@pytest.mark.parametrize('change',[{'acknowledged':False},{'acknowledged':1},{'material_reviewed':'true'},{'material_reviewed':False},{'extra':'bad'}])
def test_strict_confirmation_no_calls(setup,change):
    c,s,p=setup;j,_=prepare(c);r=c.post(URL+'/'+j['job_id']+'/confirm',json=confirm_body(j)|change)
    assert r.status_code==422 and p.calls==0 and c.get(URL).json()['occupied_fen']==0

def test_requires_saved_script_and_preserves_goal(setup):
    c,s,p=setup;w=c.get(URL).json();r=c.post(URL,json=dict(request_id=uuid4().hex,expected_workspace=w['expected_workspace'],kind='storyboard',prompt='分镜'))
    assert r.status_code==409 and p.calls==0
    d,_=generate(c);select(c,d);state=s.engine.get_state(P);state.meta['target_duration']=600;s.engine.save_session_to_disk(P)
    w=c.get(URL).json();r=c.post(URL,json=dict(request_id=uuid4().hex,expected_workspace=w['expected_workspace'],kind='storyboard',prompt='分镜'))
    assert r.status_code==422 and state.meta['target_duration']==600 and p.calls==1

@pytest.mark.parametrize('mode',['timeout','invalid','wrong_id','wrong_time'])
def test_bad_result_never_autoretries_or_modifies_document(setup,mode):
    c,s,p=setup;d,_=generate(c);select(c,d);original=copy.deepcopy(s.engine.get_state(P).artifacts);p.mode=mode
    j,fee=generate(c,'storyboard');assert j['status']=='manual_review' and j['result'] is None and j['reserved_fen']>0
    for _ in range(2):assert c.post(URL+'/'+j['job_id']+'/confirm',json=fee).status_code==200
    assert p.calls==2 and s.engine.get_state(P).artifacts==original and 'private-material' not in json.dumps(j)
    fresh=InitialDrafts(WorkflowEngine(s.engine._session_dir),p);fresh.tick();assert p.calls==2

@pytest.mark.parametrize('case',['goal','text','running','missing_key','price'])
def test_pre_send_gates(setup,case,monkeypatch):
    c,s,p=setup;j,_=prepare(c);state=s.engine.get_state(P)
    if case=='goal':state.meta['target_duration']=7;s.engine.save_session_to_disk(P)
    if case=='text':s.engine.update_artifact(P,'script_generation',SCRIPT)
    if case=='running':s.engine._active_sessions.add(P)
    if case=='missing_key':p.ready=False
    if case=='price':monkeypatch.setattr('core.initial_drafts.time.time',lambda:j['plan']['expires_at']+1)
    r=c.post(URL+'/'+j['job_id']+'/confirm',json=confirm_body(j));assert r.status_code==409 and p.calls==0

def test_shared_budget_concurrent_atomic_restart_and_old_schema(setup):
    c,s,p=setup;prepare(c)
    with s.round.transaction() as ledger:
        base=ledger.initial_jobs[0];ledger.initial_jobs=[]
        for n in range(5):
            j=base.model_copy(deep=True);j.job_id=uuid4().hex;j.prepare_request_id=uuid4().hex;j.number=n+1;j.plan.estimate_fen=500;j.plan_hash=digest(j.plan.model_dump());ledger.initial_jobs.append(j)
        s.round.save(ledger)
    def approve(j):
        try:return s.confirm(P,j.job_id,InitialConfirm(**confirm_body(j.model_dump()))).status
        except Exception:return 'blocked'
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(approve,s.workspace(P).jobs))
    assert results.count('queued')==4 and results.count('blocked')==1 and p.calls==0
    assert InitialDrafts(WorkflowEngine(s.engine._session_dir),p).workspace(P).occupied_fen==2000
    with s.round.transaction() as ledger:assert ledger.occupied()==2000

def test_selection_conflict_atomic_failure_and_idempotency(setup,monkeypatch):
    c,s,p=setup;j,_=generate(c);b=select_body(c,j);state=s.engine.get_state(P);before=copy.deepcopy(state.artifacts)
    original=s.engine.save_session_to_disk
    def fail(*args,**kwargs):raise OSError('private-save-detail')
    monkeypatch.setattr(s.engine,'save_session_to_disk',fail)
    r=c.post(URL+'/'+j['job_id']+'/select',json=b);assert r.status_code==503 and state.artifacts==before and not state.meta.get('_initial_selections')
    monkeypatch.setattr(s.engine,'save_session_to_disk',original);r=c.post(URL+'/'+j['job_id']+'/select',json=b);assert r.status_code==200,r.text
    saved=copy.deepcopy(state.artifacts);assert c.post(URL+'/'+j['job_id']+'/select',json=b|{'result_digest':'b'*64}).status_code==409 and state.artifacts==saved
    assert c.post(URL+'/'+j['job_id']+'/select',json=b|{'request_id':uuid4().hex}).status_code==409

def test_expired_submitting_manual_review_and_queue_restart(setup):
    c,s,p=setup;j,_=prepare(c);s.confirm(P,j['job_id'],InitialConfirm(**confirm_body(j)))
    restored=InitialDrafts(WorkflowEngine(s.engine._session_dir),p);restored.tick();assert p.calls==1 and restored.get(P,j['job_id']).status=='succeeded'
    j,_=prepare(c)
    with s.round.transaction() as ledger:
        job=ledger.initial_jobs[-1];job.confirm_request_id=uuid4().hex;job.confirm_digest='a'*64;job.reserved_fen=job.plan.estimate_fen;job.status='submitting';job.attempts=1;job.lease_until=0;s.round.save(ledger)
    fresh=InitialDrafts(WorkflowEngine(s.engine._session_dir),p);fresh.tick();assert fresh.get(P,j['job_id']).status=='manual_review' and p.calls==1

def test_store_damage_and_selection_damage_fail_closed(setup):
    c,s,p=setup;j,_=generate(c);select(c,j);state=s.engine.get_state(P);state.meta['_initial_selections'][0]['result_digest']='a'*64;s.engine.save_session_to_disk(P)
    assert c.get(URL).status_code==503 and p.calls==1
    s.round.path.write_text('{broken');assert c.get(URL).status_code==503 and s.round.path.read_text()=='{broken'

def test_foreign_project_job_selection_is_scoped(setup):
    c,s,p=setup;j,_=generate(c);r=c.get('/api/project/media-demo/initial-drafts/'+j['job_id']);assert r.status_code==404 and p.calls==1

def test_real_cinematic_prompt_includes_direction_without_changing_old_content():
    assert shot_content({'content':'原镜头'})=='原镜头'
    assert shot_content({'content':'画面','action':'挥手','dialogue':'你好','sound':'海浪'})=='画面\n动作：挥手\n对白：你好\n声音：海浪'
    with pytest.raises(TypeError):shot_content({'content':'原镜头','sound':{'execute':True}})

def test_native_initial_protocol_wire_is_one_call_and_bounded(setup):
    from test_wan_http_transport import wire
    from core.initial_drafts import InitialProvider
    c,s,p=setup;j,_=prepare(c)
    with s.round.transaction() as ledger:private=ledger.initial_jobs[0].model_copy(deep=True)
    payload={'output':{'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'kind':'script_generation','content':SCRIPT,'explanation':'人工说明','preservation':'人工保留'})}}]},'usage':{'input_tokens':20,'output_tokens':50}}
    with wire(body=json.dumps(payload).encode()) as (transport,calls):
        result,cost=InitialProvider().generate(private,transport)
        assert result.content.title==SCRIPT['title'] and 0<cost<=private.plan.estimate_fen and len(calls)==1
        assert json.loads(calls[0][3])['parameters']['max_tokens']==4096

def test_actual_http_queue_recovery_and_stop_restart_same_record(tmp_path):
    import socket,subprocess,sys,httpx
    from pathlib import Path
    from contextlib import contextmanager
    app,s=create_initial_demo(tmp_path);w=s.workspace(P);j=s.prepare(P,InitialPrepare(request_id=uuid4().hex,expected_workspace=w.expected_workspace,kind='script_generation',prompt='人工重启核对'))
    s.confirm(P,j.job_id,InitialConfirm(**confirm_body(j.model_dump())))
    @contextmanager
    def server():
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-m','api.initial_draft_demo','--directory',str(tmp_path),'--port',str(port)],cwd=Path(__file__).resolve().parents[1],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=2,trust_env=False) as client:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    try:
                        if client.get(URL).status_code==200:break
                    except httpx.RequestError:pass
                    time.sleep(.05)
                else:raise AssertionError('Artificial HTTP service did not start')
                yield client
        finally:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    with server() as c:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            done=c.get(URL+'/'+j.job_id).json()
            if done['status']=='succeeded':break
            time.sleep(.05)
        assert done['status']=='succeeded' and done['attempts']==1
        body=select_body(c,done);r=c.post(URL+'/'+j.job_id+'/select',json=body);assert r.status_code==200,r.text
        saved=c.get(URL).json()
    with server() as c:
        assert c.get(URL).json()==saved
        assert c.post(URL+'/'+j.job_id+'/select',json=body).json()==saved['jobs'][0]
