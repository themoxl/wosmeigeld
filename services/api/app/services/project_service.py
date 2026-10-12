"""DB-Orchestrierung fuer Vorhaben, Szenarien, Kostenplan, Finanzierung und
Haushaltswirkung (Plan 7.1-7.4).

Reine Rechenlogik lebt in `project_calculation_service.py` (Kosten),
`debt_calculation_service.py`/`debt_plan_service.py` (Tilgung) und
`household_cashflow_service.py` (Monats-Cashflow/Leistbarkeit); dieser Dienst
laedt/schreibt Zeilen, prueft Statusmatrix/Konkurrenz/Haushaltsgrenzen und
schreibt Audit-Ereignisse. Foerderung (P3) und Ist-Kosten-Zuordnung (P4)
folgen in spaeteren Phasen.
"""

import hashlib
import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import extract_sqlstate
from app.schemas.debt_plans import (
    DebtPlanConfigurationInput,
    DebtPlanCreate,
    DebtPlanUpdate,
    DrawInput,
)
from app.schemas.projects import (
    AffordabilityMetrics,
    BulkAssignActualsRequest,
    BulkAssignActualsResult,
    CashflowAssumptions,
    CashflowAssumptionsInput,
    CashflowMonthRow,
    CashflowSuggestion,
    CompletionApplyResponse,
    CompletionPreview,
    CostActual,
    CostActualCreate,
    CostDrawPreview,
    CostDrawPreviewInput,
    CostItem,
    CostItemCreate,
    CostItemUpdate,
    CostScheduleEntry,
    CostScheduleEntryInput,
    CostSection,
    CostSectionCreate,
    CostSectionUpdate,
    EquityAvailability,
    EquityAvailabilityAccount,
    FinancingConfigurationCreate,
    FinancingConfigurationResponse,
    FinancingConfigurationUpdate,
    FinancingSource,
    FinancingSourceCreate,
    FinancingSourceUpdate,
    FundingGap,
    GoalAssumption,
    GoalAssumptionImpact,
    GoalAssumptionInput,
    MajorProject,
    MajorProjectCreate,
    MajorProjectUpdate,
    ProjectDashboardNextPayment,
    ProjectDashboardSummary,
    ProjectDashboardWarning,
    ProjectDeleteImpact,
    ProjectListItem,
    ProjectReportRow,
    ProjectScenario,
    ProjectScenarioCreate,
    ProjectScenarioUpdate,
    RecurringImpact,
    RecurringImpactCreate,
    RecurringImpactUpdate,
    ScenarioCalculation,
    ScenarioCloneRequest,
    ScenarioComparison,
    ScenarioComparisonRow,
    ScenarioFinancialSummary,
    ScenarioTimeline,
    TransactionCandidate,
)
from app.services import debt_plan_service
from app.services.account_balance_service import fetch_current_account_balances
from app.services.audit_service import record_audit_event
from app.services.household_cashflow_service import (
    CashflowAssumptionsInput as EngineCashflowAssumptions,
)
from app.services.household_cashflow_service import (
    CostOutflow,
    DebtCashflow,
    FinancingInflow,
    GoalContribution,
    HouseholdCashflowInput,
    calculate_household_cashflow,
)
from app.services.household_cashflow_service import (
    RecurringImpactInput as EngineRecurringImpactInput,
)
from app.services.project_calculation_service import (
    CostFormulaCycleError,
    CostItemInput,
    ScenarioContingencyInput,
    calculate_project_costs,
)

MAJOR_PROJECT_ENTITY_TYPE: Final = "major_project"
PROJECT_SCENARIO_ENTITY_TYPE: Final = "project_scenario"
PROJECT_COST_ITEM_ENTITY_TYPE: Final = "project_cost_item"
PROJECT_FINANCING_SOURCE_ENTITY_TYPE: Final = "project_financing_source"
PROJECT_COST_ACTUAL_ENTITY_TYPE: Final = "project_cost_actual"

PROJECT_NOT_FOUND: Final = "project_not_found"
SCENARIO_NOT_FOUND: Final = "scenario_not_found"
COST_SECTION_NOT_FOUND: Final = "cost_section_not_found"
COST_ITEM_NOT_FOUND: Final = "cost_item_not_found"
COST_ACTUAL_NOT_FOUND: Final = "cost_actual_not_found"

# Meldung des Triggers check_project_cost_actuals_no_overbooking()
# (supabase/migrations/0001_schema.sql): verhindert, dass eine Buchung ueber
# ihren eigenen Betrag hinaus auf Projektpositionen alloziert wird, auch bei
# zwei gleichzeitigen Zuordnungsversuchen (Plan 4.6/P4-Abnahme).
_CHECK_VIOLATION_SQLSTATE: Final = "23514"
_OVERBOOKING_MESSAGE_MARKER: Final = "project_cost_actuals: Zuordnung ueberschreitet Buchungsbetrag"
ACTUALS_OVERBOOKING: Final = "actuals_overbooking"

# Postgres-Fehlerklasse 40P01: der Deadlock-Detector hat diese Transaktion
# abgebrochen, um einen zyklischen Sperr-Wartezustand aufzuloesen (siehe
# bulk_assign_actuals - die Sortierung nach transaction_id verhindert das im
# Regelfall bereits, dies ist die Absicherung fuer den Rest). Fachlich kein
# Fehler des Aufrufers, ein Retry derselben Anfrage funktioniert i. d. R.
_DEADLOCK_SQLSTATE: Final = "40P01"
ACTUALS_CONCURRENT_RETRY: Final = "actuals_concurrent_retry"


def _is_actuals_overbooking_error(exc: DBAPIError) -> bool:
    if extract_sqlstate(exc) != _CHECK_VIOLATION_SQLSTATE:
        return False
    return _OVERBOOKING_MESSAGE_MARKER in str(getattr(exc, "orig", exc))


ENGINE_VERSION: Final = "v1"

_ALLOWED_TRANSITIONS: Final[dict[str, set[str]]] = {
    "idea": {"planning", "cancelled", "archived"},
    "planning": {"idea", "approved", "cancelled", "archived"},
    "approved": {"planning", "active", "cancelled", "archived"},
    "active": {"planning", "completed", "archived"},
    "completed": {"archived"},
    "cancelled": {"idea", "archived"},
    "archived": {"idea"},
}

# Uebersicht/Berichte (Plan 11.7/P5): "aktiv" heisst hier noch nicht
# abgeschlossen, storniert oder archiviert - dieselbe Grundidee wie die
# Eigenkapitalreservierung in 11.1 ("nicht archivierter Projekte"), hier
# zusaetzlich um completed/cancelled verengt, weil ein beendetes oder
# storniertes Vorhaben keine offene Zahlung, Luecke oder Warnung mehr
# beitraegt.
_DASHBOARD_ACTIVE_STATUSES: Final[tuple[str, ...]] = ("idea", "planning", "approved", "active")

# Rangfolge fuer "wichtigste Warnung" (Plan 11.7): je kleiner der Index,
# desto dringlicher. equity_overallocated/secured_funding_gap/
# goal_protection_breached sind echte Deckungs- bzw. Datenintegritaetsrisiken,
# liquidity_reserve_breached betrifft den Haushalt insgesamt, funding_gap ist
# nur die geplante (nicht gesicherte) Deckung, die letzten beiden sind
# Hinweise ohne unmittelbares Deckungsrisiko. Unbekannte Codes fallen ans
# Ende, werden aber weiterhin angezeigt, wenn nichts Bekanntes vorliegt.
_WARNING_SEVERITY_ORDER: Final[tuple[str, ...]] = (
    "equity_overallocated",
    "secured_funding_gap",
    "goal_protection_breached",
    "liquidity_reserve_breached",
    "funding_gap",
    "cost_schedule_incomplete",
    "calculation_horizon_reached",
)


def _warning_rank(code: str) -> int:
    try:
        return _WARNING_SEVERITY_ORDER.index(code)
    except ValueError:
        return len(_WARNING_SEVERITY_ORDER)


def _row_to_project(row: Any) -> MajorProject:
    return MajorProject(**row._mapping)


def _row_to_scenario(row: Any) -> ProjectScenario:
    return ProjectScenario(**row._mapping)


_PROJECT_COLUMNS = (
    "id, name, project_type, status, currency, country_code, region_code, postal_code, "
    "municipality, planned_start_on, planned_completion_on, actual_start_on, "
    "actual_completion_on, result_asset_id, note, version, created_at, updated_at"
)
_SCENARIO_COLUMNS = (
    "id, project_id, name, scenario_kind, status, source_scenario_id, as_of_date, "
    "contingency_mode, contingency_rate_bps, contingency_fixed_minor, "
    "minimum_liquidity_reserve_minor, calculation_horizon_months, selected_at, "
    "version, created_at, updated_at"
)


async def require_project(
    session: AsyncSession, household_id: UUID, project_id: UUID
) -> MajorProject:
    row = (
        await session.execute(
            text(
                f"select {_PROJECT_COLUMNS} from major_projects "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(project_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND)
    return _row_to_project(row)


async def require_scenario(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_id: UUID
) -> ProjectScenario:
    row = (
        await session.execute(
            text(
                f"select {_SCENARIO_COLUMNS} from project_scenarios "
                "where id = :id and project_id = :project_id and household_id = :household_id"
            ),
            {
                "id": str(scenario_id),
                "project_id": str(project_id),
                "household_id": str(household_id),
            },
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_NOT_FOUND)
    return _row_to_scenario(row)


# ---------------------------------------------------------------------------
# Projekte
# ---------------------------------------------------------------------------


async def list_projects(
    session: AsyncSession,
    household_id: UUID,
    *,
    status_filter: str | None,
    project_type_filter: str | None,
    search: str | None,
    include_archived: bool,
) -> list[ProjectListItem]:
    conditions = ["p.household_id = :household_id"]
    params: dict[str, Any] = {"household_id": str(household_id)}
    if not include_archived:
        conditions.append("p.status <> 'archived'")
    if status_filter:
        conditions.append("p.status = :status_filter")
        params["status_filter"] = status_filter
    if project_type_filter:
        conditions.append("p.project_type = :project_type_filter")
        params["project_type_filter"] = project_type_filter
    if search:
        conditions.append("p.name ilike :search")
        params["search"] = f"%{search}%"

    result = await session.execute(
        text(
            "select p.id, p.name, p.project_type, p.status, p.planned_completion_on, "
            "p.updated_at, s.id as selected_scenario_id, s.name as selected_scenario_name "
            "from major_projects p "
            "left join project_scenarios s on s.project_id = p.id and s.status = 'selected' "
            "where " + " and ".join(conditions) + " "
            "order by p.updated_at desc"
        ),
        params,
    )
    rows = result.fetchall()
    items: list[ProjectListItem] = []
    for row in rows:
        cash_cost: int | None = None
        if row.selected_scenario_id is not None:
            calc = await calculate_scenario(session, household_id, row.id, row.selected_scenario_id)
            cash_cost = calc.project_cash_cost_minor
        items.append(
            ProjectListItem(
                id=row.id,
                name=row.name,
                project_type=row.project_type,
                status=row.status,
                selected_scenario_id=row.selected_scenario_id,
                selected_scenario_name=row.selected_scenario_name,
                project_cash_cost_minor=cash_cost,
                planned_completion_on=row.planned_completion_on,
                updated_at=row.updated_at,
            )
        )
    return items


async def create_project(
    session: AsyncSession, household_id: UUID, user_id: UUID, payload: MajorProjectCreate
) -> MajorProject:
    row = (
        await session.execute(
            text(
                "insert into major_projects "
                "(household_id, name, project_type, currency, country_code, region_code, "
                "postal_code, municipality, planned_start_on, planned_completion_on, note, "
                "created_by) "
                "values (:household_id, :name, :project_type, :currency, :country_code, "
                ":region_code, :postal_code, :municipality, :planned_start_on, "
                ":planned_completion_on, :note, :created_by) "
                f"returning {_PROJECT_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "name": payload.name,
                "project_type": payload.project_type,
                "currency": payload.currency,
                "country_code": payload.country_code,
                "region_code": payload.region_code,
                "postal_code": payload.postal_code,
                "municipality": payload.municipality,
                "planned_start_on": payload.planned_start_on,
                "planned_completion_on": payload.planned_completion_on,
                "note": payload.note,
                "created_by": str(user_id),
            },
        )
    ).first()
    assert row is not None
    project = _row_to_project(row)

    await record_audit_event(
        session, household_id, user_id, MAJOR_PROJECT_ENTITY_TYPE, project.id, "create",
        after=project.model_dump(mode="json"),
    )

    await create_scenario(
        session,
        household_id,
        project.id,
        user_id,
        ProjectScenarioCreate(
            name="Basis", scenario_kind="baseline", template_key=payload.template_key
        ),
    )
    return project


async def update_project(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    payload: MajorProjectUpdate,
    expected_version: int | None,
) -> MajorProject:
    before = await require_project(session, household_id, project_id)
    _assert_version(before.version, expected_version)

    changes = payload.model_dump(exclude={"clear_fields"}, exclude_unset=True)
    for field in payload.clear_fields:
        changes[field] = None
    if not changes:
        return before

    assignments = ", ".join(f"{key} = :{key}" for key in changes)
    params: dict[str, Any] = {**changes, "id": str(project_id), "household_id": str(household_id)}
    updated = await session.execute(
        text(
            f"update major_projects set {assignments} "
            "where id = :id and household_id = :household_id"
        ),
        params,
    )
    if cast(CursorResult[Any], updated).rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROJECT_NOT_FOUND)

    after = await require_project(session, household_id, project_id)
    await record_audit_event(
        session, household_id, None, MAJOR_PROJECT_ENTITY_TYPE, project_id, "update",
        before=before.model_dump(mode="json"), after=after.model_dump(mode="json"),
    )
    return after


async def transition_project(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    to_status: str,
    note: str | None,
    expected_version: int | None,
    user_id: UUID,
) -> MajorProject:
    before = await require_project(session, household_id, project_id)
    _assert_version(before.version, expected_version)

    allowed = _ALLOWED_TRANSITIONS.get(before.status, set())
    if to_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid_project_transition:{before.status}->{to_status}",
        )

    await session.execute(
        text("update major_projects set status = :status where id = :id"),
        {"status": to_status, "id": str(project_id)},
    )
    after = await require_project(session, household_id, project_id)
    await record_audit_event(
        session, household_id, user_id, MAJOR_PROJECT_ENTITY_TYPE, project_id, "update",
        before=before.model_dump(mode="json"), after=after.model_dump(mode="json"),
        metadata={"transition_note": note} if note else None,
    )
    return after


async def delete_impact(
    session: AsyncSession, household_id: UUID, project_id: UUID
) -> ProjectDeleteImpact:
    project = await require_project(session, household_id, project_id)
    scenario_count_row = (
        await session.execute(
            text(
                "select count(*) as n from project_scenarios "
                "where project_id = :project_id and household_id = :household_id"
            ),
            {"project_id": str(project_id), "household_id": str(household_id)},
        )
    ).first()
    scenario_count = int(scenario_count_row.n) if scenario_count_row else 0

    actuals_row = (
        await session.execute(
            text(
                "select count(*) as n from project_cost_actuals a "
                "join project_cost_items ci on ci.id = a.cost_item_id "
                "join project_scenarios s on s.id = ci.scenario_id "
                "where s.project_id = :project_id and a.household_id = :household_id"
            ),
            {"project_id": str(project_id), "household_id": str(household_id)},
        )
    ).first()
    has_actuals = bool(actuals_row and int(actuals_row.n) > 0)

    reasons: list[str] = []
    if has_actuals:
        reasons.append("has_actual_costs")
    if project.status not in ("idea", "planning"):
        reasons.append(f"status_not_deletable:{project.status}")

    return ProjectDeleteImpact(
        scenario_count=scenario_count,
        has_actual_costs=has_actuals,
        hard_delete_allowed=not reasons,
        reasons=reasons,
    )


async def delete_project(
    session: AsyncSession, household_id: UUID, project_id: UUID, user_id: UUID
) -> None:
    project = await require_project(session, household_id, project_id)
    impact = await delete_impact(session, household_id, project_id)
    if not impact.hard_delete_allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project_not_deletable_use_archive",
        )
    await session.execute(
        text("delete from major_projects where id = :id and household_id = :household_id"),
        {"id": str(project_id), "household_id": str(household_id)},
    )
    await record_audit_event(
        session, household_id, user_id, MAJOR_PROJECT_ENTITY_TYPE, project_id, "delete",
        before=project.model_dump(mode="json"),
    )


async def archive_project(
    session: AsyncSession, household_id: UUID, project_id: UUID, user_id: UUID
) -> MajorProject:
    before = await require_project(session, household_id, project_id)
    await session.execute(
        text("update major_projects set status = 'archived' where id = :id"),
        {"id": str(project_id)},
    )
    after = await require_project(session, household_id, project_id)
    await record_audit_event(
        session, household_id, user_id, MAJOR_PROJECT_ENTITY_TYPE, project_id, "archive",
        before=before.model_dump(mode="json"), after=after.model_dump(mode="json"),
    )
    return after


# ---------------------------------------------------------------------------
# Szenarien
# ---------------------------------------------------------------------------


async def list_scenarios(
    session: AsyncSession, household_id: UUID, project_id: UUID
) -> list[ProjectScenario]:
    await require_project(session, household_id, project_id)
    result = await session.execute(
        text(
            f"select {_SCENARIO_COLUMNS} from project_scenarios "
            "where project_id = :project_id and household_id = :household_id "
            "order by created_at asc"
        ),
        {"project_id": str(project_id), "household_id": str(household_id)},
    )
    return [_row_to_scenario(row) for row in result]


async def create_scenario(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    user_id: UUID,
    payload: ProjectScenarioCreate,
) -> ProjectScenario:
    await require_project(session, household_id, project_id)
    row = (
        await session.execute(
            text(
                "insert into project_scenarios "
                "(household_id, project_id, name, scenario_kind, as_of_date, "
                "contingency_mode, contingency_rate_bps, contingency_fixed_minor, "
                "minimum_liquidity_reserve_minor, calculation_horizon_months, "
                "calculation_engine_version) "
                "values (:household_id, :project_id, :name, :scenario_kind, "
                "coalesce(:as_of_date, current_date), :contingency_mode, :contingency_rate_bps, "
                ":contingency_fixed_minor, :minimum_liquidity_reserve_minor, "
                ":calculation_horizon_months, :engine_version) "
                f"returning {_SCENARIO_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "project_id": str(project_id),
                "name": payload.name,
                "scenario_kind": payload.scenario_kind,
                "as_of_date": payload.as_of_date,
                "contingency_mode": payload.contingency_mode,
                "contingency_rate_bps": payload.contingency_rate_bps,
                "contingency_fixed_minor": payload.contingency_fixed_minor,
                "minimum_liquidity_reserve_minor": payload.minimum_liquidity_reserve_minor,
                "calculation_horizon_months": payload.calculation_horizon_months,
                "engine_version": ENGINE_VERSION,
            },
        )
    ).first()
    assert row is not None
    scenario = _row_to_scenario(row)

    if payload.template_key:
        await _apply_template(session, household_id, scenario.id, payload.template_key)

    await record_audit_event(
        session, household_id, user_id, PROJECT_SCENARIO_ENTITY_TYPE, scenario.id, "create",
        after=scenario.model_dump(mode="json"),
    )
    return scenario


async def update_scenario(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    scenario_id: UUID,
    payload: ProjectScenarioUpdate,
    expected_version: int | None,
) -> ProjectScenario:
    before = await require_scenario(session, household_id, project_id, scenario_id)
    _assert_version(before.version, expected_version)

    changes = payload.model_dump(exclude={"clear_fields"}, exclude_unset=True)
    for field in payload.clear_fields:
        changes[field] = None
    if not changes:
        return before

    assignments = ", ".join(f"{key} = :{key}" for key in changes)
    params: dict[str, Any] = {**changes, "id": str(scenario_id)}
    await session.execute(
        text(f"update project_scenarios set {assignments} where id = :id"), params
    )
    after = await require_scenario(session, household_id, project_id, scenario_id)
    await record_audit_event(
        session, household_id, None, PROJECT_SCENARIO_ENTITY_TYPE, scenario_id, "update",
        before=before.model_dump(mode="json"), after=after.model_dump(mode="json"),
    )
    return after


async def delete_scenario(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_id: UUID, user_id: UUID
) -> None:
    scenario = await require_scenario(session, household_id, project_id, scenario_id)
    if scenario.status != "draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="scenario_not_draft")
    actuals_row = (
        await session.execute(
            text(
                "select count(*) as n from project_cost_actuals a "
                "join project_cost_items ci on ci.id = a.cost_item_id "
                "where ci.scenario_id = :scenario_id"
            ),
            {"scenario_id": str(scenario_id)},
        )
    ).first()
    if actuals_row and int(actuals_row.n) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="scenario_has_actual_costs"
        )

    await session.execute(
        text("delete from project_scenarios where id = :id"), {"id": str(scenario_id)}
    )
    await record_audit_event(
        session, household_id, user_id, PROJECT_SCENARIO_ENTITY_TYPE, scenario_id, "delete",
        before=scenario.model_dump(mode="json"),
    )


