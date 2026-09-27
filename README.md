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

## Status

**MVP — work in progress.**

See `.specify/specs/pr-review-bot-mvp/` for the SDD artifacts driving this work
(`spec.md`, `plan.md`, `tasks.md`).

## Quick start (once MVP ships)

```bash
cp .env.example .env
# edit .env with your MiniMax API key + GitLab token
docker compose up -d
```

Configure a GitLab webhook to point at the running container, open a test MR,
watch the bot comment.

## License

PR-Agent is Apache 2.0. Anything we add on top follows the same license unless
noted otherwise.
