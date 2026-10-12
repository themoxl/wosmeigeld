"""KI-Spaltenvorschlag ueber den Gateway des KI-Kerns (Etappe 1b).

Was frueher im Code stand und jetzt nicht mehr: ein eigenes Denkbudget
(6000 + 500 je Spalte, Decke 12000), eine Eskalation nach abgeschnittener
Antwort, ein Formatierer-Zweitaufruf mit ausgeschaltetem Denken und 1500
Token, eine Kuerzung des Denk-Memos und eine Hoechstzahl von 40 Spalten.
Diese Tests halten fest, was stattdessen gilt: Antwortbudget aus dem geladenen
Fenster, Reasoning nach der Haushaltseinstellung, genau ein Aufruf, JSON aus
dem Denktext derselben Antwort, und benannte Gruende, wenn etwas nicht passt.

Ersetzt wird nur der Modellserver (httpx.AsyncClient.get/post); Gateway,
Budgetrechnung und llm_calls laufen echt gegen die lokale Datenbank. Die
Grundfaelle (Vorschlag bleibt Vorschlag, Maskierung, Ausfall ohne Server)
stehen in tests/test_imports_phase3.py.
"""

import json
from types import SimpleNamespace
from typing import Any, get_args
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import text

from app.core.db import get_session_factory
from app.llm import reasons
from app.llm.answers import Ready
from app.llm.budget import compute
from app.llm.discovery import Target
from app.llm.window import ReasoningCapability, WindowInfo
from app.schemas.import_platform import MappingSuggestionReason
from app.services import ai_settings_service, import_mapping_ai
from tests import test_imports_phase3 as phase3

client = phase3.client
household_headers = phase3.household_headers
account_id = phase3.account_id

_RESERVE = 0.25
_ZEICHEN_JE_TOKEN = 3.0
_SCHAETZABSTAND = 0.15

_ANTWORT = json.loads(phase3._MAPPING_ANSWER)


async def _sql(query: str, params: dict[str, Any]) -> Any:
    session_factory = get_session_factory()
    async with session_factory() as session:
        ergebnis = await session.execute(text(query), params)
        await session.commit()
        return ergebnis


async def _einstellungen(headers: dict[str, str], **werte: Any) -> None:
    werte = {
        "answer_reserve_share": _RESERVE,
        "start_chars_per_token": _ZEICHEN_JE_TOKEN,
        "start_estimate_error": _SCHAETZABSTAND,
        "reasoning": None,
        **werte,
    }
    zuweisung = ", ".join(f"{name} = :{name}" for name in werte)
    await _sql(
        f"update ai_household_settings set {zuweisung} where household_id = cast(:h as uuid)",
        {**werte, "h": headers["X-Household-Id"]},
    )


async def _protokoll(headers: dict[str, str]) -> list[Any]:
    return list(
        (
            await _sql(
                "select status, reason_code, error_message, max_tokens_sent, reasoning_sent "
                "from llm_calls where household_id = cast(:h as uuid) and stage = :stage",
                {"h": headers["X-Household-Id"], "stage": import_mapping_ai.LLM_CALL_STAGE},
            )
        ).all()
    )


def _server(
    monkeypatch: pytest.MonkeyPatch, *antworten: httpx.Response, fenster: int = 32768
) -> tuple[AsyncMock, AsyncMock]:
    """LM Studio mit GELADENEM Fenster (/api/v0/models) und festen Antworten."""

    async def get(url: str, *args: Any, **kwargs: Any) -> MagicMock:
        response = MagicMock()
        if url.endswith("/api/v0/models"):
            response.status_code = 200
            response.json.return_value = {
                "data": [
                    {
                        "id": "fake-mapping-model",
                        "type": "llm",
                        "state": "loaded",
                        "loaded_context_length": fenster,
                    }
                ]
            }
        else:
            response.status_code = 404
            response.json.return_value = {}
        return response

    lesen = AsyncMock(side_effect=get)
    senden = AsyncMock(side_effect=list(antworten))
    monkeypatch.setattr(httpx.AsyncClient, "get", lesen)
    monkeypatch.setattr(httpx.AsyncClient, "post", senden)
    return lesen, senden


def _antwort(
    inhalt: str = "", *, finish: str = "stop", denktext: str | None = None
) -> httpx.Response:
    nachricht: dict[str, Any] = {"content": inhalt}
    if denktext is not None:
        nachricht["reasoning_content"] = denktext
    return httpx.Response(200, json={"choices": [{"message": nachricht, "finish_reason": finish}]})


