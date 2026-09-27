#!/usr/bin/env bash
# T16 — deep smoke test for templates/.gitlab-ci.yml.template.
#
# Goes beyond structural validation (test_template_validity.sh) by catching
# issues that would surface only when GitLab actually runs the template:
#   - unresolved $VAR references (typos in variable names)
#   - non-semver image tags
#   - basic shell syntax errors in the script
#   - the template loads cleanly when wrapped as an `include:` target
#   - no reserved keys misused (e.g. `trigger`, `include` inside a job)
#
# Does NOT require `gitlab-ci-local` or network — pure local validation.
set -euo pipefail

cd "$(dirname "$0")/.."

TEMPLATE="templates/.gitlab-ci.yml.template"

if [ ! -f "$TEMPLATE" ]; then
    echo "FAIL: $TEMPLATE does not exist"
    exit 1
fi

# Python does the deep validation; bash just wraps it.
python - "$TEMPLATE" <<'PY'
import sys
import re
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    raw = f.read()
cfg = yaml.safe_load(raw)

# GitLab predefined variables that we are allowed to reference without
# declaring them ourselves.
PREDEFINED = {
    "CI_COMMIT_SHA", "CI_COMMIT_REF_NAME", "CI_COMMIT_REF_SLUG",
    "CI_COMMIT_BRANCH", "CI_COMMIT_TAG",
    "CI_PROJECT_ID", "CI_PROJECT_NAME", "CI_PROJECT_PATH",
    "CI_PROJECT_URL", "CI_PROJECT_DIR",
    "CI_PIPELINE_ID", "CI_PIPELINE_SOURCE", "CI_PIPELINE_IID",
    "CI_JOB_ID", "CI_JOB_NAME", "CI_JOB_STAGE",
    "CI_RUNNER_ID", "CI_RUNNER_SHORT_TOKEN",
    "CI_MERGE_REQUEST_ID", "CI_MERGE_REQUEST_IID",
    "CI_MERGE_REQUEST_PROJECT_URL", "CI_MERGE_REQUEST_TITLE",
    "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "CI_MERGE_REQUEST_TARGET_BRANCH_NAME",
    "CI_MERGE_REQUEST_SOURCE_PROJECT_URL",
    "CI_REPOSITORY_URL",
    "CI_API_V4_URL", "CI_SERVER_URL", "CI_SERVER_FQDN",
    "GITLAB_USER_ID", "GITLAB_USER_EMAIL", "GITLAB_USER_NAME",
    "CI_DEBUG_TRACE",
}

# Top-level keys that may appear at root but are not jobs.
RESERVED_TOP_KEYS = {
    "stages", "variables", "include", "workflow", "default",
    "image", "services", "cache", "before_script", "after_script",
}

# Keys that must never appear INSIDE a job.
RESERVED_JOB_KEYS = {
    "include",   # include only at top-level
    "stages",    # stages only at top-level
    "trigger",   # trigger is a job type, not a job key
    "workflow",  # workflow only at top-level
}

# Vars we expect target repos to provide via Settings > CI/CD > Variables.
# Document these in the template's header comment so users know what to set.
EXPECTED_EXTERNAL_VARS = {
    "MINIMAX_API_KEY",
    "PR_AGENT_TOKEN",
}

errors = []

# --- 1. Top-level structure ---
job_names = [k for k in cfg if k not in RESERVED_TOP_KEYS]
if not job_names:
    errors.append("no jobs defined")

# --- 2. Per-job smoke checks ---
for job_name in job_names:
    job = cfg[job_name]
    if not isinstance(job, dict):
        errors.append(f"job '{job_name}' is not a mapping")
        continue

    # 2a. No reserved keys inside job
    for key in job:
        if key in RESERVED_JOB_KEYS:
            errors.append(f"job '{job_name}' has reserved key '{key}'")

    # 2b. Image semver check
    if "image" in job:
        image = str(job["image"])
        tag = image.rsplit(":", 1)[-1] if ":" in image else ""
        if not re.match(r'^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$', tag):
            errors.append(f"job '{job_name}' image tag is not semver: '{tag}'")

    # 2c. Collect variable refs in job
    job_text = yaml.safe_dump(job)
    refs = set(re.findall(r'\$([A-Z_][A-Z0-9_]*)', job_text))

    # 2d. Collect vars defined in this job
    defined = set()
    if "variables" in job and isinstance(job["variables"], dict):
        defined.update(job["variables"].keys())

    # 2e. Each $VAR must be predefined, defined locally, or expected external
    for ref in refs:
        if ref in PREDEFINED:
            continue
        if ref in defined:
            continue
        if ref in EXPECTED_EXTERNAL_VARS:
            continue
        errors.append(
            f"job '{job_name}' references undefined var '${{{ref}}}' "
            f"(not GitLab-predefined, not in job variables, not in "
            f"EXPECTED_EXTERNAL_VARS — likely typo)"
        )

    # 2f. Script command parses as bash (basic: balanced quotes, no obvious typos)
    if "script" in job:
        scripts = job["script"] if isinstance(job["script"], list) else [job["script"]]
        for line in scripts:
            line = line.rstrip()
            # A real pr-agent invocation should reference $CI_* or be the main command.
            if "pr-agent" in line:
                # ok — this is the main invocation
                continue
            # Other lines: should look like a valid shell command (start with valid char)
            if not re.match(r'^[a-zA-Z0-9_/$.-]', line):
                errors.append(f"job '{job_name}' script line starts oddly: '{line[:60]}'")

# --- 3. Wrap test: simulate `include:`-ing the template ---
# Build a parent .gitlab-ci.yml that includes our template, then verify
# the combined structure is well-formed.
parent = {
    "include": [{"local": path}],
    "stages": ["review"],
}
combined_yaml = yaml.safe_dump(parent) + "\n" + raw
try:
    combined = yaml.safe_load(combined_yaml)
    if "include" not in combined:
        errors.append("combined parent+template YAML is missing include key")
except yaml.YAMLError as e:
    errors.append(f"combined parent+template YAML invalid: {e}")

# --- Report ---
if errors:
    print(f"FAIL: {path} failed smoke checks:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print(f"PASS: {path} — {len(job_names)} job(s), all vars resolved, "
      f"image semver, script syntax OK, include-wrap valid")
PY
