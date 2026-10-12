"""Herkunft einer Kategoriezuordnung (docs/ANALYSE-EXPLORER-PLAN.md, Baustein A).

Prueft die Zusage, die den ganzen Analyse-Ausbau traegt: fuer jede Buchung ist
nachvollziehbar, ob Regel, KI oder Mensch die Kategorie gesetzt hat - bei einer
Regel zusaetzlich, welche und woran sie gegriffen hat.

Laeuft wie die uebrigen Tests gegen ein echtes lokales Supabase, damit auch
Migration 0008 und die zusammengesetzten Fremdschluessel mitgeprueft werden.
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.conftest import wait_for_import_processing
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

GROCERY = "Lebensmittel"
RESTAURANT = "Restaurant"


@pytest.fixture()
def headers() -> dict[str, str]:
    token = _signup(f"prov-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households",
        json={"name": "Herkunfts-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(headers: dict[str, str]) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return str(account["id"])


def _category_id(headers: dict[str, str], name: str) -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Standardkategorie '{name}' nicht gefunden")


def _provenance(headers: dict[str, str], transaction_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/transactions/{transaction_id}/provenance", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _import(headers: dict[str, str], account_id: str, filename: str, content: bytes) -> None:
    upload = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": (filename, content, "application/octet-stream")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    run_id = upload.json()["id"]

    wait_for_import_processing()

    commit = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=headers)
    assert commit.status_code == 200, commit.text


def _import_two_rows(headers: dict[str, str], account_id: str) -> list[dict[str, Any]]:
    """Importiert zwei CSV-Zeilen mit getrenntem Empfaenger und
    Verwendungszweck und committet sie."""
    csv_content = (
        b"Datum;Betrag;Empfaenger;Verwendungszweck\n"
        b"2026-03-02;-42,50;REWE MARKT GMBH;EINKAUF REWE FILIALE 42\n"
        b"2026-03-03;-13,20;BAECKEREI SCHMIDT;BROT UND BROETCHEN\n"
    )
    _import(headers, account_id, "umsaetze.csv", csv_content)
    return list(client.get("/v1/transactions", headers=headers).json())


def test_import_preserves_bank_raw_text(headers: dict[str, str], account_id: str) -> None:
    """Der Bankrohtext ueberlebt den Import - vorher landete der Zweck nur in
    note und war dort von einer Regelaktion ueberschreibbar."""
    transactions = _import_two_rows(headers, account_id)
    assert len(transactions) == 2

    rewe = next(t for t in transactions if t["amount_minor"] == -4250)
    provenance = _provenance(headers, str(rewe["id"]))

    assert provenance["source"] == "import"
    assert provenance["payee_raw"] == "REWE MARKT GMBH"
    assert provenance["purpose_raw"] == "EINKAUF REWE FILIALE 42"

    origin = provenance["import_origin"]
    assert origin is not None
    assert origin["filename"] == "umsaetze.csv"
    assert origin["detected_format"] == "csv_generic"
    assert origin["row_number"] is not None


def test_mt940_import_preserves_bank_transaction_code(
    headers: dict[str, str], account_id: str
) -> None:
    """Der Bankcode ist die strukturierte Information, die eine Textsuche im
    Zweck nicht ersetzt - CSV fuehrt ihn nicht, MT940 und CAMT schon."""
    sample = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "test-fixtures"
        / "imports"
        / "mt940_sample.mta"
    )
    _import(headers, account_id, "umsaetze.mta", sample.read_bytes())

    transactions = client.get("/v1/transactions", headers=headers).json()
    assert len(transactions) == 2

    einkauf = next(t for t in transactions if t["amount_minor"] == -4250)
    provenance = _provenance(headers, str(einkauf["id"]))

    assert provenance["source"] == "import"
    assert provenance["bank_transaction_code"] == "N024"
    assert provenance["purpose_raw"] is not None
    assert "Einkauf" in provenance["purpose_raw"]


def test_rule_match_is_recorded_with_matched_criterion(
    headers: dict[str, str], account_id: str
) -> None:
    """Eine greifende Regel hinterlaesst Regel-ID, Regelname und das
    getroffene Kriterium - das ist die Grundlage fuer den Klartextsatz
    'gesetzt durch Regel X, weil der Verwendungszweck Y enthaelt'."""
    grocery_id = _category_id(headers, GROCERY)
    rule = client.post(
        "/v1/rules",
        json={
            "name": "REWE ist Supermarkt",
            "priority": 100,
            "match_purpose": "REWE",
            "action_category_id": grocery_id,
        },
        headers=headers,
    )
    assert rule.status_code == 201, rule.text
    rule_id = rule.json()["id"]

    transactions = _import_two_rows(headers, account_id)
    rewe = next(t for t in transactions if t["amount_minor"] == -4250)
    baker = next(t for t in transactions if t["amount_minor"] == -1320)

    rewe_split = _provenance(headers, str(rewe["id"]))["splits"][0]
    assert rewe_split["category_source"] == "rule"
    assert rewe_split["category_id"] == grocery_id
    assert rewe_split["rule_id"] == rule_id
    assert rewe_split["rule_name"] == "REWE ist Supermarkt"
    assert rewe_split["rule_priority"] == 100
    assert rewe_split["matched_on"] == {
        "rule_name": "REWE ist Supermarkt",
        "criteria": [{"field": "purpose", "pattern": "REWE"}],
    }

    # Die zweite Zeile trifft keine Regel und bleibt ohne Kategorie - dann darf
    # auch keine Herkunft behauptet werden.
    baker_split = _provenance(headers, str(baker["id"]))["splits"][0]
    assert baker_split["category_source"] == "unset"
    assert baker_split["category_id"] is None
    assert baker_split["rule_id"] is None
    assert baker_split["categorized_at"] is None


def test_manual_assignment_records_actor(headers: dict[str, str], account_id: str) -> None:
    """Eine Zuordnung von Hand wird als 'manual' samt Zeitpunkt und Person
    festgehalten."""
    grocery_id = _category_id(headers, GROCERY)
    created = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-03-04",
            "amount_minor": -999,
            "category_id": grocery_id,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    transaction_id = str(created.json()["id"])

    split = _provenance(headers, transaction_id)["splits"][0]
    assert split["category_source"] == "manual"
    assert split["category_name"] == GROCERY
    assert split["category_group_name"] is not None
    assert split["categorized_at"] is not None
    assert split["categorized_by"] is not None
    assert split["rule_id"] is None
    assert split["decision_id"] is None


async def _stage_decision(
    household_id: str, transaction_id: str, category_id: str, source: str
) -> None:
    """Ein Vorschlag, wie ihn der Klassifikationslauf schreibt. Direkt in die
    Tabelle, weil es dafuer keinen Endpunkt gibt (und kein Modell laufen
    soll)."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into classification_decisions (household_id, transaction_id, "
                "payee_name, category_id, confidence, reason_code, source, model_name) "
                "values (:household_id, :transaction_id, 'REWE', :category_id, 0.95, "
                ":reason_code, :source, case when :source = 'ai' then 'test-model' end)"
            ),
            {
                "household_id": household_id,
                "transaction_id": transaction_id,
                "category_id": category_id,
                "reason_code": (
                    "known_payee" if source == "history" else "merchant_and_context_match"
                ),
                "source": source,
            },
        )


