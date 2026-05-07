# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
