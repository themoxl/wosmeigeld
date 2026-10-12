"""Abgleichsmotor fuer Vertraege (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 5).

Phase 1 hat `contracts` samt Faelligkeiten, Betragsverlauf und Ereignissen
angelegt, aber niemand hat sie je gefuellt. Dieses Modul ist der einzige
Schreibpfad, der `contract_occurrences` erzeugt, `next_due_date` fortschreibt,
Preisaenderungen erkennt und ausgebliebene Zahlungen zaehlt.

Zwei Festlegungen praegen den Aufbau:

* Der Kern ist DB-frei. `score_candidate`, `pick_best_candidate` und
  `match_contract` bekommen fertige Datensaetze und liefern einen Schreibplan.
  Nur dadurch ist die Bewertungsformel ohne laufendes Supabase pruefbar.
* Das Modul laeuft in zwei Umgebungen: im API-Prozess als `authenticated` mit
  RLS und im Worker als `postgres` ohne RLS (`services/worker/worker/db.py`).
  Dort liefert `default current_household_id()` NULL, deshalb steht
  `household_id` in jedem INSERT und in jeder WHERE-Klausel ausdruecklich.

Committet wird hier nichts: im API-Prozess umschliesst die Transaktion den
gesamten Request (`auth/dependencies.get_household_session`), im Worker
committet der Orchestrator.
"""

import calendar
import logging
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Final, Literal, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.contract import Contract
from app.services import contract_service, rules_service

logger = logging.getLogger(__name__)

CONTRACT_MATCH_ENTITY_TYPE: Final = "contract"

# Ein lange nicht gelaufener Haushalt darf keine Endlosschleife erzeugen.
_MAX_CATCHUP_PERIODS: Final = 24
_BACKFILL_MAX_MONTHS: Final = 36
_BACKFILL_MAX_TRANSACTIONS: Final = 200

# Wie viele bereits abgeschriebene Perioden ein Lauf hoechstens noch einmal
# prueft. Deckel aus demselben Grund wie oben: ein Vertrag mit Jahren an
# Fehlperioden darf nicht bei jedem Lauf hunderte Fenster abfragen.
_MAX_REPAIR_PERIODS: Final = 24

# Spiegel der Inbox-Schwelle (`rules_service._CONTRACT_ENDED_CHECK_MIN_MISSED`).
# Entscheidung 17.3 des Plans verlangt die Zwischenstufe ausdruecklich: ab zwei
# Fehlperioden erscheint das Review-Item "Laeuft dieser Vertrag noch?", "auch
# wenn die Automatik spaeter ohnehin greifen wuerde". Ein einziger Nachholauf
# kann von 0 auf 3 zaehlen; ohne diese Schwelle waere der Vertrag beendet, bevor
# die Frage je in der Inbox stand.
_REVIEW_MIN_MISSED_PERIODS: Final = rules_service._CONTRACT_ENDED_CHECK_MIN_MISSED

# Eine Preisaenderung darf nur bei belastbarer Identitaet angenommen werden,
# und der Deckel verhindert, dass eine zufaellige Grosszahlung an denselben
# Empfaenger den Sollbetrag des Vertrags zerstoert.
_PRICE_CHANGE_MIN_IDENTITY: Final = 0.80
_PRICE_CHANGE_MAX_RATIO: Final = 0.60

# Untergrenze der Betragstoleranz: ein Vertrag mit tolerance_amount_minor = 0
# (Vorgabe beim Anlegen) wuerde sonst schon an einem Cent Rundungsdifferenz
# scheitern.
_AMOUNT_TOLERANCE_FLOOR_MINOR: Final = 100
# Bezugsgroesse der Betragsnaehe. Ohne Untergrenze waere bei einem 2-EUR-Vertrag
# jede Abweichung sofort maximal weit entfernt.
_AMOUNT_CLOSENESS_FLOOR_MINOR: Final = 500

_IDENTITY_CONTRACT_KEY: Final = 1.00
_IDENTITY_PAYEE_ID: Final = 0.80
_IDENTITY_PAYEE_KEY: Final = 0.60

# Ab wie vielen Zeichen ein Vertragsschluessel die Identitaet allein traegt.
# `contract_detection_service._MIN_MANDATE_KEY_LENGTH` laesst schon vier Zeichen
# als Schluessel zu ("Kundennr. 1234"); vier Ziffern stehen aber in jeder
# zweiten Rechnungsnummer. Darunter braucht es zusaetzlich den Empfaenger.
_MIN_STANDALONE_KEY_LENGTH: Final = 6

_WEIGHT_IDENTITY: Final = 0.5
_WEIGHT_AMOUNT: Final = 0.3
_WEIGHT_DATE: Final = 0.2

_DEFAULT_AUTO_END_AFTER_MISSED: Final = "3"
_AUTO_END_OFF: Final = "off"

# Nur laufende und gekuendigte Vertraege werden abgeglichen: pausierte sollen
# ausdruecklich ruhen, beendete und verneinte sind Geschichte.
RECONCILE_STATUSES: Final = ("active", "cancelled")

# Spiegel von contract_service._MONTHS_PER_PERIOD. Das dortige Dict ist privat
# und kennt nur die Vorwaertsrichtung; der Backfill braucht denselben Kalender
# rueckwaerts. Wird eine Frequenz ergaenzt, muessen beide Stellen wachsen.
_MONTHS_PER_PERIOD: Final[dict[str, int]] = {
    "monthly": 1,
    "quarterly": 3,
    "semiannual": 6,
    "yearly": 12,
}


# ---------------------------------------------------------------------------
# Datentypen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractSpec:
    """Der Vertrag reduziert auf das, was der Abgleich braucht.

    Bewusst kein Pydantic-Modell: der Kern muss ohne Request und ohne
    Validierungsschicht rechnen koennen.
    """

    contract_id: UUID
    household_id: UUID
    # Falls bekannt, bestimmt dieses Konto den importierten Datenstand. Ohne
    # Kontobezug faellt der Abgleich auf den Datenstand des Haushalts zurueck.
    account_id: UUID | None
    direction: Literal["expense", "income"]
    # Immer positiv (CHECK amount_minor > 0). Verglichen wird gegen abs(ist).
    amount_minor: int
    frequency: str
    due_rule: str
    due_day: int | None
    next_due_date: date
    tolerance_days: int
    tolerance_amount_minor: int
    status: str
    contract_key: str | None
    payee_id: UUID | None
    # payee_key(provider_label oder name), einmal vorberechnet.
    identity_label_key: str
    ends_on: date | None
    cancelled_on: date | None
    installments_total: int | None
    installments_paid: int
    missed_periods: int
    version: int
    # Hat der Abgleich fuer diesen Vertrag jemals eine Buchung getroffen?
    # Standard True, damit bestehende Aufrufer unveraendert rechnen; der echte
    # Ladeweg setzt den Wert ausdruecklich.
    ever_matched: bool = True
    # Stammt der Vertrag aus der SQL-Uebernahme aus `recurring_rules`? Diese
    # Zeilen sind nie durch einen Anwendungsservice gelaufen und haben deshalb
    # weder Schluessel noch Zeitraeume - siehe Plan 19.2.
    legacy_import: bool = False


@dataclass(frozen=True)
class CandidateTx:
    """Eine Buchung im Faelligkeitsfenster."""

    transaction_id: UUID
    booked_date: date
    # Vorzeichen wie in transactions: Ausgaben sind negativ.
    amount_minor: int
    payee_id: UUID | None
    payee_key: str
    purpose_normalized: str


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: CandidateTx
    identity: float
    amount_closeness: float
    date_closeness: float
    score: float
    # abs(ist) - soll: positiv heisst teurer geworden.
    amount_delta_minor: int
    # booked_date - due_date in Tagen: positiv heisst spaeter gebucht.
    days_delta: int


@dataclass(frozen=True)
class PendingEvent:
    kind: str
    occurred_on: date
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchOutcome:
    """Ergebnis GENAU EINER Faelligkeitsperiode.

    Die Schleife ueber mehrere Perioden fuehrt `reconcile_contract`, nicht diese
    Auswertung - sonst waere der Kern nicht mehr ohne DB pruefbar.
    """

    kind: Literal["matched", "price_change", "missing", "pending", "term_reached"]
    due_date: date
    chosen: ScoredCandidate | None
    occurrence_status: Literal["matched", "missing"] | None
    # Nur bei kind == 'price_change' gesetzt.
    new_amount_minor: int | None
    new_tolerance_amount_minor: int | None
    # Fortgeschrieben, ausser bei 'pending' und 'term_reached'.
    next_due_date: date
    # Zaehler NACH dieser Periode.
    missed_periods: int
    new_status: str | None
    installments_paid: int
    events: tuple[PendingEvent, ...] = ()


@dataclass(frozen=True)
class ReconcileOutcome:
    """Ergebnis eines vollstaendigen Laufs ueber EINEN Vertrag."""

    contract_id: UUID
    periods_processed: int
    matched_count: int
    price_change_count: int
    missing_count: int
    resumed_count: int
    ended_count: int
    installments_completed_count: int
    final_status: str
    next_due_date: date
    missed_periods: int


