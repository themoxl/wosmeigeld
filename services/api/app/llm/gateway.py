"""Der EINZIGE Weg zu einem Chat-Completion-Aufruf (OpenAI-kompatibel).

Was jeder Aufruf hier bekommt, ohne dass die Aufrufstelle es wiederholen muss:

* Antwortbudget aus dem GELADENEN Fenster (app/llm/budget.py):
  max_tokens = W_frei - E - A, gesendet nur, wenn E + A <= W_frei - R.
  Der Aufrufer gibt KEIN max_tokens vor - es gibt dafuer keinen Parameter.
* Kein Aufruf ohne Fenster (model_context_unknown), ohne passende Eingabe
  (model_input_too_large, mit Aufschluesselung), bei ausgeschalteter KI
  (ai_disabled). Jeder Fehlschlag ist ein benannter Grund (app/llm/reasons.py)
  mit Klartext - nie ein stilles Uebersteuern.
* Reasoning nur, wenn die Haushaltseinstellung (oder die Uebersteuerung des
  Aufrufers) einen Wert hat: off -> "none", on -> "high", Stufen unveraendert.
  Meldet das Modell keine Denkfaehigkeit, wird nichts gesendet
  (Info-Ereignis model_reasoning_unsupported). Kein automatisches "ohne
  Denken wiederholen" (E1).
* Fensterueberlauf des Servers ("Context size has been exceeded", "input has
  N tokens"): Messung mit der echten Eingabe korrigieren, EINMAL neu rechnen
  (Ereignis model_context_overflow). Ein HTTP 400 wegen Ueberlauf ist damit
  etwas anderes als "Server kann kein Schema" (model_schema_unsupported).
* Messung (ai_model_measurements) aus usage.prompt_tokens fortschreiben;
  fehlt usage, Info-Ereignis model_usage_unavailable.
* Genau eine llm_calls-Zeile je Aufruf mit Budgetzeile (app/llm/calls_log.py).

Erweiterungspunkte fuer Etappe 2 (KI-Feld), ohne bestehende Aufrufer zu
aendern:
* Werkzeuge: ChatRequest.tools / tool_choice werden heute schon gesendet und
  gezaehlt (tool_defs), ChatResult.tool_calls traegt die Aufrufe.
* Abbruch: ChatHooks.cancel (CancelToken) - heute schon wirksam: der
  laufende Request wird abgebrochen, die Verbindung zu LM Studio schliesst.
* Verdichtung: ChatHooks.compact wird gerufen, wenn die Eingabe nicht passt
  oder der Server einen Ueberlauf meldet; liefert er eine kleinere Liste,
  wird einmal neu gerechnet.
* Exakte Zaehlung ueber mehrere Aufrufe eines Laufs: ChatRequest.basis rein,
  ChatResult.basis raus.
* Streaming: ChatHooks.on_stream gesetzt -> SSE mit
  stream_options.include_usage (lehnt der Server das mit HTTP 400 ab, genau
  einmal ohne; dann fehlen die Tokenzahlen und das Ereignis
  model_usage_unavailable sagt warum). Jedes Stueck Denktext, Antwort und
  Werkzeugaufruf geht live an den Rueckruf (app/llm/stream.py); das Ergebnis
  hat dieselbe Form wie beim blockierenden Aufruf. Der Abbruch schliesst die
  Verbindung auch in der stillen Prefill-Phase.
* Frist des ganzen Aufrufs: ChatHooks.deadline (time.monotonic()), z. B. das
  Ende der Laufzeit eines KI-Laufs -> model_timeout mit deadline_reached.

Anthropic (Einstellung provider = "anthropic"): dieselben Regeln, anderer
Transport. Die Anfrage wird in die Messages-API uebersetzt und immer
gestreamt, die Antwort in die OpenAI-Form zurueckgebracht
(app/llm/anthropic_api.py) - Budget, Messung, Protokoll und Gruende sind
dieselben. Zusaetzlich: kein Aufruf ohne hinterlegten Schluessel
(model_key_missing), max_tokens hoechstens die Antwortgrenze des Modells,
eigene Gruende fuer abgelehnten Schluessel, aufgebrauchtes Guthaben,
Ratenlimit und eine verweigerte Antwort (model_refused).
"""

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID, uuid4

import httpx

from app.llm import anthropic_api, providers, reasons
from app.llm.budget import (
    Basis,
    Budget,
    BudgetInputError,
    compute,
    number_de,
    tools_signature,
)
from app.llm.calls_log import CallScope, status_for, write_call
from app.llm.discovery import Target
from app.llm.measurements import (
    Measurement,
    load_measurement,
    record_measurement,
    server_target_of,
    start_measurement,
)
from app.llm.settings import LLMSettings
from app.llm.stream import StreamAccumulator, StreamCallback, StreamEvent
from app.llm.usage import UsageTokens, extract_usage_tokens
from app.llm.window import ReasoningCapability, WindowInfo

Message = dict[str, Any]

# Server (server_target), die stream_options mit HTTP 400 abgelehnt haben -
# fuer die Lebensdauer des Prozesses, damit nicht jeder Aufruf erst scheitert.
# Eine Faehigkeit des Servers, kein KI-Parameter.
_NO_STREAM_OPTIONS: set[str] = set()

# HTTP-Status, mit denen ein OpenAI-kompatibler Server einen nicht
# unterstuetzten Parameter ablehnt. Nur wenn der Fehlertext KEIN Ueberlauf ist
# und strukturierte Ausgabe/Werkzeuge gesendet wurden, gilt das als
# "Schema nicht unterstuetzt" - frueher wurde jeder 400 so gewertet.
CAPABILITY_REJECT_STATUS: Final = frozenset({400, 404, 422, 501})

