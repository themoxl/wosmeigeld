"""Live-Ereignisse eines KI-Laufs zwischen Worker und API (E4).

Zwei Kanaele je Lauf:

  assistant:run:<run_id>     Stromzeilen (gespeicherte mit seq, fluechtige
                             think/delta/targs ohne seq)
  assistant:cancel:<run_id>  "Abbrechen" von der API an den Worker

Gespeicherte Zeilen stehen VOR dem Veroeffentlichen in assistant_run_events
(streng steigende seq, siehe app/assistant/store.py); der Bus ist nur der
schnelle Weg. Geht dort etwas verloren (Redis-Pub/Sub merkt sich nichts),
holt der Strom-Endpunkt es aus der Datenbank nach.

RedisBus fuer den Betrieb (redis.asyncio), MemoryBus fuer Tests - auch ueber
Threads hinweg (TestClient der API laeuft in eigener Ereignisschleife).
"""

import asyncio
import contextlib
import json
import threading
import weakref
from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID

Event = dict[str, Any]


def run_channel(run_id: UUID | str) -> str:
    return f"assistant:run:{run_id}"


def cancel_channel(run_id: UUID | str) -> str:
    return f"assistant:cancel:{run_id}"


class Subscription(Protocol):
    async def get(self, timeout: float) -> Event | None:
        """Naechste Nachricht oder None nach `timeout` Sekunden."""


class EventBus(Protocol):
    async def publish(self, channel: str, event: Event) -> None: ...

    def subscribe(self, channel: str) -> contextlib.AbstractAsyncContextManager[Subscription]: ...

    async def close(self) -> None: ...


# ---------------------------------------------------------------- Speicher


class _MemorySubscription:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[Event] = asyncio.Queue()

    def deliver(self, event: Event) -> None:
        # Aus jeder Ereignisschleife und jedem Thread: zugestellt wird in der
        # Schleife des Abonnenten.
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    async def get(self, timeout: float) -> Event | None:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=max(0.0, timeout))
        except TimeoutError:
            return None


class MemoryBus:
    """Bus im Prozess (Tests). Thread-sicher."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[_MemorySubscription]] = {}
        self.published: list[tuple[str, Event]] = []

    async def publish(self, channel: str, event: Event) -> None:
        # Wie Redis: eine Kopie, nie dasselbe Objekt beim Empfaenger.
        copy = json.loads(json.dumps(event, default=str))
        with self._lock:
            self.published.append((channel, copy))
            targets = list(self._subscribers.get(channel, ()))
        for subscription in targets:
            with contextlib.suppress(RuntimeError):  # Schleife des Abonnenten ist zu
                subscription.deliver(copy)

    @contextlib.asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[Subscription]:
        subscription = _MemorySubscription()
        with self._lock:
            self._subscribers.setdefault(channel, []).append(subscription)
        try:
            yield subscription
        finally:
            with self._lock:
                listeners = self._subscribers.get(channel, [])
                if subscription in listeners:
                    listeners.remove(subscription)

    def subscriber_count(self, channel: str) -> int:
        with self._lock:
            return len(self._subscribers.get(channel, ()))

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------- Redis


class _RedisSubscription:
    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    async def get(self, timeout: float) -> Event | None:
        loop = asyncio.get_running_loop()
        until = loop.time() + max(0.0, timeout)
        while True:
            remaining = until - loop.time()
            if remaining <= 0:
                return None
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=remaining
            )
            if message is None:
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                event = json.loads(data) if isinstance(data, str) else None
            except ValueError:
                event = None
            if isinstance(event, dict):
                return event


class RedisBus:
    """Redis-Pub/Sub. Ein Client gehoert zu genau einer Ereignisschleife: der
    Worker startet je Aufgabe eine neue (asyncio.run), und Dramatiq fuehrt
    mehrere Aufgaben gleichzeitig in eigenen Threads aus. Deshalb ein Client
    je Schleife; close() schliesst nur den der laufenden."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._lock = threading.Lock()
        self._clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
            weakref.WeakKeyDictionary()
        )

    def _redis(self) -> Any:
        import redis.asyncio as redis_asyncio

        loop = asyncio.get_running_loop()
        with self._lock:
            client = self._clients.get(loop)
            if client is None:
                client = redis_asyncio.Redis.from_url(self._url)
                self._clients[loop] = client
            return client

    async def publish(self, channel: str, event: Event) -> None:
        await self._redis().publish(channel, json.dumps(event, ensure_ascii=False, default=str))

    @contextlib.asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[Subscription]:
        pubsub = self._redis().pubsub()
        await pubsub.subscribe(channel)
        try:
            yield _RedisSubscription(pubsub)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            client = self._clients.pop(loop, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Bus des Prozesses (FastAPI-Abhaengigkeit und Worker). Tests setzen
    mit set_event_bus() einen MemoryBus."""
    global _bus
    if _bus is None:
        from app.core.config import get_settings

        _bus = RedisBus(get_settings().redis_url)
    return _bus


def set_event_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus
