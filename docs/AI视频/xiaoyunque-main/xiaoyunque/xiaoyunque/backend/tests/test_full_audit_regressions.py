"""Artificial, isolated regressions for the comprehensive audit; no model calls."""

import asyncio
import io
import json
import logging
import queue
import subprocess
from pathlib import Path

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from api.errors import register_error_handlers
from api.routers import files, sandbox, workflow
from api.services.project_helpers import stream_workflow_task
from api.services.static_results import ResultStaticFiles
from config import settings
from core.asset_library import AssetLibraryError
from core.orchestrator import WorkflowEngine, WorkflowStage
from pipelines import events, storage


def client_for(router):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", str(tmp_path / "uploads"))
    return client_for(files.router)


@pytest.mark.parametrize("name,content", [("fake.png", b"not image"), ("fake.mp4", b"not video"),
    ("fake.pdf", b"not PDF"), ("fake.docx", b"not zip"), ("fake.txt", b"bad\x00binary"),
    ("fake.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1not Word")])
def test_spoofed_uploads_are_rejected_and_removed(uploads, name, content):
    route = "/api/upload_media" if name.endswith(("png", "mp4")) else "/api/upload_file"
    response = uploads.post(route, files={"file": (name, content, "application/octet-stream")})
    assert response.status_code == 415
    assert response.json()["error"]
    assert list(Path(settings.TEMP_DIR).iterdir()) == []


@pytest.mark.parametrize("ext,mime", [("png", "image/png"), ("jpg", "image/jpeg"), ("webp", "image/webp"), ("bmp", "image/bmp")])
def test_valid_legacy_images_preserve_upload_contract(uploads, ext, mime):
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(image, format={"jpg": "JPEG"}.get(ext, ext.upper()))
    response = uploads.post("/api/upload_media", files={"file": ("artificial." + ext, image.getvalue(), mime)})
    assert response.status_code == 200
    assert Path(response.json()["file_path"]).read_bytes() == image.getvalue()


def test_valid_documents_and_mime_mismatch(uploads):
    word = io.BytesIO(); document = Document(); document.add_paragraph("artificial document"); document.save(word)
    pdf = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.write(pdf)
    for name, content, mime in [("sample.txt", "人工文本".encode(), "text/plain"),
            ("sample.md", b"# artificial", "text/markdown"), ("sample.pdf", pdf.getvalue(), "application/pdf"),
            ("sample.docx", word.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")]:
        response = uploads.post("/api/upload_file", files={"file": (name, content, mime)})
        assert response.status_code == 200
        assert Path(settings.TEMP_DIR, response.json()["file_path"]).read_bytes() == content
    response = uploads.post("/api/upload_file", files={"file": ("wrong.pdf", pdf.getvalue(), "text/plain")})
    assert response.status_code == 415


def test_valid_legacy_mp4_uses_real_probe(uploads, tmp_path):
    video = tmp_path / "artificial.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=32x32:d=1", "-an", str(video)], check=True)
    response = uploads.post("/api/upload_media", files={"file": (video.name, video.read_bytes(), "video/mp4")})
    assert response.status_code == 200


def test_upload_size_and_control_name_fail_without_leftovers(uploads, monkeypatch):
    monkeypatch.setattr(files, "DOCUMENT_UPLOAD_LIMIT", 3)
    assert uploads.post("/api/upload_file", files={"file": ("large.txt", b"1234", "text/plain")}).status_code == 413
    assert uploads.post("/api/upload_file", files={"file": ("bad%00name.txt", b"ok", "text/plain")}).status_code == 422
    assert list(Path(settings.TEMP_DIR).iterdir()) == []


