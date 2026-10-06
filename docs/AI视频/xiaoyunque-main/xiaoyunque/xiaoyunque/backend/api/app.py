import logging
import inspect
import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.services.static_results import ResultStaticFiles

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from api.logging_config import setup_concurrent_logging
from api.routers.content_revisions import router as content_revisions_router
from api.routers.co_creation import router as co_creation_router
from api.routers.creation_candidates import router as creation_candidates_router
from api.routers.release_media import router as release_media_router
from api.routers.generation_plans import router as generation_plans_router
from api.routers.generation_tasks import router as generation_tasks_router
from api.routers.generation_readiness import router as generation_readiness_router
from api.routers.execution_checks import router as execution_checks_router
from api.routers.model_preflight import router as model_preflight_router
from api.errors import ErrorResponse, HTTP_ERRORS, register_error_handlers
from config import settings

setup_concurrent_logging()

logger = logging.getLogger(__name__)

from api.routers import (
    assets_router,
    configuration_router,
    files_router,
    health_router,
    pipelines_router,
    sandbox_router,
    sessions_router,
    stages_router,
    workflow_router,
)

DEFAULT_CORS_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]


def _cors_origins() -> list[str]:
    configured = []
    for raw_origin in os.getenv("XYQ_CORS_ORIGINS", "").split(","):
        origin = raw_origin.strip().rstrip("/")
        parsed = urlparse(origin)
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.netloc
            and not parsed.path
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
        ):
            configured.append(origin)
    return list(dict.fromkeys(DEFAULT_CORS_ORIGINS + configured))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting XiaoYunQue API")
    logger.info("Code directory mounted at /code: %s", settings.CODE_DIR)
    from api.dependencies import get_workflow_engine
    from api.routers.assets import get_asset_library, get_media_archive
    from core.generation_plans import GenerationPlans
    from core.release_media import ReleaseMedia
    from core.release_media_worker import MediaWorker
    from core.compositions import Compositions
    from core.composition_worker import CompositionWorker
    async def resolve(factory, *args):
        # Background recovery must use the same data stores as the HTTP routes.
        override = app.dependency_overrides.get(factory)
        value = override() if override else factory(*args)
        return await value if inspect.isawaitable(value) else value

    engine = await resolve(get_workflow_engine)
    library = await resolve(get_asset_library)
    archive = await resolve(get_media_archive, library)
    worker = MediaWorker(ReleaseMedia(engine,library,archive,GenerationPlans(engine,library,archive)))
    films = CompositionWorker(Compositions(engine,library,archive))
    worker.start()
    films.start()
    from core.initial_drafts import InitialDrafts
    from core.initial_draft_worker import InitialWorker
    drafts = InitialWorker(InitialDrafts(engine))
    drafts.start()
    try:
        yield
    finally:
        worker.stop()
        films.stop()
        drafts.stop()
    logger.info("XiaoYunQue API shutdown complete")


app = FastAPI(
    title="XiaoYunQue", version="2.0.0", lifespan=lifespan,
    responses={status: {"model": ErrorResponse} for status in HTTP_ERRORS},
)
register_error_handlers(app)
from api.services.legacy_cost_gate import LegacyCostGate,router as cost_policy_router
app.add_middleware(LegacyCostGate)
app.include_router(cost_policy_router)

cors_origins = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("CORS enabled for origins: %s", cors_origins)

os.makedirs(settings.CODE_DIR, exist_ok=True)
app.mount("/code", ResultStaticFiles(directory=settings.CODE_DIR), name="code")

from api.routers.project_goals import router as project_goals_router
app.include_router(project_goals_router)
from api.routers.initial_drafts import router as initial_drafts_router
app.include_router(initial_drafts_router)
from api.routers.compositions import router as compositions_router
app.include_router(compositions_router)

app.include_router(health_router)
app.include_router(files_router)
app.include_router(workflow_router)
app.include_router(sessions_router)
app.include_router(stages_router)
app.include_router(sandbox_router)
app.include_router(pipelines_router)
app.include_router(configuration_router)
app.include_router(assets_router)
app.include_router(generation_plans_router)
app.include_router(generation_tasks_router)
app.include_router(model_preflight_router)
app.include_router(generation_readiness_router)
app.include_router(execution_checks_router)
app.include_router(content_revisions_router)
app.include_router(co_creation_router)
app.include_router(creation_candidates_router)
app.include_router(release_media_router)
logger.info("API routers registered")


@app.get("/")
async def root():
    return {"service": "XiaoYunQue", "version": "2.0.0", "health": "/api/health"}