@dataclass(frozen=True)
class ReconcileResult:
    """Ergebnis eines vollstaendigen Laufs ueber einen Haushalt."""

    household_id: UUID
    contracts_examined: int
    matched_count: int
    price_change_count: int
    missing_count: int
    resumed_count: int
    ended_count: int
    installments_completed_count: int


# Der Plan nennt das Ergebnisobjekt an einer Stelle `ReconcileSummary`.
ReconcileSummary = ReconcileResult


@dataclass(frozen=True)
class BackfillResult:
    contract_id: UUID
    occurrences_written: int
    amount_stages_written: int
    first_seen_date: date | None
    last_seen_date: date | None


@dataclass
class _Counters:
    """Veraenderlicher Zaehlersatz waehrend eines Laufs."""

    matched: int = 0
    price_change: int = 0
    missing: int = 0
    resumed: int = 0
    ended: int = 0
    installments_completed: int = 0
    periods: int = 0

    def add(self, outcome: MatchOutcome) -> None:
        # Bewusst ueberschneidungsfrei: matched + price_change + missing ergibt
        # die Zahl der geschriebenen Faelligkeiten. Waere eine Preisaenderung
        # zugleich ein Treffer, koennte der Rescan-Endpunkt keine Summe bilden.
        if outcome.kind == "matched":
            self.matched += 1
        elif outcome.kind == "price_change":
            self.price_change += 1
        elif outcome.kind == "missing":
            self.missing += 1
        if outcome.occurrence_status is not None:
            self.periods += 1
        for event in outcome.events:
            if event.kind == "payment_resumed":
                self.resumed += 1
            elif event.kind == "ended":
                self.ended += 1
            elif event.kind == "installment_completed":
                self.installments_completed += 1

    def add_repair(self, repaired: int, *, resumed: bool) -> None:
        """Nachgetragene Perioden zaehlen wie Treffer.

        Sie sind welche - nur eben in einer Periode, die ein frueherer Lauf
        mangels Buchung bereits abgeschrieben hatte.
        """
        self.matched += repaired
        self.periods += repaired
        if resumed:
            self.resumed += 1


# ---------------------------------------------------------------------------
# Reine Rechenlogik (kein DB-Zugriff)
# ---------------------------------------------------------------------------


def payee_key(label: str | None) -> str:
    """Vergleichsschluessel fuer Empfaengernamen.

    Delegiert bewusst an die Erkennung (`rules_service._recurring_payee_key`):
    wuerde der Abgleich eine eigene Normalisierung mitbringen, koennte er einen
    Vertrag nicht mehr wiederfinden, den die Erkennung selbst vorgeschlagen hat.
    """
    if not label:
        return ""
    return rules_service._recurring_payee_key(label)


def normalize_purpose(value: str | None) -> str:
    """Verwendungszweck fuer den Vergleich mit `contract_key`.

    Dieselbe Regel wie beim Empfaengerschluessel: Kleinschreibung, alle
    Nicht-Wortzeichen zu Leerzeichen. `contract_key` wird beim Anlegen nur von
    Mehrfachleerzeichen befreit, deshalb wird auch er hier normalisiert - sonst
    scheitert eine Mandatsreferenz mit Bindestrich an ihrer eigenen Schreibweise.
    """
    if not value:
        return ""
    return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).strip()


def amount_tolerance_minor(amount_minor: int) -> int:
    """5 % des Betrags, mindestens 1 EUR, hoechstens 10 EUR.

    Dieselbe Formel wie in der Erkennung, damit ein erkannter und ein per
    Preisaenderung fortgeschriebener Vertrag dieselbe Bandbreite haben.
    """
    return rules_service._amount_tolerance(amount_minor)


def _key_stands_alone(key: str, purpose_normalized: str) -> bool:
    """Steht `key` als vollstaendige Tokenfolge im Verwendungszweck?

    `normalize_purpose` liefert leerzeichengetrennte Token, deshalb genuegt der
    Vergleich mit umschliessenden Leerzeichen. Eine reine Teilstringpruefung
    fand einen vierstelligen Schluessel auch mitten in einer fremden
    Rechnungsnummer ("4711" in "947110").
    """
    return f" {key} " in f" {purpose_normalized} "


def identity_score(contract: ContractSpec, tx: CandidateTx) -> float:
    """Wie sicher gehoert die Buchung zu diesem Vertrag?

    Absteigend geprueft, die erste zutreffende Regel gewinnt. Eine
    Mandatsreferenz im Verwendungszweck ist ein Beleg, ein gleicher Empfaenger
    nur ein Indiz.

    Zwei Einschraenkungen sichern den Beleg ab: der Schluessel muss als eigenes
    Token dastehen, und ein KURZER Schluessel (die Erkennung laesst ab vier
    Zeichen zu) belegt nur zusammen mit dem Empfaenger. Ohne beides genuegte
    eine fremde Buchung mit einer zufaellig passenden Rechnungsnummer, um ueber
    `classify_hit` -> 'price_change' den Sollbetrag des Vertrags zu
    ueberschreiben.
    """
    same_payee = (
        contract.payee_id is not None and tx.payee_id == contract.payee_id
    ) or bool(contract.identity_label_key and tx.payee_key == contract.identity_label_key)

    key = normalize_purpose(contract.contract_key)
    if (
        key
        and _key_stands_alone(key, tx.purpose_normalized)
        and (len(key) >= _MIN_STANDALONE_KEY_LENGTH or same_payee)
    ):
        return _IDENTITY_CONTRACT_KEY
    if contract.payee_id is not None and tx.payee_id == contract.payee_id:
        return _IDENTITY_PAYEE_ID
    if contract.identity_label_key and tx.payee_key == contract.identity_label_key:
        return _IDENTITY_PAYEE_KEY
    return 0.0


def window_for(contract: ContractSpec) -> tuple[date, date]:
    """Suchfenster um die naechste Faelligkeit."""
    span = timedelta(days=contract.tolerance_days)
    return contract.next_due_date - span, contract.next_due_date + span


def score_candidate(
    contract: ContractSpec, tx: CandidateTx, due_date: date
) -> ScoredCandidate | None:
    """Bewertung eines Kandidaten. `None`, wenn die Identitaet 0 ist - dann ist
    die Buchung kein Kandidat, sondern nur zufaellig im Fenster."""
    identity = identity_score(contract, tx)
    if identity <= 0.0:
        return None

    expected = contract.amount_minor
    actual = abs(tx.amount_minor)
    amount_delta = actual - expected
    amount_reference = max(expected * 0.5, float(_AMOUNT_CLOSENESS_FLOOR_MINOR))
    amount_closeness = 1.0 - min(1.0, abs(amount_delta) / amount_reference)

    days_delta = (tx.booked_date - due_date).days
    date_reference = float(max(contract.tolerance_days, 1))
    date_closeness = 1.0 - min(1.0, abs(days_delta) / date_reference)

    score = (
        _WEIGHT_IDENTITY * identity
        + _WEIGHT_AMOUNT * amount_closeness
        + _WEIGHT_DATE * date_closeness
    )
    return ScoredCandidate(
        candidate=tx,
        identity=identity,
        amount_closeness=amount_closeness,
        date_closeness=date_closeness,
        score=score,
        amount_delta_minor=amount_delta,
        days_delta=days_delta,
    )


def classify_hit(
    contract: ContractSpec, scored: ScoredCandidate
) -> Literal["matched", "price_change"] | None:
    """Ist der Kandidat ein Treffer - und wenn ja, welcher Art?

    `None` heisst: die Buchung geht diesen Vertrag nichts an. Das ist der
    entscheidende Filter gegen die Zerstoerung des Sollbetrags durch eine
    einmalige Grosszahlung an denselben Empfaenger.
    """
    tolerance = max(contract.tolerance_amount_minor, _AMOUNT_TOLERANCE_FLOOR_MINOR)
    delta = abs(scored.amount_delta_minor)
    if delta <= tolerance:
        return "matched"
    if scored.identity < _PRICE_CHANGE_MIN_IDENTITY:
        return None
    if contract.amount_minor <= 0:
        return None
    if delta / contract.amount_minor > _PRICE_CHANGE_MAX_RATIO:
        return None
    return "price_change"


def _tie_break_key(scored: ScoredCandidate) -> tuple[float, int, int, str]:
    """Deterministische Reihenfolge: hoechster Score, dann der betragsnaechste,
    dann der datumsnaechste, dann die kleinere Buchungs-ID. Zwei Laeufe muessen
    dasselbe Ergebnis liefern, sonst ist der Motor nicht idempotent."""
    return (
        -scored.score,
        abs(scored.amount_delta_minor),
        abs(scored.days_delta),
        str(scored.candidate.transaction_id),
    )


def _score_all(
    contract: ContractSpec, candidates: Iterable[CandidateTx], due_date: date
) -> list[ScoredCandidate]:
    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        entry = score_candidate(contract, candidate, due_date)
        if entry is not None:
            scored.append(entry)
    return scored


def _best_of(scored: Sequence[ScoredCandidate]) -> ScoredCandidate | None:
    if not scored:
        return None
    return sorted(scored, key=_tie_break_key)[0]


