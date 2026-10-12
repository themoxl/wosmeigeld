from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from worker.config import get_settings

# Der Worker verbindet sich mit erweiterten Rechten (kein SET LOCAL ROLE
# authenticated/RLS-Kontext wie in services/api - er handelt nicht im Namen
# eines einzelnen HTTP-Requests, sondern verarbeitet einen bereits von der
# API household-validierten Job). Jede Abfrage filtert deshalb bewusst
# explizit nach household_id, statt sich auf RLS zu verlassen.


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)
