"""Gateway des KI-Kerns (app/llm/gateway.py) gegen einen gefaelschten Modellserver.

Echter httpx-Client mit httpx.MockTransport (kein LM Studio, kein Netz); die
Sitzung ist eine Attrappe, die ihre Statements protokolliert. Geprueft wird,
was jeder Aufruf garantiert: Budget aus dem Fenster statt einer Zahl im Code,
kein Aufruf ohne Fenster oder ohne Platz, benannte Gruende statt stiller
Uebersteuerung, Ueberlauf einmal mit korrigierter Messung neu gerechnet,
Schema-Ablehnung getrennt vom Ueberlauf, eine llm_calls-Zeile je Aufruf mit
Budgetzeile, Messung aus usage.prompt_tokens, Abbruch und Verdichtung.
"""

import asyncio
import json
import math
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.llm import reasons
from app.llm.budget import Budget
from app.llm.calls_log import CallScope
from app.llm.discovery import Target
from app.llm.gateway import (
    CancelToken,
    ChatHooks,
    ChatRequest,
    ChatResult,
    build_body,
    chat,
    chat_structured,
    is_overflow,
    overflow_input_tokens,
)
from app.llm.settings import LLMSettings
from app.llm.window import ReasoningCapability, WindowInfo

BASE = "http://lm.test:1234/v1"
MODEL = "test-model"


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


def _target(
    window: int | None = 8192,
    capability: ReasoningCapability | None = None,
) -> Target:
    return Target(
        base_url=BASE,
        model=MODEL,
        window=WindowInfo(
            loaded=window,
            cap=None,
            effective=window,
            source="/api/v1/models" if window else None,
            reason=None if window else "Modell nicht geladen",
            reasoning_capability=capability or ReasoningCapability(),
        ),
    )


def _messages(user_chars: int = 300) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "Du bist ein Test."},
        {"role": "user", "content": "x" * user_chars},
    ]


def _answer(
    content: str = '{"ok": true}',
    *,
    finish_reason: str = "stop",
    usage: dict[str, Any] | None = None,
    message: Any = "default",
) -> httpx.Response:
    choice: dict[str, Any] = {"finish_reason": finish_reason}
    choice["message"] = {"content": content} if message == "default" else message
    payload: dict[str, Any] = {"choices": [choice]}
    if usage is not None:
        payload["usage"] = usage
    return httpx.Response(200, json=payload)


class _Server:
    def __init__(self, answers: list[httpx.Response | Callable[[], httpx.Response]]) -> None:
        self.answers = answers
        self.bodies: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        answer = self.answers[min(len(self.bodies), len(self.answers)) - 1]
        return answer() if callable(answer) else answer

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def _session() -> AsyncMock:
    async def execute(statement: Any, params: Any = None, *args: Any, **kwargs: Any) -> MagicMock:
        result = MagicMock()
        result.first.return_value = None  # keine Messung vorhanden
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute)
    return session


def _scope(session: AsyncMock, **overrides: Any) -> CallScope:
    values: dict[str, Any] = {
        "session": session,
        "household_id": uuid4(),
        "stage": "test",
        "prompt_version": "test-v1",
    }
    values.update(overrides)
    return CallScope(**values)


