"""Compile Wan inputs locally; no transport, SDK, upload, or paid execution."""

import base64
import hashlib
import re
from urllib.parse import urlsplit
from uuid import uuid4

from PIL import Image

from api.schemas.generation_plans import digest
from api.schemas.model_preflight import (
    MODELS, ConnectionCheck, ImageCheck, Issue, PreflightDocument, PreflightReport,
    ReportView, TargetCheck, WanInput, WanMedia, WanParameters, WanRequest,
)
from core.asset_library import AssetLibraryError, now
from core.media_archive import sync_directory
from core.persistence import atomic_write_session
from core.task_provider import read_document

MESSAGES = {
    "MODEL_NOT_ADAPTED": "本轮仅检查万相 2.7 图生视频，当前模型尚未适配",
    "MODE_NOT_ADAPTED": "本轮仅检查首帧与首尾帧，参考生视频协议待适配",
    "DURATION_UNSUPPORTED": "万相图生视频时长须为 2—15 整数秒",
    "RESOLUTION_UNSUPPORTED": "本轮协议仅支持 720P 或 1080P",
    "PROMPT_TOO_LONG": "固定提示词超过 5000 字符，请修订计划；不会静默截断",
    "SOUND_OFF_UNSUPPORTED": "该协议未提供关闭声音的参数，不能保证无声输出",
    "MEDIA_UNAVAILABLE": "归档素材缺失、损坏或无法读取，请保留记录并核对原版本",
    "IMAGE_DIMENSIONS_UNSUPPORTED": "图像宽和高分别须为 240—8000 像素",
    "IMAGE_RATIO_UNSUPPORTED": "图像宽高比须在 1:8 至 8:1 之间",
    "PNG_ALPHA_UNSUPPORTED": "PNG 含透明通道，请另建符合规格的版本并显式选择",
    "SHOT_TYPE_NOT_SENT": "镜头类型不透传为平台参数，镜头意图取自固定提示词，效果待验",
    "RATIO_DERIVED_FROM_MEDIA": "输出比例依输入图处理，不能保证计划比例精确生效",
    "PLAN_NOT_CONFIRMED": "计划尚未确认本地内容，不编译请求",
    "PLAN_SOURCE_CHANGED": "计划来源已变化，请新建并确认计划",
    "KEY_MISSING": "后端尚未配置万相 Key；不要在聊天或仓库中填写密钥",
    "ENDPOINT_UNSUPPORTED": "后端 Endpoint 未匹配本轮官方 HTTPS 地址规则",
    "NON_MAINLAND_ENDPOINT": "Endpoint 属于新加坡，不符合本轮境内处理规则",
    "INPUT_PROTOCOL_INVALID": "部分片段的输入未通过本地协议检查",
    "REAL_PRICING_UNCONFIGURED": "真实报价和单次、日／月额度尚未配置，SIM 不能代替真实预算",
    "REAL_EXECUTION_DISABLED": "本轮仅本地预检，真实执行保持关闭",
    "REAL_ADAPTER_NOT_IMPLEMENTED": "真实提交、查询、下载和原生超时适配尚未实现",
    "AUTHENTICATION_UNVERIFIED": "Key 存在性不代表认证或地域匹配，真实认证待验",
    "SAMPLE_REVIEW_REQUIRED": "总体隐私规则已定，实际样例与素材权利仍须逐次核对",
}


def issue(code, warning=False):
    return Issue(code=code, message=MESSAGES[code], severity="warning" if warning else "error")


