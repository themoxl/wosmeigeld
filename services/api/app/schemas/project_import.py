"""Request-/Response-Schemas fuer den Excel-/CSV-Kostenplanimport (Plan
Abschnitt 10). Eigene Datei statt Erweiterung von schemas/projects.py: das
Zielschema (Datei-Preview/Apply-Fluss) ist inhaltlich unabhaengig von den
Vorhaben-Kernschemas und wuerde diese sonst unnoetig aufblaehen."""

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ImportFileFormat = Literal["csv_generic", "xlsx_generic"]
RowAction = Literal["create", "update", "skip"]


class CostImportMappingProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    format: ImportFileFormat
    column_mapping: dict[str, str] = Field(default_factory=dict)


class CostImportMappingProfile(CostImportMappingProfileInput):
    id: UUID


class CostImportPreviewRow(BaseModel):
    row_number: int
    section_name: str | None
    item_name: str | None
    status: str | None
    calculation_mode: Literal["fixed", "quantity"]
    quantity: Decimal | None
    unit_label: str | None
    unit_price_minor: int | None
    estimate_manual_minor: int | None
    quoted_amount_minor: int | None
    committed_amount_minor: int | None
    vendor_label: str | None
    expected_start_on: date | None
    expected_end_on: date | None
    note: str | None
    matched_section_id: UUID | None
    matched_item_id: UUID | None
    errors: list[str]


class CostImportPreviewSums(BaseModel):
    row_count: int
    rows_with_errors_count: int
    new_items_count: int
    matched_items_count: int
    estimate_total_minor: int
    quoted_total_minor: int
    committed_total_minor: int


class CostImportPreviewResponse(BaseModel):
    preview_fingerprint: str
    detected_format: ImportFileFormat
    sheet_names: list[str]
    selected_sheet: str | None
    rows: list[CostImportPreviewRow]
    sums: CostImportPreviewSums


class CostImportRowResolution(BaseModel):
    row_number: int
    action: RowAction
    target_item_id: UUID | None = None


class CostImportApplyResult(BaseModel):
    request_id: str
    idempotent_replay: bool
    created_item_ids: list[UUID]
    updated_item_ids: list[UUID]
    skipped_row_numbers: list[int]
