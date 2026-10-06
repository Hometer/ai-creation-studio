"""Paid media candidates, immutable approvals and durable provider checkpoints."""
from typing import Literal, Annotated
from pydantic import Field, StringConstraints, model_validator, field_validator
from api.schemas.assets import Contract, Id, ProjectId, Asset, AssetVersion, Reference
from api.schemas.co_creation import Hash
from api.schemas.model_preflight import ImageCheck

ProviderId=Annotated[str,StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')]
State=Literal['awaiting_confirmation','queued','submitting','pending','querying','ready_download','downloading','succeeded','failed','manual_review','cancelled']

class MediaPrepare(Contract):
    kind: Literal['image','video']
    source_id: Id
    segment_id: ProjectId | None = None
    expected_workspace: Hash
    prompt: str = Field(min_length=1,max_length=2000)
    preserve: str = Field(default='',max_length=2000)
    request_id: Id

    @model_validator(mode='after')
    def target(self):
        if not self.prompt.strip() or (self.kind=='video' and self.segment_id is None):raise ValueError('Invalid media scope')
        return self

class MediaPlan(Contract):
    project_id: ProjectId
    kind: Literal['image','video']
    source_id: Id
    segment_id: ProjectId | None
    expected_token: Hash
    reference_revision: int | None = Field(default=None,ge=1)
    request_digest: Hash
    model: Literal['wanx2.1-imageedit','wan2.7-i2v','wan2.7-i2v-2026-04-25']
    execution_prompt: str = Field(min_length=1,max_length=5000)
    preserve: str = Field(max_length=2000)
    duration: int | None = Field(default=None,ge=2,le=15)
    resolution: Literal['720P','1080P'] | None = None
    input_refs: list[ImageCheck] = Field(min_length=1,max_length=2)
    estimate_fen: int = Field(gt=0,le=500)
    quantity: Literal[1] = 1
    price_version: str
    expires_at: int

    @model_validator(mode='after')
    def pricing(self):
        if self.kind=='image':
            if self.reference_revision is None or self.model!='wanx2.1-imageedit' or self.duration is not None or self.resolution is not None or self.estimate_fen!=14 or len(self.execution_prompt)>800 or len(self.input_refs)!=1:raise ValueError('Invalid image plan')
        elif self.reference_revision is not None or self.model=='wanx2.1-imageedit' or self.duration is None or self.resolution is None or self.estimate_fen!=self.duration*(60 if self.resolution=='720P' else 100):raise ValueError('Invalid video price')
        return self

class MediaConfirm(Contract):
    plan_hash: Hash
    request_id: Id
    acknowledged: Literal[True]
    material_reviewed: Literal[True]
    @field_validator('acknowledged','material_reviewed',mode='before')
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError('Explicit acknowledgement required')
        return value

class MediaAction(Contract):
    acknowledged: Literal[True]
    @field_validator('acknowledged',mode='before')
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError('Explicit acknowledgement required')
        return value

class MediaSelect(MediaAction):
    expected_selection: Hash
    request_id: Id

class MediaJob(Contract):
    job_id: Id
    number: int = Field(ge=1)
    prepare: MediaPrepare
    plan: MediaPlan
    plan_hash: Hash
    created_at: str
    status: State = 'awaiting_confirmation'
    confirm_request_id: Id | None = None
    confirm_digest: Hash | None = None
    reserved_fen: int = Field(default=0,ge=0,le=500)
    submit_attempts: int = Field(default=0,ge=0,le=1)
    submitted_at: int | None = None
    provider_task_id: ProviderId | None = None
    query_attempts: int = Field(default=0,ge=0,le=30)
    failures: int = Field(default=0,ge=0,le=3)
    next_at: int = 0
    lease_until: int | None = None
    result_url: str | None = Field(default=None,max_length=8192,repr=False)
    asset_id: Id | None = None
    version_id: Id | None = None
    usage_estimate_fen: int | None = Field(default=None,ge=0,le=500)
    actual_billed_fen: None = None
    error: str | None = Field(default=None,max_length=300)
    simulation_only: bool = False
    @model_validator(mode='after')
    def checkpoint(self):
        from core.content_revisions import digest
        if digest(self.plan.model_dump())!=self.plan_hash or self.prepare.kind!=self.plan.kind or self.prepare.source_id!=self.plan.source_id or self.prepare.segment_id!=self.plan.segment_id or self.prepare.preserve!=self.plan.preserve:raise ValueError('Invalid plan checksum')
        if self.status in {'queued','submitting','pending','querying','ready_download','downloading','succeeded','failed','manual_review'} and self.confirm_request_id is None:raise ValueError('Missing approval')
        if self.confirm_request_id is None and self.reserved_fen!=0:raise ValueError('Unapproved reservation')
        if (self.confirm_request_id is None)!=(self.confirm_digest is None):raise ValueError('Incomplete approval')
        if self.confirm_request_id and self.reserved_fen!=self.plan.estimate_fen:raise ValueError('Invalid reservation')
        if self.submit_attempts and (self.confirm_request_id is None or self.submitted_at is None):raise ValueError('Unapproved submission')
        if self.status in {'pending','querying','ready_download','downloading','succeeded'} and (not self.provider_task_id or self.submit_attempts!=1):raise ValueError('Missing provider checkpoint')
        if self.status=='submitting' and self.submit_attempts!=1:raise ValueError('Invalid submit checkpoint')
        if (self.asset_id is None)!=(self.version_id is None) or (self.status=='succeeded')!=(self.asset_id is not None):raise ValueError('Invalid result checkpoint')
        return self

class MediaSelection(Contract):
    request_id: Id
    job_id: Id
    project_id: ProjectId
    kind: Literal['image','video']
    target: ProjectId
    asset_id: Id
    version_id: Id
    selected_at: str
    previous_token: Hash

class MediaView(Contract):
    job_id: Id
    number: int
    plan: MediaPlan
    plan_hash: Hash
    created_at: str
    status: State
    reserved_fen: int
    submit_attempts: int
    provider_task_id: ProviderId | None
    query_attempts: int
    usage_estimate_fen: int | None
    actual_billed_fen: None = None
    asset_id: Id | None
    version_id: Id | None
    error: str | None
    simulation_only: bool
    current: bool
    selected: bool
    selection_token: Hash

class ImageOption(Contract):
    reference: Reference
    asset: Asset
    version: AssetVersion

class MediaWorkspace(Contract):
    project_id: ProjectId
    expected_workspace: Hash
    credential_present: bool
    endpoint_allowed: bool
    price_current: bool
    single_limit_fen: Literal[500] = 500
    round_limit_fen: Literal[2000] = 2000
    occupied_fen: int = Field(ge=0,le=2000)
    available_fen: int = Field(ge=0,le=2000)
    images: list[ImageOption]
    jobs: list[MediaView]
    selections: list[MediaSelection]
