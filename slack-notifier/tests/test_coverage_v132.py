"""T32 — push coverage from 82% toward 90%.

Targeted tests for uncovered lines in notifier.py:
- metrics_render edge cases (no labels, empty counters)
- WebhookHandler metrics endpoint
- handle_admin_command with valid verb but no slack_url (admin flow)
- _extract_* with malformed payloads (defensive defaults)
- post_to_slack with empty URL
- main() KeyboardInterrupt handling
"""
import json
import os
import signal
import socket
import sys
import threading
import unittest
from http.server import HTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier


# ── Metrics edge cases (lines 74, 101-110) ────────────────────────────────

class MetricsEdgeCaseTests(unittest.TestCase):
    """metrics_render / inc / set cover edge cases."""

    def setUp(self):
        notifier.reset_metrics()

    def test_increment_with_no_labels(self):
        notifier.metrics_inc("counter_no_labels")
        text = notifier.metrics_render()
        self.assertIn("pr_review_bot_counter_no_labels 1", text)

    def test_set_gauge_with_labels(self):
        notifier.metrics_set("queue_depth", 42, {"queue": "main"})
        text = notifier.metrics_render()
        self.assertIn('pr_review_bot_queue_depth{queue="main"} 42', text)

    def test_render_with_no_metrics(self):
        notifier.reset_metrics()
        # Don't add any metrics
        text = notifier.metrics_render()
        self.assertEqual(text.strip(), "")

    def test_render_escapes_quotes_in_label_values(self):
        """Quotation marks in label values must be escaped per Prometheus format."""
        notifier.metrics_inc("counter", {"key": 'value"with"quotes'})
        text = notifier.metrics_render()
        self.assertIn('key="value\\"with\\"quotes"', text)


# ── Admin command edge cases (lines 74, 145, 200) ────────────────────────

class AdminEdgeCaseTests(unittest.TestCase):
    """handle_admin_command edge cases."""

    def setUp(self):
        notifier.reset_metrics()
        notifier._bot_state["enabled"] = True
        notifier._bot_state["admin_user_id"] = "42"

    def test_admin_command_with_non_slash_command(self):
        result = notifier.handle_admin_command(
            "regular comment", admin_user_id="42"
        )
        self.assertFalse(result)

    def test_admin_command_with_empty_command(self):
        result = notifier.handle_admin_command("", admin_user_id="42")
        self.assertFalse(result)

    def test_admin_command_stats_with_no_slack_url(self):
        with patch.object(notifier, "post_to_slack", return_value=False) as mock:
            result = notifier.handle_admin_command(
                "/bot stats", admin_user_id="42", slack_webhook_url=""
            )
            self.assertTrue(result)
            mock.assert_called_once()

    def test_admin_command_disable_without_slack(self):
        with patch.object(notifier, "post_to_slack", return_value=False):
            result = notifier.handle_admin_command(
                "/bot disable", admin_user_id="42", slack_webhook_url=""
            )
            self.assertTrue(result)
            self.assertFalse(notifier._bot_state["enabled"])

    def test_admin_command_enable_when_already_enabled(self):
        # Idempotent — should still return True
        with patch.object(notifier, "post_to_slack", return_value=True):
            result = notifier.handle_admin_command("/bot enable", admin_user_id="42")
            self.assertTrue(result)
            self.assertTrue(notifier._bot_state["enabled"])


# ── Provider extraction defensive defaults (line 481) ─────────────────────

class ExtractionDefensiveTests(unittest.TestCase):
    """Extraction functions handle missing fields gracefully."""

    def test_extract_comment_returns_empty_for_unknown_provider(self):
        self.assertEqual(notifier._extract_comment({}, "unknown"), "")
        self.assertEqual(notifier._extract_comment({"comment": {}}, "github"), "")

    def test_extract_pr_url_returns_empty_for_unknown_provider(self):
        self.assertEqual(notifier._extract_pr_url({}, "unknown"), "")

    def test_extract_mr_title_returns_empty_for_unknown_provider(self):
        self.assertEqual(notifier._extract_mr_title({}, "unknown"), "")

    def test_extract_author_id_returns_none_for_unknown_provider(self):
        self.assertIsNone(notifier._extract_author_id({}, "unknown"))

    def test_extract_comment_handles_empty_payload(self):
        self.assertEqual(notifier._extract_comment({}, "gitlab"), "")
        self.assertEqual(notifier._extract_comment({}, "github"), "")

    def test_extract_author_id_handles_missing_user_sender(self):
        self.assertIsNone(notifier._extract_author_id({"user": {}}, "gitlab"))
        self.assertIsNone(notifier._extract_author_id({"sender": {}}, "github"))


