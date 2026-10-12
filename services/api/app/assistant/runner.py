"""Ausfuehrung eines KI-Laufs im Worker (ai_tasks.task_type 'assistant_chat').

execute_run(run_id) laedt Lauf, Chat und Verlauf, fragt das Modell ueber die
Agentenschleife (app/assistant/loop.py) und haelt alles fest:

* jede gespeicherte Stromzeile in assistant_run_events (streng steigende seq)
  und erst DANACH auf dem Bus (Regel 5: done/error erst nach dem Commit),
* jeden Modellaufruf in assistant_run_calls (der Gateway schreibt dazu seine
  llm_calls-Zeile), jedes Werkzeugergebnis in assistant_tool_results,
* Denktext nie - nur seine Laenge (E6).

Abbrechen: ein Waechter hoert auf den Abbruch-Kanal des Laufs und prueft
zusaetzlich ai_tasks (cancel_requested / Status) - beides setzt den
CancelToken, der Gateway schliesst die Verbindung zum Modellserver. Der
Waechter schreibt auch das Lebenszeichen der Aufgabe (heartbeat_at), damit
ein Lauf, der laenger als die Wiederherstellungsfrist dauert, nicht als
verwaist gilt.

Die Sitzung des Laufs ist im Worker eine ohne RLS (jede Abfrage filtert nach
Haushalt); die Werkzeuge bekommen eigene READ-ONLY-Sitzungen mit den Rechten
des Verfassers.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import llm
from app.assistant import prompts, store
from app.assistant.compaction import SummaryState
from app.assistant.events import EventBus, cancel_channel, get_event_bus, run_channel
from app.assistant.loop import (
    AgentLoop,
    CallRecord,
    KeyAllocator,
    LoopCancelled,
    LoopFailed,
    LoopOutcome,
    ToolOutcome,
    ToolRecord,
)
from app.assistant.number_check import NumberCheck, Source, check
from app.assistant.tools import domain_tools, field_catalog_text, start_context
from app.assistant.tools.base import (
    ToolContext,
    ToolRejected,
    ToolReply,
    ToolSpec,
    ToolTable,
    read_only_session,
    render_page,
)
from app.llm import reasons

logger = logging.getLogger(__name__)

Message = dict[str, Any]
Event = dict[str, Any]

# fest: Abstand der Lebenszeichen und Abbruchpruefungen (wie der Ping des Stroms)
WATCH_SECONDS = 10.0

STAGE = "assistant_chat"


@dataclass(frozen=True)
class RunResult:
    """Was der Aufgabenverarbeiter fuer ai_tasks braucht."""

    status: str  # done | failed | cancelled | missing
    error_code: str | None = None
    error_message: str | None = None
    model_name: str | None = None
    chat_id: UUID | None = None
    already_finished: bool = False


# ---------------------------------------------------------------- Protokoll


class RunRecorder:
    """Speichert und veroeffentlicht, was die Schleife meldet (LoopRecorder)."""

    def __init__(self, session: AsyncSession, bus: EventBus, run: store.RunRow) -> None:
        self.session = session
        self.bus = bus
        self.run = run
        self.warnings = 0
        self.last_tool_key = 0

    async def publish(self, event: Event) -> None:
        try:
            await self.bus.publish(run_channel(self.run.id), event)
        except Exception:  # noqa: BLE001 - der Strom holt Fehlendes aus der Datenbank nach
            logger.warning("Ereignis des KI-Laufs %s nicht veroeffentlicht", self.run.id)

    async def persist(self, payload: Event) -> Event:
        event = await store.append_event(self.session, self.run.id, payload)
        await self.session.commit()
        await self.publish(event)
        return event

    async def live(self, payload: dict[str, Any]) -> None:
        await self.publish(payload)

    async def call_started(self, record: CallRecord) -> None:
        await self.session.execute(
            text(
                "insert into assistant_run_calls (household_id, created_by, run_id, nr, step, "
                "retry_of, retry_reason, started_at, budget, tools_offered) values "
                "(:h, :u, :run_id, :nr, :step, :retry_of, :retry_reason, :started_at, "
                "cast(:budget as jsonb), cast(:tools as jsonb))"
            ),
            {
                "h": str(self.run.household_id),
                "u": str(self.run.created_by),
                "run_id": str(self.run.id),
                "nr": record.nr,
                "step": record.step,
                "retry_of": record.retry_of,
                "retry_reason": record.retry_reason,
                "started_at": record.started_at,
                "budget": json.dumps(record.budget) if record.budget is not None else None,
                "tools": json.dumps(record.tools_offered),
            },
        )
        await self.persist(
            {"type": "call_start", "nr": record.nr, "step": record.step, "budget": record.budget}
        )

    async def call_finished(self, record: CallRecord) -> None:
        await self.session.execute(
            text(
                "update assistant_run_calls set duration_ms = :duration, ttft_ms = :ttft, "
                "finish_reason = :finish, model = :model, budget = cast(:budget as jsonb), "
                "prompt_tokens = :ptok, completion_tokens = :ctok, reasoning_tokens = :rtok, "
                "reasoning_chars = :rchars, tokens_per_s = :tps, answer_text = :answer, "
                "tool_call_names = cast(:names as jsonb), error_code = :error_code, "
                "error = :error, llm_call_id = :llm_call_id "
                "where run_id = :run_id and nr = :nr"
            ),
            {
                "duration": record.duration_ms,
                "ttft": record.ttft_ms,
                "finish": record.finish_reason,
                "model": record.model,
                "budget": json.dumps(record.budget) if record.budget is not None else None,
                "ptok": record.prompt_tokens,
                "ctok": record.completion_tokens,
                "rtok": record.reasoning_tokens,
                "rchars": record.reasoning_chars,
                "tps": record.tokens_per_s,
                "answer": record.answer_text,
                "names": json.dumps(record.tool_call_names),
                "error_code": record.error_code,
                "error": record.error,
                "llm_call_id": record.llm_call_id,
                "run_id": str(self.run.id),
                "nr": record.nr,
            },
        )
        await self.session.commit()

    async def round_message(self, call_nr: int, message: Message) -> None:
        await self.session.execute(
            text(
                "update assistant_run_calls set assistant_message = cast(:message as jsonb) "
                "where run_id = :run_id and nr = :nr"
            ),
            {
                "message": json.dumps(message, ensure_ascii=False),
                "run_id": str(self.run.id),
                "nr": call_nr,
            },
        )

    async def tool_started(self, key: str, name: str, args: dict[str, Any]) -> None:
        await self.persist({"type": "tool", "key": key, "name": name, "args": args})

    async def tool_finished(self, record: ToolRecord) -> None:
        await self.session.execute(
            text(
                "insert into assistant_tool_results (household_id, created_by, chat_id, run_id, "
                "key_nr, key, call_nr, tool_call_id, tool_name, args, status, result_text, "
                "result_columns, result_rows, row_count, duration_ms) values "
                "(:h, :u, :chat_id, :run_id, :key_nr, :key, :call_nr, :tool_call_id, :name, "
                "cast(:args as jsonb), :status, :text, cast(:columns as jsonb), "
                "cast(:rows as jsonb), :row_count, :duration)"
            ),
            {
                "h": str(self.run.household_id),
                "u": str(self.run.created_by),
                "chat_id": str(self.run.chat_id),
                "run_id": str(self.run.id),
                "key_nr": record.key_nr,
                "key": record.key,
                "call_nr": record.call_nr,
                "tool_call_id": record.tool_call_id,
                "name": record.name,
                "args": json.dumps(record.args, ensure_ascii=False, default=str),
                "status": record.status,
                "text": record.result_text,
                "columns": (
                    json.dumps(record.columns, ensure_ascii=False)
                    if record.columns is not None
                    else None
                ),
                "rows": (
                    json.dumps(record.rows, ensure_ascii=False, default=str)
                    if record.rows is not None
                    else None
                ),
                "row_count": record.row_count,
                "duration": record.duration_ms,
            },
        )
        self.last_tool_key = max(self.last_tool_key, record.key_nr)
        await self.session.execute(
            text(
                "update assistant_chats set last_tool_key = greatest(last_tool_key, :nr) "
                "where id = :chat_id"
            ),
            {"nr": record.key_nr, "chat_id": str(self.run.chat_id)},
        )
        await self.persist(
            {
                "type": "tool_result",
                "key": record.key,
                "status": record.status,
                "row_count": record.row_count,
                "preview": _preview(record.result_text),
            }
        )

    async def event(
        self,
        kind: str,
        level: str,
        message: str,
        details: dict[str, Any] | None = None,
        call_nr: int | None = None,
    ) -> None:
        normalized = level if level in ("info", "warnung", "fehler") else "info"
        if normalized != "info":
            self.warnings += 1
        await self.persist(
            {
                "type": "event",
                "kind": kind,
                "level": normalized,
                "message": message,
                "details": details,
                "call_nr": call_nr,
            }
        )

    async def meter(self, meter: dict[str, Any]) -> None:
        await self.persist({"type": "meter", **meter})


def _preview(text_value: str) -> str:
    # fest: Vorschau der Live-Anzeige (erste Zeilen), kein Promptmaterial
    lines = text_value.splitlines()[:4]
    joined = "\n".join(lines)
    # fest: Laenge der Vorschau in der Live-Anzeige, kein Promptmaterial
    return joined if len(joined) <= 400 else joined[:400] + " …"


# ---------------------------------------------------------------- Werkzeuge


class RunTools:
    """Werkzeuge eines Laufs (ToolRunner): die Fachwerkzeuge in READ-ONLY-
    Sitzungen, dazu result_page und reopen_result ueber die gespeicherten
    Ergebnisse des Chats."""

    def __init__(
        self,
        *,
        specs: list[ToolSpec],
        session_factory: async_sessionmaker[AsyncSession],
        session: AsyncSession,
        run: store.RunRow,
        today: date,
    ) -> None:
        self.specs = {spec.name: spec for spec in specs}
        self.session_factory = session_factory
        self.session = session
        self.row = run
        self.today = today
        self.page_size: Callable[[], int] | None = None
        self.replies: dict[str, ToolReply] = {}
        self.texts: dict[str, str] = {}

    def definitions(self) -> list[dict[str, Any]]:
        return [
            *(spec.definition() for spec in self.specs.values()),
            prompts.RESULT_PAGE_TOOL,
            prompts.REOPEN_RESULT_TOOL,
        ]

    def parameters_of(self, name: str) -> dict[str, Any] | None:
        if name in self.specs:
            return self.specs[name].parameters
        for builtin in (prompts.RESULT_PAGE_TOOL, prompts.REOPEN_RESULT_TOOL):
            if builtin["function"]["name"] == name:
                parameters: dict[str, Any] = builtin["function"]["parameters"]
                return parameters
        return None

    def _page_size(self) -> int:
        if self.page_size is None:
            raise RuntimeError("Seitengroesse der Werkzeugergebnisse ist nicht gesetzt.")
        return self.page_size()

    async def _stored(self, key: str) -> Row[Any] | None:
        return (
            await self.session.execute(
                text(
                    "select key, status, result_text, result_columns, result_rows "
                    "from assistant_tool_results where chat_id = :chat_id and key = :key "
                    "and household_id = :h"
                ),
                {"chat_id": str(self.row.chat_id), "key": key, "h": str(self.row.household_id)},
            )
        ).first()

    async def run(
        self, key: str, name: str, args: dict[str, Any], *, remaining_ms: int
    ) -> ToolOutcome:
        if name == "result_page":
            return await self._result_page(args)
        if name == "reopen_result":
            return await self._reopen(args)
        spec = self.specs[name]
        if spec.handler is None:
            return ToolOutcome("rejected", f"Fehler: „{name}“ ist hier nicht verfügbar.")
        household, user = self.row.household_id, self.row.created_by
        try:
            if spec.needs_db:
                async with read_only_session(
                    self.session_factory,
                    household_id=household,
                    user_id=user,
                    timeout_ms=remaining_ms,
                ) as session:
                    reply = await spec.handler(
                        ToolContext(session, household, user, self.today), args
                    )
            else:
                reply = await spec.handler(
                    ToolContext(self.session, household, user, self.today), args
                )
        except ToolRejected as exc:
            return ToolOutcome("rejected", f"Fehler: {exc}")
        page = render_page(key, reply, 0, self._page_size())
        result_text = page.text
        if reply.status != "ok" and not result_text.startswith("Fehler"):
            result_text = "Fehler: " + result_text
        self.replies[key] = reply
        self.texts[key] = result_text
        table = reply.table
        return ToolOutcome(
            status=reply.status,
            text=result_text,
            columns=table.columns if table is not None else None,
            rows=table.rows if table is not None else None,
            row_count=table.total if table is not None else None,
        )

    async def _result_page(self, args: dict[str, Any]) -> ToolOutcome:
        key = str(args.get("id") or "").strip()
        start = args.get("from")
        if isinstance(start, str) and start.strip().isdigit():
            start = int(start.strip())
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            return ToolOutcome(
                "rejected", "Fehler: „from“ (erste Zeile der Seite, ab 0) fehlt oder ist ungültig."
            )
        reply = self.replies.get(key)
        if reply is None:
            stored = await self._stored(key)
            if stored is not None and stored.result_rows is not None:
                reply = ToolReply(
                    table=ToolTable(
                        columns=list(stored.result_columns or []),
                        rows=[list(row) for row in stored.result_rows],
                    )
                )
        if reply is None or reply.table is None:
            return ToolOutcome(
                "rejected",
                f"Fehler: kein Ergebnis mit Zeilen und der Kennung „{key}“ in diesem Chat.",
            )
        page = render_page(key, reply, start, self._page_size())
        return ToolOutcome("ok", page.text, row_count=page.total)

    async def _reopen(self, args: dict[str, Any]) -> ToolOutcome:
        key = str(args.get("id") or "").strip()
        original = self.texts.get(key)
        if original is None:
            stored = await self._stored(key)
            original = str(stored.result_text) if stored is not None else None
        if original is None:
            return ToolOutcome(
                "rejected", f"Fehler: kein Werkzeugergebnis mit der Kennung „{key}“ in diesem Chat."
            )
        return ToolOutcome("ok", original)


# ---------------------------------------------------------------- Verlauf


@dataclass
class History:
    transcript: list[Message]
    question: Message | None
    keys: dict[int, str]
    message_ids: dict[int, UUID]
    tool_texts: dict[str, str]
    summary: SummaryState | None


async def build_history(
    session: AsyncSession, run: store.RunRow, chat: Row[Any], *, include_question: bool
) -> History:
    """Verlauf frueherer Fragen fuer das Modell: je Frage die juengste
    erfolgreiche Antwort samt ihren Werkzeugrunden (Werkzeugergebnisse im
    Wortlaut, wie das Modell sie bekam). Fragen ohne erfolgreiche Antwort
    entfallen. Die Zusammenfassung des Chats ersetzt den abgedeckten Anfang."""
    rows = [row for row in await store.message_rows(session, run.chat_id, run.household_id)]
    chat_rows = [row for row in rows if row.kind == "chat"]
    question_row = next(
        (row for row in chat_rows if run.question_message_id and row.id == run.question_message_id),
        None,
    )
    limit_seq = int(question_row.seq) if question_row is not None else None
    answers: dict[str, Row[Any]] = {}
    for row in chat_rows:
        if row.role == "assistant" and row.status == "done" and row.reply_to_message_id:
            answers[str(row.reply_to_message_id)] = row  # spaetere ueberschreiben fruehere
    pairs: list[tuple[Row[Any], Row[Any]]] = []
    for row in chat_rows:
        if row.role != "user":
            continue
        if limit_seq is not None and int(row.seq) >= limit_seq:
            continue
        answer = answers.get(str(row.id))
        if answer is not None:
            pairs.append((row, answer))
    run_ids = [str(answer.run_id) for _question, answer in pairs if answer.run_id]
    calls_by_run: dict[str, list[Row[Any]]] = {}
    tools_by_run: dict[str, list[Row[Any]]] = {}
    if run_ids:
        for call in (
            await session.execute(
                text(
                    "select run_id, nr, assistant_message from assistant_run_calls "
                    "where household_id = :h and run_id = any(cast(:ids as uuid[])) "
                    "and assistant_message is not null order by run_id, nr"
                ),
                {"h": str(run.household_id), "ids": run_ids},
            )
        ).all():
            calls_by_run.setdefault(str(call.run_id), []).append(call)
        for tool in (
            await session.execute(
                text(
                    "select run_id, call_nr, tool_call_id, key, result_text "
                    "from assistant_tool_results where household_id = :h "
                    "and run_id = any(cast(:ids as uuid[])) order by run_id, key_nr"
                ),
                {"h": str(run.household_id), "ids": run_ids},
            )
        ).all():
            tools_by_run.setdefault(str(tool.run_id), []).append(tool)

    transcript: list[Message] = []
    keys: dict[int, str] = {}
    message_ids: dict[int, UUID] = {}
    tool_texts: dict[str, str] = {}
    by_id: dict[str, Message] = {}
    for question, answer in pairs:
        user: Message = {"role": "user", "content": str(question.content)}
        transcript.append(user)
        message_ids[id(user)] = UUID(str(question.id))
        by_id[str(question.id)] = user
        run_key = str(answer.run_id) if answer.run_id else ""
        results = tools_by_run.get(run_key, [])
        for call in calls_by_run.get(run_key, []):
            message = dict(call.assistant_message)
            transcript.append(message)
            for tool_call in message.get("tool_calls") or []:
                match = next(
                    (
                        item
                        for item in results
                        if item.call_nr == call.nr and item.tool_call_id == tool_call.get("id")
                    ),
                    None,
                )
                if match is None:
                    continue
                tool_message: Message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": str(match.result_text),
                }
                transcript.append(tool_message)
                keys[id(tool_message)] = str(match.key)
                tool_texts[str(match.key)] = str(match.result_text)
        reply: Message = {"role": "assistant", "content": str(answer.content)}
        transcript.append(reply)
        message_ids[id(reply)] = UUID(str(answer.id))
        by_id[str(answer.id)] = reply

    current: Message | None = None
    if include_question and question_row is not None:
        current = {"role": "user", "content": str(question_row.content)}
        transcript.append(current)
        message_ids[id(current)] = UUID(str(question_row.id))
    elif not include_question and pairs:
        # Verdichtung: Grenze ist die letzte Frage (sie und ihre Antwort
        # bleiben im Wortlaut).
        current = by_id.get(str(pairs[-1][0].id))

    summary: SummaryState | None = None
    summary_run = await store.chat_summary_run(session, chat)
    if summary_run is not None and summary_run.summary_text:
        covers = by_id.get(str(summary_run.summary_covers_until_message_id))
        if covers is not None:
            summary = SummaryState(
                covers=covers,
                text=str(summary_run.summary_text),
                nr=int(summary_run.summary_nr or 1),
                message_id=UUID(str(summary_run.summary_covers_until_message_id)),
                tokens_before=summary_run.summary_tokens_before,
                tokens_after=summary_run.summary_tokens_after,
                unverified=int(summary_run.summary_unverified or 0),
            )
    return History(
        transcript=transcript,
        question=current,
        keys=keys,
        message_ids=message_ids,
        tool_texts=tool_texts,
        summary=summary,
    )


# ---------------------------------------------------------------- Waechter


async def _watch(
    run: store.RunRow,
    cancel: llm.CancelToken,
    bus: EventBus,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Abbruch ueber den Bus (sofort) und ueber ai_tasks/assistant_runs
    (Ruecksicherung, wenn Redis fehlt); dazu das Lebenszeichen der Aufgabe."""

    async def poll() -> None:
        while not cancel.cancelled:
            async with session_factory() as session:
                state = (
                    await session.execute(
                        text("select status from assistant_runs where id = :id"),
                        {"id": str(run.id)},
                    )
                ).first()
                task = None
                if run.ai_task_id is not None:
                    task = (
                        await session.execute(
                            text(
                                "update ai_tasks set heartbeat_at = now() where id = :id "
                                "and household_id = :h returning status, cancel_requested"
                            ),
                            {"id": str(run.ai_task_id), "h": str(run.household_id)},
                        )
                    ).first()
                await session.commit()
            stop = state is None or state.status not in store.ACTIVE_STATUSES
            if run.ai_task_id is not None:
                stop = (
                    stop
                    or task is None
                    or task.status not in store.ACTIVE_STATUSES
                    or bool(task.cancel_requested)
                )
            if stop:
                cancel.cancel()
                return
            await asyncio.sleep(WATCH_SECONDS)

    async def listen() -> None:
        try:
            async with bus.subscribe(cancel_channel(run.id)) as subscription:
                while not cancel.cancelled:
                    if await subscription.get(WATCH_SECONDS) is not None:
                        cancel.cancel()
                        return
        except Exception:  # noqa: BLE001 - ohne Bus bleibt die Pruefung ueber die Datenbank
            logger.warning("Abbruch-Kanal des KI-Laufs %s nicht erreichbar", run.id)

    await asyncio.gather(poll(), listen())


