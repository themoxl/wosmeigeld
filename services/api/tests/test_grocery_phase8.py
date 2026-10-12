"""Phase 8 (Lebensmittelplanung): Essensplan, Einkaufsliste, Wochenbudget.
Abnahmekriterium laut Architekturplan Abschnitt 17: "Plan- und Istkosten
werden getrennt ausgewiesen"; "keine erfundene Artikelaufteilung aus einem
Bankumsatz"."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p8-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Phase-8-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: dict[str, str]) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return str(account["id"])


@pytest.fixture()
def meal_week_id(household_headers: dict[str, str]) -> str:
    week = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-08-03", "budget_minor": 10000},
        headers=household_headers,
    ).json()
    return str(week["id"])


def test_meal_week_and_meal_crud(household_headers: dict[str, str], meal_week_id: str) -> None:
    m_res = client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-03",
            "meal_type": "dinner",
            "dish_name": "Spaghetti Bolognese",
            "servings": 4,
            "estimated_cost_minor": 1200,
        },
        headers=household_headers,
    )
    assert m_res.status_code == 201
    meal = m_res.json()
    assert meal["dish_name"] == "Spaghetti Bolognese"

    list_res = client.get(
        f"/v1/meals?meal_week_id={meal_week_id}", headers=household_headers
    )
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1

    u_res = client.patch(
        f"/v1/meals/{meal['id']}", json={"servings": 6}, headers=household_headers
    )
    assert u_res.status_code == 200
    assert u_res.json()["servings"] == 6


def test_shopping_list_and_items_crud(
    household_headers: dict[str, str], meal_week_id: str
) -> None:
    sl_res = client.post(
        "/v1/shopping-lists",
        json={"meal_week_id": meal_week_id, "store": "Supermarkt"},
        headers=household_headers,
    )
    assert sl_res.status_code == 201
    shopping_list = sl_res.json()
    assert shopping_list["status"] == "planning"
    assert shopping_list["planned_total_minor"] == 0
    assert shopping_list["actual_total_minor"] == 0

    item_res = client.post(
        "/v1/shopping-items",
        json={
            "shopping_list_id": shopping_list["id"],
            "name": "Nudeln",
            "quantity": "2",
            "unit": "Pkg",
            "planned_price_minor": 300,
        },
        headers=household_headers,
    )
    assert item_res.status_code == 201
    item = item_res.json()
    assert item["is_purchased"] is False

    toggle_res = client.patch(
        f"/v1/shopping-items/{item['id']}", json={"is_purchased": True}, headers=household_headers
    )
    assert toggle_res.status_code == 200
    assert toggle_res.json()["is_purchased"] is True

    refreshed_list = client.get(
        f"/v1/shopping-lists?meal_week_id={meal_week_id}", headers=household_headers
    ).json()[0]
    assert refreshed_list["planned_total_minor"] == 300


def test_purchase_link_separates_plan_and_actual_costs(
    household_headers: dict[str, str], meal_week_id: str, account_id: str
) -> None:
    shopping_list = client.post(
        "/v1/shopping-lists",
        json={"meal_week_id": meal_week_id, "store": "Supermarkt"},
        headers=household_headers,
    ).json()
    client.post(
        "/v1/shopping-items",
        json={
            "shopping_list_id": shopping_list["id"],
            "name": "Nudeln",
            "planned_price_minor": 300,
        },
        headers=household_headers,
    )
    client.post(
        "/v1/shopping-items",
        json={
            "shopping_list_id": shopping_list["id"],
            "name": "Tomatensauce",
            "planned_price_minor": 250,
        },
        headers=household_headers,
    )

    tx = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-08-03",
            "amount_minor": -4550,
            "payee_label": "Supermarkt",
        },
        headers=household_headers,
    ).json()

    link_res = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/purchase-links",
        json={"transaction_id": tx["id"]},
        headers=household_headers,
    )
    assert link_res.status_code == 201
    link = link_res.json()
    assert link["transaction"]["amount_minor"] == -4550

    refreshed = client.get(
        f"/v1/shopping-lists?meal_week_id={meal_week_id}", headers=household_headers
    ).json()[0]
    # Planpreis kommt ausschliesslich aus den Artikeln, Istpreis ausschliesslich
    # aus der verknuepften Gesamttransaktion - keine wechselseitige Verrechnung.
    # Die Istsumme ist normalisiert (positiv = Ausgabe) und damit mit den
    # Plangroessen vergleichbar; der rohe Buchungsbetrag bleibt negativ.
    assert refreshed["planned_total_minor"] == 550
    assert refreshed["actual_total_minor"] == 4550
    assert refreshed["linked_transactions"][0]["amount_minor"] == -4550
    assert len(refreshed["linked_transactions"]) == 1

    # Doppelte Verknuepfung derselben Transaktion ist nicht erlaubt.
    conflict_res = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/purchase-links",
        json={"transaction_id": tx["id"]},
        headers=household_headers,
    )
    assert conflict_res.status_code == 409

    unlink_res = client.delete(
        f"/v1/purchase-links/{link['id']}", headers=household_headers
    )
    assert unlink_res.status_code == 204

    after_unlink = client.get(
        f"/v1/shopping-lists?meal_week_id={meal_week_id}", headers=household_headers
    ).json()[0]
    assert after_unlink["actual_total_minor"] == 0
    assert after_unlink["linked_transactions"] == []


def test_deleting_linked_transaction_removes_purchase_link(
    household_headers: dict[str, str], meal_week_id: str, account_id: str
) -> None:
    shopping_list = client.post(
        "/v1/shopping-lists",
        json={"meal_week_id": meal_week_id},
        headers=household_headers,
    ).json()
    tx = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-08-04", "amount_minor": -2000},
        headers=household_headers,
    ).json()
    client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/purchase-links",
        json={"transaction_id": tx["id"]},
        headers=household_headers,
    )

    delete_res = client.delete(f"/v1/transactions/{tx['id']}", headers=household_headers)
    assert delete_res.status_code == 204

    refreshed = client.get(
        f"/v1/shopping-lists?meal_week_id={meal_week_id}", headers=household_headers
    ).json()[0]
    assert refreshed["actual_total_minor"] == 0
    assert refreshed["linked_transactions"] == []


def test_meal_week_summary_reports_plan_and_actual_separately(
    household_headers: dict[str, str], meal_week_id: str, account_id: str
) -> None:
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-03",
            "meal_type": "dinner",
            "dish_name": "Spaghetti Bolognese",
            "estimated_cost_minor": 1200,
        },
        headers=household_headers,
    )
    shopping_list = client.post(
        "/v1/shopping-lists",
        json={"meal_week_id": meal_week_id},
        headers=household_headers,
    ).json()
    client.post(
        "/v1/shopping-items",
        json={
            "shopping_list_id": shopping_list["id"],
            "name": "Nudeln",
            "planned_price_minor": 300,
        },
        headers=household_headers,
    )
    tx = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-08-03", "amount_minor": -4550},
        headers=household_headers,
    ).json()
    client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/purchase-links",
        json={"transaction_id": tx["id"]},
        headers=household_headers,
    )

    summary = client.get(
        f"/v1/meal-weeks/{meal_week_id}/summary", headers=household_headers
    ).json()
    assert summary["budget_minor"] == 10000
    assert summary["meals_estimated_minor"] == 1200
    assert summary["items_planned_minor"] == 300
    # Ausgabe von -4550 ergibt normalisierte Istkosten von 4550; der Restbetrag
    # SINKT dadurch auf 5450 (Regression: er wuchs frueher auf 14550).
    assert summary["actual_minor"] == 4550
    assert summary["remaining_vs_budget_minor"] == 5450


def _link_transaction(
    household_headers: dict[str, str],
    shopping_list_id: str,
    account_id: str,
    amount_minor: int,
) -> None:
    """Legt eine Buchung an und verknuepft sie mit der Einkaufsliste."""
    tx = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-08-03",
            "amount_minor": amount_minor,
        },
        headers=household_headers,
    ).json()
    link_res = client.post(
        f"/v1/shopping-lists/{shopping_list_id}/purchase-links",
        json={"transaction_id": tx["id"]},
        headers=household_headers,
    )
    assert link_res.status_code == 201


def test_meal_week_summary_reports_budget_overrun_as_negative_remaining(
    household_headers: dict[str, str], meal_week_id: str, account_id: str
) -> None:
    """Ueberschreitung: Ist > Budget muss einen negativen Restbetrag liefern."""
    shopping_list = client.post(
        "/v1/shopping-lists",
        json={"meal_week_id": meal_week_id},
        headers=household_headers,
    ).json()
    _link_transaction(household_headers, shopping_list["id"], account_id, -12500)

    summary = client.get(
        f"/v1/meal-weeks/{meal_week_id}/summary", headers=household_headers
    ).json()
    assert summary["budget_minor"] == 10000
    assert summary["actual_minor"] == 12500
    assert summary["remaining_vs_budget_minor"] == -2500


def test_refund_reduces_actual_costs(
    household_headers: dict[str, str], meal_week_id: str, account_id: str
) -> None:
    """Eine positive Buchung (Retoure/Pfandgutschrift) mindert die Istkosten."""
    shopping_list = client.post(
        "/v1/shopping-lists",
        json={"meal_week_id": meal_week_id},
        headers=household_headers,
    ).json()
    _link_transaction(household_headers, shopping_list["id"], account_id, -4550)
    _link_transaction(household_headers, shopping_list["id"], account_id, 550)

    refreshed = client.get(
        f"/v1/shopping-lists?meal_week_id={meal_week_id}", headers=household_headers
    ).json()[0]
    assert refreshed["actual_total_minor"] == 4000
    assert len(refreshed["linked_transactions"]) == 2

    summary = client.get(
        f"/v1/meal-weeks/{meal_week_id}/summary", headers=household_headers
    ).json()
    assert summary["actual_minor"] == 4000
    assert summary["remaining_vs_budget_minor"] == 6000


# --- Gerichte-Katalog ---
def test_dish_catalog_crud_and_reuse_across_weeks(household_headers: dict[str, str]) -> None:
    """Ein Gericht wird einmal angelegt und danach in mehreren Wochen geplant -
    genau der Zweck des Katalogs."""
    d_res = client.post(
        "/v1/dishes",
        json={
            "name": "Chili con Carne",
            "default_meal_type": "dinner",
            "default_servings": 4,
            "estimated_cost_minor": 950,
            "category": "Hauptgericht",
        },
        headers=household_headers,
    )
    assert d_res.status_code == 201
    dish = d_res.json()
    assert dish["default_servings"] == 4
    assert dish["usage_count"] == 0

    week_a = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-08-03", "budget_minor": 10000},
        headers=household_headers,
    ).json()
    week_b = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-08-10", "budget_minor": 10000},
        headers=household_headers,
    ).json()

    for week, meal_date in ((week_a, "2026-08-05"), (week_b, "2026-08-12")):
        meal_res = client.post(
            "/v1/meals",
            json={
                "meal_week_id": week["id"],
                "meal_date": meal_date,
                "meal_type": dish["default_meal_type"],
                "dish_name": dish["name"],
                "dish_id": dish["id"],
                "servings": dish["default_servings"],
                "estimated_cost_minor": dish["estimated_cost_minor"],
            },
            headers=household_headers,
        )
        assert meal_res.status_code == 201
        assert meal_res.json()["dish_id"] == dish["id"]

    catalog = client.get("/v1/dishes", headers=household_headers).json()
    assert len(catalog) == 1
    assert catalog[0]["usage_count"] == 2

    patched = client.patch(
        f"/v1/dishes/{dish['id']}", json={"estimated_cost_minor": 1100}, headers=household_headers
    )
    assert patched.status_code == 200
    assert patched.json()["estimated_cost_minor"] == 1100
    assert patched.json()["usage_count"] == 2


def test_dish_name_is_unique_per_household_case_insensitively(
    household_headers: dict[str, str],
) -> None:
    first = client.post(
        "/v1/dishes", json={"name": "Kartoffelsuppe"}, headers=household_headers
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/v1/dishes", json={"name": "kartoffelSUPPE"}, headers=household_headers
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "dish_name_already_exists"


def test_deleting_dish_keeps_already_planned_meals(
    household_headers: dict[str, str], meal_week_id: str
) -> None:
    """Der Katalog ist Vorlage, nicht Quelle der Wahrheit: ein geloeschtes
    Gericht darf einen bereits geplanten Wochenplan nicht veraendern."""
    dish = client.post(
        "/v1/dishes", json={"name": "Gemuesepfanne", "estimated_cost_minor": 700},
        headers=household_headers,
    ).json()
    meal = client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-03",
            "meal_type": "lunch",
            "dish_name": "Gemuesepfanne",
            "dish_id": dish["id"],
            "estimated_cost_minor": 700,
        },
        headers=household_headers,
    ).json()

    assert client.delete(f"/v1/dishes/{dish['id']}", headers=household_headers).status_code == 204

    meals = client.get(f"/v1/meals?meal_week_id={meal_week_id}", headers=household_headers).json()
    assert len(meals) == 1
    assert meals[0]["id"] == meal["id"]
    assert meals[0]["dish_name"] == "Gemuesepfanne"
    assert meals[0]["estimated_cost_minor"] == 700
    assert meals[0]["dish_id"] is None


def test_meal_rejects_dish_from_other_household(household_headers: dict[str, str]) -> None:
    other_token = _signup(f"p8-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-2")
    other_household = client.post(
        "/v1/households", json={"name": "Fremdhaushalt"}, headers=_auth_headers(other_token)
    ).json()
    other_headers = {**_auth_headers(other_token), "X-Household-Id": other_household["id"]}
    foreign_dish = client.post(
        "/v1/dishes", json={"name": "Fremdes Gericht"}, headers=other_headers
    ).json()

    week = client.post(
        "/v1/meal-weeks", json={"week_start": "2026-09-07"}, headers=household_headers
    ).json()
    res = client.post(
        "/v1/meals",
        json={
            "meal_week_id": week["id"],
            "meal_date": "2026-09-07",
            "meal_type": "dinner",
            "dish_name": "Fremdes Gericht",
            "dish_id": foreign_dish["id"],
        },
        headers=household_headers,
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "dish_not_found"

    assert client.get("/v1/dishes", headers=household_headers).json() == []


# --- Zutaten und Einkaufsliste aus dem Wochenplan ---
def _dish_with_ingredients(
    household_headers: dict[str, str],
    name: str,
    default_servings: int,
    ingredients: list[dict[str, object]],
) -> dict[str, object]:
    dish = client.post(
        "/v1/dishes",
        json={"name": name, "default_servings": default_servings},
        headers=household_headers,
    ).json()
    for ingredient in ingredients:
        res = client.post(
            "/v1/dish-ingredients",
            json={"dish_id": dish["id"], **ingredient},
            headers=household_headers,
        )
        assert res.status_code == 201
    return dish


def test_dish_ingredients_crud_and_count(household_headers: dict[str, str]) -> None:
    dish = _dish_with_ingredients(
        household_headers,
        "Spaghetti Bolognese",
        4,
        [
            {"name": "Nudeln", "quantity": "500", "unit": "g", "category": "Trockenware"},
            {"name": "Tomatensauce", "quantity": "400", "unit": "g"},
            {"name": "Salz"},  # ohne Menge - "Kleinkram" gehoert trotzdem drauf
        ],
    )

    ingredients = client.get(
        f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers
    ).json()
    assert [i["name"] for i in ingredients] == ["Nudeln", "Tomatensauce", "Salz"]
    assert ingredients[2]["quantity"] is None

    catalog = client.get("/v1/dishes", headers=household_headers).json()
    assert catalog[0]["ingredient_count"] == 3

    patched = client.patch(
        f"/v1/dish-ingredients/{ingredients[0]['id']}",
        json={"quantity": "600"},
        headers=household_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["quantity"] == "600.00"

    assert (
        client.delete(
            f"/v1/dish-ingredients/{ingredients[2]['id']}", headers=household_headers
        ).status_code
        == 204
    )
    assert client.get("/v1/dishes", headers=household_headers).json()[0]["ingredient_count"] == 2


def test_fill_shopping_list_scales_and_merges_ingredients(
    household_headers: dict[str, str], meal_week_id: str
) -> None:
    """Der eigentliche Zweck der Rezeptliste: aus dem Essensplan wird die
    Einkaufsliste - mit Hochrechnung auf die geplanten Portionen und
    Zusammenfassung gleicher Zutaten."""
    spaghetti = _dish_with_ingredients(
        household_headers,
        "Spaghetti Bolognese",
        4,
        [
            {"name": "Nudeln", "quantity": "500", "unit": "g", "category": "Trockenware"},
            {"name": "Tomatensauce", "quantity": "400", "unit": "g"},
        ],
    )
    auflauf = _dish_with_ingredients(
        household_headers,
        "Nudelauflauf",
        4,
        [{"name": "Nudeln", "quantity": "250", "unit": "g"}],
    )

    # 6 statt 4 Portionen -> Faktor 1,5 auf alle Mengen des Gerichts.
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-03",
            "meal_type": "dinner",
            "dish_name": spaghetti["name"],
            "dish_id": spaghetti["id"],
            "servings": 6,
        },
        headers=household_headers,
    )
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-05",
            "meal_type": "dinner",
            "dish_name": auflauf["name"],
            "dish_id": auflauf["id"],
            "servings": 4,
        },
        headers=household_headers,
    )
    # Frei eingetippte Mahlzeit ohne Katalogbezug - sie kann nichts beitragen
    # und muss deshalb gemeldet werden.
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-06",
            "meal_type": "lunch",
            "dish_name": "Reste",
        },
        headers=household_headers,
    )

    shopping_list = client.post(
        "/v1/shopping-lists", json={"meal_week_id": meal_week_id}, headers=household_headers
    ).json()

    fill = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    )
    assert fill.status_code == 200
    result = fill.json()
    assert result["considered_meals"] == 2
    assert result["skipped_meals"] == ["Reste"]
    assert result["created_items"] == 2

    items = client.get(
        f"/v1/shopping-items?shopping_list_id={shopping_list['id']}", headers=household_headers
    ).json()
    by_name = {item["name"]: item for item in items}
    # 500 g * 1,5 = 750 g aus den Spaghetti, plus 250 g aus dem Auflauf.
    assert by_name["Nudeln"]["quantity"] == "1000.00"
    assert by_name["Nudeln"]["unit"] == "g"
    assert by_name["Nudeln"]["category"] == "Trockenware"
    assert by_name["Tomatensauce"]["quantity"] == "600.00"


def test_fill_shopping_list_twice_does_not_duplicate(
    household_headers: dict[str, str], meal_week_id: str
) -> None:
    dish = _dish_with_ingredients(
        household_headers, "Gemuesepfanne", 2, [{"name": "Paprika", "quantity": "2", "unit": "Stk"}]
    )
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-04",
            "meal_type": "dinner",
            "dish_name": dish["name"],
            "dish_id": dish["id"],
            "servings": 2,
        },
        headers=household_headers,
    )
    shopping_list = client.post(
        "/v1/shopping-lists", json={"meal_week_id": meal_week_id}, headers=household_headers
    ).json()

    first = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert first["created_items"] == 1

    second = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert second["created_items"] == 0
    assert second["merged_items"] == 1

    items = client.get(
        f"/v1/shopping-items?shopping_list_id={shopping_list['id']}", headers=household_headers
    ).json()
    assert len(items) == 1


def test_fill_merges_unit_spellings_of_the_same_unit(
    household_headers: dict[str, str], meal_week_id: str
) -> None:
    """"2 St" und "1 Stueck" sind dieselbe Einheit und gehoeren auf eine Zeile.

    Die Preislogik kennt diese Schreibweisen laengst als gleichwertig; die
    Zusammenfassung verglich frueher den Rohtext und machte daraus zwei
    Einkaufsposten mit demselben Namen.
    """
    a = _dish_with_ingredients(
        household_headers, "Pfanne", 2, [{"name": "Zwiebeln", "quantity": "2", "unit": "St"}]
    )
    b = _dish_with_ingredients(
        household_headers, "Auflauf", 2, [{"name": "Zwiebeln", "quantity": "1", "unit": "Stück"}]
    )
    for tag, gericht in (("2026-08-03", a), ("2026-08-04", b)):
        client.post(
            "/v1/meals",
            json={
                "meal_week_id": meal_week_id,
                "meal_date": tag,
                "meal_type": "dinner",
                "dish_name": gericht["name"],
                "dish_id": gericht["id"],
                "servings": 2,
            },
            headers=household_headers,
        )

    shopping_list = client.post(
        "/v1/shopping-lists", json={"meal_week_id": meal_week_id}, headers=household_headers
    ).json()
    fill = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert fill["created_items"] == 1

    items = client.get(
        f"/v1/shopping-items?shopping_list_id={shopping_list['id']}", headers=household_headers
    ).json()
    assert len(items) == 1
    assert items[0]["quantity"] == "3.00"


def test_fill_keeps_same_name_different_unit_rows_apart(
    household_headers: dict[str, str], meal_week_id: str
) -> None:
    """Dieselbe Zutat in nicht umrechenbaren Einheiten ergibt zwei Zeilen -
    und beide muessen beim wiederholten Fuellen ihre eigene Zeile behalten.

    Vorher zeigte der Abgleich (nur ueber den Namen) fuer beide Zutaten auf
    dieselbe Zeile: die eine wurde zweimal ueberschrieben, die andere behielt
    fuer immer den Stand des ersten Laufs.
    """
    dal = _dish_with_ingredients(
        household_headers,
        "Dal",
        2,
        [{"name": "Knoblauch", "quantity": "3", "unit": "Zehen"}],
    )
    suppe = _dish_with_ingredients(
        household_headers,
        "Suppe",
        2,
        [{"name": "Knoblauch", "quantity": "1", "unit": "Stk"}],
    )
    for tag, gericht in (("2026-08-03", dal), ("2026-08-04", suppe)):
        client.post(
            "/v1/meals",
            json={
                "meal_week_id": meal_week_id,
                "meal_date": tag,
                "meal_type": "dinner",
                "dish_name": gericht["name"],
                "dish_id": gericht["id"],
                "servings": 2,
            },
            headers=household_headers,
        )

    shopping_list = client.post(
        "/v1/shopping-lists", json={"meal_week_id": meal_week_id}, headers=household_headers
    ).json()

    first = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert first["created_items"] == 2

    # Menge einer Zeile von Hand verstellen, damit sichtbar wird, ob der
    # zweite Lauf wirklich BEIDE Zeilen wieder auf den Planstand zieht.
    items = client.get(
        f"/v1/shopping-items?shopping_list_id={shopping_list['id']}", headers=household_headers
    ).json()
    zehen = next(i for i in items if i["unit"] == "Zehen")
    client.patch(
        f"/v1/shopping-items/{zehen['id']}", json={"quantity": "99"}, headers=household_headers
    )

    second = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert second["created_items"] == 0
    assert second["merged_items"] == 2

    items = client.get(
        f"/v1/shopping-items?shopping_list_id={shopping_list['id']}", headers=household_headers
    ).json()
    assert len(items) == 2
    nach_einheit = {i["unit"]: i for i in items}
    assert set(nach_einheit) == {"Zehen", "Stk"}
    assert nach_einheit["Zehen"]["quantity"] == "3.00"
    assert nach_einheit["Stk"]["quantity"] == "1.00"


def test_deleting_dish_removes_its_ingredients(household_headers: dict[str, str]) -> None:
    dish = _dish_with_ingredients(
        household_headers, "Linsensuppe", 4, [{"name": "Linsen", "quantity": "300", "unit": "g"}]
    )
    assert client.delete(f"/v1/dishes/{dish['id']}", headers=household_headers).status_code == 204
    assert (
        client.get(f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers).status_code
        == 404
    )
