# PR-Agent version tracking

This project pins the PR-Agent Docker image to a specific version for
reproducibility. Never use `:latest` in production — you lose the ability
to roll back, debug, or audit.

## Current pinned version

**`pragent/pr-agent:0.46.0`** (last published 2026-09-21)

Image source: [Docker Hub — pragent/pr-agent](https://hub.docker.com/r/pragent/pr-agent)

Source repository: [The-PR-Agent/pr-agent on GitHub](https://github.com/The-PR-Agent/pr-agent)

## Where it's used

| File | Image reference | Purpose |
|---|---|---|
| `docker-compose.yml` | `pragent/pr-agent:0.46.0` | Mode A — long-running webhook server |
| `templates/.gitlab-ci.yml.template` | `pragent/pr-agent:0.46.0` | Mode B — per-MR CI job (uses `pr-agent` CLI) |

## Why a single pinned version (not specialized tags)

The community image includes both the webhook server and the CLI in the
same layer. Using a single tag avoids drift between modes (e.g., webhook
mode running v0.46.0-gitlab_webhook while CI mode runs v0.46.0, with
different bug fixes in each).

If a specific variant becomes necessary later (e.g., a `0.47.0-gitlab_lambda`
appears with a feature we need), split the pin — both files in one commit
with a CHANGELOG entry.

## Upgrade procedure

1. **Watch for releases.** Subscribe to [The-PR-Agent releases](https://github.com/The-PR-Agent/pr-agent/releases)
   on GitHub. Filter on tag prefix `v` for stable releases.
2. **Check the changelog.** Skim release notes for breaking config changes
   affecting our setup. Most relevant for us:
   - `configuration.toml` schema changes
   - webhook path/route changes (breaks our GitLab webhook URL)
   - new env vars we should set
3. **Test on a sandbox.** Update `docker-compose.yml`, restart the container,
   open a test MR, verify review posts.
4. **Bump in both files** in the same commit:
   - `docker-compose.yml`
   - `templates/.gitlab-ci.yml.template`
5. **Tag a new release** of pr-review-bot (`vX.Y.Z`).
6. **For Mode B users** who pinned to our previous version: bump their
   `include: remote:` URL to point at the new tag.

## Cadence

- **Security-relevant release:** upgrade within 7 days.
- **Regular feature release:** upgrade within 30 days, batched.
- **Major version bump (e.g., 0.x → 1.x):** read release notes carefully,
  test extensively, expect config changes.

## History

| Date | Version | Notes |
|---|---|---|
| 2026-09-27 | `0.46.0` | Initial pin. GitHub-hosted deployment, dual-mode (webhook + CI template). |
