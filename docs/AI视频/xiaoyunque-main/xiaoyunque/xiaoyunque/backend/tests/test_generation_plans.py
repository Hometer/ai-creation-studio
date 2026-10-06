"""Pinned real local images and durable local approvals, never model outputs."""

import copy
import inspect
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_workflow_engine
from api.media_demo import create_demo
from api.routers.assets import get_asset_library, get_media_archive
from api.schemas.generation_plans import ConfirmInput
from core.generation_plans import GenerationPlans


def seed_references(client):
    source = {"project_id": "media-demo", "stage": "character_design", "item_type": "characters", "item_id": "role-1"}
    asset = client.post("/api/assets/import-media", json={**source, "candidate_number": 1}).json()
    asset = client.post("/api/assets/import-media", json={**source, "candidate_number": 2}).json()
    first = client.post("/api/asset-references", json={"project_id": "media-demo", "segment_id": "seg-1",
        "shot_number": 1, "asset_id": asset["asset_id"], "version_id": asset["versions"][0]["version_id"]}).json()
    second = client.post("/api/asset-references", json={"project_id": "media-demo",
        "asset_id": asset["asset_id"], "version_id": asset["versions"][1]["version_id"]}).json()
    text = client.post("/api/assets", json={"kind": "character", "name": "人工角色", "definition": "黄色信使，固定文字定义"}).json()
    context = client.post("/api/asset-references", json={"project_id": "media-demo", "asset_id": text["asset_id"],
        "version_id": text["versions"][0]["version_id"]}).json()
    return asset, first, second, text, context


def request_for(first, context=None, mode="first_frame", second=None):
    role = "reference_image" if mode == "reference" else "first_frame"
    bindings = [{"reference_id": first["reference_id"], "role": role}]
    if context:
        bindings.append({"reference_id": context["reference_id"], "role": "context"})
    if second:
        bindings.append({"reference_id": second["reference_id"], "role": "last_frame" if mode == "start_end_frame" else "reference_image"})
    return {"project_id": "media-demo", "options": {"model": "local-test-model", "mode": mode},
            "targets": [{"segment_id": "seg-1", "duration": 5, "bindings": bindings}]}


@pytest.fixture
def fixture(tmp_path):
    app = create_demo(tmp_path)
    engine = app.dependency_overrides[get_workflow_engine]()
    library = app.dependency_overrides[get_asset_library]()
    archive = app.dependency_overrides[get_media_archive]()
    engine.create_session("other-project", {"idea": "隔离人工样例"})
    state = engine.get_state("media-demo")
    state.artifacts["storyboard"]["episodes"][0]["segments"].append({"segment_id": "seg-2", "shots": [
        {"shot_number": 1, "duration": 5, "content": "人工第二片段"}]})
    engine.save_session_to_disk("media-demo")
    with TestClient(app, raise_server_exceptions=False) as client:
        references = seed_references(client)
        yield client, engine, library, archive, references


def create_plan(fixture, payload=None):
    client, _, _, _, (_, first, _, _, context) = fixture
    response = client.post("/api/generation-plans", json=payload or request_for(first, context))
    assert response.status_code == 201, response.text
    return response.json()


def confirm(client, plan):
    return client.post(f'/api/generation-plans/{plan["plan_id"]}/confirm', json={
        "content_hash": plan["content_hash"], "local_only": True})


def assert_error(response, status, code=None):
    assert response.status_code == status, response.text
    assert set(response.json()) == {"error", "detail"}
    assert set(response.json()["error"]) == {"code", "message"}
    if code:
        assert response.json()["error"]["code"] == code
    assert "Traceback" not in response.text and "private-marker" not in response.text


def test_plan_pins_old_versions_and_never_treats_unknown_cost_as_free(fixture):
    client, engine, _, _, (asset, first, _, _, _) = fixture
    before = engine.get_status_snapshot("media-demo")
    plan = create_plan(fixture)
    assert plan["status"] == "draft" and plan["current"]
    assert plan["cost"] == {"status": "pending_confirmation", "amount": None, "currency": None}
    assert plan["execution_allowed"] is False and len(plan["execution_blockers"]) == 3
    target = plan["content"]["targets"][0]
    assert target["bindings"][0]["version"]["version_id"] == asset["versions"][0]["version_id"]
    assert target["bindings"][0]["target"]["shot_number"] == 1
    assert plan["impacts"][0]["newer_version_references"] == [first["reference_id"]]
    assert "黄色信使，固定文字定义" in target["prompt"] and "人工黄色色块" in target["prompt"]
    assert client.get(f'/api/generation-plans/{plan["plan_id"]}').json() == plan
    assert client.get("/api/project/media-demo/generation-plans").json() == [plan]
    assert client.get("/api/project/other-project/generation-plans").json() == []
    assert engine.get_status_snapshot("media-demo") == before


