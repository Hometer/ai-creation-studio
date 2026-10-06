"""Explicit, complete clip manifests and immutable local-film checkpoints."""
from typing import Literal
from pydantic import Field, model_validator, field_validator
from api.schemas.assets import Contract, Id, ProjectId, MediaInfo
from api.schemas.co_creation import Hash

State = Literal['draft', 'queued', 'rendering', 'succeeded', 'failed', 'manual_review', 'cancelled']

class ClipSelection(Contract):
    segment_id: ProjectId
    asset_id: Id
    version_id: Id

class ClipOption(Contract):
    asset_id: Id
    version_id: Id
    name: str = Field(min_length=1, max_length=100)
    number: int = Field(ge=1)
    media: MediaInfo
    source_type: str
    source_project: ProjectId | None
    simulation_only: bool
    stale: bool

class SegmentOption(Contract):
    segment_id: ProjectId
    label: str = Field(max_length=200)
    selected: ClipSelection | None

class CompositionPrepare(Contract):
    request_id: Id
    expected_workspace: Hash
    clips: list[ClipSelection] = Field(min_length=1, max_length=20)
    keep_stale: list[ProjectId] = Field(default_factory=list, max_length=20)
    @model_validator(mode='after')
    def unique(self):
        if len({c.segment_id for c in self.clips}) != len(self.clips) or len(set(self.keep_stale)) != len(self.keep_stale):
            raise ValueError('Repeated segments')
        return self

class CompositionClip(ClipSelection):
    name: str = Field(min_length=1, max_length=100)
    version_number: int = Field(ge=1)
    media: MediaInfo
    simulation_only: bool
    stale: bool

class CompositionPlan(Contract):
    project_id: ProjectId
    expected_workspace: Hash
    clips: list[CompositionClip] = Field(min_length=1, max_length=20)
    keep_stale: list[ProjectId] = Field(max_length=20)
    width: int = Field(ge=720, le=1280)
    height: int = Field(ge=720, le=1280)
    video_ratio: Literal['16:9', '9:16', '1:1', '4:3', '3:4']
    resolution: Literal['720P'] = '720P'
    fps: Literal[30] = 30
    target_duration: int | None = Field(default=None, ge=1, le=600)
    total_duration: float = Field(gt=0, le=600, allow_inf_nan=False)
    model_fee_fen: Literal[0] = 0
    @model_validator(mode='after')
    def consistent(self):
        expected = {'16:9': (1280,720), '9:16': (720,1280), '1:1': (720,720), '4:3': (960,720), '3:4': (720,960)}
        if (self.width, self.height) != expected[self.video_ratio]:raise ValueError('Invalid dimensions')
        if abs(sum(c.media.duration or 0 for c in self.clips) - self.total_duration) > .000001:raise ValueError('Invalid duration')
        if any(c.media.mime_type != 'video/mp4' for c in self.clips):raise ValueError('Only archived MP4 clips')
        ids = [c.segment_id for c in self.clips]
        if len(set(ids)) != len(ids) or set(self.keep_stale) != {c.segment_id for c in self.clips if c.stale}:raise ValueError('Invalid stale acknowledgement')
        return self

class CompositionConfirm(Contract):
    request_id: Id
    plan_hash: Hash
    acknowledged: Literal[True]
    @field_validator('acknowledged', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:raise ValueError('Explicit review required')
        return value

class CompositionJob(Contract):
    job_id: Id
    number: int = Field(ge=1)
    prepare: CompositionPrepare
    plan: CompositionPlan
    plan_hash: Hash
    created_at: str
    status: State = 'draft'
    confirmation: CompositionConfirm | None = None
    confirmed_at: str | None = None
    attempts: int = Field(default=0, ge=0, le=1)
    lease_until: int | None = None
    output: MediaInfo | None = None
    error: str | None = Field(default=None, max_length=300)
    @model_validator(mode='after')
    def checkpoint(self):
        from core.content_revisions import digest
        if digest(self.plan.model_dump()) != self.plan_hash:raise ValueError('Invalid plan digest')
        if self.prepare.expected_workspace != self.plan.expected_workspace or self.prepare.keep_stale != self.plan.keep_stale or [c.model_dump() for c in self.prepare.clips] != [c.model_dump(include={'segment_id','asset_id','version_id'}) for c in self.plan.clips]:raise ValueError('Invalid prepared manifest')
        if self.confirmation and self.confirmation.plan_hash != self.plan_hash:raise ValueError('Invalid confirmation')
        if (self.confirmation is None) != (self.confirmed_at is None):raise ValueError('Incomplete confirmation')
        if self.status in {'queued','rendering','succeeded','failed','manual_review'} and self.confirmation is None:raise ValueError('Missing approval')
        if self.attempts and self.confirmation is None:raise ValueError('Unapproved attempt')
        if self.status in {'rendering','succeeded','failed','manual_review'} and self.attempts != 1:raise ValueError('Invalid attempt checkpoint')
        if self.status == 'succeeded' and self.output is None:raise ValueError('Missing film')
        if self.output and (self.output.mime_type != 'video/mp4' or self.output.file_id != self.job_id):raise ValueError('Invalid film scope')
        if self.output and (self.status not in {'rendering','succeeded'} or self.output.width != self.plan.width or self.output.height != self.plan.height or abs(self.output.duration - self.plan.total_duration) > .2):raise ValueError('Invalid film checkpoint')
        return self

class CompositionStore(Contract):
    schema_version: Literal[1] = 1
    jobs: list[CompositionJob] = Field(default_factory=list, max_length=100)
    @model_validator(mode='after')
    def unique(self):
        ids = [j.job_id for j in self.jobs]
        requests = [j.prepare.request_id for j in self.jobs] + [j.confirmation.request_id for j in self.jobs if j.confirmation]
        if len(set(ids)) != len(ids) or len(set(requests)) != len(requests) or any(j.number != n for n,j in enumerate(self.jobs,1)):raise ValueError('Invalid job sequence')
        return self

class CompositionView(Contract):
    job_id: Id
    number: int
    plan: CompositionPlan
    plan_hash: Hash
    created_at: str
    status: State
    confirmed_at: str | None
    attempts: int
    output: MediaInfo | None
    error: str | None
    current: bool

class CompositionWorkspace(Contract):
    project_id: ProjectId
    expected_workspace: Hash
    segments: list[SegmentOption]
    options: list[ClipOption]
    jobs: list[CompositionView]
    video_ratio: str
    target_duration: int | None
    tools_available: bool
