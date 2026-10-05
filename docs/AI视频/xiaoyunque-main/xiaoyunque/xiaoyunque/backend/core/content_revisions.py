"""Atomic, optimistic, single-process local text revisions inside session files."""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pydantic import ValidationError
from core.asset_library import AssetLibraryError
from core.persistence import read_session, session_path
from api.schemas.content_revisions import Editor, History, Revision, Preview, Workspace, Saved, RevisionInput

STAGES = ('script_generation', 'storyboard')
MAX_CONTENT = 2 * 1024 * 1024
MAX_HISTORY = 32 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def fail(status, code, message):
    raise AssetLibraryError(status, code, message)


def fields(stage, content):
    result = []
    def add(path, label, obj, key):
        value = obj.get(key)
        if isinstance(value, str):
            result.append({'path': [*path, key], 'label': ('旧结构 · ' if path and path[0] == 'payload' else '') + label, 'value': value})
    def script(obj, prefix):
        for key, label in (('title', '剧本标题'), ('logline', '故事梗概'), ('beat_sheet', '节拍大纲')):
            add(prefix, label, obj, key)
        for key, field, label in (('episodes', 'content', '章节正文'), ('scenes', 'plot', '场次剧情')):
            for i, item in enumerate(obj.get(key, []) if isinstance(obj.get(key), list) else []):
                if isinstance(item, dict):
                    add([*prefix, key, i], f'{label} {i + 1}', item, field)
    def storyboard(obj, prefix):
        def segments(items, path):
            for i, seg in enumerate(items if isinstance(items, list) else []):
                if not isinstance(seg, dict):
                    continue
                sid = str(seg.get('segment_id', f'片段 {i + 1}'))
                base = [*path, i]
                add(base, f'{sid} · 画面提示词', seg, 'visual_prompt')
                for j, shot in enumerate(seg.get('shots', []) if isinstance(seg.get('shots'), list) else []):
                    if isinstance(shot, dict):
                        for key, label in (('content', '镜头内容'), ('plot', '镜头剧情'), ('dialogue', '台词')):
                            add([*base, 'shots', j], f'{sid} · 镜头 {shot.get("shot_number", j+1)} · {label}', shot, key)
        segments(obj.get('segments'), [*prefix, 'segments'])
        for i, ep in enumerate(obj.get('episodes', []) if isinstance(obj.get('episodes'), list) else []):
            if isinstance(ep, dict):
                segments(ep.get('segments'), [*prefix, 'episodes', i, 'segments'])
    visit = script if stage == 'script_generation' else storyboard
    visit(content, [])
    if isinstance(content.get('payload'), dict):
        visit(content['payload'], ['payload'])
    if len(result) > 500:
        fail(422, 'CONTENT_LIMIT', '可编辑字段超过本轮支持上限，请保留原文并拆分项目')
    return result


def differences(before, after, path=None):
    path = path or []
    if encoded(before) == encoded(after):
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                render = lambda value: value if isinstance(value,str) else encoded(value).decode()
                changes.append({'path':[*path,key], 'before':render(before.get(key)), 'after':render(after.get(key)),
                                'before_present':key in before, 'after_present':key in after})
            else:
                changes.extend(differences(before[key],after[key],[*path,key]))
        return changes
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [change for i, (a, b) in enumerate(zip(before, after)) for change in differences(a, b, [*path, i])]
    render = lambda value: value if isinstance(value, str) else encoded(value).decode()
    return [{'path': path, 'before': render(before), 'after': render(after)}]


def impact(stage, changes, storyboard):
    affected = []
    def intersects(a, b):
        return a[:len(b)] == b or b[:len(a)] == a
    def segments(items, prefix):
        for i, seg in enumerate(items if isinstance(items, list) else []):
            if not isinstance(seg, dict) or not seg.get('segment_id'):
                continue
            base = [*prefix, i]
            shots = seg.get('shots', [])
            shots = shots if isinstance(shots, list) and shots else [None]
            for j, shot in enumerate(shots):
                if stage == 'storyboard' and not any(intersects(c['path'], [*base, 'shots', j]) or
                        (c['path'][:len(base)] == base and 'shots' not in c['path'][len(base):]) for c in changes):
                    continue
                n = shot.get('shot_number') if isinstance(shot, dict) else None
                affected.append({'segment_id': str(seg['segment_id']), 'shot_number': n if type(n) is int else None,
                    'reason': '剧本变化，现有分镜、参考图、视频及成片需人工核对' if stage == 'script_generation' else '该片段或镜头的参考图、视频及成片需人工核对'})
    def visit(obj, prefix):
        segments(obj.get('segments'), [*prefix, 'segments'])
        for i, ep in enumerate(obj.get('episodes', []) if isinstance(obj.get('episodes'), list) else []):
            if isinstance(ep, dict):
                segments(ep.get('segments'), [*prefix, 'episodes', i, 'segments'])
    visit(storyboard, [])
    if isinstance(storyboard.get('payload'), dict):
        visit(storyboard['payload'], ['payload'])
    return unique(affected)


