import uuid
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services.goal_service import evaluate_traffic_light
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _database_today() -> date:
    """Kalendertag der DATENBANK, nicht des Testrechners.

    `asset_valuations.valued_on` bekommt seinen Wert aus dem Schema-Default
    `current_date` (routes/goals.py fuegt die Spalte nicht selbst ein), und
    `current_date` richtet sich nach der Zeitzone der Datenbanksitzung - bei
    Supabase UTC. Zwischen lokaler Mitternacht und dem UTC-Versatz liegen
    beide Kalendertage auseinander; ein Vergleich gegen `date.today()` liess
    diesen Test im Sommer jede Nacht zwischen 00:00 und 02:00 Ortszeit
    fehlschlagen, ohne dass sich am Code etwas geaendert hatte.

    Eigene synchrone Verbindung, bewusst getrennt von der Async-Engine der
    Anwendung - dasselbe Vorgehen wie in packages/test-fixtures/dbcleanup.py.
    """
    dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cur:
            cur.execute("select current_date")
            row = cur.fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, date)
    return value


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p7-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Phase-7-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def test_savings_goals_crud_and_monthly_required(household_headers: dict[str, str]) -> None:
    # 1. Sparziel anlegen
    g_res = client.post(
        "/v1/goals",
        json={
            "name": "Notgroschen 3 Monatsgehälter",
            "target_amount_minor": 900000,
            "starting_amount_minor": 300000,
            "target_date": "2027-12-31",
        },
        headers=household_headers,
    )
    assert g_res.status_code == 201
    g_data = g_res.json()
    assert g_data["name"] == "Notgroschen 3 Monatsgehälter"
    assert g_data["starting_amount_minor"] == 300000
    assert g_data["current_amount_minor"] == 300000  # noch keine zugeordneten Splits
    assert g_data["monthly_required_minor"] > 0

    # 2. Sparziele listen
    l_res = client.get("/v1/goals", headers=household_headers)
    assert l_res.status_code == 200
    assert len(l_res.json()) == 1

    # 3. Startbetrag aktualisieren
    goal_id = g_data["id"]
    u_res = client.patch(
        f"/v1/goals/{goal_id}",
        json={"starting_amount_minor": 500000},
        headers=household_headers,
    )
    assert u_res.status_code == 200
    assert u_res.json()["current_amount_minor"] == 500000


def test_both_planning_modes_preview_and_budget_overlay_are_server_calculated(
    household_headers: dict[str, str],
) -> None:
    today = date.today()
    before = client.get(
        f"/v1/budgets/{today.year}/{today.month}", headers=household_headers
    ).json()

    date_goal = client.post(
        "/v1/goals",
        json={
            "name": "Familienbesuch",
            "target_amount_minor": 150000,
            "starting_amount_minor": 0,
            "planning_mode": "target_date",
            "target_date": f"{today.year + 1}-06-01",
            "include_in_planning": True,
            "priority": 1,
        },
        headers=household_headers,
    )
    assert date_goal.status_code == 201, date_goal.text
    assert date_goal.json()["monthly_required_minor"] > 0
    assert date_goal.json()["effective_monthly_reserve_minor"] > 0
    assert date_goal.json()["projected_completion_date"].endswith("-06-01")

    monthly_goal = client.post(
        "/v1/goals",
        json={
            "name": "Neues Handy",
            "target_amount_minor": 70000,
            "planning_mode": "monthly_amount",
            "monthly_contribution_minor": 2500,
            "include_in_planning": True,
            "priority": 2,
        },
        headers=household_headers,
    )
    assert monthly_goal.status_code == 201, monthly_goal.text
    assert monthly_goal.json()["monthly_required_minor"] == 2500
    assert monthly_goal.json()["projected_completion_date"] is not None

    excluded = client.post(
        "/v1/goals",
        json={
            "name": "Nur beobachten",
            "target_amount_minor": 100000,
            "planning_mode": "monthly_amount",
            "monthly_contribution_minor": 10000,
            "include_in_planning": False,
        },
        headers=household_headers,
    )
    assert excluded.status_code == 201

    reached = client.post(
        "/v1/goals",
        json={
            "name": "Schon erreicht",
            "target_amount_minor": 20000,
            "starting_amount_minor": 20000,
            "planning_mode": "monthly_amount",
            "monthly_contribution_minor": 5000,
            "include_in_planning": True,
        },
        headers=household_headers,
    )
    assert reached.json()["status"] == "reached"
    assert reached.json()["effective_monthly_reserve_minor"] == 0

    after = client.get(
        f"/v1/budgets/{today.year}/{today.month}", headers=household_headers
    ).json()
    expected_reserve = (
        date_goal.json()["effective_monthly_reserve_minor"]
        + monthly_goal.json()["effective_monthly_reserve_minor"]
    )
    assert after["goal_reserves_monthly_minor"] == expected_reserve
    assert after["goal_reserve_count"] == 2
    assert after["free_after_goal_reserves_minor"] == (
        after["surplus_before_goal_reserves_minor"] - expected_reserve
    )
    # Additive Ebene: kein existierender Plan-/Ist-/Bucket-Wert darf sich
    # allein durch das Anlegen kalkulatorischer Spartoepfe veraendern.
    invariant_fields = (
        "total_income_minor",
        "total_planned_minor",
        "total_rollover_minor",
        "total_spent_minor",
        "total_remaining_minor",
        "savings_rate_percent",
        "bucket_needs_minor",
        "bucket_wants_minor",
        "bucket_savings_minor",
        "fixed_costs_monthly_minor",
    )
    assert {field: before[field] for field in invariant_fields} == {
        field: after[field] for field in invariant_fields
    }

    preview = client.post(
        "/v1/goals/planning-preview",
        json={
            "target_amount_minor": 70000,
            "current_amount_minor": 10000,
            "planning_mode": "monthly_amount",
            "monthly_contribution_minor": 5000,
        },
        headers=household_headers,
    )
    assert preview.status_code == 200
    assert preview.json()["remaining_amount_minor"] == 60000
    assert preview.json()["monthly_required_minor"] == 5000
    assert preview.json()["projected_completion_date"] is not None


