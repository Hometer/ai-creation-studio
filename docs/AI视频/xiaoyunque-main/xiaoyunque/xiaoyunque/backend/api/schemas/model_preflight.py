"""Offline protocol observations, never permission to execute a paid request."""

from typing import Literal

from pydantic import Field, model_validator

from api.schemas.assets import Contract, Id, ProjectId
from api.schemas.generation_plans import Digest, ModelName, Role

SPEC_VERSION = "wan-i2v-2026-10-04"
MODELS = ("wan2.7-i2v", "wan2.7-i2v-2026-04-25")


class EmptyPreflightInput(Contract):
    pass


class ConnectionCheck(Contract):
    credential_present: bool
    endpoint_region: Literal["beijing", "singapore", "unrecognized"]
    endpoint_allowed: bool
    authentication_verified: Literal[False] = False

    @model_validator(mode="after")
    def region_policy(self):
        if self.endpoint_allowed != (self.endpoint_region == "beijing"):
            raise ValueError("Invalid domestic endpoint policy")
        return self


IssueCode = Literal[
    "MODEL_NOT_ADAPTED", "MODE_NOT_ADAPTED", "DURATION_UNSUPPORTED", "RESOLUTION_UNSUPPORTED",
    "PROMPT_TOO_LONG", "SOUND_OFF_UNSUPPORTED", "MEDIA_UNAVAILABLE", "IMAGE_DIMENSIONS_UNSUPPORTED",
    "IMAGE_RATIO_UNSUPPORTED", "PNG_ALPHA_UNSUPPORTED", "SHOT_TYPE_NOT_SENT", "RATIO_DERIVED_FROM_MEDIA",
    "PLAN_NOT_CONFIRMED", "PLAN_SOURCE_CHANGED", "KEY_MISSING", "ENDPOINT_UNSUPPORTED",
    "NON_MAINLAND_ENDPOINT", "INPUT_PROTOCOL_INVALID", "REAL_PRICING_UNCONFIGURED",
    "REAL_EXECUTION_DISABLED", "REAL_ADAPTER_NOT_IMPLEMENTED", "AUTHENTICATION_UNVERIFIED",
    "SAMPLE_REVIEW_REQUIRED",
]


class Issue(Contract):
    code: IssueCode
    severity: Literal["error", "warning"] = "error"
    message: str = Field(min_length=1, max_length=200)


class ImageCheck(Contract):
    reference_id: Id
    asset_id: Id
    version_id: Id
    role: Role
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    byte_size: int = Field(gt=0)
    sha256: Digest


class TargetCheck(Contract):
    segment_id: ProjectId
    model: ModelName
    duration: int = Field(ge=1, le=60)
    prompt_characters: int = Field(ge=1)
    input_protocol_ready: bool
    request_sha256: Digest | None
    images: list[ImageCheck]
    issues: list[Issue]

    @model_validator(mode="after")
    def consistent_checks(self):
        if self.input_protocol_ready != (not any(i.severity == "error" for i in self.issues)):
            raise ValueError("Invalid readiness")
        if self.request_sha256 is not None and not self.input_protocol_ready:
            raise ValueError("Cannot compile invalid inputs")
        return self


class RecoveryContract(Contract):
    requires_provider_task_id: Literal[True] = True
    query_by_client_request_id: Literal[False] = False
    missing_task_id_action: Literal["manual_review_no_resubmit"] = "manual_review_no_resubmit"
    query_window_seconds: Literal[86400] = 86400
    result_window_seconds: Literal[86400] = 86400


class PreflightReport(Contract):
    report_id: Id
    plan_id: Id
    project_id: ProjectId
    content_hash: Digest
    checked_at: str
    spec_version: Literal["wan-i2v-2026-10-04"] = SPEC_VERSION
    provider: Literal["dashscope"] = "dashscope"
    plan_confirmed_at_check: bool
    plan_current_at_check: bool
    connection: ConnectionCheck
    targets: list[TargetCheck] = Field(min_length=1, max_length=20)
    input_protocol_ready: bool
    blockers: list[Issue]
    real_execution_allowed: Literal[False] = False
    network_used: Literal[False] = False
    sample_rights_verified: Literal[False] = False
    pricing_kind: Literal["unavailable"] = "unavailable"
    amount_minor: None = None
    currency: None = None
    recovery: RecoveryContract = Field(default_factory=RecoveryContract)

    @model_validator(mode="after")
    def consistent_report(self):
        expected = self.plan_confirmed_at_check and self.plan_current_at_check and all(t.input_protocol_ready for t in self.targets)
        if self.input_protocol_ready != expected or len({t.segment_id for t in self.targets}) != len(self.targets):
            raise ValueError("Invalid report graph")
        if any(t.request_sha256 is not None for t in self.targets) and not (self.plan_confirmed_at_check and self.plan_current_at_check):
            raise ValueError("Unconfirmed request compilation")
        return self


class ReportView(PreflightReport):
    current: bool


class PreflightDocument(Contract):
    schema_version: Literal[1] = 1
    reports: dict[Id, PreflightReport] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_graph(self):
        if any(key != value.report_id for key, value in self.reports.items()):
            raise ValueError("Report ID mismatch")
        return self


class WanMedia(Contract):
    type: Literal["first_frame", "last_frame"]
    url: str = Field(pattern=r"^data:image/(jpeg|png|bmp|webp);base64,[A-Za-z0-9+/]+=*$")


class WanInput(Contract):
    prompt: str = Field(min_length=1, max_length=5000)
    media: list[WanMedia] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def valid_roles(self):
        if [m.type for m in self.media] not in [["first_frame"], ["first_frame", "last_frame"]]:
            raise ValueError("Invalid i2v media")
        return self


class WanParameters(Contract):
    duration: int = Field(ge=2, le=15)
    resolution: Literal["720P", "1080P"]
    prompt_extend: Literal[False] = False
    watermark: Literal[False] = False


class WanRequest(Contract):
    model: Literal["wan2.7-i2v", "wan2.7-i2v-2026-04-25"]
    input: WanInput
    parameters: WanParameters
