"""Sammeluebernahme sicherer KI-Vorschlaege ("Alle sicheren uebernehmen").

docs/KATEGORISIERUNGSPLAN.md verschiebt diesen Knopf ausdruecklich in den
KI-Plan: *"'Alle sicheren uebernehmen' fuer heterogene KI-Vorschlaege bleibt
Teil des KI-Plans. Dieser Kategorienplan liefert dafuer die aktiven
Zielkategorien, Versionspruefung und sichere Schreiblogik"*. Genau diese drei
Zusagen werden hier eingeloest - und zwar mit derselben Mechanik wie der
manuelle Mehrfachpfad in `api/v1/routes/transactions.py`
(`bulk-category/preview|apply`), nicht mit einer zweiten, eigenen:

* jede Kategorie-ID laeuft durch `category_service.resolve_category_id()`
  (kanonisiert Merge-Tombstones, erkennt archivierte Kategorien),
* jede Buchung wird einzeln quittiert statt der ganze Vorgang abgebrochen,
* jede geschriebene Buchung laeuft in einem eigenen `begin_nested()`, damit ein
  Konflikt die uebrigen gueltigen Aenderungen nicht blockiert,
* die Version steigt um genau 1 - dieselbe Technik wie in
  `replace_transaction_splits`.

Der Unterschied zum manuellen Bulk: die Zielkategorie kommt nicht vom Client,
sondern je Buchung aus dem juengsten Eintrag in `classification_decisions`.
Daraus folgen die Sicherheitszusagen dieses Moduls:

1. Nur der juengste Entscheid je Buchung zaehlt. Die Tabelle ist
   append-only; ein aelterer Vorschlag darf einen neueren nie ueberstimmen.
2. Eine menschliche Entscheidung wird niemals ueberschrieben. Anwendbar ist
   ausschliesslich eine Buchung, deren einziger Split noch `category_id IS
   NULL` traegt.
3. Split-Buchungen bleiben unangetastet - unklar, welcher Split gemeint waere.
4. `no_match` (Entscheid mit `category_id IS NULL`) ist eine gueltige Antwort
   der KI und wird nie zu einer erzwungenen Kategorie umgedeutet.
5. Unterhalb der Schwelle wird nicht geschrieben. Die Schwelle kommt aus dem
   Request, sonst aus `ai_household_settings.auto_categorize_min_confidence`,
   sonst aus DEFAULT_MIN_CONFIDENCE - und wird im Ergebnis zurueckgemeldet.
"""

from typing import Any, Final
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import extract_sqlstate
from app.schemas.ai import (
    DEFAULT_MIN_CONFIDENCE,
    MAX_AI_PROPOSAL_TRANSACTIONS,
    AIProposalApplyInput,
    AIProposalBatchResult,
    AIProposalItem,
    AIProposalReason,
    AIProposalStatus,
)
from app.services import audit_service, provenance_service
from app.services.category_service import load_category, resolve_category_id

# Entitaetstyp des Vorgangs im Audit-Log. Bewusst ein eigener Typ statt
# 'transaction': protokolliert wird der Vorgang als Ganzes, nicht jede einzelne
# Buchung - die Herkunft je Buchung steht ohnehin am Split
# (category_source='ai', category_decision_id, category_confidence).
AI_PROPOSAL_BATCH_ENTITY_TYPE: Final = "ai_proposal_batch"

# Platzhalter fuer `reason_code`, wenn zu einer angefragten Buchung ueberhaupt
# kein Entscheid vorliegt. Der Aufrufer bekommt trotzdem eine Ergebniszeile -
# eine Buchung stillschweigend aus der Antwort zu streichen waere schwerer zu
# deuten als ein ausdrueckliches "es gibt hier nichts zu uebernehmen".
NO_DECISION_REASON_CODE: Final = "no_decision"

# Meldung des Triggers reject_archived_category_reference()
# (supabase/migrations/0001_schema.sql). Zwilling von
# `_is_archived_category_error` in routes/transactions.py - bewusst als eigene
# kleine Kopie, damit ein Service nicht in eine Route hineingreifen muss.
_CATEGORY_ARCHIVED_MESSAGE: Final = "CATEGORY_ARCHIVED"
_CHECK_VIOLATION_SQLSTATE: Final = "23514"

