"""Real artificial PNG/MP4 files, isolated projects, no model calls."""

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from api.dependencies import get_workflow_engine
from api.errors import register_error_handlers
from api.routers.assets import get_asset_library, get_media_archive, router
from core import asset_library, media_archive
from core.asset_library import AssetLibrary
from core.media_archive import MediaArchive
from core.orchestrator import WorkflowEngine


def seed_media_project(directory, video_bytes):
    directory = Path(directory)
    engine = WorkflowEngine(str(directory / "sessions"))
    for sid in ("media-proof", "other-project"):
        engine.create_session(sid, {"idea": "隔离人工素材，不使用模型"})
    image_dir = directory / "code" / "result" / "image" / "media-proof"
    video_dir = directory / "code" / "result" / "video" / "media-proof"
    image_dir.mkdir(parents=True)
    video_dir.mkdir(parents=True)
    images = [image_dir / "yellow.png", image_dir / "blue.png"]
    for path, color in zip(images, ("gold", "royalblue")):
        Image.new("RGB", (160, 90), color).save(path)
    video = video_dir / "clip.mp4"
    video.write_bytes(video_bytes)
    relative = [str(path.relative_to(directory)) for path in images]
    item = {"id": "role-1", "name": "人工小云雀图", "description": "黄色与蓝色人工色块",
            "selected": relative[0], "versions": relative}
    state = engine.get_state("media-proof")
    state.artifacts = {
        "character_design": {"characters": [item], "settings": [{**item, "id": "setting-1"}]},
        "reference_generation": {"scenes": [{**item, "id": "seg-1"}]},
        "video_generation": {"clips": [{"id": "seg-1", "title": "人工短片",
                                          "description": "ffmpeg 本地色块样例",
                                          "selected": str(video.relative_to(directory)),
                                          "versions": [str(video.relative_to(directory))]}]},
        "storyboard": {"episodes": [{"episode_number": 1, "segments": [
            {"segment_id": "seg-1", "shots": [{"shot_number": 1}]},
        ]}]},
    }
    engine.save_session_to_disk("media-proof")
    return engine, images, video


@pytest.fixture(scope="module")
def video_bytes(tmp_path_factory):
    path = tmp_path_factory.mktemp("artificial-mp4") / "sample.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gold:s=64x48:r=10",
                    "-t", "0.3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)],
                   check=True, capture_output=True, timeout=10)
    return path.read_bytes()


@pytest.fixture
def fixture(tmp_path, video_bytes):
    engine, images, video = seed_media_project(tmp_path, video_bytes)
    library = AssetLibrary(tmp_path / "data" / "asset-library")
    archive = MediaArchive(library.directory, tmp_path / "code" / "result", tmp_path)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_asset_library] = lambda: library
    app.dependency_overrides[get_media_archive] = lambda: archive
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, library, archive, engine, images, video


def import_request(**changes):
    return {"project_id": "media-proof", "stage": "character_design", "item_type": "characters",
            "item_id": "role-1", **changes}


def media_url(asset, index=0):
    return f'/api/assets/{asset["asset_id"]}/versions/{asset["versions"][index]["version_id"]}/media'


def selected_item(fixture):
    return fixture[3].get_state("media-proof").artifacts["character_design"]["characters"][0]


