"""No-charge, local-only text revision workspace."""
from fastapi import APIRouter, Depends
from api.dependencies import get_workflow_engine
from api.schemas.assets import ProjectId
from api.schemas.content_revisions import RevisionInput, SaveRevision, Workspace, Preview, Saved, Editor, Stage
from core.content_revisions import ContentRevisions

router = APIRouter(tags=['文字修改与版本（本地，不生成）'])

def service(engine=Depends(get_workflow_engine)):
    return ContentRevisions(engine)

@router.get('/api/project/{project_id}/content-revisions', response_model=Workspace)
def read(project_id: ProjectId, revisions=Depends(service)):
    return revisions.read(project_id)

@router.post('/api/project/{project_id}/content-revisions/preview', response_model=Preview)
def preview(project_id: ProjectId, payload: RevisionInput, revisions=Depends(service)):
    return revisions.preview(project_id, payload)

@router.post('/api/project/{project_id}/content-revisions', response_model=Saved, status_code=201)
def save(project_id: ProjectId, payload: SaveRevision, revisions=Depends(service)):
    return revisions.save(project_id, payload)

@router.get('/api/project/{project_id}/content-revisions/editor/{stage}', response_model=Editor)
def editor(project_id: ProjectId, stage: Stage, revisions=Depends(service)):
    return revisions.editor(project_id, stage)

# Manual decisions are independent from workflow confirmation and generation.
from api.routers.assets import get_asset_library
from api.schemas.impact_reviews import ReviewInput, ReviewWorkspace, ReviewSaved
from core.impact_reviews import ImpactReviews

def reviews_service(engine=Depends(get_workflow_engine), library=Depends(get_asset_library)):
    return ImpactReviews(engine, library)

@router.get('/api/project/{project_id}/content-revisions/reviews', response_model=ReviewWorkspace)
def read_reviews(project_id: ProjectId, reviews=Depends(reviews_service)):
    return reviews.read(project_id)

@router.post('/api/project/{project_id}/content-revisions/reviews', response_model=ReviewSaved, status_code=201)
def save_review(project_id: ProjectId, payload: ReviewInput, reviews=Depends(reviews_service)):
    return reviews.save(project_id, payload)