def unique(items):
    return list({(item['segment_id'], item['shot_number'], item['reason']): item for item in items}.values())


def validate_history(raw):
    if not isinstance(raw, dict) or type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise ValueError('Invalid revision schema')
    history = History.model_validate(raw)
    ids, requests, previous = set(), set(), {}
    for i, version in enumerate(history.versions):
        if version.number != i + 1 or version.version_id in ids or version.digest != digest(version.content):
            raise ValueError('Invalid revision history')
        datetime.fromisoformat(version.created_at.replace('Z', '+00:00'))
        is_write = version.source in ('edit', 'restore')
        if is_write != (version.request_id is not None and version.request_digest is not None):
            raise ValueError('Invalid request history')
        if not is_write and (version.request_id is not None or version.request_digest is not None):
            raise ValueError('Unexpected request history')
        if version.source == 'restore':
            if version.restored_from not in previous or previous[version.restored_from] != version.stage:
                raise ValueError('Invalid restoration history')
        elif version.restored_from is not None:
            raise ValueError('Unexpected restoration history')
        if version.request_id is not None:
            if version.request_id in requests:
                raise ValueError('Repeated request history')
            requests.add(version.request_id)
        ids.add(version.version_id)
        previous[version.version_id] = version.stage
    return history


class ContentRevisions:
    def __init__(self, engine):
        self.engine = engine

    def _read(self, project_id):
        try:
            path = session_path(self.engine._session_dir, project_id)
            if not path.exists():
                fail(404, 'PROJECT_NOT_FOUND', '项目不存在')
            if not path.is_file():
                raise ValueError('Not a regular session file')
            disk_data = None
            if path.exists():
                disk_data = read_session(path, project_id)  # Refuse damaged/new-schema files even with cached state.
            state = self.engine.get_state(project_id)
            if state is None:
                fail(404, 'PROJECT_NOT_FOUND', '项目不存在')
            raw = getattr(state, 'content_history', None)
            if disk_data is not None and raw != disk_data.get('content_history'):
                raise ValueError('Revision history changed outside current engine')
            history = History.model_validate(raw) if raw is not None else History()
            validate_history(history.model_dump())
            current = {stage: copy.deepcopy(state.artifacts.get(stage, {})) for stage in STAGES}
            if disk_data is not None and any(encoded(current[stage]) != encoded(disk_data.get('artifacts', {}).get(stage, {})) for stage in STAGES):
                raise ValueError('Content changed outside current engine')
            if any(not isinstance(value, dict) for value in current.values()):
                fail(422, 'UNSUPPORTED_CONTENT', '此项目文字结构暂不支持本轮编辑')
            return state, history, current
        except AssetLibraryError:
            raise
        except (OSError, ValueError, TypeError, ValidationError):
            fail(503, 'REVISION_STORE_UNAVAILABLE', '文字版本数据暂不可读取，请保留数据并检查服务')

    def _workspace(self, project_id, state, history, current):
        versions = history.model_dump()['versions']
        latest = {stage: next((v for v in reversed(versions) if v['stage'] == stage), None) for stage in STAGES}
        return Workspace.model_validate({
            'project_id': project_id, 'token': digest({'current': current, 'history': history.model_dump()}),
            'running': project_id in self.engine._active_sessions or 'running' in state.status.values(),
            'fields': {stage: fields(stage, current[stage]) for stage in STAGES},
            'untracked_stages': [stage for stage in STAGES if latest[stage] and latest[stage]['digest'] != digest(current[stage])],
            'versions': [{k: v[k] for k in ('version_id', 'number', 'stage', 'source', 'created_at', 'changes', 'impacts', 'restored_from')} |
                         {'current': latest[v['stage']] == v and v['digest'] == digest(current[v['stage']]), 'fields': fields(v['stage'], v['content'])} for v in versions],
            'impacts': unique([item for v in versions for item in v['impacts']]),
        })

    def read(self, project_id):
        with self.engine._state_lock:
            return self._workspace(project_id, *self._read(project_id))

    def editor(self, project_id, stage):
        with self.engine._state_lock:
            state, history, current = self._read(project_id)
            if len(encoded(current[stage])) > MAX_CONTENT:
                fail(422, 'CONTENT_LIMIT', '文字内容超过本轮支持上限，请保留原文')
            from core.manual_content import validate_replacement
            validate_replacement(stage, current[stage], current[stage])
            return Editor(workspace=self._workspace(project_id, state, history, current), artifact=current[stage])

    def _preview(self, project_id, payload, state, history, current):
        workspace = self._workspace(project_id, state, history, current)
        if workspace.running:
            fail(409, 'PROJECT_RUNNING', '项目正在执行，请结束任务后再修改文字')
        if payload.expected_token != workspace.token:
            fail(409, 'REVISION_CHANGED', '文字或历史已变化，请重新读取并预览')
        stage = payload.stage
        if len(encoded(current[stage])) > MAX_CONTENT:
            fail(422, 'CONTENT_LIMIT', '文字内容超过本轮支持上限，请保留原文')
        if payload.restore_version_id:
            version = next((v for v in history.versions if v.version_id == payload.restore_version_id and v.stage == stage), None)
            if version is None:
                fail(404, 'VERSION_NOT_FOUND', '所选阶段的版本不存在')
            target = copy.deepcopy(version.content)
        elif payload.replacement is not None:
            from core.manual_content import validate_replacement
            validate_replacement(stage, current[stage], payload.replacement)
            target = copy.deepcopy(payload.replacement)
        else:
            if not any(f['path'] == payload.path for f in fields(stage, current[stage])):
                fail(422, 'FIELD_NOT_EDITABLE', '只能修改已列出的文字字段')
            target = copy.deepcopy(current[stage])
            obj = target
            for part in payload.path[:-1]:
                obj = obj[part]
            obj[payload.path[-1]] = payload.value
        if len(encoded(target)) > MAX_CONTENT:
            fail(422, 'CONTENT_LIMIT', '文字内容超过本轮支持上限，请保留原文')
        fields(stage, target)  # Validate projection limits before persisting an unreadable version.
        changes = differences(current[stage], target)
        if not changes:
            fail(422, 'NO_CHANGE', '内容没有变化，无需保存新版本')
        impacts = unique(impact(stage, changes, current['storyboard']) + (impact(stage, changes, target) if stage == 'storyboard' else []))
        preview = Preview(stage=stage, expected_token=workspace.token, changes=changes, impacts=impacts,
                          restore_version_id=payload.restore_version_id,
                          preview_token=digest({'input': payload.model_dump(), 'target': target, 'impacts': impacts}))
        return preview, target

    def preview(self, project_id, payload):
        with self.engine._state_lock:
            return self._preview(project_id, payload, *self._read(project_id))[0]

    def save(self, project_id, payload):
        with self.engine._state_lock:
            state, history, current = self._read(project_id)
            request_digest = digest(payload.model_dump())
            previous = next((v for v in history.versions if v.request_id == payload.request_id), None)
            if previous:
                if previous.request_digest != request_digest:
                    fail(409, 'REQUEST_ID_REUSED', '请求编号已用于其他修改，请重新预览')
                return Saved(version_id=previous.version_id, workspace=self._workspace(project_id, state, history, current), artifact=current[payload.stage])
            input_data = RevisionInput.model_validate(payload.model_dump(include=set(RevisionInput.model_fields)))
            preview, target = self._preview(project_id, input_data, state, history, current)
            if payload.preview_token != preview.preview_token:
                fail(409, 'PREVIEW_CHANGED', '预览与提交不一致，请重新预览')
            next_history = history.model_copy(deep=True)
            def append(stage, source, content, **kwargs):
                version = Revision(version_id=uuid.uuid4().hex, number=len(next_history.versions)+1, stage=stage,
                                   source=source, content=copy.deepcopy(content), digest=digest(content),
                                   created_at=datetime.now(timezone.utc).isoformat(), changes=[], impacts=[], **kwargs)
                next_history.versions.append(version)
                return version
            for stage in STAGES:
                latest = next((v for v in reversed(history.versions) if v.stage == stage), None)
                if current[stage] and (latest is None or latest.digest != digest(current[stage])):
                    append(stage, 'baseline' if latest is None else 'external', current[stage])
            version = append(payload.stage, 'restore' if payload.restore_version_id else 'edit', target,
                             restored_from=payload.restore_version_id, request_id=payload.request_id, request_digest=request_digest)
            version.changes = preview.changes
            version.impacts = preview.impacts
            if len(encoded(next_history.model_dump())) > MAX_HISTORY:
                fail(422, 'HISTORY_LIMIT', '版本历史超过本轮支持上限，请保留数据并导出备份后处理')
            old_artifacts, old_history, old_time = copy.deepcopy(state.artifacts), getattr(state, 'content_history', None), state.updated_at
            try:
                state.artifacts[payload.stage] = target
                state.content_history = next_history.model_dump()
                state.updated_at = datetime.now()
                self.engine.save_session_to_disk(project_id)
            except Exception:
                state.artifacts, state.content_history, state.updated_at = old_artifacts, old_history, old_time
                fail(503, 'REVISION_SAVE_FAILED', '保存未能完成，请重新读取后核对，原有版本已保留')
            updated = {**current, payload.stage: target}
            return Saved(version_id=version.version_id, workspace=self._workspace(project_id, state, next_history, updated), artifact=updated[payload.stage])
