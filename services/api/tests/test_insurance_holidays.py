"""Feiertagskalender und Fristenregister (docs/VERSICHERUNGEN-PLAN.md, P2).

Reine Rechnung, keine Datenbank - derselbe Grund wie in
`test_insurance_deadlines.py`: ein Kalender laesst sich gegen Gesetzestext
pruefen, nicht gegen ein Bauchgefuehl. Die Faelle sind deshalb nach dem Tag und
seinem Geltungsbereich benannt, nicht nach der Funktion, die sie aufrufen.

**Worum es hier geht.** Der Kalender haengt am Bundesland des EMPFAENGERS
(`insurance_policies.recipient_state`), weil Paragraph 193 BGB auf den
Erklaerungs- oder Leistungsort abstellt. Ein Versicherer in Muenchen ist an
Fronleichnam geschlossen, einer in Berlin nicht - wer beide gleich behandelt,
liegt bei einem von beiden falsch, und zwar in der Richtung, in der die
Kuendigung zu spaet ankommt.

**Zwei Praedikate, nicht eines.** Der Kern trennt bewusst

* `is_business_day`  (VORWAERTS, Paragraph 193 BGB): nur regional geltende Tage
  zaehlen NICHT - die Norm verlangt einen "allgemeinen" Feiertag,
* `is_delivery_day`  (RUECKWAERTS, Zugang und Versandpuffer): sie zaehlen MIT -
  rueckwaerts kostet ein Tag zu frueh nichts, ein Tag zu spaet die Frist.

Ein Test, der nur eines der beiden prueft, bliebe gruen, waehrend das andere die
falsche Richtung nimmt. Deshalb steht der saechsische Fronleichnam hier
ausdruecklich mit BEIDEN Antworten in einem einzigen Fall.

Die Testdaten sind feste Kalenderjahre und nicht `date.today()`: ein
Feiertagsdatum ist kein relativer Wert, und ein am heutigen Tag gerechneter
Fall verschoebe sich jedes Jahr auf einen anderen Wochentag.
"""

from datetime import date, timedelta
from typing import Any

from app.services.holiday_calendar import (
    STATE_CODES,
    holiday_on,
    holidays_for,
    is_closed_for_delivery,
    is_recognised_holiday,
    penance_day,
)
from app.services.insurance_deadline_service import (
    DeadlineKind,
    InsuranceDeadline,
    compute_insurance_deadline,
    easter_sunday,
    is_business_day,
    is_delivery_day,
    is_public_holiday,
    shifts_for,
)

# Fronleichnam 2026: Ostersonntag 05.04. + 60 Tage = Donnerstag, 04.06.2026.
# Der Wochentag ist Teil des Falles - an einem Samstag verschoebe schon das
# Wochenende den Zugang, und der Feiertag bewiese nichts mehr.
FRONLEICHNAM_2026 = date(2026, 6, 4)
# Reformationstag 2025 faellt auf einen Freitag; 2026 waere er ein Samstag und
# damit als Beleg wertlos.
REFORMATIONSTAG_2025 = date(2025, 10, 31)
# Buss- und Bettag 2026: Mittwoch vor dem 23.11., also der 18.11.2026.
BUSS_UND_BETTAG_2026 = date(2026, 11, 18)


def _periodenende(
    ablauf: date, *, state: str | None, kind: DeadlineKind = "kuendigungsfrist"
) -> InsuranceDeadline:
    """Eine Frist, deren rechtliches Fristende genau auf `ablauf` faellt.

    Ueber `period_end` (Paragraph 168 Abs. 1 VVG) gebaut, weil dieser Anker
    keine vorgelagerte Monatsfrist kennt: das Fristende ist schlicht der Tag vor
    dem Beginn der naechsten Periode. Damit steht im Test genau der eine
    Kalendertag, um den es geht, und keine Monatsarithmetik daneben.

    `service_channel='email'` haelt den Versandpuffer bei null - sonst laegen
    zwischen dem geprueften Tag und dem empfohlenen Handlungsdatum drei weitere
    Werktage, die den Fall nur verrauschen.
    """
    return compute_insurance_deadline(
        anchor="period_end",
        deadline_kind=kind,
        today=ablauf - timedelta(days=140),
        main_due_on=ablauf + timedelta(days=1),
        main_due_source="user_confirmed",
        service_channel="email",
        recipient_state=state,
    )


