"""Tests for deep healthcheck (T20).

TDD: tests define the contract. Implementation in notifier.py.
"""
import json
import os
import sys
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from notifier import check_gitlab, check_llm, run_health_checks


class FakeHTTPServer:
    """Captures GET/POST requests and returns configurable responses."""

    def __init__(self, port=0, response_code=200, response_body=b'{}', fail_after=0):
        self.port = port
        self.response_code = response_code
        self.response_body = response_body
        self.fail_after = fail_after  # 0 = always succeed, N = fail after N reqs
        self.call_count = 0
        self._server = None
        self._thread = None

    def start(self):
        slf = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                slf.call_count += 1
                if slf.fail_after > 0 and slf.call_count > slf.fail_after:
                    handler.send_response(500)
                    handler.end_headers()
                    return
                handler.send_response(slf.response_code)
                handler.send_header("Content-Type", "application/json")
                handler.end_headers()
                handler.wfile.write(slf.response_body)

            def do_POST(handler):
                return self.do_GET(handler)

            def log_message(self, *args, **kwargs):
                pass

        self._server = HTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


class CheckGitlabTests(unittest.TestCase):
    """check_gitlab() pings GitLab /api/v4/version."""

    def test_returns_ok_when_reachable(self):
        srv = FakeHTTPServer(response_code=200, response_body=b'{"version":"16.0"}')
        srv.start()
        try:
            result = check_gitlab(srv.url + "/api/v4/version", timeout=2)
            self.assertEqual(result["status"], "ok")
            self.assertIn("latency_ms", result)
            self.assertGreaterEqual(result["latency_ms"], 0)
        finally:
            srv.stop()

    def test_returns_fail_when_5xx(self):
        srv = FakeHTTPServer(response_code=500)
        srv.start()
        try:
            result = check_gitlab(srv.url + "/api/v4/version", timeout=2)
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["http_code"], 500)
        finally:
            srv.stop()

    def test_returns_fail_on_connection_error(self):
        # Unreachable port
        result = check_gitlab("http://127.0.0.1:1/api/v4/version", timeout=1)
        self.assertEqual(result["status"], "fail")
        self.assertIn("error", result)


class CheckLlmTests(unittest.TestCase):
    """check_llm() pings LLM /v1/models endpoint."""

    def test_returns_ok_when_reachable(self):
        srv = FakeHTTPServer(response_code=200, response_body=b'{"data":[]}')
        srv.start()
        try:
            result = check_llm(srv.url + "/v1/models", api_key="test-key", timeout=2)
            self.assertEqual(result["status"], "ok")
        finally:
            srv.stop()

    def test_returns_fail_on_5xx(self):
        srv = FakeHTTPServer(response_code=503)
        srv.start()
        try:
            result = check_llm(srv.url + "/v1/models", api_key="test-key", timeout=2)
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["http_code"], 503)
        finally:
            srv.stop()

    def test_returns_fail_on_connection_error(self):
        result = check_llm("http://127.0.0.1:1/v1/models", api_key="test-key", timeout=1)
        self.assertEqual(result["status"], "fail")


class RunHealthChecksTests(unittest.TestCase):
    """run_health_checks() runs all checks and returns aggregate status."""

    def test_all_ok_returns_healthy(self):
        gitlab = FakeHTTPServer(response_code=200)
        llm = FakeHTTPServer(response_code=200)
        gitlab.start()
        llm.start()
        try:
            result = run_health_checks(
                gitlab_url=gitlab.url + "/api/v4/version",
                llm_url=llm.url + "/v1/models",
                llm_api_key="test",
                gitlab_timeout=2,
                llm_timeout=2,
            )
            self.assertEqual(result["status"], "healthy")
            self.assertEqual(result["checks"]["gitlab"]["status"], "ok")
            self.assertEqual(result["checks"]["llm"]["status"], "ok")
        finally:
            gitlab.stop()
            llm.stop()

    def test_gitlab_down_returns_unhealthy(self):
        # Unreachable GitLab, reachable LLM
        llm = FakeHTTPServer(response_code=200)
        llm.start()
        try:
            result = run_health_checks(
                gitlab_url="http://127.0.0.1:1/api/v4/version",
                llm_url=llm.url + "/v1/models",
                llm_api_key="test",
                gitlab_timeout=1,
                llm_timeout=2,
            )
            self.assertEqual(result["status"], "unhealthy")
            self.assertEqual(result["checks"]["gitlab"]["status"], "fail")
            self.assertEqual(result["checks"]["llm"]["status"], "ok")
        finally:
            llm.stop()

    def test_llm_down_returns_unhealthy(self):
        gitlab = FakeHTTPServer(response_code=200)
        gitlab.start()
        try:
            result = run_health_checks(
                gitlab_url=gitlab.url + "/api/v4/version",
                llm_url="http://127.0.0.1:1/v1/models",
                llm_api_key="test",
                gitlab_timeout=2,
                llm_timeout=1,
            )
            self.assertEqual(result["status"], "unhealthy")
            self.assertEqual(result["checks"]["gitlab"]["status"], "ok")
            self.assertEqual(result["checks"]["llm"]["status"], "fail")
        finally:
            gitlab.stop()

    def test_both_down_returns_unhealthy(self):
        result = run_health_checks(
            gitlab_url="http://127.0.0.1:1/api/v4/version",
            llm_url="http://127.0.0.1:1/v1/models",
            llm_api_key="test",
            gitlab_timeout=1,
            llm_timeout=1,
        )
        self.assertEqual(result["status"], "unhealthy")
        self.assertEqual(result["checks"]["gitlab"]["status"], "fail")
        self.assertEqual(result["checks"]["llm"]["status"], "fail")


class ParallelExecutionTests(unittest.TestCase):
    """run_health_checks runs GitLab and LLM probes concurrently."""

    def test_parallel_not_sequential(self):
        # Set up two SEPARATE slow endpoints (1s each), one for GitLab,
        # one for LLM. Each endpoint backed by its own thread so the
        # server itself isn't the bottleneck (Python's HTTPServer is
        # single-threaded by default).
        # If our run_health_checks is sequential: total ~2s.
        # If our run_health_checks is parallel: ~1s.
        # Threshold 1.8s = parallel (with overhead), 2.5s as safety margin
        # for CI variability.
        from socketserver import ThreadingMixIn

        class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(1)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            def log_message(self, *args, **kwargs):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            start = time.monotonic()
            result = run_health_checks(
                gitlab_url=f"http://127.0.0.1:{port}/gitlab",
                llm_url=f"http://127.0.0.1:{port}/llm",
                llm_api_key="test",
                gitlab_timeout=5,
                llm_timeout=5,
            )
            elapsed = time.monotonic() - start
            # Parallel: ~1s. Sequential would be ~2s.
            self.assertLess(elapsed, 1.8,
                            f"Expected parallel execution (<1.8s), got {elapsed:.2f}s")
            self.assertEqual(result["status"], "healthy")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
