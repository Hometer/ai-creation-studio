"""Two-stage restoration in one durable session replacement, with receipts."""
import copy, re, uuid
from datetime import datetime, timezone
from api.schemas.revision_import_restore import PreparedBackup, RestoreLedger, RestoreReceipt, RestoreWorkspace, RestoredBackup
from api.schemas.content_revisions import Revision, Change
from core.content_revisions import ContentRevisions, STAGES, MAX_HISTORY, digest, encoded, validate_history, fail
from core.revision_import_preview import RevisionImportPreview
from core.revision_import_precheck import parse_backup
from core.persistence import read_session, session_path
MAX_RESTORE_BYTES = 256*1024


def validate_restores(raw, history):
    if not isinstance(raw, dict) or type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise ValueError('Invalid backup restoration ledger')
    ledger = RestoreLedger.model_validate(raw)
    if len(encoded(ledger.model_dump())) > MAX_RESTORE_BYTES:
        raise ValueError('Restoration ledger limit')
    versions = {v.version_id:v for v in validate_history(history).versions}
    ids, requests, linked = set(), set(), set()
    for n, record in enumerate(ledger.records, 1):
        if record.number != n or record.restore_id in ids or record.request_id in requests:
            raise ValueError('Invalid restoration sequence')
        datetime.fromisoformat(record.created_at.replace('Z','+00:00'))
        for values in (record.before_digests, record.after_digests):
            if set(values) != set(STAGES) or any(not re.fullmatch(r'[a-f0-9]{64}',h) for h in values.values()):
                raise ValueError('Invalid restoration digests')
        changed = [s for s in STAGES if record.before_digests[s] != record.after_digests[s]]
        if not changed or set(record.version_ids) != set(changed):
            raise ValueError('Invalid restoration stage binding')
        for s in changed:
            v = versions.get(record.version_ids[s])
            if v is None or v.version_id in linked or v.stage != s or v.source != 'edit' or v.digest != record.after_digests[s] or v.request_digest != record.request_digest or (s == changed[0] and v.request_id != record.request_id):
                raise ValueError('Invalid restoration version binding')
            before = next((old for old in reversed(list(versions.values())) if old.stage == s and old.number < v.number), None)
            if before is None or before.digest != record.before_digests[s]:
                raise ValueError('Missing restoration prior text')
            linked.add(v.version_id)
        ids.add(record.restore_id); requests.add(record.request_id)
    return ledger


def public(record):
    return record.model_dump(exclude={'request_digest'})


