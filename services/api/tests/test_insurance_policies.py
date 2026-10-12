"""Policenregister ueber die API (docs/VERSICHERUNGEN-PLAN.md, Phase P1).

Geprueft werden hier die vier Leitsaetze des Moduls, nicht die Felder:

1. Das Modul modelliert kein Geld. Es gibt genau drei Geldfaelle - Vertrag,
   Fremdbetrag, keiner - und der vierte ist nicht erfassbar.
2. Jedes Datum traegt seine Herkunft, und der Client kann sie nicht setzen.
   `main_due_source = 'user_confirmed'` entsteht ausschliesslich ueber die
   Einfeldaktion, die dabei die Sitzungsvariable setzt.
3. Ein angenommenes Datum bekommt nie einen Countdown.
4. Wo eine Angabe fehlt, sagt das Werkzeug das - es erfindet kein Datum.

Laeuft wie alle API-Tests gegen ein echtes lokales Supabase. Die Tests rechnen
bewusst relativ zum heutigen Tag: die Routen setzen `today = date.today()`, und
ein fest verdrahtetes Jahr liesse die Suite an einem Silvester rot werden.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

BASIS = "/v1/insurance"


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


def _haushalt(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    haushalt = client.post(
        "/v1/households",
        json={"name": "Versicherungs-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": str(haushalt["id"])}


@pytest.fixture()
def kopf() -> dict[str, str]:
    """Function-scoped mit Absicht: das autouse-Aufraeumen entfernt nach jedem
    Test die dabei entstandenen Haushalte, ein module-scoped Haushalt waere ab
    dem zweiten Test verschwunden."""
    return _haushalt("ins")


def _police(kopf: dict[str, str], **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "line_key": "private_liability",
        "insurer_name": "Beispiel Versicherung AG",
    }
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/policies", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _vertrag(kopf: dict[str, str], **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "name": "Haftpflicht Beispiel",
        "amount_minor": 1999,
        "frequency": "monthly",
        "next_due_date": date.today().isoformat(),
        "contract_type": "insurance",
    }
    koerper.update(felder)
    antwort = client.post("/v1/contracts", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _detail(kopf: dict[str, str], policy_id: str) -> dict[str, Any]:
    antwort = client.get(f"{BASIS}/policies/{policy_id}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _hole(kopf: dict[str, str], policy_id: str) -> dict[str, Any]:
    return dict(_detail(kopf, policy_id)["policy"])


def _etag(kopf: dict[str, str], policy_id: str) -> str:
    antwort = client.get(f"{BASIS}/policies/{policy_id}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return str(antwort.headers["ETag"])


def _bestaetige_hauptfaelligkeit(
    kopf: dict[str, str], policy_id: str, koerper: dict[str, Any] | None = None
) -> dict[str, Any]:
    antwort = client.post(
        f"{BASIS}/policies/{policy_id}/confirm-main-due", json=koerper, headers=kopf
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


async def _spalten(policy_id: str, spalten: str) -> Any:
    """Liest Spalten direkt aus der Datenbank.

    Noetig fuer alles, was die API bewusst nicht herausgibt -
    `main_due_confirmed_by` etwa steht in keinem Antwortmodell, ist aber die
    Bedingung, unter der die Bestaetigung ueberhaupt speicherbar ist."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        ergebnis = await session.execute(
            text(f"select {spalten} from insurance_policies where id = :id"),
            {"id": policy_id},
        )
        return ergebnis.first()


# ---------------------------------------------------------------------------
# 1. Die nackte Police
# ---------------------------------------------------------------------------


