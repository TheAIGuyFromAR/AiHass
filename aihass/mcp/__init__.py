"""MCP server — exposes Home Assistant to Claude, Cursor, and other MCP clients."""

from .server import create_mcp_server

__all__ = ["create_mcp_server"]
