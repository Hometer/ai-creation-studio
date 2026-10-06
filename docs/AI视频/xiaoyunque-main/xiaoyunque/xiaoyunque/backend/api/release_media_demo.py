"""Persistent artificial media provider and isolated real files; no vendor calls."""
import argparse
import threading
from contextlib import asynccontextmanager
import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4
import uvicorn
from PIL import Image
from api.media_demo import create_demo
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library,get_media_archive
from api.routers.release_media import router,service
from api.routers.creation_candidates import router as candidate_router,service as candidate_service
from api.schemas.assets import UploadSource,VersionInput,ReferenceInput
from api.schemas.generation_plans import PlanInput,ConfirmInput
from core.asset_library import now
from core.creation_candidates import CreationCandidates
from api.creation_candidate_demo import ArtificialCandidateProvider
from core.generation_plans import GenerationPlans
from core.release_media import ReleaseMedia
from core.model_preflight import check_connection
from core.persistence import atomic_write_session
from core.content_revisions import digest

class ArtificialMediaProvider:
    simulation_only=True
    def __init__(self,directory):
        self.directory=Path(directory);self.mode='success';self.ready=True;self.lock=threading.RLock()
    def connection(self):return check_connection('artificial-media-fixture','https://dashscope.aliyuncs.com/api/v1')
    def initialize(self):
        if not self.ready:raise ValueError('Artificial initialization failure')
        return self
    def submit(self,job,body,transport):
        with self.lock:return self._submit(job,body,transport)
    def _submit(self,job,body,transport):
        if digest(body)!=job.plan.request_digest:raise ValueError('Approval differs')
        p=self.directory/'artificial-provider.json';data=json.loads(p.read_text()) if p.exists() else {'schema_version':1,'jobs':{}}
        if job.job_id in data['jobs']:raise ValueError('Duplicate artificial submission')
        task='artificial-'+job.job_id
        data['jobs'][job.job_id]={'task_id':task,'kind':job.plan.kind,'duration':job.plan.duration,'request_digest':digest(body),'submitted_at':now()}
        atomic_write_session(p,data)
        if self.mode=='lost-submit':raise TimeoutError('Artificial lost reply')
        return task
    def query(self,job,transport):
        data=json.loads((self.directory/'artificial-provider.json').read_text())['jobs'][job.job_id]
        if data['task_id']!=job.provider_task_id:raise ValueError('Foreign task')
        if self.mode=='query-timeout':raise TimeoutError('Artificial query interruption')
        if self.mode=='pending':return 'RUNNING',None,None
        if self.mode=='unknown':return 'UNKNOWN',None,None
        if self.mode=='failed':return 'FAILED',None,None
        return 'SUCCEEDED','https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/artificial.'+('png' if job.plan.kind=='image' else 'mp4'),job.plan.estimate_fen
    def download(self,job,directory,transport):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        out=directory/('result.png' if job.plan.kind=='image' else 'result.mp4')
        if not out.exists():
            if self.mode=='bad-media':out.write_bytes(b'artificial-invalid-format')
            else:shutil.copyfile(self.directory/('artificial-result.png' if job.plan.kind=='image' else 'artificial-result.mp4'),out)
        return out

def create_release_demo(directory):
    directory=Path(directory);app=create_demo(directory)
    app.title='首版媒体修订 · 人工接口样例，无模型调用'
    engine=app.dependency_overrides[get_workflow_engine]();library=app.dependency_overrides[get_asset_library]();archive=app.dependency_overrides[get_media_archive]();plans=GenerationPlans(engine,library,archive)
    # Add, never overwrite, the dedicated artificial files.
    image=directory/'artificial-input.png'
    if not image.exists():Image.new('RGB',(640,640),'gold').save(image)
    result=directory/'artificial-result.png'
    if not result.exists():Image.new('RGB',(640,640),'royalblue').save(result)
    video=directory/'artificial-result.mp4'
    if not video.exists():subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=royalblue:s=320x180:r=10','-t','2','-c:v','libx264','-pix_fmt','yuv420p','-y',str(video)],capture_output=True,check=True,timeout=10)
    data=library.read()
    ref=next((r for r in data.references.values() if r.project_id=='media-demo' and r.segment_id=='seg-1'),None)
    if ref is None:
        with archive.prepare(image,'image') as (temporary,details):asset=library.upload_media(UploadSource(project_id='media-demo',upload_id=uuid4().hex),'image',VersionInput(name='人工图片输入',definition='本机人工色块，验证确定性流程，不代表模型结果'),archive,temporary,details)
        ref=library.create_reference(ReferenceInput(project_id='media-demo',segment_id='seg-1',shot_number=1,asset_id=asset.asset_id,version_id=asset.versions[0].version_id))
    if not plans.list('media-demo'):
        plan=plans.create(PlanInput(project_id='media-demo',options={'model':'wan2.7-i2v','mode':'first_frame','resolution':'720P','video_ratio':'1:1'},targets=[{'segment_id':'seg-1','duration':2,'bindings':[{'reference_id':ref.reference_id,'role':'first_frame'}]}]))
        plans.confirm(plan.plan_id,ConfirmInput(content_hash=plan.content_hash,local_only=True))
    media=ReleaseMedia(engine,library,archive,plans,ArtificialMediaProvider(directory));app.include_router(router);app.include_router(candidate_router)
    app.dependency_overrides[service]=lambda:media
    app.dependency_overrides[candidate_service]=lambda:CreationCandidates(engine,ArtificialCandidateProvider())
    from core.release_media_worker import MediaWorker
    @asynccontextmanager
    async def lifespan(_):
        worker=MediaWorker(media);worker.start()
        try:yield
        finally:worker.stop()
    app.router.lifespan_context=lifespan
    return app,media

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--port',type=int,default=8018);a=p.parse_args()
    app,_=create_release_demo(a.directory.resolve());uvicorn.run(app,host='127.0.0.1',port=a.port,access_log=False)
if __name__=='__main__':main()
