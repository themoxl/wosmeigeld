"""Bedarfsabgleich und Deckungsluecken (docs/VERSICHERUNGEN-PLAN.md, P6).

Diese Datei prueft den heikelsten Teil des Moduls: eine Maschine, die aus
Angaben und Bestand die Aussage "hierfuer besteht kein Schutz" ableitet. Fuenf
Zusagen tragen sie, und jeder Testfall gehoert zu genau einer:

1. **Regelbasiert und nachvollziehbar.** Jeder Befund nennt die Regel, die
   Fundstelle, das Pruefdatum und die Angaben, auf denen er beruht. Geprueft
   wird das an einem konkreten Befund Feld fuer Feld und nicht am Vorhandensein
   des Schluessels.
2. **Kein Schutzscore.** Keine Note, kein Prozentwert, keine Gesamtzahl ueber
   alle Risikofelder. Die Antworten werden aktiv danach durchsucht.
3. **Keine Beratung, kein Vertrieb.** Kein Anbietername, kein Tarif, kein
   Wechselvorschlag - auch nicht in einem Befundtext.
4. **Ein Befund ueber einen ungepflegten Bestand ist schaedlich.** Eine
   unbeantwortete Frage ergibt "Angabe fehlt" und NIE eine Luecke; "Angabe
   fehlt" wird nirgends als Luecke gezaehlt.
5. **Bedarfsangaben sind Gesundheitsdaten.** Das Personenprofil haengt an
   `health_can_access_person`; ein Haushaltsmitglied ohne Freigabe liest und
   schreibt es nicht.

## Aufbau

Die vier Referenzprofile aus Plan 13.3 sind der Kern: je ein Haushalt mit
definierten Angaben und definiertem Bestand, dazu die VOLLSTAENDIGE
Ergebnistabelle `rule_key -> outcome`, Zeile fuer Zeile. Ein Bedarfsabgleich,
von dem nur ein paar Stichproben geprueft sind, ist nicht geprueft - deshalb
vergleicht jeder Profiltest die gesamte Tabelle in einem Rutsch und nicht
einzelne Zellen.

Die Tabellen tragen mehr Zeilen als Plan 13.3 ausschreibt. Der Plan nennt je
Profil nur die bemerkenswerten Regeln und sagt fuer Profil A ausdruecklich
"nicht aufgefuehrte Regeln muessen `not_applicable` liefern"; fuer B bis D ist
dieser Satz nicht haltbar (R2 und R17 haben die Bedingung "immer" und koennen
gar nicht `not_applicable` werden). Die hier ergaenzten Zellen sind deshalb aus
10.2 und 10.3 abgeleitet und in `## Abgeleitete Zellen` am jeweiligen Profil
begruendet.

## Zwei Abweichungen, die beim Bauen dieser Tabellen aufgefallen sind

Beide sind bei der Abnahme behoben worden; der Testfall
`test_pflichtangaben_werden_erst_nach_der_bedingung_verlangt` haelt die
Reihenfolge seither fest. `_evaluate_one` prueft `required_facts` jetzt ZULETZT.

* **R22 fragte jede gesetzlich versicherte Person nach ihrem
  Beihilfebemessungssatz.** Die Regel gilt nur fuer
  `health_coverage = 'pkv_beihilfe'`, verlangte `beihilfe_rate_bp` aber schon
  vor ihrer Bedingung - fuer eine GKV-Person entstand `unclear` statt
  `not_applicable`, und die Onboarding-Fuehrung verlangte eine Angabe, die
  diesen Haushalt nichts angeht.
* **R4a fragte Kinder nach ihrem Nettoeinkommen.** Dieselbe Reihenfolge, hier
  gegenueber `depends_on_line_key`: die Anschlussregel setzt eine vorhandene
  Berufsunfaehigkeitspolice voraus, verlangte `net_income_minor` aber schon
  davor. Ohne BU-Police im Bestand lautete das Ergebnis `unclear` statt
  `not_applicable` - Plan 13.3 tabelliert fuer Profil A ausdruecklich
  "greift erst mit vorhandener BU".

Laeuft wie alle API-Tests gegen ein echtes lokales Supabase mit echten
Anmeldungen: die Gesundheitsschranke steht in der Datenbank, ein Test mit
gemockter Sitzung prueft sie nicht.
"""

import re
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.data import insurance_rules
from app.main import app
from app.services import insurance_needs_service as needs_service
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

BASIS = "/v1/insurance"
HEUTE = date.today()

# Die sieben Ergebniszustaende aus Plan 10.2 - mehr darf in `counts` nicht
# auftauchen, und ein achter waere entweder ein Score oder ein Zwischending.
ERGEBNISZUSTAENDE = frozenset(
    {
        "fulfilled",
        "underinsured",
        "gap",
        "unclear",
        "overinsured",
        "dismissed",
        "not_applicable",
    }
)

# Alles, was als Luecke gilt. `unclear` steht bewusst NICHT dabei.
LUECKENZUSTAENDE = frozenset({"gap", "underinsured"})


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


def _geburtstag(alter: int) -> str:
    """Ein Geburtsdatum, das heute GENAU dieses Alter ergibt - an jedem Tag.

    Der 1. Januar, weil `_age_years` den Geburtstag mit `(Monat, Tag) <` prueft:
    zum 1. Januar ist er in jedem Lauf schon vorbei, und der Test haengt nicht
    daran, wann er laeuft.
    """
    return date(HEUTE.year - alter, 1, 1).isoformat()


def _haushalt(kennung: str) -> dict[str, str]:
    token = _signup(f"{kennung}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-61")
    haushalt = client.post(
        "/v1/households", json={"name": f"P6-{kennung}"}, headers=_auth_headers(token)
    )
    assert haushalt.status_code == 201, haushalt.text
    return {**_auth_headers(token), "X-Household-Id": str(haushalt.json()["id"])}


