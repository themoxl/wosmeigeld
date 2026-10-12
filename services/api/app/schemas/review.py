from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.contract import (
    ContractDirection,
    ContractFrequency,
    ContractKeySource,
    ContractStatus,
    ContractType,
)


class ReviewItemUnclassified(BaseModel):
    type: Literal["unclassified"] = "unclassified"
    transaction_id: UUID
    account_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None
    note: str | None
    # Echter Bank-Verwendungszweck; `note` ist nur die App-Notiz. Additiv und
    # optional fuer bereits ausgelieferte Clients.
    purpose: str | None = None


class ReviewCounterpart(BaseModel):
    """Die bereits vorhandene Buchung, gegen die eine Zeile als Dublette gilt.

    Ohne sie ist die Frage nicht zu beantworten: die Oberflaeche zeigte bisher
    nur die neue Zeile und verlangte die Entscheidung "Dublette oder nicht" -
    ohne zu sagen, wovon. Der Nutzer musste in "Alle Buchungen" nachschlagen
    oder blind klicken."""

    transaction_id: UUID
    booked_date: date | None = None
    amount_minor: int | None = None
    payee_label: str | None = None
    purpose: str | None = None
    account_name: str | None = None
    # Woher die vorhandene Buchung stammt - "aus derselben Datei" ist eine
    # andere Lage als "die hast du vor drei Monaten von Hand erfasst".
    source: str | None = None


class ReviewItemDuplicate(BaseModel):
    type: Literal["possible_duplicate"] = "possible_duplicate"
    import_row_id: UUID
    import_run_id: UUID
    booked_date: date | None
    amount_minor: int | None
    payee_raw: str | None
    purpose: str | None
    duplicate_of_transaction_id: UUID | None
    duplicate_of: ReviewCounterpart | None = None


class ReviewItemTransfer(BaseModel):
    type: Literal["transfer_candidate"] = "transfer_candidate"
    import_row_id: UUID
    candidate_transaction_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None


class BookedDuplicateTransaction(ReviewCounterpart):
    version: int
    currency: str
    import_filename: str | None = None


class ReviewItemBookedDuplicate(BaseModel):
    first: BookedDuplicateTransaction
    second: BookedDuplicateTransaction
    match: Literal["duplicate_exact", "duplicate_possible"]


class BookedDuplicateScanResult(BaseModel):
    scanned_count: int
    total_count: int
    items: list[ReviewItemBookedDuplicate]


class BookedDuplicatePairInput(BaseModel):
    first_transaction_id: UUID
    second_transaction_id: UUID
    first_version: int = Field(ge=1)
    second_version: int = Field(ge=1)


class ResolveBookedDuplicateInput(BookedDuplicatePairInput):
    action: Literal["keep_both", "archive_first", "archive_second"]


class BulkResolveBookedDuplicatesInput(BaseModel):
    items: list[ResolveBookedDuplicateInput] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_pairs(self) -> "BulkResolveBookedDuplicatesInput":
        seen: set[tuple[UUID, UUID]] = set()
        for item in self.items:
            first, second = sorted((item.first_transaction_id, item.second_transaction_id))
            if first == second or (first, second) in seen:
                raise ValueError("Jedes Buchungspaar darf nur einmal ausgewählt werden.")
            seen.add((first, second))
        return self


class BulkResolveBookedDuplicatesResult(BaseModel):
    processed_count: int
    archived_count: int
    kept_count: int


class BookedDuplicateAssessmentInput(BaseModel):
    items: list[BookedDuplicatePairInput] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def unique_pairs(self) -> "BookedDuplicateAssessmentInput":
        seen: set[tuple[UUID, UUID]] = set()
        for item in self.items:
            first, second = sorted((item.first_transaction_id, item.second_transaction_id))
            if first == second or (first, second) in seen:
                raise ValueError("Jedes Buchungspaar darf nur einmal ausgewählt werden.")
            seen.add((first, second))
        return self


class BookedDuplicateAssessment(BookedDuplicatePairInput):
    verdict: Literal["same", "different", "unsure"]
    reason: str
    confidence: float = Field(ge=0, le=1)
    action: Literal["archive_first", "archive_second", "keep_both"] | None = None


