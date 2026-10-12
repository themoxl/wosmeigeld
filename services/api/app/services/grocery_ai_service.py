"""KI-Vorschlaege fuer die Lebensmittelplanung: Zutaten und Wochenplan.

Beide Funktionen sind reine VORSCHLAEGE - dieser Dienst schreibt nie. Erst ein
ausdrueckliches Uebernehmen legt Zeilen an (``/v1/dish-ingredients/bulk`` bzw.
``/v1/meals/bulk``). Das ist dasselbe Muster wie bei den Kategorienvorschlaegen
(``category_suggestion_service``) und aus demselben Grund: ein Modellvorschlag
ist eine Meinung, kein Datensatz.

Zwei Regeln, die hier den Unterschied zwischen brauchbar und gefaehrlich
machen:

1. **Der Wochenplan darf nur aus dem Katalog waehlen.** ``dish_id`` ist im
   JSON-Schema ein Enum ueber die real vorhandenen Gerichte, und nach dem
   Parsen wird noch einmal gegen den Katalog gefiltert. Ohne das erfindet das
   Modell Gerichte, zu denen es weder Zutaten noch Kostenschaetzung gibt - der
   Plan saehe vollstaendig aus und waere leer.
2. **Geldbetraege rechnet der Server, nicht das Modell.** Die Kostenschaetzung
   eines Plans ist die Summe der hinterlegten ``dishes.estimated_cost_minor``.
   Das Modell waehlt die Kombination, es beziffert sie nicht. Dieselbe Trennung
   gilt im ganzen Projekt (siehe ``Insight.ai_advice``).

Die Zutatenmengen sind die eine bewusste Ausnahme: sie kommen zwangslaeufig vom
Modell. Deshalb sind sie ausdruecklich Vorschlag und muessen den Review-Schritt
durchlaufen, bevor sie in ``dish_ingredients`` landen.

Modellaufruf: ueber den Gateway des KI-Kerns (app/llm) - Antwortbudget aus dem
geladenen Fenster, llm_calls-Zeile je Aufruf. Liefert das Modell nichts
Verwertbares (Server weg, Zeitbudget, abgeschnittene oder kaputte Antwort),
scheitert der Vorschlag mit benanntem Grund (ai_answers.AIStepError, HTTP 503)
statt mit einer leeren Liste: "das Modell lief nicht" und "das Modell hatte
keine Idee" sind fuer den Nutzer voellig verschiedene Lagen. Eine leere Liste
bleibt die gueltige Antwort eines Modells, das das Gericht nicht kennt.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.llm import answers, reasons
from app.schemas.grocery import (
    IngredientSuggestion,
    IngredientSuggestionResult,
    MealSuggestion,
    MealType,
    WeekPlanSuggestionResult,
)
from app.services import ai_answers, ai_settings_service, grocery_pricing_service

# llm_calls: Stufen und Vertragsversionen der beiden Vorschlaege.
INGREDIENT_STAGE: Final = "grocery_ingredients"
INGREDIENT_PROMPT_VERSION: Final = "grocery-ingredients-v1"
PLAN_STAGE: Final = "grocery_week_plan"
PLAN_PROMPT_VERSION: Final = "grocery-week-plan-v1"

MEAL_TYPE_LABELS: Final[dict[str, str]] = {
    "breakfast": "Fruehstueck",
    "lunch": "Mittagessen",
    "dinner": "Abendessen",
    "snack": "Snack",
}

WEEKDAY_LABELS: Final = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)

# Ein Gericht fuer mehr als 20 Personen ist im Haushaltskontext kein Plan mehr,
# sondern ein Vertipper des Modells.
# fest: Ausgabepruefung - Portionen je Mahlzeit, keine KI-Grenze
_MAX_SERVINGS: Final = 20
# fest: Ausgabepruefung - mehr als 25 Zutaten fuer ein Haushaltsgericht ist ein Ausreisser
_MAX_INGREDIENTS: Final = 25
# Sinnlose Zutatennamen: Platzhalter, die das Modell einsetzt, wenn es nichts
# weiss. Als Einkaufsposten waeren sie wertlos.
_FORBIDDEN_INGREDIENTS: Final = frozenset(
    {"zutat", "zutaten", "sonstiges", "diverses", "etc", "usw", "unbekannt", "n/a", "-"}
)
_SANE_NAME: Final = re.compile(r"^[\wÄÖÜäöüß0-9 &.,\-/()%+]{2,80}$", re.UNICODE)


async def _ask_model(
    session: AsyncSession,
    household_id: UUID,
    *,
    stage: str,
    prompt_version: str,
    system_prompt: str,
    user_prompt: str,
    json_schema: dict[str, Any],
    key: str,
    compact_user_prompt: str | None = None,
) -> tuple[list[Any], str]:
    """Ein Modellaufruf ueber den Gateway: (Liste unter ``key``, Modellname).

    Struktur zuerst; lehnt der Server ``response_format`` ab, genau ein
    zweiter Aufruf ohne Schema (llm.chat_structured). Jeder Fehlschlag ist
    ein AIStepError mit dem Grund des KI-Kerns - auch eine Antwort, die
    abgeschnitten ist, kein JSON enthaelt oder unter ``key`` keine Liste
    traegt (dann wird die llm_calls-Zeile des Aufrufs markiert).

    ``compact_user_prompt`` ist die knappe Fassung des Nutzerprompts, die der
    Gateway einsetzt, wenn die volle samt Antwortreserve nicht ins Fenster
    passt; passt auch sie nicht, scheitert der Aufruf mit
    model_input_too_large und Aufschluesselung."""
    settings = await ai_settings_service.resolve(session, household_id)
    target = await ai_answers.require_target(settings)
    scope = llm.CallScope(
        session=session, household_id=household_id, stage=stage, prompt_version=prompt_version
    )
    hooks: llm.ChatHooks | None = None
    if compact_user_prompt is not None and compact_user_prompt != user_prompt:
        compact = compact_user_prompt

        async def use_compact(
            messages: list[dict[str, Any]], budget: llm.Budget
        ) -> list[dict[str, Any]] | None:
            del budget
            if messages[-1].get("content") != user_prompt:
                return None
            return [*messages[:-1], {"role": "user", "content": compact}]

        hooks = llm.ChatHooks(compact=use_compact)

    answer: answers.Answer[Any] = await answers.ask(
        answers.Ready(settings, target, scope),
        llm.ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_schema", "json_schema": json_schema},
        ),
        hooks=hooks,
    )
    if not answer.ok:
        raise ai_answers.AIStepError(
            answer.reason_code or reasons.MODEL_OUTPUT_UNUSABLE, answer.message or ""
        )
    try:
        items = answers.json_object(answer.text).get(key)
        if not isinstance(items, list):
            raise ValueError(f"Die Modellantwort enthält keine Liste unter „{key}“.")
    except ValueError as exc:
        rejected = await answers.reject(
            scope, answer.result, reasons.MODEL_OUTPUT_UNUSABLE, str(exc)
        )
        raise ai_answers.AIStepError(
            rejected.reason_code or reasons.MODEL_OUTPUT_UNUSABLE, rejected.message or ""
        ) from exc
    return items, target.model


def _clean_name(raw: Any) -> str | None:
    name = str(raw or "").strip()
    if not name or not _SANE_NAME.match(name):
        return None
    if name.lower() in _FORBIDDEN_INGREDIENTS:
        return None
    return name


def _clean_quantity(raw: Any) -> Decimal | None:
    """Menge oder None. None ist ein gueltiges Ergebnis ("Salz nach
    Geschmack") - deshalb wird hier nicht auf 1 zurueckgefallen."""
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None
    if value <= 0 or value > Decimal("100000"):
        return None
    return value.quantize(Decimal("0.01"))


def _optional_text(raw: Any, max_length: int = 40) -> str | None:
    value = str(raw or "").strip()
    return value[:max_length] if value else None


# ---------------------------------------------------------------------------
# A: Zutaten zu einem Gericht vorschlagen
# ---------------------------------------------------------------------------

INGREDIENT_SYSTEM_PROMPT: Final = (
    "Du bist ein Kuechenassistent fuer einen deutschsprachigen Privathaushalt. "
    "Du bekommst den Namen eines Gerichts und eine Personenzahl und nennst die "
    "Einkaufszutaten dafuer.\n"
    "Regeln:\n"
    "- Mengen gelten fuer die genannte Personenzahl, in metrischen "
    "Haushaltseinheiten (g, kg, ml, l, Stk, Pkg, Dose, Bund, TL, EL).\n"
    "- Nenne, was man einkaufen muss. Wasser aus der Leitung gehoert nicht dazu.\n"
    "- Grundgewuerze und Kleinkram (Salz, Pfeffer, Oel) darfst du ohne Menge "
    "nennen: quantity und unit dann leer lassen.\n"
    "- category ist die Ladenabteilung (z. B. Gemuese, Fleisch, Molkerei, "
    "Trockenware, Konserven, Gewuerze, Tiefkuehl).\n"
    "- Keine Zubereitungsschritte, keine Erklaerungen, nur Zutaten.\n"
    "- Kennst du das Gericht nicht, gib eine leere Liste zurueck statt zu raten."
)


def build_ingredient_user_prompt(
    dish_name: str, servings: int, note: str | None, existing: list[str]
) -> str:
    lines = [f"Gericht: {dish_name}", f"Personen: {servings}"]
    if note:
        lines.append(f"Notiz des Haushalts: {note}")
    if existing:
        # Bereits Erfasstes mitgeben, damit das Modell ergaenzt statt zu
        # wiederholen. Doppelte werden zusaetzlich clientseitig markiert.
        lines.append("Bereits erfasst (nicht wiederholen): " + ", ".join(existing))
    return "\n".join(lines)


def build_ingredient_json_schema() -> dict[str, Any]:
    return {
        "name": "dish_ingredients",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "ingredients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            # Menge als String: so kann das Modell sie leer
                            # lassen, ohne dass ein strict-Schema mit
                            # nullable-Zahl noetig waere (das beherrschen
                            # nicht alle Runtimes zuverlaessig).
                            "quantity": {"type": "string"},
                            "unit": {"type": "string"},
                            "category": {"type": "string"},
                        },
                        "required": ["name", "quantity", "unit", "category"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["ingredients"],
            "additionalProperties": False,
        },
    }


