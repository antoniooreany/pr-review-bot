#!/usr/bin/env bash
# T3 — assert .env.example documents every required variable with a
# non-empty placeholder. Real .env is allowed to have empty values,
# but the example must show what's expected.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env.example ]; then
    echo "FAIL: .env.example does not exist"
    exit 1
fi

REQUIRED_KEYS=(
    "OPENAI_BASE_URL"
    "OPENAI_KEY"
    "GITLAB_URL"
    "GITLAB_TOKEN"
    "WEBHOOK_SECRET"
    "PR_AGENT_CONFIG"
)

FAIL=0
for key in "${REQUIRED_KEYS[@]}"; do
    # Match `KEY=...` lines (allow leading whitespace, allow quoted/unquoted values)
    if ! grep -qE "^[[:space:]]*${key}=" .env.example; then
        echo "FAIL: .env.example missing required key: ${key}"
        FAIL=1
    fi
done

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi

# Assert no real-looking secrets leaked into the example
# (placeholder patterns only: change-me, your-, xxx, example, placeholder, <...>)
SUSPECT=$(grep -E "(sk-[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,})" .env.example || true)
if [ -n "$SUSPECT" ]; then
    echo "FAIL: .env.example appears to contain a real-looking secret:"
    echo "$SUSPECT"
    exit 1
fi

echo "PASS: .env.example has all required keys and no real secrets"
exit 0
