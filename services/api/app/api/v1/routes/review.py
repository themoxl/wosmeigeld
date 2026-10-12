"""API-Endpunkte fuer die Review-Inbox (Phase 4, Vertragsvorschlaege ab Phase 3
der Vertragsplanung)."""

from datetime import date
from types import SimpleNamespace
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.schemas.review import (
    AcceptContractAmountChange,
    AcceptContractDuplicate,
    AcceptContractEnded,
    AcceptContractSuggestion,
    AcceptDuplicate,
    AcceptRecurring,
    AcceptTransfer,
    AcceptUnclassified,
    BookedDuplicateAssessmentInput,
    BookedDuplicateAssessmentResult,
    BookedDuplicateScanResult,
    BulkAcceptDuplicatesInput,
    BulkAcceptDuplicatesResult,
    BulkResolveBookedDuplicatesInput,
    BulkResolveBookedDuplicatesResult,
    CorrectDuplicate,
    CorrectTransfer,
    CorrectUnclassified,
    DuplicateAssessmentInput,
    DuplicateAssessmentResult,
    ResolveBookedDuplicateInput,
    ReviewAcceptInput,
    ReviewContractSuggestionDetail,
    ReviewCorrectInput,
    ReviewHistoryMatch,
    ReviewHistoryMatchesResponse,
    ReviewHistoryMatchItem,
    ReviewInboxResponse,
    ReviewRecurringDetail,
    ReviewRulePreviewInput,
)
from app.schemas.rules import RuleApplyResult
from app.services import (
    booked_duplicate_ai_service,
    booked_duplicate_service,
    contract_detection_service,
    contract_matching_service,
    contract_service,
    duplicate_ai_service,
    provenance_service,
    rules_service,
)
from app.services.audit_service import (
    fetch_rule_snapshot,
    record_audit_event,
    record_rule_event,
)
from app.services.category_service import resolve_category_id
from app.services.import_service import (
    commit_single_row,
    get_import_row_for_review,
    mark_import_row_skipped,
    maybe_complete_run,
)
from app.services.rules_service import contract_suggestion_detail, get_review_inbox

