from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ClassificationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    priority: int = 0
    is_active: bool = True
    match_payee_raw: str | None = None
    match_payee_exact_word: bool = False
    match_purpose: str | None = None
    match_purpose_exact_word: bool = False
    exclude_payee_raw: str | None = None
    exclude_purpose: str | None = None
    match_min_amount_minor: int | None = None
    match_max_amount_minor: int | None = None
    match_amount_mode: Literal["inside", "outside"] = "inside"
    match_account_id: UUID | None = None
    action_payee_id: UUID | None = None
    action_category_id: UUID | None = None
    action_set_transfer: bool = False
    action_note: str | None = None
    # Erstattungsfaehigkeit (docs/ERSTATTUNGSFAEHIGKEIT-PLAN.md, Abschnitt 6).
    # None heisst "diese Regel sagt nichts dazu" - nur Regeln mit gesetztem
    # action_reimbursable nehmen am zweiten Auswertungsdurchlauf teil.
    action_reimbursable: Literal["expected", "excluded"] | None = None
    action_expectation_mode: Literal["full", "quota", "fixed", "unknown"] | None = None
    action_expectation_rate: Decimal | None = Field(default=None, gt=0, le=1)
    action_expectation_minor: int | None = Field(default=None, ge=0)
    source: Literal["user", "ai_confirmed", "imported"] = "user"


class ClassificationRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    priority: int | None = None
    is_active: bool | None = None
    match_payee_raw: str | None = None
    match_payee_exact_word: bool | None = None
    match_purpose: str | None = None
    match_purpose_exact_word: bool | None = None
    exclude_payee_raw: str | None = None
    exclude_purpose: str | None = None
    match_min_amount_minor: int | None = None
    match_max_amount_minor: int | None = None
    match_amount_mode: Literal["inside", "outside"] | None = None
    match_account_id: UUID | None = None
    action_payee_id: UUID | None = None
    action_category_id: UUID | None = None
    action_set_transfer: bool | None = None
    action_note: str | None = None
    action_reimbursable: Literal["expected", "excluded"] | None = None
    action_expectation_mode: Literal["full", "quota", "fixed", "unknown"] | None = None
    action_expectation_rate: Decimal | None = Field(default=None, gt=0, le=1)
    action_expectation_minor: int | None = Field(default=None, ge=0)


class ClassificationRule(BaseModel):
    id: UUID
    household_id: UUID
    name: str
    priority: int
    is_active: bool
    match_payee_raw: str | None
    match_payee_exact_word: bool = False
    match_purpose: str | None
    match_purpose_exact_word: bool = False
    exclude_payee_raw: str | None = None
    exclude_purpose: str | None = None
    match_min_amount_minor: int | None
    match_max_amount_minor: int | None
    match_amount_mode: Literal["inside", "outside"] = "inside"
    match_account_id: UUID | None
    action_payee_id: UUID | None
    action_category_id: UUID | None
    action_set_transfer: bool
    action_note: str | None
    action_reimbursable: Literal["expected", "excluded"] | None = None
    action_expectation_mode: Literal["full", "quota", "fixed", "unknown"] | None = None
    action_expectation_rate: Decimal | None = None
    action_expectation_minor: int | None = None
    source: str
    # Wird vom Trigger classification_rules_version_trg bei jedem Update
    # hochgezaehlt (siehe supabase/migrations/0001_schema.sql).
    version: int


# --- Rueckwirkende Regelanwendung -------------------------------------------
# Klassifikationsregeln greifen sonst nur beim Import (siehe
# import_service.commit_single_row). Diese Modelle beschreiben den Lauf ueber
# bereits gebuchte Transaktionen: erst Vorschau, dann Anwendung.

RuleApplyScope = Literal["uncategorized", "all"]
RuleApplyStatus = Literal["applied", "unchanged", "skipped", "conflict"]


class RuleApplyInput(BaseModel):
    """Umfang eines rueckwirkenden Regellaufs.

    `scope` ist bewusst auf "uncategorized" vorbelegt: eine neue Regel soll im
    Regelfall Luecken fuellen und nicht stillschweigend Handarbeit
    ueberschreiben. Wer bewusst umkategorisieren will, waehlt "all"."""

    scope: RuleApplyScope = "uncategorized"
    from_date: date | None = None
    to_date: date | None = None
    # Obergrenze je Lauf. Was darueber hinaus zutrifft, wird nicht still
    # verschluckt, sondern in RuleApplyResult.truncated gemeldet.
    limit: int = Field(default=500, ge=1, le=5000)


class RuleApplyItem(BaseModel):
    """Eine Buchung im Ergebnis eines Regellaufs. Vorschau und Anwendung
    liefern dieselbe Struktur, damit die Oberflaeche exakt anzeigen kann, was
    passieren wird bzw. passiert ist."""

    transaction_id: UUID
    booked_date: date
    amount_minor: int
    payee: str | None
    purpose: str | None
    status: RuleApplyStatus
    reason: str | None = None
    previous_category_id: UUID | None = None
    # Nur beim Sammellauf ueber alle Regeln gefuellt: bei einem Lauf mit genau
    # einer Regel steht sie bereits im Kopf des Ergebnisses.
    rule_id: UUID | None = None
    rule_name: str | None = None