def test_archive_independent_versions_refs_and_original_media_preserved(fixture):
    client, library, archive, engine, images, _ = fixture
    snapshot = engine.get_status_snapshot("media-proof")
    response = client.post("/api/assets/import-media", json=import_request())
    assert response.status_code == 200
    asset = response.json()
    first = asset["versions"][0]
    assert asset["kind"] == "image" and first["media"]["width"] == 160
    assert first["source"] == {"type": "artifact", "project_id": "media-proof", "stage": "character_design",
                               "item_type": "characters", "item_id": "role-1"}
    assert str(images[0]) not in response.text and "code/result" not in response.text
    assert client.post("/api/assets/import-media", json=import_request()).json() == asset
    original = images[0].read_bytes()
    received = client.get(media_url(asset))
    assert received.content == original
    assert received.headers["content-type"] == "image/png"
    assert received.headers["etag"] == '"' + hashlib.sha256(original).hexdigest() + '"'
    assert received.headers["x-content-type-options"] == "nosniff"
    assert "private" in received.headers["cache-control"]
    ref = client.post("/api/asset-references", json={
        "asset_id": asset["asset_id"], "version_id": first["version_id"],
        "project_id": "media-proof", "segment_id": "seg-1", "shot_number": 1,
    }).json()
    changed = client.post("/api/assets/import-media", json=import_request(candidate_number=2)).json()
    assert changed["asset_id"] == asset["asset_id"] and len(changed["versions"]) == 2
    assert changed["versions"][0] == first
    assert engine.get_status_snapshot("media-proof") == snapshot
    assert client.get(f'/api/assets/{asset["asset_id"]}/usage').json()[0]["has_newer_version"]
    assert library.read().references[ref["reference_id"]].version_id == first["version_id"]
    next_id = changed["versions"][1]["version_id"]
    route = f'/api/asset-references/{ref["reference_id"]}'
    assert client.patch(route, json={"version_id": next_id, "expected_revision": 1}).json()["revision"] == 2
    rolled_back = client.patch(route, json={"version_id": first["version_id"], "expected_revision": 2}).json()
    assert rolled_back["revision"] == 3 and rolled_back["has_newer_version"]
    images[0].write_bytes(b"original file changed")
    images[1].unlink()
    assert client.get(media_url(asset)).content == original
    assert hashlib.sha256(client.get(media_url(changed, 1)).content).hexdigest() == changed["versions"][1]["media"]["sha256"]
    assert len(list(archive.directory.glob("*.png"))) == 2


@pytest.mark.parametrize("stage,item_type,item_id,kind,mime", [
    ("character_design", "settings", "setting-1", "image", "image/png"),
    ("reference_generation", "scenes", "seg-1", "image", "image/png"),
    ("video_generation", "clips", "seg-1", "video", "video/mp4"),
])
def test_existing_stage_media_can_be_archived(fixture, stage, item_type, item_id, kind, mime):
    client, _, _, engine, _, video = fixture
    response = client.post("/api/assets/import-media", json=import_request(stage=stage, item_type=item_type, item_id=item_id))
    assert response.status_code == 200
    asset = response.json()
    assert asset["kind"] == kind
    assert client.get(media_url(asset)).headers["content-type"] == mime
    if kind == "video":
        assert asset["versions"][0]["media"]["duration"] > 0
        assert client.get(media_url(asset)).content == video.read_bytes()
        ranged = client.get(media_url(asset), headers={"Range": "bytes=0-15"})
        assert ranged.status_code == 206 and ranged.content == video.read_bytes()[:16]


@pytest.mark.parametrize("extension,format,mime", [
    (".jpg", "JPEG", "image/jpeg"), (".jpeg", "JPEG", "image/jpeg"),
    (".webp", "WEBP", "image/webp"), (".bmp", "BMP", "image/bmp"),
])
def test_image_formats_checked_from_real_content(fixture, extension, format, mime):
    client, _, _, _, images, _ = fixture
    path = images[0].with_suffix(extension)
    Image.new("RGB", (24, 18), "green").save(path, format=format)
    selected_item(fixture)["selected"] = str(path)
    response = client.post("/api/assets/import-media", json=import_request())
    assert response.status_code == 200
    asset = response.json()
    assert asset["versions"][0]["media"]["mime_type"] == mime
    assert client.get(media_url(asset)).content == path.read_bytes()


@pytest.mark.parametrize("changes,status", [
    ({"stage": "video_generation", "item_type": "characters"}, 422),
    ({"candidate_number": 0}, 422), ({"candidate_number": True}, 422),
    ({"candidate_number": 99}, 422), ({"item_id": "missing"}, 404),
    ({"project_id": "missing"}, 404), ({"path": "private-artificial-input"}, 422),
])
def test_invalid_selection_requests_do_not_create_assets(fixture, changes, status):
    client, library, _, _, _, _ = fixture
    response = client.post("/api/assets/import-media", json=import_request(**changes))
    assert response.status_code == status and "private-artificial-input" not in response.text
    assert library.read().assets == {}


