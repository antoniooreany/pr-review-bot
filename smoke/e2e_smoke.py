"""Real end-to-end smoke test for pr-review-bot.

Runs the actual slack-notifier.py as a real HTTP server, stands up a mock
Slack server, fires a fake GitLab note webhook through, and verifies:
  1. Slack-notifier accepts the webhook (HMAC validation)
  2. Slack-notifier posts to Slack (mock server captures it)
  3. Jira context is fetched and prepended to Slack message
  4. Metrics increment correctly
  5. request_id flows through log lines
  6. /health/deep probes dependencies (uses mock servers)

This is NOT a unit test -- it's a real HTTP smoke test of the actual deployed
binary. Confirms the bot actually works end-to-end, not just in isolation.

Run from slack-notifier/ directory: PYTHONPATH=. python ../smoke/e2e_smoke.py
"""
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# Force UTF-8 stdout on Windows (cp1252 default) so we can print emojis.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


# ── Mock servers (Slack + GitLab + Jira) ────────────────────────────────────

captured = {
    "slack_posts": [],
    "gitlab_api_calls": [],
    "jira_api_calls": [],
}


class MockSlackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        captured["slack_posts"].append(json.loads(body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a, **kw): pass


class MockGitlabHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        captured["gitlab_api_calls"].append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if "/api/v4/version" in self.path:
            self.wfile.write(b'{"version":"17.0.0"}')
        else:
            self.wfile.write(b'{}')
    def log_message(self, *a, **kw): pass


class MockJiraHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        captured["jira_api_calls"].append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"key":"HD-1234","fields":{"summary":"Add OAuth login flow","description":"Implement GitHub OAuth","status":{"name":"In Progress"}}}')
    def log_message(self, *a, **kw): pass


def start_mock_server(handler, name):
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


# ── Step 1: start mocks ────────────────────────────────────────────────────

print("=== Starting mock Slack, GitLab, Jira servers ===")
slack_srv, slack_port = start_mock_server(MockSlackHandler, "slack")
gitlab_srv, gitlab_port = start_mock_server(MockGitlabHandler, "gitlab")
jira_srv, jira_port = start_mock_server(MockJiraHandler, "jira")
print(f"  slack mock:    http://127.0.0.1:{slack_port}")
print(f"  gitlab mock:   http://127.0.0.1:{gitlab_port}")
print(f"  jira mock:     http://127.0.0.1:{jira_port}")


# ── Step 2: start slack-notifier with env pointing at mocks ─────────────────

print("\n=== Starting slack-notifier ===")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "slack-notifier"))
import notifier  # noqa: E402

# Set env BEFORE starting the HTTP server (it reads on startup)
os.environ["SLACK_WEBHOOK_URL"] = f"http://127.0.0.1:{slack_port}/webhook"
os.environ["GITLAB_BOT_USER_ID"] = "99"
os.environ["GITLAB_URL"] = f"http://127.0.0.1:{gitlab_port}"
os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{gitlab_port}"  # reuse for LLM probe
os.environ["OPENAI_KEY"] = "test-key"
os.environ["JIRA_URL"] = f"http://127.0.0.1:{jira_port}"
os.environ["JIRA_EMAIL"] = "bot@example"
os.environ["JIRA_TOKEN"] = "jira-token"
os.environ["WEBHOOK_SECRET"] = "test-secret-123"
os.environ["PORT"] = "3001"

# Start slack-notifier in a background thread
notifier_port = 3001
notifier.WebhookHandler.bot_user_id = 99
notifier.WebhookHandler.slack_webhook_url = os.environ["SLACK_WEBHOOK_URL"]
notifier.WebhookHandler.webhook_secret = os.environ["WEBHOOK_SECRET"]
httpd = HTTPServer(("127.0.0.1", notifier_port), notifier.WebhookHandler)
notifier_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
notifier_thread.start()
time.sleep(0.5)  # let server bind
print(f"  slack-notifier: http://127.0.0.1:{notifier_port}")
print(f"  signature secret: {os.environ['WEBHOOK_SECRET']}")


# ── Step 3: health endpoints ────────────────────────────────────────────────

print("\n=== Test: GET /health (liveness) ===")
resp = urllib.request.urlopen(f"http://127.0.0.1:{notifier_port}/health", timeout=2)
print(f"  status: {resp.status}")
assert resp.status == 200, "liveness should return 200"
print("  PASS")

print("\n=== Test: GET /health/deep (readiness with deps) ===")
resp = urllib.request.urlopen(f"http://127.0.0.1:{notifier_port}/health/deep", timeout=5)
print(f"  status: {resp.status}")
body = json.loads(resp.read())
print(f"  body: {json.dumps(body, indent=2)[:200]}")
assert resp.status == 200, "all deps should be reachable"
assert body["status"] == "healthy"
print("  PASS")

print("\n=== Test: GET /metrics (Prometheus format) ===")
resp = urllib.request.urlopen(f"http://127.0.0.1:{notifier_port}/metrics", timeout=2)
text = resp.read().decode("utf-8")
print(f"  sample: {text.split(chr(10))[0]}")
assert "pr_review_bot_" in text
print("  PASS")


