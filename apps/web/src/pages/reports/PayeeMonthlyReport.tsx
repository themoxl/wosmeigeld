import { useMemo } from "react";
import { rowsQuery } from "../../lib/analytics";
import type { AnalyticsQuery, AnalyticsResult } from "../../lib/types";
import { ReportLimitNotice, ReportMonthlyTable } from "./ReportMonthlyTable";
import { pivotMonthlyResult } from "./reportPivots";

export function PayeeMonthlyReport({
  result,
  isLoading,
  isError,
  onOpen,
}: {
  result?: AnalyticsResult;
  isLoading: boolean;
  isError: boolean;
  onOpen: (title: string, query: AnalyticsQuery) => void;
}) {
  const pivot = useMemo(() => (result ? pivotMonthlyResult(result, "payee") : null), [result]);
  if (isLoading && !result) return <p className="report-state">Empfänger werden geladen…</p>;
  if (isError)
    return (
      <p className="report-state report-state--error">
        Der Empfängerbericht konnte nicht geladen werden.
      </p>
    );
  if (!result || !pivot || pivot.rows.length === 0) {
    return <p className="report-state">Keine Empfänger im gewählten Zeitraum.</p>;
  }
  return (
    <section className="report-ledger-section" aria-labelledby="payee-ledger-title">
      <div className="report-section-heading">
        <div>
          <p className="report-eyebrow">Zahlungsziele</p>
          <h2 id="payee-ledger-title">Empfänger nach Monat</h2>
        </div>
        <p>Exakte Monatswerte statt eines schwer vergleichbaren Durchschnitts.</p>
      </div>
      <ReportLimitNotice result={result} />
      <ReportMonthlyTable
        pivot={pivot}
        labelHeader="Empfänger"
        ariaLabel="Empfänger nach Monat"
        onCellClick={(row, month) =>
          onOpen(
            `${row.label} · ${month.label}`,
            rowsQuery({
              field: "payee",
              value: row.id,
              from_date: month.from,
              to_date: month.to,
              direction: "expense",
            }),
          )
        }
      />
    </section>
  );
}
