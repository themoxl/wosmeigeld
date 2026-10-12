"""Spartenkatalog und Bausteinkatalog (docs/VERSICHERUNGEN-PLAN.md, Phase P1).

Der Katalog ist Migrationsgut, kein Haushaltsdatum - und er traegt den Text,
der die Ausgangsfrage des Moduls beantwortet ("was ist das ueberhaupt fuer eine
Versicherung"). Redaktionelle Zusagen sind hier deshalb Testgegenstand wie jede
Rechenregel: ein Zwecktext, der wertet oder den Leser anspricht, ist ein Fehler
im Sinne des Plans, auch wenn er syntaktisch tadellos ist.

Die Schreibregel steht in 0001_schema.sql direkt ueber dem Seed:
  1. Der Text beschreibt die SPARTE, nie die Police des Nutzers.
  2. Keine Wertung, keine Absolutheit.
  3. Eine Pflichtaussage nur mit Fundstelle.
  4. Leistungsaussagen mit "je nach Tarif" oder "nach den vereinbarten
     Bedingungen".
  5. purpose_short ist genau ein Satz.
"""

import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

BASIS = "/v1/insurance"

# Welle K (15 Zeilen) plus Welle 2 aus P4 (18 Zeilen). Die Zahl steht hier fest
# verdrahtet, weil eine still wachsende Katalogtabelle die redaktionellen Tests
# weiter unten unbemerkt um ganze Zeilen herumfuehren wuerde.
ERWARTETE_SPARTEN = 33


