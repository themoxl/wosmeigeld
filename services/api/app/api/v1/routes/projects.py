"""Vorhaben-API: Projekte, Szenarien, Kostenabschnitte/-positionen, Finanzierung,
Darlehensverknuepfung, Annahmen, Berechnung, Abschluss, Foerderung und
Ist-Kosten-Zuordnung (Plan 7.1-7.5, 7.2 Ist-Zuordnung). Import/Export
(Plan 10) lebt in einem eigenen Router, siehe routes/project_import.py.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_household_session, require_household_editor
from app.auth.jwt import AuthenticatedUser
from app.core.concurrency import entity_etag, parse_if_match
from app.schemas.funding import (
    AdoptMatchRequest,
    AdoptMatchResponse,
    CombinationEvaluateRequest,
    CombinationEvaluateResponse,
    EligibilityProfile,
    EligibilityProfileInput,
    FundingCheckRunResponse,
)
from app.schemas.projects import (
    BulkAssignActualsRequest,
    BulkAssignActualsResult,
    CashflowAssumptions,
    CashflowAssumptionsInput,
    CashflowSuggestion,
    CompletionApplyResponse,
    CompletionPreview,
    CostActual,
    CostActualCreate,
    CostAiSuggestionApply,
    CostAiSuggestionPreview,
    CostAiSuggestionRequest,
    CostAiSuggestionResult,
    CostDrawPreview,
    CostDrawPreviewInput,
    CostItem,
    CostItemCreate,
    CostItemUpdate,
    CostScheduleEntry,
    CostScheduleReplace,
    CostSection,
    CostSectionCreate,
    CostSectionReorder,
    CostSectionUpdate,
    EquityAvailability,
    FinancingConfigurationCreate,
    FinancingConfigurationResponse,
    FinancingConfigurationUpdate,
    FinancingSource,
    FinancingSourceCreate,
    FinancingSourceUpdate,
    GoalAssumption,
    GoalAssumptionInput,
    MajorProject,
    MajorProjectCreate,
    MajorProjectUpdate,
    ProjectDashboardSummary,
    ProjectDeleteImpact,
    ProjectListItem,
    ProjectReportRow,
    ProjectScenario,
    ProjectScenarioCreate,
    ProjectScenarioUpdate,
    ProjectTransitionRequest,
    RecurringImpact,
    RecurringImpactCreate,
    RecurringImpactUpdate,
    ScenarioCalculation,
    ScenarioCloneRequest,
    ScenarioComparison,
    ScenarioFinancialSummary,
    ScenarioTimeline,
    TransactionCandidate,
)
from app.services import (
    funding_catalog_service,
    project_cost_ai_suggestion_service,
    project_service,
)

router = APIRouter(prefix="/projects", tags=["projects"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[tuple[AsyncSession, UUID], Depends(require_household_editor)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
IfMatchHeader = Annotated[str | None, Header(alias="If-Match")]


@router.get("", response_model=list[ProjectListItem])
async def list_projects(
    ctx: HouseholdSession,
    status: str | None = Query(default=None),
    project_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
) -> list[ProjectListItem]:
    session, household_id = ctx
    return await project_service.list_projects(
        session,
        household_id,
        status_filter=status,
        project_type_filter=project_type,
        search=search,
        include_archived=include_archived,
    )


@router.post("", response_model=MajorProject, status_code=201)
async def create_project(
    payload: MajorProjectCreate, ctx: HouseholdEditorSession, user: CurrentUser, response: Response
) -> MajorProject:
    session, household_id = ctx
    project = await project_service.create_project(session, household_id, user.id, payload)
    response.headers["ETag"] = entity_etag(project.version)
    return project


# Statische Pfade MUESSEN vor "/{project_id}" stehen, sonst faengt die
# dynamische Route "dashboard-summary"/"reports-overview" faelschlich als
# (ungueltige) project_id ab.
@router.get("/dashboard-summary", response_model=ProjectDashboardSummary)
async def get_dashboard_summary(ctx: HouseholdSession) -> ProjectDashboardSummary:
    session, household_id = ctx
    return await project_service.get_dashboard_summary(session, household_id)


@router.get("/reports-overview", response_model=list[ProjectReportRow])
async def get_reports_overview(ctx: HouseholdSession) -> list[ProjectReportRow]:
    session, household_id = ctx
    return await project_service.get_reports_overview(session, household_id)


@router.get("/{project_id}", response_model=MajorProject)
async def get_project(project_id: UUID, ctx: HouseholdSession, response: Response) -> MajorProject:
    session, household_id = ctx
    project = await project_service.require_project(session, household_id, project_id)
    response.headers["ETag"] = entity_etag(project.version)
    return project


@router.patch("/{project_id}", response_model=MajorProject)
async def update_project(
    project_id: UUID,
    payload: MajorProjectUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> MajorProject:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    project = await project_service.update_project(
        session, household_id, project_id, payload, expected_version
    )
    response.headers["ETag"] = entity_etag(project.version)
    return project


@router.post("/{project_id}/transition", response_model=MajorProject)
async def transition_project(
    project_id: UUID,
    payload: ProjectTransitionRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> MajorProject:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    project = await project_service.transition_project(
        session, household_id, project_id, payload.to_status, payload.note,
        expected_version, user.id
    )
    response.headers["ETag"] = entity_etag(project.version)
    return project


@router.get("/{project_id}/delete-impact", response_model=ProjectDeleteImpact)
async def get_delete_impact(project_id: UUID, ctx: HouseholdEditorSession) -> ProjectDeleteImpact:
    session, household_id = ctx
    return await project_service.delete_impact(session, household_id, project_id)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser) -> None:
    session, household_id = ctx
    await project_service.delete_project(session, household_id, project_id, user.id)


@router.post("/{project_id}/archive", response_model=MajorProject)
async def archive_project(
    project_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser, response: Response
) -> MajorProject:
    session, household_id = ctx
    project = await project_service.archive_project(session, household_id, project_id, user.id)
    response.headers["ETag"] = entity_etag(project.version)
    return project


@router.get("/{project_id}/scenarios", response_model=list[ProjectScenario])
async def list_scenarios(project_id: UUID, ctx: HouseholdSession) -> list[ProjectScenario]:
    session, household_id = ctx
    return await project_service.list_scenarios(session, household_id, project_id)


@router.post("/{project_id}/scenarios", response_model=ProjectScenario, status_code=201)
async def create_scenario(
    project_id: UUID,
    payload: ProjectScenarioCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> ProjectScenario:
    session, household_id = ctx
    scenario = await project_service.create_scenario(
        session, household_id, project_id, user.id, payload
    )
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


@router.patch("/{project_id}/scenarios/{scenario_id}", response_model=ProjectScenario)
async def update_scenario(
    project_id: UUID,
    scenario_id: UUID,
    payload: ProjectScenarioUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> ProjectScenario:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    scenario = await project_service.update_scenario(
        session, household_id, project_id, scenario_id, payload, expected_version
    )
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


@router.delete("/{project_id}/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> None:
    session, household_id = ctx
    await project_service.delete_scenario(session, household_id, project_id, scenario_id, user.id)


@router.post(
    "/{project_id}/scenarios/{scenario_id}/clone",
    response_model=ProjectScenario,
    status_code=201,
)
async def clone_scenario(
    project_id: UUID,
    scenario_id: UUID,
    payload: ScenarioCloneRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> ProjectScenario:
    session, household_id = ctx
    scenario = await project_service.clone_scenario(
        session, household_id, project_id, scenario_id, user.id, payload
    )
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


@router.post("/{project_id}/scenarios/{scenario_id}/select", response_model=ProjectScenario)
async def select_scenario(
    project_id: UUID,
    scenario_id: UUID,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> ProjectScenario:
    session, household_id = ctx
    scenario = await project_service.select_scenario(
        session, household_id, project_id, scenario_id, user.id
    )
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


@router.get("/{project_id}/comparison", response_model=ScenarioComparison)
async def compare_scenarios(
    project_id: UUID, ctx: HouseholdSession, scenario_id: Annotated[list[UUID], Query()]
) -> ScenarioComparison:
    session, household_id = ctx
    return await project_service.compare_scenarios(session, household_id, project_id, scenario_id)


@router.post("/{project_id}/scenarios/{scenario_id}/calculate", response_model=ScenarioCalculation)
async def calculate_scenario(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> ScenarioCalculation:
    session, household_id = ctx
    return await project_service.calculate_scenario(session, household_id, project_id, scenario_id)


# ---------------------------------------------------------------------------
# Kostenabschnitte
# ---------------------------------------------------------------------------


@router.get("/{project_id}/scenarios/{scenario_id}/cost-sections", response_model=list[CostSection])
async def list_cost_sections(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> list[CostSection]:
    session, household_id = ctx
    return await project_service.list_cost_sections(session, household_id, scenario_id)


@router.post(
    "/{project_id}/scenarios/{scenario_id}/cost-sections",
    response_model=CostSection,
    status_code=201,
)
async def create_cost_section(
    project_id: UUID,
    scenario_id: UUID,
    payload: CostSectionCreate,
    ctx: HouseholdEditorSession,
    response: Response,
) -> CostSection:
    session, household_id = ctx
    section = await project_service.create_cost_section(session, household_id, scenario_id, payload)
    response.headers["ETag"] = entity_etag(section.version)
    return section


@router.patch("/{project_id}/cost-sections/{section_id}", response_model=CostSection)
async def update_cost_section(
    project_id: UUID,
    section_id: UUID,
    payload: CostSectionUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CostSection:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    section = await project_service.update_cost_section(
        session, household_id, section_id, payload, expected_version
    )
    response.headers["ETag"] = entity_etag(section.version)
    return section


@router.delete("/{project_id}/cost-sections/{section_id}", status_code=204)
async def delete_cost_section(
    project_id: UUID, section_id: UUID, ctx: HouseholdEditorSession
) -> None:
    session, household_id = ctx
    await project_service.delete_cost_section(session, household_id, section_id)


@router.post(
    "/{project_id}/scenarios/{scenario_id}/cost-sections/reorder", response_model=list[CostSection]
)
async def reorder_cost_sections(
    project_id: UUID, scenario_id: UUID, payload: CostSectionReorder, ctx: HouseholdEditorSession
) -> list[CostSection]:
    session, household_id = ctx
    return await project_service.reorder_cost_sections(
        session, household_id, scenario_id, payload.ordered_section_ids
    )


# ---------------------------------------------------------------------------
# Kostenpositionen
# ---------------------------------------------------------------------------


@router.get("/{project_id}/scenarios/{scenario_id}/cost-items", response_model=list[CostItem])
async def list_cost_items(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> list[CostItem]:
    session, household_id = ctx
    return await project_service.list_cost_items(session, household_id, scenario_id)


@router.post(
    "/{project_id}/scenarios/{scenario_id}/cost-items",
    response_model=CostItem,
    status_code=201,
)
async def create_cost_item(
    project_id: UUID,
    scenario_id: UUID,
    payload: CostItemCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> CostItem:
    session, household_id = ctx
    item = await project_service.create_cost_item(
        session, household_id, scenario_id, user.id, payload
    )
    response.headers["ETag"] = entity_etag(item.version)
    return item


@router.patch("/{project_id}/cost-items/{item_id}", response_model=CostItem)
async def update_cost_item(
    project_id: UUID,
    item_id: UUID,
    payload: CostItemUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CostItem:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    item = await project_service.update_cost_item(
        session, household_id, item_id, payload, expected_version
    )
    response.headers["ETag"] = entity_etag(item.version)
    return item


@router.delete("/{project_id}/cost-items/{item_id}", status_code=204)
async def delete_cost_item(
    project_id: UUID, item_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> None:
    session, household_id = ctx
    await project_service.delete_cost_item(session, household_id, item_id, user.id)


@router.put("/{project_id}/cost-items/{item_id}/schedule", response_model=list[CostScheduleEntry])
async def replace_cost_schedule(
    project_id: UUID, item_id: UUID, payload: CostScheduleReplace, ctx: HouseholdEditorSession
) -> list[CostScheduleEntry]:
    session, household_id = ctx
    return await project_service.replace_cost_schedule(
        session, household_id, item_id, payload.entries
    )


@router.post(
    "/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
    response_model=CostAiSuggestionPreview,
)
async def preview_cost_ai_suggestions(
    project_id: UUID,
    scenario_id: UUID,
    payload: CostAiSuggestionRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> CostAiSuggestionPreview:
    """Laesst das Modell aus einer Freitextbeschreibung Schaetzwerte fuer die
    bestehenden Kostenpositionen des Szenarios vorschlagen. Ohne erreichbares
    Modell antwortet der Dienst mit 503 statt mit einer leeren Vorschlagsliste
    (dieselbe Begruendung wie bei Kategorienvorschlaegen)."""
    session, household_id = ctx
    return await project_cost_ai_suggestion_service.build_preview(
        session, household_id, project_id, scenario_id, user.id, payload
    )


@router.post(
    "/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/apply",
    response_model=CostAiSuggestionResult,
)
async def apply_cost_ai_suggestions(
    project_id: UUID,
    scenario_id: UUID,
    payload: CostAiSuggestionApply,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> CostAiSuggestionResult:
    """Uebernimmt nur die vom Nutzer bestaetigten Positionen aus einer
    Vorschau. Nicht bestaetigte Vorschlaege bleiben folgenlos."""
    session, household_id = ctx
    return await project_cost_ai_suggestion_service.apply(
        session, household_id, project_id, scenario_id, user.id, payload
    )


@router.get("/{project_id}/cost-items/{item_id}/actuals", response_model=list[CostActual])
async def list_actuals(
    project_id: UUID, item_id: UUID, ctx: HouseholdSession
) -> list[CostActual]:
    session, household_id = ctx
    return await project_service.list_actuals_for_item(session, household_id, item_id)


@router.post(
    "/{project_id}/cost-items/{item_id}/actuals", response_model=CostActual, status_code=201
)
async def create_actual(
    project_id: UUID,
    item_id: UUID,
    payload: CostActualCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> CostActual:
    session, household_id = ctx
    return await project_service.create_actual(session, household_id, item_id, user.id, payload)


@router.delete("/{project_id}/cost-actuals/{actual_id}", status_code=204)
async def delete_actual(
    project_id: UUID, actual_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> None:
    session, household_id = ctx
    await project_service.delete_actual(session, household_id, actual_id, user.id)


@router.get(
    "/{project_id}/transaction-candidates", response_model=list[TransactionCandidate]
)
async def list_transaction_candidates(
    project_id: UUID,
    ctx: HouseholdSession,
    search: str | None = Query(default=None),
    account_id: UUID | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TransactionCandidate]:
    session, household_id = ctx
    await project_service.require_project(session, household_id, project_id)
    return await project_service.list_transaction_candidates(
        session,
        household_id,
        search=search,
        account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.post("/{project_id}/actuals/bulk-assign", response_model=BulkAssignActualsResult)
async def bulk_assign_actuals(
    project_id: UUID,
    payload: BulkAssignActualsRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> BulkAssignActualsResult:
    session, household_id = ctx
    return await project_service.bulk_assign_actuals(
        session, household_id, project_id, user.id, payload
    )


# ---------------------------------------------------------------------------
# Finanzierung und Darlehen (Plan 7.3)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/scenarios/{scenario_id}/financing-sources", response_model=list[FinancingSource]
)
async def list_financing_sources(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> list[FinancingSource]:
    session, household_id = ctx
    return await project_service.list_financing_sources(session, household_id, scenario_id)


@router.post(
    "/{project_id}/scenarios/{scenario_id}/financing-draw-preview",
    response_model=CostDrawPreview,
)
async def preview_financing_draws(
    project_id: UUID,
    scenario_id: UUID,
    payload: CostDrawPreviewInput,
    ctx: HouseholdSession,
) -> CostDrawPreview:
    session, household_id = ctx
    await project_service.require_scenario(session, household_id, project_id, scenario_id)
    return await project_service.preview_financing_draws(session, scenario_id, payload)


@router.post(
    "/{project_id}/scenarios/{scenario_id}/financing-sources",
    response_model=FinancingSource,
    status_code=201,
)
async def create_financing_source(
    project_id: UUID,
    scenario_id: UUID,
    payload: FinancingSourceCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> FinancingSource:
    session, household_id = ctx
    source = await project_service.create_financing_source(
        session, household_id, scenario_id, user.id, payload
    )
    response.headers["ETag"] = entity_etag(source.version)
    return source


@router.post(
    "/{project_id}/scenarios/{scenario_id}/financing-configurations",
    response_model=FinancingConfigurationResponse,
    status_code=201,
)
async def create_financing_configuration(
    project_id: UUID,
    scenario_id: UUID,
    payload: FinancingConfigurationCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> FinancingConfigurationResponse:
    session, household_id = ctx
    await project_service.require_scenario(session, household_id, project_id, scenario_id)
    return await project_service.create_financing_configuration(
        session, household_id, scenario_id, user.id, payload
    )


@router.put(
    "/{project_id}/financing-sources/{source_id}/configuration",
    response_model=FinancingConfigurationResponse,
)
async def update_financing_configuration(
    project_id: UUID,
    source_id: UUID,
    payload: FinancingConfigurationUpdate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    if_match: IfMatchHeader = None,
) -> FinancingConfigurationResponse:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    source = await project_service.require_financing_source(session, household_id, source_id)
    scenario = await project_service.require_scenario_by_id(
        session, household_id, source.scenario_id
    )
    if scenario.project_id != project_id:
        raise HTTPException(status_code=404, detail="financing_source_not_found")
    return await project_service.update_financing_configuration(
        session, household_id, source_id, user.id, payload, expected_version
    )


@router.patch("/{project_id}/financing-sources/{source_id}", response_model=FinancingSource)
async def update_financing_source(
    project_id: UUID,
    source_id: UUID,
    payload: FinancingSourceUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> FinancingSource:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    source = await project_service.update_financing_source(
        session, household_id, source_id, payload, expected_version
    )
    response.headers["ETag"] = entity_etag(source.version)
    return source


@router.delete("/{project_id}/financing-sources/{source_id}", status_code=204)
async def delete_financing_source(
    project_id: UUID, source_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> None:
    session, household_id = ctx
    await project_service.delete_financing_source(session, household_id, source_id, user.id)


@router.get("/{project_id}/equity-availability", response_model=EquityAvailability)
async def get_equity_availability(project_id: UUID, ctx: HouseholdSession) -> EquityAvailability:
    session, household_id = ctx
    return await project_service.get_equity_availability(session, household_id, project_id)


# ---------------------------------------------------------------------------
# Annahmen, Berechnung und Abschluss (Plan 7.4)
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/scenarios/{scenario_id}/assumptions/suggest", response_model=CashflowSuggestion
)
async def suggest_cashflow_assumptions(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> CashflowSuggestion:
    session, household_id = ctx
    return await project_service.suggest_cashflow_assumptions(session, household_id, project_id)


@router.get(
    "/{project_id}/scenarios/{scenario_id}/assumptions",
    response_model=CashflowAssumptions | None,
)
async def get_cashflow_assumptions(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> CashflowAssumptions | None:
    """Gespeicherte Haushaltsannahmen des Szenarios, `null` wenn noch keine.

    Ohne diesen Lesezugriff gab es zu PUT und /suggest keine Entsprechung: die
    Werte lagen in der Datenbank, das Formular startete aber jedes Mal leer,
    weil es keinen Weg hatte, sie zu holen. Gespeicherte Annahmen sahen damit
    aus wie nie eingetragene.
    """
    session, household_id = ctx
    return await project_service.get_cashflow_assumptions(session, household_id, scenario_id)


@router.put("/{project_id}/scenarios/{scenario_id}/assumptions", response_model=CashflowAssumptions)
async def save_cashflow_assumptions(
    project_id: UUID,
    scenario_id: UUID,
    payload: CashflowAssumptionsInput,
    ctx: HouseholdEditorSession,
) -> CashflowAssumptions:
    session, household_id = ctx
    return await project_service.save_cashflow_assumptions(
        session, household_id, scenario_id, payload
    )


@router.get(
    "/{project_id}/scenarios/{scenario_id}/recurring-impacts", response_model=list[RecurringImpact]
)
async def list_recurring_impacts(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> list[RecurringImpact]:
    session, household_id = ctx
    return await project_service.list_recurring_impacts(session, household_id, scenario_id)


@router.post(
    "/{project_id}/scenarios/{scenario_id}/recurring-impacts",
    response_model=RecurringImpact,
    status_code=201,
)
async def create_recurring_impact(
    project_id: UUID,
    scenario_id: UUID,
    payload: RecurringImpactCreate,
    ctx: HouseholdEditorSession,
) -> RecurringImpact:
    session, household_id = ctx
    return await project_service.create_recurring_impact(
        session, household_id, scenario_id, payload
    )


@router.patch("/{project_id}/recurring-impacts/{impact_id}", response_model=RecurringImpact)
async def update_recurring_impact(
    project_id: UUID,
    impact_id: UUID,
    payload: RecurringImpactUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> RecurringImpact:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    impact = await project_service.update_recurring_impact(
        session, household_id, impact_id, payload, expected_version
    )
    response.headers["ETag"] = entity_etag(impact.version)
    return impact


@router.delete("/{project_id}/recurring-impacts/{impact_id}", status_code=204)
async def delete_recurring_impact(
    project_id: UUID, impact_id: UUID, ctx: HouseholdEditorSession
) -> None:
    session, household_id = ctx
    await project_service.delete_recurring_impact(session, household_id, impact_id)


@router.get(
    "/{project_id}/scenarios/{scenario_id}/goal-assumptions", response_model=list[GoalAssumption]
)
async def list_goal_assumptions(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> list[GoalAssumption]:
    session, household_id = ctx
    return await project_service.list_goal_assumptions(session, household_id, scenario_id)


@router.put("/{project_id}/scenarios/{scenario_id}/goal-assumptions", response_model=GoalAssumption)
async def save_goal_assumption(
    project_id: UUID,
    scenario_id: UUID,
    payload: GoalAssumptionInput,
    ctx: HouseholdEditorSession,
) -> GoalAssumption:
    session, household_id = ctx
    return await project_service.save_goal_assumption(session, household_id, scenario_id, payload)


@router.delete("/{project_id}/goal-assumptions/{goal_assumption_id}", status_code=204)
async def delete_goal_assumption(
    project_id: UUID, goal_assumption_id: UUID, ctx: HouseholdEditorSession
) -> None:
    session, household_id = ctx
    await project_service.delete_goal_assumption(session, household_id, goal_assumption_id)


@router.get(
    "/{project_id}/scenarios/{scenario_id}/financial-summary",
    response_model=ScenarioFinancialSummary,
)
async def get_financial_summary(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> ScenarioFinancialSummary:
    session, household_id = ctx
    return await project_service.calculate_financial_summary(
        session, household_id, project_id, scenario_id
    )


@router.get("/{project_id}/scenarios/{scenario_id}/timeline", response_model=ScenarioTimeline)
async def get_timeline(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> ScenarioTimeline:
    session, household_id = ctx
    return await project_service.get_timeline(session, household_id, project_id, scenario_id)


@router.post("/{project_id}/complete/preview", response_model=CompletionPreview)
async def preview_completion(project_id: UUID, ctx: HouseholdSession) -> CompletionPreview:
    session, household_id = ctx
    return await project_service.preview_completion(session, household_id, project_id)


@router.post("/{project_id}/complete/apply", response_model=CompletionApplyResponse)
async def apply_completion(
    project_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> CompletionApplyResponse:
    session, household_id = ctx
    return await project_service.apply_completion(session, household_id, project_id, user.id)


# ---------------------------------------------------------------------------
# Foerderung (Plan 7.5)
# ---------------------------------------------------------------------------


@router.get("/{project_id}/eligibility-profile", response_model=EligibilityProfile | None)
async def get_eligibility_profile(
    project_id: UUID, ctx: HouseholdSession
) -> EligibilityProfile | None:
    session, household_id = ctx
    return await funding_catalog_service.get_eligibility_profile(session, household_id, project_id)


@router.put("/{project_id}/eligibility-profile", response_model=EligibilityProfile)
async def save_eligibility_profile(
    project_id: UUID, payload: EligibilityProfileInput, ctx: HouseholdEditorSession
) -> EligibilityProfile:
    session, household_id = ctx
    return await funding_catalog_service.save_eligibility_profile(
        session, household_id, project_id, payload
    )


@router.post(
    "/{project_id}/scenarios/{scenario_id}/funding-check", response_model=FundingCheckRunResponse
)
async def run_funding_check(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> FundingCheckRunResponse:
    session, household_id = ctx
    return await funding_catalog_service.run_funding_check(
        session, household_id, project_id, scenario_id, user.id
    )


@router.get(
    "/{project_id}/scenarios/{scenario_id}/funding-matches",
    response_model=FundingCheckRunResponse | None,
)
async def get_funding_matches(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> FundingCheckRunResponse | None:
    session, household_id = ctx
    return await funding_catalog_service.get_latest_funding_check(
        session, household_id, scenario_id
    )


@router.post(
    "/{project_id}/scenarios/{scenario_id}/funding-combinations/evaluate",
    response_model=CombinationEvaluateResponse,
)
async def evaluate_funding_combinations(
    project_id: UUID,
    scenario_id: UUID,
    payload: CombinationEvaluateRequest,
    ctx: HouseholdSession,
) -> CombinationEvaluateResponse:
    session, household_id = ctx
    return await funding_catalog_service.evaluate_match_combinations(
        session, household_id, payload.match_ids
    )


@router.post("/{project_id}/funding-matches/{match_id}/adopt", response_model=AdoptMatchResponse)
async def adopt_funding_match(
    project_id: UUID,
    match_id: UUID,
    payload: AdoptMatchRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> AdoptMatchResponse:
    session, household_id = ctx
    return await funding_catalog_service.adopt_match(
        session, household_id, project_id, match_id, user.id, payload
    )
