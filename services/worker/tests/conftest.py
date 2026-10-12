"""Muss vor jedem Testmodul-Import laufen, analog zu
services/api/tests/conftest.py: worker.config.get_settings()/worker.db.get_engine()
sind @lru_cache-dekoriert."""

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres"
)

# Dasselbe Aufraeumen wie in der API-Suite, siehe
# packages/test-fixtures/dbcleanup.py: die Worker-Tests legen ihre Haushalte
# zwar direkt per SQL an statt ueber die API, hinterlassen sie aber genauso.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "test-fixtures"))
from dbcleanup import (  # noqa: E402, F401
    _cleanup_connection,
    entferne_testdaten,
    testlauf_exklusiv,
)

if sys.platform == "win32":
    # psycopg (async) kann nicht mit Windows' Default-ProactorEventLoop
    # arbeiten (siehe worker/actors.py fuer dieselbe Begruendung).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