def test_static_private_records_blocked_but_result_urls_work(tmp_path):
    for path in ["data/sessions/id.json", "data/tasks/id.json", "result/sandbox/history.json", "source.py", "result/image/id/image.png"]:
        item = tmp_path / path; item.parent.mkdir(parents=True, exist_ok=True); item.write_bytes(b"artificial")
    app = FastAPI(); register_error_handlers(app); app.mount("/code", ResultStaticFiles(directory=tmp_path))
    client = TestClient(app)
    for path in ["data/sessions/id.json", "data/tasks/id.json", "result/sandbox/history.json",
                 "result/sandbox/HISTORY.JSON", "result/SANDBOX/history.json", "result/sandbox/history.json/",
                 "source.py", "result/../data/tasks/id.json"]:
        assert client.get("/code/" + path).status_code == 404
    assert client.get("/code/result/image/id/image.png").content == b"artificial"
    (tmp_path / "result/escaped.png").symlink_to(tmp_path / "data/sessions/id.json")
    assert client.get("/code/result/escaped.png").status_code == 404


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "TASK_DATA_DIR", str(tmp_path / "tasks"))
    monkeypatch.setattr(storage, "TASK_RESULT_DIR", str(tmp_path / "results"))
    return storage.create_task("standard", {})


@pytest.mark.parametrize("task_id", ["../outside", "..", "/outside", "a/b", "a\\b", "", "a" * 129])
def test_task_path_escape_is_rejected(tasks, task_id):
    with pytest.raises(AssetLibraryError): storage.load_task(task_id)
    with pytest.raises(AssetLibraryError): storage.delete_task(task_id)


def test_task_symlink_and_corrupt_history_are_visible_and_preserved(tasks, tmp_path):
    path = Path(storage.task_metadata_path(tasks["task_id"])); original = path.read_bytes()
    path.write_text("{broken")
    with pytest.raises(AssetLibraryError): storage.list_tasks()
    assert path.read_text() == "{broken"
    path.unlink(); outside = tmp_path / "private.json"; outside.write_bytes(original); path.symlink_to(outside)
    with pytest.raises(AssetLibraryError): storage.load_task(tasks["task_id"])
    assert outside.read_bytes() == original


def test_deletion_rejects_active_and_external_outputs(tasks, tmp_path):
    task_id = tasks["task_id"]
    with pytest.raises(AssetLibraryError): storage.delete_task(task_id)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep.txt").write_text("keep")
    storage.update_task(task_id, status="completed", output_dir=str(outside))
    before = Path(storage.task_metadata_path(task_id)).read_bytes()
    with pytest.raises(AssetLibraryError): storage.delete_task(task_id)
    assert (outside / "keep.txt").read_text() == "keep"
    assert Path(storage.task_metadata_path(task_id)).read_bytes() == before
    storage.update_task(task_id, output_dir=storage.task_output_dir(task_id))
    assert storage.delete_task(task_id) is True


def test_bad_sandbox_history_never_overwritten_and_can_recover(tmp_path, monkeypatch):
    path = tmp_path / "history.json"; path.write_text("{broken")
    monkeypatch.setattr(sandbox, "SANDBOX_HISTORY_FILE", str(path))
    client = client_for(sandbox.router)
    assert client.get("/api/sandbox/history").status_code == 503
    with pytest.raises(AssetLibraryError): sandbox._add_record("llm", "artificial", {}, {"response": "artificial"})
    assert path.read_text() == "{broken"
    path.write_text("[]")
    sandbox._add_record("llm", "artificial", {}, {"response": "artificial"})
    assert client.get("/api/sandbox/history").status_code == 200


def test_bad_session_not_hidden_as_empty_and_recovers(tmp_path):
    path = tmp_path / "bad.json"; path.write_text("{broken")
    engine = WorkflowEngine(str(tmp_path))
    with pytest.raises(AssetLibraryError): engine.list_saved_sessions()
    path.write_text(json.dumps({"session_id": "bad", "status": {}, "updated_at": 0}))
    assert engine.list_saved_sessions()[0]["id"] == "bad"


