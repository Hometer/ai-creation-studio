from fastapi.testclient import TestClient
from api.revision_restore_history_demo import create_history_demo

def files(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*') if p.is_file()}

def test_history_demo_seeds_persistent_two_stage_and_partial_restores_once(tmp_path):
    client = TestClient(create_history_demo(tmp_path))
    url = '/api/project/comparison-demo/content-revisions'
    data = client.get(url+'/import-restores').json()
    assert len(data['restores']) == 2
    assert len(data['restores'][0]['version_ids']) == 2
    assert set(data['restores'][1]['version_ids']) == {'script_generation'}
    assert len(data['workspace']['versions']) == 15
    before = files(tmp_path)
    fresh = TestClient(create_history_demo(tmp_path))
    assert fresh.get(url+'/import-restores').json() == data
    assert files(tmp_path) == before

def test_history_reads_compare_and_reviews_are_readonly_and_exactly_bound(tmp_path):
    client = TestClient(create_history_demo(tmp_path))
    url = '/api/project/comparison-demo/content-revisions'
    # Initialize the asset-library coordination lock before the read-only baseline.
    assert client.get(url+'/reviews').status_code == 200
    baseline = files(tmp_path)
    data = client.get(url+'/import-restores').json()
    versions = data['workspace']['versions']
    sources = client.get(url+'/reviews').json()['sources']
    for record in data['restores']:
        for stage, id in record['version_ids'].items():
            after = next(v for v in versions if v['version_id'] == id)
            before = next(v for v in reversed(versions) if v['stage'] == stage and v['number'] < after['number'])
            result = client.get(url+'/compare', params={'expected_token': data['workspace']['token'],
                'before_version_id': before['version_id'], 'after_version_id': id})
            assert result.status_code == 200
            assert result.json()['changes']
            assert result.json()['stage'] == stage
            assert any(s['version_id'] == id for s in sources)
    assert files(tmp_path) == baseline
