"""Real HTTP/files, artificial provider outcomes and durable cost checkpoints."""
import copy
import json
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from api.release_media_demo import create_release_demo
from api.schemas.release_media import MediaPrepare,MediaConfirm,MediaSelect
from api.schemas.creation_candidates import CandidateLedger
from api.schemas.assets import ReferenceUpdate,UploadSource,VersionInput
from core.release_media import ReleaseMedia
from core.asset_library import AssetLibraryError
from core.release_media_provider import ReleaseMediaProvider
from core.orchestrator import WorkflowEngine
from core.generation_plans import GenerationPlans
from core.creation_candidates import CreationCandidates,PRICE_EXPIRES
from core.content_revisions import digest
from test_wan_http_transport import wire
P='media-demo';URL='/api/project/'+P+'/media-revisions'

@pytest.fixture
def setup(tmp_path,monkeypatch):
    clock=[1791259200];monkeypatch.setattr('core.release_media.time.time',lambda:clock[0])
    app,s=create_release_demo(tmp_path);return TestClient(app,raise_server_exceptions=False),s,clock

def payload(c,s,kind='image',**values):
    w=c.get(URL).json()
    source=w['images'][0]['reference']['reference_id'] if kind=='image' else s.plans.list(P)[0].plan_id
    return {'kind':kind,'source_id':source,'segment_id':'seg-1','expected_workspace':w['expected_workspace'],'prompt':'将黄色改成蓝色','preserve':'保持方形画面','request_id':uuid4().hex}|values

def prepare(c,s,kind='image',**values):
    body=payload(c,s,kind,**values);r=c.post(URL,json=body);assert r.status_code==201,r.text;return r.json(),body

def approval(job):return dict(plan_hash=job['plan_hash'],request_id=uuid4().hex,acknowledged=True,material_reviewed=True)

def confirm(c,job):
    body=approval(job);r=c.post(URL+'/'+job['job_id']+'/confirm',json=body);assert r.status_code==200,r.text
    return c.get(URL+'/'+job['job_id']).json(),body

def finish(c,s,clock,job):
    job,_=confirm(c,job)
    clock[0]+=6;r=c.post(URL+'/'+job['job_id']+'/advance',json={});assert r.status_code==200,r.text
    r=c.post(URL+'/'+job['job_id']+'/advance',json={});assert r.status_code==200,r.text
    assert r.json()['status']=='succeeded',r.text;return r.json()

def test_image_candidate_old_versions_unchanged_until_select_and_restart(setup):
    c,s,clock=setup;before=s.engine.get_status_snapshot(P)['artifacts'];ref=s.workspace(P).images[0].reference;job,_=prepare(c,s);done=finish(c,s,clock,job)
    assert done['simulation_only'] and done['actual_billed_fen'] is None
    data=s.library.read();assert data.references[ref.reference_id]==ref
    asset=data.assets[done['asset_id']];assert len(asset.versions)==2 and asset.versions[0].version_id==ref.version_id
    assert asset.versions[1].source.type=='media_task' and asset.versions[1].source.simulation_only
    select=dict(expected_selection=done['selection_token'],request_id=uuid4().hex,acknowledged=True)
    selected=c.post(URL+'/'+done['job_id']+'/select',json=select);assert selected.status_code==200,selected.text
    assert selected.json()['selected'] and not selected.json()['current']
    assert c.post(URL+'/'+done['job_id']+'/select',json=select).status_code==200
    assert s.engine.get_status_snapshot(P)['artifacts']==before
    assert s.library.read().references[ref.reference_id].revision==2
    restored_engine=WorkflowEngine(s.engine._session_dir);restored=ReleaseMedia(restored_engine,s.library,s.archive,GenerationPlans(restored_engine,s.library,s.archive),s.provider)
    assert restored.get(P,done['job_id']).selected and restored.workspace(P).occupied_fen==14

