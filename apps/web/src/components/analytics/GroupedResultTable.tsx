import { Fragment, useMemo, type ReactNode } from "react";
import { drilldownQuery } from "../../lib/analytics";
import { flattenGroupTree, isSummableMeasureKey, type GroupNode } from "../../lib/groupTree";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import { useAnalyticsQuery } from "../../hooks/useAnalytics";
import type {
  AnalyticsColumn,
  AnalyticsQuery,
  AnalyticsResult,
  AnalyticsRow,
} from "../../lib/types";
import { TransactionTable } from "./TransactionTable";

// Der aufklappbare Gruppenbaum des Explorers.
//
// Eine einzige Gruppenspalte statt einer Spalte je Ebene: nur so bleibt die
// Tabelle bei drei oder vier Gruppierungsebenen noch lesbar, und die
// Einrueckung zeigt die Schachtelung deutlicher als nebeneinanderstehende
// Wiederholungen desselben Werts.
//
// Sortiert wird hier im Browser und auf JEDER Ebene. Serverseitig sortiert
// waeren nur die flachen Kombinationszeilen - die Reihenfolge der
// Zwischenebenen ergaebe sich daraus zufaellig.

export type GroupSortKey = { key: string; direction: "asc" | "desc" };

interface GroupedResultTableProps {
  tree: GroupNode[];
  result: AnalyticsResult;
  query: AnalyticsQuery;
  /** Ueberschriften der Gruppierungsebenen, fuer den Kopf der Gruppenspalte. */
  dimensionLabels: string[];
  measureColumns: AnalyticsColumn[];
  sort: GroupSortKey;
  onSortChange: (sort: GroupSortKey) => void;
  expanded: ReadonlySet<string>;
  onToggle: (path: string) => void;
  /** Menue der Gruppenspalte (Gruppierung aendern). */
  groupColumnMenu?: ReactNode;
}

export const GROUP_LABEL_KEY = "__label__";

