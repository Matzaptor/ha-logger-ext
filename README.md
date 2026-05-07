# ha-logger-ext

[![Tests](https://github.com/Matzaptor/ha-logger-ext/actions/workflows/tests.yml/badge.svg)](https://github.com/Matzaptor/ha-logger-ext/actions/workflows/tests.yml)

A Home Assistant custom integration that logs entity state and attribute observations into a structured database optimized for Machine Learning datasets, feature engineering, and historical analysis.

## Why this exists

Home Assistant's built-in recorder stores raw events. This integration stores **validity-range observations** instead: each record represents one value for one field (state or attribute) of one entity over a continuous time interval. Repeated observations of the same value extend the interval rather than creating duplicate rows. This produces a compact, analytics-friendly dataset.

## Installation

1. Copy `custom_components/ha_logger_ext/` into your Home Assistant `custom_components/` directory.
2. Restart Home Assistant.
3. Click the button below or go to **Settings → Devices & Services → Add Integration** and search for **HA Logger Extended**.

[![Add to My Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=ha_logger_ext)

> The button above only works after the files are already in `custom_components/`. It opens the setup wizard directly in your Home Assistant instance.

## Configuration

### Setup

| Field | Description | Default |
|---|---|---|
| Database type | `sqlite` (MySQL/PostgreSQL coming later) | `sqlite` |
| Database file path | Path to the SQLite file, relative to the HA config directory | `ha_logger_ext/ha_logger_ext.db` |
| Exclude domains | Comma-separated domains to skip (e.g. `automation,sun`) | empty |
| Exclude entities | Comma-separated entity IDs to skip | empty |
| Exclude attributes | Comma-separated attribute names to never log | empty |

### Options (adjustable after setup)

| Field | Description | Default | Range |
|---|---|---|---|
| Flush interval | Seconds between buffer flushes to the database | `30` | 5–300 |
| Queue size limit | Max observations buffered in memory before dropping | `10000` | 100–100 000 |
| Exclude domains | Override the setup-time domain filter | _(from setup)_ | |
| Exclude entities | Override the setup-time entity filter | _(from setup)_ | |
| Exclude attributes | Override the setup-time attribute filter | _(from setup)_ | |

Options can be changed at any time via **Settings → Devices & Services → HA Logger Extended → Configure**. Changing options reloads the integration automatically. Filter values set here override the values entered at setup time.

## Database schema

The integration creates and manages two tables.

### `entities`

One row per tracked entity.

| Column | Type | Description |
|---|---|---|
| `id` | BLOB (UUIDv7) | Primary key |
| `entity_id` | TEXT | e.g. `sensor.living_room_temperature` |
| `domain` | TEXT | e.g. `sensor` |
| `first_seen` | TEXT (ISO 8601 UTC) | When the entity was first observed |
| `last_seen` | TEXT (ISO 8601 UTC) | Updated on every observation |

### `observations`

One row per **validity interval** for a field (state or attribute).

| Column | Type | Description |
|---|---|---|
| `id` | BLOB (UUIDv7) | Primary key |
| `entity_pk` | BLOB | Foreign key → `entities.id` |
| `field` | TEXT | `"state"` or an attribute name |
| `value_type` | TEXT | Type discriminator (see below) |
| `value_str` | TEXT | Active when `value_type = 'str'` |
| `value_int` | INTEGER | Active when `value_type = 'int'` |
| `value_float` | REAL | Active when `value_type IN ('float', 'timedelta')` |
| `value_bool` | INTEGER | Active when `value_type = 'bool'` (0 or 1) |
| `value_datetime` | TEXT | Active when `value_type = 'datetime'` (ISO 8601 UTC) |
| `value_date` | TEXT | Active when `value_type = 'date'` (YYYY-MM-DD) |
| `value_time` | TEXT | Active when `value_type = 'time'` (HH:MM:SS[.ffffff]) |
| `value_json` | TEXT | Active when `value_type = 'json'` (lists, dicts, etc.) |
| `first_seen` | TEXT | When this value was first observed |
| `last_seen` | TEXT | Updated as long as the value stays the same |

#### Supported value types

| `value_type` | Python type | Notes |
|---|---|---|
| `str` | `str` | |
| `int` | `int` | |
| `float` | `float` | NaN and Inf are stored as `null` |
| `bool` | `bool` | |
| `null` | `None` | Also used for unavailable/unknown |
| `datetime` | `datetime.datetime` | Always normalized to UTC |
| `date` | `datetime.date` | |
| `time` | `datetime.time` | |
| `timedelta` | `datetime.timedelta` | Stored as total seconds in `value_float` |
| `json` | `list`, `dict`, other | JSON-encoded string |

Primary keys use **UUIDv7** (time-ordered), which provides natural chronological ordering and efficient B-tree indexing without integer overflow risk.

## Deduplication semantics

The integration tracks the **latest stored value** for each `(entity, field)` pair.

- If the new observation equals the latest stored value → `last_seen` is updated, no new row.
- If the value changed → a new row is inserted with a fresh `first_seen`.

Example for `sensor.temperature`:

```
time  value   result
T0    20.0  → INSERT  (first_seen=T0, last_seen=T0)
T1    20.0  → UPDATE  (last_seen=T1)
T2    21.0  → INSERT  (first_seen=T2, last_seen=T2)
T3    20.0  → INSERT  (first_seen=T3, last_seen=T3)  ← new interval, not the T0 row
```

The third `20.0` opens a new interval because it follows a different value. This preserves the full history of value transitions without duplicating unchanged periods.

## Performance

State change observations are buffered in an in-memory queue and flushed to the database in a **single transaction** per flush cycle. No I/O happens inside HA event callbacks.

If the queue fills up before a flush, new observations are dropped and a warning is logged. Increase **Queue size limit** if this happens frequently.

## Diagnostics

The integration exposes diagnostics data under **Settings → Devices & Services → HA Logger Extended → Download diagnostics**.

| Section | Key | Description |
|---|---|---|
| `config` | `db_type` | Database backend in use |
| `config` | `db_path` | Resolved path to the database file |
| `config` | `exclude_domains` | Domains excluded from logging |
| `config` | `exclude_entities` | Entities excluded from logging |
| `config` | `exclude_attributes` | Attributes excluded from logging |
| `options` | `flush_interval` | Flush interval in seconds |
| `options` | `queue_max_size` | Maximum queue depth |
| `coordinator` | `is_running` | Whether the background loop is active |
| `coordinator` | `queue_size` | Current number of buffered observations |
| `coordinator` | `flush_interval` | Effective flush interval |
| `coordinator` | `last_flush` | UTC ISO 8601 timestamp of last successful flush, or `null` |

## Development setup

```bash
# Clone the repository
git clone <repo-url>
cd ha-logger-ext

# Create a virtual environment (Python 3.12+)
python3.12 -m venv .venv
source .venv/bin/activate

# Install test dependencies
pip install -r requirements-test.txt

# Run tests
pytest tests/ -v
```

Tests use `pytest-homeassistant-custom-component` and do not require a running Home Assistant instance or a real database server.

## Known limitations

- Only SQLite is supported currently. MySQL and PostgreSQL backends are planned.
- No data retention policy yet — the database grows indefinitely.
- No export tooling yet — query the SQLite file directly with any SQL client.

## Removal

1. Go to **Settings → Devices & Services → HA Logger Extended**.
2. Click the three-dot menu → **Delete**.
3. Restart Home Assistant.
4. Optionally delete the database directory from your HA config directory (default: `ha_logger_ext/`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch naming conventions, commit message format, development setup, and the pull request checklist.

## Security

The integration runs entirely inside the Home Assistant process and stores data locally. No data is sent outside your local network. Do not commit database files, `.env` files, or HA tokens to version control.
