"""Anthropic (Claude) als KI-Anbieter: Uebersetzer, Schluessel, Gateway.

Kein echter Aufruf: der Anthropic-Client des SDK bekommt einen
MockTransport, der die Messages-API nachbildet (SSE-Ereignisse wie im
Original). Geprueft wird, was der Kern daraus macht - dieselben Gruende,
dasselbe Budget, dieselbe Antwortform wie bei LM Studio.
"""

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import anthropic
import httpx2
import pytest

from app import llm
from app.llm import anthropic_api, gateway, keys, providers, reasons
from app.llm.budget import compute
from app.llm.discovery import Target
from app.llm.settings import LLMSettings
from app.llm.stream import StreamEvent
from app.llm.window import WindowInfo
from app.schemas.ai import AIModelInfo

KEY = "sk-ant-api03-test-0123456789abcdefWXYZ"
MODEL = "claude-test-5"

ALL_CAPS = {
    "thinking": {
        "supported": True,
        "types": {
            "adaptive": {"supported": True},
            "enabled": {"supported": False},
            "disabled": {"supported": True},
        },
    },
    "effort": {
        "supported": True,
        "low": {"supported": True},
        "medium": {"supported": True},
        "high": {"supported": True},
        "xhigh": {"supported": True},
        "max": {"supported": True},
    },
    "structured_outputs": {"supported": True},
    "image_input": {"supported": True},
}


def settings(**changes: Any) -> LLMSettings:
    base: dict[str, Any] = {
        "is_enabled": True,
        "min_confidence": 0.85,
        "custom_base_url": None,
        "model_name": MODEL,
        "temperature": 1.0,
        "request_timeout_seconds": 30.0,
        "answer_reserve_share": 0.25,
        "start_chars_per_token": 3.0,
        "start_estimate_error": 0.15,
        "provider": providers.ANTHROPIC,
        "api_key": keys.ApiKey(KEY),
    }
    base.update(changes)
    return LLMSettings(**base)


def target(window: int = 200_000, output_limit: int = 8_000) -> Target:
    model = anthropic_api.model_from_api(
        {
            "id": MODEL,
            "display_name": "Claude Test 5",
            "max_input_tokens": window,
            "max_tokens": output_limit,
            "capabilities": ALL_CAPS,
        }
    )
    return Target(
        base_url="https://api.anthropic.com",
        model=MODEL,
        window=anthropic_api.window_of(model, None),
        selected=model.info,
    )


# ---------------------------------------------------------------------------
# Nachgebauter Anthropic-Server
# ---------------------------------------------------------------------------


def sse(*events: dict[str, Any]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def message_events(
    blocks: list[dict[str, Any]],
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    cache_read: int = 0,
    output_tokens: int = 20,
    stop_details: dict[str, Any] | None = None,
) -> bytes:
    """Ein vollstaendiger Strom: message_start, je Block start/delta/stop,
    message_delta, message_stop."""
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 1,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    ]
    for index, block in enumerate(blocks):
        kind = block["type"]
        if kind == "text":
            events.append(
                {"type": "content_block_start", "index": index,
                 "content_block": {"type": "text", "text": ""}}
            )
            events.append(
                {"type": "content_block_delta", "index": index,
                 "delta": {"type": "text_delta", "text": block["text"]}}
            )
        elif kind == "thinking":
            events.append(
                {"type": "content_block_start", "index": index,
                 "content_block": {"type": "thinking", "thinking": "", "signature": ""}}
            )
            events.append(
                {"type": "content_block_delta", "index": index,
                 "delta": {"type": "thinking_delta", "thinking": block["thinking"]}}
            )
            events.append(
                {"type": "content_block_delta", "index": index,
                 "delta": {"type": "signature_delta", "signature": "sig"}}
            )
        elif kind == "tool_use":
            events.append(
                {"type": "content_block_start", "index": index,
                 "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"],
                                   "input": {}}}
            )
            events.append(
                {"type": "content_block_delta", "index": index,
                 "delta": {"type": "input_json_delta",
                           "partial_json": json.dumps(block["input"])}}
            )
        events.append({"type": "content_block_stop", "index": index})
    delta: dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_details is not None:
        delta["stop_details"] = stop_details
    events.append(
        {"type": "message_delta", "delta": delta, "usage": {"output_tokens": output_tokens}}
    )
    events.append({"type": "message_stop"})
    return sse(*events)