export function GroupedResultTable({
  tree,
  result,
  query,
  dimensionLabels,
  measureColumns,
  sort,
  onSortChange,
  expanded,
  onToggle,
  groupColumnMenu,
}: GroupedResultTableProps) {
  const sortedTree = useMemo(() => sortTree(tree, sort), [tree, sort]);
  const visible = useMemo(() => flattenGroupTree(sortedTree, expanded), [sortedTree, expanded]);
  const columnCount = measureColumns.length + 1;

  function toggleSort(key: string) {
    onSortChange(
      sort.key === key
        ? { key, direction: sort.direction === "desc" ? "asc" : "desc" }
        : { key, direction: key === GROUP_LABEL_KEY ? "asc" : "desc" },
    );
  }

  return (
    <div className="txn-table-wrapper">
      <table className="txn-table grid-tree-table">
        <thead>
          <tr>
            <th className="txn-th grid-th grid-tree-table__group" aria-label="Gruppe" scope="col">
              <span className="grid-th-inner">
                <button
                  type="button"
                  className="grid-th-label"
                  aria-label="Nach Gruppenbezeichnung sortieren"
                  onClick={() => toggleSort(GROUP_LABEL_KEY)}
                >
                  <span className="grid-th-text">
                    {dimensionLabels.length > 0 ? dimensionLabels.join(" › ") : "Gruppe"}
                  </span>
                  <span className="grid-th-sort" aria-hidden="true">
                    {sort.key === GROUP_LABEL_KEY ? (sort.direction === "asc" ? "↑" : "↓") : ""}
                  </span>
                </button>
                {groupColumnMenu}
              </span>
            </th>
            {measureColumns.map((column) => (
              <th
                key={column.key}
                className="txn-th grid-th txn-th--amount"
                aria-label={column.label}
                scope="col"
              >
                <span className="grid-th-inner grid-th-inner--end">
                  <button
                    type="button"
                    className="grid-th-label"
                    aria-label={`Nach ${column.label} sortieren`}
                    onClick={() => toggleSort(column.key)}
                  >
                    <span className="grid-th-text">{column.label}</span>
                    <span className="grid-th-sort" aria-hidden="true">
                      {sort.key === column.key ? (sort.direction === "asc" ? "↑" : "↓") : ""}
                    </span>
                  </button>
                </span>
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {visible.length === 0 && (
            <tr>
              <td className="txn-td txn-td--empty" colSpan={columnCount}>
                Keine Buchungen für diese Filter.
              </td>
            </tr>
          )}

          {visible.map((node) => {
            const isLeaf = node.children.length === 0;
            const isOpen = expanded.has(node.path);
            return (
              <Fragment key={node.path}>
                <tr
                  className={`txn-tr grid-tree-row grid-tree-row--depth-${Math.min(node.depth, 3)}`}
                >
                  <td className="txn-td grid-tree-table__group">
                    <span
                      className="grid-tree-cell"
                      style={{ paddingLeft: `${node.depth * 18}px` }}
                    >
                      <button
                        type="button"
                        className="grid-tree-toggle"
                        onClick={() => onToggle(node.path)}
                        aria-expanded={isOpen}
                        aria-label={
                          isOpen
                            ? `${node.label} zuklappen`
                            : isLeaf
                              ? `Buchungen zu ${node.label} anzeigen`
                              : `${node.label} aufklappen`
                        }
                      >
                        {isOpen ? "▾" : "▸"}
                      </button>
                      <span className="grid-tree-label">{node.label}</span>
                      <span className="grid-tree-count">{isLeaf ? "" : `${node.leafCount}`}</span>
                    </span>
                  </td>
                  {measureColumns.map((column) => (
                    <td key={column.key} className="txn-td txn-td--amount grid-tree-measure">
                      {renderMeasure(node, column)}
                    </td>
                  ))}
                </tr>

                {isLeaf && isOpen && (
                  <tr className="txn-tr grid-tree-detail-row">
                    <td className="txn-td grid-tree-detail" colSpan={columnCount}>
                      <GroupDetailRows query={query} row={node.row} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>

        {result.total_row && (
          <tfoot>
            <tr className="txn-tr explorer-total">
              <td className="txn-td">Summe (alle Treffer)</td>
              {measureColumns.map((column) => (
                <td key={column.key} className="txn-td txn-td--amount">
                  {typeof result.total_row?.[column.key] === "number"
                    ? formatMeasure(Number(result.total_row[column.key]), column)
                    : "—"}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/**
 * Die Einzelbuchungen einer Blattgruppe - erst beim Aufklappen geladen.
 *
 * Bewusst dieselbe Ableitung wie der Drilldown aus Diagrammen und Berichten
 * (`drilldownQuery`): was hier aufgeklappt erscheint, ist per Konstruktion
 * dieselbe Menge, die die Zeile darueber summiert.
 */
function GroupDetailRows({ query, row }: { query: AnalyticsQuery; row: AnalyticsRow | null }) {
  const detailQuery = useMemo(() => (row ? drilldownQuery(query, row) : null), [query, row]);
  const { data, isLoading, error } = useAnalyticsQuery(
    detailQuery ?? { grain: "rows" },
    Boolean(detailQuery),
  );

  if (!detailQuery) {
    return (
      <p className="placeholder-note">Zu dieser Zusammenfassung gibt es keine Einzelzeilen.</p>
    );
  }
  if (isLoading) return <p className="placeholder-note">Lade Buchungen…</p>;
  if (error) return <p className="status-error">Die Buchungen konnten nicht geladen werden.</p>;
  if (!data) return null;

  return (
    <TransactionTable
      result={data}
      onRowClick={() => undefined}
      emptyText="Keine Einzelbuchungen in dieser Gruppe."
    />
  );
}

function renderMeasure(node: GroupNode, column: AnalyticsColumn) {
  const value = node.measures[column.key];
  if (typeof value !== "number") {
    // Auf Zwischenebenen bleibt eine nicht summierbare Kennzahl leer. Der
    // Titel benennt den Grund, statt die Zelle wortlos leer zu lassen.
    return (
      <span
        className="txn-td--empty"
        title={
          isSummableMeasureKey(column.key)
            ? "Kein Wert"
            : "Diese Kennzahl lässt sich nicht über Ebenen zusammenfassen."
        }
      >
        —
      </span>
    );
  }
  return (
    <span className={value < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}>
      {formatMeasure(value, column)}
    </span>
  );
}

function formatMeasure(value: number, column: AnalyticsColumn): string {
  return column.is_money ? formatMinorUnitsAsEuro(value) : String(value);
}

function sortTree(nodes: readonly GroupNode[], sort: GroupSortKey): GroupNode[] {
  const factor = sort.direction === "asc" ? 1 : -1;
  const sorted = [...nodes].sort((a, b) => {
    // Die zusammengefasste Restzeile bleibt immer unten: sie steht fuer viele
    // Segmente und wuerde sonst je nach Sortierung mitten im Feld landen.
    const restA = a.row?.[a.dimKey] === "__rest__";
    const restB = b.row?.[b.dimKey] === "__rest__";
    if (restA !== restB) return restA ? 1 : -1;

    if (sort.key === GROUP_LABEL_KEY) {
      return a.label.localeCompare(b.label, "de") * factor;
    }
    const left = a.measures[sort.key];
    const right = b.measures[sort.key];
    if (typeof left !== "number" && typeof right !== "number") return 0;
    if (typeof left !== "number") return 1;
    if (typeof right !== "number") return -1;
    // Nach Betragshoehe: Ausgaben sind negativ gespeichert, eine schlichte
    // Sortierung stellte sonst die kleinste Belastung nach vorn.
    return (Math.abs(left) - Math.abs(right)) * factor;
  });
  return sorted.map((node) =>
    node.children.length > 0 ? { ...node, children: sortTree(node.children, sort) } : node,
  );
}
