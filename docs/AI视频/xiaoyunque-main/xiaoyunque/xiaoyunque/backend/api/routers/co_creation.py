"""Local co-creation records; no provider execution."""
from fastapi import APIRouter, Depends
from api.dependencies import get_workflow_engine
from api.schemas.assets import ProjectId
from api.schemas.co_creation import RequestInput, DecisionInput, CreationWorkspace, CreationSaved, ApplyInput, ApplySave, ApplicationWorkspace, ApplyPreview, Applied
from core.co_creation import CoCreation

router = APIRouter(tags=['对话共创意图（本地，不调用模型）'])

def service(engine=Depends(get_workflow_engine)):
    return CoCreation(engine)

@router.get('/api/project/{project_id}/co-creation', response_model=CreationWorkspace)
def read(project_id: ProjectId, creation=Depends(service)):
    return creation.read(project_id)

@router.post('/api/project/{project_id}/co-creation', response_model=CreationSaved, status_code=201)
def create(project_id: ProjectId, payload: RequestInput, creation=Depends(service)):
    return creation.save(project_id, payload)

@router.post('/api/project/{project_id}/co-creation/decision', response_model=CreationSaved, status_code=201)
def decision(project_id: ProjectId, payload: DecisionInput, creation=Depends(service)):
    return creation.save(project_id, payload)

@router.get('/api/project/{project_id}/co-creation/applications', response_model=ApplicationWorkspace)
def applications(project_id: ProjectId, creation=Depends(service)):
    return creation.applications(project_id)

@router.post('/api/project/{project_id}/co-creation/applications/preview', response_model=ApplyPreview)
def preview_application(project_id: ProjectId, payload: ApplyInput, creation=Depends(service)):
    return creation.preview_application(project_id, payload)

@router.post('/api/project/{project_id}/co-creation/applications', response_model=Applied, status_code=201)
def apply(project_id: ProjectId, payload: ApplySave, creation=Depends(service)):
    return creation.apply(project_id, payload)