def pick_best_candidate(
    contract: ContractSpec, candidates: Sequence[CandidateTx], due_date: date
) -> ScoredCandidate | None:
    """Bester Kandidat nach der Bewertungsformel, ohne Betragspruefung."""
    return _best_of(_score_all(contract, candidates, due_date))


# Der Plan nennt die Funktion an einer Stelle `pick_best`.
pick_best = pick_best_candidate


def pick_best_match(
    contract: ContractSpec, candidates: Sequence[CandidateTx], due_date: date
) -> ScoredCandidate | None:
    """Bester Kandidat, der auch die Betragspruefung besteht.

    Ein knapp bewerteter, aber betraglich plausibler Kandidat schlaegt einen
    hoch bewerteten Ausreisser - deshalb wird gefiltert und danach sortiert.
    """
    scored = [
        entry
        for entry in _score_all(contract, candidates, due_date)
        if classify_hit(contract, entry) is not None
    ]
    return _best_of(scored)


def resolve_conflicts(
    proposals: Mapping[UUID, ScoredCandidate],
) -> tuple[dict[UUID, ScoredCandidate], frozenset[UUID]]:
    """Arbitrierung, wenn zwei Vertraege dieselbe Buchung beanspruchen.

    Ohne diesen Schritt entscheidet die Verarbeitungsreihenfolge, und der
    partielle Unique-Index `contract_occurrences_transaction_uniq` quittiert das
    mit einem IntegrityError statt mit dem besseren Treffer.

    Rueckgabe: Gewinner je Vertrag und die Vertraege, die neu waehlen muessen.
    """
    by_transaction: dict[UUID, list[tuple[UUID, ScoredCandidate]]] = {}
    for contract_id, scored in proposals.items():
        by_transaction.setdefault(scored.candidate.transaction_id, []).append(
            (contract_id, scored)
        )

    winners: dict[UUID, ScoredCandidate] = {}
    losers: set[UUID] = set()
    for entries in by_transaction.values():
        if len(entries) == 1:
            contract_id, scored = entries[0]
            winners[contract_id] = scored
            continue
        # Die Vertrags-ID als letzter Schluessel: bei voellig gleichem Score
        # muss auch die Wahl zwischen zwei Vertraegen reproduzierbar sein.
        ranked = sorted(entries, key=lambda item: (*_tie_break_key(item[1]), str(item[0])))
        winner_id, winner_scored = ranked[0]
        winners[winner_id] = winner_scored
        losers.update(contract_id for contract_id, _ in ranked[1:])

    return winners, frozenset(losers)


def _advance(contract: ContractSpec, due_date: date) -> date:
    return contract_service.next_due_date(
        due_date,
        contract.frequency,
        due_rule=contract.due_rule,
        due_day=contract.due_day,
    )


def _auto_end_limit(auto_end_after_missed: str) -> int | None:
    """`'off'` heisst nie beenden; alles andere ist die Zahl der Fehlperioden."""
    if auto_end_after_missed == _AUTO_END_OFF:
        return None
    try:
        return int(auto_end_after_missed)
    except ValueError:
        return int(_DEFAULT_AUTO_END_AFTER_MISSED)


def is_term_reached(contract: ContractSpec, today: date) -> bool:
    """Laufzeit vorbei und keine Faelligkeit mehr innerhalb der Laufzeit offen.

    Ausgelagert, weil der Lauf die Frage vor der Arbitrierung braucht: ein
    solcher Vertrag verwirft jeden Kandidaten (siehe `match_contract`), duerfte
    also gar nicht erst mitbieten.
    """
    return (
        contract.ends_on is not None
        and today > contract.ends_on
        and contract.next_due_date > contract.ends_on
    )


def match_contract(
    contract: ContractSpec,
    candidates: Sequence[CandidateTx],
    today: date,
    *,
    auto_end_after_missed: str = _DEFAULT_AUTO_END_AFTER_MISSED,
    allow_auto_end: bool = True,
    data_through: date | None = None,
) -> MatchOutcome:
    """Wertet GENAU EINE Faelligkeitsperiode aus und liefert den Schreibplan.

    `kind='pending'` heisst: weder Treffer noch abgelaufene Toleranz - es ist
    nichts zu tun und `next_due_date` bleibt stehen.

    `allow_auto_end=False` schreibt die Fehlperiode, laesst den Vertrag aber
    laufen. Der Lauf setzt das, solange die Frage "laeuft der noch?" noch nicht
    in der Review-Inbox stand (siehe `_REVIEW_MIN_MISSED_PERIODS`).
    """
    due = contract.next_due_date

    # Ein Vertrag, dessen Laufzeit vorbei ist und fuer den keine Faelligkeit
    # mehr innerhalb der Laufzeit offen ist, ist beendet. Bewusst auch fuer
    # 'active': die Auto-Ende-Automatik nimmt Vertraege mit gesetztem ends_on
    # ausdruecklich aus, ohne diesen Zweig wuerden sie fuer immer haengen.
    ends_on = contract.ends_on
    if ends_on is not None and is_term_reached(contract, today):
        return MatchOutcome(
            kind="term_reached",
            due_date=due,
            chosen=None,
            occurrence_status=None,
            new_amount_minor=None,
            new_tolerance_amount_minor=None,
            next_due_date=due,
            missed_periods=contract.missed_periods,
            new_status="ended",
            installments_paid=contract.installments_paid,
            events=(
                PendingEvent(
                    kind="ended",
                    occurred_on=ends_on,
                    payload={"reason": "term_reached", "ends_on": str(ends_on)},
                ),
            ),
        )

    chosen = pick_best_match(contract, candidates, due)
    if chosen is not None:
        return _hit_outcome(contract, chosen, due)

    # Erst wenn auch die Toleranz IM VORLIEGENDEN DATENSTAND verstrichen ist,
    # gilt eine Zahlung als ausgeblieben. Der Kalender allein ist kein Beleg:
    # wenn der letzte Kontoauszug im Juli endet, darf eine August-Faelligkeit
    # auch am 7. August noch nicht als fehlend abgeschrieben werden.
    evaluated_through = min(today, data_through) if data_through is not None else today
    if evaluated_through <= due + timedelta(days=contract.tolerance_days):
        return MatchOutcome(
            kind="pending",
            due_date=due,
            chosen=None,
            occurrence_status=None,
            new_amount_minor=None,
            new_tolerance_amount_minor=None,
            next_due_date=due,
            missed_periods=contract.missed_periods,
            new_status=None,
            installments_paid=contract.installments_paid,
        )

    return _missing_outcome(contract, due, auto_end_after_missed, allow_auto_end)


def _hit_outcome(
    contract: ContractSpec, chosen: ScoredCandidate, due: date
) -> MatchOutcome:
    """Gemeinsamer Pfad fuer Treffer und Preisaenderung: beide sind eine
    tatsaechlich geleistete Zahlung und zaehlen den Fehlzaehler zurueck."""
    hit_kind = classify_hit(contract, chosen)
    booked = chosen.candidate.booked_date
    events: list[PendingEvent] = []

    new_amount: int | None = None
    new_tolerance: int | None = None
    if hit_kind == "price_change":
        new_amount = abs(chosen.candidate.amount_minor)
        new_tolerance = amount_tolerance_minor(new_amount)
        events.append(
            PendingEvent(
                kind="price_change",
                occurred_on=booked,
                payload={
                    "old_minor": contract.amount_minor,
                    "new_minor": new_amount,
                    "delta_minor": chosen.amount_delta_minor,
                    "pct": round(chosen.amount_delta_minor * 100.0 / contract.amount_minor, 2),
                },
            )
        )

    if contract.missed_periods > 0:
        events.append(
            PendingEvent(
                kind="payment_resumed",
                occurred_on=booked,
                payload={
                    "due_date": str(due),
                    "missed_periods": contract.missed_periods,
                },
            )
        )

    installments_paid = contract.installments_paid
    new_status: str | None = None
    if contract.installments_total is not None:
        installments_paid += 1
        if installments_paid >= contract.installments_total:
            new_status = "ended"
            events.append(
                PendingEvent(
                    kind="installment_completed",
                    occurred_on=booked,
                    payload={
                        "installments_total": contract.installments_total,
                        "installments_paid": installments_paid,
                    },
                )
            )

    return MatchOutcome(
        kind="price_change" if hit_kind == "price_change" else "matched",
        due_date=due,
        chosen=chosen,
        occurrence_status="matched",
        new_amount_minor=new_amount,
        new_tolerance_amount_minor=new_tolerance,
        next_due_date=_advance(contract, due),
        missed_periods=0,
        new_status=new_status,
        installments_paid=installments_paid,
        events=tuple(events),
    )


