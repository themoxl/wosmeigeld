"""Finanz-Empfehlungen: was aus den eigenen Zahlen konkret zu tun waere.

Aufbau in zwei getrennten Schichten - das ist die zentrale Entscheidung:

1. **Detektoren.** Jede Empfehlung entsteht aus SQL ueber den echten Bestand.
   Titel, Beschreibung, Betraege und Belege sind vollstaendig, auch wenn nie
   ein Modell laeuft. Ein Vergleich, den kein Detektor rechnen kann, wird
   nicht behauptet.
2. **Optionale Formulierung.** Erst danach darf ein lokales Modell zu einer
   fertig berechneten Empfehlung einen Handlungssatz schreiben. Es bekommt die
   Zahlen als Text und darf ausdruecklich keine eigenen nennen - durchgesetzt
   ueber `_advice_is_clean()`, das jede Antwort mit Ziffern verwirft.

Damit gilt fuer diese Seite dieselbe Zusage wie fuer die Klassifikation
(docs/KI-KATEGORISIERUNG.md): "ein LLM darf keine Salden oder Summen
erfinden".

Die Formulierung laeuft ueber den Gateway des KI-Kerns (app/llm): Budget aus
dem geladenen Fenster, llm_calls-Zeile je Aufruf (Stufe ``insights_advice``).
Bleibt sie aus - kein Modell, abgeschnittene oder verworfene Antwort -, ist
das ein stilles, aber sichtbar gekennzeichnetes Weglassen: der Bericht bleibt
vollstaendig, ``ai_advice`` leer, und ``InsightReport.ai_warnings`` nennt den
Grund (Reason-Code des Kerns). Eine bei finish_reason=length abgeschnittene
Antwort ist nie ein Ratschlag, auch wenn ihr Anfang wie einer aussieht.

Abgrenzung der Zahlen untereinander: In `monthly_potential_minor` der
Gesamtsumme zaehlen ausschliesslich `category_trend` und `price_increase`.
Budgetueberschreitung, Wunsch-Anteil und Kleinbetraege beschreiben dieselben
Ausgaben aus einem anderen Blickwinkel; sie zu addieren wuerde dasselbe Geld
mehrfach als Potenzial ausweisen.

Preiserhoehungen haben seit Phase 4 zwei Quellen, und der Unterschied ist
fachlich: gibt es einen Vertrag, stammt die Aussage aus
`contract_amount_history` - exakt, mit Datum, altem und neuem Betrag. Gibt es
keinen, bleibt der Median der frueheren Zahlungen die einzige verfuegbare
Aussage. Beide Wege liefern `monthly_potential_minor` auf einen Monat normiert:
das Delta einer Jahrespraemie ist das Zwoelffache dessen, was pro Monat frei
wuerde, und stuende sonst so in der Gesamtsumme. Ein Empfaenger, fuer den der
Vertragsweg bereits gemeldet hat, faellt im Median-Weg heraus - sonst stuende
dieselbe Erhoehung zweimal auf der Seite und zweimal in der Summe.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.llm import answers, reasons
from app.schemas.ai import AIWarning
from app.schemas.insights import (
    Insight,
    InsightDrilldown,
    InsightEvidence,
    InsightReport,
)
from app.services import ai_answers, ai_settings_service, contract_service, rules_service

# Betrachtungszeitraum in Monaten, Vorgabe der Route.
DEFAULT_MONTHS: Final = 12
MIN_MONTHS: Final = 3
MAX_MONTHS: Final = 36

# Schwellen. Bewusst als benannte Konstanten und nicht im SQL vergraben -
# sie sind fachliche Setzungen und muessen diskutierbar bleiben.
_TREND_MIN_MONTHS: Final = 6  # darunter gibt es keine belastbare Grundlinie
_TREND_RECENT_MONTHS: Final = 3
_TREND_MIN_RATIO: Final = 1.2  # 20 % ueber der eigenen Grundlinie
_TREND_MIN_DELTA_MINOR: Final = 2500  # 25 EUR/Monat - darunter ist es Rauschen
_TREND_MAX_ITEMS: Final = 3

_BUDGET_MIN_OVERRUN_MINOR: Final = 2000
_BUDGET_MAX_ITEMS: Final = 3

_PRICE_MIN_BOOKINGS: Final = 4
_PRICE_MIN_MONTHS: Final = 4
_PRICE_MIN_RATIO: Final = 1.05
_PRICE_MIN_DELTA_MINOR: Final = 200
# Gemeinsamer Deckel beider Preiswege. Je Detektor eigene drei Plaetze ergaeben
# sechs Preiskarten auf einer Seite - die Erhoehung, die wirklich weh tut, ginge
# darin unter.
_PRICE_MAX_ITEMS: Final = 3

# Ab wie vielen Vertraegen derselben Art im selben Monat gebuendelt wird (I13).
# Fuenf Beitragsanpassungen zum Jahreswechsel sind eine Nachricht, nicht fuenf.
_PRICE_BUNDLE_MIN_CONTRACTS: Final = 2
_PRICE_BUNDLE_MAX_EVIDENCE: Final = 6

# Mittlerer Abstand zweier Zahlungen -> Rhythmus. Nur der empfaengerbasierte Weg
# braucht das: dort steht keine Frequenz im Bestand, und ohne diese Einordnung
# waere das Delta einer Vierteljahresrechnung das Dreifache dessen, was pro
# Monat frei wuerde. Die Grenzen liegen jeweils deutlich zwischen zwei
# Rhythmen, damit eine um Tage verschobene Abbuchung nicht umkippt.
_INTERVAL_TO_FREQUENCY: Final[tuple[tuple[float, str], ...]] = (
    (10.0, "weekly"),
    (45.0, "monthly"),
    (135.0, "quarterly"),
    (225.0, "semiannual"),
)

# Vertraege, deren Zahlung mehrfach ausgeblieben ist, und Doppelvertraege.
_CONTRACT_UNUSED_MAX_ITEMS: Final = 3
_CONTRACT_DUPLICATE_MAX_ITEMS: Final = 2

# Beide Schwellen ausdruecklich aus den Bestandsmodulen statt neu gesetzt: der
# Anlegen-Dialog, die Review-Inbox und diese Seite beantworten dieselbe Frage
# und duerften sich sonst widersprechen.
_CONTRACT_MISSED_MIN: Final = rules_service._CONTRACT_ENDED_CHECK_MIN_MISSED
_DUPLICATE_NAME_SIMILARITY: Final = contract_service._DUPLICATE_NAME_SIMILARITY
_DUPLICATE_AMOUNT_RATIO: Final = contract_service._DUPLICATE_AMOUNT_RATIO

_SMALL_MAX_AVG_MINOR: Final = 1500  # Einzelbuchung bis 15 EUR
_SMALL_MIN_COUNT: Final = 12
_SMALL_MAX_ITEMS: Final = 2

_WANTS_TARGET_SHARE: Final = 0.30  # 50/30/20
_SAVINGS_TARGET_RATE: Final = 0.10

# Gemeinsame Auswahlregel aller Detektoren - identisch zu
# budget_service.get_budget_summary(), damit "Ist" hier und dort dieselbe Zahl
# ist. Eine abweichende Regel waere der sicherste Weg zu zwei Seiten, die sich
# widersprechen.
_BASE_WHERE: Final = (
    "s.household_id = :household_id "
    "and a.on_budget = true "
    "and not t.is_archived "
    "and not t.is_transfer "
    "and t.booked_date between :from_date and :to_date"
)

_BASE_FROM: Final = (
    "from transaction_splits s "
    "join transactions t on t.id = s.transaction_id "
    "join accounts a on a.id = t.account_id "
)


def _eur(amount_minor: int) -> str:
    """Deutsche Betragsschreibweise fuer die Texte der Detektoren."""
    formatted = f"{abs(amount_minor) / 100:,.2f}".replace(",", "X").replace(".", ",")
    return f"{formatted.replace('X', '.')} €"


def _month_name(value: date) -> str:
    names = (
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    )
    return f"{names[value.month - 1]} {value.year}"


# Deutsche Bezeichner der Vertragsarten, Einzahl und Mehrzahl. Der Bestand
# fuehrt sie nur im Frontend; eine gebuendelte Meldung ("deine Versicherungen
# kosten ab Januar mehr") entsteht aber im Server und braucht das Wort hier.
_CONTRACT_TYPE_LABEL: Final[dict[str, tuple[str, str]]] = {
    "subscription": ("Abo", "Abos"),
    "insurance": ("Versicherung", "Versicherungen"),
    "rent": ("Miete", "Mietverträge"),
    "loan": ("Kredit", "Kredite"),
    "installment": ("Ratenzahlung", "Ratenzahlungen"),
    "utility": ("Energie- oder Wasservertrag", "Energie- und Wasserverträge"),
    "telecom": ("Telefon- oder Internetvertrag", "Telefon- und Internetverträge"),
    "membership": ("Mitgliedschaft", "Mitgliedschaften"),
    "donation": ("Spende", "Spenden"),
    "savings": ("Sparvertrag", "Sparverträge"),
    "income": ("Einnahme", "Einnahmen"),
    "other": ("Vertrag", "Verträge"),
}

_FREQUENCY_LABEL: Final[dict[str, str]] = {
    "weekly": "wöchentlich",
    "monthly": "monatlich",
    "quarterly": "vierteljährlich",
    "semiannual": "halbjährlich",
    "yearly": "jährlich",
}


def _type_label(contract_type: str, *, plural: bool = False) -> str:
    singular, many = _CONTRACT_TYPE_LABEL.get(contract_type, ("Vertrag", "Verträge"))
    return many if plural else singular


def _frequency_from_interval(days: float) -> str:
    """Rhythmus aus dem mittleren Zahlungsabstand - nur fuer Empfaenger ohne
    Vertrag, wo keine Frequenz im Bestand steht."""
    for limit, frequency in _INTERVAL_TO_FREQUENCY:
        if days <= limit:
            return frequency
    return "yearly"


# ---------------------------------------------------------------------------
# Zeitfenster
# ---------------------------------------------------------------------------


async def resolve_window(
    session: AsyncSession,
    household_id: UUID,
    months: int,
    anchor_year: int | None = None,
    anchor_month: int | None = None,
) -> tuple[date, date] | None:
    """Letzte `months` VOLLSTAENDIGE Monate mit Daten, endend am Anker-Monat.

    Ohne `anchor_year`/`anchor_month` bleibt der laufende Kalendermonat
    aussen vor: ein halber Monat wuerde jeden Monatsdurchschnitt nach unten
    ziehen und einen Trend vortaeuschen, der nur daran liegt, dass der Monat
    noch nicht vorbei ist. Endet der Bestand frueher, ist der letzte Monat mit
    Daten der Anker. Sind beide Parameter gesetzt, navigiert der Aufrufer
    gezielt zu einem bestimmten Monat - dann gilt genau dieser als Anker,
    auch der laufende Monat."""
    if anchor_year is not None and anchor_month is not None:
        anchor = date(anchor_year, anchor_month, 1)
    else:
        result = await session.execute(
            text(
                "select "
                # least() ignoriert NULL: ohne die explizite Pruefung auf
                # last_booking lieferte ein leerer Haushalt den Vormonat als
                # Anker und damit einen Bericht ueber nichts.
                "  max(t.booked_date) as last_booking, "
                "  least("
                "    date_trunc('month', current_date) - interval '1 month', "
                "    date_trunc('month', max(t.booked_date))"
                "  )::date as anchor "
                "from transactions t "
                "where t.household_id = :household_id and not t.is_archived"
            ),
            {"household_id": str(household_id)},
        )
        row = result.first()
        if row is None or row.last_booking is None or row.anchor is None:
            return None
        anchor = row.anchor

    bounds = await session.execute(
        text(
            "select (cast(:anchor as date) - make_interval(months => :offset))::date as from_date, "
            "(cast(:anchor as date) + interval '1 month' - interval '1 day')::date as to_date"
        ),
        {"anchor": anchor, "offset": months - 1},
    )
    window = bounds.one()
    return window.from_date, window.to_date


# ---------------------------------------------------------------------------
# Kennzahlen des Zeitraums
# ---------------------------------------------------------------------------


async def _totals(
    session: AsyncSession, household_id: UUID, from_date: date, to_date: date
) -> tuple[int, int, int]:
    """(Einnahmen, Ausgaben, abgedeckte Monate) - Ausgaben vorzeichenlos."""
    result = await session.execute(
        text(
            "select "
            "coalesce(sum(s.amount_minor) filter (where s.amount_minor > 0), 0) as income, "
            "coalesce(-sum(s.amount_minor) filter (where s.amount_minor < 0), 0) as expense, "
            "count(distinct date_trunc('month', t.booked_date)) as months "
            f"{_BASE_FROM}"
            f"where {_BASE_WHERE}"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
        },
    )
    row = result.one()
    return int(row.income), int(row.expense), max(1, int(row.months))


# ---------------------------------------------------------------------------
# Detektoren
# ---------------------------------------------------------------------------


async def _detect_category_trend(
    session: AsyncSession, household_id: UUID, from_date: date, to_date: date
) -> list[Insight]:
    """Kategorien, die zuletzt deutlich ueber ihrer eigenen Grundlinie liegen.

    Verglichen wird die Kategorie mit sich selbst, nicht mit einem fremden
    Richtwert: was fuer diesen Haushalt normal ist, steht in seinen eigenen
    Vormonaten."""
    result = await session.execute(
        text(
            "with monthly as ( "
            "  select s.category_id, "
            "         date_trunc('month', t.booked_date)::date as month, "
            "         -sum(s.amount_minor) as expense_minor "
            f"  {_BASE_FROM}"
            f"  where {_BASE_WHERE} and s.amount_minor < 0 and s.category_id is not null "
            "  group by 1, 2 "
            "), ranked as ( "
            "  select category_id, month, expense_minor, "
            "         dense_rank() over (order by month desc) as recency "
            "  from monthly "
            ") "
            "select r.category_id, c.name as category_name, "
            "  count(*) as month_count, "
            "  coalesce(avg(expense_minor) filter (where recency <= :recent), 0) as recent_avg, "
            "  coalesce(avg(expense_minor) filter (where recency > :recent), 0) as base_avg, "
            "  count(*) filter (where recency > :recent) as base_months "
            "from ranked r join categories c on c.id = r.category_id "
            "group by r.category_id, c.name"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
            "recent": _TREND_RECENT_MONTHS,
        },
    )

    candidates: list[tuple[int, Insight]] = []
    for row in result:
        if int(row.month_count) < _TREND_MIN_MONTHS or int(row.base_months) == 0:
            continue
        recent = int(Decimal(row.recent_avg).to_integral_value())
        base = int(Decimal(row.base_avg).to_integral_value())
        if base <= 0 or recent < base * _TREND_MIN_RATIO:
            continue
        delta = recent - base
        if delta < _TREND_MIN_DELTA_MINOR:
            continue

        category_id = UUID(str(row.category_id))
        name = str(row.category_name)
        candidates.append(
            (
                delta,
                Insight(
                    key=f"category_trend:{category_id}",
                    kind="category_trend",
                    severity="opportunity",
                    title=f"{name}: {_eur(delta)} pro Monat mehr als sonst",
                    detail=(
                        f"In den letzten {_TREND_RECENT_MONTHS} Monaten waren es im Schnitt "
                        f"{_eur(recent)} pro Monat, davor {_eur(base)}. Zurück auf das frühere "
                        f"Niveau wären {_eur(delta)} im Monat."
                    ),
                    amount_minor=recent,
                    monthly_potential_minor=delta,
                    category_id=category_id,
                    category_name=name,
                    evidence=[
                        InsightEvidence(
                            label=f"Ø letzte {_TREND_RECENT_MONTHS} Monate",
                            amount_minor=recent,
                        ),
                        InsightEvidence(
                            label=f"Ø davor ({int(row.base_months)} Monate)", amount_minor=base
                        ),
                    ],
                    drilldown=InsightDrilldown(
                        field="category",
                        value=str(category_id),
                        from_date=from_date,
                        to_date=to_date,
                        direction="expense",
                    ),
                ),
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [insight for _, insight in candidates[:_TREND_MAX_ITEMS]]


async def _detect_budget_overrun(
    session: AsyncSession, household_id: UUID, month_end: date
) -> list[Insight]:
    """Ueberschreitungen im betrachteten Monat (Ende des aufgeloesten Fensters).

    Ohne Plan fuer diesen Monat gibt es keine Ueberschreitung, nur Ausgaben -
    die Abfrage liefert dann schlicht keine Zeilen.

    Die Ist-Summe verwendet dieselben Konstanten (`_BASE_WHERE`/`_BASE_FROM`)
    wie jeder andere Detektor und `budget_service.get_budget_summary`: eine
    fruehere Fassung filterte den Monat in der `ON`-Bedingung eines
    `LEFT JOIN transactions`, wodurch nicht passende Buchungszeilen nicht
    verworfen wurden, sondern nur `t.*` NULL wurde - die Summe umfasste damit
    jede Ausgabe der Kategorie ueber alle Zeit statt nur den Zielmonat."""
    month_start = date(month_end.year, month_end.month, 1)
    result = await session.execute(
        text(
            "with actuals as ( "
            "  select s.category_id, "
            "    coalesce(-sum(s.amount_minor) filter (where s.amount_minor < 0), 0) "
            "      as actual_minor "
            f"  {_BASE_FROM}"
            f"  where {_BASE_WHERE} "
            "  group by s.category_id "
            ") "
            "select c.id as category_id, c.name as category_name, ba.planned_minor, "
            "  coalesce(a.actual_minor, 0) as actual_minor "
            "from budgets b "
            "join budget_allocations ba on ba.budget_id = b.id "
            "join categories c on c.id = ba.category_id "
            "left join actuals a on a.category_id = c.id "
            "where b.household_id = :household_id and b.year = :year and b.month = :month "
            "  and ba.planned_minor > 0 and c.default_type <> 'income'"
        ),
        {
            "household_id": str(household_id),
            "from_date": month_start,
            "to_date": month_end,
            "year": month_start.year,
            "month": month_start.month,
        },
    )

    candidates: list[tuple[int, Insight]] = []
    for row in result:
        planned = int(row.planned_minor)
        actual = int(row.actual_minor)
        overrun = actual - planned
        if overrun < _BUDGET_MIN_OVERRUN_MINOR:
            continue
        category_id = UUID(str(row.category_id))
        name = str(row.category_name)
        candidates.append(
            (
                overrun,
                Insight(
                    key=f"budget_overrun:{category_id}",
                    kind="budget_overrun",
                    severity="warning",
                    title=f"{name}: {_eur(overrun)} über Plan",
                    detail=(
                        f"Geplant waren im {_month_name(month_start)} {_eur(planned)}, "
                        f"tatsächlich wurden es {_eur(actual)}."
                    ),
                    amount_minor=overrun,
                    category_id=category_id,
                    category_name=name,
                    evidence=[
                        InsightEvidence(label="Plan", amount_minor=planned),
                        InsightEvidence(label="Ist", amount_minor=actual),
                    ],
                    drilldown=InsightDrilldown(
                        field="category",
                        value=str(category_id),
                        from_date=month_start,
                        to_date=month_end,
                        direction="expense",
                    ),
                ),
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [insight for _, insight in candidates[:_BUDGET_MAX_ITEMS]]


@dataclass(frozen=True)
class _ContractRaise:
    """Eine belegte Preisstufe eines Vertrags - das Zwischenergebnis zwischen
    Abfrage und Karte, weil Buendelung und Deckel vor dem Formulieren stehen."""

    contract_id: UUID
    name: str
    contract_type: str
    frequency: str
    category_id: UUID | None
    category_name: str | None
    provider_label: str
    old_minor: int
    new_minor: int
    changed_on: date
    monthly_delta_minor: int


async def _load_contract_raises(
    session: AsyncSession, household_id: UUID, from_date: date, to_date: date
) -> list[_ContractRaise]:
    """Vertraege, deren offene Preisstufe im Zeitraum teurer wurde als die
    davor. Quelle ist der Betragsverlauf, nicht die Buchungsreihe."""
    result = await session.execute(
        text(
            "select c.id as contract_id, c.name, c.contract_type, c.frequency, "
            "  c.category_id, cat.name as category_name, "
            "  coalesce(p.name, c.provider_label, c.name) as provider_label, "
            "  h_new.amount_minor as new_minor, h_new.valid_from as changed_on, "
            "  h_old.amount_minor as old_minor "
            "from contracts c "
            "join contract_amount_history h_new on h_new.contract_id = c.id "
            "  and h_new.household_id = c.household_id and h_new.valid_to is null "
            # Die zuletzt geschlossene Stufe ist der Preis davor. `join lateral`
            # statt Fensterfunktion: ohne Vorstufe gibt es keine Erhoehung,
            # sondern nur einen ersten Preis - die Zeile soll dann herausfallen.
            "join lateral ( "
            "  select h.amount_minor from contract_amount_history h "
            "  where h.contract_id = c.id and h.household_id = c.household_id "
            "    and h.valid_to is not null "
            "  order by h.valid_to desc limit 1 "
            ") h_old on true "
            "left join payees p on p.id = c.payee_id "
            "left join categories cat on cat.id = c.category_id "
            "where c.household_id = :household_id and c.direction = 'expense' "
            "  and c.status in ('active', 'cancelled') "
            "  and h_new.valid_from between :from_date and :to_date "
            # Kein Verhaeltnis wie beim Median-Weg: eine belegte Stufe ist keine
            # Schaetzung, die ein Ausreisser verfaelschen koennte. Zwei Prozent
            # Mieterhoehung sind real und sollen nicht wegen der Quote fehlen.
            "  and h_new.amount_minor - h_old.amount_minor >= :min_delta"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
            "min_delta": _PRICE_MIN_DELTA_MINOR,
        },
    )

    raises: list[_ContractRaise] = []
    for row in result:
        frequency = str(row.frequency)
        old_minor = int(row.old_minor)
        new_minor = int(row.new_minor)
        raises.append(
            _ContractRaise(
                contract_id=UUID(str(row.contract_id)),
                name=str(row.name),
                contract_type=str(row.contract_type),
                frequency=frequency,
                category_id=UUID(str(row.category_id)) if row.category_id else None,
                category_name=str(row.category_name) if row.category_name else None,
                provider_label=str(row.provider_label),
                old_minor=old_minor,
                new_minor=new_minor,
                changed_on=row.changed_on,
                # Nicht das rohe Delta: bei einer Jahrespraemie waere es das
                # Zwoelffache dessen, was pro Monat frei wuerde.
                monthly_delta_minor=(
                    contract_service.monthly_minor(new_minor, frequency)
                    - contract_service.monthly_minor(old_minor, frequency)
                ),
            )
        )
    return raises


async def _contract_payee_labels(
    session: AsyncSession, household_id: UUID, contract_ids: list[UUID]
) -> dict[UUID, list[str]]:
    """Buchungslabel der zugeordneten Zahlungen, haeufigstes zuerst.

    Zwei Zwecke: der Drilldown eines Vertrags soll auf genau die Buchungen
    zeigen (der Explorer filtert auf `coalesce(payee_label, payee_raw)`), und
    der Median-Weg muss denselben Empfaenger ausschliessen koennen. Der
    Vertragsname weicht vom Buchungslabel regelmaessig ab.
    """
    if not contract_ids:
        return {}
    result = await session.execute(
        text(
            "select o.contract_id, coalesce(t.payee_label, t.payee_raw) as payee, "
            "  count(*) as bookings "
            "from contract_occurrences o "
            "join transactions t on t.id = o.transaction_id "
            "where o.household_id = :household_id "
            "  and o.contract_id = any(cast(:ids as uuid[])) "
            "  and coalesce(t.payee_label, t.payee_raw) is not null "
            "group by 1, 2 order by 1, 3 desc"
        ),
        {"household_id": str(household_id), "ids": [str(item) for item in contract_ids]},
    )
    labels: dict[UUID, list[str]] = {}
    for row in result:
        labels.setdefault(UUID(str(row.contract_id)), []).append(str(row.payee))
    return labels


def _bundled_price_insight(
    group: list[_ContractRaise], month_start: date, from_date: date, to_date: date
) -> Insight:
    """Eine Meldung fuer mehrere Beitragsanpassungen derselben Art im selben
    Monat (I13). Fuenf Einzelkarten zum Jahreswechsel sind dieselbe Nachricht
    fuenfmal - und die Summe, um die es geht, stuende auf keiner davon."""
    total = sum(item.monthly_delta_minor for item in group)
    contract_type = group[0].contract_type
    plural = _type_label(contract_type, plural=True)
    ranked = sorted(group, key=lambda item: -item.monthly_delta_minor)
    return Insight(
        key=f"price_increase:contract_type:{contract_type}:{month_start:%Y-%m}",
        kind="price_increase",
        severity="warning",
        title=f"{plural} kosten ab {_month_name(month_start)} {_eur(total)} mehr im Monat",
        detail=(
            f"{len(group)} {plural} wurden im {_month_name(month_start)} teurer. Einzeln "
            f"fällt das kaum auf, zusammen sind es {_eur(total)} pro Monat. Die Beträge "
            "stammen aus dem hinterlegten Betragsverlauf, nicht aus einer Schätzung."
        ),
        amount_minor=total,
        monthly_potential_minor=total,
        evidence=[
            *(
                InsightEvidence(label=item.name, amount_minor=item.monthly_delta_minor)
                for item in ranked[:_PRICE_BUNDLE_MAX_EVIDENCE]
            ),
            InsightEvidence(label="Verträge", count=len(group)),
        ],
        # Ohne Vertragskennung: die Karte spricht ueber mehrere. Der Weg dorthin
        # fuehrt ueber die Belegzeilen, nicht ueber einen willkuerlich
        # herausgegriffenen Sprung.
        drilldown=InsightDrilldown(from_date=from_date, to_date=to_date, direction="expense"),
    )


def _single_price_insight(
    item: _ContractRaise, payee_label: str | None, from_date: date, to_date: date
) -> Insight:
    rhythm = _FREQUENCY_LABEL.get(item.frequency, item.frequency)
    monthly_note = (
        ""
        if item.frequency == "monthly"
        else f" Auf den Monat gerechnet sind das {_eur(item.monthly_delta_minor)} mehr."
    )
    return Insight(
        key=f"price_increase:contract:{item.contract_id}",
        kind="price_increase",
        severity="warning",
        title=f"{item.name}: {_eur(item.monthly_delta_minor)} pro Monat teurer",
        detail=(
            f"Der {rhythm}e Betrag stieg zum {item.changed_on:%d.%m.%Y} von "
            f"{_eur(item.old_minor)} auf {_eur(item.new_minor)}.{monthly_note} Der Wert "
            "steht so im Betragsverlauf des Vertrags – geschätzt ist daran nichts."
        ),
        amount_minor=item.monthly_delta_minor,
        monthly_potential_minor=item.monthly_delta_minor,
        category_id=item.category_id,
        category_name=item.category_name,
        evidence=[
            InsightEvidence(label="Vorher", amount_minor=item.old_minor),
            InsightEvidence(label="Nachher", amount_minor=item.new_minor),
            InsightEvidence(label=f"Gültig ab {item.changed_on:%d.%m.%Y}"),
        ],
        drilldown=InsightDrilldown(
            field="payee" if payee_label else None,
            value=payee_label,
            from_date=from_date,
            to_date=to_date,
            direction="expense",
            contract_id=item.contract_id,
        ),
    )


async def _detect_contract_price_increase(
    session: AsyncSession, household_id: UUID, from_date: date, to_date: date
) -> tuple[list[Insight], set[str]]:
    """Preiserhoehungen aus dem Betragsverlauf - exakt statt geschaetzt.

    Rueckgabe ist ein Paar: die Empfehlungen und die Empfaengerschluessel, fuer
    die damit bereits gemeldet wurde. Der Median-Weg blendet genau diese aus -
    aber nur diese: ein Vertrag, dessen Preisstufe der Abgleich noch nicht
    kennt, soll seine geschaetzte Meldung behalten.
    """
    raises = await _load_contract_raises(session, household_id, from_date, to_date)
    if not raises:
        return [], set()

    groups: dict[tuple[str, date], list[_ContractRaise]] = {}
    for item in raises:
        month_start = date(item.changed_on.year, item.changed_on.month, 1)
        groups.setdefault((item.contract_type, month_start), []).append(item)

    ranked = sorted(
        groups.items(),
        key=lambda entry: -sum(item.monthly_delta_minor for item in entry[1]),
    )[:_PRICE_MAX_ITEMS]

    reported = [item for _, group in ranked for item in group]
    labels = await _contract_payee_labels(
        session, household_id, [item.contract_id for item in reported]
    )

    insights: list[Insight] = []
    for (_, month_start), group in ranked:
        if len(group) >= _PRICE_BUNDLE_MIN_CONTRACTS:
            insights.append(_bundled_price_insight(group, month_start, from_date, to_date))
            continue
        item = group[0]
        payee_labels = labels.get(item.contract_id) or []
        insights.append(
            _single_price_insight(
                item, payee_labels[0] if payee_labels else None, from_date, to_date
            )
        )

    # Beide Namensquellen, weil ein Vertrag ohne `payee_id` sonst durchfaellt:
    # der Anbietername des Vertrags und jedes Buchungslabel seiner Zahlungen.
    covered = {
        rules_service._recurring_payee_key(item.provider_label) for item in reported
    } | {
        rules_service._recurring_payee_key(label)
        for item in reported
        for label in labels.get(item.contract_id, [])
    }
    covered.discard("")
    return insights, covered


async def _detect_price_increase(
    session: AsyncSession,
    household_id: UUID,
    from_date: date,
    to_date: date,
    skip_payee_keys: set[str],
    max_items: int,
) -> list[Insight]:
    """Regelmaessige Zahlungen, die zuletzt teurer geworden sind.

    Verglichen wird gegen den Median der frueheren Betraege, nicht gegen den
    Durchschnitt: eine einzelne Nachzahlung soll keine Preiserhoehung
    vortaeuschen. Fuer Empfaenger mit Vertrag hat `_detect_contract_price_increase`
    bereits die exakte Aussage geliefert; hier bleibt der Weg fuer alle anderen,
    wo die Schaetzung die einzige verfuegbare Aussage ist."""
    if max_items <= 0:
        return []
    result = await session.execute(
        text(
            "with payments as ( "
            "  select coalesce(t.payee_label, t.payee_raw) as payee, "
            "         t.booked_date, -s.amount_minor as amount_minor "
            f"  {_BASE_FROM}"
            f"  where {_BASE_WHERE} and s.amount_minor < 0 "
            "    and coalesce(t.payee_label, t.payee_raw) is not null "
            "), agg as ( "
            "  select payee, count(*) as bookings, "
            "         count(distinct date_trunc('month', booked_date)) as months, "
            "         min(booked_date) as first_date, max(booked_date) as last_date "
            "  from payments group by payee "
            ") "
            "select a.payee, a.bookings, a.months, a.first_date, a.last_date, "
            "  (select p.amount_minor from payments p "
            "   where p.payee = a.payee order by p.booked_date desc limit 1) as last_amount, "
            "  (select percentile_cont(0.5) within group (order by p.amount_minor) "
            "   from payments p where p.payee = a.payee and p.booked_date < a.last_date"
            "  ) as median_before "
            "from agg a "
            "where a.bookings >= :min_bookings and a.months >= :min_months "
            # Etwa eine Zahlung je Monat: mehrere Einkaeufe im selben Monat
            # sind kein Abo, und eine Preiserhoehung waere dort eine
            # Fehldeutung.
            "  and a.bookings <= a.months * 1.5"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
            "min_bookings": _PRICE_MIN_BOOKINGS,
            "min_months": _PRICE_MIN_MONTHS,
        },
    )

    candidates: list[tuple[int, Insight]] = []
    for row in result:
        if row.median_before is None or row.last_amount is None:
            continue
        median = int(Decimal(row.median_before).to_integral_value())
        last = int(row.last_amount)
        if median <= 0 or last < median * _PRICE_MIN_RATIO:
            continue
        delta = last - median
        if delta < _PRICE_MIN_DELTA_MINOR:
            continue

        payee = str(row.payee)
        # Der Ausschluss laeuft in Python und nicht im SQL, weil er dieselbe
        # Normalisierung verwenden muss wie der Abgleichsmotor - sonst faellt er
        # genau bei den Vertraegen aus, die der Motor selbst gefunden hat.
        if rules_service._recurring_payee_key(payee) in skip_payee_keys:
            continue

        bookings = int(row.bookings)
        interval_days = (row.last_date - row.first_date).days / max(1, bookings - 1)
        monthly = contract_service.monthly_minor(
            delta, _frequency_from_interval(interval_days)
        )
        # Weicht das Monatspotenzial vom Betrag der Einzelzahlung ab, steht sonst
        # eine zweite Zahl auf der Karte, die im Text nirgends vorkommt.
        rhythm_note = (
            "" if monthly == delta else f" Auf den Monat gerechnet sind das {_eur(monthly)}."
        )
        candidates.append(
            (
                delta,
                Insight(
                    key=f"price_increase:{payee}",
                    kind="price_increase",
                    severity="warning",
                    title=f"{payee}: {_eur(delta)} teurer geworden",
                    detail=(
                        f"Die letzte Zahlung am {row.last_date:%d.%m.%Y} lag bei {_eur(last)}, "
                        f"üblich waren {_eur(median)}. Bei {bookings} Zahlungen in "
                        f"{int(row.months)} Monaten sieht das nach einer laufenden Verpflichtung "
                        f"aus – Vertrag und Alternativen prüfen.{rhythm_note}"
                    ),
                    amount_minor=delta,
                    monthly_potential_minor=monthly,
                    evidence=[
                        InsightEvidence(label="Zuletzt", amount_minor=last),
                        InsightEvidence(label="Üblich (Median)", amount_minor=median),
                        InsightEvidence(label="Zahlungen", count=bookings),
                    ],
                    drilldown=InsightDrilldown(
                        field="payee",
                        value=payee,
                        from_date=from_date,
                        to_date=to_date,
                        direction="expense",
                    ),
                ),
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [insight for _, insight in candidates[:max_items]]


async def _detect_contract_unused(
    session: AsyncSession, household_id: UUID, from_date: date, to_date: date
) -> list[Insight]:
    """Vertraege, deren Zahlung mehrfach ausgeblieben ist.

    Bewusst `info` und ohne Monatspotenzial: der Detektor weiss nicht, ob der
    Vertrag geendet hat - dann ist das Geld laengst gespart - oder ob eine
    Lastschrift geplatzt ist und nachkommt. Beides zu behaupten waere eine
    Aussage, die die Daten nicht hergeben.
    """
    result = await session.execute(
        text(
            "select c.id, c.name, c.contract_type, c.amount_minor, c.frequency, "
            "  c.missed_periods, c.last_seen_date, c.next_due_date, "
            "  c.category_id, cat.name as category_name "
            "from contracts c "
            "left join categories cat on cat.id = c.category_id "
            "where c.household_id = :household_id and c.direction = 'expense' "
            "  and c.status in ('active', 'cancelled') "
            "  and c.missed_periods >= :min_missed "
            # Ein Vertrag mit erreichtem Enddatum hat aufgehoert zu zahlen, weil
            # er vorbei ist - das ist keine Auffaelligkeit.
            "  and (c.ends_on is null or c.ends_on >= current_date) "
            "order by c.missed_periods desc, c.name asc limit 20"
        ),
        {"household_id": str(household_id), "min_missed": _CONTRACT_MISSED_MIN},
    )

    candidates: list[tuple[int, Insight]] = []
    for row in result:
        frequency = str(row.frequency)
        monthly = contract_service.monthly_minor(int(row.amount_minor), frequency)
        missed = int(row.missed_periods)
        name = str(row.name)
        rhythm = _FREQUENCY_LABEL.get(frequency, frequency)
        contract_id = UUID(str(row.id))
        seen = (
            f"Zuletzt abgebucht wurde am {row.last_seen_date:%d.%m.%Y}."
            if row.last_seen_date is not None
            else "Diesem Vertrag wurde noch keine Zahlung zugeordnet."
        )
        candidates.append(
            (
                monthly,
                Insight(
                    key=f"contract_unused:{contract_id}",
                    kind="contract_unused",
                    severity="info",
                    title=f"{name}: {missed} Zahlungen ausgeblieben",
                    detail=(
                        f"{seen} Seitdem sind {missed} {rhythm}e Fälligkeiten offen, die "
                        f"nächste steht zum {row.next_due_date:%d.%m.%Y}. Entweder läuft der "
                        "Vertrag nicht mehr – dann beende ihn, damit er aus der Fixkostensumme "
                        "fällt – oder eine Abbuchung ist fehlgeschlagen."
                    ),
                    amount_minor=monthly,
                    category_id=UUID(str(row.category_id)) if row.category_id else None,
                    category_name=str(row.category_name) if row.category_name else None,
                    evidence=[
                        InsightEvidence(label="Ausgebliebene Fälligkeiten", count=missed),
                        InsightEvidence(label="Vereinbart pro Monat", amount_minor=monthly),
                    ],
                    drilldown=InsightDrilldown(
                        from_date=from_date,
                        to_date=to_date,
                        direction="expense",
                        contract_id=contract_id,
                    ),
                ),
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [insight for _, insight in candidates[:_CONTRACT_UNUSED_MAX_ITEMS]]


async def _detect_contract_duplicate(
    session: AsyncSession, household_id: UUID, from_date: date, to_date: date
) -> list[Insight]:
    """Zwei laufende Vertraege derselben Art mit aehnlichem Anbieter (I5).

    Die Paarbildung verlangt gleiche Vertragsart UND gleichen Empfaenger oder
    aehnlichen Namen. Die Betragsnaehe allein reicht hier - anders als im
    Anlegen-Dialog - nicht: dort schaut der Nutzer auf einen konkreten neuen
    Vertrag, hier wuerden zwei zufaellig gleich teure Abos zu einer
    Falschmeldung. Verwendet werden trotzdem dieselben beiden Schwellen, damit
    Dialog und Empfehlungsseite sich nicht widersprechen.
    """
    result = await session.execute(
        text(
            "select a.id as a_id, a.name as a_name, a.amount_minor as a_amount, "
            "  a.frequency as a_frequency, b.id as b_id, b.name as b_name, "
            "  b.amount_minor as b_amount, b.frequency as b_frequency, "
            "  a.contract_type, a.category_id, cat.name as category_name, "
            "  (a.payee_id is not null and a.payee_id = b.payee_id) as same_payee "
            "from contracts a "
            # b.id > a.id liefert jedes Paar genau einmal.
            "join contracts b on b.household_id = a.household_id and b.id > a.id "
            "  and b.contract_type = a.contract_type and b.direction = a.direction "
            "  and b.status in ('active', 'cancelled') "
            "left join categories cat on cat.id = a.category_id "
            "where a.household_id = :household_id and a.direction = 'expense' "
            "  and a.status in ('active', 'cancelled') "
            "  and (a.ends_on is null or a.ends_on >= current_date) "
            "  and (b.ends_on is null or b.ends_on >= current_date) "
            "  and ( "
            "    (a.payee_id is not null and a.payee_id = b.payee_id) "
            "    or similarity(lower(coalesce(a.provider_label, a.name)), "
            "                  lower(coalesce(b.provider_label, b.name))) >= :name_similarity "
            "  ) "
            "order by a.name asc, b.name asc limit 20"
        ),
        {
            "household_id": str(household_id),
            "name_similarity": _DUPLICATE_NAME_SIMILARITY,
        },
    )

    candidates: list[tuple[int, Insight]] = []
    paired: set[UUID] = set()
    for row in result:
        contract_type = str(row.contract_type)
        a_monthly = contract_service.monthly_minor(int(row.a_amount), str(row.a_frequency))
        b_monthly = contract_service.monthly_minor(int(row.b_amount), str(row.b_frequency))
        total = a_monthly + b_monthly
        a_id, b_id = UUID(str(row.a_id)), UUID(str(row.b_id))
        # Drei aehnliche Vertraege ergeben drei Paare. Jeder Vertrag darf nur in
        # einer Karte stehen, sonst liest der Nutzer dreimal dieselbe Sache mit
        # wechselnden Namen.
        if a_id in paired or b_id in paired:
            continue
        paired |= {a_id, b_id}
        a_name, b_name = str(row.a_name), str(row.b_name)
        label = _type_label(contract_type)

        # Nahezu gleiche Betraege sind der staerkste Hinweis auf eine doppelte
        # Erfassung - dieselbe Schwelle wie im Anlegen-Dialog.
        larger = max(a_monthly, b_monthly)
        close_amounts = larger > 0 and abs(a_monthly - b_monthly) <= round(
            larger * _DUPLICATE_AMOUNT_RATIO
        )
        title = (
            f"Möglicherweise doppelt versichert: {a_name} und {b_name}"
            if contract_type == "insurance"
            else f"{a_name} und {b_name} könnten derselbe Vertrag sein"
        )
        candidates.append(
            (
                total,
                Insight(
                    key=f"contract_duplicate:{a_id}:{b_id}",
                    kind="contract_duplicate",
                    severity="warning",
                    title=title,
                    detail=(
                        f"Beide laufen als {label} und kosten zusammen {_eur(total)} pro Monat."
                        + (
                            " Die Beträge liegen fast gleichauf, was für eine doppelte "
                            "Erfassung desselben Vertrags spricht."
                            if close_amounts
                            else ""
                        )
                        + (
                            " Derselbe Zahlungsempfänger steht hinter beiden."
                            if bool(row.same_payee)
                            else ""
                        )
                        + " Ob du beide brauchst, entscheidest du – zusammenführen oder "
                        "beenden geht in der Vertragsverwaltung."
                    ),
                    amount_minor=total,
                    category_id=UUID(str(row.category_id)) if row.category_id else None,
                    category_name=str(row.category_name) if row.category_name else None,
                    evidence=[
                        InsightEvidence(label=a_name, amount_minor=a_monthly),
                        InsightEvidence(label=b_name, amount_minor=b_monthly),
                    ],
                    # Sprungziel ist der teurere der beiden: dort liegt das Geld,
                    # um das es bei einer Entscheidung geht.
                    drilldown=InsightDrilldown(
                        from_date=from_date,
                        to_date=to_date,
                        direction="expense",
                        contract_id=a_id if a_monthly >= b_monthly else b_id,
                    ),
                ),
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [insight for _, insight in candidates[:_CONTRACT_DUPLICATE_MAX_ITEMS]]


async def _detect_small_amounts(
    session: AsyncSession,
    household_id: UUID,
    from_date: date,
    to_date: date,
    months: int,
    skip_categories: set[UUID],
) -> list[Insight]:
    """Viele kleine Betraege, die in der Summe auffallen.

    Zaehlt bewusst NICHT ins Gesamtpotenzial: wie viel davon vermeidbar ist,
    weiss der Haushalt und nicht der Detektor."""
    result = await session.execute(
        text(
            "select s.category_id, c.name as category_name, count(*) as bookings, "
            "  -sum(s.amount_minor) as total_minor, "
            "  -avg(s.amount_minor) as avg_minor "
            f"{_BASE_FROM}"
            "join categories c on c.id = s.category_id "
            f"where {_BASE_WHERE} and s.amount_minor < 0 "
            "group by s.category_id, c.name "
            "having count(*) >= :min_count and -avg(s.amount_minor) <= :max_avg"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
            "min_count": _SMALL_MIN_COUNT,
            "max_avg": _SMALL_MAX_AVG_MINOR,
        },
    )

    candidates: list[tuple[int, Insight]] = []
    for row in result:
        category_id = UUID(str(row.category_id))
        if category_id in skip_categories:
            continue
        total = int(row.total_minor)
        per_month = total // months
        if per_month <= 0:
            continue
        name = str(row.category_name)
        candidates.append(
            (
                per_month,
                Insight(
                    key=f"small_amounts:{category_id}",
                    kind="small_amounts",
                    severity="info",
                    title=f"{name}: {_eur(per_month)} pro Monat in kleinen Beträgen",
                    detail=(
                        f"{int(row.bookings)} Buchungen mit im Schnitt "
                        f"{_eur(int(Decimal(row.avg_minor).to_integral_value()))} ergeben "
                        f"{_eur(total)} im Betrachtungszeitraum. Einzeln fällt das nicht auf."
                    ),
                    amount_minor=per_month,
                    category_id=category_id,
                    category_name=name,
                    evidence=[
                        InsightEvidence(label="Buchungen", count=int(row.bookings)),
                        InsightEvidence(label="Summe", amount_minor=total),
                    ],
                    drilldown=InsightDrilldown(
                        field="category",
                        value=str(category_id),
                        from_date=from_date,
                        to_date=to_date,
                        direction="expense",
                    ),
                ),
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [insight for _, insight in candidates[:_SMALL_MAX_ITEMS]]


async def _detect_wants_share(
    session: AsyncSession,
    household_id: UUID,
    from_date: date,
    to_date: date,
    months: int,
) -> list[Insight]:
    """Anteil des Budgetbereichs "Wunsch" gemessen an 50/30/20."""
    result = await session.execute(
        text(
            "select c.budget_bucket, -sum(s.amount_minor) as total_minor "
            f"{_BASE_FROM}"
            "join categories c on c.id = s.category_id "
            f"where {_BASE_WHERE} and s.amount_minor < 0 and c.budget_bucket is not null "
            "group by c.budget_bucket"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
        },
    )
    buckets = {str(row.budget_bucket): int(row.total_minor) for row in result}
    total = sum(buckets.values())
    wants = buckets.get("wants", 0)
    if total <= 0 or wants <= 0:
        return []
    share = wants / total
    if share <= _WANTS_TARGET_SHARE:
        return []

    above = int(wants - total * _WANTS_TARGET_SHARE) // months
    if above <= 0:
        return []
    return [
        Insight(
            key="wants_share",
            kind="wants_share",
            severity="opportunity",
            title=f"Wünsche machen {round(share * 100)} % deiner Ausgaben aus",
            detail=(
                f"Die 50/30/20-Faustregel sieht 30 % vor. Das sind {_eur(above)} pro Monat "
                "über der Marke. Die Faustregel ist kein Gesetz – aber ein Anhaltspunkt, "
                "wo Spielraum liegt."
            ),
            amount_minor=above,
            evidence=[
                InsightEvidence(label="Wunsch", amount_minor=wants),
                InsightEvidence(label="Bedarf", amount_minor=buckets.get("needs", 0)),
                InsightEvidence(label="Sparen", amount_minor=buckets.get("savings", 0)),
            ],
            drilldown=InsightDrilldown(
                field="budget_bucket",
                value="wants",
                from_date=from_date,
                to_date=to_date,
                direction="expense",
            ),
        )
    ]


def _detect_savings_rate(
    income: int, expense: int, months: int, from_date: date, to_date: date
) -> list[Insight]:
    if income <= 0:
        return []
    net = income - expense
    rate = net / income
    if rate >= _SAVINGS_TARGET_RATE:
        return [
            Insight(
                key="savings_rate",
                kind="savings_rate",
                severity="info",
                title=f"Sparquote {round(rate * 100)} %",
                detail=(
                    f"Von {_eur(income // months)} Einnahmen im Monat bleiben "
                    f"{_eur(net // months)} übrig."
                ),
                amount_minor=net // months,
                drilldown=InsightDrilldown(from_date=from_date, to_date=to_date),
            )
        ]
    gap = int(income * _SAVINGS_TARGET_RATE) - net
    return [
        Insight(
            key="savings_rate",
            kind="savings_rate",
            severity="warning",
            title=(
                f"Sparquote {round(rate * 100)} %"
                if net >= 0
                else f"Monatlich {_eur(abs(net) // months)} mehr aus als ein"
            ),
            detail=(
                f"Von {_eur(income // months)} Einnahmen im Monat bleiben "
                f"{_eur(net // months)}. Für 10 % Sparquote fehlen "
                f"{_eur(gap // months)} pro Monat."
            ),
            amount_minor=gap // months,
            evidence=[
                InsightEvidence(label="Einnahmen Ø/Monat", amount_minor=income // months),
                InsightEvidence(label="Ausgaben Ø/Monat", amount_minor=expense // months),
            ],
            drilldown=InsightDrilldown(from_date=from_date, to_date=to_date),
        )
    ]


async def _detect_tax_deductible(
    session: AsyncSession, household_id: UUID, from_date: date, to_date: date
) -> list[Insight]:
    """Ausgaben in Kategorien, die als steuerlich absetzbar markiert sind
    (Migration 0010). Eine Einordnung, keine Steuerberatung."""
    result = await session.execute(
        text(
            "select -sum(s.amount_minor) as total_minor, count(*) as bookings "
            f"{_BASE_FROM}"
            "join categories c on c.id = s.category_id "
            f"where {_BASE_WHERE} and s.amount_minor < 0 and c.is_tax_deductible"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
        },
    )
    row = result.one()
    total = int(row.total_minor or 0)
    if total <= 0:
        return []
    return [
        Insight(
            key="tax_deductible",
            kind="tax_deductible",
            severity="opportunity",
            title=f"{_eur(total)} in als absetzbar markierten Kategorien",
            detail=(
                f"{int(row.bookings)} Buchungen im Betrachtungszeitraum liegen in Kategorien, "
                "die du als steuerlich absetzbar gekennzeichnet hast. Die Belege dazu lohnen "
                "sich für die Steuererklärung – die Einstufung bleibt deine Entscheidung."
            ),
            amount_minor=total,
            evidence=[InsightEvidence(label="Buchungen", count=int(row.bookings))],
            drilldown=InsightDrilldown(from_date=from_date, to_date=to_date, direction="expense"),
        )
    ]


async def _detect_uncategorized(
    session: AsyncSession, household_id: UUID, from_date: date, to_date: date
) -> list[Insight]:
    """Ohne Kategorie ist jede andere Auswertung nur so gut wie ihr blinder
    Fleck - deshalb steht diese Empfehlung ganz oben, wenn sie zutrifft."""
    result = await session.execute(
        text(
            "select count(*) as bookings, "
            "coalesce(-sum(s.amount_minor) filter (where s.amount_minor < 0), 0) as total_minor "
            f"{_BASE_FROM}"
            f"where {_BASE_WHERE} and s.category_id is null"
        ),
        {
            "household_id": str(household_id),
            "from_date": from_date,
            "to_date": to_date,
        },
    )
    row = result.one()
    bookings = int(row.bookings)
    if bookings == 0:
        return []
    return [
        Insight(
            key="uncategorized",
            kind="uncategorized",
            severity="warning",
            title=f"{bookings} Buchungen ohne Kategorie",
            detail=(
                f"Dahinter stecken {_eur(int(row.total_minor))} an Ausgaben, die in keiner "
                "Auswertung auftauchen. „Kategorien vorschlagen (KI)“ ordnet gleichartige "
                "Buchungen in einem Durchgang zu."
            ),
            amount_minor=int(row.total_minor),
            evidence=[InsightEvidence(label="Buchungen", count=bookings)],
            drilldown=InsightDrilldown(
                field="category", value=None, from_date=from_date, to_date=to_date
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Optionale Formulierung durch das Modell
# ---------------------------------------------------------------------------

_ADVICE_SCHEMA: Final[dict[str, Any]] = {
    "name": "financial_advice",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"advice": {"type": "string"}},
        "required": ["advice"],
        "additionalProperties": False,
    },
}

_ADVICE_SYSTEM_PROMPT: Final = (
    "Du bist ein nüchterner deutschsprachiger Finanzcoach für private "
    "Haushalte. Du bekommst eine bereits ausgerechnete Beobachtung und "
    "formulierst dazu GENAU EINEN konkreten Handlungssatz.\n\n"
    "Regeln:\n"
    "- Schreibe KEINE Zahlen, keine Beträge, keine Prozentwerte und keine "
    "Jahreszahlen. Die Zahlen stehen bereits daneben; eine zweite Zahl von "
    "dir wäre im Zweifel falsch.\n"
    "- Ein Satz, maximal 200 Zeichen, im Du.\n"
    "- Konkret und überprüfbar ('Kündige das teurere der beiden Abos'), "
    "nicht allgemein ('Spare mehr Geld').\n"
    "- Keine Anlage-, Kredit- oder Steuerberatung, keine Produktempfehlung.\n"
    "- Wenn dir nichts Konkretes einfällt, antworte mit einem leeren String."
)

# Ziffern in jeder Schreibweise. Die Formulierung darf keine eigenen Zahlen
# einfuehren - das ist die technische Durchsetzung der Prompt-Regel und
# nicht bloss eine Bitte an das Modell.
_DIGIT = re.compile(r"\d")


def _advice_is_clean(advice: str) -> bool:
    # fest: Ausgabepruefung - ein Handlungssatz, hoechstens 240 Zeichen, keine Ziffer
    return bool(advice) and len(advice) <= 240 and not _DIGIT.search(advice)


def build_advice_prompt(insight: Insight) -> str:
    lines = [f"Beobachtung: {insight.title}", f"Einordnung: {insight.detail}"]
    if insight.category_name:
        lines.append(f"Kategorie: {insight.category_name}")
    return "\n".join(lines)


# llm_calls: Stufe und Vertragsversion der Formulierung.
ADVICE_STAGE: Final = "insights_advice"
ADVICE_PROMPT_VERSION: Final = "insights-advice-v1"


async def _ask_for_advice(
    insight: Insight,
    *,
    target: llm.Target,
    settings: llm.LLMSettings,
    scope: llm.CallScope,
    use_schema: bool,
) -> tuple[str | None, answers.Answer[Any]]:
    """Ein Handlungssatz zu einer fertig gerechneten Empfehlung.

    Rueckgabe (Ratschlag oder None, Antwort mit Grund). None ohne Grund heisst:
    das Modell hatte nichts Konkretes (leerer String - so erlaubt es der
    Prompt). Verworfen und in llm_calls markiert wird eine Antwort, die kein
    gueltiges JSON ist, das Feld ``advice`` nicht traegt oder eigene Zahlen
    nennt; abgeschnittene Antworten verwirft llm.answers.ask() bereits
    (model_budget_exhausted)."""
    answer: answers.Answer[Any] = await answers.ask(
        answers.Ready(settings, target, scope),
        llm.ChatRequest(
            messages=[
                {"role": "system", "content": _ADVICE_SYSTEM_PROMPT},
                {"role": "user", "content": build_advice_prompt(insight)},
            ],
            response_format=(
                {"type": "json_schema", "json_schema": _ADVICE_SCHEMA} if use_schema else None
            ),
        ),
    )
    if not answer.ok:
        return None, answer
    content = answer.text.strip()
    if "{" in content:
        # Strukturierte Antwort (oder JSON auf dem Prompt+Parse-Weg): dann
        # zaehlt nur das Feld advice - nie der rohe JSON-Text.
        try:
            parsed = answers.json_object(content)
        except ValueError as exc:
            return None, await answers.reject(
                scope, answer.result, reasons.MODEL_OUTPUT_UNUSABLE, str(exc)
            )
        if "advice" not in parsed:
            return None, await answers.reject(
                scope,
                answer.result,
                reasons.MODEL_OUTPUT_UNUSABLE,
                "Die Modellantwort enthält kein Feld „advice“.",
            )
        content = str(parsed.get("advice") or "").strip()
    if not content:
        return None, answer
    if not _advice_is_clean(content):
        return None, await answers.reject(
            scope,
            answer.result,
            reasons.MODEL_OUTPUT_UNUSABLE,
            "Ratschlag verworfen: er nennt eigene Zahlen oder ist länger als ein Satz.",
        )
    return content, answer


def _summarize(
    rejected: list[answers.Answer[Any]],
    stop: answers.Answer[Any] | None,
    asked: int,
    total: int,
) -> list[AIWarning]:
    """Benannte Gruende fuer weggelassene Ratschlaege - zusammengefasst, damit
    zwoelf gleiche Fehlschlaege eine Zeile sind und nicht zwoelf."""
    warnings: list[AIWarning] = []
    if rejected:
        first = rejected[0]
        warnings.append(
            ai_answers.warning(
                first.reason_code or reasons.MODEL_OUTPUT_UNUSABLE,
                f"{len(rejected)} von {total} Ratschlägen verworfen. {first.message or ''}".strip(),
            )
        )
    if stop is not None:
        code = stop.reason_code or reasons.MODEL_OUTPUT_UNUSABLE
        warnings.append(
            ai_answers.warning(
                code,
                f"Formulierung bei der {asked}. von {total} Empfehlungen beendet: "
                f"{stop.message or reasons.describe(code)}",
            )
        )
    return warnings


async def add_ai_advice(
    session: AsyncSession, household_id: UUID, insights: list[Insight]
) -> tuple[str | None, list[AIWarning]]:
    """Ergaenzt jede Empfehlung um einen Handlungssatz.

    Rueckgabe (Modell, Warnungen): das Modell, wenn es mindestens einmal
    geantwortet hat, sonst None; die Warnungen nennen, warum Ratschlaege
    fehlen (KI aus, kein Modell, Fenster unbekannt, abgeschnitten, verworfen).
    Der Bericht scheitert daran nie - die Empfehlungen stehen auch ohne Rat.

    Verwendet dasselbe Modell wie die Klassifikation: die Einstellung gilt
    haushaltsweit, nicht je Funktion. Ein Grund, der den Zustand von Server
    oder Einstellung beschreibt, beendet die Formulierung (llm.answers.ends_run)
    statt fuer jede weitere Empfehlung erneut zu warten.

    Empfehlungen duerfen auch Betrachter abrufen. Protokoll und Messung laufen
    trotzdem in der Request-Sitzung unter RLS: llm_calls und
    ai_model_measurements lassen Einfuegen und das Nachtragen des Ergebnisses
    fuer jedes Mitglied des eigenen Haushalts zu (0001_schema.sql, Abschnitt
    "KI-Protokoll fuer alle Mitglieder")."""
    settings = await ai_settings_service.resolve(session, household_id)
    # KI aus heisst: kein Kontakt zum Modellserver (resolve_target: ai_disabled).
    found = await llm.resolve_target(settings)
    if not found.ready or found.target is None:
        code = found.reason_code or reasons.MODEL_UNAVAILABLE
        return None, [ai_answers.warning(code, found.message)]
    target = found.target
    scope = llm.CallScope(
        session=session,
        household_id=household_id,
        stage=ADVICE_STAGE,
        prompt_version=ADVICE_PROMPT_VERSION,
    )

    model_name: str | None = None
    rejected: list[answers.Answer[Any]] = []
    stop: answers.Answer[Any] | None = None
    asked = 0
    use_schema = True
    for insight in insights:
        asked += 1
        advice, answer = await _ask_for_advice(
            insight, target=target, settings=settings, scope=scope, use_schema=use_schema
        )
        if answer.result.schema_fallback:
            use_schema = False
        insight.ai_advice = advice
        if answer.ok:
            model_name = target.model
        elif answers.ends_run(answer.reason_code):
            stop = answer
            break
        else:
            rejected.append(answer)
    return model_name, _summarize(rejected, stop, asked, len(insights))


# ---------------------------------------------------------------------------
# Bericht
# ---------------------------------------------------------------------------

# Reihenfolge der Anzeige. Blinde Flecken zuerst (was fehlt), dann Warnungen,
# dann Chancen, dann Einordnung.
_KIND_ORDER: Final[dict[str, int]] = {
    "uncategorized": 0,
    "budget_overrun": 1,
    "price_increase": 2,
    "contract_unused": 3,
    "category_trend": 4,
    "contract_duplicate": 5,
    "wants_share": 6,
    "small_amounts": 7,
    "savings_rate": 8,
    "tax_deductible": 9,
}

# Nur diese Arten zaehlen ins Gesamtpotenzial - siehe Modul-Docstring.
# `contract_unused` und `contract_duplicate` stehen bewusst nicht dabei: bei der
# ausgebliebenen Zahlung ist offen, ob das Geld schon gespart ist oder die
# Lastschrift nachkommt, und ob zwei Vertraege wirklich doppelt sind, entscheidet
# der Nutzer und nicht der Detektor.
_POTENTIAL_KINDS: Final = frozenset({"category_trend", "price_increase"})


async def build_report(
    session: AsyncSession,
    household_id: UUID,
    months: int = DEFAULT_MONTHS,
    with_ai: bool = False,
    anchor_year: int | None = None,
    anchor_month: int | None = None,
) -> InsightReport:
    months = max(MIN_MONTHS, min(MAX_MONTHS, months))
    window = await resolve_window(session, household_id, months, anchor_year, anchor_month)
    if window is None:
        today = date.today()
        return InsightReport(
            from_date=today,
            to_date=today,
            months_covered=0,
            monthly_income_minor=0,
            monthly_expense_minor=0,
            monthly_net_minor=0,
            monthly_potential_minor=0,
        )

    from_date, to_date = window
    income, expense, covered = await _totals(session, household_id, from_date, to_date)

    trend = await _detect_category_trend(session, household_id, from_date, to_date)
    trend_categories = {i.category_id for i in trend if i.category_id is not None}

    # Erst der exakte Weg, dann der geschaetzte: `covered` traegt die
    # Empfaenger, fuer die bereits aus dem Betragsverlauf gemeldet wurde. Ohne
    # diese Reihenfolge stuende jede Vertragserhoehung zweimal auf der Seite und
    # zweimal in der Gesamtsumme.
    contract_price, covered_payees = await _detect_contract_price_increase(
        session, household_id, from_date, to_date
    )

    insights: list[Insight] = []
    insights += await _detect_uncategorized(session, household_id, from_date, to_date)
    insights += await _detect_budget_overrun(session, household_id, to_date)
    insights += contract_price
    insights += await _detect_price_increase(
        session,
        household_id,
        from_date,
        to_date,
        covered_payees,
        _PRICE_MAX_ITEMS - len(contract_price),
    )
    insights += await _detect_contract_unused(session, household_id, from_date, to_date)
    insights += await _detect_contract_duplicate(session, household_id, from_date, to_date)
    insights += trend
    insights += await _detect_wants_share(session, household_id, from_date, to_date, covered)
    insights += await _detect_small_amounts(
        session, household_id, from_date, to_date, covered, trend_categories
    )
    insights += _detect_savings_rate(income, expense, covered, from_date, to_date)
    insights += await _detect_tax_deductible(session, household_id, from_date, to_date)

    insights.sort(key=lambda item: (_KIND_ORDER.get(item.kind, 99), -(item.amount_minor or 0)))

    model_name: str | None = None
    ai_warnings: list[AIWarning] = []
    if with_ai and insights:
        model_name, ai_warnings = await add_ai_advice(session, household_id, insights)

    potential = sum(
        insight.monthly_potential_minor or 0
        for insight in insights
        if insight.kind in _POTENTIAL_KINDS
    )

    return InsightReport(
        from_date=from_date,
        to_date=to_date,
        months_covered=covered,
        monthly_income_minor=income // covered,
        monthly_expense_minor=expense // covered,
        monthly_net_minor=(income - expense) // covered,
        monthly_potential_minor=potential,
        model_name=model_name,
        ai_warnings=ai_warnings,
        insights=insights,
    )
