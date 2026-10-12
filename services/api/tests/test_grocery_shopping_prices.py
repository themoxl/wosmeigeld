"""Einkaufsliste aus dem Wochenplan: Kassenpreis in ganzen Packungen.

Der Unterschied zur Gerichtsschaetzung ist der Kern dieser Suite. Das Gericht
fragt "was ist die Mahlzeit wert" und rechnet anteilig; die Einkaufsliste
fragt "was zahle ich an der Kasse" und rundet auf ganze Packungen auf. Beides
in eine Zahl zu pressen waere fuer jeweils eine der beiden Ansichten falsch.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p8s-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Einkaufs-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _dish_with(
    headers: dict[str, str], name: str, servings: int, ingredients: list[dict]
) -> dict:
    dish = client.post(
        "/v1/dishes", json={"name": name, "default_servings": servings}, headers=headers
    ).json()
    for ingredient in ingredients:
        client.post(
            "/v1/dish-ingredients", json={"dish_id": dish["id"], **ingredient}, headers=headers
        )
    return dish


def _plan(headers: dict[str, str], week_id: str, dish: dict, day: str, servings: int) -> None:
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": week_id,
            "meal_date": day,
            "meal_type": "dinner",
            "dish_name": dish["name"],
            "dish_id": dish["id"],
            "servings": servings,
        },
        headers=headers,
    )


@pytest.fixture()
def priced_week(household_headers: dict[str, str]) -> str:
    """Woche mit zwei Gerichten, die sich dieselbe Zutat teilen."""
    week = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-08-03", "budget_minor": 10000},
        headers=household_headers,
    ).json()
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Nudeln", "price_minor": 199, "pack_quantity": "1", "pack_unit": "kg"},
        headers=household_headers,
    )
    spaghetti = _dish_with(
        household_headers, "Spaghetti", 4, [{"name": "Nudeln", "quantity": "750", "unit": "g"}]
    )
    auflauf = _dish_with(
        household_headers, "Auflauf", 4, [{"name": "Nudeln", "quantity": "250", "unit": "g"}]
    )
    _plan(household_headers, week["id"], spaghetti, "2026-08-03", 4)
    _plan(household_headers, week["id"], auflauf, "2026-08-05", 4)
    return str(week["id"])


def _new_list(headers: dict[str, str], week_id: str) -> dict:
    return client.post(
        "/v1/shopping-lists", json={"meal_week_id": week_id}, headers=headers
    ).json()


def _items(headers: dict[str, str], list_id: str) -> dict[str, dict]:
    return {
        item["name"]: item
        for item in client.get(
            f"/v1/shopping-items?shopping_list_id={list_id}", headers=headers
        ).json()
    }


def test_shopping_list_gets_the_checkout_price_in_whole_packs(
    household_headers: dict[str, str], priced_week: str
) -> None:
    shopping_list = _new_list(household_headers, priced_week)

    result = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert result["created_items"] == 1
    assert result["unpriced_items"] == 0

    item = _items(household_headers, shopping_list["id"])["Nudeln"]
    # 750 g + 250 g = 1000 g Bedarf. Erst addieren, DANN aufrunden - sonst
    # waeren es zwei Packungen statt einer.
    assert item["quantity"] == "1.00"
    assert item["unit"] == "kg"
    assert item["planned_price_minor"] == 199

    # Die Gerichtsschaetzung bleibt anteilig: 750 g bzw. 250 g von 1,99 EUR/kg.
    dishes = {d["name"]: d for d in client.get("/v1/dishes", headers=household_headers).json()}
    assert dishes["Spaghetti"]["computed_cost_minor"] == 149
    assert dishes["Auflauf"]["computed_cost_minor"] == 50


def test_leftover_from_rounding_is_noted_not_hidden(
    household_headers: dict[str, str], priced_week: str
) -> None:
    """Wer 800 g auf der Liste sieht, obwohl das Rezept 500 g braucht, muss den
    Grund danebenstehen haben."""
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Tomatensauce", "price_minor": 89, "pack_quantity": "400", "pack_unit": "g"},
        headers=household_headers,
    )
    pasta = _dish_with(
        household_headers, "Pasta", 4, [{"name": "Tomatensauce", "quantity": "500", "unit": "g"}]
    )
    _plan(household_headers, priced_week, pasta, "2026-08-06", 4)

    shopping_list = _new_list(household_headers, priced_week)
    client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    )

    items = _items(household_headers, shopping_list["id"])
    # 500 g Bedarf, Packung 400 g -> zwei Packungen, 800 g, 1,78 EUR.
    assert items["Tomatensauce"]["quantity"] == "800.00"
    assert items["Tomatensauce"]["planned_price_minor"] == 178
    assert items["Tomatensauce"]["note"] == "für 500 g benötigt"
    # Wo Kaufmenge und Bedarf uebereinstimmen, gibt es keine Notiz.
    assert items["Nudeln"]["note"] is None


def test_filling_twice_neither_doubles_amounts_nor_money(
    household_headers: dict[str, str], priced_week: str
) -> None:
    """Der Knopf ist ein Abgleich, kein Anhaengen. Vorher verdoppelte ein
    zweiter Lauf still die Mengen - mit Preisen waere daraus doppeltes Geld
    geworden."""
    shopping_list = _new_list(household_headers, priced_week)
    client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    )
    second = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert second["created_items"] == 0
    assert second["merged_items"] == 1

    items = _items(household_headers, shopping_list["id"])
    assert len(items) == 1
    assert items["Nudeln"]["quantity"] == "1.00"
    assert items["Nudeln"]["planned_price_minor"] == 199

    refreshed = client.get(
        f"/v1/shopping-lists?meal_week_id={priced_week}", headers=household_headers
    ).json()[0]
    assert refreshed["planned_total_minor"] == 199


def test_fill_follows_a_changed_plan(
    household_headers: dict[str, str], priced_week: str
) -> None:
    """Weil der Lauf abgleicht statt anzuhaengen, sinkt die Menge auch wieder,
    wenn eine Mahlzeit wegfaellt."""
    shopping_list = _new_list(household_headers, priced_week)
    client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    )

    meals = client.get(
        f"/v1/meals?meal_week_id={priced_week}", headers=household_headers
    ).json()
    spaghetti = next(m for m in meals if m["dish_name"] == "Spaghetti")
    client.delete(f"/v1/meals/{spaghetti['id']}", headers=household_headers)

    client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    )
    item = _items(household_headers, shopping_list["id"])["Nudeln"]
    # Nur noch 250 g Bedarf - aber eine Packung bleibt die kleinste Einheit.
    assert item["quantity"] == "1.00"
    assert item["planned_price_minor"] == 199
    assert item["note"] == "für 250 g benötigt"


def test_deleting_the_source_meal_keeps_the_row_under_the_plans_control(
    household_headers: dict[str, str], priced_week: str
) -> None:
    """Regression: die Herkunft haengt an from_meal_plan, nicht an meal_id.

    meal_id ist 'on delete set null'. Wuerde der Abgleich daran erkennen, ob
    eine Zeile ihm gehoert, wechselte sie beim Loeschen der Quell-Mahlzeit
    lautlos die Besitzverhaeltnisse - und bliebe danach mit veralteter Menge
    und veraltetem Preis stehen.
    """
    shopping_list = _new_list(household_headers, priced_week)
    client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    )

    meals = client.get(f"/v1/meals?meal_week_id={priced_week}", headers=household_headers).json()
    source = next(m for m in meals if m["dish_name"] == "Spaghetti")
    client.delete(f"/v1/meals/{source['id']}", headers=household_headers)

    item = _items(household_headers, shopping_list["id"])["Nudeln"]
    assert item["meal_id"] is None  # der Verweis ist weg ...
    assert item["from_meal_plan"] is True  # ... die Herkunft bleibt

    result = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert result["merged_items"] == 1
    assert _items(household_headers, shopping_list["id"])["Nudeln"]["note"] == "für 250 g benötigt"


def test_hand_added_items_are_left_alone(
    household_headers: dict[str, str], priced_week: str
) -> None:
    """Eine selbst eingetragene Zeile gehoert dem Nutzer. Der Abgleich darf
    ihren Preis nicht ueberschreiben, auch wenn der Name passt."""
    shopping_list = _new_list(household_headers, priced_week)
    client.post(
        "/v1/shopping-items",
        json={
            "shopping_list_id": shopping_list["id"],
            "name": "Nudeln",
            "quantity": "3",
            "unit": "Pkg",
            "planned_price_minor": 500,
        },
        headers=household_headers,
    )

    result = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert result["created_items"] == 0
    assert result["merged_items"] == 1

    item = _items(household_headers, shopping_list["id"])["Nudeln"]
    assert item["quantity"] == "3.00"
    assert item["unit"] == "Pkg"
    assert item["planned_price_minor"] == 500


def test_ingredient_without_price_lands_on_the_list_without_amount(
    household_headers: dict[str, str], priced_week: str
) -> None:
    """Ohne Preis fehlt der Betrag - die Zutat aber nicht. Und die Antwort
    sagt, wie viele Posten unbewertet blieben."""
    salat = _dish_with(
        household_headers, "Salat", 2, [{"name": "Gurke", "quantity": "1", "unit": "Stk"}]
    )
    _plan(household_headers, priced_week, salat, "2026-08-07", 2)

    shopping_list = _new_list(household_headers, priced_week)
    result = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert result["unpriced_items"] == 1

    items = _items(household_headers, shopping_list["id"])
    assert items["Gurke"]["planned_price_minor"] is None
    assert items["Gurke"]["quantity"] == "1.00"

    # Nur die bewerteten Posten stehen in der Plansumme - sie ist damit
    # sichtbar unvollstaendig statt scheinbar fertig.
    refreshed = client.get(
        f"/v1/shopping-lists?meal_week_id={priced_week}", headers=household_headers
    ).json()[0]
    assert refreshed["planned_total_minor"] == 199


def test_mixed_units_of_one_ingredient_become_a_single_pack(
    household_headers: dict[str, str],
) -> None:
    """"2 Zehen" aus dem einen Gericht und "10 g" aus dem anderen sind
    dieselbe Zutat. Ohne Umrechnung blieben es zwei Zeilen, und die
    Packungsrundung fiele zweimal an - man kauft zwei Glaeser statt einem."""
    week = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-09-07", "budget_minor": 10000},
        headers=household_headers,
    ).json()
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Knoblauch", "price_minor": 129, "pack_quantity": "100", "pack_unit": "g"},
        headers=household_headers,
    )
    client.post(
        "/v1/ingredient-unit-conversions",
        json={"ingredient_name": "Knoblauch", "unit": "Zehe", "factor": "5", "base_unit": "g"},
        headers=household_headers,
    )
    curry = _dish_with(
        household_headers, "Curry", 2, [{"name": "Knoblauch", "quantity": "2", "unit": "Zehen"}]
    )
    dip = _dish_with(
        household_headers, "Dip", 2, [{"name": "Knoblauch", "quantity": "10", "unit": "g"}]
    )
    _plan(household_headers, str(week["id"]), curry, "2026-09-07", 2)
    _plan(household_headers, str(week["id"]), dip, "2026-09-08", 2)

    shopping_list = _new_list(household_headers, str(week["id"]))
    result = client.post(
        f"/v1/shopping-lists/{shopping_list['id']}/fill-from-meals", headers=household_headers
    ).json()
    assert result["created_items"] == 1
    assert result["unpriced_items"] == 0

    item = _items(household_headers, shopping_list["id"])["Knoblauch"]
    # 2 Zehen (10 g) + 10 g = 20 g Bedarf -> eine Packung zu 100 g.
    assert item["quantity"] == "100.00"
    assert item["unit"] == "g"
    assert item["planned_price_minor"] == 129
