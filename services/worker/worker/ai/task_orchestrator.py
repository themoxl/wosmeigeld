"""Session-Grenze fuer die generische KI-Warteschlange."""

from uuid import UUID

from worker.ai.task_processor import drain_household_queue, recover_stale_tasks
from worker.db import get_session_factory


async def drain_queue(household_id: str | UUID) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await drain_household_queue(session, UUID(str(household_id)))


async def recover_queues() -> list[UUID]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await recover_stale_tasks(session)
