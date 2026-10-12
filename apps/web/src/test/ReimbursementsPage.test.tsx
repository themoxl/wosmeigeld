import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  ReimbursementsPage,
  type ReimbursementCase,
  type ReimbursementSummary,
} from "../pages/ReimbursementsPage";

vi.mock("../hooks/useProfile", async () => {
  const { formatDate, formatDateTime } = await import("../lib/dates");
  return {
    useFormatDate: () => ({
      dateFormat: "de" as const,
      formatDate: (value: string | Date) => formatDate(value, "de"),
      formatDateTime: (value: string | Date) => formatDateTime(value, "de"),
    }),
  };
});

const summary: ReimbursementSummary = {
  year: 2026,
  currency: "EUR",
  actionable_count: 1,
  processing_count: 1,
  expected_minor: 23940,
  received_minor: 49500,
  final_own_share_minor: 8230,
  unresolved_minor: 16475,
};

const cases: ReimbursementCase[] = [
  {
    id: "case-action",
    person_id: "person-anna",
    person_name: "Anna",
    provider_name: "Praxis am Stadtpark",
    invoice_number: "R-2026-118",
    invoice_date: "2026-07-12",
    due_date: "2026-08-12",
    billed_minor: 16475,
    reimbursed_minor: 0,
    outstanding_minor: 16475,
    currency: "EUR",
    overall_status: "action_required",
    payment_status: "paid",
    submission_status: "not_submitted",
    decision_status: "missing",
    reimbursement_status: "not_received",
    next_action: "Rechnung bei der PKV einreichen",
    document_count: 1,
    updated_at: "2026-08-02T09:00:00Z",
  },
  {
    id: "case-waiting",
    person_id: "person-luis",
    person_name: "Luis",
    provider_name: "Zahnzentrum West",
    invoice_number: "ZW-8821",
    invoice_date: "2026-06-18",
    due_date: null,
    billed_minor: 15700,
    reimbursed_minor: 0,
    outstanding_minor: 7500,
    currency: "EUR",
    overall_status: "waiting",
    payment_status: "paid",
    submission_status: "submitted",
    decision_status: "in_review",
    reimbursement_status: "not_received",
    next_action: "Auf Leistungsabrechnung warten",
    document_count: 2,
    updated_at: "2026-07-28T09:00:00Z",
  },
  {
    id: "case-settled",
    person_id: "person-anna",
    person_name: "Anna",
    provider_name: "Augenärzte Mitte",
    invoice_number: null,
    invoice_date: "2026-03-05",
    due_date: null,
    billed_minor: 49500,
    reimbursed_minor: 49500,
    outstanding_minor: 0,
    currency: "EUR",
    overall_status: "reconciled",
    payment_status: "paid",
    submission_status: "submitted",
    decision_status: "approved",
    reimbursement_status: "received",
    next_action: null,
    document_count: 3,
    updated_at: "2026-04-09T09:00:00Z",
  },
];

describe("ReimbursementsPage", () => {
  it("zeigt Handlungsurteil und exakte Kennzahlen", () => {
    render(<ReimbursementsPage summary={summary} cases={cases} />);

    expect(screen.getByRole("heading", { name: "1 Vorgang braucht dich" })).toBeInTheDocument();
    const verdict = screen.getByRole("region", { name: "1 Vorgang braucht dich" });
    expect(within(verdict).getByText("164,75 €")).toBeInTheDocument();

    const metrics = screen.getByRole("region", { name: "Abrechnungskennzahlen" });
    const actionMetric = within(metrics).getByText("Handlungsbedarf").closest("article");
    expect(actionMetric).not.toBeNull();
    expect(within(actionMetric!).getByText("1")).toBeInTheDocument();
    expect(within(metrics).getByText("239,40 €")).toBeInTheDocument();
    expect(within(metrics).getByText("495,00 €")).toBeInTheDocument();
  });

  it("zeigt bei vollständig erledigtem Haushalt das All-clear und eine leere Aktionsliste", () => {
    render(
      <ReimbursementsPage
        summary={{
          ...summary,
          actionable_count: 0,
          processing_count: 0,
          expected_minor: 0,
          unresolved_minor: 0,
        }}
        cases={[]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Alles abgeglichen" })).toBeInTheDocument();
    expect(screen.getByText("Nichts zu tun")).toBeInTheDocument();
    expect(screen.getByText("Noch keine Vorgänge")).toBeInTheDocument();
  });

  it("filtert den Ledger nach Suche und Status", async () => {
    render(<ReimbursementsPage summary={summary} cases={cases} />);
    const user = userEvent.setup();
    const tableRegion = screen.getByRole("region", { name: "Erstattungsvorgänge als Tabelle" });

    await user.type(screen.getByRole("searchbox", { name: "Vorgänge durchsuchen" }), "Zahnzentrum");
    expect(within(tableRegion).getByText("Zahnzentrum West")).toBeInTheDocument();
    expect(within(tableRegion).queryByText("Praxis am Stadtpark")).not.toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox", { name: "Vorgänge durchsuchen" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "reconciled");
    expect(within(tableRegion).getByText("Augenärzte Mitte")).toBeInTheDocument();
    expect(within(tableRegion).queryByText("Zahnzentrum West")).not.toBeInTheDocument();
  });

  it("öffnet den Vorgang, zeigt die sechs Schritte und schließt per Escape", async () => {
    render(<ReimbursementsPage summary={summary} cases={cases} />);
    const user = userEvent.setup();
    const tableRegion = screen.getByRole("region", { name: "Erstattungsvorgänge als Tabelle" });

    await user.click(within(tableRegion).getByRole("button", { name: "Praxis am Stadtpark" }));
    const dialog = screen.getByRole("dialog", { name: "Praxis am Stadtpark" });
    expect(within(dialog).getByText("Abrechnungskette")).toBeInTheDocument();
    expect(within(dialog).getByText("Rechnung")).toBeInTheDocument();
    expect(within(dialog).getByText("Abgleich")).toBeInTheDocument();
    expect(within(dialog).getByText("6 Prüfpunkte")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Diagnose:/)).not.toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("übergibt ausgewählte PDFs und Bilder an den Upload-Callback", async () => {
    const onUpload = vi.fn(async (_files: File[], _personId: string) => undefined);
    const { container } = render(
      <ReimbursementsPage summary={summary} cases={cases} onUpload={onUpload} />,
    );
    const user = userEvent.setup();
    const input = container.querySelector<HTMLInputElement>("#reimbursement-document-input");
    const files = [
      new File(["invoice"], "rechnung.pdf", { type: "application/pdf" }),
      new File(["image"], "beleg.png", { type: "image/png" }),
    ];

    expect(input).not.toBeNull();
    await user.selectOptions(screen.getByRole("combobox", { name: "Person" }), "person-anna");
    await user.upload(input!, files);

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(files, "person-anna"));
    expect(screen.getByRole("status")).toHaveTextContent("2 Dokumente wurden hinzugefügt");
    expect(input).toHaveValue("");
  });

  it("liefert getrennte Desktop- und Mobile-Strukturen", () => {
    const { container } = render(<ReimbursementsPage summary={summary} cases={cases} />);
    expect(container.querySelector(".reimbursement-table-region")).toBeInTheDocument();
    expect(container.querySelector(".reimbursement-mobile-cases")).toBeInTheDocument();
  });
});