def test_police_braucht_nur_sparte_und_versicherer(kopf: dict[str, str]) -> None:
    """Der haeufigste Erfassungsfall - und er darf kein Formular erzwingen.

    Ohne Hauptfaelligkeit gibt es keine Frist. Das ist eine Aussage ("Angabe
    fehlt"), kein Fehler: eine Police, die sich nur vollstaendig erfassen
    laesst, wird gar nicht erfasst.
    """
    police = _police(kopf)

    assert police["main_due_on"] is None
    assert police["main_due_source"] == "not_computable"
    assert police["main_due_confirmed_at"] is None
    frist = police["deadline"]
    assert frist["certainty"] == "not_computable"
    assert frist["deadline_legal_on"] is None
    assert frist["safe_service_on"] is None
    assert frist["days_left"] is None
    # Kein leeres Feld, sondern eine Begruendung - Leitsatz 4.
    assert frist["reason"]
    assert police["premium"]["kind"] == "none"
    assert police["next_task"] == "Hauptfaelligkeit erfassen"
    assert police["version"] == 1

    liste = client.get(f"{BASIS}/policies", headers=kopf)
    assert liste.status_code == 200, liste.text
    assert [eintrag["id"] for eintrag in liste.json()] == [police["id"]]

    aufgaben = client.get(f"{BASIS}/tasks", headers=kopf).json()
    assert [aufgabe["kind"] for aufgabe in aufgaben] == ["missing_main_due"]
    assert aufgaben[0]["why"]


# ---------------------------------------------------------------------------
# 2. Die drei Geldfaelle
# ---------------------------------------------------------------------------


def test_es_gibt_genau_drei_geldfaelle_und_keinen_vierten(kopf: dict[str, str]) -> None:
    """(a) Vertrag, (b) Fremdbetrag samt Zahler, (c) keins - und niemals zwei.

    Ein selbst bezahlter Beitrag ohne Vertrag kann hier nicht entstehen: dafuer
    gibt es kein Feld. Der Fremdbetrag bleibt aus der Jahressumme der Fixkosten
    ausdruecklich draussen, sonst waere er die erste Zahl, die jemand als
    eigenen Beitrag missversteht.
    """
    vertrag = _vertrag(kopf, name="Haftpflicht Nordwacht")

    mit_vertrag = _police(kopf, insurer_name="Nordwacht AG", contract_id=vertrag["id"])
    assert mit_vertrag["premium"]["kind"] == "contract"
    assert mit_vertrag["premium"]["contract_id"] == vertrag["id"]
    assert mit_vertrag["premium"]["contract_amount_minor"] == 1999
    assert mit_vertrag["premium"]["yearly_in_fixed_costs_minor"] == 1999 * 12

    fremd = _police(
        kopf,
        line_key="health_private",
        insurer_name="Arbeitgeberkasse",
        external={
            "external_premium_minor": 45000,
            "external_frequency": "monthly",
            "premium_payer": "employer",
        },
    )
    assert fremd["premium"]["kind"] == "external"
    assert fremd["premium"]["external_premium_minor"] == 45000
    assert fremd["premium"]["premium_payer"] == "employer"
    assert fremd["premium"]["yearly_in_fixed_costs_minor"] is None

    ohne = _police(kopf, insurer_name="Beitragsfreie Police")
    assert ohne["premium"]["kind"] == "none"

    beides = client.post(
        f"{BASIS}/policies",
        json={
            "line_key": "private_liability",
            "insurer_name": "Doppelt gezaehlt",
            "contract_id": vertrag["id"],
            "external": {
                "external_premium_minor": 1000,
                "external_frequency": "monthly",
                "premium_payer": "partner",
            },
        },
        headers=kopf,
    )
    assert beides.status_code == 422, beides.text

    zusammenfassung = client.get(f"{BASIS}/summary", headers=kopf).json()
    assert zusammenfassung["policy_count"] == 3
    assert zusammenfassung["yearly_in_fixed_costs_minor"] == 1999 * 12
    assert zusammenfassung["yearly_paid_by_others_minor"] == 45000 * 12
    assert zusammenfassung["paid_by_others_breakdown"] == {"employer": 45000 * 12}
    assert zusammenfassung["no_premium_count"] == 1


def test_ein_unvollstaendiger_fremdbeitrag_wird_abgewiesen(kopf: dict[str, str]) -> None:
    """Betrag, Rhythmus und Zahler gehoeren zusammen.

    Ein Betrag ohne benannten Zahler waere wieder ein Geldwert im
    Versicherungsmodul, den niemand zuordnen kann.
    """
    antwort = client.post(
        f"{BASIS}/policies",
        json={
            "line_key": "private_liability",
            "insurer_name": "Ohne Zahler",
            "external": {"external_premium_minor": 1000, "external_frequency": "monthly"},
        },
        headers=kopf,
    )
    assert antwort.status_code == 422, antwort.text


