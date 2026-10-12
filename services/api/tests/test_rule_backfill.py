"""Rueckwirkende Regelanwendung (POST /v1/rules/{id}/apply).

Klassifikationsregeln griffen frueher ausschliesslich beim Import. Diese
Tests decken den Lauf ueber bereits gebuchte Transaktionen ab -
insbesondere den Fall, der die Funktion ausgeloest hat: unkategorisierte
Kreditkartenabrechnungen zaehlten als Einnahme, weil die Budgetsummen jeden
unkategorisierten Zufluss als solche werten (siehe budget_service, Schritt 4).
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

# Namen aus dem deutschen Standardpaket
# (supabase/migrations/0001_schema.sql). Die Transferkategorie ist
# genau die, in die eine Kreditkartenabrechnung fachlich gehoert.
CAT_TRANSFER = "Kreditkarte & Zahlungsdienst-Ausgleich"
CAT_GROCERIES = "Lebensmittel"


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"backfill-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Regel-Backfill-Haushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: dict[str, str]) -> str:
    acc = client.post(
        "/v1/accounts",
        json={"name": "Kreditkarte", "account_type": "credit_card"},
        headers=household_headers,
    ).json()
    return str(acc["id"])


def _category_id(headers: dict[str, str], name: str) -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Kategorie '{name}' nicht gefunden")


def _create_transaction(
    headers: dict[str, str],
    account_id: str,
    *,
    booked_date: str,
    amount_minor: int,
    note: str,
    payee_label: str | None = None,
    category_id: str | None = None,
) -> str:
    body: dict[str, Any] = {
        "account_id": account_id,
        "booked_date": booked_date,
        "amount_minor": amount_minor,
        "note": note,
    }
    if payee_label is not None:
        body["payee_label"] = payee_label
    if category_id is not None:
        body["category_id"] = category_id
    res = client.post("/v1/transactions", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def _create_rule(headers: dict[str, str], **overrides: Any) -> str:
    body: dict[str, Any] = {
        "name": "Kreditkartenabrechnung",
        "priority": 10,
        "match_purpose": "Abrechnung vom",
        "action_set_transfer": False,
    }
    body.update(overrides)
    res = client.post("/v1/rules", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def _split_category(headers: dict[str, str], transaction_id: str) -> str | None:
    detail = client.get(f"/v1/transactions/{transaction_id}", headers=headers).json()
    return detail["splits"][0]["category_id"]


async def _rule_apply_events(household_id: str, rule_id: str) -> list[dict[str, Any]]:
    """audit_log hat bewusst keinen Lese-Endpunkt - die Testsession laeuft ohne
    SET ROLE authenticated und damit als postgres mit BYPASSRLS."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        res = await session.execute(
            text(
                "select action, metadata from audit_log "
                "where household_id = :household_id and entity_id = :entity_id "
                "and action = 'rule_apply' order by created_at asc"
            ),
            {"household_id": household_id, "entity_id": rule_id},
        )
        return [dict(row._mapping) for row in res]


