"""Bounded initial creative documents and single-call approval checkpoints."""
from typing import Annotated, Literal
from pydantic import Field, StringConstraints, model_validator,field_validator
from api.schemas.assets import Contract,Id,ProjectId
from api.schemas.co_creation import Hash
from api.schemas.content_revisions import Stage,Change,Impact
Text=Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=2000)]
Short=Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=100)]

class DraftPerson(Contract):
    id: ProjectId
    name: Short
    description: Text

class DraftEpisode(Contract):
    episode_number: int = Field(ge=1,le=10)
    act_title: Short
    content: Text

class ScriptDraft(Contract):
    title: Short
    logline: Text
    beat_sheet: Text
    characters: list[DraftPerson] = Field(min_length=1,max_length=10)
    settings: list[DraftPerson] = Field(min_length=1,max_length=10)
    episodes: list[DraftEpisode] = Field(min_length=1,max_length=10)
    @model_validator(mode='after')
    def identities(self):
        ids=[p.id for p in self.characters+self.settings]
        if len(set(ids))!=len(ids) or [e.episode_number for e in self.episodes]!=list(range(1,len(self.episodes)+1)):raise ValueError('Duplicate or unordered script identities')
        return self

class DraftShot(Contract):
    shot_number: int = Field(ge=1,le=20)
    duration: int = Field(ge=1,le=15)
    content: Text
    action: str = Field(max_length=500)
    dialogue: str = Field(max_length=500)
    sound: str = Field(max_length=500)

class DraftSegment(Contract):
    segment_id: ProjectId
    visual_prompt: Text
    character_ids: list[ProjectId] = Field(max_length=10)
    setting_ids: list[ProjectId] = Field(min_length=1,max_length=10)
    shots: list[DraftShot] = Field(min_length=1,max_length=20)
    @model_validator(mode='after')
    def timing(self):
        if [s.shot_number for s in self.shots]!=list(range(1,len(self.shots)+1)) or sum(s.duration for s in self.shots)>15:raise ValueError('Invalid shot sequence or segment duration')
        if len(set(self.character_ids))!=len(self.character_ids) or len(set(self.setting_ids))!=len(self.setting_ids):raise ValueError('Duplicate asset identities')
        return self

class BoardEpisode(Contract):
    episode_number: int = Field(ge=1,le=10)
    episode_title: Short
    segments: list[DraftSegment] = Field(min_length=1,max_length=20)

class BoardDraft(Contract):
    episodes: list[BoardEpisode] = Field(min_length=1,max_length=10)
    @model_validator(mode='after')
    def identities(self):
        segments=[s for e in self.episodes for s in e.segments]
        if len(segments)>20 or len({s.segment_id for s in segments})!=len(segments) or [e.episode_number for e in self.episodes]!=list(range(1,len(self.episodes)+1)):raise ValueError('Invalid storyboard scope')
        return self

class InitialResult(Contract):
    kind: Stage
    content: ScriptDraft | BoardDraft
    explanation: Text
    preservation: Text
    @model_validator(mode='after')
    def content_type(self):
        if (self.kind=='script_generation') != isinstance(self.content,ScriptDraft):raise ValueError('Wrong creative document')
        return self

class InitialPrepare(Contract):
    request_id: Id
    expected_workspace: Hash
    kind: Stage
    prompt: Text
    preserve: str = Field(default='',max_length=2000)

class InitialPlan(Contract):
    project_id: ProjectId
    expected_workspace: Hash
    kind: Stage
    prompt: Text
    preserve: str = Field(max_length=2000)
    model: Literal['qwen-plus-2025-12-01'] = 'qwen-plus-2025-12-01'
    input_digest: Hash
    input_byte_size: int = Field(gt=0,le=100000)
    max_output_tokens: Literal[4096] = 4096
    estimate_fen: int = Field(gt=0,le=500)
    price_version: str
    expires_at: int
    episodes: int = Field(ge=1,le=10)
    target_duration: int | None = Field(default=None,ge=1,le=600)
    scope: str

