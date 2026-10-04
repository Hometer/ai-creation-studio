"""Persisted local Wan checks, with no real provider or outbound transport."""

from fastapi import APIRouter, Depends

from api.errors import ErrorResponse
from api.routers.generation_plans import get_generation_plans
from api.schemas.assets import Id
from api.schemas.model_preflight import EmptyPreflightInput, ReportView
from config import Config
from core.model_preflight import ModelPreflight, check_connection

router = APIRouter(tags=["万相离线预检（不调用模型）"], responses={503: {"model": ErrorResponse}})


def get_preflight_connection():
    return check_connection(Config.DASHSCOPE_API_KEY, Config.DASHSCOPE_BASE_URL)


def get_model_preflight(plans=Depends(get_generation_plans), connection=Depends(get_preflight_connection)):
    return ModelPreflight(plans, connection)


@router.post("/api/generation-plans/{plan_id}/wan-preflight", response_model=ReportView, status_code=201)
def create_preflight(plan_id: Id, payload: EmptyPreflightInput | None = None, service=Depends(get_model_preflight)):
    return service.create(plan_id)


@router.get("/api/model-preflights/{report_id}", response_model=ReportView)
def get_preflight(report_id: Id, service=Depends(get_model_preflight)):
    return service.get(report_id)
