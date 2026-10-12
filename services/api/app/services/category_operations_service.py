"""Lebenszyklus-Operationen der Kategorienverwaltung.

Deckt docs/KATEGORISIERUNGSPLAN.md, Abschnitt "Lebenszyklus einer Kategorie"
(Verschieben, Archivieren, Wiederherstellen, Zusammenfuehren) sowie die
Lieferabschnitte 2 und 4 ab.

Warum ein eigener Service neben `category_service.py`?
`category_service.py` ist der Resolver und die Lesequelle - er wird von jedem
Schreibpfad des Systems benutzt und muss klein und nebenwirkungsfrei bleiben.
Die Operationen hier sind dagegen zusammengesetzte, transaktionale Vorgaenge
mit Vorschau, Sperren, Referenzmigration und Audit. Sie gehoeren nicht in eine
Route (Merge allein sind ein Dutzend Statements) und auch nicht in den
Resolver.

Gemeinsames Muster aller Operationen (Plan: "Standardkatalog und
Operationsvorschauen"):

1. Die Vorschau berechnet einen *Wirkungssnapshot* (`impact`), schreibt ihn mit
   einem stabilen `fingerprint` in `category_operations` und liefert dessen id
   als `operation_id` zurueck.
2. Das Anwenden sperrt die betroffenen Kategorien per `select ... for update`,
   berechnet denselben Snapshot erneut und vergleicht den Fingerprint. Jede
   zwischenzeitliche Aenderung - auch eine Umbenennung, denn die Version steckt
   im Snapshot - laesst die Operation mit `409 category_preview_stale`
   scheitern, statt eine fremde Aenderung zu ueberschreiben.

Der Fingerprint ersetzt damit fachlich das `If-Match` der einfachen
Mutationen. `If-Match` bleibt trotzdem zusaetzlich erlaubt (die Routen reichen
es als `expected_version` durch), ist hier aber nicht verpflichtend: die
Vorschauantworten fuehren bewusst keine Entitaetsversion, ein Pflichtheader
waere ohne zweiten Read gar nicht erfuellbar.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final, Literal, cast
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import raise_version_conflict
from app.schemas.category import (
    ArchiveApply,
    ArchivePreview,
    CatalogCategory,
    CatalogGroup,
    CategoryReferenceCounts,
    GroupArchiveApply,
    GroupArchivePreview,
    MergeApply,
    MergeCollision,
    MergePreview,
    MergeRequest,
    MergeResult,
    MoveApply,
    MovePreview,
    RestoreRequest,
)
from app.services.audit_service import record_category_event, record_category_group_event
from app.services.category_service import (
    CATEGORY_GROUP_INACTIVE,
    CATEGORY_GROUP_NOT_FOUND,
    CATEGORY_INACTIVE,
    CATEGORY_NOT_FOUND,
    assert_group_active,
    count_category_references,
    fetch_category_snapshot,
    fetch_group_snapshot,
    get_taxonomy_revision,
    load_catalog_category,
    load_catalog_group,
    normalize_category_name,
    resolve_category_id,
    split_reference_counts,
)

# ---------------------------------------------------------------------------
# Fehlerkennungen
# ---------------------------------------------------------------------------

CATEGORY_OPERATION_NOT_FOUND: Final = "category_operation_not_found"
CATEGORY_PREVIEW_STALE: Final = "category_preview_stale"
CATEGORY_DUPLICATE_NAME: Final = "category_duplicate_name"
CATEGORY_GROUP_DUPLICATE_NAME: Final = "category_group_duplicate_name"
CATEGORY_GROUP_HAS_ACTIVE_CATEGORIES: Final = "category_group_has_active_categories"
CATEGORY_MERGED_NOT_RESTORABLE: Final = "category_merged_not_restorable"
CATEGORY_MERGE_TYPE_MISMATCH: Final = "category_merge_type_mismatch"
CATEGORY_MERGE_SAME_CATEGORY: Final = "category_merge_same_category"
CATEGORY_REPLACEMENT_INVALID: Final = "category_replacement_invalid"

OperationName = Literal["move", "archive", "restore", "merge", "group_archive"]

# Kurze Lebensdauer laut Plan ("kurz lebende Vorschauen"). Identisch zum
# Spaltendefault der Migration, hier aber explizit gesetzt, damit die Antwort
# denselben Wert liefert, den auch das Apply prueft.
PREVIEW_TTL_MINUTES: Final = 30

# Der Hinweistext der Move-Vorschau. Bewusst hier und nicht in der Route: die
# Formulierung ist Teil der Planzusage ("Wer die alte Gruppierung erhalten
# will, bekommt den konkreten Hinweis").
MOVE_HINT: Final = (
    "Berichte und Budgets gruppieren rueckwirkend nach der neuen Gruppe - "
    "auch alle bisherigen Buchungen dieser Kategorie. Soll die bisherige "
    "Gruppierung fuer die Vergangenheit erhalten bleiben, statt zu verschieben "
    "eine neue Kategorie in der Zielgruppe anlegen und diese hier archivieren."
)

# Zukunftswirksame Referenzen ausser den Klassifikationsregeln. Regeln haben
# einen eigenen Pfad, weil der partielle Unique-Index
# classification_rules_payee_category_uniq bei einem blinden Umhaengen bricht.
_FORWARD_TABLES: Final[tuple[tuple[str, str, str], ...]] = (
    ("payees", "default_category_id", "payee_defaults"),
    ("contracts", "category_id", "contracts"),
    ("savings_goals", "category_id", "savings_goals"),
)

# Bedingung fuer eine "einfache Haendlerregel" - deckungsgleich mit dem
# partiellen Unique-Index aus 0002_audit_log.sql. Nur solche Regeln koennen
# beim Merge zu einem Indexkonflikt fuehren.
_SIMPLE_PAYEE_RULE: Final = (
    "{alias}.match_payee_raw is not null and {alias}.match_purpose is null "
    "and {alias}.match_min_amount_minor is null "
    "and {alias}.match_max_amount_minor is null "
    "and {alias}.match_account_id is null"
)


def _rowcount(result: Any) -> int:
    """SQLAlchemy typisiert das Ergebnis von execute() als Result[Any]; die
    Trefferzahl haengt am CursorResult (gleiches Muster wie routes/rules.py)."""
    return cast(CursorResult[Any], result).rowcount


# ---------------------------------------------------------------------------
# Wirkungssnapshot und Operationszeilen
# ---------------------------------------------------------------------------


def _fingerprint(impact: Mapping[str, Any]) -> str:
    """Stabiler Hash ueber den Wirkungssnapshot.

    `sort_keys` und feste Trenner sind entscheidend: Vorschau und Apply
    berechnen denselben Snapshot in getrennten Requests, ein von der
    Dict-Reihenfolge abhaengiger Hash wuerde zufaellig scheitern."""
    canonical = json.dumps(
        impact, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _create_operation(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    operation: OperationName,
    impact: Mapping[str, Any],
    *,
    source_id: UUID,
    target_id: UUID | None = None,
    source_version: int | None = None,
    target_version: int | None = None,
) -> tuple[UUID, datetime]:
    revision = await get_taxonomy_revision(session, household_id)
    result = await session.execute(
        text(
            "insert into category_operations (household_id, operation, status, created_by, "
            "source_id, target_id, source_version, target_version, taxonomy_revision, "
            "fingerprint, impact, expires_at) values "
            "(:household_id, :operation, 'preview', :actor, :source_id, :target_id, "
            ":source_version, :target_version, :revision, :fingerprint, "
            f"cast(:impact as jsonb), now() + interval '{PREVIEW_TTL_MINUTES} minutes') "
            "returning id, expires_at"
        ),
        {
            "household_id": str(household_id),
            "operation": operation,
            "actor": str(actor_user_id) if actor_user_id else None,
            "source_id": str(source_id),
            "target_id": str(target_id) if target_id else None,
            "source_version": source_version,
            "target_version": target_version,
            "revision": revision,
            "fingerprint": _fingerprint(impact),
            "impact": json.dumps(impact, default=str, ensure_ascii=False),
        },
    )
    row = result.one()
    return UUID(str(row.id)), cast(datetime, row.expires_at)


async def _load_operation(
    session: AsyncSession,
    household_id: UUID,
    operation: OperationName,
    operation_id: UUID,
) -> dict[str, Any]:
    """Laedt und sperrt die Vorschauzeile.

    `for update` serialisiert zwei gleichzeitige Applies derselben Vorschau:
    der zweite wartet und sieht danach status='applied'. Eine fremde oder
    unbekannte id liefert 404, damit keine Existenz eines anderen Haushalts
    verraten wird."""
    result = await session.execute(
        text(
            "select id, operation, status, request_id, source_id, target_id, fingerprint, "
            "impact, result, expires_at, expires_at <= now() as is_expired "
            "from category_operations where id = :id and household_id = :household_id "
            "for update"
        ),
        {"id": str(operation_id), "household_id": str(household_id)},
    )
    row = result.first()
    if row is None or str(row.operation) != operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_OPERATION_NOT_FOUND
        )
    # Abgelaufen oder bereits angewendet: der Aufrufer braucht eine frische
    # Vorschau. Die Pruefung sitzt hier und nicht erst beim
    # Fingerprint-Vergleich, damit ein bereits angewendeter Vorgang nicht als
    # Folgefehler ("Kategorie inaktiv") aus der Neuberechnung faellt.
    if str(row.status) != "preview" or bool(row.is_expired):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_PREVIEW_STALE
        )
    return dict(row._mapping)


def _assert_preview_fresh(operation_row: Mapping[str, Any], fingerprint: str) -> None:
    """Vergleicht den neu berechneten Wirkungssnapshot mit dem der Vorschau."""
    if str(operation_row["fingerprint"]) != fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_PREVIEW_STALE
        )


async def _mark_operation_applied(
    session: AsyncSession,
    operation_id: UUID,
    *,
    request_id: str | None = None,
    result_payload: Mapping[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            "update category_operations set status = 'applied', applied_at = now(), "
            "request_id = coalesce(:request_id, request_id), "
            "result = cast(:result as jsonb) where id = :id"
        ),
        {
            "id": str(operation_id),
            "request_id": request_id,
            "result": (
                json.dumps(result_payload, default=str, ensure_ascii=False)
                if result_payload is not None
                else None
            ),
        },
    )


# ---------------------------------------------------------------------------
# Gemeinsame Ladehilfen
# ---------------------------------------------------------------------------

_LOCK_COLUMNS: Final = (
    "id, group_id, name, default_type, is_archived, merged_into_category_id, "
    "sort_order, version"
)


async def _load_category(
    session: AsyncSession, household_id: UUID, category_id: UUID, *, lock: bool = False
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"select {_LOCK_COLUMNS} from categories "
            "where id = :id and household_id = :household_id"
            + (" for update" if lock else "")
        ),
        {"id": str(category_id), "household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_NOT_FOUND)
    return dict(row._mapping)


async def _load_group(
    session: AsyncSession, household_id: UUID, group_id: UUID, *, lock: bool = False
) -> dict[str, Any]:
    result = await session.execute(
        text(
            "select id, name, is_archived, sort_order, version from category_groups "
            "where id = :id and household_id = :household_id"
            + (" for update" if lock else "")
        ),
        {"id": str(group_id), "household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_GROUP_NOT_FOUND
        )
    return dict(row._mapping)


def _assert_expected_version(current_version: int, expected_version: int | None) -> None:
    """Optionales `If-Match` der Routen. Bewusst erst nach dem Sperren der
    Zeile ausgewertet: vorher gelesen waere die Pruefung selbst ein Rennen."""
    if expected_version is not None and expected_version != current_version:
        raise_version_conflict(current_version)


def _assert_active(category: Mapping[str, Any]) -> None:
    if category["is_archived"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_INACTIVE)


async def _assert_category_name_free(
    session: AsyncSession,
    household_id: UUID,
    group_id: UUID,
    name: str,
    *,
    exclude_id: UUID,
) -> None:
    """Vorpruefung gegen categories_active_name_uniq. Der partielle Index
    bleibt die Absicherung; hier geht es nur um eine saubere 409 statt eines
    abgebrochenen Statements."""
    result = await session.execute(
        text(
            "select 1 from categories where household_id = :household_id "
            "and group_id = :group_id and not is_archived "
            "and category_normalize_name(name) = :normalized "
            "and id <> :exclude_id limit 1"
        ),
        {
            "household_id": str(household_id),
            "group_id": str(group_id),
            "normalized": normalize_category_name(name),
            "exclude_id": str(exclude_id),
        },
    )
    if result.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_DUPLICATE_NAME
        )


async def _assert_group_name_free(
    session: AsyncSession, household_id: UUID, name: str, *, exclude_id: UUID
) -> None:
    result = await session.execute(
        text(
            "select 1 from category_groups where household_id = :household_id "
            "and not is_archived and category_normalize_name(name) = :normalized "
            "and id <> :exclude_id limit 1"
        ),
        {
            "household_id": str(household_id),
            "normalized": normalize_category_name(name),
            "exclude_id": str(exclude_id),
        },
    )
    if result.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_GROUP_DUPLICATE_NAME
        )


async def _next_sort_order(session: AsyncSession, household_id: UUID, group_id: UUID) -> int:
    result = await session.execute(
        text(
            "select coalesce(max(sort_order) + 1, 0) as next from categories "
            "where household_id = :household_id and group_id = :group_id"
        ),
        {"household_id": str(household_id), "group_id": str(group_id)},
    )
    row = result.one()
    return int(row.next)


# ---------------------------------------------------------------------------
# Verschieben
# ---------------------------------------------------------------------------


async def _move_impact(
    session: AsyncSession,
    household_id: UUID,
    category: Mapping[str, Any],
    target_group: Mapping[str, Any],
) -> dict[str, Any]:
    """Wirkungssnapshot des Gruppenwechsels.

    Die Versionen von Kategorie und Zielgruppe sind Teil des Snapshots: eine
    zwischenzeitliche Umbenennung oder Archivierung soll die Vorschau
    ungueltig machen, obwohl sich die Zahl der Splits nicht geaendert hat."""
    stats = await session.execute(
        text(
            "select count(*) as splits, min(t.booked_date) as first_date, "
            "max(t.booked_date) as last_date from transaction_splits s "
            "join transactions t on t.id = s.transaction_id "
            "where s.household_id = :household_id and s.category_id = :category_id"
        ),
        {"household_id": str(household_id), "category_id": str(category["id"])},
    )
    split_row = stats.one()
    allocations = await session.execute(
        text(
            "select count(*) as allocations from budget_allocations "
            "where household_id = :household_id and category_id = :category_id"
        ),
        {"household_id": str(household_id), "category_id": str(category["id"])},
    )
    return {
        "category_id": str(category["id"]),
        "category_version": int(category["version"]),
        "source_group_id": str(category["group_id"]),
        "target_group_id": str(target_group["id"]),
        "target_group_version": int(target_group["version"]),
        "affected_splits": int(split_row.splits),
        "first_booked_date": str(split_row.first_date) if split_row.first_date else None,
        "last_booked_date": str(split_row.last_date) if split_row.last_date else None,
        "affected_budget_allocations": int(allocations.one().allocations),
    }


async def preview_move(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    category_id: UUID,
    target_group_id: UUID,
) -> MovePreview:
    category = await _load_category(session, household_id, category_id)
    _assert_active(category)
    target_group = await assert_group_active(session, household_id, target_group_id)
    source_group = await _load_group(session, household_id, UUID(str(category["group_id"])))
    await _assert_category_name_free(
        session,
        household_id,
        target_group_id,
        str(category["name"]),
        exclude_id=category_id,
    )

    impact = await _move_impact(session, household_id, category, target_group)
    operation_id, expires_at = await _create_operation(
        session,
        household_id,
        actor_user_id,
        "move",
        impact,
        source_id=category_id,
        target_id=target_group_id,
        source_version=int(category["version"]),
        target_version=int(target_group["version"]),
    )
    return MovePreview(
        operation_id=operation_id,
        expires_at=expires_at,
        category_id=category_id,
        category_name=str(category["name"]),
        source_group_id=UUID(str(source_group["id"])),
        source_group_name=str(source_group["name"]),
        target_group_id=target_group_id,
        target_group_name=str(target_group["name"]),
        affected_splits=int(impact["affected_splits"]),
        first_booked_date=impact["first_booked_date"],
        last_booked_date=impact["last_booked_date"],
        affected_budget_allocations=int(impact["affected_budget_allocations"]),
        hint=MOVE_HINT,
    )


async def apply_move(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    category_id: UUID,
    payload: MoveApply,
    *,
    expected_version: int | None = None,
) -> CatalogCategory:
    operation = await _load_operation(session, household_id, "move", payload.operation_id)
    if str(operation["source_id"]) != str(category_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_OPERATION_NOT_FOUND
        )

    category = await _load_category(session, household_id, category_id, lock=True)
    _assert_expected_version(int(category["version"]), expected_version)
    _assert_active(category)
    target_group = await assert_group_active(session, household_id, payload.target_group_id)
    impact = await _move_impact(session, household_id, category, target_group)
    # Der Fingerprint deckt auch payload.target_group_id ab: eine Vorschau fuer
    # Gruppe A kann nicht auf Gruppe B angewendet werden.
    _assert_preview_fresh(operation, _fingerprint(impact))

    await _assert_category_name_free(
        session,
        household_id,
        payload.target_group_id,
        str(category["name"]),
        exclude_id=category_id,
    )

    before = await fetch_category_snapshot(session, household_id, category_id)
    await session.execute(
        text(
            "update categories set group_id = :group_id, sort_order = :sort_order "
            "where id = :id and household_id = :household_id"
        ),
        {
            "group_id": str(payload.target_group_id),
            "sort_order": await _next_sort_order(session, household_id, payload.target_group_id),
            "id": str(category_id),
            "household_id": str(household_id),
        },
    )
    after = await fetch_category_snapshot(session, household_id, category_id)
    await record_category_event(
        session,
        household_id,
        actor_user_id,
        category_id,
        "move",
        before=before,
        after=after,
        correlation_id=payload.operation_id,
        metadata={"operation_id": str(payload.operation_id), "impact": impact},
    )
    await _mark_operation_applied(session, payload.operation_id, result_payload=impact)
    return await load_catalog_category(session, household_id, category_id)


# ---------------------------------------------------------------------------
# Archivieren einer Kategorie
# ---------------------------------------------------------------------------


async def _archive_impact(
    session: AsyncSession, household_id: UUID, category: Mapping[str, Any]
) -> tuple[dict[str, Any], CategoryReferenceCounts, CategoryReferenceCounts]:
    counts = await count_category_references(
        session, household_id, UUID(str(category["id"]))
    )
    historical, forward = split_reference_counts(counts)
    impact = {
        "category_id": str(category["id"]),
        "category_version": int(category["version"]),
        "historical": historical.model_dump(),
        "forward": forward.model_dump(),
    }
    return impact, historical, forward


async def preview_archive(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    category_id: UUID,
) -> ArchivePreview:
    category = await _load_category(session, household_id, category_id)
    _assert_active(category)
    impact, historical, forward = await _archive_impact(session, household_id, category)
    operation_id, expires_at = await _create_operation(
        session,
        household_id,
        actor_user_id,
        "archive",
        impact,
        source_id=category_id,
        source_version=int(category["version"]),
    )
    return ArchivePreview(
        operation_id=operation_id,
        expires_at=expires_at,
        category_id=category_id,
        category_name=str(category["name"]),
        historical=historical,
        forward=forward,
        requires_resolution=forward.total > 0,
    )


async def _repoint_classification_rules(
    session: AsyncSession, household_id: UUID, source_id: UUID, target_id: UUID
) -> tuple[int, int]:
    """Haengt Regeln von `source_id` auf `target_id` um.

    Zwei Schritte, weil `classification_rules_payee_category_uniq` sonst
    bricht: eine einfache Haendlerregel, die es fuer dasselbe Haendlermuster
    beim Ziel schon gibt, waere nach dem Umhaengen ein Duplikat. Sie wird
    stattdessen deaktiviert und bleibt erhalten (Plan: "Die Zielregel bleibt
    aktiv, die redundante Quellregel wird deaktiviert"). Sie zeigt bewusst
    weiter auf die Quelle - der Trigger reject_archived_category_reference()
    laesst unveraenderte Kategorie-Referenzen zu, und eine inaktive Regel
    schreibt nirgends mehr hin.

    Rueckgabe: (umgehaengt, deaktiviert)."""
    duplicate_condition = (
        f"{_SIMPLE_PAYEE_RULE.format(alias='src')} and exists ("
        "select 1 from classification_rules tgt "
        "where tgt.household_id = src.household_id "
        "and tgt.action_category_id = :target_id and "
        f"{_SIMPLE_PAYEE_RULE.format(alias='tgt')} and "
        "lower(btrim(tgt.match_payee_raw)) = lower(btrim(src.match_payee_raw)))"
    )
    params = {
        "household_id": str(household_id),
        "source_id": str(source_id),
        "target_id": str(target_id),
    }
    deactivated = await session.execute(
        text(
            "update classification_rules src set is_active = false "
            "where src.household_id = :household_id "
            f"and src.action_category_id = :source_id and {duplicate_condition}"
        ),
        params,
    )
    repointed = await session.execute(
        text(
            "update classification_rules src set action_category_id = :target_id "
            "where src.household_id = :household_id "
            f"and src.action_category_id = :source_id and not ({duplicate_condition})"
        ),
        params,
    )
    return _rowcount(repointed), _rowcount(deactivated)


async def _resolve_forward_references(
    session: AsyncSession,
    household_id: UUID,
    category_id: UUID,
    replacement_id: UUID | None,
) -> dict[str, int]:
    """Loest alle zukunftswirksamen Referenzen auf.

    Muss VOR dem Setzen des Archivflags laufen: danach wuerde
    reject_archived_category_reference() jedes Umhaengen ablehnen, weil die
    Kategorie dann bereits archiviert ist."""
    resolved: dict[str, int] = {}
    for table, column, field in _FORWARD_TABLES:
        result = await session.execute(
            text(
                f"update {table} set {column} = cast(:replacement as uuid) "
                f"where household_id = :household_id and {column} = :category_id"
            ),
            {
                "replacement": str(replacement_id) if replacement_id else None,
                "household_id": str(household_id),
                "category_id": str(category_id),
            },
        )
        resolved[field] = _rowcount(result)

    if replacement_id is None:
        # Ohne Ersatz duerfen Regeln nicht einfach kategorielos weiterlaufen -
        # sie wuerden sonst still weiter matchen und nichts mehr zuordnen.
        result = await session.execute(
            text(
                "update classification_rules set action_category_id = null, is_active = false "
                "where household_id = :household_id and action_category_id = :category_id"
            ),
            {"household_id": str(household_id), "category_id": str(category_id)},
        )
        resolved["classification_rules"] = _rowcount(result)
        resolved["deactivated_rules"] = _rowcount(result)
    else:
        repointed, deactivated = await _repoint_classification_rules(
            session, household_id, category_id, replacement_id
        )
        resolved["classification_rules"] = repointed
        resolved["deactivated_rules"] = deactivated
    return resolved


async def _resolve_replacement(
    session: AsyncSession,
    household_id: UUID,
    category_id: UUID,
    replacement_category_id: UUID | None,
) -> UUID | None:
    if replacement_category_id is None:
        return None
    replacement_id = await resolve_category_id(
        session, household_id, replacement_category_id, allow_none=False
    )
    if replacement_id is None or replacement_id == category_id:
        # Die zu archivierende Kategorie als eigenen Ersatz zu setzen waere ein
        # stiller No-Op, der hinterher als aufgeloest gilt.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_REPLACEMENT_INVALID
        )
    return replacement_id


async def _archive_category_row(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    category_id: UUID,
    replacement_id: UUID | None,
    correlation_id: UUID,
    metadata: dict[str, Any],
) -> dict[str, int]:
    """Archiviert genau eine Kategorie inklusive Referenzaufloesung und Audit.

    Historische Referenzen (Splits, Budgetzeilen, Rollovers, KI-Entscheidungen)
    werden bewusst nicht angefasst - Archivieren darf laut Plan keine
    historische Summe veraendern."""
    resolved = await _resolve_forward_references(
        session, household_id, category_id, replacement_id
    )
    before = await fetch_category_snapshot(session, household_id, category_id)
    await session.execute(
        text(
            "update categories set is_archived = true, archived_at = now(), "
            "archived_by = cast(:actor as uuid) "
            "where id = :id and household_id = :household_id"
        ),
        {
            "actor": str(actor_user_id) if actor_user_id else None,
            "id": str(category_id),
            "household_id": str(household_id),
        },
    )
    after = await fetch_category_snapshot(session, household_id, category_id)
    await record_category_event(
        session,
        household_id,
        actor_user_id,
        category_id,
        "archive",
        before=before,
        after=after,
        correlation_id=correlation_id,
        metadata={
            **metadata,
            "replacement_category_id": str(replacement_id) if replacement_id else None,
            "resolved_forward_references": resolved,
        },
    )
    return resolved


async def apply_archive(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    category_id: UUID,
    payload: ArchiveApply,
    *,
    expected_version: int | None = None,
) -> CatalogCategory:
    operation = await _load_operation(session, household_id, "archive", payload.operation_id)
    if str(operation["source_id"]) != str(category_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_OPERATION_NOT_FOUND
        )

    category = await _load_category(session, household_id, category_id, lock=True)
    _assert_expected_version(int(category["version"]), expected_version)
    _assert_active(category)
    impact, _, _ = await _archive_impact(session, household_id, category)
    _assert_preview_fresh(operation, _fingerprint(impact))

    replacement_id = await _resolve_replacement(
        session, household_id, category_id, payload.replacement_category_id
    )
    resolved = await _archive_category_row(
        session,
        household_id,
        actor_user_id,
        category_id,
        replacement_id,
        payload.operation_id,
        {"operation_id": str(payload.operation_id), "impact": impact},
    )
    await _mark_operation_applied(
        session, payload.operation_id, result_payload={"resolved": resolved, "impact": impact}
    )
    return await load_catalog_category(session, household_id, category_id)


# ---------------------------------------------------------------------------
# Archivieren einer Gruppe
# ---------------------------------------------------------------------------


async def _group_archive_impact(
    session: AsyncSession, household_id: UUID, group: Mapping[str, Any]
) -> tuple[dict[str, Any], list[UUID], list[str]]:
    result = await session.execute(
        text(
            "select id, name from categories where household_id = :household_id "
            "and group_id = :group_id and not is_archived order by sort_order, name"
        ),
        {"household_id": str(household_id), "group_id": str(group["id"])},
    )
    rows = [dict(row._mapping) for row in result]
    ids = [UUID(str(row["id"])) for row in rows]
    names = [str(row["name"]) for row in rows]
    impact = {
        "group_id": str(group["id"]),
        "group_version": int(group["version"]),
        # Sortiert, damit der Fingerprint nicht von der Sortierreihenfolge
        # abhaengt, die ein Reorder zwischen Vorschau und Apply aendern koennte.
        "active_category_ids": sorted(str(value) for value in ids),
    }
    return impact, ids, names


async def preview_group_archive(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    group_id: UUID,
) -> GroupArchivePreview:
    group = await _load_group(session, household_id, group_id)
    if group["is_archived"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_GROUP_INACTIVE
        )
    impact, ids, names = await _group_archive_impact(session, household_id, group)
    operation_id, expires_at = await _create_operation(
        session,
        household_id,
        actor_user_id,
        "group_archive",
        impact,
        source_id=group_id,
        source_version=int(group["version"]),
    )
    return GroupArchivePreview(
        operation_id=operation_id,
        expires_at=expires_at,
        group_id=group_id,
        group_name=str(group["name"]),
        active_category_ids=ids,
        active_category_names=names,
        requires_resolution=len(ids) > 0,
    )


async def apply_group_archive(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    group_id: UUID,
    payload: GroupArchiveApply,
    *,
    expected_version: int | None = None,
) -> CatalogGroup:
    operation = await _load_operation(
        session, household_id, "group_archive", payload.operation_id
    )
    if str(operation["source_id"]) != str(group_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_OPERATION_NOT_FOUND
        )

    group = await _load_group(session, household_id, group_id, lock=True)
    _assert_expected_version(int(group["version"]), expected_version)
    if group["is_archived"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_GROUP_INACTIVE
        )
    impact, ids, _ = await _group_archive_impact(session, household_id, group)
    _assert_preview_fresh(operation, _fingerprint(impact))

    if ids and not payload.archive_categories:
        # Kein Cascade-Archiv ohne ausdrueckliche Bestaetigung (Plan:
        # "Es gibt kein Cascade-Archiv ohne Vorschau").
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=CATEGORY_GROUP_HAS_ACTIVE_CATEGORIES,
        )

    replacement_id: UUID | None = None
    if ids:
        replacement_id = await _resolve_replacement(
            session, household_id, group_id, payload.replacement_category_id
        )
        if replacement_id is not None and replacement_id in ids:
            # Ein Ersatz aus derselben Gruppe wird in diesem Vorgang selbst
            # archiviert - das waere sofort wieder eine tote Referenz.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_REPLACEMENT_INVALID
            )

    correlation_id = payload.operation_id
    for category_id in ids:
        await _archive_category_row(
            session,
            household_id,
            actor_user_id,
            category_id,
            replacement_id,
            correlation_id,
            {"operation_id": str(payload.operation_id), "group_id": str(group_id)},
        )

    before = await fetch_group_snapshot(session, household_id, group_id)
    await session.execute(
        text(
            "update category_groups set is_archived = true, archived_at = now(), "
            "archived_by = cast(:actor as uuid) "
            "where id = :id and household_id = :household_id"
        ),
        {
            "actor": str(actor_user_id) if actor_user_id else None,
            "id": str(group_id),
            "household_id": str(household_id),
        },
    )
    after = await fetch_group_snapshot(session, household_id, group_id)
    await record_category_group_event(
        session,
        household_id,
        actor_user_id,
        group_id,
        "archive",
        before=before,
        after=after,
        correlation_id=correlation_id,
        metadata={
            "operation_id": str(payload.operation_id),
            "archived_categories": [str(value) for value in ids],
            "replacement_category_id": str(replacement_id) if replacement_id else None,
        },
    )
    await _mark_operation_applied(
        session, payload.operation_id, result_payload={"impact": impact}
    )
    return await load_catalog_group(session, household_id, group_id)


async def restore_group(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    group_id: UUID,
    *,
    correlation_id: UUID | None = None,
    expected_version: int | None = None,
) -> CatalogGroup:
    """Stellt eine Gruppe wieder her.

    Die enthaltenen Kategorien bleiben archiviert: sie sind einzeln
    wiederherstellbar, und ein stilles Massen-Restore waere die Umkehrung des
    ausdruecklich bestaetigten Cascade-Archivs."""
    group = await _load_group(session, household_id, group_id, lock=True)
    _assert_expected_version(int(group["version"]), expected_version)
    if not group["is_archived"]:
        return await load_catalog_group(session, household_id, group_id)

    await _assert_group_name_free(
        session, household_id, str(group["name"]), exclude_id=group_id
    )
    before = await fetch_group_snapshot(session, household_id, group_id)
    await session.execute(
        text(
            "update category_groups set is_archived = false, archived_at = null, "
            "archived_by = null where id = :id and household_id = :household_id"
        ),
        {"id": str(group_id), "household_id": str(household_id)},
    )
    after = await fetch_group_snapshot(session, household_id, group_id)
    await record_category_group_event(
        session,
        household_id,
        actor_user_id,
        group_id,
        "restore",
        before=before,
        after=after,
        correlation_id=correlation_id,
    )
    return await load_catalog_group(session, household_id, group_id)


# ---------------------------------------------------------------------------
# Wiederherstellen einer Kategorie
# ---------------------------------------------------------------------------


async def restore_category(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    category_id: UUID,
    payload: RestoreRequest,
    *,
    expected_version: int | None = None,
) -> CatalogCategory:
    category = await _load_category(session, household_id, category_id, lock=True)
    _assert_expected_version(int(category["version"]), expected_version)
    if category["merged_into_category_id"] is not None:
        # Ein Tombstone ist kein archivierter Datensatz, sondern der Beleg
        # eines abgeschlossenen Merges - seine Referenzen liegen beim Ziel.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_MERGED_NOT_RESTORABLE
        )

    correlation_id = uuid4()
    current_group_id = UUID(str(category["group_id"]))
    restored_group = False

    if payload.target_group_id is not None:
        await assert_group_active(session, household_id, payload.target_group_id)
        target_group_id = payload.target_group_id
    else:
        target_group_id = current_group_id
        group = await _load_group(session, household_id, current_group_id)
        if group["is_archived"]:
            if not payload.restore_group:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_GROUP_INACTIVE
                )
            await restore_group(
                session,
                household_id,
                actor_user_id,
                current_group_id,
                correlation_id=correlation_id,
            )
            restored_group = True

    name = payload.new_name or str(category["name"])
    await _assert_category_name_free(
        session, household_id, target_group_id, name, exclude_id=category_id
    )

    # Die bisherige Position bleibt erhalten; nur ein Gruppenwechsel setzt die
    # Kategorie ans Ende der Zielgruppe.
    sort_order = int(category["sort_order"])
    if target_group_id != current_group_id:
        sort_order = await _next_sort_order(session, household_id, target_group_id)

    before = await fetch_category_snapshot(session, household_id, category_id)
    await session.execute(
        text(
            "update categories set is_archived = false, archived_at = null, "
            "archived_by = null, group_id = :group_id, name = :name, "
            "sort_order = :sort_order where id = :id and household_id = :household_id"
        ),
        {
            "group_id": str(target_group_id),
            "name": name,
            "sort_order": sort_order,
            "id": str(category_id),
            "household_id": str(household_id),
        },
    )
    after = await fetch_category_snapshot(session, household_id, category_id)
    await record_category_event(
        session,
        household_id,
        actor_user_id,
        category_id,
        "restore",
        before=before,
        after=after,
        correlation_id=correlation_id,
        metadata={
            "target_group_id": str(target_group_id),
            "restored_group": restored_group,
            "renamed": payload.new_name is not None,
        },
    )
    return await load_catalog_category(session, household_id, category_id)


# ---------------------------------------------------------------------------
# Zusammenfuehren
# ---------------------------------------------------------------------------


async def _merge_endpoints(
    session: AsyncSession, household_id: UUID, request: MergeRequest
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prueft Quelle und Ziel und flacht Merge-Ketten ab.

    Das Ziel laeuft durch den Resolver: ist es selbst ein Tombstone, wird auf
    das endgueltige Ziel kanonisiert (Plan: "Merge-Ketten werden beim naechsten
    Merge abgeflacht"). Die Quelle laeuft bewusst NICHT durch den Resolver -
    eine bereits gemergte Quelle wuerde sonst auf ihr eigenes Ziel zeigen und
    der Vorgang waere ein sinnloser Selbst-Merge."""
    source = await _load_category(session, household_id, request.source_category_id)
    if source["merged_into_category_id"] is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_INACTIVE)

    target_id = await resolve_category_id(
        session, household_id, request.target_category_id, allow_none=False
    )
    if target_id is None:  # pragma: no cover - allow_none=False schliesst das aus
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_NOT_FOUND)
    if target_id == UUID(str(source["id"])):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_MERGE_SAME_CATEGORY
        )
    target = await _load_category(session, household_id, target_id)

    source_type = source["default_type"]
    target_type = target["default_type"]
    if source_type is not None and target_type is not None and source_type != target_type:
        # Eine Einnahme in eine Ausgabe zu mergen wuerde Berichte und
        # 50/30/20-Auswertungen dauerhaft verfaelschen.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_MERGE_TYPE_MISMATCH
        )
    return source, target


