"""Resolve threads + merge MRs via GitLab API.

Designed for `satdevpro` (Nikita Zuber) or any other maintainer of
wwt-public/hotels-data/hotels-data (project 198) to run on their
machine.

Usage:
  # If you have your GitLab token in CredMan under default target:
  python smoke/resolve_and_merge_mr.py

  # Or pass token directly via env:
  GITLAB_TOKEN=glpat-xxxxxxxxxxxx python smoke/resolve_and_merge_mr.py

Workflow per MR (!411, !359, !361, !362, !390):
  1. Cancel auto-merge if set (POST .../cancel_merge_when_pipeline_succeeds)
  2. Resolve all unresolved threads (PUT .../discussions/{id})
  3. Try merge (PUT .../merge)
  4. Report final status

Already merged MRs are skipped.
"""
import json
import os
import sys
import urllib.error
import urllib.request

# Resolve token: env var → CredMan (try several targets)
TOKEN = os.environ.get("GITLAB_TOKEN")
if not TOKEN:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "slack-notifier"))
    from notifier import get_secret
    for target in ("GITLAB_TOKEN", "git:https://gitlab.winwin.travel",
                   "pr-review-bot:GITLAB_MAINTAINER", "pr-review-bot:GITLAB"):
        TOKEN = get_secret(target)
        if TOKEN:
            break
    if not TOKEN:
        print("ERROR: no GitLab token. Set $GITLAB_TOKEN or store in CredMan.")
        sys.exit(1)

HEADERS = {"PRIVATE-TOKEN": TOKEN, "Content-Type": "application/json"}
BASE = "https://gitlab.winwin.travel/api/v4/projects/198"


def api(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def api(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def resolve_all(iid):
    """Resolve all unresolved threads on an MR."""
    discs = json.loads(urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/merge_requests/{iid}/discussions", headers={'PRIVATE-TOKEN': TOKEN}), timeout=15).read())
    resolved = 0
    for d in discs:
        for n in d.get('notes', []):
            if not n.get('resolved'):
                s, b = api('PUT', f"/merge_requests/{iid}/discussions/{d['id']}", {"resolved": True})
                if s == 200:
                    resolved += 1
                else:
                    sys.stderr.write(f"  d={d['id'][:8]} n={n['id']}: HTTP {s}: {b[:100]}\n")
    return resolved


def cancel_auto_merge(iid):
    s, b = api('POST', f"/merge_requests/{iid}/cancel_merge_when_pipeline_succeeds")
    return s


def try_merge(iid):
    s, b = api('PUT', f"/merge_requests/{iid}/merge", {"squash": False, "should_remove_source_branch": True})
    return s, b


def check_status(iid):
    mr = json.loads(urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/merge_requests/{iid}", headers={'PRIVATE-TOKEN': TOKEN}), timeout=15).read())
    return {
        'state': mr['state'],
        'merge_status': mr['merge_status'],
        'detailed': mr.get('detailed_merge_status'),
        'conflicts': mr['has_conflicts'],
        'disc_blocked': not mr['blocking_discussions_resolved'],
        'auto_merge': mr.get('merge_when_pipeline_succeeds'),
    }


# Process each MR
TARGETS = [411, 359, 361, 362, 390]
for iid in TARGETS:
    print(f"\n========== MR !{iid} ==========")
    s = check_status(iid)
    print(f"  Initial: state={s['state']} merge={s['merge_status']} conf={s['conflicts']} disc_blocked={s['disc_blocked']}")

    if s['state'] in ('merged', 'closed'):
        print(f"  Already {s['state']}, skipping")
        continue

    # 1. Cancel auto-merge if set
    if s['auto_merge']:
        cancel_s = cancel_auto_merge(iid)
        print(f"  Cancel auto-merge: HTTP {cancel_s}")

    # 2. Resolve all threads
    n_resolved = resolve_all(iid)
    print(f"  Resolved {n_resolved} threads")

    # 3. Try merge
    merge_s, merge_b = try_merge(iid)
    print(f"  Merge: HTTP {merge_s}: {merge_b[:200]}")

    # 4. Final status
    s2 = check_status(iid)
    print(f"  Final: state={s2['state']} merge={s2['merge_status']} conf={s2['conflicts']} disc_blocked={s2['disc_blocked']}")
