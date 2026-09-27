"""Tests for llm_review_one.py: post_to_pr() posts review to GitHub PR."""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

# Import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "smoke"))

import llm_review_one


class PostToPRTests(unittest.TestCase):
    """post_to_pr() invokes gh CLI with correct arguments."""

    def setUp(self):
        llm_review_one.REPO = "test-repo"
        llm_review_one.PR_NUMBER = 42

    def test_post_calls_gh_pr_comment_with_body(self):
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            llm_review_one.post_to_pr("Review text here")
            # Verify gh was called with expected args
            args = mock_run.call_args[0][0]
            self.assertEqual(args[0], "gh")
            self.assertEqual(args[1], "pr")
            self.assertEqual(args[2], "comment")
            self.assertEqual(args[3], "42")
            self.assertEqual(args[4], "--repo")
            self.assertEqual(args[5], "antoniooreany/test-repo")
            self.assertEqual(args[6], "--body")
            body = args[7]
            self.assertIn("PR-Agent review (via LLM)", body)
            self.assertIn("Review text here", body)
            self.assertIn("pr-review-bot", body)

    def test_post_handles_failure_gracefully(self):
        """gh CLI failure → logs warning, doesn't crash."""
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="not found")
            # Should not raise
            llm_review_one.post_to_pr("text")  # exits via print but doesn't raise

    def test_post_uses_module_level_repo_and_pr(self):
        """post_to_pr uses module-level REPO and PR_NUMBER constants."""
        llm_review_one.REPO = "different-repo"
        llm_review_one.PR_NUMBER = 99
        with patch.object(subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            llm_review_one.post_to_pr("x")
            args = mock_run.call_args[0][0]
            # args is a list, so use 'in' on the joined string or check specific index
            self.assertIn("antoniooreany/different-repo", args)
            self.assertEqual(args[3], "99")

    def test_main_calls_post_to_pr(self):
        """main() should call post_to_pr with the LLM output."""
        # Stub out everything main() depends on, including the OpenAI client
        # to avoid real API calls during unit tests.
        with patch.object(llm_review_one, "fetch_info") as mock_info, \
             patch.object(llm_review_one, "fetch_diff", return_value="diff"), \
             patch.object(llm_review_one, "client") as mock_client, \
             patch.object(llm_review_one, "post_to_pr") as mock_post, \
             patch("builtins.print"):  # suppress output noise
            mock_info.return_value = {
                "title": "Test PR",
                "headRefName": "test-branch",
                "changedFiles": 1,
                "additions": 5,
                "deletions": 2,
            }
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "LLM review text"
            mock_response.usage.total_tokens = 100
            mock_response.model = "gpt-5.4-mini"
            mock_client.chat.completions.create.return_value = mock_response

            llm_review_one.REPO = "x"
            llm_review_one.PR_NUMBER = 1
            llm_review_one.main()

            mock_post.assert_called_once_with("LLM review text")


if __name__ == "__main__":
    unittest.main(verbosity=2)