class BookedDuplicateAssessmentResult(BaseModel):
    available: bool
    reason: str
    message: str
    model_name: str | None = None
    prompt_version: str
    minimum_confidence: float = 0.9
    assessments: list[BookedDuplicateAssessment] = Field(default_factory=list)


class ReviewItemRecurring(BaseModel):
    """VERALTET, abgeloest durch `ReviewItemContractSuggestion`.

    Der Server fuellt `recurring_suggestions` nicht mehr (siehe
    `rules_service.get_review_inbox`). Das Modell bleibt, weil ein halb
    ausgerollter Client den Typ noch senden kann und weil der alte
    Detail-Endpunkt weiterhin darauf antwortet.
    """

    type: Literal["recurring_suggestion"] = "recurring_suggestion"
    source_transaction_id: UUID
    payee_label: str
    occurrences_count: int
    expected_amount_minor: int
    amount_tolerance_minor: int
    suggested_frequency: Literal["weekly", "monthly", "quarterly", "yearly"]
    first_date: date
    last_date: date
    suggested_category_id: UUID | None = None


class ReviewRecurringTransaction(BaseModel):
    transaction_id: UUID
    account_id: UUID
    account_name: str
    booked_date: date
    amount_minor: int
    payee_label: str | None
    note: str | None


class ReviewRecurringDetail(BaseModel):
    suggestion: ReviewItemRecurring
    transactions: list[ReviewRecurringTransaction]


class ReviewItemContractEndedCheck(BaseModel):
    """Ein Vertrag, dessen Zahlung mehrfach ausgeblieben ist: "laeuft der noch?".

    Der Abgleich kann nur zaehlen, dass nichts kam. Ob gekuendigt, gestundet oder
    nur eine Zahlung verschoben wurde, weiss allein der Nutzer - deshalb eine
    Frage in der Inbox statt einer stillen Statusaenderung.
    """

    type: Literal["contract_ended_check"] = "contract_ended_check"
    contract_id: UUID
    name: str
    provider_label: str | None
    # Wie in `contracts`: immer positiv, die Richtung steht in `direction`.
    amount_minor: int
    direction: ContractDirection
    frequency: ContractFrequency
    missed_periods: int
    last_seen_date: date | None
    next_due_date: date


class ReviewItemContractAmountChangeCheck(BaseModel):
    """Ein Vertrag, dessen Betrag sich erheblich geaendert hat: "stimmt das?".

    Grundlage ist ausschliesslich das bereits geschriebene Ereignis
    `contract_events.kind='price_change'` (erzeugt in
    `contract_matching_service._hit_outcome`); ein eigener Datensatz entsteht
    dafuer nicht.

    Warum ueberhaupt gefragt wird: der Abgleich UEBERNIMMT die Aenderung heute
    still. Er kann nicht unterscheiden, ob der Anbieter den Preis wirklich
    geaendert hat oder ob eine fremde Zahlung desselben Empfaengers
    faelschlich auf diesen Vertrag gebucht wurde - beides sieht in den Daten
    gleich aus. Das beantwortet nur der Nutzer.

    Ausdruecklich richtungsneutral: eine SENKUNG steht hier genauso wie eine
    Erhoehung. Der Anlassfall ist eine Senkung
    (Musterfunk Festnetz 49,95 EUR -> 39,95 EUR).
    """

    type: Literal["contract_amount_change_check"] = "contract_amount_change_check"
    # Anker ist das EREIGNIS, nicht der Vertrag: ein Vertrag kann mehrere
    # unbestaetigte Preisstufen tragen, und bestaetigt wird immer genau eine.
    event_id: UUID
    contract_id: UUID
    name: str
    provider_label: str | None
    direction: ContractDirection
    frequency: ContractFrequency
    # Beide wie in `contracts`: immer positiv, die Richtung steht in `direction`.
    old_amount_minor: int
    new_amount_minor: int
    # Vorzeichenbehaftet: negativ heisst guenstiger geworden. Beide Felder
    # werden aus alt/neu NEU berechnet und nicht aus der Ereignis-Nutzlast
    # uebernommen - eine aeltere oder von Hand geschriebene Nutzlast soll die
    # angezeigte Zahl nicht verfaelschen koennen.
    delta_minor: int
    delta_pct: float
    # Der Buchungstag, an dem der neue Betrag zuerst auftauchte.
    occurred_on: date


