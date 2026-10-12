"""Haushaltsweiter Reparaturlauf fuer Legacy-Vertraege.

docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 19.9 (P5.1b). Ausloeser: die
Uebernahme aus `recurring_rules`/`subscriptions` (0001_schema.sql) setzt
`source='confirmed'` anhand des Notiz-Praefixes, ruft aber keinen
Anwendungsservice auf - migrierte Vertraege bleiben ohne `contract_key` und
ohne `first_seen_date`/`last_seen_date` stehen. Damit kann weder die
schluesselbewusste Duplikaterkennung (Abschnitt 19.8) greifen noch die
Detailseite einen Verlauf zeigen.

Arbeitsweise als Kohorte pro Haushalt (nicht Vertrag fuer Vertrag): alle
Buchungen werden EINMAL mit dem Kern der Erkennung v2 zu kanonischen
Zahlungsreihen gebuendelt (`contract_detection_service.cluster_candidates`),
erst DANACH deterministisch gegen die bestehenden Legacy-Vertraege bewertet.
Nacheinander pro Vertrag zu arbeiten wuerde die Reihenfolge ergebnisbestimmend
machen: bei mehreren Vertraegen desselben Anbieters koennte der zuerst
verarbeitete Buchungen "wegschnappen", die eigentlich zu einem anderen
gehoeren (siehe Docstring von `contract_matching_service.fetch_candidates`
fuer dasselbe Problem beim laufenden Abgleich).

Nur eine eindeutige 1:1-Zuordnung (eine Preisstufe <-> genau ein Legacy-
Vertrag mit passendem Betrag) schreibt etwas. Bilden mehrere Legacy-Zeilen
zusammen genau eine kanonische Reihe (der Referenzfall: 49,95 EUR und
39,95 EUR sind zwei Legacy-Zeilen, aber eine Preistreppe), werden beide
einzeln angereichert und zusaetzlich als Merge-Vorschlag gemeldet - die
eigentliche Zusammenfuehrung bleibt eine bewusste Nutzeraktion (Leitsatz 3).
Nach der Anreicherung tragen beide denselben `contract_key`, wodurch sie die
schluesselbewusste Duplikaterkennung (P5.1a) danach von selbst als starken
Beleg auf der Vertraege-Seite anzeigt.

Idempotent: eine bereits anreicherte Zeile hat `first_seen_date is not null`
und faellt beim naechsten Lauf aus der Auswahl heraus. Ein manuell gesetzter
Schluessel (`contract_key_source = 'manual'`) wird nie angefasst (Leitsatz 5).

Buendelung (der Grund fuer die Umstellung)
------------------------------------------
Vertrag und Zahlungsreihe finden sich ueber den NORMALISIERTEN ANBIETERNAMEN;
`payee_id` ist ein zusaetzlicher Beleg, kein Ersatz. Die Erstfassung buendelte
umgekehrt: `payee_id`, wenn vorhanden, sonst der Name. Genau daran ist der Lauf
gegen die Migrationsdaten gescheitert - die SQL-Uebernahme in
0001_schema.sql kopiert `rr.payee_id` in den Vertrag, waehrend
`ContractCandidate.payee_id` aus den Buchungen stammt, und die tragen im
Bestand durchgehend `payee_id is null`. Die Vertragsseite landete damit im
`payee:`-Buendel, die Kandidatenseite im `name:`-Buendel, und die beiden
konnten sich strukturell nie treffen (gemessen: `contracts_scanned=26`,
`contracts_enriched=0`, 26-mal `no_matching_transactions`, obwohl die
Clusterung 25 passende Zahlungsreihen fand).

Ueber den Namen zu buendeln ist zulaessig, weil `payee_key` NICHT unscharf
vergleicht: er senkt nur die Schreibweise und ersetzt Satzzeichen durch
Leerzeichen. "Allianz Krankenversicherung AG" und "Allianz
Krankenversicherung" bleiben deshalb getrennt, ebenso "MaxEnergy GmbH" und
"Max Energy GmbH". Eine Praefixregel wie `_is_payee_name_variant` in der
Erkennung wird hier bewusst NICHT verwendet - sie wuerde zwei Anbieter mit
aehnlichem Namen zusammenziehen.

Weil die Buendelung damit lockerer ist als zuvor, muessen die Trennmerkmale aus
Abschnitt 19.9 Punkt 3 zusammenpassen, bevor eine Zuordnung entsteht: Konto,
Richtung, Frequenz, Faelligkeitstag, Vertragsschluessel und Betrag. Vorher
wurden davon nur Richtung und Betrag geprueft.

Wie viel diese Liste im Ernstfall wirklich traegt, ist allerdings nachgemessen
und geringer als sie aussieht. Im Referenzfall trennen FUENF der sechs
Merkmale nichts: alle drei Vertraege haengen am selben Konto, in derselben
Richtung, mit derselben Frequenz, ohne Vertragsschluessel, und der
Faelligkeitstag liegt innerhalb der Toleranz. Es trennt ausschliesslich der
Betrag. Die eigentliche Absicherung ist deshalb nicht die Merkmalsliste,
sondern die Eindeutigkeitsregel dahinter: passen zwei Legacy-Zeilen auf
dieselbe Preisstufe, greift `hits[0] if len(hits) == 1 else None` und es wird
GAR NICHTS zugeordnet. Der Fehlermodus ist "kein Treffer", nie "falscher
Treffer" - darauf ruht die Sicherheit, nicht auf den Merkmalen.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.contract_detection_service import (
    FREQUENCY_SPECS,
    AmountStep,
    ContractCandidate,
    cluster_all_household_transactions,
)
from app.services.contract_matching_service import (
    amount_tolerance_minor,
    normalize_purpose,
    payee_key,
)

# Deckungsgleich mit dem rueckwirkenden Erstlauf beim Bestaetigen eines
# Vertrags (contract_service Abschnitt 5.5): "hoechstens 36 Monate rueckwaerts".
_REPAIR_MONTHS_BACK: Final = 36

# `payees` wird mitgelesen, weil die Erkennung ihren Anbieternamen aus
# `coalesce(ai.payee_name, p.name, t.payee_label)` bildet: steht am Vertrag eine
# `payee_id`, ist `payees.name` die Schreibweise, die der Kandidat mit hoechster
# Wahrscheinlichkeit traegt - und nicht zwingend `provider_label`.
_LEGACY_ELIGIBLE_SQL: Final = """
select c.id, c.name, c.provider_label, c.payee_id, p.name as payee_name,
       c.account_id, c.direction, c.amount_minor, c.frequency,
       c.due_rule, c.due_day, c.tolerance_days,
       c.contract_key, c.contract_key_source
