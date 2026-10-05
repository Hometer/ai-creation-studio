"""Isolated original-editor preview with artificial content and no model execution routes."""
import argparse
import copy
from pathlib import Path
import uvicorn
from fastapi import HTTPException
from api.revision_demo import create_revision_demo
from core.orchestrator import WorkflowStage
from api.dependencies import get_workflow_engine
from api.routers.stages import router as stages_router


def create_manual_demo(directory: Path):
    app=create_revision_demo(directory)
    app.title='原编辑入口版本接线 · 人工样例，不调用模型'
    app.include_router(stages_router)
    engine=app.dependency_overrides[get_workflow_engine]()
    if not (directory/'sessions/manual-edit-demo.json').exists():
        engine.create_session('manual-edit-demo',{'idea':'原编辑入口 · 人工样例（不调用模型）'})
    if not engine.get_state('manual-edit-demo').artifacts:
        state=engine.get_state('manual-edit-demo')
        state.artifacts=copy.deepcopy(engine.get_state('revision-demo').artifacts)
        script=state.artifacts['script_generation']
        script.update(session_id='manual-edit-demo',phase='completed',mood='宁静',genre=['剧情'],extra={'preserve':'人工附加字段'})
        script['episodes'][0].update(act_number=1,act_title='渡口')
        script['episodes'][1].update(act_number=2,act_title='灯塔')
        storyboard=state.artifacts['storyboard'];storyboard.update(session_id='manual-edit-demo',extra={'preserve':'原编辑不丢字段'})
        ep=storyboard['episodes'][0];ep.update(episode_title='最后的渡船',unknown='人工章节附加字段')
        for index,seg in enumerate(ep['segments']):
            seg.update(segment_number=index+1,episode_number=1,location='渡口' if index==0 else '灯塔',characters=['阿舟'] if index==0 else ['守望者'],total_duration=5*len(seg['shots']))
            for shot in seg['shots']:shot.update(shot_type='中景',unknown={'binding':'人工附加字段保留'})
        state.status.update(script_generation='completed',storyboard='completed')
        state.current_stage=WorkflowStage.STORYBOARD
        engine.save_session_to_disk('manual-edit-demo')
    @app.get('/api/project/{project_id}/status/from_disk')
    def status_from_disk(project_id:str):
        snapshot=engine.get_status_snapshot(project_id)
        if snapshot is None:raise HTTPException(404)
        return snapshot
    @app.get('/api/models')
    def models():return {'models':[]}
    @app.get('/api/pipelines/standard/templates')
    def templates():return {'templates':[]}
    @app.get('/api/config')
    def config():return {'providers':[], 'models':{}}
    return app

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--port',type=int,default=8004)
    args=parser.parse_args()
    uvicorn.run(create_manual_demo(args.directory.resolve()),host='127.0.0.1',port=args.port,access_log=False)
if __name__=='__main__':main()
