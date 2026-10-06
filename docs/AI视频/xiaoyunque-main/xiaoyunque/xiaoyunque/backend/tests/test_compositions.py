"""Actual local output, complete manifests, approval, recovery and old-film preservation."""
import copy
import hashlib
import json
from contextlib import contextmanager
from uuid import uuid4
import subprocess
import pytest
from fastapi.testclient import TestClient
from api.composition_demo import create_composition_demo
from api.schemas.compositions import CompositionConfirm,CompositionPrepare
from core.orchestrator import WorkflowEngine
from core.compositions import Compositions
from core.asset_library import AssetLibraryError
P='composition-demo';U='/api/project/'+P+'/compositions'
@pytest.fixture
def setup(tmp_path):
    app,s=create_composition_demo(tmp_path)
    return TestClient(app,raise_server_exceptions=False),s,tmp_path

def prepare_body(c,reverse=False):
    w=c.get(U).json(); options={o['name']:o for o in w['options']}
    clips=[{'segment_id':'blue',**{k:options['人工蓝色有声音'][k] for k in ['asset_id','version_id']}},
           {'segment_id':'gold',**{k:options['人工黄色静音'][k] for k in ['asset_id','version_id']}}]
    return {'request_id':uuid4().hex,'expected_workspace':w['expected_workspace'],'clips':clips[::-1] if reverse else clips,'keep_stale':[]}

def prepared(c,reverse=False):
    body=prepare_body(c,reverse);r=c.post(U,json=body);assert r.status_code==201,r.text
    job=r.json();return body,job

def confirm(c,j):
    body={'request_id':uuid4().hex,'plan_hash':j['plan_hash'],'acknowledged':True}
    r=c.post(U+'/'+j['job_id']+'/confirm',json=body);assert r.status_code==200,r.text
    return body,r.json()

