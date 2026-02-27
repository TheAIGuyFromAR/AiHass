"""State sync — pipes HA state changes into the database for queryable history."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aihass.ha_client import HAClient
from aihass.models import StateChange
from .engine import get_engine
from .models import EntityRecord, StateHistory

logger = logging.getLogger(__name__)


class StateSync:
    """Listens to HA state changes and persists them to PostgreSQL."""

    def __init__(self, ha_client: HAClient) -> None:
        self._ha_client = ha_client
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def start(self) -> None:
        engine = get_engine()
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._ha_client.on_state_change(self._on_state_change)

        # Initial sync of all current states
        await self._sync_all_states()
        logger.info("StateSync started")

    async def _sync_all_states(self) -> None:
        """Upsert all current HA entity states into the entities table."""
        assert self._session_factory is not None
        states = self._ha_client.get_states()

        async with self._session_factory() as session:
            for state in states:
                stmt = insert(EntityRecord).values(
                    entity_id=state.entity_id,
                    domain=state.domain,
                    friendly_name=state.friendly_name,
                    last_state=state.state,
                    last_changed=state.last_changed,
                    attributes=state.attributes,
                ).on_conflict_do_update(
                    index_elements=["entity_id"],
                    set_={
                        "last_state": state.state,
                        "last_changed": state.last_changed,
                        "attributes": state.attributes,
                        "friendly_name": state.friendly_name,
                    },
                )
                await session.execute(stmt)
            await session.commit()

        logger.info("Synced %d entity states to database", len(states))

    async def _on_state_change(self, change: StateChange) -> None:
        """Handle a state change: update entity record + insert history row."""
        if not self._session_factory or not change.new_state:
            return

        try:
            async with self._session_factory() as session:
                # Update entity record
                stmt = insert(EntityRecord).values(
                    entity_id=change.entity_id,
                    domain=change.new_state.domain,
                    friendly_name=change.new_state.friendly_name,
                    last_state=change.new_state.state,
                    last_changed=change.new_state.last_changed,
                    attributes=change.new_state.attributes,
                ).on_conflict_do_update(
                    index_elements=["entity_id"],
                    set_={
                        "last_state": change.new_state.state,
                        "last_changed": change.new_state.last_changed,
                        "attributes": change.new_state.attributes,
                        "friendly_name": change.new_state.friendly_name,
                    },
                )
                await session.execute(stmt)

                # Insert history row
                history = StateHistory(
                    time=change.new_state.last_changed or datetime.utcnow(),
                    entity_id=change.entity_id,
                    state=change.new_state.state,
                    attributes=change.new_state.attributes,
                )
                session.add(history)
                await session.commit()
        except Exception:
            logger.exception("Failed to sync state change for %s", change.entity_id)