# ---------------------------------------------------------------- Lauf


async def _set_run_info(session: AsyncSession, run_id: UUID, values: dict[str, Any]) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in values)
    await session.execute(
        text(f"update assistant_runs set {assignments}, updated_at = now() where id = :run_id"),
        {**values, "run_id": str(run_id)},
    )


async def _store_summary(
    session: AsyncSession, run: store.RunRow, summary: SummaryState
) -> None:
    if summary.message_id is None:
        return
    await session.execute(
        text(
            "update assistant_runs set summary_nr = :nr, summary_text = :text, "
            "summary_covers_until_message_id = :covers, summary_tokens_before = :before, "
            "summary_tokens_after = :after, summary_unverified = :unverified where id = :id"
        ),
        {
            "nr": summary.nr,
            "text": summary.text,
            "covers": str(summary.message_id),
            "before": summary.tokens_before,
            "after": summary.tokens_after,
            "unverified": summary.unverified,
            "id": str(run.id),
        },
    )
    await session.execute(
        text("update assistant_chats set summary_run_id = :run_id where id = :chat_id"),
        {"run_id": str(run.id), "chat_id": str(run.chat_id)},
    )


def _number_sources(history_texts: dict[str, str], transcript: list[Message]) -> list[Source]:
    sources = [Source(key, value) for key, value in history_texts.items()]
    inputs = "\n".join(
        str(message.get("content") or "")
        for message in transcript
        if message.get("role") in ("system", "user")
    )
    sources.append(Source("Eingabe", inputs, "eingabe"))
    return sources


