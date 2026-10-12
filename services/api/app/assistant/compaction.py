"""Verdichtung des Verlaufs in drei Stufen (Port von sia_public ai.js,
stufe1/fasseZusammen/stufe2/passtNicht).

Stufe 1  Aeltere Werkzeugergebnisse werden zu Platzhaltern, das aelteste
         zuerst ("[Verdichtet · Ergebnis W3 · N Zeichen ... reopen_result]").
         Das Original bleibt im Protokoll und laesst sich wieder oeffnen.
Stufe 2  Dasselbe Modell fasst den aelteren Verlauf in 7 Abschnitten zusammen
         (Auftrag; alle Nutzernachrichten im Wortlaut; Datenbasis; Zahlen mit
         [W-Kennung]; Fehler/Korrekturen; verworfene Annahmen; Offenes). Zu
         grosse Verlaeufe in Abschnitten - wie viele Teile in einen Aufruf
         passen, sagt eine Binaersuche ueber die Budgetrechnung. Die Zahlen der
         Zusammenfassung laufen durch die Zahlenpruefung; sie ist nie Nachweis.
Stufe 3  Passt es dann noch nicht, scheitert der Aufruf laut mit
         Aufschluesselung (model_input_too_large) - nichts wird gesendet.

Grenzen der Zusammenfassung sind die aktuelle Frage (alles davor) und der
letzte Rundenstart (alles davor, nur innerhalb der aktuellen Frage). Eine
Zusammenfassung an der Fragegrenze gilt fuer den Chat (ChatSummary) und wird
gespeichert; eine an der Rundengrenze nur fuer den laufenden Lauf.

Kein Netz, keine DB: das Modell ruft der Aufrufer ueber `summarize_call`.
"""

import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.assistant import prompts
from app.assistant.number_check import Source, check
from app.assistant.text import clean_answer
from app.llm.budget import Budget, chars_of, number_de

Message = dict[str, Any]
BudgetFn = Callable[[list[Message], list[dict[str, Any]] | None], Budget]
# (Nachrichten, Schritt) -> Antworttext oder None, wenn das Modell nichts lieferte
SummarizeCall = Callable[[list[Message], str], Awaitable[str | None]]
EventFn = Callable[[str, str, str, dict[str, Any] | None], Awaitable[None]]


class InputTooLarge(Exception):
    """Stufe 3: auch nach dem Verdichten passt der Auftrag nicht."""

    def __init__(self, budget: Budget, lead: str) -> None:
        super().__init__(f"{lead}: {budget.describe()}")
        self.budget = budget
        self.lead = lead