def check_connection(api_key, base_url):
    """Do not retain the credential, original URL, hostname, or workspace ID."""
    region = "unrecognized"
    try:
        clean = base_url if isinstance(base_url, str) and not any(ord(c) <= 32 or ord(c) == 127 for c in base_url) else ""
        parsed = urlsplit(clean)
        if (parsed.scheme == "https" and parsed.username is None and parsed.password is None
                and parsed.port is None and parsed.path.rstrip("/") == "/api/v1"
                and not parsed.query and not parsed.fragment):
            host = parsed.hostname or ""
            if host == "dashscope.aliyuncs.com" or re.fullmatch(r"[a-z0-9][a-z0-9-]*\.cn-beijing\.maas\.aliyuncs\.com", host):
                region = "beijing"
            elif host == "dashscope-intl.aliyuncs.com" or re.fullmatch(r"[a-z0-9][a-z0-9-]*\.ap-southeast-1\.maas\.aliyuncs\.com", host):
                region = "singapore"
    except ValueError:
        pass
    return ConnectionCheck(credential_present=isinstance(api_key, str) and bool(api_key.strip()),
                           endpoint_region=region, endpoint_allowed=region == "beijing")


def inspect_target(content, target, archive, compile_request):
    """Private request is short lived; the report never serializes its content."""
    options, issues, images, media = content.options, [], [], []
    if options.model not in MODELS: issues.append(issue("MODEL_NOT_ADAPTED"))
    if options.mode not in {"first_frame", "start_end_frame"}: issues.append(issue("MODE_NOT_ADAPTED"))
    if not 2 <= target.duration <= 15: issues.append(issue("DURATION_UNSUPPORTED"))
    if options.resolution not in {"720P", "1080P"}: issues.append(issue("RESOLUTION_UNSUPPORTED"))
    if len(target.prompt) > 5000: issues.append(issue("PROMPT_TOO_LONG"))
    if options.sound == "off": issues.append(issue("SOUND_OFF_UNSUPPORTED"))
    issues.append(issue("SHOT_TYPE_NOT_SENT", warning=True))
    for binding in target.bindings:
        if binding.role == "context": continue
        details = binding.version.media
        images.append(ImageCheck(reference_id=binding.reference_id, asset_id=binding.asset_id,
            version_id=binding.version.version_id, role=binding.role, width=details.width,
            height=details.height, byte_size=details.byte_size, sha256=details.sha256))
        try:
            path = archive.verify_file(details)
            with Image.open(path) as image:
                width, height = image.size
                if (width, height) != (details.width, details.height):
                    raise ValueError("Image metadata mismatch")
                if not (240 <= width <= 8000 and 240 <= height <= 8000):
                    issues.append(issue("IMAGE_DIMENSIONS_UNSUPPORTED"))
                if not (width <= 8 * height and height <= 8 * width):
                    issues.append(issue("IMAGE_RATIO_UNSUPPORTED"))
                if image.format == "PNG" and ("A" in image.getbands() or "transparency" in image.info):
                    issues.append(issue("PNG_ALPHA_UNSUPPORTED"))
            if binding.role == "first_frame":
                ratio_width, ratio_height = map(int, options.video_ratio.split(":"))
                if width * ratio_height != height * ratio_width:
                    issues.append(issue("RATIO_DERIVED_FROM_MEDIA", warning=True))
            if binding.role in {"first_frame", "last_frame"}:
                media.append((binding.role, path, details.mime_type))
        except (AssetLibraryError, OSError, ValueError, Image.DecompressionBombError):
            issues.append(issue("MEDIA_UNAVAILABLE"))
    ready = not any(i.severity == "error" for i in issues)
    request = None
    if ready and compile_request:
        try:
            # Reverify the archived bytes before reading them for a data URI.
            payload = []
            for role in ("first_frame", "last_frame"):
                for media_role, path, mime in media:
                    if role == media_role:
                        details = next(b.version.media for b in target.bindings if b.role == role)
                        archive.verify_file(details)
                        raw = path.read_bytes()
                        if len(raw) != details.byte_size or hashlib.sha256(raw).hexdigest() != details.sha256:
                            raise ValueError("Archived bytes changed while compiling")
                        encoded = base64.b64encode(raw).decode("ascii")
                        payload.append(WanMedia(type=role, url=f"data:{mime};base64,{encoded}"))
            request = WanRequest(model=options.model, input=WanInput(prompt=target.prompt, media=payload),
                parameters=WanParameters(duration=target.duration, resolution=options.resolution))
        except (AssetLibraryError, OSError, ValueError):
            issues.append(issue("MEDIA_UNAVAILABLE"))
            ready = False
    # Deduplicate identical issues from two image inputs.
    issues = list({(i.code, i.severity): i for i in issues}.values())
    check = TargetCheck(segment_id=target.segment_id, model=options.model, duration=target.duration,
        prompt_characters=len(target.prompt), input_protocol_ready=ready,
        request_sha256=digest(request.model_dump(mode="json")) if request else None,
        images=images, issues=issues)
    return check, request


