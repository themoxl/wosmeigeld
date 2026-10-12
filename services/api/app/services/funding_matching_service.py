"""Reine Foerder-Matching-Engine (Plan 6.2-6.4): wertet die kleine, validierte
Regel-DSL aus `criteria_json`/`benefit_json`/`combination_rules_json` gegen ein
privates Foerderprofil aus. Kein DB-Zugriff, kein Netz, kein LLM - deterministisch
und zentgenau nachvollziehbar. `funding_catalog_service.py` bindet dies an echte
Programmversionen und das Foerderprofil an.

Vier-wertige Kriterienlogik (met/violated/missing/manual) statt einfachem
Bool: eine fehlende Angabe darf laut Plan niemals zu `ineligible` fuehren,
nur ein ausdruecklich verletztes Kriterium. `manual`-Kriterien werden nur dann
als erfuellt gewertet, wenn der Nutzer sie im Foerderprofil ausdruecklich
bestaetigt hat (`manual_confirmations`)."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MatchStatus = Literal["eligible", "potentially_eligible", "ineligible", "unknown"]
CriterionOutcome = Literal["met", "violated", "missing", "manual"]
CombinationStatus = Literal["compatible", "incompatible", "manual_check", "unknown"]

_MAX_RANKED_PROGRAMS = 10
_MAX_COMBINATIONS = 50


class FundingMatchingError(ValueError):
    """Eine Programmversion enthaelt eine nicht unterstuetzte Regelform.

    Fuehrt beim Aufrufer zu `unknown`, nie zu einem Absturz - eine kaputte
    oder zu neue Regelform darf laut Plan 6.3 nur den Katalogtreffer selbst,
    nie die uebrige Anwendung stoeren."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EligibilityFacts(BaseModel):
    """Privates Foerderprofil (Plan 4.10) - validierte, benannte Felder statt
    beliebigem JSON. `extra` haelt programmspezifische Zusatzangaben, die noch
    kein benanntes Feld haben, ohne das Schema staendig brechen zu muessen."""

    model_config = ConfigDict(frozen=True)

    application_date: date | None = None
    applicant_type: str | None = None
    household_income_year_minor: int | None = Field(default=None, ge=0)
    children_count: int | None = Field(default=None, ge=0)
    self_occupied: bool | None = None
    existing_homeowner: bool | None = None
    disability_severity_percent: int | None = Field(default=None, ge=0, le=100)
    manual_confirmations: frozenset[str] = Field(default_factory=frozenset)
    extra: dict[str, Any] = Field(default_factory=dict)

    def get(self, field: str) -> Any:
        if hasattr(self, field) and field != "extra":
            return getattr(self, field)
        return self.extra.get(field)


class CriterionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    outcome: CriterionOutcome
    field: str | None = None
    input_value: Any | None = None
    rule: dict[str, Any]


class ProgramMatchInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_version_id: str
    program_semantic_key: str
    country_code: str
    region_codes: tuple[str, ...] = ()
    scope: Literal["supranational", "national", "regional", "municipal"]
    applicant_types: tuple[str, ...] = ()
    project_types: tuple[str, ...] = ()
    valid_from: date | None = None
    valid_to: date | None = None
    review_due_at: date
    normalization_status: Literal["discovery_only", "rules_partial", "rules_complete", "retired"]
    criteria_json: dict[str, Any]
    benefit_json: dict[str, Any]
    combination_rules_json: dict[str, Any] = Field(default_factory=dict)


class BenefitEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    grant_min_minor: int | None = None
    grant_max_minor: int | None = None
    loan_min_minor: int | None = None
    loan_max_minor: int | None = None


class ProgramMatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_version_id: str
    status: MatchStatus
    benefit: BenefitEstimate
    criteria_met: tuple[CriterionResult, ...]
    criteria_violated: tuple[CriterionResult, ...]
    criteria_missing: tuple[CriterionResult, ...]
    criteria_manual: tuple[CriterionResult, ...]
    unknown_reason: str | None = None


def _prefilter_ok(program: ProgramMatchInput, facts: EligibilityFacts, project_type: str) -> bool:
    """Plan 6.1: Land/Region, Projektart, Antragstellerart, Gueltigkeit und
    Programmstatus werden VOR der Kriterienauswertung geprueft - ein
    bayerisches Programm taucht in einem BW-Projekt gar nicht erst auf."""
    if program.project_types and project_type not in program.project_types:
        return False
    if program.applicant_types and facts.applicant_type is not None:
        if facts.applicant_type not in program.applicant_types:
            return False
    as_of = facts.application_date or date.today()
    if program.valid_from is not None and as_of < program.valid_from:
        return False
    if program.valid_to is not None and as_of > program.valid_to:
        return False
    return True


