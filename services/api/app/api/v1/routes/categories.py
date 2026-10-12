"""Kategorienverwaltung: Picker-Baum, Katalog, CRUD, Sortierung und Standardpaket
(docs/KATEGORISIERUNGSPLAN.md, Lieferabschnitt 1 "Standardpaket, CRUD und
sichtbare Zuordnung").

`GET /v1/categories` bleibt unveraendert der rueckwaertskompatible aktive
Picker-Baum; alles Verwaltende haengt darunter und liefert die reicheren
Katalogmodelle. Lebenszyklus-Operationen mit Vorschau (Move, Archive, Restore,
Merge) liegen bewusst in einem eigenen Router - hier stehen nur Operationen, die
ohne Wirkungsvorschau auskommen.

Nebenlaeufigkeit: Einzelsatzaenderungen verlangen `If-Match` (428 ohne Header,
409 bei veraltetem Stand) und liefern die neue Version als `ETag`. Baumweite
Sortierungen pruefen stattdessen die Taxonomie-Revision, damit eine parallele
Strukturaenderung nicht stillschweigend ueberschrieben wird.
"""

from typing import Annotated, Any, Final, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
    require_household_owner,
)
from app.auth.jwt import AuthenticatedUser
from app.core.concurrency import entity_etag, parse_if_match, raise_version_conflict
from app.schemas.category import (
    CatalogCategory,
    CatalogGroup,
    CategoryCatalog,
    CategoryCreate,
    CategoryGroup,
    CategoryGroupCreate,
    CategoryGroupUpdate,
    CategoryUpdate,
    ReorderRequest,
    StandardPackageApply,
    StandardPackagePreview,
    StandardPackageResult,
    StandardPackageStatus,
)
from app.services import category_service, category_taxonomy_service
from app.services.audit_service import record_category_event, record_category_group_event

router = APIRouter(prefix="/categories", tags=["categories"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
# Ein haushaltsweites Paket-Upgrade ist laut Plan owner-only.
HouseholdOwnerSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_owner)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
IfMatchHeader = Annotated[str | None, Header(alias="If-Match")]

CATEGORY_DUPLICATE_NAME: Final = "category_duplicate_name"
CATEGORY_GROUP_DUPLICATE_NAME: Final = "category_group_duplicate_name"
CATEGORY_TYPE_LOCKED: Final = "category_type_locked"
CATEGORY_GROUP_REQUIRED: Final = "category_group_required"
CATEGORY_REORDER_DUPLICATE_IDS: Final = "category_reorder_duplicate_ids"

# Feste Spaltenlisten statt der Payload-Schluessel, damit kein Feldname
# ungeprueft in ein SET-Fragment wandert (gleiches Muster wie routes/rules.py).
_GROUP_UPDATABLE_COLUMNS: Final = ("name", "icon", "color")
_CATEGORY_UPDATABLE_COLUMNS: Final = (
    "name",
    "default_type",
    "budget_bucket",
    "is_budgeted",
    "icon",
    "color",
    "description",
    "synonyms",
    "is_tax_deductible",
)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


async def _assert_group_name_free(
    session: AsyncSession, household_id: UUID, name: str, *, exclude_id: UUID | None = None
) -> None:
    """Duplikatpruefung vor dem Schreiben. Der partielle Unique-Index
    category_groups_active_name_uniq bleibt die Absicherung; diese Vorpruefung
    existiert nur, damit der Normalfall eine saubere 409 statt eines
    abgebrochenen Statements liefert."""
    result = await session.execute(
        text(
            "select 1 from category_groups where household_id = :household_id "
            "and not is_archived and category_normalize_name(name) = :normalized "
            "and (cast(:exclude_id as uuid) is null or id <> cast(:exclude_id as uuid)) "
            "limit 1"
        ),
        {
            "household_id": str(household_id),
            "normalized": category_service.normalize_category_name(name),
            "exclude_id": str(exclude_id) if exclude_id else None,
        },
    )
    if result.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_GROUP_DUPLICATE_NAME
        )


