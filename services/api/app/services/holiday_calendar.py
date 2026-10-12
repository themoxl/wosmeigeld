"""Feiertagskalender je Bundesland (docs/VERSICHERUNGEN-PLAN.md, P2).

Warum landesspezifisch und nicht bundesweit: Paragraph 193 BGB stellt auf den
"am Erklaerungs- oder Leistungsort staatlich anerkannten allgemeinen Feiertag"
ab. Der Leistungsort einer Kuendigung ist der Sitz des EMPFAENGERS - deshalb
haengt der Kalender an ``insurance_policies.recipient_state`` und nicht am
Wohnort des Nutzers. Ein Versicherer in Muenchen ist an Fronleichnam
geschlossen, einer in Berlin nicht; wer beide gleich behandelt, liegt bei einem
von beiden falsch.

**Drei Geltungsgrade, und der Unterschied ist keine Spitzfindigkeit.**

* ``nationwide`` gilt in allen 16 Laendern,
* ``state``     gilt im ganzen genannten Land,
* ``regional``  gilt nur in einem Teil der Gemeinden dieses Landes
  (Mariae Himmelfahrt in Bayern nur in ueberwiegend katholischen Gemeinden,
  Fronleichnam in Sachsen nur in bestimmten Gemeinden, in Thueringen nur in
  ueberwiegend katholischen).

Aus dem Bundesland allein laesst sich bei ``regional`` NICHT entscheiden, ob der
Empfaenger an diesem Tag arbeitet. Deshalb werden diese Tage in den beiden
Rechnungen bewusst VERSCHIEDEN behandelt, siehe
``insurance_deadline_service.is_business_day`` gegenueber ``is_delivery_day``:

* Fuer die Fristverlaengerung nach Paragraph 193 BGB zaehlen sie NICHT. Die Norm
  verlangt einen "allgemeinen" Feiertag; ein Tag, der nur in einem Teil der
  Gemeinden gilt, ist keiner. Wer ihn trotzdem verlaengernd ansetzt, meldet eine
  Widerrufsfrist einen Tag zu spaet - die schaedliche Richtung.
* Fuer den letzten sicheren Zugangstag zaehlen sie SEHR WOHL. Dort wird
  rueckwaerts gerechnet, und ein zu frueh angesetzter Zugangstag kostet den
  Nutzer nichts, waehrend ein zu spaeter die Frist reissen laesst.

Damit steht in beiden Rechnungen die vorsichtige Variante, ohne dass eine der
beiden die andere verbiegen muesste.

Historische Stichjahre sind mitgefuehrt (``since``), weil der Kalender auch fuer
zurueckliegende Jahre befragt wird - etwa wenn eine Frist im Nachhinein
begruendet werden soll. Der einmalige bundesweite Reformationstag 2017 (500.
Jahrestag) fehlt bewusst: er ist fuer keine laufende Frist mehr erreichbar und
stuende sonst als Sonderfall im Regelwerk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

StateCode = Literal[
    "BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV", "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH"
]

# Deckungsgleich mit dem CHECK auf insurance_policies.recipient_state.
STATE_CODES: frozenset[str] = frozenset(
    {
        "BW",
        "BY",
        "BE",
        "BB",
        "HB",
        "HH",
        "HE",
        "MV",
        "NI",
        "NW",
        "RP",
        "SL",
        "SN",
        "ST",
        "SH",
        "TH",
    }
)

HolidayScope = Literal["nationwide", "state", "regional"]


@dataclass(frozen=True)
class Holiday:
    """Ein Feiertag mit seiner Geltung und seinem Beleg.

    ``basis`` ist ein Nachweis fuer die Oberflaeche, kein Schluessel: die
    Anzeige entscheidet ueber ``scope``, nie ueber den Text.
    """

    on: date
    name: str
    scope: HolidayScope
    basis: str


@dataclass(frozen=True)
class _Rule:
    """Eine Feiertagsregel, unabhaengig vom Jahr.

    ``states`` und ``partial_states`` sind getrennt, weil sie verschieden
    wirken; ``nationwide`` ist ein eigenes Feld und nicht "states ist leer",
    damit eine vergessene Landesliste nicht versehentlich bundesweit gilt.
    """

    name: str
    basis: str
    nationwide: bool = False
    states: frozenset[str] = field(default_factory=frozenset)
    partial_states: frozenset[str] = field(default_factory=frozenset)
    # Erstes Jahr, ab dem die Regel gilt. None = seit jeher.
    since: int | None = None


# ---------------------------------------------------------------------------
# Bewegliche Tage
# ---------------------------------------------------------------------------


def easter_sunday(year: int) -> date:
    """Ostersonntag nach der anonymen gregorianischen Osterformel.

    Liegt hier und nicht im Fristenkern, weil der halbe Kalender daran haengt -
    und weil zwei Osterformeln in einem Projekt zwei Karfreitage ergeben
    koennen. ``insurance_deadline_service`` reicht diesen Namen weiter.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return date(year, month, day + 1)


