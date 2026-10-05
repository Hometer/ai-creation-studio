"""Compatible, validated session files for the single-process workflow engine."""

import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SESSION_SCHEMA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SESSION_STATUSES = {
    "pending", "running", "waiting", "completed", "stopped", "error",
    "waiting_intervention", "session_completed",
}


class SessionDocument(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    schema_version: int = 0
    session_id: str
    current_stage: str = "init"
    status: dict[str, str] | str = "pending"
    artifacts: dict[str, Any] = Field(default_factory=dict)
    stage_progress: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    updated_at: float | int | str = 0


def session_path(directory: str | Path, session_id: str) -> Path:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Invalid session identifier")
    path = Path(directory) / f"{session_id}.json"
    if path.is_symlink():
        raise ValueError("Session files cannot be symbolic links")
    return path


def restore_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("Invalid session timestamp")
        return datetime.fromtimestamp(value)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("Invalid session timestamp")


def read_session(path: Path, session_id: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    document = SessionDocument.model_validate(data)
    if document.schema_version not in {0, SESSION_SCHEMA_VERSION}:
        raise ValueError("Unsupported session schema")
    if document.session_id != session_id:
        raise ValueError("Session identity mismatch")
    statuses = document.status.values() if isinstance(document.status, dict) else [document.status]
    if any(status not in SESSION_STATUSES for status in statuses):
        raise ValueError("Invalid session status")
    restore_timestamp(document.updated_at)
    if 'content_history' in data:
        from core.content_revisions import validate_history
        validate_history(data['content_history'])
    if 'content_reviews' in data:
        from core.impact_reviews import validate_reviews
        validate_reviews(data['content_reviews'])
    if 'co_creation' in data:
        from core.co_creation import validate_ledger
        validate_ledger(data['co_creation'])
    return data


def atomic_write_session(path: Path, data: dict[str, Any]) -> None:
    """A failed serialization or replacement leaves the previous file untouched."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".session-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
