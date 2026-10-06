"""Isolated candidate workflow with an explicitly artificial provider, never a model."""
import argparse
from pathlib import Path
from uuid import uuid4
import uvicorn
from api.co_creation_demo import create_co_creation_demo
from api.dependencies import get_workflow_engine
from api.routers.creation_candidates import router,service
from api.schemas.co_creation import RequestInput,DecisionInput
from core.co_creation import CoCreation
from core.creation_candidates import CreationCandidates
from core.model_preflight import check_connection


class ArtificialCandidateProvider:
    simulation_only=True
    def connection(self):return check_connection('artificial-offline-fixture','https://dashscope.aliyuncs.com/api/v1')
    def initialize(self):return self
    def generate(self,job,transport):
        return {'value':'灯塔与最后一班渡船 · 人工接口候选',
                'explanation':'这是人工接口样例，用于验证确认、载入和版本保存，不代表模型理解。',
                'preservation':'保留渡船与灯塔；文字含义仍需人工核对。'},1


def create_candidate_demo(directory:Path):
    app=create_co_creation_demo(directory)
    app.title='首版模型共创候选 · 人工接口样例，不调用模型'
    app.include_router(router)
    engine=app.dependency_overrides[get_workflow_engine]()
    candidates=CreationCandidates(engine,ArtificialCandidateProvider())
    app.dependency_overrides[service]=lambda:candidates
    creation=CoCreation(engine)
    w=creation.read('comparison-demo')
    if not w.records:
        f=next(f for f in w.fields['script_generation'] if f.path==['title'])
        result=creation.save('comparison-demo',RequestInput(expected_token=w.token,request_id=uuid4().hex,
            stage='script_generation',path=f.path,message='把标题改得更温暖，保留灯塔与渡船。',preserve='保留灯塔与渡船，其他字段不改。'))
        creation.save('comparison-demo',DecisionInput(expected_token=result.workspace.token,intent_id=result.intent_id,
            request_id=uuid4().hex,decision='confirm',acknowledged=True))
    return app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8017)
    args=parser.parse_args()
    uvicorn.run(create_candidate_demo(args.directory.resolve()),host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