async def _assert_category_name_free(
    session: AsyncSession,
    household_id: UUID,
    group_id: UUID,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    """Derselbe Name darf laut Plan in unterschiedlichen Gruppen vorkommen -
    geprueft wird deshalb nur innerhalb der Gruppe."""
    result = await session.execute(
        text(
            "select 1 from categories where household_id = :household_id "
            "and group_id = :group_id and not is_archived "
            "and category_normalize_name(name) = :normalized "
            "and (cast(:exclude_id as uuid) is null or id <> cast(:exclude_id as uuid)) "
            "limit 1"
        ),
        {
            "household_id": str(household_id),
            "group_id": str(group_id),
            "normalized": category_service.normalize_category_name(name),
            "exclude_id": str(exclude_id) if exclude_id else None,
        },
    )
    if result.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_DUPLICATE_NAME
        )


def _assert_unique_ids(ordered_ids: list[UUID]) -> None:
    """Eine doppelte ID in der Reihenfolge waere kein harmloser Tippfehler,
    sondern eine mehrdeutige Zielordnung - lieber ablehnen als raten."""
    if len(set(ordered_ids)) != len(ordered_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=CATEGORY_REORDER_DUPLICATE_IDS
        )


async def _next_sort_order(
    session: AsyncSession, table: str, household_id: UUID, group_id: UUID | None = None
) -> int:
    condition = " and group_id = :group_id" if group_id is not None else ""
    params: dict[str, Any] = {"household_id": str(household_id)}
    if group_id is not None:
        params["group_id"] = str(group_id)
    result = await session.execute(
        text(
            f"select coalesce(max(sort_order) + 1, 0) as next from {table} "
            f"where household_id = :household_id{condition}"
        ),
        params,
    )
    row = result.first()
    return int(row.next) if row is not None else 0


def _assert_version(current_version: int, expected_version: int | None) -> None:
    if expected_version is not None and current_version != expected_version:
        raise_version_conflict(current_version)


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


@router.get("", response_model=list[CategoryGroup])
async def list_categories(ctx: HouseholdSession) -> list[CategoryGroup]:
    """Rueckwaertskompatibler aktiver Picker-Baum."""
    session, household_id = ctx
    return await category_service.load_active_tree(session, household_id)


@router.get("/catalog", response_model=CategoryCatalog)
async def get_category_catalog(
    ctx: HouseholdSession,
    include_archived: bool = True,
    include_usage: bool = False,
) -> CategoryCatalog:
    """Vollstaendige Verwaltungsansicht inklusive Baum-ETag."""
    session, household_id = ctx
    return await category_service.build_catalog(
        session,
        household_id,
        include_archived=include_archived,
        include_usage=include_usage,
    )


# ---------------------------------------------------------------------------
# Gruppen
# ---------------------------------------------------------------------------


