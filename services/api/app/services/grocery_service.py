"""Service-Logik fuer Essensplan, Einkaufsliste und Wochenbudget (Phase 8)."""

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.grocery import LinkedTransaction, MealWeekSummaryResponse
from app.services import grocery_pricing_service


def _to_actual_minor(raw_amount_minor: int) -> int:
    """Normalisiert rohe Buchungsbetraege zu positiven Istkosten - analog
    ``get_budget_summary`` bzw. ``budget_service`` (dort ``spent = -raw_act``).

    Ausgaben sind in ``transactions.amount_minor`` negativ, Plangroessen
    (``meal_weeks.budget_minor``, ``shopping_items.planned_price_minor``) sind
    per CHECK-Constraint >= 0. Ohne Vorzeichenwechsel waeren Plan und Ist nicht
    vergleichbar und der Restbetrag wuechse mit jeder Ausgabe.

    Rueckerstattungen (positive Buchung auf einer Lebensmittelkategorie, z.B.
    Retoure oder Pfandgutschrift) ergeben hier bewusst einen negativen Beitrag
    und mindern damit die Istkosten der Liste bzw. der Woche.
    """
    return -raw_amount_minor


async def fetch_shopping_list_totals(
    session: AsyncSession, household_id: UUID, shopping_list_id: UUID
) -> tuple[int, int, list[LinkedTransaction]]:
    """Plan- (aus shopping_items) und Istkosten (aus verknuepften, nicht
    archivierten Transaktionen) fuer eine Einkaufsliste - getrennt berechnet,
    keine Verrechnung untereinander (Abschnitt 6.8)."""
    planned_row = (
        await session.execute(
            text(
                "select coalesce(sum(planned_price_minor), 0) as total "
                "from shopping_items where shopping_list_id = :id"
            ),
            {"id": str(shopping_list_id)},
        )
    ).one()

    tx_res = await session.execute(
        text(
            "select t.id, t.booked_date, t.amount_minor, t.payee_label "
            "from shopping_purchase_links spl "
            "join transactions t on t.id = spl.transaction_id "
            "where spl.shopping_list_id = :id and not t.is_archived "
            "order by t.booked_date desc, t.created_at desc"
        ),
        {"id": str(shopping_list_id)},
    )
    # LinkedTransaction.amount_minor bleibt bewusst ROH (negativ fuer Ausgaben):
    # das ist die echte Buchung, die im Client als Buchungsbetrag angezeigt und
    # verlinkt wird - normalisiert wird nur die aggregierte Istkosten-Summe.
    linked_transactions = [LinkedTransaction(**row._mapping) for row in tx_res]
    actual_total_minor = _to_actual_minor(sum(t.amount_minor for t in linked_transactions))

    return planned_row.total, actual_total_minor, linked_transactions


async def fetch_meal_week_summary(
    session: AsyncSession, household_id: UUID, meal_week_id: UUID
) -> MealWeekSummaryResponse | None:
    week_row = (
        await session.execute(
            text(
                "select id, week_start, budget_minor from meal_weeks "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(meal_week_id), "household_id": str(household_id)},
        )
    ).first()
    if week_row is None:
        return None

    meals_row = (
        await session.execute(
            text(
                "select coalesce(sum(estimated_cost_minor), 0) as total "
                "from meals where meal_week_id = :id"
            ),
            {"id": str(meal_week_id)},
        )
    ).one()

    items_row = (
        await session.execute(
            text(
                "select coalesce(sum(si.planned_price_minor), 0) as total "
                "from shopping_items si "
                "join shopping_lists sl on sl.id = si.shopping_list_id "
                "where sl.meal_week_id = :id"
            ),
            {"id": str(meal_week_id)},
        )
    ).one()

    actual_row = (
        await session.execute(
            text(
                "select coalesce(sum(t.amount_minor), 0) as total "
                "from shopping_purchase_links spl "
                "join shopping_lists sl on sl.id = spl.shopping_list_id "
                "join transactions t on t.id = spl.transaction_id "
                "where sl.meal_week_id = :id and not t.is_archived"
            ),
            {"id": str(meal_week_id)},
        )
    ).one()

    # Genau eine Normalisierungsstelle je Funktion: ab hier ist actual_minor
    # positiv-als-Ausgabe und damit direkt mit budget_minor vergleichbar.
    actual_minor = _to_actual_minor(actual_row.total)

    return MealWeekSummaryResponse(
        meal_week_id=week_row.id,
        week_start=week_row.week_start,
        budget_minor=week_row.budget_minor,
        meals_estimated_minor=meals_row.total,
        items_planned_minor=items_row.total,
        actual_minor=actual_minor,
        # Negativer Restbetrag = Budgetueberschreitung.
        remaining_vs_budget_minor=week_row.budget_minor - actual_minor,
    )


@dataclass(frozen=True)
class AggregatedIngredient:
    """Eine zusammengefasste Zutat, fertig fuer die Einkaufsliste."""

    name: str
    quantity: Decimal | None
    unit: str | None
    category: str | None
    meal_id: UUID


@dataclass(frozen=True)
class WeekIngredients:
    items: list[AggregatedIngredient]
    considered_meals: int
    # Mahlzeiten, die nichts beitragen konnten (kein Katalogbezug oder keine
    # Zutatenliste). Werden bis in die API-Antwort durchgereicht, damit eine
    # halb gefuellte Liste nicht wie eine vollstaendige aussieht.
    skipped_meals: list[str]