class RevisionImportRestore:
    def __init__(self, engine):
        self.engine = engine
        self.revisions = ContentRevisions(engine)

    def _read(self, project_id):
        state, history, current = self.revisions._read(project_id)
        disk = read_session(session_path(self.engine._session_dir, project_id), project_id)
        raw = getattr(state, 'backup_restores', None)
        if raw != disk.get('backup_restores'):
            fail(503, 'BACKUP_RESTORE_STORE', '恢复记录已在其他进程变化，请重启本地服务后读取')
        try:
            ledger = validate_restores(raw, history.model_dump()) if raw is not None else RestoreLedger()
        except (ValueError, TypeError):
            fail(503, 'BACKUP_RESTORE_STORE', '恢复记录暂不可读，请保留数据并检查服务')
        return state, history, current, ledger

    def read(self, project_id):
        with self.engine._state_lock:
            state, history, current, ledger = self._read(project_id)
            return RestoreWorkspace(workspace=self.revisions._workspace(project_id,state,history,current), restores=[public(r) for r in ledger.records])

    def prepare(self, project_id, expected_token, filename, body):
        with self.engine._state_lock:
            self._read(project_id)
            preview = RevisionImportPreview(self.engine).inspect(project_id,expected_token,filename,body)
            result=PreparedBackup(preview=preview, confirmation_token=digest({'project_id':project_id,'filename':filename,'preview':preview.model_dump()}))
            if len(encoded(result.model_dump()))>8*1024*1024:
                fail(422,'BACKUP_PREVIEW_LIMIT','恢复确认预览超过 8 MiB，请保留数据')
            return result

    def save(self, project_id, expected_token, filename, body, confirmation_token, request_id):
        with self.engine._state_lock:
            state, history, current, ledger = self._read(project_id)
            checksum = __import__('hashlib').sha256(body).hexdigest()
            request_digest = digest({'operation':'backup_restore','project_id':project_id,'expected_token':expected_token,'filename':filename,'file_sha256':checksum,'confirmation_token':confirmation_token,'request_id':request_id,'acknowledged':True})
            prior = next((r for r in ledger.records if r.request_id == request_id),None)
            if prior:
                if prior.request_digest != request_digest:
                    fail(409,'REQUEST_ID_REUSED','请求编号已用于不同恢复，请重新读取记录')
                return RestoredBackup(receipt=public(prior),data=self.read(project_id))
            co = getattr(state,'co_creation',None) or {}
            if any(v.request_id == request_id for v in history.versions) or any(r.get('request_id') == request_id for key in ('records','decisions','applications','drafts') for r in co.get(key,[])):
                fail(409,'REQUEST_ID_REUSED','请求编号已用于其他操作，请重新预览')
            prepared = self.prepare(project_id,expected_token,filename,body)
            if confirmation_token != prepared.confirmation_token:
                fail(409,'BACKUP_CONFIRMATION_CHANGED','文件或恢复预览已变化，请重新检查预览并确认')
            preview = prepared.preview
            if not preview.total_changes:
                fail(422,'NO_CHANGE','内容相同，无需恢复或追加版本')
            # Keep history readable by all existing version consumers.
            if any(len(c.path)>10 for s in preview.stages.values() for c in s.changes):
                fail(422,'CONTENT_LIMIT','变化层级超过本轮版本记录上限，请保留备份')
            doc = parse_backup(body)
            next_history = history.model_copy(deep=True)
            now = datetime.now(timezone.utc).isoformat()
            def append(stage, content, source, **kw):
                v = Revision(version_id=uuid.uuid4().hex,number=len(next_history.versions)+1,stage=stage,source=source,content=copy.deepcopy(content),digest=digest(content),created_at=now,changes=[],impacts=[],**kw)
                next_history.versions.append(v); return v
            # Archive even an empty prior snapshot before any changed stage.
            for stage in STAGES:
                latest = next((v for v in reversed(next_history.versions) if v.stage == stage),None)
                if (current[stage] or preview.stages[stage].changes) and (latest is None or latest.digest != digest(current[stage])):
                    append(stage,current[stage],'baseline' if latest is None else 'external')
            version_ids = {}
            for stage in STAGES:
                s = preview.stages[stage]
                if not s.changes: continue
                v = append(stage,doc.current[stage].content,'edit',request_id=request_id if not version_ids else uuid.uuid4().hex,request_digest=request_digest)
                v.changes = [Change.model_validate(c.model_dump(include=set(Change.model_fields))) for c in s.changes]
                v.impacts = s.impacts; version_ids[stage]=v.version_id
            if len(encoded(next_history.model_dump())) > MAX_HISTORY:
                fail(422,'HISTORY_LIMIT','版本历史达到上限，请保留数据并备份后处理')
            if len(ledger.records)>=100:
                fail(422,'BACKUP_RESTORE_LIMIT','恢复记录达到上限，请保留原数据')
            receipt = RestoreReceipt(restore_id=uuid.uuid4().hex,number=len(ledger.records)+1,request_id=request_id,request_digest=request_digest,created_at=now,file_sha256=checksum,file_bytes=len(body),expected_token=expected_token,confirmation_token=confirmation_token,before_digests={s:preview.stages[s].before_digest for s in STAGES},after_digests={s:preview.stages[s].after_digest for s in STAGES},version_ids=version_ids)
            next_ledger = ledger.model_copy(deep=True); next_ledger.records.append(receipt)
            if len(next_ledger.records)>100 or len(encoded(next_ledger.model_dump()))>MAX_RESTORE_BYTES:
                fail(422,'BACKUP_RESTORE_LIMIT','恢复记录达到上限，请保留原数据')
            validate_restores(next_ledger.model_dump(),next_history.model_dump())
            candidate = RestoredBackup(receipt=public(receipt),data=RestoreWorkspace(workspace=self.revisions._workspace(project_id,state,next_history,{s:doc.current[s].content for s in STAGES}),restores=[public(r) for r in next_ledger.records]))
            if len(encoded(candidate.model_dump()))>8*1024*1024:
                fail(422,'BACKUP_RESTORE_LIMIT','恢复结果超过 8 MiB，请保留数据并拆分项目')
            old_artifacts, old_history, old_ledger, old_time = copy.deepcopy(state.artifacts), copy.deepcopy(state.content_history), copy.deepcopy(getattr(state,'backup_restores',None)), state.updated_at
            try:
                state.artifacts.update({s:copy.deepcopy(doc.current[s].content) for s in STAGES})
                state.content_history = next_history.model_dump(); state.backup_restores = next_ledger.model_dump(); state.updated_at=datetime.now()
                self.engine.save_session_to_disk(project_id,expected_revision=(old_artifacts,old_history),expected_backup_restores=old_ledger)
            except Exception:
                state.artifacts, state.content_history, state.backup_restores, state.updated_at = old_artifacts,old_history,old_ledger,old_time
                # A replacement may have completed before a transport/notification failure.
                # Read accepted disk state, never retry the write or overwrite it.
                try:
                    disk=read_session(session_path(self.engine._session_dir,project_id),project_id)
                    if any(r.get('request_id')==request_id and r.get('request_digest')==request_digest for r in disk.get('backup_restores',{}).get('records',[])):
                        self.engine.sessions[project_id]=self.engine._restore_state(disk)
                except Exception: pass
                fail(503,'BACKUP_RESTORE_UNCERTAIN','恢复结果尚未确认，请读取恢复记录与版本；不会自动重发')
            return RestoredBackup(receipt=public(receipt),data=self.read(project_id))
