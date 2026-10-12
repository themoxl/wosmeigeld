"""Vertragsverwaltung (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Phase 1).

Loest `/v1/recurring` und `/v1/subscriptions` ab. Beide Altpfade bleiben als
lesende Kompatibilitaetsschicht in `routes/rules.py` bestehen, damit
ausgelieferte Mobile-Versionen weiterlaufen.

Nebenlaeufigkeit wie in der Kategorienverwaltung: `PATCH` verlangt `If-Match`
(428 ohne Header, 409 bei veraltetem Stand) und liefert die neue Version als
`ETag`. Der Statuswechsel ueber `/status` ist eine Einfeldaktion aus dem
Zeilenmenue und akzeptiert `If-Match` optional.
"""

from datetime import date, timedelta
from typing import Annotated, Any, Final, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.core.concurrency import entity_etag, parse_if_match, raise_version_conflict
from app.schemas.contract import (
    CashflowGranularity,
    Contract,
    ContractAICrosscheckRequest,
    ContractAICrosscheckResponse,
    ContractAITransactionCheckRequest,
    ContractAITransactionCheckResponse,
    ContractAmountCreate,
    ContractAmountEntry,
    ContractAmountEvidenceResponse,
    ContractAmountRecalculationResult,
    ContractAssignmentPreview,
    ContractAssignmentPreviewRequest,
    ContractCashflowResponse,
    ContractCostTargetInput,
    ContractCostTargetView,
    ContractCreate,
    ContractDuplicateCandidate,
    ContractDuplicateCheck,
    ContractEvent,
    ContractFreeTransactionsResponse,
    ContractIncomeSummary,
    ContractMergeApply,
    ContractMergePreview,
    ContractMergeRequest,
    ContractMergeResult,
    ContractMergeUndoPreview,
    ContractMergeUndoResult,
    ContractOccurrence,
    ContractProviderSuggestion,
    ContractReassignPreview,
    ContractReassignRequest,
    ContractReassignResult,
    ContractRescanResult,
    ContractScenario,
    ContractScenarioCreate,
    ContractScenarioUpdate,
    ContractSettings,
    ContractSettingsUpdate,
    ContractStatusChange,
    ContractSummary,
    ContractUpcoming,
    ContractUpdate,
)
from app.schemas.review import ContractDuplicateSuggestionsResponse
from app.services import (
    category_service,
    contract_ai_crosscheck_service,
    contract_matching_service,
    contract_service,
    contract_target_service,
    person_service,
    rules_service,
)
from app.services.audit_service import record_audit_event
from app.services.fixed_cost_reserve_service import is_reserve_eligible

