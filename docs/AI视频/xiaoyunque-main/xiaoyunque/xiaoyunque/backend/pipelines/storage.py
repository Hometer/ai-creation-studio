import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import settings
from core.asset_library import AssetLibraryError
from core.persistence import atomic_write_session
from .events import publish_task_event

logger = logging.getLogger(__name__)
_task_store_lock = threading.RLock()
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

TASK_DATA_DIR = os.path.join(settings.CODE_DIR, "data", "tasks")
TASK_RESULT_DIR = os.path.join(settings.RESULT_DIR, "task")


def ensure_task_dirs() -> None:
    if Path(TASK_DATA_DIR).is_symlink() or Path(TASK_RESULT_DIR).is_symlink():
        raise AssetLibraryError(503, "TASK_STORE_UNAVAILABLE", "任务存储暂不可用，原记录已保留")
    os.makedirs(TASK_DATA_DIR, exist_ok=True)
    os.makedirs(TASK_RESULT_DIR, exist_ok=True)


def new_task_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def task_metadata_path(task_id: str) -> str:
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise AssetLibraryError(422, "TASK_ID_INVALID", "任务编号不符合要求")
    path = Path(TASK_DATA_DIR) / f"{task_id}.json"
    if Path(TASK_DATA_DIR).is_symlink() or path.is_symlink():
        raise AssetLibraryError(503, "TASK_STORE_UNAVAILABLE", "任务存储暂不可用，原记录已保留")
    return str(path)


def task_output_dir(task_id: str) -> str:
    task_metadata_path(task_id)
    path = Path(TASK_RESULT_DIR) / task_id
    if Path(TASK_RESULT_DIR).is_symlink() or path.is_symlink():
        raise AssetLibraryError(503, "TASK_STORE_UNAVAILABLE", "任务结果目录暂不可用")
    return str(path)


def now_iso() -> str:
    return datetime.now().isoformat()


def save_task(metadata: Dict[str, Any]) -> None:
    with _task_store_lock:
        ensure_task_dirs()
        path = task_metadata_path(metadata["task_id"])
        atomic_write_session(Path(path), metadata)


def load_task(task_id: str) -> Optional[Dict[str, Any]]:
    with _task_store_lock:
        path = task_metadata_path(task_id)
        if not os.path.exists(path):
            return None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "r", encoding="utf-8") as f:
                data = json.load(f)
            if (not isinstance(data, dict) or data.get("task_id") != task_id
                    or not isinstance(data.get("created_at"), str)
                    or not isinstance(data.get("status"), str) or not isinstance(data.get("pipeline"), str)):
                raise ValueError("Invalid task record")
            return data
        except (OSError, ValueError, TypeError) as exc:
            raise AssetLibraryError(503, "TASK_STORE_UNAVAILABLE", "任务记录暂不可读，原文件已保留") from exc


def delete_task(task_id: str) -> bool:
    with _task_store_lock:
        metadata = load_task(task_id)
        if not metadata:
            return False

        if metadata["status"] in {"pending", "queued", "running"}:
            raise AssetLibraryError(409, "TASK_ACTIVE", "请等待任务结束后再删除")

        metadata_path = task_metadata_path(task_id)
        output_dir = task_output_dir(task_id)
        recorded = metadata.get("output_dir")
        if recorded and Path(recorded).resolve() != Path(output_dir).resolve():
            raise AssetLibraryError(409, "TASK_OUTPUT_MISMATCH", "任务结果目录不匹配，原记录已保留")
        if os.path.exists(metadata_path):
            os.remove(metadata_path)
        if output_dir and os.path.exists(output_dir):
            shutil.rmtree(output_dir)
    logger.info("Deleted pipeline task: task_id=%s output_dir=%s", task_id, output_dir)
    return True


def list_tasks(limit: int = 100) -> list[Dict[str, Any]]:
    with _task_store_lock:
        ensure_task_dirs()
        records = []
        for filename in os.listdir(TASK_DATA_DIR):
            if not filename.endswith(".json"):
                continue
            records.append(load_task(filename[:-5]))
        records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return records[:limit]


