"""Actual portable export inspection through HTTP; all project bytes retained."""
import copy, hashlib, json, uuid
import pytest
from fastapi.testclient import TestClient
from api.revision_import_precheck_demo import create_precheck_demo
from api.dependencies import get_workflow_engine
from core.content_revisions import digest,encoded
P='comparison-demo';URL=f'/api/project/{P}/content-revisions'
@pytest.fixture
def setup(tmp_path):
 app=create_precheck_demo(tmp_path);c=TestClient(app,raise_server_exceptions=False);w=c.get(URL).json()
 body=c.get(URL+'/export',params={'format':'json','expected_token':w['token']}).content
 return c,app.dependency_overrides[get_workflow_engine](),tmp_path,w,body

def files(d):return {str(p.relative_to(d)):p.read_bytes() for p in d.rglob('*') if p.is_file()}
def post(c,w,body,project=P,filename='backup.json',headers=None):
 return c.post(f'/api/project/{project}/content-revisions/import-precheck',params={'expected_token':w['token'],'filename':filename},content=body,headers=headers or {'content-type':'application/json'})

def test_actual_export_inspection_is_repeatable_no_writes_or_full_document(setup,monkeypatch):
 c,e,d,w,body=setup;before=files(d);state=copy.deepcopy(e.get_state(P).__dict__)
 monkeypatch.setattr(e,'save_session_to_disk',lambda *a,**k:pytest.fail('precheck wrote'))
 r=post(c,w,body);assert r.status_code==200,r.text;x=r.json()
 assert x['mode']=='read_only' and x['same_project'] and x['source_project_id']==P
 assert x['file_sha256']==hashlib.sha256(body).hexdigest() and x['file_bytes']==len(body) and len(x['versions'])==8
 assert all(s['matches_current'] for s in x['stages'].values()) and sum(s['version_count'] for s in x['stages'].values())==8
 assert all('content' not in v and 'changes' not in v for v in x['versions'])
 assert post(c,w,body).json()==x and files(d)==before and e.get_state(P).__dict__==state

def test_restart_keeps_inspection_and_export_bytes(setup):
 c,_,d,w,body=setup;x=post(c,w,body).json();before=files(d);fresh=TestClient(create_precheck_demo(d))
 assert post(fresh,w,body).json()==x and fresh.get(URL+'/export',params={'format':'json','expected_token':w['token']}).content==body and files(d)==before

def test_foreign_source_and_empty_backup(setup):
 c,_,d,w,body=setup;empty=c.get('/api/project/revision-empty/content-revisions').json();before=files(d)
 r=post(c,empty,body,project='revision-empty');assert r.status_code==200 and not r.json()['same_project'] and r.json()['source_project_id']==P
 b=c.get('/api/project/revision-empty/content-revisions/export',params={'format':'json','expected_token':empty['token']}).content
 x=post(c,empty,b,project='revision-empty').json();assert x['versions']==[] and all(s['field_count']==0 and s['version_id'] is None and s['matches_current'] for s in x['stages'].values()) and files(d)==before

def test_old_backup_needs_current_target_token(setup):
 c,_,d,w,body=setup;p={'expected_token':w['token'],'stage':'script_generation','path':['title'],'value':'人工预检后续标题'}
 preview=c.post(URL+'/preview',json=p).json();r=c.post(URL,json={**p,'preview_token':preview['preview_token'],'request_id':uuid.uuid4().hex,'acknowledged':True});assert r.status_code==201
 new=r.json()['workspace'];before=files(d);assert post(c,w,body).status_code==409
 x=post(c,new,body).json();assert not x['stages']['script_generation']['matches_current'] and x['stages']['storyboard']['matches_current'] and len(x['versions'])==8 and files(d)==before

def test_untracked_and_restore_relation_support(setup):
 c,e,d,_,_=setup;s=e.get_state(P);s.artifacts['script_generation']['title']='人工未归档当前内容';e.save_session_to_disk(P)
 w=c.get(URL).json();body=c.get(URL+'/export',params={'format':'json','expected_token':w['token']}).content;before=files(d)
 x=post(c,w,body).json();assert x['stages']['script_generation']['untracked'] and x['stages']['script_generation']['version_id'] is None and files(d)==before
 doc=json.loads(body);v=copy.deepcopy(doc['versions'][0]);v.update(version_id=uuid.uuid4().hex,number=9,source='restore',restored_from=doc['versions'][0]['version_id']);doc['versions'].append(v)
 doc['current']['script_generation'].update(content=v['content'],digest=v['digest'],version_id=v['version_id']);doc['untracked_stages']=[]
 r=post(c,w,encoded(doc));assert r.status_code==200 and r.json()['versions'][-1]['restored_from']==v['restored_from'] and files(d)==before

