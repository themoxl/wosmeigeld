"""Integrationstests der Liquiditaetsvorschau gegen das lokale Supabase -
kein Mocking (Muster: `test_reserves_api.py`), inklusive Haushaltsisolation.

Der Rechenkern selbst ist in `test_household_forecast_service.py` und
`test_household_forecast_reference_cases.py` centgenau geprueft. Hier geht es
um den Weg dorthin: welche Konten zaehlen, welcher Saldo gilt zum Stichtag,
welche Faelligkeit entfaellt zugunsten einer bereits vorgemerkten Buchung.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

TODAY = date.today()


def _headers(name: str) -> dict[str, str]:
    token = _signup(f"forecast-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    household = client.post(
        "/v1/households", json={"name": name}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def household_headers() -> dict[str, str]:
    return _headers("Vorschau")


def _create_account(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Girokonto",
        "account_type": "checking",
        "opening_balance_minor": 0,
    }
    body.update(overrides)
    response = client.post("/v1/accounts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _create_contract(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Miete",
        "amount_minor": 100_000,
        "frequency": "monthly",
        "next_due_date": (TODAY + timedelta(days=5)).isoformat(),
        "due_day": (TODAY + timedelta(days=5)).day,
    }
    body.update(overrides)
    response = client.post("/v1/contracts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _create_transaction(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"booked_date": TODAY.isoformat(), "amount_minor": -1_000}
    body.update(overrides)
    response = client.post("/v1/transactions", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _forecast(headers: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get("/v1/forecast", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Kontenauswahl und Startsaldo
# ---------------------------------------------------------------------------


def test_only_liquid_accounts_count_and_savings_can_be_switched_on(
    household_headers: dict[str, str],
) -> None:
    _create_account(household_headers, name="Girokonto", opening_balance_minor=92_000)
    _create_account(
        household_headers, name="Bargeld", account_type="cash", opening_balance_minor=18_000
    )
    _create_account(
        household_headers,
        name="Tagesgeld",
        account_type="savings",
        opening_balance_minor=840_000,
    )

    base = _forecast(household_headers, horizon_months=3, granularity="month")
    assert base["opening_liquidity_minor"] == 110_000
    assert base["quality"]["excluded_account_names"] == ["Tagesgeld"]

    with_savings = _forecast(
        household_headers, horizon_months=3, granularity="month", include_savings=True
    )
    assert with_savings["opening_liquidity_minor"] == 950_000
    assert with_savings["quality"]["excluded_account_names"] == []


def test_forecast_liquidity_overrides_the_account_type_heuristic(
    household_headers: dict[str, str],
) -> None:
    _create_account(household_headers, name="Girokonto", opening_balance_minor=50_000)
    # Ein Tagesgeldkonto, das der Nutzer ausdruecklich als liquide erklaert.
    _create_account(
        household_headers,
        name="Tagesgeld",
        account_type="savings",
        opening_balance_minor=200_000,
        forecast_liquidity=True,
    )
    # Ein Girokonto, das ausdruecklich NICHT zaehlen soll.
    excluded = _create_account(
        household_headers, name="Geschaeftskonto", opening_balance_minor=999_000
    )
    patched = client.patch(
        f"/v1/accounts/{excluded['id']}",
        json={"forecast_liquidity": False},
        headers=household_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["forecast_liquidity"] is False

    result = _forecast(household_headers, horizon_months=3, granularity="month")
    assert result["opening_liquidity_minor"] == 250_000
    assert result["quality"]["excluded_account_names"] == ["Geschaeftskonto"]


def test_future_dated_transaction_is_a_flow_and_not_part_of_the_opening_balance(
    household_headers: dict[str, str],
) -> None:
    """Der Startsaldo kommt aus der Stichtagsvariante - sonst zaehlte jede
    Vormerkung doppelt (kritische Regel 1)."""
    account = _create_account(household_headers, opening_balance_minor=100_000)
    _create_transaction(
        household_headers,
        account_id=account["id"],
        booked_date=(TODAY - timedelta(days=1)).isoformat(),
        amount_minor=-5_000,
        payee_label="Gestern gebucht",
    )
    _create_transaction(
        household_headers,
        account_id=account["id"],
        booked_date=(TODAY + timedelta(days=3)).isoformat(),
        amount_minor=-20_000,
        payee_label="Vorgemerkt",
    )

    result = _forecast(household_headers, horizon_months=3)
    # Nur die Buchung von gestern steckt im Startsaldo.
    assert result["opening_liquidity_minor"] == 95_000

    upcoming = client.get("/v1/forecast/upcoming?days=30", headers=household_headers)
    assert upcoming.status_code == 200, upcoming.text
    groups = upcoming.json()
    assert [group["on"] for group in groups] == [(TODAY + timedelta(days=3)).isoformat()]
    assert groups[0]["total_minor"] == -20_000
    assert groups[0]["flows"][0]["source_kind"] == "transaction"
    assert groups[0]["balance_after_minor"] == 75_000


# ---------------------------------------------------------------------------
# Doppelzaehlung
# ---------------------------------------------------------------------------


async def _link_occurrence(
    household_id: str, contract_id: str, due_date: date, transaction_id: str
) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "insert into contract_occurrences (household_id, contract_id, due_date, "
                "status, transaction_id, booked_date) values (:household_id, :contract_id, "
                ":due_date, 'matched', :transaction_id, :due_date)"
            ),
            {
                "household_id": household_id,
                "contract_id": contract_id,
                "due_date": due_date,
                "transaction_id": transaction_id,
            },
        )


async def test_matched_occurrence_replaces_the_contract_due_date_exactly_once(
    household_headers: dict[str, str],
) -> None:
    """Eine zukuenftig datierte Buchung, die einer Vertragsfaelligkeit
    zugeordnet ist, darf genau einmal wirken (kritische Regel 3)."""
    account = _create_account(household_headers, opening_balance_minor=500_000)
    due = TODAY + timedelta(days=5)
    contract = _create_contract(
        household_headers,
        name="Miete",
        amount_minor=100_000,
        next_due_date=due.isoformat(),
        due_day=due.day,
    )
    transaction = _create_transaction(
        household_headers,
        account_id=account["id"],
        booked_date=due.isoformat(),
        amount_minor=-100_000,
        payee_label="Miete Vormerkung",
    )

    before = client.get("/v1/forecast/upcoming?days=10", headers=household_headers).json()
    day_before = next(group for group in before if group["on"] == due.isoformat())
    # Ohne Zuordnung zaehlen beide - genau der Fehler, den die Zuordnung loest.
    assert day_before["total_minor"] == -200_000

    await _link_occurrence(
        household_headers["X-Household-Id"], contract["id"], due, transaction["id"]
    )

    after = client.get("/v1/forecast/upcoming?days=10", headers=household_headers).json()
    day_after = next(group for group in after if group["on"] == due.isoformat())
    assert day_after["total_minor"] == -100_000
    assert [flow["source_kind"] for flow in day_after["flows"]] == ["transaction"]


# ---------------------------------------------------------------------------
# Variabler Monatsansatz
# ---------------------------------------------------------------------------


def _category_id(headers: dict[str, str], name: str = "Lebensmittel") -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Default-Kategorie '{name}' nicht gefunden")


def _set_budget(headers: dict[str, str], month: date, category_id: str, planned: int) -> None:
    response = client.put(
        f"/v1/budgets/{month.year}/{month.month}/allocations",
        json={"allocations": [{"category_id": category_id, "planned_minor": planned}]},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def test_current_month_forecasts_only_the_unspent_remainder(
    household_headers: dict[str, str],
) -> None:
    """Was bis heute ausgegeben wurde, steckt schon im Startsaldo und darf
    nicht ein zweites Mal prognostiziert werden (kritische Regel 2)."""
    account = _create_account(household_headers, opening_balance_minor=1_000_000)
    category = _category_id(household_headers)
    month_start = date(TODAY.year, TODAY.month, 1)
    _set_budget(household_headers, month_start, category, 60_000)

    untouched = _forecast(household_headers, horizon_months=1, granularity="month")
    full_month_variable = untouched["monthly"][0]["variable_minor"]
    assert full_month_variable > 0

    _create_transaction(
        household_headers,
        account_id=account["id"],
        booked_date=month_start.isoformat(),
        amount_minor=-60_000,
        category_id=category,
        payee_label="Wocheneinkauf",
    )

    spent = _forecast(household_headers, horizon_months=1, granularity="month")
    assert spent["monthly"][0]["variable_minor"] == 0
    # Die bereits gebuchte Ausgabe steckt im Startsaldo, nicht in der Prognose.
    assert spent["opening_liquidity_minor"] == 940_000


def test_budget_plan_of_a_category_with_a_contract_is_not_counted_as_variable(
    household_headers: dict[str, str],
) -> None:
    """Sonst zaehlte derselbe Vertrag als Faelligkeit UND als variabler
    Monatsansatz."""
    _create_account(household_headers, opening_balance_minor=1_000_000)
    category = _category_id(household_headers)
    next_month = date(TODAY.year + (TODAY.month // 12), TODAY.month % 12 + 1, 1)
    _set_budget(household_headers, next_month, category, 60_000)

    before = _forecast(household_headers, horizon_months=2, granularity="month")
    assert before["monthly"][1]["variable_minor"] == 60_000

    due = TODAY + timedelta(days=5)
    _create_contract(
        household_headers,
        name="Lieferdienst-Abo",
        amount_minor=60_000,
        category_id=category,
        next_due_date=due.isoformat(),
        due_day=due.day,
    )
    after = _forecast(household_headers, horizon_months=2, granularity="month")
    assert after["monthly"][1]["variable_minor"] == 0


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


def test_settings_default_and_roundtrip(household_headers: dict[str, str]) -> None:
    default = client.get("/v1/forecast/settings", headers=household_headers)
    assert default.status_code == 200, default.text
    assert default.json() == {
        "safety_buffer_minor": 0,
        "variable_basis": "max",
        "variable_lookback_months": 6,
        "horizon_months": 24,
        # Die Push-Schalter sind vorgabeweise an (Stufe 5d): sie melden nur
        # Zustandswechsel, eine stumme Vorgabe waere hier die Ueberraschung.
        "notify_shortfall": True,
        "notify_below_buffer": True,
        "notify_income_missing": True,
        "updated_at": None,
    }

    stored = client.put(
        "/v1/forecast/settings",
        json={
            "safety_buffer_minor": 50_000,
            "variable_basis": "budget",
            "variable_lookback_months": 12,
            "horizon_months": 36,
        },
        headers=household_headers,
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["safety_buffer_minor"] == 50_000
    assert stored.json()["updated_at"] is not None

    again = client.get("/v1/forecast/settings", headers=household_headers).json()
    assert again["variable_basis"] == "budget"
    assert again["variable_lookback_months"] == 12
    assert again["horizon_months"] == 36

    # Der gespeicherte Horizont ist der Default der Vorschau.
    _create_account(household_headers, opening_balance_minor=1_000)
    assert _forecast(household_headers, granularity="month")["horizon_months"] == 36


def test_notify_switches_survive_the_roundtrip(household_headers: dict[str, str]) -> None:
    """Die drei Schalter steuern den Lauf notify_forecast_alerts (Stufe 5d).
    Wer sie nicht mitschickt, schaltet nichts ab - sonst loeschte ein Client,
    der nur den Puffer aendert, stillschweigend die Benachrichtigungen."""
    body = {
        "safety_buffer_minor": 10_000,
        "variable_basis": "max",
        "variable_lookback_months": 6,
        "horizon_months": 12,
    }
    ohne_schalter = client.put("/v1/forecast/settings", json=body, headers=household_headers)
    assert ohne_schalter.status_code == 200, ohne_schalter.text
    assert ohne_schalter.json()["notify_shortfall"] is True
    assert ohne_schalter.json()["notify_below_buffer"] is True
    assert ohne_schalter.json()["notify_income_missing"] is True

    abgeschaltet = client.put(
        "/v1/forecast/settings",
        json={**body, "notify_shortfall": False, "notify_income_missing": False},
        headers=household_headers,
    )
    assert abgeschaltet.status_code == 200, abgeschaltet.text

    again = client.get("/v1/forecast/settings", headers=household_headers).json()
    assert again["notify_shortfall"] is False
    assert again["notify_below_buffer"] is True
    assert again["notify_income_missing"] is False


def test_settings_reject_values_outside_the_allowed_range(
    household_headers: dict[str, str],
) -> None:
    response = client.put(
        "/v1/forecast/settings",
        json={
            "safety_buffer_minor": -1,
            "variable_basis": "max",
            "variable_lookback_months": 6,
            "horizon_months": 24,
        },
        headers=household_headers,
    )
    assert response.status_code == 422, response.text


def test_safety_buffer_drives_the_buffer_metrics(household_headers: dict[str, str]) -> None:
    _create_account(household_headers, opening_balance_minor=30_000)
    client.put(
        "/v1/forecast/settings",
        json={
            "safety_buffer_minor": 50_000,
            "variable_basis": "max",
            "variable_lookback_months": 6,
            "horizon_months": 3,
        },
        headers=household_headers,
    )
    result = _forecast(household_headers, granularity="month")
    assert result["safety_buffer_minor"] == 50_000
    assert result["metrics"]["first_below_buffer_on"] == TODAY.isoformat()
    assert result["metrics"]["needed_for_buffer_minor"] == 20_000
    assert result["metrics"]["first_negative_on"] is None
    assert {warning["code"] for warning in result["warnings"]} >= {"below_buffer"}


# ---------------------------------------------------------------------------
# Warnungen
# ---------------------------------------------------------------------------


def test_household_without_income_is_flagged(household_headers: dict[str, str]) -> None:
    _create_account(household_headers, opening_balance_minor=100_000)
    _create_contract(household_headers)
    result = _forecast(household_headers, horizon_months=3, granularity="month")
    codes = {warning["code"] for warning in result["warnings"]}
    assert "no_income_sources" in codes
    assert result["quality"]["level"] == "insufficient"
    assert result["quality"]["income_count"] == 0
    assert result["quality"]["contract_count"] == 1


def test_income_contract_counts_as_an_inflow(household_headers: dict[str, str]) -> None:
    _create_account(household_headers, opening_balance_minor=0)
    due = TODAY + timedelta(days=3)
    _create_contract(
        household_headers,
        name="Gehalt",
        direction="income",
        contract_type="income",
        amount_minor=300_000,
        next_due_date=due.isoformat(),
        due_day=due.day,
    )
    result = _forecast(household_headers, horizon_months=3, granularity="month")
    assert result["metrics"]["income_monthly_minor"] == 300_000
    assert result["quality"]["income_count"] == 1
    assert "no_income_sources" not in {warning["code"] for warning in result["warnings"]}
    assert result["monthly"][0]["income_minor"] == 300_000


def test_contract_ending_in_the_horizon_is_reported(
    household_headers: dict[str, str],
) -> None:
    _create_account(household_headers, opening_balance_minor=100_000)
    _create_contract(
        household_headers, name="Befristet", ends_on=(TODAY + timedelta(days=40)).isoformat()
    )
    result = _forecast(household_headers, horizon_months=6, granularity="month")
    assert result["quality"]["contracts_ending_in_horizon"] == 1
    assert "horizon_beyond_contracts" in {warning["code"] for warning in result["warnings"]}


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def _simulate(headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/forecast/simulate", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_simulation_without_changes_equals_the_base(
    household_headers: dict[str, str],
) -> None:
    """Leitsatz 2 auch ueber die API: gleiche Zahlen, Feld fuer Feld."""
    _create_account(household_headers, opening_balance_minor=100_000)
    due = TODAY + timedelta(days=3)
    _create_contract(
        household_headers,
        name="Gehalt",
        direction="income",
        contract_type="income",
        amount_minor=300_000,
        next_due_date=due.isoformat(),
        due_day=due.day,
    )
    payload = _simulate(household_headers, {"horizon_months": 6, "changes": []})
    assert payload["base"] == payload["simulation"]
    assert payload["contributions"] == []
    assert payload["comparison"]["surplus_delta_minor"] == 0
    assert payload["comparison"]["balance_delta_24_months_minor"] == 0
    assert payload["comparison"]["resolves_shortfall"] is False


def test_simulation_reports_contribution_per_change(
    household_headers: dict[str, str],
) -> None:
    _create_account(household_headers, opening_balance_minor=0)
    # Das Gehalt kommt vor der Miete - eine Gehaltserhoehung kann den Engpass
    # deshalb ueberhaupt beseitigen; die Betreuungskosten koennen es nicht.
    salary_due = TODAY + timedelta(days=5)
    salary = _create_contract(
        household_headers,
        name="Gehalt Person A",
        direction="income",
        contract_type="income",
        amount_minor=200_000,
        next_due_date=salary_due.isoformat(),
        due_day=salary_due.day,
    )
    rent_due = TODAY + timedelta(days=20)
    _create_contract(
        household_headers,
        name="Miete",
        amount_minor=250_000,
        next_due_date=rent_due.isoformat(),
        due_day=rent_due.day,
    )

    base = _forecast(household_headers, horizon_months=6, granularity="month")
    assert base["metrics"]["first_negative_on"] is not None

    payload = _simulate(
        household_headers,
        {
            "horizon_months": 6,
            "changes": [
                {
                    "change_type": "income_change",
                    "target_contract_id": salary["id"],
                    "label": "Gehalt Person A",
                    "amount_mode": "delta",
                    "amount_minor": 200_000,
                    "frequency": "monthly",
                    "starts_on": TODAY.isoformat(),
                },
                {
                    "change_type": "expense_add",
                    "label": "Kinderbetreuung",
                    "amount_mode": "absolute",
                    "amount_minor": -28_000,
                    "frequency": "monthly",
                    "due_day": 1,
                    "starts_on": TODAY.isoformat(),
                },
            ],
        },
    )

    assert payload["comparison"]["surplus_delta_minor"] == 172_000
    assert payload["simulation"]["metrics"]["income_monthly_minor"] == 400_000
    contributions = payload["contributions"]
    assert [item["change_index"] for item in contributions] == [0, 1]
    assert contributions[0]["monthly_minor"] == 200_000
    assert contributions[0]["resolves_shortfall"] is True
    assert contributions[1]["monthly_minor"] == -28_000
    assert contributions[1]["resolves_shortfall"] is False
    # Eine neue wiederkehrende Ausgabe hebt den variablen Ansatz nicht.
    assert (
        payload["simulation"]["metrics"]["variable_monthly_minor"]
        == payload["base"]["metrics"]["variable_monthly_minor"]
    )


def _create_person(headers: dict[str, str], name: str) -> str:
    response = client.post("/v1/people", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _salary_change(**overrides: Any) -> dict[str, Any]:
    change: dict[str, Any] = {
        "change_type": "income_add",
        "label": "Person B, Teilzeit 25 h",
        "amount_mode": "absolute",
        "amount_minor": 145_000,
        "frequency": "monthly",
        "due_day": 15,
        "income_kind": "salary",
        "starts_on": TODAY.isoformat(),
    }
    change.update(overrides)
    return change


def _hint_codes(payload: dict[str, Any]) -> set[str]:
    return {warning["code"] for warning in payload["simulation"]["warnings"]}


def test_simulation_hints_at_the_tax_class_when_a_second_salary_joins(
    household_headers: dict[str, str],
) -> None:
    """Seit Stufe 6 kennt `contracts` Art und Person - der Hinweis darf sich
    deshalb nicht mehr mit "irgendeine Einnahme existiert" begnuegen."""
    _create_account(household_headers, opening_balance_minor=100_000)
    due = TODAY + timedelta(days=3)
    person_a_id = _create_person(household_headers, "Person A")
    _create_contract(
        household_headers,
        name="Gehalt Person A",
        direction="income",
        contract_type="income",
        amount_minor=300_000,
        next_due_date=due.isoformat(),
        due_day=due.day,
        income_kind="salary",
        person_id=person_a_id,
    )
    payload = _simulate(
        household_headers, {"horizon_months": 6, "changes": [_salary_change()]}
    )
    assert "simulation_tax_class_hint" in _hint_codes(payload)
    assert "simulation_tax_class_hint" not in {
        warning["code"] for warning in payload["base"]["warnings"]
    }

    # Dieselbe Person, die schon ein Gehalt hat: ein Zweitjob aendert keine
    # Steuerklassenkombination.
    same_person = _simulate(
        household_headers,
        {"horizon_months": 6, "changes": [_salary_change(person_id=person_a_id)]},
    )
    assert "simulation_tax_class_hint" not in _hint_codes(same_person)


def test_simulation_stays_silent_when_the_existing_income_is_not_a_salary(
    household_headers: dict[str, str],
) -> None:
    """Neben einer Miete oder einer Rente aendert ein hinzukommendes Gehalt
    keine Steuerklassenkombination - genau hier lag die alte Fassung falsch."""
    _create_account(household_headers, opening_balance_minor=100_000)
    due = TODAY + timedelta(days=3)
    _create_contract(
        household_headers,
        name="Mieteinnahme",
        direction="income",
        contract_type="income",
        amount_minor=80_000,
        next_due_date=due.isoformat(),
        due_day=due.day,
        income_kind="rental",
    )
    payload = _simulate(
        household_headers, {"horizon_months": 6, "changes": [_salary_change()]}
    )
    assert "simulation_tax_class_hint" not in _hint_codes(payload)


def test_simulation_hints_when_the_existing_salary_has_no_person(
    household_headers: dict[str, str],
) -> None:
    """Ein ungepflegtes `person_id` heisst nicht "dieselbe Person" - im
    Zweifel ist der Hinweis die harmlosere Antwort."""
    _create_account(household_headers, opening_balance_minor=100_000)
    due = TODAY + timedelta(days=3)
    _create_contract(
        household_headers,
        name="Gehalt",
        direction="income",
        contract_type="income",
        amount_minor=300_000,
        next_due_date=due.isoformat(),
        due_day=due.day,
        income_kind="salary",
    )
    payload = _simulate(
        household_headers,
        {
            "horizon_months": 6,
            "changes": [_salary_change(person_id=_create_person(household_headers, "Person B"))],
        },
    )
    assert "simulation_tax_class_hint" in _hint_codes(payload)


def test_simulation_with_unknown_target_contract_is_rejected(
    household_headers: dict[str, str],
) -> None:
    _create_account(household_headers, opening_balance_minor=100_000)
    response = client.post(
        "/v1/forecast/simulate",
        json={
            "changes": [
                {
                    "change_type": "expense_end",
                    "target_contract_id": str(uuid.uuid4()),
                    "label": "Gibt es nicht",
                    "starts_on": TODAY.isoformat(),
                }
            ]
        },
        headers=household_headers,
    )
    assert response.status_code == 400, response.text


def test_simulation_rejects_a_contradictory_change(
    household_headers: dict[str, str],
) -> None:
    response = client.post(
        "/v1/forecast/simulate",
        json={
            "changes": [
                {
                    "change_type": "income_add",
                    "label": "Ohne Art",
                    "amount_mode": "absolute",
                    "amount_minor": 100_000,
                    "frequency": "monthly",
                    "starts_on": TODAY.isoformat(),
                }
            ]
        },
        headers=household_headers,
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Grenzen und Isolation
# ---------------------------------------------------------------------------


def test_horizon_and_day_limits_are_enforced(household_headers: dict[str, str]) -> None:
    assert client.get("/v1/forecast?horizon_months=0", headers=household_headers).status_code == 422
    assert (
        client.get("/v1/forecast?horizon_months=61", headers=household_headers).status_code == 422
    )
    assert client.get("/v1/forecast/upcoming?days=0", headers=household_headers).status_code == 422
    assert (
        client.get("/v1/forecast/upcoming?days=366", headers=household_headers).status_code == 422
    )


def test_forecast_is_isolated_between_households() -> None:
    first = _headers("Vorschau A")
    second = _headers("Vorschau B")

    _create_account(first, opening_balance_minor=777_000)
    due = TODAY + timedelta(days=2)
    _create_contract(
        first,
        name="Gehalt A",
        direction="income",
        contract_type="income",
        amount_minor=500_000,
        next_due_date=due.isoformat(),
        due_day=due.day,
    )

    own = _forecast(first, horizon_months=3, granularity="month")
    assert own["opening_liquidity_minor"] == 777_000

    foreign = _forecast(second, horizon_months=3, granularity="month")
    assert foreign["opening_liquidity_minor"] == 0
    assert foreign["quality"]["contract_count"] == 0
    assert client.get("/v1/forecast/upcoming?days=30", headers=second).json() == []

    # Fremder Haushaltskontext mit eigenem Token: an der Mitgliedschaftspruefung
    # gescheitert, nicht erst an RLS.
    crossed = client.get(
        "/v1/forecast",
        headers={
            "Authorization": second["Authorization"],
            "X-Household-Id": first["X-Household-Id"],
        },
    )
    assert crossed.status_code == 403


# ---------------------------------------------------------------------------
# Vertragsgebundene Buchungen und die variable Basis
#
# Die Abgrenzung "variabel" lief urspruenglich allein ueber die KATEGORIE des
# Vertrags. Weicht die Kategorie der BUCHUNG davon ab - etwa eine
# Krankenversicherungspraemie, die als "Arzt & Zahnarzt" kategorisiert wurde -,
# zaehlte dieselbe Zahlung doppelt: als Vertragsfaelligkeit UND als variabler
# Verbrauch - im Monat ein vierstelliger Betrag.
# ---------------------------------------------------------------------------




async def test_contract_payment_in_a_different_category_is_not_counted_as_variable(
    household_headers: dict[str, str],
) -> None:
    """Praemie in abweichender Kategorie - der eigentliche Regressionsschutz."""
    account = _create_account(household_headers)
    versicherung = _category_id(household_headers, "Kranken- & Pflegeversicherung")
    arzt = _category_id(household_headers, "Arzt & Zahnarzt")

    # Vertrag traegt die Versicherungskategorie ...
    contract = _create_contract(
        household_headers,
        name="Krankenversicherung",
        amount_minor=150_000,
        category_id=versicherung,
        account_id=account["id"],
    )

    letzter_monat = date(TODAY.year, TODAY.month, 1) - timedelta(days=15)
    # ... die tatsaechliche Abbuchung aber die Arztkategorie.
    praemie = _create_transaction(
        household_headers,
        account_id=account["id"],
        booked_date=letzter_monat.isoformat(),
        amount_minor=-150_000,
        category_id=arzt,
    )
    await _link_occurrence(
        household_headers["X-Household-Id"], contract["id"], letzter_monat, praemie["id"]
    )

    # Dazu eine tatsaechliche Arztrechnung in derselben Kategorie.
    _create_transaction(
        household_headers,
        account_id=account["id"],
        booked_date=letzter_monat.isoformat(),
        amount_minor=-20_000,
        category_id=arzt,
    )

    payload = _forecast(household_headers)
    variabel = payload["metrics"]["variable_monthly_minor"]
    fix = payload["metrics"]["fixed_monthly_minor"]

    # Die Praemie steckt in den Fixkosten, nicht in der variablen Basis.
    assert fix >= 150_000
    # Genau die tatsaechliche Arztrechnung, verteilt auf den Rueckblick von sechs
    # Monaten - keine Schranke, sondern der exakte Sollwert. Ohne den
    # Occurrence-Filter stuenden hier (150_000 + 20_000) / 6 = 28_333.
    assert variabel == round(20_000 / 6), (
        "Vertragszahlung zaehlt doppelt: sie steht in den Fixkosten UND in der "
        f"variablen Basis ({variabel})"
    )


def test_real_variable_spending_in_a_covered_category_stays_excluded(
    household_headers: dict[str, str],
) -> None:
    """Der neue Filter ersetzt den alten nicht, er ergaenzt ihn."""
    account = _create_account(household_headers)
    versicherung = _category_id(household_headers, "Kranken- & Pflegeversicherung")
    vertrag = _create_contract(
        household_headers,
        name="Krankenversicherung",
        amount_minor=150_000,
        category_id=versicherung,
        account_id=account["id"],
    )
    assert vertrag["category_id"] == versicherung, vertrag
    assert vertrag["status"] == "active", vertrag

    letzter_monat = date(TODAY.year, TODAY.month, 1) - timedelta(days=15)
    # Ausgabe in der gedeckten Kategorie, aber OHNE Vertragsbindung.
    _create_transaction(
        household_headers,
        account_id=account["id"],
        booked_date=letzter_monat.isoformat(),
        amount_minor=-5_000,
        category_id=versicherung,
    )

    payload = _forecast(household_headers)
    assert payload["metrics"]["variable_monthly_minor"] == 0


async def test_payment_of_a_non_counting_contract_stays_variable(
    household_headers: dict[str, str],
) -> None:
    """Ein beendeter Vertrag erzeugt keine Faelligkeit - seine Buchung muss
    deshalb weiter variabel zaehlen, sonst fehlt sie in beiden Rechnungen."""
    account = _create_account(household_headers)
    arzt = _category_id(household_headers, "Arzt & Zahnarzt")
    contract = _create_contract(
        household_headers,
        name="Beendetes Abo",
        amount_minor=3_000,
        category_id=_category_id(household_headers, "Streaming & digitale Medien"),
        account_id=account["id"],
    )
    response = client.patch(
        f"/v1/contracts/{contract['id']}",
        json={"status": "ended"},
        headers={**household_headers, "If-Match": str(contract["version"])},
    )
    assert response.status_code == 200, response.text

    letzter_monat = date(TODAY.year, TODAY.month, 1) - timedelta(days=15)
    zahlung = _create_transaction(
        household_headers,
        account_id=account["id"],
        booked_date=letzter_monat.isoformat(),
        amount_minor=-3_000,
        category_id=arzt,
    )
    await _link_occurrence(
        household_headers["X-Household-Id"], contract["id"], letzter_monat, zahlung["id"]
    )

    payload = _forecast(household_headers)
    assert payload["metrics"]["variable_monthly_minor"] > 0