def _statements(session: AsyncMock, needle: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for call in session.execute.await_args_list:
        sql = str(getattr(call.args[0], "text", call.args[0]))
        if needle in sql:
            found.append(call.args[1] if len(call.args) > 1 else {})
    return found


async def _call(
    server: _Server,
    request: ChatRequest | None = None,
    *,
    target: Target | None = None,
    settings: LLMSettings | None = None,
    scope: CallScope | None = None,
    hooks: ChatHooks | None = None,
) -> ChatResult:
    async with server.client() as client:
        return await chat(
            request or ChatRequest(messages=_messages()),
            target=target or _target(),
            settings=settings or _settings(),
            scope=scope,
            hooks=hooks,
            client=client,
        )


# ---------------------------------------------------------------------------
# Budget aus dem Fenster
# ---------------------------------------------------------------------------


async def test_answer_budget_is_the_free_window_and_the_call_is_logged() -> None:
    session = _session()
    server = _Server([_answer(usage={"prompt_tokens": 120, "completion_tokens": 20})])

    result = await _call(server, scope=_scope(session))

    assert result.ok
    budget = result.budget
    assert budget is not None
    messages = _messages()
    chars = sum(len(m["content"]) + len(m["role"]) for m in messages)
    expected_input = math.ceil(chars / 3.0)
    assert budget.E == expected_input
    assert budget.A == math.ceil(expected_input * 0.15)
    assert budget.R == 2048
    assert server.bodies[0]["max_tokens"] == 8192 - budget.E - budget.A
    assert server.bodies[0]["model"] == MODEL

    rows = _statements(session, "insert into llm_calls")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "success"
    assert row["reason"] is None
    assert row["window_tokens"] == 8192
    assert row["window_source"] == "/api/v1/models"
    assert row["input_tokens"] == budget.E
    assert row["exact"] is False
    assert row["reserve"] == 2048
    assert row["allowance"] == budget.A
    assert row["max_tokens"] == server.bodies[0]["max_tokens"]
    assert row["prompt_chars"] == chars
    assert row["ptok"] == 120
    assert json.loads(row["parts"])["system"] > 0
    assert row["target"] == "http://lm.test:1234"
    assert result.call_id is not None

    # Messung aus den gemeldeten prompt_tokens fortgeschrieben.
    measured = _statements(session, "insert into ai_model_measurements")
    assert measured == [
        {
            "household_id": str(row["household_id"]),
            "target": "http://lm.test:1234",
            "model": MODEL,
            "chars": chars,
            "prompt": 120,
            "need": 20,
        }
    ]
    assert result.basis is not None and result.basis.prompt_tokens == 120


async def test_the_budget_grows_with_the_window() -> None:
    budgets = []
    for window in (4096, 8192, 32768):
        server = _Server([_answer()])
        await _call(server, target=_target(window))
        budgets.append(server.bodies[0]["max_tokens"])

    assert budgets == sorted(budgets) and len(set(budgets)) == 3


async def test_a_second_call_counts_its_known_prefix_exactly() -> None:
    server = _Server([_answer(usage={"prompt_tokens": 140}), _answer()])
    first = await _call(server)
    assert first.basis is not None

    follow_up = [
        *_messages(),
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "und?"},
    ]
    second = await _call(server, ChatRequest(messages=follow_up, basis=first.basis))

    assert second.budget is not None
    assert second.budget.exact
    new_chars = len("ok") + len("assistant") + len("und?") + len("user")
    assert second.budget.E == 140 + math.ceil(new_chars / 3.0)


# ---------------------------------------------------------------------------
# Kein Aufruf ohne Fenster, ohne Platz, bei ausgeschalteter KI
# ---------------------------------------------------------------------------


async def test_unknown_window_means_no_call_and_a_refused_row() -> None:
    session = _session()
    server = _Server([_answer()])

    result = await _call(server, target=_target(None), scope=_scope(session))

    assert server.bodies == []
    assert result.reason_code == reasons.MODEL_CONTEXT_UNKNOWN
    assert result.message is not None and "Kontext höchstens" in result.message
    row = _statements(session, "insert into llm_calls")[0]
    assert row["status"] == "refused"
    assert row["reason"] == reasons.MODEL_CONTEXT_UNKNOWN


async def test_disabled_ai_sends_nothing() -> None:
    server = _Server([_answer()])

    result = await _call(server, settings=_settings(is_enabled=False))

    assert server.bodies == []
    assert result.reason_code == reasons.AI_DISABLED


async def test_missing_budget_setting_is_named_not_replaced() -> None:
    server = _Server([_answer()])

    result = await _call(server, settings=_settings(answer_reserve_share=None))

    assert server.bodies == []
    assert result.reason_code == reasons.MODEL_SETTINGS_INVALID
    assert result.message is not None and "answer_reserve_share" in result.message


async def test_input_that_does_not_fit_is_refused_with_its_parts() -> None:
    server = _Server([_answer()])

    result = await _call(server, ChatRequest(messages=_messages(user_chars=30000)))

    assert server.bodies == []
    assert result.reason_code == reasons.MODEL_INPUT_TOO_LARGE
    assert result.message is not None
    assert "Fenster 8.192 Token" in result.message
    assert "aktuelle Frage" in result.message


