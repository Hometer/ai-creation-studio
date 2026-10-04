"""Strict contracts for local text assets; no media or model calls."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Id = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
ProjectId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Definition = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
Kind = Literal["character", "setting", "prop"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VersionInput(Contract):
    name: Name
    definition: Definition


class AssetInput(VersionInput):
    kind: Kind


class ScriptImport(Contract):
    project_id: ProjectId
    kind: Literal["character", "setting"]
    item_id: ProjectId


class Source(Contract):
    type: Literal["manual", "script"] = "manual"
    project_id: ProjectId | None = None
    item_id: ProjectId | None = None
    stage: Literal["script_generation"] | None = None

    @model_validator(mode="after")
    def consistent_source(self):
        values = (self.project_id, self.item_id, self.stage)
        complete, empty = all(v is not None for v in values), all(v is None for v in values)
        if not (complete if self.type == "script" else empty):
            raise ValueError("Invalid source")
        return self


class AssetVersion(VersionInput):
    version_id: Id
    number: int = Field(ge=1)
    created_at: str
    source: Source


class Asset(Contract):
    asset_id: Id
    kind: Kind
    versions: list[AssetVersion] = Field(min_length=1)
    import_key: str | None = None


class Target(Contract):
    project_id: ProjectId
    segment_id: ProjectId | None = None
    shot_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def shot_requires_segment(self):
        if self.shot_number is not None and self.segment_id is None:
            raise ValueError("A shot requires a segment")
        return self


class ReferenceInput(Target):
    asset_id: Id
    version_id: Id


class ReferenceUpdate(Contract):
    version_id: Id
    expected_revision: int = Field(ge=1)


class Selection(Contract):
    version_id: Id
    selected_at: str


class Reference(ReferenceInput):
    reference_id: Id
    revision: int = Field(ge=1)
    history: list[Selection] = Field(min_length=1)


class ReferenceView(Reference):
    has_newer_version: bool
    target_exists: bool


class Library(Contract):
    schema_version: Literal[1] = 1
    assets: dict[Id, Asset] = Field(default_factory=dict)
    references: dict[Id, Reference] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_graph(self):
        version_ids, import_keys, bindings = set(), set(), set()
        for key, asset in self.assets.items():
            if key != asset.asset_id:
                raise ValueError("Asset ID mismatch")
            if asset.import_key is not None:
                if asset.import_key in import_keys:
                    raise ValueError("Duplicate import source")
                import_keys.add(asset.import_key)
            for number, version in enumerate(asset.versions, 1):
                if version.number != number or version.version_id in version_ids:
                    raise ValueError("Invalid version sequence")
                version_ids.add(version.version_id)
        for key, ref in self.references.items():
            asset = self.assets.get(ref.asset_id)
            if key != ref.reference_id or asset is None:
                raise ValueError("Invalid reference")
            valid_versions = {v.version_id for v in asset.versions}
            if (ref.version_id not in valid_versions or len(ref.history) != ref.revision
                    or ref.history[-1].version_id != ref.version_id
                    or any(item.version_id not in valid_versions for item in ref.history)):
                raise ValueError("Invalid reference history")
            binding = (ref.asset_id, ref.project_id, ref.segment_id, ref.shot_number)
            if binding in bindings:
                raise ValueError("Duplicate reference target")
            bindings.add(binding)
        return self
