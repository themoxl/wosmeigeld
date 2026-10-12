"""Attrappe des Modellservers fuer Integrationstests der KI-Aufrufstellen.

Ersetzt werden nur die beiden Netzgrenzen des KI-Kerns: die Suche nach Server,
Modell und Fenster (``llm.resolve_target``) und das Netz unter dem Gateway
(sein httpx-Client bekommt einen httpx.MockTransport). Alles dazwischen laeuft
echt und gegen die echte Datenbank: Haushaltseinstellungen, Budget aus dem
Fenster, Schema-Rueckfall, llm_calls-Zeile je Aufruf, Messung - und RLS. Genau
dort sitzen die Zusicherungen, die ein Ersatz der ganzen Aufrufstelle nicht
nachweisen koennte.
"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from app import llm
from app.core.db import get_session_factory
from app.llm import gateway, reasons
from app.llm.discovery import Target, TargetResult
from app.llm.discovery import resolve_target as _real_resolve_target
from app.llm.window import WindowInfo

BASE_URL = "http://lm.test:1234/v1"
MODEL = "test-model"
WINDOW = 32768

Reply = Callable[[dict[str, Any]], httpx.Response]


def ready_target(window: int = WINDOW) -> TargetResult:
    target = Target(
        base_url=BASE_URL,
        model=MODEL,
        window=WindowInfo(loaded=window, cap=None, effective=window, source="/api/v1/models"),
    )
    return TargetResult(target=target, base_url=BASE_URL, models=[], detailed=True)


def unavailable_target() -> TargetResult:
    return TargetResult(
        target=None,
        base_url=None,
        models=[],
        detailed=False,
        reason_code=reasons.MODEL_UNAVAILABLE,
        message=reasons.describe(reasons.MODEL_UNAVAILABLE, "Keine der Adressen antwortet."),
    )


def answer(
    content: str | dict[str, Any],
    *,
    finish_reason: str = "stop",
    reasoning: str | None = None,
    prompt_tokens: int = 600,
) -> httpx.Response:
    """HTTP-200-Antwort im OpenAI-Format. Ein dict wird als JSON-Text gesendet."""
    text_content = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    message: dict[str, Any] = {"role": "assistant", "content": text_content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": finish_reason, "message": message}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 40},
        },
    )


def server_error(status: int = 500, message: str = "Modell abgestuerzt") -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message}})


class _HttpxForGateway:
    """Das httpx-Modul, wie der Gateway es sieht - nur dass jeder Client, den
    er sich baut, die Attrappe als Transport bekommt. Alles andere (Timeout,
    Ausnahmen) ist das echte httpx."""

    def __init__(self, transport: httpx.MockTransport) -> None:
        self._transport = transport

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)

    def AsyncClient(self, *args: Any, **kwargs: Any) -> httpx.AsyncClient:  # noqa: N802
        kwargs["transport"] = self._transport
        return httpx.AsyncClient(*args, **kwargs)


class ModelStub:
    """Modellserver aus Sicht des Gateways. ``reply`` bekommt den gesendeten
    Anfragekoerper und liefert die Antwort; ``bodies`` haelt alle Koerper fest."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        reply: Reply,
        target: TargetResult | None = None,
    ) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.discoveries = 0
        found = target or ready_target()

        async def fake_resolve(settings: Any, *, client: Any = None) -> TargetResult:
            if not settings.is_enabled:
                # Wie das Original: KI aus -> ai_disabled ohne Serverkontakt,
                # also auch keine Discovery.
                return await _real_resolve_target(settings)
            self.discoveries += 1
            return found

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.bodies.append(body)
            return reply(body)

        monkeypatch.setattr(llm, "resolve_target", fake_resolve)
        monkeypatch.setattr(gateway, "httpx", _HttpxForGateway(httpx.MockTransport(handle)))

    def user_prompts(self) -> list[str]:
        return [str(body["messages"][-1]["content"]) for body in self.bodies]


def user_prompt_of(body: dict[str, Any]) -> str:
    return str(body["messages"][-1]["content"])


async def llm_calls(household_id: str | UUID, stage: str) -> list[dict[str, Any]]:
    """Protokollzeilen eines Haushalts und einer Stufe, aelteste zuerst
    (ohne RLS gelesen - die Tests pruefen, was wirklich gespeichert ist)."""
    async with get_session_factory()() as session:
        rows = await session.execute(
            text(
                "select status, reason_code, finish_reason, error_message, max_tokens_sent, "
                "window_tokens, model_name, prompt_version from llm_calls "
                "where household_id = :household_id and stage = :stage order by created_at"
            ),
            {"household_id": str(household_id), "stage": stage},
        )
        return [dict(row._mapping) for row in rows]