@pytest.fixture()
def kopf() -> dict[str, str]:
    token = _signup(f"kat-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-8")
    haushalt = client.post(
        "/v1/households",
        json={"name": "Katalog-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": str(haushalt["id"])}


def _sparten(kopf: dict[str, str], **abfrage: str) -> list[dict[str, Any]]:
    antwort = client.get(f"{BASIS}/lines", params=abfrage, headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return [dict(zeile) for zeile in antwort.json()]


# ---------------------------------------------------------------------------
# 13. Bestand und redaktionelle Vollstaendigkeit
# ---------------------------------------------------------------------------


def test_der_katalog_traegt_beide_wellen_mit_genau_einem_platzhalter(
    kopf: dict[str, str],
) -> None:
    """Der Platzhalter ist die wichtigste Zeile des Seeds.

    Jede aus einem Vertrag uebernommene Police startet auf ihr; ohne sie waere
    die Uebernahme gezwungen, eine Sparte zu raten.
    """
    sparten = _sparten(kopf)
    assert len(sparten) == ERWARTETE_SPARTEN

    platzhalter = [zeile for zeile in sparten if zeile["is_placeholder"]]
    assert [zeile["line_key"] for zeile in platzhalter] == ["unknown"]
    # Ein Platzhalter darf keine Frist erzeugen - eine Police ohne bekannte
    # Sparte hat keinen Bezugspunkt.
    assert platzhalter[0]["default_notice_anchor"] == "none"
    assert platzhalter[0]["need_tier"] is None

    assert len({zeile["line_key"] for zeile in sparten}) == ERWARTETE_SPARTEN
    assert [zeile["sort_order"] for zeile in sparten] == sorted(
        zeile["sort_order"] for zeile in sparten
    )


def test_jede_gepflegte_sparte_traegt_bedarfsstufe_grund_und_geprueftes_datum(
    kopf: dict[str, str],
) -> None:
    """Ohne Pruefdatum veraltet eine Wertung still.

    Die vier Felder haengen zusammen: eine Bedarfsstufe ohne Begruendung waere
    eine Behauptung, eine Quelle ohne Pruefdatum eine, die niemand nachhaelt.
    """
    for zeile in _sparten(kopf):
        if zeile["is_placeholder"]:
            continue
        schluessel = zeile["line_key"]
        assert zeile["need_tier"] in {"S0", "S1", "S2", "S3", "S4"}, schluessel
        assert zeile["need_reason"], schluessel
        assert str(zeile["source_url"]).startswith("https://"), schluessel
        assert zeile["source_checked_on"], schluessel
        # Der Fristtyp entscheidet, ob Paragraph 193 BGB schiebt - er darf
        # nirgends fehlen.
        assert zeile["default_deadline_kind"], schluessel
        assert zeile["default_anchor_basis_kind"] in {"statute", "policy_terms", "contract"}


def test_der_risikofeldfilter_liefert_eine_echte_teilmenge(kopf: dict[str, str]) -> None:
    gesundheit = _sparten(kopf, risk_field="health")
    assert gesundheit
    assert {zeile["risk_field"] for zeile in gesundheit} == {"health"}
    assert len(gesundheit) < ERWARTETE_SPARTEN


def test_der_bausteinkatalog_haengt_an_den_sparten(kopf: dict[str, str]) -> None:
    """Doppelversicherung ist eine Katalogaussage, keine Sonderlogik im Code."""
    antwort = client.get(f"{BASIS}/coverage-keys", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    bausteine = antwort.json()
    assert len(bausteine) == 14

    schluessel = {zeile["line_key"] for zeile in _sparten(kopf)}
    for baustein in bausteine:
        assert baustein["purpose_short"]
        assert baustein["applies_to"]
        # Ein Baustein, der auf eine unbekannte Sparte zeigt, waere in der
        # Oberflaeche unsichtbar und im Bedarfsabgleich still wirkungslos.
        assert set(baustein["applies_to"]) <= schluessel, baustein["coverage_key"]

    gefiltert = client.get(
        f"{BASIS}/coverage-keys", params={"line_key": "private_liability"}, headers=kopf
    ).json()
    assert gefiltert
    assert {baustein["coverage_key"] for baustein in gefiltert} < {
        baustein["coverage_key"] for baustein in bausteine
    }


def test_das_spartendetail_liefert_formularschema_und_den_nenner(kopf: dict[str, str]) -> None:
    """`completeness_fields` ist der Nenner des Vollstaendigkeitsbalkens -
    ohne ihn waere "7 von 12" eine erfundene Zahl."""
    antwort = client.get(f"{BASIS}/lines/motor_liability", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    sparte = antwort.json()
    assert sparte["completeness_fields"]
    assert "main_due_on" in sparte["completeness_fields"]
    assert isinstance(sparte["attr_schema"], dict)
    assert sparte["covered_by_keys"]

    unbekannt = client.get(f"{BASIS}/lines/gibt_es_nicht", headers=kopf)
    assert unbekannt.status_code == 404, unbekannt.text
    assert unbekannt.json()["detail"] == "insurance_line_unknown"


def test_im_katalog_steht_kein_fester_kfz_stichtag(kopf: dict[str, str]) -> None:
    """Der 30.11. ist ein Rechenergebnis, keine Katalogzeile.

    Kfz laeuft ueber `term_end` plus Hauptfaelligkeit; die Vorbelegung 01-01 ist
    sichtbar als Annahme hinterlegt. Stuende hier 11-30, verschwaende die
    Annahme in einem Datum, und ein abweichendes Versicherungsjahr fiele still
    unter den Tisch.
    """
    sparten = {zeile["line_key"]: zeile for zeile in _sparten(kopf)}
    kfz = sparten["motor_liability"]
    assert kfz["default_notice_anchor"] == "term_end"
    assert kfz["default_main_due_md"] == "01-01"
    # Paragraph 11 Abs. 3 VVG: nicht weniger als ein Monat, nicht mehr als drei.
    assert kfz["default_notice_period_months"] == 1

    for zeile in sparten.values():
        assert zeile["default_main_due_md"] != "11-30", zeile["line_key"]
        if zeile["default_notice_period_months"] is not None:
            assert 1 <= zeile["default_notice_period_months"] <= 3, zeile["line_key"]


# ---------------------------------------------------------------------------
# 14. Die Schreibregel der Zwecktexte
# ---------------------------------------------------------------------------

# Wendungen, die der Katalog nicht fuehren darf. Zwei Gruppen, aus zwei
# verschiedenen Gruenden:
#
#   Wertung und Absolutheit - der Katalog beschreibt eine Sparte; er weiss
#   nichts ueber den Haushalt, der ihn liest, und darf ihm deshalb nichts
#   empfehlen. "Pflicht fuer jeden" ist der Musterfall: die Aussage ist fuer
#   keine einzige Sparte in dieser Form richtig.
#
#   Anrede - der Text spricht ueber die Sparte, nie zum Leser. Sobald er
#   "Ihre Police" sagt, behauptet er etwas ueber einen Vertrag, den er nicht
#   kennt; genau davor warnt der Vorbehalt am Zweck-Endpunkt.
VERBOTENE_WENDUNGEN = (
    "wichtigste",
    "wichtigsten",
    "beste",
    "besten",
    "bester",
    "guenstigste",
    "einzige",
    "einzigen",
    "unverzichtbar",
    "unbedingt",
    "immer",
    "nie",
    "jeder",
    "jeden",
    "jedem",
    "alle Haushalte",
    "sollte",
    "sollten",
    "empfohlen",
    "empfehlenswert",
    "lohnt",
    "Pflicht fuer jeden",
    "braucht jeder",
    "muss jeder",
    "Ihre",
    "Ihr",
    "Ihnen",
    "du",
    "dein",
    "deine",
)

# Wendungen, die eine Pflicht behaupten. Sie sind erlaubt - aber nur mit
# Fundstelle im selben Text (Schreibregel 3).
PFLICHTWENDUNGEN = (
    "pflicht zur",
    "versicherungspflicht",
    "vorgeschrieben",
    "verpflichtet",
    "muss",
)


def _zwecktexte(kopf: dict[str, str]) -> list[tuple[str, str, str]]:
    return [
        (zeile["line_key"], zeile["purpose_short"], zeile["purpose_long"])
        for zeile in _sparten(kopf)
    ]


def test_zwecktexte_werten_nicht_und_sprechen_den_leser_nicht_an(
    kopf: dict[str, str],
) -> None:
    """Die Wortliste laeuft ueber alle Zeilen, nicht ueber eine Stichprobe.

    Geprueft wird mit Wortgrenzen: "besteht" enthaelt "beste", und eine
    Teilstringpruefung machte den Test zu einem Zufallsgenerator.
    """
    treffer: list[str] = []
    for schluessel, kurz, lang in _zwecktexte(kopf):
        text = f"{kurz} {lang}"
        for wendung in VERBOTENE_WENDUNGEN:
            if re.search(rf"\b{re.escape(wendung)}\b", text, re.IGNORECASE):
                treffer.append(f"{schluessel}: '{wendung}'")
    assert treffer == []


def test_purpose_short_ist_genau_ein_satz(kopf: dict[str, str]) -> None:
    """Schreibregel 5. Der kurze Zweck steht in der Liste hinter jeder Police -
    zwei Saetze brechen dort die Zeile, und der zweite wird nie gelesen."""
    for schluessel, kurz, _lang in _zwecktexte(kopf):
        saetze = [teil for teil in re.split(r"(?<=[.!?])\s+", kurz.strip()) if teil]
        assert len(saetze) == 1, f"{schluessel}: {kurz}"
        assert kurz.strip().endswith("."), schluessel


def test_jede_pflichtaussage_nennt_ihre_fundstelle(kopf: dict[str, str]) -> None:
    """Schreibregel 3.

    "Ist vorgeschrieben" ohne Norm ist die Sorte Satz, die der Nutzer nicht
    nachpruefen kann und trotzdem glauben soll.
    """
    for schluessel, kurz, lang in _zwecktexte(kopf):
        text = f"{kurz} {lang}"
        behauptet_pflicht = any(
            re.search(rf"\b{wendung}", text, re.IGNORECASE) for wendung in PFLICHTWENDUNGEN
        )
        if behauptet_pflicht:
            assert "Paragraph" in text, schluessel


def test_der_lange_zweck_beschreibt_die_sparte_und_ist_belegbar(
    kopf: dict[str, str],
) -> None:
    """Der lange Text traegt die Erklaerung, nicht nur eine Wiederholung des
    kurzen - der Seed verlangt dafuer mindestens 40 Zeichen, gemeint sind
    Saetze."""
    for zeile in _sparten(kopf):
        schluessel = zeile["line_key"]
        assert len(zeile["purpose_short"]) >= 10, schluessel
        assert len(zeile["purpose_long"]) > len(zeile["purpose_short"]), schluessel
        if not zeile["is_placeholder"]:
            assert len(zeile["purpose_long"]) >= 40, schluessel
            assert zeile["typically_covers"], schluessel
