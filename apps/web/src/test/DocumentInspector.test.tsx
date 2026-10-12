import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { DocumentInspector } from "../components/reimbursements/DocumentInspector";
import type { DocumentExtraction } from "../lib/reimbursementsApi";
import type { ReimbursementDocumentView } from "../pages/ReimbursementsPage";

// Warnungen der Gesundheitsdokument-Auswertung: der Worker speichert nur
// Schluessel. Gruende des KI-Kerns muessen als Klartext mit Abhilfe
// erscheinen, nie als Rohschluessel.

const document: ReimbursementDocumentView = {
  id: "document-anna",
  insured_person_id: "person-anna",
  case_id: null,
  document_type: "invoice",
  original_filename: "anna-rechnung.pdf",
  mime_type: "application/pdf",
  size_bytes: 120_000,
  page_count: 1,
  processing_status: "needs_review",
  extraction_confidence: 0.6,
  error_message: null,
  created_at: "2026-08-03T10:00:00Z",
};

function extraction(warnings: unknown[]): DocumentExtraction {
  return {
    id: "extraction-1",
    document_id: "document-anna",
    model_name: null,
    status: "needs_review",
    confidence: 0.6,
    structured_data: {
      document_type: "invoice",
      person_name: "Anna",
      provider_name: "Praxis am Park",
      invoice_number: "A-101",
      invoice_date: "2026-07-10",
      due_date: null,
      billed_minor: 18000,
      expected_minor: null,
      reimbursed_minor: null,
      currency: "EUR",
      external_reference: null,
    },
    field_evidence: {},
    validation_warnings: warnings,
    confirmed_data: null,
    confirmed_at: null,
    created_at: "2026-08-03T10:00:00Z",
  };
}

function renderInspector(warnings: unknown[]) {
  return render(
    <MemoryRouter>
      <DocumentInspector
        document={document}
        personName="Anna"
        extraction={extraction(warnings)}
        extractionLoading={false}
        extractionError={null}
        fileUrl={null}
        fileLoading={false}
        fileError={null}
        confirmPending={false}
        confirmError={null}
        reprocessPending={false}
        deletePending={false}
        onConfirm={vi.fn()}
        onReprocess={vi.fn()}
        onDelete={vi.fn()}
        onClose={vi.fn()}
        caseCandidates={[]}
        caseCandidatesLoading={false}
        linkedCaseLabel={null}
        linkPending={false}
        linkError={null}
        onLinkCase={vi.fn()}
      />
    </MemoryRouter>,
  );
}

function warningList() {
  return screen
    .getAllByRole("listitem")
    .filter((item) => item.closest(".reimbursement-inspector-warnings"));
}

describe("DocumentInspector – Warnungen", () => {
  it("zeigt Gruende des KI-Kerns als Klartext mit Abhilfe", () => {
    renderInspector([
      "model_input_too_large",
      "model_timeout",
      "model_context_unknown",
      "model_missing",
      "invoice_core_fields_incomplete",
    ]);
    const items = warningList();
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent(
      "Die Eingabe passt samt Antwortreserve nicht in das Kontextfenster.",
    );
    expect(items[0]).toHaveTextContent("hier stehen nur die Werte aus den Textregeln");
    expect(items[1]).toHaveTextContent("Der Modellaufruf hat das Zeitbudget überschritten.");
    expect(
      within(items[1]!).getByRole("link", { name: "Zeitbudget unter Einstellungen › KI anpassen" }),
    ).toHaveAttribute("href", "/einstellungen?tab=ki");
    expect(items[2]).toHaveTextContent("Das Kontextfenster des Modells ist unbekannt");
    expect(within(items[2]!).getByRole("link")).toHaveAttribute("href", "/einstellungen?tab=ki");
    expect(items[3]).toHaveTextContent("Modell in LM Studio laden.");
    expect(items[4]).toHaveTextContent("Für eine Rechnung fehlen noch Kernfelder.");
    for (const item of items) expect(item).not.toHaveTextContent(/\bmodel_[a-z_]+\b/);
  });

  it("erklaert ein aufgebrauchtes Antwortbudget ohne die alte JSON-Uebertragung", () => {
    renderInspector(["model_budget_exhausted"]);
    const [item] = warningList();
    expect(item).toHaveTextContent("Das Antwortbudget war aufgebraucht");
    expect(item).toHaveTextContent("Denken (Reasoning)");
    expect(item).not.toHaveTextContent("JSON");
    expect(
      within(item!).getByRole("link", { name: "Reasoning unter Einstellungen › KI umstellen" }),
    ).toBeInTheDocument();
  });

  it("nennt einen unbekannten Schluessel nicht ohne Erklaerung", () => {
    renderInspector(["neuer_befund"]);
    expect(warningList()[0]).toHaveTextContent(
      "Unbekannter Befund der Auswertung (Kennung: neuer_befund).",
    );
  });
});
