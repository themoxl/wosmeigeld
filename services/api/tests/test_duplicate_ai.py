"""KI-Einschaetzung zu Dublettenkandidaten - ohne echtes Modell.

Geprueft werden die Zusagen des Moduls, nicht die Qualitaet eines Modells:

* Sie wird NIE angewendet - import_rows bleiben unveraendert.
* Jeder Fehlschlag ist ein geordneter Ausgang (available=False) mit dem
  benannten Grund des KI-Kerns, kein Fehler der Review-Inbox.
* Was das Modell antwortet, wird streng geprueft: ein unbekanntes Urteil darf
  nicht als Entscheidung durchgehen.
* Der Aufruf laeuft ueber den Gateway: Schema mit {name, strict, schema}-Huelle,
  Antwortbudget aus dem Fenster, Paare in Stapeln nach Passung statt einer
  festen Hoechstzahl, keine gekuerzten Texte, kein Zusatzaufruf.

Ersetzt werden nur die Zielsuche (llm.resolve_target) und der HTTP-Aufruf an
den Modellserver; Gateway, Budgetrechnung und Protokoll laufen echt gegen die
lokale Datenbank. Ein Test, der ein laufendes LM Studio braucht, prueft die
Verfuegbarkeit eines fremden Dienstes und nicht diesen Code.
"""

import json
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.sql import text

from app import llm
from app.core.db import get_session_factory
from app.llm import reasons
from app.llm.budget import compute
from app.llm.discovery import Target, TargetResult
from app.llm.discovery import resolve_target as _echte_suche
from app.llm.window import WindowInfo
from app.main import app
from app.services import duplicate_ai_service
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

# Startwerte der Budgetrechnung, wie sie die Tests in die Einstellungen
# schreiben - damit die Fenstergroessen unten nicht an Schemavorgaben haengen.
_RESERVE = 0.25
_ZEICHEN_JE_TOKEN = 3.0
_SCHAETZABSTAND = 0.15


@pytest.fixture()
def kopf() -> dict[str, str]:
    token = _signup(f"dupai-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    haushalt = client.post(
        "/v1/households", json={"name": "KI-Dublettenhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": haushalt["id"]}


async def _sql(query: str, params: dict[str, Any]) -> Any:
    session_factory = get_session_factory()
    async with session_factory() as session:
        ergebnis = await session.execute(text(query), params)
        await session.commit()
        return ergebnis


async def _einstellungen(kopf: dict[str, str], **werte: Any) -> None:
    """Budgetwerte fest setzen (und z. B. die KI ausschalten)."""
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
        {**werte, "h": kopf["X-Household-Id"]},
    )


async def _kandidat(kopf: dict[str, str], zweck: str = "PP.1234.PP Musterdienst") -> str:
    """Eine offene Dublettenzeile samt Gegenbuchung."""
    haushalt = kopf["X-Household-Id"]
    konto = client.post(
        "/v1/accounts", json={"name": "Giro", "account_type": "checking"}, headers=kopf
    ).json()["id"]
    buchung = client.post(
        "/v1/transactions",
        json={
            "account_id": konto,
            "booked_date": "2026-06-10",
            "amount_minor": -450,
            "payee_label": "PayPal Europe S.a.r.l.",
        },
        headers=kopf,
    ).json()["id"]
    lauf = (
        await _sql(
            "insert into import_runs (household_id, account_id, filename, file_hash, "
            " file_size_bytes, status) values (cast(:h as uuid), cast(:k as uuid), "
            " 'ki.csv', :hash, 1, 'ready') returning id",
            {"h": haushalt, "k": konto, "hash": uuid.uuid4().hex},
        )
    ).scalar_one()
    zeile = (
        await _sql(
            "insert into import_rows (household_id, import_run_id, row_number, raw_data, "
            " booked_date, amount_minor, currency, payee_raw, purpose, status, "
            " duplicate_of_transaction_id) values (cast(:h as uuid), cast(:l as uuid), 1, "
            " '{}'::jsonb, '2026-06-10', -450, 'EUR', 'PAYPAL', :zweck, "
            " 'duplicate_possible', cast(:tx as uuid)) returning id",
            {"h": haushalt, "l": str(lauf), "tx": buchung, "zweck": zweck},
        )
    ).scalar_one()
    return str(zeile)


