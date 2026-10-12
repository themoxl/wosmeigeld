"""Beteiligte, Objekte, Bausteine und Buendel (docs/VERSICHERUNGEN-PLAN.md, P4).

Fuenf Zusagen tragen diese Datei; jeder Testfall gehoert zu genau einer:

1. **Es gibt keine zweite Personenliste.** Beteiligte kommen aus dem
   Erstattungsmodul, auch wenn sie im Versicherungsdialog entstehen. Der Name
   an der Police ist eine Funktion und keine Kopie - eine Umbenennung schlaegt
   deshalb durch.
2. **Das Erwerbsdatum ist Nutzereingabe und wird nie erfunden.** An ihm haengt
   die Erwerberfrist nach Paragraph 96 Abs. 2 S. 2 VVG (ein Monat ab Erwerb,
   Fristbeginn am Folgetag nach den Paragraphen 187 Abs. 1, 188 Abs. 2 BGB).
   Fehlt es, bleibt das Feld leer - das Werkzeug traegt die Luecke sichtbar
   weiter, statt ein Datum abzuleiten. Die Frist selbst rechnet erst P5; was
   sie braucht, muss aber schon jetzt erfassbar und als fehlend erkennbar sein.
3. **Ein Kennzeichenwechsel ist eine neue Zeile.** Ueberschreiben wuerde die
   Zuordnung alter Buchungen und Schaeden verlieren; zwei offene Zeilen waeren
   nicht Historie, sondern Widerspruch.
4. **PUT auf die Bausteine ersetzt die Menge.** Was nicht im Rumpf steht, ist
   danach weg - auch bei einer leeren Liste.
5. **Ein Buendel hat genau eine Ebene.** Nur so bleibt bestimmbar, welche
   Police der Nummern-Index als Wurzel sieht - und nur deshalb darf eine
   Kindpolice dieselbe Versicherungsscheinnummer tragen wie ihre Hauptpolice.

Dazu die Redaktion der Katalogwelle 2: die Schreibregel gilt fuer die neuen
achtzehn Zeilen genauso wie fuer die Welle K, und sie wird mit derselben
Wortliste geprueft - importiert und nicht abgeschrieben, sonst driften zwei
Listen auseinander.
"""

import os
import re
import uuid
from datetime import date
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup
from tests.test_insurance_catalog import PFLICHTWENDUNGEN, VERBOTENE_WENDUNGEN

client = TestClient(app)

# Zweiter Client fuer den einen Fall, in dem der Dienst heute mit einem
# Serverfehler antwortet (siehe test_eine_mopedpolice_ist_ueber_die_api_erfassbar).
# Der Standardclient wuerde die Ausnahme durchreichen, statt die Antwort zu
# zeigen - und damit den Befund unkenntlich machen.
client_ohne_serverfehler = TestClient(app, raise_server_exceptions=False)

BASIS = "/v1/insurance"

_DSN = os.environ.get(
    "TEST_DIRECT_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:55322/postgres",
)

# Die achtzehn Zeilen der Welle 2 (Plan 3.10). Namentlich und nicht als Zahl:
# eine reine Mengenangabe waere auch dann gruen, wenn eine Sparte gegen eine
# voellig andere getauscht wuerde.
WELLE_2 = (
    "animal_liability",
    "landowner_liability",
    "water_damage_liability",
    "builder_liability",
    "drone_liability",
    "hunting_liability",
    "boat_liability",
    "construction_works",
    "fire_shell",
    "photovoltaic",
    "pet_health",
    "elemental",
    "glass",
    "bicycle",
    "motor_comprehensive",
    "motor_partial",
    "passenger_accident",
    "moped",
)


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


