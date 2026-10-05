"""A read-only, plan-scoped snapshot of local preparation history."""

from api.schemas.assets import Contract
from api.schemas.execution_checks import ExecutionCheckView
from api.schemas.generation_plans import PlanView
from api.schemas.generation_readiness import BudgetView, PreparationView, PriceView, QuoteView, ReviewView


class ReadinessWorkspace(Contract):
    plan: PlanView
    project_exists: bool
    current_price: PriceView | None
    budget: BudgetView
    reviews: list[ReviewView]
    quotes: list[QuoteView]
    preparations: list[PreparationView]
    checks: list[ExecutionCheckView]
