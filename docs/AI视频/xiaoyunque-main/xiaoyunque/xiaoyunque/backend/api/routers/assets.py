"""Preparation APIs which never invoke or alter the generation workflow."""

import json
from functools import lru_cache
from pathlib import Path
from pydantic import ValidationError

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from api.dependencies import get_workflow_engine
from api.errors import ErrorResponse
from api.schemas.assets import (
    Asset, AssetInput, Id, MediaImport, ProjectId, ReferenceInput, ReferenceUpdate,
    ReferenceView, ScriptImport, Target, UploadInput, UploadSource, VersionInput,
)
from core.asset_library import AssetLibrary, AssetLibraryError, not_found
from core.media_archive import EXTENSIONS, MediaArchive
from api.services.asset_media import archive_artifact
from api.services.asset_upload import archive_upload
from config import settings

router = APIRouter(tags=["资产与版本"], responses={503: {"model": ErrorResponse}})


@lru_cache
def get_asset_library() -> AssetLibrary:
    return AssetLibrary(Path(__file__).resolve().parents[2] / "data" / "asset-library")


def get_media_archive(library=Depends(get_asset_library)) -> MediaArchive:
    return MediaArchive(library.directory, settings.RESULT_DIR, settings.BASE_DIR)


def require_target(engine, target: Target):
    snapshot = engine.get_status_snapshot(target.project_id)
    if snapshot is None:
        raise AssetLibraryError(404, "REFERENCE_TARGET_NOT_FOUND", "项目或分镜位置不存在")
    if target.segment_id is None:
        return snapshot
    board = snapshot.get("artifacts", {}).get("storyboard")
    episodes = board.get("episodes", []) if isinstance(board, dict) else board
    matches = []
    for episode in episodes if isinstance(episodes, list) else []:
        if not isinstance(episode, dict):
            continue
        segments = episode.get("segments", [])
        for index, segment in enumerate(segments if isinstance(segments, list) else [], 1):
            if not isinstance(segment, dict):
                continue
            # Match the legacy fallback in the existing storyboard synchronizer.
            ep = episode.get("episode_number", 0)
            fallback = f"seg_{ep:02d}_{index:02d}" if type(ep) is int else None
            if segment.get("segment_id", fallback) == target.segment_id:
                matches.append(segment)
    if len(matches) != 1:
        raise AssetLibraryError(404, "REFERENCE_TARGET_NOT_FOUND", "项目或分镜位置不存在或不唯一")
    if target.shot_number is not None:
        shots = matches[0].get("shots", [])
        matches = [shot for shot in shots if isinstance(shot, dict)
                   and type(shot.get("shot_number")) is int and shot["shot_number"] == target.shot_number] if isinstance(shots, list) else []
        if len(matches) != 1:
            raise AssetLibraryError(404, "REFERENCE_TARGET_NOT_FOUND", "镜头不存在或不唯一")
    return snapshot


def view(ref, data, engine) -> ReferenceView:
    try:
        require_target(engine, ref)
        exists = True
    except AssetLibraryError:
        exists = False
    asset = data.assets[ref.asset_id]
    number = next(v.number for v in asset.versions if v.version_id == ref.version_id)
    return ReferenceView(**ref.model_dump(), has_newer_version=number < asset.versions[-1].number,
                         target_exists=exists)


@router.post("/api/assets", response_model=Asset, status_code=201)
def create_asset(payload: AssetInput, library=Depends(get_asset_library)):
    return library.create(payload)


@router.get("/api/assets", response_model=list[Asset])
def list_assets(library=Depends(get_asset_library)):
    return list(library.read().assets.values())