@pytest.mark.parametrize("mode", ["first_frame", "start_end_frame", "reference"])
def test_three_modes_match_video_client_signature_without_initialization(fixture, mode, monkeypatch):
    from models.video_client import VideoClient

    def forbidden(*args, **kwargs):
        raise AssertionError("A local plan must never initialize or invoke a model")

    monkeypatch.setattr(VideoClient, "__init__", forbidden)
    signature = inspect.signature(VideoClient.generate_video)
    monkeypatch.setattr(VideoClient, "generate_video", forbidden)
    client, engine, library, archive, (asset, first, second, _, context) = fixture
    payload = request_for(first, context, mode, second if mode != "first_frame" else None)
    plan = create_plan(fixture, payload)
    assert confirm(client, plan).status_code == 200
    calls, preview = GenerationPlans(engine, library, archive).prepare(plan["plan_id"])
    args = calls[0].arguments
    signature.bind(None, save_path="future-server-owned-output.mp4", **args)
    assert args["model"] == "local-test-model" and args["duration"] == 5
    assert args["prompt"] == plan["content"]["targets"][0]["prompt"]
    if mode == "reference":
        assert args["image_path"] is None and len(args["reference_image_paths"]) == 2
        paths = args["reference_image_paths"]
    else:
        paths = [args["image_path"]]
        if mode == "start_end_frame":
            assert args["last_image_path"] and args["last_image_path"] != args["image_path"]
            paths.append(args["last_image_path"])
        else:
            assert args["last_image_path"] is None
    assert all(Path(path).is_file() and str(library.directory / "media") in path for path in paths)
    public = client.post(f'/api/generation-plans/{plan["plan_id"]}/dry-run')
    assert public.status_code == 200 and public.json() == preview.model_dump(mode="json")
    assert str(library.directory) not in public.text and "image_path" not in public.text
    urls = public.json()["targets"][0]["reference_image_urls"] or [public.json()["targets"][0]["first_frame_url"]]
    assert client.get(urls[0]).headers["etag"].strip('"') == asset["versions"][0]["media"]["sha256"]
    assert public.json()["execution_allowed"] is False


@pytest.mark.parametrize("case", ["extra", "missing_model", "bad_mode", "bool_duration", "string_duration", "duration_zero",
    "duration_over", "bad_id", "bad_role", "duplicate_binding", "duplicate_target", "no_targets", "too_many_targets"])
def test_strict_request_rejects_ambiguous_or_unbounded_inputs(fixture, case):
    client, _, _, _, (_, first, _, _, context) = fixture
    payload = request_for(first, context)
    target = payload["targets"][0]
    if case == "extra": payload["private-marker"] = "private-marker"
    elif case == "missing_model": del payload["options"]["model"]
    elif case == "bad_mode": payload["options"]["mode"] = "auto"
    elif case == "bool_duration": target["duration"] = True
    elif case == "string_duration": target["duration"] = "5"
    elif case == "duration_zero": target["duration"] = 0
    elif case == "duration_over": target["duration"] = 61
    elif case == "bad_id": target["bindings"][0]["reference_id"] = "../private-marker"
    elif case == "bad_role": target["bindings"][0]["role"] = "auto"
    elif case == "duplicate_binding": target["bindings"].append(target["bindings"][0])
    elif case == "duplicate_target": payload["targets"].append(copy.deepcopy(target))
    elif case == "no_targets": payload["targets"] = []
    elif case == "too_many_targets": payload["targets"] = [{**target, "segment_id": f"seg-{i}"} for i in range(21)]
    assert_error(client.post("/api/generation-plans", json=payload), 422)


@pytest.mark.parametrize("case", ["missing_first", "missing_last", "extra_reference", "image_context", "text_image",
    "cross_project", "cross_segment", "stale_shot", "video_image", "missing_reference"])