def _missing_outcome(
    contract: ContractSpec,
    due: date,
    auto_end_after_missed: str,
    allow_auto_end: bool = True,
) -> MatchOutcome:
    missed = contract.missed_periods + 1
    events: list[PendingEvent] = []
    if contract.missed_periods == 0:
        events.append(
            PendingEvent(
                kind="payment_missing",
                occurred_on=due,
                payload={"due_date": str(due), "missed_periods": missed},
            )
        )

    new_status: str | None = None
    limit = _auto_end_limit(auto_end_after_missed)
    # Eine ausdrueckliche Nutzerangabe schlaegt die Heuristik: wer ein Ende oder
    # eine Kuendigung eingetragen hat, bekommt keinen automatisch beendeten
    # Vertrag untergeschoben.
    user_decided = contract.ends_on is not None or contract.cancelled_on is not None
    # Uebernommene Altvertraege, die der Abgleich noch NIE getroffen hat, sind
    # nie durch einen Backfill gelaufen und haben ohne Schluessel keine Chance
    # auf die Identitaetsschwelle (Plan 19.2). Ihre "ausgebliebenen" Zahlungen
    # beschreiben die Zuordnung, nicht den Vertrag - sie zu beenden waere ein
    # Fehlschluss aus einem Datenmangel.
    # Bewusst NUR fuer den Altbestand: ein von Hand angelegter Vertrag, auf den
    # nie eine Zahlung laeuft, soll weiterhin automatisch enden.
    never_had_a_chance = not contract.ever_matched and contract.legacy_import
    if (
        limit is not None
        and missed >= limit
        and not user_decided
        and allow_auto_end
        and not never_had_a_chance
    ):
        new_status = "ended"
        events.append(
            PendingEvent(
                kind="ended",
                occurred_on=due,
                payload={"reason": "auto_missed", "missed_periods": missed},
            )
        )

    return MatchOutcome(
        kind="missing",
        due_date=due,
        chosen=None,
        occurrence_status="missing",
        new_amount_minor=None,
        new_tolerance_amount_minor=None,
        # Trotz Fehltreffer fortschreiben, sonst bleibt der Motor an dieser
        # Periode fuer immer stehen.
        next_due_date=_advance(contract, due),
        missed_periods=missed,
        new_status=new_status,
        installments_paid=contract.installments_paid,
        events=tuple(events),
    )


# ---------------------------------------------------------------------------
# Lesen aus der Datenbank
# ---------------------------------------------------------------------------

_SPEC_COLUMNS: Final = (
    "id, household_id, account_id, direction, amount_minor, frequency, due_rule, due_day, "
    "next_due_date, tolerance_days, tolerance_amount_minor, status, contract_key, "
    "payee_id, coalesce(nullif(btrim(provider_label), ''), name) as identity_label, "
    "ends_on, cancelled_on, installments_total, installments_paid, missed_periods, version, "
    "(last_matched_at is not null) as ever_matched, "
    "(legacy_recurring_rule_id is not null) as legacy_import"
)


def _spec_from_row(row: Any) -> ContractSpec:
    return ContractSpec(
        contract_id=row.id,
        household_id=row.household_id,
        account_id=row.account_id,
        direction=cast(Literal["expense", "income"], row.direction),
        amount_minor=int(row.amount_minor),
        frequency=str(row.frequency),
        due_rule=str(row.due_rule),
        due_day=int(row.due_day) if row.due_day is not None else None,
        next_due_date=row.next_due_date,
        tolerance_days=int(row.tolerance_days),
        tolerance_amount_minor=int(row.tolerance_amount_minor),
        status=str(row.status),
        contract_key=row.contract_key,
        payee_id=row.payee_id,
        identity_label_key=payee_key(row.identity_label),
        ends_on=row.ends_on,
        cancelled_on=row.cancelled_on,
        installments_total=(
            int(row.installments_total) if row.installments_total is not None else None
        ),
        installments_paid=int(row.installments_paid),
        missed_periods=int(row.missed_periods),
        version=int(row.version),
        ever_matched=bool(row.ever_matched),
        legacy_import=bool(row.legacy_import),
    )


def spec_from_contract(contract: Contract) -> ContractSpec:
    """Bruecke fuer Aufrufer, die den Vertrag bereits als Pydantic-Modell haben
    (etwa der Rescan-Endpunkt oder der Backfill nach dem Anlegen)."""
    return ContractSpec(
        contract_id=contract.id,
        household_id=contract.household_id,
        account_id=contract.account_id,
        direction=contract.direction,
        amount_minor=contract.amount_minor,
        frequency=contract.frequency,
        due_rule=contract.due_rule,
        due_day=contract.due_day,
        next_due_date=contract.next_due_date,
        tolerance_days=contract.tolerance_days,
        tolerance_amount_minor=contract.tolerance_amount_minor,
        status=contract.status,
        contract_key=contract.contract_key,
        payee_id=contract.payee_id,
        identity_label_key=payee_key(contract.provider_label or contract.name),
        ends_on=contract.ends_on,
        cancelled_on=contract.cancelled_on,
        installments_total=contract.installments_total,
        installments_paid=contract.installments_paid,
        missed_periods=contract.missed_periods,
        version=contract.version,
        ever_matched=contract.last_matched_at is not None,
    )


async def load_match_specs(
    session: AsyncSession,
    household_id: UUID,
    *,
    contract_ids: Sequence[UUID] | None = None,
) -> list[ContractSpec]:
    """Abzugleichende Vertraege in fester Reihenfolge.

    Die Sortierung nach Faelligkeit und ID macht den Lauf reproduzierbar - bei
    gleichwertigen Kandidaten haengt das Ergebnis sonst an der Planreihenfolge
    von Postgres.
    """
    params: dict[str, Any] = {
        "household_id": str(household_id),
        "statuses": list(RECONCILE_STATUSES),
    }
    condition = ""
    if contract_ids is not None:
        params["ids"] = [str(item) for item in contract_ids]
        condition = " and id = any(cast(:ids as uuid[]))"

    result = await session.execute(
        text(
            f"select {_SPEC_COLUMNS} from contracts "
            "where household_id = :household_id and merged_into_id is null "
            "and status = any(cast(:statuses as text[]))" + condition + " "
            "order by next_due_date asc, id asc"
        ),
        params,
    )
    return [_spec_from_row(row) for row in result]


async def list_reconcilable_households(session: AsyncSession) -> list[UUID]:
    """Haushalte mit mindestens einem abzugleichenden Vertrag.

    Nur fuer den naechtlichen Lauf gedacht: laeuft ohne RLS-Kontext und darf
    deshalb nicht auf `current_household_id()` bauen.
    """
    result = await session.execute(
        text(
            "select distinct household_id from contracts "
            "where merged_into_id is null "
            "and status = any(cast(:statuses as text[])) order by household_id"
        ),
        {"statuses": list(RECONCILE_STATUSES)},
    )
    return [cast(UUID, row.household_id) for row in result]


_CANDIDATE_COLUMNS: Final = (
    "t.id, t.booked_date, t.amount_minor, t.payee_id, "
    "coalesce(nullif(btrim(p.name), ''), nullif(btrim(t.payee_label), ''), "
    "nullif(btrim(t.payee_raw), ''), '') as payee_label, "
    # purpose_raw ist der Ort der Mandatsreferenz; von Hand erfasste Buchungen
    # haben keinen, dort ist die Notiz der einzige Text.
    "coalesce(nullif(btrim(t.purpose_raw), ''), t.note, '') as purpose_text"
)


async def fetch_candidates(
    session: AsyncSession,
    household_id: UUID,
    contract: ContractSpec,
    window_from: date,
    window_to: date,
    *,
    claimed_transaction_ids: Collection[UUID] = (),
    ignore_own_occurrences: bool = False,
) -> list[CandidateTx]:
    """Buchungen im Faelligkeitsfenster, die noch keinem Vertrag gehoeren.

    `claimed_transaction_ids` sind die in DIESEM Lauf bereits vergebenen
    Buchungen: sie stehen noch nicht in `contract_occurrences` und waeren fuer
    das `not exists` unsichtbar.

    `ignore_own_occurrences` braucht der Backfill: dort sollen die bereits
    zugeordneten Buchungen desselben Vertrags erneut gefunden werden, damit ein
    zweiter Backfill dieselbe Betragstreppe erzeugt statt eine leere.
    """
    # direction ist ein Literal aus dem Schema, kein Nutzertext - die
    # Fallunterscheidung hier statt im SQL haelt den Index nutzbar.
    sign_condition = (
        "t.amount_minor < 0" if contract.direction == "expense" else "t.amount_minor > 0"
    )
    own_condition = " and o.contract_id <> :contract_id" if ignore_own_occurrences else ""

    result = await session.execute(
        text(
            f"select {_CANDIDATE_COLUMNS} from transactions t "
            "left join payees p on p.id = t.payee_id and p.household_id = t.household_id "
            "where t.household_id = :household_id "
            "and not t.is_archived and not t.is_transfer "
            f"and {sign_condition} "
            "and t.booked_date between :window_from and :window_to "
            # Der Haushaltsfilter im Unterausdruck ist kein Beiwerk: der
            # partielle Unique-Index liegt auf (household_id, transaction_id).
            "and not exists (select 1 from contract_occurrences o "
            "  where o.household_id = :household_id and o.transaction_id = t.id"
            f"{own_condition}) "
            "and not (t.id = any(cast(:claimed as uuid[]))) "
            # Eine manuelle Zuordnung schlaegt die Erkennung (Abschnitt 19.6,
            # Leitsatz 5): eine Buchung, die der Nutzer einem ANDEREN Vertrag
            # zugeordnet oder ausdruecklich geloest hat (contract_id is null),
            # darf hier nicht erneut auftauchen. Zeigt der Override auf DIESEN
            # Vertrag, steht die Buchung laengst in contract_occurrences und
            # ist durch den Ausschluss oben bereits unsichtbar.
            "and not exists (select 1 from contract_assignment_overrides ov "
            "  where ov.household_id = :household_id and ov.transaction_id = t.id "
            "  and (ov.contract_id is null or ov.contract_id <> :contract_id)) "
            "order by t.booked_date asc, t.id asc"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(contract.contract_id),
            "window_from": window_from,
            "window_to": window_to,
            "claimed": [str(item) for item in claimed_transaction_ids],
        },
    )
    return [
        CandidateTx(
            transaction_id=row.id,
            booked_date=row.booked_date,
            amount_minor=int(row.amount_minor),
            payee_id=row.payee_id,
            payee_key=payee_key(row.payee_label),
            purpose_normalized=normalize_purpose(row.purpose_text),
        )
        for row in result
    ]


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

