"""Public errors never include request bodies or arbitrary exception text."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
    detail: str  # Compatibility for the existing frontend.


HTTP_ERRORS = {
    400: ("BAD_REQUEST", "请求不符合要求"),
    401: ("UNAUTHORIZED", "请先完成身份验证"),
    403: ("FORBIDDEN", "没有执行此操作的权限"),
    404: ("NOT_FOUND", "请求的资源不存在"),
    409: ("CONFLICT", "当前状态不允许此操作"),
    413: ("FILE_TOO_LARGE", "上传文件超过大小限制"),
    415: ("UNSUPPORTED_MEDIA_TYPE", "不支持此文件格式"),
    422: ("VALIDATION_ERROR", "请求参数不符合要求"),
    429: ("RATE_LIMITED", "请求过于频繁，请稍后重试"),
    500: ("INTERNAL_ERROR", "服务暂时无法完成请求，请稍后重试"),
}


def error_response(status: int, code: str, message: str, headers=None) -> JSONResponse:
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message), detail=message)
    return JSONResponse(status_code=status, content=payload.model_dump(), headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    from core.asset_library import AssetLibraryError

    @app.exception_handler(AssetLibraryError)
    async def asset_error_handler(request, exc: AssetLibraryError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def handle_http_error(_request: Request, exc: HTTPException):
        code, message = HTTP_ERRORS.get(exc.status_code, ("REQUEST_FAILED", "请求未能完成"))
        return error_response(exc.status_code, code, message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, _exc: RequestValidationError):
        code, message = HTTP_ERRORS[422]
        return error_response(422, code, message)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception):
        logger.error("Request failed (%s)", type(exc).__name__)
        code, message = HTTP_ERRORS[500]
        return error_response(500, code, message)
