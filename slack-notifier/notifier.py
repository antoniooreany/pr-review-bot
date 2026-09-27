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
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import base64
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

# ── T26: admin commands via MR/PR comments ────────────────────────────────────

_bot_state = {
    "enabled": True,
    "disabled_at": None,
    "disabled_by": None,
    "admin_user_id": "",  # set via env var BOT_ADMIN_USER_ID
}


def handle_admin_command(command, admin_user_id, slack_webhook_url=""):
    """Process a /bot <verb> slash command from an MR/PR comment.

    Allowed commands:
      /bot status   — post Slack with health snapshot
      /bot disable  — turn off Slack notifications (bot reviews still post)
      /bot enable   — re-enable
      /bot stats    — post Slack with metrics summary

    Authorization: only the configured admin_user_id can run.
    Non-admin attempts return False silently (no Slack reply).
    Returns True if processed, False if rejected.
    """
    # Auth check FIRST — don't mutate state before validating caller.
    caller = str(admin_user_id) if admin_user_id else ""
    expected = _bot_state.get("admin_user_id", "")
    if caller != expected or not caller:
        log.info(f"admin_unauthorized caller={caller}")
        return False

    # Now seed admin_user_id from env if not yet set (first admin call)
    if not _bot_state["admin_user_id"] and caller:
        _bot_state["admin_user_id"] = caller

    if not command or not command.startswith("/bot "):
        return False

    verb = command[5:].strip().lower()
    if verb not in ("status", "disable", "enable", "stats"):
        log.info(f"admin_unknown_verb verb={verb}")
        return False

    log.info(f"admin_command verb={verb}")

    # Execute verb. Return True if processed (regardless of Slack post result).
    if verb == "status":
        summary = f"enabled={_bot_state['enabled']}  disabled_at={_bot_state['disabled_at']}"
        post_to_slack(
            url=slack_webhook_url,
            mr_url="admin://status",
            mr_title="Bot status",
            author="admin",
            severity="ℹ️ INFO",
            summary=summary,
            request_id="admin-status",
        )
        return True

    if verb == "stats":
        post_to_slack(
            url=slack_webhook_url,
            mr_url="admin://stats",
            mr_title="Bot stats",
            author="admin",
            severity="ℹ️ INFO",
            summary=metrics_render()[:500],
            request_id="admin-stats",
        )
        return True

    if verb == "disable":
        _bot_state["enabled"] = False
        _bot_state["disabled_at"] = time.time()
        _bot_state["disabled_by"] = caller
        log.info(f"admin_disabled by={caller}")
        post_to_slack(
            url=slack_webhook_url,
            mr_url="admin://disable",
            mr_title="Bot disabled",
            author="admin",
            severity="ℹ️ INFO",
            summary=f"Slack notifications OFF. Re-enable with `/bot enable`. Disabled by user_id={caller}.",
            request_id="admin-disable",
        )
        return True

    if verb == "enable":
        was_disabled_by = _bot_state["disabled_by"]
        _bot_state["enabled"] = True
        _bot_state["disabled_at"] = None
        _bot_state["disabled_by"] = None
        log.info(f"admin_enabled by={caller}")
        post_to_slack(
            url=slack_webhook_url,
            mr_url="admin://enable",
            mr_title="Bot re-enabled",
            author="admin",
            severity="ℹ️ INFO",
            summary=f"Slack notifications ON. Was disabled by user_id={was_disabled_by}.",
            request_id="admin-enable",
        )
        return True

    return False  # unreachable


# ── T24: request_id generation (12 hex chars) ────────────────────────────────

def generate_request_id():
    """Generate a 12-char hex request_id for log correlation."""
    return uuid.uuid4().hex[:12]


# ── T26: admin state (module-level singleton) ────────────────────────────────

_bot_state = {
    "enabled": True,
    "disabled_at": None,
    "disabled_by": None,
    "admin_user_id": "",  # set via env var BOT_ADMIN_USER_ID
}


# ── T29: secrets resolution (env -> Windows Credential Manager -> fallback) ─

