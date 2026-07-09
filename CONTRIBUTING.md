# Contributing to ha-recorder-ext

Thank you for your interest in contributing. This document describes the conventions and workflow expected from all contributors.

---

## Branch strategy

The repository uses an environment-tiered branch model. The following branches are **protected** and accept only maintainer merges:

| Branch pattern | Purpose |
|---|---|
| `dev/main` | Default integration branch (target for contributor PRs) |
| `dev/*` | Development environment branches |
| `qa/*` | QA environment branches |
| `uat/*` | User acceptance testing branches |
| `staging/*` | Staging environment branches |
| `prod/*` | Production environment branches |

### Contributor branches

Contributors must work on short-lived branches following this naming pattern:

| Prefix | When to use | Example |
|---|---|---|
| `feat/` | New feature | `feat/mysql-backend` |
| `fix/` | Bug fix | `fix/dedup-float-nan` |
| `refactor/` | Code restructuring without behavior change | `refactor/storage-abstraction` |
| `test/` | Adding or improving tests | `test/lifecycle-coverage` |
| `docs/` | Documentation only | `docs/readme-schema` |
| `chore/` | Maintenance, dependencies, tooling | `chore/bump-aiosqlite` |
| `ci/` | CI/CD changes | `ci/add-python314` |

**Open pull requests against `dev/main`**, not against `master`.

---

## Commit messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

### Format

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### Types

| Type | When to use |
|---|---|
| `feat` | A new feature |
| `fix` | A bug fix |
| `refactor` | Code change that is neither a bug fix nor a new feature |
| `test` | Adding or correcting tests |
| `docs` | Documentation only |
| `chore` | Maintenance (dependencies, build, tooling) |
| `ci` | CI/CD pipeline changes |
| `perf` | Performance improvement |

### Scopes (optional but encouraged)

Use a scope to clarify which part of the codebase is affected:

```
feat(storage): add PostgreSQL backend
fix(config-flow): validate db_path on submit
test(dedup): add validity-range edge cases
ci: add Python 3.14 to test matrix
```

### Breaking changes

Append `!` after the type/scope or add a `BREAKING CHANGE:` footer:

```
feat(storage)!: rename value_bool column to value_boolean

BREAKING CHANGE: existing databases must be migrated.
```

### Examples

```
feat: add MySQL backend
fix(sqlite): handle empty queue in flush
refactor(storage): extract _maybe_commit helper
test(config-flow): add exclude_domains validation coverage
docs: document deduplication semantics
chore: bump aiosqlite to 0.20.0
ci: add Python 3.14 to test matrix
```

---

## Development setup

```bash
git clone git@github.com:Matzaptor/ha-recorder-ext.git
cd ha-recorder-ext

# Create a virtual environment (Python 3.13+)
python3.13 -m venv .venv
source .venv/bin/activate

# Install test dependencies
pip install -r requirements-test.txt
```

---

## Running tests

```bash
pytest tests/ -v
```

All tests must pass before opening a pull request. Tests do not require a running Home Assistant instance or a real database.

**Add or update tests whenever you change behavior.** The project targets the following coverage areas:

- Config flow (happy path, validation, error cases)
- Storage backend (schema init, migrations, CRUD)
- Serialization and deduplication logic
- Integration lifecycle (setup, unload, graceful shutdown)
- Filter behavior

---

## Pull request checklist

Before requesting review, verify:

- [ ] Branch is named according to the convention above
- [ ] All commits follow Conventional Commits format
- [ ] `pytest tests/ -v` passes locally
- [ ] New behavior is covered by tests
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] No secrets, tokens, local paths, or personal data are committed

---

## What not to contribute (yet)

The following are out of scope until the core integration is stable:

- MySQL or PostgreSQL backends (architecture is ready; implementation planned)
- Cloud sync or remote export
- Dashboard or frontend components
- HACS-specific features that would prevent HA core adoption
- Raw Home Assistant event archiving (the data model is observation-based, not event-based)

If you are unsure whether your contribution fits the project scope, open an issue first.

---

## Reporting issues

Use the [GitHub issue tracker](https://github.com/Matzaptor/ha-recorder-ext/issues).

When reporting a bug, include:
- Home Assistant version
- Integration version (visible in Settings → Devices & Services → HA External Recorder)
- Database backend and version
- Relevant log output (with sensitive values redacted)
