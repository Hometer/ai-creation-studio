"""Template v1 and local VideoClient arguments; no SDK import or invocation."""

from dataclasses import dataclass

from api.schemas.generation_plans import PlanContent, PlannedTarget, Shot


def render_prompt(style: str, shots: list[Shot], bindings) -> str:
    context = [f"- {b.version.name}：{b.version.definition}" for b in bindings if b.role == "context"]
    lines = [f"风格控制：{style}", "明确引用的文字定义：", *(context or ["无"]), "分镜列表："]
    lines.extend(f"分镜{s.shot_number}：[{s.duration:g}秒] {s.content}" for s in shots)
    lines.append("不要生成字幕或水印")
    return "\n".join(lines)


@dataclass(frozen=True)
class PreparedCall:
    segment_id: str
    arguments: dict


def prepare_calls(content: PlanContent, archive) -> list[PreparedCall]:
    """Private paths must never be serialized to the public preview."""
    calls = []
    for target in content.targets:
        images = {b.reference_id: str(archive.verify_file(b.version.media))
                  for b in target.bindings if b.version.media is not None}
        first = next((images[b.reference_id] for b in target.bindings if b.role == "first_frame"), None)
        last = next((images[b.reference_id] for b in target.bindings if b.role == "last_frame"), None)
        references = [images[b.reference_id] for b in target.bindings if b.role == "reference_image"]
        options = content.options
        calls.append(PreparedCall(target.segment_id, {
            "prompt": target.prompt, "image_path": first, "last_image_path": last,
            "reference_image_paths": references or None, "model": options.model,
            "duration": target.duration, "sound": options.sound, "shot_type": options.shot_type,
            "video_ratio": options.video_ratio, "resolution": options.resolution,
        }))
    return calls


def media_urls(target: PlannedTarget, role: str) -> list[str]:
    return [f"/api/assets/{b.asset_id}/versions/{b.version.version_id}/media"
            for b in target.bindings if b.role == role]
