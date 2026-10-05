"""Persistent local co-creation intent ledger, independent of content writes."""
import copy
import uuid
from datetime import datetime, timezone
from api.schemas.co_creation import Ledger, Intent, Decision, CreationWorkspace, CreationSaved, Application, ApplicationWorkspace, ApplyPreview, Applied
from core.content_revisions import ContentRevisions, digest, encoded, fail, fields, differences
from api.schemas.content_revisions import RevisionInput
from core.persistence import read_session, session_path
from core.asset_library import AssetLibraryError

MAX_LEDGER_BYTES = 2 * 1024 * 1024

def validate_ledger(raw):
    if not isinstance(raw, dict) or type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise ValueError('Unsupported intent ledger')
    ledger = Ledger.model_validate(raw)
    ids, requests, states = set(), set(), {}
    for n, r in enumerate(ledger.records, 1):
        if r.number != n or r.intent_id in ids or r.request_id in requests or not r.message.strip():
            raise ValueError('Invalid intent identity')
        if not r.target.path or len(r.target.path) > 10 or any(type(p) not in (str, int) or isinstance(p, int) and p < 0 for p in r.target.path):
            raise ValueError('Invalid target path')
        datetime.fromisoformat(r.created_at.replace('Z', '+00:00'))
        ids.add(r.intent_id); requests.add(r.request_id); states[r.intent_id] = 'pending'
    decision_ids = set()
    for n, d in enumerate(ledger.decisions, 1):
        previous = states.get(d.intent_id)
        if d.number != n or d.decision_id in decision_ids or d.request_id in requests or previous is None or previous == 'withdrawn' or d.decision == 'confirm' and previous != 'pending':
            raise ValueError('Invalid decision sequence')
        datetime.fromisoformat(d.created_at.replace('Z', '+00:00'))
        decision_ids.add(d.decision_id); requests.add(d.request_id)
        states[d.intent_id] = 'confirmed' if d.decision == 'confirm' else 'withdrawn'
    applications, applied_intents, versions = set(), set(), set()
    confirmed = {d.intent_id for d in ledger.decisions if d.decision == 'confirm'}
    for n, a in enumerate(ledger.applications, 1):
        if a.number != n or a.application_id in applications or a.intent_id in applied_intents or a.version_id in versions or a.request_id in requests or a.intent_id not in confirmed:
            raise ValueError('Invalid application association')
        datetime.fromisoformat(a.created_at.replace('Z', '+00:00'))
        applications.add(a.application_id); applied_intents.add(a.intent_id); versions.add(a.version_id); requests.add(a.request_id)
    if len(encoded(ledger.model_dump())) > MAX_LEDGER_BYTES:
        raise ValueError('Intent ledger too large')
    return ledger

