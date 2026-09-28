"""Tests for smoke/bot_review_demo.py markdown output and CLI args."""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "smoke"))

import bot_review_demo


SAMPLE_INFO = {
    "title": "HD-1754: fix question selector",
    "headRefName": "feature/hd-1754-fix",
    "additions": 50,
    "deletions": 20,
    "changedFiles": 3,
    "state": "opened",
    "url": "https://github.com/antoniooreany/test/pull/42",
}

SAMPLE_FINDINGS_HIGH = [
    {"id": "hardcoded_url", "severity": "HIGH", "line": 42, "match": "http://prod.com/api", "desc": "Hardcoded URL"},
]

SAMPLE_FINDINGS_MED = [
    {"id": "wildcard_import", "severity": "MEDIUM", "line": 5, "match": "import java.util.*", "desc": "Wildcard import"},
]

SAMPLE_FINDINGS_LOW = [
    {"id": "magic_number", "severity": "LOW", "line": 100, "match": "if (x == 100)", "desc": "Magic number"},
]


class FormatFindingsMarkdownTests(unittest.TestCase):
    """format_findings_markdown renders correct structure."""

    def test_no_findings_renders_clean_state(self):
        out = bot_review_demo.format_findings_markdown(SAMPLE_INFO, [])
        self.assertIn("✅ No issues found by static rules", out)
        self.assertIn("HD-1754: fix question selector", out)
        self.assertIn("`feature/hd-1754-fix`", out)

    def test_severity_counts_in_header(self):
        findings = SAMPLE_FINDINGS_HIGH + SAMPLE_FINDINGS_MED + SAMPLE_FINDINGS_LOW
        out = bot_review_demo.format_findings_markdown(SAMPLE_INFO, findings)
        # Header should show 1 HIGH, 1 MEDIUM, 1 LOW
        self.assertIn("🔴 1 HIGH", out)
        self.assertIn("🟡 1 MEDIUM", out)
        self.assertIn("🟢 1 LOW", out)

    def test_high_finding_renders_with_emoji(self):
        out = bot_review_demo.format_findings_markdown(SAMPLE_INFO, SAMPLE_FINDINGS_HIGH)
        self.assertIn("🔴 **[HIGH]**", out)
        self.assertIn("`hardcoded_url`", out)
        self.assertIn("L42", out)

    def test_finding_match_is_code_block(self):
        out = bot_review_demo.format_findings_markdown(SAMPLE_INFO, SAMPLE_FINDINGS_HIGH)
        self.assertIn("```", out)  # code block fence

    def test_url_in_title_is_link(self):
        out = bot_review_demo.format_findings_markdown(SAMPLE_INFO, [])
        self.assertIn("[HD-1754: fix question selector]", out)
        self.assertIn("(https://github.com/antoniooreany/test/pull/42)", out)


class FormatFullMarkdownReportTests(unittest.TestCase):
    """format_full_markdown_report aggregates multiple PRs."""

    def test_empty_report(self):
        out = bot_review_demo.format_full_markdown_report([])
        self.assertIn("# pr-review-bot Demo Report", out)
        self.assertIn("**0** PRs reviewed", out)

    def test_aggregates_severity_totals(self):
        results = [
            (SAMPLE_INFO, SAMPLE_FINDINGS_HIGH + SAMPLE_FINDINGS_MED),
            (SAMPLE_INFO, SAMPLE_FINDINGS_LOW),
        ]
        out = bot_review_demo.format_full_markdown_report(results)
        # 1 HIGH total (from first), 1 MEDIUM total, 1 LOW total (from second)
        self.assertIn("🔴 1", out)
        self.assertIn("🟡 1", out)
        self.assertIn("🟢 1", out)

    def test_per_pr_breakdown_section(self):
        results = [(SAMPLE_INFO, SAMPLE_FINDINGS_HIGH)]
        out = bot_review_demo.format_full_markdown_report(results)
        self.assertIn("Per-PR Breakdown", out)
        # Each PR has a section with its title
        self.assertIn("HD-1754: fix question selector", out)


