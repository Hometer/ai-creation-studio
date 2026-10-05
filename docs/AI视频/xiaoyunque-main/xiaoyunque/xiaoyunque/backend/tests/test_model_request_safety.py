"""Artificial SDK/download responses only; never contact a model provider."""

import asyncio
import ast
import importlib
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import requests
from openai import APIStatusError, OpenAI

from config import Config
from models import request_safety
from models.image_processor import ImageProcessor
from models.request_safety import ModelRequestError


CLIENTS = [("llm_gpt", "GPT", "query"), ("llm_gemini", "Gemini", "query"),
           ("vlm_gpt", "GPTVLClient", "chat"), ("vlm_gemini", "GeminiVLClient", "chat")]
PRIVATE = "artificial-private-marker"


def response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class SDK:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.options = []
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome() if callable(outcome) else outcome


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    # Patch this module's reference, not the global time module used by logging.
    monkeypatch.setattr(request_safety, "time", clock)
    return clock


def configured(monkeypatch, kind, sdk, timeout=30, total_timeout=30):
    module_name, class_name, method = kind
    module = importlib.import_module("models." + module_name)
    init_options = []
    def factory(**kwargs):
        init_options.append(kwargs)
        return sdk
    monkeypatch.setattr(module, "OpenAI", factory)
    monkeypatch.setattr(Config, "provider_proxy", lambda provider: None)
    owner = getattr(module, class_name)(api_key="artificial-config", timeout=timeout, total_timeout=total_timeout)
    def call():
        if method == "chat":
            return owner.chat(PRIVATE, ["https://example.invalid/" + PRIVATE])
        return owner.query(PRIVATE, image_urls=["https://example.invalid/" + PRIVATE])
    return owner, call, init_options


@pytest.mark.parametrize("kind", CLIENTS)
@pytest.mark.parametrize("outcome", [SimpleNamespace(choices=[]), response(None), response(""), response(" \n "),
                                    response([]), PRIVATE, RuntimeError(PRIVATE), SimpleNamespace()])
def test_every_invalid_response_counts_toward_request_limit(kind, outcome, monkeypatch, clock, caplog):
    sdk = SDK([outcome, outcome])
    owner, call, init_options = configured(monkeypatch, kind, sdk)
    owner.max_attempts = 2
    with caplog.at_level(logging.DEBUG), pytest.raises(ModelRequestError) as failure:
        call()
    assert len(sdk.calls) == 2
    assert len(clock.sleeps) == 1  # No sleep after the final failed request.
    assert init_options[0]["max_retries"] == 0
    assert all(options["max_retries"] == 0 for options in sdk.options)
    assert PRIVATE not in caplog.text + str(failure.value)


@pytest.mark.parametrize("kind", CLIENTS)
def test_one_attempt_never_retries_or_sleeps(kind, monkeypatch, clock):
    sdk = SDK([response(None)])
    owner, call, _ = configured(monkeypatch, kind, sdk)
    owner.max_attempts = 1
    with pytest.raises(ModelRequestError):
        call()
    assert len(sdk.calls) == 1 and clock.sleeps == []


@pytest.mark.parametrize("kind", CLIENTS)
def test_failure_then_success_keeps_request_contract_and_remaining_timeout(kind, monkeypatch, clock):
    sdk = SDK([response(None), response("  artificial success  ")])
    owner, call, _ = configured(monkeypatch, kind, sdk)
    owner.max_attempts = 3
    assert call() == "  artificial success  "
    assert len(sdk.calls) == 2
    assert sdk.calls[0]["messages"][0]["role"] in {"system", "user"}
    assert sdk.options[1]["timeout"] < sdk.options[0]["timeout"]


@pytest.mark.parametrize("kind", CLIENTS)
def test_total_budget_prevents_backoff_and_next_call(kind, monkeypatch, clock):
    sdk = SDK([response(None)])
    owner, call, _ = configured(monkeypatch, kind, sdk, timeout=30, total_timeout=1)
    with pytest.raises(ModelRequestError):
        call()
    assert len(sdk.calls) == 1 and clock.sleeps == []
    assert sdk.options[0]["timeout"] == 1


