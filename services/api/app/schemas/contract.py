"""Vertragsobjekt: Vertraege, Abonnements und wiederkehrende Zahlungen.

Loest die getrennten Modelle `RecurringRule` und `Subscription` ab
(docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Phase 1).

Zwei Festlegungen praegen diese Modelle:

* `amount_minor` ist immer positiv, die Richtung steht getrennt in `direction`.
  Die frueheren `recurring_rules` mischten negative (erkannte) und positive
  (manuell angelegte) Betraege; Summe und Einfaerbung widersprachen sich dadurch.
* `status` unterscheidet `cancelled` (gekuendigt, laeuft bis `ends_on` weiter und
  kostet bis dahin Geld) von `ended` (beendet). Ohne diese Trennung verschwindet
  ein zum Jahresende gekuendigter Vertrag zu frueh aus dem Budget.

Phase 4 ergaenzt den Kuendigungstermin (`ContractTermDeadline`). Er haengt am
`Contract` und nicht nur an der Zusammenfassung, weil er ausschliesslich aus den
Feldern desselben Datensatzes entsteht - ohne Join, ohne Aggregat. Die
Detailseite braucht den Countdown, und ein zweiter Endpunkt fuer eine reine
Rechenfunktion waere ein Rundweg. Die Zusammenfassung fuehrt zusaetzlich
`term_deadlines` als gefilterte Projektion fuer die Uebersicht.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

ContractType = Literal[
    "subscription",
    "insurance",
    "rent",
    "loan",
    "installment",
    "utility",
    "telecom",
    "membership",
    "donation",
    "savings",
    "income",
    "other",
]
ContractStatus = Literal["active", "paused", "cancelled", "ended", "not_a_contract"]
ContractDirection = Literal["expense", "income"]
ContractFrequency = Literal["weekly", "monthly", "quarterly", "semiannual", "yearly"]
ContractDueRule = Literal["day_of_month", "month_end", "nth_business_day"]
ContractKeySource = Literal["mandate", "purpose_pattern", "manual"]
ContractSource = Literal["detected", "manual", "confirmed"]

# Einnahmeart (Liquiditaetsplan, Stufe 6). Bewusst getrennt von
# `contract_type`: der Vertragstyp beschreibt, WAS der Vertrag ist, die
# Einnahmeart, WOHER das Geld kommt. Ein Vertrag vom Typ `income` kann eine
# Rente, eine Miete oder ein Gehalt sein - und nur beim Gehalt haengt die
# Steuerklasse daran (household_forecast_service.tax_class_hint_applies).
# Deckungsgleich mit contracts_income_kind_check im Schema.
ContractIncomeKind = Literal[
    "salary",
    "self_employment",
    "pension",
    "benefit",
    "rental",
    "capital",
    "support",
    "other",
]

ContractClearableField = Literal[
    "provider_label",
    "payee_id",
    "account_id",
    "category_id",
    "contract_key",
    "contract_key_source",
    "due_day",
    "cancelled_on",
    "ends_on",
    "min_term_months",
    "cancellation_period_days",
    "starts_on",
    "renewal_term_months",
    "provider_template_key",
    "installments_total",
    "note",
    "reserve_started_on",
    "income_kind",
    "person_id",
]

_NAME_FIELD = Field(min_length=1, max_length=160)


def _clean_text(value: str) -> str:
    return " ".join(value.split())


# ---------------------------------------------------------------------------
# Vertrag
# ---------------------------------------------------------------------------


class ContractCreate(BaseModel):
    name: str = _NAME_FIELD
    provider_label: str | None = Field(default=None, max_length=160)
    contract_type: ContractType = "other"

    payee_id: UUID | None = None
    account_id: UUID | None = None
    category_id: UUID | None = None
    contract_key: str | None = Field(default=None, max_length=160)
    contract_key_source: ContractKeySource | None = None

    direction: ContractDirection = "expense"
    amount_minor: int = Field(gt=0)
    frequency: ContractFrequency = "monthly"

    # Stufe 6: Einnahmen nach Person und Art. Beide Felder bleiben optional -
    # eine Einnahme ohne Zuordnung ist eine gueltige Einnahme, nur eben eine
    # ungruppierte. Die Art ist an `direction = 'income'` gebunden (CHECK
    # contracts_income_kind_direction_chk), die Person nicht: eine gemeinsam
    # getragene Ausgabe darf ebenfalls einer Person zugeordnet sein.
    income_kind: ContractIncomeKind | None = None
    person_id: UUID | None = None

    due_rule: ContractDueRule = "day_of_month"
    due_day: int | None = Field(default=None, ge=1, le=31)
    next_due_date: date
    tolerance_days: int = Field(default=4, ge=0, le=31)
    tolerance_amount_minor: int = Field(default=0, ge=0)

    status: ContractStatus = "active"
    cancelled_on: date | None = None
    ends_on: date | None = None
    min_term_months: int | None = Field(default=None, ge=1, le=600)
    cancellation_period_days: int | None = Field(default=None, ge=0, le=1095)
    # Vertragsbeginn, nicht der erste erkannte Buchungstag: `first_seen_date`
    # haengt am Importzeitraum und taugt als Anker der Mindestlaufzeit nicht.
    starts_on: date | None = None
    renewal_term_months: int | None = Field(default=None, ge=1, le=120)
    provider_template_key: str | None = Field(default=None, max_length=80)

    installments_total: int | None = Field(default=None, ge=2, le=240)

    note: str | None = None
    # None means "client did not decide": eligible new expense contracts are
    # enabled by default in the route.  False is an explicit opt-out.
    reserve_enabled: bool | None = None
    reserve_started_on: date | None = None
    reserve_starting_amount_minor: int = Field(default=0, ge=0)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return _clean_text(value)

    @field_validator("provider_label", "contract_key")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _clean_text(value)
        return cleaned or None

    @model_validator(mode="after")
    def _income_kind_needs_income(self) -> "ContractCreate":
        # Spiegelt contracts_income_kind_direction_chk. Ohne die Pruefung hier
        # antwortete die API auf eine widerspruechliche Eingabe mit 500 aus
        # einer abgelehnten Einfuegung statt mit 422 an der richtigen Stelle.
        if self.income_kind is not None and self.direction != "income":
            raise ValueError("income_kind ist nur bei direction='income' erlaubt")
        return self


class ContractUpdate(BaseModel):
    """Teilaenderung. Nicht mitgeschickte Felder bleiben unangetastet; ein Feld
    wirklich zu leeren verlangt seinen Namen in `clear_fields`, weil `null`
    allein von "nicht angefasst" nicht unterscheidbar waere.

    `source`, `detection_confidence`, `missed_periods`, `first_seen_date` und
    `last_seen_date` fehlen bewusst: das sind Befunde des Abgleichslaufs, keine
    Nutzerangaben.
    """

    name: str | None = Field(default=None, min_length=1, max_length=160)
    provider_label: str | None = Field(default=None, max_length=160)
    contract_type: ContractType | None = None

    payee_id: UUID | None = None
    account_id: UUID | None = None
    category_id: UUID | None = None
    contract_key: str | None = Field(default=None, max_length=160)
    contract_key_source: ContractKeySource | None = None

    direction: ContractDirection | None = None
    amount_minor: int | None = Field(default=None, gt=0)
    frequency: ContractFrequency | None = None

    # Beide leerbar ueber `clear_fields` - der Widerspruch "Art gesetzt, aber
    # Richtung auf Ausgabe geaendert" wird im Schreibpfad aufgeloest, weil
    # dort erst die resultierende Richtung feststeht (routes/contracts.py).
    income_kind: ContractIncomeKind | None = None
    person_id: UUID | None = None

    due_rule: ContractDueRule | None = None
    due_day: int | None = Field(default=None, ge=1, le=31)
    next_due_date: date | None = None
    tolerance_days: int | None = Field(default=None, ge=0, le=31)
    tolerance_amount_minor: int | None = Field(default=None, ge=0)

    status: ContractStatus | None = None
    cancelled_on: date | None = None
    ends_on: date | None = None
    min_term_months: int | None = Field(default=None, ge=1, le=600)
    cancellation_period_days: int | None = Field(default=None, ge=0, le=1095)
    starts_on: date | None = None
    renewal_term_months: int | None = Field(default=None, ge=1, le=120)
    provider_template_key: str | None = Field(default=None, max_length=80)

    installments_total: int | None = Field(default=None, ge=2, le=240)

    note: str | None = None
    reserve_enabled: bool | None = None
    reserve_started_on: date | None = None
    reserve_starting_amount_minor: int | None = Field(default=None, ge=0)
    clear_fields: list[ContractClearableField] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return _clean_text(value) if value is not None else None

    @field_validator("provider_label", "contract_key")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _clean_text(value)
        return cleaned or None


class ContractTermDeadline(BaseModel):
    """Letzter Tag, an dem eine Kuendigung beim Anbieter sein muss.

    Die Kante ist `deadline_on`, nicht `term_ends_on`: wer bis zum Ende der
    Laufzeit wartet, hat die Frist bereits verpasst. Der Countdown zaehlt
    deshalb Tage bis `deadline_on`, die Karte nennt `term_ends_on` als
    "wirksam zum".

    `kind` trennt zwei Faelle, die dieselbe Karte fuellen: bei `fixed_term`
    laeuft eine Frist ab und `days_left` ist der Countdown; bei `open_ended`
    gibt es keine Frist, die verstreichen kann - der Vertrag ist mit
    `cancellation_period_days` Vorlauf jederzeit kuendbar, `days_left` ist dann
    ohne Aussage und `term_ends_on` der fruehestmoegliche Wirksamkeitstag.
    Nur `fixed_term` erzeugt ein Ereignis.
    """

    kind: Literal["fixed_term", "open_ended"]
    deadline_on: date
    term_ends_on: date
    days_left: int
    # 0 = Mindestlaufzeit, 1 = erste automatische Verlaengerung, ...
    renewal_index: int


class Contract(BaseModel):
    id: UUID
    household_id: UUID

    name: str
    provider_label: str | None
    contract_type: ContractType

    payee_id: UUID | None
    account_id: UUID | None
    category_id: UUID | None
    contract_key: str | None
    contract_key_source: ContractKeySource | None

    direction: ContractDirection
    amount_minor: int
    frequency: ContractFrequency

    income_kind: ContractIncomeKind | None
    person_id: UUID | None

    due_rule: ContractDueRule
    due_day: int | None
    next_due_date: date
    first_seen_date: date | None
    last_seen_date: date | None
    tolerance_days: int
    tolerance_amount_minor: int

    status: ContractStatus
    cancelled_on: date | None
    ends_on: date | None
    min_term_months: int | None
    cancellation_period_days: int | None
    starts_on: date | None
    renewal_term_months: int | None
    provider_template_key: str | None

    installments_total: int | None
    installments_paid: int

    source: ContractSource
    detection_confidence: float | None
    missed_periods: int
    last_matched_at: datetime | None

    note: str | None
    reserve_enabled: bool
    reserve_started_on: date | None
    reserve_starting_amount_minor: int
    # Current server calculation for compact list/detail display.  The budget
    # endpoint exposes the complete per-month breakdown.
    reserve_monthly_minor: int = 0
    version: int
    # Letzter Anker der Fristrechnung, wenn weder starts_on noch
    # first_seen_date gesetzt sind - ein manuell angelegter Vertrag ohne
    # Buchungshistorie bekaeme sonst nie einen Kuendigungstermin.
    created_at: datetime

    # Abgeleitet, nicht gespeichert: reine Rechnung aus den Feldern oben
    # (contract_service.compute_term_deadline). None heisst "nichts zu
    # erinnern", nicht "keine Kuendigungsfrist bekannt".
    term_deadline: ContractTermDeadline | None = None


class ContractStatusChange(BaseModel):
    """Statuswechsel als eigener Endpunkt statt als PATCH-Feld, damit die
    Begleitdaten (Kuendigungs- und Enddatum) fachlich zusammen geprueft werden
    koennen und ein Ereignis entsteht."""

    status: ContractStatus
    cancelled_on: date | None = None
    ends_on: date | None = None
    reason: str | None = Field(default=None, max_length=280)


# ---------------------------------------------------------------------------
# Betragsverlauf, Faelligkeiten, Ereignisse
# ---------------------------------------------------------------------------


class ContractAmountEntry(BaseModel):
    id: UUID
    contract_id: UUID
    amount_minor: int
    valid_from: date
    valid_to: date | None
    delta_minor: int | None
    change_reason: Literal["initial", "detected_change", "manual", "backfill"]
    source_transaction_id: UUID | None


class ContractAmountCreate(BaseModel):
    """Manuelle Betragskorrektur. Schliesst die offene Stufe ab `valid_from - 1`
    und oeffnet eine neue."""

    amount_minor: int = Field(gt=0)
    valid_from: date


class ContractOccurrence(BaseModel):
    id: UUID
    contract_id: UUID
    due_date: date
    status: Literal["matched", "missing", "skipped"]
    transaction_id: UUID | None
    booked_date: date | None
    amount_minor: int | None
    amount_delta_minor: int | None
    days_delta: int | None
    match_score: float | None


class ContractEvent(BaseModel):
    id: UUID
    contract_id: UUID
    kind: Literal[
        "created",
        "price_change",
        "payment_missing",
        "payment_resumed",
        "cancelled",
        "ended",
        "reactivated",
        "merged",
        "merge_undone",
        "dismissed",
        "installment_completed",
        "term_ending",
    ]
    occurred_on: date
    # Bewusst NICHT `Any`: daraus erzeugt openapi-typescript ein unbrauchbares
    # `Record<string, unknown>`. Die Liste deckt alle tatsaechlich geschriebenen
    # Nutzlasten ab (Statuswechsel, Preisdelta, Quell-IDs beim Zusammenfuehren).
    payload: dict[str, str | int | float | bool | list[str] | None]
    acknowledged: bool


# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------


class ContractTypeSummary(BaseModel):
    contract_type: ContractType
    count: int
    monthly_minor: int
    yearly_minor: int


class ContractCategorySummary(BaseModel):
    """Wie ContractTypeSummary, aber nach der frei vergebenen Budget-Kategorie
    statt der festen Vertragsart - die fuehrende Gruppierung fuer Filter,
    Sortierung und Auswertung in der Oberflaeche (Vertragsart bleibt nur ein
    Pflichtfeld im Datenmodell, siehe contract_type oben). `category_id` ist
    absichtlich nur die ID: Name/Farbe/Gruppe loest die Oberflaeche gegen den
    ohnehin geladenen Kategoriebaum auf, damit eine spaetere Umbenennung nicht
    zu zwei auseinanderlaufenden Namensquellen fuehrt. `None` buendelt alle
    Vertraege ohne Kategorie."""

    category_id: UUID | None
    count: int
    monthly_minor: int
    yearly_minor: int


class ContractUpcoming(BaseModel):
    contract_id: UUID
    name: str
    contract_type: ContractType
    direction: ContractDirection
    due_date: date
    amount_minor: int


class ContractEnding(BaseModel):
    contract_id: UUID
    name: str
    contract_type: ContractType
    ends_on: date
    amount_minor: int


CashflowGranularity = Literal["month", "quarter"]


class ContractCashflowPeriod(BaseModel):
    """Ein Kalendermonat bzw. -quartal der Cashflow-Prognose.

    Bezugsraum ist der KALENDERZEITRAUM und nicht ein rollierendes Tagesfenster.
    Der Unterschied ist keine Kosmetik: in "die naechsten 30 Tage" faellt ein
    monatlicher Vertrag je nach Faelligkeitstag ein- oder zweimal, in "die
    naechsten 90 Tage" drei- oder viermal. Die Summe schwankte damit mit dem
    Kalender und stimmte nie mit der Fixkostensumme oder der Fixkostenquote im
    Budget ueberein - genau der Widerspruch, den diese Aufteilung beseitigt.

    `expense_minor` und `income_minor` sind Bruttosummen und immer positiv; die
    Richtung steckt im Feldnamen, `net_minor` ist Einnahme minus Ausgabe.

    Die `remaining_*`-Felder zaehlen nur, was ab heute noch aussteht. Sie stehen
    neben den vollen Summen und ersetzen sie nicht: die volle Summe beantwortet
    "was kostet mich der Monat", der Rest "was geht noch vom Konto".
    """

    period_start: date
    period_end: date
    label: str

    expense_minor: int
    income_minor: int
    net_minor: int

    remaining_expense_minor: int
    remaining_income_minor: int
    remaining_net_minor: int

    # Laufende Summe der Nettobetraege ueber die Perioden hinweg - die einzige
    # Kumulation, die eine Aussage traegt. Die frueherere zeilenweise laufende
    # Summe ueber Einzelfaelligkeiten addierte Ausgaben und Einnahmen quer durch
    # den Kalender und war an keiner Stelle mit irgendetwas vergleichbar.
    cumulative_net_minor: int

    items: list[ContractUpcoming]


class ContractCashflowResponse(BaseModel):
    granularity: CashflowGranularity
    generated_on: date
    periods: list[ContractCashflowPeriod]


class ContractYearlyCost(BaseModel):
    """Ein Vertrag in der Rangliste der teuersten.

    `amount_minor` ist der Betrag je Periode und steht bewusst daneben: ohne ihn
    liesse sich die Jahressumme einer jaehrlichen Praemie nicht von zwoelf
    Monatsbetraegen unterscheiden.
    """

    contract_id: UUID
    name: str
    contract_type: ContractType
    frequency: ContractFrequency
    amount_minor: int
    monthly_minor: int
    yearly_minor: int


class ContractTermSoon(BaseModel):
    """Vertrag mit bald ablaufender Kuendigungsfrist."""

    contract_id: UUID
    name: str
    contract_type: ContractType
    amount_minor: int
    deadline_on: date
    term_ends_on: date
    days_left: int


class ContractSummary(BaseModel):
    """Ausgaben und Einnahmen getrennt: ein Gehaltsvertrag darf die
    Fixkostensumme nicht verwaessern."""

    monthly_expense_minor: int
    yearly_expense_minor: int
    monthly_income_minor: int
    yearly_income_minor: int
    monthly_net_minor: int

    active_count: int
    paused_count: int
    cancelled_count: int
    ended_count: int

    by_type: list[ContractTypeSummary]
    by_category: list[ContractCategorySummary]
    upcoming: list[ContractUpcoming]
    ending_soon: list[ContractEnding]
    # Nach JAHRESkosten sortiert, nicht nach Monatsbetrag: eine jaehrliche
    # Praemie von 600 EUR ist teurer als ein Abo fuer 9,99 EUR im Monat, stuende
    # in einer Monatsrangliste aber weit darunter.
    yearly_by_contract: list[ContractYearlyCost]
    term_deadlines: list[ContractTermSoon]


# ---------------------------------------------------------------------------
# Einnahmen nach Person und Art (Liquiditaetsplan, Stufe 6)
# ---------------------------------------------------------------------------


class ContractIncomeKindBucket(BaseModel):
    """Eine Einnahmeart mit ihrer Monatssumme.

    `income_kind = null` ist kein Fehler, sondern der Bestand vor dieser Stufe:
    Einnahmen, denen noch keine Art zugewiesen wurde. Die Oberflaeche soll sie
    ausweisen koennen, statt sie stillschweigend unter "Sonstiges" zu mischen.
    """

    income_kind: ContractIncomeKind | None
    contract_count: int
    monthly_minor: int


class ContractIncomePersonBucket(BaseModel):
    """Eine Person mit ihrer Monatssumme und deren Aufteilung nach Art."""

    person_id: UUID | None
    # Null nur zusammen mit `person_id = null`: die Einnahmen ohne Zuordnung.
    person_name: str | None
    # Geloeschte Personen tauchen weiter auf, solange Vertraege auf sie zeigen
    # (Soft-Delete). Die Anzeige soll sie kennzeichnen koennen, statt eine
    # laengst entfernte Person wie eine aktuelle zu fuehren.
    person_is_active: bool
    contract_count: int
    monthly_minor: int
    by_kind: list[ContractIncomeKindBucket]


class ContractIncomeTrendPoint(BaseModel):
    """Ein Monat der Entwicklung. Einnahmen und Fixkosten stehen bewusst
    nebeneinander: eine steigende Einnahme sagt fuer sich genommen nichts,
    solange die Fixkosten schneller steigen."""

    period_start: date
    label: str
    income_minor: int
    fixed_minor: int
    net_minor: int


class ContractIncomeSummary(BaseModel):
    """Einnahmen je Person und je Art, dazu ihre Entwicklung.

    Alle Betraege sind je Vertrag ueber `contract_service.monthly_minor`
    normiert und erst danach summiert - niemals aus einer Tagesreihe
    zurueckgerechnet (Plan 5.2). Sonst weichen diese Zahlen von Budget,
    Berichten und Vorschau um Rundungscent ab.
    """

    generated_on: date
    monthly_income_minor: int
    monthly_fixed_minor: int
    by_person: list[ContractIncomePersonBucket]
    by_kind: list[ContractIncomeKindBucket]
    trend: list[ContractIncomeTrendPoint]


# ---------------------------------------------------------------------------
# Zusammenfuehren
# ---------------------------------------------------------------------------


class ContractMergeRequest(BaseModel):
    target_id: UUID
    source_ids: list[UUID] = Field(min_length=1)


class ContractMergePreview(BaseModel):
    target_id: UUID
    target_name: str
    source_names: list[str]
    moved_occurrences: int
    moved_history_entries: int
    moved_events: int
    resulting_amount_minor: int
    resulting_frequency: ContractFrequency
    amount_steps: list[ContractAmountEntry]
    blocking_reason: str | None


class ContractMergeApply(ContractMergeRequest):
    request_id: str = Field(min_length=1, max_length=120)


class ContractMergeResult(BaseModel):
    contract: Contract
    merged_count: int
    moved_occurrences: int
    moved_history_entries: int
    # Kennung der Operation fuer die sofortige "Rueckgaengig"-Aktion
    # (Abschnitt 19.7). None nur fuer den idempotenten Wiederholungsfall, in
    # dem gar nichts geschrieben wurde.
    merge_id: UUID | None = None


# ---------------------------------------------------------------------------
# Reversible Zusammenfuehrung: Undo (Abschnitt 19.7)
# ---------------------------------------------------------------------------


class ContractMergeUndoPreview(BaseModel):
    """Antwort auf GET .../merges/{id}/undo-preview.

    `can_undo=false` bedeutet: seit dem Merge ist etwas hinzugekommen, das ein
    stiller Rollback verschlucken wuerde (neue Buchungszuordnung, weitere
    Aenderung am Zielvertrag). Der Nutzer sieht dann die Gruende statt eines
    Teil-Rollbacks (Plan Abschnitt 19.7)."""

    merge_id: UUID
    target_id: UUID
    target_name: str
    source_ids: list[UUID]
    source_names: list[str]
    already_undone: bool
    can_undo: bool
    conflict_reasons: list[
        Literal[
            "already_undone",
            "target_missing",
            "target_changed_since_merge",
            "new_activity_since_merge",
        ]
    ]
    new_occurrence_count: int
    merged_at: datetime


class ContractMergeUndoResult(BaseModel):
    contract: Contract
    restored_source_count: int


# ---------------------------------------------------------------------------
# Manuelle Buchungszuordnung ohne Merge (Abschnitt 19.7)
# ---------------------------------------------------------------------------


class ContractReassignPreview(BaseModel):
    transaction_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None
    current_contract_id: UUID | None
    current_contract_name: str | None
    target_contract_id: UUID | None
    target_contract_name: str | None
    # Faelligkeit, die durch die Umhaengung frei bzw. neu belegt wird - leer,
    # wenn die Buchung aktuell an keiner Faelligkeit haengt.
    affects_occurrence: bool


class ContractReassignRequest(BaseModel):
    # None = Zuordnung ausdruecklich loesen ("kein Vertrag"), nicht bloss
    # "keine Angabe". Fuer "einem NEUEN Vertrag zuordnen" legt der Client
    # zuerst ueber POST /v1/contracts einen Vertrag an (inkl. rueckwirkendem
    # Erstlauf) und ruft danach mit dessen id hierher - kein eigener
    # kombinierter Endpunkt, um Vertragsanlage nicht zu verdoppeln.
    target_contract_id: UUID | None = None


class ContractReassignResult(BaseModel):
    transaction_id: UUID
    contract_id: UUID | None
    override_id: UUID


# ---------------------------------------------------------------------------
# Duplikatpruefung und Einstellungen
# ---------------------------------------------------------------------------


class ContractDuplicateCheck(BaseModel):
    """Vor dem Anlegen aufgerufen, damit der Nutzer den Treffer sieht, bevor er
    einen zweiten Eintrag desselben Vertrags erzeugt."""

    name: str = _NAME_FIELD
    payee_id: UUID | None = None
    amount_minor: int | None = Field(default=None, gt=0)
    frequency: ContractFrequency | None = None
    exclude_id: UUID | None = None


class ContractDuplicateCandidate(BaseModel):
    id: UUID
    name: str
    provider_label: str | None
    amount_minor: int
    frequency: ContractFrequency
    status: ContractStatus
    reason: Literal["same_payee", "similar_name", "similar_amount"]


class ContractProviderSuggestion(BaseModel):
    """Vorschlag aus dem globalen Anbieterkatalog fuer das Anlegen-Formular.

    `category_id` ist die im Haushalt aufgeloeste Kategorie zu
    `default_category_key`. Sie ist `None`, wenn der Haushalt die
    Standardkategorie umbenannt, archiviert oder nie installiert hat - dann
    bleibt der Vorschlag trotzdem brauchbar, nur ohne Kategorievorbelegung.
    """

    semantic_key: str
    provider_name: str
    contract_type: ContractType
    default_category_key: str | None
    category_id: UUID | None
    typical_frequency: ContractFrequency | None
    typical_min_term_months: int | None
    typical_cancellation_days: int | None


class ContractSettings(BaseModel):
    """Haushaltsweite Vertragseinstellungen.

    Die drei `notify_*`-Schalter sind einzeln waehlbar, weil der Worker sie
    einzeln auswertet (worker/notifications/contract_alerts.py): wem die
    taeglichen Preisaenderungs-Hinweise zu viel sind, der soll nicht auch die
    Kuendigungsfrist-Erinnerung verlieren. Ohne sie bliebe nur "alles oder
    nichts" ueber die Berechtigung des Betriebssystems.
    """

    auto_end_after_missed: Literal["off", "2", "3", "4"]
    notify_price_change: bool = True
    notify_payment_missing: bool = True
    notify_term_ending: bool = True


class ContractSettingsUpdate(BaseModel):
    auto_end_after_missed: Literal["off", "2", "3", "4"]
    notify_price_change: bool = True
    notify_payment_missing: bool = True
    notify_term_ending: bool = True


# ---------------------------------------------------------------------------
# Abgleichslauf
# ---------------------------------------------------------------------------


class ContractRescanResult(BaseModel):
    """Ergebnis des manuell angestossenen Abgleichs.

    Die drei Trefferarten sind ueberschneidungsfrei: ihre Summe ist die Zahl der
    in diesem Lauf geschriebenen Faelligkeiten. Eine Preisaenderung zaehlt also
    nicht zusaetzlich als Treffer - sonst waere die Anzeige "12 Zahlungen
    zugeordnet" bei 11 Buchungen schlicht falsch.

    `ended_count` steht daneben und zaehlt Vertraege, nicht Faelligkeiten.

    Die im selben Aufruf geschriebenen Fristerinnerungen stehen bewusst NICHT
    hier: sie sind weder Faelligkeit noch Vertragsende, sondern ein Ereignis,
    und eine dritte Einheit in derselben Ergebniszeile waere genau die
    Vermischung, die diese Klasse vermeidet. Nachvollziehbar bleiben sie im
    Audit-Log des Rescans und in `contract_events`.
    """

    contracts_examined: int
    matched_count: int
    price_change_count: int
    missing_count: int
    ended_count: int


# ---------------------------------------------------------------------------
# Fixkostenziel und Senkungsszenarien
# ---------------------------------------------------------------------------


class ContractScenarioItemInput(BaseModel):
    contract_id: UUID
    action: Literal["cancel", "reduce"]
    # Betrag JE PERIODE, gleiche Einheit wie contracts.amount_minor - nicht je
    # Monat. Sonst hiesse "auf 20 EUR senken" bei einem Jahresvertrag etwas
    # anderes als in der Vertragszeile daneben.
    target_amount_minor: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=300)

    # Bewusst `model_validator` statt `field_validator`: ein WEGGELASSENES Feld
    # laeuft nicht durch die Feldvalidierung (Pydantic validiert Defaults nicht),
    # und genau der Fall - "reduce" ohne Zielbetrag - ist der, den diese Regel
    # abfangen muss.
    @model_validator(mode="after")
    def _amount_matches_action(self) -> "ContractScenarioItemInput":
        if self.action == "reduce" and self.target_amount_minor is None:
            raise ValueError("target_amount_minor ist bei action='reduce' erforderlich")
        if self.action == "cancel" and self.target_amount_minor is not None:
            raise ValueError("target_amount_minor ist bei action='cancel' nicht erlaubt")
        return self


class ContractScenarioItem(BaseModel):
    """Eine Position mit ihrer Wirkung.

    `current_monthly_minor` und `planned_monthly_minor` sind normierte
    Monatswerte, `monthly_saving_minor` ihre Differenz. Sie stehen alle drei
    hier, damit die Oberflaeche nicht selbst normieren muss - eine zweite
    Normierung im Client waere die naechste Quelle fuer Rundungsabweichungen
    gegenueber der Fixkostensumme.
    """

    id: UUID
    contract_id: UUID
    contract_name: str
    contract_type: ContractType
    frequency: ContractFrequency
    action: Literal["cancel", "reduce"]
    amount_minor: int
    target_amount_minor: int | None
    note: str | None

    current_monthly_minor: int
    planned_monthly_minor: int
    monthly_saving_minor: int
    yearly_saving_minor: int


class ContractScenario(BaseModel):
    """Ein Was-waere-wenn-Plan samt gerechnetem Ergebnis.

    Das Szenario aendert keinen Vertrag. Ein geplanter Kuendigungswunsch ist
    keine Kuendigung, und die Fixkostensumme darf sich davon nicht bewegen -
    sonst zeigte das Budget eine Ersparnis an, die es noch gar nicht gibt.
    """

    id: UUID
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    items: list[ContractScenarioItem]

    monthly_saving_minor: int
    yearly_saving_minor: int
    resulting_monthly_minor: int
    resulting_ratio_percent: float
    reaches_target: bool


class ContractScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_active: bool = False
    items: list[ContractScenarioItemInput] = Field(default_factory=list)


class ContractScenarioUpdate(BaseModel):
    """Teilaenderung. `items` ersetzt bei Angabe die gesamte Liste.

    Bewusst Ersetzen statt Zusammenfuehren: die Oberflaeche haelt die Auswahl als
    Ganzes: eine Position zu entfernen waere sonst ein zweiter Endpunkt.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None
    items: list[ContractScenarioItemInput] | None = None