async def suggest_ingredients(
    session: AsyncSession, household_id: UUID, dish_id: UUID
) -> IngredientSuggestionResult:
    dish = (
        await session.execute(
            text(
                "select id, name, default_servings, note from dishes "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(dish_id), "household_id": str(household_id)},
        )
    ).first()
    if dish is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dish_not_found")

    existing_rows = (
        await session.execute(
            text("select name from dish_ingredients where dish_id = :dish_id order by sort_order"),
            {"dish_id": str(dish_id)},
        )
    ).all()
    existing_names = [row.name for row in existing_rows]
    existing_lookup = {name.strip().lower() for name in existing_names}

    items, model_name = await _ask_model(
        session,
        household_id,
        stage=INGREDIENT_STAGE,
        prompt_version=INGREDIENT_PROMPT_VERSION,
        system_prompt=INGREDIENT_SYSTEM_PROMPT,
        user_prompt=build_ingredient_user_prompt(
            dish.name, dish.default_servings, dish.note, existing_names
        ),
        json_schema=build_ingredient_json_schema(),
        key="ingredients",
    )

    suggestions: list[IngredientSuggestion] = []
    seen: set[str] = set()
    for raw in items[:_MAX_INGREDIENTS]:
        if not isinstance(raw, dict):
            continue
        name = _clean_name(raw.get("name"))
        if name is None or name.lower() in seen:
            continue
        seen.add(name.lower())
        suggestions.append(
            IngredientSuggestion(
                name=name,
                quantity=_clean_quantity(raw.get("quantity")),
                unit=_optional_text(raw.get("unit"), 20),
                category=_optional_text(raw.get("category")),
                already_present=name.lower() in existing_lookup,
            )
        )

    return IngredientSuggestionResult(
        dish_id=dish.id,
        dish_name=dish.name,
        servings=dish.default_servings,
        model_name=model_name,
        ingredients=suggestions,
    )


