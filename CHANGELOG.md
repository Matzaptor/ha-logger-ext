# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.1.6] — 2026-07-08

### Added

- `import_from_recorder`/`import_from_external_db` now log an INFO-level heartbeat at
  most once every 30 seconds while working through a single entity's intervals. A
  high-cardinality entity (e.g. a noisy BLE/GPS tracker whose value changes on nearly
  every reading, defeating RLE compression) can have hundreds of thousands of
  intervals, each needing its own sequential read+write round trip against the
  destination database — that can legitimately take hours, and previously produced
  nothing in the log between the "processing X" line at the start and the
  "X — N inserted, M skipped" line at the very end. Confirmed live: a single
  `device_tracker` entity with 95,281 states in one day-sized chunk ran for 3+ hours
  with no log output at all, indistinguishable from a genuine hang even though it was
  actually still working (no timeout ever fired, since each individual query was
  completing normally — it was simply an enormous number of them, one at a time). The
  heartbeat is time-based, not count-based, so it stays silent for normally sized
  entities and never goes fully quiet regardless of how large one entity turns out to be.

---

## [2.1.5] — 2026-07-07

### Fixed

- **Critical: the live recording flush loop and an in-progress import could corrupt
  each other's database connection.** All three storage backends (`SQLiteBackend`,
  `MySQLBackend`, `PostgreSQLBackend`) keep the current transaction on shared instance
  state (`self._conn` / `self._in_transaction`). The periodic flush loop and the import
  service both hold a reference to the *same* backend instance and run concurrently —
  if the flush loop had a transaction open via `begin()` at the same moment an import
  call ran, the import call would see `self._in_transaction == True` and reuse the
  flush's connection, believing it already owned it. For MySQL this corrupted the wire
  protocol outright (`RuntimeError: readexactly() called while another coroutine is
  already waiting for incoming data`), which desynced the connection for everything
  that followed, cascading into a wave of unrelated `AssertionError`s in
  `get_or_create_entity` for the live flush's own writes. For SQLite/PostgreSQL the
  same race is more insidious: a write can silently end up committed or rolled back as
  a side effect of a transaction it was never part of.
  All three backends now serialize access with an `asyncio.Lock`, held for the whole
  `begin()...commit()`/`rollback()` span. Only the task that itself opened the
  transaction may bypass the lock for its own follow-up calls (avoiding
  self-deadlock); every other caller waits its turn.

---

## [2.1.4] — 2026-07-07

### Fixed

- Audited every `except` block in the integration for silent failures. Two were
  found: `RecorderImporter._fetch_all_entity_ids()` and `_fetch_earliest_state_time()`
  silently returned an empty result (`[]` / `None`) if the `homeassistant.components.recorder`
  import failed, with no log line at all — reported identically to a genuinely empty
  recorder. Both now log an ERROR first, matching the sibling `_fetch_states()` method,
  which already did this correctly.
- `MySQLBackend`'s index-creation step caught *any* exception and unconditionally
  logged it at DEBUG as "may already exist" — including real failures (e.g. permission
  denied) that have nothing to do with the index already being there. It now checks the
  actual MySQL error code (1061, duplicate key name) and only stays quiet for that
  specific, expected case; anything else is now logged at WARNING.

---

## [2.1.3] — 2026-07-07

### Fixed

- **Critical: `MySQLBackend` leaked a pooled connection on every non-transactional
  write.** `_execute()` acquired a connection with `pool.acquire()` and released it
  with a direct `conn.close()` instead of `pool.release(conn)`. Closing a
  pool-acquired connection directly does not tell the pool it's free — the pool's
  internal accounting keeps counting it as in use, permanently shrinking available
  capacity by one on every call. `begin()`/`commit()`/`rollback()` (the live
  recording flush transaction) had the identical bug. With the default pool size,
  the pool would fully exhaust after roughly 10 such calls — at which point the next
  `pool.acquire()` call blocked forever, with **no timeout protecting it** (2.1.1
  only wrapped the query itself, not acquiring the connection to run it on) and no
  query ever reaching the server to explain why. This is what a real stuck import
  looked like: confirmed live against MariaDB, `SHOW FULL PROCESSLIST` showed nothing
  on either server, because the code never got far enough to send a query at all.
  `_execute()`, `begin()`, `commit()`, and `rollback()` now all properly acquire
  through a shared helper and release back to the pool via `pool.release()`.
- Acquiring a connection from the pool is now itself wrapped in a 10s timeout, so a
  genuinely exhausted pool (leaked or otherwise) raises a clear, loggable
  `TimeoutError` instead of hanging forever.
