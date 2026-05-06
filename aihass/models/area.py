"""Area / room models."""

from __future__ import annotations

from pydantic import BaseModel


class Area(BaseModel):
    """A physical area/room in the home."""

    area_id: str
    name: str
    floor: str | None = None
    entity_ids: list[str] = []
