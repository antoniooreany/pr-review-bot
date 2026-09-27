# Deployment modes

pr-review-bot supports two deployment modes for adding AI code review to
GitLab merge requests. **Both modes read the same `review_guidelines.md`**
and `configuration.toml` — the difference is how the bot is triggered.

## Mode A — Group-level webhook (default, no CI/CD changes)

A single Docker container runs the bot in webhook mode. GitLab sends MR
events to it via a webhook URL configured at the group level.

**Files involved:**
- `docker-compose.yml` — service definition
- `configuration.toml` — bot behavior
- `review_guidelines.md` — review rules
- `.env` — secrets (gitignored)

**Setup:**
1. Deploy the container (single VM, k8s, or anywhere reachable from GitLab).
2. In GitLab: `Group → Settings → Webhooks → Add webhook`
3. URL: `https://<your-host>:3000/<pr-agent-path>` (check container logs
   for exact path)
4. Trigger: **Merge request events** + **Comments**
5. Secret: same value as `WEBHOOK_SECRET` in `.env`
6. SSL: enabled

**Trigger flow:**

```
MR event → GitLab webhook → our container → PR-Agent → LLM → inline comments via API
```

**Pros:**
- One webhook covers all repos in the group
- Slash commands work (`/review`, `/improve`, `/ask`)
- No changes needed in any target repo's CI/CD

**Cons:**
- Public endpoint = attack surface (mitigate: nginx + rate limit + secret)
- Single container = single point of failure (mitigate: `restart: unless-stopped`)
- Reverse proxy / TLS / firewall = ops burden
- Webhook secret rotation is a two-place operation

---

## Mode B — GitLab CI pipeline job (per-repo opt-in)

Target repositories add a single `include:` line to their `.gitlab-ci.yml`.
The bot runs as a per-MR CI job on shared GitLab runners.

**Files involved:**
- `templates/.gitlab-ci.yml.template` — the pipeline template
- `configuration.toml` and `review_guidelines.md` — NOT used directly by
  Mode B (the template embeds the rules inline via `PR_REVIEWER_EXTRA_INSTRUCTIONS`).
  We keep them in sync manually for now; a future task could auto-generate
  the env var from `review_guidelines.md`.

**Setup (in target repo, requires repo owner or Vitalii):**

1. Add to `.gitlab-ci.yml`:
   ```yaml
   include:
     - remote: 'https://raw.githubusercontent.com/pr-review-bot/pr-review-bot/v0.2.0-dual-mode/templates/.gitlab-ci.yml.template'
   ```
2. In Settings → CI/CD → Variables, add (masked + protected):
   - `MINIMAX_API_KEY`
   - `PR_AGENT_TOKEN` (GitLab PAT with `api` scope)

**Trigger flow:**

```
MR event → GitLab CI pipeline → pr-agent review (CLI in container) → inline comments via API
```

**Pros:**
- Shared GitLab runners — no infra to maintain
- Bot runs in GitLab's security context (no public endpoint)
- Full GitLab context: pipeline status, commit SHA, can correlate with tests
- Cost predictable: one LLM call per MR event, no manual rate-limiting
- Group-level `include:` policy can roll out bot to all repos at once
  (Vitalii's domain)

**Cons:**
- Requires modifying the target repo's `.gitlab-ci.yml` (single line, but
  still a change — needs Vitalii or repo owner)
- Slash commands don't work in CI mode (no comment-event reception)
- Image pulled on every job = slower cold start (mitigate: runner cache)

---

## Which mode should I use?

| Scenario | Recommended mode |
|---|---|
| Single repo, want it running in 30 min | **Mode A** |
| Need to roll out to many repos fast | **Mode B** (with group-level include) |
| Need slash commands (`/review`, `/improve`) | **Mode A** |
| Concerned about public endpoint attack surface | **Mode B** |
| Don't want to touch CI/CD of target repos | **Mode A** (only option) |
| Want full GitLab context (pipeline status, commit SHA) | **Mode B** |

## Running both modes simultaneously

Both modes can run on the same repo without conflict. They post comments as
the same service account, so they show up as one actor in the MR timeline.
Webhook comments and CI job comments are visually identical from the
reviewer's perspective.

This is useful during migration: start with Mode A everywhere, opt specific
repos into Mode B, observe quality difference, decide.

---

## Sources

- [AI code review with PR-Agent (qodo)](https://confluence.winwin.travel/spaces/BENEW/pages/3351754) — Mode B precedent (existing pipeline setup in hotels-data)
- [🔍 Pull Request Guidelines (HDB)](https://confluence.winwin.travel/spaces/~71202098af837e35f44694bc1f817257a1df64/pages/4850772) — workflow guidance
- [PR-Agent GitLab install docs](https://docs.pr-agent.ai/installation/gitlab/) — webhook mode reference
- [PR-Agent Docker Hub tags](https://hub.docker.com/r/pragent/pr-agent/tags) — image version reference
