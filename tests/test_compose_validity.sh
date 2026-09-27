#!/usr/bin/env bash
# T2 — assert docker-compose.yml is syntactically valid.
# docker compose config exits non-zero on parse/syntax errors.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f docker-compose.yml ]; then
    echo "FAIL: docker-compose.yml does not exist"
    exit 1
fi

# docker compose refuses to validate when env_file points at a missing .env.
# For static validation we don't need real secrets — substitute with the
# example file (parseable values, no leakage). At runtime, the operator
# supplies a real .env. .env is gitignored, so creating it here is safe.
CREATED_ENV=0
if [ ! -f .env ]; then
    cp .env.example .env
    CREATED_ENV=1
fi
if [ "$CREATED_ENV" -eq 1 ]; then
    trap 'rm -f .env' EXIT
fi

if docker compose config --quiet 2>/dev/null; then
    echo "PASS: docker-compose.yml is valid"
    exit 0
else
    echo "FAIL: docker compose config reported errors:"
    docker compose config 2>&1 | sed 's/^/    /'
    exit 1
fi
