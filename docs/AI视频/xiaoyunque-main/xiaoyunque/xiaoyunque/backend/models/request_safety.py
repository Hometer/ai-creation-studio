"""Bounded synchronous completion requests without private diagnostic content."""

import logging
import math
import time

from openai import APIStatusError


class ModelRequestError(RuntimeError):
    """Safe internal error; API boundaries provide their existing error envelope."""


class SDKLogFilter(logging.Filter):
    """SDK debug records may contain entire bodies, headers and signed URLs."""

    def filter(self, record):
        record.msg = "SDK transport event (private details omitted)"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


_sdk_filter = SDKLogFilter()


def protect_sdk_logs():
    # These logger names are used by the installed OpenAI/httpx/httpcore SDKs.
    names = {"openai", "openai._base_client", "openai._response", "openai._legacy_response",
             "httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2",
             "httpcore.proxy", "httpcore.socks", "urllib3.connectionpool", "urllib3.util.retry",
             "urllib3.poolmanager", "dashscope"}
    names.update(name for name in list(logging.Logger.manager.loggerDict)
                 if name.startswith(("openai.", "httpcore.", "urllib3.", "dashscope.")))
    for name in names:
        logger = logging.getLogger(name)
        if _sdk_filter not in logger.filters:
            logger.addFilter(_sdk_filter)


def positive_seconds(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ModelRequestError("模型超时配置无效。") from None
    if not math.isfinite(value) or value <= 0:
        raise ModelRequestError("模型超时配置无效。")
    return value


def initialize_client(factory, kwargs, logger, proxy=None):
    protect_sdk_logs()
    try:
        if proxy:
            import httpx
            kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=kwargs["timeout"])
        return factory(**kwargs)
    except Exception as exc:
        transport = kwargs.get("http_client")
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass  # Original initialization failure remains the reported cause.
        logger.warning("Model SDK initialization failed (%s)", type(exc).__name__)
        raise ModelRequestError("模型客户端初始化失败，请检查后端配置。") from None


def completion_text(owner, request, logger, backoff=10):
    """Count every attempt, limit scheduling, and reject invalid/late results.

    HTTP timeout is an inactivity/phase timeout, not forced wall-clock cancellation.
    The monotonic deadline bounds retry scheduling and result acceptance.
    """
    timeout = positive_seconds(owner.timeout)
    deadline = time.monotonic() + positive_seconds(owner.total_timeout)
    if isinstance(owner.max_attempts, bool) or not isinstance(owner.max_attempts, int) or owner.max_attempts < 1:
        raise ModelRequestError("模型重试配置无效。")
    protect_sdk_logs()
    for attempt in range(1, owner.max_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            client = owner.client.with_options(timeout=min(timeout, remaining), max_retries=0)
            response = client.chat.completions.create(**request)
            choices = getattr(response, "choices", None)
            content = getattr(getattr(choices[0], "message", None), "content", None) if choices else None
            if not isinstance(content, str) or not content.strip():
                raise ModelRequestError("模型返回空内容或无效响应。")
            if time.monotonic() >= deadline:
                raise ModelRequestError("模型请求超过等待期限。")
            return content
        except Exception as exc:
            logger.warning("Model request failed: attempt=%d/%d category=%s",
                           attempt, owner.max_attempts, type(exc).__name__)
            # Authentication, permission, invalid input and billing need action.
            if isinstance(exc, APIStatusError) and exc.status_code not in {408, 409, 429} and exc.status_code < 500:
                raise ModelRequestError("模型请求被拒绝，请检查配置与账户。") from None
            if isinstance(exc, APIStatusError):
                body = exc.body if isinstance(exc.body, dict) else {}
                error = body.get("error", body)
                if isinstance(error, dict) and error.get("code") in {"insufficient_quota", "billing_hard_limit_reached"}:
                    raise ModelRequestError("模型账户额度不足，请检查账户。") from None
            remaining = deadline - time.monotonic()
            if attempt == owner.max_attempts or remaining <= 0:
                break
            delay = backoff(attempt) if callable(backoff) else backoff
            # Never retry earlier than the server's requested delay.
            if isinstance(exc, APIStatusError):
                retry_after = exc.response.headers.get("retry-after")
                if retry_after is not None:
                    try:
                        delay = max(delay, positive_seconds(retry_after))
                    except ModelRequestError:
                        # Unrecognized server hint: do not risk an early retry.
                        break
            if delay >= remaining:
                break
            time.sleep(delay)
    raise ModelRequestError("模型请求未获得有效结果，已停止重试。") from None
