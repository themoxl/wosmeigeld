"""Struktur und Importvalidierung mehrerer Darlehenskonditionen."""

from copy import deepcopy

from app.data.funding_packages.kfw_jung_kauft_alt import PACKAGE
from app.services.funding_import_export_service import _validate_loan_terms


def _terms() -> dict:
    return deepcopy(PACKAGE["programs"][0]["loan_terms_json"])


def test_shipped_kfw_308_variants_have_exact_published_rates() -> None:
    terms = _terms()
    assert terms["rate_as_of"] == "2026-08-09"
    assert [variant["id"] for variant in terms["variants"]] == [
        "annuity_7_10",
        "annuity_11_25",
        "annuity_26_35",
        "bullet_7_10",
    ]
    assert [variant["nominal_rate_bps_annual"] for variant in terms["variants"]] == [
        1,
        25,
        53,
        69,
    ]
    assert [variant["effective_rate_bps_annual"] for variant in terms["variants"]] == [
        1,
        25,
        53,
        69,
    ]
    assert _validate_loan_terms(terms) == []


def test_duplicate_variant_ids_are_rejected() -> None:
    terms = _terms()
    terms["variants"][1]["id"] = terms["variants"][0]["id"]
    problems = _validate_loan_terms(terms)
    assert any("id ist doppelt" in problem for problem in problems)


def test_inverted_term_and_grace_ranges_are_rejected() -> None:
    terms = _terms()
    terms["variants"][0]["min_term_months"] = 121
    terms["variants"][0]["min_repayment_free_months"] = 25
    problems = _validate_loan_terms(terms)
    assert any("Mindestlaufzeit" in problem for problem in problems)
    assert any("Mindest-Anlaufzeit" in problem for problem in problems)


def test_bullet_variant_cannot_claim_a_grace_period() -> None:
    terms = _terms()
    terms["variants"][3]["max_repayment_free_months"] = 12
    problems = _validate_loan_terms(terms)
    assert any("endfällige Varianten" in problem for problem in problems)
