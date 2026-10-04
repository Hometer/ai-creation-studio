"""Local SIM accounting and actual file/process recovery, never paid calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_workflow_engine
from api.media_demo import create_demo
from api.routers.assets import get_asset_library, get_media_archive
from api.routers.generation_tasks import get_task_provider
from core import generation_tasks
from core.generation_plans import GenerationPlans
from core.generation_tasks import GenerationTasks
from test_generation_plans import assert_error, confirm, request_for, seed_references


@pytest.fixture
def fixture(tmp_path):
    app = create_demo(tmp_path, task_simulation=True)
    engine = app.dependency_overrides[get_workflow_engine]()
    library = app.dependency_overrides[get_asset_library]()
    archive = app.dependency_overrides[get_media_archive]()
    provider = app.dependency_overrides[get_task_provider]()
    service = GenerationTasks(GenerationPlans(engine, library, archive), provider)
    with TestClient(app, raise_server_exceptions=False) as client:
        _, first, _, _, context = seed_references(client)
        response = client.post("/api/generation-plans", json=request_for(first, context))
        assert response.status_code == 201
        plan = response.json()
        assert confirm(client, plan).status_code == 200
        yield client, service, provider, plan, engine, app


def quote_for(fixture):
    client, _, _, plan, _, _ = fixture
    response = client.post("/api/generation-quotes", json={"plan_id": plan["plan_id"], "segment_id": "seg-1"})
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, quote, maximum=10, limit=50):
    return client.post(f'/api/generation-quotes/{quote["quote_id"]}/confirm', json={"quote_hash": quote["quote_hash"],
        "max_amount_minor": maximum, "project_limit_minor": limit, "simulation_only": True})


def task_for(client, quote, request_id=None):
    response = client.post("/api/generation-tasks", json={"quote_id": quote["quote_id"], "request_id": request_id or uuid4().hex})
    assert response.status_code == 201, response.text
    return response.json()


def enqueue(client, task, quote):
    return client.post(f'/api/generation-tasks/{task["task_id"]}/enqueue', json={
        "expected_revision": task["revision"], "quote_hash": quote["quote_hash"]})


def advance(client, task):
    response = client.post(f'/api/generation-tasks/{task["task_id"]}/advance')
    assert response.status_code == 200, response.text
    return response.json()


def queued(fixture, limit=50):
    client = fixture[0]
    quote = quote_for(fixture)
    assert approve(client, quote, limit=limit).status_code == 200
    task = task_for(client, quote)
    response = enqueue(client, task, quote)
    assert response.status_code == 200, response.text
    return response.json(), quote


def finish(client, task):
    for _ in range(8):
        if task["status"] in {"succeeded", "failed", "manual_review"}:
            return task
        task = advance(client, task)
    raise AssertionError("Artificial task failed to terminate within its bounded steps")


def provider_records(provider):
    return json.loads((provider.directory / "simulation-provider.json").read_text())["jobs"]


def test_formal_service_keeps_unknown_quote_and_task_blocked(fixture):
    client, _, _, _, _, app = fixture
    app.dependency_overrides[get_task_provider] = lambda: None
    quote = quote_for(fixture)
    assert quote["offer"]["pricing_kind"] == "unavailable"
    assert quote["offer"]["amount_minor"] is None and quote["offer"]["currency"] is None
    assert quote["real_execution_allowed"] is False
    assert_error(approve(client, quote), 409, "QUOTE_UNAVAILABLE")
    task = task_for(client, quote)
    assert task["status"] == "blocked" and not task["simulation_only"]
    assert set(task["blockers"]) == {"quote_unavailable", "approval_required", "provider_unavailable"}
    assert_error(enqueue(client, task, quote), 409, "QUOTE_UNAVAILABLE")
    assert_error(client.post(f'/api/generation-tasks/{task["task_id"]}/advance'), 409, "TASK_PROVIDER_UNAVAILABLE")
    budget = client.get("/api/project/media-demo/generation-budget").json()
    assert budget["currency"] is None and budget["available_minor"] is None
    assert client.get(f'/api/generation-tasks/{task["task_id"]}').json() == task


def test_simulated_quote_has_explicit_basis_and_separate_local_approval(fixture):
    client, _, _, _, _, _ = fixture
    quote = quote_for(fixture)
    assert quote["offer"]["amount_minor"] == 10 and quote["offer"]["currency"] == "SIM"
    assert "不代表平台报价" in quote["offer"]["basis"]
    assert quote["approval"] is None and quote["current"] and not quote["expired"]
    task = task_for(client, quote)
    assert_error(enqueue(client, task, quote), 409, "QUOTE_NOT_CONFIRMED")
    confirmed = approve(client, quote).json()
    assert confirmed["approval"]["simulation_only"] is True
    assert approve(client, quote).json() == confirmed
    assert_error(approve(client, quote, maximum=11), 409, "QUOTE_ALREADY_CONFIRMED")
    assert_error(client.post(f'/api/generation-quotes/{quote["quote_id"]}/confirm', json={"quote_hash": "0" * 64,
        "max_amount_minor": 10, "project_limit_minor": 50, "simulation_only": True}), 409, "QUOTE_HASH_MISMATCH")


@pytest.mark.parametrize("payload", [
    {"simulation_only": False}, {"simulation_only": 1}, {"simulation_only": "true"},
    {"max_amount_minor": 1.5}, {"max_amount_minor": "10"}, {"max_amount_minor": True},
    {"max_amount_minor": -1}, {"project_limit_minor": 9}, {"amount_minor": 0}, {"currency": "CNY"},
])
def test_confirmation_cannot_forge_prices_or_bypass_simulation_scope(fixture, payload):
    client = fixture[0]
    quote = quote_for(fixture)
    request = {"quote_hash": quote["quote_hash"], "max_amount_minor": 10, "project_limit_minor": 50,
               "simulation_only": True, **payload}
    assert_error(client.post(f'/api/generation-quotes/{quote["quote_id"]}/confirm', json=request), 422)
    assert client.get(f'/api/generation-quotes/{quote["quote_id"]}').json()["approval"] is None


@pytest.mark.parametrize("extra", [{"amount_minor": 0}, {"pricing_kind": "simulation"}, {"provider": "paid"}, {"options": {}}])
def test_quote_accepts_only_plan_and_segment(fixture, extra):
    client, _, _, plan, _, _ = fixture
    assert_error(client.post("/api/generation-quotes", json={"plan_id": plan["plan_id"], "segment_id": "seg-1", **extra}), 422)


def test_single_and_project_budgets_are_checked_atomically(fixture):
    client = fixture[0]
    first = quote_for(fixture)
    assert_error(approve(client, first, maximum=9), 409, "SINGLE_BUDGET_EXCEEDED")
    assert approve(client, first, limit=10).status_code == 200
    second = quote_for(fixture)
    assert approve(client, second, limit=10).status_code == 200
    tasks = [task_for(client, quote) for quote in [first, second]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda pair: enqueue(client, *pair), zip(tasks, [first, second])))
    assert sorted(r.status_code for r in responses) == [200, 409]
    blocked = [client.get(f'/api/generation-tasks/{task["task_id"]}').json() for task in tasks]
    assert next(task for task in blocked if task["status"] == "blocked")["blockers"] == ["project_budget_exceeded"]
    assert client.get("/api/project/media-demo/generation-budget").json() == {
        "project_id": "media-demo", "currency": "SIM", "limit_minor": 10,
        "reserved_minor": 10, "settled_minor": 0, "available_minor": 0, "real_execution_allowed": False}
    third = quote_for(fixture)
    assert_error(approve(client, third, maximum=10, limit=10 - 1), 422)
    assert_error(approve(client, third, maximum=0, limit=0), 409)


def test_idempotency_and_one_task_per_quote_preserve_single_reservation(fixture):
    client = fixture[0]
    quote = quote_for(fixture)
    request_id = uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post("/api/generation-tasks", json={
            "quote_id": quote["quote_id"], "request_id": request_id}), range(6)))
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["task_id"] for r in responses}) == 1
    task = responses[0].json()
    assert_error(client.post("/api/generation-tasks", json={"quote_id": quote["quote_id"], "request_id": uuid4().hex}), 409, "QUOTE_ALREADY_USED")
    another = quote_for(fixture)
    assert_error(client.post("/api/generation-tasks", json={"quote_id": another["quote_id"], "request_id": request_id}), 409, "TASK_IDEMPOTENCY_CONFLICT")
    approve(client, quote)
    queued_task = enqueue(client, task, quote).json()
    assert enqueue(client, task, quote).json() == queued_task
    assert client.get("/api/project/media-demo/generation-budget").json()["reserved_minor"] == 10
    assert len(client.get("/api/project/media-demo/generation-tasks").json()) == 1


def test_stale_revision_and_hash_cannot_enqueue(fixture):
    client = fixture[0]
    quote = quote_for(fixture)
    approve(client, quote)
    task = task_for(client, quote)
    route = f'/api/generation-tasks/{task["task_id"]}/enqueue'
    assert_error(client.post(route, json={"expected_revision": 2, "quote_hash": quote["quote_hash"]}), 409, "TASK_REVISION_CONFLICT")
    assert_error(client.post(route, json={"expected_revision": 1, "quote_hash": "0" * 64}), 409, "QUOTE_HASH_MISMATCH")


@pytest.mark.parametrize("change", ["source", "expiry"])
def test_changed_or_expired_inputs_block_new_submission_and_release_only_unsent_reservation(fixture, change, monkeypatch):
    client, _, provider, _, engine, _ = fixture
    task, quote = queued(fixture)
    if change == "source":
        engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]["shots"][0]["content"] = "人工改动"
    else:
        current = generation_tasks.time.time()
        monkeypatch.setattr(generation_tasks.time, "time", lambda: current + 1201)
    result = advance(client, task)
    assert result["status"] == "blocked" and result["submit_attempts"] == 0
    assert result["billing"]["state"] == "unreserved" and client.get("/api/project/media-demo/generation-budget").json()["reserved_minor"] == 0
    assert not (provider.directory / "simulation-provider.json").exists()
    assert_error(approve(client, quote), 409)


def test_already_submitted_task_tracks_original_inputs_after_upstream_changes(fixture):
    client, _, provider, _, engine, _ = fixture
    task, _ = queued(fixture)
    task = advance(client, task)
    assert task["status"] == "submitted"
    engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]["shots"][0]["content"] = "后来修改"
    result = finish(client, task)
    assert result["status"] == "succeeded" and result["submit_attempts"] == 1
    assert provider_records(provider)[task["request_id"]]["submit_calls"] == 1


def test_result_versions_keep_old_selection_and_exact_task_provenance(fixture):
    client, service, _, plan, engine, _ = fixture
    before = engine.get_status_snapshot("media-demo")
    old = client.post("/api/assets/import-media", json={"project_id": "media-demo", "stage": "video_generation",
        "item_type": "clips", "item_id": "seg-1"}).json()
    ref = client.post("/api/asset-references", json={"project_id": "media-demo", "segment_id": "seg-1", "asset_id": old["asset_id"],
        "version_id": old["versions"][0]["version_id"]}).json()
    results = []
    for _ in range(2):
        task, _ = queued(fixture)
        result = finish(client, task)
        assert result["status"] == "succeeded" and result["billing"]["billed_minor"] == 10
        assert client.get(result["result_url"]).status_code == 200
        assert str(service.path.parent) not in json.dumps(result)
        results.append(result)
    asset = client.get(f'/api/assets/{old["asset_id"]}').json()
    assert len(asset["versions"]) == 3
    assert asset["versions"][1]["source"]["task_id"] == results[0]["task_id"]
    assert asset["versions"][2]["source"]["task_id"] == results[1]["task_id"]
    assert all(v["source"]["content_hash"] == plan["content_hash"] and v["source"]["simulation_only"] for v in asset["versions"][1:])
    assert client.get("/api/project/media-demo/asset-references").json()[-1]["version_id"] == ref["version_id"]
    assert engine.get_status_snapshot("media-demo") == before
    assert client.get("/api/project/media-demo/generation-budget").json()["settled_minor"] == 20
    assert advance(client, results[0]) == results[0]


@pytest.mark.parametrize("failure", ["init", "submit"])
def test_uncertain_submit_or_initialization_never_resubmits(fixture, failure, monkeypatch):
    client, _, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    original_submit, original_initialize = provider.submit, provider.initialize
    calls = []

    def broken_submit(*args, **kwargs):
        calls.append(True)
        original_submit(*args, **kwargs)
        raise TimeoutError("private-marker: response lost after provider accepted")

    def broken_init():
        raise RuntimeError("private-marker SDK initialization failure")

    monkeypatch.setattr(provider, "submit", broken_submit)
    if failure == "init": monkeypatch.setattr(provider, "initialize", broken_init)
    result = advance(client, task)
    assert result["status"] == "manual_review" and result["submit_attempts"] == 1
    assert result["billing"]["state"] == "reserved" and "private-marker" not in json.dumps(result)
    monkeypatch.setattr(provider, "initialize", original_initialize)
    result = advance(client, result)
    if failure == "submit":
        assert result["status"] == "running" and result["provider_task_id"]
        assert len(calls) == 1
        assert finish(client, result)["status"] == "succeeded"
    else:
        assert result["status"] == "manual_review" and result["error"]["code"] == "SUBMISSION_NOT_FOUND"
        assert not calls


def test_submission_checkpoint_survives_failed_task_write_and_expired_lease(fixture, monkeypatch):
    client, service, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    original = generation_tasks.atomic_write_session
    writes = 0

    def fail_second(path, data):
        nonlocal writes
        writes += 1
        if writes == 2: raise OSError("private-marker")
        return original(path, data)

    monkeypatch.setattr(generation_tasks, "atomic_write_session", fail_second)
    assert_error(client.post(f'/api/generation-tasks/{task["task_id"]}/advance'), 503)
    stored = client.get(f'/api/generation-tasks/{task["task_id"]}').json()
    assert stored["status"] == "submitting" and stored["provider_task_id"] is None and stored["lease_token"]
    assert_error(client.post(f'/api/generation-tasks/{task["task_id"]}/advance'), 409, "TASK_BUSY")
    monkeypatch.setattr(generation_tasks, "atomic_write_session", original)
    current = generation_tasks.time.time()
    monkeypatch.setattr(generation_tasks.time, "time", lambda: current + 31)
    recovered = advance(client, stored)
    assert recovered["status"] == "running" and recovered["provider_task_id"]
    assert provider_records(provider)[task["request_id"]]["submit_calls"] == 1
    assert finish(client, recovered)["status"] == "succeeded"


def test_active_lease_prevents_concurrent_duplicate_provider_call(fixture, monkeypatch):
    import threading
    client, _, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    entered, release = threading.Event(), threading.Event()
    original = provider.submit

    def wait(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(provider, "submit", wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(advance, client, task)
        assert entered.wait(timeout=5)
        try:
            assert_error(client.post(f'/api/generation-tasks/{task["task_id"]}/advance'), 409, "TASK_BUSY")
        finally:
            release.set()
        assert future.result()["status"] == "submitted"
    assert provider_records(provider)[task["request_id"]]["submit_calls"] == 1


@pytest.mark.parametrize("failure", ["timeout", "invalid", "unknown", "overrun", "changed_id"])
def test_query_failures_are_bounded_preserve_reservation_and_do_not_submit(fixture, failure, monkeypatch):
    client, _, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    task = advance(client, task)

    def query(*args, **kwargs):
        if failure == "timeout": raise TimeoutError("private-marker")
        if failure == "invalid": return {"status": "succeeded", "provider_task_id": {"private-marker": 1}}
        if failure == "unknown": return {"status": "unknown"}
        if failure == "overrun": return {"status": "succeeded", "provider_task_id": task["provider_task_id"], "billed_minor": 11}
        return {"status": "running", "provider_task_id": "wrong_id"}

    monkeypatch.setattr(provider, "query", query)
    for _ in range(3): task = advance(client, task)
    assert task["status"] == "manual_review" and task["billing"]["state"] == "reserved"
    assert "private-marker" not in json.dumps(task)
    assert_error(client.post(f'/api/generation-tasks/{task["task_id"]}/advance'), 409, "TASK_REVIEW_REQUIRED")
    assert provider_records(provider)[task["request_id"]]["submit_calls"] == 1


@pytest.mark.parametrize("billed", [None, 0, 3])
def test_explicit_failure_does_not_invent_a_refund(fixture, billed, monkeypatch):
    client, _, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    task = advance(client, task)
    monkeypatch.setattr(provider, "query", lambda *args, **kwargs: {
        "status": "failed", "provider_task_id": task["provider_task_id"], "billed_minor": billed})
    failed = advance(client, task)
    assert failed["status"] == "failed"
    assert failed["billing"]["state"] == ("reserved" if billed is None else "settled")
    assert failed["billing"]["billed_minor"] == billed
    assert failed["submit_attempts"] == 1 and advance(client, failed) == failed


def test_running_poll_limit_stops_instead_of_polling_forever(fixture, monkeypatch):
    client, _, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    task = advance(client, task)
    monkeypatch.setattr(provider, "query", lambda *args, **kwargs: {"status": "running", "provider_task_id": task["provider_task_id"]})
    for _ in range(20): task = advance(client, task)
    assert task["status"] == "manual_review" and task["error"]["code"] == "QUERY_LIMIT_REACHED"
    assert_error(client.post(f'/api/generation-tasks/{task["task_id"]}/advance'), 409)


def test_last_permitted_query_success_still_allows_result_archiving(fixture, monkeypatch):
    client, _, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    task = advance(client, task)
    original = provider.query
    calls = 0

    def wait_until_twentieth(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 19:
            return {"status": "running", "provider_task_id": task["provider_task_id"]}
        return original(*args, **kwargs)

    monkeypatch.setattr(provider, "query", wait_until_twentieth)
    for _ in range(20): task = advance(client, task)
    assert task["status"] == "fetching" and task["query_attempts"] == 20
    assert advance(client, task)["status"] == "succeeded"


def test_invalid_result_retries_only_result_reading_not_generation(fixture, monkeypatch):
    client, _, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    for _ in range(3): task = advance(client, task)
    assert task["status"] == "fetching"

    def bad(task_id, request_id, destination, **kwargs):
        destination.write_bytes(b"private-marker-invalid-video")

    monkeypatch.setattr(provider, "fetch_result", bad)
    for _ in range(3): task = advance(client, task)
    assert task["status"] == "manual_review" and task["fetch_attempts"] == 3
    assert task["billing"]["state"] == "reserved"
    assert provider_records(provider)[task["request_id"]]["submit_calls"] == 1
    assert_error(client.post(f'/api/generation-tasks/{task["task_id"]}/advance'), 409)


def test_archived_result_recovery_after_another_task_completed_is_idempotent(fixture, monkeypatch):
    client, service, provider, _, _, _ = fixture
    task, _ = queued(fixture)
    for _ in range(3): task = advance(client, task)
    original = generation_tasks.atomic_write_session
    writes = 0

    def fail_second(path, data):
        nonlocal writes
        writes += 1
        if writes == 2: raise OSError("private-marker")
        return original(path, data)

    monkeypatch.setattr(generation_tasks, "atomic_write_session", fail_second)
    assert_error(client.post(f'/api/generation-tasks/{task["task_id"]}/advance'), 503)
    monkeypatch.setattr(generation_tasks, "atomic_write_session", original)
    other, _ = queued(fixture)
    assert finish(client, other)["status"] == "succeeded"
    current = generation_tasks.time.time()
    monkeypatch.setattr(generation_tasks.time, "time", lambda: current + 31)

    def forbidden(*args, **kwargs): raise AssertionError("An archived result must not be fetched again")
    monkeypatch.setattr(provider, "fetch_result", forbidden)
    result = advance(client, task)
    assert result["status"] == "succeeded"
    asset = client.get(f'/api/assets/{result["result"]["asset_id"]}').json()
    assert len(asset["versions"]) == 2
    assert result["result"]["version_id"] == asset["versions"][0]["version_id"]


def test_last_fetch_committed_result_recovers_without_provider_or_more_downloads(fixture, monkeypatch):
    from api.schemas.assets import TaskSource, VersionInput
    client, service, provider, _, _, app = fixture
    task, _ = queued(fixture)
    for _ in range(3): task = advance(client, task)
    original = provider.fetch_result

    def fail(*args, **kwargs): raise TimeoutError("private-marker")
    monkeypatch.setattr(provider, "fetch_result", fail)
    for _ in range(3): task = advance(client, task)
    assert task["status"] == "manual_review" and task["fetch_attempts"] == 3
    source = TaskSource(project_id=task["project_id"], item_id=task["segment_id"], task_id=task["task_id"],
        plan_id=task["plan_id"], content_hash=task["content_hash"], simulation_only=True)
    with service.plans.archive.prepare(provider.sample_video, "video") as (temporary, details):
        service.plans.library.import_media(source, "video", VersionInput(name="人工恢复结果", definition="模拟平台迟到的已归档结果"),
                                           service.plans.archive, temporary, details)
    app.dependency_overrides[get_task_provider] = lambda: None
    recovered = advance(client, task)
    assert recovered["status"] == "succeeded" and recovered["fetch_attempts"] == 3
    assert recovered["submit_attempts"] == 1


@pytest.mark.parametrize("damage", ["json", "schema", "bool_schema", "missing_graph", "bad_hash", "duplicate", "link", "fifo", "missing_reservation", "overdrawn", "approval_without_budget"])
def test_corrupt_task_store_is_preserved_and_does_not_zero_budget(fixture, damage):
    import os
    client, service, _, _, _, _ = fixture
    task, quote = queued(fixture)
    path = service.path
    data = json.loads(path.read_text())
    if damage == "json": path.write_text("private-marker")
    elif damage == "duplicate": path.write_text('{"schema_version":1,"schema_version":1,"quotes":{},"tasks":{},"budget_limits":{}}')
    elif damage == "link":
        target = path.with_suffix(".backup")
        path.rename(target)
        path.symlink_to(target)
    elif damage == "fifo":
        path.unlink(); os.mkfifo(path)
        assert_error(client.get(f'/api/generation-tasks/{task["task_id"]}'), 503)
        assert path.exists()
        return
    else:
        if damage == "schema": data["schema_version"] = 2
        elif damage == "bool_schema": data["schema_version"] = True
        elif damage == "missing_graph": del data["tasks"]
        elif damage == "bad_hash": data["quotes"][quote["quote_id"]]["quote_hash"] = "0" * 64
        elif damage == "missing_reservation": data["tasks"][task["task_id"]]["billing"] = {"state": "unreserved", "reserved_minor": 0, "billed_minor": None}
        elif damage == "overdrawn": data["budget_limits"]["media-demo"] = 0
        elif damage == "approval_without_budget":
            data["budget_limits"] = {}
            data["tasks"][task["task_id"]]["status"] = "blocked"
            data["tasks"][task["task_id"]]["billing"] = {"state": "unreserved", "reserved_minor": 0, "billed_minor": None}
        path.write_text(json.dumps(data))
    before = path.read_bytes()
    assert_error(client.get(f'/api/generation-tasks/{task["task_id"]}'), 503)
    assert path.read_bytes() == before


def test_failed_enqueue_commit_does_not_reserve_or_queue(fixture, monkeypatch):
    client, service, _, _, _, _ = fixture
    quote = quote_for(fixture)
    approve(client, quote)
    task = task_for(client, quote)
    before = service.path.read_bytes()
    monkeypatch.setattr(generation_tasks, "atomic_write_session", lambda *args: (_ for _ in ()).throw(OSError("private-marker")))
    assert_error(enqueue(client, task, quote), 503)
    assert service.path.read_bytes() == before
    assert client.get("/api/project/media-demo/generation-budget").json()["reserved_minor"] == 0
    assert client.get(f'/api/generation-tasks/{task["task_id"]}').json()["status"] == "blocked"


def test_http_tasks_and_platform_id_survive_actual_restart(tmp_path):
    from test_live_recovery import server
    app = create_demo(tmp_path, task_simulation=True)
    (tmp_path / "media-demo.json").write_bytes((tmp_path / "sessions/media-demo.json").read_bytes())
    with server(tmp_path, task_simulation=True) as client:
        _, first, _, _, context = seed_references(client)
        plan = client.post("/api/generation-plans", json=request_for(first, context)).json()
        confirm(client, plan)
        quote = client.post("/api/generation-quotes", json={"plan_id": plan["plan_id"], "segment_id": "seg-1"}).json()
        approved = approve(client, quote).json()
        task = task_for(client, quote)
        enqueued = enqueue(client, task, quote).json()
        budget = client.get("/api/project/media-demo/generation-budget").json()
    with server(tmp_path, task_simulation=True) as client:
        assert client.get(f'/api/generation-quotes/{quote["quote_id"]}').json() == approved
        assert client.get(f'/api/generation-tasks/{task["task_id"]}').json() == enqueued
        assert client.get("/api/project/media-demo/generation-budget").json() == budget
        submitted = advance(client, enqueued)
        assert submitted["provider_task_id"] and submitted["status"] == "submitted"
    with server(tmp_path, task_simulation=True) as client:
        assert client.get(f'/api/generation-tasks/{task["task_id"]}').json() == submitted
        succeeded = finish(client, submitted)
        assert succeeded["status"] == "succeeded"
        video = client.get(succeeded["result_url"]).content
        budget = client.get("/api/project/media-demo/generation-budget").json()
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/generation-tasks/{task_id}/advance"]["post"]["responses"]["503"]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")
        assert client.get("/code/data/asset-library/generation-tasks.json").status_code == 404
    with server(tmp_path, task_simulation=True) as client:
        assert client.get(f'/api/generation-tasks/{task["task_id"]}').json() == succeeded
        assert client.get(succeeded["result_url"]).content == video
        assert client.get("/api/project/media-demo/generation-budget").json() == budget
        records = json.loads((tmp_path / "simulation-provider/simulation-provider.json").read_text())["jobs"]
        assert records[task["request_id"]]["submit_calls"] == 1


def test_real_clients_are_not_initialized_by_simulation(fixture, monkeypatch):
    from models.video_client import VideoClient
    def forbidden(*args, **kwargs): raise AssertionError("Real SDK must not be initialized")
    monkeypatch.setattr(VideoClient, "__init__", forbidden)
    monkeypatch.setattr(VideoClient, "generate_video", forbidden)
    task, _ = queued(fixture)
    assert finish(fixture[0], task)["status"] == "succeeded"


def test_failed_confirmation_after_replacement_can_be_safely_retried(fixture, monkeypatch):
    client = fixture[0]
    quote = quote_for(fixture)
    monkeypatch.setattr(generation_tasks, "sync_directory", lambda *args: (_ for _ in ()).throw(OSError("private-marker")))
    assert_error(approve(client, quote), 503)
    committed = client.get(f'/api/generation-quotes/{quote["quote_id"]}').json()
    assert committed["approval"] is not None
    assert approve(client, quote).json() == committed


def test_private_output_symlink_is_rejected_without_overwriting_target(fixture, tmp_path):
    client, service, _, _, _, _ = fixture
    task, _ = queued(fixture)
    for _ in range(3): task = advance(client, task)
    target = tmp_path / "private-marker-original"
    target.write_bytes(b"artificial-original")
    directory = service.path.parent / "task-output" / task["task_id"]
    directory.mkdir(parents=True)
    (directory / "output.mp4").symlink_to(target)
    task = advance(client, task)
    assert task["error"]["code"] == "RESULT_FAILED" and target.read_bytes() == b"artificial-original"


def test_missing_ids_and_client_controlled_job_fields_are_rejected(fixture):
    client, _, _, plan, _, _ = fixture
    quote = quote_for(fixture)
    for extra in [{"status": "succeeded"}, {"provider_task_id": "paid-task"}, {"result_path": "/private-marker"}]:
        assert_error(client.post("/api/generation-tasks", json={"quote_id": quote["quote_id"], "request_id": uuid4().hex, **extra}), 422)
    assert_error(client.post("/api/generation-quotes", json={"plan_id": plan["plan_id"], "segment_id": "missing"}), 404)
    assert_error(client.get("/api/generation-quotes/" + "0" * 32), 404)
    assert_error(client.get("/api/generation-tasks/" + "0" * 32), 404)
