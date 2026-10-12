import { useMemo, useState } from "react";
import { useAnalyticsQuery } from "../../hooks/useAnalytics";
import { rowsQuery } from "../../lib/analytics";
import { monthNameLong, toIsoDateString } from "../../lib/dates";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { AnalyticsQuery, AnalyticsResult } from "../../lib/types";
import { ReportLimitNotice, ReportTableScrollHint } from "./ReportMonthlyTable";
import { compareDimensionResults, fairYearPeriods, monthKey } from "./reportPivots";
import { comparisonQuery } from "./reportQueries";

function valueForMonth(result: AnalyticsResult | undefined, month: number): number {
  return (result?.rows ?? [])
    .filter((row) => Number(monthKey(row["date:month"]).slice(5, 7)) === month)
    .reduce((sum, row) => sum + Number(row.sum_amount ?? 0), 0);
}

function percent(delta: number, previous: number): number | null {
  return previous === 0 ? null : (delta / Math.abs(previous)) * 100;
}

function formatDelta(value: number) {
  return `${value > 0 ? "+" : ""}${formatMinorUnitsAsEuro(value)}`;
}

function deltaTone(value: number) {
  return value > 0
    ? "report-comparison__delta--favorable"
    : value < 0
      ? "report-comparison__delta--unfavorable"
      : "";
}

function ComparisonState({ loading, error }: { loading: boolean; error: boolean }) {
  if (loading) return <p className="report-state">Vergleich wird geladen…</p>;
  if (error)
    return (
      <p className="report-state report-state--error">
        Der Jahresvergleich konnte nicht geladen werden.
      </p>
    );
  return null;
}