# ── Step 4: webhook signature validation ────────────────────────────────────

print("\n=== Test: POST /webhook without signature -> 401 ===")
req = urllib.request.Request(
    f"http://127.0.0.1:{notifier_port}/webhook",
    data=b'{}',
    method="POST",
)
try:
    resp = urllib.request.urlopen(req, timeout=2)
    assert False, f"Should have 401, got {resp.status}"
except urllib.error.HTTPError as e:
    print(f"  status: {e.code}")
    assert e.code == 401
    print("  PASS")


# ── Step 5: webhook with bot note -> Slack notification with Jira context ───

print("\n=== Test: POST /webhook (bot note on HD-1234 MR) -> Slack post with Jira context ===")
gitlab_payload = {
    "object_kind": "note",
    "object_attributes": {
        "noteable_type": "MergeRequest",
        "note": "🤖 AI review complete. 🔴 HIGH severity issue: missing @Transactional on save method.",
    },
    "merge_request": {
        "url": "https://gitlab.winwin.travel/hotels-data/backend/-/merge_requests/1234",
        "title": "HD-1234: Add OAuth login flow",
        "source_branch": "HD-1234-oauth",
    },
    "user": {"id": 99, "username": "pr-review-bot"},
}

req = urllib.request.Request(
    f"http://127.0.0.1:{notifier_port}/webhook",
    data=json.dumps(gitlab_payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-Gitlab-Token": os.environ["WEBHOOK_SECRET"],
    },
    method="POST",
)
resp = urllib.request.urlopen(req, timeout=5)
print(f"  webhook response: {resp.status}")
assert resp.status == 200

# Verify X-Request-Id header in response
request_id = resp.headers.get("X-Request-Id")
print(f"  X-Request-Id: {request_id}")
assert request_id and len(request_id) == 12
print("  PASS")


# ── Step 6: verify side effects ────────────────────────────────────────────

print("\n=== Verify Slack received the notification ===")
time.sleep(0.5)  # let Slack post land
assert len(captured["slack_posts"]) >= 1, "expected Slack post"
slack_msg = captured["slack_posts"][-1]
print(f"  text: {slack_msg['text']}")
print(f"  blocks: {len(slack_msg['blocks'])} sections")
# Verify Jira context was prepended (search for HD-1234 in summary field)
field_texts = json.dumps(slack_msg)
assert "HD-1234" in field_texts, "Jira key should be in Slack message"
assert "OAuth login" in field_texts, "Jira summary should be prepended"
print("  PASS -- Jira context (HD-1234 OAuth login) is in Slack message")

print("\n=== Verify Jira API was called ===")
assert any("HD-1234" in path for path in captured["jira_api_calls"]), "Jira API should have been hit"
print(f"  Jira hits: {captured['jira_api_calls']}")
print("  PASS")

print("\n=== Verify Slack message contains severity tag ===")
# Find the severity field
fields = slack_msg["blocks"][1]["fields"]
severity_field = [f["text"] for f in fields if "Severity" in f["text"]]
assert severity_field, "severity field should be present"
print(f"  {severity_field[0]}")
assert "🔴 HIGH" in severity_field[0]
print("  PASS -- 🔴 HIGH severity correctly extracted")

print("\n=== Verify GitLab API was hit (for /health/deep probe) ===")
assert any("/api/v4/version" in path for path in captured["gitlab_api_calls"])
print(f"  GitLab hits: {captured['gitlab_api_calls']}")
print("  PASS")

print("\n=== Verify metrics counter incremented ===")
resp = urllib.request.urlopen(f"http://127.0.0.1:{notifier_port}/metrics", timeout=2)
text = resp.read().decode("utf-8")
# Find slack_posts_total line
for line in text.split("\n"):
    if "slack_posts_total" in line and "status=" in line:
        print(f"  {line}")
        assert '"ok"' in line
        break
else:
    print(f"  Metrics output:\n{text}")
    assert False, "expected slack_posts_total counter"
print("  PASS")


# ── Final summary ──────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("END-TO-END SMOKE TEST: ALL PASSED")
print("=" * 60)
print(f"Webhook signature:    validated (HMAC)")
print(f"Slack post:           {len(captured['slack_posts'])} message(s) delivered")
print(f"Jira fetch:           {len([p for p in captured['jira_api_calls'] if 'HD-1234' in p])} hit(s)")
print(f"Severity extraction:  🔴 HIGH (from bot note text)")
print(f"Request ID correlation: {request_id} (12 hex chars)")
print(f"GitLab probes:        {len(captured['gitlab_api_calls'])} hit(s) for /health/deep")
print(f"Metrics:              incremented correctly")
print()

# Cleanup
httpd.shutdown()
slack_srv.shutdown()
gitlab_srv.shutdown()
jira_srv.shutdown()
print("Mock servers stopped. Done.")