class SummaryFailed(Exception):
    """Das Modell lieferte keine Zusammenfassung (Grund im Text)."""

    def __init__(self, reason_code: str | None, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass
class SummaryState:
    """Gueltige Zusammenfassung: sie ersetzt alles bis einschliesslich
    `covers` (Identitaet der Nachricht im Verlauf)."""

    covers: Message
    text: str
    nr: int
    # Nachricht des Chats, bis zu der sie reicht (nur an der Fragegrenze)
    message_id: UUID | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None
    unverified: int = 0
    # neu in diesem Lauf entstanden (speichern)
    fresh: bool = False


@dataclass
class Prepared:
    messages: list[Message]
    budget: Budget


def is_round_start(message: Message | None) -> bool:
    return bool(message and message.get("role") == "assistant" and message.get("tool_calls"))


def first_content(messages: Sequence[Message]) -> int:
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        index += 1
    return index


def _index_of(messages: Sequence[Message], target: Message | None) -> int:
    if target is None:
        return -1
    return next((index for index, item in enumerate(messages) if item is target), -1)


@dataclass
class Compactor:
    """Verdichtung eines Laufs ueber seinem Verlauf (`transcript`).

    transcript   Originale, nur angehaengt (Systemnachricht, Verlauf frueherer
                 Fragen, aktuelle Frage, Runden dieses Laufs)
    keys         id(Nachricht) -> Werkzeugkennung (W3) der Werkzeugergebnisse
    message_ids  id(Nachricht) -> Chatnachricht, die sie abschliesst (Frage
                 bzw. Antwort) - fuer die Grenze einer Chat-Zusammenfassung
    question     die aktuelle Frage (Grenze der Chat-Zusammenfassung)
    budget_fn    Budget fuer eine Liste (Fenster, Messung, Reserve, Basis)
    summarize_call  ein Modellaufruf ohne Werkzeuge"""

    transcript: list[Message]
    budget_fn: BudgetFn
    summarize_call: SummarizeCall
    event: EventFn
    chars_per_token: Callable[[], float]
    keys: dict[int, str] = field(default_factory=dict)
    message_ids: dict[int, UUID] = field(default_factory=dict)
    question: Message | None = None
    summary: SummaryState | None = None
    # die juengste in diesem Lauf entstandene Zusammenfassung an der
    # Fragegrenze - sie gilt fuer den Chat und wird gespeichert
    chat_summary: SummaryState | None = None
    tool_texts: dict[str, str] = field(default_factory=dict)
    _placeholders: dict[int, Message] = field(default_factory=dict)
    _carriers: dict[int, Message] = field(default_factory=dict)
    _reported: str | None = None

    # ------------------------------------------------------------ Ansicht

    def summary_valid(self) -> bool:
        if self.summary is None:
            return False
        return _index_of(self.transcript, self.summary.covers) >= first_content(self.transcript)

    def with_summary(self) -> list[Message]:
        """Der Verlauf mit der gueltigen Zusammenfassung anstelle des
        abgedeckten Anfangs. Steht danach eine Nutzernachricht, traegt sie die
        Zusammenfassung (eine Nachricht weniger, gleiche Rolle wie vorher)."""
        messages = self.transcript
        if not self.summary_valid():
            return list(messages)
        assert self.summary is not None
        start = first_content(messages)
        end = _index_of(messages, self.summary.covers)
        head = list(messages[:start])
        rest = list(messages[end + 1 :])
        block = prompts.summary_block(self.summary.nr, self.summary.text)
        if rest and rest[0].get("role") == "user":
            follower = rest[0]
            carrier = self._carriers.get(id(follower))
            expected = block + "\n\n---\n\n" + str(follower.get("content") or "")
            if carrier is None or carrier.get("content") != expected:
                carrier = {"role": "user", "content": expected}
                self._carriers[id(follower)] = carrier
            return [*head, carrier, *rest[1:]]
        return [*head, {"role": "user", "content": block}, *rest]

    def _placeholder(self, message: Message) -> Message:
        cached = self._placeholders.get(id(message))
        if cached is not None:
            return cached
        content = str(message.get("content") or "")
        key = self.keys.get(id(message))
        replacement = {
            "role": "tool",
            "tool_call_id": message.get("tool_call_id"),
            "content": prompts.placeholder(key, len(content)),
        }
        self._placeholders[id(message)] = replacement
        return replacement

    # ------------------------------------------------------------ Stufe 1

    def stage1(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        force: bool,
        include_round: bool = False,
    ) -> tuple[list[Message], Budget, list[str], int, int]:
        """Werkzeugergebnisse vor der laufenden Runde (mit include_round: bis
        auf das juengste) durch Platzhalter ersetzen, das aelteste zuerst, bis
        der Auftrag passt (force: alle)."""
        protect = len(messages)
        for index in reversed(range(len(messages))):
            item = messages[index]
            if include_round and item.get("role") == "tool":
                protect = index
                break
            if not include_round and is_round_start(item):
                protect = index
                break
        out = list(messages)
        ids: list[str] = []
        before = 0
        after = 0
        budget = self.budget_fn(out, tools)
        placeholders = {id(value) for value in self._placeholders.values()}
        for index in range(len(out)):
            if not force and budget.fits:
                break
            item = out[index]
            if item.get("role") != "tool" or index >= protect:
                continue
            original = item
            if id(item) in placeholders:
                continue  # schon ein Platzhalter
            replacement = self._placeholder(original)
            if len(str(replacement["content"])) >= len(str(original.get("content") or "")):
                continue
            out[index] = replacement
            before += len(str(original.get("content") or ""))
            after += len(str(replacement["content"]))
            key = self.keys.get(id(original))
            if key:
                ids.append(key)
            budget = self.budget_fn(out, tools)
        return out, budget, ids, before, after

    async def _report_stage1(self, ids: list[str], before: int, after: int) -> None:
        if not before:
            return
        marker = ",".join(ids)
        if marker == self._reported:
            return
        self._reported = marker
        named = (
            f"1 älteres Werkzeugergebnis ({ids[0]})"
            if len(ids) == 1
            else f"{len(ids)} ältere Werkzeugergebnisse ({', '.join(ids)})"
        )
        await self.event(
            "compacted",
            "info",
            f"{named} verdichtet ({number_de(before)} → {number_de(after)} Zeichen), damit der "
            "Auftrag unter die Antwortreserve passt. Mit reopen_result wieder zu öffnen.",
            {"ids": ids, "before": before, "after": after},
        )

    # ------------------------------------------------------------ Stufe 2

    def _as_text(self, message: Message) -> str:
        content = str(message.get("content") or "")
        role = message.get("role")
        if role == "user":
            return "[Nutzer] " + content
        if role == "tool":
            key = self.keys.get(id(message))
            return f"[Ergebnis {key or 'ohne Kennung'}] " + content
        if role == "assistant":
            parts = ["[Assistent] " + content] if content else []
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                parts.append(
                    f"[Werkzeugaufruf {function.get('name') or '?'}] "
                    f"{function.get('arguments') or ''}"
                )
            return "\n".join(parts) or "[Assistent]"
        return f"[{role}] " + content

    def _tokens(self, messages: Sequence[Message]) -> int:
        return math.ceil(chars_of(messages) / self.chars_per_token())

    async def summarize(
        self, start: int, end: int, focus: str | None
    ) -> tuple[str, int, int, int] | None:
        """Fasst transcript[start:end] zusammen, aufbauend auf der bisherigen
        Zusammenfassung. Liefert (Text, Token vorher, Token nachher, Zahlen
        ohne Beleg) oder None, wenn es nichts zusammenzufassen gibt."""
        previous = self.summary.text if self.summary_valid() and self.summary else None
        span = [item for item in self.transcript[start:end] if item.get("role") != "system"]
        parts = [self._as_text(item) for item in span]
        if not parts and not (focus is not None and previous):
            return None
        if not parts:
            parts = [prompts.NOTHING_NEW]
        tokens_before = self._tokens(span)
        current = previous
        sections = 0
        remaining = list(parts)
        while remaining:
            low, high = 0, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                probe = prompts.summary_messages(current, remaining[:middle], focus)
                if self.budget_fn(probe, None).fits:
                    low = middle
                else:
                    high = middle - 1
            if low < 1:
                single = prompts.summary_messages(current, remaining[:1], focus)
                raise InputTooLarge(
                    self.budget_fn(single, None),
                    "Ein einzelner Teil des Verlaufs ist zu groß, um ihn zusammenzufassen",
                )
            sections += 1
            step = "Verdichtung" if sections == 1 else f"Verdichtung, Abschnitt {sections}"
            answer = await self.summarize_call(
                prompts.summary_messages(current, remaining[:low], focus), step
            )
            text = clean_answer(answer)
            if not text:
                raise SummaryFailed(None, "Das Modell lieferte keine Zusammenfassung.")
            current = text
            del remaining[:low]
        assert current is not None
        # Zahlen der Zusammenfassung: belegt, wenn sie im Verlauf (alle Rollen,
        # im Wortlaut) oder in einem Werkzeugergebnis stehen - nicht aber in
        # einer frueheren Zusammenfassung.
        history = "\n".join(self._as_text(item) for item in self.transcript)
        sources = [Source("Verlauf", history, "eingabe")]
        sources.extend(Source(key, text) for key, text in self.tool_texts.items())
        unverified = check(current, sources).unverified
        tokens_after = math.ceil(len(current) / self.chars_per_token())
        await self.event(
            "summarized",
            "warnung" if unverified else "info",
            (
                f"{len(span)} Nachrichten durch das Modell zusammengefasst: "
                f"{number_de(tokens_before)} → {number_de(tokens_after)} Token"
                + (f" in {sections} Abschnitten" if sections > 1 else "")
                + (f", {unverified} Zahl(en) ohne Beleg" if unverified else "")
                + ". Die Originale bleiben im Protokoll."
            ),
            {
                "text": current,
                "before": tokens_before,
                "after": tokens_after,
                "sections": sections,
                "unverified": unverified,
                "focus": focus,
                "messages": len(span),
            },
        )
        return current, tokens_before, tokens_after, unverified

    async def stage2(
        self, tools: list[dict[str, Any]] | None, *, force: bool, focus: str | None, deficit: int
    ) -> tuple[list[Message], Budget] | None:
        messages = self.transcript
        start = first_content(messages)
        question = _index_of(messages, self.question)
        round_start = -1
        for index, item in enumerate(messages):
            if index >= start and is_round_start(item):
                round_start = index
        bounds = [question]
        if focus is None and round_start > question:
            bounds.append(round_start)
        best: tuple[list[Message], Budget] | None = None
        for bound in sorted({value for value in bounds if value > start}):
            valid = self.summary_valid()
            covered = _index_of(messages, self.summary.covers) if valid and self.summary else -1
            if valid and covered >= bound - 1 and focus is None:
                continue  # schon bis hierher zusammengefasst
            begin = covered + 1 if valid else start
            if focus is None and deficit > 0 and self._tokens(messages[begin:bound]) < deficit:
                continue  # dieser Abschnitt kann den Platz nie schaffen
            produced = await self.summarize(begin, bound, focus)
            if produced is None:
                continue
            text, tokens_before, tokens_after, unverified = produced
            previous_nr = self.summary.nr if valid and self.summary else 0
            covers = messages[bound - 1]
            self.summary = SummaryState(
                covers=covers,
                text=text,
                nr=previous_nr + 1,
                message_id=self.message_ids.get(id(covers)) if bound == question else None,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                unverified=unverified,
                fresh=True,
            )
            if bound == question:
                self.chat_summary = self.summary
            out, budget, ids, before, after = self.stage1(self.with_summary(), tools, force=force)
            await self._report_stage1(ids, before, after)
            best = (out, budget)
            if budget.fits and not force:
                return best
        return best

    # ------------------------------------------------------------ Ablauf

    async def prepare(
        self,
        tools: list[dict[str, Any]] | None,
        *,
        force: bool = False,
        focus: str | None = None,
    ) -> Prepared:
        """Liste fuer den naechsten Aufruf: Zusammenfassung einsetzen, Budget
        rechnen, notfalls Stufe 1 -> 2 -> 3. `force`: alles Raeumbare raeumen
        (nach einem Ueberlauf oder wenn das Denken den Platz verbraucht hat)."""
        view = self.with_summary()
        budget = self.budget_fn(view, tools)
        if budget.fits and not force and focus is None:
            return Prepared(view, budget)
        out, budget, ids, before, after = self.stage1(view, tools, force=force)
        await self._report_stage1(ids, before, after)
        if budget.fits and not force and focus is None:
            return Prepared(out, budget)
        deficit = budget.E + budget.A - (budget.W_free - budget.R)
        second = await self.stage2(tools, force=force, focus=focus, deficit=deficit)
        if second is not None:
            out, budget = second
        if not budget.fits:
            out, budget, ids, before, after = self.stage1(
                out, tools, force=False, include_round=True
            )
            await self._report_stage1(ids, before, after)
        if budget.fits:
            return Prepared(out, budget)
        raise InputTooLarge(
            budget, "Der Auftrag passt auch nach dem Verdichten nicht in das Kontextfenster"
        )
