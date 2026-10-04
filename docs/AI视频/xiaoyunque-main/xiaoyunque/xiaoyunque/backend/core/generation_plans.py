"""Durable local confirmations and explicit pinned input dependencies."""

import json
import os
import stat
from contextlib import contextmanager
from uuid import uuid4

from pydantic import ValidationError

from api.schemas.assets import Target
from api.schemas.generation_plans import (
    Confirmation, PinnedBinding, Plan, PlanContent, PlannedTarget, PlanStoreDocument,
    PlanView, PreparedPreview, PreparedTarget, Shot, TargetImpact, digest,
)
from core.asset_library import AssetLibraryError, now, unique_fields
from core.generation_inputs import media_urls, prepare_calls, render_prompt
from core.media_archive import sync_directory
from core.persistence import atomic_write_session


def conflict(code="PLAN_SOURCE_CHANGED", message="计划来源已变化，请查看影响后新建并重新确认"):
    return AssetLibraryError(409, code, message)


def find_segment(snapshot, segment_id):
    board = (snapshot or {}).get("artifacts", {}).get("storyboard")
    episodes = board.get("episodes", []) if isinstance(board, dict) else board
    matches = []
    for episode in episodes if isinstance(episodes, list) else []:
        if not isinstance(episode, dict):
            continue
        segments = episode.get("segments", [])
        for index, segment in enumerate(segments if isinstance(segments, list) else [], 1):
            if not isinstance(segment, dict):
                continue
            number = episode.get("episode_number", 0)
            fallback = f"seg_{number:02d}_{index:02d}" if type(number) is int else None
            if segment.get("segment_id", fallback) == segment_id:
                matches.append(segment)
    return matches[0] if len(matches) == 1 else None