class ContractCostTargetInput(BaseModel):
    """Angestrebte Fixkosten.

    Die Quote fuehrt, der Betrag ist optional. Grund: ein absolutes Ziel von
    "2.400 EUR" wird nach der naechsten Gehaltsaenderung stillschweigend falsch,
    ohne dass irgendwo etwas kaputtgeht - eine Quote nicht. Wer trotzdem einen
    festen Betrag will, setzt `target_monthly_minor`; dann fuehrt der Betrag.
    """

    target_ratio_percent: float = Field(gt=0, le=100)
    target_monthly_minor: int | None = Field(default=None, ge=0)
    target_date: date | None = None
    note: str | None = Field(default=None, max_length=500)


class ContractCostTargetView(ContractCostTargetInput):
    """Ziel, Ist und Luecke in einer Antwort.

    Die Bezugsgroesse der Quote ist bewusst dieselbe wie bei der Fixkostenquote
    im Budget (gebuchte Einnahmen, ersatzweise die Einnahmevertraege). Eine
    eigene Bezugsgroesse waere eine zweite Quote unter demselben Namen - und der
    Nutzer haette zwei Zahlen, von denen keine falsch aussieht.

    `is_configured` unterscheidet "noch kein Ziel gesetzt" von "Ziel zufaellig
    auf dem Vorgabewert". Ohne dieses Feld saehe die Oberflaeche einen
    Vorgabewert von 50 % und meldete eine Zielverfehlung, die niemand gesetzt
    hat.
    """

    is_configured: bool

    # Ist-Zustand
    current_monthly_minor: int
    current_yearly_minor: int
    current_ratio_percent: float
    income_basis_minor: int
    income_basis: Literal["actual", "contract"]
    contract_count: int

    # Aus Quote bzw. Betrag abgeleiteter Zielwert in Euro-Minor.
    effective_target_monthly_minor: int
    # Positiv = so viel muss noch runter. Negativ = Ziel bereits unterschritten.
    gap_monthly_minor: int
    gap_yearly_minor: int
    on_track: bool

    # Der Plan, der das Ziel erreichen soll - falls einer aktiv ist.
    active_scenario: ContractScenario | None = None


