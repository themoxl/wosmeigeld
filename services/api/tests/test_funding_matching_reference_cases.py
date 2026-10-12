"""Referenzfaelle fuer die Foerder-Matching-Engine (Plan 15.3, Teilmenge ohne
Importpipeline -- diese folgt in `test_funding_import_service.py`)."""

from datetime import date
from typing import Any

from app.services.funding_matching_service import (
    EligibilityFacts,
    ProgramMatchInput,
    ProgramMatchResult,
    evaluate_combinations,
    match_program,
)

TODAY = date(2026, 8, 4)


def _program(**overrides: Any) -> ProgramMatchInput:
    base: dict[str, Any] = {
        "program_version_id": "v1",
        "program_semantic_key": "test_program",
        "country_code": "DE",
        "region_codes": (),
        "scope": "national",
        "applicant_types": (),
        "project_types": ("home_build",),
        "valid_from": None,
        "valid_to": None,
        "review_due_at": date(2027, 1, 1),
        "normalization_status": "rules_complete",
        "criteria_json": {"all": []},
        "benefit_json": {"type": "fixed_grant", "amount_minor": 0},
        "combination_rules_json": {},
    }
    base.update(overrides)
    return ProgramMatchInput(**base)


def _match(
    program: ProgramMatchInput, facts: EligibilityFacts, **kwargs: Any
) -> ProgramMatchResult:
    defaults: dict[str, Any] = {
        "project_type": "home_build",
        "country_code": "DE",
        "region_code": None,
        "eligible_cost_basis_minor": None,
        "as_of": TODAY,
    }
    defaults.update(kwargs)
    result = match_program(program, facts, **defaults)
    assert result is not None
    return result


def _match_or_none(
    program: ProgramMatchInput, facts: EligibilityFacts, **kwargs: Any
) -> ProgramMatchResult | None:
    defaults: dict[str, Any] = {
        "project_type": "home_build",
        "country_code": "DE",
        "region_code": None,
        "eligible_cost_basis_minor": None,
        "as_of": TODAY,
    }
    defaults.update(kwargs)
    return match_program(program, facts, **defaults)


def test_regional_filter_bavaria_vs_baden_wuerttemberg() -> None:
    bavaria_program = _program(scope="regional", region_codes=("DE-BY",))
    facts = EligibilityFacts()

    in_bavaria = _match_or_none(bavaria_program, facts, region_code="DE-BY")
    in_bw = _match_or_none(bavaria_program, facts, region_code="DE-BW")

    assert in_bavaria is not None
    assert in_bw is None


def test_income_limit_with_child_staggering() -> None:
    program = _program(
        criteria_json={
            "linear_threshold": {
                "field": "household_income_year_minor",
                "op": "lte",
                "base_minor": 9_000_000,
                "per_unit_field": "children_count",
                "unit_offset": 1,
                "per_unit_amount_minor": 1_000_000,
                "label": "Haushaltseinkommen",
            }
        }
    )
    # 1 Kind: Grenze bleibt bei 90.000 EUR (unit_offset=1 -> 0 weitere Kinder).
    within_limit = EligibilityFacts(household_income_year_minor=9_000_000, children_count=1)
    result = _match(program, within_limit)
    assert result.status == "eligible"

    # 2 Kinder: Grenze steigt auf 100.000 EUR - 95.000 EUR liegt jetzt darunter.
    two_children = EligibilityFacts(household_income_year_minor=9_500_000, children_count=2)
    assert _match(program, two_children).status == "eligible"

    over_limit = EligibilityFacts(household_income_year_minor=9_500_000, children_count=1)
    assert _match(program, over_limit).status == "ineligible"


def test_age_check_on_application_date() -> None:
    program = _program(
        criteria_json={
            "date_compare": {
                "field": "applicant_birth_date",
                "op": "lte",
                "value": "2004-08-04",
                "label": "Mindestalter 18 zum Antragstag",
            }
        }
    )
    of_age = EligibilityFacts(extra={"applicant_birth_date": date(2000, 1, 1)})
    under_age = EligibilityFacts(extra={"applicant_birth_date": date(2010, 1, 1)})

    assert _match(program, of_age, as_of=TODAY).status == "eligible"
    assert _match(program, under_age, as_of=TODAY).status == "ineligible"


def test_missing_information_yields_potentially_eligible_not_ineligible() -> None:
    program = _program(
        criteria_json={"compare": {"field": "self_occupied", "op": "eq", "value": True}}
    )
    result = _match(program, EligibilityFacts())
    assert result.status == "potentially_eligible"
    assert len(result.criteria_missing) == 1
    assert result.criteria_violated == ()