class ModelPreflight:
    def __init__(self, plans, connection):
        self.plans, self.connection = plans, connection
        self.path = plans.library.directory / "model-preflight.json"

    def _read(self):
        try:
            return read_document(self.path, PreflightDocument)
        except (OSError, ValueError) as exc:
            raise AssetLibraryError(503, "PREFLIGHT_STORE_UNAVAILABLE", "预检记录暂不可用，请保留原文件并重试") from exc

    def _save(self, document):
        try:
            value = document.model_dump(mode="json")
            PreflightDocument.model_validate(value)
            atomic_write_session(self.path, value)
            sync_directory(self.path.parent)
        except (OSError, ValueError) as exc:
            raise AssetLibraryError(503, "PREFLIGHT_STORE_UNAVAILABLE", "预检记录暂不可用，请保留原文件并重试") from exc

    def _view(self, report, library, plans):
        plan = plans.plans.get(report.plan_id)
        current = False
        if plan and plan.content_hash == report.content_hash:
            view = self.plans._view(plan, library, self.plans.engine.get_status_snapshot(plan.content.project_id))
            current = (view.current == report.plan_current_at_check
                       and bool(plan.confirmation) == report.plan_confirmed_at_check
                       and self.connection == report.connection)
        return ReportView(**report.model_dump(), current=current)

    def create(self, plan_id):
        with self.plans._transaction() as (library, plans):
            document = self._read()
            plan = self.plans._get(plans, plan_id)
            view = self.plans._view(plan, library, self.plans.engine.get_status_snapshot(plan.content.project_id))
            confirmed = plan.confirmation is not None
            checks = [inspect_target(plan.content, target, self.plans.archive, confirmed and view.current)[0]
                      for target in plan.content.targets]
            ready = confirmed and view.current and all(t.input_protocol_ready for t in checks)
            blockers = []
            if not confirmed: blockers.append(issue("PLAN_NOT_CONFIRMED"))
            if not view.current: blockers.append(issue("PLAN_SOURCE_CHANGED"))
            if not self.connection.credential_present: blockers.append(issue("KEY_MISSING"))
            if self.connection.endpoint_region == "unrecognized": blockers.append(issue("ENDPOINT_UNSUPPORTED"))
            elif not self.connection.endpoint_allowed: blockers.append(issue("NON_MAINLAND_ENDPOINT"))
            if not all(t.input_protocol_ready for t in checks): blockers.append(issue("INPUT_PROTOCOL_INVALID"))
            blockers.extend(issue(code) for code in ("REAL_PRICING_UNCONFIGURED", "REAL_EXECUTION_DISABLED",
                "REAL_ADAPTER_NOT_IMPLEMENTED", "AUTHENTICATION_UNVERIFIED", "SAMPLE_REVIEW_REQUIRED"))
            report = PreflightReport(report_id=uuid4().hex, plan_id=plan.plan_id, project_id=plan.content.project_id,
                content_hash=plan.content_hash, checked_at=now(), plan_confirmed_at_check=confirmed,
                plan_current_at_check=view.current, connection=self.connection, targets=checks,
                input_protocol_ready=ready, blockers=blockers)
            document.reports[report.report_id] = report
            self._save(document)
            return self._view(report, library, plans)

    def get(self, report_id):
        with self.plans._transaction() as (library, plans):
            report = self._read().reports.get(report_id)
            if report is None:
                raise AssetLibraryError(404, "PREFLIGHT_NOT_FOUND", "预检报告不存在")
            return self._view(report, library, plans)