def test_included_plan_validation_and_wish_start_are_atomic(
    household_headers: dict[str, str],
) -> None:
    invalid_date = client.post(
        "/v1/goals",
        json={
            "name": "Unvollstaendig",
            "target_amount_minor": 50000,
            "planning_mode": "target_date",
            "include_in_planning": True,
        },
        headers=household_headers,
    )
    assert invalid_date.status_code == 422

    invalid_monthly = client.post(
        "/v1/goals",
        json={
            "name": "Ohne Rate",
            "target_amount_minor": 50000,
            "planning_mode": "monthly_amount",
            "include_in_planning": True,
        },
        headers=household_headers,
    )
    assert invalid_monthly.status_code == 422

    wish = client.post(
        "/v1/wishlist",
        json={"name": "Fahrrad", "price_minor": 120000, "priority": 2},
        headers=household_headers,
    ).json()
    started = client.post(
        f"/v1/wishlist/{wish['id']}/start-savings-plan",
        json={
            "planning_mode": "monthly_amount",
            "monthly_contribution_minor": 10000,
            "starting_amount_minor": 20000,
            "include_in_planning": True,
        },
        headers=household_headers,
    )
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["goal"]["name"] == "Fahrrad"
    assert body["goal"]["target_amount_minor"] == 120000
    assert body["goal"]["priority"] == 2
    assert body["wishlist_item"]["goal_id"] == body["goal"]["id"]
    assert client.get("/v1/wishlist", headers=household_headers).json()[0]["name"] == "Fahrrad"

    duplicate = client.post(
        f"/v1/wishlist/{wish['id']}/start-savings-plan",
        json={
            "planning_mode": "monthly_amount",
            "monthly_contribution_minor": 10000,
        },
        headers=household_headers,
    )
    assert duplicate.status_code == 409


def test_savings_goal_current_amount_derived_from_assigned_splits(
    household_headers: dict[str, str],
) -> None:
    # Architekturplan 6.7: Einzahlungen werden ueber zugeordnete
    # Transaktionssplits abgeleitet, nicht manuell gepflegt.
    goal = client.post(
        "/v1/goals",
        json={
            "name": "Urlaubskasse",
            "target_amount_minor": 200000,
            "starting_amount_minor": 10000,
        },
        headers=household_headers,
    ).json()

    account = client.post(
        "/v1/accounts",
        json={"name": "Sparkonto", "account_type": "savings"},
        headers=household_headers,
    ).json()
    tx = client.post(
        "/v1/transactions",
        json={"account_id": account["id"], "booked_date": "2026-07-01", "amount_minor": 5000},
        headers=household_headers,
    ).json()

    client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={"amount_minor": 5000, "splits": [{"amount_minor": 5000, "goal_id": goal["id"]}]},
        headers=household_headers,
    )

    goals = client.get("/v1/goals", headers=household_headers).json()
    updated = next(g for g in goals if g["id"] == goal["id"])
    assert updated["starting_amount_minor"] == 10000
    assert updated["current_amount_minor"] == 15000

    # PATCH ohne Aenderungen liefert denselben, ebenfalls abgeleiteten Stand.
    patched = client.patch(f"/v1/goals/{goal['id']}", json={}, headers=household_headers)
    assert patched.status_code == 200
    assert patched.json()["current_amount_minor"] == 15000