def get_secret(key, credman_target="pr-review-bot", fallback=None):
    """Resolve a secret by checking env var first, then Windows Credential Manager.

    Resolution order:
      1. Environment variable `key` — wins if set (even to empty string).
      2. Windows Credential Manager: Generic credential with target
         `<credman_target>:<KEY>` — read via PowerShell.
      3. `fallback` — used if both missing.

    Operators store creds once with `cmdkey /generic:pr-review-bot:<KEY>`:
      cmdkey /generic:pr-review-bot:OPENAI_KEY /user:apikey
    The /user value is the password (your secret). Read back via PowerShell.
    """
    val = os.environ.get(key)
    if val:
        return val
    try:
        cred_val = _read_windows_credman(credman_target, key)
    except (OSError, RuntimeError) as e:
        log.debug(f"get_secret_credman_failed key={key} err={e}")
        cred_val = None
    if cred_val:
        return cred_val
    return fallback


def _read_windows_credman(credman_target, key):
    """Read a generic credential from Windows Credential Manager via PowerShell.

    Returns the password string, or None if not found / unsupported platform.
    Never raises — all errors map to None (graceful degradation).
    """
    if sys.platform != "win32":
        return None
    target = f"{credman_target}:{key}"
    ps_cmd = (
        f"$ErrorActionPreference = 'SilentlyContinue'; "
        f"$c = Get-StoredCredential -Target '{target}'; "
        f"if ($c) {{ $c.GetNetworkCredential().Password }} "
        f"else {{ '' }}"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.debug(f"credman_read_failed target={target} err={e}")
        return None
    if result.returncode != 0:
        log.debug(f"credman_nonzero_exit target={target} stderr={result.stderr[:100]}")
        return None
    val = result.stdout.strip()
    return val if val else None


def _extract_comment(payload, provider):
    """Extract comment body, provider-agnostic."""
    if provider == "gitlab":
        return (payload.get("object_attributes") or {}).get("note", "")
    if provider == "github":
        return (payload.get("comment") or {}).get("body", "")
    return ""


def _extract_pr_url(payload, provider):
    """Extract MR/PR URL, provider-agnostic."""
    if provider == "gitlab":
        return (payload.get("merge_request") or {}).get("url", "")
    if provider == "github":
        return (payload.get("issue") or {}).get("html_url", "")
    return ""


def _extract_mr_title(payload, provider):
    """Extract MR/PR title, provider-agnostic."""
    if provider == "gitlab":
        return (payload.get("merge_request") or {}).get("title", "")
    if provider == "github":
        return (payload.get("issue") or {}).get("title", "")
    return ""


def _extract_author_id(payload, provider):
    """Extract comment author identifier, provider-agnostic.

    GitLab: numeric user.id. GitHub: string sender.login.
    """
    if provider == "gitlab":
        return (payload.get("user") or {}).get("id")
    if provider == "github":
        return (payload.get("sender") or {}).get("login")
    return None


# ── T21: metrics counters (Prometheus exposition format, in-memory) ──────────

_metrics_counters = {}
_metrics_gauges = {}


def reset_metrics():
    """Reset all metrics. Used by tests; not called in production."""
    global _metrics_counters, _metrics_gauges
    _metrics_counters = {}
    _metrics_gauges = {}


def metrics_inc(name, labels=None):
    """Increment a counter with optional labels."""
    safe = _safe_labels(labels or {})
    key = (name, _labels_key(safe))
    _metrics_counters[key] = _metrics_counters.get(key, 0) + 1


def metrics_set(name, value, labels=None):
    """Set a gauge value."""
    safe = _safe_labels(labels or {})
    key = (name, _labels_key(safe))
    _metrics_gauges[key] = value


# Label names that must NEVER appear in metrics output (could leak secrets).
_SECRET_LABEL_NAMES = frozenset({
    "api_key", "apikey", "secret", "password", "token",
    "authorization", "auth", "private_key", "pat",
})


def _safe_labels(labels):
    """Drop any label whose name is in the secrets blocklist."""
    return {k: v for k, v in labels.items() if k.lower() not in _SECRET_LABEL_NAMES}


def _labels_key(labels):
    """Sort labels for consistent key generation."""
    return tuple(sorted(labels.items()))


def _format_labels(labels_tuple):
    if not labels_tuple:
        return ""
    parts = [f'{k}="{v}"' for k, v in labels_tuple]
    return "{" + ",".join(parts) + "}"


def metrics_render():
    """Render all metrics in Prometheus exposition format."""
    lines = []
    seen_names = set()
    for (name, labels), value in sorted(_metrics_counters.items()):
        if name not in seen_names:
            lines.append(f"# HELP pr_review_bot_{name} counter")
            lines.append(f"# TYPE pr_review_bot_{name} counter")
            seen_names.add(name)
        lines.append(f"pr_review_bot_{name}{_format_labels(labels)} {value}")
    for (name, labels), value in sorted(_metrics_gauges.items()):
        if name not in seen_names:
            lines.append(f"# HELP pr_review_bot_{name} gauge")
            lines.append(f"# TYPE pr_review_bot_{name} gauge")
            seen_names.add(name)
        lines.append(f"pr_review_bot_{name}{_format_labels(labels)} {value}")
    return "\n".join(lines) + "\n"


# ── T22: Jira fetch ───────────────────────────────────────────────────────────

def extract_jira_key(text):
    """Extract HD-NNN Jira key from MR title or branch name. None if absent."""
    if not text:
        return None
    m = re.search(r'\b(HD-\d+)\b', str(text))
    return m.group(1) if m else None


def fetch_jira_issue(ticket_key, jira_url, email, token, timeout=5):
    """Fetch Jira issue by key. Returns dict with key/summary/description,
    or None on any failure (404, network error, auth failure).

    Graceful degradation: never raises. Caller decides what to do.
    """
    if not ticket_key or not jira_url:
        return None
    url = f"{jira_url.rstrip('/')}/rest/api/2/issue/{ticket_key}"
    credentials = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {credentials}",
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.warning(f"jira_fetch_failed key={ticket_key} err={e}")
        return None
    fields = data.get("fields") or {}
    return {
        "key": data.get("key", ticket_key),
        "summary": fields.get("summary", ""),
        "description": fields.get("description", ""),
        "status": (fields.get("status") or {}).get("name", ""),
    }


# ── T21 probe and T22 fetch helpers ──────────────────────────────────────────

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
    """True if the comment event was authored by our bot user.

    Provider-agnostic via T30. Falls back to GitLab format (user.id) when
    provider can't be detected — keeps legacy callers working.
    """
    if not bot_user_id:
        return False
    provider = _detect_provider(payload, {})
    if not provider:
        # Fallback for unknown payload — assume GitLab shape (legacy behavior).
        return (payload.get("user") or {}).get("id") == bot_user_id
    return _extract_author_id(payload, provider) == bot_user_id


# ── T30: provider abstraction (GitLab + GitHub) ───────────────────────────────

def _detect_provider(payload, headers):
    """Auto-detect GitLab vs GitHub webhook payload.

    Detection priority:
      1. Headers (most reliable — set by webhook sender).
      2. Payload structure (fallback for callers who don't pass headers).

    GitLab: object_kind == "note" on a MergeRequest.
    GitHub: X-GitHub-Event == "issue_comment" with action == "created",
            OR payload has {action: "created", comment, issue.html_url}.
    Returns: "gitlab" | "github" | None
    """
    if payload.get("object_kind") == "note":
        return "gitlab"
    event = (headers.get("X-Github-Event") or headers.get("X-GitHub-Event") or "").lower()
    if event == "issue_comment" and payload.get("action") == "created":
        return "github"
    # Structural fallback when headers aren't passed (e.g. unit tests, retries)
    if (payload.get("action") == "created"
            and "comment" in payload
            and "issue" in payload
            and "html_url" in payload.get("issue", {})):
        return "github"
    return None


def _extract_comment(payload, provider):
    """Extract comment body, provider-agnostic."""
    if provider == "gitlab":
        return (payload.get("object_attributes") or {}).get("note", "")
    if provider == "github":
        return (payload.get("comment") or {}).get("body", "")
    return ""


def _extract_pr_url(payload, provider):
    """Extract MR/PR URL, provider-agnostic."""
    if provider == "gitlab":
        return (payload.get("merge_request") or {}).get("url", "")
    if provider == "github":
        return (payload.get("issue") or {}).get("html_url", "")
    return ""


def _extract_mr_title(payload, provider):
    """Extract MR/PR title, provider-agnostic."""
    if provider == "gitlab":
        return (payload.get("merge_request") or {}).get("title", "")
    if provider == "github":
        return (payload.get("issue") or {}).get("title", "")
    return ""


def _extract_author_id(payload, provider):
    """Extract comment author identifier, provider-agnostic.

    GitLab: numeric user.id
    GitHub: string sender.login
    """
    if provider == "gitlab":
        return (payload.get("user") or {}).get("id")
    if provider == "github":
        return (payload.get("sender") or {}).get("login")
    return None


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
                  max_retries=3, retry_delay=1.0, request_id=""):
    """POST formatted payload to Slack incoming webhook URL.

    Returns True on success, False if all retries exhausted or non-2xx.
    Retries only on 5xx (server errors). 4xx are treated as fatal
    (bad payload / wrong URL — retrying won't help).
    """
    if not url:
        log.info("slack_disabled (no SLACK_WEBHOOK_URL)")
        metrics_inc("slack_posts_total", {"status": "disabled"})
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
                metrics_inc("slack_posts_total", {"status": "ok"})
                return True
            # Non-2xx. If 5xx, retry. Otherwise, fatal.
            if 500 <= code < 600:
                log.warning(f"slack_5xx mr={mr_url} code={code} attempt={attempt}")
                if attempt < max_retries:
                    time.sleep(retry_delay * attempt)  # linear backoff
                    continue
                metrics_inc("slack_posts_total", {"status": "fail"})
                return False
            log.error(f"slack_4xx mr={mr_url} code={code} fatal")
            metrics_inc("slack_posts_total", {"status": "fail"})
            return False
        except urllib.error.HTTPError as e:
            # HTTPError carries a status code; treat like a response.
            code = e.code
            if 500 <= code < 600 and attempt < max_retries:
                log.warning(f"slack_http_error mr={mr_url} code={code} attempt={attempt}")
                time.sleep(retry_delay * attempt)
                continue
            log.error(f"slack_http_error_fatal mr={mr_url} code={code}")
            metrics_inc("slack_posts_total", {"status": "fail"})
            return False
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning(f"slack_network_error mr={mr_url} err={e} attempt={attempt}")
            if attempt < max_retries:
                time.sleep(retry_delay * attempt)
                continue
            metrics_inc("slack_posts_total", {"status": "fail"})
            return False

    return False


