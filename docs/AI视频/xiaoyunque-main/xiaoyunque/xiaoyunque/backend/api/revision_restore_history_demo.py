"""Isolated artificial restoration history; never invokes a model."""
import argparse
import uuid
from pathlib import Path
import uvicorn
from api.revision_import_restore_demo import create_restore_demo
from api.dependencies import get_workflow_engine
from api.schemas.content_revisions import RevisionInput, SaveRevision
from core.content_revisions import ContentRevisions
from core.revision_exports import RevisionExports
from core.revision_import_restore import RevisionImportRestore


def create_history_demo(directory: Path):
    fresh = not (directory / 'sessions' / 'comparison-demo.json').exists()
    app = create_restore_demo(directory)
    app.title = '恢复记录历史定位 · 人工样例，不调用模型'
    if fresh:
        engine = app.dependency_overrides[get_workflow_engine]()
        revisions, restores = ContentRevisions(engine), RevisionImportRestore(engine)
        project = 'comparison-demo'
        workspace = revisions.read(project)
        backup = RevisionExports(engine).download(project, workspace.token, 'json').body
        def edit(stage, path, value):
            workspace = revisions.read(project)
            payload = RevisionInput(expected_token=workspace.token, stage=stage, path=path, value=value)
            preview = revisions.preview(project, payload)
            revisions.save(project, SaveRevision(**payload.model_dump(), preview_token=preview.preview_token,
                request_id=uuid.uuid4().hex, acknowledged=True))
        def restore():
            workspace = revisions.read(project)
            prepared = restores.prepare(project, workspace.token, '人工文字备份.json', backup)
            restores.save(project, workspace.token, '人工文字备份.json', backup, prepared.confirmation_token, uuid.uuid4().hex)
        edit('script_generation', ['title'], '人工恢复前的新标题')
        edit('storyboard', ['episodes', 0, 'segments', 0, 'shots', 0, 'content'], '人工恢复前的新镜头')
        restore()
        edit('script_generation', ['title'], '第二次人工恢复前的标题')
        restore()
        edit('script_generation', ['title'], '恢复之后继续创作 · 人工样例')
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8016)
    args = parser.parse_args()
    uvicorn.run(create_history_demo(args.directory.resolve()), host='127.0.0.1', port=args.port, access_log=False)

if __name__ == '__main__':
    main()
