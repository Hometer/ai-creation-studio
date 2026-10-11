"""Isolated durable local preparation UI; no provider, workflow or task routes."""

import argparse
from pathlib import Path

import uvicorn

from api.dependencies import get_workflow_engine
from api.plan_center_demo import create_plan_center_demo
from api.routers.generation_readiness import router as readiness_router
from api.routers.execution_checks import router as checks_router
from api.routers.model_preflight import get_preflight_connection
from core.model_preflight import check_connection
from core.persistence import atomic_write_session


def create_readiness_center_demo(directory: Path):
    app = create_plan_center_demo(directory)
    app.title = '映序执行前准备 · 人工样例，不联网、不收费'
    app.include_router(readiness_router)
    app.include_router(checks_router)
    app.dependency_overrides[get_preflight_connection] = lambda: check_connection('', 'https://dashscope.aliyuncs.com/api/v1')
    marker = directory / 'readiness-center-seeded.json'
    if not marker.exists():
        engine = app.dependency_overrides[get_workflow_engine]()
        state = engine.get_state('media-demo')
        state.meta.update(idea='执行前准备 · 人工色块验收（不联网、不收费）', video_first_frame_model='wan2.7-i2v')
        engine.save_session_to_disk('media-demo')
        atomic_write_session(marker, {'artificial_only': True, 'seeded': True})
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    app = create_readiness_center_demo(args.directory.resolve())
    print(f'本地准备人工验收：http://127.0.0.1:{args.port}/docs；不联网、不收费。', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