async def test_accepting_a_history_proposal_is_not_recorded_as_ai(
    headers: dict[str, str], account_id: str
) -> None:
    """Der Vorfilter fuer bekannte Empfaenger schlaegt ohne Modellaufruf vor
    (source='history'). Bestaetigt der Nutzer das in der Review-Inbox, muss die
    Herkunft das sagen: weder 'ai' (es lief kein Modell) noch 'manual' (der
    Vorschlag kam nicht vom Nutzer)."""
    grocery_id = _category_id(headers, GROCERY)
    created = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-03-05", "amount_minor": -1234},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    transaction_id = str(created.json()["id"])
    await _stage_decision(headers["X-Household-Id"], transaction_id, grocery_id, "history")

    accepted = client.post(
        "/v1/review/accept",
        json={
            "type": "unclassified",
            "transaction_id": transaction_id,
            "category_id": grocery_id,
        },
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text

    split = _provenance(headers, transaction_id)["splits"][0]
    assert split["category_source"] == "history"
    assert split["decision_id"] is not None
    assert split["reason_code"] == "known_payee"
    assert split["model_name"] is None
    # Wer bestaetigt hat, bleibt Teil der Herkunft.
    assert split["categorized_by"] is not None


def test_changing_category_overwrites_rule_provenance(
    headers: dict[str, str], account_id: str
) -> None:
    """Korrigiert ein Mensch eine Regelzuordnung, geht die Herkunft auf
    'manual' ueber - ein stehengebliebener Regelverweis waere falsch."""
    grocery_id = _category_id(headers, GROCERY)
    restaurant_id = _category_id(headers, RESTAURANT)
    client.post(
        "/v1/rules",
        json={
            "name": "REWE ist Supermarkt",
            "priority": 100,
            "match_purpose": "REWE",
            "action_category_id": grocery_id,
        },
        headers=headers,
    )

    transactions = _import_two_rows(headers, account_id)
    rewe = next(t for t in transactions if t["amount_minor"] == -4250)
    transaction_id = str(rewe["id"])

    before = _provenance(headers, transaction_id)["splits"][0]
    assert before["category_source"] == "rule"

    updated = client.put(
        f"/v1/transactions/{transaction_id}/category",
        json={"category_id": restaurant_id, "expected_version": rewe["version"]},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text

    after = _provenance(headers, transaction_id)["splits"][0]
    assert after["category_source"] == "manual"
    assert after["category_id"] == restaurant_id
    assert after["rule_id"] is None
    assert after["matched_on"] is None
    assert after["categorized_by"] is not None


def test_removing_category_clears_provenance(headers: dict[str, str], account_id: str) -> None:
    """Ohne Kategorie gibt es keine Herkunft: die Felder fallen auf 'unset'
    zurueck, statt eine alte Begruendung zu behalten."""
    grocery_id = _category_id(headers, GROCERY)
    created = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-03-05",
            "amount_minor": -500,
            "category_id": grocery_id,
        },
        headers=headers,
    ).json()
    transaction_id = str(created["id"])

    cleared = client.put(
        f"/v1/transactions/{transaction_id}/category",
        json={"category_id": None, "expected_version": created["version"]},
        headers=headers,
    )
    assert cleared.status_code == 200, cleared.text

    split = _provenance(headers, transaction_id)["splits"][0]
    assert split["category_source"] == "unset"
    assert split["category_id"] is None
    assert split["categorized_at"] is None
    assert split["categorized_by"] is None