@pytest.mark.parametrize('name',['../backup.json','a/b.json','a\\b.json','bad\n.json','backup.txt',' backup.json'])
def test_unsafe_names(setup,name):
 c,_,d,w,b=setup;before=files(d);r=post(c,w,b,filename=name);assert r.status_code==415 and 'error' in r.json() and files(d)==before

@pytest.mark.parametrize('body',[b'not a backup',b'<html>private input</html>',b'{"x":NaN}',b'{"x":1,"x":2}',b'\xff',b'{}'])
def test_invalid_real_content(setup,body):
 c,_,d,w,_=setup;before=files(d);r=post(c,w,body);assert r.status_code==422 and 'private input' not in r.text and 'Traceback' not in r.text and files(d)==before

@pytest.mark.parametrize('case',['schema','boolean_schema','missing_format','extra','format','digest','sequence','duplicate','date','restore','foreign_restore','current','scope','untracked','snapshot_type','source_id'])
def test_invalid_structure_digest_and_relationships(setup,case):
 c,_,d,w,body=setup;x=json.loads(body);v=x['versions'][1]
 if case=='schema':x['schema_version']=2
 elif case=='boolean_schema':x['schema_version']=True
 elif case=='missing_format':del x['format']
 elif case=='extra':x['secret']='private input'
 elif case=='format':x['format']='session'
 elif case=='digest':x['current']['script_generation']['content']['title']='tampered'
 elif case=='sequence':v['number']=50
 elif case=='duplicate':v['version_id']=x['versions'][0]['version_id']
 elif case=='date':v['created_at']='invalid'
 elif case=='restore':v['restored_from']=x['versions'][0]['version_id']
 elif case=='foreign_restore':v.update(source='restore',restored_from=x['versions'][0]['version_id'])
 elif case=='current':x['current']['script_generation']['version_id']=x['versions'][0]['version_id']
 elif case=='scope':del x['current']['storyboard']
 elif case=='untracked':x['untracked_stages']=['storyboard']
 elif case=='snapshot_type':v['content']=[]
 elif case=='source_id':x['project_id']='../other'
 before=files(d);r=post(c,w,encoded(x));assert r.status_code==422,r.text;assert files(d)==before and 'private input' not in r.text

@pytest.mark.parametrize('headers',[{'content-type':'text/plain'},{'content-type':'application/json','content-encoding':'gzip'}])
def test_transport_type(setup,headers):
 c,_,d,w,body=setup;before=files(d);assert post(c,w,body,headers=headers).status_code==415 and files(d)==before

def test_depth_nodes_snapshot_history_and_field_capacity_limits(setup,monkeypatch):
 c,_,d,w,body=setup;before=files(d);assert post(c,w,b'{"nested":'+b'['*65+b'0'+b']'*65+b'}').status_code==422
 import core.revision_import_precheck as module
 for key,value in [('MAX_NODES',10),('MAX_CONTENT',10),('MAX_HISTORY',10)]:
  with monkeypatch.context() as m:m.setattr(module,key,value);assert post(c,w,body).status_code==422
 x=json.loads(body);s=x['current']['script_generation'];s['content']={'episodes':[{'content':'人工字段'}]*501};s['digest']=digest(s['content']);s['version_id']=None;x['untracked_stages']=['script_generation']
 assert post(c,w,encoded(x)).status_code==422 and files(d)==before

def test_stream_size_limit_ignores_false_content_length(setup,monkeypatch):
 c,_,d,w,body=setup;before=files(d);assert post(c,w,b'').status_code==413
 import core.revision_exports as module
 monkeypatch.setattr(module,'MAX_EXPORT_BYTES',20)
 assert post(c,w,body,headers={'content-type':'application/json','content-length':'1'}).status_code==413 and files(d)==before

def test_missing_or_corrupt_target_is_safe(setup):
 c,_,d,w,body=setup;assert post(c,w,body,project='missing-project').status_code==404
 (d/'sessions/comparison-demo.json').write_text('{broken');r=post(c,w,body);assert r.status_code==503 and 'Traceback' not in r.text