# Texte, mit denen Server einen Fensterueberlauf melden (LM Studio,
# llama.cpp, vLLM/OpenAI, Anthropic).
_OVERFLOW = re.compile(
    r"context size has been exceeded|does not fit in(?:to)? (?:the )?context"
    r"|context length|context window|maximum context|n_ctx|too many tokens"
    r"|prompt is too long|exceeds? (?:the )?context limit",
    re.IGNORECASE,
)
# Wo der Server die echte Eingabe nennt.
_INPUT_TOKENS = re.compile(
    r"input has (\d+) tokens|(\d+) in the messages|prompt has (\d+) tokens"
    r"|(\d+) prompt tokens|prompt is too long: (\d+) tokens|context limit: (\d+) \+",
    re.IGNORECASE,
)
# Ablehnung der Reasoning-Einstellung: reasoning_effort (OpenAI-kompatibel),
# bei Anthropic zusaetzlich thinking/effort - nur dort, damit ein lokaler 400
# mit diesen Woertern nicht anders gewertet wird als bisher.
_REASONING_REJECTED = re.compile(r"reasoning[_ ]?effort|reasoning setting", re.IGNORECASE)
_REASONING_REJECTED_CLOUD = re.compile(
    r"reasoning[_ ]?effort|reasoning setting|\bthinking\b|\beffort\b", re.IGNORECASE
)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    # JSON-Text der Argumente, so wie das Modell ihn geliefert hat.
    arguments: str