async def _merge_collisions(
    session: AsyncSession, household_id: UUID, source_id: UUID, target_id: UUID
) -> list[MergeCollision]:
    params = {
        "household_id": str(household_id),
        "source_id": str(source_id),
        "target_id": str(target_id),
    }
    allocations = await session.execute(
        text(
            "select count(*) as both_planned, "
            "count(*) filter (where src.rollover_rule <> tgt.rollover_rule) as rule_conflicts "
            "from budget_allocations src join budget_allocations tgt "
            "on tgt.budget_id = src.budget_id and tgt.household_id = src.household_id "
            "where src.household_id = :household_id and src.category_id = :source_id "
            "and tgt.category_id = :target_id"
        ),
        params,
    )
    allocation_row = allocations.one()

    duplicate_rules = await session.execute(
        text(
            "select count(*) as duplicates from classification_rules src "
            "where src.household_id = :household_id "
            f"and src.action_category_id = :source_id and {_SIMPLE_PAYEE_RULE.format(alias='src')} "
            "and exists (select 1 from classification_rules tgt "
            "where tgt.household_id = src.household_id "
            f"and tgt.action_category_id = :target_id and {_SIMPLE_PAYEE_RULE.format(alias='tgt')} "
            "and lower(btrim(tgt.match_payee_raw)) = lower(btrim(src.match_payee_raw)))"
        ),
        params,
    )
    duplicates = int(duplicate_rules.one().duplicates)

    collisions: list[MergeCollision] = []
    if int(allocation_row.both_planned) > 0:
        collisions.append(
            MergeCollision(
                kind="budget_allocation",
                detail="Planwerte werden im selben Budget addiert.",
                count=int(allocation_row.both_planned),
            )
        )
    if int(allocation_row.rule_conflicts) > 0:
        collisions.append(
            MergeCollision(
                kind="rollover_rule",
                detail="Unterschiedliche Uebertragsregel - die Regel des Ziels gilt.",
                count=int(allocation_row.rule_conflicts),
            )
        )
    if duplicates > 0:
        collisions.append(
            MergeCollision(
                kind="duplicate_rule",
                detail="Identische Haendlerregel - die Quellregel wird deaktiviert.",
                count=duplicates,
            )
        )
    return collisions


