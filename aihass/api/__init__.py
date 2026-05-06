"""FastAPI REST layer — OpenAPI-spec'd interface to Home Assistant."""

from .app import create_app

__all__ = ["create_app"]