@pytest.mark.parametrize("selection,status", [("", 409), ("https://example.com/private.png", 415), ("//server/private.png", 415)])
def test_no_selection_or_remote_urls_are_not_fetched(fixture, selection, status):
    selected_item(fixture)["selected"] = selection
    response = fixture[0].post("/api/assets/import-media", json=import_request())
    assert response.status_code == status and "private.png" not in response.text


def test_cross_project_outside_and_symlink_paths_rejected(fixture, tmp_path):
    client, library, _, _, images, _ = fixture
    outside = tmp_path / "private-artificial.png"
    outside.write_bytes(images[0].read_bytes())
    foreign = images[0].parent.parent / "other-project" / "image.png"
    foreign.parent.mkdir()
    foreign.write_bytes(images[0].read_bytes())
    link = images[0].parent / "escape.png"
    link.symlink_to(outside)
    for path in (outside, foreign, link):
        selected_item(fixture)["selected"] = str(path)
        response = client.post("/api/assets/import-media", json=import_request())
        assert response.status_code == 404 and "private-artificial" not in response.text
    assert library.read().assets == {}


@pytest.mark.parametrize("data,suffix", [(b"not an image", ".png"), (b"{private-artificial-json}", ".jpg"), (b"none", ".svg")])
def test_invalid_image_content_and_extensions_rejected(fixture, data, suffix):
    client, library, archive, _, images, _ = fixture
    path = images[0].parent / ("invalid" + suffix)
    path.write_bytes(data)
    selected_item(fixture)["selected"] = str(path)
    response = client.post("/api/assets/import-media", json=import_request())
    assert response.status_code == 415 and "private-artificial" not in response.text
    assert library.read().assets == {}
    assert list(archive.directory.glob(".staging-*")) == []


def test_extension_mismatch_and_truncated_image_rejected(fixture):
    client, _, _, _, images, _ = fixture
    mismatched = images[0].with_suffix(".jpg")
    mismatched.write_bytes(images[0].read_bytes())
    selected_item(fixture)["selected"] = str(mismatched)
    assert client.post("/api/assets/import-media", json=import_request()).status_code == 415
    images[0].write_bytes(images[0].read_bytes()[:60])
    selected_item(fixture)["selected"] = str(images[0])
    assert client.post("/api/assets/import-media", json=import_request()).status_code == 415


@pytest.mark.parametrize("kind", ["image", "video"])
def test_size_limit_enforced_before_decoding(fixture, kind):
    client, library, _, engine, images, video = fixture
    path, limit = (images[0], media_archive.IMAGE_LIMIT) if kind == "image" else (video, media_archive.VIDEO_LIMIT)
    with path.open("wb") as stream:
        stream.truncate(limit + 1)
    request = import_request() if kind == "image" else import_request(stage="video_generation", item_type="clips", item_id="seg-1")
    assert client.post("/api/assets/import-media", json=request).status_code == 413
    assert library.read().assets == {}


def test_real_image_pixel_limit(fixture):
    client, _, _, _, images, _ = fixture
    Image.new("1", (6400, 6400)).save(images[0])
    assert client.post("/api/assets/import-media", json=import_request()).status_code == 413