export function YearComparisonReport({
  onOpen,
}: {
  onOpen: (title: string, query: AnalyticsQuery) => void;
}) {
  const currentYear = new Date().getFullYear();
  const [selectedYear, setSelectedYear] = useState(currentYear);
  const periods = useMemo(() => fairYearPeriods(selectedYear), [selectedYear]);
  const emptyQuery = comparisonQuery(`${selectedYear}-01-01`, `${selectedYear}-01-01`, "category");
  const selectedMonths = useAnalyticsQuery(
    periods ? comparisonQuery(periods.selectedFrom, periods.selectedTo, "date:month") : emptyQuery,
    Boolean(periods),
  );
  const previousMonths = useAnalyticsQuery(
    periods ? comparisonQuery(periods.previousFrom, periods.previousTo, "date:month") : emptyQuery,
    Boolean(periods),
  );
  const selectedCategories = useAnalyticsQuery(
    periods ? comparisonQuery(periods.selectedFrom, periods.selectedTo, "category") : emptyQuery,
    Boolean(periods),
  );
  const previousCategories = useAnalyticsQuery(
    periods ? comparisonQuery(periods.previousFrom, periods.previousTo, "category") : emptyQuery,
    Boolean(periods),
  );

  if (!periods) {
    return (
      <section className="report-ledger-section">
        <div className="report-year-toolbar">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setSelectedYear((year) => year - 1)}
          >
            ← Vorjahr
          </button>
          <strong>{selectedYear}</strong>
        </div>
        <p className="report-state">
          Für {selectedYear} ist noch kein vollständiger Monat verfügbar.
        </p>
      </section>
    );
  }

  const loading =
    selectedMonths.isLoading ||
    previousMonths.isLoading ||
    selectedCategories.isLoading ||
    previousCategories.isLoading;
  const error =
    selectedMonths.isError ||
    previousMonths.isError ||
    selectedCategories.isError ||
    previousCategories.isError;
  const monthRows = Array.from({ length: periods.monthCount }, (_, index) => {
    const month = index + 1;
    const selected = valueForMonth(selectedMonths.data, month);
    const previous = valueForMonth(previousMonths.data, month);
    const delta = selected - previous;
    return {
      month,
      label: monthNameLong(index),
      selected,
      previous,
      delta,
      deltaPercent: percent(delta, previous),
    };
  });
  const monthTotals = monthRows.reduce(
    (totals, row) => ({
      selected: totals.selected + row.selected,
      previous: totals.previous + row.previous,
    }),
    { selected: 0, previous: 0 },
  );
  const monthTotalDelta = monthTotals.selected - monthTotals.previous;
  const categories = compareDimensionResults(
    selectedCategories.data,
    previousCategories.data,
    "category",
  );

  const openPeriod = (title: string, from: string, to: string, categoryId?: string | null) =>
    onOpen(
      title,
      rowsQuery({
        field: categoryId === undefined ? undefined : "category",
        value: categoryId,
        from_date: from,
        to_date: to,
        direction: "expense",
      }),
    );

  return (
    <div className="reports-ledger-stack">
      <section className="report-ledger-section" aria-labelledby="year-comparison-title">
        <div className="report-year-header">
          <div>
            <p className="report-eyebrow">Like-for-like</p>
            <h2 id="year-comparison-title">
              {periods.selectedYear} gegen {periods.previousYear}
            </h2>
            <p className="report-scope report-scope--comparison">{periods.scopeLabel}</p>
          </div>
          <div className="report-year-toolbar" aria-label="Vergleichsjahr wählen">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setSelectedYear((year) => year - 1)}
              aria-label="Vorheriges Vergleichsjahr"
            >
              ←
            </button>
            <strong>{selectedYear}</strong>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setSelectedYear((year) => year + 1)}
              aria-label="Nächstes Vergleichsjahr"
              disabled={selectedYear >= currentYear}
            >
              →
            </button>
          </div>
        </div>
        <p className="report-comparison-note">
          Ausgaben bleiben mit ihrem Buchungsvorzeichen sichtbar. Eine negative Differenz bedeutet
          höhere Ausgaben und ist ungünstig.
        </p>
        <ComparisonState loading={loading && !selectedMonths.data} error={error} />
        {!error && selectedMonths.data && previousMonths.data ? (
          <>
            <ReportLimitNotice result={selectedMonths.data} />
            <ReportLimitNotice result={previousMonths.data} />
            <div className="report-table-shell">
              <ReportTableScrollHint />
              <div className="report-table-scroll">
                <table className="report-comparison-table" aria-label="Monatlicher Jahresvergleich">
                  <thead>
                    <tr>
                      <th>Monat</th>
                      <th>{periods.selectedYear}</th>
                      <th>{periods.previousYear}</th>
                      <th>Δ €</th>
                      <th>Δ %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monthRows.map((row) => {
                      const selectedMonth = `${periods.selectedYear}-${String(row.month).padStart(2, "0")}`;
                      const previousMonth = `${periods.previousYear}-${String(row.month).padStart(2, "0")}`;
                      return (
                        <tr key={row.month}>
                          <th scope="row">{row.label}</th>
                          <td>
                            <button
                              type="button"
                              className="report-value-button"
                              onClick={() =>
                                openPeriod(
                                  `${row.label} ${periods.selectedYear}`,
                                  `${selectedMonth}-01`,
                                  toIsoDateString(new Date(periods.selectedYear, row.month, 0)),
                                )
                              }
                            >
                              {row.selected ? formatMinorUnitsAsEuro(row.selected) : "·"}
                            </button>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="report-value-button"
                              onClick={() =>
                                openPeriod(
                                  `${row.label} ${periods.previousYear}`,
                                  `${previousMonth}-01`,
                                  toIsoDateString(new Date(periods.previousYear, row.month, 0)),
                                )
                              }
                            >
                              {row.previous ? formatMinorUnitsAsEuro(row.previous) : "·"}
                            </button>
                          </td>
                          <td className={deltaTone(row.delta)}>{formatDelta(row.delta)}</td>
                          <td className={deltaTone(row.delta)}>
                            {row.deltaPercent === null
                              ? "—"
                              : `${row.deltaPercent > 0 ? "+" : ""}${row.deltaPercent.toFixed(1)} %`}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th scope="row">Summe gleicher Zeitraum</th>
                      <td>{formatMinorUnitsAsEuro(monthTotals.selected)}</td>
                      <td>{formatMinorUnitsAsEuro(monthTotals.previous)}</td>
                      <td className={deltaTone(monthTotalDelta)}>{formatDelta(monthTotalDelta)}</td>
                      <td className={deltaTone(monthTotalDelta)}>
                        {percent(monthTotalDelta, monthTotals.previous) === null
                          ? "—"
                          : `${percent(monthTotalDelta, monthTotals.previous)! > 0 ? "+" : ""}${percent(monthTotalDelta, monthTotals.previous)!.toFixed(1)} %`}
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
          </>
        ) : null}
      </section>

      <section className="report-ledger-section" aria-labelledby="category-comparison-title">
        <div className="report-section-heading">
          <div>
            <p className="report-eyebrow">Veränderungstreiber</p>
            <h2 id="category-comparison-title">Kategorien im Vergleich</h2>
          </div>
          <p>Alle zurückgegebenen Kategorien, sortiert nach absoluter Differenz.</p>
        </div>
        {selectedCategories.data && previousCategories.data ? (
          <>
            <ReportLimitNotice result={selectedCategories.data} />
            <ReportLimitNotice result={previousCategories.data} />
            {categories.length > 0 ? (
              <div className="report-table-shell">
                <ReportTableScrollHint />
                <div className="report-table-scroll">
                  <table
                    className="report-comparison-table"
                    aria-label="Kategorien im Jahresvergleich"
                  >
                    <thead>
                      <tr>
                        <th>Kategorie</th>
                        <th>{periods.selectedYear}</th>
                        <th>{periods.previousYear}</th>
                        <th>Δ €</th>
                        <th>Δ %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {categories.map((entry) => (
                        <tr key={entry.identityKey}>
                          <th scope="row">{entry.label}</th>
                          <td>
                            <button
                              type="button"
                              className="report-value-button"
                              onClick={() =>
                                openPeriod(
                                  `${entry.label} · ${periods.selectedYear}`,
                                  periods.selectedFrom,
                                  periods.selectedTo,
                                  entry.id,
                                )
                              }
                            >
                              {entry.selected ? formatMinorUnitsAsEuro(entry.selected) : "·"}
                            </button>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="report-value-button"
                              onClick={() =>
                                openPeriod(
                                  `${entry.label} · ${periods.previousYear}`,
                                  periods.previousFrom,
                                  periods.previousTo,
                                  entry.id,
                                )
                              }
                            >
                              {entry.previous ? formatMinorUnitsAsEuro(entry.previous) : "·"}
                            </button>
                          </td>
                          <td className={deltaTone(entry.delta)}>{formatDelta(entry.delta)}</td>
                          <td className={deltaTone(entry.delta)}>
                            {entry.deltaPercent === null
                              ? "—"
                              : `${entry.deltaPercent > 0 ? "+" : ""}${entry.deltaPercent.toFixed(1)} %`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <p className="report-state">Keine Ausgaben in den beiden Vergleichszeiträumen.</p>
            )}
          </>
        ) : null}
      </section>
    </div>
  );
}