def _region_matches(program: ProgramMatchInput, country_code: str, region_code: str | None) -> bool:
    if program.country_code != country_code:
        return False
    if program.scope in ("supranational", "national"):
        return True
    if not program.region_codes:
        return True
    return region_code is not None and region_code in program.region_codes


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return bool(actual == expected)
    if op == "neq":
        return bool(actual != expected)
    if op == "lt":
        return bool(actual < expected)
    if op == "lte":
        return bool(actual <= expected)
    if op == "gt":
        return bool(actual > expected)
    if op == "gte":
        return bool(actual >= expected)
    if op == "in":
        return actual in expected
    if op == "contains":
        return expected in actual
    raise FundingMatchingError("funding_rule_unsupported", f"Unbekannter Vergleichsoperator: {op}")


def _lookup_tier(value: Any, tiers: list[dict[str, Any]]) -> dict[str, Any] | None:
    for tier in tiers:
        lower = tier.get("min")
        upper = tier.get("max")
        if lower is not None and value < lower:
            continue
        if upper is not None and value > upper:
            continue
        return tier
    return None


def _leaf_result(node: dict[str, Any], facts: EligibilityFacts) -> CriterionResult:
    if "present" in node:
        field = node["present"]["field"]
        value = facts.get(field)
        outcome: CriterionOutcome = "met" if value is not None else "missing"
        return CriterionResult(
            key=f"present:{field}",
            label=node["present"].get("label", f"{field} vorhanden"),
            outcome=outcome,
            field=field,
            input_value=value,
            rule=node,
        )

    if "compare" in node:
        rule = node["compare"]
        field = rule["field"]
        value = facts.get(field)
        if value is None:
            return CriterionResult(
                key=f"compare:{field}",
                label=rule.get("label", field),
                outcome="missing",
                field=field,
                input_value=None,
                rule=node,
            )
        outcome = "met" if _compare(rule["op"], value, rule["value"]) else "violated"
        return CriterionResult(
            key=f"compare:{field}",
            label=rule.get("label", field),
            outcome=outcome,
            field=field,
            input_value=value,
            rule=node,
        )

    if "date_compare" in node:
        rule = node["date_compare"]
        field = rule["field"]
        value = facts.get(field)
        if value is None:
            return CriterionResult(
                key=f"date_compare:{field}",
                label=rule.get("label", field),
                outcome="missing",
                field=field,
                input_value=None,
                rule=node,
            )
        raw_target = rule["value"]
        target = date.fromisoformat(raw_target) if isinstance(raw_target, str) else raw_target
        outcome = "met" if _compare(rule["op"], value, target) else "violated"
        return CriterionResult(
            key=f"date_compare:{field}",
            label=rule.get("label", field),
            outcome=outcome,
            field=field,
            input_value=value,
            rule=node,
        )

    if "linear_threshold" in node:
        rule = node["linear_threshold"]
        field = rule["field"]
        value = facts.get(field)
        if value is None:
            return CriterionResult(
                key=f"linear_threshold:{field}",
                label=rule.get("label", field),
                outcome="missing",
                field=field,
                input_value=None,
                rule=node,
            )
        unit_value = facts.get(rule["per_unit_field"]) if rule.get("per_unit_field") else 0
        if unit_value is None:
            return CriterionResult(
                key=f"linear_threshold:{field}",
                label=rule.get("label", field),
                outcome="missing",
                field=field,
                input_value=value,
                rule=node,
            )
        extra_units = max(int(unit_value) - int(rule.get("unit_offset", 0)), 0)
        per_unit = int(rule.get("per_unit_amount_minor", 0))
        threshold = int(rule["base_minor"]) + extra_units * per_unit
        outcome = "met" if _compare(rule.get("op", "lte"), value, threshold) else "violated"
        return CriterionResult(
            key=f"linear_threshold:{field}",
            label=rule.get("label", field),
            outcome=outcome,
            field=field,
            input_value=value,
            rule=node,
        )

    if "tier_lookup" in node:
        rule = node["tier_lookup"]
        field = rule["field"]
        value = facts.get(field)
        if value is None:
            return CriterionResult(
                key=f"tier_lookup:{field}",
                label=rule.get("label", field),
                outcome="missing",
                field=field,
                input_value=None,
                rule=node,
            )
        matched = _lookup_tier(value, rule["tiers"])
        outcome = "met" if matched is not None else "violated"
        return CriterionResult(
            key=f"tier_lookup:{field}",
            label=rule.get("label", field),
            outcome=outcome,
            field=field,
            input_value=value,
            rule=node,
        )

    if "manual_check" in node:
        rule = node["manual_check"]
        check_key = rule["key"]
        outcome = "met" if check_key in facts.manual_confirmations else "manual"
        return CriterionResult(
            key=f"manual_check:{check_key}",
            label=rule.get("label", check_key),
            outcome=outcome,
            field=None,
            input_value=None,
            rule=node,
        )

    raise FundingMatchingError("funding_rule_unsupported", f"Unbekannter Regelknoten: {list(node)}")


