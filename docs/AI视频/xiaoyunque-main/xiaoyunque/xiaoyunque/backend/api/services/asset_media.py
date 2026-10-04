"""Resolve only existing artifact selections, not user-supplied file paths."""

from api.schemas.assets import ArtifactSource, VersionInput
from core.asset_library import AssetLibraryError


def archive_artifact(payload, snapshot, library, archive):
    artifacts = snapshot.get("artifacts", {})
    artifact = artifacts.get(payload.stage)
    items = artifact.get(payload.item_type, []) if isinstance(artifact, dict) else []
    if isinstance(artifact, dict) and not items:
        legacy_keys = {"reference_generation": ("scene_images", "scene_map"),
                       "video_generation": ("video_clips", "video_map")}.get(payload.stage, ())
        items = next((artifact[key] for key in legacy_keys if artifact.get(key)), [])
    if isinstance(items, dict):
        items = [{**value, "id": key} if isinstance(value, dict) else
                 {"id": key, "name": key, "selected": value, "versions": [value]}
                 for key, value in items.items()]
    matches = [item for item in items if isinstance(item, dict) and item.get("id") == payload.item_id] if isinstance(items, list) else []
    if len(matches) != 1:
        raise AssetLibraryError(404, "MEDIA_ITEM_NOT_FOUND", "素材条目不存在或不唯一")
    item = matches[0]
    if payload.candidate_number is None:
        selected = item.get("selected")
    else:
        versions = item.get("versions", [])
        if not isinstance(versions, list) or payload.candidate_number > len(versions):
            raise AssetLibraryError(422, "MEDIA_CANDIDATE_INVALID", "候选序号不在既有素材范围内")
        selected = versions[payload.candidate_number - 1]
    kind = "video" if payload.stage == "video_generation" else "image"
    try:
        name = item.get("name") or item.get("title") or payload.item_id
        definition = item.get("description") or item.get("desc") or "项目已保存素材"
        version = VersionInput(name=name, definition=definition)
    except ValueError as exc:
        raise AssetLibraryError(422, "INVALID_MEDIA_ITEM", "素材名称或描述不符合资产要求") from exc
    source = ArtifactSource(**payload.model_dump(exclude={"candidate_number"}))
    path = archive.resolve_source(selected, payload.project_id, kind)
    with archive.prepare(path, kind) as (temporary, details):
        return library.import_media(source, kind, version, archive, temporary, details)
