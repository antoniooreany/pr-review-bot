#!/usr/bin/env bash
# Run all tests in this directory. Returns 0 only if every test passes.
set -uo pipefail

cd "$(dirname "$0")/.."

PASS=0
FAIL=0
FAILED_TESTS=()

for t in tests/test_*.sh; do
    echo "── $(basename "$t") ──"
    if bash "$t"; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILED_TESTS+=("$t")
    fi
    echo
done

# Slack notifier unit tests (Python unittest)
echo "── slack-notifier unit tests ──"
if bash slack-notifier/tests/run.sh > /dev/null 2>&1; then
    PASS=$((PASS + 1))
    echo "PASS"
else
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("slack-notifier/tests/run.sh")
fi
echo

echo "════════════════════════════════"
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo "Failing tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  - $t"
    done
    exit 1
fi
echo "All tests passed."
exit 0
