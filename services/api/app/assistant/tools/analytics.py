"""Werkzeuge ueber den Analyse-Abfragevertrag (KI-Feld, nur lesend - E5).

describe_fields erklaert Feldkatalog und Abfrageform, query_analytics fuehrt
eine AnalyticsQuery ueber analytics_service aus - denselben Pfad wie Explorer
und Diagramme. Das Modell schreibt nie SQL; der Kompiler laesst nur
Bezeichner aus dem Katalog zu und bindet jeden Wert als Parameter.

Zwei Umrechnungen an der Grenze zum Modell, weil das Modell in Euro denkt
und der Vertrag in Minor Units rechnet:

  * Betragsfilter (where auf Geldfeldern) kommen in Euro und werden vor dem
    Kompilieren in Minor Units umgerechnet - sonst hiesse "amount < -50"
    fuer den Dienst "kleiner als -0,50 EUR".
  * Geldspalten im Ergebnis (ColumnMeta.is_money) gehen als Euro zurueck.

Ungueltige Abfragen werfen nicht, sie gehen mit allen Befunden (Pfad, Code,
Satz) als status rejected an das Modell zurueck - es kann gezielt
nachbessern. `having` ist bewusst nicht freigegeben: der Kompiler wertet es
gegen die Rohfelder (Minor Units, nicht die Aggregate) aus; Gruppen nach
Groesse ordnen und begrenzen geht mit order_by und top_n.
"""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final, get_args

from pydantic import BaseModel, ValidationError

from app.assistant.tools.args import check_keys
from app.assistant.tools.base import (
    ToolArgs,
    ToolContext,
    ToolReply,
    ToolSpec,
    ToolTable,
    euro,
    mask_text,
    object_schema,
    plain,
)
from app.schemas.analytics import (
    Aggregate,
    AnalyticsQuery,
    AnalyticsResult,
    ColumnMeta,
    CompareOp,
    Condition,
    DateGranularity,
    DimensionRef,
    FieldMeta,
    FilterNode,
    Grain,
    MeasureRef,
    OrderRef,
    PeriodSpec,
    TopNSpec,
    ValidationIssue,
)
from app.services import analytics_service
from app.services.analytics_service import QueryCompilationError

# Zellwerte, die IDs sind (Kategorie, Konto, Buchung, Split ...), bleiben
# unmaskiert: mask_pii_text haelt einen rein numerischen letzten UUID-Block
# (12 Ziffern) fuer eine Kontonummer und machte die ID unbrauchbar.
_UUID_TEXT: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)

# Platzhalter der Restzeile bei top_n (analytics_service._build_rest_row).
_REST_KEY: Final = "__rest__"

_KIND_TEXT: Final[dict[str, str]] = {
    "date": "Datum",
    "num": "Zahl",
    "cat": "Text/Schlüssel",
    "bool": "ja/nein",
}

# Verschachtelte Teile der Abfrage - fuer die Pruefung auf unbekannte
# Schluessel (Pydantic ignoriert sie sonst still).
_NESTED: Final[dict[tuple[type[BaseModel], str], type[BaseModel]]] = {
    (AnalyticsQuery, "group_by"): DimensionRef,
    (AnalyticsQuery, "measures"): MeasureRef,
    (AnalyticsQuery, "period"): PeriodSpec,
    (AnalyticsQuery, "where"): FilterNode,
    (AnalyticsQuery, "having"): FilterNode,
    (AnalyticsQuery, "order_by"): OrderRef,
    (AnalyticsQuery, "top_n"): TopNSpec,
    (FilterNode, "condition"): Condition,
    (FilterNode, "all_of"): FilterNode,
    (FilterNode, "any_of"): FilterNode,
    (FilterNode, "not_of"): FilterNode,
}


# ---------------------------------------------------------------- Katalog


def _catalog_fields() -> list[FieldMeta]:
    return list(analytics_service.get_field_catalog().fields)


def _money_fields() -> frozenset[str]:
    return frozenset(field.key for field in _catalog_fields() if field.is_money)


def _has_label(key: str) -> bool:
    """Gruppierung liefert zusaetzlich `<key>_label` mit dem Namen."""
    definition = analytics_service._FIELD_BY_KEY.get(key)
    return definition is not None and definition.label_sql is not None


