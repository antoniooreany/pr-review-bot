#!/usr/bin/env bash
# T27 — validate deploy automation files.
# Asserts systemd unit + ansible playbook + docker-compose.prod.yml
# have required structure. Static checks (no systemd / ansible tools needed).
set -euo pipefail

cd "$(dirname "$0")/.."

FAIL=0

# ── systemd unit file ─────────────────────────────────────────────────────
SYSTEMD_UNIT="deploy/systemd/pr-review-bot.service"
if [ ! -f "$SYSTEMD_UNIT" ]; then
    echo "FAIL: $SYSTEMD_UNIT missing"
    exit 1
fi

REQUIRED_SYSTEMD_DIRECTIVES=(
    "[Unit]"
    "[Service]"
    "[Install]"
    "ExecStart"
    "WorkingDirectory"
    "Restart=on-failure"
    "EnvironmentFile"
)

for directive in "${REQUIRED_SYSTEMD_DIRECTIVES[@]}"; do
    if ! grep -qF "$directive" "$SYSTEMD_UNIT"; then
        echo "FAIL: systemd unit missing directive: '$directive'"
        FAIL=1
    fi
done

# Must run as non-root (security hardening)
if ! grep -qE "User=|NoNewPrivileges=yes" "$SYSTEMD_UNIT"; then
    echo "FAIL: systemd unit missing User= or NoNewPrivileges=yes"
    FAIL=1
fi

# ── ansible playbook ─────────────────────────────────────────────────────
PLAYBOOK="deploy/ansible/playbook.yml"
if [ ! -f "$PLAYBOOK" ]; then
    echo "FAIL: $PLAYBOOK missing"
    FAIL=1
else
    # yamllint or basic structural check
    python -c "import yaml; yaml.safe_load(open('$PLAYBOOK'))" 2>/dev/null || {
        echo "FAIL: $PLAYBOOK is invalid YAML"
        FAIL=1
    }
    # Required tasks
    for task in "Install system dependencies" "Clone or update bot repo" \
               "Enable and start bot service" "Copy systemd unit file"; do
        if ! grep -qF -- "- name: $task" "$PLAYBOOK"; then
            echo "FAIL: ansible playbook missing task '$task'"
            FAIL=1
        fi
    done
fi

# ── docker-compose.prod.yml ───────────────────────────────────────────────
PROD_COMPOSE="deploy/docker-compose.prod.yml"
if [ ! -f "$PROD_COMPOSE" ]; then
    echo "FAIL: $PROD_COMPOSE missing"
    FAIL=1
else
    python -c "import yaml; list(yaml.safe_load_all(open('$PROD_COMPOSE')))" 2>/dev/null || {
        echo "FAIL: $PROD_COMPOSE is invalid YAML"
        FAIL=1
    }
    # Production compose must use restart: unless-stopped for all services
    if ! grep -q "restart: unless-stopped" "$PROD_COMPOSE"; then
        echo "FAIL: prod compose missing 'restart: unless-stopped'"
        FAIL=1
    fi
    # Must NOT bind pr-agent/slack-notifier to host ports
    # (only nginx exposes ports externally)
    if grep -qE "^[[:space:]]+ports:[[:space:]]*-\s*\"127\.0\.0\.1:3000" "$PROD_COMPOSE"; then
        echo "FAIL: pr-agent port exposed to host in prod (security)"
        FAIL=1
    fi
fi

# ── README ───────────────────────────────────────────────────────────────
README="deploy/README.md"
if [ ! -f "$README" ]; then
    echo "FAIL: $README missing"
    FAIL=1
else
    # Required sections
    for section in "Architecture" "Prerequisites" "Install" "Configure" \
                   "What this bot does NOT do" "Troubleshooting"; do
        if ! grep -qF "## $section" "$README"; then
            echo "FAIL: deploy/README.md missing section '## $section'"
            FAIL=1
        fi
    done
    # Must explicitly mention review-only constraint
    if ! grep -qi "review.*only\|bot.*review" "$README"; then
        echo "FAIL: deploy/README.md must mention review-only constraint"
        FAIL=1
    fi
fi

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
echo "PASS: deploy automation files valid"
