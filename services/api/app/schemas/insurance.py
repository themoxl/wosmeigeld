"""API-Vertraege des Versicherungsmoduls (docs/VERSICHERUNGEN-PLAN.md, P1).

Zwei Dinge unterscheiden diese Modelle von denen der Nachbarmodule:

1. **Kein Geld.** Die Police traegt hoechstens einen Informationsbetrag eines
   fremden Zahlers. Ein selbst bezahlter Beitrag gehoert an einen Vertrag -
   dafuer gibt es hier bewusst kein Feld, nicht einmal ein optionales.
2. **Jedes Datum traegt seine Herkunft.** `main_due_source` und
   `notice_terms_source` sind Ausgabefelder; ein Client kann sie weder beim
   Anlegen noch beim Aendern setzen. Die Bestaetigung laeuft ueber eigene
   Einfeldaktionen, nicht ueber das allgemeine PATCH.
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RiskField = Literal[
    "health", "income", "liability", "property", "mobility", "retirement", "legal", "other"
]
NoticeAnchor = Literal[
    "term_end", "main_due", "period_end", "fixed_date", "month_end", "per_event", "none"
]
AnchorBasisKind = Literal["statute", "policy_terms", "contract"]
DeadlineKind = Literal[
    "kuendigungsfrist", "widerrufsfrist", "anfechtungsfrist", "klagefrist", "zahlungsfrist"
]
PolicyStatus = Literal[
    "draft",
    "applied",
    "active",
    "suspended",
    "paid_up",
    "dormant",
    "cancelled",
    "ended",
    "entered_in_error",
]
PremiumPayer = Literal["employer", "self_payroll", "partner", "other"]
Frequency = Literal["weekly", "monthly", "quarterly", "semiannual", "yearly", "single"]
ServiceChannel = Literal["post", "einschreiben", "email", "kuendigungsbutton", "fax"]
DateSource = Literal["user_confirmed", "catalog_suggested", "not_computable"]
NoticeTermsSource = Literal["user_confirmed", "catalog_suggested", "derived_from_days"]
Certainty = Literal["confirmed", "assumed", "not_computable"]
CancellationReason = Literal[
    "term_end",
    "premium_increase",
    "claim",
    "risk_ceased",
    "switch",
    "double_insured",
    "no_longer_needed",
    "insurer",
    "vehicle_sold",
    "vehicle_deregistered",
    "other",
]


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------


class InsuranceLine(BaseModel):
    """Eine Sparte. Traegt den Zwecktext, der die Ausgangsfrage beantwortet."""

    line_key: str
    risk_field: RiskField
    gdv_sparte: str | None
    label: str
    purpose_short: str
    purpose_long: str
    typically_covers: list[str]
    typically_not_covers: list[str]
    is_placeholder: bool
    need_tier: str | None
    need_reason: str | None
    source_url: str | None
    source_checked_on: date | None
    default_notice_anchor: NoticeAnchor
    default_anchor_basis_kind: AnchorBasisKind
    default_notice_period_months: int | None
    default_main_due_md: str | None
    default_min_term_months: int | None
    default_deadline_kind: DeadlineKind
    special_law: str
    completeness_fields: list[str]
    covered_by_keys: list[str]
    requires_object_kind: str | None
    sort_order: int


class InsuranceCoverageKey(BaseModel):
    coverage_key: str
    label: str
    purpose_short: str
    applies_to: list[str]
    sort_order: int


# ---------------------------------------------------------------------------
# Police
# ---------------------------------------------------------------------------


class PolicyPremium(BaseModel):
    """Der Geldfall einer Police, als Ausgabeform.

    `kind` macht die drei Faelle aus dem Plan in der Oberflaeche unterscheidbar,
    ohne dass sie aus dem Vorhandensein von Feldern erraten werden muessen.
    """

    kind: Literal["contract", "external", "none"]
    contract_id: UUID | None = None
    contract_name: str | None = None
    contract_amount_minor: int | None = None
    contract_frequency: str | None = None
    external_premium_minor: int | None = None
    external_frequency: Frequency | None = None
    premium_payer: PremiumPayer | None = None
    premium_payer_note: str | None = None
    premium_share_bp: int = 10000
    # Jahreswert NUR fuer den Fall (a). Fremdbetraege bleiben ausdruecklich
    # draussen - sie sind kein Teil der Fixkosten.
    yearly_in_fixed_costs_minor: int | None = None


class PolicyDeadline(BaseModel):
    """Die Frist mit ihren drei Daten und ihrer Sicherheit."""

    anchor: NoticeAnchor
    anchor_basis_kind: AnchorBasisKind
    deadline_kind: DeadlineKind
    certainty: Certainty
    term_ends_on: date | None = None
    deadline_legal_on: date | None = None
    safe_service_on: date | None = None
    recommended_action_on: date | None = None
    days_left: int | None = None
    reason: str = ""
    # Der Feiertag, der den sicheren Zustelltag verschoben hat - benannt, weil
    # die Oberflaeche ihn sonst selbst ermitteln muesste und ein zweiter
    # Feiertagskalender im Browser frueher oder spaeter einen zweiten Termin
    # ergaebe. Nur Feiertage, kein Wochenende: "Hier verschiebt ihn Samstag"
    # waere keine Begruendung, sondern eine Selbstverstaendlichkeit.
    safe_service_holiday: str | None = None


class InsurancePolicy(BaseModel):
    id: UUID
    line_key: str
    line_label: str
    line_purpose_short: str
    risk_field: RiskField
    label: str | None
    insurer_name: str
    policy_number: str | None
    customer_number: str | None
    tariff_name: str | None
    purpose_note: str | None
    status: PolicyStatus
    starts_on: date | None
    ends_on: date | None
    main_due_on: date | None
    # Herkunft, serverseitig gesetzt. Nur `user_confirmed` erzeugt Countdown
    # und Push; `catalog_suggested` erzeugt eine Rueckfrage.
    main_due_source: DateSource
    main_due_confirmed_at: datetime | None
    notice_anchor: NoticeAnchor
    anchor_basis_kind: AnchorBasisKind
    notice_period_months: int | None
    notice_terms_source: NoticeTermsSource
    deadline_kind: DeadlineKind
    min_term_months: int | None
    renewal_term_months: int | None
    waiting_period_days: int | None
    sum_insured_minor: int | None
    deductible_minor: int | None
    broker_name: str | None
    broker_phone: str | None
    claims_phone: str | None
    service_channel: ServiceChannel
    recipient_state: str | None
    cancelled_on: date | None
    cancellation_reason: CancellationReason | None
    dormant_until: date | None
    paid_up_benefit_minor: int | None
    dynamic_agreed: bool
    attrs: dict[str, Any]
    data_source: str
    line_confirmed_at: datetime | None
    line_deferred_at: datetime | None
    note: str | None
    premium: PolicyPremium
    deadline: PolicyDeadline
    # Anteil der ausgefuellten Pflichtfelder dieser Sparte, als Bruch. Der
    # Nenner kommt aus insurance_lines.completeness_fields - ohne ihn waere
    # "7 von 12" eine erfundene Zahl.
    completeness_filled: int
    completeness_total: int
    # Die eine konkrete naechste Aufgabe, oder None. Bewusst ein Satz statt
    # eines abstrakten Status: "Hauptfaelligkeit bestaetigen" sagt, was zu tun
    # ist, "unvollstaendig" nicht.
    next_task: str | None
    # Buendel (P4, Plan 3.9): die Wurzelpolice dieser Kindpolice. Der Wert kommt
    # aus der Zeile und ist deshalb keine Behauptung - ohne ihn waere eine
    # einmal gesetzte Buendelzuordnung nach dem Neuladen unsichtbar, und die
    # geteilte Scheinnummer staende ohne erkennbaren Grund zweimal da.
    parent_policy_id: UUID | None = None
    # Serverseitig aus insurance_lines.risk_field gesetzt; entscheidet ueber den
    # Personen-Gesundheitsschutz. Steht hier, damit die Oberflaeche den Hinweis
    # an der Tatsache festmacht und nicht an einer eigenen Herleitung.
    is_health_scoped: bool = False
    version: int
    created_at: datetime
    updated_at: datetime


class ExternalPremiumInput(BaseModel):
    """Fall (b): ein Dritter zahlt. Betrag, Rhythmus und Zahler gehoeren zusammen."""

    external_premium_minor: int = Field(gt=0)
    external_frequency: Frequency
    premium_payer: PremiumPayer
    premium_payer_note: str | None = Field(default=None, max_length=200)


class InsurancePolicyCreate(BaseModel):
    line_key: str = "unknown"
    insurer_name: str = Field(min_length=1, max_length=160)
    label: str | None = Field(default=None, max_length=120)
    policy_number: str | None = Field(default=None, max_length=80)
    customer_number: str | None = None
    tariff_name: str | None = None
    purpose_note: str | None = Field(default=None, max_length=2000)
    status: PolicyStatus = "active"
    starts_on: date | None = None
    ends_on: date | None = None
    # KEIN main_due_source im Eingabemodell: die Herkunft ist Serverwissen.
    # Ein hier angelegtes Datum ist immer eine Annahme, bis es ueber
    # /confirm-main-due bestaetigt wird.
    main_due_on: date | None = None
    notice_anchor: NoticeAnchor | None = None
    notice_period_months: int | None = Field(default=None, ge=0, le=36)
    min_term_months: int | None = Field(default=None, ge=1, le=600)
    renewal_term_months: int | None = Field(default=None, ge=1, le=120)
    waiting_period_days: int | None = Field(default=None, ge=0, le=3650)
    sum_insured_minor: int | None = Field(default=None, ge=0)
    deductible_minor: int | None = Field(default=None, ge=0)
    broker_name: str | None = None
    broker_phone: str | None = None
    claims_phone: str | None = None
    service_channel: ServiceChannel = "post"
    recipient_state: str | None = None
    dynamic_agreed: bool = False
    attrs: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    # Genau einer der drei Geldfaelle.
    contract_id: UUID | None = None
    external: ExternalPremiumInput | None = None

    @model_validator(mode="after")
    def genau_ein_geldfall(self) -> "InsurancePolicyCreate":
        if self.contract_id is not None and self.external is not None:
            raise ValueError(
                "Entweder ein Vertrag (der Haushalt zahlt) oder ein Fremdbetrag - nie beides."
            )
        return self


class InsurancePolicyUpdate(BaseModel):
    """Alles, was per PATCH aenderbar ist.

    Ausdruecklich NICHT dabei: `main_due_source`, `notice_terms_source`,
    `is_health_scoped`, `version`. Die Herkunft eines Datums laesst sich nicht
    per PATCH hochstufen - dafuer gibt es die Bestaetigungsaktionen.
    """

    line_key: str | None = None
    insurer_name: str | None = Field(default=None, min_length=1, max_length=160)
    label: str | None = Field(default=None, max_length=120)
    policy_number: str | None = Field(default=None, max_length=80)
    customer_number: str | None = None
    tariff_name: str | None = None
    purpose_note: str | None = Field(default=None, max_length=2000)
    starts_on: date | None = None
    ends_on: date | None = None
    main_due_on: date | None = None
    notice_anchor: NoticeAnchor | None = None
    notice_period_months: int | None = Field(default=None, ge=0, le=36)
    min_term_months: int | None = Field(default=None, ge=1, le=600)
    renewal_term_months: int | None = Field(default=None, ge=1, le=120)
    waiting_period_days: int | None = Field(default=None, ge=0, le=3650)
    sum_insured_minor: int | None = Field(default=None, ge=0)
    deductible_minor: int | None = Field(default=None, ge=0)
    broker_name: str | None = None
    broker_phone: str | None = None
    claims_phone: str | None = None
    service_channel: ServiceChannel | None = None
    recipient_state: str | None = None
    dynamic_agreed: bool | None = None
    attrs: dict[str, Any] | None = None
    note: str | None = None
    clear_fields: list[
        Literal[
            "label",
            "policy_number",
            "customer_number",
            "tariff_name",
            "purpose_note",
            "starts_on",
            "ends_on",
            "main_due_on",
            "notice_period_months",
            "min_term_months",
            "renewal_term_months",
            "waiting_period_days",
            "sum_insured_minor",
            "deductible_minor",
            "broker_name",
            "broker_phone",
            "claims_phone",
            "recipient_state",
            "note",
        ]
    ] = Field(default_factory=list)


class ConfirmMainDue(BaseModel):
    """Einfeldaktion, kein Formular.

    Sie ist der EINZIGE Weg, aus einer Annahme eine bestaetigte Angabe zu
    machen. Deshalb eigener Endpunkt statt eines Feldes im PATCH: eine
    Bestaetigung ist eine Handlung, keine Feldaenderung.
    """

    main_due_on: date
    notice_anchor: NoticeAnchor | None = None


class ConfirmNoticeTerms(BaseModel):
    notice_period_months: int = Field(ge=0, le=36)
    notice_anchor: NoticeAnchor | None = None


class ConfirmLine(BaseModel):
    line_key: str | None = None
    # "Sparte nicht in der Liste": nimmt die Police aus der Aufgabenliste,
    # ohne sie falsch zuzuordnen.
    defer: bool = False


class LinkContract(BaseModel):
    contract_id: UUID
    premium_share_bp: int = Field(default=10000, ge=1, le=10000)


class PolicyStatusChange(BaseModel):
    status: PolicyStatus
    cancelled_on: date | None = None
    cancellation_reason: CancellationReason | None = None
    dormant_until: date | None = None
    paid_up_benefit_minor: int | None = Field(default=None, ge=0)


class PolicyPurpose(BaseModel):
    """Der Zwecktext samt seiner Herkunft und dem Vorbehalt.

    `caveat` steht bewusst im Vertrag und nicht nur in der Oberflaeche: der
    Satz gehoert zum Text, nicht zur Gestaltung.
    """

    purpose_long: str
    typically_covers: list[str]
    typically_not_covers: list[str]
    coverage_source: Literal["catalog", "policy"]
    purpose_note: str | None
    source_url: str | None
    source_checked_on: date | None
    caveat: str = "Typischer Zweck dieser Sparte. Ihre konkrete Police kann abweichen."


class ContractCandidate(BaseModel):
    contract_id: UUID
    name: str
    provider_label: str | None
    amount_minor: int
    frequency: str
    next_due_date: date
    score: float
    evidence: list[str]


class InsuranceTask(BaseModel):
    """Eine Zeile aus "Jetzt wichtig".

    `what` sagt, was zu tun ist; `why` sagt, warum es ohne den Nutzer nicht
    weitergeht. Ein abstrakter Status stuende hier nutzlos herum.
    """

    kind: Literal[
        "missing_main_due",
        "unconfirmed_main_due",
        "unknown_line",
        "missing_premium_source",
        "deadline_approaching",
    ]
    policy_id: UUID
    policy_label: str
    what: str
    why: str
    due_on: date | None = None
    days_left: int | None = None


class InsuranceSummary(BaseModel):
    policy_count: int
    active_count: int
    # Nur Fall (a). Fremdbetraege stehen getrennt, damit sie nie in die
    # Fixkostenquote wandern.
    yearly_in_fixed_costs_minor: int
    yearly_paid_by_others_minor: int
    paid_by_others_breakdown: dict[str, int]
    no_premium_count: int
    unknown_line_count: int
    deferred_line_count: int
    missing_main_due_count: int
    unconfirmed_main_due_count: int
    next_deadline_on: date | None
    next_deadline_policy_id: UUID | None


class BackfillPreviewItem(BaseModel):
    contract_id: UUID
    contract_name: str
    provider_label: str | None
    amount_minor: int
    frequency: str
    suggested_line_key: str
    suggested_line_label: str
    reason: str


class BackfillPreview(BaseModel):
    """Was die Uebernahme anlegen wuerde - vor dem Anlegen.

    Der Plan verlangt eine Vorschau statt eines stillen Laufs: die Uebernahme
    beruehrt den Bestand, und der Nutzer soll vorher sehen, was entsteht.
    """

    items: list[BackfillPreviewItem]
    already_linked_count: int


class BackfillResult(BaseModel):
    created: int
    skipped: int
    policy_ids: list[UUID]


class InsuranceSettings(BaseModel):
    """Benachrichtigungseinstellungen des Haushalts.

    Die Staffeln sind Tage VOR dem empfohlenen Handlungsdatum, absteigend
    gelesen: `[60, 14, 3]` heisst "erinnere 60, dann 14, dann 3 Tage vorher".
    Der naechtliche Push liest genau diese Spalte; ohne diesen Endpunkt stand
    sie auf ihrer Voreinstellung und war fuer den Nutzer nicht erreichbar.

    Obergrenze 365: eine Staffel jenseits eines Jahres erinnerte an eine Frist,
    die im naechsten Versicherungsjahr liegt. Hoechstens fuenf Stufen, weil
    jede Stufe genau einmal feuert und eine sechste Erinnerung an dieselbe
    Frist nicht mehr informiert, sondern abstumpft.
    """

    notify_deadline: bool = True
    notify_premium_notice: bool = True
    notify_claim_followup: bool = True
    lead_days: list[int] = Field(default_factory=lambda: [60, 14, 3], max_length=5)
    lead_days_special: list[int] = Field(default_factory=lambda: [0, 7, 2], max_length=5)
    review_interval_months: int = Field(default=24, ge=6, le=60)

    @model_validator(mode="after")
    def _check_lead_days(self) -> "InsuranceSettings":
        for name, staffel in (
            ("lead_days", self.lead_days),
            ("lead_days_special", self.lead_days_special),
        ):
            if any(day < 0 or day > 365 for day in staffel):
                raise ValueError(f"{name}: nur Werte zwischen 0 und 365 Tagen")
            if len(set(staffel)) != len(staffel):
                # Eine doppelte Stufe kann nicht zweimal feuern - `is_new_stage`
                # laesst nur die engste erreichte Stufe durch. Sie waere also
                # eine Angabe ohne Wirkung, und die Oberflaeche zeigte eine
                # Erinnerung an, die es nie gibt.
                raise ValueError(f"{name}: jede Stufe darf nur einmal vorkommen")
        return self


# ---------------------------------------------------------------------------
# Beitragsanpassungen (P3)
# ---------------------------------------------------------------------------

# Teilmenge von `Frequency`: 'weekly' und 'single' fehlen bewusst. Ein
# Anpassungsschreiben setzt eine WIEDERKEHRENDE Praemie neu fest; ein
# Einmalbeitrag wird nicht angepasst, eine woechentliche Versicherungspraemie
# kommt im Spartenkatalog nicht vor.
PremiumNoticeFrequency = Literal["monthly", "quarterly", "semiannual", "yearly"]
PremiumNoticeReason = Literal[
    "claims_cost",
    "risk_change",
    "demographics",
    "index",
    "tariff_class",
    "regional_class",
    "sf_class",
    "age_bracket",
    "other",
]
# 'none' ist ein vollwertiges Ergebnis, kein fehlender Wert: es heisst "diese
# Anpassung begruendet kein Sonderkuendigungsrecht" und traegt seinen Grund im
# Feld `reason` mit.
SpecialTerminationState = Literal["none", "open", "expired"]
PremiumStepSource = Literal["contract", "premium_notice", "external"]


class SpecialTerminationWindow(BaseModel):
    """Das Sonderkuendigungsfenster nach Paragraph 40 VVG.

    Drei Daten statt eines, aus demselben Grund wie bei der ordentlichen Frist:
    `deadline_legal_on` ist das rechtliche Fristende, `safe_service_on` der
    letzte Tag mit zu erwartendem Zugang, `recommended_action_on` der Tag, an
    dem abgeschickt werden muss. `earliest_effect_on` ist das vierte und
    fachlich andersartige Datum - nicht wann gekuendigt werden muss, sondern
    wann die Kuendigung fruehestens wirkt (Paragraph 40 Abs. 1 S. 1 VVG:
    "fruehestens jedoch zum Zeitpunkt des Wirksamwerdens der Erhoehung").

    `days_left` zaehlt auf das Handlungsdatum und steht nur bei `state = 'open'`.
    """

    state: SpecialTerminationState
    deadline_kind: DeadlineKind = "kuendigungsfrist"
    deadline_legal_on: date | None = None
    safe_service_on: date | None = None
    recommended_action_on: date | None = None
    earliest_effect_on: date | None = None
    days_left: int | None = None
    # Der Nachweis, aus dem das Fenster folgt - oder der Grund, warum keines
    # entsteht. Die Oberflaeche entscheidet nie am Freitext, sondern an `state`
    # und `basis_kind`; `basis` ist der Beleg, nicht die Steuerung.
    basis: str = ""
    basis_kind: AnchorBasisKind = "statute"


class PremiumNotice(BaseModel):
    """Eine erfasste Beitragsanpassung.

    Die Betraege sind der INHALT DES ANSCHREIBENS, nicht der Beitrag. Der
    Beitrag entsteht erst mit `confirm` und ausschliesslich ueber die
    Vertragsseite; bis dahin bewegt diese Zeile kein Geld.
    """

    id: UUID
    policy_id: UUID
    received_on: date
    effective_on: date
    old_amount_minor: int
    new_amount_minor: int
    comparison_amount_minor: int | None
    frequency: PremiumNoticeFrequency
    with_benefit_increase: bool
    reason: PremiumNoticeReason | None
    # Abgeleitet, damit Liste und Steigerungstabelle nicht je Zeile rechnen -
    # und damit alle Clients dieselbe Zahl zeigen. `delta_bp` in Basispunkten,
    # bezogen auf `old_amount_minor`.
    delta_minor: int
    delta_bp: int
    yearly_old_minor: int
    yearly_new_minor: int
    special_termination: SpecialTerminationWindow
    amount_history_id: UUID | None
    confirmed_at: datetime | None
    acknowledged_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class PremiumNoticeCreate(BaseModel):
    """Was auf dem Anschreiben steht.

    `received_on` ist bewusst optional deklariert und nicht ueber Pydantic
    erzwungen: der Plan sieht fuer den fehlenden Zugang einen eigenen
    Fehlercode vor (`insurance_notice_missing_received_on`), weil "ohne
    Zugangsdatum entsteht keine Frist, sondern eine Rueckfrage" eine fachliche
    Antwort ist und keine Schemaverletzung. Der Dienst weist sie mit genau
    diesem Code ab.
    """

    received_on: date | None = None
    effective_on: date
    old_amount_minor: int = Field(gt=0)
    new_amount_minor: int = Field(gt=0)
    comparison_amount_minor: int | None = Field(default=None, gt=0)
    frequency: PremiumNoticeFrequency
    with_benefit_increase: bool = False
    reason: PremiumNoticeReason | None = None

    @model_validator(mode="after")
    def _dates_in_order(self) -> "PremiumNoticeCreate":
        if self.received_on is not None and self.effective_on < self.received_on:
            raise ValueError("Ein Schreiben kann nicht vor seinem Zugang wirksam werden.")
        return self


class PremiumNoticeUpdate(BaseModel):
    """Korrektur einer erfassten Mitteilung.

    Kein `clear_fields` fuer `received_on`: das Zugangsdatum ist der Fristanker,
    und eine Mitteilung ohne ihn ist keine Mitteilung mehr, sondern eine
    Rueckfrage. Wer sie loswerden will, nimmt sie mit DELETE zurueck.
    """

    received_on: date | None = None
    effective_on: date | None = None
    old_amount_minor: int | None = Field(default=None, gt=0)
    new_amount_minor: int | None = Field(default=None, gt=0)
    comparison_amount_minor: int | None = Field(default=None, gt=0)
    frequency: PremiumNoticeFrequency | None = None
    with_benefit_increase: bool | None = None
    reason: PremiumNoticeReason | None = None
    clear_fields: list[Literal["comparison_amount_minor", "reason"]] = Field(
        default_factory=list
    )


class PremiumNoticeConfirm(BaseModel):
    """Uebernahme der Anpassung.

    `valid_from` ist bewusst NICHT ueberschreibbar: die Preisstufe beginnt mit
    dem Wirksamwerden der Erhoehung, und ein abweichendes Datum waere eine
    zweite Wahrheit neben `effective_on`.
    """

    # Der neue Betrag kann von `new_amount_minor` abweichen, wenn der Nutzer
    # beim Uebernehmen einen Tippfehler bemerkt. Ohne diese Moeglichkeit muesste
    # er die Mitteilung erst korrigieren, um sie dann zu bestaetigen - zwei
    # Schritte fuer eine Zahl.
    amount_minor: int | None = Field(default=None, gt=0)


class PremiumNoticeConfirmResult(BaseModel):
    """Wohin der Betrag geschrieben wurde - und dass er geschrieben wurde.

    `target` macht die beiden Faelle unterscheidbar, ohne sie aus dem
    Vorhandensein von Feldern zu erraten: `contract` ist der Regelfall (der
    Haushalt zahlt, die Preistreppe fuehrt den Betrag), `external` der
    Informationsbetrag eines fremden Zahlers, der weder Preistreppe noch
    Fixkosten beruehrt.
    """

    notice: PremiumNotice
    target: Literal["contract", "external"]
    contract_id: UUID | None = None
    amount_history_id: UUID | None = None
    valid_from: date
    amount_minor: int
    hint: str = ""


class PremiumHistoryStep(BaseModel):
    """Eine Stufe der Beitragstreppe.

    `yearly_minor` steht neben `amount_minor`, weil ein Wechsel der Zahlweise
    sonst wie eine Beitragssenkung aussaehe: 120 Euro jaehrlich und 12 Euro
    monatlich sind dieselbe Belastung, und nur der Jahreswert zeigt das.
    `valid_from` darf fehlen - "seit Beginn, Datum unbekannt" ist eine Aussage,
    kein erfundenes Datum.
    """

    policy_id: UUID
    insurer_name: str
    valid_from: date | None
    valid_to: date | None
    amount_minor: int
    frequency: str
    yearly_minor: int
    yearly_delta_minor: int | None
    source: PremiumStepSource
    change_reason: str | None
    notice_id: UUID | None = None
    received_on: date | None = None
    with_benefit_increase: bool | None = None
    notice_reason: PremiumNoticeReason | None = None
    # Erste Stufe nach einem Gesellschaftswechsel. Ohne diese Marke saehe der
    # Sprung beim Wechsel wie eine Beitragsanpassung aus.
    is_switch_point: bool = False


class PremiumHistory(BaseModel):
    """Der Beitragsverlauf einer Police als Treppe.

    `policy_ids` ist die zusammengefuehrte Kette aus Vorgaenger- und
    Nachfolgepolicen, aelteste zuerst - beim Gesellschaftswechsel bricht die
    Zeitreihe damit nicht ab, sondern zeigt den Wechselpunkt.
    """

    policy_ids: list[UUID]
    steps: list[PremiumHistoryStep]
    yearly_first_minor: int | None = None
    yearly_current_minor: int | None = None
    # Gesamtsteigerung ueber den gezeigten Zeitraum, in Basispunkten.
    total_increase_bp: int | None = None
    # Erfasste, noch nicht uebernommene Mitteilungen. Sie stehen NICHT als Stufe
    # in der Treppe: ein Beitrag, den niemand bestaetigt hat, ist kein Beitrag.
    pending_notice_ids: list[UUID] = Field(default_factory=list)


class PolicyReplaceRequest(BaseModel):
    """Gesellschaftswechsel mit Historienmitnahme (Plan 4.15).

    Entweder eine bestehende Police wird als Nachfolgerin verknuepft, oder eine
    neue wird in derselben Transaktion angelegt - nie beides.
    """

    new_policy_id: UUID | None = None
    new_policy: InsurancePolicyCreate | None = None
    # Letzter Tag der alten Police. Pflicht: ohne ihn ueberlappen Vorgaenger und
    # Nachfolger unbegrenzt, und die Treppe haette zwei gleichzeitig gueltige
    # Stufen.
    ends_on: date
    # Mitgenommene Vorversicherungszeit. Fehlt sie, uebernimmt der Dienst die
    # der alten Police, hilfsweise deren Beginn - eine Wartezeit, die der Nutzer
    # bereits abgesessen hat, soll nicht neu anfangen.
    prior_cover_since: date | None = None

    @model_validator(mode="after")
    def _genau_eine_nachfolgerin(self) -> "PolicyReplaceRequest":
        if (self.new_policy_id is None) == (self.new_policy is None):
            raise ValueError(
                "Entweder eine bestehende Police verknuepfen oder eine neue anlegen - nie beides."
            )
        return self


class PolicyReplacement(BaseModel):
    """Beide Policen nach dem Wechsel, plus der offene Punkt.

    `hint` benennt den Vertrag der alten Police, wenn er weiterlaeuft: die
    Statuskopplung bleibt eine eigene, bestaetigte Handlung
    (`POST /policies/{id}/status`), damit der Wechsel nicht still die Fixkosten
    aendert.
    """

    predecessor: InsurancePolicy
    successor: InsurancePolicy
    hint: str = ""


# ---------------------------------------------------------------------------
# Beteiligte, Objekte, Deckungsbausteine (P4)
# ---------------------------------------------------------------------------

# Rollen aus GDV Anlage 7, reduziert auf das, was privat vorkommt - und
# englisch wie jedes andere Enum dieses Moduls. Die deutschen Etiketten
# (Versicherungsnehmer, versicherte Person, mitversichert, bezugsberechtigt)
# sind Sache der Oberflaeche; 'insured' traegt die mitversicherte Person mit,
# weil der Unterschied allein die Frage ist, wer den Vertrag geschlossen hat -
# und die beantwortet 'policyholder'.
PolicyPersonRole = Literal[
    "policyholder", "insured", "premium_payer", "beneficiary", "driver", "vehicle_keeper"
]
InsuredObjectKind = Literal["vehicle", "property", "pet", "device", "bike", "other"]


class PolicyPerson(BaseModel):
    """Eine beteiligte Person an einer Police.

    `display_name` kommt aus `insured_person_label()` und ist deshalb `None`,
    wenn die Person nicht mehr zum Haushalt gehoert. Die Zeile selbst bleibt
    stehen - eine Zuordnung ohne Namen ist eine Auskunft, ein stilles
    Verschwinden waere keine.
    """

    id: UUID
    policy_id: UUID
    insured_person_id: UUID
    display_name: str | None
    role: PolicyPersonRole
    # Doppelt belegt, je nach Rolle: Beihilfebemessungssatz bei 'insured'
    # (7000 = 70 %), Bezugsanteil bei 'beneficiary'.
    share_bp: int | None
    valid_from: date | None
    valid_to: date | None
    note: str | None
    created_at: datetime


class NewInsuredPerson(BaseModel):
    """Anlage einer Person im selben Aufruf.

    Sie entsteht ueber `reimbursement_service.create_person` und damit im
    Gesundheitszweig - es gibt keine zweite Personenliste und keinen zweiten
    Schreibweg zu ihr.
    """

    display_name: str = Field(min_length=1, max_length=120)
    birth_date: date | None = None


class PolicyPersonCreate(BaseModel):
    insured_person_id: UUID | None = None
    new_person: NewInsuredPerson | None = None
    role: PolicyPersonRole = "insured"
    share_bp: int | None = Field(default=None, ge=0, le=10000)
    valid_from: date | None = None
    valid_to: date | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _genau_eine_person(self) -> "PolicyPersonCreate":
        if (self.insured_person_id is None) == (self.new_person is None):
            raise ValueError(
                "Entweder eine bestehende Person waehlen oder eine neue anlegen - nie beides."
            )
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("Das Ende einer Zuordnung liegt nicht vor ihrem Beginn.")
        return self


class PolicyCoverageInput(BaseModel):
    """Ein Baustein, wie ihn der Nutzer erfasst.

    `is_included = false` ist eine vollwertige Aussage und kein fehlender Wert:
    "Elementar ist ausdruecklich nicht eingeschlossen" beantwortet die Frage des
    Bedarfsabgleichs, eine fehlende Zeile beantwortet sie nicht.
    """

    coverage_key: str = Field(min_length=3, max_length=49)
    is_included: bool = True
    sum_insured_minor: int | None = Field(default=None, ge=0)
    deductible_minor: int | None = Field(default=None, ge=0)
    benefit_rate_bp: int | None = Field(default=None, ge=0, le=10000)
    waiting_until: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class PolicyCoverage(PolicyCoverageInput):
    """Baustein samt Katalogtext - die Liste zeigt beides in einer Zeile."""

    id: UUID
    policy_id: UUID
    label: str
    purpose_short: str
    created_at: datetime
    updated_at: datetime


class PolicyCoverageReplacement(BaseModel):
    """PUT ersetzt die Menge - ein Rundlauf statt n.

    Ein Editor haelt die Bausteinliste einer Hausratpolice mit ueber zwanzig
    Zeilen im Formular; sie einzeln zu schicken hiesse, den Zwischenstand
    mehrfach zu speichern und bei jedem Abbruch eine halbe Wahrheit zu
    hinterlassen. Eine leere Liste loescht die Menge - das ist beabsichtigt und
    der einzige Weg, eine Police wieder auf den Katalogzweck zurueckzustellen.
    """

    coverages: list[PolicyCoverageInput] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def _keine_doppelten_schluessel(self) -> "PolicyCoverageReplacement":
        schluessel = [eintrag.coverage_key for eintrag in self.coverages]
        if len(set(schluessel)) != len(schluessel):
            raise ValueError("Ein Baustein darf in derselben Police nur einmal vorkommen.")
        return self


class InsuredObject(BaseModel):
    """Eine versicherte Sache.

    `current_plate` und `current_keeper_*` stammen aus der laufenden Zeile der
    Halterhistorie, nicht aus einer Spalte an dieser Tabelle: ein
    Kennzeichenwechsel ist eine neue Zeile, kein Ueberschreiben.
    """

    id: UUID
    kind: InsuredObjectKind
    label: str
    asset_id: UUID | None
    address_line: str | None
    postal_code: str | None
    city: str | None
    living_area_sqm: int | None
    build_year: int | None
    attrs: dict[str, Any]
    # Fristanker des Paragraphen 96 Abs. 2 S. 2 VVG beim Fahrzeugkauf. Immer
    # Nutzereingabe - deshalb ohne Herkunftsfeld.
    acquired_on: date | None
    # Erzeugt KEINE Kuendigungsfrist: der Veraeusserer hat eine Anzeigepflicht
    # (Paragraph 97 Abs. 1 VVG), kein Kuendigungsrecht.
    disposed_on: date | None
    is_active: bool
    current_plate: str | None = None
    current_keeper_person_id: UUID | None = None
    current_keeper_name: str | None = None
    policy_count: int = 0
    version: int
    created_at: datetime
    updated_at: datetime


class InsuredObjectCreate(BaseModel):
    kind: InsuredObjectKind
    label: str = Field(min_length=1, max_length=160)
    asset_id: UUID | None = None
    address_line: str | None = None
    postal_code: str | None = None
    city: str | None = None
    living_area_sqm: int | None = Field(default=None, ge=1, le=2000)
    build_year: int | None = Field(default=None, ge=1500, le=2200)
    attrs: dict[str, Any] = Field(default_factory=dict)
    acquired_on: date | None = None
    disposed_on: date | None = None

    @model_validator(mode="after")
    def _erwerb_vor_abgabe(self) -> "InsuredObjectCreate":
        if (
            self.acquired_on is not None
            and self.disposed_on is not None
            and self.disposed_on < self.acquired_on
        ):
            raise ValueError("Ein Objekt kann nicht vor seinem Erwerb abgegeben werden.")
        return self


class InsuredObjectUpdate(BaseModel):
    kind: InsuredObjectKind | None = None
    label: str | None = Field(default=None, min_length=1, max_length=160)
    asset_id: UUID | None = None
    address_line: str | None = None
    postal_code: str | None = None
    city: str | None = None
    living_area_sqm: int | None = Field(default=None, ge=1, le=2000)
    build_year: int | None = Field(default=None, ge=1500, le=2200)
    attrs: dict[str, Any] | None = None
    acquired_on: date | None = None
    disposed_on: date | None = None
    is_active: bool | None = None
    clear_fields: list[
        Literal[
            "asset_id",
            "address_line",
            "postal_code",
            "city",
            "living_area_sqm",
            "build_year",
            "acquired_on",
            "disposed_on",
        ]
    ] = Field(default_factory=list)


class ObjectRegistration(BaseModel):
    """Eine Zeile der Halter- und Kennzeichenhistorie."""

    id: UUID
    insured_object_id: UUID
    plate: str | None
    keeper_person_id: UUID | None
    keeper_name: str | None
    valid_from: date
    valid_to: date | None
    note: str | None
    created_at: datetime


class ObjectRegistrationCreate(BaseModel):
    """Ein Wechsel, kein Formular.

    Der Aufruf schliesst die laufende Zeile zum Vortag und oeffnet eine neue.
    Ohne Kennzeichen UND ohne Halter waere die neue Zeile inhaltsleer und
    schloesse die alte grundlos.
    """

    plate: str | None = Field(default=None, min_length=2, max_length=20)
    keeper_person_id: UUID | None = None
    valid_from: date
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _mindestens_eine_angabe(self) -> "ObjectRegistrationCreate":
        if self.plate is None and self.keeper_person_id is None:
            raise ValueError("Eine Historienzeile braucht ein Kennzeichen oder einen Halter.")
        return self


class PolicyObject(BaseModel):
    """Ein Objekt in seiner Rolle an einer Police."""

    id: UUID
    policy_id: UUID
    insured_object_id: UUID
    kind: InsuredObjectKind
    label: str
    acquired_on: date | None
    disposed_on: date | None
    current_plate: str | None
    note: str | None
    created_at: datetime


class PolicyObjectCreate(BaseModel):
    insured_object_id: UUID | None = None
    new_object: InsuredObjectCreate | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _genau_ein_objekt(self) -> "PolicyObjectCreate":
        if (self.insured_object_id is None) == (self.new_object is None):
            raise ValueError(
                "Entweder ein bestehendes Objekt waehlen oder ein neues anlegen - nie beides."
            )
        return self


class PolicyBundle(BaseModel):
    """Buendelzuordnung (Plan 3.9): Kasko unter Kfz-Haftpflicht, Elementar
    unter Wohngebaeude.

    `parent_policy_id = null` loest das Buendel wieder auf. Genau eine Ebene -
    eine Kindpolice hat selbst kein Kind, sonst waere die Wurzelpolice, auf die
    der Nummern-Index zeigt, nicht mehr bestimmbar.
    """

    parent_policy_id: UUID | None = None


class PolicyBundleResult(BaseModel):
    """Die Police nach der Zuordnung, plus die Zuordnung selbst.

    Das Feld steht zusaetzlich in `InsurancePolicy` - dort aus der Zeile
    gelesen, hier aus der eben durchgefuehrten Zuordnung. Es bleibt hier
    stehen, weil `hint` den Fall erklaert, in dem beide Policen dieselbe
    Scheinnummer tragen; das ist eine Aussage ueber die Zuordnung und nicht
    ueber die Police.
    """

    policy: InsurancePolicy
    parent_policy_id: UUID | None
    hint: str = ""


# ---------------------------------------------------------------------------
# Schaden- und Leistungsfaelle (P5)
# ---------------------------------------------------------------------------

# Der Statusweg eines Vorgangs. 'draft' ist der erfasste, aber noch nicht
# gemeldete Schaden - vor der Meldung entsteht kein Fenster und keine Frist.
ClaimStatus = Literal[
    "draft",
    "reported",
    "in_review",
    "info_requested",
    "decided",
    "payment_expected",
    "paid",
    "closed",
    "withdrawn",
]
ClaimDecision = Literal["accepted_full", "accepted_partial", "rejected"]
# Welche Norm das Sonderkuendigungsrecht traegt. Kommt aus
# `insurance_lines.default_claim_termination_basis` und steht als Konstante
# nirgends im Code: beide Normen sind dispositiv, die Bedingungen gehen vor.
ClaimTerminationBasis = Literal["vvg_92", "vvg_111", "contract", "none"]
ClaimAnchorReason = Literal[
    "final_rejection",
    "full_payment",
    "settlement_letter",
    "withdrawal",
    "judgment_final",
    "liability_acknowledged",
    "liability_refused",
    "user_confirmed",
]
ClaimAnchorSource = Literal["transaction", "manual"]
# Deutsch wie `main_due_source` an der Police: die Sicherheit eines Datums ist
# eine Aussage an den Nutzer, kein Systemzustand.
ClaimAnchorCertainty = Literal["belegt", "abgeleitet", "geschaetzt", "unbekannt"]
ClaimTerminationState = Literal["none", "open_undetermined", "open_until", "expired"]
ClaimTerminationEffect = Literal["immediate", "end_of_period"]
ClaimPaymentDirection = Literal["inbound", "outbound"]
ClaimPaymentKind = Literal["settlement", "advance", "deductible", "invoice", "recovery"]


class ClaimAnchorSuggestion(BaseModel):
    """Vorschlag fuer den Fristanker - nie eine Schreibung.

    Der "Abschluss der Verhandlungen" ist ein Rechtsbegriff, den weder das
    Werkzeug noch der Nutzer sicher bestimmen kann. Der Dienst schlaegt ihn
    deshalb vor und schreibt ihn erst auf `POST /claims/{id}/close-negotiations`.
    Bei einer Teilregulierung entsteht bewusst GAR KEIN Vorschlag: dort ist die
    Entscheidung der Anfang der Verhandlung, nicht ihr Ende.
    """

    negotiations_closed_on: date
    reason: ClaimAnchorReason
    source: ClaimAnchorSource
    certainty: ClaimAnchorCertainty
    transaction_id: UUID | None = None
    # Woraus der Vorschlag folgt, im Klartext. Die Oberflaeche entscheidet nie
    # am Freitext, sondern an `certainty` - der Satz ist der Beleg.
    rationale: str = ""


class ClaimTerminationWindow(BaseModel):
    """Das Sonderkuendigungsfenster nach dem Versicherungsfall.

    Vier Zustaende statt zweier, weil "kein Recht", "Recht besteht, Ende offen",
    "Recht besteht bis" und "Recht war da und ist verfallen" vier verschiedene
    Auskuenfte sind. `open_undetermined` ist der wichtigste: das Recht besteht
    ab Eintritt des Versicherungsfalles (Paragraph 92 Abs. 1 VVG), sein Ende
    haengt am Abschluss der Verhandlungen - und der steht oft monatelang nicht
    fest. Ein plausibel aussehendes, frei gerechnetes Datum waere hier die
    einzige Variante, die dem Nutzer schadet.
    """

    state: ClaimTerminationState
    termination_basis: ClaimTerminationBasis
    deadline_kind: DeadlineKind = "kuendigungsfrist"
    # Der Fristanker samt Herkunft - null, solange er nicht gesetzt ist.
    anchor_on: date | None = None
    anchor_reason: ClaimAnchorReason | None = None
    anchor_source: ClaimAnchorSource | None = None
    anchor_certainty: ClaimAnchorCertainty = "unbekannt"
    anchor_transaction_id: UUID | None = None
    # Die drei Daten der Frist, wie ueberall in diesem Modul. Sie stehen NUR,
    # wenn der Anker gesetzt ist.
    deadline_legal_on: date | None = None
    safe_service_on: date | None = None
    recommended_action_on: date | None = None
    days_left: int | None = None
    # Wirkung der Kuendigung ist eine Wahl, kein Datum (Paragraph 92 Abs. 2
    # S. 3 VVG deckelt den VN auf den Schluss der laufenden
    # Versicherungsperiode, die Bedingungen erlauben oft zusaetzlich sofort).
    effect_options: list[ClaimTerminationEffect] = Field(default_factory=list)
    # Spaetester Beendigungszeitpunkt fuer den Versicherungsnehmer. Nur bei
    # bestaetigter Hauptfaelligkeit gefuellt - aus einem angenommenen Anker
    # entstuende sonst ein angenommenes Vertragsende.
    latest_effect_on: date | None = None
    # `true`, solange der Anker fehlt: die maschinenlesbare Form der Aussage
    # "die Angabe fehlt", die an dieser Stelle an die Stelle eines Datums tritt.
    needs_anchor: bool = False
    law: str = ""
    rationale: str = ""
    basis_kind: AnchorBasisKind = "statute"
    suggestion: ClaimAnchorSuggestion | None = None


class ClaimPayment(BaseModel):
    """Eine Zahlung zum Vorgang.

    `transaction_id` ist null, wenn der Versicherer direkt an die Werkstatt
    gezahlt hat: dann gibt es keine Buchung, aber sehr wohl eine Zahlung. Die
    Felder `transaction_*` stammen aus der verknuepften Buchung und erlauben der
    Oberflaeche den Rueckweg in den Buchungsstrom ohne zweiten Rundlauf.
    """

    id: UUID
    claim_id: UUID
    transaction_id: UUID | None
    direction: ClaimPaymentDirection
    kind: ClaimPaymentKind
    amount_minor: int
    booked_on: date
    note: str | None
    transaction_booked_date: date | None = None
    transaction_amount_minor: int | None = None
    transaction_payee_label: str | None = None
    created_at: datetime


class ClaimPaymentCreate(BaseModel):
    """Ordnet eine Zahlung zu.

    Ohne `transaction_id` ist `booked_on` Pflicht - eine Zahlung ohne Buchung
    und ohne Datum waere weder belegt noch einsortierbar.
    """

    transaction_id: UUID | None = None
    direction: ClaimPaymentDirection = "inbound"
    kind: ClaimPaymentKind = "settlement"
    amount_minor: int = Field(gt=0)
    booked_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _datum_oder_buchung(self) -> "ClaimPaymentCreate":
        if self.transaction_id is None and self.booked_on is None:
            raise ValueError("Eine Zahlung ohne Buchung braucht ein Datum.")
        return self


class ClaimEvent(BaseModel):
    """Eine Zeile des Zeitstrahls.

    Abgeleitete Fakten, keine Anzeigetexte: `payload` traegt, was geschehen ist,
    die Oberflaeche formuliert daraus. Fertige Saetze in der Datenbank waeren
    beim ersten Sprachwechsel falsch.
    """

    id: int
    claim_id: UUID
    from_status: ClaimStatus | None
    to_status: ClaimStatus
    occurred_on: date
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class InsuranceClaim(BaseModel):
    """Ein Schadenvorgang.

    Auch hier entsteht kein Geld: `claimed_minor` und `approved_minor` sind der
    Inhalt einer Abrechnung. `settled_minor` dagegen ist ABGELEITET - es ist die
    Summe der zugeordneten Eingangszahlungen und damit die einzige Zahl dieser
    Zeile, die an Buchungen haengt.
    """

    id: UUID
    policy_id: UUID | None
    policy_label: str | None = None
    insurer_name: str | None = None
    line_key: str | None = None
    line_label: str | None = None
    insured_object_id: UUID | None
    object_label: str | None = None
    title: str
    claim_number: str | None
    occurred_on: date
    reported_on: date | None
    location: str | None
    description: str | None
    police_reference: str | None
    status: ClaimStatus
    decision: ClaimDecision | None
    decided_on: date | None
    reduction_reason: str | None
    claimed_minor: int
    approved_minor: int | None
    deductible_minor: int
    settled_minor: int
    # Was nach der Abrechnung noch aussteht: anerkannter (hilfsweise geforderter)
    # Betrag abzueglich des bereits Gezahlten, nie negativ.
    open_minor: int
    paid_to_third_party: bool
    next_action: str | None
    next_action_due: date | None
    special_termination: ClaimTerminationWindow
    payment_count: int = 0
    version: int
    created_at: datetime
    updated_at: datetime


class InsuranceClaimDetail(InsuranceClaim):
    """Der Vorgang mit Zahlungen und Zeitstrahl - die Datenlage des Dialogs."""

    payments: list[ClaimPayment] = Field(default_factory=list)
    events: list[ClaimEvent] = Field(default_factory=list)


class InsuranceClaimCreate(BaseModel):
    """Legt einen Vorgang an.

    `policy_id` ist optional, und zwar nicht aus Bequemlichkeit: ein Schaden
    kann sich gegen eine FREMDE Versicherung richten - der Auffahrunfall, bei
    dem die Gegenseite reguliert. Ein solcher Vorgang hat keine eigene Police
    und deshalb auch kein Sonderkuendigungsrecht.

    `termination_basis` bleibt in der Regel leer: der Dienst belegt sie aus der
    Sparte vor. Sie steht hier nur, weil beide Normen dispositiv sind und die
    Bedingungen einer Police abweichen duerfen.
    """

    policy_id: UUID | None = None
    insured_object_id: UUID | None = None
    title: str = Field(min_length=1, max_length=160)
    claim_number: str | None = Field(default=None, max_length=80)
    occurred_on: date
    reported_on: date | None = None
    location: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    police_reference: str | None = Field(default=None, max_length=120)
    status: ClaimStatus = "draft"
    claimed_minor: int = Field(default=0, ge=0)
    approved_minor: int | None = Field(default=None, ge=0)
    deductible_minor: int = Field(default=0, ge=0)
    next_action: str | None = Field(default=None, max_length=240)
    next_action_due: date | None = None
    termination_basis: ClaimTerminationBasis | None = None
    termination_effect_options: list[ClaimTerminationEffect] | None = None

    @model_validator(mode="after")
    def _meldung_nach_eintritt(self) -> "InsuranceClaimCreate":
        if self.reported_on is not None and self.reported_on < self.occurred_on:
            raise ValueError("Ein Schaden kann nicht vor seinem Eintritt gemeldet werden.")
        return self


class InsuranceClaimUpdate(BaseModel):
    """Teilaenderung.

    Ausdruecklich NICHT enthalten: `negotiations_closed_*` und
    `special_termination_*`. Der Fristanker entsteht ausschliesslich ueber
    `POST /claims/{id}/close-negotiations` - dieselbe Haltung wie bei
    `main_due_source` an der Police, wo die Herkunft eines Datums nicht per
    PATCH hochgestuft werden kann. `settled_minor` fehlt aus dem zweiten Grund:
    es ist die Summe der zugeordneten Zahlungen und hat nur einen Schreiber.
    """

    policy_id: UUID | None = None
    insured_object_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    claim_number: str | None = Field(default=None, max_length=80)
    occurred_on: date | None = None
    reported_on: date | None = None
    location: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    police_reference: str | None = Field(default=None, max_length=120)
    decision: ClaimDecision | None = None
    decided_on: date | None = None
    reduction_reason: str | None = Field(default=None, max_length=2000)
    claimed_minor: int | None = Field(default=None, ge=0)
    approved_minor: int | None = Field(default=None, ge=0)
    deductible_minor: int | None = Field(default=None, ge=0)
    paid_to_third_party: bool | None = None
    next_action: str | None = Field(default=None, max_length=240)
    next_action_due: date | None = None
    termination_basis: ClaimTerminationBasis | None = None
    termination_effect_options: list[ClaimTerminationEffect] | None = None
    clear_fields: list[
        Literal[
            "policy_id",
            "insured_object_id",
            "claim_number",
            "reported_on",
            "location",
            "description",
            "police_reference",
            "decision",
            "decided_on",
            "reduction_reason",
            "approved_minor",
            "next_action",
            "next_action_due",
        ]
    ] = Field(default_factory=list)


class ClaimStatusChange(BaseModel):
    """Statuswechsel als Einfeldaktion.

    Setzt KEIN Fristdatum. Beim Uebergang nach `reported` entsteht der Zustand
    `open_undetermined` - das Recht besteht ab Eintritt des Versicherungsfalles,
    sein Ende steht noch nicht fest. Die alte Regel
    "`special_termination_until = decided_on + 1 Monat`" ist ersatzlos
    entfallen.
    """

    to_status: ClaimStatus
    # Tag des Ereignisses; Vorgabe ist heute. Er landet im Zeitstrahl und, beim
    # Uebergang nach `reported`, in `reported_on` - sofern das dort noch leer
    # ist. Ein bereits erfasstes Meldedatum wird nie ueberschrieben.
    occurred_on: date | None = None
    decision: ClaimDecision | None = None
    decided_on: date | None = None
    reduction_reason: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _entscheidung_vollstaendig(self) -> "ClaimStatusChange":
        if self.decision == "accepted_partial" and not self.reduction_reason:
            raise ValueError("Eine Teilregulierung braucht die Begruendung der Kuerzung.")
        if self.to_status == "decided" and self.decision is None:
            raise ValueError("Der Uebergang nach 'decided' braucht die Entscheidung.")
        return self


class ClaimCloseNegotiations(BaseModel):
    """Setzt den Fristanker - die einzige Stelle, an der er entsteht.

    `transaction_id` ist der Unterschied zwischen einer Behauptung und einem
    Beleg: nur mit zugeordnetem Kontoumsatz erreicht der Anker die Sicherheit
    `belegt`. Ohne ihn bleibt es bei `abgeleitet` (aus dem Entscheidungsdatum)
    oder `geschaetzt` (vom Nutzer gesetzt).
    """

    negotiations_closed_on: date
    reason: ClaimAnchorReason
    transaction_id: UUID | None = None


class ClaimTransactionCandidate(BaseModel):
    """Ein Zahlungskandidat aus dem Buchungsstrom.

    Vorschlag mit Beleg, keine Automatik - dieselbe Form wie im
    Erstattungsmodul. `remaining_minor` ist der Teil der Buchung, der noch
    keinem Vorgang zugeordnet ist; eine Sammelueberweisung deckt mehrere.
    """

    transaction_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None
    note: str | None
    remaining_minor: int
    score: float
    evidence: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Erstattungsbruecke (P5, Plan 9.1)
# ---------------------------------------------------------------------------


class PolicySuggestion(BaseModel):
    """Eine Police, die zu einem Kostentraeger passen koennte."""

    policy_id: UUID
    insurer_name: str
    label: str | None
    line_key: str
    line_label: str
    is_health_scoped: bool
    score: float
    evidence: list[str] = Field(default_factory=list)


class PayerPolicyLink(BaseModel):
    """Ohne Feld ist der Aufruf ein reiner Vorschlag und schreibt nichts.

    Kein Zwang und keine Datenmigration: das Erstattungsmodul funktioniert
    unveraendert weiter, auch wenn nie eine Police angelegt oder verknuepft
    wird. Dort liegen echte Nutzerdaten.
    """

    link_policy_id: UUID | None = None
    unlink: bool = False

    @model_validator(mode="after")
    def _nicht_beides(self) -> "PayerPolicyLink":
        if self.unlink and self.link_policy_id is not None:
            raise ValueError("Entweder verknuepfen oder loesen - nie beides.")
        return self


class PayerPolicySuggestion(BaseModel):
    """Der Kostentraeger, seine Police und die Vorschlaege dazu."""

    payer_id: UUID
    payer_name: str
    payer_type: str
    policy_id: UUID | None
    suggestions: list[PolicySuggestion] = Field(default_factory=list)
    hint: str = ""


# ---------------------------------------------------------------------------
# Bedarfsabgleich (P6, Plan 4.9 und 10)
# ---------------------------------------------------------------------------
# Drei Eigenheiten unterscheiden diesen Abschnitt vom Rest der Datei:
#
# 1. **Jedes Angabefeld ist optional und ohne Default.** "Nicht beantwortet"
#    ist ein eigener Zustand neben "nein" - ein Default false machte aus einer
#    nie gestellten Frage eine Luecke.
# 2. **Kein Score.** Es gibt keine Note, keinen Balken und keine Gesamtzahl
#    ueber alle Risikofelder. `counts` zaehlt Befunde je Ergebniszustand, das
#    ist eine Anzahl und keine Bewertung.
# 3. **Jeder Befund traegt seine Herkunft**: die Regel, die Angaben, auf denen
#    sie beruht, die beruecksichtigten Policen und die Quelle mit Pruefdatum.
#    Ohne diese vier Felder ist ein Befund nicht ueberpruefbar.

NeedsOutcome = Literal[
    "fulfilled",
    "underinsured",
    "gap",
    "unclear",
    "overinsured",
    "dismissed",
    "not_applicable",
]
NeedTier = Literal["S0", "S1", "S2", "S3", "S4"]
RuleScope = Literal["household", "person"]
HousingForm = Literal["rent", "owner_occupied", "owner_rented_out", "none"]
ElementalZone = Literal["zurs1", "zurs2", "zurs3", "zurs4", "unknown"]
TravelFrequency = Literal["never", "occasional", "frequent"]
EmploymentStatus = Literal[
    "employed",
    "civil_servant",
    "self_employed",
    "apprentice",
    "student",
    "retired",
    "not_employed",
    "child",
]
HealthCoverage = Literal["gkv", "pkv", "pkv_beihilfe", "other"]
RuleDecisionKind = Literal["dismissed", "accepted", "snoozed"]
RuleDecisionReason = Literal[
    "self_insured", "covered_elsewhere", "not_applicable", "planned", "other"
]
TargetMetric = Literal[
    "policy_exists",
    "coverage_included",
    "sum_insured_min",
    "sum_insured_formula",
    "benefit_rate_min",
    "benefit_rate_formula",
    "not_needed",
]
# Die zwei Ausschlussarten aus Plan 10.3 ("Person setzt aus", "Police zaehlt
# nicht als Erfuellung") und ihr Spiegelbild aus R24 ("Police zaehlt als
# vorhanden"). Sie stehen als Datenzeile im Katalog und nicht als Sonderlogik im
# Auswerter - genau dafuer ist die Datenzeilenform da.
SpecialKind = Literal["person_suspends", "policy_not_counted", "policy_counts"]
# Geschlossene Liste benannter Funktionen, KEIN auswertbarer Ausdruck: ein
# Ausdrucksfeld waere wieder die untestbare Mini-Sprache, die die
# Operatorbeschraenkung der Bedingungsseite gerade verhindert (Plan 10.1).
TargetFormula = Literal[
    "contents_value_per_sqm",
    "open_debt",
    "beihilfe_complement",
    "net_income_annual_x5",
]
# Nur die vier Arten, an denen im Regelkatalog etwas haengt (R14, R14a): Hund
# und Pferd loesen die Tierhalterhaftpflicht aus, Katze und Kleintier nicht.
PetKind = Literal["dog", "horse", "cat", "small_animal"]
DoubleInsuranceKind = Literal["same_line", "contained_in"]
# 'established' heisst "beide Seiten sind erfasst", 'unclear' heisst woertlich
# "koennte doppelt sein, ich weiss es aber nicht" (Plan 10.4).
DoubleInsuranceCertainty = Literal["established", "unclear"]


# ---------------------------------------------------------------------------
# Die Regel selbst (Plan 10.1)
# ---------------------------------------------------------------------------
# Die Regeln liegen als DATENZEILEN in app/data/insurance_rules.py und werden
# von insurance_needs_service ausgewertet. Ihr Typ steht hier und nicht im
# Dienst, damit die Datendatei ihn importieren kann, ohne den Auswerter zu
# importieren - sonst entstuende ein Ringschluss zwischen Daten und Dienst.
#
# Der Modellvalidator ist der "Auswerter", gegen den der Plan den Seed geprueft
# wissen will: jede Regel muss zu ihrer target_metric die dort geforderten
# Felder tragen und darf keine anderen setzen. Ein Regelsatz, der das verletzt,
# faellt beim Laden auf und erzeugt KEINEN Befund - eine fehlende Regel meldet
# keine Luecke, das ist die sichere Richtung.

RULE_CONDITION_UNSUPPORTED = "insurance_rule_condition_unsupported"

_CONDITION_COMPARATORS = frozenset({"in", "gt", "lt", "eq", "is_null"})


def _validate_condition(node: Any, path: str = "condition") -> None:
    """Prueft eine Bedingung gegen das geschlossene Vokabular aus Plan 10.1.

    Erlaubt sind ausschliesslich `all`, `any` und ein Vergleich der Form
    `{"fact": ..., <op>: ...}` mit genau einem Operator aus
    `_CONDITION_COMPARATORS`. Alles andere wird abgewiesen - sonst wird aus der
    Bedingung eine untestbare Mini-Sprache, und die Nachvollziehbarkeit jedes
    Befundes haengt genau daran.
    """
    if not isinstance(node, dict):
        raise ValueError(f"{RULE_CONDITION_UNSUPPORTED}: {path} ist kein Objekt")
    if not node:
        # Die leere Bedingung ist die Regel "immer" aus dem Katalog.
        return
    keys = set(node)
    if keys in ({"all"}, {"any"}):
        (operator,) = keys
        branches = node[operator]
        if not isinstance(branches, list | tuple):
            raise ValueError(f"{RULE_CONDITION_UNSUPPORTED}: {path}.{operator} ist keine Liste")
        for index, branch in enumerate(branches):
            _validate_condition(branch, f"{path}.{operator}[{index}]")
        return
    if "fact" not in keys:
        raise ValueError(
            f"{RULE_CONDITION_UNSUPPORTED}: {path} kennt weder all, any noch fact"
        )
    if not isinstance(node["fact"], str) or not node["fact"]:
        raise ValueError(f"{RULE_CONDITION_UNSUPPORTED}: {path}.fact ist kein Merkmalsname")
    operators = keys - {"fact"}
    if len(operators) != 1 or not operators <= _CONDITION_COMPARATORS:
        raise ValueError(
            f"{RULE_CONDITION_UNSUPPORTED}: {path} braucht genau einen Operator aus "
            f"{sorted(_CONDITION_COMPARATORS)}, hat aber {sorted(operators)}"
        )
    (comparator,) = operators
    if comparator == "in" and not isinstance(node["in"], list | tuple):
        raise ValueError(f"{RULE_CONDITION_UNSUPPORTED}: {path}.in ist keine Liste")
    if comparator == "is_null" and not isinstance(node["is_null"], bool):
        raise ValueError(f"{RULE_CONDITION_UNSUPPORTED}: {path}.is_null ist kein Wahrheitswert")


def condition_facts(node: Any) -> list[str]:
    """Alle Merkmale, auf die eine Bedingung zeigt - in Lesereihenfolge.

    Der Auswerter braucht sie fuer `triggered_by`: ein Befund ohne die Angaben,
    auf denen er beruht, ist nicht ueberpruefbar und damit wertlos.
    """
    if not isinstance(node, dict) or not node:
        return []
    for operator in ("all", "any"):
        if operator in node:
            found: list[str] = []
            for branch in node[operator]:
                for fact in condition_facts(branch):
                    if fact not in found:
                        found.append(fact)
            return found
    name = node.get("fact")
    return [name] if isinstance(name, str) else []


class InsuranceRule(BaseModel):
    """Eine Regel des Bedarfsabgleichs, in der Notation aus Plan 10.1.

    Unveraenderlich und mit `extra="forbid"`: ein Tippfehler im Feldnamen soll
    beim Laden auffallen und nicht still eine Regel ohne Zielwert erzeugen. Die
    Feldnamen sind bytegleich zur Datenzeile in `app/data/insurance_rules.py` -
    der Katalog wird beim Laden ueber `from_attributes` durch dieses Modell
    gezogen, und ein Feld, das hier fehlte, fiele dabei still unter den Tisch.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    rule_key: str = Field(pattern=r"^[A-Z][0-9]{1,3}[a-z]?$")
    risk_field: RiskField
    # None nur bei einer Regel, die keine Sparte betrifft (die Personenaussetzung
    # aus R7b).
    line_key: str | None = None
    # R1 nennt zwei Sparten, von denen EINE genuegt (gesetzlich oder privat
    # krankenversichert); R11a nennt zwei, die BEIDE fehlen koennen
    # (Bauherrenhaftpflicht und Bauleistung). Ohne die Unterscheidung waere eine
    # der beiden Zeilen nicht abbildbar.
    alternative_line_keys: tuple[str, ...] = ()
    additional_line_keys: tuple[str, ...] = ()
    # "R2 erfuellt", "Hausrat vorhanden": die Anschlussregel wertet nur aus, wenn
    # eine Police dieser Sparte besteht. Ueber die Police statt ueber den
    # Regelschluessel, weil eine Basisregel `unclear` sein kann, waehrend die
    # Police unstreitig da ist.
    depends_on_line_key: str | None = None
    priority: NeedTier | None = None
    # Personenbezogene Regeln werden je sichtbarer Person einmal ausgewertet
    # (R4 "je Person einmal"), Haushaltsregeln genau einmal.
    scope: RuleScope = "household"
    condition: dict[str, Any] = Field(default_factory=dict)
    # Pflichtangaben. Fehlt eine davon, lautet das Ergebnis `unclear` und nennt
    # sie. Als Bedingung formuliert waere das Ergebnis `not_applicable` - die
    # Regel verschwiege also, dass sie etwas nicht wissen konnte.
    required_facts: tuple[str, ...] = ()
    target_metric: TargetMetric | None = None
    target_coverage_key: str | None = None
    # Ganzzahlig, in Minor Units oder Basispunkten - nie float.
    target_value: int | None = None
    target_formula: TargetFormula | None = None
    # Die zweite Zahl aus R2a: eine Anzeigeabstufung, keine zweite Schwelle.
    warn_value: int | None = None
    special: SpecialKind | None = None
    # Nur zusammen mit `special`: welche Policenstatus die Sonderart meint.
    policy_status_filter: tuple[str, ...] = ()
    headline: str = Field(min_length=3, max_length=160)
    rationale: str = Field(min_length=3, max_length=1200)
    source_url: str | None = None
    source_checked_on: date | None = None
    # Redaktionsnotiz des Katalogs. Sie wird nie ausgespielt.
    note: str = ""

    @field_validator("condition")
    @classmethod
    def _closed_vocabulary(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_condition(value)
        return value

    @model_validator(mode="after")
    def _target_is_complete(self) -> "InsuranceRule":
        if self.target_metric is None:
            if self.special is None:
                raise ValueError("Regel ohne Ziel und ohne Sonderart")
            for name in ("target_coverage_key", "target_value", "target_formula"):
                if getattr(self, name) is not None:
                    raise ValueError(f"{name} ohne target_metric")
        else:
            wants_coverage = self.target_metric == "coverage_included"
            wants_value = self.target_metric in ("sum_insured_min", "benefit_rate_min")
            wants_formula = self.target_metric in (
                "sum_insured_formula",
                "benefit_rate_formula",
            )
            for name, wanted in (
                ("target_coverage_key", wants_coverage),
                ("target_value", wants_value),
                ("target_formula", wants_formula),
            ):
                if wanted and getattr(self, name) is None:
                    raise ValueError(f"{name} fehlt zu {self.target_metric}")
                if not wanted and getattr(self, name) is not None:
                    raise ValueError(f"{name} passt nicht zu {self.target_metric}")
        if self.line_key is None and self.special != "person_suspends":
            raise ValueError("Regel ohne Sparte")
        if self.policy_status_filter and self.special is None:
            raise ValueError("Statusfilter ohne Sonderart")
        # Quellenpflicht: nur die Personenaussetzung trifft keine Bedarfsaussage,
        # fuer die es eine Fundstelle geben koennte. Ohne Quelle und Pruefdatum
        # stuende in einem Befund eine Wertung ohne Herkunft.
        if self.special != "person_suspends" and (
            not self.source_url or self.source_checked_on is None
        ):
            raise ValueError("Fundstelle oder Pruefdatum fehlt")
        return self

    @property
    def any_line_keys(self) -> tuple[str, ...]:
        """Sparten, von denen EINE genuegt."""
        first = (self.line_key,) if self.line_key else ()
        return first + self.alternative_line_keys

    @property
    def all_line_keys(self) -> tuple[str, ...]:
        """Sparten, die zusaetzlich vorhanden sein muessen."""
        return self.additional_line_keys

    @property
    def facts(self) -> list[str]:
        found = condition_facts(self.condition)
        for name in self.required_facts:
            if name not in found:
                found.append(name)
        return found


# Wortgleich zur Plan-Notation 10.1, damit die Datenzeilen in
# app/data/insurance_rules.py `Rule(rule_key=...)` heissen duerfen.
Rule = InsuranceRule


class NeedsProfileInput(BaseModel):
    """Die Haushaltsangaben.

    Jedes Feld ist optional UND darf ausdruecklich null bleiben: `null` heisst
    "nicht beantwortet", `false` heisst "nein". Der Auswerter macht daraus zwei
    verschiedene Ergebnisse (`unclear` gegenueber `not_applicable`), und genau
    diese Unterscheidung ist der Grund, warum hier kein einziges Feld einen
    Default traegt.

    Daraus folgt die Schreibregel des PUT: geschrieben werden genau die Felder,
    die im Koerper STEHEN. Ein mitgesendetes `null` loescht die Antwort, ein
    weggelassenes Feld bleibt unveraendert. Ein PUT, das fehlende Schluessel als
    "nicht beantwortet" schriebe, loeschte bei jedem Client, der eine Frage
    (noch) nicht anbietet, still die Antwort darauf - und genau dieser stille
    Verlust erzeugt die erfundene Luecke, gegen die das ganze Modul gebaut ist.
    """

    housing_form: HousingForm | None = None
    living_area_sqm: int | None = Field(default=None, ge=1, le=2000)
    elemental_zone: ElementalZone | None = None
    has_oil_tank: bool | None = None
    construction_active: bool | None = None
    has_photovoltaic: bool | None = None
    liquid_reserve_minor: int | None = Field(default=None, ge=0)
    contents_value_minor: int | None = Field(default=None, ge=0)
    has_open_loans: bool | None = None
    children_count: int | None = Field(default=None, ge=0, le=20)
    # null heisst "nie gefragt", [] heisst "gefragt, keine Tiere".
    pets: list[PetKind] | None = None
    travel_frequency: TravelFrequency | None = None
    vehicle_count: int | None = Field(default=None, ge=0)
    side_business: bool | None = None
    volunteering: bool | None = None
    has_drone: bool | None = None
    has_hunting_license: bool | None = None
    has_boat: bool | None = None
    # Ohne Angabe setzt der Dienst den heutigen Tag: das Speichern der Angaben
    # IST die Pruefung, und die Wiedervorlage braucht ein Datum.
    last_checked_on: date | None = None


class PersonNeedsProfileInput(BaseModel):
    """Angaben je Person - der sensibelste Datensatz des Moduls.

    `died_on` steht bewusst hier und nicht in einem eigenen Endpunkt: es ist
    eine Spalte an `health_insured_persons` (keine zweite Personenliste), und
    dies ist der einzige Schreibweg darauf. Ohne sie verlangt der Abgleich fuer
    eine verstorbene Person weiter Berufsunfaehigkeit und Risikoleben.

    Dieselbe Schreibregel wie beim Haushaltsprofil: nur die gesendeten Felder
    werden geschrieben.
    """

    employment_status: EmploymentStatus | None = None
    net_income_minor: int | None = Field(default=None, ge=0)
    health_coverage: HealthCoverage | None = None
    beihilfe_rate_bp: Literal[5000, 7000, 8000] | None = None
    has_dependents: bool | None = None
    education_until: date | None = None
    moved_out_on: date | None = None
    died_on: date | None = None


class PersonNeedsProfile(PersonNeedsProfileInput):
    insured_person_id: UUID
    display_name: str
    birth_date: date | None = None
    # Wer schreiben darf, entscheidet `health_can_manage_person` in der
    # Datenbank. Das Feld sagt es der Oberflaeche nur an, damit sie ein Formular
    # nicht anbietet, dessen Absenden 403 waere - es ist ausdruecklich KEIN
    # zweites Rechtesystem.
    can_manage: bool = False
    version: int = 1
    updated_at: datetime | None = None
    exists: bool = False


class NeedsProfile(NeedsProfileInput):
    """Dieselben Angaben in der Ausgabe, plus Stand, Version und Personen."""

    version: int = 1
    updated_at: datetime | None = None
    # Es gibt noch keine Zeile: der Abgleich laeuft trotzdem, er meldet dann
    # eben ueberall "Angabe fehlt". Ein 404 zwaenge die Oberflaeche, die
    # Feldliste ein zweites Mal zu kennen.
    exists: bool = False
    # Nur die Personen, die der Aufrufer ueberhaupt sehen darf. Eine leere Liste
    # heisst deshalb NICHT "keine Personen erfasst", sondern kann auch "keine
    # Gesundheitsfreigabe" heissen - die Oberflaeche darf daraus keine Aussage
    # ueber den Haushalt machen.
    persons: list[PersonNeedsProfile] = Field(default_factory=list)


class RuleDecision(BaseModel):
    """Die gespeicherte Nutzerentscheidung zu einer Regel."""

    id: UUID
    rule_key: str
    policy_id: UUID | None = None
    decision: RuleDecisionKind
    reason: RuleDecisionReason | None = None
    reason_note: str | None = None
    remind_on: date | None = None
    decided_at: datetime
    # Wiedervorlage erreicht: die Entscheidung gilt nicht mehr, der Befund ist
    # zurueck. Sie bleibt trotzdem in der Antwort, damit die Oberflaeche
    # erklaeren kann, warum er wieder da ist.
    expired: bool = False


class NeedsFinding(BaseModel):
    """Ein einzelner Befund. Traegt seine Regel, seine Angaben, seine Policen.

    `outcome` ist der Ergebniszustand aus Plan 10.2. `unclear` ist ein
    vollwertiges Ergebnis und wird NIE als Luecke gezaehlt: wo Angaben fehlen,
    sagt der Abgleich das, statt eine Luecke zu behaupten, die es womoeglich
    nicht gibt.
    """

    rule_key: str
    risk_field: RiskField
    # Einzahl: die Sparte, auf die DIESER Befund zeigt - die Sparte der
    # getroffenen Police, sonst die erste Zielsparte der Regel. `line_keys`
    # nennt daneben alle Zielsparten, weil eine Regel zwei haben kann (R1).
    line_key: str | None = None
    line_label: str | None = None
    line_keys: list[str] = Field(default_factory=list)
    line_labels: list[str] = Field(default_factory=list)
    coverage_key: str | None = None
    outcome: NeedsOutcome
    scope: RuleScope = "household"
    priority: NeedTier | None = None
    # Allgemeine Katalogvorgabe der Sparte. Die `priority` der Regel schlaegt
    # sie ("Hausrat ist S2" gilt allgemein, "fuer diesen Haushalt ist Hausrat
    # S1" gilt konkret) - beide stehen hier, damit die Oberflaeche den
    # Unterschied benennen kann, statt zwei Stufen fuer dieselbe Sparte zu
    # zeigen.
    need_tier: NeedTier | None = None
    priority_source: Literal["rule", "line"] | None = None
    headline: str
    rationale: str
    # Warum GENAU dieses Ergebnis herauskam - der Satz, den die Oberflaeche
    # unter die Begruendung setzt. Er nennt Policen und Angaben, nie ein Urteil.
    outcome_reason: str = ""
    triggered_by: dict[str, Any] = Field(default_factory=dict)
    missing_facts: list[str] = Field(default_factory=list)
    source_url: str | None = None
    source_checked_on: date | None = None
    matched_policy_ids: list[UUID] = Field(default_factory=list)
    considered_policy_ids: list[UUID] = Field(default_factory=list)
    # Was geprueft wurde, flach statt verschachtelt: die Kennzahl, der Baustein
    # und - bei den Geldregeln - die Schwelle, gegen die gemessen wurde. Ohne
    # sie stuende in einem Befund "Summe zu niedrig", ohne dass nachvollziehbar
    # waere, wogegen. Quotenregeln (`benefit_rate_*`) fuellen die
    # `*_value_minor`-Felder ausdruecklich NICHT - ein Basispunktwert in einem
    # Geldfeld waere in jeder Oberflaeche ein Eurobetrag.
    target_metric: TargetMetric | None = None
    target_formula: TargetFormula | None = None
    target_value_minor: int | None = None
    actual_value_minor: int | None = None
    gap_value_minor: int | None = None
    person_id: UUID | None = None
    person_name: str | None = None
    decision: RuleDecision | None = None


class NeedsCheck(BaseModel):
    """Das Ergebnis des Abgleichs.

    `disclaimer` steht im Koerper und nicht nur in der Fusszeile: die Aussage,
    dass hier weder beraten noch verkauft wird, gehoert zum Ergebnis und nicht
    zum Seitenlayout.
    """

    generated_on: date
    # Derselbe Tag unter dem Namen, den die Oberflaeche liest. Ein Alias und
    # keine zweite Wahrheit: der Abgleich wird bei jedem Aufruf frisch
    # gerechnet, "gerechnet am" und "erzeugt am" koennen gar nicht auseinander
    # laufen.
    checked_on: date
    disclaimer: str
    # Kein Score: eine Anzahl je Ergebniszustand. `unclear` steht bewusst
    # daneben und nicht darin.
    counts: dict[str, int] = Field(default_factory=dict)
    # Noch keine einzige Police erfasst: die Oberflaeche zeigt dann "nichts
    # erfasst" und nicht dreissig Luecken.
    stock_empty: bool = False
    profile_last_checked_on: date | None = None
    rules_evaluated: int = 0
    findings: list[NeedsFinding] = Field(default_factory=list)


class NeedsCompletenessItem(BaseModel):
    """Eine FEHLENDE Angabe und die Regeln, die ohne sie nicht entscheidbar sind."""

    field: str
    label: str | None = None
    scope: RuleScope = "household"
    person_id: UUID | None = None
    person_name: str | None = None
    blocks_rule_keys: list[str] = Field(default_factory=list)


class NeedsCompleteness(BaseModel):
    """Welche fehlenden Angaben blockieren welche Regeln.

    Ausdruecklich KEIN Fortschrittsbalken ueber die Absicherung, sondern ueber
    die Fragen: "sieben von zwoelf Angaben gemacht" ist eine Tatsache,
    "70 Prozent abgesichert" waere die Scheingenauigkeit, die dieses Modul
    vermeidet.

    Aufgefuehrt wird nur, was fehlt. Eine beantwortete Frage steht in keiner
    Liste - sie blockiert nichts, und eine vollstaendige Fragenliste zweimal zu
    fuehren (hier und im Formular) hiesse, sie frueher oder spaeter zweimal
    verschieden zu fuehren.
    """

    answered: int = 0
    total: int = 0
    blocked_rules: int = 0
    last_checked_on: date | None = None
    missing: list[NeedsCompletenessItem] = Field(default_factory=list)


class RuleDecisionRequest(BaseModel):
    """Eine Entscheidung zu einem Befund.

    `policy_id` bleibt im Normalfall leer - die meisten Regeln zeigen auf eine
    FEHLENDE Police, und eine Entscheidung dazu kann auf keine zeigen.
    """

    decision: RuleDecisionKind = "dismissed"
    policy_id: UUID | None = None
    reason: RuleDecisionReason | None = None
    reason_note: str | None = Field(default=None, max_length=2000)
    remind_on: date | None = None

    @model_validator(mode="after")
    def _wiedervorlage_braucht_datum(self) -> "RuleDecisionRequest":
        if self.decision == "snoozed" and self.remind_on is None:
            raise ValueError("Eine Zurueckstellung braucht ein Datum der Wiedervorlage.")
        return self


class DoubleInsuranceFinding(BaseModel):
    """Zwei Policen, die dieselbe Sache decken koennten.

    `certainty = 'unclear'` heisst woertlich "koennte doppelt sein, ich weiss es
    aber nicht": die Bausteine der deckenden Police sind nicht erfasst. Ein
    Befund, der sich selbst entwertet, waere schlechter als keiner - deshalb
    traegt er dann `hint` mit der Aufgabe statt einer Behauptung, und
    `missing_coverage_policy_ids` nennt die Police, an der sie ansetzt.

    `rationale` nennt die Rechtsfolge und keinen Rat: doppelte Beitraege, aber
    kein doppeltes Geld. Kein Wechselvorschlag, kein Anbietername.
    """

    kind: DoubleInsuranceKind
    certainty: DoubleInsuranceCertainty
    policy_id: UUID
    policy_label: str
    other_policy_id: UUID
    other_policy_label: str
    line_key: str
    line_label: str | None = None
    other_line_key: str
    other_line_label: str | None = None
    coverage_key: str | None = None
    coverage_label: str | None = None
    yearly_minor: int | None = None
    rationale: str
    hint: str | None = None
    missing_coverage_policy_ids: list[UUID] = Field(default_factory=list)
