"""Deterministic full snapshot preview with no persistence or restoration permission."""
import hashlib
from api.schemas.revision_import_preview import RestoreBackupPreview, RestoreStagePreview
from core.revision_import_precheck import check_filename, parse_backup
from core.content_revisions import (ContentRevisions, STAGES, MAX_CONTENT, MAX_COMPARISON_CHANGES,
    MAX_COMPARISON_RESPONSE, digest, encoded, fields, differences, impact, unique,
    comparison_label, comparison_type, fail)

class RevisionImportPreview:
    def __init__(self, engine):
        self.engine = engine

    def inspect(self, project_id, expected_token, filename, body):
        check_filename(filename)
        doc = parse_backup(body)
        with self.engine._state_lock:
            revisions = ContentRevisions(self.engine)
            state, history, current = revisions._read(project_id)
            workspace = revisions._workspace(project_id, state, history, current)
            if expected_token != workspace.token:
                fail(409, 'REVISION_CHANGED', '当前文字或历史已变化，请重新读取并检查备份后预览')
            if workspace.running:
                fail(409, 'PROJECT_RUNNING', '项目正在执行，请结束任务后再预览文字恢复')
            if doc.project_id != project_id:
                fail(422, 'BACKUP_PROJECT_MISMATCH', '备份来自另一个项目，只能检查范围，不能预览恢复到本项目')
            stages = {}; total = 0
            for stage in STAGES:
                before, after = current[stage], doc.current[stage].content
                if len(encoded(before)) > MAX_CONTENT:
                    fail(422, 'CONTENT_LIMIT', '当前文字超过 2 MiB，请保留原文并拆分项目后预览')
                before_fields, after_fields = fields(stage, before), fields(stage, after)
                changes = differences(before, after)
                total += len(changes)
                if total > MAX_COMPARISON_CHANGES:
                    fail(422, 'BACKUP_PREVIEW_LIMIT', '恢复差异超过 2000 条，请保留文件并拆分项目后预览')
                labels = {encoded(f['path']): f['label'] for f in before_fields + after_fields}
                impacts = unique([i for board in (current['storyboard'], doc.current['storyboard'].content)
                                  for i in impact(stage, changes, board)]) if changes else []
                stages[stage] = RestoreStagePreview(before_digest=digest(before), after_digest=doc.current[stage].digest,
                    before_field_count=len(before_fields), after_field_count=len(after_fields), impacts=impacts,
                    changes=[{**c, 'label': comparison_label(c['path'], labels),
                        'before_type': comparison_type(before, c['path'], c.get('before_present', True)),
                        'after_type': comparison_type(after, c['path'], c.get('after_present', True))} for c in changes])
            result = RestoreBackupPreview(project_id=project_id, token=workspace.token, source_project_id=doc.project_id,
                file_bytes=len(body), file_sha256=hashlib.sha256(body).hexdigest(), total_changes=total, stages=stages)
            if len(encoded(result.model_dump())) > MAX_COMPARISON_RESPONSE:
                fail(422, 'BACKUP_PREVIEW_LIMIT', '恢复预览超过 8 MiB，请保留文件并拆分项目后预览')
            return result
