# pr-review-bot

Self-hosted AI code reviewer for GitLab Merge Requests, built on top of
[PR-Agent](https://github.com/The-PR-Agent/pr-agent) (Apache 2.0) with
[MiniMax-M3](https://platform.minimax.io/) as the LLM backend.

## Why

- **Data stays in-house.** No code leaves our infrastructure to a third-party
  review vendor (Bito, CodeRabbit, etc. all require external data transfer).
- **Cost.** ~$1/month for a 10-dev team vs. $150-300/month for paid SaaS.
- **Conventions.** Tunable to hotels-data rules (HD-NNN branches, mandatory
  tests, naming, ADR compliance).
- **Does not block merges.** Comments only. The human reviewer keeps authority
  (currently [CODEOWNERS](https://gitlab.winwin.travel) + tech lead).

## Deployment modes

Two ways to plug the bot into GitLab MRs. Both read the same
`review_guidelines.md` and `configuration.toml` — the difference is how
the bot is triggered.

| Mode | Trigger | Touches `.gitlab-ci.yml` of target repo? | Slash commands? |
|---|---|---|---|
| **A — Group webhook** (default) | GitLab webhook → external container | No | Yes (`/review`, `/improve`, `/ask`) |
| **B — CI pipeline** | Per-MR CI job via `include: remote:` | Yes (single line) | No |

Full comparison and decision matrix: **[docs/deployment-modes.md](docs/deployment-modes.md)**.

## Quick start — Mode A (webhook)

```bash
# 1. Clone and enter
git clone https://github.com/pr-review-bot/pr-review-bot
cd pr-review-bot

# 2. Configure secrets
cp .env.example .env
# Edit .env:
#   OPENAI_KEY      — get from https://platform.minimax.io/
#   GITLAB_TOKEN    — PAT with `api` scope, owned by a service account
#   WEBHOOK_SECRET  — openssl rand -hex 32
#   GITLAB_URL      — https://gitlab.winwin.travel
#   OPENAI_BASE_URL — https://api.minimax.io/v1

# 3. Validate config (optional, recommended)
bash tests/run_all.sh

# 4. Start
docker compose up -d

# 5. Verify health
curl -fsS http://localhost:3000/health
```

Then in GitLab: **Group → Settings → Webhooks → Add webhook** with the
container URL, triggers: *Merge request events* + *Comments*.

## Quick start — Mode B (CI pipeline, requires repo owner)

In the target repo's `.gitlab-ci.yml`:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/pr-review-bot/pr-review-bot/v0.2.0-dual-mode/templates/.gitlab-ci.yml.template'
```

Then in target repo's **Settings → CI/CD → Variables** (masked + protected):

| Variable | Source |
|---|---|
| `MINIMAX_API_KEY` | MiniMax console |
| `PR_AGENT_TOKEN` | GitLab PAT, `api` scope, service account |

## Slash commands (Mode A only)

In any MR comment, the bot responds to:

| Command | Effect |
|---|---|
| `/review` | Full review of the diff |
| `/review --lite` | Quick pass, fewer tokens |
| `/describe` | Auto-generate MR description |
| `/improve` | Suggest code improvements (expensive) |
| `/ask <question>` | Q&A about the diff |

## Demo setup (sandbox for sign-off)

Before rolling out to hotels-data repos, run the bot against a sandbox
repo and demo it to the team lead. Steps: **[docs/sandbox-setup.md](docs/sandbox-setup.md)**.

## Tests

```bash
bash tests/run_all.sh
```

Seven tests:

- `test_compose_validity.sh` — `docker compose config` exits 0
- `test_env_completeness.sh` — `.env.example` documents every required key
- `test_no_secrets_leaked.sh` — no real secrets in tracked files
- `test_config_toml_parse.sh` — `configuration.toml` is valid TOML
- `test_review_guidelines.sh` — `review_guidelines.md` structure + citations
- `test_template_validity.sh` — GitLab CI template is valid + pinned + MR-scoped
- `test_image_pinned.sh` — all PR-Agent image refs are version-pinned (no `:latest`)

## Versioning

The PR-Agent image is pinned to a specific version in both `docker-compose.yml`
and `templates/.gitlab-ci.yml.template`. Upgrade procedure documented in
**[docs/pr-agent-version.md](docs/pr-agent-version.md)**.

Current pin: **`pragent/pr-agent:0.46.0`**.

## Cost

Per hotels-data usage estimate (~250 MR/month, avg 6K input + 1.5K output tokens
per `/review`):

| Model | $/month |
|---|---|
| MiniMax-M3 (default) | ~$0.90 |
| Gemini 2.5 Flash | ~$1.40 |
| Claude Haiku 4.5 | ~$3.30 |

Switch model by editing `configuration.toml` `[config.model].model_name` and
restarting the container (or editing the template's image for Mode B).

## CI/CD

This project's own CI runs on **GitHub Actions** (`.github/workflows/ci.yml`):
runs the test suite on every push and PR to `main` / `develop`.

CI/CD for **target repos** (hotels-data and friends) is **not** in scope.
Mode A doesn't touch their CI/CD at all; Mode B requires the repo owner
to add a single `include:` line. See [docs/deployment-modes.md](docs/deployment-modes.md).

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
