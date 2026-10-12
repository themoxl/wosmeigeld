"""KI-Vorschlaege der Lebensmittelplanung: Zutaten und Wochenplan.

Kernaussage dieser Suite: die Vorschlaege sind Vorschlaege. Kein Endpunkt hier
schreibt, und was das Modell an Unsinn liefert (erfundene Gerichte, belegte
Slots, kaputte Datumsangaben), darf nicht bis in die Datenbank durchschlagen.
Und: liefert das Modell NICHTS Verwertbares, ist das ein benannter Fehler
(503 mit Reason-Code des KI-Kerns), keine leere Vorschlagsliste.

Ersetzt ist nur der Modellserver (tests/ki_modell_attrappe.py); der Gateway
samt llm_calls-Zeile laeuft echt.
"""

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import grocery_ai_service
from tests.ki_modell_attrappe import (
    MODEL,
    ModelStub,
    answer,
    llm_calls,
    ready_target,
    server_error,
    unavailable_target,
)
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p8ai-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "KI-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def meal_week_id(household_headers: dict[str, str]) -> str:
    week = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-08-03", "budget_minor": 10000},
        headers=household_headers,
    ).json()
    return str(week["id"])


def _stub_model(
    monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any] | httpx.Response
) -> ModelStub:
    """Ersetzt den Modellserver: jede Anfrage bekommt dieselbe Antwort (ein
    dict ist der JSON-Inhalt einer regulaeren Antwort). `stub.user_prompts()`
    zeigt, was das Modell zu sehen bekam."""
    response = reply if isinstance(reply, httpx.Response) else answer(reply)
    return ModelStub(monkeypatch, lambda body: response)


# ---------------------------------------------------------------------------
# Zutaten
# ---------------------------------------------------------------------------


