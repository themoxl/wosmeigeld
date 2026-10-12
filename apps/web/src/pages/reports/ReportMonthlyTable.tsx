import { Fragment, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { AnalyticsResult } from "../../lib/types";
import type { MonthlyPivot, MonthlyPivotRow, ReportMonth } from "./reportPivots";

export type MonthlyCellClick = (row: MonthlyPivotRow, month: ReportMonth) => void;

function activateOnKeyboard(event: KeyboardEvent<HTMLElement>, action: () => void) {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  action();
}

function amount(value: number | null | undefined, populated = false, empty = "·") {
  return value === null || value === undefined || (value === 0 && !populated)
    ? empty
    : formatMinorUnitsAsEuro(value);
}

function deviationLevel(
  row: MonthlyPivotRow,
  value: number | null | undefined,
): string | undefined {
  if (!value || row.values === undefined) return undefined;
  const populated = Object.values(row.values).filter((entry): entry is number => Boolean(entry));
  if (populated.length < 2) return undefined;
  const baseline = populated.reduce((sum, entry) => sum + Math.abs(entry), 0) / populated.length;
  return Math.abs(value) >= baseline * 1.6 ? "high" : undefined;
}

export function ReportLimitNotice({ result }: { result: AnalyticsResult }) {
  if (!result.truncated && (result.applied_limits?.length ?? 0) === 0) return null;
  const limits = (result.applied_limits ?? [])
    .map((limit) => `${limit.kind}: ${limit.limit}`)
    .join(" · ");
  return (
    <p className="report-limit-notice" role="status">
      Die Serverantwort ist begrenzt{limits ? ` (${limits})` : ""}. Angezeigt werden alle
      zurückgegebenen Zeilen; die Auswertung ist nicht vollständig.
    </p>
  );
}

export function ReportTableScrollHint() {
  return (
    <p className="report-table-scroll-hint">
      <span aria-hidden="true">↔</span> Tabelle seitlich scrollen für weitere Spalten
    </p>
  );
}

export function ReportMonthlyTable({
  pivot,
  labelHeader,
  rows = pivot.rows,
  renderLabel,
  renderAfterRow,
  onCellClick,
  showShare = false,
  showCount = true,
  showFooter = true,
  tone = "expense",
  ariaLabel,
}: {
  pivot: MonthlyPivot;
  labelHeader: string;
  rows?: MonthlyPivotRow[];
  renderLabel?: (row: MonthlyPivotRow) => ReactNode;
  renderAfterRow?: (row: MonthlyPivotRow, columnCount: number) => ReactNode;
  onCellClick?: MonthlyCellClick;
  showShare?: boolean;
  showCount?: boolean;
  showFooter?: boolean;
  tone?: "expense" | "signed" | "neutral";
  ariaLabel: string;
}) {
  const summaryCount = 2 + Number(showShare) + Number(showCount);
  const columnCount = 1 + pivot.months.length + summaryCount;
  const toneClass = (value: number) => {
    if (tone === "neutral") return "";
    if (tone === "expense") return value < 0 ? "transaction-amount--negative" : "";
    return value > 0
      ? "transaction-amount--positive"
      : value < 0
        ? "transaction-amount--negative"
        : "";
  };

  return (
    <div className="report-table-shell" data-scroll-hint="Seitlich scrollen für weitere Monate">
      <ReportTableScrollHint />
      <div
        className="report-table-scroll"
        tabIndex={0}
        aria-label={`${ariaLabel}, horizontal scrollbar`}
      >
        <table
          className="report-monthly-table"
          aria-label={ariaLabel}
          style={
            {
              "--report-count-width": showCount ? "52px" : "0px",
              "--report-share-width": showShare ? "72px" : "0px",
            } as CSSProperties
          }
        >
          <thead>
            <tr>
              <th className="report-monthly-table__label">{labelHeader}</th>
              {pivot.months.map((month) => (
                <th
                  key={month.key}
                  className={`report-monthly-table__month ${month.isCurrent ? "report-monthly-table__month--current" : ""}`}
                  scope="col"
                >
                  <span>{month.label}</span>
                  {month.isCurrent && <small>laufend</small>}
                </th>
              ))}
              <th className="report-monthly-table__summary report-monthly-table__average">
                Ø / Monat
              </th>
              <th className="report-monthly-table__summary report-monthly-table__sum">Summe</th>
              {showShare && (
                <th className="report-monthly-table__summary report-monthly-table__share">
                  Anteil
                </th>
              )}
              {showCount && (
                <th className="report-monthly-table__summary report-monthly-table__count">#</th>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <Fragment key={row.identityKey}>
                <tr
                  className={`report-monthly-table__row ${row.identityKey.startsWith("group:") ? "report-monthly-table__row--group" : ""}`}
                >
                  <th className="report-monthly-table__label" scope="row">
                    {renderLabel ? renderLabel(row) : row.label}
                  </th>
                  {pivot.months.map((month) => {
                    const value = row.values[month.key];
                    const hasBookings = (row.counts[month.key] ?? 0) > 0;
                    const clickable = hasBookings && Boolean(onCellClick);
                    const open = () => onCellClick?.(row, month);
                    return (
                      <td
                        key={month.key}
                        className={`report-monthly-table__value ${month.isCurrent ? "report-monthly-table__value--current" : ""} ${clickable ? "report-monthly-table__value--clickable" : ""} ${toneClass(value ?? 0)}`}
                        data-deviation={deviationLevel(row, value)}
                        role={clickable ? "button" : undefined}
                        tabIndex={clickable ? 0 : undefined}
                        aria-label={
                          clickable
                            ? `${row.label}, ${month.label}: ${amount(value, true)} anzeigen`
                            : undefined
                        }
                        onClick={clickable ? open : undefined}
                        onKeyDown={
                          clickable ? (event) => activateOnKeyboard(event, open) : undefined
                        }
                      >
                        {amount(value, hasBookings)}
                      </td>
                    );
                  })}
                  <td
                    className={`report-monthly-table__summary report-monthly-table__average ${toneClass(row.average)}`}
                  >
                    {amount(row.average, row.count > 0)}
                  </td>
                  <td
                    className={`report-monthly-table__summary report-monthly-table__sum ${toneClass(row.sum)}`}
                  >
                    {amount(row.sum, row.count > 0)}
                  </td>
                  {showShare && (
                    <td className="report-monthly-table__summary report-monthly-table__share">
                      {row.share > 0 ? `${(row.share * 100).toFixed(1)} %` : "·"}
                    </td>
                  )}
                  {showCount && (
                    <td className="report-monthly-table__summary report-monthly-table__count">
                      {row.count || "·"}
                    </td>
                  )}
                </tr>
                {renderAfterRow?.(row, columnCount)}
              </Fragment>
            ))}
          </tbody>
          {showFooter && (
            <tfoot>
              <tr>
                <th className="report-monthly-table__label" scope="row">
                  Summe (alle)
                </th>
                {pivot.months.map((month) => (
                  <td
                    key={month.key}
                    className={`report-monthly-table__value ${toneClass(pivot.monthTotals[month.key] ?? 0)}`}
                  >
                    {amount(
                      pivot.monthTotals[month.key],
                      pivot.rows.some((row) => (row.counts[month.key] ?? 0) > 0),
                    )}
                  </td>
                ))}
                <td
                  className={`report-monthly-table__summary report-monthly-table__average ${toneClass(pivot.average)}`}
                >
                  {amount(pivot.average, pivot.count > 0)}
                </td>
                <td
                  className={`report-monthly-table__summary report-monthly-table__sum ${toneClass(pivot.sum)}`}
                >
                  {amount(pivot.sum, pivot.count > 0)}
                </td>
                {showShare && (
                  <td className="report-monthly-table__summary report-monthly-table__share">
                    100 %
                  </td>
                )}
                {showCount && (
                  <td className="report-monthly-table__summary report-monthly-table__count">
                    {pivot.count || "·"}
                  </td>
                )}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}
