"""Append-only versions, atomic writes and POSIX process locks; no migration."""

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from api.schemas.assets import (
    ArtifactSource, Asset, AssetInput, AssetVersion, Library, MediaInfo, Reference, ReferenceInput,
    ReferenceUpdate, Selection, Source, VersionInput,
)
from core.persistence import atomic_write_session


class AssetLibraryError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def not_found():
    return AssetLibraryError(404, "ASSET_NOT_FOUND", "资产、版本或引用不存在")


def now():
    return datetime.now(timezone.utc).isoformat()


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


class AssetLibrary:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.path = self.directory / "library.json"

    @contextmanager
    def _locked(self):
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.directory.is_symlink():
                raise ValueError("Unsafe library directory")
            descriptor = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    if self.path.is_symlink():
                        raise ValueError("Unsafe library file")
                    try:
                        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                    except FileNotFoundError:
                        library = Library()
                    else:
                        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                            document = json.load(stream, object_pairs_hook=unique_fields)
                            if (not isinstance(document, dict)
                                    or type(document.get("schema_version")) is not int
                                    or document["schema_version"] != 1
                                    or not {"assets", "references"}.issubset(document)):
                                raise ValueError("Unsupported library document")
                            library = Library.model_validate(document)
                    yield library
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        except (OSError, ValueError) as exc:
            raise AssetLibraryError(503, "ASSET_STORE_UNAVAILABLE", "资产库暂不可用，请保留原数据并重试") from exc

    def read(self) -> Library:
        with self._locked() as library:
            return library

    def locked(self):
        """Shared local transaction boundary for references and generation plans."""
        return self._locked()

    def _change(self, operation):
        with self._locked() as library:
            before = library.model_dump(mode="json")
            result = operation(library)
            after = library.model_dump(mode="json")
            Library.model_validate(after)
            if after != before:
                atomic_write_session(self.path, after)
            return result

    @staticmethod
    def _version(payload: VersionInput, number: int, source: Source) -> AssetVersion:
        return AssetVersion(**payload.model_dump(include={"name", "definition"}),
                            version_id=uuid4().hex, number=number, created_at=now(), source=source)

    @staticmethod
    def get_asset(library: Library, asset_id: str) -> Asset:
        asset = library.assets.get(asset_id)
        if asset is None:
            raise not_found()
        return asset

    @staticmethod
    def _check_version(asset: Asset, version_id: str):
        if not any(v.version_id == version_id for v in asset.versions):
            raise not_found()

    def create(self, payload: AssetInput) -> Asset:
        def operation(library):
            asset = Asset(asset_id=uuid4().hex, kind=payload.kind,
                          versions=[self._version(payload, 1, Source())])
            library.assets[asset.asset_id] = asset
            return asset
        return self._change(operation)

    def add_version(self, asset_id: str, payload: VersionInput) -> Asset:
        def operation(library):
            asset = self.get_asset(library, asset_id)
            if asset.kind in {"image", "video"}:
                raise AssetLibraryError(409, "MEDIA_VERSION_REQUIRED", "媒体版本须通过项目素材归档添加")
            asset.versions.append(self._version(payload, len(asset.versions) + 1, Source()))
            return asset
        return self._change(operation)

    def import_media(self, source: ArtifactSource, kind: str, payload: VersionInput,
                     archive, temporary, details) -> Asset:
        from core.media_archive import sync_directory

        key = hashlib.sha256(json.dumps(["media", source.project_id, source.stage,
                                        source.item_type, source.item_id]).encode()).hexdigest()
        published, file_id = None, None

        def operation(library):
            nonlocal published, file_id
            asset = next((a for a in library.assets.values() if a.import_key == key), None)
            if asset is not None:
                if asset.kind != kind:
                    raise AssetLibraryError(409, "MEDIA_KIND_CONFLICT", "归档来源类型已变化，请核对原素材")
                latest = asset.versions[-1]
                if (latest.name == payload.name and latest.definition == payload.definition
                        and latest.media is not None
                        and latest.media.sha256 == details["sha256"]
                        and latest.media.byte_size == details["byte_size"]):
                    archive.verify_file(latest.media)
                    return asset
            version = self._version(payload, len(asset.versions) + 1 if asset else 1, source)
            file_id = version.version_id
            version.media = MediaInfo(file_id=file_id, **details)
            if asset is None:
                asset = Asset(asset_id=uuid4().hex, kind=kind, import_key=key, versions=[version])
                library.assets[asset.asset_id] = asset
            else:
                asset.versions.append(version)
            published = archive.publish(temporary, version.media)
            return asset

        try:
            result = self._change(operation)
            sync_directory(self.directory)
            return result
        except Exception as exc:
            # A failed write may have raised after replacement. Never remove a
            # committed file, and retain it when commit status cannot be read.
            if published is not None:
                try:
                    data = self.read()
                except AssetLibraryError:
                    pass
                else:
                    committed = any(v.media and v.media.file_id == file_id
                                    for a in data.assets.values() for v in a.versions)
                    if not committed:
                        published.unlink(missing_ok=True)
            if isinstance(exc, OSError):
                raise AssetLibraryError(503, "MEDIA_STORE_UNAVAILABLE", "媒体归档暂不可用，请保留数据并重试") from exc
            raise

    def import_script(self, project_id: str, kind: str, item_id: str, payload: VersionInput) -> Asset:
        key = hashlib.sha256(json.dumps([project_id, kind, item_id]).encode()).hexdigest()
        source = Source(type="script", project_id=project_id, item_id=item_id, stage="script_generation")

        def operation(library):
            asset = next((a for a in library.assets.values() if a.import_key == key), None)
            if asset is None:
                asset = Asset(asset_id=uuid4().hex, kind=kind, import_key=key,
                              versions=[self._version(payload, 1, source)])
                library.assets[asset.asset_id] = asset
            else:
                latest = asset.versions[-1]
                if (latest.name, latest.definition, latest.source) != (payload.name, payload.definition, source):
                    asset.versions.append(self._version(payload, len(asset.versions) + 1, source))
            return asset
        return self._change(operation)

    def create_reference(self, payload: ReferenceInput) -> Reference:
        def operation(library):
            self._check_version(self.get_asset(library, payload.asset_id), payload.version_id)
            target = (payload.asset_id, payload.project_id, payload.segment_id, payload.shot_number)
            if any((r.asset_id, r.project_id, r.segment_id, r.shot_number) == target
                   for r in library.references.values()):
                raise AssetLibraryError(409, "REFERENCE_EXISTS", "该位置已引用此资产，请显式更新现有引用")
            ref = Reference(**payload.model_dump(), reference_id=uuid4().hex, revision=1,
                            history=[Selection(version_id=payload.version_id, selected_at=now())])
            library.references[ref.reference_id] = ref
            return ref
        return self._change(operation)

    def update_reference(self, reference_id: str, payload: ReferenceUpdate) -> Reference:
        def operation(library):
            ref = library.references.get(reference_id)
            if ref is None:
                raise not_found()
            if ref.revision != payload.expected_revision:
                raise AssetLibraryError(409, "REFERENCE_CONFLICT", "引用已变化，请重新查看后确认")
            self._check_version(self.get_asset(library, ref.asset_id), payload.version_id)
            if ref.version_id != payload.version_id:
                ref.version_id = payload.version_id
                ref.revision += 1
                ref.history.append(Selection(version_id=payload.version_id, selected_at=now()))
            return ref
        return self._change(operation)
