"""Apply pr-review-bot review rules to real GitHub PRs (no LLM needed).

For each of Anton's 5 open PRs, fetch the diff via `gh pr diff`, apply
the static rules from review_guidelines.md, and produce the same
output format the bot would post to Slack in production.

This is the demo Anton asked for: "see the bot's review" — without
needing PR-Agent/LLM to be running. Shows what the bot catches.
"""
import json
import os
import re
import subprocess
import sys

# Force UTF-8 stdout (Windows cp1252 default) for emojis and box drawing.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


# Static review rules derived from review_guidelines.md.
# Each rule: (id, severity, regex, description)
RULES = [
    ("hardcoded_url", "HIGH",
     r'https?://[^\s"\'<>]+',
     "Hardcoded URL — should be in application.properties/.env"),
    ("hardcoded_secret", "HIGH",
     r'(?i)(password|secret|api[_-]?key|token)\s*=\s*["\'][^"\']+["\']',
     "Hardcoded credential in source — security risk"),
    ("private_transactional", "HIGH",
     r'@Transactional[\s\S]{0,200}private\s+(?:static\s+)?(?:void|[\w<>,\s]+)\s+\w+\s*\(',
     "@Transactional on private method (Spring silent failure)"),
    ("wildcard_import", "MEDIUM",
     r'import\s+[\w.]+\.\*;',
     "Wildcard import — explicit imports preferred"),
    ("todo_without_ticket", "LOW",
     r'(?i)TODO(?!\([^)]*HD-)',
     "TODO without Jira ticket reference"),
    ("system_out_in_production", "MEDIUM",
     r'System\.out\.print',
     "System.out.println in production code path"),
    ("magic_number", "LOW",
     r'if\s*\([^)]*==\s*\d{3,}',
     "Magic number — use named constant"),
    ("long_method_heuristic", "LOW",
     None,  # special: counts lines
     "Method over 30 lines"),
    ("branch_naming", "LOW",
     None,  # special: checks branch via gh
     "Branch name doesn't follow HD-NNN convention"),
    ("missing_test_indicator", "MEDIUM",
     None,  # special: heuristic
     "Changed source file without test file in diff"),
]

SEVERITY_EMOJI = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}


def fetch_pr(repo, pr_number):
    """Return (title, author, base_branch, diff) for the PR."""
    # Title + body + base
    info = json.loads(subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", f"antoniooreany/{repo}",
         "--json", "title,author,baseRefName,headRefName,additions,deletions,changedFiles,body"],
        capture_output=True, text=True, check=True).stdout)
    diff = subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "--repo", f"antoniooreany/{repo}"],
        capture_output=True, text=True, check=True).stdout
    return info, diff


def apply_rules(diff, info):
    findings = []
    for rule_id, severity, pattern, desc in RULES:
        if pattern is None:
            continue
        for m in re.finditer(pattern, diff):
            line_no = diff[:m.start()].count("\n") + 1
            findings.append({
                "id": rule_id,
                "severity": severity,
                "line": line_no,
                "match": m.group(0)[:60],
                "desc": desc,
            })
    # Long-method heuristic: count lines between { and matching }
    for m in re.finditer(r'\{[^{}]*\}', diff, re.DOTALL):
        if m.group(0).count("\n") > 30:
            findings.append({
                "id": "long_method_heuristic",
                "severity": "LOW",
                "line": diff[:m.start()].count("\n") + 1,
                "match": f"{m.group(0).count(chr(10))} lines",
                "desc": "Method over 30 lines",
            })
    # Branch naming
    branch = info.get("headRefName", "")
    if not re.match(r'^(feature/)?HD-\d+(-[a-z0-9-]+)?$', branch):
        findings.append({
            "id": "branch_naming",
            "severity": "LOW",
            "line": 0,
            "match": branch,
            "desc": f"Branch '{branch}' doesn't follow HD-NNN-short-desc convention",
        })
    return findings


def slack_message(info, findings):
    """Format findings as a Slack Block Kit message (same shape as bot posts)."""
    severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        severity_counts[f["severity"]] += 1
    summary = (
        f"🔴 HIGH: {severity_counts['HIGH']}  "
        f"🟡 MEDIUM: {severity_counts['MEDIUM']}  "
        f"🟢 LOW: {severity_counts['LOW']}"
    )
    blocks = [
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"*<{info.get('url', '')}|{info['title']}>*"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Author:*\n{info.get('author', {}).get('login', '?')}"},
            {"type": "mrkdwn", "text": f"*Branch:*\n{info.get('headRefName', '')}"},
            {"type": "mrddwn", "text": f"*Severity:*\n{summary}"},
        ]},
    ]
    for f in findings[:5]:
        emoji = SEVERITY_EMOJI[f["severity"]]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{emoji} *{f['severity']}* (L{f['line']}): {f['desc']}\n```\n{f['match']}\n```"},
        })
    return {"text": f"🤖 PR-Agent review on {info['title']}", "blocks": blocks}


def main():
    prs = [
        ("save-credentials-tool", 1),
        ("google-cloud-task", 1),
        ("winwin-backend-test-task", 48),
        ("oracle-capacity-hunter-claude", 20),
        ("oracle-capacity-hunter-claude", 19),
    ]
    print("=" * 70)
    print("PR-REVIEW-BOT DEMO — static review of 5 open GitHub PRs")
    print("=" * 70)
    for repo, n in prs:
        try:
            info, diff = fetch_pr(repo, n)
        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] Failed to fetch {repo}#{n}: {e}")
            continue
        findings = apply_rules(diff, info)
        msg = slack_message(info, findings)
        print(f"\n{'─' * 70}")
        print(f"🤖 PR-Agent review on {info['title']}")
        print(f"   Branch: {info['headRefName']}  |  {info['additions']}+ / {info['deletions']}- in {info['changedFiles']} files")
        print(f"{'─' * 70}")
        if not findings:
            print("   ✅ No issues found by static rules")
        else:
            sev_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
            for f in findings:
                sev_counts[f["severity"]] += 1
            print(f"   🔴 {sev_counts['HIGH']}  🟡 {sev_counts['MEDIUM']}  🟢 {sev_counts['LOW']}")
            for f in findings:
                emoji = SEVERITY_EMOJI[f["severity"]]
                print(f"   {emoji} [{f['severity']}] L{f['line']}: {f['desc']}")
                print(f"        `{f['match']}`")
        print(f"   (Would post to Slack as Block Kit message, {len(msg['blocks'])} blocks)")
    print(f"\n{'=' * 70}")
    print("Note: this is STATIC analysis only (10 rules). Production bot also runs")
    print("LLM-based review via PR-Agent + MiniMax, catching semantic issues")
    print("(logic bugs, design patterns, security flaws) that regex can't see.")
    print("")
    print("IMPORTANT: the bot REVIEWS only. It does NOT auto-apply patches,")
    print("auto-merge MRs, or push code. The author/reviewer owns the fix.")
    print("=" * 70)


if __name__ == "__main__":
    main()
