"""Beitragsanpassungen ueber die API (docs/VERSICHERUNGEN-PLAN.md, Phase P3).

Geprueft werden die Zusagen, die dieses Modul teuer machen, wenn sie brechen:

1. **Der Fristanker ist der ZUGANG.** Paragraph 40 Abs. 1 S. 1 VVG laesst die
   Frist ab Zugang des Schreibens laufen, nicht ab Wirksamwerden der Erhoehung.
   Bei der ueblichen Ankuendigung im November zum 1. Januar liegen dazwischen
   acht Wochen - genau die Zeit, die eine Rechnung ab `effective_on` dem Nutzer
   wegnaehme.
2. **Die Fristlaenge haengt an der Sparte.** Ein Monat nach Paragraph 40 VVG,
   zwei Monate in der privaten Krankenversicherung nach Paragraph 205 Abs. 4
   VVG.
3. **Leitsatz 3: eine vereinbarte Dynamik ist keine Erhoehung** im Sinne des
   Gesetzes und erzeugt weder Fenster noch Push-Anker.
4. **Leitsatz 1: hier entsteht kein Geld.** Die Uebernahme schreibt den Betrag
   ueber die VERTRAGSSEITE fort; die Police bekommt keine zweite Betragsquelle.
   Nur wo es keinen Vertrag gibt, wandert der Informationsbetrag des fremden
   Zahlers an die Police - und auch dann nicht in die Fixkosten.

Laeuft wie alle API-Tests gegen ein echtes lokales Supabase. Wo die Fristlaenge
geprueft wird, stehen Tag und Monat fest (06.11. + ein Monat ist immer der
06.12.), das Jahr dagegen relativ zum heutigen Tag - ein fest verdrahtetes Jahr
liesse die Suite an einem Silvester rot werden.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

BASIS = "/v1/insurance"
HEUTE = date.today()


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


def _haushalt(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    haushalt = client.post(
        "/v1/households",
        json={"name": "Beitragsanpassungs-Testhaushalt"},
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


def _vertrag(kopf: dict[str, str], **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "name": "Hausrat Nordwacht",
        "amount_minor": 10000,
        "frequency": "monthly",
        # Bewusst in der Vergangenheit: `contracts` legt seine erste Preisstufe
        # auf min(heute, erste Faelligkeit). Eine Uebernahme mit rueckwirkendem
        # Wirksamkeitsdatum liefe sonst in den Schutz von `open_amount_stage`
        # gegen Stufen vor der laufenden - und das waere ein Testartefakt, kein
        # Fachverhalten.
        "next_due_date": (HEUTE - timedelta(days=120)).isoformat(),
        "contract_type": "insurance",
    }
    koerper.update(felder)
    antwort = client.post("/v1/contracts", json=koerper, headers=kopf)
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _mitteilung(kopf: dict[str, str], policy_id: str, **felder: Any) -> dict[str, Any]:
    koerper: dict[str, Any] = {
        "received_on": (HEUTE - timedelta(days=40)).isoformat(),
        "effective_on": (HEUTE - timedelta(days=10)).isoformat(),
        "old_amount_minor": 10000,
        "new_amount_minor": 11500,
        "frequency": "monthly",
    }
    koerper.update(felder)
    antwort = client.post(
        f"{BASIS}/policies/{policy_id}/premium-notices", json=koerper, headers=kopf
    )
    assert antwort.status_code == 201, antwort.text
    return dict(antwort.json())


def _bestaetige(
    kopf: dict[str, str], notice_id: str, koerper: dict[str, Any] | None = None
) -> dict[str, Any]:
    antwort = client.post(
        f"{BASIS}/premium-notices/{notice_id}/confirm", json=koerper, headers=kopf
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _verlauf(kopf: dict[str, str], policy_id: str) -> dict[str, Any]:
    antwort = client.get(f"{BASIS}/policies/{policy_id}/premium-history", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _police_lesen(kopf: dict[str, str], policy_id: str) -> dict[str, Any]:
    antwort = client.get(f"{BASIS}/policies/{policy_id}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json()["policy"])


def _vertrag_lesen(kopf: dict[str, str], contract_id: str) -> dict[str, Any]:
    antwort = client.get(f"/v1/contracts/{contract_id}", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.json())


def _treppe(kopf: dict[str, str], contract_id: str) -> list[dict[str, Any]]:
    antwort = client.get(f"/v1/contracts/{contract_id}/history", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return [dict(eintrag) for eintrag in antwort.json()]


async def _zeile(sql: str, params: dict[str, Any]) -> Any:
    """Liest direkt aus der Datenbank.

    Noetig fuer alles, was die API bewusst nicht herausgibt: die gespeicherten
    Fristdaten sind reiner Push-Dedup-Anker, `predecessor_policy_id` steht in
    keinem Antwortmodell."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        ergebnis = await session.execute(text(sql), params)
        return ergebnis.first()


# ---------------------------------------------------------------------------
# 1. Der Fristanker ist der Zugang
# ---------------------------------------------------------------------------