def _profil(kopf: dict[str, str], **angaben: Any) -> dict[str, Any]:
    antwort = client.put(f"{BASIS}/needs/profile", json=angaben, headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _person(kopf: dict[str, str], name: str, alter: int, **angaben: Any) -> str:
    """Person im Erstattungsmodul anlegen und ihr Bedarfsprofil setzen.

    Es gibt keine zweite Personenliste (Plan 4.4): die Person entsteht ueber
    `/v1/reimbursements/people`, ihre Bedarfsangaben haengen daran.
    """
    angelegt = client.post(
        "/v1/reimbursements/people",
        json={"display_name": name, "birth_date": _geburtstag(alter)},
        headers=kopf,
    )
    assert angelegt.status_code == 201, angelegt.text
    person_id = str(angelegt.json()["id"])
    if angaben:
        gesetzt = client.put(
            f"{BASIS}/needs/profile/persons/{person_id}", json=angaben, headers=kopf
        )
        assert gesetzt.status_code == 200, gesetzt.text
    return person_id


def _police(kopf: dict[str, str], line_key: str, **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "line_key": line_key,
        "insurer_name": felder.pop("insurer_name", "Nordwacht Versicherung AG"),
    }
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/policies", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _bausteine(kopf: dict[str, str], policy_id: str, *schluessel: str) -> None:
    antwort = client.put(
        f"{BASIS}/policies/{policy_id}/coverages",
        json={"coverages": [{"coverage_key": key, "is_included": True} for key in schluessel]},
        headers=kopf,
    )
    assert antwort.status_code == 200, antwort.text


def _fahrzeug(kopf: dict[str, str], alter_jahre: int, label: str = "Kombi") -> str:
    antwort = client.post(
        f"{BASIS}/objects",
        json={"kind": "vehicle", "label": label, "build_year": HEUTE.year - alter_jahre},
        headers=kopf,
    )
    assert antwort.status_code == 201, antwort.text
    return str(antwort.json()["id"])


def _darlehen(kopf: dict[str, str], minor: int, name: str = "Immobiliendarlehen") -> None:
    antwort = client.post(
        "/v1/assets",
        json={"name": name, "type": "debt", "current_value_minor": minor},
        headers=kopf,
    )
    assert antwort.status_code == 201, antwort.text


def _check(kopf: dict[str, str]) -> dict[str, Any]:
    antwort = client.get(f"{BASIS}/needs/check", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _tabelle(ergebnis: dict[str, Any]) -> dict[tuple[str, str | None], str]:
    """Die Antwort als `(rule_key, Personenname) -> outcome`.

    Der Personenname statt der Personen-UUID, damit ein Fehlschlag lesbar ist:
    `('R4', 'Bela Kind')` sagt, welche Zeile falsch ist, eine UUID nicht.
    """
    tabelle: dict[tuple[str, str | None], str] = {}
    for befund in ergebnis["findings"]:
        schluessel = (str(befund["rule_key"]), befund.get("person_name"))
        assert schluessel not in tabelle, f"Doppelter Befund fuer {schluessel}"
        tabelle[schluessel] = str(befund["outcome"])
    return tabelle


def _befund(ergebnis: dict[str, Any], rule_key: str, person: str | None = None) -> dict[str, Any]:
    for befund in ergebnis["findings"]:
        if befund["rule_key"] == rule_key and befund.get("person_name") == person:
            return dict(befund)
    raise AssertionError(f"Kein Befund {rule_key} fuer {person!r}")


# ---------------------------------------------------------------------------
# Referenzprofil A - Single, Miete, angestellt (Plan 13.3)
# ---------------------------------------------------------------------------
# Fakten laut Plan: housing_form='rent', vehicle_count=0, pets=[],
# travel_frequency='occasional', has_oil_tank=false; Person:
# employment_status='employed', health_coverage='gkv', has_dependents=false.
# Bestand: GKV-Police, Privathaftpflicht mit 10 Mio.
#
# Die uebrigen Fragen des Bogens sind mit "nein" beantwortet - genau das ist der
# Unterschied zwischen "nein" und "nie gefragt", und ohne ihn stuende in der
# Tabelle ueberall `unclear` statt `not_applicable`. Bewusst UNBEANTWORTET
# bleibt `liquid_reserve_minor`: darauf beruht die Planzeile "R15 = unclear".

A_PERSON = "Alea Single"

ERWARTUNG_A: dict[tuple[str, str | None], str] = {
    # --- Plan 13.3, Profil A, Zeile fuer Zeile
    ("R1", A_PERSON): "fulfilled",  # GKV vorhanden
    ("R2", None): "fulfilled",
    ("R2a", None): "fulfilled",  # 10 Mio erreicht
    ("R4", A_PERSON): "gap",  # keine BU-Police
    ("R4a", A_PERSON): "not_applicable",  # greift erst mit vorhandener BU
    ("R16", None): "gap",  # Auslandsreise fehlt
    ("R17", None): "gap",  # Rechtsschutz, S2
    ("R15", None): "unclear",  # liquid_reserve_minor nicht erfasst
    ("R23", A_PERSON): "gap",  # Zahnzusatz, S3
    # --- "R3, R3b, R5, R7, R8, R8a, R9a, R10-R14a, R18-R22, R24: not_applicable"
    ("R3", None): "not_applicable",
    ("R3b", A_PERSON): "not_applicable",
    ("R7", None): "not_applicable",
    ("R8", None): "not_applicable",
    ("R8a", None): "not_applicable",
    ("R9a", None): "not_applicable",
    ("R10", None): "not_applicable",
    ("R11", None): "not_applicable",
    ("R11a", None): "not_applicable",
    ("R12", None): "not_applicable",
    ("R13", None): "not_applicable",
    ("R14", None): "not_applicable",
    ("R18", None): "not_applicable",
    ("R20", None): "not_applicable",
    ("R21", None): "not_applicable",
    ("R22", A_PERSON): "not_applicable",
    ("R24", None): "not_applicable",
    # --- Abgeleitete Zellen (in 13.3 nicht aufgefuehrt)
    # R1a: Versichertenstatus ist gesetzlich, die private Pflegepflicht trifft
    #      nur die private Krankenversicherung.
    ("R1a", A_PERSON): "not_applicable",
    # R15a: Anschlussregel an eine vorhandene Hausratpolice - es gibt keine.
    ("R15a", None): "not_applicable",
    # R7b: kein Sterbedatum erfasst, die Person setzt nichts aus.
    ("R7b", A_PERSON): "not_applicable",
}


@pytest.fixture(scope="module")
def profil_a() -> dict[str, Any]:
    """Modulweit: der Haushalt ist unveraenderlich und wird nur gelesen.

    Modulweite Fixtures baut pytest vor dem funktionsweiten Aufraeumen auf; der
    Haushalt ueberlebt deshalb die Tests und faellt erst am Ende des Laufs.
    """
    kopf = _haushalt("p6-a")
    _profil(
        kopf,
        housing_form="rent",
        living_area_sqm=45,
        elemental_zone="zurs1",
        has_oil_tank=False,
        construction_active=False,
        has_photovoltaic=False,
        contents_value_minor=1_500_000,
        has_open_loans=False,
        children_count=0,
        pets=[],
        travel_frequency="occasional",
        vehicle_count=0,
        side_business=False,
        volunteering=False,
        has_drone=False,
        has_hunting_license=False,
        has_boat=False,
    )
    _person(kopf, A_PERSON, 34, employment_status="employed", health_coverage="gkv",
            has_dependents=False)
    _police(kopf, "health_statutory", insurer_name="Allgemeine Ortskrankenkasse")
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)
    return {"kopf": kopf, "ergebnis": _check(kopf)}


# ---------------------------------------------------------------------------
# Referenzprofil B - Paar mit zwei Kindern (4 und 9), Darlehen 180.000 EUR
# ---------------------------------------------------------------------------

B_ELTERN = ("Berit Eltern", "Bodo Eltern")
B_KINDER = ("Bela Kind", "Bine Kind")

ERWARTUNG_B: dict[tuple[str, str | None], str] = {
    # --- Plan 13.3, Profil B
    ("R2", None): "fulfilled",
    ("R2a", None): "fulfilled",
    ("R3", None): "gap",  # Baustein "deliktunfaehige Kinder" nicht erfasst
    ("R7", None): "gap",  # Risikoleben fehlt, Zielwert open_debt = 180.000 EUR
    ("R15", None): "fulfilled",  # Hausrat vorhanden
    # R15a: 40.000 EUR < 85 x 650 EUR = 55.250 EUR. Die Planzelle sagt `gap`,
    # ihre eigene Bemerkung dazu "Unterversicherung" - und 10.2 definiert genau
    # dafuer `underinsured` ("Police vorhanden, Summe unter Zielwert"). Die
    # Zelle widerspricht der Zustandsliste; geprueft wird die Zustandsliste.
    ("R15a", None): "underinsured",
    ("R3b", B_KINDER[0]): "not_applicable",  # beide Kinder unter 25
    ("R3b", B_KINDER[1]): "not_applicable",
    ("R16", None): "gap",  # wie A
    ("R17", None): "gap",  # wie A
    # --- Abgeleitete Zellen
    # R1/R23 "wie A", je Person einmal - auch fuer die Kinder, sie sind
    # familienversichert erfasst.
    ("R1", B_ELTERN[0]): "fulfilled",
    ("R1", B_ELTERN[1]): "fulfilled",
    ("R1", B_KINDER[0]): "fulfilled",
    ("R1", B_KINDER[1]): "fulfilled",
    ("R23", B_ELTERN[0]): "gap",
    ("R23", B_ELTERN[1]): "gap",
    ("R23", B_KINDER[0]): "gap",
    ("R23", B_KINDER[1]): "gap",
    # R4 "je Person einmal": die Erwerbstaetigen tragen die Luecke, die Kinder
    # erzielen kein Erwerbseinkommen.
    ("R4", B_ELTERN[0]): "gap",
    ("R4", B_ELTERN[1]): "gap",
    ("R4", B_KINDER[0]): "not_applicable",
    ("R4", B_KINDER[1]): "not_applicable",
    # R4a: Anschlussregel ohne BU-Police im Bestand.
    ("R4a", B_ELTERN[0]): "not_applicable",
    ("R4a", B_ELTERN[1]): "not_applicable",
    ("R4a", B_KINDER[0]): "not_applicable",
    ("R4a", B_KINDER[1]): "not_applicable",
    ("R1a", B_ELTERN[0]): "not_applicable",
    ("R1a", B_ELTERN[1]): "not_applicable",
    ("R1a", B_KINDER[0]): "not_applicable",
    ("R1a", B_KINDER[1]): "not_applicable",
    ("R22", B_ELTERN[0]): "not_applicable",
    ("R22", B_ELTERN[1]): "not_applicable",
    ("R22", B_KINDER[0]): "not_applicable",
    ("R22", B_KINDER[1]): "not_applicable",
    ("R3b", B_ELTERN[0]): "not_applicable",
    ("R3b", B_ELTERN[1]): "not_applicable",
    ("R7b", B_ELTERN[0]): "not_applicable",
    ("R7b", B_ELTERN[1]): "not_applicable",
    ("R7b", B_KINDER[0]): "not_applicable",
    ("R7b", B_KINDER[1]): "not_applicable",
    # Wohnform Miete, kein Fahrzeug, kein Tier, kein Oeltank, kein Bauvorhaben.
    ("R8", None): "not_applicable",
    ("R8a", None): "not_applicable",
    ("R9a", None): "not_applicable",
    ("R10", None): "not_applicable",
    ("R11", None): "not_applicable",
    ("R11a", None): "not_applicable",
    ("R12", None): "not_applicable",
    ("R13", None): "not_applicable",
    ("R14", None): "not_applicable",
    ("R18", None): "not_applicable",
    ("R20", None): "not_applicable",
    ("R21", None): "not_applicable",
    ("R24", None): "not_applicable",
}


@pytest.fixture(scope="module")
def profil_b() -> dict[str, Any]:
    kopf = _haushalt("p6-b")
    _profil(
        kopf,
        housing_form="rent",
        living_area_sqm=85,
        elemental_zone="zurs1",
        has_oil_tank=False,
        construction_active=False,
        has_photovoltaic=False,
        contents_value_minor=4_000_000,
        liquid_reserve_minor=500_000,
        has_open_loans=True,
        children_count=2,
        pets=[],
        travel_frequency="occasional",
        vehicle_count=0,
        side_business=False,
        volunteering=False,
        has_drone=False,
        has_hunting_license=False,
        has_boat=False,
    )
    _person(kopf, B_ELTERN[0], 38, employment_status="employed", health_coverage="gkv",
            has_dependents=True, net_income_minor=280_000)
    _person(kopf, B_ELTERN[1], 40, employment_status="employed", health_coverage="gkv",
            has_dependents=True, net_income_minor=250_000)
    _person(kopf, B_KINDER[0], 4, employment_status="child", health_coverage="gkv",
            has_dependents=False)
    _person(kopf, B_KINDER[1], 9, employment_status="child", health_coverage="gkv",
            has_dependents=False)
    _darlehen(kopf, 18_000_000)
    _police(kopf, "health_statutory", insurer_name="Allgemeine Ortskrankenkasse")
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)
    _police(kopf, "household_contents", sum_insured_minor=4_000_000)
    return {"kopf": kopf, "ergebnis": _check(kopf)}


# ---------------------------------------------------------------------------
# Referenzprofil C - Immobilienbesitzer, 140 qm, ZUERS 2, Oeltank, ein Fahrzeug
# ---------------------------------------------------------------------------

C_PERSON = "Clara Eigen"

