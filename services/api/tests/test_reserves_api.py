import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(
        f"reserve-ledger-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    household = client.post(
        "/v1/households", json={"name": "Rücklagen-Ledger"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_yearly_contract(headers: dict[str, str]) -> None:
    response = client.post(
        "/v1/contracts",
        json={
            "name": "Besuch Schwiegermutter",
            "amount_minor": 150_000,
            "frequency": "yearly",
            "next_due_date": "2027-03-15",
            "reserve_enabled": True,
            "reserve_started_on": "2026-08-01",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text


def test_deposit_updates_actual_and_balance_but_not_current_recommendation(
    household_headers: dict[str, str],
) -> None:
    _create_yearly_contract(household_headers)
    before = client.get(
        "/v1/reserves/overview?year=2026&month=8", headers=household_headers
    )
    assert before.status_code == 200, before.text
    pot = next(item for item in before.json()["pots"] if item["kind"] == "contract")
    assert pot["recommended_minor"] == 21_429
    assert pot["balance_minor"] == 0

    deposited = client.post(
        "/v1/reserves/deposits",
        json={
            "occurred_on": "2026-08-10",
            "total_minor": 10_000,
            "allocations": [{"pot_id": pot["id"], "amount_minor": 10_000}],
            "note": "Manuell bestätigt",
        },
        headers=household_headers,
    )
    assert deposited.status_code == 201, deposited.text

    after = client.get(
        "/v1/reserves/overview?year=2026&month=8", headers=household_headers
    ).json()
    updated = next(item for item in after["pots"] if item["id"] == pot["id"])
    assert updated["recommended_minor"] == 21_429
    assert updated["deposited_minor"] == 10_000
    assert updated["open_minor"] == 11_429
    assert updated["balance_minor"] == 10_000
    assert updated["next_month_recommended_minor"] == 23_334
    assert updated["status"] == "partial"

    august_budget = client.get("/v1/budgets/2026/8", headers=household_headers)
    september_budget = client.get("/v1/budgets/2026/9", headers=household_headers)
    assert august_budget.status_code == 200, august_budget.text
    assert september_budget.status_code == 200, september_budget.text
    assert august_budget.json()["fixed_cost_reserves_monthly_minor"] == 21_429
    assert september_budget.json()["fixed_cost_reserves_monthly_minor"] == 23_334


def test_skip_then_deposit_and_overdraw_protection(
    household_headers: dict[str, str],
) -> None:
    _create_yearly_contract(household_headers)
    overview = client.get(
        "/v1/reserves/overview?year=2026&month=8", headers=household_headers
    ).json()
    pot = next(item for item in overview["pots"] if item["kind"] == "contract")
    skipped = client.put(
        "/v1/reserves/month-decisions/skip",
        json={"pot_id": pot["id"], "year": 2026, "month": 8},
        headers=household_headers,
    )
    assert skipped.status_code == 204, skipped.text
    state = client.get(
        "/v1/reserves/overview?year=2026&month=8", headers=household_headers
    ).json()
    assert next(item for item in state["pots"] if item["id"] == pot["id"])["status"] == "skipped"

    restored = client.request(
        "DELETE",
        "/v1/reserves/month-decisions/skip",
        json={"pot_id": pot["id"], "year": 2026, "month": 8},
        headers=household_headers,
    )
    assert restored.status_code == 204, restored.text
    restored_state = client.get(
        "/v1/reserves/overview?year=2026&month=8", headers=household_headers
    ).json()
    assert next(
        item for item in restored_state["pots"] if item["id"] == pot["id"]
    )["status"] == "open"

    overdraw = client.post(
        "/v1/reserves/withdrawals",
        json={"pot_id": pot["id"], "amount_minor": 1, "occurred_on": "2026-08-10"},
        headers=household_headers,
    )
    assert overdraw.status_code == 409


def test_large_amount_is_only_written_after_explicit_distribution(
    household_headers: dict[str, str],
) -> None:
    _create_yearly_contract(household_headers)
    before = client.get(
        "/v1/reserves/overview?year=2026&month=8", headers=household_headers
    ).json()
    preview = client.post(
        "/v1/reserves/distribution-preview",
        json={"year": 2026, "month": 8, "total_minor": 2_000_000},
        headers=household_headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["free_remainder_minor"] == 1_978_571
    unchanged = client.get(
        "/v1/reserves/overview?year=2026&month=8", headers=household_headers
    ).json()
    assert unchanged["total_balance_minor"] == before["total_balance_minor"] == 0

    contract = next(item for item in unchanged["pots"] if item["kind"] == "contract")
    free = next(item for item in unchanged["pots"] if item["kind"] == "free")
    confirmed = client.post(
        "/v1/reserves/deposits",
        json={
            "occurred_on": "2026-08-10",
            "total_minor": 2_000_000,
            "allocations": [
                {"pot_id": contract["id"], "amount_minor": 21_429},
                {"pot_id": free["id"], "amount_minor": 1_978_571},
            ],
            "note": "20.000 Euro bewusst verteilt",
        },
        headers=household_headers,
    )
    assert confirmed.status_code == 201, confirmed.text
    after = client.get(
        "/v1/reserves/overview?year=2026&month=8", headers=household_headers
    ).json()
    assert after["total_balance_minor"] == 2_000_000
    assert after["free_reserve_balance_minor"] == 1_978_571
