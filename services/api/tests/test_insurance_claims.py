"""Schaden- und Leistungsfaelle ueber die API (docs/VERSICHERUNGEN-PLAN.md, P5).

Geprueft werden die vier Leitsaetze der Phase, nicht die Felder:

1. **Gesundheit bleibt beim Erstattungsmodul.** Ein Vorgang auf einer PKV-Police
   wird abgewiesen - mit Verweis auf `/v1/reimbursements`, und zwar sowohl vom
   Dienst als auch vom Trigger. Das Erstattungsmodul selbst bleibt dabei
   unveraendert: es funktioniert weiter, auch wenn nie eine Police entsteht.
2. **Der Fristanker ist NICHT die Entscheidung.** Ohne
   `negotiations_closed_on` gibt es kein Fensterende, sondern die Aussage, dass
   die Angabe fehlt. Mit ihm laeuft das Fenster ab DIESEM Datum - die Tests
   fuehren Entscheidungs- und Abschlussdatum bewusst auseinander und schlagen
   bei einer Verwechslung fehl. Welche der beiden Normen greift, entscheidet die
   Sparte: Haftpflicht rechnet ab Anerkennung, Kasko ab Verhandlungsschluss.
3. **Auch hier entsteht kein Geld.** `settled_minor` hat genau einen Schreiber -
   die zugeordneten Zahlungen. Eine Sammelueberweisung deckt mehrere Vorgaenge,
   ein Vorgang kennt Abschlag und Restzahlung, und die Direktzahlung an die
   Werkstatt kommt ohne Buchung aus. Mehr zuzuordnen als eine Buchung hergibt,
   wird abgewiesen - genau wie im Erstattungsabgleich.
4. **Was einem anderen Haushalt gehoert, existiert nicht.** Jeder neue Endpunkt
   antwortet 404, nicht 403.

Laeuft wie alle API-Tests gegen ein echtes lokales Supabase mit echten
Anmeldungen. Die Daten sind relativ zum heutigen Tag gerechnet: die Routen
setzen `today = date.today()`, ein fest verdrahtetes Jahr liesse die Suite an
einem Silvester rot werden. Die Monatsaddition ist hier eigens nachgebaut
(`_plus_monat`) und nicht aus `insurance_deadline_service` importiert - ein Test,
der die zu pruefende Formel aufruft, prueft nichts.
"""

import os
import uuid
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

BASIS = "/v1/insurance"
ERSTATTUNG = "/v1/reimbursements"

# Direkte Verbindung fuer alles, was die API bewusst nicht kann: der Trigger
# gegen Gesundheitspolicen sitzt in SQL und nicht in Python, und die
# Hartloeschung einer Buchung (zurueckgenommener Import) hat keinen Endpunkt.
# Gleiches Muster wie tests/test_insurance_health_access.py.
_DSN = os.environ.get(
    "TEST_DIRECT_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:55322/postgres",
)

# Die Sparte entscheidet ueber den Fristanker, nicht der Name der Police:
# insurance_lines.default_claim_termination_basis traegt fuer Hausrat 'vvg_92',
# fuer Kfz-Haftpflicht 'vvg_111' - und fuer die PKV 'none', weil dort das
# Erstattungsmodul zustaendig ist.
HAUSRAT = "household_contents"  # vvg_92
KFZ_HAFTPFLICHT = "motor_liability"  # vvg_111
KASKO = "motor_comprehensive"  # vvg_92
PKV = "health_private"  # is_health_scoped = true


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


def _tage(anzahl: int) -> date:
    return date.today() + timedelta(days=anzahl)


def _plus_monat(tag: date) -> date:
    """Ein Monat weiter, mit Klemmung auf den Monatsletzten (31.01. -> 28.02.).

    Bewusst hier nachgebaut statt aus `insurance_deadline_service` importiert:
    das Fristende ist die Zusage dieses Moduls, und ein Test, der dieselbe
    Funktion aufruft wie der Rechenkern, wuerde jede gemeinsame Verirrung
    mitmachen.
    """
    jahr = tag.year + (1 if tag.month == 12 else 0)
    monat = 1 if tag.month == 12 else tag.month + 1
    return date(jahr, monat, min(tag.day, monthrange(jahr, monat)[1]))