async def test_the_compaction_hook_gets_one_chance() -> None:
    server = _Server([_answer()])
    seen: list[Budget] = []

    async def compact(messages: list[dict[str, Any]], budget: Budget) -> list[dict[str, Any]]:
        seen.append(budget)
        return _messages(user_chars=300)

    result = await _call(
        server,
        ChatRequest(messages=_messages(user_chars=30000)),
        hooks=ChatHooks(compact=compact),
    )

    assert result.ok
    assert len(seen) == 1 and not seen[0].fits
    assert len(server.bodies[0]["messages"][1]["content"]) == 300


# ---------------------------------------------------------------------------
# Fehlerbilder des Servers
# ---------------------------------------------------------------------------


def test_overflow_texts_are_recognised() -> None:
    assert is_overflow("Context size has been exceeded.")
    assert is_overflow("The input does not fit in context (input has 9141 tokens)")
    assert is_overflow("This model's maximum context length is 8192 tokens.")
    assert not is_overflow("response_format is not supported")
    assert overflow_input_tokens("input has 9141 tokens, context 8192") == 9141
    vllm = "you requested 9000 tokens (8500 in the messages, 500 in the completion)"
    assert overflow_input_tokens(vllm) == 8500
    assert overflow_input_tokens("Context size has been exceeded.") is None


async def test_overflow_corrects_the_measurement_and_recomputes_once() -> None:
    session = _session()
    overflow = httpx.Response(
        400, json={"error": "Context size has been exceeded (input has 4000 tokens)."}
    )
    server = _Server([overflow, _answer()])

    result = await _call(
        server, ChatRequest(messages=_messages(user_chars=3000)), scope=_scope(session)
    )

    assert result.ok
    assert len(server.bodies) == 2
    # Die echte Eingabe (4000 statt ~1000 geschaetzt) gilt beim zweiten Mal
    # exakt - ohne Schaetzabstand - und laesst entsprechend weniger Platz.
    assert result.budget is not None
    assert result.budget.exact and result.budget.E == 4000 and result.budget.A == 0
    assert server.bodies[1]["max_tokens"] == 8192 - 4000
    assert [event.code for event in result.events][0] == reasons.MODEL_CONTEXT_OVERFLOW
    rows = _statements(session, "insert into llm_calls")
    assert [row["reason"] for row in rows] == [reasons.MODEL_CONTEXT_OVERFLOW, None]
    assert [row["status"] for row in rows] == ["error", "success"]
    measured = _statements(session, "insert into ai_model_measurements")
    assert measured[0]["prompt"] == 4000


async def test_an_overflow_that_still_does_not_fit_is_refused_by_name() -> None:
    overflow = httpx.Response(
        400, json={"error": "Context size has been exceeded (input has 7000 tokens)."}
    )
    server = _Server([overflow, _answer()])

    result = await _call(server)

    # 7000 von 8192 lassen die Antwortreserve (2048) nicht frei.
    assert len(server.bodies) == 1
    assert result.reason_code == reasons.MODEL_INPUT_TOO_LARGE
    assert [event.code for event in result.events] == [reasons.MODEL_CONTEXT_OVERFLOW]


async def test_overflow_without_a_real_count_is_not_repeated_blindly() -> None:
    server = _Server([httpx.Response(400, json={"error": "Context size has been exceeded."})])

    result = await _call(server)

    assert len(server.bodies) == 1
    assert result.reason_code == reasons.MODEL_CONTEXT_OVERFLOW


async def test_overflow_is_not_mistaken_for_a_missing_schema_capability() -> None:
    """Befund 7d: frueher galt jeder HTTP 400 als "Modell kann kein Schema"."""
    schema = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    overflow = await _call(
        _Server([httpx.Response(400, json={"error": "Context size has been exceeded."})]),
        ChatRequest(messages=_messages(), response_format=schema),
    )
    rejected = await _call(
        _Server([httpx.Response(400, json={"error": "response_format is not supported"})]),
        ChatRequest(messages=_messages(), response_format=schema),
    )
    plain = await _call(_Server([httpx.Response(400, json={"error": "kaputt"})]))

    assert overflow.reason_code == reasons.MODEL_CONTEXT_OVERFLOW
    assert rejected.reason_code == reasons.MODEL_SCHEMA_UNSUPPORTED
    assert plain.reason_code == reasons.MODEL_HTTP_ERROR


