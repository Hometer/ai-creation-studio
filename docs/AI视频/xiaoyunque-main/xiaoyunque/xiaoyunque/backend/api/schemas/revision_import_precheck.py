"""Read-only inspection summaries, never a restoration authorization."""
from typing import Literal
from pydantic import Field
from api.schemas.assets import Contract, ProjectId, Id
from api.schemas.content_revisions import Stage

class StageSummary(Contract):
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    field_count: int = Field(ge=0, le=500)
    version_count: int = Field(ge=0)
    matches_current: bool
    untracked: bool
    version_id: Id | None

class VersionSummary(Contract):
    version_id: Id
    number: int = Field(ge=1)
    stage: Stage
    source: Literal['baseline','external','edit','restore']
    created_at: str
    restored_from: Id | None
    field_count: int = Field(ge=0, le=500)

class PrecheckReport(Contract):
    project_id: ProjectId
    token: str = Field(pattern=r'^[a-f0-9]{64}$')
    mode: Literal['read_only'] = 'read_only'
    source_project_id: ProjectId
    same_project: bool
    file_bytes: int = Field(ge=1, le=64*1024*1024)
    file_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    schema_version: Literal[1] = 1
    stages: dict[Stage, StageSummary]
    versions: list[VersionSummary]
