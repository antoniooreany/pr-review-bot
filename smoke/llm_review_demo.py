"""LLM-based PR review using MiniMax API directly.

Avoids the pr-agent pip install issue by calling the API ourselves.
Uses the OpenAI-compatible client (since MiniMax exposes OpenAI-format).

Setup:
    pip install openai  # already installed in Anton's env (despite conflict)

Run:
    OPENAI_KEY=$(python -c "import sys; sys.path.insert(0,'slack-notifier'); from notifier import get_secret; print(get_secret('OPENAI_KEY'))") \\
    python smoke/llm_review_demo.py
"""
import json
import os
import subprocess
import sys

# Force UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

# Get API key from CredMan via bot's own get_secret
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "slack-notifier"))
import notifier
api_key = notifier.get_secret("OPENAI_KEY")
if not api_key:
    print("ERROR: OPENAI_KEY not found in env or CredMan (target pr-review-bot:OPENAI_KEY)")
    sys.exit(1)
print(f"OK: API key loaded from CredMan (length={len(api_key)})")

# Get base URL — same fallback as bot config
base_url = os.environ.get("OPENAI_BASE_URL", "https://api.minimax.io/anthropic")

# OpenAI client (MiniMax is OpenAI-compatible)
try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai package not installed. Run: pip install openai")
    sys.exit(1)

client = OpenAI(api_key=api_key, base_url=base_url)


def fetch_pr_diff(repo, pr_number):
    """Get PR diff via gh CLI."""
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "--repo", f"antoniooreany/{repo}"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def fetch_pr_info(repo, pr_number):
    """Get PR title and metadata."""
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", f"antoniooreany/{repo}",
         "--json", "title,headRefName,additions,deletions,changedFiles"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


SYSTEM_PROMPT = """You are a code review assistant for a senior backend team.
Focus on:
- Bugs and logic errors
- Security issues (hardcoded secrets, SQLi, XSS, missing auth checks)
- Missing tests
- Code style violations (hotels-data conventions: HD-NNN branch naming,
  no category prefixes like feature/, explicit imports)
- Performance issues (N+1 queries, unnecessary work)

Output format (be brief):
## Summary
[1-2 sentences]

## Findings
- 🔴 HIGH [line N]: [issue + suggestion]
- 🟡 MEDIUM [line N]: ...
- 🟢 LOW [line N]: ...

If no issues, say "No significant issues found."
"""


def review_pr(repo, pr_number):
    """Call LLM to review a PR."""
    info = fetch_pr_info(repo, pr_number)
    diff = fetch_pr_diff(repo, pr_number)
    title = info["title"]
    branch = info["headRefName"]
    print(f"\n{'=' * 70}")
    print(f"PR-Agent review on {title}")
    print(f"  Branch: {branch}  |  {info['additions']}+ / {info['deletions']}- in {info['changedFiles']} files")
    print(f"{'=' * 70}")
    print("Asking MiniMax...")

    try:
        resp = client.chat.completions.create(
            model="MiniMax-M3[1m]",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Review this PR diff:\n\n```\n{diff[:8000]}\n```"},
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        content = resp.choices[0].message.content
        print(content)
    except Exception as e:
        print(f"ERROR: API call failed: {e}")


def main():
    prs = [
        ("save-credentials-tool", 1),
        ("google-cloud-task", 1),
        ("winwin-backend-test-task", 48),
        ("oracle-capacity-hunter-claude", 20),
        ("oracle-capacity-hunter-claude", 19),
    ]
    for repo, n in prs:
        try:
            review_pr(repo, n)
        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] Failed to fetch {repo}#{n}: {e}")
        except Exception as e:
            print(f"\n[ERROR] Unexpected: {e}")


if __name__ == "__main__":
    main()