def test_input_roles_and_scopes_fail_closed_without_fallback(fixture, case):
    client, engine, library, _, (_, first, _, _, context) = fixture
    payload = request_for(first, context)
    target = payload["targets"][0]
    if case == "missing_first": target["bindings"] = [target["bindings"][1]]
    elif case == "missing_last": payload["options"]["mode"] = "start_end_frame"
    elif case == "extra_reference": target["bindings"][0]["role"] = "reference_image"
    elif case == "image_context": target["bindings"][0]["role"] = "context"
    elif case == "text_image": target["bindings"][1]["role"] = "first_frame"
    elif case == "cross_project": payload["project_id"] = "other-project"; engine.get_state("other-project").artifacts = copy.deepcopy(engine.get_state("media-demo").artifacts)
    elif case == "cross_segment": target["segment_id"] = "seg-2"
    elif case == "stale_shot": engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]["shots"][0]["shot_number"] = 2
    elif case == "video_image":
        asset = client.post("/api/assets/import-media", json={"project_id": "media-demo", "stage": "video_generation",
            "item_type": "clips", "item_id": "seg-1"}).json()
        ref = client.post("/api/asset-references", json={"project_id": "media-demo", "asset_id": asset["asset_id"],
            "version_id": asset["versions"][0]["version_id"]}).json()
        target["bindings"][0]["reference_id"] = ref["reference_id"]
    elif case == "missing_reference": target["bindings"][0]["reference_id"] = "f" * 32
    assert_error(client.post("/api/generation-plans", json=payload), 404 if case == "missing_reference" else 409)
    assert not (library.directory / "generation-plans.json").exists()


@pytest.mark.parametrize("case", ["missing_content", "missing_shots", "duplicate_shot", "string_shot", "bad_duration", "empty_shots"])
def test_invalid_storyboard_is_a_safe_business_error(fixture, case):
    client, engine, _, _, _ = fixture
    segment = engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]
    if case == "missing_content": del segment["shots"][0]["content"]
    elif case == "missing_shots": del segment["shots"]
    elif case == "duplicate_shot": segment["shots"].append(copy.deepcopy(segment["shots"][0]))
    elif case == "string_shot": segment["shots"][0]["shot_number"] = "1"
    elif case == "bad_duration": segment["shots"][0]["duration"] = -1
    elif case == "empty_shots": segment["shots"] = []
    _, _, _, _, (_, first, _, _, context) = fixture
    assert_error(client.post("/api/generation-plans", json=request_for(first, context)), 409)


def test_confirmation_is_explicit_hash_bound_local_and_idempotent(fixture):
    client, _, library, _, _ = fixture
    plan = create_plan(fixture)
    route = f'/api/generation-plans/{plan["plan_id"]}'
    assert_error(client.post(route + "/dry-run"), 409, "PLAN_NOT_CONFIRMED")
    for local_only in [False, 1, "true"]:
        assert_error(client.post(route + "/confirm", json={"content_hash": plan["content_hash"], "local_only": local_only}), 422)
    assert_error(client.post(route + "/confirm", json={"content_hash": "0" * 64, "local_only": True}), 409, "PLAN_HASH_MISMATCH")
    confirmed = confirm(client, plan).json()
    assert confirmed["status"] == "confirmed_local" and confirmed["confirmation"]["scope"] == "local_input_only"
    saved = (library.directory / "generation-plans.json").read_bytes()
    assert confirm(client, plan).json() == confirmed
    assert (library.directory / "generation-plans.json").read_bytes() == saved
    assert client.post(route + "/dry-run").status_code == 200
    assert_error(client.post(route + "/execute"), 404)
    assert confirmed["execution_allowed"] is False


