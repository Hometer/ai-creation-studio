"""Offline-only recovery records; never a production authorization or price."""

import math
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from api.schemas.assets import Contract
from api.schemas.generation_plans import Digest

ProviderId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Scenario = Literal["success", "lost-submit", "query-timeout", "bad-video"]
State = Literal["new", "submitting", "submitted", "querying", "running", "result_ready",
                "downloading", "succeeded", "failed", "canceled", "manual_review"]


class VideoResult(Contract):
    sha256: Digest
    byte_size: int = Field(gt=0, le=100 * 1024 * 1024)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration: float = Field(gt=0, allow_inf_nan=False)


class ReplayRecord(Contract):
    scenario: Scenario
    state: State = "new"
    request_sha256: Digest
    started_at: float = Field(ge=0, allow_inf_nan=False)
    provider_task_id: ProviderId | None = None
    submit_attempts: int = Field(default=0, ge=0, le=1)
    query_attempts: int = Field(default=0, ge=0, le=20)
    consecutive_query_errors: int = Field(default=0, ge=0, le=3)
    download_attempts: int = Field(default=0, ge=0, le=3)
    usage_seconds: int | None = Field(default=None, ge=1, le=15)
    video_count: Literal[1] | None = None
    result: VideoResult | None = None
    error_code: Literal["SUBMIT_UNCERTAIN", "QUERY_UNAVAILABLE", "QUERY_LIMIT", "TASK_EXPIRED",
                        "PROVIDER_UNKNOWN", "PROVIDER_FAILED", "PROVIDER_CANCELED",
                        "DOWNLOAD_UNAVAILABLE", "DOWNLOAD_LIMIT", "RESULT_CORRUPT"] | None = None
    simulation_only: Literal[True] = True
    network_used: Literal[False] = False
    real_execution_allowed: Literal[False] = False
    amount_minor: None = None
    currency: None = None

    @model_validator(mode="before")
    @classmethod
    def strict_literals(cls, value):
        if isinstance(value, dict):
            for key, expected in (("simulation_only", True), ("network_used", False), ("real_execution_allowed", False)):
                if key in value and value[key] is not expected:
                    raise ValueError("Invalid offline literal")
            if value.get("video_count") is not None and type(value["video_count"]) is not int:
                raise ValueError("Invalid usage literal")
        return value

    @model_validator(mode="after")
    def graph(self):
        if type(self.video_count) is bool or self.simulation_only is not True or self.network_used is not False or self.real_execution_allowed is not False:
            raise ValueError("Invalid offline boundary")
        if not math.isfinite(self.started_at):
            raise ValueError("Invalid checkpoint time")
        if self.state == "new" and (self.submit_attempts or self.provider_task_id):
            raise ValueError("Invalid new record")
        if self.state == "new" and (self.query_attempts or self.download_attempts or self.usage_seconds is not None):
            raise ValueError("Invalid new counters")
        if self.state != "new" and self.submit_attempts != 1:
            raise ValueError("Missing submission checkpoint")
        if self.state not in {"new", "submitting", "manual_review"} and not self.provider_task_id:
            raise ValueError("Missing platform identity")
        if self.state == "submitting" and self.provider_task_id is not None:
            raise ValueError("Ambiguous submission checkpoint")
        if self.consecutive_query_errors >= 3 and self.state != "manual_review":
            raise ValueError("Unbounded query recovery")
        if (self.result is not None) != (self.state == "succeeded"):
            raise ValueError("Invalid result checkpoint")
        if (self.usage_seconds is None) != (self.video_count is None):
            raise ValueError("Incomplete usage")
        if self.state in {"result_ready", "downloading", "succeeded"} and self.usage_seconds is None:
            raise ValueError("Missing successful usage")
        return self


class ReplayDocument(Contract):
    schema_version: Literal[1] = 1
    record: ReplayRecord | None = None

    @model_validator(mode="before")
    @classmethod
    def complete_record(cls, value):
        record = value.get("record") if isinstance(value, dict) else None
        if isinstance(record, dict) and set(record) != set(ReplayRecord.model_fields):
            raise ValueError("Incomplete replay checkpoint")
        return value


class FixtureDocument(Contract):
    schema_version: Literal[1] = 1
    submit_calls: int = Field(default=0, ge=0)
    query_calls: int = Field(default=0, ge=0)
    download_calls: int = Field(default=0, ge=0)
