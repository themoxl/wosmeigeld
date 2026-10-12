"""Phase 3 Integrationstests: Importplattform (Upload, Auto-Erkennung, Staging,
Dublettenerkennung, Commit).

Uploads verarbeiten seit Stage 2d asynchron ueber einen dramatiq-Actor (siehe
services/api/app/api/v1/routes/imports.py). Im Testprozess laeuft dafuer ein
StubBroker synchron mit (siehe tests/conftest.py) - `wait_for_import_processing()`
wartet, bis die Verarbeitung abgeschlossen ist, bevor der Run erneut per GET
abgefragt wird (die ursprüngliche POST-Antwort zeigt nur noch status='pending')."""

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services.import_mapping_ai import (
    MAPPING_PROMPT_VERSION,
    MAX_SAMPLE_ROWS,
    build_suggestion,
)
from tests.conftest import wait_for_import_processing
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p3-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-3")
    household = client.post(
        "/v1/households", json={"name": "Phase-3-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: dict[str, str]) -> str:
    acc = client.post(
        "/v1/accounts",
        json={"name": "Import-Girokonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return str(acc["id"])


def test_upload_and_auto_detect_generic_csv(
    household_headers: dict[str, str], account_id: str
) -> None:
    csv_content = (
        b"Datum;Soll/Haben;Betrag_EUR;Buchungsart;Empfaenger/Zweck\n"
        b"2026-01-02;Soll;-19,99;EINZUGSERMAECHTIGUNG;MUSTER KRANKENVERSICHERUNG\n"
        b"2026-01-03;Haben;100,00;GUTSCHRIFT;Gehaltszahlung\n"
    )

    response = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("umsaetze.csv", csv_content, "text/csv")},
        headers=household_headers,
    )
    assert response.status_code == 201
    run_id = response.json()["id"]
    assert response.json()["status"] == "pending"

    wait_for_import_processing()

    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "ready"
    assert run["detected_format"] == "csv_generic"
    assert run["total_rows"] == 2
    assert run["new_count"] == 2

    # Staging-Zeilen abrufen
    rows_resp = client.get(f"/v1/imports/{run_id}/rows", headers=household_headers)
    assert rows_resp.status_code == 200
    rows = rows_resp.json()
    assert len(rows) == 2
    assert rows[0]["amount_minor"] == -1999
    assert rows[0]["status"] == "new"

    # Commit ausfuehren
    commit_resp = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert commit_resp.status_code == 200
    assert commit_resp.json()["committed"] == 2

    # In echten Buchungen verifizieren
    txs_resp = client.get(
        f"/v1/transactions?account_id={account_id}", headers=household_headers
    )
    assert txs_resp.status_code == 200
    txs = txs_resp.json()
    assert len(txs) == 2


def test_duplicate_detection_on_second_upload(
    household_headers: dict[str, str], account_id: str
) -> None:
    csv_content = (
        b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n"
        b"2026-01-02;Soll;-50,00;REWE Markt\n"
    )

    # Erster Import + Commit
    r1_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("import1.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()
    client.post(f"/v1/imports/{r1_id}/commit", json={}, headers=household_headers)

    # Zweiter Import derselben Datei
    r2_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("import2.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()

    r2 = client.get(f"/v1/imports/{r2_id}", headers=household_headers).json()
    assert r2["duplicate_count"] == 1
    assert r2["new_count"] == 0

    rows = client.get(f"/v1/imports/{r2_id}/rows", headers=household_headers).json()
    assert rows[0]["status"] == "duplicate_exact"


def test_mapping_profile_is_applied_on_upload(
    household_headers: dict[str, str], account_id: str
) -> None:
    # Spaltennamen, die keiner automatischen Alias-Erkennung entsprechen -
    # ohne Mapping-Profil wuerde der Import mit error_count > 0 landen bzw.
    # fehlschlagen.
    csv_content = b"Wertstellung;Zahlungsbetrag;Auftraggeber\n2026-02-10;-19,99;Streaminganbieter\n"

    profile = client.post(
        "/v1/imports/mapping-profiles",
        json={
            "name": "Testbank Custom CSV",
            "bank_format": "csv_generic",
            "column_mapping": {
                "booked_date": "Wertstellung",
                "amount": "Zahlungsbetrag",
                "payee": "Auftraggeber",
            },
        },
        headers=household_headers,
    ).json()

    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id, "mapping_profile_id": profile["id"]},
        files={"file": ("custom.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()

    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["mapping_profile_id"] == profile["id"]
    assert run["status"] == "ready"
    assert run["total_rows"] == 1
    assert run["new_count"] == 1

    rows = client.get(f"/v1/imports/{run_id}/rows", headers=household_headers).json()
    assert rows[0]["amount_minor"] == -1999
    assert rows[0]["payee_raw"] == "Streaminganbieter"


# ---------------------------------------------------------------------------
# Commit-Fehlerfaelle: drei unterscheidbare HTTP-Antworten statt eines 500ers
#
# Der Nutzer traf live genau den mittleren Fall (Lauf 'ready', aber
# account_id NULL) und bekam einen 500er, den das Web als "Die API ist nicht
# erreichbar" auslegte.
# ---------------------------------------------------------------------------


def test_commit_unknown_run_returns_404(household_headers: dict[str, str]) -> None:
    res = client.post(
        f"/v1/imports/{uuid.uuid4()}/commit", json={}, headers=household_headers
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "Importlauf nicht gefunden."


def test_commit_without_any_account_in_household_says_create_one_first(
    household_headers: dict[str, str],
) -> None:
    """Der live aufgetretene Fall: Lauf ist 'ready', hat kein Zielkonto - und im
    Haushalt existiert ueberhaupt noch kein Konto. Dann ist "lege zuerst ein
    Konto an" die Anweisung, die weiterhilft."""
    csv_content = (
        b"Datum;Betrag_EUR;Empfaenger/Zweck\n2026-01-02;-19,99;MUSTER KRANKENVERSICHERUNG\n"
    )
    run_id = client.post(
        "/v1/imports/upload",
        files={"file": ("ohne-konto.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()

    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "ready"
    assert run["account_id"] is None

    res = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "kein Zielkonto zugewiesen" in detail
    assert "noch kein Konto" in detail
    assert "lege zuerst ein Konto an" in detail
    # Die frueher behauptete Einschraenkung ist keine mehr.
    assert "erneut mit ausgewaehltem Zielkonto hochladen" not in detail


def test_commit_without_account_points_to_assigning_it_afterwards(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Gibt es im Haushalt ein Konto, fehlt nur die Zuweisung - und die ist seit
    PATCH /v1/imports/{run_id} nachtraeglich moeglich. Die Meldung darf keinen
    erneuten Upload mehr als einzigen Ausweg nennen."""
    csv_content = b"Datum;Betrag_EUR;Empfaenger/Zweck\n2026-01-02;-25,99;Versicherung\n"
    run_id = client.post(
        "/v1/imports/upload",
        files={"file": ("ohne-konto-2.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()

    res = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "Bitte weise dem Importlauf ein Zielkonto zu" in detail
    assert "nachtraeglich" in detail
    assert "erneut mit ausgewaehltem Zielkonto hochladen" not in detail


def test_commit_of_already_completed_run_returns_409_with_actual_status(
    household_headers: dict[str, str], account_id: str
) -> None:
    csv_content = b"Datum;Betrag_EUR;Empfaenger/Zweck\n2026-03-04;-12,50;Baeckerei\n"
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("doppelt.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()

    first = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert first.status_code == 200
    assert first.json()["committed"] == 1

    second = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert "'completed'" in detail
    assert "bereits vollstaendig uebernommen" in detail


def test_commit_while_still_pending_returns_409(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ohne wait_for_import_processing() steht der Lauf noch auf 'pending' -
    der Nutzer soll lesen, dass er warten muss, nicht einen 500er sehen."""
    csv_content = b"Datum;Betrag_EUR;Empfaenger/Zweck\n2026-03-05;-9,90;Kiosk\n"
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("pending.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]

    res = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert res.status_code == 409
    assert "'pending'" in res.json()["detail"]

    # Die eingereihte Nachricht nicht liegen lassen (sonst arbeitet sie ein
    # spaeterer Test mit ab).
    wait_for_import_processing()


# ---------------------------------------------------------------------------
# KI-Spaltenvorschlag: immer nur ein Vorschlag, ein Ausfall bricht nichts
#
# Alle Tests laufen gegen einen deterministischen Fake-Client (gepatchtes
# httpx.AsyncClient) - sie sind damit ohne laufendes LM Studio gruen. Der
# Vorschlag laeuft ueber den Gateway des KI-Kerns ohne Vorabtest: der einzige
# POST ist der Mapping-Aufruf selbst (weitere Faelle in
# tests/test_import_mapping_ai.py).
# ---------------------------------------------------------------------------

# Spaltennamen, die die deterministische Erkennung ebenfalls versteht - der
# Lauf wird also regulaer 'ready', und der KI-Vorschlag ergaenzt eine bereits
# funktionierende Vorbelegung, statt sie zu ersetzen.
_MAPPING_CSV = (
    b"Buchungstag;Betrag;Empfaenger;Verwendungszweck\n"
    b"02.01.2026;-25,99;Haendler EINS;Rechnung 4711\n"
    b"03.01.2026;-10,00;Haendler ZWEI;Ueberweisung DE89370400440532013000\n"
    b"04.01.2026;120,00;Haendler DREI;Gutschrift\n"
    b"05.01.2026;-33,33;Haendler VIER;Abbuchung\n"
    b"06.01.2026;-1,50;Haendler FUENF;Gebuehr\n"
)

_MAPPING_ANSWER = (
    '{"booked_date_column": "Buchungstag", "amount_column": "Betrag", '
    '"payee_column": "Empfaenger", "purpose_column": "Verwendungszweck", '
    '"currency": "EUR", "date_format": "%d.%m.%Y", "decimal_separator": ",", '
    '"confidence": 0.92}'
)


def _chat_response(status_code: int, content: str, finish_reason: str = "stop") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}]
    }
    return resp


def _models_response() -> MagicMock:
    """Modellliste samt GELADENEM Kontextfenster (LM Studio /api/v0/models):
    seit dem relativen KI-Budget sendet der Kern ohne bekanntes Fenster
    nichts - weder den Vorabtest noch den Mapping-Aufruf."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": [
            {
                "id": "fake-mapping-model",
                "type": "llm",
                "state": "loaded",
                "loaded_context_length": 32768,
            }
        ]
    }
    return resp


def _upload_mapping_csv(headers: dict[str, str], account_id: str) -> str:
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("mapping.csv", _MAPPING_CSV, "text/csv")},
        headers=headers,
    ).json()["id"]
    wait_for_import_processing()
    return str(run_id)


def test_mapping_suggestion_returns_columns_and_stays_a_suggestion(
    household_headers: dict[str, str], account_id: str
) -> None:
    run_id = _upload_mapping_csv(household_headers, account_id)
    profiles_before = client.get(
        "/v1/imports/mapping-profiles", headers=household_headers
    ).json()

    post = AsyncMock(side_effect=[_chat_response(200, _MAPPING_ANSWER)])
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        res = client.post(
            f"/v1/imports/{run_id}/mapping-suggestion", json={}, headers=household_headers
        )

    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["reason"] == "ok"
    assert data["column_mapping"] == {
        "booked_date": "Buchungstag",
        "amount": "Betrag",
        "payee": "Empfaenger",
        "purpose": "Verwendungszweck",
    }
    assert data["date_format"] == "%d.%m.%Y"
    assert data["decimal_separator"] == ","
    assert data["currency"] == "EUR"
    assert data["confidence"] == 0.92
    assert data["model_name"] == "fake-mapping-model"
    assert data["prompt_version"] == MAPPING_PROMPT_VERSION
    # Reihenfolge kommt aus import_rows.raw_data (jsonb sortiert die
    # Schluessel nach Laenge, dann alphabetisch) - nicht aus der Datei.
    assert data["source_columns"] == [
        "Betrag",
        "Empfaenger",
        "Buchungstag",
        "Verwendungszweck",
    ]

    # Strukturierter Pfad wie in der Klassifikation ({name, strict, schema}),
    # und die Spaltenauswahl ist per Enum auf real vorhandene Spalten begrenzt.
    assert post.await_count == 1
    mapping_body = post.await_args_list[0].kwargs["json"]
    assert mapping_body["response_format"]["type"] == "json_schema"
    assert mapping_body["response_format"]["json_schema"]["name"] == "import_column_mapping"
    assert mapping_body["response_format"]["json_schema"]["strict"] is True
    schema = mapping_body["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["booked_date_column"]["enum"] == [
        "Betrag",
        "Empfaenger",
        "Buchungstag",
        "Verwendungszweck",
        "",
    ]

    # Der Vorschlag wird NIE angewendet: kein neues Mapping-Profil, Lauf
    # unveraendert.
    profiles_after = client.get(
        "/v1/imports/mapping-profiles", headers=household_headers
    ).json()
    assert len(profiles_after) == len(profiles_before)
    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "ready"
    assert run["mapping_profile_id"] is None


def test_mapping_suggestion_sends_only_few_masked_sample_rows(
    household_headers: dict[str, str], account_id: str
) -> None:
    """PII-Budget: hoechstens MAX_SAMPLE_ROWS Zeilen, jede durch die
    bestehende Maskierung."""
    run_id = _upload_mapping_csv(household_headers, account_id)

    post = AsyncMock(side_effect=[_chat_response(200, _MAPPING_ANSWER)])
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        res = client.post(
            f"/v1/imports/{run_id}/mapping-suggestion", json={}, headers=household_headers
        )

    assert res.json()["sample_row_count"] == MAX_SAMPLE_ROWS
    user_prompt = post.await_args_list[0].kwargs["json"]["messages"][1]["content"]
    assert f"{MAX_SAMPLE_ROWS} Stueck" in user_prompt
    # Die Datei hat fuenf Zeilen - Zeile 4 und 5 verlassen den Server nicht.
    assert "Haendler DREI" in user_prompt
    assert "Haendler VIER" not in user_prompt
    assert "Haendler FUENF" not in user_prompt
    # IBAN aus Zeile 2 ist maskiert.
    assert "DE89370400440532013000" not in user_prompt
    assert "DE89************3000" in user_prompt


def test_mapping_suggestion_without_lm_studio_is_no_import_error(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Faellt LM Studio aus, gibt es sauber 'kein Vorschlag verfuegbar' - und
    der bisherige Ablauf (Commit) funktioniert unveraendert weiter."""
    run_id = _upload_mapping_csv(household_headers, account_id)

    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=OSError("connection refused"))):
        res = client.post(
            f"/v1/imports/{run_id}/mapping-suggestion", json={}, headers=household_headers
        )

    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False
    assert data["reason"] == "model_unavailable"
    assert data["column_mapping"] == {}
    assert data["source_columns"]  # die Spalten kennt der Server auch ohne KI

    commit = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert commit.status_code == 200
    assert commit.json()["committed"] == 5


def test_mapping_suggestion_with_unusable_answer_reports_no_suggestion(
    household_headers: dict[str, str], account_id: str
) -> None:
    run_id = _upload_mapping_csv(household_headers, account_id)

    post = AsyncMock(side_effect=[_chat_response(200, "Klar, gerne! Welche Datei meinst du?")])
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        res = client.post(
            f"/v1/imports/{run_id}/mapping-suggestion", json={}, headers=household_headers
        )

    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False
    assert data["reason"] == "model_output_unusable"
    # Genau ein Aufruf: kein Formatierer, keine Eskalation.
    assert post.await_count == 1


def test_unrecognised_columns_land_in_needs_mapping_with_their_raw_rows(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Eine Datei mit unbekannten Spaltennamen ist kein Totalausfall mehr.

    Frueher endete sie auf 'failed': die Spaltennamen waren bekannt, wurden
    aber nirgends festgehalten, und der einzige Ausweg war ein neuer Upload.
    Jetzt bleiben Kopfzeile und Rohzeilen erhalten - genau das Material, das
    Mappingkorrektur und KI-Vorschlag brauchen."""
    unparsable = b"Spalte A;Spalte B\nirgendwas;42\n"
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("kaputt.csv", unparsable, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()

    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "needs_mapping"
    assert run["detected_columns"] == ["Spalte A", "Spalte B"]

    rows = client.get(f"/v1/imports/{run_id}/rows", headers=household_headers).json()
    assert [r["raw_data"] for r in rows] == [{"Spalte A": "irgendwas", "Spalte B": "42"}]
    # Ohne gueltige Zuordnung tragen die Zeilen bewusst weder Datum noch Betrag.
    assert rows[0]["booked_date"] is None and rows[0]["amount_minor"] is None


def test_mapping_suggestion_uses_the_columns_the_file_really_has(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Vorschlag speist sich aus den gestagten Rohzeilen des Laufs.

    Frueher musste der Client bei einem gescheiterten Parse Beispielzeilen
    mitschicken, weil serverseitig nichts uebrig blieb. Seit 'needs_mapping'
    die Rohzeilen behaelt, ist die Datei selbst die Quelle - und der Vorschlag
    kann sich nur noch auf Spalten beziehen, die es wirklich gibt."""
    unparsable = b"Wertstellung;Zahlungsbetrag;Auftraggeber\n10.02.2026;-19,99;Streaminganbieter\n"
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("kaputt.csv", unparsable, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()
    assert (
        client.get(f"/v1/imports/{run_id}", headers=household_headers).json()["status"]
        == "needs_mapping"
    )

    answer = (
        '{"booked_date_column": "Wertstellung", "amount_column": "Zahlungsbetrag", '
        '"payee_column": "Auftraggeber", "purpose_column": "", "currency": "EUR", '
        '"date_format": "%d.%m.%Y", "decimal_separator": ",", "confidence": 0.7}'
    )
    post = AsyncMock(side_effect=[_chat_response(200, answer)])
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        res = client.post(
            f"/v1/imports/{run_id}/mapping-suggestion",
            json={},
            headers=household_headers,
        )

    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["column_mapping"] == {
        "booked_date": "Wertstellung",
        "amount": "Zahlungsbetrag",
        "payee": "Auftraggeber",
    }


def test_mapping_suggestion_without_any_sample_rows_is_unavailable(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ohne jedes Material gibt es keinen Vorschlag - und kein Modellaufruf.

    Erreichbar nur noch fuer Formate, die gar keine Spalten kennen: bei MT940
    ist ein Spalten-Mapping fachlich sinnlos, es gibt dort keine Kopfzeile, aus
    der sich etwas ableiten liesse. Tabellenformate landen stattdessen in
    'needs_mapping' und behalten ihre Rohzeilen."""
    unparsable = b"das ist kein gueltiger SWIFT-Auszug\n"
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("kaputt2.sta", unparsable, "text/plain")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()
    assert (
        client.get(f"/v1/imports/{run_id}", headers=household_headers).json()["status"] == "failed"
    )

    res = client.post(
        f"/v1/imports/{run_id}/mapping-suggestion", json={}, headers=household_headers
    )
    assert res.status_code == 200
    assert res.json()["reason"] == "no_sample_rows"


def test_pending_run_can_be_cancelled_and_then_deleted(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Ausweg aus einem Lauf, den niemand verarbeitet.

    Frueher war genau das die Sackgasse: 'pending' sperrte das Loeschen mit
    409 ("bitte warten"), und ohne laufenden Worker wurde daraus nie etwas
    anderes. Abbrechen fuehrt jetzt in einen Zustand, aus dem das Entfernen
    erlaubt ist."""
    csv_content = b"Datum;Betrag\n2026-01-02;-25,99\n"
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("wartet.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    # BEWUSST ohne wait_for_import_processing(): der Lauf soll hier noch auf
    # 'pending' stehen - das ist der Zustand, um den es geht.

    cancelled = client.post(f"/v1/imports/{run_id}/cancel", headers=household_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # Der Worker darf die Nachricht danach noch abholen und muss sie ignorieren.
    wait_for_import_processing()
    after = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert after["status"] == "cancelled"
    assert after["total_rows"] == 0

    assert client.delete(f"/v1/imports/{run_id}", headers=household_headers).status_code == 204


def test_cancel_rejects_a_run_that_is_not_in_flight(
    household_headers: dict[str, str], account_id: str
) -> None:
    csv_content = b"Datum;Betrag\n2026-01-02;-25,99\n"
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("fertig.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()

    res = client.post(f"/v1/imports/{run_id}/cancel", headers=household_headers)
    assert res.status_code == 409
    assert "ready" in res.json()["detail"]


def test_reparse_applies_a_corrected_column_mapping(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Weg aus 'needs_mapping' heraus - ohne erneuten Upload.

    Der entscheidende Punkt: die Datei wird nirgends aufbewahrt. Das erneute
    Einlesen arbeitet ausschliesslich auf den Rohzeilen, die beim ersten Lauf
    in import_rows.raw_data gelandet sind."""
    unparsable = b"Wertstellung;Zahlungsbetrag;Auftraggeber\n10.02.2026;-19,99;Streaminganbieter\n"
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("mapping.csv", unparsable, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()
    assert (
        client.get(f"/v1/imports/{run_id}", headers=household_headers).json()["status"]
        == "needs_mapping"
    )

    res = client.post(
        f"/v1/imports/{run_id}/reparse",
        json={
            "column_mapping": {
                "booked_date": "Wertstellung",
                "amount": "Zahlungsbetrag",
                "payee": "Auftraggeber",
            }
        },
        headers=household_headers,
    )
    assert res.status_code == 200
    wait_for_import_processing()

    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "ready"
    assert run["total_rows"] == 1
    assert run["error_message"] is None

    rows = client.get(f"/v1/imports/{run_id}/rows", headers=household_headers).json()
    assert rows[0]["booked_date"] == "2026-02-10"
    assert rows[0]["amount_minor"] == -1999
    assert rows[0]["payee_raw"] == "Streaminganbieter"
    # Die Datei selbst wurde nie erneut hochgeladen - die Rohzeile ist erhalten.
    assert rows[0]["raw_data"]["Wertstellung"] == "10.02.2026"


def test_mapping_suggestion_unknown_run_returns_404(household_headers: dict[str, str]) -> None:
    res = client.post(
        f"/v1/imports/{uuid.uuid4()}/mapping-suggestion", json={}, headers=household_headers
    )
    assert res.status_code == 404


def test_build_suggestion_drops_columns_that_do_not_exist() -> None:
    """Invariante: der Vorschlag enthaelt nie eine Spalte, die es in der Datei
    nicht gibt - und ohne Datum/Betrag gilt er als unvollstaendig."""
    columns = ["Buchungstag", "Betrag"]
    rows: list[dict[str, Any]] = [{"Buchungstag": "02.01.2026", "Betrag": "-25,99"}]
    parsed: dict[str, Any] = {
        "booked_date_column": "Buchungstag",
        "amount_column": "Erfundene Spalte",
        "payee_column": "",
        "purpose_column": "Auch erfunden",
        "currency": "eur",
        "date_format": "%d.%m.%Y",
        "decimal_separator": ";",
        "confidence": 1.5,
    }

    suggestion = build_suggestion(parsed, columns, rows, "fake-model")

    assert suggestion.column_mapping == {"booked_date": "Buchungstag"}
    assert suggestion.available is False
    assert suggestion.reason == "suggestion_incomplete"
    assert suggestion.currency == "EUR"
    assert suggestion.decimal_separator is None  # ';' ist kein Dezimaltrennzeichen
    assert suggestion.confidence == 1.0  # geklemmt


def test_build_suggestion_discards_date_format_that_does_not_parse() -> None:
    """Ein plausibel klingendes, aber falsches Muster darf nicht als
    Vorbelegung im Mapping-Dialog landen."""
    columns = ["Buchungstag", "Betrag"]
    rows: list[dict[str, Any]] = [{"Buchungstag": "02.01.2026", "Betrag": "-25,99"}]
    parsed: dict[str, Any] = {
        "booked_date_column": "Buchungstag",
        "amount_column": "Betrag",
        "payee_column": "",
        "purpose_column": "",
        "currency": "",
        "date_format": "%Y-%m-%d",
        "decimal_separator": ",",
        "confidence": 0.8,
    }

    suggestion = build_suggestion(parsed, columns, rows, "fake-model")

    assert suggestion.available is True
    assert suggestion.date_format is None
    assert suggestion.currency is None


# ---------------------------------------------------------------------------
# Importlaeufe verwalten: Zielkonto nachtraeglich zuweisen, Lauf entfernen
#
# Ausgangslage war ein Lauf, der ohne Zielkonto hochgeladen wurde: nicht
# uebernehmbar (kein Konto), nicht aenderbar (nicht vorgesehen), nicht
# entfernbar - er stand dauerhaft als "Bereit zur Uebernahme" in der Historie.
# ---------------------------------------------------------------------------

_ONE_ROW_CSV = b"Datum;Betrag_EUR;Empfaenger/Zweck\n2026-04-01;-31,40;Stadtwerke\n"


def _start_upload(
    headers: dict[str, str],
    filename: str,
    *,
    content: bytes = _ONE_ROW_CSV,
    account_id: str | None = None,
) -> str:
    """Laedt hoch, ohne die Verarbeitung abzuwarten (Lauf steht auf 'pending')."""
    data = {"account_id": account_id} if account_id else None
    run_id = client.post(
        "/v1/imports/upload",
        data=data,
        files={"file": (filename, content, "text/csv")},
        headers=headers,
    ).json()["id"]
    return str(run_id)


def _upload(
    headers: dict[str, str],
    filename: str,
    *,
    content: bytes = _ONE_ROW_CSV,
    account_id: str | None = None,
) -> str:
    """Laedt hoch und wartet die Verarbeitung ab (Lauf steht danach auf 'ready')."""
    run_id = _start_upload(headers, filename, content=content, account_id=account_id)
    wait_for_import_processing()
    return run_id


async def _upload_from_async_test(
    headers: dict[str, str],
    filename: str,
    *,
    content: bytes = _ONE_ROW_CSV,
    account_id: str | None = None,
) -> str:
    """Wie _upload, aber aus einem async-Test heraus aufrufbar:
    wait_for_import_processing() fuehrt den dramatiq-Actor aus, und der ruft
    intern asyncio.run() - das scheitert innerhalb der bereits laufenden
    Event-Loop des Tests. Im Thread hat er seine eigene Loop."""
    run_id = _start_upload(headers, filename, content=content, account_id=account_id)
    await asyncio.to_thread(wait_for_import_processing)
    return run_id


async def _scalar(query: str, params: dict[str, Any]) -> Any:
    """Direkter DB-Blick fuer Invarianten, die kein Endpunkt zeigt (Staging-
    Zeilen nach dem Loeschen, Herkunft der Buchungen). Die Session laeuft ohne
    SET ROLE authenticated, also als postgres mit BYPASSRLS - sie sieht damit
    auch, was RLS einem Nutzer verbergen wuerde."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        return (await session.execute(text(query), params)).scalar_one()


async def _execute(query: str, params: dict[str, Any]) -> None:
    """Schreibender Gegenpart zu _scalar - fuer Vorbedingungen, die kein
    Endpunkt herstellt (z. B. eine bereits zugeordnete Vertragsfaelligkeit)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text(query), params)
        await session.commit()


async def _fetch_one(query: str, params: dict[str, Any]) -> dict[str, Any]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return dict((await session.execute(text(query), params)).one()._mapping)


async def _audit_events(household_id: str, entity_id: str) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        res = await session.execute(
            text(
                "select action, entity_type, actor_user_id, before, after from audit_log "
                "where household_id = :household_id and entity_id = :entity_id "
                "order by created_at asc"
            ),
            {"household_id": household_id, "entity_id": entity_id},
        )
        return [dict(row._mapping) for row in res]


def test_patch_assigns_account_and_commit_then_succeeds(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Kern des Auftrags: derselbe Lauf, der eben noch 400 gab, laesst sich
    nach der Zuweisung ohne erneuten Upload uebernehmen."""
    run_id = _upload(household_headers, "nachtraeglich.csv")

    blocked = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert blocked.status_code == 400

    patched = client.patch(
        f"/v1/imports/{run_id}",
        json={"account_id": account_id},
        headers=household_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["account_id"] == account_id
    assert patched.json()["status"] == "ready"

    # Auch fuer den naechsten Leser (GET) und fuer den Commit sichtbar.
    assert (
        client.get(f"/v1/imports/{run_id}", headers=household_headers).json()["account_id"]
        == account_id
    )
    commit = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert commit.status_code == 200
    assert commit.json()["committed"] == 1

    txs = client.get(f"/v1/transactions?account_id={account_id}", headers=household_headers).json()
    assert len(txs) == 1


def test_patch_can_also_set_the_mapping_profile(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Mitgefuehrt, aber ausdruecklich nur eine Korrektur der Verknuepfung: die
    Datei ist zu diesem Zeitpunkt bereits geparst, es wird nicht neu gelesen."""
    profile = client.post(
        "/v1/imports/mapping-profiles",
        json={
            "name": f"Profil {uuid.uuid4().hex[:8]}",
            "bank_format": "csv_generic",
            "column_mapping": {"booked_date": "Datum", "amount": "Betrag_EUR"},
        },
        headers=household_headers,
    ).json()
    run_id = _upload(household_headers, "profil.csv")

    res = client.patch(
        f"/v1/imports/{run_id}",
        json={"account_id": account_id, "mapping_profile_id": profile["id"]},
        headers=household_headers,
    )
    assert res.status_code == 200
    assert res.json()["mapping_profile_id"] == profile["id"]
    assert res.json()["account_id"] == account_id


def test_patch_of_completed_run_returns_409(
    household_headers: dict[str, str], account_id: str
) -> None:
    run_id = _upload(household_headers, "uebernommen.csv", account_id=account_id)
    assert (
        client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers).status_code
        == 200
    )

    second_account = client.post(
        "/v1/accounts",
        json={"name": "Zweitkonto", "account_type": "checking"},
        headers=household_headers,
    ).json()["id"]

    res = client.patch(
        f"/v1/imports/{run_id}",
        json={"account_id": second_account},
        headers=household_headers,
    )
    assert res.status_code == 409
    assert "'completed'" in res.json()["detail"]
    assert "bereits uebernommen" in res.json()["detail"]

    # Der Lauf bleibt unangetastet auf dem urspruenglichen Konto.
    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["account_id"] == account_id


def test_patch_with_account_of_a_foreign_household_is_rejected(
    household_headers: dict[str, str],
) -> None:
    """Haushaltsisolation: ein fremdes Konto darf weder zugewiesen noch in
    seiner Existenz bestaetigt werden."""
    foreign_token = _signup(f"p3-fremd-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-4")
    foreign_household = client.post(
        "/v1/households", json={"name": "Fremdhaushalt"}, headers=_auth_headers(foreign_token)
    ).json()
    foreign_headers = {
        **_auth_headers(foreign_token),
        "X-Household-Id": foreign_household["id"],
    }
    foreign_account = client.post(
        "/v1/accounts",
        json={"name": "Fremdkonto", "account_type": "checking"},
        headers=foreign_headers,
    ).json()["id"]

    run_id = _upload(household_headers, "fremdkonto.csv")

    res = client.patch(
        f"/v1/imports/{run_id}",
        json={"account_id": foreign_account},
        headers=household_headers,
    )
    assert res.status_code == 404
    assert "gehoert nicht zu diesem Haushalt" in res.json()["detail"]

    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["account_id"] is None


def test_patch_of_unknown_run_returns_404(
    household_headers: dict[str, str], account_id: str
) -> None:
    res = client.patch(
        f"/v1/imports/{uuid.uuid4()}",
        json={"account_id": account_id},
        headers=household_headers,
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "Importlauf nicht gefunden."


def test_patch_while_still_pending_returns_409(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Waehrend der Worker den Lauf verarbeitet, ist eine Zuweisung ein Rennen -
    er liest account_id zu Beginn der Dublettenerkennung."""
    run_id = client.post(
        "/v1/imports/upload",
        files={"file": ("pending-patch.csv", _ONE_ROW_CSV, "text/csv")},
        headers=household_headers,
    ).json()["id"]

    res = client.patch(
        f"/v1/imports/{run_id}",
        json={"account_id": account_id},
        headers=household_headers,
    )
    assert res.status_code == 409
    assert "'pending'" in res.json()["detail"]

    wait_for_import_processing()


async def test_delete_ready_run_removes_run_and_rows(
    household_headers: dict[str, str],
) -> None:
    run_id = await _upload_from_async_test(household_headers, "entfernbar.csv")
    assert len(client.get(f"/v1/imports/{run_id}/rows", headers=household_headers).json()) == 1

    res = client.delete(f"/v1/imports/{run_id}", headers=household_headers)
    assert res.status_code == 204

    assert client.get(f"/v1/imports/{run_id}", headers=household_headers).status_code == 404
    assert all(
        r["id"] != run_id
        for r in client.get("/v1/imports", headers=household_headers).json()
    )
    # CASCADE nicht geglaubt, sondern nachgesehen.
    assert (
        await _scalar(
            "select count(*) from import_rows where import_run_id = :id", {"id": run_id}
        )
        == 0
    )


async def test_delete_writes_an_audit_event(household_headers: dict[str, str]) -> None:
    run_id = await _upload_from_async_test(household_headers, "auditiert.csv")

    assert client.delete(f"/v1/imports/{run_id}", headers=household_headers).status_code == 204

    events = await _audit_events(household_headers["X-Household-Id"], run_id)
    assert len(events) == 1
    assert events[0]["entity_type"] == "import_run"
    assert events[0]["action"] == "delete"
    assert events[0]["actor_user_id"] is not None
    assert events[0]["after"] is None
    assert events[0]["before"]["filename"] == "auditiert.csv"
    assert events[0]["before"]["status"] == "ready"


async def test_delete_of_completed_run_returns_409_and_keeps_everything(
    household_headers: dict[str, str], account_id: str
) -> None:
    run_id = await _upload_from_async_test(
        household_headers, "bereits-uebernommen.csv", account_id=account_id
    )
    assert (
        client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers).json()[
            "committed"
        ]
        == 1
    )

    res = client.delete(f"/v1/imports/{run_id}", headers=household_headers)
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert "bereits uebernommener Importlauf wird nicht entfernt" in detail
    assert "1 Buchungen" in detail

    # Lauf, Buchungen und deren Herkunft sind unangetastet.
    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "completed"
    assert (
        await _scalar(
            "select count(*) from transactions where import_run_id = :id", {"id": run_id}
        )
        == 1
    )
    assert (
        await _scalar(
            "select count(*) from import_rows where import_run_id = :id", {"id": run_id}
        )
        == 1
    )
    assert await _audit_events(household_headers["X-Household-Id"], run_id) == []


async def test_delete_of_partly_committed_ready_run_is_refused(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ein Teil-Commit laesst den Lauf auf 'ready' stehen - der Status allein
    genuegt also nicht als Kriterium, sonst verlieren die bereits entstandenen
    Buchungen still ihre Herkunft."""
    csv_content = (
        b"Datum;Betrag_EUR;Empfaenger/Zweck\n"
        b"2026-05-01;-10,00;Erste Zeile\n"
        b"2026-05-02;-20,00;Zweite Zeile\n"
    )
    run_id = await _upload_from_async_test(
        household_headers, "teilweise.csv", content=csv_content, account_id=account_id
    )

    rows = client.get(f"/v1/imports/{run_id}/rows", headers=household_headers).json()
    partial = client.post(
        f"/v1/imports/{run_id}/commit",
        json={"row_ids": [rows[0]["id"]]},
        headers=household_headers,
    )
    assert partial.json()["committed"] == 1
    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "ready"

    res = client.delete(f"/v1/imports/{run_id}", headers=household_headers)
    assert res.status_code == 409
    assert "1 Buchungen" in res.json()["detail"]
    assert (
        await _scalar(
            "select count(*) from transactions where import_run_id = :id", {"id": run_id}
        )
        == 1
    )

    # Die Laufzeile sagt es auch ohne Fehlversuch: der Status ist 'ready', aber
    # eine Buchung ist schon entstanden. Ohne diese Zahl bot die Oberflaeche
    # hier ein Entfernen an, das der Server anschliessend ablehnte.
    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "ready"
    assert run["committed_count"] == 1
    assert run["active_transaction_count"] == 1


async def test_run_row_reports_what_is_still_in_the_books(
    household_headers: dict[str, str], account_id: str
) -> None:
    """committed_count und active_transaction_count laufen bewusst auseinander:
    nach dem Zuruecknehmen bleibt das Entfernen des Laufs gesperrt (er ist der
    Herkunftsnachweis), zurueckzunehmen gibt es aber nichts mehr."""
    run_id = await _upload_from_async_test(
        household_headers, "zweimal-nichts.csv", account_id=account_id
    )
    assert (
        client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers).json()[
            "committed"
        ]
        == 1
    )

    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert (run["committed_count"], run["active_transaction_count"]) == (1, 1)

    assert (
        client.post(f"/v1/imports/{run_id}/revert", headers=household_headers).json()["reverted"]
        == 1
    )

    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert (run["committed_count"], run["active_transaction_count"]) == (1, 0)
    # Auch in der Liste, nicht nur beim Einzelabruf - die Historie entscheidet
    # daran, welchen Knopf sie zeigt.
    listed = next(
        r
        for r in client.get("/v1/imports", headers=household_headers).json()
        if r["id"] == run_id
    )
    assert (listed["committed_count"], listed["active_transaction_count"]) == (1, 0)

    # Und entfernen bleibt abgelehnt, obwohl keine Buchung mehr im Bestand steht.
    assert client.delete(f"/v1/imports/{run_id}", headers=household_headers).status_code == 409


def test_delete_while_still_pending_returns_409(household_headers: dict[str, str]) -> None:
    """Loeschen, waehrend der Worker die Zeile noch anfasst, waere ein Rennen -
    es wird bewusst abgelehnt statt "meistens" zu funktionieren."""
    run_id = client.post(
        "/v1/imports/upload",
        files={"file": ("pending-delete.csv", _ONE_ROW_CSV, "text/csv")},
        headers=household_headers,
    ).json()["id"]

    res = client.delete(f"/v1/imports/{run_id}", headers=household_headers)
    assert res.status_code == 409
    assert "wird gerade verarbeitet" in res.json()["detail"]
    assert "'pending'" in res.json()["detail"]

    # Die eingereihte Nachricht nicht liegen lassen.
    wait_for_import_processing()
    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "ready"


def test_delete_of_unknown_run_returns_404(household_headers: dict[str, str]) -> None:
    res = client.delete(f"/v1/imports/{uuid.uuid4()}", headers=household_headers)
    assert res.status_code == 404
    assert res.json()["detail"] == "Importlauf nicht gefunden."


def test_delete_of_failed_run_clears_the_history(household_headers: dict[str, str]) -> None:
    """Fehlgeschlagene Laeufe sind der zweite Fall, der bisher fuer immer in der
    Historie stand - sie haben keine Staging-Zeilen und lassen sich entfernen."""
    run_id = client.post(
        "/v1/imports/upload",
        files={"file": ("kaputt.xyz", b"\x00\x01\x02kein Bankformat", "application/octet-stream")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()
    run = client.get(f"/v1/imports/{run_id}", headers=household_headers).json()
    assert run["status"] == "failed"

    assert client.delete(f"/v1/imports/{run_id}", headers=household_headers).status_code == 204
    assert client.get(f"/v1/imports/{run_id}", headers=household_headers).status_code == 404


# ---------------------------------------------------------------------------
# Import zuruecknehmen
# ---------------------------------------------------------------------------


_TWO_ROW_CSV = (
    b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n"
    b"2026-04-02;Soll;-19,99;MUSTER KRANKENVERSICHERUNG\n"
    b"2026-04-03;Haben;100,00;Gehaltszahlung\n"
)


def _commit_two_row_import(household_headers: dict[str, str], account_id: str) -> str:
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("ruecknahme.csv", _TWO_ROW_CSV, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()
    return _commit_all(household_headers, str(run_id))


async def _commit_two_row_import_async(household_headers: dict[str, str], account_id: str) -> str:
    """Wie _commit_two_row_import, aber aus einem async-Test heraus - siehe
    _upload_from_async_test zur Begruendung."""
    run_id = _start_upload(
        household_headers, "ruecknahme.csv", content=_TWO_ROW_CSV, account_id=account_id
    )
    await asyncio.to_thread(wait_for_import_processing)
    return _commit_all(household_headers, run_id)


def _commit_all(household_headers: dict[str, str], run_id: str) -> str:
    commit = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=household_headers)
    assert commit.status_code == 200
    assert commit.json()["committed"] == 2
    return run_id


def test_revert_preview_lists_what_would_be_touched(
    household_headers: dict[str, str], account_id: str
) -> None:
    run_id = _commit_two_row_import(household_headers, account_id)

    preview = client.get(f"/v1/imports/{run_id}/revert-preview", headers=household_headers)
    assert preview.status_code == 200
    body = preview.json()
    assert body["transactions_total"] == 2
    assert body["revertable"] == 2
    assert body["blocked"] == []
    assert body["from_date"] == "2026-04-02"
    assert body["to_date"] == "2026-04-03"
    # -19,99 + 100,00
    assert body["sum_minor"] == 8001

    # Eine Vorschau darf nichts anfassen.
    assert len(client.get("/v1/transactions", headers=household_headers).json()) == 2


def test_revert_archives_transactions_and_keeps_the_run(
    household_headers: dict[str, str], account_id: str
) -> None:
    run_id = _commit_two_row_import(household_headers, account_id)

    result = client.post(f"/v1/imports/{run_id}/revert", headers=household_headers)
    assert result.status_code == 200
    assert result.json()["reverted"] == 2
    assert result.json()["kept"] == 0

    # Die Buchungen sind aus dem Bestand verschwunden ...
    assert client.get("/v1/transactions", headers=household_headers).json() == []
    # ... der Lauf bleibt aber als Herkunftsnachweis stehen.
    assert client.get(f"/v1/imports/{run_id}", headers=household_headers).status_code == 200


def test_revert_keeps_edited_transactions(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Was nach dem Import bearbeitet wurde, bleibt stehen - sonst vernichtet
    ein Zuruecknehmen Arbeit, die nach dem Import entstanden ist."""
    run_id = _commit_two_row_import(household_headers, account_id)

    transactions = client.get("/v1/transactions", headers=household_headers).json()
    bearbeitet = transactions[0]
    patch_response = client.patch(
        f"/v1/transactions/{bearbeitet['id']}",
        json={"note": "von Hand ergaenzt", "version": bearbeitet["version"]},
        headers=household_headers,
    )
    assert patch_response.status_code == 200

    preview = client.get(
        f"/v1/imports/{run_id}/revert-preview", headers=household_headers
    ).json()
    assert preview["revertable"] == 1
    assert preview["blocked"] == [
        {"reason": "bearbeitet", "label": "nachtraeglich bearbeitet", "count": 1}
    ]

    result = client.post(f"/v1/imports/{run_id}/revert", headers=household_headers).json()
    assert result["reverted"] == 1
    assert result["kept"] == 1

    verbleibend = client.get("/v1/transactions", headers=household_headers).json()
    assert [t["id"] for t in verbleibend] == [bearbeitet["id"]]


def test_revert_allows_reimporting_the_same_file(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der eigentliche Zweck: schlechten Import zuruecknehmen, Datei erneut
    einlesen. Die Dublettenerkennung liest nur nicht-archivierte Buchungen und
    meldet die Zeilen deshalb wieder als neu."""
    run_id = _commit_two_row_import(household_headers, account_id)
    client.post(f"/v1/imports/{run_id}/revert", headers=household_headers)

    csv_content = (
        b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n"
        b"2026-04-02;Soll;-19,99;MUSTER KRANKENVERSICHERUNG\n"
        b"2026-04-03;Haben;100,00;Gehaltszahlung\n"
    )
    zweiter_lauf = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("ruecknahme.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()["id"]
    wait_for_import_processing()

    rows = client.get(f"/v1/imports/{zweiter_lauf}/rows", headers=household_headers).json()
    assert [r["status"] for r in rows] == ["new", "new"]


def test_revert_takes_linked_transactions_when_asked(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Ausweg aus "0 von 1 Buchungen": mit include_linked geht auch das mit,
    woran inzwischen etwas haengt. Ohne diesen Weg blieb genau die verknuepfte
    Buchung stehen und liess den erneuten Import unvollstaendig - die
    Dublettenerkennung sieht sie ja weiterhin."""
    run_id = _commit_two_row_import(household_headers, account_id)

    transactions = client.get("/v1/transactions", headers=household_headers).json()
    bearbeitet = transactions[0]
    assert (
        client.patch(
            f"/v1/transactions/{bearbeitet['id']}",
            json={"note": "von Hand ergaenzt", "version": bearbeitet["version"]},
            headers=household_headers,
        ).status_code
        == 200
    )

    # Die Vorschau nennt beide Zahlenwerke aus einem Abruf.
    preview = client.get(
        f"/v1/imports/{run_id}/revert-preview", headers=household_headers
    ).json()
    assert preview["revertable"] == 1
    assert preview["transactions_total"] == 2
    assert preview["sum_minor_with_linked"] == 8001

    result = client.post(
        f"/v1/imports/{run_id}/revert",
        json={"include_linked": True},
        headers=household_headers,
    ).json()
    assert result["reverted"] == 2
    assert result["kept"] == 0
    assert result["blocked"] == []

    assert client.get("/v1/transactions", headers=household_headers).json() == []


async def test_revert_with_linked_reopens_the_contract_period(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Die Vertragsfaelligkeit darf nicht auf eine Buchung zeigen, die aus dem
    Bestand verschwunden ist - sonst behauptet der Vertrag eine Zahlung, deren
    Beleg es nicht mehr gibt. Sie faellt deshalb auf 'missing' zurueck."""
    run_id = await _commit_two_row_import_async(household_headers, account_id)
    transactions = client.get("/v1/transactions", headers=household_headers).json()
    beleg = next(t for t in transactions if t["amount_minor"] < 0)

    contract_id = client.post(
        "/v1/contracts",
        json={
            "name": "Muster Krankenversicherung",
            "amount_minor": 1999,
            "interval": "monthly",
            "next_due_date": "2026-05-02",
            "first_due_date": "2026-04-02",
        },
        headers=household_headers,
    ).json()["id"]
    # Das Anlegen des Vertrags erzeugt die Faelligkeit bereits (Backfill) - hier
    # wird sie nur auf den Beleg gesetzt, statt sie ein zweites Mal einzufuegen.
    await _execute(
        "insert into contract_occurrences "
        "(household_id, contract_id, due_date, status, transaction_id, booked_date, "
        " amount_minor, matched_at) "
        "values (cast(:household_id as uuid), cast(:contract_id as uuid), '2026-04-02', "
        "        'matched', cast(:tx as uuid), '2026-04-02', 1999, now()) "
        "on conflict (contract_id, due_date) do update set "
        "  status = 'matched', transaction_id = excluded.transaction_id, "
        "  booked_date = excluded.booked_date, amount_minor = excluded.amount_minor, "
        "  matched_at = excluded.matched_at",
        {
            "household_id": household_headers["X-Household-Id"],
            "contract_id": contract_id,
            "tx": beleg["id"],
        },
    )
    await _execute(
        "update contracts set next_due_date = '2026-05-02' "
        "where id = cast(:contract_id as uuid)",
        {"contract_id": contract_id},
    )

    preview = client.get(
        f"/v1/imports/{run_id}/revert-preview", headers=household_headers
    ).json()
    assert {b["reason"] for b in preview["blocked"]} == {"vertrag"}

    result = client.post(
        f"/v1/imports/{run_id}/revert",
        json={"include_linked": True},
        headers=household_headers,
    ).json()
    assert result["reverted"] == 2
    geloest = {item["kind"] for item in result["untangled"]}
    assert "vertrag_faelligkeit" in geloest

    # Die Faelligkeit bleibt bestehen, verliert aber ihren Beleg ...
    faelligkeit = (
        await _fetch_one(
            "select status, transaction_id, amount_minor from contract_occurrences "
            "where contract_id = cast(:contract_id as uuid)",
            {"contract_id": contract_id},
        )
    )
    assert faelligkeit["status"] == "missing"
    assert faelligkeit["transaction_id"] is None
    # ... waehrend der Terminplan des Vertrags unangetastet bleibt. Ein
    # Zurueckdrehen von next_due_date auf die betroffene Periode waere hier
    # naheliegend und falsch: es zoege ueber alle dazwischenliegenden Perioden
    # zurueck und liesse den Abgleich sie erneut bewerten - beim Entfernen einer
    # alten Buchung Jahre an Faelligkeiten. Noetig ist es ohnehin nicht, der
    # Abgleich findet offene Fehlperioden unabhaengig davon
    # (contract_matching_service.repair_missing_periods).
    vertrag = client.get(f"/v1/contracts/{contract_id}", headers=household_headers).json()
    assert vertrag["next_due_date"] == "2026-05-02"


def test_revert_without_the_flag_still_keeps_linked_transactions(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Schalter ist ausdruecklich: ohne ihn bleibt es beim alten Verhalten.
    Ein Rumpf ist dabei gar nicht noetig."""
    run_id = _commit_two_row_import(household_headers, account_id)
    transactions = client.get("/v1/transactions", headers=household_headers).json()
    bearbeitet = transactions[0]
    client.patch(
        f"/v1/transactions/{bearbeitet['id']}",
        json={"note": "von Hand ergaenzt", "version": bearbeitet["version"]},
        headers=household_headers,
    )

    result = client.post(f"/v1/imports/{run_id}/revert", headers=household_headers).json()
    assert result["reverted"] == 1
    assert result["kept"] == 1
    assert result["untangled"] == []


def test_revert_preview_unknown_run_is_404(household_headers: dict[str, str]) -> None:
    response = client.get(
        f"/v1/imports/{uuid.uuid4()}/revert-preview", headers=household_headers
    )
    assert response.status_code == 404


async def test_revert_warns_before_taking_an_insurance_payment(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Die Warnliste muss decken, was das Entfernen loest.

    Sie tat es fuer die Versicherungszahlung zunaechst NICHT: die Buchung galt
    als unverknuepft, ging ohne gesetzten Haken mit, und die Zahlung war weg -
    obwohl das Zuruecknehmen ausdruecklich zusagt, genau solche Arbeit stehen zu
    lassen."""
    run_id = await _commit_two_row_import_async(household_headers, account_id)
    transactions = client.get("/v1/transactions", headers=household_headers).json()
    eingang = next(t for t in transactions if t["amount_minor"] > 0)

    # Eine Schadenzahlung an der Buchung - ohne Police und Vorgang ueber die API
    # nachzubauen: hier zaehlt allein, dass die Warnliste sie sieht.
    await _execute(
        "insert into insurance_claims "
        "(household_id, title, occurred_on, status, claimed_minor) "
        "values (cast(:household_id as uuid), 'Sturmschaden', '2026-04-01', "
        "        'reported', 10000)",
        {"household_id": household_headers["X-Household-Id"]},
    )
    claim_id = await _scalar(
        "select id from insurance_claims where household_id = cast(:h as uuid) "
        "order by created_at desc limit 1",
        {"h": household_headers["X-Household-Id"]},
    )
    await _execute(
        "insert into insurance_claim_payments "
        "(household_id, claim_id, transaction_id, direction, kind, amount_minor, booked_on) "
        "values (cast(:household_id as uuid), cast(:claim as uuid), cast(:tx as uuid), "
        "        'inbound', 'settlement', 10000, '2026-04-03')",
        {
            "household_id": household_headers["X-Household-Id"],
            "claim": str(claim_id),
            "tx": eingang["id"],
        },
    )

    vorschau = client.get(
        f"/v1/imports/{run_id}/revert-preview", headers=household_headers
    ).json()
    assert {b["reason"] for b in vorschau["blocked"]} == {"versicherung"}
    assert vorschau["revertable"] == 1

    # Ohne Haken bleibt die Buchung stehen - und die Zahlung mit ihr.
    ergebnis = client.post(f"/v1/imports/{run_id}/revert", headers=household_headers).json()
    assert ergebnis["reverted"] == 1
    assert ergebnis["kept"] == 1
    assert (
        await _scalar(
            "select count(*) from insurance_claim_payments "
            "where transaction_id = cast(:tx as uuid)",
            {"tx": eingang["id"]},
        )
        == 1
    )
