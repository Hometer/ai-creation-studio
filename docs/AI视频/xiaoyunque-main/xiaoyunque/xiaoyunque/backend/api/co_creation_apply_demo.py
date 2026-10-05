"""Isolated confirmed human requests to text revisions; no model execution."""
import argparse
import uuid
from pathlib import Path
import uvicorn
from api.co_creation_demo import create_co_creation_demo
from api.dependencies import get_workflow_engine
from api.schemas.co_creation import RequestInput, DecisionInput
from core.co_creation import CoCreation

def create_apply_demo(directory: Path):
    app=create_co_creation_demo(directory)
    app.title='共创意图应用 · 人工样例，不调用模型'
    creation=CoCreation(app.dependency_overrides[get_workflow_engine]())
    project='comparison-demo'
    if not creation.read(project).records:
        for stage in ('script_generation','storyboard'):
            w=creation.read(project)
            field=next((f for f in w.fields[stage] if f.path[-1]=='dialogue'),w.fields[stage][0])
            saved=creation.save(project,RequestInput(expected_token=w.token,request_id=uuid.uuid4().hex,stage=stage,path=field.path,
                message='人工样例：让这处文字更坚定，修改前核对范围。',preserve='人物关系、其他字段和镜头时长保持。'))
            creation.save(project,DecisionInput(expected_token=saved.workspace.token,request_id=uuid.uuid4().hex,intent_id=saved.intent_id,decision='confirm',acknowledged=True))
    return app

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8010)
    args=parser.parse_args()
    uvicorn.run(create_apply_demo(args.directory.resolve()),host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