def field_catalog_text() -> str:
    """Der Feldkatalog als Text - fuer describe_fields und den Startkontext.
    Quelle ist analytics_service.get_field_catalog(), dieselbe wie im
    Explorer."""
    lines: list[str] = []
    for field in _catalog_fields():
        traits = [_KIND_TEXT.get(field.kind, field.kind)]
        if field.is_money:
            traits.append("Euro")
        traits.append("gruppierbar" if field.groupable else "nicht gruppierbar")
        if not field.filterable:
            traits.append("nicht filterbar")
        if _has_label(field.key):
            traits.append(f"ID; der Name steht in {field.key}_label")
        line = f"- {field.key}: {field.label} ({', '.join(traits)})"
        line += f"; Aggregate: {', '.join(field.aggregates)}"
        if field.enum_values:
            line += f"; Werte: {', '.join(field.enum_values)}"
        lines.append(line)
    catalog = analytics_service.get_field_catalog()
    lines.append(
        "Standardspalten bei grain=rows: " + ", ".join(catalog.default_row_fields)
    )
    return "\n".join(lines)


_EXAMPLES: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    (
        "Ausgaben je Kategorie im laufenden Jahr",
        {
            "grain": "aggregate",
            "group_by": [{"field": "category"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
            "period": {"kind": "year_to_date"},
            "where": {"condition": {"field": "cashflow_role", "op": "eq", "value": "expense"}},
        },
    ),
    (
        "Monatsverlauf der Ausgaben bei REWE in den letzten 12 Monaten",
        {
            "grain": "aggregate",
            "group_by": [{"field": "date", "granularity": "month"}],
            "measures": [
                {"field": "amount", "aggregate": "sum"},
                {"field": "amount", "aggregate": "count"},
            ],
            "period": {"kind": "last_n_months", "n": 12},
            "search": "REWE",
            "order_by": [{"key": "date:month", "direction": "asc"}],
        },
    ),
    (
        "Einzelbuchungen mit mehr als 100 € Ausgabe in den letzten 3 Monaten, größte zuerst",
        {
            "grain": "rows",
            "period": {"kind": "last_n_months", "n": 3},
            "where": {"condition": {"field": "amount", "op": "lt", "value": -100}},
            "order_by": [{"key": "amount", "direction": "asc"}],
        },
    ),
)


def query_guide_text() -> str:
    """Form der Abfrage samt Beispielen."""
    granularities = ", ".join(get_args(DateGranularity))
    aggregates = ", ".join(get_args(Aggregate))
    operators = ", ".join(get_args(CompareOp))
    periods = ", ".join(get_args(PeriodSpec.model_fields["kind"].annotation))
    parts = [
        "Abfrage für query_analytics (Argument query, ein JSON-Objekt). Basis ist die "
        "Split-Ebene der Buchungen: eine aufgeteilte Buchung zählt je Teilbetrag.",
        f"- grain: {', '.join(get_args(Grain))}. aggregate = Kennzahlen je Gruppe "
        "(Standard), rows = Einzelzeilen.",
        "- group_by (nur aggregate): Liste von {field, granularity}. granularity "
        f"({granularities}) nur bei Datumsfeldern, Standard month. Ergebnisspalte heißt "
        "wie das Feld bzw. 'feld:granularity' (z. B. 'date:month', Wert = erster Tag der "
        "Periode); ID-Felder bringen zusätzlich '<feld>_label' mit dem Namen.",
        "- measures (nur aggregate, mindestens eine): Liste von {field (Standard amount), "
        f"aggregate}}. Aggregate: {aggregates}. Ergebnisspalte '<aggregate>_<field>', "
        "z. B. sum_amount. abs_sum = Summe ohne Vorzeichen, avg_per_month = Summe geteilt "
        "durch die Monate des Zeitraums (bei all_time ohne Teilung).",
        "- fields (nur rows): Spalten der Zeilen; leer = Standardspalten. split_id und "
        "transaction_id sind immer dabei (transaction_id passt zum Werkzeug transaction).",
        f"- period: {{kind: {periods}}}. last_n_months braucht n (Monate einschließlich des "
        "laufenden), year braucht year, custom braucht from_date und/oder to_date "
        "(JJJJ-MM-TT). Standard all_time.",
        "- search: Freitext über Empfänger, Verwendungszweck und Notizen (enthält, "
        "Groß-/Kleinschreibung egal).",
        "- where: Filterbaum. Jeder Knoten hat genau eines von condition {field, op, value}, "
        "all_of [Knoten], any_of [Knoten], not_of Knoten. Operatoren: "
        f"{operators}. in/not_in mit Liste (null in der Liste = ohne Wert), between mit genau "
        "zwei Werten, contains/starts_with nur bei Textfeldern, is_null/is_not_null ohne "
        "value. Beträge im Filter in Euro (Ausgaben negativ, z. B. -50 oder 12.5), Datum "
        "als JJJJ-MM-TT, Kategorie/Konto/Sparziel als ID.",
        "- order_by: Liste von {key, direction asc|desc}. Bei aggregate ist key eine "
        "Ergebnisspalte (Dimension wie category oder date:month, Kennzahl wie sum_amount); "
        "ohne Angabe absteigend nach Betragshöhe der ersten Kennzahl. Bei rows nur 'date' "
        "oder 'amount' (Standard: Datum absteigend).",
        "- limit: höchstens so viele Zeilen. Ohne limit liefert aggregate alle Gruppen und "
        "rows so viele Zeilen, wie der Dienst je Abruf erlaubt; geht es weiter, steht in "
        "den Kennzahlen next_cursor.",
        "- top_n (nur aggregate): {limit, include_rest (Standard true: der Rest als eine "
        "Zeile, die Summe bleibt vollständig), rest_label}.",
        "- cursor (nur rows): next_cursor des vorigen Ergebnisses, um die nächsten Zeilen "
        "derselben Abfrage zu holen.",
        "- include_transfers (Standard false): interne Umbuchungen mitzählen. "
        "only_on_budget (Standard false): nur Budgetkonten, ohne Depot/Immobilie/Kredit. "
        "include_archived_categories (Standard true).",
        "Vorzeichen: Ausgaben negativ, Einnahmen positiv. direction richtet sich nach dem "
        "Vorzeichen, cashflow_role nach der Budgetlogik (Einnahmekategorie bzw. "
        "unkategorisierter Zufluss = income; Ausgabe auf Budgetkategorie bzw. "
        "unkategorisierter Abfluss = expense; Umbuchungskategorien = null). Beträge im "
        "Ergebnis sind Euro.",
        "Beispiele:",
    ]
    for title, example in _EXAMPLES:
        parts.append(f"- {title}: {json.dumps(example, ensure_ascii=False)}")
    return "\n".join(parts)


def _query_schema() -> dict[str, Any]:
    """JSON-Schema des Arguments query - Werte aus den Literal-Unions und dem
    Feldkatalog, damit Schema und Kompiler nicht auseinanderlaufen."""
    fields = _catalog_fields()
    all_keys = [field.key for field in fields]
    groupable = [field.key for field in fields if field.groupable]
    filter_node = {
        "type": "object",
        "description": (
            "Filterknoten mit genau einem von condition {field, op, value}, all_of [Knoten], "
            "any_of [Knoten], not_of Knoten. Beträge in Euro."
        ),
        "properties": {
            "condition": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": all_keys},
                    "op": {"type": "string", "enum": list(get_args(CompareOp))},
                    "value": {
                        "description": (
                            "Skalar; Liste bei in/not_in; genau zwei Werte bei between; "
                            "entfällt bei is_null/is_not_null."
                        )
                    },
                },
                "required": ["field", "op"],
            },
            "all_of": {"type": "array", "items": {"type": "object"}},
            "any_of": {"type": "array", "items": {"type": "object"}},
            "not_of": {"type": "object"},
        },
    }
    return {
        "type": "object",
        "description": "Die Auswertung (AnalyticsQuery). Form und Beispiele: describe_fields.",
        "properties": {
            "grain": {"type": "string", "enum": list(get_args(Grain))},
            "group_by": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": groupable},
                        "granularity": {
                            "type": "string",
                            "enum": list(get_args(DateGranularity)),
                        },
                    },
                    "required": ["field"],
                },
            },
            "measures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": all_keys},
                        "aggregate": {"type": "string", "enum": list(get_args(Aggregate))},
                    },
                    "required": ["aggregate"],
                },
            },
            "fields": {"type": "array", "items": {"type": "string", "enum": all_keys}},
            "period": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": list(get_args(PeriodSpec.model_fields["kind"].annotation)),
                    },
                    "n": {"type": "integer", "description": "Monate inkl. laufendem"},
                    "year": {"type": "integer"},
                    "from_date": {"type": "string", "description": "JJJJ-MM-TT"},
                    "to_date": {"type": "string", "description": "JJJJ-MM-TT"},
                },
            },
            "search": {"type": "string"},
            "where": filter_node,
            "order_by": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    },
                    "required": ["key"],
                },
            },
            "limit": {"type": "integer"},
            "top_n": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "include_rest": {"type": "boolean"},
                    "rest_label": {"type": "string"},
                },
                "required": ["limit"],
            },
            "cursor": {"type": "string"},
            "include_transfers": {"type": "boolean"},
            "only_on_budget": {"type": "boolean"},
            "include_archived_categories": {"type": "boolean"},
        },
    }