# ---------------------------------------------------------------------------
# Vertragsvorschlaege (Erkennung v2, docs/VERTRAEGE-FIXKOSTEN-PLAN.md Abs. 6)
# ---------------------------------------------------------------------------


class ReviewContractAmountStep(BaseModel):
    """Eine erkannte Preisstufe: derselbe Vertrag zu einem anderen Betrag.

    Der Nutzer sieht dadurch beim Bestaetigen, dass der Preis gestiegen ist -
    und nicht nur den heutigen Betrag ohne Vorgeschichte.
    """

    # Wie in `contracts`: immer positiv, die Richtung steht am Vorschlag.
    amount_minor: int
    first_date: date
    last_date: date
    occurrences_count: int


class ReviewContractScore(BaseModel):
    """Die fuenf Bestandteile des Vertrags-Scores einzeln, nicht nur die Summe.

    Ohne sie kann die Oberflaeche nicht begruenden, warum etwas vorgeschlagen
    wird - und ein unerklaerter Score ist in einem Finanzprodukt wertlos.
    """

    date_stability: float
    amount_stability: float
    occurrence_count: float
    category_affinity: float
    payment_method: float
    total: float


class ReviewItemContractSuggestion(BaseModel):
    """Ein erkannter Vertrag; loest `ReviewItemRecurring` ab.

    Zwei Unterschiede zum Vorgaenger sind bedeutungstragend und duerfen beim
    Lesen nicht verwechselt werden:

    * `amount_minor` ist wie in `contracts` IMMER positiv, die Richtung steht
      in `direction`. Das alte `expected_amount_minor` war negativ. Deshalb
      traegt das Feld bewusst einen anderen Namen - ein Client, der den alten
      Namen liest, bekaeme sonst still das falsche Vorzeichen.
    * `amount_steps` traegt die erkannte Betragstreppe. Aus ihr entsteht beim
      Bestaetigen der Betragsverlauf des Vertrags.
    """

    type: Literal["contract_suggestion"] = "contract_suggestion"
    source_transaction_id: UUID
    # Bei einem Sammelkonto "PayPal · <Vertragsschluessel>", sonst der Empfaenger.
    display_name: str
    payee_label: str
    contract_key: str | None
    contract_key_source: ContractKeySource | None
    amount_minor: int
    direction: ContractDirection
    amount_tolerance_minor: int
    frequency: ContractFrequency
    first_date: date
    last_date: date
    occurrences_count: int
    contract_type: ContractType
    installments_total: int | None
    installments_paid: int | None
    # Aelteste zuerst; die letzte Stufe traegt den Sollbetrag.
    amount_steps: list[ReviewContractAmountStep]
    suggested_category_id: UUID | None
    detection_confidence: float
    confidence: Literal["suggested", "uncertain"]
    # Aus `confidence` abgeleitet, aber ausdruecklich mitgegeben: der Client
    # soll den unsicheren Bereich kennzeichnen koennen, ohne die Schwellen
    # 0.40/0.55 des Servers nachzubauen.
    is_uncertain: bool
    score_breakdown: ReviewContractScore


class ReviewContractTransaction(BaseModel):
    transaction_id: UUID
    account_id: UUID
    account_name: str
    booked_date: date
    amount_minor: int
    payee_label: str | None
    note: str | None
    # Index in `suggestion.amount_steps`; ohne ihn liesse sich im Detail nicht
    # zeigen, welche Buchungen zu welchem Preis gehoerten.
    amount_step_index: int


class ReviewContractSuggestionDetail(BaseModel):
    suggestion: ReviewItemContractSuggestion
    transactions: list[ReviewContractTransaction]


class ReviewContractDuplicatePartner(BaseModel):
    contract_id: UUID
    name: str
    provider_label: str | None
    amount_minor: int
    status: ContractStatus
    first_seen_date: date | None
    last_seen_date: date | None