async def clone_scenario(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    scenario_id: UUID,
    user_id: UUID,
    payload: ScenarioCloneRequest,
) -> ProjectScenario:
    """Kopiert Kostenabschnitte/-positionen/Zeitplan, Finanzierungsquellen
    (inkl. Darlehen mit allen vier Kindkollektionen), Haushaltsannahmen,
    Folgekosten und Zielannahmen in ein neues Szenario (Plan 3.3). Ein
    Verzoegerungsstress verschiebt Datumsfelder um `stress_delay_months`
    Monate und die monatsindizierten Darlehensfelder um dieselbe Anzahl
    Monate."""
    source = await require_scenario(session, household_id, project_id, scenario_id)

    cost_increase_rate = Decimal(payload.stress_cost_increase_bps or 0) / Decimal(10_000)
    delay_days = (payload.stress_delay_months or 0) * 30

    new_row = (
        await session.execute(
            text(
                "insert into project_scenarios "
                "(household_id, project_id, name, scenario_kind, source_scenario_id, as_of_date, "
                "contingency_mode, contingency_rate_bps, contingency_fixed_minor, "
                "minimum_liquidity_reserve_minor, calculation_horizon_months, "
                "calculation_engine_version, stress_transform_json) "
                "values (:household_id, :project_id, :name, :scenario_kind, :source_scenario_id, "
                ":as_of_date, :contingency_mode, :contingency_rate_bps, :contingency_fixed_minor, "
                ":minimum_liquidity_reserve_minor, :calculation_horizon_months, :engine_version, "
                "cast(:stress_transform_json as jsonb)) "
                f"returning {_SCENARIO_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "project_id": str(project_id),
                "name": payload.name,
                "scenario_kind": payload.scenario_kind,
                "source_scenario_id": str(source.id),
                "as_of_date": source.as_of_date,
                "contingency_mode": source.contingency_mode,
                "contingency_rate_bps": source.contingency_rate_bps,
                "contingency_fixed_minor": source.contingency_fixed_minor,
                "minimum_liquidity_reserve_minor": source.minimum_liquidity_reserve_minor,
                "calculation_horizon_months": source.calculation_horizon_months,
                "engine_version": ENGINE_VERSION,
                "stress_transform_json": json.dumps(
                    {
                        "stress_cost_increase_bps": payload.stress_cost_increase_bps,
                        "stress_delay_months": payload.stress_delay_months,
                        "stress_followup_rate_increase_bps": (
                            payload.stress_followup_rate_increase_bps
                        ),
                        "exclude_unsecured_funding": payload.exclude_unsecured_funding,
                    }
                ),
            },
        )
    ).first()
    assert new_row is not None
    new_scenario = _row_to_scenario(new_row)

    section_id_map: dict[UUID, UUID] = {}
    sections = (
        await session.execute(
            text(
                "select id, semantic_key, name, cost_class, sort_order, planned_start_on, "
                "planned_end_on, note from project_cost_sections where scenario_id = :scenario_id"
            ),
            {"scenario_id": str(source.id)},
        )
    ).fetchall()
    for section in sections:
        new_planned_start = _shift_date(section.planned_start_on, delay_days)
        new_planned_end = _shift_date(section.planned_end_on, delay_days)
        new_section_row = (
            await session.execute(
                text(
                    "insert into project_cost_sections "
                    "(household_id, scenario_id, semantic_key, name, cost_class, sort_order, "
                    "planned_start_on, planned_end_on, note) "
                    "values (:household_id, :scenario_id, :semantic_key, :name, :cost_class, "
                    ":sort_order, :planned_start_on, :planned_end_on, :note) returning id"
                ),
                {
                    "household_id": str(household_id),
                    "scenario_id": str(new_scenario.id),
                    "semantic_key": section.semantic_key,
                    "name": section.name,
                    "cost_class": section.cost_class,
                    "sort_order": section.sort_order,
                    "planned_start_on": new_planned_start,
                    "planned_end_on": new_planned_end,
                    "note": section.note,
                },
            )
        ).first()
        assert new_section_row is not None
        section_id_map[section.id] = new_section_row.id

    items = (
        await session.execute(
            text(
                "select * from project_cost_items "
                "where scenario_id = :scenario_id order by sort_order"
            ),
            {"scenario_id": str(source.id)},
        )
    ).fetchall()
    item_id_map: dict[UUID, UUID] = {}
    for item in items:
        estimate_manual_minor = item.estimate_manual_minor
        unit_price_minor = item.unit_price_minor
        if item.contingency_eligible and item.status != "cancelled" and cost_increase_rate > 0:
            if estimate_manual_minor is not None:
                estimate_manual_minor = _apply_bump(estimate_manual_minor, cost_increase_rate)
            if unit_price_minor is not None:
                unit_price_minor = _apply_bump(unit_price_minor, cost_increase_rate)
        new_item_row = (
            await session.execute(
                text(
                    "insert into project_cost_items "
                    "(household_id, scenario_id, section_id, semantic_key, name, cost_kind, "
                    "status, calculation_mode, estimate_manual_minor, quantity, unit_label, "
                    "unit_price_minor, percentage_rate_bps, percentage_basis, "
                    "quoted_amount_minor, committed_amount_minor, contingency_eligible, "
                    "funding_tags, vendor_label, expected_start_on, expected_end_on, "
                    "sort_order, note) "
                    "values (:household_id, :scenario_id, :section_id, :semantic_key, :name, "
                    ":cost_kind, :status, :calculation_mode, :estimate_manual_minor, :quantity, "
                    ":unit_label, :unit_price_minor, :percentage_rate_bps, :percentage_basis, "
                    ":quoted_amount_minor, :committed_amount_minor, :contingency_eligible, "
                    ":funding_tags, :vendor_label, :expected_start_on, :expected_end_on, "
                    ":sort_order, :note) returning id"
                ),
                {
                    "household_id": str(household_id),
                    "scenario_id": str(new_scenario.id),
                    "section_id": str(section_id_map[item.section_id]),
                    "semantic_key": item.semantic_key,
                    "name": item.name,
                    "cost_kind": item.cost_kind,
                    "status": item.status,
                    "calculation_mode": item.calculation_mode,
                    "estimate_manual_minor": estimate_manual_minor,
                    "quantity": item.quantity,
                    "unit_label": item.unit_label,
                    "unit_price_minor": unit_price_minor,
                    "percentage_rate_bps": item.percentage_rate_bps,
                    "percentage_basis": item.percentage_basis,
                    "quoted_amount_minor": item.quoted_amount_minor,
                    "committed_amount_minor": item.committed_amount_minor,
                    "contingency_eligible": item.contingency_eligible,
                    "funding_tags": item.funding_tags,
                    "vendor_label": item.vendor_label,
                    "expected_start_on": _shift_date(item.expected_start_on, delay_days),
                    "expected_end_on": _shift_date(item.expected_end_on, delay_days),
                    "sort_order": item.sort_order,
                    "note": item.note,
                },
            )
        ).first()
        assert new_item_row is not None
        item_id_map[item.id] = new_item_row.id

    # Zweiter Durchlauf: percentage_basis_item_id auf die neuen IDs umhaengen.
    for item in items:
        if item.percentage_basis_item_id is not None:
            await session.execute(
                text(
                    "update project_cost_items set percentage_basis_item_id = :basis_id "
                    "where id = :id"
                ),
                {
                    "basis_id": str(item_id_map[item.percentage_basis_item_id]),
                    "id": str(item_id_map[item.id]),
                },
            )

    schedules = (
        await session.execute(
            text(
                "select cost_item_id, due_on, amount_minor, status, label, sort_order "
                "from project_cost_schedule_entries where cost_item_id = any(:item_ids)"
            ),
            {"item_ids": [str(i) for i in item_id_map]},
        )
    ).fetchall()
    for entry in schedules:
        await session.execute(
            text(
                "insert into project_cost_schedule_entries "
                "(household_id, cost_item_id, due_on, amount_minor, status, label, sort_order) "
                "values (:household_id, :cost_item_id, :due_on, :amount_minor, :status, :label, "
                ":sort_order)"
            ),
            {
                "household_id": str(household_id),
                "cost_item_id": str(item_id_map[entry.cost_item_id]),
                "due_on": _shift_date(entry.due_on, delay_days),
                "amount_minor": entry.amount_minor,
                "status": entry.status,
                "label": entry.label,
                "sort_order": entry.sort_order,
            },
        )

    stress_delay_months = payload.stress_delay_months or 0
    source_id_map: dict[UUID, UUID] = {}
    financing_sources = (
        await session.execute(
            text("select * from project_financing_sources where scenario_id = :scenario_id"),
            {"scenario_id": str(source.id)},
        )
    ).fetchall()
    for financing_source in financing_sources:
        new_source_row = (
            await session.execute(
                text(
                    "insert into project_financing_sources "
                    "(household_id, scenario_id, name, source_type, status, "
                    "planned_amount_minor, principal_limit_minor, available_on, account_id, "
                    "savings_goal_id, asset_id, funding_program_version_id, gross_amount_minor, "
                    "deductions_minor, sort_order, note) "
                    "values (:household_id, :scenario_id, :name, :source_type, :status, "
                    ":planned_amount_minor, :principal_limit_minor, :available_on, :account_id, "
                    ":savings_goal_id, :asset_id, :funding_program_version_id, "
                    ":gross_amount_minor, :deductions_minor, :sort_order, :note) returning id"
                ),
                {
                    "household_id": str(household_id),
                    "scenario_id": str(new_scenario.id),
                    "name": financing_source.name,
                    "source_type": financing_source.source_type,
                    "status": financing_source.status,
                    "planned_amount_minor": financing_source.planned_amount_minor,
                    "principal_limit_minor": financing_source.principal_limit_minor,
                    "available_on": _shift_date(financing_source.available_on, delay_days),
                    "account_id": financing_source.account_id,
                    "savings_goal_id": financing_source.savings_goal_id,
                    "asset_id": financing_source.asset_id,
                    "funding_program_version_id": financing_source.funding_program_version_id,
                    "gross_amount_minor": financing_source.gross_amount_minor,
                    "deductions_minor": financing_source.deductions_minor,
                    "sort_order": financing_source.sort_order,
                    "note": financing_source.note,
                },
            )
        ).first()
        assert new_source_row is not None
        source_id_map[financing_source.id] = new_source_row.id

        debt_plan_row = (
            await session.execute(
                text("select * from debt_plans where project_financing_source_id = :sid"),
                {"sid": str(financing_source.id)},
            )
        ).first()
        if debt_plan_row is None:
            continue
        new_plan_row = (
            await session.execute(
                text(
                    "insert into debt_plans "
                    "(household_id, project_financing_source_id, name, creditor_label, "
                    "currency, status, principal_limit_minor, opening_balance_minor, "
                    "starts_on, payment_interval_months, amortization_mode, "
                    "fixed_payment_minor, initial_repayment_rate_bps, target_term_months, "
                    "repayment_free_months, payment_free_months, "
                    "capitalize_interest_during_payment_free, commitment_free_months, "
                    "commitment_rate_bps_per_month) "
                    "values (:household_id, :project_financing_source_id, :name, "
                    ":creditor_label, :currency, 'draft', :principal_limit_minor, "
                    ":opening_balance_minor, :starts_on, :payment_interval_months, "
                    ":amortization_mode, :fixed_payment_minor, :initial_repayment_rate_bps, "
                    ":target_term_months, :repayment_free_months, :payment_free_months, "
                    ":capitalize_interest_during_payment_free, :commitment_free_months, "
                    ":commitment_rate_bps_per_month) returning id"
                ),
                {
                    "household_id": str(household_id),
                    "project_financing_source_id": str(new_source_row.id),
                    "name": debt_plan_row.name,
                    "creditor_label": debt_plan_row.creditor_label,
                    "currency": debt_plan_row.currency,
                    "principal_limit_minor": debt_plan_row.principal_limit_minor,
                    "opening_balance_minor": debt_plan_row.opening_balance_minor,
                    "starts_on": _shift_date(debt_plan_row.starts_on, delay_days),
                    "payment_interval_months": debt_plan_row.payment_interval_months,
                    "amortization_mode": debt_plan_row.amortization_mode,
                    "fixed_payment_minor": debt_plan_row.fixed_payment_minor,
                    "initial_repayment_rate_bps": debt_plan_row.initial_repayment_rate_bps,
                    "target_term_months": debt_plan_row.target_term_months,
                    "repayment_free_months": debt_plan_row.repayment_free_months,
                    "payment_free_months": debt_plan_row.payment_free_months,
                    "capitalize_interest_during_payment_free": (
                        debt_plan_row.capitalize_interest_during_payment_free
                    ),
                    "commitment_free_months": debt_plan_row.commitment_free_months,
                    "commitment_rate_bps_per_month": debt_plan_row.commitment_rate_bps_per_month,
                },
            )
        ).first()
        assert new_plan_row is not None

        rate_periods = (
            await session.execute(
                text("select * from debt_rate_periods where debt_plan_id = :plan_id"),
                {"plan_id": str(debt_plan_row.id)},
            )
        ).fetchall()
        for period in rate_periods:
            await session.execute(
                text(
                    "insert into debt_rate_periods "
                    "(household_id, debt_plan_id, starts_after_month, ends_after_month, "
                    "nominal_rate_bps_annual, effective_rate_bps_annual, rate_kind, label) "
                    "values (:household_id, :debt_plan_id, :starts_after_month, "
                    ":ends_after_month, :nominal_rate_bps_annual, :effective_rate_bps_annual, "
                    ":rate_kind, :label)"
                ),
                {
                    "household_id": str(household_id),
                    "debt_plan_id": str(new_plan_row.id),
                    "starts_after_month": period.starts_after_month + stress_delay_months,
                    "ends_after_month": (
                        period.ends_after_month + stress_delay_months
                        if period.ends_after_month is not None
                        else None
                    ),
                    "nominal_rate_bps_annual": period.nominal_rate_bps_annual,
                    "effective_rate_bps_annual": period.effective_rate_bps_annual,
                    "rate_kind": period.rate_kind,
                    "label": period.label,
                },
            )

        draws = (
            await session.execute(
                text("select * from debt_draws where debt_plan_id = :plan_id"),
                {"plan_id": str(debt_plan_row.id)},
            )
        ).fetchall()
        for draw in draws:
            await session.execute(
                text(
                    "insert into debt_draws "
                    "(household_id, debt_plan_id, occurs_after_month, amount_minor, status) "
                    "values (:household_id, :debt_plan_id, :occurs_after_month, "
                    ":amount_minor, :status)"
                ),
                {
                    "household_id": str(household_id),
                    "debt_plan_id": str(new_plan_row.id),
                    "occurs_after_month": draw.occurs_after_month + stress_delay_months,
                    "amount_minor": draw.amount_minor,
                    "status": draw.status,
                },
            )

        fees = (
            await session.execute(
                text("select * from debt_fees where debt_plan_id = :plan_id"),
                {"plan_id": str(debt_plan_row.id)},
            )
        ).fetchall()
        for fee in fees:
            await session.execute(
                text(
                    "insert into debt_fees "
                    "(household_id, debt_plan_id, occurs_after_month, amount_minor, "
                    "fee_kind, financed, label) "
                    "values (:household_id, :debt_plan_id, :occurs_after_month, "
                    ":amount_minor, :fee_kind, :financed, :label)"
                ),
                {
                    "household_id": str(household_id),
                    "debt_plan_id": str(new_plan_row.id),
                    "occurs_after_month": fee.occurs_after_month + stress_delay_months,
                    "amount_minor": fee.amount_minor,
                    "fee_kind": fee.fee_kind,
                    "financed": fee.financed,
                    "label": fee.label,
                },
            )

        special_repayments = (
            await session.execute(
                text("select * from debt_special_repayments where debt_plan_id = :plan_id"),
                {"plan_id": str(debt_plan_row.id)},
            )
        ).fetchall()
        for repayment in special_repayments:
            await session.execute(
                text(
                    "insert into debt_special_repayments "
                    "(household_id, debt_plan_id, occurs_after_month, amount_minor, "
                    "status, label) "
                    "values (:household_id, :debt_plan_id, :occurs_after_month, "
                    ":amount_minor, :status, :label)"
                ),
                {
                    "household_id": str(household_id),
                    "debt_plan_id": str(new_plan_row.id),
                    "occurs_after_month": repayment.occurs_after_month + stress_delay_months,
                    "amount_minor": repayment.amount_minor,
                    "status": repayment.status,
                    "label": repayment.label,
                },
            )

    assumptions_row = (
        await session.execute(
            text(
                "select starting_liquidity_minor, monthly_net_income_minor, "
                "monthly_living_costs_minor, monthly_current_housing_minor, "
                "monthly_existing_debt_service_minor, monthly_other_commitments_minor, "
                "analysis_lookback_months, source "
                "from project_cashflow_assumptions where scenario_id = :scenario_id"
            ),
            {"scenario_id": str(source.id)},
        )
    ).first()
    if assumptions_row is not None:
        await session.execute(
            text(
                "insert into project_cashflow_assumptions "
                "(scenario_id, household_id, starting_liquidity_minor, "
                "monthly_net_income_minor, monthly_living_costs_minor, "
                "monthly_current_housing_minor, monthly_existing_debt_service_minor, "
                "monthly_other_commitments_minor, analysis_lookback_months, source) "
                "values (:scenario_id, :household_id, :starting_liquidity_minor, "
                ":monthly_net_income_minor, :monthly_living_costs_minor, "
                ":monthly_current_housing_minor, :monthly_existing_debt_service_minor, "
                ":monthly_other_commitments_minor, :analysis_lookback_months, :source)"
            ),
            {
                "scenario_id": str(new_scenario.id),
                "household_id": str(household_id),
                "starting_liquidity_minor": assumptions_row.starting_liquidity_minor,
                "monthly_net_income_minor": assumptions_row.monthly_net_income_minor,
                "monthly_living_costs_minor": assumptions_row.monthly_living_costs_minor,
                "monthly_current_housing_minor": assumptions_row.monthly_current_housing_minor,
                "monthly_existing_debt_service_minor": (
                    assumptions_row.monthly_existing_debt_service_minor
                ),
                "monthly_other_commitments_minor": (
                    assumptions_row.monthly_other_commitments_minor
                ),
                "analysis_lookback_months": assumptions_row.analysis_lookback_months,
                "source": assumptions_row.source,
            },
        )

    recurring_impacts = (
        await session.execute(
            text(
                "select name, impact_type, amount_minor, frequency, starts_on, ends_on, "
                "confidence, include_in_affordability, note "
                "from project_recurring_impacts where scenario_id = :scenario_id"
            ),
            {"scenario_id": str(source.id)},
        )
    ).fetchall()
    for impact in recurring_impacts:
        await session.execute(
            text(
                "insert into project_recurring_impacts "
                "(household_id, scenario_id, name, impact_type, amount_minor, frequency, "
                "starts_on, ends_on, confidence, include_in_affordability, note) "
                "values (:household_id, :scenario_id, :name, :impact_type, :amount_minor, "
                ":frequency, :starts_on, :ends_on, :confidence, :include_in_affordability, "
                ":note)"
            ),
            {
                "household_id": str(household_id),
                "scenario_id": str(new_scenario.id),
                "name": impact.name,
                "impact_type": impact.impact_type,
                "amount_minor": impact.amount_minor,
                "frequency": impact.frequency,
                "starts_on": _shift_date(impact.starts_on, delay_days),
                "ends_on": _shift_date(impact.ends_on, delay_days),
                "confidence": impact.confidence,
                "include_in_affordability": impact.include_in_affordability,
                "note": impact.note,
            },
        )

    goal_assumptions = (
        await session.execute(
            text(
                "select savings_goal_id, withdrawal_minor, withdrawal_on, "
                "protected_minimum_minor, baseline_monthly_contribution_minor, "
                "project_monthly_contribution_minor "
                "from project_goal_assumptions where scenario_id = :scenario_id"
            ),
            {"scenario_id": str(source.id)},
        )
    ).fetchall()
    for goal_assumption in goal_assumptions:
        await session.execute(
            text(
                "insert into project_goal_assumptions "
                "(household_id, scenario_id, savings_goal_id, withdrawal_minor, "
                "withdrawal_on, protected_minimum_minor, "
                "baseline_monthly_contribution_minor, project_monthly_contribution_minor) "
                "values (:household_id, :scenario_id, :savings_goal_id, :withdrawal_minor, "
                ":withdrawal_on, :protected_minimum_minor, "
                ":baseline_monthly_contribution_minor, :project_monthly_contribution_minor)"
            ),
            {
                "household_id": str(household_id),
                "scenario_id": str(new_scenario.id),
                "savings_goal_id": goal_assumption.savings_goal_id,
                "withdrawal_minor": goal_assumption.withdrawal_minor,
                "withdrawal_on": _shift_date(goal_assumption.withdrawal_on, delay_days),
                "protected_minimum_minor": goal_assumption.protected_minimum_minor,
                "baseline_monthly_contribution_minor": (
                    goal_assumption.baseline_monthly_contribution_minor
                ),
                "project_monthly_contribution_minor": (
                    goal_assumption.project_monthly_contribution_minor
                ),
            },
        )

    await record_audit_event(
        session, household_id, user_id, PROJECT_SCENARIO_ENTITY_TYPE, new_scenario.id, "create",
        after=new_scenario.model_dump(mode="json"), metadata={"cloned_from": str(source.id)},
    )
    return new_scenario