# ---------------------------------------------------------------------------
# 3. Herkunft ist Serverwissen
# ---------------------------------------------------------------------------


def test_der_client_kann_die_herkunft_eines_datums_nicht_setzen(kopf: dict[str, str]) -> None:
    """Weder beim Anlegen noch beim Aendern.

    Ein Datum, das der Client mitbringt, ist eine Annahme - auch wenn er
    'user_confirmed' danebenschreibt. Genau hier entstand in der zweiten Fassung
    des Plans der 30.11. als vermeintliche Tatsache.
    """
    kuenftig = date(date.today().year + 1, 1, 1)
    police = _police(
        kopf,
        line_key="motor_liability",
        insurer_name="Kfz Beispiel",
        main_due_on=kuenftig.isoformat(),
        main_due_source="user_confirmed",
        main_due_confirmed_at="2020-01-01T00:00:00Z",
    )
    assert police["main_due_on"] == kuenftig.isoformat()
    assert police["main_due_source"] == "catalog_suggested"
    assert police["main_due_confirmed_at"] is None

    spaeter = date(date.today().year + 1, 3, 1)
    geaendert = client.patch(
        f"{BASIS}/policies/{police['id']}",
        json={"main_due_on": spaeter.isoformat()},
        headers={**kopf, "If-Match": _etag(kopf, police["id"])},
    )
    assert geaendert.status_code == 200, geaendert.text
    assert geaendert.json()["main_due_on"] == spaeter.isoformat()
    # Das Datum aendert sich, die Herkunft nicht: ein PATCH stuft nicht hoch.
    assert geaendert.json()["main_due_source"] == "catalog_suggested"
    assert geaendert.json()["main_due_confirmed_at"] is None


def test_die_erste_hauptfaelligkeit_entsteht_nur_ueber_die_bestaetigung(
    kopf: dict[str, str],
) -> None:
    """"Erfassen" und "bestaetigen" sind derselbe Schreibweg.

    Ein PATCH, der das erste `main_due_on` eintraegt, liefe in den CHECK
    `main_due_source_pair` - er antwortet deshalb mit einer Verweisung auf die
    Einfeldaktion statt mit einem Datenbankfehler.
    """
    police = _police(kopf, line_key="motor_liability", insurer_name="Kfz ohne Datum")
    antwort = client.patch(
        f"{BASIS}/policies/{police['id']}",
        json={"main_due_on": date(date.today().year + 1, 1, 1).isoformat()},
        headers={**kopf, "If-Match": _etag(kopf, police["id"])},
    )
    assert antwort.status_code == 422, antwort.text
    assert antwort.json()["detail"] == "insurance_main_due_needs_confirmation"


# ---------------------------------------------------------------------------
# 4. und 5. Bestaetigt gegen angenommen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_erst_die_bestaetigung_macht_aus_der_annahme_ein_belegtes_datum(
    kopf: dict[str, str],
) -> None:
    """Der Ein-Klick-Fall: die Aktion ohne Body bestaetigt den Katalogwert.

    Beim Anlegen waere derselbe Wert eine Behauptung; hier ist er eine
    Bestaetigung - mit Zeitstempel und Urheber, wie es der CHECK
    `main_due_confirmed` verlangt.
    """
    police = _police(kopf, line_key="motor_liability", insurer_name="Kfz Beispiel")
    assert police["main_due_on"] is None

    bestaetigt = _bestaetige_hauptfaelligkeit(kopf, police["id"])
    assert bestaetigt["main_due_on"] == date(date.today().year, 1, 1).isoformat()
    assert bestaetigt["main_due_source"] == "user_confirmed"
    assert bestaetigt["main_due_confirmed_at"] is not None
    assert bestaetigt["notice_anchor"] == "term_end"

    frist = bestaetigt["deadline"]
    assert frist["certainty"] == "confirmed"
    assert frist["days_left"] is not None
    assert frist["deadline_legal_on"] is not None
    assert frist["reason"] == ""
    assert bestaetigt["next_task"] is None

    zeile = await _spalten(bestaetigt["id"], "main_due_confirmed_by")
    assert zeile is not None
    assert zeile.main_due_confirmed_by is not None

    aufgaben = client.get(f"{BASIS}/tasks", headers=kopf).json()
    assert aufgaben == []


