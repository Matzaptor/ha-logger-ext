# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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

[Unreleased]: https://github.com/Matzaptor/ha-logger-ext/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Matzaptor/ha-logger-ext/releases/tag/v0.1.0