async def execute_run(
    run_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    bus: EventBus | None = None,
    client: httpx.AsyncClient | None = None,
    today: date | None = None,
) -> RunResult:
    """Fuehrt einen Lauf aus (Antwort oder Verdichtung) und schliesst ihn ab.

    session_factory  Sitzungen des Prozesses (Worker: ohne RLS)
    bus              Live-Ereignisse (Vorgabe: Redis des Prozesses)
    client           httpx-Client fuer Modellserver und Discovery (Tests)"""
    if session_factory is None:
        from app.core.db import get_session_factory

        session_factory = get_session_factory()
    event_bus = bus or get_event_bus()
    try:
        return await _run_with(run_id, session_factory, event_bus, client, today)
    finally:
        if bus is None:
            # Der Redis-Client gehoert zur Ereignisschleife dieses Laufs (der
            # Worker startet je Aufgabe eine neue) - schliessen statt liegen
            # lassen; der naechste Lauf legt einen neuen an.
            await event_bus.close()


async def _run_with(
    run_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: EventBus,
    client: httpx.AsyncClient | None,
    today: date | None,
) -> RunResult:
    async with session_factory() as session:
        row = await store.load_run_row(session, run_id)
        if row is None:
            return RunResult(status="missing", error_message="Der KI-Lauf existiert nicht mehr.")
        run = store.RunRow.of(row)
        if run.status not in store.ACTIVE_STATUSES:
            return RunResult(status=run.status, chat_id=run.chat_id, already_finished=True)
        recorder = RunRecorder(session, event_bus, run)
        await session.execute(
            text(
                "update assistant_runs set status = 'running', "
                "started_at = coalesce(started_at, now()), updated_at = now() where id = :id"
            ),
            {"id": str(run.id)},
        )
        await session.execute(
            text(
                "update assistant_messages set status = 'running', updated_at = now() "
                "where id = :id and status = 'queued'"
            ),
            {"id": str(run.message_id)},
        )
        await recorder.persist({"type": "status", "status": "running"})

        cancel = llm.CancelToken()
        watcher = asyncio.ensure_future(_watch(run, cancel, event_bus, session_factory))
        loop: AgentLoop | None = None
        outcome: LoopOutcome | None = None
        failure: LoopFailed | None = None
        cancelled: LoopCancelled | None = None
        history: History | None = None
        try:
            loop, history = await _prepare_loop(
                session, run, recorder, cancel, session_factory, client, today
            )
            if run.mode == "compact":
                outcome = await loop.compact(run.focus or "")
            else:
                outcome = await loop.run()
        except LoopCancelled as exc:
            cancelled = exc
        except store.RunGone:
            # Chat waehrend des Laufs geloescht - nichts mehr festzuhalten.
            await session.rollback()
            return RunResult(status="cancelled", chat_id=run.chat_id, already_finished=True)
        except LoopFailed as exc:
            failure = exc
        except Exception as exc:  # noqa: BLE001 - jeder Fehler endet als benannter Lauf
            logger.exception("KI-Lauf %s ist fehlgeschlagen", run.id)
            await session.rollback()
            failure = LoopFailed(
                "assistant_internal_error",
                f"Interner Fehler im KI-Lauf ({type(exc).__name__}: {exc}).",
            )
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        if failure is None and cancelled is None and cancel.cancelled:
            # Abbruch nach der letzten Antwort: gilt trotzdem als Abbruch.
            cancelled = LoopCancelled(outcome.content if outcome else "")
        try:
            return await _finish(session, recorder, run, loop, history, outcome, failure, cancelled)
        except store.RunGone:
            await session.rollback()
            return RunResult(status="cancelled", chat_id=run.chat_id, already_finished=True)