_OCCURRENCE_UPSERT: Final = (
    "insert into contract_occurrences (household_id, contract_id, due_date, status, "
    "transaction_id, booked_date, amount_minor, amount_delta_minor, days_delta, "
    "match_score, matched_at) values (:household_id, :contract_id, :due_date, :status, "
    "cast(:transaction_id as uuid), cast(:booked_date as date), "
    "cast(:amount_minor as bigint), cast(:amount_delta_minor as bigint), "
    "cast(:days_delta as int), cast(:match_score as numeric), "
    "case when cast(:is_matched as boolean) then now() else null end) "
    "on conflict (contract_id, due_date) do update set "
    "status = excluded.status, transaction_id = excluded.transaction_id, "
    "booked_date = excluded.booked_date, amount_minor = excluded.amount_minor, "
    "amount_delta_minor = excluded.amount_delta_minor, days_delta = excluded.days_delta, "
    "match_score = excluded.match_score, matched_at = excluded.matched_at "
    # Zwei Zustaende sind vor dem Motor geschuetzt: eine vom Nutzer
    # uebersprungene Periode und ein bereits bestaetigter Treffer. Beides darf
    # ein Wiederholungslauf nicht zu 'missing' herabstufen.
    "where contract_occurrences.status <> 'skipped' "
    "and (contract_occurrences.status <> 'matched' or excluded.status = 'matched')"
)


async def _write_occurrence(
    session: AsyncSession,
    household_id: UUID,
    contract_id: UUID,
    *,
    due_date: date,
    occurrence_status: str,
    chosen: ScoredCandidate | None,
) -> bool:
    """Schreibt eine Faelligkeit. `False`, wenn die Buchung inzwischen einem
    anderen Vertrag gehoert.

    Der Savepoint ist Pflicht: ohne ihn vergiftet der IntegrityError des
    partiellen Unique-Index die gesamte umgebende Transaktion (gleiches Muster
    wie beim Start eines Klassifikationslaufs in `routes/ai.py`).
    """
    params: dict[str, Any] = {
        "household_id": str(household_id),
        "contract_id": str(contract_id),
        "due_date": due_date,
        "status": occurrence_status,
        "transaction_id": None,
        "booked_date": None,
        "amount_minor": None,
        "amount_delta_minor": None,
        "days_delta": None,
        "match_score": None,
        "is_matched": occurrence_status == "matched",
    }
    if chosen is not None:
        params.update(
            {
                "transaction_id": str(chosen.candidate.transaction_id),
                "booked_date": chosen.candidate.booked_date,
                # Festlegung: hier steht der Buchungsbetrag MIT Originalvorzeichen,
                # damit die Detailanzeige eine Ausgabe nicht als Einnahme zeigt.
                # Die Abweichung daneben ist dagegen vorzeichenlos gerechnet.
                "amount_minor": chosen.candidate.amount_minor,
                "amount_delta_minor": chosen.amount_delta_minor,
                "days_delta": chosen.days_delta,
                "match_score": round(chosen.score, 2),
            }
        )

    try:
        async with session.begin_nested():
            await session.execute(text(_OCCURRENCE_UPSERT), params)
    except IntegrityError:
        logger.warning(
            "Faelligkeit %s des Vertrags %s konnte nicht geschrieben werden: "
            "die Buchung gehoert bereits einem anderen Vertrag.",
            due_date,
            contract_id,
        )
        return False
    return True


_CONTRACT_UPDATE: Final = (
    "update contracts set next_due_date = :next_due_date, "
    "missed_periods = :missed_periods, installments_paid = :installments_paid, "
    "status = coalesce(cast(:status as text), status), "
    # Ein automatisch beendeter Vertrag braucht ein Enddatum. Ohne es liefe er
    # in jeder Zeitreihe ewig weiter: der Fixkostentrend liest die offene
    # Preisstufe und kennt ausser `ends_on` keine Grenze. Genommen wird die
    # letzte verarbeitete Faelligkeit - der Vertrag hat bis dahin gekostet.
    "ends_on = case when cast(:status as text) = 'ended' "
    "  then coalesce(ends_on, cast(:due_date as date)) else ends_on end, "
    "amount_minor = coalesce(cast(:amount_minor as bigint), amount_minor), "
    "tolerance_amount_minor = coalesce(cast(:tolerance_amount_minor as bigint), "
    "  tolerance_amount_minor), "
    # least/greatest ignorieren NULL, deshalb funktioniert das sowohl beim
    # allerersten Treffer als auch, wenn gar kein Datum mitkommt.
    "first_seen_date = least(first_seen_date, cast(:seen_date as date)), "
    "last_seen_date = greatest(last_seen_date, cast(:seen_date as date)), "
    "last_matched_at = case when cast(:is_matched as boolean) then now() "
    "  else last_matched_at end "
    "where id = :contract_id and household_id = :household_id"
)


async def apply_outcome(
    session: AsyncSession,
    household_id: UUID,
    contract: ContractSpec,
    outcome: MatchOutcome,
) -> ContractSpec:
    """Einziger Schreibpfad des Motors.

    Liefert den fortgeschriebenen `ContractSpec` fuer die naechste Runde zurueck.
    Bleibt er unveraendert, hat der Lauf fuer diesen Vertrag nichts erreicht und
    die Schleife bricht ab - das faengt sowohl 'pending' als auch eine
    verlorene Buchung ab.
    """
    if outcome.kind == "pending":
        return contract

    if outcome.occurrence_status is not None:
        written = await _write_occurrence(
            session,
            household_id,
            contract.contract_id,
            due_date=outcome.due_date,
            occurrence_status=outcome.occurrence_status,
            chosen=outcome.chosen,
        )
        if not written:
            return contract

    if outcome.new_amount_minor is not None and outcome.chosen is not None:
        await _open_price_stage(session, household_id, contract, outcome)

    booked = outcome.chosen.candidate.booked_date if outcome.chosen is not None else None
    await session.execute(
        text(_CONTRACT_UPDATE),
        {
            "household_id": str(household_id),
            "contract_id": str(contract.contract_id),
            "next_due_date": outcome.next_due_date,
            "missed_periods": outcome.missed_periods,
            "installments_paid": outcome.installments_paid,
            "status": outcome.new_status,
            "due_date": outcome.due_date,
            "amount_minor": outcome.new_amount_minor,
            "tolerance_amount_minor": outcome.new_tolerance_amount_minor,
            "seen_date": booked,
            "is_matched": booked is not None,
        },
    )

    for event in outcome.events:
        await contract_service.record_event(
            session,
            household_id,
            contract.contract_id,
            event.kind,
            occurred_on=event.occurred_on,
            payload=event.payload,
        )

    return replace(
        contract,
        next_due_date=outcome.next_due_date,
        missed_periods=outcome.missed_periods,
        installments_paid=outcome.installments_paid,
        status=outcome.new_status or contract.status,
        # Spiegelt das oben geschriebene Enddatum, damit die naechste Runde
        # denselben Vertrag sieht wie die Datenbank.
        ends_on=(
            contract.ends_on
            if contract.ends_on is not None or outcome.new_status != "ended"
            else outcome.due_date
        ),
        amount_minor=outcome.new_amount_minor or contract.amount_minor,
        tolerance_amount_minor=(
            outcome.new_tolerance_amount_minor
            if outcome.new_tolerance_amount_minor is not None
            else contract.tolerance_amount_minor
        ),
    )