ERWARTUNG_C: dict[tuple[str, str | None], str] = {
    # --- Plan 13.3, Profil C
    ("R10", None): "fulfilled",  # Wohngebaeude vorhanden
    ("R11", None): "gap",  # Elementarbaustein fehlt, S1
    ("R13", None): "gap",  # Oeltank ohne Gewaesserschadenhaftpflicht
    ("R15a", None): "fulfilled",  # 95.000 EUR >= 140 x 650 EUR = 91.000 EUR
    ("R8", None): "fulfilled",
    ("R9a", None): "not_applicable",  # Fahrzeug juenger als 10 Jahre
    ("R12", None): "not_applicable",  # nicht vermietet
    ("R11a", None): "not_applicable",  # kein Bauvorhaben
    # --- Abgeleitete Zellen
    # Der im Plan genannte Bestand fuehrt weder eine Kranken- noch eine
    # Privathaftpflicht- noch eine Rechtsschutzpolice: R1, R2, R17, R23 und R4
    # melden deshalb eine Luecke, R2a faellt als Anschlussregel aus.
    ("R1", C_PERSON): "gap",
    ("R1a", C_PERSON): "not_applicable",
    ("R2", None): "gap",
    ("R2a", None): "not_applicable",
    ("R17", None): "gap",
    ("R23", C_PERSON): "gap",
    ("R4", C_PERSON): "gap",
    ("R4a", C_PERSON): "not_applicable",
    ("R22", C_PERSON): "not_applicable",
    ("R3b", C_PERSON): "not_applicable",
    ("R7b", C_PERSON): "not_applicable",
    ("R3", None): "not_applicable",
    ("R7", None): "not_applicable",
    ("R8a", None): "not_applicable",
    ("R14", None): "not_applicable",
    ("R15", None): "fulfilled",
    ("R16", None): "not_applicable",
    ("R18", None): "not_applicable",
    ("R20", None): "not_applicable",
    ("R21", None): "not_applicable",
    ("R24", None): "not_applicable",
}


def _baue_profil_c(kennung: str, elementar: str) -> dict[str, Any]:
    """Profil C in drei Fassungen: ohne Elementar, als Baustein, als Police.

    Plan 13.3, dritter Zusatzfall: dieselbe Sachlage als Baustein und als
    eigenstaendige Police muss dasselbe Ergebnis liefern.
    """
    kopf = _haushalt(kennung)
    _profil(
        kopf,
        housing_form="owner_occupied",
        living_area_sqm=140,
        elemental_zone="zurs2",
        has_oil_tank=True,
        construction_active=False,
        has_photovoltaic=False,
        contents_value_minor=9_500_000,
        liquid_reserve_minor=1_000_000,
        has_open_loans=False,
        children_count=0,
        pets=[],
        travel_frequency="never",
        vehicle_count=1,
        side_business=False,
        volunteering=False,
        has_drone=False,
        has_hunting_license=False,
        has_boat=False,
    )
    _person(kopf, C_PERSON, 52, employment_status="employed", health_coverage="gkv",
            has_dependents=False, net_income_minor=300_000)
    _fahrzeug(kopf, 3)
    gebaeude = _police(kopf, "building", sum_insured_minor=48_000_000)
    _police(kopf, "household_contents", sum_insured_minor=9_500_000)
    _police(kopf, "motor_liability")
    _police(kopf, "motor_comprehensive")
    if elementar == "baustein":
        _bausteine(kopf, str(gebaeude["id"]), "elemental")
    elif elementar == "police":
        _police(kopf, "elemental", sum_insured_minor=48_000_000)
    return {"kopf": kopf, "ergebnis": _check(kopf)}


@pytest.fixture(scope="module")
def profil_c() -> dict[str, Any]:
    return _baue_profil_c("p6-c", elementar="keiner")


# ---------------------------------------------------------------------------
# Referenzprofil D - Autofahrer, Fahrzeug 12 Jahre, zweite Police ruhend
# ---------------------------------------------------------------------------

D_PERSON = "Dana Fahrer"

ERWARTUNG_D: dict[tuple[str, str | None], str] = {
    # --- Plan 13.3, Profil D
    ("R8", None): "fulfilled",  # fuer das angemeldete Fahrzeug
    ("R8a", None): "gap",  # die ruhende Police zaehlt nicht als Pflichterfuellung
    ("R9a", None): "overinsured",  # Vollkasko bei 12 Jahren, S4
    # --- Abgeleitete Zellen
    # Der Bestand aus 13.3 kennt nur Kfz-Policen; alles ausserhalb der
    # Mobilitaet ist damit unversorgt oder mangels Bedingung gegenstandslos.
    ("R1", D_PERSON): "gap",
    ("R1a", D_PERSON): "not_applicable",
    ("R2", None): "gap",
    ("R2a", None): "not_applicable",
    ("R3", None): "not_applicable",
    ("R3b", D_PERSON): "not_applicable",
    ("R4", D_PERSON): "gap",
    ("R4a", D_PERSON): "not_applicable",
    ("R7", None): "not_applicable",
    ("R7b", D_PERSON): "not_applicable",
    ("R10", None): "not_applicable",
    ("R11", None): "not_applicable",
    ("R11a", None): "not_applicable",
    ("R12", None): "not_applicable",
    ("R13", None): "not_applicable",
    ("R14", None): "not_applicable",
    ("R15", None): "gap",
    ("R15a", None): "not_applicable",
    ("R16", None): "not_applicable",
    ("R17", None): "gap",
    ("R18", None): "not_applicable",
    ("R20", None): "not_applicable",
    ("R21", None): "not_applicable",
    ("R22", D_PERSON): "not_applicable",
    ("R23", D_PERSON): "gap",
    ("R24", None): "not_applicable",
}


@pytest.fixture(scope="module")
def profil_d() -> dict[str, Any]:
    kopf = _haushalt("p6-d")
    _profil(
        kopf,
        housing_form="rent",
        living_area_sqm=60,
        elemental_zone="zurs1",
        has_oil_tank=False,
        construction_active=False,
        has_photovoltaic=False,
        contents_value_minor=2_000_000,
        liquid_reserve_minor=800_000,
        has_open_loans=False,
        children_count=0,
        pets=[],
        travel_frequency="never",
        vehicle_count=2,
        side_business=False,
        volunteering=False,
        has_drone=False,
        has_hunting_license=False,
        has_boat=False,
    )
    _person(kopf, D_PERSON, 45, employment_status="employed", health_coverage="gkv",
            has_dependents=False, net_income_minor=270_000)
    _fahrzeug(kopf, 12, label="Zwoelf Jahre alter Kombi")
    _police(kopf, "motor_liability", label="Angemeldetes Fahrzeug")
    _police(kopf, "motor_comprehensive", label="Vollkasko")
    _police(kopf, "motor_liability", label="Abgemeldetes Zweitfahrzeug", status="dormant")
    return {"kopf": kopf, "ergebnis": _check(kopf)}


# ---------------------------------------------------------------------------
# Die beiden gefundenen Abweichungen des Auswerters - behoben
# ---------------------------------------------------------------------------
# Beide entstanden aus derselben Reihenfolge in
# `insurance_needs_service._evaluate_one`: `required_facts` wurde VOR der
# Bedingung und VOR `depends_on_line_key` geprueft, eine Regel, die gar nicht
# greift, verlangte dadurch trotzdem ihre Pflichtangabe. Seit der Korrektur
# stehen die Zellen scharf in den vier Profiltabellen; die Reihenfolge selbst
# haelt `test_pflichtangaben_werden_erst_nach_der_bedingung_verlangt` fest.

ABWEICHENDE_ZELLEN: dict[str, frozenset[tuple[str, str | None]]] = {
    "A": frozenset({("R22", A_PERSON), ("R4a", A_PERSON)}),
    "B": frozenset({("R22", name) for name in B_ELTERN + B_KINDER}
                   | {("R4a", B_KINDER[0]), ("R4a", B_KINDER[1])}),
    "C": frozenset({("R22", C_PERSON)}),
    "D": frozenset({("R22", D_PERSON)}),
}


# ---------------------------------------------------------------------------
# 1. Die vier Referenzprofile, vollstaendig
# ---------------------------------------------------------------------------


def test_profil_a_ergibt_genau_die_tabelle_aus_133(profil_a: dict[str, Any]) -> None:
    """Single, Miete, angestellt - Zeile fuer Zeile, auch die not_applicable."""
    tabelle = _tabelle(profil_a["ergebnis"])
    assert set(tabelle) == set(ERWARTUNG_A), "Andere Regelmenge als erwartet"
    assert tabelle == ERWARTUNG_A


def test_profil_b_ergibt_genau_die_tabelle_aus_133(profil_b: dict[str, Any]) -> None:
    """Paar mit zwei Kindern - personenbezogene Regeln je Person einmal."""
    tabelle = _tabelle(profil_b["ergebnis"])
    assert set(tabelle) == set(ERWARTUNG_B), "Andere Regelmenge als erwartet"
    assert tabelle == ERWARTUNG_B


def test_profil_c_ergibt_genau_die_tabelle_aus_133(profil_c: dict[str, Any]) -> None:
    """Immobilienbesitzer mit Oeltank - der Elementarbaustein fehlt."""
    tabelle = _tabelle(profil_c["ergebnis"])
    assert set(tabelle) == set(ERWARTUNG_C), "Andere Regelmenge als erwartet"
    assert tabelle == ERWARTUNG_C


def test_profil_d_ergibt_genau_die_tabelle_aus_133(profil_d: dict[str, Any]) -> None:
    """Autofahrer mit ruhender Zweitpolice und Vollkasko am alten Fahrzeug."""
    tabelle = _tabelle(profil_d["ergebnis"])
    assert set(tabelle) == set(ERWARTUNG_D), "Andere Regelmenge als erwartet"
    assert tabelle == ERWARTUNG_D


def test_pflichtangaben_werden_erst_nach_der_bedingung_verlangt(
    profil_a: dict[str, Any],
    profil_b: dict[str, Any],
    profil_c: dict[str, Any],
    profil_d: dict[str, Any],
) -> None:
    """Die Zellen, die vor der Korrektur der Reihenfolge falsch standen."""
    for profil, erwartung, gebaut in (
        ("A", ERWARTUNG_A, profil_a),
        ("B", ERWARTUNG_B, profil_b),
        ("C", ERWARTUNG_C, profil_c),
        ("D", ERWARTUNG_D, profil_d),
    ):
        tabelle = _tabelle(gebaut["ergebnis"])
        for zelle in ABWEICHENDE_ZELLEN[profil]:
            assert tabelle[zelle] == erwartung[zelle], f"Profil {profil}, Zelle {zelle}"

    # Der eigentliche Schaden steht nicht in der Tabelle, sondern in der
    # Onboarding-Fuehrung: sie verlangt vom gesetzlich versicherten Haushalt
    # einen Beihilfebemessungssatz, den es dort nicht gibt.
    antwort = client.get(f"{BASIS}/needs/completeness", headers=profil_a["kopf"])
    blockierende_fragen = {eintrag["field"] for eintrag in antwort.json()["missing"]}
    assert "beihilfe_rate_bp" not in blockierende_fragen