# ---------------------------------------------------------------------------
# 1. Fronleichnam: derselbe Tag, zwei Antworten
# ---------------------------------------------------------------------------


def test_fronleichnam_ist_in_bayern_ein_feiertag_und_in_berlin_keiner() -> None:
    """Pflichtfall 1, erste Haelfte - die nackte Kalenderauskunft.

    Ohne diesen Unterschied gaebe es keinen Grund, das Empfaengerland
    ueberhaupt zu speichern.
    """
    assert FRONLEICHNAM_2026 == easter_sunday(2026) + timedelta(days=60)
    assert FRONLEICHNAM_2026.weekday() == 3  # Donnerstag

    assert is_public_holiday(FRONLEICHNAM_2026, "BY")
    assert not is_public_holiday(FRONLEICHNAM_2026, "BE")
    assert not is_business_day(FRONLEICHNAM_2026, "BY")
    assert is_business_day(FRONLEICHNAM_2026, "BE")


def test_fronleichnam_verschiebt_den_sicheren_zustelltag_nur_in_bayern() -> None:
    """Pflichtfall 1, zweite Haelfte - und der eigentliche Punkt.

    Eine Kuendigungsfrist wird durch einen Feiertag NICHT verlaengert (BGH
    III ZR 172/04), deshalb steht das rechtliche Fristende in beiden Laendern
    auf demselben Tag. Der Zugang aber muss AM Fristende vorliegen: wo der
    Empfaenger geschlossen hat, ist der letzte sichere Tag der davor.
    """
    bayern = _periodenende(FRONLEICHNAM_2026, state="BY")
    berlin = _periodenende(FRONLEICHNAM_2026, state="BE")

    assert bayern.deadline_legal_on == FRONLEICHNAM_2026
    assert berlin.deadline_legal_on == FRONLEICHNAM_2026

    assert bayern.safe_service_on == date(2026, 6, 3)
    assert berlin.safe_service_on == FRONLEICHNAM_2026


def test_das_register_begruendet_die_verschiebung_mit_geltung_und_norm() -> None:
    """Ohne Begruendung steht in der Oberflaeche ein Datum, das einen Tag vor
    dem Fristende liegt, und niemand kann sagen warum.

    Die Anzeige entscheidet an `scope`, nie am Freitext: 'state' heisst "im
    ganzen Land", 'regional' heisst "nur in einem Teil der Gemeinden" - und
    genau dieser Unterschied traegt weiter unten die ganze Rechnung.
    """
    bayern = _periodenende(FRONLEICHNAM_2026, state="BY")

    [verschiebung] = shifts_for(bayern, "safe_service")
    assert verschiebung.on == FRONLEICHNAM_2026
    assert verschiebung.cause == "holiday"
    assert verschiebung.label == "Fronleichnam"
    assert verschiebung.scope == "state"
    assert verschiebung.basis is not None and "BayFTG" in verschiebung.basis

    # Eine Kuendigungsfrist wird nicht geschoben - der Tag darf deshalb nicht
    # zusaetzlich unter dem rechtlichen Fristende auftauchen.
    assert shifts_for(bayern, "deadline_legal") == ()
    # Und in Berlin gibt es gar nichts zu begruenden.
    assert _periodenende(FRONLEICHNAM_2026, state="BE").shifts == ()


