"""API-Vertraege fuer den PKV-/Beihilfe-Erstattungstracker."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationInfo, model_validator

PaymentStatus = Literal["unknown", "unpaid", "partial", "paid", "not_required"]
SubmissionStatus = Literal[
    "not_submitted",
    "held_for_bonus",
    "ready",
    "submitted",
    "documents_requested",
    "not_planned",
]
DecisionStatus = Literal["none", "processing", "received", "partial", "rejected", "appeal"]
ReimbursementStatus = Literal["not_expected", "expected", "partial", "received", "mismatch"]
OverallStatus = Literal["draft", "action_required", "waiting", "reconciled"]
DocumentType = Literal[
    "unknown",
    "invoice",
    "prescription",
    "submission_receipt",
    "benefit_statement",
    "request_for_documents",
    "other",
]
DocumentProcessingStatus = Literal[
    "queued", "extracting", "needs_ocr", "needs_review", "confirmed", "failed", "duplicate"
]


class InsuredPersonCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    birth_date: date | None = None
    linked_user_id: UUID | None = None


class InsuredPerson(BaseModel):
    id: UUID
    display_name: str
    birth_date: date | None
    linked_user_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ReimbursementPayerCreate(BaseModel):
    payer_type: Literal["pkv", "beihilfe", "supplementary", "other"]
    name: str = Field(min_length=1, max_length=160)


class ReimbursementPayer(BaseModel):
    id: UUID
    payer_type: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ReimbursementCaseCreate(BaseModel):
    insured_person_id: UUID
    provider_name: str = Field(min_length=1, max_length=240)
    invoice_number: str | None = Field(default=None, max_length=160)
    invoice_date: date
    treatment_from: date | None = None
    treatment_to: date | None = None
    due_date: date | None = None
    billed_minor: int = Field(ge=0)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    expected_minor: int = Field(default=0, ge=0)
    reimbursed_minor: int = Field(default=0, ge=0)
    deductible_minor: int = Field(default=0, ge=0)
    noncovered_minor: int = Field(default=0, ge=0)
    payment_status: PaymentStatus = "unknown"
    submission_status: SubmissionStatus = "not_submitted"
    decision_status: DecisionStatus = "none"
    reimbursement_status: ReimbursementStatus = "not_expected"
    next_action: str | None = Field(default=None, max_length=500)
    next_action_due: date | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_amounts_and_dates(self) -> "ReimbursementCaseCreate":
        if self.treatment_from and self.treatment_to and self.treatment_to < self.treatment_from:
            raise ValueError("treatment_to darf nicht vor treatment_from liegen")
        if self.reimbursed_minor > self.billed_minor:
            raise ValueError("reimbursed_minor darf billed_minor nicht uebersteigen")
        if self.deductible_minor + self.noncovered_minor > self.billed_minor:
            raise ValueError("Eigenanteile duerfen billed_minor nicht uebersteigen")
        self.currency = self.currency.upper()
        return self


class ReimbursementCaseUpdate(BaseModel):
    provider_name: str | None = Field(default=None, min_length=1, max_length=240)
    invoice_number: str | None = Field(default=None, max_length=160)
    invoice_date: date | None = None
    treatment_from: date | None = None
    treatment_to: date | None = None
    due_date: date | None = None
    billed_minor: int | None = Field(default=None, ge=0)
    expected_minor: int | None = Field(default=None, ge=0)
    reimbursed_minor: int | None = Field(default=None, ge=0)
    deductible_minor: int | None = Field(default=None, ge=0)
    noncovered_minor: int | None = Field(default=None, ge=0)
    payment_status: PaymentStatus | None = None
    submission_status: SubmissionStatus | None = None
    decision_status: DecisionStatus | None = None
    reimbursement_status: ReimbursementStatus | None = None
    next_action: str | None = Field(default=None, max_length=500)
    next_action_due: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    clear_fields: list[
        Literal[
            "invoice_number",
            "treatment_from",
            "treatment_to",
            "due_date",
            "next_action",
            "next_action_due",
            "notes",
        ]
    ] = Field(default_factory=list)


class ReimbursementCase(BaseModel):
    id: UUID
    person_id: UUID
    person_name: str
    provider_name: str
    invoice_number: str | None
    invoice_date: date
    treatment_from: date | None
    treatment_to: date | None
    due_date: date | None
    billed_minor: int
    expected_minor: int
    reimbursed_minor: int
    deductible_minor: int
    noncovered_minor: int
    outstanding_minor: int
    currency: str
    overall_status: OverallStatus
    payment_status: PaymentStatus
    submission_status: SubmissionStatus
    decision_status: DecisionStatus
    reimbursement_status: ReimbursementStatus
    next_action: str | None
    next_action_due: date | None
    notes: str | None
    document_count: int
    version: int
    created_at: datetime
    updated_at: datetime
    unverified_reimbursed_minor: int = 0


class ReimbursementSummary(BaseModel):
    year: int
    currency: str = "EUR"
    actionable_count: int
    processing_count: int
    expected_minor: int
    received_minor: int
    final_own_share_minor: int
    unresolved_minor: int


class DocumentLinkedItem(BaseModel):
    item_index: int | None = None
    case_id: UUID


class ReimbursementDocument(BaseModel):
    id: UUID
    insured_person_id: UUID
    case_id: UUID | None
    document_type: DocumentType
    original_filename: str
    sha256: str
    mime_type: str
    size_bytes: int
    page_count: int | None
    processing_status: DocumentProcessingStatus
    extraction_confidence: float | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    linked_case_ids: list[UUID] = Field(default_factory=list)
    linked_items: list[DocumentLinkedItem] = Field(default_factory=list)


class ReimbursementDocumentUpload(BaseModel):
    document: ReimbursementDocument
    duplicate: bool = False
    task_id: UUID | None = None


class ReimbursementBulkReprocess(BaseModel):
    """Ergebnis einer Sammel-Neuauswertung: wie viele wurden eingereiht."""

    queued: int
    task_id: UUID | None = None


class DocumentExtraction(BaseModel):
    id: UUID
    document_id: UUID
    extractor_version: str
    schema_version: str
    model_name: str | None
    status: Literal["success", "needs_review", "failed", "needs_ocr"]
    confidence: float | None
    structured_data: dict[str, Any]
    field_evidence: dict[str, Any]
    validation_warnings: list[Any]
    confirmed_data: dict[str, Any] | None
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    created_at: datetime
    previous_confirmed_data: dict[str, Any] | None = None


class ExtractedStatementItem(BaseModel):
    """Eine Rechnung im Bescheid; bewilligt ist noch kein Bankeingang."""

    person_name: str | None = Field(default=None, max_length=120)
    provider_name: str | None = Field(default=None, max_length=240)
    invoice_number: str | None = Field(default=None, max_length=160)
    invoice_date: date | None = None
    billed_minor: int | None = Field(default=None, ge=0)
    approved_minor: int | None = Field(default=None, ge=0)
    deductible_minor: int | None = Field(default=None, ge=0)
    noncovered_minor: int | None = Field(default=None, ge=0)
    page_number: int | None = Field(default=None, ge=1)
    source_index: int | None = Field(default=None, ge=0, le=199)

    @model_validator(mode="after")
    def validate_amounts(self) -> "ExtractedStatementItem":
        if self.billed_minor is not None:
            if self.approved_minor is not None and self.approved_minor > self.billed_minor:
                raise ValueError("approved_minor darf billed_minor nicht uebersteigen")
            if (self.deductible_minor or 0) + (self.noncovered_minor or 0) > self.billed_minor:
                raise ValueError("Eigenanteile duerfen billed_minor nicht uebersteigen")
        return self


class ExtractedDocumentData(BaseModel):
    document_type: DocumentType = "unknown"
    person_name: str | None = Field(default=None, max_length=120)
    provider_name: str | None = Field(default=None, max_length=240)
    invoice_number: str | None = Field(default=None, max_length=160)
    invoice_date: date | None = None
    due_date: date | None = None
    billed_minor: int | None = Field(default=None, ge=0)
    expected_minor: int | None = Field(default=None, ge=0)
    reimbursed_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    external_reference: str | None = Field(default=None, max_length=160)
    statement_number: str | None = Field(default=None, max_length=160)
    statement_date: date | None = None
    submission_date: date | None = None
    payer_name: str | None = Field(default=None, max_length=160)
    payer_type: Literal["pkv", "beihilfe", "supplementary", "other"] | None = None
    approved_minor: int | None = Field(default=None, ge=0)
    deductible_minor: int | None = Field(default=None, ge=0)
    noncovered_minor: int | None = Field(default=None, ge=0)
    items: list[ExtractedStatementItem] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_document_data(self, info: ValidationInfo) -> "ExtractedDocumentData":
        self.currency = self.currency.upper()
        source_indices = [item.source_index for item in self.items if item.source_index is not None]
        if not (info.context or {}).get("extraction") \
                and len(source_indices) != len(set(source_indices)):
            raise ValueError("Rechnungspositionen duerfen keine doppelte Herkunft haben")
        if self.reimbursed_minor is not None and self.billed_minor is not None:
            if self.reimbursed_minor > self.billed_minor:
                raise ValueError("reimbursed_minor darf billed_minor nicht uebersteigen")
        if self.document_type == "benefit_statement" and self.approved_minor is not None \
                and self.billed_minor is not None and self.approved_minor > self.billed_minor:
            raise ValueError("approved_minor darf billed_minor nicht uebersteigen")
        if self.items and self.approved_minor is not None \
                and all(item.approved_minor is not None for item in self.items) \
                and not (info.context or {}).get("extraction"):
            if sum(item.approved_minor or 0 for item in self.items) != self.approved_minor:
                raise ValueError("Einzelbetraege stimmen nicht mit der Gesamtauszahlung ueberein")
        if self.items and self.billed_minor is not None \
                and all(item.billed_minor is not None for item in self.items) \
                and not (info.context or {}).get("extraction"):
            if sum(item.billed_minor or 0 for item in self.items) != self.billed_minor:
                raise ValueError(
                    "Einzelrechnungen stimmen nicht mit dem Gesamtrechnungsbetrag ueberein"
                )
        return self


class ExtractionConfirmation(BaseModel):
    structured_data: ExtractedDocumentData
    create_case: bool = True
    expected_case_version: int | None = Field(default=None, ge=1)


class ExtractionConfirmationResult(BaseModel):
    extraction: DocumentExtraction
    case: ReimbursementCase | None = None


class TransactionAllocationCreate(BaseModel):
    transaction_id: UUID
    submission_id: UUID | None = None
    allocation_type: Literal["provider_payment", "reimbursement"]
    amount_minor: int = Field(gt=0)


class TransactionAllocation(BaseModel):
    id: UUID
    case_id: UUID
    submission_id: UUID | None
    transaction_id: UUID
    allocation_type: Literal["provider_payment", "reimbursement"]
    amount_minor: int
    source: str
    confidence: float | None
    evidence: dict[str, Any]
    confirmed_by: UUID | None
    created_at: datetime


class TransactionAllocationView(TransactionAllocation):
    """Zuordnung samt der Buchungsdaten, die zum Pruefen noetig sind.

    Ohne Datum und Empfaenger waere eine Liste von Zuordnungen nicht
    kontrollierbar: der Nutzer soll erkennen, welche Bankbuchung an dem
    Vorgang haengt, statt eine UUID zu vergleichen. Genau das ist der
    Unterschied zwischen "zugeordnet" und "nachvollziehbar zugeordnet".
    """

    booked_date: date
    payee_label: str | None
    transaction_amount_minor: int


class CaseCandidate(BaseModel):
    """Vorschlag, zu welchem Vorgang ein Dokument gehoert.

    `score` und `evidence` sagen bewusst beide etwas: die Zahl sortiert, der
    Klartext begruendet. Zugeordnet wird nur, was jemand angeklickt hat.
    """

    case_id: UUID
    person_id: UUID
    person_name: str
    provider_name: str
    invoice_number: str | None
    invoice_date: date
    billed_minor: int
    reimbursed_minor: int
    currency: str
    overall_status: OverallStatus
    score: float
    evidence: list[str]
    version: int = 1
    expected_minor: int = 0
    item_index: int | None = None


class DocumentCaseLink(BaseModel):
    case_id: UUID
    """Entscheidung und Betraege der Abrechnung in den Vorgang uebernehmen."""
    apply_decision: bool = True
    item_index: int | None = Field(default=None, ge=0, le=199)
    payer_id: UUID | None = None
    expected_case_version: int | None = Field(default=None, ge=1)


class DocumentCaseLinkResult(BaseModel):
    document: ReimbursementDocument
    case: ReimbursementCase


class ReimbursementDecision(BaseModel):
    id: UUID
    case_id: UUID
    payer_id: UUID
    payer_name: str
    payer_type: str
    source_document_id: UUID | None
    source_item_index: int | None
    external_reference: str | None
    approved_minor: int
    deductible_minor: int
    noncovered_minor: int
    status: str


class DocumentCasePreview(BaseModel):
    case_id: UUID
    case_version: int
    expected_before_minor: int
    expected_after_minor: int
    received_minor: int
    outstanding_after_minor: int
    approved_minor: int | None
    currency: str


class TransactionCandidate(BaseModel):
    transaction_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None
    note: str | None
    score: float
    evidence: list[str]
    remaining_minor: int


# ---------------------------------------------------------------------------
# Erstattungsfaehigkeit am Buchungsstrom (docs/ERSTATTUNGSFAEHIGKEIT-PLAN.md)
# ---------------------------------------------------------------------------

MarkState = Literal["expected", "excluded"]
ExpectationMode = Literal["full", "quota", "fixed", "unknown"]
MarkSource = Literal["manual", "rule", "category"]


class ReimbursementMarkInput(BaseModel):
    """Was von einer Buchung zurueckerwartet wird.

    `unknown` ist ein eigener Zustand und nicht dasselbe wie 0: "da kommt was
    zurueck, wie viel weiss ich nicht" soll nicht aus der offenen Summe fallen.
    """

    state: MarkState = "expected"
    expectation_mode: ExpectationMode = "full"
    expectation_rate: Decimal | None = Field(default=None, gt=0, le=1)
    expectation_minor: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_expectation(self) -> "ReimbursementMarkInput":
        if self.expectation_mode == "quota" and self.expectation_rate is None:
            raise ValueError("expectation_rate fehlt fuer expectation_mode='quota'")
        if self.expectation_mode == "fixed" and self.expectation_minor is None:
            raise ValueError("expectation_minor fehlt fuer expectation_mode='fixed'")
        if self.expectation_mode != "quota":
            self.expectation_rate = None
        if self.expectation_mode != "fixed":
            self.expectation_minor = None
        return self


class ReimbursementMark(BaseModel):
    id: UUID
    transaction_id: UUID
    state: MarkState
    expectation_mode: ExpectationMode
    expectation_rate: Decimal | None
    expectation_minor: int | None
    """Aus Modus und Buchungsbetrag gerechnet. None bei 'unknown' und bei
    'excluded' - in beiden Faellen waere jede Zahl erfunden."""
    expected_minor: int | None
    source: MarkSource
    rule_id: UUID | None
    """Gesetzt, sobald ein Vorgang daraus wurde. Welcher, steht bewusst nicht
    hier - die Zuordnung ist ein Gesundheitsdatum."""
    converted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OpenReimbursementMark(ReimbursementMark):
    """Marke samt der Buchung, an der sie haengt - fuer die Liste
    'markiert, noch kein Vorgang'."""

    booked_date: date
    amount_minor: int
    payee_label: str | None
    note: str | None


class BulkReimbursementMarkInput(ReimbursementMarkInput):
    transaction_ids: list[UUID] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_ids(self) -> "BulkReimbursementMarkInput":
        seen: set[UUID] = set()
        unique: list[UUID] = []
        for item in self.transaction_ids:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        self.transaction_ids = unique
        return self


class BulkReimbursementMarkItem(BaseModel):
    transaction_id: UUID
    status: Literal["applied", "unchanged", "skipped", "conflict"]
    reason: str | None = None


class BulkReimbursementMarkResult(BaseModel):
    applied: int
    unchanged: int
    skipped: int
    conflict: int
    items: list[BulkReimbursementMarkItem]


class RuleMarkWithdrawResult(BaseModel):
    """Was eine Sammelruecknahme taete oder getan hat.

    `kept_manual` und `kept_converted` stehen bewusst daneben: eine Ruecknahme,
    die stillschweigend Handentscheidungen mitnimmt oder einen Vorgang aufloest,
    waere ein Datenverlust ohne Ankuendigung.
    """

    withdrawn: int
    kept_manual: int
    kept_converted: int


class MarkToCaseInput(BaseModel):
    """Die Person kommt erst hier ins Spiel.

    Die Marke traegt bewusst keine - eine Bankbuchung sagt nicht, wer behandelt
    wurde, und der Gesundheitsbereich ist enger sichtbar als der Haushalt.
    """

    insured_person_id: UUID


class MarkToCaseResult(BaseModel):
    case_id: UUID
