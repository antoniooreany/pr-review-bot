# Production deploy

Single-host deployment for pr-review-bot. Target: Ubuntu 22.04+ or
similar systemd-based Linux distribution.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Host: Linux VM                                              │
│                                                              │
│  systemd: pr-review-bot.service                              │
│    └─► docker compose -f docker-compose.yml \               │
│              -f deploy/docker-compose.prod.yml up -d        │
│                                                              │
│  ┌────────────┐  ┌──────────────────┐  ┌─────────────┐     │
│  │  pr-agent  │  │  slack-notifier   │  │   nginx     │     │
│  │  :3000     │  │  :3001            │  │   :443      │ ◄─ HTTPS
│  └────────────┘  └──────────────────┘  └─────────────┘     │
│        ▲             ▲                    ▲                │
│        └─────────────┴────────────────────┘                │
│           pr-review-bot-network (internal)                  │
└─────────────────────────────────────────────────────────────┘
```

All bot services communicate over an internal Docker network. Only nginx
exposes port 443 (HTTPS) to the host. pr-agent and slack-notifier are
NOT reachable from outside the Docker network.

## Prerequisites

- Linux VM with systemd (Ubuntu 22.04+, Debian 12+, etc.)
- Docker Engine 24+ with Compose v2
- TLS certificate + private key (Let's Encrypt via certbot, or your
  own PKI). Mount at `nginx/certs/cert.pem` and `nginx/private/key.pem`
  on the host.
- Outbound HTTPS to:
  - `gitlab.winwin.travel` (for webhooks + Jira)
  - `api.minimax.io` (LLM API)
  - `hooks.slack.com` (Slack webhook)
  - Optional: `api.github.com` (GitHub webhooks if used)
- DNS record pointing at the host (e.g., `pr-review-bot.winwin.travel`)

## Install

### Option A — Ansible (recommended)

```bash
ansible-playbook -i localhost, -c local deploy/ansible/playbook.yml \
  --extra-vars "bot_version=v1.4.0"
```

This will:
1. Install Docker + Compose (if missing)
2. Create system user `prreview`
3. Clone repo to `/opt/pr-review-bot`
4. Install systemd unit
5. Enable + start the service

### Option B — Manual

```bash
# As root (or sudo)
useradd -r -s /usr/sbin/nologin -d /opt/pr-review-bot prreview
mkdir -p /opt/pr-review-bot /etc/pr-review-bot
chown prreview:prreview /opt/pr-review-bot
chmod 0700 /etc/pr-review-bot

git clone https://github.com/pr-review-bot/pr-review-bot.git /opt/pr-review-bot
cd /opt/pr-review-bot
git checkout v1.4.0

cp deploy/systemd/pr-review-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pr-review-bot.service
```

## Configure

### 1. Create secrets file

```bash
sudo tee /etc/pr-review-bot/secrets.env > /dev/null <<EOF
# MiniMax API key (required)
OPENAI_KEY=your-minimax-api-key
OPENAI_BASE_URL=https://api.minimax.io/v1

# GitLab bot account PAT (required, scope: api)
GITLAB_TOKEN=glpat-xxxxxxxxxxxx

# Slack incoming webhook (optional, for notifications)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz

# GitHub bot token (optional, if using GitHub)
GITHUB_BOT_TOKEN=ghp_xxxxxxxxxxxx

# Jira (optional, for HD-NNN context)
JIRA_URL=https://jira.winwin.travel
JIRA_EMAIL=bot@winwin.travel
JIRA_TOKEN=your-jira-pat

# Webhook secret (required) — generate via: openssl rand -hex 32
WEBHOOK_SECRET=$(openssl rand -hex 32)

# Admin user ID (required for /bot admin commands)
BOT_ADMIN_USER_ID=42

# GitLab URL
GITLAB_URL=https://gitlab.winwin.travel
EOF