# ---------------------------------------------------------------------------
# B: Wochenplan aus dem Katalog vorschlagen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CatalogDish:
    """Katalogeintrag mit bereits aufgeloestem Preis, damit Prompt und
    Kostenrechnung unten mit demselben Wert arbeiten."""

    id: UUID
    name: str
    category: str | None
    default_meal_type: str | None
    default_servings: int
    estimated_cost_minor: int | None


def _effective_cost(
    row: Any, dish_costs: dict[UUID, grocery_pricing_service.DishCost]
) -> int | None:
    if row.estimated_cost_minor is not None:
        return int(row.estimated_cost_minor)
    cost = dish_costs.get(row.id)
    return cost.computed_cost_minor if cost and cost.priced_ingredients > 0 else None

PLAN_SYSTEM_PROMPT: Final = (
    "Du planst die Woche fuer einen deutschsprachigen Privathaushalt.\n"
    "Regeln:\n"
    "- Du waehlst AUSSCHLIESSLICH aus dem vorgegebenen Gerichtekatalog. "
    "Erfinde niemals ein Gericht und niemals eine dish_id.\n"
    "- Ein Gericht darf sich in einer Woche hoechstens zweimal wiederholen; "
    "sorge fuer Abwechslung.\n"
    "- Halte dich an die gewuenschten Tage und Mahlzeiten.\n"
    "- Beruecksichtige den Wunsch des Haushalts (z. B. Kueche, Anlass, "
    "guenstig). Passt kein Gericht zum Wunsch, nimm weniger Gerichte statt "
    "unpassende.\n"
    "- Nenne keine Preise und keine Summen; die rechnet die Anwendung selbst."
)