async def _open_price_stage(
    session: AsyncSession,
    household_id: UUID,
    contract: ContractSpec,
    outcome: MatchOutcome,
) -> None:
    """Neue Preisstufe zur erkannten Aenderung.

    `open_amount_stage` wirft 409, wenn die neue Stufe VOR der laufenden beginnt.
    Das kann bei einem rueckdatiert angelegten Vertrag passieren; im Worker
    waere eine HTTPException ein roher Traceback. Der Preis selbst ist dann
    trotzdem richtig - nur der Verlauf bekommt keine zusaetzliche Stufe.
    """
    if outcome.chosen is None or outcome.new_amount_minor is None:
        return
    try:
        await contract_service.open_amount_stage(
            session,
            household_id,
            contract.contract_id,
            amount_minor=outcome.new_amount_minor,
            valid_from=outcome.chosen.candidate.booked_date,
            change_reason="detected_change",
            source_transaction_id=outcome.chosen.candidate.transaction_id,
        )
    except HTTPException:
        logger.warning(
            "Preisstufe fuer Vertrag %s zum %s liegt vor der laufenden Stufe "
            "und wurde uebersprungen.",
            contract.contract_id,
            outcome.chosen.candidate.booked_date,
        )


# ---------------------------------------------------------------------------
# Nachtragen bereits abgeschriebener Perioden
# ---------------------------------------------------------------------------


async def load_open_missing_due_dates(
    session: AsyncSession, household_id: UUID, contract_id: UUID
) -> list[date]:
    """Die juengsten offenen Fehlperioden eines Vertrags, chronologisch.

    Absteigend geholt und danach gedreht: bei einem lange vernachlaessigten
    Vertrag sind die juengsten Luecken die, fuer die ueberhaupt noch ein
    Kontoauszug nachgereicht wird.
    """
    result = await session.execute(
        text(
            "select due_date from contract_occurrences "
            "where household_id = :household_id and contract_id = :contract_id "
            "and status = 'missing' and transaction_id is null "
            "order by due_date desc limit :limit"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(contract_id),
            "limit": _MAX_REPAIR_PERIODS,
        },
    )
    return sorted(cast(date, row.due_date) for row in result)


async def repair_missing_periods(
    session: AsyncSession,
    household_id: UUID,
    contract: ContractSpec,
    *,
    claimed: set[UUID],
) -> tuple[ContractSpec, int]:
    """Traegt nachgereichte Zahlungen in bereits abgeschriebene Perioden nach.

    Ohne diesen Rueckblick waere der Motor rein vorwaertsgerichtet: `window_for`
    liegt ausschliesslich um `next_due_date`, und das steht nach einer
    Fehlperiode schon eine Periode weiter. Eine Abbuchung, die der Nutzer erst
    nach Ablauf von `tolerance_days` importiert - bei monatlichem
    Kontoauszugsimport der Regelfall -, koennte dann nie mehr zugeordnet werden,
    und der Fehlzaehler liefe bis zum automatischen Ende hoch, obwohl jede
    einzelne Zahlung als Buchung in der Datenbank steht.

    Bewusst OHNE Preisstufe: eine rueckwirkend erkannte Betragsaenderung laege
    vor der laufenden Stufe, und `open_amount_stage` lehnt das mit 409 ab (siehe
    `_open_price_stage`). Der Sollbetrag bleibt deshalb unangetastet - die
    Faelligkeit wird nur ihrer Buchung zugeordnet.

    Liefert den fortgeschriebenen Spec und die Zahl der nachgetragenen Perioden.
    """
    due_dates = await load_open_missing_due_dates(session, household_id, contract.contract_id)
    if not due_dates:
        return contract, 0

    booked_dates: list[date] = []
    repaired_dues: list[date] = []
    for due in due_dates:
        probe = replace(contract, next_due_date=due)
        window_from, window_to = window_for(probe)
        candidates = await fetch_candidates(
            session,
            household_id,
            probe,
            window_from,
            window_to,
            claimed_transaction_ids=claimed,
        )
        chosen = pick_best_match(probe, candidates, due)
        if chosen is None:
            continue
        written = await _write_occurrence(
            session,
            household_id,
            contract.contract_id,
            due_date=due,
            occurrence_status="matched",
            chosen=chosen,
        )
        if not written:
            continue
        claimed.add(chosen.candidate.transaction_id)
        booked_dates.append(chosen.candidate.booked_date)
        repaired_dues.append(due)

    if not booked_dates:
        return contract, 0

    missed = max(0, contract.missed_periods - len(repaired_dues))
    await session.execute(
        text(
            "update contracts set missed_periods = :missed_periods, "
            "first_seen_date = least(first_seen_date, cast(:first_seen as date)), "
            "last_seen_date = greatest(last_seen_date, cast(:last_seen as date)), "
            "last_matched_at = now() "
            "where id = :contract_id and household_id = :household_id"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(contract.contract_id),
            "missed_periods": missed,
            "first_seen": min(booked_dates),
            "last_seen": max(booked_dates),
        },
    )

    # Nur melden, wenn der Vertrag tatsaechlich als saeumig gefuehrt wurde -
    # sonst stuende nach jedem nachgereichten Auszug ein Ereignis im Verlauf,
    # das gar keine Unterbrechung beschreibt.
    if contract.missed_periods > 0:
        await contract_service.record_event(
            session,
            household_id,
            contract.contract_id,
            "payment_resumed",
            occurred_on=max(booked_dates),
            payload={
                "due_date": str(max(repaired_dues)),
                "missed_periods": contract.missed_periods,
                "repaired_periods": len(repaired_dues),
                # Unterscheidet die nachgereichte Zahlung von einer, die im
                # laufenden Fenster wieder ankam.
                "reason": "late_booking",
            },
        )

    return replace(contract, missed_periods=missed), len(repaired_dues)


# ---------------------------------------------------------------------------
# Laeufe
# ---------------------------------------------------------------------------


def _arbitrate(
    specs: Mapping[UUID, ContractSpec],
    candidates: Mapping[UUID, list[CandidateTx]],
    proposals: Mapping[UUID, ScoredCandidate],
) -> dict[UUID, ScoredCandidate]:
    """Loest alle Doppelzuordnungen einer Runde auf.

    Verlierer duerfen erneut waehlen; die Schleife endet spaetestens, wenn jeder
    Vertrag einmal verloren hat.
    """
    current = dict(proposals)
    for _ in range(len(specs) + 1):
        winners, losers = resolve_conflicts(current)
        if not losers:
            return winners
        taken = {scored.candidate.transaction_id for scored in winners.values()}
        current = dict(winners)
        for contract_id in losers:
            spec = specs[contract_id]
            remaining = [
                candidate
                for candidate in candidates[contract_id]
                if candidate.transaction_id not in taken
            ]
            fallback = pick_best_match(spec, remaining, spec.next_due_date)
            if fallback is not None:
                current[contract_id] = fallback
    winners, _ = resolve_conflicts(current)
    return winners


async def _data_horizons(
    session: AsyncSession, household_id: UUID
) -> tuple[bool, date | None, dict[UUID, date]]:
    """Bis zu welchem Buchungstag reichen die bereits importierten Daten?

    Ein fehlender Treffer ist nur dann eine belastbare Fehlzahlung, wenn fuer
    das betreffende Konto bereits Daten NACH dem Toleranzfenster vorliegen.
    Haushalte ohne abgeschlossenen Import behalten das bisherige Verhalten;
    sie koennen ihre Buchungen vollstaendig manuell pflegen.
    """
    has_completed_import = bool(
        (
            await session.execute(
                text(
                    "select exists(select 1 from import_runs "
                    "where household_id = :household_id and status = 'completed')"
                ),
                {"household_id": str(household_id)},
            )
        ).scalar_one()
    )
    if not has_completed_import:
        return False, None, {}

    result = await session.execute(
        text(
            "select account_id, max(booked_date) as data_through "
            "from transactions where household_id = :household_id and not is_archived "
            "group by account_id"
        ),
        {"household_id": str(household_id)},
    )
    by_account: dict[UUID, date] = {}
    household_through: date | None = None
    for row in result:
        if row.data_through is None:
            continue
        through = cast(date, row.data_through)
        account_id = UUID(str(row.account_id))
        by_account[account_id] = through
        if household_through is None or through > household_through:
            household_through = through
    return True, household_through, by_account


def _data_through_for(
    spec: ContractSpec,
    *,
    has_completed_import: bool,
    household_through: date | None,
    by_account: Mapping[UUID, date],
) -> date | None:
    if not has_completed_import:
        return None
    # `date.min` bedeutet: Es gibt einen Importbetrieb, fuer dieses Konto aber
    # noch keinen einzigen belastbaren Buchungstag. Das ist keine Freigabe,
    # bis heute Fehlperioden zu erzeugen.
    if spec.account_id is not None:
        return by_account.get(spec.account_id, date.min)
    return household_through or date.min