def test_explicit_violation_yields_ineligible_with_one_documented_criterion() -> None:
    program = _program(
        criteria_json={"compare": {"field": "self_occupied", "op": "eq", "value": True}}
    )
    result = _match(program, EligibilityFacts(self_occupied=False))
    assert result.status == "ineligible"
    assert len(result.criteria_violated) == 1
    assert result.criteria_violated[0].field == "self_occupied"


def test_manual_condition_blocks_eligible_until_confirmed() -> None:
    program = _program(
        criteria_json={
            "manual_check": {"key": "before_construction_start", "label": "Vor Baubeginn"}
        }
    )
    unconfirmed = _match(program, EligibilityFacts())
    assert unconfirmed.status == "potentially_eligible"
    assert len(unconfirmed.criteria_manual) == 1

    confirmed = _match(
        program, EligibilityFacts(manual_confirmations=frozenset({"before_construction_start"}))
    )
    assert confirmed.status == "eligible"


def test_percentage_grant_with_eligible_cost_cap() -> None:
    program = _program(
        benefit_json={"type": "percent_of_costs", "rate_bps": 1000, "cap_minor": 500_000}
    )
    under_cap = _match(program, EligibilityFacts(), eligible_cost_basis_minor=3_000_000)
    assert under_cap.benefit.grant_min_minor == 300_000

    over_cap = _match(program, EligibilityFacts(), eligible_cost_basis_minor=10_000_000)
    assert over_cap.benefit.grant_min_minor == 500_000


def test_percentage_loan_can_be_combined_with_child_grant() -> None:
    program = _program(
        benefit_json={
            "type": "combined",
            "components": [
                {"type": "loan_percent_of_costs", "rate_bps": 3333},
                {
                    "type": "per_unit",
                    "unit_field": "children_count",
                    "amount_per_unit_minor": 500_000,
                },
            ],
        }
    )
    result = _match(
        program,
        EligibilityFacts(children_count=2),
        eligible_cost_basis_minor=30_000_000,
    )
    assert result.benefit.loan_max_minor == 9_999_000
    assert result.benefit.grant_max_minor == 1_000_000


def test_tiered_loan_amount_by_children() -> None:
    program = _program(
        benefit_json={
            "type": "loan_tiered",
            "tier_field": "children_count",
            "tiers": [
                {"min": 1, "max": 2, "amount_minor": 17_000_000},
                {"min": 3, "max": 4, "amount_minor": 22_000_000},
                {"min": 5, "amount_minor": 27_000_000},
            ],
        }
    )
    two_children = _match(program, EligibilityFacts(children_count=2))
    assert two_children.benefit.loan_max_minor == 17_000_000

    five_children = _match(program, EligibilityFacts(children_count=5))
    assert five_children.benefit.loan_max_minor == 27_000_000


def test_two_explicitly_combinable_programs_are_compatible() -> None:
    grant = _program(
        program_version_id="grant-v1",
        program_semantic_key="grant_program",
        combination_rules_json={"allowed_program_keys": ["loan_program"]},
    )
    loan = _program(
        program_version_id="loan-v1",
        program_semantic_key="loan_program",
        combination_rules_json={"allowed_program_keys": ["grant_program"]},
    )
    results = evaluate_combinations([grant, loan])
    assert len(results) == 1
    assert results[0].status == "compatible"


def test_two_excluded_programs_are_incompatible_and_never_summed() -> None:
    a = _program(
        program_version_id="a-v1",
        program_semantic_key="program_a",
        combination_rules_json={"excluded_program_keys": ["program_b"]},
    )
    b = _program(program_version_id="b-v1", program_semantic_key="program_b")
    results = evaluate_combinations([a, b])
    assert results[0].status == "incompatible"


def test_unknown_combinability_is_never_reported_as_compatible() -> None:
    a = _program(program_version_id="a-v1", program_semantic_key="program_a")
    b = _program(program_version_id="b-v1", program_semantic_key="program_b")
    results = evaluate_combinations([a, b])
    assert results[0].status in ("unknown", "manual_check")
    assert results[0].status != "compatible"


def test_stale_version_past_review_due_at_is_unknown() -> None:
    program = _program(review_due_at=date(2026, 1, 1))
    result = _match(program, EligibilityFacts(), as_of=date(2026, 6, 1))
    assert result.status == "unknown"
    assert result.unknown_reason == "funding_data_stale"


def test_discovery_only_program_is_unknown_not_ineligible() -> None:
    program = _program(normalization_status="discovery_only")
    result = _match(program, EligibilityFacts())
    assert result.status == "unknown"
