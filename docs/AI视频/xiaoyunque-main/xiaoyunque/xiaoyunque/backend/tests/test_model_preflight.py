"""Real archived artificial images and local HTTP recovery; no model calls."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.dependencies import get_workflow_engine
from api.media_demo import create_demo
from api.routers.assets import get_asset_library, get_media_archive
from api.routers.model_preflight import get_preflight_connection
from api.schemas.generation_plans import PlanContent
from core import model_preflight
from core.generation_plans import GenerationPlans
from core.model_preflight import ModelPreflight, check_connection, inspect_target
from test_generation_plans import assert_error, confirm, request_for, seed_references


@pytest.fixture
def fixture(tmp_path):
    app = create_demo(tmp_path)
    engine = app.dependency_overrides[get_workflow_engine]()
    library = app.dependency_overrides[get_asset_library]()
    archive = app.dependency_overrides[get_media_archive]()
    connection = check_connection("", "https://dashscope.aliyuncs.com/api/v1")
    app.dependency_overrides[get_preflight_connection] = lambda: connection
    service = ModelPreflight(GenerationPlans(engine, library, archive), connection)
    with TestClient(app, raise_server_exceptions=False) as client:
        references = seed_references(client)
        yield client, engine, library, archive, references, service, app, tmp_path


def plan_for(fixture, options=None, duration=5, confirmed=True, mode="first_frame"):
    client, _, _, _, (_, first, second, _, context), _, _, _ = fixture
    payload = request_for(first, context, mode, second if mode != "first_frame" else None)
    payload["options"].update({"model": "wan2.7-i2v", **(options or {})})
    payload["targets"][0]["duration"] = duration
    response = client.post("/api/generation-plans", json=payload)
    assert response.status_code == 201, response.text
    plan = response.json()
    if confirmed: assert confirm(client, plan).status_code == 200
    return plan


def report_for(client, plan):
    response = client.post(f'/api/generation-plans/{plan["plan_id"]}/wan-preflight')
    assert response.status_code == 201, response.text
    return response.json()


def codes(report):
    return {i["code"] for i in report["blockers"]} | {i["code"] for t in report["targets"] for i in t["issues"]}


def replace_selected_image(fixture, size, mode="RGB", transparent_palette=False):
    client, _, _, _, (_, first, _, _, _), _, _, tmp_path = fixture
    image = Image.new(mode, size)
    if transparent_palette: image.info["transparency"] = 0
    image.save(tmp_path / "code/result/image/media-demo/yellow.png")
    asset = client.post("/api/assets/import-media", json={"project_id": "media-demo", "stage": "character_design",
        "item_type": "characters", "item_id": "role-1", "candidate_number": 1}).json()
    response = client.patch(f'/api/asset-references/{first["reference_id"]}', json={
        "version_id": asset["versions"][-1]["version_id"], "expected_revision": first["revision"]})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("mode", ["first_frame", "start_end_frame"])
def test_valid_inputs_compile_exact_fixed_content_without_outbound_calls(fixture, monkeypatch, mode):
    import requests
    from models.video_client import VideoClient
    from core.task_provider import LocalSimulationProvider

    def forbidden(*args, **kwargs): raise AssertionError("Offline preflight cannot call a provider or network")
    monkeypatch.setattr(requests, "post", forbidden)
    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(VideoClient, "__init__", forbidden)
    monkeypatch.setattr(VideoClient, "generate_video", forbidden)
    monkeypatch.setattr(LocalSimulationProvider, "submit", forbidden)
    client, engine, library, archive, _, service, _, _ = fixture
    plan = plan_for(fixture, mode=mode)
    before = engine.get_status_snapshot("media-demo")
    content = PlanContent.model_validate(plan["content"])
    check, request = inspect_target(content, content.targets[0], archive, True)
    assert check.input_protocol_ready and request.input.prompt == content.targets[0].prompt
    assert request.parameters.model_dump() == {"duration": 5, "resolution": "720P", "prompt_extend": False, "watermark": False}
    assert [m.type for m in request.input.media] == (["first_frame"] if mode == "first_frame" else ["first_frame", "last_frame"])
    assert "shot_type" not in request.model_dump()["parameters"] and "ratio" not in request.model_dump()["parameters"]
    for media, binding in zip(request.input.media, [b for b in content.targets[0].bindings if b.role != "context"]):
        data = base64.b64decode(media.url.split(",", 1)[1], validate=True)
        assert hashlib.sha256(data).hexdigest() == binding.version.media.sha256
    report = report_for(client, plan)
    assert report["input_protocol_ready"] and report["current"] and report["targets"][0]["request_sha256"] == check.request_sha256
    assert report["real_execution_allowed"] is False and report["network_used"] is False
    assert report["amount_minor"] is None and report["currency"] is None
    assert {"KEY_MISSING", "REAL_EXECUTION_DISABLED", "REAL_ADAPTER_NOT_IMPLEMENTED", "REAL_PRICING_UNCONFIGURED"} <= codes(report)
    assert report["recovery"]["query_by_client_request_id"] is False
    assert report["recovery"]["missing_task_id_action"] == "manual_review_no_resubmit"
    public = json.dumps(report)
    stored = service.path.read_text()
    for text in [content.targets[0].prompt, "data:image", str(library.directory), "image_path"]:
        assert text not in public and text not in stored
    assert engine.get_status_snapshot("media-demo") == before
    assert client.get(f'/api/model-preflights/{report["report_id"]}').json() == report


@pytest.mark.parametrize("duration,ready", [(1, False), (2, True), (15, True), (16, False), (60, False)])
def test_duration_bounds_do_not_silently_adjust_fixed_plan(fixture, duration, ready):
    client = fixture[0]
    report = report_for(client, plan_for(fixture, duration=duration))
    assert report["input_protocol_ready"] is ready
    assert report["targets"][0]["duration"] == duration
    assert ("DURATION_UNSUPPORTED" in codes(report)) != ready


@pytest.mark.parametrize("options,mode,code", [
    ({"model": "local-test-model"}, "first_frame", "MODEL_NOT_ADAPTED"),
    ({"model": "wan2.7-r2v"}, "reference", "MODE_NOT_ADAPTED"),
    ({"model": "wan2.6-i2v"}, "first_frame", "MODEL_NOT_ADAPTED"),
    ({"resolution": "480P"}, "first_frame", "RESOLUTION_UNSUPPORTED"),
    ({"sound": "off"}, "first_frame", "SOUND_OFF_UNSUPPORTED"),
])
def test_unsupported_contract_is_visible_and_never_compiled(fixture, options, mode, code):
    client = fixture[0]
    report = report_for(client, plan_for(fixture, options=options, mode=mode))
    assert not report["input_protocol_ready"] and code in codes(report)
    assert report["targets"][0]["request_sha256"] is None


def test_long_prompt_is_not_truncated_or_returned_to_public_report(fixture):
    client, engine, _, _, _, _, _, _ = fixture
    engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]["shots"][0]["content"] = "人工素材文字" * 1000
    engine.save_session_to_disk("media-demo")
    plan = plan_for(fixture)
    report = report_for(client, plan)
    assert "PROMPT_TOO_LONG" in codes(report) and not report["input_protocol_ready"]
    assert report["targets"][0]["prompt_characters"] == len(plan["content"]["targets"][0]["prompt"])
    assert report["targets"][0]["request_sha256"] is None


@pytest.mark.parametrize("size,mode,palette,code", [
    ((320, 180), "RGB", False, "IMAGE_DIMENSIONS_UNSUPPORTED"),
    ((8001, 240), "RGB", False, "IMAGE_DIMENSIONS_UNSUPPORTED"),
    ((2400, 240), "RGB", False, "IMAGE_RATIO_UNSUPPORTED"),
    ((640, 360), "RGBA", False, "PNG_ALPHA_UNSUPPORTED"),
    ((640, 360), "P", True, "PNG_ALPHA_UNSUPPORTED"),
])
def test_actual_image_constraints_without_mutating_or_flattening_asset(fixture, size, mode, palette, code):
    replace_selected_image(fixture, size, mode, palette)
    client, _, library, _, _, _, _, _ = fixture
    plan = plan_for(fixture)
    before = library.read().model_dump()
    report = report_for(client, plan)
    assert code in codes(report) and not report["input_protocol_ready"]
    assert library.read().model_dump() == before


def test_ratio_warning_is_explicit_and_does_not_invent_platform_field(fixture):
    client, _, _, archive, _, _, _, _ = fixture
    plan = plan_for(fixture, options={"video_ratio": "9:16"})
    report = report_for(client, plan)
    assert report["input_protocol_ready"] and "RATIO_DERIVED_FROM_MEDIA" in codes(report)
    content = PlanContent.model_validate(plan["content"])
    _, request = inspect_target(content, content.targets[0], archive, True)
    assert "ratio" not in request.parameters.model_dump()


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_missing_or_changed_archived_image_produces_safe_blocked_report(fixture, damage):
    client, _, _, archive, _, _, _, _ = fixture
    plan = plan_for(fixture)
    media = PlanContent.model_validate(plan["content"]).targets[0].bindings[0].version.media
    path = archive.verify_file(media)
    if damage == "missing": path.unlink()
    else: path.write_bytes(b"private-marker")
    report = report_for(client, plan)
    assert "MEDIA_UNAVAILABLE" in codes(report) and not report["input_protocol_ready"]
    assert "private-marker" not in json.dumps(report)


@pytest.mark.parametrize("url,region", [
    ("https://dashscope.aliyuncs.com/api/v1", "beijing"),
    ("https://workspace-123.cn-beijing.maas.aliyuncs.com/api/v1/", "beijing"),
    ("https://dashscope-intl.aliyuncs.com/api/v1", "singapore"),
    ("https://workspace-123.ap-southeast-1.maas.aliyuncs.com/api/v1", "singapore"),
    ("http://dashscope.aliyuncs.com/api/v1", "unrecognized"),
    ("https://dashscope.aliyuncs.com.attacker.invalid/api/v1", "unrecognized"),
    ("https://private-marker@dashscope.aliyuncs.com/api/v1", "unrecognized"),
    ("https://dashscope.aliyuncs.com:443/api/v1", "unrecognized"),
    ("https://dashscope.aliyuncs.com/api/v1?private-marker", "unrecognized"),
    ("https://dashscope.aliyuncs.com/api/v1#private-marker", "unrecognized"),
    ("https://dashscope.aliyuncs.com:bad/api/v1", "unrecognized"),
    ("https://localhost/api/v1", "unrecognized"),
    ("https://dashscope.aliyu\nncs.com/api/v1", "unrecognized"),
    (" https://dashscope.aliyuncs.com/api/v1", "unrecognized"),
    (None, "unrecognized"),
])
def test_endpoint_classification_never_returns_hostname_key_or_original_url(url, region):
    check = check_connection("private-marker", url)
    assert check.endpoint_region == region and check.endpoint_allowed == (region == "beijing")
    assert check.credential_present and not check.authentication_verified
    assert "private-marker" not in check.model_dump_json() and "workspace" not in check.model_dump_json()


@pytest.mark.parametrize("key", [None, "", "  "])
def test_blank_keys_are_missing_and_not_echoed(key):
    assert not check_connection(key, "https://dashscope.aliyuncs.com/api/v1").credential_present


def test_present_key_and_singapore_endpoint_cannot_allow_real_execution(fixture):
    client, _, _, _, _, _, app, _ = fixture
    app.dependency_overrides[get_preflight_connection] = lambda: check_connection("private-marker", "https://dashscope-intl.aliyuncs.com/api/v1")
    report = report_for(client, plan_for(fixture))
    assert report["input_protocol_ready"] and report["connection"]["credential_present"]
    assert "NON_MAINLAND_ENDPOINT" in codes(report) and "AUTHENTICATION_UNVERIFIED" in codes(report)
    assert not report["real_execution_allowed"] and not report["sample_rights_verified"]
    assert "private-marker" not in json.dumps(report)


def test_unconfirmed_or_changed_plan_never_compiles_and_history_remains(fixture):
    client, engine, _, _, _, _, _, _ = fixture
    plan = plan_for(fixture, confirmed=False)
    draft = report_for(client, plan)
    assert not draft["input_protocol_ready"] and "PLAN_NOT_CONFIRMED" in codes(draft)
    assert draft["targets"][0]["request_sha256"] is None
    assert confirm(client, plan).status_code == 200
    assert client.get(f'/api/model-preflights/{draft["report_id"]}').json()["current"] is False
    confirmed = report_for(client, plan)
    engine.get_state("media-demo").artifacts["storyboard"]["episodes"][0]["segments"][0]["shots"][0]["content"] = "人工修订"
    engine.save_session_to_disk("media-demo")
    assert not client.get(f'/api/model-preflights/{confirmed["report_id"]}').json()["current"]
    stale = report_for(client, plan)
    assert "PLAN_SOURCE_CHANGED" in codes(stale) and stale["targets"][0]["request_sha256"] is None


def test_configuration_classification_change_marks_report_stale(fixture):
    client, _, _, _, _, _, app, _ = fixture
    report = report_for(client, plan_for(fixture))
    app.dependency_overrides[get_preflight_connection] = lambda: check_connection("private-marker", "https://dashscope.aliyuncs.com/api/v1")
    assert client.get(f'/api/model-preflights/{report["report_id"]}').json()["current"] is False


@pytest.mark.parametrize("payload", [{"real_execution_allowed": True}, {"api_key": "private-marker"}, {"base_url": "private-marker"}, {"amount_minor": 0}, {"model": "wan2.7-i2v"}])
def test_client_cannot_override_connection_price_or_execution(fixture, payload):
    client = fixture[0]
    plan = plan_for(fixture)
    assert_error(client.post(f'/api/generation-plans/{plan["plan_id"]}/wan-preflight', json=payload), 422)


@pytest.mark.parametrize("damage", ["json", "future", "bool_schema", "missing", "duplicate", "link", "id_mismatch", "execution_enabled"])
def test_corrupt_report_store_is_preserved(fixture, damage):
    client, _, _, _, _, service, _, _ = fixture
    report = report_for(client, plan_for(fixture))
    path = service.path
    data = json.loads(path.read_text())
    if damage == "json": path.write_text("private-marker")
    elif damage == "duplicate": path.write_text('{"schema_version":1,"schema_version":1,"reports":{}}')
    elif damage == "link":
        target = path.with_suffix(".backup"); path.rename(target); path.symlink_to(target)
    else:
        if damage == "future": data["schema_version"] = 2
        elif damage == "bool_schema": data["schema_version"] = True
        elif damage == "missing": del data["reports"]
        elif damage == "id_mismatch": data["reports"][report["report_id"]]["report_id"] = "0" * 32
        elif damage == "execution_enabled": data["reports"][report["report_id"]]["real_execution_allowed"] = True
        path.write_text(json.dumps(data))
    before = path.read_bytes()
    assert_error(client.get(f'/api/model-preflights/{report["report_id"]}'), 503, "PREFLIGHT_STORE_UNAVAILABLE")
    assert path.read_bytes() == before


def test_failed_report_save_preserves_previous_report_and_plan(fixture, monkeypatch):
    client, _, _, _, _, service, _, _ = fixture
    plan = plan_for(fixture)
    report = report_for(client, plan)
    before = service.path.read_bytes()
    plan_before = service.plans.path.read_bytes()
    monkeypatch.setattr(model_preflight, "atomic_write_session", lambda *args: (_ for _ in ()).throw(OSError("private-marker")))
    assert_error(client.post(f'/api/generation-plans/{plan["plan_id"]}/wan-preflight'), 503, "PREFLIGHT_STORE_UNAVAILABLE")
    assert service.path.read_bytes() == before and service.plans.path.read_bytes() == plan_before
    assert client.get(f'/api/model-preflights/{report["report_id"]}').json() == report


def test_image_changed_during_request_compilation_cannot_produce_request_digest(fixture, monkeypatch):
    client, _, _, archive, _, _, _, _ = fixture
    plan = plan_for(fixture)
    media = PlanContent.model_validate(plan["content"]).targets[0].bindings[0].version.media
    path = archive.verify_file(media)
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"private-marker" if self == path else original(self))
    report = report_for(client, plan)
    assert "MEDIA_UNAVAILABLE" in codes(report) and not report["input_protocol_ready"]
    assert report["targets"][0]["request_sha256"] is None


def test_missing_ids_and_invalid_paths_return_uniform_errors(fixture):
    client = fixture[0]
    assert_error(client.get("/api/model-preflights/" + "0" * 32), 404)
    assert_error(client.post("/api/generation-plans/" + "0" * 32 + "/wan-preflight"), 404)
    assert_error(client.get("/api/model-preflights/not-an-id"), 422)


def test_actual_http_restart_preserves_preflight_and_does_not_create_tasks(tmp_path):
    from test_live_recovery import server
    # Save the artificial workflow in the directory used by the isolated server.
    app = create_demo(tmp_path)
    engine = app.dependency_overrides[get_workflow_engine]()
    engine._session_dir = str(tmp_path)
    engine.save_session_to_disk("media-demo")
    with server(tmp_path) as client:
        _, first, _, _, context = seed_references(client)
        payload = request_for(first, context)
        payload["options"]["model"] = "wan2.7-i2v"
        plan = client.post("/api/generation-plans", json=payload).json()
        assert confirm(client, plan).status_code == 200
        report = report_for(client, plan)
        assert report["input_protocol_ready"]
        tasks_before = client.get("/api/project/media-demo/generation-tasks").json()
    with server(tmp_path) as client:
        assert client.get(f'/api/model-preflights/{report["report_id"]}').json() == report
        assert client.get("/api/project/media-demo/generation-tasks").json() == tasks_before == []
        assert client.get("/api/project/media-demo/generation-budget").json()["currency"] is None
