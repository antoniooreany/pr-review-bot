# Sandbox setup — demo before rollout

This is the 30-minute procedure for running the bot against a **sandbox
repo** before asking for team-wide rollout. Goal: produce a real MR with
real bot comments to show to the reviewer (Vitalii), without touching
any production repo.

## Why a sandbox

- Real demo beats a description.
- Zero risk to hotels-data MRs — webhook only fires on the sandbox repo.
- Reversible in 2 minutes — `docker compose down` and the sandbox webhook.
- Establishes a precedent: every new repo can be onboarded the same way.

## Prerequisites

| Requirement | Why |
|---|---|
| Docker + Docker Compose | Run the bot container locally |
| A sandbox repo you own | Where the demo MR lives |
| `ngrok` or similar tunnel | Expose your `localhost:3000` to GitLab |
| Service account GitLab user | The bot posts comments as this identity |
| ~30 minutes | Full setup to first bot comment |

## Step 1 — Create the sandbox repo (5 min)

On `gitlab.winwin.travel`, in your personal namespace (not the hotels-data
group):

1. New project → blank → name `pr-review-bot-demo`
2. Visibility: Private (you'll invite Vitalii manually)
3. Add a `README.md` and one or two small Java/Spring files so there's
   something to review. Recommended: copy 50-100 lines from a real
   hotels-data file, anonymized.

**Don't use your `sync` repo or any production project.** Demo stays
isolated.

## Step 2 — Start the bot locally (2 min)

```bash
cd pr-review-bot
cp .env.example .env
# Edit .env:
#   OPENAI_KEY      — from https://platform.minimax.io/
#   GITLAB_TOKEN    — service account PAT (next step)
#   GITLAB_URL      — https://gitlab.winwin.travel
#   WEBHOOK_SECRET  — openssl rand -hex 32 (save this for Step 5)
#   OPENAI_BASE_URL — https://api.minimax.io/v1

docker compose up -d
docker compose ps    # confirm "healthy"
curl -fsS http://localhost:3000/health
```

## Step 3 — Create the service account (5 min)

The bot posts comments as this identity, not as you.

1. In GitLab → Admin → Users → New user
   - Name: `pr-review-bot`
   - Username: `pr-review-bot`
   - Email: `pr-review-bot@winwin.travel` (or a shared mailbox)
   - Access level: Regular (deactivate after demo if not rolling out)
2. Log in as this user, create a Personal Access Token:
   - Name: `pr-review-bot-token`
   - Scopes: **`api`** only
   - Expiration: 90 days
3. Copy the token into `GITLAB_TOKEN` in your `.env`
4. Add the bot user to the sandbox repo as **Maintainer**

**For demo only:** you can use your own PAT instead of creating a service
account. Don't ship this to production.

## Step 4 — Expose to GitLab via tunnel (2 min)

GitLab needs to reach your container. If your laptop isn't publicly
reachable, use a tunnel.

```bash
# Option A: ngrok (free tier is enough for demo)
ngrok http 3000
# Note the https://<random>.ngrok-free.app URL

# Option B: ssh reverse tunnel to a public VM
ssh -R 3000:localhost:3000 your-public-vm

# Option C: deploy to a small cloud VM temporarily
# (Hetzner/DO free tier, ~$0)
```

The URL you give to GitLab must be `https://` — GitLab rejects http
webhooks by default.

## Step 5 — Configure the webhook (3 min)

In the sandbox repo → Settings → Webhooks → Add new:

| Field | Value |
|---|---|
| URL | `https://<your-tunnel-url>/<pr-agent-path>` |
| Trigger | ☑ Merge request events, ☑ Comments |
| SSL verification | Enable (use ngrok's TLS) |
| Secret | Same value as `WEBHOOK_SECRET` in `.env` |

The exact path that PR-Agent listens on depends on its version. Check
container logs after `docker compose up`:

```bash
docker compose logs pr-agent | grep -i "listening\|webhook"
```

Common: `/webhook`, `/api/v1/webhook`, `/`. Use whatever it says.

## Step 6 — Open a test MR (5 min)

On a new branch:

```bash
git checkout -b hd-demo-mr-1
# Make a small change that triggers typical bot comments:
#  - Add a method without a unit test
#  - Use @Transactional on a private method (silent Spring failure)
#  - Hardcode a value (URL, threshold) instead of @Value
#  - Use field @Autowired instead of constructor injection
git commit -m "HD-demo-1: small refactor"
git push -u origin hd-demo-mr-1
```

Open MR in GitLab. Bot should comment within 1-2 minutes.

## Step 7 — Demo to the reviewer

Three options:

**A. Screen share.** Open the MR, walk through the bot's comments. Point
out which are useful, which are noise. Show that the bot did NOT block
the merge or touch CI/CD.

**B. Async video.** Record a 3-minute Loom walking through the MR. Send
the link in Slack.

**C. Written summary.** Screenshot the MR with comments, attach to Slack
message. Add: "Comments are advisory, merge rules unchanged. Want me to
roll out to [specific repo] next?"

## Step 8 — Decision & cleanup

After Vitalii's response:

- **Approved →** discuss rollout scope (which repos, Mode A or B).
  Service account can stay; add it as Maintainer to the real repos.
- **Not now →** keep the sandbox repo for future demos. Container can
  stay running or be stopped. No impact on hotels-data.
- **No →** `docker compose down`, delete sandbox repo. No traces.

## Cost of demo

~10 MRs × 6K input + 1.5K output tokens = ~75K input + ~15K output.
On MiniMax-M3: **~$0.05 total**. Negligible.

## Security during demo

The bot runs on your machine. While the container is up:

- It can read every MR diff that fires the webhook (sandbox only here).
- Logs go to a Docker volume — `docker compose logs pr-agent` to inspect.
- Stop the container when done — nothing persists beyond that.

**Don't run this on a corporate laptop with sensitive code reachable from
the bot's token scope.** The demo PAT has `api` scope and could in theory
read any repo the service account has access to. Use a personal laptop
or a clean VM if the demo runs in a corporate environment.
