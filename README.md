# pr-review-bot

Self-hosted AI code reviewer for GitLab Merge Requests, built on top of
[PR-Agent](https://github.com/The-PR-Agent) (Apache 2.0) with
[MiniMax-M3](https://platform.minimax.io/) as the LLM backend.

## Why

- **Data stays in-house.** No code leaves our infrastructure to a third-party
  review vendor (Bito, CodeRabbit, etc. all require external data transfer).
- **Cost.** ~$1/month for a 10-dev team vs. $150-300/month for paid SaaS.
- **Conventions.** Tunable to hotels-data rules (HD-NNN branches, mandatory
  tests, naming, ADR compliance).

## Architecture (one paragraph)

A single Docker container runs PR-Agent in webhook mode. GitLab sends MR
events (open, update, comment) to the container over HTTPS. PR-Agent pulls
the diff, sends it to MiniMax-M3 via the OpenAI-compatible endpoint, and
posts inline comments back to GitLab. No state lives outside the container
except logs.

## Quick start

```bash
# 1. Clone and enter
git clone <repo-url> pr-review-bot
cd pr-review-bot

# 2. Configure secrets
cp .env.example .env
# Edit .env: set OPENAI_KEY, GITLAB_TOKEN, WEBHOOK_SECRET.
#   - OPENAI_KEY: get from https://platform.minimax.io/
#   - GITLAB_TOKEN: PAT with `api` scope, owned by service account
#   - WEBHOOK_SECRET: openssl rand -hex 32

# 3. Validate config (optional, recommended)
bash tests/run_all.sh

# 4. Start
docker compose up -d

# 5. Verify health
curl -fsS http://localhost:3000/health
```

## GitLab webhook setup

In `gitlab.winwin.travel` → your project → Settings → Webhooks:

| Field | Value |
|---|---|
| URL | `https://<your-host>:3000/<webhook-path>` (see PR-Agent docs) |
| Trigger events | Merge request events, Comments |
| SSL verification | enabled |
| Secret | same value as `WEBHOOK_SECRET` in `.env` |

PR-Agent's webhook receiver URL depends on its version — check the container
logs after `docker compose up` for the exact path it listens on.

## Slash commands

In any MR comment, the bot responds to:

| Command | Effect |
|---|---|
| `/review` | Full review of the diff |
| `/review --lite` | Quick pass, fewer tokens |
| `/describe` | Auto-generate MR description |
| `/improve` | Suggest code improvements (expensive) |
| `/ask <question>` | Q&A about the diff |

## Tests

```bash
bash tests/run_all.sh
```

Four tests:
- `test_compose_validity.sh` — `docker compose config` exits 0
- `test_env_completeness.sh` — `.env.example` documents every required key
- `test_no_secrets_leaked.sh` — regression guard, no real secrets in tracked files
- `test_config_toml_parse.sh` — `configuration.toml` is valid TOML with expected keys

## Cost

Per hotels-data usage estimate (~250 MR/month, avg 6K input + 1.5K output tokens
per `/review`):

| Model | $/month |
|---|---|
| MiniMax-M3 (default) | ~$0.90 |
| Gemini 2.5 Flash | ~$1.40 |
| Claude Haiku 4.5 | ~$3.30 |

Switch model by editing `configuration.toml` `[config.model].model_name` and
restarting the container.

## Rotate secrets

| Secret | How to rotate |
|---|---|
| `OPENAI_KEY` | Generate new in MiniMax console, update `.env`, `docker compose up -d` |
| `GITLAB_TOKEN` | Rotate PAT in GitLab UI, update `.env`, `docker compose up -d` |
| `WEBHOOK_SECRET` | Generate new (`openssl rand -hex 32`), update `.env` AND the GitLab webhook settings (both sides must match) |

## Troubleshooting

**Container unhealthy.** `docker compose logs pr-agent`. Common causes: missing
`.env`, invalid `OPENAI_KEY`, GitLab unreachable.

**No comments on MR.** Verify the webhook is firing (GitLab → Settings →
Webhooks → Recent deliveries). The container's IP must be reachable from
GitLab. If using a reverse proxy, check that the proxy passes the webhook
path through to the container.

**Bot comments but quality is low.** The first few runs will be noisy. Edit
`review_guidelines.md` to tune, restart the container. If persistent low
quality, swap model in `configuration.toml` to a stronger one.

## License

PR-Agent is Apache 2.0. Anything in this repo follows the same license unless
noted otherwise.

## Spec / plan / tasks

Full SDD artifacts live in `.specify/` (gitignored, local-only). For
reviewers and AI tooling, start with:

- `.specify/memory/constitution.md` — non-negotiable project principles
- `.specify/specs/pr-review-bot-mvp/spec.md` — what we're building
- `.specify/specs/pr-review-bot-mvp/plan.md` — how we built it
- `.specify/specs/pr-review-bot-mvp/tasks.md` — task tracker
