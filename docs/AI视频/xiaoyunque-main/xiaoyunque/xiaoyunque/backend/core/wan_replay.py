"""Persistent, explicitly artificial protocol replay. No network or production data."""

import argparse
import base64
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from PIL import Image

from api.schemas.model_preflight import WanRequest
from api.schemas.wan_replay import FixtureDocument, ReplayDocument, ReplayRecord
from core.asset_library import AssetLibraryError
from core.media_archive import sync_directory
from core.persistence import atomic_write_session
from core.task_provider import read_document
from core.wan_adapter import Reply, WanAdapter, WanAdapterError, request_digest, validate_local_result

SCENARIOS = ("success", "lost-submit", "query-timeout", "bad-video")
TASK_ID = "fixture-wan-task-1"
RESULT_URL = "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/fixture.mp4?signature=artificial-private"
TERMINAL = {"succeeded", "failed", "canceled", "manual_review"}


def artificial_request():
    # Neither Config nor user plans are consulted. It is not submitted to a model.
    stream = BytesIO()
    Image.new("RGB", (320, 320), "gold").save(stream, format="PNG")
    media = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")
    return WanRequest.model_validate({"model": "wan2.7-i2v-2026-04-25", "input": {
        "prompt": "本地人工协议演练，非模型结果", "media": [{"type": "first_frame", "url": media}]},
        "parameters": {"duration": 5, "resolution": "720P", "prompt_extend": False, "watermark": False}})


class FixtureTransport:
    offline_only = True

    def __init__(self, directory: Path, scenario):
        self.directory, self.scenario = Path(directory), scenario

    def initialize(self):
        if self.directory.is_symlink():
            raise ValueError("Unsafe fixture store")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        video = self.directory / "fixture.mp4"
        if video.is_symlink():
            raise ValueError("Unsafe fixture file")
        if not video.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gold:s=320x180:r=10",
                            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video)],
                           capture_output=True, timeout=10, check=True)

    def _count(self, field):
        path = self.directory / "fixture.json"
        data = read_document(path, FixtureDocument)
        setattr(data, field, getattr(data, field) + 1)
        atomic_write_session(path, data.model_dump(mode="json"))
        sync_directory(self.directory)
        return getattr(data, field)

    def request(self, method, path, **options):
        # Only reads local fixtures. There is no HTTP library, SDK, Key or URL resolver.
        if method == "POST":
            self._count("submit_calls")
            if self.scenario == "lost-submit":
                raise TimeoutError("artificial lost submit; never expose this text")
            state = "PENDING"
        else:
            queries = self._count("query_calls")
            if self.scenario == "query-timeout":
                raise TimeoutError("artificial query timeout")
            state = "RUNNING" if queries == 1 else "SUCCEEDED"
        output = {"task_id": TASK_ID, "task_status": state, "orig_prompt": "private artificial prompt"}
        body = {"request_id": "fixture-trace", "output": output}
        if state == "SUCCEEDED":
            output["video_url"] = RESULT_URL
            body["usage"] = {"duration": 5, "video_count": 1, "SR": 720, "output_video_duration": 5}
        return Reply(200, json.dumps(body).encode())

    @contextmanager
    def open_download(self, url, **options):
        self._count("download_calls")
        content = b"not a video" if self.scenario == "bad-video" else (self.directory / "fixture.mp4").read_bytes()

        class Download:
            status = 200
            content_length = len(content)

            def chunks(self):
                yield content

        yield Download()