class ReviewItemContractDuplicate(BaseModel):
    """Zwei Vertraege, die vermutlich derselbe mit neuem Preis sind.

    Bedingung ist die zeitliche TRENNUNG: gleicher Empfaenger, gleicher
    Rhythmus, und der aeltere endet, bevor der juengere beginnt. Ueberlappen
    die Zeitraeume, laufen zwei echte Vertraege desselben Anbieters parallel
    (Musterfunk Festnetz und Mobilfunk) - dann entsteht hier bewusst kein
    Vorschlag, denn ein Zusammenfuehren waere dort Datenverlust.

    Gedacht fuer den Altbestand: Vertraege, die vor der Erkennung v2 als zwei
    getrennte Eintraege entstanden sind.
    """

    type: Literal["contract_duplicate_suggestion"] = "contract_duplicate_suggestion"
    # Ziel ist immer der juengere Vertrag: er traegt den aktuellen Preis und
    # die naechste Faelligkeit.
    target_id: UUID
    source_ids: list[UUID]
    payee_label: str
    frequency: ContractFrequency
    direction: ContractDirection
    older: ReviewContractDuplicatePartner
    newer: ReviewContractDuplicatePartner
    gap_days: int
    amount_delta_minor: int
    # Schluesselbewusste Bewertung (Plan Abschnitt 19.8): 'strong', wenn beide
    # Vertraege denselben Vertragsschluessel tragen, 'review' bei fehlendem
    # Schluessel und nur indizieller Uebereinstimmung. Ein Paar mit zwei
    # VERSCHIEDENEN Schluesseln erscheint gar nicht erst (contract_key_source
    # in rules_service.get_contract_duplicate_suggestions).
    evidence_strength: Literal["strong", "review"] = "review"
    # Stabile Gruende statt einer unkalibrierten Prozentzahl (Plan: "liefert
    # der Endpunkt stabile reason_codes"), z. B. same_contract_key,
    # same_payee, non_overlapping_periods, same_frequency, matching_due_day,
    # amount_transition.
    reason_codes: list[str] = []


class ContractDuplicateSuggestionsResponse(BaseModel):
    """Antwort von GET /v1/contracts/duplicate-suggestions (Abschnitt 19.10).

    `total` und `truncated` stehen bewusst neben `items`: das interne Limit
    darf auf der Vertraege-Seite nicht als exakte Gesamtzahl erscheinen."""

    items: list[ReviewItemContractDuplicate]
    total: int
    truncated: bool


class ReviewInboxResponse(BaseModel):
    total_count: int
    unclassified_open_count: int
    unclassified_total_count: int
    unclassified_offset: int
    unclassified_limit: int
    unclassified: list[ReviewItemUnclassified]
    possible_duplicates: list[ReviewItemDuplicate]
    booked_duplicates: list[ReviewItemBookedDuplicate] = []
    booked_duplicate_count: int = 0
    transfer_candidates: list[ReviewItemTransfer]
    # VERALTET und dauerhaft leer, siehe `ReviewItemRecurring`. Das Feld bleibt
    # als Pflichtfeld bestehen, weil ausgelieferte Clients es lesen und eine
    # fehlende Liste fuer sie ein Fehler waere.
    recurring_suggestions: list[ReviewItemRecurring]
    # Default [], damit bereits ausgelieferte Clients die neuen Listen
    # ignorieren koennen, ohne dass die Antwort fuer sie ungueltig wird.
    contract_ended_checks: list[ReviewItemContractEndedCheck] = []
    contract_suggestions: list[ReviewItemContractSuggestion] = []
    contract_duplicate_suggestions: list[ReviewItemContractDuplicate] = []
    contract_amount_change_checks: list[ReviewItemContractAmountChangeCheck] = []


class AcceptUnclassified(BaseModel):
    type: Literal["unclassified"]
    transaction_id: UUID
    category_id: UUID | None = None
    create_rule: bool = False
    rule_name: str | None = None


class AcceptDuplicate(BaseModel):
    type: Literal["possible_duplicate"]
    import_row_id: UUID


class AcceptTransfer(BaseModel):
    type: Literal["transfer_candidate"]
    import_row_id: UUID


class AcceptRecurring(BaseModel):
    """VERALTET, abgeloest durch `AcceptContractSuggestion`.

    Bleibt bestehen, solange ein halb ausgerollter Client den alten Typ noch
    senden kann; der Server fuehrt ihn auf denselben Servicepfad zurueck. Erst
    wenn Web und Mobile ausgeliefert sind, faellt er zusammen mit
    `ReviewItemRecurring` und dem alten Detail-Endpunkt in einem Schritt weg.
    """

    type: Literal["recurring_suggestion"]
    # Identifiziert den serverseitig erkannten Empfaenger-/Betragscluster.
    # Betrag, Rhythmus und Empfaenger werden bewusst nicht vom Client
    # uebernommen, sondern unmittelbar vor dem Speichern erneut ermittelt.
    source_transaction_id: UUID
    category_id: UUID


