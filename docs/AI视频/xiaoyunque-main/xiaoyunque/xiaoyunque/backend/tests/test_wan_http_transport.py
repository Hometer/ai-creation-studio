"""Wire tests contact only a loopback HTTP server, never DashScope."""

import http.client
import json
import socket
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.wan_adapter import SUBMIT_PATH, WanAdapter, WanAdapterError, decode_reply
from core.wan_http_transport import WanHttpTransport

BASE = "https://dashscope.aliyuncs.com/api/v1"
ARTIFICIAL = "artificial-local-fixture-credential"
RESULT = "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/artificial.mp4?signature=fixture"
BODY = json.dumps({"output": {"task_id": "fixture-task-1", "task_status": "PENDING"}}).encode()


@contextmanager
def wire(*, status=200, body=BODY, extra=None, delay=0, slow=False, declared=None, close=False, tls=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.handle_request()

        def do_GET(self):
            self.handle_request()

        def handle_request(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.command, self.path, dict(self.headers), data))
            time.sleep(delay)
            try:
                self.send_response(status)
                if declared != "omit":
                    self.send_header("Content-Length", str(len(body) if declared is None else declared))
                for key, value in (extra or {}).items(): self.send_header(key, value)
                if close: self.send_header("Connection", "close")
                self.end_headers()
                if slow:
                    for byte in body:
                        self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(0.025)
                else:
                    self.wfile.write(body); self.wfile.flush()
                if declared not in (None, "omit") and int(declared) > len(body):
                    self.close_connection = True
            except (OSError, ValueError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls is not None:
        server.socket = tls[0].wrap_socket(server.socket, server_side=True)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LoopbackFixture(WanHttpTransport):
        # Deliberately confined to this test. Not available through production
        # constructors, config, dependency injection or HTTP parameters.
        def initialize(self):
            pass

        def _connect(self, host, deadline, connect_timeout):
            if tls is not None:
                return http.client.HTTPSConnection("localhost", server.server_port, timeout=connect_timeout, context=tls[1])
            return http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=connect_timeout)

    try:
        yield LoopbackFixture(ARTIFICIAL, BASE), calls
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def assert_code(code, callable):
    with pytest.raises(WanAdapterError) as error:
        callable()
    assert error.value.code == code
    assert ARTIFICIAL not in str(error.value) and "signature" not in str(error.value)