def test_video_candidate_selection_and_platform_id_survive_restart(setup):
    c,s,clock=setup;before=copy.deepcopy(s.engine.get_status_snapshot(P)['artifacts']);job,_=prepare(c,s,'video');pending,body=confirm(c,job)
    assert pending['provider_task_id'] and pending['submit_attempts']==1
    restored=ReleaseMedia(WorkflowEngine(s.engine._session_dir),s.library,s.archive,s.plans,s.provider)
    clock[0]+=6;restored.tick();restored.tick();done=restored.get(P,job['job_id']);assert done.status=='succeeded'
    selected=restored.select(P,job['job_id'],MediaSelect(expected_selection=done.selection_token,request_id=uuid4().hex,acknowledged=True));assert selected.selected
    assert s.engine.get_status_snapshot(P)['artifacts']==before
    assert len(json.loads((s.provider.directory/'artificial-provider.json').read_text())['jobs'])==1
    assert c.post(URL+'/'+job['job_id']+'/confirm',json=body).status_code==200

@pytest.mark.parametrize('mode',['lost-submit','query-timeout','unknown','bad-media','failed'])
def test_unknown_or_bad_media_never_resubmits(setup,mode):
    c,s,clock=setup;s.provider.mode=mode;job,_=prepare(c,s);pending,body=confirm(c,job)
    for n in range(8):clock[0]+=6;s.advance(P,job['job_id'])
    done=s.get(P,job['job_id']);assert done.status in {'manual_review','failed'} and done.asset_id is None
    for n in range(2):assert c.post(URL+'/'+job['job_id']+'/confirm',json=body).status_code==200
    assert len(json.loads((s.provider.directory/'artificial-provider.json').read_text())['jobs'])==1
    if mode=='lost-submit':assert done.provider_task_id is None and c.post(URL+'/'+job['job_id']+'/resume',json={'acknowledged':True}).status_code==409

@pytest.mark.parametrize('change',[{'acknowledged':False},{'acknowledged':1},{'material_reviewed':False},{'material_reviewed':'true'},{'extra':1}])
def test_confirmation_requires_explicit_exact_contract(setup,change):
    c,s,clock=setup;job,_=prepare(c,s);assert c.post(URL+'/'+job['job_id']+'/confirm',json=approval(job)|change).status_code==422
    assert s.workspace(P).occupied_fen==0 and not (s.provider.directory/'artificial-provider.json').exists()

@pytest.mark.parametrize('reason',['key','price','reference','running','workspace'])
def test_pre_send_blocks_without_spending(setup,reason):
    c,s,clock=setup;job,body=prepare(c,s)
    if reason=='key':s.provider.ready=False
    elif reason=='price':clock[0]=PRICE_EXPIRES+1
    elif reason=='reference':
        option=s.workspace(P).images[0]
        with s.archive.prepare(s.provider.directory/'artificial-result.png','image') as (temporary,details):
            changed=s.library.upload_media(UploadSource(project_id=P,upload_id=uuid4().hex),'image',VersionInput(name='人工替换版本',definition='核对真正的引用变化'),s.archive,temporary,details,asset_id=option.asset.asset_id)
        s.library.update_reference(body['source_id'],ReferenceUpdate(version_id=changed.versions[-1].version_id,expected_revision=1))
    elif reason=='running':s.engine._active_sessions.add(P)
    else:body['expected_workspace']='a'*64;body['request_id']=uuid4().hex;assert c.post(URL,json=body).status_code==409;return
    r=c.post(URL+'/'+job['job_id']+'/confirm',json=approval(job));assert r.status_code==409,r.text
    assert not (s.provider.directory/'artificial-provider.json').exists()

