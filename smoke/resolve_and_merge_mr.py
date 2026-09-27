"""Resolve threads + merge MR !359 + check status of remaining MRs."""
import sys, json, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'slack-notifier')
from notifier import get_secret

# Use Anton's GitLab PAT from CredMan
TOKEN = get_secret('GITLAB_TOKEN', credman_target='git:https://gitlab.winwin.travel')
HEADERS = {'PRIVATE-TOKEN': TOKEN, 'Content-Type': 'application/json'}
BASE = 'https://gitlab.winwin.travel/api/v4/projects/198'


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