def test_in_sachsen_verschiebt_fronleichnam_den_zugang_aber_nicht_die_rechtsfrist() -> None:
    """Der Fall, an dem sich die beiden Praedikate trennen muessen.

    In Sachsen gilt Fronleichnam nur in bestimmten Gemeinden ('regional').

    * Fuer den ZUGANG zaehlt er mit: aus 'SN' allein folgt nicht, dass der
      Empfaenger arbeitet, und rueckwaerts gerechnet kostet ein Tag zu frueh
      nichts.
    * Fuer die Verlaengerung einer Widerrufsfrist nach Paragraph 193 BGB zaehlt
      er NICHT: die Norm verlangt einen "allgemeinen" Feiertag. Wer ihn hier
      mitzaehlte, meldete die Widerrufsfrist einen Tag zu spaet - die
      schaedliche Richtung.

    Die Gegenprobe steht daneben: in Bayern gilt derselbe Tag im ganzen Land
    und schiebt die Widerrufsfrist sehr wohl.
    """
    saechsisch = holiday_on(FRONLEICHNAM_2026, "SN")
    assert saechsisch is not None
    assert saechsisch.scope == "regional"

    # Vorwaerts (Paragraph 193 BGB): kein allgemeiner Feiertag.
    assert not is_recognised_holiday(FRONLEICHNAM_2026, "SN")
    assert is_business_day(FRONLEICHNAM_2026, "SN")
    # Rueckwaerts (Zugang): sehr wohl geschlossen.
    assert is_closed_for_delivery(FRONLEICHNAM_2026, "SN")
    assert not is_delivery_day(FRONLEICHNAM_2026, "SN")

    sachsen = _periodenende(FRONLEICHNAM_2026, state="SN", kind="widerrufsfrist")
    assert sachsen.deadline_legal_on == FRONLEICHNAM_2026
    assert sachsen.safe_service_on == date(2026, 6, 3)

    bayern = _periodenende(FRONLEICHNAM_2026, state="BY", kind="widerrufsfrist")
    assert bayern.deadline_legal_on == date(2026, 6, 5)
    assert shifts_for(bayern, "deadline_legal")[0].scope == "state"


# ---------------------------------------------------------------------------
# 2. Reformationstag
# ---------------------------------------------------------------------------


def test_reformationstag_ist_in_niedersachsen_feiertag_und_in_nordrhein_westfalen_nicht() -> None:
    """Pflichtfall 2. Der 31.10.2025 ist ein Freitag - der Unterschied liegt
    also allein am Land, nicht am Wochentag."""
    assert REFORMATIONSTAG_2025.weekday() == 4  # Freitag

    assert is_public_holiday(REFORMATIONSTAG_2025, "NI")
    assert not is_public_holiday(REFORMATIONSTAG_2025, "NW")

    niedersachsen = _periodenende(REFORMATIONSTAG_2025, state="NI")
    nordrhein = _periodenende(REFORMATIONSTAG_2025, state="NW")

    assert niedersachsen.deadline_legal_on == REFORMATIONSTAG_2025
    assert niedersachsen.safe_service_on == date(2025, 10, 30)
    assert nordrhein.safe_service_on == REFORMATIONSTAG_2025

    [grund] = shifts_for(niedersachsen, "safe_service")
    assert (grund.label, grund.scope) == ("Reformationstag", "state")


def test_der_reformationstag_im_norden_gilt_erst_seit_2018() -> None:
    """Der Kalender wird auch fuer zurueckliegende Jahre befragt - etwa, wenn
    eine Frist im Nachhinein begruendet werden soll.

    Ein Kalender ohne Stichjahr behauptete rueckwirkend einen Feiertag, den es
    damals nicht gab, und verschoebe damit einen bereits erfolgten Zugang.
    2016 statt 2017 als Gegenprobe: 2017 war der Tag einmalig bundesweit
    arbeitsfrei und traegt deshalb eine eigene Geschichte.
    """
    assert date(2016, 10, 31).weekday() == 0  # Montag
    assert not is_public_holiday(date(2016, 10, 31), "NI")
    assert is_public_holiday(date(2018, 10, 31), "NI")

    # In Sachsen gilt er seit jeher - ohne diese Gegenprobe koennte auch
    # schlicht die ganze Regel fehlen.
    assert is_public_holiday(date(2016, 10, 31), "SN")


# ---------------------------------------------------------------------------
# 3. Buss- und Bettag
# ---------------------------------------------------------------------------