def test_ein_angenommenes_datum_bekommt_keinen_countdown(kopf: dict[str, str]) -> None:
    """Leitsatz 3: gerechnet wird trotzdem, gezaehlt wird nicht.

    Der Rechenkern liefert `days_left` unabhaengig von der Sicherheit; die API
    gibt die Zahl nur bei belegter Frist heraus. Sonst saehe eine Annahme in
    jeder Oberflaeche wie ein belegter Termin aus.
    """
    kuenftig = date(date.today().year + 1, 1, 1)
    police = _police(
        kopf,
        line_key="motor_liability",
        insurer_name="Kfz Beispiel",
        main_due_on=kuenftig.isoformat(),
    )

    frist = police["deadline"]
    assert frist["certainty"] == "assumed"
    assert frist["days_left"] is None
    assert frist["deadline_legal_on"] is not None
    assert "Annahme" in frist["reason"]
    assert police["next_task"] == "Hauptfaelligkeit bestaetigen"

    aufgaben = client.get(f"{BASIS}/tasks", headers=kopf).json()
    assert [aufgabe["kind"] for aufgabe in aufgaben] == ["unconfirmed_main_due"]
    assert aufgaben[0]["due_on"] == kuenftig.isoformat()

    zusammenfassung = client.get(f"{BASIS}/summary", headers=kopf).json()
    assert zusammenfassung["unconfirmed_main_due_count"] == 1
    # Eine Annahme ist keine "naechste Frist" - sie stuende sonst mit Countdown
    # in der Kennzahlenleiste.
    assert zusammenfassung["next_deadline_on"] is None


# ---------------------------------------------------------------------------
# 6. Kfz: der 30.11. ist ein Rechenergebnis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_der_kfz_termin_haengt_an_der_hauptfaelligkeit_und_steht_nirgends_fest(
    kopf: dict[str, str],
) -> None:
    """Zwei Policen derselben Sparte, zwei Hauptfaelligkeiten, zwei Termine.

    Ein fester Stichtag im Katalog haette den zweiten Fall verschluckt. In der
    gespeicherten Zeile steht deshalb nur die Hauptfaelligkeit und die
    Fristlaenge aus Paragraph 11 Abs. 3 VVG - kein gerechnetes Datum.
    """
    kalenderjahr = _police(kopf, line_key="motor_liability", insurer_name="Kfz Kalenderjahr")
    bestaetigt = _bestaetige_hauptfaelligkeit(kopf, kalenderjahr["id"])
    assert bestaetigt["main_due_on"] == date(date.today().year, 1, 1).isoformat()

    abweichend = _police(kopf, line_key="motor_liability", insurer_name="Kfz Sommerbeginn")
    anders = _bestaetige_hauptfaelligkeit(
        kopf,
        abweichend["id"],
        {"main_due_on": date(date.today().year + 1, 7, 1).isoformat()},
    )

    assert bestaetigt["deadline"]["certainty"] == "confirmed"
    assert anders["deadline"]["certainty"] == "confirmed"
    assert (
        anders["deadline"]["deadline_legal_on"]
        != bestaetigt["deadline"]["deadline_legal_on"]
    )

    for police in (bestaetigt, anders):
        zeile = await _spalten(
            police["id"],
            "main_due_on, notice_fixed_month, notice_fixed_day, notice_period_months",
        )
        assert zeile is not None
        # Der Termin wird gerechnet, nie gespeichert: gespeichert sind nur der
        # Anker und die Monatszahl aus Paragraph 11 Abs. 3 VVG.
        assert (zeile.main_due_on.month, zeile.main_due_on.day) != (11, 30)
        assert zeile.notice_fixed_month is None
        assert zeile.notice_fixed_day is None
        assert zeile.notice_period_months == 1


