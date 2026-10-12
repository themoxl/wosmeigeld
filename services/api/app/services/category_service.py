"""Zentraler Kategorie-Resolver (docs/KATEGORISIERUNGSPLAN.md, Abschnitt
"Zentraler Kategorie-Resolver").

Die einzige fachliche Eingangstuer fuer Kategorie-IDs. Jeder Schreibpfad -
Buchung anlegen, Split ersetzen, Bulk, Review, Regeln, Fixkosten, Abos,
Budgets, Sparziele, Import und KI - laesst eine eingehende ID hier pruefen,
bevor sie in ein Statement gelangt:

* gehoert die Kategorie zum aktiven Haushalt?
* ist sie aktiv und ihre Gruppe aktiv?
* ist sie ein Merge-Tombstone, der auf ein aktives Ziel kanonisiert werden
  kann (verspaeteter Offline-Request mit der alten ID)?
* darf NULL in diesem Kontext verwendet werden?

Die zusammengesetzten Fremdschluessel `(category_id, household_id)` und der
Trigger `reject_archived_category_reference()` aus
`supabase/migrations/0001_schema.sql` bleiben die zweite
Schutzschicht - auch ein vergessener Worker- oder SQL-Pfad kann damit keine
haushaltsfremde oder archivierte Kategorie zuordnen.
"""

from typing import Any, Final
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.category import (
    CatalogCategory,
    CatalogGroup,
    Category,
    CategoryCatalog,
    CategoryGroup,
    CategoryReferenceCounts,
)

CATEGORY_NOT_FOUND: Final = "category_not_found"
CATEGORY_INACTIVE: Final = "category_inactive"
CATEGORY_REQUIRED: Final = "category_required"
CATEGORY_GROUP_NOT_FOUND: Final = "category_group_not_found"
CATEGORY_GROUP_INACTIVE: Final = "category_group_inactive"

# Merge-Ketten werden beim Merge abgeflacht; die Schleifengrenze faengt nur
# eine durch Fremdeingriff entstandene zyklische Kette ab, statt haengen zu
# bleiben.
_MAX_MERGE_HOPS: Final = 16

_CATEGORY_SNAPSHOT_COLUMNS: Final = (
    "id, household_id, group_id, name, color, icon, is_budgeted, default_type, "
    "budget_bucket, is_archived, archived_at, sort_order, semantic_key, origin, "
    "taxonomy_version, description, synonyms, is_tax_deductible, "
    "merged_into_category_id, version"
)

_GROUP_SNAPSHOT_COLUMNS: Final = (
    "id, household_id, name, sort_order, is_archived, archived_at, semantic_key, "
    "origin, taxonomy_version, version"
)


def normalize_category_name(name: str) -> str:
    """Python-Spiegel von category_normalize_name() aus Migration 0007.

    Wird fuer Duplikatpruefung vor dem Schreiben und fuer die Suche im
    Standardpaket-Abgleich verwendet, damit API und Datenbank denselben
    Namensbegriff haben."""
    lowered = name.strip().lower()
    for source, replacement in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        lowered = lowered.replace(source, replacement)
    return " ".join(lowered.split())


async def load_category(
    session: AsyncSession, household_id: UUID, category_id: UUID
) -> dict[str, Any] | None:
    """Laedt eine Kategorie unabhaengig vom Archivstatus.

    Fuer historische Reads (Berichte, Budgets, Buchungsdetails) gedacht: der
    aktive Picker-Baum darf nie die einzige Quelle zur Namensaufloesung sein."""
    result = await session.execute(
        text(
            f"select {_CATEGORY_SNAPSHOT_COLUMNS}, "
            "(select g.is_archived from category_groups g where g.id = c.group_id) "
            "as group_is_archived "
            "from categories c where c.id = :category_id and c.household_id = :household_id"
        ),
        {"category_id": str(category_id), "household_id": str(household_id)},
    )
    row = result.first()
    return dict(row._mapping) if row is not None else None


