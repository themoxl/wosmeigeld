from uuid import UUID

from worker.ai.processor import process_classification_run as _process_run
from worker.db import get_session_factory


async def process_classification_run(run_id: str | UUID) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await _process_run(session, UUID(str(run_id)))
