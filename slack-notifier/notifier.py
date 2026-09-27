"""slack-notifier service.

Lightweight HTTP service that listens for GitLab note webhooks and forwards
PR-Agent reviews to Slack. Runs as a sidecar alongside PR-Agent.

Design constraints:
- Python stdlib only (no Flask, no FastAPI). One container, no extra deps.
- Runs in the same docker-compose as PR-Agent; both receive GitLab webhooks
  independently. PR-Agent posts comments; this service watches for those
  comments and notifies Slack.
- Never blocks PR-Agent. If Slack is down, the sidecar logs and retries;
  PR-Agent continues unaffected.
"""
import hmac
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

# Structured logging — one JSON line per event. Consumed by Promtail/Loki in
# real deploys; locally it's grep-friendly.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"slack-notifier","msg":"%(message)s"}',
)
log = logging.getLogger("slack-notifier")


# ---------------------------------------------------------------------------
# Pure functions — easy to unit-test, no I/O.
# ---------------------------------------------------------------------------

def _probe_url(url, method="GET", headers=None, timeout=5):
    """Single HTTP probe. Returns dict with status, http_code (optional),
    latency_ms, and error (optional). Never raises."""
    start = time.monotonic()
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(1)  # drain at least one byte
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {
                "status": "ok" if 200 <= resp.status < 300 else "fail",
                "http_code": resp.status,
                "latency_ms": elapsed_ms,
            }
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "fail",
            "http_code": e.code,
            "latency_ms": elapsed_ms,
            "error": f"http_{e.code}",
        }
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "fail",
            "latency_ms": elapsed_ms,
            "error": str(e)[:100],
        }


def check_gitlab(gitlab_url, timeout=3):
    """Ping GitLab /api/v4/version. Returns probe result dict."""
    return _probe_url(gitlab_url, method="GET", timeout=timeout)


def check_llm(llm_url, api_key, timeout=5):
    """Ping LLM /v1/models with auth. Returns probe result dict."""
    return _probe_url(
        llm_url,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )


def run_health_checks(gitlab_url, llm_url, llm_api_key,
                      gitlab_timeout=3, llm_timeout=5):
    """Run GitLab and LLM probes in parallel. Returns aggregate health.

    Total wall time = max(gitlab_timeout, llm_timeout) + overhead.
    """
    with ThreadPoolExecutor(max_workers=2) as executor:
        gitlab_future = executor.submit(check_gitlab, gitlab_url, gitlab_timeout)
        llm_future = executor.submit(check_llm, llm_url, llm_api_key, llm_timeout)
        gitlab_result = gitlab_future.result()
        llm_result = llm_future.result()

    aggregate = "healthy" if (gitlab_result["status"] == "ok" and
                              llm_result["status"] == "ok") else "unhealthy"
    return {
        "status": aggregate,
        "checks": {
            "gitlab": gitlab_result,
            "llm": llm_result,
        },
    }


def verify_signature(headers, expected_token):
    """Validate X-Gitlab-Token header against expected_token.

    GitLab's webhook UI has a "Secret token" field that becomes the
    `X-Gitlab-Token` header on every webhook delivery.

    Returns True on match, False on mismatch.
    Constant-time comparison via hmac.compare_digest (no timing oracle).
    If expected_token is empty, validation is disabled (dev mode; caller
    should log a warning).
    """
    if not expected_token:
        return True

    # HTTP headers are case-insensitive. rfile/HTTPServer normalizes them,
    # but our function should be robust to either casing for direct callers.
    provided = ""
    for key, value in (headers or {}).items():
        if key.lower() == "x-gitlab-token":
            provided = value
            break

    if not provided:
        return False

    return hmac.compare_digest(provided, expected_token)


def is_bot_comment(payload, bot_user_id):
    """True if the GitLab note event was authored by our bot user."""
    if not bot_user_id:
        return False
    user = payload.get("user") or {}
    return user.get("id") == bot_user_id


def is_note_on_merge_request(payload):
    """True if this is a note (comment) event on a merge request."""
    if payload.get("object_kind") != "note":
        return False
    obj = payload.get("object_attributes") or {}
    return obj.get("noteable_type") == "MergeRequest"


def build_slack_payload(mr_url, mr_title, author, severity, summary):
    """Format a Slack Block Kit message for a PR-Agent review."""
    return {
        "text": f"🤖 PR-Agent review on {mr_title}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*<{mr_url}|{mr_title}>*",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Author:*\n{author}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity}"},
                    {"type": "mrkdwn", "text": f"*Summary:*\n{summary}"},
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# I/O — HTTP outbound to Slack. Kept separate so tests can mock it.
# ---------------------------------------------------------------------------

