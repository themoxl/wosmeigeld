from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

TransactionStatus = Literal["pending", "booked", "reconciled"]


class TransactionCreate(BaseModel):
    account_id: UUID
    booked_date: date
    # Minor Units (z. B. Cent), niemals Float - Architekturplan Abschnitt 2.
    # Positiv = Einnahme, negativ = Ausgabe.
    amount_minor: int
    currency: str | None = None
    category_id: UUID | None = None
    goal_id: UUID | None = None
    payee_label: str | None = Field(default=None, max_length=160)
    note: str | None = None


# Phase 9 (Konflikterkennung): Wird ein erwarteter Versionsstand mitgeschickt,
# fuehrt die Route einen Optimistic-Concurrency-Check aus. Der Wert stammt aus
# dem version-Feld der zuletzt gelesenen Buchung (oder aus deren ETag-Header).
# null bedeutet "ungeprueft schreiben" - so bleiben Bestandsclients, die noch
# keine Version kennen, unveraendert lauffaehig.
_EXPECTED_VERSION = Field(
    default=None,
    ge=1,
    description=(
        "Zuletzt vom Client gesehene Version der Buchung. Weicht sie vom "
        "Serverstand ab, antwortet die Route mit 409 statt zu ueberschreiben. "
        "Alternativ per If-Match-Header uebertragbar."
    ),
)


class TransactionUpdate(BaseModel):
    """Bearbeitet alles ausser Betrag/Splits (dafuer: PUT .../splits, haelt
    die Split-Summen-Invariante dadurch an genau einer Stelle)."""

    booked_date: date | None = None
    value_date: date | None = None
    account_id: UUID | None = None
    payee_label: str | None = Field(default=None, max_length=160)
    note: str | None = None
    status: Literal["pending", "booked"] | None = None
    expected_version: int | None = _EXPECTED_VERSION


class Transaction(BaseModel):
    id: UUID
    account_id: UUID
    booked_date: date
    value_date: date | None
    amount_minor: int
    currency: str
    payee_label: str | None
    note: str | None
    status: str
    is_transfer: bool
    # Bei genau einem Split gesetzt (Regelfall aus Phase 1); bei mehreren
    # Splits oder keiner Kategorie null - siehe splits in TransactionDetail.
    category_id: UUID | None
    is_split: bool
    # Soft-Delete-Kennzeichen: nur in Detail-/Konfliktantworten relevant, die
    # Liste blendet archivierte Buchungen ohnehin aus. Der Client erkennt so,
    # dass eine Buchung anderswo geloescht wurde, waehrend er sie bearbeitet
    # hat (Phase 9).
    is_archived: bool
    # Optimistic-Concurrency-Token, vom DB-Trigger transactions_bump_version
    # gepflegt (Migration 0004). Bei jedem Schreibvorgang mitzuschicken, um
    # verlorene Aenderungen zu verhindern.
    version: int


class TransactionSplit(BaseModel):
    id: UUID
    category_id: UUID | None
    goal_id: UUID | None
    amount_minor: int
    note: str | None


class TransactionDetail(Transaction):
    splits: list[TransactionSplit]


class SplitInput(BaseModel):
    category_id: UUID | None = None
    goal_id: UUID | None = None
    amount_minor: int
    note: str | None = None


class TransactionSplitsUpdate(BaseModel):
    amount_minor: int
    splits: list[SplitInput] = Field(min_length=1)
    expected_version: int | None = _EXPECTED_VERSION

    @model_validator(mode="after")
    def check_sum(self) -> "TransactionSplitsUpdate":
        total = sum(split.amount_minor for split in self.splits)
        if total != self.amount_minor:
            raise ValueError(
                f"Summe der Splits ({total}) entspricht nicht dem Betrag ({self.amount_minor})."
            )
        return self


class TransactionConflict(BaseModel):
    """Antwortkoerper bei 409 (Phase 9: Konflikterkennung). Der Client
    vergleicht seinen lokalen Stand mit 'current' und laesst die Nutzerin
    entscheiden - es wird bewusst nicht automatisch zusammengefuehrt.

    detail unterscheidet die beiden Konfliktarten:
      version_conflict     - die Buchung wurde anderswo geaendert.
      transaction_archived - die Buchung wurde anderswo geloescht
                             (Soft-Delete); ein Ueberschreiben wuerde sie
                             unbemerkt wiederbeleben.
    """

    detail: Literal["version_conflict", "transaction_archived"]
    # Version, die der Client erwartet hat.
    expected_version: int
    # Aktuelle Version auf dem Server - der Client wiederholt seinen Schreib-
    # vorgang damit, sobald die Nutzerin den Konflikt aufgeloest hat.
    current_version: int
    # Vollstaendiger Serverstand inklusive Splits, damit beide Staende
    # gegenuebergestellt werden koennen, ohne nachzuladen.
    current: TransactionDetail


class BulkCategorizeInput(BaseModel):
    transaction_ids: list[UUID] = Field(min_length=1, max_length=500)
    category_id: UUID | None = None


class BulkCategorizeResult(BaseModel):
    updated: int
    skipped: int


class UnassignedGoalSplitCandidate(BaseModel):
    """Split ohne Sparziel-Zuordnung - Kandidat fuer die rueckwirkende
    Zuordnungsliste (Architekturplan 6.7, Option (c) neben der direkten
    Zuordnung am Split)."""

    split_id: UUID
    transaction_id: UUID
    account_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None
    category_id: UUID | None
    note: str | None


class BulkAssignGoalInput(BaseModel):
    split_ids: list[UUID] = Field(min_length=1, max_length=500)
    goal_id: UUID | None = None


class BulkAssignGoalResult(BaseModel):
    updated: int
    skipped: int


class TransferLinkCreate(BaseModel):
    outgoing_transaction_id: UUID
    incoming_transaction_id: UUID


class TransferLink(BaseModel):
    id: UUID
    outgoing_transaction_id: UUID
    incoming_transaction_id: UUID
    match_status: str


# --- Mobile-Redesign: cursorbasiertes Paging (docs/MOBILE-APP-REDESIGN-PLAN.md,
# Abschnitt 5 "Daten/API") -----------------------------------------------------
# GET /v1/transactions bleibt fuer Bestandsclients unveraendert (limit=100,
# keine Cursor); GET /v1/transactions/page ist additiv und fuer die neue
# Buchungsliste der Mobile-App gedacht, die per useInfiniteQuery nachlaedt.

TransactionSort = Literal["date_desc", "date_asc", "amount_desc", "amount_asc", "payee_asc"]
TransactionDirection = Literal["income", "expense"]


class TransactionPage(BaseModel):
    """Antwort von GET /v1/transactions/page.

    items steht im selben Buchungsformat wie der bestehende Listen-Endpunkt
    (Transaction). next_cursor ist opak (Client reicht ihn unveraendert als
    naechsten `cursor`-Query-Parameter zurueck) und null, wenn keine weitere
    Seite existiert."""

    items: list[Transaction]
    next_cursor: str | None