class RuleApplyResult(BaseModel):
    rule_id: UUID
    rule_name: str
    scope: RuleApplyScope
    # true = Vorschau, es wurde nichts geschrieben.
    dry_run: bool
    matched: int
    applied: int
    unchanged: int
    skipped: int
    conflict: int
    # Es haetten mehr Buchungen zugetroffen, als `limit` zulaesst.
    truncated: bool
    items: list[RuleApplyItem]


# --- Sammellauf ueber alle Regeln --------------------------------------------
# Eine Regel nach der anderen von Hand anzuwenden ist nach einem Paketimport
# unzumutbar. Der Sammellauf entscheidet je Buchung wie der Import: die erste
# passende Regel in Prioritaetsreihenfolge gewinnt, danach ist die Buchung
# vergeben.

_RULE_EXCLUDED_REASONS = """Warum eine Regel nicht mitgelaufen ist:
`inactive` (nicht aktiv, siehe `include_inactive`), `no_effect` (setzt weder
Kategorie noch Transferkennzeichen), `category_inactive` (Zielkategorie
archiviert oder unbekannt)."""


class RuleApplyAllInput(RuleApplyInput):
    """Umfang eines Sammellaufs.

    `include_inactive` ist bewusst aus: `is_active = false` heisst "wird bei
    neuen Buchungen nicht ausgewertet", und ein Sammellauf ueber den Bestand
    ist genau dieselbe automatische Auswertung - nur nachtraeglich. Wer eine
    einzelne inaktive Regel trotzdem laufen lassen will, nimmt weiterhin
    `POST /v1/rules/{id}/apply`."""

    include_inactive: bool = False


class RuleApplyRuleSummary(BaseModel):
    """Was eine einzelne Regel im Sammellauf bewirkt hat.

    Die Zaehler haben bewusst keinen Standardwert: die Antwort traegt sie
    immer, und ein Default wuerde sie im erzeugten Client optional machen."""

    rule_id: UUID
    rule_name: str
    priority: int
    matched: int
    applied: int
    unchanged: int
    skipped: int
    conflict: int
    # Gesetzt, wenn die Regel gar nicht erst ausgewertet wurde. Siehe
    # _RULE_EXCLUDED_REASONS.
    excluded_reason: str | None = Field(default=None, description=_RULE_EXCLUDED_REASONS)


class RuleApplyAllResult(BaseModel):
    """Ergebnis eines Sammellaufs. `items` ist wie beim Einzellauf durch
    `limit` gedeckelt, die Zaehler oben zaehlen dagegen vollstaendig."""

    scope: RuleApplyScope
    dry_run: bool
    rules_total: int
    rules_evaluated: int
    transactions_scanned: int
    matched: int
    applied: int
    unchanged: int
    skipped: int
    conflict: int
    truncated: bool
    rules: list[RuleApplyRuleSummary]
    items: list[RuleApplyItem]


class RecurringRuleCreate(BaseModel):
    account_id: UUID | None = None
    payee_id: UUID | None = None
    category_id: UUID | None = None
    expected_amount_minor: int
    frequency: Literal["weekly", "monthly", "quarterly", "yearly"] = "monthly"
    next_due_date: date
    tolerance_days: int = 3
    tolerance_amount_minor: int = 0
    is_active: bool = True
    note: str | None = None


class RecurringRuleUpdate(BaseModel):
    account_id: UUID | None = None
    payee_id: UUID | None = None
    category_id: UUID | None = None
    expected_amount_minor: int | None = None
    frequency: Literal["weekly", "monthly", "quarterly", "yearly"] | None = None
    next_due_date: date | None = None
    tolerance_days: int | None = None
    tolerance_amount_minor: int | None = None
    is_active: bool | None = None
    note: str | None = None


class RecurringRule(BaseModel):
    id: UUID
    household_id: UUID
    account_id: UUID | None
    payee_id: UUID | None
    category_id: UUID | None
    expected_amount_minor: int
    frequency: str
    next_due_date: date
    tolerance_days: int
    tolerance_amount_minor: int
    is_active: bool
    note: str | None


class SubscriptionCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=160)
    plan_name: str | None = None
    monthly_cost_minor: int
    annual_cost_minor: int
    cancellation_period_days: int | None = None
    next_renewal_date: date | None = None
    category_id: UUID | None = None
    account_id: UUID | None = None
    is_active: bool = True
    note: str | None = None


class SubscriptionUpdate(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=160)
    plan_name: str | None = None
    monthly_cost_minor: int | None = None
    annual_cost_minor: int | None = None
    cancellation_period_days: int | None = None
    next_renewal_date: date | None = None
    category_id: UUID | None = None
    account_id: UUID | None = None
    is_active: bool | None = None
    note: str | None = None