def test_buss_und_bettag_ist_nur_in_sachsen_feiertag() -> None:
    """Pflichtfall 3. Seit 1995 hat ihn allein Sachsen behalten.

    Geprueft gegen ALLE 16 Laender statt gegen ein Gegenbeispiel: eine
    versehentlich zu breite Landesliste faellt sonst erst dem Nutzer auf, dessen
    Kuendigung einen Tag zu frueh abgeschickt wurde.
    """
    assert BUSS_UND_BETTAG_2026 == penance_day(2026)
    assert BUSS_UND_BETTAG_2026.weekday() == 2  # Mittwoch

    laender_mit_feiertag = {
        land for land in STATE_CODES if is_public_holiday(BUSS_UND_BETTAG_2026, land)
    }
    assert laender_mit_feiertag == {"SN"}

    sachsen = _periodenende(BUSS_UND_BETTAG_2026, state="SN")
    bayern = _periodenende(BUSS_UND_BETTAG_2026, state="BY")
    assert sachsen.safe_service_on == date(2026, 11, 17)
    assert bayern.safe_service_on == BUSS_UND_BETTAG_2026


def test_der_buss_und_bettag_wird_gerechnet_und_nicht_tabelliert() -> None:
    """Er ist der Mittwoch vor dem 23. November.

    Eine Tabelle waere ab dem ersten ungepflegten Jahr still falsch - und
    "still" ist hier das Problem: ein Fristenrechner, der einen Feiertag
    uebersieht, liefert weiterhin ein plausibel aussehendes Datum.
    """
    for jahr in range(2024, 2036):
        tag = penance_day(jahr)
        assert tag.weekday() == 2, jahr
        assert tag < date(jahr, 11, 23)
        assert (date(jahr, 11, 23) - tag).days <= 7


# ---------------------------------------------------------------------------
# 4. Ohne Empfaengerland
# ---------------------------------------------------------------------------


def test_ohne_empfaengerland_rechnet_der_kern_nur_mit_bundesweiten_tagen() -> None:
    """Pflichtfall 4. Ein Kalender ohne Land darf keines unterstellen.

    Weder "alle irgendwo geltenden Tage" (das verlaengerte jede Rechnung um
    fremde Landesfeiertage) noch ein stillschweigendes Standardland. Wo die
    Angabe fehlt, wird mit dem gerechnet, was ueberall gilt.
    """
    assert [feiertag.name for feiertag in holidays_for(2026)] == [
        "Neujahr",
        "Karfreitag",
        "Ostermontag",
        "Tag der Arbeit",
        "Christi Himmelfahrt",
        "Pfingstmontag",
        "Tag der Deutschen Einheit",
        "1. Weihnachtsfeiertag",
        "2. Weihnachtsfeiertag",
    ]
    assert all(feiertag.scope == "nationwide" for feiertag in holidays_for(2026))

    # Kein einziger der drei Landesfeiertage dieser Datei taucht ohne Land auf.
    for tag in (FRONLEICHNAM_2026, REFORMATIONSTAG_2025, BUSS_UND_BETTAG_2026):
        assert holiday_on(tag) is None
        assert not is_public_holiday(tag)
        assert is_business_day(tag)
        assert is_delivery_day(tag)

    ohne_land = _periodenende(FRONLEICHNAM_2026, state=None)
    assert ohne_land.deadline_legal_on == FRONLEICHNAM_2026
    assert ohne_land.safe_service_on == FRONLEICHNAM_2026
    assert ohne_land.shifts == ()
    # Der bundesweite Teil des Kalenders bleibt trotzdem in Kraft.
    assert not is_business_day(date(2026, 10, 3))


def test_ein_unbekanntes_landeskuerzel_faellt_auf_den_bundeskalender_zurueck() -> None:
    """Der CHECK auf `recipient_state` laesst nur die 16 Kuerzel zu; hier bleibt
    also nur der Programmierfehler.

    Er darf die Fristanzeige nicht mit einer Ausnahme abbrechen lassen - eine
    Police ohne sichtbare Frist ist schlimmer als eine ohne Landesfeiertag.
    """
    assert holiday_on(FRONLEICHNAM_2026, "XX") is None
    assert not is_public_holiday(FRONLEICHNAM_2026, "XX")
    assert is_public_holiday(date(2026, 1, 1), "XX")  # Neujahr gilt weiter
    # Kleinschreibung ist dagegen kein Fehler, sondern derselbe Kalender.
    assert is_public_holiday(FRONLEICHNAM_2026, "by")