@router.post("/api/assets/import-script", response_model=Asset)
def import_script(payload: ScriptImport, library=Depends(get_asset_library), engine=Depends(get_workflow_engine)):
    snapshot = require_target(engine, Target(project_id=payload.project_id))
    script = snapshot.get("artifacts", {}).get("script_generation")
    key = "characters" if payload.kind == "character" else "settings"
    items = script.get(key, []) if isinstance(script, dict) else []
    matches = [item for item in items if isinstance(item, dict) and item.get("id") == payload.item_id] if isinstance(items, list) else []
    if len(matches) != 1:
        raise AssetLibraryError(404, "SCRIPT_ITEM_NOT_FOUND", "脚本条目不存在或不唯一")
    try:
        version = VersionInput(name=matches[0].get("name"), definition=json.dumps(
            matches[0], ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise AssetLibraryError(422, "INVALID_SCRIPT_ITEM", "脚本条目格式或长度不符合文字资产要求") from exc
    return library.import_script(payload.project_id, payload.kind, payload.item_id, version)


@router.get("/api/assets/{asset_id}", response_model=Asset)
def get_asset(asset_id: Id, library=Depends(get_asset_library)):
    return library.get_asset(library.read(), asset_id)


@router.post("/api/assets/import-media", response_model=Asset)
def import_media(payload: MediaImport, library=Depends(get_asset_library),
                 archive=Depends(get_media_archive), engine=Depends(get_workflow_engine)):
    snapshot = require_target(engine, Target(project_id=payload.project_id))
    return archive_artifact(payload, snapshot, library, archive)


@router.post("/api/project/{project_id}/assets/upload", response_model=Asset, status_code=201,
             responses={status: {"model": ErrorResponse} for status in (404, 409, 413, 415, 422, 503)})
def upload_project_asset(project_id: ProjectId, upload_id: str = Form(..., pattern=r"^[a-f0-9]{32}$"),
                         name: str = Form(..., min_length=1, max_length=100), definition: str = Form(..., min_length=1, max_length=10000),
                         file: UploadFile = File(...), asset_id: Id | None = Form(default=None),
                         library=Depends(get_asset_library), archive=Depends(get_media_archive),
                         engine=Depends(get_workflow_engine)):
    require_target(engine, Target(project_id=project_id))
    try:
        payload = UploadInput(name=name, definition=definition, upload_id=upload_id, asset_id=asset_id)
    except ValidationError as exc:
        raise AssetLibraryError(422, "VALIDATION_ERROR", "上传参数不符合要求") from exc
    return archive_upload(file, UploadSource(project_id=project_id, upload_id=payload.upload_id),
                          payload, library, archive, payload.asset_id)


@router.get("/api/assets/{asset_id}/versions/{version_id}/media", response_class=FileResponse,
            responses={200: {"description": "已验证的归档媒体", "content": {
                mime: {"schema": {"type": "string", "format": "binary"}} for mime in EXTENSIONS}}})
def read_media(asset_id: Id, version_id: Id, library=Depends(get_asset_library), archive=Depends(get_media_archive)):
    asset = library.get_asset(library.read(), asset_id)
    version = next((v for v in asset.versions if v.version_id == version_id), None)
    if version is None or version.media is None:
        raise not_found()
    media = version.media
    path = archive.verify_file(media)
    return FileResponse(path, media_type=media.mime_type,
                        filename=asset_id + "-" + version_id + EXTENSIONS[media.mime_type],
                        content_disposition_type="inline", headers={
                            "ETag": '"' + media.sha256 + '"', "X-Content-Type-Options": "nosniff",
                            "Cache-Control": "private, no-cache",
                        })


@router.post("/api/assets/{asset_id}/versions", response_model=Asset, status_code=201)
def add_version(asset_id: Id, payload: VersionInput, library=Depends(get_asset_library)):
    return library.add_version(asset_id, payload)


@router.post("/api/asset-references", response_model=ReferenceView, status_code=201)
def create_reference(payload: ReferenceInput, library=Depends(get_asset_library), engine=Depends(get_workflow_engine)):
    require_target(engine, payload)
    ref = library.create_reference(payload)
    return view(ref, library.read(), engine)


@router.patch("/api/asset-references/{reference_id}", response_model=ReferenceView)
def update_reference(reference_id: Id, payload: ReferenceUpdate, library=Depends(get_asset_library), engine=Depends(get_workflow_engine)):
    ref = library.read().references.get(reference_id)
    if ref is None:
        raise not_found()
    require_target(engine, ref)
    ref = library.update_reference(reference_id, payload)
    return view(ref, library.read(), engine)


@router.get("/api/project/{project_id}/asset-references", response_model=list[ReferenceView])
def project_references(project_id: ProjectId, library=Depends(get_asset_library), engine=Depends(get_workflow_engine)):
    # Retain historical bindings even if the original project was removed.
    data = library.read()
    return [view(ref, data, engine) for ref in data.references.values() if ref.project_id == project_id]


@router.get("/api/assets/{asset_id}/usage", response_model=list[ReferenceView])
def usage(asset_id: Id, library=Depends(get_asset_library), engine=Depends(get_workflow_engine)):
    data = library.read()
    library.get_asset(data, asset_id)
    return [view(ref, data, engine) for ref in data.references.values() if ref.asset_id == asset_id]