# Zustand aller angefragten Buchungen in einer Abfrage - inhaltlich identisch
# zu `_load_bulk_snapshots` im manuellen Bulk-Pfad, damit Vorschau, manuelle
# Mehrfachzuordnung und KI-Uebernahme dieselbe Splitlage sehen.
_SNAPSHOT_SQL: Final = (
    "select t.id as transaction_id, t.version, t.is_archived, "
    "count(ts.id) as split_count, "
    "(array_agg(ts.id))[1] as split_id, "
    "(array_agg(ts.category_id))[1] as split_category_id "
    "from transactions t "
    "left join transaction_splits ts on ts.transaction_id = t.id "
    "where t.household_id = :household_id and t.id = any(:transaction_ids) "
    "group by t.id, t.version, t.is_archived"
)

# Juengster Entscheid je Buchung. `distinct on` mit derselben Sortierung wie im
# Index (created_at desc, id desc) - `id desc` entscheidet die Faelle, in denen
# zwei Entscheide auf dieselbe Mikrosekunde fallen.
#
# Bewusst OHNE Filter auf source='ai': gaebe es zu einer Buchung einen
# neueren Entscheid anderer Herkunft, duerfte ein aelterer KI-Vorschlag ihn
# nicht ueberstimmen.
#
# `d.source` wird mitgelesen, weil nicht jeder Vorschlag vom Modell stammt:
# der Vorfilter fuer bekannte Empfaenger im Stapellauf schreibt seine
# Entscheide mit source='history'. Die Uebernahme laeuft fuer beide gleich -
# nur die Herkunft am Split unterscheidet sie.
_LATEST_DECISION_SQL: Final = (
    "select distinct on (d.transaction_id) d.transaction_id, d.id as decision_id, "
    "d.category_id, d.confidence, d.reason_code, d.source "
    "from classification_decisions d "
    "where d.household_id = :household_id and d.transaction_id = any(:transaction_ids) "
    "and (cast(:classification_run_id as uuid) is null or exists ("
    "  select 1 from ai_tasks at where at.id = d.ai_task_id "
    "  and at.household_id = d.household_id "
    "  and at.classification_run_id = cast(:classification_run_id as uuid)"
    ")) "
    "order by d.transaction_id, d.created_at desc, d.id desc"
)

# Alle offenen Vorschlaege des Haushalts (transaction_ids = null im Request):
# nicht archivierte Buchungen mit genau einem noch unkategorisierten Split, zu
# denen ueberhaupt ein Entscheid existiert. Buchungen, die inzwischen von Hand
# kategorisiert wurden, tauchen hier gar nicht erst auf - der Sammelknopf soll
# nicht mit hunderten 'already_categorized'-Zeilen antworten.
_OPEN_PROPOSAL_SQL: Final = (
    "select t.id as transaction_id "
    "from transactions t "
    "join transaction_splits ts on ts.transaction_id = t.id "
    "join lateral ("
    "  select max(d.created_at) as decided_at from classification_decisions d "
    "  where d.household_id = t.household_id and d.transaction_id = t.id "
    "  and (cast(:classification_run_id as uuid) is null or exists ("
    "    select 1 from ai_tasks at where at.id = d.ai_task_id "
    "    and at.household_id = d.household_id "
    "    and at.classification_run_id = cast(:classification_run_id as uuid)"
    "  ))"
    ") latest on latest.decided_at is not null "
    "where t.household_id = :household_id and not t.is_archived "
    "group by t.id, latest.decided_at "
    "having count(ts.id) = 1 and count(ts.category_id) = 0 "
    "order by latest.decided_at desc, t.id "
    "limit :limit"
)


def _is_archived_category_error(exc: DBAPIError) -> bool:
    """Erkennt die Ablehnung durch den Archiv-Trigger.

    Erreichbar trotz vorheriger Aufloesung: zwischen `resolve_category_id()`
    und dem UPDATE kann die Kategorie archiviert worden sein. Dann wird nur
    diese Buchung uebersprungen, nicht der ganze Vorgang abgebrochen."""
    if extract_sqlstate(exc) != _CHECK_VIOLATION_SQLSTATE:
        return False
    return _CATEGORY_ARCHIVED_MESSAGE in str(getattr(exc, "orig", exc))


async def _effective_min_confidence(
    session: AsyncSession, household_id: UUID, requested: float | None
) -> float:
    """Request schlaegt Haushaltseinstellung schlaegt Spaltendefault."""
    if requested is not None:
        return requested
    configured = (
        await session.execute(
            text(
                "select auto_categorize_min_confidence from ai_household_settings "
                "where household_id = :household_id"
            ),
            {"household_id": str(household_id)},
        )
    ).scalar_one_or_none()
    return DEFAULT_MIN_CONFIDENCE if configured is None else float(configured)


