"""CLI entry point for AiHass.

Usage:
    aihass serve       # Run API server + MCP server + DB sync
    aihass mcp         # Run only the MCP server (stdio transport for Claude/Cursor)
    aihass api         # Run only the FastAPI server
    aihass db init     # Initialize the database schema
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("aihass")


def main() -> None:
    parser = argparse.ArgumentParser(prog="aihass", description="AI-native Home Assistant bridge")
    sub = parser.add_subparsers(dest="command")

    # serve — full stack
    serve_parser = sub.add_parser("serve", help="Run the full stack (API + MCP + DB sync)")
    serve_parser.add_argument("--api-port", type=int, default=None)
    serve_parser.add_argument("--mcp-transport", choices=["stdio", "sse"], default=None)

    # mcp — MCP server only
    mcp_parser = sub.add_parser("mcp", help="Run MCP server (stdio transport for Claude/Cursor)")
    mcp_parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")

    # api — REST API only
    api_parser = sub.add_parser("api", help="Run FastAPI REST server")
    api_parser.add_argument("--port", type=int, default=None)
    api_parser.add_argument("--host", type=str, default=None)

    # db — database commands
    db_parser = sub.add_parser("db", help="Database management")
    db_sub = db_parser.add_subparsers(dest="db_command")
    db_sub.add_parser("init", help="Initialize database schema")

    args = parser.parse_args()

    if args.command == "serve":
        asyncio.run(run_serve(args))
    elif args.command == "mcp":
        asyncio.run(run_mcp(args))
    elif args.command == "api":
        asyncio.run(run_api(args))
    elif args.command == "db":
        if args.db_command == "init":
            asyncio.run(run_db_init())
        else:
            db_parser.print_help()
    else:
        parser.print_help()


async def run_serve(args: argparse.Namespace) -> None:
    """Run the full stack: connect to HA, start DB sync, start API server."""
    import uvicorn
    from aihass.config import get_settings
    from aihass.ha_client import HAClient
    from aihass.api import create_app
    from aihass.db import init_db
    from aihass.db.sync import StateSync

    settings = get_settings()
    logger.info("Starting AiHass full stack")

    # Connect to Home Assistant
    ha_client = HAClient()
    await ha_client.connect()

    # Initialize database
    await init_db()

    # Start state sync
    sync = StateSync(ha_client)
    await sync.start()

    # Create and run API server
    app = create_app(ha_client)
    port = args.api_port or settings.api_port
    host = settings.api_host

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info("API server starting on %s:%d", host, port)
    logger.info("OpenAPI docs at http://%s:%d/docs", host, port)

    try:
        await server.serve()
    finally:
        await ha_client.disconnect()


async def run_mcp(args: argparse.Namespace) -> None:
    """Run just the MCP server for Claude Desktop / Cursor integration."""
    from mcp.server.stdio import stdio_server
    from aihass.ha_client import HAClient
    from aihass.mcp import create_mcp_server

    logger.info("Starting AiHass MCP server (transport: %s)", args.transport)

    ha_client = HAClient()
    await ha_client.connect()

    server = create_mcp_server(ha_client)

    if args.transport == "stdio":
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    else:
        # SSE transport
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route
        import uvicorn

        sse = SseServerTransport("/messages")

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())

        starlette_app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages", endpoint=sse.handle_post_message, methods=["POST"]),
        ])

        config = uvicorn.Config(starlette_app, host="0.0.0.0", port=args.port or 8101)
        uv_server = uvicorn.Server(config)
        await uv_server.serve()

    await ha_client.disconnect()


async def run_api(args: argparse.Namespace) -> None:
    """Run just the FastAPI REST server."""
    import uvicorn
    from aihass.config import get_settings
    from aihass.ha_client import HAClient
    from aihass.api import create_app

    settings = get_settings()
    ha_client = HAClient()
    await ha_client.connect()

    app = create_app(ha_client)
    port = args.port or settings.api_port
    host = args.host or settings.api_host

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        await ha_client.disconnect()


async def run_db_init() -> None:
    """Initialize database schema."""
    from aihass.db import init_db
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized successfully")


if __name__ == "__main__":
    main()
