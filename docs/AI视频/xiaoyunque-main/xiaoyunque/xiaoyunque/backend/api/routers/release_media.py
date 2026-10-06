from fastapi import APIRouter,Depends,BackgroundTasks
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library,get_media_archive
from api.routers.generation_plans import get_generation_plans
from api.schemas.assets import ProjectId,Id,Contract
from api.schemas.release_media import MediaWorkspace,MediaView,MediaPrepare,MediaConfirm,MediaAction,MediaSelect
from api.errors import ErrorResponse
from core.release_media import ReleaseMedia

router=APIRouter(tags=['首版媒体修订与任务恢复'],responses={503:{'model':ErrorResponse}})
def service(engine=Depends(get_workflow_engine),library=Depends(get_asset_library),archive=Depends(get_media_archive),plans=Depends(get_generation_plans)):
    return ReleaseMedia(engine,library,archive,plans)
BASE='/api/project/{project_id}/media-revisions'
@router.get(BASE,response_model=MediaWorkspace)
def workspace(project_id:ProjectId,s=Depends(service)):return s.workspace(project_id)
@router.post(BASE,response_model=MediaView,status_code=201)
def prepare(project_id:ProjectId,payload:MediaPrepare,s=Depends(service)):return s.prepare(project_id,payload)
@router.get(BASE+'/{job_id}',response_model=MediaView)
def get(project_id:ProjectId,job_id:Id,s=Depends(service)):return s.get(project_id,job_id)
@router.post(BASE+'/{job_id}/confirm',response_model=MediaView)
def confirm(project_id:ProjectId,job_id:Id,payload:MediaConfirm,background:BackgroundTasks,s=Depends(service)):
    result=s.confirm(project_id,job_id,payload)
    if result.status=='queued':background.add_task(s.advance,project_id,job_id)
    return result
@router.post(BASE+'/{job_id}/advance',response_model=MediaView)
def advance(project_id:ProjectId,job_id:Id,payload:Contract,s=Depends(service)):return s.advance(project_id,job_id)
@router.post(BASE+'/{job_id}/resume',response_model=MediaView)
def resume(project_id:ProjectId,job_id:Id,payload:MediaAction,s=Depends(service)):return s.action(project_id,job_id,True)
@router.post(BASE+'/{job_id}/stop',response_model=MediaView)
def stop(project_id:ProjectId,job_id:Id,payload:MediaAction,s=Depends(service)):return s.action(project_id,job_id,False)
@router.post(BASE+'/{job_id}/select',response_model=MediaView)
def select(project_id:ProjectId,job_id:Id,payload:MediaSelect,s=Depends(service)):return s.select(project_id,job_id,payload)
