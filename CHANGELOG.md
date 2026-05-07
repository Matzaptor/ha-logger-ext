# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- `import_from_recorder` HA service: imports historical entity states from the built-in Recorder database into ha_logger_ext
  - 7-day sliding window chunks to keep memory footprint constant on large databases
  - RLE compression: consecutive equal values are merged into a single validity interval
  - Idempotent: existing observations detected via `has_observations_in_range` and skipped
  - Resumable: re-running after a crash skips already-imported time ranges and continues from the first gap
  - Attributes tracked per-field alongside state
  - Parameters: `start_date`, `end_date`, `entity_ids` (all optional)
  - Progress logged at INFO level
- `has_observations_in_range(entity_pk, field_name, start, end) -> bool` on `StorageBackend` ABC, implemented in all four backends (SQLite, DuckDB, MySQL, PostgreSQL)
- `services.yaml` with full field descriptions for the HA Developer tools UI
- 20 new tests: RLE unit tests, integration tests with real SQLite backend and mocked recorder, overlap detection
- Two `binary_sensor` entities exposed per config entry:
  - **Recording** (`mdi:database-clock`): `on` while the coordinator flush loop is running; attributes: `queue_size`, `last_flush`, `flush_interval_seconds`
  - **Import in progress** (`mdi:database-import`): `on` while `import_from_recorder` is running; attribute `last_import_intervals_inserted` available after completion
- Both sensors update in push mode via a coordinator listener mechanism (no polling)
- Integration's own entities are auto-excluded from being logged by the coordinator
- 11 new tests for binary sensors (141 total)

---

## [1.0.0] — 2026-05-07

### Added

- DuckDB backend (`duckdb` driver, sync API wrapped in asyncio executor): same schema, migration dispatcher, and deduplication semantics as SQLite; excellent for analytics and ML queries
- MySQL / MariaDB backend (`aiomysql`, connection pool, `INSERT IGNORE` for entity upsert, `%s` placeholders)
- PostgreSQL backend (`asyncpg`, connection pool, `ON CONFLICT DO NOTHING`, `$N` placeholders, `BYTEA` PKs)
- Config flow refactored to 3 steps: step 1 selects db_type only; step 2a (`embedded`) collects `db_path` and filters for SQLite/DuckDB; step 2b (`server`) collects host/port/name/user/password and filters for MySQL/PostgreSQL — no SQLite file is created if a server backend is chosen
- `strings.json` / `translations/en.json`: new steps `embedded` and `server` with full field labels and descriptions
- `manifest.json`: `duckdb>=0.10.0`, `aiomysql>=0.2.0`, `asyncpg>=0.29.0` added to requirements
- README: backend comparison table, 2-step setup documentation for embedded and server backends
- `test_backends.py`: DuckDB real integration tests (entity, observation, dedup, schema guard); MySQL and PostgreSQL factory tests with mocked drivers
- `test_storage.py`: updated MySQL/PostgreSQL factory tests to reflect real backend types instead of stale `NotImplementedError` stubs

### Changed

- `manifest.json`: version bumped to 1.0.0

---

## [0.3.0] — 2026-05-07

### Added

- Config flow DB validation: `_test_backend()` helper initialises and closes the backend before accepting a new entry; returns `cannot_connect` on failure and shows the error in the form
- Same validation applied to the reconfigure flow
- `strict-typing` enforced: `mypy --strict` added to `pyproject.toml`; `mypy>=1.5` added to `requirements-test.txt`
- Factory error paths: `create_backend` raises `ValueError` for unknown `db_type` and `NotImplementedError` for `mysql`/`postgresql`
- `TestSQLiteBackendEdgeCases`: close-before-initialize, idempotent initialize, schema already at current version, schema newer than integration (RuntimeError)
- `TestQueueBehavior`: queue-full during start drops snapshots without raising; queue-full on state-change drops events without raising
- `TestFlushRollback`: commit failure triggers rollback and keeps coordinator running
- Config flow range-violation tests: options flow rejects `flush_interval < 5` and `queue_max_size > 100 000`
- README: Diagnostics section with full key/value table; Options table extended with filter override fields

