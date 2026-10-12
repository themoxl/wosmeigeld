"""Agentenschleife des KI-Felds (app/assistant/loop.py) gegen einen
gefaelschten Modellserver - ohne Datenbank.

Geprueft wird der Port von lauf(): Werkzeugrunde dann Antwort, beide
Formen von Pseudo-Werkzeugaufrufen samt Typen der XML-Parameter,
<think>-Entfernung (auch live), Systemnachrichten zu einer, Stillstand,
Laufzeitende -> Schlussaufruf ohne Werkzeuge, leere Antwort ->
model_output_unusable, kein "ohne Denken wiederholen", abgeschnittene
Antwort -> model_reserve_tight, Netzwerkfehler mit Wiederholung,
Kontextzeile, Abbruch.
"""

import asyncio
import json
import time
from typing import Any

import httpx
import pytest

from app import llm
from app.assistant import loop as loop_module
from app.assistant import prompts
from app.assistant.loop import (
    AgentLoop,
    CallRecord,
    KeyAllocator,
    LiveAdapter,
    LoopCancelled,
    LoopFailed,
    ToolOutcome,
    ToolRecord,
)
from app.llm.window import ReasoningCapability, WindowInfo
from tests.assistant_fakes import MODEL, FakeModelServer, Reply

BASE = "http://lm.test:1234/v1"


def _settings(**overrides: Any) -> llm.LLMSettings:
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
    return llm.LLMSettings(**values)


def _target(window: int = 32768) -> llm.Target:
    return llm.Target(
        base_url=BASE,
        model=MODEL,
        window=WindowInfo(
            loaded=window,
            cap=None,
            effective=window,
            source="test",
            reasoning_capability=ReasoningCapability(kind="switchable"),
        ),
    )


class Recorder:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []
        self.finished: list[CallRecord] = []
        self.rounds: list[tuple[int, dict[str, Any]]] = []
        self.tools: list[ToolRecord] = []
        self.events: list[tuple[str, str, str]] = []
        self.meters: list[dict[str, Any]] = []
        self.live_lines: list[dict[str, Any]] = []

    async def call_started(self, record: CallRecord) -> None:
        self.calls.append(record)

    async def call_finished(self, record: CallRecord) -> None:
        self.finished.append(record)

    async def round_message(self, call_nr: int, message: dict[str, Any]) -> None:
        self.rounds.append((call_nr, json.loads(json.dumps(message))))

    async def tool_started(self, key: str, name: str, args: dict[str, Any]) -> None:
        return None

    async def tool_finished(self, record: ToolRecord) -> None:
        self.tools.append(record)

    async def event(
        self,
        kind: str,
        level: str,
        message: str,
        details: dict[str, Any] | None = None,
        call_nr: int | None = None,
    ) -> None:
        self.events.append((kind, level, message))

    async def meter(self, meter: dict[str, Any]) -> None:
        self.meters.append(meter)

    async def live(self, payload: dict[str, Any]) -> None:
        self.live_lines.append(payload)

    def kinds(self) -> list[str]:
        return [kind for kind, _level, _message in self.events]


LOOKUP_SCHEMA = {
    "type": "object",
    "properties": {
        "year": {"type": "integer"},
        "filters": {"type": "object"},
        "label": {"type": "string"},
    },
}