async def _prepare_loop(
    session: AsyncSession,
    run: store.RunRow,
    recorder: RunRecorder,
    cancel: llm.CancelToken,
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient | None,
    today: date | None,
) -> tuple[AgentLoop, History]:
    try:
        settings = await llm.load_settings(session, run.household_id)
    except llm.SettingsRowMissingError as exc:
        raise LoopFailed(reasons.MODEL_SETTINGS_INVALID, str(exc)) from exc
    if settings.run_time_limit_minutes is None or settings.run_time_limit_minutes <= 0:
        raise LoopFailed(
            reasons.MODEL_SETTINGS_INVALID,
            reasons.describe(
                reasons.MODEL_SETTINGS_INVALID, "Die Laufzeit eines KI-Laufs ist nicht eingestellt."
            ),
        )
    runtime_seconds = float(settings.run_time_limit_minutes) * 60
    deadline = time.monotonic() + runtime_seconds
    # Bei ausgeschalteter KI ohne Serverkontakt: ai_disabled (unten LoopFailed).
    found = await llm.resolve_target(settings, client=client)
    if found.target is not None:
        window = found.target.window
        await _set_run_info(
            session,
            run.id,
            {
                "model_configured": settings.model_name or found.target.model,
                "window_loaded": window.loaded,
                "window_cap": window.cap,
                "window_effective": window.effective,
                "window_source": window.source,
                "reasoning_capability": window.reasoning_capability.kind,
            },
        )
        await session.commit()
    if not found.ready or found.target is None:
        raise LoopFailed(
            found.reason_code or reasons.MODEL_UNAVAILABLE,
            found.message or reasons.describe(reasons.MODEL_UNAVAILABLE),
        )
    target = found.target
    scope = llm.CallScope(
        session=session,
        household_id=run.household_id,
        stage=STAGE,
        prompt_version=prompts.PROMPT_VERSION,
        ai_task_id=run.ai_task_id,
    )
    measurement = await llm.measurement_for(target, settings, scope)
    chat = await store.load_chat_row(
        session, run.chat_id, household_id=run.household_id, user_id=None
    )
    if chat is None:
        raise LoopFailed("chat_missing", "Der Chat existiert nicht mehr.")
    history = await build_history(session, run, chat, include_question=run.mode == "answer")
    day = today or date.today()
    specs = domain_tools()
    tools = RunTools(
        specs=specs, session_factory=session_factory, session=session, run=run, today=day
    )
    transcript = history.transcript
    if run.mode == "answer":
        if history.question is None:
            raise LoopFailed("question_missing", "Die Frage zu diesem Lauf fehlt.")
        transcript.insert(
            0,
            {
                "role": "system",
                "content": prompts.system_prompt(
                    context_line=bool(settings.context_line_enabled),
                    start_context=await _start_context(
                        session_factory, run, specs, day, int(runtime_seconds * 1000)
                    ),
                ),
            },
        )
    loop = AgentLoop(
        run_id=str(run.id),
        target=target,
        settings=settings,
        scope=scope,
        recorder=recorder,
        tools=tools,
        cancel=cancel,
        transcript=transcript,
        question=history.question,
        keys=KeyAllocator(int(chat.last_tool_key)),
        deadline=deadline,
        runtime_seconds=runtime_seconds,
        reasoning_override=run.reasoning_override,
        client=client,
        measurement=measurement,
    )
    loop.compactor.keys.update(history.keys)
    loop.compactor.message_ids.update(history.message_ids)
    loop.compactor.tool_texts.update(history.tool_texts)
    loop.compactor.summary = history.summary
    def page_size() -> int:
        """Seite eines Ergebnisses: Anteil des Fensters in Zeichen dieses
        Modells - mit der jeweils aktuellen Messung."""
        current = loop.measurement or measurement
        return llm.page_chars(
            target.window.effective, settings.result_page_share, current.chars_per_token
        )

    try:
        page_size()
    except llm.BudgetInputError as exc:
        raise LoopFailed(
            reasons.MODEL_SETTINGS_INVALID,
            reasons.describe(reasons.MODEL_SETTINGS_INVALID, f"{exc} ({exc.field})"),
        ) from exc
    tools.page_size = page_size
    return loop, history


