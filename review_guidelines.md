# Hotels-data review conventions

These guidelines are passed to the bot on every `/review` invocation. Keep this
file short and high-signal — long guidelines cause the bot to churn and
over-flag.

## Branch naming

- Format: `HD-NNN-kebab-case-description`
- No category prefixes (`feature/`, `fix/`, `release/`)
- No uppercase letters
- Examples: `HD-1789-enable-tests-ci`, `HD-45-email-status-docs`

## Commit messages

- Title must start with the Jira ticket ID: `HD-1789: enable CI for tests`
- Body in imperative mood, wrapped at 72 chars
- Reference the ticket URL at the bottom

## Tests

- New behavior must come with tests. MRs without test changes are flagged.
- Prefer unit tests for business logic, integration tests for API/DB edges
- Don't add tests for trivial getters/setters

## Code style

- Java/Spring: follow existing project conventions (checkstyle if configured)
- Prefer composition over inheritance
- Public methods on services need a Javadoc comment if the contract isn't obvious
- No wildcard imports (`import java.util.*`)

## Things the bot should NOT flag

- Lock files (`*.lock`, `package-lock.json`, etc.)
- Generated code (look for `@Generated` or `// AUTO-GENERATED` headers)
- Migrations under `db/migration/` or `flyway/`
- `application*.yml` formatting changes that don't change behavior

## Things the bot SHOULD flag

- Hardcoded secrets, URLs, or credentials
- Missing null-checks on Optional return values
- `System.out.println` left in production code paths
- `@Transactional` on private methods (Spring silent failure)
- Direct `EntityManager` usage outside of repositories
- TODOs without a ticket reference