async def test_das_fenster_rechnet_ab_dem_zugang_nicht_ab_dem_wirksamwerden() -> None:
    """Zwei deutlich verschiedene Daten - und nur eines ist der Anker.

    Der Regelfall des Moduls: Anschreiben im November, Erhoehung zum 1. Januar.
    Ab `effective_on` gerechnet laege das Fristende am 01.02. statt am 06.12. -
    der Nutzer bekaeme fast zwei Monate geschenkt, die er nicht hat, und
    versaeumte sein Recht in dem Glauben, es noch zu haben.

    `effective_on` hat eine eigene, andere Rolle: es ist das FRUEHESTE
    Beendigungsdatum (Paragraph 40 Abs. 1 S. 1 VVG - "fruehestens jedoch zum
    Zeitpunkt des Wirksamwerdens der Erhoehung") und steht deshalb als
    `earliest_effect_on` daneben.
    """
    kopf = _haushalt("bap-anker")
    jahr = HEUTE.year + 1
    zugang = date(jahr, 11, 6)
    wirksam = date(jahr + 1, 1, 1)

    police = _police(kopf)
    mitteilung = _mitteilung(
        kopf,
        police["id"],
        received_on=zugang.isoformat(),
        effective_on=wirksam.isoformat(),
        old_amount_minor=51200,
        new_amount_minor=57400,
        frequency="yearly",
        reason="index",
    )

    fenster = mitteilung["special_termination"]
    assert fenster["state"] == "open"
    assert fenster["deadline_kind"] == "kuendigungsfrist"
    assert fenster["deadline_legal_on"] == date(jahr, 12, 6).isoformat(), (
        "Paragraphen 187 Abs. 1, 188 Abs. 2 BGB ab Zugang - der 06.11. plus ein Monat."
    )
    assert fenster["deadline_legal_on"] != date(jahr + 1, 2, 1).isoformat(), (
        "Ab effective_on gerechnet - der Fristanker ist verwechselt."
    )
    # Der Beleg nennt den Zugang, nicht das Wirksamwerden: die Oberflaeche
    # zeigt ihn dem Nutzer als Grund.
    assert f"Zugang {zugang.strftime('%d.%m.%Y')}" in fenster["basis"]
    assert "Paragraph 40 Abs. 1 S. 1 VVG" in fenster["basis"]
    assert fenster["basis_kind"] == "statute"

    # Das zweite Datum steht daneben und wird nicht mit dem Fristende verwechselt.
    assert fenster["earliest_effect_on"] == wirksam.isoformat()

    # Handlungsdatum vor sicherem Zugang vor rechtlichem Fristende - der
    # Versandpuffer laeuft rueckwaerts, er verlaengert nichts.
    assert fenster["safe_service_on"] <= fenster["deadline_legal_on"]
    assert fenster["recommended_action_on"] <= fenster["safe_service_on"]
    assert fenster["days_left"] > 0

    # Abgeleitete Zahlen: Delta in Minor Units, Quote in Basispunkten.
    assert mitteilung["delta_minor"] == 6200
    assert mitteilung["delta_bp"] == round(6200 * 10000 / 51200)
    assert mitteilung["yearly_old_minor"] == 51200
    assert mitteilung["yearly_new_minor"] == 57400

    # Und dasselbe Datum steht als Push-Anker in der Zeile - sonst meldete der
    # Nachtlauf ein anderes Fenster als die Oberflaeche.
    gespeichert = await _zeile(
        "select special_termination_until, safe_service_on, recommended_action_on, "
        "earliest_effect_on from insurance_premium_notices where id = :id",
        {"id": mitteilung["id"]},
    )
    assert gespeichert is not None
    assert gespeichert.special_termination_until == date(jahr, 12, 6)
    assert gespeichert.safe_service_on.isoformat() == fenster["safe_service_on"]
    assert gespeichert.recommended_action_on.isoformat() == fenster["recommended_action_on"]
    assert gespeichert.earliest_effect_on == wirksam


async def test_ein_korrigierter_zugang_verschiebt_auch_den_push_anker() -> None:
    """Die gespeicherten Fristdaten folgen jeder Korrektur.

    Sonst meldete der Nachtlauf ein Datum, das niemand mehr behauptet: die
    Antwort zeigt immer den frisch gerechneten Stand, der Push liest die
    gespeicherte Spalte.
    """
    kopf = _haushalt("bap-korr")
    jahr = HEUTE.year + 1
    police = _police(kopf)
    mitteilung = _mitteilung(
        kopf,
        police["id"],
        received_on=date(jahr, 11, 6).isoformat(),
        effective_on=date(jahr + 1, 1, 1).isoformat(),
    )
    assert mitteilung["special_termination"]["deadline_legal_on"] == date(jahr, 12, 6).isoformat()

    korrigiert = client.patch(
        f"{BASIS}/premium-notices/{mitteilung['id']}",
        json={"received_on": date(jahr, 11, 20).isoformat()},
        headers={**kopf, "If-Match": '"1"'},
    )
    assert korrigiert.status_code == 200, korrigiert.text
    assert (
        korrigiert.json()["special_termination"]["deadline_legal_on"]
        == date(jahr, 12, 20).isoformat()
    )

    gespeichert = await _zeile(
        "select special_termination_until from insurance_premium_notices where id = :id",
        {"id": mitteilung["id"]},
    )
    assert gespeichert is not None
    assert gespeichert.special_termination_until == date(jahr, 12, 20)


# ---------------------------------------------------------------------------
# 2. Die Fristlaenge haengt an der Sparte
# ---------------------------------------------------------------------------


def test_die_private_krankenversicherung_hat_zwei_monate_die_hausratversicherung_einen() -> None:
    """Paragraph 205 Abs. 4 VVG gegen Paragraph 40 Abs. 1 S. 1 VVG.

    Dieselbe Erhoehung, derselbe Zugang, zwei Sparten - und ein Monat
    Unterschied. Eine einheitliche Monatsfrist verkuerzte der PKV-Police ihr
    Recht um die Haelfte, und zwar unbemerkt: das Fenster saehe vollstaendig
    aus, es waere nur zu kurz.
    """
    kopf = _haushalt("bap-frist")
    jahr = HEUTE.year + 1
    zugang = date(jahr, 11, 6)
    wirksam = date(jahr + 1, 1, 1)

    hausrat = _police(kopf, line_key="household_contents", insurer_name="Nordwacht AG")
    pkv = _police(kopf, line_key="health_private", insurer_name="Barmherzige PKV AG")

    koerper: dict[str, Any] = {
        "received_on": zugang.isoformat(),
        "effective_on": wirksam.isoformat(),
        "old_amount_minor": 45000,
        "new_amount_minor": 49500,
        "frequency": "monthly",
        "reason": "demographics",
    }
    hausrat_fenster = _mitteilung(kopf, hausrat["id"], **koerper)["special_termination"]
    pkv_fenster = _mitteilung(kopf, pkv["id"], **koerper)["special_termination"]

    assert hausrat_fenster["deadline_legal_on"] == date(jahr, 12, 6).isoformat()
    assert pkv_fenster["deadline_legal_on"] == date(jahr + 1, 1, 6).isoformat()
    assert "Paragraph 40 Abs. 1 S. 1 VVG" in hausrat_fenster["basis"]
    assert "Paragraph 205 Abs. 4 VVG" in pkv_fenster["basis"]
    # Beide offen, beide mit Countdown - der Unterschied liegt nur in der Laenge.
    assert hausrat_fenster["state"] == pkv_fenster["state"] == "open"
    assert pkv_fenster["days_left"] > hausrat_fenster["days_left"]


