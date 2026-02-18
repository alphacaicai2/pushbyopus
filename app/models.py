"""SQLAlchemy ORM models for the Miniflux to Discord push system.

Defines database models for category bindings, settings, delivery state,
and delivery logs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.base import Base


class CategoryBinding(Base):
    """Miniflux category to Discord webhook binding.

    Maps a Miniflux category (feed group) to a Discord webhook URL,
    with independent toggles for realtime and digest delivery modes.

    Attributes:
        id: Primary key.
        category_id: Miniflux category ID (unique).
        category_name: Display name (cached from Miniflux).
        webhook_url: Discord webhook URL for this category.
        realtime_enabled: Whether to push entries in realtime.
        digest_enabled: Whether to include entries in daily digest.
        created_at: Record creation timestamp.
        updated_at: Record last update timestamp.
    """

    __tablename__ = "category_binding"
    __table_args__ = (
        UniqueConstraint("category_id", name="uq_category_binding_category_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    category_name: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    realtime_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    digest_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<CategoryBinding(id={self.id}, category_id={self.category_id}, name='{self.category_name}')>"


class DigestSetting(Base):
    """Digest configuration singleton.

    Stores global settings for digest generation and delivery.
    Enforced as a singleton through CHECK constraint on id=1.

    Attributes:
        id: Primary key (always 1 for singleton).
        period_hours: Digest generation period in hours (12 or 24).
        summary_length: AI summary length preference.
        language_mode: Language processing mode for digest content.
        digest_time: Time of day to send digest (HH:MM format).
        timezone: Timezone for digest scheduling.
    """

    __tablename__ = "digest_setting"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_digest_setting_singleton_id"),
        CheckConstraint(
            "summary_length IN ('short', 'medium', 'long')",
            name="ck_digest_setting_summary_length",
        ),
        CheckConstraint(
            "language_mode IN ('chinese', 'english', 'title_only', 'original')",
            name="ck_digest_setting_language_mode",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        default=1,
        server_default=text("1"),
    )
    period_hours: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=24,
        server_default=text("24"),
    )
    summary_length: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="medium",
        server_default=text("'medium'"),
    )
    language_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="chinese",
        server_default=text("'chinese'"),
    )
    digest_time: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        default="08:00",
        server_default=text("'08:00'"),
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Asia/Shanghai",
        server_default=text("'Asia/Shanghai'"),
    )


class PollSetting(Base):
    """Polling configuration singleton.

    Stores global polling settings and state. Enforced as singleton.

    Attributes:
        id: Primary key (always 1 for singleton).
        interval_minutes: Polling interval in minutes.
        last_poll_at: Timestamp of the last successful poll.
        miniflux_url: Override URL for Miniflux instance.
        miniflux_token: Override API token (sensitive, handle with care).
    """

    __tablename__ = "poll_setting"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_poll_setting_singleton_id"),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        default=1,
        server_default=text("1"),
    )
    interval_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=15,
        server_default=text("15"),
    )
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    miniflux_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    miniflux_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Asia/Shanghai",
        server_default=text("'Asia/Shanghai'"),
    )


class DeliveryState(Base):
    """Last delivered entry checkpoint per category.

    Tracks the highest entry ID delivered for each category to enable
    incremental polling without duplicates.

    Attributes:
        id: Primary key.
        category_id: Miniflux category ID (unique).
        last_entry_id: Highest entry ID successfully delivered.
        last_push_at: Timestamp of the last successful delivery.
    """

    __tablename__ = "delivery_state"
    __table_args__ = (
        UniqueConstraint("category_id", name="uq_delivery_state_category_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    last_entry_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeliveryLog(Base):
    """Delivery result log for realtime and digest pushes.

    Records every delivery attempt with status for debugging and monitoring.

    Attributes:
        id: Primary key.
        entry_id: Miniflux entry ID.
        category_id: Miniflux category ID.
        feed_id: Miniflux feed ID (optional).
        title: Entry title at time of delivery.
        url: Entry URL.
        webhook_url: Discord webhook URL used.
        mode: Delivery mode ('realtime' or 'digest').
        status: Delivery status ('success' or 'failed').
        error_message: Error details if status is 'failed'.
        pushed_at: Timestamp of delivery attempt.
    """

    __tablename__ = "delivery_log"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('realtime', 'digest')",
            name="ck_delivery_log_mode",
        ),
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="ck_delivery_log_status",
        ),
        Index(
            "ix_delivery_log_category_pushed",
            "category_id",
            "pushed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    category_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    feed_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    pushed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<DeliveryLog(id={self.id}, entry_id={self.entry_id}, status='{self.status}')>"
