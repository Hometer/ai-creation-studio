"""Actual HTTP restoration transactions, original versions and receipt recovery."""
import copy, json, uuid, hashlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from api.revision_import_restore_demo import create_restore_demo
from api.dependencies import get_workflow_engine
from core.content_revisions import digest,encoded
P='comparison-demo'; URL=f'/api/project/{P}/content-revisions'
def files(d): return {str(p.relative_to(d)):p.read_bytes() for p in d.rglob('*') if p.is_file()}
def edit(c,w,stage,path,value):
    p={'expected_token':w['token'],'stage':stage,'path':path,'value':value}
    preview=c.post(URL+'/preview',json=p);assert preview.status_code==200,preview.text
    r=c.post(URL,json={**p,'preview_token':preview.json()['preview_token'],'request_id':uuid.uuid4().hex,'acknowledged':True});assert r.status_code==201,r.text
    return r.json()['workspace']
def post(c,w,b,suffix='import-prepare',project=P,filename='old.json',extra=None):
    return c.post(f'/api/project/{project}/content-revisions/{suffix}',params={'expected_token':w['token'],'filename':filename,**(extra or {})},content=b,headers={'content-type':'application/json'})
def prepare(c,w,b):
    r=post(c,w,b);assert r.status_code==200,r.text;return r.json()
def restore(c,w,b,p,request_id=None,**extra):
    return post(c,w,b,'import-restore',extra={'confirmation_token':p['confirmation_token'],'request_id':request_id or uuid.uuid4().hex,'acknowledged':'true',**extra})
@pytest.fixture
def setup(tmp_path):
    app=create_restore_demo(tmp_path);c=TestClient(app,raise_server_exceptions=False);e=app.dependency_overrides[get_workflow_engine]()
    w=c.get(URL).json();b=c.get(URL+'/export',params={'format':'json','expected_token':w['token']}).content
    w=edit(c,w,'script_generation',['title'],'人工恢复之前的新标题')
    w=edit(c,w,'storyboard',['episodes',0,'segments',0,'shots',0,'content'],'人工恢复之前的新镜头')
    return c,e,tmp_path,w,b

def test_prepare_readonly_then_two_stages_old_history_and_receipt_atomically_saved(setup):
    c,e,d,w,b=setup;state=e.get_state(P);prior=copy.deepcopy(state.__dict__);before=files(d)
    p=prepare(c,w,b);assert p['preview']['total_changes']==2 and files(d)==before and state.__dict__==prior
    q=uuid.uuid4().hex;r=restore(c,w,b,p,q);assert r.status_code==201,r.text;x=r.json();doc=json.loads(b)
    assert len(x['data']['workspace']['versions'])==12 and len(x['data']['restores'])==1
    assert x['receipt']==x['data']['restores'][0] and x['receipt']['request_id']==q and 'request_digest' not in x['receipt']
    assert len(x['receipt']['version_ids'])==2 and state.artifacts['script_generation']==doc['current']['script_generation']['content'] and state.artifacts['storyboard']==doc['current']['storyboard']['content']
    assert state.content_history['versions'][:10]==prior['content_history']['versions']
    assert state.co_creation==prior['co_creation'] and state.content_reviews==prior['content_reviews']
    assert all(state.artifacts[k]==v for k,v in prior['artifacts'].items() if k not in ('script_generation','storyboard'))
    unchanged={k:v for k,v in before.items() if k!='sessions/comparison-demo.json'};assert all(files(d)[k]==v for k,v in unchanged.items())
    assert c.get(URL+'/import-restores').json()==x['data']
    baseline=files(d);assert restore(c,w,b,p,q).json()==x and files(d)==baseline
    fresh=TestClient(create_restore_demo(d));assert fresh.get(URL+'/import-restores').json()==x['data'] and restore(fresh,w,b,p,q).json()==x and files(d)==baseline
    updated=x['data']['workspace'];assert post(fresh,updated,b,'import-preview').json()['total_changes']==0
    # Standard export, comparison and single-stage rollback retain compatibility.
    export=fresh.get(URL+'/export',params={'format':'json','expected_token':updated['token']});assert export.status_code==200 and len(export.json()['versions'])==12
    ids=x['receipt']['version_ids'];old=next(v for v in w['versions'] if v['current'] and v['stage']=='script_generation')
    assert fresh.get(URL+'/compare',params={'expected_token':updated['token'],'before_version_id':old['version_id'],'after_version_id':ids['script_generation']}).status_code==200
    rollback={'expected_token':updated['token'],'stage':'script_generation','restore_version_id':old['version_id']};pv=fresh.post(URL+'/preview',json=rollback);assert pv.status_code==200
    rr=fresh.post(URL,json={**rollback,'preview_token':pv.json()['preview_token'],'request_id':uuid.uuid4().hex,'acknowledged':True});assert rr.status_code==201
    assert len(fresh.get(URL+'/import-restores').json()['restores'])==1

