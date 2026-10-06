"""Typed plans, durable candidates and bounded first-release CNY approvals."""
from typing import Literal
from pydantic import Field, field_validator, model_validator
from api.schemas.assets import Contract, Id, ProjectId
from api.schemas.co_creation import Hash
from api.schemas.release_media import MediaJob, MediaSelection


class CandidateResult(Contract):
    value: str = Field(min_length=1, max_length=10000)
    explanation: str = Field(min_length=1, max_length=2000)
    preservation: str = Field(min_length=1, max_length=2000)

    @field_validator('value', 'explanation', 'preservation')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Empty model result')
        return value


class CandidatePrepare(Contract):
    expected_token: Hash
    intent_id: Id
    request_id: Id


class CandidateConfirm(Contract):
    plan_hash: Hash
    expected_token: Hash
    request_id: Id
    acknowledged: Literal[True]
    material_reviewed: Literal[True]

    @field_validator('acknowledged', 'material_reviewed', mode='before')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError('Explicit confirmation required')
        return value


class CandidatePlan(Contract):
    project_id: ProjectId
    intent_id: Id
    expected_token: Hash
    model: Literal['qwen-plus-2025-12-01'] = 'qwen-plus-2025-12-01'
    input_digest: Hash
    input_byte_size: int = Field(gt=0, le=100000)
    max_output_tokens: Literal[4096] = 4096
    estimate_fen: int = Field(gt=0, le=500)
    price_version: str
    expires_at: int
    scope: str


class CandidateJob(Contract):
    job_id: Id
    number: int = Field(ge=1)
    prepare_request_id: Id
    plan: CandidatePlan
    plan_hash: Hash
    messages: list[dict]
    created_at: str
    status: Literal['awaiting_confirmation', 'queued', 'submitting', 'succeeded', 'failed', 'manual_review'] = 'awaiting_confirmation'
    confirm_request_id: Id | None = None
    confirm_digest: Hash | None = None
    reserved_fen: int = Field(default=0, ge=0, le=500)
    attempts: int = Field(default=0, ge=0, le=1)
    lease_until: int | None = None
    result: CandidateResult | None = None
    usage_estimate_fen: int | None = Field(default=None, ge=0)
    actual_billed_fen: None = None
    error: str | None = None
    simulation_only: bool = False

    @model_validator(mode='after')
    def consistent(self):
        from core.content_revisions import digest
        if digest(self.plan.model_dump()) != self.plan_hash or digest(self.messages) != self.plan.input_digest:
            raise ValueError('Candidate checksum mismatch')
        if (self.confirm_request_id is None) != (self.confirm_digest is None):
            raise ValueError('Incomplete confirmation')
        if self.status != 'awaiting_confirmation' and self.confirm_request_id is None:
            raise ValueError('Unconfirmed execution')
        if self.confirm_request_id and self.reserved_fen != self.plan.estimate_fen:
            raise ValueError('Reservation mismatch')
        if self.status in {'submitting', 'succeeded', 'manual_review'} and self.attempts != 1:
            raise ValueError('Missing submit checkpoint')
        if (self.status == 'succeeded') != (self.result is not None):
            raise ValueError('Premature model result')
        return self


class CandidateLedger(Contract):
    schema_version: Literal[1] = 1
    round_id: Literal['20261006-first-release'] = '20261006-first-release'
    single_limit_fen: Literal[500] = 500
    round_limit_fen: Literal[2000] = 2000
    jobs: list[CandidateJob] = Field(default_factory=list, max_length=200)
    media_jobs: list[MediaJob] = Field(default_factory=list, max_length=100)
    media_selections: list[MediaSelection] = Field(default_factory=list, max_length=500)

    def occupied(self):
        return sum(j.reserved_fen for j in self.jobs+self.media_jobs)

    def used_requests(self):
        values={j.prepare_request_id for j in self.jobs}|{j.prepare.request_id for j in self.media_jobs}|{s.request_id for s in self.media_selections}
        return values|{j.confirm_request_id for j in self.jobs+self.media_jobs if j.confirm_request_id}

    @model_validator(mode='after')
    def graph(self):
        ids, requests = set(), set()
        for n, job in enumerate(self.jobs, 1):
            if job.number != n or job.job_id in ids or job.prepare_request_id in requests:
                raise ValueError('Duplicate candidate identity')
            ids.add(job.job_id)
            requests.add(job.prepare_request_id)
            if job.confirm_request_id:
                if job.confirm_request_id in requests:
                    raise ValueError('Duplicate confirmation')
                requests.add(job.confirm_request_id)
        for n, job in enumerate(self.media_jobs, 1):
            if job.number!=n or job.job_id in ids or job.prepare.request_id in requests:raise ValueError('Duplicate media task')
            ids.add(job.job_id);requests.add(job.prepare.request_id)
            if job.confirm_request_id:
                if job.confirm_request_id in requests:raise ValueError('Duplicate media approval')
                requests.add(job.confirm_request_id)
        selected=set()
        for selection in self.media_selections:
            job=next((j for j in self.media_jobs if j.job_id==selection.job_id),None)
            if job is None or job.status!='succeeded' or selection.project_id!=job.plan.project_id or selection.kind!=job.plan.kind or selection.target!=(job.plan.segment_id if job.plan.kind=='video' else job.plan.source_id) or selection.asset_id!=job.asset_id or selection.version_id!=job.version_id or selection.request_id in requests or selection.request_id in selected:raise ValueError('Invalid media selection')
            selected.add(selection.request_id)
        if self.occupied() > self.round_limit_fen:
            raise ValueError('Round budget exceeded')
        return self


class CandidateView(Contract):
    job_id: Id
    number: int
    plan: CandidatePlan
    plan_hash: Hash
    created_at: str
    status: CandidateJob.__annotations__['status']
    reserved_fen: int
    attempts: int
    result: CandidateResult | None
    usage_estimate_fen: int | None
    actual_billed_fen: None = None
    error: str | None
    current: bool
    simulation_only: bool


class CandidateWorkspace(Contract):
    project_id: ProjectId
    expected_token: Hash
    credential_present: bool
    endpoint_allowed: bool
    single_limit_fen: Literal[500] = 500
    round_limit_fen: Literal[2000] = 2000
    occupied_fen: int = Field(ge=0, le=2000)
    available_fen: int = Field(ge=0, le=2000)
    price_current: bool
    jobs: list[CandidateView]