def test_new_version_is_advisory_but_explicit_reference_upgrade_invalidates(fixture):
    client, _, _, _, (asset, first, _, text, _) = fixture
    plan = create_plan(fixture)
    confirmed = confirm(client, plan).json()
    client.post(f'/api/assets/{text["asset_id"]}/versions', json={"name": "人工角色", "definition": "新文字不自动使用"})
    current = client.get(f'/api/generation-plans/{plan["plan_id"]}').json()
    assert current["current"] and current["confirmation"] == confirmed["confirmation"]
    assert "新文字不自动使用" not in current["content"]["targets"][0]["prompt"]
    assert len(current["impacts"][0]["newer_version_references"]) == 2
    route = f'/api/asset-references/{first["reference_id"]}'
    client.patch(route, json={"version_id": asset["versions"][1]["version_id"], "expected_revision": 1})
    changed = client.get(f'/api/generation-plans/{plan["plan_id"]}').json()
    assert not changed["current"] and changed["impacts"][0]["reasons"] == ["reference_changed"]
    assert_error(confirm(client, plan), 409, "PLAN_SOURCE_CHANGED")
    assert_error(client.post(f'/api/generation-plans/{plan["plan_id"]}/dry-run'), 409)
    client.patch(route, json={"version_id": first["version_id"], "expected_revision": 2})
    assert not client.get(f'/api/generation-plans/{plan["plan_id"]}').json()["current"]
    new = create_plan(fixture)
    assert new["content_hash"] != plan["content_hash"] and new["current"] and new["confirmation"] is None
    assert len(client.get("/api/project/media-demo/generation-plans").json()) == 2


def test_upstream_changes_mark_only_affected_segments_and_keep_history(fixture):
    client, engine, _, _, (_, first, second, _, context) = fixture
    payload = request_for(first, context)
    payload["targets"].append({"segment_id": "seg-2", "duration": 5, "bindings": [
        {"reference_id": second["reference_id"], "role": "first_frame"}]})
    plan = create_plan(fixture, payload)
    confirmed = confirm(client, plan).json()
    segments = engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"]
    segments[0]["shots"][0]["content"] = "人工改动，只影响第一片段"
    current = client.get(f'/api/generation-plans/{plan["plan_id"]}').json()
    assert [i["reasons"] for i in current["impacts"]] == [["source_changed"], []]
    assert current["content"] == confirmed["content"] and current["confirmation"] == confirmed["confirmation"]
    assert_error(client.post(f'/api/generation-plans/{plan["plan_id"]}/dry-run'), 409)
    segments.pop(0)
    assert client.get(f'/api/generation-plans/{plan["plan_id"]}').json()["impacts"][0]["reasons"] == ["target_missing"]
    engine.sessions.pop("media-demo")
    (Path(engine._session_dir) / "media-demo.json").unlink()
    history = client.get("/api/project/media-demo/generation-plans").json()
    assert history[0]["plan_id"] == plan["plan_id"] and not history[0]["current"]


def test_deleted_reference_and_mutated_metadata_invalidate_pins(fixture):
    client, _, library, _, (_, first, _, text, _) = fixture
    plan = create_plan(fixture)
    data = json.loads(library.path.read_text())
    data["assets"][text["asset_id"]]["versions"][0]["definition"] = "人工元数据变动"
    del data["references"][first["reference_id"]]
    library.path.write_text(json.dumps(data))
    view = client.get(f'/api/generation-plans/{plan["plan_id"]}').json()
    assert view["impacts"][0]["reasons"] == ["reference_changed", "version_changed"]
    assert_error(confirm(client, plan), 409)


@pytest.mark.parametrize("change", ["missing", "tamper", "symlink"])
def test_corrupt_media_prevents_confirm_and_input_preparation(fixture, change):
    client, _, _, archive, (asset, _, _, _, _) = fixture
    plan = create_plan(fixture)
    assert confirm(client, plan).status_code == 200
    from api.schemas.assets import MediaInfo
    path = archive.verify_file(MediaInfo(**asset["versions"][0]["media"]))
    if change == "missing": path.unlink()
    elif change == "tamper": path.write_bytes(b"corrupt-private-marker")
    else:
        path.unlink()
        path.symlink_to(archive.directory / "nonexistent")
    assert_error(confirm(client, plan), 503, "ARCHIVED_MEDIA_UNAVAILABLE")
    assert_error(client.post(f'/api/generation-plans/{plan["plan_id"]}/dry-run'), 503, "ARCHIVED_MEDIA_UNAVAILABLE")


