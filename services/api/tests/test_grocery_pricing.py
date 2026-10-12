"""Zutaten-Preisliste und die daraus gerechnete Gerichtsschaetzung.

Der Kern ist die Einheitenumrechnung: "1,99 EUR fuer 1 kg" muss ein Rezept mit
500 g richtig bewerten, und wo die Einheiten NICHT zueinander passen, darf
keine Zahl entstehen. Eine falsche Schaetzung ist schlimmer als eine sichtbar
fehlende.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.grocery_pricing_service import (
    Conversion,
    PriceEntry,
    cost_detail,
    cost_for,
    normalize_unit,
    suggest_conversion,
    suggest_price_match,
)
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p8p-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Preis-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


# ---------------------------------------------------------------------------
# Rechenkern
# ---------------------------------------------------------------------------


def _price(price_minor: int, quantity: str, unit: str | None) -> PriceEntry:
    return PriceEntry(
        name="x", price_minor=price_minor, pack_quantity=Decimal(quantity), pack_unit=unit
    )


@pytest.mark.parametrize(
    ("quantity", "unit", "price", "expected"),
    [
        # 1,99 EUR je kg -> 500 g kosten 99,5 Cent, kaufmaennisch 100.
        ("500", "g", _price(199, "1", "kg"), 100),
        # Gleiche Einheit, glatte Teilung.
        ("400", "g", _price(89, "400", "g"), 89),
        ("800", "g", _price(89, "400", "g"), 178),
        # Volumen ueber Liter.
        ("250", "ml", _price(120, "1", "l"), 30),
        # Stueck, auch ohne Einheitsangabe im Rezept.
        ("2", "Stk", _price(249, "10", "Stk"), 50),
        ("2", None, _price(249, "10", "Stk"), 50),
        # Grossschreibung und ausgeschriebene Einheiten aendern nichts.
        ("1", "KG", _price(199, "1000", "Gramm"), 199),
    ],
)
def test_cost_conversion(
    quantity: str, unit: str | None, price: PriceEntry, expected: int
) -> None:
    assert cost_for(Decimal(quantity), unit, price) == expected


@pytest.mark.parametrize(
    ("quantity", "unit", "price"),
    [
        # Packung gegen Gramm: nicht umrechenbar, also keine Zahl erfinden.
        ("1", "Pkg", _price(89, "400", "g")),
        ("2", "Dose", _price(89, "400", "g")),
        # Gewicht gegen Volumen ist ohne Dichte nicht bestimmbar.
        ("100", "g", _price(120, "1", "l")),
        # Verschiedene Stueckeinheiten sind nicht dasselbe.
        ("1", "Bund", _price(99, "1", "Dose")),
    ],
)
def test_incompatible_units_yield_no_estimate(
    quantity: str, unit: str, price: PriceEntry
) -> None:
    assert cost_for(Decimal(quantity), unit, price) is None


def test_missing_price_or_quantity_yields_no_estimate() -> None:
    assert cost_for(Decimal("500"), "g", None) is None
    # "Salz" ohne Menge ist Absicht, kein Fehler - und kostet nichts Berechenbares.
    assert cost_for(None, "g", _price(99, "500", "g")) is None


# ---------------------------------------------------------------------------
# Umrechnung von Haushaltseinheiten
# ---------------------------------------------------------------------------
# "1 Zehe = 5 g" ist die Bruecke zwischen Rezept- und Packungseinheit. Sie wird
# NIE geraten: ohne Argument rechnet der Kern exakt wie vorher - genau das
# sichern die Negativfaelle oben ab.


def _conversion(base: str, **factors: str) -> Conversion:
    return Conversion(base_unit=base, factors={u: Decimal(v) for u, v in factors.items()})


@pytest.mark.parametrize(
    ("quantity", "unit", "price", "conversion", "expected"),
    [
        # 1,29 EUR je 100 g, 1 Zehe = 5 g -> 4 Zehen sind 20 g -> 26 Cent.
        ("4", "Zehen", _price(129, "100", "g"), _conversion("g", zehe="5"), 26),
        # Umgekehrte Richtung: das Rezept ist metrisch, die Packung zaehlbar.
        ("400", "g", _price(199, "1", "Stk"), _conversion("g", stk="350"), 227),
        # Masse gegen Volumen - die Dichte ist nur eine Zeile derselben Tabelle.
        ("9", "g", _price(349, "250", "ml"), _conversion("g", ml="0.93"), 14),
        # Loeffel auf eine Zutat, deren Preis in Litern steht.
        ("3", "EL", _price(199, "1", "l"), _conversion("ml", el="15"), 9),
        # Mehrzahl und Einzahl sind dieselbe Einheit.
        ("2", "Zehe", _price(129, "100", "g"), _conversion("g", zehe="5"), 13),
    ],
)
def test_conversion_bridges_units(
    quantity: str, unit: str, price: PriceEntry, conversion: Conversion, expected: int
) -> None:
    assert cost_for(Decimal(quantity), unit, price, conversion) == expected


def test_builtin_factors_beat_a_stored_row() -> None:
    """1 kg sind 1000 g - auch wenn jemand fuer diese Zutat Unsinn gespeichert hat."""
    price = _price(199, "1", "kg")
    conversion = _conversion("g", kg="500")
    assert cost_for(Decimal("500"), "g", price, conversion) == 100


def test_conversion_without_matching_row_stays_a_gap() -> None:
    """Gemuesebruehe: 360 ml fertig gegen 135 g Pulver ist eine Ergiebigkeit,
    keine Einheit. Ohne eigene Zeile bleibt die Luecke sichtbar."""
    assert cost_for(Decimal("360"), "ml", _price(199, "135", "g"), _conversion("g", tl="3")) is None


def test_cost_detail_marks_estimated_values() -> None:
    price = _price(129, "100", "g")
    over_conversion = cost_detail(Decimal("4"), "Zehen", price, _conversion("g", zehe="5"))
    assert over_conversion is not None and over_conversion.used_conversion is True
    # Reine Masse-Rechnung ist keine Schaetzung, auch wenn eine Umrechnung vorliegt.
    direct = cost_detail(Decimal("50"), "g", price, _conversion("g", zehe="5"))
    assert direct is not None and direct.used_conversion is False


@pytest.mark.parametrize(
    ("key", "recipe_unit", "pack_unit", "expected_unit", "expected_base"),
    [
        ("knoblauch", "Zehen", "g", "zehe", "g"),
        ("zwiebeln", "St", "kg", "stk", "g"),
        ("rapsöl", "EL", "l", "el", "ml"),
        ("sesamöl", "g", "ml", "ml", "g"),
        ("brokkoli", "g", "Stk", "stk", "g"),
    ],
)
def test_suggestion_finds_the_missing_number(
    key: str, recipe_unit: str, pack_unit: str, expected_unit: str, expected_base: str
) -> None:
    suggestion = suggest_conversion(key, recipe_unit, pack_unit)
    assert suggestion is not None
    assert (suggestion.unit, suggestion.base_unit) == (expected_unit, expected_base)


@pytest.mark.parametrize(
    ("key", "recipe_unit", "pack_unit"),
    [
        # Beide Seiten zaehlbar - dazwischen gibt es keine allgemeine Zahl.
        ("paprika chunks vegan", "Pkg", "Stk"),
        # Ergiebigkeit statt Einheit: bewusst nicht mitgeliefert.
        ("gemüsebrühe", "ml", "g"),
        # Unbekannte Zutat: lieber keine Zahl als eine geratene.
        ("wasauchimmer", "Bund", "g"),
    ],
)
def test_no_suggestion_where_there_is_no_general_number(
    key: str, recipe_unit: str, pack_unit: str
) -> None:
    assert suggest_conversion(key, recipe_unit, pack_unit) is None


def test_unit_synonyms_are_normalized() -> None:
    assert normalize_unit("Stück") == "stk"
    assert normalize_unit(" Packung ") == "pkg"
    assert normalize_unit(None) == "stk"
    # Unbekanntes bleibt unveraendert und passt dann nur auf sich selbst.
    assert normalize_unit("Schuss") == "schuss"
    # Mehrzahl und ausgeschriebene Form treffen dieselbe Zeile.
    assert normalize_unit("Zehen") == normalize_unit("Zehe") == "zehe"
    assert normalize_unit("Esslöffel") == "el"
    assert normalize_unit("Scheiben") == "scheibe"


# ---------------------------------------------------------------------------
# Preisliste und Gerichtskosten ueber die API
# ---------------------------------------------------------------------------


def _dish_with(headers: dict[str, str], name: str, servings: int, ingredients: list[dict]) -> dict:
    dish = client.post(
        "/v1/dishes", json={"name": name, "default_servings": servings}, headers=headers
    ).json()
    for ingredient in ingredients:
        client.post(
            "/v1/dish-ingredients", json={"dish_id": dish["id"], **ingredient}, headers=headers
        )
    return dish


def test_dish_cost_is_computed_from_the_price_list(household_headers: dict[str, str]) -> None:
    for entry in (
        {"name": "Nudeln", "price_minor": 199, "pack_quantity": "1", "pack_unit": "kg"},
        {"name": "Tomatensauce", "price_minor": 89, "pack_quantity": "400", "pack_unit": "g"},
    ):
        assert (
            client.post("/v1/ingredient-prices", json=entry, headers=household_headers).status_code
            == 201
        )

    _dish_with(
        household_headers,
        "Pasta",
        4,
        [
            {"name": "Nudeln", "quantity": "500", "unit": "g"},
            {"name": "Tomatensauce", "quantity": "400", "unit": "g"},
            {"name": "Salz"},  # ohne Menge: kein Beitrag, aber auch keine Luecke
        ],
    )

    dish = client.get("/v1/dishes", headers=household_headers).json()[0]
    # 100 (Nudeln) + 89 (Sauce) = 189 Cent fuer 4 Portionen.
    assert dish["computed_cost_minor"] == 189
    assert dish["unpriced_ingredients"] == 0
    # Ohne Handeingabe gilt die Berechnung.
    assert dish["estimated_cost_minor"] is None
    assert dish["effective_cost_minor"] == 189


def test_manual_estimate_overrides_computation_until_cleared(
    household_headers: dict[str, str],
) -> None:
    """Die Vorrangregel: Handeingabe gewinnt, solange sie dasteht. Das Feld zu
    leeren heisst "zurueck zur Berechnung", nicht "keine Schaetzung"."""
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Reis", "price_minor": 200, "pack_quantity": "1", "pack_unit": "kg"},
        headers=household_headers,
    )
    dish = _dish_with(
        household_headers, "Reispfanne", 2, [{"name": "Reis", "quantity": "250", "unit": "g"}]
    )

    computed = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert computed["computed_cost_minor"] == 50
    assert computed["effective_cost_minor"] == 50

    client.patch(
        f"/v1/dishes/{dish['id']}", json={"estimated_cost_minor": 900}, headers=household_headers
    )
    overridden = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert overridden["computed_cost_minor"] == 50  # Berechnung bleibt sichtbar
    assert overridden["effective_cost_minor"] == 900

    client.patch(
        f"/v1/dishes/{dish['id']}", json={"estimated_cost_minor": None}, headers=household_headers
    )
    cleared = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert cleared["effective_cost_minor"] == 50


def test_unpriced_ingredients_are_reported_not_hidden(
    household_headers: dict[str, str],
) -> None:
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Nudeln", "price_minor": 199, "pack_quantity": "1", "pack_unit": "kg"},
        headers=household_headers,
    )
    dish = _dish_with(
        household_headers,
        "Pasta mit Sauce",
        4,
        [
            {"name": "Nudeln", "quantity": "500", "unit": "g"},
            {"name": "Hackfleisch", "quantity": "500", "unit": "g"},  # kein Preis
        ],
    )

    listed = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert listed["computed_cost_minor"] == 100
    # Die Schaetzung ist unvollstaendig, und das steht in der Antwort.
    assert listed["unpriced_ingredients"] == 1

    ingredients = client.get(
        f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers
    ).json()
    by_name = {i["name"]: i for i in ingredients}
    assert by_name["Nudeln"]["cost_minor"] == 100
    assert by_name["Nudeln"]["has_price"] is True
    assert by_name["Hackfleisch"]["cost_minor"] is None
    assert by_name["Hackfleisch"]["has_price"] is False
    assert by_name["Hackfleisch"]["unit_mismatch"] is False


def test_unit_mismatch_is_distinguishable_from_missing_price(
    household_headers: dict[str, str],
) -> None:
    """Beide Faelle liefern keine Kosten - aber der Nutzer muss wissen, ob ein
    Preis fehlt oder die Einheit nicht passt. Das eine legt man an, das andere
    korrigiert man."""
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Tomatensauce", "price_minor": 89, "pack_quantity": "400", "pack_unit": "g"},
        headers=household_headers,
    )
    dish = _dish_with(
        household_headers, "Auflauf", 4, [{"name": "Tomatensauce", "quantity": "1", "unit": "Pkg"}]
    )

    ingredient = client.get(
        f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers
    ).json()[0]
    assert ingredient["cost_minor"] is None
    assert ingredient["has_price"] is True
    assert ingredient["unit_mismatch"] is True


def test_price_change_reaches_every_dish_using_the_ingredient(
    household_headers: dict[str, str],
) -> None:
    """Der eigentliche Zweck der Liste: der Preis haengt an der Zutat, nicht am
    Gericht. Einmal geaendert, wirkt er ueberall."""
    price = client.post(
        "/v1/ingredient-prices",
        json={"name": "Nudeln", "price_minor": 100, "pack_quantity": "1", "pack_unit": "kg"},
        headers=household_headers,
    ).json()
    _dish_with(
        household_headers, "Pasta A", 2, [{"name": "Nudeln", "quantity": "500", "unit": "g"}]
    )
    _dish_with(
        household_headers, "Pasta B", 2, [{"name": "nudeln", "quantity": "250", "unit": "g"}]
    )

    before = {d["name"]: d["computed_cost_minor"] for d in
              client.get("/v1/dishes", headers=household_headers).json()}
    assert before == {"Pasta A": 50, "Pasta B": 25}

    client.patch(
        f"/v1/ingredient-prices/{price['id']}", json={"price_minor": 200}, headers=household_headers
    )
    after = {d["name"]: d["computed_cost_minor"] for d in
             client.get("/v1/dishes", headers=household_headers).json()}
    # Gross-/Kleinschreibung der Zutat spielt dabei keine Rolle.
    assert after == {"Pasta A": 100, "Pasta B": 50}

    # Und die Liste zeigt, worauf sich eine Aenderung auswirkt.
    listed = client.get("/v1/ingredient-prices", headers=household_headers).json()[0]
    assert listed["used_in_dishes"] == 2


def test_duplicate_price_entry_is_rejected(household_headers: dict[str, str]) -> None:
    payload = {"name": "Butter", "price_minor": 249, "pack_quantity": "250", "pack_unit": "g"}
    created = client.post("/v1/ingredient-prices", json=payload, headers=household_headers)
    assert created.status_code == 201
    duplicate = client.post(
        "/v1/ingredient-prices", json={**payload, "name": "BUTTER"}, headers=household_headers
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "ingredient_price_already_exists"


def test_deleting_a_price_leaves_the_recipe_intact(household_headers: dict[str, str]) -> None:
    price = client.post(
        "/v1/ingredient-prices",
        json={"name": "Linsen", "price_minor": 150, "pack_quantity": "500", "pack_unit": "g"},
        headers=household_headers,
    ).json()
    dish = _dish_with(
        household_headers, "Linsensuppe", 4, [{"name": "Linsen", "quantity": "250", "unit": "g"}]
    )
    listed = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert listed["computed_cost_minor"] == 75

    assert (
        client.delete(f"/v1/ingredient-prices/{price['id']}", headers=household_headers).status_code
        == 204
    )

    after = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert after["computed_cost_minor"] is None
    assert after["unpriced_ingredients"] == 1
    # Die Zutat selbst steht unveraendert im Rezept.
    ingredients = client.get(
        f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers
    ).json()
    assert [i["name"] for i in ingredients] == ["Linsen"]


# ---------------------------------------------------------------------------
# Abdeckung und Kosten je Portion
# ---------------------------------------------------------------------------


def test_dish_reports_coverage_not_just_the_gap_count(
    household_headers: dict[str, str],
) -> None:
    """Eine nackte Lueckenzahl sagt nicht, ob das viel ist. Erst "1 von 2
    bepreist" macht klar, wie weit die Schaetzung traegt - und Zutaten ohne
    Menge duerfen die Abdeckung nicht kleinrechnen."""
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Nudeln", "price_minor": 199, "pack_quantity": "1", "pack_unit": "kg"},
        headers=household_headers,
    )
    _dish_with(
        household_headers,
        "Pasta",
        4,
        [
            {"name": "Nudeln", "quantity": "500", "unit": "g"},
            {"name": "Hackfleisch", "quantity": "500", "unit": "g"},
            {"name": "Salz"},
        ],
    )

    dish = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert dish["priced_ingredients"] == 1
    assert dish["unpriced_ingredients"] == 1
    assert dish["unquantified_ingredients"] == 1


def test_cost_per_serving_makes_dishes_comparable(household_headers: dict[str, str]) -> None:
    """12,00 fuer 4 Portionen gegen 6,00 fuer 2 ist erst als 3,00 gegen 3,00
    eine Aussage."""
    for name, servings, estimate in (("Braten", 4, 1200), ("Auflauf", 2, 600)):
        client.post(
            "/v1/dishes",
            json={"name": name, "default_servings": servings, "estimated_cost_minor": estimate},
            headers=household_headers,
        )

    per_serving = {
        d["name"]: d["cost_per_serving_minor"]
        for d in client.get("/v1/dishes", headers=household_headers).json()
    }
    assert per_serving == {"Braten": 300, "Auflauf": 300}


def test_cost_per_serving_is_absent_without_any_estimate(
    household_headers: dict[str, str],
) -> None:
    client.post("/v1/dishes", json={"name": "Unbeziffert"}, headers=household_headers)
    dish = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert dish["effective_cost_minor"] is None
    assert dish["cost_per_serving_minor"] is None


def test_seeded_dishes_leave_the_estimate_to_the_calculation(
    household_headers: dict[str, str],
) -> None:
    """Der Seeder darf keine Handschaetzung setzen. Sie ist eine
    UEBERSTEUERUNG: einmal gesetzt, kaeme die Preisliste in einem frisch
    angelegten Haushalt nie zum Zug, egal wie gut sie gepflegt waere."""
    seeded = client.post("/v1/dishes/seed-defaults", headers=household_headers)
    assert seeded.status_code == 201
    assert seeded.json()["seeded"] > 0

    dishes = client.get("/v1/dishes", headers=household_headers).json()
    assert dishes, "Der Seeder soll Gerichte anlegen"
    assert all(d["estimated_cost_minor"] is None for d in dishes)
    # Zutaten stehen trotzdem da - es fehlen nur die Preise dazu.
    assert all(d["ingredient_count"] > 0 for d in dishes)


# ---------------------------------------------------------------------------
# Preisluecken
# ---------------------------------------------------------------------------


def test_price_gaps_group_by_ingredient_not_by_dish(
    household_headers: dict[str, str],
) -> None:
    """Der Sinn der Ansicht: "Hackfleisch" fehlt in zwei Gerichten, ist aber
    EIN Handgriff. Nach Gerichten sortiert muesste man dieselbe Zutat zweimal
    anfassen."""
    _dish_with(
        household_headers, "Chili", 4, [{"name": "Hackfleisch", "quantity": "500", "unit": "g"}]
    )
    _dish_with(
        household_headers,
        "Bolognese",
        4,
        [{"name": "Hackfleisch", "quantity": "300", "unit": "g"}],
    )

    gaps = client.get("/v1/dishes/price-gaps", headers=household_headers).json()
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["name"] == "Hackfleisch"
    assert gap["kind"] == "no_price"
    assert gap["dish_count"] == 2
    assert sorted(d["dish_name"] for d in gap["dishes"]) == ["Bolognese", "Chili"]
    # Einheit aus dem Rezept: sie fuellt beim Anlegen das Einheitenfeld vor,
    # damit der Einheitenkonflikt gar nicht erst entsteht.
    assert gap["recipe_unit"] == "g"


def test_price_gaps_are_sorted_by_leverage(household_headers: dict[str, str]) -> None:
    """Oben steht, was am meisten bringt - sonst arbeitet man die Liste
    alphabetisch ab und die Abdeckung steigt erst am Ende."""
    _dish_with(
        household_headers,
        "Auflauf",
        4,
        [
            {"name": "Zwiebeln", "quantity": "2", "unit": "Stk"},
            {"name": "Anchovis", "quantity": "50", "unit": "g"},
        ],
    )
    _dish_with(
        household_headers, "Suppe", 4, [{"name": "Zwiebeln", "quantity": "1", "unit": "Stk"}]
    )

    gaps = client.get("/v1/dishes/price-gaps", headers=household_headers).json()
    assert [g["name"] for g in gaps] == ["Zwiebeln", "Anchovis"]


def test_ingredients_without_quantity_are_no_price_gap(
    household_headers: dict[str, str],
) -> None:
    """Salz ist absichtlich unbeziffert. Es als offene Aufgabe zu fuehren
    waere eine Arbeitsliste, die nie leer wird."""
    _dish_with(household_headers, "Suppe", 4, [{"name": "Salz"}])
    assert client.get("/v1/dishes/price-gaps", headers=household_headers).json() == []


def test_unit_mismatch_gap_names_the_price_row_to_fix(
    household_headers: dict[str, str],
) -> None:
    """Bei einem Einheitenkonflikt ist der Preis da - es fehlt nichts, es
    passt nur nicht. Die Ansicht muss genau die Zeile benennen, die zu
    korrigieren ist."""
    price = client.post(
        "/v1/ingredient-prices",
        json={"name": "Tomatensauce", "price_minor": 89, "pack_quantity": "400", "pack_unit": "g"},
        headers=household_headers,
    ).json()
    _dish_with(
        household_headers,
        "Auflauf",
        4,
        [{"name": "Tomatensauce", "quantity": "1", "unit": "Pkg"}],
    )

    gap = client.get("/v1/dishes/price-gaps", headers=household_headers).json()[0]
    assert gap["kind"] == "unit_mismatch"
    assert gap["price_id"] == price["id"]
    assert gap["price_pack_unit"] == "g"
    assert gap["recipe_unit"] == "Pkg"
    # Ein Zuordnungsvorschlag waere hier sinnlos - der Preis ist ja gefunden.
    assert gap["suggested_price_name"] is None


def test_price_gap_disappears_once_the_price_exists(
    household_headers: dict[str, str],
) -> None:
    _dish_with(
        household_headers, "Pasta", 4, [{"name": "Nudeln", "quantity": "500", "unit": "g"}]
    )
    assert len(client.get("/v1/dishes/price-gaps", headers=household_headers).json()) == 1

    client.post(
        "/v1/ingredient-prices",
        json={"name": "Nudeln", "price_minor": 199, "pack_quantity": "1", "pack_unit": "kg"},
        headers=household_headers,
    )
    assert client.get("/v1/dishes/price-gaps", headers=household_headers).json() == []


# ---------------------------------------------------------------------------
# Zugeordnete Schreibweisen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("recipe_name", "price_name"),
    [
        # Der Fall aus der Praxis: Klammerzusatz gegen Packungsangabe.
        ("Stückige Tomaten (Dose)", "Tomaten, stückig 400g"),
        # Ein- und Mehrzahl.
        ("Zwiebeln", "Zwiebel"),
        # Zusatz in der Preisliste, der die Zutat nicht aendert.
        ("Mehl", "Mehl Type 405"),
    ],
)
def test_similar_names_are_offered_as_a_suggestion(recipe_name: str, price_name: str) -> None:
    assert suggest_price_match(recipe_name, [price_name]) == price_name


@pytest.mark.parametrize(
    ("recipe_name", "price_name"),
    [
        # Gleicher Wortanfang, anderes Produkt - genau die Falle.
        ("Salz", "Salzkartoffeln fertig gewürzt"),
        # Gleiche Grundzutat, andere Sorte.
        ("Hackfleisch gemischt", "Hackfleisch Rind"),
        ("Passierte Tomaten", "Tomaten, stückig"),
    ],
)
def test_different_products_get_no_suggestion(recipe_name: str, price_name: str) -> None:
    """Lieber keine Zuordnung vorschlagen als eine falsche: der Vorschlag wird
    per Klick uebernommen und rechnet danach still weiter."""
    assert suggest_price_match(recipe_name, [price_name]) is None


def test_price_gap_offers_the_similar_price_entry(household_headers: dict[str, str]) -> None:
    client.post(
        "/v1/ingredient-prices",
        json={
            "name": "Tomaten, stückig 400g",
            "price_minor": 89,
            "pack_quantity": "400",
            "pack_unit": "g",
        },
        headers=household_headers,
    )
    _dish_with(
        household_headers,
        "Auflauf",
        4,
        [{"name": "Stückige Tomaten (Dose)", "quantity": "400", "unit": "g"}],
    )

    gap = client.get("/v1/dishes/price-gaps", headers=household_headers).json()[0]
    assert gap["kind"] == "no_price"
    assert gap["suggested_price_name"] == "Tomaten, stückig 400g"


def test_confirmed_alias_makes_the_price_reach_the_recipe(
    household_headers: dict[str, str],
) -> None:
    """Der eigentliche Zweck: nach der Bestaetigung rechnet das Gericht, ohne
    dass der Preis ein zweites Mal angelegt werden muss."""
    client.post(
        "/v1/ingredient-prices",
        json={
            "name": "Tomaten, stückig 400g",
            "price_minor": 89,
            "pack_quantity": "400",
            "pack_unit": "g",
        },
        headers=household_headers,
    )
    _dish_with(
        household_headers,
        "Auflauf",
        4,
        [{"name": "Stückige Tomaten (Dose)", "quantity": "800", "unit": "g"}],
    )

    created = client.post(
        "/v1/ingredient-aliases",
        json={"alias_name": "Stückige Tomaten (Dose)", "price_name": "Tomaten, stückig 400g"},
        headers=household_headers,
    )
    assert created.status_code == 201

    dish = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert dish["computed_cost_minor"] == 178
    assert dish["unpriced_ingredients"] == 0
    # Und die Luecke ist damit erledigt.
    assert client.get("/v1/dishes/price-gaps", headers=household_headers).json() == []


def test_alias_to_a_nonexistent_price_is_rejected(household_headers: dict[str, str]) -> None:
    """Eine Zuordnung ins Leere bewirkt still nichts - genau die Sorte
    Fehlschlag, die spaeter als "der Import hat nichts gebracht" wiederkommt."""
    response = client.post(
        "/v1/ingredient-aliases",
        json={"alias_name": "Irgendwas", "price_name": "Gibt es nicht"},
        headers=household_headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "ingredient_price_not_found"


def test_own_price_entry_wins_over_an_alias(household_headers: dict[str, str]) -> None:
    """Die Zuordnung ist die Ausnahme fuer abweichende Schreibweisen. Gibt es
    die Zutat unter ihrem eigenen Namen, ist das die genauere Angabe."""
    for entry in (
        {"name": "Tomaten allgemein", "price_minor": 100, "pack_quantity": "1", "pack_unit": "kg"},
        {"name": "Cocktailtomaten", "price_minor": 300, "pack_quantity": "1", "pack_unit": "kg"},
    ):
        client.post("/v1/ingredient-prices", json=entry, headers=household_headers)
    client.post(
        "/v1/ingredient-aliases",
        json={"alias_name": "Cocktailtomaten", "price_name": "Tomaten allgemein"},
        headers=household_headers,
    )

    _dish_with(
        household_headers,
        "Salat",
        2,
        [{"name": "Cocktailtomaten", "quantity": "500", "unit": "g"}],
    )
    dish = client.get("/v1/dishes", headers=household_headers).json()[0]
    assert dish["computed_cost_minor"] == 150  # eigener Preis, nicht der zugeordnete


def test_removing_an_alias_reopens_the_gap(household_headers: dict[str, str]) -> None:
    client.post(
        "/v1/ingredient-prices",
        json={
            "name": "Tomaten stückig",
            "price_minor": 89,
            "pack_quantity": "400",
            "pack_unit": "g",
        },
        headers=household_headers,
    )
    _dish_with(
        household_headers,
        "Auflauf",
        4,
        [{"name": "Stückige Tomaten", "quantity": "400", "unit": "g"}],
    )
    alias = client.post(
        "/v1/ingredient-aliases",
        json={"alias_name": "Stückige Tomaten", "price_name": "Tomaten stückig"},
        headers=household_headers,
    ).json()
    assert client.get("/v1/dishes/price-gaps", headers=household_headers).json() == []

    removed = client.delete(
        f"/v1/ingredient-aliases/{alias['id']}", headers=household_headers
    )
    assert removed.status_code == 204
    gaps = client.get("/v1/dishes/price-gaps", headers=household_headers).json()
    assert [g["name"] for g in gaps] == ["Stückige Tomaten"]


# ---------------------------------------------------------------------------
# Umrechnung ueber die API
# ---------------------------------------------------------------------------


def test_gap_offers_the_looked_up_conversion(household_headers: dict[str, str]) -> None:
    """Bei einem Einheitenkonflikt schlaegt der Server die fehlende Zahl vor -
    genommen wird sie erst per Bestaetigung."""
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Knoblauch", "price_minor": 129, "pack_quantity": "100", "pack_unit": "g"},
        headers=household_headers,
    )
    _dish_with(
        household_headers,
        "Curry",
        4,
        [{"name": "Knoblauch", "quantity": "4", "unit": "Zehen"}],
    )

    gap = client.get("/v1/dishes/price-gaps", headers=household_headers).json()[0]
    assert gap["kind"] == "unit_mismatch"
    # Der Vorschlag kommt aus der Werteliste im Code, nicht aus der Datenbank -
    # deshalb die schlichte "5" statt der Spaltengenauigkeit "5.0000".
    assert gap["suggested_conversion"] == {
        "unit": "zehe",
        "factor": "5",
        "base_unit": "g",
        "confidence": "plausibel",
    }


def test_saved_conversion_closes_the_gap(household_headers: dict[str, str]) -> None:
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Knoblauch", "price_minor": 129, "pack_quantity": "100", "pack_unit": "g"},
        headers=household_headers,
    )
    dish = _dish_with(
        household_headers,
        "Curry",
        4,
        [{"name": "Knoblauch", "quantity": "4", "unit": "Zehen"}],
    )

    created = client.post(
        "/v1/ingredient-unit-conversions",
        json={
            "ingredient_name": "Knoblauch",
            "unit": "Zehen",
            "factor": "5",
            "base_unit": "g",
            "origin": "default",
        },
        headers=household_headers,
    )
    assert created.status_code == 201
    # Mehrzahl im Rezept, Einzahl in der Tabelle - normalisiert dieselbe Zeile.
    assert created.json()["unit"] == "zehe"

    assert client.get("/v1/dishes/price-gaps", headers=household_headers).json() == []
    ingredient = client.get(
        f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers
    ).json()[0]
    # 4 Zehen = 20 g von 1,29 EUR je 100 g.
    assert ingredient["cost_minor"] == 26
    # Sichtbar geschaetzt: die Zahl beruht auf einem Stueckgewicht.
    assert ingredient["cost_is_estimated"] is True


def test_conversion_refuses_a_second_base_unit(household_headers: dict[str, str]) -> None:
    """Eine Zutat hat genau EINE Basis. Sonst entstuende die Kette
    "1 EL -> 15 ml -> 0,93 g", die bei Widerspruch still falsch aufloest."""
    body = {"ingredient_name": "Sesamöl", "unit": "ml", "factor": "0.93", "base_unit": "g"}
    assert client.post(
        "/v1/ingredient-unit-conversions", json=body, headers=household_headers
    ).status_code == 201

    clash = client.post(
        "/v1/ingredient-unit-conversions",
        json={"ingredient_name": "Sesamöl", "unit": "EL", "factor": "15", "base_unit": "ml"},
        headers=household_headers,
    )
    assert clash.status_code == 409
    assert clash.json()["detail"] == "conversion_base_unit_conflict"


def test_repeated_conversion_updates_instead_of_duplicating(
    household_headers: dict[str, str],
) -> None:
    body = {"ingredient_name": "Knoblauch", "unit": "Zehe", "factor": "5", "base_unit": "g"}
    client.post("/v1/ingredient-unit-conversions", json=body, headers=household_headers)
    client.post(
        "/v1/ingredient-unit-conversions",
        json={**body, "factor": "4"},
        headers=household_headers,
    )
    rows = client.get("/v1/ingredient-unit-conversions", headers=household_headers).json()
    assert len(rows) == 1
    assert rows[0]["factor"] == "4.0000"


def test_bulk_takes_every_suggestion_at_once(household_headers: dict[str, str]) -> None:
    """Der Weg fuer den Rueckstand: siebzig Zutaten einzeln zu bestaetigen
    waere dieselbe Fleissarbeit, die die Werteliste abnehmen soll."""
    res = client.post(
        "/v1/ingredient-unit-conversions/bulk",
        json={
            "items": [
                {"ingredient_name": "Knoblauch", "unit": "Zehe", "factor": "5", "base_unit": "g"},
                {"ingredient_name": "Zwiebeln", "unit": "St", "factor": "80", "base_unit": "g"},
            ]
        },
        headers=household_headers,
    )
    assert res.status_code == 201
    assert [row["unit"] for row in res.json()] == ["zehe", "stk"]


def test_deleting_a_conversion_reopens_the_gap(household_headers: dict[str, str]) -> None:
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Knoblauch", "price_minor": 129, "pack_quantity": "100", "pack_unit": "g"},
        headers=household_headers,
    )
    _dish_with(
        household_headers, "Curry", 4, [{"name": "Knoblauch", "quantity": "4", "unit": "Zehen"}]
    )
    created = client.post(
        "/v1/ingredient-unit-conversions",
        json={"ingredient_name": "Knoblauch", "unit": "Zehe", "factor": "5", "base_unit": "g"},
        headers=household_headers,
    ).json()
    assert client.get("/v1/dishes/price-gaps", headers=household_headers).json() == []

    client.delete(
        f"/v1/ingredient-unit-conversions/{created['id']}", headers=household_headers
    )
    # Wieder eine sichtbare Luecke - besser als eine Zahl, hinter der niemand steht.
    assert len(client.get("/v1/dishes/price-gaps", headers=household_headers).json()) == 1


def test_conversion_follows_a_confirmed_alias(household_headers: dict[str, str]) -> None:
    """Steht die Umrechnung unter "Knoblauch" und das Rezept sagt "Knobi",
    greift sie ueber die bestaetigte Zuordnung trotzdem."""
    client.post(
        "/v1/ingredient-prices",
        json={"name": "Knoblauch", "price_minor": 129, "pack_quantity": "100", "pack_unit": "g"},
        headers=household_headers,
    )
    dish = _dish_with(
        household_headers, "Curry", 4, [{"name": "Knobi", "quantity": "4", "unit": "Zehen"}]
    )
    client.post(
        "/v1/ingredient-aliases",
        json={"alias_name": "Knobi", "price_name": "Knoblauch"},
        headers=household_headers,
    )
    client.post(
        "/v1/ingredient-unit-conversions",
        json={"ingredient_name": "Knoblauch", "unit": "Zehe", "factor": "5", "base_unit": "g"},
        headers=household_headers,
    )

    ingredient = client.get(
        f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers
    ).json()[0]
    assert ingredient["cost_minor"] == 26


def test_conversions_stay_inside_the_household(household_headers: dict[str, str]) -> None:
    client.post(
        "/v1/ingredient-unit-conversions",
        json={"ingredient_name": "Knoblauch", "unit": "Zehe", "factor": "5", "base_unit": "g"},
        headers=household_headers,
    )
    other = _signup(f"p8p-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(other)
    ).json()
    foreign = {**_auth_headers(other), "X-Household-Id": household["id"]}
    assert client.get("/v1/ingredient-unit-conversions", headers=foreign).json() == []