class AcceptContractSuggestion(BaseModel):
    """Bestaetigt einen erkannten Vertrag - oder verneint ihn.

    Wie beim Vorgaenger identifiziert nur `source_transaction_id` den
    Vorschlag; Betrag, Rhythmus, Empfaenger und Preisstufen werden unmittelbar
    vor dem Speichern erneut ermittelt, damit der Client nichts einschleusen
    kann.
    """

    type: Literal["contract_suggestion"]
    source_transaction_id: UUID
    # Bei `dismiss` bewusst optional: "das ist kein Vertrag" darf keine
    # Kategoriewahl erzwingen.
    category_id: UUID | None = None
    name: str | None = Field(default=None, max_length=160)
    contract_type: ContractType | None = None
    # "Kein Vertrag" -> Zeile mit status='not_a_contract', damit derselbe
    # Vorschlag nicht beim naechsten Import zurueckkommt.
    dismiss: bool = False

    @model_validator(mode="after")
    def _category_required_unless_dismissed(self) -> "AcceptContractSuggestion":
        if not self.dismiss and self.category_id is None:
            raise ValueError("category_id ist ohne dismiss erforderlich.")
        return self


class AcceptContractDuplicate(BaseModel):
    """Fuehrt zwei Eintraege desselben Vertrags zusammen - oder verwirft die Frage.

    `dismiss` ist kein Beiwerk: ohne eine gemerkte Verneinung stuende dasselbe
    Paar nach jedem Inbox-Aufruf erneut da.
    """

    type: Literal["contract_duplicate_suggestion"]
    target_id: UUID
    source_ids: list[UUID] = Field(min_length=1, max_length=8)
    dismiss: bool = False


class AcceptContractEnded(BaseModel):
    type: Literal["contract_ended_check"]
    contract_id: UUID
    # Die Antwort des Nutzers ist zugleich der Zielstatus. 'not_a_contract' fehlt
    # bewusst: die Frage lautet "laeuft der noch?", nicht "war das je ein
    # Vertrag?" - dafuer gibt es die Statusaktion auf der Vertragsseite.
    action: Literal["ended", "active", "paused"]


class AcceptContractAmountChange(BaseModel):
    """Bestaetigt eine erkannte Betragsaenderung: "ja, der Preis stimmt jetzt".

    Der Client schickt nur die Ereignis-ID. Alte und neue Zahl werden
    ausdruecklich NICHT uebernommen - sie stehen bereits in der Datenbank, und
    ein Client, der sie mitschicken darf, koennte sie auch aendern.

    `action` traegt bewusst nur EINEN Wert, obwohl der Plan zwei vorsieht.
    'wrong_amount' - die zuletzt geoeffnete Preisstufe zuruecknehmen - fehlt
    hier absichtlich und nicht aus Versehen:

    * Das saubere Zuruecknehmen ist die Umkehrung von
      `contract_service.open_amount_stage`, und dessen Vorlage fuer eine
      belegte Ruecknahme (das Merge-Undo) arbeitet ausschliesslich ueber das
      Journal `contract_merge_changes`. Fuer eine einzelne Preisstufe gibt es
      kein solches Journal.
    * `open_amount_stage` LOESCHT die vorherige offene Stufe, wenn sie am
      selben Tag begann. Der Vorher-Zustand ist danach aus dem Bestand nicht
      mehr rekonstruierbar, nur noch geraten.

    Ein Rueckweg ohne Journal waere ein zweiter, unbelegter Schreibpfad auf
    zahlenwirksame Felder - genau das, was Abschnitt 19.7 verhindern soll. Ein
    Client, der 'wrong_amount' sendet, bekommt deshalb 422 statt eines stillen
    Nichtstuns. Bis dahin bleibt der bestehende Weg: Betrag am Vertrag
    korrigieren (PATCH /v1/contracts/{id}).
    """

    type: Literal["contract_amount_change_check"]
    event_id: UUID
    action: Literal["confirmed"]