def penance_day(year: int) -> date:
    """Buss- und Bettag: der Mittwoch vor dem 23. November.

    Gerechnet, nicht tabelliert - sonst waere der Kalender ab dem ersten
    ungepflegten Jahr still falsch. Der 22.11. ist der spaetestmoegliche Tag
    davor; von dort geht es auf den letzten Mittwoch zurueck.
    """
    reference = date(year, 11, 22)
    return reference - timedelta(days=(reference.weekday() - 2) % 7)


# ---------------------------------------------------------------------------
# Regelwerk
# ---------------------------------------------------------------------------

# Bundesweit. Ausser dem Tag der Deutschen Einheit stehen diese Tage in allen
# 16 Landesfeiertagsgesetzen gleichlautend; ein Bundesfeiertagsgesetz gibt es
# nicht.
_NEUJAHR = _Rule("Neujahr", "alle 16 Landesfeiertagsgesetze", nationwide=True)
_KARFREITAG = _Rule("Karfreitag", "alle 16 Landesfeiertagsgesetze", nationwide=True)
_OSTERMONTAG = _Rule("Ostermontag", "alle 16 Landesfeiertagsgesetze", nationwide=True)
_TAG_DER_ARBEIT = _Rule("Tag der Arbeit", "alle 16 Landesfeiertagsgesetze", nationwide=True)
_HIMMELFAHRT = _Rule("Christi Himmelfahrt", "alle 16 Landesfeiertagsgesetze", nationwide=True)
_PFINGSTMONTAG = _Rule("Pfingstmontag", "alle 16 Landesfeiertagsgesetze", nationwide=True)
_EINHEIT = _Rule("Tag der Deutschen Einheit", "Art. 2 Abs. 2 Einigungsvertrag", nationwide=True)
_WEIHNACHT_1 = _Rule("1. Weihnachtsfeiertag", "alle 16 Landesfeiertagsgesetze", nationwide=True)
_WEIHNACHT_2 = _Rule("2. Weihnachtsfeiertag", "alle 16 Landesfeiertagsgesetze", nationwide=True)

# Heilige Drei Koenige, 06.01. - drei Laender, in jedem im ganzen Land.
_DREIKOENIGE = _Rule(
    "Heilige Drei Koenige",
    "FeiertagsG BW § 1, BayFTG Art. 1, FeiertagsG LSA § 2",
    states=frozenset({"BW", "BY", "ST"}),
)

# Internationaler Frauentag, 08.03. - zwei Laender mit verschiedenen
# Stichjahren, deshalb zwei Regeln statt einer mit Sonderfall.
_FRAUENTAG_BE = _Rule(
    "Internationaler Frauentag",
    "Berliner FeiertagsG § 1 (seit 2019)",
    states=frozenset({"BE"}),
    since=2019,
)
_FRAUENTAG_MV = _Rule(
    "Internationaler Frauentag",
    "FeiertagsG M-V § 2 (seit 2023)",
    states=frozenset({"MV"}),
    since=2023,
)

# Ostersonntag und Pfingstsonntag sind nur in Brandenburg gesetzliche
# Feiertage. Praktisch aendern sie an der Rechnung nichts - beide sind ohnehin
# Sonntage -, sie stehen hier trotzdem, damit die Begruendung an der Police
# stimmt und nicht "Sonntag" sagt, wo "Ostersonntag" gemeint ist.
_OSTERSONNTAG = _Rule(
    "Ostersonntag", "BbgFeiertagsG § 2", states=frozenset({"BB"})
)
_PFINGSTSONNTAG = _Rule(
    "Pfingstsonntag", "BbgFeiertagsG § 2", states=frozenset({"BB"})
)

