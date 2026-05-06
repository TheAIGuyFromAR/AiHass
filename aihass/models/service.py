"""Service call models — wraps HA's domain.service pattern."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ServiceCall(BaseModel):
    """A request to call an HA service."""

    domain: str = Field(description="Service domain, e.g. 'light', 'switch', 'climate'")
    service: str = Field(description="Service name, e.g. 'turn_on', 'turn_off', 'set_temperature'")
    target: dict[str, Any] = Field(
        default_factory=dict,
        description="Target entities/areas/devices. e.g. {'entity_id': 'light.living_room'}",
    )
    service_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional service data, e.g. {'brightness': 255, 'color_name': 'red'}",
    )

    @property
    def full_service_name(self) -> str:
        return f"{self.domain}.{self.service}"


class ServiceDescription(BaseModel):
    """Describes an available HA service and its parameters."""

    domain: str
    service: str
    name: str = ""
    description: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] | None = None
