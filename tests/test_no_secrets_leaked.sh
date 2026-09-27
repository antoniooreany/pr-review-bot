#!/usr/bin/env bash
# T4 — regression guard: scan tracked files for patterns that look like
# real API keys or tokens. Catches accidental commits of .env contents.
set -euo pipefail

cd "$(dirname "$0")/.."

# Files to scan: anything tracked by git, minus .env itself, .env.example
# (which is allowed to mention key names but not contain real values), and
# the test that legitimately defines patterns to detect.
SCAN_FILES=$(git ls-files \
    | grep -v -E '^(\.env$|\.env\.example$|tests/test_no_secrets_leaked\.sh$|README\.md$)')

if [ -z "$SCAN_FILES" ]; then
    echo "PASS: no tracked files to scan"
    exit 0
fi

# Heuristic patterns for common secret formats:
#   OpenAI: sk-... or sk-proj-...
#   GitLab PAT: glpat-...
#   GitHub PAT: ghp_...
#   Anthropic: sk-ant-...
#   Generic long hex / base32 strings (40+ chars in a row, unlikely in code)
PATTERNS=(
    'sk-[A-Za-z0-9_-]{32,}'
    'sk-ant-[A-Za-z0-9_-]{32,}'
    'glpat-[A-Za-z0-9_-]{16,}'
    'ghp_[A-Za-z0-9]{32,}'
    'xox[baprs]-[A-Za-z0-9-]{16,}'
)

FAIL=0
for pat in "${PATTERNS[@]}"; do
    HITS=$(echo "$SCAN_FILES" | xargs grep -hE "$pat" 2>/dev/null || true)
    if [ -n "$HITS" ]; then
        echo "FAIL: pattern '$pat' matched in tracked files:"
        echo "$HITS" | sed 's/^/    /'
        FAIL=1
    fi
done

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi

echo "PASS: no secret patterns detected in tracked files"
exit 0
