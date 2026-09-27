# winwin.travel review conventions

These guidelines are passed to the bot on every `/review` invocation. Keep this
file short and high-signal — long guidelines cause the bot to churn and
over-flag. Each rule below cites its source page on Confluence so any
reviewer can verify origin and update at the source.

## Sources

All rules derive from these four authoritative documents on
[confluence.winwin.travel](https://confluence.winwin.travel):

1. [AI code review with PR-Agent (qodo)](https://confluence.winwin.travel/spaces/BENEW/pages/3351754)
   — BENEW (Backend). PR-Agent usage, Spring Boot review checklist.
2. [🔍 Pull Request Guidelines (HDB)](https://confluence.winwin.travel/spaces/~71202098af837e35f44694bc1f817257a1df64/pages/4850772)
   — Hotels Data. PR creation, review checklist, merge rules.
3. [Java Spring Project Code Review Checklist](https://confluence.winwin.travel/spaces/HR/pages/44990480)
   — HR. Scored checklist for Java/Spring MRs.
4. [AI Usage Standards & Guidelines](https://confluence.winwin.travel/spaces/QA/pages/12124185)
   — QA. What AI may and may not do inside the company.

---

## Style & naming

**Source:** [Java Spring Project Code Review Checklist](https://confluence.winwin.travel/spaces/HR/pages/44990480),
§1 Code Style & Naming Conventions.

- Classes / interfaces → **PascalCase**. Methods / variables → **camelCase**.
  Constants → **UPPER_SNAKE_CASE**.
- Indentation: 4 spaces. **Line length < 120 chars** (Spotless auto-formats
  to ~100; 120 is the human-review ceiling).
- Public classes and methods: **JavaDoc** when the contract isn't obvious.
- No wildcard imports (`import java.util.*`). Imports organized.
- No dead code: unused variables, methods, or imports — flag.
- Code is self-documenting; comments explain "why", not "what".

## Architecture

**Sources:** [Java Spring Project Code Review Checklist](https://confluence.winwin.travel/spaces/HR/pages/44990480)
§2; [HDB PR Guidelines](https://confluence.winwin.travel/spaces/~71202098af837e35f44694bc1f817257a1df64/pages/4850772);
[AI code review with PR-Agent](https://confluence.winwin.travel/spaces/BENEW/pages/3351754).

- **Layered architecture:** Controller → Service → Repository → Entity.
  Controllers handle HTTP only — **no business logic in controllers**.
- **Entities vs DTOs** are separate. Validation lives on DTOs.
- **Constructor injection** preferred over field injection
  (`@Autowired` on field is a smell).
- **No circular dependencies.**
- Interfaces used for loose coupling. `@Configuration` classes organized.
- Respect **SOLID / DRY / KISS** — flag violations explicitly with the
  principle name.
- Detect: God classes (>10 public methods), long methods (>30 lines),
  duplicate code blocks, magic numbers, deep nesting (>3 levels), methods
  with >4 parameters, lazy classes, feature envy, primitive obsession.

## Spring / JPA specifics

**Source:** [AI code review with PR-Agent](https://confluence.winwin.travel/spaces/BENEW/pages/3351754)
(§"What PR-Agent Checks (Spring Boot Specific)").

- **N+1 queries** in JPA/Hibernate. Missing fetch strategies on
  `@OneToMany` / `@ManyToOne`. Flag with the specific association.
- **`@Transactional`** usage:
  - Missing on service methods that mutate state.
  - `@Transactional` on private methods (Spring silently ignores — common
    bug). Flag as critical.
- REST API: wrong HTTP status codes (e.g. 200 on error), poor endpoint
  design (verbs in paths, RPC-style endpoints).
- Repository layer: native queries with string concatenation (SQL
  injection risk) instead of parameterized JPQL/native.

## Security

**Sources:** [Java Spring Project Code Review Checklist](https://confluence.winwin.travel/spaces/HR/pages/44990480)
§3; [AI code review with PR-Agent](https://confluence.winwin.travel/spaces/BENEW/pages/3351754);
[AI Usage Standards](https://confluence.winwin.travel/spaces/QA/pages/12124185).

- JWT validation + expiration. Reasonable access/refresh token lifetimes.
- Passwords: **bcrypt or Argon2**, never plain text.
- `@PreAuthorize` / `@Secured` for role-based access — flag missing
  authorization on state-changing endpoints.
- Secrets in **environment variables**, never in code.
  `application.properties` for non-secret config only.
- CORS not overly permissive (`*` on credentials is a smell).
- CSRF tokens on state-changing operations.
- Security headers configured (X-Frame-Options, X-Content-Type-Options,
  etc.).
- **SQL injection** prevention via parameterized JPA queries.
- **XSS**: flag unescaped user input in templates / responses.
- **Production credentials, PII, customer data must never appear in the
  bot's prompt context.** Sanitize before sending anything to the LLM.

## Logging

**Source:** [Java Spring Project Code Review Checklist](https://confluence.winwin.travel/spaces/HR/pages/44990480)
§4; [AI code review with PR-Agent](https://confluence.winwin.travel/spaces/BENEW/pages/3351754).

- **SLF4J** with Logback or Log4j2.
- Log levels by intent:
  - **ERROR** — unexpected exceptions, needs investigation.
  - **WARN** — recoverable issues.
  - **INFO** — business events (booking created, payment processed).
  - **DEBUG** — technical details for debugging.
  Flag level misuse (e.g. `INFO` in a loop, `ERROR` for validation
  failures).
- Log messages descriptive with context. Parameterized messages
  (`log.debug("Processing {}", id)`) — not string concat.
- **No PII, passwords, JWT tokens, or secrets in log lines.**
- Request tracing via correlation IDs.
- Environment-aware log config (dev verbose, prod WARN+).

## Exception handling

**Source:** [Java Spring Project Code Review Checklist](https://confluence.winwin.travel/spaces/HR/pages/44990480)
§5; [AI code review with PR-Agent](https://confluence.winwin.travel/spaces/BENEW/pages/3351754).

- `@ControllerAdvice` / `@RestControllerAdvice` for centralized handling.
- Custom exceptions extending `RuntimeException` (or project base).
- Avoid catching generic `Exception` / `Throwable`.
- Error responses: status + message + timestamp, no internals leaked.
- Try-catch for recoverable errors only, not control flow.
- Try-with-resources for streams, DB connections.
- Null-safety: `Optional` or explicit null-checks on dereference.

## Input validation

**Source:** [Java Spring Project Code Review Checklist](https://confluence.winwin.travel/spaces/HR/pages/44990480)
§6.

- Bean Validation: `@NotNull`, `@NotBlank`, `@Email`, `@Size`, `@Pattern`
  on DTOs (not entities).
- `@Valid` / `@Validated` on controller method params.
- Validation messages user-friendly, not stack traces.
- Business-rule validation in service layer (not in entity setters).
- All external input validated — never trust user input.

## Code smells

**Source:** [Java Spring Project Code Review Checklist](https://confluence.winwin.travel/spaces/HR/pages/44990480)
§8.

- God class, long method, duplicate code, magic number, deep nesting,
  too-many-params (>4), lazy class, feature envy, primitive obsession,
  temporary fields. Each as a specific named flag.

## PR hygiene

**Source:** [HDB Pull Request Guidelines](https://confluence.winwin.travel/spaces/~71202098af837e35f44694bc1f817257a1df64/pages/4850772).

- **Branch name:** `<JIRA-ID>-<short-description>` (e.g. `HD-1789-enable-tests-ci`).
  No category prefixes (`feature/`, `fix/`, `release/`).
- **CHANGELOG.md** updated in the same commit.
- **Unit tests cover the changes.** No tests → flag.
- **Database changelogs** (if any): YAML format with rollback sections.
  Naming: `<index>-<module>-<task>-<table>`.
- **No unnecessary complexity or dead code.**
- Code accomplishes the stated task — implementation actually solves the
  problem in the Jira ticket.

## AI assistant guardrails

**Source:** [AI Usage Standards & Guidelines](https://confluence.winwin.travel/spaces/QA/pages/12124185)
(§§Security, Forbidden Use Cases, Ownership Principle).

The bot is an **assistant, not a gatekeeper**. It must:

- **Comment only.** Never block merges, never set approval state, never
  close MRs. That authority belongs to the human reviewer (currently
  CODEOWNERS + tech lead).
- **Suggest, not auto-apply.** Inline suggestions may appear, but actual
  code changes are the author's responsibility. Do not generate `/improve`
  output automatically — only on explicit user request.
- **Never receive production data in the prompt context.** Strip secrets,
  PII, customer data, fraud logic, pricing algorithms, and auth
  implementation details before any diff is sent to the LLM.
- **Stay within its mandate.** Architecture, security decisions, and
  deployment gates are explicitly out of scope. The bot comments on what
  it sees; humans decide.
- **Acknowledge its limits.** When the bot is unsure, it says so in the
  comment rather than guessing.

### What the bot must NEVER flag

These create noise and erode reviewer trust. Skip silently:

- Generated / lock files (`*.lock`, `package-lock.json`, `yarn.lock`,
  `gradle.lockfile`).
- Auto-generated code (look for `@Generated`, `// AUTO-GENERATED`,
  `// Code generated by` headers).
- DB migrations under `db/migration/`, `flyway/`, `liquibase/`.
- Trivial formatting-only changes in `application*.yml` with no semantic
  difference.
- The bot's own files (review guidelines, configuration, tests for this
  project).
- Files outside the diff (PR-Agent only sees the diff, but configuration
  can exclude whole paths if needed).
