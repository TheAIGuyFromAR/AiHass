"""MCP server that exposes Home Assistant entities, services, and history as tools.

This is the primary interface for AI agents (Claude, Cursor, Conductor, etc.)
to interact with the smart home.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.types import (
    Resource,
    TextContent,
    Tool,
)

from aihass.ha_client import HAClient

logger = logging.getLogger(__name__)


def create_mcp_server(ha_client: HAClient) -> Server:
    """Create an MCP server wired to a live HA client."""

    server = Server("aihass")

    # ---- Tools ----

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_entities",
                description=(
                    "List all Home Assistant entities, optionally filtered by domain. "
                    "Domains include: light, switch, sensor, binary_sensor, climate, "
                    "lock, cover, media_player, fan, vacuum, camera, automation, scene, script."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "Filter by entity domain (e.g. 'light', 'sensor', 'climate'). Omit for all.",
                        },
                        "area": {
                            "type": "string",
                            "description": "Filter by area/room name (e.g. 'Living Room', 'Kitchen').",
                        },
                        "state": {
                            "type": "string",
                            "description": "Filter by current state value (e.g. 'on', 'off', 'home').",
                        },
                    },
                },
            ),
            Tool(
                name="get_entity_state",
                description="Get the current state and all attributes of a specific entity.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "The entity ID, e.g. 'light.living_room' or 'sensor.outdoor_temperature'.",
                        },
                    },
                    "required": ["entity_id"],
                },
            ),
            Tool(
                name="call_service",
                description=(
                    "Call a Home Assistant service to control devices. "
                    "Examples: turn on/off lights, set thermostat temperature, lock/unlock doors, "
                    "play media, open/close covers, activate scenes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "Service domain: light, switch, climate, lock, cover, media_player, scene, script, etc.",
                        },
                        "service": {
                            "type": "string",
                            "description": "Service name: turn_on, turn_off, toggle, set_temperature, lock, unlock, open_cover, close_cover, etc.",
                        },
                        "entity_id": {
                            "type": "string",
                            "description": "Target entity ID, e.g. 'light.living_room'. Can be comma-separated for multiple.",
                        },
                        "data": {
                            "type": "object",
                            "description": "Additional service data. Examples: {\"brightness\": 255}, {\"temperature\": 72}, {\"color_name\": \"red\"}",
                        },
                    },
                    "required": ["domain", "service"],
                },
            ),
            Tool(
                name="get_areas",
                description="List all areas/rooms in the home with their entities.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_history",
                description=(
                    "Get state history for an entity over a time period. "
                    "Useful for answering questions like 'what was the temperature last night?' "
                    "or 'how long was the door open?'"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "Entity to get history for.",
                        },
                        "start": {
                            "type": "string",
                            "description": "ISO 8601 start time (default: 24h ago).",
                        },
                        "end": {
                            "type": "string",
                            "description": "ISO 8601 end time (default: now).",
                        },
                    },
                    "required": ["entity_id"],
                },
            ),
            Tool(
                name="search_entities",
                description=(
                    "Search for entities by name, area, or device class. "
                    "Useful when you don't know the exact entity_id. "
                    "Example: search for 'temperature' to find all temperature sensors."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search term to match against entity names, IDs, and areas.",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="batch_service_call",
                description=(
                    "Call a service on multiple entities at once. "
                    "Example: turn off all lights, or set all thermostats to 68."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "Service domain."},
                        "service": {"type": "string", "description": "Service name."},
                        "entity_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of entity IDs to target.",
                        },
                        "data": {
                            "type": "object",
                            "description": "Service data applied to all targets.",
                        },
                    },
                    "required": ["domain", "service", "entity_ids"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        args = arguments or {}

        if name == "get_entities":
            return _format(await _get_entities(ha_client, args))
        elif name == "get_entity_state":
            return _format(await _get_entity_state(ha_client, args))
        elif name == "call_service":
            return _format(await _call_service(ha_client, args))
        elif name == "get_areas":
            return _format(await _get_areas(ha_client))
        elif name == "get_history":
            return _format(await _get_history(ha_client, args))
        elif name == "search_entities":
            return _format(await _search_entities(ha_client, args))
        elif name == "batch_service_call":
            return _format(await _batch_service_call(ha_client, args))
        else:
            return _format({"error": f"Unknown tool: {name}"})

    # ---- Resources ----

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri="hass://entities",
                name="All Entities",
                description="Complete list of all Home Assistant entities and their current states.",
                mimeType="application/json",
            ),
            Resource(
                uri="hass://areas",
                name="Areas",
                description="All areas/rooms in the home.",
                mimeType="application/json",
            ),
            Resource(
                uri="hass://config",
                name="HA Configuration",
                description="Home Assistant configuration summary.",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        if uri == "hass://entities":
            states = ha_client.get_states()
            return json.dumps([s.model_dump(mode="json") for s in states], indent=2)
        elif uri == "hass://areas":
            areas = ha_client.get_areas()
            return json.dumps([a.model_dump(mode="json") for a in areas], indent=2)
        elif uri == "hass://config":
            config = await ha_client.get_config()
            return json.dumps(config, indent=2)
        else:
            return json.dumps({"error": f"Unknown resource: {uri}"})

    return server


# ---- Tool implementations ----


async def _get_entities(client: HAClient, args: dict[str, Any]) -> list[dict]:
    domain = args.get("domain")
    area_filter = args.get("area")
    state_filter = args.get("state")

    states = client.get_states(domain=domain)

    if state_filter:
        states = [s for s in states if s.state == state_filter]

    if area_filter:
        area_lower = area_filter.lower()
        areas = client.get_areas()
        matching_area_ids = {a.area_id for a in areas if area_lower in a.name.lower()}
        # TODO: filter by area once entity→area mapping is loaded
        # For now, filter by friendly_name containing area name
        states = [s for s in states if area_lower in s.friendly_name.lower()]

    return [
        {
            "entity_id": s.entity_id,
            "state": s.state,
            "friendly_name": s.friendly_name,
            "domain": s.domain,
            "last_changed": s.last_changed.isoformat() if s.last_changed else None,
        }
        for s in states
    ]


async def _get_entity_state(client: HAClient, args: dict[str, Any]) -> dict:
    entity_id = args["entity_id"]
    state = client.get_state(entity_id)
    if not state:
        return {"error": f"Entity '{entity_id}' not found"}
    return state.model_dump(mode="json")


async def _call_service(client: HAClient, args: dict[str, Any]) -> dict:
    domain = args["domain"]
    service = args["service"]
    entity_id = args.get("entity_id")
    data = args.get("data", {})

    target = {}
    if entity_id:
        # Support comma-separated entity IDs
        ids = [e.strip() for e in entity_id.split(",")]
        target["entity_id"] = ids if len(ids) > 1 else ids[0]

    result = await client.call_service(domain, service, target=target or None, service_data=data)
    return {"success": True, "service": f"{domain}.{service}", "target": target, "result": result}


async def _get_areas(client: HAClient) -> list[dict]:
    areas = client.get_areas()
    return [a.model_dump(mode="json") for a in areas]


async def _get_history(client: HAClient, args: dict[str, Any]) -> list:
    return await client.get_history(
        entity_id=args["entity_id"],
        start=args.get("start"),
        end=args.get("end"),
    )


async def _search_entities(client: HAClient, args: dict[str, Any]) -> list[dict]:
    query = args["query"].lower()
    states = client.get_states()
    matches = []
    for s in states:
        searchable = f"{s.entity_id} {s.friendly_name} {s.attributes.get('device_class', '')}".lower()
        if query in searchable:
            matches.append({
                "entity_id": s.entity_id,
                "state": s.state,
                "friendly_name": s.friendly_name,
                "domain": s.domain,
            })
    return matches


async def _batch_service_call(client: HAClient, args: dict[str, Any]) -> dict:
    domain = args["domain"]
    service = args["service"]
    entity_ids = args["entity_ids"]
    data = args.get("data", {})

    result = await client.call_service(
        domain, service,
        target={"entity_id": entity_ids},
        service_data=data,
    )
    return {
        "success": True,
        "service": f"{domain}.{service}",
        "targets": entity_ids,
        "count": len(entity_ids),
        "result": result,
    }


def _format(data: Any) -> list[TextContent]:
    """Format tool output as MCP TextContent."""
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]