def test_der_kfz_kuendigungstermin_ist_der_30_11_des_vorjahres(kopf: dict[str, str]) -> None:
    """Plan 3.12, RF-KFZ-FRIST-1: `main_due_on` 01.01., ein Monat Frist.

    Die Versicherungsperiode laeuft dann vom 01.01. bis zum 31.12.; gekuendigt
    wird nach Paragraph 11 Abs. 2 VVG zum Schluss dieser Periode, und die
    Monatsfrist des Paragraphen 11 Abs. 3 VVG endet damit am 30.11. Der Plan
    fuehrt genau das als Erwartung: "2026-11-30, certainty = confirmed,
    Vertragsende 2026-12-31".

    Der Fall steht getrennt vom Test darueber, weil er eine andere Zusage
    prueft: dort geht es darum, DASS der Termin gerechnet wird, hier darum,
    WELCHER Tag herauskommt.
    """
    police = _police(kopf, line_key="motor_liability", insurer_name="Kfz Kalenderjahr")
    bestaetigt = _bestaetige_hauptfaelligkeit(kopf, police["id"])
    frist = bestaetigt["deadline"]

    assert frist["certainty"] == "confirmed"
    ablauf = date.fromisoformat(frist["term_ends_on"])
    termin = date.fromisoformat(frist["deadline_legal_on"])
    assert (ablauf.month, ablauf.day) == (12, 31), "Die Periode endet am 31.12., nicht am 01.01."
    assert (termin.month, termin.day) == (11, 30)
    assert termin.year == ablauf.year


# ---------------------------------------------------------------------------
# 7. bis 9. Bestandsuebernahme
# ---------------------------------------------------------------------------


def test_uebernahme_nennt_den_vertrag_legt_ihn_an_und_wiederholt_sich_nicht(
    kopf: dict[str, str],
) -> None:
    """Vorschau, Lauf, zweiter Lauf.

    Der zweite Lauf ist der eigentliche Test: findet er den Vertrag erneut,
    ueberschreibt er ein womoeglich bereits bestaetigtes Datum mit der
    wandernden Vertragsfaelligkeit.
    """
    vertrag = _vertrag(
        kopf,
        name="Hausrat Nordwacht",
        provider_label="Nordwacht Versicherung AG",
        frequency="yearly",
        amount_minor=12000,
        next_due_date=(date.today() + timedelta(days=40)).isoformat(),
    )
    _vertrag(kopf, name="Fitnessstudio", contract_type="subscription")

    vorschau = client.get(f"{BASIS}/backfill/preview", headers=kopf)
    assert vorschau.status_code == 200, vorschau.text
    daten = vorschau.json()
    # Ring 1: nur, was ausdruecklich als Versicherung gefuehrt ist.
    assert [eintrag["contract_id"] for eintrag in daten["items"]] == [vertrag["id"]]
    assert daten["items"][0]["suggested_line_key"] == "unknown"
    assert daten["items"][0]["reason"]
    assert daten["already_linked_count"] == 0

    lauf = client.post(f"{BASIS}/backfill", headers=kopf)
    assert lauf.status_code == 200, lauf.text
    ergebnis = lauf.json()
    assert ergebnis["created"] == 1
    assert ergebnis["skipped"] == 0

    police = _hole(kopf, ergebnis["policy_ids"][0])
    assert police["data_source"] == "from_contract"
    assert police["line_key"] == "unknown"
    assert police["label"] == "Hausrat Nordwacht"
    assert police["insurer_name"] == "Nordwacht Versicherung AG"
    assert police["premium"]["kind"] == "contract"
    assert police["premium"]["contract_id"] == vertrag["id"]
    assert police["next_task"] == "Sparte zuordnen"

    zweiter = client.post(f"{BASIS}/backfill", headers=kopf)
    assert zweiter.status_code == 200, zweiter.text
    assert zweiter.json()["created"] == 0
    assert zweiter.json()["skipped"] == 1
    assert len(client.get(f"{BASIS}/policies", headers=kopf).json()) == 1

    danach = client.get(f"{BASIS}/backfill/preview", headers=kopf).json()
    assert danach["items"] == []
    assert danach["already_linked_count"] == 1


