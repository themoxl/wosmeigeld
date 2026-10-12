"""NDJSON-Strom eines KI-Laufs (GET /v1/assistant/runs/{id}/stream).

Ablauf je Verbindung (Vertrag, Abschnitt "Strom"):

1. ZUERST den Kanal des Laufs abonnieren, dann aus der Datenbank ab `after`
   nachspielen - so geht zwischen Nachspielen und Live nichts verloren.
2. Live: gespeicherte Zeilen (mit seq) werden entdoppelt (seq <= zuletzt
   gesendet faellt weg); eine Luecke wird aus der Datenbank aufgefuellt.
   Fluechtige Zeilen (think/delta/targs) gehen unveraendert durch.
3. Alle 10 s ein Ping - zugleich der Moment, in dem die Datenbank auf
   Zeilen geprueft wird, die der Bus verloren haben koennte.
4. Nach done/error ist Schluss. Ein schon fertiger Lauf spielt bis dahin nach
   und schliesst.

Die Sitzungen des Generators sind eigene (kurze) mit den RLS-Rechten des
Nutzers: die Sitzung der Abhaengigkeit ist beim Streamen schon geschlossen.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.assistant import store
from app.assistant.events import EventBus, Subscription, run_channel

logger = logging.getLogger(__name__)

# fest: Ping-Abstand des Stroms (Vertrag: alle 10 s; der Client gibt nach 35 s Stille auf)
PING_SECONDS = 10.0

Event = dict[str, Any]


def line(event: Event) -> bytes:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    return (payload + "\n").encode("utf-8")


class _NoSubscription:
    """Ersatz, wenn der Bus nicht erreichbar ist: dann traegt allein die
    Datenbank (geprueft bei jedem Ping)."""

    async def get(self, timeout: float) -> Event | None:
        await asyncio.sleep(max(0.0, timeout))
        return None


@contextlib.asynccontextmanager
async def _subscribe(bus: EventBus, channel: str) -> AsyncIterator[Subscription]:
    try:
        manager = bus.subscribe(channel)
        subscription = await manager.__aenter__()
    except Exception:  # noqa: BLE001 - ohne Bus bleibt die Datenbank
        logger.warning("Kanal %s nicht abonniert - Strom nur aus der Datenbank", channel)
        yield _NoSubscription()
        return
    try:
        yield subscription
    finally:
        with contextlib.suppress(Exception):
            await manager.__aexit__(None, None, None)


async def run_stream(
    *,
    run_id: UUID,
    household_id: UUID,
    user_id: UUID,
    after: int,
    bus: EventBus,
    session_factory: async_sessionmaker[AsyncSession],
    ping_seconds: float = PING_SECONDS,
) -> AsyncIterator[bytes]:
    """Zeilen des Stroms als Bytes (eine JSON-Zeile je Ereignis)."""
    last = max(0, after)

    async def stored_after(seq: int) -> list[Event]:
        async with store.user_session(
            session_factory, user_id=user_id, household_id=household_id
        ) as session:
            return await store.events_after(session, run_id, household_id, seq)

    async with _subscribe(bus, run_channel(run_id)) as subscription:
        for event in await stored_after(last):
            last = int(event["seq"])
            yield line(event)
            if store.is_final(event):
                return
        next_ping = time.monotonic() + ping_seconds
        while True:
            message = await subscription.get(next_ping - time.monotonic())
            if message is None:
                for event in await stored_after(last):
                    last = int(event["seq"])
                    yield line(event)
                    if store.is_final(event):
                        return
                yield line({"type": "ping"})
                next_ping = time.monotonic() + ping_seconds
                continue
            seq = message.get("seq")
            if not isinstance(seq, int):
                yield line(message)
                continue
            if seq <= last:
                continue
            if seq > last + 1:
                # Luecke (verlorene Bus-Nachricht): aus der Datenbank, in
                # Reihenfolge - die gerade gekommene Zeile ist dort schon
                # gespeichert (veroeffentlicht wird erst nach dem Commit).
                for event in await stored_after(last):
                    last = int(event["seq"])
                    yield line(event)
                    if store.is_final(event):
                        return
                continue
            last = seq
            yield line(message)
            if store.is_final(message):
                return
