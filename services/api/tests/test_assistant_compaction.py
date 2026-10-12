"""Verdichtung des KI-Felds (app/assistant/compaction.py) - ohne Netz und DB.

Stufe 1: alte Werkzeugergebnisse werden zu Platzhaltern (aeltestes zuerst,
die laufende Runde bleibt), Stufe 2: Zusammenfassung in 7 Abschnitten durch
das Modell (Binaersuche fuer Abschnitte, Zahlenpruefung), Stufe 3: laut
scheitern mit Aufschluesselung.
"""

import uuid
from typing import Any

import pytest

from app.assistant import prompts
from app.assistant.compaction import Compactor, InputTooLarge
from app.llm.budget import Budget, compute

Message = dict[str, Any]
CPT = 3.0


def _budget_fn(window: int):  # type: ignore[no-untyped-def]
    def fn(messages: list[Message], tools: list[dict[str, Any]] | None) -> Budget:
        return compute(
            window=window,
            messages=messages,
            tools=tools,
            chars_per_token=CPT,
            estimate_error=0.0,
            reserve_share=0.25,
        )

    return fn


class Model:
    """Gefaelschter Zusammenfassungsaufruf: merkt sich die Auftraege."""

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = answers or []
        self.requests: list[tuple[list[Message], str]] = []

    async def __call__(self, messages: list[Message], step: str) -> str | None:
        self.requests.append((messages, step))
        return self.answers.pop(0) if self.answers else "1. Auftrag: Zusammenfassung."


class Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str, dict[str, Any] | None]] = []

    async def __call__(
        self, kind: str, level: str, message: str, details: dict[str, Any] | None
    ) -> None:
        self.items.append((kind, level, message, details))


def _history(
    tool_chars: int = 3000, rounds: int = 2, system_chars: int = 0
) -> tuple[list[Message], Compactor, Model]:
    """Systemnachricht, `rounds` fruehere Fragen mit je einer Werkzeugrunde,
    dann die aktuelle Frage."""
    transcript: list[Message] = [{"role": "system", "content": "System." + "s" * system_chars}]
    keys: dict[int, str] = {}
    ids: dict[int, uuid.UUID] = {}
    for index in range(1, rounds + 1):
        question = {"role": "user", "content": f"Frage {index}?"}
        call = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"c{index}",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        }
        result = {
            "role": "tool",
            "tool_call_id": f"c{index}",
            "content": f"W{index} " + "x" * tool_chars,
        }
        answer = {"role": "assistant", "content": f"Antwort {index}: 412,30 €."}
        transcript += [question, call, result, answer]
        keys[id(result)] = f"W{index}"
        ids[id(question)] = uuid.uuid4()
        ids[id(answer)] = uuid.uuid4()
    current = {"role": "user", "content": "Und jetzt?"}
    transcript.append(current)
    ids[id(current)] = uuid.uuid4()
    model = Model()
    compactor = Compactor(
        transcript=transcript,
        budget_fn=_budget_fn(4000),
        summarize_call=model,
        event=Events(),
        chars_per_token=lambda: CPT,
        keys=keys,
        message_ids=ids,
        question=current,
        tool_texts={f"W{index}": "x" for index in range(1, rounds + 1)},
    )
    return transcript, compactor, model


async def test_a_fitting_request_is_left_alone() -> None:
    _transcript, compactor, model = _history(tool_chars=100)
    prepared = await compactor.prepare(None)
    assert prepared.messages == compactor.transcript
    assert model.requests == []


async def test_stage1_replaces_the_oldest_results_first() -> None:
    transcript, compactor, model = _history(tool_chars=4200)
    compactor.budget_fn = _budget_fn(3000)
    prepared = await compactor.prepare(None)
    assert prepared.budget.fits
    contents = [m["content"] for m in prepared.messages if m["role"] == "tool"]
    assert contents[0].startswith("[Verdichtet · Ergebnis W1 · 4.203 Zeichen")
    assert 'reopen_result({"id":"W1"})' in contents[0]
    assert contents[1].startswith("W2 ")  # juengeres bleibt, es passt schon
    assert model.requests == []
    events = compactor.event.items  # type: ignore[attr-defined]
    assert events[0][0] == "compacted" and events[0][3]["ids"] == ["W1"]
    # Die Originale bleiben unveraendert im Verlauf.
    assert transcript[3]["content"].startswith("W1 x")


async def test_stage1_keeps_the_running_round() -> None:
    transcript, compactor, _model = _history(tool_chars=200, rounds=1)
    call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "r1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
        ],
    }
    fresh = {"role": "tool", "tool_call_id": "r1", "content": "W9 " + "y" * 5000}
    transcript += [call, fresh]
    compactor.keys[id(fresh)] = "W9"
    out, _budget, ids, _before, _after = compactor.stage1(
        compactor.with_summary(), None, force=True
    )
    assert ids == ["W1"]
    assert out[-1] is fresh
    # Letzte Moeglichkeit: auch die laufende Runde - bis auf das juengste.
    out, _budget, ids, _before, _after = compactor.stage1(out, None, force=True, include_round=True)
    assert out[-1] is fresh


