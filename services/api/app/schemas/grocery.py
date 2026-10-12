from datetime import date, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field

MealType = Literal["breakfast", "lunch", "dinner", "snack"]
ShoppingListStatus = Literal["planning", "shopping", "completed"]


# --- Essensplan (Wochen & Mahlzeiten) ---
class MealWeekCreate(BaseModel):
    week_start: date
    budget_minor: int = Field(default=0, ge=0)
    note: str | None = None


class MealWeekUpdate(BaseModel):
    budget_minor: int | None = Field(default=None, ge=0)
    note: str | None = None


class MealWeekResponse(BaseModel):
    id: UUID
    week_start: date
    budget_minor: int
    note: str | None
    created_at: datetime


class MealWeekSummaryResponse(BaseModel):
    meal_week_id: UUID
    week_start: date
    budget_minor: int
    meals_estimated_minor: int
    items_planned_minor: int
    actual_minor: int
    remaining_vs_budget_minor: int


# --- Gerichte-Katalog ---
# Wiederverwendbare Gerichte: einmal angelegt, danach im Wochenplan nur noch
# ausgewaehlt. Die geplante Mahlzeit kopiert Name/Portionen/Schaetzung beim
# Uebernehmen - der Katalog ist Vorlage, keine laufende Verknuepfung.
class DishCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    default_meal_type: MealType | None = None
    default_servings: int = Field(default=2, gt=0)
    estimated_cost_minor: int | None = Field(default=None, ge=0)
    category: str | None = None
    note: str | None = None


class DishUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    default_meal_type: MealType | None = None
    default_servings: int | None = Field(default=None, gt=0)
    estimated_cost_minor: int | None = Field(default=None, ge=0)
    category: str | None = None
    note: str | None = None


class DishResponse(BaseModel):
    id: UUID
    name: str
    default_meal_type: MealType | None
    default_servings: int
    # Handeingabe. Bleibt eine UEBERSTEUERUNG: ist sie gesetzt, gilt sie;
    # ist sie leer, gilt computed_cost_minor. Loeschen heisst also "zurueck
    # zur Berechnung", nicht "keine Schaetzung".
    estimated_cost_minor: int | None
    category: str | None
    note: str | None
    # Wie oft dieses Gericht bereits eingeplant wurde - macht sichtbar, was
    # sich als Vorlage bewaehrt hat.
    usage_count: int
    ingredient_count: int
    # Aus der Zutaten-Preisliste gerechnet, fuer default_servings Portionen.
    computed_cost_minor: int | None
    # Zutaten mit Menge, aber ohne verwertbaren Preis. > 0 heisst: die
    # Berechnung ist unvollstaendig, und das muss sichtbar sein.
    unpriced_ingredients: int
    # Gegenstueck dazu: wie viele Zutaten in computed_cost_minor eingeflossen
    # sind. Erst beide zusammen ergeben die Abdeckung ("3 von 4 bepreist") -
    # eine nackte Lueckenzahl sagt nicht, ob das viel ist.
    priced_ingredients: int
    # Zutaten ohne Menge ("Salz"). Absichtlich unbeziffert und deshalb KEINE
    # Luecke - sie duerfen die Abdeckung nicht kleinrechnen.
    unquantified_ingredients: int
    # Was tatsaechlich gilt: Handeingabe vor Berechnung.
    effective_cost_minor: int | None
    # effective_cost_minor je Portion. Die eigentliche Vergleichsgroesse:
    # "12,00 fuer 4 Portionen" gegen "6,00 fuer 2" ist erst als 3,00 gegen
    # 3,00 eine Aussage. Serverseitig gerundet, damit dieselbe Zahl ueberall
    # gleich aussieht.
    cost_per_serving_minor: int | None
    created_at: datetime
    updated_at: datetime


# --- Zutaten eines Gerichts ---
# Mengen beziehen sich immer auf DishResponse.default_servings, nicht auf eine
# einzelne Portion (siehe Kommentar in 0001_schema.sql). quantity darf fehlen -
# "Salz", "etwas Olivenoel" gehoeren auf die Liste, haben aber keine Menge.
class DishIngredientCreate(BaseModel):
    dish_id: UUID
    name: str = Field(min_length=1, max_length=160)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = None
    category: str | None = None
    note: str | None = None
    sort_order: int = 0


class DishIngredientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = None
    category: str | None = None
    note: str | None = None
    sort_order: int | None = None


