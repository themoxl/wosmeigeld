"""Die Agentenschleife des KI-Felds (Port von sia_public/src/core/ai.js, lauf()).

Ein Lauf endet durch
  answer   das Modell antwortet ohne Werkzeugaufruf,
  stall    zwei Runden hintereinander nur Bekanntes (gleiche Argumente UND
           gleiches Ergebnis; nach der ersten solchen Runde ein Hinweis an
           das Modell),
  runtime  die Laufzeit (Einstellung run_time_limit_minutes) ist erschoepft,
  failed   die Erkundung brach in einer spaeteren Runde mit einem
           Serverfehler ab,
oder durch Abbrechen. Eine Rundenzahl gibt es nicht.

Jedes Ende ausser Antwort und Abbruch bekommt die Schlussnachricht "Genug
erkundet: Es gibt jetzt keine Werkzeuge mehr. ..." und GENAU EINEN Aufruf ohne
Werkzeuge - ohne diesen Satz rief Qwen in sia weiter Werkzeuge als Text auf.

Weiter aus lauf():
* Pseudo-Werkzeugaufrufe im Text werden ausgefuehrt, XML-Parameter nach dem
  Schema in ihren Typ zurueckgewandelt (app/assistant/text.py).
* <think> im Inhalt wird entfernt - in der Antwort und live (dort als think).
* Systemnachrichten zu einer an Position 0.
* Kontextzeile "[Kontext: E von W Token belegt, Antwortreserve R]" an das
  letzte Werkzeugergebnis einer Runde (Einstellung context_line_enabled);
  am Ende wieder entfernt.
* Netzwerkfehler: wiederholt mit verdoppelter Wartezeit, solange Laufzeit
  bleibt. Fehlerantworten des Servers nie.
* Hat das Denken das Antwortbudget verbraucht (model_budget_exhausted) oder
  wurde die Antwort an der Grenze abgeschnitten: EINMAL verdichten, wenn das
  Platz schafft (E1) - sonst laut scheitern. Nie "ohne Denken wiederholen".

Die Schleife speichert nichts selbst: alles geht an einen LoopRecorder
(app/assistant/runner.py speichert und veroeffentlicht). Die Werkzeuge
laufen ueber einen ToolRunner. Das Modell erreicht sie nur ueber den Gateway
des Kerns (app.llm.chat) - mit Streaming, Abbruch und Laufzeitfrist.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

import httpx

from app import llm
from app.assistant import prompts
from app.assistant.compaction import Compactor, InputTooLarge, Prepared, SummaryFailed
from app.assistant.text import (
    PseudoCall,
    clean_answer,
    coerce_arguments,
    parse_pseudo_calls,
    stable,
    strip_pseudo,
    strip_think,
    system_first,
)
from app.llm import reasons
from app.llm.budget import number_de

Message = dict[str, Any]
T = TypeVar("T")

# fest: erste Wartezeit nach einem Netzwerkfehler; sie verdoppelt sich, solange Laufzeit bleibt
NETWORK_FIRST_WAIT_SECONDS = 2.0

# Gruende, nach denen EINE Verdichtung den Aufruf retten kann.
_COMPACT_AND_RETRY = frozenset(
    {
        reasons.MODEL_CONTEXT_OVERFLOW,
        reasons.MODEL_INPUT_TOO_LARGE,
        reasons.MODEL_BUDGET_EXHAUSTED,
        reasons.MODEL_RESERVE_TIGHT,
    }
)


# ---------------------------------------------------------------- Vertraege


@dataclass
class CallRecord:
    """Ein Modellaufruf, wie er im Laufprotokoll steht (RunCall)."""

    nr: int
    step: str
    started_at: datetime
    duration_ms: int | None = None
    ttft_ms: int | None = None
    finish_reason: str | None = None
    model: str | None = None
    budget: dict[str, Any] | None = None
    tools_offered: list[str] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    reasoning_chars: int = 0
    tokens_per_s: float | None = None
    answer_text: str | None = None
    tool_call_names: list[str] = field(default_factory=list)
    error_code: str | None = None
    error: str | None = None
    llm_call_id: str | None = None
    retry_of: int | None = None
    retry_reason: str | None = None


@dataclass
class ToolOutcome:
    """Ergebnis eines Werkzeugs fuer das Modell (text) und fuer den Speicher
    (alle Zeilen zum Weiterblaettern)."""

    status: str
    text: str
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    row_count: int | None = None


@dataclass
class ToolRecord:
    key: str
    key_nr: int
    call_nr: int
    tool_call_id: str
    name: str
    args: dict[str, Any]
    status: str
    result_text: str
    columns: list[str] | None
    rows: list[list[Any]] | None
    row_count: int | None
    duration_ms: int


class LoopRecorder(Protocol):
    async def call_started(self, record: CallRecord) -> None: ...

    async def call_finished(self, record: CallRecord) -> None: ...

    async def round_message(self, call_nr: int, message: Message) -> None: ...

    async def tool_started(self, key: str, name: str, args: dict[str, Any]) -> None: ...

    async def tool_finished(self, record: ToolRecord) -> None: ...

    async def event(
        self,
        kind: str,
        level: str,
        message: str,
        details: dict[str, Any] | None = None,
        call_nr: int | None = None,
    ) -> None: ...

    async def meter(self, meter: dict[str, Any]) -> None: ...

    async def live(self, payload: dict[str, Any]) -> None: ...


class ToolRunner(Protocol):
    def definitions(self) -> list[dict[str, Any]]: ...

    def parameters_of(self, name: str) -> dict[str, Any] | None: ...

    async def run(
        self, key: str, name: str, args: dict[str, Any], *, remaining_ms: int
    ) -> ToolOutcome: ...


class LoopCancelled(Exception):
    """Der Nutzer hat abgebrochen; `content` ist die Teilantwort."""

    def __init__(self, content: str = "") -> None:
        super().__init__("abgebrochen")
        self.content = content


class LoopFailed(Exception):
    """Der Lauf scheitert mit einem benannten Grund."""

    def __init__(self, code: str, message: str, content: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.content = content


@dataclass
class LoopOutcome:
    content: str
    end_reason: str


class KeyAllocator:
    """Werkzeugkennungen W1, W2, ... je Chat (Startwert = letzte vergebene)."""

    def __init__(self, last: int) -> None:
        self.last = last

    def reserve(self) -> tuple[str, int]:
        self.last += 1
        return f"W{self.last}", self.last


# ---------------------------------------------------------------- Live


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_PSEUDO_TAGS = ("<tool_call>", "<function=")


def _partial_suffix(text: str, tags: tuple[str, ...]) -> int:
    """Laenge des laengsten Endes von `text`, das ein Tag beginnen koennte."""
    best = 0
    for tag in tags:
        for size in reversed(range(1, len(tag))):
            if size <= best:
                break
            if text.endswith(tag[:size]):
                best = size
                break
    return best


class LiveAdapter:
    """Uebersetzt die StreamEvents eines Aufrufs in fluechtige Stromzeilen
    (think/delta/targs). <think> im Inhalt geht als think, ein beginnender
    Pseudo-Werkzeugaufruf beendet die Anzeige des Antworttexts. Werkzeugkennungen
    werden schon reserviert, wenn der Aufruf entsteht (targs vor tool)."""

    def __init__(self, live: Callable[[dict[str, Any]], Awaitable[None]], keys: KeyAllocator):
        self._live = live
        self._keys = keys
        self.pending: dict[int, tuple[str, int]] = {}
        self.reasoning_chars = 0
        self.inline_think_chars = 0
        self._buffer = ""
        self._in_think = False
        self._suppressed = False

    def key_for(self, index: int) -> tuple[str, int]:
        if index not in self.pending:
            self.pending[index] = self._keys.reserve()
        return self.pending[index]

    @property
    def think_chars(self) -> int:
        return self.reasoning_chars + self.inline_think_chars

    async def on_stream(self, event: llm.StreamEvent) -> None:
        if event.kind == "think":
            self.reasoning_chars = event.chars
            await self._live({"type": "think", "chars": self.think_chars, "text": event.text})
        elif event.kind == "delta":
            await self._content(event.text)
        elif event.kind in ("tool", "targs"):
            key, _nr = self.key_for(event.index if event.index is not None else len(self.pending))
            if event.kind == "targs" and event.text:
                await self._live({"type": "targs", "key": key, "text": event.text})

    async def _content(self, piece: str) -> None:
        self._buffer += piece
        answer = ""
        thought = ""
        while self._buffer and not self._suppressed:
            if self._in_think:
                index = self._buffer.find(_THINK_CLOSE)
                if index >= 0:
                    thought += self._buffer[:index]
                    self._buffer = self._buffer[index + len(_THINK_CLOSE) :]
                    self._in_think = False
                    continue
                keep = _partial_suffix(self._buffer, (_THINK_CLOSE,))
                thought += self._buffer[: len(self._buffer) - keep]
                self._buffer = self._buffer[len(self._buffer) - keep :]
                break
            found = [
                (index, tag)
                for tag in (_THINK_OPEN, *_PSEUDO_TAGS)
                if (index := self._buffer.find(tag)) >= 0
            ]
            if found:
                index, tag = min(found)
                answer += self._buffer[:index]
                self._buffer = self._buffer[index + len(tag) :]
                if tag == _THINK_OPEN:
                    self._in_think = True
                    continue
                self._suppressed = True
                self._buffer = ""
                break
            keep = _partial_suffix(self._buffer, (_THINK_OPEN, *_PSEUDO_TAGS))
            answer += self._buffer[: len(self._buffer) - keep]
            self._buffer = self._buffer[len(self._buffer) - keep :]
            break
        if self._suppressed:
            self._buffer = ""
        if thought:
            self.inline_think_chars += len(thought)
            await self._live({"type": "think", "chars": self.think_chars, "text": thought})
        if answer:
            await self._live({"type": "delta", "text": answer})


# ---------------------------------------------------------------- Schleife


async def _until_cancelled(work: Awaitable[T], cancel: llm.CancelToken) -> T:
    """Wartet auf `work`; ein Abbruch beendet es sofort (LoopCancelled)."""
    task = asyncio.ensure_future(work)
    stop = asyncio.ensure_future(cancel.wait())
    try:
        done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for item in (task, stop):
            if not item.done():
                item.cancel()
        await asyncio.gather(task, stop, return_exceptions=True)
    if task in done and not task.cancelled():
        return task.result()
    raise LoopCancelled()


class AgentLoop:
    """Ein Lauf ueber einem vorbereiteten Verlauf.

    transcript  Systemnachricht, Verlauf frueherer Fragen, aktuelle Frage
    question    die aktuelle Frage (Objekt aus transcript)
    deadline    time.monotonic()-Zeitpunkt, an dem die Laufzeit endet"""

    def __init__(
        self,
        *,
        run_id: str,
        target: llm.Target,
        settings: llm.LLMSettings,
        scope: llm.CallScope | None,
        recorder: LoopRecorder,
        tools: ToolRunner,
        cancel: llm.CancelToken,
        transcript: list[Message],
        question: Message | None,
        keys: KeyAllocator,
        deadline: float,
        runtime_seconds: float,
        reasoning_override: str | None = None,
        client: httpx.AsyncClient | None = None,
        measurement: llm.Measurement | None = None,
        stream: bool = True,
    ) -> None:
        self.run_id = run_id
        self.target = target
        self.settings = settings
        self.scope = scope
        self.recorder = recorder
        self.tools = tools
        self.cancel = cancel
        self.transcript = transcript
        self.keys = keys
        self.deadline = deadline
        self.runtime_seconds = runtime_seconds
        self.reasoning_override = reasoning_override
        self.client = client
        self.measurement = measurement
        self.stream = stream
        self.basis: llm.Basis | None = None
        self.call_nr = 0
        self.tool_count = 0
        self.meter: dict[str, Any] | None = None
        self.model_answering: str | None = None
        self.reasoning_sent: str | None = None
        self._context_originals: dict[int, tuple[Message, Any]] = {}
        self.compactor = Compactor(
            transcript=transcript,
            budget_fn=self._budget,
            summarize_call=self._summarize_call,
            event=self._compaction_event,
            chars_per_token=self._chars_per_token,
            question=question,
        )

    # ------------------------------------------------------------ Hilfen

    def _chars_per_token(self) -> float:
        measurement = self.measurement
        if measurement is None or not measurement.chars_per_token:
            raise LoopFailed(
                reasons.MODEL_SETTINGS_INVALID,
                reasons.describe(reasons.MODEL_SETTINGS_INVALID, "Zeichen je Token fehlt."),
            )
        return float(measurement.chars_per_token)

    def _budget(self, messages: list[Message], tools: list[dict[str, Any]] | None) -> llm.Budget:
        if self.measurement is None:
            raise LoopFailed(
                reasons.MODEL_SETTINGS_INVALID,
                reasons.describe(reasons.MODEL_SETTINGS_INVALID, "Messung fehlt."),
            )
        try:
            return llm.plan_budget(
                system_first(messages),
                window=self.target.window,
                settings=self.settings,
                measurement=self.measurement,
                tools=tools,
                basis=self.basis,
            )
        except llm.BudgetInputError as exc:
            raise LoopFailed(
                reasons.MODEL_SETTINGS_INVALID,
                reasons.describe(reasons.MODEL_SETTINGS_INVALID, f"{exc} ({exc.field})"),
            ) from exc

    async def _refresh_measurement(self) -> None:
        self.measurement = await llm.measurement_for(self.target, self.settings, self.scope)

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def _check_cancel(self, content: str = "") -> None:
        if self.cancel.cancelled:
            raise LoopCancelled(content)

    async def _sleep(self, seconds: float) -> None:
        await _until_cancelled(asyncio.sleep(seconds), self.cancel)

    async def _compaction_event(
        self, kind: str, level: str, message: str, details: dict[str, Any] | None
    ) -> None:
        await self.recorder.event(kind, level, message, details, self.call_nr + 1)

    # ------------------------------------------------------------ Aufrufe

    async def _call(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None,
        step: str,
        deadline: float,
        retry_of: int | None = None,
        retry_reason: str | None = None,
        stream: bool | None = None,
        track: bool = True,
    ) -> tuple[CallRecord, llm.ChatResult, LiveAdapter | None]:
        """Ein Modellaufruf ueber den Gateway. `track=False` (Zusammenfassung):
        weder Basis der exakten Zaehlung noch Kontextbalken fortschreiben -
        dieser Aufruf hatte eine ganz andere Eingabe."""
        self._check_cancel()
        self.call_nr += 1
        sent = system_first(messages)
        budget = self._budget(sent, tools)
        record = CallRecord(
            nr=self.call_nr,
            step=step,
            started_at=datetime.now(UTC),
            budget=budget.as_run_budget(),
            tools_offered=[str(tool["function"]["name"]) for tool in tools or []],
            retry_of=retry_of,
            retry_reason=retry_reason,
        )
        await self.recorder.call_started(record)
        streaming = self.stream if stream is None else stream
        adapter = LiveAdapter(self.recorder.live, self.keys) if streaming else None
        result = await llm.chat(
            llm.ChatRequest(
                messages=sent,
                tools=tools,
                reasoning_override=self.reasoning_override,
                basis=self.basis,
            ),
            target=self.target,
            settings=self.settings,
            scope=self.scope,
            hooks=llm.ChatHooks(
                cancel=self.cancel,
                on_stream=adapter.on_stream if adapter is not None else None,
                deadline=deadline,
            ),
            client=self.client,
            measurement=self.measurement,
        )
        usage = result.usage
        payload_model = (result.payload or {}).get("model") if result.payload else None
        inline_think = len(result.content) - len(strip_think(result.content))
        record.duration_ms = result.duration_ms
        record.ttft_ms = result.ttft_ms
        record.finish_reason = result.finish_reason
        record.model = str(payload_model) if payload_model else result.model
        if result.budget is not None:
            record.budget = result.budget.as_run_budget()
        record.prompt_tokens = usage.prompt_tokens
        record.completion_tokens = usage.completion_tokens
        record.reasoning_tokens = usage.reasoning_tokens
        record.reasoning_chars = len(result.reasoning_text) + max(0, inline_think)
        generating_ms = (result.duration_ms or 0) - (result.ttft_ms or 0)
        if usage.completion_tokens and generating_ms > 0:
            record.tokens_per_s = round(usage.completion_tokens / (generating_ms / 1000), 2)
        record.answer_text = clean_answer(result.content) or None
        record.tool_call_names = [call.name for call in result.tool_calls]
        record.error_code = result.reason_code
        record.error = result.message
        record.llm_call_id = str(result.call_id) if result.call_id else None
        await self.recorder.call_finished(record)
        if result.ok:
            self.model_answering = record.model
        if result.reasoning_sent is not None:
            self.reasoning_sent = result.reasoning_sent
        for event in result.events:
            await self.recorder.event(
                event.code, event.level, event.message, dict(event.details) or None, record.nr
            )
        if not track:
            return record, result, adapter
        if result.basis is not None:
            self.basis = result.basis
        if result.budget is not None:
            exact = usage.prompt_tokens is not None and usage.prompt_tokens > 0
            self.meter = {
                "run_id": self.run_id,
                "call_nr": record.nr,
                "input_tokens": usage.prompt_tokens if exact else result.budget.E,
                "window_tokens": result.budget.W,
                "reserve_tokens": result.budget.R,
                "exact": bool(exact or result.budget.exact),
            }
            await self.recorder.meter(self.meter)
        return record, result, adapter

    async def _summarize_call(self, messages: list[Message], step: str) -> str | None:
        """Zusammenfassungsaufruf (Stufe 2): ohne Werkzeuge, ohne Strom."""
        _record, result, _adapter = await self._call(
            messages, tools=None, step=step, deadline=self.deadline, stream=False, track=False
        )
        if result.reason_code == reasons.MODEL_CANCELLED:
            raise LoopCancelled()
        if not result.ok:
            raise SummaryFailed(result.reason_code, result.message or "")
        return result.content

    async def _prepare(
        self, tools: list[dict[str, Any]] | None, *, force: bool = False
    ) -> Prepared:
        await self._refresh_measurement()
        try:
            return await self.compactor.prepare(tools, force=force)
        except InputTooLarge as exc:
            await self.recorder.event(
                "input_too_large",
                "fehler",
                f"{exc.lead}. {exc.budget.describe()}",
                {"budget": exc.budget.as_run_budget()},
                self.call_nr + 1,
            )
            raise LoopFailed(
                reasons.MODEL_INPUT_TOO_LARGE,
                reasons.describe(
                    reasons.MODEL_INPUT_TOO_LARGE, f"{exc.lead}: {exc.budget.describe()}"
                ),
            ) from exc
        except SummaryFailed as exc:
            raise LoopFailed(
                exc.reason_code or reasons.MODEL_OUTPUT_UNUSABLE,
                str(exc) or reasons.describe(reasons.MODEL_OUTPUT_UNUSABLE),
            ) from exc

    async def _call_with_recovery(
        self,
        prepared: Prepared,
        *,
        tools: list[dict[str, Any]] | None,
        step: str,
        deadline: float,
    ) -> tuple[CallRecord, llm.ChatResult, LiveAdapter | None, str | None]:
        """Ein Schritt samt den erlaubten Wiederholungen: Netzwerkfehler (mit
        verdoppelter Wartezeit, solange Laufzeit bleibt) und EINMAL nach
        einer Verdichtung, wenn sie mehr Platz schafft. Liefert zusaetzlich
        den Grund, wenn eine formal gelungene Antwort trotzdem scheitert
        (an der Grenze abgeschnitten -> model_reserve_tight)."""
        retry_of: int | None = None
        retry_reason: str | None = None
        wait = NETWORK_FIRST_WAIT_SECONDS
        compacted = False
        while True:
            record, result, adapter = await self._call(
                prepared.messages,
                tools=tools,
                step=step,
                deadline=deadline,
                retry_of=retry_of,
                retry_reason=retry_reason,
            )
            code = result.reason_code
            if code == reasons.MODEL_CANCELLED:
                raise LoopCancelled(clean_answer(result.content))
            if code == reasons.MODEL_UNAVAILABLE:
                if deadline - time.monotonic() > wait:
                    await self.recorder.event(
                        "network_retry",
                        "warnung",
                        f"Netzwerkfehler ({result.message}) - in {wait:g} s erneut versucht.",
                        {"wait_seconds": wait},
                        record.nr,
                    )
                    await self._sleep(wait)
                    retry_of = record.nr
                    retry_reason = f"Netzwerkfehler, nach {wait:g} s wiederholt"
                    wait *= 2
                    continue
                return record, result, adapter, None
            truncated = result.ok and result.truncated
            effective = reasons.MODEL_RESERVE_TIGHT if truncated else code
            if effective in _COMPACT_AND_RETRY and not compacted:
                compacted = True
                old = prepared.budget
                self.basis = None
                try:
                    smaller = await self._prepare(tools, force=True)
                except LoopFailed:
                    smaller = None
                if smaller is not None and smaller.budget.max_tokens > old.max_tokens:
                    await self.recorder.event(
                        "compacted_retry",
                        "warnung",
                        (
                            "Der Platz im Kontextfenster reichte nicht "
                            f"({effective}) - verdichtet und einmal wiederholt: Antwortbudget "
                            f"{number_de(old.max_tokens)} → "
                            f"{number_de(smaller.budget.max_tokens)} Token."
                        ),
                        {"before": old.as_run_budget(), "after": smaller.budget.as_run_budget()},
                        record.nr,
                    )
                    prepared = smaller
                    retry_of = record.nr
                    retry_reason = "verdichtet, weil der Platz nicht reichte"
                    continue
            if truncated:
                return record, result, adapter, reasons.MODEL_RESERVE_TIGHT
            return record, result, adapter, None

    def _truncated_message(self, result: llm.ChatResult) -> str:
        budget = result.budget
        detail = "Die Antwort wurde an der Grenze des Antwortbudgets abgeschnitten"
        if budget is not None:
            detail += (
                f" (Antwortbudget {number_de(budget.max_tokens)} Token, Fenster "
                f"{number_de(budget.W)}, Eingabe {number_de(budget.E)})"
            )
        if result.usage.completion_tokens is not None:
            detail += f"; geschrieben: {number_de(result.usage.completion_tokens)} Token"
        return reasons.describe(reasons.MODEL_RESERVE_TIGHT, detail + ".")

    # ------------------------------------------------------------ Werkzeuge

    async def _execute_tool(
        self,
        call_id: str,
        name: str,
        raw_arguments: str,
        *,
        xml: bool,
        key: str,
        key_nr: int,
        call_nr: int,
    ) -> tuple[Message, str]:
        self._check_cancel()
        args: dict[str, Any] | None = None
        problem: str | None = None
        try:
            parsed = json.loads(raw_arguments or "{}")
            if isinstance(parsed, dict):
                args = parsed
            else:
                problem = "Die Argumente sind kein JSON-Objekt."
        except ValueError as exc:
            problem = f"Die Argumente sind kein gültiges JSON ({exc})."
        schema = self.tools.parameters_of(name)
        if args is not None and xml:
            args = coerce_arguments(args, schema)
        shown_args = args if args is not None else {"roh": raw_arguments}
        await self.recorder.tool_started(key, name, shown_args)
        started = time.monotonic()
        if problem is not None:
            outcome = ToolOutcome(
                "rejected", f"Fehler: {problem} Sende die Argumente als JSON-Objekt."
            )
        elif schema is None:
            known = ", ".join(
                str(tool["function"]["name"]) for tool in self.tools.definitions()
            )
            outcome = ToolOutcome(
                "rejected", f"Fehler: unbekanntes Werkzeug „{name}“. Verfügbar: {known}."
            )
        else:
            remaining_ms = max(1, int(self.remaining() * 1000))
            try:
                assert args is not None
                outcome = await _until_cancelled(
                    self.tools.run(key, name, args, remaining_ms=remaining_ms), self.cancel
                )
            except LoopCancelled:
                await self.recorder.tool_finished(
                    ToolRecord(
                        key=key,
                        key_nr=key_nr,
                        call_nr=call_nr,
                        tool_call_id=call_id,
                        name=name,
                        args=shown_args,
                        status="cancelled",
                        result_text="Abgebrochen.",
                        columns=None,
                        rows=None,
                        row_count=None,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                )
                raise
            except Exception as exc:  # noqa: BLE001 - jeder Werkzeugfehler geht an das Modell
                outcome = ToolOutcome("error", f"Fehler: {type(exc).__name__}: {exc}")
        self.tool_count += 1
        await self.recorder.tool_finished(
            ToolRecord(
                key=key,
                key_nr=key_nr,
                call_nr=call_nr,
                tool_call_id=call_id,
                name=name,
                args=shown_args,
                status=outcome.status,
                result_text=outcome.text,
                columns=outcome.columns,
                rows=outcome.rows,
                row_count=outcome.row_count,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
        self.compactor.tool_texts[key] = outcome.text
        message: Message = {"role": "tool", "tool_call_id": call_id, "content": outcome.text}
        self.compactor.keys[id(message)] = key
        # Wiederholtes Lesen ist erlaubt, wenn sich der Stand geaendert hat:
        # nur dieselben Argumente MIT demselben Ergebnis sind nichts Neues.
        # Die eigene Kennung steht im Ergebnistext und zaehlt nicht.
        signature = json.dumps(
            [name, stable(shown_args), outcome.text.replace(key, "W")],
            ensure_ascii=False,
            default=str,
        )
        return message, signature

    def _add_context_line(self, tools: list[dict[str, Any]] | None) -> None:
        if not self.settings.context_line_enabled or not self.transcript:
            return
        last = self.transcript[-1]
        if last.get("role") != "tool":
            return
        try:
            budget = self._budget(self.compactor.with_summary(), tools)
        except LoopFailed:
            return
        if id(last) not in self._context_originals:
            self._context_originals[id(last)] = (last, last.get("content"))
        last["content"] = (
            f"{last.get('content') or ''}\n\n"
            f"{prompts.context_line(budget.E, budget.W, budget.R)}"
        )

    def restore_context_lines(self) -> None:
        """Kontextzeilen zuruecknehmen (immer, auch wenn der Lauf scheitert)."""
        for message, original in self._context_originals.values():
            message["content"] = original
        self._context_originals.clear()

    # ------------------------------------------------------------ Ablauf

    async def run(self) -> LoopOutcome:
        try:
            return await self._run()
        finally:
            self.restore_context_lines()

    async def compact(self, focus: str) -> LoopOutcome:
        """Verdichtung auf Knopfdruck (wie /compact): fasst alles vor der
        letzten Frage zusammen und baut dabei auf der bisherigen
        Zusammenfassung auf; gibt es nichts Neues, wird sie mit dem Fokus neu
        gefasst."""
        await self._refresh_measurement()
        try:
            await self.compactor.stage2(None, force=False, focus=focus, deficit=0)
        except InputTooLarge as exc:
            raise LoopFailed(
                reasons.MODEL_INPUT_TOO_LARGE,
                reasons.describe(
                    reasons.MODEL_INPUT_TOO_LARGE, f"{exc.lead}: {exc.budget.describe()}"
                ),
            ) from exc
        except SummaryFailed as exc:
            raise LoopFailed(
                exc.reason_code or reasons.MODEL_OUTPUT_UNUSABLE,
                str(exc) or reasons.describe(reasons.MODEL_OUTPUT_UNUSABLE),
            ) from exc
        summary = self.compactor.chat_summary
        if summary is None:
            raise LoopFailed(
                "nothing_to_compact",
                "Nichts zu verdichten: vor der letzten Frage steht noch kein beantworteter "
                "Verlauf.",
            )
        return LoopOutcome(content=summary.text, end_reason="answer")

    async def _run(self) -> LoopOutcome:
        tools = self.tools.definitions()
        seen: set[str] = set()
        previous_without_news = False
        rounds = 0
        end: str | None = None
        while True:
            self._check_cancel()
            if self.remaining() <= 0:
                end = "runtime"
                break
            rounds += 1
            step = "Antwort" if rounds == 1 else f"Runde {rounds}"
            prepared = await self._prepare(tools)
            record, result, adapter, failure = await self._call_with_recovery(
                prepared, tools=tools, step=step, deadline=self.deadline
            )
            if failure == reasons.MODEL_RESERVE_TIGHT:
                raise LoopFailed(
                    failure, self._truncated_message(result), clean_answer(result.content)
                )
            if not result.ok:
                if result.deadline_reached:
                    end = "runtime"
                    break
                if result.reason_code == reasons.MODEL_HTTP_ERROR and rounds > 1:
                    await self.recorder.event(
                        "exploration_aborted",
                        "warnung",
                        f"Erkundung in Runde {rounds} abgebrochen ({result.message}) - die "
                        "Antwort stützt sich auf das bis dahin Erkundete.",
                        None,
                        record.nr,
                    )
                    end = "failed"
                    break
                raise LoopFailed(
                    result.reason_code or reasons.MODEL_OUTPUT_UNUSABLE,
                    result.message or reasons.describe(reasons.MODEL_OUTPUT_UNUSABLE),
                    clean_answer(result.content),
                )
            content = result.content
            calls: list[tuple[str, str, str, bool]] = [
                (call.id, call.name, call.arguments, False) for call in result.tool_calls
            ]
            if not calls:
                pseudo: list[PseudoCall] = parse_pseudo_calls(content)
                if pseudo:
                    calls = [(item.id, item.name, item.arguments, item.xml) for item in pseudo]
                    content = strip_pseudo(content)
                    await self.recorder.event(
                        "pseudo_tool",
                        "info",
                        "Werkzeugaufruf stand als Text in der Antwort und wurde als Aufruf "
                        "ausgeführt.",
                        {"names": [item.name for item in pseudo]},
                        record.nr,
                    )
            if not calls:
                answer = clean_answer(content)
                if not answer:
                    raise LoopFailed(
                        reasons.MODEL_OUTPUT_UNUSABLE,
                        reasons.describe(
                            reasons.MODEL_OUTPUT_UNUSABLE,
                            "Nach dem Entfernen von Denkblöcken blieb kein Antworttext "
                            f"(finish_reason={result.finish_reason or 'unbekannt'}).",
                        ),
                    )
                self.transcript.append({"role": "assistant", "content": answer})
                return LoopOutcome(content=answer, end_reason="answer")

            keys: list[tuple[str, int]] = []
            for position in range(len(calls)):
                if adapter is not None and position in adapter.pending:
                    keys.append(adapter.pending[position])
                else:
                    keys.append(self.keys.reserve())
            round_message: Message = {
                "role": "assistant",
                "content": clean_answer(content),
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                    for call_id, name, arguments, _xml in calls
                ],
            }
            self.transcript.append(round_message)
            await self.recorder.round_message(record.nr, round_message)
            news = False
            for (call_id, name, arguments, xml), (key, key_nr) in zip(calls, keys, strict=True):
                message, signature = await self._execute_tool(
                    call_id, name, arguments, xml=xml, key=key, key_nr=key_nr, call_nr=record.nr
                )
                self.transcript.append(message)
                if signature not in seen:
                    news = True
                seen.add(signature)
            self._add_context_line(tools)
            if not news and previous_without_news:
                await self.recorder.event(
                    "stall",
                    "info",
                    "Zwei Runden hintereinander nur bekannte Werkzeugergebnisse - "
                    "Abschlussantwort mit dem Bisherigen.",
                    None,
                    record.nr,
                )
                end = "stall"
                break
            if not news:
                self.transcript.append({"role": "user", "content": prompts.STALL_HINT})
            previous_without_news = not news

        self._check_cancel()
        if end == "runtime":
            await self.recorder.event(
                "runtime",
                "info",
                f"Die Laufzeit von {self.runtime_seconds / 60:g} min ist erschöpft - "
                "Abschlussantwort mit dem Erkundeten.",
                None,
                self.call_nr,
            )
        return await self._final_answer(end or "failed")

    async def _final_answer(self, end: str) -> LoopOutcome:
        """Schlussnachricht und genau EIN Aufruf ohne Werkzeuge."""
        self.transcript.append({"role": "user", "content": prompts.CLOSING_MESSAGE})
        # Die ganze Laufzeit als Frist gibt es nur, wenn die Laufzeit das Ende war.
        deadline = (
            time.monotonic() + self.runtime_seconds if end == "runtime" else self.deadline
        )
        prepared = await self._prepare(None)
        _record, result, _adapter, failure = await self._call_with_recovery(
            prepared, tools=None, step="Abschlussantwort", deadline=deadline
        )
        if failure == reasons.MODEL_RESERVE_TIGHT:
            raise LoopFailed(failure, self._truncated_message(result), clean_answer(result.content))
        if not result.ok:
            raise LoopFailed(
                result.reason_code or reasons.MODEL_OUTPUT_UNUSABLE,
                result.message or reasons.describe(reasons.MODEL_OUTPUT_UNUSABLE),
                clean_answer(result.content),
            )
        answer = clean_answer(result.content)
        if not answer:
            raise LoopFailed(
                reasons.MODEL_OUTPUT_UNUSABLE,
                reasons.describe(
                    reasons.MODEL_OUTPUT_UNUSABLE,
                    "Die Abschlussantwort enthielt keinen Text "
                    f"(finish_reason={result.finish_reason or 'unbekannt'}).",
                ),
            )
        self.transcript.append({"role": "assistant", "content": answer})
        return LoopOutcome(content=answer, end_reason=end)
