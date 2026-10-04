"""Local planning APIs. No model execution route exists here."""

from fastapi import APIRouter, Depends

from api.dependencies import get_workflow_engine
from api.errors import ErrorResponse
from api.routers.assets import get_asset_library, get_media_archive
from api.schemas.assets import Id, ProjectId
from api.schemas.generation_plans import ConfirmInput, PlanInput, PlanView, PreparedPreview
from core.generation_plans import GenerationPlans

router = APIRouter(tags=["生成计划（仅本地准备）"], responses={503: {"model": ErrorResponse}})


def get_generation_plans(engine=Depends(get_workflow_engine), library=Depends(get_asset_library),
                         archive=Depends(get_media_archive)):
    return GenerationPlans(engine, library, archive)


@router.post("/api/generation-plans", response_model=PlanView, status_code=201)
def create_plan(payload: PlanInput, plans=Depends(get_generation_plans)):
    return plans.create(payload)


@router.get("/api/project/{project_id}/generation-plans", response_model=list[PlanView])
def list_plans(project_id: ProjectId, plans=Depends(get_generation_plans)):
    return plans.list(project_id)


@router.get("/api/generation-plans/{plan_id}", response_model=PlanView)
def get_plan(plan_id: Id, plans=Depends(get_generation_plans)):
    return plans.get(plan_id)


@router.post("/api/generation-plans/{plan_id}/confirm", response_model=PlanView)
def confirm_plan(plan_id: Id, payload: ConfirmInput, plans=Depends(get_generation_plans)):
    return plans.confirm(plan_id, payload)


@router.post("/api/generation-plans/{plan_id}/dry-run", response_model=PreparedPreview)
def prepare_plan(plan_id: Id, plans=Depends(get_generation_plans)):
    _, preview = plans.prepare(plan_id)
    return preview
