from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import uuid


@dataclass
class ObservationRecord:
    id: uuid.UUID
    entity_pk: uuid.UUID
    field_name: str
    value_type: str
    first_seen: datetime
    last_seen: datetime
    value_str: str | None = None
    value_int: int | None = None
    value_float: float | None = None
    value_bool: int | None = None      # 0 or 1; None when not applicable
    value_datetime: str | None = None  # ISO 8601 UTC
    value_date: str | None = None      # YYYY-MM-DD
    value_time: str | None = None      # HH:MM:SS[.ffffff]
    value_json: str | None = None      # JSON-encoded string


class StorageBackend(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def get_or_create_entity(
        self, entity_id: str, domain: str, ts: datetime
    ) -> uuid.UUID: ...

    @abstractmethod
    async def get_latest_observation(
        self, entity_pk: uuid.UUID, field_name: str
    ) -> ObservationRecord | None: ...

    @abstractmethod
    async def insert_observation(self, obs: ObservationRecord) -> None: ...

    @abstractmethod
    async def update_last_seen(self, obs_id: uuid.UUID, ts: datetime) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # --- Transaction control ---
    # Concrete backends may override these to batch multiple operations into a
    # single DB transaction. Default implementations are no-ops so that callers
    # that skip begin()/commit() still work correctly (each write auto-commits).

    async def begin(self) -> None:
        """Start an explicit transaction."""

    async def commit(self) -> None:
        """Commit the current transaction."""

    async def rollback(self) -> None:
        """Roll back the current transaction."""