@pytest.mark.parametrize('kind',['false','missing','case','invalid_id','wrong_confirmation','wrong_file','renamed','stale','foreign','running','nochange','reused_edit'])
def test_refused_restoration_preserves_all_files(setup,kind):
    c,e,d,w,b=setup;p=prepare(c,w,b);kw={};body=b;expected=422
    if kind=='false':kw['acknowledged']='false'
    elif kind=='missing':
        before=files(d);r=post(c,w,b,'import-restore',extra={'confirmation_token':p['confirmation_token'],'request_id':uuid.uuid4().hex});assert r.status_code==422 and files(d)==before;return
    elif kind=='case':kw['acknowledged']='True'
    elif kind=='invalid_id':kw['request_id']='bad'
    elif kind=='wrong_confirmation':kw['confirmation_token']='a'*64;expected=409
    elif kind=='wrong_file':body=b+b' ';expected=409
    elif kind=='renamed':
        before=files(d);r=post(c,w,b,'import-restore',filename='renamed.json',extra={'confirmation_token':p['confirmation_token'],'request_id':uuid.uuid4().hex,'acknowledged':'true'});assert r.status_code==409 and files(d)==before;return
    elif kind=='stale':edit(c,w,'script_generation',['title'],'又一次真实修改');expected=409
    elif kind=='foreign':doc=json.loads(b);doc['project_id']='other';body=encoded(doc)
    elif kind=='running':e._active_sessions.add(P);expected=409
    elif kind=='nochange':body=c.get(URL+'/export',params={'format':'json','expected_token':w['token']}).content;p=prepare(c,w,body)
    else:kw['request_id']=e.get_state(P).content_history['versions'][-1]['request_id'];expected=409
    before=files(d);r=restore(c,w,body,p,**kw);assert r.status_code==expected,r.text;assert files(d)==before and 'Traceback' not in r.text

@pytest.mark.parametrize('kind',['before','replace','after'])
def test_failure_before_or_after_atomic_replace_never_leaves_half_restoration(setup,monkeypatch,kind):
    c,e,d,w,b=setup;p=prepare(c,w,b);oldfiles=files(d);oldstate=copy.deepcopy(e.get_state(P).__dict__);q=uuid.uuid4().hex
    if kind=='replace':
        import core.persistence as persistence
        original=persistence.os.replace
        def bad(*a,**kw):raise OSError('private write')
        monkeypatch.setattr(persistence.os,'replace',bad)
    else:
        original=e.save_session_to_disk
        def bad(*a,**kw):
            if kind=='after':original(*a,**kw)
            raise OSError('private write')
        monkeypatch.setattr(e,'save_session_to_disk',bad)
    r=restore(c,w,b,p,q);assert r.status_code==503 and 'private' not in r.text
    data=c.get(URL+'/import-restores');assert data.status_code==200,data.text
    if kind=='after':assert len(data.json()['restores'])==1 and len(data.json()['workspace']['versions'])==12
    else:assert files(d)==oldfiles and e.get_state(P).__dict__==oldstate and not data.json()['restores']
    assert not list((d/'sessions').glob('.session-*'))

@pytest.mark.parametrize('kind',['history','ledger','response'])
def test_capacity_refuses_before_persistence(setup,monkeypatch,kind):
    c,_,d,w,b=setup;p=prepare(c,w,b);before=files(d)
    import core.revision_import_restore as module
    if kind=='history':monkeypatch.setattr(module,'MAX_HISTORY',10)
    elif kind=='ledger':monkeypatch.setattr(module,'MAX_RESTORE_BYTES',10)
    else:
        original=module.encoded
        monkeypatch.setattr(module,'encoded',lambda v: b'x'*(8*1024*1024+1) if isinstance(v,dict) and 'receipt' in v else original(v))
    r=restore(c,w,b,p);assert r.status_code==422,r.text;assert files(d)==before

