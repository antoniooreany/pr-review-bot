"""Tests for v1.0.0 features: metrics (T21), Jira context (T22),
severity classification (T23), audit logging request_id (T24).

TDD: tests define the contract.
"""
import json
import os
import re
import sys
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier


# ── T21 Metrics ─────────────────────────────────────────────────────────────

class MetricsTests(unittest.TestCase):
    """Metrics module exposes Prometheus-format counters and gauges."""

    def test_increment_counter(self):
        notifier.reset_metrics()
        notifier.metrics_inc("slack_posts_total", {"status": "ok"})
        notifier.metrics_inc("slack_posts_total", {"status": "ok"})
        notifier.metrics_inc("slack_posts_total", {"status": "fail"})
        text = notifier.metrics_render()
        self.assertIn("pr_review_bot_slack_posts_total{status=\"ok\"} 2", text)
        self.assertIn("pr_review_bot_slack_posts_total{status=\"fail\"} 1", text)

    def test_render_includes_help_and_type(self):
        notifier.reset_metrics()
        notifier.metrics_inc("test_counter")
        text = notifier.metrics_render()
        self.assertIn("# HELP pr_review_bot_test_counter", text)
        self.assertIn("# TYPE pr_review_bot_test_counter counter", text)

    def test_gauge_set(self):
        notifier.reset_metrics()
        notifier.metrics_set("last_health_check_timestamp_seconds", 1234567890)
        text = notifier.metrics_render()
        self.assertIn("pr_review_bot_last_health_check_timestamp_seconds 1234567890", text)

    def test_render_no_secrets(self):
        notifier.reset_metrics()
        notifier.metrics_inc("test", {"api_key": "secret123"})
        text = notifier.metrics_render()
        self.assertNotIn("secret123", text)


# ── T22 Jira ────────────────────────────────────────────────────────────────

class JiraFetchTests(unittest.TestCase):
    """fetch_jira_issue() reads Jira REST API for ticket context."""

    def test_returns_summary_and_description_on_success(self):
        srv = _FakeJiraServer(response_body=b'{"key":"HD-1234","fields":{"summary":"Add login","description":"Implement OAuth flow"}}')
        srv.start()
        try:
            result = notifier.fetch_jira_issue(
                "HD-1234",
                jira_url=srv.url,
                email="bot@winwin.travel",
                token="token123",
            )
            self.assertEqual(result["key"], "HD-1234")
            self.assertEqual(result["summary"], "Add login")
            self.assertEqual(result["description"], "Implement OAuth flow")
        finally:
            srv.stop()

    def test_returns_none_on_404(self):
        srv = _FakeJiraServer(response_code=404)
        srv.start()
        try:
            result = notifier.fetch_jira_issue(
                "HD-NOTFOUND",
                jira_url=srv.url,
                email="x", token="y",
            )
            self.assertIsNone(result)
        finally:
            srv.stop()

    def test_returns_none_on_connection_error(self):
        result = notifier.fetch_jira_issue(
            "HD-1234",
            jira_url="http://127.0.0.1:1",
            email="x", token="y",
            timeout=1,
        )
        self.assertIsNone(result)

    def test_extracts_jira_key_from_branch(self):
        self.assertEqual(notifier.extract_jira_key("HD-1234-add-login"), "HD-1234")
        self.assertEqual(notifier.extract_jira_key("feature/HD-7-foo"), "HD-7")
        self.assertIsNone(notifier.extract_jira_key("no-ticket-here"))
        self.assertIsNone(notifier.extract_jira_key(""))
        self.assertIsNone(notifier.extract_jira_key(None))


# ── T23 Severity ────────────────────────────────────────────────────────────

class SeverityTests(unittest.TestCase):
    """_extract_severity classifies bot comments by emoji markers."""

    def test_high_marker(self):
        self.assertEqual(notifier._extract_severity("🔴 HIGH: SQL injection"), "🔴 HIGH")

    def test_medium_marker(self):
        self.assertEqual(notifier._extract_severity("🟡 MEDIUM: missing test"), "🟡 MEDIUM")

    def test_low_marker(self):
        self.assertEqual(notifier._extract_severity("🟢 LOW: nitpick"), "🟢 LOW")

    def test_neutral_default(self):
        self.assertEqual(notifier._extract_severity("just a comment"), "ℹ️ NEUTRAL")

    def test_high_takes_precedence_when_multiple(self):
        # Order in our check tuple: HIGH, MEDIUM, LOW — HIGH wins.
        text = "🔴 HIGH security\n🟡 MEDIUM style"
        self.assertEqual(notifier._extract_severity(text), "🔴 HIGH")


# ── T24 Audit logging ───────────────────────────────────────────────────────

class RequestIdTests(unittest.TestCase):
    """generate_request_id returns 12-char hex strings, all unique."""

    def test_length_is_12(self):
        rid = notifier.generate_request_id()
        self.assertEqual(len(rid), 12)

    def test_is_hex(self):
        rid = notifier.generate_request_id()
        self.assertRegex(rid, r"^[0-9a-f]{12}$")

    def test_unique(self):
        ids = {notifier.generate_request_id() for _ in range(1000)}
        self.assertEqual(len(ids), 1000)


# ── Test helpers ────────────────────────────────────────────────────────────

class _FakeJiraServer:
    def __init__(self, port=0, response_code=200, response_body=b'{}'):
        self.port = port
        self.response_code = response_code
        self.response_body = response_body

    def start(self):
        slf = self
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                slf.last_path = self.path
                self.send_response(slf.response_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(slf.response_body)
            def log_message(self, *a, **kw): pass
        self._server = HTTPServer(("127.0.0.1", self.port), H)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