def test_profil_b_nennt_bei_der_unterversicherung_beide_zahlen(
    profil_b: dict[str, Any],
) -> None:
    """40.000 gegen 85 x 650 = 55.250 - und die Rechnung steht im Befund.

    Ohne `target_value_minor` und `actual_value_minor` stuende dort "Summe zu
    niedrig", ohne dass nachvollziehbar waere, wogegen gemessen wurde.
    """
    befund = _befund(profil_b["ergebnis"], "R15a")
    assert befund["outcome"] == "underinsured"
    assert befund["target_metric"] == "sum_insured_formula"
    assert befund["target_formula"] == "contents_value_per_sqm"
    assert befund["target_value_minor"] == 85 * 65_000 == 5_525_000
    assert befund["actual_value_minor"] == 4_000_000
    assert befund["gap_value_minor"] == 1_525_000
    assert befund["triggered_by"]["living_area_sqm"] == 85
    # Die Regel schlaegt die Katalogvorgabe: Hausrat ist allgemein S2, hier S1.
    assert befund["priority"] == "S1"
    assert befund["need_tier"] == "S2"
    assert befund["priority_source"] == "rule"


def test_profil_b_nennt_die_offene_darlehenssumme_als_vergleichswert(
    profil_b: dict[str, Any],
) -> None:
    """R7: Zielwert ist `open_debt` aus dem Vermoegensmodul - 180.000 Euro."""
    befund = _befund(profil_b["ergebnis"], "R7")
    assert befund["outcome"] == "gap"
    assert befund["target_formula"] == "open_debt"
    assert befund["triggered_by"]["open_debt_minor"] == 18_000_000
    assert befund["triggered_by"]["children_count"] == 2


def test_profil_d_nennt_die_ruhende_police_beim_namen(profil_d: dict[str, Any]) -> None:
    """R8a ist ein Ausschluss und kein gewoehnlicher Befund.

    Er muss die Police benennen, die nicht zaehlt - sonst steht in der Liste
    eine Luecke neben einer Police, die es sichtbar gibt.
    """
    befund = _befund(profil_d["ergebnis"], "R8a")
    assert befund["outcome"] == "gap"
    assert len(befund["matched_policy_ids"]) == 1
    assert "dormant" in befund["outcome_reason"]

    vollkasko = _befund(profil_d["ergebnis"], "R9a")
    assert vollkasko["outcome"] == "overinsured"
    assert vollkasko["priority"] == "S4"
    assert vollkasko["triggered_by"]["vehicle_max_age_years"] == 12
    # S4 heisst neutral: keine Kuendigung, kein Wechsel, kein Anbieter.
    text = f"{vollkasko['headline']} {vollkasko['rationale']} {vollkasko['outcome_reason']}"
    assert "uendig" not in text and "echsel" not in text


# ---------------------------------------------------------------------------
# 2. Die vier Zusatzfaelle aus Plan 13.3
# ---------------------------------------------------------------------------


def test_baustein_und_einzelpolice_ergeben_dasselbe_ergebnis() -> None:
    """Plan 3.9 Punkt 4: derselbe Sachverhalt, zweimal erfasst, ein Ergebnis.

    Einmal Elementar als Baustein an der Wohngebaeudepolice, einmal als eigene
    Police. Beide Male `R11 = fulfilled` - und beide Male aendert sich sonst
    nichts an der Tabelle.
    """
    als_baustein = _baue_profil_c("p6-c-bau", elementar="baustein")
    als_police = _baue_profil_c("p6-c-pol", elementar="police")

    assert _befund(als_baustein["ergebnis"], "R11")["outcome"] == "fulfilled"
    assert _befund(als_police["ergebnis"], "R11")["outcome"] == "fulfilled"

    erwartet = {**ERWARTUNG_C, ("R11", None): "fulfilled"}
    for gebaut in (als_baustein, als_police):
        tabelle = _tabelle(gebaut["ergebnis"])
        assert tabelle == erwartet


def test_eine_police_ohne_bestaetigte_sparte_macht_aus_luecke_ein_vielleicht() -> None:
    """Erster Zusatzfall: `unclear` statt `gap`, wenn `line_key = 'unknown'`.

    Der Haushalt entsteht eigens und nicht aus einer Profilfixture: eine Police
    ohne Sparte verschoebe dort die halbe Ergebnistabelle.
    """
    kopf = _haushalt("p6-unbekannt")
    _profil(kopf, housing_form="rent", travel_frequency="occasional", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)
    vorher = _check(kopf)
    assert _befund(vorher, "R17")["outcome"] == "gap"

    _police(kopf, "unknown", insurer_name="Noch nicht zugeordnet")
    nachher = _check(kopf)
    rechtsschutz = _befund(nachher, "R17")
    assert rechtsschutz["outcome"] == "unclear"
    assert "ohne bestaetigte Sparte" in rechtsschutz["outcome_reason"]
    # Der Befund zeigt auf die Police, die es sein koennte - sonst waere die
    # Rueckfrage nicht beantwortbar.
    assert len(rechtsschutz["matched_policy_ids"]) == 1


def test_ein_leerer_haushalt_zeigt_nichts_erfasst_und_keine_dreissig_luecken() -> None:
    """Vierter Zusatzfall und Plan 10.2: ein vollstaendig rotes Bild bei leerer
    Erfassung ist genauso falsch wie ein gruenes."""
    kopf = _haushalt("p6-leer")
    _profil(kopf, housing_form="rent", travel_frequency="occasional", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False, has_drone=False,
            has_hunting_license=False, has_boat=False, construction_active=False)
    _person(kopf, "Erna Leer", 30, employment_status="employed", health_coverage="gkv")
    ergebnis = _check(kopf)

    assert ergebnis["stock_empty"] is True
    luecken = [f for f in ergebnis["findings"] if f["outcome"] in LUECKENZUSTAENDE]
    assert luecken == [], f"Leerer Bestand meldet Luecken: {[f['rule_key'] for f in luecken]}"
    # Die Regeln, die sonst greifen wuerden, sagen jetzt ausdruecklich warum.
    rechtsschutz = _befund(ergebnis, "R17")
    assert rechtsschutz["outcome"] == "unclear"
    assert "keine einzige Police erfasst" in rechtsschutz["outcome_reason"]


def test_eine_wegdrueckung_ohne_policenbezug_ueberlebt_den_neulauf() -> None:
    """Zweiter Zusatzfall und Pflichtfall 3.

    Der Normalfall traegt KEINE `policy_id` - die meisten Regeln zeigen auf
    eine fehlende Police. Genau dieser Fall muss speicherbar sein.
    """
    kopf = _haushalt("p6-entscheidung")
    _profil(kopf, housing_form="rent", travel_frequency="occasional", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)
    assert _befund(_check(kopf), "R17")["outcome"] == "gap"

    weg = client.post(
        f"{BASIS}/needs/R17/dismiss",
        json={"decision": "dismissed", "reason": "not_applicable",
              "reason_note": "Trifft auf diesen Haushalt nicht zu."},
        headers=kopf,
    )
    assert weg.status_code == 204, weg.text

    # Dauerhaft: zwei Laeufe hintereinander, beide Male weg.
    for lauf in (1, 2):
        befund = _befund(_check(kopf), "R17")
        assert befund["outcome"] == "dismissed", f"Lauf {lauf}"
        assert befund["decision"]["reason"] == "not_applicable"
        assert befund["decision"]["policy_id"] is None
        assert befund["decision"]["expired"] is False

    # Und sie zaehlt nicht als Luecke.
    ergebnis = _check(kopf)
    assert "R17" not in {f["rule_key"] for f in ergebnis["findings"]
                         if f["outcome"] in LUECKENZUSTAENDE}

    zurueck = client.delete(f"{BASIS}/needs/R17/dismiss", headers=kopf)
    assert zurueck.status_code == 204, zurueck.text
    assert _befund(_check(kopf), "R17")["outcome"] == "gap"


def test_die_wiedervorlage_bringt_den_befund_am_gesetzten_tag_zurueck() -> None:
    """`remind_on` ist die einzige Ausnahme von "der Nutzer schlaegt die Regel"."""
    kopf = _haushalt("p6-wiedervorlage")
    _profil(kopf, housing_form="rent", travel_frequency="occasional", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)

    morgen = (HEUTE + timedelta(days=1)).isoformat()
    gestellt = client.post(
        f"{BASIS}/needs/R17/dismiss",
        json={"decision": "snoozed", "remind_on": morgen, "reason": "planned"},
        headers=kopf,
    )
    assert gestellt.status_code == 204, gestellt.text
    zurueckgestellt = _befund(_check(kopf), "R17")
    assert zurueckgestellt["outcome"] == "dismissed"
    assert zurueckgestellt["decision"]["expired"] is False

    faellig = client.post(
        f"{BASIS}/needs/R17/dismiss",
        json={"decision": "snoozed", "remind_on": HEUTE.isoformat(), "reason": "planned"},
        headers=kopf,
    )
    assert faellig.status_code == 204, faellig.text
    wieder_da = _befund(_check(kopf), "R17")
    assert wieder_da["outcome"] == "gap"
    # Die abgelaufene Entscheidung bleibt in der Antwort, damit die Oberflaeche
    # erklaeren kann, warum der Befund wieder da ist.
    assert wieder_da["decision"]["expired"] is True
    assert wieder_da["decision"]["remind_on"] == HEUTE.isoformat()


def test_eine_zurueckstellung_ohne_datum_wird_abgewiesen() -> None:
    kopf = _haushalt("p6-ohne-datum")
    antwort = client.post(
        f"{BASIS}/needs/R17/dismiss", json={"decision": "snoozed"}, headers=kopf
    )
    assert antwort.status_code == 422, antwort.text