- `PostgreSQLBackend` did not have this bug — its `commit()`/`rollback()` already used
  `pool.release()` correctly, and its query timeout was already covered.

---

## [2.1.2] — 2026-07-07

### Fixed

- **Critical:** the periodic live-recording flush loop (`RecorderCoordinator._flush_loop`)
  called `_flush()` with no exception handling at all. `_flush()` in turn called
  `backend.begin()` unguarded. Before 2.1.1's timeout fixes, a dead backend connection
  during `begin()` would just hang the loop; after 2.1.1, the same dead connection now
  raises `TimeoutError` — which propagated straight out of `_flush_loop()`'s `while True`
  and killed the background task outright. Since nothing awaits that task, the only trace
  was a generic, easy-to-miss asyncio "Task exception was never retrieved" warning — live
  recording would then silently stop forever until the next Home Assistant restart.
  `_flush_loop()` now catches any exception from `_flush()` and retries on the next
  interval; `_flush()` itself now guards `begin()` (re-queueing the pending snapshots for
  the next cycle on failure, dropping and warning only if the queue is full) and
  `rollback()` (so a broken connection on the way out of a failed commit can no longer
  compound into an unhandled exception).
- `MySQLBackend.close()`, `PostgreSQLBackend.close()`, and both MySQL/PostgreSQL external
  reader `close()` methods now also time out (10s) instead of potentially hanging forever
  themselves — a stuck close() could otherwise block Home Assistant shutdown/unload or
  mask whatever error the caller was already handling in a `finally` block.

---

## [2.1.1] — 2026-07-07

### Fixed

- The 2.1.0 network-timeout fix only covered the *read* side of `import_from_external_db`
  (the external MySQL/PostgreSQL readers). The *write* side — `MySQLBackend` and
  `PostgreSQLBackend`, this integration's own storage backends, used by every import and
  by the live recording flush — had the identical gap: no connect timeout, no query
  timeout. A dead connection or network blip while writing observations (not just while
  reading from an external source) hung the import (and potentially live recording)
  forever, with no error and no visible query anywhere. `MySQLBackend` now sets a 10s
  connect timeout on its pool and wraps every query in a 300s timeout; `PostgreSQLBackend`
  sets the same via `asyncpg`'s native `timeout`/`command_timeout` pool options.

---

## [2.1.0] — 2026-07-07

### Fixed

- `import_from_external_db` log lines were always prefixed `import_from_recorder:`,
  even when that service — not `import_from_recorder` — was the one running. Both
  importers now log under a prefix that matches the service actually in use.

### Added

- An additional INFO-level progress line is now logged every 25 entities processed
  within a single day-sized chunk, so a calendar day with unusually heavy volume
  shows visible forward progress instead of going quiet between the once-per-chunk
  summary lines.
- Failures while fetching a chunk's states or importing one entity are now logged
  with full context (chunk date range, entity ID) before the error propagates, so
  an import failure is never silent.
- `import_from_external_db`'s MySQL and PostgreSQL readers now enforce a 10s
  connect timeout and a 300s per-query timeout. Neither `aiomysql` nor `asyncpg`
  times out by default, so a dead server or a network blip during a long-running
  query previously hung the import task forever with no error and no query visible
  on either database engine. DEBUG-level connect/close/fetch logging (row and
  entity counts, never credentials) was also added across all three external
  readers (SQLite, MySQL, PostgreSQL).
- `import_from_recorder` and `import_from_external_db` now reject a second call
  while one is already running, with a clear `ServiceValidationError`, instead of
  letting two imports race against the same backend. The guard reuses the existing
  `coordinator.is_importing` state (the same flag behind the *Import in progress*
  sensor) as the lock.

---

## [2.0.0] — 2026-07-07

### Changed

- **Breaking:** project renamed from `ha-logger-ext` ("HA Logger Extended") to
  `ha-recorder-ext` ("HA External Recorder"). The integration's actual role is an
  external, alternative recorder for Home Assistant — not a "logger" — and `ext`
  stands for "external", not "extended", since the exported data is not ML-only
  (Power BI and other analytics consumers are also in scope). The Home Assistant
  `DOMAIN` changed from `ha_logger_ext` to `ha_recorder_ext`; existing config
  entries do not migrate automatically. No migration path is provided, since no
  installations of the previous name existed at the time of this rename.
- The internal coordinator class `LoggerCoordinator` is renamed to
  `RecorderCoordinator` for the same reason.