def handle_webhook(payload, bot_user_id, slack_webhook_url="",
                  jira_url="", jira_email="", jira_token="",
                  request_id="", admin_user_id="", headers=None):
    """Dispatch a webhook payload from GitLab or GitHub. Returns True if handled
    (or intentionally ignored). False only on hard errors.

    Provider-agnostic via T30. Jira context (T22) extracted from MR/PR title.
    Admin commands (T26) parsed from comment body if present.
    """
    provider = _detect_provider(payload, headers or {})
    if not provider:
        log.debug(f"ignoring unknown-provider payload rid={request_id}")
        return True

    # Extract provider-normalized fields
    comment = _extract_comment(payload, provider)
    pr_url = _extract_pr_url(payload, provider)
    pr_title = _extract_mr_title(payload, provider)

    # T26: admin commands — short-circuit before bot-comment check
    if comment and comment.lstrip().startswith("/bot "):
        # Ensure admin_user_id is up to date for this request
        if admin_user_id:
            _bot_state["admin_user_id"] = str(admin_user_id)
        elif os.environ.get("BOT_ADMIN_USER_ID"):
            _bot_state["admin_user_id"] = os.environ["BOT_ADMIN_USER_ID"]
        return handle_admin_command(
            comment, admin_user_id=_bot_state["admin_user_id"],
            slack_webhook_url=slack_webhook_url,
        )

    # Regular flow: only act on bot's own comments
    if provider == "gitlab" and not _is_gitlab_note_on_mr(payload):
        log.debug(f"ignoring non-MR-note event rid={request_id}")
        return True

    if not is_bot_comment(payload, bot_user_id):
        log.debug(f"ignoring non-bot comment rid={request_id}")
        return True

    # T26: respect disabled state — skip Slack but don't fail
    if not _bot_state["enabled"]:
        log.info(f"slack_skipped_disabled rid={request_id}")
        metrics_inc("slack_posts_total", {"status": "disabled"})
        return True

    if not slack_webhook_url:
        log.info(f"slack_disabled_config skipping notification rid={request_id}")
        return True

    # T22: fetch Jira context if applicable. Graceful — never raises.
    jira_context = None
    ticket_key = extract_jira_key(pr_title)
    if not ticket_key and provider == "gitlab":
        source_branch = (payload.get("merge_request") or {}).get("source_branch", "")
        ticket_key = extract_jira_key(source_branch)
    if ticket_key and jira_url and jira_email and jira_token:
        log.info(f"jira_fetch_attempt key={ticket_key} rid={request_id}")
        jira_context = fetch_jira_issue(
            ticket_key,
            jira_url=jira_url,
            email=jira_email,
            token=jira_token,
        )
        if jira_context:
            log.info(f"jira_fetched key={ticket_key} rid={request_id}")

    # Build summary with optional Jira context
    bot_summary = _extract_summary(comment)
    summary = bot_summary
    if jira_context and jira_context.get("summary"):
        summary = f"[{ticket_key}] {jira_context['summary']}\n\n{bot_summary}"

    return post_to_slack(
        url=slack_webhook_url,
        mr_url=pr_url,
        mr_title=pr_title,
        author=str(_extract_author_id(payload, provider) or "bot"),
        severity=_extract_severity(comment),
        summary=summary,
        request_id=request_id,
    )