@pytest.mark.parametrize("kind", CLIENTS)
def test_late_success_is_rejected_and_cannot_start_another_call(kind, monkeypatch, clock):
    def late():
        clock.now = 31
        return response("late result")
    sdk = SDK([late])
    _, call, _ = configured(monkeypatch, kind, sdk)
    with pytest.raises(ModelRequestError):
        call()
    assert len(sdk.calls) == 1 and clock.sleeps == []


@pytest.mark.parametrize("kind", CLIENTS)
def test_constructor_failure_is_safe_and_does_not_make_a_request(kind, monkeypatch, caplog):
    module_name, class_name, _ = kind
    module = importlib.import_module("models." + module_name)
    def fail(**kwargs):
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(module, "OpenAI", fail)
    monkeypatch.setattr(Config, "provider_proxy", lambda provider: None)
    with caplog.at_level(logging.DEBUG), pytest.raises(ModelRequestError) as exc:
        getattr(module, class_name)(api_key="artificial-config")
    assert PRIVATE not in str(exc.value) + caplog.text


def test_failed_initialization_closes_custom_transport(monkeypatch):
    transport = SimpleNamespace(closed=False)
    def close():
        transport.closed = True
    transport.close = close
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: transport)
    def fail(**kwargs):
        raise ValueError(PRIVATE)
    with pytest.raises(ModelRequestError):
        request_safety.initialize_client(fail, {"timeout": 5}, logging.getLogger("test"), proxy="artificial")
    assert transport.closed


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), None, "invalid"])
def test_invalid_timeout_cannot_disable_limits(value):
    with pytest.raises(ModelRequestError):
        request_safety.positive_seconds(value)


def status_error(status, headers=None, body=None):
    request = httpx.Request("POST", "https://example.invalid/" + PRIVATE)
    return APIStatusError(PRIVATE, response=httpx.Response(status, headers=headers, request=request), body=body)


@pytest.mark.parametrize("error", [status_error(401), status_error(403), status_error(400),
    status_error(429, body={"error": {"code": "insufficient_quota"}})])
def test_permanent_errors_are_not_retried(error, monkeypatch, clock):
    sdk = SDK([error])
    _, call, _ = configured(monkeypatch, CLIENTS[0], sdk)
    with pytest.raises(ModelRequestError):
        call()
    assert len(sdk.calls) == 1 and clock.sleeps == []


def test_retry_after_is_respected_or_stopped_when_it_exceeds_budget(monkeypatch, clock):
    sdk = SDK([status_error(429, {"retry-after": "12"}), response("success")])
    _, call, _ = configured(monkeypatch, CLIENTS[0], sdk)
    assert call() == "success" and clock.sleeps == [12]
    sdk = SDK([status_error(429, {"retry-after": "60"})])
    _, call, _ = configured(monkeypatch, CLIENTS[0], sdk)
    with pytest.raises(ModelRequestError):
        call()
    assert len(sdk.calls) == 1


