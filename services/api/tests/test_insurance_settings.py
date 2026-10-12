"""Benachrichtigungseinstellungen des Versicherungsmoduls (P2).

Der naechtliche Lauf liest `insurance_settings.lead_days` und
`notify_deadline` (services/worker/worker/notifications/insurance_alerts.py).
Bis zu dieser Abnahme gab es keinen Endpunkt dazu: die Spalten standen auf
ihrer Voreinstellung, die Weboberflaeche rief `/v1/insurance/settings` ins
Leere und zeigte dauerhaft "konnte nicht geladen werden". Geprueft wird
deshalb nicht nur das Speichern, sondern auch, dass ein Haushalt ohne Zeile
genau die Werte sieht, mit denen der Lauf tatsaechlich rechnet.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

PFAD = "/v1/insurance/settings"


def _haushalt(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    haushalt = client.post(
        "/v1/households",
        json={"name": "Einstellungs-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": str(haushalt["id"])}


@pytest.fixture()
def kopf() -> dict[str, str]:
    return _haushalt("einst")


def _vollstaendig(**felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "notify_deadline": True,
        "notify_premium_notice": True,
        "notify_claim_followup": True,
        "lead_days": [60, 14, 3],
        "lead_days_special": [0, 7, 2],
        "review_interval_months": 24,
    }
    koerper.update(felder)
    return koerper


def test_ohne_gespeicherte_zeile_gilt_die_voreinstellung(kopf: dict[str, str]) -> None:
    """Kein 404 fuer einen Haushalt, der nie gespeichert hat.

    Die Voreinstellung ist genau die, mit der der naechtliche Lauf per
    `coalesce` rechnet. Ein Fehler zwaenge die Oberflaeche, dieselben Zahlen
    ein zweites Mal zu kennen - und zwei Orte fuer eine Staffel heissen frueher
    oder spaeter zwei verschiedene Staffeln.
    """
    antwort = client.get(PFAD, headers=kopf)

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["lead_days"] == [60, 14, 3]
    assert daten["lead_days_special"] == [0, 7, 2]
    assert daten["notify_deadline"] is True
    assert daten["review_interval_months"] == 24


def test_gespeicherte_staffel_kommt_unveraendert_zurueck(kopf: dict[str, str]) -> None:
    antwort = client.put(
        PFAD, json=_vollstaendig(lead_days=[90, 30, 7, 1], notify_deadline=False), headers=kopf
    )

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["lead_days"] == [90, 30, 7, 1]
    assert antwort.json()["notify_deadline"] is False

    # Zweiter Abruf aus der Datenbank, nicht aus der Antwort: erst er zeigt,
    # dass wirklich geschrieben wurde.
    erneut = client.get(PFAD, headers=kopf)
    assert erneut.status_code == 200, erneut.text
    assert erneut.json()["lead_days"] == [90, 30, 7, 1]
    assert erneut.json()["notify_deadline"] is False


def test_zweites_speichern_ersetzt_statt_eine_zweite_zeile_anzulegen(kopf: dict[str, str]) -> None:
    """Der Primaerschluessel ist der Haushalt - ein zweites PUT muss dieselbe
    Zeile treffen, sonst waehlte der naechtliche Lauf zwischen zwei Staffeln."""
    client.put(PFAD, json=_vollstaendig(lead_days=[45, 10]), headers=kopf)
    client.put(PFAD, json=_vollstaendig(lead_days=[30, 5]), headers=kopf)

    assert client.get(PFAD, headers=kopf).json()["lead_days"] == [30, 5]


def test_die_staffel_wird_absteigend_gespeichert(kopf: dict[str, str]) -> None:
    """`current_stage` sucht die engste erreichte Stufe. In beliebiger
    Reihenfolge eingetippt duerfte das Ergebnis nicht davon abhaengen."""
    antwort = client.put(PFAD, json=_vollstaendig(lead_days=[3, 60, 14]), headers=kopf)

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["lead_days"] == [60, 14, 3]


def test_eine_doppelte_stufe_wird_abgewiesen(kopf: dict[str, str]) -> None:
    """Eine Stufe feuert genau einmal; die zweite gleiche Zahl waere eine
    Angabe ohne Wirkung - die Oberflaeche zeigte eine Erinnerung an, die es
    nie gibt."""
    antwort = client.put(PFAD, json=_vollstaendig(lead_days=[30, 30, 3]), headers=kopf)

    assert antwort.status_code == 422, antwort.text


def test_eine_staffel_jenseits_eines_jahres_wird_abgewiesen(kopf: dict[str, str]) -> None:
    """366 Tage Vorlauf erinnerten an eine Frist des naechsten
    Versicherungsjahres."""
    antwort = client.put(PFAD, json=_vollstaendig(lead_days=[400, 14, 3]), headers=kopf)

    assert antwort.status_code == 422, antwort.text


def test_mehr_als_fuenf_stufen_werden_abgewiesen(kopf: dict[str, str]) -> None:
    antwort = client.put(
        PFAD, json=_vollstaendig(lead_days=[90, 60, 30, 14, 7, 3]), headers=kopf
    )

    assert antwort.status_code == 422, antwort.text


def test_ein_fremder_haushalt_sieht_die_eigene_staffel_nicht(kopf: dict[str, str]) -> None:
    """Die Einstellungen haengen am Haushalt, nicht am Nutzer."""
    client.put(PFAD, json=_vollstaendig(lead_days=[90, 30]), headers=kopf)

    fremd = _haushalt("einst-fremd")
    assert client.get(PFAD, headers=fremd).json()["lead_days"] == [60, 14, 3]
