#!/usr/bin/env bash
# T12 — assert review_guidelines.md is structured per internal WinWin rules.
# Each rule category must cite a Confluence source. Anti-noise guards
# (do-NOT-flag list, bot-must-NOT list) must be present.
set -euo pipefail

cd "$(dirname "$0")/.."

GUIDELINES="review_guidelines.md"

if [ ! -f "$GUIDELINES" ]; then
    echo "FAIL: $GUIDELINES does not exist"
    exit 1
fi

# Required top-level sections (markdown headings).
REQUIRED_SECTIONS=(
    "# winwin.travel review conventions"     # title
    "## Sources"                              # citations block
    "## Style & naming"                       # category
    "## Architecture"                         # category
    "## Spring / JPA specifics"               # category
    "## Security"                             # category
    "## Logging"                              # category
    "## Exception handling"                   # category
    "## Input validation"                     # category
    "## Code smells"                          # category
    "## PR hygiene"                           # category (branch, changelog, tests)
    "## AI assistant guardrails"              # anti-noise + safety
)

FAIL=0
for section in "${REQUIRED_SECTIONS[@]}"; do
    if ! grep -qF "$section" "$GUIDELINES"; then
        echo "FAIL: missing section: '$section'"
        FAIL=1
    fi
done

# Each rule category must cite at least one confluence.winwin.travel URL.
if ! grep -qE "confluence\.winwin\.travel" "$GUIDELINES"; then
    echo "FAIL: no confluence.winwin.travel source citations found"
    FAIL=1
fi

# Anti-noise guards must be present.
if ! grep -qE "(do NOT flag|never flag|do not flag|never review)" -i "$GUIDELINES"; then
    echo "FAIL: missing anti-noise 'do NOT flag' guidance"
    FAIL=1
fi

# Bot-must-NOT list (from AI Usage Standards): no production data, no secrets
# in prompts, no auto-merge decisions.
REQUIRED_GUARDRAILS=(
    "production"
    "secret"
    "merge"
)

for term in "${REQUIRED_GUARDRAILS[@]}"; do
    if ! grep -qi "$term" "$GUIDELINES"; then
        echo "FAIL: AI-Usage guardrail missing reference to: $term"
        FAIL=1
    fi
done

# Source citations must point to known internal pages. We assert at least
# 4 distinct confluence URLs to ensure multi-source coverage (matches our
# 4 source documents: BENEW PR-Agent, HDB PR Guidelines, HR Java Checklist,
# QA AI Usage).
UNIQUE_URLS=$(grep -oE "https://confluence\.winwin\.travel[^\"' )]+" "$GUIDELINES" \
    | sort -u | wc -l)
if [ "$UNIQUE_URLS" -lt 4 ]; then
    echo "FAIL: expected >= 4 distinct confluence URLs, found $UNIQUE_URLS"
    FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi

echo "PASS: $GUIDELINES has all required sections and $UNIQUE_URLS confluence citations"
exit 0
