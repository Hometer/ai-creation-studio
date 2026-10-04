"""Empty requests cannot grant permission to send or spend."""

from fastapi import APIRouter, Depends

from api.errors import ErrorResponse
from api.routers.generation_readiness import get_generation_readiness
from api.schemas.assets import Id
from api.schemas.execution_checks import ExecutionCheckView
from api.schemas.model_preflight import EmptyPreflightInput
from core.execution_checks import ExecutionChecks

router = APIRouter(tags=["万相执行前检查（不联网、不收费）"], responses={503: {"model": ErrorResponse}})


def get_execution_checks(readiness=Depends(get_generation_readiness)):
    return ExecutionChecks(readiness)


@router.post("/api/wan-preparations/{identifier}/execution-check", response_model=ExecutionCheckView, status_code=201)
def create(identifier: Id, payload: EmptyPreflightInput, service=Depends(get_execution_checks)):
    return service.create(identifier)


@router.get("/api/wan-execution-checks/{identifier}", response_model=ExecutionCheckView)
def get(identifier: Id, service=Depends(get_execution_checks)):
    return service.get(identifier)
