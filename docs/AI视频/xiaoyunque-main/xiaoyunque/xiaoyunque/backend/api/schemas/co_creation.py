"""Human-authored requests and decisions; never model replies or execution grants."""
from typing import Annotated, Literal
from pydantic import Field, StringConstraints, field_validator
from api.schemas.assets import Contract, Id
from api.schemas.content_revisions import Stage, TextField, Preview, Workspace

Hash = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]

class RequestInput(Contract):
    expected_token: Hash
    request_id: Id
    stage: Stage
    path: list[str | int] = Field(min_length=1, max_length=10)
    message: str = Field(min_length=1, max_length=10000)
    preserve: str = Field(default='', max_length=2000)

    @field_validator('message')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('Empty request')
        return value

class DecisionInput(Contract):
    expected_token: Hash
    request_id: Id
    intent_id: Id
    decision: Literal['confirm', 'withdraw']
    acknowledged: Literal[True]

    @field_validator('acknowledged', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError('Explicit acknowledgement required')
        return value

class Intent(Contract):
    intent_id: Id
    number: int = Field(ge=1)
    stage: Stage
    target: TextField
    message: str = Field(min_length=1, max_length=10000)
    preserve: str = Field(max_length=2000)
    text_token: Hash
    created_at: str
    request_id: Id
    request_digest: Hash

class Decision(Contract):
    decision_id: Id
    number: int = Field(ge=1)
    intent_id: Id
    decision: Literal['confirm', 'withdraw']
    created_at: str
    request_id: Id
    request_digest: Hash

class Application(Contract):
    application_id: Id
    number: int = Field(ge=1)
    intent_id: Id
    version_id: Id
    created_at: str
    request_id: Id
    request_digest: Hash

class Ledger(Contract):
    schema_version: Literal[1] = 1
    records: list[Intent] = Field(default_factory=list, max_length=100)
    decisions: list[Decision] = Field(default_factory=list, max_length=200)
    applications: list[Application] = Field(default_factory=list, max_length=100)

class IntentView(Contract):
    intent_id: Id
    number: int
    stage: Stage
    target: TextField
    message: str
    preserve: str
    text_token: Hash
    created_at: str
    request_id: Id
    status: Literal['pending', 'confirmed', 'withdrawn', 'stale']

class DecisionView(Contract):
    decision_id: Id
    number: int
    intent_id: Id
    decision: Literal['confirm', 'withdraw']
    created_at: str
    request_id: Id

class CreationWorkspace(Contract):
    project_id: str
    token: Hash
    text_token: Hash
    running: bool
    model_enabled: Literal[False] = False
    fields: dict[Stage, list[TextField]]
    records: list[IntentView]
    decisions: list[DecisionView]

class CreationSaved(Contract):
    intent_id: Id
    workspace: CreationWorkspace

class ApplyInput(Contract):
    expected_token: Hash
    intent_id: Id
    value: str = Field(max_length=10000)

class ApplySave(ApplyInput):
    preview_token: Hash
    request_id: Id
    acknowledged: Literal[True]

    @field_validator('acknowledged', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError('Explicit acknowledgement required')
        return value

class ApplicationView(Contract):
    application_id: Id
    number: int
    intent_id: Id
    version_id: Id
    created_at: str
    request_id: Id

class ApplicationWorkspace(Contract):
    workspace: CreationWorkspace
    revisions: Workspace
    applications: list[ApplicationView]

class ApplyPreview(Contract):
    intent_id: Id
    expected_token: Hash
    preview_token: Hash
    revision: Preview

class Applied(Contract):
    application_id: Id
    version_id: Id
    data: ApplicationWorkspace