class Tools:
    def __init__(self, *, delay: float = 0.0, results: list[str] | None = None) -> None:
        self.delay = delay
        self.results = results
        self.received: list[tuple[str, str, dict[str, Any]]] = []

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Schlaegt nach.",
                    "parameters": LOOKUP_SCHEMA,
                },
            }
        ]

    def parameters_of(self, name: str) -> dict[str, Any] | None:
        return LOOKUP_SCHEMA if name == "lookup" else None

    async def run(
        self, key: str, name: str, args: dict[str, Any], *, remaining_ms: int
    ) -> ToolOutcome:
        self.received.append((key, name, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        text = self.results.pop(0) if self.results else f"Ergebnis {key}: Summe 412.3"
        return ToolOutcome("ok", text, row_count=1)


def _loop(
    server: FakeModelServer,
    *,
    tools: Tools | None = None,
    recorder: Recorder | None = None,
    transcript: list[dict[str, Any]] | None = None,
    deadline_in: float = 600.0,
    cancel: llm.CancelToken | None = None,
    client: httpx.AsyncClient | None = None,
    settings: llm.LLMSettings | None = None,
) -> tuple[AgentLoop, Recorder, Tools]:
    recorder = recorder or Recorder()
    tools = tools or Tools()
    messages = transcript or [
        {"role": "system", "content": "Du bist ein Test."},
        {"role": "user", "content": "Wie viel habe ich im September ausgegeben?"},
    ]
    question = next(m for m in reversed(messages) if m["role"] == "user")
    current = settings or _settings()
    agent = AgentLoop(
        run_id="run-1",
        target=_target(server.window),
        settings=current,
        scope=None,
        recorder=recorder,
        tools=tools,
        cancel=cancel or llm.CancelToken(),
        transcript=messages,
        question=question,
        keys=KeyAllocator(0),
        deadline=time.monotonic() + deadline_in,
        runtime_seconds=deadline_in,
        client=client or server.client(),
        measurement=None,
    )
    return agent, recorder, tools


async def test_a_tool_round_then_an_answer() -> None:
    server = FakeModelServer(
        [
            Reply(tool_calls=[("lookup", {"year": 2026})], reasoning="Erst nachsehen."),
            Reply(content="Im September waren es 412,30 €."),
        ]
    )
    agent, recorder, tools = _loop(server)
    outcome = await agent.run()
    assert outcome.end_reason == "answer"
    assert outcome.content == "Im September waren es 412,30 €."
    assert tools.received == [("W1", "lookup", {"year": 2026})]
    assert [record.key for record in recorder.tools] == ["W1"]
    assert [call.step for call in recorder.calls] == ["Antwort", "Runde 2"]
    assert recorder.finished[0].tool_call_names == ["lookup"]
    assert recorder.finished[0].reasoning_chars == len("Erst nachsehen.")
    # Streaming mit usage; der zweite Aufruf zaehlt seinen Anfang exakt.
    assert server.bodies[0]["stream"] is True
    assert server.bodies[0]["stream_options"] == {"include_usage": True}
    assert recorder.calls[1].budget is not None and recorder.calls[1].budget["exact"] is True
    assert recorder.meters[-1]["exact"] is True
    # targs kam live mit derselben Kennung, die das Werkzeug bekam.
    assert any(line["type"] == "targs" and line["key"] == "W1" for line in recorder.live_lines)
    assert any(line["type"] == "think" for line in recorder.live_lines)
    # Werkzeugaufruf und Ergebnis stehen im Verlauf des zweiten Aufrufs.
    second = server.bodies[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "lookup"
    assert second[-1]["role"] == "tool" and second[-1]["content"].startswith("Ergebnis W1")


async def test_the_context_line_is_sent_and_removed_afterwards() -> None:
    server = FakeModelServer(
        [Reply(tool_calls=[("lookup", {"year": 2026})]), Reply(content="Fertig.")]
    )
    agent, _recorder, _tools = _loop(server)
    await agent.run()
    sent = server.bodies[1]["messages"][-1]["content"]
    assert "[Kontext:" in sent and "Antwortreserve" in sent
    tool_message = next(m for m in agent.transcript if m["role"] == "tool")
    assert "[Kontext:" not in tool_message["content"]

    quiet = FakeModelServer(
        [Reply(tool_calls=[("lookup", {"year": 2026})]), Reply(content="Fertig.")]
    )
    agent, _recorder, _tools = _loop(quiet, settings=_settings(context_line_enabled=False))
    await agent.run()
    assert "[Kontext:" not in quiet.bodies[1]["messages"][-1]["content"]


async def test_pseudo_tool_calls_in_xml_form_are_executed_with_their_types() -> None:
    xml = (
        "Ich schaue nach.<tool_call><function=lookup><parameter=year>2026</parameter>"
        '<parameter=filters>{"kind": "expense"}</parameter><parameter=label>2026</parameter>'
        "</function></tool_call>"
    )
    server = FakeModelServer([Reply(content=xml, pieces=7), Reply(content="Fertig.")])
    agent, recorder, tools = _loop(server)
    outcome = await agent.run()
    assert outcome.content == "Fertig."
    assert tools.received == [
        ("W1", "lookup", {"year": 2026, "filters": {"kind": "expense"}, "label": "2026"})
    ]
    assert "pseudo_tool" in recorder.kinds()
    assert recorder.rounds[0][1]["content"] == "Ich schaue nach."
    # Der Pseudoaufruf erscheint nie als Antworttext in der Live-Anzeige.
    live_text = "".join(line["text"] for line in recorder.live_lines if line["type"] == "delta")
    assert live_text == "Ich schaue nach.Fertig."
    assert "<function=" not in live_text and "<tool_call>" not in live_text


async def test_pseudo_tool_calls_in_json_form_are_executed() -> None:
    content = '<tool_call>{"name": "lookup", "arguments": {"year": 2025}}</tool_call>'
    server = FakeModelServer([Reply(content=content), Reply(content="Erledigt.")])
    agent, _recorder, tools = _loop(server)
    await agent.run()
    assert tools.received == [("W1", "lookup", {"year": 2025})]
    # Als echter Werkzeugaufruf im Verlauf, damit der Server ihn versteht.
    assert server.bodies[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "lookup"


async def test_think_blocks_are_removed_from_answer_and_live_text() -> None:
    server = FakeModelServer(
        [Reply(content="<think>Hm, rechnen</think>Die Antwort ist 42,50 €.", pieces=9)]
    )
    agent, recorder, _tools = _loop(server)
    outcome = await agent.run()
    assert outcome.content == "Die Antwort ist 42,50 €."
    live_text = "".join(line["text"] for line in recorder.live_lines if line["type"] == "delta")
    assert live_text == "Die Antwort ist 42,50 €."
    thought = [line for line in recorder.live_lines if line["type"] == "think"]
    assert "".join(line["text"] for line in thought) == "Hm, rechnen"
    assert thought[-1]["chars"] == len("Hm, rechnen")

    open_block = FakeModelServer([Reply(content="Kurz: 7.<think>und dann noch")])
    agent, _recorder, _tools = _loop(open_block)
    assert (await agent.run()).content == "Kurz: 7."


async def test_several_system_messages_become_one_at_the_front() -> None:
    server = FakeModelServer([Reply(content="Ok.")])
    transcript = [
        {"role": "system", "content": "Erste Regel."},
        {"role": "user", "content": "Frage?"},
        {"role": "system", "content": "Zweite Regel."},
    ]
    agent, _recorder, _tools = _loop(server, transcript=transcript)
    await agent.run()
    sent = server.bodies[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[0]["content"] == "Erste Regel.\n\nZweite Regel."


async def test_two_rounds_without_news_end_in_a_stall_and_one_call_without_tools() -> None:
    same = Reply(tool_calls=[("lookup", {"year": 2026})])
    server = FakeModelServer([same, same, same, Reply(content="Mit dem Bisherigen: 412,30 €.")])
    tools = Tools(results=["Summe 412.3", "Summe 412.3", "Summe 412.3"])
    agent, recorder, _tools = _loop(server, tools=tools)
    outcome = await agent.run()
    assert outcome.end_reason == "stall"
    assert len(server.bodies) == 4
    # Nach der ersten Runde ohne Neues ein Hinweis an das Modell.
    assert server.bodies[2]["messages"][-1] == {"role": "user", "content": prompts.STALL_HINT}
    final = server.bodies[3]
    assert "tools" not in final
    assert final["messages"][-1] == {"role": "user", "content": prompts.CLOSING_MESSAGE}
    assert "stall" in recorder.kinds()
    assert recorder.calls[-1].step == "Abschlussantwort"


async def test_the_runtime_ends_exploration_with_one_call_without_tools() -> None:
    server = FakeModelServer(
        [Reply(tool_calls=[("lookup", {"year": 2026})]), Reply(content="Was ich weiß: 412,30 €.")]
    )
    agent, recorder, _tools = _loop(server, tools=Tools(delay=0.4), deadline_in=0.2)
    outcome = await agent.run()
    assert outcome.end_reason == "runtime"
    assert "runtime" in recorder.kinds()
    assert "tools" not in server.bodies[1]
    assert server.bodies[1]["messages"][-1]["content"] == prompts.CLOSING_MESSAGE


async def test_a_deadline_during_a_call_also_ends_in_the_closing_call() -> None:
    server = FakeModelServer(["block", Reply(content="Abschluss.")])
    agent, _recorder, _tools = _loop(server, deadline_in=0.3)
    outcome = await agent.run()
    assert outcome.end_reason == "runtime"
    assert outcome.content == "Abschluss."
    assert server.closed == 1


async def test_an_empty_answer_is_unusable() -> None:
    server = FakeModelServer([Reply(content="<think>nur gedacht</think>")])
    agent, _recorder, _tools = _loop(server)
    with pytest.raises(LoopFailed) as failed:
        await agent.run()
    assert failed.value.code == llm.reasons.MODEL_OUTPUT_UNUSABLE


async def test_an_exhausted_budget_is_named_and_never_retried_without_thinking() -> None:
    server = FakeModelServer(
        [Reply(content="", reasoning="denke " * 50, finish_reason="length")] * 3
    )
    agent, _recorder, _tools = _loop(server)
    with pytest.raises(LoopFailed) as failed:
        await agent.run()
    assert failed.value.code == llm.reasons.MODEL_BUDGET_EXHAUSTED
    # Verdichten haette keinen Platz geschaffen - kein zweiter Aufruf, und
    # schon gar keiner "ohne Denken".
    assert len(server.bodies) == 1


async def test_an_answer_cut_at_the_limit_fails_as_reserve_tight() -> None:
    server = FakeModelServer([Reply(content="Die Antwort beginnt und", finish_reason="length")])
    agent, _recorder, _tools = _loop(server)
    with pytest.raises(LoopFailed) as failed:
        await agent.run()
    assert failed.value.code == llm.reasons.MODEL_RESERVE_TIGHT
    assert failed.value.content == "Die Antwort beginnt und"


async def test_network_errors_are_retried_with_growing_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_module, "NETWORK_FIRST_WAIT_SECONDS", 0.01)
    server = FakeModelServer([Reply(content="Wieder da: 12,00 €.")])
    attempts = {"count": 0}

    async def flaky(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise httpx.ConnectError("Verbindung abgelehnt", request=request)
        return await server.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as http:
        agent, recorder, _tools = _loop(server, client=http)
        outcome = await agent.run()
    assert outcome.content == "Wieder da: 12,00 €."
    assert recorder.kinds().count("network_retry") == 2
    assert [call.retry_of for call in recorder.calls] == [None, 1, 2]


async def test_a_server_error_in_a_later_round_ends_exploration_with_a_closing_call() -> None:
    server = FakeModelServer(
        [
            Reply(tool_calls=[("lookup", {"year": 2026})]),
            Reply(status=500, error="interner Fehler"),
            Reply(content="Soweit bekannt: 412,30 €."),
        ]
    )
    agent, recorder, _tools = _loop(server)
    outcome = await agent.run()
    assert outcome.end_reason == "failed"
    assert outcome.content == "Soweit bekannt: 412,30 €."
    assert "exploration_aborted" in recorder.kinds()
    assert "tools" not in server.bodies[2]


async def test_unknown_tools_and_broken_arguments_go_back_to_the_model() -> None:
    server = FakeModelServer(
        [
            Reply(tool_calls=[("delete_everything", {}), ("lookup", "{kaputt")]),
            Reply(content="Dann eben nicht."),
        ]
    )
    agent, recorder, tools = _loop(server)
    await agent.run()
    assert tools.received == []
    assert [record.status for record in recorder.tools] == ["rejected", "rejected"]
    assert "unbekanntes Werkzeug" in recorder.tools[0].result_text
    assert "kein gültiges JSON" in recorder.tools[1].result_text


async def test_cancel_during_a_tool_stops_the_run() -> None:
    cancel = llm.CancelToken()
    server = FakeModelServer([Reply(tool_calls=[("lookup", {"year": 2026})])])
    agent, recorder, _tools = _loop(server, tools=Tools(delay=5), cancel=cancel)
    task = asyncio.create_task(agent.run())
    await asyncio.sleep(0.2)
    cancel.cancel()
    with pytest.raises(LoopCancelled):
        await asyncio.wait_for(task, timeout=5)
    assert recorder.tools[-1].status == "cancelled"


async def test_the_live_adapter_handles_tags_split_across_chunks() -> None:
    lines: list[dict[str, Any]] = []

    async def live(payload: dict[str, Any]) -> None:
        lines.append(payload)

    adapter = LiveAdapter(live, KeyAllocator(4))
    for piece in ["Vor", "her <th", "ink>geheim</th", "ink> nach", "her <tool_", "call>{x}"]:
        await adapter.on_stream(llm.StreamEvent(kind="delta", text=piece))
    await adapter.on_stream(llm.StreamEvent(kind="tool", index=0, name="lookup"))
    await adapter.on_stream(llm.StreamEvent(kind="targs", index=0, text='{"year"'))
    answer = "".join(line["text"] for line in lines if line["type"] == "delta")
    assert answer == "Vorher  nachher "
    assert "".join(line["text"] for line in lines if line["type"] == "think") == "geheim"
    assert lines[-1] == {"type": "targs", "key": "W5", "text": '{"year"'}
    assert adapter.pending == {0: ("W5", 5)}