def _ziel(fenster: int = 32768) -> TargetResult:
    target = Target(
        base_url="http://lm.test:1234/v1",
        model="test-modell",
        window=WindowInfo(
            loaded=fenster, cap=None, effective=fenster, source="/api/v1/models"
        ),
    )
    return TargetResult(target=target, base_url=target.base_url, models=[], detailed=True)


def _antwort(
    inhalt: str = "", *, finish: str = "stop", denktext: str | None = None
) -> httpx.Response:
    nachricht: dict[str, Any] = {"content": inhalt}
    if denktext is not None:
        nachricht["reasoning_content"] = denktext
    return httpx.Response(
        200, json={"choices": [{"message": nachricht, "finish_reason": finish}]}
    )


def _urteile(*urteile: tuple[str, str]) -> str:
    return json.dumps(
        {
            "urteile": [
                {"nummer": nummer, "urteil": urteil, "begruendung": grund, "konfidenz": 0.9}
                for nummer, (urteil, grund) in enumerate(urteile, start=1)
            ]
        }
    )


def _modell(
    monkeypatch: pytest.MonkeyPatch,
    *antworten: httpx.Response | Exception,
    ziel: TargetResult | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    """Zielsuche und Modellserver ersetzen; zurueck kommen beide Attrappen.

    Die Zielsuche haelt den Vertrag des Originals ein: bei ausgeschalteter KI
    antwortet llm.resolve_target selbst mit ai_disabled, ohne Serverkontakt."""
    gefunden = ziel or _ziel()

    async def zielsuche(settings: Any, *, client: Any = None) -> TargetResult:
        if not settings.is_enabled:
            return await _echte_suche(settings)
        return gefunden

    suche = AsyncMock(side_effect=zielsuche)
    senden = AsyncMock(side_effect=list(antworten))
    monkeypatch.setattr(llm, "resolve_target", suche)
    monkeypatch.setattr(httpx.AsyncClient, "post", senden)
    return suche, senden


def _gesendet(senden: AsyncMock) -> list[dict[str, Any]]:
    return [aufruf.kwargs["json"] for aufruf in senden.await_args_list]


async def _protokoll(kopf: dict[str, str]) -> list[Any]:
    return list(
        (
            await _sql(
                "select stage, prompt_version, status, reason_code, error_message, "
                "max_tokens_sent, window_tokens from llm_calls "
                "where household_id = cast(:h as uuid) and stage = :stage",
                {"h": kopf["X-Household-Id"], "stage": duplicate_ai_service.LLM_CALL_STAGE},
            )
        ).all()
    )


def _bewerten(kopf: dict[str, str], *zeilen: str) -> dict[str, Any]:
    antwort = client.post(
        "/v1/review/duplicates/assess", json={"import_row_ids": list(zeilen)}, headers=kopf
    )
    assert antwort.status_code == 200, antwort.text
    ergebnis: dict[str, Any] = antwort.json()
    return ergebnis


def _kleinstes_fenster(nachrichten: list[dict[str, Any]]) -> int:
    """Kleinstes Fenster, in das diese Nachrichten samt Antwortreserve passen."""
    unten, oben = 1, 10_000_000
    while unten < oben:
        mitte = (unten + oben) // 2
        passt = compute(
            window=mitte,
            messages=nachrichten,
            chars_per_token=_ZEICHEN_JE_TOKEN,
            estimate_error=_SCHAETZABSTAND,
            reserve_share=_RESERVE,
        ).fits
        if passt:
            oben = mitte
        else:
            unten = mitte + 1
    return unten


def _paar(zweck: str) -> dict[str, Any]:
    """Ein Paar so, wie duplicate_ai_service es aus _kandidat() liest."""
    return {
        "import_row_id": uuid.uuid4(),
        "a": {
            "payee": "PAYPAL",
            "purpose": zweck,
            "booked_date": date(2026, 6, 10),
            "amount_minor": -450,
        },
        "b": {"payee": "PayPal Europe S.a.r.l.", "purpose": ""},
    }


async def test_the_assessment_is_returned_and_never_applied(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    zeile = await _kandidat(kopf)
    await _einstellungen(kopf)
    _modell(
        monkeypatch,
        _antwort(_urteile(("same", "Dieselbe Referenz PP.1234.PP in beiden Zeilen."))),
    )

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["available"] is True
    assert ergebnis["reason"] == "ok"
    assert ergebnis["prompt_version"] == duplicate_ai_service.DUPLICATE_PROMPT_VERSION
    assert ergebnis["assessments"] == [
        {
            "import_row_id": zeile,
            "verdict": "same",
            "reason": "Dieselbe Referenz PP.1234.PP in beiden Zeilen.",
            "confidence": 0.9,
        }
    ]

    # Entscheidend: die Zeile ist unveraendert offen. Die KI schlaegt vor, sie
    # entscheidet nicht.
    status = (
        await _sql(
            "select status from import_rows where id = cast(:id as uuid)", {"id": zeile}
        )
    ).scalar_one()
    assert status == "duplicate_possible"

    # Genau eine Protokollzeile - vom Gateway, mit Budgetzeile.
    [zeile_protokoll] = await _protokoll(kopf)
    assert zeile_protokoll.stage == duplicate_ai_service.LLM_CALL_STAGE
    assert zeile_protokoll.prompt_version == "dup-v2"
    assert zeile_protokoll.status == "success"
    assert zeile_protokoll.window_tokens == 32768
    assert zeile_protokoll.max_tokens_sent > 0


async def test_the_request_carries_the_strict_schema_envelope_and_no_fixed_budget(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund: v1 schickte das nackte Schema als json_schema - ohne
    {name, strict, schema}-Huelle griff das strenge Schema nie."""
    zeile = await _kandidat(kopf)
    await _einstellungen(kopf)
    _suche, senden = _modell(monkeypatch, _antwort(_urteile(("different", "Andere Referenz."))))

    _bewerten(kopf, zeile)

    [koerper] = _gesendet(senden)
    format_ = koerper["response_format"]
    assert format_["type"] == "json_schema"
    assert set(format_["json_schema"]) == {"name", "strict", "schema"}
    assert format_["json_schema"]["name"] == "duplicate_assessment"
    assert format_["json_schema"]["strict"] is True
    schema = format_["json_schema"]["schema"]
    assert schema == duplicate_ai_service.build_json_schema(1)
    assert schema["properties"]["urteile"]["minItems"] == 1

    # Antwortbudget = der ganze freie Platz im Fenster (W - E - A), keine Zahl
    # aus dem Code; ohne Reasoning-Einstellung wird nichts gesendet.
    erwartet = compute(
        window=32768,
        messages=koerper["messages"],
        chars_per_token=_ZEICHEN_JE_TOKEN,
        estimate_error=_SCHAETZABSTAND,
        reserve_share=_RESERVE,
    )
    assert koerper["max_tokens"] == erwartet.max_tokens
    assert "reasoning_effort" not in koerper


async def test_an_unreachable_model_is_an_orderly_exit(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    zeile = await _kandidat(kopf)
    _suche, senden = _modell(
        monkeypatch,
        ziel=TargetResult(
            target=None,
            base_url=None,
            models=[],
            detailed=False,
            reason_code=reasons.MODEL_UNAVAILABLE,
            message=reasons.describe(reasons.MODEL_UNAVAILABLE, "Keine Adresse antwortet."),
        ),
    )

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["available"] is False
    assert ergebnis["reason"] == reasons.MODEL_UNAVAILABLE
    assert "Keine Adresse antwortet." in ergebnis["message"]
    assert ergebnis["assessments"] == []
    senden.assert_not_awaited()
    [zeile_protokoll] = await _protokoll(kopf)
    assert (zeile_protokoll.status, zeile_protokoll.reason_code) == (
        "no_model",
        reasons.MODEL_UNAVAILABLE,
    )


async def test_disabled_ai_never_reaches_a_server(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    zeile = await _kandidat(kopf)
    await _einstellungen(kopf, is_enabled=False)
    suche, senden = _modell(monkeypatch)

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["available"] is False
    assert ergebnis["reason"] == reasons.AI_DISABLED
    # Die Zielsuche antwortet bei ausgeschalteter KI selbst, ohne Server.
    [aufruf] = suche.await_args_list
    assert aufruf.args[0].is_enabled is False
    senden.assert_not_awaited()
    [zeile_protokoll] = await _protokoll(kopf)
    assert (zeile_protokoll.status, zeile_protokoll.reason_code) == (
        "refused",
        reasons.AI_DISABLED,
    )


async def test_an_unparsable_answer_is_an_orderly_exit(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    zeile = await _kandidat(kopf)
    await _einstellungen(kopf)
    _modell(monkeypatch, _antwort("Klar, das sind Dubletten! (kein JSON)"))

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["available"] is False
    assert ergebnis["reason"] == reasons.MODEL_OUTPUT_UNUSABLE
    # Die Protokollzeile des Gateways traegt den fachlichen Befund.
    [zeile_protokoll] = await _protokoll(kopf)
    assert (zeile_protokoll.status, zeile_protokoll.reason_code) == (
        "error",
        reasons.MODEL_OUTPUT_UNUSABLE,
    )


async def test_a_truncated_answer_is_a_named_budget_failure_without_escalation(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    zeile = await _kandidat(kopf)
    await _einstellungen(kopf)
    _suche, senden = _modell(
        monkeypatch, _antwort('{"urteile":[{"nummer":1,"urteil":"sa', finish="length")
    )

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["available"] is False
    assert ergebnis["reason"] == reasons.MODEL_BUDGET_EXHAUSTED
    # Kein zweiter Versuch mit groesserem Budget oder ohne Denken (E1).
    assert senden.await_count == 1


async def test_json_only_in_the_reasoning_text_is_rescued_without_a_second_call(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    zeile = await _kandidat(kopf)
    await _einstellungen(kopf)
    denktext = (
        'Erst dachte ich {"urteile":[{"nummer":1,"urteil":"unsure"}]}, aber endgueltig: '
        + _urteile(("same", "Gleiche Referenz."))
    )
    _suche, senden = _modell(monkeypatch, _antwort("", denktext=denktext))

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["available"] is True
    assert ergebnis["assessments"][0]["verdict"] == "same"
    assert senden.await_count == 1
    [zeile_protokoll] = await _protokoll(kopf)
    assert zeile_protokoll.status == "success"
    assert "Denktext" in zeile_protokoll.error_message


async def test_an_unknown_verdict_never_counts_as_a_decision(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Modell, das sich einen Wert ausdenkt, darf nicht als 'same'
    durchgehen - das liesse eine echte Buchung verschwinden."""
    zeile = await _kandidat(kopf)
    await _einstellungen(kopf)
    _modell(
        monkeypatch,
        _antwort(
            '{"urteile":[{"nummer":1,"urteil":"vielleicht_schon",'
            '"begruendung":"hm","konfidenz":2.5}]}'
        ),
    )

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["assessments"][0]["verdict"] == "unsure"
    # Und die Konfidenz bleibt im gueltigen Bereich.
    assert ergebnis["assessments"][0]["confidence"] == 1.0


async def test_a_missing_judgement_does_not_invent_one(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Antwortet das Modell zu einem Paar gar nicht, wird das gesagt - statt
    stillschweigend 'different' anzunehmen."""
    zeile = await _kandidat(kopf)
    await _einstellungen(kopf)
    _modell(monkeypatch, _antwort('{"urteile":[]}'))

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["assessments"][0]["verdict"] == "unsure"
    assert "nichts geantwortet" in ergebnis["assessments"][0]["reason"]


async def test_pairs_go_in_batches_by_fit_and_are_never_cut(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Statt hoechstens 25 Paare und 200 Zeichen je Feld: so viele Paare je
    Aufruf, wie ins geladene Fenster passen - und jeder Text vollstaendig."""
    zweck = "Rechnung 4711 Musterdienst " + "Z" * 1500
    erste = await _kandidat(kopf, zweck)
    zweite = await _kandidat(kopf, zweck)
    await _einstellungen(kopf)
    ein_paar = _kleinstes_fenster(duplicate_ai_service.build_messages([_paar(zweck)]))
    zwei_paare = _kleinstes_fenster(
        duplicate_ai_service.build_messages([_paar(zweck), _paar(zweck)])
    )
    fenster = ein_paar + 50
    assert fenster < zwei_paare, "Testaufbau: zwei Paare duerfen zusammen nicht passen"
    _suche, senden = _modell(
        monkeypatch,
        _antwort(_urteile(("same", "Gleiche Rechnung."))),
        _antwort(_urteile(("different", "Andere Rechnung."))),
        ziel=_ziel(fenster),
    )

    ergebnis = _bewerten(kopf, erste, zweite)
    assert ergebnis["available"] is True
    assert ergebnis["reason"] == "ok"
    assert sorted(item["verdict"] for item in ergebnis["assessments"]) == ["different", "same"]

    koerper = _gesendet(senden)
    assert len(koerper) == 2
    for einzeln in koerper:
        assert einzeln["response_format"]["json_schema"]["schema"]["properties"]["urteile"][
            "minItems"
        ] == 1
        # Der ganze Verwendungszweck geht hinaus - nichts still gekuerzt.
        assert zweck in einzeln["messages"][1]["content"]
    assert len(await _protokoll(kopf)) == 2


async def test_a_failing_later_batch_keeps_the_earlier_judgements(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    zweck = "Rechnung 4711 Musterdienst " + "Z" * 1500
    erste = await _kandidat(kopf, zweck)
    zweite = await _kandidat(kopf, zweck)
    await _einstellungen(kopf)
    fenster = _kleinstes_fenster(duplicate_ai_service.build_messages([_paar(zweck)])) + 50
    _suche, senden = _modell(
        monkeypatch,
        _antwort(_urteile(("same", "Gleiche Rechnung."))),
        httpx.ReadTimeout("Modell zu langsam"),
        ziel=_ziel(fenster),
    )

    ergebnis = _bewerten(kopf, erste, zweite)
    assert ergebnis["available"] is True
    assert ergebnis["reason"] == reasons.MODEL_TIMEOUT
    assert "1 von 2 Paaren beurteilt" in ergebnis["message"]
    verdicts = [item["verdict"] for item in ergebnis["assessments"]]
    assert sorted(verdicts) == ["same", "unsure"]
    nicht = next(item for item in ergebnis["assessments"] if item["verdict"] == "unsure")
    assert nicht["reason"].startswith("Nicht beurteilt:")
    assert senden.await_count == 2
    stati = sorted(zeile.status for zeile in await _protokoll(kopf))
    assert stati == ["success", "timeout"]


async def test_a_pair_too_large_for_the_window_is_named_not_cut(
    kopf: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    zeile = await _kandidat(kopf, "Z" * 3000)
    await _einstellungen(kopf)
    _suche, senden = _modell(monkeypatch, ziel=_ziel(1024))

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["available"] is False
    assert ergebnis["reason"] == reasons.MODEL_INPUT_TOO_LARGE
    # Mit Aufschluesselung, damit klar ist, was nicht passt.
    assert "Fenster 1.024 Token" in ergebnis["message"]
    senden.assert_not_awaited()
    [zeile_protokoll] = await _protokoll(kopf)
    assert (zeile_protokoll.status, zeile_protokoll.reason_code) == (
        "refused",
        reasons.MODEL_INPUT_TOO_LARGE,
    )


async def test_an_already_decided_row_has_nothing_to_assess(kopf: dict[str, str]) -> None:
    zeile = await _kandidat(kopf)
    await _sql(
        "update import_rows set status = 'skipped' where id = cast(:id as uuid)",
        {"id": zeile},
    )

    ergebnis = _bewerten(kopf, zeile)
    assert ergebnis["available"] is False
    assert ergebnis["reason"] == "no_candidates"


def test_reading_verdicts_tolerates_text_around_the_json() -> None:
    assert duplicate_ai_service.lies_urteile('Hier: {"urteile": []} fertig.') == []
    assert duplicate_ai_service.lies_urteile("kein JSON") is None
    assert duplicate_ai_service.lies_urteile('{"urteile": "nein"}') is None