# ── post_to_slack edge cases (lines 145, 804-821, 848-870) ───────────────

class PostToSlackEdgeCaseTests(unittest.TestCase):
    """post_to_slack edge cases: empty URL, urllib errors."""

    def test_empty_url_returns_false_without_http_call(self):
        """When url is empty/falsy, don't attempt any HTTP call."""
        with patch("urllib.request.urlopen") as mock:
            result = notifier.post_to_slack(
                "", mr_url="x", mr_title="t", author="bot",
                severity="s", summary="s",
            )
            self.assertFalse(result)
            mock.assert_not_called()

    def test_network_timeout_returns_false(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            result = notifier.post_to_slack(
                "http://127.0.0.1:1/", mr_url="x", mr_title="t",
                author="bot", severity="s", summary="s",
                max_retries=1, retry_delay=0.001,
            )
            self.assertFalse(result)


# ── WebhookHandler metrics endpoint (lines 523, 532, 541) ────────────────

class MetricsEndpointTests(unittest.TestCase):
    """/metrics endpoint returns Prometheus format."""

    def setUp(self):
        notifier.reset_metrics()
        # Use ephemeral port
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        notifier.WebhookHandler.bot_user_id = 1
        notifier.WebhookHandler.slack_webhook_url = ""
        notifier.WebhookHandler.webhook_secret = ""
        self.server = HTTPServer(("127.0.0.1", port), notifier.WebhookHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        # Trigger some metrics
        notifier.metrics_inc("test_metric", {"label": "value"})
        notifier.metrics_set("test_gauge", 42)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        notifier.reset_metrics()

    def _get(self, path):
        import urllib.request
        return urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=2)

    def test_metrics_endpoint_returns_prometheus_format(self):
        resp = self._get("/metrics")
        self.assertEqual(resp.status, 200)
        body = resp.read().decode("utf-8")
        self.assertIn("pr_review_bot_test_metric", body)
        self.assertIn("pr_review_bot_test_gauge 42", body)

    def test_metrics_endpoint_has_help_and_type(self):
        resp = self._get("/metrics")
        body = resp.read().decode("utf-8")
        self.assertIn("# HELP pr_review_bot_test_metric counter", body)
        self.assertIn("# TYPE pr_review_bot_test_metric counter", body)
        self.assertIn("# TYPE pr_review_bot_test_gauge gauge", body)


# ── main() boot path (lines 885-891) ─────────────────────────────────────

class MainBootTests(unittest.TestCase):
    """main() starts server and handles SIGINT."""

    def test_main_starts_http_server(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        with patch.dict(os.environ, {"PORT": str(port)}):
            with patch("signal.signal"):
                with patch("notifier.HTTPServer") as mock_server_class:
                    mock_instance = mock_server_class.return_value
                    # Simulate serve_forever raising KeyboardInterrupt after first call
                    mock_instance.serve_forever.side_effect = KeyboardInterrupt
                    try:
                        notifier.main()
                    except SystemExit:
                        pass

        mock_server_class.assert_called_once()
        call_args = mock_server_class.call_args
        self.assertEqual(call_args[0][0][0], "0.0.0.0")
        self.assertEqual(call_args[0][0][1], port)
        mock_instance.shutdown.assert_called_once()


# ── generate_request_id (line 145 is "unreachable" — defensive test) ───

class RequestIdGenerationTests(unittest.TestCase):
    def test_unique_ids(self):
        ids = {notifier.generate_request_id() for _ in range(1000)}
        self.assertEqual(len(ids), 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
