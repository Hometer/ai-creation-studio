"""Real HTTP backup previews; preserve every target file and state."""
import copy, hashlib, json, uuid
import pytest
from fastapi.testclient import TestClient
from api.revision_import_preview_demo import create_preview_demo
from api.dependencies import get_workflow_engine
from core.content_revisions import digest, encoded
P='comparison-demo'; URL=f'/api/project/{P}/content-revisions'
@pytest.fixture
def setup(tmp_path):
    app=create_preview_demo(tmp_path); c=TestClient(app,raise_server_exceptions=False)
    w=c.get(URL).json(); body=c.get(URL+'/export',params={'format':'json','expected_token':w['token']}).content
    return c,app.dependency_overrides[get_workflow_engine](),tmp_path,w,body

def files(d): return {str(p.relative_to(d)):p.read_bytes() for p in d.rglob('*') if p.is_file()}
def post(c,w,body,project=P,filename='backup.json',headers=None):
    return c.post(f'/api/project/{project}/content-revisions/import-preview',params={'expected_token':w['token'],'filename':filename},content=body,headers=headers or {'content-type':'application/json'})
def save(c,w,stage,path,value):
    p={'expected_token':w['token'],'stage':stage,'path':path,'value':value}
    preview=c.post(URL+'/preview',json=p); assert preview.status_code==200
    r=c.post(URL,json={**p,'preview_token':preview.json()['preview_token'],'request_id':uuid.uuid4().hex,'acknowledged':True})
    assert r.status_code==201,r.text
    return r.json()['workspace']

def test_unchanged_actual_backup_has_zero_diffs_no_writes_or_authorization(setup,monkeypatch):
    c,e,d,w,b=setup; before=files(d); state=copy.deepcopy(e.get_state(P).__dict__)
    monkeypatch.setattr(e,'save_session_to_disk',lambda *a,**k:pytest.fail('preview wrote'))
    r=post(c,w,b); assert r.status_code==200,r.text; x=r.json()
    assert x['mode']=='read_only' and x['scope']=='backup_current' and x['source_project_id']==P and x['total_changes']==0
    assert x['file_sha256']==hashlib.sha256(b).hexdigest() and x['file_bytes']==len(b)
    assert all(s['before_digest']==s['after_digest'] and s['changes']==s['impacts']==[] for s in x['stages'].values())
    assert 'preview_token' not in x and 'artifact' not in x and 'versions' not in x
    assert post(c,w,b).json()==x and files(d)==before and e.get_state(P).__dict__==state

def test_old_download_against_actual_new_edits_binds_current_token_and_direction(setup):
    c,e,d,w,b=setup; original=json.loads(b)
    w2=save(c,w,'script_generation',['title'],'人工备份之后的新标题')
    w3=save(c,w2,'storyboard',['episodes',0,'segments',0,'shots',0,'content'],'人工备份之后的新镜头')
    before=files(d); assert post(c,w,b).status_code==409
    r=post(c,w3,b); assert r.status_code==200,r.text; x=r.json()
    assert x['token']==w3['token'] and x['total_changes']==2
    script=x['stages']['script_generation']; board=x['stages']['storyboard']
    assert script['changes'][0]['before']=='人工备份之后的新标题' and script['changes'][0]['after']==original['current']['script_generation']['content']['title']
    assert board['changes'][0]['before']=='人工备份之后的新镜头' and all(s['impacts'] for s in x['stages'].values())
    assert len(c.get(URL).json()['versions'])==10 and files(d)==before

def test_actual_restart_preserves_workspace_export_precheck_and_preview(setup):
    c,_,d,w,b=setup; w=save(c,w,'script_generation',['title'],'人工重启当前标题'); before=files(d)
    preview=post(c,w,b).json(); check=c.post(URL+'/import-precheck',params={'expected_token':w['token'],'filename':'old.json'},content=b,headers={'content-type':'application/json'}).json()
    fresh=TestClient(create_preview_demo(d)); assert fresh.get(URL).json()==w and post(fresh,w,b).json()==preview
    assert fresh.post(URL+'/import-precheck',params={'expected_token':w['token'],'filename':'old.json'},content=b,headers={'content-type':'application/json'}).json()==check and files(d)==before

def test_empty_backup_and_cross_project_rejection(setup):
    c,_,d,w,b=setup; base='/api/project/revision-empty/content-revisions'; empty=c.get(base).json(); body=c.get(base+'/export',params={'expected_token':empty['token'],'format':'json'}).content; before=files(d)
    r=post(c,empty,body,project='revision-empty'); assert r.status_code==200 and r.json()['total_changes']==0
    assert all(s['before_field_count']==s['after_field_count']==0 for s in r.json()['stages'].values())
    r=post(c,empty,b,project='revision-empty'); assert r.status_code==422 and r.json()['error']['code']=='BACKUP_PROJECT_MISMATCH' and files(d)==before