def _vorschlag(headers: dict[str, str], run_id: str) -> dict[str, Any]:
    res = client.post(f"/v1/imports/{run_id}/mapping-suggestion", json={}, headers=headers)
    assert res.status_code == 200, res.text
    ergebnis: dict[str, Any] = res.json()
    return ergebnis


def _breite_datei(spalten: int) -> bytes:
    kopf = ["Buchungstag", "Betrag", "Empfaenger", "Verwendungszweck"] + [
        f"Zusatzspalte {nummer}" for nummer in range(1, spalten - 3)
    ]
    zeile = ["02.01.2026", "-25,99", "Haendler EINS", "Rechnung 4711"] + [
        "x" for _ in range(1, spalten - 3)
    ]
    return (";".join(kopf) + "\n" + ";".join(zeile) + "\n").encode()


async def _hochladen(
    headers: dict[str, str], konto: str, inhalt: bytes = phase3._MAPPING_CSV
) -> str:
    """Hochladen und Verarbeitung abwarten - aus einem async-Test heraus."""
    return await phase3._upload_from_async_test(
        headers, "mapping.csv", content=inhalt, account_id=konto
    )


def test_mapping_reasons_cover_every_core_reason() -> None:
    """Jeder benannte Grund des KI-Kerns muss im Antwortvertrag vorkommen -
    sonst wuerde ein neuer Kerngrund zur Validierungsfehlermeldung."""
    assert set(reasons.REASONS) <= set(get_args(MappingSuggestionReason))


