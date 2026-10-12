import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  BankReconciliationPanel,
  type BankReconciliationCase,
} from "../components/reimbursements/BankReconciliationPanel";
import type {
  ReimbursementAllocation,
  ReimbursementTransactionCandidate,
} from "../lib/reimbursementsApi";

function vorgang(overrides: Partial<BankReconciliationCase> = {}): BankReconciliationCase {
  return {
    id: "c1",
    person_name: "Kind",
    provider_name: "Praxis Beispiel",
    invoice_date: "2026-07-01",
    expected_minor: 20000,
    reimbursed_minor: 0,
    outstanding_minor: 20000,
    ...overrides,
  };
}

function kandidat(
  overrides: Partial<ReimbursementTransactionCandidate> = {},
): ReimbursementTransactionCandidate {
  return {
    transaction_id: "t1",
    booked_date: "2026-07-20",
    amount_minor: 20000,
    payee_label: "Krankenversicherung AG",
    note: null,
    score: 0.9,
    evidence: ["Belegnummer steht im Verwendungszweck"],
    remaining_minor: 20000,
    ...overrides,
  };
}

function zuordnung(overrides: Partial<ReimbursementAllocation> = {}): ReimbursementAllocation {
  return {
    id: "a1",
    case_id: "c1",
    submission_id: null,
    transaction_id: "t1",
    allocation_type: "reimbursement",
    amount_minor: 12000,
    source: "manual",
    confidence: 1,
    confirmed_by: null,
    created_at: "2026-07-21T09:00:00Z",
    booked_date: "2026-07-20",
    payee_label: "Krankenversicherung AG",
    transaction_amount_minor: 20000,
    ...overrides,
  };
}

const NOOP = {
  onSelectCase: vi.fn(),
  onAllocate: vi.fn(),
  onRemoveAllocation: vi.fn(),
};

describe("BankReconciliationPanel", () => {
  it("sagt ohne offene Vorgaenge, was passieren muss, statt nur leer zu sein", () => {
    render(
      <BankReconciliationPanel
        cases={[]}
        selectedCaseId={null}
        candidates={[]}
        allocations={[]}
        {...NOOP}
      />,
    );
    expect(screen.getByText(/Kein Vorgang wartet auf eine Zahlung/)).toBeInTheDocument();
  });

  it("laedt Kandidaten erst nach Auswahl und zeigt die Trefferbegruendung", async () => {
    const onSelectCase = vi.fn();
    const { rerender } = render(
      <BankReconciliationPanel
        cases={[vorgang()]}
        selectedCaseId={null}
        candidates={[]}
        allocations={[]}
        {...NOOP}
        onSelectCase={onSelectCase}
      />,
    );
    // Ohne Auswahl steht die Begruendung nicht da - sie waere sonst zu einem
    // Vorgang gezeigt, den niemand geoeffnet hat.
    expect(screen.queryByText(/Belegnummer/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Zahlungen zuordnen" }));
    expect(onSelectCase).toHaveBeenCalledWith("c1");

    rerender(
      <BankReconciliationPanel
        cases={[vorgang()]}
        selectedCaseId="c1"
        candidates={[kandidat()]}
        allocations={[]}
        {...NOOP}
        onSelectCase={onSelectCase}
      />,
    );
    expect(screen.getByText("Belegnummer steht im Verwendungszweck")).toBeInTheDocument();
  });

  it("schlaegt den kleineren der beiden Betraege vor und ordnet ihn zu", async () => {
    const onAllocate = vi.fn();
    render(
      <BankReconciliationPanel
        cases={[vorgang({ outstanding_minor: 8000 })]}
        selectedCaseId="c1"
        candidates={[kandidat({ remaining_minor: 20000 })]}
        allocations={[]}
        {...NOOP}
        onAllocate={onAllocate}
      />,
    );
    // Offen sind 80 EUR, frei waeren 200 EUR - vorgeschlagen wird das Minimum,
    // sonst entstuende eine Ueberzahlung des Vorgangs.
    expect(screen.getByLabelText(/Betrag für Krankenversicherung AG/)).toHaveValue("80,00");

    await userEvent.click(screen.getByRole("button", { name: "Zuordnen" }));
    expect(onAllocate).toHaveBeenCalledWith("c1", "t1", 8000);
  });

  it("laesst Teilbetraege zu und weist mehr als frei ist ab", async () => {
    const onAllocate = vi.fn();
    render(
      <BankReconciliationPanel
        cases={[vorgang()]}
        selectedCaseId="c1"
        candidates={[kandidat({ remaining_minor: 20000 })]}
        allocations={[]}
        {...NOOP}
        onAllocate={onAllocate}
      />,
    );
    const feld = screen.getByLabelText(/Betrag für Krankenversicherung AG/);

    await userEvent.clear(feld);
    await userEvent.type(feld, "120,00");
    await userEvent.click(screen.getByRole("button", { name: "Zuordnen" }));
    expect(onAllocate).toHaveBeenCalledWith("c1", "t1", 12000);

    onAllocate.mockClear();
    await userEvent.clear(feld);
    await userEvent.type(feld, "250,00");
    expect(screen.getByRole("button", { name: "Zuordnen" })).toBeDisabled();
    expect(screen.getByText(/nicht überschreiten/)).toBeInTheDocument();
    expect(onAllocate).not.toHaveBeenCalled();
  });

  it("zeigt bestehende Zuordnungen mit Buchungsdaten und laesst sie entfernen", async () => {
    const onRemoveAllocation = vi.fn();
    render(
      <BankReconciliationPanel
        cases={[vorgang({ reimbursed_minor: 12000, outstanding_minor: 8000 })]}
        selectedCaseId="c1"
        candidates={[]}
        allocations={[zuordnung()]}
        {...NOOP}
        onRemoveAllocation={onRemoveAllocation}
      />,
    );
    const tabelle = screen.getByText("Bereits zugeordnet").parentElement as HTMLElement;
    // Datum und Empfaenger muessen dastehen: eine Zuordnung ohne sie waere
    // nicht nachpruefbar.
    expect(within(tabelle).getByText("2026-07-20")).toBeInTheDocument();
    expect(within(tabelle).getAllByText("Krankenversicherung AG").length).toBeGreaterThan(0);
    // Buchung gesamt 200 EUR, diesem Vorgang 120 EUR - der Unterschied traegt
    // den Fall Sammelueberweisung.
    expect(within(tabelle).getByText("200,00 €")).toBeInTheDocument();
    expect(within(tabelle).getByText("120,00 €")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Zuordnung entfernen" }));
    expect(onRemoveAllocation).toHaveBeenCalledWith("c1", "a1");
  });

  it("sperrt die Bedienung, solange eine Zuordnung laeuft", () => {
    render(
      <BankReconciliationPanel
        cases={[vorgang()]}
        selectedCaseId="c1"
        candidates={[kandidat()]}
        allocations={[zuordnung()]}
        busy
        {...NOOP}
      />,
    );
    expect(screen.getByRole("button", { name: "Zuordnen" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Zuordnung entfernen" })).toBeDisabled();
  });
});