async def test_chat_structured_falls_back_once_when_the_schema_is_rejected() -> None:
    schema = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    session = _session()
    server = _Server(
        [httpx.Response(400, json={"error": "response_format is not supported"}), _answer()]
    )

    async with server.client() as client:
        result = await chat_structured(
            ChatRequest(messages=_messages(), response_format=schema),
            target=_target(),
            settings=_settings(),
            scope=_scope(session),
            client=client,
        )

    assert result.ok and result.schema_fallback
    assert "response_format" in server.bodies[0]
    assert "response_format" not in server.bodies[1]
    assert result.events[0].code == reasons.MODEL_SCHEMA_UNSUPPORTED
    rows = _statements(session, "insert into llm_calls")
    assert [row["reason"] for row in rows] == [reasons.MODEL_SCHEMA_UNSUPPORTED, None]


async def test_chat_structured_does_not_fall_back_on_an_overflow() -> None:
    schema = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    server = _Server([httpx.Response(400, json={"error": "Context size has been exceeded."})])

    async with server.client() as client:
        result = await chat_structured(
            ChatRequest(messages=_messages(), response_format=schema),
            target=_target(),
            settings=_settings(),
            client=client,
        )

    assert len(server.bodies) == 1
    assert result.reason_code == reasons.MODEL_CONTEXT_OVERFLOW
    assert not result.schema_fallback


async def test_empty_answer_at_the_length_limit_is_a_named_budget_failure() -> None:
    server = _Server(
        [
            _answer(
                "",
                finish_reason="length",
                message={"content": "", "reasoning_content": "Ich denke nach ..."},
            )
        ]
    )

    result = await _call(server)

    assert result.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    assert result.reasoning_text == "Ich denke nach ..."
    assert result.message is not None and "Antwortbudget" in result.message
    # Kein automatischer zweiter Versuch (E1).
    assert len(server.bodies) == 1


async def test_a_null_message_is_unusable_not_a_crash() -> None:
    result = await _call(_Server([_answer(message=None)]))

    assert result.reason_code == reasons.MODEL_OUTPUT_UNUSABLE


async def test_http_200_with_an_error_body_is_an_error() -> None:
    result = await _call(_Server([httpx.Response(200, json={"error": "Unexpected endpoint"})]))

    assert result.reason_code == reasons.MODEL_HTTP_ERROR
    assert result.message is not None and "Unexpected endpoint" in result.message


async def test_timeout_and_connection_errors_are_named() -> None:
    def timeout() -> httpx.Response:
        raise httpx.ReadTimeout("zu langsam")

    def refused() -> httpx.Response:
        raise httpx.ConnectError("verweigert")

    session = _session()
    slow = await _call(_Server([timeout]), scope=_scope(session))
    gone = await _call(_Server([refused]))

    assert slow.reason_code == reasons.MODEL_TIMEOUT
    assert _statements(session, "insert into llm_calls")[0]["status"] == "timeout"
    assert gone.reason_code == reasons.MODEL_UNAVAILABLE


# ---------------------------------------------------------------------------
# Reasoning, Messung, Ereignisse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "override", "sent"),
    [
        (None, None, None),
        ("off", None, "none"),
        ("on", None, "high"),
        ("medium", None, "medium"),
        (None, "off", "none"),
        ("on", "off", "none"),
    ],
)
async def test_reasoning_effort_follows_the_setting_only(
    setting: str | None, override: str | None, sent: str | None
) -> None:
    server = _Server([_answer()])

    result = await _call(
        server,
        ChatRequest(messages=_messages(), reasoning_override=override),
        settings=_settings(reasoning=setting),
    )

    assert server.bodies[0].get("reasoning_effort") == sent
    assert result.reasoning_sent == sent


async def test_a_model_without_reasoning_capability_gets_no_effort() -> None:
    server = _Server([_answer()])

    result = await _call(
        server,
        settings=_settings(reasoning="off"),
        target=_target(capability=ReasoningCapability(kind="none")),
    )

    assert "reasoning_effort" not in server.bodies[0]
    event = next(e for e in result.events if e.code == reasons.MODEL_REASONING_UNSUPPORTED)
    assert event.level == "info"
    assert "„off“" in event.message


