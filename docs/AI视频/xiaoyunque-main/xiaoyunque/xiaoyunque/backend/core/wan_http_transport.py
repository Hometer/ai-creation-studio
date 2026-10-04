"""Dormant native HTTP transport. No execution authorization issuer exists yet.

Every public I/O entry rejects production execution, even with a configured Key.
Tests alone override that guard and connect the private wire implementation to a
loopback fixture. No proxy, redirect, cookie, retry or user supplied headers.
"""

import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

from core.model_preflight import check_connection
from core.wan_adapter import JSON_LIMIT, SUBMIT_PATH, Reply, WanAdapterError, safe_result_url
from core.media_archive import VIDEO_LIMIT


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class DownloadStream:
    def __init__(self, response, connection, deadline, limit, clock):
        self.response, self.connection, self.deadline = response, connection, deadline
        self.limit, self.clock = limit, clock
        self.status = response.status
        values = response.headers.get_all("Content-Length", [])
        if len(values) > 1 or (values and not re.fullmatch(r"[0-9]{1,12}", values[0])):
            raise WanAdapterError("HTTP_RESPONSE_INVALID")
        self.content_length = int(values[0]) if values else None
        if (self.status != 200 or (self.content_length is not None and not 0 < self.content_length <= limit)
                or (values and response.getheader("Transfer-Encoding") is not None)
                or response.getheader("Content-Encoding", "identity") != "identity"):
            raise WanAdapterError("HTTP_RESPONSE_INVALID")

    def chunks(self):
        size = 0
        try:
            while True:
                remaining = self.deadline - self.clock()
                if remaining <= 0:
                    raise TimeoutError()
                if self.connection.sock is not None:
                    self.connection.sock.settimeout(min(20, remaining))
                chunk = self.response.read1(min(64 * 1024, self.limit - size + 1))
                if self.clock() >= self.deadline:
                    raise TimeoutError()
                if not chunk:
                    break
                size += len(chunk)
                if size > self.limit:
                    raise ValueError("Oversized response")
                yield chunk
            if not size or (self.content_length is not None and size != self.content_length):
                raise ValueError("Incomplete response")
        except Exception:
            raise WanAdapterError("HTTP_TRANSPORT_UNAVAILABLE") from None


