"""Local estimates, sample attestations and planning allocations; no paid calls."""

from fastapi import APIRouter, Depends

from api.errors import ErrorResponse
from api.routers.generation_plans import get_generation_plans
from api.routers.model_preflight import get_preflight_connection
from api.schemas.assets import Id
from api.schemas.generation_readiness import (
    BudgetInput, BudgetPolicy, BudgetView, CancelInput, PreparationInput, PreparationView,
    PriceInput, PriceView, QuoteInput, QuoteView, ReviewInput, ReviewView,
)
from core.generation_readiness import GenerationReadiness

router = APIRouter(tags=["万相报价、额度与样例准备（不收费）"], responses={503: {"model": ErrorResponse}})


def get_generation_readiness(plans=Depends(get_generation_plans), connection=Depends(get_preflight_connection)):
    return GenerationReadiness(plans, connection)


@router.post("/api/wan-prices", response_model=PriceView, status_code=201)
def price(payload: PriceInput, service=Depends(get_generation_readiness)):
    return service.price(payload)


@router.get("/api/wan-prices/{identifier}", response_model=PriceView)
def get_price(identifier: Id, service=Depends(get_generation_readiness)):
    return service.get_price(identifier)


@router.post("/api/wan-budget-policies", response_model=BudgetPolicy, status_code=201)
def budget(payload: BudgetInput, service=Depends(get_generation_readiness)):
    return service.budget(payload)


@router.get("/api/wan-budget", response_model=BudgetView)
def get_budget(service=Depends(get_generation_readiness)):
    return service.get_budget()


@router.post("/api/generation-plans/{plan_id}/sample-reviews", response_model=ReviewView, status_code=201)
def review(plan_id: Id, payload: ReviewInput, service=Depends(get_generation_readiness)):
    return service.review(plan_id, payload)


@router.get("/api/wan-sample-reviews/{identifier}", response_model=ReviewView)
def get_review(identifier: Id, service=Depends(get_generation_readiness)):
    return service.get_review(identifier)


@router.post("/api/wan-quotes", response_model=QuoteView, status_code=201)
def quote(payload: QuoteInput, service=Depends(get_generation_readiness)):
    return service.quote(payload)


@router.get("/api/wan-quotes/{identifier}", response_model=QuoteView)
def get_quote(identifier: Id, service=Depends(get_generation_readiness)):
    return service.get_quote(identifier)


@router.post("/api/wan-preparations", response_model=PreparationView, status_code=201)
def prepare(payload: PreparationInput, service=Depends(get_generation_readiness)):
    return service.prepare(payload)


@router.get("/api/wan-preparations/{identifier}", response_model=PreparationView)
def get_preparation(identifier: Id, service=Depends(get_generation_readiness)):
    return service.get_preparation(identifier)


@router.post("/api/wan-preparations/{identifier}/cancel", response_model=PreparationView)
def cancel(identifier: Id, payload: CancelInput, service=Depends(get_generation_readiness)):
    return service.cancel(identifier, payload)