# ---------------------------------------------------------------------------
# Gegenpruefung durch das Sprachmodell (Abschnitt 19.11, P5.3)
# ---------------------------------------------------------------------------
#
# Die deutschen Feldnamen (`gehoert_zusammen`, `begruendung`, `geprueft`) sind
# Absicht und keine Stilabweichung: sie stehen so im Plan und benennen eine
# Aussage, die woertlich in der Oberflaeche erscheint. Ein englisches
# `belongs_together` waere hier eine zusaetzliche Uebersetzungsschicht zwischen
# Plan, API und Anzeige.


class ContractAICrosscheckRequest(BaseModel):
    """Optionale Einschraenkung des Laufs.

    Ohne `contract_ids` werden alle Paare des Haushalts betrachtet (bis zur
    Obergrenze des Dienstes). Mit `contract_ids` bleiben nur Paare uebrig, bei
    denen BEIDE Vertraege in der Liste stehen - das ist der Weg fuer einen
    Gegenpruef-Knopf an einer einzelnen Duplikatkarte.
    """

    contract_ids: list[UUID] | None = Field(default=None, max_length=24)


class ContractAICrosscheckPartner(BaseModel):
    """Ein Vertrag eines beurteilten Paares - nur zum Wiedererkennen.

    Bewusst schmal: die Oberflaeche hat die vollstaendigen Vertragszeilen
    bereits geladen, und ein zweiter, moeglicherweise aelterer Abzug derselben
    Felder waere eine Quelle fuer widerspruechliche Anzeigen.
    """

    id: UUID
    name: str
    amount_minor: int