class WanReplay:
    def __init__(self, directory: Path, scenario, adapter=None, clock=time.time):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown fixture scenario")
        self.directory, self.scenario, self.clock = Path(directory), scenario, clock
        self.path = self.directory / "replay.json"
        self.adapter = adapter if adapter is not None else WanAdapter(FixtureTransport(self.directory / "fixture", scenario))

    @contextmanager
    def _locked(self):
        try:
            if self.directory.is_symlink():
                raise ValueError("Unsafe replay directory")
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    data = read_document(self.path, ReplayDocument)
                    if data.record is not None and data.record.scenario != self.scenario:
                        raise ValueError("Scenario mismatch")
                    yield data
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        except (OSError, ValueError) as exc:
            raise AssetLibraryError(503, "WAN_REPLAY_STORE_UNAVAILABLE", "离线演练状态不可用，请保留原数据") from exc

    def _save(self, data):
        checked = ReplayDocument.model_validate(data.model_dump(mode="json"))
        atomic_write_session(self.path, checked.model_dump(mode="json"))
        sync_directory(self.directory)

    def status(self):
        with self._locked() as data:
            if data.record is not None and data.record.state == "succeeded":
                self._verify_result(data.record)
            return data.record

    def _verify_result(self, record):
        try:
            observed = validate_local_result(self.directory / "result.mp4")
            if record.result is not None and observed != record.result:
                raise ValueError("Result mismatch")
            return observed
        except Exception:
            raise AssetLibraryError(503, "WAN_REPLAY_RESULT_UNAVAILABLE", "人工演练结果缺失或校验异常，请保留数据") from None

    def step(self):
        with self._locked() as data:
            if data.record is None:
                data.record = ReplayRecord(scenario=self.scenario, request_sha256=request_digest(artificial_request()),
                                           started_at=float(self.clock()))
                self._save(data)
            record = data.record
            if record.state in TERMINAL:
                if record.state == "succeeded":
                    self._verify_result(record)
                return record
            if record.state == "submitting":
                record.state, record.error_code = "manual_review", "SUBMIT_UNCERTAIN"
                self._save(data)
                return record
            # Recover already-published results before any provider call or expiry check.
            result_file = self.directory / "result.mp4"
            if record.state in {"downloading", "result_ready"} and (result_file.exists() or result_file.is_symlink()):
                try:
                    record.result = self._verify_result(record)
                    record.state, record.error_code = "succeeded", None
                except AssetLibraryError:
                    record.result = None
                    record.state, record.error_code = "manual_review", "RESULT_CORRUPT"
                self._save(data)
                return record
            if self.clock() - record.started_at >= 86400:
                # No submit has happened for a new record: do not invent one for expiry.
                if record.state == "new":
                    raise AssetLibraryError(409, "WAN_REPLAY_EXPIRED", "离线演练已过期，请核对记录")
                record.state, record.error_code = "manual_review", "TASK_EXPIRED"
                self._save(data)
                return record
            if record.state == "new":
                try:
                    self.adapter.initialize()
                except WanAdapterError:
                    raise AssetLibraryError(503, "WAN_REPLAY_INITIALIZATION_FAILED", "离线适配器初始化失败，尚未提交") from None
                record.state, record.submit_attempts = "submitting", 1
                self._save(data)  # Failure here means no transport submit.
                try:
                    reply = self.adapter.submit(artificial_request())
                    record.provider_task_id, record.state = reply.task_id, "submitted"
                except Exception:
                    record.state, record.error_code = "manual_review", "SUBMIT_UNCERTAIN"
                self._save(data)  # Failure leaves durable submitting, never auto-resubmit.
                return record
            fetching = record.state in {"result_ready", "downloading"}
            if fetching and record.download_attempts >= 3:
                record.state, record.error_code = "manual_review", "DOWNLOAD_LIMIT"
                self._save(data)
                return record
            if record.query_attempts >= 20:
                record.state, record.error_code = "manual_review", "QUERY_LIMIT"
                self._save(data)
                return record
            record.query_attempts += 1
            record.state = "downloading" if fetching else "querying"
            if fetching:
                record.download_attempts += 1
            self._save(data)
            try:
                reply = self.adapter.query(record.provider_task_id)
            except Exception:
                record.consecutive_query_errors += 1
                record.state = "manual_review" if record.consecutive_query_errors >= 3 else ("result_ready" if fetching else "running")
                record.error_code = "QUERY_UNAVAILABLE"
                self._save(data)
                return record
            record.consecutive_query_errors, record.error_code = 0, None
            if reply.state in {"PENDING", "RUNNING"}:
                record.state = "running"
                record.usage_seconds = record.video_count = None
            elif reply.state in {"FAILED", "CANCELED", "UNKNOWN"}:
                record.state = {"FAILED": "failed", "CANCELED": "canceled", "UNKNOWN": "manual_review"}[reply.state]
                record.error_code = {"FAILED": "PROVIDER_FAILED", "CANCELED": "PROVIDER_CANCELED", "UNKNOWN": "PROVIDER_UNKNOWN"}[reply.state]
            else:
                record.usage_seconds, record.video_count = reply.usage_seconds, reply.video_count
                record.state = "result_ready"
                if fetching:
                    try:
                        record.result = self.adapter.download(reply.video_url, self.directory)
                        record.state = "succeeded"
                    except Exception:
                        record.error_code = "DOWNLOAD_UNAVAILABLE"
                        if record.download_attempts >= 3:
                            record.state = "manual_review"
            self._save(data)
            return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, default="success")
    parser.add_argument("--action", choices=["step", "status"], default="status")
    args = parser.parse_args()
    try:
        replay = WanReplay(args.directory / args.scenario, args.scenario)
        record = replay.step() if args.action == "step" else replay.status()
        result = record.model_dump(mode="json") if record else {"scenario": args.scenario, "state": "not_started",
            "simulation_only": True, "network_used": False, "real_execution_allowed": False, "amount_minor": None, "currency": None}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except AssetLibraryError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({"error": {"code": "WAN_REPLAY_UNAVAILABLE", "message": "离线演练暂不可用，请保留数据并检查本地环境"}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
