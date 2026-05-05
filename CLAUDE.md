# CLAUDE.md

## Project

Project name: `ha-logger-ext`

This repository contains a Home Assistant custom integration intended to provide an extended logging component optimized for Machine Learning datasets.

The goal is to collect Home Assistant entity state and attribute observations into a structured database schema designed for analytics, feature engineering, reproducible exports, and model training.

The project must be developed with the long-term objective of being compatible with Home Assistant standards and potentially acceptable as an official integration in the future, without depending on HACS-specific behavior.

## Language and style

- All source code, comments, docstrings, commit messages, test names, and technical documentation must be written in English.
- Keep the implementation simple, explicit, and maintainable.
- Prefer boring, standard, production-grade code over clever abstractions.
- Do not introduce unnecessary frameworks.
- Avoid premature optimization, but design the database layer with scalability and data-processing workloads in mind.
- Use clear naming. Names should describe business intent, not implementation tricks.
- Prefer explicit behavior over implicit magic.
- Do not write demo-quality code unless explicitly requested.

## Home Assistant integration model

This project is a Home Assistant custom integration.

The integration runs inside the Home Assistant process and must use Home Assistant's internal APIs.

Do not implement the production integration as an external WebSocket client connecting back to Home Assistant.

The integration must use:

- the `hass` object
- Home Assistant config entries
- Home Assistant event bus
- Home Assistant state machine
- Home Assistant lifecycle hooks
- official Home Assistant helpers and APIs where available

The production integration must not depend on local environment variables.

## Home Assistant integration standards

Follow Home Assistant development conventions as closely as possible.

The integration should be designed as a standard Home Assistant integration with:

- `custom_components/ha_logger_ext/`
- `manifest.json`
- `__init__.py`
- `const.py`
- `config_flow.py`
- `strings.json`
- `services.yaml` if services are introduced
- `diagnostics.py` if diagnostics are useful
- `quality_scale.yaml` when appropriate
- tests under `tests/components/ha_logger_ext/`

Prefer UI-based configuration through a config flow instead of YAML-only configuration.

The integration must be async-first and must not block the Home Assistant event loop.

Use Home Assistant helpers and APIs instead of ad-hoc implementations whenever possible.

## Target quality

Target Home Assistant "Bronze" quality as the initial baseline.

Prioritize:

- setup through the Home Assistant UI
- proper config flow validation
- clear error handling
- diagnostics support where useful
- automated tests for config flow and setup
- no blocking I/O in async code
- typed code
- stable migration path for future config/data schema changes

## Security requirements

Never commit secrets, tokens, API keys, local URLs, database passwords, or personal Home Assistant details.

The following files must remain local-only:

- `.env`
- `.env.*`
- `CLAUDE.local.md`
- local database files
- local Home Assistant tokens
- local Claude/Anthropic credentials
- local test credentials
- machine-specific configuration files

Do not log sensitive values.

Do not expose Home Assistant long-lived access tokens in errors, diagnostics, logs, tests, or documentation examples.

When generating examples, use placeholders only.

If Claude Code requires an Anthropic API key, configure it outside the repository through the local shell environment or Claude Code configuration.

## Configuration principles

The integration should be configured through Home Assistant config entries.

Configuration may eventually include:

- database backend
- database path, host, port, and database name
- database username and password where applicable
- entities to include or exclude
- attributes to include or exclude
- retention policy
- flush interval
- batch size
- schema migration options

Do not require users to configure:

- Home Assistant URL
- Home Assistant WebSocket URL
- Home Assistant long-lived access token

Those are not appropriate for an integration running inside Home Assistant.

## Core data model

This integration must not primarily persist raw Home Assistant events.

The primary dataset is based on entity state and attribute observations.

Each stored record should represent one observed value for one entity field over a validity interval.

A field may be:

- the entity state itself
- one entity attribute

Each persisted record should include at least:

- entity id
- field name
- value
- first inserted timestamp
- last seen timestamp

The storage layer must avoid inserting duplicate rows for repeated observations of the same value.

Deduplication must be applied against the latest stored value for the same entity id and field name.

If the latest stored value for the same entity id and field name is equal to the newly observed value, the integration must not insert a duplicate record. Instead, it should update the latest record's last seen timestamp.

If the value changed, the integration must insert a new record with a new validity interval.

Do not use a uniqueness rule based only on entity id, field name, and value across the entire history, because the same value may legitimately appear again after a different value.

Example:

- `temperature = 20`
- `temperature = 21`
- `temperature = 20`

The second `20` must create a new validity interval, not update the first historical `20`.

This compact validity-range representation is better suited for Machine Learning datasets and historical feature extraction than raw event streams.

## Database design goals

The database schema should be optimized for downstream Machine Learning and analytics workflows.

Design principles:

- preserve entity state and attribute observations where useful
- normalize where it improves querying and storage efficiency
- keep timestamps precise and timezone-safe
- store entity metadata separately from time-series observations where appropriate
- avoid schema designs that only mimic Home Assistant's default recorder tables
- support future database backends such as SQLite, MySQL, and PostgreSQL when practical
- keep backend-specific SQL isolated behind a storage abstraction
- design for reliable exports and reproducible datasets
- keep schema migrations explicit and testable
- avoid duplicate records when the latest value has not changed
- represent value validity ranges through first-seen and last-seen timestamps

The first implementation may start with SQLite if that reduces complexity, but the architecture must not prevent adding other database engines later.

The database schema should be generated and initialized automatically by the integration, similarly to how Home Assistant's native recorder manages its own storage setup.

## Recorder relationship

This integration is inspired by Home Assistant's standard recorder behavior, but it should not blindly clone its schema or internals.