def test_eine_entscheidung_zu_einer_unbekannten_regel_wird_abgewiesen() -> None:
    """Ein Tippfehler im Regelschluessel darf keine Zeile anlegen, die nie wirkt."""
    kopf = _haushalt("p6-unbekannte-regel")
    antwort = client.post(
        f"{BASIS}/needs/R99/dismiss", json={"decision": "dismissed"}, headers=kopf
    )
    assert antwort.status_code == 404, antwort.text
    assert antwort.json()["detail"] == needs_service.INSURANCE_RULE_UNKNOWN


# ---------------------------------------------------------------------------
# 3. Pflichtfall 1 und 2: fehlende Angabe ist keine Luecke
# ---------------------------------------------------------------------------

# Die Regeln, deren Bedingung genau eine unbeantwortete Haushaltsfrage liest.
# Ohne Antwort darauf darf keine von ihnen eine Luecke melden.
FRAGENGEBUNDENE_REGELN: tuple[tuple[str, str], ...] = (
    ("R8", "vehicle_count"),
    ("R10", "housing_form"),
    ("R11", "housing_form"),
    ("R11a", "construction_active"),
    ("R12", "housing_form"),
    ("R13", "has_oil_tank"),
    ("R14", "pets"),
    ("R16", "travel_frequency"),
    ("R18", "has_drone"),
    ("R20", "has_hunting_license"),
    ("R21", "has_boat"),
)


def test_eine_unbeantwortete_frage_ergibt_angabe_fehlt_und_niemals_eine_luecke() -> None:
    """Pflichtfall 1 - der Leitsatz dieser Phase.

    Der Bestand ist ausdruecklich NICHT leer: sonst schluege die Abschwaechung
    aus 10.2 zu und der Test bewiese nur, dass ein leerer Haushalt schweigt.
    """
    kopf = _haushalt("p6-unbeantwortet")
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)
    ergebnis = _check(kopf)

    for rule_key, frage in FRAGENGEBUNDENE_REGELN:
        befund = _befund(ergebnis, rule_key)
        assert befund["outcome"] == "unclear", f"{rule_key} bei unbeantworteter Frage {frage}"
        assert frage in befund["missing_facts"], f"{rule_key} nennt {frage} nicht"
        # Die Begruendung der Regel wird ERSETZT: sie behauptet sonst eine
        # Luecke, die gerade nicht geprueft werden konnte.
        assert "fehlt" in befund["rationale"]
        assert befund["outcome"] not in LUECKENZUSTAENDE


