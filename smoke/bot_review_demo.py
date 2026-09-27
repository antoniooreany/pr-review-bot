"""Apply pr-review-bot review rules to real GitHub PRs (no LLM needed).

For each of Anton's 5 open PRs, fetch the diff via `gh pr diff`, apply
the static rules from review_guidelines.md, and produce the same
output format the bot would post to Slack in production.

Usage:
    python smoke/bot_review_demo.py                          # all of Anton's open PRs
    python smoke/bot_review_demo.py --format markdown       # markdown summary
    python smoke/bot_review_demo.py --format markdown > review.md
    python smoke/bot_review_demo.py --target save-credentials-tool 1
    python smoke/bot_review_demo.py --quiet                  # only summary, no per-PR output
"""
import argparse
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
    ("long_method_heuristic", "LOW", None, "Method over 30 lines"),
    ("branch_naming", "LOW", None,
     "Branch name doesn't follow HD-NNN-short-desc convention"),
    ("missing_test_indicator", "MEDIUM", None,
     "Changed source file without test file in diff"),
]

SEVERITY_EMOJI = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def fetch_pr(repo, pr_number):
    info = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", f"antoniooreany/{repo}",
         "--json", "title,headRefName,additions,deletions,changedFiles,body,state"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(info.stdout)


def fetch_diff(repo, pr_number):
    return subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "--repo", f"antoniooreany/{repo}"],
        capture_output=True, text=True, check=True,
    ).stdout


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
    # Long method heuristic
    for m in re.finditer(r"\{[^{}]*\}", diff, re.DOTALL):
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
    if not re.match(r"^(feature/)?HD-\d+(-[a-z0-9-]+)?$", branch):
        findings.append({
            "id": "branch_naming",
            "severity": "LOW",
            "line": 0,
            "match": branch,
            "desc": f"Branch '{branch}' doesn't follow HD-NNN-short-desc convention",
        })
    return findings


def slack_message(info, findings):
    severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        severity_counts[f["severity"]] += 1
    summary = (
        f"🔴 {severity_counts['HIGH']}  "
        f"🟡 {severity_counts['MEDIUM']}  "
        f"🟢 {severity_counts['LOW']}"
    )
    blocks = [
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"*<{info.get('url', '')}|{info['title']}>*"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Author:*\n{info.get('author', {}).get('login', '?')}"},
            {"type": "mrkdwn", "text": f"*Branch:*\n{info.get('headRefName', '')}"},
            {"type": "mrkdwn", "text": f"*Severity:*\n{summary}"},
        ]},
    ]
    for f in sorted(findings, key=lambda x: SEVERITY_ORDER[x["severity"]])[:5]:
        emoji = SEVERITY_EMOJI[f["severity"]]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{emoji} *{f['severity']}* (L{f['line']}): {f['desc']}\n```\n{f['match']}\n```"},
        })
    return {"text": f"🤖 PR-Agent review on {info['title']}", "blocks": blocks}


def format_findings_text(info, findings):
    severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        severity_counts[f["severity"]] += 1
    title = info["title"]
    branch = info.get("headRefName", "")
    output = []
    output.append("")
    output.append("─" * 70)
    output.append(f"🤖 PR-Agent review on {title}")
    output.append(f"   Branch: {branch}  |  +{info['additions']}/-{info['deletions']} in {info['changedFiles']} files")
    output.append("─" * 70)
    if not findings:
        output.append("   ✅ No issues found by static rules")
    else:
        output.append(f"   🔴 {severity_counts['HIGH']}  🟡 {severity_counts['MEDIUM']}  🟢 {severity_counts['LOW']}")
        for f in sorted(findings, key=lambda x: SEVERITY_ORDER[x["severity"]]):
            emoji = SEVERITY_EMOJI[f["severity"]]
            output.append(f"   {emoji} [{f['severity']}] L{f['line']}: {f['desc']}")
            output.append(f"        `{f['match']}`")
    output.append(f"   (Would post to Slack as Block Kit message, {len(slack_message(info, findings)['blocks'])} blocks)")
    return "\n".join(output)