def _dish_catalog_block(dishes: Sequence[Any], *, compact: bool = False) -> str:
    """Der Katalog fuer den Prompt - immer VOLLSTAENDIG, denn gewaehlt werden
    darf nur daraus. Die knappe Fassung laesst nur die beschreibenden Angaben
    weg (Kategorie, typische Mahlzeit, Standardportionen); ID, Name und Preis
    bleiben, weil Auswahl und Wochenbudget daran haengen."""
    lines = []
    for dish in dishes:
        parts = [f"{dish.id} | {dish.name}"]
        if not compact:
            if dish.category:
                parts.append(f"Kategorie: {dish.category}")
            if dish.default_meal_type:
                parts.append(f"typisch: {MEAL_TYPE_LABELS.get(dish.default_meal_type, '')}")
            parts.append(f"Standard: {dish.default_servings} Portionen")
        if dish.estimated_cost_minor is not None:
            parts.append(f"ca. {dish.estimated_cost_minor / 100:.2f} EUR")
        lines.append(" — ".join(parts))
    return "\n".join(lines)


def build_plan_user_prompt(
    dishes: Sequence[Any],
    week_start: date,
    days: list[date],
    meal_types: list[str],
    wish: str | None,
    household_servings: int,
    budget_minor: int,
    *,
    compact: bool = False,
) -> str:
    day_lines = [f"{d.isoformat()} ({WEEKDAY_LABELS[d.weekday()]})" for d in days]
    lines = [
        "Gerichtekatalog (dish_id | Name):",
        _dish_catalog_block(dishes, compact=compact),
        "",
        f"Woche ab {week_start.isoformat()}. Zu planende Tage:",
        *day_lines,
        "",
        "Zu planende Mahlzeiten je Tag: "
        + ", ".join(MEAL_TYPE_LABELS.get(t, t) for t in meal_types),
        f"Ueblich sind {household_servings} Portionen pro Mahlzeit.",
    ]
    if budget_minor > 0:
        # Das Budget ist Kontext fuer die Auswahl, keine Rechenaufgabe - die
        # Summe bildet der Server aus den hinterlegten Schaetzungen.
        lines.append(
            f"Wochenbudget des Haushalts: {budget_minor / 100:.2f} EUR. "
            "Waehle eher guenstige Gerichte, wenn der Katalog teure enthaelt."
        )
    lines.append(f"Wunsch des Haushalts: {wish.strip() if wish else 'keiner, ausgewogen planen'}")
    return "\n".join(lines)


def build_plan_json_schema(
    valid_dish_ids: list[str], days: list[date], meal_types: list[str]
) -> dict[str, Any]:
    """dish_id, Datum und Mahlzeit sind Enums ueber real Vorhandenes. Das ist
    die eigentliche Absicherung: erfundene Gerichte oder Tage ausserhalb der
    Woche kommen so gar nicht erst zurueck."""
    return {
        "name": "week_meal_plan",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "meals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "meal_date": {"type": "string", "enum": [d.isoformat() for d in days]},
                            "meal_type": {"type": "string", "enum": meal_types},
                            "dish_id": {"type": "string", "enum": sorted(valid_dish_ids)},
                            "servings": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["meal_date", "meal_type", "dish_id", "servings", "reason"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["meals"],
            "additionalProperties": False,
        },
    }