def test_uebernahme_erfindet_bei_monatlicher_zahlweise_keine_hauptfaelligkeit(
    kopf: dict[str, str],
) -> None:
    """Der Einzugstermin ist nicht der Beginn des Versicherungsjahres.

    Bei monatlicher Zahlweise laege der daraus gerechnete Kuendigungstermin bis
    zu elf Monate daneben - eine erfundene Frist. Bei jaehrlicher Zahlweise
    fallen beide zusammen, und das Datum wird als Annahme uebernommen.
    """
    faellig = date.today() + timedelta(days=45)
    _vertrag(kopf, name="Kfz monatlich", frequency="monthly")
    _vertrag(kopf, name="Hausrat jaehrlich", frequency="yearly", next_due_date=faellig.isoformat())

    lauf = client.post(f"{BASIS}/backfill", headers=kopf)
    assert lauf.status_code == 200, lauf.text
    assert lauf.json()["created"] == 2

    liste = client.get(f"{BASIS}/policies", headers=kopf).json()
    policen = {eintrag["label"]: eintrag for eintrag in liste}

    monatlich = policen["Kfz monatlich"]
    assert monatlich["main_due_on"] is None
    assert monatlich["main_due_source"] == "not_computable"
    assert monatlich["deadline"]["certainty"] == "not_computable"
    assert monatlich["deadline"]["days_left"] is None
    assert monatlich["next_task"] == "Sparte zuordnen"

    jaehrlich = policen["Hausrat jaehrlich"]
    assert jaehrlich["main_due_on"] == faellig.isoformat()
    # Uebernommen heisst angenommen: der Lauf bestaetigt nichts.
    assert jaehrlich["main_due_source"] == "catalog_suggested"
    assert jaehrlich["main_due_confirmed_at"] is None


def test_aus_tagen_umgerechnete_frist_bleibt_angenommen_trotz_bestaetigtem_datum(
    kopf: dict[str, str],
) -> None:
    """Zwei Unsicherheitsquellen, nicht eine.

    `cancellation_period_days` sagt nicht, WOVON die Tage gerechnet werden. Die
    Umrechnung traegt ihre Herkunft deshalb als Spalte und blockiert die
    belegte Frist, bis der Bezugspunkt bestaetigt ist - auch wenn die
    Hauptfaelligkeit laengst bestaetigt wurde.
    """
    faellig = date.today() + timedelta(days=60)
    _vertrag(
        kopf,
        name="Hausrat mit Frist",
        provider_label="Nordwacht Versicherung AG",
        frequency="yearly",
        cancellation_period_days=90,
        next_due_date=faellig.isoformat(),
    )
    lauf = client.post(f"{BASIS}/backfill", headers=kopf).json()
    police = _hole(kopf, lauf["policy_ids"][0])

    assert police["notice_terms_source"] == "derived_from_days"
    assert police["notice_period_months"] == 3
    assert police["notice_anchor"] == "term_end"
    assert police["main_due_on"] == faellig.isoformat()
    assert police["deadline"]["certainty"] == "assumed"

    bestaetigt = _bestaetige_hauptfaelligkeit(kopf, police["id"])
    assert bestaetigt["main_due_source"] == "user_confirmed"
    assert bestaetigt["deadline"]["certainty"] == "assumed"
    assert "Bezugspunkt" in bestaetigt["deadline"]["reason"]
    assert bestaetigt["deadline"]["days_left"] is None

    fertig = client.post(
        f"{BASIS}/policies/{police['id']}/confirm-notice-terms",
        json={"notice_period_months": 3},
        headers=kopf,
    )
    assert fertig.status_code == 200, fertig.text
    assert fertig.json()["notice_terms_source"] == "user_confirmed"
    assert fertig.json()["deadline"]["certainty"] == "confirmed"
    assert fertig.json()["deadline"]["days_left"] is not None


