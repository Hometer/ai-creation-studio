"""Backup current snapshots against durable project text; never write authorization."""
from typing import Literal
from pydantic import Field
from api.schemas.assets import Contract, ProjectId
from api.schemas.content_revisions import Stage, ComparisonChange, Impact

class RestoreStagePreview(Contract):
    before_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    after_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    before_field_count: int = Field(ge=0, le=500)
    after_field_count: int = Field(ge=0, le=500)
    changes: list[ComparisonChange] = Field(max_length=2000)
    impacts: list[Impact]

class RestoreBackupPreview(Contract):
    project_id: ProjectId
    token: str = Field(pattern=r'^[a-f0-9]{64}$')
    mode: Literal['read_only'] = 'read_only'
    source_project_id: ProjectId
    file_bytes: int = Field(ge=1, le=64*1024*1024)
    file_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    scope: Literal['backup_current'] = 'backup_current'
    total_changes: int = Field(ge=0, le=2000)
    stages: dict[Stage, RestoreStagePreview]