async def suggest_week_plan(
    session: AsyncSession,
    household_id: UUID,
    meal_week_id: UUID,
    wish: str | None,
    meal_types: list[MealType],
    servings: int,
    skip_planned_days: bool,
) -> WeekPlanSuggestionResult:
    week = (
        await session.execute(
            text(
                "select id, week_start, budget_minor from meal_weeks "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(meal_week_id), "household_id": str(household_id)},
        )
    ).first()
    if week is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meal_week_not_found")

    catalog_rows = (
        await session.execute(
            text(
                "select id, name, category, default_meal_type, default_servings, "
                "estimated_cost_minor from dishes where household_id = :household_id "
                "order by lower(name)"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    # Der wirksame Preis eines Gerichts ist die Handeingabe, sonst die Summe
    # seiner Zutatenpreise - dieselbe Vorrangregel wie in der Gerichteliste.
    # Ohne das waeren Gerichte, die nur ueber die Preisliste bewertet sind, im
    # Plan kostenlos und das Wochenbudget waere wertlos.
    dish_costs = await grocery_pricing_service.load_dish_costs(session, household_id)
    catalog = [
        _CatalogDish(
            id=row.id,
            name=row.name,
            category=row.category,
            default_meal_type=row.default_meal_type,
            default_servings=row.default_servings,
            estimated_cost_minor=_effective_cost(row, dish_costs),
        )
        for row in catalog_rows
    ]
    if not catalog:
        # Ohne Katalog gibt es nichts zu kombinieren. Das ist kein Modellfehler,
        # sondern eine Bringschuld des Nutzers - deshalb eigene Kennung.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="dish_catalog_empty"
        )

    planned = {
        (row.meal_date, row.meal_type)
        for row in (
            await session.execute(
                text(
                    "select meal_date, meal_type from meals "
                    "where household_id = :household_id and meal_week_id = :week_id"
                ),
                {"household_id": str(household_id), "week_id": str(meal_week_id)},
            )
        ).all()
    }

    # fest: sieben Kalendertage einer Planungswoche, keine Rundenzahl
    days = [week.week_start + timedelta(days=offset) for offset in range(7)]
    open_slots = {
        (day, meal_type)
        for day in days
        for meal_type in meal_types
        if not (skip_planned_days and (day, meal_type) in planned)
    }
    if not open_slots:
        return WeekPlanSuggestionResult(
            meal_week_id=week.id,
            week_start=week.week_start,
            budget_minor=week.budget_minor,
            model_name=None,
            meals=[],
            estimated_total_minor=0,
            discarded_suggestions=0,
        )

    dishes_by_id = {str(dish.id): dish for dish in catalog}
    open_days = sorted({day for day, _ in open_slots})

    def plan_prompt(*, compact: bool) -> str:
        return build_plan_user_prompt(
            catalog,
            week.week_start,
            open_days,
            list(meal_types),
            wish,
            servings,
            week.budget_minor,
            compact=compact,
        )

    items, model_name = await _ask_model(
        session,
        household_id,
        stage=PLAN_STAGE,
        prompt_version=PLAN_PROMPT_VERSION,
        system_prompt=PLAN_SYSTEM_PROMPT,
        user_prompt=plan_prompt(compact=False),
        json_schema=build_plan_json_schema(list(dishes_by_id), days, list(meal_types)),
        key="meals",
        compact_user_prompt=plan_prompt(compact=True),
    )

    meals: list[MealSuggestion] = []
    discarded = 0
    used_slots: set[tuple[date, str]] = set()
    for raw in items:
        if not isinstance(raw, dict):
            discarded += 1
            continue
        dish = dishes_by_id.get(str(raw.get("dish_id", "")).strip())
        if dish is None:
            # Zweite Verteidigungslinie hinter dem Schema-Enum: ohne
            # response_format (Capability-Fallback) kann hier alles ankommen.
            discarded += 1
            continue
        try:
            meal_date = date.fromisoformat(str(raw.get("meal_date", "")).strip())
        except ValueError:
            discarded += 1
            continue
        meal_type = str(raw.get("meal_type", "")).strip()
        slot = (meal_date, meal_type)
        if meal_type not in meal_types or slot not in open_slots or slot in used_slots:
            discarded += 1
            continue
        used_slots.add(slot)

        try:
            slot_servings = int(str(raw.get("servings")))
        except (TypeError, ValueError):
            slot_servings = servings
        slot_servings = max(1, min(_MAX_SERVINGS, slot_servings))

        # Die Kostenschaetzung skaliert mit den Portionen und stammt
        # ausschliesslich aus dem Katalog - nie aus der Modellantwort.
        estimated = (
            round(dish.estimated_cost_minor * slot_servings / max(1, dish.default_servings))
            if dish.estimated_cost_minor is not None
            else None
        )
        meals.append(
            MealSuggestion(
                meal_date=meal_date,
                meal_type=cast(MealType, meal_type),
                dish_id=dish.id,
                dish_name=dish.name,
                servings=slot_servings,
                estimated_cost_minor=estimated,
                reason=_optional_text(raw.get("reason"), 160),
            )
        )

    meals.sort(key=lambda m: (m.meal_date, list(meal_types).index(m.meal_type)))
    return WeekPlanSuggestionResult(
        meal_week_id=week.id,
        week_start=week.week_start,
        budget_minor=week.budget_minor,
        model_name=model_name,
        meals=meals,
        estimated_total_minor=sum(m.estimated_cost_minor or 0 for m in meals),
        discarded_suggestions=discarded,
    )
