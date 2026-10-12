import { Fragment, type ReactNode } from "react";
import { dimensionLabel } from "../../lib/analytics";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { AnalyticsColumn, AnalyticsResult, AnalyticsRow } from "../../lib/types";

// Ergebnistabelle einer Analyse-Abfrage.
//
// Bewusst aus ExplorerPage herausgeloest: dieselbe Tabelle zeigt inzwischen
// das Explorer-Ergebnis UND jeden Drilldown aus Berichten, Budget und
// Empfehlungen. Zwei Kopien waeren zwei Stellen, an denen sich Betragsfarbe,
// Label-Aufloesung und Summenzeile auseinanderentwickeln koennten.

interface TransactionTableProps {
  result: AnalyticsResult;
  onRowClick: (row: AnalyticsRow) => void;
  emptyText?: string;
  /**
   * Ersetzt die Kopfzelle einer Spalte durch einen interaktiven Kopf
   * (Sortierung, Spaltenfilter).
   *
   * Bewusst optional: dieselbe Tabelle zeigt auch jeden Drilldown aus
   * Berichten, Budget und Empfehlungen. Dort ist die Zeilenmenge durch das
   * angeklickte Segment bereits festgelegt - ein Spaltenfilter darin wuerde
   * eine Teilmenge zeigen, deren Summe nicht mehr zur angeklickten Zahl passt.
   */
  renderHeader?: (column: AnalyticsColumn) => ReactNode;
  /** Reihenfolge und Sichtbarkeit der Spalten; ohne Angabe alle fachlichen. */
  visibleKeys?: readonly string[];
}

const TECHNICAL_COLUMN_KEYS = new Set(["split_id", "transaction_id"]);

const COLUMN_CLASS_BY_KEY: Record<string, string> = {
  date: "date",
  account: "account",
  payee: "payee",
  purpose: "purpose",
  category: "category",
  amount: "amount",
  category_source: "source",
  status: "status",
};

export function TransactionTable({
  result,
  onRowClick,
  emptyText,
  renderHeader,
  visibleKeys,
}: TransactionTableProps) {
  // Label-Spalten werden nicht eigenstaendig angezeigt: sie gehoeren zur
  // Dimension daneben und wuerden die Tabelle sonst verdoppeln. Technische
  // IDs bleiben am Datensatz fuer Zeilenschluessel und Provenienz erhalten,
  // sind aber keine fachlichen Tabellenspalten.
  const businessColumns = result.columns.filter(
    (column) => !column.key.endsWith("_label") && !TECHNICAL_COLUMN_KEYS.has(column.key),
  );
  const columns = visibleKeys
    ? visibleKeys
        .map((key) => businessColumns.find((column) => column.key === key))
        .filter((column): column is (typeof businessColumns)[number] => Boolean(column))
    : businessColumns;

  if (result.rows.length === 0 || columns.length === 0) {
    return <p className="placeholder-note">{emptyText ?? "Keine Buchungen für diese Filter."}</p>;
  }

  return (
    <div className="txn-table-wrapper analytics-transaction-table-wrapper">
      <table
        className={`txn-table analytics-transaction-table ${
          result.grain === "rows" ? "analytics-transaction-table--rows" : ""
        }`}
      >
        <thead>
          <tr>
            {columns.map((column) =>
              renderHeader ? (
                <Fragment key={column.key}>{renderHeader(column)}</Fragment>
              ) : (
                <th
                  key={column.key}
                  className={`txn-th ${column.kind === "num" ? "txn-th--amount" : ""}${analyticsColumnClass(column.key)}`}
                >
                  {column.label}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, index) => (
            <tr
              key={String(row.split_id ?? row.transaction_id ?? index)}
              className="txn-tr explorer-row"
              onClick={() => onRowClick(row)}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`txn-td ${column.kind === "num" ? "txn-td--amount" : ""} ${
                    column.is_money && Number(row[column.key]) < 0
                      ? "transaction-amount--negative"
                      : column.is_money
                        ? "transaction-amount--positive"
                        : ""
                  }${analyticsColumnClass(column.key)}`}
                  style={column.kind === "num" ? { fontFamily: "var(--font-mono)" } : undefined}
                >
                  {renderCell(row, column.key, column.is_money ?? false)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {result.total_row && (
          <tfoot>
            <tr className="txn-tr explorer-total">
              {columns.map((column, index) => (
                <td key={column.key} className={`txn-td${analyticsColumnClass(column.key)}`}>
                  {index === 0
                    ? "Summe (alle Treffer)"
                    : column.is_money && typeof result.total_row?.[column.key] === "number"
                      ? formatMinorUnitsAsEuro(Number(result.total_row[column.key]))
                      : ""}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

function analyticsColumnClass(key: string) {
  const semanticName = COLUMN_CLASS_BY_KEY[key];
  return semanticName ? ` analytics-transaction-table__column--${semanticName}` : "";
}

function renderCell(row: AnalyticsRow, key: string, isMoney: boolean) {
  const value = row[key];
  if (isMoney && typeof value === "number") return formatMinorUnitsAsEuro(value);
  const label = row[`${key}_label`];
  if (typeof label === "string" && label.length > 0) return label;
  if (value === null || value === undefined) return <span className="txn-td--empty">—</span>;
  if (typeof value === "boolean") return value ? "ja" : "nein";
  return dimensionLabel(row, key);
}
