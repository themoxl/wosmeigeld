"""Abbildung von `loan_terms_json` auf einen Tilgungsplan-Entwurf.

Reine Funktion ohne DB: `_debt_plan_from_loan_terms` entscheidet, ob aus den
Programmkonditionen ueberhaupt ein Plan entstehen darf. Der Kern der Pruefung ist,
dass nichts geraten wird -- fehlt der Zinssatz, entsteht kein Plan, statt einen
plausiblen Wert einzusetzen.
"""

from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.data.funding_packages.bayernlabo_eigenwohnraum import PACKAGE as BAYERN_PACKAGE
from app.data.funding_packages.kfw_jung_kauft_alt import PACKAGE
from app.services.debt_calculation_service import (
    DebtPlanInput as EngineDebtPlanInput,
)
from app.services.debt_calculation_service import (
    RatePeriodInput as EngineRatePeriodInput,
)
from app.services.debt_calculation_service import (
    SpecialRepaymentInput as EngineSpecialRepaymentInput,
)
from app.services.debt_calculation_service import (
    calculate_debt_schedule,
)
from app.services.funding_catalog_service import _debt_plan_from_loan_terms

_SOURCE_ID = uuid4()
_STARTS_ON = date(2026, 9, 1)


def _build(loan_terms: dict[str, object], *, principal_minor: int = 100_000_00):
    warnings: list[str] = []
    result = _debt_plan_from_loan_terms(
        loan_terms,
        financing_source_id=_SOURCE_ID,
        principal_minor=principal_minor,
        name="Testdarlehen",
        starts_on=_STARTS_ON,
        warnings=warnings,
    )
    return result, warnings


def test_kfw_124_konditionen_ergeben_ohne_zinssatz_keinen_plan() -> None:
    # Genau der Seed-Stand von KfW 124: Laufzeit und tilgungsfreie Zeit sind
    # bekannt, der effektive Jahreszins bewusst nicht (er wird taeglich
    # aktualisiert). Ein geratener Zins saehe im Tilgungsplan wie eine Zusage aus.
    result, warnings = _build({"max_term_months": 420, "max_repayment_free_months": 60})
    assert result is None
    assert warnings == ["kein_zinssatz_im_katalog"]


def test_vollstaendige_konditionen_ergeben_plan_mit_einer_zinsphase() -> None:
    result, warnings = _build(
        {
            "max_term_months": 420,
            "max_repayment_free_months": 60,
            "nominal_rate_bps_annual": 312,
        }
    )
    assert result is not None
    plan, periods, special_repayments = result
    assert warnings == []
    assert plan.project_financing_source_id == _SOURCE_ID
    assert plan.principal_limit_minor == 100_000_00
    assert plan.opening_balance_minor == 100_000_00
    assert plan.starts_on == "2026-09-01"
    assert plan.amortization_mode == "full_repayment_term"
    assert plan.target_term_months == 420
    assert plan.repayment_free_months == 60
    assert plan.status == "draft"
    assert special_repayments == []

    assert len(periods) == 1
    assert periods[0].starts_after_month == 0
    assert periods[0].ends_after_month is None
    assert periods[0].nominal_rate_bps_annual == 312
    # `assumed` haelt im Tilgungsplan auseinander, was aus dem Programm stammt
    # und was der Nutzer vertraglich zugesagt bekommen hat.
    assert periods[0].rate_kind == "assumed"


def test_zinsverbilligung_ergibt_zwei_luecklose_phasen() -> None:
    result, warnings = _build(
        {
            "max_term_months": 360,
            "nominal_rate_bps_annual": 420,
            "subsidized_rate_bps_annual": 180,
            "subsidized_months": 120,
        }
    )
    assert result is not None
    _, periods, _ = result
    assert warnings == []
    assert len(periods) == 2
    assert (periods[0].starts_after_month, periods[0].ends_after_month) == (0, 119)
    assert periods[0].nominal_rate_bps_annual == 180
    assert periods[1].starts_after_month == 120
    assert periods[1].ends_after_month is None
    assert periods[1].nominal_rate_bps_annual == 420
    assert all(period.rate_kind == "assumed" for period in periods)


def test_anfaengliche_tilgung_wenn_keine_laufzeit_hinterlegt_ist() -> None:
    result, warnings = _build({"nominal_rate_bps_annual": 350, "initial_repayment_rate_bps": 200})
    assert result is not None
    plan, _, _ = result
    assert warnings == []
    assert plan.amortization_mode == "initial_repayment"
    assert plan.initial_repayment_rate_bps == 200
    assert plan.target_term_months is None