def test_invalid_execution_and_model_patch_do_not_mutate_projects(tmp_path, monkeypatch):
    engine = WorkflowEngine(str(tmp_path)); engine.create_session("saved", {"llm_model": "artificial"})
    monkeypatch.setattr(workflow, "workflow_engine", engine)
    client = client_for(workflow.router); before = (tmp_path / "saved.json").read_bytes()
    assert client.post("/api/project/missing/execute/script_generation", json={}).status_code == 404
    assert client.post("/api/project/saved/execute/unknown", json={}).status_code == 422
    assert client.post("/api/project/saved/execute/script_generation", json=[]).status_code == 422
    assert client.post("/api/project/saved/intervene", json={"stage": "unknown", "modifications": {}}).status_code == 422
    for values in [{"llm_model": []}, {"enable_concurrency": "false"}, {"video_generation_mode": "unsupported"}]:
        assert client.patch("/api/project/saved/models", json=values).status_code == 422
    assert set(engine.sessions) == {"saved"}
    assert (tmp_path / "saved.json").read_bytes() == before


def test_workflow_stream_real_envelope_safe_failure_and_fast_end(tmp_path, caplog):
    class Request:
        async def is_disconnected(self): return False
    async def run(failure=False):
        engine = WorkflowEngine(str(tmp_path / str(failure))); engine.create_session("stream", {})
        class Agent:
            def set_cancellation_check(self, callback): pass
            def set_progress_callback(self, callback): pass
            async def process(self, data, intervention=None):
                if failure: raise RuntimeError("artificial-private-upstream-detail")
                return {"payload": {"title": "人工协议样例"}, "requires_intervention": True}
        engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = Agent
        values = [json.loads(item) async for item in stream_workflow_task(request=Request(), workflow_engine=engine,
            state=engine.get_state("stream"), stage="script_generation", input_data={}, cancellation_check=lambda: False,
            progress_callback=lambda *a: None, progress_events=queue.Queue(), event_trigger=asyncio.Event())]
        return values, engine.get_status_snapshot("stream")
    with caplog.at_level(logging.ERROR):
        success, state = asyncio.run(asyncio.wait_for(run(), timeout=1))
        failure, failed_state = asyncio.run(asyncio.wait_for(run(True), timeout=1))
    assert success[-1]["type"] == "done"
    assert isinstance(success[-2]["status"], dict)
    assert failure[-1]["type"] == "error" and failure[-1]["error"]["code"] == "WORKFLOW_FAILED"
    assert failed_state["status"]["script_generation"] == "error"
    assert "artificial-private-upstream-detail" not in json.dumps(failure) + json.dumps(failed_state) + caplog.text


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_sse_already_terminal_finishes_without_heartbeat(status):
    async def run():
        return [item async for item in events.task_event_stream("artificial", {"type": "snapshot", "status": status})]
    chunks = asyncio.run(asyncio.wait_for(run(), timeout=1))
    assert f"event: {'done' if status == 'completed' else 'error'}" in chunks[-1]
    assert not events._subscribers


def test_sse_thread_publish_and_disconnect_cleanup():
    async def run():
        stream = events.task_event_stream("thread-proof", {"type": "snapshot", "status": "running"})
        await anext(stream)
        await asyncio.to_thread(events.publish_task_event, "thread-proof", {"type": "completed", "status": "completed"})
        assert '"completed"' in await asyncio.wait_for(anext(stream), timeout=1)
        assert "event: done" in await anext(stream)
        await stream.aclose()
        assert not events._subscribers
    asyncio.run(run())


def test_sdk_initialization_failure_is_safe_and_persisted(tmp_path, caplog):
    engine = WorkflowEngine(str(tmp_path)); engine.create_session("init-error", {})
    def broken_factory(): raise RuntimeError("artificial-private-init-detail")
    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = broken_factory
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        asyncio.run(engine.execute_stage(engine.get_state("init-error"), WorkflowStage.SCRIPT_GENERATION, {}))
    snapshot = engine.get_status_snapshot("init-error")
    assert snapshot["status"]["script_generation"] == "error"
    assert WorkflowEngine(str(tmp_path)).get_status_snapshot("init-error") == snapshot
    assert "artificial-private-init-detail" not in json.dumps(snapshot) + caplog.text


