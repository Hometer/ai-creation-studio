"""Real files/HTTP and explicit provider doubles; never paid model calls."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from api.co_creation_demo import create_co_creation_demo
from api.dependencies import get_workflow_engine
from api.routers.creation_candidates import router,service
from api.schemas.creation_candidates import CandidateLedger,CandidatePrepare,CandidateConfirm
from core.creation_candidates import CreationCandidates,CandidateProvider,token_estimate
from core.orchestrator import WorkflowEngine
from core.model_preflight import check_connection
from core.content_revisions import digest

P='comparison-demo';BASE=f'/api/project/{P}/co-creation';URL=BASE+'/candidates'

class FixtureProvider:
    simulation_only=True
    def __init__(self): self.calls=0;self.mode='success';self.ready=True
    def connection(self):return check_connection('artificial-fixture','https://dashscope.aliyuncs.com/api/v1')
    def initialize(self):
        if not self.ready:raise ValueError('private-initialization-detail')
        return self
    def generate(self,job,transport):
        self.calls+=1
        if self.mode=='timeout':raise TimeoutError('private-material-and-key')
        value={'value':'人工接口候选：渡船到了，我们一起点亮灯塔。','explanation':'人工契约样例，不代表真实模型理解。','preservation':'保留要求仍需人工核对。'}
        if self.mode=='invalid':value['execute']='untrusted'
        if self.mode=='unchanged':value['value']=json.loads(job.messages[1]['content'])['target']['value']
        if self.mode=='blank':value['value']=' '
        if self.mode=='oversized':value['value']='字'*10001
        return value,1

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("core.creation_candidates.time.time", lambda: 1791259200)
    app=create_co_creation_demo(tmp_path);app.include_router(router)
    engine=app.dependency_overrides[get_workflow_engine]();provider=FixtureProvider();candidate=CreationCandidates(engine,provider)
    app.dependency_overrides[service]=lambda:candidate
    return TestClient(app,raise_server_exceptions=False),engine,candidate,provider

def intent(c,project=P):
    base=f'/api/project/{project}/co-creation';w=c.get(base).json();field=w['fields']['script_generation'][0]
    result=c.post(base,json=dict(expected_token=w['token'],request_id=uuid4().hex,stage='script_generation',path=field['path'],message='把标题改得更温暖',preserve='保留渡船与灯塔')).json()
    confirmed=c.post(base+'/decision',json=dict(expected_token=result['workspace']['token'],intent_id=result['intent_id'],request_id=uuid4().hex,decision='confirm',acknowledged=True)).json()
    return dict(expected_token=confirmed['workspace']['token'],intent_id=result['intent_id'],request_id=uuid4().hex)

def prepare(c):
    payload=intent(c);r=c.post(URL,json=payload);assert r.status_code==201,r.text
    return r.json(),payload

def confirmation(job):
    return dict(plan_hash=job['plan_hash'],expected_token=job['plan']['expected_token'],request_id=uuid4().hex,acknowledged=True,material_reviewed=True)

def confirm(c,job):
    body=confirmation(job);r=c.post(URL+'/'+job['job_id']+'/confirm',json=body);assert r.status_code==200,r.text
    return body,c.get(URL+'/'+job['job_id']).json()

def test_read_prepare_no_network_and_complete_candidate_apply_restart(setup):
    c,e,s,p=setup;artifacts=copy.deepcopy(e.get_state(P).artifacts)
    assert c.get(URL).json()['occupied_fen']==0 and p.calls==0
    job,request=prepare(c)
    assert job['status']=='awaiting_confirmation' and job['reserved_fen']==0 and p.calls==0
    assert c.post(URL,json=request).json()==job
    body,done=confirm(c,job)
    assert done['status']=='succeeded' and done['simulation_only'] is True and done['actual_billed_fen'] is None
    assert e.get_state(P).artifacts==artifacts and p.calls==1
    assert c.post(URL+'/'+job['job_id']+'/confirm',json=body).status_code==200 and p.calls==1
    restored=CreationCandidates(WorkflowEngine(e._session_dir),p)
    assert restored.get(P,job['job_id']).model_dump()==done
    w=c.get(BASE+'/applications').json()['workspace'];input=dict(expected_token=w['token'],intent_id=job['plan']['intent_id'],value=done['result']['value'])
    preview=c.post(BASE+'/applications/preview',json=input).json()
    saved=c.post(BASE+'/applications',json=input|dict(preview_token=preview['preview_token'],request_id=uuid4().hex,acknowledged=True))
    assert saved.status_code==201,saved.text
    assert saved.json()['data']['revisions']['versions'][-1]['changes'][0]['after']==done['result']['value']
    assert c.get(URL+'/'+job['job_id']).json()['current'] is False

@pytest.mark.parametrize('change',[{'acknowledged':False},{'acknowledged':1},{'material_reviewed':False},{'material_reviewed':'true'},{'plan_hash':'bad'},{'request_id':'bad'},{'extra':'field'}])
def test_confirmation_strict_no_sending(setup,change):
    c,e,s,p=setup;job,_=prepare(c)
    assert c.post(URL+'/'+job['job_id']+'/confirm',json=confirmation(job)|change).status_code==422
    assert c.get(URL).json()['occupied_fen']==0 and p.calls==0

@pytest.mark.parametrize('mode',['timeout','invalid','unchanged','blank','oversized'])
def test_failed_response_checkpoint_never_resubmits(setup,mode):
    c,e,s,p=setup;p.mode=mode;job,_=prepare(c);body,done=confirm(c,job)
    assert done['status']=='manual_review' and done['result'] is None and done['reserved_fen']>0
    for _ in range(2):
        assert c.post(URL+'/'+job['job_id']+'/confirm',json=body).status_code==200
        assert c.post(URL+'/'+job['job_id']+'/advance',json={}).status_code==200
    assert p.calls==1 and 'private-material' not in json.dumps(done)

@pytest.mark.parametrize('case',['stale','running','withdrawn','missing_key','expiry','tamper'])
def test_pre_send_gates(setup,case,monkeypatch):
    c,e,s,p=setup;job,_=prepare(c);body=confirmation(job)
    if case=='stale':
        value=copy.deepcopy(e.get_state(P).artifacts['script_generation']);value['title']='另一次修改';e.update_artifact(P,'script_generation',value)
    elif case=='running':e._active_sessions.add(P)
    elif case=='withdrawn':c.post(BASE+'/decision',json=dict(expected_token=job['plan']['expected_token'],intent_id=job['plan']['intent_id'],request_id=uuid4().hex,decision='withdraw',acknowledged=True))
    elif case=='missing_key':p.ready=False
    elif case=='expiry':monkeypatch.setattr('core.creation_candidates.time.time',lambda:job['plan']['expires_at']+1)
    else:body['plan_hash']='b'*64
    r=c.post(URL+'/'+job['job_id']+'/confirm',json=body)
    assert r.status_code==409 and p.calls==0 and c.get(URL).json()['occupied_fen']==0

def test_shared_budget_concurrent_confirm_atomic_and_no_reset(setup):
    c,e,s,p=setup;job,_=prepare(c)
    # Valid synthetic expensive plans make the budget boundary reachable without fees.
    with s.transaction() as ledger:
        for n in range(5):
            j=ledger.jobs[0].model_copy(deep=True);j.job_id=uuid4().hex;j.number=n+1;j.prepare_request_id=uuid4().hex
            j.plan.estimate_fen=500;j.plan_hash=digest(j.plan.model_dump())
            if n==0:ledger.jobs=[]
            ledger.jobs.append(j)
        s.save(ledger)
    jobs=s.workspace(P).jobs
    def approve(j):
        try:return s.confirm(P,j.job_id,CandidateConfirm(**confirmation(j.model_dump())))
        except Exception:return None
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(approve,jobs))
    assert sum(r is not None for r in results)==4 and s.workspace(P).occupied_fen==2000 and p.calls==0
    restored=CreationCandidates(WorkflowEngine(e._session_dir),p)
    assert restored.workspace(P).available_fen==0
    assert c.get('/api/project/manual-edit-demo/co-creation/candidates').json()['available_fen']==0

def test_crash_after_submit_recovers_without_repeat(setup,monkeypatch):
    c,e,s,p=setup;job,_=prepare(c);s.confirm(P,job['job_id'],CandidateConfirm(**confirmation(job)))
    with s.transaction() as ledger:
        j=ledger.jobs[0];j.status='submitting';j.attempts=1;j.lease_until=1;s.save(ledger)
    restored=CreationCandidates(WorkflowEngine(e._session_dir),p)
    assert restored.get(P,job['job_id']).status=='manual_review'
    assert restored.advance(P,job['job_id']).status=='manual_review' and p.calls==0

def test_corruption_cross_project_no_leak_or_overwrite(setup):
    c,e,s,p=setup;job,_=prepare(c)
    assert c.get('/api/project/manual-edit-demo/co-creation/candidates/'+job['job_id']).status_code==404
    original=s.path.read_bytes();s.path.write_text('{broken-private-text')
    r=c.get(URL);assert r.status_code==503 and 'broken-private' not in r.text and s.path.read_text()=='{broken-private-text'
    s.path.write_bytes(original)
    assert c.get(URL).status_code==200

def test_write_failure_before_submit_and_id_reuse(setup,monkeypatch):
    c,e,s,p=setup;job,request=prepare(c);body=confirmation(job)
    assert c.post(URL,json=request|{'intent_id':uuid4().hex}).status_code==409
    original=s.path.read_bytes()
    monkeypatch.setattr('core.creation_candidates.atomic_write_session',lambda *a:(_ for _ in ()).throw(OSError('private-failure')))
    r=c.post(URL+'/'+job['job_id']+'/confirm',json=body)
    assert r.status_code==503 and 'private-failure' not in r.text and p.calls==0 and s.path.read_bytes()==original

def test_default_provider_missing_config_never_resolves(monkeypatch):
    from config import Config
    monkeypatch.setattr(Config,'DASHSCOPE_API_KEY','')
    monkeypatch.setattr('socket.getaddrinfo',lambda *a:(_ for _ in ()).throw(AssertionError('network forbidden')))
    with pytest.raises(ValueError):CandidateProvider().initialize()
    assert token_estimate(100000+512,4096)<=500

def test_succeeded_candidate_retained_if_context_changes_during_generation(setup):
    c,e,s,p=setup;job,_=prepare(c);original=p.generate
    def changed(j,t):
        result=original(j,t);value=copy.deepcopy(e.get_state(P).artifacts['script_generation']);value['title']='模型执行期间人工另改';e.update_artifact(P,'script_generation',value);return result
    p.generate=changed
    _,done=confirm(c,job)
    assert done['status']=='succeeded' and done['current'] is False and done['result']

def test_native_protocol_real_loopback_wire_no_hidden_retry(setup):
    from test_wan_http_transport import wire
    c,e,s,p=setup;job,_=prepare(c)
    with s.transaction() as ledger:private=ledger.jobs[0].model_copy(deep=True)
    content={'value':'人工协议候选','explanation':'人工协议说明','preservation':'人工保留说明'}
    payload={'output':{'choices':[{'message':{'content':json.dumps(content,ensure_ascii=False)},'finish_reason':'stop'}]},'usage':{'input_tokens':100,'output_tokens':100}}
    with wire(body=json.dumps(payload).encode()) as (transport,calls):
        result,cost=CandidateProvider().generate(private,transport)
        assert result.value=='人工协议候选' and cost==1 and len(calls)==1
        method,path,headers,raw=calls[0];request=json.loads(raw)
        assert method=='POST' and path=='/api/v1/services/aigc/text-generation/generation'
        assert request['model']=='qwen-plus-2025-12-01' and request['input']['messages']==private.messages
        assert request['parameters']['max_tokens']==4096 and request['parameters']['enable_thinking'] is False
        assert request['parameters']['response_format']=={'type':'json_object'} and 'X-DashScope-Async' not in headers

@pytest.mark.parametrize('fault',['truncated','blank','extra','usage','badjson','status','redirect'])
def test_native_wire_invalid_output_no_retry(setup,fault):
    from test_wan_http_transport import wire
    c,e,s,p=setup;prepare(c)
    with s.transaction() as ledger:private=ledger.jobs[0].model_copy(deep=True)
    result={'value':'人工协议候选','explanation':'人工说明','preservation':'人工说明'}
    payload={'output':{'choices':[{'message':{'content':json.dumps(result)},'finish_reason':'stop'}]},'usage':{'input_tokens':100,'output_tokens':100}}
    if fault=='truncated':payload['output']['choices'][0]['finish_reason']='length'
    elif fault=='blank':payload['output']['choices'][0]['message']['content']=''
    elif fault=='extra':payload['output']['choices'][0]['message']['content']=json.dumps(result|{'execute':True})
    elif fault=='usage':payload['usage']['output_tokens']=8192
    body=b'{broken' if fault=='badjson' else json.dumps(payload).encode()
    status=503 if fault=='status' else 302 if fault=='redirect' else 200
    with wire(status=status,body=body) as (transport,calls):
        with pytest.raises(Exception):CandidateProvider().generate(private,transport)
        assert len(calls)==1
