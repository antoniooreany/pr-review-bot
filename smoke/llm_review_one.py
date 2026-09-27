"""Single-PR LLM review via MiniMax. Usage: python llm_review_one.py <repo> <pr_number>"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# Resolve single PR from args
if len(sys.argv) != 3:
    print("Usage: python llm_review_one.py <repo> <pr_number>")
    print("Example: python llm_review_one.py winwin-backend-test-task 48")
    sys.exit(1)

REPO = sys.argv[1]
PR_NUMBER = int(sys.argv[2])

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "slack-notifier"))
import notifier
api_key = notifier.get_secret("OPENAI_KEY")
if not api_key:
    print("ERROR: OPENAI_KEY not found in env or CredMan")
    sys.exit(1)
print(f"OK: API key loaded (length={len(api_key)})")

base_url = os.environ.get("OPENAI_BASE_URL", "https://api.minimax.chat/v1")

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai not installed")
    sys.exit(1)

client = OpenAI(api_key=api_key, base_url=base_url)


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

    print("Asking MiniMax...")
    try:
        resp = client.chat.completions.create(
            model="MiniMax-M3",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Review this PR diff:\n\n```\n{diff[:10000]}\n```"},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        content = resp.choices[0].message.content
        print(content)
        print(f"\n{'─' * 70}")
        print(f"Tokens used: {resp.usage.total_tokens} (model: {resp.model})")
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
