"""Erstattungsfaehigkeit am Buchungsstrom (docs/ERSTATTUNGSFAEHIGKEIT-PLAN.md).

Laeuft wie die uebrigen Tests gegen ein echtes lokales Supabase - inklusive RLS
und des Triggers `validate_transaction_reimbursement_mark()`. Gerade die
Richtungs- und Betragspruefung liegt in der Datenbank und nicht in Python; ein
Test mit Attrappe wuerde genau das nicht pruefen.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _household(prefix: str) -> tuple[dict[str, str], str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    household = client.post(
        "/v1/households",
        json={"name": "Erstattungsmarken-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    headers = {**_auth_headers(token), "X-Household-Id": household["id"]}
    return headers, str(household["id"])


@pytest.fixture()
def env() -> dict[str, Any]:
    headers, household_id = _household("mark")
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return {"headers": headers, "household_id": household_id, "account_id": str(account["id"])}


def _transaction(env: dict[str, Any], amount_minor: int = -12346) -> dict[str, Any]:
    response = client.post(
        "/v1/transactions",
        json={
            "account_id": env["account_id"],
            "booked_date": "2026-07-01",
            "amount_minor": amount_minor,
            "payee_label": "Dr. Mueller",
        },
        headers=env["headers"],
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _put(env: dict[str, Any], transaction_id: str, **body: Any) -> Any:
    return client.put(
        f"/v1/reimbursements/marks/{transaction_id}", json=body, headers=env["headers"]
    )


# --- Erwartungshoehe -------------------------------------------------------


def test_vollerstattung_erwartet_den_ganzen_betrag(env: dict[str, Any]) -> None:
    tx = _transaction(env)
    response = _put(env, tx["id"], state="expected", expectation_mode="full")
    assert response.status_code == 200, response.text
    assert response.json()["expected_minor"] == 12346


def test_quote_rechnet_kaufmaennisch(env: dict[str, Any]) -> None:
    tx = _transaction(env)
    response = _put(env, tx["id"], expectation_mode="quota", expectation_rate="0.8")
    assert response.status_code == 200, response.text
    # 12346 * 0.8 = 9876.8 -> 9877
    assert response.json()["expected_minor"] == 9877


def test_fixbetrag_traegt_den_teilerstattungsfall(env: dict[str, Any]) -> None:
    """Apotheke: Rezept erstattungsfaehig, Kosmetik nicht. Entschieden am
    10.08.2026 gegen eine Marke je Split-Zeile."""
    tx = _transaction(env, amount_minor=-4000)
    response = _put(env, tx["id"], expectation_mode="fixed", expectation_minor=1200)
    assert response.status_code == 200, response.text
    assert response.json()["expected_minor"] == 1200


def test_unbekannte_hoehe_ist_nicht_null(env: dict[str, Any]) -> None:
    """'Da kommt was zurueck, wie viel weiss ich nicht' darf nicht als 0
    durchgehen - sonst faellt die Buchung aus der offenen Summe."""
    tx = _transaction(env)
    response = _put(env, tx["id"], expectation_mode="unknown")
    assert response.status_code == 200, response.text
    assert response.json()["expected_minor"] is None
    offen = client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json()
    assert [item["transaction_id"] for item in offen] == [tx["id"]]


def test_ausgenommene_buchung_erwartet_nichts(env: dict[str, Any]) -> None:
    tx = _transaction(env)
    response = _put(env, tx["id"], state="excluded")
    assert response.status_code == 200, response.text
    assert response.json()["expected_minor"] is None
    assert client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json() == []


# --- Was die Datenbank abweist --------------------------------------------


def test_eingangsbuchung_laesst_sich_nicht_markieren(env: dict[str, Any]) -> None:
    """Eine Erstattungserwartung setzt einen Abgang voraus."""
    tx = _transaction(env, amount_minor=25000)
    response = _put(env, tx["id"], expectation_mode="full")
    assert response.status_code == 422, response.text
    assert "Abgaenge" in response.json()["detail"]


def test_fixbetrag_ueber_der_buchung_wird_abgewiesen(env: dict[str, Any]) -> None:
    tx = _transaction(env, amount_minor=-4000)
    response = _put(env, tx["id"], expectation_mode="fixed", expectation_minor=99999)
    assert response.status_code == 422, response.text


def test_quote_ohne_satz_wird_abgewiesen(env: dict[str, Any]) -> None:
    tx = _transaction(env)
    assert _put(env, tx["id"], expectation_mode="quota").status_code == 422


# --- Die beiden Ruecknahmewege --------------------------------------------


def test_ausnehmen_bleibt_bestehen_neutralstellen_nicht(env: dict[str, Any]) -> None:
    """Der Unterschied, an dem solche Funktionen sonst scheitern.

    'Ausnehmen' setzt state='excluded' und bleibt - 'Markierung entfernen'
    loescht die Zeile, danach darf die Automatik wieder greifen.
    """
    tx = _transaction(env)
    assert _put(env, tx["id"], state="excluded").status_code == 200

    entfernt = client.delete(
        f"/v1/reimbursements/marks/{tx['id']}", headers=env["headers"]
    )
    assert entfernt.status_code == 204
    # Zweites Entfernen findet nichts mehr.
    assert (
        client.delete(f"/v1/reimbursements/marks/{tx['id']}", headers=env["headers"]).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_handentscheidung_ueberlebt_einen_regellauf(env: dict[str, Any]) -> None:
    """Die Zusage aus Abschnitt 5 des Plans: Hand schlaegt Regel.

    Ohne sie waere jede Handkorrektur wertlos, weil der naechste Import sie
    kassiert.
    """
    from app.schemas.reimbursements import ReimbursementMarkInput
    from app.services import reimbursement_mark_service

    tx = _transaction(env)
    assert _put(env, tx["id"], state="excluded").status_code == 200

    factory = get_session_factory()
    async with factory() as session:
        # SET LOCAL nimmt keine Bind-Parameter - set_config ist der Weg.
        await session.execute(
            text("select set_config('app.household_id', :household_id, true)"),
            {"household_id": env["household_id"]},
        )
        with pytest.raises(reimbursement_mark_service.MarkConflict) as conflict:
            await reimbursement_mark_service.set_mark(
                session,
                uuid.UUID(env["household_id"]),
                uuid.UUID(tx["id"]),
                ReimbursementMarkInput(state="expected", expectation_mode="full"),
                source="rule",
            )
        assert conflict.value.existing_source == "manual"

    unveraendert = client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json()
    assert unveraendert == []


# --- Mehrfachauswahl -------------------------------------------------------


def test_vorschau_schreibt_nichts(env: dict[str, Any]) -> None:
    eins = _transaction(env)
    zwei = _transaction(env, amount_minor=-5000)
    body = {
        "transaction_ids": [eins["id"], zwei["id"]],
        "state": "expected",
        "expectation_mode": "full",
    }
    vorschau = client.post(
        "/v1/reimbursements/marks/bulk/preview", json=body, headers=env["headers"]
    )
    assert vorschau.status_code == 200, vorschau.text
    assert vorschau.json()["applied"] == 2
    assert client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json() == []

    angewendet = client.post(
        "/v1/reimbursements/marks/bulk/apply", json=body, headers=env["headers"]
    )
    assert angewendet.json()["applied"] == 2
    assert len(client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json()) == 2


def test_eine_eingangsbuchung_kippt_den_sammellauf_nicht(env: dict[str, Any]) -> None:
    """Der SAVEPOINT haelt eine Abweisung lokal."""
    abgang = _transaction(env)
    eingang = _transaction(env, amount_minor=9000)
    antwort = client.post(
        "/v1/reimbursements/marks/bulk/apply",
        json={
            "transaction_ids": [abgang["id"], eingang["id"]],
            "expectation_mode": "full",
        },
        headers=env["headers"],
    )
    assert antwort.status_code == 200, antwort.text
    ergebnis = antwort.json()
    assert ergebnis["applied"] == 1
    assert ergebnis["skipped"] == 1
    uebersprungen = next(i for i in ergebnis["items"] if i["status"] == "skipped")
    assert uebersprungen["transaction_id"] == eingang["id"]
    assert "Abgaenge" in (uebersprungen["reason"] or "")
    assert len(client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json()) == 1


def test_unveraenderte_marke_zaehlt_nicht_als_angewendet(env: dict[str, Any]) -> None:
    tx = _transaction(env)
    body = {"transaction_ids": [tx["id"]], "expectation_mode": "full"}
    client.post("/v1/reimbursements/marks/bulk/apply", json=body, headers=env["headers"])
    zweiter = client.post(
        "/v1/reimbursements/marks/bulk/apply", json=body, headers=env["headers"]
    )
    assert zweiter.json()["unchanged"] == 1
    assert zweiter.json()["applied"] == 0


# --- Haushaltsgrenze -------------------------------------------------------


def test_marken_ueberschreiten_keine_haushaltsgrenze(env: dict[str, Any]) -> None:
    tx = _transaction(env)
    assert _put(env, tx["id"], expectation_mode="full").status_code == 200

    fremde_headers, _ = _household("fremd")
    assert client.get("/v1/reimbursements/marks/open", headers=fremde_headers).json() == []
    fremd = client.put(
        f"/v1/reimbursements/marks/{tx['id']}",
        json={"expectation_mode": "full"},
        headers=fremde_headers,
    )
    # Genau 404, nicht "irgendein Fehlercode". Ein 403 wuerde bestaetigen, dass
    # es die Buchung gibt und sie nur gesperrt ist; ein 422 wuerde auf eine
    # Formpruefung hindeuten. Beides waere ein Informationsleck ueber den
    # fremden Haushalt.
    assert fremd.status_code == 404, fremd.text
    assert fremd.json()["detail"] == "transaction_not_found"


# --- Regelebene ------------------------------------------------------------


def _rule(env: dict[str, Any], **body: Any) -> dict[str, Any]:
    payload = {"name": "Arztkosten", "match_payee_raw": "Dr.", **body}
    response = client.post("/v1/rules", json=payload, headers=env["headers"])
    assert response.status_code in (200, 201), response.text
    return dict(response.json())


def test_regel_traegt_die_erstattungsaktion_durch_die_api(env: dict[str, Any]) -> None:
    """Sechs handgeschriebene Spaltenlisten muessen die neuen Felder fuehren -
    eine vergessene faellt still aus, ohne Fehler."""
    rule = _rule(
        env,
        action_reimbursable="expected",
        action_expectation_mode="quota",
        action_expectation_rate="0.8",
    )
    assert rule["action_reimbursable"] == "expected"
    assert rule["action_expectation_mode"] == "quota"

    gelesen = client.get("/v1/rules", headers=env["headers"]).json()
    treffer = next(r for r in gelesen if r["id"] == rule["id"])
    assert treffer["action_expectation_rate"] is not None

    geaendert = client.patch(
        f"/v1/rules/{rule['id']}",
        json={"action_reimbursable": "excluded"},
        headers=env["headers"],
    )
    assert geaendert.status_code == 200, geaendert.text
    assert geaendert.json()["action_reimbursable"] == "excluded"


@pytest.mark.asyncio
async def test_zweiter_durchlauf_ist_vom_ersten_unabhaengig(env: dict[str, Any]) -> None:
    """Der Kern von Abschnitt 6: eine hoeher priorisierte Kategorieregel darf
    eine tiefer stehende Erstattungsregel nicht verdecken."""
    from app.services import rules_service

    _rule(env, name="Kategorie zuerst", priority=100)
    erstattung = _rule(
        env,
        name="Erstattung tiefer",
        priority=1,
        action_reimbursable="expected",
        action_expectation_mode="full",
    )

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("select set_config('app.household_id', :household_id, true)"),
            {"household_id": env["household_id"]},
        )
        treffer = await rules_service.match_reimbursement_rule_for_transaction(
            session,
            uuid.UUID(env["household_id"]),
            "Dr. Mueller",
            "Rechnung",
            -12346,
            uuid.UUID(env["account_id"]),
        )
    assert treffer is not None
    assert str(treffer.id) == erstattung["id"]


@pytest.mark.asyncio
async def test_sammelruecknahme_laesst_handentscheidungen_stehen(env: dict[str, Any]) -> None:
    from app.services import reimbursement_mark_service

    rule = _rule(env, action_reimbursable="expected", action_expectation_mode="full")
    von_regel = _transaction(env)
    von_hand = _transaction(env, amount_minor=-8000)

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("select set_config('app.household_id', :household_id, true)"),
            {"household_id": env["household_id"]},
        )
        rows = (
            await session.execute(
                text(
                    "select id, action_reimbursable, action_expectation_mode, "
                    "action_expectation_rate, action_expectation_minor "
                    "from classification_rules where id = :id"
                ),
                {"id": rule["id"]},
            )
        ).one()
        assert await reimbursement_mark_service.apply_rule(
            session, uuid.UUID(env["household_id"]), uuid.UUID(von_regel["id"]), rows
        )
        await session.commit()

    assert _put(env, von_hand["id"], expectation_mode="full").status_code == 200
    assert len(client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json()) == 2

    vorschau = client.post(
        f"/v1/reimbursements/marks/by-rule/{rule['id']}/withdraw?preview=true",
        headers=env["headers"],
    )
    assert vorschau.status_code == 200, vorschau.text
    assert vorschau.json() == {"withdrawn": 1, "kept_manual": 1, "kept_converted": 0}
    # Die Vorschau hat nichts angefasst.
    assert len(client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json()) == 2

    client.post(
        f"/v1/reimbursements/marks/by-rule/{rule['id']}/withdraw?preview=false",
        headers=env["headers"],
    )
    verblieben = client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json()
    assert [item["transaction_id"] for item in verblieben] == [von_hand["id"]]


# --- Bruecke zum Vorgang ---------------------------------------------------


def _person(env: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/v1/reimbursements/people", json={"display_name": "Kind"}, headers=env["headers"]
    )
    assert response.status_code in (200, 201), response.text
    return dict(response.json())


def test_aus_der_marke_wird_ein_vorgang_der_sich_meldet(env: dict[str, Any]) -> None:
    """Die eigentliche Zusage des Merkmals.

    Eine hochgeladene Arztrechnung nennt keinen Erstattungsbetrag; der Vorgang
    entstand deshalb mit expected_minor = 0 und fiel in 'draft' - er meldete
    sich nie. Ueber die Marke traegt er eine Erwartung und wird
    'action_required'.
    """
    person = _person(env)
    tx = _transaction(env)
    assert _put(env, tx["id"], expectation_mode="quota", expectation_rate="0.8").status_code == 200

    antwort = client.post(
        f"/v1/reimbursements/marks/{tx['id']}/case",
        json={"insured_person_id": person["id"]},
        headers=env["headers"],
    )
    assert antwort.status_code == 200, antwort.text
    case_id = antwort.json()["case_id"]

    vorgang = client.get(f"/v1/reimbursements/cases/{case_id}", headers=env["headers"]).json()
    assert vorgang["provider_name"] == "Dr. Mueller"
    assert vorgang["billed_minor"] == 12346
    assert vorgang["expected_minor"] == 9877
    # Die Buchung IST der Zahlungsbeleg.
    assert vorgang["payment_status"] == "paid"
    # Und genau deshalb meldet er sich jetzt.
    assert vorgang["overall_status"] == "action_required"

    # Die Marke ist aus der offenen Liste raus, aber nicht geloescht.
    assert client.get("/v1/reimbursements/marks/open", headers=env["headers"]).json() == []
    alle = client.get(
        "/v1/reimbursements/marks/open?only_open=false", headers=env["headers"]
    ).json()
    assert alle[0]["converted_at"] is not None


def test_umgewandelte_marke_laesst_sich_nicht_mehr_zuruecknehmen(env: dict[str, Any]) -> None:
    """An ihr haengt ein Vorgang - der wird von Hand aufgeloest oder gar nicht."""
    person = _person(env)
    tx = _transaction(env)
    _put(env, tx["id"], expectation_mode="full")
    client.post(
        f"/v1/reimbursements/marks/{tx['id']}/case",
        json={"insured_person_id": person["id"]},
        headers=env["headers"],
    )

    assert client.delete(
        f"/v1/reimbursements/marks/{tx['id']}", headers=env["headers"]
    ).status_code == 404

    zweimal = client.post(
        f"/v1/reimbursements/marks/{tx['id']}/case",
        json={"insured_person_id": person["id"]},
        headers=env["headers"],
    )
    assert zweimal.status_code == 409


def test_ausgenommene_buchung_ergibt_keinen_vorgang(env: dict[str, Any]) -> None:
    person = _person(env)
    tx = _transaction(env)
    _put(env, tx["id"], state="excluded")
    antwort = client.post(
        f"/v1/reimbursements/marks/{tx['id']}/case",
        json={"insured_person_id": person["id"]},
        headers=env["headers"],
    )
    assert antwort.status_code == 422, antwort.text
