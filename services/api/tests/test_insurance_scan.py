"""Naechtlicher Versicherungslauf und Fristen-Push (Plan 8.2/8.3, P2).

Gegen ein echtes lokales Supabase, weil beide Laeufe fast nur aus SQL bestehen:
ein Nachtlauf, der mit nachgebauten Zeilen geprueft wird, prueft die Nachbauten.

**Der Lauf liegt in `services/worker`, dieser Test in `services/api`.** Das ist
kein Versehen: der Fristenrechner steht in `services/api`, und der Worker holt
ihn sich ueber denselben `sys.path`-Spiegel, den `worker/contracts/reconcile.py`
seit Phase 2 benutzt (`services/worker/worker/insurance/__init__.py`). Wer den
Push in der Worker-Suite pruefte, muesste diesen Spiegel dort ein zweites Mal
aufbauen - und die Policen selbst muessten per rohem SQL entstehen statt ueber
die Endpunkte, die sie im Betrieb anlegen.

**Was hier bewiesen werden soll, in einem Satz:** ein angenommenes Datum wird
zur Frage, ein bestaetigtes zum Countdown - und der Countdown zaehlt auf den Tag,
an dem der Nutzer etwas tun muss, nicht auf das rechtliche Fristende.

Der Versand selbst ist ersetzt (`captured_pushes`); die Expo-API ist nicht
Gegenstand dieses Tests und waere ein Netzwerkaufruf.
"""

import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services.insurance_deadline_service import (
    InsuranceDeadline,
    compute_insurance_deadline,
)
from tests.test_household_isolation import _auth_headers, _signup

# `worker` lebt nicht in dieser Codebase. Derselbe Weg wie in
# app/api/v1/routes/imports.py: Verzeichnis anhaengen statt Paket installieren,
# damit Repo und Docker-Abbild dieselbe Verzeichnistiefe voraussetzen.
_WORKER_DIR = Path(__file__).resolve().parents[2] / "worker"
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from worker.insurance.scan import (  # noqa: E402
    InsuranceFinding,
    InsuranceScanResult,
    scan_insurance,
)
from worker.notifications import insurance_alerts  # noqa: E402
from worker.notifications.insurance_alerts import (  # noqa: E402
    check_insurance_alerts,
    select_pending_alerts,
)
from worker.notifications.push import PushMessage, PushTicket  # noqa: E402

client = TestClient(app)

BASIS = "/v1/insurance"
HEUTE = date.today()

# Zwei Hauptfaelligkeiten, und der Unterschied ist keine Kosmetik.
#
# FERNE: fester Monatstag mitten im Jahr. Die Faelle, die `select_pending_alerts`
# mit frei gewaehlten Stichtagen befragen, brauchen genau das - alle Stichtage
# einer Reihe liegen dann im selben Kalenderjahr wie die Wiederkehr, und
# `_next_period_start` sucht sie nicht versehentlich im Vorjahr.
#
# NAHE: relativ zu heute. Die Faelle, die den ganzen Push-Lauf fahren, koennen
# den Stichtag NICHT frei waehlen - `notified_at` schreibt `now()` und haengt
# damit am echten Kalender. Sie laufen deshalb auf `HEUTE`, und die
# Hauptfaelligkeit muss dann weit genug weg sein, dass das empfohlene
# Handlungsdatum sicher in der Zukunft liegt (bei einem festen Monatstag waere
# es an rund zwei Wochen im Jahr bereits verstrichen).
FERNE_HAUPTFAELLIGKEIT = date(HEUTE.year + 2, 6, 15)
NAHE_HAUPTFAELLIGKEIT = HEUTE + timedelta(days=120)


class Haushalt(NamedTuple):
    """Haushalt, Kopfzeilen und der eine Push-Token dieses Haushalts.

    Der Token gehoert dazu, weil beide Laeufe haushaltuebergreifend arbeiten:
    bewertet wird nur, was an DIESES Geraet ging.
    """

    kopf: dict[str, str]
    id: str
    expo_token: str


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