async def test_the_whole_free_window_is_the_budget_and_no_reasoning_is_forced(
    household_headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await _hochladen(household_headers, account_id)
    await _einstellungen(household_headers)
    _lesen, senden = _server(monkeypatch, _antwort(phase3._MAPPING_ANSWER))

    daten = _vorschlag(household_headers, run_id)
    assert daten["available"] is True and daten["reason"] == "ok"

    # Genau ein Aufruf - kein Vorabtest, kein Formatierer.
    [aufruf] = senden.await_args_list
    koerper = aufruf.kwargs["json"]
    erwartet = compute(
        window=32768,
        messages=koerper["messages"],
        chars_per_token=_ZEICHEN_JE_TOKEN,
        estimate_error=_SCHAETZABSTAND,
        reserve_share=_RESERVE,
    )
    # Frueher hoechstens 12000 (Denkbudget) bzw. die Haushaltszahl - jetzt der
    # ganze freie Platz im geladenen Fenster.
    assert koerper["max_tokens"] == erwartet.max_tokens > 12_000
    assert "reasoning_effort" not in koerper
    assert "genau EINMAL" not in koerper["messages"][0]["content"]
    [zeile] = await _protokoll(household_headers)
    assert zeile.status == "success" and zeile.max_tokens_sent == erwartet.max_tokens


async def test_the_household_reasoning_setting_is_sent_unchanged(
    household_headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein eigenes Denkbudget und kein erzwungenes reasoning=off - die
    Einstellung des Haushalts gilt, auch hier."""
    run_id = await _hochladen(household_headers, account_id)
    await _einstellungen(household_headers, reasoning="on")
    _lesen, senden = _server(monkeypatch, _antwort(phase3._MAPPING_ANSWER))

    assert _vorschlag(household_headers, run_id)["available"] is True
    [aufruf] = senden.await_args_list
    koerper = aufruf.kwargs["json"]
    assert koerper["reasoning_effort"] == "high"
    # Bei aktivem Denken steht die Anti-Nochmal-pruefen-Regel im Prompt.
    assert "genau EINMAL" in koerper["messages"][0]["content"]


async def test_a_truncated_answer_is_budget_exhausted_without_escalation_or_formatter(
    household_headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await _hochladen(household_headers, account_id)
    await _einstellungen(household_headers, reasoning="on")
    _lesen, senden = _server(
        monkeypatch,
        _antwort(
            '{"booked_date_column": "Buchungstag", "amount_col',
            finish="length",
            denktext="Spalte Buchungstag ist das Datum, Betrag ist ... ich pruefe noch",
        ),
    )

    daten = _vorschlag(household_headers, run_id)
    assert daten["available"] is False
    assert daten["reason"] == reasons.MODEL_BUDGET_EXHAUSTED
    assert "Antwortbudget" in daten["message"]
    # Ein Aufruf. Frueher folgten Eskalation und Formatierer mit reasoning=off.
    assert senden.await_count == 1
    [zeile] = await _protokoll(household_headers)
    assert (zeile.status, zeile.reason_code) == ("error", reasons.MODEL_BUDGET_EXHAUSTED)


async def test_json_only_in_the_reasoning_text_is_taken_from_the_same_answer(
    household_headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await _hochladen(household_headers, account_id)
    await _einstellungen(household_headers)
    entwurf = dict(_ANTWORT, amount_column="Empfaenger")
    denktext = (
        f"Erster Entwurf: {json.dumps(entwurf)} - nein, Betrag ist die Betragsspalte. "
        f"Endgueltig: {phase3._MAPPING_ANSWER}"
    )
    _lesen, senden = _server(monkeypatch, _antwort("", denktext=denktext))

    daten = _vorschlag(household_headers, run_id)
    assert daten["available"] is True
    # Das zuletzt geschriebene Objekt gilt, nicht der Entwurf.
    assert daten["column_mapping"]["amount"] == "Betrag"
    assert senden.await_count == 1
    [zeile] = await _protokoll(household_headers)
    assert zeile.status == "success" and "Denktext" in zeile.error_message


async def test_many_columns_are_not_cut(
    household_headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frueher gingen hoechstens 40 Spalten an das Modell - eine Datumsspalte an
    Position 41 waere still verschwunden."""
    run_id = await _hochladen(household_headers, account_id, _breite_datei(45))
    await _einstellungen(household_headers)
    _lesen, senden = _server(monkeypatch, _antwort(phase3._MAPPING_ANSWER))

    daten = _vorschlag(household_headers, run_id)
    assert len(daten["source_columns"]) == 45
    [aufruf] = senden.await_args_list
    schema = aufruf.kwargs["json"]["response_format"]["json_schema"]["schema"]
    assert len(schema["properties"]["booked_date_column"]["enum"]) == 46  # + "" fuer keine


async def test_columns_that_do_not_fit_the_window_are_named_not_cut(
    household_headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await _hochladen(household_headers, account_id, _breite_datei(45))
    await _einstellungen(household_headers)
    _lesen, senden = _server(monkeypatch, fenster=512)

    daten = _vorschlag(household_headers, run_id)
    assert daten["available"] is False
    assert daten["reason"] == reasons.MODEL_INPUT_TOO_LARGE
    assert "Fenster 512 Token" in daten["message"]
    assert len(daten["source_columns"]) == 45
    senden.assert_not_awaited()
    [zeile] = await _protokoll(household_headers)
    assert (zeile.status, zeile.reason_code) == ("refused", reasons.MODEL_INPUT_TOO_LARGE)


async def test_disabled_ai_reaches_no_server(
    household_headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await _hochladen(household_headers, account_id)
    await _einstellungen(household_headers, is_enabled=False)
    lesen, senden = _server(monkeypatch)

    daten = _vorschlag(household_headers, run_id)
    assert daten["available"] is False
    assert daten["reason"] == reasons.AI_DISABLED
    assert daten["source_columns"]
    lesen.assert_not_awaited()
    senden.assert_not_awaited()
    [zeile] = await _protokoll(household_headers)
    assert (zeile.status, zeile.reason_code) == ("refused", reasons.AI_DISABLED)


def _bereit(reasoning: str | None, kind: str) -> Ready:
    settings = ai_settings_service.ResolvedAISettings(
        is_enabled=True,
        min_confidence=0.85,
        custom_base_url=None,
        model_name=None,
        temperature=1.0,
        request_timeout_seconds=30.0,
        reasoning=reasoning,
    )
    target = Target(
        base_url="http://lm.test/v1",
        model="m",
        window=WindowInfo(
            loaded=8192,
            cap=None,
            effective=8192,
            source="/api/v1/models",
            reasoning_capability=ReasoningCapability(kind=kind),  # type: ignore[arg-type]
        ),
    )
    return Ready(settings, target, SimpleNamespace())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "reasoning,kind,active",
    [
        (None, "unknown", False),
        (None, "always", True),
        ("on", "switchable", True),
        ("high", "unknown", True),
        ("off", "switchable", False),
        ("on", "none", False),
    ],
)
def test_the_reasoning_prompt_rule_follows_setting_and_model(
    reasoning: str | None, kind: str, active: bool
) -> None:
    """Nur Prompttext: die Anti-Nochmal-pruefen-Regel steht genau dann im
    Prompt, wenn das Modell bei diesem Aufruf denkt."""
    assert import_mapping_ai.reasoning_active(_bereit(reasoning, kind)) is active


def test_a_mapping_answer_is_found_after_other_objects() -> None:
    text_ = 'Beispielzeile {"Buchungstag": "02.01.2026"} dann ' + phase3._MAPPING_ANSWER
    assert import_mapping_ai.parse_mapping_content(text_) == _ANTWORT
    assert import_mapping_ai.parse_mapping_content('{"Buchungstag": "x"}') is None
    assert import_mapping_ai.parse_mapping_content("kein JSON") is None