class DishIngredientResponse(BaseModel):
    id: UUID
    dish_id: UUID
    name: str
    quantity: Decimal | None
    unit: str | None
    category: str | None
    note: str | None
    sort_order: int
    # Kosten dieser Zutat aus der Preisliste. None hat drei Ursachen, die das
    # Frontend unterscheidet: kein Preis hinterlegt, keine Menge angegeben,
    # oder Einheiten passen nicht zueinander (siehe has_price/unit_mismatch).
    cost_minor: int | None
    has_price: bool
    unit_mismatch: bool
    # True, wenn erst eine hinterlegte Umrechnung ("1 Zehe = 5 g") Rezept und
    # Preisliste zusammengebracht hat. Die Oberflaeche setzt darauf ihr
    # "ungefaehr"-Zeichen - die Zahl beruht dann auf einem Stueckgewicht.
    cost_is_estimated: bool = False
    created_at: datetime
    updated_at: datetime


# --- Zutaten-Preisliste ---
# price_minor gilt fuer pack_quantity pack_unit, also fuer die Packung wie im
# Laden: "1,99 EUR fuer 1 kg", "0,89 EUR fuer 400 g", "2,49 EUR fuer 10 Stk".
class IngredientPriceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    price_minor: int = Field(ge=0)
    pack_quantity: Decimal = Field(default=Decimal("1"), gt=0)
    pack_unit: str | None = None
    category: str | None = None
    note: str | None = None


class IngredientPriceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    price_minor: int | None = Field(default=None, ge=0)
    pack_quantity: Decimal | None = Field(default=None, gt=0)
    pack_unit: str | None = None
    category: str | None = None
    note: str | None = None


class IngredientPriceResponse(BaseModel):
    id: UUID
    name: str
    price_minor: int
    pack_quantity: Decimal
    pack_unit: str | None
    category: str | None
    note: str | None
    # In wie vielen Gerichten diese Zutat vorkommt - zeigt, worauf sich eine
    # Preisaenderung auswirkt.
    used_in_dishes: int
    # Woher die Zahl stammt. 'manual' heisst: von Hand getippt und gegen jeden
    # Import gesperrt - der haeufigste Grund, warum ein Import "nichts bewirkt"
    # zu haben scheint. 'derived' laesst die Sichtungen entscheiden, 'pinned'
    # haengt an genau einer.
    # Ohne Vorgabewert: die Felder kommen immer aus der Abfrage, und ein
    # Default wuerde sie im erzeugten TypeScript optional machen - dann muesste
    # die Oberflaeche einen Fall behandeln, den es nicht gibt.
    price_source: Literal["manual", "pinned", "derived"]
    price_observed_on: date | None
    # Keine Sichtung mehr innerhalb der Altersgrenze. Der Preis gilt weiter, er
    # wird nur nicht mehr als frisch ausgegeben.
    price_is_stale: bool
    created_at: datetime
    updated_at: datetime


# --- Preisluecken ---
# Dieselben Daten wie DishResponse.unpriced_ingredients, nach ZUTAT gruppiert
# statt nach Gericht. Der Unterschied ist der Arbeitsablauf: eine Zutat
# einmal bepreisen repariert jedes Gericht, das sie verwendet.
class PriceGapDish(BaseModel):
    dish_id: UUID
    dish_name: str
    quantity: Decimal
    unit: str | None


class SuggestedConversionResponse(BaseModel):
    """Vorschlag aus der mitgelieferten Werteliste, nicht aus der Datenbank."""

    unit: str
    factor: Decimal
    base_unit: Literal["g", "ml"]
    # 'belegt' | 'plausibel' | 'grob'. Nur die ersten beiden gehen in die
    # Sammeluebernahme ein; bei 'grob' soll jemand hinschauen.
    confidence: Literal["belegt", "plausibel", "grob"]


class PriceGapResponse(BaseModel):
    # Kleingeschriebener Name - derselbe Schluessel, mit dem der Rechenkern
    # Zutat und Preis zusammenbringt.
    key: str
    name: str
    # 'no_price' legt man an, 'unit_mismatch' korrigiert man. Zwei
    # verschiedene Handgriffe, die als bloss "fehlt" nicht unterscheidbar
    # waeren.
    kind: Literal["no_price", "unit_mismatch"]
    dishes: list[PriceGapDish]
    dish_count: int
    planned_count: int
    # Haeufigste Einheit in den Rezepten - fuellt beim Anlegen das
    # Einheitenfeld vor, damit der Einheitenkonflikt gar nicht erst entsteht.
    recipe_unit: str | None
    # Nur bei 'unit_mismatch' gesetzt: die Preiszeile, die nicht passt. Der
    # Preis kommt mit, damit die Korrektur nur die Packung umstellen muss und
    # die Zahl nicht neu getippt werden will.
    price_id: UUID | None
    price_name: str | None
    price_minor: int | None
    price_pack_quantity: Decimal | None
    price_pack_unit: str | None
    # Nur bei 'no_price': ein aehnlich geschriebener Preislisteneintrag.
    # Ausdruecklich ein VORSCHLAG - uebernommen wird er erst per Bestaetigung,
    # weil "Salz" der Anfang von "Salzkartoffeln" ist.
    suggested_price_name: str | None
    # Nur bei 'unit_mismatch': die nachgeschlagene Umrechnung, die genau diese
    # Luecke schliessen wuerde. None heisst, dass es dafuer keine allgemeine
    # Zahl gibt ("1 Pkg" gegen "2 Stk") - dann muss der Haushalt entscheiden.
    suggested_conversion: SuggestedConversionResponse | None = None


