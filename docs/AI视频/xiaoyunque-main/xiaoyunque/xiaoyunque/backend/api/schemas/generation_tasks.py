"""Durable quotes, local simulation approvals and recoverable task contracts."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from api.schemas.assets import Contract, Id, ProjectId
from api.schemas.generation_plans import Digest, VideoOptions, digest

Amount = Annotated[int, Field(ge=0, le=1_000_000_000)]
ProviderId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Status = Literal["blocked", "queued", "submitting", "submitted", "running", "fetching", "succeeded", "failed", "manual_review"]


class QuoteInput(Contract):
    plan_id: Id
    segment_id: ProjectId


class Offer(QuoteInput):
    project_id: ProjectId
    content_hash: Digest
    options: VideoOptions
    duration: int = Field(ge=1, le=60)
    pricing_kind: Literal["unavailable", "simulation"]
    currency: Literal["SIM"] | None
    amount_minor: Amount | None
    basis: str
    expires_at: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_price(self):
        if self.pricing_kind == "unavailable":
            if self.currency is not None or self.amount_minor is not None:
                raise ValueError("Unknown price cannot be zero")
        elif self.currency != "SIM" or self.amount_minor is None:
            raise ValueError("Simulation uses SIM only")
        return self


class QuoteConfirm(Contract):
    quote_hash: Digest
    max_amount_minor: Amount
    project_limit_minor: Amount
    simulation_only: bool

    @model_validator(mode="after")
    def acknowledged_simulation(self):
        if self.simulation_only is not True or self.max_amount_minor > self.project_limit_minor:
            raise ValueError("Invalid local simulation acknowledgement or budget")
        return self


class Approval(QuoteConfirm):
    confirmed_at: str


class Quote(Contract):
    quote_id: Id
    created_at: str
    offer: Offer
    quote_hash: Digest
    approval: Approval | None = None

    @model_validator(mode="after")
    def valid_quote(self):
        if digest(self.offer.model_dump(mode="json")) != self.quote_hash:
            raise ValueError("Invalid quote hash")
        if self.approval and (self.approval.quote_hash != self.quote_hash
                or self.offer.pricing_kind != "simulation" or self.offer.amount_minor > self.approval.max_amount_minor):
            raise ValueError("Invalid approval")
        return self


class QuoteView(Quote):
    current: bool
    expired: bool
    real_execution_allowed: Literal[False] = False


class TaskInput(Contract):
    quote_id: Id
    request_id: Id


class EnqueueInput(Contract):
    expected_revision: int = Field(ge=1)
    quote_hash: Digest


class Billing(Contract):
    state: Literal["unreserved", "reserved", "settled"] = "unreserved"
    reserved_minor: Amount = 0
    billed_minor: Amount | None = None

    @model_validator(mode="after")
    def consistent_billing(self):
        if (self.state == "unreserved" and (self.reserved_minor != 0 or self.billed_minor is not None)
                or self.state == "reserved" and self.billed_minor is not None
                or self.state == "settled" and (self.billed_minor is None or self.billed_minor > self.reserved_minor)):
            raise ValueError("Invalid billing state")
        return self


class TaskError(Contract):
    code: Literal["PROVIDER_INIT_FAILED", "SUBMISSION_UNCERTAIN", "SUBMISSION_NOT_FOUND", "QUERY_FAILED",
        "QUERY_LIMIT_REACHED", "PROVIDER_FAILED", "PROVIDER_CONTRACT_INVALID", "RESULT_FAILED", "SOURCE_CHANGED", "QUOTE_EXPIRED"]
    message: str


class Result(Contract):
    asset_id: Id
    version_id: Id
    sha256: Digest


class Task(TaskInput):
    task_id: Id
    project_id: ProjectId
    plan_id: Id
    segment_id: ProjectId
    content_hash: Digest
    created_at: str
    updated_at: str
    revision: int = Field(ge=1)
    status: Status = "blocked"
    provider_task_id: ProviderId | None = None
    submit_attempts: int = Field(default=0, ge=0, le=1)
    query_attempts: int = Field(default=0, ge=0, le=20)
    consecutive_errors: int = Field(default=0, ge=0, le=3)
    fetch_attempts: int = Field(default=0, ge=0, le=3)
    lease_token: Id | None = None
    lease_until: int | None = None
    billing: Billing = Field(default_factory=Billing)
    observed_billed_minor: Amount | None = None
    error: TaskError | None = None
    result: Result | None = None

    @model_validator(mode="after")
    def consistent_task(self):
        if (self.lease_token is None) != (self.lease_until is None):
            raise ValueError("Incomplete lease")
        if (self.status == "succeeded") != (self.result is not None):
            raise ValueError("Missing result or premature success")
        if self.status in {"submitting", "submitted", "running", "fetching", "succeeded", "manual_review"} and self.submit_attempts != 1:
            raise ValueError("Missing submission checkpoint")
        if self.status in {"submitted", "running", "fetching", "succeeded"} and self.provider_task_id is None:
            raise ValueError("Missing provider task")
        return self


class TaskView(Task):
    progress: int
    blockers: list[str]
    simulation_only: bool
    real_execution_allowed: Literal[False] = False
    result_url: str | None


class BudgetView(Contract):
    project_id: ProjectId
    currency: Literal["SIM"] | None
    limit_minor: Amount | None
    reserved_minor: Amount
    settled_minor: Amount
    available_minor: Amount | None
    real_execution_allowed: Literal[False] = False


class TaskStoreDocument(Contract):
    schema_version: Literal[1] = 1
    quotes: dict[Id, Quote] = Field(default_factory=dict)
    tasks: dict[Id, Task] = Field(default_factory=dict)
    budget_limits: dict[ProjectId, Amount] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_graph(self):
        used_keys, used_quotes = set(), set()
        for key, quote in self.quotes.items():
            if key != quote.quote_id:
                raise ValueError("Quote ID mismatch")
            if quote.approval is not None and quote.offer.project_id not in self.budget_limits:
                raise ValueError("Confirmed quote requires a project budget")
        for key, task in self.tasks.items():
            quote = self.quotes.get(task.quote_id)
            if (key != task.task_id or quote is None or task.request_id in used_keys or task.quote_id in used_quotes
                    or task.project_id != quote.offer.project_id or task.plan_id != quote.offer.plan_id
                    or task.segment_id != quote.offer.segment_id or task.content_hash != quote.offer.content_hash):
                raise ValueError("Invalid task graph")
            if task.billing.state != "unreserved":
                if quote.approval is None or task.billing.reserved_minor != quote.offer.amount_minor:
                    raise ValueError("Unapproved reservation")
            elif task.status != "blocked":
                raise ValueError("A runnable task requires a reservation")
            used_keys.add(task.request_id)
            used_quotes.add(task.quote_id)
        for project_id, limit in self.budget_limits.items():
            used = sum(t.billing.billed_minor if t.billing.state == "settled" else t.billing.reserved_minor
                       for t in self.tasks.values() if t.project_id == project_id)
            if used > limit:
                raise ValueError("Budget overdrawn")
        if any(t.billing.state != "unreserved" and t.project_id not in self.budget_limits for t in self.tasks.values()):
            raise ValueError("Missing budget")
        return self


class Submission(Contract):
    provider_task_id: ProviderId


class Observation(Contract):
    status: Literal["running", "succeeded", "failed", "unknown"]
    provider_task_id: ProviderId | None = None
    billed_minor: Amount | None = None

    @model_validator(mode="after")
    def consistent_observation(self):
        if self.status != "unknown" and self.provider_task_id is None:
            raise ValueError("Missing task identifier")
        if self.status in {"running", "unknown"} and self.billed_minor is not None:
            raise ValueError("Premature billing")
        return self