def create_task(pipeline: str, input_params: Dict[str, Any]) -> Dict[str, Any]:
    with _task_store_lock:
        ensure_task_dirs()
        task_id = new_task_id()
        output_dir = task_output_dir(task_id)
        os.makedirs(output_dir, exist_ok=True)
        metadata = {
            "task_id": task_id,
            "pipeline": pipeline,
            "status": "pending",
            "progress": 0,
            "message": "Task created",
            "input": input_params,
            "output": {},
            "artifacts": [],
            "error": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "output_dir": output_dir,
        }
        save_task(metadata)
    logger.info("Created pipeline task: task_id=%s pipeline=%s output_dir=%s", task_id, pipeline, output_dir)
    return metadata


def update_task(task_id: str, **updates: Any) -> Dict[str, Any]:
    with _task_store_lock:
        metadata = load_task(task_id)
        if not metadata:
            raise FileNotFoundError(f"Task not found: {task_id}")
        metadata.update(updates)
        metadata["updated_at"] = now_iso()
        save_task(metadata)
    if "progress" in updates or "status" in updates:
        logger.info(
            "Task update: task_id=%s status=%s progress=%s message=%s",
            task_id,
            metadata.get("status"),
            metadata.get("progress"),
            metadata.get("message"),
        )
    publish_task_event(task_id, {
        "type": "progress",
        "status": metadata.get("status"),
        "progress": metadata.get("progress", 0),
    })
    return metadata


def append_artifact(task_id: str, new_artifact: Dict[str, Any]) -> Dict[str, Any]:
    with _task_store_lock:
        metadata = load_task(task_id)
        if not metadata:
            raise FileNotFoundError(f"Task not found: {task_id}")

        if not new_artifact.get("created_at"):
            new_artifact = {**new_artifact, "created_at": now_iso()}

        artifacts = list(metadata.get("artifacts") or [])
        key = (new_artifact.get("kind"), new_artifact.get("name"), new_artifact.get("path"))
        if not any((item.get("kind"), item.get("name"), item.get("path")) == key for item in artifacts):
            artifacts.append(new_artifact)
            logger.info(
                "Task artifact: task_id=%s kind=%s name=%s path=%s",
                task_id,
                new_artifact.get("kind"),
                new_artifact.get("name"),
                new_artifact.get("path"),
            )
        metadata["artifacts"] = artifacts
        metadata["updated_at"] = now_iso()
        save_task(metadata)
    publish_task_event(task_id, {
        "type": "artifact",
        "status": metadata.get("status"),
        "progress": metadata.get("progress", 0),
        "artifact": new_artifact,
    })
    return metadata


def mark_running(task_id: str) -> Dict[str, Any]:
    return update_task(task_id, status="running", progress=1, message="Task running", started_at=now_iso())


def mark_completed(task_id: str, output: Dict[str, Any], artifacts: list[Dict[str, Any]]) -> Dict[str, Any]:
    metadata = load_task(task_id) or {"task_id": task_id}
    started_at = metadata.get("started_at")
    duration = None
    if started_at:
        try:
            duration = time.time() - datetime.fromisoformat(started_at).timestamp()
        except Exception:
            duration = None
    existing_artifacts = list(metadata.get("artifacts") or [])
    merged_artifacts = list(existing_artifacts)
    seen = {
        (item.get("kind"), item.get("name"), item.get("path"))
        for item in merged_artifacts
    }
    for item in artifacts or []:
        key = (item.get("kind"), item.get("name"), item.get("path"))
        if key in seen:
            continue
        merged_artifacts.append({**item, "created_at": item.get("created_at") or now_iso()})
        seen.add(key)

    metadata = update_task(
        task_id,
        status="completed",
        progress=100,
        message="Task completed",
        output=output,
        artifacts=merged_artifacts,
        error=None,
        completed_at=now_iso(),
        duration_seconds=duration,
    )
    publish_task_event(task_id, {
        "type": "completed",
        "status": "completed",
        "progress": 100,
    })
    return metadata


def mark_failed(task_id: str, error: str) -> Dict[str, Any]:
    metadata = update_task(
        task_id,
        status="failed",
        message="Task failed",
        error=error,
        completed_at=now_iso(),
    )
    publish_task_event(task_id, {
        "type": "failed",
        "status": "failed",
        "progress": metadata.get("progress", 0),
    })
    return metadata