# ---------------------------------------------------------------------------
# 3. Was kein Fenster erzeugt
# ---------------------------------------------------------------------------


async def test_eine_vereinbarte_dynamik_erzeugt_kein_fenster() -> None:
    """Leitsatz 3.

    Eine planmaessige Dynamik ist keine Erhoehung im Sinne des Paragraphen 40
    VVG. Sie darf deshalb weder ein Fenster, noch einen Countdown, noch einen
    Push-Anker erzeugen - sonst faellt der Haushalt jaehrlich auf dieselbe
    Rueckfrage herein und stumpft gegen die echten ab.

    "Kein Fenster" ist dabei ein Ergebnis, kein Leerwert: `state = 'none'`
    traegt seinen Grund in `basis` mit.
    """
    kopf = _haushalt("bap-dyn")
    police = _police(kopf, dynamic_agreed=True)
    mitteilung = _mitteilung(kopf, police["id"], old_amount_minor=10000, new_amount_minor=10300)

    fenster = mitteilung["special_termination"]
    assert fenster["state"] == "none"
    assert fenster["deadline_legal_on"] is None
    assert fenster["safe_service_on"] is None
    assert fenster["recommended_action_on"] is None
    assert fenster["days_left"] is None
    assert "Dynamik" in fenster["basis"]
    # Das Wirksamkeitsdatum bleibt trotzdem sichtbar: die Erhoehung findet
    # statt, nur das Sonderrecht entsteht nicht.
    assert fenster["earliest_effect_on"] == (HEUTE - timedelta(days=10)).isoformat()

    # Kein Push-Anker in der Zeile: der Teilindex des Nachtlaufs greift auf
    # `special_termination_until is not null`.
    gespeichert = await _zeile(
        "select special_termination_until, recommended_action_on "
        "from insurance_premium_notices where id = :id",
        {"id": mitteilung["id"]},
    )
    assert gespeichert is not None
    assert gespeichert.special_termination_until is None
    assert gespeichert.recommended_action_on is None


def test_leistungserweiterung_und_sparte_ohne_sonderrecht_erzeugen_ebenfalls_keines() -> None:
    """Zwei weitere Ausschluesse - jeder mit eigener Begruendung.

    Paragraph 40 Abs. 1 S. 1 VVG setzt eine Erhoehung OHNE entsprechende
    Leistungserweiterung voraus; eine Sparte ohne Sonderkuendigungsrecht kennt
    das Fenster gar nicht. Beide Faelle muessen sagen, WARUM - "kein Recht" und
    "nicht gerechnet" saehen sonst gleich aus.
    """
    kopf = _haushalt("bap-aus")

    mehrleistung = _mitteilung(
        kopf, _police(kopf)["id"], with_benefit_increase=True
    )["special_termination"]
    assert mehrleistung["state"] == "none"
    assert "Versicherungsschutzes" in mehrleistung["basis"]

    ohne_recht = _mitteilung(
        kopf, _police(kopf, line_key="unknown", insurer_name="Sparte offen")["id"]
    )["special_termination"]
    assert ohne_recht["state"] == "none"
    assert "Sparte" in ohne_recht["basis"]

    keine_erhoehung = _mitteilung(
        kopf,
        _police(kopf, insurer_name="Gesenkt AG")["id"],
        old_amount_minor=11500,
        new_amount_minor=10000,
    )["special_termination"]
    assert keine_erhoehung["state"] == "none"
    assert "steigt nicht" in keine_erhoehung["basis"]


def test_beim_kfz_entscheidet_der_vergleichsbeitrag_nicht_der_vorjahresbeitrag() -> None:
    """Die Erhoehung versteckt sich hinter der SF-Hochstufung.

    Massgeblich ist der auf der Beitragsrechnung ausgewiesene Vergleichsbeitrag.
    Beide Richtungen sind falsch, wenn gegen den Vorjahresbeitrag geprueft wird:
    ein bestehendes Recht verschwaende (Rechnung sinkt, liegt aber ueber dem
    Vergleichsbeitrag), oder es entstuende eines, das es nicht gibt (Rechnung
    steigt, bleibt aber darunter).
    """
    kopf = _haushalt("bap-kfz")
    zugang = (HEUTE - timedelta(days=5)).isoformat()
    wirksam = (HEUTE + timedelta(days=40)).isoformat()

    # Rechnungsbetrag SINKT gegenueber dem Vorjahr - und das Recht besteht doch.
    versteckt = _mitteilung(
        kopf,
        _police(kopf, line_key="motor_liability", insurer_name="Kfz Nord")["id"],
        received_on=zugang,
        effective_on=wirksam,
        old_amount_minor=59000,
        new_amount_minor=57400,
        comparison_amount_minor=51200,
        frequency="yearly",
        reason="sf_class",
    )["special_termination"]
    assert versteckt["state"] == "open"

    # Rechnungsbetrag STEIGT - und es besteht trotzdem keines.
    unauffaellig = _mitteilung(
        kopf,
        _police(kopf, line_key="motor_liability", insurer_name="Kfz Sued")["id"],
        received_on=zugang,
        effective_on=wirksam,
        old_amount_minor=51200,
        new_amount_minor=57400,
        comparison_amount_minor=59000,
        frequency="yearly",
        reason="sf_class",
    )["special_termination"]
    assert unauffaellig["state"] == "none"
    assert "Vergleichsbeitrag" in unauffaellig["basis"]


# ---------------------------------------------------------------------------
# 4. Die Uebernahme laeuft ueber die Vertragsseite
# ---------------------------------------------------------------------------