router = APIRouter(prefix="/contracts", tags=["contracts"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
IfMatchHeader = Annotated[str | None, Header(alias="If-Match")]

CONTRACT_EVENT_NOT_FOUND: Final = "contract_event_not_found"

# Obergrenze beim Vorziehen der Faelligkeit eines wieder aufgenommenen
# Vertrags. Gleicher Wert und gleicher Grund wie in
# contract_service.build_forecast und routes/review.py: ein sehr alter
# Wochenvertrag darf die Schleife nicht sprengen.
_MAX_RESUME_PERIODS: Final = 240

# Feste Spaltenliste statt der Payload-Schluessel, damit kein Feldname ungeprueft
# in ein SET-Fragment wandert (gleiches Muster wie routes/categories.py).
_UPDATABLE_COLUMNS: Final = (
    "name",
    "provider_label",
    "contract_type",
    "payee_id",
    "account_id",
    "category_id",
    "contract_key",
    "contract_key_source",
    "direction",
    "amount_minor",
    "frequency",
    "due_rule",
    "due_day",
    "next_due_date",
    "tolerance_days",
    "tolerance_amount_minor",
    "status",
    "cancelled_on",
    "ends_on",
    "min_term_months",
    "cancellation_period_days",
    "starts_on",
    "renewal_term_months",
    "provider_template_key",
    "installments_total",
    "reserve_enabled",
    "reserve_started_on",
    "reserve_starting_amount_minor",
    "note",
    "income_kind",
    "person_id",
)

_UUID_COLUMNS: Final = frozenset({"payee_id", "account_id", "category_id", "person_id"})

# Eine Einnahmeart an einer Ausgabe ist widerspruechlich - das Schema lehnt sie
# ueber contracts_income_kind_direction_chk ab. Der Client soll dafuer eine
# erklaerte 400 bekommen und keinen abgebrochenen Request.
CONTRACT_INCOME_KIND_ON_EXPENSE: Final = "contract_income_kind_requires_income"

# Statuswechsel im Audit-Log auf die bereits erlaubten Aktionen abbilden; das
# spart eine Migration an audit_log.action.
_STATUS_AUDIT_ACTION: Final[dict[str, str]] = {
    "ended": "archive",
    "not_a_contract": "archive",
    "active": "restore",
}


def _assert_version(current_version: int, expected_version: int | None) -> None:
    if expected_version is not None and current_version != expected_version:
        raise_version_conflict(current_version)


def _placeholder(column: str) -> str:
    return f"cast(:{column} as uuid)" if column in _UUID_COLUMNS else f":{column}"


def _param_value(column: str, value: Any) -> Any:
    if column in _UUID_COLUMNS and value is not None:
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Auflisten und Anlegen
# ---------------------------------------------------------------------------


@router.get("", response_model=list[Contract])
async def list_contracts(
    ctx: HouseholdSession,
    contract_status: Annotated[str | None, Query(alias="status")] = None,
    contract_type: str | None = None,
    direction: str | None = None,
    q: Annotated[str | None, Query(max_length=160)] = None,
    include_ended: bool = False,
) -> list[Contract]:
    """Standardmaessig ohne beendete und verneinte Vertraege - sonst waechst die
    Liste mit jeder Fehlerkennung dauerhaft an."""
    session, household_id = ctx
    if contract_status:
        statuses = [contract_status]
    elif include_ended:
        statuses = None
    else:
        statuses = ["active", "paused", "cancelled"]

    return await contract_service.list_contracts(
        session,
        household_id,
        statuses=statuses,
        contract_type=contract_type,
        direction=direction,
        search=q,
    )


@router.post("", response_model=Contract, status_code=status.HTTP_201_CREATED)
async def create_contract(
    payload: ContractCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> Contract:
    session, household_id = ctx
    category_id = await category_service.resolve_category_id(
        session, household_id, payload.category_id
    )
    await contract_service.assert_provider_template(session, payload.provider_template_key)
    if payload.person_id is not None:
        # Der Fremdschluessel auf household_people ist "initially deferred" und
        # schlaege erst beim COMMIT fehl - also nach der Antwort und damit als
        # 500. Die Pruefung holt den Fehler in den Request zurueck.
        await person_service.assert_assignable(session, household_id, payload.person_id)
    reserve_eligible = is_reserve_eligible(payload.direction, payload.frequency)
    reserve_enabled = (
        payload.reserve_enabled if payload.reserve_enabled is not None else reserve_eligible
    ) and reserve_eligible
    reserve_started_on = payload.reserve_started_on or date.today()

    result = await session.execute(
        text(
            "insert into contracts (household_id, name, provider_label, contract_type, "
            "payee_id, account_id, category_id, contract_key, contract_key_source, "
            "direction, amount_minor, frequency, due_rule, due_day, next_due_date, "
            "tolerance_days, tolerance_amount_minor, status, cancelled_on, ends_on, "
            "min_term_months, cancellation_period_days, starts_on, renewal_term_months, "
            "provider_template_key, installments_total, source, note, reserve_enabled, "
            "reserve_started_on, reserve_starting_amount_minor, income_kind, person_id) "
            "values (:household_id, :name, :provider_label, :contract_type, "
            "cast(:payee_id as uuid), cast(:account_id as uuid), cast(:category_id as uuid), "
            ":contract_key, :contract_key_source, :direction, :amount_minor, :frequency, "
            ":due_rule, :due_day, :next_due_date, :tolerance_days, :tolerance_amount_minor, "
            ":status, :cancelled_on, :ends_on, :min_term_months, :cancellation_period_days, "
            ":starts_on, :renewal_term_months, :provider_template_key, "
            ":installments_total, 'manual', :note, :reserve_enabled, :reserve_started_on, "
            ":reserve_starting_amount_minor, :income_kind, cast(:person_id as uuid)) "
            "returning id"
        ),
        {
            "household_id": str(household_id),
            "name": payload.name,
            "provider_label": payload.provider_label,
            "contract_type": payload.contract_type,
            "payee_id": str(payload.payee_id) if payload.payee_id else None,
            "account_id": str(payload.account_id) if payload.account_id else None,
            "category_id": str(category_id) if category_id else None,
            "contract_key": payload.contract_key,
            "contract_key_source": payload.contract_key_source,
            "direction": payload.direction,
            "amount_minor": payload.amount_minor,
            "frequency": payload.frequency,
            "due_rule": payload.due_rule,
            # Ohne festen Zieltag wandert eine Faelligkeit am 31. ueber den
            # Februar dauerhaft nach vorn - deshalb aus dem Startdatum ableiten.
            "due_day": payload.due_day or payload.next_due_date.day,
            "next_due_date": payload.next_due_date,
            "tolerance_days": payload.tolerance_days,
            "tolerance_amount_minor": payload.tolerance_amount_minor,
            "status": payload.status,
            "cancelled_on": payload.cancelled_on,
            "ends_on": payload.ends_on,
            "min_term_months": payload.min_term_months,
            "cancellation_period_days": payload.cancellation_period_days,
            "starts_on": payload.starts_on,
            "renewal_term_months": payload.renewal_term_months,
            "provider_template_key": payload.provider_template_key,
            "installments_total": payload.installments_total,
            "note": payload.note,
            "reserve_enabled": reserve_enabled,
            "reserve_started_on": reserve_started_on,
            "reserve_starting_amount_minor": (
                payload.reserve_starting_amount_minor if reserve_eligible else 0
            ),
            "income_kind": payload.income_kind,
            "person_id": str(payload.person_id) if payload.person_id else None,
        },
    )
    contract_id = cast(UUID, result.one().id)
    if reserve_eligible:
        await session.execute(
            text(
                "insert into reserve_pots (household_id, kind, contract_id) "
                "values (:household_id, 'contract', :contract_id) on conflict do nothing"
            ),
            {"household_id": str(household_id), "contract_id": str(contract_id)},
        )

    await contract_service.open_amount_stage(
        session,
        household_id,
        contract_id,
        amount_minor=payload.amount_minor,
        # Der Preis gilt ab heute, auch wenn die erste Faelligkeit noch
        # bevorsteht - sonst haette der Vertrag bis dahin keinen gueltigen Betrag.
        valid_from=min(date.today(), payload.next_due_date),
        change_reason="initial",
    )
    await contract_service.record_event(session, household_id, contract_id, "created")

    # Rueckwirkender Erstlauf: die Zahlungen dieses Vertrags stehen laengst im
    # Konto, nur kannte sie bisher niemand als Vertrag. Ohne den Backfill zeigt
    # die Detailseite direkt nach dem Anlegen eine leere Historie und der Nutzer
    # haelt das Anlegen fuer gescheitert. Der Lauf schreibt bewusst weder
    # Ereignisse noch Fehlzaehler (contract_matching_service.backfill_contract),
    # ein neuer Vertrag bringt also keinen erfundenen Alarmverlauf mit.
    # Beendete und verneinte Vertraege bleiben aussen vor - fuer sie waere eine
    # nachtraeglich erzeugte Faelligkeitsreihe nur Ballast.
    if payload.status not in ("ended", "not_a_contract"):
        await contract_matching_service.backfill_contract(
            session, household_id, contract_id, today=date.today()
        )

    # Erst nach dem Backfill lesen: er setzt first_seen_date/last_seen_date, und
    # der wertbasierte Versionstrigger zaehlt dabei `version` hoch. Ein vorher
    # gelesenes ETag waere sofort veraltet und der naechste PATCH liefe in 409.
    contract = await contract_service.require_contract(session, household_id, contract_id)
    await record_audit_event(
        session,
        household_id,
        user.id,
        contract_service.CONTRACT_ENTITY_TYPE,
        contract_id,
        "create",
        after=await contract_service.fetch_contract_snapshot(
            session, household_id, contract_id
        ),
    )
    response.headers["ETag"] = entity_etag(contract.version)
    return contract


# ---------------------------------------------------------------------------
# Feste Pfade vor /{contract_id} - Starlette matcht in Deklarationsreihenfolge
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=ContractSummary)
async def get_summary(ctx: HouseholdSession) -> ContractSummary:
    session, household_id = ctx
    return await contract_service.build_summary(
        session, household_id, today=date.today()
    )


@router.get("/income-summary", response_model=ContractIncomeSummary)
async def get_income_summary(
    ctx: HouseholdSession,
    months: Annotated[int, Query(ge=1, le=60)] = 24,
) -> ContractIncomeSummary:
    """Einnahmen je Person und je Art, dazu ihre Entwicklung gegen die
    Fixkosten (Liquiditaetsplan, Stufe 6).

    Kein eigener `/v1/income`-Zweig: eine Einnahmequelle IST ein Vertrag (Plan
    Abschnitt 3). Was fehlte, war eine Sicht - und genau die liefert dieser
    Endpunkt, ohne eine zweite Verwaltungsstrecke daneben aufzumachen.
    """
    session, household_id = ctx
    return await contract_service.build_income_summary(
        session, household_id, today=date.today(), months=months
    )


@router.get("/forecast", response_model=list[ContractUpcoming])
async def get_forecast(
    ctx: HouseholdSession,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> list[ContractUpcoming]:
    session, household_id = ctx
    today = date.today()
    return await contract_service.build_forecast(
        session, household_id, today=today, until=today + timedelta(days=days)
    )


@router.get("/cashflow", response_model=ContractCashflowResponse)
async def get_cashflow(
    ctx: HouseholdSession,
    granularity: Annotated[CashflowGranularity, Query()] = "month",
    periods: Annotated[int, Query(ge=1, le=24)] = 6,
) -> ContractCashflowResponse:
    """Vertrags-Cashflow je Kalendermonat oder -quartal.

    Loest die rollierende Tagesansicht von `/forecast` als Prognosegrundlage ab.
    Dort fiel ein monatlicher Vertrag in "die naechsten 30 Tage" je nach
    Faelligkeitstag ein- oder zweimal; die Summe schwankte mit dem Kalender und
    war weder mit der Fixkostensumme noch mit der Fixkostenquote im Budget
    vergleichbar. `/forecast` bleibt als flache Liste der naechsten
    Faelligkeiten bestehen - dafuer ist ein Tagesfenster das richtige Mittel.
    """
    session, household_id = ctx
    return await contract_service.build_cashflow(
        session,
        household_id,
        today=date.today(),
        granularity=granularity,
        periods=periods,
    )


# ---------------------------------------------------------------------------
# Fixkostenziel und Senkungsszenarien
# ---------------------------------------------------------------------------


@router.get("/cost-target", response_model=ContractCostTargetView)
async def get_cost_target(ctx: HouseholdSession) -> ContractCostTargetView:
    session, household_id = ctx
    return await contract_target_service.get_target(session, household_id, today=date.today())


@router.put("/cost-target", response_model=ContractCostTargetView)
async def put_cost_target(
    payload: ContractCostTargetInput, ctx: HouseholdEditorSession
) -> ContractCostTargetView:
    session, household_id = ctx
    return await contract_target_service.put_target(
        session, household_id, payload, today=date.today()
    )


# ---------------------------------------------------------------------------
# Fixkostenszenarien - ab Stufe 7 des Liquiditaetsplans der SPEZIALFALL
#
# `household_scenarios` (`/v1/scenarios`) ist seit der Uebernahme das
# kanonische Szenariosystem des Haushalts: ein `cancel` ist dort ein
# `expense_end`, ein `reduce` ein `expense_change`. Diese fuenf Endpunkte
# bleiben vollstaendig funktionsfaehig - der Fixkosten-Editor arbeitet weiter
# damit - tragen aber `deprecated=True`, damit die Abloesung in der
# OpenAPI-Beschreibung sichtbar ist. Gleiches Vorbild wie `GET /recurring` und
# `/subscriptions` in routes/rules.py: nichts loeschen, solange etwas darauf
# zeigt (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 11).
# ---------------------------------------------------------------------------


@router.get("/scenarios", response_model=list[ContractScenario], deprecated=True)
async def list_scenarios(ctx: HouseholdSession) -> list[ContractScenario]:
    session, household_id = ctx
    return await contract_target_service.list_scenarios(session, household_id, today=date.today())


@router.post(
    "/scenarios",
    response_model=ContractScenario,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
async def create_scenario(
    payload: ContractScenarioCreate, ctx: HouseholdEditorSession
) -> ContractScenario:
    session, household_id = ctx
    return await contract_target_service.create_scenario(
        session, household_id, payload, today=date.today()
    )


@router.get("/scenarios/{scenario_id}", response_model=ContractScenario, deprecated=True)
async def get_scenario(scenario_id: UUID, ctx: HouseholdSession) -> ContractScenario:
    session, household_id = ctx
    return await contract_target_service.get_scenario(
        session, household_id, scenario_id, today=date.today()
    )


@router.patch("/scenarios/{scenario_id}", response_model=ContractScenario, deprecated=True)
async def update_scenario(
    scenario_id: UUID, payload: ContractScenarioUpdate, ctx: HouseholdEditorSession
) -> ContractScenario:
    """Teilaenderung ohne `If-Match`.

    Anders als beim Vertrag selbst: ein Szenario ist ein Gedankenspiel, das
    genau eine Person zur Zeit bearbeitet, und traegt darum keine `version`.
    Ein verlorenes Update kostet hier eine Haekchenauswahl, keine Buchung.
    """
    session, household_id = ctx
    return await contract_target_service.update_scenario(
        session, household_id, scenario_id, payload, today=date.today()
    )


@router.delete(
    "/scenarios/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    deprecated=True,
)
async def delete_scenario(scenario_id: UUID, ctx: HouseholdEditorSession) -> None:
    session, household_id = ctx
    await contract_target_service.delete_scenario(session, household_id, scenario_id)


@router.get("/provider-suggestions", response_model=list[ContractProviderSuggestion])
async def provider_suggestions(
    ctx: HouseholdSession,
    q: Annotated[str, Query(min_length=1, max_length=160)],
) -> list[ContractProviderSuggestion]:
    """Anbietervorschlaege fuer das Anlegen-Formular.

    Nur lesend und ohne Editorrecht: der Katalog ist global und verraet nichts
    ueber den Haushalt - bis auf die aufgeloeste Kategorie-ID, die jedes
    Haushaltsmitglied ohnehin sehen darf.
    """
    session, household_id = ctx
    return await contract_service.suggest_providers(session, household_id, q)


@router.get("/settings", response_model=ContractSettings)
async def get_settings(ctx: HouseholdSession) -> ContractSettings:
    session, household_id = ctx
    return await contract_service.get_settings(session, household_id)


@router.put("/settings", response_model=ContractSettings)
async def put_settings(
    payload: ContractSettingsUpdate, ctx: HouseholdEditorSession
) -> ContractSettings:
    session, household_id = ctx
    return await contract_service.put_settings(session, household_id, payload)


@router.post("/rescan", response_model=ContractRescanResult)
async def rescan_contracts(
    ctx: HouseholdEditorSession, user: CurrentUser
) -> ContractRescanResult:
    """Stoesst denselben Abgleich an, den der naechtliche Actor faehrt - nur
    synchron im Request und ausschliesslich fuer diesen Haushalt.

    Bewusst kein `.send()`: das hier ist die Notbremse fuer den Fall, dass Redis
    oder der Worker steht. Genau dann waere eine eingereihte Nachricht wertlos,
    und der Nutzer saehe weiter leere Faelligkeiten ohne zu wissen, warum.
    Der Lauf ist idempotent, ein versehentlicher Doppelklick schadet nicht.
    """
    session, household_id = ctx
    today = date.today()
    result = await contract_matching_service.reconcile_household(
        session, household_id, today=today
    )
    # Erst nach dem Abgleich: er schreibt Status und Enddatum fort, und ein
    # gerade beendeter Vertrag soll keine Fristerinnerung mehr bekommen. Kein
    # commit hier - die Transaktion umschliesst den Request.
    term_ending_count = await contract_service.scan_term_deadlines(
        session, household_id, today=today
    )

    # Der Lauf beruehrt alle Vertraege des Haushalts, es gibt also keine einzelne
    # betroffene Entitaet - auditiert wird der Haushalt selbst. Die Spur je
    # Vertrag steht in contract_events, die der Motor ohnehin schreibt. Der
    # naechtliche Lauf hat dagegen keinen Akteur und schreibt kein Audit; nur so
    # ist im Log erkennbar, dass ein Mensch den Abgleich ausgeloest hat.
    await record_audit_event(
        session,
        household_id,
        user.id,
        contract_matching_service.CONTRACT_MATCH_ENTITY_TYPE,
        household_id,
        "update",
        metadata={
            "operation": "rescan",
            "contracts_examined": result.contracts_examined,
            "matched_count": result.matched_count,
            "price_change_count": result.price_change_count,
            "missing_count": result.missing_count,
            "ended_count": result.ended_count,
            "term_ending_count": term_ending_count,
        },
    )

    return ContractRescanResult(
        contracts_examined=result.contracts_examined,
        matched_count=result.matched_count,
        price_change_count=result.price_change_count,
        missing_count=result.missing_count,
        ended_count=result.ended_count,
    )


@router.post("/duplicate-check", response_model=list[ContractDuplicateCandidate])
async def duplicate_check(
    payload: ContractDuplicateCheck, ctx: HouseholdSession
) -> list[ContractDuplicateCandidate]:
    """Vor dem Anlegen aufgerufen. Bewusst kein Block beim Speichern: der
    Nutzer soll den Treffer sehen und selbst entscheiden."""
    session, household_id = ctx
    return await contract_service.find_duplicate_candidates(session, household_id, payload)


@router.post("/merge/preview", response_model=ContractMergePreview)
async def merge_preview(
    payload: ContractMergeRequest, ctx: HouseholdEditorSession
) -> ContractMergePreview:
    session, household_id = ctx
    return await contract_service.build_merge_preview(
        session, household_id, payload.target_id, payload.source_ids
    )


@router.post("/merge", response_model=ContractMergeResult)
async def merge_contracts(
    payload: ContractMergeApply, ctx: HouseholdEditorSession, user: CurrentUser
) -> ContractMergeResult:
    """Fuehrt Quellvertraege in den Zielvertrag ueber.

    Anders als der Kategorien-Merge nur fuer Editoren, nicht owner-only: hier
    verschiebt sich keine historische Summe, und der Vorgang bleibt ueber das
    Ereignis `merged` nachvollziehbar.
    """
    session, household_id = ctx

    if await contract_service.find_merge_by_request_id(
        session, household_id, payload.target_id, payload.request_id
    ):
        contract = await contract_service.require_contract(
            session, household_id, payload.target_id
        )
        return ContractMergeResult(
            contract=contract,
            merged_count=0,
            moved_occurrences=0,
            moved_history_entries=0,
            merge_id=None,
        )

    contract, moved_occurrences, moved_history, merge_id = await contract_service.apply_merge(
        session,
        household_id,
        payload.target_id,
        payload.source_ids,
        payload.request_id,
        actor_user_id=user.id,
    )
    await record_audit_event(
        session,
        household_id,
        user.id,
        contract_service.CONTRACT_ENTITY_TYPE,
        payload.target_id,
        "merge",
        metadata={
            "source_ids": [str(item) for item in payload.source_ids],
            "request_id": payload.request_id,
            "merge_id": str(merge_id),
        },
    )
    return ContractMergeResult(
        contract=contract,
        merged_count=len(payload.source_ids),
        moved_occurrences=moved_occurrences,
        moved_history_entries=moved_history,
        merge_id=merge_id,
    )


@router.get("/merges/{merge_id}/undo-preview", response_model=ContractMergeUndoPreview)
async def merge_undo_preview(merge_id: UUID, ctx: HouseholdSession) -> ContractMergeUndoPreview:
    """Meldet, ob seit dem Merge etwas hinzugekommen ist, das ein stiller
    Rollback verschlucken wuerde (Abschnitt 19.7). Lesend fuer alle
    Haushaltsmitglieder, wie der uebrige Beleg-Zugriff auf Vertraege."""
    session, household_id = ctx
    return await contract_service.build_undo_preview(session, household_id, merge_id)


@router.post("/merges/{merge_id}/undo", response_model=ContractMergeUndoResult)
async def merge_undo(
    merge_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> ContractMergeUndoResult:
    """Macht eine Zusammenfuehrung rueckgaengig - konfliktfrei und idempotent.

    Prueft die Konfliktbedingungen unabhaengig von einer vorherigen
    `undo-preview`-Antwort erneut: die Vorschau kann veraltet sein, bis der
    Nutzer tatsaechlich klickt.
    """
    session, household_id = ctx
    contract, restored_count = await contract_service.apply_undo(
        session, household_id, merge_id, actor_user_id=user.id
    )
    if restored_count:
        await record_audit_event(
            session,
            household_id,
            user.id,
            contract_service.CONTRACT_ENTITY_TYPE,
            contract.id,
            "merge_undo",
            metadata={"merge_id": str(merge_id), "restored_source_count": restored_count},
        )
    return ContractMergeUndoResult(contract=contract, restored_source_count=restored_count)


@router.post(
    "/transactions/{transaction_id}/reassign/preview", response_model=ContractReassignPreview
)
async def reassign_preview(
    transaction_id: UUID, payload: ContractReassignRequest, ctx: HouseholdSession
) -> ContractReassignPreview:
    """Zeigt Quelle, Ziel und Auswirkung, bevor eine Buchung umgehaengt wird
    (Abschnitt 19.7, "Vorschau zeigt Quelle, Ziel, betroffene Buchungen")."""
    session, household_id = ctx
    return await contract_service.build_reassign_preview(
        session, household_id, transaction_id, payload.target_contract_id
    )


@router.post("/transactions/{transaction_id}/reassign", response_model=ContractReassignResult)
async def reassign_transaction(
    transaction_id: UUID,
    payload: ContractReassignRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ContractReassignResult:
    """Ordnet eine einzelne Buchung verbindlich einem Vertrag zu, loest sie
    ausdruecklich von jedem Vertrag (`target_contract_id: null`), oder haengt
    sie um. Der entstehende Override gewinnt gegen Erkennung, Reparaturlauf
    und Rescan, bis eine erneute Nutzeraktion ihn aendert."""
    session, household_id = ctx
    override_id = await contract_service.apply_reassign(
        session, household_id, transaction_id, payload.target_contract_id, actor_user_id=user.id
    )
    await record_audit_event(
        session,
        household_id,
        user.id,
        "transaction",
        transaction_id,
        "reassign",
        metadata={
            "contract_id": str(payload.target_contract_id) if payload.target_contract_id else None,
            "override_id": str(override_id),
        },
    )
    return ContractReassignResult(
        transaction_id=transaction_id,
        contract_id=payload.target_contract_id,
        override_id=override_id,
    )


@router.get("/duplicate-suggestions", response_model=ContractDuplicateSuggestionsResponse)
async def duplicate_suggestions(
    ctx: HouseholdSession, limit: int = Query(default=20, ge=1, le=50)
) -> ContractDuplicateSuggestionsResponse:
    """Duenner Endpunkt ueber die schluesselbewusste Erkennung aus
    `rules_service.get_contract_duplicate_suggestions` (Abschnitt 19.10) - die
    Karte auf der Vertraege-Seite braucht dieselbe Logik wie die Review-Inbox,
    aber als eigene Liste statt als Inbox-Eintrag."""
    session, household_id = ctx
    # Ein Eintrag mehr als das Limit verraet, ob abgeschnitten wurde, ohne
    # eine zweite COUNT-Abfrage zu brauchen.
    items = await rules_service.get_contract_duplicate_suggestions(
        session, household_id, limit=limit + 1
    )
    truncated = len(items) > limit
    return ContractDuplicateSuggestionsResponse(
        items=items[:limit], total=len(items[:limit]), truncated=truncated
    )


@router.post("/ai-crosscheck", response_model=ContractAICrosscheckResponse)
async def ai_crosscheck(
    ctx: HouseholdEditorSession,
    payload: ContractAICrosscheckRequest | None = None,
) -> ContractAICrosscheckResponse:
    """Laesst das lokale Sprachmodell Vertragspaare gegenpruefen (Abschnitt 19.11).

    Gedacht fuer die Restmenge, die deterministisch nicht entscheidbar ist: im
    echten Altbestand fehlen `first_seen_date`, `last_seen_date` und
    `payee_id`, weshalb `GET /duplicate-suggestions` dort gar nichts findet,
    obwohl zwei von drei Zeilen derselbe Vertrag sind.

    **Editorrecht, obwohl nichts geschrieben wird.** Die Antwort wird nicht
    persistiert, es findet kein Merge statt, und lesender Belegzugriff waere
    fuer jedes Haushaltsmitglied erlaubt (wie `duplicate-suggestions`). Der
    Ausschlag gibt die andere Seite: der Aufruf stoesst wie
    `POST /rescan` einen teuren haushaltsweiten Lauf an - hier mehrere
    Modellaufrufe nacheinander im Request. Ein Knopf, den jeder Mitleser
    beliebig oft druecken kann, waere ein Selbstbeschuss; und handeln kann auf
    das Ergebnis ohnehin nur, wer den Merge ausfuehren darf.

    Kein Audit-Eintrag: anders als beim Rescan aendert dieser Lauf keinen
    Zustand, ueber den spaeter jemand Rechenschaft braucht.
    """
    session, household_id = ctx
    return await contract_ai_crosscheck_service.crosscheck_household(
        session,
        household_id,
        contract_ids=payload.contract_ids if payload is not None else None,
    )


@router.post("/events/{event_id}/ack", status_code=status.HTTP_204_NO_CONTENT)
async def acknowledge_event(event_id: UUID, ctx: HouseholdEditorSession) -> None:
    session, household_id = ctx
    result = await session.execute(
        text(
            "update contract_events set acknowledged_at = now() "
            "where id = :id and household_id = :household_id and acknowledged_at is null "
            "returning id"
        ),
        {"id": str(event_id), "household_id": str(household_id)},
    )
    if result.first() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CONTRACT_EVENT_NOT_FOUND
        )


# ---------------------------------------------------------------------------
# Einzelvertrag
# ---------------------------------------------------------------------------


@router.get("/{contract_id}", response_model=Contract)
async def get_contract(
    contract_id: UUID, ctx: HouseholdSession, response: Response
) -> Contract:
    session, household_id = ctx
    contract = await contract_service.require_contract(session, household_id, contract_id)
    response.headers["ETag"] = entity_etag(contract.version)
    return contract


@router.patch("/{contract_id}", response_model=Contract)
async def update_contract(
    contract_id: UUID,
    payload: ContractUpdate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> Contract:
    """Aendert den Vertrag. Ein Betragswechsel oeffnet zugleich eine neue Stufe
    im Betragsverlauf - sonst wuerde die Anzeige einen Verlauf behaupten, den
    die Historie nicht kennt."""
    session, household_id = ctx
    expected_version = parse_if_match(if_match)

    before = await contract_service.require_contract(session, household_id, contract_id)
    before_snapshot = await contract_service.fetch_contract_snapshot(
        session, household_id, contract_id
    )
    _assert_version(before.version, expected_version)

    changes = payload.model_dump(exclude_unset=True)
    changes.pop("clear_fields", None)
    if changes.get("name") is None:
        changes.pop("name", None)
    # clear_fields gewinnt gegen einen gleichzeitig gesetzten Wert: der
    # ausdrueckliche Loeschwunsch ist die spezifischere Angabe.
    for cleared_column in payload.clear_fields:
        changes[cleared_column] = None

    # Dieselbe Ableitung wie beim Anlegen (siehe create_contract): das
    # Bearbeiten-Formular schickt ausschliesslich `next_due_date`, nie
    # `due_day`. Ohne diese Zeile rechnete der Abgleichsmotor mit dem alten
    # Zieltag weiter (contract_matching_service._advance) und machte die
    # Korrektur des Nutzers mit der ersten Fortschreibung wieder rueckgaengig.
    # Ein ausdruecklich mitgeschicktes oder geleertes `due_day` gewinnt.
    if changes.get("next_due_date") is not None and "due_day" not in changes:
        changes["due_day"] = cast(date, changes["next_due_date"]).day

    resulting_direction = str(changes.get("direction", before.direction))
    resulting_frequency = str(changes.get("frequency", before.frequency))

    if resulting_direction != "income":
        if changes.get("income_kind") is not None:
            # Ausdruecklich mitgeschickte Art an einer Ausgabe: das ist ein
            # Widerspruch in der Eingabe und wird nicht stillschweigend
            # zurechtgebogen.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=CONTRACT_INCOME_KIND_ON_EXPENSE,
            )
        if before.income_kind is not None:
            # Wechsel von Einnahme auf Ausgabe: die alte Art muss mit, sonst
            # verletzt das UPDATE contracts_income_kind_direction_chk und der
            # Nutzer bekaeme fuer eine harmlose Richtungsaenderung einen 500er.
            changes["income_kind"] = None

    if changes.get("person_id") is not None:
        await person_service.assert_assignable(
            session, household_id, cast(UUID, changes["person_id"])
        )

    if not is_reserve_eligible(resulting_direction, resulting_frequency):
        # A changed income/monthly/weekly contract must never leak into reserve
        # planning, even if a stale client still sends the old toggle value.
        changes["reserve_enabled"] = False
        changes["reserve_starting_amount_minor"] = 0
    elif changes.get("reserve_enabled") is True and not changes.get("reserve_started_on"):
        changes["reserve_started_on"] = before.reserve_started_on or date.today()

    if "category_id" in changes and changes["category_id"] is not None:
        changes["category_id"] = await category_service.resolve_category_id(
            session, household_id, cast(UUID, changes["category_id"])
        )

    if changes.get("provider_template_key") is not None:
        await contract_service.assert_provider_template(
            session, str(changes["provider_template_key"])
        )

    if "status" in changes and changes["status"] != before.status:
        contract_service.assert_status_transition(before.status, str(changes["status"]))

    assignments: list[str] = []
    params: dict[str, Any] = {"id": str(contract_id), "household_id": str(household_id)}
    for column in _UPDATABLE_COLUMNS:
        if column not in changes:
            continue
        assignments.append(f"{column} = {_placeholder(column)}")
        params[column] = _param_value(column, changes[column])

    if not assignments:
        response.headers["ETag"] = entity_etag(before.version)
        return before

    updated = await session.execute(
        text(
            "update contracts set "
            + ", ".join(assignments)
            + " where id = :id and household_id = :household_id"
        ),
        params,
    )
    if cast(CursorResult[Any], updated).rowcount == 0:  # pragma: no cover - RLS-Restrisiko
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=contract_service.CONTRACT_NOT_FOUND
        )

    if "amount_minor" in changes and int(changes["amount_minor"]) != before.amount_minor:
        await contract_service.open_amount_stage(
            session,
            household_id,
            contract_id,
            amount_minor=int(changes["amount_minor"]),
            valid_from=date.today(),
            change_reason="manual",
        )

    if "status" in changes and changes["status"] != before.status:
        await contract_service.record_event(
            session,
            household_id,
            contract_id,
            _status_event_kind(str(changes["status"])),
            payload={"status_from": before.status, "status_to": changes["status"]},
        )

    if is_reserve_eligible(resulting_direction, resulting_frequency):
        await session.execute(
            text(
                "insert into reserve_pots (household_id, kind, contract_id) "
                "values (:household_id, 'contract', :contract_id) on conflict do nothing"
            ),
            {"household_id": str(household_id), "contract_id": str(contract_id)},
        )

    await record_audit_event(
        session,
        household_id,
        user.id,
        contract_service.CONTRACT_ENTITY_TYPE,
        contract_id,
        "update",
        before=before_snapshot,
        after=await contract_service.fetch_contract_snapshot(
            session, household_id, contract_id
        ),
    )

    contract = await contract_service.require_contract(session, household_id, contract_id)
    response.headers["ETag"] = entity_etag(contract.version)
    return contract


def _status_event_kind(target_status: str) -> str:
    if target_status == "ended":
        return "ended"
    if target_status == "cancelled":
        return "cancelled"
    if target_status == "not_a_contract":
        return "dismissed"
    return "reactivated"


@router.post("/{contract_id}/status", response_model=Contract)
async def change_status(
    contract_id: UUID,
    payload: ContractStatusChange,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> Contract:
    """Einfeldaktion aus dem Zeilenmenue. `If-Match` ist optional, weil hier
    nichts ueberschrieben wird, was ein anderer Nutzer parallel getippt haben
    koennte."""
    session, household_id = ctx
    expected_version = parse_if_match(if_match, required=False)

    before = await contract_service.require_contract(session, household_id, contract_id)
    before_snapshot = await contract_service.fetch_contract_snapshot(
        session, household_id, contract_id
    )
    _assert_version(before.version, expected_version)
    contract_service.assert_status_transition(before.status, payload.status)

    resumed = payload.status == "active"
    today = date.today()
    next_due = before.next_due_date
    clear_ends_on = False
    clear_cancelled_on = False
    # Ein beendeter Vertrag braucht ein Enddatum. Das Zeilenmenue sendet nur den
    # Status (ContractsPage: "Beenden"), und ohne `ends_on` liefe der Vertrag im
    # Fixkostentrend bis in alle Zukunft weiter - die offene Preisstufe trifft
    # jeden Monat des Rasters, und der Bericht kann nie sinken.
    ends_on = payload.ends_on
    if ends_on is None and payload.status == "ended" and before.ends_on is None:
        ends_on = today
    if resumed:
        # Der Fehlzaehler allein genuegt nicht. Bliebe next_due_date in der
        # Vergangenheit stehen, holte der naechste Abgleich dieselben Perioden
        # erneut nach, zaehlte sie wieder als ausgeblieben und die Automatik
        # beendete den Vertrag am Morgen nach der Reaktivierung erneut - mit
        # frei erfundenen 'missing'-Faelligkeiten. Derselbe Schritt wie im
        # Inbox-Pfad (routes/review.py, _answer_contract_ended_check).
        guard = 0
        while next_due < today and guard < _MAX_RESUME_PERIODS:
            next_due = contract_service.next_due_date(
                next_due,
                before.frequency,
                due_rule=before.due_rule,
                due_day=before.due_day,
            )
            guard += 1
        # Ein ausdruecklich wieder aufgenommener Vertrag kann kein Ende in der
        # Vergangenheit mehr haben: `contract_matching_service.is_term_reached`
        # setzte ihn im naechsten Lauf sofort wieder auf 'ended'. Ein im selben
        # Aufruf mitgeschicktes Datum bleibt unangetastet - der Nutzer weiss es
        # besser als diese Heuristik.
        clear_ends_on = (
            ends_on is None and before.ends_on is not None and before.ends_on < today
        )
        clear_cancelled_on = (
            payload.cancelled_on is None
            and before.cancelled_on is not None
            and before.cancelled_on < today
        )

    await session.execute(
        text(
            "update contracts set status = :status, "
            "cancelled_on = case when cast(:clear_cancelled_on as boolean) then null "
            "  else coalesce(:cancelled_on, cancelled_on) end, "
            "ends_on = case when cast(:clear_ends_on as boolean) then null "
            "  else coalesce(:ends_on, ends_on) end, "
            "next_due_date = :next_due_date, "
            # Ein wieder aufgenommener Vertrag startet ohne Fehlzaehler, sonst
            # wuerde die Automatik ihn sofort erneut beenden.
            "missed_periods = case when :status in ('active', 'paused') then 0 "
            "else missed_periods end "
            "where id = :id and household_id = :household_id"
        ),
        {
            "id": str(contract_id),
            "household_id": str(household_id),
            "status": payload.status,
            "cancelled_on": payload.cancelled_on,
            "ends_on": ends_on,
            "clear_cancelled_on": clear_cancelled_on,
            "clear_ends_on": clear_ends_on,
            "next_due_date": next_due,
        },
    )

    await contract_service.record_event(
        session,
        household_id,
        contract_id,
        _status_event_kind(payload.status),
        payload={
            "status_from": before.status,
            "status_to": payload.status,
            "reason": payload.reason or "manual",
        },
    )
    await record_audit_event(
        session,
        household_id,
        user.id,
        contract_service.CONTRACT_ENTITY_TYPE,
        contract_id,
        cast(Any, _STATUS_AUDIT_ACTION.get(payload.status, "update")),
        before=before_snapshot,
        after=await contract_service.fetch_contract_snapshot(
            session, household_id, contract_id
        ),
        metadata={"status_from": before.status, "status_to": payload.status},
    )

    contract = await contract_service.require_contract(session, household_id, contract_id)
    response.headers["ETag"] = entity_etag(contract.version)
    return contract


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contract(
    contract_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> None:
    """Hartes Loeschen ist hier ausdruecklich erlaubt: anders als Kategorien
    tragen Vertraege keine historischen Summen, und eine Fehlerkennung soll
    wirklich verschwinden koennen."""
    session, household_id = ctx
    before = await contract_service.fetch_contract_snapshot(
        session, household_id, contract_id
    )
    result = await session.execute(
        text(
            "delete from contracts where id = :id and household_id = :household_id "
            "returning id"
        ),
        {"id": str(contract_id), "household_id": str(household_id)},
    )
    # Ohne returning antwortet ein Delete, das keine Zeile getroffen hat, mit
    # 204 - das Frontend rendert dann eine Loeschung, die nie stattfand.
    if result.first() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=contract_service.CONTRACT_NOT_FOUND
        )
    await record_audit_event(
        session,
        household_id,
        user.id,
        contract_service.CONTRACT_ENTITY_TYPE,
        contract_id,
        "delete",
        before=before,
    )


# ---------------------------------------------------------------------------
# Verlauf, Faelligkeiten, Ereignisse
# ---------------------------------------------------------------------------


@router.get("/{contract_id}/history", response_model=list[ContractAmountEntry])
async def list_history(contract_id: UUID, ctx: HouseholdSession) -> list[ContractAmountEntry]:
    session, household_id = ctx
    await contract_service.require_contract(session, household_id, contract_id)
    return await contract_service.list_amount_history(session, household_id, contract_id)


@router.post(
    "/{contract_id}/history",
    response_model=Contract,
    status_code=status.HTTP_201_CREATED,
)
async def add_history_entry(
    contract_id: UUID,
    payload: ContractAmountCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> Contract:
    """Manuelle Betragskorrektur ab einem Stichtag. Der Sollbetrag folgt nur
    dann, wenn die neue Stufe die aktuell offene ist."""
    session, household_id = ctx
    before = await contract_service.require_contract(session, household_id, contract_id)
    before_snapshot = await contract_service.fetch_contract_snapshot(
        session, household_id, contract_id
    )

    await contract_service.open_amount_stage(
        session,
        household_id,
        contract_id,
        amount_minor=payload.amount_minor,
        valid_from=payload.valid_from,
        change_reason="manual",
    )
    await session.execute(
        text(
            "update contracts set amount_minor = :amount_minor "
            "where id = :id and household_id = :household_id"
        ),
        {
            "id": str(contract_id),
            "household_id": str(household_id),
            "amount_minor": payload.amount_minor,
        },
    )
    await contract_service.record_event(
        session,
        household_id,
        contract_id,
        "price_change",
        occurred_on=payload.valid_from,
        payload={
            "old_minor": before.amount_minor,
            "new_minor": payload.amount_minor,
            "delta_minor": payload.amount_minor - before.amount_minor,
            "source": "manual",
        },
    )
    await record_audit_event(
        session,
        household_id,
        user.id,
        contract_service.CONTRACT_ENTITY_TYPE,
        contract_id,
        "update",
        before=before_snapshot,
        after=await contract_service.fetch_contract_snapshot(
            session, household_id, contract_id
        ),
    )
    return await contract_service.require_contract(session, household_id, contract_id)


@router.get("/{contract_id}/occurrences", response_model=list[ContractOccurrence])
async def list_occurrences(
    contract_id: UUID,
    ctx: HouseholdSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 120,
) -> list[ContractOccurrence]:
    session, household_id = ctx
    await contract_service.require_contract(session, household_id, contract_id)
    return await contract_service.list_occurrences(
        session, household_id, contract_id, limit=limit
    )


@router.get("/{contract_id}/events", response_model=list[ContractEvent])
async def list_events(
    contract_id: UUID,
    ctx: HouseholdSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> list[ContractEvent]:
    session, household_id = ctx
    await contract_service.require_contract(session, household_id, contract_id)
    return await contract_service.list_events(session, household_id, contract_id, limit=limit)


# ---------------------------------------------------------------------------
# Belege, freie Buchungen und Neuberechnung (Abschnitt 19.7)
# ---------------------------------------------------------------------------
#
# Vier Endpunkte fuer einen Ablauf: der Nutzer sieht, dass eine Preisstufe
# nichts belegt, sucht die fehlenden Buchungen, sieht die Auswirkung und laesst
# die Treppe neu rechnen. Das Zuordnen selbst bleibt der vorhandene
# `POST /transactions/{id}/reassign`; hier entsteht kein zweiter Schreibweg.


@router.get(
    "/{contract_id}/amount-history/evidence", response_model=ContractAmountEvidenceResponse
)
async def amount_history_evidence(
    contract_id: UUID, ctx: HouseholdSession
) -> ContractAmountEvidenceResponse:
    """Welche Buchungen stehen hinter welcher Preisstufe?

    Lesen ist wie bei den uebrigen Belegen fuer jedes Haushaltsmitglied erlaubt
    (Abschnitt 19.10).
    """
    session, household_id = ctx
    return await contract_service.build_amount_evidence(session, household_id, contract_id)


@router.get("/{contract_id}/free-transactions", response_model=ContractFreeTransactionsResponse)
async def free_transactions(
    contract_id: UUID,
    ctx: HouseholdSession,
    date_from: date | None = None,
    date_to: date | None = None,
    amount_min_minor: Annotated[int | None, Query(ge=0)] = None,
    amount_max_minor: Annotated[int | None, Query(ge=0)] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    use_contract_defaults: bool = True,
    limit: Annotated[
        int, Query(ge=1, le=contract_service.FREE_TRANSACTIONS_MAX_LIMIT)
    ] = contract_service.FREE_TRANSACTIONS_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ContractFreeTransactionsResponse:
    """Buchungen des Haushalts, die noch keinem Vertrag zugeordnet sind.

    Ohne Angaben gilt die Vorbelegung aus dem Vertrag (Anbieterwort, weites
    Betragsband um die bekannten Preisstufen, 36 Monate Rueckblick). Jeder
    Parameter ueberschreibt sie einzeln, `use_contract_defaults=false` schaltet
    sie ganz ab - wer hier sucht, sucht gerade deshalb von Hand, weil die
    Automatik nichts gefunden hat.
    """
    session, household_id = ctx
    contract = await contract_service.require_contract(session, household_id, contract_id)
    contract_filter = await contract_service.build_free_transaction_filter(
        session,
        household_id,
        contract,
        date_from=date_from,
        date_to=date_to,
        amount_min_minor=amount_min_minor,
        amount_max_minor=amount_max_minor,
        search=search,
        use_contract_defaults=use_contract_defaults,
    )
    return await contract_service.search_free_transactions(
        session, household_id, contract, contract_filter, limit=limit, offset=offset
    )


@router.post("/{contract_id}/assignment-preview", response_model=ContractAssignmentPreview)
async def assignment_preview(
    contract_id: UUID, payload: ContractAssignmentPreviewRequest, ctx: HouseholdSession
) -> ContractAssignmentPreview:
    """Vorschau des Zuordnens mehrerer Buchungen: Quelle, Ziel, betroffene
    Buchungen und die Auswirkung auf Zeitraeume und Preisverlauf (19.7).

    Schreibt nichts. Der bestehende
    `POST /transactions/{id}/reassign/preview` bleibt die Einzelfallvorschau;
    diese hier beantwortet die Frage fuer einen ganzen Schwung Buchungen, weil
    nur die Gesamtmenge die Treppe ergibt, die am Ende entsteht.
    """
    session, household_id = ctx
    return await contract_service.build_assignment_preview(
        session, household_id, contract_id, payload.transaction_ids
    )


@router.post(
    "/{contract_id}/amount-history/recalculate",
    response_model=ContractAmountRecalculationResult,
)
async def recalculate_amount_history(
    contract_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> ContractAmountRecalculationResult:
    """Rechnet den Betragsverlauf aus den zugeordneten Buchungen neu.

    Der Schritt nach dem Zuordnen: ohne ihn sieht der Nutzer zwar die neue
    Buchung am Vertrag, der Betragsverlauf bliebe aber der alte. Manuelle Zeilen
    bleiben unangetastet (siehe Dienst-Docstring), `contracts.amount_minor`
    ebenfalls - deshalb `update` und nicht `merge` im Audit-Log.
    """
    session, household_id = ctx
    result = await contract_service.recalculate_amount_history(
        session, household_id, contract_id
    )
    if result.changed:
        await record_audit_event(
            session,
            household_id,
            user.id,
            contract_service.CONTRACT_ENTITY_TYPE,
            contract_id,
            "update",
            metadata={
                "amount_history_recalculated": True,
                "stages_written": result.stages_written,
                "stages_removed": result.stages_removed,
                "manual_stages_kept": result.manual_stages_kept,
            },
        )
    return result


@router.post(
    "/{contract_id}/ai-transaction-check", response_model=ContractAITransactionCheckResponse
)
async def ai_transaction_check(
    contract_id: UUID,
    ctx: HouseholdEditorSession,
    payload: ContractAITransactionCheckRequest | None = None,
) -> ContractAITransactionCheckResponse:
    """Laesst das lokale Sprachmodell die restlichen freien Buchungen zu diesem
    Vertrag beurteilen (Abschnitt 19.11).

    Das Modell schlaegt vor und begruendet; es ordnet nichts zu, schreibt kein
    Feld und liefert keine Prozentzahl. Zugeordnet wird ausschliesslich ueber
    `POST /transactions/{id}/reassign` durch den Nutzer.

    Editorrecht aus demselben Grund wie bei `/ai-crosscheck`: der Aufruf stoesst
    mehrere Modellaufrufe nacheinander an, und handeln kann auf das Ergebnis
    ohnehin nur, wer zuordnen darf.
    """
    session, household_id = ctx
    return await contract_ai_crosscheck_service.check_transactions_for_contract(
        session,
        household_id,
        contract_id,
        transaction_ids=payload.transaction_ids if payload is not None else None,
    )