async def resolve_category_id(
    session: AsyncSession,
    household_id: UUID,
    category_id: UUID | None,
    *,
    allow_none: bool = True,
    canonicalize_merged: bool = True,
) -> UUID | None:
    """Prueft eine eingehende Kategorie-ID und gibt die zu schreibende ID zurueck.

    Rueckgabe ist nicht zwingend die Eingabe: ein Merge-Tombstone wird auf sein
    aktives Ziel kanonisiert, damit ein verspaeteter Offline-Request nicht
    scheitert, obwohl die fachliche Absicht eindeutig ist.

    Fehlerfaelle (fremde UUIDs liefern bewusst 404, damit keine Existenz eines
    anderen Haushalts verraten wird):
      * unbekannt oder fremder Haushalt -> 404 category_not_found
      * archiviert ohne Merge-Ziel      -> 409 category_inactive
      * Gruppe archiviert               -> 409 category_inactive
    """
    if category_id is None:
        if allow_none:
            return None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=CATEGORY_REQUIRED
        )

    current_id = category_id
    for _ in range(_MAX_MERGE_HOPS):
        row = await load_category(session, household_id, current_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_NOT_FOUND
            )

        merged_into = row["merged_into_category_id"]
        if merged_into is not None and canonicalize_merged:
            current_id = UUID(str(merged_into))
            continue

        if row["is_archived"] or row["group_is_archived"]:
            # Nur archiviert, nicht gemergt: die fachliche Absicht ist hier
            # gerade nicht eindeutig - der Nutzer muss eine aktive Kategorie
            # waehlen.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_INACTIVE
            )
        return UUID(str(row["id"]))

    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_INACTIVE)


async def resolve_category_ids(
    session: AsyncSession,
    household_id: UUID,
    category_ids: list[UUID | None],
) -> list[UUID | None]:
    """Mehrfachaufloesung fuer Splitlisten. Bewusst sequenziell und ohne
    Sammelfehler: der erste ungueltige Split bricht den Request ab, damit nie
    eine teilweise gueltige Splitmenge geschrieben wird."""
    resolved: list[UUID | None] = []
    cache: dict[UUID, UUID | None] = {}
    for candidate in category_ids:
        if candidate is None:
            resolved.append(None)
            continue
        if candidate in cache:
            resolved.append(cache[candidate])
            continue
        value = await resolve_category_id(session, household_id, candidate)
        cache[candidate] = value
        resolved.append(value)
    return resolved


async def assert_group_active(
    session: AsyncSession, household_id: UUID, group_id: UUID
) -> dict[str, Any]:
    """Prueft eine Zielgruppe fuer Anlegen/Verschieben/Wiederherstellen."""
    result = await session.execute(
        text(
            f"select {_GROUP_SNAPSHOT_COLUMNS} from category_groups "
            "where id = :group_id and household_id = :household_id"
        ),
        {"group_id": str(group_id), "household_id": str(household_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_GROUP_NOT_FOUND
        )
    group = dict(row._mapping)
    if group["is_archived"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CATEGORY_GROUP_INACTIVE
        )
    return group


async def fetch_category_snapshot(
    session: AsyncSession, household_id: UUID, category_id: UUID
) -> dict[str, Any] | None:
    """Zustandsabbild fuer das Audit-Log (vorher/nachher)."""
    return await load_category(session, household_id, category_id)


async def fetch_group_snapshot(
    session: AsyncSession, household_id: UUID, group_id: UUID
) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            f"select {_GROUP_SNAPSHOT_COLUMNS} from category_groups "
            "where id = :group_id and household_id = :household_id"
        ),
        {"group_id": str(group_id), "household_id": str(household_id)},
    )
    row = result.first()
    return dict(row._mapping) if row is not None else None


# ---------------------------------------------------------------------------
# Referenzzaehlung
# ---------------------------------------------------------------------------

# (Tabelle, Spalte, Feldname in CategoryReferenceCounts). Eine einzige Liste,
# damit Archivvorschau, Merge-Vorschau und Verwaltungsansicht garantiert
# dieselben Referenzen zaehlen wie die Migration verknuepft hat.
CATEGORY_REFERENCE_TABLES: Final[tuple[tuple[str, str, str], ...]] = (
    ("transaction_splits", "category_id", "transaction_splits"),
    ("payees", "default_category_id", "payee_defaults"),
    ("classification_rules", "action_category_id", "classification_rules"),
    ("contracts", "category_id", "contracts"),
    ("budget_allocations", "category_id", "budget_allocations"),
    ("budget_rollovers", "category_id", "budget_rollovers"),
    ("savings_goals", "category_id", "savings_goals"),
    ("classification_decisions", "category_id", "classification_decisions"),
)