ReviewAcceptInput = Annotated[
    AcceptUnclassified
    | AcceptDuplicate
    | AcceptTransfer
    | AcceptRecurring
    | AcceptContractSuggestion
    | AcceptContractDuplicate
    | AcceptContractEnded
    | AcceptContractAmountChange,
    Field(discriminator="type"),
]


class CorrectUnclassified(BaseModel):
    type: Literal["unclassified"]
    transaction_id: UUID
    category_id: UUID
    payee_label: str | None = None
    create_rule_always: bool = False
    rule_match_payee: bool = True
    rule_match_purpose: bool = False
    rule_match_exact_amount: bool = False
    rule_match_account: bool = False
    apply_rule_to_existing: bool = False


class ReviewRulePreviewInput(BaseModel):
    transaction_id: UUID
    # Fuer die reine Treffer-Vorpruefung ist noch keine Zielkategorie noetig.
    # Sobald angewendet wird, verlangt CorrectUnclassified sie weiterhin.
    category_id: UUID | None = None
    payee_pattern: str | None = None
    match_payee: bool = True
    match_purpose: bool = False
    match_exact_amount: bool = False
    match_account: bool = False


class ReviewHistoryMatch(BaseModel):
    """Kategorie aus einer aktiven Regel oder dem bestaetigten Bestand."""

    matched: bool
    category_id: UUID | None = None
    category_label: str | None = None
    evidence_count: int = 0
    ambiguous: bool = False
    source: Literal["rule", "history"] | None = None
    rule_id: UUID | None = None
    rule_name: str | None = None


class ReviewHistoryMatchItem(ReviewHistoryMatch):
    transaction_id: UUID


class ReviewHistoryMatchesResponse(BaseModel):
    checked_count: int
    matched_count: int
    items: list[ReviewHistoryMatchItem]


class CorrectDuplicate(BaseModel):
    type: Literal["possible_duplicate"]
    import_row_id: UUID
    category_id: UUID


class CorrectTransfer(BaseModel):
    type: Literal["transfer_candidate"]
    import_row_id: UUID


ReviewCorrectInput = Annotated[
    CorrectUnclassified | CorrectDuplicate | CorrectTransfer,
    Field(discriminator="type"),
]


class BulkAcceptDuplicatesInput(BaseModel):
    """Mehrere moegliche Dubletten in einem Zug bestaetigen.

    Ein Import bringt sie zu Dutzenden - einzeln zu klicken heisst, dass die
    Liste liegen bleibt, und dann ist auch die echte Dublette darin verloren.
    """

    import_row_ids: list[UUID] = Field(min_length=1, max_length=500)


class BulkAcceptDuplicatesResult(BaseModel):
    confirmed: int
    # Zeilen, die inzwischen nicht mehr offen waren (schon entschieden, oder der
    # Lauf wurde zurueckgenommen). Kein Fehler, aber der Nutzer soll es sehen -
    # sonst wundert er sich ueber eine Zahl, die nicht zur Auswahl passt.
    skipped: int


DuplicateVerdict = Literal["same", "different", "unsure"]


class DuplicateAssessment(BaseModel):
    """Die Einschaetzung des Modells zu EINEM gemeldeten Dublettenkandidaten.

    Ein Hinweis, keine Entscheidung - angewendet wird nichts. Deshalb traegt sie
    ihre Begruendung mit: eine Zahl ohne Grund ist in einem Finanzprodukt
    wertlos, und der Nutzer muss widersprechen koennen.
    """

    import_row_id: UUID
    verdict: DuplicateVerdict
    reason: str
    confidence: float = 0.0


class DuplicateAssessmentResult(BaseModel):
    """Wie bei jedem KI-Vorschlag in diesem Projekt: `available=False` ist kein
    Fehler, sondern der geordnete Ausgang. Die Dublettenliste bleibt dann
    unveraendert von Hand bedienbar (Muster: ImportMappingSuggestion)."""

    available: bool
    reason: str
    message: str
    model_name: str | None = None
    prompt_version: str
    assessments: list[DuplicateAssessment] = Field(default_factory=list)


class DuplicateAssessmentInput(BaseModel):
    import_row_ids: list[UUID] = Field(min_length=1, max_length=25)
