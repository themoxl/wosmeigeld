"""Antwortpruefung des KI-Kerns (app/llm/answers.py) und der KI-Schalter in
resolve_target - ohne Datenbank, mit falschem Modellserver.

Die Aufrufstellen (Mapping, Dubletten, Vertragsabgleich, Kostenschaetzung,
Kategorien, Lebensmittel, Empfehlungen, Gesundheitsdokumente) pruefen ihre
Antworten nicht mehr selbst. Was hier steht, gilt deshalb fuer alle:

* abgeschnitten (finish_reason=length) ist nie eine Antwort - auch nicht, wenn
  der Anfang wie fertiges JSON aussieht, und auch nicht aus dem Denktext;
* JSON aus dem Denktext nur aus derselben, NICHT abgeschnittenen Antwort, und
  die Protokollzeile sagt das;
* ein Vertragsbruch markiert die Zeile desselben Aufrufs;
* kein zweiter Aufruf, ausser dem bewussten Schema-Rueckfall.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.llm import answers, gateway, reasons
from app.llm.calls_log import CallScope
from app.llm.discovery import Target, resolve_target
from app.llm.gateway import ChatRequest, ChatResult
from app.llm.settings import LLMSettings
from app.llm.window import ReasoningCapability, WindowInfo

_real_chat_structured = gateway.chat_structured


def _settings(**overrides: Any) -> LLMSettings:
    values: dict[str, Any] = {
        "is_enabled": True,
        "min_confidence": 0.85,
        "custom_base_url": None,
        "model_name": None,
        "temperature": 1.0,
        "request_timeout_seconds": 30.0,
        "answer_reserve_share": 0.25,
        "result_page_share": 0.1,
        "run_time_limit_minutes": 20,
        "start_chars_per_token": 3.0,
        "start_estimate_error": 0.15,
        "context_line_enabled": True,
    }
    values.update(overrides)
    return LLMSettings(**values)


def _ready(session: AsyncMock) -> answers.Ready:
    target = Target(
        base_url="http://lm.test:1234/v1",
        model="test-model",
        window=WindowInfo(
            loaded=8192,
            cap=None,
            effective=8192,
            source="/api/v1/models",
            reasoning_capability=ReasoningCapability(),
        ),
    )
    scope = CallScope(session=session, household_id=uuid4(), stage="test", prompt_version="t1")
    return answers.Ready(_settings(), target, scope)


def _session() -> AsyncMock:
    async def execute(statement: Any, params: Any = None, *args: Any, **kwargs: Any) -> MagicMock:
        result = MagicMock()
        result.first.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute)
    return session


def _updates(session: AsyncMock) -> list[dict[str, Any]]:
    return [
        call.args[1]
        for call in session.execute.await_args_list
        if str(getattr(call.args[0], "text", call.args[0])).startswith("update llm_calls")
    ]


def _response(content: str, *, reasoning: str = "", finish: str = "stop") -> httpx.Response:
    message: dict[str, Any] = {"content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return httpx.Response(
        200, json={"choices": [{"finish_reason": finish, "message": message}]}
    )


class _Server:
    """Falscher Modellserver: Antworten der Reihe nach, gesendete Koerper."""

    def __init__(self) -> None:
        self.replies: list[httpx.Response] = []
        self.sent: list[dict[str, Any]] = []

    def append(self, reply: httpx.Response) -> None:
        self.replies.append(reply)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        return self.replies[len(self.sent) - 1]


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch) -> _Server:
    fake = _Server()

    async def via_server(request: ChatRequest, **kwargs: Any) -> ChatResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
            kwargs["client"] = client
            return await _real_chat_structured(request, **kwargs)

    # answers.ask ruft den Gateway ueber sein Modul - hier wird ersetzt.
    monkeypatch.setattr(gateway, "chat_structured", via_server)
    return fake


def _request() -> ChatRequest:
    return ChatRequest(
        messages=[{"role": "user", "content": "Frage"}],
        response_format=answers.json_schema_format("probe", {"type": "object"}),
    )


def _verdict(obj: dict[str, Any]) -> str | None:
    value = obj.get("urteil")
    return value if isinstance(value, str) else None


async def test_a_truncated_answer_is_never_accepted_even_if_it_parses(
    server: _Server,
) -> None:
    session = _session()
    server.append(_response('{"urteil": "ja"}', finish="length"))

    answer = await answers.ask(_ready(session), _request(), accept=_verdict)

    assert answer.value is None
    assert answer.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    assert "finish_reason=length" in (answer.message or "")
    [update] = _updates(session)
    assert (update["status"], update["reason"]) == ("error", reasons.MODEL_BUDGET_EXHAUSTED)
    assert len(server.sent) == 1


async def test_json_from_a_truncated_reasoning_text_is_a_draft(
    server: _Server,
) -> None:
    """Ein abgebrochener Denktext ist ein Entwurf - auch ein darin
    vollstaendiges Objekt wird nicht uebernommen."""
    session = _session()
    server.append(
        _response("", reasoning='{"urteil": "ja"} ... noch einmal pruefen', finish="length")
    )

    answer = await answers.ask(_ready(session), _request(), accept=_verdict)

    assert answer.value is None
    assert answer.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    assert _updates(session) == []


async def test_the_last_matching_object_of_a_finished_reasoning_text_is_rescued(
    server: _Server,
) -> None:
    session = _session()
    server.append(
        _response("", reasoning='Entwurf {"urteil": "nein"} - endgueltig: {"urteil": "ja"}')
    )

    answer = await answers.ask(_ready(session), _request(), accept=_verdict)

    assert answer.value == "ja"
    assert answer.rescued is True
    [update] = _updates(session)
    assert update["status"] == "success" and "Denktext" in update["err"]


async def test_without_a_contract_only_the_whole_reasoning_span_counts(
    server: _Server,
) -> None:
    """Ohne Vertrag (Text-Aufrufer) waere ein Teilobjekt aus einem
    Zwischenstand geraten - es gilt nur die ganze Spanne."""
    session = _session()
    server.append(_response("", reasoning='Ich liste: {"items": [{"name": "Mehl"}]}'))
    rescued = await answers.ask(_ready(session), _request())
    assert rescued.ok and json.loads(rescued.text) == {"items": [{"name": "Mehl"}]}

    session = _session()
    server.append(_response("", reasoning='{"a": 1} und dann {"b": 2}'))
    unusable = await answers.ask(_ready(session), _request())
    assert unusable.reason_code == reasons.MODEL_OUTPUT_UNUSABLE
    assert _updates(session) == []


async def test_a_contract_breach_marks_the_same_call(server: _Server) -> None:
    session = _session()
    server.append(_response('{"anders": true}'))

    answer = await answers.ask(
        _ready(session), _request(), accept=_verdict, breach="Kein Urteil in der Antwort."
    )

    assert answer.reason_code == reasons.MODEL_OUTPUT_UNUSABLE
    assert "Kein Urteil" in (answer.message or "")
    [update] = _updates(session)
    assert (update["status"], update["reason"]) == ("error", reasons.MODEL_OUTPUT_UNUSABLE)
    assert len(server.sent) == 1


async def test_a_plain_answer_goes_through_as_text(server: _Server) -> None:
    session = _session()
    server.append(_response("Ein ganzer Satz."))

    answer = await answers.ask(_ready(session), _request())

    assert answer.ok and answer.text == "Ein ganzer Satz."
    assert _updates(session) == []


def test_only_a_single_unusable_answer_lets_a_run_go_on() -> None:
    assert answers.ends_run(None) is False
    assert answers.ends_run(reasons.MODEL_OUTPUT_UNUSABLE) is False
    for code in (
        reasons.AI_DISABLED,
        reasons.MODEL_BUDGET_EXHAUSTED,
        reasons.MODEL_INPUT_TOO_LARGE,
        reasons.MODEL_TIMEOUT,
        reasons.MODEL_HTTP_ERROR,
        reasons.MODEL_CONTEXT_UNKNOWN,
    ):
        assert answers.ends_run(code) is True, code


def test_the_structured_format_carries_the_strict_envelope() -> None:
    assert answers.json_schema_format("probe", {"type": "object"}) == {
        "type": "json_schema",
        "json_schema": {"name": "probe", "strict": True, "schema": {"type": "object"}},
    }


async def test_switched_off_ai_is_refused_before_any_server_is_asked() -> None:
    """resolve_target fragt bei ausgeschalteter KI keinen Server: ai_disabled
    mit Klartext - die Aufrufer muessen den Schalter nicht selbst pruefen."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        found = await resolve_target(_settings(is_enabled=False), client=client)

    assert asked == []
    assert found.ready is False
    assert found.reason_code == reasons.AI_DISABLED
    assert found.message == reasons.describe(reasons.AI_DISABLED)