def _is_gitlab_note_on_mr(payload):
    """True for GitLab note events on merge requests (not issues, not commits)."""
    if payload.get("object_kind") != "note":
        return False
    return (payload.get("object_attributes") or {}).get("noteable_type") == "MergeRequest"


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
        # T24: request_id for log correlation across this request.
        request_id = generate_request_id()
        self._request_id = request_id
        self.send_header("X-Request-Id", request_id)

        if self.path != "/webhook":
            metrics_inc("webhook_requests_total", {"path": self.path, "status": "404"})
            self.send_response(404)
            self.end_headers()
            return

        # Defense in depth: validate signature even when nginx is in front.
        if not verify_signature(self.headers, self.webhook_secret):
            log.warning(f"webhook_signature_invalid rid={request_id}")
            metrics_inc("webhook_requests_total", {"path": self.path, "status": "401"})
            self.send_response(401)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            log.warning(f"invalid_json rid={request_id}")
            metrics_inc("webhook_requests_total", {"path": self.path, "status": "400"})
            self.send_response(400)
            self.end_headers()
            return

        ok = handle_webhook(
            payload,
            bot_user_id=self.bot_user_id,
            slack_webhook_url=self.slack_webhook_url,
            jira_url=os.environ.get("JIRA_URL", ""),
            jira_email=os.environ.get("JIRA_EMAIL", ""),
            jira_token=os.environ.get("JIRA_TOKEN", ""),
            request_id=request_id,
            admin_user_id=os.environ.get("BOT_ADMIN_USER_ID", ""),
            headers=dict(self.headers),
        )
        metrics_inc("webhook_requests_total", {
            "path": self.path,
            "status": "200" if ok else "500",
        })
        self.send_response(200 if ok else 500)
        self.send_header("X-Request-Id", request_id)
        self.end_headers()

    def do_GET(self):
        # T24: attach request_id for log correlation.
        request_id = generate_request_id()
        self._request_id = request_id
        self.send_header("X-Request-Id", request_id)

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
            metrics_set("last_health_check_timestamp_seconds", int(time.time()))
            code = 200 if result["status"] == "healthy" else 503
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
            return
        # T21: Prometheus metrics endpoint.
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(metrics_render().encode("utf-8"))
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
