from __future__ import annotations

DOMAIN = "ha_logger_ext"

# Database config
CONF_DB_TYPE = "db_type"
CONF_DB_PATH = "db_path"
CONF_DB_HOST = "db_host"
CONF_DB_PORT = "db_port"
CONF_DB_NAME = "db_name"
CONF_DB_USERNAME = "db_username"
CONF_DB_PASSWORD = "db_password"

DB_TYPE_SQLITE = "sqlite"
DB_TYPE_DUCKDB = "duckdb"
DB_TYPE_MYSQL = "mysql"
DB_TYPE_POSTGRESQL = "postgresql"

# Backends that use a local file path (no server needed)
DB_TYPE_EMBEDDED = (DB_TYPE_SQLITE, DB_TYPE_DUCKDB)
# Backends that require server connection parameters
DB_TYPE_SERVER = (DB_TYPE_MYSQL, DB_TYPE_POSTGRESQL)

DEFAULT_DB_TYPE = DB_TYPE_SQLITE
DEFAULT_DB_PATH = "ha_logger_ext/ha_logger_ext.db"
DEFAULT_DUCKDB_PATH = "ha_logger_ext/ha_logger_ext.duckdb"
DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_NAME = "ha_logger_ext"
DEFAULT_MYSQL_PORT = 3306
DEFAULT_POSTGRESQL_PORT = 5432

# Filter config (stored as list[str] in config entry data)
CONF_EXCLUDE_DOMAINS = "exclude_domains"
CONF_EXCLUDE_ENTITIES = "exclude_entities"
CONF_EXCLUDE_ATTRIBUTES = "exclude_attributes"

# Performance options (stored in config entry options)
CONF_FLUSH_INTERVAL = "flush_interval"
CONF_QUEUE_MAX_SIZE = "queue_max_size"

DEFAULT_FLUSH_INTERVAL = 30       # seconds
DEFAULT_QUEUE_MAX_SIZE = 10_000   # max buffered snapshots before dropping

# Services
SERVICE_IMPORT_FROM_RECORDER = "import_from_recorder"
SERVICE_IMPORT_FROM_EXTERNAL_DB = "import_from_external_db"
