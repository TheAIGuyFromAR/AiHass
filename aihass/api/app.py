"""FastAPI application with full OpenAPI spec for AI consumption."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from aihass import __version__
from aihass.ha_client import HAClient
from aihass.models import EntityState, ServiceCall, StateChange

logger = logging.getLogger(__name__)


# -- Request/Response models --

class ServiceCallRequest(BaseModel):
    """Request body for calling an HA service."""
    entity_id: str | list[str] | None = Field(default=None, description="Target entity ID(s)")
    data: dict[str, Any] = Field(default_factory=dict, description="Service data")


class ServiceCallResponse(BaseModel):
    success: bool
    service: str
    target: dict[str, Any] | None = None


class EntityListResponse(BaseModel):
    count: int
    entities: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    version: str
    ha_connected: bool
    entity_count: int
    area_count: int


# -- App factory --

def create_app(ha_client: HAClient) -> FastAPI:
    app = FastAPI(
        title="AiHass API",
        description=(
            "AI-native REST API for Home Assistant. "
            "Provides typed, OpenAPI-spec'd access to all HA entities, services, and history. "
            "Designed for consumption by AI agents, LLMs, and modern HTTP clients."
        ),
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Health ----

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["System"])
    async def health():
        """Health check endpoint."""
        return HealthResponse(
            status="ok",
            version=__version__,
            ha_connected=ha_client._connected.is_set(),
            entity_count=len(ha_client._state_cache),
            area_count=len(ha_client._areas),
        )

    # ---- Entities ----

    @app.get("/api/v1/entities", response_model=EntityListResponse, tags=["Entities"])
    async def list_entities(
        domain: str | None = Query(None, description="Filter by domain (light, sensor, etc.)"),
        state: str | None = Query(None, description="Filter by state value (on, off, etc.)"),
        search: str | None = Query(None, description="Search entity names and IDs"),
    ):
        """List all entities with optional filtering."""
        states = ha_client.get_states(domain=domain)

        if state:
            states = [s for s in states if s.state == state]

        if search:
            q = search.lower()
            states = [s for s in states if q in s.entity_id.lower() or q in s.friendly_name.lower()]

        entities = [
            {
                "entity_id": s.entity_id,
                "state": s.state,
                "friendly_name": s.friendly_name,
                "domain": s.domain,
                "attributes": s.attributes,
                "last_changed": s.last_changed.isoformat() if s.last_changed else None,
            }
            for s in states
        ]
        return EntityListResponse(count=len(entities), entities=entities)

    @app.get("/api/v1/entities/{entity_id}", tags=["Entities"])
    async def get_entity(entity_id: str):
        """Get full state and attributes for a single entity."""
        state = ha_client.get_state(entity_id)
        if not state:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")
        return state.model_dump(mode="json")

    @app.get("/api/v1/entities/{entity_id}/history", tags=["Entities"])
    async def get_entity_history(
        entity_id: str,
        start: str | None = Query(None, description="ISO 8601 start time"),
        end: str | None = Query(None, description="ISO 8601 end time"),
    ):
        """Get state history for an entity."""
        state = ha_client.get_state(entity_id)
        if not state:
            raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")
        history = await ha_client.get_history(entity_id, start=start, end=end)
        return {"entity_id": entity_id, "history": history}

    # ---- Services ----

    @app.post("/api/v1/services/{domain}/{service}", response_model=ServiceCallResponse, tags=["Services"])
    async def call_service(
        domain: str,
        service: str,
        body: ServiceCallRequest | None = None,
    ):
        """Call a Home Assistant service.

        Examples:
        - POST /api/v1/services/light/turn_on {"entity_id": "light.living_room", "data": {"brightness": 255}}
        - POST /api/v1/services/climate/set_temperature {"entity_id": "climate.thermostat", "data": {"temperature": 72}}
        - POST /api/v1/services/scene/turn_on {"entity_id": "scene.movie_night"}
        """
        req = body or ServiceCallRequest()
        target = {}
        if req.entity_id:
            target["entity_id"] = req.entity_id

        result = await ha_client.call_service(
            domain, service,
            target=target or None,
            service_data=req.data,
        )
        return ServiceCallResponse(success=True, service=f"{domain}.{service}", target=target or None)

    # ---- Areas ----

    @app.get("/api/v1/areas", tags=["Areas"])
    async def list_areas():
        """List all areas/rooms in the home."""
        areas = ha_client.get_areas()
        return {"count": len(areas), "areas": [a.model_dump(mode="json") for a in areas]}

    # ---- Events (SSE) ----

    @app.get("/api/v1/events/stream", tags=["Events"])
    async def event_stream(
        domain: str | None = Query(None, description="Filter events by entity domain"),
    ):
        """Server-Sent Events stream of real-time state changes.

        Connect with any SSE client to receive live updates:
            curl -N http://localhost:8100/api/v1/events/stream

        Each event is a JSON object with entity_id, old_state, new_state, and timestamp.
        """
        queue: asyncio.Queue[StateChange] = asyncio.Queue()

        async def listener(change: StateChange) -> None:
            if domain and change.entity_id.split(".")[0] != domain:
                return
            await queue.put(change)

        ha_client.on_state_change(listener)

        async def generate():
            try:
                while True:
                    change = await queue.get()
                    yield {
                        "event": "state_changed",
                        "data": json.dumps(change.model_dump(mode="json"), default=str),
                    }
            except asyncio.CancelledError:
                ha_client._listeners.remove(listener)

        return EventSourceResponse(generate())

    return app