@dataclass(frozen=True)
class ChatEvent:
    """Benannte Beobachtung waehrend eines Aufrufs (Info oder Warnung). Der
    Aufrufer kann sie ins Laufprotokoll uebernehmen (ai_task_events,
    assistant_run_events)."""

    code: str
    level: reasons.Level
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class CancelToken:
    """Abbruch eines laufenden Aufrufs von aussen (Nutzer klickt "Abbrechen").

    cancel() darf aus jeder Coroutine derselben Ereignisschleife kommen; der
    Gateway bricht den HTTP-Request dann ab, wodurch die Verbindung zum
    Modellserver schliesst und dieser aufhoert zu rechnen."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


# Verdichtung: bekommt die Nachrichtenliste und das Budget, das nicht passt;
# liefert eine kleinere Liste oder None (kann nichts verdichten).
CompactHook = Callable[[list[Message], Budget], Awaitable[list[Message] | None]]


@dataclass(frozen=True)
class ChatHooks:
    """Optionale Eingriffe in einen Aufruf.

    cancel     Abbruch von aussen; schliesst die Verbindung zum Modellserver
    compact    Verdichtung, wenn die Eingabe nicht passt (siehe CompactHook)
    on_stream  gesetzt -> SSE-Streaming; jedes Stueck Denktext/Antwort/
               Werkzeugaufruf geht live an diesen Rueckruf (nie gespeichert)
    deadline   Frist des ganzen Aufrufs als time.monotonic()-Zeitpunkt, z. B.
               das Ende der Laufzeit eines KI-Laufs"""

    cancel: CancelToken | None = None
    compact: CompactHook | None = None
    on_stream: StreamCallback | None = None
    deadline: float | None = None


@dataclass(frozen=True)
class ChatRequest:
    """Was gesendet werden soll - ohne max_tokens (das rechnet der Gateway).

    messages          OpenAI-Nachrichten (system/user/assistant/tool)
    response_format   z. B. {"type": "json_schema", "json_schema": {...}}
    tools/tool_choice Werkzeuge (OpenAI-Form)
    reasoning_override  None = Haushaltseinstellung; sonst on/off/Stufe
    basis             exakte Zaehlung des Vorgaengers im selben Lauf"""

    messages: list[Message]
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    reasoning_override: str | None = None
    basis: Basis | None = None


@dataclass
class ChatResult:
    """Ergebnis eines Aufrufs - auch eines nicht gesendeten.

    reason_code None heisst: HTTP 200 mit Inhalt oder Werkzeugaufrufen.
    reasoning_text lebt nur im Speicher; gespeichert wird hoechstens seine
    Laenge."""

    reason_code: str | None
    message: str | None
    model: str
    window: WindowInfo | None
    content: str = ""
    reasoning_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: UsageTokens = field(default_factory=UsageTokens)
    budget: Budget | None = None
    measurement: Measurement | None = None
    http_status: int | None = None
    duration_ms: int = 0
    reasoning_sent: str | None = None
    events: list[ChatEvent] = field(default_factory=list)
    basis: Basis | None = None
    messages_sent: list[Message] = field(default_factory=list)
    # llm_calls-Zeile des (letzten) Versuchs; None ohne Protokoll.
    call_id: UUID | None = None
    # Vollstaendige Antwort des Servers (nur bei HTTP 200).
    payload: dict[str, Any] | None = None
    # chat_structured(): True, wenn dieses Ergebnis vom zweiten Aufruf OHNE
    # response_format stammt, weil der Server das Schema abgelehnt hat.
    schema_fallback: bool = False
    # Streaming: True, wenn die Antwort als SSE kam; ttft_ms = Zeit bis zum
    # ersten Stueck (Denktext, Antwort oder Werkzeugaufruf) des letzten
    # Versuchs.
    streamed: bool = False
    ttft_ms: int | None = None
    # model_timeout, weil ChatHooks.deadline erreicht war (nicht die Stille
    # einer einzelnen Anfrage).
    deadline_reached: bool = False

    @property
    def ok(self) -> bool:
        return self.reason_code is None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


def reasoning_plan(
    settings: LLMSettings, override: str | None, capability: ReasoningCapability
) -> tuple[str | None, ChatEvent | None]:
    """Welcher reasoning_effort geht raus - und warum ggf. keiner.

    Ohne Einstellung wird nichts gesendet (Modellstandard). Meldet LM Studio
    ausdruecklich keine Denkfaehigkeit, wird der Wert nicht gesendet und das
    als Info-Ereignis gesagt - der Schalter wirkt bei diesem Modell nicht."""
    effort = settings.reasoning_effort(override)
    if effort is None:
        return None, None
    if capability.kind == "none":
        chosen = settings.effective_reasoning(override)
        return None, ChatEvent(
            code=reasons.MODEL_REASONING_UNSUPPORTED,
            level="info",
            message=(
                "Das Modell meldet keine Denkfähigkeit; die Reasoning-Einstellung "
                f"„{chosen}“ wird nicht gesendet."
            ),
        )
    return effort, None


def build_body(
    *,
    model: str,
    messages: Sequence[Message],
    settings: LLMSettings,
    max_tokens: int,
    response_format: dict[str, Any] | None = None,
    tools: Sequence[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    reasoning_override: str | None = None,
) -> dict[str, Any]:
    """Anfragekoerper. Sampling nur, wenn ein Profil oder der manuelle Modus
    es vorgibt; response_format, Werkzeuge und reasoning_effort nur, wenn
    gesetzt - sonst gilt der Server-/Modellstandard."""
    body: dict[str, Any] = {"model": model, "messages": list(messages), "max_tokens": max_tokens}
    body.update(settings.sampling_parameters(model, reasoning_override))
    if response_format is not None:
        body["response_format"] = response_format
    if tools:
        body["tools"] = list(tools)
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    return body


def error_text(response: Any) -> str:
    """Fehlermeldung aus einer Serverantwort. LM Studios Fehlerform ist
    uneinheitlich: {"error": "text"}, {"error": {"message": ...}}, Klartext."""
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - kein JSON ist eine erlaubte Fehlerform
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if isinstance(error, str):
            return error
        for key in ("detail", "message"):
            if isinstance(payload.get(key), str):
                return str(payload[key])
    raw = getattr(response, "text", "")
    return raw if isinstance(raw, str) else ""


def _short(value: str) -> str:
    # fest: Kuerzung einer Servermeldung fuer Protokoll und Anzeige, kein Promptmaterial
    return value if len(value) <= 600 else value[:600] + " …"


def overflow_input_tokens(message: str) -> int | None:
    """Die echte Eingabe, wenn der Server sie in der Ueberlaufmeldung nennt."""
    match = _INPUT_TOKENS.search(message)
    if not match:
        return None
    for group in match.groups():
        if group:
            return int(group)
    return None


def is_overflow(message: str) -> bool:
    return bool(_OVERFLOW.search(message))


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _tool_calls_of(message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments")
        calls.append(
            ToolCall(
                id=str(raw.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=arguments if isinstance(arguments, str) else _json_text(arguments),
            )
        )
    return calls


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


async def measurement_for(
    target: Target, settings: LLMSettings, scope: CallScope | None
) -> Measurement:
    """Messung, mit der fuer dieses Ziel gerechnet wird: aus der Datenbank,
    wenn ein Haushaltskontext da ist, sonst die Startwerte der Einstellung."""
    if scope is None:
        return start_measurement(settings)
    return await load_measurement(
        scope.session, scope.household_id, target.base_url, target.model, settings
    )


def plan_budget(
    messages: Sequence[Message],
    *,
    window: WindowInfo,
    settings: LLMSettings,
    measurement: Measurement,
    tools: Sequence[dict[str, Any]] | None = None,
    basis: Basis | None = None,
) -> Budget:
    """Budget fuer eine Nachrichtenliste, ohne zu senden - fuer Aufrufer, die
    ihre Eingabe vorab an das Fenster anpassen (z. B. Kandidatenliste ohne
    Beschreibungen). Wirft BudgetInputError wie compute(). Nennt der Server
    eine Antwortgrenze des Modells (window.output_limit), ist max_tokens
    hoechstens diese."""
    return compute(
        window=window.effective,
        messages=messages,
        tools=tools,
        basis=basis,
        chars_per_token=measurement.chars_per_token,
        estimate_error=measurement.estimate_error,
        reserve_share=settings.answer_reserve_share,
        output_limit=window.output_limit,
    )


class _Cancelled(Exception):
    pass


class _DeadlineReached(Exception):
    pass


async def _race[T](
    work: Awaitable[T], cancel: CancelToken | None, deadline: float | None
) -> T:
    """Wartet auf `work`, bis Abbruch oder Frist dazwischenkommen.

    Die Arbeit laeuft in einer eigenen Task; gewinnt der Abbruch (oder die
    Frist), wird sie abgebrochen - httpx schliesst dabei die Verbindung, und
    der Modellserver hoert auf zu rechnen, auch mitten in der stillen
    Prefill-Phase, in der noch kein Byte kam."""
    if cancel is None and deadline is None:
        return await work
    task = asyncio.ensure_future(work)
    if cancel is not None and cancel.cancelled:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise _Cancelled
    stop = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
    waiting: set[asyncio.Future[Any]] = {task}
    if stop is not None:
        waiting.add(stop)
    timeout = max(0.0, deadline - time.monotonic()) if deadline is not None else None
    try:
        done, _pending = await asyncio.wait(
            waiting, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        # Auch wenn der Aufrufer selbst abgebrochen wird: keine Task bleibt
        # haengen, die Verbindung schliesst in jedem Fall.
        leftovers = [item for item in waiting if not item.done()]
        for item in leftovers:
            item.cancel()
        await asyncio.gather(*leftovers, return_exceptions=True)
    if task in done:
        return task.result()
    if stop is not None and stop in done:
        raise _Cancelled
    raise _DeadlineReached


@dataclass
class _Attempt:
    """Ergebnis eines HTTP-Versuchs, gleich ob blockierend oder gestreamt.

    error ist gesetzt, wenn der Server nicht mit einer Antwort, sondern mit
    einem Fehler geantwortet hat (Status != 200 oder Fehler mitten im Strom);
    response hat .json()/.text wie eine httpx-Antwort."""

    status: int
    error: str | None
    response: Any
    ttft_ms: int | None = None


class _Assembled:
    """Aus dem Strom zusammengesetzte Antwort in der Form der blockierenden."""

    def __init__(self, payload: Any, text: str = "") -> None:
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("keine JSON-Antwort")
        return self._payload


async def _post_blocking(
    client: httpx.AsyncClient, url: str, body: dict[str, Any]
) -> _Attempt:
    response = await client.post(url, json=body)
    status = int(response.status_code)
    if status != 200:
        return _Attempt(status=status, error=error_text(response), response=response)
    return _Attempt(status=status, error=None, response=response)


async def _post_streaming(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    accumulator: StreamAccumulator,
    emit: Callable[[StreamEvent], Awaitable[None]],
    started: float,
    first_seen: list[float],
) -> _Attempt:
    """SSE lesen. Was bis zu einem Abbruch ankam, steht im accumulator."""
    async with client.stream("POST", url, json=body) as response:
        status = int(response.status_code)
        if status != 200:
            await response.aread()
            return _Attempt(status=status, error=error_text(response), response=response)
        async for line in response.aiter_lines():
            events = accumulator.feed_line(line)
            if events and not first_seen:
                first_seen.append(time.monotonic())
            for event in events:
                await emit(event)
            if accumulator.done:
                break
    ttft = int((first_seen[0] - started) * 1000) if first_seen else None
    if accumulator.error is not None:
        return _Attempt(
            status=status, error=accumulator.error, response=_Assembled(None), ttft_ms=ttft
        )
    fallback = accumulator.fallback_payload()
    if fallback is not None:
        # Kein SSE: der Server hat blockierend geantwortet (oder mit einem
        # Fehler-JSON) - beides wertet _read_answer aus wie sonst auch.
        return _Attempt(
            status=status,
            error=None,
            response=_Assembled(fallback, "\n".join(accumulator.other_lines)),
            ttft_ms=ttft,
        )
    if not accumulator.saw_data:
        return _Attempt(
            status=status,
            error=None,
            response=_Assembled(
                {"error": "Der Server lieferte keine SSE-Antwort."},
                "\n".join(accumulator.other_lines),
            ),
            ttft_ms=ttft,
        )
    return _Attempt(
        status=status, error=None, response=_Assembled(accumulator.payload()), ttft_ms=ttft
    )


async def _post_anthropic(
    settings: LLMSettings,
    target: Target,
    params: dict[str, Any],
    accumulator: StreamAccumulator,
    emit: Callable[[StreamEvent], Awaitable[None]],
    started: float,
    first_seen: list[float],
) -> _Attempt:
    """Ein Versuch ueber die Messages-API; Ergebnis in derselben Form wie bei
    LM Studio. Der Schluessel wird in Fehlermeldungen unkenntlich gemacht."""
    api_key = settings.api_key

    def mask(message: str) -> str:
        return api_key.mask(message) if api_key is not None else message

    async with anthropic_api.make_client(
        settings, target.base_url, settings.request_timeout_seconds
    ) as client:
        outcome = await anthropic_api.attempt(
            client, params, accumulator, emit, started, first_seen, mask
        )
    if outcome.error is not None:
        return _Attempt(
            status=outcome.status,
            error=outcome.error,
            response=_Assembled(None, outcome.error),
            ttft_ms=outcome.ttft_ms,
        )
    return _Attempt(
        status=outcome.status,
        error=None,
        response=_Assembled(outcome.payload),
        ttft_ms=outcome.ttft_ms,
    )


async def chat(
    request: ChatRequest,
    *,
    target: Target,
    settings: LLMSettings,
    scope: CallScope | None = None,
    hooks: ChatHooks | None = None,
    client: httpx.AsyncClient | None = None,
    measurement: Measurement | None = None,
) -> ChatResult:
    """Ein Modellaufruf nach den Regeln des Kerns (siehe Moduldoc).

    target       aus discovery.resolve_target() (Server, Modell, Fenster)
    settings     aus settings.load_settings() bzw. ai_settings_service.resolve()
    scope        Protokoll und Messung; None nur fuer den Verbindungstest
                 (keine Zeile, keine Messung, Startwerte)
    hooks        Abbruch und Verdichtung (optional)
    client       bestehender httpx-Client (optional, z. B. fuer Tests)
    measurement  vorab geladene Messung (optional; sonst laedt der Gateway sie)"""
    resolved_hooks = hooks or ChatHooks()
    if client is None:
        timeout = httpx.Timeout(settings.request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as own_client:
            return await _chat(
                request, target, settings, scope, resolved_hooks, own_client, measurement
            )
    return await _chat(request, target, settings, scope, resolved_hooks, client, measurement)


async def chat_structured(
    request: ChatRequest,
    *,
    target: Target,
    settings: LLMSettings,
    scope: CallScope | None = None,
    hooks: ChatHooks | None = None,
    client: httpx.AsyncClient | None = None,
    measurement: Measurement | None = None,
) -> ChatResult:
    """chat() mit dem bewussten Capability-Rueckfall fuer strukturierte Ausgabe.

    Lehnt der Server response_format ab (model_schema_unsupported - NICHT
    bei einem Fensterueberlauf, der einen eigenen Grund hat), folgt GENAU EIN
    zweiter Aufruf mit denselben Nachrichten ohne response_format; das
    Ergebnis traegt dann schema_fallback=True und ein Info-Ereignis. Beide
    Aufrufe stehen in llm_calls. Der Aufrufer prueft die Antwort danach selbst
    gegen seinen Vertrag (auf dem Prompt+Parse-Weg erzwingt kein Server das
    Schema) und vermerkt die Faehigkeit, wenn er sie speichert."""
    first = await chat(
        request,
        target=target,
        settings=settings,
        scope=scope,
        hooks=hooks,
        client=client,
        measurement=measurement,
    )
    if request.response_format is None or first.reason_code != reasons.MODEL_SCHEMA_UNSUPPORTED:
        return first
    second = await chat(
        ChatRequest(
            messages=request.messages,
            tools=request.tools,
            tool_choice=request.tool_choice,
            reasoning_override=request.reasoning_override,
            basis=request.basis,
        ),
        target=target,
        settings=settings,
        scope=scope,
        hooks=hooks,
        client=client,
        measurement=measurement,
    )
    second.schema_fallback = True
    second.events = [
        ChatEvent(
            code=reasons.MODEL_SCHEMA_UNSUPPORTED,
            level="info",
            message=(
                "Der Server lehnt die strukturierte Ausgabe ab - ohne response_format "
                f"erneut gefragt ({first.message})."
            ),
            details={"rejected_call_id": str(first.call_id) if first.call_id else None},
        ),
        *second.events,
    ]
    return second


async def _chat(
    request: ChatRequest,
    target: Target,
    settings: LLMSettings,
    scope: CallScope | None,
    hooks: ChatHooks,
    client: httpx.AsyncClient,
    preloaded: Measurement | None,
) -> ChatResult:
    started = time.monotonic()
    # Beginn des laufenden HTTP-Versuchs: die Protokollzeile traegt die Dauer
    # GENAU dieses Versuchs, das Ergebnis die Dauer des ganzen Aufrufs.
    attempt_started = started
    window = target.window
    events: list[ChatEvent] = []
    server_target = server_target_of(target.base_url)
    url = f"{target.base_url.rstrip('/')}/chat/completions"

    async def finish(
        result: ChatResult, *, max_tokens_sent: int | None = None
    ) -> ChatResult:
        now = time.monotonic()
        result.duration_ms = int((now - started) * 1000)
        result.events = events
        if scope is not None and scope.log:
            call_id = uuid4()
            await write_call(
                scope,
                call_id=call_id,
                model=target.model,
                status=status_for(result.reason_code, result.http_status),
                duration_ms=int((now - attempt_started) * 1000),
                reason_code=result.reason_code,
                error_message=result.message,
                budget=result.budget,
                window=window,
                usage=result.usage,
                finish_reason=result.finish_reason,
                http_status=result.http_status,
                server_target=server_target,
                reasoning_sent=result.reasoning_sent,
                max_tokens_sent=max_tokens_sent,
            )
            result.call_id = call_id
        return result

    def refused(code: str, detail: str | None, budget: Budget | None = None) -> ChatResult:
        return ChatResult(
            reason_code=code,
            message=reasons.describe(code, detail),
            model=target.model,
            window=window,
            budget=budget,
            messages_sent=list(request.messages),
        )

    cloud = settings.provider == providers.ANTHROPIC
    if not settings.is_enabled:
        return await finish(refused(reasons.AI_DISABLED, None))
    if cloud and settings.api_key is None:
        return await finish(refused(reasons.MODEL_KEY_MISSING, settings.key_problem))
    if not window.known:
        return await finish(refused(reasons.MODEL_CONTEXT_UNKNOWN, window.reason))

    measurement = preloaded or await measurement_for(target, settings, scope)
    messages: list[Message] = list(request.messages)
    basis = request.basis
    compacted = False
    overflow_corrected = False
    choice = anthropic_api.ReasoningChoice()
    reasoning_sent: str | None
    if cloud:
        choice = anthropic_api.reasoning_choice(
            settings,
            request.reasoning_override,
            target.selected.capabilities if target.selected is not None else [],
        )
        reasoning_sent = choice.label
        if choice.skipped:
            events.append(
                ChatEvent(
                    code=reasons.MODEL_REASONING_UNSUPPORTED, level="info", message=choice.skipped
                )
            )
    else:
        reasoning_sent, reasoning_event = reasoning_plan(
            settings, request.reasoning_override, window.reasoning_capability
        )
        if reasoning_event is not None:
            events.append(reasoning_event)
    request_notes_reported = False

    streaming = hooks.on_stream is not None
    with_stream_options = streaming and not cloud and server_target not in _NO_STREAM_OPTIONS
    stream_options_dropped = False
    callback_failed = False

    async def emit(event: StreamEvent) -> None:
        """Live-Stueck an den Aufrufer. Scheitert dessen Anzeige, laeuft der
        Aufruf weiter - einmal als Warnung vermerkt."""
        nonlocal callback_failed
        if hooks.on_stream is None:
            return
        try:
            await hooks.on_stream(event)
        except Exception as exc:  # noqa: BLE001 - die Anzeige darf den Aufruf nicht kippen
            if not callback_failed:
                callback_failed = True
                events.append(
                    ChatEvent(
                        code="live_callback_failed",
                        level="warnung",
                        message=(
                            "Die Live-Anzeige meldete einen Fehler "
                            f"({type(exc).__name__}: {_short(str(exc))}) - der Aufruf lief weiter."
                        ),
                    )
                )

    while True:
        try:
            budget = plan_budget(
                messages,
                window=window,
                settings=settings,
                measurement=measurement,
                tools=request.tools,
                basis=basis,
            )
        except BudgetInputError as exc:
            return await finish(refused(reasons.MODEL_SETTINGS_INVALID, f"{exc} ({exc.field})"))

        if not budget.fits and hooks.compact is not None and not compacted:
            smaller = await hooks.compact(messages, budget)
            compacted = True
            if smaller is not None:
                messages = smaller
                basis = None
                continue
        if not budget.fits:
            return await finish(
                refused(reasons.MODEL_INPUT_TOO_LARGE, budget.describe(), budget)
            )

        body: dict[str, Any] = {}
        params: dict[str, Any] = {}
        if cloud:
            built = anthropic_api.build_request(
                model=target.model,
                messages=messages,
                settings=settings,
                max_tokens=budget.max_tokens,
                response_format=request.response_format,
                tools=request.tools,
                tool_choice=request.tool_choice,
                reasoning=choice,
                reasoning_override=request.reasoning_override,
            )
            params = built.params
            if built.notes and not request_notes_reported:
                request_notes_reported = True
                events.extend(
                    ChatEvent(code="anthropic_request_adjusted", level="info", message=note)
                    for note in built.notes
                )
        else:
            body = build_body(
                model=target.model,
                messages=messages,
                settings=settings,
                max_tokens=budget.max_tokens,
                response_format=request.response_format,
                tools=request.tools,
                tool_choice=request.tool_choice,
                reasoning_effort=reasoning_sent,
                reasoning_override=request.reasoning_override,
            )
        base = ChatResult(
            reason_code=None,
            message=None,
            model=target.model,
            window=window,
            budget=budget,
            measurement=measurement,
            reasoning_sent=reasoning_sent,
            messages_sent=list(messages),
        )
        if streaming and not cloud:
            body["stream"] = True
            if with_stream_options:
                body["stream_options"] = {"include_usage": True}
        # Anthropic wird immer gestreamt (lange Antworten laufen sonst in das
        # Zeitbudget einer einzelnen HTTP-Antwort).
        base.streamed = streaming or cloud
        attempt_started = time.monotonic()
        accumulator = StreamAccumulator()
        first_seen: list[float] = []
        work: Awaitable[_Attempt]
        if cloud:
            work = _post_anthropic(
                settings, target, params, accumulator, emit, attempt_started, first_seen
            )
        elif streaming:
            work = _post_streaming(
                client, url, body, accumulator, emit, attempt_started, first_seen
            )
        else:
            work = _post_blocking(client, url, body)
        try:
            attempt = await _race(work, hooks.cancel, hooks.deadline)
        except (
            _Cancelled,
            _DeadlineReached,
            httpx.TimeoutException,
            *anthropic_api.TIMEOUT_ERRORS,
        ) as exc:
            # Was bis hierher kam, bleibt sichtbar (Teilantwort eines Stroms).
            base.content = accumulator.content
            base.reasoning_text = accumulator.reasoning
            if first_seen:
                base.ttft_ms = int((first_seen[0] - attempt_started) * 1000)
            if isinstance(exc, _Cancelled):
                base.reason_code = reasons.MODEL_CANCELLED
                base.message = reasons.describe(reasons.MODEL_CANCELLED)
            elif isinstance(exc, _DeadlineReached):
                base.reason_code = reasons.MODEL_TIMEOUT
                base.deadline_reached = True
                base.message = reasons.describe(
                    reasons.MODEL_TIMEOUT, "Die Laufzeit des KI-Laufs ist erschöpft."
                )
            else:
                base.reason_code = reasons.MODEL_TIMEOUT
                base.message = reasons.describe(
                    reasons.MODEL_TIMEOUT,
                    f"Zeitbudget {settings.request_timeout_seconds:g} s ({type(exc).__name__}).",
                )
            return await finish(base, max_tokens_sent=budget.max_tokens)
        except anthropic_api.UnusableStream as exc:
            # Strom kam an, liess sich aber nicht zusammensetzen - was bis
            # dahin kam, bleibt sichtbar.
            base.content = accumulator.content
            base.reasoning_text = accumulator.reasoning
            base.http_status = 200
            base.reason_code = reasons.MODEL_OUTPUT_UNUSABLE
            base.message = reasons.describe(reasons.MODEL_OUTPUT_UNUSABLE, _short(str(exc)))
            return await finish(base, max_tokens_sent=budget.max_tokens)
        except (httpx.HTTPError, OSError, *anthropic_api.CONNECTION_ERRORS) as exc:
            base.reason_code = reasons.MODEL_UNAVAILABLE
            base.message = reasons.describe(
                reasons.MODEL_UNAVAILABLE, _short(f"{type(exc).__name__}: {exc}")
            )
            return await finish(base, max_tokens_sent=budget.max_tokens)

        status = attempt.status
        base.http_status = status
        base.ttft_ms = attempt.ttft_ms
        response = attempt.response
        if (
            streaming
            and with_stream_options
            and not stream_options_dropped
            and status == 400
            and "stream_options" in (attempt.error or "")
        ):
            # Der Endpunkt kennt stream_options nicht und lehnt die ganze
            # Anfrage ab: genau einmal ohne. Die Tokenzahlen fehlen dann -
            # das Ereignis sagt warum, statt einer stillen Null.
            stream_options_dropped = True
            with_stream_options = False
            _NO_STREAM_OPTIONS.add(server_target)
            base.reason_code = reasons.MODEL_USAGE_UNAVAILABLE
            base.message = reasons.describe(
                reasons.MODEL_USAGE_UNAVAILABLE,
                f"Der Endpunkt lehnt stream_options ab (HTTP 400: {_short(attempt.error or '')}).",
            )
            await finish(base, max_tokens_sent=budget.max_tokens)
            events.append(
                ChatEvent(
                    code=reasons.MODEL_USAGE_UNAVAILABLE,
                    level="info",
                    message=(
                        "Der Endpunkt kennt stream_options nicht - ohne Tokenzahlen gestreamt; "
                        "die Messung von Zeichen je Token bleibt für diesen Aufruf aus."
                    ),
                )
            )
            continue
        if attempt.error is not None:
            text = _short(attempt.error)
            provider_code = anthropic_api.error_code(status, text) if cloud else None
            if provider_code is not None:
                base.reason_code = provider_code
                base.message = reasons.describe(provider_code, f"HTTP {status}: {text}")
                return await finish(base, max_tokens_sent=budget.max_tokens)
            if is_overflow(text):
                real = overflow_input_tokens(text)
                base.reason_code = reasons.MODEL_CONTEXT_OVERFLOW
                base.message = reasons.describe(
                    reasons.MODEL_CONTEXT_OVERFLOW, f"Server: {text} ({budget.describe()})"
                )
                # Erster Ueberlauf: die Schaetzung lag zu niedrig. Nennt der
                # Server die echte Eingabe, wird damit gemessen und EINMAL neu
                # gerechnet; sonst hilft nur eine Verdichtung. Aendert sich
                # nichts, wird nicht ein zweites Mal dasselbe gesendet.
                retry_messages: list[Message] | None = None
                if not overflow_corrected:
                    if real is not None:
                        retry_messages = messages
                    elif hooks.compact is not None and not compacted:
                        compacted = True
                        retry_messages = await hooks.compact(messages, budget)
                if retry_messages is None:
                    return await finish(base, max_tokens_sent=budget.max_tokens)
                overflow_corrected = True
                await finish(base, max_tokens_sent=budget.max_tokens)
                if real is not None:
                    if scope is not None and scope.measure:
                        await record_measurement(
                            scope.session,
                            scope.household_id,
                            target.base_url,
                            target.model,
                            chars=budget.chars,
                            prompt_tokens=real,
                        )
                    measurement = Measurement(
                        chars_per_token=budget.chars / real,
                        estimate_error=measurement.estimate_error,
                        is_start_value=False,
                        error_is_start_value=measurement.error_is_start_value,
                        calls=measurement.calls,
                        largest_need_tokens=measurement.largest_need_tokens,
                        sums=measurement.sums,
                    )
                events.append(
                    ChatEvent(
                        code=reasons.MODEL_CONTEXT_OVERFLOW,
                        level="warnung",
                        message=(
                            f"Das Fenster lief über ({text})"
                            + (
                                " - die Messung wurde mit der echten Eingabe "
                                f"({number_de(real)} Token) korrigiert"
                                if real is not None
                                else " - verdichtet"
                            )
                            + "; neu gerechnet."
                        ),
                        details={"input_tokens": real, "budget": budget.as_run_budget()},
                    )
                )
                # Hat der Server die Eingabe genau dieser Liste genannt, ist sie
                # beim zweiten Mal exakt bekannt (kein Schaetzabstand mehr).
                basis = (
                    Basis(
                        messages=tuple(messages),
                        tools_signature=tools_signature(request.tools),
                        prompt_tokens=real,
                    )
                    if real is not None and retry_messages is messages
                    else None
                )
                messages = retry_messages
                continue
            rejected = _REASONING_REJECTED_CLOUD if cloud else _REASONING_REJECTED
            if reasoning_sent and rejected.search(text):
                base.reason_code = reasons.MODEL_REASONING_UNSUPPORTED
                sent = reasoning_sent if cloud else f"reasoning_effort=„{reasoning_sent}“"
                base.message = reasons.describe(
                    reasons.MODEL_REASONING_UNSUPPORTED,
                    f"Gesendet: {sent}. Server: {text}",
                )
                return await finish(base, max_tokens_sent=budget.max_tokens)
            structured = request.response_format is not None or bool(request.tools)
            # Bei Anthropic nur, wenn der Fehler das Schema bzw. die Werkzeuge
            # nennt: ein anderer 400 (Datenaufbewahrung, Rollenfolge) waere
            # sonst als fehlende Faehigkeit gespeichert worden.
            if cloud and not anthropic_api.mentions_schema(text):
                structured = False
            if structured and status in CAPABILITY_REJECT_STATUS:
                base.reason_code = reasons.MODEL_SCHEMA_UNSUPPORTED
                base.message = reasons.describe(
                    reasons.MODEL_SCHEMA_UNSUPPORTED, f"HTTP {status}: {text}".strip()
                )
                return await finish(base, max_tokens_sent=budget.max_tokens)
            base.reason_code = reasons.MODEL_HTTP_ERROR
            base.message = reasons.describe(
                reasons.MODEL_HTTP_ERROR, f"HTTP {status}: {text}".strip()
            )
            return await finish(base, max_tokens_sent=budget.max_tokens)

        return await finish(
            await _read_answer(base, response, budget, messages, request, target, scope, events),
            max_tokens_sent=budget.max_tokens,
        )


async def _read_answer(
    base: ChatResult,
    response: Any,
    budget: Budget,
    messages: list[Message],
    request: ChatRequest,
    target: Target,
    scope: CallScope | None,
    events: list[ChatEvent],
) -> ChatResult:
    """HTTP-200-Antwort auswerten: Inhalt, Denktext, Werkzeugaufrufe, usage,
    Messung - und benennen, wenn nichts Brauchbares darin steht."""
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - kaputtes JSON ist ein Befund
        base.reason_code = reasons.MODEL_OUTPUT_UNUSABLE
        base.message = reasons.describe(
            reasons.MODEL_OUTPUT_UNUSABLE, f"Keine JSON-Antwort ({type(exc).__name__})."
        )
        return base
    if not isinstance(payload, dict):
        base.reason_code = reasons.MODEL_OUTPUT_UNUSABLE
        base.message = reasons.describe(reasons.MODEL_OUTPUT_UNUSABLE, "Antwort ist kein Objekt.")
        return base
    base.payload = payload
    base.usage = extract_usage_tokens(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        # LM Studio beantwortet falsche Pfade mit HTTP 200 + {"error": ...}.
        detail = error_text(response) or "Antwort ohne choices."
        base.reason_code = (
            reasons.MODEL_HTTP_ERROR if "error" in payload else reasons.MODEL_OUTPUT_UNUSABLE
        )
        base.message = reasons.describe(base.reason_code, _short(detail))
        return base
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    base.finish_reason = str(finish_reason) if finish_reason else None
    message = choice.get("message")
    if not isinstance(message, dict):
        base.reason_code = reasons.MODEL_OUTPUT_UNUSABLE
        base.message = reasons.describe(
            reasons.MODEL_OUTPUT_UNUSABLE, "Die Antwort enthält keine Nachricht (message fehlt)."
        )
        return base
    base.content = _content_text(message.get("content"))
    reasoning_raw = message.get("reasoning_content") or message.get("reasoning")
    base.reasoning_text = _content_text(reasoning_raw)
    base.tool_calls = _tool_calls_of(message)
    if base.finish_reason == "refusal":
        # Anthropic: der Sicherheitsfilter hat die Antwort verweigert. Was bis
        # dahin geschrieben war, ist keine Antwort - auch wenn es so aussieht.
        details = payload.get("stop_details")
        category = details.get("category") if isinstance(details, dict) else None
        base.content = ""
        base.reasoning_text = ""
        base.tool_calls = []
        base.reason_code = reasons.MODEL_REFUSED
        base.message = reasons.describe(
            reasons.MODEL_REFUSED, f"Kategorie: {category}." if category else None
        )
        return base

    usage = base.usage
    if usage.prompt_tokens is not None and usage.prompt_tokens > 0:
        base.basis = Basis(
            messages=tuple(messages),
            tools_signature=tools_signature(request.tools),
            prompt_tokens=usage.prompt_tokens,
        )
        if scope is not None and scope.measure:
            await record_measurement(
                scope.session,
                scope.household_id,
                target.base_url,
                target.model,
                chars=budget.chars,
                prompt_tokens=usage.prompt_tokens,
                need_tokens=usage.completion_tokens,
            )
    elif not any(event.code == reasons.MODEL_USAGE_UNAVAILABLE for event in events):
        events.append(
            ChatEvent(
                code=reasons.MODEL_USAGE_UNAVAILABLE,
                level="info",
                message=reasons.describe(reasons.MODEL_USAGE_UNAVAILABLE),
            )
        )
    if usage.completion_tokens is not None and usage.completion_tokens > budget.R:
        events.append(
            ChatEvent(
                code=reasons.MODEL_RESERVE_TIGHT,
                level="info",
                message=reasons.describe(
                    reasons.MODEL_RESERVE_TIGHT,
                    f"Die Antwort brauchte {number_de(usage.completion_tokens)} Token, die "
                    f"Antwortreserve beträgt {number_de(budget.R)}.",
                ),
                details={"completion_tokens": usage.completion_tokens, "reserve": budget.R},
            )
        )

    if not base.content.strip() and not base.tool_calls:
        if base.truncated:
            base.reason_code = reasons.MODEL_BUDGET_EXHAUSTED
            base.message = reasons.describe(
                reasons.MODEL_BUDGET_EXHAUSTED,
                f"Antwortbudget {number_de(budget.max_tokens)} Token (Fenster "
                f"{number_de(budget.W)}, Eingabe {number_de(budget.E)}); "
                f"{number_de(len(base.reasoning_text))} Zeichen Denktext, kein Antworttext.",
            )
        else:
            base.reason_code = reasons.MODEL_OUTPUT_UNUSABLE
            base.message = reasons.describe(
                reasons.MODEL_OUTPUT_UNUSABLE,
                f"Leere Antwort (finish_reason={base.finish_reason or 'unbekannt'}).",
            )
    return base
