from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session, require_household_editor
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
from app.services import groceries_service

router = APIRouter(prefix="/groceries", tags=["groceries"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[tuple[AsyncSession, UUID], Depends(require_household_editor)]

# --- Dishes ---

@router.get("/dishes", response_model=list[DishResponse])
async def list_dishes(ctx: HouseholdSession) -> list[DishResponse]:
    session, _ = ctx
    return await groceries_service.get_dishes(session)

@router.post("/dishes/seed-defaults", response_model=dict, status_code=status.HTTP_201_CREATED)
async def seed_default_dishes(ctx: HouseholdEditorSession) -> dict[str, int]:
    session, household_id = ctx
    count = await groceries_service.seed_default_dishes(session, household_id)
    return {"seeded": count}


@router.post("/dishes", response_model=DishResponse, status_code=status.HTTP_201_CREATED)
async def create_dish(ctx: HouseholdEditorSession, dish: DishCreate) -> DishResponse:
    session, _ = ctx
    return await groceries_service.create_dish(session, dish)

@router.get("/dishes/{dish_id}", response_model=DishResponse)
async def get_dish(ctx: HouseholdSession, dish_id: UUID) -> DishResponse:
    session, _ = ctx
    result = await groceries_service.get_dish(session, dish_id)
    if not result:
        raise HTTPException(status_code=404, detail="Dish not found")
    return result

@router.patch("/dishes/{dish_id}", response_model=DishResponse)
async def update_dish(ctx: HouseholdEditorSession, dish_id: UUID, dish: DishUpdate) -> DishResponse:
    session, _ = ctx
    result = await groceries_service.update_dish(session, dish_id, dish)
    if not result:
        raise HTTPException(status_code=404, detail="Dish not found")
    return result

@router.delete("/dishes/{dish_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dish(ctx: HouseholdEditorSession, dish_id: UUID) -> None:
    session, _ = ctx
    deleted = await groceries_service.delete_dish(session, dish_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dish not found")


# --- Meal Weeks ---

@router.get("/meal-weeks", response_model=list[MealWeekResponse])
async def list_meal_weeks(ctx: HouseholdSession) -> list[MealWeekResponse]:
    session, _ = ctx
    return await groceries_service.get_meal_weeks(session)

@router.post("/meal-weeks", response_model=MealWeekResponse, status_code=status.HTTP_201_CREATED)
async def create_meal_week(
    ctx: HouseholdEditorSession,
    meal_week: MealWeekCreate,
) -> MealWeekResponse:
    session, _ = ctx
    return await groceries_service.create_meal_week(session, meal_week)

@router.get("/meal-weeks/{meal_week_id}", response_model=MealWeekResponse)
async def get_meal_week(ctx: HouseholdSession, meal_week_id: UUID) -> MealWeekResponse:
    session, _ = ctx
    result = await groceries_service.get_meal_week(session, meal_week_id)
    if not result:
        raise HTTPException(status_code=404, detail="Meal week not found")
    return result

@router.patch("/meal-weeks/{meal_week_id}", response_model=MealWeekResponse)
async def update_meal_week(
    ctx: HouseholdEditorSession,
    meal_week_id: UUID,
    meal_week: MealWeekUpdate,
) -> MealWeekResponse:
    session, _ = ctx
    result = await groceries_service.update_meal_week(session, meal_week_id, meal_week)
    if not result:
        raise HTTPException(status_code=404, detail="Meal week not found")
    return result

@router.delete("/meal-weeks/{meal_week_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal_week(ctx: HouseholdEditorSession, meal_week_id: UUID) -> None:
    session, _ = ctx
    deleted = await groceries_service.delete_meal_week(session, meal_week_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Meal week not found")


# --- Shopping Lists ---

@router.get("/shopping-lists", response_model=list[ShoppingListResponse])
async def list_shopping_lists(ctx: HouseholdSession) -> list[ShoppingListResponse]:
    session, _ = ctx
    return await groceries_service.get_shopping_lists(session)

@router.post(
    "/shopping-lists",
    response_model=ShoppingListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_shopping_list(
    ctx: HouseholdEditorSession,
    shopping_list: ShoppingListCreate,
) -> ShoppingListResponse:
    session, _ = ctx
    return await groceries_service.create_shopping_list(session, shopping_list)

@router.get("/shopping-lists/{shopping_list_id}", response_model=ShoppingListResponse)
async def get_shopping_list(ctx: HouseholdSession, shopping_list_id: UUID) -> ShoppingListResponse:
    session, _ = ctx
    result = await groceries_service.get_shopping_list(session, shopping_list_id)
    if not result:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    return result

@router.patch("/shopping-lists/{shopping_list_id}", response_model=ShoppingListResponse)
async def update_shopping_list(
    ctx: HouseholdEditorSession,
    shopping_list_id: UUID,
    shopping_list: ShoppingListUpdate,
) -> ShoppingListResponse:
    session, _ = ctx
    result = await groceries_service.update_shopping_list(session, shopping_list_id, shopping_list)
    if not result:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    return result

@router.delete("/shopping-lists/{shopping_list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shopping_list(ctx: HouseholdEditorSession, shopping_list_id: UUID) -> None:
    session, _ = ctx
    deleted = await groceries_service.delete_shopping_list(session, shopping_list_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Shopping list not found")


# --- Import ---

@router.post("/prices/import", response_model=dict)
async def import_prices(
    ctx: HouseholdEditorSession,
    file: UploadFile
) -> dict[str, int]:
    session, household_id = ctx
    count = await groceries_service.import_prices_from_csv(session, household_id, file)
    return {"imported": count}

