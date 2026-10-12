"""Streaming im KI-Kern (app/llm/gateway.py + app/llm/stream.py).

SSE eines OpenAI-kompatiblen Servers ergibt dieselbe Ergebnisform wie der
blockierende Aufruf; Denktext, Antwort und Werkzeugaufrufe gehen live an den
Rueckruf. stream_options wird bei Ablehnung genau einmal weggelassen
(model_usage_unavailable). Abbruch schliesst die Verbindung - auch in der
stillen Prefill-Phase; die Frist des Laufs beendet den Aufruf als
model_timeout.
"""

import asyncio
import json
import time
from typing import Any

import httpx
import pytest

from app import llm
from app.llm import gateway, reasons
from app.llm.stream import StreamAccumulator
from app.llm.window import WindowInfo
from tests.assistant_fakes import BLOCK, BLOCK_AFTER_FIRST, MODEL, FakeModelServer, Reply

BASE = "http://lm.test:1234/v1"


def _settings() -> llm.LLMSettings:
    return llm.LLMSettings(
        is_enabled=True,
        min_confidence=0.85,
        custom_base_url=None,
        model_name=None,
        temperature=1.0,
        request_timeout_seconds=30.0,
        answer_reserve_share=0.25,
        result_page_share=0.1,
        run_time_limit_minutes=20,
        start_chars_per_token=3.0,
        start_estimate_error=0.15,
        context_line_enabled=True,
    )


def _target() -> llm.Target:
    return llm.Target(
        base_url=BASE,
        model=MODEL,
        window=WindowInfo(loaded=8192, cap=None, effective=8192, source="test"),
    )


def _request(**extra: Any) -> llm.ChatRequest:
    return llm.ChatRequest(
        messages=[{"role": "system", "content": "Test."}, {"role": "user", "content": "Frage?"}],
        **extra,
    )


@pytest.fixture(autouse=True)
def _fresh_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway, "_NO_STREAM_OPTIONS", set())


async def _chat(
    client: httpx.AsyncClient, hooks: llm.ChatHooks, request: llm.ChatRequest | None = None
) -> llm.ChatResult:
    return await llm.chat(
        request or _request(), target=_target(), settings=_settings(), hooks=hooks, client=client
    )


async def test_a_streamed_answer_has_the_blocking_shape_and_live_pieces() -> None:
    reply = Reply(
        content="Antwort mit 12,50 €.",
        reasoning="Ich überlege.",
        tool_calls=[("lookup", {"year": 2026})],
        pieces=3,
    )
    seen: list[llm.StreamEvent] = []

    async def collect(event: llm.StreamEvent) -> None:
        seen.append(event)

    streamed_server = FakeModelServer([reply])
    async with streamed_server.client() as http:
        streamed = await _chat(http, llm.ChatHooks(on_stream=collect))
    blocking_server = FakeModelServer([reply])
    async with blocking_server.client() as http:
        blocking = await _chat(http, llm.ChatHooks())

    assert streamed.ok and blocking.ok
    assert streamed.streamed and not blocking.streamed
    for name in ("content", "reasoning_text", "tool_calls", "finish_reason", "usage"):
        assert getattr(streamed, name) == getattr(blocking, name), name
    assert streamed.basis is not None and streamed.basis.prompt_tokens == 900
    assert streamed.ttft_ms is not None
    assert streamed_server.bodies[0]["stream"] is True
    assert streamed_server.bodies[0]["stream_options"] == {"include_usage": True}
    assert "stream" not in blocking_server.bodies[0]

    kinds = [event.kind for event in seen]
    assert kinds[0] == "think" and "delta" in kinds and "tool" in kinds and kinds[-1] == "targs"
    thinks = [event for event in seen if event.kind == "think"]
    assert thinks[-1].chars == len("Ich überlege.")
    assert "".join(e.text for e in seen if e.kind == "delta") == "Antwort mit 12,50 €."
    assert "".join(e.text for e in seen if e.kind == "targs") == json.dumps({"year": 2026})
    assert next(e for e in seen if e.kind == "tool").name == "lookup"