def _shift_date(value: Any, days: int) -> Any:
    if value is None or days == 0:
        return value
    return value + timedelta(days=days)


def _apply_bump(amount_minor: int, rate: Decimal) -> int:
    bumped = Decimal(amount_minor) * (Decimal(1) + rate)
    return int(bumped.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def select_scenario(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_id: UUID, user_id: UUID
) -> ProjectScenario:
    scenario = await require_scenario(session, household_id, project_id, scenario_id)
    calculation = await calculate_scenario(session, household_id, project_id, scenario_id)
    if "cost_schedule_incomplete" in calculation.warnings:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cost_schedule_incomplete"
        )

    await session.execute(
        text(
            "update project_scenarios set status = 'superseded' "
            "where project_id = :project_id and household_id = :household_id "
            "and status = 'selected'"
        ),
        {"project_id": str(project_id), "household_id": str(household_id)},
    )
    await session.execute(
        text(
            "update project_scenarios set status = 'selected', selected_at = now(), "
            "selected_by = :user_id where id = :id"
        ),
        {"user_id": str(user_id), "id": str(scenario_id)},
    )

    await session.execute(
        text(
            "insert into project_calculation_snapshots "
            "(household_id, scenario_id, reason, scenario_version, input_fingerprint, "
            "engine_version, summary_json, created_by) "
            "values (:household_id, :scenario_id, 'selected', :scenario_version, "
            ":input_fingerprint, :engine_version, cast(:summary_json as jsonb), :created_by)"
        ),
        {
            "household_id": str(household_id),
            "scenario_id": str(scenario_id),
            "scenario_version": scenario.version,
            "input_fingerprint": calculation.input_fingerprint,
            "engine_version": ENGINE_VERSION,
            "summary_json": calculation.model_dump_json(),
            "created_by": str(user_id),
        },
    )

    after = await require_scenario(session, household_id, project_id, scenario_id)
    await record_audit_event(
        session, household_id, user_id, PROJECT_SCENARIO_ENTITY_TYPE, scenario_id, "update",
        before=scenario.model_dump(mode="json"), after=after.model_dump(mode="json"),
        metadata={"action": "select"},
    )
    return after


async def compare_scenarios(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_ids: list[UUID]
) -> ScenarioComparison:
    if not (2 <= len(scenario_ids) <= 4):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="comparison_needs_2_to_4_scenarios",
        )
    rows: list[ScenarioComparisonRow] = []
    for scenario_id in scenario_ids:
        scenario = await require_scenario(session, household_id, project_id, scenario_id)
        calc = await calculate_scenario(session, household_id, project_id, scenario_id)
        rows.append(
            ScenarioComparisonRow(
                scenario_id=scenario.id,
                name=scenario.name,
                scenario_kind=scenario.scenario_kind,
                status=scenario.status,
                estimated_cost_minor=calc.estimated_cost_minor,
                forecast_cash_cost_minor=calc.forecast_cash_cost_minor,
                contingency_minor=calc.contingency_minor,
                project_cash_cost_minor=calc.project_cash_cost_minor,
                project_economic_cost_minor=calc.project_economic_cost_minor,
                warnings=calc.warnings,
            )
        )
    return ScenarioComparison(rows=rows)


def _assert_version(current_version: int, expected_version: int | None) -> None:
    from app.core.concurrency import raise_version_conflict

    if expected_version is not None and current_version != expected_version:
        raise_version_conflict(current_version)


# ---------------------------------------------------------------------------
# Kostenabschnitte und -positionen
# ---------------------------------------------------------------------------


