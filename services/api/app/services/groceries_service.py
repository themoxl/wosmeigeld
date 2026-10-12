import codecs
import csv
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.groceries import (
    DishCreate,
    DishResponse,
    DishUpdate,
    MealWeekCreate,
    MealWeekResponse,
    MealWeekUpdate,
    ShoppingListCreate,
    ShoppingListResponse,
    ShoppingListUpdate,
)


async def create_dish(session: AsyncSession, dish: DishCreate) -> DishResponse:
    query = text("""
        INSERT INTO dishes (name, description, default_servings)
        VALUES (:name, :description, :default_servings)
        RETURNING id, household_id, name, description, default_servings, created_at
    """)
    result = await session.execute(query, {
        "name": dish.name,
        "description": dish.description,
        "default_servings": dish.default_servings
    })
    row = result.one()
    return DishResponse(**row._mapping)

async def get_dishes(session: AsyncSession) -> list[DishResponse]:
    query = text("""
        SELECT id, household_id, name, description, default_servings, created_at
        FROM dishes
        ORDER BY name
    """)
    result = await session.execute(query)
    return [DishResponse(**row._mapping) for row in result.fetchall()]

async def get_dish(session: AsyncSession, dish_id: UUID) -> DishResponse | None:
    query = text("""
        SELECT id, household_id, name, description, default_servings, created_at
        FROM dishes
        WHERE id = :id
    """)
    result = await session.execute(query, {"id": dish_id})
    row = result.fetchone()
    if row:
        return DishResponse(**row._mapping)
    return None

async def update_dish(
    session: AsyncSession,
    dish_id: UUID,
    dish: DishUpdate,
) -> DishResponse | None:
    updates = []
    params: dict[str, Any] = {"id": dish_id}
    
    if dish.name is not None:
        updates.append("name = :name")
        params["name"] = dish.name
    if dish.description is not None:
        updates.append("description = :description")
        params["description"] = dish.description
    if dish.default_servings is not None:
        updates.append("default_servings = :default_servings")
        params["default_servings"] = dish.default_servings
        
    if not updates:
        return await get_dish(session, dish_id)
        
    query = text(f"""
        UPDATE dishes
        SET {", ".join(updates)}
        WHERE id = :id
        RETURNING id, household_id, name, description, default_servings, created_at
    """)
    result = await session.execute(query, params)
    row = result.fetchone()
    if row:
        return DishResponse(**row._mapping)
    return None

async def delete_dish(session: AsyncSession, dish_id: UUID) -> bool:
    query = text("DELETE FROM dishes WHERE id = :id RETURNING id")
    result = await session.execute(query, {"id": dish_id})
    return result.fetchone() is not None


async def create_meal_week(session: AsyncSession, meal_week: MealWeekCreate) -> MealWeekResponse:
    query = text("""
        INSERT INTO meal_weeks (start_date, notes)
        VALUES (:start_date, :notes)
        RETURNING id, household_id, start_date, notes
    """)
    result = await session.execute(query, {
        "start_date": meal_week.start_date,
        "notes": meal_week.notes
    })
    row = result.one()
    return MealWeekResponse(**row._mapping)

async def get_meal_weeks(session: AsyncSession) -> list[MealWeekResponse]:
    query = text("""
        SELECT id, household_id, start_date, notes
        FROM meal_weeks
        ORDER BY start_date DESC
    """)
    result = await session.execute(query)
    return [MealWeekResponse(**row._mapping) for row in result.fetchall()]

async def get_meal_week(session: AsyncSession, meal_week_id: UUID) -> MealWeekResponse | None:
    query = text("""
        SELECT id, household_id, start_date, notes
        FROM meal_weeks
        WHERE id = :id
    """)
    result = await session.execute(query, {"id": meal_week_id})
    row = result.fetchone()
    if row:
        return MealWeekResponse(**row._mapping)
    return None

async def update_meal_week(
    session: AsyncSession,
    meal_week_id: UUID,
    meal_week: MealWeekUpdate,
) -> MealWeekResponse | None:
    updates = []
    params: dict[str, Any] = {"id": meal_week_id}
    
    if meal_week.start_date is not None:
        updates.append("start_date = :start_date")
        params["start_date"] = meal_week.start_date
    if meal_week.notes is not None:
        updates.append("notes = :notes")
        params["notes"] = meal_week.notes
        
    if not updates:
        return await get_meal_week(session, meal_week_id)
        
    query = text(f"""
        UPDATE meal_weeks
        SET {", ".join(updates)}
        WHERE id = :id
        RETURNING id, household_id, start_date, notes
    """)
    result = await session.execute(query, params)
    row = result.fetchone()
    if row:
        return MealWeekResponse(**row._mapping)
    return None

async def delete_meal_week(session: AsyncSession, meal_week_id: UUID) -> bool:
    query = text("DELETE FROM meal_weeks WHERE id = :id RETURNING id")
    result = await session.execute(query, {"id": meal_week_id})
    return result.fetchone() is not None


async def create_shopping_list(
    session: AsyncSession,
    shopping_list: ShoppingListCreate,
) -> ShoppingListResponse:
    query = text("""
        INSERT INTO shopping_lists (status, meal_week_id)
        VALUES (:status, :meal_week_id)
        RETURNING id, household_id, status, meal_week_id
    """)
    result = await session.execute(query, {
        "status": shopping_list.status,
        "meal_week_id": shopping_list.meal_week_id
    })
    row = result.one()
    return ShoppingListResponse(**row._mapping)

