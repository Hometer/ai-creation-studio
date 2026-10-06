from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.logging_config import apply_access_log_setting, apply_log_level_setting
from config import Config
from core.content_revisions import fail
from api.errors import ErrorResponse

router = APIRouter(tags=["Configuration"])


class ConfigUpdateRequest(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


@router.get("/api/config")
async def get_config():
    return {
        "config": Config.as_public_dict(),
        "path": "backend/config.yaml",
    }


@router.put("/api/config", responses={503: {"model": ErrorResponse}})
async def update_config(req: ConfigUpdateRequest):
    try:
        Config.update_config(req.values)
    except Exception:
        fail(503, "CONFIG_SAVE_UNCONFIRMED", "配置保存未确认，请重新读取；密钥不会显示在错误中")
    apply_log_level_setting()
    apply_access_log_setting()
    return {
        "config": Config.as_public_dict(),
        "path": "backend/config.yaml",
    }
