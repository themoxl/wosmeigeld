"""API-Endpunkte fuer Essensplan, Einkaufsliste und Wochenbudget (Phase 8)."""

from collections import defaultdict
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session, require_household_editor
from app.schemas.grocery import (
    DishCreate,
    DishIngredientBulkCreate,
    DishIngredientCreate,
    DishIngredientResponse,
    DishIngredientUpdate,
    DishResponse,
    DishUpdate,
    IngredientAliasCreate,
    IngredientAliasResponse,
    IngredientPriceCreate,
    IngredientPriceResponse,
    IngredientPriceUpdate,
    IngredientSuggestionResult,
    LinkedTransaction,
    MealBulkCreate,
    MealCreate,
    MealResponse,
    MealUpdate,
    MealWeekCreate,
    MealWeekResponse,
    MealWeekSummaryResponse,
    MealWeekUpdate,
    PriceGapDish,
    PriceGapResponse,
    PurchaseLinkCreate,
    PurchaseLinkResponse,
    ShoppingItemCreate,
    ShoppingItemResponse,
    ShoppingItemUpdate,
    ShoppingListCreate,
    ShoppingListFillResult,
    ShoppingListResponse,
    ShoppingListUpdate,
    SuggestedConversionResponse,
    UnitConversionBulkCreate,
    UnitConversionCreate,
    UnitConversionResponse,
    WeekPlanSuggestionInput,
    WeekPlanSuggestionResult,
)
from app.services import grocery_ai_service, grocery_pricing_service
from app.services.ai_answers import AIStepError
from app.services.grocery_service import (
    collect_week_ingredients,
    fetch_meal_week_summary,
    fetch_shopping_list_totals,
)

