"""No-charge, local-only text revision workspace."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from api.dependencies import get_workflow_engine
from api.schemas.assets import ProjectId, Id
from api.schemas.content_revisions import RevisionInput, SaveRevision, Workspace, Preview, Saved, Editor, Stage
from api.schemas.content_revisions import Comparison, ComparisonInput
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

@router.get('/api/project/{project_id}/content-revisions/compare', response_model=Comparison)
def compare(project_id: ProjectId, before_version_id: Id, after_version_id: Id,
            expected_token: str = Query(pattern=r'^[a-f0-9]{64}$'), revisions=Depends(service)):
    return revisions.compare(project_id, ComparisonInput(expected_token=expected_token,
        before_version_id=before_version_id, after_version_id=after_version_id))

@router.get('/api/project/{project_id}/content-revisions/export', response_class=Response,
            responses={200: {'content': {'application/json': {}, 'text/plain': {}}, 'description': 'UTF-8 text snapshot attachment'},
                       404: {'description': 'Project missing'}, 409: {'description': 'Revision changed'},
                       422: {'description': 'Invalid format, unsupported content or export limit'},
                       503: {'description': 'Revision storage unavailable'}})
def export(project_id: ProjectId, format: str = Query(pattern=r'^(json|text)$'),
           expected_token: str = Query(pattern=r'^[a-f0-9]{64}$'), revisions=Depends(service)):
    from core.revision_exports import RevisionExports
    result = RevisionExports(revisions.engine).download(project_id, expected_token, format)
    return Response(result.body, media_type=result.media_type, headers=result.headers)

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


from fastapi import Request
from starlette.concurrency import run_in_threadpool
from api.schemas.revision_import_precheck import PrecheckReport

@router.post('/api/project/{project_id}/content-revisions/import-precheck', response_model=PrecheckReport,
             responses={404:{'description':'Project missing'},409:{'description':'Target changed'},
                        413:{'description':'Backup size limit'},415:{'description':'Unsupported file type'},
                        422:{'description':'Invalid backup'},503:{'description':'Target storage unavailable'}})
async def import_precheck(project_id:ProjectId,request:Request,
        expected_token:str=Query(pattern=r'^[a-f0-9]{64}$'),filename:str=Query(min_length=1,max_length=128),revisions=Depends(service)):
    from core.revision_import_precheck import RevisionImportPrecheck
    body = await read_backup_body(request, filename)
    return await run_in_threadpool(RevisionImportPrecheck(revisions.engine).inspect,project_id,expected_token,filename,body)

async def read_backup_body(request: Request, filename: str):
    from core.revision_import_precheck import check_filename
    from core.revision_exports import MAX_EXPORT_BYTES
    from core.content_revisions import fail
    check_filename(filename)
    if request.headers.get('content-type','').split(';')[0].lower()!='application/json' or request.headers.get('content-encoding') not in (None,'identity'):
        fail(415,'BACKUP_TYPE','只支持未压缩的 JSON 文字备份')
    body=bytearray()
    async for chunk in request.stream():
        if len(body)+len(chunk)>MAX_EXPORT_BYTES:fail(413,'BACKUP_SIZE','文字备份不能超过 64 MiB')
        body.extend(chunk)
    return bytes(body)

from api.schemas.revision_import_preview import RestoreBackupPreview

@router.post('/api/project/{project_id}/content-revisions/import-preview', response_model=RestoreBackupPreview,
             responses={404:{'description':'Project missing'},409:{'description':'Target changed or running'},
                        413:{'description':'Backup size limit'},415:{'description':'Unsupported file type'},
                        422:{'description':'Invalid backup, source project or preview limit'},503:{'description':'Target storage unavailable'}})
async def import_preview(project_id:ProjectId, request:Request,
        expected_token:str=Query(pattern=r'^[a-f0-9]{64}$'),filename:str=Query(min_length=1,max_length=128),revisions=Depends(service)):
    from core.revision_import_preview import RevisionImportPreview
    body = await read_backup_body(request, filename)
    return await run_in_threadpool(RevisionImportPreview(revisions.engine).inspect,project_id,expected_token,filename,body)