async def get_shopping_lists(session: AsyncSession) -> list[ShoppingListResponse]:
    query = text("""
        SELECT id, household_id, status, meal_week_id
        FROM shopping_lists
        ORDER BY id DESC
    """)
    result = await session.execute(query)
    return [ShoppingListResponse(**row._mapping) for row in result.fetchall()]

async def get_shopping_list(
    session: AsyncSession,
    shopping_list_id: UUID,
) -> ShoppingListResponse | None:
    query = text("""
        SELECT id, household_id, status, meal_week_id
        FROM shopping_lists
        WHERE id = :id
    """)
    result = await session.execute(query, {"id": shopping_list_id})
    row = result.fetchone()
    if row:
        return ShoppingListResponse(**row._mapping)
    return None

async def update_shopping_list(
    session: AsyncSession,
    shopping_list_id: UUID,
    shopping_list: ShoppingListUpdate,
) -> ShoppingListResponse | None:
    updates = []
    params: dict[str, Any] = {"id": shopping_list_id}
    
    if shopping_list.status is not None:
        updates.append("status = :status")
        params["status"] = shopping_list.status
    if shopping_list.meal_week_id is not None:
        updates.append("meal_week_id = :meal_week_id")
        params["meal_week_id"] = shopping_list.meal_week_id
        
    if not updates:
        return await get_shopping_list(session, shopping_list_id)
        
    query = text(f"""
        UPDATE shopping_lists
        SET {", ".join(updates)}
        WHERE id = :id
        RETURNING id, household_id, status, meal_week_id
    """)
    result = await session.execute(query, params)
    row = result.fetchone()
    if row:
        return ShoppingListResponse(**row._mapping)
    return None

async def delete_shopping_list(session: AsyncSession, shopping_list_id: UUID) -> bool:
    query = text("DELETE FROM shopping_lists WHERE id = :id RETURNING id")
    result = await session.execute(query, {"id": shopping_list_id})
    return result.fetchone() is not None


async def import_prices_from_csv(
    session: AsyncSession,
    household_id: UUID,
    file: UploadFile,
) -> int:
    latest_prices: dict[str, tuple[str, int, float | None, str | None]] = {}
    
    reader = csv.DictReader(codecs.iterdecode(file.file, 'utf-8'), delimiter=';')
    for row in reader:
        name = row.get("name")
        if not name:
            continue
            
        date_str = row.get("priceDate") or ""
        
        price_str = row.get("price")
        try:
            price_minor = int(float(price_str) * 100) if price_str else 0
        except ValueError:
            price_minor = 0
            
        quantity_str = row.get("quantity")
        try:
            quantity = float(quantity_str) if quantity_str else None
        except ValueError:
            quantity = None
            
        unit = row.get("unit") or None
        
        if name not in latest_prices or latest_prices[name][0] < date_str:
            latest_prices[name] = (date_str, price_minor, quantity, unit)
            
    if not latest_prices:
        return 0
        
    values_list = []
    for name, (_date, price_minor, quantity, unit) in latest_prices.items():
        values_list.append({
            "household_id": household_id,
            "name": name,
            "evaluation_price_minor": price_minor,
            "package_amount": quantity,
            "unit": unit
        })
        
    query = text("""
        INSERT INTO ingredient_prices
            (household_id, name, evaluation_price_minor, package_amount, unit)
        VALUES (:household_id, :name, :evaluation_price_minor, :package_amount, :unit)
        ON CONFLICT (household_id, name) DO UPDATE SET
            evaluation_price_minor = EXCLUDED.evaluation_price_minor,
            package_amount = EXCLUDED.package_amount,
            unit = EXCLUDED.unit
    """)
    
    await session.execute(query, values_list)
    return len(values_list)

async def seed_default_dishes(session: AsyncSession, household_id: UUID) -> int:
    defaults = [
        {"name": "Nudeln mit Hackfleischsoße", "description": "Klassische Spaghetti Bolognese"},
        {"name": "Chili con Carne", "description": "Herzhafter mexikanischer Eintopf"},
        {"name": "Schnitzel mit Kartoffelsalat", "description": "Klassiker der Hausmannskost"},
        {"name": "Pommes", "description": "Knusprige Pommes aus dem Ofen"},
        {"name": "Pfannkuchen", "description": "Süße oder herzhafte Pfannkuchen"},
        {"name": "Pizza Margherita", "description": "Klassische Pizza mit Tomate und Mozzarella"},
        {"name": "Linsensuppe", "description": "Deftige Suppe mit Würstchen"},
        {"name": "Käsespätzle", "description": "Spätzle mit Röstzwiebeln und Käse"},
    ]
    
    count = 0
    for d in defaults:
        check_query = text(
            "SELECT id FROM dishes "
            "WHERE household_id = :household_id AND lower(name) = lower(:name)",
        )
        res = await session.execute(check_query, {"household_id": household_id, "name": d["name"]})
        if res.fetchone() is None:
            insert_query = text("""
                INSERT INTO dishes (household_id, name, description, default_servings)
                VALUES (:household_id, :name, :description, 2)
            """)
            await session.execute(insert_query, {
                "household_id": household_id,
                "name": d["name"],
                "description": d["description"]
            })
            count += 1
            
    return count
