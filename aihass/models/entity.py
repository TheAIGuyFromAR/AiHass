"""Entity and state models — mirrors HA's entity/state structure."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EntityState(BaseModel):
    """Current state of a single HA entity."""

    entity_id: str = Field(description="Unique ID in domain.object_id format, e.g. light.living_room")
    state: str = Field(description="Current state value, e.g. 'on', 'off', '72.5'")
    attributes: dict[str, Any] = Field(default_factory=dict, description="Entity attributes from HA")
    last_changed: datetime | None = Field(default=None, description="When state last changed")
    last_updated: datetime | None = Field(default=None, description="When entity was last updated")

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def object_id(self) -> str:
        return self.entity_id.split(".", 1)[1]

    @property
    def friendly_name(self) -> str:
        return self.attributes.get("friendly_name", self.object_id.replace("_", " ").title())


class Entity(BaseModel):
    """Entity registry entry with metadata beyond just state."""

    entity_id: str
    domain: str
    friendly_name: str = ""
    area_id: str | None = None
    area_name: str | None = None
    device_class: str | None = None
    unit_of_measurement: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    current_state: EntityState | None = None


class StateChange(BaseModel):
    """Represents a state_changed event from HA."""

    entity_id: str
    old_state: EntityState | None
    new_state: EntityState | None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @property
    def changed_to(self) -> str | None:
        return self.new_state.state if self.new_state else None

    @property
    def changed_from(self) -> str | None:
        return self.old_state.state if self.old_state else None
