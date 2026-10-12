"""Request-/Response-Schemas fuer den dynamischen Foerderkatalog (Plan
Abschnitt 6/7.5). Die Regel-DSL selbst (`criteria_json`/`benefit_json`/
`combination_rules_json`) bleibt bewusst als lose `dict[str, Any]` typisiert
-- sie wird von `funding_matching_service.py` strukturell ausgewertet, nicht
hier vorab in ein starres Pydantic-Modell gezwungen, damit neue DSL-Knoten
keine Schema-Migration brauchen (nicht unterstuetzte Knoten fuehren dort zu
`unknown`, nicht zum Absturz)."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

FundingScope = Literal["supranational", "national", "regional", "municipal"]
BenefitType = Literal["grant", "loan", "loan_with_grant", "guarantee", "tax", "information"]
NormalizationStatus = Literal["discovery_only", "rules_partial", "rules_complete", "retired"]
MatchStatus = Literal["eligible", "potentially_eligible", "ineligible", "unknown"]
CombinationStatus = Literal["compatible", "incompatible", "manual_check", "unknown"]
CatalogRunStatus = Literal["running", "succeeded", "failed"]


class EligibilityProfileInput(BaseModel):
    """Private Foerderkriterien-Eingaben (Plan 4.10). `facts` deckt die von
    `funding_matching_service.EligibilityFacts` unterstuetzten Felder ab, wird
    aber hier als offenes Dict gefuehrt, damit ein neues Fact-Feld nicht
    zwingend eine API-Schema-Aenderung braucht; die Engine validiert beim
    Matchlauf."""

    facts: dict[str, Any] = Field(default_factory=dict)
    manual_confirmations: list[str] = Field(default_factory=list)


class EligibilityProfile(BaseModel):
    project_id: UUID
    facts: dict[str, Any]
    manual_confirmations: list[str]
    schema_version: int
    version: int
    updated_at: datetime


class FundingProgramSummary(BaseModel):
    id: UUID
    semantic_key: str
    provider_key: str
    provider_name: str
    program_code: str | None
    title: str
    country_code: str
    scope: FundingScope
    region_codes: list[str]
    official_url: str
    current_version_id: UUID | None
    benefit_type: BenefitType | None
    normalization_status: NormalizationStatus | None
    review_due_at: datetime | None
    is_stale: bool
    # `is_own` unterscheidet den kuratierten Katalog (mitgeliefert, unveraenderlich)
    # vom eigenen Katalog des Haushalts (ueber Paket-/Dateiimport entstanden).
    is_own: bool = False
    is_active: bool = True
    source_version: str | None = None
    retrieved_at: datetime | None = None
    source_url: str | None = None


class FundingProgramVersionDetail(BaseModel):
    id: UUID
    source_version: str
    valid_from: date | None
    valid_to: date | None
    published_at: datetime | None
    retrieved_at: datetime
    review_due_at: datetime
    currency: str
    benefit_type: BenefitType
    applicant_types: list[str]
    project_types: list[str]
    criteria_json: dict[str, Any]
    benefit_json: dict[str, Any]
    loan_terms_json: dict[str, Any]
    combination_rules_json: dict[str, Any]
    manual_checks_json: list[Any]
    source_url: str
    license_name: str | None
    license_url: str | None
    attribution: str | None
    normalization_status: NormalizationStatus


class FundingProgramDetail(FundingProgramSummary):
    current_version: FundingProgramVersionDetail | None


class FundingProgramTermsUpdate(BaseModel):
    """Manuell gepflegter Konditionsstand eines installierten Programms.

    Der Editor darf bewusst nur die veraenderlichen Konditionen ersetzen. Die
    fachlichen Voraussetzungen (``criteria_json``), Zielgruppen und
    Geltungsbereiche bleiben beim Programm und werden aus der vorherigen
    Version uebernommen. So kann ein Haushalt Zins, Laufzeit oder
    Foerderbetrag aktuell halten, ohne versehentlich die Foerderlogik
    umzuschreiben.
    """

    source_version: str = Field(min_length=1, max_length=80)
    review_due_on: date
    source_url: str = Field(min_length=1)
    benefit_json: dict[str, Any]
    loan_terms_json: dict[str, Any] = Field(default_factory=dict)


class ProviderCatalogStatus(BaseModel):
    provider_key: str
    last_run_status: CatalogRunStatus | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error_message: str | None
    age_days: int | None
    programs_new: int
    programs_changed: int
    programs_retired: int


class CatalogStatus(BaseModel):
    providers: list[ProviderCatalogStatus]


class CriterionResultSchema(BaseModel):
    key: str
    label: str
    field: str | None
    input_value: Any | None


class FundingMatch(BaseModel):
    id: UUID
    program_version_id: UUID
    program_id: UUID
    program_semantic_key: str
    program_title: str
    provider_name: str
    status: MatchStatus
    unknown_reason: str | None = None
    estimated_grant_min_minor: int | None
    estimated_grant_max_minor: int | None
    estimated_loan_min_minor: int | None
    estimated_loan_max_minor: int | None
    criteria_met: list[CriterionResultSchema]
    criteria_violated: list[CriterionResultSchema]
    criteria_missing: list[CriterionResultSchema]
    criteria_manual: list[CriterionResultSchema]
    catalog_age_days: int | None
    checked_at: datetime


class FundingCheckRunResponse(BaseModel):
    id: UUID
    scenario_id: UUID
    scenario_version: int
    input_fingerprint: str
    engine_version: str
    matches: list[FundingMatch]
    warnings: list[str]
    created_at: datetime


class CombinationEvaluateRequest(BaseModel):
    match_ids: list[UUID] = Field(min_length=2, max_length=10)


class CombinationResultSchema(BaseModel):
    match_ids: list[UUID]
    program_titles: list[str]
    status: CombinationStatus


class CombinationEvaluateResponse(BaseModel):
    combinations: list[CombinationResultSchema]


# ---------------------------------------------------------------------------
# Foerderkatalog-Import/-Export (Import-Plan S3/S4)
# ---------------------------------------------------------------------------

PreviewStatus = Literal["new", "updated", "unchanged", "conflict", "invalid"]


class PortableFundingProgram(BaseModel):
    """Ein Foerderprogramm in Paketform.

    `source_content_hash` steht bewusst NICHT im Format: er wird beim Import aus
    dem Inhalt berechnet. Ein mitgelieferter Hash waere eine Behauptung, ein
    berechneter eine Tatsache -- und nur der erkennt Aenderungen zuverlaessig.
    """

    semantic_key: str = Field(min_length=1, max_length=120)
    provider_key: str = Field(min_length=1, max_length=60)
    provider_name: str = Field(min_length=1, max_length=160)
    program_code: str | None = None
    title: str = Field(min_length=1, max_length=240)
    country_code: str = Field(default="DE", pattern=r"^[A-Z]{2}$")
    scope: FundingScope = "national"
    region_codes: list[str] = Field(default_factory=list)
    official_url: str = Field(min_length=1)

    source_version: str = Field(min_length=1, max_length=80)
    source_url: str = Field(min_length=1)
    valid_from: date | None = None
    valid_to: date | None = None
    published_at: datetime | None = None
    # Frischeablauf: nach diesem Datum stuft die Engine Treffer auf `unknown`
    # zurueck. Pflicht, weil ein Foerderprogramm ohne Ablaufdatum dasselbe
    # Problem ist wie ein Angebot ohne "gueltig bis".
    review_due_on: date
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    benefit_type: BenefitType
    applicant_types: list[str] = Field(default_factory=list)
    project_types: list[str] = Field(default_factory=list)
    criteria_json: dict[str, Any] = Field(default_factory=dict)
    benefit_json: dict[str, Any] = Field(default_factory=dict)
    loan_terms_json: dict[str, Any] = Field(default_factory=dict)
    combination_rules_json: dict[str, Any] = Field(default_factory=dict)
    manual_checks_json: list[Any] = Field(default_factory=list)
    license_name: str | None = None
    license_url: str | None = None
    attribution: str | None = None
    normalization_status: NormalizationStatus = "discovery_only"


class FundingPackagePayload(BaseModel):
    format_version: str = "1.0"
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    icon: str = "🏛️"
    publisher: str = Field(default="", max_length=160)
    package_version: str = Field(default="", max_length=40)
    researched_on: date | None = None
    programs: list[PortableFundingProgram] = Field(default_factory=list)


class FundingPackageMeta(BaseModel):
    """Kachel im Paket-Tab, inklusive Installationsstand."""

    id: str
    name: str
    description: str
    icon: str
    publisher: str
    package_version: str
    researched_on: date | None
    program_count: int
    installed_count: int
    outdated_count: int


class FundingImportRequest(BaseModel):
    package: FundingPackagePayload
    skip_indices: list[int] = Field(default_factory=list)


class FundingPreviewItem(BaseModel):
    index: int
    semantic_key: str
    title: str
    provider_name: str
    benefit_type: BenefitType
    status: PreviewStatus
    recommended: bool
    changed_fields: list[str] = Field(default_factory=list)
    existing_source_version: str | None = None
    existing_retrieved_at: datetime | None = None
    # Wie viele Vorhaben die aktive Version schon uebernommen haben. Kein
    # Hinderungsgrund -- die alte Version bleibt bestehen und rechnet unveraendert
    # weiter -- aber der Nutzer muss es sehen, bevor er einen Stand abloest.
    adopted_by_projects: int = 0
    problems: list[str] = Field(default_factory=list)


class FundingPreviewResponse(BaseModel):
    package_id: str
    package_name: str
    items: list[FundingPreviewItem]
    new_count: int
    updated_count: int
    unchanged_count: int
    conflict_count: int
    invalid_count: int


class FundingImportResult(BaseModel):
    programs_created: int
    versions_created: int
    unchanged: int
    skipped_deselected: int
    invalid: int
    errors: list[str] = Field(default_factory=list)
    run_id: UUID | None = None


class FundingExportRequest(BaseModel):
    program_ids: list[UUID] = Field(default_factory=list)
    package_name: str = "Meine Förderprogramme"
    description: str = ""
    include_curated: bool = False


class AdoptMatchRequest(BaseModel):
    scenario_id: UUID
    name: str | None = None
    request_id: UUID | None = None
    loan_term_variant_id: str | None = Field(default=None, min_length=1, max_length=80)
    loan_rate_option_id: str | None = Field(default=None, min_length=1, max_length=80)
    term_months: int | None = Field(default=None, ge=1, le=600)
    repayment_free_months: int | None = Field(default=None, ge=0, le=120)
    follow_up_rate_bps_annual: int | None = Field(default=None, ge=0, le=100_000)


class AdoptMatchResponse(BaseModel):
    """Ergebnis der Uebernahme eines Treffers.

    `financing_source_id` bleibt die fuehrende Quelle (bei Darlehensprogrammen
    das Darlehen), damit bestehende Aufrufer unveraendert funktionieren.
    `grant_financing_source_id` ist nur bei `benefit_type='loan_with_grant'`
    gesetzt -- dort entstehen zwei Quellen, weil ein Zuschussanteil sonst
    verloren ginge.

    `warnings` erklaert, warum kein Tilgungsplan entstanden ist. Ein fehlender
    Zinssatz fuehrt bewusst NICHT zu einem geratenen Wert: die KfW aktualisiert
    ihn taeglich und der Katalog haelt ihn deshalb absichtlich nicht vor.
    """

    financing_source_id: UUID
    already_applied: bool
    grant_financing_source_id: UUID | None = None
    debt_plan_id: UUID | None = None
    warnings: list[str] = Field(default_factory=list)
