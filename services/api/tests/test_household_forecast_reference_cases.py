"""Von Hand gerechnete Referenzfaelle der Liquiditaetsvorschau - centgenau,
ohne DB und ohne API (Muster: `test_debt_calculation_reference_cases.py`).

Der Hauptfall ist der Haushalt aus `docs/mockups/vorschau-prototyp.html`. Der
Prototyp rechnet denselben Kern im Browser nach; seine Zahlen sind die
Sollwerte dieser Datei. Weicht der Python-Kern hier ab, ist eine der beiden
Seiten falsch - und zwar sichtbar, nicht schleichend.

Dazu die vier im Plan (Abschnitt 14) genannten Muster: nur Fixkosten, eine
Quartalsrate im Horizont, ein Jahr mit Weihnachtsgeld, und ein Verlauf, der
unter null geht und wieder heraus.
"""

from datetime import date
from uuid import UUID

from app.services import contract_service
from app.services.household_forecast_service import (
    ForecastContext,
    ForecastContractTerm,
    ForecastInput,
    ScheduledFlow,
    VariableMonth,
    calculate_forecast,
)

AS_OF = date(2026, 8, 13)
HORIZON_MONTHS = 24
HORIZON_END = date(2028, 8, 13)
OPENING_MINOR = 110_000  # Girokonto 920,00 + Bargeld 180,00; Tagesgeld zaehlt nicht
BUFFER_MINOR = 50_000

# Vertragsbestand des Prototyps (Konstante CONTRACTS). `next_due_date` ist
# jeweils die erste Faelligkeit ab dem Stichtag - `due_dates_in_window` laeuft
# von dort ohnehin erst zurueck und dann wieder vorwaerts.
_CONTRACTS: tuple[tuple[str, str, int, str, int, date], ...] = (
    ("Gehalt Person A", "income", 298_000, "monthly", 28, date(2026, 8, 28)),
    ("Kindergeld", "income", 51_000, "monthly", 5, date(2026, 9, 5)),
    ("Miete", "expense", 118_000, "monthly", 1, date(2026, 9, 1)),
    ("Autokredit", "expense", 24_500, "monthly", 15, date(2026, 8, 15)),
    ("Strom (Abschlag)", "expense", 9_600, "monthly", 5, date(2026, 9, 5)),
    ("Internet & Telefon", "expense", 4_490, "monthly", 3, date(2026, 9, 3)),
    ("Mobilfunk (2 Karten)", "expense", 5_980, "monthly", 8, date(2026, 9, 8)),
    ("Streaming", "expense", 1_799, "monthly", 12, date(2026, 9, 12)),
    ("Fitnessstudio", "expense", 3_490, "monthly", 2, date(2026, 9, 2)),
    ("Haftpflicht & Hausrat", "expense", 2_150, "monthly", 10, date(2026, 9, 10)),
    ("Rundfunkbeitrag", "expense", 5_508, "quarterly", 15, date(2026, 8, 15)),
    ("KFZ-Versicherung", "expense", 61_800, "yearly", 1, date(2026, 10, 1)),
    ("KFZ-Steuer", "expense", 17_800, "yearly", 12, date(2026, 9, 12)),
)

# Konstante VARIABLE des Prototyps: Lebensmittel 680, Freizeit 220, Tanken 195,
# Sonstiges 250, Drogerie 130, Kleidung 85 = 1.560,00 EUR im Monat.
VARIABLE_TOTAL_MINOR = 156_000
# Konstante SPENT_THIS_MONTH: bis zum 12.08. bereits gebucht.
SPENT_THIS_MONTH_MINOR = 61_240