class ContractAICrosscheckItem(BaseModel):
    """Ein Paar, zu dem das Modell eine verwertbare Aussage geliefert hat.

    `gehoert_zusammen` ist ein Vorschlag, kein Ergebnis: der Endpunkt schreibt
    nichts und fuehrt nichts zusammen (Leitsatz 8). Es gibt bewusst KEIN
    Konfidenz- oder Prozentfeld - eine unkalibrierte Zahl waere hier erfundene
    Praezision, und genau deshalb kennt schon das Antwortschema des Modells
    keins.
    """

    contract_a: ContractAICrosscheckPartner
    contract_b: ContractAICrosscheckPartner
    gehoert_zusammen: bool
    begruendung: str
    # Feste Herkunftsmarkierung, damit die Oberflaeche eine Modellaussage nie
    # neben einem deterministischen Beleg als gleichwertig darstellt.
    quelle: Literal["ai"] = "ai"


ContractAICrosscheckSkipReason = Literal[
    # Zwei vorhandene, verschiedene Vertragsschluessel (Leitsatz 4). Solche
    # Paare werden gar nicht erst gefragt.
    "different_contract_key",
    # Der Nutzer hat das Paar bereits verneint (contract_events kind='dismissed').
    "dismissed_by_user",
    # Gefragt, aber ohne verwertbare Antwort - gilt als NICHT beurteilt und
    # ausdruecklich nicht als Treffer.
    "no_model_answer",
]


