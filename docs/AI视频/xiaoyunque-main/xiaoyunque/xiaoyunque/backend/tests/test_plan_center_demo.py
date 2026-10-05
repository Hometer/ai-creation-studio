"""Real plan persistence and the isolated UI acceptance boundary."""
from fastapi.testclient import TestClient

from api.dependencies import get_workflow_engine
from api.plan_center_demo import create_plan_center_demo


def test_offline_plan_center_restarts_with_fixed_versions_and_confirmation(tmp_path):
    app = create_plan_center_demo(tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/project/media-demo/status').status_code == 200
        assert client.get('/api/project/media-demo/generation-plans').json() == []
        refs = client.get('/api/project/media-demo/asset-references').json()
        assets = client.get('/api/assets').json()
        image = next(r for r in refs if next(a for a in assets if a['asset_id'] == r['asset_id'])['kind'] == 'image')
        plan = client.post('/api/generation-plans', json={'project_id': 'media-demo', 'options': {'model': 'local-test-model', 'mode': 'first_frame'}, 'targets': [{'segment_id': 'seg-1', 'duration': 5, 'bindings': [{'reference_id': image['reference_id'], 'role': 'first_frame'}]}]}).json()
        assert plan['status'] == 'draft'
        confirmed = client.post(f"/api/generation-plans/{plan['plan_id']}/confirm", json={'content_hash': plan['content_hash'], 'local_only': True}).json()
        assert confirmed['status'] == 'confirmed_local'
        assert confirmed['execution_allowed'] is False
        assert client.post(f"/api/generation-plans/{plan['plan_id']}/dry-run").status_code == 200
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*.json')}
    with TestClient(create_plan_center_demo(tmp_path)) as client:
        assert client.get('/api/project/media-demo/generation-plans').json() == [confirmed]
        restored = client.get('/api/assets').json()
        assert len(restored) == 2
        assert len(next(a for a in restored if a['kind'] == 'image')['versions']) == 2
        assert len({r['version_id'] for r in refs if r['asset_id'] == image['asset_id']}) == 2
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*.json')} == before


def test_plan_demo_has_no_execution_or_task_routes_and_keeps_orphan_history(tmp_path):
    app = create_plan_center_demo(tmp_path)
    paths = list(app.openapi()['paths'])
    assert not any(p.startswith(('/api/generation-tasks', '/api/generation-quotes', '/api/wan-', '/api/sandbox', '/api/config')) for p in paths)
    with TestClient(app) as client:
        assert client.post('/api/project/media-demo/run').status_code == 404
        assert client.post('/api/generation-tasks').status_code == 404
        engine = app.dependency_overrides[get_workflow_engine]()
        engine.delete_session('media-demo')
        result = client.get('/api/project/media-demo/status')
        assert result.status_code == 404 and result.json()['error']['code'] == 'NOT_FOUND'
        assert client.get('/api/project/media-demo/generation-plans').json() == []
