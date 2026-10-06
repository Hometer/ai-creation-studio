"""Real HTTP history workflow against isolated deterministic human records."""
import json
import uuid
from fastapi.testclient import TestClient
from api.co_creation_draft_history_demo import create_history_demo

URL='/api/project/comparison-demo/co-creation/drafts'
def files(directory):return {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}

def test_history_demo_seeds_two_scoped_histories_once_and_reads_without_mutation(tmp_path):
    c=TestClient(create_history_demo(tmp_path));w=c.get(URL).json();before=files(tmp_path)
    assert len(w['drafts'])==5 and len(w['data']['revisions']['versions'])==8 and w['data']['applications']==[]
    first,second=w['data']['workspace']['records'];assert [d['value'] for d in w['drafts'] if d['intent_id']==first['intent_id']]==['人工历史草稿：雾港来信','','人工最近草稿：雾散之后，再一起出发。']
    assert len([d for d in w['drafts'] if d['intent_id']==second['intent_id']])==2
    assert c.get(URL).json()==w and files(tmp_path)==before
    restored=TestClient(create_history_demo(tmp_path));assert restored.get(URL).json()==w and files(tmp_path)==before

def test_restore_old_and_empty_then_append_does_not_replace_history_text_or_versions(tmp_path):
    c=TestClient(create_history_demo(tmp_path));initial=c.get(URL).json();old_rows=initial['drafts'];history=initial['data']['revisions'];project=tmp_path/'sessions/comparison-demo.json';artifacts=json.loads(project.read_text())['artifacts']
    for source in [old_rows[0],old_rows[1]]:
        w=c.get(URL).json();payload=dict(expected_token=w['data']['workspace']['token'],intent_id=source['intent_id'],value=source['value'],request_id=uuid.uuid4().hex)
        response=c.post(URL,json=payload);assert response.status_code==201;result=response.json()['result']
        assert result['drafts'][:len(old_rows)]==old_rows and result['drafts'][-1]['value']==source['value'] and result['drafts'][-1]['draft_id']!=source['draft_id']
        assert result['data']['revisions']==history and json.loads(project.read_text())['artifacts']==artifacts
        before=files(tmp_path);assert c.post(URL,json=payload).json()==response.json() and files(tmp_path)==before
    before=files(tmp_path);fresh=TestClient(create_history_demo(tmp_path));assert fresh.get(URL).json()==c.get(URL).json() and files(tmp_path)==before
