"""Isolated hand-written contract fixtures, never AI effects or paid calls."""
import argparse,json
from pathlib import Path
from contextlib import asynccontextmanager
import uvicorn
from api.media_demo import create_demo
from api.routers.release_tasks import router as release_tasks_router
from api.dependencies import get_workflow_engine
from api.routers.initial_drafts import router,service
from api.routers.project_goals import router as goals_router
from api.routers.content_revisions import router as revisions_router
from core.initial_drafts import InitialDrafts
from core.model_preflight import check_connection
from core.initial_draft_worker import InitialWorker

SCRIPT={'title':'人工：灯塔重逢','logline':'人工合同样例：朋友在灯塔重逢。','beat_sheet':'相遇、点亮灯塔。','characters':[{'id':'role-1','name':'朋友','description':'人工角色文字'}],'settings':[{'id':'set-1','name':'灯塔','description':'人工场景文字'}],'episodes':[{'episode_number':1,'act_title':'重逢','content':'人工合同样例。朋友走到灯塔前，挥手相迎，一起点亮灯塔。不代表真实模型效果。'}]}
BOARD={'episodes':[{'episode_number':1,'episode_title':'重逢','segments':[{'segment_id':f'seg-{n}','visual_prompt':'人工灯塔重逢画面','character_ids':['role-1'],'setting_ids':['set-1'],'shots':[{'shot_number':1,'duration':3,'content':content,'action':'朋友挥手','dialogue':'好久不见','sound':'轻柔的海浪声'}]} for n,content in [(1,'朋友走到灯塔前'),(2,'朋友一起点亮灯塔')]]}]}
class ArtificialProvider:
    simulation_only=True
    def __init__(self):self.calls=0;self.mode='success';self.ready=True
    def connection(self):return check_connection('artificial-fixture','https://dashscope.aliyuncs.com/api/v1')
    def initialize(self):
        if not self.ready:raise ValueError('private-init-detail')
        return self
    def generate(self,job,transport):
        self.calls+=1
        if self.mode=='timeout':raise TimeoutError('private-material')
        content=json.loads(json.dumps(SCRIPT if job.plan.kind=='script_generation' else BOARD))
        if self.mode=='invalid':content['execute']='untrusted'
        if self.mode=='wrong_id':content['episodes'][0]['segments'][0]['character_ids']=['foreign-id']
        if self.mode=='wrong_time':content['episodes'][0]['segments'][0]['shots'][0]['duration']=4
        return {'kind':job.plan.kind,'content':content,'explanation':'人工契约样例，不代表模型理解。','preservation':'真实效果与保留要求待你核对。'},1

def create_initial_demo(directory):
    app=create_demo(Path(directory));engine=app.dependency_overrides[get_workflow_engine]()
    if engine.get_status_snapshot('initial-demo') is None:
        engine.create_session('initial-demo',{'idea':'人工样例：两位朋友在灯塔重逢。','project_name':'人工从零剧本核对','episodes':1,'style':'realistic','video_ratio':'16:9','target_duration':6})
    drafts=InitialDrafts(engine,ArtificialProvider());app.include_router(router);app.include_router(goals_router);app.include_router(revisions_router);app.dependency_overrides[service]=lambda:drafts
    app.include_router(release_tasks_router)
    app.title='剧本与分镜 · 人工合同样例，无模型调用'
    @asynccontextmanager
    async def lifespan(_):
        worker=InitialWorker(drafts);worker.start()
        try:yield
        finally:worker.stop()
    app.router.lifespan_context=lifespan
    return app,drafts

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--port',type=int,default=8020);args=p.parse_args()
    app,_=create_initial_demo(args.directory.resolve());uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
if __name__=='__main__':main()
