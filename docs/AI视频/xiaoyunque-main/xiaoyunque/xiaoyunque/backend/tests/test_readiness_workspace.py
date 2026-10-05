"""Scoped read-only snapshots, denied transport, stale history and durable demos."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_workflow_engine
from api.readiness_center_demo import create_readiness_center_demo
from test_generation_readiness import fixture, ready, post, review_payload
from test_generation_plans import assert_error, confirm, request_for


def workspace(client, plan):
    response = client.get(f'/api/generation-plans/{plan["plan_id"]}/readiness')
    assert response.status_code == 200, response.text
    return response.json()


def test_snapshot_get_is_read_only_and_does_not_dispatch(fixture, monkeypatch):
    import requests
    from core.wan_adapter import WanAdapter
    def forbidden(*args, **kwargs):
        raise AssertionError('No transport in local snapshots')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(WanAdapter, 'submit', forbidden)
    monkeypatch.setattr('socket.create_connection', forbidden)
    client, _, plan, _, _, _, _, _, _, folder = fixture
    before = {str(p): p.read_bytes() for p in folder.rglob('*.json')}
    first = workspace(client, plan)
    assert first['current_price'] is None and first['budget']['policy'] is None
    assert first['quotes'] == first['preparations'] == first['reviews'] == first['checks'] == []
    assert workspace(client, plan) == first
    assert {str(p): p.read_bytes() for p in folder.rglob('*.json')} == before


def test_history_scope_associations_and_saved_checks(fixture):
    client, service, plan, _, _, _, _, refs, *_ = fixture
    *_, payload = ready(fixture)
    preparation = post(client, '/api/wan-preparations', payload)
    check = post(client, f'/api/wan-preparations/{preparation["preparation_id"]}/execution-check', {})
    second = post(client, '/api/generation-plans', request_for(refs[1], refs[4]))
    post(client, '/api/wan-quotes', {'plan_id': second['plan_id']})
    first = workspace(client, plan)
    assert first['project_exists']
    assert first['preparations'] == [preparation] and first['checks'] == [check]
    assert len(first['quotes']) == len(first['reviews']) == 1
    other = workspace(client, second)
    assert other['preparations'] == other['checks'] == other['reviews'] == []
    assert all(q['terms']['plan_id'] == second['plan_id'] for q in other['quotes'])
    assert first['budget']['daily_planning_held_minor'] == 35  # Shared, not scoped to the plan.
    assert service._read().preparations[preparation['preparation_id']].submit_attempts == 0


def test_negative_review_and_cancel_preserve_history_release_allocation(fixture):
    client, _, plan, *_ = fixture
    *_, payload = ready(fixture)
    p = post(client, '/api/wan-preparations', payload)
    post(client, f'/api/wan-preparations/{p["preparation_id"]}/execution-check', {})
    post(client, f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', review_payload(plan, rights_confirmed=False))
    snapshot = workspace(client, plan)
    assert not snapshot['preparations'][0]['local_preparation_ready']
    assert not snapshot['checks'][0]['current']
    assert snapshot['budget']['daily_planning_held_minor'] == 35
    result = client.post(f'/api/wan-preparations/{p["preparation_id"]}/cancel', json={'expected_revision': 1})
    assert result.status_code == 200
    snapshot = workspace(client, plan)
    assert snapshot['preparations'][0]['state'] == 'cancelled'
    assert snapshot['budget']['daily_planning_held_minor'] == 0
    assert len(snapshot['reviews']) == 2 and len(snapshot['checks']) == 1


def test_deleted_project_keeps_history_and_allows_explicit_local_cancel(fixture):
    client, _, plan, _, engine, *_ = fixture
    *_, payload = ready(fixture)
    p = post(client, '/api/wan-preparations', payload)
    engine.delete_session('media-demo')
    snapshot = workspace(client, plan)
    assert not snapshot['project_exists'] and not snapshot['plan']['current']
    assert 'PLAN_SOURCE_CHANGED' in snapshot['preparations'][0]['blockers']
    assert client.post(f'/api/wan-preparations/{p["preparation_id"]}/cancel', json={'expected_revision': 1}).status_code == 200
    assert workspace(client, plan)['budget']['daily_available_minor'] == 100


def test_expired_estimate_stays_visible_and_check_becomes_stale(fixture):
    client, _, plan, clock, *_ = fixture
    *_, payload = ready(fixture)
    p = post(client, '/api/wan-preparations', payload)
    post(client, f'/api/wan-preparations/{p["preparation_id"]}/execution-check', {})
    clock[0] += 1200
    snapshot = workspace(client, plan)
    assert snapshot['quotes'][0]['expired']
    assert 'QUOTE_EXPIRED' in snapshot['preparations'][0]['blockers']
    assert not snapshot['checks'][0]['current']
    assert snapshot['budget']['daily_planning_held_minor'] == 35


@pytest.mark.parametrize('store', ['readiness', 'checks'])
def test_corrupt_store_is_safe_unavailable_without_repair_or_loss(fixture, store):
    client, service, plan, *_ = fixture
    path = service.path if store == 'readiness' else service.path.parent / 'execution-checks.json'
    path.write_text('{broken')
    response = client.get(f'/api/generation-plans/{plan["plan_id"]}/readiness')
    assert_error(response, 503, 'READINESS_STORE_UNAVAILABLE' if store == 'readiness' else 'EXECUTION_CHECK_STORE_UNAVAILABLE')
    assert path.read_text() == '{broken'
    assert 'broken' not in response.text and str(path) not in response.text


def test_missing_plan_is_404(fixture):
    client, *_ = fixture
    assert_error(client.get(f'/api/generation-plans/{uuid4().hex}/readiness'), 404, 'PLAN_NOT_FOUND')


def test_isolated_demo_restores_preparation_and_has_no_generation_routes(tmp_path):
    app = create_readiness_center_demo(tmp_path)
    paths = app.openapi()['paths']
    assert not any(p.startswith(('/api/generation-tasks', '/api/generation-quotes', '/api/config', '/api/sandbox')) for p in paths)
    assert not any(p.endswith(('/run', '/intervene')) for p in paths)
    with TestClient(app) as client:
        refs = client.get('/api/project/media-demo/asset-references').json()
        plan = post(client, '/api/generation-plans', {'project_id': 'media-demo', 'options': {'model': 'wan2.7-i2v', 'mode': 'first_frame', 'resolution': '720P', 'sound': 'on'}, 'targets': [{'segment_id': 'seg-1', 'duration': 5, 'bindings': [{'reference_id': refs[0]['reference_id'], 'role': 'first_frame'}]}]})
        assert confirm(client, plan).status_code == 200
        import time
        price = post(client, '/api/wan-prices', {'model': 'wan2.7-i2v', 'source_kind': 'manual_account', 'rate_720p_minor': 7, 'rate_1080p_minor': 11, 'expires_at': int(time.time())+7200})
        budget = post(client, '/api/wan-budget-policies', {'source_kind': 'manual_setting', 'single_limit_minor': 100, 'daily_limit_minor': 200, 'monthly_limit_minor': 300, 'preparation_only': True})
        review = post(client, f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', review_payload(plan))
        quote = post(client, '/api/wan-quotes', {'plan_id': plan['plan_id']})
        p = post(client, '/api/wan-preparations', {'quote_id': quote['quote_id'], 'quote_hash': quote['quote_hash'], 'review_id': review['review_id'], 'budget_id': budget['budget_id'], 'request_id': uuid4().hex, 'preparation_only': True})
        post(client, f'/api/wan-preparations/{p["preparation_id"]}/execution-check', {})
        old = workspace(client, plan)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*.json')}
    with TestClient(create_readiness_center_demo(tmp_path)) as client:
        assert workspace(client, plan) == old
        assert client.post('/api/project/media-demo/run').status_code == 404
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*.json')} == before
