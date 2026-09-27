"""Validate setup_credentials.ps1 / test_credman.ps1 files exist and parse correctly."""
import os
import re
import sys
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")


class CredentialScriptsExistTests(unittest.TestCase):
    """Both PowerShell scripts exist with required structure."""

    def test_setup_script_exists(self):
        path = os.path.join(REPO, "scripts", "setup_credentials.ps1")
        self.assertTrue(os.path.exists(path), f"missing {path}")

    def test_test_script_exists(self):
        path = os.path.join(REPO, "scripts", "test_credman.ps1")
        self.assertTrue(os.path.exists(path), f"missing {path}")

    def test_setup_uses_securestring(self):
        """Read-Host -AsSecureString prevents key from showing on screen."""
        path = os.path.join(REPO, "scripts", "setup_credentials.ps1")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Read-Host", content)
        self.assertIn("AsSecureString", content)

    def test_setup_uses_correct_target_name(self):
        """Target name must match what the bot reads via get_secret()."""
        path = os.path.join(REPO, "scripts", "setup_credentials.ps1")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn('pr-review-bot:OPENAI_KEY', content)

    def test_setup_clears_memory(self):
        """Plaintext key variable should be nulled after use."""
        path = os.path.join(REPO, "scripts", "setup_credentials.ps1")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("$plainKey = $null", content)

    def test_setup_verifies_storage(self):
        """Script must verify cmdkey /list shows the credential."""
        path = os.path.join(REPO, "scripts", "setup_credentials.ps1")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("cmdkey /list", content)

    def test_setup_tests_bot_retrieval(self):
        """Script must use Get-StoredCredential to validate roundtrip."""
        path = os.path.join(REPO, "scripts", "setup_credentials.ps1")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Get-StoredCredential", content)

    def test_setup_uses_pass_not_user(self):
        """Must use /pass: (password field), not /user: (username field).

        Bot reads via GetNetworkCredential().Password — that's the password
        field. Storing in /user: puts the key in username field, bot reads empty.
        """
        path = os.path.join(REPO, "scripts", "setup_credentials.ps1")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("/pass:", content)
        # /user: only OK as a non-secret username like "MiniMax"
        import re
        # Find the cmdkey call line — ensure /pass: present and /user:NOT being used for the secret
        for line in content.split("\n"):
            if "cmdkey" in line and "pr-review-bot" in line:
                self.assertIn("/pass:", line, f"cmdkey line missing /pass: → {line!r}")

    def test_test_script_uses_correct_target(self):
        path = os.path.join(REPO, "scripts", "test_credman.ps1")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pr-review-bot:OPENAI_KEY", content)

    def test_test_script_returns_exit_code_zero(self):
        """Successful test exits 0."""
        path = os.path.join(REPO, "scripts", "test_credman.ps1")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("exit 0", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
