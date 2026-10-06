"""Isolated saved draft history for human verification, no model calls."""
import argparse
import uuid
from pathlib import Path
import uvicorn
from api.co_creation_draft_demo import create_draft_demo
from api.dependencies import get_workflow_engine
from api.schemas.co_creation import DraftInput
from core.co_creation import CoCreation

def create_history_demo(directory: Path):
    app=create_draft_demo(directory)
    app.title='历史草稿选择与恢复 · 人工样例，不调用模型'
    creation=CoCreation(app.dependency_overrides[get_workflow_engine]())
    project='comparison-demo'
    if not creation.drafts(project).drafts:
        for intent in creation.read(project).records:
            candidates=['人工历史草稿：雾港来信','', '人工最近草稿：雾散之后，再一起出发。'] if intent.stage=='script_generation' else ['人工分镜旧草稿：我等你回来。','人工分镜最近草稿：雾散后，一起上船。']
            for value in candidates:
                workspace=creation.read(project)
                creation.save_draft(project,DraftInput(expected_token=workspace.token,intent_id=intent.intent_id,value=value,request_id=uuid.uuid4().hex))
    return app

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8012)
    args=parser.parse_args()
    uvicorn.run(create_history_demo(args.directory.resolve()),host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