def format_findings_markdown(info, findings, all_findings_by_pr=None):
    """Render a single PR's findings as a Markdown section.

    If all_findings_by_pr is provided, render as a full multi-PR report.
    """
    title = info["title"]
    branch = info.get("headRefName", "")
    url = info.get("url", "")

    severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        severity_counts[f["severity"]] += 1

    md = []
    md.append(f"## 🔍 [{title}]({url})")
    md.append("")
    md.append(f"- **Branch:** `{branch}`")
    md.append(f"- **State:** {info.get('state', '?')}")
    md.append(f"- **Changes:** +{info['additions']}/-{info['deletions']} in {info['changedFiles']} files")
    md.append(f"- **Severity:** 🔴 {severity_counts['HIGH']} HIGH, 🟡 {severity_counts['MEDIUM']} MEDIUM, 🟢 {severity_counts['LOW']} LOW")
    md.append("")

    if not findings:
        md.append("✅ No issues found by static rules.")
    else:
        md.append("### Findings")
        md.append("")
        for f in sorted(findings, key=lambda x: SEVERITY_ORDER[x["severity"]]):
            emoji = SEVERITY_EMOJI[f["severity"]]
            md.append(f"- {emoji} **[{f['severity']}]** `{f['id']}` (L{f['line']}): {f['desc']}")
            md.append(f"    ```\n    {f['match']}\n    ```")
    md.append("")
    return "\n".join(md)


def format_full_markdown_report(pr_results):
    """Render the full multi-PR Markdown report.

    pr_results: list of (info, findings) tuples.
    """
    total_high = sum(1 for _, findings in pr_results for f in findings if f["severity"] == "HIGH")
    total_med = sum(1 for _, findings in pr_results for f in findings if f["severity"] == "MEDIUM")
    total_low = sum(1 for _, findings in pr_results for f in findings if f["severity"] == "LOW")

    md = []
    md.append("# pr-review-bot Demo Report")
    md.append("")
    md.append(f"Generated by [pr-review-bot](https://github.com/pr-review-bot/pr-review-bot)")
    md.append("")
    md.append("## Summary")
    md.append("")
    md.append(f"- **{len(pr_results)}** PRs reviewed")
    md.append(f"- **🔴 {total_high}** HIGH, **🟡 {total_med}** MEDIUM, **🟢 {total_low}** LOW findings")
    md.append("")
    md.append("## Per-PR Breakdown")
    md.append("")
    for info, findings in pr_results:
        md.append(format_findings_markdown(info, findings))
    return "\n".join(md)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["text", "markdown"], default="text")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-PR details, show only summary")
    parser.add_argument("--target", nargs=2, metavar=("REPO", "PR"),
                        help="Review single PR (REPO PR_NUMBER)")
    args = parser.parse_args()

    prs = [("save-credentials-tool", 1),
           ("google-cloud-task", 1),
           ("winwin-backend-test-task", 48),
           ("oracle-capacity-hunter-claude", 20),
           ("oracle-capacity-hunter-claude", 19)]

    if args.target:
        prs = [(args.target[0], int(args.target[1]))]

    pr_results = []
    for repo, n in prs:
        try:
            info = fetch_pr(repo, n)
            diff = fetch_diff(repo, n)
            findings = apply_rules(diff, info)
            pr_results.append((info, findings))

            if not args.quiet and args.format == "text":
                print(format_findings_text(info, findings))
        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] Failed to fetch {repo}#{n}: {e}")
        except Exception as e:
            print(f"\n[ERROR] Unexpected: {e}")

    if args.format == "markdown":
        print(format_full_markdown_report(pr_results))
    else:
        if not args.quiet:
            print("\n" + "=" * 70)
            print("Note: this is STATIC analysis only (10 rules). Production bot also runs")
            print("LLM-based review via PR-Agent + MiniMax, catching semantic issues")
            print("(logic bugs, design patterns, security flaws) that regex can't see.")
            print("")
            print("IMPORTANT: the bot REVIEWS only. It does NOT auto-apply patches,")
            print("auto-merge MRs, or push code. The author/reviewer owns the fix.")


if __name__ == "__main__":
    main()
