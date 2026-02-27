"""WebSocket + REST client for Home Assistant.

Maintains a persistent WebSocket connection to HA Core, handles authentication,
subscribes to state changes, and provides methods for service calls and state queries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

import aiohttp

from aihass.config import get_settings
from aihass.models import EntityState, StateChange, Area, ServiceDescription

logger = logging.getLogger(__name__)

# HA WebSocket message types
MSG_TYPE_AUTH_REQUIRED = "auth_required"
MSG_TYPE_AUTH_OK = "auth_ok"
MSG_TYPE_AUTH_INVALID = "auth_invalid"
MSG_TYPE_RESULT = "result"
MSG_TYPE_EVENT = "event"


class HAClient:
    """Async client that bridges to a running Home Assistant instance.

    Handles:
    - WebSocket connection with auto-reconnect
    - Authentication via long-lived access token
    - State subscriptions and caching
    - Service calls
    - REST API fallback for bulk operations
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
    ) -> None:
        settings = get_settings()
        self._url = (url or settings.hass_url).rstrip("/")
        self._token = token or settings.hass_token
        self._ws_url = self._url.replace("http", "ws", 1) + "/api/websocket"
        self._rest_url = self._url + "/api"

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._state_cache: dict[str, EntityState] = {}
        self._areas: dict[str, Area] = {}
        self._services: dict[str, ServiceDescription] = {}
        self._listeners: list[Callable[[StateChange], Coroutine]] = []
        self._listen_task: asyncio.Task | None = None
        self._connected = asyncio.Event()

    # -- Lifecycle --

    async def connect(self) -> None:
        """Establish WebSocket connection and authenticate."""
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self._ws_url)
        except aiohttp.ClientError as exc:
            await self._session.close()
            raise ConnectionError(f"Cannot reach Home Assistant at {self._ws_url}") from exc

        # HA sends auth_required immediately
        auth_msg = await self._ws.receive_json()
        if auth_msg.get("type") != MSG_TYPE_AUTH_REQUIRED:
            raise ConnectionError(f"Unexpected first message: {auth_msg}")

        # Send auth token
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        auth_result = await self._ws.receive_json()
        if auth_result.get("type") == MSG_TYPE_AUTH_INVALID:
            await self.disconnect()
            raise PermissionError(f"Authentication failed: {auth_result.get('message', 'invalid token')}")

        logger.info("Connected to Home Assistant at %s (version %s)", self._url, auth_result.get("ha_version"))
        self._connected.set()

        # Start the message listener loop
        self._listen_task = asyncio.create_task(self._listen_loop())

        # Initial data load
        await self._load_states()
        await self._load_areas()
        await self._subscribe_events()

    async def disconnect(self) -> None:
        """Cleanly close the connection."""
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        self._connected.clear()
        logger.info("Disconnected from Home Assistant")

    async def reconnect(self) -> None:
        """Reconnect with exponential backoff."""
        settings = get_settings()
        delay = settings.ha_reconnect_base_delay
        retries = 0
        while True:
            try:
                await self.disconnect()
                await self.connect()
                logger.info("Reconnected successfully")
                return
            except (ConnectionError, aiohttp.ClientError) as exc:
                retries += 1
                max_retries = settings.ha_reconnect_max_retries
                if max_retries and retries >= max_retries:
                    raise ConnectionError(f"Failed to reconnect after {retries} attempts") from exc
                logger.warning("Reconnect attempt %d failed, retrying in %.1fs: %s", retries, delay, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, settings.ha_reconnect_max_delay)

    # -- Message handling --

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send_command(self, msg_type: str, **kwargs: Any) -> Any:
        """Send a WS command and wait for the result."""
        if not self._ws or self._ws.closed:
            raise ConnectionError("Not connected to Home Assistant")

        msg_id = self._next_id()
        payload = {"id": msg_id, "type": msg_type, **kwargs}
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        await self._ws.send_json(payload)
        return await future

    async def _listen_loop(self) -> None:
        """Background task that reads WS messages and dispatches them."""
        assert self._ws is not None
        try:
            async for raw_msg in self._ws:
                if raw_msg.type == aiohttp.WSMsgType.TEXT:
                    msg = raw_msg.json()
                    await self._handle_message(msg)
                elif raw_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.warning("WebSocket closed/error, attempting reconnect")
                    asyncio.create_task(self.reconnect())
                    return
        except asyncio.CancelledError:
            return

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")
        msg_id = msg.get("id")

        if msg_type == MSG_TYPE_RESULT and msg_id in self._pending:
            future = self._pending.pop(msg_id)
            if msg.get("success"):
                future.set_result(msg.get("result"))
            else:
                future.set_exception(RuntimeError(f"HA error: {msg.get('error')}"))

        elif msg_type == MSG_TYPE_EVENT:
            event = msg.get("event", {})
            if event.get("event_type") == "state_changed":
                await self._handle_state_changed(event.get("data", {}))

    async def _handle_state_changed(self, data: dict[str, Any]) -> None:
        """Process a state_changed event: update cache and notify listeners."""
        entity_id = data.get("entity_id", "")
        new_state_data = data.get("new_state")
        old_state_data = data.get("old_state")

        new_state = EntityState(**new_state_data) if new_state_data else None
        old_state = EntityState(**old_state_data) if old_state_data else None

        # Update cache
        if new_state:
            self._state_cache[entity_id] = new_state
        elif entity_id in self._state_cache:
            del self._state_cache[entity_id]

        # Notify listeners
        change = StateChange(entity_id=entity_id, old_state=old_state, new_state=new_state)
        for listener in self._listeners:
            try:
                await listener(change)
            except Exception:
                logger.exception("Error in state change listener")

    # -- Data loading --

    async def _load_states(self) -> None:
        """Load all current entity states into cache."""
        result = await self._send_command("get_states")
        for state_data in result:
            entity_id = state_data.get("entity_id", "")
            self._state_cache[entity_id] = EntityState(**state_data)
        logger.info("Loaded %d entity states", len(self._state_cache))

    async def _load_areas(self) -> None:
        """Load area registry."""
        result = await self._send_command("config/area_registry/list")
        for area_data in result:
            area = Area(
                area_id=area_data["area_id"],
                name=area_data["name"],
                floor=area_data.get("floor_id"),
            )
            self._areas[area.area_id] = area
        logger.info("Loaded %d areas", len(self._areas))

    async def _subscribe_events(self) -> None:
        """Subscribe to state_changed events."""
        await self._send_command("subscribe_events", event_type="state_changed")
        logger.info("Subscribed to state_changed events")

    # -- Public API --

    def on_state_change(self, callback: Callable[[StateChange], Coroutine]) -> None:
        """Register a callback for state changes."""
        self._listeners.append(callback)

    def get_states(self, domain: str | None = None) -> list[EntityState]:
        """Get all cached entity states, optionally filtered by domain."""
        states = list(self._state_cache.values())
        if domain:
            states = [s for s in states if s.domain == domain]
        return states

    def get_state(self, entity_id: str) -> EntityState | None:
        """Get a single entity's state from cache."""
        return self._state_cache.get(entity_id)

    def get_areas(self) -> list[Area]:
        """Get all areas."""
        return list(self._areas.values())

    async def call_service(
        self,
        domain: str,
        service: str,
        target: dict[str, Any] | None = None,
        service_data: dict[str, Any] | None = None,
    ) -> Any:
        """Call an HA service.

        Examples:
            await client.call_service("light", "turn_on",
                target={"entity_id": "light.living_room"},
                service_data={"brightness": 255})

            await client.call_service("climate", "set_temperature",
                target={"entity_id": "climate.thermostat"},
                service_data={"temperature": 72})
        """
        payload: dict[str, Any] = {"domain": domain, "service": service}
        if target:
            payload["target"] = target
        if service_data:
            payload["service_data"] = service_data
        return await self._send_command("call_service", **payload)

    async def get_services(self) -> dict[str, Any]:
        """Get all available services from HA."""
        return await self._send_command("get_services")

    async def get_config(self) -> dict[str, Any]:
        """Get HA configuration."""
        return await self._send_command("get_config")

    # -- REST API helpers (for operations better suited to HTTP) --

    async def rest_get(self, path: str) -> Any:
        """Make a GET request to the HA REST API."""
        if not self._session:
            raise ConnectionError("Not connected")
        headers = {"Authorization": f"Bearer {self._token}"}
        async with self._session.get(f"{self._rest_url}/{path}", headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def rest_post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """Make a POST request to the HA REST API."""
        if not self._session:
            raise ConnectionError("Not connected")
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        async with self._session.post(f"{self._rest_url}/{path}", headers=headers, json=data or {}) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_history(
        self,
        entity_id: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get state history for an entity via REST API.

        Args:
            entity_id: Entity to query history for.
            start: ISO timestamp for start of range (default: 24h ago).
            end: ISO timestamp for end of range (default: now).
        """
        path = f"history/period"
        if start:
            path += f"/{start}"
        params = f"filter_entity_id={entity_id}&minimal_response"
        if end:
            params += f"&end_time={end}"
        return await self.rest_get(f"{path}?{params}")

    # -- Context manager --

    async def __aenter__(self) -> HAClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()
