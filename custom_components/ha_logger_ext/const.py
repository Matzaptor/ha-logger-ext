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
DB_TYPE_MYSQL = "mysql"
DB_TYPE_POSTGRESQL = "postgresql"

DEFAULT_DB_TYPE = DB_TYPE_SQLITE
DEFAULT_DB_PATH = "ha_logger_ext.db"

# Filter config (stored as list[str] in config entry data)
CONF_EXCLUDE_DOMAINS = "exclude_domains"
CONF_EXCLUDE_ENTITIES = "exclude_entities"
CONF_EXCLUDE_ATTRIBUTES = "exclude_attributes"

# Performance options (stored in config entry options)
CONF_FLUSH_INTERVAL = "flush_interval"
CONF_QUEUE_MAX_SIZE = "queue_max_size"

DEFAULT_FLUSH_INTERVAL = 30       # seconds
DEFAULT_QUEUE_MAX_SIZE = 10_000   # max buffered snapshots before dropping