@pytest.mark.parametrize("change", ["syntax", "schema", "boolean_schema", "duplicate_json", "wrong_hash", "wrong_id", "symlink", "fifo"])
def test_corrupt_or_future_plan_store_is_preserved(fixture, change):
    client, _, library, _, _ = fixture
    plan = create_plan(fixture)
    path = library.directory / "generation-plans.json"
    data = json.loads(path.read_text())
    if change == "syntax": path.write_text("private-marker-not-json")
    elif change == "duplicate_json": path.write_text('{"schema_version":1,"schema_version":1,"plans":{}}')
    elif change == "symlink":
        destination = library.directory / "private-marker-copy.json"
        path.rename(destination)
        path.symlink_to(destination)
    elif change == "fifo":
        import os
        path.unlink()
        os.mkfifo(path)
        assert_error(client.get(f'/api/generation-plans/{plan["plan_id"]}'), 503, "PLAN_STORE_UNAVAILABLE")
        assert path.exists()
        return
    else:
        if change == "schema": data["schema_version"] = 2
        elif change == "boolean_schema": data["schema_version"] = True
        elif change == "wrong_hash": data["plans"][plan["plan_id"]]["content_hash"] = "0" * 64
        elif change == "wrong_id": data["plans"]["0" * 32] = data["plans"].pop(plan["plan_id"])
        path.write_text(json.dumps(data))
    before = path.read_bytes()
    assert_error(client.get(f'/api/generation-plans/{plan["plan_id"]}'), 503, "PLAN_STORE_UNAVAILABLE")
    assert path.read_bytes() == before


def test_failed_confirmation_preserves_previous_document(fixture, monkeypatch):
    from core import generation_plans
    client, _, library, _, _ = fixture
    plan = create_plan(fixture)
    path = library.directory / "generation-plans.json"
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("private-marker")

    monkeypatch.setattr(generation_plans, "atomic_write_session", fail)
    assert_error(confirm(client, plan), 503, "PLAN_STORE_UNAVAILABLE")
    assert path.read_bytes() == before
    assert client.get(f'/api/generation-plans/{plan["plan_id"]}').json()["confirmation"] is None


def test_failure_after_atomic_confirmation_can_be_safely_retried(fixture, monkeypatch):
    from core import generation_plans
    client, _, _, _, _ = fixture
    plan = create_plan(fixture)

    def fail(*args):
        raise OSError("private-marker")

    monkeypatch.setattr(generation_plans, "sync_directory", fail)
    assert_error(confirm(client, plan), 503)
    committed = client.get(f'/api/generation-plans/{plan["plan_id"]}').json()
    assert committed["confirmation"] is not None
    assert confirm(client, plan).json() == committed


def test_legacy_segment_id_and_missing_shot_duration_use_existing_defaults(fixture):
    client, engine, _, _, (asset, _, _, _, _) = fixture
    segment = engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]
    del segment["segment_id"]
    del segment["shots"][0]["duration"]
    ref = client.post("/api/asset-references", json={"project_id": "media-demo", "segment_id": "seg_01_01",
        "asset_id": asset["asset_id"], "version_id": asset["versions"][0]["version_id"]}).json()
    payload = request_for(ref)
    payload["targets"][0]["segment_id"] = "seg_01_01"
    plan = create_plan(fixture, payload)
    assert plan["content"]["targets"][0]["shots"][0]["duration"] == 5
    assert confirm(client, plan).status_code == 200


def test_five_reference_images_require_an_explicit_smaller_plan(fixture):
    client, engine, _, _, (asset, first, second, _, _) = fixture
    shots = engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]["shots"]
    shots.extend([{"shot_number": n, "duration": 5, "content": "人工镜头"} for n in [2, 3]])
    refs = [first, second]
    for number in [None, 2, 3]:
        refs.append(client.post("/api/asset-references", json={"project_id": "media-demo", "segment_id": "seg-1",
            "shot_number": number, "asset_id": asset["asset_id"], "version_id": asset["versions"][0]["version_id"]}).json())
    payload = request_for(first, mode="reference")
    payload["targets"][0]["bindings"] = [{"reference_id": ref["reference_id"], "role": "reference_image"} for ref in refs]
    assert_error(client.post("/api/generation-plans", json=payload), 409, "PLAN_BINDING_INVALID")


def test_concurrent_plan_creation_preserves_every_immutable_plan(fixture):
    client, _, _, _, (_, first, _, _, context) = fixture
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post("/api/generation-plans", json=request_for(first, context)), range(8)))
    assert all(r.status_code == 201 for r in responses)
    ids = {r.json()["plan_id"] for r in responses}
    assert len(ids) == 8 and {p["plan_id"] for p in client.get("/api/project/media-demo/generation-plans").json()} == ids


def test_concurrent_local_confirmations_are_one_durable_record(fixture):
    client, _, _, _, _ = fixture
    plan = create_plan(fixture)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: confirm(client, plan), range(8)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["confirmation"]["confirmed_at"] for r in responses}) == 1