class CoCreation:
    def __init__(self, engine):
        self.engine = engine
        self.revisions = ContentRevisions(engine)

    def _read(self, project_id):
        state, history, current = self.revisions._read(project_id)
        try:
            disk = read_session(session_path(self.engine._session_dir, project_id), project_id)
            raw = getattr(state, 'co_creation', None)
            if raw != disk.get('co_creation'):
                raise ValueError('External ledger update')
            ledger = validate_ledger(raw) if raw is not None else Ledger()
            for a in ledger.applications:
                intent = next(r for r in ledger.records if r.intent_id == a.intent_id)
                version = next((v for v in history.versions if v.version_id == a.version_id), None)
                if version is None or version.source != 'edit' or version.stage != intent.stage or version.request_id != a.request_id or version.request_digest != a.request_digest or len(version.changes) != 1:
                    raise ValueError('Invalid application version')
                before = next((v for v in reversed(history.versions[:version.number-1]) if v.stage == version.stage), None)
                actual = differences(before.content, version.content) if before else []
                recorded = [{k:v for k,v in c.model_dump().items() if k not in ('before_present','after_present')} for c in version.changes]
                if not before or actual != recorded or a.created_at != version.created_at or not any(d.intent_id == intent.intent_id and d.decision == 'confirm' and datetime.fromisoformat(d.created_at) <= datetime.fromisoformat(a.created_at) for d in ledger.decisions) or any(d.intent_id == intent.intent_id and d.decision == 'withdraw' and datetime.fromisoformat(d.created_at) < datetime.fromisoformat(a.created_at) for d in ledger.decisions):
                    raise ValueError('Invalid application snapshot or chronology')
                change = version.changes[0]
                value = next((f['value'] for f in fields(version.stage, version.content) if f['path'] == intent.target.path), None)
                if change.path != intent.target.path or change.before != intent.target.value or change.after != value or not change.before_present or not change.after_present or change.before == change.after:
                    raise ValueError('Invalid application change')
            text = self.revisions._workspace(project_id, state, history, current)
            records = []
            for r in ledger.records:
                last = next((d for d in reversed(ledger.decisions) if d.intent_id == r.intent_id), None)
                status = 'withdrawn' if last and last.decision == 'withdraw' else 'stale' if r.text_token != text.token else 'confirmed' if last else 'pending'
                records.append({**{k:v for k,v in r.model_dump().items() if k != 'request_digest'}, 'status': status})
            workspace = CreationWorkspace(project_id=project_id,
                token=digest({'text': text.token, 'ledger': ledger.model_dump()}),
                text_token=text.token, running=text.running, fields=text.fields,
                records=records, decisions=[{k:v for k,v in d.model_dump().items() if k != 'request_digest'} for d in ledger.decisions])
            return state, ledger, workspace
        except AssetLibraryError:
            raise
        except (OSError, ValueError, TypeError):
            fail(503, 'INTENT_STORE_UNAVAILABLE', '共创记录暂不可读取，请保留数据并检查服务')

    def read(self, project_id):
        with self.engine._state_lock:
            return self._read(project_id)[2]

    def save(self, project_id, payload):
        with self.engine._state_lock:
            state, ledger, workspace = self._read(project_id)
            request_digest = digest(payload.model_dump())
            prior = next((r for r in [*ledger.records, *ledger.decisions, *ledger.applications] if r.request_id == payload.request_id), None)
            if prior:
                if isinstance(prior, Application) or prior.request_digest != request_digest:
                    fail(409, 'INTENT_REQUEST_REUSED', '请求编号已用于其他操作，请重新读取')
                return CreationSaved(intent_id=prior.intent_id, workspace=workspace)
            if payload.expected_token != workspace.token:
                fail(409, 'INTENT_CHANGED', '正文或共创记录已变化，请重新读取核对')
            next_ledger = ledger.model_copy(deep=True)
            now = datetime.now(timezone.utc).isoformat()
            is_decision = hasattr(payload, 'decision')
            if not is_decision:
                if workspace.running:
                    fail(409, 'PROJECT_RUNNING', '项目正在执行，请结束后再记录修改意图')
                field = next((f for f in workspace.fields[payload.stage] if f.path == payload.path), None)
                if field is None:
                    fail(422, 'INTENT_TARGET_UNAVAILABLE', '请选择当前项目的一处可修改文字')
                record = Intent(intent_id=uuid.uuid4().hex, number=len(ledger.records)+1, stage=payload.stage,
                    target=field.model_copy(deep=True), message=payload.message, preserve=payload.preserve,
                    text_token=workspace.text_token, created_at=now, request_id=payload.request_id, request_digest=request_digest)
                next_ledger.records.append(record)
                intent_id = record.intent_id
            else:
                intent = next((r for r in workspace.records if r.intent_id == payload.intent_id), None)
                if intent is None:
                    fail(404, 'INTENT_NOT_FOUND', '这条共创请求不存在')
                if intent.status == 'withdrawn' or payload.decision == 'confirm' and intent.status != 'pending':
                    fail(422, 'INTENT_DECISION_INVALID', '请求已确认、撤回或依据变化，请核对记录')
                if payload.decision == 'confirm' and workspace.running:
                    fail(409, 'PROJECT_RUNNING', '项目正在执行，请结束后再确认修改意图')
                if payload.decision == 'confirm' and not any(f.path == intent.target.path and f.value == intent.target.value for f in workspace.fields[intent.stage]):
                    fail(409, 'INTENT_TARGET_CHANGED', '修改对象已变化，请重新记录请求')
                next_ledger.decisions.append(Decision(decision_id=uuid.uuid4().hex, number=len(ledger.decisions)+1,
                    intent_id=intent.intent_id, decision=payload.decision, created_at=now,
                    request_id=payload.request_id, request_digest=request_digest))
                intent_id = intent.intent_id
            if len(next_ledger.records) > 100 or len(next_ledger.decisions) > 200 or len(encoded(next_ledger.model_dump())) > MAX_LEDGER_BYTES:
                fail(422, 'INTENT_LIMIT', '共创记录达到本轮上限，请保留数据并联系开发者处理')
            old, old_time = copy.deepcopy(getattr(state, 'co_creation', None)), state.updated_at
            try:
                state.co_creation = next_ledger.model_dump()
                state.updated_at = datetime.now()
                self.engine.save_session_to_disk(project_id, expected_co_creation=old)
            except Exception:
                state.co_creation, state.updated_at = old, old_time
                fail(503, 'INTENT_SAVE_FAILED', '保存结果尚未确认，请重新读取共创记录，原记录保留')
            return CreationSaved(intent_id=intent_id, workspace=self._read(project_id)[2])

    def _application_workspace(self, project_id):
        state, ledger, workspace = self._read(project_id)
        _, history, current = self.revisions._read(project_id)
        return ApplicationWorkspace(workspace=workspace,
            revisions=self.revisions._workspace(project_id, state, history, current),
            applications=[{k:v for k,v in a.model_dump().items() if k != 'request_digest'} for a in ledger.applications])

    def applications(self, project_id):
        with self.engine._state_lock:
            return self._application_workspace(project_id)

    def _apply_preview(self, project_id, payload, state, ledger, workspace):
        if payload.expected_token != workspace.token:
            fail(409, 'INTENT_CHANGED', '正文或共创记录已变化，请重新读取核对')
        intent = next((r for r in workspace.records if r.intent_id == payload.intent_id), None)
        if intent is None:
            fail(404, 'INTENT_NOT_FOUND', '这条共创请求不存在')
        if any(a.intent_id == intent.intent_id for a in ledger.applications):
            fail(422, 'INTENT_ALREADY_APPLIED', '请求已保存为版本，请查看原关联版本')
        if intent.status != 'confirmed':
            fail(422, 'INTENT_NOT_CONFIRMED', '请基于当前正文记录并确认修改意图')
        _, history, current = self.revisions._read(project_id)
        revision, target = self.revisions._preview(project_id,
            RevisionInput(expected_token=workspace.text_token, stage=intent.stage, path=intent.target.path, value=payload.value), state, history, current)
        token = digest({'input': {'expected_token': payload.expected_token, 'intent_id': payload.intent_id, 'value': payload.value}, 'revision': revision.model_dump()})
        return ApplyPreview(intent_id=intent.intent_id, expected_token=workspace.token, preview_token=token, revision=revision), target, history, current

    def preview_application(self, project_id, payload):
        with self.engine._state_lock:
            return self._apply_preview(project_id, payload, *self._read(project_id))[0]

    def apply(self, project_id, payload):
        with self.engine._state_lock:
            state, ledger, workspace = self._read(project_id)
            request_digest = digest(payload.model_dump())
            prior = next((r for r in [*ledger.records, *ledger.decisions, *ledger.applications] if r.request_id == payload.request_id), None)
            if prior:
                if not isinstance(prior, Application) or prior.request_digest != request_digest:
                    fail(409, 'INTENT_REQUEST_REUSED', '请求编号已用于其他操作，请重新读取')
                return Applied(application_id=prior.application_id, version_id=prior.version_id, data=self._application_workspace(project_id))
            # A request from the legacy revision endpoint also cannot be reused.
            _, existing, _ = self.revisions._read(project_id)
            if any(v.request_id == payload.request_id for v in existing.versions):
                fail(409, 'INTENT_REQUEST_REUSED', '请求编号已用于其他文字修改，请重新读取')
            preview, target, history, current = self._apply_preview(project_id, payload, state, ledger, workspace)
            if payload.preview_token != preview.preview_token:
                fail(409, 'PREVIEW_CHANGED', '修改内容或预览已变化，请重新预览')
            next_history, version = self.revisions._prepare_history(history, current, preview.revision, target, payload.request_id, request_digest)
            next_ledger = ledger.model_copy(deep=True)
            application = Application(application_id=uuid.uuid4().hex, number=len(ledger.applications)+1,
                intent_id=payload.intent_id, version_id=version.version_id, created_at=version.created_at,
                request_id=payload.request_id, request_digest=request_digest)
            next_ledger.applications.append(application)
            if len(next_ledger.applications) > 100 or len(encoded(next_ledger.model_dump())) > MAX_LEDGER_BYTES:
                fail(422, 'INTENT_LIMIT', '共创记录达到本轮上限，请保留数据')
            old_artifacts, old_history = copy.deepcopy(state.artifacts), copy.deepcopy(getattr(state, 'content_history', None))
            old_ledger, old_time = copy.deepcopy(getattr(state, 'co_creation', None)), state.updated_at
            try:
                state.artifacts[preview.revision.stage] = target
                state.content_history = next_history.model_dump()
                state.co_creation = next_ledger.model_dump()
                state.updated_at = datetime.now()
                self.engine.save_session_to_disk(project_id, expected_co_creation=old_ledger, expected_revision=(old_artifacts, old_history))
            except Exception:
                state.artifacts, state.content_history, state.co_creation, state.updated_at = old_artifacts, old_history, old_ledger, old_time
                fail(503, 'INTENT_APPLY_FAILED', '保存结果尚未确认，请重新读取关联和版本；原记录保留')
            return Applied(application_id=application.application_id, version_id=version.version_id, data=self._application_workspace(project_id))