async def _load_open_proposal_ids(
    session: AsyncSession,
    household_id: UUID,
    classification_run_id: UUID | None = None,
) -> list[UUID]:
    result = await session.execute(
        text(_OPEN_PROPOSAL_SQL),
        {
            "household_id": str(household_id),
            "classification_run_id": (
                str(classification_run_id) if classification_run_id else None
            ),
            "limit": MAX_AI_PROPOSAL_TRANSACTIONS,
        },
    )
    return [UUID(str(row.transaction_id)) for row in result]


async def _load_latest_decisions(
    session: AsyncSession,
    household_id: UUID,
    transaction_ids: list[UUID],
    classification_run_id: UUID | None = None,
) -> dict[UUID, dict[str, Any]]:
    result = await session.execute(
        text(_LATEST_DECISION_SQL),
        {
            "household_id": str(household_id),
            "transaction_ids": [str(tid) for tid in transaction_ids],
            "classification_run_id": (
                str(classification_run_id) if classification_run_id else None
            ),
        },
    )
    return {UUID(str(row.transaction_id)): dict(row._mapping) for row in result}


async def _load_snapshots(
    session: AsyncSession, household_id: UUID, transaction_ids: list[UUID]
) -> dict[UUID, dict[str, Any]]:
    result = await session.execute(
        text(_SNAPSHOT_SQL),
        {
            "household_id": str(household_id),
            "transaction_ids": [str(tid) for tid in transaction_ids],
        },
    )
    return {UUID(str(row.transaction_id)): dict(row._mapping) for row in result}


async def _resolve_target(
    session: AsyncSession,
    household_id: UUID,
    category_id: UUID,
    cache: dict[UUID, tuple[UUID | None, str | None]],
) -> tuple[UUID | None, str | None]:
    """Aufloesung samt Anzeigename, mit Zwischenspeicher.

    In einem Vorgang teilen sich viele Buchungen dieselben Kategorien; ohne
    Zwischenspeicher entstuenden bei 500 Buchungen 1000 Abfragen. Ein 404/409
    des Resolvers ist hier kein Requestfehler, sondern das Ergebnis
    'category_inactive' fuer genau diese eine Buchung - der Vorschlag kann
    Stunden alt sein und seine Kategorie seither archiviert."""
    if category_id in cache:
        return cache[category_id]
    resolved: UUID | None
    try:
        resolved = await resolve_category_id(
            session, household_id, category_id, allow_none=False
        )
    except HTTPException:
        resolved = None
    name: str | None = None
    if resolved is not None:
        row = await load_category(session, household_id, resolved)
        name = str(row["name"]) if row is not None else None
    cache[category_id] = (resolved, name)
    return resolved, name


async def _write_proposal(
    session: AsyncSession,
    household_id: UUID,
    snapshot: dict[str, Any],
    decision: dict[str, Any],
    target_category_id: UUID,
    confidence: float,
    actor_user_id: UUID,
) -> tuple[AIProposalStatus, AIProposalReason | None, int | None]:
    """Schreibt genau eine Buchung. Rueckgabe: (Status, Grund, Version danach).

    Der Savepoint ist kein Luxus: der Archiv-Trigger kann eine einzelne Zeile
    ablehnen, und ohne Savepoint waere danach die gesamte Request-Transaktion
    abgebrochen - der Sammelknopf haette dann alles oder nichts geschrieben."""
    transaction_id = UUID(str(snapshot["transaction_id"]))
    bumped_version: int | None = None
    try:
        async with session.begin_nested():
            bumped = (
                await session.execute(
                    text(
                        "update transactions set version = version + 1 "
                        "where id = :id and household_id = :household_id "
                        "and version = :expected_version and not is_archived "
                        "returning version"
                    ),
                    {
                        "id": str(transaction_id),
                        "household_id": str(household_id),
                        "expected_version": int(snapshot["version"]),
                    },
                )
            ).first()
            if bumped is not None:
                bumped_version = int(bumped.version)
                await session.execute(
                    text(
                        "update transaction_splits set "
                        f"{provenance_service.CATEGORY_ASSIGNMENT_SET_SQL} "
                        "where id = :split_id"
                    ),
                    {
                        "split_id": str(snapshot["split_id"]),
                        # Herkunft des Entscheids mit Verweis auf ihn: an der
                        # Buchung bleibt nachvollziehbar, dass die Kategorie aus
                        # der Sammeluebernahme stammt und wer sie ausgeloest hat.
                        # 'ai' fuer einen Modellvorschlag, 'history' fuer einen
                        # aus dem eigenen Bestand abgeleiteten - eine pauschale
                        # KI-Herkunft waere fuer letzteren schlicht falsch.
                        **provenance_service.decision_assignment(
                            target_category_id,
                            UUID(str(decision["decision_id"])),
                            confidence,
                            decision_source=str(decision["source"]),
                            actor_user_id=actor_user_id,
                        ),
                    },
                )
    except DBAPIError as exc:
        if not _is_archived_category_error(exc):
            raise
        return "skipped", "category_inactive", None

    if bumped_version is None:
        # Zwischen Schnappschuss und Schreibvorgang hat jemand anders
        # geschrieben - der Vorschlag bezieht sich damit auf einen ueberholten
        # Stand und wird nicht angewendet.
        current = (
            await session.execute(
                text(
                    "select version from transactions "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(transaction_id), "household_id": str(household_id)},
            )
        ).scalar_one_or_none()
        return "conflict", "version_conflict", None if current is None else int(current)

    return "applied", None, bumped_version


