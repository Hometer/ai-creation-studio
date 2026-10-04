"""Local execution checks cannot reserve real funds or contact a provider."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from core.execution_checks import ExecutionChecks
from core.model_preflight import check_connection
from test_generation_plans import assert_error, confirm, request_for, seed_references
from test_generation_readiness import (
    budget_payload, fixture, post, price_payload, ready, review_payload,
)


def prepared(fixture, **kwargs):
    response = ready(fixture, **kwargs)
    client = fixture[0]
    preparation = post(client, "/api/wan-preparations", response[-1])
    route = f'/api/wan-preparations/{preparation["preparation_id"]}'
    return preparation, route, response


def create(client, route):
    return post(client, route + "/execution-check", {})


def get(client, check):
    return client.get(f'/api/wan-execution-checks/{check["check_id"]}')


def closed(check):
    assert check["preparation_only"] is True and check["real_execution_allowed"] is False
    assert check["network_used"] is False and check["submit_attempts"] == 0
    assert check["provider_task_id"] is None and check["actual_charged_minor"] is None
    assert check["real_money_reserved"] is False
    assert {"REAL_EXECUTION_DISABLED", "REAL_TRANSPORT_UNAVAILABLE", "AUTHENTICATION_UNVERIFIED",
            "REAL_BILLING_UNCONNECTED"}.issubset(check["execution_blockers"])


@pytest.mark.parametrize("sources", [("offline_fixture", "offline_fixture"), ("manual_account", "manual_setting")])
def test_local_compile_is_persisted_read_only_and_never_dispatches(fixture, monkeypatch, sources):
    import socket
    import requests
    from core.wan_adapter import WanAdapter, request_digest
    from core.wan_http_transport import WanHttpTransport
    from core.model_preflight import inspect_target
    def forbidden(*args, **kwargs): raise AssertionError("Local checks cannot dispatch")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(WanAdapter, "submit", forbidden)
    monkeypatch.setattr(WanHttpTransport, "request", forbidden)
    client, service, plan, _, engine, *_ = fixture
    preparation, route, _ = prepared(fixture, price_source=sources[0], budget_source=sources[1])
    before = service.path.read_bytes()
    project_before = engine.get_status_snapshot("media-demo")
    check = create(client, route)
    closed(check)
    assert check["current"] and check["conditions_ready"] and check["inputs_compiled"]
    assert check["blockers"] == [] and check["current_blockers"] == []
    with service.plans._transaction() as (_, plans):
        stored_plan = plans.plans[plan["plan_id"]]
    _, request = inspect_target(stored_plan.content, stored_plan.content.targets[0], service.plans.archive, True)
    assert list(check["request_sha256"].values()) == [request_digest(request)]
    assert service.path.read_bytes() == before
    assert client.get(route).json() == preparation and engine.get_status_snapshot("media-demo") == project_before
    store = ExecutionChecks(service).path
    stored = store.read_bytes()
    assert get(client, check).json() == check and store.read_bytes() == stored
    for private in (plan["content"]["targets"][0]["prompt"], "data:image", "Authorization", str(store.parent)):
        assert private not in stored.decode()
    restored = ExecutionChecks(service).get(check["check_id"]).model_dump(mode="json")
    assert restored == check


@pytest.mark.parametrize("change,code", [("cancel", "PREPARATION_CANCELLED"),
    ("review", "SAMPLE_REVIEW_CHANGED"), ("price", "PRICE_CHANGED"), ("budget", "BUDGET_CHANGED"),
    ("expire", "QUOTE_EXPIRED"), ("period", "PREPARATION_PERIOD_CHANGED"), ("media", "INPUT_PROTOCOL_INVALID")])
def test_latest_conditions_override_history_and_new_check_cannot_compile(fixture, change, code):
    client, service, plan, clock, _, _, archive, *_ = fixture
    _, route, _ = prepared(fixture)
    original = create(client, route)
    stored = ExecutionChecks(service).path.read_bytes()
    if change == "cancel": client.post(route + "/cancel", json={"expected_revision": 1})
    elif change == "review": post(client, f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', review_payload(plan, rights_confirmed=False))
    elif change == "price": post(client, "/api/wan-prices", price_payload(clock[0], rate_720p_minor=8))
    elif change == "budget": post(client, "/api/wan-budget-policies", budget_payload(single_limit_minor=90))
    elif change == "expire": clock[0] += 1200
    elif change == "period": clock[0] += 86400
    elif change == "media":
        with service.plans._transaction() as (_, plans):
            stored_plan = plans.plans[plan["plan_id"]]
        details = next(b.version.media for b in stored_plan.content.targets[0].bindings if b.role == "first_frame")
        archive.verify_file(details).write_bytes(b"artificial-corrupted-media")
    historical = get(client, original).json()
    assert historical["current"] is False and code in historical["current_blockers"]
    assert historical["inputs_compiled"] is True  # Historical observation is retained.
    assert ExecutionChecks(service).path.read_bytes() == stored
    latest = create(client, route)
    assert latest["inputs_compiled"] is False and code in latest["blockers"] and latest["request_sha256"] == {}
    closed(latest)


def test_connection_classification_changes_invalidate_without_enabling(fixture):
    client, service, *_ = fixture
    _, route, _ = prepared(fixture)
    original = create(client, route)
    service.connection = check_connection("artificial-not-a-real-key", "https://dashscope.aliyuncs.com/api/v1")
    assert get(client, original).json()["current"] is False
    latest = create(client, route)
    closed(latest)
    assert latest["connection"]["credential_present"] and "KEY_MISSING" not in latest["execution_blockers"]
    service.connection = check_connection("artificial-not-a-real-key", "https://dashscope-intl.aliyuncs.com/api/v1")
    assert get(client, latest).json()["current"] is False
    foreign = create(client, route)
    closed(foreign)
    assert "ENDPOINT_NOT_MAINLAND" in foreign["execution_blockers"]


def test_failure_between_recheck_and_compile_drops_all_partial_requests(fixture, monkeypatch):
    import core.execution_checks as module
    from core.model_preflight import issue
    client, *_ = fixture
    _, route, _ = prepared(fixture)
    original = module.inspect_target
    def changed(*args, **kwargs):
        checked, request = original(*args, **kwargs)
        checked.input_protocol_ready = False
        checked.issues.append(issue("MEDIA_UNAVAILABLE"))
        return checked, None
    monkeypatch.setattr(module, "inspect_target", changed)
    report = create(client, route)
    assert report["request_sha256"] == {} and report["inputs_compiled"] is False
    assert "MEDIA_UNAVAILABLE" in report["blockers"]
    closed(report)


@pytest.mark.parametrize("payload", [{"execute": True}, {"real_execution_allowed": True}, {"api_key": "artificial-secret"},
    {"expected_revision": 1}, [], None])
def test_client_cannot_supply_permission_or_credentials(fixture, payload):
    client, service, *_ = fixture
    _, route, _ = prepared(fixture)
    result = client.post(route + "/execution-check", json=payload)
    assert result.status_code == 422 and "artificial-secret" not in result.text
    assert not ExecutionChecks(service).path.exists()


def test_not_found_and_openapi_error_contract(fixture):
    client = fixture[0]
    assert_error(client.post(f'/api/wan-preparations/{uuid4().hex}/execution-check', json={}), 404, "READINESS_NOT_FOUND")
    assert_error(client.get(f'/api/wan-execution-checks/{uuid4().hex}'), 404, "EXECUTION_CHECK_NOT_FOUND")
    assert client.get("/api/wan-execution-checks/invalid").status_code == 422
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/wan-execution-checks/{identifier}"]["get"]["responses"]["503"]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")


@pytest.mark.parametrize("damage", ["future", "missing", "incomplete", "bool_version", "duplicate", "symlink",
    "id", "preparation", "quote", "hash", "compiled", "network", "execution", "submitted", "targets", "revision"])
def test_corrupt_store_returns_safe_error_and_preserves_bytes(fixture, damage, tmp_path):
    client, service, *_ = fixture
    _, route, _ = prepared(fixture)
    report = create(client, route)
    path = ExecutionChecks(service).path
    data = json.loads(path.read_text())
    check = data["checks"][report["check_id"]]
    if damage == "future": data["schema_version"] = 2
    elif damage == "missing": del data["checks"]
    elif damage == "incomplete": del check["network_used"]
    elif damage == "bool_version": data["schema_version"] = True
    elif damage == "id": check["check_id"] = uuid4().hex
    elif damage == "preparation": check["preparation_id"] = uuid4().hex
    elif damage == "quote": check["quote_id"] = uuid4().hex
    elif damage == "hash": check["quote_hash"] = "0" * 64
    elif damage == "compiled": check["request_sha256"] = {}
    elif damage == "network": check["network_used"] = True
    elif damage == "execution": check["real_execution_allowed"] = 0
    elif damage == "submitted": check["submit_attempts"] = False
    elif damage == "targets": check["target_count"] = 2
    elif damage == "revision": check["preparation_revision"] = 2
    path.write_text(json.dumps(data))
    if damage == "duplicate": path.write_text('{"schema_version":1,"schema_version":1,"checks":{}}')
    if damage == "symlink":
        target = tmp_path / "artificial-copy.json"; target.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(target)
    before = path.read_bytes()
    result = get(client, report)
    assert_error(result, 503, "EXECUTION_CHECK_STORE_UNAVAILABLE")
    assert path.read_bytes() == before and str(path) not in result.text
    assert_error(client.post(route + "/execution-check", json={}), 503, "EXECUTION_CHECK_STORE_UNAVAILABLE")
    assert path.read_bytes() == before


def test_atomic_save_failure_preserves_prior_check_and_readiness(fixture, monkeypatch):
    import core.execution_checks as module
    client, service, *_ = fixture
    _, route, _ = prepared(fixture)
    create(client, route)
    path = ExecutionChecks(service).path
    before, readiness = path.read_bytes(), service.path.read_bytes()
    def fail(*args, **kwargs): raise OSError("Artificial write failure")
    monkeypatch.setattr(module, "atomic_write_session", fail)
    assert_error(client.post(route + "/execution-check", json={}), 503, "EXECUTION_CHECK_STORE_UNAVAILABLE")
    assert path.read_bytes() == before and service.path.read_bytes() == readiness


def test_concurrent_reports_are_not_lost_and_do_not_allocate_again(fixture):
    client, service, *_ = fixture
    preparation, _, _ = prepared(fixture)
    readiness = service.path.read_bytes()
    with ThreadPoolExecutor(max_workers=4) as executor:
        reports = list(executor.map(lambda _: ExecutionChecks(service).create(preparation["preparation_id"]), range(8)))
    data = ExecutionChecks(service)._read(service._read())
    assert len(data.checks) == 8 and len({r.check_id for r in reports}) == 8
    assert service.path.read_bytes() == readiness


def test_actual_http_restart_preserves_checks_then_invalidates_after_cancellation(tmp_path):
    from api.media_demo import create_demo
    from api.dependencies import get_workflow_engine
    from test_live_recovery import server
    app = create_demo(tmp_path)
    engine = app.dependency_overrides[get_workflow_engine]()
    engine._session_dir = str(tmp_path); engine.save_session_to_disk("media-demo")
    with server(tmp_path) as client:
        refs = seed_references(client)
        payload = request_for(refs[1], refs[4]); payload["options"]["model"] = "wan2.7-i2v"
        plan = post(client, "/api/generation-plans", payload)
        assert confirm(client, plan).status_code == 200
        post(client, "/api/wan-prices", price_payload(int(time.time())))
        budget = post(client, "/api/wan-budget-policies", budget_payload())
        review = post(client, f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', review_payload(plan))
        quote = post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})
        record = post(client, "/api/wan-preparations", {"quote_id": quote["quote_id"], "quote_hash": quote["quote_hash"],
            "review_id": review["review_id"], "budget_id": budget["budget_id"], "request_id": uuid4().hex, "preparation_only": True})
        route = f'/api/wan-preparations/{record["preparation_id"]}'
        report = create(client, route)
        assert report["inputs_compiled"] and report["current"]
        snapshot = client.get("/api/wan-budget").json()
    with server(tmp_path) as client:
        assert get(client, report).json() == report
        assert client.get("/api/wan-budget").json() == snapshot
        client.post(route + "/cancel", json={"expected_revision": 1})
        denied = create(client, route)
    with server(tmp_path) as client:
        historical = get(client, report).json()
        assert not historical["current"] and "PREPARATION_CANCELLED" in historical["current_blockers"]
        assert get(client, denied).json() == denied and not denied["inputs_compiled"]
        assert client.get("/api/wan-budget").json()["daily_planning_held_minor"] == 0