router = APIRouter(prefix="/review", tags=["review"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Annehmen/Korrigieren schreiben Buchungen, Splits und Regeln - 403 statt
# stillem Nichtstun, wenn die Rolle nicht schreiben darf.
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

# Aus einer Review-Korrektur abgeleitete Regeln laufen mit der Default-
# Prioritaet 0 - genau wie manuell angelegte Regeln. Frueher vergab die
# Review-Inbox 10 bzw. 20 und ueberstimmte damit jede ausdrueckliche
# Nutzerregel. Bei Gleichstand entscheidet in
# rules_service.match_rules_for_transaction() das aeltere created_at, die
# bestehende Nutzerregel gewinnt also.
_REVIEW_RULE_PRIORITY = 0


@router.get("", response_model=ReviewInboxResponse)
async def fetch_review_inbox(
    session_ctx: HouseholdSession,
    unclassified_limit: int = Query(default=100, ge=1, le=250),
    unclassified_offset: int = Query(default=0, ge=0),
    unclassified_search: str | None = Query(default=None, max_length=160),
) -> ReviewInboxResponse:
    db, household_id = session_ctx
    return await get_review_inbox(
        db,
        household_id,
        unclassified_limit=unclassified_limit,
        unclassified_offset=unclassified_offset,
        unclassified_search=unclassified_search,
    )


_CONTRACT_SUGGESTION_NOT_FOUND = "Vertragsvorschlag nicht (mehr) gefunden."


@router.get("/duplicates/scan", response_model=BookedDuplicateScanResult)
async def scan_duplicates(session_ctx: HouseholdSession) -> BookedDuplicateScanResult:
    db, household_id = session_ctx
    return await booked_duplicate_service.scan_booked_duplicates(db, household_id)


@router.post("/duplicates/resolve", status_code=204)
async def resolve_booked_duplicate(
    payload: ResolveBookedDuplicateInput,
    session_ctx: HouseholdEditorSession,
    background_tasks: BackgroundTasks,
) -> None:
    from app.api.v1.routes.transactions import delete_transaction

    db, household_id = session_ctx
    await booked_duplicate_service.validate_pair(db, household_id, payload)
    if payload.action == "keep_both":
        await booked_duplicate_service.dismiss_pair(db, household_id, payload)
    else:
        transaction_id = (
            payload.first_transaction_id if payload.action == "archive_first"
            else payload.second_transaction_id
        )
        await delete_transaction(transaction_id, session_ctx, background_tasks)


@router.post("/duplicates/resolve-bulk", response_model=BulkResolveBookedDuplicatesResult)
async def resolve_booked_duplicates_bulk(
    payload: BulkResolveBookedDuplicatesInput,
    session_ctx: HouseholdEditorSession,
    background_tasks: BackgroundTasks,
) -> BulkResolveBookedDuplicatesResult:
    from app.services.worker_dispatch import enqueue_contract_reconcile

    db, household_id = session_ctx
    result, detached = await booked_duplicate_service.resolve_bulk(db, household_id, payload)
    if any(key.startswith("vertrag") for key in detached):
        background_tasks.add_task(
            enqueue_contract_reconcile, household_id, context="booked_duplicates_bulk"
        )
    return result


@router.post("/duplicates/assess-booked", response_model=BookedDuplicateAssessmentResult)
async def assess_booked_duplicates(
    payload: BookedDuplicateAssessmentInput,
    session_ctx: HouseholdEditorSession,
) -> BookedDuplicateAssessmentResult:
    db, household_id = session_ctx
    return await booked_duplicate_ai_service.assess_booked_duplicates(db, household_id, payload)


@router.get(
    "/contract-suggestions/{source_transaction_id}",
    response_model=ReviewContractSuggestionDetail,
)
async def fetch_contract_suggestion_detail(
    source_transaction_id: UUID,
    session_ctx: HouseholdSession,
) -> ReviewContractSuggestionDetail:
    """Buchungen, Preisstufen und Score-Bestandteile des angeklickten Vorschlags."""
    db, household_id = session_ctx
    candidate = await contract_detection_service.find_candidate(
        db, household_id, source_transaction_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail=_CONTRACT_SUGGESTION_NOT_FOUND)
    return contract_suggestion_detail(candidate)


@router.get(
    "/recurring-suggestions/{source_transaction_id}",
    response_model=ReviewRecurringDetail,
)
async def fetch_recurring_suggestion_detail(
    source_transaction_id: UUID,
    session_ctx: HouseholdSession,
) -> ReviewRecurringDetail:
    """VERALTET, abgeloest durch `/contract-suggestions/{id}`.

    Bleibt fuer bereits ausgelieferte Clients. Halbjaehrliche und unsichere
    Kandidaten lassen sich im Altformat nicht darstellen - dafuer gibt es 404
    statt einer Antwort, die den Response-Validator zerlegt oder einen
    unsicheren Vorschlag als gleichwertig ausgibt.
    """
    db, household_id = session_ctx
    candidate = await contract_detection_service.find_candidate(
        db, household_id, source_transaction_id
    )
    detail = candidate.as_legacy_detail() if candidate is not None else None
    if detail is None:
        raise HTTPException(
            status_code=404, detail="Wiederkehrender Vorschlag nicht (mehr) gefunden."
        )
    return detail


async def _create_review_rule(
    db: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID,
    payee: str,
    category_id: UUID,
    rule_name: str | None,
    *,
    match_purpose: str | None = None,
    match_exact_amount: int | None = None,
    match_account_id: UUID | None = None,
    match_payee: bool = True,
) -> UUID | None:
    """Einziger Pfad, ueber den die Review-Inbox eine Klassifikationsregel
    anlegt (frueher legten Annehmen und Korrigieren je eine eigene an).

    `on conflict do nothing` greift auf den partiellen Unique-Index
    classification_rules_payee_category_uniq (siehe
    supabase/migrations/0001_schema.sql): eine wiederholte identische
    Korrektur erzeugt weder eine zweite Regel noch ein weiteres
    Audit-Ereignis.

    Die Zielkategorie laeuft auch hier durch den zentralen Resolver: eine Regel
    wirkt in die Zukunft und darf niemals auf eine archivierte oder
    haushaltsfremde Kategorie zeigen (docs/KATEGORISIERUNGSPLAN.md,
    "Zentraler Kategorie-Resolver")."""
    resolved_category_id = await resolve_category_id(
        db, household_id, category_id, allow_none=False
    )
    name = rule_name or f"Regel fuer {payee or 'passende Buchungen'}"
    res = await db.execute(
        text(
            "insert into classification_rules (household_id, name, priority, is_active, "
            "match_payee_raw, match_purpose, match_min_amount_minor, "
            "match_max_amount_minor, match_account_id, action_category_id, source) "
            "values (:household_id, :name, :priority, true, :match_payee, :match_purpose, "
            ":match_amount, :match_amount, :match_account_id, :cat_id, 'user') "
            "on conflict do nothing "
            "returning id"
        ),
        {
            "household_id": str(household_id),
            "name": name,
            "priority": _REVIEW_RULE_PRIORITY,
            "match_payee": payee if match_payee else None,
            "match_purpose": match_purpose,
            "match_amount": match_exact_amount,
            "match_account_id": str(match_account_id) if match_account_id else None,
            "cat_id": str(resolved_category_id),
        },
    )
    created = res.first()
    if created is None:
        return None

    snapshot = await fetch_rule_snapshot(db, household_id, created.id)
    await record_rule_event(
        db, household_id, actor_user_id, created.id, "create", after=snapshot
    )
    return UUID(str(created.id))


async def _review_rule_from_transaction(
    db: AsyncSession,
    household_id: UUID,
    transaction_id: UUID,
    category_id: UUID | None,
    *,
    payee_pattern: str | None,
    match_payee: bool,
    match_purpose: bool,
    match_exact_amount: bool,
    match_account: bool,
) -> tuple[Any, UUID | None]:
    """Baut den Regelentwurf aus den echten Buchungswerten auf dem Server."""
    row = (
        await db.execute(
            text(
                "select payee_label, payee_raw, note, purpose_raw, amount_minor, account_id "
                "from transactions where id = :id and household_id = :household_id "
                "and not is_archived"
            ),
            {"id": str(transaction_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden.")
    if not any((match_payee, match_purpose, match_exact_amount, match_account)):
        raise HTTPException(
            status_code=422, detail="Die Regel braucht mindestens ein Vergleichsmerkmal."
        )
    # Die reine Vorpruefung darf vor der Kategorieentscheidung laufen. Der
    # Endpunkt schreibt nichts; beim spaeteren Anwenden bleibt die Kategorie
    # ueber CorrectUnclassified zwingend.
    target_id = await resolve_category_id(db, household_id, category_id, allow_none=True)
    effective_payee = (payee_pattern or row.payee_raw or row.payee_label or "").strip()
    effective_purpose = (row.purpose_raw or row.note or "").strip()
    if match_payee and not effective_payee:
        raise HTTPException(status_code=422, detail="Diese Buchung hat keinen Empfaenger.")
    if match_purpose and not effective_purpose:
        raise HTTPException(status_code=422, detail="Diese Buchung hat keinen Verwendungszweck.")
    rule = SimpleNamespace(
        id=transaction_id,
        name=f"Regel fuer {effective_payee or 'passende Buchungen'}",
        match_payee_raw=effective_payee if match_payee else None,
        match_purpose=effective_purpose if match_purpose else None,
        match_min_amount_minor=int(row.amount_minor) if match_exact_amount else None,
        match_max_amount_minor=int(row.amount_minor) if match_exact_amount else None,
        match_account_id=row.account_id if match_account else None,
        action_category_id=target_id,
        action_set_transfer=False,
    )
    return rule, target_id


async def _evaluate_review_rule(
    db: AsyncSession,
    household_id: UUID,
    rule: Any,
    target_id: UUID | None,
    *,
    write: bool,
) -> RuleApplyResult:
    items, matched, truncated = await rules_service.apply_rule_to_existing(
        db,
        household_id,
        rule,
        target_category_id=target_id,
        scope="uncategorized",
        from_date=None,
        to_date=None,
        limit=500,
        write=write,
    )
    return RuleApplyResult(
        rule_id=rule.id,
        rule_name=rule.name,
        scope="uncategorized",
        dry_run=not write,
        matched=matched,
        applied=sum(1 for item in items if item.status == "applied"),
        unchanged=sum(1 for item in items if item.status == "unchanged"),
        skipped=sum(1 for item in items if item.status == "skipped"),
        conflict=sum(1 for item in items if item.status == "conflict"),
        truncated=truncated,
        items=items,
    )


@router.post("/rule-preview", response_model=RuleApplyResult)
async def preview_review_rule(
    payload: ReviewRulePreviewInput,
    session_ctx: HouseholdEditorSession,
) -> RuleApplyResult:
    """Regelvergleich ohne Modellaufruf und ohne Schreibvorgang."""
    db, household_id = session_ctx
    rule, target_id = await _review_rule_from_transaction(
        db,
        household_id,
        payload.transaction_id,
        payload.category_id,
        payee_pattern=payload.payee_pattern,
        match_payee=payload.match_payee,
        match_purpose=payload.match_purpose,
        match_exact_amount=payload.match_exact_amount,
        match_account=payload.match_account,
    )
    return await _evaluate_review_rule(db, household_id, rule, target_id, write=False)


async def _load_active_review_rules(
    db: AsyncSession, household_id: UUID
) -> list[Any]:
    """Laedt Regeln einmalig in derselben Reihenfolge wie der Importpfad."""
    rows = await db.execute(
        text(
            "select r.id, r.name, r.action_category_id, r.action_set_transfer, "
            "r.match_payee_raw, r.match_payee_exact_word, r.match_purpose, "
            "r.match_purpose_exact_word, r.exclude_payee_raw, r.exclude_purpose, "
            "r.match_min_amount_minor, r.match_max_amount_minor, r.match_amount_mode, "
            "r.match_account_id, "
            "c.name as category_name, cg.name as group_name, "
            "(c.id is not null and not c.is_archived and not cg.is_archived "
            "and c.merged_into_category_id is null) as is_selectable "
            "from classification_rules r "
            "left join categories c on c.id = r.action_category_id "
            "and c.household_id = r.household_id "
            "left join category_groups cg on cg.id = c.group_id "
            "and cg.household_id = c.household_id "
            "where r.household_id = :household_id and r.is_active = true "
            "order by r.priority desc, r.created_at asc"
        ),
        {"household_id": str(household_id)},
    )
    return list(rows.all())


def _matching_review_category_rule(rules: list[Any], transaction: Any) -> Any | None:
    """Gibt die gewinnende aktive Regel zurueck, sofern sie eine Kategorie setzt."""
    for rule in rules:
        matched_on = rules_service.evaluate_rule(
            rule,
            payee_label=transaction.payee_text,
            purpose=transaction.purpose_text,
            amount_minor=int(transaction.amount_minor),
            account_id=UUID(str(transaction.account_id)),
        )
        if matched_on is None:
            continue

        # Genau wie beim Import gewinnt die erste passende aktive Regel. Eine
        # reine Notiz-/Transferregel wird hier aber nicht als Kategorieangebot
        # dargestellt; eine niedriger priorisierte Regel darf sie nicht umgehen.
        if (
            rule.action_category_id is None
            or bool(rule.action_set_transfer)
            or not bool(rule.is_selectable)
        ):
            return None
        return rule
    return None


@router.get("/history-match/{transaction_id}", response_model=ReviewHistoryMatch)
async def review_history_match(
    transaction_id: UUID,
    session_ctx: HouseholdEditorSession,
) -> ReviewHistoryMatch:
    """Sucht vor jedem Modellaufruf in Regeln und bestaetigtem Bestand.

    Zuerst zaehlen manuelle und regelbasierte Zuordnungen als Vorbild. Falls
    dort nichts Eindeutiges vorliegt, laufen aktive Regeln durch exakt dieselbe
    Auswertung wie beim Import. KI- und Historienvorschlaege duerfen sich nicht
    selbst bestaetigen.
    """
    db, household_id = session_ctx
    current = (
        await db.execute(
            text(
                "select coalesce(nullif(btrim(payee_label), ''), "
                "nullif(btrim(payee_raw), '')) as history_payee_label, "
                "coalesce(nullif(payee_raw, ''), payee_label) as payee_text, "
                "coalesce(nullif(purpose_raw, ''), note) as purpose_text, "
                "amount_minor, account_id "
                "from transactions where id = :transaction_id "
                "and household_id = :household_id and not is_archived"
            ),
            {
                "transaction_id": str(transaction_id),
                "household_id": str(household_id),
            },
        )
    ).first()
    if current is None:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden.")

    key = rules_service._recurring_payee_key(current.history_payee_label)
    matches: list[Any] = []
    if key:
        rows = await db.execute(
            text(
                "select t.payee_label, s.category_id, c.name as category_name, "
                "cg.name as group_name, "
                "(not c.is_archived and not cg.is_archived "
                "and c.merged_into_category_id is null) as is_selectable "
                "from transactions t "
                "join transaction_splits s on s.transaction_id = t.id "
                "and s.household_id = t.household_id "
                "join categories c on c.id = s.category_id "
                "and c.household_id = t.household_id "
                "join category_groups cg on cg.id = c.group_id "
                "and cg.household_id = c.household_id "
                "where t.household_id = :household_id and not t.is_archived "
                "and not t.is_transfer and s.category_id is not null "
                "and s.category_source in ('manual', 'rule') "
                "and t.payee_label is not null and btrim(t.payee_label) <> ''"
            ),
            {"household_id": str(household_id)},
        )
        matches = [
            row
            for row in rows
            if rules_service._recurring_payee_key(row.payee_label) == key
        ]

    category_ids = {UUID(str(row.category_id)) for row in matches}
    ambiguous = len(category_ids) > 1
    if len(category_ids) == 1:
        category_id = next(iter(category_ids))
        selected = next(row for row in matches if UUID(str(row.category_id)) == category_id)
        if bool(selected.is_selectable):
            return ReviewHistoryMatch(
                matched=True,
                category_id=category_id,
                category_label=f"{selected.group_name} · {selected.category_name}",
                evidence_count=len(matches),
                source="history",
            )

    active_rules = await _load_active_review_rules(db, household_id)
    matching_rule = _matching_review_category_rule(active_rules, current)
    if matching_rule is not None:
        return ReviewHistoryMatch(
            matched=True,
            category_id=UUID(str(matching_rule.action_category_id)),
            category_label=f"{matching_rule.group_name} · {matching_rule.category_name}",
            source="rule",
            rule_id=UUID(str(matching_rule.id)),
            rule_name=str(matching_rule.name),
        )

    return ReviewHistoryMatch(
        matched=False,
        evidence_count=len(matches),
        ambiguous=ambiguous,
    )


@router.get("/history-matches", response_model=ReviewHistoryMatchesResponse)
async def review_history_matches(
    session_ctx: HouseholdEditorSession,
) -> ReviewHistoryMatchesResponse:
    """Bestands- und Regelabgleich fuer alle offenen Buchungen ohne KI."""
    db, household_id = session_ctx
    open_rows = (
        await db.execute(
            text(
                "select t.id, coalesce(nullif(btrim(t.payee_label), ''), "
                "nullif(btrim(t.payee_raw), '')) as history_payee_label, "
                "coalesce(nullif(t.payee_raw, ''), t.payee_label) as payee_text, "
                "coalesce(nullif(t.purpose_raw, ''), t.note) as purpose_text, "
                "t.amount_minor, t.account_id "
                "from transactions t where t.household_id = :household_id "
                "and not t.is_archived and not t.is_transfer "
                "and (not exists (select 1 from transaction_splits s "
                "where s.transaction_id = t.id) or exists (select 1 "
                "from transaction_splits s where s.transaction_id = t.id "
                "and s.category_id is null))"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    active_rules = await _load_active_review_rules(db, household_id)
    precedent_rows = (
        await db.execute(
            text(
                "select t.payee_label, s.category_id, c.name as category_name, "
                "cg.name as group_name, (not c.is_archived and not cg.is_archived "
                "and c.merged_into_category_id is null) as is_selectable "
                "from transactions t join transaction_splits s on s.transaction_id = t.id "
                "and s.household_id = t.household_id "
                "join categories c on c.id = s.category_id "
                "and c.household_id = t.household_id "
                "join category_groups cg on cg.id = c.group_id "
                "and cg.household_id = c.household_id "
                "where t.household_id = :household_id and not t.is_archived "
                "and not t.is_transfer and s.category_id is not null "
                "and s.category_source in ('manual', 'rule') "
                "and t.payee_label is not null and btrim(t.payee_label) <> ''"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    precedents_by_key: dict[str, list[Any]] = {}
    for row in precedent_rows:
        key = rules_service._recurring_payee_key(row.payee_label)
        if key:
            precedents_by_key.setdefault(key, []).append(row)

    items: list[ReviewHistoryMatchItem] = []
    for row in open_rows:
        key = rules_service._recurring_payee_key(row.history_payee_label)
        precedents = precedents_by_key.get(key, []) if key else []
        category_ids = {UUID(str(item.category_id)) for item in precedents}
        ambiguous = len(category_ids) > 1
        selected = precedents[0] if len(category_ids) == 1 and precedents else None
        if selected is not None and bool(selected.is_selectable):
            items.append(
                ReviewHistoryMatchItem(
                    transaction_id=UUID(str(row.id)),
                    matched=True,
                    category_id=UUID(str(selected.category_id)),
                    category_label=f"{selected.group_name} · {selected.category_name}",
                    evidence_count=len(precedents),
                    source="history",
                )
            )
            continue

        matching_rule = _matching_review_category_rule(active_rules, row)
        if matching_rule is not None:
            items.append(
                ReviewHistoryMatchItem(
                    transaction_id=UUID(str(row.id)),
                    matched=True,
                    category_id=UUID(str(matching_rule.action_category_id)),
                    category_label=(
                        f"{matching_rule.group_name} · {matching_rule.category_name}"
                    ),
                    source="rule",
                    rule_id=UUID(str(matching_rule.id)),
                    rule_name=str(matching_rule.name),
                )
            )
            continue

        items.append(
            ReviewHistoryMatchItem(
                transaction_id=UUID(str(row.id)),
                matched=False,
                evidence_count=len(precedents),
                ambiguous=ambiguous,
            )
        )
    return ReviewHistoryMatchesResponse(
        checked_count=len(items),
        matched_count=sum(1 for item in items if item.matched),
        items=items,
    )


async def _resolve_unclassified(
    db: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID,
    transaction_id: UUID,
    category_id: UUID | None,
    payee_label: str | None,
    create_rule: bool,
    rule_name: str | None,
    *,
    from_proposal: bool,
    rule_match_payee: bool = True,
    rule_match_purpose: bool = False,
    rule_match_exact_amount: bool = False,
    rule_match_account: bool = False,
    apply_rule_to_existing: bool = False,
) -> None:
    tx_res = await db.execute(
        text(
            "select payee_label, payee_raw, purpose_raw, note, amount_minor, account_id "
            "from transactions "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(transaction_id), "household_id": str(household_id)},
    )
    tx = tx_res.first()
    if not tx:
        raise HTTPException(status_code=404, detail="Buchung nicht gefunden.")

    # Vor jedem Schreibvorgang aufloesen: sonst koennte der Zahlungsempfaenger
    # bereits umbenannt sein, waehrend die Kategoriezuordnung danach am
    # Trigger scheitert.
    resolved_category_id = await resolve_category_id(db, household_id, category_id)

    payee = payee_label or tx.payee_label

    if payee_label is not None:
        await db.execute(
            text("update transactions set payee_label = :payee where id = :tx_id"),
            {"payee": payee, "tx_id": str(transaction_id)},
        )

    if resolved_category_id is not None:
        # Herkunft: "Uebernehmen" bestaetigt in der Regel einen Vorschlag.
        # Statt dem Client zu glauben, wird nachgesehen, ob es tatsaechlich
        # einen Entscheid mit genau dieser Kategorie gibt - nur dann ist eine
        # andere Herkunft als 'manual' eine belegte Aussage, sonst war es eine
        # eigene Wahl des Nutzers. Uebernommen wird auch die Herkunft des
        # Entscheids: ein Vorschlag aus dem Bestand (Vorfilter fuer bekannte
        # Empfaenger) ist kein Modellergebnis und wird nicht als solches
        # protokolliert.
        assignment = None
        if from_proposal:
            decision = await provenance_service.find_matching_decision(
                db, household_id, transaction_id, resolved_category_id
            )
            if decision is not None:
                decision_id, confidence, decision_source = decision
                assignment = provenance_service.decision_assignment(
                    resolved_category_id,
                    decision_id,
                    confidence,
                    decision_source=decision_source,
                    actor_user_id=actor_user_id,
                )
        if assignment is None:
            assignment = provenance_service.manual_assignment(
                resolved_category_id, actor_user_id
            )

        await db.execute(
            text(
                "update transaction_splits set "
                f"{provenance_service.CATEGORY_ASSIGNMENT_SET_SQL} "
                "where transaction_id = :tx_id"
            ),
            {"tx_id": str(transaction_id), **assignment},
        )

    if create_rule and resolved_category_id:
        purpose = (tx.purpose_raw or tx.note or "").strip() if rule_match_purpose else None
        rule_id = await _create_review_rule(
            db,
            household_id,
            actor_user_id,
            payee or "",
            resolved_category_id,
            rule_name,
            match_purpose=purpose,
            match_exact_amount=int(tx.amount_minor) if rule_match_exact_amount else None,
            match_account_id=tx.account_id if rule_match_account else None,
            match_payee=rule_match_payee,
        )
        if apply_rule_to_existing and rule_id is not None:
            rule = SimpleNamespace(
                id=rule_id,
                name=rule_name or f"Regel fuer {payee or 'passende Buchungen'}",
                match_payee_raw=(payee or "") if rule_match_payee else None,
                match_purpose=purpose,
                match_min_amount_minor=(
                    int(tx.amount_minor) if rule_match_exact_amount else None
                ),
                match_max_amount_minor=(
                    int(tx.amount_minor) if rule_match_exact_amount else None
                ),
                match_account_id=tx.account_id if rule_match_account else None,
                action_category_id=resolved_category_id,
                action_set_transfer=False,
            )
            await _evaluate_review_rule(
                db, household_id, rule, resolved_category_id, write=True
            )


@router.post("/accept")
async def accept_review_item(
    payload: ReviewAcceptInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> dict[str, str]:
    db, household_id = session_ctx

    if isinstance(payload, AcceptUnclassified):
        await _resolve_unclassified(
            db,
            household_id,
            user.id,
            payload.transaction_id,
            payload.category_id,
            None,
            payload.create_rule,
            payload.rule_name,
            # "Uebernehmen" bestaetigt den angezeigten Vorschlag - deshalb wird
            # nach einem passenden KI-Entscheid gesucht.
            from_proposal=True,
        )
        return {"status": "accepted"}

    if isinstance(payload, AcceptDuplicate):
        resolved = await mark_import_row_skipped(db, household_id, payload.import_row_id)
        if not resolved:
            raise HTTPException(status_code=404, detail="Dublette nicht gefunden.")
        return {"status": "accepted"}

    if isinstance(payload, AcceptTransfer):
        row = await get_import_row_for_review(
            db, household_id, payload.import_row_id, "transfer_candidate"
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Transfer-Kandidat nicht gefunden.")
        await commit_single_row(db, household_id, row.account_id, row, is_transfer=True)
        if row.duplicate_of_transaction_id:
            await db.execute(
                text(
                    "update transactions set is_transfer = true "
                    "where id = :id and household_id = :household_id"
                ),
                {
                    "id": str(row.duplicate_of_transaction_id),
                    "household_id": str(household_id),
                },
            )
        await maybe_complete_run(db, row.import_run_id)
        return {"status": "accepted"}

    if isinstance(payload, AcceptContractSuggestion):
        await _accept_contract_suggestion(db, household_id, payload)
        return {"status": "accepted"}

    if isinstance(payload, AcceptRecurring):
        # Altpfad: derselbe Servicepfad, nur ohne die neuen Felder. Ein halb
        # ausgerollter Client soll nicht auf 422 laufen.
        await _accept_contract_suggestion(
            db,
            household_id,
            AcceptContractSuggestion(
                type="contract_suggestion",
                source_transaction_id=payload.source_transaction_id,
                category_id=payload.category_id,
            ),
        )
        return {"status": "accepted"}

    if isinstance(payload, AcceptContractDuplicate):
        await _merge_contract_duplicate(db, household_id, user.id, payload)
        return {"status": "accepted"}

    if isinstance(payload, AcceptContractEnded):
        await _answer_contract_ended_check(db, household_id, payload)
        return {"status": "accepted"}

    if isinstance(payload, AcceptContractAmountChange):
        await _confirm_contract_amount_change(db, household_id, payload)
        return {"status": "accepted"}

    raise HTTPException(status_code=400, detail="Unbekannter Review-Typ.")


@router.post("/accept/duplicates", response_model=BulkAcceptDuplicatesResult)
async def accept_duplicates_bulk(
    payload: BulkAcceptDuplicatesInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> BulkAcceptDuplicatesResult:
    """Bestaetigt mehrere moegliche Dubletten in einem Zug.

    Bewusst ein eigener Endpunkt statt einer Schleife im Client: ein Import
    bringt Dutzende Dubletten, und fuenfzig einzelne Requests waeren fuenfzig
    Transaktionen, von denen die Haelfte durchgehen kann und die andere nicht.
    Hier entscheidet EIN Vorgang ueber alles oder nichts.

    Zeilen, die nicht mehr offen sind (schon entschieden, oder der Importlauf
    wurde inzwischen zurueckgenommen), werden gezaehlt statt zu scheitern - der
    Nutzer soll nicht wegen einer veralteten Liste von vorn anfangen.
    """
    db, household_id = session_ctx
    bestaetigt = 0
    for import_row_id in payload.import_row_ids:
        if await mark_import_row_skipped(db, household_id, import_row_id):
            bestaetigt += 1
    return BulkAcceptDuplicatesResult(
        confirmed=bestaetigt, skipped=len(payload.import_row_ids) - bestaetigt
    )


@router.post("/duplicates/assess", response_model=DuplicateAssessmentResult)
async def assess_duplicates_endpoint(
    payload: DuplicateAssessmentInput,
    session_ctx: HouseholdEditorSession,
) -> DuplicateAssessmentResult:
    """Laesst das lokale Modell die gemeldeten Dublettenkandidaten beurteilen.

    Immer 200: ein KI-seitiger Fehlschlag kommt als available=False mit
    Begruendung zurueck, damit die Dublettenliste unveraendert von Hand bedienbar
    bleibt. Die Einschaetzung wird nie angewendet - sie steht neben der Zeile,
    entschieden wird weiter per Knopf.
    """
    db, household_id = session_ctx
    return await duplicate_ai_service.assess_duplicates(
        db, household_id, payload.import_row_ids
    )


@router.post("/correct")
async def correct_review_item(
    payload: ReviewCorrectInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> dict[str, str]:
    db, household_id = session_ctx

    if isinstance(payload, CorrectUnclassified):
        # Die Regelanlage passiert ausschliesslich in _resolve_unclassified()
        # bzw. _create_review_rule(). Hier stand frueher ein zweiter
        # Insert-Block, der bei gesetztem payee_label eine identische zweite
        # Regel erzeugt hat.
        await _resolve_unclassified(
            db,
            household_id,
            user.id,
            payload.transaction_id,
            payload.category_id,
            payload.payee_label,
            payload.create_rule_always,
            None,
            # Korrigieren heisst: der Nutzer waehlt bewusst etwas anderes als
            # vorgeschlagen. Das ist immer eine manuelle Zuordnung.
            from_proposal=False,
            rule_match_payee=payload.rule_match_payee,
            rule_match_purpose=payload.rule_match_purpose,
            rule_match_exact_amount=payload.rule_match_exact_amount,
            rule_match_account=payload.rule_match_account,
            apply_rule_to_existing=payload.apply_rule_to_existing,
        )
        return {"status": "corrected"}

    if isinstance(payload, CorrectDuplicate):
        row = await get_import_row_for_review(
            db, household_id, payload.import_row_id, "duplicate_possible"
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Dublette nicht gefunden.")
        # Ausdruecklich hier und nicht erst in commit_single_row(): eine
        # ungueltige Kategorie soll den Vorgang abbrechen, bevor die
        # Staging-Zeile in eine Buchung ueberfuehrt wird.
        resolved_category_id = await resolve_category_id(
            db, household_id, payload.category_id, allow_none=False
        )
        await commit_single_row(
            db,
            household_id,
            row.account_id,
            row,
            category_id=resolved_category_id,
            committed_by=user.id,
        )
        await maybe_complete_run(db, row.import_run_id)
        return {"status": "corrected"}

    if isinstance(payload, CorrectTransfer):
        row = await get_import_row_for_review(
            db, household_id, payload.import_row_id, "transfer_candidate"
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Transfer-Kandidat nicht gefunden.")
        await commit_single_row(db, household_id, row.account_id, row, is_transfer=False)
        await maybe_complete_run(db, row.import_run_id)
        return {"status": "corrected"}

    raise HTTPException(status_code=400, detail="Unbekannter Review-Typ.")


async def _accept_contract_suggestion(
    db: AsyncSession, household_id: UUID, payload: AcceptContractSuggestion
) -> None:
    """Bestaetigt oder verneint einen erkannten Vertrag.

    Der Kandidat wird unmittelbar vor dem Speichern erneut berechnet: der
    Client schickt nur die Ankerbuchung, nie Betrag, Rhythmus oder Empfaenger.
    """
    candidate = await contract_detection_service.find_candidate(
        db, household_id, payload.source_transaction_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail=_CONTRACT_SUGGESTION_NOT_FOUND)

    if payload.dismiss:
        # "Kein Vertrag": als Zeile mit status='not_a_contract' festgehalten,
        # sonst stuende derselbe Vorschlag nach dem naechsten Import wieder da.
        await contract_detection_service.dismiss_candidate(db, household_id, candidate)
        return

    # Ein Vertrag wirkt in die Zukunft - die Kategorie muss aktiv und im
    # Haushalt sein, sonst faellt der Fehler erst beim naechsten Faelligwerden
    # auf.
    resolved_category_id = await resolve_category_id(
        db, household_id, payload.category_id, allow_none=False
    )
    if resolved_category_id is None:  # pragma: no cover - allow_none=False schliesst das aus
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden.")

    contract_id = await contract_detection_service.create_contract_from_candidate(
        db,
        household_id,
        candidate,
        category_id=resolved_category_id,
        name=payload.name,
        contract_type=payload.contract_type,
    )
    # Rueckwirkender Erstlauf: ohne ihn ist die Detailseite des gerade
    # bestaetigten Vertrags leer, obwohl die Buchungen genau die sind, aus
    # denen er erkannt wurde.
    await contract_matching_service.backfill_contract(db, household_id, contract_id)


# Deterministisch aus dem Paar gebildet: ein doppelt abgeschickter Klick trifft
# denselben Wert und wird von `find_merge_by_request_id` abgefangen.
_DUPLICATE_MERGE_REQUEST_PREFIX = "review-duplicate"


async def _merge_contract_duplicate(
    db: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID,
    payload: AcceptContractDuplicate,
) -> None:
    """Fuehrt doppelt gefuehrte Vertraege zusammen - oder merkt die Verneinung.

    Ohne das Merken der Verneinung stuende dasselbe Paar nach jedem
    Inbox-Aufruf erneut da. Festgehalten wird es als `dismissed`-Ereignis am
    Zielvertrag; eine eigene Tabelle braucht es dafuer nicht.
    """
    if await contract_service.load_contract(db, household_id, payload.target_id) is None:
        raise HTTPException(status_code=404, detail="Vertrag nicht gefunden.")

    request_id = "{}:{}:{}".format(
        _DUPLICATE_MERGE_REQUEST_PREFIX,
        payload.target_id,
        ",".join(sorted(str(item) for item in payload.source_ids)),
    )
    # Vor der Existenzpruefung der Quellen: die Zusammenfuehrung LOESCHT sie.
    # Ein doppelt abgeschickter Klick wuerde sonst nicht auf die Idempotenz
    # laufen, sondern auf ein 404 fuer den gerade selbst entfernten Vertrag.
    if await contract_service.find_merge_by_request_id(
        db, household_id, payload.target_id, request_id
    ):
        return

    for source_id in payload.source_ids:
        if await contract_service.load_contract(db, household_id, source_id) is None:
            raise HTTPException(status_code=404, detail="Vertrag nicht gefunden.")

    if payload.dismiss:
        for source_id in payload.source_ids:
            await contract_service.record_event(
                db,
                household_id,
                payload.target_id,
                "dismissed",
                payload={
                    "duplicate_of": str(source_id),
                    "reason": "review_inbox",
                },
            )
        return

    _, _, _, merge_id = await contract_service.apply_merge(
        db,
        household_id,
        payload.target_id,
        list(payload.source_ids),
        request_id,
        actor_user_id=actor_user_id,
    )
    await record_audit_event(
        db,
        household_id,
        actor_user_id,
        contract_service.CONTRACT_ENTITY_TYPE,
        payload.target_id,
        "merge",
        metadata={
            "source_ids": [str(item) for item in payload.source_ids],
            "request_id": request_id,
            "merge_id": str(merge_id),
            "origin": "review_inbox",
        },
    )



async def _answer_contract_ended_check(
    db: AsyncSession, household_id: UUID, payload: AcceptContractEnded
) -> None:
    """Beantwortet die Inbox-Frage "laeuft dieser Vertrag noch?".

    Bewusst derselbe Update- und Ereignispfad wie POST /v1/contracts/{id}/status,
    aber ohne `If-Match`: die Inbox zeigt drei Antwortknoepfe und keine
    bearbeitbaren Felder, an denen sich zwei Nutzer ueberschreiben koennten.
    """
    contract = await contract_service.load_contract(db, household_id, payload.contract_id)
    if contract is None:
        # load_contract statt require_contract: der Fehlertext soll wie in den
        # uebrigen Review-Zweigen aus dieser Datei kommen.
        raise HTTPException(status_code=404, detail="Vertrag nicht gefunden.")

    contract_service.assert_status_transition(contract.status, payload.action)

    resumed = payload.action == "active"
    next_due = contract.next_due_date
    if resumed:
        # Die offene Faelligkeit liegt mehrere Perioden zurueck - genau deshalb
        # stand der Vertrag in der Inbox. Ohne Vorziehen wuerde der naechste
        # Abgleich dieselben Perioden erneut als ausgeblieben zaehlen und die
        # Frage sofort wieder stellen. Obergrenze wie in
        # contract_service.build_forecast, damit ein sehr alter Wochenvertrag
        # die Schleife nicht sprengt.
        today = date.today()
        guard = 0
        while next_due < today and guard < 240:
            next_due = contract_service.next_due_date(
                next_due,
                contract.frequency,
                due_rule=contract.due_rule,
                due_day=contract.due_day,
            )
            guard += 1

    await db.execute(
        text(
            "update contracts set status = :status, next_due_date = :next_due_date, "
            # Nur "laeuft weiter" und "pausiert" loeschen den Fehlzaehler. Bei
            # 'ended' bleibt er stehen: er ist die Begruendung fuer das Ende.
            "missed_periods = :missed_periods "
            "where id = :id and household_id = :household_id"
        ),
        {
            "id": str(payload.contract_id),
            "household_id": str(household_id),
            "status": payload.action,
            "next_due_date": next_due,
            "missed_periods": (contract.missed_periods if payload.action == "ended" else 0),
        },
    )

    event_payload: dict[str, Any] = {
        "status_from": contract.status,
        "status_to": payload.action,
        # Unterscheidet die Nutzerantwort von einem Ende, das die Automatik
        # nach zu vielen Fehlperioden selbst gezogen hat.
        "reason": "user_confirmed",
        "missed_periods": contract.missed_periods,
    }
    if resumed:
        event_payload["next_due_date"] = next_due
        kind = "payment_resumed"
    else:
        # contract_events.kind kennt kein 'paused'; fuer denselben Zielstatus
        # schreibt auch POST /v1/contracts/{id}/status ein 'reactivated'.
        kind = "ended" if payload.action == "ended" else "reactivated"
    await contract_service.record_event(
        db, household_id, payload.contract_id, kind, payload=event_payload
    )


_AMOUNT_CHANGE_NOT_FOUND = "Betragsaenderung nicht (mehr) gefunden."


async def _confirm_contract_amount_change(
    db: AsyncSession, household_id: UUID, payload: AcceptContractAmountChange
) -> None:
    """Beantwortet die Inbox-Frage "der Betrag hat sich geaendert - stimmt das?".

    AUSDRUECKLICH FESTGEHALTEN (Plan Abschnitt 19.11): die Bestaetigung setzt
    `contract_events.acknowledged_at` - dieselbe Spalte, die der naechtliche
    Push-Filter liest (`worker/notifications/contract_alerts.py`,
    `_PENDING_QUERY`: "and ce.acknowledged_at is null"). Wer die Aenderung hier
    bestaetigt, bekommt abends KEINE Push-Nachricht mehr dazu. Das ist gewollt
    und keine Nebenwirkung: es ist dieselbe Tatsache, und ein Hinweis auf dem
    Sperrbildschirm zu etwas, das der Nutzer in der App bereits beantwortet
    hat, ist der schnellste Weg, den ganzen Kanal abzuschalten.

    Zahlenwirksam passiert hier nichts: Betrag, Toleranz und Preisstufe hat der
    Abgleich bereits geschrieben, die Antwort ist reine Kenntnisnahme. Es
    entsteht deshalb auch kein neues Ereignis (`contract_events.kind` kennt
    keine Bestaetigungsart) und kein Audit-Eintrag - die bestaetigte Zeile
    selbst IST der Nachweis, mit Zeitpunkt.

    Der Gegenweg 'wrong_amount' fehlt bewusst; die Begruendung steht am
    Eingabemodell `AcceptContractAmountChange`.
    """
    # Der Vertrag wird mitgeprueft: ein zusammengefuehrter Vertrag ist ueberall
    # sonst unsichtbar und steht auch nicht in der Inbox - eine Bestaetigung
    # dazu kaeme nur aus einer veralteten Ansicht.
    found = (
        await db.execute(
            text(
                "select ce.acknowledged_at is not null as acknowledged "
                "from contract_events ce "
                "join contracts c "
                "  on c.id = ce.contract_id and c.household_id = ce.household_id "
                "where ce.id = :event_id and ce.household_id = :household_id "
                "and ce.kind = 'price_change' and c.merged_into_id is null"
            ),
            {"event_id": str(payload.event_id), "household_id": str(household_id)},
        )
    ).first()
    if found is None:
        raise HTTPException(status_code=404, detail=_AMOUNT_CHANGE_NOT_FOUND)
    if found.acknowledged:
        # Idempotent: ein zweiter Klick auf denselben Eintrag ist keine
        # Fehlbedienung, sondern eine langsame Verbindung. Der bereits
        # gespeicherte Zeitpunkt bleibt stehen - er gehoert zur ersten Antwort.
        return

    await db.execute(
        text(
            "update contract_events set acknowledged_at = now() "
            "where id = :event_id and household_id = :household_id "
            "and acknowledged_at is null"
        ),
        {"event_id": str(payload.event_id), "household_id": str(household_id)},
    )


# Das frueher hier stehende `_next_due_date` ist entfallen: sein
# Monatsmapping kannte kein 'semiannual' und waere bei einem halbjaehrlichen
# Vertrag mit KeyError gescheitert. Zustaendig ist
# `contract_service.next_due_date` - es kennt alle fuenf Frequenzen und
# beachtet zusaetzlich `due_rule`.
