"""Die Fachwerkzeuge des KI-Felds gegen die echte Datenbank (E5: nur lesend).

Jedes Werkzeug laeuft wie in der Schleife in read_only_session(): READ ONLY
als erste Anweisung, danach Rolle und Claims des Nutzers (RLS). Damit
beweist ein gruener Lauf dreierlei:

  * kein Werkzeug schreibt - ein Schreibversuch scheiterte an READ ONLY,
    insbesondere legt `budget` fuer einen ungeplanten Monat keine
    budgets-Zeile an;
  * ein Werkzeug sieht nur den Haushalt, dem der Nutzer angehoert - auch
    dann, wenn der Haushaltskontext auf einen fremden Haushalt zeigt;
  * Betraege kommen in Euro, Texte mit IBAN maskiert.

Laeuft gegen das lokale Supabase wie tests/test_household_isolation.py.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.assistant.tools import domain_tools, field_catalog_text, start_context
from app.assistant.tools.base import (
    ToolContext,
    ToolRejected,
    ToolReply,
    read_only_session,
)
from app.core.config import get_settings
from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import (
    _auth_headers,
    _invite_member,
    _signup,
    _user_id_from_token,
)

client = TestClient(app)

_DSN = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
_TIMEOUT_MS = 60_000

TODAY = date.today()
THIS_MONTH = TODAY.replace(day=1)
LAST_MONTH = (THIS_MONTH - timedelta(days=1)).replace(day=1)

# Ein Monat, fuer den es sicher keine budgets-Zeile gibt.
UNPLANNED_YEAR, UNPLANNED_MONTH = 2003, 4

IBAN = "DE89370400440532013000"
IBAN_SPACED = "DE89 3704 0044 0532 0130 00"

EXPECTED_TOOLS = [
    "describe_fields",
    "query_analytics",
    "list_accounts",
    "overview",
    "cashflow_trend",
    "fixed_costs_trend",
    "variable_costs",
    "budget",
    "budget_year",
    "contracts",
    "contract",
    "contract_summary",
    "goals",
    "net_worth",
    "forecast",
    "categories",
    "transaction",
    "reserves",
    "insights",
]


def _headers(token: str, household_id: str) -> dict[str, str]:
    return {**_auth_headers(token), "X-Household-Id": household_id}


def _category_id(headers: dict[str, str], name: str) -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Standardkategorie '{name}' nicht gefunden")


def _tx(
    headers: dict[str, str], account_id: str, booked: date, amount_minor: int, **extra: Any
) -> str:
    response = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": booked.isoformat(),
            "amount_minor": amount_minor,
            **extra,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.fixture(scope="module")
def world() -> dict[str, Any]:
    """Haushalt A mit Konto, Buchungen, Vertrag und Sparziel; ein viewer in A;
    Haushalt B mit eigenen Daten; ein Nutzer ohne Haushalt.

    Modulweit, damit die Daten vor dem ersten Funktions-Aufraeumen entstehen
    (packages/test-fixtures/dbcleanup.py raeumt sie am Laufende ab)."""
    owner_token = _signup(f"kit-a-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-1")
    household_a = client.post(
        "/v1/households", json={"name": "KI-Werkzeuge A"}, headers=_auth_headers(owner_token)
    ).json()["id"]
    headers_a = _headers(owner_token, household_a)
    account_a = client.post(
        "/v1/accounts",
        json={"name": "Girokonto A", "account_type": "checking", "opening_balance_minor": 100000},
        headers=headers_a,
    ).json()["id"]
    grocery = _category_id(headers_a, "Lebensmittel")
    salary = _category_id(headers_a, "Gehalt & Lohn")

    tx_small = _tx(
        headers_a, account_a, THIS_MONTH, -1234, category_id=grocery, payee_label="Kleinmarkt"
    )
    tx_iban = _tx(
        headers_a,
        account_a,
        THIS_MONTH,
        -500,
        category_id=grocery,
        payee_label="Ibanmarkt",
        note=f"Rueckfrage an {IBAN}",
    )
    _tx(headers_a, account_a, LAST_MONTH, -4250, category_id=grocery, payee_label="Kleinmarkt")
    _tx(headers_a, account_a, LAST_MONTH, 250000, category_id=salary, payee_label="Arbeitgeber")
    # Verwendungszweck kommt sonst nur aus dem Import - hier direkt gesetzt.
    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute(
            "update transactions set purpose_raw = %s where id = %s",
            (f"SEPA-Gutschrift IBAN {IBAN_SPACED} Referenz", tx_iban),
        )

    contract_a = client.post(
        "/v1/contracts",
        json={
            "name": "Streaming A",
            "amount_minor": 1299,
            "frequency": "monthly",
            "next_due_date": TODAY.isoformat(),
            "contract_key": "MANDAT-GEHEIM-4711",
            "category_id": grocery,
        },
        headers=headers_a,
    )
    assert contract_a.status_code == 201, contract_a.text
    goal = client.post(
        "/v1/goals", json={"name": "Urlaub A", "target_amount_minor": 300000}, headers=headers_a
    )
    assert goal.status_code == 201, goal.text

    viewer_headers = _invite_member(client, headers_a, household_a, "viewer", "kit-viewer")
    viewer_token = viewer_headers["Authorization"].removeprefix("Bearer ")

    owner_b_token = _signup(
        f"kit-b-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    household_b = client.post(
        "/v1/households", json={"name": "KI-Werkzeuge B"}, headers=_auth_headers(owner_b_token)
    ).json()["id"]
    headers_b = _headers(owner_b_token, household_b)
    account_b = client.post(
        "/v1/accounts", json={"name": "Konto B", "account_type": "checking"}, headers=headers_b
    ).json()["id"]
    tx_b = _tx(headers_b, account_b, THIS_MONTH, -777, payee_label="Nur-B-Laden")

    outsider_token = _signup(
        f"kit-c-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-3"
    )

    return {
        "household_a": uuid.UUID(household_a),
        "owner_a": uuid.UUID(_user_id_from_token(owner_token)),
        "viewer_a": uuid.UUID(_user_id_from_token(viewer_token)),
        "account_a": account_a,
        "tx_small": tx_small,
        "tx_iban": tx_iban,
        "contract_a": str(contract_a.json()["id"]),
        "grocery": grocery,
        "household_b": uuid.UUID(household_b),
        "owner_b": uuid.UUID(_user_id_from_token(owner_b_token)),
        "account_b": account_b,
        "tx_b": tx_b,
        "outsider": uuid.UUID(_user_id_from_token(outsider_token)),
    }


async def _run(
    name: str, household_id: uuid.UUID, user_id: uuid.UUID, args: dict[str, Any]
) -> ToolReply:
    """Ein Werkzeug wie in der Schleife: eigene READ-ONLY-Transaktion, RLS des
    Nutzers; ToolRejected wird zum Ergebnis mit status rejected."""
    spec = next(item for item in domain_tools() if item.name == name)
    assert spec.handler is not None
    async with read_only_session(
        get_session_factory(), household_id=household_id, user_id=user_id, timeout_ms=_TIMEOUT_MS
    ) as session:
        ctx = ToolContext(session=session, household_id=household_id, user_id=user_id, today=TODAY)
        try:
            return await spec.handler(ctx, args)
        except ToolRejected as error:
            return ToolReply(status="rejected", text=str(error))


def _rows(reply: ToolReply) -> list[dict[str, Any]]:
    assert reply.table is not None
    return [dict(zip(reply.table.columns, row, strict=True)) for row in reply.table.rows]


def _args_for(world: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "query_analytics": {
            "query": {
                "group_by": [{"field": "category"}],
                "measures": [{"field": "amount", "aggregate": "sum"}],
            }
        },
        "contract": {"id": world["contract_a"]},
        "transaction": {"id": world["tx_small"]},
        "budget": {"year": UNPLANNED_YEAR, "month": UNPLANNED_MONTH},
    }


def _budget_rows(household_id: uuid.UUID) -> list[tuple[int, int]]:
    with psycopg.connect(_DSN, autocommit=True) as conn:
        rows = conn.execute(
            "select year, month from budgets where household_id = %s order by year, month",
            (str(household_id),),
        ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]


# ---------------------------------------------------------------------------
# Jedes Werkzeug laeuft lesend
# ---------------------------------------------------------------------------


def test_domain_tools_are_complete_and_ordered() -> None:
    assert [spec.name for spec in domain_tools()] == EXPECTED_TOOLS
    for spec in domain_tools():
        assert spec.definition()["function"]["parameters"]["type"] == "object"
    assert "amount" in field_catalog_text()


async def test_every_tool_runs_read_only_for_the_owner(world: dict[str, Any]) -> None:
    household, owner = world["household_a"], world["owner_a"]
    budgets_before = _budget_rows(household)
    assert (UNPLANNED_YEAR, UNPLANNED_MONTH) not in budgets_before
    args = _args_for(world)

    for name in EXPECTED_TOOLS:
        reply = await _run(name, household, owner, args.get(name, {}))
        assert reply.status == "ok", f"{name}: {reply.status} {reply.text}"

    # Weder der ungeplante Monat noch der laufende (overview/budget ohne
    # Argument) bekam eine budgets-Zeile - die Werkzeuge lesen nur.
    assert _budget_rows(household) == budgets_before


async def test_tool_results_carry_the_household_data(world: dict[str, Any]) -> None:
    household, owner = world["household_a"], world["owner_a"]

    accounts = await _run("list_accounts", household, owner, {})
    account = next(row for row in _rows(accounts) if row["id"] == world["account_a"])
    # 1.000,00 Eroeffnung - 12,34 - 5,00 - 42,50 + 2.500,00
    assert account["balance_eur"] == 3440.16
    assert "balance_minor" not in account

    contract = await _run("contract", household, owner, {"id": world["contract_a"]})
    assert contract.summary is not None
    assert contract.summary["amount_eur"] == 12.99
    assert "contract_key" not in contract.summary
    assert "MANDAT-GEHEIM-4711" not in repr(contract)

    budget = await _run(
        "budget", household, owner, {"year": UNPLANNED_YEAR, "month": UNPLANNED_MONTH}
    )
    assert budget.summary is not None and budget.summary["budget_exists"] is False

    year = await _run("budget_year", household, owner, {"year": THIS_MONTH.year})
    assert year.summary is not None and year.summary["categories_without_values"] > 0
    assert [row["category_name"] for row in _rows(year)] == ["Lebensmittel"]

    facts = await _run_start_context(household, owner)
    assert facts["today"] == TODAY.isoformat()
    assert facts["transactions"]["count"] == 4
    assert any(item["balance_eur"] == 3440.16 for item in facts["accounts"])


async def _run_start_context(household: uuid.UUID, user: uuid.UUID) -> dict[str, Any]:
    async with read_only_session(
        get_session_factory(), household_id=household, user_id=user, timeout_ms=_TIMEOUT_MS
    ) as session:
        return await start_context(session, household, TODAY)


async def test_viewer_can_run_every_tool(world: dict[str, Any]) -> None:
    household, viewer = world["household_a"], world["viewer_a"]
    args = _args_for(world)
    for name in EXPECTED_TOOLS:
        reply = await _run(name, household, viewer, args.get(name, {}))
        assert reply.status == "ok", f"{name}: {reply.status} {reply.text}"

    accounts = await _run("list_accounts", household, viewer, {})
    assert world["account_a"] in [row["id"] for row in _rows(accounts)]


async def test_read_only_session_refuses_writes(world: dict[str, Any]) -> None:
    household, owner = world["household_a"], world["owner_a"]
    statements = [
        (
            "insert into accounts (household_id, name, account_type) "
            "values (:household_id, 'Schreibversuch', 'checking')",
            {"household_id": str(household)},
        ),
        (
            "update accounts set name = 'Umbenannt' where id = :id",
            {"id": world["account_a"]},
        ),
    ]
    for statement, params in statements:
        with pytest.raises(DBAPIError) as caught:
            async with read_only_session(
                get_session_factory(),
                household_id=household,
                user_id=owner,
                timeout_ms=_TIMEOUT_MS,
            ) as session:
                await session.execute(text(statement), params)
        assert "read-only transaction" in str(caught.value)

    with psycopg.connect(_DSN, autocommit=True) as conn:
        names = conn.execute(
            "select name from accounts where household_id = %s", (str(household),)
        ).fetchall()
    assert [row[0] for row in names] == ["Girokonto A"]


# ---------------------------------------------------------------------------
# Haushaltstrennung
# ---------------------------------------------------------------------------


async def test_other_household_never_sees_household_a(world: dict[str, Any]) -> None:
    household_b, owner_b = world["household_b"], world["owner_b"]

    accounts = await _run("list_accounts", household_b, owner_b, {})
    assert [row["id"] for row in _rows(accounts)] == [world["account_b"]]

    rows = await _run(
        "query_analytics", household_b, owner_b, {"query": {"grain": "rows"}}
    )
    assert rows.status == "ok"
    transaction_ids = {row["transaction_id"] for row in _rows(rows)}
    assert transaction_ids == {world["tx_b"]}

    foreign_tx = await _run("transaction", household_b, owner_b, {"id": world["tx_small"]})
    assert foreign_tx.status == "rejected"
    foreign_contract = await _run("contract", household_b, owner_b, {"id": world["contract_a"]})
    assert foreign_contract.status == "rejected"
    contracts = await _run("contracts", household_b, owner_b, {})
    assert _rows(contracts) == []


@pytest.mark.parametrize("intruder", ["owner_b", "outsider"])
async def test_non_member_sees_nothing_even_with_household_a_context(
    world: dict[str, Any], intruder: str
) -> None:
    """Der Haushaltskontext zeigt auf A, der Nutzer ist dort kein Mitglied:
    RLS allein liefert nichts - unabhaengig von der Mitgliedschaftspruefung
    der API."""
    household_a, user = world["household_a"], world[intruder]

    accounts = await _run("list_accounts", household_a, user, {})
    assert _rows(accounts) == []
    rows = await _run("query_analytics", household_a, user, {"query": {"grain": "rows"}})
    assert _rows(rows) == []
    transaction = await _run("transaction", household_a, user, {"id": world["tx_small"]})
    assert transaction.status == "rejected"
    contracts = await _run("contracts", household_a, user, {})
    assert _rows(contracts) == []
    goals = await _run("goals", household_a, user, {})
    assert _rows(goals) == []
    facts = await _run_start_context(household_a, user)
    assert facts["accounts"] == [] and facts["transactions"]["count"] == 0


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("budget", {"year": 2026, "month": 13}, "'month'"),
        ("budget", {"monat": 5}, "Unbekannte Argumente: monat"),
        ("cashflow_trend", {"months": "viele"}, "'months'"),
        ("contracts", {"status": "aktiv"}, "'status'"),
        ("contract", {"id": "kein-uuid"}, "keine gültige ID"),
        ("contract", {"id": str(uuid.uuid4())}, "gibt es in diesem Haushalt nicht"),
        ("transaction", {}, "'id' fehlt"),
        ("forecast", {"include_savings": "vielleicht"}, "'include_savings'"),
    ],
)
async def test_invalid_arguments_are_rejected(
    world: dict[str, Any], name: str, args: dict[str, Any], expected: str
) -> None:
    reply = await _run(name, world["household_a"], world["owner_a"], args)
    assert reply.status == "rejected"
    assert reply.text is not None and expected in reply.text


# ---------------------------------------------------------------------------
# query_analytics: Ablehnung, Euro, Maskierung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            {"group_by": [{"field": "gibt_es_nicht"}], "measures": [{"aggregate": "sum"}]},
            ["group_by[0]", "field_unknown"],
        ),
        ({"grain": "quer", "measures": [{"aggregate": "sum"}]}, ["grain"]),
        ({"measures": [{"aggregate": "sum"}], "filters": {}}, ["filters", "unbekannter"]),
        ({"grain": "aggregate"}, ["Kennzahl"]),
        (
            {
                "measures": [{"aggregate": "sum"}],
                "having": {"condition": {"field": "amount", "op": "gt", "value": 1}},
            },
            ["having"],
        ),
    ],
)
async def test_invalid_query_is_rejected_with_issues(
    world: dict[str, Any], query: dict[str, Any], expected: list[str]
) -> None:
    reply = await _run("query_analytics", world["household_a"], world["owner_a"], {"query": query})
    assert reply.status == "rejected"
    assert reply.text is not None
    for fragment in expected:
        assert fragment in reply.text


async def test_query_analytics_speaks_euro(world: dict[str, Any]) -> None:
    household, owner = world["household_a"], world["owner_a"]

    rows = await _run(
        "query_analytics",
        household,
        owner,
        {"query": {"grain": "rows", "search": "Kleinmarkt", "order_by": [{"key": "date"}]}},
    )
    amounts = sorted(row["amount"] for row in _rows(rows))
    assert amounts == [-42.5, -12.34]

    # Betragsfilter in Euro: -10 heisst -10,00 EUR, nicht -0,10 EUR.
    filtered = await _run(
        "query_analytics",
        household,
        owner,
        {
            "query": {
                "grain": "rows",
                "period": {"kind": "current_month"},
                "where": {"condition": {"field": "amount", "op": "lt", "value": -10}},
            }
        },
    )
    assert [row["transaction_id"] for row in _rows(filtered)] == [world["tx_small"]]

    total = await _run(
        "query_analytics",
        household,
        owner,
        {
            "query": {
                "group_by": [{"field": "category"}],
                "measures": [{"field": "amount", "aggregate": "sum"}],
                "period": {"kind": "current_month"},
            }
        },
    )
    assert total.summary is not None
    assert total.summary["total_row"]["sum_amount"] == -17.34
    by_category = {row["category"]: row["sum_amount"] for row in _rows(total)}
    assert by_category[world["grocery"]] == -17.34
    assert "(Euro)" in total.summary["columns"]["sum_amount"]


async def test_iban_in_note_and_purpose_is_masked(world: dict[str, Any]) -> None:
    household, owner = world["household_a"], world["owner_a"]

    detail = await _run("transaction", household, owner, {"id": world["tx_iban"]})
    assert detail.status == "ok" and detail.summary is not None
    rendered = repr(detail.summary)
    assert IBAN not in rendered
    assert "0532 0130" not in rendered
    assert "0532013000" not in rendered
    assert detail.summary["note"].startswith("Rueckfrage an DE89")
    assert detail.summary["amount_eur"] == -5.0

    rows = await _run(
        "query_analytics",
        household,
        owner,
        {"query": {"grain": "rows", "search": "Ibanmarkt", "fields": ["purpose", "note"]}},
    )
    (row,) = _rows(rows)
    assert IBAN not in repr(row) and "0532 0130" not in repr(row)
    assert row["note"] is not None and row["purpose"] is not None