class InitialConfirm(Contract):
    request_id: Id
    plan_hash: Hash
    acknowledged: Literal[True]
    material_reviewed: Literal[True]
    @field_validator('acknowledged','material_reviewed',mode='before')
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError('Explicit confirmation required')
        return value

class InitialSelect(Contract):
    request_id: Id
    expected_workspace: Hash
    result_digest: Hash
    acknowledged: Literal[True]
    @field_validator('acknowledged',mode='before')
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError('Explicit selection required')
        return value

class InitialSelection(Contract):
    request_id: Id
    job_id: Id
    kind: Stage
    version_id: Id
    result_digest: Hash
    input_digest: Hash
    selected_at: str

class InitialJob(Contract):
    job_id: Id
    number: int = Field(ge=1)
    prepare_request_id: Id
    plan: InitialPlan
    plan_hash: Hash
    messages: list[dict]
    created_at: str
    status: Literal['awaiting_confirmation','queued','submitting','succeeded','failed','manual_review'] = 'awaiting_confirmation'
    confirm_request_id: Id | None = None
    confirm_digest: Hash | None = None
    reserved_fen: int = Field(default=0,ge=0,le=500)
    attempts: int = Field(default=0,ge=0,le=1)
    lease_until: int | None = None
    result: InitialResult | None = None
    usage_estimate_fen: int | None = Field(default=None,ge=0,le=500)
    actual_billed_fen: None = None
    error: str | None = Field(default=None,max_length=300)
    simulation_only: bool = False
    @model_validator(mode='after')
    def checkpoint(self):
        from core.content_revisions import digest,encoded
        if digest(self.plan.model_dump())!=self.plan_hash or digest(self.messages)!=self.plan.input_digest or len(encoded(self.messages))!=self.plan.input_byte_size:raise ValueError('Invalid initial plan checksum')
        if (self.confirm_request_id is None)!=(self.confirm_digest is None):raise ValueError('Incomplete confirmation')
        if self.status!='awaiting_confirmation' and self.confirm_request_id is None:raise ValueError('Missing approval')
        if self.confirm_request_id and self.reserved_fen!=self.plan.estimate_fen or self.confirm_request_id is None and self.reserved_fen:raise ValueError('Invalid reservation')
        if self.status in {'submitting','succeeded','manual_review'} and self.attempts!=1:raise ValueError('Invalid attempt checkpoint')
        if (self.status=='succeeded')!=(self.result is not None) or self.result and self.result.kind!=self.plan.kind:raise ValueError('Invalid result scope')
        if self.usage_estimate_fen is not None and self.usage_estimate_fen>self.reserved_fen:raise ValueError('Unbounded usage')
        return self

class InitialView(Contract):
    job_id: Id
    number: int
    plan: InitialPlan
    plan_hash: Hash
    created_at: str
    status: InitialJob.__annotations__['status']
    reserved_fen: int
    attempts: int
    result: InitialResult | None
    usage_estimate_fen: int | None
    actual_billed_fen: None = None
    error: str | None
    simulation_only: bool
    current: bool
    selected: bool
    selected_version_id: Id | None
    result_digest: Hash | None
    changes: list[Change]
    impacts: list[Impact]

class InitialWorkspace(Contract):
    project_id: ProjectId
    expected_workspace: Hash
    story: str
    style: str
    video_ratio: str
    target_duration: int | None
    episodes: int
    script: dict
    storyboard: dict
    credential_present: bool
    endpoint_allowed: bool
    price_current: bool
    single_limit_fen: Literal[500] = 500
    round_limit_fen: Literal[2000] = 2000
    occupied_fen: int = Field(ge=0,le=2000)
    available_fen: int = Field(ge=0,le=2000)
    jobs: list[InitialView]