from contracts c
left join payees p on p.id = c.payee_id and p.household_id = c.household_id
where c.household_id = :household_id
  and c.merged_into_id is null
  and c.first_seen_date is null
  and c.last_seen_date is null
  and (c.legacy_recurring_rule_id is not null or c.legacy_subscription_id is not null)
order by c.id
"""


@dataclass(frozen=True)
class LegacyContractRow:
    id: UUID
    name: str
    provider_label: str | None
    payee_name: str | None
    payee_id: UUID | None
    account_id: UUID | None
    direction: str
    amount_minor: int
    frequency: str
    due_rule: str
    due_day: int | None
    tolerance_days: int
    contract_key: str | None
    contract_key_source: str | None


@dataclass(frozen=True)
class RepairedContract:
    contract_id: UUID
    name: str
    # `None` heisst: die Zahlungsreihe traegt keinen Vertragsschluessel. Der
    # Zeitraum wird trotzdem ergaenzt - er ist der Grund, warum die Detailseite
    # leer bleibt. Frueher stand hier ein leerer String, der als
    # `contract_key = ''` mit `contract_key_source = 'mandate'` in der Tabelle
    # landete: ein Schluessel, der keiner ist, aber wie ein belegter aussieht.
    contract_key: str | None
    contract_key_source: str | None
    first_seen_date: date
    last_seen_date: date
    occurrences_written: int


@dataclass(frozen=True)
class MergeSuggestion:
    target_id: UUID
    source_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class AmbiguousContract:
    contract_id: UUID
    name: str
    reason: str


@dataclass(frozen=True)
class SkippedContract:
    """Wegen manueller Hoheit uebersprungen (Abschnitt 19.9: das Ergebnis muss
    nennen, WELCHE Vertraege uebersprungen wurden, nicht nur wie viele)."""

    contract_id: UUID
    name: str
    reason: str


@dataclass
class LegacyRepairPlan:
    contracts_scanned: int = 0
    contracts_enriched: int = 0
    keys_enriched: int = 0
    ambiguous: int = 0
    skipped_manual: int = 0
    enriched_contracts: list[RepairedContract] = field(default_factory=list)
    merge_suggestions: list[MergeSuggestion] = field(default_factory=list)
    ambiguous_contracts: list[AmbiguousContract] = field(default_factory=list)
    skipped_manual_contracts: list[SkippedContract] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Buendelung: Anbietername als gemeinsamer Nenner, payee_id als Zusatzbeleg
# ---------------------------------------------------------------------------


def _name_bucket_key(label: str | None) -> str | None:
    key = payee_key(label)
    return f"name:{key}" if key else None


def _contract_bucket_keys(row: LegacyContractRow) -> tuple[str, ...]:
    """Alle Buendel, unter denen dieser Vertrag zu finden waere - staerkster
    Beleg zuerst.

    Der Name steht dreifach drin, weil die drei Quellen auseinanderlaufen
    duerfen: `payees.name` ist die Schreibweise, die die Erkennung bevorzugt,
    `provider_label` die aus der Migration uebernommene, `name` der Rueckfall
    fuer Zeilen ohne verknuepften Empfaenger (bei denen die Uebernahme
    `provider_label` leer laesst). Zugeordnet wird der Vertrag spaeter zu GENAU
    EINEM davon (`_pick_bucket`) - in mehreren Buendeln zugleich koennte er
    dieselbe Zahlungsreihe zweimal beanspruchen.
    """
    keys: list[str] = []
    if row.payee_id is not None:
        keys.append(f"payee:{row.payee_id}")
    for label in (row.payee_name, row.provider_label, row.name):
        key = _name_bucket_key(label)
        if key is not None and key not in keys:
            keys.append(key)
    # Ein Vertrag ohne verwertbaren Namen bekommt sein eigenes Buendel, statt
    # sich ein leeres mit allen anderen namenlosen Zeilen zu teilen.
    return tuple(keys) if keys else (f"contract:{row.id}",)


def _candidate_bucket_keys(candidate: ContractCandidate) -> tuple[str, ...]:
    """Dieselben Buendel von der Kandidatenseite aus.

    `candidate.payee_key` ist bereits `payee_key(payee_label)` - dieselbe
    Normalisierung wie oben, sonst haetten die beiden Seiten wieder getrennte
    Namensraeume.
    """
    keys: list[str] = []
    if candidate.payee_id is not None:
        keys.append(f"payee:{candidate.payee_id}")
    if candidate.payee_key:
        keys.append(f"name:{candidate.payee_key}")
    return tuple(keys)


def _bucket_order(bucket: str) -> tuple[int, str]:
    """Verarbeitungsreihenfolge der Buendel.

    Ein Kandidat haengt an bis zu zwei Buendeln (Empfaenger und Name). Treffen
    zwei verschiedene Legacy-Zeilen aus verschiedenen Buendeln auf dieselbe
    Zahlungsreihe, entscheidet der staerkere Beleg zuerst - sonst haenge das
    Ergebnis an der Reihenfolge eines Dictionarys.
    """
    return (0 if bucket.startswith("payee:") else 1, bucket)


def _pick_bucket(row: LegacyContractRow, available: dict[str, list[ContractCandidate]]) -> str:
    for key in _contract_bucket_keys(row):
        if key in available:
            return key
    return _contract_bucket_keys(row)[0]


# ---------------------------------------------------------------------------
# Trennmerkmale (Abschnitt 19.9 Punkt 3)
# ---------------------------------------------------------------------------

# Reihenfolge = Rang: je weiter hinten, desto weiter ist der Kandidat gekommen.
# Der aussagekraeftigste Grund fuer eine ausgebliebene Zuordnung ist der des
# knappsten Fehlschlags, nicht der des ersten geprueften Kandidaten.
_REJECT_REASONS: Final[tuple[str, ...]] = (
    "direction_mismatch",
    "frequency_mismatch",
    "different_payee_link",
    "account_mismatch",
    "contract_key_mismatch",
    "no_amount_match",
    "due_day_mismatch",
)

# Der Faelligkeitstag wandert: die Bank bucht auf den naechsten Bankarbeitstag.
# Als Untergrenze dient dieselbe Toleranz, mit der die Rhythmuserkennung diese
# Frequenz misst (monatlich 4 Tage, vierteljaehrlich 10, jaehrlich 21) - ein
# eigener Wert hier wuerde beides auseinanderlaufen lassen. Steht am Vertrag
# eine groessere `tolerance_days`, gewinnt die.
_DUE_DAY_TOLERANCE_BY_FREQUENCY: Final[dict[str, int]] = {
    str(name): spec.tolerance_days for name, spec in FREQUENCY_SPECS.items()
}
_DEFAULT_DUE_DAY_TOLERANCE_DAYS: Final = 4
_MONTH_CYCLE_DAYS: Final = FREQUENCY_SPECS["monthly"].period_days


def _amount_matches(legacy_amount_minor: int, step_amount_minor: int) -> bool:
    # contracts.amount_minor ist immer positiv (Vertragskonvention), aber
    # AmountStep.amount_minor traegt das Rohvorzeichen der Buchung (Ausgaben
    # negativ) - siehe DetectionTx/AmountStep in contract_detection_service.
    # Ohne den Betrag hier zu normieren, verglich diese Funktion 9990 mit
    # -9990 und fand nie eine eindeutige Zuordnung.
    step_amount_abs = abs(step_amount_minor)
    return abs(legacy_amount_minor - step_amount_abs) <= amount_tolerance_minor(step_amount_abs)


def _cyclic_day_distance(left: float, right: float) -> float:
    """Abstand zweier Monatstage, zyklisch gerechnet.

    Ohne Zyklus waeren der 31. und der 1. dreissig Tage auseinander, obwohl sie
    derselbe Zahltag in benachbarten Monaten sind - dieselbe Ueberlegung wie in
    `contract_detection_service._payday_offsets`.
    """
    half = _MONTH_CYCLE_DAYS / 2.0
    return abs(((left - right + half) % _MONTH_CYCLE_DAYS) - half)


def _due_day_matches(row: LegacyContractRow, step: AmountStep) -> bool:
    """Passt der Faelligkeitstag des Vertrags zu den Buchungstagen der Stufe?

    Verglichen wird gegen den MEDIAN der Buchungstage und nicht gegen den
    Mittelwert: ein einzelner ans Monatsende verschobener Zahltag soll die
    Reihe nicht verschieben.

    Nicht geprueft wird, wo `contracts.due_day` gar keinen Kalendertag meint:
    bei `nth_business_day` ist es eine laufende Nummer, bei woechentlichen
    Vertraegen ist ein Monatstag ohne Aussage.
    """
    if row.due_day is None or row.due_rule == "nth_business_day" or row.frequency == "weekly":
        return True
    days = [float(tx.booked_date.day) for tx in step.transactions]
    if not days:
        return True
    tolerance = max(
        row.tolerance_days,
        _DUE_DAY_TOLERANCE_BY_FREQUENCY.get(row.frequency, _DEFAULT_DUE_DAY_TOLERANCE_DAYS),
    )
    return _cyclic_day_distance(statistics.median(days), float(row.due_day)) <= tolerance


def _account_matches(row: LegacyContractRow, candidate: ContractCandidate) -> bool:
    """Laeuft die Zahlungsreihe ueber das Konto des Vertrags?

    `candidate.account_id` ist bereits `None`, sobald die Reihe ueber mehrere
    Konten laeuft - deshalb wird hier ueber die Buchungen selbst geprueft und
    nicht ueber das abgeleitete Feld.
    """
    if row.account_id is None:
        return True
    accounts = {tx.account_id for tx in candidate.transactions}
    return not accounts or row.account_id in accounts


def _key_conflict(row: LegacyContractRow, candidate: ContractCandidate) -> bool:
    """Zwei VERSCHIEDENE vorhandene Schluessel schliessen einander aus.

    Dieselbe Regel wie bei den schluesselbewussten Duplikatvorschlaegen
    (Abschnitt 19.8). Normalisiert wird auf beiden Seiten, sonst scheitert eine
    Mandatsreferenz an ihrer eigenen Schreibweise mit Bindestrich.
    """
    left = normalize_purpose(row.contract_key)
    right = normalize_purpose(candidate.contract_key)
    return bool(left) and bool(right) and left != right


def _payee_conflict(row: LegacyContractRow, candidate: ContractCandidate) -> bool:
    """Zwei VERSCHIEDENE verknuepfte Empfaenger schliessen einander aus.

    Der Gegenpol zur gelockerten Buendelung: sobald beide Seiten eine
    `payee_id` tragen, ist sie der staerkere Beleg und darf einen
    Namensgleichklang ueberstimmen. Fehlt sie auf einer Seite - der heutige
    Bestand -, sagt sie nichts und verhindert nichts.
    """
    return (
        row.payee_id is not None
        and candidate.payee_id is not None
        and row.payee_id != candidate.payee_id
    )


def _candidate_conflict(row: LegacyContractRow, candidate: ContractCandidate) -> str | None:
    """Ausschlussgruende, die fuer den ganzen Kandidaten gelten. `None` = passt."""
    if candidate.direction != row.direction:
        return "direction_mismatch"
    if candidate.frequency != row.frequency:
        return "frequency_mismatch"
    if _payee_conflict(row, candidate):
        return "different_payee_link"
    if not _account_matches(row, candidate):
        return "account_mismatch"
    if _key_conflict(row, candidate):
        return "contract_key_mismatch"
    return None


def _step_conflict(row: LegacyContractRow, step: AmountStep) -> str | None:
    """Ausschlussgruende gegen EINE Preisstufe. `None` = passt."""
    if not _amount_matches(row.amount_minor, step.amount_minor):
        return "no_amount_match"
    if not _due_day_matches(row, step):
        return "due_day_mismatch"
    return None


def _fits(row: LegacyContractRow, candidate: ContractCandidate, step: AmountStep) -> bool:
    return _candidate_conflict(row, candidate) is None and _step_conflict(row, step) is None


def _candidate_order(candidate: ContractCandidate) -> tuple[int, str, str]:
    """Verarbeitungsreihenfolge der Kandidaten INNERHALB eines Buendels.

    Die Clusterung liefert die Kandidaten ungeordnet, `matched_ids` waechst aber
    waehrend der Schleife: verbraucht ein Kandidat eine Legacy-Zeile, kann eine
    fuer den naechsten sonst mehrdeutige Stufe eindeutig werden. Ohne feste
    Reihenfolge haengt das Ergebnis damit an der Iterationsreihenfolge eines
    Dictionaries - genau das, was 19.9 ausschliesst.

    Mehrstufige Kandidaten zuerst: sie sind die spezifischere Aussage und
    beanspruchen mehrere Legacy-Zeilen gemeinsam. `source_transaction_id`
    schliesst zuletzt jede verbleibende Zweideutigkeit.
    """
    return (
        -len(candidate.amount_steps),
        str(candidate.first_date),
        str(candidate.source_transaction_id),
    )


def _rejection_reason(
    row: LegacyContractRow, bucket_candidates: list[ContractCandidate]
) -> str:
    """Der aussagekraeftigste Grund, warum keine Zahlungsreihe passt."""
    failures: list[str] = []
    for candidate in bucket_candidates:
        conflict = _candidate_conflict(row, candidate)
        if conflict is not None:
            failures.append(conflict)
            continue
        failures.extend(
            reason
            for step in candidate.amount_steps
            if (reason := _step_conflict(row, step)) is not None
        )
    if not failures:
        return "no_unique_candidate_match"
    return max(failures, key=_REJECT_REASONS.index)


async def _load_eligible_contracts(
    session: AsyncSession, household_id: UUID
) -> tuple[list[LegacyContractRow], list[LegacyContractRow]]:
    """Liefert (reparierbare Zeilen, wegen manuellem Schluessel uebersprungene)."""
    rows = (
        await session.execute(
            text(_LEGACY_ELIGIBLE_SQL), {"household_id": str(household_id)}
        )
    ).all()
    eligible: list[LegacyContractRow] = []
    skipped: list[LegacyContractRow] = []
    for row in rows:
        item = LegacyContractRow(
            id=UUID(str(row.id)),
            name=str(row.name),
            provider_label=row.provider_label,
            payee_name=row.payee_name,
            payee_id=UUID(str(row.payee_id)) if row.payee_id is not None else None,
            account_id=UUID(str(row.account_id)) if row.account_id is not None else None,
            direction=str(row.direction),
            amount_minor=int(row.amount_minor),
            frequency=str(row.frequency),
            due_rule=str(row.due_rule),
            due_day=int(row.due_day) if row.due_day is not None else None,
            tolerance_days=int(row.tolerance_days),
            contract_key=row.contract_key,
            contract_key_source=row.contract_key_source,
        )
        (skipped if row.contract_key_source == "manual" else eligible).append(item)
    return eligible, skipped


@dataclass(frozen=True)
class _Assignment:
    """Interne Erweiterung von `RepairedContract` um die Buchungen der
    zugehoerigen Preisstufe - `analyze_household` gibt nur die oeffentliche
    Zusammenfassung zurueck, `apply_household` braucht zusaetzlich die
    Buchungen selbst, um `contract_occurrences` zu schreiben."""

    contract_id: UUID
    candidate: ContractCandidate
    step: AmountStep


async def _analyze(
    session: AsyncSession, household_id: UUID
) -> tuple[LegacyRepairPlan, list[_Assignment]]:
    eligible, skipped_manual = await _load_eligible_contracts(session, household_id)
    plan = LegacyRepairPlan(
        contracts_scanned=len(eligible) + len(skipped_manual),
        skipped_manual=len(skipped_manual),
        skipped_manual_contracts=[
            SkippedContract(row.id, row.name, "manual_contract_key") for row in skipped_manual
        ],
    )
    assignments: list[_Assignment] = []
    if not eligible:
        return plan, assignments

    candidates = await cluster_all_household_transactions(
        session, household_id, months_back=_REPAIR_MONTHS_BACK
    )

    candidates_by_bucket: dict[str, list[ContractCandidate]] = defaultdict(list)
    for candidate in candidates:
        for key in _candidate_bucket_keys(candidate):
            candidates_by_bucket[key].append(candidate)

    legacy_by_bucket: dict[str, list[LegacyContractRow]] = defaultdict(list)
    for row in eligible:
        legacy_by_bucket[_pick_bucket(row, candidates_by_bucket)].append(row)

    matched_ids: set[UUID] = set()
    # Ein Kandidat haengt an bis zu zwei Buendeln und darf trotzdem nur EINE
    # Zuordnung erzeugen - sonst schriebe der Apply-Lauf dieselben Buchungen
    # zwei Vertraegen gut.
    used_candidates: set[UUID] = set()
    # Gruende, die sich erst aus der Gesamtbetrachtung eines Kandidaten ergeben
    # und die `_rejection_reason` deshalb nicht selbst herleiten kann.
    structural_reason: dict[UUID, str] = {}

    for bucket in sorted(legacy_by_bucket, key=_bucket_order):
        legacy_rows = legacy_by_bucket[bucket]
        bucket_candidates = candidates_by_bucket.get(bucket, [])

        for candidate in sorted(bucket_candidates, key=_candidate_order):
            if candidate.source_transaction_id in used_candidates:
                continue
            steps = candidate.amount_steps
            hits_per_step = [
                [
                    row
                    for row in legacy_rows
                    if row.id not in matched_ids and _fits(row, candidate, step)
                ]
                for step in steps
            ]
            step_matches: list[LegacyContractRow | None] = [
                hits[0] if len(hits) == 1 else None for hits in hits_per_step
            ]
            per_row = Counter(item.id for item in step_matches if item is not None)
            # HIER STAND EINE REGEL, DIE VORHANDENE DATEN BESCHAEDIGT HAT.
            # Versuch war: trifft genau EIN Legacy-Vertrag irgendeine Preisstufe
            # eines Kandidaten, soll er den ganzen Kandidaten bekommen (Fall
            # "ein Vertrag, mehrere Preisstufen" - ein Abo 9,99 -> 2,99).
            # Angewendet hat sie einen guenstigen Abo-Vertrag
            # (4,99 EUR) rund 40 fremde Buchungen einsammeln lassen: 99,90,
            # 250,00, 100,00 und weitere. Grund: `split_amount_steps`
            # liefert bei einem unsauberen Buendel eben KEINE Preistreppe,
            # sondern beliebige Betragsniveaus - und "ein Treffer auf irgendeiner
            # Stufe" ist dann kein Beleg fuer die uebrigen.
            # Der Fall bleibt offen (`unmatched_amount_steps`). Ihn zu loesen
            # heisst, vorher zu pruefen, ob die Stufen ueberhaupt eine Treppe
            # DESSELBEN Vertrags sind - nicht, die Eindeutigkeitsregel zu
            # lockern. Bis dahin gilt Leitsatz 3: lieber kein Treffer als ein
            # falscher.
            if any(item is None for item in step_matches) or len(per_row) != len(steps):
                # Keine saubere 1:1-Zuordnung. Festhalten, WORAN es lag: eine
                # Preisstufe ohne Gegenstueck ist etwas anderes als zwei
                # Legacy-Zeilen, die um dieselbe Stufe konkurrieren.
                for hits in hits_per_step:
                    for row in hits:
                        structural_reason.setdefault(
                            row.id,
                            "ambiguous_amount_match"
                            if len(hits) > 1 or per_row[row.id] > 1
                            else "unmatched_amount_steps",
                        )
                continue

            matched_rows: list[LegacyContractRow] = [item for item in step_matches if item]
            used_candidates.add(candidate.source_transaction_id)
            for row, step in zip(matched_rows, steps, strict=True):
                matched_ids.add(row.id)
                plan.enriched_contracts.append(
                    RepairedContract(
                        contract_id=row.id,
                        name=row.name,
                        contract_key=candidate.contract_key,
                        contract_key_source=candidate.contract_key_source,
                        first_seen_date=step.first_date,
                        last_seen_date=step.last_date,
                        occurrences_written=len(step.transactions),
                    )
                )
                assignments.append(_Assignment(row.id, candidate, step))
            if len(matched_rows) > 1:
                # amount_steps sind "aelteste zuerst" (ContractCandidate-
                # Docstring) - dieselbe Reihenfolge wie beim Zusammenfuehren:
                # Ziel ist die juengste Stufe.
                plan.merge_suggestions.append(
                    MergeSuggestion(
                        target_id=matched_rows[-1].id,
                        source_ids=tuple(row.id for row in matched_rows[:-1]),
                    )
                )

        for row in legacy_rows:
            if row.id in matched_ids:
                continue
            if not bucket_candidates:
                reason = "no_matching_transactions"
            else:
                reason = structural_reason.get(row.id) or _rejection_reason(
                    row, bucket_candidates
                )
            plan.ambiguous_contracts.append(AmbiguousContract(row.id, row.name, reason))

    plan.contracts_enriched = len({item.contract_id for item in plan.enriched_contracts})
    # Getrennt gezaehlt, weil nicht jede zugeordnete Zahlungsreihe einen
    # Vertragsschluessel hat (Streamingdienste ohne Mandatsreferenz: kein
    # verwertbares Muster im Zweck). Der Zeitraum wird dort ergaenzt, ein
    # Schluessel nicht - und genau diese Differenz nennt Abschnitt 19.9, indem
    # er beide Kennzahlen einzeln verlangt.
    plan.keys_enriched = len(
        {item.contract_id for item in plan.enriched_contracts if item.contract_key}
    )
    plan.ambiguous = len(plan.ambiguous_contracts)
    return plan, assignments


async def analyze_household(session: AsyncSession, household_id: UUID) -> LegacyRepairPlan:
    """Reiner Lesevorgang fuer den Dry-Run. Schreibt nichts."""
    plan, _assignments = await _analyze(session, household_id)
    return plan


async def _write_enrichment(
    session: AsyncSession, household_id: UUID, item: RepairedContract
) -> bool:
    """Liefert, ob tatsaechlich geschrieben wurde. `False` heisst: die Zeile
    wurde seit `_analyze` bereits anderweitig veraendert (zweiter Lauf,
    manuelle Bearbeitung) - der Aufrufer darf dann keine Buchungen dafuer
    anlegen."""
    result = cast(
        CursorResult[Any],
        await session.execute(
            text(
                "update contracts set "
                # Ohne Schluessel bleibt die Spalte, wie sie war: der Zeitraum
                # ist auch allein eine Verbesserung, ein leerer Schluessel waere
                # keine. Derselbe Ausdruck wie beim Zusammenfuehren
                # (contract_service._merge_contract_key_update).
                "contract_key = coalesce(cast(:contract_key as text), contract_key), "
                "contract_key_source = case when cast(:contract_key as text) is not null "
                "  then cast(:contract_key_source as text) else contract_key_source end, "
                "first_seen_date = :first_seen_date, last_seen_date = :last_seen_date "
                "where id = :id and household_id = :household_id "
                # Sicherheitsnetz gegen einen zwischenzeitlich manuell gesetzten
                # Schluessel oder einen parallel gelaufenen zweiten
                # Reparaturlauf - beide sollen gewinnen, nicht dieser Schreib-
                # zugriff.
                "and first_seen_date is null and coalesce(contract_key_source, '') <> 'manual'"
            ),
            {
                "id": str(item.contract_id),
                "household_id": str(household_id),
                "contract_key": item.contract_key,
                # Die Herkunft des Schluessels wird mitgeschrieben statt
                # pauschal 'mandate' behauptet: eine ueber die laengste
                # gemeinsame Zeichenkette abgeleitete Referenz ist
                # 'purpose_pattern' und darf sich nicht als Mandatsreferenz
                # ausgeben.
                "contract_key_source": item.contract_key_source or "purpose_pattern",
                "first_seen_date": item.first_seen_date,
                "last_seen_date": item.last_seen_date,
            },
        ),
    )
    return bool(result.rowcount > 0)


async def apply_household(session: AsyncSession, household_id: UUID) -> LegacyRepairPlan:
    """Schreibt die Anreicherung aus `_analyze` - transaktional ueber den
    gesamten Haushalt (der Aufrufer klammert Commit/Rollback)."""
    plan, assignments = await _analyze(session, household_id)

    # Je Vertrag gebuendelt: `_write_enrichment` traegt das Sicherheitsnetz
    # `first_seen_date is null` und liefert beim ZWEITEN Aufruf fuer denselben
    # Vertrag `False`. Wuerde je Zuordnung geschrieben, verloere ein Vertrag mit
    # mehreren Preisstufen alle Buchungen ausser denen der ersten Stufe.
    by_contract: dict[UUID, list[_Assignment]] = defaultdict(list)
    for assignment in assignments:
        by_contract[assignment.contract_id].append(assignment)

    for contract_id, contract_assignments in by_contract.items():
        matching_result = next(
            item for item in plan.enriched_contracts if item.contract_id == contract_id
        )
        if not await _write_enrichment(session, household_id, matching_result):
            continue

        # `contract_id` kommt aus DIESER Schleife. Eine frueherer Fassung nahm
        # hier `assignment.contract_id` aus einer laengst beendeten Schleife -
        # die Variable zeigte auf den LETZTEN Eintrag der Gesamtliste, und
        # damit landeten die Buchungen aller Vertraege auf einem einzigen.
        # Praktisch hiess das: ein Abo fuer 4,99 EUR sammelte Dutzende
        # fremde Buchungen mehrerer Empfaenger ein.
        for step_transactions in (item.step.transactions for item in contract_assignments):
            for tx in step_transactions:
                await session.execute(
                    text(
                        "insert into contract_occurrences "
                        "(household_id, contract_id, due_date, status, transaction_id, "
                        "booked_date, amount_minor, matched_at) "
                        "values (:household_id, :contract_id, :due_date, 'matched', "
                        ":transaction_id, :booked_date, :amount_minor, now()) "
                        "on conflict do nothing"
                    ),
                    {
                        "household_id": str(household_id),
                        "contract_id": str(contract_id),
                        "due_date": tx.booked_date,
                        "transaction_id": str(tx.transaction_id),
                        "booked_date": tx.booked_date,
                        "amount_minor": abs(tx.amount_minor),
                    },
                )

    return plan