# Referenzen, die in die Zukunft wirken und bei einer Archivierung ersetzt oder
# geloest werden muessen. Alles andere ist Historie und bleibt unangetastet.
FORWARD_REFERENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "payee_defaults",
        "classification_rules",
        "contracts",
        "savings_goals",
    }
)


async def count_category_references(
    session: AsyncSession, household_id: UUID, category_id: UUID
) -> CategoryReferenceCounts:
    """Zaehlt alle Referenzen auf eine Kategorie in einer einzigen Abfrage."""
    parts = [
        f"(select count(*) from {table} where {column} = :category_id "
        f"and household_id = :household_id) as {field}"
        for table, column, field in CATEGORY_REFERENCE_TABLES
    ]
    result = await session.execute(
        text("select " + ", ".join(parts)),
        {"category_id": str(category_id), "household_id": str(household_id)},
    )
    row = result.first()
    if row is None:  # pragma: no cover - ein select ohne from liefert immer eine Zeile
        return CategoryReferenceCounts()
    return CategoryReferenceCounts(**{k: int(v) for k, v in row._mapping.items()})


def split_reference_counts(
    counts: CategoryReferenceCounts,
) -> tuple[CategoryReferenceCounts, CategoryReferenceCounts]:
    """Teilt Referenzen in historische und zukunftswirksame auf."""
    data = counts.model_dump(exclude={"total"})
    historical = CategoryReferenceCounts(
        **{k: v for k, v in data.items() if k not in FORWARD_REFERENCE_FIELDS}
    )
    forward = CategoryReferenceCounts(
        **{k: v for k, v in data.items() if k in FORWARD_REFERENCE_FIELDS}
    )
    return historical, forward


# ---------------------------------------------------------------------------
# Lesemodelle
# ---------------------------------------------------------------------------

_CATEGORY_TREE_SELECT: Final = (
    "select c.id, c.group_id, c.name, c.default_type, c.budget_bucket, c.is_archived, "
    "c.icon, c.color, c.is_budgeted, c.sort_order, c.synonyms, c.description, "
    "c.is_tax_deductible, "
    "c.semantic_key, c.version, c.origin, c.taxonomy_version, c.archived_at, "
    "c.merged_into_category_id, c.updated_at, "
    "(select t.name from categories t where t.id = c.merged_into_category_id) "
    "as merged_into_name "
    "from categories c where c.household_id = :household_id"
)

_GROUP_TREE_SELECT: Final = (
    "select id, name, sort_order, is_archived, icon, color, semantic_key, version, "
    "origin, taxonomy_version, updated_at "
    "from category_groups where household_id = :household_id"
)


def _category_from_row(row: dict[str, Any]) -> Category:
    return Category(
        id=row["id"],
        group_id=row["group_id"],
        name=row["name"],
        default_type=row["default_type"],
        budget_bucket=row["budget_bucket"],
        is_archived=row["is_archived"],
        icon=row["icon"],
        color=row["color"],
        is_budgeted=row["is_budgeted"],
        sort_order=row["sort_order"],
        synonyms=list(row["synonyms"] or []),
        description=row["description"],
        semantic_key=row["semantic_key"],
        version=row["version"],
        is_tax_deductible=bool(row.get("is_tax_deductible", False)),
    )


def _catalog_category_from_row(
    row: dict[str, Any], usage: CategoryReferenceCounts | None = None
) -> CatalogCategory:
    base = _category_from_row(row)
    return CatalogCategory(
        **base.model_dump(),
        origin=row["origin"],
        taxonomy_version=row["taxonomy_version"],
        archived_at=row["archived_at"],
        merged_into_category_id=row["merged_into_category_id"],
        merged_into_name=row.get("merged_into_name"),
        updated_at=row["updated_at"],
        usage=usage,
    )


