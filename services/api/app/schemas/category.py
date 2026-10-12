"""Vertrag der Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md).

Category/CategoryGroup bleiben der rueckwaertskompatible Picker-Baum von
GET /v1/categories - sie wurden nur additiv um Felder erweitert, die der
durchsuchbare Picker braucht (Synonyme, Symbol, Sortierung). Die
Verwaltungsansicht, die Lebenszyklus-Operationen und die Mehrfachzuordnung
haben eigene, davon getrennte Modelle.
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_validator

from app.schemas.ai import AIWarning

CategoryDefaultType = Literal["income", "expense", "transfer"]
CategoryBudgetBucket = Literal["needs", "wants", "savings"]
CategoryOrigin = Literal["standard", "user", "legacy"]

# Obergrenze aus dem Plan ("Maximal 500 Buchungen pro Vorgang"). Bewusst hier
# und nicht nur in der Route: Preview und Apply muessen dieselbe Grenze haben.
MAX_BULK_TRANSACTIONS = 500

_NAME_FIELD = Field(min_length=1, max_length=120)


def _clean_name(value: str) -> str:
    """Trimmen und Mehrfachleerzeichen normalisieren - dieselbe Vorstufe, die
    category_normalize_name() in der Datenbank fuer die Duplikatpruefung
    anwendet. Gespeichert wird der so bereinigte Anzeigename."""
    return " ".join(value.split())


class Category(BaseModel):
    """Eine aktive Kategorie im Picker-Baum."""

    id: UUID
    group_id: UUID
    name: str
    default_type: str | None
    budget_bucket: str | None
    is_archived: bool
    # Additiv ab der Kategorienverwaltung: der durchsuchbare Picker sucht ueber
    # Synonyme mit, zeigt Symbol/Farbe und respektiert die Sortierung.
    icon: str | None = None
    color: str | None = None
    is_budgeted: bool = True
    sort_order: int = 0
    synonyms: list[str] = Field(default_factory=list)
    description: str | None = None
    semantic_key: str | None = None
    version: int = 1
    # Steuerrelevanz ist eine getrennte Dimension, kein Budgetbereich und keine
    # eigene Kategorie (Migration 0010).
    is_tax_deductible: bool = False


class CategoryGroup(BaseModel):
    id: UUID
    name: str
    sort_order: int
    categories: list[Category]
    is_archived: bool = False
    icon: str | None = None
    color: str | None = None
    semantic_key: str | None = None
    version: int = 1


# ---------------------------------------------------------------------------
# Verwaltungsansicht
# ---------------------------------------------------------------------------


class CategoryReferenceCounts(BaseModel):
    """Wie oft eine Kategorie referenziert wird, aufgeschluesselt nach Tabelle.

    Der Plan unterscheidet historische Referenzen (bleiben erhalten) von
    zukunftswirksamen (muessen bei Archivierung ersetzt oder geloest werden) -
    deshalb wird derselbe Typ zweimal verwendet statt einer Gesamtzahl."""

    transaction_splits: int = 0
    payee_defaults: int = 0
    classification_rules: int = 0
    # Seit 0014 ein Feld statt der frueheren recurring_rules/subscriptions:
    # beide Toepfe sind in `contracts` aufgegangen.
    contracts: int = 0
    budget_allocations: int = 0
    budget_rollovers: int = 0
    savings_goals: int = 0
    classification_decisions: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return (
            self.transaction_splits
            + self.payee_defaults
            + self.classification_rules
            + self.contracts
            + self.budget_allocations
            + self.budget_rollovers
            + self.savings_goals
            + self.classification_decisions
        )


class CatalogCategory(Category):
    """Kategoriezeile der Verwaltungsansicht - inklusive archivierter."""

    origin: CategoryOrigin = "user"
    taxonomy_version: int | None = None
    archived_at: datetime | None = None
    merged_into_category_id: UUID | None = None
    merged_into_name: str | None = None
    updated_at: datetime | None = None
    usage: CategoryReferenceCounts | None = None


class CatalogGroup(BaseModel):
    id: UUID
    name: str
    sort_order: int
    is_archived: bool
    icon: str | None = None
    color: str | None = None
    semantic_key: str | None = None
    origin: CategoryOrigin = "user"
    version: int = 1
    updated_at: datetime | None = None
    active_category_count: int = 0
    archived_category_count: int = 0
    categories: list[CatalogCategory] = Field(default_factory=list)


class CategoryCatalog(BaseModel):
    """Vollstaendige Verwaltungsansicht mit Baum-ETag."""

    taxonomy_revision: int
    installed_taxonomy_version: int | None = None
    available_taxonomy_version: int | None = None
    groups: list[CatalogGroup]


# ---------------------------------------------------------------------------
# Schreibmodelle
# ---------------------------------------------------------------------------


class CategoryGroupCreate(BaseModel):
    name: Annotated[str, _NAME_FIELD]
    icon: str | None = None
    color: str | None = None
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return _clean_name(value)


class CategoryGroupUpdate(BaseModel):
    name: Annotated[str, _NAME_FIELD] | None = None
    icon: str | None = None
    color: str | None = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None


class CategoryCreate(BaseModel):
    group_id: UUID
    name: Annotated[str, _NAME_FIELD]
    default_type: CategoryDefaultType | None = "expense"
    budget_bucket: CategoryBudgetBucket | None = None
    is_budgeted: bool = True
    icon: str | None = None
    color: str | None = None
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    is_tax_deductible: bool = False

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return _clean_name(value)


class CategoryUpdate(BaseModel):
    """`group_id` fehlt bewusst: ein Gruppenwechsel wirkt rueckwirkend auf
    Berichte und laeuft deshalb ausschliesslich ueber move-preview/move."""

    name: Annotated[str, _NAME_FIELD] | None = None
    default_type: CategoryDefaultType | None = None
    budget_bucket: CategoryBudgetBucket | None = None
    is_budgeted: bool | None = None
    icon: str | None = None
    color: str | None = None
    description: str | None = None
    synonyms: list[str] | None = None
    is_tax_deductible: bool | None = None
    # Fuer budget_bucket/description/icon/color muss ein explizites Loeschen
    # moeglich sein - None allein bedeutet "nicht angefasst".
    clear_fields: list[
        Literal["budget_bucket", "icon", "color", "description", "default_type"]
    ] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None


class ReorderRequest(BaseModel):
    """Atomare, versionsgepruefte Sortierung. `taxonomy_revision` ist das
    Baum-ETag: eine zwischenzeitliche Strukturaenderung laesst den Request
    scheitern statt eine fremde Umsortierung zu ueberschreiben."""

    taxonomy_revision: int
    ordered_ids: list[UUID] = Field(min_length=1)
    group_id: UUID | None = None


# ---------------------------------------------------------------------------
# Operationen mit Vorschau
# ---------------------------------------------------------------------------


class OperationPreviewBase(BaseModel):
    operation_id: UUID
    expires_at: datetime


class MovePreview(OperationPreviewBase):
    category_id: UUID
    category_name: str
    source_group_id: UUID
    source_group_name: str
    target_group_id: UUID
    target_group_name: str
    affected_splits: int
    first_booked_date: date | None = None
    last_booked_date: date | None = None
    affected_budget_allocations: int
    hint: str


class MoveApply(BaseModel):
    operation_id: UUID
    target_group_id: UUID


class ArchivePreview(OperationPreviewBase):
    category_id: UUID
    category_name: str
    # Bleiben unveraendert erhalten (Historie).
    historical: CategoryReferenceCounts
    # Wirken in die Zukunft und muessen ersetzt oder geloest werden.
    forward: CategoryReferenceCounts
    requires_resolution: bool
    blocking_reason: str | None = None


class ArchiveApply(BaseModel):
    operation_id: UUID
    # Aktive Ersatzkategorie fuer zukunftswirksame Referenzen. Ohne Ersatz
    # werden Regeln deaktiviert und die uebrigen Referenzen auf NULL geloest.
    replacement_category_id: UUID | None = None


class GroupArchivePreview(OperationPreviewBase):
    group_id: UUID
    group_name: str
    active_category_ids: list[UUID]
    active_category_names: list[str]
    requires_resolution: bool


class GroupArchiveApply(BaseModel):
    operation_id: UUID
    # Kategorien der Gruppe, die in derselben Operation mit archiviert werden.
    archive_categories: bool = False
    replacement_category_id: UUID | None = None


class RestoreRequest(BaseModel):
    """Wiederherstellung. Ist die Gruppe archiviert, muss entweder sie
    mitwiederhergestellt oder eine aktive Zielgruppe gewaehlt werden."""

    target_group_id: UUID | None = None
    restore_group: bool = False
    new_name: str | None = None

    @field_validator("new_name")
    @classmethod
    def _normalize_new_name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None


class MergeCollision(BaseModel):
    kind: Literal[
        "budget_allocation",
        "rollover_rule",
        "duplicate_rule",
        "ai_proposal",
    ]
    detail: str
    count: int


class MergePreview(OperationPreviewBase):
    source_category_id: UUID
    source_category_name: str
    target_category_id: UUID
    target_category_name: str
    references: CategoryReferenceCounts
    transactions_to_bump: int
    collisions: list[MergeCollision]
    blocking_reason: str | None = None


class MergeRequest(BaseModel):
    source_category_id: UUID
    target_category_id: UUID


class MergeApply(BaseModel):
    operation_id: UUID
    request_id: str = Field(min_length=1, max_length=120)


class MergeResult(BaseModel):
    source_category_id: UUID
    target_category_id: UUID
    moved_references: CategoryReferenceCounts
    bumped_transactions: int
    merged_budget_allocations: int
    deactivated_rules: int
    idempotent_replay: bool = False


# ---------------------------------------------------------------------------
# Standardpaket
# ---------------------------------------------------------------------------


class StandardPackageStatus(BaseModel):
    installed_version: int | None
    available_version: int | None
    installed_at: datetime | None
    taxonomy_revision: int
    upgrade_available: bool


class StandardPackageEntry(BaseModel):
    semantic_key: str
    name: str
    group_semantic_key: str
    group_name: str
    default_type: str | None
    budget_bucket: str | None
    status: Literal["new", "already_present", "possible_match", "name_conflict"]
    existing_category_id: UUID | None = None
    existing_category_name: str | None = None


class StandardPackagePreview(OperationPreviewBase):
    taxonomy_version: int
    taxonomy_revision: int
    new_groups: list[str]
    entries: list[StandardPackageEntry]
    new_count: int
    already_present_count: int
    possible_match_count: int
    name_conflict_count: int


class StandardPackageApply(BaseModel):
    operation_id: UUID
    request_id: str | None = Field(default=None, max_length=120)
    # semantic_key -> vorhandene Kategorie, die der Nutzer ausdruecklich als
    # diese Standardkategorie bestaetigt. Ohne Bestaetigung wird nichts
    # umgedeutet.
    confirmed_matches: dict[str, UUID] = Field(default_factory=dict)


class StandardPackageResult(BaseModel):
    taxonomy_version: int
    created_groups: int
    created_categories: int
    linked_existing: int
    skipped_name_conflicts: int
    idempotent_replay: bool = False


# ---------------------------------------------------------------------------
# KI-Kategorievorschlaege
# ---------------------------------------------------------------------------
# Umsetzung von Planentscheidung 8 ("KI darf vorschlagen, nicht erfinden") und
# der offenen Frage aus docs/KI-FINANZASSISTENT-PLAN.md ("Soll die KI neue
# Kategorien anlegen? Vorschlagen: ja. Automatisch anlegen: nein.").
#
# Die Klassifikation eines Einzelbelegs bleibt bewusst auf bestehende IDs
# festgenagelt. Hier laeuft der andere Weg: aus mehreren aehnlichen Buchungen
# entsteht ein Cluster, und erst zu einem Cluster darf die KI eine neue
# Kategorie vorschlagen.

# Kleinste Clustergroesse. Ein einzelner Beleg rechtfertigt keine neue
# Kategorie - genau das verlangt Planentscheidung 8.
MIN_SUGGESTION_CLUSTER_SIZE = 2

# Obergrenze der Cluster pro Lauf. Jeder Cluster ist ein LLM-Aufruf gegen einen
# lokalen Server; ohne Deckel wuerde ein Erstimport mit hunderten Haendlern die
# Vorschau minutenlang blockieren.
MAX_SUGGESTION_CLUSTERS = 12

SuggestionKind = Literal["new_category", "existing_category"]


class SuggestionSample(BaseModel):
    """Eine Beispielbuchung des Clusters - Beleg fuer den Vorschlag."""

    transaction_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None = None
    note: str | None = None


class CategorySuggestion(BaseModel):
    """Ein Vorschlag zu genau einem Buchungscluster."""

    cluster_key: str
    payee_label: str
    transaction_count: int
    total_amount_minor: int
    first_booked_date: date
    last_booked_date: date
    samples: list[SuggestionSample] = Field(default_factory=list)

    kind: SuggestionKind
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str

    # kind == "existing_category"
    existing_category_id: UUID | None = None
    existing_category_name: str | None = None

    # kind == "new_category"
    proposed_name: str | None = None
    proposed_group_id: UUID | None = None
    proposed_group_name: str | None = None
    proposed_default_type: CategoryDefaultType | None = None
    proposed_budget_bucket: CategoryBudgetBucket | None = None
    proposed_icon: str | None = None
    proposed_synonyms: list[str] = Field(default_factory=list)
    proposed_is_tax_deductible: bool = False


class CategorySuggestionPreview(OperationPreviewBase):
    taxonomy_revision: int
    model_name: str | None = None
    # Wie viele Buchungen ueberhaupt ohne Kategorie sind, unabhaengig davon, ob
    # sie es in einen Cluster geschafft haben. Ohne diese Zahl wirkt "3
    # Vorschlaege" bei 500 offenen Buchungen wie ein Fehler.
    uncategorized_transactions: int
    clustered_transactions: int
    skipped_singletons: int
    # Cluster, zu denen das Modell nichts Verwertbares geliefert hat (Antwort
    # verworfen, Lauf vorzeitig beendet oder abgebrochen). Ohne diese Zahl
    # saehe ein halber Lauf wie ein vollstaendiger aus; der Grund steht in
    # `warnings` (Reason-Code des KI-Kerns samt Klartext).
    unassessed_clusters: int = 0
    warnings: list[AIWarning] = Field(default_factory=list)
    suggestions: list[CategorySuggestion] = Field(default_factory=list)


class CategorySuggestionAccept(BaseModel):
    """Genau ein angenommener Vorschlag. Name, Gruppe und Einordnung sind
    ueberschreibbar - der Vorschlag ist ein Entwurf, kein Diktat."""

    cluster_key: str
    name: Annotated[str, _NAME_FIELD] | None = None
    group_id: UUID | None = None
    budget_bucket: CategoryBudgetBucket | None = None
    is_tax_deductible: bool | None = None
    # Vorhandene Kategorie statt einer neuen: setzt den Vorschlagstyp
    # ausdruecklich auf "zuordnen".
    use_existing_category_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None


class CategorySuggestionApply(BaseModel):
    operation_id: UUID
    request_id: str | None = Field(default=None, max_length=120)
    accepted: list[CategorySuggestionAccept] = Field(default_factory=list)
    # Die geclusterten Buchungen gleich mit zuordnen. Ohne dieses Flag entsteht
    # nur die Kategorie, und der Nutzer haette wieder Handarbeit vor sich.
    assign_transactions: bool = True


class CategorySuggestionResultItem(BaseModel):
    cluster_key: str
    category_id: UUID
    category_name: str
    created: bool
    assigned_transactions: int
    skipped_transactions: int


class CategorySuggestionResult(BaseModel):
    created_categories: int
    linked_existing: int
    assigned_transactions: int
    skipped_transactions: int
    items: list[CategorySuggestionResultItem] = Field(default_factory=list)
    idempotent_replay: bool = False


# ---------------------------------------------------------------------------
# Zuordnung an Buchungen
# ---------------------------------------------------------------------------


class TransactionCategoryUpdate(BaseModel):
    """Kategorie genau eines Splits aendern. Buchungen mit mehreren Splits
    werden bewusst abgelehnt, damit eine Gesamtkategorie keine vorhandene
    Aufteilung still ueberschreibt."""

    category_id: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)


BulkItemStatus = Literal["applied", "unchanged", "skipped", "conflict"]

BulkSkipReason = Literal[
    "split_transaction",
    "already_assigned",
    "version_conflict",
    "not_found",
    "archived_transaction",
    "category_inactive",
]


class BulkCategoryItem(BaseModel):
    transaction_id: UUID
    status: BulkItemStatus
    reason: BulkSkipReason | None = None
    current_version: int | None = None


class BulkCategoryInput(BaseModel):
    transaction_ids: Annotated[list[UUID], Field(min_length=1, max_length=MAX_BULK_TRANSACTIONS)]
    category_id: UUID | None = None
    # Zuletzt gesehene Version je Buchung. Fehlt ein Eintrag, wird die Buchung
    # ohne Versionspruefung geschrieben (z. B. direkt nach einem frischen Read).
    expected_versions: dict[UUID, int] = Field(default_factory=dict)

    @field_validator("transaction_ids")
    @classmethod
    def _unique_ids(cls, value: list[UUID]) -> list[UUID]:
        seen: set[UUID] = set()
        unique: list[UUID] = []
        for item in value:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique


class BulkCategoryResult(BaseModel):
    applied: int
    unchanged: int
    skipped: int
    conflict: int
    items: list[BulkCategoryItem]