class Subscription(BaseModel):
    id: UUID
    household_id: UUID
    provider: str
    plan_name: str | None
    monthly_cost_minor: int
    annual_cost_minor: int
    cancellation_period_days: int | None
    next_renewal_date: date | None
    category_id: UUID | None
    account_id: UUID | None
    is_active: bool
    note: str | None


# --- Regel-Import/Export -------------------------------------------------------

class RulePackageMeta(BaseModel):
    """Metadaten eines eingebauten oder importierten Regelpakets."""
    id: str
    name: str
    description: str
    icon: str
    rule_count: int
    # Wie viele Regeln des Pakets es im Haushalt schon gibt (Vergleich ueber
    # das normalisierte Haendlermuster). Damit sieht man vor dem Klick, dass
    # ein Paket bereits installiert ist, statt es zum zweiten Mal zu oeffnen.
    installed_count: int = 0


class PortableRule(BaseModel):
    """Eine Regel im portablen Austauschformat (Kategorie als semantic_key, nicht UUID)."""
    name: str = Field(min_length=1, max_length=160)
    match_payee_raw: str | None = None
    match_payee_exact_word: bool = False
    match_purpose: str | None = None
    match_purpose_exact_word: bool = False
    exclude_payee_raw: str | None = None
    exclude_purpose: str | None = None
    match_min_amount_minor: int | None = None
    match_max_amount_minor: int | None = None
    match_amount_mode: Literal["inside", "outside"] = "inside"
    action_category_key: str | None = None
    action_set_transfer: bool = False
    action_note: str | None = None
    priority: int = 10
    is_active: bool = True


class RulePackage(BaseModel):
    """Ein portables Regelpaket fuer Import/Export."""
    format_version: str = "1.0"
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    author: str = ""
    rules: list[PortableRule] = Field(min_length=1)


# Ergebnis der Duplikatpruefung fuer genau eine einzuspielende Regel. Die
# Abstufung entscheidet, was die Oberflaeche vorbelegt - siehe
# app/services/rule_import_export_service.py fuer die Herleitung.
RuleImportItemStatus = Literal["new", "duplicate", "conflict", "overlap"]


class RuleImportPreviewItem(BaseModel):
    """Vorschau fuer eine einzelne Regel im Import."""
    # Position in `RulePackage.rules`. Ueber sie waehlt die Oberflaeche
    # einzelne Regeln ab (`RuleImportInput.skip_indices`); der Regelname taugt
    # dafuer nicht, er ist innerhalb eines Pakets nicht eindeutig.
    index: int = 0
    rule_name: str
    match_payee_raw: str | None = None
    category_key: str | None
    resolved_category_id: UUID | None
    resolved_category_name: str | None
    status: RuleImportItemStatus = "new"
    is_duplicate: bool
    duplicate_rule_id: UUID | None = None
    # Die bereits vorhandene Regel, an der sich `status` entschieden hat.
    existing_rule_name: str | None = None
    existing_category_name: str | None = None
    # Bei `overlap` die gemeinsamen Suchbegriffe ("amazon"), damit die
    # Oberflaeche die Ueberschneidung benennen kann statt sie nur zu behaupten.
    shared_terms: list[str] = []
    # true = das Duplikat steckt im Paket selbst, nicht im Bestand.
    in_package_duplicate: bool = False
    # Vorbelegung der Auswahl: nur unbekannte Regeln sind vorausgewaehlt.
    recommended: bool = True


class RuleImportPreview(BaseModel):
    """Gesamtvorschau eines Regelimports."""
    package_name: str
    total_rules: int
    mappable: int
    unmapped: int
    duplicates: int
    conflicts: int = 0
    overlaps: int = 0
    # Zahl der vorbelegten Regeln (`recommended`).
    recommended: int = 0
    items: list[RuleImportPreviewItem]


class RuleImportInput(BaseModel):
    """Eingabe fuer den tatsaechlichen Regelimport."""
    package: RulePackage
    category_overrides: dict[str, str] = {}  # action_category_key -> category_id (as string UUID)
    skip_duplicates: bool = True
    # Positionen aus `RuleImportPreviewItem.index`, die NICHT importiert werden
    # sollen. Leer = alles ausser den ohnehin uebersprungenen Duplikaten.
    skip_indices: list[int] = []


class RuleImportResult(BaseModel):
    """Ergebnis eines Regelimports."""
    created: int
    # Summe der beiden folgenden Zaehler.
    skipped: int
    # Als Duplikat erkannt - von der Vorschau oder vom Unique-Index der
    # Datenbank.
    skipped_duplicates: int = 0
    # In der Oberflaeche abgewaehlt.
    skipped_deselected: int = 0
    errors: list[str]


class RuleExportInput(BaseModel):
    """Eingabe fuer den Regelexport."""
    rule_ids: list[UUID] = []  # leer = alle exportieren
    package_name: str = "Meine Regeln"
    description: str = ""