async def require_cost_section(
    session: AsyncSession, household_id: UUID, section_id: UUID
) -> CostSection:
    row = (
        await session.execute(
            text(
                "select id, scenario_id, semantic_key, name, cost_class, sort_order, "
                "planned_start_on, planned_end_on, note, version, created_at, updated_at "
                "from project_cost_sections where id = :id and household_id = :household_id"
            ),
            {"id": str(section_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=COST_SECTION_NOT_FOUND)
    return CostSection(**row._mapping)


async def list_cost_sections(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> list[CostSection]:
    result = await session.execute(
        text(
            "select id, scenario_id, semantic_key, name, cost_class, sort_order, "
            "planned_start_on, planned_end_on, note, version, created_at, updated_at "
            "from project_cost_sections "
            "where scenario_id = :scenario_id and household_id = :household_id "
            "order by sort_order"
        ),
        {"scenario_id": str(scenario_id), "household_id": str(household_id)},
    )
    return [CostSection(**row._mapping) for row in result]


async def create_cost_section(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, payload: CostSectionCreate
) -> CostSection:
    row = (
        await session.execute(
            text(
                "insert into project_cost_sections "
                "(household_id, scenario_id, semantic_key, name, cost_class, sort_order, "
                "planned_start_on, planned_end_on, note) "
                "values (:household_id, :scenario_id, :semantic_key, :name, :cost_class, "
                ":sort_order, :planned_start_on, :planned_end_on, :note) "
                "returning id, scenario_id, semantic_key, name, cost_class, sort_order, "
                "planned_start_on, planned_end_on, note, version, created_at, updated_at"
            ),
            {
                "household_id": str(household_id),
                "scenario_id": str(scenario_id),
                **payload.model_dump(),
            },
        )
    ).first()
    assert row is not None
    return CostSection(**row._mapping)


async def update_cost_section(
    session: AsyncSession,
    household_id: UUID,
    section_id: UUID,
    payload: CostSectionUpdate,
    expected_version: int | None,
) -> CostSection:
    before = await require_cost_section(session, household_id, section_id)
    _assert_version(before.version, expected_version)
    changes = payload.model_dump(exclude={"clear_fields"}, exclude_unset=True)
    for field in payload.clear_fields:
        changes[field] = None
    if not changes:
        return before
    assignments = ", ".join(f"{key} = :{key}" for key in changes)
    await session.execute(
        text(f"update project_cost_sections set {assignments} where id = :id"),
        {**changes, "id": str(section_id)},
    )
    return await require_cost_section(session, household_id, section_id)


async def delete_cost_section(session: AsyncSession, household_id: UUID, section_id: UUID) -> None:
    await require_cost_section(session, household_id, section_id)
    await session.execute(
        text("delete from project_cost_sections where id = :id and household_id = :household_id"),
        {"id": str(section_id), "household_id": str(household_id)},
    )


async def reorder_cost_sections(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, ordered_section_ids: list[UUID]
) -> list[CostSection]:
    existing = await list_cost_sections(session, household_id, scenario_id)
    if {s.id for s in existing} != set(ordered_section_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reorder_must_include_all_sections",
        )
    for index, section_id in enumerate(ordered_section_ids):
        await session.execute(
            text("update project_cost_sections set sort_order = :sort_order where id = :id"),
            {"sort_order": index, "id": str(section_id)},
        )
    return await list_cost_sections(session, household_id, scenario_id)


_COST_ITEM_COLUMNS = (
    "id, scenario_id, section_id, semantic_key, name, cost_kind, status, calculation_mode, "
    "estimate_manual_minor, quantity, unit_label, unit_price_minor, percentage_rate_bps, "
    "percentage_basis, percentage_basis_item_id, quoted_amount_minor, committed_amount_minor, "
    "contingency_eligible, funding_tags, vendor_label, expected_start_on, expected_end_on, "
    "sort_order, note, estimate_source, ai_operation_id, ai_confidence, ai_rationale, "
    "version, created_at, updated_at"
)


async def _actuals_by_item(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> dict[Any, int]:
    """Ist-Summe je Kostenposition, ohne archivierte Buchungen (Plan 4.6/
    P4-Abnahme: "Archivierung entfernt sie aus Ist ohne Linkverlust" - der
    Zuordnungssatz bleibt in project_cost_actuals stehen, zaehlt aber nicht
    mehr mit). Gemeinsam genutzt von _cost_item_inputs (Rechenkern) und
    list_cost_items (Anzeige je Position), damit die Archiv-Regel nur an
    einer Stelle steht."""
    actuals = (
        await session.execute(
            text(
                "select pca.cost_item_id, coalesce(sum(pca.amount_minor), 0) as total "
                "from project_cost_actuals pca "
                "left join transactions t on t.id = pca.transaction_id "
                "where pca.household_id = :household_id and pca.cost_item_id in ("
                "select id from project_cost_items where scenario_id = :scenario_id) "
                "and (pca.transaction_id is null or coalesce(t.is_archived, false) = false) "
                "group by pca.cost_item_id"
            ),
            {"scenario_id": str(scenario_id), "household_id": str(household_id)},
        )
    ).fetchall()
    return {row.cost_item_id: int(row.total) for row in actuals}


async def _cost_item_inputs(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> tuple[CostItemInput, ...]:
    rows = (
        await session.execute(
            text(
                f"select {_COST_ITEM_COLUMNS} from project_cost_items "
                "where scenario_id = :scenario_id and household_id = :household_id"
            ),
            {"scenario_id": str(scenario_id), "household_id": str(household_id)},
        )
    ).fetchall()
    actuals_by_item = await _actuals_by_item(session, household_id, scenario_id)
    return tuple(
        CostItemInput(
            id=str(row.id),
            section_id=str(row.section_id),
            cost_kind=row.cost_kind,
            status=row.status,
            calculation_mode=row.calculation_mode,
            estimate_manual_minor=row.estimate_manual_minor,
            quantity=row.quantity,
            unit_price_minor=row.unit_price_minor,
            percentage_rate_bps=row.percentage_rate_bps,
            percentage_basis=row.percentage_basis,
            percentage_basis_item_id=(
                str(row.percentage_basis_item_id) if row.percentage_basis_item_id else None
            ),
            quoted_amount_minor=row.quoted_amount_minor,
            committed_amount_minor=row.committed_amount_minor,
            contingency_eligible=row.contingency_eligible,
            actual_amount_minor=actuals_by_item.get(row.id, 0),
        )
        for row in rows
    )


async def _assert_no_cost_formula_cycle(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> None:
    inputs = await _cost_item_inputs(session, household_id, scenario_id)
    try:
        calculate_project_costs(inputs)
    except CostFormulaCycleError as exc:
        raise HTTPException(status_code=422, detail="cost_formula_cycle") from exc


async def list_cost_items(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> list[CostItem]:
    rows = (
        await session.execute(
            text(
                f"select {_COST_ITEM_COLUMNS} from project_cost_items "
                "where scenario_id = :scenario_id and household_id = :household_id "
                "order by sort_order"
            ),
            {"scenario_id": str(scenario_id), "household_id": str(household_id)},
        )
    ).fetchall()
    result = calculate_project_costs(await _cost_item_inputs(session, household_id, scenario_id))
    actuals_by_item = await _actuals_by_item(session, household_id, scenario_id)
    items: list[CostItem] = []
    for row in rows:
        data = dict(row._mapping)
        item_id = str(row.id)
        data["estimate_minor"] = result.per_item_estimate_minor.get(item_id, 0)
        data["forecast_minor"] = result.per_item_forecast_minor.get(item_id, 0)
        data["actual_amount_minor"] = actuals_by_item.get(row.id, 0)
        items.append(CostItem(**data))
    return items


async def require_cost_item(session: AsyncSession, household_id: UUID, item_id: UUID) -> Any:
    row = (
        await session.execute(
            text(
                f"select {_COST_ITEM_COLUMNS} from project_cost_items "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(item_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=COST_ITEM_NOT_FOUND)
    return row


async def create_cost_item(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    user_id: UUID,
    payload: CostItemCreate,
) -> CostItem:
    await require_cost_section(session, household_id, payload.section_id)
    row = (
        await session.execute(
            text(
                "insert into project_cost_items "
                "(household_id, scenario_id, section_id, semantic_key, name, cost_kind, status, "
                "calculation_mode, estimate_manual_minor, quantity, unit_label, unit_price_minor, "
                "percentage_rate_bps, percentage_basis, percentage_basis_item_id, "
                "quoted_amount_minor, committed_amount_minor, contingency_eligible, funding_tags, "
                "vendor_label, expected_start_on, expected_end_on, sort_order, note) "
                "values (:household_id, :scenario_id, :section_id, :semantic_key, :name, "
                ":cost_kind, :status, :calculation_mode, :estimate_manual_minor, :quantity, "
                ":unit_label, :unit_price_minor, :percentage_rate_bps, :percentage_basis, "
                ":percentage_basis_item_id, :quoted_amount_minor, :committed_amount_minor, "
                ":contingency_eligible, :funding_tags, :vendor_label, :expected_start_on, "
                ":expected_end_on, :sort_order, :note) "
                f"returning {_COST_ITEM_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "scenario_id": str(scenario_id),
                **{
                    k: (str(v) if k == "percentage_basis_item_id" and v else v)
                    for k, v in payload.model_dump().items()
                },
            },
        )
    ).first()
    assert row is not None
    await _assert_no_cost_formula_cycle(session, household_id, scenario_id)

    await record_audit_event(
        session, household_id, user_id, PROJECT_COST_ITEM_ENTITY_TYPE, row.id, "create",
        after={k: str(v) if isinstance(v, UUID) else v for k, v in row._mapping.items()},
    )
    items = await list_cost_items(session, household_id, scenario_id)
    return next(i for i in items if i.id == row.id)


async def update_cost_item(
    session: AsyncSession,
    household_id: UUID,
    item_id: UUID,
    payload: CostItemUpdate,
    expected_version: int | None,
) -> CostItem:
    before = await require_cost_item(session, household_id, item_id)
    _assert_version(before.version, expected_version)

    changes = payload.model_dump(exclude={"clear_fields"}, exclude_unset=True)
    for field in payload.clear_fields:
        changes[field] = None
    # Eine manuelle Bearbeitung des Schaetzwerts beendet die KI-Provenienz -
    # ohne diesen Rueckfall wuerde eine laengst ueberschriebene Zahl weiter
    # als "KI-Schaetzung" angezeigt (Plan 13.8 "kein unbelegter KI-Text").
    if "estimate_manual_minor" in changes and before.estimate_source == "ai":
        changes["estimate_source"] = "manual"
        changes["ai_operation_id"] = None
        changes["ai_confidence"] = None
        changes["ai_rationale"] = None
    if changes:
        assignments = ", ".join(f"{key} = :{key}" for key in changes)
        params = {
            k: (str(v) if isinstance(v, UUID) else v) for k, v in changes.items()
        }
        await session.execute(
            text(f"update project_cost_items set {assignments} where id = :id"),
            {**params, "id": str(item_id)},
        )
    await _assert_no_cost_formula_cycle(session, household_id, before.scenario_id)

    after = await require_cost_item(session, household_id, item_id)
    await record_audit_event(
        session, household_id, None, PROJECT_COST_ITEM_ENTITY_TYPE, item_id, "update",
        before={k: str(v) if isinstance(v, UUID) else v for k, v in before._mapping.items()},
        after={k: str(v) if isinstance(v, UUID) else v for k, v in after._mapping.items()},
    )
    items = await list_cost_items(session, household_id, before.scenario_id)
    return next(i for i in items if i.id == item_id)


async def delete_cost_item(
    session: AsyncSession, household_id: UUID, item_id: UUID, user_id: UUID
) -> None:
    before = await require_cost_item(session, household_id, item_id)
    await session.execute(
        text("delete from project_cost_items where id = :id and household_id = :household_id"),
        {"id": str(item_id), "household_id": str(household_id)},
    )
    await record_audit_event(
        session, household_id, user_id, PROJECT_COST_ITEM_ENTITY_TYPE, item_id, "delete",
        before={k: str(v) if isinstance(v, UUID) else v for k, v in before._mapping.items()},
    )


async def replace_cost_schedule(
    session: AsyncSession, household_id: UUID, item_id: UUID, entries: list[CostScheduleEntryInput]
) -> list[CostScheduleEntry]:
    await require_cost_item(session, household_id, item_id)
    await session.execute(
        text("delete from project_cost_schedule_entries where cost_item_id = :item_id"),
        {"item_id": str(item_id)},
    )
    for index, entry in enumerate(entries):
        await session.execute(
            text(
                "insert into project_cost_schedule_entries "
                "(household_id, cost_item_id, due_on, amount_minor, label, sort_order) "
                "values (:household_id, :cost_item_id, :due_on, :amount_minor, :label, :sort_order)"
            ),
            {
                "household_id": str(household_id),
                "cost_item_id": str(item_id),
                "due_on": entry.due_on,
                "amount_minor": entry.amount_minor,
                "label": entry.label,
                "sort_order": entry.sort_order if entry.sort_order else index,
            },
        )
    result = await session.execute(
        text(
            "select id, due_on, amount_minor, status, label, sort_order "
            "from project_cost_schedule_entries where cost_item_id = :item_id order by sort_order"
        ),
        {"item_id": str(item_id)},
    )
    return [CostScheduleEntry(**row._mapping) for row in result]


# ---------------------------------------------------------------------------
# Ist-Kosten-Zuordnung (Plan 4.6/7.2/P4)
#
# Der Ueberbuchungsschutz (nie mehr als den Buchungsbetrag alloziert) lebt
# bewusst als DB-Trigger (check_project_cost_actuals_no_overbooking,
# supabase/migrations/0001_schema.sql), nicht nur hier: eine reine
# Anwendungspruefung haette bei zwei gleichzeitigen Requests auf dieselbe
# Buchung eine Race Condition (beide lesen dieselbe "alte" Summe). Diese
# Funktionen fangen die Trigger-Fehlermeldung ab und uebersetzen sie in einen
# fachlichen 422 statt eines 500.
# ---------------------------------------------------------------------------

TRANSACTION_NOT_FOUND: Final = "transaction_not_found"

_ACTUAL_COLUMNS: Final = (
    "pca.id, pca.cost_item_id, pca.source, pca.transaction_id, pca.amount_minor, "
    "pca.occurred_on, pca.note, pca.created_by, pca.created_at, pca.updated_at, "
    "coalesce(t.is_archived, false) as is_archived_link"
)
_ACTUAL_FROM: Final = (
    "from project_cost_actuals pca left join transactions t on t.id = pca.transaction_id"
)


async def require_actual(session: AsyncSession, household_id: UUID, actual_id: UUID) -> CostActual:
    row = (
        await session.execute(
            text(
                f"select {_ACTUAL_COLUMNS} {_ACTUAL_FROM} "
                "where pca.id = :id and pca.household_id = :household_id"
            ),
            {"id": str(actual_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=COST_ACTUAL_NOT_FOUND)
    return CostActual(**row._mapping)


async def list_actuals_for_item(
    session: AsyncSession, household_id: UUID, item_id: UUID
) -> list[CostActual]:
    await require_cost_item(session, household_id, item_id)
    rows = (
        await session.execute(
            text(
                f"select {_ACTUAL_COLUMNS} {_ACTUAL_FROM} "
                "where pca.household_id = :household_id and pca.cost_item_id = :item_id "
                "order by coalesce(pca.occurred_on, pca.created_at::date) desc, pca.created_at desc"
            ),
            {"household_id": str(household_id), "item_id": str(item_id)},
        )
    ).fetchall()
    return [CostActual(**row._mapping) for row in rows]


async def _require_household_transaction(
    session: AsyncSession, household_id: UUID, transaction_id: UUID
) -> None:
    row = (
        await session.execute(
            text("select id from transactions where id = :id and household_id = :household_id"),
            {"id": str(transaction_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TRANSACTION_NOT_FOUND)


async def create_actual(
    session: AsyncSession,
    household_id: UUID,
    item_id: UUID,
    user_id: UUID,
    payload: CostActualCreate,
) -> CostActual:
    await require_cost_item(session, household_id, item_id)
    if payload.transaction_id is not None:
        await _require_household_transaction(session, household_id, payload.transaction_id)

    try:
        row = (
            await session.execute(
                text(
                    "insert into project_cost_actuals "
                    "(household_id, cost_item_id, source, transaction_id, amount_minor, "
                    "occurred_on, note, created_by) "
                    "values (:household_id, :cost_item_id, :source, :transaction_id, "
                    ":amount_minor, :occurred_on, :note, :created_by) "
                    "returning id"
                ),
                {
                    "household_id": str(household_id),
                    "cost_item_id": str(item_id),
                    "source": payload.source,
                    "transaction_id": (
                        str(payload.transaction_id) if payload.transaction_id else None
                    ),
                    "amount_minor": payload.amount_minor,
                    "occurred_on": payload.occurred_on,
                    "note": payload.note,
                    "created_by": str(user_id),
                },
            )
        ).first()
    except DBAPIError as exc:
        if _is_actuals_overbooking_error(exc):
            raise HTTPException(status_code=422, detail=ACTUALS_OVERBOOKING) from exc
        raise
    assert row is not None

    actual = await require_actual(session, household_id, row.id)
    await record_audit_event(
        session, household_id, user_id, PROJECT_COST_ACTUAL_ENTITY_TYPE, actual.id, "create",
        after=actual.model_dump(mode="json"),
    )
    return actual


async def delete_actual(
    session: AsyncSession, household_id: UUID, actual_id: UUID, user_id: UUID
) -> None:
    before = await require_actual(session, household_id, actual_id)
    await session.execute(
        text("delete from project_cost_actuals where id = :id and household_id = :household_id"),
        {"id": str(actual_id), "household_id": str(household_id)},
    )
    await record_audit_event(
        session, household_id, user_id, PROJECT_COST_ACTUAL_ENTITY_TYPE, actual_id, "delete",
        before=before.model_dump(mode="json"),
    )


async def list_transaction_candidates(
    session: AsyncSession,
    household_id: UUID,
    *,
    search: str | None = None,
    account_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
) -> list[TransactionCandidate]:
    """Buchungen mit noch verfuegbarem Zuordnungsbetrag (Plan 7.2).

    "Verfuegbar" heisst: abs(Buchungsbetrag) minus die Summe aller bereits
    existierenden Ist-Zuordnungen zu dieser Buchung ist noch groesser null.
    Eine Buchung kann so auf mehrere Positionen verteilt werden (Plan-
    Abnahme), taucht aber nicht mehr auf, sobald sie voll alloziert ist.
    Archivierte Buchungen sind keine Kandidaten fuer NEUE Zuordnungen."""
    conditions = ["t.household_id = :household_id", "t.is_archived = false"]
    params: dict[str, Any] = {"household_id": str(household_id), "limit": limit}
    if account_id is not None:
        conditions.append("t.account_id = :account_id")
        params["account_id"] = str(account_id)
    if date_from is not None:
        conditions.append("t.booked_date >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        conditions.append("t.booked_date <= :date_to")
        params["date_to"] = date_to
    if search:
        conditions.append(
            "(t.payee_label ilike :search or t.note ilike :search)"
        )
        params["search"] = f"%{search}%"

    rows = (
        await session.execute(
            text(
                "select t.id as transaction_id, t.account_id, t.booked_date, t.amount_minor, "
                "t.payee_label, t.note, coalesce(pca.allocated_minor, 0) as allocated_minor "
                "from transactions t "
                "left join ("
                "  select transaction_id, sum(amount_minor) as allocated_minor "
                "  from project_cost_actuals "
                "  where household_id = :household_id and transaction_id is not null "
                "  group by transaction_id"
                ") pca on pca.transaction_id = t.id "
                f"where {' and '.join(conditions)} "
                "and coalesce(pca.allocated_minor, 0) < abs(t.amount_minor) "
                "order by t.booked_date desc "
                "limit :limit"
            ),
            params,
        )
    ).fetchall()
    return [
        TransactionCandidate(
            transaction_id=row.transaction_id,
            account_id=row.account_id,
            booked_date=row.booked_date,
            amount_minor=row.amount_minor,
            payee_label=row.payee_label,
            note=row.note,
            allocated_minor=int(row.allocated_minor),
            remaining_minor=abs(row.amount_minor) - int(row.allocated_minor),
        )
        for row in rows
    ]


async def bulk_assign_actuals(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    user_id: UUID,
    payload: BulkAssignActualsRequest,
) -> BulkAssignActualsResult:
    """Mehrfachallokation in einer einzigen DB-Transaktion (Plan 7.2: "max.
    500, atomare Betragspruefung") - verletzt eine Zeile den Ueberbuchungs-
    schutz, scheitert die gesamte Anfrage und keine der 500 Zeilen wird
    uebernommen, statt einen teilweise angewendeten Batch zu hinterlassen."""
    await require_project(session, household_id, project_id)

    item_ids = {item.cost_item_id for item in payload.items}
    for item_id in item_ids:
        await require_cost_item(session, household_id, item_id)
    for txn_id in {item.transaction_id for item in payload.items}:
        await _require_household_transaction(session, household_id, txn_id)

    # Nach transaction_id sortiert verarbeiten: der Ueberbuchungs-Trigger
    # sperrt je Zeile die referenzierte Buchung per `for update`. Ohne feste
    # Reihenfolge koennten zwei gleichzeitige Bulk-Anfragen, die dieselben
    # zwei Buchungen in umgekehrter Reihenfolge referenzieren, sich
    # gegenseitig blockieren (klassisches Lock-Order-Deadlock) - Postgres
    # bricht das dann mit SQLSTATE 40P01 ab, was ohne die Behandlung unten
    # als 500 durchschlagen wuerde. Dieselbe Reihenfolge auf beiden Seiten
    # verhindert den Deadlock von vornherein.
    sorted_items = sorted(payload.items, key=lambda item: str(item.transaction_id))

    created: list[CostActual] = []
    try:
        for item in sorted_items:
            row = (
                await session.execute(
                    text(
                        "insert into project_cost_actuals "
                        "(household_id, cost_item_id, source, transaction_id, amount_minor, "
                        "note, created_by) "
                        "values (:household_id, :cost_item_id, 'transaction', :transaction_id, "
                        ":amount_minor, :note, :created_by) "
                        "returning id"
                    ),
                    {
                        "household_id": str(household_id),
                        "cost_item_id": str(item.cost_item_id),
                        "transaction_id": str(item.transaction_id),
                        "amount_minor": item.amount_minor,
                        "note": item.note,
                        "created_by": str(user_id),
                    },
                )
            ).first()
            assert row is not None
            created.append(await require_actual(session, household_id, row.id))
    except DBAPIError as exc:
        if _is_actuals_overbooking_error(exc):
            raise HTTPException(status_code=422, detail=ACTUALS_OVERBOOKING) from exc
        if extract_sqlstate(exc) == _DEADLOCK_SQLSTATE:
            raise HTTPException(status_code=409, detail=ACTUALS_CONCURRENT_RETRY) from exc
        raise

    for actual in created:
        await record_audit_event(
            session, household_id, user_id, PROJECT_COST_ACTUAL_ENTITY_TYPE, actual.id, "create",
            after=actual.model_dump(mode="json"),
        )
    return BulkAssignActualsResult(created=created)


# ---------------------------------------------------------------------------
# Berechnung
# ---------------------------------------------------------------------------


async def calculate_scenario(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_id: UUID
) -> ScenarioCalculation:
    scenario = await require_scenario(session, household_id, project_id, scenario_id)
    inputs = await _cost_item_inputs(session, household_id, scenario_id)
    try:
        result = calculate_project_costs(
            inputs,
            ScenarioContingencyInput(
                mode=scenario.contingency_mode,
                rate_bps=scenario.contingency_rate_bps,
                fixed_minor=scenario.contingency_fixed_minor,
            ),
        )
    except CostFormulaCycleError as exc:
        raise HTTPException(status_code=422, detail="cost_formula_cycle") from exc

    warnings: list[str] = []
    schedule_incomplete = await _has_incomplete_cost_schedule(
        session, household_id, scenario_id, result.per_item_forecast_minor
    )
    if schedule_incomplete:
        warnings.append("cost_schedule_incomplete")

    fingerprint_payload = json.dumps(
        {
            "scenario_version": scenario.version,
            "items": sorted(
                (i.id, i.calculation_mode, str(i.estimate_manual_minor), str(i.quantity),
                 i.unit_price_minor, i.percentage_rate_bps, i.percentage_basis,
                 i.quoted_amount_minor, i.committed_amount_minor, i.status)
                for i in inputs
            ),
        },
        sort_keys=True,
        default=str,
    )
    input_fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()

    return ScenarioCalculation(
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        input_fingerprint=input_fingerprint,
        engine_version=ENGINE_VERSION,
        as_of_date=scenario.as_of_date,
        estimated_cost_minor=result.estimated_cost_minor,
        quoted_cost_minor=result.quoted_cost_minor,
        committed_cost_minor=result.committed_cost_minor,
        forecast_cash_cost_minor=result.forecast_cash_cost_minor,
        non_cash_cost_minor=result.non_cash_cost_minor,
        contingency_minor=result.contingency_minor,
        project_cash_cost_minor=result.project_cash_cost_minor,
        project_economic_cost_minor=result.project_economic_cost_minor,
        actual_cost_minor=result.actual_cost_minor,
        remaining_forecast_minor=result.remaining_forecast_minor,
        per_item_estimate_minor=result.per_item_estimate_minor,
        per_item_forecast_minor=result.per_item_forecast_minor,
        warnings=warnings,
    )


async def _has_incomplete_cost_schedule(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, forecasts: dict[str, int]
) -> bool:
    rows = (
        await session.execute(
            text(
                "select ci.id, ci.cost_kind, ci.status, "
                "coalesce(sum(se.amount_minor) "
                "filter (where se.status = 'planned'), 0) as scheduled "
                "from project_cost_items ci "
                "left join project_cost_schedule_entries se on se.cost_item_id = ci.id "
                "where ci.scenario_id = :scenario_id and ci.household_id = :household_id "
                "group by ci.id, ci.cost_kind, ci.status"
            ),
            {"scenario_id": str(scenario_id), "household_id": str(household_id)},
        )
    ).fetchall()
    for row in rows:
        if row.cost_kind != "cash" or row.status == "cancelled":
            continue
        forecast = forecasts.get(str(row.id), 0)
        if int(row.scheduled) != forecast:
            return True
    return False


# ---------------------------------------------------------------------------
# Vorlagen
# ---------------------------------------------------------------------------


async def _apply_template(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, template_key: str
) -> None:
    row = (
        await session.execute(
            text(
                "select template_json from project_template_versions "
                "where semantic_key = :semantic_key and is_released "
                "order by version desc limit 1"
            ),
            {"semantic_key": template_key},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project_template_not_found"
        )

    template = row.template_json
    for section_index, section in enumerate(template["sections"]):
        section_row = (
            await session.execute(
                text(
                    "insert into project_cost_sections "
                    "(household_id, scenario_id, semantic_key, name, cost_class, sort_order) "
                    "values (:household_id, :scenario_id, :semantic_key, :name, "
                    ":cost_class, :sort_order) "
                    "returning id"
                ),
                {
                    "household_id": str(household_id),
                    "scenario_id": str(scenario_id),
                    "semantic_key": section["semantic_key"],
                    "name": section["name"],
                    "cost_class": section["cost_class"],
                    "sort_order": section_index,
                },
            )
        ).first()
        assert section_row is not None
        for item_index, item in enumerate(section.get("items", [])):
            await session.execute(
                text(
                    "insert into project_cost_items "
                    "(household_id, scenario_id, section_id, semantic_key, name, cost_kind, "
                    "calculation_mode, estimate_manual_minor, contingency_eligible, sort_order) "
                    "values (:household_id, :scenario_id, :section_id, :semantic_key, :name, "
                    ":cost_kind, 'fixed', 0, :contingency_eligible, :sort_order)"
                ),
                {
                    "household_id": str(household_id),
                    "scenario_id": str(scenario_id),
                    "section_id": str(section_row.id),
                    "semantic_key": item["semantic_key"],
                    "name": item["name"],
                    "cost_kind": item.get("cost_kind", "cash"),
                    "contingency_eligible": item.get("contingency_eligible", True),
                    "sort_order": item_index,
                },
            )


# ---------------------------------------------------------------------------
# Wunschliste -> Vorhaben
# ---------------------------------------------------------------------------


async def convert_wishlist_to_project(
    session: AsyncSession, household_id: UUID, wishlist_id: UUID, user_id: UUID
) -> tuple[UUID, bool]:
    wishlist_row = (
        await session.execute(
            text(
                "select id, name, price_minor, note, goal_id, converted_project_id "
                "from wishlist_items where id = :id and household_id = :household_id"
            ),
            {"id": str(wishlist_id), "household_id": str(household_id)},
        )
    ).first()
    if wishlist_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wishlist_item_not_found")
    if wishlist_row.converted_project_id is not None:
        return wishlist_row.converted_project_id, True

    project = await create_project(
        session,
        household_id,
        user_id,
        MajorProjectCreate(name=wishlist_row.name, project_type="other", note=wishlist_row.note),
    )
    scenarios = await list_scenarios(session, household_id, project.id)
    baseline = scenarios[0]
    section = await create_cost_section(
        session, household_id, baseline.id,
        CostSectionCreate(name="Anschaffung", cost_class="direct", sort_order=0),
    )
    await create_cost_item(
        session, household_id, baseline.id, user_id,
        CostItemCreate(
            section_id=section.id, name=wishlist_row.name, calculation_mode="fixed",
            estimate_manual_minor=wishlist_row.price_minor,
        ),
    )
    await session.execute(
        text("update wishlist_items set converted_project_id = :project_id where id = :id"),
        {"project_id": str(project.id), "id": str(wishlist_id)},
    )
    return project.id, False


# ---------------------------------------------------------------------------
# Finanzierungsquellen (Plan 4.7/7.3)
# ---------------------------------------------------------------------------

_FINANCING_SOURCE_COLUMNS = (
    "id, scenario_id, name, source_type, status, planned_amount_minor, principal_limit_minor, "
    "available_on, account_id, savings_goal_id, asset_id, funding_program_version_id, "
    "gross_amount_minor, deductions_minor, sort_order, note, source_snapshot, version, "
    "created_at, updated_at"
)


async def _row_to_financing_source(
    session: AsyncSession, household_id: UUID, row: Any
) -> FinancingSource:
    mapping = dict(row._mapping)
    snapshot = mapping.pop("source_snapshot", None) or {}
    loan_terms = snapshot.get("loan_terms") if isinstance(snapshot, dict) else None
    selected_terms = snapshot.get("selected_loan_terms") if isinstance(snapshot, dict) else None
    draw_mode = (
        snapshot.get("draw_mode", "immediate") if isinstance(snapshot, dict) else "immediate"
    )
    if draw_mode not in ("immediate", "cost_plan", "manual"):
        draw_mode = "immediate"
    debt_plan = await debt_plan_service.get_debt_plan_detail_by_source(
        session, household_id, row.id
    )
    if debt_plan is not None:
        debt_plan = debt_plan.model_copy(update={"draw_mode": draw_mode})
    cost_draw_preview = None
    if mapping["source_type"] in ("commercial_loan", "subsidized_loan"):
        limit = mapping["principal_limit_minor"] or mapping["planned_amount_minor"]
        if limit > 0:
            starts_on = (
                date.fromisoformat(debt_plan.starts_on)
                if debt_plan is not None
                else (mapping["available_on"] or date.today())
            )
            cost_draw_preview = await _build_cost_draw_preview(
                session, mapping["scenario_id"], starts_on, limit, "cost_plan", []
            )
    return FinancingSource(
        **mapping,
        has_debt_plan=debt_plan is not None,
        funding_loan_terms=loan_terms if isinstance(loan_terms, dict) and loan_terms else None,
        selected_loan_terms=(
            selected_terms if isinstance(selected_terms, dict) and selected_terms else None
        ),
        debt_plan=debt_plan,
        cost_draw_preview=cost_draw_preview,
    )


async def require_financing_source(
    session: AsyncSession, household_id: UUID, source_id: UUID
) -> FinancingSource:
    row = (
        await session.execute(
            text(
                f"select {_FINANCING_SOURCE_COLUMNS} from project_financing_sources "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(source_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="financing_source_not_found"
        )
    return await _row_to_financing_source(session, household_id, row)


async def list_financing_sources(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> list[FinancingSource]:
    rows = (
        await session.execute(
            text(
                f"select {_FINANCING_SOURCE_COLUMNS} from project_financing_sources "
                "where scenario_id = :scenario_id and household_id = :household_id "
                "order by sort_order"
            ),
            {"scenario_id": str(scenario_id), "household_id": str(household_id)},
        )
    ).fetchall()
    return [await _row_to_financing_source(session, household_id, row) for row in rows]


async def create_financing_source(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    user_id: UUID,
    payload: FinancingSourceCreate,
    *,
    source_snapshot: dict[str, Any] | None = None,
) -> FinancingSource:
    row = (
        await session.execute(
            text(
                "insert into project_financing_sources "
                "(household_id, scenario_id, name, source_type, status, planned_amount_minor, "
                "principal_limit_minor, available_on, account_id, savings_goal_id, asset_id, "
                "funding_program_version_id, gross_amount_minor, deductions_minor, sort_order, "
                "note, source_snapshot) "
                "values (:household_id, :scenario_id, :name, :source_type, :status, "
                ":planned_amount_minor, :principal_limit_minor, :available_on, :account_id, "
                ":savings_goal_id, :asset_id, :funding_program_version_id, :gross_amount_minor, "
                ":deductions_minor, :sort_order, :note, cast(:source_snapshot as jsonb)) "
                f"returning {_FINANCING_SOURCE_COLUMNS}"
            ),
            {
                # Spalte ist `not null default '{}'` -- ohne Snapshot also leeres Objekt,
                # nicht NULL.
                "source_snapshot": json.dumps(source_snapshot or {}, default=str),
                "household_id": str(household_id),
                "scenario_id": str(scenario_id),
                "name": payload.name,
                "source_type": payload.source_type,
                "status": payload.status,
                "planned_amount_minor": payload.planned_amount_minor,
                "principal_limit_minor": payload.principal_limit_minor,
                "available_on": payload.available_on,
                "account_id": str(payload.account_id) if payload.account_id else None,
                "savings_goal_id": (
                    str(payload.savings_goal_id) if payload.savings_goal_id else None
                ),
                "asset_id": str(payload.asset_id) if payload.asset_id else None,
                "funding_program_version_id": (
                    str(payload.funding_program_version_id)
                    if payload.funding_program_version_id
                    else None
                ),
                "gross_amount_minor": payload.gross_amount_minor,
                "deductions_minor": payload.deductions_minor,
                "sort_order": payload.sort_order,
                "note": payload.note,
            },
        )
    ).first()
    assert row is not None
    source = await _row_to_financing_source(session, household_id, row)
    await record_audit_event(
        session, household_id, user_id, PROJECT_FINANCING_SOURCE_ENTITY_TYPE, source.id, "create",
        after=source.model_dump(mode="json"),
    )
    return source


async def update_financing_source(
    session: AsyncSession,
    household_id: UUID,
    source_id: UUID,
    payload: FinancingSourceUpdate,
    expected_version: int | None,
) -> FinancingSource:
    before = await require_financing_source(session, household_id, source_id)
    _assert_version(before.version, expected_version)

    changes = payload.model_dump(exclude={"clear_fields"}, exclude_unset=True)
    for field in payload.clear_fields:
        changes[field] = None
    if changes:
        params = {k: (str(v) if isinstance(v, UUID) else v) for k, v in changes.items()}
        assignments = ", ".join(f"{key} = :{key}" for key in changes)
        await session.execute(
            text(f"update project_financing_sources set {assignments} where id = :id"),
            {**params, "id": str(source_id)},
        )

    after = await require_financing_source(session, household_id, source_id)
    await record_audit_event(
        session, household_id, None, PROJECT_FINANCING_SOURCE_ENTITY_TYPE, source_id, "update",
        before=before.model_dump(mode="json"), after=after.model_dump(mode="json"),
    )
    return after


async def _build_cost_draw_preview(
    session: AsyncSession,
    scenario_id: UUID,
    starts_on: date,
    principal_limit_minor: int,
    draw_mode: str,
    manual_draws: list[DrawInput],
) -> CostDrawPreview:
    """Ordnet den Kreditrahmen chronologisch den geplanten Zahlungen zu.

    Monat 0 umfasst alle Zahlungen bis zum Kreditbeginn. Dadurch wird der Kauf
    zuerst bedient; spaetere Sanierungspositionen bleiben in ihrem wirklichen
    Faelligkeitsmonat sichtbar. Pro Monat wird genau eine Tranche erzeugt.
    """

    rows = (
        await session.execute(
            text(
                "select se.due_on, sum(se.amount_minor)::bigint as amount_minor "
                "from project_cost_schedule_entries se "
                "join project_cost_items ci on ci.id = se.cost_item_id "
                "where ci.scenario_id = :scenario_id and se.status = 'planned' "
                "group by se.due_on order by se.due_on"
            ),
            {"scenario_id": str(scenario_id)},
        )
    ).fetchall()
    monthly_costs: dict[int, int] = {}
    scheduled_cost_minor = 0
    for row in rows:
        month = max(_months_between(starts_on, row.due_on), 0)
        amount = int(row.amount_minor)
        scheduled_cost_minor += amount
        monthly_costs[month] = monthly_costs.get(month, 0) + amount

    if draw_mode == "immediate":
        allocated_minor = principal_limit_minor
        draws: list[DrawInput] = []
    elif draw_mode == "manual":
        draws = sorted(manual_draws, key=lambda draw: draw.occurs_after_month)
        allocated_minor = sum(draw.amount_minor for draw in draws)
    else:
        remaining = principal_limit_minor
        draws = []
        for month, cost_minor in sorted(monthly_costs.items()):
            amount = min(cost_minor, remaining)
            if amount > 0:
                draws.append(
                    DrawInput(occurs_after_month=month, amount_minor=amount, status="planned")
                )
                remaining -= amount
            if remaining == 0:
                break
        allocated_minor = principal_limit_minor - remaining

    return CostDrawPreview(
        draws=draws,
        scheduled_cost_minor=scheduled_cost_minor,
        allocated_minor=allocated_minor,
        unallocated_credit_minor=max(principal_limit_minor - allocated_minor, 0),
        uncovered_cost_minor=max(scheduled_cost_minor - allocated_minor, 0),
    )


async def preview_financing_draws(
    session: AsyncSession, scenario_id: UUID, payload: CostDrawPreviewInput
) -> CostDrawPreview:
    """Berechnet Abrufe fuer ungespeicherte Editorwerte ohne Schreibzugriff."""

    return await _build_cost_draw_preview(
        session,
        scenario_id,
        payload.starts_on,
        payload.principal_limit_minor,
        payload.draw_mode,
        payload.draws,
    )


def _debt_plan_create_payload(
    source_id: UUID, configuration: DebtPlanConfigurationInput, draws: list[DrawInput]
) -> DebtPlanCreate:
    return DebtPlanCreate(
        project_financing_source_id=source_id,
        name=configuration.name,
        creditor_label=configuration.creditor_label,
        status=configuration.status,
        principal_limit_minor=configuration.principal_limit_minor,
        opening_balance_minor=(
            configuration.principal_limit_minor if configuration.draw_mode == "immediate" else 0
        ),
        starts_on=configuration.starts_on,
        payment_interval_months=configuration.payment_interval_months,
        amortization_mode=configuration.amortization_mode,
        fixed_payment_minor=configuration.fixed_payment_minor,
        initial_repayment_rate_bps=configuration.initial_repayment_rate_bps,
        target_term_months=configuration.target_term_months,
        repayment_free_months=configuration.repayment_free_months,
        payment_free_months=configuration.payment_free_months,
        capitalize_interest_during_payment_free=(
            configuration.capitalize_interest_during_payment_free
        ),
        commitment_free_months=configuration.commitment_free_months,
        commitment_rate_bps_per_month=configuration.commitment_rate_bps_per_month,
        recast_payment_on_rate_change=configuration.recast_payment_on_rate_change,
    )


async def _save_debt_configuration(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    source_id: UUID,
    user_id: UUID,
    configuration: DebtPlanConfigurationInput,
) -> CostDrawPreview:
    starts_on = date.fromisoformat(configuration.starts_on)
    preview = await _build_cost_draw_preview(
        session,
        scenario_id,
        starts_on,
        configuration.principal_limit_minor,
        configuration.draw_mode,
        configuration.draws,
    )
    effective_draws = preview.draws
    current = await debt_plan_service.get_debt_plan_detail_by_source(
        session, household_id, source_id
    )
    if current is None:
        plan = await debt_plan_service.create_debt_plan(
            session,
            household_id,
            user_id,
            _debt_plan_create_payload(source_id, configuration, effective_draws),
        )
    else:
        clear_fields: list[str] = []
        if configuration.amortization_mode != "fixed_payment":
            clear_fields.append("fixed_payment_minor")
        if configuration.amortization_mode != "initial_repayment":
            clear_fields.append("initial_repayment_rate_bps")
        if configuration.amortization_mode != "full_repayment_term":
            clear_fields.append("target_term_months")
        plan = await debt_plan_service.update_debt_plan(
            session,
            household_id,
            current.id,
            DebtPlanUpdate(
                name=configuration.name,
                creditor_label=configuration.creditor_label,
                status=configuration.status,
                principal_limit_minor=configuration.principal_limit_minor,
                opening_balance_minor=(
                    configuration.principal_limit_minor
                    if configuration.draw_mode == "immediate"
                    else 0
                ),
                starts_on=configuration.starts_on,
                payment_interval_months=configuration.payment_interval_months,
                amortization_mode=configuration.amortization_mode,
                fixed_payment_minor=configuration.fixed_payment_minor,
                initial_repayment_rate_bps=configuration.initial_repayment_rate_bps,
                target_term_months=configuration.target_term_months,
                repayment_free_months=configuration.repayment_free_months,
                payment_free_months=configuration.payment_free_months,
                capitalize_interest_during_payment_free=(
                    configuration.capitalize_interest_during_payment_free
                ),
                commitment_free_months=configuration.commitment_free_months,
                commitment_rate_bps_per_month=configuration.commitment_rate_bps_per_month,
                recast_payment_on_rate_change=configuration.recast_payment_on_rate_change,
                clear_fields=clear_fields,
            ),
            configuration.version,
        )
    await debt_plan_service.replace_rate_periods(
        session, household_id, plan.id, configuration.rate_periods
    )
    await debt_plan_service.replace_draws(session, household_id, plan.id, effective_draws)
    await debt_plan_service.replace_fees(session, household_id, plan.id, configuration.fees)
    await debt_plan_service.replace_special_repayments(
        session, household_id, plan.id, configuration.special_repayments
    )
    return preview


async def create_financing_configuration(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    user_id: UUID,
    payload: FinancingConfigurationCreate,
) -> FinancingConfigurationResponse:
    configuration = payload.debt_plan
    snapshot: dict[str, Any] = {}
    if payload.selected_loan_terms:
        snapshot["selected_loan_terms"] = payload.selected_loan_terms
        snapshot["loan_terms"] = payload.selected_loan_terms
    if configuration is not None:
        snapshot["draw_mode"] = configuration.draw_mode
    source = await create_financing_source(
        session,
        household_id,
        scenario_id,
        user_id,
        payload.source,
        source_snapshot=snapshot,
    )
    preview = CostDrawPreview()
    if configuration is not None:
        preview = await _save_debt_configuration(
            session, household_id, scenario_id, source.id, user_id, configuration
        )
    return FinancingConfigurationResponse(
        source=await require_financing_source(session, household_id, source.id),
        cost_draw_preview=preview,
    )


async def update_financing_configuration(
    session: AsyncSession,
    household_id: UUID,
    source_id: UUID,
    user_id: UUID,
    payload: FinancingConfigurationUpdate,
    expected_version: int | None,
) -> FinancingConfigurationResponse:
    before = await require_financing_source(session, household_id, source_id)
    await update_financing_source(
        session, household_id, source_id, payload.source, expected_version
    )
    preview = await _save_debt_configuration(
        session,
        household_id,
        before.scenario_id,
        source_id,
        user_id,
        payload.debt_plan,
    )

    snapshot_row = (
        await session.execute(
            text("select source_snapshot from project_financing_sources where id = :id"),
            {"id": str(source_id)},
        )
    ).first()
    snapshot = dict(snapshot_row.source_snapshot or {}) if snapshot_row else {}
    snapshot["draw_mode"] = payload.debt_plan.draw_mode
    if payload.selected_loan_terms is not None:
        snapshot["selected_loan_terms"] = payload.selected_loan_terms
        snapshot["loan_terms"] = payload.selected_loan_terms
    await session.execute(
        text(
            "update project_financing_sources set source_snapshot = cast(:snapshot as jsonb) "
            "where id = :id and household_id = :household_id"
        ),
        {
            "snapshot": json.dumps(snapshot, default=str),
            "id": str(source_id),
            "household_id": str(household_id),
        },
    )
    return FinancingConfigurationResponse(
        source=await require_financing_source(session, household_id, source_id),
        cost_draw_preview=preview,
    )


async def delete_financing_source(
    session: AsyncSession, household_id: UUID, source_id: UUID, user_id: UUID
) -> None:
    before = await require_financing_source(session, household_id, source_id)
    await session.execute(
        text(
            "delete from project_financing_sources where id = :id and household_id = :household_id"
        ),
        {"id": str(source_id), "household_id": str(household_id)},
    )
    await record_audit_event(
        session, household_id, user_id, PROJECT_FINANCING_SOURCE_ENTITY_TYPE, source_id, "delete",
        before=before.model_dump(mode="json"),
    )


async def get_equity_availability(
    session: AsyncSession, household_id: UUID, project_id: UUID
) -> EquityAvailability:
    """Kontostaende minus Reservierungen aus ALLEN ausgewaehlten/aktiven Szenarien
    anderer, nicht archivierter Projekte (Plan 8.5) -- ein Konto darf nicht in
    zwei Vorhaben gleichzeitig als volles Eigenkapital gelten."""
    balances = await fetch_current_account_balances(session, household_id)
    reserved_rows = await session.execute(
        text(
            "select pfs.account_id, sum(pfs.planned_amount_minor) as reserved "
            "from project_financing_sources pfs "
            "join project_scenarios s on s.id = pfs.scenario_id "
            "join major_projects p on p.id = s.project_id "
            "where pfs.household_id = :household_id and pfs.source_type = 'cash_equity' "
            "and s.status = 'selected' and p.status <> 'archived' and p.id <> :project_id "
            "group by pfs.account_id"
        ),
        {"household_id": str(household_id), "project_id": str(project_id)},
    )
    reserved_by_account = {row.account_id: int(row.reserved) for row in reserved_rows}

    account_names = await session.execute(
        text("select id, name from accounts where household_id = :household_id and not is_closed"),
        {"household_id": str(household_id)},
    )
    names_by_id = {row.id: row.name for row in account_names}

    accounts = [
        EquityAvailabilityAccount(
            account_id=b.account_id,
            account_name=names_by_id.get(b.account_id, "?"),
            current_balance_minor=b.balance_minor,
            reserved_by_other_projects_minor=reserved_by_account.get(b.account_id, 0),
            available_minor=b.balance_minor - reserved_by_account.get(b.account_id, 0),
        )
        for b in balances
        if b.balance_minor > 0
    ]
    return EquityAvailability(accounts=accounts)


# ---------------------------------------------------------------------------
# Haushaltsannahmen (Plan 4.9/7.4)
# ---------------------------------------------------------------------------


async def suggest_cashflow_assumptions(
    session: AsyncSession, household_id: UUID, project_id: UUID, lookback_months: int = 12
) -> CashflowSuggestion:
    """Read-only Vorschlag aus der Buchungshistorie (Plan 5.8): Median der
    letzten `lookback_months` vollstaendigen Monate. Der unvollstaendige
    aktuelle Monat wird ausgeschlossen; Projekt-Istkosten sind in P2 noch
    nirgends verbucht und beeinflussen die Historie daher noch nicht."""
    balances = await fetch_current_account_balances(session, household_id)
    starting_liquidity = sum(
        b.balance_minor
        for b in balances
        if b.account_type in ("checking", "savings", "cash") and b.balance_minor > 0
    )

    income_rows = await session.execute(
        text(
            "select month, coalesce(sum(total), 0) as monthly_total from ("
            "  select date_trunc('month', t.booked_date) as month, sum(s.amount_minor) as total "
            "  from transaction_splits s "
            "  join transactions t on t.id = s.transaction_id "
            "  join accounts a on a.id = t.account_id "
            "  left join categories c on c.id = s.category_id "
            "  where t.household_id = :household_id and a.on_budget and not t.is_archived "
            "  and not t.is_transfer "
            "  and t.booked_date >= (current_date - make_interval(months => :lookback)) "
            "  and t.booked_date < date_trunc('month', current_date) "
            "  and (c.default_type = 'income' or (s.category_id is null and s.amount_minor > 0)) "
            "  group by date_trunc('month', t.booked_date)"
            ") monthly group by month"
        ),
        {"household_id": str(household_id), "lookback": lookback_months},
    )
    income_values = sorted(int(row.monthly_total) for row in income_rows)

    expense_rows = await session.execute(
        text(
            "select month, coalesce(sum(total), 0) as monthly_total from ("
            "  select date_trunc('month', t.booked_date) as month, sum(s.amount_minor) as total "
            "  from transaction_splits s "
            "  join transactions t on t.id = s.transaction_id "
            "  join accounts a on a.id = t.account_id "
            "  left join categories c on c.id = s.category_id "
            "  where t.household_id = :household_id and a.on_budget and not t.is_archived "
            "  and not t.is_transfer "
            "  and t.booked_date >= (current_date - make_interval(months => :lookback)) "
            "  and t.booked_date < date_trunc('month', current_date) "
            "  and s.amount_minor < 0 and coalesce(c.default_type, 'expense') <> 'income' "
            "  group by date_trunc('month', t.booked_date)"
            ") monthly group by month"
        ),
        {"household_id": str(household_id), "lookback": lookback_months},
    )
    expense_values = sorted(abs(int(row.monthly_total)) for row in expense_rows)

    debt_rows = await session.execute(
        text(
            "select coalesce(sum(fixed_payment_minor), 0) as total from debt_plans "
            "where household_id = :household_id and status = 'active' "
            "and fixed_payment_minor is not null and payment_interval_months = 1"
        ),
        {"household_id": str(household_id)},
    )
    existing_debt_service = int(debt_rows.scalar_one())

    def _median(values: list[int]) -> int:
        if not values:
            return 0
        n = len(values)
        mid = n // 2
        if n % 2 == 1:
            return values[mid]
        return (values[mid - 1] + values[mid]) // 2

    return CashflowSuggestion(
        starting_liquidity_minor=starting_liquidity,
        monthly_net_income_minor=_median(income_values),
        monthly_living_costs_minor=_median(expense_values),
        monthly_existing_debt_service_minor=existing_debt_service,
        analysis_lookback_months=lookback_months,
        source_snapshot={
            "months_with_income_data": len(income_values),
            "months_with_expense_data": len(expense_values),
            "as_of": date.today().isoformat(),
        },
    )


async def get_cashflow_assumptions(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> CashflowAssumptions | None:
    row = (
        await session.execute(
            text(
                "select scenario_id, starting_liquidity_minor, monthly_net_income_minor, "
                "monthly_living_costs_minor, monthly_current_housing_minor, "
                "monthly_existing_debt_service_minor, monthly_other_commitments_minor, "
                "analysis_lookback_months, source, source_snapshot, updated_at "
                "from project_cashflow_assumptions "
                "where scenario_id = :id and household_id = :household_id"
            ),
            {"id": str(scenario_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return None
    return CashflowAssumptions(**dict(row._mapping))


async def save_cashflow_assumptions(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, payload: CashflowAssumptionsInput
) -> CashflowAssumptions:
    await require_scenario_by_id(session, household_id, scenario_id)
    await session.execute(
        text(
            "insert into project_cashflow_assumptions "
            "(scenario_id, household_id, starting_liquidity_minor, monthly_net_income_minor, "
            "monthly_living_costs_minor, monthly_current_housing_minor, "
            "monthly_existing_debt_service_minor, monthly_other_commitments_minor, "
            "analysis_lookback_months, source, updated_at) "
            "values (:scenario_id, :household_id, :starting_liquidity_minor, "
            ":monthly_net_income_minor, :monthly_living_costs_minor, "
            ":monthly_current_housing_minor, :monthly_existing_debt_service_minor, "
            ":monthly_other_commitments_minor, :analysis_lookback_months, :source, now()) "
            "on conflict (scenario_id) do update set "
            "starting_liquidity_minor = excluded.starting_liquidity_minor, "
            "monthly_net_income_minor = excluded.monthly_net_income_minor, "
            "monthly_living_costs_minor = excluded.monthly_living_costs_minor, "
            "monthly_current_housing_minor = excluded.monthly_current_housing_minor, "
            "monthly_existing_debt_service_minor = excluded.monthly_existing_debt_service_minor, "
            "monthly_other_commitments_minor = excluded.monthly_other_commitments_minor, "
            "analysis_lookback_months = excluded.analysis_lookback_months, "
            "source = excluded.source, updated_at = now()"
        ),
        {
            "scenario_id": str(scenario_id),
            "household_id": str(household_id),
            **payload.model_dump(),
        },
    )
    result = await get_cashflow_assumptions(session, household_id, scenario_id)
    assert result is not None
    return result


async def require_scenario_by_id(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> ProjectScenario:
    row = (
        await session.execute(
            text(
                f"select {_SCENARIO_COLUMNS} from project_scenarios "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(scenario_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_NOT_FOUND)
    return _row_to_scenario(row)


# ---------------------------------------------------------------------------
# Folgekosten (Plan 4.9)
# ---------------------------------------------------------------------------

_RECURRING_IMPACT_COLUMNS = (
    "id, scenario_id, name, impact_type, amount_minor, frequency, starts_on, ends_on, "
    "confidence, include_in_affordability, note, version, created_at, updated_at"
)


async def list_recurring_impacts(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> list[RecurringImpact]:
    result = await session.execute(
        text(
            f"select {_RECURRING_IMPACT_COLUMNS} from project_recurring_impacts "
            "where scenario_id = :scenario_id and household_id = :household_id "
            "order by starts_on"
        ),
        {"scenario_id": str(scenario_id), "household_id": str(household_id)},
    )
    return [RecurringImpact(**row._mapping) for row in result]


async def require_recurring_impact(
    session: AsyncSession, household_id: UUID, impact_id: UUID
) -> RecurringImpact:
    row = (
        await session.execute(
            text(
                f"select {_RECURRING_IMPACT_COLUMNS} from project_recurring_impacts "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(impact_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="recurring_impact_not_found"
        )
    return RecurringImpact(**row._mapping)


async def create_recurring_impact(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, payload: RecurringImpactCreate
) -> RecurringImpact:
    row = (
        await session.execute(
            text(
                "insert into project_recurring_impacts "
                "(household_id, scenario_id, name, impact_type, amount_minor, frequency, "
                "starts_on, ends_on, confidence, include_in_affordability, note) "
                "values (:household_id, :scenario_id, :name, :impact_type, :amount_minor, "
                ":frequency, :starts_on, :ends_on, :confidence, :include_in_affordability, :note) "
                f"returning {_RECURRING_IMPACT_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "scenario_id": str(scenario_id),
                **payload.model_dump(),
            },
        )
    ).first()
    assert row is not None
    return RecurringImpact(**row._mapping)


async def update_recurring_impact(
    session: AsyncSession,
    household_id: UUID,
    impact_id: UUID,
    payload: RecurringImpactUpdate,
    expected_version: int | None,
) -> RecurringImpact:
    before = await require_recurring_impact(session, household_id, impact_id)
    _assert_version(before.version, expected_version)
    changes = payload.model_dump(exclude={"clear_fields"}, exclude_unset=True)
    for field in payload.clear_fields:
        changes[field] = None
    if changes:
        assignments = ", ".join(f"{key} = :{key}" for key in changes)
        await session.execute(
            text(f"update project_recurring_impacts set {assignments} where id = :id"),
            {**changes, "id": str(impact_id)},
        )
    return await require_recurring_impact(session, household_id, impact_id)


async def delete_recurring_impact(
    session: AsyncSession, household_id: UUID, impact_id: UUID
) -> None:
    await require_recurring_impact(session, household_id, impact_id)
    await session.execute(
        text(
            "delete from project_recurring_impacts where id = :id and household_id = :household_id"
        ),
        {"id": str(impact_id), "household_id": str(household_id)},
    )


# ---------------------------------------------------------------------------
# Zielwirkung (Plan 4.9/5.9)
# ---------------------------------------------------------------------------


async def list_goal_assumptions(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> list[GoalAssumption]:
    result = await session.execute(
        text(
            "select id, scenario_id, savings_goal_id, withdrawal_minor, withdrawal_on, "
            "protected_minimum_minor, baseline_monthly_contribution_minor, "
            "project_monthly_contribution_minor, created_at, updated_at "
            "from project_goal_assumptions "
            "where scenario_id = :scenario_id and household_id = :household_id"
        ),
        {"scenario_id": str(scenario_id), "household_id": str(household_id)},
    )
    return [GoalAssumption(**row._mapping) for row in result]


async def save_goal_assumption(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, payload: GoalAssumptionInput
) -> GoalAssumption:
    await require_scenario_by_id(session, household_id, scenario_id)
    row = (
        await session.execute(
            text(
                "insert into project_goal_assumptions "
                "(household_id, scenario_id, savings_goal_id, withdrawal_minor, withdrawal_on, "
                "protected_minimum_minor, baseline_monthly_contribution_minor, "
                "project_monthly_contribution_minor) "
                "values (:household_id, :scenario_id, :savings_goal_id, :withdrawal_minor, "
                ":withdrawal_on, :protected_minimum_minor, :baseline_monthly_contribution_minor, "
                ":project_monthly_contribution_minor) "
                "on conflict (scenario_id, savings_goal_id) do update set "
                "withdrawal_minor = excluded.withdrawal_minor, "
                "withdrawal_on = excluded.withdrawal_on, "
                "protected_minimum_minor = excluded.protected_minimum_minor, "
                "baseline_monthly_contribution_minor = "
                "excluded.baseline_monthly_contribution_minor, "
                "project_monthly_contribution_minor = excluded.project_monthly_contribution_minor, "
                "updated_at = now() "
                "returning id, scenario_id, savings_goal_id, withdrawal_minor, withdrawal_on, "
                "protected_minimum_minor, baseline_monthly_contribution_minor, "
                "project_monthly_contribution_minor, created_at, updated_at"
            ),
            {
                "household_id": str(household_id),
                "scenario_id": str(scenario_id),
                "savings_goal_id": str(payload.savings_goal_id),
                "withdrawal_minor": payload.withdrawal_minor,
                "withdrawal_on": payload.withdrawal_on,
                "protected_minimum_minor": payload.protected_minimum_minor,
                "baseline_monthly_contribution_minor": payload.baseline_monthly_contribution_minor,
                "project_monthly_contribution_minor": payload.project_monthly_contribution_minor,
            },
        )
    ).first()
    assert row is not None
    return GoalAssumption(**row._mapping)


async def delete_goal_assumption(
    session: AsyncSession, household_id: UUID, goal_assumption_id: UUID
) -> None:
    deleted = await session.execute(
        text(
            "delete from project_goal_assumptions where id = :id and household_id = :household_id "
            "returning id"
        ),
        {"id": str(goal_assumption_id), "household_id": str(household_id)},
    )
    if deleted.first() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="goal_assumption_not_found"
        )


def _months_between(start: date, end: date) -> int:
    return max((end.year - start.year) * 12 + (end.month - start.month), 0)


async def _calculate_goal_impacts(
    session: AsyncSession, household_id: UUID, scenario: ProjectScenario, project: MajorProject
) -> list[GoalAssumptionImpact]:
    from app.services.goal_service import fetch_goal_available_by_goal

    assumptions = await list_goal_assumptions(session, household_id, scenario.id)
    if not assumptions:
        return []

    available_by_goal = await fetch_goal_available_by_goal(session, household_id)
    goals_rows = await session.execute(
        text(
            "select id, name, target_amount_minor from savings_goals "
            "where household_id = :household_id and id = any(:ids)"
        ),
        {
            "household_id": str(household_id),
            "ids": [str(a.savings_goal_id) for a in assumptions],
        },
    )
    goal_meta = {row.id: (row.name, row.target_amount_minor) for row in goals_rows}

    project_start_month = (
        _months_between(scenario.as_of_date, project.planned_start_on)
        if project.planned_start_on
        else 0
    )

    impacts: list[GoalAssumptionImpact] = []
    for assumption in assumptions:
        name, target = goal_meta.get(assumption.savings_goal_id, ("?", 0))
        current = available_by_goal.get(assumption.savings_goal_id, 0)

        baseline_reach = _simulate_goal_reach(
            current, target, assumption.baseline_monthly_contribution_minor
        )

        withdrawal_month = (
            _months_between(scenario.as_of_date, assumption.withdrawal_on)
            if assumption.withdrawal_on
            else None
        )
        after_withdrawal = current - assumption.withdrawal_minor
        protection_breached = after_withdrawal < assumption.protected_minimum_minor
        max_free_withdrawal = max(current - assumption.protected_minimum_minor, 0)

        project_reach = _simulate_goal_reach(
            current,
            target,
            assumption.baseline_monthly_contribution_minor,
            project_monthly=assumption.project_monthly_contribution_minor,
            project_start_month=project_start_month,
            withdrawal_month=withdrawal_month,
            withdrawal_amount=assumption.withdrawal_minor,
        )

        delay = (
            None
            if baseline_reach is None or project_reach is None
            else project_reach - baseline_reach
        )

        impacts.append(
            GoalAssumptionImpact(
                savings_goal_id=assumption.savings_goal_id,
                goal_name=name,
                current_amount_minor=current,
                target_amount_minor=target,
                baseline_reach_month=baseline_reach,
                project_reach_month=project_reach,
                delay_months=delay,
                reachable=project_reach is not None,
                protection_breached=protection_breached,
                max_free_withdrawal_minor=max_free_withdrawal,
            )
        )
    return impacts


def _simulate_goal_reach(
    current_amount: int,
    target_amount: int,
    baseline_monthly: int,
    *,
    project_monthly: int | None = None,
    project_start_month: int = 0,
    withdrawal_month: int | None = None,
    withdrawal_amount: int = 0,
) -> int | None:
    """Ausgangsstand, optionale Entnahme, danach Baseline- bzw. ab
    `project_start_month` Projekt-Beitrag (Plan 5.9). Liefert `None`, wenn das
    Ziel unter diesen Annahmen nicht erreichbar ist -- kein erfundenes Datum."""
    if current_amount >= target_amount:
        return 0
    balance = Decimal(current_amount)
    target = Decimal(target_amount)
    for month in range(1, 601):
        if withdrawal_month is not None and month == withdrawal_month:
            balance -= Decimal(withdrawal_amount)
        rate = (
            project_monthly
            if project_monthly is not None and month > project_start_month
            else baseline_monthly
        )
        if rate <= 0 and balance < target:
            return None
        balance += Decimal(rate)
        if balance >= target:
            return month
    return None


# ---------------------------------------------------------------------------
# Finanzierungsluecke, Leistbarkeit, Zeitreihe (Plan 5.7/5.8)
# ---------------------------------------------------------------------------


async def _build_household_cashflow_input(
    session: AsyncSession, household_id: UUID, scenario: ProjectScenario
) -> HouseholdCashflowInput | None:
    assumptions = await get_cashflow_assumptions(session, household_id, scenario.id)
    if assumptions is None:
        return None

    horizon = scenario.calculation_horizon_months
    as_of = scenario.as_of_date

    def month_of(value: date | None) -> int:
        if value is None:
            return 0
        return max(_months_between(as_of, value), 0)

    schedule_rows = await session.execute(
        text(
            "select se.due_on, se.amount_minor from project_cost_schedule_entries se "
            "join project_cost_items ci on ci.id = se.cost_item_id "
            "where ci.scenario_id = :scenario_id and se.status = 'planned'"
        ),
        {"scenario_id": str(scenario.id)},
    )
    cost_outflows = tuple(
        CostOutflow(month=month_of(row.due_on), amount_minor=row.amount_minor)
        for row in schedule_rows
    )

    source_rows = (
        await session.execute(
            text(
                "select id, source_type, status, planned_amount_minor, available_on "
                "from project_financing_sources where scenario_id = :scenario_id"
            ),
            {"scenario_id": str(scenario.id)},
        )
    ).fetchall()

    planned_inflows: list[FinancingInflow] = []
    debt_cashflows: list[DebtCashflow] = []
    for source in source_rows:
        if source.status == "rejected" or source.source_type in ("cash_equity", "non_cash_equity"):
            continue
        secured = source.status in ("approved", "contracted", "received")
        if source.source_type in ("commercial_loan", "subsidized_loan"):
            plan_row = (
                await session.execute(
                    text(
                        "select id, starts_on, opening_balance_minor from debt_plans "
                        "where project_financing_source_id = :sid"
                    ),
                    {"sid": str(source.id)},
                )
            ).first()
            if plan_row is None:
                continue
            start_month = month_of(plan_row.starts_on)
            if start_month >= horizon:
                continue
            schedule = await debt_plan_service.calculate_schedule(
                session, household_id, plan_row.id, horizon_months=horizon - start_month
            )
            # Eine Sofortauszahlung liegt als opening_balance im Tilgungsplan
            # und erscheint deshalb nicht noch einmal als debt_draws-Zeile.
            # Im Haushalts-Cashflow ist sie dennoch genau einmal ein Zufluss.
            if plan_row.opening_balance_minor > 0:
                debt_cashflows.append(
                    DebtCashflow(
                        month=start_month,
                        draw_minor=plan_row.opening_balance_minor,
                        cash_outflow_minor=0,
                    )
                )
            for row in schedule.rows:
                debt_cashflows.append(
                    DebtCashflow(
                        month=start_month + row.month,
                        draw_minor=row.draw_minor,
                        cash_outflow_minor=row.cash_outflow_minor,
                    )
                )
        else:
            planned_inflows.append(
                FinancingInflow(
                    month=month_of(source.available_on),
                    amount_minor=source.planned_amount_minor,
                    secured=secured,
                )
            )

    impact_rows = await session.execute(
        text(
            "select impact_type, amount_minor, frequency, starts_on, ends_on, "
            "include_in_affordability from project_recurring_impacts "
            "where scenario_id = :scenario_id"
        ),
        {"scenario_id": str(scenario.id)},
    )
    recurring = tuple(
        EngineRecurringImpactInput(
            impact_type=row.impact_type,
            amount_minor=row.amount_minor,
            frequency=row.frequency,
            starts_on_month=month_of(row.starts_on),
            ends_on_month=month_of(row.ends_on) if row.ends_on else None,
            include_in_affordability=row.include_in_affordability,
        )
        for row in impact_rows
    )

    goal_rows = await session.execute(
        text(
            "select project_monthly_contribution_minor from project_goal_assumptions "
            "where scenario_id = :scenario_id"
        ),
        {"scenario_id": str(scenario.id)},
    )
    goal_contributions = tuple(
        GoalContribution(
            monthly_amount_minor=row.project_monthly_contribution_minor, starts_on_month=0
        )
        for row in goal_rows
    )

    contingency = await calculate_scenario(session, household_id, scenario.project_id, scenario.id)

    return HouseholdCashflowInput(
        assumptions=EngineCashflowAssumptions(
            starting_liquidity_minor=assumptions.starting_liquidity_minor,
            monthly_net_income_minor=assumptions.monthly_net_income_minor,
            monthly_living_costs_minor=assumptions.monthly_living_costs_minor,
            monthly_current_housing_minor=assumptions.monthly_current_housing_minor,
            monthly_existing_debt_service_minor=assumptions.monthly_existing_debt_service_minor,
            monthly_other_commitments_minor=assumptions.monthly_other_commitments_minor,
            minimum_liquidity_reserve_minor=scenario.minimum_liquidity_reserve_minor,
        ),
        horizon_months=horizon,
        cost_outflows=cost_outflows,
        planned_financing_inflows=tuple(planned_inflows),
        secured_financing_inflows=tuple(f for f in planned_inflows if f.secured),
        debt_cashflows=tuple(debt_cashflows),
        recurring_impacts=recurring,
        goal_contributions=goal_contributions,
        contingency_minor=contingency.contingency_minor,
    )


async def calculate_financial_summary(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_id: UUID
) -> ScenarioFinancialSummary:
    scenario = await require_scenario(session, household_id, project_id, scenario_id)
    project = await require_project(session, household_id, project_id)
    cost_calc = await calculate_scenario(session, household_id, project_id, scenario_id)

    source_rows = await session.execute(
        text(
            "select status, source_type, planned_amount_minor, account_id "
            "from project_financing_sources where scenario_id = :scenario_id"
        ),
        {"scenario_id": str(scenario_id)},
    )
    planned_funding = 0
    secured_funding = 0
    cash_equity_by_account: dict[UUID, int] = {}
    for row in source_rows:
        if row.status == "rejected" or row.source_type == "non_cash_equity":
            continue
        planned_funding += row.planned_amount_minor
        if row.status in ("approved", "contracted", "received"):
            secured_funding += row.planned_amount_minor
        if row.source_type == "cash_equity" and row.account_id is not None:
            cash_equity_by_account[row.account_id] = (
                cash_equity_by_account.get(row.account_id, 0) + row.planned_amount_minor
            )

    equity_overallocated = False
    if cash_equity_by_account:
        equity = await get_equity_availability(session, household_id, project_id)
        balances_by_account = {a.account_id: a for a in equity.accounts}
        for account_id, reserved_here in cash_equity_by_account.items():
            info = balances_by_account.get(account_id)
            if info is None:
                continue
            if reserved_here + info.reserved_by_other_projects_minor > info.current_balance_minor:
                equity_overallocated = True

    cash_required = cost_calc.project_cash_cost_minor
    funding_gap = FundingGap(
        cash_required_until_completion_minor=cash_required,
        planned_cash_funding_minor=planned_funding,
        secured_cash_funding_minor=secured_funding,
        planned_gap_minor=cash_required - planned_funding,
        secured_gap_minor=cash_required - secured_funding,
    )

    warnings = list(cost_calc.warnings)
    if funding_gap.secured_gap_minor > 0:
        warnings.append("secured_funding_gap")
    elif funding_gap.planned_gap_minor > 0:
        warnings.append("funding_gap")
    if equity_overallocated:
        warnings.append("equity_overallocated")

    affordability: AffordabilityMetrics | None = None
    cashflow_input = await _build_household_cashflow_input(session, household_id, scenario)
    if cashflow_input is not None:
        result = calculate_household_cashflow(cashflow_input)
        warnings.extend(w for w in result.warnings if w not in warnings)
        affordability = AffordabilityMetrics(
            lowest_reserve_minor=result.lowest_reserve_minor,
            lowest_reserve_month=result.lowest_reserve_month,
            negative_months_count=result.negative_months_count,
            peak_debt_service_minor=result.peak_debt_service_minor,
            monthly_surplus_before_minor=result.monthly_surplus_before_minor,
            monthly_surplus_after_minor=result.monthly_surplus_after_minor,
            first_shortfall_month=result.first_shortfall_month,
            reserve_breached=result.reserve_breached,
        )

    goal_impacts = await _calculate_goal_impacts(session, household_id, scenario, project)
    for impact in goal_impacts:
        if impact.protection_breached:
            warnings.append("goal_protection_breached")

    return ScenarioFinancialSummary(
        scenario_id=scenario_id,
        funding_gap=funding_gap,
        affordability=affordability,
        goal_impacts=goal_impacts,
        warnings=sorted(set(warnings)),
    )


async def get_timeline(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_id: UUID
) -> ScenarioTimeline:
    scenario = await require_scenario(session, household_id, project_id, scenario_id)
    cashflow_input = await _build_household_cashflow_input(session, household_id, scenario)
    if cashflow_input is None:
        return ScenarioTimeline(scenario_id=scenario_id, months=[])
    result = calculate_household_cashflow(cashflow_input)
    return ScenarioTimeline(
        scenario_id=scenario_id,
        months=[CashflowMonthRow(**row.model_dump()) for row in result.rows],
    )


# ---------------------------------------------------------------------------
# Uebersicht und Berichte (Plan 11.7/P5)
# ---------------------------------------------------------------------------


async def _active_projects_with_selected_scenario(
    session: AsyncSession, household_id: UUID
) -> list[Any]:
    result = await session.execute(
        text(
            "select p.id, p.name, p.project_type, s.id as selected_scenario_id, "
            "s.name as selected_scenario_name "
            "from major_projects p "
            "left join project_scenarios s "
            "  on s.project_id = p.id and s.status = 'selected' "
            "  and s.household_id = p.household_id "
            "where p.household_id = :household_id and p.status = any(:statuses) "
            "order by p.updated_at desc"
        ),
        {"household_id": str(household_id), "statuses": list(_DASHBOARD_ACTIVE_STATUSES)},
    )
    return list(result.fetchall())


async def _next_scheduled_payment(
    session: AsyncSession, household_id: UUID
) -> ProjectDashboardNextPayment | None:
    """Naechste faellige, noch nicht stornierte Kostenposition ueber alle
    aktiven Vorhaben (ausgewaehltes Szenario) hinweg. Ueberfaellige Eintraege
    zaehlen als "naechste" mit, weil sie noch dringlicher sind - kein Filter
    auf `due_on >= heute`."""
    row = (
        await session.execute(
            text(
                "select p.id as project_id, p.name as project_name, "
                "ci.name as cost_item_name, se.due_on, se.amount_minor "
                "from project_cost_schedule_entries se "
                "join project_cost_items ci "
                "  on ci.id = se.cost_item_id and ci.household_id = se.household_id "
                "join project_scenarios s "
                "  on s.id = ci.scenario_id and s.household_id = ci.household_id "
                "join major_projects p "
                "  on p.id = s.project_id and p.household_id = s.household_id "
                "where p.household_id = :household_id "
                "  and p.status = any(:statuses) "
                "  and s.status = 'selected' "
                "  and se.status = 'planned' "
                "order by se.due_on asc, se.amount_minor desc "
                "limit 1"
            ),
            {"household_id": str(household_id), "statuses": list(_DASHBOARD_ACTIVE_STATUSES)},
        )
    ).first()
    if row is None:
        return None
    return ProjectDashboardNextPayment(
        project_id=row.project_id,
        project_name=row.project_name,
        cost_item_name=row.cost_item_name,
        due_on=row.due_on,
        amount_minor=row.amount_minor,
    )


async def get_dashboard_summary(
    session: AsyncSession, household_id: UUID
) -> ProjectDashboardSummary:
    """Uebersichtskarte fuer das Dashboard (Plan 11.7): Anzahl aktiver
    Vorhaben, naechste grosse Zahlung, gesicherte Luecke, wichtigste Warnung.

    Jede Zahl kommt unveraendert aus derselben Funktion, die auch die
    Vorhaben-Detailseite fuer genau dieses Projekt aufruft
    (`calculate_financial_summary`) - keine zweite, moeglicherweise
    abweichende Berechnung."""
    rows = await _active_projects_with_selected_scenario(session, household_id)

    total_secured_gap_minor = 0
    best_warning: tuple[int, ProjectDashboardWarning] | None = None

    for row in rows:
        if row.selected_scenario_id is None:
            continue
        summary = await calculate_financial_summary(
            session, household_id, row.id, row.selected_scenario_id
        )
        # Ein negativer/ueberdeckter Gap bei Projekt A darf die offene Luecke
        # von Projekt B nicht rechnerisch ausgleichen - gesicherte Mittel
        # sind je Szenario zweckgebunden (Plan 11.1).
        if summary.funding_gap.secured_gap_minor > 0:
            total_secured_gap_minor += summary.funding_gap.secured_gap_minor
        for code in summary.warnings:
            rank = _warning_rank(code)
            if best_warning is None or rank < best_warning[0]:
                best_warning = (
                    rank,
                    ProjectDashboardWarning(project_id=row.id, project_name=row.name, code=code),
                )

    next_payment = await _next_scheduled_payment(session, household_id)

    return ProjectDashboardSummary(
        active_project_count=len(rows),
        next_payment=next_payment,
        total_secured_gap_minor=total_secured_gap_minor,
        top_warning=best_warning[1] if best_warning else None,
    )


async def _financing_cost_for_scenario(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> int:
    """Summe der Finanzierungskosten (Zins- und Bereitstellungszinsen sowie
    Gebuehren, OHNE Tilgung/Kapital) aller an dieses Szenario gebundenen
    Tilgungsplaene - dieselbe Groesse, die `debt_plan_service.calculate_schedule`
    auch fuer einzeln aufgerufene Tilgungsplaene liefert."""
    plan_rows = (
        await session.execute(
            text(
                "select dp.id from debt_plans dp "
                "join project_financing_sources pfs "
                "  on pfs.id = dp.project_financing_source_id "
                "  and pfs.household_id = dp.household_id "
                "where pfs.scenario_id = :scenario_id and dp.household_id = :household_id"
            ),
            {"scenario_id": str(scenario_id), "household_id": str(household_id)},
        )
    ).fetchall()

    total = 0
    for plan_row in plan_rows:
        try:
            schedule = await debt_plan_service.calculate_schedule(
                session, household_id, plan_row.id
            )
        except HTTPException:
            # Unvollstaendiger Tilgungsplan (z. B. keine Ratephase hinterlegt,
            # Plan 4.5) zaehlt hier als 0 Finanzierungskosten statt den ganzen
            # Bericht abzubrechen; die Luecke bleibt im Vorhaben selbst ueber
            # dessen eigene Warnungen sichtbar.
            continue
        total += (
            schedule.total_interest_minor
            + schedule.total_commitment_interest_minor
            + schedule.total_fees_minor
        )
    return total


async def get_reports_overview(
    session: AsyncSession, household_id: UUID
) -> list[ProjectReportRow]:
    """Vorhaben-Berichtsregister (Plan 11.7): Forecast/Ist/Abweichung und
    Finanzierungskosten je aktivem Vorhaben mit ausgewaehltem Szenario.

    Eigener, vom Analyse-Explorer und vom Cashflow-Istbericht getrennter
    Datenpfad (siehe Modul-Docstring von `analytics_service.py`): eine
    Bausumme ist keine Buchung auf `transaction_splits` und wuerde dort bei
    mehreren Positionen/Zuordnungen zu einer Transaktion Summen vervielfachen.
    Forecast/Ist stammen unveraendert aus `calculate_scenario` - derselben
    Funktion, die auch Kostenplan/Finanzierung der Detailseite fuellt."""
    rows = await _active_projects_with_selected_scenario(session, household_id)

    report_rows: list[ProjectReportRow] = []
    for row in rows:
        if row.selected_scenario_id is None:
            continue
        calc = await calculate_scenario(session, household_id, row.id, row.selected_scenario_id)
        financing_cost_minor = await _financing_cost_for_scenario(
            session, household_id, row.selected_scenario_id
        )
        report_rows.append(
            ProjectReportRow(
                project_id=row.id,
                project_name=row.name,
                project_type=row.project_type,
                scenario_id=row.selected_scenario_id,
                scenario_name=row.selected_scenario_name,
                forecast_cost_minor=calc.project_cash_cost_minor,
                actual_cost_minor=calc.actual_cost_minor,
                variance_minor=calc.project_cash_cost_minor - calc.actual_cost_minor,
                financing_cost_minor=financing_cost_minor,
                warnings=calc.warnings,
            )
        )
    return report_rows


# ---------------------------------------------------------------------------
# Projektabschluss (Plan 7.4)
# ---------------------------------------------------------------------------


async def preview_completion(
    session: AsyncSession, household_id: UUID, project_id: UUID
) -> CompletionPreview:
    project = await require_project(session, household_id, project_id)
    calc = await _selected_scenario_calculation(session, household_id, project_id)

    debt_plan_ids: list[UUID] = []
    if calc is not None:
        source_rows = await session.execute(
            text(
                "select dp.id from debt_plans dp "
                "join project_financing_sources pfs on pfs.id = dp.project_financing_source_id "
                "where pfs.scenario_id = :scenario_id"
            ),
            {"scenario_id": str(calc[0].id)},
        )
        debt_plan_ids = [row.id for row in source_rows]

    value = calc[1].project_cash_cost_minor if calc else 0
    return CompletionPreview(
        project_id=project_id,
        already_applied=project.result_asset_id is not None,
        result_asset_name=project.name,
        result_asset_value_minor=value,
        debt_plans_to_activate=debt_plan_ids,
    )


async def _selected_scenario_calculation(
    session: AsyncSession, household_id: UUID, project_id: UUID
) -> tuple[ProjectScenario, ScenarioCalculation] | None:
    row = (
        await session.execute(
            text(
                f"select {_SCENARIO_COLUMNS} from project_scenarios "
                "where project_id = :project_id and household_id = :household_id "
                "and status = 'selected'"
            ),
            {"project_id": str(project_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return None
    scenario = _row_to_scenario(row)
    calc = await calculate_scenario(session, household_id, project_id, scenario.id)
    return scenario, calc


async def apply_completion(
    session: AsyncSession, household_id: UUID, project_id: UUID, user_id: UUID
) -> CompletionApplyResponse:
    project = await require_project(session, household_id, project_id)
    if project.result_asset_id is not None:
        activated = await session.execute(
            text(
                "select dp.id from debt_plans dp where dp.asset_debt_id is not null "
                "and dp.project_financing_source_id in ("
                "  select id from project_financing_sources where scenario_id in ("
                "    select id from project_scenarios where project_id = :project_id))"
            ),
            {"project_id": str(project_id)},
        )
        return CompletionApplyResponse(
            project_id=project_id,
            result_asset_id=project.result_asset_id,
            activated_debt_plan_ids=[row.id for row in activated],
            already_applied=True,
        )

    calc = await _selected_scenario_calculation(session, household_id, project_id)
    if calc is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no_selected_scenario")
    scenario, calculation = calc

    asset_row = (
        await session.execute(
            text(
                "insert into assets_and_debts "
                "(household_id, name, type, asset_class, current_value_minor) "
                "values (:household_id, :name, 'asset', 'other', :value) returning id"
            ),
            {
                "household_id": str(household_id),
                "name": project.name,
                "value": calculation.project_cash_cost_minor,
            },
        )
    ).first()
    assert asset_row is not None
    asset_id = asset_row.id

    await session.execute(
        text("update major_projects set result_asset_id = :asset_id where id = :id"),
        {"asset_id": str(asset_id), "id": str(project_id)},
    )

    source_rows = await session.execute(
        text(
            "select dp.id from debt_plans dp "
            "join project_financing_sources pfs on pfs.id = dp.project_financing_source_id "
            "where pfs.scenario_id = :scenario_id and dp.status <> 'active'"
        ),
        {"scenario_id": str(scenario.id)},
    )
    activated_ids = [row.id for row in source_rows]
    for plan_id in activated_ids:
        await session.execute(
            text(
                "update debt_plans set status = 'active', asset_debt_id = :asset_id "
                "where id = :id"
            ),
            {"asset_id": str(asset_id), "id": str(plan_id)},
        )

    await record_audit_event(
        session, household_id, user_id, MAJOR_PROJECT_ENTITY_TYPE, project_id, "update",
        metadata={"action": "complete_apply", "result_asset_id": str(asset_id)},
    )

    return CompletionApplyResponse(
        project_id=project_id,
        result_asset_id=asset_id,
        activated_debt_plan_ids=activated_ids,
        already_applied=False,
    )