def post_to_slack(url, mr_url, mr_title, author, severity, summary,
                  max_retries=3, retry_delay=1.0):
    """POST formatted payload to Slack incoming webhook URL.

    Returns True on success, False if all retries exhausted or non-2xx.
    Retries only on 5xx (server errors). 4xx are treated as fatal
    (bad payload / wrong URL — retrying won't help).
    """
    if not url:
        log.info("slack_disabled (no SLACK_WEBHOOK_URL)")
        return False

    payload = build_slack_payload(mr_url, mr_title, author, severity, summary)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = resp.status
            if 200 <= code < 300:
                log.info(f"slack_posted mr={mr_url} attempt={attempt}")
                return True
            # Non-2xx. If 5xx, retry. Otherwise, fatal.
            if 500 <= code < 600:
                log.warning(f"slack_5xx mr={mr_url} code={code} attempt={attempt}")
                if attempt < max_retries:
                    time.sleep(retry_delay * attempt)  # linear backoff
                    continue
                return False
            log.error(f"slack_4xx mr={mr_url} code={code} fatal")
            return False
        except urllib.error.HTTPError as e:
            # HTTPError carries a status code; treat like a response.
            code = e.code
            if 500 <= code < 600 and attempt < max_retries:
                log.warning(f"slack_http_error mr={mr_url} code={code} attempt={attempt}")
                time.sleep(retry_delay * attempt)
                continue
            log.error(f"slack_http_error_fatal mr={mr_url} code={code}")
            return False
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning(f"slack_network_error mr={mr_url} err={e} attempt={attempt}")
            if attempt < max_retries:
                time.sleep(retry_delay * attempt)
                continue
            return False

    return False


def handle_webhook(payload, bot_user_id, slack_webhook_url):
    """Dispatch a GitLab webhook payload. Returns True if handled (or
    intentionally ignored, including disabled state). False only on hard errors."""
    if not is_note_on_merge_request(payload):
        log.debug("ignoring non-note event")
        return True  # intentional ignore, not failure

    if not is_bot_comment(payload, bot_user_id):
        log.debug("ignoring non-bot note")
        return True

    # Bot note on MR, but Slack is disabled — intentional no-op, not failure.
    if not slack_webhook_url:
        log.info("slack_disabled skipping notification")
        return True

    mr = payload.get("merge_request") or {}
    note = (payload.get("object_attributes") or {}).get("note", "")
    user = payload.get("user") or {}

    return post_to_slack(
        url=slack_webhook_url,
        mr_url=mr.get("url", ""),
        mr_title=mr.get("title", "(no title)"),
        author=user.get("username", "bot"),
        severity=_extract_severity(note),
        summary=_extract_summary(note),
    )


def _extract_severity(note_text):
    """Pull severity tag from the bot's note. Falls back to NEUTRAL."""
    for tag in ("🔴 HIGH", "🟡 MEDIUM", "🟢 LOW"):
        if tag in note_text:
            return tag
    return "ℹ️ NEUTRAL"


def _extract_summary(note_text):
    """First line of the note, truncated. Used as Slack field."""
    first_line = note_text.split("\n", 1)[0].strip()
    return first_line[:300]


# ---------------------------------------------------------------------------
# HTTP server — receives GitLab webhooks on /webhook.
# ---------------------------------------------------------------------------

class WebhookHandler(BaseHTTPRequestHandler):
    # Configured at server start via env vars.
    bot_user_id = int(os.environ.get("GITLAB_BOT_USER_ID", "0"))
    slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "")

    def do_POST(self):
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        # Defense in depth: validate signature even when nginx is in front.
        if not verify_signature(self.headers, self.webhook_secret):
            log.warning(f"webhook_signature_invalid path={self.path}")
            self.send_response(401)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            log.warning(f"invalid_json path={self.path}")
            self.send_response(400)
            self.end_headers()
            return

        ok = handle_webhook(
            payload,
            bot_user_id=self.bot_user_id,
            slack_webhook_url=self.slack_webhook_url,
        )
        self.send_response(200 if ok else 500)
        self.end_headers()

    def do_GET(self):
        # Simple health endpoint for the sidecar itself.
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        # Deep health: probe upstream dependencies in parallel.
        if self.path == "/health/deep":
            gitlab_url = os.environ.get("GITLAB_URL", "")
            llm_url = os.environ.get("OPENAI_BASE_URL", "")
            llm_key = os.environ.get("OPENAI_KEY", "")
            if not gitlab_url or not llm_url:
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"unconfigured"}')
                return
            result = run_health_checks(
                gitlab_url=f"{gitlab_url.rstrip('/')}/api/v4/version",
                llm_url=f"{llm_url.rstrip('/')}/v1/models",
                llm_api_key=llm_key,
            )
            code = 200 if result["status"] == "healthy" else 503
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # Quiet the default access log; our structured logger handles this.
        pass


def main():
    port = int(os.environ.get("PORT", "3001"))
    if not os.environ.get("WEBHOOK_SECRET"):
        log.warning("WEBHOOK_SECRET not set — webhook signature validation disabled (dev mode)")
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    log.info(f"starting slack-notifier on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
