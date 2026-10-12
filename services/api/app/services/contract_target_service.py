"""Fixkostenziel und Senkungsszenarien (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, I7/I8).

Die Fixkostenquote beantwortet "wie stehe ich da". Dieses Modul beantwortet die
beiden Fragen dahinter: "wo will ich hin" (Ziel) und "was bringt es, wenn ich X
kuendige" (Szenario).

Drei Festlegungen praegen den Code:

* **Ein Szenario aendert keinen Vertrag.** Es ist eine Absichtserklaerung, kein
  Vollzug. Wuerde es die Vertraege anfassen, zeigte das Budget sofort eine
  Ersparnis an, die es noch gar nicht gibt.
* **Die Bezugsgroesse der Quote ist dieselbe wie im Budget** - gebuchte
  Einnahmen, ersatzweise die Einnahmevertraege. Deshalb laeuft sie ueber
  `budget_service.booked_income_minor` und `budget_service.fixed_costs_snapshot`
  und nicht ueber eine eigene Abfrage: zwei Abfragen waeren zwei Quoten unter
  einem Namen.
* **Normiert wird je Vertrag, dann summiert** - wie in
  `contract_service.build_summary`. Wuerde erst summiert und dann normiert,
  wichen Zielkarte und Fixkostensumme um Rundungscent ab.

Zur Nebenlaeufigkeit: die Transaktion umschliesst den gesamten Request
(`auth/dependencies.get_household_session`), deshalb committet hier nichts.
"""

import calendar
from datetime import date
from typing import Any, Final, NamedTuple, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.contract import (
    ContractCostTargetInput,
    ContractCostTargetView,
    ContractScenario,
    ContractScenarioCreate,
    ContractScenarioItem,
    ContractScenarioItemInput,
    ContractScenarioUpdate,
)
from app.services import budget_service, contract_service

SCENARIO_NOT_FOUND: Final = "contract_scenario_not_found"
SCENARIO_CONTRACT_NOT_FOUND: Final = "contract_scenario_contract_not_found"
SCENARIO_CONTRACT_NOT_EXPENSE: Final = "contract_scenario_contract_not_expense"
SCENARIO_CONTRACT_NOT_RUNNING: Final = "contract_scenario_contract_not_running"
SCENARIO_REDUCE_NOT_LOWER: Final = "contract_scenario_reduce_not_lower"
SCENARIO_DUPLICATE_CONTRACT: Final = "contract_scenario_duplicate_contract"

# Vorgabe, solange niemand ein Ziel gesetzt hat. Sie entspricht der unteren
# Schwelle der Fixkostenampel (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, I7) - ein
# anderer Wert haette zwei verschiedene "gut" nebeneinander gestellt.
_DEFAULT_TARGET_RATIO: Final = 50.0


class _Baseline(NamedTuple):
    """Ist-Zustand, gegen den Ziel und Szenario rechnen."""

    monthly_expense_minor: int
    monthly_income_minor: int
    income_basis_minor: int
    income_basis: str
    contract_count: int


def _ratio_percent(expense_minor: int, income_basis_minor: int) -> float:
    """Fixkostenquote mit derselben Rundung wie im Budget."""
    if income_basis_minor <= 0:
        return 0.0
    return round(expense_minor / income_basis_minor * 100.0, 1)


async def _load_baseline(
    session: AsyncSession, household_id: UUID, today: date
) -> _Baseline:
    month_start = date(today.year, today.month, 1)
    month_end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])

    fixed = await budget_service.fixed_costs_snapshot(
        session, household_id, month_start, month_end
    )
    booked_income = await budget_service.booked_income_minor(
        session, household_id, month_start, month_end
    )

    # Gleiche Ersatzregel wie im Budget: ohne gebuchte Einnahmen treten die
    # Einnahmevertraege an ihre Stelle. Eine Quote von 0 % waere am
    # Monatsersten sonst jedes Mal die falsche Entwarnung.
    if booked_income > 0:
        return _Baseline(
            fixed.expense_monthly_minor,
            fixed.income_monthly_minor,
            booked_income,
            "actual",
            fixed.contract_count,
        )
    return _Baseline(
        fixed.expense_monthly_minor,
        fixed.income_monthly_minor,
        fixed.income_monthly_minor,
        "contract",
        fixed.contract_count,
    )


# ---------------------------------------------------------------------------
# Ziel
# ---------------------------------------------------------------------------


class _StoredTarget(NamedTuple):
    target_ratio_percent: float
    target_monthly_minor: int | None
    target_date: date | None
    note: str | None
    is_configured: bool


