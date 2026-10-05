"""Read all history under the existing asset lock, without writes or dispatch."""

from api.schemas.readiness_workspace import ReadinessWorkspace
from core.execution_checks import ExecutionChecks


def read_workspace(service, identifier):
    with service.plans._transaction() as (library, plans):
        plan, view = service._plan_view(plans, library, identifier)
        data = service._read()
        execution = ExecutionChecks(service)
        checks = execution._read(data)
        quotes = [q for q in data.quotes.values() if q.terms.plan_id == identifier]
        quote_ids = {q.quote_id for q in quotes}
        preparations = [p for p in data.preparations.values() if p.quote_id in quote_ids]
        price = data.prices.get(data.current_price_id)
        return ReadinessWorkspace(
            plan=view, project_exists=service.plans.engine.get_status_snapshot(plan.content.project_id) is not None,
            current_price=service._price_view(data, price) if price else None,
            budget=service._budget_view(data),
            reviews=[service._review_view(data, r, plans, library) for r in sorted(data.reviews.values(), key=lambda r: (r.reviewed_at, r.review_id), reverse=True) if r.plan_id == identifier],
            quotes=[service._quote_view(data, q, plans, library) for q in sorted(quotes, key=lambda q: (q.created_at, q.quote_id), reverse=True)],
            preparations=[service._preparation_view(data, p, plans, library) for p in sorted(preparations, key=lambda p: (p.created_at, p.preparation_id), reverse=True)],
            checks=[execution._view(c, data, plans, library) for c in sorted(checks.checks.values(), key=lambda c: (c.checked_at, c.check_id), reverse=True) if c.plan_id == identifier],
        )