sudo chmod 0600 /etc/pr-review-bot/secrets.env
sudo chown root:root /etc/pr-review-bot/secrets.env
```

**Alternative**: store these in Windows Credential Manager via T29
`get_secret()` — if the host runs Windows instead.

### 2. Provision TLS certificate

```bash
# Let's Encrypt example (requires port 80 reachable)
sudo certbot certonly --standalone -d pr-review-bot.winwin.travel
sudo cp /etc/letsencrypt/live/pr-review-bot.winwin.travel/fullchain.pem \
        /opt/pr-review-bot/nginx/certs/cert.pem
sudo cp /etc/letsencrypt/live/pr-review-bot.winwin.travel/privkey.pem \
        /opt/pr-review-bot/nginx/private/key.pem
```

For self-signed (dev only):
```bash
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout nginx/private/key.pem -out nginx/certs/cert.pem \
  -days 365 -subj "/CN=pr-review-bot.local"
```

### 3. Start

```bash
sudo systemctl restart pr-review-bot.service
sudo journalctl -u pr-review-bot -f   # watch logs
```

## Configure GitLab webhook

In `gitlab.winwin.travel`:
1. Go to your project (or group) → Settings → Webhooks
2. URL: `https://pr-review-bot.winwin.travel/<pr-agent-webhook-path>`
3. Trigger: **Merge request events** + **Comments**
4. Secret token: same as `WEBHOOK_SECRET` in `/etc/pr-review-bot/secrets.env`
5. SSL: enabled (cert is mounted)

## Configure Slack

1. Create incoming webhook at https://api.slack.com/messaging/webhooks
2. Copy URL to `SLACK_WEBHOOK_URL` in secrets.env
3. Set `GITLAB_BOT_USER_ID` to the numeric ID of the bot account
   (find in GitLab → User profile → API)

## Health checks

```bash
# Liveness
curl -k https://pr-review-bot.winwin.travel/health

# Readiness (probes GitLab + MiniMax)
curl -k https://pr-review-bot.winwin.travel/health/deep

# Prometheus metrics
curl -k https://pr-review-bot.winwin.travel/metrics
```

The systemd unit restarts the service on failure. Logs are in
journald — `journalctl -u pr-review-bot`.

## Upgrade

```bash
# Stop, update code, restart
sudo systemctl stop pr-review-bot.service
cd /opt/pr-review-bot && sudo -u prreview git pull
sudo -u prreview git checkout v1.5.0  # or whatever new tag
sudo systemctl start pr-review-bot.service
```

Watch logs after upgrade:
```bash
sudo journalctl -u pr-review-bot -f
```

## Rotate secrets

Secrets file at `/etc/pr-review-bot/secrets.env`. To rotate (e.g., new
GitHub token):

```bash
sudo nano /etc/pr-review-bot/secrets.env
sudo systemctl restart pr-review-bot.service
```

The systemd unit has `EnvironmentFile=-` — restart picks up new values.

## Rollback

```bash
sudo systemctl stop pr-review-bot.service
cd /opt/pr-review-bot && sudo -u prreview git checkout v1.3.2   # previous version
sudo systemctl start pr-review-bot.service
```

## What this bot does NOT do

Per Constitution §6 + AI Usage Standards (Ownership Principle):
- ❌ Auto-apply patches
- ❌ Auto-merge MRs
- ❌ Push code to target repositories

The bot posts a review and notifies. The author or reviewer decides
what to do with the feedback.

## Troubleshooting

**Container unhealthy.** `sudo journalctl -u pr-review-bot -n 100`.

**Webhook signature invalid.** Verify GitLab webhook "Secret token"
exactly matches `WEBHOOK_SECRET` in secrets.env (whitespace matters).

**Slack notifications missing.** Check `SLACK_WEBHOOK_URL` is set.
Check `GITLAB_BOT_USER_ID` matches the bot account (not a human).

**No comments on MR.** Check GitLab webhook is firing (Settings →
Webhooks → Recent deliveries). Check nginx logs: `docker compose logs nginx`.

**/health/deep returns 503.** Either GitLab or MiniMax unreachable.
Check `GITLAB_URL` and `OPENAI_BASE_URL` env vars.