### Changed

- `quality_scale.yaml`: `exception-translations` and `strict-typing` promoted from `todo` to `done`
- Options section in README clarifies that filter values set in options override setup-time values
- CLAUDE.md: implementation status updated (steps 11–19 marked completed, step 20 remaining)

### Fixed

- `test_rollback_discards_writes`: missing `await b.close()` left an aiosqlite worker thread alive, causing the HA test fixture thread-leak assertion to fail

---

## [0.2.0] — 2026-05-07

### Added

- SQLite database now created in a dedicated `ha_logger_ext/` subdirectory under the HA config directory (default: `ha_logger_ext/ha_logger_ext.db`); parent directory is created automatically on first setup
- Schema migration dispatcher: module-level `_MIGRATIONS` dict maps each target version to an async migration function; adding a future migration requires only registering a function in the dict
- `diagnostics.py` extended: `last_flush` timestamp (UTC ISO 8601, `null` before first flush), `flush_interval`, and a dedicated `options` section alongside `config`
- `quality_scale.yaml` extended with Gold-level rules; entity/device/discovery rules marked `exempt` for this background service
- Integration icon (`icon.png`, 256×256 RGBA): database cylinder with analytics bar chart on HA blue background; shown in the HA integrations panel and picked up automatically by HACS
- `flush_interval` property on `LoggerCoordinator` for diagnostics and testing

### Fixed

- Migration loop bug: was always passing the original `current` version instead of `target - 1`, causing incorrect from-version in multi-step migrations
- `strings.json`: `reconfigure` step was nested at `config.reconfigure.reconfigure` instead of the correct `config.step.reconfigure`, inconsistent with `translations/en.json`
- `quality_scale.yaml`: duplicate keys (`docs-installation-instructions`, `reconfiguration-flow`, `repair-issues`) appeared in both Silver and Gold sections

### Changed

- README: default database path updated to `ha_logger_ext/ha_logger_ext.db`; Removal section updated accordingly; stale Known Limitations entry about filters removed (filters are editable via the options flow without re-installing)
- Filter configuration (exclude domains/entities/attributes) is now documented as editable via the options flow at any time

---

## [0.1.0] — 2026-05-06

### Added

- Initial integration scaffold: `manifest.json`, `const.py`, `config_flow.py`, `strings.json`
- UI-based setup via Home Assistant config flow (SQLite default)
- Entity and attribute filter configuration at setup time (`exclude_domains`, `exclude_entities`, `exclude_attributes`)
- Options flow for runtime-tunable performance settings (`flush_interval`, `queue_max_size`)
- `StorageBackend` ABC with explicit transaction support (`begin`/`commit`/`rollback`)
- SQLite backend via `aiosqlite` with WAL mode, FK cascade, typed value columns
- UUIDv7 primary keys (time-ordered, no integer overflow risk)
- Ten typed value columns: `str`, `int`, `float`, `bool`, `null`, `datetime`, `date`, `time`, `timedelta`, `json`
- Validity-range deduplication: repeated values extend `last_seen` instead of inserting duplicate rows
- Batch flush: all observations in one cycle committed in a single DB transaction
- Schema migration system with `schema_version` table; existing databases without the table are adopted at version 1
- `EVENT_HOMEASSISTANT_STOP` listener for graceful shutdown flush
- `diagnostics.py` for integration diagnostics in HA
- `quality_scale.yaml` targeting Bronze quality standard
- GitHub Actions CI matrix: Python 3.12, 3.13, 3.14
- 59 automated tests covering config flow, serialization, storage, deduplication, migrations, lifecycle, filters, and graceful shutdown

[Unreleased]: https://github.com/Matzaptor/ha-logger-ext/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Matzaptor/ha-logger-ext/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Matzaptor/ha-logger-ext/releases/tag/v0.1.0