# ---------------------------------------------------------------------------
# 5. Der Kalender darf den Bestand nicht verschieben
# ---------------------------------------------------------------------------

# Die Faelle aus `test_insurance_deadlines.py`, wortgleich uebernommen, mit
# ihrem dort festgeschriebenen Ergebnis. Sie stehen hier ein zweites Mal, weil
# P2 die Rechnung um zwei Parameter erweitert hat: waere einer von ihnen
# unbeabsichtigt wirksam, verschoebe sich der Bestand lautlos.
_REFERENZFAELLE: dict[str, tuple[dict[str, Any], date, date]] = {
    # BGH III ZR 172/04: Sonntag bleibt Sonntag, der Zugang rutscht auf Freitag.
    "kuendigungsfrist_am_sonntag": (
        {
            "anchor": "term_end",
            "deadline_kind": "kuendigungsfrist",
            "today": date(2029, 1, 10),
            "main_due_on": date(2030, 1, 1),
            "notice_period_months": 3,
            "service_channel": "email",
            "main_due_source": "user_confirmed",
        },
        date(2029, 9, 30),
        date(2029, 9, 28),
    ),
    # Plan 13.1, RF-KFZ-FRIST-1: der 30.11. ist ein Rechenergebnis.
    "kfz_kalenderjahr": (
        {
            "anchor": "term_end",
            "deadline_kind": "kuendigungsfrist",
            "today": date(2026, 8, 14),
            "main_due_on": date(2027, 1, 1),
            "notice_period_months": 1,
            "main_due_source": "user_confirmed",
        },
        date(2026, 11, 30),
        date(2026, 11, 30),
    ),
    # Plan 13.1, RF-KFZ-FRIST-5: Saisonkennzeichen, der 31.01. statt des 28.01.
    "saisonkennzeichen": (
        {
            "anchor": "term_end",
            "deadline_kind": "kuendigungsfrist",
            "today": date(2026, 8, 14),
            "main_due_on": date(2027, 3, 1),
            "notice_period_months": 1,
            "main_due_source": "user_confirmed",
        },
        date(2027, 1, 31),
        date(2027, 1, 29),
    ),
    # Moped, Paragraph 26 FZV: der Stichtag bezeichnet das ENDE der Periode.
    "moped_fixed_date": (
        {
            "anchor": "fixed_date",
            "deadline_kind": "kuendigungsfrist",
            "today": date(2026, 8, 14),
            "notice_fixed_month": 2,
            "notice_fixed_day": 28,
            "notice_period_months": 1,
            "main_due_source": "user_confirmed",
        },
        date(2027, 1, 31),
        date(2027, 1, 29),
    ),
    # Fristende an Neujahr: zugestellt wird zuletzt am 31.12.
    "fristende_an_neujahr": (
        {
            "anchor": "period_end",
            "deadline_kind": "kuendigungsfrist",
            "today": date(2026, 6, 1),
            "main_due_on": date(2027, 1, 2),
            "main_due_source": "user_confirmed",
        },
        date(2027, 1, 1),
        date(2026, 12, 31),
    ),
}


def test_die_referenzfaelle_liefern_unveraendert_ihr_altes_ergebnis() -> None:
    """Pflichtfall 5, erste Haelfte: ohne Empfaengerland aendert P2 nichts."""
    for name, (eingabe, fristende, zugang) in _REFERENZFAELLE.items():
        ergebnis = compute_insurance_deadline(**eingabe)
        assert ergebnis.deadline_legal_on == fristende, name
        assert ergebnis.safe_service_on == zugang, name
        assert ergebnis.certainty == "confirmed", name


