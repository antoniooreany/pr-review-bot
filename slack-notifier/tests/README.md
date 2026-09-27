# slack-notifier tests

Four test types, four classes of bugs caught.

## Unit (`tests/test_*.py`)
Pure-Python `unittest` tests with mocks for HTTP / subprocess.
Fast (~5s for full suite), catches logic bugs in single functions.
Covers: notifier detection, Slack post, Jira fetch, secret resolution,
admin commands, provider abstraction.

## Contract (`test_coverage_v131.ContractPayloadTests`)
Real-world webhook payload fixtures (captured from production GitLab +
GitLab-like GitHub events). Catches: schema drift, missing required
fields, unexpected payload shapes from real senders.

## Property-based (`test_coverage_v131.PropertyBasedTests`)
Uses [hypothesis](https://hypothesis.readthedocs.io/) to generate
arbitrary inputs across the input space. Catches: edge cases hand-written
tests miss (long titles, special characters, numeric edge cases).
Examples:
- `extract_jira_key` never raises on arbitrary text (200 examples)
- `build_slack_payload` accepts any string inputs (20 examples)
- Severity classification always returns one of 4 valid tags

## Integration / e2e (`smoke/e2e_smoke.py`)
Real `slack-notifier.py` running as HTTP server, mock Slack/GitLab/Jira
servers, real HTTP requests through the full webhook flow. Not a unit
test — confirms the bot actually works end-to-end on a deployed binary.

## Running

```bash
# All unit + property + contract tests
bash tests/run.sh

# Integration e2e (from slack-notifier/ dir)
cd slack-notifier && PYTHONPATH=. python ../smoke/e2e_smoke.py

# Coverage (with coverage.py installed)
python -m coverage run --source=notifier --omit="*/tests/*" -m unittest tests.test_notifier tests.test_signature tests.test_healthcheck tests.test_v1_features tests.test_secrets tests.test_admin_and_multi tests.test_coverage_v131
python -m coverage report
```

Current coverage: ~82% on `notifier.py`. Uncovered areas: WebhookHandler
HTTP edge cases covered by `smoke/e2e_smoke.py`, not unit tests.

## When to add which type

| You're testing... | Add a... |
|---|---|
| Single function logic | Unit test |
| Real-world webhook format | Contract test |
| Edge cases across many inputs | Property test |
| Full HTTP request → response flow | Integration test |