# Fronleichnam, Ostern + 60 Tage. Sechs Laender im ganzen Land; in Sachsen nur
# in bestimmten Gemeinden (sorbisches Siedlungsgebiet), in Thueringen nur in
# ueberwiegend katholischen Gemeinden - dort also 'regional'.
_FRONLEICHNAM = _Rule(
    "Fronleichnam",
    "FeiertagsG BW § 1, BayFTG Art. 1, HFeiertagsG § 1, FeiertagsG NRW § 2, "
    "LFtG RP § 2, SFeiertagsG § 2; in SN/TH nur in bestimmten Gemeinden",
    states=frozenset({"BW", "BY", "HE", "NW", "RP", "SL"}),
    partial_states=frozenset({"SN", "TH"}),
)

# Augsburger Hohes Friedensfest, 08.08. - nur im Stadtgebiet Augsburg. Steht
# hier, weil dort Versicherer sitzen und der Tag rechtlich in derselben Klasse
# liegt wie Mariae Himmelfahrt in Bayern.
_FRIEDENSFEST = _Rule(
    "Augsburger Hohes Friedensfest",
    "BayFTG Art. 1 (nur Stadtgebiet Augsburg)",
    partial_states=frozenset({"BY"}),
)

# Mariae Himmelfahrt, 15.08. - im Saarland im ganzen Land, in Bayern nur in
# Gemeinden mit ueberwiegend katholischer Bevoelkerung (BayFTG Art. 1 Satz 2).
# Das ist der Musterfall fuer 'regional': aus 'BY' allein folgt nicht, ob der
# Empfaenger arbeitet.
_MARIAE_HIMMELFAHRT = _Rule(
    "Mariae Himmelfahrt",
    "SFeiertagsG § 2; BayFTG Art. 1 nur in ueberwiegend katholischen Gemeinden",
    states=frozenset({"SL"}),
    partial_states=frozenset({"BY"}),
)

# Weltkindertag, 20.09. - allein Thueringen, seit 2019.
_WELTKINDERTAG = _Rule(
    "Weltkindertag",
    "ThuerFtG § 2 (seit 2019)",
    states=frozenset({"TH"}),
    since=2019,
)

# Reformationstag, 31.10. - zwei Gruppen mit verschiedenen Stichjahren:
# in BB, MV, SN, ST und TH seit jeher, in HB, HH, NI und SH erst seit 2018.
_REFORMATION_ALT = _Rule(
    "Reformationstag",
    "BbgFeiertagsG § 2, FeiertagsG M-V § 2, SaechsSonn- und FeiertagsG § 1, "
    "FeiertagsG LSA § 2, ThuerFtG § 2",
    states=frozenset({"BB", "MV", "SN", "ST", "TH"}),
)
_REFORMATION_NORD = _Rule(
    "Reformationstag",
    "BremFeiertagsG § 2, HmbFeiertagsG § 1, NFeiertagsG § 2, SFTG SH § 2 (seit 2018)",
    states=frozenset({"HB", "HH", "NI", "SH"}),
    since=2018,
)

# Allerheiligen, 01.11. - fuenf Laender, jeweils im ganzen Land.
_ALLERHEILIGEN = _Rule(
    "Allerheiligen",
    "FeiertagsG BW § 1, BayFTG Art. 1, FeiertagsG NRW § 2, LFtG RP § 2, SFeiertagsG § 2",
    states=frozenset({"BW", "BY", "NW", "RP", "SL"}),
)

# Buss- und Bettag - seit 1995 nur noch in Sachsen gesetzlicher Feiertag.
_BUSS_UND_BETTAG = _Rule(
    "Buss- und Bettag",
    "SaechsSonn- und FeiertagsG § 1 (Mittwoch vor dem 23.11.)",
    states=frozenset({"SN"}),
)


def _rules_for(year: int) -> tuple[tuple[date, _Rule], ...]:
    """Alle Regeln eines Jahres mit ihrem konkreten Datum."""
    easter = easter_sunday(year)
    return (
        (date(year, 1, 1), _NEUJAHR),
        (date(year, 1, 6), _DREIKOENIGE),
        (date(year, 3, 8), _FRAUENTAG_BE),
        (date(year, 3, 8), _FRAUENTAG_MV),
        (easter - timedelta(days=2), _KARFREITAG),
        (easter, _OSTERSONNTAG),
        (easter + timedelta(days=1), _OSTERMONTAG),
        (date(year, 5, 1), _TAG_DER_ARBEIT),
        (easter + timedelta(days=39), _HIMMELFAHRT),
        (easter + timedelta(days=49), _PFINGSTSONNTAG),
        (easter + timedelta(days=50), _PFINGSTMONTAG),
        (easter + timedelta(days=60), _FRONLEICHNAM),
        (date(year, 8, 8), _FRIEDENSFEST),
        (date(year, 8, 15), _MARIAE_HIMMELFAHRT),
        (date(year, 9, 20), _WELTKINDERTAG),
        (date(year, 10, 3), _EINHEIT),
        (date(year, 10, 31), _REFORMATION_ALT),
        (date(year, 10, 31), _REFORMATION_NORD),
        (date(year, 11, 1), _ALLERHEILIGEN),
        (penance_day(year), _BUSS_UND_BETTAG),
        (date(year, 12, 25), _WEIHNACHT_1),
        (date(year, 12, 26), _WEIHNACHT_2),
    )