def test_kein_bundesland_verschiebt_einen_referenzfall() -> None:
    """Pflichtfall 5, zweite Haelfte - und die schaerfere Fassung.

    Die Referenzfaelle liegen auf Tagen, an denen in keinem der 16 Laender ein
    Feiertag liegt. Genau deshalb taugen sie als Probe: ein Kalender, der
    irgendwo einen Tag zu viel fuehrt, faellt hier auf, ohne dass fuer jedes
    Land ein eigener Fall geschrieben werden muesste.
    """
    for name, (eingabe, fristende, zugang) in _REFERENZFAELLE.items():
        for land in sorted(STATE_CODES):
            ergebnis = compute_insurance_deadline(**eingabe, recipient_state=land)
            assert ergebnis.deadline_legal_on == fristende, f"{name}/{land}"
            assert ergebnis.safe_service_on == zugang, f"{name}/{land}"
            assert ergebnis.recipient_state == land, f"{name}/{land}"


# ---------------------------------------------------------------------------
# 6. Reihenfolge der Begruendungen und Stichjahre
# ---------------------------------------------------------------------------


def test_ein_feiertag_am_sonntag_wird_als_sonntag_begruendet() -> None:
    """Der Weltkindertag faellt 2026 auf einen Sonntag.

    Der wirksame Grund ist dann der Sonntag - der Feiertag verschiebt nichts
    mehr. Stuende er trotzdem im Register, erklaerte die Oberflaeche den Abstand
    mit einem Feiertag, der fuer ihn gar nichts bewirkt hat.
    """
    weltkindertag = date(2026, 9, 20)
    assert weltkindertag.weekday() == 6  # Sonntag
    assert is_public_holiday(weltkindertag, "TH")

    ergebnis = _periodenende(weltkindertag, state="TH")
    assert ergebnis.safe_service_on == date(2026, 9, 18)

    gruende = shifts_for(ergebnis, "safe_service")
    assert [grund.cause for grund in gruende] == ["sunday", "saturday"]
    assert all(grund.scope == "weekend" for grund in gruende)


def test_stichjahre_gelten_auch_fuer_frauentag_und_weltkindertag() -> None:
    """Drei Laender haben in den letzten Jahren Feiertage eingefuehrt.

    Die Stichjahre stehen hier zusammen: sie sind der einzige Teil des
    Kalenders, den ein Blick in den heutigen Feiertagsplan nicht bestaetigt.
    """
    # Internationaler Frauentag - Berlin seit 2019, Mecklenburg-Vorpommern
    # erst seit 2023. Zwei Regeln, nicht eine mit Sonderfall.
    assert not is_public_holiday(date(2018, 3, 8), "BE")
    assert is_public_holiday(date(2019, 3, 8), "BE")
    assert not is_public_holiday(date(2022, 3, 8), "MV")
    assert is_public_holiday(date(2023, 3, 8), "MV")
    assert not is_public_holiday(date(2027, 3, 8), "BB")

    # Weltkindertag - allein Thueringen, seit 2019.
    assert not is_public_holiday(date(2018, 9, 20), "TH")
    assert is_public_holiday(date(2019, 9, 20), "TH")
    assert not is_public_holiday(date(2027, 9, 20), "SN")


def test_die_beweglichen_tage_haengen_an_einer_einzigen_osterformel() -> None:
    """Zwei Osterformeln in einem Projekt ergaeben zwei Karfreitage.

    Der Kalender rechnet Karfreitag, Ostermontag, Himmelfahrt, Pfingstmontag
    und Fronleichnam aus demselben `easter_sunday`, das der Fristenkern
    weiterreicht - geprueft ueber mehrere Jahre, weil ein Formelfehler oft nur
    in einzelnen Jahren durchschlaegt.
    """
    for jahr in (2025, 2026, 2027, 2028, 2029, 2030):
        ostern = easter_sunday(jahr)
        assert ostern.weekday() == 6, jahr
        assert is_public_holiday(ostern - timedelta(days=2))  # Karfreitag
        assert is_public_holiday(ostern + timedelta(days=1))  # Ostermontag
        assert is_public_holiday(ostern + timedelta(days=39))  # Himmelfahrt
        assert is_public_holiday(ostern + timedelta(days=50))  # Pfingstmontag
        # Fronleichnam ist kein bundesweiter Tag - er haengt aber an derselben
        # Formel und muss deshalb in Bayern genau 60 Tage nach Ostern liegen.
        fronleichnam = holiday_on(ostern + timedelta(days=60), "BY")
        assert fronleichnam is not None and fronleichnam.name == "Fronleichnam", jahr