def test_reused_receipt_different_request_refused_after_restart(setup):
    c,_,d,w,b=setup;p=prepare(c,w,b);q=uuid.uuid4().hex;assert restore(c,w,b,p,q).status_code==201
    fresh=TestClient(create_restore_demo(d));before=files(d)
    assert restore(fresh,w,b+b' ',p,q).status_code==409 and files(d)==before

def test_concurrent_same_confirmation_new_ids_only_one_commit(setup):
    c,_,_,w,b=setup;p=prepare(c,w,b)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:restore(c,w,b,p).status_code,range(2)))
    assert sorted(results)==[201,409] and len(c.get(URL+'/import-restores').json()['restores'])==1

def test_empty_prior_and_untracked_current_preserved_without_importing_backup_history(setup):
    c,e,d,w,b=setup;state=e.get_state(P);state.artifacts['script_generation']={};state.artifacts['storyboard']=json.loads(b)['current']['storyboard']['content'];state.content_history={'schema_version':1,'versions':[]};e.save_session_to_disk(P)
    w=c.get(URL).json();r=restore(c,w,b,prepare(c,w,b));assert r.status_code==201,r.text
    history=e.get_state(P).content_history['versions'];assert len(history)==3 and history[0]['content']=={} and history[0]['source']=='baseline'
    assert len(r.json()['receipt']['version_ids'])==1 and len(c.get(URL+'/export',params={'format':'json','expected_token':r.json()['data']['workspace']['token']}).json()['versions'])==3

@pytest.mark.parametrize('kind',['record_id','digest','link','request','sequence','schema'])
def test_corrupt_ledger_is_refused_not_overwritten(setup,kind):
    c,_,d,w,b=setup;p=prepare(c,w,b);x=restore(c,w,b,p).json();path=d/'sessions/comparison-demo.json';doc=json.loads(path.read_text());record=doc['backup_restores']['records'][0]
    if kind=='record_id':record['restore_id']='bad'
    elif kind=='digest':record['before_digests']['script_generation']='a'*64
    elif kind=='link':record['version_ids']['script_generation']=record['version_ids']['storyboard']
    elif kind=='request':record['request_id']='a'*32
    elif kind=='sequence':record['number']=2
    else:doc['backup_restores']['schema_version']=True
    path.write_text(json.dumps(doc));before=files(d)
    assert c.get(URL+'/import-restores').status_code==503 and restore(c,w,b,p).status_code==503 and files(d)==before

def test_stale_second_engine_cannot_overwrite_a_completed_receipt(setup):
    c,e,d,w,b=setup;p=prepare(c,w,b);other=create_restore_demo(d).dependency_overrides[get_workflow_engine]();assert restore(c,w,b,p).status_code==201;before=files(d)
    with pytest.raises(ValueError):other.save_session_to_disk(P)
    assert files(d)==before


def test_restore_empty_target_keeps_previous_text_and_only_changed_stage(setup):
    c,e,d,w,b=setup;doc=json.loads(b)
    # Keep the current storyboard, while deliberately restoring script to {}.
    state=e.get_state(P);doc['current']['storyboard']['content']=copy.deepcopy(state.artifacts['storyboard']);doc['current']['storyboard']['digest']=digest(state.artifacts['storyboard']);doc['current']['storyboard']['version_id']=None
    doc['current']['script_generation'].update(content={},digest=digest({}),version_id=None)
    doc['untracked_stages']=['script_generation','storyboard'];body=encoded(doc);old=copy.deepcopy(state.content_history)
    p=prepare(c,w,body);r=restore(c,w,body,p);assert r.status_code==201,r.text
    assert state.artifacts['script_generation']=={} and state.content_history['versions'][:10]==old['versions']
    assert len(state.content_history['versions'])==11 and set(r.json()['receipt']['version_ids'])=={'script_generation'}
    fresh=TestClient(create_restore_demo(d));assert fresh.get(URL+'/import-restores').json()==r.json()['data']
