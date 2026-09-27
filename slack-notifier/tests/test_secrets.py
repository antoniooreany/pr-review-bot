"""Tests for T29: secrets resolution from env -> Windows Credential Manager.

TDD: tests define the contract. Implementation in notifier.py.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier


class SecretResolutionTests(unittest.TestCase):
    """get_secret() resolves a secret by checking env first, then CredMan."""

    def test_env_var_takes_precedence(self):
        with patch.dict(os.environ, {"PR_TEST_KEY": "from-env"}):
            with patch.object(notifier, "_read_windows_credman") as mock_cred:
                result = notifier.get_secret("PR_TEST_KEY", credman_target="pr-review-bot")
                self.assertEqual(result, "from-env")
                mock_cred.assert_not_called()

    def test_falls_back_to_credman_when_env_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "PR_TEST_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(notifier, "_read_windows_credman", return_value="from-credman") as mock_cred:
                result = notifier.get_secret("PR_TEST_KEY", credman_target="pr-review-bot")
                self.assertEqual(result, "from-credman")
                mock_cred.assert_called_once_with("pr-review-bot", "PR_TEST_KEY")

    def test_returns_none_when_both_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "PR_TEST_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(notifier, "_read_windows_credman", return_value=None):
                result = notifier.get_secret("PR_TEST_KEY")
                self.assertIsNone(result)

    def test_returns_fallback_when_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "PR_TEST_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(notifier, "_read_windows_credman", return_value=None):
                result = notifier.get_secret("PR_TEST_KEY", fallback="default-value")
                self.assertEqual(result, "default-value")

    def test_credman_failure_doesnt_crash(self):
        """If PowerShell is missing or fails, we get None, not exception."""
        env = {k: v for k, v in os.environ.items() if k != "PR_TEST_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(notifier, "_read_windows_credman", side_effect=OSError("no powershell")):
                result = notifier.get_secret("PR_TEST_KEY")
                self.assertIsNone(result)


class WindowsCredManTests(unittest.TestCase):
    """_read_windows_credman() reads credential via PowerShell."""

    def test_returns_password_on_success(self):
        fake_result = type("R", (), {"returncode": 0, "stdout": "secret123\n", "stderr": ""})()
        with patch("subprocess.run", return_value=fake_result):
            result = notifier._read_windows_credman("pr-review-bot", "OPENAI_KEY")
            self.assertEqual(result, "secret123")

    def test_returns_none_on_nonzero_exit(self):
        fake_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "not found"})()
        with patch("subprocess.run", return_value=fake_result):
            result = notifier._read_windows_credman("pr-review-bot", "MISSING_KEY")
            self.assertIsNone(result)

    def test_returns_none_on_subprocess_exception(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("powershell not found")):
            result = notifier._read_windows_credman("pr-review-bot", "OPENAI_KEY")
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