@router.post("/groups", response_model=CatalogGroup, status_code=status.HTTP_201_CREATED)
async def create_category_group(
    payload: CategoryGroupCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> CatalogGroup:
    session, household_id = ctx
    await _assert_group_name_free(session, household_id, payload.name)

    sort_order = payload.sort_order
    if sort_order is None:
        sort_order = await _next_sort_order(session, "category_groups", household_id)

    try:
        result = await session.execute(
            text(
                "insert into category_groups (household_id, name, sort_order, icon, color, "
                "origin) values (:household_id, :name, :sort_order, :icon, :color, 'user') "
                "returning id"
            ),
            {
                "household_id": str(household_id),
                "name": payload.name,
                "sort_order": sort_order,
                "icon": payload.icon,
                "color": payload.color,
            },
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_GROUP_DUPLICATE_NAME
        ) from exc

    group_id = UUID(str(result.one().id))
    await record_category_group_event(
        session,
        household_id,
        user.id,
        group_id,
        "create",
        after=await category_service.fetch_group_snapshot(session, household_id, group_id),
    )
    group = await category_service.load_catalog_group(session, household_id, group_id)
    response.headers["ETag"] = entity_etag(group.version)
    return group


@router.patch("/groups/{group_id}", response_model=CatalogGroup)
async def update_category_group(
    group_id: UUID,
    payload: CategoryGroupUpdate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CatalogGroup:
    """Umbenennen und Darstellung. Archivierte Gruppen duerfen umbenannt
    werden - ein unleserlicher Altname bliebe sonst dauerhaft in Historie und
    Berichten stehen. Das Reaktivieren liegt im Operations-Router."""
    session, household_id = ctx
    expected_version = parse_if_match(if_match)

    before = await category_service.fetch_group_snapshot(session, household_id, group_id)
    if before is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=category_service.CATEGORY_GROUP_NOT_FOUND,
        )
    _assert_version(int(before["version"]), expected_version)

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is None:
        # name ist NOT NULL - ein ausdrueckliches null bedeutet hier "nicht
        # anfassen" und nicht "Namen loeschen".
        changes.pop("name", None)

    assignments: list[str] = []
    params: dict[str, Any] = {"id": str(group_id), "household_id": str(household_id)}
    for column in _GROUP_UPDATABLE_COLUMNS:
        if column not in changes:
            continue
        assignments.append(f"{column} = :{column}")
        params[column] = changes[column]

    if not assignments:
        group = await category_service.load_catalog_group(session, household_id, group_id)
        response.headers["ETag"] = entity_etag(group.version)
        return group

    if "name" in changes and not before["is_archived"]:
        await _assert_group_name_free(
            session, household_id, str(changes["name"]), exclude_id=group_id
        )

    try:
        await session.execute(
            text(
                "update category_groups set " + ", ".join(assignments) + " "
                "where id = :id and household_id = :household_id"
            ),
            params,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_GROUP_DUPLICATE_NAME
        ) from exc

    await record_category_group_event(
        session,
        household_id,
        user.id,
        group_id,
        "update",
        before=before,
        after=await category_service.fetch_group_snapshot(session, household_id, group_id),
    )
    group = await category_service.load_catalog_group(session, household_id, group_id)
    response.headers["ETag"] = entity_etag(group.version)
    return group


@router.post("/groups/reorder", response_model=CategoryCatalog)
async def reorder_category_groups(
    payload: ReorderRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> CategoryCatalog:
    """Atomare, versionsgepruefte Gruppensortierung."""
    session, household_id = ctx
    await category_service.assert_taxonomy_revision(
        session, household_id, payload.taxonomy_revision
    )
    _assert_unique_ids(payload.ordered_ids)

    known = {
        UUID(str(row.id))
        for row in await session.execute(
            text(
                "select id from category_groups "
                "where household_id = :household_id and not is_archived"
            ),
            {"household_id": str(household_id)},
        )
    }
    for group_id in payload.ordered_ids:
        if group_id not in known:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=category_service.CATEGORY_GROUP_NOT_FOUND,
            )

    for index, group_id in enumerate(payload.ordered_ids):
        await session.execute(
            text(
                "update category_groups set sort_order = :sort_order "
                "where id = :id and household_id = :household_id"
            ),
            {
                "sort_order": index,
                "id": str(group_id),
                "household_id": str(household_id),
            },
        )

    # Ein Ereignis fuer den ganzen Baum statt eines pro Zeile: die
    # Einzelzeilen waeren im Log unlesbar. Entitaet ist deshalb der Haushalt.
    await record_category_group_event(
        session,
        household_id,
        user.id,
        household_id,
        "reorder",
        correlation_id=uuid4(),
        metadata={"ordered_ids": [str(group_id) for group_id in payload.ordered_ids]},
    )
    return await category_service.build_catalog(session, household_id)


# ---------------------------------------------------------------------------
# Kategorien
# ---------------------------------------------------------------------------


@router.post("/reorder", response_model=CategoryCatalog)
async def reorder_categories(
    payload: ReorderRequest,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> CategoryCatalog:
    """Atomare Sortierung innerhalb genau einer Gruppe. Ein Gruppenwechsel
    laeuft ausschliesslich ueber move-preview/move."""
    session, household_id = ctx
    if payload.group_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=CATEGORY_GROUP_REQUIRED
        )
    await category_service.assert_taxonomy_revision(
        session, household_id, payload.taxonomy_revision
    )
    _assert_unique_ids(payload.ordered_ids)
    await category_service.assert_group_active(session, household_id, payload.group_id)

    known = {
        UUID(str(row.id))
        for row in await session.execute(
            text(
                "select id from categories where household_id = :household_id "
                "and group_id = :group_id and not is_archived"
            ),
            {"household_id": str(household_id), "group_id": str(payload.group_id)},
        )
    }
    for category_id in payload.ordered_ids:
        if category_id not in known:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=category_service.CATEGORY_NOT_FOUND,
            )

    for index, category_id in enumerate(payload.ordered_ids):
        await session.execute(
            text(
                "update categories set sort_order = :sort_order "
                "where id = :id and household_id = :household_id"
            ),
            {
                "sort_order": index,
                "id": str(category_id),
                "household_id": str(household_id),
            },
        )

    await record_category_group_event(
        session,
        household_id,
        user.id,
        payload.group_id,
        "reorder",
        correlation_id=uuid4(),
        metadata={"ordered_ids": [str(category_id) for category_id in payload.ordered_ids]},
    )
    return await category_service.build_catalog(session, household_id)