class ContractAICrosscheckSkip(BaseModel):
    contract_a: ContractAICrosscheckPartner
    contract_b: ContractAICrosscheckPartner
    grund: ContractAICrosscheckSkipReason


class ContractAICrosscheckResponse(BaseModel):
    """Antwort von POST /v1/contracts/ai-crosscheck.

    Zaehlweise, damit die Oberflaeche nichts dazuerfinden muss:

    * `geprueft` ist die Zahl der Paare mit verwertbarem Urteil und damit immer
      gleich `len(items)`.
    * `uebersprungen` zaehlt die Paare ohne Urteil - von der deterministischen
      Vorfilterung ausgeschlossen ODER ohne brauchbare Modellantwort. Die
      Gruende dazu stehen einzeln in `uebersprungen_gruende`; stilles
      Weglassen gibt es nicht.
    * `truncated` meldet, dass es mehr Kandidatenpaare gab als ein Lauf
      beurteilt. Diese Paare stehen in keiner der beiden Zahlen - sie wurden
      nicht uebersprungen, sondern noch nicht betrachtet.

    `meldung` ist gesetzt, wenn ueberhaupt nichts beurteilt werden konnte
    (kein Modell erreichbar, KI abgeschaltet). Sie ist fuer Nutzer geschrieben
    und ersetzt einen Fehlerstatus - ein 500er waere fuer einen abgeschalteten
    optionalen Zusatz die falsche Antwort.
    """

    items: list[ContractAICrosscheckItem]
    geprueft: int
    uebersprungen: int
    uebersprungen_gruende: list[ContractAICrosscheckSkip] = []
    truncated: bool
    modell_name: str | None = None
    meldung: str | None = None