def test_production_transport_never_reaches_dns_socket_or_adapter(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError("Production execution must remain closed")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    transport = WanHttpTransport(ARTIFICIAL, BASE)
    assert ARTIFICIAL not in repr(transport) and transport.offline_only is False
    for action in (transport.initialize, lambda: transport.request("POST", SUBMIT_PATH, body={}),
                   lambda: transport.request("GET", "/tasks/fixture-1"),
                   lambda: transport.open_download(RESULT).__enter__(), lambda: WanAdapter(transport).initialize()):
        assert_code("REAL_EXECUTION_DISABLED", action)


@pytest.mark.parametrize("url", ["http://dashscope.aliyuncs.com/api/v1", "https://127.0.0.1/api/v1",
    "https://dashscope-intl.aliyuncs.com/api/v1", "https://dashscope.aliyuncs.com:443/api/v1",
    "https://dashscope.aliyuncs.com/api/v1?x=1", "https://user@dashscope.aliyuncs.com/api/v1"])
def test_endpoint_policy(url):
    assert_code("HTTP_ENDPOINT_UNSUPPORTED", lambda: WanHttpTransport(ARTIFICIAL, url))


@pytest.mark.parametrize("key", ["", "line\nbreak", "space key", "非ASCII", None])
def test_header_injection_and_empty_key_rejected(key):
    assert_code("HTTP_CREDENTIAL_INVALID", lambda: WanHttpTransport(key, BASE))


def test_submit_query_protocol_exact_and_headers_not_caller_controlled(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    with wire() as (transport, calls):
        reply = transport.request("POST", SUBMIT_PATH, body={"fixture": "人工协议"},
            headers={"Authorization": "untrusted", "Cookie": "untrusted", "Idempotency-Key": "untrusted"})
        assert decode_reply(reply).task_id == "fixture-task-1"
        assert len(calls) == 1 and calls[0][0:2] == ("POST", "/api/v1" + SUBMIT_PATH)
        assert calls[0][2]["Authorization"] == "Bearer " + ARTIFICIAL
        assert calls[0][2]["X-DashScope-Async"] == "enable"
        assert json.loads(calls[0][3]) == {"fixture": "人工协议"}
        assert "Cookie" not in calls[0][2] and "Idempotency-Key" not in calls[0][2]
        transport.request("GET", "/tasks/fixture-task-1")
        assert len(calls) == 2 and calls[1][1] == "/api/v1/tasks/fixture-task-1"
        assert "X-DashScope-Async" not in calls[1][2] and calls[1][3] == b""


@pytest.mark.parametrize("method,path,options", [("GET", "/tasks/../../secret", {}),
    ("GET", "/tasks/task?secret=1", {}), ("POST", "/arbitrary", {"body": {}}),
    ("GET", "/tasks/task", {"body": {}}), ("POST", SUBMIT_PATH, {"body": {}, "allow_redirects": True}),
    ("GET", "/tasks/task", {"max_bytes": True})])
def test_invalid_path_or_policy_prevents_wire_request(method, path, options):
    with wire() as (transport, calls):
        assert_code("HTTP_REQUEST_INVALID", lambda: transport.request(method, path, **options))
        assert calls == []


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 429, 500, 503])
def test_errors_do_not_retry_or_follow_redirect_or_echo_body(status):
    with wire(status=status, body=ARTIFICIAL.encode(), extra={"Location": "https://unreviewed.invalid/"}) as (transport, calls):
        assert_code("HTTP_RESPONSE_INVALID", lambda: transport.request("POST", SUBMIT_PATH, body={}))
        assert len(calls) == 1


@pytest.mark.parametrize("declared,extra,body", [(1000, {}, b"x"), ("invalid", {}, b"x"),
    (0, {}, b""), (2, {"Content-Length": "2"}, b"xx"),
    (2, {"Transfer-Encoding": "chunked"}, b"xx"), (2, {"Content-Encoding": "gzip"}, b"xx")])
def test_size_encoding_and_duplicate_length_fail_closed(declared, extra, body):
    with wire(body=body, declared=declared, extra=extra) as (transport, calls):
        with pytest.raises(WanAdapterError): transport.request("GET", "/tasks/task", max_bytes=100)
        assert len(calls) == 1


def test_truncated_response_does_not_succeed():
    with wire(body=b"short", declared=10) as (transport, calls):
        assert_code("HTTP_TRANSPORT_UNAVAILABLE", lambda: transport.request("POST", SUBMIT_PATH, body={}))
        assert len(calls) == 1


def test_actual_stream_limit_without_content_length():
    with wire(body=b"12345", declared="omit", close=True) as (transport, calls):
        assert_code("HTTP_TRANSPORT_UNAVAILABLE", lambda: transport.request("GET", "/tasks/task", max_bytes=4))
        assert len(calls) == 1
    with wire(body=b"12345", declared="omit", close=True) as (transport, calls):
        assert transport.request("GET", "/tasks/task", max_bytes=5).body == b"12345"
        assert len(calls) == 1


def test_connection_initialization_failure_is_safe_and_single_attempt():
    with wire() as (transport, calls):
        def fail(*args): raise OSError(ARTIFICIAL)
        transport._connect = fail
        assert_code("HTTP_TRANSPORT_UNAVAILABLE", lambda: transport.request("POST", SUBMIT_PATH, body={}))
        assert calls == []


@pytest.mark.parametrize("close", [False, True])
def test_absolute_deadline_interrupts_slow_trickle_even_connection_close(close):
    with wire(body=b"abcdefghijklmnopqrstuvwxyz", slow=True, close=close) as (transport, calls):
        start = time.monotonic()
        assert_code("HTTP_TRANSPORT_UNAVAILABLE", lambda: transport.request("GET", "/tasks/task", total_seconds=0.12))
        assert time.monotonic() - start < 0.8 and len(calls) == 1