# --- Umrechnung von Haushaltseinheiten ---
# "1 Zehe Knoblauch = 5 g". Die Bruecke zwischen der Einheit im Rezept und der
# Packungseinheit der Preisliste; eine Dichte ist derselbe Satz mit ml als
# Einheit ("1 ml Sesamoel = 0,93 g").
class UnitConversionCreate(BaseModel):
    ingredient_name: str = Field(min_length=1, max_length=160)
    unit: str = Field(min_length=1, max_length=40)
    factor: Decimal = Field(gt=0)
    base_unit: Literal["g", "ml"]
    origin: Literal["default", "manual"] = "manual"


class UnitConversionBulkCreate(BaseModel):
    items: list[UnitConversionCreate] = Field(min_length=1, max_length=200)


class UnitConversionResponse(BaseModel):
    id: UUID
    ingredient_name: str
    unit: str
    factor: Decimal
    base_unit: str
    origin: str
    created_at: datetime


# --- Zugeordnete Schreibweisen ---
class IngredientAliasCreate(BaseModel):
    # Name so, wie er im Rezept steht.
    alias_name: str = Field(min_length=1, max_length=160)
    # Name der Preiszeile, auf die er zeigen soll.
    price_name: str = Field(min_length=1, max_length=160)


class IngredientAliasResponse(BaseModel):
    id: UUID
    alias_name: str
    price_name: str
    created_at: datetime


class MealCreate(BaseModel):
    meal_week_id: UUID
    meal_date: date
    meal_type: MealType
    dish_name: str = Field(min_length=1, max_length=160)
    dish_id: UUID | None = None
    servings: int = Field(default=1, gt=0)
    estimated_cost_minor: int | None = Field(default=None, ge=0)
    note: str | None = None


class MealUpdate(BaseModel):
    meal_date: date | None = None
    meal_type: MealType | None = None
    dish_name: str | None = None
    dish_id: UUID | None = None
    servings: int | None = Field(default=None, gt=0)
    estimated_cost_minor: int | None = Field(default=None, ge=0)
    note: str | None = None


class MealResponse(BaseModel):
    id: UUID
    meal_week_id: UUID
    meal_date: date
    meal_type: MealType
    dish_name: str
    dish_id: UUID | None
    servings: int
    estimated_cost_minor: int | None
    note: str | None
    created_at: datetime
    updated_at: datetime


# --- Einkaufsliste ---
class LinkedTransaction(BaseModel):
    id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None


class ShoppingListCreate(BaseModel):
    meal_week_id: UUID
    store: str | None = None
    status: ShoppingListStatus = "planning"


class ShoppingListUpdate(BaseModel):
    store: str | None = None
    status: ShoppingListStatus | None = None


class ShoppingListResponse(BaseModel):
    id: UUID
    meal_week_id: UUID
    store: str | None
    status: ShoppingListStatus
    planned_total_minor: int
    actual_total_minor: int
    linked_transactions: list[LinkedTransaction]
    created_at: datetime
    updated_at: datetime


class ShoppingItemCreate(BaseModel):
    shopping_list_id: UUID
    meal_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    category: str | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str | None = None
    planned_price_minor: int | None = Field(default=None, ge=0)
    actual_price_minor: int | None = Field(default=None, ge=0)
    is_purchased: bool = False
    note: str | None = None


class ShoppingItemUpdate(BaseModel):
    meal_id: UUID | None = None
    name: str | None = None
    category: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = None
    planned_price_minor: int | None = Field(default=None, ge=0)
    actual_price_minor: int | None = Field(default=None, ge=0)
    is_purchased: bool | None = None
    note: str | None = None