def test_ohne_tilgungsvorgabe_entsteht_kein_plan() -> None:
    result, warnings = _build({"nominal_rate_bps_annual": 350})
    assert result is None
    assert warnings == ["keine_tilgungsvorgabe_im_katalog"]


def test_werte_ausserhalb_der_schemagrenzen_werden_gekappt_und_benannt() -> None:
    # `repayment_free_months` ist im Schema auf 120 begrenzt, `target_term_months`
    # auf 600. Ein Programm darf daran nicht scheitern -- aber die Kappung muss
    # sichtbar werden, statt still eine andere Kondition zu behaupten.
    result, warnings = _build(
        {
            "max_term_months": 720,
            "max_repayment_free_months": 180,
            "nominal_rate_bps_annual": 300,
        }
    )
    assert result is not None
    plan, _, _ = result
    assert plan.target_term_months == 600
    assert plan.repayment_free_months == 120
    assert set(warnings) == {"laufzeit_gekappt", "tilgungsfreie_zeit_gekappt"}


def test_ohne_darlehensbetrag_entsteht_kein_plan() -> None:
    result, warnings = _build(
        {"nominal_rate_bps_annual": 300, "max_term_months": 360}, principal_minor=0
    )
    assert result is None
    assert warnings == []


def test_unbrauchbare_typen_fuehren_nicht_zum_absturz() -> None:
    # Katalogdaten kommen aus einem Import und sind nicht typgeprueft. Ein
    # `true` oder eine Zeichenkette darf nur dazu fuehren, dass kein Plan
    # entsteht -- nicht zu einem Fehler bei der Uebernahme.
    result, warnings = _build({"nominal_rate_bps_annual": True, "max_term_months": "420"})
    assert result is None
    assert warnings == ["kein_zinssatz_im_katalog"]


def _kfw_terms() -> dict[str, object]:
    return PACKAGE["programs"][0]["loan_terms_json"]


def test_kfw_308_requires_an_explicit_variant() -> None:
    with pytest.raises(HTTPException) as error:
        _build(_kfw_terms())
    assert error.value.status_code == 422
    assert error.value.detail == "loan_term_variant_required"


def _bayern_terms() -> dict[str, object]:
    return BAYERN_PACKAGE["programs"][0]["loan_terms_json"]


def test_bayernlabo_requires_explicit_rate_option() -> None:
    with pytest.raises(HTTPException) as error:
        _build(_bayern_terms())
    assert error.value.status_code == 422
    assert error.value.detail == "loan_rate_option_required"


def test_bayernlabo_rate_option_creates_open_initial_repayment_plan() -> None:
    warnings: list[str] = []
    result = _debt_plan_from_loan_terms(
        _bayern_terms(),
        financing_source_id=_SOURCE_ID,
        principal_minor=100_000_00,
        name="BayernLabo",
        starts_on=_STARTS_ON,
        warnings=warnings,
        loan_rate_option_id="fixed_15_years",
        follow_up_rate_bps_annual=450,
    )
    assert result is not None
    plan, periods, _ = result
    assert warnings == []
    assert plan.amortization_mode == "initial_repayment"
    assert plan.initial_repayment_rate_bps == 200
    assert plan.target_term_months is None
    assert plan.opening_balance_minor == 100_000_00
    assert plan.repayment_free_months == 12
    assert [(period.starts_after_month, period.ends_after_month) for period in periods] == [
        (0, 179),
        (180, None),
    ]
    assert periods[0].nominal_rate_bps_annual == 295
    assert periods[0].rate_kind == "contracted"
    assert periods[1].nominal_rate_bps_annual == 450
    assert periods[1].rate_kind == "assumed"


@pytest.mark.parametrize(
    ("variant_id", "term", "grace", "rate"),
    [
        ("annuity_7_10", 120, 24, 1),
        ("annuity_11_25", 300, 36, 25),
        ("annuity_26_35", 420, 60, 53),
    ],
)
def test_every_kfw_annuity_variant_resolves_to_its_coupled_terms(
    variant_id: str, term: int, grace: int, rate: int
) -> None:
    warnings: list[str] = []
    result = _debt_plan_from_loan_terms(
        _kfw_terms(),
        financing_source_id=_SOURCE_ID,
        principal_minor=100_000_00,
        name="KfW 308",
        starts_on=_STARTS_ON,
        warnings=warnings,
        loan_term_variant_id=variant_id,
        selected_term_months=term,
        selected_repayment_free_months=grace,
        follow_up_rate_bps_annual=450,
    )
    assert result is not None
    plan, periods, special_repayments = result
    assert warnings == []
    assert plan.amortization_mode == "full_repayment_term"
    assert plan.target_term_months == term - grace
    assert plan.repayment_free_months == grace
    assert plan.opening_balance_minor == 100_000_00
    assert periods[0].nominal_rate_bps_annual == rate
    assert periods[0].label == "Veröffentlichte KfW-Kondition"
    assert special_repayments == []