@pytest.mark.parametrize("data", [b"not a video", b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00"])
def test_invalid_video_signature_and_container_rejected(fixture, data):
    client, _, _, _, _, video = fixture
    video.write_bytes(data)
    assert client.post("/api/assets/import-media", json=import_request(stage="video_generation", item_type="clips", item_id="seg-1")).status_code == 415


@pytest.mark.parametrize("failure", ["missing", "timeout"])
def test_probe_failures_are_safe(fixture, monkeypatch, failure):
    client = fixture[0]
    if failure == "missing":
        monkeypatch.setattr(media_archive.shutil, "which", lambda _: None)
    else:
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("private-artificial-command", 10)
        monkeypatch.setattr(media_archive.subprocess, "run", timeout)
    response = client.post("/api/assets/import-media", json=import_request(stage="video_generation", item_type="clips", item_id="seg-1"))
    assert response.status_code == 503 and "private-artificial" not in response.text


def test_failed_metadata_write_cleans_only_uncommitted_copy(fixture, monkeypatch):
    client, library, archive, _, _, _ = fixture
    asset = client.post("/api/assets/import-media", json=import_request()).json()
    before = library.path.read_bytes()
    files = set(archive.directory.iterdir())

    def fail(*args):
        raise OSError("private-artificial-error")

    monkeypatch.setattr(asset_library, "atomic_write_session", fail)
    response = client.post("/api/assets/import-media", json=import_request(candidate_number=2))
    assert response.status_code == 503 and "private-artificial" not in response.text
    assert library.path.read_bytes() == before
    assert set(archive.directory.iterdir()) == files
    assert client.get(media_url(asset)).status_code == 200


def test_exception_after_commit_never_deletes_committed_media(fixture, monkeypatch):
    client, library, archive, _, _, _ = fixture
    actual_write = asset_library.atomic_write_session

    def committed_then_error(*args):
        actual_write(*args)
        raise OSError("artificial post-commit error")

    monkeypatch.setattr(asset_library, "atomic_write_session", committed_then_error)
    assert client.post("/api/assets/import-media", json=import_request()).status_code == 503
    asset = client.get("/api/assets").json()[0]
    assert client.get(media_url(asset)).status_code == 200
    assert client.post("/api/assets/import-media", json=import_request()).json() == asset


@pytest.mark.parametrize("corruption", ["missing", "changed", "symlink"])
def test_missing_or_corrupted_archive_never_serves_wrong_bytes(fixture, tmp_path, corruption):
    client, library, archive, _, images, _ = fixture
    asset = client.post("/api/assets/import-media", json=import_request()).json()
    version = library.read().assets[asset["asset_id"]].versions[0]
    path = archive.path(version.media)
    if corruption == "missing":
        path.unlink()
    elif corruption == "changed":
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
    else:
        path.unlink()
        path.symlink_to(images[0])
    response = client.get(media_url(asset))
    assert response.status_code == 503 and response.json()["error"]["code"] == "ARCHIVED_MEDIA_UNAVAILABLE"


def test_media_directory_symlink_rejected(fixture, tmp_path):
    client, library, archive, _, _, _ = fixture
    library.directory.mkdir(parents=True)
    target = tmp_path / "unsafe"
    target.mkdir()
    archive.directory.symlink_to(target)
    assert client.post("/api/assets/import-media", json=import_request()).status_code == 503
    assert list(target.iterdir()) == []


def test_text_api_cannot_add_fileless_media_versions(fixture):
    client = fixture[0]
    asset = client.post("/api/assets/import-media", json=import_request()).json()
    response = client.post(f'/api/assets/{asset["asset_id"]}/versions', json={"name": "图片", "definition": "人工"})
    assert response.status_code == 409
    assert client.post("/api/assets", json={"kind": "video", "name": "视频", "definition": "人工"}).status_code == 422


def test_old_text_library_without_media_field_remains_readable(fixture):
    client, library, _, _, _, _ = fixture
    old = client.post("/api/assets", json={"kind": "prop", "name": "信件", "definition": "文字"}).json()
    document = json.loads(library.path.read_text())
    document["assets"][old["asset_id"]]["versions"][0].pop("media")
    library.path.write_text(json.dumps(document))
    assert client.get(f'/api/assets/{old["asset_id"]}').json()["versions"][0]["media"] is None
    assert client.post("/api/assets/import-media", json=import_request()).status_code == 200
    assert client.get(f'/api/assets/{old["asset_id"]}').json()["versions"][0]["definition"] == "文字"


def test_concurrent_identical_archives_are_idempotent(fixture):
    client, library, archive, _, _, _ = fixture
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post("/api/assets/import-media", json=import_request()).json(), range(4)))
    assert all(result == results[0] for result in results)
    assert len(library.read().assets) == 1
    assert len(list(archive.directory.glob("*.png"))) == 1
    assert not list(archive.directory.glob(".staging-*"))


@pytest.mark.parametrize("stage,item_type,key", [
    ("reference_generation", "scenes", "scene_images"),
    ("reference_generation", "scenes", "scene_map"),
    ("video_generation", "clips", "video_clips"),
    ("video_generation", "clips", "video_map"),
])
def test_legacy_mapping_can_be_explicitly_archived(fixture, stage, item_type, key):
    client, _, _, engine, _, video = fixture
    value = str(video) if stage == "video_generation" else selected_item(fixture)["selected"]
    engine.get_state("media-proof").artifacts[stage] = {key: {"seg-1": value}}
    assert client.post("/api/assets/import-media", json=import_request(stage=stage, item_type=item_type, item_id="seg-1")).status_code == 200