def test_single_limit_and_shared_text_budget(setup):
    c,s,clock=setup;plan=s.plans.list(P)[0];body=plan.content.model_dump(exclude={'template_version'});body['targets'][0]={'segment_id':'seg-1','duration':9,'bindings':[{'reference_id':s.workspace(P).images[0].reference.reference_id,'role':'first_frame'}]}
    expensive=c.post('/api/generation-plans',json=body).json();c.post('/api/generation-plans/'+expensive['plan_id']+'/confirm',json={'content_hash':expensive['content_hash'],'local_only':True})
    assert c.post(URL,json=payload(c,s,'video',source_id=expensive['plan_id'])).status_code==409
    job,_=prepare(c,s)
    with s.round.transaction() as ledger:
        original=ledger.media_jobs[0]
        for n in range(4):
            j=original.model_copy(deep=True);j.job_id=uuid4().hex;j.number=n+2;j.prepare.request_id=uuid4().hex
            j.plan.kind='video';j.prepare.kind='video';j.plan.model='wan2.7-i2v';j.plan.reference_revision=None;j.plan.duration=5;j.plan.resolution='1080P';j.plan.estimate_fen=500;j.plan_hash=digest(j.plan.model_dump())
            j.confirm_request_id=uuid4().hex;j.confirm_digest='b'*64;j.reserved_fen=500;j.status='queued';ledger.media_jobs.append(j)
        s.round.save(ledger)
    assert CreationCandidates(s.engine).workspace(P).available_fen==0
    assert c.post(URL+'/'+job['job_id']+'/confirm',json=approval(job)).status_code==409

def test_crash_checkpoint_no_repeat_and_pending_id_query_limit(setup):
    c,s,clock=setup;job,_=prepare(c,s)
    s.confirm(P,job['job_id'],MediaConfirm(**approval(job)))
    with s.round.transaction() as ledger:
        j=ledger.media_jobs[0];j.status='submitting';j.submit_attempts=1;j.submitted_at=clock[0]-50;j.lease_until=clock[0]-1;s.round.save(ledger)
    assert s.advance(P,job['job_id']).status=='manual_review' and not (s.provider.directory/'artificial-provider.json').exists()

def test_lost_image_selection_ledger_reply_recovers_actual_reference(setup,monkeypatch):
    c,s,clock=setup;job,_=prepare(c,s);done=finish(c,s,clock,job);payload=dict(expected_selection=done['selection_token'],request_id=uuid4().hex,acknowledged=True)
    original=s.round.save
    with monkeypatch.context() as patch:
        patch.setattr(s.round,'save',lambda ledger:(_ for _ in ()).throw(OSError('artificial-private-details')))
        assert c.post(URL+'/'+job['job_id']+'/select',json=payload).status_code==500
    assert s.get(P,job['job_id']).selected
    r=c.post(URL+'/'+job['job_id']+'/select',json=payload);assert r.status_code==200,r.text
    assert len(s.workspace(P).selections)==1 and s.workspace(P).images[0].reference.revision==2

def test_old_round_ledger_optional_fields_compatible_and_corruption_refused(setup):
    c,s,clock=setup;job,_=prepare(c,s)
    path=s.round.path;document=json.loads(path.read_text());document['media_jobs']=[];document.pop('media_selections');path.write_text(json.dumps(document))
    assert CreationCandidates(s.engine).workspace(P).occupied_fen==0
    path.write_text('{');before=path.read_bytes();assert c.get(URL).status_code==503 and path.read_bytes()==before

@pytest.mark.parametrize('kind',['image','video'])
def test_native_wire_submit_exact_approved_request_and_same_id_query(setup,kind):
    c,s,clock=setup;job,request=prepare(c,s,kind);body,*_=s.compile(P,MediaPrepare(**request));private=None
    with s.round.transaction() as ledger:private=ledger.media_jobs[0].model_copy(deep=True)
    provider=ReleaseMediaProvider()
    reply=json.dumps({'output':{'task_id':'fixture-task-1','task_status':'PENDING'}}).encode()
    with wire(body=reply) as (transport,calls):
        task=provider.submit(private,body,transport);assert task=='fixture-task-1' and len(calls)==1
        method,path,headers,data=calls[0];assert method=='POST' and path.endswith('image2image/image-synthesis' if kind=='image' else 'video-generation/video-synthesis')
        assert json.loads(data)==body and headers['X-DashScope-Async']=='enable'
    private.provider_task_id=task
    with wire(body=json.dumps({'output':{'task_id':task,'task_status':'RUNNING'}}).encode()) as (transport,calls):
        assert provider.query(private,transport)==('RUNNING',None,None) and calls[0][1]=='/api/v1/tasks/'+task