router = APIRouter(tags=["grocery"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Schreibende Endpunkte: 403 statt stillem Nichtstun, wenn die Rolle nicht
# schreiben darf (siehe app/auth/dependencies.py).
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]

_MEAL_WEEK_COLUMNS = "id, week_start, budget_minor, note, created_at"
_DISH_COLUMNS = (
    "id, name, default_meal_type, default_servings, estimated_cost_minor, "
    "category, note, created_at, updated_at"
)
_MEAL_COLUMNS = (
    "id, meal_week_id, meal_date, meal_type, dish_name, dish_id, servings, "
    "estimated_cost_minor, note, created_at, updated_at"
)
_SHOPPING_LIST_COLUMNS = "id, meal_week_id, store, status, created_at, updated_at"
_SHOPPING_ITEM_COLUMNS = (
    "id, shopping_list_id, meal_id, name, category, quantity, unit, "
    "planned_price_minor, actual_price_minor, is_purchased, note, from_meal_plan, "
    "created_at, updated_at"
)


async def _assert_meal_week_in_household(
    session: AsyncSession, meal_week_id: UUID, household_id: UUID
) -> None:
    row = (
        await session.execute(
            text("select 1 from meal_weeks where id = :id and household_id = :household_id"),
            {"id": str(meal_week_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meal_week_not_found")


async def _assert_shopping_list_in_household(
    session: AsyncSession, shopping_list_id: UUID, household_id: UUID
) -> None:
    row = (
        await session.execute(
            text("select 1 from shopping_lists where id = :id and household_id = :household_id"),
            {"id": str(shopping_list_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="shopping_list_not_found")


async def _assert_meal_in_household(
    session: AsyncSession, meal_id: UUID, household_id: UUID
) -> None:
    row = (
        await session.execute(
            text("select 1 from meals where id = :id and household_id = :household_id"),
            {"id": str(meal_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meal_not_found")


async def _assert_dish_in_household(
    session: AsyncSession, dish_id: UUID, household_id: UUID
) -> None:
    row = (
        await session.execute(
            text("select 1 from dishes where id = :id and household_id = :household_id"),
            {"id": str(dish_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dish_not_found")


def _to_meal_week_response(row: Any) -> MealWeekResponse:
    return MealWeekResponse(**row._mapping)


def _to_meal_response(row: Any) -> MealResponse:
    return MealResponse(**row._mapping)


def _to_shopping_item_response(row: Any) -> ShoppingItemResponse:
    return ShoppingItemResponse(**row._mapping)


async def _to_shopping_list_response(
    session: AsyncSession, household_id: UUID, row: Any
) -> ShoppingListResponse:
    planned_total_minor, actual_total_minor, linked_transactions = await fetch_shopping_list_totals(
        session, household_id, row.id
    )
    return ShoppingListResponse(
        **row._mapping,
        planned_total_minor=planned_total_minor,
        actual_total_minor=actual_total_minor,
        linked_transactions=linked_transactions,
    )


# --- Essensplan: Wochen ---
@router.get("/meal-weeks", response_model=list[MealWeekResponse])
async def list_meal_weeks(session_ctx: HouseholdSession) -> list[MealWeekResponse]:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            f"select {_MEAL_WEEK_COLUMNS} from meal_weeks "
            "where household_id = :household_id order by week_start desc"
        ),
        {"household_id": str(household_id)},
    )
    return [_to_meal_week_response(row) for row in res]


@router.post("/meal-weeks", response_model=MealWeekResponse, status_code=status.HTTP_201_CREATED)
async def create_meal_week(
    payload: MealWeekCreate, session_ctx: HouseholdEditorSession
) -> MealWeekResponse:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            "insert into meal_weeks (household_id, week_start, budget_minor, note) "
            "values (:household_id, :week_start, :budget, :note) "
            f"returning {_MEAL_WEEK_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "week_start": payload.week_start,
            "budget": payload.budget_minor,
            "note": payload.note,
        },
    )
    return _to_meal_week_response(res.one())


@router.patch("/meal-weeks/{meal_week_id}", response_model=MealWeekResponse)
async def update_meal_week(
    meal_week_id: UUID, payload: MealWeekUpdate, session_ctx: HouseholdEditorSession
) -> MealWeekResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        row = (
            await db.execute(
                text(
                    f"select {_MEAL_WEEK_COLUMNS} from meal_weeks "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(meal_week_id), "household_id": str(household_id)},
            )
        ).first()
    else:
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        updates["id"] = str(meal_week_id)
        updates["household_id"] = str(household_id)
        row = (
            await db.execute(
                text(
                    f"update meal_weeks set {set_clause} "
                    "where id = :id and household_id = :household_id "
                    f"returning {_MEAL_WEEK_COLUMNS}"
                ),
                updates,
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meal_week_not_found")
    return _to_meal_week_response(row)


@router.delete("/meal-weeks/{meal_week_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal_week(meal_week_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    db, household_id = session_ctx
    # 'returning id' + Pruefung: ohne sie antwortet ein DELETE, das keine Zeile
    # getroffen hat (fremder Haushalt, bereits geloescht), mit 204 - das
    # Frontend rendert dann eine Loeschung, die nie stattgefunden hat.
    deleted = (
        await db.execute(
            text(
                "delete from meal_weeks where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(meal_week_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meal_week_not_found")


@router.get("/meal-weeks/{meal_week_id}/summary", response_model=MealWeekSummaryResponse)
async def get_meal_week_summary(
    meal_week_id: UUID, session_ctx: HouseholdSession
) -> MealWeekSummaryResponse:
    db, household_id = session_ctx
    summary = await fetch_meal_week_summary(db, household_id, meal_week_id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meal_week_not_found")
    return summary


# --- Gerichte-Katalog ---
# usage_count zaehlt die geplanten Mahlzeiten, die aus diesem Katalogeintrag
# entstanden sind. Als Unterabfrage statt als Join+group by, damit die
# Spaltenliste unveraendert bleibt und Gerichte ohne Verwendung sauber mit 0
# herauskommen.
_DISH_SELECT_COLUMNS = (
    "d.id, d.name, d.default_meal_type, d.default_servings, d.estimated_cost_minor, "
    "d.category, d.note, d.created_at, d.updated_at, "
    "(select count(*) from meals m where m.dish_id = d.id)::int as usage_count, "
    "(select count(*) from dish_ingredients i where i.dish_id = d.id)::int as ingredient_count"
)

_DISH_INGREDIENT_COLUMNS = (
    "id, dish_id, name, quantity, unit, category, note, sort_order, created_at, updated_at"
)


def _to_dish_response(
    row: Any, costs: dict[UUID, grocery_pricing_service.DishCost]
) -> DishResponse:
    """Baut die Antwort samt Kostenbild.

    effective_cost_minor haelt die Vorrangregel an EINER Stelle fest: eine von
    Hand eingetragene Schaetzung uebersteuert die Berechnung, solange sie
    dasteht. Das Frontend liest den Wert, statt die Regel nachzubauen."""
    cost = costs.get(row.id)
    computed = cost.computed_cost_minor if cost and cost.priced_ingredients > 0 else None
    effective = row.estimated_cost_minor if row.estimated_cost_minor is not None else computed
    return DishResponse(
        **row._mapping,
        computed_cost_minor=computed,
        unpriced_ingredients=cost.unpriced_ingredients if cost else 0,
        priced_ingredients=cost.priced_ingredients if cost else 0,
        unquantified_ingredients=cost.unquantified_ingredients if cost else 0,
        effective_cost_minor=effective,
        # Ganze Cent: eine Portion mit halben Cent zu beziffern taeuscht eine
        # Genauigkeit vor, die eine Schaetzung nicht hat.
        cost_per_serving_minor=(
            None if effective is None else round(effective / row.default_servings)
        ),
    )


async def _fetch_dish(
    session: AsyncSession, household_id: UUID, dish_id: UUID
) -> DishResponse | None:
    row = (
        await session.execute(
            text(
                f"select {_DISH_SELECT_COLUMNS} from dishes d "
                "where d.id = :id and d.household_id = :household_id"
            ),
            {"id": str(dish_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return None
    costs = await grocery_pricing_service.load_dish_costs(session, household_id)
    return _to_dish_response(row, costs)


@router.get("/dishes", response_model=list[DishResponse])
async def list_dishes(session_ctx: HouseholdSession) -> list[DishResponse]:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            f"select {_DISH_SELECT_COLUMNS} from dishes d "
            "where d.household_id = :household_id order by lower(d.name) asc"
        ),
        {"household_id": str(household_id)},
    )
    rows = res.all()
    costs = await grocery_pricing_service.load_dish_costs(db, household_id)
    return [_to_dish_response(row, costs) for row in rows]


@router.get("/dishes/price-gaps", response_model=list[PriceGapResponse])
async def list_price_gaps(session_ctx: HouseholdSession) -> list[PriceGapResponse]:
    """Zutaten mit Menge, aber ohne verwertbaren Preis - nach Zutat gruppiert.

    Der Arbeitsvorrat zur unvollstaendigen Berechnung. Nach Hebel sortiert:
    oben steht, was in den meisten Gerichten steckt, weil eine Zutat einmal
    zu bepreisen jedes Gericht repariert, das sie verwendet.
    """
    db, household_id = session_ctx
    gaps = await grocery_pricing_service.load_price_gaps(db, household_id)
    return [
        PriceGapResponse(
            key=gap.key,
            name=gap.name,
            kind="unit_mismatch" if gap.kind == "unit_mismatch" else "no_price",
            dishes=[
                PriceGapDish(
                    dish_id=dish.dish_id,
                    dish_name=dish.dish_name,
                    quantity=dish.quantity,
                    unit=dish.unit,
                )
                for dish in gap.dishes
            ],
            dish_count=len(gap.dishes),
            planned_count=gap.planned_count,
            recipe_unit=gap.recipe_unit,
            price_id=gap.price_id,
            price_name=gap.price_name,
            price_minor=gap.price_minor,
            price_pack_quantity=gap.price_pack_quantity,
            price_pack_unit=gap.price_pack_unit,
            suggested_price_name=gap.suggested_price_name,
            suggested_conversion=(
                SuggestedConversionResponse(
                    unit=gap.suggested_conversion.unit,
                    factor=gap.suggested_conversion.factor,
                    base_unit=gap.suggested_conversion.base_unit,  # type: ignore[arg-type]
                    confidence=gap.suggested_conversion.confidence,  # type: ignore[arg-type]
                )
                if gap.suggested_conversion is not None
                else None
            ),
        )
        for gap in gaps
    ]


@router.post(
    "/dishes/seed-defaults", response_model=dict[str, int], status_code=status.HTTP_201_CREATED
)
async def seed_default_dishes(session_ctx: HouseholdEditorSession) -> dict[str, int]:
    session, household_id = session_ctx
    defaults: list[dict[str, Any]] = [
        {
            "name": "Nudeln mit Hackfleischsoße", "category": "meat", "default_meal_type": "dinner",
            "ingredients": [
                {"name": "Spaghetti", "amount": 500, "unit": "g"},
                {"name": "Hackfleisch gemischt", "amount": 500, "unit": "g"},
                {"name": "Stückige Tomaten (Dose)", "amount": 800, "unit": "g"},
                {"name": "Zwiebeln", "amount": 2, "unit": "Stück"}
            ]
        },
        {
            "name": "Chili con Carne", "category": "meat", "default_meal_type": "dinner",
            "ingredients": [
                {"name": "Hackfleisch gemischt", "amount": 500, "unit": "g"},
                {"name": "Kidneybohnen (Dose)", "amount": 400, "unit": "g"},
                {"name": "Mais (Dose)", "amount": 285, "unit": "g"},
                {"name": "Passierte Tomaten", "amount": 500, "unit": "g"}
            ]
        },
        {
            "name": "Schnitzel mit Kartoffelsalat", "category": "meat",
            "default_meal_type": "dinner",
            "ingredients": [
                {"name": "Schweineschnitzel", "amount": 4, "unit": "Stück"},
                {"name": "Kartoffeln", "amount": 1000, "unit": "g"},
                {"name": "Zwiebeln", "amount": 1, "unit": "Stück"}
            ]
        },
        {
            "name": "Pommes frites", "category": "vegetarian", "default_meal_type": "snack",
            "ingredients": [
                {"name": "Pommes frites (TK)", "amount": 1000, "unit": "g"}
            ]
        },
        {
            "name": "Pfannkuchen", "category": "vegetarian", "default_meal_type": "lunch",
            "ingredients": [
                {"name": "Mehl", "amount": 400, "unit": "g"},
                {"name": "Eier", "amount": 4, "unit": "Stück"},
                {"name": "Milch", "amount": 500, "unit": "ml"}
            ]
        },
        {
            "name": "Pizza Margherita", "category": "vegetarian", "default_meal_type": "dinner",
            "ingredients": [
                {"name": "Pizzateig (Rolle)", "amount": 1, "unit": "Stück"},
                {"name": "Passierte Tomaten", "amount": 300, "unit": "g"},
                {"name": "Mozzarella", "amount": 250, "unit": "g"}
            ]
        },
        {
            "name": "Linsensuppe", "category": "meat", "default_meal_type": "lunch",
            "ingredients": [
                {"name": "Linsen", "amount": 500, "unit": "g"},
                {"name": "Suppengrün", "amount": 1, "unit": "Bund"},
                {"name": "Wiener Würstchen", "amount": 4, "unit": "Stück"}
            ]
        },
        {
            "name": "Käsespätzle", "category": "vegetarian", "default_meal_type": "dinner",
            "ingredients": [
                {"name": "Spätzle", "amount": 500, "unit": "g"},
                {"name": "Emmentaler gerieben", "amount": 200, "unit": "g"},
                {"name": "Röstzwiebeln", "amount": 100, "unit": "g"}
            ]
        },
    ]
    
    count = 0
    for d in defaults:
        check_query = text(
            "SELECT id FROM dishes "
            "WHERE household_id = :household_id AND lower(name) = lower(:name)"
        )
        res = await session.execute(check_query, {"household_id": household_id, "name": d["name"]})
        row = res.fetchone()
        dish_id = row[0] if row else None
        
        if not dish_id:
            # BEWUSST ohne estimated_cost_minor: die Spalte ist eine
            # Uebersteuerung der Berechnung, keine Startzahl. Ein Seeder, der
            # sie setzt, verankert in jedem neuen Haushalt acht Handwerte, die
            # nie wieder verschwinden - die Preisliste kaeme dann nie zum Zug,
            # egal wie gut sie gepflegt ist. Ohne Wert rechnen die Gerichte,
            # sobald Preise da sind, und bis dahin weist die Preisluecken-
            # Ansicht aus, was fehlt.
            insert_query = text("""
                INSERT INTO dishes
                    (household_id, name, default_meal_type, category, default_servings)
                VALUES (:household_id, :name, :default_meal_type, :category, 2)
                RETURNING id
            """)
            res = await session.execute(insert_query, {
                "household_id": household_id,
                "name": d["name"],
                "default_meal_type": d["default_meal_type"],
                "category": d["category"]
            })
            inserted = res.fetchone()
            assert inserted is not None  # gerade eingefuegt
            dish_id = inserted[0]
            count += 1
            
            # Insert ingredients
            for ing in d["ingredients"]:
                ing_query = text("""
                    INSERT INTO dish_ingredients (household_id, dish_id, name, quantity, unit)
                    VALUES (:household_id, :dish_id, :name, :quantity, :unit)
                """)
                await session.execute(ing_query, {
                    "household_id": household_id,
                    "dish_id": dish_id,
                    "name": ing["name"],
                    "quantity": ing["amount"],
                    "unit": ing["unit"]
                })
            
    return {"seeded": count}



@router.post("/dishes", response_model=DishResponse, status_code=status.HTTP_201_CREATED)
async def create_dish(payload: DishCreate, session_ctx: HouseholdEditorSession) -> DishResponse:
    db, household_id = session_ctx
    try:
        res = await db.execute(
            text(
                "insert into dishes (household_id, name, default_meal_type, default_servings, "
                "estimated_cost_minor, category, note) "
                "values (:household_id, :name, :default_meal_type, :default_servings, "
                ":estimated, :category, :note) "
                "returning id"
            ),
            {
                "household_id": str(household_id),
                "name": payload.name.strip(),
                "default_meal_type": payload.default_meal_type,
                "default_servings": payload.default_servings,
                "estimated": payload.estimated_cost_minor,
                "category": payload.category,
                "note": payload.note,
            },
        )
    except IntegrityError as exc:
        # dishes_household_name_idx (unique auf lower(name)): ein zweiter
        # Eintrag desselben Gerichts waere kein Katalog mehr.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="dish_name_already_exists"
        ) from exc

    dish = await _fetch_dish(db, household_id, res.one().id)
    assert dish is not None  # gerade eingefuegt
    return dish


@router.patch("/dishes/{dish_id}", response_model=DishResponse)
async def update_dish(
    dish_id: UUID, payload: DishUpdate, session_ctx: HouseholdEditorSession
) -> DishResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        updates["name"] = str(updates["name"]).strip()
    if updates:
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        params = {**updates, "id": str(dish_id), "household_id": str(household_id)}
        try:
            row = (
                await db.execute(
                    text(
                        f"update dishes set {set_clause}, updated_at = now() "
                        "where id = :id and household_id = :household_id returning id"
                    ),
                    params,
                )
            ).first()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="dish_name_already_exists"
            ) from exc
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dish_not_found")

    dish = await _fetch_dish(db, household_id, dish_id)
    if dish is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dish_not_found")
    return dish


@router.delete("/dishes/{dish_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dish(dish_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    """Loescht nur den Katalogeintrag. Bereits geplante Mahlzeiten bleiben
    unveraendert bestehen (meals.dish_id ist 'on delete set null', der
    kopierte dish_name bleibt stehen) - ein aufgeraeumter Katalog darf keine
    vergangenen Wochenplaene umschreiben."""
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text("delete from dishes where id = :id and household_id = :household_id returning id"),
            {"id": str(dish_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dish_not_found")


# --- Zutaten eines Gerichts ---
def _to_ingredient_response(
    row: Any,
    prices: dict[str, grocery_pricing_service.PriceEntry],
    conversions: dict[str, grocery_pricing_service.Conversion] | None = None,
) -> DishIngredientResponse:
    key = row.name.strip().lower()
    price = prices.get(key)
    result = grocery_pricing_service.cost_detail(
        row.quantity, row.unit, price, (conversions or {}).get(key)
    )
    cost = None if result is None else result.minor
    # Einheitenkonflikt heisst: Preis vorhanden, Menge vorhanden, trotzdem
    # keine Kosten. Nur dann - eine fehlende Menge ("Salz") ist kein Konflikt.
    unit_mismatch = price is not None and row.quantity is not None and cost is None
    return DishIngredientResponse(
        **row._mapping,
        cost_minor=cost,
        has_price=price is not None,
        unit_mismatch=unit_mismatch,
        cost_is_estimated=result is not None and result.used_conversion,
    )


@router.get("/dishes/{dish_id}/ingredients", response_model=list[DishIngredientResponse])
async def list_dish_ingredients(
    dish_id: UUID, session_ctx: HouseholdSession
) -> list[DishIngredientResponse]:
    db, household_id = session_ctx
    await _assert_dish_in_household(db, dish_id, household_id)
    res = await db.execute(
        text(
            f"select {_DISH_INGREDIENT_COLUMNS} from dish_ingredients "
            "where household_id = :household_id and dish_id = :dish_id "
            "order by sort_order asc, created_at asc"
        ),
        {"household_id": str(household_id), "dish_id": str(dish_id)},
    )
    rows = res.all()
    prices = await grocery_pricing_service.load_price_lookup(db, household_id)
    conversions = await grocery_pricing_service.load_conversions(db, household_id)
    return [_to_ingredient_response(row, prices, conversions) for row in rows]


# --- Zutaten-Preisliste ---
_PRICE_SELECT_COLUMNS = (
    "p.id, p.name, p.price_minor, p.pack_quantity, p.pack_unit, p.category, p.note, "
    "p.created_at, p.updated_at, "
    # Herkunft der Zahl, direkt in der Liste: sonst braeuchte die Uebersicht
    # eine Zusatzabfrage je Zeile, nur um "von Hand" von "abgeleitet" zu
    # unterscheiden - und genau das entscheidet, ob ein Import ueberhaupt
    # durchschlaegt.
    "p.price_source, p.price_observed_on, p.price_is_stale, "
    "(select count(distinct i.dish_id) from dish_ingredients i "
    " where i.household_id = p.household_id and lower(i.name) = lower(p.name))::int "
    "as used_in_dishes"
)


async def _fetch_price(
    session: AsyncSession, household_id: UUID, price_id: UUID
) -> IngredientPriceResponse | None:
    row = (
        await session.execute(
            text(
                f"select {_PRICE_SELECT_COLUMNS} from ingredient_prices p "
                "where p.id = :id and p.household_id = :household_id"
            ),
            {"id": str(price_id), "household_id": str(household_id)},
        )
    ).first()
    return None if row is None else IngredientPriceResponse(**row._mapping)


@router.get("/ingredient-prices", response_model=list[IngredientPriceResponse])
async def list_ingredient_prices(session_ctx: HouseholdSession) -> list[IngredientPriceResponse]:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            f"select {_PRICE_SELECT_COLUMNS} from ingredient_prices p "
            "where p.household_id = :household_id order by lower(p.name) asc"
        ),
        {"household_id": str(household_id)},
    )
    return [IngredientPriceResponse(**row._mapping) for row in res]


@router.post(
    "/ingredient-prices",
    response_model=IngredientPriceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ingredient_price(
    payload: IngredientPriceCreate, session_ctx: HouseholdEditorSession
) -> IngredientPriceResponse:
    db, household_id = session_ctx
    try:
        res = await db.execute(
            text(
                "insert into ingredient_prices (household_id, name, price_minor, pack_quantity, "
                "pack_unit, category, note) values (:household_id, :name, :price, :pack_quantity, "
                ":pack_unit, :category, :note) returning id"
            ),
            {
                "household_id": str(household_id),
                "name": payload.name.strip(),
                "price": payload.price_minor,
                "pack_quantity": payload.pack_quantity,
                "pack_unit": payload.pack_unit,
                "category": payload.category,
                "note": payload.note,
            },
        )
    except IntegrityError as exc:
        # Zwei Preise fuer dieselbe Zutat waeren genau das, was diese Liste
        # verhindern soll: derselbe Artikel, zwei Wahrheiten.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="ingredient_price_already_exists"
        ) from exc

    price = await _fetch_price(db, household_id, res.one().id)
    assert price is not None  # gerade eingefuegt
    return price


@router.patch("/ingredient-prices/{price_id}", response_model=IngredientPriceResponse)
async def update_ingredient_price(
    price_id: UUID, payload: IngredientPriceUpdate, session_ctx: HouseholdEditorSession
) -> IngredientPriceResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        updates["name"] = str(updates["name"]).strip()
    if updates:
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        params = {**updates, "id": str(price_id), "household_id": str(household_id)}
        try:
            row = (
                await db.execute(
                    text(
                        f"update ingredient_prices set {set_clause}, updated_at = now() "
                        "where id = :id and household_id = :household_id returning id"
                    ),
                    params,
                )
            ).first()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="ingredient_price_already_exists"
            ) from exc
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="ingredient_price_not_found"
            )

    price = await _fetch_price(db, household_id, price_id)
    if price is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ingredient_price_not_found"
        )
    return price


@router.delete("/ingredient-prices/{price_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ingredient_price(
    price_id: UUID, session_ctx: HouseholdEditorSession
) -> None:
    """Entfernt nur den Preis. Die Zutat bleibt in jedem Rezept stehen, das
    Gericht ist danach lediglich wieder unbewertet."""
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text(
                "delete from ingredient_prices where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(price_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ingredient_price_not_found"
        )


# --- Zugeordnete Schreibweisen ---
# Bruecke zwischen einem Rezeptnamen und einer Preiszeile, die anders heisst.
# Der Server schlaegt Kandidaten vor (siehe suggest_price_match), angelegt
# wird eine Zuordnung aber nur hier - also nachdem jemand sie bestaetigt hat.
_ALIAS_COLUMNS = "id, alias_name, price_name, created_at"


@router.get("/ingredient-aliases", response_model=list[IngredientAliasResponse])
async def list_ingredient_aliases(
    session_ctx: HouseholdSession,
) -> list[IngredientAliasResponse]:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            f"select {_ALIAS_COLUMNS} from ingredient_price_aliases "
            "where household_id = :household_id order by lower(alias_name) asc"
        ),
        {"household_id": str(household_id)},
    )
    return [IngredientAliasResponse(**row._mapping) for row in res]


@router.post(
    "/ingredient-aliases",
    response_model=IngredientAliasResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ingredient_alias(
    payload: IngredientAliasCreate, session_ctx: HouseholdEditorSession
) -> IngredientAliasResponse:
    db, household_id = session_ctx
    alias_key = payload.alias_name.strip().lower()
    price_key = payload.price_name.strip().lower()
    if alias_key == price_key:
        # Gleiche Schreibweise heisst: es gibt gar nichts zu ueberbruecken.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="alias_is_identical")

    target = (
        await db.execute(
            text(
                "select name from ingredient_prices "
                "where household_id = :household_id and lower(name) = :price_key"
            ),
            {"household_id": str(household_id), "price_key": price_key},
        )
    ).first()
    if target is None:
        # Eine Zuordnung auf einen Preis, den es nicht gibt, wuerde still
        # nichts bewirken - genau die Sorte Fehlschlag, die spaeter als
        # "der Import hat nichts gebracht" wiederkommt.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ingredient_price_not_found"
        )

    try:
        res = await db.execute(
            text(
                "insert into ingredient_price_aliases "
                "(household_id, alias_key, alias_name, price_key, price_name) "
                "values (:household_id, :alias_key, :alias_name, :price_key, :price_name) "
                f"returning {_ALIAS_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "alias_key": alias_key,
                "alias_name": payload.alias_name.strip(),
                "price_key": price_key,
                "price_name": target.name,
            },
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="alias_already_exists"
        ) from exc
    created = res.first()
    assert created is not None  # gerade eingefuegt
    return IngredientAliasResponse(**created._mapping)


@router.delete("/ingredient-aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ingredient_alias(
    alias_id: UUID, session_ctx: HouseholdEditorSession
) -> None:
    """Nimmt nur die Zuordnung zurueck. Rezept und Preis bleiben, die Zutat
    steht danach wieder als Luecke da."""
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text(
                "delete from ingredient_price_aliases "
                "where id = :id and household_id = :household_id returning id"
            ),
            {"id": str(alias_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ingredient_alias_not_found"
        )


# --- Umrechnung von Haushaltseinheiten ---
# "1 Zehe Knoblauch = 5 g". Die Bruecke zwischen Rezepteinheit und
# Packungseinheit - dieselbe Bauart wie die Schreibweisen-Zuordnung darueber,
# und aus demselben Grund: der Server schlaegt aus einer nachgeschlagenen
# Werteliste vor, gespeichert wird nur, was jemand bestaetigt hat.
_CONVERSION_COLUMNS = "id, ingredient_name, unit, factor, base_unit, origin, created_at"


async def _upsert_conversion(
    db: AsyncSession, household_id: UUID, payload: UnitConversionCreate
) -> Any:
    key = payload.ingredient_name.strip().lower()
    unit = grocery_pricing_service.normalize_unit(payload.unit)

    # Eine Zutat hat genau EINE Basiseinheit. Zwei verschiedene nebeneinander
    # ergaeben die Kette "1 EL -> 15 ml -> 0,93 g", die bei widerspruechlichen
    # Zeilen still falsch aufloest - deshalb hier ein sichtbarer Konflikt.
    other_base = (
        await db.execute(
            text(
                "select base_unit from ingredient_unit_conversions "
                "where household_id = :household_id and ingredient_key = :key "
                "and base_unit <> :base_unit limit 1"
            ),
            {"household_id": str(household_id), "key": key, "base_unit": payload.base_unit},
        )
    ).first()
    if other_base is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="conversion_base_unit_conflict"
        )

    res = await db.execute(
        text(
            "insert into ingredient_unit_conversions "
            "(household_id, ingredient_key, ingredient_name, unit, factor, base_unit, origin) "
            "values (:household_id, :key, :name, :unit, :factor, :base_unit, :origin) "
            "on conflict (household_id, ingredient_key, unit) do update set "
            "factor = excluded.factor, base_unit = excluded.base_unit, "
            "ingredient_name = excluded.ingredient_name, origin = excluded.origin, "
            "updated_at = now() "
            f"returning {_CONVERSION_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "key": key,
            "name": payload.ingredient_name.strip(),
            "unit": unit,
            "factor": payload.factor,
            "base_unit": payload.base_unit,
            "origin": payload.origin,
        },
    )
    return res.one()


@router.get("/ingredient-unit-conversions", response_model=list[UnitConversionResponse])
async def list_unit_conversions(
    session_ctx: HouseholdSession,
) -> list[UnitConversionResponse]:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            f"select {_CONVERSION_COLUMNS} from ingredient_unit_conversions "
            "where household_id = :household_id "
            "order by lower(ingredient_name) asc, unit asc"
        ),
        {"household_id": str(household_id)},
    )
    return [UnitConversionResponse(**row._mapping) for row in res]


# Vor der /{conversion_id}-Route: sonst liest FastAPI "bulk" als Kennung.
@router.post(
    "/ingredient-unit-conversions/bulk",
    response_model=list[UnitConversionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_unit_conversions_bulk(
    payload: UnitConversionBulkCreate, session_ctx: HouseholdEditorSession
) -> list[UnitConversionResponse]:
    """Alle Vorschlaege auf einmal uebernehmen.

    Der Weg fuer den Rueckstand: siebzig Zutaten einzeln zu bestaetigen waere
    dieselbe Fleissarbeit, die die Werteliste gerade abnehmen soll.
    """
    db, household_id = session_ctx
    return [
        UnitConversionResponse(**(await _upsert_conversion(db, household_id, item))._mapping)
        for item in payload.items
    ]


@router.post(
    "/ingredient-unit-conversions",
    response_model=UnitConversionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_unit_conversion(
    payload: UnitConversionCreate, session_ctx: HouseholdEditorSession
) -> UnitConversionResponse:
    db, household_id = session_ctx
    row = await _upsert_conversion(db, household_id, payload)
    return UnitConversionResponse(**row._mapping)


@router.delete(
    "/ingredient-unit-conversions/{conversion_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_unit_conversion(
    conversion_id: UUID, session_ctx: HouseholdEditorSession
) -> None:
    """Nimmt die Umrechnung zurueck. Die Zutat steht danach wieder als Luecke
    da - besser als eine Zahl, hinter der niemand mehr steht."""
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text(
                "delete from ingredient_unit_conversions "
                "where id = :id and household_id = :household_id returning id"
            ),
            {"id": str(conversion_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="unit_conversion_not_found"
        )


@router.post(
    "/dish-ingredients",
    response_model=DishIngredientResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_dish_ingredient(
    payload: DishIngredientCreate, session_ctx: HouseholdEditorSession
) -> DishIngredientResponse:
    db, household_id = session_ctx
    await _assert_dish_in_household(db, payload.dish_id, household_id)
    res = await db.execute(
        text(
            "insert into dish_ingredients (household_id, dish_id, name, quantity, unit, "
            "category, note, sort_order) "
            "values (:household_id, :dish_id, :name, :quantity, :unit, :category, :note, "
            "coalesce(:sort_order, (select coalesce(max(sort_order), 0) + 1 from dish_ingredients "
            "where dish_id = :dish_id))) "
            f"returning {_DISH_INGREDIENT_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "dish_id": str(payload.dish_id),
            "name": payload.name.strip(),
            "quantity": payload.quantity,
            "unit": payload.unit,
            "category": payload.category,
            "note": payload.note,
            "sort_order": payload.sort_order or None,
        },
    )
    prices = await grocery_pricing_service.load_price_lookup(db, household_id)
    conversions = await grocery_pricing_service.load_conversions(db, household_id)
    return _to_ingredient_response(res.one(), prices, conversions)


@router.patch("/dish-ingredients/{ingredient_id}", response_model=DishIngredientResponse)
async def update_dish_ingredient(
    ingredient_id: UUID, payload: DishIngredientUpdate, session_ctx: HouseholdEditorSession
) -> DishIngredientResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        updates["name"] = str(updates["name"]).strip()
    if not updates:
        row = (
            await db.execute(
                text(
                    f"select {_DISH_INGREDIENT_COLUMNS} from dish_ingredients "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(ingredient_id), "household_id": str(household_id)},
            )
        ).first()
    else:
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        updates["id"] = str(ingredient_id)
        updates["household_id"] = str(household_id)
        row = (
            await db.execute(
                text(
                    f"update dish_ingredients set {set_clause}, updated_at = now() "
                    "where id = :id and household_id = :household_id "
                    f"returning {_DISH_INGREDIENT_COLUMNS}"
                ),
                updates,
            )
        ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="dish_ingredient_not_found"
        )
    prices = await grocery_pricing_service.load_price_lookup(db, household_id)
    conversions = await grocery_pricing_service.load_conversions(db, household_id)
    return _to_ingredient_response(row, prices, conversions)


@router.post(
    "/dish-ingredients/bulk",
    response_model=list[DishIngredientResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_dish_ingredients_bulk(
    payload: DishIngredientBulkCreate, session_ctx: HouseholdEditorSession
) -> list[DishIngredientResponse]:
    """Uebernimmt mehrere Zutaten auf einmal - der Anwenden-Schritt hinter den
    KI-Vorschlaegen. Bereits vorhandene Namen werden uebersprungen statt
    doppelt angelegt; ein zweiter Durchlauf verdoppelt die Liste also nicht."""
    db, household_id = session_ctx
    await _assert_dish_in_household(db, payload.dish_id, household_id)

    existing = {
        row.name.strip().lower()
        for row in (
            await db.execute(
                text("select name from dish_ingredients where dish_id = :dish_id"),
                {"dish_id": str(payload.dish_id)},
            )
        ).all()
    }
    next_sort = (
        await db.execute(
            text(
                "select coalesce(max(sort_order), 0) from dish_ingredients "
                "where dish_id = :dish_id"
            ),
            {"dish_id": str(payload.dish_id)},
        )
    ).scalar_one()

    prices = await grocery_pricing_service.load_price_lookup(db, household_id)
    conversions = await grocery_pricing_service.load_conversions(db, household_id)
    created: list[DishIngredientResponse] = []
    for item in payload.ingredients:
        name = item.name.strip()
        if name.lower() in existing:
            continue
        existing.add(name.lower())
        next_sort += 1
        row = (
            await db.execute(
                text(
                    "insert into dish_ingredients (household_id, dish_id, name, quantity, unit, "
                    "category, sort_order) values (:household_id, :dish_id, :name, :quantity, "
                    ":unit, :category, :sort_order) "
                    f"returning {_DISH_INGREDIENT_COLUMNS}"
                ),
                {
                    "household_id": str(household_id),
                    "dish_id": str(payload.dish_id),
                    "name": name,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "category": item.category,
                    "sort_order": next_sort,
                },
            )
        ).one()
        created.append(_to_ingredient_response(row, prices, conversions))
    return created


@router.post(
    "/dishes/{dish_id}/ingredient-suggestions", response_model=IngredientSuggestionResult
)
async def suggest_dish_ingredients(
    dish_id: UUID, session_ctx: HouseholdEditorSession
) -> IngredientSuggestionResult | JSONResponse:
    """Laesst das lokale Modell die Zutatenliste vorschlagen. Schreibt NICHTS -
    das Uebernehmen laeuft ueber /v1/dish-ingredients/bulk, nachdem der Nutzer
    ausgewaehlt hat. Ohne verwertbare Modellantwort 503 mit benanntem Grund
    (zurueckgegeben statt geworfen, damit die llm_calls-Zeile bleibt)."""
    db, household_id = session_ctx
    try:
        return await grocery_ai_service.suggest_ingredients(db, household_id, dish_id)
    except AIStepError as exc:
        return exc.response()


@router.delete("/dish-ingredients/{ingredient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dish_ingredient(
    ingredient_id: UUID, session_ctx: HouseholdEditorSession
) -> None:
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text(
                "delete from dish_ingredients where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(ingredient_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="dish_ingredient_not_found"
        )


# --- Essensplan: Mahlzeiten ---
@router.get("/meals", response_model=list[MealResponse])
async def list_meals(meal_week_id: UUID, session_ctx: HouseholdSession) -> list[MealResponse]:
    db, household_id = session_ctx
    await _assert_meal_week_in_household(db, meal_week_id, household_id)
    res = await db.execute(
        text(
            f"select {_MEAL_COLUMNS} from meals "
            "where household_id = :household_id and meal_week_id = :meal_week_id "
            "order by meal_date asc, created_at asc"
        ),
        {"household_id": str(household_id), "meal_week_id": str(meal_week_id)},
    )
    return [_to_meal_response(row) for row in res]


@router.post("/meals", response_model=MealResponse, status_code=status.HTTP_201_CREATED)
async def create_meal(payload: MealCreate, session_ctx: HouseholdEditorSession) -> MealResponse:
    db, household_id = session_ctx
    await _assert_meal_week_in_household(db, payload.meal_week_id, household_id)
    if payload.dish_id is not None:
        await _assert_dish_in_household(db, payload.dish_id, household_id)
    res = await db.execute(
        text(
            "insert into meals (household_id, meal_week_id, meal_date, meal_type, dish_name, "
            "dish_id, servings, estimated_cost_minor, note) "
            "values (:household_id, :meal_week_id, :meal_date, :meal_type, :dish_name, "
            ":dish_id, :servings, :estimated, :note) "
            f"returning {_MEAL_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "meal_week_id": str(payload.meal_week_id),
            "meal_date": payload.meal_date,
            "meal_type": payload.meal_type,
            "dish_name": payload.dish_name,
            "dish_id": str(payload.dish_id) if payload.dish_id else None,
            "servings": payload.servings,
            "estimated": payload.estimated_cost_minor,
            "note": payload.note,
        },
    )
    return _to_meal_response(res.one())


@router.post("/meals/bulk", response_model=list[MealResponse], status_code=status.HTTP_201_CREATED)
async def create_meals_bulk(
    payload: MealBulkCreate, session_ctx: HouseholdEditorSession
) -> list[MealResponse]:
    """Legt mehrere Mahlzeiten auf einmal an - der Anwenden-Schritt hinter dem
    KI-Wochenplan. Belegte Slots (Datum + Mahlzeit) werden uebersprungen, damit
    ein Vorschlag nichts ueberschreibt, was schon geplant war."""
    db, household_id = session_ctx
    await _assert_meal_week_in_household(db, payload.meal_week_id, household_id)

    taken = {
        (row.meal_date, row.meal_type)
        for row in (
            await db.execute(
                text(
                    "select meal_date, meal_type from meals "
                    "where household_id = :household_id and meal_week_id = :week_id"
                ),
                {"household_id": str(household_id), "week_id": str(payload.meal_week_id)},
            )
        ).all()
    }

    created: list[MealResponse] = []
    for item in payload.meals:
        if (item.meal_date, item.meal_type) in taken:
            continue
        if item.dish_id is not None:
            await _assert_dish_in_household(db, item.dish_id, household_id)
        taken.add((item.meal_date, item.meal_type))
        row = (
            await db.execute(
                text(
                    "insert into meals (household_id, meal_week_id, meal_date, meal_type, "
                    "dish_name, dish_id, servings, estimated_cost_minor, note) "
                    "values (:household_id, :meal_week_id, :meal_date, :meal_type, :dish_name, "
                    ":dish_id, :servings, :estimated, :note) "
                    f"returning {_MEAL_COLUMNS}"
                ),
                {
                    "household_id": str(household_id),
                    "meal_week_id": str(payload.meal_week_id),
                    "meal_date": item.meal_date,
                    "meal_type": item.meal_type,
                    "dish_name": item.dish_name,
                    "dish_id": str(item.dish_id) if item.dish_id else None,
                    "servings": item.servings,
                    "estimated": item.estimated_cost_minor,
                    "note": item.note,
                },
            )
        ).one()
        created.append(_to_meal_response(row))
    return created


@router.post(
    "/meal-weeks/{meal_week_id}/plan-suggestions", response_model=WeekPlanSuggestionResult
)
async def suggest_week_plan(
    meal_week_id: UUID, payload: WeekPlanSuggestionInput, session_ctx: HouseholdEditorSession
) -> WeekPlanSuggestionResult | JSONResponse:
    """Laesst das lokale Modell einen Wochenplan AUS DEM KATALOG bauen.
    Schreibt nichts; uebernommen wird ueber /v1/meals/bulk. Ohne verwertbare
    Modellantwort 503 mit benanntem Grund (wie oben)."""
    db, household_id = session_ctx
    try:
        return await grocery_ai_service.suggest_week_plan(
            db,
            household_id,
            meal_week_id,
            payload.wish,
            payload.meal_types,
            payload.servings,
            payload.skip_planned_days,
        )
    except AIStepError as exc:
        return exc.response()


@router.patch("/meals/{meal_id}", response_model=MealResponse)
async def update_meal(
    meal_id: UUID, payload: MealUpdate, session_ctx: HouseholdEditorSession
) -> MealResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("dish_id") is not None:
        await _assert_dish_in_household(db, updates["dish_id"], household_id)
        updates["dish_id"] = str(updates["dish_id"])
    if not updates:
        row = (
            await db.execute(
                text(
                    f"select {_MEAL_COLUMNS} from meals "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(meal_id), "household_id": str(household_id)},
            )
        ).first()
    else:
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        updates["id"] = str(meal_id)
        updates["household_id"] = str(household_id)
        row = (
            await db.execute(
                text(
                    f"update meals set {set_clause}, updated_at = now() "
                    "where id = :id and household_id = :household_id "
                    f"returning {_MEAL_COLUMNS}"
                ),
                updates,
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meal_not_found")
    return _to_meal_response(row)


@router.delete("/meals/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal(meal_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text(
                "delete from meals where id = :id and household_id = :household_id returning id"
            ),
            {"id": str(meal_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meal_not_found")


# --- Einkaufsliste ---
@router.get("/shopping-lists", response_model=list[ShoppingListResponse])
async def list_shopping_lists(
    meal_week_id: UUID, session_ctx: HouseholdSession
) -> list[ShoppingListResponse]:
    db, household_id = session_ctx
    await _assert_meal_week_in_household(db, meal_week_id, household_id)
    res = await db.execute(
        text(
            f"select {_SHOPPING_LIST_COLUMNS} from shopping_lists "
            "where household_id = :household_id and meal_week_id = :meal_week_id "
            "order by created_at asc"
        ),
        {"household_id": str(household_id), "meal_week_id": str(meal_week_id)},
    )
    rows = res.all()
    return [await _to_shopping_list_response(db, household_id, row) for row in rows]


@router.post(
    "/shopping-lists", response_model=ShoppingListResponse, status_code=status.HTTP_201_CREATED
)
async def create_shopping_list(
    payload: ShoppingListCreate, session_ctx: HouseholdEditorSession
) -> ShoppingListResponse:
    db, household_id = session_ctx
    await _assert_meal_week_in_household(db, payload.meal_week_id, household_id)
    res = await db.execute(
        text(
            "insert into shopping_lists (household_id, meal_week_id, store, status) "
            "values (:household_id, :meal_week_id, :store, :status) "
            f"returning {_SHOPPING_LIST_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "meal_week_id": str(payload.meal_week_id),
            "store": payload.store,
            "status": payload.status,
        },
    )
    return await _to_shopping_list_response(db, household_id, res.one())


@router.patch("/shopping-lists/{shopping_list_id}", response_model=ShoppingListResponse)
async def update_shopping_list(
    shopping_list_id: UUID, payload: ShoppingListUpdate, session_ctx: HouseholdEditorSession
) -> ShoppingListResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        row = (
            await db.execute(
                text(
                    f"select {_SHOPPING_LIST_COLUMNS} from shopping_lists "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(shopping_list_id), "household_id": str(household_id)},
            )
        ).first()
    else:
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        updates["id"] = str(shopping_list_id)
        updates["household_id"] = str(household_id)
        row = (
            await db.execute(
                text(
                    f"update shopping_lists set {set_clause}, updated_at = now() "
                    "where id = :id and household_id = :household_id "
                    f"returning {_SHOPPING_LIST_COLUMNS}"
                ),
                updates,
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="shopping_list_not_found")
    return await _to_shopping_list_response(db, household_id, row)


@router.delete("/shopping-lists/{shopping_list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shopping_list(
    shopping_list_id: UUID, session_ctx: HouseholdEditorSession
) -> None:
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text(
                "delete from shopping_lists where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(shopping_list_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="shopping_list_not_found"
        )


# --- Einkaufsliste: Artikel ---
@router.get("/shopping-items", response_model=list[ShoppingItemResponse])
async def list_shopping_items(
    shopping_list_id: UUID, session_ctx: HouseholdSession
) -> list[ShoppingItemResponse]:
    db, household_id = session_ctx
    await _assert_shopping_list_in_household(db, shopping_list_id, household_id)
    res = await db.execute(
        text(
            f"select {_SHOPPING_ITEM_COLUMNS} from shopping_items "
            "where household_id = :household_id and shopping_list_id = :shopping_list_id "
            "order by created_at asc"
        ),
        {"household_id": str(household_id), "shopping_list_id": str(shopping_list_id)},
    )
    return [_to_shopping_item_response(row) for row in res]


@router.post(
    "/shopping-items", response_model=ShoppingItemResponse, status_code=status.HTTP_201_CREATED
)
async def create_shopping_item(
    payload: ShoppingItemCreate, session_ctx: HouseholdEditorSession
) -> ShoppingItemResponse:
    db, household_id = session_ctx
    await _assert_shopping_list_in_household(db, payload.shopping_list_id, household_id)
    if payload.meal_id is not None:
        await _assert_meal_in_household(db, payload.meal_id, household_id)
    res = await db.execute(
        text(
            "insert into shopping_items (household_id, shopping_list_id, meal_id, name, "
            "category, quantity, unit, planned_price_minor, actual_price_minor, is_purchased, "
            "note) "
            "values (:household_id, :shopping_list_id, :meal_id, :name, :category, :quantity, "
            ":unit, :planned, :actual, :is_purchased, :note) "
            f"returning {_SHOPPING_ITEM_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "shopping_list_id": str(payload.shopping_list_id),
            "meal_id": str(payload.meal_id) if payload.meal_id else None,
            "name": payload.name,
            "category": payload.category,
            "quantity": payload.quantity,
            "unit": payload.unit,
            "planned": payload.planned_price_minor,
            "actual": payload.actual_price_minor,
            "is_purchased": payload.is_purchased,
            "note": payload.note,
        },
    )
    return _to_shopping_item_response(res.one())


@router.patch("/shopping-items/{item_id}", response_model=ShoppingItemResponse)
async def update_shopping_item(
    item_id: UUID, payload: ShoppingItemUpdate, session_ctx: HouseholdEditorSession
) -> ShoppingItemResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("meal_id") is not None:
        await _assert_meal_in_household(db, updates["meal_id"], household_id)
    if not updates:
        row = (
            await db.execute(
                text(
                    f"select {_SHOPPING_ITEM_COLUMNS} from shopping_items "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(item_id), "household_id": str(household_id)},
            )
        ).first()
    else:
        if "meal_id" in updates and updates["meal_id"] is not None:
            updates["meal_id"] = str(updates["meal_id"])
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        updates["id"] = str(item_id)
        updates["household_id"] = str(household_id)
        row = (
            await db.execute(
                text(
                    f"update shopping_items set {set_clause}, updated_at = now() "
                    "where id = :id and household_id = :household_id "
                    f"returning {_SHOPPING_ITEM_COLUMNS}"
                ),
                updates,
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="shopping_item_not_found")
    return _to_shopping_item_response(row)


@router.delete("/shopping-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shopping_item(item_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text(
                "delete from shopping_items where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(item_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="shopping_item_not_found"
        )


@router.post(
    "/shopping-lists/{shopping_list_id}/fill-from-meals", response_model=ShoppingListFillResult
)
async def fill_shopping_list_from_meals(
    shopping_list_id: UUID, session_ctx: HouseholdEditorSession
) -> ShoppingListFillResult:
    """Uebertraegt die Zutaten aller Mahlzeiten der Woche in diese Liste.

    Rein rechnerisch, ohne KI: die Zutaten stehen im Gerichte-Katalog, die
    Portionszahl an der geplanten Mahlzeit, die Preise in der Preisliste.

    Zwei Eigenschaften, die den Unterschied machen:

    1. **Kassenpreis statt Anteil.** Auf der Liste steht, was zu KAUFEN ist -
       also ganze Packungen. Wer 750 g Nudeln braucht, kauft 1 kg und zahlt
       1 kg. Der anteilige Wert (was die Mahlzeit wert ist) steht dagegen am
       Gericht; siehe grocery_pricing_service.purchase_for vs. cost_for.
       Aufgerundet wird erst NACH dem Aufaddieren ueber alle Gerichte der
       Woche, sonst wuerden 750 g + 250 g zu zwei Packungen statt einer.
    2. **Wiederholbar.** Der Aufruf ist ein Abgleich, kein Anhaengen. Zeilen,
       die aus einem frueheren Lauf stammen (erkennbar an meal_id), werden auf
       den aktuellen Stand gesetzt; von Hand ergaenzte Zeilen (ohne meal_id)
       bleiben unangetastet. Zweimal fuellen verdoppelt also weder Mengen noch
       Betraege - das tat es vorher.
    """
    db, household_id = session_ctx
    await _assert_shopping_list_in_household(db, shopping_list_id, household_id)

    week_row = (
        await db.execute(
            text("select meal_week_id from shopping_lists where id = :id"),
            {"id": str(shopping_list_id)},
        )
    ).one()

    conversions = await grocery_pricing_service.load_conversions(db, household_id)
    collected = await collect_week_ingredients(
        db, household_id, week_row.meal_week_id, conversions
    )
    prices = await grocery_pricing_service.load_price_lookup(db, household_id)

    existing_rows = (
        await db.execute(
            text(
                "select id, name, unit, quantity, from_meal_plan from shopping_items "
                "where shopping_list_id = :id order by created_at asc"
            ),
            {"id": str(shopping_list_id)},
        )
    ).all()
    # Je Name eine LISTE, nicht eine Zeile: collect_week_ingredients liefert
    # denselben Namen mehrfach, sobald die Einheiten nicht ineinander
    # umrechenbar sind ("2 Zehen Knoblauch" neben "1 Stk Knoblauch", siehe
    # grocery_service._merge_key). Ein Abgleich, der je Name nur eine Zeile
    # kennt, laesst dann beim zweiten Fuellen beide Zutaten auf dieselbe Zeile
    # zeigen: eine wird doppelt ueberschrieben, die andere behaelt fuer immer
    # Menge und Preis des ersten Laufs.
    existing: dict[str, list[Any]] = defaultdict(list)
    for row in existing_rows:
        existing[row.name.strip().lower()].append(row)

    created = 0
    merged = 0
    unpriced = 0
    for item in collected.items:
        item_key = item.name.strip().lower()
        purchase = grocery_pricing_service.purchase_for(
            item.quantity, item.unit, prices.get(item_key), conversions.get(item_key)
        )
        if purchase is None:
            unpriced += 1
            quantity = item.quantity
            unit = item.unit
            price_minor = None
            note = None
        else:
            quantity = purchase.buy_quantity
            unit = purchase.buy_unit
            price_minor = purchase.price_minor
            # Nur vermerken, wenn wirklich etwas uebrig bleibt - sonst waere
            # die Notiz nur Rauschen. covers_exactly vergleicht in der
            # Basiseinheit, "1 kg" gilt also als exakt fuer 1000 g Bedarf.
            note = (
                None
                if purchase.covers_exactly
                else (
                    "für "
                    f"{grocery_pricing_service.format_amount(item.quantity, item.unit)} benötigt"
                )
            )

        # Passende Zeile heraussuchen UND aus der Liste nehmen: jede vorhandene
        # Zeile darf hoechstens eine Zutat aufnehmen. Bevorzugt wird die Zeile,
        # deren Einheit zur Zutat passt - entweder die Rezepteinheit oder die
        # Kaufeinheit, denn beim Fuellen wird auf Packungen gerundet ("300 g
        # Bedarf" steht als "1 kg" auf der Liste).
        kandidaten = existing.get(item_key, [])
        match = None
        if kandidaten:
            einheiten = {
                (item.unit or "").strip().lower(),
                (unit or "").strip().lower(),
            }

            passend = [r for r in kandidaten if (r.unit or "").strip().lower() in einheiten]

            # Reihenfolge der Vorliebe: eine Planzeile mit passender Einheit ist
            # eindeutig gemeint. Eine Planzeile ohne passende Einheit immer noch
            # eher als eine Handzeile - sonst bliebe die Planzeile veraltet
            # stehen, waehrend der Abgleich sich an der Handzeile die Zaehne
            # ausbeisst, die er ohnehin nicht anfassen darf.
            match = (
                next((r for r in passend if r.from_meal_plan), None)
                or next((r for r in kandidaten if r.from_meal_plan), None)
                or (passend[0] if passend else None)
                or kandidaten[0]
            )
            kandidaten.remove(match)

        if match is not None:
            # Von Hand ergaenzte Zeilen gehoeren dem Nutzer - nicht anfassen.
            if not match.from_meal_plan:
                merged += 1
                continue
            await db.execute(
                text(
                    "update shopping_items set quantity = coalesce(:quantity, quantity), "
                    "unit = :unit, planned_price_minor = :price, note = :note, "
                    "updated_at = now() where id = :id"
                ),
                {
                    "quantity": quantity,
                    "unit": unit,
                    "price": price_minor,
                    "note": note,
                    "id": str(match.id),
                },
            )
            merged += 1
            continue

        await db.execute(
            text(
                "insert into shopping_items (household_id, shopping_list_id, meal_id, name, "
                "category, quantity, unit, planned_price_minor, note, from_meal_plan) "
                "values (:household_id, :shopping_list_id, :meal_id, :name, :category, "
                "coalesce(:quantity, 1), :unit, :price, :note, true)"
            ),
            {
                "household_id": str(household_id),
                "shopping_list_id": str(shopping_list_id),
                "meal_id": str(item.meal_id),
                "name": item.name,
                "category": item.category,
                "quantity": quantity,
                "unit": unit,
                "price": price_minor,
                "note": note,
            },
        )
        created += 1

    return ShoppingListFillResult(
        created_items=created,
        merged_items=merged,
        considered_meals=collected.considered_meals,
        skipped_meals=collected.skipped_meals,
        unpriced_items=unpriced,
    )


# --- Verknuepfung mit realen Banktransaktionen ---
@router.post(
    "/shopping-lists/{shopping_list_id}/purchase-links",
    response_model=PurchaseLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_purchase(
    shopping_list_id: UUID, payload: PurchaseLinkCreate, session_ctx: HouseholdEditorSession
) -> PurchaseLinkResponse:
    """Verknuepft eine Einkaufsliste mit einer ganzen, echten Banktransaktion.
    Keine Artikelaufteilung aus dem Kontoauszug (Abschnitt 6.8) - die
    Transaktion wird als Ganzes verknuepft, nie ein Teilbetrag."""
    db, household_id = session_ctx
    await _assert_shopping_list_in_household(db, shopping_list_id, household_id)

    tx_row = (
        await db.execute(
            text(
                "select id, booked_date, amount_minor, payee_label from transactions "
                "where id = :id and household_id = :household_id and not is_archived"
            ),
            {"id": str(payload.transaction_id), "household_id": str(household_id)},
        )
    ).first()
    if tx_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found")

    already_linked = (
        await db.execute(
            text("select 1 from shopping_purchase_links where transaction_id = :id"),
            {"id": str(payload.transaction_id)},
        )
    ).first()
    if already_linked is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="transaction_already_linked"
        )

    link_row = (
        await db.execute(
            text(
                "insert into shopping_purchase_links "
                "(household_id, shopping_list_id, transaction_id) "
                "values (:household_id, :shopping_list_id, :transaction_id) "
                "returning id, shopping_list_id, created_at"
            ),
            {
                "household_id": str(household_id),
                "shopping_list_id": str(shopping_list_id),
                "transaction_id": str(payload.transaction_id),
            },
        )
    ).one()

    return PurchaseLinkResponse(
        id=link_row.id,
        shopping_list_id=link_row.shopping_list_id,
        transaction=LinkedTransaction(**tx_row._mapping),
        created_at=link_row.created_at,
    )


@router.delete("/purchase-links/{purchase_link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_purchase(purchase_link_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    db, household_id = session_ctx
    row = (
        await db.execute(
            text(
                "delete from shopping_purchase_links "
                "where id = :id and household_id = :household_id returning id"
            ),
            {"id": str(purchase_link_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="purchase_link_not_found")
