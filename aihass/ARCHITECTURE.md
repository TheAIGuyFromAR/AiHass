# AiHass — AI-Native Home Assistant Bridge

## Design Philosophy

AiHass is **not a replacement** for Home Assistant. It's an AI-native interoperability
layer that sits on top of HA, preserving full compatibility with HA's 2000+ integrations
while exposing them through modern, AI-friendly interfaces.

```
┌─────────────────────────────────────────────────────────┐
│                    AI Consumers                          │
│  Claude (MCP) │ Cursor │ OpenWebUI │ LiteLLM │ Conductor│
├─────────────────────────────────────────────────────────┤
│                   AiHass Layer                           │
│  ┌───────────┐  ┌───────────┐  ┌──────────────────┐    │
│  │ MCP Server │  │ FastAPI   │  │ Event Stream     │    │
│  │ (tools +   │  │ (OpenAPI  │  │ (SSE + WebSocket │    │
│  │  resources)│  │  spec)    │  │  fan-out)        │    │
│  └─────┬─────┘  └─────┬─────┘  └────────┬─────────┘    │
│        │              │                  │               │
│  ┌─────┴──────────────┴──────────────────┴─────────┐    │
│  │              Unified State Manager               │    │
│  │  - Entity registry (mirrors HA)                  │    │
│  │  - State cache (in-memory, sub-ms reads)         │    │
│  │  - Change detection & diffing                    │    │
│  └─────────────────────┬───────────────────────────┘    │
│                        │                                 │
│  ┌─────────────────────┴───────────────────────────┐    │
│  │              HA Client (WebSocket + REST)         │    │
│  │  - Persistent WS connection to HA Core            │    │
│  │  - Auth via long-lived access tokens              │    │
│  │  - Auto-reconnect with backoff                    │    │
│  └─────────────────────┬───────────────────────────┘    │
│                        │                                 │
│  ┌─────────────────────┴───────────────────────────┐    │
│  │              Data Layer (PostgreSQL)              │    │
│  │  - TimescaleDB for time-series sensor data        │    │
│  │  - Entity metadata & relationships               │    │
│  │  - Automation history & audit log                 │    │
│  │  - SQL-queryable by AI agents                     │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Home Assistant Core                         │
│  2000+ integrations: Zigbee, Z-Wave, Matter, MQTT,      │
│  Hue, Sonos, Tesla, Ecobee, Ring, Unifi, ESPHome...     │
└─────────────────────────────────────────────────────────┘
```

## Compatibility Strategy

### What we keep from HA (via bridge):
- **All device integrations** — every sensor, switch, light, lock, etc.
- **Entity ID format** — `domain.object_id` (e.g., `light.living_room`)
- **Service calls** — `domain.service` with service data
- **Event model** — `state_changed`, `automation_triggered`, etc.
- **Add-on ecosystem** — HACS custom components still work (they run in HA)

### What we add:
- **MCP Server** — Claude/LLMs can directly control devices via tool-use
- **OpenAPI REST** — auto-generated clients, proper schemas, typed responses
- **SQL access** — query device history with plain SQL, not HA's weird API
- **SSE event stream** — subscribe to real-time state changes without WebSocket complexity
- **Batch operations** — control multiple devices in one call
- **Natural language automations** — describe what you want, AI generates the logic
- **Structured entity metadata** — areas, floors, device types, capabilities as first-class data

### What we DON'T do:
- We don't replace HA Core — it keeps running, managing devices
- We don't rewrite integrations — HA's integration library is the whole point
- We don't fork the HA frontend — build new UIs on top of our API instead
- We don't require migration — connect to an existing HA instance and go

## Key Interfaces

### 1. MCP Server (for Claude, Cursor, Conductor)

Tools exposed:
- `get_entities` — list/filter entities by domain, area, state
- `get_entity_state` — get current state + attributes of an entity
- `call_service` — invoke any HA service (turn on lights, lock doors, etc.)
- `get_history` — query state history with SQL-like filters
- `get_areas` — list areas/rooms in the home
- `create_automation` — define automations from natural language descriptions
- `query_data` — run SQL queries against the time-series database

Resources exposed:
- `hass://entities` — live entity registry
- `hass://config` — HA configuration summary
- `hass://areas` — area/floor layout
- `hass://automations` — current automation definitions

### 2. FastAPI REST (for any HTTP client)

```
GET    /api/v1/entities                    # list all entities
GET    /api/v1/entities/{entity_id}        # get entity state
GET    /api/v1/entities/{entity_id}/history # time-series history
POST   /api/v1/services/{domain}/{service} # call a service
GET    /api/v1/areas                       # list areas
GET    /api/v1/events/stream               # SSE event stream
POST   /api/v1/automations                 # create automation
POST   /api/v1/query                       # SQL query endpoint
GET    /api/v1/health                      # health check
```

All endpoints return typed JSON with proper HTTP status codes and OpenAPI schemas.

### 3. Database Schema (PostgreSQL + TimescaleDB)

```sql
-- Core entity registry (mirrors HA)
CREATE TABLE entities (
    entity_id       TEXT PRIMARY KEY,
    domain          TEXT NOT NULL,
    friendly_name   TEXT,
    area_id         TEXT REFERENCES areas(area_id),
    device_class    TEXT,
    unit_of_measurement TEXT,
    capabilities    JSONB,
    last_state      TEXT,
    last_changed    TIMESTAMPTZ,
    attributes      JSONB
);

-- Time-series state history (TimescaleDB hypertable)
CREATE TABLE state_history (
    time            TIMESTAMPTZ NOT NULL,
    entity_id       TEXT NOT NULL,
    state           TEXT,
    attributes      JSONB
);
SELECT create_hypertable('state_history', 'time');

-- Areas / rooms
CREATE TABLE areas (
    area_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    floor           TEXT
);

-- Automations (AI-generated or manual)
CREATE TABLE automations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    description     TEXT,
    trigger_config  JSONB NOT NULL,
    action_config   JSONB NOT NULL,
    created_by      TEXT,  -- 'ai' or 'user'
    prompt          TEXT,  -- original natural language prompt if AI-generated
    enabled         BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

## Running

```bash
# Set environment variables
export HASS_URL=http://homeassistant.local:8123
export HASS_TOKEN=your_long_lived_access_token
export DATABASE_URL=postgresql://user:pass@localhost/aihass

# Run the full stack
aihass serve

# Run just the MCP server (for Claude Desktop / Cursor)
aihass mcp

# Run just the API server
aihass api --port 8100
```