def test_ingredient_suggestion_does_not_write_anything(
    household_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dish = client.post(
        "/v1/dishes",
        json={"name": "Spaghetti Bolognese", "default_servings": 4},
        headers=household_headers,
    ).json()
    _stub_model(
        monkeypatch,
        {
            "ingredients": [
                {"name": "Nudeln", "quantity": "500", "unit": "g", "category": "Trockenware"},
                {"name": "Salz", "quantity": "", "unit": "", "category": "Gewuerze"},
            ]
        },
    )

    res = client.post(
        f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["model_name"] == MODEL
    assert [i["name"] for i in body["ingredients"]] == ["Nudeln", "Salz"]
    # Zutat ohne Menge bleibt ohne Menge - kein stiller Ersatz durch 1.
    assert body["ingredients"][1]["quantity"] is None

    # Entscheidend: der Vorschlag allein hat nichts angelegt.
    assert (
        client.get(f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers).json()
        == []
    )


def test_ingredient_suggestion_marks_and_skips_existing(
    household_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dish = client.post(
        "/v1/dishes", json={"name": "Chili", "default_servings": 4}, headers=household_headers
    ).json()
    client.post(
        "/v1/dish-ingredients",
        json={"dish_id": dish["id"], "name": "Hackfleisch", "quantity": "500", "unit": "g"},
        headers=household_headers,
    )
    stub = _stub_model(
        monkeypatch,
        {
            "ingredients": [
                {"name": "Hackfleisch", "quantity": "500", "unit": "g", "category": "Fleisch"},
                {"name": "Kidneybohnen", "quantity": "400", "unit": "g", "category": "Konserven"},
            ]
        },
    )

    body = client.post(
        f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers
    ).json()
    by_name = {i["name"]: i for i in body["ingredients"]}
    assert by_name["Hackfleisch"]["already_present"] is True
    assert by_name["Kidneybohnen"]["already_present"] is False
    # Das Modell bekommt den Bestand mit, damit es ergaenzt statt wiederholt.
    assert "Hackfleisch" in stub.user_prompts()[0]

    # Uebernehmen legt nur das Neue an, der Bestand wird nicht verdoppelt.
    applied = client.post(
        "/v1/dish-ingredients/bulk",
        json={
            "dish_id": dish["id"],
            "ingredients": [
                {"name": "Hackfleisch", "quantity": "500", "unit": "g"},
                {"name": "Kidneybohnen", "quantity": "400", "unit": "g"},
            ],
        },
        headers=household_headers,
    )
    assert applied.status_code == 201
    assert [i["name"] for i in applied.json()] == ["Kidneybohnen"]
    ingredients = client.get(
        f"/v1/dishes/{dish['id']}/ingredients", headers=household_headers
    ).json()
    assert [i["name"] for i in ingredients] == ["Hackfleisch", "Kidneybohnen"]


def test_ingredient_suggestion_drops_placeholder_names(
    household_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Modell, das nichts weiss, fuellt gern mit "Zutat"/"Sonstiges" auf.
    Als Einkaufsposten waere das wertlos."""
    dish = client.post(
        "/v1/dishes", json={"name": "Phantasiegericht"}, headers=household_headers
    ).json()
    _stub_model(
        monkeypatch,
        {
            "ingredients": [
                {"name": "Zutat", "quantity": "1", "unit": "Stk", "category": ""},
                {"name": "Sonstiges", "quantity": "", "unit": "", "category": ""},
                {"name": "Reis", "quantity": "250", "unit": "g", "category": "Trockenware"},
            ]
        },
    )

    body = client.post(
        f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers
    ).json()
    assert [i["name"] for i in body["ingredients"]] == ["Reis"]


def test_suggestions_report_unavailable_model_as_503(
    household_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Modell laeuft nicht" darf nicht als leere Vorschlagsliste erscheinen."""
    dish = client.post("/v1/dishes", json={"name": "Suppe"}, headers=household_headers).json()
    stub = ModelStub(monkeypatch, lambda body: answer({}), target=unavailable_target())

    res = client.post(f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers)
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail["code"] == "model_unavailable"
    assert "nicht erreichbar" in detail["message"]
    assert stub.bodies == []


async def test_a_model_failure_is_a_named_error_not_an_empty_list(
    household_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund K2: frueher wurde jeder Modellfehler zu HTTP 200 mit
    ingredients=[] - fuer den Nutzer "das Modell kennt das Gericht nicht".
    Jetzt 503 mit dem Grund des Kerns, und der Aufruf steht in llm_calls (die
    Fehlerantwort wird zurueckgegeben, damit das Protokoll nicht mit
    zurueckrollt)."""
    dish = client.post("/v1/dishes", json={"name": "Gulasch"}, headers=household_headers).json()
    _stub_model(monkeypatch, server_error(500, "Modell abgestuerzt"))

    res = client.post(f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers)
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "model_http_error"
    assert "Modell abgestuerzt" in detail["message"]

    calls = await llm_calls(household_headers["X-Household-Id"], "grocery_ingredients")
    assert [(call["status"], call["reason_code"]) for call in calls] == [
        ("error", "model_http_error")
    ]
    assert calls[0]["prompt_version"] == grocery_ai_service.INGREDIENT_PROMPT_VERSION


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        # Abgeschnitten: der Anfang sieht nach JSON aus, ist aber keine Antwort.
        (
            answer('{"ingredients": [{"name": "Rindfl', finish_reason="length"),
            "model_budget_exhausted",
        ),
        (answer("Gulasch braucht Rindfleisch und Zwiebeln."), "model_output_unusable"),
        # JSON, aber ohne Liste unter "ingredients" - Vertragsbruch.
        (answer({"zutaten": ["Rindfleisch"]}), "model_output_unusable"),
    ],
)
async def test_an_unusable_answer_is_named_and_marked_in_the_protocol(
    household_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    reply: httpx.Response,
    code: str,
) -> None:
    dish = client.post("/v1/dishes", json={"name": "Gulasch"}, headers=household_headers).json()
    _stub_model(monkeypatch, reply)

    res = client.post(f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers)
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == code

    calls = await llm_calls(household_headers["X-Household-Id"], "grocery_ingredients")
    assert [(call["status"], call["reason_code"]) for call in calls] == [("error", code)]


def test_an_empty_list_from_the_model_stays_a_valid_answer(
    household_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Prompt erlaubt ausdruecklich "kenne ich nicht" als leere Liste -
    das ist eine Antwort, kein Fehler."""
    dish = client.post(
        "/v1/dishes", json={"name": "Phantasiegericht"}, headers=household_headers
    ).json()
    _stub_model(monkeypatch, {"ingredients": []})

    res = client.post(f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers)
    assert res.status_code == 200, res.text
    assert res.json()["ingredients"] == []
    assert res.json()["model_name"] == MODEL


def test_json_only_in_reasoning_content_is_rescued_from_the_same_answer(
    household_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manche Backends legen das JSON in reasoning_content ab und lassen
    content leer. Es stammt aus demselben Aufruf und darf gerettet werden -
    ohne zweiten, anders konfigurierten Aufruf."""
    dish = client.post("/v1/dishes", json={"name": "Gulasch"}, headers=household_headers).json()
    stub = _stub_model(
        monkeypatch,
        answer(
            "",
            reasoning='Ich liste: {"ingredients": [{"name": "Rindfleisch", "quantity": "800", '
            '"unit": "g", "category": "Fleisch"}]}',
        ),
    )

    res = client.post(f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers)
    assert res.status_code == 200, res.text
    assert [item["name"] for item in res.json()["ingredients"]] == ["Rindfleisch"]
    assert len(stub.bodies) == 1


def test_ai_switched_off_never_contacts_the_model(
    household_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dish = client.post("/v1/dishes", json={"name": "Suppe"}, headers=household_headers).json()
    stub = _stub_model(monkeypatch, {"ingredients": []})
    switched = client.put(
        "/v1/ai/settings", json={"is_enabled": False}, headers=household_headers
    )
    assert switched.status_code == 200, switched.text

    res = client.post(f"/v1/dishes/{dish['id']}/ingredient-suggestions", headers=household_headers)
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "ai_disabled"
    assert stub.discoveries == 0
    assert stub.bodies == []


# ---------------------------------------------------------------------------
# Wochenplan
# ---------------------------------------------------------------------------


def _catalog(household_headers: dict[str, str]) -> dict[str, dict[str, Any]]:
    dishes = {}
    for name, cost in (("Pasta Pesto", 600), ("Pizza Margherita", 900)):
        dishes[name] = client.post(
            "/v1/dishes",
            json={"name": name, "default_servings": 2, "estimated_cost_minor": cost},
            headers=household_headers,
        ).json()
    return dishes


def test_week_plan_computes_costs_from_catalog_not_from_model(
    household_headers: dict[str, str], meal_week_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Das Modell waehlt die Kombination, es beziffert sie nicht. Auch wenn es
    Preise mitschickt, zaehlt ausschliesslich die Katalogschaetzung."""
    dishes = _catalog(household_headers)
    _stub_model(
        monkeypatch,
        {
            "meals": [
                {
                    "meal_date": "2026-08-03",
                    "meal_type": "dinner",
                    "dish_id": dishes["Pasta Pesto"]["id"],
                    "servings": 4,
                    "reason": "guenstig",
                    "estimated_cost_minor": 99999,  # wird ignoriert
                },
                {
                    "meal_date": "2026-08-04",
                    "meal_type": "dinner",
                    "dish_id": dishes["Pizza Margherita"]["id"],
                    "servings": 2,
                    "reason": "italienisch",
                },
            ]
        },
    )

    body = client.post(
        f"/v1/meal-weeks/{meal_week_id}/plan-suggestions",
        json={"wish": "italienisch und guenstig", "meal_types": ["dinner"], "servings": 2},
        headers=household_headers,
    ).json()

    # Pasta: 6,00 EUR fuer 2 Portionen -> 4 Portionen = 12,00 EUR.
    # Pizza: 9,00 EUR fuer 2 Portionen -> 2 Portionen = 9,00 EUR.
    assert [m["estimated_cost_minor"] for m in body["meals"]] == [1200, 900]
    assert body["estimated_total_minor"] == 2100
    assert body["budget_minor"] == 10000
    assert body["discarded_suggestions"] == 0

    # Auch der Wochenplan schreibt beim Vorschlagen nichts.
    assert (
        client.get(f"/v1/meals?meal_week_id={meal_week_id}", headers=household_headers).json()
        == []
    )


def test_week_plan_discards_invented_dishes_and_foreign_slots(
    household_headers: dict[str, str], meal_week_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne response_format kann das Modell alles liefern. Die zweite
    Verteidigungslinie hinter dem Schema-Enum muss halten."""
    dishes = _catalog(household_headers)
    _stub_model(
        monkeypatch,
        {
            "meals": [
                # Erfundenes Gericht - nicht im Katalog.
                {
                    "meal_date": "2026-08-03",
                    "meal_type": "dinner",
                    "dish_id": str(uuid.uuid4()),
                    "servings": 2,
                    "reason": "erfunden",
                },
                # Tag ausserhalb der Woche.
                {
                    "meal_date": "2026-09-01",
                    "meal_type": "dinner",
                    "dish_id": dishes["Pasta Pesto"]["id"],
                    "servings": 2,
                    "reason": "falsche Woche",
                },
                # Nicht angeforderte Mahlzeit.
                {
                    "meal_date": "2026-08-04",
                    "meal_type": "breakfast",
                    "dish_id": dishes["Pasta Pesto"]["id"],
                    "servings": 2,
                    "reason": "nicht gewuenscht",
                },
                # Gueltig.
                {
                    "meal_date": "2026-08-05",
                    "meal_type": "dinner",
                    "dish_id": dishes["Pizza Margherita"]["id"],
                    "servings": 2,
                    "reason": "passt",
                },
                # Slot doppelt belegt.
                {
                    "meal_date": "2026-08-05",
                    "meal_type": "dinner",
                    "dish_id": dishes["Pasta Pesto"]["id"],
                    "servings": 2,
                    "reason": "doppelt",
                },
            ]
        },
    )

    body = client.post(
        f"/v1/meal-weeks/{meal_week_id}/plan-suggestions",
        json={"meal_types": ["dinner"]},
        headers=household_headers,
    ).json()

    assert len(body["meals"]) == 1
    assert body["meals"][0]["dish_name"] == "Pizza Margherita"
    # Verworfenes wird gezaehlt, nicht verschwiegen.
    assert body["discarded_suggestions"] == 4


def test_week_plan_skips_days_that_are_already_planned(
    household_headers: dict[str, str], meal_week_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    dishes = _catalog(household_headers)
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-03",
            "meal_type": "dinner",
            "dish_name": "Handgeplant",
        },
        headers=household_headers,
    )
    stub = _stub_model(
        monkeypatch,
        {
            "meals": [
                {
                    "meal_date": "2026-08-03",
                    "meal_type": "dinner",
                    "dish_id": dishes["Pasta Pesto"]["id"],
                    "servings": 2,
                    "reason": "belegt",
                }
            ]
        },
    )

    body = client.post(
        f"/v1/meal-weeks/{meal_week_id}/plan-suggestions",
        json={"meal_types": ["dinner"], "skip_planned_days": True},
        headers=household_headers,
    ).json()

    # Der belegte Montag steht nicht in der Liste der zu planenden Tage (der
    # Wochenstart selbst wird weiter oben im Prompt genannt) ...
    assert "2026-08-03 (Montag)" not in stub.user_prompts()[0]
    assert "2026-08-04 (Dienstag)" in stub.user_prompts()[0]
    # ... und ein Vorschlag darauf wird verworfen statt zu ueberschreiben.
    assert body["meals"] == []
    assert body["discarded_suggestions"] == 1


def test_week_plan_needs_a_catalog(
    household_headers: dict[str, str], meal_week_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Gerichte gibt es nichts zu kombinieren - eigene Kennung statt
    eines nichtssagenden leeren Plans."""
    _stub_model(monkeypatch, {"meals": []})
    res = client.post(
        f"/v1/meal-weeks/{meal_week_id}/plan-suggestions", json={}, headers=household_headers
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "dish_catalog_empty"


async def test_week_plan_model_failure_is_named_not_an_empty_plan(
    household_headers: dict[str, str], meal_week_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befund K2: ein Modellfehler war ein Plan mit meals=[] und model_name -
    also "nichts passt". Jetzt 503 mit Grund."""
    _catalog(household_headers)
    _stub_model(monkeypatch, server_error(500, "Modell abgestuerzt"))

    res = client.post(
        f"/v1/meal-weeks/{meal_week_id}/plan-suggestions",
        json={"meal_types": ["dinner"]},
        headers=household_headers,
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["code"] == "model_http_error"
    calls = await llm_calls(household_headers["X-Household-Id"], "grocery_week_plan")
    assert [(call["status"], call["reason_code"]) for call in calls] == [
        ("error", "model_http_error")
    ]


def _bulky_catalog(household_headers: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Zwei Gerichte mit ausufernder Kategorie: die volle Katalogfassung ist
    um ein Vielfaches groesser als die knappe (ID, Name, Preis)."""
    dishes = {}
    for name in ("Pasta Pesto", "Pizza Margherita"):
        dishes[name] = client.post(
            "/v1/dishes",
            json={
                "name": name,
                "default_servings": 2,
                "estimated_cost_minor": 700,
                "category": "Italienisch, vegetarisch, schnell " * 200,
            },
            headers=household_headers,
        ).json()
    return dishes


def test_week_plan_sends_the_compact_catalog_when_the_full_one_does_not_fit(
    household_headers: dict[str, str], meal_week_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passt der volle Katalog samt Antwortreserve nicht ins geladene Fenster,
    geht die knappe Fassung raus - jedes Gericht bleibt drin, nur die
    beschreibenden Angaben fallen weg. Nichts wird still abgeschnitten."""
    dishes = _bulky_catalog(household_headers)
    stub = ModelStub(
        monkeypatch, lambda body: answer({"meals": []}), target=ready_target(window=2048)
    )

    res = client.post(
        f"/v1/meal-weeks/{meal_week_id}/plan-suggestions",
        json={"meal_types": ["dinner"]},
        headers=household_headers,
    )
    assert res.status_code == 200, res.text
    assert len(stub.bodies) == 1
    sent = stub.user_prompts()[0]
    assert "Italienisch" not in sent
    for dish in dishes.values():
        assert dish["id"] in sent
    assert "ca. 7.00 EUR" in sent


async def test_week_plan_too_large_for_the_window_is_refused_with_a_breakdown(
    household_headers: dict[str, str], meal_week_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passt auch die knappe Fassung nicht, wird nicht gesendet:
    model_input_too_large mit Aufschluesselung, Protokollzeile 'refused'."""
    _bulky_catalog(household_headers)
    stub = ModelStub(
        monkeypatch, lambda body: answer({"meals": []}), target=ready_target(window=400)
    )

    res = client.post(
        f"/v1/meal-weeks/{meal_week_id}/plan-suggestions",
        json={"meal_types": ["dinner"]},
        headers=household_headers,
    )
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "model_input_too_large"
    assert "Fenster 400 Token" in detail["message"]
    assert stub.bodies == []
    calls = await llm_calls(household_headers["X-Household-Id"], "grocery_week_plan")
    assert [(call["status"], call["reason_code"]) for call in calls] == [
        ("refused", "model_input_too_large")
    ]


def test_applying_a_plan_never_overwrites_existing_meals(
    household_headers: dict[str, str], meal_week_id: str
) -> None:
    dishes = _catalog(household_headers)
    client.post(
        "/v1/meals",
        json={
            "meal_week_id": meal_week_id,
            "meal_date": "2026-08-03",
            "meal_type": "dinner",
            "dish_name": "Handgeplant",
        },
        headers=household_headers,
    )

    res = client.post(
        "/v1/meals/bulk",
        json={
            "meal_week_id": meal_week_id,
            "meals": [
                {
                    "meal_date": "2026-08-03",
                    "meal_type": "dinner",
                    "dish_name": "Pasta Pesto",
                    "dish_id": dishes["Pasta Pesto"]["id"],
                    "servings": 2,
                },
                {
                    "meal_date": "2026-08-04",
                    "meal_type": "dinner",
                    "dish_name": "Pizza Margherita",
                    "dish_id": dishes["Pizza Margherita"]["id"],
                    "servings": 2,
                    "estimated_cost_minor": 900,
                },
            ],
        },
        headers=household_headers,
    )
    assert res.status_code == 201
    assert [m["dish_name"] for m in res.json()] == ["Pizza Margherita"]

    meals = client.get(f"/v1/meals?meal_week_id={meal_week_id}", headers=household_headers).json()
    assert [m["dish_name"] for m in meals] == ["Handgeplant", "Pizza Margherita"]


def test_bulk_meals_reject_dish_from_other_household(
    household_headers: dict[str, str], meal_week_id: str
) -> None:
    other_token = _signup(f"p8ai-x-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-3")
    other_household = client.post(
        "/v1/households", json={"name": "Fremd"}, headers=_auth_headers(other_token)
    ).json()
    other_headers = {**_auth_headers(other_token), "X-Household-Id": other_household["id"]}
    foreign = client.post(
        "/v1/dishes", json={"name": "Fremdgericht"}, headers=other_headers
    ).json()

    res = client.post(
        "/v1/meals/bulk",
        json={
            "meal_week_id": meal_week_id,
            "meals": [
                {
                    "meal_date": "2026-08-03",
                    "meal_type": "dinner",
                    "dish_name": "Fremdgericht",
                    "dish_id": foreign["id"],
                    "servings": 2,
                }
            ],
        },
        headers=household_headers,
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "dish_not_found"
    assert (
        client.get(f"/v1/meals?meal_week_id={meal_week_id}", headers=household_headers).json()
        == []
    )
