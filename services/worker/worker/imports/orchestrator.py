import base64
from uuid import UUID

from worker.db import get_session_factory
from worker.imports.processor import process_import_run as _process_run


async def process_import_run(import_run_id: str | UUID, filename: str, file_b64: str) -> None:
    file_bytes = base64.b64decode(file_b64)
    session_factory = get_session_factory()
    async with session_factory() as session:
        await _process_run(session, UUID(str(import_run_id)), file_bytes)
