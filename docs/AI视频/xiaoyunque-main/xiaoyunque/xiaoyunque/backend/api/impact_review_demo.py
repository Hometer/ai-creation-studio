"""Isolated human review demo. Artificial text, no model execution routes."""
import argparse
import copy
from pathlib import Path
import uvicorn
from api.manual_edit_demo import create_manual_demo
from api.dependencies import get_workflow_engine
from api.routers.assets import router as assets_router
from api.routers.generation_plans import router as plans_router
from api.schemas.content_revisions import RevisionInput,SaveRevision
from core.content_revisions import ContentRevisions


def create_impact_demo(directory:Path):
    app=create_manual_demo(directory)
    app.title='修改影响人工核对 · 人工样例，不调用模型'
    app.include_router(assets_router);app.include_router(plans_router)
    engine=app.dependency_overrides[get_workflow_engine]()
    if not (directory/'sessions/impact-review-demo.json').exists():
        engine.create_session('impact-review-demo',{'idea':'修改影响人工核对 · 人工样例，不调用模型'})
    state=engine.get_state('impact-review-demo')
    if not state.artifacts:
        origin=engine.get_state('manual-edit-demo');state.artifacts=copy.deepcopy(origin.artifacts);state.status=copy.deepcopy(origin.status);state.current_stage=origin.current_stage
        for stage in ('script_generation','storyboard'):state.artifacts[stage]['session_id']='impact-review-demo'
        engine.save_session_to_disk('impact-review-demo')
    if getattr(state,'content_history',None) is None:
        revisions=ContentRevisions(engine);workspace=revisions.read('impact-review-demo')
        payload=RevisionInput(expected_token=workspace.token,stage='script_generation',path=['logline'],value='人工修改：守望者把灯交给青年，共同等待渡船。')
        preview=revisions.preview('impact-review-demo',payload)
        revisions.save('impact-review-demo',SaveRevision(**payload.model_dump(),preview_token=preview.preview_token,request_id='e'*32,acknowledged=True))
    return app


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--port',type=int,default=8005);args=parser.parse_args()
    uvicorn.run(create_impact_demo(args.directory.resolve()),host='127.0.0.1',port=args.port,access_log=False)
if __name__=='__main__':main()