def _confirm_worker(directory, plan_id, content_hash, queue):
    app = create_demo(Path(directory))
    service = GenerationPlans(app.dependency_overrides[get_workflow_engine](),
        app.dependency_overrides[get_asset_library](), app.dependency_overrides[get_media_archive]())
    result = service.confirm(plan_id, ConfirmInput(content_hash=content_hash, local_only=True))
    queue.put(result.confirmation.confirmed_at)


def test_confirmation_is_serialized_across_local_processes(fixture, tmp_path):
    plan = create_plan(fixture)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_confirm_worker, args=(str(tmp_path), plan["plan_id"], plan["content_hash"], queue)) for _ in range(2)]
    try:
        for process in processes: process.start()
        values = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        assert len(set(values)) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        queue.close()


def test_plan_and_input_preview_survive_actual_http_process_restart(tmp_path):
    from test_live_recovery import server

    app = create_demo(tmp_path)
    engine = app.dependency_overrides[get_workflow_engine]()
    # Reuse its established isolated layout without touching real application data.
    (tmp_path / "media-demo.json").write_bytes((tmp_path / "sessions/media-demo.json").read_bytes())
    before = engine.get_status_snapshot("media-demo")
    with server(tmp_path) as client:
        asset, first, _, _, context = seed_references(client)
        response = client.post("/api/generation-plans", json=request_for(first, context))
        assert response.status_code == 201, response.text
        plan = response.json()
        confirmed = confirm(client, plan).json()
        preview = client.post(f'/api/generation-plans/{plan["plan_id"]}/dry-run').json()
        schema = client.get("/openapi.json").json()
        for route in ["/api/generation-plans", "/api/generation-plans/{plan_id}/confirm", "/api/generation-plans/{plan_id}/dry-run"]:
            for status in ["422", "409", "503"]:
                assert schema["paths"][route]["post"]["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")
        assert client.get("/code/data/asset-library/generation-plans.json").status_code == 404
        assert client.get("/data/asset-library/generation-plans.json").status_code == 404
    for image in (tmp_path / "code/result/image/media-demo").iterdir(): image.unlink()
    with server(tmp_path) as client:
        assert client.get(f'/api/generation-plans/{plan["plan_id"]}').json() == confirmed
        assert client.post(f'/api/generation-plans/{plan["plan_id"]}/dry-run').json() == preview
        assert client.get("/api/project/media-demo/status").json() == before
        url = preview["targets"][0]["first_frame_url"]
        assert client.get(url).headers["etag"].strip('"') == asset["versions"][0]["media"]["sha256"]


def test_demo_routes_still_exclude_model_execution_and_private_config(fixture):
    client, _, _, _, _ = fixture
    for route in ["/api/project/start", "/api/config", "/api/stage/execute"]:
        assert client.post(route, json={}).status_code == 404
    assert client.post("/api/generation-plans/execute", json={}).status_code == 405
    assert "/api/generation-plans/execute" not in client.get("/openapi.json").json()["paths"]
    assert client.delete("/api/generation-plans/" + "0" * 32).status_code == 405


def test_missing_targets_and_plan_ids_are_safe(fixture):
    client, _, _, _, (_, first, _, _, context) = fixture
    payload = request_for(first, context)
    payload["project_id"] = "missing-project"
    assert_error(client.post("/api/generation-plans", json=payload), 404)
    payload["project_id"] = "media-demo"
    payload["targets"][0]["segment_id"] = "missing-segment"
    assert_error(client.post("/api/generation-plans", json=payload), 404)
    assert_error(client.get("/api/generation-plans/" + "0" * 32), 404, "PLAN_NOT_FOUND")


def test_actual_planned_prompt_pins_actions_dialogue_and_sound(fixture):
    c,e,*_=fixture
    shot=e.get_state('media-demo').artifacts['storyboard']['episodes'][0]['segments'][0]['shots'][0]
    shot.update(action='朋友挥手',dialogue='好久不见',sound='海浪声')
    e.save_session_to_disk('media-demo')
    plan=create_plan(fixture);target=plan['content']['targets'][0]
    for text in ['动作：朋友挥手','对白：好久不见','声音：海浪声']:assert text in target['prompt'] and text in target['shots'][0]['content']
    assert c.get('/api/generation-plans/'+plan['plan_id']).json()==plan
