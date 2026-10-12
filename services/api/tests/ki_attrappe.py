"""Attrappe fuer Tests von KI-Aufrufstellen: echter Gateway, falscher Modellserver.

Ersetzt werden genau zwei Dinge: die Discovery (`llm.resolve_target` liefert
ein Ziel mit bekanntem Fenster, ohne LM Studio zu fragen - bei ausgeschalteter
KI wie das Original ai_disabled) und der HTTP-Weg zum Modellserver
(`httpx.MockTransport`). Budgetrechnung, Schema-Rueckfall,
Reasoning-Abbildung, benannte Gruende und die llm_calls-Zeilen laufen echt -
dort sitzen die Zusicherungen des KI-Kerns, die ein Mock von `_ask_model`
nicht nachweisen koennte.

Kein Testmodul (Name ohne `test_`), sondern ein Helfer fuer mehrere.
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
from app.llm.discovery import resolve_target as _real_resolve_target
from app.llm.gateway import chat_structured as _real_chat_structured

WINDOW = 32_768
BASE_URL = "http://127.0.0.1:1234/v1"
MODEL = "test-model"

Reply = httpx.Response | Exception


def target_result(
    *, window: int | None = WINDOW, capability: llm.ReasoningCapability | None = None
) -> llm.TargetResult:
    """Ziel mit geladenem Fenster - oder, mit window=None, mit unbekanntem
    Fenster (model_context_unknown), wie resolve_target es dann meldet."""
    info = llm.WindowInfo(
        loaded=window,
        cap=None,
        effective=window,
        source="/api/v1/models" if window else None,
        reason=None if window else "LM Studio hat „test-model“ gerade nicht geladen.",
        reasoning_capability=capability
        or llm.ReasoningCapability(kind="switchable", allowed_options=("off", "on")),
    )
    target = llm.Target(base_url=BASE_URL, model=MODEL, window=info)
    if window is None:
        return llm.TargetResult(
            target=target,
            base_url=BASE_URL,
            models=[],
            detailed=True,
            reason_code=reasons.MODEL_CONTEXT_UNKNOWN,
            message=reasons.describe(reasons.MODEL_CONTEXT_UNKNOWN, info.reason),
        )
    return llm.TargetResult(target=target, base_url=BASE_URL, models=[], detailed=True)


def offline() -> llm.TargetResult:
    """Kein Modellserver erreichbar."""
    return llm.TargetResult(
        target=None,
        base_url=None,
        models=[],
        detailed=False,
        reason_code=reasons.MODEL_UNAVAILABLE,
        message=reasons.describe(
            reasons.MODEL_UNAVAILABLE, f"Keine der Adressen antwortet: {BASE_URL}."
        ),
    )


def completion(
    content: str = "",
    *,
    reasoning: str = "",
    finish_reason: str = "stop",
    prompt_tokens: int | None = None,
) -> httpx.Response:
    """Antwort im OpenAI-Format, wie LM Studio sie liefert."""
    message: dict[str, Any] = {"content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    payload: dict[str, Any] = {"choices": [{"finish_reason": finish_reason, "message": message}]}
    if prompt_tokens is not None:
        payload["usage"] = {"prompt_tokens": prompt_tokens, "completion_tokens": 40}
    return httpx.Response(200, json=payload)


class ModelServer:
    """Falscher OpenAI-kompatibler Server. Merkt sich jeden Anfragekoerper;
    `reply` bekommt den Koerper und liefert eine Antwort oder eine Ausnahme
    (z. B. httpx.ReadTimeout fuer ein abgelaufenes Zeitbudget)."""

    def __init__(self, reply: Callable[[dict[str, Any]], Reply]) -> None:
        self.reply = reply
        self.bodies: list[dict[str, Any]] = []

    @property
    def user_prompts(self) -> list[str]:
        return [str(body["messages"][-1]["content"]) for body in self.bodies]

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        answer = self.reply(body)
        if isinstance(answer, Exception):
            raise answer
        return answer


def json_server(answer: Callable[[str], Any] | Any) -> ModelServer:
    """Server, der als Inhalt das JSON von `answer` liefert - fest oder als
    Funktion ueber den Nutzerprompt. None ergibt eine leere Antwort."""

    def reply(body: dict[str, Any]) -> Reply:
        value = answer(str(body["messages"][-1]["content"])) if callable(answer) else answer
        return completion("" if value is None else json.dumps(value, ensure_ascii=False))

    return ModelServer(reply)


def install(
    monkeypatch: pytest.MonkeyPatch,
    server: ModelServer,
    found: llm.TargetResult | None = None,
) -> ModelServer:
    """Discovery und Modellserver ersetzen; der Gateway selbst bleibt echt."""
    result = found or target_result()

    async def fake_resolve(
        settings: llm.LLMSettings, *, client: httpx.AsyncClient | None = None
    ) -> llm.TargetResult:
        if not settings.is_enabled:
            # Das Original antwortet dann ohne jeden Serverkontakt.
            return await _real_resolve_target(settings)
        return result

    async def via_server(request: llm.ChatRequest, **kwargs: Any) -> llm.ChatResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(server.handle)) as client:
            kwargs["client"] = client
            return await _real_chat_structured(request, **kwargs)

    monkeypatch.setattr(llm, "resolve_target", fake_resolve)
    monkeypatch.setattr(llm, "chat_structured", via_server)
    # Die Antwortpruefung des Kerns (llm.answers.ask) ruft den Gateway ueber
    # sein Modul.
    monkeypatch.setattr(gateway, "chat_structured", via_server)
    return server


async def llm_calls(household_id: str | UUID) -> list[Any]:
    """llm_calls-Zeilen des Haushalts in Schreibreihenfolge (ohne RLS)."""
    async with get_session_factory()() as session:
        result = await session.execute(
            text(
                "select stage, status, reason_code, prompt_version, max_tokens_sent, "
                "window_tokens, reasoning_sent, error_message from llm_calls "
                "where household_id = cast(:household_id as uuid) order by created_at"
            ),
            {"household_id": str(household_id)},
        )
        return list(result)