async def test_rejected_stream_options_are_dropped_once_and_named() -> None:
    bodies: list[dict[str, Any]] = []
    server = FakeModelServer([Reply(content="Ok.", prompt_tokens=None)] * 3)

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("stream_options"):
            return httpx.Response(
                400, json={"error": {"message": "Unrecognized key: stream_options"}}
            )
        return await server.handle(request)

    async def ignore(_event: llm.StreamEvent) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _chat(http, llm.ChatHooks(on_stream=ignore))
        again = await _chat(http, llm.ChatHooks(on_stream=ignore))
    assert result.ok and result.content == "Ok."
    assert [bool(body.get("stream_options")) for body in bodies] == [True, False, False]
    codes = [event.code for event in result.events]
    assert codes.count(reasons.MODEL_USAGE_UNAVAILABLE) == 1
    assert "stream_options" in result.events[0].message
    assert again.ok  # gemerkt: kein zweites Scheitern


async def test_cancel_during_the_silent_prefill_closes_the_connection() -> None:
    server = FakeModelServer([BLOCK])
    cancel = llm.CancelToken()

    async def ignore(_event: llm.StreamEvent) -> None:
        return None

    async with server.client() as http:
        task = asyncio.create_task(_chat(http, llm.ChatHooks(cancel=cancel, on_stream=ignore)))
        await asyncio.wait_for(server.started.wait(), timeout=5)
        cancel.cancel()
        result = await asyncio.wait_for(task, timeout=5)
    assert result.reason_code == reasons.MODEL_CANCELLED
    assert server.closed == 1


async def test_cancel_mid_stream_keeps_what_arrived() -> None:
    server = FakeModelServer([BLOCK_AFTER_FIRST])
    cancel = llm.CancelToken()
    seen: list[llm.StreamEvent] = []

    async def collect(event: llm.StreamEvent) -> None:
        seen.append(event)
        cancel.cancel()

    async with server.client() as http:
        result = await asyncio.wait_for(
            _chat(http, llm.ChatHooks(cancel=cancel, on_stream=collect)), timeout=5
        )
    assert result.reason_code == reasons.MODEL_CANCELLED
    assert result.reasoning_text == "Ich denke"
    assert result.ttft_ms is not None
    assert server.closed == 1


async def test_the_run_deadline_ends_the_call_as_timeout() -> None:
    server = FakeModelServer([BLOCK])
    async with server.client() as http:
        result = await _chat(http, llm.ChatHooks(deadline=time.monotonic() + 0.2))
    assert result.reason_code == reasons.MODEL_TIMEOUT
    assert result.deadline_reached
    assert server.closed == 1


async def test_an_overflow_reported_inside_the_stream_is_an_overflow() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        error = {"error": {"message": "Context size has been exceeded."}}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(error)}\n\n".encode(),
        )

    async def ignore(_event: llm.StreamEvent) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _chat(http, llm.ChatHooks(on_stream=ignore))
    assert result.reason_code == reasons.MODEL_CONTEXT_OVERFLOW


async def test_a_server_without_streaming_answers_in_one_piece() -> None:
    payload = {
        "choices": [{"message": {"content": "Ganz."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def ignore(_event: llm.StreamEvent) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _chat(http, llm.ChatHooks(on_stream=ignore))
    assert result.ok and result.content == "Ganz." and result.usage.prompt_tokens == 10


async def test_a_failing_live_display_does_not_break_the_call() -> None:
    async def broken(_event: llm.StreamEvent) -> None:
        raise RuntimeError("Anzeige kaputt")

    server = FakeModelServer([Reply(content="Trotzdem da.")])
    async with server.client() as http:
        result = await _chat(http, llm.ChatHooks(on_stream=broken))
    assert result.ok and result.content == "Trotzdem da."
    assert [event.code for event in result.events].count("live_callback_failed") == 1


def test_the_accumulator_assembles_split_tool_calls() -> None:
    accumulator = StreamAccumulator()
    lines = [
        ": kommentar",
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":'
        '{"name":"x","arguments":"{\\"y\\""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":":1}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1}}',
        "data: [DONE]",
    ]
    for line in lines:
        accumulator.feed_line(line)
    payload = accumulator.payload()
    call = payload["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "a"
    assert call["function"] == {"name": "x", "arguments": '{"y":1}'}
    assert payload["choices"][0]["finish_reason"] == "tool_calls"
    assert payload["usage"] == {"prompt_tokens": 5, "completion_tokens": 1}
    assert accumulator.done
