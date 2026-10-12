"""Der Aggregationsdienst (docs/ANALYSE-EXPLORER-PLAN.md, Baustein B).

Die einzige Stelle, an der aus einer `AnalyticsQuery` SQL wird. Explorer,
Diagramme, Drilldown, Berichte und der spaetere KI-Assistent lesen
ausschliesslich hierueber.

Sicherheitsprinzip: Feld-, Aggregat-, Operator- und Sortierbezeichner
stammen ausschliesslich aus dem Katalog bzw. den Literal-Unions in
`app/schemas/analytics.py`. In das erzeugte SQL gelangt aus der Anfrage
NICHTS ausser gebundenen Parametern - auch kein Suchtext, kein
Feldname und keine Sortierrichtung. Ein unbekannter Bezeichner ist ein
Validierungsfehler mit maschinenlesbarem Code, kein stiller Fallback.

Basis jeder Auswertung ist die Split-Ebene: Kategorien haengen an
`transaction_splits`, und nur so summieren aufgeteilte Buchungen korrekt.
`transactions` liefert Datum, Konto, Rohtext und Herkunft dazu.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.analytics import (
    Aggregate,
    AnalyticsQuery,
    AnalyticsResult,
    AppliedLimit,
    ColumnKind,
    ColumnMeta,
    Condition,
    DimensionRef,
    FacetRequest,
    FacetResult,
    FacetValue,
    FieldCatalog,
    FieldMeta,
    FilterNode,
    MeasureRef,
    ValidationIssue,
    ValidationResult,
)
from app.services.cashflow_semantics import CASHFLOW_ROLE_SQL

# Harte Obergrenzen. Sie werden nie stillschweigend angewandt - jede
# greifende Grenze landet als AppliedLimit in der Antwort.
MAX_GROUP_ROWS: Final = 5000
MAX_ROW_ROWS: Final = 2000
DEFAULT_ROW_LIMIT: Final = 100

_NUMERIC_AGGREGATES: Final[tuple[Aggregate, ...]] = (
    "sum",
    "avg",
    "median",
    "min",
    "max",
    "abs_sum",
    "count",
    "count_distinct",
    "avg_per_month",
)
_ANY_AGGREGATES: Final[tuple[Aggregate, ...]] = ("count", "count_distinct")


@dataclass(frozen=True)
class FieldDef:
    """Ein Feld des Katalogs samt seiner SQL-Entsprechung.

    `sql` ist ein konstanter, im Code definierter Ausdruck - niemals etwas,
    das aus der Anfrage stammt."""

    key: str
    label: str
    kind: ColumnKind
    sql: str
    is_money: bool = False
    groupable: bool = True
    filterable: bool = True
    aggregates: tuple[Aggregate, ...] = ()
    enum_values: tuple[str, ...] | None = None
    # Zusaetzlicher Ausdruck fuer den Anzeigetext einer Dimension, wenn der
    # Gruppierungsschluessel eine ID ist (z. B. category_id -> Kategoriename).
    label_sql: str | None = None


# ---------------------------------------------------------------------------
# Feldkatalog
# ---------------------------------------------------------------------------
#
# Die Bezeichner sind fachlich und stabil; sie entkoppeln die Abfrage von der
# physischen Tabellenstruktur. Wird eine Spalte umbenannt, aendert sich hier
# genau eine Zeile und keine einzige gespeicherte Ansicht.

_FIELDS: Final[tuple[FieldDef, ...]] = (
    # --- Zeit -------------------------------------------------------------
    FieldDef("date", "Datum", "date", "t.booked_date"),
    FieldDef("value_date", "Wertstellung", "date", "t.value_date"),
    FieldDef(
        "weekday",
        "Wochentag",
        "cat",
        # ISO: 1 = Montag. Als Zahl gruppiert, damit die Sortierung stimmt;
        # das Label kommt aus der Oberflaeche.
        "extract(isodow from t.booked_date)::int",
    ),
    # --- Betrag -----------------------------------------------------------
    FieldDef(
        "amount",
        "Betrag",
        "num",
        "s.amount_minor",
        is_money=True,
        groupable=False,
        aggregates=_NUMERIC_AGGREGATES,
    ),
    FieldDef(
        "direction",
        "Richtung",
        "cat",
        "case when s.amount_minor >= 0 then 'income' else 'expense' end",
        enum_values=("income", "expense"),
    ),
    FieldDef(
        "cashflow_role",
        "Cashflow-Zuordnung",
        "cat",
        CASHFLOW_ROLE_SQL,
        enum_values=("income", "expense"),
    ),
    # --- Kategorie --------------------------------------------------------
    FieldDef(
        "category",
        "Kategorie",
        "cat",
        "s.category_id::text",
        label_sql="c.name",
    ),
    FieldDef(
        "category_group",
        "Kategoriegruppe",
        "cat",
        "c.group_id::text",
        label_sql="cg.name",
    ),
    FieldDef(
        "budget_bucket",
        "Budgetbereich",
        "cat",
        "c.budget_bucket",
        enum_values=("needs", "wants", "savings"),
    ),
    FieldDef(
        "category_source",
        "Herkunft der Kategorie",
        "cat",
        "s.category_source",
        enum_values=("unset", "unknown", "manual", "import", "rule", "ai", "history"),
    ),
    FieldDef("category_rule", "Zugeordnet durch Regel", "cat", "s.category_rule_id::text"),
    # --- Konto ------------------------------------------------------------
    FieldDef("account", "Konto", "cat", "t.account_id::text", label_sql="a.name"),
    FieldDef(
        "account_type",
        "Kontoart",
        "cat",
        "a.account_type::text",
        enum_values=(
            "checking",
            "credit_card",
            "savings",
            "cash",
            "brokerage",
            "loan",
            "real_estate",
            "other",
        ),
    ),
    FieldDef("on_budget", "On-Budget", "bool", "a.on_budget"),
    # --- Gegenpartei und Texte -------------------------------------------
    FieldDef("payee", "Zahlungsempfänger", "cat", "coalesce(t.payee_label, t.payee_raw)"),
    FieldDef("payee_raw", "Empfänger laut Bank", "cat", "t.payee_raw", groupable=False),
    FieldDef("purpose", "Verwendungszweck", "cat", "t.purpose_raw", groupable=False),
    FieldDef("note", "Notiz", "cat", "coalesce(s.note, t.note)", groupable=False),
    FieldDef(
        "bank_transaction_code",
        "Buchungsart",
        "cat",
        "t.bank_transaction_code",
    ),
    # --- Merkmale ---------------------------------------------------------
    FieldDef(
        "status",
        "Status",
        "cat",
        "t.status::text",
        enum_values=("pending", "booked", "reconciled"),
    ),
    FieldDef(
        "source",
        "Erfassungsart",
        "cat",
        "t.source::text",
        enum_values=("manual", "import", "bank_sync"),
    ),
    FieldDef("is_transfer", "Umbuchung", "bool", "t.is_transfer"),
    FieldDef("goal", "Sparziel", "cat", "s.goal_id::text"),
    FieldDef("import_run", "Importlauf", "cat", "t.import_run_id::text"),
    # --- Vorhaben (Plan VORHABEN-GROSSINVESTITIONEN-PLAN.md 11.6, P5) -----
    # NUR filterbar, nie gruppierbar: project_cost_actuals haengt (wie
    # transaction_splits) unabhaengig an transaction_id, nicht an split_id -
    # ein JOIN auf project_cost_actuals in _FROM_SQL wuerde also fuer eine
    # Buchung mit z. B. 2 Splits und 2 Projektzuordnungen 4 statt 2 Zeilen
    # erzeugen und amount_minor damit verfaelschen (Plan 11.6: "Keine
    # Vervielfachung der Transaktionssumme bei mehreren Projektzuordnungen").
    # Als skalare korrelierte Subquery in einer WHERE-Bedingung kann daraus
    # dagegen nie eine zusaetzliche Zeile entstehen - eine Bedingung schliesst
    # eine Zeile nur ein oder aus, multipliziert sie nie. Eine auf mehrere
    # Vorhaben verteilte Buchung (Rand- statt Regelfall) liefert hier bewusst
    # nur die ERSTE Zuordnung (`limit 1`) - fuer Filterung ("zeig mir Buchungen
    # zu Vorhaben X") ausreichend, fuer eine Summenbildung waere das falsch,
    # weshalb dieses Feld nicht gruppierbar ist.
    FieldDef(
        "project",
        "Vorhaben",
        "cat",
        "(select mp.id::text from project_cost_actuals pca "
        "join project_cost_items pci on pci.id = pca.cost_item_id "
        "join project_scenarios psc on psc.id = pci.scenario_id "
        "join major_projects mp on mp.id = psc.project_id "
        "where pca.transaction_id = t.id limit 1)",
        groupable=False,
    ),
    FieldDef(
        "project_cost_item",
        "Projekt-Kostenposition",
        "cat",
        "(select pca.cost_item_id::text from project_cost_actuals pca "
        "where pca.transaction_id = t.id limit 1)",
        groupable=False,
    ),
    # --- Technisch (nur fuer grain=rows) ---------------------------------
    FieldDef(
        "transaction_id", "Buchung", "cat", "t.id::text", groupable=False, filterable=True
    ),
    FieldDef("split_id", "Split", "cat", "s.id::text", groupable=False),
    FieldDef(
        "currency", "Währung", "cat", "t.currency", enum_values=("EUR",), groupable=True
    ),
)

_FIELD_BY_KEY: Final[dict[str, FieldDef]] = {f.key: f for f in _FIELDS}

# Standardspalten einer Zeilenabfrage.
DEFAULT_ROW_FIELDS: Final[tuple[str, ...]] = (
    "date",
    "account",
    "payee",
    "purpose",
    "category",
    "amount",
    "category_source",
    "status",
)

# Felder, die die Volltextsuche abdeckt. Bewusst eine feste Liste im Code -
# der Suchbegriff selbst wird ausschliesslich gebunden.
_SEARCH_FIELDS: Final[tuple[str, ...]] = (
    "t.payee_label",
    "t.payee_raw",
    "t.purpose_raw",
    "t.note",
    "s.note",
)

# Basis jeder Auswertung. Der LEFT JOIN auf categories ist bewusst OHNE
# Archivfilter: eine historische Zuordnung auf eine inzwischen archivierte
# Kategorie muss sichtbar bleiben, sonst verschwinden Betraege aus der
# Vergangenheit (KATEGORISIERUNGSPLAN.md, "Historie, Budgets und Berichte").
_FROM_SQL: Final = """
from transaction_splits s
join transactions t on t.id = s.transaction_id
join accounts a on a.id = t.account_id
left join categories c on c.id = s.category_id
left join category_groups cg on cg.id = c.group_id
"""


class QueryCompilationError(Exception):
    """Die Abfrage ist nicht kompilierbar. Traegt die strukturierten
    Einzelbefunde, damit die Route sie unveraendert ausliefern kann."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in issues))


