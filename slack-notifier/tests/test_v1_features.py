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


# ── T22 wire-in: Jira context flows into webhook handling ───────────────────

class JiraWiredTests(unittest.TestCase):
    """When MR has HD-NNN, Jira fetch is called and context flows through."""

    def test_jira_summary_added_to_slack_payload(self):
        from unittest.mock import patch
        captured = {}

        def fake_post(url, mr_url, mr_title, author, severity, summary, **kwargs):
            captured["mr_title"] = mr_title
            captured["summary"] = summary
            captured["url"] = url
            return True

        # Pretend Jira returns a ticket
        def fake_fetch(key, **kwargs):
            return {"key": key, "summary": "Add login", "description": "OAuth flow"}

        with patch.object(notifier, "post_to_slack", side_effect=fake_post), \
             patch.object(notifier, "fetch_jira_issue", side_effect=fake_fetch):
            payload = {
                "object_kind": "note",
                "object_attributes": {"noteable_type": "MergeRequest", "note": "🤖 review"},
                "merge_request": {
                    "url": "https://gitlab/x/-/merge_requests/42",
                    "title": "HD-1234: Add login flow",
                },
                "user": {"id": 99, "username": "pr-review-bot"},
            }
            result = notifier.handle_webhook(
                payload,
                bot_user_id=99,
                slack_webhook_url="https://slack.example/webhook",
                jira_url="https://jira.example",
                jira_email="bot@example",
                jira_token="tok",
            )
            self.assertTrue(result)
            # Summary should now include Jira ticket title
            self.assertIn("HD-1234", captured["summary"])
            self.assertIn("Add login", captured["summary"])

    def test_no_jira_key_skips_fetch(self):
        from unittest.mock import patch
        captured = {}

        def fake_post(*args, **kwargs):
            captured["called"] = True
            return True

        def fake_fetch(*args, **kwargs):
            captured["fetched"] = True
            return None

        with patch.object(notifier, "post_to_slack", side_effect=fake_post), \
             patch.object(notifier, "fetch_jira_issue", side_effect=fake_fetch):
            payload = {
                "object_kind": "note",
                "object_attributes": {"noteable_type": "MergeRequest", "note": "review"},
                "merge_request": {
                    "url": "https://gitlab/x/-/merge_requests/42",
                    "title": "no-jira-key-here",
                },
                "user": {"id": 99, "username": "bot"},
            }
            notifier.handle_webhook(
                payload,
                bot_user_id=99,
                slack_webhook_url="https://slack.example/webhook",
            )
            self.assertTrue(captured.get("called"))
            self.assertFalse(captured.get("fetched", False))

    def test_jira_fetch_failure_doesnt_break(self):
        from unittest.mock import patch

        def fake_post(*args, **kwargs):
            return True

        def fake_fetch(*args, **kwargs):
            return None  # graceful failure

        with patch.object(notifier, "post_to_slack", side_effect=fake_post), \
             patch.object(notifier, "fetch_jira_issue", side_effect=fake_fetch):
            payload = {
                "object_kind": "note",
                "object_attributes": {"noteable_type": "MergeRequest", "note": "x"},
                "merge_request": {"url": "x", "title": "HD-9999: broken ticket"},
                "user": {"id": 99, "username": "bot"},
            }
            result = notifier.handle_webhook(payload, bot_user_id=99)
            self.assertTrue(result)  # graceful, not failure


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


class RequestIdCorrelationTests(unittest.TestCase):
    """T24 fix: rid flows through webhook → handle_webhook → post_to_slack
    so all log lines for one request can be correlated."""

    def test_rid_appears_in_slack_log(self):
        import urllib.request
        from unittest.mock import patch, MagicMock

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            with patch.object(notifier, "log") as mock_log:
                rid = "abcdef012345"
                payload = {
                    "object_kind": "note",
                    "object_attributes": {"noteable_type": "MergeRequest", "note": "🤖 review"},
                    "merge_request": {"url": "https://gitlab/x", "title": "test"},
                    "user": {"id": 1, "username": "bot"},
                }
                notifier.handle_webhook(
                    payload,
                    bot_user_id=1,
                    slack_webhook_url="https://slack/x",
                    request_id=rid,
                )

                # Find the slack_posted log call
                slack_log_calls = [c for c in mock_log.info.call_args_list
                                   if "slack_posted" in str(c)]
                self.assertTrue(len(slack_log_calls) > 0,
                                "Expected at least one slack_posted log call")
                # rid should be in the log message
                self.assertIn(rid, str(slack_log_calls[0]))


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
