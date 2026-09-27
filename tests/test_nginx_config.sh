#!/usr/bin/env bash
# T19 — structural validation of nginx/nginx.conf.
#
# Asserts the config has the directives required for our security model:
#   - TLS termination on 443
#   - Rate limit zones for both services
#   - Path-based routing to pr-agent and slack-notifier
#   - Standard reverse-proxy headers
#
# If nginx binary is installed, also runs `nginx -t` for syntax check.
set -euo pipefail

cd "$(dirname "$0")/.."

NGINX_CONF="nginx/nginx.conf"

if [ ! -f "$NGINX_CONF" ]; then
    echo "FAIL: $NGINX_CONF does not exist"
    exit 1
fi

FAIL=0

# Required directives.
REQUIRED_DIRECTIVES=(
    "listen 443 ssl"
    "ssl_certificate"
    "ssl_certificate_key"
    "limit_req_zone"
    "proxy_pass"
)

for directive in "${REQUIRED_DIRECTIVES[@]}"; do
    if ! grep -qF "$directive" "$NGINX_CONF"; then
        echo "FAIL: missing required directive: '$directive'"
        FAIL=1
    fi
done

# Rate limit zones for both services.
if ! grep -qE "limit_req_zone.*pr[_-]?agent" -i "$NGINX_CONF"; then
    echo "FAIL: no rate limit zone for pr-agent"
    FAIL=1
fi
if ! grep -qE "limit_req_zone.*slack" -i "$NGINX_CONF"; then
    echo "FAIL: no rate limit zone for slack-notifier"
    FAIL=1
fi

# Path-based routing.
if ! grep -qE "location.*/agent" "$NGINX_CONF"; then
    echo "FAIL: no /agent/ location block"
    FAIL=1
fi
if ! grep -qE "location.*/slack" "$NGINX_CONF"; then
    echo "FAIL: no /slack/ location block"
    FAIL=1
fi

# Standard reverse-proxy headers.
if ! grep -qF "X-Forwarded-For" "$NGINX_CONF"; then
    echo "FAIL: missing X-Forwarded-For header forwarding"
    FAIL=1
fi

# If nginx is installed, run actual syntax check.
if command -v nginx >/dev/null 2>&1; then
    echo "  nginx binary found, running syntax check..."
    # nginx -t requires a pid file location; use -p to override prefix
    if nginx -t -c "$(pwd)/$NGINX_CONF" -p "$(pwd)/nginx/" 2>&1 | grep -q "syntax is ok"; then
        echo "  nginx -t: syntax OK"
    else
        echo "FAIL: nginx -t reported syntax errors:"
        nginx -t -c "$(pwd)/$NGINX_CONF" -p "$(pwd)/nginx/" 2>&1 | sed 's/^/    /'
        FAIL=1
    fi
else
    echo "  nginx binary not available — structural checks only"
fi

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
echo "PASS: $NGINX_CONF has all required directives"
exit 0