class FakeAnthropic:
    """Antwortet der Reihe nach mit den vorbereiteten Antworten und merkt
    sich jede Anfrage (Pfad, Koepfe, Koerper)."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Mehr Anfragen als vorbereitete Antworten.")
        return self.responses.pop(0)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def make_client(
            current: LLMSettings, base_url: str, timeout_seconds: float
        ) -> anthropic.AsyncAnthropic:
            assert current.api_key is not None
            return anthropic.AsyncAnthropic(
                api_key=current.api_key.reveal(),
                base_url=base_url,
                max_retries=0,
                http_client=anthropic.DefaultAsyncHttpxClient(
                    transport=httpx2.MockTransport(self.handler)
                ),
            )

        monkeypatch.setattr(anthropic_api, "make_client", make_client)


def streamed(body: bytes) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def failed(status: int, kind: str, message: str) -> httpx2.Response:
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": kind, "message": message}}
    )


# ---------------------------------------------------------------------------
# Uebersetzung
# ---------------------------------------------------------------------------


def test_systemnachrichten_werden_zu_system_und_werkzeugrunden_zu_bloecken() -> None:
    system, converted = anthropic_api.convert_messages(
        [
            {"role": "system", "content": "Regeln"},
            {"role": "user", "content": "Wie hoch ist mein Saldo?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call 1", "type": "function",
                     "function": {"name": "konten", "arguments": '{"art": "giro"}'}},
                    {"id": "call_2", "type": "function",
                     "function": {"name": "budget", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call 1", "content": "1.000 €"},
            {"role": "tool", "tool_call_id": "call_2", "content": "ok"},
            {"role": "user", "content": "Danke"},
        ],
        with_tools=True,
    )
    assert system == "Regeln"
    assert [message["role"] for message in converted] == ["user", "assistant", "user"]
    tool_uses = converted[1]["content"]
    assert [block["type"] for block in tool_uses] == ["tool_use", "tool_use"]
    # Kennungen nur aus [A-Za-z0-9_-]
    assert tool_uses[0]["id"] == "call_1"
    assert tool_uses[0]["input"] == {"art": "giro"}
    # Beide Ergebnisse in EINER user-Nachricht, vorn; die Folgefrage danach.
    last = converted[2]["content"]
    assert [block["type"] for block in last] == ["tool_result", "tool_result", "text"]
    assert last[0]["tool_use_id"] == "call_1"
    assert last[2]["text"] == "Danke"


def test_fehlendes_werkzeugergebnis_wird_als_fehler_benannt() -> None:
    _system, converted = anthropic_api.convert_messages(
        [
            {"role": "user", "content": "Frage"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "a", "function": {"name": "x", "arguments": "{}"}}]},
            {"role": "assistant", "content": "Antwort"},
        ],
        with_tools=True,
    )
    assert [message["role"] for message in converted] == ["user", "assistant", "user", "assistant"]
    filler = converted[2]["content"][0]
    assert filler["type"] == "tool_result"
    assert filler["is_error"] is True


def test_ohne_werkzeuge_werden_aufrufe_und_ergebnisse_zu_text() -> None:
    _system, converted = anthropic_api.convert_messages(
        [
            {"role": "user", "content": "Frage"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "a", "function": {"name": "konten", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "Ergebnis"},
            {"role": "user", "content": "Bitte jetzt antworten."},
        ],
        with_tools=False,
    )
    kinds = {block["type"] for message in converted for block in message["content"]}
    assert kinds == {"text"}
    assert "Werkzeugaufruf konten" in converted[1]["content"][0]["text"]


def test_doppelte_kennungen_werden_eindeutig() -> None:
    _system, converted = anthropic_api.convert_messages(
        [
            {"role": "user", "content": "Frage"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "pseudo_1", "function": {"name": "x", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "pseudo_1", "content": "eins"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "pseudo_1", "function": {"name": "x", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "pseudo_1", "content": "zwei"},
        ],
        with_tools=True,
    )
    first = converted[1]["content"][0]["id"]
    second = converted[3]["content"][0]["id"]
    assert first != second
    assert converted[4]["content"][0]["tool_use_id"] == second


def test_erste_nachricht_ist_immer_vom_nutzer() -> None:
    _system, converted = anthropic_api.convert_messages(
        [{"role": "system", "content": "S"}, {"role": "assistant", "content": "Hallo"}],
        with_tools=False,
    )
    assert converted[0]["role"] == "user"


def test_schema_wird_nur_bei_festen_feldern_erzwungen() -> None:
    closed = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 5}},
              "required": ["a"]}
    output, note = anthropic_api.convert_format(llm.answers.json_schema_format("x", closed))
    assert note is None
    assert output is not None and output["type"] == "json_schema"
    assert output["schema"]["additionalProperties"] is False
    free = {"type": "object", "properties": {"m": {"type": "object",
                                                    "additionalProperties": {"type": "number"}}}}
    output, note = anthropic_api.convert_format(llm.answers.json_schema_format("x", free))
    assert output is None and note
    assert anthropic_api.convert_format({"type": "json_object"}) == (None, None)


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("auto", {"type": "auto"}),
        ("none", {"type": "none"}),
        ("required", {"type": "any"}),
        ({"type": "function", "function": {"name": "f"}}, {"type": "tool", "name": "f"}),
        (None, None),
    ],
)
def test_werkzeugwahl(choice: Any, expected: Any) -> None:
    assert anthropic_api.convert_tool_choice(choice) == expected


def test_modellliste_liefert_fenster_antwortgrenze_und_denkstufen() -> None:
    model = anthropic_api.model_from_api(
        {"id": MODEL, "max_input_tokens": 1_000_000, "max_tokens": 128_000,
         "capabilities": ALL_CAPS}
    )
    assert model.info.reasoning_options == ["off", "on", "low", "medium", "high", "xhigh", "max"]
    window = anthropic_api.window_of(model, 50_000)
    assert (window.loaded, window.cap, window.effective) == (1_000_000, 50_000, 50_000)
    assert window.output_limit == 128_000
    no_off = anthropic_api.model_from_api(
        {"id": "y", "max_input_tokens": 10, "capabilities": {
            "thinking": {"supported": True, "types": {"adaptive": {"supported": True},
                                                      "disabled": {"supported": False}}}}}
    )
    assert "off" not in no_off.info.reasoning_options


def test_reasoning_einstellung_wird_fuer_claude_uebersetzt() -> None:
    caps = target().selected.capabilities  # type: ignore[union-attr]
    assert anthropic_api.reasoning_choice(settings(), None, caps) == anthropic_api.ReasoningChoice()
    off = anthropic_api.reasoning_choice(settings(reasoning="off"), None, caps)
    assert off.thinking == {"type": "disabled"}
    high = anthropic_api.reasoning_choice(settings(reasoning="high"), None, caps)
    assert high.effort == "high"
    assert high.thinking == {"type": "adaptive", "display": "summarized"}
    # Ueberschreibung des Chats vor der Einstellung
    on = anthropic_api.reasoning_choice(settings(reasoning="off"), "on", caps)
    assert on.thinking == {"type": "adaptive", "display": "summarized"}
    # Nicht angeboten: nichts senden, sagen warum
    minimal = anthropic_api.reasoning_choice(settings(reasoning="minimal"), None, caps)
    assert minimal.thinking is None and minimal.effort is None and minimal.skipped
    no_disable = anthropic_api.reasoning_choice(
        settings(reasoning="off"), None, ["thinking:adaptive"]
    )
    assert no_disable.thinking is None and no_disable.skipped


def test_antwortgrenze_deckelt_max_tokens_nicht_aber_passt() -> None:
    budget = compute(
        window=1_000_000,
        messages=[{"role": "user", "content": "x" * 300}],
        chars_per_token=3.0,
        estimate_error=0.15,
        reserve_share=0.25,
        output_limit=128_000,
    )
    assert budget.max_tokens == 128_000
    assert budget.fits


def test_lokaler_anbieter_behaelt_seine_kandidaten_claude_hat_genau_eine_adresse() -> None:
    assert settings().candidate_urls() == ["https://api.anthropic.com"]
    local = settings(provider=providers.LOCAL, api_key=None, custom_base_url="http://lm:1234/v1")
    assert local.candidate_urls()[0] == "http://lm:1234/v1"
    assert "https://api.anthropic.com" not in local.candidate_urls()


@pytest.mark.parametrize(
    ("status", "text", "code"),
    [
        (401, "authentication_error: invalid x-api-key", reasons.MODEL_KEY_REJECTED),
        (403, "permission_error: nope", reasons.MODEL_KEY_REJECTED),
        (
            400,
            "invalid_request_error: Your credit balance is too low",
            reasons.MODEL_CREDIT_EXHAUSTED,
        ),
        (429, "rate_limit_error: slow down", reasons.MODEL_RATE_LIMITED),
        (529, "overloaded_error: Overloaded", reasons.MODEL_UNAVAILABLE),
        (400, "invalid_request_error: prompt is too long: 250000 tokens", None),
    ],
)
def test_fehler_des_anbieters_bekommen_ihren_grund(
    status: int, text: str, code: str | None
) -> None:
    assert anthropic_api.error_code(status, text) == code


# ---------------------------------------------------------------------------
# Schluessel
# ---------------------------------------------------------------------------


@pytest.fixture
def server_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "get_settings", lambda: SimpleNamespace(supabase_jwt_secret="s" * 40))


@pytest.mark.usefixtures("server_secret")
def test_schluessel_ist_an_haushalt_und_ziel_gebunden() -> None:
    household = uuid4()
    blob = keys.encrypt(household, "https://api.anthropic.com:443", KEY)
    assert KEY not in blob
    assert keys.decrypt(household, "https://api.anthropic.com:443", blob) == KEY
    with pytest.raises(keys.ProviderKeyError):
        keys.decrypt(uuid4(), "https://api.anthropic.com:443", blob)
    with pytest.raises(keys.ProviderKeyError):
        keys.decrypt(household, "https://evil.example:443", blob)


def test_ohne_servergeheimnis_kein_schluessel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "get_settings", lambda: SimpleNamespace(supabase_jwt_secret=""))
    with pytest.raises(keys.ProviderKeyError):
        keys.encrypt(uuid4(), "https://api.anthropic.com:443", KEY)


def test_schluessel_zeigt_sich_nirgends() -> None:
    api_key = keys.ApiKey(KEY)
    assert KEY not in repr(api_key) and KEY not in str(api_key)
    assert KEY not in repr(settings())
    assert api_key.ending == "…WXYZ"
    assert api_key.mask(f"bad key {KEY}") == "bad key …WXYZ"
    with pytest.raises(keys.ProviderKeyError):
        keys.check_key("sk-ant mit leerzeichen")
    assert keys.check_key(f"  {KEY}\n") == KEY


# ---------------------------------------------------------------------------
# Gateway gegen den nachgebauten Server
# ---------------------------------------------------------------------------


async def test_strukturierte_antwort_mit_cache_und_gedeckeltem_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeAnthropic(
        streamed(
            message_events(
                [{"type": "thinking", "thinking": "kurz nachdenken"},
                 {"type": "text", "text": '{"kategorie": "Lebensmittel"}'}],
                input_tokens=40, cache_read=60, output_tokens=30,
            )
        )
    )
    server.install(monkeypatch)
    schema = {"type": "object", "properties": {"kategorie": {"type": "string"}},
              "required": ["kategorie"]}
    result = await llm.chat_structured(
        llm.ChatRequest(
            messages=[{"role": "system", "content": "Ordne zu."},
                      {"role": "user", "content": "REWE 12,34 €"}],
            response_format=llm.answers.json_schema_format("kat", schema),
        ),
        target=target(window=200_000, output_limit=8_000),
        settings=settings(reasoning="high"),
    )
    assert result.ok, result.message
    assert json.loads(result.content) == {"kategorie": "Lebensmittel"}
    assert result.reasoning_text == "kurz nachdenken"
    # Eingabe = nicht gecachter Teil + Cache: so stimmt die Messung.
    assert result.usage.prompt_tokens == 100
    assert result.usage.completion_tokens == 30
    request = server.requests[0]
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == KEY
    body = server.bodies()[0]
    assert body["max_tokens"] == 8_000  # Antwortgrenze des Modells, nicht der freie Platz
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "high"
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "REWE 12,34 €"}]}
    ]
    assert result.reasoning_sent == "effort=high"


async def test_werkzeugrunde_wird_live_gemeldet(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeAnthropic(
        streamed(
            message_events(
                [{"type": "text", "text": "Ich schaue nach."},
                 {"type": "tool_use", "id": "toolu_1", "name": "konten", "input": {"art": "giro"}}],
                stop_reason="tool_use",
            )
        )
    )
    server.install(monkeypatch)
    seen: list[StreamEvent] = []

    async def on_stream(event: StreamEvent) -> None:
        seen.append(event)

    tools = [{"type": "function", "function": {"name": "konten", "description": "Konten",
                                               "parameters": {"type": "object", "properties": {}}}}]
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "Saldo?"}], tools=tools,
                        tool_choice="auto"),
        target=target(),
        settings=settings(),
        hooks=llm.ChatHooks(on_stream=on_stream),
    )
    assert result.ok, result.message
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [gateway.ToolCall(id="toolu_1", name="konten",
                                                  arguments='{"art": "giro"}')]
    assert [event.kind for event in seen] == ["delta", "tool", "targs"]
    body = server.bodies()[0]
    assert body["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert body["tool_choice"] == {"type": "auto"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in body  # ohne Einstellung: Modellstandard


async def test_ohne_schluessel_geht_nichts_hinaus(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeAnthropic()
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "Hallo"}]),
        target=target(),
        settings=settings(api_key=None, key_problem="nicht lesbar"),
    )
    assert result.reason_code == reasons.MODEL_KEY_MISSING
    assert "nicht lesbar" in (result.message or "")
    assert server.requests == []


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (lambda: failed(401, "authentication_error", f"invalid x-api-key {KEY}"),
         reasons.MODEL_KEY_REJECTED),
        (lambda: failed(400, "invalid_request_error",
                        "Your credit balance is too low to access the Anthropic API."),
         reasons.MODEL_CREDIT_EXHAUSTED),
        (lambda: failed(429, "rate_limit_error", "Number of requests has exceeded your limit"),
         reasons.MODEL_RATE_LIMITED),
    ],
)
async def test_fehler_des_anbieters_werden_benannt_und_der_schluessel_nie_gezeigt(
    monkeypatch: pytest.MonkeyPatch, response: Callable[[], httpx2.Response], code: str
) -> None:
    server = FakeAnthropic(response())
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "Hallo"}]),
        target=target(),
        settings=settings(),
    )
    assert result.reason_code == code
    assert KEY not in (result.message or "")


async def test_ueberlauf_mit_echter_eingabe_wird_korrigiert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeAnthropic(
        failed(400, "invalid_request_error", "prompt is too long: 250000 tokens > 200000 maximum")
    )
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "x" * 3000}]),
        target=target(window=200_000),
        settings=settings(),
    )
    # Mit der echten Eingabe passt die Anfrage nicht mehr - kein zweiter Versuch.
    assert result.reason_code == reasons.MODEL_INPUT_TOO_LARGE
    overflow = [event for event in result.events if event.code == reasons.MODEL_CONTEXT_OVERFLOW]
    assert overflow and overflow[0].details["input_tokens"] == 250000
    assert len(server.requests) == 1


async def test_verweigerte_antwort_ist_keine_antwort(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeAnthropic(
        streamed(
            message_events(
                [{"type": "text", "text": "Teil"}],
                stop_reason="refusal",
                stop_details={"type": "refusal", "category": "cyber", "explanation": None},
            )
        )
    )
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "Hallo"}]),
        target=target(),
        settings=settings(),
    )
    assert result.reason_code == reasons.MODEL_REFUSED
    assert "cyber" in (result.message or "")


async def test_abgeschnittene_antwort_heisst_budget_erschoepft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeAnthropic(
        streamed(
            message_events([{"type": "thinking", "thinking": "lang"}], stop_reason="max_tokens")
        )
    )
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "Hallo"}]),
        target=target(),
        settings=settings(),
    )
    assert result.reason_code == reasons.MODEL_BUDGET_EXHAUSTED


async def test_denkstufe_die_der_server_ablehnt_wird_benannt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeAnthropic(
        failed(400, "invalid_request_error", "thinking.type: disabled is not supported")
    )
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "Hallo"}]),
        target=target(),
        settings=settings(reasoning="off"),
    )
    assert result.reason_code == reasons.MODEL_REASONING_UNSUPPORTED
    assert "thinking=disabled" in (result.message or "")


async def test_modellwahl_bei_claude_ist_pflicht(monkeypatch: pytest.MonkeyPatch) -> None:
    async def listing(current: LLMSettings, base_url: str) -> list[anthropic_api.ClaudeModel]:
        return [anthropic_api.model_from_api({"id": MODEL, "max_input_tokens": 1000,
                                              "max_tokens": 100, "capabilities": ALL_CAPS})]

    monkeypatch.setattr(anthropic_api, "list_models", listing)
    found = await llm.resolve_target(settings(model_name=None))
    assert found.reason_code == reasons.MODEL_NOT_CHOSEN
    assert found.models and found.models[0].id == MODEL
    found = await llm.resolve_target(settings(model_name="claude-gibt-es-nicht"))
    assert found.reason_code == reasons.MODEL_MISSING
    found = await llm.resolve_target(settings())
    assert found.ready
    assert found.target is not None and isinstance(found.target.window, WindowInfo)
    assert found.target.window.output_limit == 100
    missing = await llm.resolve_target(settings(api_key=None))
    assert missing.reason_code == reasons.MODEL_KEY_MISSING


def test_modellinfo_kennt_anzeigenamen() -> None:
    info = target().selected
    assert isinstance(info, AIModelInfo)
    assert info.display_name == "Claude Test 5"
    assert info.output_limit_tokens == 8_000


async def test_manuelles_sampling_geht_nicht_an_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeAnthropic(streamed(message_events([{"type": "text", "text": "ok"}])))
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "Hallo"}]),
        target=target(),
        settings=settings(sampling_mode="manual", temperature=0.2, top_p=0.5, top_k=10),
    )
    assert result.ok, result.message
    body = server.bodies()[0]
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert any(event.code == "anthropic_request_adjusted" for event in result.events)


def test_bilder_werden_zu_bildbloecken_und_gehen_nie_still_verloren() -> None:
    _system, converted = anthropic_api.convert_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Lies die Seite."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0"}},
                    {"type": "image_url", "image_url": {"url": "data:image/tiff;base64,AAAA"}},
                ],
            }
        ],
        with_tools=False,
    )
    blocks = converted[0]["content"]
    assert blocks[0] == {"type": "text", "text": "Lies die Seite."}
    assert blocks[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0"},
    }
    assert blocks[2]["type"] == "text" and "image/tiff" in blocks[2]["text"]


class _BrokenStream(httpx2.AsyncByteStream):
    """Strom, der nach einer Teilantwort abreisst (Verbindung weg)."""

    def __init__(self, head: bytes) -> None:
        self.head = head

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield self.head
        raise httpx2.RemoteProtocolError("peer closed connection without sending complete body")

    async def aclose(self) -> None:
        return None


async def test_abriss_mitten_im_strom_ist_benannt_und_wirft_nicht(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete = message_events([{"type": "text", "text": "Teilantwort"}])
    head = complete[: complete.index(b"event: content_block_stop")]
    server = FakeAnthropic(
        httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_BrokenStream(head)
        )
    )
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "Hallo"}]),
        target=target(),
        settings=settings(),
    )
    assert result.reason_code == reasons.MODEL_UNAVAILABLE
    assert "RemoteProtocolError" in (result.message or "")


async def test_werkzeugnummern_zaehlen_aufrufe_nicht_inhaltsbloecke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeAnthropic(
        streamed(
            message_events(
                [
                    {"type": "thinking", "thinking": "erst nachdenken"},
                    {"type": "tool_use", "id": "toolu_a", "name": "konten", "input": {}},
                    {"type": "tool_use", "id": "toolu_b", "name": "budget", "input": {}},
                ],
                stop_reason="tool_use",
            )
        )
    )
    server.install(monkeypatch)
    seen: list[StreamEvent] = []

    async def on_stream(event: StreamEvent) -> None:
        seen.append(event)

    tools = [
        {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
        for name in ("konten", "budget")
    ]
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "?"}], tools=tools),
        target=target(),
        settings=settings(),
        hooks=llm.ChatHooks(on_stream=on_stream),
    )
    assert result.ok, result.message
    assert [(event.index, event.name) for event in seen if event.kind == "tool"] == [
        (0, "konten"),
        (1, "budget"),
    ]
    assert [call.name for call in result.tool_calls] == ["konten", "budget"]


async def test_anderer_400_mit_schema_ist_keine_fehlende_faehigkeit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeAnthropic(
        failed(
            400,
            "invalid_request_error",
            "In order to access this model, your organization must have data retention enabled.",
        )
    )
    server.install(monkeypatch)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    result = await llm.chat_structured(
        llm.ChatRequest(
            messages=[{"role": "user", "content": "x"}],
            response_format=llm.answers.json_schema_format("x", schema),
        ),
        target=target(),
        settings=settings(),
    )
    assert result.reason_code == reasons.MODEL_HTTP_ERROR
    assert not result.schema_fallback
    assert len(server.requests) == 1


async def test_verweigerte_antwort_hinterlaesst_keinen_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeAnthropic(
        streamed(message_events([{"type": "text", "text": "Halbe Antwort"}], stop_reason="refusal"))
    )
    server.install(monkeypatch)
    result = await llm.chat(
        llm.ChatRequest(messages=[{"role": "user", "content": "x"}]),
        target=target(),
        settings=settings(),
    )
    assert result.reason_code == reasons.MODEL_REFUSED
    assert result.content == "" and result.tool_calls == []


def test_ohne_servergeheimnis_heisst_es_auch_beim_lesen_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keys, "get_settings", lambda: SimpleNamespace(supabase_jwt_secret=""))
    with pytest.raises(keys.ProviderKeyError, match="SUPABASE_JWT_SECRET"):
        keys.decrypt(uuid4(), "https://api.anthropic.com:443", "AAAA")


def test_nicht_umformbares_schema_wird_nicht_erzwungen() -> None:
    output, note = anthropic_api.convert_format(
        llm.answers.json_schema_format(
            "x", {"type": "object", "properties": {"a": {}}, "required": ["a"]}
        )
    )
    assert output is None and note
