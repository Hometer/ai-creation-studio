"""Unified history reads preserve original task, budget, and result checkpoints."""

import json

import pytest

from test_generation_tasks import fixture, finish, queued, quote_for, task_for
from test_generation_plans import assert_error


def test_empty_task_center_returns_list_without_creating_task_store(fixture):
    client, service, *_ = fixture
    assert not service.path.exists()
    response = client.get('/api/generation-tasks')
    assert response.status_code == 200
    assert response.json() == []
    assert not service.path.exists()


def test_history_reads_do_not_advance_tasks_or_change_budget(fixture, monkeypatch):
    client, service, provider, *_ = fixture
    task, _ = queued(fixture)
    before = service.path.read_bytes()
    def unexpected(*args, **kwargs):
        pytest.fail('A history read must never call the provider')
    for name in ['submit', 'query', 'fetch_result']:
        monkeypatch.setattr(provider, name, unexpected)
    for _ in range(3):
        response = client.get('/api/generation-tasks')
        assert response.status_code == 200
        assert response.json() == [task]
    assert service.path.read_bytes() == before
    assert not (provider.directory / 'simulation-provider.json').exists()


def test_sorted_history_retains_projectless_tasks_and_archived_results(fixture):
    client, service, _, _, engine, _ = fixture
    first, _ = queued(fixture)
    done = finish(client, first)
    second = task_for(client, quote_for(fixture))
    document = json.loads(service.path.read_text())
    document['tasks'][first['task_id']]['created_at'] = '2026-10-04T00:00:00+00:00'
    document['tasks'][second['task_id']]['created_at'] = '2026-10-05T00:00:00+00:00'
    service.path.write_text(json.dumps(document))
    engine.delete_session(first['project_id'])
    before = service.path.read_bytes()
    response = client.get('/api/generation-tasks')
    assert response.status_code == 200
    rows = response.json()
    assert [r['task_id'] for r in rows] == [second['task_id'], first['task_id']]
    assert rows[1]['result_url'] == done['result_url']
    assert client.get(rows[1]['result_url']).status_code == 200
    assert service.path.read_bytes() == before


@pytest.mark.parametrize('raw', ['{broken', '{"schema_version":99,"tasks":{}}'])
def test_unreadable_history_is_safe_error_and_preserves_original_file(fixture, raw):
    client, service, *_ = fixture
    service.path.write_text(raw)
    response = client.get('/api/generation-tasks')
    assert_error(response, 503, 'TASK_STORE_UNAVAILABLE')
    assert service.path.read_text() == raw
    assert 'Traceback' not in response.text


def test_history_get_remains_available_with_execution_disabled(fixture):
    from api.routers.generation_tasks import get_task_provider
    client, service, _, _, _, app = fixture
    app.dependency_overrides[get_task_provider] = lambda: None
    task = task_for(client, quote_for(fixture))
    before = service.path.read_bytes()
    rows = client.get('/api/generation-tasks').json()
    assert rows == [task]
    assert rows[0]['real_execution_allowed'] is False
    assert rows[0]['simulation_only'] is False
    assert rows[0]['billing']['billed_minor'] is None
    assert service.path.read_bytes() == before


def test_isolated_demo_restart_keeps_artificial_history_without_reseeding(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api.task_center_demo import create_task_center_demo
    from pipelines import storage
    monkeypatch.setattr(storage, 'TASK_DATA_DIR', storage.TASK_DATA_DIR)
    monkeypatch.setattr(storage, 'TASK_RESULT_DIR', storage.TASK_RESULT_DIR)
    app = create_task_center_demo(tmp_path)
    with TestClient(app) as client:
        before = client.get('/api/generation-tasks').json()
        assert {t['status'] for t in before} == {'blocked', 'queued', 'running', 'succeeded'}
        assert all(t['simulation_only'] for t in before)
        assert len(client.get('/api/tasks').json()['tasks']) == 2
        assert client.get('/api/sandbox/tasks').json()['tasks'] == []
        assert client.post('/api/sandbox/generate').status_code == 404
    stored = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*.json')}
    with TestClient(create_task_center_demo(tmp_path)) as client:
        assert client.get('/api/generation-tasks').json() == before
    assert {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob('*.json')} == stored
