"""API-Vertraege der Liquiditaetsvorschau
(docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 1 und 2).

Die Vorschau selbst speichert nichts (Leitsatz 1) - persistiert wird nur
`forecast_settings`. Alle uebrigen Modelle hier sind Ableitungen, die der
Rechenkern in `services/household_forecast_service.py` erzeugt.

Alle Betraege sind Netto und ganzzahlig in Cent (Suffix `_minor`); eine
Brutto-Netto-Rechnung ist ausdruecklich nicht Teil des Plans (Abschnitt 12.1).
"""

from datetime import date
from datetime import datetime as DateTime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ForecastGranularity = Literal["day", "month"]
ForecastSourceKind = Literal["contract", "transaction", "simulation"]
ForecastWarningLevel = Literal["critical", "watch", "info"]
ForecastQualityLevel = Literal["good", "limited", "insufficient"]
ForecastVariableBasis = Literal["budget", "history", "max"]

ForecastChangeType = Literal[
    "income_add",
    "income_change",
    "income_end",
    "expense_add",
    "expense_change",
    "expense_end",
    "one_off",
]
ForecastAmountMode = Literal["absolute", "delta", "percent"]
ForecastChangeFrequency = Literal[
    "once", "weekly", "monthly", "quarterly", "semiannual", "yearly"
]
ForecastConfidence = Literal["contracted", "estimated", "speculative"]

# Aenderungstypen, die einen bestehenden Vertrag als Ziel brauchen.
_TARGETED_CHANGE_TYPES: frozenset[str] = frozenset(
    {"income_change", "income_end", "expense_change", "expense_end"}
)
_END_CHANGE_TYPES: frozenset[str] = frozenset({"income_end", "expense_end"})


# ---------------------------------------------------------------------------
# Ergebnis der Vorschau
# ---------------------------------------------------------------------------


class ForecastFlow(BaseModel):
    """Eine einzelne datierte Bewegung. `amount_minor` ist vorzeichenbehaftet:
    positiv = Zufluss, negativ = Abfluss."""

    model_config = ConfigDict(frozen=True)

    label: str
    amount_minor: int
    source_kind: ForecastSourceKind
    source_id: UUID | None = None


class ForecastDay(BaseModel):
    """Ein Tag der Vorschau. `variable_minor` ist der geschaetzte Tagesanteil
    der variablen Ausgaben - er steht getrennt neben `flows`, damit die
    Oberflaeche Gerechnetes von Geschaetztem unterscheiden kann."""

    model_config = ConfigDict(frozen=True)

    on: date
    opening_balance_minor: int
    variable_minor: int
    balance_minor: int
    is_event: bool
    flows: tuple[ForecastFlow, ...] = ()


class ForecastMonth(BaseModel):
    model_config = ConfigDict(frozen=True)

    year: int
    month: int
    income_minor: int
    fixed_minor: int
    variable_minor: int
    net_minor: int
    closing_balance_minor: int


class ForecastMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    lowest_balance_minor: int
    lowest_balance_on: date
    first_negative_on: date | None
    first_below_buffer_on: date | None
    recovery_on: date | None
    days_below_zero: int
    days_below_buffer: int
    needed_against_negative_minor: int
    needed_for_buffer_minor: int
    average_monthly_surplus_minor: int
    income_monthly_minor: int
    fixed_monthly_minor: int
    variable_monthly_minor: int
    savings_rate_percent: float
    fixed_cost_ratio_percent: float
    # "Reserve ohne Einnahmen": liquides Vermoegen geteilt durch Monatsfixkosten.
    # Der Name kommt aus Abschnitt 15.1 - "Fixkosten gedeckt fuer X Monate"
    # wurde als "inklusive Einkommen" missverstanden.
    reserve_without_income_months: float
    balance_in_3_months_minor: int
    balance_in_6_months_minor: int
    balance_in_12_months_minor: int
    balance_in_24_months_minor: int


