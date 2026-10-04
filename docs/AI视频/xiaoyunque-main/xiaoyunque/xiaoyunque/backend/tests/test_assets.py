"""Artificial, offline asset workflows; never read or modify user projects."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_workflow_engine
from api.errors import register_error_handlers
from api.routers.assets import get_asset_library, router
from api.schemas.assets import AssetInput, VersionInput
from api.schemas.assets import ReferenceInput, ReferenceUpdate
from core import asset_library
from core.asset_library import AssetLibrary, AssetLibraryError
from core.orchestrator import WorkflowEngine


@pytest.fixture
def fixture(tmp_path):
    engine = WorkflowEngine(str(tmp_path / "sessions"))
    for sid in ("project-a", "project-b"):
        engine.create_session(sid, {"idea": "资产引用人工测试"})
        state = engine.get_state(sid)
        state.artifacts = {
            "script_generation": {
                "characters": [{"id": "role-1", "name": "小云雀", "description": "勇敢的信使"}],
                "settings": [{"id": "setting-1", "name": "山谷", "description": "清晨的山谷"}],
            },
            "storyboard": {"episodes": [{"episode_number": 1, "segments": [
                {"segment_id": "seg-1", "shots": [{"shot_number": 1, "content": "人工镜头"}]},
            ]}]},
        }
        engine.save_session_to_disk(sid)
    library = AssetLibrary(tmp_path / "library")
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_asset_library] = lambda: library
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, library, engine


def create(client, kind="character"):
    response = client.post("/api/assets", json={"kind": kind, "name": "小云雀", "definition": "黄色羽毛"})
    assert response.status_code == 201
    return response.json()


def bind(client, asset, project="project-a", **target):
    return client.post("/api/asset-references", json={
        "project_id": project, "asset_id": asset["asset_id"],
        "version_id": asset["versions"][0]["version_id"], **target,
    })


def test_versions_pin_cross_project_references_upgrade_and_rollback(fixture):
    client, library, engine = fixture
    before = engine.get_status_snapshot("project-a")
    asset = create(client)
    old_version = asset["versions"][0]
    first = bind(client, asset, segment_id="seg-1", shot_number=1).json()
    second = bind(client, asset, "project-b").json()
    changed = client.post(f'/api/assets/{asset["asset_id"]}/versions', json={
        "name": "小云雀", "definition": "蓝色羽毛",
    }).json()
    assert changed["versions"][0] == old_version
    refs = client.get(f'/api/assets/{asset["asset_id"]}/usage').json()
    assert len(refs) == 2
    assert all(r["version_id"] == old_version["version_id"] and r["has_newer_version"] for r in refs)
    new_version = changed["versions"][-1]["version_id"]
    route = f'/api/asset-references/{first["reference_id"]}'
    upgraded = client.patch(route, json={"version_id": new_version, "expected_revision": 1}).json()
    assert upgraded["revision"] == 2 and not upgraded["has_newer_version"]
    assert [h["version_id"] for h in upgraded["history"]] == [old_version["version_id"], new_version]
    conflict = client.patch(route, json={"version_id": old_version["version_id"], "expected_revision": 1})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "REFERENCE_CONFLICT"
    assert client.patch(route, json={"version_id": new_version, "expected_revision": 2}).json() == upgraded
    rolled_back = client.patch(route, json={"version_id": old_version["version_id"], "expected_revision": 2}).json()
    assert rolled_back["revision"] == 3 and rolled_back["has_newer_version"]
    assert library.read().references[second["reference_id"]].revision == 1
    assert engine.get_status_snapshot("project-a") == before
    assert len(AssetLibrary(library.directory).read().assets) == 1


@pytest.mark.parametrize("kind,item", [("character", "role-1"), ("setting", "setting-1")])
def test_existing_script_import_is_explicit_idempotent_and_preserves_source(fixture, kind, item):
    client, library, engine = fixture
    request = {"project_id": "project-a", "kind": kind, "item_id": item}
    before = engine.get_status_snapshot("project-a")
    assert client.get("/api/assets").json() == []
    first = client.post("/api/assets/import-script", json=request)
    assert first.status_code == 200
    asset = first.json()
    assert client.post("/api/assets/import-script", json=request).json() == asset
    assert engine.get_status_snapshot("project-a") == before
    old = asset["versions"][0]
    assert old["source"] == {"type": "script", "project_id": "project-a", "item_id": item, "stage": "script_generation"}
    key = "characters" if kind == "character" else "settings"
    assert json.loads(old["definition"]) == before["artifacts"]["script_generation"][key][0]
    ref = bind(client, asset).json()
    engine.get_state("project-a").artifacts["script_generation"][key][0]["description"] = "人工更新文字"
    engine.save_session_to_disk("project-a")
    changed = client.post("/api/assets/import-script", json=request).json()
    assert changed["asset_id"] == asset["asset_id"] and len(changed["versions"]) == 2
    assert changed["versions"][0] == old
    assert library.read().references[ref["reference_id"]].version_id == old["version_id"]


@pytest.mark.parametrize("target", [
    {"project": "missing"}, {"segment_id": "missing"},
    {"segment_id": "seg-1", "shot_number": 9},
])
def test_nonexistent_targets_do_not_create_projects_or_references(fixture, target):
    client, library, engine = fixture
    asset = create(client)
    assert bind(client, asset, **target).status_code == 404
    assert library.read().references == {}
    assert engine.get_state("missing") is None


def test_foreign_version_duplicate_binding_and_missing_asset_rejected(fixture):
    client, library, engine = fixture
    asset, other = create(client), create(client, "prop")
    assert bind(client, asset).status_code == 201
    assert bind(client, asset).status_code == 409
    payload = {"project_id": "project-b", "asset_id": asset["asset_id"], "version_id": other["versions"][0]["version_id"]}
    assert client.post("/api/asset-references", json=payload).status_code == 404
    assert client.get("/api/assets/" + "a" * 32).status_code == 404
    assert len(library.read().references) == 1


def test_removed_target_keeps_history_and_cannot_be_upgraded(fixture):
    client, library, engine = fixture
    asset = create(client)
    ref = bind(client, asset, segment_id="seg-1", shot_number=1).json()
    engine.get_state("project-a").artifacts["storyboard"] = {"episodes": []}
    engine.save_session_to_disk("project-a")
    listed = client.get("/api/project/project-a/asset-references").json()
    assert listed[0]["target_exists"] is False
    assert client.patch(f'/api/asset-references/{ref["reference_id"]}', json={
        "version_id": ref["version_id"], "expected_revision": 1,
    }).status_code == 404
    assert library.read().references[ref["reference_id"]].history[0].version_id == ref["version_id"]


@pytest.mark.parametrize("change", [
    {"definition": " "}, {"name": " "}, {"definition": "x" * 10001},
    {"kind": "video"}, {"name": 123}, {"source": {"type": "private-artificial-text"}},
])
def test_request_structure_and_lengths_are_safe(fixture, change):
    client, library, _ = fixture
    payload = {"kind": "character", "name": "角色", "definition": "人工定义", **change}
    response = client.post("/api/assets", json=payload)
    assert response.status_code == 422
    assert "private-artificial-text" not in response.text
    assert library.read().assets == {}


def test_shot_requires_segment_and_bool_is_not_integer(fixture):
    client, _, _ = fixture
    asset = create(client)
    assert bind(client, asset, shot_number=1).status_code == 422
    assert bind(client, asset, segment_id="seg-1", shot_number=True).status_code == 422


def test_legacy_segment_fallback_and_ambiguous_target(fixture):
    client, _, engine = fixture
    segments = engine.get_state("project-a").artifacts["storyboard"]["episodes"][0]["segments"]
    segments[0].pop("segment_id")
    asset = create(client)
    assert bind(client, asset, segment_id="seg_01_01", shot_number=1).status_code == 201
    segments.append({"segment_id": "seg_01_01", "shots": []})
    assert bind(client, create(client), segment_id="seg_01_01").status_code == 404


@pytest.mark.parametrize("replacement", [
    "{broken", "{}", '{"schema_version":99,"assets":{},"references":{}}',
    '{"schema_version":true,"assets":{},"references":{}}',
    '{"schema_version":1,"schema_version":1,"assets":{},"references":{}}',
    '{"schema_version":1,"assets":[],"references":{}}',
])
def test_corrupt_or_future_document_not_overwritten(fixture, replacement):
    client, library, _ = fixture
    library.read()
    library.path.write_text(replacement)
    response = client.post("/api/assets", json={"kind": "prop", "name": "道具", "definition": "人工"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ASSET_STORE_UNAVAILABLE"
    assert library.path.read_text() == replacement


def test_graph_corruption_is_detected_before_changes(fixture):
    client, library, _ = fixture
    asset = create(client)
    bind(client, asset)
    document = json.loads(library.path.read_text())
    ref = next(iter(document["references"].values()))
    ref["version_id"] = "f" * 32
    library.path.write_text(json.dumps(document))
    before = library.path.read_bytes()
    assert client.get("/api/assets").status_code == 503
    assert library.path.read_bytes() == before


@pytest.mark.parametrize("filename", ["library.json", ".lock"])
def test_symlink_is_rejected_without_touching_destination(fixture, tmp_path, filename):
    client, library, _ = fixture
    library.directory.mkdir()
    destination = tmp_path / "private-artificial-file"
    destination.write_text("private-artificial-text")
    (library.directory / filename).symlink_to(destination)
    response = client.get("/api/assets")
    assert response.status_code == 503
    assert "private-artificial" not in response.text
    assert destination.read_text() == "private-artificial-text"


def test_failed_write_leaves_disk_and_references_unchanged(fixture, monkeypatch):
    client, library, _ = fixture
    asset = create(client)
    ref = bind(client, asset).json()
    next_version = client.post(f'/api/assets/{asset["asset_id"]}/versions', json={"name": "新角色", "definition": "新定义"}).json()["versions"][-1]["version_id"]
    before = library.path.read_bytes()

    def fail(*args):
        raise OSError("private-artificial-path")

    monkeypatch.setattr(asset_library, "atomic_write_session", fail)
    response = client.patch(f'/api/asset-references/{ref["reference_id"]}', json={"version_id": next_version, "expected_revision": 1})
    assert response.status_code == 503 and "private-artificial-path" not in response.text
    assert library.path.read_bytes() == before
    assert library.read().references[ref["reference_id"]].revision == 1


def test_concurrent_stores_cannot_lose_versions(tmp_path):
    directory = tmp_path / "library"
    asset = AssetLibrary(directory).create(AssetInput(kind="prop", name="信件", definition="初版"))

    def append(index):
        return AssetLibrary(directory).add_version(asset.asset_id, VersionInput(name="信件", definition=f"人工修订 {index}"))

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(append, range(12)))
    versions = AssetLibrary(directory).read().assets[asset.asset_id].versions
    assert [v.number for v in versions] == list(range(1, 14))
    assert len({v.definition for v in versions}) == 13


def test_independent_processes_share_same_lock_and_data(tmp_path):
    store = AssetLibrary(tmp_path)
    asset = store.create(AssetInput(kind="prop", name="信件", definition="初版"))
    script = """
