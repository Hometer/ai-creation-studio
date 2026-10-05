"""Isolated durable plan UI samples. No model, task, readiness or execution routes."""

import argparse
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException

from api.dependencies import get_workflow_engine
from api.media_demo import create_demo
from api.errors import ErrorResponse, HTTP_ERRORS, register_error_handlers
from api.routers.assets import router as assets_router
from api.routers.generation_plans import router as plans_router
from core.persistence import atomic_write_session


def create_plan_center_demo(directory: Path):
    base = create_demo(directory)
    # Reuse real stores, explicitly mount only the two offline routers.
    app = FastAPI(title='创作工作室生成计划 · 人工样例，无模型调用',
                  responses={status: {'model': ErrorResponse} for status in HTTP_ERRORS})
    register_error_handlers(app)
    app.include_router(assets_router)
    app.include_router(plans_router)
    app.dependency_overrides.update(base.dependency_overrides)
    engine = app.dependency_overrides[get_workflow_engine]()
    marker = directory / 'plan-center-seeded.json'
    if not marker.exists():
        if (directory / 'asset-library/library.json').exists():
            raise ValueError('人工初始化未完成，请保留原目录并核对')
        state = engine.get_state('media-demo')
        state.meta.update(idea='生成计划 · 人工色块验收（无模型调用）', video_first_frame_model='local-test-model')
        engine.save_session_to_disk('media-demo')
        with TestClient(app) as client:
            def post(path, payload):
                response = client.post(path, json=payload)
                response.raise_for_status()
                return response.json()
            for candidate in (1, 2):
                asset = post('/api/assets/import-media', {'project_id': 'media-demo', 'stage': 'character_design', 'item_type': 'characters', 'item_id': 'role-1', 'candidate_number': candidate})
                post('/api/asset-references', {'project_id': 'media-demo', 'segment_id': 'seg-1', 'shot_number': candidate if candidate == 1 else None, 'asset_id': asset['asset_id'], 'version_id': asset['versions'][-1]['version_id']})
            text = post('/api/assets', {'kind': 'character', 'name': '人工角色设定', 'definition': '人工色块代表角色，不是模型结果。'})
            post('/api/asset-references', {'project_id': 'media-demo', 'asset_id': text['asset_id'], 'version_id': text['versions'][0]['version_id']})
        atomic_write_session(marker, {'artificial_only': True, 'seeded': True})

    @app.get('/api/project/{project_id}/status')
    def status(project_id: str):
        snapshot = engine.get_status_snapshot(project_id)
        if snapshot is None:
            raise HTTPException(status_code=404)
        return snapshot

    @app.get('/api/sessions')
    def sessions():
        return {'sessions': engine.list_saved_sessions()}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    app = create_plan_center_demo(args.directory.resolve())
    print(f'生成计划人工验收：http://127.0.0.1:{args.port}/docs；独立人工数据，无模型调用。', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
