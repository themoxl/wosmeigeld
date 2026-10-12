"""API-Endpunkte fuer Klassifikationsregeln (Phase 4).

Die frueheren Fixkosten- und Abo-Endpunkte `/recurring` und `/subscriptions`
liegen weiterhin hier, sind aber seit 0014 nur noch eine veraltete Sicht auf
`contracts` (siehe routes/contracts.py).
"""

from datetime import date
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.data import rule_packages
from app.schemas.contract import Contract
from app.schemas.rules import (
    ClassificationRule,
    ClassificationRuleCreate,
    ClassificationRuleUpdate,
    RecurringRule,
    RecurringRuleCreate,
    RuleApplyAllInput,
    RuleApplyAllResult,
    RuleApplyInput,
    RuleApplyResult,
    RuleApplyRuleSummary,
    RuleExportInput,
    RuleImportInput,
    RuleImportPreview,
    RuleImportResult,
    RulePackage,
    RulePackageMeta,
    Subscription,
    SubscriptionCreate,
)
from app.services import (
    category_service,
    contract_service,
    rule_import_export_service,
    rules_service,
)
from app.services.audit_service import (
    RULE_ENTITY_TYPE,
    RULE_SNAPSHOT_COLUMNS,
    fetch_rule_snapshot,
    record_audit_event,
    record_rule_event,
)

router = APIRouter(prefix="", tags=["rules"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Schreibende Endpunkte: 403 statt stillem Nichtstun, wenn die Rolle nicht
# schreiben darf (siehe app/auth/dependencies.py).
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

# Felder, die PATCH /rules/{id} schreiben darf. Bewusst eine feste Liste und
# nicht die Payload-Schluessel, damit kein Feldname ungeprueft in das
# SET-Fragment wandert.
_RULE_UPDATABLE_COLUMNS = (
    "name",
    "priority",
    "is_active",
    "match_payee_raw",
    "match_payee_exact_word",
    "match_purpose",
    "match_purpose_exact_word",
    "exclude_payee_raw",
    "exclude_purpose",
    "match_min_amount_minor",
    "match_max_amount_minor",
    "match_amount_mode",
    "match_account_id",
    "action_payee_id",
    "action_category_id",
    "action_set_transfer",
    "action_note",
    "action_reimbursable",
    "action_expectation_mode",
    "action_expectation_rate",
    "action_expectation_minor",
)

_DUPLICATE_RULE_DETAIL = (
    "Es existiert bereits eine Regel fuer diesen Haendler und diese Kategorie."
)


# ---------- Klassifikationsregeln --------------------------------------------

@router.get("/rules", response_model=list[ClassificationRule])
async def list_rules(
    session_ctx: HouseholdSession,
) -> list[ClassificationRule]:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            f"select {RULE_SNAPSHOT_COLUMNS} "
            "from classification_rules "
            "where household_id = :household_id order by priority desc, created_at asc"
        ),
        {"household_id": str(household_id)},
    )
    return [ClassificationRule(**row._mapping) for row in res]