@pytest.mark.parametrize('reply',[
 {'output':{'task_id':'wrong-id','task_status':'RUNNING'}},
 {'output':{'task_id':'fixture-task-1','task_status':'FAKE'}},
 {'output':{'task_id':'fixture-task-1','task_status':'SUCCEEDED','results':[{'url':'https://unreviewed.invalid/result.png'}]},'usage':{'image_count':1}},
 {'output':{'task_id':'fixture-task-1','task_status':'SUCCEEDED','results':[{'url':'https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/result.png'}]},'usage':{'image_count':2}},
])
def test_native_invalid_query_no_retry(setup,reply):
    c,s,clock=setup;job,_=prepare(c,s)
    with s.round.transaction() as ledger:private=ledger.media_jobs[0].model_copy(deep=True)
    private.provider_task_id='fixture-task-1'
    with wire(body=json.dumps(reply).encode()) as (transport,calls):
        with pytest.raises(Exception):ReleaseMediaProvider().query(private,transport)
        assert len(calls)==1

def test_native_download_no_authorization_and_real_format_archive(setup,tmp_path):
    c,s,clock=setup;job,_=prepare(c,s)
    with s.round.transaction() as ledger:private=ledger.media_jobs[0].model_copy(deep=True)
    private.result_url='https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/result.png?artificial=1'
    raw=(s.provider.directory/'artificial-result.png').read_bytes()
    with wire(body=raw) as (transport,calls):
        path=ReleaseMediaProvider().download(private,tmp_path/'download',transport)
        assert path.read_bytes()==raw and 'Authorization' not in calls[0][2]
        with s.archive.prepare(path,'image') as (_,details):assert details['width']==640

def test_concurrent_confirmations_share_one_atomic_round_ceiling(setup):
    c,s,clock=setup;plan=s.plans.list(P)[0];body=plan.content.model_dump(exclude={'template_version'})
    body['options']['resolution']='1080P';body['targets'][0]={'segment_id':'seg-1','duration':5,'bindings':[{'reference_id':s.workspace(P).images[0].reference.reference_id,'role':'first_frame'}]}
    created=c.post('/api/generation-plans',json=body);assert created.status_code==201
    plan=created.json();assert c.post('/api/generation-plans/'+plan['plan_id']+'/confirm',json={'content_hash':plan['content_hash'],'local_only':True}).status_code==200
    jobs=[prepare(c,s,'video',source_id=plan['plan_id'])[0] for _ in range(5)]
    for j in jobs[:3]:s.confirm(P,j['job_id'],MediaConfirm(**approval(j)))
    def attempt(j):
        try:return s.confirm(P,j['job_id'],MediaConfirm(**approval(j))).status
        except AssetLibraryError as e:return e.status
    with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(attempt,jobs[3:]))
    assert sorted(results,key=str)==[409,'queued']
    assert s.workspace(P).occupied_fen==2000 and CreationCandidates(s.engine).workspace(P).available_fen==0
    assert not (s.provider.directory/'artificial-provider.json').exists()

def test_reused_prepare_id_cannot_approve_or_poison_shared_ledger(setup):
    c,s,clock=setup;job,body=prepare(c,s)
    r=c.post(URL+'/'+job['job_id']+'/confirm',json=approval(job)|{'request_id':body['request_id']})
    assert r.status_code==409 and s.workspace(P).occupied_fen==0
    assert not (s.provider.directory/'artificial-provider.json').exists()