class FormatFindingsTextTests(unittest.TestCase):
    """format_findings_text preserves the legacy terminal-friendly output."""

    def test_no_findings_returns_clean_state(self):
        out = bot_review_demo.format_findings_text(SAMPLE_INFO, [])
        self.assertIn("No issues found by static rules", out)

    def test_high_finding_uses_emoji(self):
        out = bot_review_demo.format_findings_text(SAMPLE_INFO, SAMPLE_FINDINGS_HIGH)
        self.assertIn("🔴", out)
        self.assertIn("[HIGH]", out)
        self.assertIn("Hardcoded URL", out)


class SlackMessageTests(unittest.TestCase):
    """slack_message produces valid Block Kit structure."""

    def test_block_kit_structure(self):
        msg = bot_review_demo.slack_message(SAMPLE_INFO, SAMPLE_FINDINGS_HIGH)
        self.assertIn("text", msg)
        self.assertIn("blocks", msg)
        self.assertEqual(msg["text"], "🤖 PR-Agent review on HD-1754: fix question selector")
        # First block = title link (text has nested type=text structure)
        title_block = msg["blocks"][0]
        self.assertEqual(title_block["type"], "section")
        title_text = title_block["text"]["text"]
        self.assertIn("HD-1754: fix question selector", title_text)
        # At least one finding block
        self.assertGreaterEqual(len(msg["blocks"])-1, 1)

    def test_severity_in_fields(self):
        msg = bot_review_demo.slack_message(SAMPLE_INFO, SAMPLE_FINDINGS_HIGH + SAMPLE_FINDINGS_LOW)
        # Find Severity field
        all_text = json.dumps(msg)
        self.assertIn("Severity", all_text)


class ApplyRulesTests(unittest.TestCase):
    """apply_rules finds expected patterns in diff text."""

    def test_finds_hardcoded_url(self):
        diff = "+ import org.example;\n+ String url = \"http://api.example.com/v1\";\n"
        findings = bot_review_demo.apply_rules(diff, SAMPLE_INFO)
        url_findings = [f for f in findings if f["id"] == "hardcoded_url"]
        self.assertGreaterEqual(len(url_findings), 1)
        self.assertEqual(url_findings[0]["severity"], "HIGH")

    def test_finds_wildcard_import(self):
        diff = "+ import java.util.*;\n+ import other.Class;\n"
        findings = bot_review_demo.apply_rules(diff, SAMPLE_INFO)
        wildcard = [f for f in findings if f["id"] == "wildcard_import"]
        self.assertEqual(len(wildcard), 1)
        self.assertEqual(wildcard[0]["severity"], "MEDIUM")

    def test_finds_system_out_in_java(self):
        diff = "+ class Foo {\n+   void m() { System.out.print(\"x\"); }\n+ }\n"
        findings = bot_review_demo.apply_rules(diff, SAMPLE_INFO)
        sout = [f for f in findings if f["id"] == "system_out_in_production"]
        self.assertEqual(len(sout), 1)
        self.assertEqual(sout[0]["severity"], "MEDIUM")

    def test_branch_naming_for_HD_NNN(self):
        info = {**SAMPLE_INFO, "headRefName": "HD-1234-fix-thing"}
        findings = bot_review_demo.apply_rules("", info)
        # No branch_naming finding for HD-NNN
        bn = [f for f in findings if f["id"] == "branch_naming"]
        self.assertEqual(len(bn), 0)

    def test_branch_naming_finding_for_non_HD(self):
        info = {**SAMPLE_INFO, "headRefName": "feature/random"}
        findings = bot_review_demo.apply_rules("", info)
        bn = [f for f in findings if f["id"] == "branch_naming"]
        self.assertEqual(len(bn), 1)
        self.assertEqual(bn[0]["severity"], "LOW")


if __name__ == "__main__":
    unittest.main(verbosity=2)
