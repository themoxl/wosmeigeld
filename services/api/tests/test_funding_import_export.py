"""Paket-/Dateiimport und Export des Foerderkatalogs (Import-Plan S3/S4).

Die tragenden Zusagen des Moduls:

* Ein Import aktualisiert, ohne den alten Stand zu zerstoeren -- ein Vorhaben,
  das eine Foerderung als Finanzierungsquelle uebernommen hat, rechnet nach dem
  Update unveraendert weiter.
* Ein Programm mit unbekanntem Regelknoten wird nicht importiert, sondern
  `invalid` -- sonst taeuschte die Match-Engine spaeter "keine Angabe" vor.
* Der kuratierte Katalog bleibt unantastbar, und Haushalte sehen einander nicht.
"""

import copy
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _headers(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-20")
    household = client.post(
        "/v1/households", json={"name": "Import-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def household_headers() -> dict[str, str]:
    return _headers("funding-import")


def _package(**overrides: Any) -> dict[str, Any]:
    """Ein minimales, gueltiges Paket mit eindeutigem Schluessel je Testlauf."""
    key = f"test_programm_{uuid.uuid4().hex[:12]}"
    package: dict[str, Any] = {
        "format_version": "1.0",
        "id": "testpaket",
        "name": "Testpaket",
        "description": "Nur für Tests",
        "icon": "🧪",
        "publisher": "Testherausgeber",
        "package_version": "2026-08-08",
        "researched_on": "2026-08-08",
        "programs": [
            {
                "semantic_key": key,
                "provider_key": "testbank",
                "provider_name": "Testbank",
                "program_code": "T1",
                "title": "Testförderung",
                "country_code": "DE",
                "scope": "national",
                "region_codes": [],
                "official_url": "https://example.test/programm",
                "source_url": "https://example.test/programm",
                "source_version": "test-2026-08-08",
                "valid_from": "2026-01-01",
                "review_due_on": "2099-01-01",
                "benefit_type": "loan",
                "applicant_types": ["private_individual"],
                "project_types": ["home_build"],
                "criteria_json": {
                    "compare": {
                        "field": "self_occupied",
                        "op": "eq",
                        "value": True,
                        "label": "Selbstnutzung",
                    }
                },
                "benefit_json": {"type": "loan_fixed", "amount_minor": 5_000_000},
                "loan_terms_json": {
                    "max_term_months": 240,
                    "max_repayment_free_months": 24,
                    "nominal_rate_bps_annual": 250,
                },
                "combination_rules_json": {},
                "manual_checks_json": [],
                "normalization_status": "rules_complete",
            }
        ],
    }
    package.update(overrides)
    return package


def _create_project(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Hausbau Import", "project_type": "home_build"}
    body.update(overrides)
    response = client.post("/v1/projects", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_builtin_packages_are_offered_with_install_state(
    household_headers: dict[str, str],
) -> None:
    packages = client.get("/v1/funding/packages", headers=household_headers)
    assert packages.status_code == 200, packages.text
    by_id = {p["id"]: p for p in packages.json()}
    assert "kfw_sanierung" in by_id
    assert "kfw_jung_kauft_alt" in by_id
    assert "bayernlabo_eigenwohnraum" in by_id
    assert by_id["kfw_sanierung"]["program_count"] > 0
    assert by_id["kfw_jung_kauft_alt"]["program_count"] == 1
    assert by_id["bayernlabo_eigenwohnraum"]["program_count"] == 2
    # Der alte kuratierte BayernLabo-Seed ist noch keine Paketinstallation.
    assert by_id["bayernlabo_eigenwohnraum"]["installed_count"] == 0
    # Noch nichts eingespielt.
    assert by_id["kfw_sanierung"]["installed_count"] == 0

    payload = client.get("/v1/funding/packages/kfw_sanierung", headers=household_headers)
    assert payload.status_code == 200, payload.text
    assert payload.json()["publisher"] == "KfW"


def test_legacy_bayernlabo_seed_immediately_exposes_complete_bundled_terms(
    household_headers: dict[str, str],
) -> None:
    programs = client.get("/v1/funding/programs", headers=household_headers).json()
    summary = next(
        item for item in programs if item["semantic_key"] == "bayernlabo_eigenwohnraumfoerderung"
    )
    detail = client.get(f"/v1/funding/programs/{summary['id']}", headers=household_headers).json()
    version = detail["current_version"]
    assert version["benefit_type"] == "loan_with_grant"
    loan, grant = version["benefit_json"]["components"]
    assert loan["type"] == "loan_percent_of_costs"
    assert loan["rate_bps"] == 3333
    assert grant["amount_per_unit_minor"] == 500_000
    assert grant["unit_field"] == "children_count"
    assert version["loan_terms_json"]["initial_repayment_rate_bps"] == 200
    assert version["loan_terms_json"]["rate_options"] == [
        {
            "id": "fixed_10_years",
            "label": "10 Jahre Zinsbindung",
            "fixed_rate_months": 120,
            "nominal_rate_bps_annual": 265,
            "effective_rate_bps_annual": 272,
        },
        {
            "id": "fixed_15_years",
            "label": "15 Jahre Zinsbindung",
            "fixed_rate_months": 180,
            "nominal_rate_bps_annual": 295,
            "effective_rate_bps_annual": 303,
        },
    ]
    equity = str(version["criteria_json"])
    assert "20 %" in equity
    assert "15 %" in equity
    assert "Einzelfall" in equity


def test_editing_global_bayernlabo_creates_household_head_without_leak(
    household_headers: dict[str, str],
) -> None:
    programs = client.get("/v1/funding/programs", headers=household_headers).json()
    global_program = next(
        item for item in programs if item["semantic_key"] == "bayernlabo_eigenwohnraumfoerderung"
    )
    detail = client.get(
        f"/v1/funding/programs/{global_program['id']}", headers=household_headers
    ).json()
    version = detail["current_version"]
    terms = copy.deepcopy(version["loan_terms_json"])
    terms["rate_options"][0]["nominal_rate_bps_annual"] = 266
    response = client.put(
        f"/v1/funding/programs/{global_program['id']}/terms",
        json={
            "source_version": "household-bayern-2026-08-09",
            "review_due_on": "2099-01-01",
            "source_url": version["source_url"],
            "benefit_json": version["benefit_json"],
            "loan_terms_json": terms,
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text
    own = response.json()
    assert own["id"] != global_program["id"]
    assert own["is_own"] is True
    assert (
        own["current_version"]["loan_terms_json"]["rate_options"][0]["nominal_rate_bps_annual"]
        == 266
    )

    foreign = _headers("funding-bayern-isolation")
    foreign_programs = client.get("/v1/funding/programs", headers=foreign).json()
    foreign_summary = next(
        item
        for item in foreign_programs
        if item["semantic_key"] == "bayernlabo_eigenwohnraumfoerderung"
    )
    assert foreign_summary["id"] == global_program["id"]
    foreign_detail = client.get(
        f"/v1/funding/programs/{foreign_summary['id']}", headers=foreign
    ).json()
    assert (
        foreign_detail["current_version"]["loan_terms_json"]["rate_options"][0][
            "nominal_rate_bps_annual"
        ]
        == 265
    )


def test_legacy_installed_kfw_308_is_offered_and_applied_as_variant_update(
    household_headers: dict[str, str],
) -> None:
    """Reproduziert den echten Laufweg eines schon installierten Altstands.

    Der alte flache Eintrag muss als Paketupdate erkannt werden. Nach dessen
    Anwendung liefert genau der Programmpfad, den der Projekt-Dialog abfragt,
    alle vier Varianten samt veröffentlichten Zinsen.
    """
    current = client.get(
        "/v1/funding/packages/kfw_jung_kauft_alt", headers=household_headers
    ).json()
    legacy = copy.deepcopy(current)
    legacy["package_version"] = "2026-08-08"
    legacy_program = legacy["programs"][0]
    legacy_program["source_version"] = "kfw-308-2026-08-03"
    legacy_program["loan_terms_json"] = {
        "max_term_months": 420,
        "fixed_rate_months": 120,
        "max_repayment_free_months": 60,
        "note": "Altstand ohne veröffentlichte Konditionsvarianten",
    }
    first = client.post("/v1/funding/import", json={"package": legacy}, headers=household_headers)
    assert first.status_code == 200, first.text

    # Das ist der reale Pfad des Projekt-Dialogs: Programmliste -> Detail.
    # Bereits vor dem formalen Paketupdate darf er nicht mehr in die alte
    # manuelle Zinseingabe zurückfallen.
    legacy_programs = client.get(
        "/v1/funding/programs?project_type=property_purchase", headers=household_headers
    ).json()
    legacy_summary = next(
        item for item in legacy_programs if item["semantic_key"] == "kfw_308_jung_kauft_alt"
    )
    legacy_detail = client.get(
        f"/v1/funding/programs/{legacy_summary['id']}", headers=household_headers
    ).json()
    visible_terms = legacy_detail["current_version"]["loan_terms_json"]
    assert [variant["nominal_rate_bps_annual"] for variant in visible_terms["variants"]] == [
        1,
        25,
        53,
        69,
    ]

    project = _create_project(
        household_headers, name="Jung-kauft-Alt-Laufweg", project_type="property_purchase"
    )
    scenario = client.get(
        f"/v1/projects/{project['id']}/scenarios", headers=household_headers
    ).json()[0]
    client.put(
        f"/v1/projects/{project['id']}/eligibility-profile",
        json={
            "facts": {
                "self_occupied": True,
                "children_count": 1,
                "household_income_year_minor": 8_000_000,
                "existing_homeowner": False,
            },
            "manual_confirmations": [],
        },
        headers=household_headers,
    )
    run = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-check",
        headers=household_headers,
    ).json()
    match = next(
        item for item in run["matches"] if item["program_semantic_key"] == "kfw_308_jung_kauft_alt"
    )
    missing_selection = client.post(
        f"/v1/projects/{project['id']}/funding-matches/{match['id']}/adopt",
        json={"scenario_id": scenario["id"]},
        headers=household_headers,
    )
    assert missing_selection.status_code == 422
    assert missing_selection.json()["detail"] == "loan_term_variant_required"

    adopted = client.post(
        f"/v1/projects/{project['id']}/funding-matches/{match['id']}/adopt",
        json={
            "scenario_id": scenario["id"],
            "loan_term_variant_id": "annuity_7_10",
            "term_months": 120,
            "repayment_free_months": 24,
        },
        headers=household_headers,
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["debt_plan_id"] is not None

    package_state = client.get("/v1/funding/packages", headers=household_headers).json()
    kfw_state = next(item for item in package_state if item["id"] == "kfw_jung_kauft_alt")
    assert kfw_state["installed_count"] == 1
    assert kfw_state["outdated_count"] == 1

    preview = client.post(
        "/v1/funding/import/preview", json={"package": current}, headers=household_headers
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["items"][0]["status"] == "updated"
    assert preview.json()["items"][0]["recommended"] is True

    update = client.post("/v1/funding/import", json={"package": current}, headers=household_headers)
    assert update.status_code == 200, update.text
    assert update.json()["versions_created"] == 1

    programs = client.get(
        "/v1/funding/programs?project_type=property_purchase", headers=household_headers
    ).json()
    program = next(item for item in programs if item["semantic_key"] == "kfw_308_jung_kauft_alt")
    detail = client.get(f"/v1/funding/programs/{program['id']}", headers=household_headers).json()
    terms = detail["current_version"]["loan_terms_json"]
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

    jung_kauft_alt = client.get(
        "/v1/funding/packages/kfw_jung_kauft_alt", headers=household_headers
    )
    assert jung_kauft_alt.status_code == 200, jung_kauft_alt.text
    program_308 = jung_kauft_alt.json()["programs"][0]
    assert program_308["program_code"] == "308"
    assert program_308["benefit_json"]["tiers"][-1]["amount_minor"] == 18_000_000

    bayernlabo = client.get(
        "/v1/funding/packages/bayernlabo_eigenwohnraum", headers=household_headers
    )
    assert bayernlabo.status_code == 200, bayernlabo.text
    assert len(bayernlabo.json()["programs"]) == 2


def test_builtin_bayernlabo_package_recommends_curated_overlay(
    household_headers: dict[str, str],
) -> None:
    package = client.get(
        "/v1/funding/packages/bayernlabo_eigenwohnraum", headers=household_headers
    ).json()
    preview = client.post(
        "/v1/funding/import/preview",
        json={"package": package},
        headers=household_headers,
    )
    assert preview.status_code == 200, preview.text
    by_key = {item["semantic_key"]: item for item in preview.json()["items"]}
    overlay = by_key["bayernlabo_eigenwohnraumfoerderung"]
    assert overlay["status"] == "conflict"
    assert overlay["recommended"] is True
    assert by_key["bayern_wohnraumanpassung_behinderung"]["status"] == "new"

    installed = client.post(
        "/v1/funding/import",
        json={"package": package},
        headers=household_headers,
    )
    assert installed.status_code == 200, installed.text
    assert installed.json()["programs_created"] == 2
    assert installed.json()["versions_created"] == 2

    programs = client.get(
        "/v1/funding/programs",
        params={"region_code": "DE-BY"},
        headers=household_headers,
    ).json()
    installed_by_key = {program["semantic_key"]: program for program in programs}
    assert (
        installed_by_key["bayernlabo_eigenwohnraumfoerderung"]["title"]
        == "Bayerisches Zinsverbilligungsprogramm für Eigenwohnraum"
    )
    assert installed_by_key["bayernlabo_eigenwohnraumfoerderung"]["is_own"] is True
    package_state = client.get("/v1/funding/packages", headers=household_headers).json()
    bayernlabo_state = next(
        item for item in package_state if item["id"] == "bayernlabo_eigenwohnraum"
    )
    assert bayernlabo_state["installed_count"] == 2


def test_preview_never_writes(household_headers: dict[str, str]) -> None:
    package = _package()
    before = client.get("/v1/funding/programs", headers=household_headers).json()

    preview = client.post(
        "/v1/funding/import/preview", json={"package": package}, headers=household_headers
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["new_count"] == 1
    assert body["items"][0]["status"] == "new"
    assert body["items"][0]["recommended"] is True

    after = client.get("/v1/funding/programs", headers=household_headers).json()
    assert len(after) == len(before)


def test_second_import_of_the_same_package_reports_unchanged(
    household_headers: dict[str, str],
) -> None:
    package = _package()
    first = client.post(
        "/v1/funding/import", json={"package": package}, headers=household_headers
    ).json()
    assert first["programs_created"] == 1
    assert first["versions_created"] == 1

    second_preview = client.post(
        "/v1/funding/import/preview", json={"package": package}, headers=household_headers
    ).json()
    assert second_preview["unchanged_count"] == 1
    assert second_preview["items"][0]["recommended"] is False

    second = client.post(
        "/v1/funding/import", json={"package": package}, headers=household_headers
    ).json()
    assert second["versions_created"] == 0
    assert second["unchanged"] == 1


def test_manual_terms_update_creates_a_new_current_version(
    household_headers: dict[str, str],
) -> None:
    package = _package()
    imported = client.post(
        "/v1/funding/import", json={"package": package}, headers=household_headers
    )
    assert imported.status_code == 200, imported.text

    programs = client.get("/v1/funding/programs", headers=household_headers).json()
    program = next(
        p for p in programs if p["semantic_key"] == package["programs"][0]["semantic_key"]
    )
    old_version_id = program["current_version_id"]

    update = client.put(
        f"/v1/funding/programs/{program['id']}/terms",
        json={
            "source_version": "manuell-2026-08-09",
            "review_due_on": "2099-06-30",
            "source_url": "https://example.test/programm/neue-konditionen",
            "benefit_json": {"type": "loan_fixed", "amount_minor": 7_000_000},
            "loan_terms_json": {
                "max_term_months": 360,
                "fixed_rate_months": 120,
                "max_repayment_free_months": 36,
                "nominal_rate_bps_annual": 275,
            },
        },
        headers=household_headers,
    )
    assert update.status_code == 200, update.text
    body = update.json()
    assert body["current_version_id"] != old_version_id
    assert body["source_version"] == "manuell-2026-08-09"
    assert body["current_version"]["benefit_json"]["amount_minor"] == 7_000_000
    assert body["current_version"]["loan_terms_json"]["nominal_rate_bps_annual"] == 275
    assert body["current_version"]["loan_terms_json"]["fixed_rate_months"] == 120


def test_manual_terms_update_rejects_incomplete_subsidized_rate(
    household_headers: dict[str, str],
) -> None:
    package = _package()
    client.post("/v1/funding/import", json={"package": package}, headers=household_headers)
    programs = client.get("/v1/funding/programs", headers=household_headers).json()
    program = next(
        p for p in programs if p["semantic_key"] == package["programs"][0]["semantic_key"]
    )

    update = client.put(
        f"/v1/funding/programs/{program['id']}/terms",
        json={
            "source_version": "ungueltig",
            "review_due_on": "2099-06-30",
            "source_url": "https://example.test/programm",
            "benefit_json": {"type": "loan_fixed", "amount_minor": 5_000_000},
            "loan_terms_json": {"subsidized_rate_bps_annual": 100},
        },
        headers=household_headers,
    )
    assert update.status_code == 422, update.text
    assert "gemeinsam" in str(update.json()["detail"]["problems"])


def test_update_adds_a_version_and_leaves_the_adopted_amount_untouched(
    household_headers: dict[str, str],
) -> None:
    """Der Kern der Versionierung: ein neuer Stand aendert nichts rueckwirkend."""
    package = _package()
    client.post("/v1/funding/import", json={"package": package}, headers=household_headers)

    project = _create_project(household_headers)
    scenario = client.get(
        f"/v1/projects/{project['id']}/scenarios", headers=household_headers
    ).json()[0]
    client.put(
        f"/v1/projects/{project['id']}/eligibility-profile",
        json={"facts": {"self_occupied": True}, "manual_confirmations": []},
        headers=household_headers,
    )
    run = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-check",
        headers=household_headers,
    ).json()
    match = next(
        m
        for m in run["matches"]
        if m["program_semantic_key"] == package["programs"][0]["semantic_key"]
    )
    assert match["estimated_loan_max_minor"] == 5_000_000

    adopt = client.post(
        f"/v1/projects/{project['id']}/funding-matches/{match['id']}/adopt",
        json={"scenario_id": scenario["id"]},
        headers=household_headers,
    ).json()
    # Zinssatz liegt vor -> der Tilgungsplan entsteht gleich mit.
    assert adopt["debt_plan_id"] is not None
    assert adopt["warnings"] == []

    updated = copy.deepcopy(package)
    updated["programs"][0]["benefit_json"]["amount_minor"] = 6_000_000
    updated["programs"][0]["source_version"] = "test-2026-09-01"

    preview = client.post(
        "/v1/funding/import/preview", json={"package": updated}, headers=household_headers
    ).json()
    item = preview["items"][0]
    assert item["status"] == "updated"
    assert "Förderbetrag" in item["changed_fields"]
    assert item["adopted_by_projects"] == 1
    assert item["existing_source_version"] == "test-2026-08-08"

    result = client.post(
        "/v1/funding/import", json={"package": updated}, headers=household_headers
    ).json()
    assert result["programs_created"] == 0
    assert result["versions_created"] == 1

    sources = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers=household_headers,
    ).json()
    adopted = next(s for s in sources if s["id"] == adopt["financing_source_id"])
    # Unveraendert: die uebernommene Quelle haengt an der alten Version.
    assert adopted["planned_amount_minor"] == 5_000_000


def test_program_with_unknown_rule_node_is_invalid_and_not_imported(
    household_headers: dict[str, str],
) -> None:
    package = _package()
    package["programs"][0]["benefit_json"] = {"type": "tilgungszuschuss", "rate_bps": 2000}

    preview = client.post(
        "/v1/funding/import/preview", json={"package": package}, headers=household_headers
    ).json()
    item = preview["items"][0]
    assert item["status"] == "invalid"
    assert item["recommended"] is False
    assert any("benefit_json" in problem for problem in item["problems"])

    result = client.post(
        "/v1/funding/import", json={"package": package}, headers=household_headers
    ).json()
    assert result["versions_created"] == 0
    assert result["invalid"] == 1


def test_expired_review_date_is_invalid(household_headers: dict[str, str]) -> None:
    package = _package()
    package["programs"][0]["review_due_on"] = "2020-01-01"

    preview = client.post(
        "/v1/funding/import/preview", json={"package": package}, headers=household_headers
    ).json()
    assert preview["items"][0]["status"] == "invalid"
    assert any("review_due_on" in p for p in preview["items"][0]["problems"])


def test_deselected_programs_are_skipped(household_headers: dict[str, str]) -> None:
    package = _package()
    second = copy.deepcopy(package["programs"][0])
    second["semantic_key"] = f"test_programm_{uuid.uuid4().hex[:12]}"
    second["title"] = "Zweite Testförderung"
    package["programs"].append(second)

    result = client.post(
        "/v1/funding/import",
        json={"package": package, "skip_indices": [1]},
        headers=household_headers,
    ).json()
    assert result["versions_created"] == 1
    assert result["skipped_deselected"] == 1


def test_curated_program_key_is_flagged_as_conflict(household_headers: dict[str, str]) -> None:
    package = _package()
    package["programs"][0]["semantic_key"] = "kfw_124_wohneigentumsprogramm"

    preview = client.post(
        "/v1/funding/import/preview", json={"package": package}, headers=household_headers
    ).json()
    item = preview["items"][0]
    assert item["status"] == "conflict"
    assert item["recommended"] is False


def test_imported_program_is_invisible_to_another_household(
    household_headers: dict[str, str],
) -> None:
    package = _package()
    client.post("/v1/funding/import", json={"package": package}, headers=household_headers)
    key = package["programs"][0]["semantic_key"]

    own = client.get("/v1/funding/programs", headers=household_headers).json()
    own_entry = next(p for p in own if p["semantic_key"] == key)
    assert own_entry["is_own"] is True

    foreign_headers = _headers("funding-import-foreign")
    foreign = client.get("/v1/funding/programs", headers=foreign_headers).json()
    assert all(p["semantic_key"] != key for p in foreign)
    # Kuratierte Programme sieht der fremde Haushalt sehr wohl.
    assert any(p["semantic_key"] == "kfw_124_wohneigentumsprogramm" for p in foreign)

    detail = client.get(f"/v1/funding/programs/{own_entry['id']}", headers=foreign_headers)
    assert detail.status_code == 404


def test_deactivate_removes_own_program_from_the_check_but_keeps_history(
    household_headers: dict[str, str],
) -> None:
    package = _package()
    client.post("/v1/funding/import", json={"package": package}, headers=household_headers)
    key = package["programs"][0]["semantic_key"]
    programs = client.get("/v1/funding/programs", headers=household_headers).json()
    own = next(p for p in programs if p["semantic_key"] == key)

    response = client.post(
        f"/v1/funding/programs/{own['id']}/deactivate", headers=household_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False

    after = client.get("/v1/funding/programs", headers=household_headers).json()
    assert all(p["semantic_key"] != key for p in after)
    with_inactive = client.get(
        "/v1/funding/programs", params={"include_inactive": True}, headers=household_headers
    ).json()
    assert any(p["semantic_key"] == key for p in with_inactive)


def test_curated_programs_cannot_be_deactivated(household_headers: dict[str, str]) -> None:
    programs = client.get("/v1/funding/programs", headers=household_headers).json()
    curated = next(p for p in programs if p["semantic_key"] == "kfw_124_wohneigentumsprogramm")
    assert curated["is_own"] is False

    response = client.post(
        f"/v1/funding/programs/{curated['id']}/deactivate", headers=household_headers
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "curated_program_not_editable"


def test_export_round_trips_into_an_importable_package(
    household_headers: dict[str, str],
) -> None:
    package = _package()
    client.post("/v1/funding/import", json={"package": package}, headers=household_headers)

    export = client.post(
        "/v1/funding/export", json={"package_name": "Mein Export"}, headers=household_headers
    )
    assert export.status_code == 200, export.text
    exported = export.json()
    assert exported["name"] == "Mein Export"
    assert len(exported["programs"]) == 1

    # In einen anderen Haushalt eingespielt, entsteht dasselbe Programm neu.
    other = _headers("funding-export-target")
    result = client.post("/v1/funding/import", json={"package": exported}, headers=other).json()
    assert result["programs_created"] == 1

    # Und ist dort inhaltlich identisch -- ein erneuter Import meldet `unchanged`.
    again = client.post(
        "/v1/funding/import/preview", json={"package": exported}, headers=other
    ).json()
    assert again["unchanged_count"] == 1


def test_import_appears_in_catalog_status(household_headers: dict[str, str]) -> None:
    package = _package()
    client.post("/v1/funding/import", json={"package": package}, headers=household_headers)

    status = client.get("/v1/funding/catalog-status", headers=household_headers).json()
    by_provider = {p["provider_key"]: p for p in status["providers"]}
    assert "testbank" in by_provider
    assert by_provider["testbank"]["last_run_status"] == "succeeded"
    assert by_provider["testbank"]["programs_new"] == 1
