"""Gesundheitsschutz des Policenregisters (docs/VERSICHERUNGEN-PLAN.md, P4, 13.4).

Der Plan nennt diese Pruefung ausdruecklich prueffbeduerftig und begruendet das
mit einem Satz, der den Ton dieser Datei vorgibt: eine Luecke hier ist ein
Datenschutzvorfall und kein Fehler. Geprueft wird deshalb nicht, ob ein Feld
ankommt, sondern wer eine Zeile sehen darf - und zwar aus der Sicht zweier
Nutzer DESSELBEN Haushalts. Die Haushaltsgrenze allein beweist hier nichts:
sie war schon in P1 dicht, und genau deshalb faellt eine fehlende
Personenpruefung in keinem Test auf, der nur einen Nutzer kennt.

Vier Zusagen, die dieser Datei ihre Form geben:

1. **Der Schutz haengt am Erstattungsmodul, nicht an einem zweiten
   Rechtesystem.** `health_can_manage_or_access()` uebersetzt "Zugriff auf eine
   Person" in "Zugriff auf eine Police" und entscheidet selbst nichts;
   `health_can_access_person()` bleibt unveraendert. Das wird hier am
   Funktionsrumpf geprueft und nicht am Kommentar darueber.
2. **Ohne Personenbezug bleibt eine Gesundheitspolice sichtbar.** Sonst waeren
   die in P1 angelegten Policen mit dem Einspielen von P4 stillschweigend
   verschwunden - eine Datenmigration, die niemand angeordnet hat.
3. **`using` allein genuegt nicht.** Ein Editor ohne Freigabe darf eine Police
   auch nicht in einen Zustand schreiben, den er danach nicht mehr lesen kann;
   dafuer traegt die Policy dieselbe Bedingung im `with check`.
4. **Ein Name ist kein Gesundheitsdatum.** `insured_person_label()` gibt genau
   ein Feld heraus; Vorgaenge und Dokumente derselben Person bleiben unsichtbar.

Laeuft wie alle API-Tests gegen ein echtes lokales Supabase mit echten
Anmeldungen - der Zugriffsschutz steht in der Datenbank, ein Test mit
gemockter Sitzung prueft ihn deshalb nicht.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup, _user_id_from_token

client = TestClient(app)

BASIS = "/v1/insurance"

# Direkte Verbindung fuer die Freigabeverwaltung: fuer health_access_grants
# gibt es (bewusst) keinen Endpunkt, die Freigabe entsteht sonst nur beim
# Anlegen einer Person. Gleiches Muster wie tests/test_reimbursements.py.
_DSN = os.environ.get(
    "TEST_DIRECT_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:55322/postgres",
)

# Eine gesundheitsnahe und eine sachliche Sparte. Der Unterschied ist nicht der
# Name, sondern insurance_lines.risk_field - daraus setzt der Trigger
# is_health_scoped, und daran haengt der ganze Rest dieser Datei.
GESUNDHEITSSPARTE = "dental_supplementary"
SACHSPARTE = "household_contents"


# ---------------------------------------------------------------------------
# Aufbau: drei Nutzer, ein Haushalt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Welt:
    """Ein Haushalt mit drei Nutzern und einer versicherten Person.

    `a` hat Zugriff auf die Person (er hat sie angelegt und dabei die
    Verwaltungsfreigabe bekommen), `b` nicht. Beide sind Editoren - der
    Unterschied zwischen ihnen ist ausschliesslich die Gesundheitsfreigabe und
    nicht die Haushaltsrolle. Genau das macht den Fall zum Testfall.
    """

    haushalt_id: str
    besitzer: dict[str, str]
    a: dict[str, str]
    b: dict[str, str]
    b_user_id: str
    person_id: str
    person_name: str


def _kopf(token: str, haushalt_id: str) -> dict[str, str]:
    return {**_auth_headers(token), "X-Household-Id": haushalt_id}


@pytest.fixture(scope="module")
def token_besitzer() -> str:
    """Modulweit, weil jede Anmeldung ein echter Supabase-Aufruf ist.

    Die Nutzer ueberleben das Aufraeumen nach jedem Test (die autouse-Fixture
    entfernt nur, was WAEHREND des Tests entstand, und modulweite Fixtures baut
    pytest davor auf); der Haushalt darunter entsteht dagegen je Test neu, damit
    Listen und Kennzahlen exakt gezaehlt werden koennen.
    """
    return _signup(f"ins-h-owner-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-21")


@pytest.fixture(scope="module")
def token_a() -> str:
    return _signup(f"ins-h-a-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-22")


@pytest.fixture(scope="module")
def token_b() -> str:
    return _signup(f"ins-h-b-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-23")


def _einladen(
    besitzer: dict[str, str], haushalt_id: str, token: str, rolle: str
) -> dict[str, str]:
    einladung = client.post("/v1/households/invites", json={"role": rolle}, headers=besitzer)
    assert einladung.status_code == 201, einladung.text
    beigetreten = client.post(
        "/v1/households/invites/redeem",
        json={"code": einladung.json()["code"]},
        headers=_auth_headers(token),
    )
    assert beigetreten.status_code == 200, beigetreten.text
    return _kopf(token, haushalt_id)


@pytest.fixture()
def welt(token_besitzer: str, token_a: str, token_b: str) -> Welt:
    haushalt = client.post(
        "/v1/households",
        json={"name": "Gesundheitsschutz-Testhaushalt"},
        headers=_auth_headers(token_besitzer),
    ).json()
    besitzer = _kopf(token_besitzer, str(haushalt["id"]))
    a = _einladen(besitzer, str(haushalt["id"]), token_a, "editor")
    b = _einladen(besitzer, str(haushalt["id"]), token_b, "editor")

    # A legt die Person an und bekommt dabei die Verwaltungsfreigabe - das ist
    # der einzige Weg, auf dem eine Freigabe ohne Zutun des Besitzers entsteht.
    person = client.post(
        "/v1/reimbursements/people",
        json={"display_name": "Mara Beispiel"},
        headers=a,
    )
    assert person.status_code == 201, person.text

    return Welt(
        haushalt_id=str(haushalt["id"]),
        besitzer=besitzer,
        a=a,
        b=b,
        b_user_id=_user_id_from_token(token_b),
        person_id=str(person.json()["id"]),
        person_name="Mara Beispiel",
    )


def _police(kopf: dict[str, str], line_key: str, **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {"line_key": line_key, "insurer_name": "Beispiel Versicherung AG"}
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/policies", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _verknuepfe(
    kopf: dict[str, str], policy_id: str, person_id: str, rolle: str = "insured"
) -> dict[str, Any]:
    antwort = client.post(
        f"{BASIS}/policies/{policy_id}/persons",
        json={"insured_person_id": person_id, "role": rolle},
        headers=kopf,
    )
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _sichtbare_ids(kopf: dict[str, str]) -> set[str]:
    antwort = client.get(f"{BASIS}/policies", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return {str(eintrag["id"]) for eintrag in antwort.json()}


def _setze_freigabe(welt: Welt, permission: str) -> None:
    """Legt eine Gesundheitsfreigabe fuer B an - oder aendert sie.

    Direkt in der Datenbank und nicht ueber die API: fuer
    health_access_grants gibt es keinen Endpunkt. Damit prueft der Test die
    Wirkung der Freigabe und nicht den Weg, auf dem sie entsteht.
    """
    with psycopg.connect(_DSN, autocommit=True) as verbindung:
        verbindung.execute(
            "insert into health_access_grants "
            "(household_id, insured_person_id, user_id, permission) "
            "values (%s, %s, %s, %s) "
            "on conflict (household_id, insured_person_id, user_id) "
            "do update set permission = excluded.permission",
            (welt.haushalt_id, welt.person_id, welt.b_user_id, permission),
        )


def _entziehe_freigabe(welt: Welt) -> None:
    with psycopg.connect(_DSN, autocommit=True) as verbindung:
        verbindung.execute(
            "delete from health_access_grants where household_id = %s "
            "and insured_person_id = %s and user_id = %s",
            (welt.haushalt_id, welt.person_id, welt.b_user_id),
        )


async def _funktionsrumpf(name: str) -> str:
    session_factory = get_session_factory()
    async with session_factory() as session:
        rumpf = (
            await session.execute(
                text(
                    "select pg_get_functiondef(p.oid) from pg_proc p "
                    "join pg_namespace n on n.oid = p.pronamespace "
                    "where n.nspname = 'public' and p.proname = :name"
                ),
                {"name": name},
            )
        ).scalar_one()
    return str(rumpf)


# ---------------------------------------------------------------------------
# 1. Die gesundheitsnahe Police mit Personenbezug
# ---------------------------------------------------------------------------


def test_die_gesundheitspolice_mit_person_ist_fuer_den_zweiten_editor_nirgends_sichtbar(
    welt: Welt,
) -> None:
    """Liste, Detail, Kennzahlen UND Aufgabenliste - alle vier oder keiner.

    Die Aufgabenliste steht hier bewusst dabei: sie ist der Endpunkt, der die
    Police am beilaeufigsten verraet. `InsuranceTask` traegt `policy_id` und
    `policy_label`; eine RLS, die nur die Policenliste filtert, liesse die
    Zahnzusatzversicherung dort unter ihrem Namen stehen.
    """
    police = _police(welt.a, GESUNDHEITSSPARTE, insurer_name="Zahnkasse AG")
    _verknuepfe(welt.a, police["id"], welt.person_id)

    # A hat die Person angelegt und sieht die Police.
    assert police["id"] in _sichtbare_ids(welt.a)
    assert client.get(f"{BASIS}/policies/{police['id']}", headers=welt.a).status_code == 200
    assert client.get(f"{BASIS}/summary", headers=welt.a).json()["policy_count"] == 1

    # B ist Editor im selben Haushalt - und sieht nichts davon.
    assert _sichtbare_ids(welt.b) == set()
    detail = client.get(f"{BASIS}/policies/{police['id']}", headers=welt.b)
    assert detail.status_code == 404, detail.text
    assert detail.json()["detail"] == "insurance_policy_not_found"
    assert client.get(f"{BASIS}/summary", headers=welt.b).json()["policy_count"] == 0
    aufgaben = client.get(f"{BASIS}/tasks", headers=welt.b).json()
    assert aufgaben == []
    # Und der Name der versicherten Person taucht in keiner Antwort auf.
    assert welt.person_name not in client.get(f"{BASIS}/policies", headers=welt.b).text

    # Der Besitzer sieht sie ohne ausdrueckliche Freigabe: current_household_role()
    # = 'owner' ist in health_can_access_person() der erste Zweig. Das ist eine
    # Entscheidung des Erstattungsmoduls und keine des Versicherungsmoduls.
    assert police["id"] in _sichtbare_ids(welt.besitzer)


def test_ohne_personenbezug_bleibt_die_gesundheitspolice_fuer_alle_sichtbar(
    welt: Welt,
) -> None:
    """Der P1-Bestand darf mit P4 nicht verschwinden.

    Eine PKV-Police ohne benannte Person enthaelt kein Personendatum; sie zu
    verbergen naehme dem Haushalt die Uebersicht, ohne irgendetwas zu schuetzen.
    Der zweite Teil des Tests ist der eigentliche Beweis: DIESELBE Police
    verschwindet in dem Moment, in dem eine fremde Person an ihr haengt, und
    kommt zurueck, sobald die Zuordnung wieder geloest ist. Damit steht fest,
    dass die Personenzuordnung schaltet und nicht die Sparte.
    """
    police = _police(welt.a, "health_private", insurer_name="Privatkasse AG")

    assert police["id"] in _sichtbare_ids(welt.b)
    assert client.get(f"{BASIS}/policies/{police['id']}", headers=welt.b).status_code == 200
    assert client.get(f"{BASIS}/summary", headers=welt.b).json()["policy_count"] == 1

    verknuepfung = _verknuepfe(welt.a, police["id"], welt.person_id)
    assert police["id"] not in _sichtbare_ids(welt.b)
    assert client.get(f"{BASIS}/policies/{police['id']}", headers=welt.b).status_code == 404

    geloest = client.delete(
        f"{BASIS}/policies/{police['id']}/persons/{verknuepfung['id']}", headers=welt.a
    )
    assert geloest.status_code == 204, geloest.text
    assert police["id"] in _sichtbare_ids(welt.b)


def test_eine_sachpolice_bleibt_sichtbar_und_gibt_den_namen_ohne_die_gesundheitsdaten_heraus(
    welt: Welt,
) -> None:
    """Hausrat mit mitversicherter Person: sichtbar, samt Name.

    Das ist keine Nachlaessigkeit, sondern der Grund fuer `insured_person_label()`:
    die Funktion gibt genau EIN Feld heraus. Wer die Zuordnung selbst erfassen
    kann, muss sie auch lesen koennen - waehrend die Vorgaenge und Belege
    derselben Person unsichtbar bleiben. Der letzte Block prueft deshalb beides
    in einem Atemzug.
    """
    police = _police(welt.a, SACHSPARTE, insurer_name="Nordwacht AG")
    _verknuepfe(welt.a, police["id"], welt.person_id)

    for kopf in (welt.a, welt.b, welt.besitzer):
        assert police["id"] in _sichtbare_ids(kopf)
        detail = client.get(f"{BASIS}/policies/{police['id']}", headers=kopf)
        assert detail.status_code == 200, detail.text

    beteiligte = client.get(f"{BASIS}/policies/{police['id']}/persons", headers=welt.b)
    assert beteiligte.status_code == 200, beteiligte.text
    assert [eintrag["display_name"] for eintrag in beteiligte.json()] == [welt.person_name]

    # Ein Name ist kein Gesundheitsdatum - die Person selbst bleibt fuer B zu.
    personen = client.get("/v1/reimbursements/people", headers=welt.b)
    assert personen.status_code == 200, personen.text
    assert personen.json() == []
    faelle = client.get("/v1/reimbursements/cases", headers=welt.b)
    assert faelle.status_code == 200, faelle.text
    assert faelle.json() == []


# ---------------------------------------------------------------------------
# 2. Schreiben: using UND with check
# ---------------------------------------------------------------------------


def test_der_editor_ohne_freigabe_kann_die_verborgene_police_auf_keinem_weg_schreiben(
    welt: Welt,
) -> None:
    """Jeder schreibende Weg der Police antwortet 404 - kein 403, kein 500.

    404 und nicht 403 ist Absicht: ein abweichender Code verriete, dass es die
    Police gibt und sie nur nicht zugaenglich ist. Die Liste enthaelt auch die
    P4-Wege (Beteiligte, Bausteine, Objekte, Buendel), denn ueber sie liefe
    sonst genau die Aenderung, die der Leseschutz verhindern soll.
    """
    police = _police(welt.a, GESUNDHEITSSPARTE, insurer_name="Zahnkasse AG")
    verknuepfung = _verknuepfe(welt.a, police["id"], welt.person_id)
    policy_id = str(police["id"])
    irgendwas = str(uuid.uuid4())

    versuche: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = [
        ("GET", f"{BASIS}/policies/{policy_id}/purpose", None, {}),
        ("PATCH", f"{BASIS}/policies/{policy_id}", {"label": "Fremd"}, {"If-Match": 'W/"0.1"'}),
        ("POST", f"{BASIS}/policies/{policy_id}/status", {"status": "ended"}, {}),
        ("POST", f"{BASIS}/policies/{policy_id}/confirm-line", {"line_key": SACHSPARTE}, {}),
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
        ("GET", f"{BASIS}/policies/{policy_id}/persons", None, {}),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/persons",
            {"insured_person_id": welt.person_id, "role": "driver"},
            {},
        ),
        ("DELETE", f"{BASIS}/policies/{policy_id}/persons/{verknuepfung['id']}", None, {}),
        ("GET", f"{BASIS}/policies/{policy_id}/coverages", None, {}),
        ("PUT", f"{BASIS}/policies/{policy_id}/coverages", {"coverages": []}, {}),
        ("GET", f"{BASIS}/policies/{policy_id}/objects", None, {}),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/objects",
            {"new_object": {"kind": "device", "label": "Fremdgeraet"}},
            {},
        ),
        ("DELETE", f"{BASIS}/policies/{policy_id}/objects/{irgendwas}", None, {}),
        ("POST", f"{BASIS}/policies/{policy_id}/bundle", {"parent_policy_id": None}, {}),
        ("GET", f"{BASIS}/policies/{policy_id}/premium-notices", None, {}),
        ("DELETE", f"{BASIS}/policies/{policy_id}", None, {}),
    ]

    for methode, pfad, koerper, zusatz in versuche:
        antwort = client.request(methode, pfad, json=koerper, headers={**welt.b, **zusatz})
        assert antwort.status_code == 404, f"{methode} {pfad}: {antwort.status_code} {antwort.text}"

    # Und aus A gesehen ist danach nichts passiert - insbesondere hat der
    # Objektversuch kein Objekt hinterlassen.
    unveraendert = client.get(f"{BASIS}/policies/{policy_id}", headers=welt.a).json()["policy"]
    assert unveraendert["version"] == 1
    assert unveraendert["line_key"] == GESUNDHEITSSPARTE
    assert unveraendert["status"] == "active"
    assert len(client.get(f"{BASIS}/policies/{policy_id}/persons", headers=welt.a).json()) == 1
    assert client.get(f"{BASIS}/objects", headers=welt.a).json() == []


def test_with_check_verhindert_das_umstellen_auf_eine_gesundheitssparte(welt: Welt) -> None:
    """Der Fall, den `using` allein nicht abdeckt.

    B darf die Hausratpolice lesen und aendern. Stellte er sie auf eine
    Gesundheitssparte um, waere die Zeile fuer ihn im selben Augenblick
    unlesbar - er haette Daten geschrieben, die er nicht mehr sieht, und die
    Police waere fuer den Rest des Haushalts unveraendert lesbar geblieben. Genau
    das faengt die zweite Haelfte der Policy ab; ohne sie liefe der Aufruf
    fehlerfrei durch.

    Die Gegenprobe im selben Test ist wichtig: dieselbe Umstellung gelingt an
    einer Police OHNE fremden Personenbezug. Die Ablehnung haengt also an der
    Person und nicht an der Sparte.
    """
    police = _police(welt.a, SACHSPARTE, insurer_name="Nordwacht AG")
    _verknuepfe(welt.a, police["id"], welt.person_id)
    assert client.get(f"{BASIS}/policies/{police['id']}", headers=welt.b).status_code == 200

    abgelehnt = client.post(
        f"{BASIS}/policies/{police['id']}/confirm-line",
        json={"line_key": GESUNDHEITSSPARTE},
        headers=welt.b,
    )
    assert abgelehnt.status_code == 403, abgelehnt.text
    assert abgelehnt.json()["detail"] == "insufficient_privilege"
    # Die Meldung von Postgres nennt Tabelle und Zeileninhalt - sie darf den
    # Client nicht erreichen (app/core/errors.py).
    rumpf = abgelehnt.text.lower()
    assert "row-level security" not in rumpf
    assert "insurance_policies" not in rumpf

    danach = client.get(f"{BASIS}/policies/{police['id']}", headers=welt.a).json()["policy"]
    assert danach["line_key"] == SACHSPARTE

    eigene = _police(welt.b, SACHSPARTE, insurer_name="Ohne Personenbezug")
    gelungen = client.post(
        f"{BASIS}/policies/{eigene['id']}/confirm-line",
        json={"line_key": GESUNDHEITSSPARTE},
        headers=welt.b,
    )
    assert gelungen.status_code == 200, gelungen.text
    assert gelungen.json()["line_key"] == GESUNDHEITSSPARTE


def test_erst_die_freigabe_oeffnet_die_police_und_ihr_entzug_schliesst_sie_wieder(
    welt: Welt,
) -> None:
    """Der Schalter ist die Freigabe, nicht irgendein Nebeneffekt.

    Ein Test, der nur "unsichtbar" zeigt, kann auch einen kaputten Join
    beweisen. Deshalb hier der Rueckweg: mit einer Lesefreigabe auf DIESELBE
    Person erscheint DIESELBE Police, und ihr Entzug nimmt sie wieder weg.

    Die Lesefreigabe eroeffnet dabei ausdruecklich auch den Schreibweg auf die
    Police - `health_can_manage_or_access()` fragt bewusst nur
    `health_can_access_person()`. Eine zusaetzliche manage-Pruefung stuende in
    einer Read-Policy am falschen Platz und entzoege einem lesend Freigegebenen
    die Police, obwohl er die Person sehen darf. Der Fall steht hier, damit
    diese Entscheidung nicht unbemerkt gedreht wird.
    """
    police = _police(welt.a, GESUNDHEITSSPARTE, insurer_name="Zahnkasse AG")
    _verknuepfe(welt.a, police["id"], welt.person_id)
    assert police["id"] not in _sichtbare_ids(welt.b)

    _setze_freigabe(welt, "read")
    assert police["id"] in _sichtbare_ids(welt.b)
    assert client.get(f"{BASIS}/policies/{police['id']}", headers=welt.b).status_code == 200
    geaendert = client.patch(
        f"{BASIS}/policies/{police['id']}",
        json={"label": "Zahnzusatz Mara"},
        headers={**welt.b, "If-Match": 'W/"0.1"'},
    )
    assert geaendert.status_code == 200, geaendert.text

    _entziehe_freigabe(welt)
    assert police["id"] not in _sichtbare_ids(welt.b)
    assert client.get(f"{BASIS}/policies/{police['id']}", headers=welt.b).status_code == 404
    # A sieht die Aenderung weiterhin - entzogen wurde eine Freigabe, nicht ein
    # Schreibvorgang.
    assert client.get(f"{BASIS}/policies/{police['id']}", headers=welt.a).json()["policy"][
        "label"
    ] == "Zahnzusatz Mara"


# ---------------------------------------------------------------------------
# 3. Haushaltsgrenze der P4-Endpunkte
# ---------------------------------------------------------------------------


def test_ein_fremder_haushalt_bekommt_auf_jeden_p4_endpunkt_genau_404(welt: Welt) -> None:
    """Dieselbe Zusage wie in P1, jetzt fuer Beteiligte, Bausteine und Objekte.

    Der fremde Nutzer fragt mit SEINEM eigenen Haushaltskontext nach einer
    bekannten fremden UUID - das ist der Fall, den die Haushaltspruefung der API
    nicht abfaengt und den deshalb die Abfrage selbst abfangen muss.
    """
    police = _police(welt.a, SACHSPARTE, insurer_name="Nordwacht AG")
    objekt = client.post(
        f"{BASIS}/objects",
        json={"kind": "vehicle", "label": "Kombi", "acquired_on": "2026-03-10"},
        headers=welt.a,
    )
    assert objekt.status_code == 201, objekt.text
    object_id = str(objekt.json()["id"])
    verknuepfung = _verknuepfe(welt.a, str(police["id"]), welt.person_id)

    fremd_token = _signup(f"ins-h-x-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-b-24")
    fremd_haushalt = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(fremd_token)
    ).json()
    fremd = _kopf(fremd_token, str(fremd_haushalt["id"]))

    policy_id = str(police["id"])
    abfragen: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = [
        ("GET", f"{BASIS}/policies/{policy_id}/persons", None, {}),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/persons",
            {"insured_person_id": welt.person_id},
            {},
        ),
        ("DELETE", f"{BASIS}/policies/{policy_id}/persons/{verknuepfung['id']}", None, {}),
        ("GET", f"{BASIS}/policies/{policy_id}/coverages", None, {}),
        ("PUT", f"{BASIS}/policies/{policy_id}/coverages", {"coverages": []}, {}),
        ("GET", f"{BASIS}/policies/{policy_id}/objects", None, {}),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/objects",
            {"insured_object_id": object_id},
            {},
        ),
        ("DELETE", f"{BASIS}/policies/{policy_id}/objects/{object_id}", None, {}),
        ("POST", f"{BASIS}/policies/{policy_id}/bundle", {"parent_policy_id": None}, {}),
        ("GET", f"{BASIS}/objects/{object_id}", None, {}),
        ("PATCH", f"{BASIS}/objects/{object_id}", {"label": "Fremd"}, {"If-Match": '"1"'}),
        ("DELETE", f"{BASIS}/objects/{object_id}", None, {}),
        ("GET", f"{BASIS}/objects/{object_id}/registrations", None, {}),
        (
            "POST",
            f"{BASIS}/objects/{object_id}/registrations",
            {"plate": "N-XX 1", "valid_from": "2026-04-01"},
            {},
        ),
    ]

    for methode, pfad, koerper, zusatz in abfragen:
        antwort = client.request(methode, pfad, json=koerper, headers={**fremd, **zusatz})
        assert antwort.status_code == 404, f"{methode} {pfad}: {antwort.status_code} {antwort.text}"

    assert client.get(f"{BASIS}/objects", headers=fremd).json() == []
    # Im eigenen Haushalt ist alles unberuehrt geblieben.
    assert len(client.get(f"{BASIS}/policies/{policy_id}/persons", headers=welt.a).json()) == 1
    assert client.get(f"{BASIS}/objects/{object_id}", headers=welt.a).json()["version"] == 1


# ---------------------------------------------------------------------------
# 4. Das Fundament: die Funktionen selbst
# ---------------------------------------------------------------------------


async def test_health_can_access_person_kennt_das_versicherungsmodul_nicht(welt: Welt) -> None:
    """Abnahmekriterium der Phase: die Funktion des Erstattungsmoduls bleibt
    unveraendert.

    Ein Bytevergleich mit dem Vorzustand ist im laufenden System nicht
    fuehrbar - die Aussage laesst sich aber schaerfer stellen als "sieht gleich
    aus": der Rumpf darf das Versicherungsmodul ueberhaupt nicht kennen. Nennte
    er es, waere aus der einen Zugriffsregel des Gesundheitszweigs ein zweites,
    querliegendes Rechtesystem geworden.
    """
    zugriff = (await _funktionsrumpf("health_can_access_person")).lower()
    assert "health_access_grants" in zugriff
    assert "current_household_role" in zugriff
    assert "insurance" not in zugriff
    assert "insured_object" not in zugriff
    assert "security definer" in zugriff

    # Die Uebersetzung "Person -> Police" entscheidet nichts selbst: sie ruft
    # health_can_access_person und sonst keine Zugriffsfunktion.
    police = (await _funktionsrumpf("health_can_manage_or_access")).lower()
    assert "health_can_access_person" in police
    assert "health_can_manage_person" not in police
    assert "health_access_grants" not in police
    assert "insurance_policy_persons" in police
    assert "security definer" in police

    # Und der Namensgeber gibt genau ein Feld heraus - kein Geburtsdatum, keine
    # Zeile - und nur innerhalb des aktuellen Haushalts.
    label = (await _funktionsrumpf("insured_person_label")).lower()
    assert "display_name" in label
    assert "birth_date" not in label
    assert "current_household_id()" in label
    assert "security definer" in label


async def test_jede_tabelle_des_moduls_traegt_rls_lese_und_schreibpolicy(welt: Welt) -> None:
    """Ohne `enable row level security` ist eine neue Tabelle fuer jeden
    angemeldeten Nutzer haushaltsuebergreifend offen (0001_schema.sql, Abschnitt
    ueber die Standardrechte) - und das faellt in keinem Test auf, der nur einen
    Haushalt kennt.

    Der Test liest die Tabellennamen aus dem Katalog statt aus einer
    abgeschriebenen Liste: so faellt auch die naechste vergessene Tabelle auf,
    ohne dass jemand daran denken muss. Die beiden Katalogtabellen sind der
    begruendete Sonderfall - sie sind Migrationsgut und tragen deshalb NUR
    einen Leseweg; ihr fehlender Schreibweg ist die Aussage, nicht die Luecke.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        zeilen = (
            await session.execute(
                text(
                    "select c.relname as tabelle, c.relrowsecurity as rls, "
                    "  exists (select 1 from information_schema.columns k "
                    "    where k.table_schema = 'public' and k.table_name = c.relname "
                    "      and k.column_name = 'household_id') as haushaltsgebunden, "
                    "  (select count(*) from pg_policies p where p.schemaname = 'public' "
                    "     and p.tablename = c.relname) as policies, "
                    "  (select count(*) from pg_policies p where p.schemaname = 'public' "
                    "     and p.tablename = c.relname and p.cmd <> 'SELECT') as schreibwege "
                    "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                    "where n.nspname = 'public' and c.relkind = 'r' "
                    "  and (c.relname like 'insurance%' or c.relname like 'insured%') "
                    "order by c.relname"
                )
            )
        ).all()

    assert zeilen, "Kein einziger Treffer - der Namensfilter passt nicht mehr."
    for zeile in zeilen:
        assert zeile.rls, f"{zeile.tabelle} hat keine RLS"
        if zeile.haushaltsgebunden:
            assert zeile.policies >= 2, f"{zeile.tabelle} traegt keinen Lese- und Schreibweg"
            assert zeile.schreibwege >= 1, f"{zeile.tabelle} traegt keinen Schreibweg"
        else:
            # Kein Haushaltsbezug, also Katalog: lesbar fuer jeden Angemeldeten,
            # aenderbar fuer niemanden. Ohne diese Zusage koennte ein einzelner
            # Haushalt den Spartentext aller anderen umschreiben.
            assert zeile.schreibwege == 0, f"{zeile.tabelle} ist ein Katalog und nicht schreibbar"
    haushaltsgebunden = {zeile.tabelle for zeile in zeilen if zeile.haushaltsgebunden}
    assert {
        "insurance_policies",
        "insurance_policy_persons",
        "insurance_policy_objects",
        "insurance_coverages",
        "insured_objects",
        "insured_object_registrations",
    } <= haushaltsgebunden


def test_eine_tierkrankenversicherung_ist_keine_gesundheitspolice(welt: Welt) -> None:
    """Welle 2 fuehrt `pet_health` in `risk_field = 'property'`, nicht in
    'health' - ein Hund ist keine versicherte Person.

    Die Einordnung ist keine Geschmacksfrage: 'health' setzt ueber den Trigger
    `is_health_scoped` und damit den Personenschutz. Eine Tierkrankenversicherung
    waere dann fuer den halben Haushalt unsichtbar, sobald jemand das Tier einer
    Person zuordnet.
    """
    police = _police(welt.a, "pet_health", insurer_name="Tierkasse AG")
    _verknuepfe(welt.a, str(police["id"]), welt.person_id, rolle="policyholder")

    assert police["id"] in _sichtbare_ids(welt.b)
    sparten = {
        zeile["line_key"]: zeile for zeile in client.get(f"{BASIS}/lines", headers=welt.a).json()
    }
    assert sparten["pet_health"]["risk_field"] == "property"
    assert {zeile["line_key"] for zeile in sparten.values() if zeile["risk_field"] == "health"} == {
        "health_statutory",
        "health_private",
        "long_term_care_mandatory",
        "dental_supplementary",
        "travel_health",
    }