class ShoppingItemResponse(BaseModel):
    id: UUID
    shopping_list_id: UUID
    meal_id: UUID | None
    name: str
    category: str | None
    quantity: Decimal
    unit: str | None
    planned_price_minor: int | None
    actual_price_minor: int | None
    is_purchased: bool
    # "Aus Wochenplan fuellen" vermerkt hier den Wochenbedarf, wenn er von der
    # Kaufmenge abweicht ("fuer 750 g benoetigt" bei 1 kg auf der Liste).
    note: str | None
    # Aus dem Wochenplan-Abgleich entstanden - nur solche Zeilen darf ein
    # erneuter Abgleich ueberschreiben. Eigene Spalte statt "meal_id is not
    # null", weil meal_id beim Loeschen der Mahlzeit auf null faellt.
    from_meal_plan: bool
    created_at: datetime
    updated_at: datetime


# --- KI-Vorschlaege ---
# Ausdruecklich VORSCHLAEGE: diese Modelle beschreiben nichts Gespeichertes.
# Geschrieben wird erst ueber die Bulk-Endpunkte unten, nachdem der Nutzer
# ausgewaehlt hat.
class IngredientSuggestion(BaseModel):
    name: str
    quantity: Decimal | None
    unit: str | None
    category: str | None
    # Steht schon in der Zutatenliste - das Frontend waehlt solche Zeilen ab,
    # damit ein zweiter Lauf nicht alles verdoppelt.
    already_present: bool


class IngredientSuggestionResult(BaseModel):
    dish_id: UUID
    dish_name: str
    servings: int
    model_name: str | None
    ingredients: list[IngredientSuggestion]


class WeekPlanSuggestionInput(BaseModel):
    # Freitext des Haushalts: "guenstig", "italienisch", "Geburtstag am
    # Samstag". Bewusst unstrukturiert - das ist der Teil, den ein Modell
    # besser versteht als ein Formular.
    wish: str | None = Field(default=None, max_length=400)
    meal_types: list[MealType] = Field(
        default_factory=lambda: cast(list[MealType], ["dinner"]), min_length=1
    )
    servings: int = Field(default=2, gt=0, le=20)
    # Bereits geplante Mahlzeiten stehen lassen und nur Luecken fuellen.
    skip_planned_days: bool = True


class MealSuggestion(BaseModel):
    meal_date: date
    meal_type: MealType
    dish_id: UUID
    dish_name: str
    servings: int
    # Aus dishes.estimated_cost_minor hochgerechnet, NICHT vom Modell beziffert.
    estimated_cost_minor: int | None
    reason: str | None


class WeekPlanSuggestionResult(BaseModel):
    meal_week_id: UUID
    week_start: date
    budget_minor: int
    model_name: str | None
    meals: list[MealSuggestion]
    # Server-Summe der Einzelschaetzungen - die einzige Zahl, die der Nutzer
    # gegen sein Wochenbudget haelt.
    estimated_total_minor: int
    # Verworfene Modellantworten (erfundenes Gericht, belegter Slot, kaputtes
    # Datum). Sichtbar, damit ein halber Plan nicht wie ein ganzer aussieht.
    discarded_suggestions: int


# --- Uebernehmen von Vorschlaegen (Bulk) ---
class DishIngredientBulkItem(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: str | None = None
    category: str | None = None


class DishIngredientBulkCreate(BaseModel):
    dish_id: UUID
    ingredients: list[DishIngredientBulkItem] = Field(min_length=1, max_length=50)


class MealBulkItem(BaseModel):
    meal_date: date
    meal_type: MealType
    dish_name: str = Field(min_length=1, max_length=160)
    dish_id: UUID | None = None
    servings: int = Field(default=1, gt=0)
    estimated_cost_minor: int | None = Field(default=None, ge=0)
    note: str | None = None


class MealBulkCreate(BaseModel):
    meal_week_id: UUID
    meals: list[MealBulkItem] = Field(min_length=1, max_length=60)


# --- Einkaufsliste aus dem Wochenplan erzeugen ---
class ShoppingListFillResult(BaseModel):
    """Ergebnis von "Aus Wochenplan fuellen".

    `skipped_meals` ist Absicht: Mahlzeiten ohne Katalogbezug oder ohne
    hinterlegte Zutaten koennen nicht beitragen. Sie stillschweigend zu
    uebergehen liesse die Liste vollstaendig aussehen, obwohl die Haelfte des
    Wochenplans fehlt."""

    created_items: int
    merged_items: int
    considered_meals: int
    skipped_meals: list[str]
    # Zutaten ohne verwertbaren Preis: sie stehen auf der Liste, aber ohne
    # Betrag. Die Plansumme ist dann sichtbar unvollstaendig.
    unpriced_items: int


# --- Verknuepfung mit realen Banktransaktionen ---
class PurchaseLinkCreate(BaseModel):
    transaction_id: UUID


class PurchaseLinkResponse(BaseModel):
    id: UUID
    shopping_list_id: UUID
    transaction: LinkedTransaction
    created_at: datetime