# ---------------------------------------------------------------- Pruefung


def _unknown_keys(data: Any, model: type[BaseModel], path: str, out: list[str]) -> None:
    """Schluessel, die die Abfrage nicht kennt (rekursiv)."""
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        here = f"{path}.{key}" if path else str(key)
        if key not in model.model_fields:
            out.append(here)
            continue
        nested = _NESTED.get((model, key))
        if nested is None:
            continue
        if isinstance(value, list):
            for index, item in enumerate(value):
                _unknown_keys(item, nested, f"{here}[{index}]", out)
        else:
            _unknown_keys(value, nested, here, out)


def _to_minor(value: Any) -> Any:
    """Euro -> Minor Units fuer einen Filterwert. Nicht Umrechenbares bleibt
    unveraendert - der Kompiler meldet es dann als value_invalid."""
    if value is None or isinstance(value, bool):
        return value
    if not isinstance(value, int | float | str):
        return value
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return value
    if not amount.is_finite():
        return value
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _euro_node(node: FilterNode, money: frozenset[str]) -> FilterNode:
    if node.condition is not None:
        condition = node.condition
        if condition.field not in money:
            return node
        value = condition.value
        converted = (
            [_to_minor(item) for item in value] if isinstance(value, list) else _to_minor(value)
        )
        return node.model_copy(
            update={"condition": condition.model_copy(update={"value": converted})}
        )
    if node.all_of is not None:
        return node.model_copy(update={"all_of": [_euro_node(c, money) for c in node.all_of]})
    if node.any_of is not None:
        return node.model_copy(update={"any_of": [_euro_node(c, money) for c in node.any_of]})
    if node.not_of is not None:
        return node.model_copy(update={"not_of": _euro_node(node.not_of, money)})
    return node


