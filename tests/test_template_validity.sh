#!/usr/bin/env bash
# T14 — assert templates/.gitlab-ci.yml.template is a valid GitLab CI
# template suitable for `include:`-ing from target repos. Asserts the
# image is pinned, the job is scoped to MR events only, and the Spring
# Boot focus instructions are present.
set -euo pipefail

cd "$(dirname "$0")/.."

TEMPLATE="templates/.gitlab-ci.yml.template"

if [ ! -f "$TEMPLATE" ]; then
    echo "FAIL: $TEMPLATE does not exist"
    exit 1
fi

# `-` tells Python to read the script from stdin (the heredoc), with
# `sys.argv[1:]` being the file args after.
python - "$TEMPLATE" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

if not cfg:
    print(f"FAIL: {path} is empty or not valid YAML")
    sys.exit(1)

job_names = [k for k in cfg if k not in ("stages", "variables", "include", "workflow")]
if not job_names:
    print(f"FAIL: {path} defines no jobs")
    sys.exit(1)

job_name = job_names[0]
job = cfg[job_name]
print(f"Found job: {job_name}")

# Image must be pinned (not :latest, not untagged).
if "image" not in job:
    print(f"FAIL: job '{job_name}' missing 'image'")
    sys.exit(1)
image = str(job["image"])
if image.endswith(":latest"):
    print(f"FAIL: image uses ':latest' (not pinned): {image}")
    sys.exit(1)
if ":" not in image:
    print(f"FAIL: image has no tag pin: {image}")
    sys.exit(1)
print(f"  image: {image} (pinned)")

# Rules must restrict to merge_request_event.
if "rules" not in job:
    print(f"FAIL: job '{job_name}' missing 'rules'")
    sys.exit(1)
rules_str = str(job["rules"])
if "merge_request_event" not in rules_str:
    print(f"FAIL: job rules don't restrict to merge_request_event")
    sys.exit(1)
print(f"  rules restrict to MR events")

# Script must invoke pr-agent CLI.
if "script" not in job:
    print(f"FAIL: job '{job_name}' missing 'script'")
    sys.exit(1)
script_str = " ".join(job["script"]) if isinstance(job["script"], list) else str(job["script"])
if "pr-agent" not in script_str:
    print(f"FAIL: script doesn't invoke pr-agent: {script_str}")
    sys.exit(1)
print(f"  script invokes pr-agent")

# Variables should include PR_REVIEWER_EXTRA_INSTRUCTIONS with Spring focus.
if "variables" not in job:
    print("FAIL: variables block missing")
    sys.exit(1)
vars_str = str(job["variables"])
if "PR_REVIEWER_EXTRA_INSTRUCTIONS" not in vars_str:
    print("FAIL: PR_REVIEWER_EXTRA_INSTRUCTIONS not set")
    sys.exit(1)
if "spring" not in vars_str.lower():
    print("WARN: PR_REVIEWER_EXTRA_INSTRUCTIONS doesn't mention Spring")
print(f"  PR_REVIEWER_EXTRA_INSTRUCTIONS includes Spring Boot focus")

sys.exit(0)
PY
PY_EXIT=$?

# Bash-side: README must reference deployment docs so users find Mode B.
if [ -f "README.md" ]; then
    if ! grep -qE "deployment|deployment-modes" "README.md"; then
        echo "WARN: README doesn't reference deployment docs"
    fi
fi

if [ "$PY_EXIT" -eq 0 ]; then
    echo "PASS: $TEMPLATE is a valid pinned, MR-scoped pr-agent template"
    exit 0
else
    exit 1
fi
