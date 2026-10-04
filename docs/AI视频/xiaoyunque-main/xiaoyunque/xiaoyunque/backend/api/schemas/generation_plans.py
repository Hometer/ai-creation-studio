"""Local plan contracts. Confirmation never authorizes a model call."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from api.schemas.assets import AssetVersion, Contract, Id, Name, ProjectId, Target, TextKind

Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Role = Literal["context", "first_frame", "last_frame", "reference_image"]
ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}$")]


def digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class VideoOptions(Contract):
    model: ModelName
    mode: Literal["first_frame", "start_end_frame", "reference"]
    video_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"] = "16:9"
    resolution: Literal["480P", "720P", "1080P"] = "720P"
    sound: Literal["on", "off"] = "on"
    shot_type: Literal["single", "multi"] = "multi"
    style: Name = "realistic"


class BindingInput(Contract):
    reference_id: Id
    role: Role


class PlanTargetInput(Contract):
    segment_id: ProjectId
    duration: int = Field(ge=1, le=60)
    bindings: list[BindingInput] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_bindings(self):
        if len({b.reference_id for b in self.bindings}) != len(self.bindings):
            raise ValueError("Duplicate binding")
        return self


class PlanInput(Contract):
    project_id: ProjectId
    options: VideoOptions
    targets: list[PlanTargetInput] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_targets(self):
        if len({t.segment_id for t in self.targets}) != len(self.targets):
            raise ValueError("Duplicate target")
        return self


class PinnedBinding(BindingInput):
    revision: int = Field(ge=1)
    target: Target
    asset_id: Id
    kind: TextKind | Literal["image"]
    version: AssetVersion

    @model_validator(mode="after")
    def matching_kind(self):
        if self.role == "context":
            if self.kind == "image" or self.version.media is not None:
                raise ValueError("Invalid context")
        elif self.kind != "image" or self.version.media is None or not self.version.media.mime_type.startswith("image/"):
            raise ValueError("Invalid image binding")
        return self


class Shot(Contract):
    shot_number: int = Field(ge=1)
    duration: float = Field(gt=0, le=60, allow_inf_nan=False)
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


class PlannedTarget(Contract):
    segment_id: ProjectId
    duration: int = Field(ge=1, le=60)
    source_hash: Digest
    shots: list[Shot] = Field(min_length=1, max_length=100)
    bindings: list[PinnedBinding] = Field(min_length=1, max_length=20)
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=250000)]


class PlanContent(Contract):
    project_id: ProjectId
    template_version: Literal[1] = 1
    options: VideoOptions
    targets: list[PlannedTarget] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def valid_targets(self):
        if len({t.segment_id for t in self.targets}) != len(self.targets):
            raise ValueError("Duplicate target")
        for target in self.targets:
            if len({s.shot_number for s in target.shots}) != len(target.shots):
                raise ValueError("Duplicate shot")
            if len({b.reference_id for b in target.bindings}) != len(target.bindings):
                raise ValueError("Duplicate binding")
            counts = {role: sum(b.role == role for b in target.bindings)
                      for role in ("first_frame", "last_frame", "reference_image")}
            expected = {"first_frame": (1, 0, 0), "start_end_frame": (1, 1, 0)}
            if self.options.mode in expected:
                if tuple(counts.values()) != expected[self.options.mode]:
                    raise ValueError("Invalid input roles")
            elif counts["first_frame"] or counts["last_frame"] or not 1 <= counts["reference_image"] <= 4:
                raise ValueError("Invalid reference count")
            for binding in target.bindings:
                scope = binding.target
                if (scope.project_id != self.project_id
                        or scope.segment_id not in {None, target.segment_id}
                        or scope.shot_number is not None and scope.shot_number not in {s.shot_number for s in target.shots}):
                    raise ValueError("Invalid binding scope")
        return self


class Confirmation(Contract):
    confirmed_at: str
    content_hash: Digest
    scope: Literal["local_input_only"] = "local_input_only"


class ConfirmInput(Contract):
    content_hash: Digest
    local_only: bool

    @model_validator(mode="after")
    def local_confirmation_only(self):
        if self.local_only is not True:
            raise ValueError("Local acknowledgement required")
        return self


class Plan(Contract):
    plan_id: Id
    created_at: str
    content: PlanContent
    content_hash: Digest
    confirmation: Confirmation | None = None

    @model_validator(mode="after")
    def valid_hash(self):
        if digest(self.content.model_dump(mode="json")) != self.content_hash:
            raise ValueError("Plan checksum mismatch")
        if self.confirmation is not None and self.confirmation.content_hash != self.content_hash:
            raise ValueError("Confirmation checksum mismatch")
        return self


class PlanStoreDocument(Contract):
    schema_version: Literal[1] = 1
    plans: dict[Id, Plan] = Field(default_factory=dict)

    @model_validator(mode="after")
    def matching_ids(self):
        if any(key != plan.plan_id for key, plan in self.plans.items()):
            raise ValueError("Plan ID mismatch")
        return self


class TargetImpact(Contract):
    segment_id: ProjectId
    reasons: list[Literal["target_missing", "source_changed", "reference_changed", "version_changed"]]
    newer_version_references: list[Id]


class PendingCost(Contract):
    status: Literal["pending_confirmation"] = "pending_confirmation"
    amount: None = None
    currency: None = None


class PlanView(Plan):
    status: Literal["draft", "confirmed_local"]
    current: bool
    impacts: list[TargetImpact]
    cost: PendingCost = Field(default_factory=PendingCost)
    execution_allowed: Literal[False] = False
    execution_blockers: list[Literal["pricing_unconfirmed", "model_and_privacy_unconfirmed", "execution_not_implemented"]]


class PreparedTarget(Contract):
    segment_id: ProjectId
    prompt: str
    duration: int
    first_frame_url: str | None
    last_frame_url: str | None
    reference_image_urls: list[str]


class PreparedPreview(Contract):
    plan_id: Id
    content_hash: Digest
    options: VideoOptions
    targets: list[PreparedTarget]
    execution_allowed: Literal[False] = False
    cost: PendingCost = Field(default_factory=PendingCost)