- `import_from_recorder` / `import_from_external_db`: history import chunk size
  reduced from 7 days to 1 day. This bounds the worst-case single-query wait when
  importing from a large external database (the raw SQL path's `ORDER BY` over a
  joined table is often not covered by the recorder's default indexes, so large
  chunks could trigger an expensive filesort with no visible progress) and reduces
  wasted work if a restart interrupts an in-progress import.
- Added a per-chunk INFO-level progress log line (cumulative inserted/skipped
  counts) for `import_from_recorder`/`import_from_external_db`, so long-running
  imports show visible progress without needing debug logging.

---

## [1.2.0] — 2026-07-02

### Added

A second import service, `import_from_external_db`, brings in history from an external database holding a backup of a Home Assistant Recorder installation, not just the one attached to this HA instance. It works with SQLite, MySQL, or PostgreSQL as the source and is safe to run more than once over the same data.

---

## [1.1.3] — 2026-05-24

### Added

- `import_from_recorder` now emits per-entity structured log messages at DEBUG level;
  grep by entity ID to see exactly how many intervals were inserted or already present
- Per-chunk log downgraded from INFO to DEBUG; overall start/completion summary stays at INFO
- 6 new `caplog`-based tests covering INFO and DEBUG paths, including idempotency and empty-chunk edge cases

---

## [1.1.2] — 2026-05-07

### Fixed

- `import_from_recorder`: newer HA versions raise `ValueError: entity_ids must be provided` when `None` is passed to `get_significant_states`; the importer now resolves `entity_ids=None` by querying the recorder for all distinct entity IDs before entering the chunk loop

---

## [1.1.1] — 2026-05-07

### Fixed

- `duckdb`, `aiomysql`, `asyncpg` removed from mandatory `requirements` in `manifest.json`; only `aiosqlite` is installed automatically — resolves startup hang on Python 3.14 / ARM64 (e.g. HA Green) where no pre-built wheel exists for duckdb

### Changed

- CI: Python 3.14 re-added to the test matrix; DuckDB tests skipped automatically when the package is unavailable; optional dependencies split into `requirements-test-optional.txt`

---

## [1.1.0] — 2026-05-07

### Added

- `import_from_recorder` HA service: imports historical entity states from the built-in Recorder database into ha_logger_ext
  - 7-day sliding window chunks to keep memory footprint constant on large databases
  - RLE compression: consecutive equal values are merged into a single validity interval
  - Idempotent: existing observations detected via `has_observations_in_range` and skipped
  - Resumable: re-running after a crash skips already-imported time ranges and continues from the first gap
  - Attributes tracked per-field alongside state
  - Parameters: `start_date` (defaults to oldest recorder state), `end_date` (defaults to now), `entity_ids` (all optional)
  - Progress logged at INFO level
- `has_observations_in_range(entity_pk, field_name, start, end) -> bool` on `StorageBackend` ABC, implemented in all four backends (SQLite, DuckDB, MySQL, PostgreSQL)
- `services.yaml` with full field descriptions for the HA Developer tools UI
- 22 new tests: RLE unit tests, integration tests with real SQLite backend and mocked recorder, overlap detection, `None` start time handling
- Two `binary_sensor` entities exposed per config entry:
  - **Recording** (`mdi:database-clock`): `on` while the coordinator flush loop is running; attributes: `queue_size`, `last_flush`, `flush_interval_seconds`
  - **Import in progress** (`mdi:database-import`): `on` while `import_from_recorder` is running; attribute `last_import_intervals_inserted` available after completion
- Both sensors update in push mode via a coordinator listener mechanism (no polling)
- Integration's own entities are auto-excluded from being logged by the coordinator
- 11 new tests for binary sensors (141 total)

---

## [Unreleased]

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

[Unreleased]: https://github.com/Matzaptor/ha-recorder-ext/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/Matzaptor/ha-recorder-ext/compare/v1.2.0...v2.0.0
[1.2.0]: https://github.com/Matzaptor/ha-logger-ext/compare/v1.1.3...v1.2.0
[1.1.3]: https://github.com/Matzaptor/ha-logger-ext/compare/v1.1.2...v1.1.3
[1.1.2]: https://github.com/Matzaptor/ha-logger-ext/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/Matzaptor/ha-logger-ext/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/Matzaptor/ha-logger-ext/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Matzaptor/ha-logger-ext/compare/v0.3.0...v1.0.0
[0.3.0]: https://github.com/Matzaptor/ha-logger-ext/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Matzaptor/ha-logger-ext/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Matzaptor/ha-logger-ext/releases/tag/v0.1.0
