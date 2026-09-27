"""Single-PR LLM review via MiniMax. Usage: python llm_review_one.py <repo> <pr_number>"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# REPO and PR_NUMBER are set by main() at runtime (not at import —
# otherwise the module-level `int(sys.argv[2])` breaks test discovery).
REPO = ""
PR_NUMBER = 0
HEAD_SHA = ""  # cached after first fetch_head_sha() call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "slack-notifier"))
# Prefer env OPENAI_API_KEY (real OpenAI key in Anton's env), fall back to CredMan
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    import notifier
    api_key = notifier.get_secret("OPENAI_API_KEY")
if not api_key:
    print("ERROR: OPENAI_API_KEY not found in env or CredMan")
    sys.exit(1)
print(f"OK: API key loaded (length={len(api_key)}, source={'env' if os.environ.get('OPENAI_API_KEY') else 'CredMan'})")

# Use the configured base URL + auto-detect model.
base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai not installed")
    sys.exit(1)

client = OpenAI(api_key=api_key, base_url=base_url)


def pick_model():
    """Auto-detect an available model from the API."""
    env_override = os.environ.get("OPENAI_MODEL")
    if env_override:
        return env_override
    try:
        models = client.models.list()
        # Prefer gpt-5/4o family, then 3.5
        preferred = ["gpt-5.4-mini", "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
        for target in preferred:
            for m in models.data:
                if m.id == target or target in m.id:
                    return m.id
        return models.data[0].id if models.data else "gpt-3.5-turbo"
    except Exception as e:
        print(f"WARN: could not list models ({e}), falling back to gpt-3.5-turbo")
        return "gpt-3.5-turbo"


model = pick_model()
print(f"Using model: {model}")


def uses_new_param_style(model_name):
    """gpt-5+ and o1+ use max_completion_tokens, not max_tokens."""
    name = model_name.lower()
    return "gpt-5" in name or name.startswith("o1") or name.startswith("o3") or name.startswith("o4")


def fetch_diff():
    return subprocess.run(
        ["gh", "pr", "diff", str(PR_NUMBER), "--repo", f"antoniooreany/{REPO}"],
        capture_output=True, text=True, check=True,
    ).stdout


def fetch_info():
    return json.loads(subprocess.run(
        ["gh", "pr", "view", str(PR_NUMBER), "--repo", f"antoniooreany/{REPO}",
         "--json", "title,headRefName,additions,deletions,changedFiles,body"],
        capture_output=True, text=True, check=True,
    ).stdout)


SYSTEM = """You are a senior code reviewer for a Spring Boot / Java backend team.
Focus on:
- Bugs and logic errors
- Security: hardcoded credentials, SQL injection, XSS, missing auth
- Test coverage gaps
- Code style: HD-NNN branch naming, no category prefixes (feature/, fix/),
  explicit imports (no jakarta.persistence.*)
- Performance: N+1 queries, unnecessary work

Output format (terse):

## Summary
[1-2 sentences]

## Findings
- 🔴 HIGH [line N or file]: [issue + suggested fix]
- 🟡 MEDIUM [line N or file]: ...
- 🟢 LOW [line N or file]: ...