def test_bulk_assignment_records_provenance(headers: dict[str, str], account_id: str) -> None:
    """Auch die Mehrfachzuordnung schreibt die Herkunft - sonst waere sie der
    Weg, mit dem sich Buchungen unbemerkt ohne Nachweis kategorisieren
    lassen."""
    grocery_id = _category_id(headers, GROCERY)
    transactions = _import_two_rows(headers, account_id)
    ids = [str(t["id"]) for t in transactions]

    applied = client.post(
        "/v1/transactions/bulk-category/apply",
        json={"transaction_ids": ids, "category_id": grocery_id},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == 2

    for transaction_id in ids:
        split = _provenance(headers, transaction_id)["splits"][0]
        assert split["category_source"] == "manual"
        assert split["category_id"] == grocery_id
        assert split["categorized_by"] is not None


def test_provenance_is_household_scoped(headers: dict[str, str], account_id: str) -> None:
    """Ein fremder Haushalt bekommt 404 statt einer Herkunftsauskunft - die
    Existenz der Buchung darf so wenig verraten werden wie ihr Inhalt."""
    created = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-03-06", "amount_minor": -100},
        headers=headers,
    ).json()

    other_token = _signup(
        f"prov-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    other_household = client.post(
        "/v1/households",
        json={"name": "Fremder Haushalt"},
        headers=_auth_headers(other_token),
    ).json()
    other_headers = {
        **_auth_headers(other_token),
        "X-Household-Id": other_household["id"],
    }

    response = client.get(
        f"/v1/transactions/{created['id']}/provenance", headers=other_headers
    )
    assert response.status_code == 404