async def _start_context(
    session_factory: async_sessionmaker[AsyncSession],
    run: store.RunRow,
    specs: list[ToolSpec],
    day: date,
    timeout_ms: int,
) -> str:
    """Startkontext vor der ersten Antwort: Datum, Zeitraum der Daten, Konten
    und Feldkatalog - in einer READ-ONLY-Sitzung des Verfassers."""
    async with read_only_session(
        session_factory,
        household_id=run.household_id,
        user_id=run.created_by,
        timeout_ms=timeout_ms,
    ) as session:
        facts = await start_context(session, run.household_id, day)
    offers_analytics = any(spec.name == "query_analytics" for spec in specs)
    return prompts.start_context_text(facts, field_catalog_text() if offers_analytics else None)


async def _finish(
    session: AsyncSession,
    recorder: RunRecorder,
    run: store.RunRow,
    loop: AgentLoop | None,
    history: History | None,
    outcome: LoopOutcome | None,
    failure: LoopFailed | None,
    cancelled: LoopCancelled | None,
) -> RunResult:
    meter = loop.meter if loop is not None else None
    queries = loop.tool_count if loop is not None else 0
    numbers: NumberCheck | None = None
    content: str | None = None
    status: str
    end_reason: str | None
    error_code: str | None = None
    error_message: str | None = None
    if cancelled is not None:
        status, end_reason = "cancelled", "cancelled"
        content = cancelled.content
        error_code = reasons.MODEL_CANCELLED
        error_message = "Vom Nutzer abgebrochen."
    elif failure is not None:
        status, end_reason = "failed", "failed"
        content = failure.content
        error_code = failure.code
        error_message = failure.message
    else:
        assert outcome is not None
        status, end_reason = "done", outcome.end_reason
        content = outcome.content
        if run.mode == "answer" and loop is not None:
            texts = dict(loop.compactor.tool_texts)
            numbers = check(content, _number_sources(texts, loop.transcript))
    if loop is not None:
        await _set_run_info(
            session,
            run.id,
            {"model_answering": loop.model_answering, "reasoning_sent": loop.reasoning_sent},
        )
        summary = loop.compactor.chat_summary
        if summary is not None and (status == "done" or run.mode == "answer"):
            await _store_summary(session, run, summary)
    run_summary = {
        "queries": queries,
        "warnings": recorder.warnings,
        "unverified_numbers": numbers.unverified if numbers is not None else 0,
    }
    message_row = await store.finish_run_rows(
        session,
        run_id=run.id,
        status=status,
        end_reason=end_reason,
        error_code=error_code,
        error_message=error_message,
        content=content if content else None,
        numbers=numbers.as_dict() if numbers is not None else None,
        context_meter=meter,
        run_summary=run_summary if run.mode == "answer" else None,
    )
    if message_row is None:
        await session.commit()
        return RunResult(status=status, chat_id=run.chat_id, already_finished=True)
    message = store.message_of(message_row)
    await recorder.persist(
        store.final_event(message, error_code=error_code, error_text=error_message)
    )
    return RunResult(
        status=status,
        error_code=error_code,
        error_message=error_message,
        model_name=loop.model_answering if loop is not None else None,
        chat_id=run.chat_id,
    )