def test_preview_reports_without_writing(
    household_headers: dict[str, str], account_id: str
) -> None:
    transfer_category = _category_id(household_headers, CAT_TRANSFER)
    tx_id = _create_transaction(
        household_headers,
        account_id,
        booked_date="2026-07-07",
        amount_minor=150000,
        note="Abrechnung vom 30.06.2026",
    )
    rule_id = _create_rule(household_headers, action_category_id=transfer_category)

    preview = client.post(
        f"/v1/rules/{rule_id}/apply/preview", json={}, headers=household_headers
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True
    assert body["matched"] == 1
    assert body["applied"] == 1
    assert body["truncated"] is False
    assert [item["transaction_id"] for item in body["items"]] == [tx_id]
    assert body["items"][0]["purpose"] == "Abrechnung vom 30.06.2026"
    assert body["items"][0]["previous_category_id"] is None

    # Entscheidend: die Vorschau meldet "applied", hat aber nichts geschrieben.
    assert _split_category(household_headers, tx_id) is None


def test_apply_writes_category_with_rule_provenance(
    household_headers: dict[str, str], account_id: str
) -> None:
    transfer_category = _category_id(household_headers, CAT_TRANSFER)
    tx_id = _create_transaction(
        household_headers,
        account_id,
        booked_date="2026-07-07",
        amount_minor=150000,
        note="Abrechnung vom 30.06.2026",
    )
    rule_id = _create_rule(household_headers, action_category_id=transfer_category)

    applied = client.post(f"/v1/rules/{rule_id}/apply", json={}, headers=household_headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["dry_run"] is False
    assert applied.json()["applied"] == 1

    assert _split_category(household_headers, tx_id) == transfer_category

    # Die Herkunft muss die Regel benennen, nicht "manuell" behaupten.
    provenance = client.get(
        f"/v1/transactions/{tx_id}/provenance", headers=household_headers
    ).json()
    split = provenance["splits"][0]
    assert split["category_source"] == "rule"
    assert split["rule_id"] == rule_id
    assert split["matched_on"]["criteria"] == [
        {"field": "purpose", "pattern": "Abrechnung vom"}
    ]

    # Ein zweiter Lauf findet nichts mehr zu tun.
    again = client.post(f"/v1/rules/{rule_id}/apply", json={}, headers=household_headers).json()
    assert again["applied"] == 0
    assert again["skipped"] == 1
    assert again["items"][0]["reason"] == "already_categorized"


def test_scope_uncategorized_protects_manual_work(
    household_headers: dict[str, str], account_id: str
) -> None:
    transfer_category = _category_id(household_headers, CAT_TRANSFER)
    groceries = _category_id(household_headers, CAT_GROCERIES)
    tx_id = _create_transaction(
        household_headers,
        account_id,
        booked_date="2026-07-07",
        amount_minor=150000,
        note="Abrechnung vom 30.06.2026",
        category_id=groceries,
    )
    rule_id = _create_rule(household_headers, action_category_id=transfer_category)

    default_scope = client.post(
        f"/v1/rules/{rule_id}/apply", json={}, headers=household_headers
    ).json()
    assert default_scope["scope"] == "uncategorized"
    assert default_scope["skipped"] == 1
    assert default_scope["items"][0]["reason"] == "already_categorized"
    assert _split_category(household_headers, tx_id) == groceries

    overwrite = client.post(
        f"/v1/rules/{rule_id}/apply", json={"scope": "all"}, headers=household_headers
    ).json()
    assert overwrite["applied"] == 1
    assert overwrite["items"][0]["previous_category_id"] == groceries
    assert _split_category(household_headers, tx_id) == transfer_category


def test_apply_sets_transfer_flag_and_skips_split_transactions(
    household_headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(household_headers, CAT_GROCERIES)
    plain_id = _create_transaction(
        household_headers,
        account_id,
        booked_date="2026-07-18",
        amount_minor=75000,
        note="Per Lastschrift dankend erhalten",
    )
    split_id = _create_transaction(
        household_headers,
        account_id,
        booked_date="2026-07-19",
        amount_minor=10000,
        note="Per Lastschrift dankend erhalten",
    )
    split_res = client.put(
        f"/v1/transactions/{split_id}/splits",
        json={
            "amount_minor": 10000,
            "splits": [
                {"amount_minor": 6000, "category_id": groceries},
                {"amount_minor": 4000, "category_id": groceries},
            ],
        },
        headers=household_headers,
    )
    assert split_res.status_code == 200, split_res.text

    rule_id = _create_rule(
        household_headers,
        name="Zweitkonto Einzug",
        match_purpose="Per Lastschrift dankend erhalten",
        action_set_transfer=True,
    )
    result = client.post(
        f"/v1/rules/{rule_id}/apply", json={"scope": "all"}, headers=household_headers
    ).json()

    assert result["matched"] == 2
    by_id = {item["transaction_id"]: item for item in result["items"]}
    assert by_id[plain_id]["status"] == "applied"
    assert by_id[split_id]["status"] == "skipped"
    assert by_id[split_id]["reason"] == "split_transaction"

    detail = client.get(f"/v1/transactions/{plain_id}", headers=household_headers).json()
    assert detail["is_transfer"] is True
    # Ohne Zielkategorie bleibt der Split unberuehrt statt auf null zu fallen.
    assert detail["splits"][0]["category_id"] is None


def test_date_range_limits_the_run(household_headers: dict[str, str], account_id: str) -> None:
    transfer_category = _category_id(household_headers, CAT_TRANSFER)
    for booked_date in ("2026-05-08", "2026-06-05", "2026-07-07"):
        _create_transaction(
            household_headers,
            account_id,
            booked_date=booked_date,
            amount_minor=100000,
            note=f"Abrechnung vom {booked_date}",
        )
    rule_id = _create_rule(household_headers, action_category_id=transfer_category)

    scoped = client.post(
        f"/v1/rules/{rule_id}/apply/preview",
        json={"from_date": "2026-06-01", "to_date": "2026-06-30"},
        headers=household_headers,
    ).json()
    assert scoped["matched"] == 1
    assert scoped["items"][0]["booked_date"] == "2026-06-05"


def test_limit_reports_truncation(household_headers: dict[str, str], account_id: str) -> None:
    transfer_category = _category_id(household_headers, CAT_TRANSFER)
    for day in (1, 2, 3):
        _create_transaction(
            household_headers,
            account_id,
            booked_date=f"2026-07-0{day}",
            amount_minor=100000,
            note="Abrechnung vom Vormonat",
        )
    rule_id = _create_rule(household_headers, action_category_id=transfer_category)

    limited = client.post(
        f"/v1/rules/{rule_id}/apply/preview", json={"limit": 2}, headers=household_headers
    ).json()
    # matched zaehlt alles, items sind gedeckelt - kein stilles Abschneiden.
    assert limited["matched"] == 3
    assert len(limited["items"]) == 2
    assert limited["truncated"] is True


def test_rule_without_effect_is_rejected(
    household_headers: dict[str, str], account_id: str
) -> None:
    rule_id = _create_rule(household_headers)
    res = client.post(f"/v1/rules/{rule_id}/apply", json={}, headers=household_headers)
    assert res.status_code == 422

    missing = client.post(
        f"/v1/rules/{uuid.uuid4()}/apply", json={}, headers=household_headers
    )
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_apply_is_audited(household_headers: dict[str, str], account_id: str) -> None:
    transfer_category = _category_id(household_headers, CAT_TRANSFER)
    tx_id = _create_transaction(
        household_headers,
        account_id,
        booked_date="2026-07-07",
        amount_minor=150000,
        note="Abrechnung vom 30.06.2026",
    )
    rule_id = _create_rule(household_headers, action_category_id=transfer_category)

    client.post(f"/v1/rules/{rule_id}/apply/preview", json={}, headers=household_headers)
    household_id = household_headers["X-Household-Id"]
    # Eine Vorschau ist keine Aenderung und darf nichts protokollieren.
    assert await _rule_apply_events(household_id, rule_id) == []

    client.post(f"/v1/rules/{rule_id}/apply", json={}, headers=household_headers)
    events = await _rule_apply_events(household_id, rule_id)
    assert len(events) == 1
    assert events[0]["metadata"]["applied"] == 1
    assert events[0]["metadata"]["transaction_ids"] == [tx_id]


def test_backfill_removes_settlement_from_budget_income(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Anlassfall: eine unkategorisierte Gutschrift zaehlt als Einnahme,
    weil budget_service jeden unkategorisierten Zufluss so wertet. Erst die
    Transferkategorie nimmt sie wieder heraus."""
    transfer_category = _category_id(household_headers, CAT_TRANSFER)
    _create_transaction(
        household_headers,
        account_id,
        booked_date="2026-07-07",
        amount_minor=150000,
        note="Abrechnung vom 30.06.2026",
    )

    before = client.get("/v1/budgets/2026/7", headers=household_headers).json()
    assert before["total_income_minor"] == 150000

    rule_id = _create_rule(household_headers, action_category_id=transfer_category)
    client.post(f"/v1/rules/{rule_id}/apply", json={}, headers=household_headers)

    after = client.get("/v1/budgets/2026/7", headers=household_headers).json()
    assert after["total_income_minor"] == 0
