"""A real local HTTP server restarts against isolated, artificial project data."""

import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx


@contextmanager
def server(data_dir):
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    script = """
import sys, uvicorn
from api.app import app
from api.routers import workflow, sessions
from core.orchestrator import WorkflowEngine
engine = WorkflowEngine(sys.argv[1])
workflow.workflow_engine = engine
sessions.workflow_engine = engine
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), access_log=False, log_level='critical')
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(data_dir), str(port)],
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
