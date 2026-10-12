import datetime as dt
from uuid import UUID

from pydantic import BaseModel, Field


# --- Dish ---
class DishBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    default_servings: int = Field(default=2, gt=0)

class DishCreate(DishBase):
    pass

class DishUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    default_servings: int | None = Field(None, gt=0)

class DishResponse(DishBase):
    id: UUID
    household_id: UUID
    created_at: dt.datetime

# --- Dish Ingredient ---
class DishIngredientBase(BaseModel):
    ingredient_name: str
    amount: float | None = None
    unit: str | None = None
    is_optional: bool = False

class DishIngredientCreate(DishIngredientBase):
    dish_id: UUID

class DishIngredientUpdate(BaseModel):
    ingredient_name: str | None = None
    amount: float | None = None
    unit: str | None = None
    is_optional: bool | None = None

class DishIngredientResponse(DishIngredientBase):
    id: UUID
    dish_id: UUID
    household_id: UUID

# --- Meal Week ---
class MealWeekBase(BaseModel):
    start_date: dt.date
    notes: str | None = None

class MealWeekCreate(MealWeekBase):
    pass

class MealWeekUpdate(BaseModel):
    start_date: dt.date | None = None
    notes: str | None = None

class MealWeekResponse(MealWeekBase):
    id: UUID
    household_id: UUID

# --- Meal ---
class MealBase(BaseModel):
    date: dt.date
    meal_type: str
    dish_id: UUID | None = None
    dish_name: str | None = None
    servings: int | None = None

class MealCreate(MealBase):
    meal_week_id: UUID

class MealUpdate(BaseModel):
    date: dt.date | None = None
    meal_type: str | None = None
    dish_id: UUID | None = None
    dish_name: str | None = None
    servings: int | None = None

class MealResponse(MealBase):
    id: UUID
    household_id: UUID
    meal_week_id: UUID

# --- Grocery Store ---
class GroceryStoreBase(BaseModel):
    chain_name: str
    branch_name: str | None = None

class GroceryStoreCreate(GroceryStoreBase):
    pass

class GroceryStoreUpdate(BaseModel):
    chain_name: str | None = None
    branch_name: str | None = None

class GroceryStoreResponse(GroceryStoreBase):
    id: UUID
    household_id: UUID

# --- Ingredient Price ---
class IngredientPriceBase(BaseModel):
    name: str
    evaluation_price_minor: int
    package_amount: float | None = None
    unit: str | None = None

class IngredientPriceCreate(IngredientPriceBase):
    pass

class IngredientPriceUpdate(BaseModel):
    name: str | None = None
    evaluation_price_minor: int | None = None
    package_amount: float | None = None
    unit: str | None = None

class IngredientPriceResponse(IngredientPriceBase):
    id: UUID
    household_id: UUID

# --- Shopping List ---
class ShoppingListBase(BaseModel):
    status: str = "draft"
    meal_week_id: UUID | None = None

class ShoppingListCreate(ShoppingListBase):
    pass

class ShoppingListUpdate(BaseModel):
    status: str | None = None
    meal_week_id: UUID | None = None

class ShoppingListResponse(ShoppingListBase):
    id: UUID
    household_id: UUID

# --- Shopping Item ---
class ShoppingItemBase(BaseModel):
    ingredient_name: str
    required_amount: float | None = None
    unit: str | None = None
    is_checked: bool = False

class ShoppingItemCreate(ShoppingItemBase):
    shopping_list_id: UUID

class ShoppingItemUpdate(BaseModel):
    ingredient_name: str | None = None
    required_amount: float | None = None
    unit: str | None = None
    is_checked: bool | None = None

class ShoppingItemResponse(ShoppingItemBase):
    id: UUID
    shopping_list_id: UUID
    household_id: UUID