async def _load_target(session: AsyncSession, household_id: UUID) -> _StoredTarget:
    result = await session.execute(
        text(
            "select target_ratio_percent, target_monthly_minor, target_date, note "
            "from contract_cost_targets where household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        return _StoredTarget(_DEFAULT_TARGET_RATIO, None, None, None, False)
    return _StoredTarget(
        float(row.target_ratio_percent),
        int(row.target_monthly_minor) if row.target_monthly_minor is not None else None,
        row.target_date,
        row.note,
        True,
    )


def _effective_target_minor(stored: _StoredTarget, baseline: _Baseline) -> int:
    """Zielbetrag in Euro-Minor.

    Ein ausdruecklich gesetzter Betrag fuehrt. Sonst entsteht er aus der Quote -
    und ohne Bezugsgroesse aus gar nichts: 0 waere hier die gefaehrlichste
    Antwort, sie behauptete eine Luecke in voller Hoehe der Fixkosten, wo
    schlicht kein Einkommen bekannt ist. Deshalb faellt der Zielbetrag dann auf
    die heutigen Fixkosten zurueck, die Luecke ist 0 und die Oberflaeche kann
    ueber `income_basis` erklaeren, warum.
    """
    if stored.target_monthly_minor is not None:
        return stored.target_monthly_minor
    if baseline.income_basis_minor <= 0:
        return baseline.monthly_expense_minor
    return round(baseline.income_basis_minor * stored.target_ratio_percent / 100.0)


async def get_target(
    session: AsyncSession, household_id: UUID, *, today: date
) -> ContractCostTargetView:
    baseline = await _load_baseline(session, household_id, today)
    stored = await _load_target(session, household_id)
    active = await _load_active_scenario(session, household_id, baseline, stored)
    return _build_target_view(stored, baseline, active)


def _build_target_view(
    stored: _StoredTarget, baseline: _Baseline, active: ContractScenario | None
) -> ContractCostTargetView:
    effective = _effective_target_minor(stored, baseline)
    gap = baseline.monthly_expense_minor - effective
    return ContractCostTargetView(
        target_ratio_percent=stored.target_ratio_percent,
        target_monthly_minor=stored.target_monthly_minor,
        target_date=stored.target_date,
        note=stored.note,
        is_configured=stored.is_configured,
        current_monthly_minor=baseline.monthly_expense_minor,
        current_yearly_minor=baseline.monthly_expense_minor * 12,
        current_ratio_percent=_ratio_percent(
            baseline.monthly_expense_minor, baseline.income_basis_minor
        ),
        income_basis_minor=baseline.income_basis_minor,
        income_basis=cast(Any, baseline.income_basis),
        contract_count=baseline.contract_count,
        effective_target_monthly_minor=effective,
        gap_monthly_minor=gap,
        gap_yearly_minor=gap * 12,
        on_track=gap <= 0,
        active_scenario=active,
    )


async def put_target(
    session: AsyncSession,
    household_id: UUID,
    payload: ContractCostTargetInput,
    *,
    today: date,
) -> ContractCostTargetView:
    await session.execute(
        text(
            "insert into contract_cost_targets "
            "(household_id, target_ratio_percent, target_monthly_minor, target_date, note) "
            "values (:household_id, :ratio, :monthly, :target_date, :note) "
            "on conflict (household_id) do update set "
            "target_ratio_percent = excluded.target_ratio_percent, "
            "target_monthly_minor = excluded.target_monthly_minor, "
            "target_date = excluded.target_date, "
            "note = excluded.note, "
            "updated_at = now()"
        ),
        {
            "household_id": str(household_id),
            "ratio": round(payload.target_ratio_percent, 2),
            "monthly": payload.target_monthly_minor,
            "target_date": payload.target_date,
            "note": payload.note,
        },
    )
    return await get_target(session, household_id, today=today)


# ---------------------------------------------------------------------------
# Szenarien
# ---------------------------------------------------------------------------

# Ein gekuendigter Vertrag kostet bis zum Enddatum weiter Geld und laesst sich
# deshalb noch senken; pausierte und beendete nicht. Bewusst dieselbe Menge wie
# contract_service._COUNTING_STATUSES und budget_service._FIXED_COSTS_STATUSES.
_PLANNABLE_STATUSES: Final = ("active", "cancelled")


def _item_effect(
    row: Any, action: str, target_amount_minor: int | None
) -> tuple[int, int]:
    """(aktueller Monatswert, geplanter Monatswert) einer Position."""
    current = contract_service.monthly_minor(int(row.amount_minor), row.frequency)
    if action == "cancel":
        return current, 0
    return current, contract_service.monthly_minor(int(target_amount_minor or 0), row.frequency)


async def _load_scenario_items(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> list[ContractScenarioItem]:
    result = await session.execute(
        text(
            "select i.id, i.contract_id, i.action, i.target_amount_minor, i.note, "
            "c.name, c.contract_type, c.frequency, c.amount_minor "
            "from contract_scenario_items i "
            "join contracts c on c.id = i.contract_id "
            "where i.household_id = :household_id and i.scenario_id = :scenario_id "
            "order by c.name asc"
        ),
        {"household_id": str(household_id), "scenario_id": str(scenario_id)},
    )

    items: list[ContractScenarioItem] = []
    for row in result:
        target_amount = (
            int(row.target_amount_minor) if row.target_amount_minor is not None else None
        )
        current, planned = _item_effect(row, row.action, target_amount)
        items.append(
            ContractScenarioItem(
                id=row.id,
                contract_id=row.contract_id,
                contract_name=row.name,
                contract_type=cast(Any, row.contract_type),
                frequency=cast(Any, row.frequency),
                action=cast(Any, row.action),
                amount_minor=int(row.amount_minor),
                target_amount_minor=target_amount,
                note=row.note,
                current_monthly_minor=current,
                planned_monthly_minor=planned,
                monthly_saving_minor=current - planned,
                yearly_saving_minor=(current - planned) * 12,
            )
        )
    return items


def _assemble_scenario(
    row: Any,
    items: list[ContractScenarioItem],
    baseline: _Baseline,
    stored: _StoredTarget,
) -> ContractScenario:
    monthly_saving = sum(item.monthly_saving_minor for item in items)
    resulting = baseline.monthly_expense_minor - monthly_saving
    return ContractScenario(
        id=row.id,
        name=row.name,
        is_active=bool(row.is_active),
        created_at=row.created_at,
        updated_at=row.updated_at,
        items=items,
        monthly_saving_minor=monthly_saving,
        yearly_saving_minor=monthly_saving * 12,
        resulting_monthly_minor=resulting,
        resulting_ratio_percent=_ratio_percent(resulting, baseline.income_basis_minor),
        reaches_target=resulting <= _effective_target_minor(stored, baseline),
    )


async def _load_scenario_row(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> Any:
    result = await session.execute(
        text(
            "select id, name, is_active, created_at, updated_at from contract_scenarios "
            "where household_id = :household_id and id = :scenario_id"
        ),
        {"household_id": str(household_id), "scenario_id": str(scenario_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_NOT_FOUND)
    return row


async def _load_active_scenario(
    session: AsyncSession, household_id: UUID, baseline: _Baseline, stored: _StoredTarget
) -> ContractScenario | None:
    result = await session.execute(
        text(
            "select id, name, is_active, created_at, updated_at from contract_scenarios "
            "where household_id = :household_id and is_active"
        ),
        {"household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        return None
    items = await _load_scenario_items(session, household_id, row.id)
    return _assemble_scenario(row, items, baseline, stored)


async def list_scenarios(
    session: AsyncSession, household_id: UUID, *, today: date
) -> list[ContractScenario]:
    baseline = await _load_baseline(session, household_id, today)
    stored = await _load_target(session, household_id)

    result = await session.execute(
        text(
            "select id, name, is_active, created_at, updated_at from contract_scenarios "
            "where household_id = :household_id "
            "order by is_active desc, created_at desc"
        ),
        {"household_id": str(household_id)},
    )
    rows = result.all()

    scenarios: list[ContractScenario] = []
    for row in rows:
        items = await _load_scenario_items(session, household_id, row.id)
        scenarios.append(_assemble_scenario(row, items, baseline, stored))
    return scenarios


async def get_scenario(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, *, today: date
) -> ContractScenario:
    row = await _load_scenario_row(session, household_id, scenario_id)
    baseline = await _load_baseline(session, household_id, today)
    stored = await _load_target(session, household_id)
    items = await _load_scenario_items(session, household_id, scenario_id)
    return _assemble_scenario(row, items, baseline, stored)


async def _assert_items_plannable(
    session: AsyncSession, household_id: UUID, items: list[ContractScenarioItemInput]
) -> None:
    """Prueft jede Position, bevor irgendetwas geschrieben wird.

    Vier Ablehnungsgruende, alle mit demselben Motiv: eine Ersparnis, die es in
    der Wirklichkeit nicht geben kann, darf im Plan nicht auftauchen. Ein
    Einnahmevertrag "spart" nichts, ein beendeter Vertrag kostet nichts mehr,
    und eine "Senkung" auf einen hoeheren Betrag ist keine.
    """
    seen: set[UUID] = set()
    for item in items:
        if item.contract_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=SCENARIO_DUPLICATE_CONTRACT
            )
        seen.add(item.contract_id)

        result = await session.execute(
            text(
                "select direction, status, amount_minor from contracts "
                "where household_id = :household_id and id = :contract_id"
            ),
            {"household_id": str(household_id), "contract_id": str(item.contract_id)},
        )
        row = result.first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_CONTRACT_NOT_FOUND
            )
        if row.direction != "expense":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=SCENARIO_CONTRACT_NOT_EXPENSE,
            )
        if row.status not in _PLANNABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=SCENARIO_CONTRACT_NOT_RUNNING,
            )
        if item.action == "reduce" and (item.target_amount_minor or 0) >= int(row.amount_minor):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=SCENARIO_REDUCE_NOT_LOWER
            )


async def _replace_items(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    items: list[ContractScenarioItemInput],
) -> None:
    await session.execute(
        text(
            "delete from contract_scenario_items "
            "where household_id = :household_id and scenario_id = :scenario_id"
        ),
        {"household_id": str(household_id), "scenario_id": str(scenario_id)},
    )
    for item in items:
        await session.execute(
            text(
                "insert into contract_scenario_items "
                "(household_id, scenario_id, contract_id, action, target_amount_minor, note) "
                "values (:household_id, :scenario_id, :contract_id, :action, :amount, :note)"
            ),
            {
                "household_id": str(household_id),
                "scenario_id": str(scenario_id),
                "contract_id": str(item.contract_id),
                "action": item.action,
                "amount": item.target_amount_minor,
                "note": item.note,
            },
        )


async def _deactivate_others(
    session: AsyncSession, household_id: UUID, keep_id: UUID | None
) -> None:
    """Haelt den partiellen Unique-Index einlesbar statt ihn krachen zu lassen.

    Ohne diesen Schritt lieferte das Aktivsetzen eines zweiten Szenarios einen
    Datenbankfehler statt der erwarteten Umschaltung - fachlich ist "das ist
    jetzt mein Plan" aber genau eine Umschaltung.
    """
    await session.execute(
        text(
            "update contract_scenarios set is_active = false, updated_at = now() "
            "where household_id = :household_id and is_active "
            # Der Cast steht auch im IS NULL: ohne ihn kann Postgres den Typ des
            # Parameters nicht bestimmen und lehnt die Anweisung ab.
            "and (cast(:keep_id as uuid) is null or id <> cast(:keep_id as uuid))"
        ),
        {
            "household_id": str(household_id),
            "keep_id": str(keep_id) if keep_id is not None else None,
        },
    )


async def create_scenario(
    session: AsyncSession,
    household_id: UUID,
    payload: ContractScenarioCreate,
    *,
    today: date,
) -> ContractScenario:
    await _assert_items_plannable(session, household_id, payload.items)

    result = await session.execute(
        text(
            "insert into contract_scenarios (household_id, name, is_active) "
            "values (:household_id, :name, false) returning id"
        ),
        {"household_id": str(household_id), "name": payload.name.strip()},
    )
    scenario_id = cast(UUID, result.scalar_one())

    await _replace_items(session, household_id, scenario_id, payload.items)
    if payload.is_active:
        await _deactivate_others(session, household_id, scenario_id)
        await session.execute(
            text(
                "update contract_scenarios set is_active = true, updated_at = now() "
                "where household_id = :household_id and id = :scenario_id"
            ),
            {"household_id": str(household_id), "scenario_id": str(scenario_id)},
        )

    return await get_scenario(session, household_id, scenario_id, today=today)


async def update_scenario(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    payload: ContractScenarioUpdate,
    *,
    today: date,
) -> ContractScenario:
    await _load_scenario_row(session, household_id, scenario_id)

    if payload.items is not None:
        await _assert_items_plannable(session, household_id, payload.items)
        await _replace_items(session, household_id, scenario_id, payload.items)

    if payload.is_active:
        await _deactivate_others(session, household_id, scenario_id)

    sets: list[str] = []
    params: dict[str, Any] = {
        "household_id": str(household_id),
        "scenario_id": str(scenario_id),
    }
    if payload.name is not None:
        sets.append("name = :name")
        params["name"] = payload.name.strip()
    if payload.is_active is not None:
        sets.append("is_active = :is_active")
        params["is_active"] = payload.is_active

    # `updated_at` auch dann, wenn nur Positionen gewechselt haben: die
    # Zeitangabe soll den Plan beschreiben, nicht seine Ueberschrift.
    sets.append("updated_at = now()")
    await session.execute(
        text(
            f"update contract_scenarios set {', '.join(sets)} "
            "where household_id = :household_id and id = :scenario_id"
        ),
        params,
    )

    return await get_scenario(session, household_id, scenario_id, today=today)


async def delete_scenario(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> None:
    await _load_scenario_row(session, household_id, scenario_id)
    await session.execute(
        text(
            "delete from contract_scenarios "
            "where household_id = :household_id and id = :scenario_id"
        ),
        {"household_id": str(household_id), "scenario_id": str(scenario_id)},
    )
