from fastapi import APIRouter,Depends
from api.dependencies import get_workflow_engine
from api.schemas.assets import ProjectId
from api.schemas.project_goals import GoalView,GoalInput
from api.errors import ErrorResponse
from core.project_goals import ProjectGoals
router=APIRouter(tags=['首版项目命名与目标'],responses={503:{'model':ErrorResponse}})
@router.get('/api/project/{project_id}/goal',response_model=GoalView)
def read(project_id:ProjectId,engine=Depends(get_workflow_engine)):return ProjectGoals(engine).read(project_id)
@router.post('/api/project/{project_id}/goal',response_model=GoalView)
def save(project_id:ProjectId,payload:GoalInput,engine=Depends(get_workflow_engine)):return ProjectGoals(engine).save(project_id,payload)