async def test_a_rejected_reasoning_effort_is_named_and_not_retried() -> None:
    server = _Server([httpx.Response(400, json={"error": "Unsupported reasoning_effort value"})])

    result = await _call(server, settings=_settings(reasoning="on"))

    assert result.reason_code == reasons.MODEL_REASONING_UNSUPPORTED
    assert len(server.bodies) == 1


async def test_missing_usage_and_a_tight_reserve_are_reported_as_events() -> None:
    session = _session()
    no_usage = await _call(_Server([_answer()]), scope=_scope(session))
    tight = await _call(_Server([_answer(usage={"prompt_tokens": 100, "completion_tokens": 5000})]))

    assert [event.code for event in no_usage.events] == [reasons.MODEL_USAGE_UNAVAILABLE]
    assert _statements(session, "insert into ai_model_measurements") == []
    assert reasons.MODEL_RESERVE_TIGHT in [event.code for event in tight.events]


async def test_the_connection_test_neither_logs_nor_measures() -> None:
    session = _session()
    scope = _scope(session, log=False, measure=False)

    result = await _call(_Server([_answer(usage={"prompt_tokens": 18})]), scope=scope)

    assert result.ok
    assert _statements(session, "insert into llm_calls") == []
    assert _statements(session, "insert into ai_model_measurements") == []


# ---------------------------------------------------------------------------
# Werkzeuge und Abbruch (Etappe 2 nutzt beides)
# ---------------------------------------------------------------------------


async def test_tools_are_sent_counted_and_their_calls_returned() -> None:
    tools = [{"type": "function", "function": {"name": "overview", "parameters": {}}}]
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "overview", "arguments": '{"monat": "2026-09"}'},
    }
    server = _Server([_answer("", message={"content": None, "tool_calls": [call]})])

    result = await _call(server, ChatRequest(messages=_messages(), tools=tools, tool_choice="auto"))

    assert result.ok
    assert server.bodies[0]["tools"] == tools
    assert server.bodies[0]["tool_choice"] == "auto"
    assert result.budget is not None and result.budget.parts.tool_defs > 0
    assert result.tool_calls[0].name == "overview"
    assert json.loads(result.tool_calls[0].arguments) == {"monat": "2026-09"}


async def test_cancel_closes_a_running_request() -> None:
    token = CancelToken()
    started = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(30)
        return _answer()

    async def cancel_soon() -> None:
        await started.wait()
        token.cancel()

    session = _session()
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        canceller = asyncio.create_task(cancel_soon())
        result = await chat(
            ChatRequest(messages=_messages()),
            target=_target(),
            settings=_settings(),
            scope=_scope(session),
            hooks=ChatHooks(cancel=token),
            client=client,
        )
        await canceller

    assert result.reason_code == reasons.MODEL_CANCELLED
    assert _statements(session, "insert into llm_calls")[0]["status"] == "cancelled"


def test_the_body_sends_sampling_only_from_a_profile_or_manual_mode() -> None:
    gemma = build_body(
        model="google/gemma-4-26b-a4b-qat",
        messages=_messages(),
        settings=_settings(reasoning="on"),
        max_tokens=100,
        reasoning_effort="high",
    )
    unknown = build_body(
        model="voellig-unbekannt", messages=_messages(), settings=_settings(), max_tokens=100
    )
    manual = build_body(
        model="voellig-unbekannt",
        messages=_messages(),
        settings=_settings(
            sampling_mode="manual",
            temperature=0.42,
            top_p=0.81,
            top_k=37,
            repeat_penalty=1.07,
            min_p=0.03,
            presence_penalty=0.25,
        ),
        max_tokens=100,
    )

    assert (gemma["temperature"], gemma["top_p"], gemma["top_k"]) == (1.0, 0.95, 64)
    assert gemma["reasoning_effort"] == "high"
    assert set(unknown) == {"model", "messages", "max_tokens"}
    assert {key: manual[key] for key in ("temperature", "top_p", "top_k")} == {
        "temperature": 0.42,
        "top_p": 0.81,
        "top_k": 37,
    }
    assert manual["presence_penalty"] == 0.25