def _evaluate_node(
    node: dict[str, Any], facts: EligibilityFacts
) -> tuple[CriterionOutcome, list[CriterionResult]]:
    if "all" in node:
        children = [_evaluate_node(child, facts) for child in node["all"]]
        outcomes = [outcome for outcome, _ in children]
        leaves = [leaf for _, leaves in children for leaf in leaves]
        if "violated" in outcomes:
            return "violated", leaves
        if "missing" in outcomes:
            return "missing", leaves
        if "manual" in outcomes:
            return "manual", leaves
        return "met", leaves

    if "any" in node:
        children = [_evaluate_node(child, facts) for child in node["any"]]
        outcomes = [outcome for outcome, _ in children]
        leaves = [leaf for _, leaves in children for leaf in leaves]
        if "met" in outcomes:
            return "met", leaves
        if "missing" in outcomes:
            return "missing", leaves
        if "manual" in outcomes:
            return "manual", leaves
        return "violated", leaves

    if "not" in node:
        outcome, leaves = _evaluate_node(node["not"], facts)
        inverted: CriterionOutcome = (
            "violated" if outcome == "met" else "met" if outcome == "violated" else outcome
        )
        return inverted, leaves

    leaf = _leaf_result(node, facts)
    return leaf.outcome, [leaf]


def _status_from_outcome(root_outcome: CriterionOutcome) -> MatchStatus:
    if root_outcome == "violated":
        return "ineligible"
    if root_outcome == "met":
        return "eligible"
    return "potentially_eligible"


def _calculate_benefit(
    node: dict[str, Any], facts: EligibilityFacts, eligible_cost_basis_minor: int | None
) -> BenefitEstimate:
    kind = node.get("type")

    if kind == "fixed_grant":
        amount = int(node["amount_minor"])
        return BenefitEstimate(grant_min_minor=amount, grant_max_minor=amount)

    if kind == "percent_of_costs":
        if eligible_cost_basis_minor is None:
            return BenefitEstimate()
        raw = eligible_cost_basis_minor * int(node["rate_bps"]) // 10_000
        cap = node.get("cap_minor")
        amount = min(raw, cap) if cap is not None else raw
        return BenefitEstimate(grant_min_minor=amount, grant_max_minor=amount)

    if kind == "loan_percent_of_costs":
        if eligible_cost_basis_minor is None:
            return BenefitEstimate()
        raw = eligible_cost_basis_minor * int(node["rate_bps"]) // 10_000
        cap = node.get("cap_minor")
        amount = min(raw, cap) if cap is not None else raw
        return BenefitEstimate(loan_min_minor=amount, loan_max_minor=amount)

    if kind == "per_unit":
        unit_value = facts.get(node["unit_field"])
        if unit_value is None:
            return BenefitEstimate()
        raw = int(unit_value) * int(node["amount_per_unit_minor"])
        cap = node.get("cap_minor")
        amount = min(raw, cap) if cap is not None else raw
        return BenefitEstimate(grant_min_minor=amount, grant_max_minor=amount)

    if kind == "loan_fixed":
        amount = int(node["amount_minor"])
        return BenefitEstimate(loan_min_minor=amount, loan_max_minor=amount)

    if kind == "loan_tiered":
        value = facts.get(node["tier_field"])
        if value is None:
            return BenefitEstimate()
        matched = _lookup_tier(value, node["tiers"])
        if matched is None:
            return BenefitEstimate()
        amount = int(matched["amount_minor"])
        return BenefitEstimate(loan_min_minor=amount, loan_max_minor=amount)

    if kind == "combined":
        grant_min = grant_max = loan_min = loan_max = 0
        any_grant = any_loan = False
        for component in node["components"]:
            part = _calculate_benefit(component, facts, eligible_cost_basis_minor)
            if part.grant_min_minor is not None:
                grant_min += part.grant_min_minor
                grant_max += part.grant_max_minor or part.grant_min_minor
                any_grant = True
            if part.loan_min_minor is not None:
                loan_min += part.loan_min_minor
                loan_max += part.loan_max_minor or part.loan_min_minor
                any_loan = True
        return BenefitEstimate(
            grant_min_minor=grant_min if any_grant else None,
            grant_max_minor=grant_max if any_grant else None,
            loan_min_minor=loan_min if any_loan else None,
            loan_max_minor=loan_max if any_loan else None,
        )

    raise FundingMatchingError("funding_rule_unsupported", f"Unbekannter Foerderbetrag-Typ: {kind}")