@router.post("/rules", response_model=ClassificationRule, status_code=201)
async def create_rule(
    payload: ClassificationRuleCreate,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ClassificationRule:
    db, household_id = session_ctx
    # Eine Regel schreibt kuenftig auf diese Kategorie - sie muss deshalb dem
    # Haushalt gehoeren und aktiv sein (zentraler Resolver, siehe
    # app/services/category_service.py).
    action_category_id = await category_service.resolve_category_id(
        db, household_id, payload.action_category_id
    )
    try:
        res = await db.execute(
            text(
                "insert into classification_rules (household_id, name, priority, is_active, "
                "match_payee_raw, match_payee_exact_word, match_purpose, match_purpose_exact_word, "
                "exclude_payee_raw, exclude_purpose, match_min_amount_minor, "
                "match_max_amount_minor, match_amount_mode, match_account_id, "
                "action_payee_id, action_category_id, action_set_transfer, action_note, "
                "action_reimbursable, action_expectation_mode, "
                "action_expectation_rate, action_expectation_minor, source) "
                "values (:household_id, :name, :priority, :is_active, :match_payee_raw, "
                ":match_payee_exact_word, :match_purpose, :match_purpose_exact_word, "
                ":exclude_payee_raw, :exclude_purpose, :match_min_amount_minor, "
                ":match_max_amount_minor, :match_amount_mode, :match_account_id, "
                ":action_payee_id, :action_category_id, :action_set_transfer, :action_note, "
                ":action_reimbursable, :action_expectation_mode, "
                ":action_expectation_rate, :action_expectation_minor, :source) "
                f"returning {RULE_SNAPSHOT_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "name": payload.name,
                "priority": payload.priority,
                "is_active": payload.is_active,
                "match_payee_raw": payload.match_payee_raw,
                "match_payee_exact_word": payload.match_payee_exact_word,
                "match_purpose": payload.match_purpose,
                "match_purpose_exact_word": payload.match_purpose_exact_word,
                "exclude_payee_raw": payload.exclude_payee_raw,
                "exclude_purpose": payload.exclude_purpose,
                "match_min_amount_minor": payload.match_min_amount_minor,
                "match_max_amount_minor": payload.match_max_amount_minor,
                "match_amount_mode": payload.match_amount_mode,
                "match_account_id": (
                    str(payload.match_account_id) if payload.match_account_id else None
                ),
                "action_payee_id": (
                    str(payload.action_payee_id) if payload.action_payee_id else None
                ),
                "action_category_id": (
                    str(action_category_id) if action_category_id else None
                ),
                "action_set_transfer": payload.action_set_transfer,
                "action_note": payload.action_note,
                "action_reimbursable": payload.action_reimbursable,
                "action_expectation_mode": payload.action_expectation_mode,
                "action_expectation_rate": payload.action_expectation_rate,
                "action_expectation_minor": payload.action_expectation_minor,
                "source": payload.source,
            },
        )
    except IntegrityError as exc:
        # Partieller Unique-Index classification_rules_payee_category_uniq -
        # eine gleichartige einfache Haendler/Kategorie-Regel gibt es schon.
        raise HTTPException(status_code=409, detail=_DUPLICATE_RULE_DETAIL) from exc

    snapshot = dict(res.one()._mapping)
    await record_rule_event(
        db, household_id, user.id, snapshot["id"], "create", after=snapshot
    )
    return ClassificationRule(**snapshot)


@router.get("/rules/packages", response_model=list[RulePackageMeta])
async def list_rule_packages(
    session_ctx: HouseholdSession,
) -> list[RulePackageMeta]:
    """Die eingebauten Pakete samt der Zahl bereits vorhandener Regeln - ohne
    sie sieht man erst nach dem Oeffnen, dass ein Paket schon installiert
    ist."""
    db, household_id = session_ctx
    installed = await rule_import_export_service.package_install_counts(db, household_id)
    return [
        RulePackageMeta(**pkg, installed_count=installed.get(pkg["id"], 0))
        for pkg in rule_packages.list_package_metas()
    ]


@router.get("/rules/packages/{package_id}", response_model=RulePackage)
async def get_rule_package(
    package_id: str,
    session_ctx: HouseholdSession,
) -> RulePackage:
    pkg = rule_packages.get_package(package_id)
    if not pkg:
        raise HTTPException(status_code=404, detail="Regelpaket nicht gefunden.")
    return RulePackage(**pkg)


@router.post("/rules/import/preview", response_model=RuleImportPreview)
async def preview_rule_import(
    payload: RuleImportInput,
    session_ctx: HouseholdEditorSession,
) -> RuleImportPreview:
    db, household_id = session_ctx
    return await rule_import_export_service.preview_import(
        db, household_id, payload.package, payload.category_overrides
    )


@router.post("/rules/import", response_model=RuleImportResult)
async def execute_rule_import(
    payload: RuleImportInput,
    session_ctx: HouseholdEditorSession,
) -> RuleImportResult:
    db, household_id = session_ctx
    return await rule_import_export_service.execute_import(
        db,
        household_id,
        payload.package,
        payload.category_overrides,
        payload.skip_duplicates,
        payload.skip_indices,
    )


@router.post("/rules/export", response_model=RulePackage)
async def export_rule_package(
    payload: RuleExportInput,
    session_ctx: HouseholdEditorSession,
) -> RulePackage:
    db, household_id = session_ctx
    return await rule_import_export_service.export_rules(
        db, household_id, payload.rule_ids, payload.package_name, payload.description
    )


# ---------- Sammellauf ueber alle Regeln --------------------------------------
# Nach einem Paketimport stuenden sonst vierzig frische Regeln da, die der
# Nutzer einzeln auf den Bestand anwenden muesste. Der Sammellauf nimmt alle
# auf einmal und entscheidet je Buchung wie der Import: hoechste Prioritaet
# zuerst, die erste passende Regel gewinnt.
#
# Die beiden Routen stehen bewusst VOR den `/rules/{rule_id}`-Routen: "apply-all"
# ist keine UUID, und bei umgekehrter Reihenfolge fienge `/rules/{rule_id}/apply`
# den Pfad ab und antwortete 422.

_RULE_EXCLUDED_INACTIVE = "inactive"
_RULE_EXCLUDED_NO_EFFECT = "no_effect"
_RULE_EXCLUDED_CATEGORY = "category_inactive"

_BULK_RULE_COLUMNS = (
    "id, name, priority, is_active, match_payee_raw, match_payee_exact_word, "
    "match_purpose, match_purpose_exact_word, exclude_payee_raw, exclude_purpose, "
    "match_min_amount_minor, match_max_amount_minor, match_amount_mode, "
    "match_account_id, action_category_id, action_set_transfer, "
    "action_reimbursable, action_expectation_mode, "
    "action_expectation_rate, action_expectation_minor"
)


async def _prepare_rules_for_bulk_run(
    db: AsyncSession, household_id: UUID, *, include_inactive: bool
) -> tuple[list[rules_service.PreparedRule], list[RuleApplyRuleSummary]]:
    """Trennt die laufbereiten Regeln von denen, die nichts bewirken koennen.

    Eine archivierte Zielkategorie nimmt hier nur die betroffene Regel aus dem
    Lauf - anders als im Einzellauf, wo sie den ganzen Aufruf mit 409 beendet.
    Der Nutzer hat den gesamten Bestand angestossen; eine einzelne unbrauchbare
    Regel darf ihn nicht anhalten, muss im Ergebnis aber benannt werden."""
    res = await db.execute(
        text(
            f"select {_BULK_RULE_COLUMNS} from classification_rules "
            "where household_id = :household_id "
            "order by priority desc, created_at asc"
        ),
        {"household_id": str(household_id)},
    )

    prepared: list[rules_service.PreparedRule] = []
    summaries: list[RuleApplyRuleSummary] = []
    for row in res:
        summary = RuleApplyRuleSummary(
            rule_id=row.id,
            rule_name=row.name,
            priority=row.priority,
            matched=0,
            applied=0,
            unchanged=0,
            skipped=0,
            conflict=0,
        )
        if not row.is_active and not include_inactive:
            summary.excluded_reason = _RULE_EXCLUDED_INACTIVE
        elif row.action_category_id is None and not row.action_set_transfer:
            summary.excluded_reason = _RULE_EXCLUDED_NO_EFFECT
        else:
            try:
                target_category_id = await category_service.resolve_category_id(
                    db, household_id, row.action_category_id
                )
            except HTTPException:
                summary.excluded_reason = _RULE_EXCLUDED_CATEGORY
            else:
                prepared.append(rules_service.PreparedRule(row, target_category_id))
        summaries.append(summary)

    return prepared, summaries


async def _run_bulk_rule_application(
    db: AsyncSession,
    household_id: UUID,
    payload: RuleApplyAllInput,
    *,
    write: bool,
    actor_user_id: UUID,
) -> RuleApplyAllResult:
    prepared, summaries = await _prepare_rules_for_bulk_run(
        db, household_id, include_inactive=payload.include_inactive
    )

    items, counters, scanned, truncated = await rules_service.apply_all_rules_to_existing(
        db,
        household_id,
        prepared,
        scope=payload.scope,
        from_date=payload.from_date,
        to_date=payload.to_date,
        limit=payload.limit,
        write=write,
    )

    for summary in summaries:
        counts = counters.get(summary.rule_id)
        if counts is None:
            continue
        summary.matched = counts["matched"]
        summary.applied = counts["applied"]
        summary.unchanged = counts["unchanged"]
        summary.skipped = counts["skipped"]
        summary.conflict = counts["conflict"]

    if write:
        # Ein Audit-Ereignis je Regel, nicht je Buchung - dieselbe Linie faehrt
        # der Einzellauf (siehe _run_rule_application).
        applied_by_rule: dict[UUID, list[str]] = {}
        for item in items:
            if item.status == "applied" and item.rule_id is not None:
                applied_by_rule.setdefault(item.rule_id, []).append(
                    str(item.transaction_id)
                )
        for rule_id, transaction_ids in applied_by_rule.items():
            await record_audit_event(
                db,
                household_id,
                actor_user_id,
                RULE_ENTITY_TYPE,
                rule_id,
                "rule_apply",
                metadata={
                    "bulk": True,
                    "scope": payload.scope,
                    "from_date": payload.from_date,
                    "to_date": payload.to_date,
                    "applied": len(transaction_ids),
                    "transaction_ids": transaction_ids,
                },
            )

    return RuleApplyAllResult(
        scope=payload.scope,
        dry_run=not write,
        rules_total=len(summaries),
        rules_evaluated=len(prepared),
        transactions_scanned=scanned,
        matched=sum(summary.matched for summary in summaries),
        applied=sum(summary.applied for summary in summaries),
        unchanged=sum(summary.unchanged for summary in summaries),
        skipped=sum(summary.skipped for summary in summaries),
        conflict=sum(summary.conflict for summary in summaries),
        truncated=truncated,
        rules=summaries,
        items=items,
    )


@router.post("/rules/apply-all/preview", response_model=RuleApplyAllResult)
async def preview_all_rules_application(
    payload: RuleApplyAllInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> RuleApplyAllResult:
    """Zeigt, was ein Lauf ueber alle Regeln taete - ohne zu schreiben."""
    db, household_id = session_ctx
    return await _run_bulk_rule_application(
        db, household_id, payload, write=False, actor_user_id=user.id
    )


@router.post("/rules/apply-all", response_model=RuleApplyAllResult)
async def apply_all_rules_to_existing_transactions(
    payload: RuleApplyAllInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> RuleApplyAllResult:
    """Wendet alle laufbereiten Regeln auf den Buchungsbestand an."""
    db, household_id = session_ctx
    return await _run_bulk_rule_application(
        db, household_id, payload, write=True, actor_user_id=user.id
    )


@router.patch("/rules/{rule_id}", response_model=ClassificationRule)
async def update_rule(
    rule_id: UUID,
    payload: ClassificationRuleUpdate,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ClassificationRule:
    """Teilaktualisierung einer Regel. Geschrieben werden nur ausdruecklich
    mitgeschickte Felder (exclude_unset), damit ein weggelassenes Feld nicht
    faelschlich auf null gesetzt wird. Vorher-/Nachher-Zustand landen im
    Audit-Log, die Versionsnummer zaehlt der Datenbank-Trigger hoch."""
    db, household_id = session_ctx

    before = await fetch_rule_snapshot(db, household_id, rule_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden.")

    changes = payload.model_dump(exclude_unset=True)
    if "action_category_id" in changes:
        changes["action_category_id"] = await category_service.resolve_category_id(
            db, household_id, changes["action_category_id"]
        )
    params: dict[str, Any] = {"id": str(rule_id), "household_id": str(household_id)}
    assignments: list[str] = []
    for column in _RULE_UPDATABLE_COLUMNS:
        if column not in changes:
            continue
        value = changes[column]
        assignments.append(f"{column} = :{column}")
        params[column] = str(value) if isinstance(value, UUID) else value

    if not assignments:
        # Leerer Patch: keine Aenderung, keine neue Version, kein Audit-Eintrag.
        return ClassificationRule(**before)

    try:
        res = await db.execute(
            text(
                "update classification_rules set " + ", ".join(assignments) + " "
                "where id = :id and household_id = :household_id "
                f"returning {RULE_SNAPSHOT_COLUMNS}"
            ),
            params,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=_DUPLICATE_RULE_DETAIL) from exc

    after = dict(res.one()._mapping)
    await record_rule_event(
        db, household_id, user.id, rule_id, "update", before=before, after=after
    )
    return ClassificationRule(**after)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: UUID,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> None:
    db, household_id = session_ctx
    before = await fetch_rule_snapshot(db, household_id, rule_id)
    res = await db.execute(
        text("delete from classification_rules where id = :id and household_id = :household_id"),
        {"id": str(rule_id), "household_id": str(household_id)},
    )
    if cast(CursorResult[Any], res).rowcount == 0:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden.")
    await record_rule_event(db, household_id, user.id, rule_id, "delete", before=before)


# ---------- Rueckwirkende Regelanwendung -------------------------------------
# Klassifikationsregeln greifen sonst ausschliesslich beim Import
# (import_service.commit_single_row). Eine neu angelegte Regel liess den
# Bestand damit unberuehrt - genau die Luecke, die Firefly III mit "apply rule
# to existing transactions" schliesst.

_RULE_WITHOUT_EFFECT = (
    "Die Regel setzt weder eine Zielkategorie noch das Transferkennzeichen "
    "und kann deshalb nichts am Bestand aendern."
)


async def _load_rule_for_apply(
    db: AsyncSession, household_id: UUID, rule_id: UUID
) -> Any:
    row = (
        await db.execute(
            text(
                "select id, name, match_payee_raw, match_payee_exact_word, match_purpose, "
                "match_purpose_exact_word, exclude_payee_raw, exclude_purpose, "
                "match_min_amount_minor, match_max_amount_minor, match_amount_mode, "
                "match_account_id, action_category_id, action_set_transfer, "
                "action_reimbursable, action_expectation_mode, "
                "action_expectation_rate, action_expectation_minor "
                "from classification_rules "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(rule_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden.")
    if row.action_category_id is None and not row.action_set_transfer:
        raise HTTPException(status_code=422, detail=_RULE_WITHOUT_EFFECT)
    return row


async def _run_rule_application(
    db: AsyncSession,
    household_id: UUID,
    rule_id: UUID,
    payload: RuleApplyInput,
    *,
    write: bool,
    actor_user_id: UUID,
) -> RuleApplyResult:
    rule = await _load_rule_for_apply(db, household_id, rule_id)
    # Eine unbekannte oder archivierte Zielkategorie ist ein Fehler des ganzen
    # Laufs (404/409) und kein Grund, hunderte Buchungen einzeln zu
    # ueberspringen - dieselbe Linie faehrt die Mehrfachzuordnung.
    target_category_id = await category_service.resolve_category_id(
        db, household_id, rule.action_category_id
    )

    items, matched, truncated = await rules_service.apply_rule_to_existing(
        db,
        household_id,
        rule,
        target_category_id=target_category_id,
        scope=payload.scope,
        from_date=payload.from_date,
        to_date=payload.to_date,
        limit=payload.limit,
        write=write,
    )

    applied = [item for item in items if item.status == "applied"]
    if write and applied:
        # Die Regel ist die auditierte Entitaet, die veraenderten Buchungen
        # haengen als Metadaten daran. Ein Ereignis je Buchung wuerde das Log
        # bei 500 Treffern unlesbar machen.
        await record_audit_event(
            db,
            household_id,
            actor_user_id,
            RULE_ENTITY_TYPE,
            rule_id,
            "rule_apply",
            metadata={
                "scope": payload.scope,
                "from_date": payload.from_date,
                "to_date": payload.to_date,
                "matched": matched,
                "applied": len(applied),
                "transaction_ids": [str(item.transaction_id) for item in applied],
            },
        )

    return RuleApplyResult(
        rule_id=rule_id,
        rule_name=rule.name,
        scope=payload.scope,
        dry_run=not write,
        matched=matched,
        applied=len(applied),
        unchanged=sum(1 for item in items if item.status == "unchanged"),
        skipped=sum(1 for item in items if item.status == "skipped"),
        conflict=sum(1 for item in items if item.status == "conflict"),
        truncated=truncated,
        items=items,
    )


@router.post("/rules/{rule_id}/apply/preview", response_model=RuleApplyResult)
async def preview_rule_application(
    rule_id: UUID,
    payload: RuleApplyInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> RuleApplyResult:
    """Zeigt je Buchung, was ein rueckwirkender Lauf taete - ohne zu schreiben."""
    db, household_id = session_ctx
    return await _run_rule_application(
        db, household_id, rule_id, payload, write=False, actor_user_id=user.id
    )


@router.post("/rules/{rule_id}/apply", response_model=RuleApplyResult)
async def apply_rule_to_existing_transactions(
    rule_id: UUID,
    payload: RuleApplyInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> RuleApplyResult:
    """Wendet die Regel auf bereits gebuchte Transaktionen an.

    Geschrieben werden Kategorie und Transferkennzeichen, jede Buchung mit
    eigener Rueckmeldung. Auch inaktive Regeln lassen sich anwenden: `is_active`
    steuert die automatische Auswertung beim Import, nicht die ausdrueckliche
    Anweisung des Nutzers, genau diese Regel jetzt laufen zu lassen."""
    db, household_id = session_ctx
    return await _run_rule_application(
        db, household_id, rule_id, payload, write=True, actor_user_id=user.id
    )


# ---------- Kompatibilitaetsschicht fuer Fixkosten und Abos ------------------
# Seit 0014 gibt es genau ein Vertragsobjekt (`contracts`, siehe
# routes/contracts.py und docs/VERTRAEGE-FIXKOSTEN-PLAN.md). Diese vier
# Endpunkte bleiben nur bestehen, damit ausgelieferte Mobile-Versionen
# weiterlaufen; sie lesen und schreiben ausschliesslich `contracts`. Die
# Tabellen `recurring_rules` und `subscriptions` werden nicht mehr angefasst.
#
# Die Aufteilung ist ueberschneidungsfrei: /recurring liefert alles ausser
# Abonnements, /subscriptions nur Abonnements. Sonst wuerde die alte App
# denselben Vertrag doppelt in ihre Monatssumme rechnen.

_LEGACY_ACTIVE_STATUSES = ("active", "paused", "cancelled")


def _legacy_signed_amount(contract: Contract, amount_minor: int) -> int:
    return -amount_minor if contract.direction == "expense" else amount_minor


def _as_legacy_recurring(contract: Contract) -> RecurringRule:
    return RecurringRule(
        id=contract.id,
        household_id=contract.household_id,
        account_id=contract.account_id,
        payee_id=contract.payee_id,
        category_id=contract.category_id,
        expected_amount_minor=_legacy_signed_amount(contract, contract.amount_minor),
        frequency=contract.frequency,
        next_due_date=contract.next_due_date,
        tolerance_days=contract.tolerance_days,
        tolerance_amount_minor=contract.tolerance_amount_minor,
        is_active=contract.status == "active",
        # Der Anzeigename steht jetzt in einem eigenen Feld; die alte App kennt
        # nur `note` und zeigte dort bisher "Erkannt aus Buchungsverlauf: ...".
        note=contract.name,
    )


def _as_legacy_subscription(contract: Contract) -> Subscription:
    # `monthly_cost_minor` und `annual_cost_minor` sind KOSTEN und waren im
    # Altpfad immer positiv - er schrieb die Nutzlast unveraendert in
    # `subscriptions` zurueck. Ein Vorzeichenwechsel wie bei
    # `RecurringRule.expected_amount_minor` (das der Buchungskonvention folgt)
    # wuerde in einer ausgelieferten Mobile-Version negative Abo-Betraege und
    # eine negative Monatssumme anzeigen.
    return Subscription(
        id=contract.id,
        household_id=contract.household_id,
        provider=contract.provider_label or contract.name,
        plan_name=contract.name,
        monthly_cost_minor=contract_service.monthly_minor(
            contract.amount_minor, contract.frequency
        ),
        annual_cost_minor=contract_service.yearly_minor(
            contract.amount_minor, contract.frequency
        ),
        cancellation_period_days=contract.cancellation_period_days,
        next_renewal_date=contract.next_due_date,
        category_id=contract.category_id,
        account_id=contract.account_id,
        is_active=contract.status == "active",
        note=contract.note,
    )


async def _create_legacy_contract(
    db: AsyncSession,
    household_id: UUID,
    *,
    name: str,
    contract_type: str,
    amount_minor_signed: int,
    frequency: str,
    next_due_date: date,
    category_id: UUID | None,
    account_id: UUID | None,
    payee_id: UUID | None = None,
    provider_label: str | None = None,
    tolerance_days: int = 4,
    tolerance_amount_minor: int = 0,
    cancellation_period_days: int | None = None,
    is_active: bool = True,
    note: str | None = None,
) -> Contract:
    resolved_category_id = await category_service.resolve_category_id(
        db, household_id, category_id
    )
    res = await db.execute(
        text(
            "insert into contracts (household_id, name, provider_label, contract_type, "
            "payee_id, account_id, category_id, direction, amount_minor, frequency, "
            "due_rule, due_day, next_due_date, tolerance_days, tolerance_amount_minor, "
            "cancellation_period_days, status, source, note) "
            "values (:household_id, :name, :provider_label, :contract_type, "
            "cast(:payee_id as uuid), cast(:account_id as uuid), cast(:category_id as uuid), "
            ":direction, :amount_minor, :frequency, :due_rule, :due_day, :next_due_date, "
            ":tolerance_days, :tolerance_amount_minor, :cancellation_period_days, "
            ":status, 'manual', :note) "
            "returning id"
        ),
        {
            "household_id": str(household_id),
            "name": name[:160],
            "provider_label": provider_label[:160] if provider_label else None,
            "contract_type": contract_type,
            "payee_id": str(payee_id) if payee_id else None,
            "account_id": str(account_id) if account_id else None,
            "category_id": str(resolved_category_id) if resolved_category_id else None,
            "direction": "expense" if amount_minor_signed < 0 else "income",
            "amount_minor": abs(amount_minor_signed),
            "frequency": frequency,
            "due_rule": "month_end" if next_due_date.day > 28 else "day_of_month",
            "due_day": next_due_date.day,
            "next_due_date": next_due_date,
            "tolerance_days": tolerance_days,
            "tolerance_amount_minor": tolerance_amount_minor,
            "cancellation_period_days": cancellation_period_days,
            # 'paused' und nicht 'ended': `is_active=false` heisst im Altmodell
            # "ruht", nicht "endgueltig vorbei". Mit 'ended' waere der gerade
            # angelegte Eintrag fuer GET /v1/recurring (_LEGACY_ACTIVE_STATUSES)
            # sofort unsichtbar - und da der Altpfad weder PATCH noch DELETE
            # kennt, dauerhaft unerreichbar.
            "status": "active" if is_active else "paused",
            "note": note,
        },
    )
    contract_id = cast(UUID, res.one().id)
    await contract_service.open_amount_stage(
        db,
        household_id,
        contract_id,
        amount_minor=abs(amount_minor_signed),
        valid_from=min(date.today(), next_due_date),
        change_reason="initial",
    )
    await contract_service.record_event(db, household_id, contract_id, "created")
    return await contract_service.require_contract(db, household_id, contract_id)


@router.get("/recurring", response_model=list[RecurringRule], deprecated=True)
async def list_recurring_rules(
    session_ctx: HouseholdSession,
) -> list[RecurringRule]:
    """Veraltet - `GET /v1/contracts` verwenden."""
    db, household_id = session_ctx
    contracts = await contract_service.list_contracts(
        db, household_id, statuses=list(_LEGACY_ACTIVE_STATUSES)
    )
    return [
        _as_legacy_recurring(contract)
        for contract in contracts
        if contract.contract_type != "subscription"
    ]


@router.post(
    "/recurring", response_model=RecurringRule, status_code=201, deprecated=True
)
async def create_recurring_rule(
    payload: RecurringRuleCreate,
    session_ctx: HouseholdEditorSession,
) -> RecurringRule:
    """Veraltet - `POST /v1/contracts` verwenden. Legt einen Vertrag an."""
    db, household_id = session_ctx
    if payload.expected_amount_minor == 0:
        raise HTTPException(status_code=422, detail="contract_amount_invalid")
    contract = await _create_legacy_contract(
        db,
        household_id,
        name=(payload.note or "Wiederkehrende Zahlung").strip() or "Wiederkehrende Zahlung",
        contract_type="income" if payload.expected_amount_minor > 0 else "other",
        amount_minor_signed=payload.expected_amount_minor,
        frequency=payload.frequency,
        next_due_date=payload.next_due_date,
        category_id=payload.category_id,
        account_id=payload.account_id,
        payee_id=payload.payee_id,
        tolerance_days=payload.tolerance_days,
        tolerance_amount_minor=payload.tolerance_amount_minor,
        is_active=payload.is_active,
    )
    return _as_legacy_recurring(contract)


@router.get("/subscriptions", response_model=list[Subscription], deprecated=True)
async def list_subscriptions(
    session_ctx: HouseholdSession,
) -> list[Subscription]:
    """Veraltet - `GET /v1/contracts?contract_type=subscription` verwenden."""
    db, household_id = session_ctx
    contracts = await contract_service.list_contracts(
        db,
        household_id,
        statuses=list(_LEGACY_ACTIVE_STATUSES),
        contract_type="subscription",
    )
    return [_as_legacy_subscription(contract) for contract in contracts]


@router.post(
    "/subscriptions", response_model=Subscription, status_code=201, deprecated=True
)
async def create_subscription(
    payload: SubscriptionCreate,
    session_ctx: HouseholdEditorSession,
) -> Subscription:
    """Veraltet - `POST /v1/contracts` verwenden. Legt einen Vertrag an."""
    db, household_id = session_ctx
    if payload.monthly_cost_minor != 0:
        amount_signed, frequency = payload.monthly_cost_minor, "monthly"
    elif payload.annual_cost_minor != 0:
        amount_signed, frequency = payload.annual_cost_minor, "yearly"
    else:
        raise HTTPException(status_code=422, detail="contract_amount_invalid")

    # Die alte Oberflaeche erfasste den Betrag mal mit, mal ohne Vorzeichen. Ein
    # Abonnement ist immer eine Ausgabe, deshalb hier eindeutig negativ.
    amount_signed = -abs(amount_signed)
    plan_suffix = f" - {payload.plan_name.strip()}" if payload.plan_name else ""

    contract = await _create_legacy_contract(
        db,
        household_id,
        name=f"{payload.provider.strip()}{plan_suffix}",
        provider_label=payload.provider.strip(),
        contract_type="subscription",
        amount_minor_signed=amount_signed,
        frequency=frequency,
        next_due_date=payload.next_renewal_date or date.today(),
        category_id=payload.category_id,
        account_id=payload.account_id,
        cancellation_period_days=payload.cancellation_period_days,
        is_active=payload.is_active,
        note=payload.note,
    )
    return _as_legacy_subscription(contract)