def _merge_key(
    name: str, unit: str | None, conversion: grocery_pricing_service.Conversion | None
) -> tuple[str, str]:
    """Zusammengefasst wird nur, was sich wirklich addieren laesst.

    Ohne Umrechnung bleibt es bei "gleicher Name UND gleiche Einheit":
    "500 g Nudeln" und "1 Pkg Nudeln" sind nicht addierbar und bleiben zwei
    Zeilen, statt eine falsche Summe zu bilden.

    Gibt es fuer die Zutat eine Umrechnung, die diese Einheit kennt, zaehlt
    stattdessen die Basiseinheit. Sonst blieben "2 Zehen" aus einem Gericht
    und "10 g Knoblauch" aus dem naechsten getrennt, und die Packungsrundung
    fiele zweimal an - man kauft zwei Packungen statt einer.

    Die Einheit wird dabei normalisiert. "St", "Stk" und "Stueck" sind
    dieselbe Einheit, und der Rest der Anwendung behandelt sie auch so
    (grocery_pricing_service._UNIT_SYNONYMS). Auf dem Rohtext zu vergleichen
    hiess: derselbe Einkaufsposten stand zweimal auf der Liste, nur weil zwei
    Rezepte ihn unterschiedlich abgekuerzt haben.
    """
    key = name.strip().lower()
    if conversion is not None:
        own_family = "masse" if conversion.base_unit == "g" else "volumen"
        _, family = grocery_pricing_service.to_base_amount_for(Decimal(1), unit, conversion)
        # Nur wenn die Einheit wirklich in der Basis der Zutat landet. Eine
        # Einheit ohne Zeile faellt auf ihre eigene Familie zurueck und bleibt
        # damit - richtigerweise - eine eigene Zeile.
        if family == own_family:
            return (key, family)
    return (key, grocery_pricing_service.normalize_unit(unit))


async def collect_week_ingredients(
    session: AsyncSession,
    household_id: UUID,
    meal_week_id: UUID,
    conversions: dict[str, grocery_pricing_service.Conversion] | None = None,
) -> WeekIngredients:
    """Rechnet die Zutaten aller geplanten Mahlzeiten einer Woche zusammen.

    Die im Katalog hinterlegten Mengen gelten fuer ``dishes.default_servings``
    (so steht es im Rezept: "fuer 4 Personen: 500 g Nudeln"). Weicht die
    geplante Portionszahl ab, wird linear hochgerechnet - ein Gericht fuer 6
    statt 4 Personen braucht das 1,5-Fache.
    """
    rows = (
        await session.execute(
            text(
                "select m.id as meal_id, m.dish_name, m.servings, "
                "       d.default_servings, "
                "       i.name, i.quantity, i.unit, i.category "
                "from meals m "
                "left join dishes d on d.id = m.dish_id "
                "left join dish_ingredients i on i.dish_id = m.dish_id "
                "where m.household_id = :household_id and m.meal_week_id = :meal_week_id "
                "order by m.meal_date asc, m.created_at asc, i.sort_order asc"
            ),
            {"household_id": str(household_id), "meal_week_id": str(meal_week_id)},
        )
    ).all()

    aggregated: dict[tuple[str, str], AggregatedIngredient] = {}
    contributing_meals: set[UUID] = set()
    seen_meals: dict[UUID, str] = {}

    for row in rows:
        seen_meals.setdefault(row.meal_id, row.dish_name)
        if row.name is None:
            # left join ohne Treffer: Mahlzeit ohne Katalogbezug oder ohne
            # hinterlegte Zutaten.
            continue
        contributing_meals.add(row.meal_id)

        quantity: Decimal | None = None
        if row.quantity is not None:
            base = row.default_servings or 1
            factor = Decimal(row.servings or base) / Decimal(base)
            # Zwei Nachkommastellen wie die Spalte selbst; ROUND_HALF_UP, damit
            # aufgerundet wird statt bankers rounding - beim Einkauf ist zu
            # wenig schlimmer als zu viel.
            quantity = (Decimal(row.quantity) * factor).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        row_key = row.name.strip().lower()
        conversion = (conversions or {}).get(row_key)
        key = _merge_key(row.name, row.unit, conversion)
        existing = aggregated.get(key)
        if existing is None:
            aggregated[key] = AggregatedIngredient(
                name=row.name.strip(),
                quantity=quantity,
                unit=row.unit,
                category=row.category,
                meal_id=row.meal_id,
            )
            continue

        # Mengen addieren, sofern beide Seiten eine haben. Fehlt einer Seite
        # die Menge ("Salz"), bleibt die Summe bewusst leer statt eine
        # Teilsumme vorzutaeuschen.
        # In die Einheit der bereits vorhandenen Zeile umrechnen, nicht in die
        # Basiseinheit: die Liste soll weiter "6 Zehen" sagen und nicht
        # "30 g".
        addend = quantity
        if (
            conversion is not None
            and quantity is not None
            and (row.unit or "") != (existing.unit or "")
        ):
            in_base, _ = grocery_pricing_service.to_base_amount_for(
                quantity, row.unit, conversion
            )
            per_existing, _ = grocery_pricing_service.to_base_amount_for(
                Decimal(1), existing.unit, conversion
            )
            addend = (
                (in_base / per_existing).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if per_existing > 0
                else quantity
            )
        merged_quantity = (
            existing.quantity + addend
            if existing.quantity is not None and addend is not None
            else None
        )
        aggregated[key] = replace(
            existing,
            quantity=merged_quantity,
            category=existing.category or row.category,
        )

    skipped = [name for meal_id, name in seen_meals.items() if meal_id not in contributing_meals]
    return WeekIngredients(
        items=list(aggregated.values()),
        considered_meals=len(contributing_meals),
        skipped_meals=skipped,
    )