# ---------------------------------------------------------------------------
# Belege je Preisstufe, freie Buchungen, Neuberechnung (Abschnitt 19.7)
# ---------------------------------------------------------------------------
#
# Ausgangslage ist der Festnetzvertrag aus 19.1. Sein Betragsverlauf besteht aus
# zwei Zeilen, die beide am Tag der Datenuebernahme beginnen, keine Quellbuchung
# nennen und deshalb gar nichts belegen - waehrend drei Buchungen derselben
# Mandatsreferenz voellig unzugeordnet danebenliegen. Der Nutzer soll beides
# sehen koennen: welche Buchungen hinter einer Preisstufe stehen, und welche
# Buchungen ueberhaupt noch frei sind.


class ContractEvidenceTransaction(BaseModel):
    """Eine Buchung als Beleg - fuer Preisstufen wie fuer die freie Suche.

    `amount_minor` traegt das Originalvorzeichen der Buchung (Ausgaben negativ),
    damit die Anzeige eine Ausgabe nicht als Einnahme einfaerbt. Das ist
    dieselbe Festlegung wie in `contract_occurrences.amount_minor`.

    `purpose_excerpt` ist gekuerzt: die Liste dient dem Wiedererkennen, der
    vollstaendige Zweck steht in der Buchungsansicht.
    """

    transaction_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None
    purpose_excerpt: str | None
    account_id: UUID | None = None
    # Nur bei bereits zugeordneten Buchungen gesetzt (Faelligkeit am Vertrag).
    due_date: date | None = None
    # Diese Buchung steht in `contract_amount_history.source_transaction_id` der
    # Stufe, ist also der Beleg, der die Stufe eroeffnet hat.
    is_stage_source: bool = False


