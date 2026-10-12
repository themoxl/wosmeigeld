"""Antwortmodelle fuer GET /v1/transactions/{id}/provenance.

Die Herkunft wird als Datum ausgeliefert, nicht als fertiger Satz: die
Oberflaeche baut daraus den Klartext ("gesetzt durch Regel X, weil der
Verwendungszweck Y enthaelt"). Ein serverseitig formulierter Satz waere
weder uebersetzbar noch in Web und Mobile unterschiedlich darstellbar.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

CategorySource = Literal["unset", "unknown", "manual", "import", "rule", "ai", "history"]


class RuleCriterion(BaseModel):
    """Ein Kriterium, das an der treffenden Regel gesetzt war.

    `field` benennt das gepruefte Feld (payee_raw, purpose, amount_min,
    amount_max, account), `pattern` den geforderten Wert."""

    field: str
    pattern: str


class RuleMatchEvidence(BaseModel):
    """Der zum Trefferzeitpunkt eingefrorene Zustand der Regel.

    Bewusst ein eigenes Modell statt eines freien Objekts: nur so entsteht im
    generierten Typvertrag (packages/contracts) etwas, mit dem die Clients
    ohne Rateparsing arbeiten koennen."""

    rule_name: str | None = None
    criteria: list[RuleCriterion] = []


class ImportOrigin(BaseModel):
    """Woher die Buchung kam, wenn sie aus einem Import stammt."""

    import_run_id: UUID
    filename: str | None
    detected_format: str | None
    row_number: int | None
    imported_at: datetime | None


class SplitProvenance(BaseModel):
    """Herkunft der Kategorie genau eines Splits."""

    split_id: UUID
    amount_minor: int

    category_id: UUID | None
    category_name: str | None
    category_group_name: str | None
    # Archivierte Kategorien werden hier absichtlich aufgeloest und markiert,
    # statt als leer zu erscheinen.
    category_is_archived: bool | None

    category_source: CategorySource

    rule_id: UUID | None
    rule_name: str | None
    rule_priority: int | None
    rule_is_active: bool | None
    # Was an der Regel gesetzt war, als sie getroffen hat - eingefroren, weil
    # die Regel inzwischen geaendert worden sein kann.
    matched_on: RuleMatchEvidence | None

    decision_id: UUID | None
    model_name: str | None
    reason_code: str | None
    confidence: float | None

    categorized_at: datetime | None
    categorized_by: UUID | None


class TransactionProvenance(BaseModel):
    """Vollstaendige Herkunftsauskunft zu einer Buchung."""

    transaction_id: UUID
    source: str

    payee_label: str | None
    payee_raw: str | None
    purpose_raw: str | None
    note: str | None
    bank_transaction_code: str | None
    external_reference: str | None
    fingerprint: str | None

    import_origin: ImportOrigin | None
    splits: list[SplitProvenance]