def _pydantic_issues(error: ValidationError) -> list[str]:
    lines: list[str] = []
    for item in error.errors():
        path = ".".join(str(part) for part in item.get("loc", ())) or "query"
        lines.append(f"- {path}: {item.get('msg', 'ungültig')} (Code {item.get('type', '?')})")
    return lines


def _issue_lines(issues: list[ValidationIssue]) -> list[str]:
    return [f"- {issue.path}: {issue.message} (Code {issue.code})" for issue in issues]


def _rejected(lines: list[str]) -> ToolReply:
    return ToolReply(
        status="rejected",
        text=(
            "Die Abfrage wurde nicht ausgeführt. Bitte korrigieren:\n"
            + "\n".join(lines)
            + "\nFelder, Operatoren und Beispiele: describe_fields."
        ),
    )


def parse_query(raw: Any) -> AnalyticsQuery | ToolReply:
    """Rohdaten des Modells -> AnalyticsQuery (Betragsfilter in Minor Units)
    oder eine Ablehnung mit allen Befunden."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return _rejected(["- query: muss ein JSON-Objekt sein, kein Text."])
    if not isinstance(raw, dict):
        return _rejected(["- query: muss ein JSON-Objekt sein."])
    unknown: list[str] = []
    _unknown_keys(raw, AnalyticsQuery, "", unknown)
    if unknown:
        return _rejected(
            [f"- {path}: unbekannter Schlüssel (Code field_unknown)" for path in unknown]
        )
    if raw.get("having") is not None:
        return _rejected(
            [
                "- having: wird hier nicht unterstützt (Code operator_not_allowed). Filtere "
                "Buchungen mit where; Gruppen nach Größe ordnen/begrenzen geht mit order_by "
                "und top_n."
            ]
        )
    try:
        query = AnalyticsQuery.model_validate(raw)
    except ValidationError as error:
        return _rejected(_pydantic_issues(error))
    money = _money_fields()
    if query.where is not None:
        query = query.model_copy(update={"where": _euro_node(query.where, money)})
    return query


# ---------------------------------------------------------------- Ergebnis


def _cell(column: ColumnMeta | None, value: Any) -> Any:
    if (
        column is not None
        and column.is_money
        and isinstance(value, int | float | Decimal)
        and not isinstance(value, bool)
    ):
        return euro(int(value))
    if isinstance(value, str):
        if value == _REST_KEY or _UUID_TEXT.fullmatch(value):
            return value
        return mask_text(value)
    return plain(value)


def result_reply(result: AnalyticsResult) -> ToolReply:
    columns = result.columns
    by_key = {column.key: column for column in columns}
    table = ToolTable(
        columns=[column.key for column in columns],
        rows=[[_cell(column, row.get(column.key)) for column in columns] for row in result.rows],
    )
    summary: dict[str, Any] = {
        "query_id": result.query_id,
        "grain": result.grain,
        "period_from": plain(result.period_from),
        "period_to": plain(result.period_to),
        "months_covered": result.months_covered,
        "row_count": result.row_count,
        "columns": {
            column.key: column.label + (" (Euro)" if column.is_money else "")
            for column in columns
        },
    }
    if result.total_row is not None:
        summary["total_row"] = {
            key: _cell(by_key.get(key), value) for key, value in result.total_row.items()
        }
    summary["truncated"] = result.truncated
    if result.applied_limits:
        summary["applied_limits"] = [limit.model_dump() for limit in result.applied_limits]
    if result.next_cursor:
        summary["next_cursor"] = result.next_cursor
        summary["continue_with"] = (
            "Weitere Zeilen: dieselbe Abfrage mit \"cursor\": next_cursor erneut ausführen."
        )
    return ToolReply(summary=summary, table=table)


# ---------------------------------------------------------------- Handler


async def _describe_fields(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    check_keys(args, ())
    return ToolReply(
        text="Feldkatalog von query_analytics:\n"
        + field_catalog_text()
        + "\n\n"
        + query_guide_text()
    )


async def _query_analytics(ctx: ToolContext, args: ToolArgs) -> ToolReply:
    # Haeufiger Modellfehler: die Abfrage steht direkt in den Argumenten
    # statt unter "query" - sie ist dann trotzdem eindeutig.
    raw = args["query"] if "query" in args else args
    if "query" in args:
        check_keys(args, ("query",))
    parsed = parse_query(raw)
    if isinstance(parsed, ToolReply):
        return parsed
    query = parsed
    if query.grain == "rows" and query.limit is None:
        # Keine eigene Zeilengrenze: so viele Zeilen, wie der Dienst je Abruf
        # zulaesst; die Schleife zeigt sie seitenweise, next_cursor fuehrt weiter.
        query = query.model_copy(update={"limit": analytics_service.MAX_ROW_ROWS})
    check = analytics_service.validate_query(query, ctx.household_id, ctx.today)
    if not check.ok:
        return _rejected(_issue_lines(check.issues))
    try:
        result = await analytics_service.run_query(
            ctx.session, ctx.household_id, query, ctx.today
        )
    except QueryCompilationError as error:
        return _rejected(_issue_lines(error.issues))
    return result_reply(result)


DESCRIBE_FIELDS = ToolSpec(
    name="describe_fields",
    description=(
        "Erklärt query_analytics: alle Felder (gruppierbar, filterbar, erlaubte Aggregate, "
        "Wertelisten), die Form der Abfrage (grain, group_by, measures, period, where, "
        "order_by, limit, top_n, search, cursor) und Beispiele. Beträge in Ergebnissen und "
        "Filtern sind Euro. Keine Argumente."
    ),
    parameters=object_schema(),
    handler=_describe_fields,
    needs_db=False,
)

QUERY_ANALYTICS = ToolSpec(
    name="query_analytics",
    description=(
        "Freie Auswertung über alle Buchungen des Haushalts (Split-Ebene) - Summen, "
        "Durchschnitte, Anzahlen je Kategorie/Konto/Empfänger/Monat usw. (grain=aggregate) "
        "oder Einzelbuchungen (grain=rows). Kennzahlen nennen Zeitraum, Zeilenzahl, "
        "Gesamtzeile über die volle Filtermenge und jede angewandte Grenze. Beträge sind "
        "Euro, Ausgaben negativ, Einnahmen positiv; auch Betragsfilter in Euro. Umbuchungen "
        "sind standardmäßig ausgeschlossen. Ungültige Abfragen kommen mit allen Befunden "
        "zurück (rejected) - dann korrigieren. Felder und Beispiele: describe_fields."
    ),
    parameters=object_schema({"query": _query_schema()}, required=("query",)),
    handler=_query_analytics,
)

TOOLS: Final[tuple[ToolSpec, ...]] = (DESCRIBE_FIELDS, QUERY_ANALYTICS)