def test_sandbox_sdk_failure_is_structured_and_does_not_echo_private_detail(monkeypatch, caplog):
    import models.llm_client
    class FakeLLM:
        def query(self, *args, **kwargs): raise RuntimeError("artificial-private-sdk-detail")
    monkeypatch.setattr(models.llm_client, "LLM", FakeLLM)
    with caplog.at_level(logging.ERROR):
        response = client_for(sandbox.router).post("/api/sandbox/llm", json={"model": "artificial", "prompt": "artificial"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "artificial-private-sdk-detail" not in response.text + caplog.text
    assert not sandbox.SANDBOX_ACTIVE_TASKS


def test_pipeline_failure_stores_safe_error_instead_of_traceback(tasks, monkeypatch, caplog):
    from pipelines import runner
    async def fail(*args): raise RuntimeError("artificial-private-pipeline-detail")
    monkeypatch.setitem(runner.PIPELINE_REGISTRY, "artificial", fail)
    with caplog.at_level(logging.ERROR): asyncio.run(runner.run_pipeline_task(tasks["task_id"], "artificial", {}))
    record = storage.load_task(tasks["task_id"])
    assert record["status"] == "failed"
    assert "artificial-private-pipeline-detail" not in json.dumps(record) + caplog.text
    assert "Traceback" not in record["error"]


def test_pdf_extraction_keeps_text_after_blank_page_and_enforces_timeout(tmp_path, monkeypatch):
    from models import pdf_reader
    from models.file_reader import FileReader
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    page = writer.add_blank_page(width=300, height=300)
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 20 20 Td (artificial text) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "artificial.pdf"; writer.write(path)
    assert FileReader.extract_text(str(path)) == "artificial text"
    def timed_out(*args, **kwargs): raise subprocess.TimeoutExpired("artificial-parser", 10)
    monkeypatch.setattr(pdf_reader.subprocess, "run", timed_out)
    with pytest.raises(pdf_reader.PDFReadError) as exc: pdf_reader.read_pdf(str(path))
    assert exc.value.unavailable


def test_real_binary_word_is_supported_on_this_mac(uploads, tmp_path):
    import shutil
    if shutil.which("textutil") is None:
        pytest.skip("Native legacy Word converter unavailable on this platform")
    source = tmp_path / "artificial.txt"; source.write_text("artificial native Word")
    word = tmp_path / "artificial.doc"
    subprocess.run(["textutil", "-convert", "doc", "-output", str(word), str(source)], check=True, capture_output=True)
    response = uploads.post("/api/upload_file", files={"file": (word.name, word.read_bytes(), "application/msword")})
    assert response.status_code == 200
    assert "artificial native Word" in files.merge_uploaded_file_into_idea("base", response.json()["file_path"])


def test_sandbox_media_reference_cannot_read_private_session_data(tmp_path, monkeypatch):
    root = tmp_path / "code"; private = root / "data/private.json"; private.parent.mkdir(parents=True); private.write_text("artificial")
    image = root / "result/image/artificial.png"; image.parent.mkdir(parents=True); image.write_text("artificial")
    monkeypatch.setattr(settings, "CODE_DIR", str(root)); monkeypatch.setattr(settings, "RESULT_DIR", str(root / "result"))
    monkeypatch.setattr(settings, "TEMP_DIR", str(tmp_path / "uploads"))
    from fastapi import HTTPException
    with pytest.raises(HTTPException): sandbox._validated_media_reference(str(private))
    assert sandbox._validated_media_reference("/code/result/image/artificial.png") == str(image)


def test_sandbox_deletion_does_not_trust_external_record_files(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"; root.mkdir()
    outside = tmp_path / "private.txt"; outside.write_text("keep")
    history = root / "history.json"
    history.write_text(json.dumps([{"id": "unsafe", "tool": "llm", "model": "artificial", "input": {}, "output": {}, "created_at": "2026-10-05T00:00:00", "files": [str(outside)]}]))
    before = history.read_bytes()
    monkeypatch.setattr(sandbox, "SANDBOX_DIR", str(root)); monkeypatch.setattr(sandbox, "SANDBOX_HISTORY_FILE", str(history))
    response = client_for(sandbox.router).delete("/api/sandbox/history/unsafe")
    assert response.status_code == 409
    assert outside.read_text() == "keep"
    assert history.read_bytes() == before
