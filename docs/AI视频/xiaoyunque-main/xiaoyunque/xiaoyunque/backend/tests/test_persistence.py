import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from core.orchestrator import WorkflowEngine, WorkflowStage
from core.persistence import atomic_write_session, restore_timestamp, session_path


def test_restart_preserves_snapshot_and_confirmation(tmp_path):
    engine = WorkflowEngine(str(tmp_path))
    engine.create_session("project-1", {"idea": "人工测试故事", "expand_idea": False})
    state = engine.get_state("project-1")
    state.current_stage = WorkflowStage.CHARACTER_DESIGN
    state.status["script_generation"] = "completed"
    state.status["character_design"] = "waiting"
    state.artifacts = {"character_design": {"characters": [{"id": "role-1", "selected_image": "v1.png"}]}}
    state.stage_progress = {"character_design": {"message": "等待人工确认"}}
    engine.save_session_to_disk("project-1")
    snapshot = engine.get_status_snapshot("project-1")
    restarted = WorkflowEngine(str(tmp_path))
    assert restarted.get_status_snapshot("project-1") == snapshot
    assert restarted.list_saved_sessions() == engine.list_saved_sessions()
    assert restarted._active_sessions == set()
    on_disk = json.loads((tmp_path / "project-1.json").read_text())
    assert on_disk["schema_version"] == 1
    assert isinstance(on_disk["updated_at"], float)


def test_legacy_startup_and_lazy_load_are_identical(tmp_path):
    legacy = {
        "session_id": "legacy", "current_stage": "character_design",
        "status": "waiting_intervention", "stages_completed": ["script_generation"],
        "idea": "兼容故事", "expand_idea": "false", "updated_at": 1700000000,
        "artifacts": {"character_design": {"selected": "image-1"}},
        "error": "人工测试错误状态",
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy))
    engine = WorkflowEngine(str(tmp_path))
    startup = engine.get_status_snapshot("legacy")
    engine.sessions.clear()
    assert engine.get_status_snapshot("legacy") == startup
    assert startup["status"]["character_design"] == "waiting"
    assert startup["status"]["script_generation"] == "completed"
    assert startup["meta"]["expand_idea"] is False
    engine.save_session_to_disk("legacy")
    assert json.loads(path.read_text())["schema_version"] == 1


@pytest.mark.parametrize("replacement", [
    "{broken", json.dumps({"schema_version": 99, "session_id": "safe"}),
    json.dumps({"session_id": "another"}),
    json.dumps({"session_id": "safe", "artifacts": []}),
    json.dumps({"session_id": "safe", "status": {"storyboard": "invalid"}}),
])
def test_damaged_or_future_data_is_never_overwritten(tmp_path, replacement):
    engine = WorkflowEngine(str(tmp_path))
    engine.create_session("safe", {"idea": "测试"})
    path = tmp_path / "safe.json"
    path.write_text(replacement)
    with pytest.raises(ValueError):
        engine.save_session_to_disk("safe")
    assert path.read_text() == replacement
    assert WorkflowEngine(str(tmp_path)).get_state("safe") is None


def test_atomic_replace_failure_preserves_previous_version(tmp_path):
    path = tmp_path / "save.json"
    path.write_text('{"previous": true}')
    with patch("core.persistence.os.replace", side_effect=OSError("test disk error")):
        with pytest.raises(OSError):
            atomic_write_session(path, {"next": True})
    assert json.loads(path.read_text()) == {"previous": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["save.json"]


def test_serialization_failure_preserves_previous_version(tmp_path):
    path = tmp_path / "save.json"
    path.write_text('{"previous": true}')
    with pytest.raises(TypeError):
        atomic_write_session(path, {"invalid": object()})
    assert json.loads(path.read_text()) == {"previous": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_parameter_save_rolls_back_memory_and_disk(tmp_path):
    engine = WorkflowEngine(str(tmp_path))
    engine.create_session("safe", {"style": "anime"})
    before = engine.get_status_snapshot("safe")
    with patch("core.persistence.os.replace", side_effect=OSError("test disk error")):
        with pytest.raises(OSError):
            engine.update_session_meta("safe", {"style": "realistic"}, ("style",))
    assert engine.get_status_snapshot("safe") == before
    assert WorkflowEngine(str(tmp_path)).get_status_snapshot("safe") == before


def test_failed_creation_does_not_leave_a_phantom_project(tmp_path):
    engine = WorkflowEngine(str(tmp_path))
    with patch("core.persistence.os.replace", side_effect=OSError("test disk error")):
        with pytest.raises(OSError):
            engine.create_session("new", {"idea": "测试"})
    assert engine.list_saved_sessions() == []
    assert engine._stop_events == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("session_id", ["../outside", "..", "/private", "a/b", "", "a\\b", "a" * 129])
def test_identifier_cannot_escape_data_directory(tmp_path, session_id):
    with pytest.raises(ValueError):
        session_path(tmp_path, session_id)


def test_session_file_symlink_is_rejected(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_text('{"private": "artificial-text"}')
    try:
        (tmp_path / "link.json").symlink_to(outside)
        with pytest.raises(ValueError):
            session_path(tmp_path, "link")
    finally:
        outside.unlink()


@pytest.mark.parametrize("value", [1700000000, 1700000000.125, "2024-01-02T03:04:05", "2024-01-02T03:04:05Z"])
def test_legacy_time_formats(value):
    assert isinstance(restore_timestamp(value), datetime)


@pytest.mark.parametrize("value", [True, None, "invalid", float("nan"), float("inf")])
def test_invalid_time_rejected(value):
    with pytest.raises(ValueError):
        restore_timestamp(value)


def test_recovery_in_a_new_python_process(tmp_path):
    engine = WorkflowEngine(str(tmp_path))
    engine.create_session("process-test", {"idea": "跨进程恢复", "style": "anime"})
    expected = engine.get_status_snapshot("process-test")
    script = """
import json, sys
from core.orchestrator import WorkflowEngine
engine = WorkflowEngine(sys.argv[1])
print(json.dumps(engine.get_status_snapshot('process-test'), ensure_ascii=False))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1]) == expected
