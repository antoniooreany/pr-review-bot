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
if (cd slack-notifier && PYTHONPATH=. python -m unittest \
        tests.test_notifier tests.test_signature tests.test_healthcheck \
        tests.test_v1_features tests.test_secrets tests.test_admin_and_multi \
        tests.test_coverage_v131 tests.test_coverage_v132 tests.test_coverage_v133 \
        -v 2>&1) > /dev/null; then
    PASS=$((PASS + 1))
    echo "PASS"
else
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("slack-notifier/tests/run.sh")
fi
echo

# CredMan setup script tests (Python unit, repo root)
echo "── CredMan script tests ──"
if PYTHONPATH=. python -m unittest tests.test_credman_setup -v 2>&1 > /dev/null; then
    PASS=$((PASS + 1))
    echo "PASS"
else
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("tests/test_credman_setup.py")
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