@dataclass
class _Compiled:
    sql: str
    params: dict[str, Any]
    columns: list[ColumnMeta]
    period_from: date | None
    period_to: date | None
    months_covered: int
    group_keys: list[str] = dataclass_field(default_factory=list)
    measure_keys: list[str] = dataclass_field(default_factory=list)


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------


def get_field_catalog() -> FieldCatalog:
    """Der Feldkatalog - dieselbe Quelle fuer Explorer-UI und KI-Prompt.

    Zwei getrennte Listen waeren die sichere Art, den Prompt gegen einen
    veralteten Katalog laufen zu lassen."""
    return FieldCatalog(
        fields=[
            FieldMeta(
                key=f.key,
                label=f.label,
                kind=f.kind,
                is_money=f.is_money,
                groupable=f.groupable,
                filterable=f.filterable,
                aggregates=list(f.aggregates) if f.aggregates else list(_ANY_AGGREGATES),
                enum_values=list(f.enum_values) if f.enum_values else None,
            )
            for f in _FIELDS
        ],
        default_row_fields=list(DEFAULT_ROW_FIELDS),
    )


# ---------------------------------------------------------------------------
# Zeitraum
# ---------------------------------------------------------------------------


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _shift_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def resolve_period(query: AnalyticsQuery, today: date) -> tuple[date | None, date | None]:
    """Loest den Zeitraum serverseitig auf.

    `today` wird hereingereicht statt hier gelesen, damit Tests reproduzierbar
    bleiben und eine gespeicherte Ansicht nicht davon abhaengt, welcher Prozess
    sie ausgewertet hat."""
    period = query.period
    if period.kind == "all_time":
        return None, None
    if period.kind == "current_month":
        return _month_start(today), _month_end(today)
    if period.kind == "last_n_months":
        # n Monate EINSCHLIESSLICH des laufenden.
        start = _shift_months(_month_start(today), -(period.n or 1) + 1)
        return start, _month_end(today)
    if period.kind == "year_to_date":
        return date(today.year, 1, 1), _month_end(today)
    if period.kind == "year":
        year = period.year or today.year
        return date(year, 1, 1), date(year, 12, 31)
    return period.from_date, period.to_date