def test_eine_fehlende_angabe_am_bestand_behaelt_die_begruendung_der_regel() -> None:
    """Der Unterschied zwischen zwei Arten von `unclear`.

    Fehlt eine Angabe in der BEDINGUNG, wurde die Regel nicht geprueft - ihre
    Begruendung behauptete dann eine Luecke, die niemand geprueft hat, und wird
    ersetzt. Fehlt dagegen eine Angabe am BESTAND ("Deckungssumme nicht
    erfasst"), bleibt richtig und nachlesbar, wogegen geprueft werden sollte.
    """
    kopf = _haushalt("p6-summe-fehlt")
    _profil(kopf, housing_form="rent", travel_frequency="never", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    _police(kopf, "private_liability")  # ohne sum_insured_minor

    befund = _befund(_check(kopf), "R2a")
    assert befund["outcome"] == "unclear"
    assert befund["rationale"] == insurance_rules.RULES_BY_KEY["R2a"].rationale
    assert "Deckungssumme" in befund["outcome_reason"]
    assert befund["missing_facts"] == []


def test_die_deckungssumme_wird_gegen_die_belegte_schwelle_geprueft() -> None:
    """R2a traegt zwei Zahlen: die belegten 10 Millionen und einen Warnwert.

    Nur die belegte Zahl darf im Begruendungstext stehen - fuer den Warnwert
    liegt keine Fremdaussage vor, und eine Zahl ohne Fundstelle waere in einem
    Befund eine Meinung.
    """
    kopf = _haushalt("p6-deckungssumme")
    _profil(kopf, housing_form="rent", travel_frequency="never", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    _police(kopf, "private_liability", sum_insured_minor=300_000_000)  # 3 Mio Euro

    befund = _befund(_check(kopf), "R2a")
    assert befund["outcome"] == "underinsured"
    assert befund["target_value_minor"] == 1_000_000_000
    assert befund["actual_value_minor"] == 300_000_000
    assert befund["gap_value_minor"] == 700_000_000
    assert "Warnwert" in befund["outcome_reason"]
    regel = insurance_rules.RULES_BY_KEY["R2a"]
    assert regel.warn_value == 500_000_000
    assert "500" not in befund["rationale"] and "5 Millionen" not in befund["rationale"]
    assert "10 Millionen" in befund["rationale"]


def test_eine_quotenregel_fuellt_kein_geldfeld() -> None:
    """R22 rechnet in Basispunkten. Ein Basispunktwert in `target_value_minor`
    waere in jeder Oberflaeche ein Eurobetrag."""
    kopf = _haushalt("p6-beihilfe")
    _profil(kopf, housing_form="rent", travel_frequency="never", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    person_id = _person(kopf, "Konrad Beamter", 45, employment_status="civil_servant",
                        health_coverage="pkv_beihilfe", beihilfe_rate_bp=7000,
                        net_income_minor=400_000)
    assert person_id
    _police(kopf, "health_private", attrs={"benefit_rate_bp": 2000})

    befund = _befund(_check(kopf), "R22", "Konrad Beamter")
    assert befund["outcome"] == "underinsured"
    assert befund["target_metric"] == "benefit_rate_formula"
    assert befund["target_formula"] == "beihilfe_complement"
    assert befund["target_value_minor"] is None
    assert befund["actual_value_minor"] is None
    assert befund["gap_value_minor"] is None
    # 10000 minus 7000 Basispunkte - die Zahl steht im Begruendungssatz des
    # Ergebnisses, nicht in einem Geldfeld.
    assert "3000" in befund["outcome_reason"] and "2000" in befund["outcome_reason"]


def test_angabe_fehlt_wird_nirgends_als_luecke_gezaehlt(profil_a: dict[str, Any]) -> None:
    """Pflichtfall 2: `counts` zaehlt je Zustand und fasst nichts zusammen."""
    ergebnis = profil_a["ergebnis"]
    counts = ergebnis["counts"]

    unbekannt = set(counts) - ERGEBNISZUSTAENDE
    assert unbekannt == set(), f"Unbekannter Zaehler: {sorted(unbekannt)}"
    for zustand in ERGEBNISZUSTAENDE:
        erwartet = len([f for f in ergebnis["findings"] if f["outcome"] == zustand])
        assert counts.get(zustand, 0) == erwartet, zustand
    assert sum(counts.values()) == len(ergebnis["findings"])

    # Und der einzelne Nachweis: R15 ist `unclear` und steht in keinem
    # Lueckenzaehler.
    assert _befund(ergebnis, "R15")["outcome"] == "unclear"
    assert counts.get("unclear", 0) >= 1


def test_die_vollstaendigkeit_fuehrt_nur_fehlende_angaben_und_bewertet_nichts(
    profil_a: dict[str, Any],
) -> None:
    """`GET /needs/completeness` ist die Fuehrung durch den Fragebogen.

    "Sieben von zwoelf Angaben" ist eine Tatsache; "70 Prozent abgesichert"
    waere die Scheingenauigkeit, gegen die dieses Modul gebaut ist. Der Test
    prueft deshalb, dass `total` die Zahl der FRAGEN ist und nicht die Zahl der
    Regeln oder Risikofelder.
    """
    antwort = client.get(f"{BASIS}/needs/completeness", headers=profil_a["kopf"])
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()

    fragen = len(needs_service.HOUSEHOLD_QUESTIONS) + len(needs_service.PERSON_QUESTIONS)
    assert daten["total"] == fragen
    # Unbeantwortet sind genau zwei: die Ruecklage im Haushaltsbogen und das
    # Nettoeinkommen der Person.
    assert daten["answered"] == fragen - 2
    assert 0 < daten["blocked_rules"] <= len(insurance_rules.INSURANCE_RULES)

    fehlend = {(eintrag["field"], eintrag["scope"]) for eintrag in daten["missing"]}
    assert ("liquid_reserve_minor", "household") in fehlend
    for eintrag in daten["missing"]:
        assert eintrag["blocks_rule_keys"], f"{eintrag['field']} blockiert nichts"
        assert eintrag["label"], "Ein roher Spaltenname ist keine Frage"
    # Beantwortete Fragen stehen in keiner Liste - eine vollstaendige
    # Fragenliste zweimal zu fuehren hiesse, sie zweimal verschieden zu fuehren.
    assert "housing_form" not in {eintrag["field"] for eintrag in daten["missing"]}


# ---------------------------------------------------------------------------
# 4. Pflichtfall 4: ein Befund, Feld fuer Feld
# ---------------------------------------------------------------------------


def test_ein_befund_nennt_regel_quelle_pruefdatum_und_die_ausloesenden_angaben(
    profil_c: dict[str, Any],
) -> None:
    """R13 in Profil C: Oeltank ohne Gewaesserschadenhaftpflichtversicherung.

    Feld fuer Feld gegen die Datenzeile in `app/data/insurance_rules.py` - nicht
    gegen abgeschriebene Zeichenketten: ein Test, der die Texte kopiert, prueft
    nur, dass jemand zweimal dasselbe getippt hat.
    """
    regel = insurance_rules.RULES_BY_KEY["R13"]
    befund = _befund(profil_c["ergebnis"], "R13")

    assert befund["rule_key"] == "R13"
    assert befund["outcome"] == "gap"
    assert befund["risk_field"] == regel.risk_field == "liability"
    assert befund["line_key"] == regel.line_key == "water_damage_liability"
    assert befund["line_label"]  # Klartext der Sparte aus dem Katalog
    assert befund["priority"] == regel.priority == "S1"
    assert befund["headline"] == regel.headline
    assert befund["rationale"] == regel.rationale
    assert befund["source_url"] == regel.source_url
    assert regel.source_checked_on is not None
    assert befund["source_checked_on"] == regel.source_checked_on.isoformat()
    assert befund["target_metric"] == "policy_exists"
    # Die Angaben, auf denen der Befund beruht - im Klartext und nicht als
    # Rechnung.
    assert befund["triggered_by"] == {"has_oil_tank": True}
    assert befund["missing_facts"] == []
    assert befund["outcome_reason"]
    assert befund["decision"] is None

    # Und die Fundstelle steht im Begruendungstext selbst, nicht nur als Feld.
    assert "89" in befund["rationale"] and "WHG" in befund["rationale"]


def test_jeder_wirksame_befund_traegt_fundstelle_und_pruefdatum(
    profil_a: dict[str, Any],
    profil_b: dict[str, Any],
    profil_c: dict[str, Any],
    profil_d: dict[str, Any],
) -> None:
    """Kein Befund ohne Herkunft - ueber alle vier Profile.

    Ausgenommen ist nur `not_applicable`: eine Regel, die nicht greift, trifft
    keine Aussage, fuer die es eine Fundstelle geben muesste. R7b traegt als
    einzige Zeile ueberhaupt keine Quelle und wird deshalb nie wirksam.
    """
    for profil in (profil_a, profil_b, profil_c, profil_d):
        for befund in profil["ergebnis"]["findings"]:
            if befund["outcome"] == "not_applicable":
                continue
            assert befund["source_url"], befund["rule_key"]
            assert befund["source_checked_on"], befund["rule_key"]
            assert befund["headline"] and befund["rationale"], befund["rule_key"]
            assert befund["rule_key"] in insurance_rules.RULES_BY_KEY


def test_kein_befund_nennt_einen_anbieter_einen_tarif_oder_einen_wechsel(
    profil_a: dict[str, Any],
    profil_b: dict[str, Any],
    profil_c: dict[str, Any],
    profil_d: dict[str, Any],
) -> None:
    """Leitsatz 3: keine Beratung, kein Vertrieb.

    Die Namen stammen aus dem Bestand der Profile - taucht einer davon in einem
    Befundtext auf, empfiehlt das Werkzeug einen Anbieter.
    """
    verboten = re.compile(
        r"\b(guenstiger|billiger|empfehlen|empfohlen|Empfehlung|Tarifvergleich|"
        r"Nordwacht|Ortskrankenkasse|Angebot|Abschluss jetzt)\b",
        re.IGNORECASE,
    )
    for profil in (profil_a, profil_b, profil_c, profil_d):
        for befund in profil["ergebnis"]["findings"]:
            text = " ".join(
                str(befund[feld])
                for feld in ("headline", "rationale", "outcome_reason")
            )
            treffer = verboten.findall(text)
            assert not treffer, f"{befund['rule_key']}: {treffer}"


# ---------------------------------------------------------------------------
# 5. Pflichtfall 5: nirgends ein Score, ein Prozentwert, eine Gesamtzahl
# ---------------------------------------------------------------------------

# Namen, die eine Bewertung tragen wuerden. Bewusst als Ganzwortliste und nicht
# als Teilzeichenkette: `beihilfe_rate_bp` enthaelt "rate" und ist trotzdem eine
# Angabe des Nutzers.
VERBOTENE_FELDNAMEN = frozenset(
    {
        "score",
        "scores",
        "protection_score",
        "schutzscore",
        "rating",
        "grade",
        "note",
        "noten",
        "stars",
        "sterne",
        "percent",
        "percentage",
        "prozent",
        "quote",
        "ampel",
        "bewertung",
        "gesamt",
        "gesamtwert",
        "gesamtstand",
        "progress",
        "fortschritt",
        "index",
        "level",
        "risk_score",
        "coverage_ratio",
    }
)


def _felder_und_werte(knoten: Any, pfad: str = "") -> list[tuple[str, Any]]:
    """Alle Schluessel-Wert-Paare eines JSON-Baumes, flach."""
    gefunden: list[tuple[str, Any]] = []
    if isinstance(knoten, dict):
        for schluessel, wert in knoten.items():
            unter = f"{pfad}.{schluessel}" if pfad else str(schluessel)
            gefunden.append((unter, wert))
            gefunden.extend(_felder_und_werte(wert, unter))
    elif isinstance(knoten, list):
        for index, wert in enumerate(knoten):
            gefunden.extend(_felder_und_werte(wert, f"{pfad}[{index}]"))
    return gefunden


@pytest.mark.parametrize(
    "pfad", ["/needs/check", "/needs/completeness", "/needs/profile", "/duplicates"]
)
def test_keine_antwort_des_abgleichs_traegt_einen_score(
    profil_c: dict[str, Any], pfad: str
) -> None:
    """Aktiv gesucht: Score, Note, Prozentwert, Gesamtbalken.

    Genau dafuer hat Stiftung Warentest alle vier geprueften Makler-Apps
    abgewertet (Plan 10.2). Geprueft wird auf drei Wegen zugleich: verbotene
    Feldnamen, Gleitkommazahlen (eine Note ist selten ganzzahlig) und das
    Prozentzeichen im gesamten Antworttext.
    """
    antwort = client.get(f"{BASIS}{pfad}", headers=profil_c["kopf"])
    assert antwort.status_code == 200, antwort.text

    for name, wert in _felder_und_werte(antwort.json()):
        blatt = name.rsplit(".", 1)[-1]
        assert blatt.lower() not in VERBOTENE_FELDNAMEN, f"{pfad}: Feld {name}"
        assert not isinstance(wert, float), f"{pfad}: Gleitkommawert in {name} = {wert}"
    assert "%" not in antwort.text, f"{pfad}: Prozentzeichen in der Antwort"


def test_der_abgleich_zaehlt_je_zustand_und_kennt_keine_gesamtnote(
    profil_c: dict[str, Any],
) -> None:
    """Die einzige Zahl ueber alle Regeln ist eine ANZAHL.

    `rules_evaluated` sagt, wie viele Regeln gelaufen sind; `counts` sagt, wie
    oft welcher Zustand herauskam. Keine der beiden bewertet.
    """
    ergebnis = profil_c["ergebnis"]
    assert ergebnis["rules_evaluated"] == len(insurance_rules.INSURANCE_RULES)
    assert set(ergebnis) == {
        "generated_on",
        "checked_on",
        "disclaimer",
        "counts",
        "stock_empty",
        "profile_last_checked_on",
        "rules_evaluated",
        "findings",
    }
    assert ergebnis["checked_on"] == HEUTE.isoformat()


def test_der_disclaimer_steht_im_koerper_und_nicht_nur_in_der_fusszeile(
    profil_a: dict[str, Any],
) -> None:
    """P6-Abnahme woertlich."""
    disclaimer = profil_a["ergebnis"]["disclaimer"]
    assert disclaimer == needs_service.DISCLAIMER
    assert "keine Beratung" in disclaimer
    assert "Regelkatalog" in disclaimer


# ---------------------------------------------------------------------------
# 6. Pflichtfall 6: Doppelversicherung
# ---------------------------------------------------------------------------


def test_zwei_policen_mit_ueberlappendem_baustein_ergeben_einen_befund_der_beide_nennt() -> None:
    """Glasbruch als eigene Police neben einer Hausratpolice, die ihn fuehrt.

    Erkannt wird das ueber `insurance_lines.covered_by_keys` - eine
    Katalogaussage und keine Sonderlogik im Dienst.
    """
    kopf = _haushalt("p6-doppelt")
    hausrat = _police(kopf, "household_contents", label="Hausrat", sum_insured_minor=5_000_000)
    glas = _police(kopf, "glass", label="Glasbruch einzeln")

    # Ohne erfasste Bausteine sagt der Befund ehrlich "ich weiss es nicht".
    ohne_bausteine = client.get(f"{BASIS}/duplicates", headers=kopf)
    assert ohne_bausteine.status_code == 200, ohne_bausteine.text
    unsicher = ohne_bausteine.json()
    assert len(unsicher) == 1
    assert unsicher[0]["kind"] == "contained_in"
    assert unsicher[0]["certainty"] == "unclear"
    assert unsicher[0]["hint"]
    assert unsicher[0]["missing_coverage_policy_ids"] == [hausrat["id"]]

    _bausteine(kopf, str(hausrat["id"]), "glass", "elemental")
    befunde = client.get(f"{BASIS}/duplicates", headers=kopf).json()
    assert len(befunde) == 1
    befund = befunde[0]
    assert befund["kind"] == "contained_in"
    assert befund["certainty"] == "established"
    # BEIDE Policen stehen im Befund - ein Befund, der nur eine nennt, ist nicht
    # nachpruefbar.
    assert {befund["policy_id"], befund["other_policy_id"]} == {glas["id"], hausrat["id"]}
    assert befund["coverage_key"] == "glass"
    # Die Rechtsfolge, kein Rat.
    assert "78" in befund["rationale"] and "79" in befund["rationale"]
    assert "uendig" not in befund["rationale"].lower().replace("kuendbar", "")


def test_zwei_policen_derselben_sparte_ergeben_same_line_und_eine_einzelne_keinen() -> None:
    """Der zweite Fall aus 10.4 - und die Gegenprobe."""
    kopf = _haushalt("p6-gleiche-sparte")
    erste = _police(kopf, "household_contents", label="Hausrat alt", insurer_name="Erste AG")
    assert client.get(f"{BASIS}/duplicates", headers=kopf).json() == []

    zweite = _police(kopf, "household_contents", label="Hausrat neu", insurer_name="Zweite AG")
    befunde = client.get(f"{BASIS}/duplicates", headers=kopf).json()
    assert len(befunde) == 1
    assert befunde[0]["kind"] == "same_line"
    assert {befunde[0]["policy_id"], befunde[0]["other_policy_id"]} == {erste["id"], zweite["id"]}
    assert befunde[0]["line_key"] == "household_contents"


def test_der_gesellschaftswechsel_ist_keine_doppelversicherung() -> None:
    """Erste Ausnahme aus 10.4: waehrend der Ueberlappung laufen zwei Policen -
    das ist der Wechsel selbst und kein Befund."""
    kopf = _haushalt("p6-wechsel")
    alt = _police(kopf, "household_contents", label="Hausrat alt", insurer_name="Erste AG")
    ersetzt = client.post(
        f"{BASIS}/policies/{alt['id']}/replace-with",
        json={
            "ends_on": (HEUTE + timedelta(days=30)).isoformat(),
            "new_policy": {
                "line_key": "household_contents",
                "insurer_name": "Zweite AG",
                "label": "Hausrat neu",
                "starts_on": HEUTE.isoformat(),
            },
        },
        headers=kopf,
    )
    assert ersetzt.status_code == 200, ersetzt.text
    # Die Vorgaengerin laeuft noch dreissig Tage - trotzdem kein Befund.
    assert ersetzt.json()["predecessor"]["ends_on"] == (HEUTE + timedelta(days=30)).isoformat()
    assert client.get(f"{BASIS}/duplicates", headers=kopf).json() == []


def test_zwei_personen_mit_je_eigener_police_sind_keine_doppelversicherung() -> None:
    """Dritte Ausnahme aus 10.4 - und der Fall, der ohne sie im Haushalt eines
    berufstaetigen Paares sofort falsch meldete.

    Die Gegenprobe steht bewusst daneben: ohne Personenzuordnung sind dieselben
    zwei Policen sehr wohl ein Befund. Nur so ist belegt, dass die Ausnahme
    greift und nicht die Erkennung insgesamt schweigt.
    """
    kopf = _haushalt("p6-zwei-personen")
    eine = _police(kopf, "occupational_disability", label="BU eine", insurer_name="Erste AG")
    andere = _police(kopf, "occupational_disability", label="BU andere", insurer_name="Zweite AG")

    ohne_zuordnung = client.get(f"{BASIS}/duplicates", headers=kopf).json()
    assert len(ohne_zuordnung) == 1
    assert ohne_zuordnung[0]["kind"] == "same_line"

    for policy, name in ((eine, "Ines Erste"), (andere, "Jens Zweite")):
        zuordnung = client.post(
            f"{BASIS}/policies/{policy['id']}/persons",
            json={"new_person": {"display_name": name}, "role": "insured"},
            headers=kopf,
        )
        assert zuordnung.status_code == 201, zuordnung.text

    assert client.get(f"{BASIS}/duplicates", headers=kopf).json() == []


# ---------------------------------------------------------------------------
# 7. Pflichtfall 7: das Personenprofil ist gesundheitsgeschuetzt
# ---------------------------------------------------------------------------


def test_ein_haushaltsmitglied_ohne_freigabe_liest_und_schreibt_kein_personenprofil() -> None:
    """Plan 4.9 und 13.4: der sensibelste Datensatz des Moduls.

    Zwei Editoren DESSELBEN Haushalts - der Unterschied zwischen ihnen ist
    ausschliesslich die Gesundheitsfreigabe. Die Haushaltsgrenze beweist hier
    nichts, sie ist seit P1 dicht.
    """
    token_besitzer = _signup(
        f"p6-owner-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-62"
    )
    token_a = _signup(f"p6-mit-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-63")
    token_b = _signup(f"p6-ohne-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-64")
    haushalt = client.post(
        "/v1/households",
        json={"name": "P6-Gesundheitsschutz"},
        headers=_auth_headers(token_besitzer),
    )
    assert haushalt.status_code == 201, haushalt.text
    haushalt_id = str(haushalt.json()["id"])
    besitzer = {**_auth_headers(token_besitzer), "X-Household-Id": haushalt_id}

    koepfe = {}
    for name, token in (("a", token_a), ("b", token_b)):
        einladung = client.post("/v1/households/invites", json={"role": "editor"}, headers=besitzer)
        assert einladung.status_code == 201, einladung.text
        beigetreten = client.post(
            "/v1/households/invites/redeem",
            json={"code": einladung.json()["code"]},
            headers=_auth_headers(token),
        )
        assert beigetreten.status_code == 200, beigetreten.text
        koepfe[name] = {**_auth_headers(token), "X-Household-Id": haushalt_id}

    # A legt die Person an und bekommt dabei die Verwaltungsfreigabe.
    person_id = _person(koepfe["a"], "Mara Vertraulich", 44,
                        employment_status="civil_servant", health_coverage="pkv_beihilfe",
                        beihilfe_rate_bp=7000, net_income_minor=420_000)

    fuer_a = client.get(f"{BASIS}/needs/profile/persons/{person_id}", headers=koepfe["a"])
    assert fuer_a.status_code == 200, fuer_a.text
    assert fuer_a.json()["beihilfe_rate_bp"] == 7000
    assert fuer_a.json()["net_income_minor"] == 420_000
    assert fuer_a.json()["can_manage"] is True

    # B ist Editor desselben Haushalts - und sieht nichts davon. 404 statt 403:
    # die Existenz einer fremden Person wird nicht verraten.
    fuer_b = client.get(f"{BASIS}/needs/profile/persons/{person_id}", headers=koepfe["b"])
    assert fuer_b.status_code == 404, fuer_b.text

    schreibt_b = client.put(
        f"{BASIS}/needs/profile/persons/{person_id}",
        json={"net_income_minor": 1},
        headers=koepfe["b"],
    )
    assert schreibt_b.status_code == 404, schreibt_b.text

    # Auch die Sammelantwort verraet weder Name noch Einkommen.
    profil_b_sicht = client.get(f"{BASIS}/needs/profile", headers=koepfe["b"])
    assert profil_b_sicht.status_code == 200, profil_b_sicht.text
    assert profil_b_sicht.json()["persons"] == []
    assert "Mara Vertraulich" not in profil_b_sicht.text
    assert "420000" not in profil_b_sicht.text

    # Und der Abgleich selbst erzeugt fuer B keinen personenbezogenen Befund.
    ergebnis_b = _check(koepfe["b"])
    assert [f for f in ergebnis_b["findings"] if f["person_id"] is not None] == []
    assert "Mara Vertraulich" not in str(ergebnis_b)

    # Gegenprobe: A sieht die personenbezogenen Befunde sehr wohl.
    ergebnis_a = _check(koepfe["a"])
    assert [f for f in ergebnis_a["findings"] if f["person_name"] == "Mara Vertraulich"]
    # Und die Beihilferegel wertet fuer sie aus, weil der Satz erfasst ist.
    beihilfe = _befund(ergebnis_a, "R22", "Mara Vertraulich")
    assert beihilfe["triggered_by"]["health_coverage"] == "pkv_beihilfe"
    assert beihilfe["missing_facts"] == []

    # Der Haushaltsbogen ist dagegen kein Gesundheitsdatum - B darf ihn lesen.
    assert profil_b_sicht.json()["exists"] is False


def test_das_haushaltsprofil_schreibt_nur_die_gesendeten_felder() -> None:
    """Ein weggelassenes Feld bleibt, ein gesendetes `null` loescht.

    Ohne diese Unterscheidung loeschte jeder Client, der eine Frage nicht
    anbietet, still die Antwort darauf - und genau daraus entstuende die
    erfundene Luecke, gegen die das ganze Modul gebaut ist.
    """
    kopf = _haushalt("p6-teilschreiben")
    _profil(kopf, housing_form="rent", has_oil_tank=False, vehicle_count=1,
            travel_frequency="frequent")

    # Ein zweiter Aufruf mit EINEM Feld darf die uebrigen Antworten nicht
    # anfassen.
    nachher = _profil(kopf, vehicle_count=2)
    assert nachher["housing_form"] == "rent"
    assert nachher["has_oil_tank"] is False
    assert nachher["travel_frequency"] == "frequent"
    assert nachher["vehicle_count"] == 2

    # Ein ausdruecklich gesendetes null loescht - und aus "nein" wird wieder
    # "nie gefragt".
    geloescht = _profil(kopf, has_oil_tank=None)
    assert geloescht["has_oil_tank"] is None
    assert geloescht["housing_form"] == "rent"

    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)
    befund = _befund(_check(kopf), "R13")
    assert befund["outcome"] == "unclear"
    assert "has_oil_tank" in befund["missing_facts"]


def test_die_leere_antwortliste_bleibt_von_der_nie_gestellten_frage_unterscheidbar() -> None:
    """`pets = []` heisst "gefragt, keine Tiere"; `pets = null` heisst "nie
    gefragt". Ohne den Unterschied waere `not_applicable` von `unclear` nicht zu
    trennen - und aus jeder ungestellten Frage wuerde eine erfundene Luecke."""
    kopf = _haushalt("p6-tiere")
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)
    assert _befund(_check(kopf), "R14")["outcome"] == "unclear"

    _profil(kopf, pets=[])
    assert _befund(_check(kopf), "R14")["outcome"] == "not_applicable"

    _profil(kopf, pets=["dog"])
    hund = _befund(_check(kopf), "R14")
    assert hund["outcome"] == "gap"
    assert hund["triggered_by"]["pets"] == "dog"


# ---------------------------------------------------------------------------
# 8. Pflichtfall 8: fremder Haushalt
# ---------------------------------------------------------------------------


def test_ein_fremder_haushalt_bekommt_auf_jeden_bedarfsendpunkt_genau_404() -> None:
    """Kein 403, kein 422 - ein abweichender Code verriete, dass es die Person
    gibt und sie nur nicht zugaenglich ist."""
    eigen = _haushalt("p6-eigen")
    person_id = _person(eigen, "Frieda Eigen", 40, employment_status="employed",
                        health_coverage="gkv", net_income_minor=300_000)
    _police(eigen, "private_liability", sum_insured_minor=1_000_000_000)

    fremd = _haushalt("p6-fremd")
    abfragen: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", f"{BASIS}/needs/profile/persons/{person_id}", None),
        ("PUT", f"{BASIS}/needs/profile/persons/{person_id}", {"net_income_minor": 1}),
    ]
    for methode, pfad, koerper in abfragen:
        antwort = client.request(methode, pfad, json=koerper, headers=fremd)
        assert antwort.status_code == 404, f"{methode} {pfad}: {antwort.status_code}"

    # Der Abgleich des fremden Haushalts kennt weder Person noch Police.
    fremdes_ergebnis = _check(fremd)
    assert fremdes_ergebnis["stock_empty"] is True
    assert "Frieda Eigen" not in str(fremdes_ergebnis)
    assert client.get(f"{BASIS}/duplicates", headers=fremd).json() == []
    assert client.get(f"{BASIS}/needs/profile", headers=fremd).json()["persons"] == []