async def _rewind_unverified_missing_periods(
    session: AsyncSession,
    household_id: UUID,
    spec: ContractSpec,
    *,
    data_through: date | None,
) -> ContractSpec:
    """Nimmt voreilige Fehlperioden aus aelteren Motorversionen zurueck.

    Ohne diesen Schritt wuerde die neue Datenstandsgrenze nur kuenftige Fehler
    verhindern. Eine bereits als `missing` gespeicherte August-Faelligkeit
    bliebe sichtbar und der Vertrag stuende weiter mit erhoehtem Fehlzaehler
    da. Bestaetigte und uebersprungene Perioden werden nie angefasst.
    """
    if data_through is None:
        return spec

    rows = (
        await session.execute(
            text(
                "select due_date, status from contract_occurrences "
                "where household_id = :household_id and contract_id = :contract_id "
                "order by due_date desc"
            ),
            {
                "household_id": str(household_id),
                "contract_id": str(spec.contract_id),
            },
        )
    ).all()
    invalid_due_dates = [
        cast(date, row.due_date)
        for row in rows
        if row.status == "missing"
        and data_through <= row.due_date + timedelta(days=spec.tolerance_days)
    ]
    if not invalid_due_dates:
        return spec

    invalid_set = set(invalid_due_dates)
    remaining = [row for row in rows if row.due_date not in invalid_set]
    missed_periods = 0
    for row in remaining:
        if row.status != "missing":
            break
        missed_periods += 1

    await session.execute(
        text(
            "delete from contract_occurrences "
            "where household_id = :household_id and contract_id = :contract_id "
            "and due_date = any(cast(:due_dates as date[])) and status = 'missing'"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(spec.contract_id),
            "due_dates": invalid_due_dates,
        },
    )
    await session.execute(
        text(
            "delete from contract_events "
            "where household_id = :household_id and contract_id = :contract_id "
            "and kind = 'payment_missing' "
            "and payload->>'due_date' = any(cast(:due_dates as text[]))"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(spec.contract_id),
            "due_dates": [item.isoformat() for item in invalid_due_dates],
        },
    )
    rewound_due = min(min(invalid_due_dates), spec.next_due_date)
    await session.execute(
        text(
            "update contracts set next_due_date = :next_due_date, "
            "missed_periods = :missed_periods "
            "where household_id = :household_id and id = :contract_id "
            "and status in ('active', 'cancelled')"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(spec.contract_id),
            "next_due_date": rewound_due,
            "missed_periods": missed_periods,
        },
    )
    return replace(spec, next_due_date=rewound_due, missed_periods=missed_periods)


async def _run(
    session: AsyncSession,
    household_id: UUID,
    specs: Sequence[ContractSpec],
    *,
    today: date,
    auto_end_after_missed: str,
) -> tuple[_Counters, dict[UUID, ContractSpec]]:
    """Rundenweiser Lauf: je Runde schlaegt jeder offene Vertrag seinen besten
    Kandidaten vor, die Arbitrierung entscheidet, danach wird geschrieben."""
    claimed: set[UUID] = set()
    counters = _Counters()

    has_completed_import, household_through, by_account = await _data_horizons(
        session, household_id
    )
    data_through_by_contract = {
        spec.contract_id: _data_through_for(
            spec,
            has_completed_import=has_completed_import,
            household_through=household_through,
            by_account=by_account,
        )
        for spec in specs
    }

    # Erst zurueck, dann vorwaerts. Zwei Gruende: eine nachgereichte Zahlung
    # gehoert in ihre eigene Periode und nicht in die naechste offene, und der
    # dabei zurueckgesetzte Fehlzaehler entscheidet mit, ob die Automatik in
    # diesem Lauf ueberhaupt beenden darf.
    repaired_specs: list[ContractSpec] = []
    for spec in specs:
        spec = await _rewind_unverified_missing_periods(
            session,
            household_id,
            spec,
            data_through=data_through_by_contract[spec.contract_id],
        )
        advanced_spec, repaired = await repair_missing_periods(
            session, household_id, spec, claimed=claimed
        )
        counters.add_repair(repaired, resumed=repaired > 0 and spec.missed_periods > 0)
        repaired_specs.append(advanced_spec)

    open_specs: dict[UUID, ContractSpec] = {
        spec.contract_id: spec for spec in repaired_specs
    }
    final: dict[UUID, ContractSpec] = dict(open_specs)
    rounds: dict[UUID, int] = dict.fromkeys(open_specs, 0)
    # Einmal zu Beginn festgehalten: waehrend eines Nachholaufs waechst der
    # Fehlzaehler Runde um Runde, und die Frage "laeuft der noch?" haette nie in
    # der Inbox gestanden, wenn dieselbe Runde ihn schon beenden duerfte.
    auto_end_allowed: dict[UUID, bool] = {
        contract_id: spec.missed_periods >= _REVIEW_MIN_MISSED_PERIODS
        for contract_id, spec in open_specs.items()
    }

    while open_specs:
        candidates: dict[UUID, list[CandidateTx]] = {}
        proposals: dict[UUID, ScoredCandidate] = {}
        for contract_id, spec in open_specs.items():
            # Ein Vertrag am Ende seiner Laufzeit verwirft jeden Kandidaten
            # (match_contract steigt vor der Bewertung aus). Boete er trotzdem
            # mit, koennte er die Buchung einem noch laufenden Vertrag
            # wegnehmen und ihm eine erfundene Fehlperiode eintragen.
            if is_term_reached(spec, today):
                candidates[contract_id] = []
                continue
            window_from, window_to = window_for(spec)
            found = await fetch_candidates(
                session,
                household_id,
                spec,
                window_from,
                window_to,
                claimed_transaction_ids=claimed,
            )
            candidates[contract_id] = found
            best = pick_best_match(spec, found, spec.next_due_date)
            if best is not None:
                proposals[contract_id] = best

        winners = _arbitrate(open_specs, candidates, proposals)

        next_open: dict[UUID, ContractSpec] = {}
        for contract_id, spec in open_specs.items():
            chosen = winners.get(contract_id)
            outcome = match_contract(
                spec,
                [chosen.candidate] if chosen is not None else [],
                today,
                auto_end_after_missed=auto_end_after_missed,
                allow_auto_end=auto_end_allowed[contract_id],
                data_through=data_through_by_contract[contract_id],
            )
            # Bewusst `outcome.chosen` statt `chosen`: hat der Vertrag den
            # Kandidaten verworfen, bleibt die Buchung fuer die uebrigen
            # Vertraege dieses Laufs verfuegbar.
            if outcome.chosen is not None:
                claimed.add(outcome.chosen.candidate.transaction_id)

            advanced = await apply_outcome(session, household_id, spec, outcome)
            final[contract_id] = advanced
            counters.add(outcome)
            rounds[contract_id] += 1

            if outcome.kind in ("pending", "term_reached"):
                continue
            if advanced.status not in RECONCILE_STATUSES:
                continue
            # Kein Fortschritt heisst: die Faelligkeit konnte nicht geschrieben
            # werden. Weiterlaufen wuerde dieselbe Runde ewig wiederholen.
            if advanced.next_due_date == spec.next_due_date:
                continue
            if rounds[contract_id] >= _MAX_CATCHUP_PERIODS:
                continue
            next_open[contract_id] = advanced

        open_specs = next_open

    return counters, final


async def reconcile_contract(
    session: AsyncSession,
    household_id: UUID,
    contract: ContractSpec,
    *,
    today: date | None = None,
    auto_end_after_missed: str | None = None,
) -> ReconcileOutcome:
    """Gleicht einen einzelnen Vertrag ueber alle offenen Perioden ab."""
    day = today or date.today()
    setting = auto_end_after_missed
    if setting is None:
        setting = (await contract_service.get_settings(session, household_id)).auto_end_after_missed

    counters, final = await _run(
        session, household_id, [contract], today=day, auto_end_after_missed=setting
    )
    result = final[contract.contract_id]
    return ReconcileOutcome(
        contract_id=contract.contract_id,
        periods_processed=counters.periods,
        matched_count=counters.matched,
        price_change_count=counters.price_change,
        missing_count=counters.missing,
        resumed_count=counters.resumed,
        ended_count=counters.ended,
        installments_completed_count=counters.installments_completed,
        final_status=result.status,
        next_due_date=result.next_due_date,
        missed_periods=result.missed_periods,
    )


async def reconcile_household(
    session: AsyncSession,
    household_id: UUID,
    *,
    today: date | None = None,
    contract_ids: Sequence[UUID] | None = None,
) -> ReconcileResult:
    """Vollstaendiger Abgleichslauf fuer einen Haushalt.

    Idempotent: ein zweiter Lauf findet keine unverbrauchten Buchungen mehr und
    schreibt dieselben Werte. Der wertbasierte Trigger `bump_contract_version`
    zaehlt `version` dabei nicht hoch, weil `last_matched_at` und `updated_at`
    vom Vergleich ausgenommen sind und dieses Modul `version` nie selbst setzt.
    """
    day = today or date.today()
    settings = await contract_service.get_settings(session, household_id)
    specs = await load_match_specs(session, household_id, contract_ids=contract_ids)

    counters, _ = await _run(
        session,
        household_id,
        specs,
        today=day,
        auto_end_after_missed=settings.auto_end_after_missed,
    )
    return ReconcileResult(
        household_id=household_id,
        contracts_examined=len(specs),
        matched_count=counters.matched,
        price_change_count=counters.price_change,
        missing_count=counters.missing,
        resumed_count=counters.resumed,
        ended_count=counters.ended,
        installments_completed_count=counters.installments_completed,
    )


# ---------------------------------------------------------------------------
# Rueckwirkender Erstlauf
# ---------------------------------------------------------------------------


