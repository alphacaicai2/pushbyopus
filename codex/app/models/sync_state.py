"""
Sync state model for tracking polling cursors.

This model stores key-value state data used by the polling mechanism
to track the last processed entry or synchronization point.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from sqlmodel import Field, SQLModel

# Maximum lengths for database fields
KEY_MAX_LENGTH: Final[int] = 128
VALUE_MAX_LENGTH: Final[int] = 2048


def _utcnow() -> datetime:
    """Get current UTC datetime with timezone info."""
    return datetime.now(timezone.utc)


class SyncState(SQLModel, table=True):
    """
    Synchronization state for tracking polling cursors.

    This table stores arbitrary key-value pairs used to track the state
    of various synchronization processes. For example, it can store the
    last processed entry ID from Miniflux to avoid reprocessing entries.

    Attributes:
        key: Unique identifier for the state (e.g., "miniflux_last_entry_id").
        value: The state value (e.g., entry ID as string, timestamp, etc.).
        updated_at: Timestamp of the last update (auto-updated on modification).

    Example:
        ```python
        # Store last processed entry ID
        sync = SyncState(key="miniflux_last_entry_id", value="12345")

        # Store last sync timestamp
        sync = SyncState(key="last_full_sync", value="2024-01-15T10:30:00Z")
        ```
    """

    __tablename__ = "sync_state"

    key: str = Field(
        primary_key=True,
        max_length=KEY_MAX_LENGTH,
        description="Unique identifier for the sync state",
    )
    value: str = Field(
        nullable=False,
        max_length=VALUE_MAX_LENGTH,
        description="State value (entry ID, timestamp, etc.)",
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column_kwargs={
            "onupdate": _utcnow,
            "nullable": False,
        },
        description="Timestamp of the last update",
    )

    def touch(self) -> None:
        """Update the updated_at timestamp to current time."""
        self.updated_at = _utcnow()
