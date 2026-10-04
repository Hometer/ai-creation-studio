"""Artificial REST contracts and real process recovery; never a model call."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from api.schemas.wan_replay import FixtureDocument
from core.asset_library import AssetLibraryError
from core.task_provider import read_document
from core.wan_adapter import (
    JSON_LIMIT, SUBMIT_PATH, TIMEOUTS, TOTAL_SECONDS, Reply, WanAdapter, WanAdapterError,
    decode_reply, safe_result_url,
)
from core.wan_replay import RESULT_URL, TASK_ID, FixtureTransport, WanReplay, artificial_request


def response(state="PENDING", **output):
    return Reply(200, json.dumps({"output": {"task_id": TASK_ID, "task_status": state, **output},
        "usage": {"duration": 5, "video_count": 1, "unknown": "ignored"}, "message": "private-secret"}).encode())


class Transport:
    offline_only = True

    def __init__(self, reply=None, content=b"not an mp4", clock=None):
        self.reply = reply or response()
        self.content, self.calls, self.clock = content, [], clock
        self.length, self.status, self.break_stream = len(content), 200, False
        self.initialization_error = False

    def initialize(self):
        if self.initialization_error:
            raise RuntimeError("private-secret")

    def request(self, method, path, **options):
        self.calls.append((method, path, options))
        if self.clock is not None:
            self.clock[0] += 31
        return self.reply

    @contextmanager
    def open_download(self, url, **options):
        self.calls.append(("download", url, options))
        transport = self

        class Download:
            status = transport.status
            content_length = transport.length

            def chunks(self):
                if transport.clock is not None:
                    transport.clock[0] += 31
                yield transport.content
                if transport.break_stream:
                    raise TimeoutError("private-secret")

        yield Download()


@pytest.fixture
def video(tmp_path):
    fixture = FixtureTransport(tmp_path / "sample", "success")
    fixture.initialize()
    return (fixture.directory / "fixture.mp4").read_bytes()


def advance(replay, n=4):
    result = None
    for _ in range(n):
        result = replay.step()
    return result


def counts(directory):
    return read_document(directory / "fixture/fixture.json", FixtureDocument)


def mutate(directory, operation):
    path = directory / "replay.json"
    data = json.loads(path.read_text())
    operation(data["record"])
    path.write_text(json.dumps(data))


def test_default_closed_even_nonoffline_transport_has_methods():
    transport = Transport()
    transport.offline_only = False
    for adapter in (WanAdapter(), WanAdapter(transport)):
        with pytest.raises(WanAdapterError, match="REAL_EXECUTION_DISABLED"):
            adapter.submit(artificial_request())
    assert transport.calls == []


def test_contract_uses_async_no_authorization_no_local_id_and_bounded_requests():
    transport = Transport()
    adapter = WanAdapter(transport)
    assert adapter.submit(artificial_request()).task_id == TASK_ID
    assert adapter.query(TASK_ID).state == "PENDING"
    method, path, options = transport.calls[0]
    assert (method, path) == ("POST", SUBMIT_PATH)
    assert options["headers"] == {"Content-Type": "application/json", "X-DashScope-Async": "enable"}
    assert set(options["body"]) == {"model", "input", "parameters"}
    assert options["timeouts"] == TIMEOUTS and options["total_seconds"] == TOTAL_SECONDS
    assert options["max_bytes"] == JSON_LIMIT and options["allow_redirects"] is False
    assert transport.calls[1][1] == f"/tasks/{TASK_ID}"
    assert "X-DashScope-Async" not in transport.calls[1][2]["headers"]


@pytest.mark.parametrize("identifier", [None, "", "../private", "x?key=private", "x/y", "x\n", "a" * 129, 12])
def test_query_requires_safe_platform_id_before_transport(identifier):
    transport = Transport()
    with pytest.raises(WanAdapterError, match="PROVIDER_TASK_ID_REQUIRED"):
        WanAdapter(transport).query(identifier)
    assert transport.calls == []


@pytest.mark.parametrize("state", ["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"])
def test_all_vendor_states_and_usage_never_price(state):
    observation = decode_reply(response(state, video_url=RESULT_URL), TASK_ID)
    assert observation.state == state
    if state == "SUCCEEDED":
        assert observation.usage_seconds == 5 and observation.video_count == 1
        assert "signature" not in repr(observation)
    else:
        assert observation.usage_seconds is None and observation.video_url is None


@pytest.mark.parametrize("body", [
    b"not json", b"[]", b'{}', b'{"output":null}',
    b'{"output":{"task_id":"x","task_status":"PENDING","task_status":"RUNNING"}}',
    json.dumps({"output": {"task_id": TASK_ID, "task_status": "NEW_VENDOR_STATE"}}).encode(),
    json.dumps({"output": {"task_id": "other", "task_status": "PENDING"}}).encode(),
    json.dumps({"output": {"task_id": TASK_ID, "task_status": "SUCCEEDED"}}).encode(),
])
def test_malformed_untrusted_response_is_safe(body):
    with pytest.raises(WanAdapterError) as error:
        decode_reply(Reply(200, body), TASK_ID)
    assert str(error.value) == "PROVIDER_RESPONSE_INVALID"


@pytest.mark.parametrize("duration,count", [(True, 1), (0, 1), (16, 1), (5, 2), (5, True), ("5", 1), (5, None)])
def test_invalid_successful_usage(duration, count):
    body = json.loads(response("SUCCEEDED", video_url=RESULT_URL).body)
    body["usage"] = {"duration": duration, "video_count": count}
    with pytest.raises(WanAdapterError):
        decode_reply(Reply(200, json.dumps(body).encode()))


@pytest.mark.parametrize("status,body", [(503, b"private-secret"), (302, b"{}"), (200, b" " * (JSON_LIMIT + 1))])
def test_http_errors_and_large_body_not_exposed(status, body):
    with pytest.raises(WanAdapterError) as error:
        decode_reply(Reply(status, body))
    assert "private-secret" not in str(error.value)


@pytest.mark.parametrize("url", [
    "http://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/x.mp4",
    "https://127.0.0.1/x.mp4", "https://localhost/x.mp4", "https://10.1.2.3/x.mp4",
    "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com.evil.example/x.mp4",
    "https://private@dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/x.mp4",
    "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com:443/x.mp4",
    "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/x.mp4#private",
    "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/x.mp4\n",
    "https://dashscope-a717.oss-accelerate.aliyuncs.com/x.mp4", "file:///x.mp4", RESULT_URL.replace(".mp4", ".html"),
])
def test_conservative_domestic_url_policy(url):
    with pytest.raises(WanAdapterError, match="RESULT_LOCATION_UNSUPPORTED"):
        safe_result_url(url)


def test_request_deadline_and_init_failure_are_safe():
    clock = [0]
    transport = Transport(clock=clock)
    with pytest.raises(WanAdapterError, match="TRANSPORT_UNAVAILABLE"):
        WanAdapter(transport, clock=lambda: clock[0]).submit(artificial_request())
    assert len(transport.calls) == 1
    transport.initialization_error = True
    with pytest.raises(WanAdapterError, match="ADAPTER_INITIALIZATION_FAILED") as error:
        WanAdapter(transport).initialize()
    assert "private-secret" not in str(error.value)


def test_success_replay_no_network_no_sensitive_store_and_reuse(tmp_path, monkeypatch):
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("No network may be used")

    monkeypatch.setattr(requests, "request", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    replay = WanReplay(tmp_path, "success")
    result = advance(replay)
    assert result.state == "succeeded"
    assert (result.submit_attempts, result.query_attempts, result.download_attempts) == (1, 3, 1)
    assert result.usage_seconds == 5 and result.result.duration == 1
    assert result.amount_minor is None and result.currency is None and result.real_execution_allowed is False
    store = (tmp_path / "replay.json").read_text()
    for private in ("private", "signature", "orig_prompt", "base64", "人工协议演练", "Authorization", "video_url"):
        assert private not in store
    before = counts(tmp_path)
    assert WanReplay(tmp_path, "success").step() == result
    assert counts(tmp_path) == before


def test_lost_submission_never_resubmits_or_queries_after_restart(tmp_path):
    first = WanReplay(tmp_path, "lost-submit").step()
    assert first.state == "manual_review" and first.error_code == "SUBMIT_UNCERTAIN"
    assert first.provider_task_id is None
    assert advance(WanReplay(tmp_path, "lost-submit"), 10) == first
    assert counts(tmp_path).submit_calls == 1 and counts(tmp_path).query_calls == 0


def test_query_failure_cap(tmp_path):
    final = advance(WanReplay(tmp_path, "query-timeout"))
    assert final.state == "manual_review" and final.consecutive_query_errors == 3
    assert final.submit_attempts == 1 and final.query_attempts == 3
    assert WanReplay(tmp_path, "query-timeout").step() == final


def test_query_total_limit_and_task_expiry_do_not_resubmit(tmp_path):
    replay = WanReplay(tmp_path, "success")
    replay.step()
    mutate(tmp_path, lambda r: r.update(query_attempts=20))
    assert replay.step().error_code == "QUERY_LIMIT"
    assert counts(tmp_path).submit_calls == 1 and counts(tmp_path).query_calls == 0
    other = tmp_path / "expiry"
    replay = WanReplay(other, "success", clock=lambda: 100.0)
    replay.step()
    assert WanReplay(other, "success", clock=lambda: 86500.0).step().error_code == "TASK_EXPIRED"
    assert counts(other).query_calls == 0


@pytest.mark.parametrize("state,expected", [("FAILED", "failed"), ("CANCELED", "canceled"), ("UNKNOWN", "manual_review")])
def test_terminal_provider_status_never_regenerates(tmp_path, state, expected):
    replay = WanReplay(tmp_path, "success")
    replay.step()
    replay.adapter = WanAdapter(Transport(response(state)))
    result = replay.step()
    assert result.state == expected and result.submit_attempts == 1
    assert replay.step() == result


def test_invalid_mp4_three_fetches_cleanup_and_stop(tmp_path):
    final = advance(WanReplay(tmp_path, "bad-video"), 6)
    assert final.state == "manual_review" and final.download_attempts == 3
    assert counts(tmp_path).download_calls == 3 and counts(tmp_path).submit_calls == 1
    assert not (tmp_path / "result.mp4").exists() and not list(tmp_path.glob(".download-*"))


@pytest.mark.parametrize("kind", ["size", "length", "redirect", "interrupted", "deadline", "invalid"])
def test_stream_limits_cleanup_and_retryable_failure(tmp_path, video, kind, monkeypatch):
    clock = [0]
    transport = Transport(content=video)
    if kind == "size":
        monkeypatch.setattr("core.wan_adapter.VIDEO_LIMIT", len(video) - 1)
        transport.length = None
    elif kind == "length":
        transport.length += 1
    elif kind == "redirect":
        transport.status = 302
    elif kind == "interrupted":
        transport.break_stream = True
    elif kind == "deadline":
        transport.clock = clock
    else:
        transport.content = b"<html>private-secret</html>"
        transport.length = len(transport.content)
    with pytest.raises(WanAdapterError, match="DOWNLOAD_UNAVAILABLE") as error:
        WanAdapter(transport, clock=lambda: clock[0]).download(RESULT_URL, tmp_path / "output")
    assert "private-secret" not in str(error.value)
    assert not (tmp_path / "output/result.mp4").exists()
    assert not list((tmp_path / "output").glob(".download-*"))
    assert transport.calls[0][2]["headers"] == {} and transport.calls[0][2]["allow_redirects"] is False


def test_download_publishes_once_preserves_old_and_rejects_symlink(tmp_path, video):
    transport = Transport(content=video)
    adapter = WanAdapter(transport)
    result = adapter.download(RESULT_URL, tmp_path / "output")
    assert result.byte_size == len(video) and result.duration == 1
    transport.content = b"invalid"
    assert adapter.download(RESULT_URL, tmp_path / "output") == result
    assert len(transport.calls) == 1
    (tmp_path / "link").mkdir()
    (tmp_path / "link/result.mp4").symlink_to(tmp_path / "output/result.mp4")
    with pytest.raises(WanAdapterError, match="RESULT_INVALID"):
        adapter.download(RESULT_URL, tmp_path / "link")
    assert (tmp_path / "output/result.mp4").read_bytes() == video


def test_checkpoint_write_failures_before_and_after_submit(tmp_path, monkeypatch):
    import core.wan_replay as module

    write = module.atomic_write_session
    count = [0]

    def fail(path, data):
        if path.name == "replay.json":
            count[0] += 1
            if count[0] == 2:
                raise OSError("private-secret")
        write(path, data)

    monkeypatch.setattr(module, "atomic_write_session", fail)
    with pytest.raises(AssetLibraryError):
        WanReplay(tmp_path, "success").step()
    assert counts(tmp_path).submit_calls == 0
    assert WanReplay(tmp_path, "success").status().state == "new"
    count[0] = 0
    # Existing new record: submitting saves, provider accepts, final checkpoint fails.
    with pytest.raises(AssetLibraryError):
        WanReplay(tmp_path, "success").step()
    assert counts(tmp_path).submit_calls == 1
    monkeypatch.setattr(module, "atomic_write_session", write)
    assert WanReplay(tmp_path, "success").step().error_code == "SUBMIT_UNCERTAIN"
    assert counts(tmp_path).submit_calls == 1


def test_publish_then_crash_recovers_without_network_even_after_limits(tmp_path, monkeypatch):
    import core.wan_replay as module

    replay = WanReplay(tmp_path, "success")
    advance(replay, 3)
    write = module.atomic_write_session

    def fail_final(path, data):
        if path.name == "replay.json" and data["record"]["state"] == "succeeded":
            raise OSError("artificial crash after publication")
        write(path, data)

    monkeypatch.setattr(module, "atomic_write_session", fail_final)
    with pytest.raises(AssetLibraryError):
        replay.step()
    assert (tmp_path / "result.mp4").exists()
    mutate(tmp_path, lambda r: r.update(download_attempts=3, query_attempts=20, started_at=0.0))
    before = counts(tmp_path)
    monkeypatch.setattr(module, "atomic_write_session", write)
    closed = WanAdapter()
    recovered = WanReplay(tmp_path, "success", adapter=closed).step()
    assert recovered.state == "succeeded" and counts(tmp_path) == before


@pytest.mark.parametrize("damage", ["json", "future", "bool", "missing", "duplicate", "link", "scenario", "execution", "count"])
def test_damaged_checkpoint_not_replaced(tmp_path, damage):
    WanReplay(tmp_path, "success").step()
    path = tmp_path / "replay.json"
    data = json.loads(path.read_text())
    if damage == "json":
        path.write_text("{broken")
    elif damage == "future":
        data["schema_version"] = 2
    elif damage == "bool":
        data["schema_version"] = True
    elif damage == "missing":
        del data["record"]["submit_attempts"]
    elif damage == "duplicate":
        path.write_text('{"schema_version":1,"schema_version":1,"record":null}')
    elif damage == "link":
        target = tmp_path / "previous.json"
        path.rename(target)
        path.symlink_to(target)
    elif damage == "scenario":
        data["record"]["scenario"] = "lost-submit"
    elif damage == "execution":
        data["record"]["real_execution_allowed"] = True
    else:
        data["record"]["query_attempts"] = -1
    if damage not in {"json", "duplicate", "link"}:
        path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(AssetLibraryError, match="WAN_REPLAY_STORE_UNAVAILABLE"):
        WanReplay(tmp_path, "success").step()
    assert path.read_bytes() == before and counts(tmp_path).submit_calls == 1


def test_two_concurrent_steps_one_submit(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: WanReplay(tmp_path, "success").step(), range(2)))
    assert counts(tmp_path).submit_calls == 1
    assert WanReplay(tmp_path, "success").status().query_attempts == 1


def test_initialization_failure_never_submits(tmp_path):
    transport = Transport()
    transport.initialization_error = True
    with pytest.raises(AssetLibraryError, match="WAN_REPLAY_INITIALIZATION_FAILED"):
        WanReplay(tmp_path, "success", WanAdapter(transport)).step()
    assert WanReplay(tmp_path, "success").status().submit_attempts == 0 and transport.calls == []


def test_interrupted_query_checkpoint_retries_same_id_and_resets_errors(tmp_path):
    replay = WanReplay(tmp_path, "success")
    replay.step()
    mutate(tmp_path, lambda r: r.update(state="querying", query_attempts=1, consecutive_query_errors=1))
    result = replay.step()
    assert result.state == "running" and result.consecutive_query_errors == 0 and result.query_attempts == 2
    assert result.provider_task_id == TASK_ID and counts(tmp_path).submit_calls == 1


def test_corrupted_published_result_preserves_file_and_holds_for_review(tmp_path):
    replay = WanReplay(tmp_path, "success")
    advance(replay, 3)
    (tmp_path / "result.mp4").write_bytes(b"corrupt private")
    before = counts(tmp_path)
    result = replay.step()
    assert result.state == "manual_review" and result.error_code == "RESULT_CORRUPT"
    assert counts(tmp_path) == before and (tmp_path / "result.mp4").read_bytes() == b"corrupt private"


def test_finished_result_missing_is_safe_read_error(tmp_path):
    replay = WanReplay(tmp_path, "success")
    advance(replay)
    before = (tmp_path / "replay.json").read_bytes()
    (tmp_path / "result.mp4").unlink()
    with pytest.raises(AssetLibraryError, match="WAN_REPLAY_RESULT_UNAVAILABLE"):
        replay.status()
    assert (tmp_path / "replay.json").read_bytes() == before


@pytest.mark.parametrize("key,value", [("simulation_only", 1), ("network_used", 0), ("video_count", True), ("real_execution_allowed", 0)])
def test_offline_literals_cannot_be_forged_with_integer_boolean(tmp_path, key, value):
    WanReplay(tmp_path, "success").step()
    mutate(tmp_path, lambda r: r.update({key: value}))
    with pytest.raises(AssetLibraryError, match="WAN_REPLAY_STORE_UNAVAILABLE"):
        WanReplay(tmp_path, "success").status()


def test_replay_survives_actual_process_restarts(tmp_path):
    directory = Path(__file__).resolve().parents[1]

    def run(scenario, action="step"):
        result = subprocess.run([sys.executable, "-m", "core.wan_replay", "--directory", str(tmp_path),
            "--scenario", scenario, "--action", action], cwd=directory, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert run("success")["state"] == "submitted"
    assert run("success")["state"] == "running"
    assert run("success")["state"] == "result_ready"
    result = run("success")
    assert result["state"] == "succeeded" and run("success", "status") == result
    assert run("success") == result and counts(tmp_path / "success").submit_calls == 1
    uncertain = run("lost-submit")
    assert uncertain["state"] == "manual_review"
    assert run("lost-submit") == uncertain and run("lost-submit", "status") == uncertain
    assert counts(tmp_path / "lost-submit").submit_calls == 1
