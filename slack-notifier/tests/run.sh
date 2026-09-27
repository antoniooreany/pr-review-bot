#!/usr/bin/env bash
# TDD wrapper: run slack-notifier unit tests. Add to tests/run_all.sh later.
set -euo pipefail

cd "$(dirname "$0")/.."

# Stub implementation so the import doesn't fail before we write notifier.py.
if [ ! -f notifier.py ]; then
    cat > notifier.py <<'STUB'
"""Stub notifier — replaced by real implementation after tests are RED."""
def is_bot_comment(payload, bot_user_id):
    return False
def post_to_slack(url, mr_url, mr_title, author, severity, summary, max_retries=3, retry_delay=1.0):
    return False
def handle_webhook(payload, bot_user_id, slack_webhook_url):
    return False
def verify_signature(headers, expected_token):
    return False
STUB
fi

PYTHONPATH=. python -m unittest tests.test_notifier tests.test_signature tests.test_healthcheck tests.test_v1_features tests.test_secrets -v 2>&1
