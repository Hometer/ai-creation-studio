"""Local text revision contracts; no provider or workflow continuation."""
from typing import Literal
from pydantic import Field, model_validator, field_validator
from api.schemas.assets import Contract, Id

Stage = Literal['script_generation', 'storyboard']

class RevisionInput(Contract):
    expected_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    stage: Stage
    path: list[str | int] | None = Field(default=None, min_length=1, max_length=10)
    value: str | None = Field(default=None, max_length=10000)
    replacement: dict | None = None
    restore_version_id: Id | None = None

    @model_validator(mode='after')
    def one_operation(self):
        editing = self.path is not None or self.value is not None
        if sum([editing, self.restore_version_id is not None, self.replacement is not None]) != 1:
            raise ValueError('Exactly one operation required')
        if editing and (self.path is None or self.value is None):
            raise ValueError('Missing edit')
        return self

class SaveRevision(RevisionInput):
    preview_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_id: Id
    acknowledged: Literal[True]

    @field_validator('acknowledged', mode='before')
    @classmethod
    def explicit_boolean(cls, value):
        if value is not True:
            raise ValueError('Explicit confirmation required')
        return value

class TextField(Contract):
    path: list[str | int]
    label: str
    value: str

class Change(Contract):
    path: list[str | int]
    before: str
    after: str
    before_present: bool = True
    after_present: bool = True

class Impact(Contract):
    segment_id: str
    shot_number: int | None
    reason: str

class Revision(Contract):
    version_id: Id
    number: int = Field(ge=1)
    stage: Stage
    source: Literal['baseline', 'external', 'edit', 'restore']
    created_at: str
    content: dict
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    changes: list[Change]
    impacts: list[Impact]
    restored_from: Id | None = None
    request_id: Id | None = None
    request_digest: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

class History(Contract):
    schema_version: Literal[1] = 1
    versions: list[Revision] = Field(default_factory=list)

class VersionView(Contract):
    version_id: Id
    number: int
    stage: Stage
    source: Literal['baseline', 'external', 'edit', 'restore']
    created_at: str
    current: bool
    fields: list[TextField]
    changes: list[Change]
    impacts: list[Impact]
    restored_from: Id | None

class Workspace(Contract):
    project_id: str
    token: str
    running: bool
    fields: dict[Stage, list[TextField]]
    versions: list[VersionView]
    untracked_stages: list[Stage]
    impacts: list[Impact]

class Preview(Contract):
    stage: Stage
    expected_token: str
    preview_token: str
    changes: list[Change]
    impacts: list[Impact]
    restore_version_id: Id | None

class Editor(Contract):
    workspace: Workspace
    artifact: dict

class Saved(Contract):
    version_id: Id
    workspace: Workspace
    artifact: dict

ValueType = Literal['text', 'number', 'boolean', 'object', 'array', 'null', 'missing']

class ComparisonInput(Contract):
    expected_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    before_version_id: Id
    after_version_id: Id

class ComparisonChange(Change):
    label: str
    before_type: ValueType
    after_type: ValueType

class Comparison(Contract):
    project_id: str
    token: str
    stage: Stage
    before_version_id: Id
    after_version_id: Id
    changes: list[ComparisonChange] = Field(max_length=2000)
    impacts: list[Impact]
