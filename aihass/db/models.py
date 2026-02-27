"""SQLAlchemy models for AiHass database.

Uses PostgreSQL + TimescaleDB for time-series state history.
This gives AI agents SQL access to device history — something
Home Assistant's built-in recorder makes unnecessarily painful.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EntityRecord(Base):
    """Mirrors HA entity registry — queryable metadata about every device/entity."""

    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(String, primary_key=True)
    domain: Mapped[str] = mapped_column(String, nullable=False, index=True)
    friendly_name: Mapped[str] = mapped_column(String, default="")
    area_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    device_class: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_of_measurement: Mapped[str | None] = mapped_column(String, nullable=True)
    capabilities: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_state: Mapped[str | None] = mapped_column(String, nullable=True)
    last_changed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class StateHistory(Base):
    """Time-series state history — intended for TimescaleDB hypertable.

    This replaces HA's awkward recorder with a proper time-series table
    that you can query with standard SQL:

        SELECT time, state FROM state_history
        WHERE entity_id = 'sensor.temperature'
          AND time > now() - interval '24 hours'
        ORDER BY time;
    """

    __tablename__ = "state_history"

    # Composite PK for TimescaleDB (time + entity_id)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=func.now())
    entity_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class AreaRecord(Base):
    """Physical areas/rooms in the home."""

    __tablename__ = "areas"

    area_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    floor: Mapped[str | None] = mapped_column(String, nullable=True)


class AutomationRecord(Base):
    """Automations — both AI-generated and manually created.

    Stores the original natural language prompt alongside
    the structured trigger/action config, enabling AI agents
    to understand and modify automations.
    """

    __tablename__ = "automations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String, default="user")  # 'ai' or 'user'
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)  # original NL prompt
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