def _haushalt(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-11")
    haushalt = client.post(
        "/v1/households",
        json={"name": "Schadenfall-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": str(haushalt["id"])}


@pytest.fixture()
def kopf() -> dict[str, str]:
    """Function-scoped mit Absicht: das autouse-Aufraeumen entfernt nach jedem
    Test die dabei entstandenen Haushalte."""
    return _haushalt("clm")


@pytest.fixture()
def fremd() -> dict[str, str]:
    """Ein zweiter Nutzer mit eigenem Haushalt - die Gegenprobe zu jedem Endpunkt."""
    return _haushalt("clm-fremd")


def _police(
    kopf: dict[str, str],
    line_key: str = HAUSRAT,
    insurer_name: str = "Beispiel Versicherung AG",
    **felder: Any,
) -> dict[str, Any]:
    koerper: dict[str, Any] = {"line_key": line_key, "insurer_name": insurer_name}
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/policies", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _bestaetige_hauptfaelligkeit(
    kopf: dict[str, str], policy_id: str, tag: date
) -> dict[str, Any]:
    antwort = client.post(
        f"{BASIS}/policies/{policy_id}/confirm-main-due",
        json={"main_due_on": tag.isoformat()},
        headers=kopf,
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _vorgang(kopf: dict[str, str], **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "title": "Wasserschaden Kueche",
        "occurred_on": _tage(-30).isoformat(),
    }
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/claims", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _hole(kopf: dict[str, str], claim_id: str) -> dict[str, Any]:
    antwort = client.get(f"{BASIS}/claims/{claim_id}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _fenster(kopf: dict[str, str], claim_id: str) -> dict[str, Any]:
    return dict(_hole(kopf, claim_id)["special_termination"])


def _status(kopf: dict[str, str], claim_id: str, **felder: Any) -> dict[str, Any]:
    antwort = client.post(f"{BASIS}/claims/{claim_id}/status", json=felder, headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _abschluss(kopf: dict[str, str], claim_id: str, **felder: Any) -> dict[str, Any]:
    antwort = client.post(
        f"{BASIS}/claims/{claim_id}/close-negotiations", json=felder, headers=kopf
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _konto(kopf: dict[str, str]) -> str:
    antwort = client.post(
        "/v1/accounts", json={"name": "Girokonto", "account_type": "checking"}, headers=kopf
    )
    assert antwort.status_code == 201, antwort.text
    return str(antwort.json()["id"])


def _buchung(
    kopf: dict[str, str],
    konto_id: str,
    amount_minor: int,
    *,
    booked_date: date | None = None,
    payee_label: str = "Beispiel Versicherung AG",
    note: str | None = None,
) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "account_id": konto_id,
        "booked_date": (booked_date or _tage(-3)).isoformat(),
        "amount_minor": amount_minor,
        "payee_label": payee_label,
    }
    if note is not None:
        koerper["note"] = note
    antwort = client.post("/v1/transactions", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _zahlung(kopf: dict[str, str], claim_id: str, **felder: Any) -> dict[str, Any]:
    antwort = client.post(f"{BASIS}/claims/{claim_id}/payments", json=felder, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _spalten(claim_id: str, spalten: str) -> tuple[Any, ...]:
    """Liest Spalten direkt aus der Datenbank.

    Der Nachtlauf liest `special_termination_state` und `_until` aus der Zeile
    und rechnet nicht nach (Plan 8.2 Punkt 2b) - was die API antwortet, muss
    deshalb auch dort stehen."""
    with psycopg.connect(_DSN, autocommit=True) as verbindung:
        zeile = verbindung.execute(
            f"select {spalten} from insurance_claims where id = %s", (claim_id,)
        ).fetchone()
    assert zeile is not None
    return tuple(zeile)


# ---------------------------------------------------------------------------
# 1. Gesundheit bleibt beim Erstattungsmodul
# ---------------------------------------------------------------------------


def test_ein_vorgang_auf_einer_pkv_police_wird_mit_verweis_auf_die_erstattung_abgewiesen(
    kopf: dict[str, str],
) -> None:
    """Pflichtfall 1 (Plan 4.8, Leitsatz 2 des Auftrags).

    Zwei Vorgangsmodelle fuer dieselbe Rechnung waeren die Zweitwahrheit, die
    dieser Plan ueberall vermeidet. Geprueft wird deshalb nicht nur der Status,
    sondern auch, dass die Abweisung einen WEG nennt - eine Sackgasse waere
    fachlich dasselbe wie ein 500er.
    """
    pkv = _police(kopf, line_key=PKV, insurer_name="Muster Krankenversicherung AG")
    assert pkv["is_health_scoped"] is True

    antwort = client.post(
        f"{BASIS}/claims",
        json={
            "policy_id": pkv["id"],
            "title": "Zahnersatz",
            "occurred_on": _tage(-10).isoformat(),
            "claimed_minor": 120000,
        },
        headers=kopf,
    )
    assert antwort.status_code == 422, antwort.text
    detail = antwort.json()["detail"]
    assert detail["code"] == "insurance_claim_health_scoped"
    # Der Verweis steht maschinenlesbar UND im Klartext - der Nutzer soll ihn
    # lesen, die Oberflaeche soll ihn verlinken koennen.
    assert detail["endpoint"] == "/v1/reimbursements/cases"
    assert "/v1/reimbursements" in detail["hint"]

    # Derselbe Weg ueber den Policenpfad - die Police im Pfad gewinnt gegen den
    # Koerper, sie darf die Grenze deshalb nicht umgehen.
    ueber_police = client.post(
        f"{BASIS}/policies/{pkv['id']}/claims",
        json={"title": "Zahnersatz", "occurred_on": _tage(-10).isoformat()},
        headers=kopf,
    )
    assert ueber_police.status_code == 422, ueber_police.text
    assert ueber_police.json()["detail"]["code"] == "insurance_claim_health_scoped"

    # Und der stille Weg hinein: ein harmloser Vorgang, nachtraeglich umgehaengt.
    sach = _police(kopf, line_key=HAUSRAT)
    vorgang = _vorgang(kopf, policy_id=sach["id"])
    umhaengen = client.patch(
        f"{BASIS}/claims/{vorgang['id']}",
        json={"policy_id": pkv["id"]},
        headers={**kopf, "If-Match": f'"{vorgang["version"]}"'},
    )
    assert umhaengen.status_code == 422, umhaengen.text
    assert umhaengen.json()["detail"]["code"] == "insurance_claim_health_scoped"

    # Kein Vorgang haengt an der Gesundheitspolice, und der Sachvorgang steht
    # unveraendert an seiner eigenen.
    assert client.get(f"{BASIS}/policies/{pkv['id']}/claims", headers=kopf).json() == []
    assert _hole(kopf, vorgang["id"])["policy_id"] == sach["id"]


def test_die_grenze_zur_gesundheit_haelt_auch_am_dienst_vorbei(kopf: dict[str, str]) -> None:
    """Der Trigger, nicht der Kommentar (Plan 4.8, "per Trigger erzwungen").

    Der Dienst prueft freundlich und antwortet 422. Ein Rennen zwischen zwei
    Anfragen, ein Skript oder eine spaetere zweite Schreibstelle gehen daran
    vorbei - deshalb muss die Datenbank dasselbe sagen.
    """
    pkv = _police(kopf, line_key=PKV, insurer_name="Muster Krankenversicherung AG")
    haushalt_id = kopf["X-Household-Id"]

    with psycopg.connect(_DSN, autocommit=True) as verbindung:
        with pytest.raises(psycopg.Error) as beim_anlegen:
            verbindung.execute(
                "insert into insurance_claims (household_id, policy_id, title, occurred_on) "
                "values (%s, %s, %s, %s)",
                (haushalt_id, pkv["id"], "Zahnersatz", date.today()),
            )
        assert "INSURANCE_CLAIM_HEALTH_SCOPED" in str(beim_anlegen.value)

        # Auch das nachtraegliche Umhaengen einer bestehenden Zeile.
        zeile = verbindung.execute(
            "insert into insurance_claims (household_id, title, occurred_on) "
            "values (%s, %s, %s) returning id",
            (haushalt_id, "Auffahrunfall", date.today()),
        ).fetchone()
        assert zeile is not None
        with pytest.raises(psycopg.Error) as beim_umhaengen:
            verbindung.execute(
                "update insurance_claims set policy_id = %s where id = %s",
                (pkv["id"], zeile[0]),
            )
        assert "INSURANCE_CLAIM_HEALTH_SCOPED" in str(beim_umhaengen.value)


def test_das_erstattungsmodul_bleibt_unveraendert_auch_ohne_verknuepfte_police(
    kopf: dict[str, str],
) -> None:
    """Pflichtfall 2 (Plan 9.1).

    Die Bruecke ist eine Spalte und ein Vorschlag - kein Zwang und keine
    Datenmigration. Ein Erstattungsvorgang muss Zeichen fuer Zeichen derselbe
    bleiben, egal was P5 daneben tut.
    """
    person = client.post(
        f"{ERSTATTUNG}/people", json={"display_name": "Testperson"}, headers=kopf
    )
    assert person.status_code == 201, person.text
    fall = client.post(
        f"{ERSTATTUNG}/cases",
        json={
            "insured_person_id": person.json()["id"],
            "provider_name": "Praxis Beispiel",
            "invoice_number": "RE-2026-17",
            "invoice_date": _tage(-20).isoformat(),
            "billed_minor": 25000,
            "expected_minor": 20000,
            "payment_status": "paid",
            "submission_status": "submitted",
            "reimbursement_status": "expected",
        },
        headers=kopf,
    )
    assert fall.status_code == 201, fall.text
    fall_id = str(fall.json()["id"])
    vorher = client.get(f"{ERSTATTUNG}/cases/{fall_id}", headers=kopf).json()

    zahler = client.post(
        f"{ERSTATTUNG}/payers",
        json={"payer_type": "pkv", "name": "Muster Krankenversicherung AG"},
        headers=kopf,
    )
    assert zahler.status_code == 201, zahler.text
    zahler_id = str(zahler.json()["id"])

    # (a) Ohne Police gibt es nichts vorzuschlagen - und der Vorgang laeuft
    #     trotzdem. Das ist der Zustand nach einer Datenuebernahme ohne
    #     hinterlegte Police.
    ohne = client.post(f"{ERSTATTUNG}/payers/{zahler_id}/suggest-policy", headers=kopf)
    assert ohne.status_code == 200, ohne.text
    assert ohne.json()["policy_id"] is None
    assert ohne.json()["suggestions"] == []
    assert "freiwillig" in ohne.json()["hint"]

    # (b) Mit Police entsteht ein Vorschlag - aber der Aufruf ohne Koerper
    #     SCHREIBT NICHTS. Geprueft an der Spalte, nicht an der Antwort.
    police = _police(kopf, line_key=PKV, insurer_name="Muster Krankenversicherung AG")
    vorschlag = client.post(f"{ERSTATTUNG}/payers/{zahler_id}/suggest-policy", headers=kopf)
    assert vorschlag.status_code == 200, vorschlag.text
    assert vorschlag.json()["policy_id"] is None
    treffer = vorschlag.json()["suggestions"]
    assert [eintrag["policy_id"] for eintrag in treffer] == [police["id"]]
    assert treffer[0]["evidence"], "ein Vorschlag ohne Begruendung ist nicht ueberpruefbar"
    with psycopg.connect(_DSN, autocommit=True) as verbindung:
        gespeichert = verbindung.execute(
            "select policy_id from reimbursement_payers where id = %s", (zahler_id,)
        ).fetchone()
    assert gespeichert is not None and gespeichert[0] is None

    # (c) Verknuepfen und wieder loesen - beides aendert am Vorgang nichts.
    verknuepft = client.post(
        f"{ERSTATTUNG}/payers/{zahler_id}/suggest-policy",
        json={"link_policy_id": police["id"]},
        headers=kopf,
    )
    assert verknuepft.status_code == 200, verknuepft.text
    assert verknuepft.json()["policy_id"] == police["id"]
    assert client.get(f"{ERSTATTUNG}/cases/{fall_id}", headers=kopf).json() == vorher

    # Und die Grenze bleibt: eine verknuepfte Police macht aus der Rechnung
    # keinen Schadenvorgang.
    verboten = client.post(
        f"{BASIS}/claims",
        json={
            "policy_id": police["id"],
            "title": "Zahnersatz",
            "occurred_on": _tage(-10).isoformat(),
        },
        headers=kopf,
    )
    assert verboten.status_code == 422, verboten.text
    assert client.get(f"{BASIS}/claims", headers=kopf).json() == []

    geloest = client.post(
        f"{ERSTATTUNG}/payers/{zahler_id}/suggest-policy",
        json={"unlink": True},
        headers=kopf,
    )
    assert geloest.status_code == 200, geloest.text
    assert geloest.json()["policy_id"] is None
    assert client.get(f"{ERSTATTUNG}/cases/{fall_id}", headers=kopf).json() == vorher


# ---------------------------------------------------------------------------
# 2. Der Fristanker ist nicht die Entscheidung
# ---------------------------------------------------------------------------


def test_ohne_verhandlungsschluss_gibt_es_kein_fenster_sondern_die_fehlende_angabe(
    kopf: dict[str, str],
) -> None:
    """Pflichtfall 3 (Plan 3.12 Pruefung 3, Leitsatz 3 des Auftrags).

    Der Test fuehrt Entscheidungsdatum und Verhandlungsschluss bewusst
    auseinander: 40 Tage liegen dazwischen. Wer aus `decided_on` rechnet,
    bekommt ein Fenster, das laengst verfallen waere - der Test schlaegt dann
    zweifach fehl, im Datum und im Zustand.
    """
    police = _police(kopf, line_key=HAUSRAT, insurer_name="Hausrat Beispiel AG")
    _bestaetige_hauptfaelligkeit(kopf, str(police["id"]), date(date.today().year, 1, 1))

    schadentag = _tage(-90)
    entscheidung = _tage(-45)
    verhandlungsschluss = _tage(-5)
    vorgang = _vorgang(
        kopf,
        policy_id=police["id"],
        title="Rohrbruch",
        occurred_on=schadentag.isoformat(),
        claimed_minor=250000,
    )
    # Die Sparte belegt die Norm vor, geraten wird nichts.
    assert vorgang["special_termination"]["termination_basis"] == "vvg_92"
    # Vor der Meldung gibt es kein Fenster - und ausdruecklich eine Begruendung.
    assert vorgang["special_termination"]["state"] == "none"
    assert vorgang["special_termination"]["rationale"]

    gemeldet = _status(kopf, str(vorgang["id"]), to_status="reported")
    fenster = gemeldet["special_termination"]
    assert fenster["state"] == "open_undetermined"
    assert fenster["needs_anchor"] is True
    assert fenster["deadline_legal_on"] is None
    assert fenster["days_left"] is None
    assert "steht noch nicht fest" in fenster["rationale"]

    # Die Teilregulierung: die Entscheidung des Versicherers ist hier der ANFANG
    # der Verhandlung. Ein Vorschlag entstuende hier falsch - deshalb entsteht
    # keiner.
    _status(
        kopf,
        str(vorgang["id"]),
        to_status="decided",
        decision="accepted_partial",
        decided_on=entscheidung.isoformat(),
        reduction_reason="Zeitwert statt Neuwert",
    )
    nach_entscheidung = _fenster(kopf, str(vorgang["id"]))
    assert nach_entscheidung["state"] == "open_undetermined"
    assert nach_entscheidung["needs_anchor"] is True
    assert nach_entscheidung["deadline_legal_on"] is None
    assert nach_entscheidung["suggestion"] is None
    assert _hole(kopf, str(vorgang["id"]))["decided_on"] == entscheidung.isoformat()

    # Erst der Abschluss der Verhandlungen setzt den Anker - und das Fenster
    # laeuft ab DIESEM Datum.
    abgeschlossen = _abschluss(
        kopf,
        str(vorgang["id"]),
        negotiations_closed_on=verhandlungsschluss.isoformat(),
        reason="user_confirmed",
    )
    endfenster = abgeschlossen["special_termination"]
    assert endfenster["state"] == "open_until"
    assert endfenster["needs_anchor"] is False
    assert endfenster["anchor_on"] == verhandlungsschluss.isoformat()
    assert endfenster["deadline_legal_on"] == _plus_monat(verhandlungsschluss).isoformat()
    # Die Gegenprobe, die eine Verwechslung sichtbar macht:
    assert endfenster["deadline_legal_on"] != _plus_monat(entscheidung).isoformat()
    # Der Nutzer hat das Datum gesetzt, nicht ein Beleg - und der Client kann
    # die Sicherheit nicht behaupten.
    assert endfenster["anchor_certainty"] == "geschaetzt"
    assert endfenster["anchor_source"] == "manual"
    assert "voraussichtlich" in endfenster["rationale"]
    # Paragraph 92 Abs. 2 S. 3: spaetester Beendigungszeitpunkt fuer den VN.
    assert endfenster["latest_effect_on"] == date(date.today().year, 12, 31).isoformat()
    assert endfenster["days_left"] is not None and endfenster["days_left"] >= 0

    # Was die API rechnet, muss der Nachtlauf in der Zeile finden.
    zustand, bis = _spalten(
        str(vorgang["id"]), "special_termination_state, special_termination_until"
    )
    assert zustand == "open_until"
    assert bis == _plus_monat(verhandlungsschluss)


def test_ein_vorgang_verfaellt_nie_von_selbst(kopf: dict[str, str]) -> None:
    """Ein 400 Tage alter Vorgang ohne Anker bleibt `open_undetermined`.

    Das Werkzeug erklaert ein Recht niemals von sich aus fuer verfallen (Plan
    4.8): ohne Abschluss der Verhandlungen ist das Fensterende schlicht nicht
    bekannt - und ein plausibel aussehendes, frei gerechnetes Datum waere die
    einzige Variante, die dem Nutzer schadet.
    """
    police = _police(kopf, line_key=HAUSRAT)
    vorgang = _vorgang(
        kopf,
        policy_id=police["id"],
        occurred_on=_tage(-400).isoformat(),
        reported_on=_tage(-395).isoformat(),
        status="reported",
    )
    fenster = vorgang["special_termination"]
    assert fenster["state"] == "open_undetermined"
    assert fenster["needs_anchor"] is True
    assert fenster["deadline_legal_on"] is None
    assert _spalten(str(vorgang["id"]), "special_termination_state")[0] == "open_undetermined"

    # Ueber einen GESETZTEN Anker erreicht derselbe Vorgang sehr wohl 'expired' -
    # und bleibt dann sichtbar. Ein verfallenes Recht wird nicht stillschweigend
    # geleert: der Nutzer soll sehen, dass es bestand (Plan 4.8).
    verfallen = _abschluss(
        kopf,
        str(vorgang["id"]),
        negotiations_closed_on=_tage(-120).isoformat(),
        reason="user_confirmed",
    )["special_termination"]
    assert verfallen["state"] == "expired"
    assert verfallen["anchor_on"] == _tage(-120).isoformat()
    assert verfallen["deadline_legal_on"] == _plus_monat(_tage(-120)).isoformat()
    assert verfallen["days_left"] is None
    assert verfallen["rationale"]


def test_haftpflicht_rechnet_ab_anerkennung_kasko_ab_verhandlungsschluss(
    kopf: dict[str, str],
) -> None:
    """Pflichtfall 4 (Plan 13.1, RF-KFZ-FRIST-8 und RF-KFZ-FRIST-9).

    Ein Unfall, zwei Policen, zwei verschiedene Fristen. Waere die Norm global
    statt an der Sparte verankert, lieferten Haftpflicht und Kasko denselben
    Tag - und genau einer der beiden waere sicher falsch.
    """
    haft = _police(kopf, line_key=KFZ_HAFTPFLICHT, insurer_name="Kfz Beispiel AG")
    kasko = _police(kopf, line_key=KASKO, insurer_name="Kfz Beispiel AG")
    _bestaetige_hauptfaelligkeit(kopf, str(haft["id"]), date(date.today().year, 1, 1))

    unfall = _tage(-60)
    anerkennung = _tage(-20)
    verhandlungsschluss = _tage(-5)

    a = _vorgang(
        kopf,
        policy_id=haft["id"],
        title="Auffahrunfall - Schaden am fremden Fahrzeug",
        occurred_on=unfall.isoformat(),
        status="reported",
        claimed_minor=480000,
    )
    b = _vorgang(
        kopf,
        policy_id=kasko["id"],
        title="Auffahrunfall - Schaden am eigenen Fahrzeug",
        occurred_on=unfall.isoformat(),
        status="reported",
        claimed_minor=310000,
    )
    assert a["special_termination"]["termination_basis"] == "vvg_111"
    assert "111" in a["special_termination"]["law"]
    assert b["special_termination"]["termination_basis"] == "vvg_92"
    assert "92" in b["special_termination"]["law"]

    for vorgang_id in (str(a["id"]), str(b["id"])):
        _status(
            kopf,
            vorgang_id,
            to_status="decided",
            decision="accepted_full",
            decided_on=anerkennung.isoformat(),
        )

    # Haftpflicht: die Anerkennung IST der Anker (Paragraph 111 Abs. 2 S. 1).
    vorschlag = _fenster(kopf, str(a["id"]))["suggestion"]
    assert vorschlag is not None
    assert vorschlag["negotiations_closed_on"] == anerkennung.isoformat()
    assert vorschlag["reason"] == "liability_acknowledged"
    assert vorschlag["certainty"] == "abgeleitet"

    # Kasko: dieselbe Anerkennung ist hier KEIN Anker - die Verhandlungen ueber
    # die Entschaedigung laufen weiter.
    kasko_fenster = _fenster(kopf, str(b["id"]))
    assert kasko_fenster["suggestion"] is None
    assert kasko_fenster["state"] == "open_undetermined"

    a_fertig = _abschluss(
        kopf,
        str(a["id"]),
        negotiations_closed_on=anerkennung.isoformat(),
        reason="liability_acknowledged",
    )["special_termination"]
    b_fertig = _abschluss(
        kopf,
        str(b["id"]),
        negotiations_closed_on=verhandlungsschluss.isoformat(),
        reason="settlement_letter",
    )["special_termination"]

    assert a_fertig["deadline_legal_on"] == _plus_monat(anerkennung).isoformat()
    assert b_fertig["deadline_legal_on"] == _plus_monat(verhandlungsschluss).isoformat()
    assert a_fertig["deadline_legal_on"] != b_fertig["deadline_legal_on"]
    # Das Datum stammt aus dem Vorgang, nicht aus einem Beleg.
    assert a_fertig["anchor_certainty"] == "abgeleitet"
    # Paragraph 92 Abs. 2 S. 3 ueber Paragraph 111 Abs. 2 S. 2 - aber nur bei
    # BESTAETIGTER Hauptfaelligkeit; aus einer Annahme entstuende sonst ein
    # angenommenes Vertragsende.
    assert a_fertig["latest_effect_on"] == date(date.today().year, 12, 31).isoformat()
    assert b_fertig["latest_effect_on"] is None


def test_ohne_eigene_police_gibt_es_nichts_zu_kuendigen(kopf: dict[str, str]) -> None:
    """Der Auffahrunfall, bei dem die Gegenseite reguliert.

    Ein solcher Vorgang hat keine eigene Police - und deshalb kein
    Sonderkuendigungsrecht. Der Zustand ist `none` MIT Begruendung, nicht ein
    leeres Feld: "kein Recht" ist eine Aussage.
    """
    vorgang = _vorgang(kopf, title="Fremdverschulden", status="reported")
    fenster = vorgang["special_termination"]
    assert vorgang["policy_id"] is None
    assert fenster["termination_basis"] == "none"
    assert fenster["state"] == "none"
    assert "keiner eigenen Police" in fenster["rationale"]

    abweisung = client.post(
        f"{BASIS}/claims/{vorgang['id']}/close-negotiations",
        json={"negotiations_closed_on": _tage(-1).isoformat(), "reason": "user_confirmed"},
        headers=kopf,
    )
    assert abweisung.status_code == 422, abweisung.text
    assert abweisung.json()["detail"]["code"] == "insurance_claim_no_termination_right"


def test_der_fristanker_laesst_sich_nicht_per_patch_setzen(kopf: dict[str, str]) -> None:
    """Ein Datum ohne Herkunft ist eine Behauptung (Plan 4.8, Muster
    `main_due_source`).

    `negotiations_closed_on` steht in keinem PATCH-Feldsatz. Der Versuch
    veraendert nichts - und darf vor allem kein Fenster oeffnen.
    """
    police = _police(kopf, line_key=HAUSRAT)
    vorgang = _vorgang(kopf, policy_id=police["id"], status="reported")
    versuch = client.patch(
        f"{BASIS}/claims/{vorgang['id']}",
        json={
            "negotiations_closed_on": _tage(-1).isoformat(),
            "negotiations_closed_certainty": "belegt",
            "special_termination_state": "open_until",
        },
        headers={**kopf, "If-Match": f'"{vorgang["version"]}"'},
    )
    assert versuch.status_code == 200, versuch.text
    fenster = versuch.json()["special_termination"]
    assert fenster["anchor_on"] is None
    assert fenster["anchor_certainty"] == "unbekannt"
    assert fenster["state"] == "open_undetermined"

    # Und ein Anker vor dem Schadentag ist keiner.
    zu_frueh = client.post(
        f"{BASIS}/claims/{vorgang['id']}/close-negotiations",
        json={
            "negotiations_closed_on": _tage(-365).isoformat(),
            "reason": "user_confirmed",
        },
        headers=kopf,
    )
    assert zu_frueh.status_code == 422, zu_frueh.text
    assert zu_frueh.json()["detail"] == "insurance_claim_anchor_before_event"


def test_ein_belegter_anker_faellt_auf_geschaetzt_zurueck_wenn_die_buchung_verschwindet(
    kopf: dict[str, str],
) -> None:
    """Volle Anerkennung, bezahlt - und dann faellt der Beleg weg.

    Erst der Vorschlag, den die App als einzigen hart BELEGEN kann: bei
    `accepted_full` + `paid` enden die Verhandlungen mit der vollstaendigen
    Zahlung, und das Datum stammt aus einem Kontoumsatz. Danach der
    zurueckgenommene Import: Datum und Fenster bleiben stehen, nur der Nachweis
    ist weg. Die Alternative waere, dass eine Buchung nicht mehr loeschbar ist,
    weil irgendwo ein Schaden auf sie zeigt.
    """
    police = _police(kopf, line_key=HAUSRAT)
    konto = _konto(kopf)
    eingang = _buchung(kopf, konto, 250000, booked_date=_tage(-4))
    vorgang = _vorgang(kopf, policy_id=police["id"], status="reported", claimed_minor=250000)
    _zahlung(
        kopf,
        str(vorgang["id"]),
        transaction_id=eingang["id"],
        direction="inbound",
        kind="settlement",
        amount_minor=250000,
    )
    _status(
        kopf,
        str(vorgang["id"]),
        to_status="decided",
        decision="accepted_full",
        decided_on=_tage(-8).isoformat(),
    )
    _status(kopf, str(vorgang["id"]), to_status="paid")

    vorschlag = _fenster(kopf, str(vorgang["id"]))["suggestion"]
    assert vorschlag is not None
    # Nicht das Entscheidungsdatum, sondern der Zahlungseingang.
    assert vorschlag["negotiations_closed_on"] == _tage(-4).isoformat()
    assert vorschlag["reason"] == "full_payment"
    assert vorschlag["source"] == "transaction"
    assert vorschlag["certainty"] == "belegt"
    assert vorschlag["transaction_id"] == eingang["id"]

    belegt = _abschluss(
        kopf,
        str(vorgang["id"]),
        negotiations_closed_on=vorschlag["negotiations_closed_on"],
        reason=vorschlag["reason"],
        transaction_id=vorschlag["transaction_id"],
    )["special_termination"]
    assert belegt["anchor_certainty"] == "belegt"
    assert belegt["anchor_source"] == "transaction"
    assert belegt["anchor_transaction_id"] == eingang["id"]
    assert "zugeordneten Kontoumsatz" in belegt["rationale"]

    with psycopg.connect(_DSN, autocommit=True) as verbindung:
        verbindung.execute("delete from transactions where id = %s", (eingang["id"],))

    danach = _fenster(kopf, str(vorgang["id"]))
    assert danach["anchor_certainty"] == "geschaetzt"
    assert danach["anchor_transaction_id"] is None
    assert danach["anchor_on"] == _tage(-4).isoformat()
    assert danach["deadline_legal_on"] == _plus_monat(_tage(-4)).isoformat()


def test_der_loeschknopf_an_der_buchung_nimmt_den_belegten_anker_mit(
    kopf: dict[str, str],
) -> None:
    """Derselbe Rueckfall, aber ueber den Weg, den der Nutzer tatsaechlich geht.

    Der Test darueber loescht die Buchung per SQL und trifft damit die
    ON-DELETE-SET-NULL-Regel des Fremdschluessels. Ueber die Oberflaeche wird
    aber nie geloescht, sondern archiviert - und dabei griff die Regel nicht.
    Der Anker blieb "belegt" und zeigte auf einen Kontoumsatz, den der Nutzer
    nirgends mehr fand; das Sonderkuendigungsrecht haette an einem Phantom
    gehangen. Seit transaction_detach_service loest der Loeschpfad den Verweis
    selbst, und der Trigger stuft die Sicherheit zurueck.
    """
    police = _police(kopf, line_key=HAUSRAT)
    konto = _konto(kopf)
    eingang = _buchung(kopf, konto, 250000, booked_date=_tage(-4))
    vorgang = _vorgang(kopf, policy_id=police["id"], status="reported", claimed_minor=250000)
    _zahlung(
        kopf,
        str(vorgang["id"]),
        transaction_id=eingang["id"],
        direction="inbound",
        kind="settlement",
        amount_minor=250000,
    )
    _status(
        kopf,
        str(vorgang["id"]),
        to_status="decided",
        decision="accepted_full",
        decided_on=_tage(-8).isoformat(),
    )
    _status(kopf, str(vorgang["id"]), to_status="paid")

    vorschlag = _fenster(kopf, str(vorgang["id"]))["suggestion"]
    belegt = _abschluss(
        kopf,
        str(vorgang["id"]),
        negotiations_closed_on=vorschlag["negotiations_closed_on"],
        reason=vorschlag["reason"],
        transaction_id=vorschlag["transaction_id"],
    )["special_termination"]
    assert belegt["anchor_certainty"] == "belegt"

    entfernt = client.delete(f"/v1/transactions/{eingang['id']}", headers=kopf)
    assert entfernt.status_code == 204, entfernt.text

    danach = _fenster(kopf, str(vorgang["id"]))
    assert danach["anchor_certainty"] == "geschaetzt"
    assert danach["anchor_transaction_id"] is None
    # Das Datum bleibt - nur sein Nachweis ist weg.
    assert danach["anchor_on"] == _tage(-4).isoformat()

    # Und die Zahlung selbst zaehlt nicht mehr: der Vorgang gilt nicht laenger
    # als reguliert, obwohl das Geld im Bestand nicht mehr steht.
    stand = client.get(f"{BASIS}/claims/{vorgang['id']}", headers=kopf).json()
    assert stand["settled_minor"] == 0


# ---------------------------------------------------------------------------
# 3. Zahlungen - eine Summe, ein Schreiber
# ---------------------------------------------------------------------------


def test_eine_sammelueberweisung_deckt_mehrere_vorgaenge(kopf: dict[str, str]) -> None:
    """Pflichtfall 5, erste Haelfte (Plan 4.8: n:m ist kein Selbstzweck).

    Der Versicherer ueberweist einmal 500 Euro fuer zwei Schaeden. Danach ist
    die Buchung erschoepft - und darf keinem dritten Vorgang mehr als Kandidat
    angeboten werden.
    """
    police = _police(kopf, line_key=HAUSRAT)
    konto = _konto(kopf)
    ueberweisung = _buchung(kopf, konto, 50000, booked_date=_tage(-3))

    a = _vorgang(
        kopf,
        policy_id=police["id"],
        title="Sturmschaden Dach",
        occurred_on=_tage(-20).isoformat(),
        reported_on=_tage(-10).isoformat(),
        status="reported",
        claimed_minor=30000,
    )
    b = _vorgang(
        kopf,
        policy_id=police["id"],
        title="Sturmschaden Gartenhaus",
        occurred_on=_tage(-20).isoformat(),
        reported_on=_tage(-10).isoformat(),
        status="reported",
        claimed_minor=20000,
    )

    kandidaten = client.get(
        f"{BASIS}/claims/{a['id']}/transaction-candidates", headers=kopf
    ).json()
    treffer = [k for k in kandidaten if k["transaction_id"] == ueberweisung["id"]]
    assert treffer, "die Ueberweisung muss als Kandidat erscheinen"
    assert treffer[0]["remaining_minor"] == 50000
    assert treffer[0]["evidence"], "ein Kandidat ohne Begruendung ist nicht ueberpruefbar"

    _zahlung(
        kopf,
        str(a["id"]),
        transaction_id=ueberweisung["id"],
        direction="inbound",
        kind="settlement",
        amount_minor=30000,
    )
    # Der Rest der Buchung ist noch frei - genau das macht die Sammelbuchung aus.
    rest = client.get(f"{BASIS}/claims/{b['id']}/transaction-candidates", headers=kopf).json()
    frei = [k for k in rest if k["transaction_id"] == ueberweisung["id"]]
    assert frei and frei[0]["remaining_minor"] == 20000

    _zahlung(
        kopf,
        str(b["id"]),
        transaction_id=ueberweisung["id"],
        direction="inbound",
        kind="settlement",
        amount_minor=20000,
    )

    assert _hole(kopf, str(a["id"]))["settled_minor"] == 30000
    assert _hole(kopf, str(a["id"]))["open_minor"] == 0
    assert _hole(kopf, str(b["id"]))["settled_minor"] == 20000
    assert _hole(kopf, str(b["id"]))["open_minor"] == 0

    c = _vorgang(
        kopf,
        policy_id=police["id"],
        title="Sturmschaden Zaun",
        occurred_on=_tage(-20).isoformat(),
        reported_on=_tage(-10).isoformat(),
        status="reported",
        claimed_minor=15000,
    )
    erschoepft = client.get(
        f"{BASIS}/claims/{c['id']}/transaction-candidates", headers=kopf
    ).json()
    assert [k for k in erschoepft if k["transaction_id"] == ueberweisung["id"]] == []


def test_ein_vorgang_kennt_abschlag_und_restzahlung(kopf: dict[str, str]) -> None:
    """Pflichtfall 5, zweite Haelfte.

    Abschlag und Restzahlung sind zwei Zahlungen an einem Vorgang, keine
    Korrektur der ersten. `settled_minor` ist ihre Summe und hat genau einen
    Schreiber.
    """
    police = _police(kopf, line_key=HAUSRAT)
    konto = _konto(kopf)
    vorgang = _vorgang(
        kopf,
        policy_id=police["id"],
        title="Leitungswasser Bad",
        occurred_on=_tage(-40).isoformat(),
        reported_on=_tage(-35).isoformat(),
        status="reported",
        claimed_minor=40000,
        approved_minor=40000,
    )
    abschlag = _buchung(kopf, konto, 15000, booked_date=_tage(-20))
    restzahlung = _buchung(kopf, konto, 25000, booked_date=_tage(-4))

    _zahlung(
        kopf,
        str(vorgang["id"]),
        transaction_id=abschlag["id"],
        direction="inbound",
        kind="advance",
        amount_minor=15000,
    )
    zwischenstand = _hole(kopf, str(vorgang["id"]))
    assert zwischenstand["settled_minor"] == 15000
    assert zwischenstand["open_minor"] == 25000

    _zahlung(
        kopf,
        str(vorgang["id"]),
        transaction_id=restzahlung["id"],
        direction="inbound",
        kind="settlement",
        amount_minor=25000,
    )
    fertig = _hole(kopf, str(vorgang["id"]))
    assert fertig["settled_minor"] == 40000
    assert fertig["open_minor"] == 0
    assert fertig["payment_count"] == 2
    assert [zahlung["kind"] for zahlung in fertig["payments"]] == ["advance", "settlement"]

    # Eine geloeste Zuordnung faellt von allein aus der Summe - die Summe wird
    # neu gebildet und nicht fortgeschrieben.
    geloest = client.delete(
        f"{BASIS}/claims/{vorgang['id']}/payments/{fertig['payments'][0]['id']}", headers=kopf
    )
    assert geloest.status_code == 204, geloest.text
    danach = _hole(kopf, str(vorgang["id"]))
    assert danach["settled_minor"] == 25000
    # Die BUCHUNG bleibt: geloescht wurde die Zuordnung, nicht das Geld.
    assert client.get(f"/v1/transactions/{abschlag['id']}", headers=kopf).status_code == 200


def test_eine_direktzahlung_an_die_werkstatt_kommt_ohne_buchung_aus(
    kopf: dict[str, str],
) -> None:
    """Pflichtfall 6 (Plan 4.8).

    Der Versicherer zahlt direkt an die Werkstatt: keine Buchung, aber sehr wohl
    eine Zahlung. Ohne sie waere ein vollstaendig regulierter Schaden in der
    Uebersicht ein offener.
    """
    police = _police(kopf, line_key=KASKO, insurer_name="Kfz Beispiel AG")
    vorgang = _vorgang(
        kopf,
        policy_id=police["id"],
        title="Hagelschaden",
        occurred_on=_tage(-30).isoformat(),
        status="reported",
        claimed_minor=180000,
        deductible_minor=30000,
        approved_minor=150000,
    )
    assert vorgang["paid_to_third_party"] is False

    zahlung = _zahlung(
        kopf,
        str(vorgang["id"]),
        direction="inbound",
        kind="settlement",
        amount_minor=150000,
        booked_on=_tage(-2).isoformat(),
        note="Direkt an die Werkstatt",
    )
    assert zahlung["transaction_id"] is None

    danach = _hole(kopf, str(vorgang["id"]))
    assert danach["settled_minor"] == 150000
    assert danach["open_minor"] == 0
    assert danach["paid_to_third_party"] is True

    # Eine Zahlung ohne Buchung UND ohne Datum ist weder belegt noch
    # einsortierbar - und deshalb keine Zahlung.
    ohne_alles = client.post(
        f"{BASIS}/claims/{vorgang['id']}/payments",
        json={"direction": "inbound", "kind": "settlement", "amount_minor": 100},
        headers=kopf,
    )
    assert ohne_alles.status_code == 422, ohne_alles.text


def test_mehr_zuordnen_als_die_buchung_hergibt_wird_abgewiesen(kopf: dict[str, str]) -> None:
    """Pflichtfall 7 - dieselbe Zusage wie im Erstattungsabgleich.

    Der Trigger sitzt in SQL und nicht in Python: eine Sammelueberweisung deckt
    mehrere Schaeden, aber nur bis zu ihrem Betrag.
    """
    police = _police(kopf, line_key=HAUSRAT)
    konto = _konto(kopf)
    ueberweisung = _buchung(kopf, konto, 10000, booked_date=_tage(-3))
    a = _vorgang(kopf, policy_id=police["id"], status="reported", claimed_minor=6000)
    b = _vorgang(kopf, policy_id=police["id"], status="reported", claimed_minor=6000)

    _zahlung(
        kopf,
        str(a["id"]),
        transaction_id=ueberweisung["id"],
        direction="inbound",
        kind="settlement",
        amount_minor=6000,
    )
    zuviel = client.post(
        f"{BASIS}/claims/{b['id']}/payments",
        json={
            "transaction_id": ueberweisung["id"],
            "direction": "inbound",
            "kind": "settlement",
            "amount_minor": 5000,
        },
        headers=kopf,
    )
    assert zuviel.status_code == 409, zuviel.text
    assert zuviel.json()["detail"]["code"] == "insurance_claim_payment_overallocated"
    assert _hole(kopf, str(b["id"]))["settled_minor"] == 0

    # Der freie Rest laesst sich sehr wohl zuordnen.
    _zahlung(
        kopf,
        str(b["id"]),
        transaction_id=ueberweisung["id"],
        direction="inbound",
        kind="settlement",
        amount_minor=4000,
    )
    assert _hole(kopf, str(b["id"]))["settled_minor"] == 4000

    # Und die Richtung stimmt oder die Zuordnung entsteht nicht: ein
    # Zahlungseingang ist kein Ausgang.
    falsche_richtung = client.post(
        f"{BASIS}/claims/{a['id']}/payments",
        json={
            "transaction_id": ueberweisung["id"],
            "direction": "outbound",
            "kind": "deductible",
            "amount_minor": 1,
        },
        headers=kopf,
    )
    assert falsche_richtung.status_code == 422, falsche_richtung.text
    assert falsche_richtung.json()["detail"] == "insurance_claim_payment_direction_mismatch"


# ---------------------------------------------------------------------------
# 4. Haushaltsgrenze und Nebenlaeufigkeit
# ---------------------------------------------------------------------------


def test_ein_fremder_haushalt_bekommt_auf_jedem_neuen_endpunkt_404(
    kopf: dict[str, str], fremd: dict[str, str]
) -> None:
    """Pflichtfall 8.

    404 und nicht 403: die Existenz einer fremden Ressource soll nicht verraten
    werden. Geprueft wird jeder schreibende und jeder lesende Einzelendpunkt der
    Phase, samt der Bruecke ins Erstattungsmodul.
    """
    police = _police(kopf, line_key=HAUSRAT)
    vorgang = _vorgang(kopf, policy_id=police["id"], status="reported")
    zahlung = _zahlung(
        kopf,
        str(vorgang["id"]),
        direction="inbound",
        kind="settlement",
        amount_minor=5000,
        booked_on=_tage(-2).isoformat(),
    )
    zahler = client.post(
        f"{ERSTATTUNG}/payers",
        json={"payer_type": "pkv", "name": "Beispiel Versicherung AG"},
        headers=kopf,
    )
    assert zahler.status_code == 201, zahler.text

    vorgang_id = str(vorgang["id"])
    anfragen: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = [
        ("GET", f"{BASIS}/claims/{vorgang_id}", None, {}),
        (
            "PATCH",
            f"{BASIS}/claims/{vorgang_id}",
            {"title": "fremd"},
            {"If-Match": '"1"'},
        ),
        ("DELETE", f"{BASIS}/claims/{vorgang_id}", None, {}),
        ("POST", f"{BASIS}/claims/{vorgang_id}/status", {"to_status": "withdrawn"}, {}),
        (
            "POST",
            f"{BASIS}/claims/{vorgang_id}/close-negotiations",
            {"negotiations_closed_on": _tage(-1).isoformat(), "reason": "user_confirmed"},
            {},
        ),
        ("GET", f"{BASIS}/claims/{vorgang_id}/transaction-candidates", None, {}),
        ("GET", f"{BASIS}/claims/{vorgang_id}/payments", None, {}),
        ("GET", f"{BASIS}/policies/{police['id']}/claims", None, {}),
        (
            "POST",
            f"{BASIS}/claims/{vorgang_id}/payments",
            {
                "direction": "inbound",
                "kind": "settlement",
                "amount_minor": 100,
                "booked_on": _tage(-1).isoformat(),
            },
            {},
        ),
        ("DELETE", f"{BASIS}/claims/{vorgang_id}/payments/{zahlung['id']}", None, {}),
        (
            "POST",
            f"{BASIS}/policies/{police['id']}/claims",
            {"title": "fremd", "occurred_on": _tage(-1).isoformat()},
            {},
        ),
        (
            "POST",
            f"{BASIS}/claims",
            {
                "policy_id": str(police["id"]),
                "title": "fremd",
                "occurred_on": _tage(-1).isoformat(),
            },
            {},
        ),
        ("POST", f"{ERSTATTUNG}/payers/{zahler.json()['id']}/suggest-policy", None, {}),
    ]
    for methode, pfad, koerper, zusatz in anfragen:
        antwort = client.request(methode, pfad, json=koerper, headers={**fremd, **zusatz})
        assert antwort.status_code == 404, f"{methode} {pfad}: {antwort.status_code}"

    # Und der Vorgang steht danach unveraendert im eigenen Haushalt.
    assert _hole(kopf, vorgang_id)["title"] == "Wasserschaden Kueche"
    assert len(_hole(kopf, vorgang_id)["payments"]) == 1
    # Auch der Filter der haushaltsweiten Liste ist kein Durchgriff: eine fremde
    # Police antwortet 404 und nicht mit der leeren Liste. Die leere Liste waere
    # eine Auskunft darueber, dass es die Police gibt.
    assert (
        client.get(f"{BASIS}/claims?policy_id={police['id']}", headers=fremd).status_code
        == 404
    )
    # Die eigene haushaltsweite Liste bleibt fuer den Fremden leer.
    assert client.get(f"{BASIS}/claims", headers=fremd).json() == []


def test_zahlungsliste_eines_fremden_vorgangs_antwortet_404(
    kopf: dict[str, str], fremd: dict[str, str]
) -> None:
    """Die Unterliste prueft den Vorgang, nicht nur den Haushalt.

    Die leere Liste waere kein Datenabfluss, aber eine falsche Auskunft: sie
    sagt "diesen Vorgang gibt es, er hat nur keine Zahlungen". Ein frei
    erfundener Vorgang muss dieselbe Antwort bekommen wie ein fremder.
    """
    police = _police(kopf, line_key=HAUSRAT)
    vorgang = _vorgang(kopf, policy_id=police["id"], status="reported")
    antwort = client.get(f"{BASIS}/claims/{vorgang['id']}/payments", headers=fremd)
    assert antwort.status_code == 404, antwort.text

    erfunden = client.get(f"{BASIS}/claims/{uuid.uuid4()}/payments", headers=kopf)
    assert erfunden.status_code == 404, erfunden.text


def test_vorgangsliste_einer_fremden_police_antwortet_404(
    kopf: dict[str, str], fremd: dict[str, str]
) -> None:
    """Lesen und Schreiben auf demselben Pfad antworten gleich.

    POST auf `/policies/{id}/claims` pruefte die Police schon, GET filterte sie
    nur - dieselbe fremde Kennung bekam damit zwei verschiedene Antworten.
    """
    police = _police(kopf, line_key=HAUSRAT)
    antwort = client.get(f"{BASIS}/policies/{police['id']}/claims", headers=fremd)
    assert antwort.status_code == 404, antwort.text

    erfunden = client.get(f"{BASIS}/policies/{uuid.uuid4()}/claims", headers=kopf)
    assert erfunden.status_code == 404, erfunden.text


def test_if_match_ist_pflicht_und_ein_veralteter_stand_verliert(kopf: dict[str, str]) -> None:
    """Pflichtfall 9 (Plan 5.4).

    Ein fehlender Header ist 428 und nicht 400: der Client hat nichts falsch
    formuliert, ihm fehlt nur die Vorbedingung. Und eine Zahlung entwertet das
    ETag - sie verschiebt die Summe des Vorgangs.
    """
    police = _police(kopf, line_key=HAUSRAT)
    vorgang = _vorgang(kopf, policy_id=police["id"], status="reported")

    ohne = client.patch(f"{BASIS}/claims/{vorgang['id']}", json={"title": "Neu"}, headers=kopf)
    assert ohne.status_code == 428, ohne.text
    assert ohne.json()["detail"] == "if_match_required"

    erst = client.get(f"{BASIS}/claims/{vorgang['id']}", headers=kopf)
    etag = str(erst.headers["ETag"])
    assert etag == '"1"'

    geaendert = client.patch(
        f"{BASIS}/claims/{vorgang['id']}",
        json={"title": "Rohrbruch Kueche"},
        headers={**kopf, "If-Match": etag},
    )
    assert geaendert.status_code == 200, geaendert.text
    assert geaendert.headers["ETag"] == '"2"'

    veraltet = client.patch(
        f"{BASIS}/claims/{vorgang['id']}",
        json={"title": "Noch einmal"},
        headers={**kopf, "If-Match": etag},
    )
    assert veraltet.status_code == 409, veraltet.text
    assert veraltet.json()["detail"] == "version_conflict"
    assert veraltet.headers["ETag"] == '"2"'
    assert _hole(kopf, str(vorgang["id"]))["title"] == "Rohrbruch Kueche"

    # Zahlungen tragen kein eigenes ETag - sie sind eine Menge am Vorgang. Sie
    # verschieben aber seine Summe und damit seine Version.
    _zahlung(
        kopf,
        str(vorgang["id"]),
        direction="inbound",
        kind="settlement",
        amount_minor=5000,
        booked_on=_tage(-1).isoformat(),
    )
    nach_zahlung = client.patch(
        f"{BASIS}/claims/{vorgang['id']}",
        json={"title": "Nach der Zahlung"},
        headers={**kopf, "If-Match": '"2"'},
    )
    assert nach_zahlung.status_code == 409, nach_zahlung.text