async def _merge_impact(
    session: AsyncSession,
    household_id: UUID,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
) -> tuple[dict[str, Any], CategoryReferenceCounts, int, list[MergeCollision]]:
    source_id = UUID(str(source["id"]))
    target_id = UUID(str(target["id"]))
    references = await count_category_references(session, household_id, source_id)
    transactions = await session.execute(
        text(
            "select count(distinct transaction_id) as affected from transaction_splits "
            "where household_id = :household_id and category_id = :category_id"
        ),
        {"household_id": str(household_id), "category_id": str(source_id)},
    )
    transactions_to_bump = int(transactions.one().affected)
    collisions = await _merge_collisions(session, household_id, source_id, target_id)
    impact = {
        "source_category_id": str(source_id),
        "source_version": int(source["version"]),
        "target_category_id": str(target_id),
        "target_version": int(target["version"]),
        "references": references.model_dump(),
        "transactions_to_bump": transactions_to_bump,
        "collisions": [collision.model_dump() for collision in collisions],
    }
    return impact, references, transactions_to_bump, collisions


async def preview_merge(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    request: MergeRequest,
) -> MergePreview:
    source, target = await _merge_endpoints(session, household_id, request)
    impact, references, transactions_to_bump, collisions = await _merge_impact(
        session, household_id, source, target
    )
    operation_id, expires_at = await _create_operation(
        session,
        household_id,
        actor_user_id,
        "merge",
        impact,
        source_id=UUID(str(source["id"])),
        target_id=UUID(str(target["id"])),
        source_version=int(source["version"]),
        target_version=int(target["version"]),
    )
    return MergePreview(
        operation_id=operation_id,
        expires_at=expires_at,
        source_category_id=UUID(str(source["id"])),
        source_category_name=str(source["name"]),
        target_category_id=UUID(str(target["id"])),
        target_category_name=str(target["name"]),
        references=references,
        transactions_to_bump=transactions_to_bump,
        collisions=collisions,
    )