def test_savings_goal_status_is_derived_and_reversible(
    household_headers: dict[str, str],
) -> None:
    goal = client.post(
        "/v1/goals",
        json={
            "name": "Kurzfristiges Ziel",
            "target_amount_minor": 10000,
            "starting_amount_minor": 5000,
        },
        headers=household_headers,
    ).json()
    assert goal["status"] == "active"

    account = client.post(
        "/v1/accounts",
        json={"name": "Tagesgeld", "account_type": "savings"},
        headers=household_headers,
    ).json()
    client.post(
        "/v1/transactions",
        json={
            "account_id": account["id"],
            "booked_date": "2026-08-02",
            "amount_minor": 5000,
            "goal_id": goal["id"],
            "payee_label": "Monatliche Sparrate",
            "note": "August",
        },
        headers=household_headers,
    ).json()

    updated = next(
        item
        for item in client.get("/v1/goals", headers=household_headers).json()
        if item["id"] == goal["id"]
    )
    assert updated["current_amount_minor"] == 10000
    assert updated["status"] == "reached"
    assert updated["monthly_required_minor"] == 0

    raised = client.patch(
        f"/v1/goals/{goal['id']}",
        json={"target_amount_minor": 20000},
        headers=household_headers,
    )
    assert raised.status_code == 200
    assert raised.json()["status"] == "active"

    # Ein explizit archiviertes Ziel bleibt archiviert, bis es bewusst wieder
    # aktiviert wird.
    archived = client.patch(
        f"/v1/goals/{goal['id']}",
        json={"status": "cancelled"},
        headers=household_headers,
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "cancelled"

    restored = client.patch(
        f"/v1/goals/{goal['id']}",
        json={"status": "active"},
        headers=household_headers,
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"

    manual_reached = client.patch(
        f"/v1/goals/{goal['id']}",
        json={"status": "reached"},
        headers=household_headers,
    )
    assert manual_reached.status_code == 422


def test_wishlist_and_buy_traffic_light(household_headers: dict[str, str]) -> None:
    # 1. Wunschliste Item anlegen
    w_res = client.post(
        "/v1/wishlist",
        json={
            "name": "Neuer Gaming Laptop",
            "price_minor": 150000,
            "priority": 1,
            "note": "Fuer Entwicklungsarbeiten",
        },
        headers=household_headers,
    )
    assert w_res.status_code == 201
    w_data = w_res.json()
    assert "buy_traffic_light" in w_data
    assert w_data["status"] == "open"

    # 2. Wunschliste listen
    list_res = client.get("/v1/wishlist", headers=household_headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1

    # 3. Wunschliste-Eintrag aktualisieren (Priorität ändern)
    item_id = w_data["id"]
    u_res = client.patch(
        f"/v1/wishlist/{item_id}",
        json={"priority": 3},
        headers=household_headers,
    )
    assert u_res.status_code == 200
    assert u_res.json()["priority"] == 3

    # Ein Wunsch kann ohne Hard-Delete durch seinen Lebenszyklus bewegt und
    # spaeter wieder geoeffnet werden.
    for lifecycle_status in ("deferred", "purchased", "archived", "open"):
        status_res = client.patch(
            f"/v1/wishlist/{item_id}",
            json={"status": lifecycle_status},
            headers=household_headers,
        )
        assert status_res.status_code == 200
        assert status_res.json()["status"] == lifecycle_status

    invalid_status = client.patch(
        f"/v1/wishlist/{item_id}",
        json={"status": "unknown"},
        headers=household_headers,
    )
    assert invalid_status.status_code == 422


def test_traffic_light_counts_goal_balance_before_monthly_budget() -> None:
    # Gegenbeispiel aus dem Finding: 500 EUR Wunsch, 600 EUR bereits im
    # Sparziel gedeckt, 50 EUR freies Monatsbudget -> voll finanziert, gruen.
    covered = evaluate_traffic_light(50000, 5000, 60000)
    assert covered.status == "green"
    assert covered.coverage_ratio == 1.0
    assert covered.shortfall_minor == 0
    assert covered.earliest_purchase_date == date.today()

    # Ohne Sparzielguthaben bleibt derselbe Wunsch unfinanzierbar.
    uncovered = evaluate_traffic_light(50000, 5000, 0)
    assert uncovered.status == "red"
    assert uncovered.shortfall_minor == 40000  # 500 EUR - 100 EUR Mindestkapazitaet
    assert uncovered.coverage_ratio == 0.2
    assert uncovered.earliest_purchase_date is not None
    assert uncovered.earliest_purchase_date > date.today()

    # Teildeckung: Restluecke innerhalb von zwei Monatskapazitaeten -> gelb.
    partial = evaluate_traffic_light(50000, 10000, 20000)
    assert partial.status == "yellow"
    assert partial.shortfall_minor == 20000
    assert partial.coverage_ratio == 0.6


def test_wishlist_traffic_light_uses_linked_goal_and_goal_id_is_patchable(
    household_headers: dict[str, str],
) -> None:
    # Sparziel mit 600 EUR Guthaben (abgeleitet aus starting_amount_minor)
    goal = client.post(
        "/v1/goals",
        json={
            "name": "Technikruecklage",
            "target_amount_minor": 60000,
            "starting_amount_minor": 60000,
        },
        headers=household_headers,
    ).json()

    # 500 EUR Wunsch ohne Verknuepfung: nur die Mindestkapazitaet zaehlt
    item = client.post(
        "/v1/wishlist",
        json={"name": "Monitor", "price_minor": 50000, "priority": 1},
        headers=household_headers,
    ).json()
    assert item["buy_traffic_light"] == "red"
    assert item["shortfall_minor"] == 40000
    assert item["coverage_ratio"] < 1.0
    assert item["earliest_purchase_date"] > date.today().isoformat()

    # goal_id ist per PATCH aenderbar und schlaegt sofort auf die Ampel durch
    patched = client.patch(
        f"/v1/wishlist/{item['id']}",
        json={"goal_id": goal["id"]},
        headers=household_headers,
    )
    assert patched.status_code == 200
    p_data = patched.json()
    assert p_data["goal_id"] == goal["id"]
    assert p_data["buy_traffic_light"] == "green"
    assert p_data["coverage_ratio"] == 1.0
    assert p_data["shortfall_minor"] == 0
    assert p_data["earliest_purchase_date"] == date.today().isoformat()

    # Auch die Liste bewertet mit dem abgeleiteten Sparzielstand
    listed = next(
        w
        for w in client.get("/v1/wishlist", headers=household_headers).json()
        if w["id"] == item["id"]
    )
    assert listed["buy_traffic_light"] == "green"
    assert listed["shortfall_minor"] == 0

    # Verknuepfung wieder loesen -> Ampel faellt zurueck
    unlinked = client.patch(
        f"/v1/wishlist/{item['id']}",
        json={"goal_id": None},
        headers=household_headers,
    ).json()
    assert unlinked["goal_id"] is None
    assert unlinked["buy_traffic_light"] == "red"


def test_asset_valuation_history_is_kept_on_update(household_headers: dict[str, str]) -> None:
    asset = client.post(
        "/v1/assets",
        json={
            "name": "Depot",
            "type": "asset",
            "asset_class": "depot",
            "current_value_minor": 1000000,
        },
        headers=household_headers,
    ).json()

    # Startbewertung ist bereits historisiert
    initial = client.get(f"/v1/assets/{asset['id']}/valuations", headers=household_headers)
    assert initial.status_code == 200
    assert [v["value_minor"] for v in initial.json()] == [1000000]

    # Zwei Neubewertungen ueberschreiben den Wert, verlieren ihn aber nicht
    for new_value in (1200000, 900000):
        upd = client.patch(
            f"/v1/assets/{asset['id']}",
            json={"current_value_minor": new_value},
            headers=household_headers,
        )
        assert upd.status_code == 200
        assert upd.json()["current_value_minor"] == new_value

    history = client.get(f"/v1/assets/{asset['id']}/valuations", headers=household_headers).json()
    assert [v["value_minor"] for v in history] == [900000, 1200000, 1000000]
    assert all(v["asset_id"] == asset["id"] for v in history)
    assert all(v["valued_on"] == _database_today().isoformat() for v in history)

    # Ein PATCH ohne Wertaenderung kann auch die Klasse aendern und erzeugt
    # dabei keinen neuen Historienpunkt.
    metadata_update = client.patch(
        f"/v1/assets/{asset['id']}",
        json={"name": "Depot ETF", "asset_class": "cash"},
        headers=household_headers,
    )
    assert metadata_update.status_code == 200
    assert metadata_update.json()["name"] == "Depot ETF"
    assert metadata_update.json()["asset_class"] == "cash"
    unchanged = client.get(f"/v1/assets/{asset['id']}/valuations", headers=household_headers).json()
    assert len(unchanged) == 3

    # Unbekannte Position liefert 404 statt einer leeren Historie
    missing = client.get(f"/v1/assets/{uuid.uuid4()}/valuations", headers=household_headers)
    assert missing.status_code == 404


def test_assets_debts_payoff_and_net_worth_report(household_headers: dict[str, str]) -> None:
    # 1. Vermögenswert (Immobilie) anlegen
    a_res = client.post(
        "/v1/assets",
        json={
            "name": "Eigentumswohnung",
            "type": "asset",
            "asset_class": "real_estate",
            "current_value_minor": 35000000,
        },
        headers=household_headers,
    )
    assert a_res.status_code == 201

    # 2. Schulden (Baukredit) anlegen
    d_res = client.post(
        "/v1/assets",
        json={
            "name": "Immobilienkredit",
            "type": "debt",
            "asset_class": "loan",
            "current_value_minor": 20000000,
            "interest_rate_percent": 3.5,
            "monthly_payment_minor": 120000,
        },
        headers=household_headers,
    )
    assert d_res.status_code == 201
    d_data = d_res.json()
    assert d_data["payoff_months_projected"] is not None
    assert d_data["payoff_months_projected"] > 0

    # 2b. Direkt editierbare Schuldenwerte aktualisieren. Der verknuepfte
    # Legacy-Tilgungsplan muss im selben PATCH synchronisiert werden.
    initial_payoff = d_data["payoff_months_projected"]
    u_res = client.patch(
        f"/v1/assets/{d_data['id']}",
        json={
            "current_value_minor": 18000000,
            "interest_rate_percent": 4.25,
            "monthly_payment_minor": 200000,
        },
        headers=household_headers,
    )
    assert u_res.status_code == 200
    updated_debt = u_res.json()
    assert updated_debt["current_value_minor"] == 18000000
    assert updated_debt["payoff_months_projected"] != initial_payoff

    # Ein frisch mit denselben Werten angelegter Plan liefert dieselbe
    # Projektion; auch ein erneutes Listen darf nicht auf den alten Stand fallen.
    reference_debt = client.post(
        "/v1/assets",
        json={
            "name": "Referenzkredit",
            "type": "debt",
            "asset_class": "loan",
            "current_value_minor": 18000000,
            "interest_rate_percent": 4.25,
            "monthly_payment_minor": 200000,
        },
        headers=household_headers,
    ).json()
    assert updated_debt["payoff_months_projected"] == reference_debt["payoff_months_projected"]
    listed_debts = client.get("/v1/assets", headers=household_headers).json()
    listed_updated = next(item for item in listed_debts if item["id"] == d_data["id"])
    assert listed_updated["payoff_months_projected"] == updated_debt["payoff_months_projected"]

    # 3. Nettovermögensbericht
    nw_res = client.get("/v1/reports/net-worth", headers=household_headers)
    assert nw_res.status_code == 200
    nw_data = nw_res.json()
    assert nw_data["total_assets_minor"] >= 35000000
    assert nw_data["total_debts_minor"] >= 18000000
    assert (
        nw_data["net_worth_minor"] == nw_data["total_assets_minor"] - nw_data["total_debts_minor"]
    )

    # Der Legacy-Tilgungsplan darf das Loeschen seiner Schuld nicht per FK
    # blockieren; die Position verschwindet nach dem bestaetigten DELETE.
    delete_res = client.delete(f"/v1/assets/{d_data['id']}", headers=household_headers)
    assert delete_res.status_code == 204
    after_delete = client.get("/v1/assets", headers=household_headers).json()
    assert all(item["id"] != d_data["id"] for item in after_delete)
