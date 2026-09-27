"""Tests for T26 (admin commands) + T30 (GitHub support).

TDD: tests define the contract.
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier


def reset():
    """Reset module state between tests."""
    notifier.reset_metrics()
    notifier._bot_state["enabled"] = True
    notifier._bot_state["disabled_at"] = None
    notifier._bot_state["disabled_by"] = None
    notifier._bot_state["admin_user_id"] = "1"


# ── T30 provider detection ─────────────────────────────────────────────────

class ProviderDetectionTests(unittest.TestCase):
    """_detect_provider returns 'gitlab' | 'github' | None."""

    def test_gitlab_payload_detected(self):
        payload = {"object_kind": "note", "object_attributes": {}}
        self.assertEqual(notifier._detect_provider(payload, {}), "gitlab")

    def test_github_issue_comment_detected(self):
        payload = {"action": "created", "issue": {"pull_request": {}}}
        headers = {"X-Github-Event": "issue_comment"}
        self.assertEqual(notifier._detect_provider(payload, headers), "github")

    def test_unknown_payload_returns_none(self):
        self.assertIsNone(notifier._detect_provider({}, {}))

    def test_github_event_header_alone_is_not_enough(self):
        # Need both header AND issue_comment-shaped payload
        payload = {"random": "data"}
        self.assertIsNone(notifier._detect_provider(payload, {"X-Github-Event": "issue_comment"}))


class ProviderExtractionTests(unittest.TestCase):
    """Provider-specific field extractors return normalized data."""

    def test_gitlab_extract_comment(self):
        payload = {"object_attributes": {"note": "hello world"}}
        self.assertEqual(notifier._extract_comment(payload, "gitlab"), "hello world")

    def test_github_extract_comment(self):
        payload = {"comment": {"body": "hello github"}}
        self.assertEqual(notifier._extract_comment(payload, "github"), "hello github")

    def test_gitlab_extract_pr_url(self):
        payload = {"merge_request": {"url": "https://gitlab/x/y/-/merge_requests/1"}}
        self.assertEqual(
            notifier._extract_pr_url(payload, "gitlab"),
            "https://gitlab/x/y/-/merge_requests/1",
        )

    def test_github_extract_pr_url(self):
        payload = {"issue": {"html_url": "https://github.com/o/r/pull/42"}}
        self.assertEqual(
            notifier._extract_pr_url(payload, "github"),
            "https://github.com/o/r/pull/42",
        )

    def test_gitlab_extract_author_id(self):
        payload = {"user": {"id": 99}}
        self.assertEqual(notifier._extract_author_id(payload, "gitlab"), 99)

    def test_github_extract_author_id(self):
        payload = {"sender": {"id": 12345, "login": "my-bot"}}
        # GitHub uses string login, not numeric ID
        self.assertEqual(notifier._extract_author_id(payload, "github"), "my-bot")

    def test_gitlab_extract_mr_title(self):
        payload = {"merge_request": {"title": "HD-123: add login"}}
        self.assertEqual(notifier._extract_mr_title(payload, "gitlab"), "HD-123: add login")

    def test_github_extract_mr_title(self):
        payload = {"issue": {"title": "HD-456: fix bug"}}
        self.assertEqual(notifier._extract_mr_title(payload, "github"), "HD-456: fix bug")


# ── T26 admin commands ──────────────────────────────────────────────────────

class AdminCommandTests(unittest.TestCase):
    """handle_admin_command returns True if command processed, False otherwise."""

    def setUp(self):
        reset()

    def test_status_command_returns_true(self):
        result = notifier.handle_admin_command(
            "/bot status", admin_user_id="1", slack_webhook_url="https://slack/x"
        )
        self.assertTrue(result)

    def test_disable_command_flips_state(self):
        notifier.handle_admin_command("/bot disable", admin_user_id="1")
        self.assertFalse(notifier._bot_state["enabled"])
        self.assertEqual(notifier._bot_state["disabled_by"], "1")

    def test_enable_command_restores_state(self):
        notifier._bot_state["enabled"] = False
        notifier.handle_admin_command("/bot enable", admin_user_id="1")
        self.assertTrue(notifier._bot_state["enabled"])

    def test_unknown_command_returns_false(self):
        result = notifier.handle_admin_command(
            "/bot nuke", admin_user_id="1"
        )
        self.assertFalse(result)

    def test_non_admin_cannot_run_status(self):
        """Non-admin user calling /bot status returns False, no Slack post."""
        with patch.object(notifier, "post_to_slack") as mock_post:
            result = notifier.handle_admin_command(
                "/bot status", admin_user_id="2", slack_webhook_url="https://slack/x"
            )
            self.assertFalse(result)
            mock_post.assert_not_called()

    def test_disabled_bot_skips_slack_for_bot_review(self):
        """After /bot disable, subsequent bot notes don't post to Slack."""
        notifier.handle_admin_command("/bot disable", admin_user_id="1")

        with patch.object(notifier, "post_to_slack") as mock_post:
            result = notifier.handle_webhook(
                {
                    "object_kind": "note",
                    "object_attributes": {"noteable_type": "MergeRequest", "note": "🤖 review"},
                    "merge_request": {"url": "https://gitlab/x", "title": "t"},
                    "user": {"id": 99, "username": "bot"},
                },
                bot_user_id=99,
                slack_webhook_url="https://slack/x",
            )
            self.assertTrue(result)  # processed
            mock_post.assert_not_called()  # but not posted


# ── T30 handle_webhook multi-provider ────────────────────────────────────────

class MultiProviderWebhookTests(unittest.TestCase):
    """handle_webhook processes both GitLab and GitHub payloads."""

    def setUp(self):
        reset()

    def test_github_payload_processed(self):
        """GitHub issue_comment from bot → Slack post."""
        payload = {
            "action": "created",
            "comment": {"body": "🤖 AI review: looks good"},
            "issue": {
                "html_url": "https://github.com/o/r/pull/42",
                "title": "HD-789: new feature",
            },
            "sender": {"login": "my-bot"},
        }
        with patch.object(notifier, "post_to_slack", return_value=True) as mock_post:
            result = notifier.handle_webhook(
                payload,
                bot_user_id="my-bot",
                slack_webhook_url="https://slack/x",
            )
            self.assertTrue(result)
            mock_post.assert_called_once()
            # Verify the MR title made it through (post_to_slack uses kwargs)
            _, kwargs = mock_post.call_args
            self.assertIn("HD-789", kwargs.get("mr_title", ""))

    def test_gitlab_payload_still_works(self):
        """Regression: GitLab payload still processes."""
        payload = {
            "object_kind": "note",
            "object_attributes": {"noteable_type": "MergeRequest", "note": "🤖 review"},
            "merge_request": {"url": "https://gitlab/x/y/-/merge_requests/1", "title": "test"},
            "user": {"id": 99, "username": "bot"},
        }
        with patch.object(notifier, "post_to_slack", return_value=True) as mock_post:
            result = notifier.handle_webhook(
                payload, bot_user_id=99, slack_webhook_url="https://slack/x"
            )
            self.assertTrue(result)
            mock_post.assert_called_once()

    def test_unknown_provider_returns_true_no_post(self):
        """Unknown payload format → ignored, no error."""
        payload = {"random": "data"}
        with patch.object(notifier, "post_to_slack") as mock_post:
            result = notifier.handle_webhook(
                payload, bot_user_id=99, slack_webhook_url="https://slack/x"
            )
            self.assertTrue(result)  # intentional ignore
            mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