async def test_stage2_summarizes_up_to_the_question_in_seven_sections() -> None:
    # Das System ist gross (gehoert nie in die Zusammenfassung), der Verlauf
    # passt allein in einen Zusammenfassungsaufruf, beides zusammen nicht.
    transcript, compactor, model = _history(tool_chars=200, rounds=3, system_chars=3000)
    for message in transcript:
        if message["role"] == "assistant" and not message.get("tool_calls"):
            message["content"] += " " + "Erklärung " * 120
    compactor.budget_fn = _budget_fn(3000)
    model.answers = ["1. Auftrag\n2. Frage 1? Frage 2? Frage 3?\n4. 412,30 € [W1]"]
    prepared = await compactor.prepare(None)
    assert prepared.budget.fits
    instruction = model.requests[0][0][0]["content"]
    assert instruction == prompts.SUMMARY_INSTRUCTION
    assert "7. Offenes und nächster Schritt" in instruction
    body = model.requests[0][0][1]["content"]
    assert "[Nutzer] Frage 1?" in body and "[Ergebnis W1]" in body
    assert "Und jetzt?" not in body
    summary = compactor.chat_summary
    assert summary is not None and summary.nr == 1
    # bis zur Antwort vor der aktuellen Frage
    assert summary.covers is transcript[-2]
    assert summary.message_id == compactor.message_ids[id(transcript[-2])]
    assert summary.tokens_before and summary.tokens_after
    assert summary.tokens_before > summary.tokens_after
    assert summary.unverified == 0
    # Die Zusammenfassung reist in der aktuellen Frage mit.
    assert [m["role"] for m in prepared.messages] == ["system", "user"]
    assert prepared.messages[1]["content"].startswith("Zusammenfassung des bisherigen Verlaufs")
    assert prepared.messages[1]["content"].endswith("---\n\nUnd jetzt?")
    kinds = [item[0] for item in compactor.event.items]  # type: ignore[attr-defined]
    assert "summarized" in kinds


async def test_a_long_history_is_summarized_in_sections() -> None:
    transcript, compactor, model = _history(tool_chars=200, rounds=6)
    for message in transcript:
        if message["role"] == "assistant" and not message.get("tool_calls"):
            message["content"] += " " + "lang " * 400
    compactor.budget_fn = _budget_fn(5000)
    await compactor.prepare(None)
    steps = [step for _messages, step in model.requests]
    assert steps[0] == "Verdichtung" and steps[1] == "Verdichtung, Abschnitt 2"
    # Jeder weitere Abschnitt baut auf der bisherigen Zusammenfassung auf.
    assert model.requests[1][0][1]["content"].startswith("Bisherige Zusammenfassung:")


async def test_numbers_of_a_summary_are_checked() -> None:
    _transcript, compactor, model = _history(tool_chars=200, rounds=3, system_chars=3000)
    for message in compactor.transcript:
        if message["role"] == "assistant" and not message.get("tool_calls"):
            message["content"] += " " + "Erklärung " * 120
    compactor.budget_fn = _budget_fn(3000)
    model.answers = ["4. Zahlen: 412,30 € [W1] und erfundene 9.999,99 €"]
    await compactor.prepare(None)
    assert compactor.chat_summary is not None
    assert compactor.chat_summary.unverified == 1
    level = next(
        item[1] for item in compactor.event.items if item[0] == "summarized"  # type: ignore[attr-defined]
    )
    assert level == "warnung"


async def test_stage3_fails_loudly_with_the_parts() -> None:
    _transcript, compactor, _model = _history(tool_chars=100, rounds=1)
    compactor.transcript[-1]["content"] = "riesig " * 3000
    with pytest.raises(InputTooLarge) as failed:
        await compactor.prepare(None)
    text = str(failed.value)
    assert "aktuelle Frage" in text and "Antwortreserve" in text
    assert failed.value.budget.parts.current > 0


async def test_manual_compaction_with_focus_rebuilds_an_existing_summary() -> None:
    _transcript, compactor, model = _history(tool_chars=100, rounds=2)
    await compactor.stage2(None, force=False, focus="Fixkosten", deficit=0)
    first = compactor.chat_summary
    assert first is not None and first.nr == 1
    assert "Worauf besonders zu achten ist: Fixkosten" in model.requests[0][0][0]["content"]
    # Nichts Neues seitdem: mit Fokus wird die bisherige neu gefasst.
    await compactor.stage2(None, force=False, focus="Versicherungen", deficit=0)
    assert compactor.chat_summary is not None and compactor.chat_summary.nr == 2
    second_body = model.requests[1][0][1]["content"]
    assert second_body.startswith("Bisherige Zusammenfassung:")
    assert prompts.NOTHING_NEW in second_body
