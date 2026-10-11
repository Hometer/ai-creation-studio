"""Deterministic read-only exports of two text stages and their complete history."""
import hashlib
from dataclasses import dataclass
from api.schemas.revision_exports import ExportDocument, ExportSnapshot, ExportVersion
from core.content_revisions import ContentRevisions, STAGES, encoded, digest, fail, comparison_label

MAX_EXPORT_BYTES = 64 * 1024 * 1024
NAMES = {'script_generation': '剧本', 'storyboard': '分镜'}
SOURCES = {'baseline': '首次归档', 'external': '原入口更新归档', 'edit': '手动修改', 'restore': '恢复旧版'}
VERSION_KEYS = ('version_id', 'number', 'stage', 'source', 'created_at', 'content', 'digest', 'changes', 'impacts', 'restored_from')


@dataclass(frozen=True)
class ExportFile:
    body: bytes
    filename: str
    media_type: str
    headers: dict[str, str]


def check_size(size):
    if size > MAX_EXPORT_BYTES:
        fail(422, 'EXPORT_LIMIT', '文字导出超过 64 MiB，请保留原数据并联系开发者处理')


def readable(document):
    """Plain text only, retaining every value and type as JSON at each leaf."""
    parts, size = [], 0

    def add(text):
        nonlocal size
        body = (text + '\n').encode('utf-8')
        size += len(body)
        check_size(size)
        parts.append(body)

    def content(value, path=None):
        path = path or []
        if isinstance(value, dict) and value:
            for key in sorted(value):
                content(value[key], [*path, key])
        elif isinstance(value, list) and value:
            for i, item in enumerate(value):
                content(item, [*path, i])
        else:
            add(comparison_label(path, {}) + '\n' + (value if isinstance(value, str) else encoded(value).decode('utf-8')))

    add('映序 · 文字版本导出')
    add('项目：' + document.project_id)
    add('导出依据：' + document.token)
    add(f'范围：当前剧本、当前分镜与全部 {len(document.versions)} 条已归档文字版本')
    add('仅文字留存；不含未保存草稿、媒体、计划、任务、费用或人工核对记录。没有导入恢复功能。')
    for stage in STAGES:
        item = document.current[stage]
        archived = next((v for v in document.versions if v.version_id == item.version_id), None)
        status = f'已归档 v{archived.number}' if archived else ('未归档变化' if stage in document.untracked_stages else '暂无对应归档版本')
        add('\n===== 当前' + NAMES[stage] + ' · ' + status + ' =====')
        add('内容摘要：' + item.digest)
        if not item.content:
            add('暂无文字（完整结构为 {}）')
        content(item.content)
        add('完整结构（保留附加字段与类型）\n' + encoded(item.content).decode('utf-8'))
    for version in document.versions:
        add(f'\n===== 归档 v{version.number} · {NAMES[version.stage]} · {SOURCES[version.source]} =====')
        add('版本编号：' + version.version_id)
        add('记录时间：' + version.created_at)
        add('内容摘要：' + version.digest)
        if version.restored_from:
            add('恢复自版本编号：' + version.restored_from)
        content(version.content)
        add('完整结构（保留附加字段与类型）\n' + encoded(version.content).decode('utf-8'))
        add('记录的差异与影响（不表示媒体已更新）\n' + encoded({
            'changes': [c.model_dump() for c in version.changes],
            'impacts': [i.model_dump() for i in version.impacts]}).decode('utf-8'))
    return b''.join(parts)


class RevisionExports:
    def __init__(self, engine):
        self.engine = engine

    def download(self, project_id, expected_token, file_format):
        if file_format not in ('json', 'text'):
            fail(422, 'EXPORT_FORMAT', '请选择 JSON 或可读 TXT')
        with self.engine._state_lock:
            _, history, current = ContentRevisions(self.engine)._read(project_id)
            token = digest({'current': current, 'history': history.model_dump()})
            if token != expected_token:
                fail(409, 'REVISION_CHANGED', '文字或历史已变化，请重新读取版本后导出')
            latest = {s: next((v for v in reversed(history.versions) if v.stage == s), None) for s in STAGES}
            snapshots = {s: ExportSnapshot(content=current[s], digest=digest(current[s]),
                version_id=latest[s].version_id if latest[s] and latest[s].digest == digest(current[s]) else None) for s in STAGES}
            versions = []
            for version in history.versions:
                raw = version.model_dump()
                versions.append(ExportVersion(**{k: raw[k] for k in VERSION_KEYS}))
            document = ExportDocument(project_id=project_id, token=token, current=snapshots,
                versions=versions,
                untracked_stages=[s for s in STAGES if latest[s] and latest[s].digest != snapshots[s].digest])
        # All content is detached under the engine lock; serialization cannot observe later writes.
        body = encoded(document.model_dump()) + b'\n' if file_format == 'json' else readable(document)
        check_size(len(body))
        filename = f'text-versions-{project_id}-{token[:12]}.{"json" if file_format == "json" else "txt"}'
        return ExportFile(body, filename, 'application/json' if file_format == 'json' else 'text/plain', {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Revision-Token': token, 'X-Export-Format': file_format,
            'X-Export-Versions': str(len(document.versions)),
            'X-Export-Bytes': str(len(body)),
            'X-Export-SHA256': hashlib.sha256(body).hexdigest()})