async def test_die_uebernahme_schreibt_den_betrag_ueber_die_vertragsseite_fort() -> None:
    """Leitsatz 1 in einem Test.

    Nach der Bestaetigung traegt der VERTRAG den neuen Betrag und eine neue
    Preisstufe; die Police bleibt ohne eigenen Beitrag. Eine zweite Betragsquelle
    im Versicherungsmodul waere genau die Zahl, die frueher oder spaeter von der
    ersten abweicht - und niemand wuesste, welche gilt.
    """
    kopf = _haushalt("bap-vertrag")
    vertrag = _vertrag(kopf)
    police = _police(kopf, contract_id=vertrag["id"])
    mitteilung = _mitteilung(kopf, police["id"])
    wirksam = HEUTE - timedelta(days=10)

    ergebnis = _bestaetige(kopf, mitteilung["id"])
    assert ergebnis["target"] == "contract"
    assert ergebnis["contract_id"] == vertrag["id"]
    assert ergebnis["amount_history_id"] is not None
    assert ergebnis["valid_from"] == wirksam.isoformat()
    assert ergebnis["amount_minor"] == 11500
    # Rueckwirkend wirksam: der Sollbetrag folgt sofort, also kein Vorbehalt.
    assert ergebnis["hint"] == ""
    assert ergebnis["notice"]["confirmed_at"] is not None
    assert ergebnis["notice"]["acknowledged_at"] is not None

    # (a) Der Vertrag traegt den neuen Betrag.
    assert _vertrag_lesen(kopf, vertrag["id"])["amount_minor"] == 11500

    # (b) ... und eine neue Preisstufe, die auf die Mitteilung zurueckgeht.
    treppe = _treppe(kopf, vertrag["id"])
    assert [stufe["amount_minor"] for stufe in treppe] == [10000, 11500]
    assert treppe[0]["valid_to"] == (wirksam - timedelta(days=1)).isoformat()
    assert treppe[1]["valid_from"] == wirksam.isoformat()
    assert treppe[1]["valid_to"] is None
    assert treppe[1]["change_reason"] == "manual"
    assert treppe[1]["delta_minor"] == 1500
    assert treppe[1]["id"] == ergebnis["amount_history_id"]

    # (c) Der Anlass steht am Vertrag, nicht nur an der Police.
    ereignisse = client.get(f"/v1/contracts/{vertrag['id']}/events", headers=kopf).json()
    preisaenderungen = [e for e in ereignisse if e["kind"] == "price_change"]
    assert len(preisaenderungen) == 1
    assert preisaenderungen[0]["payload"]["source"] == "insurance_premium_notice"
    assert preisaenderungen[0]["payload"]["new_minor"] == 11500
    assert preisaenderungen[0]["payload"]["delta_minor"] == 1500

    # (d) Die Police selbst traegt weiterhin keinen eigenen Beitrag.
    danach = _police_lesen(kopf, police["id"])
    assert danach["premium"]["kind"] == "contract"
    assert danach["premium"]["contract_amount_minor"] == 11500
    assert danach["premium"]["external_premium_minor"] is None
    assert danach["premium"]["yearly_in_fixed_costs_minor"] == 11500 * 12

    zeile = await _zeile(
        "select external_premium_minor, external_frequency from insurance_policies "
        "where id = :id",
        {"id": police["id"]},
    )
    assert zeile is not None
    assert zeile.external_premium_minor is None
    assert zeile.external_frequency is None


def test_eine_kuenftige_erhoehung_wartet_mit_dem_sollbetrag() -> None:
    """Die Preisstufe steht sofort, der Sollbetrag erst ab Wirksamkeit.

    Die im November erfasste Januar-Erhoehung zaehlte sonst zwei Monate zu frueh
    in die Fixkosten, und der Abgleichsmotor meldete jede dazwischenliegende
    Lastschrift als Abweichung. Die Antwort sagt das ausdruecklich, statt es
    still zu tun.
    """
    kopf = _haushalt("bap-kuenftig")
    vertrag = _vertrag(kopf)
    police = _police(kopf, contract_id=vertrag["id"])
    wirksam = HEUTE + timedelta(days=45)
    mitteilung = _mitteilung(
        kopf,
        police["id"],
        received_on=HEUTE.isoformat(),
        effective_on=wirksam.isoformat(),
    )

    ergebnis = _bestaetige(kopf, mitteilung["id"])
    assert ergebnis["hint"]
    assert "Wirksamkeitsdatum" in ergebnis["hint"]

    assert _vertrag_lesen(kopf, vertrag["id"])["amount_minor"] == 10000
    treppe = _treppe(kopf, vertrag["id"])
    assert [stufe["amount_minor"] for stufe in treppe] == [10000, 11500]
    assert treppe[1]["valid_from"] == wirksam.isoformat()


def test_eine_abweichende_zahlweise_wird_abgewiesen_statt_umgerechnet() -> None:
    """Ein Jahresbetrag in einer Monatsstufe waere ein stiller Faktor zwoelf.

    Umrechnen waere hier keine Hilfe, sondern eine erfundene Zahl: das
    Anschreiben nennt einen Jahresbeitrag, der Vertrag zieht monatlich ein, und
    welcher der beiden kuenftig gilt, weiss nur der Nutzer.
    """
    kopf = _haushalt("bap-rhythmus")
    vertrag = _vertrag(kopf, frequency="monthly")
    police = _police(kopf, contract_id=vertrag["id"])
    mitteilung = _mitteilung(kopf, police["id"], frequency="yearly")

    antwort = client.post(
        f"{BASIS}/premium-notices/{mitteilung['id']}/confirm", headers=kopf
    )
    assert antwort.status_code == 422, antwort.text
    detail = antwort.json()["detail"]
    assert detail["code"] == "insurance_premium_notice_frequency_mismatch"
    assert detail["notice_frequency"] == "yearly"
    assert detail["contract_frequency"] == "monthly"
    # Nichts angefasst: kein halber Schreibvorgang.
    assert _vertrag_lesen(kopf, vertrag["id"])["amount_minor"] == 10000
    assert len(_treppe(kopf, vertrag["id"])) == 1


# ---------------------------------------------------------------------------
# 5. Ohne Vertrag: nur der Fremdbetrag - und nur, wenn es einen Zahler gibt
# ---------------------------------------------------------------------------


