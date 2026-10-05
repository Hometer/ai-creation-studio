"""Allowlisted, portable text snapshots. Not a raw session or import contract."""
from typing import Literal
from pydantic import Field
from api.schemas.assets import Contract, Id
from api.schemas.content_revisions import Stage, Change, Impact

ExportFormat = Literal['json', 'text']


class ExportSnapshot(Contract):
    content: dict
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    version_id: Id | None


class ExportVersion(Contract):
    version_id: Id
    number: int = Field(ge=1)
    stage: Stage
    source: Literal['baseline', 'external', 'edit', 'restore']
    created_at: str
    content: dict
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    changes: list[Change]
    impacts: list[Impact]
    restored_from: Id | None


class ExportDocument(Contract):
    format: Literal['text-revisions'] = 'text-revisions'
    schema_version: Literal[1] = 1
    project_id: str
    token: str = Field(pattern=r'^[a-f0-9]{64}$')
    current: dict[Stage, ExportSnapshot]
    versions: list[ExportVersion]
    untracked_stages: list[Stage]
