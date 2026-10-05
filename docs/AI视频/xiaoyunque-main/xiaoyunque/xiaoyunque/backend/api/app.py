import logging
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
    yield
    logger.info("XiaoYunQue API shutdown complete")


app = FastAPI(
    title="XiaoYunQue", version="2.0.0", lifespan=lifespan,
    responses={status: {"model": ErrorResponse} for status in HTTP_ERRORS},
)
register_error_handlers(app)

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
logger.info("API routers registered")


@app.get("/")
async def root():
    return {"service": "XiaoYunQue", "version": "2.0.0", "health": "/api/health"}