def test_native_read_timeout_does_not_resubmit():
    with wire(delay=0.25) as (transport, calls):
        assert_code("HTTP_TRANSPORT_UNAVAILABLE", lambda: transport.request("POST", SUBMIT_PATH,
            body={}, timeouts=(0.1, 0.05)))
        assert len(calls) == 1


def test_download_has_no_authorization_cookie_or_async_header():
    with wire(body=b"artificial-video-bytes") as (transport, calls):
        with transport.open_download(RESULT, headers={"Authorization": "untrusted", "Cookie": "untrusted"}) as response:
            assert b"".join(response.chunks()) == b"artificial-video-bytes"
        assert len(calls) == 1 and calls[0][1] == "/artificial.mp4?signature=fixture"
        assert not {"Authorization", "Cookie", "X-DashScope-Async"}.intersection(calls[0][2])


@pytest.mark.parametrize("url", ["https://127.0.0.1/file.mp4", "https://unreviewed.invalid/file.mp4",
    "http://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/file.mp4"])
def test_download_destination_cannot_be_overridden(url):
    with wire() as (transport, calls):
        assert_code("RESULT_LOCATION_UNSUPPORTED", lambda: transport.open_download(url).__enter__())
        assert calls == []


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.1"], ["::1"], ["169.254.169.254"],
    ["8.8.8.8", "192.168.1.1"]])
def test_dns_all_addresses_must_be_public(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs:
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses])
    transport = WanHttpTransport(ARTIFICIAL, BASE)
    assert_code("HTTP_DESTINATION_UNSUPPORTED", lambda: transport._resolve("fixture.invalid", time.monotonic() + 1))


def test_dns_pin_and_bounded_stalled_resolver(monkeypatch):
    entered, released = threading.Event(), threading.Event()
    def stalled(*args, **kwargs):
        entered.set(); released.wait(1)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", stalled)
    transport = WanHttpTransport(ARTIFICIAL, BASE)
    try:
        assert_code("HTTP_TRANSPORT_UNAVAILABLE", lambda: transport._resolve("fixture.invalid", time.monotonic() + 0.03))
        assert entered.is_set()
        assert_code("HTTP_TRANSPORT_UNAVAILABLE", lambda: transport._resolve("fixture.invalid", time.monotonic() + 1))
    finally:
        released.set()


def test_pinned_tls_uses_checked_address_and_official_sni(monkeypatch):
    from core.wan_http_transport import PinnedHTTPSConnection
    calls = []
    class Raw:
        def close(self): calls.append("closed")
    class Context:
        def wrap_socket(self, raw, server_hostname):
            calls.append(server_hostname)
            return raw
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: (calls.append((address, timeout)) or Raw()))
    connection = PinnedHTTPSConnection("dashscope.aliyuncs.com", "8.8.8.8", 5)
    connection._context = Context()
    connection.connect()
    assert calls == [(("8.8.8.8", 443), 5), "dashscope.aliyuncs.com"]


def test_actual_loopback_tls_read_and_absolute_timeout(tmp_path):
    # Temporary artificial certificate. Its private key never enters Git/logs.
    certificate, private = tmp_path / "fixture-cert.pem", tmp_path / "fixture-private.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=localhost", "-keyout", str(private), "-out", str(certificate)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, private)
    client_context = ssl.create_default_context(cafile=str(certificate))
    with wire(tls=(server_context, client_context)) as (transport, calls):
        assert decode_reply(transport.request("GET", "/tasks/fixture-task-1")).task_id == "fixture-task-1"
        assert len(calls) == 1
    with wire(tls=(server_context, client_context), body=b"abcdefghijklmnopqrstuvwxyz", slow=True, close=True) as (transport, calls):
        start = time.monotonic()
        assert_code("HTTP_TRANSPORT_UNAVAILABLE", lambda: transport.request("GET", "/tasks/task", total_seconds=0.12))
        assert time.monotonic() - start < 0.8 and len(calls) == 1