If clean, say "No significant issues found." """


def main():
    info = fetch_info()
    diff = fetch_diff()
    title = info["title"]
    branch = info["headRefName"]
    files = info["changedFiles"]
    adds = info["additions"]
    dels = info["deletions"]

    print(f"\n{'=' * 70}")
    print(f"🤖 LLM Review (MiniMax-M3) — {REPO}#{PR_NUMBER}")
    print(f"   Title: {title}")
    print(f"   Branch: {branch}  |  +{adds}/-{dels} in {files} files")
    print(f"{'=' * 70}\n")

    print("Asking LLM...")
    try:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Review this PR diff:\n\n```\n{diff[:10000]}\n```"},
            ],
            "temperature": 0.2,
        }
        # Newer models (gpt-5+, o1+) use max_completion_tokens; older use max_tokens.
        if uses_new_param_style(model):
            kwargs["max_completion_tokens"] = 2000
        else:
            kwargs["max_tokens"] = 2000
        resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content
        print(content)
        print(f"\n{'─' * 70}")
        print(f"Tokens used: {resp.usage.total_tokens} (model: {resp.model})")
    except Exception as e:
        print(f"ERROR: {e}")
        return

    # Post to GitHub PR as a comment so it shows in the web UI.
    post_to_pr(content)

    # T36: determine verdict and post GitHub Check Run + PR Review.
    # This is what makes the bot block merges via branch protection.
    try:
        event, conclusion, summary = determine_verdict(content)
        print(f"\n📊 Verdict: {event} (check conclusion: {conclusion})")
        print(f"   {summary}")

        global HEAD_SHA
        HEAD_SHA = fetch_head_sha()
        post_check_run(conclusion, summary)
        post_review(event, f"🤖 **pr-review-bot verdict: {event}**\n\n{summary}\n\nSee PR comment for full review.")
    except Exception as e:
        print(f"   WARN: verdict posting failed: {e}")


def post_to_pr(review_text):
    """Post review as a PR comment via gh CLI."""
    body = (
        "🤖 **PR-Agent review (via LLM)**\n\n"
        f"{review_text}\n\n"
        "<sub>Posted by [pr-review-bot](https://github.com/pr-review-bot/pr-review-bot)</sub>\n"
    )
    result = subprocess.run(
        ["gh", "pr", "comment", str(PR_NUMBER),
         "--repo", f"antoniooreany/{REPO}",
         "--body", body],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"\n✅ Posted review as PR comment to {REPO}#{PR_NUMBER}")
        print(f"   View at: https://github.com/antoniooreany/{REPO}/pull/{PR_NUMBER}")
    else:
        print(f"\n⚠️ Failed to post comment: {result.stderr}")


def determine_verdict(text):
    """Classify LLM review text and return (event, conclusion, summary).

    Severity markers: '🔴 HIGH', '🟡 MEDIUM', '🟢 LOW'.

    Returns:
        event       — 'APPROVE' | 'COMMENT' | 'REQUEST_CHANGES' (GitHub PR review)
        conclusion  — 'success' | 'neutral' | 'failure' (GitHub check run)
        summary     — short description for the status check
    """
    high = text.count("🔴 HIGH")
    medium = text.count("🟡 MEDIUM")
    low = text.count("🟢 LOW")

    if high > 0:
        return ("REQUEST_CHANGES", "failure",
                f"🔴 {high} HIGH issue(s) — merge blocked until fixed")
    if medium > 0:
        return ("COMMENT", "neutral",
                f"🟡 {medium} MEDIUM issue(s) — review needed")
    return ("APPROVE", "success",
            f"✅ Only {low} LOW issue(s) — safe to merge")


def fetch_head_sha():
    """Get the head SHA of the current PR (for status check)."""
    result = subprocess.run(
        ["gh", "pr", "view", str(PR_NUMBER),
         "--repo", f"antoniooreany/{REPO}",
         "--json", "headRefOid"],
        capture_output=True, text=True, check=True,
    )
    import json as _json
    return _json.loads(result.stdout)["headRefOid"]


def post_check_run(conclusion, summary):
    """Post a status check on the PR's head SHA. Visible as a badge in PR UI
    and can block merge via branch protection rule on this context name.

    Uses GitHub Status API (legacy) — works with PAT, no GitHub App required.
    conclusion: 'success' | 'neutral' | 'failure' (GitHub Status API state)
    summary:     short description for the status badge
    """
    sha = HEAD_SHA or fetch_head_sha()
    # Map verdict conclusion → Status API state (same names)
    result = subprocess.run(
        ["gh", "api", f"repos/antoniooreany/{REPO}/statuses/{sha}",
         "-X", "POST",
         "-f", f"state={conclusion}",
         "-f", "context=pr-review-bot/pr-review",
         "-f", f"description={summary}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"   ✅ Status: state={conclusion}")
    else:
        print(f"   ⚠️ Status failed: {result.stderr[:200]}")


def post_review(event, body):
    """Submit a PR review with given event (APPROVE/COMMENT/REQUEST_CHANGES)."""
    result = subprocess.run(
        ["gh", "api", f"repos/antoniooreany/{REPO}/pulls/{PR_NUMBER}/reviews",
         "-X", "POST",
         "-f", f"event={event}",
         "-f", f"body={body}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"   ✅ PR review: {event}")
    else:
        print(f"   ⚠️ PR review failed: {result.stderr[:200]}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python llm_review_one.py <repo> <pr_number>")
        print("Example: python llm_review_one.py winwin-backend-test-task 48")
        sys.exit(1)
    REPO = sys.argv[1]
    PR_NUMBER = int(sys.argv[2])
    main()
