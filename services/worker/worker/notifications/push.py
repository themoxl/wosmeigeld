"""Versand von Push-Benachrichtigungen ueber die Expo Push API.

Der mobile Client spricht Expos Push-API nie direkt an - er registriert nur
seinen Token bei der BFF-API (POST /v1/devices/push-token). Nur der Worker
sendet tatsaechlich, analog zu "kein Service-Key im Client"
(Architekturplan Abschnitt 5).
"""

import logging
from typing import Any, Literal, TypedDict

import httpx

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
# Expo akzeptiert maximal 100 Nachrichten je Request.
_BATCH_SIZE = 100


class PushMessage(TypedDict):
    to: str
    title: str
    body: str
    data: dict[str, Any]


class PushTicket(TypedDict, total=False):
    status: Literal["ok", "error"]
    id: str
    message: str
    details: dict[str, Any]


async def send_expo_push_batch(messages: list[PushMessage]) -> list[PushTicket]:
    """Sendet Nachrichten in Batches zu je maximal 100 und gibt die
    Expo-Tickets in derselben Reihenfolge wie `messages` zurueck (ein
    Ticket-Eintrag mit status="error" und details.error="DeviceNotRegistered"
    markiert einen toten Token - das Invalidieren dieses Tokens ist als
    Fast-Follow vorgesehen, nicht Teil der ersten Version)."""
    tickets: list[PushTicket] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=4.0)) as client:
        for start in range(0, len(messages), _BATCH_SIZE):
            batch = messages[start : start + _BATCH_SIZE]
            try:
                response = await client.post(
                    EXPO_PUSH_URL,
                    json=batch,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                tickets.extend(payload.get("data", []))
            except httpx.HTTPError as exc:
                logger.error("Expo-Push-Batch fehlgeschlagen: %s", exc)
                tickets.extend(PushTicket(status="error", message=str(exc)) for _ in batch)
    return tickets
