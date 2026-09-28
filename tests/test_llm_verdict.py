"""TDD tests for LLM verdict + GitHub status check posting."""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "smoke"))

import llm_review_one


class DetermineVerdictTests(unittest.TestCase):
    """determine_verdict() classifies review text by severity."""

    def test_no_findings_returns_approve(self):
        event, conclusion, summary = llm_review_one.determine_verdict(
            "No significant issues found. Looks clean."
        )
        self.assertEqual(event, "APPROVE")
        self.assertEqual(conclusion, "success")
        self.assertIn("safe to merge", summary.lower())

    def test_only_low_severity_returns_approve(self):
        text = "- 🟢 LOW [file]: small style nit\n- 🟢 LOW [file]: typo"
        event, conclusion, _ = llm_review_one.determine_verdict(text)
        self.assertEqual(event, "APPROVE")
        self.assertEqual(conclusion, "success")

    def test_medium_severity_returns_comment(self):
        text = "- 🟡 MEDIUM [file]: doc missing"
        event, conclusion, _ = llm_review_one.determine_verdict(text)
        self.assertEqual(event, "COMMENT")
        self.assertEqual(conclusion, "neutral")

    def test_high_severity_returns_request_changes(self):
        text = "- 🔴 HIGH [file]: security issue"
        event, conclusion, _ = llm_review_one.determine_verdict(text)
        self.assertEqual(event, "REQUEST_CHANGES")
        self.assertEqual(conclusion, "failure")

    def test_high_takes_priority_over_medium_and_low(self):
        text = (
            "- 🔴 HIGH: critical bug\n"
            "- 🟡 MEDIUM: doc issue\n"
            "- 🟢 LOW: style nit"
        )
        event, _, _ = llm_review_one.determine_verdict(text)
        self.assertEqual(event, "REQUEST_CHANGES")

    def test_summary_includes_count_when_issues(self):
        text = "- 🔴 HIGH [a]: x\n- 🔴 HIGH [b]: y"
        _, _, summary = llm_review_one.determine_verdict(text)
        self.assertIn("2", summary)


class PostCheckRunTests(unittest.TestCase):
    """post_check_run() invokes gh api with correct args."""

    def setUp(self):
        llm_review_one.REPO = "test-repo"
        llm_review_one.PR_NUMBER = 42
        llm_review_one.HEAD_SHA = "abc123def456"

    def test_post_check_run_failure(self):
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout='{"id": 99}')
            llm_review_one.post_check_run("failure", "🔴 HIGH issue")
            args = mock_run.call_args[0][0]
            # GitHub Status API: POST repos/.../statuses/<sha>
            self.assertEqual(args[0], "gh")
            self.assertEqual(args[1], "api")
            self.assertEqual(args[2], "repos/antoniooreany/test-repo/statuses/abc123def456")
            self.assertIn("state=failure", args)
            self.assertIn("context=pr-review-bot/pr-review", args)
            self.assertIn("🔴 HIGH", " ".join(args))  # description field contains summary text
            # Verify no Authorization header (would leak token)
            self.assertNotIn("Authorization", args)

    def test_post_check_run_success(self):
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            llm_review_one.post_check_run("success", "All good")
            args = mock_run.call_args[0][0]
            # GitHub Status API uses 'state' not 'conclusion'
            self.assertIn("state=success", args)

    def test_post_check_run_handles_gh_failure_gracefully(self):
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="auth error")
            # Should not raise
            llm_review_one.post_check_run("neutral", "x")


class PostReviewTests(unittest.TestCase):
    """post_review() submits a GitHub PR review with state event."""

    def setUp(self):
        llm_review_one.REPO = "test-repo"
        llm_review_one.PR_NUMBER = 42

    def test_post_review_approve(self):
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            llm_review_one.post_review("APPROVE", "LGTM")
            args = mock_run.call_args[0][0]
            self.assertEqual(args[2], "repos/antoniooreany/test-repo/pulls/42/reviews")
            self.assertIn("-f", args)
            self.assertIn("event=APPROVE", args)
            self.assertIn("body=LGTM", args)

    def test_post_review_request_changes(self):
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            llm_review_one.post_review("REQUEST_CHANGES", "Fix security")
            args = mock_run.call_args[0][0]
            self.assertIn("event=REQUEST_CHANGES", args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
