"""Isolated automatic archive demo; only local PATCH, no model execution routes."""
import argparse
import copy
from pathlib import Path
import uvicorn
from fastapi import HTTPException, Request
from api.manual_edit_demo import create_manual_demo
from api.dependencies import get_workflow_engine
from api.routers.assets import router as assets_router
from api.routers.generation_plans import router as plans_router


def create_automatic_demo(directory: Path):
    app = create_manual_demo(directory)
    app.title = '旧写入自动归档 · 人工样例，不调用模型'
    app.include_router(assets_router)
    app.include_router(plans_router)
    engine = app.dependency_overrides[get_workflow_engine]()
    project = 'automatic-revision-demo'
    if not (directory / 'sessions' / f'{project}.json').exists():
        engine.create_session(project, {'idea': '旧写入自动归档 · 人工样例，不调用模型'})
        original = engine.get_state('manual-edit-demo')
        state = engine.get_state(project)
        state.artifacts = copy.deepcopy(original.artifacts)
        state.status = copy.deepcopy(original.status)
        state.current_stage = original.current_stage
        for stage in ('script_generation', 'storyboard'):
            state.artifacts[stage]['session_id'] = project
        engine.save_session_to_disk(project)

    @app.patch('/api/project/{session_id}/artifact/{stage}')
    async def patch(session_id: str, stage: str, request: Request):
        if session_id != project or stage not in ('script_generation', 'storyboard'):
            raise HTTPException(422)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422)
        return engine.update_artifact(session_id, stage, body)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8006)
    args = parser.parse_args()
    uvicorn.run(create_automatic_demo(args.directory.resolve()), host='127.0.0.1', port=args.port, access_log=False)

if __name__ == '__main__':
    main()