def test_eine_entscheidung_des_fremden_haushalts_loescht_die_eigene_nicht() -> None:
    """Die Ruecknahme ist idempotent und ohne 404 - sie darf deshalb erst recht
    nicht ueber die Haushaltsgrenze wirken."""
    eigen = _haushalt("p6-entscheidung-eigen")
    _profil(eigen, housing_form="rent", travel_frequency="never", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    _police(eigen, "private_liability", sum_insured_minor=1_000_000_000)
    assert client.post(
        f"{BASIS}/needs/R17/dismiss", json={"decision": "dismissed"}, headers=eigen
    ).status_code == 204

    fremd = _haushalt("p6-entscheidung-fremd")
    assert client.delete(f"{BASIS}/needs/R17/dismiss", headers=fremd).status_code == 204

    assert _befund(_check(eigen), "R17")["outcome"] == "dismissed"


def test_ein_betrachter_darf_lesen_aber_nicht_entscheiden() -> None:
    """Rollen-Gate: der Abgleich ist lesend, die Entscheidung ist schreibend."""
    token_besitzer = _signup(
        f"p6-rolle-owner-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-65"
    )
    token_viewer = _signup(
        f"p6-rolle-viewer-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-66"
    )
    haushalt = client.post(
        "/v1/households", json={"name": "P6-Rollen"}, headers=_auth_headers(token_besitzer)
    )
    haushalt_id = str(haushalt.json()["id"])
    besitzer = {**_auth_headers(token_besitzer), "X-Household-Id": haushalt_id}
    einladung = client.post("/v1/households/invites", json={"role": "viewer"}, headers=besitzer)
    assert einladung.status_code == 201, einladung.text
    beigetreten = client.post(
        "/v1/households/invites/redeem",
        json={"code": einladung.json()["code"]},
        headers=_auth_headers(token_viewer),
    )
    assert beigetreten.status_code == 200, beigetreten.text
    viewer = {**_auth_headers(token_viewer), "X-Household-Id": haushalt_id}

    assert client.get(f"{BASIS}/needs/check", headers=viewer).status_code == 200
    assert client.get(f"{BASIS}/needs/completeness", headers=viewer).status_code == 200
    assert client.put(f"{BASIS}/needs/profile", json={"vehicle_count": 1},
                      headers=viewer).status_code == 403
    assert client.post(f"{BASIS}/needs/R17/dismiss", json={"decision": "dismissed"},
                       headers=viewer).status_code == 403


# ---------------------------------------------------------------------------
# 9. Der Regelkatalog als Datenpaket
# ---------------------------------------------------------------------------


def test_der_regelkatalog_laedt_vollstaendig_und_ohne_beanstandung() -> None:
    """Die Selbstpruefung des Auswerters ueber sein Datenpaket.

    Faellt eine Zeile beim Laden durch, erzeugt sie keinen Befund - das ist die
    sichere Richtung, aber es waere still. Deshalb steht sie hier.
    """
    katalog = needs_service.rule_catalog(refresh=True)
    assert katalog.problems == ()
    assert len(katalog.rules) == len(insurance_rules.INSURANCE_RULES)
    assert {regel.rule_key for regel in katalog.rules} == set(insurance_rules.RULES_BY_KEY)


def test_jede_regel_zeigt_auf_eine_geseedete_sparte_und_ein_bekanntes_merkmal(
    profil_a: dict[str, Any],
) -> None:
    """Pruefung 5 des Seed-Tests, von der Bedarfsseite her.

    Eine Regel auf eine nicht geseedete Sparte fiele sonst erst im Betrieb auf -
    als Befund, der auf eine Sparte zeigt, die es nicht gibt.
    """
    lines = client.get(f"{BASIS}/lines", headers=profil_a["kopf"])
    assert lines.status_code == 200, lines.text
    geseedet = {zeile["line_key"] for zeile in lines.json()}
    unbekannt = insurance_rules.line_keys_used() - geseedet
    assert unbekannt == set(), f"Regeln zeigen auf nicht geseedete Sparten: {sorted(unbekannt)}"

    keys = client.get(f"{BASIS}/coverage-keys", headers=profil_a["kopf"])
    assert keys.status_code == 200, keys.text
    bausteine = {zeile["coverage_key"] for zeile in keys.json()}
    assert insurance_rules.coverage_keys_used() <= bausteine

    unbekannte_merkmale = insurance_rules.facts_used() - set(needs_service.FACT_LABELS)
    assert unbekannte_merkmale == set(), f"Unbekannte Merkmale: {sorted(unbekannte_merkmale)}"


def test_jede_regel_traegt_eine_fundstelle_ausser_der_personenaussetzung() -> None:
    """Ohne Quelle und Pruefdatum stuende in einem Befund eine Wertung ohne
    Herkunft. Die einzige Ausnahme ist R7b - sie trifft keine Bedarfsaussage."""
    ohne_quelle = [
        regel.rule_key
        for regel in insurance_rules.INSURANCE_RULES
        if not regel.source_url or regel.source_checked_on is None
    ]
    assert ohne_quelle == ["R7b"]
    assert insurance_rules.RULES_BY_KEY["R7b"].special == "person_suspends"


def test_eine_verstorbene_person_setzt_die_personenbezogenen_regeln_aus() -> None:
    """R7b, Ausschlussart 1 aus 10.3.

    Ohne sie verlangte der Abgleich fuer eine verstorbene Person weiter
    Berufsunfaehigkeit und Risikoleben - der Fall, in dem ein falscher Befund am
    meisten schadet.
    """
    kopf = _haushalt("p6-todesfall")
    _profil(kopf, housing_form="rent", travel_frequency="never", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)
    lebt = _person(kopf, "Gerd Lebt", 50, employment_status="employed", health_coverage="gkv",
                   net_income_minor=300_000)
    verstorben = _person(kopf, "Hilde Verstorben", 78, employment_status="employed",
                         health_coverage="gkv", net_income_minor=200_000)
    assert lebt and verstorben

    vorher = _check(kopf)
    assert _befund(vorher, "R4", "Hilde Verstorben")["outcome"] == "gap"

    gesetzt = client.put(
        f"{BASIS}/needs/profile/persons/{verstorben}",
        json={"died_on": (HEUTE - timedelta(days=30)).isoformat()},
        headers=kopf,
    )
    assert gesetzt.status_code == 200, gesetzt.text

    nachher = _check(kopf)
    for rule_key in ("R1", "R4", "R23"):
        ausgesetzt = _befund(nachher, rule_key, "Hilde Verstorben")
        assert ausgesetzt["outcome"] == "not_applicable", rule_key
    # Und die lebende Person bleibt unberuehrt.
    assert _befund(nachher, "R4", "Gerd Lebt")["outcome"] == "gap"
    aussetzung = _befund(nachher, "R7b", "Hilde Verstorben")
    assert aussetzung["outcome"] == "not_applicable"
    assert "died_on" in aussetzung["triggered_by"]


# ---------------------------------------------------------------------------
# 9. Die Korrekturen der Abnahme
# ---------------------------------------------------------------------------


def test_eine_luecke_nennt_den_zielwert_auch_ohne_police(profil_b: dict[str, Any]) -> None:
    """Plan 13.3 zu R7: "Risikoleben fehlt, Zielwert `open_debt` = 180.000 Euro".

    Vor der Korrektur kehrte `_amount_outcome` bei leerer Policenmenge zurueck,
    bevor der Zielwert im Befund landete. Der Befund sagte dann "keine Police
    erfasst", ohne die Zahl zu nennen, gegen die geprueft wurde - also ohne die
    Grundlage, die Leitsatz 1 fuer jeden Befund verlangt.
    """
    befund = _befund(profil_b["ergebnis"], "R7")
    assert befund["outcome"] == "gap"
    assert befund["target_value_minor"] == 18_000_000
    assert befund["triggered_by"]["open_debt_minor"] == 18_000_000


def test_ein_trifft_nicht_zu_nennt_keine_fehlende_angabe(profil_a: dict[str, Any]) -> None:
    """Widerspruchsfrei: was nicht greift, verlangt auch nichts.

    R9a prueft die Vollkaskodeckung eines alten Fahrzeugs. Ohne erfasstes
    Fahrzeug greift die Regel nicht - trug aber trotzdem
    `vehicle_max_age_years` als fehlende Angabe, weil der Auswerter die Spur
    der Bedingungspruefung ungefiltert uebernahm.
    """
    tabelle = _tabelle(profil_a["ergebnis"])
    assert tabelle[("R9a", None)] == "not_applicable"
    for befund in profil_a["ergebnis"]["findings"]:
        if befund["outcome"] != "unclear":
            assert befund["missing_facts"] == [], befund["rule_key"]


def test_ohne_erfasste_person_verschwindet_keine_personenregel() -> None:
    """Leitsatz 4: der Abgleich sagt, dass er nichts weiss, statt zu schweigen.

    Vorher erzeugte eine personenbezogene Regel ohne erfasste Person gar keine
    Zeile - auch R1 nicht, die Krankenversicherungspflicht auf S0. Der
    Bildschirm sah damit vollstaendig aus, obwohl acht Regeln fehlten.
    """
    kopf = _haushalt("p6-ohne-person")
    _profil(kopf, housing_form="rent", travel_frequency="occasional", vehicle_count=0,
            children_count=0, pets=[], has_oil_tank=False)
    _police(kopf, "private_liability", sum_insured_minor=1_000_000_000)

    ergebnis = _check(kopf)
    personenregeln = [f for f in ergebnis["findings"] if f["scope"] == "person"]
    assert personenregeln, "Personenbezogene Regeln fehlen vollstaendig"

    krankenversicherung = _befund(ergebnis, "R1")
    assert krankenversicherung["outcome"] == "unclear"
    assert "keine Person" in krankenversicherung["outcome_reason"]
    # Und keine dieser Regeln behauptet eine Luecke.
    assert [f["rule_key"] for f in personenregeln if f["outcome"] in LUECKENZUSTAENDE] == []