async def test_ohne_vertrag_wird_der_fremdbetrag_fortgeschrieben_und_nur_dieser() -> None:
    """Fall (b): ein Dritter zahlt.

    Der Informationsbetrag wandert an die Police, samt Rhythmus - die
    Paarbindung `insurance_policies_external_triple` verbietet das eine ohne das
    andere. In die Fixkosten geht er ausdruecklich nicht ein, und eine
    Preisstufe entsteht nirgends: es gibt keinen Vertrag, an dem sie haengen
    koennte.
    """
    kopf = _haushalt("bap-fremd")
    police = _police(
        kopf,
        line_key="health_private",
        insurer_name="Arbeitgeberkasse",
        starts_on=(HEUTE - timedelta(days=400)).isoformat(),
        external={
            "external_premium_minor": 45000,
            "external_frequency": "monthly",
            "premium_payer": "employer",
        },
    )
    mitteilung = _mitteilung(
        kopf, police["id"], old_amount_minor=45000, new_amount_minor=47000
    )

    ergebnis = _bestaetige(kopf, mitteilung["id"])
    assert ergebnis["target"] == "external"
    assert ergebnis["contract_id"] is None
    # Keine Preisstufe: der Beitrag lebt in `contracts`, und es gibt keinen.
    assert ergebnis["amount_history_id"] is None
    assert ergebnis["amount_minor"] == 47000
    assert "Fixkosten" in ergebnis["hint"]

    danach = _police_lesen(kopf, police["id"])
    assert danach["premium"]["kind"] == "external"
    assert danach["premium"]["external_premium_minor"] == 47000
    assert danach["premium"]["external_frequency"] == "monthly"
    assert danach["premium"]["premium_payer"] == "employer"
    assert danach["premium"]["yearly_in_fixed_costs_minor"] is None

    zusammenfassung = client.get(f"{BASIS}/summary", headers=kopf).json()
    assert zusammenfassung["yearly_in_fixed_costs_minor"] == 0
    assert zusammenfassung["yearly_paid_by_others_minor"] == 47000 * 12

    # Der Verlauf einer vertragslosen Police kommt aus den Schreiben selbst -
    # und zeigt jede Aenderung genau einmal.
    verlauf = _verlauf(kopf, police["id"])
    assert [stufe["source"] for stufe in verlauf["steps"]] == ["external", "premium_notice"]
    assert [stufe["amount_minor"] for stufe in verlauf["steps"]] == [45000, 47000]
    assert verlauf["steps"][1]["notice_id"] == mitteilung["id"]


def test_ohne_vertrag_und_ohne_zahler_gibt_es_nichts_fortzuschreiben() -> None:
    """Fall (c): weder Vertrag noch fremder Zahler.

    Die Antwort ist eine Rueckfrage mit Vorschlag, kein roher Datenbankfehler -
    und vor allem entsteht hier kein Betrag an der Police. Genau das waere die
    zweite Geldquelle, die dieses Modul nicht haben darf.
    """
    kopf = _haushalt("bap-nichts")
    police = _police(kopf, insurer_name="Beitragsfreie Police")
    mitteilung = _mitteilung(kopf, police["id"])

    antwort = client.post(
        f"{BASIS}/premium-notices/{mitteilung['id']}/confirm", headers=kopf
    )
    assert antwort.status_code == 422, antwort.text
    detail = antwort.json()["detail"]
    assert detail["code"] == "insurance_premium_notice_requires_external_payer"
    assert detail["hint"]

    danach = _police_lesen(kopf, police["id"])
    assert danach["premium"]["kind"] == "none"
    assert danach["premium"]["external_premium_minor"] is None


# ---------------------------------------------------------------------------
# 6. Der Beitragsverlauf zeigt jede Aenderung genau einmal
# ---------------------------------------------------------------------------


def test_der_beitragsverlauf_zeigt_jede_aenderung_genau_einmal() -> None:
    """Vertragsstufen und bestaetigte Mitteilungen sind DIESELBE Aenderung.

    Beide Quellen naiv aneinanderzuhaengen ergaebe fuer jede uebernommene
    Anpassung zwei Stufen - und eine Steigerungsquote, die den Sprung doppelt
    zaehlt. Die Verknuepfung laeuft ueber `amount_history_id`: die Stufe traegt
    den Betrag, die Mitteilung den Grund.

    Eine erfasste, aber nicht uebernommene Anpassung steht bewusst gar nicht in
    der Treppe - ein Beitrag, den niemand bestaetigt hat, ist kein Beitrag.
    """
    kopf = _haushalt("bap-verlauf")
    vertrag = _vertrag(
        kopf, amount_minor=10000, next_due_date=(HEUTE - timedelta(days=200)).isoformat()
    )
    police = _police(kopf, contract_id=vertrag["id"])

    erste = _mitteilung(
        kopf,
        police["id"],
        received_on=(HEUTE - timedelta(days=150)).isoformat(),
        effective_on=(HEUTE - timedelta(days=120)).isoformat(),
        old_amount_minor=10000,
        new_amount_minor=11000,
        reason="claims_cost",
    )
    zweite = _mitteilung(
        kopf,
        police["id"],
        received_on=(HEUTE - timedelta(days=60)).isoformat(),
        effective_on=(HEUTE - timedelta(days=30)).isoformat(),
        old_amount_minor=11000,
        new_amount_minor=12000,
        reason="index",
    )
    offen = _mitteilung(
        kopf,
        police["id"],
        received_on=(HEUTE - timedelta(days=5)).isoformat(),
        effective_on=(HEUTE + timedelta(days=60)).isoformat(),
        old_amount_minor=12000,
        new_amount_minor=13000,
    )
    _bestaetige(kopf, erste["id"])
    _bestaetige(kopf, zweite["id"])

    verlauf = _verlauf(kopf, police["id"])
    stufen = verlauf["steps"]

    # Drei Stufen fuer drei Betraege - nicht fuenf.
    assert [stufe["amount_minor"] for stufe in stufen] == [10000, 11000, 12000]
    assert {stufe["source"] for stufe in stufen} == {"contract"}
    assert [stufe["notice_id"] for stufe in stufen] == [None, erste["id"], zweite["id"]]
    assert stufen[1]["received_on"] == (HEUTE - timedelta(days=150)).isoformat()
    assert stufen[1]["notice_reason"] == "claims_cost"
    assert stufen[1]["change_reason"] == "manual"
    assert stufen[0]["change_reason"] == "initial"

    # Jahreswerte und Steigerung rechnen auf denselben drei Stufen.
    assert [stufe["yearly_minor"] for stufe in stufen] == [120000, 132000, 144000]
    assert [stufe["yearly_delta_minor"] for stufe in stufen] == [None, 12000, 12000]
    assert verlauf["yearly_first_minor"] == 120000
    assert verlauf["yearly_current_minor"] == 144000
    assert verlauf["total_increase_bp"] == 2000

    # Die nicht uebernommene Anpassung steht daneben, nicht drin.
    assert verlauf["pending_notice_ids"] == [offen["id"]]
    assert verlauf["policy_ids"] == [police["id"]]


