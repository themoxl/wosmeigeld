"""Foerderkatalog-API: Katalog, Foerderprofil, Matchlauf, Kombinationspruefung
und Uebernahme als Finanzierungsquelle (Plan 7.5), gegen die kuratierten
Programme aus `0001_schema.sql` (P3)."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"funding-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-10")
    household = client.post(
        "/v1/households", json={"name": "Foerder-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_project(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Hausbau Musterstrasse",
        "project_type": "home_build",
        "country_code": "DE",
    }
    body.update(overrides)
    response = client.post("/v1/projects", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _baseline_scenario(headers: dict[str, str], project_id: str) -> dict[str, Any]:
    scenarios = client.get(f"/v1/projects/{project_id}/scenarios", headers=headers).json()
    return dict(scenarios[0])


def test_catalog_lists_curated_programs_filtered_by_region(
    household_headers: dict[str, str],
) -> None:
    # Der Katalog ist haushaltsbezogen: kuratierte Programme (household_id is null)
    # sind fuer jeden sichtbar, eigene nur fuer den eigenen Haushalt. Deshalb
    # braucht auch der lesende Zugriff den Haushaltskopf.
    headers = household_headers

    nationwide = client.get(
        "/v1/funding/programs", params={"country_code": "DE"}, headers=headers
    ).json()
    keys = {p["semantic_key"] for p in nationwide}
    assert "kfw_124_wohneigentumsprogramm" in keys
    assert "kfw_300_wohneigentum_familien" in keys
    # Ohne region_code wird NICHT nach Region gefiltert: die Katalogseite
    # schickt keine Region mit, und ein Register, das Landesprogramme
    # verschweigt, laesst den Nutzer glauben, es gaebe sie nicht. Das
    # Eingrenzen auf das passende Bundesland ist Sache des Foerderchecks.
    assert "bayernlabo_eigenwohnraumfoerderung" in keys
    assert "lbank_z15_wohneigentum_bw" in keys

    bavaria = client.get(
        "/v1/funding/programs",
        params={"country_code": "DE", "region_code": "DE-BY"},
        headers=headers,
    ).json()
    bavaria_keys = {p["semantic_key"] for p in bavaria}
    assert "bayernlabo_eigenwohnraumfoerderung" in bavaria_keys
    assert "lbank_z15_wohneigentum_bw" not in bavaria_keys


def test_catalog_status_lists_provider(household_headers: dict[str, str]) -> None:
    status = client.get("/v1/funding/catalog-status", headers=household_headers).json()
    provider_keys = {p["provider_key"] for p in status["providers"]}
    assert "kfw" in provider_keys


def test_eligibility_profile_roundtrip(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    assert (
        client.get(
            f"/v1/projects/{project['id']}/eligibility-profile", headers=household_headers
        ).json()
        is None
    )

    saved = client.put(
        f"/v1/projects/{project['id']}/eligibility-profile",
        json={
            "facts": {"self_occupied": True, "household_income_year_minor": 8_000_000},
            "manual_confirmations": [],
        },
        headers=household_headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["facts"]["self_occupied"] is True

    fetched = client.get(
        f"/v1/projects/{project['id']}/eligibility-profile", headers=household_headers
    ).json()
    assert fetched["facts"]["household_income_year_minor"] == 8_000_000


def test_funding_check_finds_eligible_national_loan_program(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    client.put(
        f"/v1/projects/{project['id']}/eligibility-profile",
        json={"facts": {"self_occupied": True}, "manual_confirmations": []},
        headers=household_headers,
    )

    run = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-check",
        headers=household_headers,
    )
    assert run.status_code == 200, run.text
    body = run.json()
    by_key = {m["program_semantic_key"]: m for m in body["matches"]}
    assert by_key["kfw_124_wohneigentumsprogramm"]["status"] == "eligible"
    assert by_key["kfw_124_wohneigentumsprogramm"]["estimated_loan_max_minor"] == 100_000_00
    # Bayerisches/BW-Programm taucht fuer ein Projekt ohne region_code gar
    # nicht erst auf (Vorfilter Plan 6.1), nicht als "ineligible".
    assert "bayernlabo_eigenwohnraumfoerderung" not in by_key

    # Idempotent: derselbe Matchlauf (gleicher Fingerprint) liefert denselben Run.
    again = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-check",
        headers=household_headers,
    )
    assert again.json()["id"] == body["id"]


def test_funding_check_requires_manual_confirmation_for_kfw_300(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    client.put(
        f"/v1/projects/{project['id']}/eligibility-profile",
        json={
            "facts": {
                "self_occupied": True,
                "children_count": 2,
                "household_income_year_minor": 9_500_000,
            },
            "manual_confirmations": [],
        },
        headers=household_headers,
    )

    run = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-check",
        headers=household_headers,
    ).json()
    by_key = {m["program_semantic_key"]: m for m in run["matches"]}
    kfw_300 = by_key["kfw_300_wohneigentum_familien"]
    # Einkommen/Kinder erfuellt, aber der Effizienzhaus-Nachweis ist ein
    # manual_check -> potentially_eligible, nicht eligible und nicht ineligible.
    assert kfw_300["status"] == "potentially_eligible"
    assert len(kfw_300["criteria_manual"]) == 1


def test_funding_matches_are_retrievable_after_check(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-check",
        headers=household_headers,
    )
    latest = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-matches",
        headers=household_headers,
    )
    assert latest.status_code == 200
    assert len(latest.json()["matches"]) > 0


def test_adopt_match_creates_financing_source(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
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
        m for m in run["matches"] if m["program_semantic_key"] == "kfw_124_wohneigentumsprogramm"
    )

    adopt = client.post(
        f"/v1/projects/{project['id']}/funding-matches/{match['id']}/adopt",
        json={"scenario_id": scenario["id"]},
        headers=household_headers,
    )
    assert adopt.status_code == 200, adopt.text
    source_id = adopt.json()["financing_source_id"]

    sources = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers=household_headers,
    ).json()
    adopted = next(s for s in sources if s["id"] == source_id)
    assert adopted["source_type"] == "subsidized_loan"
    assert adopted["planned_amount_minor"] == 100_000_00
    assert adopted["funding_program_version_id"] == match["program_version_id"]


def test_adopt_match_carries_loan_terms_without_inventing_an_interest_rate(
    household_headers: dict[str, str],
) -> None:
    """KfW 124 kennt Laufzeit und tilgungsfreie Zeit, aber keinen Zinssatz.

    Die Konditionen muessen an der Quelle haengen (damit der Tilgungsplan-Dialog
    sie vorbelegen kann), es darf aber kein Plan mit einem erfundenen Zins
    entstehen -- der saehe im Kostenverlauf wie eine Zusage aus.
    """
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
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
        m for m in run["matches"] if m["program_semantic_key"] == "kfw_124_wohneigentumsprogramm"
    )

    adopt = client.post(
        f"/v1/projects/{project['id']}/funding-matches/{match['id']}/adopt",
        json={"scenario_id": scenario["id"]},
        headers=household_headers,
    )
    assert adopt.status_code == 200, adopt.text
    body = adopt.json()
    assert body["debt_plan_id"] is None
    assert body["warnings"] == ["kein_zinssatz_im_katalog"]
    # Reines Darlehensprogramm: kein zweiter Zuschuss-Eintrag.
    assert body["grant_financing_source_id"] is None

    sources = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers=household_headers,
    ).json()
    assert len(sources) == 1
    terms = sources[0]["funding_loan_terms"]
    assert terms["max_term_months"] == 420
    assert terms["max_repayment_free_months"] == 60
    assert "nominal_rate_bps_annual" not in terms


def test_adopt_match_is_idempotent_per_request_id(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
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
        m for m in run["matches"] if m["program_semantic_key"] == "kfw_124_wohneigentumsprogramm"
    )
    request_id = str(uuid.uuid4())
    body = {"scenario_id": scenario["id"], "request_id": request_id}

    first = client.post(
        f"/v1/projects/{project['id']}/funding-matches/{match['id']}/adopt",
        json=body,
        headers=household_headers,
    ).json()
    second = client.post(
        f"/v1/projects/{project['id']}/funding-matches/{match['id']}/adopt",
        json=body,
        headers=household_headers,
    ).json()

    assert first["already_applied"] is False
    assert second["already_applied"] is True
    assert second["financing_source_id"] == first["financing_source_id"]

    sources = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers=household_headers,
    ).json()
    assert len(sources) == 1


def test_combination_evaluate_reports_unknown_not_compatible(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    client.put(
        f"/v1/projects/{project['id']}/eligibility-profile",
        json={
            "facts": {
                "self_occupied": True,
                "children_count": 2,
                "household_income_year_minor": 9_500_000,
            },
            "manual_confirmations": [],
        },
        headers=household_headers,
    )
    run = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-check",
        headers=household_headers,
    ).json()
    match_ids = [m["id"] for m in run["matches"][:2]]

    combos = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/funding-combinations/evaluate",
        json={"match_ids": match_ids},
        headers=household_headers,
    )
    assert combos.status_code == 200, combos.text
    for combo in combos.json()["combinations"]:
        assert combo["status"] != "compatible"


def test_eligibility_profile_is_isolated_per_household(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    client.put(
        f"/v1/projects/{project['id']}/eligibility-profile",
        json={"facts": {"self_occupied": True}, "manual_confirmations": []},
        headers=household_headers,
    )

    foreign_token = _signup(f"funding-foreign-{uuid.uuid4().hex}@finanzplaner.test", "correct-13")
    foreign_household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(foreign_token)
    ).json()
    foreign_headers = {**_auth_headers(foreign_token), "X-Household-Id": foreign_household["id"]}

    foreign_get = client.get(
        f"/v1/projects/{project['id']}/eligibility-profile", headers=foreign_headers
    )
    assert foreign_get.status_code == 404


def test_curated_program_terms_can_be_overlaid_by_household(
    household_headers: dict[str, str],
) -> None:
    """Der Zinssatz fehlt in jedem kuratierten Programm - Foerderbanken setzen
    ihn taeglich neu fest. Er muss deshalb nachtragbar sein, sonst entsteht bei
    der Uebernahme nie ein Tilgungsplan. Die Eingabe legt eine eigene Version
    ueber den mitgelieferten Stand, statt ihn zu veraendern."""
    programs = client.get(
        "/v1/funding/programs", params={"country_code": "DE"}, headers=household_headers
    ).json()
    curated = next(p for p in programs if p["semantic_key"] == "kfw_124_wohneigentumsprogramm")
    assert curated["is_own"] is False

    detail_before = client.get(
        f"/v1/funding/programs/{curated['id']}", headers=household_headers
    ).json()
    version_before = detail_before["current_version"]
    assert "nominal_rate_bps_annual" not in version_before["loan_terms_json"]

    response = client.put(
        f"/v1/funding/programs/{curated['id']}/terms",
        headers=household_headers,
        json={
            "source_version": "eigener-zinsstand-2026-08",
            "review_due_on": "2027-08-01",
            "source_url": version_before["source_url"],
            "benefit_json": version_before["benefit_json"],
            "loan_terms_json": {
                **version_before["loan_terms_json"],
                "nominal_rate_bps_annual": 312,
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_version"]["loan_terms_json"]["nominal_rate_bps_annual"] == 312

    # Der manuelle Stand bekommt einen haushaltseigenen Programmkopf. So kann
    # seine Version nie als vermeintlich neuester globaler Stand in einen
    # anderen Haushalt durchsickern.
    own = response.json()
    assert own["id"] != curated["id"]
    assert own["is_own"] is True
    again = client.get(f"/v1/funding/programs/{own['id']}", headers=household_headers).json()
    assert again["current_version"]["loan_terms_json"]["nominal_rate_bps_annual"] == 312
    unchanged_global = client.get(
        f"/v1/funding/programs/{curated['id']}", headers=household_headers
    ).json()
    assert "nominal_rate_bps_annual" not in unchanged_global["current_version"]["loan_terms_json"]
    assert again["current_version"]["source_version"] == "eigener-zinsstand-2026-08"


def test_curated_terms_edit_creates_a_household_shadow_program(
    household_headers: dict[str, str],
) -> None:
    """Konditionen an einem MITGELIEFERTEN Programm nachtragen.

    Der kuratierte Eintrag gehoert dem Code und bleibt unangetastet; die
    Eingabe erzeugt ein eigenes Programm mit demselben `semantic_key`, das den
    mitgelieferten Stand verdeckt. Wichtig ist beides: der Wert muss danach
    lesbar sein (sonst meldet der PUT 200 und nichts passiert), und ein FREMDER
    Haushalt darf davon nichts sehen.
    """
    programs = client.get(
        "/v1/funding/programs", params={"country_code": "DE"}, headers=household_headers
    ).json()
    curated = next(p for p in programs if p["semantic_key"] == "kfw_124_wohneigentumsprogramm")
    assert curated["is_own"] is False
    base_version = client.get(
        f"/v1/funding/programs/{curated['id']}", headers=household_headers
    ).json()["current_version"]

    saved = client.put(
        f"/v1/funding/programs/{curated['id']}/terms",
        headers=household_headers,
        json={
            "source_version": "eigener-zinsstand",
            "review_due_on": "2027-08-01",
            "source_url": base_version["source_url"],
            "benefit_json": base_version["benefit_json"],
            "loan_terms_json": {**base_version["loan_terms_json"], "nominal_rate_bps_annual": 321},
        },
    )
    assert saved.status_code == 200, saved.text
    shadow = saved.json()
    assert shadow["is_own"] is True
    assert shadow["id"] != curated["id"]
    assert shadow["current_version"]["loan_terms_json"]["nominal_rate_bps_annual"] == 321

    # Der kuratierte Eintrag selbst bleibt unveraendert.
    still_curated = client.get(
        f"/v1/funding/programs/{curated['id']}", headers=household_headers
    ).json()
    assert still_curated["current_version"]["source_version"] == base_version["source_version"]
    assert "nominal_rate_bps_annual" not in still_curated["current_version"]["loan_terms_json"]

    # Ein fremder Haushalt sieht den Schatten nicht.
    other_token = _signup(f"funding-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-batt-11")
    other_household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(other_token)
    ).json()
    other_headers = {**_auth_headers(other_token), "X-Household-Id": other_household["id"]}
    visible = client.get(
        "/v1/funding/programs", params={"country_code": "DE"}, headers=other_headers
    ).json()
    assert shadow["id"] not in {p["id"] for p in visible}