def _months_between(start: date | None, end: date | None) -> int:
    """Anzahl angefangener Kalendermonate im Zeitraum - der Nenner fuer
    avg_per_month. Ohne Zeitraum ist er unbekannt und wird als 1 gefuehrt,
    damit avg_per_month dann schlicht der Summe entspricht statt eine
    erfundene Zahl zu liefern."""
    if start is None or end is None:
        return 1
    return max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)


# ---------------------------------------------------------------------------
# Kompiler
# ---------------------------------------------------------------------------


class _ParamBag:
    """Sammelt gebundene Parameter und vergibt eindeutige Namen.

    Der einzige Weg, auf dem ein Wert aus der Anfrage in das Statement
    gelangt."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self._counter = 0

    def bind(self, value: Any) -> str:
        self._counter += 1
        name = f"p{self._counter}"
        self.values[name] = value
        return f":{name}"


def _field_or_issue(
    key: str, path: str, issues: list[ValidationIssue]
) -> FieldDef | None:
    field_def = _FIELD_BY_KEY.get(key)
    if field_def is None:
        issues.append(
            ValidationIssue(
                code="field_unknown",
                path=path,
                message=f"Unbekanntes Feld '{key}'.",
            )
        )
    return field_def


def _dimension_sql(dim: DimensionRef, field_def: FieldDef) -> str:
    if field_def.kind != "date":
        return field_def.sql
    granularity = dim.granularity or "month"
    # date_trunc erwartet einen Literaltext; er stammt aus der Literal-Union
    # DateGranularity, nicht aus freier Eingabe.
    return f"date_trunc('{granularity}', {field_def.sql})::date"


def _measure_sql(measure: MeasureRef, field_def: FieldDef, month_count: int) -> str:
    expr = field_def.sql
    aggregate = measure.aggregate
    if aggregate == "count":
        return "count(*)"
    if aggregate == "count_distinct":
        return f"count(distinct {expr})"
    # ::bigint ist nicht kosmetisch: sum() ueber bigint liefert in Postgres
    # numeric, psycopg macht daraus Decimal, und Pydantic serialisiert Decimal
    # als JSON-String. Die Clients bekaemen "-7350" statt -7350 und koennten
    # nicht damit rechnen. Dieselbe Falle ist in budget_service bereits mit
    # einem int()-Cast entschaerft.
    if aggregate == "sum":
        return f"coalesce(sum({expr}), 0)::bigint"
    if aggregate == "abs_sum":
        return f"coalesce(sum(abs({expr})), 0)::bigint"
    if aggregate == "avg":
        return f"coalesce(round(avg({expr})), 0)::bigint"
    if aggregate == "median":
        return (
            f"coalesce(round(percentile_cont(0.5) within group (order by {expr})), 0)::bigint"
        )
    if aggregate == "min":
        return f"coalesce(min({expr}), 0)"
    if aggregate == "max":
        return f"coalesce(max({expr}), 0)"
    if aggregate == "avg_per_month":
        # month_count stammt aus dem serverseitig aufgeloesten Zeitraum, nicht
        # aus der Anfrage - deshalb als Literal unbedenklich.
        return f"coalesce(round(sum({expr})::numeric / {month_count}), 0)::bigint"
    raise AssertionError(f"Unbehandeltes Aggregat: {aggregate}")


def _compile_condition(
    condition: Condition, path: str, params: _ParamBag, issues: list[ValidationIssue]
) -> str | None:
    field_def = _field_or_issue(condition.field, path, issues)
    if field_def is None:
        return None
    if not field_def.filterable:
        issues.append(
            ValidationIssue(
                code="operator_not_allowed",
                path=path,
                message=f"Feld '{condition.field}' ist nicht filterbar.",
            )
        )
        return None

    expr = field_def.sql
    op = condition.op
    value = condition.value

    if op in ("is_null", "is_not_null"):
        return f"({expr} is {'null' if op == 'is_null' else 'not null'})"

    if op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            issues.append(
                ValidationIssue(
                    code="value_invalid",
                    path=path,
                    message=f"Operator '{op}' verlangt eine nicht-leere Liste.",
                )
            )
            return None
        # NULL laesst sich mit IN nicht ausdruecken; "Ohne Kategorie" ist aber
        # ein normaler Filterwunsch, deshalb hier ausdruecklich behandelt.
        has_null = any(v is None for v in value)
        concrete = [v for v in value if v is not None]
        parts: list[str] = []
        if concrete:
            placeholders = ", ".join(params.bind(_as_text(v)) for v in concrete)
            parts.append(f"{expr}::text in ({placeholders})")
        if has_null:
            parts.append(f"{expr} is null")
        if not parts:
            return None
        joined = " or ".join(parts)
        return f"(not ({joined}))" if op == "not_in" else f"({joined})"

    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            issues.append(
                ValidationIssue(
                    code="value_invalid",
                    path=path,
                    message="Operator 'between' verlangt genau zwei Werte.",
                )
            )
            return None
        low = params.bind(_coerce(field_def, value[0]))
        high = params.bind(_coerce(field_def, value[1]))
        return f"({expr} between {low} and {high})"

    if op in ("contains", "starts_with"):
        if field_def.kind not in ("cat",):
            issues.append(
                ValidationIssue(
                    code="operator_not_allowed",
                    path=path,
                    message=f"Operator '{op}' gilt nur fuer Textfelder.",
                )
            )
            return None
        raw = "" if value is None else str(value)
        pattern = f"{_escape_like(raw)}%" if op == "starts_with" else f"%{_escape_like(raw)}%"
        return f"({expr} ilike {params.bind(pattern)} escape '\\')"

    sql_op = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[op]
    if value is None:
        # `eq null` ist ein haeufiger Client-Fehler; ihn als IS NULL zu deuten
        # waere Raten. Lieber ein klarer Befund.
        issues.append(
            ValidationIssue(
                code="value_invalid",
                path=path,
                message=f"Operator '{op}' braucht einen Wert; fuer NULL bitte is_null.",
            )
        )
        return None
    return f"({expr} {sql_op} {params.bind(_coerce(field_def, value))})"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _coerce(field_def: FieldDef, value: Any) -> Any:
    """Bringt einen Filterwert in die Form, die die Spalte erwartet.

    Ein als String gelieferter Betrag ist der Normalfall aus JSON - ihn
    stillschweigend als Text zu vergleichen wuerde falsche Ergebnisse
    liefern statt eines Fehlers."""
    if field_def.kind == "num":
        if isinstance(value, bool):
            raise ValueError("Boolescher Wert fuer ein Zahlenfeld.")
        return int(value)
    if field_def.kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")
    if field_def.kind == "date":
        return date.fromisoformat(value) if isinstance(value, str) else value
    return _as_text(value)


def _compile_filter(
    node: FilterNode | None, path: str, params: _ParamBag, issues: list[ValidationIssue]
) -> str | None:
    if node is None:
        return None
    if node.condition is not None:
        try:
            return _compile_condition(node.condition, path, params, issues)
        except (ValueError, TypeError) as exc:
            issues.append(
                ValidationIssue(code="value_invalid", path=path, message=str(exc))
            )
            return None
    if node.all_of is not None:
        parts = [
            p
            for i, child in enumerate(node.all_of)
            if (p := _compile_filter(child, f"{path}.all_of[{i}]", params, issues))
        ]
        return f"({' and '.join(parts)})" if parts else None
    if node.any_of is not None:
        parts = [
            p
            for i, child in enumerate(node.any_of)
            if (p := _compile_filter(child, f"{path}.any_of[{i}]", params, issues))
        ]
        return f"({' or '.join(parts)})" if parts else None
    inner = _compile_filter(node.not_of, f"{path}.not_of", params, issues)
    return f"(not {inner})" if inner else None


def _base_conditions(
    query: AnalyticsQuery,
    household_id: UUID,
    params: _ParamBag,
    period_from: date | None,
    period_to: date | None,
    issues: list[ValidationIssue],
    *,
    skip_field: str | None = None,
) -> list[str]:
    """Die Bedingungen, die unabhaengig vom Filterbaum immer gelten."""
    conditions = [
        f"t.household_id = {params.bind(str(household_id))}",
        "not t.is_archived",
    ]
    if not query.include_transfers:
        conditions.append("not t.is_transfer")
    if query.only_on_budget:
        conditions.append("a.on_budget")
    if not query.include_archived_categories:
        conditions.append("(c.id is null or not c.is_archived)")
    if period_from is not None:
        conditions.append(f"t.booked_date >= {params.bind(period_from)}")
    if period_to is not None:
        conditions.append(f"t.booked_date <= {params.bind(period_to)}")

    if query.search and query.search.strip():
        # Ein einziger gebundener Parameter fuer alle Spalten: der Suchtext
        # gelangt nie in den Statement-Text, und die abgedeckten Spalten
        # liegen im Code fest.
        placeholder = params.bind(f"%{_escape_like(query.search.strip())}%")
        parts = [f"{column} ilike {placeholder} escape '\\'" for column in _SEARCH_FIELDS]
        conditions.append(f"({' or '.join(parts)})")

    where_sql = _compile_filter(
        _without_field(query.where, skip_field) if skip_field else query.where,
        "where",
        params,
        issues,
    )
    if where_sql:
        conditions.append(where_sql)
    return conditions


def _without_field(node: FilterNode | None, field_key: str) -> FilterNode | None:
    """Entfernt alle Bedingungen zu einem Feld aus dem Baum.

    Grundlage der kontextabhaengigen Facetten: die Werteliste einer Spalte
    beruecksichtigt alle uebrigen Filter, aber nicht sich selbst - genau das
    Verhalten, das Nutzer aus Excel kennen."""
    if node is None:
        return None
    if node.condition is not None:
        return None if node.condition.field == field_key else node
    if node.all_of is not None:
        kept = [c for child in node.all_of if (c := _without_field(child, field_key))]
        return FilterNode(all_of=kept) if kept else None
    if node.any_of is not None:
        kept = [c for child in node.any_of if (c := _without_field(child, field_key))]
        return FilterNode(any_of=kept) if kept else None
    inner = _without_field(node.not_of, field_key)
    return FilterNode(not_of=inner) if inner else None


def compute_query_id(query: AnalyticsQuery, household_id: UUID) -> str:
    """Stabiler Hash der normalisierten Abfrage."""
    payload = json.dumps(
        {"household": str(household_id), "query": query.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compile_query(
    query: AnalyticsQuery, household_id: UUID, today: date
) -> _Compiled:
    """Uebersetzt eine Abfrage in parametrisiertes SQL.

    Wirft QueryCompilationError mit allen Einzelbefunden statt beim ersten
    Problem abzubrechen - eine Oberflaeche kann dann alle Fehler auf einmal
    anzeigen."""
    issues: list[ValidationIssue] = []
    params = _ParamBag()
    period_from, period_to = resolve_period(query, today)
    months = _months_between(period_from, period_to)

    conditions = _base_conditions(
        query, household_id, params, period_from, period_to, issues
    )
    where_clause = " and ".join(conditions)

    if query.grain == "rows":
        compiled = _compile_rows(query, params, where_clause, issues)
    else:
        compiled = _compile_aggregate(query, params, where_clause, months, issues)

    if issues:
        raise QueryCompilationError(issues)

    compiled.period_from = period_from
    compiled.period_to = period_to
    compiled.months_covered = months
    return compiled


def _compile_rows(
    query: AnalyticsQuery,
    params: _ParamBag,
    where_clause: str,
    issues: list[ValidationIssue],
) -> _Compiled:
    keys = list(query.fields) if query.fields else list(DEFAULT_ROW_FIELDS)
    # split_id ist immer dabei: er traegt die Keyset-Paginierung und ist der
    # stabile Schluessel jeder Zeile.
    if "split_id" not in keys:
        keys.append("split_id")
    if "transaction_id" not in keys:
        keys.append("transaction_id")

    select_parts: list[str] = []
    columns: list[ColumnMeta] = []
    for key in keys:
        field_def = _field_or_issue(key, f"fields.{key}", issues)
        if field_def is None:
            continue
        select_parts.append(f'{field_def.sql} as "{field_def.key}"')
        columns.append(
            ColumnMeta(
                key=field_def.key,
                label=field_def.label,
                kind=field_def.kind,
                is_money=field_def.is_money,
            )
        )
        if field_def.label_sql:
            select_parts.append(f'{field_def.label_sql} as "{field_def.key}_label"')
            columns.append(
                ColumnMeta(
                    key=f"{field_def.key}_label",
                    label=f"{field_def.label} (Name)",
                    kind="cat",
                )
            )

    order_sql = _compile_row_order(query, issues)
    cursor_clause = ""
    if query.cursor:
        # Keyset statt OFFSET: tiefe Seiten kosten dadurch nicht mehr als
        # frueh e. Der Cursor traegt Datum und Split-ID der letzten Zeile.
        try:
            cursor_date, cursor_split = _decode_cursor(query.cursor)
            # cast(... as uuid) statt ::uuid: SQLAlchemys text() erkennt einen
            # Bind-Parameter unmittelbar vor '::' nicht und laesst ihn
            # unersetzt im Statement stehen.
            cursor_clause = (
                f" and (t.booked_date, s.id) < "
                f"({params.bind(cursor_date)}, cast({params.bind(cursor_split)} as uuid))"
            )
        except ValueError:
            issues.append(
                ValidationIssue(
                    code="value_invalid", path="cursor", message="Cursor ist ungueltig."
                )
            )

    limit = min(query.limit or DEFAULT_ROW_LIMIT, MAX_ROW_ROWS)
    sql = (
        f"select {', '.join(select_parts)} {_FROM_SQL} "
        f"where {where_clause}{cursor_clause} {order_sql} limit {limit + 1}"
    )
    return _Compiled(
        sql=sql, params=params.values, columns=columns, period_from=None,
        period_to=None, months_covered=1,
    )


def _compile_row_order(query: AnalyticsQuery, issues: list[ValidationIssue]) -> str:
    """Zeilen sind immer nach Datum und Split-ID sortiert.

    Die zusammengesetzte Sortierung ist keine Kosmetik: ohne einen eindeutigen
    zweiten Schluessel waere die Keyset-Paginierung nicht stabil und einzelne
    Zeilen wuerden zwischen zwei Seiten verschwinden oder doppelt erscheinen."""
    if not query.order_by:
        return "order by t.booked_date desc, s.id desc"
    for i, order in enumerate(query.order_by):
        if order.key not in ("date", "amount"):
            issues.append(
                ValidationIssue(
                    code="order_key_unknown",
                    path=f"order_by[{i}]",
                    message="Zeilenabfragen sortieren nach 'date' oder 'amount'.",
                )
            )
    primary = query.order_by[0]
    direction = "asc" if primary.direction == "asc" else "desc"
    column = "t.booked_date" if primary.key == "date" else "s.amount_minor"
    return f"order by {column} {direction} nulls last, s.id {direction}"


def _encode_cursor(booked_date: Any, split_id: Any) -> str:
    return f"{booked_date}|{split_id}"


def _decode_cursor(cursor: str) -> tuple[date, str]:
    raw_date, _, raw_id = cursor.partition("|")
    if not raw_date or not raw_id:
        raise ValueError("Cursor ist unvollstaendig.")
    return date.fromisoformat(raw_date), str(UUID(raw_id))


def _compile_aggregate(
    query: AnalyticsQuery,
    params: _ParamBag,
    where_clause: str,
    months: int,
    issues: list[ValidationIssue],
) -> _Compiled:
    select_parts: list[str] = []
    group_parts: list[str] = []
    columns: list[ColumnMeta] = []
    group_keys: list[str] = []

    for i, dim in enumerate(query.group_by):
        field_def = _field_or_issue(dim.field, f"group_by[{i}]", issues)
        if field_def is None:
            continue
        if not field_def.groupable:
            issues.append(
                ValidationIssue(
                    code="field_not_groupable",
                    path=f"group_by[{i}]",
                    message=f"Feld '{dim.field}' kann nicht gruppiert werden.",
                )
            )
            continue
        if dim.granularity is not None and field_def.kind != "date":
            issues.append(
                ValidationIssue(
                    code="granularity_not_allowed",
                    path=f"group_by[{i}]",
                    message=f"Feld '{dim.field}' ist kein Datumsfeld.",
                )
            )
            continue

        expr = _dimension_sql(dim, field_def)
        select_parts.append(f'{expr} as "{dim.key}"')
        group_parts.append(expr)
        group_keys.append(dim.key)
        columns.append(
            ColumnMeta(
                key=dim.key,
                label=field_def.label,
                kind=field_def.kind,
                is_money=field_def.is_money,
            )
        )
        if field_def.label_sql:
            select_parts.append(f'min({field_def.label_sql}) as "{dim.key}_label"')
            columns.append(
                ColumnMeta(key=f"{dim.key}_label", label=f"{field_def.label} (Name)", kind="cat")
            )

    measure_keys: list[str] = []
    # Der Ausdruck je Kennzahl wird mitgefuehrt: Postgres erlaubt einen
    # SELECT-Alias in ORDER BY nur nackt, nicht innerhalb eines Ausdrucks -
    # fuer die Standardsortierung nach Betragshoehe (abs(...)) muss der
    # Aggregatausdruck deshalb wiederholt werden.
    measure_sql_by_key: dict[str, str] = {}
    for i, measure in enumerate(query.measures):
        field_def = _field_or_issue(measure.field, f"measures[{i}]", issues)
        if field_def is None:
            continue
        allowed = field_def.aggregates or _ANY_AGGREGATES
        if measure.aggregate not in allowed:
            issues.append(
                ValidationIssue(
                    code="aggregate_not_allowed",
                    path=f"measures[{i}]",
                    message=(
                        f"Aggregat '{measure.aggregate}' ist fuer Feld "
                        f"'{measure.field}' nicht erlaubt."
                    ),
                )
            )
            continue
        expr = _measure_sql(measure, field_def, months)
        select_parts.append(f'{expr} as "{measure.key}"')
        measure_keys.append(measure.key)
        measure_sql_by_key[measure.key] = expr
        columns.append(
            ColumnMeta(
                key=measure.key,
                label=f"{_AGGREGATE_LABELS[measure.aggregate]} {field_def.label}",
                kind="num",
                is_money=field_def.is_money and measure.aggregate != "count",
            )
        )

    if issues:
        raise QueryCompilationError(issues)

    having_sql = _compile_filter(query.having, "having", params, issues)
    order_sql = _compile_aggregate_order(
        query, group_keys, measure_keys, measure_sql_by_key, issues
    )

    group_clause = f" group by {', '.join(group_parts)}" if group_parts else ""
    having_clause = f" having {having_sql}" if having_sql else ""
    limit = min(
        query.top_n.limit if query.top_n else (query.limit or MAX_GROUP_ROWS),
        MAX_GROUP_ROWS,
    )

    sql = (
        f"select {', '.join(select_parts)} {_FROM_SQL} where {where_clause}"
        f"{group_clause}{having_clause} {order_sql} limit {limit + 1}"
    )
    return _Compiled(
        sql=sql,
        params=params.values,
        columns=columns,
        period_from=None,
        period_to=None,
        months_covered=months,
        group_keys=group_keys,
        measure_keys=measure_keys,
    )


_AGGREGATE_LABELS: Final[dict[Aggregate, str]] = {
    "sum": "Summe",
    "avg": "Ø",
    "median": "Median",
    "min": "Minimum",
    "max": "Maximum",
    "abs_sum": "Summe (Betrag)",
    "count": "Anzahl",
    "count_distinct": "Anzahl verschiedener",
    "avg_per_month": "Ø/Monat",
}


def _compile_aggregate_order(
    query: AnalyticsQuery,
    group_keys: list[str],
    measure_keys: list[str],
    measure_sql_by_key: dict[str, str],
    issues: list[ValidationIssue],
) -> str:
    known = set(group_keys) | set(measure_keys)
    parts: list[str] = []
    for i, order in enumerate(query.order_by):
        if order.key not in known:
            issues.append(
                ValidationIssue(
                    code="order_key_unknown",
                    path=f"order_by[{i}]",
                    # Die gueltigen Schluessel gehoeren in die Meldung: nach ihr
                    # korrigiert das KI-Feld seine Abfrage, und ein Modell, das nur
                    # "date" statt "date:month" kennt, wiederholt sonst denselben Fehler.
                    message=(
                        f"'{order.key}' ist weder Dimension noch Kennzahl dieser Abfrage. "
                        f"Gültige Schlüssel: {', '.join(sorted(known))}."
                    ),
                )
            )
            continue
        direction = "asc" if order.direction == "asc" else "desc"
        parts.append(f'"{order.key}" {direction} nulls last')

    if not parts:
        # Ohne Vorgabe nach der ersten Kennzahl absteigend - und zwar nach
        # Betragshoehe: Ausgaben sind negativ gespeichert, eine schlichte
        # Desc-Sortierung stellte sonst die kleinste Belastung nach vorn und
        # top_n schnitte genau die groessten Posten weg.
        if measure_keys:
            parts.append(f"abs({measure_sql_by_key[measure_keys[0]]}) desc nulls last")
        elif group_keys:
            parts.append(f'"{group_keys[0]}" asc')
    return f"order by {', '.join(parts)}" if parts else ""


# ---------------------------------------------------------------------------
# Ausfuehrung
# ---------------------------------------------------------------------------


def validate_query(
    query: AnalyticsQuery, household_id: UUID, today: date
) -> ValidationResult:
    """Kompiliert trocken und meldet strukturierte Fehler, ohne auszufuehren."""
    try:
        compiled = compile_query(query, household_id, today)
    except QueryCompilationError as exc:
        return ValidationResult(ok=False, issues=exc.issues)
    return ValidationResult(ok=True, issues=[], sql_preview=compiled.sql)


async def run_query(
    session: AsyncSession, household_id: UUID, query: AnalyticsQuery, today: date
) -> AnalyticsResult:
    """Fuehrt eine Abfrage aus und liefert Zeilen samt Gesamtzeile."""
    compiled = compile_query(query, household_id, today)
    result = await session.execute(text(compiled.sql), compiled.params)
    raw_rows = [dict(row._mapping) for row in result]

    applied_limits: list[AppliedLimit] = []
    next_cursor: str | None = None

    if query.grain == "rows":
        limit = min(query.limit or DEFAULT_ROW_LIMIT, MAX_ROW_ROWS)
        truncated = len(raw_rows) > limit
        rows = raw_rows[:limit]
        if truncated and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(last.get("date"), last.get("split_id"))
            applied_limits.append(AppliedLimit(kind="row_limit", limit=limit))
        total_row = None
    else:
        limit = min(
            query.top_n.limit if query.top_n else (query.limit or MAX_GROUP_ROWS),
            MAX_GROUP_ROWS,
        )
        truncated = len(raw_rows) > limit
        rows = raw_rows[:limit]
        total_row = await _run_total(session, household_id, query, today)

        if truncated:
            if query.top_n and query.top_n.include_rest and total_row:
                # Die Restzeile wird aus der Gesamtsumme abgeleitet, nicht
                # geschaetzt: dadurch stimmt die Summe der angezeigten Zeilen
                # exakt mit der Gesamtzeile ueberein.
                rows.append(_build_rest_row(query, compiled, rows, total_row))
                applied_limits.append(
                    AppliedLimit(
                        kind="top_n", limit=limit, dropped_label=query.top_n.rest_label
                    )
                )
            else:
                applied_limits.append(AppliedLimit(kind="group_cap", limit=limit))

    return AnalyticsResult(
        grain=query.grain,
        columns=compiled.columns,
        rows=rows,
        total_row=total_row,
        row_count=len(rows),
        truncated=truncated,
        applied_limits=applied_limits,
        next_cursor=next_cursor,
        query_id=compute_query_id(query, household_id),
        period_from=compiled.period_from,
        period_to=compiled.period_to,
        months_covered=compiled.months_covered,
    )


def _build_rest_row(
    query: AnalyticsQuery,
    compiled: _Compiled,
    rows: list[dict[str, Any]],
    total_row: dict[str, Any],
) -> dict[str, Any]:
    assert query.top_n is not None
    rest: dict[str, Any] = {key: None for key in compiled.group_keys}
    if compiled.group_keys:
        rest[compiled.group_keys[0]] = "__rest__"
        rest[f"{compiled.group_keys[0]}_label"] = query.top_n.rest_label
    for key in compiled.measure_keys:
        total = total_row.get(key)
        if isinstance(total, int | float):
            shown = sum(
                r.get(key) or 0 for r in rows if isinstance(r.get(key), int | float)
            )
            rest[key] = total - shown
        else:
            rest[key] = None
    return rest


async def _run_total(
    session: AsyncSession, household_id: UUID, query: AnalyticsQuery, today: date
) -> dict[str, Any] | None:
    """Gesamtzeile ueber die volle Filtermenge - ohne Gruppierung und ohne
    Grenze. Sie ist der Anker fuer Anteile und fuer die Invariante
    "Summe der Drilldown-Ebene == Aggregat der Ebene darueber"."""
    total_query = query.model_copy(
        update={"group_by": [], "order_by": [], "top_n": None, "limit": None}
    )
    compiled = compile_query(total_query, household_id, today)
    result = await session.execute(text(compiled.sql), compiled.params)
    row = result.first()
    return dict(row._mapping) if row is not None else None


async def run_facets(
    session: AsyncSession, household_id: UUID, request: FacetRequest, today: date
) -> FacetResult:
    """Distinkte Werte eines Feldes unter Beruecksichtigung aller UEBRIGEN
    Filter."""
    issues: list[ValidationIssue] = []
    field_def = _field_or_issue(request.field, "field", issues)
    if field_def is None or issues:
        raise QueryCompilationError(issues)
    if not field_def.groupable:
        raise QueryCompilationError(
            [
                ValidationIssue(
                    code="field_not_groupable",
                    path="field",
                    message=f"Feld '{request.field}' hat keine Facetten.",
                )
            ]
        )

    params = _ParamBag()
    period_from, period_to = resolve_period(request.query, today)
    conditions = _base_conditions(
        request.query,
        household_id,
        params,
        period_from,
        period_to,
        issues,
        skip_field=request.field,
    )
    if request.search:
        label_expr = field_def.label_sql or field_def.sql
        pattern = f"%{_escape_like(request.search)}%"
        conditions.append(f"{label_expr} ilike {params.bind(pattern)} escape '\\'")
    if issues:
        raise QueryCompilationError(issues)

    label_expr = field_def.label_sql or field_def.sql
    sql = (
        f'select {field_def.sql} as "value", min({label_expr}) as "label", '
        f'count(*) as "count" {_FROM_SQL} where {" and ".join(conditions)} '
        f"group by {field_def.sql} order by count(*) desc, 1 asc limit {request.limit + 1}"
    )
    result = await session.execute(text(sql), params.values)
    raw = [dict(row._mapping) for row in result]
    capped = len(raw) > request.limit

    values = [
        FacetValue(
            value=None if r["value"] is None else str(r["value"]),
            label=str(r["label"]) if r["label"] is not None else "(ohne Angabe)",
            count=int(r["count"]),
        )
        for r in raw[: request.limit]
    ]
    return FacetResult(
        field=request.field, values=values, capped=capped, total_distinct=None
    )
