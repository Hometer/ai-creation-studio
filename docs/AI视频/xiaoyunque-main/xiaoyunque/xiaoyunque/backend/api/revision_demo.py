"""Isolated persistent artificial text samples; only local revision routes."""
import argparse
from pathlib import Path
import uvicorn
from fastapi import FastAPI, HTTPException
from api.dependencies import get_workflow_engine
from api.errors import register_error_handlers
from api.routers.content_revisions import router
from core.orchestrator import WorkflowEngine


def create_revision_demo(directory: Path):
    engine = WorkflowEngine(str(directory / 'sessions'))
    if not (directory / 'sessions/revision-demo.json').exists():
        engine.create_session('revision-demo', {'idea': '文字修改与回退 · 人工样例（不调用模型）'})
        state = engine.get_state('revision-demo')
        state.artifacts = {
            'script_generation': {'title': '最后一班渡船 · 人工样例', 'logline': '青年与灯塔守望者在渡口相遇。',
                'episodes': [{'episode_number': 1, 'content': '阿舟说：“我会等最后一班渡船。”'},
                             {'episode_number': 2, 'content': '守望者点亮灯塔，远处的船缓缓靠岸。'}]},
            'storyboard': {'episodes': [{'episode_number': 1, 'segments': [
                {'segment_id': 'seg-1', 'visual_prompt': '暮色渡口，青年站在木桥上。', 'shots': [
                    {'shot_number': 1, 'duration': 5, 'content': '阿舟望向河面。', 'dialogue': '我会等最后一班渡船。'},
                    {'shot_number': 2, 'duration': 5, 'content': '岸边的灯亮起。'}]},
                {'segment_id': 'seg-2', 'visual_prompt': '灯塔与远处渡船。', 'shots': [
                    {'shot_number': 1, 'duration': 5, 'content': '守望者点亮灯塔。'}]}]}]},
            'reference_generation': {'scenes': [{'id': 'seg-1', 'selected': 'artificial-reference-v1', 'versions': ['artificial-reference-v1']}]},
            'video_generation': {'clips': [{'id': 'seg-1', 'selected': 'artificial-video-v1', 'versions': ['artificial-video-v1']}]},
        }
        engine.save_session_to_disk('revision-demo')
    if not (directory / 'sessions/revision-empty.json').exists():
        engine.create_session('revision-empty', {'idea': '空文字状态 · 人工样例'})
    app = FastAPI(title='文字修改与版本 · 人工文本，不调用模型')
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    @app.get('/api/tasks')
    @app.get('/api/sandbox/tasks')
    def empty_tasks():
        return {'tasks': []}  # This isolated service never creates these task kinds.

    @app.get('/api/sessions')
    def sessions():
        return {'sessions': engine.list_saved_sessions()}

    @app.get('/api/project/{project_id}/status')
    def status(project_id: str):
        snapshot = engine.get_status_snapshot(project_id)
        if snapshot is None:
            raise HTTPException(404)
        return snapshot

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8003)
    args = parser.parse_args()
    print(f'人工文字预览：http://127.0.0.1:{args.port}/docs；独立数据，不调用模型。', flush=True)
    uvicorn.run(create_revision_demo(args.directory.resolve()), host='127.0.0.1', port=args.port, access_log=False)

if __name__ == '__main__':
    main()
