"""GitLab MR LLM review — equivalent of llm_review_one.py for GitLab.

For each MR of HD-1754 (or any project), runs LLM analysis, posts review
as MR note, sets status check on the head SHA, adds verdict label.

Usage:
    python smoke/llm_review_gitlab.py <project_id> <mr_iid>
    python smoke/llm_review_gitlab.py <project_id> all     # all open MRs
    python smoke/llm_review_gitlab.py <project_id> HD-1754  # by title search

API: https://gitlab.winwin.travel/api/v4
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "slack-notifier"))
from notifier import get_secret

GITLAB_URL = "https://gitlab.winwin.travel"
GITLAB_TOKEN = get_secret("GITLAB_TOKEN") or os.environ.get("GITLAB_TOKEN")
if not GITLAB_TOKEN:
    print("ERROR: GITLAB_TOKEN not found")
    sys.exit(1)
print(f"OK: GitLab token loaded (length={len(GITLAB_TOKEN)})")


def api(method, path, body=None, params=None):
    """GitLab REST API helper. Returns parsed JSON or text."""
    url = f"{GITLAB_URL}/api/v4{path}"
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("PRIVATE-TOKEN", GITLAB_TOKEN)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body_text = resp.read().decode("utf-8")
            if not body_text:
                return None
            try:
                return json.loads(body_text)
            except json.JSONDecodeError:
                return body_text
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8") if e.fp else ""
        print(f"  HTTP {e.code} {method} {path}: {body_text[:200]}")
        raise


def fetch_mr(project_id, mr_iid):
    mr = api("GET", f"/projects/{project_id}/merge_requests/{mr_iid}")
    return mr


def fetch_diff(project_id, mr_iid):
    """Return diff text. MR diffs can be huge — cap at 20k chars."""
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/changes"
    req = urllib.request.Request(url)
    req.add_header("PRIVATE-TOKEN", GITLAB_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} fetching diff: {e.read().decode()[:200]}")
        return ""
    parts = []
    for change in data.get("changes", []):
        old = change.get("old_path", "")
        new = change.get("new_path", "")
        parts.append(f"--- {old} -> {new} ---")
        parts.append(change.get("diff", "")[:5000])
    return "\n".join(parts)[:20000]


def post_note(project_id, mr_iid, body):
    """Post MR note (comment)."""
    return api("POST", f"/projects/{project_id}/merge_requests/{mr_iid}/notes",
               body={"body": body})


def post_status(project_id, sha, state, description):
    """Set commit status on the head SHA."""
    return api("POST", f"/projects/{project_id}/statuses/{sha}",
               body={"state": state, "ref": "main", "name": "pr-review-bot/pr-review",
                     "description": description[:140]})


def add_label(project_id, mr_iid, label):
    """Add a label to the MR (creates label if not exists)."""
    try:
        api("POST", f"/projects/{project_id}/labels",
            body={"name": label, "color": "#FF0000"})
    except urllib.error.HTTPError:
        pass  # Already exists
    return api("PUT", f"/projects/{project_id}/merge_requests/{mr_iid}",
               body={"add_labels": label})


# ── LLM part (OpenAI-compatible, same as GitHub demo) ────────────────────

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or get_secret("OPENAI_API_KEY")
base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai not installed")
    sys.exit(1)

client = OpenAI(api_key=OPENAI_API_KEY, base_url=base_url)


def detect_model():
    if os.environ.get("OPENAI_MODEL"):
        return os.environ["OPENAI_MODEL"]
    try:
        models = client.models.list()
        for target in ["gpt-5.4-mini", "gpt-4o-mini", "gpt-3.5-turbo"]:
            for m in models.data:
                if m.id == target or target in m.id:
                    return m.id
        return models.data[0].id
    except Exception as e:
        return "gpt-3.5-turbo"


def uses_new_param_style(name):
    n = name.lower()
    return "gpt-5" in n or n.startswith(("o1", "o3", "o4"))


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


def determine_verdict(text):
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


def review_mr(project_id, mr_iid):
    mr = fetch_mr(project_id, mr_iid)
    title = mr["title"]
    branch = mr["source_branch"]
    state = mr["state"]
    sha = mr["sha"]
    web_url = mr["web_url"]

    print(f"\n{'=' * 70}")
    print(f"🤖 GitLab MR Review — project {project_id} !{mr_iid}")
    print(f"   Title: {title}")
    print(f"   Branch: {branch} → {mr['target_branch']}  |  State: {state}")
    print(f"   SHA: {sha[:10]}...")
    print(f"{'=' * 70}\n")

    if state == "merged":
        print("  SKIP: MR already merged")
        return

    diff = fetch_diff(project_id, mr_iid)
    if not diff:
        print("  WARN: empty diff, skipping")
        return

    print("Asking LLM...")
    model = detect_model()
    print(f"Using model: {model}")
    try:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Review this MR diff:\n\n```\n{diff}\n```"},
            ],
            "temperature": 0.2,
        }
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

    # Post as MR note
    body = (
        "🤖 **pr-review-bot LLM review**\n\n"
        f"{content}\n\n"
        "<sub>Posted by [pr-review-bot](https://github.com/pr-review-bot/pr-review-bot)</sub>\n"
    )
    try:
        post_note(project_id, mr_iid, body)
        print(f"\n✅ Posted note to !{mr_iid}")
    except Exception as e:
        print(f"\n⚠️ Failed to post note: {e}")

    # Verdict + status check + label
    event, conclusion, summary = determine_verdict(content)
    print(f"\n📊 Verdict: {event} (status: {conclusion})")
    print(f"   {summary}")

    try:
        post_status(project_id, sha, conclusion, summary)
        print(f"   ✅ Status: {conclusion}")
    except Exception as e:
        print(f"   ⚠️ Status failed: {e}")

    label = f"pr-review-bot:{ 'fail' if conclusion == 'failure' else 'pass' if conclusion == 'success' else 'review' }"
    try:
        add_label(project_id, mr_iid, label)
        print(f"   ✅ Label: {label}")
    except Exception as e:
        print(f"   ⚠️ Label failed: {e}")


def find_open_mrs(project_id, search_term=None):
    """Find open MRs in project, optionally filtered by title."""
    params = {"state": "opened", "per_page": 100}
    if search_term:
        params["search"] = search_term
    mrs = api("GET", f"/projects/{project_id}/merge_requests", params=params)
    return mrs


def main():
    if len(sys.argv) < 3:
        print("Usage: python llm_review_gitlab.py <project_id> <mr_iid|all|search_term>")
        sys.exit(1)
    project_id = sys.argv[1]
    target = sys.argv[2]

    if target == "all":
        mrs = find_open_mrs(project_id)
    elif target.isdigit():
        review_mr(project_id, int(target))
        return
    else:
        mrs = find_open_mrs(project_id, search_term=target)

    print(f"Found {len(mrs)} open MR(s)")
    for mr in mrs:
        review_mr(project_id, mr["iid"])


if __name__ == "__main__":
    main()