# ---------------------------------------------------------------------------
# 7. Gesellschaftswechsel
# ---------------------------------------------------------------------------


async def test_der_gesellschaftswechsel_nimmt_die_historie_mit() -> None:
    """Die Reihe bricht beim Wechsel nicht ab, sie bekommt einen Wechselpunkt.

    Ein Abbruch saehe aus wie ein beendeter Schutz - und die Frage "was zahle
    ich seit fuenf Jahren mehr" waere nach jedem Anbieterwechsel unbeantwortbar.
    Der Vertrag der alten Police bleibt dabei ausdruecklich unberuehrt: ihn
    still zu beenden aenderte die Fixkosten ohne Rueckfrage.
    """
    kopf = _haushalt("bap-wechsel")
    alt_vertrag = _vertrag(
        kopf, name="Hausrat Alt", next_due_date=(HEUTE - timedelta(days=300)).isoformat()
    )
    alt = _police(
        kopf,
        insurer_name="Alt AG",
        policy_number="A-4711",
        contract_id=alt_vertrag["id"],
        starts_on=(HEUTE - timedelta(days=300)).isoformat(),
    )
    mitteilung = _mitteilung(
        kopf,
        alt["id"],
        received_on=(HEUTE - timedelta(days=200)).isoformat(),
        effective_on=(HEUTE - timedelta(days=180)).isoformat(),
        old_amount_minor=10000,
        new_amount_minor=11000,
    )
    _bestaetige(kopf, mitteilung["id"])

    neu_vertrag = _vertrag(
        kopf, name="Hausrat Neu", amount_minor=9000, next_due_date=HEUTE.isoformat()
    )
    ende = HEUTE - timedelta(days=1)
    antwort = client.post(
        f"{BASIS}/policies/{alt['id']}/replace-with",
        json={
            "ends_on": ende.isoformat(),
            "new_policy": {
                "line_key": "household_contents",
                "insurer_name": "Neu AG",
                "contract_id": neu_vertrag["id"],
                "starts_on": HEUTE.isoformat(),
            },
        },
        headers=kopf,
    )
    assert antwort.status_code == 200, antwort.text
    wechsel = antwort.json()
    neu_id = wechsel["successor"]["id"]

    # Die alte Police ist beendet - mit Begruendung, nicht als Kuendigung.
    assert wechsel["predecessor"]["status"] == "ended"
    assert wechsel["predecessor"]["ends_on"] == ende.isoformat()
    assert wechsel["predecessor"]["cancellation_reason"] == "switch"
    assert wechsel["predecessor"]["cancelled_on"] is None

    # Die neue kennt ihre Vorgaengerin. Steht in keinem Antwortmodell - der
    # Verweis ist Bestandsdaten, kein Anzeigefeld.
    zeile = await _zeile(
        "select predecessor_policy_id, previous_insurer_name, previous_policy_number, "
        "prior_cover_since from insurance_policies where id = :id",
        {"id": neu_id},
    )
    assert zeile is not None
    assert str(zeile.predecessor_policy_id) == alt["id"]
    assert zeile.previous_insurer_name == "Alt AG"
    assert zeile.previous_policy_number == "A-4711"
    # Ohne Angabe erbt die Nachfolgerin den Beginn der Vorgaengerin - eine
    # abgesessene Wartezeit faengt nicht neu an.
    assert zeile.prior_cover_since == HEUTE - timedelta(days=300)

    # Der Vertrag der alten Police laeuft weiter, und die Antwort sagt es.
    assert "Hausrat Alt" in wechsel["hint"]
    assert _vertrag_lesen(kopf, alt_vertrag["id"])["status"] == "active"

    verlauf = _verlauf(kopf, neu_id)
    assert verlauf["policy_ids"] == [alt["id"], neu_id]
    stufen = verlauf["steps"]
    assert [stufe["amount_minor"] for stufe in stufen] == [10000, 11000, 9000]
    assert [stufe["insurer_name"] for stufe in stufen] == ["Alt AG", "Alt AG", "Neu AG"]
    assert stufen[1]["notice_id"] == mitteilung["id"]
    # Die Stufen der Vorgaengerin enden mit ihr, sonst ueberlappten beide.
    assert stufen[1]["valid_to"] == ende.isoformat()
    assert stufen[2]["valid_from"] == HEUTE.isoformat()
    # Der Sprung nach unten ist ein Wechsel, keine Beitragssenkung.
    assert [stufe["is_switch_point"] for stufe in stufen] == [False, False, True]

    # Wer die alte Police oeffnet, sieht denselben Verlauf.
    von_alt = _verlauf(kopf, alt["id"])
    assert von_alt["policy_ids"] == verlauf["policy_ids"]
    assert [stufe["amount_minor"] for stufe in von_alt["steps"]] == [10000, 11000, 9000]


# ---------------------------------------------------------------------------
# 8. Haushaltsgrenze
# ---------------------------------------------------------------------------


