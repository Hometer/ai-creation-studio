"""Quotes and durable task checkpoints; real model execution stays disabled."""

from fastapi import APIRouter, Depends

from api.errors import ErrorResponse
from api.routers.generation_plans import get_generation_plans
from api.schemas.assets import Id, ProjectId
from api.schemas.generation_tasks import BudgetView, EnqueueInput, QuoteConfirm, QuoteInput, QuoteView, TaskInput, TaskView
from core.generation_tasks import GenerationTasks

router = APIRouter(tags=["费用与持久化任务"], responses={503: {"model": ErrorResponse}})


def get_task_provider():
    """A public request or saved API key cannot enable a paid provider."""
    return None


def get_generation_tasks(plans=Depends(get_generation_plans), provider=Depends(get_task_provider)):
    return GenerationTasks(plans, provider)


@router.post("/api/generation-quotes", response_model=QuoteView, status_code=201)
def create_quote(payload: QuoteInput, tasks=Depends(get_generation_tasks)):
    return tasks.quote(payload)


@router.get("/api/generation-quotes/{quote_id}", response_model=QuoteView)
def get_quote(quote_id: Id, tasks=Depends(get_generation_tasks)):
    return tasks.get_quote(quote_id)


@router.post("/api/generation-quotes/{quote_id}/confirm", response_model=QuoteView)
def confirm_quote(quote_id: Id, payload: QuoteConfirm, tasks=Depends(get_generation_tasks)):
    return tasks.confirm_quote(quote_id, payload)


@router.post("/api/generation-tasks", response_model=TaskView, status_code=201)
def create_task(payload: TaskInput, tasks=Depends(get_generation_tasks)):
    return tasks.create(payload)


@router.get("/api/generation-tasks", response_model=list[TaskView])
def list_all_tasks(tasks=Depends(get_generation_tasks)):
    return tasks.list_all()


@router.get("/api/generation-tasks/{task_id}", response_model=TaskView)
def get_task(task_id: Id, tasks=Depends(get_generation_tasks)):
    return tasks.get(task_id)


@router.get("/api/project/{project_id}/generation-tasks", response_model=list[TaskView])
def list_tasks(project_id: ProjectId, tasks=Depends(get_generation_tasks)):
    return tasks.list(project_id)


@router.get("/api/project/{project_id}/generation-budget", response_model=BudgetView)
def project_budget(project_id: ProjectId, tasks=Depends(get_generation_tasks)):
    return tasks.budget(project_id)


@router.post("/api/generation-tasks/{task_id}/enqueue", response_model=TaskView)
def enqueue_task(task_id: Id, payload: EnqueueInput, tasks=Depends(get_generation_tasks)):
    return tasks.enqueue(task_id, payload)


@router.post("/api/generation-tasks/{task_id}/advance", response_model=TaskView)
def advance_task(task_id: Id, tasks=Depends(get_generation_tasks)):
    return tasks.advance(task_id)
