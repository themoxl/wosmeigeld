import { useMemo, useState } from "react";
import { useAnalyticsQuery } from "../../hooks/useAnalytics";
import { rowsQuery, upsertCondition } from "../../lib/analytics";
import type { AnalyticsQuery, AnalyticsResult } from "../../lib/types";
import { ReportLimitNotice, ReportMonthlyTable } from "./ReportMonthlyTable";
import { pivotMonthlyResult, type MonthlyPivotRow } from "./reportPivots";
import { payeeMonthlyQuery } from "./reportQueries";

type OpenDrilldown = (title: string, query: AnalyticsQuery) => void;

function groupRows(rows: MonthlyPivotRow[], monthKeys: string[]): MonthlyPivotRow[] {
  const reliableGroups = rows.length > 0 && rows.every((row) => row.groupId !== null);
  if (!reliableGroups) return rows;
  const groups = new Map<string, MonthlyPivotRow[]>();
  rows.forEach((row) => {
    const key = row.groupId as string;
    groups.set(key, [...(groups.get(key) ?? []), row]);
  });
  const combined = [...groups.entries()].map(([groupId, children]) => {
    const values = Object.fromEntries(
      monthKeys.map((key) => [key, children.reduce((sum, row) => sum + (row.values[key] ?? 0), 0)]),
    );
    const sum = children.reduce((total, row) => total + row.sum, 0);
    const group: MonthlyPivotRow = {
      id: groupId,
      identityKey: `group:${groupId}`,
      label: children[0]?.groupLabel ?? "Weitere Ausgaben",
      groupId,
      groupLabel: children[0]?.groupLabel ?? "Weitere Ausgaben",
      values,
      counts: Object.fromEntries(
        monthKeys.map((key) => [
          key,
          children.reduce((total, row) => total + (row.counts[key] ?? 0), 0),
        ]),
      ),
      sum,
      average: monthKeys.length ? Math.round(sum / monthKeys.length) : 0,
      count: children.reduce((total, row) => total + row.count, 0),
      share: children.reduce((total, row) => total + row.share, 0),
    };
    return { group, children };
  });
  combined.sort(
    (a, b) =>
      Math.abs(b.group.sum) - Math.abs(a.group.sum) ||
      a.group.label.localeCompare(b.group.label, "de"),
  );
  return combined.flatMap(({ group, children }) => [group, ...children]);
}

function PayeeDisclosure({
  category,
  months,
  columnCount,
  onOpen,
}: {
  category: MonthlyPivotRow;
  months: number;
  columnCount: number;
  onOpen: OpenDrilldown;
}) {
  const query = useMemo(() => payeeMonthlyQuery(months, category.id), [months, category.id]);
  const payees = useAnalyticsQuery(query);
  const pivot = useMemo(
    () => (payees.data ? pivotMonthlyResult(payees.data, "payee") : null),
    [payees.data],
  );

  return (
    <tr className="report-monthly-table__disclosure-row">
      <td colSpan={columnCount}>
        <div className="report-payee-disclosure">
          <div className="report-payee-disclosure__heading">
            <strong>Empfänger in „{category.label}“</strong>
            <span>Monatliche Buchungsspur innerhalb dieser Kategorie</span>
          </div>
          {payees.isLoading && !payees.data ? (
            <p className="report-state">Empfänger werden geladen…</p>
          ) : null}
          {payees.isError ? (
            <p className="report-state report-state--error">
              Empfänger konnten nicht geladen werden.
            </p>
          ) : null}
          {payees.data && pivot && pivot.rows.length > 0 ? (
            <>
              <ReportLimitNotice result={payees.data} />
              <ReportMonthlyTable
                pivot={pivot}
                labelHeader="Empfänger"
                showShare={false}
                ariaLabel={`Empfänger in ${category.label} nach Monat`}
                onCellClick={(payee, month) => {
                  let detail = rowsQuery({
                    field: "category",
                    value: category.id,
                    from_date: month.from,
                    to_date: month.to,
                    direction: "expense",
                  });
                  detail = upsertCondition(
                    detail,
                    payee.id === null
                      ? { field: "payee", op: "is_null", value: null }
                      : { field: "payee", op: "eq", value: payee.id },
                  );
                  onOpen(`${category.label} · ${payee.label} · ${month.label}`, detail);
                }}
              />
            </>
          ) : payees.data && !payees.isLoading && !payees.isError ? (
            <p className="report-state">Keine Empfänger in dieser Kategorie und diesem Zeitraum.</p>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

export function CategoryMonthlyReport({
  result,
  isLoading,
  isError,
  months,
  onOpen,
}: {
  result?: AnalyticsResult;
  isLoading: boolean;
  isError: boolean;
  months: number;
  onOpen: OpenDrilldown;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const pivot = useMemo(
    () => (result ? pivotMonthlyResult(result, "category", "category_group") : null),
    [result],
  );
  const visibleRows = useMemo(
    () =>
      pivot
        ? groupRows(
            pivot.rows,
            pivot.months.map((month) => month.key),
          )
        : [],
    [pivot],
  );

  if (isLoading && !result) return <p className="report-state">Kategorien werden geladen…</p>;
  if (isError)
    return (
      <p className="report-state report-state--error">
        Der Kategorienbericht konnte nicht geladen werden.
      </p>
    );
  if (!result || !pivot || pivot.rows.length === 0) {
    return <p className="report-state">Keine kategorisierten Ausgaben im gewählten Zeitraum.</p>;
  }

  return (
    <section className="report-ledger-section" aria-labelledby="category-ledger-title">
      <div className="report-section-heading">
        <div>
          <p className="report-eyebrow">Vollständiges Ausgabenbuch</p>
          <h2 id="category-ledger-title">Kategorien nach Monat</h2>
        </div>
        <p>Kategorie öffnen, Empfänger prüfen, anschließend die exakten Buchungen ansehen.</p>
      </div>
      <ReportLimitNotice result={result} />
      <ReportMonthlyTable
        pivot={pivot}
        rows={visibleRows}
        labelHeader="Gruppe / Kategorie"
        showShare
        ariaLabel="Kategorien nach Monat"
        renderLabel={(row) => {
          const isGroup = row.identityKey.startsWith("group:");
          if (isGroup) return <span className="report-category-group">{row.label}</span>;
          const isExpanded = expanded.has(row.identityKey);
          return (
            <button
              type="button"
              className="report-category-toggle"
              aria-expanded={isExpanded}
              onClick={() =>
                setExpanded((current) => {
                  const next = new Set(current);
                  if (next.has(row.identityKey)) next.delete(row.identityKey);
                  else next.add(row.identityKey);
                  return next;
                })
              }
            >
              <span aria-hidden="true">{isExpanded ? "−" : "+"}</span>
              {row.label}
            </button>
          );
        }}
        renderAfterRow={(row, columnCount) =>
          !row.identityKey.startsWith("group:") && expanded.has(row.identityKey) ? (
            <PayeeDisclosure
              category={row}
              months={months}
              columnCount={columnCount}
              onOpen={onOpen}
            />
          ) : null
        }
        onCellClick={(row, month) => {
          const isGroup = row.identityKey.startsWith("group:");
          onOpen(
            `${row.label} · ${month.label}`,
            rowsQuery({
              field: isGroup ? "category_group" : "category",
              value: row.id,
              from_date: month.from,
              to_date: month.to,
              direction: "expense",
            }),
          );
        }}
      />
    </section>
  );
}