def _previous_due_date(
    current: date, frequency: str, *, due_rule: str = "day_of_month", due_day: int | None = None
) -> date:
    """Spiegelbild von `contract_service.next_due_date`.

    Der Backfill muss die Faelligkeiten der Vergangenheit auf demselben Raster
    treffen, auf dem der Vorwaertslauf sie erzeugt - sonst zeigt die Detailseite
    zwei versetzte Reihen derselben Zahlungen.
    """
    if frequency == "weekly":
        return current - timedelta(days=7)

    total = current.month - 1 - _MONTHS_PER_PERIOD[frequency]
    year = current.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    if due_rule == "month_end":
        return date(year, month, last_day)
    target_day = due_day if due_day is not None else current.day
    return date(year, month, min(target_day, last_day))


def _months_back(value: date, months: int) -> date:
    total = value.month - 1 - months
    year = value.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(value.day, last_day))


@dataclass(frozen=True)
class _AmountStage:
    amount_minor: int
    valid_from: date
    source_transaction_id: UUID


async def backfill_contract(
    session: AsyncSession,
    household_id: UUID,
    contract_id: UUID,
    *,
    today: date | None = None,
) -> BackfillResult:
    """Rueckwirkender Erstlauf beim Anlegen oder Bestaetigen eines Vertrags.

    Ohne ihn ist die Detailseite eines frisch angelegten Vertrags leer, obwohl
    die Buchungen seit Jahren im Konto stehen.

    Bewusst OHNE Ereignisse und ohne Fehlzaehler: ein gerade angelegter Vertrag
    darf keinen Alarmverlauf mitbringen. Aus demselben Grund entstehen
    'missing'-Faelligkeiten nur zwischen dem ersten und dem letzten Treffer -
    davor gab es den Vertrag schlicht nicht.
    """
    day = today or date.today()
    contract = await contract_service.load_contract(session, household_id, contract_id)
    if contract is None:
        return BackfillResult(contract_id, 0, 0, None, None)
    spec = spec_from_contract(contract)

    due_dates = _backfill_due_dates(spec, day)
    if not due_dates:
        return BackfillResult(contract_id, 0, 0, None, None)

    hits, stages = await _collect_backfill_hits(session, household_id, spec, due_dates)
    if not hits:
        return BackfillResult(contract_id, 0, 0, None, None)

    occurrences = await _write_backfill_occurrences(
        session, household_id, spec, due_dates, hits
    )
    stage_count = await _write_backfill_stages(session, household_id, contract_id, stages)

    first_seen = min(item.candidate.booked_date for item in hits.values())
    last_seen = max(item.candidate.booked_date for item in hits.values())
    await session.execute(
        text(
            "update contracts set "
            "first_seen_date = least(first_seen_date, cast(:first_seen as date)), "
            "last_seen_date = greatest(last_seen_date, cast(:last_seen as date)) "
            "where id = :contract_id and household_id = :household_id"
        ),
        {
            "household_id": str(household_id),
            "contract_id": str(contract_id),
            "first_seen": first_seen,
            "last_seen": last_seen,
        },
    )

    return BackfillResult(
        contract_id=contract_id,
        occurrences_written=occurrences,
        amount_stages_written=stage_count,
        first_seen_date=first_seen,
        last_seen_date=last_seen,
    )


def _backfill_due_dates(spec: ContractSpec, today: date) -> list[date]:
    """Vergangene Faelligkeiten, chronologisch aufsteigend.

    Aufsteigend ist Pflicht: `open_amount_stage` und die Betragstreppe vertragen
    keine Stufe, die vor der bereits offenen beginnt.
    """
    floor = _months_back(today, _BACKFILL_MAX_MONTHS)
    dates: list[date] = []
    cursor = _previous_due_date(
        spec.next_due_date, spec.frequency, due_rule=spec.due_rule, due_day=spec.due_day
    )
    while cursor >= floor and len(dates) < _BACKFILL_MAX_TRANSACTIONS:
        dates.append(cursor)
        cursor = _previous_due_date(
            cursor, spec.frequency, due_rule=spec.due_rule, due_day=spec.due_day
        )
    dates.reverse()
    return dates


async def _collect_backfill_hits(
    session: AsyncSession,
    household_id: UUID,
    spec: ContractSpec,
    due_dates: Sequence[date],
) -> tuple[dict[date, ScoredCandidate], list[_AmountStage]]:
    """Sucht je vergangener Faelligkeit die passende Buchung.

    Der Sollbetrag wandert mit: aeltere Zahlungen liegen oft unter dem heutigen
    Preis, und ohne mitwanderndes Niveau fiele die halbe Geschichte durch die
    Betragspruefung.
    """
    hits: dict[date, ScoredCandidate] = {}
    stages: list[_AmountStage] = []
    claimed: set[UUID] = set()
    running = spec

    for due in due_dates:
        probe = replace(running, next_due_date=due)
        window_from, window_to = window_for(probe)
        candidates = await fetch_candidates(
            session,
            household_id,
            probe,
            window_from,
            window_to,
            claimed_transaction_ids=claimed,
            ignore_own_occurrences=True,
        )
        chosen = pick_best_match(probe, candidates, due)
        if chosen is None:
            continue

        hits[due] = chosen
        claimed.add(chosen.candidate.transaction_id)
        actual = abs(chosen.candidate.amount_minor)
        if not stages or abs(actual - stages[-1].amount_minor) > amount_tolerance_minor(
            stages[-1].amount_minor
        ):
            stages.append(
                _AmountStage(
                    amount_minor=actual,
                    valid_from=chosen.candidate.booked_date,
                    source_transaction_id=chosen.candidate.transaction_id,
                )
            )
            running = replace(
                running,
                amount_minor=actual,
                tolerance_amount_minor=amount_tolerance_minor(actual),
            )
        if len(hits) >= _BACKFILL_MAX_TRANSACTIONS:
            break

    return hits, stages


async def _write_backfill_occurrences(
    session: AsyncSession,
    household_id: UUID,
    spec: ContractSpec,
    due_dates: Sequence[date],
    hits: Mapping[date, ScoredCandidate],
) -> int:
    first_hit = min(hits)
    last_hit = max(hits)
    written = 0
    for due in due_dates:
        if due < first_hit or due > last_hit:
            continue
        chosen = hits.get(due)
        ok = await _write_occurrence(
            session,
            household_id,
            spec.contract_id,
            due_date=due,
            occurrence_status="matched" if chosen is not None else "missing",
            chosen=chosen,
        )
        if ok:
            written += 1
    return written


async def _write_backfill_stages(
    session: AsyncSession,
    household_id: UUID,
    contract_id: UUID,
    stages: Sequence[_AmountStage],
) -> int:
    """Baut die Betragstreppe der Vergangenheit.

    Bewusst NICHT ueber `open_amount_stage`: die beim Anlegen eroeffnete
    'initial'-Stufe beginnt heute, jede historische Stufe laege davor und wuerde
    dort mit 409 abgelehnt. Stattdessen werden die historischen Stufen mit
    eigenem `valid_to` vor der offenen Stufe eingehaengt; deren Betrag - die
    Angabe des Nutzers - bleibt unangetastet.
    """
    if not stages:
        return 0

    # Ein zweiter Backfill soll dieselbe Treppe erzeugen, nicht eine zweite.
    await session.execute(
        text(
            "delete from contract_amount_history where household_id = :household_id "
            "and contract_id = :contract_id and change_reason = 'backfill'"
        ),
        {"household_id": str(household_id), "contract_id": str(contract_id)},
    )

    open_row = (
        await session.execute(
            text(
                "select valid_from from contract_amount_history "
                "where household_id = :household_id and contract_id = :contract_id "
                "and valid_to is null"
            ),
            {"household_id": str(household_id), "contract_id": str(contract_id)},
        )
    ).first()
    open_from: date | None = open_row.valid_from if open_row is not None else None

    written = 0
    previous_amount: int | None = None
    for index, stage in enumerate(stages):
        if open_from is not None and stage.valid_from >= open_from:
            # Ab hier beschreibt die laufende Stufe denselben Zeitraum.
            break
        if index + 1 < len(stages):
            valid_to = stages[index + 1].valid_from - timedelta(days=1)
        elif open_from is not None:
            valid_to = open_from - timedelta(days=1)
        else:
            valid_to = None
        if valid_to is not None and valid_to < stage.valid_from:
            # Zwei Stufen am selben Tag ergaeben ein leeres Intervall und
            # verletzten den CHECK valid_to >= valid_from.
            continue

        await session.execute(
            text(
                "insert into contract_amount_history (household_id, contract_id, "
                "amount_minor, valid_from, valid_to, delta_minor, change_reason, "
                "source_transaction_id) values (:household_id, :contract_id, :amount_minor, "
                ":valid_from, cast(:valid_to as date), cast(:delta_minor as bigint), "
                "'backfill', cast(:source_transaction_id as uuid))"
            ),
            {
                "household_id": str(household_id),
                "contract_id": str(contract_id),
                "amount_minor": stage.amount_minor,
                "valid_from": stage.valid_from,
                "valid_to": valid_to,
                "delta_minor": (
                    None if previous_amount is None else stage.amount_minor - previous_amount
                ),
                "source_transaction_id": str(stage.source_transaction_id),
            },
        )
        previous_amount = stage.amount_minor
        written += 1

    return written
