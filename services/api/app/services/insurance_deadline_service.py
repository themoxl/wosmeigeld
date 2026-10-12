"""Fristenrechnung fuer Versicherungspolicen (docs/VERSICHERUNGEN-PLAN.md, P1/P2).

Reine Rechnung ohne Datenbankzugriff, damit dieselbe Antwort in der Liste, auf
der Detailseite und spaeter im naechtlichen Lauf herauskommt - dasselbe Muster
wie `contract_service.compute_term_deadline`.

Die drei Daten, die dieses Modul liefert, sind bewusst verschieden:

* ``deadline_legal_on``      das rechtliche Fristende
* ``safe_service_on``        der letzte Tag, an dem der Zugang sicher ist
* ``recommended_action_on``  der Tag, an dem abgeschickt werden sollte

Warum drei und nicht eins: eine Kuendigung ist eine empfangsbeduerftige
Willenserklaerung und wird erst mit ZUGANG wirksam (Paragraph 130 Abs. 1 BGB).
Ein Werkzeug, das nur das rechtliche Fristende zeigt, laesst den Nutzer am
Sonntag einwerfen und die Frist reissen; eines, das nur den empfohlenen Tag
zeigt, laesst ihn glauben, danach sei alles verloren.

**Die wichtigste Regel dieses Moduls, und die am haeufigsten falsch gemachte:**
Paragraph 193 BGB schiebt ein Fristende, das auf Samstag, Sonntag oder einen
Feiertag faellt, auf den naechsten Werktag - **auf Kuendigungsfristen ist er
aber weder unmittelbar noch entsprechend anwendbar** (BGH, Urteil vom
17.02.2005 - III ZR 172/04). Grund: Paragraph 193 BGB schuetzt den
Erklaerenden, eine Kuendigungsfrist schuetzt den Empfaenger; eine Verlaengerung
kehrte die Schutzrichtung um. Endet die Kuendigungsfrist am Sonntag, ist die
Kuendigung am Montag verspaetet.

Fuer Widerrufs-, Anfechtungs-, Klage- und Zahlungsfristen gilt Paragraph 193
BGB dagegen sehr wohl. Deshalb traegt jede Police einen ``deadline_kind`` -
und deshalb hat die Spalte bewusst keinen Datenbankdefault: ein Default
``kuendigungsfrist`` waere ausgerechnet der nicht-schiebende Zweig, und eine
vergessene Widerrufsfrist rechnete still einen Tag zu frueh.

**Die zweite Regel, die hier schon einmal falsch stand:** ``main_due_on`` ist
der BEGINN des Versicherungsjahres, nicht sein Ende. Die laufende Periode endet
am Tag davor (Hauptfaelligkeit 01.01. -> Ablauf 31.12.), und die Kuendigungsfrist
zaehlt vom Beginn der naechsten Periode zurueck (siehe ``_notice_deadline``).
Wer stattdessen die Wiederkehr selbst als Ablauf nimmt, erklaert einen Zugang am
01.12. fuer rechtzeitig, obwohl die Kuendigung dann verspaetet ist - die
schaedliche Fehlerrichtung, und sie faellt nur an kurzen Monaten und an
Wochenendfaellen ueberhaupt auf.

**Neu in P2 - das Fristenregister.** Jede Verschiebung nennt ihren Grund
(``InsuranceDeadline.shifts``). Ohne das steht in der Oberflaeche ein Datum, das
zwei Tage vor dem rechtlichen Fristende liegt, und niemand kann sagen warum;
mit dem Register steht dort "Fronleichnam, in Bayern gesetzlicher Feiertag".
Der Kalender haengt am Bundesland des EMPFAENGERS
(``insurance_policies.recipient_state``), nicht am Wohnort des Nutzers -
Paragraph 193 BGB stellt auf den Erklaerungs- oder Leistungsort ab.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Literal, cast

from app.services.holiday_calendar import (
    Holiday,
    HolidayScope,
    StateCode,
    easter_sunday,
    holiday_on,
    is_recognised_holiday,
)

NoticeAnchor = Literal[
    "term_end", "main_due", "period_end", "fixed_date", "month_end", "per_event", "none"
]
DeadlineKind = Literal[
    "kuendigungsfrist", "widerrufsfrist", "anfechtungsfrist", "klagefrist", "zahlungsfrist"
]
ServiceChannel = Literal["post", "einschreiben", "email", "kuendigungsbutton", "fax"]
Certainty = Literal["confirmed", "assumed", "not_computable"]
DeadlineStep = Literal["deadline_legal", "safe_service", "recommended_action"]
ShiftCause = Literal["saturday", "sunday", "holiday"]

# ``easter_sunday`` wird hier nicht mehr gerechnet, sondern aus dem Kalender
# bezogen und weitergereicht: Bestandscode und Tests beziehen ihn von diesem
# Modul, und zwei Osterformeln in einem Projekt ergaeben zwei Karfreitage.
__all__ = [
    "Certainty",
    "DeadlineKind",
    "DeadlineShift",
    "DeadlineStep",
    "InsuranceDeadline",
    "NoticeAnchor",
    "NoticeTerms",
    "ServiceChannel",
    "ShiftCause",
    "StateCode",
    "add_months",
    "compute_insurance_deadline",
    "easter_sunday",
    "is_business_day",
    "is_delivery_day",
    "is_public_holiday",
    "minus_delivery_days",
    "next_business_day",
    "previous_delivery_day",
    "resolve_notice_terms",
    "shifts_for",
]

# Paragraph 193 BGB schiebt NUR bei diesen Fristarten. 'kuendigungsfrist' fehlt
# hier absichtlich - siehe Modulkopf.
_SHIFTING_KINDS: frozenset[str] = frozenset(
    {"widerrufsfrist", "anfechtungsfrist", "klagefrist", "zahlungsfrist"}
)

# Anker, die ihr Datum aus ``main_due_on`` beziehen. Nur bei ihnen ist die
# Herkunft der Hauptfaelligkeit eine Unsicherheitsquelle: eine Mopedpolice
# (``fixed_date``, Paragraph 26 FZV) und eine Geraeteversicherung
# (``month_end``) haben gar keine Hauptfaelligkeit, und eine ewige Rueckfrage
# "Hauptfaelligkeit bestaetigen" waere dort eine unbeantwortbare Frage.
_ANCHORS_USING_MAIN_DUE: frozenset[str] = frozenset({"term_end", "main_due", "period_end"})

# Versandpuffer bis zum sicheren Zugang, in WERKTAGEN. Post und Einschreiben
# brauchen Laufzeit; ein Kuendigungsbutton nach Paragraph 312k BGB, E-Mail und
# Fax mit Sendebericht gehen am selben Tag zu.
_CHANNEL_BUFFER_BUSINESS_DAYS: dict[str, int] = {
    "post": 3,
    "einschreiben": 3,
    "fax": 0,
    "email": 0,
    "kuendigungsbutton": 0,
}

_MAX_PERIODS = 60  # Iterationsdeckel wie in contract_service

_NOTICE_ANCHORS: frozenset[str] = frozenset(
    {"term_end", "main_due", "period_end", "fixed_date", "month_end", "per_event", "none"}
)
_DEADLINE_KINDS: frozenset[str] = frozenset(
    {"kuendigungsfrist", "widerrufsfrist", "anfechtungsfrist", "klagefrist", "zahlungsfrist"}
)
_SERVICE_CHANNELS: frozenset[str] = frozenset(
    {"post", "einschreiben", "email", "kuendigungsbutton", "fax"}
)


@dataclass(frozen=True)
class DeadlineShift:
    """Ein Tag, der bei einer Verschiebung uebersprungen wurde, mit Grund.

    Damit kann die Oberflaeche den Abstand zwischen rechtlichem Fristende und
    empfohlenem Handlungsdatum begruenden, statt ihn nur anzuzeigen. ``scope``
    traegt die Geltung des Feiertags als Wert (``nationwide``/``state``/
    ``regional``/``weekend``) - die Anzeige entscheidet nie am Freitext
    ``label``.
    """

    step: DeadlineStep
    on: date
    cause: ShiftCause
    label: str
    scope: HolidayScope | Literal["weekend"]
    basis: str | None


@dataclass(frozen=True)
class InsuranceDeadline:
    """Ergebnis der Fristenrechnung.

    ``certainty`` ist Teil des Ergebnisses und nicht der Anzeige: ein
    angenommenes Datum darf nirgends wie ein bestaetigtes aussehen, auch nicht
    in einer anderen Oberflaeche als der, fuer die es gedacht war.
    """

    anchor: NoticeAnchor
    deadline_kind: DeadlineKind
    certainty: Certainty
    # Der Termin, zu dem gekuendigt wird (Ablauf der Versicherungsperiode).
    term_ends_on: date | None
    deadline_legal_on: date | None
    safe_service_on: date | None
    recommended_action_on: date | None
    days_left: int | None
    reason: str
    # Fristenregister: Empfaengerland und die Tage, die die Verschiebung
    # verursacht haben. Nachgestellt und mit Vorgabewert, damit bestehende
    # Konstruktionen unveraendert weiterlaufen.
    recipient_state: str | None = None
    shifts: tuple[DeadlineShift, ...] = ()


@dataclass(frozen=True)
class NoticeTerms:
    """Fristangaben einer Police, wie sie ``compute_term_deadline`` hereinreicht.

    Bewusst ein eigener, flacher Typ und keine Zeile aus der Datenbank: die
    Vorschaltung in ``contract_service`` muss eine REINE Funktion bleiben, sonst
    gaebe es zwei Rechenwege fuer denselben Vertrag und damit zwei
    Kuendigungstermine auf zwei Seiten derselben Anwendung (Plan 8.1).

    Gegenueber der Skizze in Plan 8.1 traegt der Typ drei zusaetzliche Felder,
    und jedes hat einen Grund:

    * ``starts_on``          - ohne Beginn ist ``min_term_months`` unbrauchbar,
      und die Mindestbindung ist genau das, was den Mehrjahresvertrag von einem
      einjaehrigen unterscheidet.
    * ``main_due_source`` und ``notice_terms_source`` - sie werden
      durchgereicht statt in ``resolve_notice_terms`` zu verschwinden, damit die
      Vorschaltung selbst noch einmal auf ``certainty == 'confirmed'`` pruefen
      kann. Ein von Hand gebautes ``NoticeTerms`` mit einem angenommenen Datum
      kann so keinen Countdown ausloesen (Leitsatz 1).
    """

    anchor: NoticeAnchor
    anchor_basis_kind: str
    deadline_kind: DeadlineKind
    period_months: int | None
    main_due_on: date | None
    fixed_month: int | None
    fixed_day: int | None
    min_term_months: int | None
    renewal_term_months: int | None
    service_channel: ServiceChannel
    recipient_state: str | None
    starts_on: date | None = None
    main_due_source: str = "user_confirmed"
    notice_terms_source: str = "user_confirmed"


# ---------------------------------------------------------------------------
# Werktage, Zustelltage und ihre Begruendung
# ---------------------------------------------------------------------------


def is_public_holiday(day: date, state: str | None = None) -> bool:
    """Gesetzlicher Feiertag im Sinne des Paragraphen 193 BGB.

    Ohne ``state`` nur die bundesweiten Tage. Landesfeiertage haengen am
    Bundesland des EMPFAENGERS; ein Kalender ohne diese Angabe darf keines
    unterstellen. Nur regional geltende Tage (Mariae Himmelfahrt in Bayern)
    zaehlen hier NICHT mit - Begruendung im Kopf von ``holiday_calendar``.
    """
    return is_recognised_holiday(day, state)


def is_business_day(day: date, state: str | None = None) -> bool:
    """Werktag im Sinne des Paragraphen 193 BGB: kein Samstag, Sonntag, Feiertag."""
    return day.weekday() < 5 and not is_recognised_holiday(day, state)


def is_delivery_day(day: date, state: str | None = None) -> bool:
    """Tag, an dem ein Zugang beim Empfaenger zu erwarten ist.

    Strenger als ``is_business_day``: hier zaehlen auch die nur regional
    geltenden Feiertage mit. Der Unterschied ist die vorsichtige Richtung -
    rueckwaerts gerechnet kostet ein Tag zu frueh nichts, ein Tag zu spaet die
    Frist.
    """
    return day.weekday() < 5 and holiday_on(day, state) is None


def _shift_for(day: date, state: str | None, step: DeadlineStep) -> DeadlineShift:
    """Begruendung, warum an diesem Tag nicht zugestellt bzw. gerechnet wird.

    Wochenende zuerst: faellt ein Feiertag auf einen Sonntag, ist der Sonntag
    der wirksame Grund - der Feiertag verschiebt dann nichts mehr.
    """
    if day.weekday() == 5:
        return DeadlineShift(step, day, "saturday", "Samstag", "weekend", None)
    if day.weekday() == 6:
        return DeadlineShift(step, day, "sunday", "Sonntag", "weekend", None)
    found: Holiday | None = holiday_on(day, state)
    if found is None:  # pragma: no cover - nur erreichbar bei falscher Verwendung
        return DeadlineShift(step, day, "holiday", "Feiertag", "nationwide", None)
    return DeadlineShift(step, day, "holiday", found.name, found.scope, found.basis)


def next_business_day(
    day: date,
    state: str | None = None,
    *,
    step: DeadlineStep = "deadline_legal",
    log: list[DeadlineShift] | None = None,
) -> date:
    """Vorwaerts auf den naechsten Werktag - der Schub des Paragraphen 193 BGB.

    Bewusst mit ``is_business_day`` und nicht mit ``is_delivery_day``: hier wird
    eine RECHTSFRIST verlaengert. Ein nur in einem Teil der Gemeinden geltender
    Feiertag ist kein "allgemeiner" Feiertag im Sinne der Norm; wer ihn hier
    mitzaehlte, meldete eine Widerrufsfrist einen Tag zu spaet.
    """
    while not is_business_day(day, state):
        if log is not None:
            log.append(_shift_for(day, state, step))
        day += timedelta(days=1)
    return day


def previous_delivery_day(
    day: date,
    state: str | None = None,
    *,
    step: DeadlineStep = "safe_service",
    log: list[DeadlineShift] | None = None,
) -> date:
    """Rueckwaerts auf den letzten Tag mit zu erwartendem Zugang."""
    while not is_delivery_day(day, state):
        if log is not None:
            log.append(_shift_for(day, state, step))
        day -= timedelta(days=1)
    return day


def minus_delivery_days(
    day: date,
    count: int,
    state: str | None = None,
    *,
    step: DeadlineStep = "recommended_action",
    log: list[DeadlineShift] | None = None,
) -> date:
    """``count`` Zustelltage zurueck - der Versandpuffer."""
    for _ in range(count):
        day = previous_delivery_day(day - timedelta(days=1), state, step=step, log=log)
    return day


def add_months(anchor: date, months: int) -> date:
    """Monatsaddition mit Klemmung auf den Monatsletzten (31.01. + 1 = 28.02.)."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    if month == 12:
        last = 31
    else:
        last = (date(year, month + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(anchor.day, last))


def min_term_end_on(term_start: date | None, min_term_months: int | None) -> date | None:
    """Letzter Tag einer Mindestbindung, oder ``None`` wenn sie nicht feststeht.

    Steht hier und nicht bei den Aufrufern, weil die Bindung von zwei Seiten
    gelesen wird (Vertragsansicht und Versicherungsansicht). Zwei Kopien dieser
    drei Zeilen waeren zwei Kuendigungstermine fuer dieselbe Police - genau der
    Widerspruch, den die Regel "ein Fristenrechner" (Plan 8.1) verhindert.

    Ohne Beginn gibt es keine Bindung: eine Monatszahl ohne Anfang ist keine
    Angabe, aus der man ein Datum ableiten darf (Leitsatz 2).
    """
    if term_start is None or min_term_months is None:
        return None
    return add_months(term_start, min_term_months) - timedelta(days=1)


def _end_of_month(day: date) -> date:
    return add_months(date(day.year, day.month, 1), 1) - timedelta(days=1)


def _clamp_to_month(year: int, month: int, day: int) -> date:
    """Tag im Monat, geklemmt auf den Monatsletzten.

    Geklemmt wird nur, wo der Tag im Zielmonat nicht existiert (29.02. in einem
    Nicht-Schaltjahr, 31. in einem 30-Tage-Monat). Ein 30. bleibt der 30. - ihn
    pauschal auf den Monatsletzten zu ziehen, verschoebe die Periode um einen
    Tag und damit den Kuendigungstermin.
    """
    return date(year, month, min(day, _end_of_month(date(year, month, 1)).day))


def _next_period_start(month_day: date, today: date) -> date:
    """Naechster Beginn einer Versicherungsperiode ab heute.

    Strikt nach heute: faellt der Beginn auf heute, laeuft die neue Periode
    bereits, und die naechste Gelegenheit zu kuendigen liegt ein Jahr weiter.
    """
    candidate = _clamp_to_month(today.year, month_day.month, month_day.day)
    if candidate <= today:
        candidate = _clamp_to_month(today.year + 1, month_day.month, month_day.day)
    return candidate


def _notice_deadline(period_starts_on: date, months: int) -> date:
    """Letzter Tag, an dem die Kuendigung noch rechtzeitig zugeht.

    Gerechnet wird vom BEGINN der naechsten Periode zurueck und dann ein Tag
    abgezogen - nicht vom Periodenende aus. Der Unterschied ist keine
    Spitzfindigkeit, sondern schlaegt bei kurzen Monaten durch: eine
    Hauptfaelligkeit am 01.03. ergibt so den 31.01. (Plan 13.1,
    Saisonkennzeichen), waehrend die Rechnung ab dem Periodenende (28.02.) den
    28.01. lieferte und dem Nutzer drei Tage seiner Frist naehme. Umgekehrt
    stimmen beide Wege beim Kalenderjahr ueberein (01.01. -> 30.11.), weshalb
    der Fehler nur an den kurzen Monaten sichtbar wird.
    """
    return add_months(period_starts_on, -months) - timedelta(days=1)


def compute_insurance_deadline(
    *,
    anchor: NoticeAnchor,
    deadline_kind: DeadlineKind,
    today: date,
    main_due_on: date | None = None,
    notice_period_months: int | None = None,
    notice_fixed_month: int | None = None,
    notice_fixed_day: int | None = None,
    service_channel: ServiceChannel = "post",
    main_due_source: str = "not_computable",
    notice_terms_source: str = "catalog_suggested",
    status: str = "active",
    ends_on: date | None = None,
    recipient_state: str | None = None,
    min_term_ends_on: date | None = None,
) -> InsuranceDeadline:
    """Rechnet die drei Fristdaten einer Police.

    Gibt IMMER ein Ergebnis zurueck, nie ``None``: "nicht berechenbar" ist eine
    Aussage, die die Oberflaeche anzeigen soll, kein fehlender Wert. Genau hier
    entstand im Entwurf der erfundene Termin - ein Rechner, der bei fehlender
    Angabe irgendetwas liefert, ist schlimmer als einer, der schweigt.

    ``recipient_state`` ist das Bundesland des EMPFAENGERS und steuert den
    Feiertagskalender; ``min_term_ends_on`` ist der letzte Tag einer
    Mindestbindung, vor dem keine Kuendigung wirksam werden kann (Paragraph 11
    Abs. 4 VVG beim Mehrjahresvertrag, Paragraph 175 Abs. 4 SGB V bei der GKV).
    Beide sind optional; ohne sie rechnet die Funktion wie vor P2.
    """
    not_computable = InsuranceDeadline(
        anchor=anchor,
        deadline_kind=deadline_kind,
        certainty="not_computable",
        term_ends_on=None,
        deadline_legal_on=None,
        safe_service_on=None,
        recommended_action_on=None,
        days_left=None,
        reason="",
        recipient_state=recipient_state,
    )

    if status not in ("active", "suspended", "dormant", "paid_up"):
        return replace(not_computable, reason="Police ist nicht mehr aktiv.")
    if anchor in ("none", "per_event"):
        return replace(
            not_computable,
            reason=(
                "Fuer diese Sparte gibt es keine ordentliche Kuendigungsfrist."
                if anchor == "per_event"
                else "Kein Fristanker hinterlegt - Angabe fehlt."
            ),
        )

    # --- Periodengrenze bestimmen -----------------------------------------
    # Gerechnet wird ueber den BEGINN der naechsten Periode, nicht ueber ihr
    # Ende. Beide liegen einen Tag auseinander, und dieser eine Tag entscheidet
    # ueber ein weiteres Vertragsjahr: die laufende Periode endet am Tag VOR
    # der naechsten Wiederkehr (Plan 12.2 - "term_end rechnet ab der
    # Hauptfaelligkeit, also ab dem Beginn des Versicherungsjahres").
    if anchor in ("term_end", "main_due", "period_end"):
        if main_due_on is None:
            return replace(not_computable, reason="Hauptfaelligkeit fehlt.")
        period_starts_on = _next_period_start(main_due_on, today)
    elif anchor == "fixed_date":
        if notice_fixed_month is None or notice_fixed_day is None:
            return replace(not_computable, reason="Fester Termin fehlt.")
        # Der Kalenderfixpunkt bezeichnet hier das ENDE des Versicherungsjahres
        # (Moped: 28.02., Paragraph 26 FZV) - der Beginn ist der Tag danach.
        period_starts_on = _next_period_start(
            date(today.year, notice_fixed_month, notice_fixed_day), today
        ) + timedelta(days=1)
    elif anchor == "month_end":
        # Rollierend zum Monatsende, Frist in Monaten (GKV: Paragraph 175
        # Abs. 4 SGB V). Der Ablauftermin ist das Ende des Monats, das nach
        # Ablauf der Frist erreichbar ist.
        if notice_period_months is None:
            # Ohne Fristlaenge waere der naechste erreichbare Monatsletzte
            # geraten. Frueher stand hier ein `or 0`, das still den laufenden
            # Monat als Termin auswies.
            return replace(not_computable, reason="Kuendigungsfrist fehlt.")
        first_of_month = date(today.year, today.month, 1)
        period_starts_on = add_months(first_of_month, notice_period_months + 1)
    else:  # pragma: no cover - vom Literal ausgeschlossen
        return replace(not_computable, reason="Unbekannter Fristanker.")

    if anchor not in ("period_end", "month_end") and notice_period_months is None:
        return replace(not_computable, reason="Kuendigungsfrist fehlt.")

    term_ends_on = period_starts_on - timedelta(days=1)

    # --- Rechtliches Fristende --------------------------------------------
    def _legal_deadline(period_start: date) -> date:
        if anchor == "period_end":
            # Paragraph 168 Abs. 1 VVG: jederzeit zum Schluss der laufenden
            # Versicherungsperiode. Es gibt keine vorgelagerte Frist, die
            # verstreichen koennte - die Erklaerung muss nur bis dahin zugehen.
            return period_start - timedelta(days=1)
        if anchor == "month_end":
            # Erklaert werden muss bis zum Ende des laufenden Monats; wirksam
            # wird die Kuendigung zum oben gerechneten Ablauftermin.
            return _end_of_month(today)
        return _notice_deadline(period_start, notice_period_months or 0)

    deadline_legal_on = _legal_deadline(period_starts_on)

    # Schrittweite der Wiederkehr. Bei 'month_end' rollt der erreichbare Termin
    # monatlich weiter, bei allen anderen Ankern jaehrlich - mit 12 Monaten fuer
    # beide uebersprunge die Mindestbindungsschleife elf erreichbare Termine.
    step_months = 1 if anchor == "month_end" else 12

    # Der Deckel begrenzt JAHRE, nicht Schritte: bei monatlicher Schrittweite
    # braucht eine lange Mindestbindung entsprechend mehr Runden, und ein
    # gemeinsamer Zahlendeckel schnitte ausgerechnet dort ab.
    max_steps = _MAX_PERIODS * (12 // step_months)

    periods = 0
    while periods < max_steps and (
        deadline_legal_on < today
        # Mindestbindung: vor ihrem Ablauf kann keine Kuendigung wirksam werden
        # (Mehrjahresvertrag Paragraph 11 Abs. 4 VVG, GKV-Bindung Paragraph 175
        # Abs. 4 SGB V). Ohne diese Bedingung nennte das Werkzeug einen Termin,
        # den der Versicherer zurueckweisen wird.
        or (min_term_ends_on is not None and term_ends_on < min_term_ends_on)
    ):
        period_starts_on = add_months(period_starts_on, step_months)
        term_ends_on = period_starts_on - timedelta(days=1)
        deadline_legal_on = _legal_deadline(period_starts_on)
        periods += 1

    if deadline_legal_on < today or (
        min_term_ends_on is not None and term_ends_on < min_term_ends_on
    ):
        # Der Deckel hat gegriffen. Lieber schweigen als einen Termin nennen,
        # der die Mindestbindung verletzt oder in der Vergangenheit liegt - das
        # waere genau der erfundene Termin, den Leitsatz 2 verbietet.
        return replace(not_computable, reason="Frist liegt ausserhalb des Rechenbereichs.")

    if ends_on is not None and ends_on < term_ends_on:
        # Ein eingetragenes Enddatum ist eine Nutzerangabe und schlaegt die
        # Rechnung - dieselbe Haltung wie bei Vertraegen.
        term_ends_on = ends_on

    shifts: list[DeadlineShift] = []
    state = recipient_state

    # --- Paragraph 193 BGB, aber nur wo er gilt ---------------------------
    if deadline_kind in _SHIFTING_KINDS:
        deadline_legal_on = next_business_day(
            deadline_legal_on, state, step="deadline_legal", log=shifts
        )

    # --- Sicherer Zugang und empfohlenes Handlungsdatum -------------------
    # Der Zugang muss AM Fristende vorliegen. Faellt es auf einen Tag, an dem
    # nicht zugestellt wird, ist der letzte sichere Tag davor - und bei einer
    # Kuendigungsfrist verlaengert sich dadurch nichts (BGH III ZR 172/04).
    safe_service_on = previous_delivery_day(
        deadline_legal_on, state, step="safe_service", log=shifts
    )
    buffer_days = _CHANNEL_BUFFER_BUSINESS_DAYS.get(service_channel, 3)
    recommended_action_on = (
        minus_delivery_days(
            safe_service_on, buffer_days, state, step="recommended_action", log=shifts
        )
        if buffer_days
        else safe_service_on
    )

    # --- Sicherheit des Ergebnisses ---------------------------------------
    # Bestaetigt ist die Frist nur, wenn BEIDE Unsicherheitsquellen ausgeraeumt
    # sind: das Ankerdatum und die Fristlaenge. 'derived_from_days' ist die
    # Umrechnung aus contracts.cancellation_period_days und bleibt eine
    # Schaetzung, auch wenn der Nutzer die Hauptfaelligkeit bestaetigt hat.
    # Ob das Ankerdatum ueberhaupt eine Unsicherheitsquelle IST, haengt am
    # Anker: 'fixed_date' und 'month_end' kennen keine Hauptfaelligkeit.
    anchor_confirmed = anchor not in _ANCHORS_USING_MAIN_DUE or main_due_source == "user_confirmed"
    terms_confirmed = notice_terms_source != "derived_from_days"
    if anchor_confirmed and terms_confirmed:
        certainty: Certainty = "confirmed"
        reason = ""
    else:
        certainty = "assumed"
        reason = (
            "Fristlaenge aus Tagen umgerechnet - Bezugspunkt bitte pruefen."
            if not terms_confirmed
            else "Hauptfaelligkeit ist eine Annahme aus dem Katalog, nicht bestaetigt."
        )

    return InsuranceDeadline(
        anchor=anchor,
        deadline_kind=deadline_kind,
        certainty=certainty,
        term_ends_on=term_ends_on,
        deadline_legal_on=deadline_legal_on,
        safe_service_on=safe_service_on,
        recommended_action_on=recommended_action_on,
        # Der Countdown zaehlt auf das HANDLUNGSDATUM, nicht auf das
        # rechtliche Fristende: bis dorthin muss der Nutzer etwas tun.
        days_left=(recommended_action_on - today).days,
        reason=reason,
        recipient_state=recipient_state,
        shifts=tuple(shifts),
    )


def shifts_for(deadline: InsuranceDeadline, step: DeadlineStep) -> tuple[DeadlineShift, ...]:
    """Die Verschiebungsgruende eines einzelnen Schrittes.

    Die Oberflaeche begruendet die drei Daten getrennt; ein gemeinsamer Topf
    liesse "Fronleichnam" unter dem rechtlichen Fristende erscheinen, wo er den
    Versandpuffer verlaengert hat.
    """
    return tuple(item for item in deadline.shifts if item.step == step)


# ---------------------------------------------------------------------------
# Vorschaltung fuer contract_service
# ---------------------------------------------------------------------------


def resolve_notice_terms(policy_row: Mapping[str, Any]) -> NoticeTerms | None:
    """Fristangaben einer Police, oder ``None`` wenn sie nicht bestaetigt sind.

    Reine Funktion ueber einer bereits geladenen Zeile - kein Nachladen, keine
    Session. Der Aufrufer laedt die Policen eines Haushalts in EINER Abfrage
    (Plan 8.1) und ruft diese Funktion je Zeile.

    ``None`` heisst: ``compute_term_deadline`` rechnet buchstaeblich wie vorher.
    Das ist der Regelfall und der Grund, warum die Vorschaltung den Bestand
    nicht anfasst.

    Die Bestaetigungspruefung ist gegenueber der Skizze in Plan 8.1 an einer
    Stelle praeziser: ``main_due_source`` wird nur dort verlangt, wo der Anker
    die Hauptfaelligkeit ueberhaupt benutzt. Woertlich genommen ("None bei
    main_due_source <> 'user_confirmed'") koennte eine Mopedpolice niemals
    gewinnen - sie hat gar keine Hauptfaelligkeit, ihr Versicherungsjahr steht
    in Paragraph 26 FZV, und ``main_due_source`` steht dort per CHECK auf
    'not_computable'.
    """
    anchor_raw = policy_row.get("notice_anchor")
    if anchor_raw not in _NOTICE_ANCHORS:
        return None
    anchor = cast(NoticeAnchor, anchor_raw)

    kind_raw = policy_row.get("deadline_kind")
    if kind_raw not in _DEADLINE_KINDS:
        # Der Fristtyp ist Pflicht und hat bewusst keinen Default (Plan 4.7a).
        # Fehlt er, wird nicht geraten, sondern der Bestandsweg genommen.
        return None
    deadline_kind = cast(DeadlineKind, kind_raw)

    main_due_source = str(policy_row.get("main_due_source") or "not_computable")
    notice_terms_source = str(policy_row.get("notice_terms_source") or "catalog_suggested")

    if notice_terms_source == "derived_from_days":
        # Umgerechnete Tage sind eine Schaetzung. Sie duerfen den bestehenden
        # Vertragsrechner nicht ausstechen, sonst wanderte auf /fixkosten ein
        # Termin, ohne dass jemand ihn bestaetigt haette.
        return None
    if anchor in _ANCHORS_USING_MAIN_DUE and main_due_source != "user_confirmed":
        return None

    channel_raw = policy_row.get("service_channel")
    # Unbekannter Zustellweg -> 'post'. Das ist der laengste Puffer und damit
    # die vorsichtige Richtung; ein geratenes 'email' verschoebe das
    # Handlungsdatum um drei Werktage nach hinten.
    channel = cast(
        ServiceChannel, channel_raw if channel_raw in _SERVICE_CHANNELS else "post"
    )

    return NoticeTerms(
        anchor=anchor,
        anchor_basis_kind=str(policy_row.get("anchor_basis_kind") or "policy_terms"),
        deadline_kind=deadline_kind,
        period_months=_as_int(policy_row.get("notice_period_months")),
        main_due_on=_as_date(policy_row.get("main_due_on")),
        fixed_month=_as_int(policy_row.get("notice_fixed_month")),
        fixed_day=_as_int(policy_row.get("notice_fixed_day")),
        min_term_months=_as_int(policy_row.get("min_term_months")),
        renewal_term_months=_as_int(policy_row.get("renewal_term_months")),
        service_channel=channel,
        recipient_state=_as_state(policy_row.get("recipient_state")),
        starts_on=_as_date(policy_row.get("starts_on")),
        main_due_source=main_due_source,
        notice_terms_source=notice_terms_source,
    )


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def _as_date(value: Any) -> date | None:
    # datetime ist eine Unterklasse von date. Ein durchgereichtes datetime
    # verglichen mit einem date wirft, deshalb hier ausdruecklich normiert.
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _as_state(value: Any) -> str | None:
    return value.upper() if isinstance(value, str) and value else None
