"""Was von der Fristenrechnung tatsaechlich bis zum Nutzer durchkommt (P2).

Die Faelle in `test_insurance_holidays.py` pruefen den Rechenkern direkt. Das
reicht nicht: der Kern kann richtig rechnen und die Ansicht trotzdem etwas
anderes zeigen, wenn ein Aufrufer eine Angabe nicht weiterreicht. Genau das war
der Fall - `insurance_service._deadline` liess `recipient_state` und die
Mindestbindung weg, und dieselbe Police nannte auf /fixkosten und auf
/versicherungen verschiedene Kuendigungstermine.

Diese Datei prueft deshalb ueber die API, nicht ueber die Funktion.

Die Feiertagsfaelle suchen ihren Tag zur Laufzeit im Kalender, statt ein Datum
fest zu verdrahten: Fronleichnam wandert mit Ostern, und ein hart notiertes
Jahr machte die Datei ab dem naechsten Juni rot. Gesucht wird ein Werktag, der
in einem Bundesland Feiertag ist und in einem anderen nicht - fiele er auf ein
Wochenende, bewiese der Test das Wochenende und nicht den Feiertag.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.holiday_calendar import holiday_on
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

BASIS = "/v1/insurance"

# Zwei Laender mit deutlich verschiedenem Kalender: Bayern traegt die
# katholischen Tage (Fronleichnam, Mariae Himmelfahrt, Allerheiligen), Berlin
# keinen davon.
LAND_MIT = "BY"
LAND_OHNE = "BE"


def _haushalt(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    haushalt = client.post(
        "/v1/households",
        json={"name": "Fristen-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": str(haushalt["id"])}


@pytest.fixture()
def kopf() -> dict[str, str]:
    return _haushalt("frist")


def _police(kopf: dict[str, str], **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "line_key": "private_liability",
        "insurer_name": "Beispiel Versicherung AG",
    }
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/policies", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _bestaetigte_police(kopf: dict[str, str], main_due_on: date, **felder: Any) -> dict[str, Any]:
    """Police mit bestaetigter Hauptfaelligkeit.

    Ohne die Bestaetigung bliebe die Frist eine Annahme; dann sagte der Test
    nichts ueber den Kalender aus, sondern nur ueber Leitsatz 1.
    """
    police = _police(kopf, main_due_on=main_due_on.isoformat(), **felder)
    antwort = client.post(
        f"{BASIS}/policies/{police['id']}/confirm-main-due",
        json={"main_due_on": main_due_on.isoformat()},
        headers=kopf,
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _frist(kopf: dict[str, str], policy_id: str) -> dict[str, Any]:
    antwort = client.get(f"{BASIS}/policies/{policy_id}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json()["policy"]["deadline"])


def _landesfeiertag_werktag(ab: date) -> date:
    """Erster Werktag ab `ab`, der in BY Feiertag ist und in BE nicht.

    Sucht bis zu zwei Jahre voraus - Fronleichnam und Mariae Himmelfahrt
    kommen jaehrlich, ein leeres Ergebnis waere ein Kalenderfehler.
    """
    tag = ab
    for _ in range(760):
        if (
            tag.weekday() < 5
            and holiday_on(tag, LAND_MIT) is not None
            and holiday_on(tag, LAND_OHNE) is None
        ):
            return tag
        tag += timedelta(days=1)
    raise AssertionError("kein Landesfeiertag auf einem Werktag gefunden")


# ---------------------------------------------------------------------------
# Zusage D - der Feiertagskalender wirkt dort, wo der Nutzer hinsieht
# ---------------------------------------------------------------------------


def test_ein_landesfeiertag_verschiebt_den_zustelltag_nur_im_richtigen_bundesland(
    kopf: dict[str, str],
) -> None:
    """Zusage D, gemessen an der API und nicht am Rechenkern.

    Beide Policen sind bis auf das Empfaengerland gleich. Verschoebe der
    Feiertag beide oder keine, waere `recipient_state` eine Spalte, die
    gespeichert und angezeigt, aber nie gerechnet wird - genau der Zustand vor
    dieser Abnahme.
    """
    feiertag = _landesfeiertag_werktag(date.today() + timedelta(days=1))
    # Bei Anker 'period_end' endet die Periode am Tag vor ihrem naechsten
    # Beginn - die Hauptfaelligkeit muss also einen Tag nach dem Feiertag
    # liegen, damit das rechtliche Fristende genau auf ihn faellt.
    hauptfaelligkeit = feiertag + timedelta(days=1)

    in_bayern = _bestaetigte_police(
        kopf, hauptfaelligkeit, notice_anchor="period_end", recipient_state=LAND_MIT
    )
    in_berlin = _bestaetigte_police(
        kopf, hauptfaelligkeit, notice_anchor="period_end", recipient_state=LAND_OHNE
    )

    bayern = _frist(kopf, in_bayern["id"])
    berlin = _frist(kopf, in_berlin["id"])

    # Dasselbe rechtliche Fristende: Paragraph 193 BGB gilt fuer
    # Kuendigungsfristen nicht, der Feiertag verschiebt es also in keinem Land.
    assert bayern["deadline_legal_on"] == feiertag.isoformat()
    assert berlin["deadline_legal_on"] == feiertag.isoformat()

    # Aber nur in Bayern wird an diesem Tag nicht zugestellt.
    assert berlin["safe_service_on"] == feiertag.isoformat()
    assert bayern["safe_service_on"] < feiertag.isoformat()


def test_die_frist_benennt_den_feiertag_der_sie_verschoben_hat(kopf: dict[str, str]) -> None:
    """Der Name kommt vom Server, damit die Oberflaeche keinen zweiten
    Feiertagskalender braucht - zwei Kalender heissen zwei Termine."""
    feiertag = _landesfeiertag_werktag(date.today() + timedelta(days=1))
    erwartet = holiday_on(feiertag, LAND_MIT)
    assert erwartet is not None

    in_bayern = _bestaetigte_police(
        kopf,
        feiertag + timedelta(days=1),
        notice_anchor="period_end",
        recipient_state=LAND_MIT,
    )
    in_berlin = _bestaetigte_police(
        kopf,
        feiertag + timedelta(days=1),
        notice_anchor="period_end",
        recipient_state=LAND_OHNE,
    )

    assert _frist(kopf, in_bayern["id"])["safe_service_holiday"] == erwartet.name
    # Wo nichts verschoben wurde, wird auch nichts benannt - ein Feiertagsname
    # ohne Wirkung liesse eine Verschiebung vermuten, die es nicht gab.
    assert _frist(kopf, in_berlin["id"])["safe_service_holiday"] is None


def test_ohne_bundesland_zaehlen_nur_die_bundesweiten_tage(kopf: dict[str, str]) -> None:
    """Ein fehlendes Empfaengerland ist kein Grund zu raten - es ist der
    Grund, den kleineren Kalender zu nehmen."""
    feiertag = _landesfeiertag_werktag(date.today() + timedelta(days=1))
    police = _bestaetigte_police(
        kopf, feiertag + timedelta(days=1), notice_anchor="period_end", recipient_state=None
    )

    frist = _frist(kopf, police["id"])
    assert frist["safe_service_on"] == feiertag.isoformat()
    assert frist["safe_service_holiday"] is None


# ---------------------------------------------------------------------------
# Mindestbindung
# ---------------------------------------------------------------------------


def test_die_mindestbindung_verschiebt_den_kuendigungstermin_in_der_ansicht(
    kopf: dict[str, str],
) -> None:
    """Eine vereinbarte Bindung darf die Ansicht nicht uebergehen.

    Ohne sie nennte die Detailseite den naechsten Jahrestermin - einen Termin,
    den der Versicherer zurueckweisen wird.
    """
    beginn = date.today().replace(month=1, day=1)
    hauptfaelligkeit = date(beginn.year + 1, 1, 1)

    ohne_bindung = _bestaetigte_police(
        kopf,
        hauptfaelligkeit,
        notice_anchor="term_end",
        notice_period_months=3,
        starts_on=beginn.isoformat(),
    )
    mit_bindung = _bestaetigte_police(
        kopf,
        hauptfaelligkeit,
        notice_anchor="term_end",
        notice_period_months=3,
        starts_on=beginn.isoformat(),
        min_term_months=36,
    )

    frei = _frist(kopf, ohne_bindung["id"])
    gebunden = _frist(kopf, mit_bindung["id"])

    assert frei["term_ends_on"] is not None
    assert gebunden["term_ends_on"] is not None
    # Drei Jahre Bindung ab dem 01.01. enden am 31.12. des dritten Jahres; der
    # erste kuendbare Termin liegt danach.
    assert gebunden["term_ends_on"] >= date(beginn.year + 2, 12, 31).isoformat()
    assert gebunden["term_ends_on"] > frei["term_ends_on"]
