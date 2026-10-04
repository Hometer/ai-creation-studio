"""Local planning only, with isolated images and actual HTTP restart coverage."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_workflow_engine
from api.media_demo import create_demo
from api.routers.assets import get_asset_library, get_media_archive
from api.routers.generation_readiness import get_generation_readiness
from api.schemas.generation_readiness import PreparationInput
from core.generation_plans import GenerationPlans
from core.generation_readiness import GenerationReadiness, periods
from core.model_preflight import check_connection
from test_generation_plans import assert_error, confirm, request_for, seed_references

NOW = int(datetime(2026, 10, 4, 13, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def fixture(tmp_path):
    app = create_demo(tmp_path)
    engine = app.dependency_overrides[get_workflow_engine]()
    library = app.dependency_overrides[get_asset_library]()
    archive = app.dependency_overrides[get_media_archive]()
    clock = [NOW]
    service = GenerationReadiness(GenerationPlans(engine, library, archive),
        check_connection("", "https://dashscope.aliyuncs.com/api/v1"), clock=lambda: clock[0])
    app.dependency_overrides[get_generation_readiness] = lambda: service
    with TestClient(app, raise_server_exceptions=False) as client:
        refs = seed_references(client)
        payload = request_for(refs[1], refs[4])
        payload["options"]["model"] = "wan2.7-i2v"
        response = client.post("/api/generation-plans", json=payload)
        assert response.status_code == 201
        plan = response.json()
        assert confirm(client, plan).status_code == 200
        yield client, service, plan, clock, engine, library, archive, refs, app, tmp_path


def price_payload(clock=NOW, **changes):
    return {"model": "wan2.7-i2v", "source_kind": "offline_fixture", "rate_720p_minor": 7,
        "rate_1080p_minor": 11, "expires_at": clock + 7200, **changes}


def budget_payload(**changes):
    return {"source_kind": "offline_fixture", "single_limit_minor": 100, "daily_limit_minor": 100,
        "monthly_limit_minor": 200, "preparation_only": True, **changes}


def review_payload(plan, **changes):
    return {"content_hash": plan["content_hash"], "rights_confirmed": True, "non_sensitive_confirmed": True,
        "mainland_only": True, "preparation_only": True, **changes}


def post(client, url, payload):
    response = client.post(url, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def ready(fixture, price_source="offline_fixture", budget_source="offline_fixture", limits=None):
    client, _, plan, clock, *_ = fixture
    price = post(client, "/api/wan-prices", price_payload(clock[0], source_kind=price_source))
    budget = post(client, "/api/wan-budget-policies", budget_payload(source_kind=budget_source, **(limits or {})))
    review = post(client, f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', review_payload(plan))
    quote = post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})
    payload = {"quote_id": quote["quote_id"], "quote_hash": quote["quote_hash"], "review_id": review["review_id"],
        "budget_id": budget["budget_id"], "request_id": uuid4().hex, "preparation_only": True}
    return price, budget, review, quote, payload


def new_request(client, plan, original):
    quote = post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})
    return {**original, "quote_id": quote["quote_id"], "quote_hash": quote["quote_hash"], "request_id": uuid4().hex}


def test_unknown_price_budget_and_no_model_calls(fixture, monkeypatch):
    import requests
    from core.wan_adapter import WanAdapter

    def forbidden(*args, **kwargs): raise AssertionError("Local planning cannot invoke a transport")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(WanAdapter, "submit", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    client, service, plan, *_ = fixture
    quote = post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})
    assert quote["terms"]["pricing_kind"] == "unavailable"
    assert quote["terms"]["amount_minor"] is None and quote["terms"]["currency"] is None
    assert "PRICE_UNAVAILABLE" in quote["blockers"] and quote["real_execution_allowed"] is False
    budget = client.get("/api/wan-budget").json()
    assert budget["policy"] is None and budget["daily_available_minor"] is None
    _, _, _, _, payload = ready(fixture)
    payload.update(quote_id=quote["quote_id"], quote_hash=quote["quote_hash"])
    assert_error(client.post("/api/wan-preparations", json=payload), 409, "PRICE_UNAVAILABLE")
    assert not service._read().preparations


@pytest.mark.parametrize("kind,source,budget_source", [("offline_fixture", "offline_fixture", "offline_fixture"),
    ("manual_estimate", "manual_account", "manual_setting")])
def test_estimate_review_and_allocation_are_persistent_but_execution_closed(fixture, kind, source, budget_source):
    client, service, plan, _, engine, *_ = fixture
    before = engine.get_status_snapshot("media-demo")
    price, budget, review, quote, payload = ready(fixture, source, budget_source)
    assert quote["terms"]["amount_minor"] == 35 and quote["terms"]["rate_minor"] == 7
    assert quote["terms"]["currency"] == "CNY" and quote["terms"]["pricing_kind"] == kind
    assert price["account_price_verified"] is False and quote["account_price_verified"] is False
    assert review["accepted"] and review["current"]
    record = post(client, "/api/wan-preparations", payload)
    assert record["state"] == "prepared" and record["local_preparation_ready"]
    assert record["planning_held_minor"] == 35 and record["actual_charged_minor"] is None
    assert record["real_money_reserved"] is False and record["submit_attempts"] == 0
    assert record["provider_task_id"] is None and record["real_execution_allowed"] is False
    assert "REAL_EXECUTION_DISABLED" in record["execution_blockers"] and "AUTHENTICATION_UNVERIFIED" in record["execution_blockers"]
    view = client.get("/api/wan-budget").json()
    assert view["daily_planning_held_minor"] == 35 and view["daily_available_minor"] == 65
    assert view["actual_charged_minor"] is None
    assert client.get(f'/api/wan-preparations/{record["preparation_id"]}').json() == record
    assert engine.get_status_snapshot("media-demo") == before
    content = service.path.read_text()
    for private in (plan["content"]["targets"][0]["prompt"], "data:image", "Authorization", str(service.path.parent)):
        assert private not in content


def test_precision_multiple_targets_and_full_hash(fixture):
    client, _, plan, _, engine, _, _, refs, *_ = fixture
    segment = copy.deepcopy(engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0])
    segment["segment_id"] = "seg-2"
    engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"].append(segment)
    engine.save_session_to_disk("media-demo")
    payload = request_for(refs[1], refs[4])
    payload["options"].update(model="wan2.7-i2v", resolution="1080P")
    second = copy.deepcopy(payload["targets"][0])
    second.update(segment_id="seg-2", duration=3)
    # The first-frame reference is pinned to shot 1 of seg-1; make an explicit seg-2 reference.
    reference = post(client, "/api/asset-references", {"project_id": "media-demo", "segment_id": "seg-2", "shot_number": 1,
        "asset_id": refs[0]["asset_id"], "version_id": refs[0]["versions"][0]["version_id"]})
    second["bindings"][0]["reference_id"] = reference["reference_id"]
    payload["targets"].append(second)
    plan = post(client, "/api/generation-plans", payload)
    assert confirm(client, plan).status_code == 200
    post(client, "/api/wan-prices", price_payload())
    quote = post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})
    assert quote["terms"]["amount_minor"] == 88 and len(quote["terms"]["lines"]) == 2
    assert quote["terms"]["content_hash"] == plan["content_hash"]


@pytest.mark.parametrize("field", ["rights_confirmed", "non_sensitive_confirmed", "mainland_only"])
def test_each_negative_attestation_saved_and_blocks_preparation(fixture, field):
    client, service, plan, *_ = fixture
    *_, payload = ready(fixture)
    review = post(client, f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', review_payload(plan, **{field: False}))
    assert not review["accepted"] and review["current"]
    payload["review_id"] = review["review_id"]
    assert_error(client.post("/api/wan-preparations", json=payload), 409, "SAMPLE_REVIEW_REQUIRED")
    assert not service._read().preparations


def test_new_denial_invalidates_old_review_without_releasing_hold(fixture):
    client, _, plan, *_ = fixture
    _, _, old, _, payload = ready(fixture)
    record = post(client, "/api/wan-preparations", payload)
    post(client, f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', review_payload(plan, rights_confirmed=False))
    assert client.get(f'/api/wan-sample-reviews/{old["review_id"]}').json()["current"] is False
    view = client.get(f'/api/wan-preparations/{record["preparation_id"]}').json()
    assert not view["local_preparation_ready"] and "SAMPLE_REVIEW_CHANGED" in view["blockers"]
    assert client.get("/api/wan-budget").json()["daily_planning_held_minor"] == 35


@pytest.mark.parametrize("change,code", [("price", "PRICE_CHANGED"), ("budget", "BUDGET_CHANGED"),
    ("expiry", "QUOTE_EXPIRED"), ("source", "PLAN_SOURCE_CHANGED"), ("media", "INPUT_PROTOCOL_INVALID")])
def test_changed_preparation_conditions_block_new_confirmation(fixture, change, code):
    client, service, plan, clock, engine, _, archive, refs, *_ = fixture
    *_, payload = ready(fixture)
    if change == "price": post(client, "/api/wan-prices", price_payload(rate_720p_minor=8))
    elif change == "budget": post(client, "/api/wan-budget-policies", budget_payload())
    elif change == "expiry": clock[0] += 1200
    elif change == "source":
        engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]["shots"][0]["content"] = "修订后的人工分镜"
        engine.save_session_to_disk("media-demo")
    else:
        from api.schemas.assets import MediaInfo
        media = MediaInfo.model_validate(refs[0]["versions"][0]["media"])
        archive.path(media).write_bytes(b"damaged artificial image")
    assert_error(client.post("/api/wan-preparations", json=payload), 409, code)
    assert not service._read().preparations


def test_price_expiry_caps_quote_and_unknown_model_not_priced(fixture):
    client, _, plan, clock, *_ = fixture
    post(client, "/api/wan-prices", price_payload(expires_at=NOW + 100))
    quote = post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})
    assert quote["terms"]["expires_at"] == NOW + 100
    clock[0] += 100
    view = client.get(f'/api/wan-quotes/{quote["quote_id"]}').json()
    assert {"PRICE_EXPIRED", "QUOTE_EXPIRED"} <= set(view["blockers"])
    assert post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})["terms"]["amount_minor"] is None
    assert_error(client.post("/api/wan-prices", json=price_payload(expires_at=clock[0])), 409, "PRICE_EXPIRED")
    post(client, "/api/wan-prices", price_payload(clock[0], model="wan2.7-i2v-2026-04-25"))
    assert post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})["terms"]["amount_minor"] is None


@pytest.mark.parametrize("limit,expected", [("single", "BUDGET_SINGLE_EXCEEDED"), ("daily", "BUDGET_DAILY_EXCEEDED"),
                                         ("monthly", "BUDGET_MONTHLY_EXCEEDED")])
def test_single_daily_monthly_caps(fixture, limit, expected):
    client, _, plan, clock, *_ = fixture
    limits = {"single_limit_minor": 30} if limit == "single" else {"single_limit_minor": 35, "daily_limit_minor": 60, "monthly_limit_minor": 60 if limit == "monthly" else 200}
    *_, payload = ready(fixture, limits=limits)
    if limit != "single":
        post(client, "/api/wan-preparations", payload)
        if limit == "monthly":
            clock[0] += 86400
            post(client, "/api/wan-prices", price_payload(clock[0]))
        payload = new_request(client, plan, payload)
    assert_error(client.post("/api/wan-preparations", json=payload), 409, expected)


def test_shanghai_day_month_rollover_and_old_period_reapproval(fixture):
    client, _, plan, clock, *_ = fixture
    clock[0] = int(datetime(2026, 10, 31, 15, 59, 59, tzinfo=timezone.utc).timestamp())
    *_, payload = ready(fixture)
    record = post(client, "/api/wan-preparations", payload)
    assert record["period_day"] == "2026-10-31"
    clock[0] += 1
    assert periods(clock[0]) == ("2026-11-01", "2026-11")
    budget = client.get("/api/wan-budget").json()
    assert budget["daily_planning_held_minor"] == budget["monthly_planning_held_minor"] == 0
    view = client.get(f'/api/wan-preparations/{record["preparation_id"]}').json()
    assert "PREPARATION_PERIOD_CHANGED" in view["blockers"] and not view["local_preparation_ready"]


def test_idempotent_preparation_quote_uniqueness_and_cancel_release(fixture):
    client, _, plan, *_ = fixture
    *_, payload = ready(fixture)
    record = post(client, "/api/wan-preparations", payload)
    assert post(client, "/api/wan-preparations", payload) == record
    assert_error(client.post("/api/wan-preparations", json={**payload, "request_id": uuid4().hex}), 409, "QUOTE_ALREADY_PREPARED")
    other = new_request(client, plan, payload)
    other["request_id"] = payload["request_id"]
    assert_error(client.post("/api/wan-preparations", json=other), 409, "PREPARATION_REQUEST_CONFLICT")
    route = f'/api/wan-preparations/{record["preparation_id"]}/cancel'
    assert_error(client.post(route, json={"expected_revision": 2}), 409, "PREPARATION_REVISION_CHANGED")
    cancelled = client.post(route, json={"expected_revision": 1}).json()
    assert cancelled["state"] == "cancelled" and cancelled["revision"] == 2
    assert cancelled["planning_held_minor"] == 35 and cancelled["active_planning_held_minor"] == 0
    assert client.post(route, json={"expected_revision": 1}).json() == cancelled
    assert post(client, "/api/wan-preparations", payload) == cancelled
    assert client.get("/api/wan-budget").json()["daily_planning_held_minor"] == 0


def test_cannot_reduce_budget_below_current_hold_or_mix_fixture(fixture):
    client, *_ = fixture
    *_, payload = ready(fixture)
    post(client, "/api/wan-preparations", payload)
    assert_error(client.post("/api/wan-budget-policies", json=budget_payload(single_limit_minor=0, daily_limit_minor=0, monthly_limit_minor=0)), 409, "BUDGET_BELOW_HELD")
    *_, payload = ready(fixture, "manual_account", "offline_fixture")
    assert_error(client.post("/api/wan-preparations", json=payload), 409, "PRICING_SOURCE_MISMATCH")


def test_concurrent_allocations_do_not_exceed_cap(fixture):
    client, service, plan, *_ = fixture
    *_, payload = ready(fixture, limits={"single_limit_minor": 35, "daily_limit_minor": 35, "monthly_limit_minor": 35})
    another = new_request(client, plan, payload)

    def prepare(p):
        from core.asset_library import AssetLibraryError
        try: return service.prepare(PreparationInput.model_validate(p)).state
        except AssetLibraryError as error: return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(prepare, [payload, another]))
    assert sorted(result) == ["BUDGET_DAILY_EXCEEDED", "prepared"]
    assert service.get_budget().daily_planning_held_minor == 35


@pytest.mark.parametrize("route,field,value", [
    ("wan-prices", "rate_720p_minor", True), ("wan-prices", "rate_720p_minor", 0),
    ("wan-prices", "rate_720p_minor", 0.1), ("wan-prices", "expires_at", True),
    ("wan-prices", "api_key", "private-marker"), ("wan-prices", "endpoint", "https://private.example"),
    ("wan-budget-policies", "daily_limit_minor", -1), ("wan-budget-policies", "single_limit_minor", True),
    ("wan-budget-policies", "preparation_only", False), ("wan-budget-policies", "preparation_only", 1),
    ("wan-budget-policies", "real_execution_allowed", True),
])
def test_strict_mutation_contract_no_client_execution_switch(fixture, route, field, value):
    client = fixture[0]
    payload = price_payload() if route == "wan-prices" else budget_payload()
    payload[field] = value
    assert_error(client.post(f"/api/{route}", json=payload), 422)


@pytest.mark.parametrize("field,value", [("quote_hash", "0" * 64), ("review_id", "0" * 32), ("budget_id", "0" * 32),
    ("preparation_only", False), ("preparation_only", 1), ("execute", True)])
def test_invalid_preparation_identifiers_and_acknowledgment(fixture, field, value):
    client = fixture[0]
    *_, payload = ready(fixture)
    payload[field] = value
    expected = 409 if field == "quote_hash" else 404 if field in {"review_id", "budget_id"} else 422
    assert_error(client.post("/api/wan-preparations", json=payload), expected)


@pytest.mark.parametrize("damage", ["broken", "future", "boolean", "duplicate", "link", "missing", "price", "hash", "review", "hold", "period", "execute", "submit"])
def test_corrupt_store_is_preserved(fixture, damage):
    client, service, *_ = fixture
    *_, payload = ready(fixture)
    post(client, "/api/wan-preparations", payload)
    path = service.path
    data = json.loads(path.read_text())
    if damage == "broken": path.write_text("{broken")
    elif damage == "future": data["schema_version"] = 2
    elif damage == "boolean": data["schema_version"] = True
    elif damage == "duplicate": path.write_text('{"schema_version":1,"schema_version":1}')
    elif damage == "link":
        target = path.with_name("previous.json"); path.rename(target); path.symlink_to(target)
    elif damage == "missing": del next(iter(data["preparations"].values()))["planning_held_minor"]
    elif damage == "price": next(iter(data["prices"].values()))["rate_720p_minor"] += 1
    elif damage == "hash": next(iter(data["quotes"].values()))["quote_hash"] = "0" * 64
    elif damage == "review": next(iter(data["reviews"].values()))["accepted"] = False
    elif damage == "hold": next(iter(data["preparations"].values()))["planning_held_minor"] = 0
    elif damage == "period": next(iter(data["preparations"].values()))["period_day"] = "2026-10-05"
    elif damage == "execute": next(iter(data["preparations"].values()))["real_money_reserved"] = True
    else: next(iter(data["preparations"].values()))["submit_attempts"] = False
    if damage not in {"broken", "duplicate", "link"}: path.write_text(json.dumps(data))
    before = path.read_bytes()
    assert_error(client.get("/api/wan-budget"), 503, "READINESS_STORE_UNAVAILABLE")
    assert path.read_bytes() == before


def test_write_failure_does_not_allocate_or_destroy_prior_state(fixture, monkeypatch):
    client, service, *_ = fixture
    *_, payload = ready(fixture)
    before = service.path.read_bytes()
    def fail(*args, **kwargs): raise OSError("private-marker")
    monkeypatch.setattr("core.generation_readiness.atomic_write_session", fail)
    assert_error(client.post("/api/wan-preparations", json=payload), 503, "READINESS_STORE_UNAVAILABLE")
    assert service.path.read_bytes() == before
    assert client.get("/api/wan-budget").json()["daily_planning_held_minor"] == 0


def test_draft_plan_and_wrong_review_digest_not_approved(fixture):
    client, service, plan, _, _, _, _, refs, *_ = fixture
    ready(fixture)
    payload = request_for(refs[1], refs[4]); payload["options"]["model"] = "wan2.7-i2v"
    draft = post(client, "/api/generation-plans", payload)
    quote = post(client, "/api/wan-quotes", {"plan_id": draft["plan_id"]})
    assert "PLAN_NOT_CONFIRMED" in quote["blockers"]
    review = post(client, f'/api/generation-plans/{draft["plan_id"]}/sample-reviews', review_payload(draft))
    payload = {"quote_id": quote["quote_id"], "quote_hash": quote["quote_hash"], "review_id": review["review_id"],
        "budget_id": service._read().current_budget_id, "request_id": uuid4().hex, "preparation_only": True}
    assert_error(client.post("/api/wan-preparations", json=payload), 409, "PLAN_NOT_CONFIRMED")
    assert_error(client.post(f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', json=review_payload(plan, content_hash="0" * 64)), 409, "PLAN_SOURCE_CHANGED")


def test_global_budget_includes_other_projects(fixture):
    client, _, plan, _, engine, _, _, refs, *_ = fixture
    *_, payload = ready(fixture, limits={"single_limit_minor": 35, "daily_limit_minor": 35, "monthly_limit_minor": 70})
    post(client, "/api/wan-preparations", payload)
    engine.create_session("other", {"idea": "隔离人工项目"})
    engine.get_state("other").artifacts = copy.deepcopy(engine.get_state("media-demo").artifacts)
    engine.save_session_to_disk("other")
    reference = post(client, "/api/asset-references", {"project_id": "other", "segment_id": "seg-1", "shot_number": 1,
        "asset_id": refs[0]["asset_id"], "version_id": refs[0]["versions"][0]["version_id"]})
    new_plan_payload = request_for(reference); new_plan_payload["project_id"] = "other"
    new_plan_payload["options"]["model"] = "wan2.7-i2v"
    other = post(client, "/api/generation-plans", new_plan_payload)
    assert confirm(client, other).status_code == 200
    review = post(client, f'/api/generation-plans/{other["plan_id"]}/sample-reviews', review_payload(other))
    payload = new_request(client, other, payload); payload["review_id"] = review["review_id"]
    assert_error(client.post("/api/wan-preparations", json=payload), 409, "BUDGET_DAILY_EXCEEDED")


def test_key_presence_cannot_enable_execution_and_get_does_not_write(fixture):
    client, service, *_ = fixture
    *_, payload = ready(fixture)
    record = post(client, "/api/wan-preparations", payload)
    service.connection = check_connection("artificial-nonempty", "https://dashscope.aliyuncs.com/api/v1")
    before = service.path.read_bytes()
    view = client.get(f'/api/wan-preparations/{record["preparation_id"]}').json()
    assert view["real_execution_allowed"] is False and "KEY_MISSING" not in view["execution_blockers"]
    assert "AUTHENTICATION_UNVERIFIED" in view["execution_blockers"]
    assert service.path.read_bytes() == before


def test_cancel_atomic_failure_retains_hold(fixture, monkeypatch):
    client, service, *_ = fixture
    *_, payload = ready(fixture)
    record = post(client, "/api/wan-preparations", payload)
    before = service.path.read_bytes()
    def fail(*args, **kwargs): raise OSError("private-marker")
    monkeypatch.setattr("core.generation_readiness.atomic_write_session", fail)
    assert_error(client.post(f'/api/wan-preparations/{record["preparation_id"]}/cancel', json={"expected_revision": 1}), 503)
    assert service.path.read_bytes() == before and client.get("/api/wan-budget").json()["daily_planning_held_minor"] == 35


def test_real_http_restart_preserves_price_review_quote_hold_and_cancel(tmp_path):
    import time
    from test_live_recovery import server
    app = create_demo(tmp_path)
    engine = app.dependency_overrides[get_workflow_engine]()
    engine._session_dir = str(tmp_path)
    engine.save_session_to_disk("media-demo")
    with server(tmp_path) as client:
        refs = seed_references(client)
        payload = request_for(refs[1], refs[4]); payload["options"]["model"] = "wan2.7-i2v"
        plan = post(client, "/api/generation-plans", payload)
        assert confirm(client, plan).status_code == 200
        price = post(client, "/api/wan-prices", price_payload(int(time.time())))
        budget = post(client, "/api/wan-budget-policies", budget_payload())
        review = post(client, f'/api/generation-plans/{plan["plan_id"]}/sample-reviews', review_payload(plan))
        quote = post(client, "/api/wan-quotes", {"plan_id": plan["plan_id"]})
        payload = {"quote_id": quote["quote_id"], "quote_hash": quote["quote_hash"], "review_id": review["review_id"],
            "budget_id": budget["budget_id"], "request_id": uuid4().hex, "preparation_only": True}
        preparation = post(client, "/api/wan-preparations", payload)
        route = f'/api/wan-preparations/{preparation["preparation_id"]}'
        budget_before = client.get("/api/wan-budget").json()
    with server(tmp_path) as client:
        assert client.get(route).json() == preparation
        assert client.get("/api/wan-budget").json() == budget_before
        assert client.get(f'/api/wan-prices/{price["price_id"]}').json() == price
        assert client.get(f'/api/wan-sample-reviews/{review["review_id"]}').json() == review
        assert client.get(f'/api/wan-quotes/{quote["quote_id"]}').json() == quote
        assert post(client, "/api/wan-preparations", payload) == preparation
        cancelled = client.post(route + "/cancel", json={"expected_revision": 1}).json()
    with server(tmp_path) as client:
        assert client.get(route).json() == cancelled
        assert client.get("/api/wan-budget").json()["daily_planning_held_minor"] == 0