class GenerationPlans:
    def __init__(self, engine, library, archive):
        self.engine, self.library, self.archive = engine, library, archive
        self.path = library.directory / "generation-plans.json"

    @contextmanager
    def _transaction(self):
        # One workflow writer; references and plans share the POSIX library lock.
        with self.engine._state_lock:
            with self.library.locked() as library:
                try:
                    if self.path.is_symlink():
                        raise ValueError("Unsafe plan file")
                    try:
                        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    except FileNotFoundError:
                        document = PlanStoreDocument()
                    else:
                        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                                raise ValueError("Plan store must be a regular file")
                            value = json.load(stream, object_pairs_hook=unique_fields)
                        if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
                                or value["schema_version"] != 1 or "plans" not in value):
                            raise ValueError("Unsupported plan document")
                        document = PlanStoreDocument.model_validate(value)
                    yield library, document
                except (OSError, ValueError) as exc:
                    raise AssetLibraryError(503, "PLAN_STORE_UNAVAILABLE", "生成计划暂不可用，请保留原数据并重试") from exc

    def _save(self, document):
        value = document.model_dump(mode="json")
        PlanStoreDocument.model_validate(value)
        atomic_write_session(self.path, value)
        sync_directory(self.library.directory)

    @staticmethod
    def _get(document, plan_id):
        plan = document.plans.get(plan_id)
        if plan is None:
            raise AssetLibraryError(404, "PLAN_NOT_FOUND", "生成计划不存在")
        return plan

    def _view(self, plan, library, snapshot):
        impacts = []
        for target in plan.content.targets:
            reasons, newer = [], []
            segment = find_segment(snapshot, target.segment_id)
            if segment is None:
                reasons.append("target_missing")
            elif digest(segment) != target.source_hash:
                reasons.append("source_changed")
            for binding in target.bindings:
                ref = library.references.get(binding.reference_id)
                if (ref is None or ref.revision != binding.revision or ref.asset_id != binding.asset_id
                        or ref.version_id != binding.version.version_id
                        or ref.model_dump(include={"project_id", "segment_id", "shot_number"}) != binding.target.model_dump()):
                    reasons.append("reference_changed")
                asset = library.assets.get(binding.asset_id)
                version = next((v for v in asset.versions if v.version_id == binding.version.version_id), None) if asset else None
                if version is None or version != binding.version:
                    reasons.append("version_changed")
                elif asset.versions[-1].number > version.number:
                    newer.append(binding.reference_id)
            impacts.append(TargetImpact(segment_id=target.segment_id, reasons=sorted(set(reasons)),
                                        newer_version_references=newer))
        return PlanView(**plan.model_dump(), status="confirmed_local" if plan.confirmation else "draft",
                        current=all(not impact.reasons for impact in impacts), impacts=impacts,
                        execution_blockers=["pricing_unconfirmed", "model_and_privacy_unconfirmed", "execution_not_implemented"])

    def create(self, payload):
        with self._transaction() as (library, document):
            snapshot = self.engine.get_status_snapshot(payload.project_id)
            if snapshot is None:
                raise AssetLibraryError(404, "PLAN_TARGET_NOT_FOUND", "项目或分镜片段不存在")
            targets = []
            for selected in payload.targets:
                segment = find_segment(snapshot, selected.segment_id)
                if segment is None:
                    raise AssetLibraryError(404, "PLAN_TARGET_NOT_FOUND", "项目或分镜片段不存在或不唯一")
                try:
                    shots = [Shot(shot_number=s["shot_number"], duration=s.get("duration", 5), content=s["content"])
                             for s in segment["shots"]]
                except (KeyError, TypeError, ValidationError):
                    raise conflict("PLAN_INVALID_STORYBOARD", "分镜需有效镜头编号、时长和可读内容") from None
                bindings = []
                for chosen in selected.bindings:
                    ref = library.references.get(chosen.reference_id)
                    if ref is None:
                        raise AssetLibraryError(404, "PLAN_REFERENCE_NOT_FOUND", "明确素材引用不存在")
                    asset = library.assets[ref.asset_id]
                    if (chosen.role == "context" and asset.kind not in {"character", "setting", "prop"}
                            or chosen.role != "context" and asset.kind != "image"):
                        raise conflict("PLAN_BINDING_INVALID", "文字上下文与图片输入须选择对应类型资产")
                    version = next(v for v in asset.versions if v.version_id == ref.version_id)
                    if version.media:
                        self.archive.verify_file(version.media)
                    bindings.append(PinnedBinding(reference_id=ref.reference_id, role=chosen.role,
                        revision=ref.revision, target=Target(**ref.model_dump(include={"project_id", "segment_id", "shot_number"})),
                        asset_id=asset.asset_id, kind=asset.kind, version=version))
                try:
                    targets.append(PlannedTarget(segment_id=selected.segment_id, duration=selected.duration,
                        source_hash=digest(segment), shots=shots, bindings=bindings,
                        prompt=render_prompt(payload.options.style, shots, bindings)))
                except ValidationError:
                    raise conflict("PLAN_BINDING_INVALID", "片段分镜或输入绑定无效，请核对") from None
            try:
                content = PlanContent(project_id=payload.project_id, options=payload.options, targets=targets)
            except ValidationError:
                raise conflict("PLAN_BINDING_INVALID", "请核对引用位置、镜头编号、输入角色和图片数量") from None
            plan = Plan(plan_id=uuid4().hex, created_at=now(), content=content,
                        content_hash=digest(content.model_dump(mode="json")))
            document.plans[plan.plan_id] = plan
            self._save(document)
            return self._view(plan, library, snapshot)

    def get(self, plan_id):
        with self._transaction() as (library, document):
            plan = self._get(document, plan_id)
            return self._view(plan, library, self.engine.get_status_snapshot(plan.content.project_id))

    def list(self, project_id):
        with self._transaction() as (library, document):
            snapshot = self.engine.get_status_snapshot(project_id)
            return [self._view(plan, library, snapshot) for plan in document.plans.values()
                    if plan.content.project_id == project_id]

    def confirm(self, plan_id, payload):
        with self._transaction() as (library, document):
            plan = self._get(document, plan_id)
            view = self._view(plan, library, self.engine.get_status_snapshot(plan.content.project_id))
            if payload.content_hash != plan.content_hash:
                raise conflict("PLAN_HASH_MISMATCH", "计划摘要与预览不一致，请重新查看")
            if not view.current:
                raise conflict()
            # Confirmations also fail closed if a pinned image has gone missing.
            prepare_calls(plan.content, self.archive)
            if plan.confirmation is None:
                plan.confirmation = Confirmation(confirmed_at=now(), content_hash=plan.content_hash)
                self._save(document)
            return self._view(plan, library, self.engine.get_status_snapshot(plan.content.project_id))

    def prepare(self, plan_id):
        """Return private calls and a safe public preview, without executing either."""
        with self._transaction() as (library, document):
            plan = self._get(document, plan_id)
            if plan.confirmation is None:
                raise conflict("PLAN_NOT_CONFIRMED", "请先查看计划并确认本地内容")
            if not self._view(plan, library, self.engine.get_status_snapshot(plan.content.project_id)).current:
                raise conflict()
            calls = prepare_calls(plan.content, self.archive)
            targets = []
            for target in plan.content.targets:
                first, last = media_urls(target, "first_frame"), media_urls(target, "last_frame")
                targets.append(PreparedTarget(segment_id=target.segment_id, prompt=target.prompt,
                    duration=target.duration, first_frame_url=first[0] if first else None,
                    last_frame_url=last[0] if last else None,
                    reference_image_urls=media_urls(target, "reference_image")))
            preview = PreparedPreview(plan_id=plan.plan_id, content_hash=plan.content_hash,
                                      options=plan.content.options, targets=targets)
            return calls, preview