def test_source_project_directory_symlink_cannot_change_allowed_boundary(fixture, tmp_path):
    client, library, _, _, images, _ = fixture
    parent = images[0].parent
    moved = tmp_path / "outside-project"
    parent.rename(moved)
    parent.symlink_to(moved)
    assert client.post("/api/assets/import-media", json=import_request()).status_code == 404
    assert library.read().assets == {}


def test_publish_sync_failure_does_not_commit_dangling_version(fixture, monkeypatch):
    client, library, archive, _, images, _ = fixture

    def fail(*args):
        raise OSError("private-artificial-sync")

    monkeypatch.setattr(media_archive, "sync_directory", fail)
    response = client.post("/api/assets/import-media", json=import_request())
    assert response.status_code == 503 and "private-artificial-sync" not in response.text
    assert library.read().assets == {}
    assert list(archive.directory.iterdir()) == []
    assert images[0].is_file()


def test_real_http_restart_restores_media_bytes_and_version_references(tmp_path, video_bytes):
    from test_live_recovery import server

    engine, images, video = seed_media_project(tmp_path, video_bytes)
    # The server reads projects from its supplied session directory.
    for path in (tmp_path / "sessions").glob("*.json"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    snapshot = engine.get_status_snapshot("media-proof")
    original_image, original_video = images[0].read_bytes(), video.read_bytes()
    with server(tmp_path) as client:
        first = client.post("/api/assets/import-media", json=import_request()).json()
        second = client.post("/api/assets/import-media", json=import_request(candidate_number=2)).json()
        film = client.post("/api/assets/import-media", json=import_request(stage="video_generation", item_type="clips", item_id="seg-1")).json()
        response = client.post("/api/asset-references", json={
            "asset_id": first["asset_id"], "version_id": first["versions"][0]["version_id"],
            "project_id": "media-proof", "segment_id": "seg-1", "shot_number": 1,
        })
        assert response.status_code == 201
        ref = response.json()
        route = f'/api/asset-references/{ref["reference_id"]}'
        ref = client.patch(route, json={"version_id": second["versions"][1]["version_id"], "expected_revision": 1}).json()
        assert ref["revision"] == 2 and len(ref["history"]) == 2
        schema = client.get("/openapi.json").json()
        assert "video/mp4" in schema["paths"]["/api/assets/{asset_id}/versions/{version_id}/media"]["get"]["responses"]["200"]["content"]
        assert client.get("/api/project/media-proof/status").json() == snapshot
    # Independent snapshots remain usable after original sources are removed.
    images[0].unlink()
    images[1].unlink()
    video.unlink()
    with server(tmp_path) as client:
        assert client.get(f'/api/assets/{second["asset_id"]}').json() == second
        assert client.get(media_url(first)).content == original_image
        assert client.get(media_url(film)).content == original_video
        assert hashlib.sha256(client.get(media_url(second, 1)).content).hexdigest() == second["versions"][1]["media"]["sha256"]
        assert client.get("/api/project/media-proof/asset-references").json() == [ref]
        assert client.get("/api/project/media-proof/status").json() == snapshot
        assert client.patch(route, json={"version_id": first["versions"][0]["version_id"], "expected_revision": 1}).status_code == 409
        assert client.get("/code/data/asset-library/library.json").status_code == 404
        assert client.get("/data/asset-library/media/" + first["versions"][0]["media"]["file_id"] + ".png").status_code == 404


def test_artificial_acceptance_app_excludes_paid_routes_and_preserves_records(tmp_path):
    from api.media_demo import create_demo

    directory = tmp_path / "demo"
    with TestClient(create_demo(directory)) as client:
        for path in ("/api/project/start", "/api/project/media-demo/execute/script_generation", "/api/config"):
            assert client.post(path, json={}).status_code == 404
        response = client.post("/api/assets/import-media", json=import_request(project_id="media-demo"))
        assert response.status_code == 200
        asset = response.json()
    with TestClient(create_demo(directory)) as client:
        assert client.get("/api/assets").json() == [asset]
        assert client.get(media_url(asset)).status_code == 200
