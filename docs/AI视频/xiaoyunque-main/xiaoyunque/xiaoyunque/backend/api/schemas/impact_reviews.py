"""Append-only human review decisions; never an execution authorization."""
from typing import Literal
from pydantic import Field, field_validator
from api.schemas.assets import Contract, Id
from api.schemas.content_revisions import Change, Stage, TextField

Hash = str
class ReviewInput(Contract):
    expected_token: Hash = Field(pattern=r'^[a-f0-9]{64}$')
    item_id: Hash = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['retain', 'follow_up']
    note: str = Field(min_length=1, max_length=1000)
    request_id: Id
    acknowledged: Literal[True]

    @field_validator('acknowledged', mode='before')
    @classmethod
    def confirmation(cls, value):
        if value is not True: raise ValueError('Explicit confirmation required')
        return value

    @field_validator('note')
    @classmethod
    def note_text(cls, value):
        if not value.strip(): raise ValueError('Review explanation required')
        return value

class ReviewRecord(Contract):
    review_id: Id
    number: int = Field(ge=1)
    item_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['retain', 'follow_up']
    note: str = Field(min_length=1, max_length=1000)
    context_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_id: Id
    request_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    created_at: str

class ReviewHistory(Contract):
    schema_version: Literal[1] = 1
    records: list[ReviewRecord] = Field(default_factory=list, max_length=2000)

class RecordView(ReviewRecord):
    applicable: bool

class ReviewItem(Contract):
    item_id: str
    version_id: Id
    version_number: int
    stage: Stage
    segment_id: str
    shot_number: int | None
    reason: str
    current_fields: list[TextField]
    target_exists: bool
    status: Literal['pending', 'follow_up', 'retained', 'stale']
    latest_review_id: Id | None

class ReviewSource(Contract):
    version_id: Id
    version_number: int
    stage: Stage
    changes: list[Change]

class ReviewWorkspace(Contract):
    project_id: str
    token: str
    running: bool
    untracked_stages: list[Stage]
    items: list[ReviewItem]
    records: list[RecordView]
    sources: list[ReviewSource]

class ReviewSaved(Contract):
    review_id: Id
    workspace: ReviewWorkspace