def pixel(path,when):
    raw=subprocess.run(['ffmpeg','-v','error','-ss',str(when),'-i',str(path),'-frames:v','1','-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True,timeout=10).stdout
    return list(raw)

def test_real_ffmpeg_order_sound_download_versions_restart(setup):
    c,s,d=setup;before=copy.deepcopy(s.engine.get_status_snapshot(P)['artifacts'])
    body,j=prepared(c);assert j['attempts']==0 and j['output'] is None and j['plan']['total_duration']==3
    assert c.post(U,json=body).json()==j
    approval,q=confirm(c,j);assert q['status']=='queued' and q['attempts']==0
    assert c.post(U+'/'+j['job_id']+'/confirm',json=approval).json()==q
    done=s.advance(P,j['job_id']);assert done.status=='succeeded',done.error
    path=s.output_path(P,j['job_id']);a=pixel(path,.5);b=pixel(path,2.5)
    assert a[2]>a[0] and b[0]>b[2]
    audio=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','f32le','-ac','1','-ar','48000','pipe:1'],capture_output=True,check=True,timeout=10).stdout
    import array
    samples=array.array('f');samples.frombytes(audio)
    assert sum(x*x for x in samples[24000:48000])/24000>.0001
    assert sum(x*x for x in samples[120000:132000])/12000<.00001
    downloaded=c.get(U+'/'+j['job_id']+'/media?download=true');assert downloaded.status_code==200
    oldhash=hashlib.sha256(path.read_bytes()).hexdigest();assert hashlib.sha256(downloaded.content).hexdigest()==oldhash
    assert 'attachment' in downloaded.headers['content-disposition']
    _,second=prepared(c,True);confirm(c,second);assert s.advance(P,second['job_id']).status=='succeeded'
    assert pixel(s.output_path(P,second['job_id']),.5)[0]>pixel(s.output_path(P,second['job_id']),.5)[2]
    assert hashlib.sha256(path.read_bytes()).hexdigest()==oldhash
    assert s.engine.get_status_snapshot(P)['artifacts']==before
    restored=Compositions(WorkflowEngine(s.engine._session_dir),s.library,s.archive)
    assert restored.get(P,j['job_id']).model_dump()==s.get(P,j['job_id']).model_dump()
    assert restored.output_path(P,j['job_id']).read_bytes()==downloaded.content

@pytest.mark.parametrize('change',['missing','extra','repeated','foreign','false','unknown'])
def test_incomplete_or_unapproved_contract_does_not_render(setup,change):
    c,s,d=setup;b=prepare_body(c)
    if change=='missing':b['clips'].pop()
    elif change=='extra':b['clips'].append(b['clips'][0]|{'segment_id':'extra'})
    elif change=='repeated':b['clips'].append(b['clips'][0])
    elif change=='foreign':b['clips'][0]['asset_id']='f'*32
    elif change=='false':b['expected_workspace']='0'*64
    else:b['keep_stale']=['blue']
    r=c.post(U,json=b);assert r.status_code in {409,422}
    assert c.get(U).json()['jobs']==[]

@pytest.mark.parametrize('ack',[False,1,'true',None])
def test_explicit_confirmation_contract(setup,ack):
    c,s,d=setup;_,j=prepared(c)
    assert c.post(U+'/'+j['job_id']+'/confirm',json={'request_id':uuid4().hex,'plan_hash':j['plan_hash'],'acknowledged':ack}).status_code==422
    assert s.get(P,j['job_id']).status=='draft'

def test_changed_goal_refuses_old_plan_and_reused_ids(setup):
    c,s,d=setup;b,j=prepared(c)
    assert c.post(U,json=b|{'keep_stale':['blue']}).status_code==409
    s.engine.update_session_meta(P,{'target_duration':4},('target_duration',))
    assert c.post(U+'/'+j['job_id']+'/confirm',json={'request_id':uuid4().hex,'plan_hash':j['plan_hash'],'acknowledged':True}).status_code==409
    assert s.get(P,j['job_id']).status=='draft'
    assert c.get('/api/project/foreign/compositions/'+j['job_id']).status_code==404
    assert c.get('/api/project/foreign/compositions/'+j['job_id']+'/media').status_code==404

def test_corrupt_or_missing_file_fails_closed_without_overwriting(setup):
    c,s,d=setup;b=prepare_body(c);w=c.get(U).json();m=w['options'][0]['media']
    from api.schemas.assets import MediaInfo
    path=s.archive.path(MediaInfo.model_validate(m));path.write_bytes(b'broken')
    assert c.post(U,json=b).status_code==503
    assert c.get(U).json()['jobs']==[]
    s.path.write_text('{"schema_version":1,"jobs":[{}]}')
    original=s.path.read_bytes();assert c.get(U).status_code==503 and s.path.read_bytes()==original

def test_failed_render_and_unknown_restart_never_repeat(setup):
    c,s,d=setup;_,j=prepared(c);confirm(c,j)
    class Failing:
        @staticmethod
        def available():return True
        @contextmanager
        def render(self,*args):
            raise TimeoutError('private-artificial-message')
            yield
    s.renderer=Failing();done=s.advance(P,j['job_id']);assert done.status=='failed' and done.attempts==1
    assert 'private-artificial-message' not in done.error and s.advance(P,j['job_id']).status=='failed'
    _,j2=prepared(c);confirm(c,j2)
    with s.transaction() as (_,store):
        job=s.find(store,P,j2['job_id']);job.status='rendering';job.attempts=1;job.lease_until=0;s.save(store)
    assert s.advance(P,j2['job_id']).status=='manual_review'
    assert s.advance(P,j2['job_id']).attempts==1

def test_queued_restores_and_cancel_prevents_worker_render(setup):
    c,s,d=setup;_,j=prepared(c);confirm(c,j)
    restored=Compositions(WorkflowEngine(s.engine._session_dir),s.library,s.archive)
    assert restored.get(P,j['job_id']).status=='queued'
    r=c.post(U+'/'+j['job_id']+'/stop',json={'acknowledged':True});assert r.status_code==200 and r.json()['status']=='cancelled'
    s.tick();assert s.get(P,j['job_id']).attempts==0
    assert c.get(U+'/'+j['job_id']+'/media').status_code==409

def test_selected_media_default_and_stale_upstream_requires_per_clip_ack(tmp_path,monkeypatch):
    from api.release_media_demo import create_release_demo
    from api.schemas.release_media import MediaSelect
    from test_release_media import prepare,finish
    clock=[1791259200];monkeypatch.setattr('core.release_media.time.time',lambda:clock[0])
    app,media=create_release_demo(tmp_path);c=TestClient(app,raise_server_exceptions=False)
    j,_=prepare(c,media,'video');done=finish(c,media,clock,j)
    media.select('media-demo',done['job_id'],MediaSelect(expected_selection=done['selection_token'],request_id=uuid4().hex,acknowledged=True))
    s=Compositions(media.engine,media.library,media.archive)
    w=s.workspace('media-demo');selected=w.segments[0].selected;assert selected.version_id==done['version_id']
    original=media.engine.get_state('media-demo').artifacts['storyboard']['episodes'][0]['segments'][0]['shots'][0]['content']
    media.engine.get_state('media-demo').artifacts['storyboard']['episodes'][0]['segments'][0]['shots'][0]['content']=original+' 上游已改变'
    media.engine.save_session_to_disk('media-demo')
    w=s.workspace('media-demo');assert w.options[0].stale
    body=CompositionPrepare(request_id=uuid4().hex,expected_workspace=w.expected_workspace,clips=[selected])
    with pytest.raises(AssetLibraryError) as e:s.prepare('media-demo',body)
    assert e.value.code=='COMPOSITION_STALE_REVIEW_REQUIRED'
    good=s.prepare('media-demo',body.model_copy(update={'keep_stale':['seg-1']}));assert good.plan.clips[0].stale
    assert media.workspace('media-demo').occupied_fen==120

def test_confirm_concurrency_and_output_checkpoint_recovery(setup):
    from concurrent.futures import ThreadPoolExecutor
    c,s,d=setup;_,j=prepared(c)
    def attempt(_):
        try:return s.confirm(P,j['job_id'],CompositionConfirm(request_id=uuid4().hex,plan_hash=j['plan_hash'],acknowledged=True)).status
        except AssetLibraryError:return 'conflict'
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(attempt,range(2)))==['conflict','queued']
    assert s.advance(P,j['job_id']).status=='succeeded';path=s.output_path(P,j['job_id']);old=path.read_bytes()
    with s.transaction() as (_,store):
        job=s.find(store,P,j['job_id']);job.status='rendering';job.lease_until=0;s.save(store)
    class NoRender:
        def render(self,*a):raise AssertionError('Must recover original output')
        @staticmethod
        def available():return True
    s.renderer=NoRender();assert s.advance(P,j['job_id']).status=='succeeded'
    assert path.read_bytes()==old and s.get(P,j['job_id']).attempts==1

def test_interrupted_process_and_running_project_do_not_touch_inputs(setup):
    import threading
    c,s,d=setup;b,j=prepared(c);before={o.version_id:s.archive.verify_file(o.media).read_bytes() for o in s.workspace(P).options}
    s.engine._active_sessions.add(P)
    assert c.post(U,json=b|{'request_id':uuid4().hex}).status_code==409
    s.engine._active_sessions.remove(P);confirm(c,j)
    stop=threading.Event();stop.set();assert s.advance(P,j['job_id'],stop).status=='manual_review'
    assert {o.version_id:s.archive.verify_file(o.media).read_bytes() for o in s.workspace(P).options}==before

def test_missing_tools_and_store_write_error_leave_original_plan(setup,monkeypatch):
    c,s,d=setup;_,j=prepared(c);original=s.path.read_bytes()
    monkeypatch.setattr(s.renderer,'available',lambda:False)
    assert c.post(U+'/'+j['job_id']+'/confirm',json={'request_id':uuid4().hex,'plan_hash':j['plan_hash'],'acknowledged':True}).status_code==503
    assert s.path.read_bytes()==original
    monkeypatch.setattr(s.renderer,'available',lambda:True)
    def broken(*args):raise OSError('private-write-details')
    monkeypatch.setattr('core.compositions.atomic_write_session',broken)
    response=c.post(U+'/'+j['job_id']+'/confirm',json={'request_id':uuid4().hex,'plan_hash':j['plan_hash'],'acknowledged':True})
    assert response.status_code==503 and 'private-write-details' not in response.text and s.path.read_bytes()==original

def test_unsafe_ledger_symlink_is_read_only(setup):
    c,s,d=setup;target=d/'foreign.json';target.write_text('{"schema_version":1,"jobs":[]}')
    s.round.directory.mkdir(parents=True,exist_ok=True);s.path.symlink_to(target)
    assert c.get(U).status_code==503 and target.read_text()=='{"schema_version":1,"jobs":[]}'

def test_native_http_worker_recovers_approved_queue_and_download_after_restart(tmp_path):
    import socket,sys,time,httpx
    from pathlib import Path
    app,s=create_composition_demo(tmp_path);c=TestClient(app,raise_server_exceptions=False)
    _,j=prepared(c);confirm(c,j);assert s.get(P,j['job_id']).status=='queued'
    @contextmanager
    def server():
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-m','api.composition_demo','--directory',str(tmp_path),'--port',str(port)],cwd=Path(__file__).resolve().parents[1],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=2,trust_env=False) as client:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    try:
                        if client.get('/').status_code==200:break
                    except httpx.TransportError:pass
                    time.sleep(.05)
                else:raise AssertionError('Local worker did not become ready')
                yield client
        finally:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    with server() as client:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            result=client.get(U+'/'+j['job_id']).json()
            if result['status']=='succeeded':break
            time.sleep(.1)
        assert result['status']=='succeeded' and result['attempts']==1
        downloaded=client.get(U+'/'+j['job_id']+'/media').content
    with server() as client:
        assert client.get(U+'/'+j['job_id']).json()==result
        assert client.get(U+'/'+j['job_id']+'/media').content==downloaded
        assert client.get('/api/project/foreign/compositions/'+j['job_id']+'/media').status_code==404