async def load_active_tree(
    session: AsyncSession, household_id: UUID
) -> list[CategoryGroup]:
    """Der rueckwaertskompatible Picker-Baum: nur aktive Gruppen und
    Kategorien, sortiert."""
    groups_result = await session.execute(
        text(_GROUP_TREE_SELECT + " and not is_archived order by sort_order, name"),
        {"household_id": str(household_id)},
    )
    groups = [dict(row._mapping) for row in groups_result]

    categories_result = await session.execute(
        text(_CATEGORY_TREE_SELECT + " and not c.is_archived order by c.sort_order, c.name"),
        {"household_id": str(household_id)},
    )
    by_group: dict[UUID, list[Category]] = {}
    for row in categories_result:
        category = _category_from_row(dict(row._mapping))
        by_group.setdefault(category.group_id, []).append(category)

    return [
        CategoryGroup(
            id=group["id"],
            name=group["name"],
            sort_order=group["sort_order"],
            categories=by_group.get(group["id"], []),
            is_archived=group["is_archived"],
            icon=group["icon"],
            color=group["color"],
            semantic_key=group["semantic_key"],
            version=group["version"],
        )
        for group in groups
    ]


async def load_catalog_category(
    session: AsyncSession, household_id: UUID, category_id: UUID
) -> CatalogCategory:
    result = await session.execute(
        text(_CATEGORY_TREE_SELECT + " and c.id = :category_id"),
        {"household_id": str(household_id), "category_id": str(category_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_NOT_FOUND)
    return _catalog_category_from_row(dict(row._mapping))


async def load_catalog_group(
    session: AsyncSession, household_id: UUID, group_id: UUID
) -> CatalogGroup:
    result = await session.execute(
        text(_GROUP_TREE_SELECT + " and id = :group_id"),
        {"household_id": str(household_id), "group_id": str(group_id)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=CATEGORY_GROUP_NOT_FOUND
        )
    group = dict(row._mapping)
    counts = await session.execute(
        text(
            "select count(*) filter (where not is_archived) as active_count, "
            "count(*) filter (where is_archived) as archived_count "
            "from categories where household_id = :household_id and group_id = :group_id"
        ),
        {"household_id": str(household_id), "group_id": str(group_id)},
    )
    count_row = counts.first()
    return CatalogGroup(
        id=group["id"],
        name=group["name"],
        sort_order=group["sort_order"],
        is_archived=group["is_archived"],
        icon=group["icon"],
        color=group["color"],
        semantic_key=group["semantic_key"],
        origin=group["origin"],
        version=group["version"],
        updated_at=group["updated_at"],
        active_category_count=int(count_row.active_count) if count_row else 0,
        archived_category_count=int(count_row.archived_count) if count_row else 0,
    )


async def build_catalog(
    session: AsyncSession,
    household_id: UUID,
    *,
    include_archived: bool = True,
    include_usage: bool = False,
) -> CategoryCatalog:
    """Vollstaendige Verwaltungsansicht inklusive Baum-ETag.

    Leere Gruppen erscheinen hier bewusst (im Picker dagegen nicht)."""
    group_sql = _GROUP_TREE_SELECT
    category_sql = _CATEGORY_TREE_SELECT
    if not include_archived:
        group_sql += " and not is_archived"
        category_sql += " and not c.is_archived"

    groups_result = await session.execute(
        text(group_sql + " order by sort_order, name"), {"household_id": str(household_id)}
    )
    groups = [dict(row._mapping) for row in groups_result]

    categories_result = await session.execute(
        text(category_sql + " order by c.sort_order, c.name"),
        {"household_id": str(household_id)},
    )
    category_rows = [dict(row._mapping) for row in categories_result]

    usage_by_category: dict[UUID, CategoryReferenceCounts] = {}
    if include_usage:
        usage_by_category = await _load_usage_map(session, household_id)

    by_group: dict[UUID, list[CatalogCategory]] = {}
    active_counts: dict[UUID, int] = {}
    archived_counts: dict[UUID, int] = {}
    for row in category_rows:
        group_id = UUID(str(row["group_id"]))
        entry = _catalog_category_from_row(
            row, usage_by_category.get(UUID(str(row["id"]))) if include_usage else None
        )
        by_group.setdefault(group_id, []).append(entry)
        if entry.is_archived:
            archived_counts[group_id] = archived_counts.get(group_id, 0) + 1
        else:
            active_counts[group_id] = active_counts.get(group_id, 0) + 1

    taxonomy = await session.execute(
        text(
            "select taxonomy_version, revision from household_category_taxonomy "
            "where household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    taxonomy_row = taxonomy.first()

    available = await session.execute(
        text("select max(version) as version from category_taxonomy_versions where is_released")
    )
    available_row = available.first()

    return CategoryCatalog(
        taxonomy_revision=int(taxonomy_row.revision) if taxonomy_row else 0,
        installed_taxonomy_version=(
            int(taxonomy_row.taxonomy_version)
            if taxonomy_row and taxonomy_row.taxonomy_version is not None
            else None
        ),
        available_taxonomy_version=(
            int(available_row.version)
            if available_row and available_row.version is not None
            else None
        ),
        groups=[
            CatalogGroup(
                id=group["id"],
                name=group["name"],
                sort_order=group["sort_order"],
                is_archived=group["is_archived"],
                icon=group["icon"],
                color=group["color"],
                semantic_key=group["semantic_key"],
                origin=group["origin"],
                version=group["version"],
                updated_at=group["updated_at"],
                active_category_count=active_counts.get(UUID(str(group["id"])), 0),
                archived_category_count=archived_counts.get(UUID(str(group["id"])), 0),
                categories=by_group.get(UUID(str(group["id"])), []),
            )
            for group in groups
        ],
    )


async def _load_usage_map(
    session: AsyncSession, household_id: UUID
) -> dict[UUID, CategoryReferenceCounts]:
    """Nutzungszahlen fuer alle Kategorien des Haushalts in einer Abfrage -
    eine Abfrage je Kategorie waere bei 50+ Kategorien eine Zeilenlawine."""
    unions = " union all ".join(
        f"select {column} as category_id, '{field}' as ref_field, count(*) as ref_count "
        f"from {table} where household_id = :household_id and {column} is not null "
        f"group by {column}"
        for table, column, field in CATEGORY_REFERENCE_TABLES
    )
    result = await session.execute(text(unions), {"household_id": str(household_id)})

    raw: dict[UUID, dict[str, int]] = {}
    for row in result:
        category_id = UUID(str(row.category_id))
        raw.setdefault(category_id, {})[str(row.ref_field)] = int(row.ref_count)
    return {
        category_id: CategoryReferenceCounts(**fields) for category_id, fields in raw.items()
    }


async def get_taxonomy_revision(session: AsyncSession, household_id: UUID) -> int:
    """Baum-ETag des Haushalts. Fehlt die Zeile (Haushalt aus der Zeit vor
    Migration 0007, dessen Kategorien seither nicht angefasst wurden), gilt
    Revision 0 statt eines Fehlers."""
    result = await session.execute(
        text(
            "select revision from household_category_taxonomy where household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    row = result.first()
    return int(row.revision) if row is not None else 0


async def assert_taxonomy_revision(
    session: AsyncSession, household_id: UUID, expected_revision: int
) -> int:
    """Versionspruefung fuer baumweite Operationen (Reorder, Paket-Apply)."""
    current = await get_taxonomy_revision(session, household_id)
    if current != expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="category_tree_revision_conflict",
        )
    return current


async def bump_taxonomy_revision(session: AsyncSession, household_id: UUID) -> int:
    """Hebt die Baum-Revision fuer Aenderungen an, die keine Zeile in
    categories/category_groups schreiben (der Trigger deckt nur diese ab)."""
    result = await session.execute(
        text(
            "insert into household_category_taxonomy (household_id, revision) "
            "values (:household_id, 1) "
            "on conflict (household_id) do update "
            "set revision = household_category_taxonomy.revision + 1, updated_at = now() "
            "returning revision"
        ),
        {"household_id": str(household_id)},
    )
    row = result.first()
    return int(row.revision) if row is not None else 0