@pytest.mark.parametrize('running',['active','status'])
def test_running_target_blocks_preview_while_precheck_stays_readonly(setup,running):
    c,e,d,w,b=setup
    if running=='active': e._active_sessions.add(P)
    else: e.get_state(P).status['script_generation']='running'; e.save_session_to_disk(P)
    before=files(d); r=post(c,w,b); assert r.status_code==409 and r.json()['error']['code']=='PROJECT_RUNNING'
    assert c.post(URL+'/import-precheck',params={'expected_token':w['token'],'filename':'x.json'},content=b,headers={'content-type':'application/json'}).status_code==200 and files(d)==before

def test_full_structure_presence_types_empty_and_two_storyboard_impact_union(setup):
    c,e,d,w,b=setup; doc=json.loads(b); target=doc['current']['script_generation']
    target['content']['title']=7; target['content']['new_null']=None; del target['content']['logline']; target['content']['beat_sheet']=''
    target['digest']=digest(target['content']); target['version_id']=None
    board=doc['current']['storyboard']; board['content']['episodes'][0]['segments'][0]['segment_id']='backup-only-segment'; board['digest']=digest(board['content']); board['version_id']=None
    doc['untracked_stages']=['script_generation','storyboard']; before=files(d)
    r=post(c,w,encoded(doc)); assert r.status_code==200,r.text; x=r.json(); rows={tuple(v['path']):v for v in x['stages']['script_generation']['changes']}
    assert rows[('title',)]['before_type']=='text' and rows[('title',)]['after_type']=='number'
    assert rows[('new_null',)]['before_type']=='missing' and rows[('new_null',)]['after_type']=='null'
    assert rows[('logline',)]['after_present'] is False and rows[('beat_sheet',)]['after']==''
    ids={i['segment_id'] for i in x['stages']['script_generation']['impacts']}; assert 'backup-only-segment' in ids and len(ids)>1
    assert x['total_changes']==sum(len(s['changes']) for s in x['stages'].values()) and files(d)==before

@pytest.mark.parametrize('case',['json','duplicate','nan','utf8','missing_format','digest','history_relation','deep','empty','wrong_type','compression','filename'])
def test_invalid_file_transport_and_contents_safe_no_write(setup,case):
    c,_,d,w,b=setup; body=b; headers=None; filename='x.json'; status=422
    if case=='json': body=b'{private'
    elif case=='duplicate': body=b'{"x":1,"x":2}'
    elif case=='nan': body=b'{"x":NaN}'
    elif case=='utf8': body=b'\xff'
    elif case=='deep': body=b'{"x":'+b'['*65+b'0'+b']'*65+b'}'
    elif case=='empty': body=b''; status=413
    elif case=='wrong_type': headers={'content-type':'text/plain'}; status=415
    elif case=='compression': headers={'content-type':'application/json','content-encoding':'gzip'}; status=415
    elif case=='filename': filename='../x.json'; status=415
    else:
        doc=json.loads(b)
        if case=='missing_format': del doc['format']
        elif case=='digest': doc['current']['script_generation']['content']['title']='private'
        else: doc['versions'][0]['restored_from']='a'*32
        body=encoded(doc)
    before=files(d); r=post(c,w,body,filename=filename,headers=headers); assert r.status_code==status,r.text
    assert 'error' in r.json() and 'private' not in r.text and 'Traceback' not in r.text and files(d)==before

def test_combined_diff_and_serialized_response_limits_refuse_without_truncation(setup,monkeypatch):
    c,_,d,w,b=setup; w=save(c,w,'script_generation',['title'],'new title'); w=save(c,w,'storyboard',['episodes',0,'segments',0,'shots',0,'content'],'new shot'); before=files(d)
    import core.revision_import_preview as module
    with monkeypatch.context() as m:
        m.setattr(module,'MAX_COMPARISON_CHANGES',1); r=post(c,w,b); assert r.status_code==422 and r.json()['error']['code']=='BACKUP_PREVIEW_LIMIT'
    with monkeypatch.context() as m:
        m.setattr(module,'MAX_COMPARISON_RESPONSE',50); assert post(c,w,b).status_code==422
    with monkeypatch.context() as m:
        m.setattr(module,'MAX_CONTENT',10); assert post(c,w,b).status_code==422
    assert files(d)==before

def test_stream_limit_does_not_trust_content_length(setup,monkeypatch):
    c,_,d,w,b=setup; before=files(d)
    import core.revision_exports as module
    monkeypatch.setattr(module,'MAX_EXPORT_BYTES',20)
    assert post(c,w,b,headers={'content-type':'application/json','content-length':'1'}).status_code==413 and files(d)==before

def test_missing_corrupt_and_invalid_target_query_are_safe(setup):
    c,_,d,w,b=setup; assert post(c,w,b,project='missing-project').status_code==404
    assert post(c,{**w,'token':'invalid'},b).status_code==422
    (d/'sessions/comparison-demo.json').write_text('{broken'); r=post(c,w,b); assert r.status_code==503 and 'Traceback' not in r.text
