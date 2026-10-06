"""Explicit preparation, per-call approval and separate document selection."""
from fastapi import APIRouter,BackgroundTasks,Depends
from api.dependencies import get_workflow_engine
from api.schemas.assets import Id,ProjectId
from api.schemas.initial_drafts import InitialPrepare,InitialConfirm,InitialSelect,InitialView,InitialWorkspace
from core.initial_drafts import InitialDrafts
router=APIRouter(tags=['剧本与分镜候选'])
def service(engine=Depends(get_workflow_engine)):return InitialDrafts(engine)
@router.get('/api/project/{project_id}/initial-drafts',response_model=InitialWorkspace)
def read(project_id:ProjectId,drafts=Depends(service)):return drafts.workspace(project_id)
@router.post('/api/project/{project_id}/initial-drafts',response_model=InitialView,status_code=201)
def prepare(project_id:ProjectId,payload:InitialPrepare,drafts=Depends(service)):return drafts.prepare(project_id,payload)
@router.get('/api/project/{project_id}/initial-drafts/{job_id}',response_model=InitialView)
def get(project_id:ProjectId,job_id:Id,drafts=Depends(service)):return drafts.get(project_id,job_id)
@router.post('/api/project/{project_id}/initial-drafts/{job_id}/confirm',response_model=InitialView)
def confirm(project_id:ProjectId,job_id:Id,payload:InitialConfirm,tasks:BackgroundTasks,drafts=Depends(service)):
    result=drafts.confirm(project_id,job_id,payload)
    if result.status=='queued':tasks.add_task(drafts.advance,project_id,job_id)
    return result
@router.post('/api/project/{project_id}/initial-drafts/{job_id}/select',response_model=InitialView)
def select(project_id:ProjectId,job_id:Id,payload:InitialSelect,drafts=Depends(service)):return drafts.select(project_id,job_id,payload)