def _scope(rule: _Rule, state: str | None) -> HolidayScope | None:
    if rule.nationwide:
        return "nationwide"
    if state is None:
        return None
    if state in rule.states:
        return "state"
    if state in rule.partial_states:
        return "regional"
    return None


def _normalise_state(state: str | None) -> str | None:
    """Unbekannte Kuerzel fallen auf den bundesweiten Kalender zurueck.

    Der CHECK auf ``insurance_policies.recipient_state`` laesst nur die 16
    gueltigen Kuerzel zu; diese Zeile faengt also nur Programmierfehler ab. Sie
    wirft bewusst nicht: eine Fristanzeige darf an einem Tippfehler im
    Landeskuerzel nicht mit einem Fehler abbrechen, sie soll den bundesweiten
    Kalender nehmen und rechnen.
    """
    if state is None:
        return None
    upper = state.upper()
    return upper if upper in STATE_CODES else None


@lru_cache(maxsize=512)
def _index(year: int, state: str | None) -> dict[date, Holiday]:
    """Feiertage eines Jahres als Nachschlagetabelle.

    Privat, weil ein zwischengespeichertes ``dict`` nach aussen veraenderbar
    waere. ``holidays_for`` gibt eine Kopie als Tupel zurueck.
    """
    found: dict[date, Holiday] = {}
    for day, rule in _rules_for(year):
        if rule.since is not None and year < rule.since:
            continue
        scope = _scope(rule, state)
        if scope is None:
            continue
        # Faellt ein bundesweiter und ein Landesfeiertag auf denselben Tag,
        # gewinnt der zuerst eingetragene - fuer die Rechnung ist der Tag so
        # oder so kein Werktag, und die Begruendung bleibt zutreffend.
        found.setdefault(day, Holiday(on=day, name=rule.name, scope=scope, basis=rule.basis))
    return found


def holidays_for(year: int, state: str | None = None) -> tuple[Holiday, ...]:
    """Alle Feiertage eines Jahres, chronologisch.

    Ohne ``state`` nur die bundesweiten - nicht "alle irgendwo geltenden": ein
    Kalender ohne Empfaengerland darf keine Landesfeiertage unterstellen.
    """
    return tuple(sorted(_index(year, _normalise_state(state)).values(), key=lambda item: item.on))


def holiday_on(day: date, state: str | None = None) -> Holiday | None:
    """Der Feiertag an diesem Tag, einschliesslich der nur regional geltenden.

    Wer die Verschiebung einer RECHTSFRIST beurteilt, muss zusaetzlich
    ``scope != 'regional'`` pruefen - dafuer gibt es ``is_recognised_holiday``.
    """
    return _index(day.year, _normalise_state(state)).get(day)


def is_recognised_holiday(day: date, state: str | None = None) -> bool:
    """Allgemeiner Feiertag im Sinne des Paragraphen 193 BGB.

    Regional beschraenkte Tage zaehlen hier NICHT: Paragraph 193 BGB verlangt
    einen "allgemeinen" Feiertag, und ein nur in einem Teil der Gemeinden
    geltender Tag ist keiner. Wer ihn hier mitzaehlte, verlaengerte eine
    Widerrufsfrist um einen Tag, den es nicht gibt.
    """
    found = holiday_on(day, state)
    return found is not None and found.scope != "regional"


def is_closed_for_delivery(day: date, state: str | None = None) -> bool:
    """Tag, an dem beim Empfaenger kein Zugang zu erwarten ist.

    Anders als ``is_recognised_holiday`` zaehlen die regional beschraenkten Tage
    hier mit. Begruendung siehe Modulkopf: ein zu frueh angesetzter Zugangstag
    kostet nichts, ein zu spaeter die Frist.
    """
    return holiday_on(day, state) is not None
