"""One-shot demo script for showing pr-review-bot to Vitalii.

Generates a Markdown report of bot reviews on Anton's open PRs
and (optionally) posts to Slack if a webhook is configured.

Usage:
    # Markdown only (safe — no external calls beyond read-only GitHub)
    python smoke/vitalii_demo.py --output vitalii-demo.md

    # With Slack notification (requires SLACK_WEBHOOK_URL env)
    SLACK_WEBHOOK_URL=https://hooks.slack.com/... python smoke/vitalii_demo.py

    # Single PR only
    python smoke/vitalii_demo.py --target save-credentials-tool 1
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "slack-notifier"))

import bot_review_demo


# These are the MRs/PRs that demonstrate the bot's value.
# Source of truth: Anton's open PRs at https://github.com/antoniooreany
DEFAULT_PRS = [
    ("save-credentials-tool", 1),                # dependabot bump
    ("google-cloud-task", 1),                    # security encryption
    ("winwin-backend-test-task", 48),            # CI + tests
    ("oracle-capacity-hunter-claude", 20),       # test automation
    ("oracle-capacity-hunter-claude", 19),       # commitlint
]


def fetch_pr_safe(repo, pr_number):
    """Fetch PR info, gracefully skip on failure."""
    try:
        return bot_review_demo.fetch_pr(repo, pr_number)
    except Exception as e:
        print(f"  SKIP {repo}#{pr_number}: {e}", file=sys.stderr)
        return None


def fetch_diff_safe(repo, pr_number):
    try:
        return bot_review_demo.fetch_diff(repo, pr_number)
    except Exception:
        return ""


def post_to_slack(webhook_url, markdown_text, pr_count, severity_counts):
    """Optional Slack notification with a summary."""
    import urllib.request
    text = (
        f"📊 *pr-review-bot demo for Vitalii*\n\n"
        f"Reviewed *{pr_count}* open PRs.\n"
        f"🔴 {severity_counts['HIGH']} HIGH · "
        f"🟡 {severity_counts['MEDIUM']} MEDIUM · "
        f"🟢 {severity_counts['LOW']} LOW\n\n"
        f"Full report attached."
    )
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except Exception as e:
        return f"err: {e}"


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description="pr-review-bot demo for Vitalii")
    parser.add_argument("--target", nargs=2, metavar=("REPO", "PR"),
                        help="Demo on single PR instead of all open")
    parser.add_argument("--output", "-o", default=None,
                        help="Write Markdown report to file")
    parser.add_argument("--slack-webhook", default=None,
                        help="Slack webhook URL (or set $SLACK_WEBHOOK_URL)")
    parser.add_argument("--no-github", action="store_true",
                        help="Skip GitHub fetch (use cached data)")
    args = parser.parse_args()

    prs = [(args.target[0], int(args.target[1]))] if args.target else DEFAULT_PRS

    results = []
    print(f"\n🤖 pr-review-bot demo for Vitalii")
    print(f"   Reviewing {len(prs)} PR(s)...\n")

    for repo, n in prs:
        print(f"  → {repo}#{n}...", end=" ", flush=True)
        info = fetch_pr_safe(repo, n)
        if info is None:
            continue
        diff = fetch_diff_safe(repo, n)
        findings = bot_review_demo.apply_rules(diff, info)
        results.append((info, findings))
        sev = {k: sum(1 for f in findings if f["severity"] == k) for k in ["HIGH", "MEDIUM", "LOW"]}
        print(f"🔴 {sev['HIGH']} 🟡 {sev['MEDIUM']} 🟢 {sev['LOW']}")

    if not results:
        print("\nNo PRs reviewed successfully. Check gh CLI auth.")
        sys.exit(1)

    # Build report
    report = bot_review_demo.format_full_markdown_report(results)

    # Add header for Vitalii
    intro = (
        "# pr-review-bot Demo for Vitalii\n\n"
        "Reviewer: **pr-review-bot** (v1.5.5)\n"
        "Date: 2026-09-28\n"
        "Scope: static analysis only (10 regex rules from `review_guidelines.md`).\n"
        "Production also runs LLM-based review via PR-Agent + MiniMax for\n"
        "semantic issues (logic bugs, security flaws, design patterns).\n\n"
        "---\n\n"
    )
    full = intro + report

    # Output
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(full)
        print(f"\n✅ Report written to {args.output}")
    else:
        print("\n" + "=" * 60)
        print(full)

    # Optional Slack
    webhook = args.slack_webhook or os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        sev_totals = {k: sum(1 for _, findings in results for f in findings if f["severity"] == k)
                      for k in ["HIGH", "MEDIUM", "LOW"]}
        status = post_to_slack(webhook, full, len(results), sev_totals)
        print(f"   Slack post: {status}")

    print(f"\n📊 Total: {len(results)} PRs reviewed")
