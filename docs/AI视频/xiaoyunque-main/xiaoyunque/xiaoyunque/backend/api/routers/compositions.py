from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library,get_media_archive
from api.schemas.assets import ProjectId, Id
from api.schemas.release_media import MediaAction
from api.schemas.compositions import CompositionWorkspace, CompositionPrepare, CompositionView, CompositionConfirm
from api.errors import ErrorResponse
from core.compositions import Compositions
router=APIRouter(tags=['首版合成与导出'],responses={503:{'model':ErrorResponse}})
BASE='/api/project/{project_id}/compositions'
def service(engine=Depends(get_workflow_engine),library=Depends(get_asset_library),archive=Depends(get_media_archive)):
    return Compositions(engine,library,archive)
@router.get(BASE,response_model=CompositionWorkspace)
def workspace(project_id:ProjectId,s=Depends(service)):return s.workspace(project_id)
@router.post(BASE,response_model=CompositionView,status_code=201)
def prepare(project_id:ProjectId,payload:CompositionPrepare,s=Depends(service)):return s.prepare(project_id,payload)
@router.get(BASE+'/{job_id}',response_model=CompositionView)
def read(project_id:ProjectId,job_id:Id,s=Depends(service)):return s.get(project_id,job_id)
@router.post(BASE+'/{job_id}/confirm',response_model=CompositionView)
def confirm(project_id:ProjectId,job_id:Id,payload:CompositionConfirm,s=Depends(service)):return s.confirm(project_id,job_id,payload)
@router.post(BASE+'/{job_id}/stop',response_model=CompositionView)
def stop(project_id:ProjectId,job_id:Id,payload:MediaAction,s=Depends(service)):return s.stop(project_id,job_id)
@router.get(BASE+'/{job_id}/media',response_class=FileResponse)
def media(project_id:ProjectId,job_id:Id,download:bool=False,s=Depends(service)):
    return FileResponse(s.output_path(project_id,job_id),media_type='video/mp4',filename='film-'+job_id+'.mp4',content_disposition_type='attachment' if download else 'inline',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
