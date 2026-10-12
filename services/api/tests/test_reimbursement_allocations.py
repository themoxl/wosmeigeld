"""Bankabgleich eines Erstattungsvorgangs (docs/VERSICHERUNGEN-PLAN.md, Anhang P0).

Der Pfad, den die Oberflaeche bisher nicht gehen konnte: eine eingegangene
Auszahlung wird einem Vorgang zugeordnet, teilweise oder ganz, und laesst sich
wieder loesen. Die Statusnachfuehrung liegt in `_reconcile_case_after_allocation`
und wird hier ueber die echte Datenbank geprueft - inklusive des
Ueberzahlungs-Triggers, der in SQL sitzt und nicht in Python.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _household(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    household = client.post(
        "/v1/households",
        json={"name": "Bankabgleich-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": str(household["id"])}


@pytest.fixture()
def env() -> dict[str, Any]:
    headers = _household("alloc")
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    person = client.post(
        "/v1/reimbursements/people",
        json={"display_name": "Testperson"},
        headers=headers,
    ).json()
    return {"headers": headers, "account_id": str(account["id"]), "person_id": str(person["id"])}


def _case(env: dict[str, Any], *, billed: int = 25000, expected: int = 20000) -> dict[str, Any]:
    response = client.post(
        "/v1/reimbursements/cases",
        json={
            "insured_person_id": env["person_id"],
            "provider_name": "Praxis Beispiel",
            "invoice_number": "RE-2026-17",
            "invoice_date": "2026-07-01",
            "billed_minor": billed,
            "expected_minor": expected,
            "payment_status": "paid",
            "submission_status": "submitted",
            "reimbursement_status": "expected",
        },
        headers=env["headers"],
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _payout(env: dict[str, Any], amount_minor: int, *, note: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "account_id": env["account_id"],
        "booked_date": "2026-07-20",
        "amount_minor": amount_minor,
        "payee_label": "Krankenversicherung AG",
    }
    if note is not None:
        body["note"] = note
    response = client.post("/v1/transactions", json=body, headers=env["headers"])
    assert response.status_code == 201, response.text
    return dict(response.json())


def _get_case(env: dict[str, Any], case_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/reimbursements/cases/{case_id}", headers=env["headers"])
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_auszahlung_wird_kandidat_und_nach_zuordnung_ist_der_vorgang_abgeglichen(
    env: dict[str, Any],
) -> None:
    """Der Golden Path: Geld kommt an, wird vorgeschlagen, wird zugeordnet."""
    case = _case(env)
    payout = _payout(env, 20000, note="Erstattung RE-2026-17")

    kandidaten = client.get(
        f"/v1/reimbursements/cases/{case['id']}/transaction-candidates",
        headers=env["headers"],
    )
    assert kandidaten.status_code == 200, kandidaten.text
    treffer = [k for k in kandidaten.json() if k["transaction_id"] == payout["id"]]
    assert treffer, "die passende Auszahlung muss als Kandidat erscheinen"
    # Die Begruendung ist Teil der Zusage: eine Zahl allein waere nicht pruefbar.
    assert treffer[0]["evidence"], "ein Kandidat ohne Begruendung ist nicht ueberpruefbar"
    assert treffer[0]["remaining_minor"] == 20000

    zuordnung = client.post(
        f"/v1/reimbursements/cases/{case['id']}/allocations",
        json={
            "transaction_id": payout["id"],
            "allocation_type": "reimbursement",
            "amount_minor": 20000,
        },
        headers=env["headers"],
    )
    assert zuordnung.status_code == 201, zuordnung.text

    danach = _get_case(env, case["id"])
    assert danach["reimbursed_minor"] == 20000
    assert danach["reimbursement_status"] == "received"
    assert danach["outstanding_minor"] == 0


def test_teilzahlung_bleibt_teilzahlung_bis_der_rest_kommt(env: dict[str, Any]) -> None:
    case = _case(env)
    erste = _payout(env, 12000)
    zweite = _payout(env, 8000)

    for transaction, betrag in ((erste, 12000), (zweite, 8000)):
        antwort = client.post(
            f"/v1/reimbursements/cases/{case['id']}/allocations",
            json={
                "transaction_id": transaction["id"],
                "allocation_type": "reimbursement",
                "amount_minor": betrag,
            },
            headers=env["headers"],
        )
        assert antwort.status_code == 201, antwort.text
        zwischenstand = _get_case(env, case["id"])
        if betrag == 12000:
            assert zwischenstand["reimbursement_status"] == "partial"
            assert zwischenstand["outstanding_minor"] == 8000

    assert _get_case(env, case["id"])["reimbursement_status"] == "received"


def test_sammelueberweisung_deckt_zwei_vorgaenge(env: dict[str, Any]) -> None:
    """Eine Buchung, zwei Vorgaenge - der haeufigste reale Fall bei der PKV."""
    erster = _case(env, billed=25000, expected=20000)
    zweiter = _case(env, billed=10000, expected=7000)
    sammel = _payout(env, 27000)

    for case, betrag in ((erster, 20000), (zweiter, 7000)):
        antwort = client.post(
            f"/v1/reimbursements/cases/{case['id']}/allocations",
            json={
                "transaction_id": sammel["id"],
                "allocation_type": "reimbursement",
                "amount_minor": betrag,
            },
            headers=env["headers"],
        )
        assert antwort.status_code == 201, antwort.text

    assert _get_case(env, erster["id"])["reimbursement_status"] == "received"
    assert _get_case(env, zweiter["id"])["reimbursement_status"] == "received"


def test_mehr_zuordnen_als_die_buchung_hergibt_wird_abgewiesen(env: dict[str, Any]) -> None:
    case = _case(env)
    payout = _payout(env, 20000)

    erste = client.post(
        f"/v1/reimbursements/cases/{case['id']}/allocations",
        json={
            "transaction_id": payout["id"],
            "allocation_type": "reimbursement",
            "amount_minor": 20000,
        },
        headers=env["headers"],
    )
    assert erste.status_code == 201, erste.text

    zweiter = _case(env, billed=10000, expected=5000)
    ueberzahlung = client.post(
        f"/v1/reimbursements/cases/{zweiter['id']}/allocations",
        json={
            "transaction_id": payout["id"],
            "allocation_type": "reimbursement",
            "amount_minor": 5000,
        },
        headers=env["headers"],
    )
    assert ueberzahlung.status_code == 409, ueberzahlung.text
    assert ueberzahlung.json()["detail"] == "transaction_overallocated"


def test_zuordnung_laesst_sich_wieder_loesen_und_der_status_faellt_zurueck(
    env: dict[str, Any],
) -> None:
    """Eine falsche Zuordnung muss korrigierbar sein, ohne Spuren zu hinterlassen."""
    case = _case(env)
    payout = _payout(env, 20000)

    zuordnung = client.post(
        f"/v1/reimbursements/cases/{case['id']}/allocations",
        json={
            "transaction_id": payout["id"],
            "allocation_type": "reimbursement",
            "amount_minor": 20000,
        },
        headers=env["headers"],
    ).json()
    assert _get_case(env, case["id"])["reimbursement_status"] == "received"

    liste = client.get(
        f"/v1/reimbursements/cases/{case['id']}/allocations", headers=env["headers"]
    )
    assert liste.status_code == 200, liste.text
    eintraege = liste.json()
    assert len(eintraege) == 1
    # Ohne diese drei Felder waere die Liste nicht nachpruefbar.
    assert eintraege[0]["booked_date"] == "2026-07-20"
    assert eintraege[0]["payee_label"] == "Krankenversicherung AG"
    assert eintraege[0]["transaction_amount_minor"] == 20000

    entfernen = client.delete(
        f"/v1/reimbursements/cases/{case['id']}/allocations/{zuordnung['id']}",
        headers=env["headers"],
    )
    assert entfernen.status_code == 204, entfernen.text

    danach = _get_case(env, case["id"])
    assert danach["reimbursed_minor"] == 0
    # NICHT 'partial': teilweise erstattet waere nach dem Loesen schlicht falsch.
    assert danach["reimbursement_status"] == "expected"
    assert danach["outstanding_minor"] == 20000
    assert client.get(
        f"/v1/reimbursements/cases/{case['id']}/allocations", headers=env["headers"]
    ).json() == []

    # Und die Buchung ist wieder frei.
    kandidaten = client.get(
        f"/v1/reimbursements/cases/{case['id']}/transaction-candidates",
        headers=env["headers"],
    ).json()
    treffer = [k for k in kandidaten if k["transaction_id"] == payout["id"]]
    assert treffer and treffer[0]["remaining_minor"] == 20000


def test_fremder_haushalt_bekommt_genau_404_und_nichts_anderes() -> None:
    """Eindeutig 404 - kein 403, kein 422.

    Ein abweichender Code waere ein Informationsleck: er verriete, dass es den
    Vorgang gibt und er nur nicht zugaenglich ist.
    """
    eigen_headers = _household("alloc-a")
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=eigen_headers,
    ).json()
    person = client.post(
        "/v1/reimbursements/people",
        json={"display_name": "Testperson"},
        headers=eigen_headers,
    ).json()
    eigen = {
        "headers": eigen_headers,
        "account_id": str(account["id"]),
        "person_id": str(person["id"]),
    }
    case = _case(eigen)
    payout = _payout(eigen, 20000)
    zuordnung = client.post(
        f"/v1/reimbursements/cases/{case['id']}/allocations",
        json={
            "transaction_id": payout["id"],
            "allocation_type": "reimbursement",
            "amount_minor": 20000,
        },
        headers=eigen_headers,
    ).json()

    fremd_headers = _household("alloc-b")

    kandidaten = client.get(
        f"/v1/reimbursements/cases/{case['id']}/transaction-candidates",
        headers=fremd_headers,
    )
    assert kandidaten.status_code == 404, kandidaten.text

    liste = client.get(
        f"/v1/reimbursements/cases/{case['id']}/allocations", headers=fremd_headers
    )
    assert liste.status_code == 404, liste.text

    anlegen = client.post(
        f"/v1/reimbursements/cases/{case['id']}/allocations",
        json={
            "transaction_id": payout["id"],
            "allocation_type": "reimbursement",
            "amount_minor": 100,
        },
        headers=fremd_headers,
    )
    assert anlegen.status_code == 404, anlegen.text

    loeschen = client.delete(
        f"/v1/reimbursements/cases/{case['id']}/allocations/{zuordnung['id']}",
        headers=fremd_headers,
    )
    assert loeschen.status_code == 404, loeschen.text

    # Und im eigenen Haushalt haengt die Zuordnung unveraendert.
    assert _get_case(eigen, case["id"])["reimbursed_minor"] == 20000


def test_unbekannte_zuordnung_am_eigenen_vorgang_ist_ebenfalls_404(env: dict[str, Any]) -> None:
    case = _case(env)
    antwort = client.delete(
        f"/v1/reimbursements/cases/{case['id']}/allocations/{uuid.uuid4()}",
        headers=env["headers"],
    )
    assert antwort.status_code == 404, antwort.text
    assert antwort.json()["detail"] == "transaction_allocation_not_found"
