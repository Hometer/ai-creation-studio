"""Isolated genuine local film composition with artificial inputs; no model calls."""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
import subprocess
from uuid import uuid4
import uvicorn
from api.media_demo import create_demo
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library,get_media_archive
from api.routers.compositions import router,service
from api.routers.project_goals import router as goals_router
from api.schemas.assets import UploadSource,VersionInput
from core.compositions import Compositions
from core.composition_worker import CompositionWorker

def create_composition_demo(directory):
    directory=Path(directory);app=create_demo(directory)
    engine=app.dependency_overrides[get_workflow_engine]();library=app.dependency_overrides[get_asset_library]();archive=app.dependency_overrides[get_media_archive]()
    if engine.get_status_snapshot('composition-demo') is None:
        engine.create_session('composition-demo',{'idea':'人工色块：蓝色视频（440Hz音调）与黄色静音视频，本机合成，无模型效果。','project_name':'人工成片合成核对','target_duration':3,'video_ratio':'16:9'})
        engine.get_state('composition-demo').artifacts={'storyboard':{'episodes':[{'episode_number':1,'segments':[
            {'segment_id':'blue','summary':'人工蓝色，2秒，440Hz音调','shots':[{'shot_number':1,'duration':2,'content':'人工蓝色色块'}]},
            {'segment_id':'gold','summary':'人工黄色，1秒，静音','shots':[{'shot_number':1,'duration':1,'content':'人工黄色色块'}]}]}]}}
        engine.save_session_to_disk('composition-demo')
    for color,seconds,audio in [('royalblue',2,True),('gold',1,False)]:
        path=directory/('composition-'+color+'.mp4')
        if not path.exists():
            command=['ffmpeg','-v','error','-f','lavfi','-i','color=c='+color+':s=320x180:r=10']
            if audio:command+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-c:a','aac']
            command+=['-t',str(seconds),'-c:v','libx264','-pix_fmt','yuv420p','-y',str(path)]
            subprocess.run(command,capture_output=True,check=True,timeout=10)
        name='人工'+('蓝色有声音' if audio else '黄色静音')
        if not any(v.name==name for a in library.read().assets.values() for v in a.versions):
            with archive.prepare(path,'video') as (temporary,details):
                library.upload_media(UploadSource(project_id='composition-demo',upload_id=uuid4().hex),'video',VersionInput(name=name,definition='人工色块，本机程序制作，非模型生成。'),archive,temporary,details)
    app.include_router(router);app.include_router(goals_router);app.title='合成与导出 · 人工样例，无模型生成'
    films=Compositions(engine,library,archive);app.dependency_overrides[service]=lambda:films
    @asynccontextmanager
    async def lifespan(_):
        worker=CompositionWorker(films);worker.start()
        try:yield
        finally:worker.stop()
    app.router.lifespan_context=lifespan
    return app,films

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--port',type=int,default=8019);args=parser.parse_args()
    app,_=create_composition_demo(args.directory.resolve());uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
if __name__=='__main__':main()