class ForecastCause(BaseModel):
    """Der groesste Abflusstag in den 30 Tagen vor dem ersten Minustag.

    Auslaeser und Wirkung liegen im Referenzhaushalt drei Wochen auseinander;
    ohne diese Zuordnung wirken beide Daten willkuerlich."""

    model_config = ConfigDict(frozen=True)

    on: date
    total_minor: int
    flows: tuple[ForecastFlow, ...]


class ForecastWarning(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    level: ForecastWarningLevel
    context: dict[str, Any] = Field(default_factory=dict)


class ForecastOpeningAccount(BaseModel):
    """Ein Konto im Startsaldo - einbezogen oder nicht.

    Der Startsaldo war bisher eine einzelne Zahl ohne Herkunft. Genau daran
    scheitert die Nachvollziehbarkeit: steht dort ein unerwarteter Wert, kann
    der Nutzer nicht sehen, welches Konto ihn verursacht. Ausgeschlossene
    Konten stehen mit ihrem Saldo dabei, weil ein nicht mitgezaehltes
    Kreditkartenminus die Lage besser aussehen laesst, als sie ist.
    """

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    name: str
    account_type: str
    included: bool
    balance_minor: int
    last_booking_on: date | None
    # Warum das Konto (nicht) zaehlt: 'heuristik' oder 'gewaehlt'.
    reason: Literal["heuristic", "explicit"]


class ForecastQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: ForecastQualityLevel
    last_booking_on: date | None
    contract_count: int
    income_count: int
    variable_basis: ForecastVariableBasis
    excluded_account_names: tuple[str, ...]
    contracts_ending_in_horizon: int


class ForecastResponse(BaseModel):
    """Ergebnis eines Rechenlaufs.

    `as_of`, `opening_liquidity_minor` und `safety_buffer_minor` stehen neben
    den im Plan (5.3) genannten Feldern, weil das Diagramm ohne sie weder die
    Heute-Linie noch die Pufferzone zeichnen kann.
    """

    model_config = ConfigDict(frozen=True)

    as_of: date
    horizon_months: int
    granularity: ForecastGranularity
    opening_liquidity_minor: int
    opening_accounts: tuple[ForecastOpeningAccount, ...] = ()
    # Nur ausgewiesen, NIE vom Kontostand abgezogen: reserve_pots hat keinen
    # Kontobezug (Plan Abschnitt 4.4, kritische Regel 5). Steht bewusst neben
    # der Kontolinie und nicht in den Kennzahlen - es ist ein Bestand, keine
    # Ableitung aus dem Verlauf.
    bound_reserve_minor: int
    safety_buffer_minor: int
    daily: tuple[ForecastDay, ...]
    monthly: tuple[ForecastMonth, ...]
    metrics: ForecastMetrics
    cause: ForecastCause | None
    warnings: tuple[ForecastWarning, ...]
    quality: ForecastQuality


# Die Vorschau heisst im Plan `ForecastResult`, in der API `ForecastResponse` -
# es ist dasselbe Modell, kein zweites.
ForecastResult = ForecastResponse


class ForecastDayGroup(BaseModel):
    """Eine Gruppe von `GET /forecast/upcoming`: alle Posten eines Tages mit
    Tagessumme und dem Stand danach. Eine flache Liste beantwortet "wodurch"
    nur halb (Abschnitt 15.1)."""

    model_config = ConfigDict(frozen=True)

    on: date
    total_minor: int
    balance_after_minor: int
    is_heavy: bool
    flows: tuple[ForecastFlow, ...]


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


class ForecastSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    safety_buffer_minor: int
    variable_basis: ForecastVariableBasis
    variable_lookback_months: int
    horizon_months: int
    # Push nur bei Zustandswechsel (Plan Stufe 5d, Abschnitt 9). Die drei
    # Schalter stehen hier und nicht in einer eigenen Tabelle - Vorbild
    # contract_settings.notify_*. `notify_below_buffer` deckt auch den
    # kritisch werdenden Monat ab: der Ausloeser ist dasselbe Unterschreiten
    # des Puffers, nur monatsweise statt taggenau.
    notify_shortfall: bool = True
    notify_below_buffer: bool = True
    notify_income_missing: bool = True
    updated_at: DateTime | None = None


class ForecastSettingsInput(BaseModel):
    safety_buffer_minor: int = Field(ge=0)
    variable_basis: ForecastVariableBasis
    variable_lookback_months: int = Field(ge=3, le=24)
    horizon_months: int = Field(ge=1, le=60)
    # Mit Vorgabewert, damit ein Client, der die Schalter nicht kennt, die
    # Rechenannahmen weiter speichern kann, ohne sie stillschweigend
    # abzuschalten.
    notify_shortfall: bool = True
    notify_below_buffer: bool = True
    notify_income_missing: bool = True


# ---------------------------------------------------------------------------
# Was-waere-wenn
# ---------------------------------------------------------------------------


class ForecastChange(BaseModel):
    """Eine Annahme des Was-waere-wenn (Plan Abschnitt 6.2).

    `amount_minor` ist vorzeichenbehaftet, damit eine Gehaltssenkung oder eine
    Kostenreduktion ausdrueckbar ist. Bei `delta` und `percent` wirkt das
    Vorzeichen auf den BETRAG des Zielvertrags, nicht auf seine Richtung: ein
    negativer Delta senkt eine Einnahme und ebenso eine Ausgabe. Andernfalls
    liesse sich "Kostenreduktion" (Plan 6.2) gar nicht ausdruecken.
    """

    model_config = ConfigDict(frozen=True)

    change_type: ForecastChangeType
    target_contract_id: UUID | None = None
    label: str = Field(min_length=1, max_length=160)
    amount_mode: ForecastAmountMode | None = None
    amount_minor: int | None = None
    amount_percent: Decimal | None = None
    frequency: ForecastChangeFrequency = "monthly"
    due_day: int | None = Field(default=None, ge=1, le=31)
    income_kind: str | None = None
    # Wem die neue Einnahme gehoert (Stufe 6). Nur der Steuerklassenhinweis
    # liest das Feld: ohne es liesse sich ein Zweitjob DERSELBEN Person nicht
    # von einem zweiten Gehalt im Haushalt unterscheiden, und der Hinweis
    # ("beide Nettobetraege pruefen") waere im ersten Fall falsch. Die
    # Rechnung selbst kennt keine Personen - eine Einnahme wirkt unabhaengig
    # davon, auf wessen Konto sie faellt.
    person_id: UUID | None = None
    starts_on: date
    ends_on: date | None = None
    confidence: ForecastConfidence = "estimated"

    @model_validator(mode="after")
    def _check(self) -> "ForecastChange":
        needs_target = self.change_type in _TARGETED_CHANGE_TYPES
        if needs_target and self.target_contract_id is None:
            raise ValueError(f"target_contract_id ist bei {self.change_type} erforderlich")
        if not needs_target and self.target_contract_id is not None:
            raise ValueError(f"target_contract_id ist bei {self.change_type} nicht erlaubt")

        is_end = self.change_type in _END_CHANGE_TYPES
        if is_end:
            # Bei *_end gibt es keinen Betrag - amount_mode ist deshalb
            # nullable (Korrektur aus Abschnitt 15).
            if self.amount_mode is not None:
                raise ValueError("amount_mode ist bei *_end nicht erlaubt")
            if self.amount_minor is not None or self.amount_percent is not None:
                raise ValueError("Betragsangaben sind bei *_end nicht erlaubt")
        else:
            if self.amount_mode is None:
                raise ValueError(f"amount_mode ist bei {self.change_type} erforderlich")
            if self.amount_mode == "percent":
                if self.amount_percent is None:
                    raise ValueError("amount_percent ist bei amount_mode=percent erforderlich")
                if self.amount_minor is not None:
                    raise ValueError("amount_minor nur bei amount_mode absolute/delta")
            else:
                if self.amount_minor is None:
                    raise ValueError("amount_minor ist bei amount_mode absolute/delta erforderlich")
                if self.amount_percent is not None:
                    raise ValueError("amount_percent nur bei amount_mode=percent")

        if self.change_type == "one_off":
            if self.frequency != "once":
                raise ValueError("one_off verlangt frequency=once")
        elif self.frequency == "once":
            raise ValueError("frequency=once ist nur bei one_off erlaubt")

        if self.change_type == "income_add":
            if not self.income_kind:
                raise ValueError("income_kind ist bei income_add erforderlich")
        elif self.income_kind is not None:
            raise ValueError("income_kind ist nur bei income_add erlaubt")

        # Anders als `income_kind` bleibt die Person auch bei `income_add`
        # freiwillig: wer "400 EUR mehr im Monat" durchrechnet, muss nicht
        # zuerst eine Person anlegen.
        if self.change_type != "income_add" and self.person_id is not None:
            raise ValueError("person_id ist nur bei income_add erlaubt")

        # Bei `absolute` legt das Vorzeichen die Richtung fest und muss zum
        # Aenderungstyp passen - sonst wuerde ein "income_add" mit negativem
        # Betrag still zu einem Abfluss.
        if self.amount_mode == "absolute" and self.amount_minor is not None:
            if self.change_type in ("income_add", "income_change") and self.amount_minor <= 0:
                raise ValueError("amount_minor muss bei Einnahmen positiv sein")
            if self.change_type in ("expense_add", "expense_change") and self.amount_minor >= 0:
                raise ValueError("amount_minor muss bei Ausgaben negativ sein")
        if self.amount_minor == 0:
            raise ValueError("amount_minor darf nicht 0 sein")

        if self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("ends_on muss >= starts_on sein")
        return self


class ForecastSimulationRequest(BaseModel):
    horizon_months: int | None = Field(default=None, ge=1, le=60)
    include_savings: bool = False
    changes: list[ForecastChange] = Field(default_factory=list)


class ForecastComparison(BaseModel):
    """Die vier eindeutig definierten Groessen aus Abschnitt 6.3, dazu die
    Kontostandsdifferenzen und die Verschiebung des Engpasses."""

    model_config = ConfigDict(frozen=True)

    # Vorher/Nachher, weil der Plan (6.3) genau so vergleicht - und zusaetzlich
    # die Differenz, damit die Oberflaeche sie nicht selbst bilden muss.
    surplus_before_minor: int
    surplus_after_minor: int
    surplus_delta_minor: int
    savings_rate_before_percent: float
    savings_rate_after_percent: float
    savings_rate_delta_percent: float
    fixed_cost_ratio_before_percent: float
    fixed_cost_ratio_after_percent: float
    fixed_cost_ratio_delta_percent: float
    reserve_without_income_before_months: float
    reserve_without_income_after_months: float
    reserve_without_income_delta_months: float
    balance_delta_3_months_minor: int
    balance_delta_6_months_minor: int
    balance_delta_12_months_minor: int
    balance_delta_24_months_minor: int
    lowest_balance_delta_minor: int
    first_negative_shift_days: int | None
    resolves_shortfall: bool


class ForecastContribution(BaseModel):
    """Beitrag genau EINER Aenderung, aus einem eigenen Rechenlauf mit nur
    dieser Aenderung (Abschnitt 8.2). Ohne diesen zweiten Lauf laesst sich
    nicht sagen, welche Annahme den Engpass tatsaechlich beseitigt."""

    model_config = ConfigDict(frozen=True)

    change_index: int
    monthly_minor: int
    resolves_shortfall: bool
    shift_days: int | None
    first_negative_on: date | None


class ForecastSimulationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    base: ForecastResponse
    simulation: ForecastResponse
    comparison: ForecastComparison
    contributions: tuple[ForecastContribution, ...]
