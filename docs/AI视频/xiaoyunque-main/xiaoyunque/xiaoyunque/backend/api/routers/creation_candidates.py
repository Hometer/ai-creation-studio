"""Explicitly confirmed candidate jobs; all reads are provider-free."""
from fastapi import APIRouter, BackgroundTasks, Depends
from api.dependencies import get_workflow_engine
from api.schemas.assets import Id, ProjectId
from api.schemas.creation_candidates import CandidatePrepare, CandidateConfirm, CandidateView, CandidateWorkspace
from core.creation_candidates import CreationCandidates

router=APIRouter(tags=['首版模型共创候选'])

def service(engine=Depends(get_workflow_engine)):
    return CreationCandidates(engine)

@router.get('/api/project/{project_id}/co-creation/candidates',response_model=CandidateWorkspace)
def read(project_id:ProjectId,creation=Depends(service)):
    return creation.workspace(project_id)

@router.post('/api/project/{project_id}/co-creation/candidates',response_model=CandidateView,status_code=201)
def prepare(project_id:ProjectId,payload:CandidatePrepare,creation=Depends(service)):
    return creation.prepare(project_id,payload)

@router.post('/api/project/{project_id}/co-creation/candidates/{job_id}/confirm',response_model=CandidateView)
def confirm(project_id:ProjectId,job_id:Id,payload:CandidateConfirm,tasks:BackgroundTasks,creation=Depends(service)):
    result=creation.confirm(project_id,job_id,payload)
    if result.status=='queued':
        tasks.add_task(creation.advance,project_id,job_id)
    return result

@router.get('/api/project/{project_id}/co-creation/candidates/{job_id}',response_model=CandidateView)
def get(project_id:ProjectId,job_id:Id,creation=Depends(service)):
    return creation.get(project_id,job_id)

@router.post('/api/project/{project_id}/co-creation/candidates/{job_id}/advance',response_model=CandidateView)
def advance(project_id:ProjectId,job_id:Id,tasks:BackgroundTasks,creation=Depends(service)):
    result=creation.get(project_id,job_id)
    if result.status=='queued':
        tasks.add_task(creation.advance,project_id,job_id)
    return result