def test_actual_installed_sdk_has_no_hidden_transport_retries(monkeypatch, clock, caplog):
    seen = []
    def transport(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(500, json={"error": {"message": PRIVATE}})
        return httpx.Response(200, json={"id": "artificial", "object": "chat.completion", "created": 0,
            "model": "artificial", "choices": [{"index": 0, "message": {"role": "assistant", "content": "success"}}]})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http_client:
        with OpenAI(api_key="artificial-config", http_client=http_client, max_retries=0) as client:
            sdk = SDK([])
            owner, _, _ = configured(monkeypatch, CLIENTS[0], sdk)
            owner.client = client
            owner.max_attempts = 2
            with caplog.at_level(logging.DEBUG):
                result = owner.query(PRIVATE)
    assert result == "success" and len(seen) == 2
    assert clock.sleeps == [10]
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("name", ["openai._base_client", "openai._response", "httpx", "httpcore.http11"])
def test_sdk_debug_and_exception_details_are_redacted(name, caplog):
    request_safety.protect_sdk_logs()
    with caplog.at_level(logging.DEBUG):
        try:
            raise RuntimeError(PRIVATE)
        except RuntimeError:
            logging.getLogger(name).exception("request=%s", PRIVATE)
    assert PRIVATE not in caplog.text
    assert "SDK transport event" in caplog.text


class DownloadResponse:
    def __init__(self, status=200, broken=False):
        self.status_code = status
        self.broken = broken
        self.closed = False

    def iter_content(self, chunk_size):
        yield b"artificial-bytes"
        if self.broken:
            raise requests.exceptions.ConnectionError(PRIVATE)

    def close(self):
        self.closed = True


def downloader():
    # No initialization or credentials needed for the pure download method.
    return ImageProcessor.__new__(ImageProcessor)


def test_certificate_failure_never_downgrades_tls_or_changes_existing_file(tmp_path, monkeypatch, caplog):
    seen = []
    def get(*args, **kwargs):
        seen.append(kwargs)
        raise requests.exceptions.SSLError(PRIVATE)
    monkeypatch.setattr(requests, "get", get)
    target = tmp_path / "existing.png"
    target.write_bytes(b"original")
    with caplog.at_level(logging.DEBUG), pytest.raises(ModelRequestError) as exc:
        downloader().download_image("https://example.invalid/" + PRIVATE, target)
    assert len(seen) == 1 and seen[0]["verify"] is True
    assert target.read_bytes() == b"original" and len(list(tmp_path.iterdir())) == 1
    assert PRIVATE not in caplog.text + str(exc.value)


def test_download_stream_failure_is_bounded_closed_and_atomic(tmp_path, monkeypatch, caplog):
    responses = []
    def get(*args, **kwargs):
        assert kwargs["verify"] is True
        item = DownloadResponse(broken=True)
        responses.append(item)
        return item
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    target = tmp_path / "existing.png"
    target.write_bytes(b"original")
    with caplog.at_level(logging.DEBUG), pytest.raises(ModelRequestError):
        downloader().download_image("https://example.invalid/" + PRIVATE, target, max_retries=2)
    assert len(responses) == 2 and all(item.closed for item in responses)
    assert target.read_bytes() == b"original" and len(list(tmp_path.iterdir())) == 1
    assert PRIVATE not in caplog.text


def test_download_timeout_then_success_preserves_proxy_and_closes_response(tmp_path, monkeypatch):
    response = DownloadResponse()
    seen = []
    def get(*args, **kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise requests.exceptions.Timeout(PRIVATE)
        return response
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    target = tmp_path / "success.png"
    assert downloader().download_image("https://example.invalid", target, proxies={"https": "artificial"})
    assert target.read_bytes() == b"artificial-bytes" and response.closed
    assert len(seen) == 2 and all(item["verify"] for item in seen)
    assert seen[1]["proxies"] == {"https": "artificial"}
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("status,attempts", [(404, 1), (500, 2)])
def test_http_download_failure_closes_responses_without_partial_files(status, attempts, tmp_path, monkeypatch):
    responses = []
    def get(*args, **kwargs):
        item = DownloadResponse(status=status)
        responses.append(item)
        return item
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert not downloader().download_image("https://example.invalid", tmp_path / "absent.png", max_retries=2)
    assert len(responses) == attempts and all(item.closed for item in responses)
    assert list(tmp_path.iterdir()) == []


def test_model_smoke_scripts_never_print_credential_fragments():
    for path in (Path(__file__).parents[1] / "models").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                for item in ast.walk(node):
                    assert not (isinstance(item, ast.Subscript) and isinstance(item.value, ast.Name)
                                and item.value.id in {"api_key", "access_key", "secret_key"}), path.name


def test_specific_client_failure_is_persisted_safely_after_restart(tmp_path, monkeypatch, clock, caplog):
    from core.orchestrator import WorkflowEngine, WorkflowStage
    sdk = SDK([response(None)])
    owner, call, _ = configured(monkeypatch, CLIENTS[0], sdk)
    owner.max_attempts = 1
    engine = WorkflowEngine(str(tmp_path))
    engine.create_session("model-safety", {})
    class Agent:
        def set_cancellation_check(self, callback): pass
        def set_progress_callback(self, callback): pass
        async def process(self, data, intervention=None):
            return call()
    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = Agent
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        asyncio.run(engine.execute_stage(engine.get_state("model-safety"), WorkflowStage.SCRIPT_GENERATION, {}))
    snapshot = engine.get_status_snapshot("model-safety")
    assert snapshot["status"]["script_generation"] == "error"
    assert WorkflowEngine(str(tmp_path)).get_status_snapshot("model-safety") == snapshot
    assert PRIVATE not in (tmp_path / "model-safety.json").read_text() + caplog.text
    # Read the saved failure through two independent real HTTP service processes.
    from test_live_recovery import server
    for _ in range(2):
        with server(tmp_path) as client:
            result = client.get("/api/project/model-safety/status")
            assert result.status_code == 200 and result.json() == snapshot
            assert PRIVATE not in result.text
    assert len(sdk.calls) == 1  # Restoring saved state never resubmits the model.


@pytest.mark.parametrize("kind", ["llm", "vlm", "image", "video"])
def test_input_debug_switch_keeps_only_summary_in_all_unified_clients(kind, tmp_path, monkeypatch, caplog):
    from models.llm_client import LLM
    from models.vlm_client import VLM
    from models.image_client import ImageClient
    from models.video_client import VideoClient
    monkeypatch.setattr(Config, "PRINT_MODEL_INPUT", True)
    url = "https://example.invalid/" + PRIVATE
    with caplog.at_level(logging.DEBUG):
        if kind == "llm":
            owner = LLM.__new__(LLM)
            owner._gpt_client = SimpleNamespace(query=lambda *args, **kwargs: "artificial result")
            assert owner.query(PRIVATE, image_urls=[url], model="gpt-artificial")
        elif kind == "vlm":
            owner = VLM.__new__(VLM)
            owner._gpt_client = SimpleNamespace(chat=lambda *args, **kwargs: "artificial result")
            assert owner.query(PRIVATE, image_paths=[url], model="gpt-artificial")
        elif kind == "image":
            owner = ImageClient.__new__(ImageClient)
            def fail(**kwargs):
                raise RuntimeError(PRIVATE)
            owner._gpt_client = SimpleNamespace(generate_image=fail)
            assert owner.generate_image(PRIVATE, image_paths=[url], model="gpt-artificial", save_dir=str(tmp_path)) == []
        else:
            owner = VideoClient.__new__(VideoClient)
            monkeypatch.setattr(owner, "_generate_wan", lambda *args: "artificial result")
            assert owner.generate_video(PRIVATE, url, str(tmp_path / "output.mp4"),
                negative_prompt=PRIVATE, reference_image_paths=[url], reference_video_paths=[url])
    assert PRIVATE not in caplog.text
    assert "input summary" in caplog.text and "prompt_chars=" in caplog.text


def test_reference_agent_does_not_log_vlm_response_or_media_urls(monkeypatch, caplog):
    from core.agents.reference_agent import ReferenceGeneratorAgent
    from models import vlm_client
    class VLM:
        def query(self, *args, **kwargs):
            return '{"selected_index": 0, "score": 8, "reason": "' + PRIVATE + '"}'
    monkeypatch.setattr(vlm_client, "VLM", VLM)
    owner = ReferenceGeneratorAgent.__new__(ReferenceGeneratorAgent)
    url = "https://example.invalid/" + PRIVATE
    with caplog.at_level(logging.DEBUG):
        path, result = owner._select_best_with_vlm([url], {"segment_id": "artificial"}, PRIVATE, PRIVATE)
    assert path == url and result["reason"] == PRIVATE
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_attempt_limit_never_contacts_sdk(value, monkeypatch, clock):
    sdk = SDK([])
    owner, call, _ = configured(monkeypatch, CLIENTS[0], sdk)
    owner.max_attempts = value
    with pytest.raises(ModelRequestError):
        call()
    assert sdk.calls == []