def _contract_id(index: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{index:012d}")


def _reference_input() -> ForecastInput:
    flows: list[ScheduledFlow] = []
    terms: list[ForecastContractTerm] = []
    income_monthly = 0
    fixed_monthly = 0

    for index, (label, direction, amount, frequency, due_day, next_due) in enumerate(_CONTRACTS):
        contract_id = _contract_id(index)
        # Erst je Vertrag normieren, dann summieren (Plan 5.2).
        monthly = contract_service.monthly_minor(amount, frequency)
        if direction == "income":
            income_monthly += monthly
        else:
            fixed_monthly += monthly
        terms.append(
            ForecastContractTerm(
                contract_id=contract_id,
                label=label,
                direction="income" if direction == "income" else "expense",
                amount_minor=amount,
                frequency=frequency,
            )
        )
        for due in contract_service.due_dates_in_window(
            next_due_date_value=next_due,
            frequency=frequency,
            due_rule="day_of_month",
            due_day=due_day,
            starts_on=None,
            ends_on=None,
            window_start=AS_OF,
            window_end=HORIZON_END,
        ):
            flows.append(
                ScheduledFlow(
                    on=due,
                    amount_minor=amount if direction == "income" else -amount,
                    label=label,
                    source_kind="contract",
                    source_id=contract_id,
                )
            )

    variable: list[VariableMonth] = []
    index = AS_OF.year * 12 + AS_OF.month - 1
    last = HORIZON_END.year * 12 + HORIZON_END.month - 1
    while index <= last:
        year, month = index // 12, index % 12 + 1
        variable.append(
            VariableMonth(
                year=year,
                month=month,
                planned_minor=VARIABLE_TOTAL_MINOR,
                booked_minor=(
                    SPENT_THIS_MONTH_MINOR
                    if (year, month) == (AS_OF.year, AS_OF.month)
                    else 0
                ),
            )
        )
        index += 1

    return ForecastInput(
        as_of=AS_OF,
        horizon_months=HORIZON_MONTHS,
        daily_until=HORIZON_END,
        opening_liquidity_minor=OPENING_MINOR,
        scheduled=tuple(flows),
        variable_monthly=tuple(variable),
        contract_terms=tuple(terms),
        bound_reserve_minor=34_000,
        safety_buffer_minor=BUFFER_MINOR,
        income_monthly_minor=income_monthly,
        fixed_monthly_minor=fixed_monthly,
        context=ForecastContext(
            last_booking_on=date(2026, 8, 12),
            contract_count=len(_CONTRACTS),
            income_count=2,
            variable_basis="max",
            excluded_account_names=("Tagesgeld",),
        ),
    )


# ---------------------------------------------------------------------------
# Hauptfall: der Beispielhaushalt des Prototyps
# ---------------------------------------------------------------------------


def test_prototype_household_normalised_monthly_values() -> None:
    data = _reference_input()
    # 298.000 + 51.000
    assert data.income_monthly_minor == 349_000
    # 118.000 + 24.500 + 9.600 + 4.490 + 5.980 + 1.799 + 3.490 + 2.150
    # + 1.836 (5.508 quartalsweise) + 5.150 (61.800 jaehrlich) + 1.483 (17.800 jaehrlich)
    assert data.fixed_monthly_minor == 178_478


def test_prototype_household_shortfall_matches_the_prototype() -> None:
    metrics = calculate_forecast(_reference_input()).metrics

    assert metrics.lowest_balance_minor == -28_243
    assert metrics.lowest_balance_on == date(2026, 10, 27)
    assert metrics.first_negative_on == date(2026, 10, 22)
    assert metrics.recovery_on == date(2026, 10, 28)
    assert metrics.days_below_zero == 9
    assert metrics.needed_against_negative_minor == 28_243
    assert metrics.needed_for_buffer_minor == 78_243
    assert metrics.average_monthly_surplus_minor == 14_522


def test_prototype_household_cause_is_rent_plus_car_insurance() -> None:
    """Ausloeser (01.10.) und Wirkung (22.10.) liegen drei Wochen auseinander -
    genau deshalb gibt es die Ursachenzuordnung (Abschnitt 15.1)."""
    result = calculate_forecast(_reference_input())

    assert result.cause is not None
    assert result.cause.on == date(2026, 10, 1)
    assert result.cause.total_minor == -179_800
    assert sorted(flow.label for flow in result.cause.flows) == [
        "KFZ-Versicherung",
        "Miete",
    ]


def test_prototype_household_current_month_uses_the_remainder_only() -> None:
    result = calculate_forecast(_reference_input())
    # Rest 156.000 - 61.240 = 94.760 auf 19 verbleibende Tage (13.-31.08.).
    assert result.daily[0].variable_minor == 4_987
    assert result.monthly[0].year == 2026
    assert result.monthly[0].month == 8
    assert result.monthly[0].variable_minor == 4_987 * 19
    # Ein voller Monat setzt dagegen den ganzen Ansatz an.
    assert result.monthly[1].variable_minor == 156_000


def test_prototype_household_quality_and_warnings() -> None:
    result = calculate_forecast(_reference_input())
    codes = {warning.code for warning in result.warnings}

    assert "below_zero" in codes
    assert "below_buffer" in codes
    # Juengster Umsatz einen Tag alt - keine Warnung ueber veraltete Daten.
    assert "stale_account_data" not in codes
    assert result.quality.level == "limited"
    assert result.quality.excluded_account_names == ("Tagesgeld",)
    assert result.quality.income_count == 2
    assert result.bound_reserve_minor == 34_000


def test_prototype_household_ratio_metrics() -> None:
    metrics = calculate_forecast(_reference_input()).metrics
    # 14.522 / 349.000
    assert metrics.savings_rate_percent == 4.2
    # 178.478 / 349.000
    assert metrics.fixed_cost_ratio_percent == 51.1
    # 110.000 / 178.478
    assert metrics.reserve_without_income_months == 0.6
    assert metrics.variable_monthly_minor == 156_000


# ---------------------------------------------------------------------------
# Muster aus Plan Abschnitt 14
# ---------------------------------------------------------------------------


def _simple_input(
    *,
    horizon_months: int,
    opening_minor: int,
    flows: tuple[ScheduledFlow, ...],
    safety_buffer_minor: int = 0,
) -> ForecastInput:
    return ForecastInput(
        as_of=AS_OF,
        horizon_months=horizon_months,
        daily_until=contract_service.add_months(AS_OF, horizon_months),
        opening_liquidity_minor=opening_minor,
        scheduled=flows,
        safety_buffer_minor=safety_buffer_minor,
        context=ForecastContext(last_booking_on=AS_OF, income_count=1),
    )


def _series(
    *, label: str, amount_minor: int, frequency: str, due_day: int, next_due: date, until: date
) -> tuple[ScheduledFlow, ...]:
    contract_id = _contract_id(99)
    return tuple(
        ScheduledFlow(
            on=due,
            amount_minor=amount_minor,
            label=label,
            source_kind="contract",
            source_id=contract_id,
        )
        for due in contract_service.due_dates_in_window(
            next_due_date_value=next_due,
            frequency=frequency,
            due_rule="day_of_month",
            due_day=due_day,
            starts_on=None,
            ends_on=None,
            window_start=AS_OF,
            window_end=until,
        )
    )


def test_only_fixed_costs_drain_the_account_by_the_monthly_rate() -> None:
    """Drei Monatsraten zu je 500,00 EUR ab dem 01.09."""
    flows = _series(
        label="Miete",
        amount_minor=-50_000,
        frequency="monthly",
        due_day=1,
        next_due=date(2026, 9, 1),
        until=date(2026, 11, 13),
    )
    result = calculate_forecast(
        _simple_input(horizon_months=3, opening_minor=200_000, flows=flows)
    )

    assert [flow.on for flow in flows] == [
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
    ]
    assert result.metrics.lowest_balance_minor == 50_000
    assert result.metrics.lowest_balance_on == date(2026, 11, 1)
    assert result.metrics.first_negative_on is None
    assert result.monthly[-1].closing_balance_minor == 50_000


def test_quarterly_rate_hits_four_times_in_twelve_months() -> None:
    flows = _series(
        label="Rundfunkbeitrag",
        amount_minor=-6_000,
        frequency="quarterly",
        due_day=15,
        next_due=date(2026, 10, 15),
        until=date(2027, 8, 13),
    )
    assert [flow.on for flow in flows] == [
        date(2026, 10, 15),
        date(2027, 1, 15),
        date(2027, 4, 15),
        date(2027, 7, 15),
    ]
    result = calculate_forecast(
        _simple_input(horizon_months=12, opening_minor=100_000, flows=flows)
    )
    assert result.metrics.balance_in_12_months_minor == 76_000
    assert contract_service.monthly_minor(6_000, "quarterly") == 2_000


def test_year_with_christmas_bonus_shows_the_spike_in_one_month() -> None:
    salary = _series(
        label="Gehalt",
        amount_minor=250_000,
        frequency="monthly",
        due_day=28,
        next_due=date(2026, 8, 28),
        until=date(2027, 8, 13),
    )
    bonus = (
        ScheduledFlow(
            on=date(2026, 11, 30),
            amount_minor=150_000,
            label="Weihnachtsgeld",
            source_kind="contract",
            source_id=_contract_id(98),
        ),
    )
    result = calculate_forecast(
        _simple_input(horizon_months=12, opening_minor=0, flows=salary + bonus)
    )
    november = next(row for row in result.monthly if (row.year, row.month) == (2026, 11))
    october = next(row for row in result.monthly if (row.year, row.month) == (2026, 10))
    assert october.income_minor == 250_000
    assert november.income_minor == 400_000
    # Zwoelf Gehaelter (28.08.2026 bis 28.07.2027) plus Weihnachtsgeld; die
    # Rate vom 28.08.2027 liegt hinter dem Horizontende 13.08.2027.
    assert [flow.on.isoformat() for flow in salary][-1] == "2027-07-28"
    assert len(salary) == 12
    assert result.metrics.balance_in_12_months_minor == 12 * 250_000 + 150_000


def test_run_that_dips_below_zero_and_recovers() -> None:
    flows = (
        ScheduledFlow(
            on=date(2026, 9, 1),
            amount_minor=-100_000,
            label="Jahresrechnung",
            source_kind="contract",
            source_id=_contract_id(97),
        ),
        ScheduledFlow(
            on=date(2026, 9, 10),
            amount_minor=200_000,
            label="Gehalt",
            source_kind="contract",
            source_id=_contract_id(96),
        ),
    )
    result = calculate_forecast(
        _simple_input(
            horizon_months=2, opening_minor=30_000, flows=flows, safety_buffer_minor=50_000
        )
    )
    metrics = result.metrics

    assert metrics.first_below_buffer_on == AS_OF
    assert metrics.first_negative_on == date(2026, 9, 1)
    assert metrics.lowest_balance_minor == -70_000
    assert metrics.lowest_balance_on == date(2026, 9, 1)
    assert metrics.recovery_on == date(2026, 9, 10)
    assert metrics.days_below_zero == 9
    assert metrics.needed_against_negative_minor == 70_000
    assert metrics.needed_for_buffer_minor == 120_000
    assert result.cause is not None
    assert result.cause.on == date(2026, 9, 1)
    assert result.cause.total_minor == -100_000
