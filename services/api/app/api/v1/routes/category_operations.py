"""Lebenszyklus-Endpunkte der Kategorienverwaltung (Lieferabschnitte 2 und 4
aus docs/KATEGORISIERUNGSPLAN.md): Verschieben, Archivieren, Wiederherstellen
und Zusammenfuehren - jeweils als Paar aus Vorschau und bestaetigtem Anwenden.

Bewusst ein zweiter Router mit demselben Prefix `/categories` neben
`routes/categories.py`: dort liegt das schlichte CRUD des Kategoriebaums, hier
die zusammengesetzten Operationen mit Vorschau, Sperren und Referenzmigration.
Zwei Dateien statt einer 1500-Zeilen-Route; nach aussen ist es derselbe
Ressourcenpfad.

Reihenfolge in dieser Datei: erst die literalen Pfade (`/merge-preview`,
`/merge`, `/groups/...`), dann die Muster mit `{category_id}`. Starlette
prueft Routen in Deklarationsreihenfolge - eine vorgezogene Musterroute wuerde
`/categories/merge` als Kategorie-UUID interpretieren und mit 422 antworten.

Die fachliche Absicherung gegen parallele Aenderungen leistet hier der
Fingerprint der Vorschau (siehe category_operations_service). `If-Match` wird
zusaetzlich akzeptiert und gegen die aktuelle Version geprueft, ist aber nicht
verpflichtend: die Vorschauantworten fuehren keine Entitaetsversion, ein
Pflichtheader waere ohne zweiten Read nicht erfuellbar.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    require_household_editor,
    require_household_owner,
)
from app.auth.jwt import AuthenticatedUser
from app.core.concurrency import entity_etag, parse_if_match
from app.schemas.category import (
    ArchiveApply,
    ArchivePreview,
    CatalogCategory,
    CatalogGroup,
    GroupArchiveApply,
    GroupArchivePreview,
    MergeApply,
    MergePreview,
    MergeRequest,
    MergeResult,
    MoveApply,
    MovePreview,
    RestoreRequest,
)
from app.services import category_operations_service as operations

router = APIRouter(prefix="/categories", tags=["categories"])

HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
# Der Merge ist irreversibel und veraendert historische Buchungen - laut Plan
# darf ihn ausschliesslich der Owner ausloesen.
HouseholdOwnerSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_owner)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
IfMatchHeader = Annotated[str | None, Header(alias="If-Match")]


class MovePreviewRequest(BaseModel):
    """Eigenes, kleines Anfragemodell: die Vorschau kennt noch keine
    `operation_id` und kann deshalb nicht `MoveApply` wiederverwenden."""

    target_group_id: UUID


def _expected_version(if_match: str | None) -> int | None:
    """`If-Match` in eine erwartete Entitaetsversion uebersetzen. Geprueft wird
    sie erst im Service, nachdem die Zeile gesperrt ist."""
    return parse_if_match(if_match, required=False)


# ---------------------------------------------------------------------------
# Zusammenfuehren (literale Pfade zuerst)
# ---------------------------------------------------------------------------


@router.post("/merge-preview", response_model=MergePreview)
async def preview_merge(
    payload: MergeRequest, ctx: HouseholdEditorSession, user: CurrentUser
) -> MergePreview:
    session, household_id = ctx
    return await operations.preview_merge(session, household_id, user.id, payload)


@router.post("/merge", response_model=MergeResult)
async def apply_merge(
    payload: MergeApply, ctx: HouseholdOwnerSession, user: CurrentUser
) -> MergeResult:
    session, household_id = ctx
    return await operations.apply_merge(session, household_id, user.id, payload)


# ---------------------------------------------------------------------------
# Gruppen
# ---------------------------------------------------------------------------


@router.post("/groups/{group_id}/archive-preview", response_model=GroupArchivePreview)
async def preview_group_archive(
    group_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> GroupArchivePreview:
    session, household_id = ctx
    return await operations.preview_group_archive(session, household_id, user.id, group_id)


@router.post("/groups/{group_id}/archive", response_model=CatalogGroup)
async def archive_group(
    group_id: UUID,
    payload: GroupArchiveApply,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CatalogGroup:
    session, household_id = ctx
    group = await operations.apply_group_archive(
        session,
        household_id,
        user.id,
        group_id,
        payload,
        expected_version=_expected_version(if_match),
    )
    response.headers["ETag"] = entity_etag(group.version)
    return group


@router.post("/groups/{group_id}/restore", response_model=CatalogGroup)
async def restore_group(
    group_id: UUID,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CatalogGroup:
    session, household_id = ctx
    group = await operations.restore_group(
        session,
        household_id,
        user.id,
        group_id,
        expected_version=_expected_version(if_match),
    )
    response.headers["ETag"] = entity_etag(group.version)
    return group


# ---------------------------------------------------------------------------
# Verschieben
# ---------------------------------------------------------------------------


@router.post("/{category_id}/move-preview", response_model=MovePreview)
async def preview_move(
    category_id: UUID,
    payload: MovePreviewRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> MovePreview:
    session, household_id = ctx
    return await operations.preview_move(
        session, household_id, user.id, category_id, payload.target_group_id
    )


@router.post("/{category_id}/move", response_model=CatalogCategory)
async def apply_move(
    category_id: UUID,
    payload: MoveApply,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CatalogCategory:
    session, household_id = ctx
    category = await operations.apply_move(
        session,
        household_id,
        user.id,
        category_id,
        payload,
        expected_version=_expected_version(if_match),
    )
    response.headers["ETag"] = entity_etag(category.version)
    return category


# ---------------------------------------------------------------------------
# Archivieren und Wiederherstellen
# ---------------------------------------------------------------------------


@router.post("/{category_id}/archive-preview", response_model=ArchivePreview)
async def preview_archive(
    category_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> ArchivePreview:
    session, household_id = ctx
    return await operations.preview_archive(session, household_id, user.id, category_id)


@router.post("/{category_id}/archive", response_model=CatalogCategory)
async def apply_archive(
    category_id: UUID,
    payload: ArchiveApply,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CatalogCategory:
    session, household_id = ctx
    category = await operations.apply_archive(
        session,
        household_id,
        user.id,
        category_id,
        payload,
        expected_version=_expected_version(if_match),
    )
    response.headers["ETag"] = entity_etag(category.version)
    return category


@router.post("/{category_id}/restore", response_model=CatalogCategory)
async def restore_category(
    category_id: UUID,
    payload: RestoreRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CatalogCategory:
    session, household_id = ctx
    category = await operations.restore_category(
        session,
        household_id,
        user.id,
        category_id,
        payload,
        expected_version=_expected_version(if_match),
    )
    response.headers["ETag"] = entity_etag(category.version)
    return category