def match_program(
    program: ProgramMatchInput,
    facts: EligibilityFacts,
    *,
    project_type: str,
    country_code: str,
    region_code: str | None,
    eligible_cost_basis_minor: int | None,
    as_of: date,
) -> ProgramMatchResult | None:
    """Wertet EIN Programm gegen das Foerderprofil aus. Gibt `None` zurueck,
    wenn der Land-/Region-/Typ-/Gueltigkeits-Vorfilter (Plan 6.1) das Programm
    ausschliesst - dann taucht es im Ergebnis gar nicht erst auf, statt als
    `ineligible` zu erscheinen."""
    if not _region_matches(program, country_code, region_code):
        return None
    if not _prefilter_ok(program, facts, project_type):
        return None

    if program.normalization_status in ("discovery_only", "retired"):
        return ProgramMatchResult(
            program_version_id=program.program_version_id,
            status="unknown",
            benefit=BenefitEstimate(),
            criteria_met=(),
            criteria_violated=(),
            criteria_missing=(),
            criteria_manual=(),
            unknown_reason="normalization_incomplete",
        )
    if as_of > program.review_due_at:
        return ProgramMatchResult(
            program_version_id=program.program_version_id,
            status="unknown",
            benefit=BenefitEstimate(),
            criteria_met=(),
            criteria_violated=(),
            criteria_missing=(),
            criteria_manual=(),
            unknown_reason="funding_data_stale",
        )

    try:
        root_outcome, leaves = _evaluate_node(program.criteria_json, facts)
        benefit = (
            _calculate_benefit(program.benefit_json, facts, eligible_cost_basis_minor)
            if root_outcome in ("met", "missing", "manual")
            else BenefitEstimate()
        )
    except FundingMatchingError:
        return ProgramMatchResult(
            program_version_id=program.program_version_id,
            status="unknown",
            benefit=BenefitEstimate(),
            criteria_met=(),
            criteria_violated=(),
            criteria_missing=(),
            criteria_manual=(),
            unknown_reason="funding_rule_unsupported",
        )

    status = _status_from_outcome(root_outcome)
    return ProgramMatchResult(
        program_version_id=program.program_version_id,
        status=status,
        benefit=benefit,
        criteria_met=tuple(c for c in leaves if c.outcome == "met"),
        criteria_violated=tuple(c for c in leaves if c.outcome == "violated"),
        criteria_missing=tuple(c for c in leaves if c.outcome == "missing"),
        criteria_manual=tuple(c for c in leaves if c.outcome == "manual"),
    )


class CombinationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    program_version_ids: tuple[str, ...]
    status: CombinationStatus
    combined_cap_minor: int | None = None


def _pair_combination(a: ProgramMatchInput, b: ProgramMatchInput) -> CombinationStatus:
    rules_a = a.combination_rules_json or {}
    rules_b = b.combination_rules_json or {}

    for rules, other in ((rules_a, b), (rules_b, a)):
        excluded = rules.get("excluded_program_keys")
        if excluded and other.program_semantic_key in excluded:
            return "incompatible"
        if rules.get("relation") == "not_with":
            return "incompatible"

    if rules_a.get("manual_check") or rules_b.get("manual_check"):
        return "manual_check"

    allowed_a = rules_a.get("allowed_program_keys")
    allowed_b = rules_b.get("allowed_program_keys")
    if allowed_a is not None and b.program_semantic_key not in allowed_a:
        return "unknown"
    if allowed_b is not None and a.program_semantic_key not in allowed_b:
        return "unknown"
    if allowed_a is not None or allowed_b is not None:
        return "compatible"

    return "unknown"


def evaluate_combinations(programs: list[ProgramMatchInput]) -> list[CombinationResult]:
    """Plan 6.4: bewertet hoechstens die zehn bestbewerteten (Aufrufreihenfolge
    = bereits vorsortiert vom Aufrufer) Programme und hoechstens 50
    Paarkombinationen, damit die Engine nicht unbeschraenkt die Potenzmenge
    durchsucht. Nur explizit belegte Regeln liefern `compatible`; alles andere
    bleibt `unknown`/`manual_check`, nie optimistisch summiert."""
    ranked = programs[:_MAX_RANKED_PROGRAMS]
    results: list[CombinationResult] = []
    checked = 0
    for i in range(len(ranked)):
        for j in range(i + 1, len(ranked)):
            if checked >= _MAX_COMBINATIONS:
                return results
            status = _pair_combination(ranked[i], ranked[j])
            pair_ids = (ranked[i].program_version_id, ranked[j].program_version_id)
            results.append(CombinationResult(program_version_ids=pair_ids, status=status))
            checked += 1
    return results
