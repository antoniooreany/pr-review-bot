"""Tests for webhook signature validation in slack-notifier.

TDD: these tests define the contract. Implementation in notifier.py.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from notifier import verify_signature


class SignatureValidationTests(unittest.TestCase):
    """verify_signature() compares X-Gitlab-Token to WEBHOOK_SECRET."""

    def test_returns_true_when_token_matches(self):
        headers = {"X-Gitlab-Token": "secret123"}
        self.assertTrue(verify_signature(headers, expected_token="secret123"))

    def test_returns_false_when_token_wrong(self):
        headers = {"X-Gitlab-Token": "wrong-token"}
        self.assertFalse(verify_signature(headers, expected_token="secret123"))

    def test_returns_false_when_token_missing(self):
        headers = {}  # no X-Gitlab-Token at all
        self.assertFalse(verify_signature(headers, expected_token="secret123"))

    def test_returns_false_when_token_empty_string(self):
        headers = {"X-Gitlab-Token": ""}
        self.assertFalse(verify_signature(headers, expected_token="secret123"))

    def test_no_expected_token_disables_validation(self):
        # If WEBHOOK_SECRET is unset in env (development mode), accept anything.
        # This is logged as a warning by the caller.
        headers = {"X-Gitlab-Token": "anything"}
        self.assertTrue(verify_signature(headers, expected_token=""))

    def test_header_lookup_is_case_insensitive(self):
        # HTTP headers are case-insensitive; our function should handle both.
        headers = {"x-gitlab-token": "secret123"}  # lowercase
        self.assertTrue(verify_signature(headers, expected_token="secret123"))

    def test_returns_false_on_partial_match(self):
        # Prefix matches should NOT authenticate (constant-time + exact match).
        headers = {"X-Gitlab-Token": "secret"}
        self.assertFalse(verify_signature(headers, expected_token="secret123"))

    def test_returns_false_on_suffix_match(self):
        headers = {"X-Gitlab-Token": "23"}
        self.assertFalse(verify_signature(headers, expected_token="secret123"))


class TimingSafetyTests(unittest.TestCase):
    """verify_signature must use constant-time comparison."""

    def test_uses_hmac_compare_digest(self):
        import inspect
        import re
        from notifier import verify_signature
        src = inspect.getsource(verify_signature)
        # Match the actual call site: "compare_digest(" with NO whitespace
        # between the function name and the opening paren. This avoids false
        # positives from docstring mentions like "via hmac.compare_digest (no...".
        self.assertRegex(
            src,
            r'\bcompare_digest\(',
            "verify_signature must call hmac.compare_digest(...) for constant-time comparison",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