def test_kfw_35_year_variant_has_30_repayment_years_and_follow_up_stage() -> None:
    warnings: list[str] = []
    result = _debt_plan_from_loan_terms(
        _kfw_terms(),
        financing_source_id=_SOURCE_ID,
        principal_minor=180_000_00,
        name="KfW 308",
        starts_on=_STARTS_ON,
        warnings=warnings,
        loan_term_variant_id="annuity_26_35",
        selected_term_months=420,
        selected_repayment_free_months=60,
        follow_up_rate_bps_annual=450,
    )
    assert result is not None
    plan, periods, _ = result
    assert plan.target_term_months == 360
    assert [(period.starts_after_month, period.ends_after_month) for period in periods] == [
        (0, 119),
        (120, None),
    ]
    assert periods[0].rate_kind == "contracted"
    assert periods[1].rate_kind == "assumed"
    assert periods[1].nominal_rate_bps_annual == 450


def test_kfw_bullet_variant_creates_interest_only_plan_and_final_repayment() -> None:
    warnings: list[str] = []
    result = _debt_plan_from_loan_terms(
        _kfw_terms(),
        financing_source_id=_SOURCE_ID,
        principal_minor=140_000_00,
        name="KfW 308 endfällig",
        starts_on=_STARTS_ON,
        warnings=warnings,
        loan_term_variant_id="bullet_7_10",
        selected_term_months=120,
        selected_repayment_free_months=0,
    )
    assert result is not None
    plan, periods, special_repayments = result
    assert plan.amortization_mode == "initial_repayment"
    assert plan.initial_repayment_rate_bps == 0
    assert periods[0].nominal_rate_bps_annual == 69
    assert special_repayments[0].occurs_after_month == 119
    assert special_repayments[0].amount_minor == 140_000_00

    schedule = calculate_debt_schedule(
        EngineDebtPlanInput(
            principal_limit_minor=plan.principal_limit_minor,
            opening_balance_minor=plan.opening_balance_minor,
            amortization_mode=plan.amortization_mode,
            initial_repayment_rate_bps=plan.initial_repayment_rate_bps,
            repayment_free_months=plan.repayment_free_months,
            calculation_horizon_months=120,
            rate_periods=tuple(
                EngineRatePeriodInput(**period.model_dump(exclude={"label"})) for period in periods
            ),
            special_repayments=tuple(
                EngineSpecialRepaymentInput(
                    occurs_after_month=repayment.occurs_after_month,
                    amount_minor=repayment.amount_minor,
                )
                for repayment in special_repayments
            ),
        )
    )
    assert all(row.principal_payment_minor == 0 for row in schedule.rows[:-1])
    assert schedule.rows[-1].interest_paid_minor > 0
    assert schedule.rows[-1].special_repayment_minor == 140_000_00
    assert schedule.rows[-1].cash_outflow_minor == (
        schedule.rows[-1].interest_paid_minor + 140_000_00
    )
    assert schedule.peak_payment_minor == schedule.rows[-1].cash_outflow_minor
    assert schedule.first_negative_amortization_month is None
    assert "loan_negative_amortization" not in schedule.warnings
    assert schedule.ending_balance_minor == 0
    assert schedule.payoff_month == 119


def test_kfw_35_year_schedule_reaches_zero_at_contract_end() -> None:
    warnings: list[str] = []
    result = _debt_plan_from_loan_terms(
        _kfw_terms(),
        financing_source_id=_SOURCE_ID,
        principal_minor=180_000_00,
        name="KfW 308",
        starts_on=_STARTS_ON,
        warnings=warnings,
        loan_term_variant_id="annuity_26_35",
        selected_term_months=420,
        selected_repayment_free_months=60,
        follow_up_rate_bps_annual=450,
    )
    assert result is not None
    plan, periods, _ = result
    schedule = calculate_debt_schedule(
        EngineDebtPlanInput(
            principal_limit_minor=plan.principal_limit_minor,
            opening_balance_minor=plan.opening_balance_minor,
            amortization_mode=plan.amortization_mode,
            target_term_months=plan.target_term_months,
            repayment_free_months=plan.repayment_free_months,
            calculation_horizon_months=420,
            rate_periods=tuple(
                EngineRatePeriodInput(**period.model_dump(exclude={"label"})) for period in periods
            ),
        )
    )
    assert all(row.principal_payment_minor == 0 for row in schedule.rows[:60])
    assert schedule.payoff_month == 419
    assert schedule.ending_balance_minor == 0
