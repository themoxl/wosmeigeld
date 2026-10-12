"""Standardpaket-Dienst (docs/KATEGORISIERUNGSPLAN.md, Abschnitt "Versioniertes
Standardpaket und bestehende Haushalte").

Die additive Installation selbst liegt bewusst als SQL-Funktion
`install_category_taxonomy()` in Migration 0007: sie ist idempotent, laeuft
atomar und ist damit auch fuer `create_household()` die einzige Quelle der
Wahrheit. Dieser Dienst liefert das, was SQL nicht sinnvoll leisten kann - den
fachlichen Abgleich mit dem Bestand ("bereits vorhanden", "moegliche
Entsprechung", "Namenskonflikt"), die kurzlebige Vorschau in
`category_operations` und die Idempotenz ueber `request_id`.

Grundregel des Plans: Anwenden ist rein additiv. Es wird nichts geloescht,
archiviert, verschoben oder umbenannt, und eine vorhandene Kategorie wird nur
nach ausdruecklicher Bestaetigung (`confirmed_matches`) einem Standard-Schluessel
zugeordnet.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Final, cast
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.category import (
    StandardPackageApply,
    StandardPackageEntry,
    StandardPackagePreview,
    StandardPackageResult,
    StandardPackageStatus,
)
from app.services import category_service
from app.services.audit_service import record_category_event

TAXONOMY_OPERATION: Final = "taxonomy_apply"

CATEGORY_OPERATION_NOT_FOUND: Final = "category_operation_not_found"
CATEGORY_PREVIEW_STALE: Final = "category_preview_stale"
CATEGORY_TAXONOMY_UNAVAILABLE: Final = "category_taxonomy_unavailable"


async def _latest_released_version(session: AsyncSession) -> int | None:
    result = await session.execute(
        text("select max(version) as version from category_taxonomy_versions where is_released")
    )
    row = result.first()
    return int(row.version) if row is not None and row.version is not None else None


async def get_status(session: AsyncSession, household_id: UUID) -> StandardPackageStatus:
    """Installierter und verfuegbarer Paketstand eines Haushalts."""
    result = await session.execute(
        text(
            "select taxonomy_version, revision, applied_at from household_category_taxonomy "
            "where household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    row = result.first()
    installed = (
        int(row.taxonomy_version) if row is not None and row.taxonomy_version is not None else None
    )
    available = await _latest_released_version(session)
    return StandardPackageStatus(
        installed_version=installed,
        available_version=available,
        installed_at=row.applied_at if row is not None else None,
        taxonomy_revision=int(row.revision) if row is not None else 0,
        upgrade_available=(
            available is not None and (installed is None or available > installed)
        ),
    )


# ---------------------------------------------------------------------------
# Abgleich Vorlage <-> Bestand
# ---------------------------------------------------------------------------


async def _build_plan(
    session: AsyncSession, household_id: UUID
) -> tuple[int, list[str], list[StandardPackageEntry]]:
    """Vergleicht die neueste freigegebene Paketversion mit dem Haushalt.

    Wird zweimal berechnet - einmal fuer die Vorschau, einmal beim Anwenden.
    Genau das ist der Zweck: weicht das Ergebnis ab, hat sich der Bestand
    zwischenzeitlich geaendert und die Vorschau ist wertlos (409)."""
    version = await _latest_released_version(session)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_TAXONOMY_UNAVAILABLE
        )

    group_templates = [
        dict(row._mapping)
        for row in await session.execute(
            text(
                "select semantic_key, name, sort_order from category_group_templates "
                "where taxonomy_version = :version order by sort_order, semantic_key"
            ),
            {"version": version},
        )
    ]
    category_templates = [
        dict(row._mapping)
        for row in await session.execute(
            text(
                "select semantic_key, group_semantic_key, name, default_type, budget_bucket, "
                "synonyms from category_templates where taxonomy_version = :version "
                "order by group_semantic_key, sort_order, semantic_key"
            ),
            {"version": version},
        )
    ]

    household_groups = [
        dict(row._mapping)
        for row in await session.execute(
            text(
                "select id, name, semantic_key, is_archived from category_groups "
                "where household_id = :household_id order by sort_order, name, id"
            ),
            {"household_id": str(household_id)},
        )
    ]
    household_categories = [
        dict(row._mapping)
        for row in await session.execute(
            text(
                "select id, group_id, name, semantic_key, is_archived from categories "
                "where household_id = :household_id order by sort_order, name, id"
            ),
            {"household_id": str(household_id)},
        )
    ]

    normalize = category_service.normalize_category_name

    groups_by_key: dict[str, dict[str, Any]] = {
        str(group["semantic_key"]): group
        for group in household_groups
        if group["semantic_key"] is not None
    }
    # Nur aktive Gruppen kommen als Ziel in Frage - dieselbe Wiederverwendung,
    # die install_category_taxonomy() vornimmt, damit Vorschau und Anwendung
    # dieselbe Zielgruppe bestimmen.
    active_group_by_name: dict[str, dict[str, Any]] = {}
    for group in household_groups:
        if not group["is_archived"]:
            active_group_by_name.setdefault(normalize(str(group["name"])), group)

    categories_by_key: dict[str, dict[str, Any]] = {
        str(category["semantic_key"]): category
        for category in household_categories
        if category["semantic_key"] is not None
    }
    active_in_group_by_name: dict[tuple[str, str], dict[str, Any]] = {}
    # Kandidaten fuer eine "moegliche Entsprechung": aktiv und noch ohne
    # Standard-Schluessel. Eine Kategorie, die bereits einen anderen Schluessel
    # traegt, ist fachlich etwas anderes und darf nicht umgedeutet werden.
    free_by_name: dict[str, dict[str, Any]] = {}
    for category in household_categories:
        if category["is_archived"]:
            continue
        normalized = normalize(str(category["name"]))
        active_in_group_by_name.setdefault((str(category["group_id"]), normalized), category)
        if category["semantic_key"] is None:
            free_by_name.setdefault(normalized, category)

    new_groups = [
        str(template["name"])
        for template in group_templates
        if str(template["semantic_key"]) not in groups_by_key
        and normalize(str(template["name"])) not in active_group_by_name
    ]
    group_template_names = {
        str(template["semantic_key"]): str(template["name"]) for template in group_templates
    }

    entries: list[StandardPackageEntry] = []
    for template in category_templates:
        semantic_key = str(template["semantic_key"])
        group_key = str(template["group_semantic_key"])
        group_name = group_template_names.get(group_key, group_key)
        normalized_name = normalize(str(template["name"]))

        entry_status: str = "new"
        existing: dict[str, Any] | None = categories_by_key.get(semantic_key)
        if existing is not None:
            entry_status = "already_present"
        else:
            target_group = groups_by_key.get(group_key) or active_group_by_name.get(
                normalize(group_name)
            )
            occupant = (
                active_in_group_by_name.get((str(target_group["id"]), normalized_name))
                if target_group is not None
                else None
            )
            if occupant is not None and occupant["semantic_key"] is not None:
                # Gleicher Name in der Zielgruppe, aber bereits als andere
                # Standardkategorie gefuehrt - das ist ein echter Konflikt.
                entry_status = "name_conflict"
                existing = occupant
            else:
                candidate = free_by_name.get(normalized_name)
                if candidate is None:
                    for synonym in template["synonyms"] or []:
                        candidate = free_by_name.get(normalize(str(synonym)))
                        if candidate is not None:
                            break
                if candidate is not None:
                    entry_status = "possible_match"
                    existing = candidate

        entries.append(
            StandardPackageEntry(
                semantic_key=semantic_key,
                name=str(template["name"]),
                group_semantic_key=group_key,
                group_name=group_name,
                default_type=template["default_type"],
                budget_bucket=template["budget_bucket"],
                status=cast(Any, entry_status),
                existing_category_id=(
                    UUID(str(existing["id"])) if existing is not None else None
                ),
                existing_category_name=(
                    str(existing["name"]) if existing is not None else None
                ),
            )
        )

    return version, new_groups, entries


def _impact(
    version: int, new_groups: list[str], entries: list[StandardPackageEntry]
) -> dict[str, Any]:
    return {
        "taxonomy_version": version,
        "new_groups": new_groups,
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }


def _fingerprint(impact: dict[str, Any]) -> str:
    """Stabiler Hash ueber den berechneten Plan.

    Bewusst ohne die Baum-Revision: eine Aenderung an einer voellig anderen
    Kategorie erhoeht zwar die Revision, macht diese Vorschau aber nicht
    falsch."""
    canonical = json.dumps(impact, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def build_preview(
    session: AsyncSession, household_id: UUID, actor_user_id: UUID | None
) -> StandardPackagePreview:
    """Berechnet die additive Ergaenzung und legt sie als kurzlebige Vorschau ab."""
    version, new_groups, entries = await _build_plan(session, household_id)
    impact = _impact(version, new_groups, entries)
    revision = await category_service.get_taxonomy_revision(session, household_id)

    result = await session.execute(
        text(
            "insert into category_operations (household_id, operation, status, created_by, "
            "taxonomy_revision, fingerprint, impact) "
            "values (:household_id, :operation, 'preview', :created_by, :revision, "
            ":fingerprint, cast(:impact as jsonb)) returning id, expires_at"
        ),
        {
            "household_id": str(household_id),
            "operation": TAXONOMY_OPERATION,
            "created_by": str(actor_user_id) if actor_user_id else None,
            "revision": revision,
            "fingerprint": _fingerprint(impact),
            "impact": json.dumps(impact, ensure_ascii=False),
        },
    )
    row = result.one()

    def _count(wanted: str) -> int:
        return sum(1 for entry in entries if entry.status == wanted)

    return StandardPackagePreview(
        operation_id=UUID(str(row.id)),
        expires_at=row.expires_at,
        taxonomy_version=version,
        taxonomy_revision=revision,
        new_groups=new_groups,
        entries=entries,
        new_count=_count("new"),
        already_present_count=_count("already_present"),
        possible_match_count=_count("possible_match"),
        name_conflict_count=_count("name_conflict"),
    )


# ---------------------------------------------------------------------------
# Anwenden
# ---------------------------------------------------------------------------


async def _find_applied_result(
    session: AsyncSession, household_id: UUID, request_id: str
) -> StandardPackageResult | None:
    """Idempotenz ueber den partiellen Unique-Index
    category_operations_request_uniq: derselbe request_id liefert dasselbe
    Ergebnis, statt das Paket ein zweites Mal anzuwenden."""
    result = await session.execute(
        text(
            "select result from category_operations where household_id = :household_id "
            "and operation = :operation and request_id = :request_id and status = 'applied'"
        ),
        {
            "household_id": str(household_id),
            "operation": TAXONOMY_OPERATION,
            "request_id": request_id,
        },
    )
    row = result.first()
    if row is None or row.result is None:
        return None
    stored = dict(row.result)
    stored["idempotent_replay"] = True
    return StandardPackageResult(**stored)


async def apply(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    payload: StandardPackageApply,
) -> StandardPackageResult:
    """Wendet eine bestaetigte Vorschau an - additiv, idempotent und ohne
    stille Umdeutung vorhandener Kategorien."""
    if payload.request_id is not None:
        replay = await _find_applied_result(session, household_id, payload.request_id)
        if replay is not None:
            return replay

    operation = await session.execute(
        text(
            "select id, status, expires_at, fingerprint from category_operations "
            "where id = :id and household_id = :household_id and operation = :operation "
            "for update"
        ),
        {
            "id": str(payload.operation_id),
            "household_id": str(household_id),
            "operation": TAXONOMY_OPERATION,
        },
    )
    row = operation.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_OPERATION_NOT_FOUND
        )
    if row.status != "preview" or row.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_PREVIEW_STALE
        )

    version, new_groups, entries = await _build_plan(session, household_id)
    if _fingerprint(_impact(version, new_groups, entries)) != row.fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_PREVIEW_STALE
        )

    correlation_id = uuid4()
    known_keys = {entry.semantic_key for entry in entries}

    # Bestaetigte Entsprechungen zuerst: waere zuerst installiert worden, haette
    # install_category_taxonomy() die Standardkategorie bereits angelegt und der
    # semantic_key liesse sich nicht mehr an die vorhandene Kategorie haengen.
    linked_existing = 0
    conflicts = 0
    for semantic_key, category_id in payload.confirmed_matches.items():
        if semantic_key not in known_keys:
            conflicts += 1
            continue
        updated = await session.execute(
            text(
                "update categories set semantic_key = :semantic_key, taxonomy_version = :version "
                "where id = :category_id and household_id = :household_id "
                "and not is_archived and semantic_key is null"
            ),
            {
                "semantic_key": semantic_key,
                "version": version,
                "category_id": str(category_id),
                "household_id": str(household_id),
            },
        )
        if cast(CursorResult[Any], updated).rowcount == 1:
            linked_existing += 1
        else:
            conflicts += 1

    installed = await session.execute(
        text("select install_category_taxonomy(:household_id, :version) as summary"),
        {"household_id": str(household_id), "version": version},
    )
    summary = dict(installed.one().summary)

    result = StandardPackageResult(
        taxonomy_version=int(summary["taxonomy_version"]),
        created_groups=int(summary["created_groups"]),
        created_categories=int(summary["created_categories"]),
        linked_existing=linked_existing,
        skipped_name_conflicts=int(summary["skipped_name_conflicts"]) + conflicts,
    )

    await session.execute(
        text(
            "update category_operations set status = 'applied', applied_at = now(), "
            "request_id = coalesce(:request_id, request_id), "
            "result = cast(:result as jsonb) "
            "where id = :id and household_id = :household_id"
        ),
        {
            "request_id": payload.request_id,
            "result": json.dumps(result.model_dump(), ensure_ascii=False),
            "id": str(payload.operation_id),
            "household_id": str(household_id),
        },
    )

    # Ein Paket-Apply betrifft den gesamten Kategoriebaum und nicht eine
    # einzelne Zeile - als Entitaet wird deshalb der Haushalt gefuehrt, die
    # Einzelheiten stehen in den Metadaten.
    await record_category_event(
        session,
        household_id,
        actor_user_id,
        household_id,
        "taxonomy_apply",
        after=result.model_dump(),
        correlation_id=correlation_id,
        metadata={
            "operation_id": str(payload.operation_id),
            "taxonomy_version": version,
            "confirmed_matches": {
                key: str(value) for key, value in payload.confirmed_matches.items()
            },
        },
    )
    return result