class ContractAmountStageEvidence(BaseModel):
    """Eine Preisstufe samt der Buchungen, die in ihrem Zeitraum liegen.

    `has_evidence=false` ist die fachlich wichtige Aussage dieses Modells: die
    Stufe behauptet einen Preis, ohne dass eine einzige Buchung sie stuetzt.
    Genau so sehen die beiden aus der Migration uebernommenen Zeilen eines
    Festnetzvertrags aus (`change_reason='initial'`, `source_transaction_id`
    leer, Zeitraum am Uebernahmetag). Ohne dieses Feld muesste der Nutzer aus
    einer leeren Liste raten, ob die Stufe unbelegt oder die Anzeige kaputt ist.
    """

    stage: ContractAmountEntry
    transactions: list[ContractEvidenceTransaction]
    transaction_count: int
    has_source_transaction: bool
    has_evidence: bool


class ContractAmountEvidenceResponse(BaseModel):
    """Antwort von GET /v1/contracts/{id}/amount-history/evidence.

    `transactions_outside_stages` ist kein Randfall, sondern der zweite Teil des
    Befunds: beim uebernommenen Vertrag liegen alle vier zugeordneten Buchungen
    ausserhalb beider Preisstufen, weil die Stufen erst am Uebernahmetag
    beginnen. Diese Liste macht sichtbar, dass der Verlauf und die Buchungen
    nicht zusammenpassen.
    """

    contract_id: UUID
    stages: list[ContractAmountStageEvidence]
    transactions_outside_stages: list[ContractEvidenceTransaction]
    stages_without_evidence: int
    assigned_transaction_count: int


class ContractFreeTransactionFilter(BaseModel):
    """Die tatsaechlich angewendete Suche - immer mit ausgeliefert.

    Die Vorbelegung stammt aus dem Vertrag (Anbieterwort, Betragsband, Zeitraum)
    und ist vollstaendig ueberschreibbar: der Nutzer sucht ja gerade deshalb von
    Hand, weil die Automatik nichts gefunden hat. Damit er sieht, wonach
    ueberhaupt gesucht wurde, steht das Ergebnis der Vorbelegung in der Antwort
    statt nur im Serverlog.
    """

    date_from: date
    date_to: date
    amount_min_minor: int | None
    amount_max_minor: int | None
    search: str | None
    # false = der Aufrufer hat die Vorbelegung ausdruecklich abgeschaltet.
    from_contract_defaults: bool


class ContractFreeTransactionsResponse(BaseModel):
    """Antwort von GET /v1/contracts/{id}/free-transactions.

    `total` ist die echte Trefferzahl der Suche (nicht die Seitenlaenge) und
    `truncated` meldet, dass hinter der aktuellen Seite noch etwas liegt -
    dieselbe Zusicherung wie bei `/duplicate-suggestions`, nur mit Blaetterung.
    """

    items: list[ContractEvidenceTransaction]
    total: int
    truncated: bool
    limit: int
    offset: int
    applied_filter: ContractFreeTransactionFilter