async def _additive_budget_merge(
    session: AsyncSession, household_id: UUID, source_id: UUID, target_id: UUID, table: str
) -> tuple[int, int]:
    """Addiert kollidierende Budgetzeilen und haengt den Rest um.

    Sowohl budget_allocations als auch budget_rollovers sind je
    (budget_id, category_id) eindeutig - ein blindes Umhaengen wuerde am
    Unique-Index scheitern, sobald Quelle und Ziel im selben Budget liegen.
    Der Betrag wird deshalb zuerst auf die Zielzeile addiert, die Quellzeile
    danach geloescht. Summenneutral, wie der Plan es verlangt.

    Rueckgabe: (addierte Zeilen, umgehaengte Zeilen)."""
    amount_column = "planned_minor" if table == "budget_allocations" else "amount_minor"
    params = {
        "household_id": str(household_id),
        "source_id": str(source_id),
        "target_id": str(target_id),
    }
    merged = await session.execute(
        text(
            f"update {table} tgt set {amount_column} = tgt.{amount_column} + src.{amount_column} "
            f"from {table} src where src.household_id = :household_id "
            "and src.category_id = :source_id and tgt.household_id = :household_id "
            "and tgt.category_id = :target_id and tgt.budget_id = src.budget_id"
        ),
        params,
    )
    await session.execute(
        text(
            f"delete from {table} src where src.household_id = :household_id "
            "and src.category_id = :source_id and exists ("
            f"select 1 from {table} tgt where tgt.household_id = :household_id "
            "and tgt.category_id = :target_id and tgt.budget_id = src.budget_id)"
        ),
        params,
    )
    repointed = await session.execute(
        text(
            f"update {table} set category_id = :target_id "
            "where household_id = :household_id and category_id = :source_id"
        ),
        params,
    )
    return _rowcount(merged), _rowcount(repointed)


