import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TransactionDrilldown } from "../components/analytics/TransactionDrilldown";
import { TransactionTable } from "../components/analytics/TransactionTable";
import type { AnalyticsResult } from "../lib/types";

vi.mock("../hooks/useAnalytics", () => ({
  useAnalyticsQuery: () => ({ data: undefined, isLoading: false, isError: false }),
}));

const result: AnalyticsResult = {
  grain: "rows",
  columns: [
    { key: "date", label: "Datum", kind: "date" },
    { key: "account", label: "Konto", kind: "cat" },
    { key: "payee", label: "Zahlungsempfaenger", kind: "cat" },
    { key: "purpose", label: "Verwendungszweck", kind: "cat" },
    { key: "category", label: "Kategorie", kind: "cat" },
    { key: "amount", label: "Betrag", kind: "num", is_money: true },
    { key: "category_source", label: "Herkunft", kind: "cat" },
    { key: "status", label: "Status", kind: "cat" },
    { key: "account_label", label: "Kontolabel", kind: "cat" },
    { key: "split_id", label: "Split-ID", kind: "cat" },
    { key: "transaction_id", label: "Transaktions-ID", kind: "cat" },
  ],
  rows: [
    {
      date: "2026-07-14",
      account: "account-1",
      account_label: "Girokonto",
      payee: "Allianz Lebensservice",
      purpose: "Monatlicher Versicherungsbeitrag",
      category: "Versicherungen",
      amount: -12500,
      category_source: "Regel",
      status: "Gebucht",
      split_id: "split-1",
      transaction_id: "transaction-1",
    },
  ],
  row_count: 1,
  total_row: null,
  query_id: "query-1",
  months_covered: 12,
};

describe("TransactionTable", () => {
  it("zeigt nur fachliche Spalten und reicht beim Klick die Originalzeile weiter", () => {
    const onRowClick = vi.fn();
    render(<TransactionTable result={result} onRowClick={onRowClick} />);

    expect(screen.queryByRole("columnheader", { name: "Split-ID" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Transaktions-ID" })).not.toBeInTheDocument();
    expect(screen.queryByText("split-1")).not.toBeInTheDocument();
    expect(screen.queryByText("transaction-1")).not.toBeInTheDocument();

    expect(screen.getByRole("columnheader", { name: "Datum" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Betrag" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Verwendungszweck" })).toBeInTheDocument();
    expect(screen.getByText("2026-07-14")).toBeInTheDocument();
    expect(screen.getByText("Girokonto")).toBeInTheDocument();
    expect(screen.getByText("Allianz Lebensservice")).toBeInTheDocument();
    expect(screen.getByText("Monatlicher Versicherungsbeitrag")).toBeInTheDocument();
    expect(screen.getByText("Versicherungen")).toBeInTheDocument();
    expect(screen.getByText(/^-125,00/)).toBeInTheDocument();
    expect(screen.getByText("Regel")).toBeInTheDocument();
    expect(screen.getByText("Gebucht")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Allianz Lebensservice").closest("tr")!);

    expect(onRowClick).toHaveBeenCalledOnce();
    expect(onRowClick).toHaveBeenCalledWith(result.rows[0]);
    expect(onRowClick.mock.calls[0]?.[0]?.transaction_id).toBe("transaction-1");
  });
});

describe("TransactionDrilldown", () => {
  it("verwendet die tabellenorientierte Modalgroesse", () => {
    render(
      <TransactionDrilldown
        title="Allianz Lebensservice"
        query={{ grain: "rows" }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Allianz Lebensservice" })).toHaveClass(
      "modal-panel--table",
    );
  });
});