class ContractAmountStagePlan(BaseModel):
    """Eine geplante Preisstufe der Neuberechnung.

    Bewusst getrennt von `ContractAmountEntry`: dieses Modell beschreibt eine
    Stufe, die es noch nicht gibt (Vorschau) oder gerade entstanden ist, und
    nennt zusaetzlich, wie viele Buchungen sie tragen.
    """

    amount_minor: int
    valid_from: date
    valid_to: date | None
    delta_minor: int | None
    transaction_count: int
    source_transaction_id: UUID


class ContractAssignmentPreviewRequest(BaseModel):
    """Welche freien Buchungen der Nutzer dem Vertrag zuordnen will."""

    transaction_ids: list[UUID] = Field(min_length=1, max_length=200)


class ContractAssignmentSource(BaseModel):
    """Woher die Buchungen kommen. `contract_id=None` heisst: bisher frei."""

    contract_id: UUID | None
    contract_name: str | None
    transaction_count: int


class ContractAssignmentPreview(BaseModel):
    """Antwort von POST /v1/contracts/{id}/assignment-preview.

    Deckt die woertliche Forderung aus 19.7 ab - "Quelle, Ziel, betroffene
    Buchungen und die Auswirkung auf Zeitraeume/Preisverlauf" - und zwar fuer
    mehrere Buchungen zugleich: die drei fehlenden Buchungen sind ein
    Vorgang, nicht drei.

    Es wird nichts geschrieben. Der Schreibweg bleibt der vorhandene
    `POST /v1/contracts/transactions/{id}/reassign` je Buchung, gefolgt von
    `POST /v1/contracts/{id}/amount-history/recalculate`.
    """

    contract_id: UUID
    contract_name: str
    transactions: list[ContractEvidenceTransaction]
    sources: list[ContractAssignmentSource]
    # Ids, die es im Haushalt nicht (mehr) gibt - stilles Weglassen waere hier
    # der schlechtere Weg, weil der Nutzer sie gerade angehakt hat.
    unknown_transaction_ids: list[UUID]

    first_seen_date_before: date | None
    first_seen_date_after: date | None
    last_seen_date_before: date | None
    last_seen_date_after: date | None

    amount_stages_before: list[ContractAmountEntry]
    amount_stages_after: list[ContractAmountStagePlan]
    # Anzahl der Stufen ohne jeden Beleg vor bzw. nach dem Vorgang.
    stages_without_evidence_before: int


class ContractAmountRecalculationResult(BaseModel):
    """Antwort von POST /v1/contracts/{id}/amount-history/recalculate."""

    contract_id: UUID
    changed: bool
    stages_written: int
    stages_removed: int
    manual_stages_kept: int
    amount_stages: list[ContractAmountEntry]
    first_seen_date: date | None
    last_seen_date: date | None
    # Betrag der juengsten hergeleiteten Stufe und der aktuelle Sollbetrag des
    # Vertrags. Sie koennen auseinanderlaufen; die Neuberechnung fasst
    # `contracts.amount_minor` ausdruecklich nicht an (siehe Docstring des
    # Dienstes), damit die Fixkostensumme sich nicht unbemerkt verschiebt.
    resulting_amount_minor: int | None
    contract_amount_minor: int


# ---------------------------------------------------------------------------
# Sprachmodell: freie Buchungen zu EINEM Vertrag (Abschnitt 19.11)
# ---------------------------------------------------------------------------
#
# Zweiter Modus derselben Disziplin wie die Paar-Gegenpruefung: harte
# Vorfilterung und Deckelung vor dem Modell, keine Prozentzahl, kein
# Schreibvorgang. Deutsche Feldnamen aus demselben Grund wie oben.


class ContractAITransactionCheckRequest(BaseModel):
    """Optionale Einschraenkung auf bestimmte Buchungen.

    Ohne `transaction_ids` beurteilt der Lauf die aus dem Vertrag abgeleitete
    Vorauswahl freier Buchungen - dieselbe wie `GET .../free-transactions`. Mit
    `transaction_ids` bleibt der Schnitt aus dieser Vorauswahl und der Liste
    uebrig: die deterministische Vorfilterung laesst sich einschraenken, aber
    nicht umgehen.
    """

    transaction_ids: list[UUID] | None = Field(default=None, max_length=50)


class ContractAITransactionItem(BaseModel):
    """Eine beurteilte Buchung. Vorschlag, kein Ergebnis: der Endpunkt ordnet
    nichts zu (Leitsatz 8). Kein Konfidenz- oder Prozentfeld."""

    transaction_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None
    gehoert_zum_vertrag: bool
    begruendung: str
    quelle: Literal["ai"] = "ai"


ContractAITransactionSkipReason = Literal[
    # Ausdruecklich angefragt, aber nicht in der deterministischen Vorauswahl:
    # bereits einem Vertrag zugeordnet, vom Nutzer geloest (Leitsatz 5), nicht
    # auffindbar oder ausserhalb von Zeitraum, Betragsband und Anbieterwort.
    "not_free",
    # Gefragt, aber ohne verwertbare Antwort - gilt als NICHT beurteilt und
    # ausdruecklich nicht als Treffer.
    "no_model_answer",
]


class ContractAITransactionSkip(BaseModel):
    transaction_id: UUID
    booked_date: date | None
    amount_minor: int | None
    grund: ContractAITransactionSkipReason


class ContractAITransactionCheckResponse(BaseModel):
    """Antwort von POST /v1/contracts/{id}/ai-transaction-check.

    Zaehlweise wie bei der Paar-Gegenpruefung: `geprueft` ist die Zahl der
    Buchungen mit verwertbarem Urteil, `uebersprungen` die der Buchungen ohne -
    entweder deterministisch ausgeschlossen oder ohne brauchbare Antwort.
    `truncated` meldet, dass es mehr Kandidaten gab, als ein Lauf beurteilt.
    """

    contract_id: UUID
    items: list[ContractAITransactionItem]
    geprueft: int
    uebersprungen: int
    uebersprungen_gruende: list[ContractAITransactionSkip] = []
    truncated: bool
    # Wie viele freie Buchungen die deterministische Vorauswahl gefunden hat,
    # und wonach sie gesucht hat. Beides gehoert in die Antwort: die Aussage des
    # Modells ist nur so viel wert wie die Liste, die es zu sehen bekam.
    kandidaten_gesamt: int
    vorauswahl: ContractFreeTransactionFilter
    modell_name: str | None = None
    meldung: str | None = None
