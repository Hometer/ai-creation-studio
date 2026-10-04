"""Durable local checks are observations, never execution permits."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from api.schemas.assets import Contract, Id
from api.schemas.generation_plans import Digest, ProjectId
from api.schemas.generation_readiness import ClosedContract, Time
from api.schemas.model_preflight import ConnectionCheck

Code = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")]


class ExecutionCheck(ClosedContract):
    check_id: Id
    preparation_id: Id
    preparation_revision: int = Field(ge=1, le=2)
    plan_id: Id
    quote_id: Id
    review_id: Id
    budget_id: Id
    content_hash: Digest
    quote_hash: Digest
    checked_at: Time
    basis_sha256: Digest
    connection: ConnectionCheck
    target_count: int = Field(ge=1, le=20)
    request_sha256: dict[ProjectId, Digest]
    conditions_ready: bool
    inputs_compiled: bool
    blockers: list[Code]
    execution_blockers: list[Code] = Field(min_length=4)
    preparation_only: Literal[True] = True
    real_execution_allowed: Literal[False] = False
    real_money_reserved: Literal[False] = False
    submit_attempts: Literal[0] = 0
    provider_task_id: None = None
    actual_charged_minor: None = None
    network_used: bool = False

    @model_validator(mode="after")
    def boundaries(self):
        if (self.network_used is not False or self.conditions_ready != (not self.blockers)
                or self.inputs_compiled != self.conditions_ready
                or len(self.request_sha256) != (self.target_count if self.inputs_compiled else 0)
                or len(set(self.blockers)) != len(self.blockers)
                or len(set(self.execution_blockers)) != len(self.execution_blockers)
                or not {"REAL_EXECUTION_DISABLED", "REAL_TRANSPORT_UNAVAILABLE",
                        "AUTHENTICATION_UNVERIFIED", "REAL_BILLING_UNCONNECTED"}.issubset(self.execution_blockers)):
            raise ValueError("Invalid execution check boundary")
        return self


class ExecutionCheckView(ExecutionCheck):
    current: bool
    current_blockers: list[Code]


class ExecutionChecksDocument(Contract):
    schema_version: Literal[1] = 1
    checks: dict[Id, ExecutionCheck] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def complete_records(cls, value):
        if isinstance(value, dict) and isinstance(value.get("checks"), dict):
            for key, record in value["checks"].items():
                if (not isinstance(record, dict) or record.get("check_id") != key
                        or not set(ExecutionCheck.model_fields).issubset(record)):
                    raise ValueError("Incomplete execution check")
        return value