# ---------------------------------------------------------------------------
# Standardpaket
# ---------------------------------------------------------------------------


@router.get("/standard-package", response_model=StandardPackageStatus)
async def get_standard_package(ctx: HouseholdSession) -> StandardPackageStatus:
    session, household_id = ctx
    return await category_taxonomy_service.get_status(session, household_id)


@router.post("/standard-package/preview", response_model=StandardPackagePreview)
async def preview_standard_package(
    ctx: HouseholdEditorSession, user: CurrentUser
) -> StandardPackagePreview:
    session, household_id = ctx
    return await category_taxonomy_service.build_preview(session, household_id, user.id)


@router.post("/standard-package/apply", response_model=StandardPackageResult)
async def apply_standard_package(
    payload: StandardPackageApply,
    ctx: HouseholdOwnerSession,
    user: CurrentUser,
) -> StandardPackageResult:
    session, household_id = ctx
    return await category_taxonomy_service.apply(session, household_id, user.id, payload)


# ---------------------------------------------------------------------------
# Kategorie anlegen und aendern (Pfadparameter zuletzt)
# ---------------------------------------------------------------------------


@router.post("", response_model=CatalogCategory, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> CatalogCategory:
    session, household_id = ctx
    await category_service.assert_group_active(session, household_id, payload.group_id)
    await _assert_category_name_free(
        session, household_id, payload.group_id, payload.name
    )

    sort_order = await _next_sort_order(
        session, "categories", household_id, group_id=payload.group_id
    )
    try:
        result = await session.execute(
            text(
                "insert into categories (household_id, group_id, name, default_type, "
                "budget_bucket, is_budgeted, icon, color, description, synonyms, sort_order, "
                "origin, is_tax_deductible) "
                "values (:household_id, :group_id, :name, :default_type, "
                ":budget_bucket, :is_budgeted, :icon, :color, :description, "
                "cast(:synonyms as text[]), :sort_order, 'user', :is_tax_deductible) "
                "returning id"
            ),
            {
                "household_id": str(household_id),
                "group_id": str(payload.group_id),
                "name": payload.name,
                "default_type": payload.default_type,
                "budget_bucket": payload.budget_bucket,
                "is_budgeted": payload.is_budgeted,
                "icon": payload.icon,
                "color": payload.color,
                "description": payload.description,
                "synonyms": payload.synonyms,
                "sort_order": sort_order,
                "is_tax_deductible": payload.is_tax_deductible,
            },
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_DUPLICATE_NAME
        ) from exc

    category_id = UUID(str(result.one().id))
    await record_category_event(
        session,
        household_id,
        user.id,
        category_id,
        "create",
        after=await category_service.fetch_category_snapshot(
            session, household_id, category_id
        ),
    )
    category = await category_service.load_catalog_category(
        session, household_id, category_id
    )
    response.headers["ETag"] = entity_etag(category.version)
    return category


@router.patch("/{category_id}", response_model=CatalogCategory)
async def update_category(
    category_id: UUID,
    payload: CategoryUpdate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
    if_match: IfMatchHeader = None,
) -> CatalogCategory:
    """Name, Darstellung und erlaubte Eigenschaften. Ein Gruppenwechsel ist
    hier bewusst nicht moeglich (siehe CategoryUpdate); eine archivierte
    Kategorie bleibt umbenennbar, wird hier aber nie wieder aktiv."""
    session, household_id = ctx
    expected_version = parse_if_match(if_match)

    before = await category_service.load_category(session, household_id, category_id)
    if before is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=category_service.CATEGORY_NOT_FOUND
        )
    _assert_version(int(before["version"]), expected_version)

    changes = payload.model_dump(exclude_unset=True)
    changes.pop("clear_fields", None)
    if changes.get("name") is None:
        changes.pop("name", None)
    # clear_fields gewinnt bewusst gegen einen gleichzeitig gesetzten Wert: der
    # ausdrueckliche Loeschwunsch ist die spezifischere Angabe.
    for cleared_column in payload.clear_fields:
        changes[cleared_column] = None

    assignments: list[str] = []
    params: dict[str, Any] = {"id": str(category_id), "household_id": str(household_id)}
    for column in _CATEGORY_UPDATABLE_COLUMNS:
        if column not in changes:
            continue
        placeholder = (
            "cast(:synonyms as text[])" if column == "synonyms" else f":{column}"
        )
        assignments.append(f"{column} = {placeholder}")
        params[column] = changes[column]

    if not assignments:
        category = await category_service.load_catalog_category(
            session, household_id, category_id
        )
        response.headers["ETag"] = entity_etag(category.version)
        return category

    if "default_type" in changes and changes["default_type"] != before["default_type"]:
        # Plan: "default_type ist nach der ersten fachlichen Referenz nicht mehr
        # still aenderbar" - sonst wuerde eine Umdeutung rueckwirkend Berichte
        # und Budgets verschieben.
        usage = await category_service.count_category_references(
            session, household_id, category_id
        )
        if usage.total > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_TYPE_LOCKED
            )

    if "name" in changes and not before["is_archived"]:
        await _assert_category_name_free(
            session,
            household_id,
            UUID(str(before["group_id"])),
            str(changes["name"]),
            exclude_id=category_id,
        )

    try:
        updated = await session.execute(
            text(
                "update categories set " + ", ".join(assignments) + " "
                "where id = :id and household_id = :household_id"
            ),
            params,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_DUPLICATE_NAME
        ) from exc

    if cast(CursorResult[Any], updated).rowcount == 0:  # pragma: no cover - RLS-Restrisiko
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=category_service.CATEGORY_NOT_FOUND
        )

    await record_category_event(
        session,
        household_id,
        user.id,
        category_id,
        "update",
        before=before,
        after=await category_service.fetch_category_snapshot(
            session, household_id, category_id
        ),
    )
    category = await category_service.load_catalog_category(
        session, household_id, category_id
    )
    response.headers["ETag"] = entity_etag(category.version)
    return category