def test_fremder_haushalt_bekommt_auf_jeden_neuen_endpunkt_genau_404() -> None:
    """Eindeutig 404 - kein 403, kein 422.

    Ein abweichender Code waere ein Informationsleck: er verriete, dass es die
    Police oder die Mitteilung gibt und sie nur nicht zugaenglich ist. Eine
    Beitragsanpassung nennt Versicherer, Betrag und Anlass - hier ist der Preis
    eines Lecks hoeher als anderswo.
    """
    eigen = _haushalt("bap-a")
    vertrag = _vertrag(eigen)
    police = _police(eigen, contract_id=vertrag["id"])
    mitteilung = _mitteilung(eigen, police["id"])
    policy_id, notice_id = police["id"], mitteilung["id"]

    fremd = _haushalt("bap-b")
    neue_mitteilung: dict[str, Any] = {
        "received_on": HEUTE.isoformat(),
        "effective_on": (HEUTE + timedelta(days=30)).isoformat(),
        "old_amount_minor": 10000,
        "new_amount_minor": 12000,
        "frequency": "monthly",
    }
    # Das If-Match steht beim PATCH bewusst dabei: ohne Header antwortete er mit
    # 428, bevor er ueberhaupt nach der Mitteilung sucht - das waere kein Beleg
    # fuer die Haushaltsgrenze.
    abfragen: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = [
        ("GET", f"{BASIS}/policies/{policy_id}/premium-notices", None, {}),
        ("POST", f"{BASIS}/policies/{policy_id}/premium-notices", neue_mitteilung, {}),
        ("GET", f"{BASIS}/policies/{policy_id}/premium-history", None, {}),
        (
            "POST",
            f"{BASIS}/policies/{policy_id}/replace-with",
            {"new_policy_id": str(uuid.uuid4()), "ends_on": HEUTE.isoformat()},
            {},
        ),
        ("PATCH", f"{BASIS}/premium-notices/{notice_id}", {"reason": "index"}, {"If-Match": '"1"'}),
        ("DELETE", f"{BASIS}/premium-notices/{notice_id}", None, {}),
        ("POST", f"{BASIS}/premium-notices/{notice_id}/confirm", None, {}),
        ("POST", f"{BASIS}/premium-notices/{notice_id}/ack", None, {}),
    ]

    for methode, pfad, koerper, zusatz in abfragen:
        antwort = client.request(methode, pfad, json=koerper, headers={**fremd, **zusatz})
        assert antwort.status_code == 404, f"{methode} {pfad}: {antwort.status_code} {antwort.text}"

    # Und im eigenen Haushalt liegt alles unveraendert.
    eigene = client.get(f"{BASIS}/policies/{policy_id}/premium-notices", headers=eigen).json()
    assert len(eigene) == 1
    assert eigene[0]["version"] == 1
    assert eigene[0]["confirmed_at"] is None
    assert eigene[0]["acknowledged_at"] is None


# ---------------------------------------------------------------------------
# 9. Nebenlaeufigkeit
# ---------------------------------------------------------------------------


def test_if_match_ist_pflicht_und_ein_veralteter_stand_wird_abgewiesen() -> None:
    """Das ETag der Mitteilung ist einfach, nicht zusammengesetzt.

    Anders als bei der Police: eine parallele Betragsaenderung am Vertrag
    aendert nichts daran, was in einem Anschreiben steht. Erst die Uebernahme
    beruehrt beide Seiten, und die ist eine Handlung mit eigener Vorbedingung.
    """
    kopf = _haushalt("bap-etag")
    police = _police(kopf)

    angelegt = client.post(
        f"{BASIS}/policies/{police['id']}/premium-notices",
        json={
            "received_on": (HEUTE - timedelta(days=3)).isoformat(),
            "effective_on": (HEUTE + timedelta(days=30)).isoformat(),
            "old_amount_minor": 10000,
            "new_amount_minor": 11500,
            "frequency": "monthly",
        },
        headers=kopf,
    )
    assert angelegt.status_code == 201, angelegt.text
    notice_id = angelegt.json()["id"]
    assert angelegt.json()["version"] == 1
    assert angelegt.headers["ETag"] == '"1"'

    ohne = client.patch(
        f"{BASIS}/premium-notices/{notice_id}", json={"reason": "index"}, headers=kopf
    )
    assert ohne.status_code == 428, ohne.text
    assert ohne.json()["detail"] == "if_match_required"

    veraltet = client.patch(
        f"{BASIS}/premium-notices/{notice_id}",
        json={"reason": "index"},
        headers={**kopf, "If-Match": '"99"'},
    )
    assert veraltet.status_code == 409, veraltet.text
    assert veraltet.json()["detail"] == "version_conflict"
    # Der aktuelle Stand kommt mit, damit der Client ohne zweiten Request neu
    # aufsetzen kann.
    assert veraltet.headers["ETag"] == '"1"'

    aktuell = client.patch(
        f"{BASIS}/premium-notices/{notice_id}",
        json={"reason": "index"},
        headers={**kopf, "If-Match": 'W/"1"'},
    )
    assert aktuell.status_code == 200, aktuell.text
    assert aktuell.json()["reason"] == "index"
    assert aktuell.json()["version"] == 2
    assert aktuell.headers["ETag"] == '"2"'


def test_eine_uebernommene_mitteilung_laesst_sich_weder_aendern_noch_loeschen() -> None:
    """Ihr Betrag steht als Preisstufe im Vertragsverlauf.

    Eine Korrektur hier machte die beiden Zahlen still verschieden, ein Loeschen
    liesse die Stufe ohne ihren Beleg zurueck. Der Weg zurueck fuehrt ueber die
    Vertragsseite, wo der Betrag lebt.
    """
    kopf = _haushalt("bap-sperre")
    vertrag = _vertrag(kopf)
    police = _police(kopf, contract_id=vertrag["id"])
    mitteilung = _mitteilung(kopf, police["id"])
    ergebnis = _bestaetige(kopf, mitteilung["id"])
    version = ergebnis["notice"]["version"]

    geaendert = client.patch(
        f"{BASIS}/premium-notices/{mitteilung['id']}",
        json={"new_amount_minor": 20000},
        headers={**kopf, "If-Match": f'"{version}"'},
    )
    assert geaendert.status_code == 409, geaendert.text
    assert geaendert.json()["detail"] == "insurance_premium_notice_already_confirmed"

    geloescht = client.delete(f"{BASIS}/premium-notices/{mitteilung['id']}", headers=kopf)
    assert geloescht.status_code == 409, geloescht.text
    assert geloescht.json()["detail"] == "insurance_premium_notice_already_confirmed"

    nochmal = client.post(
        f"{BASIS}/premium-notices/{mitteilung['id']}/confirm", headers=kopf
    )
    assert nochmal.status_code == 409, nochmal.text
    assert nochmal.json()["detail"] == "insurance_premium_notice_already_confirmed"

    # Der Vertrag hat genau eine zusaetzliche Stufe bekommen, nicht zwei.
    assert len(_treppe(kopf, vertrag["id"])) == 2


