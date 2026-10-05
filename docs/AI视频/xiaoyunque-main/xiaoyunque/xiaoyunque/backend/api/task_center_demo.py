"""Isolated task-center acceptance with durable artificial samples, no AI routes."""

import argparse
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi.testclient import TestClient

from api.dependencies import get_workflow_engine
from api.media_demo import create_demo
from core.persistence import atomic_write_session
from pipelines import storage


def create_task_center_demo(directory: Path):
    app = create_demo(directory, task_simulation=True)
    app.title = '创作工作室任务中心 · 人工验收，无模型调用'
    engine = app.dependency_overrides[get_workflow_engine]()
    storage.TASK_DATA_DIR = str(directory / 'pipeline-tasks')
    storage.TASK_RESULT_DIR = str(directory / 'pipeline-results')
    marker = directory / 'task-center-seeded.json'
    if not marker.exists():
        # Never continue an incomplete seed silently; preserve its records for review.
        if (directory / 'asset-library/generation-tasks.json').exists():
            raise ValueError('人工样例初始化未完成，请保留原目录并核对')
        state = engine.get_state('media-demo')
        state.meta['idea'] = '任务中心 · 人工流程（无模型调用）'
        state.status['storyboard'] = 'waiting'
        engine.save_session_to_disk('media-demo')
        with TestClient(app) as client:
            def post(path, payload=None):
                response = client.post(path, json=payload)
                response.raise_for_status()
                return response.json()
            asset = post('/api/assets/import-media', {'project_id': 'media-demo', 'stage': 'character_design', 'item_type': 'characters', 'item_id': 'role-1', 'candidate_number': 1})
            ref = post('/api/asset-references', {'project_id': 'media-demo', 'segment_id': 'seg-1', 'shot_number': 1, 'asset_id': asset['asset_id'], 'version_id': asset['versions'][0]['version_id']})
            plan = post('/api/generation-plans', {'project_id': 'media-demo', 'options': {'model': 'local-test-model', 'mode': 'first_frame'}, 'targets': [{'segment_id': 'seg-1', 'duration': 5, 'bindings': [{'reference_id': ref['reference_id'], 'role': 'first_frame'}]}]})
            post(f"/api/generation-plans/{plan['plan_id']}/confirm", {'content_hash': plan['content_hash'], 'local_only': True})
            for phase in ['succeeded', 'queued', 'running', 'blocked']:
                quote = post('/api/generation-quotes', {'plan_id': plan['plan_id'], 'segment_id': 'seg-1'})
                task = post('/api/generation-tasks', {'quote_id': quote['quote_id'], 'request_id': uuid4().hex})
                if phase != 'blocked':
                    post(f"/api/generation-quotes/{quote['quote_id']}/confirm", {'quote_hash': quote['quote_hash'], 'max_amount_minor': 10, 'project_limit_minor': 100, 'simulation_only': True})
                    task = post(f"/api/generation-tasks/{task['task_id']}/enqueue", {'expected_revision': task['revision'], 'quote_hash': quote['quote_hash']})
                    for _ in range(8):
                        if task['status'] == phase: break
                        task = post(f"/api/generation-tasks/{task['task_id']}/advance")
                    if task['status'] != phase: raise ValueError('人工任务未到达目标状态')
        for pipeline, status, name in [('standard', 'completed', '人工专项 · 完成状态'), ('action_transfer', 'failed', '人工专项 · 失败状态')]:
            task = storage.create_task(pipeline, {})
            # Explicit UI fixtures; these are not produced by a model or claimed as results.
            storage.update_task(task['task_id'], status=status, progress=100 if status == 'completed' else 30, output={'title': name + '（无模型结果）'})
        atomic_write_session(marker, {'artificial_only': True, 'seeded': True})

    @app.get('/api/sessions')
    def sessions() -> dict[str, list[dict]]:
        return {'sessions': engine.list_saved_sessions()}

    @app.get('/api/tasks')
    def pipeline_tasks() -> dict[str, list[dict]]:
        return {'tasks': storage.list_tasks(limit=500)}

    @app.get('/api/sandbox/tasks')
    def sandbox_tasks() -> dict:
        return {'success': True, 'tasks': []}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    app = create_task_center_demo(args.directory.resolve())
    print(f'任务中心人工验收：http://127.0.0.1:{args.port}/docs；无模型请求、无真实费用。', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