def _merge_result_from_payload(payload: Mapping[str, Any]) -> MergeResult:
    result = MergeResult.model_validate(dict(payload))
    result.idempotent_replay = True
    return result


async def apply_merge(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    payload: MergeApply,
) -> MergeResult:
    """Fuehrt den bestaetigten Merge in einer fokussierten Transaktion aus.

    Reihenfolge ist fachlich zwingend:
    * Versionssprung der Buchungen VOR dem Umhaengen der Splits, weil die
      betroffenen Buchungen danach nicht mehr ueber die Quelle auffindbar sind.
    * Tombstone der Quelle ganz zum Schluss, weil
      reject_archived_category_reference() jede Zuordnung zu einer bereits
      archivierten Kategorie ablehnt.
    """
    # Idempotenz zuerst: eine Wiederholung derselben request_id darf nicht am
    # inzwischen auf 'applied' stehenden Vorschausatz scheitern.
    replay = await session.execute(
        text(
            "select result from category_operations where household_id = :household_id "
            "and operation = 'merge' and request_id = :request_id and status = 'applied'"
        ),
        {"household_id": str(household_id), "request_id": payload.request_id},
    )
    replay_row = replay.first()
    if replay_row is not None:
        if replay_row.result is None:  # pragma: no cover - Apply schreibt immer ein Ergebnis
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_PREVIEW_STALE
            )
        return _merge_result_from_payload(replay_row.result)

    operation = await _load_operation(session, household_id, "merge", payload.operation_id)
    source_id = UUID(str(operation["source_id"]))
    target_id = UUID(str(operation["target_id"]))

    # Immer in derselben Reihenfolge sperren, sonst koennen zwei
    # gegenlaeufige Merges (A->B und B->A) einander blockieren.
    for locked_id in sorted([source_id, target_id], key=str):
        await _load_category(session, household_id, locked_id, lock=True)

    source, target = await _merge_endpoints(
        session,
        household_id,
        MergeRequest(source_category_id=source_id, target_category_id=target_id),
    )
    impact, references, transactions_to_bump, collisions = await _merge_impact(
        session, household_id, source, target
    )
    _assert_preview_fresh(operation, _fingerprint(impact))

    params = {
        "household_id": str(household_id),
        "source_id": str(source_id),
        "target_id": str(target_id),
    }

    # 1. Genau ein Versionssprung je betroffener Buchung - auch wenn mehrere
    # ihrer Splits umgehaengt werden. Ein explizit gesetzter Wert laesst
    # bump_transaction_version() genau einmal hochzaehlen.
    bumped = await session.execute(
        text(
            "update transactions set version = version + 1 "
            "where household_id = :household_id and id in ("
            "select transaction_id from transaction_splits "
            "where household_id = :household_id and category_id = :source_id)"
        ),
        params,
    )
    bumped_transactions = _rowcount(bumped)

    # 2. Uebertragsregeln festhalten, bevor die Quellzeilen verschwinden.
    conflicts = await session.execute(
        text(
            "select src.budget_id, src.rollover_rule as source_rule, "
            "tgt.rollover_rule as target_rule from budget_allocations src "
            "join budget_allocations tgt on tgt.budget_id = src.budget_id "
            "and tgt.household_id = src.household_id "
            "where src.household_id = :household_id and src.category_id = :source_id "
            "and tgt.category_id = :target_id and src.rollover_rule <> tgt.rollover_rule"
        ),
        params,
    )
    rollover_conflicts = [
        {
            "budget_id": str(row.budget_id),
            "source_rule": str(row.source_rule),
            "target_rule": str(row.target_rule),
        }
        for row in conflicts
    ]

    # 3. Referenzen migrieren.
    splits = await session.execute(
        text(
            "update transaction_splits set category_id = :target_id "
            "where household_id = :household_id and category_id = :source_id"
        ),
        params,
    )
    moved: dict[str, int] = {"transaction_splits": _rowcount(splits)}
    for table, column, field in _FORWARD_TABLES:
        result = await session.execute(
            text(
                f"update {table} set {column} = :target_id "
                f"where household_id = :household_id and {column} = :source_id"
            ),
            params,
        )
        moved[field] = _rowcount(result)
    repointed_rules, deactivated_rules = await _repoint_classification_rules(
        session, household_id, source_id, target_id
    )
    moved["classification_rules"] = repointed_rules

    merged_allocations, repointed_allocations = await _additive_budget_merge(
        session, household_id, source_id, target_id, "budget_allocations"
    )
    moved["budget_allocations"] = merged_allocations + repointed_allocations
    merged_rollovers, repointed_rollovers = await _additive_budget_merge(
        session, household_id, source_id, target_id, "budget_rollovers"
    )
    moved["budget_rollovers"] = merged_rollovers + repointed_rollovers
    # classification_decisions bleibt bewusst auf der historischen Kategorie:
    # ein Entscheidungsnachweis darf nicht rueckwirkend umgeschrieben werden.
    moved["classification_decisions"] = 0

    # 4. Bestehende Tombstones nachziehen, damit keine Kette entsteht.
    await session.execute(
        text(
            "update categories set merged_into_category_id = :target_id "
            "where household_id = :household_id and merged_into_category_id = :source_id "
            "and id <> :source_id"
        ),
        params,
    )

    # 5. Quelle wird Tombstone.
    source_before = await fetch_category_snapshot(session, household_id, source_id)
    target_before = await fetch_category_snapshot(session, household_id, target_id)
    await session.execute(
        text(
            "update categories set is_archived = true, archived_at = now(), "
            "archived_by = cast(:actor as uuid), merged_into_category_id = :target_id "
            "where id = :source_id and household_id = :household_id"
        ),
        {**params, "actor": str(actor_user_id) if actor_user_id else None},
    )
    source_after = await fetch_category_snapshot(session, household_id, source_id)
    target_after = await fetch_category_snapshot(session, household_id, target_id)

    moved_references = CategoryReferenceCounts(**moved)
    result_model = MergeResult(
        source_category_id=source_id,
        target_category_id=target_id,
        moved_references=moved_references,
        bumped_transactions=bumped_transactions,
        merged_budget_allocations=merged_allocations,
        deactivated_rules=deactivated_rules,
    )

    # 6. Ein zusammenhaengender Audit-Vorgang statt tausender Einzelereignisse.
    correlation_id = payload.operation_id
    metadata = {
        "operation_id": str(payload.operation_id),
        "request_id": payload.request_id,
        "source_category_id": str(source_id),
        "target_category_id": str(target_id),
        "moved_references": moved,
        "bumped_transactions": bumped_transactions,
        "merged_budget_allocations": merged_allocations,
        "merged_budget_rollovers": merged_rollovers,
        "deactivated_rules": deactivated_rules,
        "rollover_rule_conflicts": rollover_conflicts,
        "collisions": [collision.model_dump() for collision in collisions],
        "reference_counts_before": references.model_dump(),
        "transactions_to_bump": transactions_to_bump,
    }
    await record_category_event(
        session,
        household_id,
        actor_user_id,
        source_id,
        "merge",
        before=source_before,
        after=source_after,
        correlation_id=correlation_id,
        metadata={**metadata, "role": "source"},
    )
    await record_category_event(
        session,
        household_id,
        actor_user_id,
        target_id,
        "merge",
        before=target_before,
        after=target_after,
        correlation_id=correlation_id,
        metadata={**metadata, "role": "target"},
    )

    await _mark_operation_applied(
        session,
        payload.operation_id,
        request_id=payload.request_id,
        result_payload=result_model.model_dump(),
    )
    return result_model
