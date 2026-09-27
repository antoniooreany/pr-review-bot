"""Unit tests for slack-notifier service.

Test-first: these tests define the contract for the notifier. Implementation
lives in notifier.py. Run with `python -m unittest slack-notifier/tests/test_notifier.py`.
"""
import json
import os
import sys
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

# Add parent dir to path so we can import notifier.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from notifier import handle_webhook, post_to_slack, is_bot_comment


class FakeSlackServer:
    """Captures POST requests from notifier.post_to_slack for assertions."""

    def __init__(self, port=0, response_code=200, fail_times=0):
        self.port = port
        self.received_requests = []
        self.response_code = response_code
        self.fail_times = fail_times
        self.call_count = 0
        self._server = None
        self._thread = None

    def start(self):
        slf = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                length = int(handler.headers.get("Content-Length", 0))
                body = handler.rfile.read(length).decode("utf-8") if length else ""
                slf.received_requests.append({
                    "headers": dict(handler.headers),
                    "body": body,
                })
                slf.call_count += 1
                if slf.fail_times > 0:
                    slf.fail_times -= 1
                    handler.send_response(500)
                    handler.end_headers()
                    return
                handler.send_response(slf.response_code)
                handler.end_headers()

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
    def last_payload(self):
        if not self.received_requests:
            return None
        return json.loads(self.received_requests[-1]["body"])


class BotDetectionTests(unittest.TestCase):
    """is_bot_comment() identifies notes authored by the configured bot user."""

    def test_returns_true_for_bot_author(self):
        # Real GitLab note payload uses "user" key, not "author".
        self.assertTrue(is_bot_comment({"user": {"id": 42}}, bot_user_id=42))

    def test_returns_false_for_other_user(self):
        self.assertFalse(is_bot_comment({"user": {"id": 99}}, bot_user_id=42))

    def test_returns_false_for_missing_author(self):
        self.assertFalse(is_bot_comment({}, bot_user_id=42))


class SlackPostTests(unittest.TestCase):
    """post_to_slack() sends formatted payload to Slack webhook URL."""

    def setUp(self):
        self.fake_slack = FakeSlackServer()
        self.fake_slack.start()
        self.slack_url = f"http://127.0.0.1:{self.fake_slack.port}/webhook"

    def tearDown(self):
        self.fake_slack.stop()

    def test_posts_formatted_payload(self):
        result = post_to_slack(
            self.slack_url,
            mr_url="https://gitlab/test/repo/-/merge_requests/42",
            mr_title="HD-1234 add feature",
            author="pr-review-bot",
            severity="🟡 MEDIUM",
            summary="3 issues found",
        )
        self.assertTrue(result)
        payload = self.fake_slack.last_payload
        self.assertEqual(payload["text"], "🤖 PR-Agent review on HD-1234 add feature")
        # Block kit should have fields for severity, author, summary, link
        blocks = payload["blocks"]
        self.assertTrue(any("HD-1234" in str(b) for b in blocks))

    def test_returns_false_when_slack_returns_500(self):
        # Override the fake server to fail
        self.fake_slack.stop()
        failing = FakeSlackServer(response_code=500)
        failing.start()
        try:
            url = f"http://127.0.0.1:{failing.port}/webhook"
            # post_to_slack should retry on 500 and eventually give up
            result = post_to_slack(
                url,
                mr_url="https://gitlab/x/-/merge_requests/1",
                mr_title="t",
                author="bot",
                severity="🟢 LOW",
                summary="ok",
                max_retries=2,
                retry_delay=0.01,
            )
            self.assertFalse(result)
            self.assertGreaterEqual(failing.call_count, 2)
        finally:
            failing.stop()


class WebhookHandlerTests(unittest.TestCase):
    """handle_webhook() dispatches GitLab note events."""

    def setUp(self):
        self.fake_slack = FakeSlackServer()
        self.fake_slack.start()
        self.slack_url = f"http://127.0.0.1:{self.fake_slack.port}/webhook"

    def tearDown(self):
        self.fake_slack.stop()

    def test_bot_comment_triggers_slack_post(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "noteable_type": "MergeRequest",
                "note": "🤖 AI review: 2 issues found",
            },
            "merge_request": {
                "url": "https://gitlab/x/-/merge_requests/42",
                "title": "HD-1234 test",
            },
            "user": {"id": 42, "username": "pr-review-bot"},
        }
        result = handle_webhook(
            payload,
            bot_user_id=42,
            slack_webhook_url=self.slack_url,
        )
        self.assertTrue(result)
        self.assertEqual(len(self.fake_slack.received_requests), 1)

    def test_non_bot_comment_ignored(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "noteable_type": "MergeRequest",
                "note": "lgtm",
            },
            "merge_request": {
                "url": "https://gitlab/x/-/merge_requests/42",
                "title": "test",
            },
            "user": {"id": 99, "username": "human"},
        }
        result = handle_webhook(
            payload,
            bot_user_id=42,
            slack_webhook_url=self.slack_url,
        )
        self.assertTrue(result)  # Returns true (= processed), but no Slack call
        self.assertEqual(len(self.fake_slack.received_requests), 0)

    def test_no_slack_url_is_noop(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {"noteable_type": "MergeRequest", "note": "x"},
            "merge_request": {"url": "x", "title": "x"},
            "user": {"id": 42, "username": "bot"},
        }
        result = handle_webhook(
            payload,
            bot_user_id=42,
            slack_webhook_url="",  # disabled
        )
        self.assertTrue(result)  # Notifies no failure
        self.assertEqual(len(self.fake_slack.received_requests), 0)

    def test_non_note_event_ignored(self):
        payload = {
            "object_kind": "merge_request",
            "user": {"id": 42, "username": "bot"},
        }
        result = handle_webhook(
            payload,
            bot_user_id=42,
            slack_webhook_url=self.slack_url,
        )
        self.assertTrue(result)
        self.assertEqual(len(self.fake_slack.received_requests), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
