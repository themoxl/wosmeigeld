import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DocumentCaseComparison } from "../components/reimbursements/DocumentCaseComparison";
import { displayedBox } from "../lib/pdfGeometry";
import {
  DocumentInspector,
  type DocumentInspectorProps,
} from "../components/reimbursements/DocumentInspector";
import type { ExtractedDocumentData } from "../lib/reimbursementsApi";

const data: ExtractedDocumentData = {
  document_type: "benefit_statement",
  person_name: "Anna",
  provider_name: null,
  invoice_number: "R-100",
  invoice_date: "2026-10-01",
  due_date: null,
  billed_minor: 40000,
  expected_minor: 25000,
  reimbursed_minor: 25000,
  currency: "EUR",
  external_reference: null,
  approved_minor: 25000,
  payer_name: "Muster PKV",
  payer_type: "pkv",
};
const candidate = {
  case_id: "case",
  person_id: "person",
  person_name: "Anna",
  provider_name: "Praxis",
  invoice_number: "R-100",
  invoice_date: "2026-10-01",
  billed_minor: 40000,
  reimbursed_minor: 0,
  expected_minor: 0,
  currency: "EUR",
  overall_status: "waiting" as const,
  score: 0.9,
  evidence: ["Rechnungsnummer stimmt überein"],
  version: 3,
};

describe("PKV-Dokumentvergleich", () => {
  it("zeigt vor der Übernahme die Erwartung und lässt den Bankeingang unverändert", async () => {
    const user = userEvent.setup();
    const onLink = vi.fn();
    render(
      <DocumentCaseComparison
        candidates={[candidate]}
        data={data}
        confirmed
        itemIndex={null}
        payers={[]}
        loading={false}
        linkedCaseLabel={null}
        pending={false}
        errorText={null}
        onLink={onLink}
        loadPreview={async () => ({
          case_id: "case",
          case_version: 3,
          expected_before_minor: 0,
          expected_after_minor: 25000,
          received_minor: 0,
          outstanding_after_minor: 25000,
          approved_minor: 25000,
          currency: "EUR",
        })}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Praxis/ }));
    expect(onLink).not.toHaveBeenCalled();
    expect(await screen.findByText("Bankeingang")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Zuordnung übernehmen" }));
    expect(onLink).toHaveBeenCalledWith(
      "case",
      expect.objectContaining({
        apply_decision: true,
        expected_case_version: 3,
        item_index: null,
      }),
    );
  });

  it("macht aus einem unbekannten Bewilligungsbetrag keine Nullentscheidung", async () => {
    const user = userEvent.setup();
    const onLink = vi.fn();
    render(
      <DocumentCaseComparison
        candidates={[candidate]}
        data={{ ...data, approved_minor: null, expected_minor: null, reimbursed_minor: null }}
        confirmed
        itemIndex={null}
        payers={[]}
        loading={false}
        linkedCaseLabel={null}
        pending={false}
        errorText={null}
        onLink={onLink}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Praxis/ }));
    expect(screen.getByRole("button", { name: "Zuordnung übernehmen" })).toBeDisabled();
    expect(screen.getByText("Unbekannt")).toBeInTheDocument();
    expect(onLink).not.toHaveBeenCalled();
  });

  it("verlangt die Feldbestätigung vor der Bescheidübernahme", async () => {
    const user = userEvent.setup();
    render(
      <DocumentCaseComparison
        candidates={[candidate]}
        data={data}
        confirmed={false}
        itemIndex={null}
        payers={[]}
        loading={false}
        linkedCaseLabel={null}
        pending={false}
        errorText={null}
        onLink={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Praxis/ }));
    expect(screen.getByRole("button", { name: "Zuordnung übernehmen" })).toBeDisabled();
  });

  it("führt bestätigte Korrekturen als Formularwerte weiter", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const invoice = { ...data, document_type: "invoice" as const, provider_name: "Praxis" };
    const props: DocumentInspectorProps = {
      document: {
        id: "doc",
        insured_person_id: "person",
        case_id: null,
        document_type: "invoice",
        original_filename: "test.pdf",
        mime_type: "application/pdf",
        size_bytes: 100,
        page_count: 1,
        processing_status: "confirmed",
        extraction_confidence: 0.9,
        error_message: null,
        created_at: "2026-10-01",
      },
      personName: "Anna",
      extraction: {
        id: "extract",
        document_id: "doc",
        model_name: null,
        status: "needs_review",
        confidence: 0.9,
        structured_data: invoice,
        confirmed_data: { ...invoice, billed_minor: 42000 },
        field_evidence: {},
        validation_warnings: [],
        confirmed_at: "2026-10-11",
        created_at: "2026-10-11",
      },
      extractionLoading: false,
      extractionError: null,
      fileUrl: null,
      fileLoading: false,
      fileError: null,
      confirmPending: false,
      confirmError: null,
      reprocessPending: false,
      deletePending: false,
      onConfirm,
      onReprocess: vi.fn(),
      onDelete: vi.fn(),
      onClose: vi.fn(),
      caseCandidates: [],
      caseCandidatesLoading: false,
      linkedCaseLabel: null,
      linkPending: false,
      linkError: null,
      onLinkCase: vi.fn(),
    };
    render(<DocumentInspector {...props} />);
    expect(screen.getByRole("textbox", { name: "Rechnungsbetrag" })).toHaveValue("420,00");
    await user.click(screen.getByRole("button", { name: "Erneut bestätigen" }));
    await waitFor(() =>
      expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ billed_minor: 42000 })),
    );
  });

  it("bildet Fundstellen auf gedrehten Seiten korrekt ab", () => {
    expect(displayedBox([0.1, 0.2, 0.3, 0.4], 90)).toEqual([0.6, 0.1, 0.8, 0.3]);
    expect(displayedBox([0.1, 0.2, 0.3, 0.4], 180)).toEqual([0.7, 0.6, 0.9, 0.8]);
  });
});