def test_ohne_zugangsdatum_entsteht_eine_rueckfrage_statt_einer_frist() -> None:
    """Leitsatz 4 an der Stelle, an der er am meisten kostet.

    Ohne Zugang gibt es keinen Fristanker. Das Schreiben trotzdem mit einem
    erfundenen Datum zu speichern erzeugte einen Countdown, den niemand
    behauptet hat - und ein verstrichenes Recht, von dem der Nutzer nichts weiss.
    """
    kopf = _haushalt("bap-ohne")
    police = _police(kopf)
    antwort = client.post(
        f"{BASIS}/policies/{police['id']}/premium-notices",
        json={
            "effective_on": (HEUTE + timedelta(days=30)).isoformat(),
            "old_amount_minor": 10000,
            "new_amount_minor": 11500,
            "frequency": "monthly",
        },
        headers=kopf,
    )
    assert antwort.status_code == 422, antwort.text
    assert antwort.json()["detail"] == "insurance_notice_missing_received_on"
    assert client.get(f"{BASIS}/policies/{police['id']}/premium-notices", headers=kopf).json() == []


def test_ein_pflichtfeld_auf_null_ist_eine_rueckfrage_und_kein_serverfehler() -> None:
    """Derselbe Bedienfehler bekommt an jedem Feld dieselbe Antwort.

    `PremiumNoticeUpdate` deklariert jedes Feld als optional, damit ein PATCH
    einzelne Angaben aendern kann - dadurch ist ein uebertragenes `null` von
    "nicht gesendet" nicht mehr zu unterscheiden und kam bis in die
    Fensterrechnung durch. Dort verglich es ein Datum mit None. Fuer den Nutzer
    war das ein Serverfehler statt einer Auskunft, und an zwei der sechs Felder
    (die mit NOT NULL in der Tabelle) sah derselbe Fehler wieder anders aus.

    Die beiden Felder, die eine Mitteilung verlieren DARF, bleiben davon
    unberuehrt: `comparison_amount_minor` und `reason` stehen in `clear_fields`
    und sind nullable.
    """
    kopf = _haushalt("bap-null")
    police = _police(kopf)

    for feld in (
        "received_on",
        "effective_on",
        "old_amount_minor",
        "new_amount_minor",
        "frequency",
        "with_benefit_increase",
    ):
        mitteilung = _mitteilung(
            kopf,
            police["id"],
            reason="index",
            comparison_amount_minor=9000,
        )
        antwort = client.patch(
            f"{BASIS}/premium-notices/{mitteilung['id']}",
            json={feld: None},
            headers={**kopf, "If-Match": f'"{mitteilung["version"]}"'},
        )
        assert antwort.status_code == 422, f"{feld}: {antwort.text}"
        detail = antwort.json()["detail"]
        assert detail["code"] == "insurance_premium_notice_field_not_clearable"
        assert detail["fields"] == [feld]

        # Nichts geschrieben, nichts hochgezaehlt: das ETag des Clients gilt
        # weiter, ein zweiter Versuch braucht kein erneutes Lesen.
        unveraendert = client.get(
            f"{BASIS}/policies/{police['id']}/premium-notices", headers=kopf
        ).json()[0]
        assert unveraendert["version"] == mitteilung["version"]
        assert unveraendert["received_on"] == mitteilung["received_on"]
        assert unveraendert["effective_on"] == mitteilung["effective_on"]
        assert unveraendert["old_amount_minor"] == mitteilung["old_amount_minor"]
        assert unveraendert["new_amount_minor"] == mitteilung["new_amount_minor"]

        client.delete(f"{BASIS}/premium-notices/{mitteilung['id']}", headers=kopf)

    # Gegenprobe: die beiden nullable Felder lassen sich sehr wohl leeren.
    mitteilung = _mitteilung(kopf, police["id"], reason="index", comparison_amount_minor=9000)
    geleert = client.patch(
        f"{BASIS}/premium-notices/{mitteilung['id']}",
        json={"reason": None, "comparison_amount_minor": None},
        headers={**kopf, "If-Match": f'"{mitteilung["version"]}"'},
    )
    assert geleert.status_code == 200, geleert.text
    assert geleert.json()["reason"] is None
    assert geleert.json()["comparison_amount_minor"] is None


def test_eine_noch_nicht_begonnene_police_hoert_nicht_sie_sei_nicht_mehr_aktiv() -> None:
    """"Noch nicht" und "nicht mehr" sind fuer den Nutzer nicht dasselbe.

    Ohne Fenster zu bleiben ist bei einem laufenden Antrag richtig - es gibt
    noch keinen Beitrag, der angepasst werden koennte. Die Begruendung dazu darf
    aber nicht behaupten, die Police sei beendet: wer das auf einem Antrag
    liest, haelt seinen Versicherungsschutz fuer weg.
    """
    kopf = _haushalt("bap-status")

    for status_wert, satzteil in (
        ("draft", "noch nicht in Kraft"),
        ("applied", "noch nicht in Kraft"),
        ("ended", "nicht mehr aktiv"),
        ("paid_up", "Beitragsfrei"),
    ):
        police = _police(kopf, status=status_wert)
        mitteilung = _mitteilung(kopf, police["id"])
        fenster = mitteilung["special_termination"]
        assert fenster["state"] == "none", status_wert
        assert satzteil in fenster["basis"], f"{status_wert}: {fenster['basis']}"
        if status_wert in ("draft", "applied"):
            assert "nicht mehr" not in fenster["basis"]