The objective is to provide an alternative logging format better suited for:

- dataset generation
- feature extraction
- historical analysis
- ML training pipelines
- reproducible exports
- compact historical state representation

When possible, reuse Home Assistant event and state APIs rather than duplicating internal recorder behavior.

Do not depend on unsupported internal recorder implementation details unless there is a clear reason and the trade-off is documented.

The integration should be conceptually similar to the native recorder/logger role, but its storage model must be different and optimized for ML-oriented state and attribute history.

## Code quality rules

- Use Python type hints consistently.
- Use `from __future__ import annotations` where appropriate.
- Prefer `dataclasses` or typed structures for internal records.
- Keep functions small and testable.
- Separate Home Assistant integration glue from database and storage logic.
- Avoid global mutable state.
- Avoid broad `except Exception` unless the exception is logged and handled intentionally.
- Use structured logging.
- Do not use print statements.
- Do not introduce synchronous database calls inside async Home Assistant callbacks.
- If synchronous database drivers are used, isolate them through Home Assistant executor jobs or a dedicated safe abstraction.
- Keep domain constants in `const.py`.
- Keep storage-specific code isolated from Home Assistant setup code.
- Make failure modes explicit.

## Async and performance rules

The integration must not block the Home Assistant event loop.

For state and attribute logging:

- avoid slow operations inside callbacks
- buffer writes where appropriate
- use controlled batch flushing
- handle shutdown cleanly
- avoid unbounded memory growth
- apply backpressure or a documented dropping policy if needed
- document any data-loss trade-offs clearly

Database writes must be designed so that Home Assistant remains responsive even under high state-change volume.

## Testing expectations

Add or update tests whenever behavior changes.

Prioritize tests for:

- config flow happy path
- config flow invalid input
- duplicate configuration prevention
- setup and unload
- database schema initialization
- state serialization
- attribute serialization
- deduplication logic
- validity-range behavior
- migration behavior
- error handling
- listener registration and cleanup
- graceful shutdown behavior

Tests should be deterministic and must not require a real Home Assistant server, real database server, or real API tokens unless explicitly marked as integration or manual tests.

## Documentation expectations

Keep documentation clear, technical, and maintainable.

Documentation should eventually cover:

- project purpose
- installation instructions
- configuration instructions
- supported database backends
- known limitations
- development setup
- security notes
- roadmap
- database model
- deduplication behavior
- validity-range semantics

Documentation should be clear enough for Home Assistant users and precise enough for future maintainers.

Do not document unsupported features as if they already exist.

## Git workflow

Before proposing a commit, summarize:

- what changed
- why it changed
- how it was tested

Commit messages must follow Conventional Commits.

Examples:

- `feat: add initial Home Assistant integration scaffold`
- `feat(storage): add SQLite schema initializer`
- `feat(storage): add state value deduplication`
- `fix(config-flow): validate database connection settings`
- `test: add config flow coverage`
- `docs: document local development setup`
- `refactor(storage): isolate backend-specific SQL`

## Development workflow

When asked to implement changes:

1. Inspect the existing repository structure first.
2. Propose a minimal plan.
3. Make focused changes.
4. Avoid unrelated rewrites.
5. Run relevant tests or explain why they could not be run.
6. Report modified files.
7. Highlight risks, assumptions, and follow-up work.

Do not create large architectural rewrites without an explicit reason.

Do not introduce features that were not requested unless they are necessary for the requested change.

Prefer small, reviewable commits.

## Dependency policy

Keep dependencies minimal.

Before adding a dependency, explain:

- why it is needed
- whether Home Assistant already provides an equivalent
- whether it is async-compatible
- whether it is suitable for future Home Assistant core inclusion
- whether it increases installation or maintenance risk

Avoid dependencies that would make official Home Assistant adoption harder.

## Compatibility

Prefer compatibility with current Home Assistant development standards.

Avoid deprecated Home Assistant APIs.

When uncertain, follow:

- official Home Assistant developer documentation
- existing Home Assistant core integrations
- current Home Assistant integration quality scale expectations

Do not optimize for HACS-specific behavior.

HACS may be useful for early distribution, but the integration should not depend on HACS features to work correctly.

## Project boundaries

This project is not intended to be:

- a generic ETL framework
- a cloud service
- a dashboarding system
- a full data warehouse
- a telemetry exporter that sends user data outside the local environment by default
- an external Home Assistant client
- a replacement for Home Assistant's core event bus
- a raw event archive

The default behavior must be local-first and privacy-respecting.

## Preferred implementation direction

Start with a clean custom integration scaffold.

Then proceed in this order:

1. domain constants and manifest
2. config flow
3. setup and unload lifecycle
4. storage abstraction
5. SQLite backend
6. schema initialization
7. entity state and attribute observer
8. deduplication logic
9. batch flushing
10. tests
11. documentation
12. optional MySQL/PostgreSQL backend support

Do not start from advanced ML features before the integration foundation is stable.

## Initial implementation constraints

For the initial scaffold:

- do not implement real database writes yet
- do not implement full deduplication yet
- do not add MySQL or PostgreSQL support yet
- do not add external Home Assistant WebSocket access
- do not persist raw Home Assistant events as the main dataset
- do not introduce cloud services
- do not require environment variables
- do not require secrets
- do not create complex abstractions before the basic integration lifecycle is working

The first useful milestone is a valid Home Assistant custom integration scaffold that can be installed, configured, loaded, unloaded, and tested.

## Local-only notes

Personal working notes, local credentials, temporary prompts, experiments, and machine-specific paths must go into `CLAUDE.local.md`, not this file.

`CLAUDE.local.md` must not be committed.