def _batch_result(min_confidence: float, items: list[AIProposalItem]) -> AIProposalBatchResult:
    return AIProposalBatchResult(
        min_confidence=min_confidence,
        applied=sum(1 for item in items if item.status == "applied"),
        unchanged=sum(1 for item in items if item.status == "unchanged"),
        skipped=sum(1 for item in items if item.status == "skipped"),
        conflict=sum(1 for item in items if item.status == "conflict"),
        items=items,
    )


async def _find_replay(
    session: AsyncSession, household_id: UUID, request_id: str
) -> AIProposalBatchResult | None:
    """Idempotenz ueber das Audit-Log.

    Der Vorgang braucht dafuer keine eigene Tabelle: das Audit-Ereignis, das er
    ohnehin schreiben muss, traegt die request_id in `metadata` und das
    vollstaendige Ergebnis in `after`. Ein Retry nach Verbindungsabbruch
    bekommt damit exakt die Antwort des ersten Laufs zurueck, statt ein zweites
    Mal zu schreiben. (Zwei exakt gleichzeitige Laeufe kann das nicht
    verhindern - dagegen schuetzt die Versionspruefung je Buchung: der zweite
    Lauf findet die Buchung dann bereits kategorisiert vor.)"""
    row = (
        await session.execute(
            text(
                "select after from audit_log "
                "where household_id = :household_id and entity_type = :entity_type "
                "and metadata->>'request_id' = :request_id "
                "order by created_at desc limit 1"
            ),
            {
                "household_id": str(household_id),
                "entity_type": AI_PROPOSAL_BATCH_ENTITY_TYPE,
                "request_id": request_id,
            },
        )
    ).first()
    if row is None or row.after is None:
        return None
    stored = AIProposalBatchResult.model_validate(row.after)
    return stored.model_copy(update={"idempotent_replay": True})


async def _record_batch_audit(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID,
    payload: AIProposalApplyInput,
    result: AIProposalBatchResult,
) -> None:
    """Ein Ereignis fuer den ganzen Vorgang, nicht eines je Split.

    `correlation_id` klammert den Vorgang und ist zugleich seine Entitaets-ID -
    kuenftige Folgeereignisse (etwa ein Zuruecknehmen) haengen sich mit
    derselben Korrelation an denselben Vorgang. Ein Lauf ohne Wirkung wird nur
    dann protokolliert, wenn er eine request_id trug: dann ist die Zeile die
    Idempotenzspur, sonst waere sie reines Rauschen."""
    if result.applied == 0 and payload.request_id is None:
        return
    correlation_id = uuid4()
    await audit_service.record_audit_event(
        session,
        household_id,
        actor_user_id,
        AI_PROPOSAL_BATCH_ENTITY_TYPE,
        correlation_id,
        "update",
        after=result.model_dump(mode="json"),
        correlation_id=correlation_id,
        metadata={
            "request_id": payload.request_id,
            "min_confidence": result.min_confidence,
            "requested_transaction_count": (
                None if payload.transaction_ids is None else len(payload.transaction_ids)
            ),
            "applied": result.applied,
            "unchanged": result.unchanged,
            "skipped": result.skipped,
            "conflict": result.conflict,
        },
    )