class WanHttpTransport:
    offline_only = False

    def __init__(self, api_key, base_url, clock=time.monotonic):
        if not check_connection(api_key, base_url).endpoint_allowed:
            raise WanAdapterError("HTTP_ENDPOINT_UNSUPPORTED")
        if (not isinstance(api_key, str) or not api_key or len(api_key) > 4096
                or any(ord(c) <= 32 or ord(c) >= 127 for c in api_key)):
            raise WanAdapterError("HTTP_CREDENTIAL_INVALID")
        parsed = urlsplit(base_url)
        self._host, self._prefix, self._key = parsed.hostname, "/api/v1", api_key
        self._clock = clock
        # Prevent unbounded unresolved DNS threads if the OS resolver stalls.
        self._dns_busy = threading.Lock()

    def __repr__(self):
        return "WanHttpTransport(execution_disabled=True)"

    def initialize(self):
        # No constructor flag, config toggle, env switch or public permit exists.
        raise WanAdapterError("REAL_EXECUTION_DISABLED")

    def _resolve(self, host, deadline):
        if not self._dns_busy.acquire(blocking=False):
            raise WanAdapterError("HTTP_TRANSPORT_UNAVAILABLE")
        finished, outcome = threading.Event(), []

        def resolve():
            try:
                addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                outcome.extend(addresses)
            except Exception:
                pass
            finally:
                self._dns_busy.release()
                finished.set()

        threading.Thread(target=resolve, daemon=True).start()
        if not finished.wait(max(0, min(5, deadline - self._clock()))) or not outcome:
            raise WanAdapterError("HTTP_TRANSPORT_UNAVAILABLE")
        addresses = [entry[4][0] for entry in outcome]
        if not all(ipaddress.ip_address(address).is_global for address in addresses):
            raise WanAdapterError("HTTP_DESTINATION_UNSUPPORTED")
        return addresses[0]

    def _connect(self, host, deadline, connect_timeout):
        address = self._resolve(host, deadline)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError()
        return PinnedHTTPSConnection(host, address, min(connect_timeout, remaining))

    @contextmanager
    def _stream(self, method, host, path, body, headers, timeouts, total_seconds, max_bytes):
        # Private wire helper. Production public methods enforce initialize first.
        connection, timer, watchdog_socket = None, None, None
        try:
            if (not isinstance(total_seconds, (int, float)) or not 0 < total_seconds <= 30
                    or len(timeouts) != 2 or not 0 < timeouts[0] <= 5 or not 0 < timeouts[1] <= 20):
                raise ValueError("Invalid timeout policy")
            deadline = self._clock() + total_seconds
            connection = self._connect(host, deadline, timeouts[0])

            def interrupt():
                stream = watchdog_socket or connection.sock
                if stream is not None:
                    try:
                        stream.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError()
            timer = threading.Timer(remaining, interrupt)
            timer.daemon = True
            timer.start()
            connection.connect()
            # HTTPResponse may close connection.sock while retaining its file
            # descriptor. A duplicate still interrupts that slow response.
            # SSLSocket.dup() is unsupported; duplicate the descriptor as a
            # plain socket used only for shutdown, never for reading TLS data.
            watchdog_socket = socket.fromfd(connection.sock.fileno(), connection.sock.family, connection.sock.type)
            connection.sock.settimeout(min(timeouts[1], max(0.001, deadline - self._clock())))
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            yield DownloadStream(response, connection, deadline, max_bytes, self._clock)
        except WanAdapterError:
            raise
        except Exception:
            raise WanAdapterError("HTTP_TRANSPORT_UNAVAILABLE") from None
        finally:
            if timer is not None:
                timer.cancel()
            if connection is not None:
                connection.close()
            if watchdog_socket is not None:
                watchdog_socket.close()

    def request(self, method, path, *, body=None, headers=None, timeouts=(5, 20),
                total_seconds=30, max_bytes=JSON_LIMIT, allow_redirects=False):
        self.initialize()
        if (allow_redirects is not False or type(max_bytes) is not int or not 0 < max_bytes <= JSON_LIMIT
                or not ((method == "POST" and path == SUBMIT_PATH and isinstance(body, dict))
                    or (method == "GET" and isinstance(path, str) and re.fullmatch(r"/tasks/[A-Za-z0-9][A-Za-z0-9_-]{0,127}", path) and body is None))):
            raise WanAdapterError("HTTP_REQUEST_INVALID")
        # Ignore caller headers completely. In particular no Idempotency-Key.
        protected = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        encoded = None
        if method == "POST":
            protected.update({"Content-Type": "application/json", "X-DashScope-Async": "enable"})
            try:
                encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (ValueError, TypeError):
                raise WanAdapterError("HTTP_REQUEST_INVALID") from None
        with self._stream(method, self._host, self._prefix + path, encoded, protected,
                          timeouts, total_seconds, max_bytes) as response:
            return Reply(status=response.status, body=b"".join(response.chunks()))

    @contextmanager
    def open_download(self, url, *, headers=None, timeouts=(5, 20), total_seconds=30,
                      max_bytes=VIDEO_LIMIT, allow_redirects=False):
        self.initialize()
        if allow_redirects is not False or type(max_bytes) is not int or not 0 < max_bytes <= VIDEO_LIMIT:
            raise WanAdapterError("HTTP_REQUEST_INVALID")
        parsed = urlsplit(safe_result_url(url))
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        with self._stream("GET", parsed.hostname, path, None, {"Accept": "video/mp4"},
                          timeouts, total_seconds, max_bytes) as response:
            yield response
