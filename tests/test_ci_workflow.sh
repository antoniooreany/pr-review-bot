#!/usr/bin/env bash
# Validate .github/workflows/ci.yml has required T25 self-test job.
set -euo pipefail

cd "$(dirname "$0")/.."

WORKFLOW=".github/workflows/ci.yml"

if [ ! -f "$WORKFLOW" ]; then
    echo "FAIL: $WORKFLOW missing"
    exit 1
fi

# Use Python yaml for parsing
python - "$WORKFLOW" <<'PY'
import sys
import yaml

with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f)

# Must have at least 2 jobs: test + e2e-smoke (T25)
jobs = cfg.get("jobs", {})
if len(jobs) < 2:
    print(f"FAIL: expected at least 2 jobs (test + e2e-smoke), found {len(jobs)}: {list(jobs.keys())}")
    sys.exit(1)

# Must have e2e-smoke job
if "e2e-smoke" not in jobs:
    print(f"FAIL: missing e2e-smoke job (T25 self-test). Have: {list(jobs.keys())}")
    sys.exit(1)

e2e = jobs["e2e-smoke"]
steps = e2e.get("steps", [])

# Must run e2e_smoke.py
found_smoke = False
for s in steps:
    if "e2e_smoke" in s.get("run", "") or "e2e_smoke" in s.get("name", ""):
        found_smoke = True
        break
if not found_smoke:
    print(f"FAIL: e2e-smoke job doesn't run e2e_smoke.py")
    print(f"  steps: {[s.get('name','?') for s in steps]}")
    sys.exit(1)

# Must run on ubuntu (real binary test, not mock)
if e2e.get("runs-on") != "ubuntu-latest":
    print(f"WARN: e2e-smoke runs on {e2e.get('runs-on')}, expected ubuntu-latest")

print(f"PASS: ci.yml has {len(jobs)} jobs including e2e-smoke")
print(f"  jobs: {list(jobs.keys())}")
PY
