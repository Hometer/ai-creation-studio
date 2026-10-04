"""Wan REST protocol adapter with no network implementation in this slice.

Only explicitly offline transports can be injected. Public execution remains closed.
Raw provider messages, prompts and signed links never enter errors or checkpoints.
"""

import hashlib
import json
import os
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter

from api.schemas.assets import Contract
from api.schemas.model_preflight import WanRequest
from api.schemas.wan_replay import ProviderId, VideoResult
from core.media_archive import MediaArchive, VIDEO_LIMIT, sync_directory

SUBMIT_PATH = "/services/aigc/video-generation/video-synthesis"
JSON_LIMIT = 256 * 1024
TIMEOUTS = (5, 20)
TOTAL_SECONDS = 30
STATES = {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


class WanAdapterError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class Reply:
    status: int
    body: bytes = field(repr=False)


@dataclass
class Observation:
    task_id: str
    state: str
    usage_seconds: int | None = None
    video_count: int | None = None
    video_url: str | None = field(default=None, repr=False)


class Usage(Contract):
    duration: int = Field(ge=1, le=15)
    video_count: int = Field(ge=1, le=1)


def safe_result_url(value):
    """A project privacy allowlist, not the complete vendor domain inventory."""
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError("Invalid result link")
        parts = urlsplit(value)
        if (parts.scheme != "https" or parts.hostname != "dashscope-result-sh.oss-cn-shanghai.aliyuncs.com"
                or parts.netloc != parts.hostname or parts.fragment or not parts.path.endswith(".mp4")):
            raise ValueError("Unreviewed result location")
        return value
    except (ValueError, TypeError):
        raise WanAdapterError("RESULT_LOCATION_UNSUPPORTED") from None


def decode_reply(reply, expected_task_id=None):
    try:
        if type(reply.status) is not int or reply.status != 200 or type(reply.body) is not bytes or len(reply.body) > JSON_LIMIT:
            raise ValueError("Invalid response")
        from core.asset_library import unique_fields

        body = json.loads(reply.body, object_pairs_hook=unique_fields)
        if body.get("code") not in (None, ""):
            raise ValueError("Provider rejected the request")
        output = body["output"]
        task_id = TypeAdapter(ProviderId).validate_python(output["task_id"], strict=True)
        state = output["task_status"]
        if type(state) is not str or state not in STATES or (expected_task_id is not None and task_id != expected_task_id):
            raise ValueError("Invalid task identity or state")
        result = Observation(task_id=task_id, state=state)
        if state == "SUCCEEDED":
            result.video_url = safe_result_url(output["video_url"])
            usage = body["usage"]
            checked = Usage.model_validate({"duration": usage["duration"], "video_count": usage["video_count"]})
            result.usage_seconds, result.video_count = checked.duration, checked.video_count
        return result
    except WanAdapterError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise WanAdapterError("PROVIDER_RESPONSE_INVALID") from None


def request_digest(request: WanRequest):
    data = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def validate_local_result(path: Path):
    # Reuse the existing archive's true-type and bounded ffprobe verification.
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= VIDEO_LIMIT:
                raise ValueError("Invalid result file")
            digest, size = hashlib.sha256(), 0
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > VIDEO_LIMIT:
                    raise ValueError("Result grew during verification")
                digest.update(chunk)
            if size != info.st_size:
                raise ValueError("Result changed during verification")
        details = MediaArchive._video(path)
        return VideoResult(sha256=digest.hexdigest(), byte_size=info.st_size,
                           width=details["width"], height=details["height"], duration=details["duration"])
    except Exception:
        raise WanAdapterError("RESULT_INVALID") from None


class WanAdapter:
    """Default closed; fake contract exposes timeouts/limits but cannot use a Key."""

    def __init__(self, transport=None, clock=time.monotonic):
        self.transport, self.clock = transport, clock

    def initialize(self):
        if self.transport is None or getattr(self.transport, "offline_only", None) is not True:
            raise WanAdapterError("REAL_EXECUTION_DISABLED")
        try:
            self.transport.initialize()
        except Exception:
            raise WanAdapterError("ADAPTER_INITIALIZATION_FAILED") from None

    def _request(self, method, path, body=None):
        self.initialize()
        started = self.clock()
        try:
            reply = self.transport.request(method, path, body=body,
                headers={"Content-Type": "application/json", **({"X-DashScope-Async": "enable"} if method == "POST" else {})},
                timeouts=TIMEOUTS, total_seconds=TOTAL_SECONDS, max_bytes=JSON_LIMIT, allow_redirects=False)
            if self.clock() - started > TOTAL_SECONDS:
                raise TimeoutError()
            return reply
        except Exception:
            raise WanAdapterError("TRANSPORT_UNAVAILABLE") from None

    def submit(self, request: WanRequest):
        # No loop, idempotency header or vendor wait() here.
        checked = WanRequest.model_validate(request.model_dump(mode="json"))
        reply = decode_reply(self._request("POST", SUBMIT_PATH, checked.model_dump(mode="json")))
        if reply.state not in {"PENDING", "RUNNING"}:
            raise WanAdapterError("PROVIDER_RESPONSE_INVALID")
        return reply

    def query(self, task_id):
        try:
            checked = TypeAdapter(ProviderId).validate_python(task_id, strict=True)
        except ValueError:
            raise WanAdapterError("PROVIDER_TASK_ID_REQUIRED") from None
        return decode_reply(self._request("GET", f"/tasks/{checked}"), checked)

    def download(self, video_url, directory: Path):
        self.initialize()
        url = safe_result_url(video_url)
        directory = Path(directory)
        if directory.is_symlink():
            raise WanAdapterError("RESULT_STORE_UNAVAILABLE")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = directory / "result.mp4"
        if destination.exists() or destination.is_symlink():
            return validate_local_result(destination)
        descriptor, name = tempfile.mkstemp(prefix=".download-", suffix=".mp4", dir=directory)
        temporary = Path(name)
        started = self.clock()
        try:
            with os.fdopen(descriptor, "wb") as output:
                # Downloads never inherit Authorization, cookies, retries or redirects.
                with self.transport.open_download(url, headers={}, timeouts=TIMEOUTS,
                        total_seconds=TOTAL_SECONDS, max_bytes=VIDEO_LIMIT, allow_redirects=False) as response:
                    length = response.content_length
                    if response.status != 200 or (length is not None and (type(length) is not int or not 0 < length <= VIDEO_LIMIT)):
                        raise WanAdapterError("DOWNLOAD_RESPONSE_INVALID")
                    size = 0
                    for chunk in response.chunks():
                        if self.clock() - started > TOTAL_SECONDS or type(chunk) is not bytes:
                            raise WanAdapterError("DOWNLOAD_RESPONSE_INVALID")
                        size += len(chunk)
                        if size > VIDEO_LIMIT:
                            raise WanAdapterError("DOWNLOAD_TOO_LARGE")
                        output.write(chunk)
                    if self.clock() - started > TOTAL_SECONDS or not size or (length is not None and length != size):
                        raise WanAdapterError("DOWNLOAD_RESPONSE_INVALID")
                    output.flush()
                    os.fsync(output.fileno())
            details = validate_local_result(temporary)
            os.link(temporary, destination, follow_symlinks=False)
            sync_directory(directory)
            return details
        except Exception:
            raise WanAdapterError("DOWNLOAD_UNAVAILABLE") from None
        finally:
            temporary.unlink(missing_ok=True)
