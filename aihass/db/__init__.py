"""Database layer — PostgreSQL + TimescaleDB for queryable time-series state history."""

from .engine import get_engine, init_db
from .models import Base, EntityRecord, StateHistory, AreaRecord, AutomationRecord

__all__ = [
    "AreaRecord",
    "AutomationRecord",
    "Base",
    "EntityRecord",
    "StateHistory",
    "get_engine",
    "init_db",
]