def test_sparte_zuordnen_zieht_die_vorgaben_der_neuen_sparte_nach(
    kopf: dict[str, str],
) -> None:
    """Nach dem Spartenwechsel duerfen keine Vorgaben der alten Sparte
    stehenbleiben - sonst schoebe Paragraph 193 BGB eine Frist, die er bei einer
    Kuendigungsfrist nicht schieben darf."""
    _vertrag(kopf, name="Hausrat Nordwacht", frequency="yearly")
    lauf = client.post(f"{BASIS}/backfill", headers=kopf).json()
    policy_id = lauf["policy_ids"][0]

    antwort = client.post(
        f"{BASIS}/policies/{policy_id}/confirm-line",
        json={"line_key": "household_contents"},
        headers=kopf,
    )
    assert antwort.status_code == 200, antwort.text
    police = antwort.json()
    assert police["line_key"] == "household_contents"
    assert police["line_confirmed_at"] is not None
    assert police["notice_period_months"] == 3
    assert police["notice_anchor"] == "term_end"
    assert police["deadline"]["deadline_kind"] == "kuendigungsfrist"
    assert police["next_task"] == "Hauptfaelligkeit bestaetigen"

    zusammenfassung = client.get(f"{BASIS}/summary", headers=kopf).json()
    assert zusammenfassung["unknown_line_count"] == 0


# ---------------------------------------------------------------------------
# 10. Loeschen
# ---------------------------------------------------------------------------


def test_loeschen_trennt_police_und_vertrag_ohne_den_anderen_zu_beschaedigen(
    kopf: dict[str, str],
) -> None:
    """Beide Richtungen.

    Die Police zu loeschen darf den Beitrag nicht mitnehmen; den Vertrag zu
    loeschen darf die Police nicht mitnehmen. Der Fremdschluessel setzt
    spaltenweise nur `contract_id` auf null - saetze er beide Spalten, scheiterte
    das Loeschen des Vertrags an `household_id not null`.
    """
    vertrag_bleibt = _vertrag(kopf, name="Bleibt bestehen")
    police_weg = _police(kopf, insurer_name="Police faellt weg", contract_id=vertrag_bleibt["id"])
    assert client.delete(f"{BASIS}/policies/{police_weg['id']}", headers=kopf).status_code == 204

    weiter = client.get(f"/v1/contracts/{vertrag_bleibt['id']}", headers=kopf)
    assert weiter.status_code == 200, weiter.text
    assert weiter.json()["amount_minor"] == 1999
    assert weiter.json()["status"] == "active"

    vertrag_weg = _vertrag(kopf, name="Wird geloescht")
    police_bleibt = _police(kopf, insurer_name="Police bleibt", contract_id=vertrag_weg["id"])
    assert client.delete(f"/v1/contracts/{vertrag_weg['id']}", headers=kopf).status_code == 204

    danach = _hole(kopf, police_bleibt["id"])
    assert danach["premium"]["kind"] == "none"
    assert danach["premium"]["contract_id"] is None
    assert danach["insurer_name"] == "Police bleibt"


# ---------------------------------------------------------------------------
# 11. Haushaltsgrenze
# ---------------------------------------------------------------------------