async def evaluate_proposals(
    session: AsyncSession,
    household_id: UUID,
    payload: AIProposalApplyInput,
    *,
    write: bool,
    actor_user_id: UUID,
) -> AIProposalBatchResult:
    """Gemeinsame Logik von Vorschau und Anwendung.

    Die Vorschau fuehrt bewusst kein einziges UPDATE aus - die
    Request-Transaktion wird am Ende committet, ein versehentlicher
    Schreibvorgang bliebe also bestehen. Beide Modi durchlaufen sonst genau
    dieselben Pruefungen in derselben Reihenfolge, damit die Vorschau nicht
    nach anderen Regeln urteilt als das Anwenden."""
    if write and payload.request_id is not None:
        replay = await _find_replay(session, household_id, payload.request_id)
        if replay is not None:
            return replay

    min_confidence = await _effective_min_confidence(
        session, household_id, payload.min_confidence
    )

    transaction_ids = (
        await _load_open_proposal_ids(session, household_id, payload.classification_run_id)
        if payload.transaction_ids is None
        else payload.transaction_ids
    )
    if not transaction_ids:
        return _batch_result(min_confidence, [])

    decisions = await _load_latest_decisions(
        session, household_id, transaction_ids, payload.classification_run_id
    )
    snapshots = await _load_snapshots(session, household_id, transaction_ids)
    resolved_cache: dict[UUID, tuple[UUID | None, str | None]] = {}

    items: list[AIProposalItem] = []
    for transaction_id in transaction_ids:
        decision = decisions.get(transaction_id)
        base: dict[str, Any] = {
            "transaction_id": transaction_id,
            "reason_code": (
                NO_DECISION_REASON_CODE if decision is None else str(decision["reason_code"])
            ),
            "confidence": 0.0 if decision is None else float(decision["confidence"]),
            "source": None if decision is None else str(decision["source"]),
        }

        snapshot = snapshots.get(transaction_id)
        if snapshot is None:
            # Fremde oder geloeschte Buchung. Bewusst dieselbe Auskunft wie fuer
            # einen anderen Haushalt: die Existenz fremder Buchungen wird nicht
            # verraten.
            items.append(
                AIProposalItem(**base, status="skipped", reason="transaction_not_found")
            )
            continue

        base["current_version"] = int(snapshot["version"])

        if snapshot["is_archived"]:
            items.append(
                AIProposalItem(**base, status="skipped", reason="archived_transaction")
            )
            continue

        if int(snapshot["split_count"]) != 1:
            items.append(AIProposalItem(**base, status="skipped", reason="split_transaction"))
            continue

        if snapshot["split_category_id"] is not None:
            # Die wichtigste Regel des Moduls: eine bereits gesetzte Kategorie
            # stammt von einem Menschen (oder aus einer Regel, die ein Mensch
            # angelegt hat) und wird von der Sammeluebernahme nie ueberschrieben.
            items.append(
                AIProposalItem(**base, status="unchanged", reason="already_categorized")
            )
            continue

        if decision is None or decision["category_id"] is None:
            # no_match ist eine richtige Antwort der KI (Leitplanke "KI darf
            # vorschlagen, nicht erfinden") und darf nie in eine erzwungene
            # Kategorie umgedeutet werden.
            items.append(AIProposalItem(**base, status="skipped", reason="no_match"))
            continue

        if base["confidence"] < min_confidence:
            items.append(AIProposalItem(**base, status="skipped", reason="below_confidence"))
            continue

        target_id, target_name = await _resolve_target(
            session, household_id, UUID(str(decision["category_id"])), resolved_cache
        )
        if target_id is None:
            # Seit dem Vorschlag archiviert oder geloescht: uebersprungen, nicht
            # abgebrochen.
            items.append(AIProposalItem(**base, status="skipped", reason="category_inactive"))
            continue

        base["category_id"] = target_id
        base["category_name"] = target_name

        if not write:
            items.append(AIProposalItem(**base, status="applied"))
            continue

        status_value, reason, version_after = await _write_proposal(
            session,
            household_id,
            snapshot,
            decision,
            target_id,
            base["confidence"],
            actor_user_id,
        )
        if version_after is not None:
            base["current_version"] = version_after
        items.append(AIProposalItem(**base, status=status_value, reason=reason))

    result = _batch_result(min_confidence, items)
    if write:
        await _record_batch_audit(session, household_id, actor_user_id, payload, result)
    return result
