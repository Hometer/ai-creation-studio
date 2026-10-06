from fastapi import APIRouter,Depends
from api.dependencies import get_workflow_engine
from api.routers.assets import get_asset_library,get_media_archive
from api.schemas.release_tasks import ReleaseTasks
from core.release_tasks import ReleaseTasksReader
router=APIRouter(tags=['首版任务只读汇总'])
@router.get('/api/first-release-tasks',response_model=ReleaseTasks)
def read(engine=Depends(get_workflow_engine),library=Depends(get_asset_library),archive=Depends(get_media_archive)):
    return ReleaseTasksReader(engine,library,archive).read()
