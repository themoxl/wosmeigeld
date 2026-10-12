"""Der Analyse-Abfragevertrag (docs/ANALYSE-EXPLORER-PLAN.md, Baustein B).

Eine `AnalyticsQuery` beschreibt eine Auswertung vollstaendig und
serialisierbar. Der Server kompiliert sie zu parametrisiertem SQL; die
Clients senden nie SQL. Explorer, Diagramme, Drilldown, Berichte und die KI
erzeugen dieselbe Struktur und lesen ueber denselben Endpunkt - sonst
bekaeme jedes dieser Features seinen eigenen Aggregationspfad, und
"Diagramm und Tabelle widersprechen sich" waere nur eine Frage der Zeit.

Bewusst KEIN freies SQL und keine freien Spaltennamen: Feld-, Aggregat- und
Operatorbezeichner stammen ausschliesslich aus den Literal-Unions dieser
Datei bzw. aus dem Feldkatalog in `app/services/analytics_service.py`. Ein
unbekannter Bezeichner ist ein Validierungsfehler mit maschinenlesbarem
Code, kein stiller Fallback.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# Ergebniswerte sind immer Skalare: Betraege als int (Minor Units), Datums-
# und Kategorienamen als str, Merkmale als bool, fehlende Zuordnung als None.
#
# Bewusst NICHT `Any`: daraus erzeugt openapi-typescript `Record<string,
# never>`, und die Clients koennten mit keinem einzigen Zellwert rechnen.
#
# `date` gehoert dazu, weil eine nach Monat gruppierte Achse echte
# date-Objekte liefert (date_trunc(...)::date); JSON-seitig wird daraus ein
# ISO-String, im TypeScript-Vertrag also ebenfalls `string`.
type AnalyticsScalar = str | int | float | bool | date | None

# --------------------------------------------------------------------------
# Bausteine
# --------------------------------------------------------------------------

Grain = Literal["aggregate", "rows"]

DateGranularity = Literal["day", "week", "month", "quarter", "year"]

Aggregate = Literal[
    "sum",
    "avg",
    "median",
    "min",
    "max",
    # Betrag ohne Vorzeichen - fuer "wie viel floss durch diese Kategorie",
    # wenn Einnahmen und Ausgaben gemischt sind.
    "abs_sum",
    "count",
    "count_distinct",
    # Summe geteilt durch die Anzahl abgedeckter Monate: die Ø/Mt-Spalte, die
    # den Bankreport lesbar macht.
    "avg_per_month",
]

CompareOp = Literal[
    "eq",
    "ne",
    "lt",
    "lte",
    "gt",
    "gte",
    "in",
    "not_in",
    "between",
    "contains",
    "starts_with",
    "is_null",
    "is_not_null",
]

ColumnKind = Literal["date", "num", "cat", "bool"]


class DimensionRef(BaseModel):
    """Eine Gruppierungsachse."""

    field: str
    # Nur fuer Datumsfelder erlaubt; ohne Angabe gilt 'month'.
    granularity: DateGranularity | None = None

    @property
    def key(self) -> str:
        return f"{self.field}:{self.granularity}" if self.granularity else self.field


class MeasureRef(BaseModel):
    """Eine Kennzahl."""

    field: str = "amount"
    aggregate: Aggregate

    @property
    def key(self) -> str:
        return f"{self.aggregate}_{self.field}"


class Condition(BaseModel):
    """Ein Blatt im Filterbaum."""

    field: str
    op: CompareOp
    # Skalar, Liste (in/not_in) oder Zweierliste (between). Bei is_null /
    # is_not_null ohne Bedeutung.
    value: AnalyticsScalar | list[AnalyticsScalar] = None


class FilterNode(BaseModel):
    """Filterbaum: entweder genau ein Blatt oder genau eine Verknuepfung.

    Die Alternative - eine diskriminierte Union - erzeugt im generierten
    TypeScript-Vertrag deutlich unhandlichere Typen; die Invariante wird
    deshalb hier per Validator gehalten."""

    condition: Condition | None = None
    all_of: list[FilterNode] | None = None
    any_of: list[FilterNode] | None = None
    not_of: FilterNode | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> FilterNode:
        set_fields = [
            f
            for f in ("condition", "all_of", "any_of", "not_of")
            if getattr(self, f) is not None
        ]
        if len(set_fields) != 1:
            raise ValueError(
                "Ein Filterknoten traegt genau eines von condition/all_of/any_of/not_of."
            )
        if self.all_of is not None and not self.all_of:
            raise ValueError("all_of darf nicht leer sein.")
        if self.any_of is not None and not self.any_of:
            raise ValueError("any_of darf nicht leer sein.")
        return self


class PeriodSpec(BaseModel):
    """Zeitraum als eigene Angabe statt als Filter.

    Relative Zeitraeume ("letzte 12 Monate") gehoeren nicht in den
    Filterbaum: sie muessen serverseitig aufgeloest werden, damit eine
    gespeicherte Ansicht morgen etwas anderes bedeutet als heute - und damit
    die KI keinen Datumsbereich ausrechnen muss."""

    kind: Literal[
        "all_time",
        "current_month",
        "last_n_months",
        "year_to_date",
        "year",
        "custom",
    ] = "all_time"
    n: Annotated[int | None, Field(default=None, ge=1, le=120)] = None
    year: Annotated[int | None, Field(default=None, ge=1900, le=2200)] = None
    from_date: date | None = None
    to_date: date | None = None

    @model_validator(mode="after")
    def _consistent(self) -> PeriodSpec:
        if self.kind == "last_n_months" and self.n is None:
            raise ValueError("kind=last_n_months verlangt n.")
        if self.kind == "year" and self.year is None:
            raise ValueError("kind=year verlangt year.")
        if self.kind == "custom" and self.from_date is None and self.to_date is None:
            raise ValueError("kind=custom verlangt from_date und/oder to_date.")
        if (
            self.from_date is not None
            and self.to_date is not None
            and self.from_date > self.to_date
        ):
            raise ValueError("from_date liegt nach to_date.")
        return self


class OrderRef(BaseModel):
    """Sortierung. `key` verweist auf eine Dimension oder Kennzahl der
    Abfrage - nicht auf eine beliebige Spalte."""

    key: str
    direction: Literal["asc", "desc"] = "desc"


class TopNSpec(BaseModel):
    """Begrenzung mit sichtbarem Rest.

    `include_rest` fasst alles jenseits der Grenze zu einer Zeile zusammen,
    statt es wegzulassen. Damit hat ein Diagramm dieselbe Gesamtsumme wie die
    zugehoerige Tabelle - die haeufigste Ursache dafuer, dass beide sich zu
    widersprechen scheinen."""

    limit: Annotated[int, Field(ge=1, le=200)]
    include_rest: bool = True
    rest_label: str = "Übrige"


class AnalyticsQuery(BaseModel):
    """Eine vollstaendige Auswertung."""

    grain: Grain = "aggregate"

    group_by: list[DimensionRef] = []
    measures: list[MeasureRef] = []
    # Nur bei grain=rows: welche Felder die Zeilen tragen sollen. Leer =
    # Standardsatz aus dem Feldkatalog.
    fields: list[str] = []

    period: PeriodSpec = PeriodSpec()
    # Ein Feld ueber Empfaenger, Verwendungszweck und Notizen. Bewusst
    # getrennt vom Filterbaum: es ist die haeufigste Eingabe ueberhaupt, und
    # die abgedeckten Spalten sollen serverseitig festliegen statt vom Client
    # zusammengesetzt zu werden.
    search: str | None = None
    where: FilterNode | None = None
    having: FilterNode | None = None
    order_by: list[OrderRef] = []

    limit: Annotated[int | None, Field(default=None, ge=1, le=5000)] = None
    top_n: TopNSpec | None = None
    # Keyset-Paginierung bei grain=rows (undurchsichtiger Cursor aus der
    # letzten Zeile; kein Offset, damit tiefe Seiten nicht teurer werden).
    cursor: str | None = None

    # Interne Umbuchungen verzerren jede Auswertung, deshalb standardmaessig
    # heraus. Der Zustand wird an jeder Auswertung angezeigt, nicht versteckt.
    include_transfers: bool = False
    # Historische Zuordnungen auf inzwischen archivierte Kategorien sind der
    # Normalfall und duerfen in Auswertungen nicht verschwinden
    # (KATEGORISIERUNGSPLAN.md, "Historie, Budgets und Berichte").
    include_archived_categories: bool = True
    # Off-Budget-Konten (Broker, Immobilie) gehoeren nicht in eine
    # Ausgabenauswertung.
    only_on_budget: bool = False

    @model_validator(mode="after")
    def _grain_consistent(self) -> AnalyticsQuery:
        if self.grain == "aggregate":
            if not self.measures:
                raise ValueError("grain=aggregate verlangt mindestens eine Kennzahl.")
            if self.fields:
                raise ValueError("fields gilt nur fuer grain=rows.")
        else:
            if self.group_by or self.measures:
                raise ValueError("group_by/measures gelten nur fuer grain=aggregate.")
            if self.top_n is not None:
                raise ValueError("top_n gilt nur fuer grain=aggregate.")
        return self


# --------------------------------------------------------------------------
# Antworten
# --------------------------------------------------------------------------


class ColumnMeta(BaseModel):
    """Beschreibt eine Ergebnisspalte, damit Clients ohne Namensraten
    formatieren und Diagrammrollen zuordnen koennen."""

    key: str
    label: str
    kind: ColumnKind
    # true fuer Geldbetraege in Minor Units - die Clients formatieren sie als
    # Euro statt als blanke Zahl.
    is_money: bool = False


class AppliedLimit(BaseModel):
    """Eine tatsaechlich angewandte Grenze.

    Jede Kuerzung wird ausgewiesen. Eine stillschweigend gekuerzte Auswertung
    liest sich wie eine vollstaendige - das ist genau der Fehler, den das
    heutige feste `limit: 200` im Buchungsregister macht."""

    kind: Literal["top_n", "row_limit", "facet_cap", "group_cap"]
    limit: int
    dropped_label: str | None = None


class FieldMeta(BaseModel):
    """Ein Feld des Katalogs - versorgt Explorer-UI und KI-Prompt aus
    derselben Quelle."""

    key: str
    label: str
    kind: ColumnKind
    is_money: bool = False
    groupable: bool = False
    filterable: bool = True
    aggregates: list[Aggregate] = []
    # Nur bei kind=cat mit ueberschaubarem Wertevorrat: Vorschlagsliste fuer
    # den Filter, ohne Facettenabfrage.
    enum_values: list[str] | None = None


class FieldCatalog(BaseModel):
    fields: list[FieldMeta]
    default_row_fields: list[str]


class AnalyticsResult(BaseModel):
    # Damit die Oberflaeche nicht die Abfrage mitschleppen muss, um zu
    # wissen, was sie bekommen hat.
    grain: Grain
    columns: list[ColumnMeta]
    # Zeilen als Objekte (key -> Wert). Bewusst nicht als Arrays: die
    # Spaltenzuordnung bleibt dann auch bei Typwechsel eines Diagramms stabil.
    rows: list[dict[str, AnalyticsScalar]]
    # Gesamtzeile ueber die gesamte Filtermenge, nicht nur die gelieferten
    # Zeilen - Grundlage fuer Anteile und fuer die Invariante
    # "Drilldown-Summe == Aggregat der Ebene darueber".
    total_row: dict[str, AnalyticsScalar] | None = None
    row_count: int
    truncated: bool = False
    applied_limits: list[AppliedLimit] = []
    next_cursor: str | None = None
    # Stabiler Hash der normalisierten Abfrage: Cache-Schluessel, Referenz in
    # gespeicherten Ansichten und Beleg in KI-Antworten ("diese Zahl stammt
    # aus Abfrage a7f3...").
    query_id: str
    # Aufgeloester Zeitraum, damit die Oberflaeche zeigen kann, worueber
    # tatsaechlich gerechnet wurde.
    period_from: date | None = None
    period_to: date | None = None
    months_covered: int


class ValidationIssue(BaseModel):
    code: Literal[
        "field_unknown",
        "field_not_groupable",
        "aggregate_not_allowed",
        "granularity_not_allowed",
        "operator_not_allowed",
        "value_invalid",
        "order_key_unknown",
        "grain_mismatch",
    ]
    path: str
    message: str


class ValidationResult(BaseModel):
    ok: bool
    issues: list[ValidationIssue] = []
    # Nur zur Anzeige: das kompilierte SQL mit Platzhaltern, damit der Nutzer
    # (und die KI-Werkbank) sehen kann, was ausgefuehrt wuerde. Enthaelt
    # niemals Werte.
    sql_preview: str | None = None


class FacetValue(BaseModel):
    value: str | None
    label: str
    count: int


class FacetResult(BaseModel):
    field: str
    values: list[FacetValue]
    capped: bool
    total_distinct: int | None = None


class FacetRequest(BaseModel):
    """Werte einer Dimension unter Beruecksichtigung aller UEBRIGEN Filter.

    Genau dieses Verhalten kennen Nutzer aus Excel: die Werteliste einer
    Spalte respektiert die anderen Filter, aber nicht sich selbst. Deshalb
    traegt der Request die vollstaendige Abfrage und das Feld getrennt - der
    Dienst entfernt die Bedingungen dieses Feldes selbst."""

    field: str
    query: AnalyticsQuery
    search: str | None = None
    limit: Annotated[int, Field(default=200, ge=1, le=2000)] = 200