def test_fremder_haushalt_bekommt_auf_jeden_endpunkt_genau_404() -> None:
    """Eindeutig 404 - kein 403, kein 422.

    Ein abweichender Code waere ein Informationsleck: er verriete, dass es die
    Police gibt und sie nur nicht zugaenglich ist.
    """
    eigen = _haushalt("ins-a")
    vertrag = _vertrag(eigen, name="Fremdzugriff")
    police = _police(eigen, insurer_name="Eigene Police", contract_id=vertrag["id"])
    policy_id = police["id"]

    fremd = _haushalt("ins-b")
    # Das If-Match steht bewusst dabei: ohne Header antwortete PATCH mit 428,
    # bevor er ueberhaupt nach der Police sucht - das waere kein Beleg fuer die
    # Haushaltsgrenze.
    abfragen: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = [
        ("GET", f"{BASIS}/policies/{policy_id}", None, {}),
        ("GET", f"{BASIS}/policies/{policy_id}/purpose", None, {}),
        ("GET", f"{BASIS}/policies/{policy_id}/contract-candidates", None, {}),
        ("PATCH", f"{BASIS}/policies/{policy_id}", {"label": "Fremd"}, {"If-Match": 'W/"0.1"'}),
        ("POST", f"{BASIS}/policies/{policy_id}/status", {"status": "ended"}, {}),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/confirm-line",
            {"line_key": "household_contents"},
            {},
        ),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/confirm-main-due",
            {"main_due_on": date(date.today().year + 1, 1, 1).isoformat()},
            {},
        ),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/confirm-notice-terms",
            {"notice_period_months": 3},
            {},
        ),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/link-contract",
            {"contract_id": vertrag["id"]},
            {},
        ),
        ("DELETE", f"{BASIS}/policies/{policy_id}/link-contract", None, {}),
        ("DELETE", f"{BASIS}/policies/{policy_id}", None, {}),
    ]

    for methode, pfad, koerper, zusatz in abfragen:
        antwort = client.request(methode, pfad, json=koerper, headers={**fremd, **zusatz})
        assert antwort.status_code == 404, f"{methode} {pfad}: {antwort.status_code} {antwort.text}"

    # Und im eigenen Haushalt haengt die Police unveraendert.
    unveraendert = _hole(eigen, policy_id)
    assert unveraendert["version"] == 1
    assert unveraendert["premium"]["contract_id"] == vertrag["id"]
    assert client.get(f"{BASIS}/policies", headers=fremd).json() == []


# ---------------------------------------------------------------------------
# 12. Nebenlaeufigkeit
# ---------------------------------------------------------------------------


def test_if_match_ist_pflicht_und_ein_veralteter_stand_wird_abgewiesen(
    kopf: dict[str, str],
) -> None:
    """Das ETag ist zusammengesetzt: Vertragsversion und Policenversion.

    Ohne die Vertragsversion bliebe eine parallele Betragsaenderung am Vertrag
    unbemerkt - und genau die aendert, was auf der Police steht.
    """
    police = _police(kopf)
    assert _etag(kopf, police["id"]) == 'W/"0.1"'

    ohne = client.patch(
        f"{BASIS}/policies/{police['id']}", json={"label": "Neuer Name"}, headers=kopf
    )
    assert ohne.status_code == 428, ohne.text
    assert ohne.json()["detail"] == "if_match_required"

    veraltet = client.patch(
        f"{BASIS}/policies/{police['id']}",
        json={"label": "Neuer Name"},
        headers={**kopf, "If-Match": 'W/"0.99"'},
    )
    assert veraltet.status_code == 409, veraltet.text
    assert veraltet.json()["detail"] == "version_conflict"
    # Der aktuelle Stand kommt mit, damit der Client ohne zweiten Request neu
    # aufsetzen kann.
    assert veraltet.headers["ETag"] == 'W/"0.1"'

    aktuell = client.patch(
        f"{BASIS}/policies/{police['id']}",
        json={"label": "Neuer Name"},
        headers={**kopf, "If-Match": 'W/"0.1"'},
    )
    assert aktuell.status_code == 200, aktuell.text
    assert aktuell.json()["label"] == "Neuer Name"
    assert aktuell.json()["version"] == 2
    assert aktuell.headers["ETag"] == 'W/"0.2"'


# ---------------------------------------------------------------------------
# Zwecktext an der Police
# ---------------------------------------------------------------------------


def test_die_zweckfrage_beantwortet_die_sparte_und_nennt_ihren_vorbehalt(
    kopf: dict[str, str],
) -> None:
    """"Wofuer ist die da" - beantwortet aus dem Katalog, mit Vorbehalt.

    Der Satz "Ihre konkrete Police kann abweichen" steht im Vertrag und nicht
    nur in der Oberflaeche: solange keine Deckungsbausteine erfasst sind (ab
    P4), beschreibt der Text die Sparte, nie die Police.
    """
    police = _police(kopf)
    antwort = client.get(f"{BASIS}/policies/{police['id']}/purpose", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    zweck = antwort.json()

    assert zweck["coverage_source"] == "catalog"
    assert "kann abweichen" in zweck["caveat"]
    assert zweck["typically_covers"]
    assert zweck["typically_not_covers"]
    assert str(zweck["source_url"]).startswith("https://")
    assert zweck["source_checked_on"] is not None