@pytest.fixture()
def haushalt() -> Haushalt:
    token = _signup(f"ins-scan-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    kopf = _auth_headers(token)
    angelegt = client.post(
        "/v1/households", json={"name": "Nachtlauf-Testhaushalt"}, headers=kopf
    )
    assert angelegt.status_code == 201, angelegt.text

    # Ohne Geraet waehlt der Push-Lauf die Police gar nicht erst aus - er
    # markierte sonst `notified_at`, ohne dass je etwas zugestellt wurde.
    expo_token = f"ExponentPushToken[{uuid.uuid4().hex}]"
    geraet = client.post(
        "/v1/devices/push-token",
        json={"expo_token": expo_token, "platform": "ios"},
        headers=kopf,
    )
    assert geraet.status_code == 201, geraet.text

    haushalt_id = str(angelegt.json()["id"])
    return Haushalt(
        kopf={**kopf, "X-Household-Id": haushalt_id}, id=haushalt_id, expo_token=expo_token
    )


@pytest.fixture()
def captured_pushes(monkeypatch: pytest.MonkeyPatch) -> list[PushMessage]:
    """Faengt den Expo-Versand ab und liefert die gesendeten Nachrichten."""
    gesendet: list[PushMessage] = []

    async def _fake_send(messages: list[PushMessage]) -> list[PushTicket]:
        gesendet.extend(messages)
        return [PushTicket(status="ok", id=str(uuid.uuid4())) for _ in messages]

    monkeypatch.setattr(insurance_alerts, "send_expo_push_batch", _fake_send)
    return gesendet


def _police(haushalt: Haushalt, **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "line_key": "occupational_disability",
        "insurer_name": "Nordwacht Leben AG",
    }
    koerper.update(felder)
    antwort = client.post(f"{BASIS}/policies", json=koerper, headers=haushalt.kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _vertrag(haushalt: Haushalt, **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "name": "Versicherungsvertrag",
        "amount_minor": 4599,
        "frequency": "monthly",
        "next_due_date": HEUTE.isoformat(),
        "contract_type": "insurance",
    }
    koerper.update(felder)
    antwort = client.post("/v1/contracts", json=koerper, headers=haushalt.kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _bestaetige(haushalt: Haushalt, policy_id: str) -> dict[str, Any]:
    """Der einzige Weg zu `main_due_source = 'user_confirmed'`."""
    antwort = client.post(
        f"{BASIS}/policies/{policy_id}/confirm-main-due", json=None, headers=haushalt.kopf
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _bestaetigte_police(
    haushalt: Haushalt, *, main_due_on: date = FERNE_HAUPTFAELLIGKEIT, **felder: Any
) -> dict[str, Any]:
    police = _police(haushalt, main_due_on=main_due_on.isoformat(), **felder)
    return _bestaetige(haushalt, police["id"])


def _frist(main_due_on: date, *, heute: date) -> InsuranceDeadline:
    """Dieselbe Rechnung, die der Push benutzt - mit denselben Vorgaben.

    Bewusst der Kern und keine nachgebaute Erwartung: der Test soll den Weg
    vom Datum zum Push pruefen, nicht die Monatsarithmetik ein zweites Mal
    aufschreiben. `period_end` und `post` sind die Vorgaben, mit denen die
    Police oben entsteht (Paragraph 168 Abs. 1 VVG, Spaltendefault 'post').
    """
    return compute_insurance_deadline(
        anchor="period_end",
        deadline_kind="kuendigungsfrist",
        today=heute,
        main_due_on=main_due_on,
        main_due_source="user_confirmed",
    )


async def _execute(sql: str, **params: Any) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def _zeile(policy_id: str, spalten: str) -> dict[str, Any]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        ergebnis = await session.execute(
            text(f"select {spalten} from insurance_policies where id = :id"),  # noqa: S608
            {"id": policy_id},
        )
        return dict(ergebnis.one()._mapping)


async def _vertragszeile(contract_id: str, spalten: str) -> dict[str, Any]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        ergebnis = await session.execute(
            text(f"select {spalten} from contracts where id = :id"),  # noqa: S608
            {"id": contract_id},
        )
        return dict(ergebnis.one()._mapping)


async def _staffel(
    haushalt: Haushalt, lead_days: list[int], *, benachrichtigen: bool = True
) -> None:
    await _execute(
        "insert into insurance_settings (household_id, lead_days, notify_deadline) "
        "values (cast(:household_id as uuid), cast(:lead_days as int[]), :benachrichtigen) "
        "on conflict (household_id) do update set lead_days = excluded.lead_days, "
        "notify_deadline = excluded.notify_deadline",
        household_id=haushalt.id,
        lead_days=lead_days,
        benachrichtigen=benachrichtigen,
    )


async def _lauf(haushalt: Haushalt, *, heute: date = HEUTE) -> InsuranceScanResult:
    """Der Nachtlauf, wie ihn der Actor faehrt: eigene Sitzung, ein Commit."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        ergebnis = await scan_insurance(session, UUID(haushalt.id), today=heute)
        await session.commit()
    return ergebnis


async def _offene_hinweise(
    haushalt: Haushalt, *, heute: date
) -> tuple[list[Any], list[Any]]:
    """Auswahl des Push-Laufs, auf diesen Haushalt eingeschraenkt.

    Der Lauf arbeitet haushaltuebergreifend; die Datenbank traegt die Policen
    der uebrigen Tests. Bewertet wird deshalb nur der eigene Haushalt - dasselbe
    Vorgehen wie in `services/worker/tests/test_contract_alerts.py`.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        fristen, fragen = await select_pending_alerts(session, today=heute)
    return (
        [alarm for alarm in fristen if alarm.household_id == haushalt.id],
        [frage for frage in fragen if frage.household_id == haushalt.id],
    )


def _nachrichten(pushes: list[PushMessage], haushalt: Haushalt) -> list[PushMessage]:
    return [nachricht for nachricht in pushes if nachricht["to"] == haushalt.expo_token]


def _befunde(ergebnis: InsuranceScanResult, kind: str) -> list[InsuranceFinding]:
    return [befund for befund in ergebnis.findings if befund.kind == kind]


# ---------------------------------------------------------------------------
# 1. Ein angenommenes Datum wird zur Frage, nicht zur Frist
# ---------------------------------------------------------------------------


async def test_eine_angenommene_hauptfaelligkeit_erzeugt_eine_rueckfrage_und_keine_frist(
    haushalt: Haushalt, captured_pushes: list[PushMessage]
) -> None:
    """Pflichtfall 6 und die Kernzusage von P2 (Plan, Leitsatz 1).

    Ein Datum mit `main_due_source = 'catalog_suggested'` ist der uebliche
    Termin der Sparte, nicht der Termin dieser Police. Ein Countdown darauf
    saehe von einem belegten nicht zu unterscheiden aus, und ein Push darauf
    schickte den Nutzer an einem erfundenen Datum zur Kuendigung.

    Zwei Policen mit Absicht: nach einer Bestandsuebernahme tragen leicht ein
    Dutzend Policen ein angenommenes Datum. Kaeme je Police eine Nachricht, waere
    das kein Hinweis mehr, sondern der Grund, Benachrichtigungen abzuschalten.
    """
    erste = _police(haushalt, main_due_on=FERNE_HAUPTFAELLIGKEIT.isoformat())
    zweite = _police(
        haushalt,
        line_key="private_liability",
        insurer_name="Suedstern Versicherung AG",
        main_due_on=(FERNE_HAUPTFAELLIGKEIT + timedelta(days=30)).isoformat(),
    )
    assert erste["main_due_source"] == "catalog_suggested"
    assert erste["deadline"]["certainty"] == "assumed"
    # Gerechnet wird, gezaehlt wird nicht - der Countdown fehlt schon in der API.
    assert erste["deadline"]["deadline_legal_on"] is not None
    assert erste["deadline"]["days_left"] is None

    fristen, fragen = await _offene_hinweise(haushalt, heute=HEUTE)
    assert fristen == []
    assert {frage.policy_id for frage in fragen} == {erste["id"], zweite["id"]}

    await check_insurance_alerts(HEUTE)

    [nachricht] = _nachrichten(captured_pushes, haushalt)
    assert nachricht["data"]["type"] == "insurance_main_due_question"
    assert nachricht["data"]["policy_count"] == 2
    assert nachricht["title"] == "Angabe fehlt"
    assert nachricht["body"] == "2 Versicherungen brauchen eine bestaetigte Angabe."
    # Keine Tageszahl: das Datum, um das es geht, ist geraten - eine Zahl
    # daneben liesse es belegt aussehen. Die 2 ist die Anzahl der Policen.
    assert "Tagen" not in nachricht["body"]

    # Und der Nachtlauf macht daraus eine Rueckfrage, keine Frist.
    ergebnis = await _lauf(haushalt)
    assert {befund.policy_id for befund in _befunde(ergebnis, "unconfirmed_main_due")} == {
        erste["id"],
        zweite["id"],
    }
    assert _befunde(ergebnis, "unconfirmed_notice_terms") == []

    aufgaben = client.get(f"{BASIS}/tasks", headers=haushalt.kopf).json()
    assert {aufgabe["kind"] for aufgabe in aufgaben} == {"unconfirmed_main_due"}


# ---------------------------------------------------------------------------
# 2. Die Staffel
# ---------------------------------------------------------------------------


async def test_die_staffel_zaehlt_auf_das_empfohlene_handlungsdatum(
    haushalt: Haushalt,
) -> None:
    """Pflichtfall 7: Vorlaufstufen 60/14/3 vor dem HANDLUNGSDATUM.

    Der Unterschied zum rechtlichen Fristende ist der ganze Punkt. Am 61. Tag
    vor dem Handlungsdatum schweigt der Lauf - obwohl das rechtliche Fristende
    dann noch deutlich weiter entfernt ist. Zaehlte die Staffel auf das
    Fristende, meldete sie sich zu spaet: bis dorthin muss die Kuendigung
    ZUGEGANGEN sein, und der Postweg braucht davor noch Werktage.

    `select_pending_alerts` statt des ganzen Laufs, weil diese Funktion nichts
    schreibt - der Stichtag ist hier frei waehlbar, ohne dass `notified_at`
    (das `now()` schreibt und damit am echten Kalender haengt) die Reihe stoert.
    """
    police = _bestaetigte_police(haushalt)
    assert police["main_due_source"] == "user_confirmed"
    assert police["deadline"]["certainty"] == "confirmed"

    frist = _frist(FERNE_HAUPTFAELLIGKEIT, heute=date(FERNE_HAUPTFAELLIGKEIT.year, 3, 1))
    handlungstag = frist.recommended_action_on
    assert handlungstag is not None
    assert frist.deadline_legal_on is not None and frist.deadline_legal_on > handlungstag

    async def _stufe(tage_vorher: int) -> list[Any]:
        fristen, _fragen = await _offene_hinweise(
            haushalt, heute=handlungstag - timedelta(days=tage_vorher)
        )
        return fristen

    # 61 Tage vorher: noch nichts - und das rechtliche Fristende ist zu diesem
    # Zeitpunkt noch weiter weg als 61 Tage.
    assert await _stufe(61) == []
    assert (frist.deadline_legal_on - (handlungstag - timedelta(days=61))).days > 61

    for tage_vorher, erwartete_stufe in ((60, 60), (14, 14), (3, 3), (0, 3)):
        [alarm] = await _stufe(tage_vorher)
        assert alarm.policy_id == police["id"]
        assert alarm.stage_days == erwartete_stufe, tage_vorher
        assert alarm.days_left == tage_vorher, tage_vorher


async def test_die_staffel_kommt_aus_den_einstellungen_des_haushalts(
    haushalt: Haushalt,
) -> None:
    """`insurance_settings.lead_days` ist die Quelle, nicht eine Konstante.

    Die Gegenprobe steht daneben: `notify_deadline = false` legt denselben
    Termin still. Ohne sie waere nicht unterscheidbar, ob die Einstellung
    gelesen oder nur zufaellig dieselbe Zahl getroffen wird.
    """
    police = _bestaetigte_police(haushalt)
    frist = _frist(FERNE_HAUPTFAELLIGKEIT, heute=date(FERNE_HAUPTFAELLIGKEIT.year, 3, 1))
    handlungstag = frist.recommended_action_on
    assert handlungstag is not None

    await _staffel(haushalt, [45, 7])

    fristen, _ = await _offene_hinweise(haushalt, heute=handlungstag - timedelta(days=60))
    assert fristen == []  # 60 Tage sind in dieser Staffel keine Stufe mehr

    [bei_45] = (await _offene_hinweise(haushalt, heute=handlungstag - timedelta(days=45)))[0]
    assert (bei_45.policy_id, bei_45.stage_days) == (police["id"], 45)

    [bei_7] = (await _offene_hinweise(haushalt, heute=handlungstag - timedelta(days=7)))[0]
    assert bei_7.stage_days == 7

    await _staffel(haushalt, [45, 7], benachrichtigen=False)
    fristen, fragen = await _offene_hinweise(
        haushalt, heute=handlungstag - timedelta(days=45)
    )
    assert (fristen, fragen) == ([], [])


async def test_eine_police_mit_vertrag_bekommt_keinen_zweiten_fristen_push(
    haushalt: Haushalt,
) -> None:
    """Der Typ schliesst nur die Luecke, die der Vertragsweg offen laesst.

    Policen MIT Vertrag werden ueber `scan_term_deadlines` ->
    `contract_term_ending` erinnert. Fiele diese Bedingung weg, bekaeme der
    Nutzer zu einer einzigen Frist zwei Nachrichten aus zwei Laeufen.
    """
    vertrag = _vertrag(haushalt, name="Nordwacht BU")
    mit_vertrag = _bestaetigte_police(haushalt, contract_id=vertrag["id"])
    # Dieselbe Sparte und dieselbe Hauptfaelligkeit: beide Policen stehen damit
    # am selben Tag in derselben Vorlaufstufe, und der Unterschied im Ergebnis
    # kann nur am Vertrag liegen.
    ohne_vertrag = _bestaetigte_police(haushalt, insurer_name="Suedstern Leben AG")

    frist = _frist(FERNE_HAUPTFAELLIGKEIT, heute=date(FERNE_HAUPTFAELLIGKEIT.year, 3, 1))
    assert frist.recommended_action_on is not None
    fristen, _ = await _offene_hinweise(
        haushalt, heute=frist.recommended_action_on - timedelta(days=14)
    )

    assert [alarm.policy_id for alarm in fristen] == [ohne_vertrag["id"]]
    assert mit_vertrag["premium"]["contract_id"] == vertrag["id"]


# ---------------------------------------------------------------------------
# 3. Der Text auf dem Sperrbildschirm
# ---------------------------------------------------------------------------


async def test_der_push_text_nennt_weder_betrag_noch_person_noch_versicherungsart(
    haushalt: Haushalt, captured_pushes: list[PushMessage]
) -> None:
    """Pflichtfall 8, geprueft am tatsaechlich erzeugten Text.

    Strenger als bei Vertraegen (Plan 7.1): "Berufsunfaehigkeit" auf einem
    Sperrbildschirm ist eine Gesundheitsangabe, die jeder im Raum mitliest -
    deshalb faellt hier ausser Betrag und Person auch die Versicherungsart weg.

    Die Police traegt absichtlich lauter Angaben, die nicht nach draussen
    duerfen: sprechender Name, Personenbezug in der Bezeichnung und ein
    Fremdbeitrag. Geprueft wird gegen die Zeichenketten dieser Police, nicht
    gegen eine Wunschliste - eine spaeter eingebaute Personalisierung faellt so
    auf, egal welches Feld sie benutzt.

    Die Staffel des Haushalts wird auf genau eine Stufe gesetzt: so faellt der
    Versand auf den heutigen Tag, und `notified_at` (das `now()` schreibt) passt
    zum Stichtag des Laufs.
    """
    police = _bestaetigte_police(
        haushalt,
        main_due_on=NAHE_HAUPTFAELLIGKEIT,
        insurer_name="Muster Lebensversicherung a.G.",
        label="BU Erika Mustermann",
        external={
            "external_premium_minor": 8000,
            "external_frequency": "monthly",
            "premium_payer": "employer",
            "premium_payer_note": "Zahlt der Arbeitgeber von Erika Mustermann",
        },
    )
    frist = _frist(NAHE_HAUPTFAELLIGKEIT, heute=HEUTE)
    assert frist.days_left is not None and frist.days_left > 0
    await _staffel(haushalt, [frist.days_left])

    await check_insurance_alerts(HEUTE)

    [nachricht] = _nachrichten(captured_pushes, haushalt)
    text_gesamt = f"{nachricht['title']} {nachricht['body']}"
    assert nachricht["title"] == "Frist laeuft ab"
    assert nachricht["body"] == f"Eine Frist laeuft in {frist.days_left} Tagen ab."

    for verboten in (
        "Muster Lebensversicherung",  # Versicherer
        "Erika",  # Person
        "Mustermann",
        "Arbeitgeber",
        "Berufsunfaehigkeit",  # Versicherungsart
        "BU ",
        "8000",  # Betrag in Minor Units
        "80,00",  # und als Anzeigebetrag
    ):
        assert verboten not in text_gesamt, verboten

    # Die einzige Zahl im Text ist der Countdown - kein Datum, kein Betrag.
    assert "".join(z for z in text_gesamt if z.isdigit()) == str(frist.days_left)

    # Der Datenteil traegt nur, was der mobile Router zum Weiterleiten braucht.
    assert nachricht["data"] == {
        "type": "insurance_deadline_ordinary",
        "policy_id": police["id"],
    }


# ---------------------------------------------------------------------------
# 4. Dedup und Unversehrtheit des Nutzerstands
# ---------------------------------------------------------------------------


async def test_zweimaliger_lauf_am_selben_tag_sendet_genau_einmal(
    haushalt: Haushalt, captured_pushes: list[PushMessage]
) -> None:
    """Pflichtfall 9. Der eigentliche Zweck von `notified_at`.

    Eine Erinnerung, die jede Nacht erneut kommt, ist schlimmer als gar keine -
    der Nutzer schaltet dann den ganzen Kanal ab. Dedup-Schluessel ist die
    ERREICHTE STUFE: `notified_at` allein wuerde die spaeteren Eskalationen
    mitverschlucken.
    """
    police = _bestaetigte_police(haushalt, main_due_on=NAHE_HAUPTFAELLIGKEIT)
    frist = _frist(NAHE_HAUPTFAELLIGKEIT, heute=HEUTE)
    assert frist.days_left is not None and frist.days_left > 0
    await _staffel(haushalt, [frist.days_left])

    await check_insurance_alerts(HEUTE)
    assert len(_nachrichten(captured_pushes, haushalt)) == 1

    await check_insurance_alerts(HEUTE)
    assert len(_nachrichten(captured_pushes, haushalt)) == 1

    zeile = await _zeile(police["id"], "notified_at")
    assert zeile["notified_at"] is not None


async def test_der_lauf_veraendert_die_version_der_police_nicht(
    haushalt: Haushalt, captured_pushes: list[PushMessage]
) -> None:
    """Pflichtfall 10. `notified_at` ist aus dem Versionstrigger ausgenommen.

    Ohne diese Ausnahme entwertete jeder naechtliche Push das ETag des Nutzers:
    sein naechstes `PATCH /v1/insurance/policies/{id}` liefe ohne jede
    inhaltliche Aenderung in ein 409, und zwar genau bei den Policen, um die er
    sich gerade kuemmert.
    """
    police = _bestaetigte_police(haushalt, main_due_on=NAHE_HAUPTFAELLIGKEIT)
    frist = _frist(NAHE_HAUPTFAELLIGKEIT, heute=HEUTE)
    assert frist.days_left is not None and frist.days_left > 0
    await _staffel(haushalt, [frist.days_left])

    vorher = await _zeile(police["id"], "version, updated_at, notified_at")
    assert vorher["notified_at"] is None
    # Das ETag, das der Nutzer in seinem geoeffneten Formular haelt - vor dem
    # Lauf gezogen, denn genau darum geht es.
    etag = client.get(f"{BASIS}/policies/{police['id']}", headers=haushalt.kopf).headers["ETag"]

    await check_insurance_alerts(HEUTE)
    assert len(_nachrichten(captured_pushes, haushalt)) == 1

    nachher = await _zeile(police["id"], "version, updated_at, notified_at")
    # Gegenprobe zuerst: der Lauf hat wirklich geschrieben.
    assert nachher["notified_at"] is not None
    assert nachher["version"] == vorher["version"]
    assert nachher["updated_at"] == vorher["updated_at"]

    # Und der Nutzer kann mit seinem alten ETag weiterhin speichern.
    geaendert = client.patch(
        f"{BASIS}/policies/{police['id']}",
        json={"label": "Nach dem Nachtlauf"},
        headers={**haushalt.kopf, "If-Match": etag},
    )
    assert geaendert.status_code == 200, geaendert.text


# ---------------------------------------------------------------------------
# 5. Der Nachtlauf meldet, er korrigiert nicht
# ---------------------------------------------------------------------------


async def test_statusdivergenz_wird_gemeldet_und_nicht_still_korrigiert(
    haushalt: Haushalt,
) -> None:
    """Pflichtfall 11 (Plan 4.16 Punkt 3).

    Police beitragsfrei gestellt, Vertrag weiter aktiv: der Beitrag zaehlt
    weiter in die Fixkosten. Welche der beiden Angaben stimmt, weiss nur der
    Nutzer - eine stille Korrektur naehme ihm die Entscheidung und veraenderte
    dabei eine Zahl, die er an anderer Stelle liest.

    Die Divergenz entsteht ueber den dafuer vorgesehenen Weg: `contract_status`
    ausdruecklich auf 'active' heisst "der Vertrag bleibt". Ohne diese Angabe
    antwortet der Endpunkt mit einer Vorschau und schreibt gar nichts.
    """
    vertrag = _vertrag(haushalt, name="Nordwacht BU")
    police = _police(
        haushalt, contract_id=vertrag["id"], main_due_on=FERNE_HAUPTFAELLIGKEIT.isoformat()
    )

    antwort = client.post(
        f"{BASIS}/policies/{police['id']}/status",
        json={"status": "paid_up", "contract_status": "active"},
        headers=haushalt.kopf,
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["applied"] is True

    vertrag_vorher = await _vertragszeile(
        vertrag["id"], "status, version, amount_minor, ends_on"
    )
    assert vertrag_vorher["status"] == "active"

    ergebnis = await _lauf(haushalt)
    [befund] = _befunde(ergebnis, "status_divergence_policy_ended")
    assert befund.policy_id == police["id"]
    assert befund.what == "Vertrag pruefen"
    assert befund.why
    # Der Befund traegt keine Bezeichnung - er landet im Protokoll, und
    # "Berufsunfaehigkeit" darin waere dieselbe Angabe, die Plan 7.1 vom
    # Sperrbildschirm fernhaelt.
    assert "Nordwacht" not in f"{befund.what} {befund.why}"
    assert _befunde(ergebnis, "status_divergence_contract_ended") == []
    assert ergebnis.repaired_count == 0

    # Und danach steht der Vertrag unveraendert da.
    assert (
        await _vertragszeile(vertrag["id"], "status, version, amount_minor, ends_on")
        == vertrag_vorher
    )
    assert (await _zeile(police["id"], "status"))["status"] == "paid_up"


async def test_ein_geloeschter_vertrag_hinterlaesst_eine_police_mit_einer_aufgabe(
    haushalt: Haushalt,
) -> None:
    """Pflichtfall 12.

    Der Fremdschluessel setzt beim Loeschen des Vertrags spaltenweise nur
    `contract_id` auf null - die Police ueberlebt. Sichtbar ist danach nichts:
    der Beitrag zaehlt lautlos nicht mehr in die Fixkosten. Genau deshalb muss
    der Nachtlauf das melden.
    """
    _vertrag(haushalt, name="Hausrat Nordwacht", frequency="yearly", amount_minor=12000)
    uebernahme = client.post(f"{BASIS}/backfill", headers=haushalt.kopf)
    assert uebernahme.status_code == 200, uebernahme.text
    policy_id = str(uebernahme.json()["policy_ids"][0])

    police = client.get(f"{BASIS}/policies/{policy_id}", headers=haushalt.kopf).json()["policy"]
    assert police["data_source"] == "from_contract"
    vertrag_id = str(police["premium"]["contract_id"])

    assert client.delete(f"/v1/contracts/{vertrag_id}", headers=haushalt.kopf).status_code == 204

    danach = client.get(f"{BASIS}/policies/{policy_id}", headers=haushalt.kopf)
    assert danach.status_code == 200, danach.text
    assert danach.json()["policy"]["premium"]["kind"] == "none"
    assert danach.json()["policy"]["premium"]["contract_id"] is None
    assert (await _zeile(policy_id, "contract_id"))["contract_id"] is None

    ergebnis = await _lauf(haushalt)
    [befund] = _befunde(ergebnis, "orphaned_contract")
    assert befund.policy_id == policy_id
    assert befund.what == "Beitrag neu zuordnen"
    assert "Fixkosten" in befund.why
    # Gemeldet, nicht repariert: der Lauf kann nicht wissen, wer jetzt zahlt.
    assert ergebnis.repaired_count == 0
    assert (await _zeile(policy_id, "contract_id"))["contract_id"] is None


async def test_der_grabstein_haengt_die_police_auf_den_zielvertrag_um(
    haushalt: Haushalt,
) -> None:
    """Schritt 1 des Laufs - die einzige Stelle, an der er schreibt.

    Ein zusammengefuehrter Vertrag bleibt als Grabstein stehen
    (`merged_into_id`) und ist aus allen Lesepfaden gefiltert. Bliebe die Police
    darauf haengen, verloere sie lautlos ihren Beitrag - dasselbe Ergebnis wie
    beim geloeschten Vertrag, nur ohne dass jemand etwas geloescht haette.
    """
    ziel = _vertrag(haushalt, name="Nordwacht BU neu", amount_minor=4599)
    # Der Quellvertrag liegt zeitlich davor - so ist die Preistreppe nach dem
    # Zusammenfuehren eindeutig geordnet und der Zielbetrag bleibt der neuere.
    quelle = _vertrag(
        haushalt,
        name="Nordwacht BU alt",
        amount_minor=3999,
        next_due_date=(HEUTE - timedelta(days=60)).isoformat(),
    )
    police = _police(
        haushalt, contract_id=quelle["id"], main_due_on=FERNE_HAUPTFAELLIGKEIT.isoformat()
    )

    zusammen = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": ziel["id"],
            "source_ids": [quelle["id"]],
            "request_id": f"merge-{uuid.uuid4().hex}",
        },
        headers=haushalt.kopf,
    )
    assert zusammen.status_code == 200, zusammen.text
    assert zusammen.json()["merged_count"] == 1
    assert (await _vertragszeile(quelle["id"], "merged_into_id"))["merged_into_id"] is not None
    # Vor dem Lauf zeigt die Police noch auf den Grabstein.
    assert str((await _zeile(police["id"], "contract_id"))["contract_id"]) == quelle["id"]

    ergebnis = await _lauf(haushalt)
    assert ergebnis.repaired_count == 1
    [befund] = _befunde(ergebnis, "merged_contract_repaired")
    assert befund.policy_id == police["id"]

    assert str((await _zeile(police["id"], "contract_id"))["contract_id"]) == ziel["id"]
    aktuell = client.get(f"{BASIS}/policies/{police['id']}", headers=haushalt.kopf).json()
    # Der Beitrag ist wieder da - und zwar am ueberlebenden Vertrag. WELCHER
    # Betrag nach dem Zusammenfuehren gilt, entscheidet die Preistreppe des
    # Vertragsmoduls (tests/test_contracts_merge.py); hier zaehlt nur, dass die
    # Police ueberhaupt wieder an einem lebenden Vertrag haengt.
    assert aktuell["policy"]["premium"]["kind"] == "contract"
    assert aktuell["policy"]["premium"]["contract_id"] == ziel["id"]
    assert aktuell["policy"]["premium"]["contract_amount_minor"] is not None

    # Zweiter Lauf: nichts mehr zu tun, und nichts wird ein zweites Mal gemeldet.
    zweiter = await _lauf(haushalt)
    assert zweiter.repaired_count == 0
    assert _befunde(zweiter, "merged_contract_repaired") == []