@pytest.fixture()
def kopf() -> dict[str, str]:
    """Function-scoped wie im Policentest: das autouse-Aufraeumen entfernt nach
    jedem Test die dabei entstandenen Haushalte."""
    token = _signup(f"p4-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-31")
    haushalt = client.post(
        "/v1/households",
        json={"name": "P4-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": str(haushalt["id"])}


def _police(kopf: dict[str, str], **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "line_key": "household_contents",
        "insurer_name": "Nordwacht Versicherung AG",
    }
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/policies", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _person(kopf: dict[str, str], name: str, **felder: Any) -> dict[str, Any]:
    antwort = client.post(
        "/v1/reimbursements/people", json={"display_name": name, **felder}, headers=kopf
    )
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _objekt(kopf: dict[str, str], **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {"kind": "vehicle", "label": "Kombi"}
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/objects", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _etag(kopf: dict[str, str], object_id: str) -> str:
    antwort = client.get(f"{BASIS}/objects/{object_id}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return str(antwort.headers["ETag"])


def _policy_etag(kopf: dict[str, str], policy_id: str) -> str:
    antwort = client.get(f"{BASIS}/policies/{policy_id}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return str(antwort.headers["ETag"])


def _sparten(kopf: dict[str, str]) -> dict[str, dict[str, Any]]:
    antwort = client.get(f"{BASIS}/lines", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return {str(zeile["line_key"]): dict(zeile) for zeile in antwort.json()}


async def _offene_kennzeichenzeilen(object_id: str) -> int:
    """Zaehlt die laufenden Historienzeilen direkt in der Datenbank.

    Ueber die API waere nur zu sehen, welche Zeile ANGEZEIGT wird - der
    Partialindex verspricht aber, dass es nie zwei GIBT.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        anzahl = (
            await session.execute(
                text(
                    "select count(*) from insured_object_registrations "
                    "where insured_object_id = cast(:id as uuid) and valid_to is null"
                ),
                {"id": object_id},
            )
        ).scalar_one()
    return int(anzahl)


# ---------------------------------------------------------------------------
# 1. Beteiligte
# ---------------------------------------------------------------------------


def test_die_person_entsteht_im_erstattungsmodul_und_der_name_ist_keine_kopie(
    kopf: dict[str, str],
) -> None:
    """Der Anlegeweg im Versicherungsdialog fuehrt in dieselbe Personenliste.

    Zwei Wahrheiten ueber dieselben Menschen waeren der teuerste Fehler dieses
    Moduls - die Kinder eines Haushalts stuenden dann zweimal da, mit zwei
    Schreibweisen und zwei Geburtsdaten. Die Umbenennung am Ende ist der
    eigentliche Beweis: `insured_person_label()` ist eine Funktion und keine
    Spalte, sonst zeigte die Police weiter den alten Namen.
    """
    police = _police(kopf, line_key="term_life", insurer_name="Lebenskasse AG")
    angelegt = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={
            "new_person": {"display_name": "Jonas Beispiel", "birth_date": "2015-04-01"},
            "role": "insured",
            "share_bp": 7000,
        },
        headers=kopf,
    )
    assert angelegt.status_code == 201, angelegt.text
    verknuepfung = angelegt.json()
    assert verknuepfung["display_name"] == "Jonas Beispiel"
    assert verknuepfung["role"] == "insured"
    assert verknuepfung["share_bp"] == 7000

    personen = client.get("/v1/reimbursements/people", headers=kopf).json()
    assert [(zeile["id"], zeile["display_name"]) for zeile in personen] == [
        (verknuepfung["insured_person_id"], "Jonas Beispiel")
    ]
    assert personen[0]["birth_date"] == "2015-04-01"

    with psycopg.connect(_DSN, autocommit=True) as verbindung:
        verbindung.execute(
            "update health_insured_persons set display_name = %s where id = %s",
            ("Jonas Beispiel-Neumann", verknuepfung["insured_person_id"]),
        )

    nachher = client.get(f"{BASIS}/policies/{police['id']}/persons", headers=kopf).json()
    assert [zeile["display_name"] for zeile in nachher] == ["Jonas Beispiel-Neumann"]


def test_eine_police_hat_genau_einen_versicherungsnehmer(kopf: dict[str, str]) -> None:
    """Der Versicherungsnehmer ist der Vertragspartner, nicht eine von mehreren
    gleichrangigen Rollen - deshalb der Partialindex und deshalb 409.

    Dieselbe Person in einer ZWEITEN Rolle ist dagegen zulaessig: wer den
    Vertrag geschlossen hat, ist regelmaessig auch mitversichert. Verboten ist
    nur die Wiederholung derselben Rolle.
    """
    police = _police(kopf)
    erste = _person(kopf, "Anna Beispiel")
    zweite = _person(kopf, "Bernd Beispiel")

    halter = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": erste["id"], "role": "policyholder"},
        headers=kopf,
    )
    assert halter.status_code == 201, halter.text

    zweiter_halter = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": zweite["id"], "role": "policyholder"},
        headers=kopf,
    )
    assert zweiter_halter.status_code == 409, zweiter_halter.text
    assert zweiter_halter.json()["detail"] == "insurance_policyholder_already_set"

    zweite_rolle = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": erste["id"], "role": "insured"},
        headers=kopf,
    )
    assert zweite_rolle.status_code == 201, zweite_rolle.text

    doppelt = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": erste["id"], "role": "insured"},
        headers=kopf,
    )
    assert doppelt.status_code == 409, doppelt.text
    assert doppelt.json()["detail"] == "insurance_person_already_linked"

    liste = client.get(f"{BASIS}/policies/{police['id']}/persons", headers=kopf).json()
    assert [zeile["role"] for zeile in liste] == ["policyholder", "insured"]


def test_bezugsrechte_bleiben_zusammen_unter_hundert_prozent(kopf: dict[str, str]) -> None:
    """Eine Bedingung ueber mehrere Zeilen - ein CHECK kann sie nicht sehen.

    Bewusst "hoechstens 100" und nicht "genau 100": ein noch nicht vollstaendig
    erfasstes Bezugsrecht ist ein Zwischenstand und kein Fehler, der nicht
    vergebene Rest bleibt sichtbar.
    """
    police = _police(kopf, line_key="term_life", insurer_name="Lebenskasse AG")
    anna = _person(kopf, "Anna Beispiel")
    bernd = _person(kopf, "Bernd Beispiel")

    erste = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": anna["id"], "role": "beneficiary", "share_bp": 6000},
        headers=kopf,
    )
    assert erste.status_code == 201, erste.text

    zuviel = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": bernd["id"], "role": "beneficiary", "share_bp": 5000},
        headers=kopf,
    )
    assert zuviel.status_code == 409, zuviel.text
    assert zuviel.json()["detail"] == "insurance_beneficiary_share_exceeded"

    passend = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": bernd["id"], "role": "beneficiary", "share_bp": 4000},
        headers=kopf,
    )
    assert passend.status_code == 201, passend.text


def test_eine_unbekannte_person_ist_ein_404_und_das_loesen_nimmt_nur_die_zuordnung(
    kopf: dict[str, str],
) -> None:
    """Geloest wird die Verknuepfungszeile, nicht der Mensch.

    Die Person bleibt im Haushalt, mit ihren Vorgaengen und ihren Freigaben -
    sonst naehme das Entfernen einer Rolle aus einer Police jemandem seine
    Krankheitsgeschichte.
    """
    police = _police(kopf)
    anna = _person(kopf, "Anna Beispiel")

    unbekannt = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": str(uuid.uuid4())},
        headers=kopf,
    )
    assert unbekannt.status_code == 404, unbekannt.text
    assert unbekannt.json()["detail"] == "insurance_person_not_found"

    verknuepfung = client.post(
        f"{BASIS}/policies/{police['id']}/persons",
        json={"insured_person_id": anna["id"]},
        headers=kopf,
    ).json()

    geloest = client.delete(
        f"{BASIS}/policies/{police['id']}/persons/{verknuepfung['id']}", headers=kopf
    )
    assert geloest.status_code == 204, geloest.text
    nochmal = client.delete(
        f"{BASIS}/policies/{police['id']}/persons/{verknuepfung['id']}", headers=kopf
    )
    assert nochmal.status_code == 404, nochmal.text
    assert nochmal.json()["detail"] == "insurance_person_link_not_found"

    assert client.get(f"{BASIS}/policies/{police['id']}/persons", headers=kopf).json() == []
    personen = client.get("/v1/reimbursements/people", headers=kopf).json()
    assert [zeile["id"] for zeile in personen] == [anna["id"]]


# ---------------------------------------------------------------------------
# 2. Objekte und das Erwerbsdatum
# ---------------------------------------------------------------------------


def test_das_erwerbsdatum_wird_getragen_und_nie_erfunden(kopf: dict[str, str]) -> None:
    """Ohne Erwerbsdatum ist die Erwerberfrist des Paragraphen 96 Abs. 2 S. 2 VVG
    nicht berechenbar - und das Werkzeug sagt das, statt sie zu raten.

    "Sagen" heisst hier: das Feld bleibt leer und wird an jeder Stelle, an der
    das Fahrzeug erscheint, als leer weitergereicht - auch an der Zuordnung zur
    Police, denn dort steht die Frist spaeter. Kein Anlegedatum, kein
    Vertragsbeginn, kein "heute" schiebt sich an diese Stelle: das Datum ist
    IMMER Nutzereingabe, deshalb traegt es keine Herkunftsspalte.
    """
    ohne = _objekt(kopf, label="Kombi ohne Kaufdatum")
    assert ohne["acquired_on"] is None
    assert ohne["disposed_on"] is None
    assert ohne["version"] == 1

    police = _police(kopf, line_key="motor_liability", insurer_name="Kfz Beispiel")
    verknuepft = client.post(
        f"{BASIS}/policies/{police['id']}/objects",
        json={"insured_object_id": ohne["id"]},
        headers=kopf,
    )
    assert verknuepft.status_code == 201, verknuepft.text
    # Die Luecke wandert mit: an der Police-Objekt-Zuordnung steht dasselbe
    # leere Feld, sonst muesste die Oberflaeche das Objekt einzeln nachladen,
    # um die Rueckfrage stellen zu koennen.
    assert verknuepft.json()["acquired_on"] is None

    nachgetragen = client.patch(
        f"{BASIS}/objects/{ohne['id']}",
        json={"acquired_on": "2026-03-10"},
        headers={**kopf, "If-Match": _etag(kopf, str(ohne["id"]))},
    )
    assert nachgetragen.status_code == 200, nachgetragen.text
    assert nachgetragen.json()["acquired_on"] == "2026-03-10"
    assert nachgetragen.json()["version"] == 2
    zuordnungen = client.get(f"{BASIS}/policies/{police['id']}/objects", headers=kopf).json()
    assert [zeile["acquired_on"] for zeile in zuordnungen] == ["2026-03-10"]

    geleert = client.patch(
        f"{BASIS}/objects/{ohne['id']}",
        json={"clear_fields": ["acquired_on"]},
        headers={**kopf, "If-Match": _etag(kopf, str(ohne["id"]))},
    )
    assert geleert.status_code == 200, geleert.text
    assert geleert.json()["acquired_on"] is None

    # Eine Abgabe vor dem Erwerb ist kein Zwischenstand, sondern ein
    # Widerspruch - das faengt der Vertrag ab, bevor die Datenbank es tut.
    widerspruch = client.post(
        f"{BASIS}/objects",
        json={
            "kind": "vehicle",
            "label": "Zeitreisender Kombi",
            "acquired_on": "2026-05-01",
            "disposed_on": "2026-04-01",
        },
        headers=kopf,
    )
    assert widerspruch.status_code == 422, widerspruch.text

    # Und auf demselben Weg nachtraeglich ebenfalls nicht: der PATCH hat keinen
    # eigenen Modellvalidator, hier haelt der CHECK der Tabelle die Reihenfolge.
    mit_datum = _objekt(kopf, label="Kombi mit Kaufdatum", acquired_on="2026-05-01")
    rueckwaerts = client.patch(
        f"{BASIS}/objects/{mit_datum['id']}",
        json={"disposed_on": "2026-04-01"},
        headers={**kopf, "If-Match": _etag(kopf, str(mit_datum["id"]))},
    )
    assert rueckwaerts.status_code == 422, rueckwaerts.text
    unveraendert = client.get(f"{BASIS}/objects/{mit_datum['id']}", headers=kopf).json()
    assert unveraendert["disposed_on"] is None


def test_der_objektpatch_verlangt_if_match_und_ein_leerer_patch_bumpt_nicht(
    kopf: dict[str, str],
) -> None:
    """Dasselbe Nebenlaeufigkeitsmuster wie an der Police, aber mit einfachem
    ETag: ein Fahrzeug hat eigene Felder und keinen Vertrag darunter."""
    objekt = _objekt(kopf, kind="property", label="Wohnung Nord", living_area_sqm=78)
    assert _etag(kopf, str(objekt["id"])) == '"1"'

    ohne = client.patch(
        f"{BASIS}/objects/{objekt['id']}", json={"city": "Musterstadt"}, headers=kopf
    )
    assert ohne.status_code == 428, ohne.text
    assert ohne.json()["detail"] == "if_match_required"

    veraltet = client.patch(
        f"{BASIS}/objects/{objekt['id']}",
        json={"city": "Musterstadt"},
        headers={**kopf, "If-Match": '"99"'},
    )
    assert veraltet.status_code == 409, veraltet.text
    assert veraltet.json()["detail"] == "version_conflict"
    assert veraltet.headers["ETag"] == '"1"'

    aktuell = client.patch(
        f"{BASIS}/objects/{objekt['id']}",
        json={"city": "Musterstadt"},
        headers={**kopf, "If-Match": '"1"'},
    )
    assert aktuell.status_code == 200, aktuell.text
    assert aktuell.json()["version"] == 2

    # Derselbe Wert noch einmal: der Versionstrigger vergleicht die Zeile.
    unveraendert = client.patch(
        f"{BASIS}/objects/{objekt['id']}",
        json={"city": "Musterstadt"},
        headers={**kopf, "If-Match": '"2"'},
    )
    assert unveraendert.status_code == 200, unveraendert.text
    assert unveraendert.json()["version"] == 2


def test_ein_verknuepftes_objekt_wird_datiert_und_nicht_geloescht(kopf: dict[str, str]) -> None:
    """Der Fremdschluessel steht auf RESTRICT, und das ist die fachliche Aussage:
    ein Objekt, das noch in einer Police steht, verschwindet nicht.

    Der Ausweg ist `disposed_on` - ein abgegebenes Fahrzeug bleibt erhalten,
    sonst verloeren alte Buchungen und spaetere Schaeden ihren Bezug; es steht
    nur nicht mehr in der Auswahl.
    """
    objekt = _objekt(kopf, label="Kombi", acquired_on="2024-01-15")
    police = _police(kopf, line_key="motor_liability", insurer_name="Kfz Beispiel")
    client.post(
        f"{BASIS}/policies/{police['id']}/objects",
        json={"insured_object_id": objekt["id"]},
        headers=kopf,
    )

    in_benutzung = client.delete(f"{BASIS}/objects/{objekt['id']}", headers=kopf)
    assert in_benutzung.status_code == 409, in_benutzung.text
    assert in_benutzung.json()["detail"] == "insurance_object_in_use"
    assert client.get(f"{BASIS}/objects/{objekt['id']}", headers=kopf).status_code == 200

    abgegeben = client.patch(
        f"{BASIS}/objects/{objekt['id']}",
        json={"disposed_on": "2026-06-30"},
        headers={**kopf, "If-Match": _etag(kopf, str(objekt["id"]))},
    )
    assert abgegeben.status_code == 200, abgegeben.text
    assert client.get(f"{BASIS}/objects", headers=kopf).json() == []
    mit_abgegebenen = client.get(
        f"{BASIS}/objects", params={"include_disposed": "true"}, headers=kopf
    ).json()
    assert [zeile["id"] for zeile in mit_abgegebenen] == [objekt["id"]]

    geloest = client.delete(
        f"{BASIS}/policies/{police['id']}/objects/{objekt['id']}", headers=kopf
    )
    assert geloest.status_code == 204, geloest.text
    assert client.delete(f"{BASIS}/objects/{objekt['id']}", headers=kopf).status_code == 204


# ---------------------------------------------------------------------------
# 3. Halter- und Kennzeichenhistorie
# ---------------------------------------------------------------------------


async def test_ein_kennzeichenwechsel_schliesst_die_laufende_zeile_und_oeffnet_eine_neue(
    kopf: dict[str, str],
) -> None:
    """Die alte Zeile endet am Vortag des neuen Beginns - luecken- und
    ueberschneidungsfrei.

    Die Zaehlung der offenen Zeilen laeuft absichtlich an der API vorbei: die
    Liste zeigt nur, welche Zeile ANGEZEIGT wird; der Partialindex verspricht
    aber, dass es nie zwei GIBT.
    """
    objekt = _objekt(kopf, label="Kombi", acquired_on="2026-03-01")
    halter = _person(kopf, "Anna Beispiel")

    erste = client.post(
        f"{BASIS}/objects/{objekt['id']}/registrations",
        json={"plate": "N-AB 123", "keeper_person_id": halter["id"], "valid_from": "2026-03-10"},
        headers=kopf,
    )
    assert erste.status_code == 201, erste.text
    assert erste.json()["valid_to"] is None
    assert erste.json()["keeper_name"] == "Anna Beispiel"
    aktuell = client.get(f"{BASIS}/objects/{objekt['id']}", headers=kopf).json()
    assert aktuell["current_plate"] == "N-AB 123"
    assert aktuell["current_keeper_name"] == "Anna Beispiel"

    zweite = client.post(
        f"{BASIS}/objects/{objekt['id']}/registrations",
        json={"plate": "N-XY 9", "valid_from": "2026-07-01"},
        headers=kopf,
    )
    assert zweite.status_code == 201, zweite.text

    historie = client.get(f"{BASIS}/objects/{objekt['id']}/registrations", headers=kopf).json()
    assert [zeile["plate"] for zeile in historie] == ["N-XY 9", "N-AB 123"]
    assert historie[0]["valid_to"] is None
    assert historie[1]["valid_to"] == "2026-06-30"
    assert await _offene_kennzeichenzeilen(str(objekt["id"])) == 1

    danach = client.get(f"{BASIS}/objects/{objekt['id']}", headers=kopf).json()
    assert danach["current_plate"] == "N-XY 9"
    # Der Halter der alten Zeile bleibt an ihr haengen und wandert nicht mit.
    assert danach["current_keeper_person_id"] is None


def test_ein_rueckdatierter_eintrag_wird_zurueckgewiesen_statt_still_korrigiert(
    kopf: dict[str, str],
) -> None:
    """Sonst muesste die laufende Zeile am Tag vor ihrem eigenen Beginn enden.

    Die Historie soll eine Reihenfolge behaupten, die sie auch halten kann -
    deshalb eine Rueckfrage und keine stille Korrektur.
    """
    objekt = _objekt(kopf, label="Kombi")
    client.post(
        f"{BASIS}/objects/{objekt['id']}/registrations",
        json={"plate": "N-AB 123", "valid_from": "2026-03-10"},
        headers=kopf,
    )

    rueckdatiert = client.post(
        f"{BASIS}/objects/{objekt['id']}/registrations",
        json={"plate": "N-XY 9", "valid_from": "2026-02-01"},
        headers=kopf,
    )
    assert rueckdatiert.status_code == 422, rueckdatiert.text
    assert rueckdatiert.json()["detail"] == "insurance_registration_out_of_order"

    gleicher_tag = client.post(
        f"{BASIS}/objects/{objekt['id']}/registrations",
        json={"plate": "N-XY 9", "valid_from": "2026-03-10"},
        headers=kopf,
    )
    assert gleicher_tag.status_code == 422, gleicher_tag.text

    leer = client.post(
        f"{BASIS}/objects/{objekt['id']}/registrations",
        json={"valid_from": "2026-08-01"},
        headers=kopf,
    )
    assert leer.status_code == 422, leer.text

    unbekannter_halter = client.post(
        f"{BASIS}/objects/{objekt['id']}/registrations",
        json={"keeper_person_id": str(uuid.uuid4()), "valid_from": "2026-08-01"},
        headers=kopf,
    )
    assert unbekannter_halter.status_code == 404, unbekannter_halter.text
    assert unbekannter_halter.json()["detail"] == "insurance_person_not_found"

    historie = client.get(f"{BASIS}/objects/{objekt['id']}/registrations", headers=kopf).json()
    assert [zeile["plate"] for zeile in historie] == ["N-AB 123"]


# ---------------------------------------------------------------------------
# 4. Deckungsbausteine
# ---------------------------------------------------------------------------


def test_put_ersetzt_die_bausteinmenge_vollstaendig(kopf: dict[str, str]) -> None:
    """Was nicht im Rumpf steht, ist danach weg - bis hin zur leeren Liste.

    Bestehende Zeilen werden dabei aktualisiert und nicht geloescht und neu
    angelegt: wer wissen will, seit wann ein Baustein erfasst ist, soll es nach
    jedem Speichern noch beantwortet bekommen. Genau deshalb prueft der Test
    `created_at` und nicht nur die Schluesselmenge.
    """
    police = _police(kopf)
    erste = client.put(
        f"{BASIS}/policies/{police['id']}/coverages",
        json={
            "coverages": [
                {"coverage_key": "glass", "is_included": True, "sum_insured_minor": 500000},
                {"coverage_key": "elemental", "is_included": False},
                {"coverage_key": "bicycle", "is_included": True, "deductible_minor": 15000},
            ]
        },
        headers=kopf,
    )
    assert erste.status_code == 200, erste.text
    vorher = {zeile["coverage_key"]: zeile for zeile in erste.json()}
    assert set(vorher) == {"glass", "elemental", "bicycle"}
    # Der Katalogtext kommt mit - die Liste zeigt Baustein und Zweck in einer Zeile.
    assert vorher["glass"]["label"] == "Glasbruch"
    assert vorher["glass"]["purpose_short"]
    assert vorher["elemental"]["is_included"] is False

    zweite = client.put(
        f"{BASIS}/policies/{police['id']}/coverages",
        json={
            "coverages": [
                {"coverage_key": "glass", "is_included": True, "sum_insured_minor": 800000},
                {"coverage_key": "gross_negligence"},
            ]
        },
        headers=kopf,
    )
    assert zweite.status_code == 200, zweite.text
    nachher = {zeile["coverage_key"]: zeile for zeile in zweite.json()}
    assert set(nachher) == {"glass", "gross_negligence"}
    assert nachher["glass"]["sum_insured_minor"] == 800000
    assert nachher["glass"]["created_at"] == vorher["glass"]["created_at"]
    assert nachher["glass"]["updated_at"] >= vorher["glass"]["updated_at"]

    geleert = client.put(
        f"{BASIS}/policies/{police['id']}/coverages", json={"coverages": []}, headers=kopf
    )
    assert geleert.status_code == 200, geleert.text
    assert geleert.json() == []
    assert client.get(f"{BASIS}/policies/{police['id']}/coverages", headers=kopf).json() == []


def test_erfasste_bausteine_ueberschreiben_die_katalogaussage_samt_herkunft(
    kopf: dict[str, str],
) -> None:
    """Die staerkere Aussage gewinnt - und sagt, dass sie es ist.

    Der Katalog beschreibt die SPARTE ("typischerweise"), ein erfasster Baustein
    beschreibt DIESE Police. Sobald einer da ist, muss `coverage_source` auf
    'policy' kippen und die beiden Spalten aus den erfassten Zeilen kommen; die
    Oberflaeche haengt ihre Ueberschriften genau daran und nicht an einem
    eigenen Zaehlen.

    `is_included = false` ist dabei eine eigene Aussage und landet in der
    zweiten Spalte - "nicht erfasst" und "ausdruecklich nicht mitversichert"
    duerfen nicht dieselbe Zeile ergeben.

    Der Vorbehalt bleibt: er gilt weiter fuer `purpose_long`, den kein Baustein
    ersetzt. Und das Leeren der Bausteinmenge gibt das Wort an den Katalog
    zurueck, sonst behauptete eine leere Police, ueber sich selbst zu sprechen.
    """
    police = _police(kopf)
    aus_katalog = client.get(f"{BASIS}/policies/{police['id']}/purpose", headers=kopf).json()
    assert aus_katalog["coverage_source"] == "catalog"
    katalogspalte = list(aus_katalog["typically_covers"])
    assert katalogspalte

    gespeichert = client.put(
        f"{BASIS}/policies/{police['id']}/coverages",
        json={
            "coverages": [
                {"coverage_key": "glass", "is_included": True},
                {"coverage_key": "elemental", "is_included": False},
            ]
        },
        headers=kopf,
    )
    assert gespeichert.status_code == 200, gespeichert.text

    aus_police = client.get(f"{BASIS}/policies/{police['id']}/purpose", headers=kopf).json()
    assert aus_police["coverage_source"] == "policy"
    # Die Beschriftung kommt aus dem Bausteinkatalog und wird nicht aus dem
    # Schluessel gebaut.
    assert aus_police["typically_covers"] == ["Glasbruch"]
    assert aus_police["typically_not_covers"] == ["Elementarschaeden"]
    assert aus_police["typically_covers"] != katalogspalte
    assert aus_police["purpose_long"] == aus_katalog["purpose_long"]
    assert "kann abweichen" in aus_police["caveat"]

    client.put(
        f"{BASIS}/policies/{police['id']}/coverages", json={"coverages": []}, headers=kopf
    )
    zurueck = client.get(f"{BASIS}/policies/{police['id']}/purpose", headers=kopf).json()
    assert zurueck["coverage_source"] == "catalog"
    assert zurueck["typically_covers"] == katalogspalte


def test_ein_unbekannter_baustein_nennt_sich_beim_namen(kopf: dict[str, str]) -> None:
    """`covered_by_keys` und `applies_to` sind Textspalten ohne Fremdschluessel -
    ein Tippfehler waere sonst still. Die Antwort nennt deshalb die abgelehnten
    Schluessel und nicht nur einen Fehlercode.
    """
    police = _police(kopf)
    unbekannt = client.put(
        f"{BASIS}/policies/{police['id']}/coverages",
        json={"coverages": [{"coverage_key": "glass"}, {"coverage_key": "gibt_es_nicht"}]},
        headers=kopf,
    )
    assert unbekannt.status_code == 422, unbekannt.text
    detail = unbekannt.json()["detail"]
    assert detail["code"] == "insurance_coverage_key_unknown"
    assert detail["coverage_keys"] == ["gibt_es_nicht"]
    # Nichts davon wurde gespeichert - der Rundlauf ist ganz oder gar nicht.
    assert client.get(f"{BASIS}/policies/{police['id']}/coverages", headers=kopf).json() == []

    doppelt = client.put(
        f"{BASIS}/policies/{police['id']}/coverages",
        json={"coverages": [{"coverage_key": "glass"}, {"coverage_key": "glass"}]},
        headers=kopf,
    )
    assert doppelt.status_code == 422, doppelt.text


# ---------------------------------------------------------------------------
# 5. Buendel
# ---------------------------------------------------------------------------


def test_eine_kindpolice_teilt_die_scheinnummer_eine_unabhaengige_police_nicht(
    kopf: dict[str, str],
) -> None:
    """Der Buendelfall ist der Grund fuer den Indexwechsel.

    Kfz-Haftpflicht und Vollkasko stehen beim selben Versicherer unter EINER
    Versicherungsscheinnummer. Eindeutig ist die Nummer deshalb nur noch unter
    Wurzelpolicen - und das Aufloesen des Buendels macht die Kindpolice wieder
    zur Wurzel, weshalb es an derselben Bedingung scheitert.
    """
    nummer = "NW-2026-4711"
    haupt = _police(
        kopf, line_key="motor_liability", insurer_name="Nordwacht AG", policy_number=nummer
    )

    unabhaengig = client.post(
        f"{BASIS}/policies",
        json={
            "line_key": "motor_comprehensive",
            "insurer_name": "Nordwacht AG",
            "policy_number": nummer,
        },
        headers=kopf,
    )
    assert unabhaengig.status_code == 409, unabhaengig.text
    assert unabhaengig.json()["detail"] == "insurance_policy_number_duplicate"

    kind = _police(kopf, line_key="motor_comprehensive", insurer_name="Nordwacht AG")
    gebuendelt = client.post(
        f"{BASIS}/policies/{kind['id']}/bundle",
        json={"parent_policy_id": haupt["id"]},
        headers=kopf,
    )
    assert gebuendelt.status_code == 200, gebuendelt.text
    assert gebuendelt.json()["parent_policy_id"] == haupt["id"]
    assert gebuendelt.json()["hint"]

    geteilt = client.patch(
        f"{BASIS}/policies/{kind['id']}",
        json={"policy_number": nummer},
        headers={**kopf, "If-Match": _policy_etag(kopf, str(kind["id"]))},
    )
    assert geteilt.status_code == 200, geteilt.text
    assert geteilt.json()["policy_number"] == nummer

    aufgeloest = client.post(
        f"{BASIS}/policies/{kind['id']}/bundle",
        json={"parent_policy_id": None},
        headers=kopf,
    )
    assert aufgeloest.status_code == 409, aufgeloest.text
    assert aufgeloest.json()["detail"] == "insurance_policy_number_duplicate"
    # Und die Zuordnung steht unveraendert - eine abgelehnte Aufloesung darf
    # das Buendel nicht halb zerlegen.
    assert (
        client.post(
            f"{BASIS}/policies/{kind['id']}/bundle",
            json={"parent_policy_id": haupt["id"]},
            headers=kopf,
        ).status_code
        == 200
    )


def test_ein_buendel_hat_genau_eine_ebene(kopf: dict[str, str]) -> None:
    """Kein Baum, in beide Richtungen geprueft.

    Der Nummern-Index laesst genau die Zeilen mit `parent_policy_id is null`
    zu; ein Enkel truege dieselbe Nummer wie sein Grossvater, ohne dass der
    Index ihn sieht. Die Gegenrichtung ist genauso wichtig: dieselbe zweite
    Ebene entstuende sonst ueber den Umweg "eine Wurzel mit Kindern bekommt
    nachtraeglich selbst einen Elternteil".
    """
    haupt = _police(kopf, line_key="motor_liability", insurer_name="Nordwacht AG")
    kind = _police(kopf, line_key="motor_comprehensive", insurer_name="Nordwacht AG")
    enkel = _police(kopf, line_key="passenger_accident", insurer_name="Nordwacht AG")
    andere_wurzel = _police(kopf, line_key="household_contents", insurer_name="Suedwacht AG")

    assert (
        client.post(
            f"{BASIS}/policies/{kind['id']}/bundle",
            json={"parent_policy_id": haupt["id"]},
            headers=kopf,
        ).status_code
        == 200
    )

    zu_tief = client.post(
        f"{BASIS}/policies/{enkel['id']}/bundle",
        json={"parent_policy_id": kind["id"]},
        headers=kopf,
    )
    assert zu_tief.status_code == 422, zu_tief.text
    assert zu_tief.json()["detail"] == "insurance_policy_nesting_too_deep"

    abwaerts = client.post(
        f"{BASIS}/policies/{haupt['id']}/bundle",
        json={"parent_policy_id": andere_wurzel["id"]},
        headers=kopf,
    )
    assert abwaerts.status_code == 422, abwaerts.text
    assert abwaerts.json()["detail"] == "insurance_policy_nesting_too_deep"

    auf_sich_selbst = client.post(
        f"{BASIS}/policies/{haupt['id']}/bundle",
        json={"parent_policy_id": haupt["id"]},
        headers=kopf,
    )
    assert auf_sich_selbst.status_code == 422, auf_sich_selbst.text
    assert auf_sich_selbst.json()["detail"] == "insurance_policy_self_reference"

    fremde_police = client.post(
        f"{BASIS}/policies/{enkel['id']}/bundle",
        json={"parent_policy_id": str(uuid.uuid4())},
        headers=kopf,
    )
    assert fremde_police.status_code == 404, fremde_police.text


def test_die_buendelzuordnung_ist_nach_dem_neuladen_noch_da(kopf: dict[str, str]) -> None:
    """Eine Zuordnung, die man nur im Moment des Setzens sieht, ist keine.

    Stuende `parent_policy_id` allein in der Antwort von `/bundle`, waere nach
    dem naechsten Laden nicht mehr erkennbar, warum zwei Policen dieselbe
    Scheinnummer tragen - und das Aufloesen des Buendels waere eine Aktion ohne
    sichtbaren Gegenstand. Der Wert kommt deshalb aus der Zeile: an der
    Wurzelpolice steht `null`, an der Kindpolice die Wurzel, in Liste UND
    Detail.
    """
    haupt = _police(kopf, line_key="motor_liability", insurer_name="Nordwacht AG")
    kind = _police(kopf, line_key="motor_comprehensive", insurer_name="Nordwacht AG")
    client.post(
        f"{BASIS}/policies/{kind['id']}/bundle",
        json={"parent_policy_id": haupt["id"]},
        headers=kopf,
    )

    detail = client.get(f"{BASIS}/policies/{kind['id']}", headers=kopf)
    assert detail.status_code == 200, detail.text
    assert detail.json()["policy"]["parent_policy_id"] == haupt["id"]
    assert detail.json()["policy"]["is_health_scoped"] is False

    liste = {
        str(zeile["id"]): zeile for zeile in client.get(f"{BASIS}/policies", headers=kopf).json()
    }
    assert liste[str(kind["id"])]["parent_policy_id"] == haupt["id"]
    assert liste[str(haupt["id"])]["parent_policy_id"] is None

    # Und die Gesundheitsflagge kommt aus der Zeile, nicht aus einer Herleitung
    # der Oberflaeche - an ihr haengt der Hinweis vor dem ersten Personenbezug.
    gesundheit = _police(kopf, line_key="dental_supplementary", insurer_name="Zahnkasse AG")
    gesundheitsdetail = client.get(f"{BASIS}/policies/{gesundheit['id']}", headers=kopf)
    assert gesundheitsdetail.json()["policy"]["is_health_scoped"] is True


# ---------------------------------------------------------------------------
# 6. Katalogwelle 2
# ---------------------------------------------------------------------------


def test_die_zweite_welle_steht_vollstaendig_und_gepflegt_im_katalog(
    kopf: dict[str, str],
) -> None:
    """Achtzehn benannte Zeilen, jede mit Bedarfsstufe, Grund, Quelle und
    Pruefdatum.

    Ohne Pruefdatum veraltet eine Wertung still; eine Bedarfsstufe ohne
    Begruendung waere eine Behauptung. Der Test nennt die Schluessel einzeln:
    eine reine Mengenangabe waere auch dann gruen, wenn eine Sparte gegen eine
    voellig andere getauscht wuerde.
    """
    sparten = _sparten(kopf)
    fehlend = [schluessel for schluessel in WELLE_2 if schluessel not in sparten]
    assert fehlend == []

    for schluessel in WELLE_2:
        zeile = sparten[schluessel]
        assert zeile["is_placeholder"] is False, schluessel
        assert zeile["need_tier"] in {"S0", "S1", "S2", "S3", "S4"}, schluessel
        assert zeile["need_reason"], schluessel
        assert str(zeile["source_url"]).startswith("https://"), schluessel
        assert zeile["source_checked_on"], schluessel
        assert zeile["default_deadline_kind"], schluessel
        assert zeile["default_anchor_basis_kind"] in {"statute", "policy_terms", "contract"}
        assert zeile["typically_covers"], schluessel
        assert zeile["typically_not_covers"], schluessel

    # Die Sortierung bleibt aufsteigend - die Welle wird angehaengt und schiebt
    # sich nicht zwischen die Zeilen der Welle K.
    reihenfolge = [zeile["sort_order"] for zeile in sparten.values()]
    assert reihenfolge == sorted(reihenfolge)
    assert len({zeile["sort_order"] for zeile in sparten.values()}) == len(sparten)


def test_die_zweite_welle_haelt_die_schreibregel_der_zwecktexte_ein(
    kopf: dict[str, str],
) -> None:
    """Dieselbe Wortliste wie in test_insurance_catalog.py - importiert und
    nicht abgeschrieben.

    Zwei Listen driften auseinander, und die zweite ist dann die, die nichts
    mehr findet. Geprueft wird mit Wortgrenzen: "besteht" enthaelt "beste".
    """
    sparten = _sparten(kopf)
    treffer: list[str] = []
    for schluessel in WELLE_2:
        zeile = sparten[schluessel]
        text_ = f"{zeile['purpose_short']} {zeile['purpose_long']}"
        for wendung in VERBOTENE_WENDUNGEN:
            if re.search(rf"\b{re.escape(wendung)}\b", text_, re.IGNORECASE):
                treffer.append(f"{schluessel}: '{wendung}'")
        # Regel 5: purpose_short ist genau ein Satz.
        saetze = [
            teil for teil in re.split(r"(?<=[.!?])\s+", str(zeile["purpose_short"]).strip()) if teil
        ]
        assert len(saetze) == 1, f"{schluessel}: {zeile['purpose_short']}"
        assert str(zeile["purpose_short"]).strip().endswith("."), schluessel
        # Regel 3: eine Pflichtaussage nur mit Fundstelle im selben Text.
        if any(re.search(rf"\b{wendung}", text_, re.IGNORECASE) for wendung in PFLICHTWENDUNGEN):
            assert "Paragraph" in text_, schluessel
        assert len(str(zeile["purpose_long"])) >= 40, schluessel
    assert treffer == []


def test_die_zweite_welle_bindet_ihre_sparten_an_objekte_und_haelt_den_einzigen_stichtag(
    kopf: dict[str, str],
) -> None:
    """Die Welle kommt mit P4 und nicht frueher, weil ihre Sparten ein Objekt
    verlangen - `requires_object_kind` ist der Grund fuer diese Reihenfolge.

    `fixed_date` ist im ganzen Katalog ausschliesslich bei `moped` gesetzt: das
    Versicherungsjahr des Versicherungskennzeichens laeuft vom 1. Maerz bis zum
    Ende des Februars. Jeder weitere feste Stichtag waere eine Frist, die kein
    Vertrag hergibt - deshalb prueft der Test alle Zeilen und nicht nur die neuen.
    """
    sparten = _sparten(kopf)

    assert sparten["motor_comprehensive"]["requires_object_kind"] == "vehicle"
    assert sparten["animal_liability"]["requires_object_kind"] == "pet"
    assert sparten["photovoltaic"]["requires_object_kind"] == "property"
    assert sparten["moped"]["requires_object_kind"] == "vehicle"

    mit_stichtag = [
        schluessel
        for schluessel, zeile in sparten.items()
        if zeile["default_notice_anchor"] == "fixed_date"
    ]
    assert mit_stichtag == ["moped"]

    for schluessel, zeile in sparten.items():
        assert zeile["default_main_due_md"] != "11-30", schluessel
        if zeile["default_notice_period_months"] is not None:
            assert 1 <= zeile["default_notice_period_months"] <= 3, schluessel

    # Und die neuen Sparten sind nicht nur Text: eine Police laesst sich darauf
    # anlegen, ohne dass eine Fristvorgabe des Katalogs sie unspeicherbar macht.
    # `moped` steht hier nicht dabei - genau daran scheitert es heute, siehe den
    # folgenden Test.
    for schluessel in WELLE_2:
        if schluessel == "moped":
            continue
        angelegt = client.post(
            f"{BASIS}/policies",
            json={"line_key": schluessel, "insurer_name": f"Versicherer {schluessel}"},
            headers=kopf,
        )
        assert angelegt.status_code == 201, f"{schluessel}: {angelegt.text}"
        assert angelegt.json()["line_key"] == schluessel
        assert angelegt.json()["deadline"]["certainty"] in {
            "confirmed",
            "assumed",
            "not_computable",
        }


def test_eine_mopedpolice_ist_ueber_die_api_erfassbar(kopf: dict[str, str]) -> None:
    """Das Versicherungskennzeichen ist der Fall, fuer den es `fixed_date` gibt.

    Das Versicherungsjahr laeuft vom 1. Maerz bis zum Ende des Februars
    (Paragraph 26 FZV); der Katalog traegt dafuer 02-28 als festen Stichtag. Die
    Police muss diesen Stichtag mitbekommen - `insurance_policies_fixed_date_pair`
    verlangt Monat UND Tag, sobald der Anker `fixed_date` ist, und der Rechenkern
    liest sie aus der Policenzeile.

    Beide Wege sind betroffen, und der zweite ist der schwerere: `confirm-line`
    ist der Weg jeder aus einem Vertrag uebernommenen Police ("Sparte zuordnen").
    Ein verletzter CHECK darf dort keinen Serverfehler ergeben, sondern eine
    Rueckfrage.
    """
    angelegt = client_ohne_serverfehler.post(
        f"{BASIS}/policies",
        json={"line_key": "moped", "insurer_name": "Rollerkasse AG"},
        headers=kopf,
    )
    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["notice_anchor"] == "fixed_date"

    uebernommen = _police(kopf, line_key="unknown", insurer_name="Rollerkasse AG")
    zugeordnet = client_ohne_serverfehler.post(
        f"{BASIS}/policies/{uebernommen['id']}/confirm-line",
        json={"line_key": "moped"},
        headers=kopf,
    )
    assert zugeordnet.status_code == 200, zugeordnet.text


def test_der_stichtag_gehoert_zur_sparte_und_nicht_zum_anker(kopf: dict[str, str]) -> None:
    """`fixed_date` ist an genau eine Sparte gebunden (Plan 3.12).

    Wer den Anker an einer Sparte ohne Kalenderfixpunkt verlangt, verlangt einen
    Stichtag, den niemand nennen kann - das muss eine Auskunft sein und darf
    kein Serverfehler aus einem verletzten CHECK werden.
    """
    abgelehnt = client_ohne_serverfehler.post(
        f"{BASIS}/policies",
        json={
            "line_key": "household_contents",
            "insurer_name": "Hausrat AG",
            "notice_anchor": "fixed_date",
        },
        headers=kopf,
    )
    assert abgelehnt.status_code == 422, abgelehnt.text
    assert abgelehnt.json()["detail"] == "insurance_fixed_date_unknown"


def test_eine_sparte_der_zweiten_welle_traegt_ihren_zweck_bis_an_die_police(
    kopf: dict[str, str],
) -> None:
    """Der Zwecktext beantwortet die Ausgangsfrage des Moduls - auch fuer die
    neuen Sparten und mit demselben Vorbehalt.

    Solange keine Bausteine erfasst sind, beschreibt der Text die SPARTE und
    nie die Police; sobald welche erfasst sind, beschreibt die Police ihren
    Umfang selbst. Der Vorbehalt steht im Vertrag und nicht nur in der
    Oberflaeche.
    """
    police = _police(kopf, line_key="animal_liability", insurer_name="Tierkasse AG")
    zweck = client.get(f"{BASIS}/policies/{police['id']}/purpose", headers=kopf)
    assert zweck.status_code == 200, zweck.text
    assert zweck.json()["coverage_source"] == "catalog"
    assert "kann abweichen" in zweck.json()["caveat"]
    assert zweck.json()["typically_covers"]
    assert str(zweck.json()["source_url"]).startswith("https://")
    assert date.fromisoformat(str(zweck.json()["source_checked_on"])).year >= 2026