import sys
from core.asset_library import AssetLibrary
from api.schemas.assets import VersionInput
for index in range(4):
    AssetLibrary(sys.argv[1]).add_version(sys.argv[2], VersionInput(name='信件', definition=sys.argv[3]+str(index)))
"""
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), asset.asset_id, str(index)],
                                 cwd=Path(__file__).resolve().parents[1]) for index in range(3)]
    for process in processes:
        assert process.wait(timeout=10) == 0
    assert len(store.read().assets[asset.asset_id].versions) == 13


def test_concurrent_reference_changes_require_a_fresh_confirmation(tmp_path):
    library = AssetLibrary(tmp_path)
    asset = library.create(AssetInput(kind="prop", name="信件", definition="旧版"))
    ref = library.create_reference(ReferenceInput(project_id="artificial", asset_id=asset.asset_id,
                                                  version_id=asset.versions[0].version_id))
    asset = library.add_version(asset.asset_id, VersionInput(name="信件", definition="新版"))

    def upgrade(_):
        try:
            AssetLibrary(tmp_path).update_reference(ref.reference_id, ReferenceUpdate(
                version_id=asset.versions[-1].version_id, expected_revision=1))
            return 200
        except AssetLibraryError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(upgrade, range(2))) == [200, 409]
    assert library.read().references[ref.reference_id].revision == 2


def test_default_private_store_is_not_in_static_code_tree():
    from config import settings

    directory = get_asset_library().directory.resolve()
    assert not directory.is_relative_to(Path(settings.CODE_DIR).resolve())
