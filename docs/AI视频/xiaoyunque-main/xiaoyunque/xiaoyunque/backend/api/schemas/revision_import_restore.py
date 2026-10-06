"""Compatible local backup restoration receipts and explicit confirmation."""
from typing import Literal
from pydantic import Field
from api.schemas.assets import Contract, Id
from api.schemas.content_revisions import Stage, Workspace
from api.schemas.revision_import_preview import RestoreBackupPreview

class PreparedBackup(Contract):
    preview: RestoreBackupPreview
    confirmation_token: str = Field(pattern=r'^[a-f0-9]{64}$')

class RestoreReceipt(Contract):
    restore_id: Id
    number: int = Field(ge=1, le=100)
    request_id: Id
    request_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    created_at: str
    file_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    file_bytes: int = Field(ge=1, le=64*1024*1024)
    expected_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmation_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    before_digests: dict[Stage, str]
    after_digests: dict[Stage, str]
    version_ids: dict[Stage, Id]

class RestoreLedger(Contract):
    schema_version: Literal[1] = 1
    records: list[RestoreReceipt] = Field(default_factory=list, max_length=100)

class RestoreWorkspace(Contract):
    workspace: Workspace
    restores: list[dict]

class RestoredBackup(Contract):
    receipt: dict
    data: RestoreWorkspace
