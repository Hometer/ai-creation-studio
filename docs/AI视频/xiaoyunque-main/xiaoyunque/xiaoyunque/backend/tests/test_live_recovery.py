"""A real local HTTP server restarts against isolated, artificial project data."""

import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx


@contextmanager
def server(data_dir, task_simulation=False):
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    script = """
import sys, uvicorn
from api.app import app
from api.routers import workflow, sessions
from core.orchestrator import WorkflowEngine
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library, get_media_archive
from core.asset_library import AssetLibrary
from core.media_archive import MediaArchive
from pathlib import Path
from api.routers.generation_tasks import get_task_provider
from core.task_provider import LocalSimulationProvider
engine = WorkflowEngine(sys.argv[1])
workflow.workflow_engine = engine
sessions.workflow_engine = engine
app.dependency_overrides[get_workflow_engine] = lambda: engine
app.dependency_overrides[get_asset_library] = lambda: AssetLibrary(Path(sys.argv[1]) / 'asset-library')
app.dependency_overrides[get_media_archive] = lambda: MediaArchive(Path(sys.argv[1]) / 'asset-library', Path(sys.argv[1]) / 'code' / 'result', sys.argv[1])
if sys.argv[3] == 'simulation':
    provider = LocalSimulationProvider(Path(sys.argv[1]) / 'simulation-provider', Path(sys.argv[1]) / 'code/result/video/media-demo/sample.mp4')
    app.dependency_overrides[get_task_provider] = lambda: provider
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), access_log=False, log_level='critical')
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(data_dir), str(port), "simulation" if task_simulation else "unavailable"],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False) as client:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("Isolated backend exited before becoming ready")
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Isolated backend did not become ready")
            yield client
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_http_project_survives_actual_service_restart(tmp_path):
    with server(tmp_path) as client:
        response = client.post("/api/project/start", json={
            "idea": "隔离进程测试，不生成媒体", "llm_model": "test-llm", "vlm_model": "test-vlm",
            "image_t2i_model": "test-image", "image_it2i_model": "test-image", "video_model": "test-video",
            "expand_idea": False, "episodes": 1,
        })
        assert response.status_code == 200
        sid = response.json()["session_id"]
        response = client.patch(f"/api/project/{sid}/models", json={"style": "anime", "video_ratio": "16:9"})
        assert response.status_code == 200
        snapshot = client.get(f"/api/project/{sid}/status").json()
        assert isinstance(snapshot["updated_at"], str)
        assert snapshot["meta"]["style"] == "anime"
        assert snapshot["status"]["script_generation"] == "pending"
        assert snapshot["artifacts"] == {}
    with server(tmp_path) as client:
        assert client.get(f"/api/project/{sid}/status").json() == snapshot
        assert client.get(f"/api/project/{sid}/status/from_disk").json() == snapshot
        missing = client.get("/api/project/unknown/status")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "NOT_FOUND"
        invalid = client.post("/api/project/start", json={"idea": {"private": "artificial-text"}})
        assert invalid.status_code == 422
        assert "artificial-text" not in invalid.text


def test_http_assets_versions_and_references_survive_actual_restart(tmp_path):
    from core.orchestrator import WorkflowEngine

    engine = WorkflowEngine(str(tmp_path))
    engine.create_session("asset-proof", {"idea": "人工样例，不调用模型"})
    state = engine.get_state("asset-proof")
    state.artifacts = {
        "script_generation": {"characters": [{"id": "role-1", "name": "小云雀", "description": "黄色信使"}]},
        "storyboard": {"episodes": [{"segments": [{"segment_id": "seg-1", "shots": [{"shot_number": 1}]}]}]},
    }
    engine.save_session_to_disk("asset-proof")
    project_before = engine.get_status_snapshot("asset-proof")
    with server(tmp_path) as client:
        asset = client.post("/api/assets/import-script", json={
            "project_id": "asset-proof", "kind": "character", "item_id": "role-1",
        }).json()
        old = asset["versions"][0]["version_id"]
        response = client.post("/api/asset-references", json={
            "asset_id": asset["asset_id"], "version_id": old, "project_id": "asset-proof",
            "segment_id": "seg-1", "shot_number": 1,
        })
        assert response.status_code == 201
        ref = response.json()
        asset = client.post(f'/api/assets/{asset["asset_id"]}/versions', json={
            "name": "小云雀", "definition": "蓝色信使，人工第二版",
        }).json()
        route = f'/api/asset-references/{ref["reference_id"]}'
        ref = client.patch(route, json={"version_id": asset["versions"][-1]["version_id"], "expected_revision": 1}).json()
        assert ref["revision"] == 2 and len(ref["history"]) == 2
        assert client.get("/api/project/asset-proof/status").json() == project_before
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/assets"]["post"]["responses"]["503"]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")
        assert client.get("/code/data/asset-library/library.json").status_code == 404
        assert client.get("/data/asset-library/library.json").status_code == 404
    with server(tmp_path) as client:
        assert client.get(f'/api/assets/{asset["asset_id"]}').json() == asset
        assert client.get("/api/project/asset-proof/asset-references").json() == [ref]
        assert client.get("/api/project/asset-proof/status").json() == project_before
        assert client.patch(route, json={"version_id": old, "expected_revision": 1}).status_code == 409
        restored = client.patch(route, json={"version_id": old, "expected_revision": 2})
        assert restored.json()["revision"] == 3 and restored.json()["has_newer_version"]
