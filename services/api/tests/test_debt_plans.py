"""Tilgungsplan-API: eigenstaendig nutzbar fuer Bestandsschulden, per
`project_financing_source_id` auch fuer Projektdarlehen (Plan 7.3/14.2)."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"debtplan-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-6")
    household = client.post(
        "/v1/households", json={"name": "Darlehen-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_asset_debt(headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/v1/assets",
        json={
            "name": "Bestandskredit",
            "type": "debt",
            "asset_class": "loan",
            "current_value_minor": 50_000_00,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_asset_debt_creation_auto_provisions_a_debt_plan(household_headers: dict[str, str]) -> None:
    asset = _create_asset_debt(household_headers)
    # Plan 14.4: bei einer neuen Schuld entsteht in derselben Transaktion ein
    # einfacher debt_plan, damit debt_plans ab sofort die alleinige
    # Rechengrundlage ist -- kein `None`/500 beim payoff-Feld.
    assert asset["payoff_months_projected"] is not None

    plans = client.get(
        "/v1/debt-plans", headers=household_headers
    ).json()
    matching = [p for p in plans if p["asset_debt_id"] == asset["id"]]
    assert len(matching) == 1
    assert matching[0]["amortization_mode"] == "full_repayment_term"
    assert matching[0]["target_term_months"] == 360


def test_debt_plan_crud_concurrency_and_schedule(household_headers: dict[str, str]) -> None:
    created = client.post(
        "/v1/debt-plans",
        json={
            "asset_debt_id": None,
            "name": "Neues Darlehen",
            "principal_limit_minor": 200_000_00,
            "starts_on": "2027-01-01",
            "amortization_mode": "fixed_payment",
            "fixed_payment_minor": 1_200_00,
        },
        headers=household_headers,
    )
    # asset_debt_id und project_financing_source_id sind beide None -> ungueltig.
    assert created.status_code == 422

    ok = client.post(
        "/v1/debt-plans",
        json={
            "name": "Neues Darlehen",
            "principal_limit_minor": 200_000_00,
            "opening_balance_minor": 200_000_00,
            "starts_on": "2027-01-01",
            "amortization_mode": "fixed_payment",
            "fixed_payment_minor": 1_200_00,
            **_owner_for_new_asset(household_headers),
        },
        headers=household_headers,
    )
    assert ok.status_code == 201, ok.text
    plan = ok.json()
    assert plan["version"] == 1

    # Ohne Ratephase liefert die Berechnung einen kontrollierten 422, kein 500.
    no_rate = client.get(f"/v1/debt-plans/{plan['id']}/schedule", headers=household_headers)
    assert no_rate.status_code == 422
    assert no_rate.json()["detail"] == "loan_rate_gap"

    rate_periods = client.put(
        f"/v1/debt-plans/{plan['id']}/rate-periods",
        json={"periods": [{"starts_after_month": 0, "nominal_rate_bps_annual": 400}]},
        headers=household_headers,
    )
    assert rate_periods.status_code == 200, rate_periods.text

    schedule = client.get(f"/v1/debt-plans/{plan['id']}/schedule", headers=household_headers)
    assert schedule.status_code == 200, schedule.text
    body = schedule.json()
    assert body["rows"][0]["opening_balance_minor"] == 200_000_00
    assert body["ending_balance_minor"] >= 0

    without_header = client.patch(
        f"/v1/debt-plans/{plan['id']}", json={"note": "x"}, headers=household_headers
    )
    assert without_header.status_code == 428

    stale = client.patch(
        f"/v1/debt-plans/{plan['id']}",
        json={"creditor_label": "Bank X"},
        headers={**household_headers, "If-Match": '"7"'},
    )
    assert stale.status_code == 409

    updated = client.patch(
        f"/v1/debt-plans/{plan['id']}",
        json={"creditor_label": "Bank X"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2


def _owner_for_new_asset(headers: dict[str, str]) -> dict[str, Any]:
    asset = client.post(
        "/v1/assets",
        json={"name": "Zweitobjekt", "type": "asset", "asset_class": "real_estate"},
        headers=headers,
    ).json()
    return {"asset_debt_id": asset["id"]}


def test_debt_plan_is_isolated_per_household(household_headers: dict[str, str]) -> None:
    asset = _owner_for_new_asset(household_headers)["asset_debt_id"]
    plan = client.post(
        "/v1/debt-plans",
        json={
            "asset_debt_id": asset,
            "name": "Isoliertes Darlehen",
            "principal_limit_minor": 100_000_00,
            "starts_on": "2027-01-01",
            "amortization_mode": "full_repayment_term",
            "target_term_months": 180,
        },
        headers=household_headers,
    ).json()

    foreign_token = _signup(
        f"debtplan-foreign-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-7"
    )
    foreign_household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(foreign_token)
    ).json()
    foreign_headers = {**_auth_headers(foreign_token), "X-Household-Id": foreign_household["id"]}

    assert client.get(f"/v1/debt-plans/{plan['id']}", headers=foreign_headers).status_code == 404
    assert (
        client.get(f"/v1/debt-plans/{plan['id']}/schedule", headers=foreign_headers).status_code
        == 404
    )


def test_debt_plan_viewer_cannot_write(household_headers: dict[str, str]) -> None:
    household_id = household_headers["X-Household-Id"]
    viewer_headers = _invite_member(client, household_headers, household_id, "viewer", "debtplan-v")
    asset = _owner_for_new_asset(household_headers)["asset_debt_id"]

    create = client.post(
        "/v1/debt-plans",
        json={
            "asset_debt_id": asset,
            "name": "Viewer-Test",
            "principal_limit_minor": 10_000_00,
            "starts_on": "2027-01-01",
            "amortization_mode": "full_repayment_term",
            "target_term_months": 120,
        },
        headers=viewer_headers,
    )
    assert create.status_code == 403
