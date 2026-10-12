import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { DocumentExtraction } from "../lib/reimbursementsApi";
import type {
  ReimbursementCase,
  ReimbursementDocumentView,
  ReimbursementSummary,
} from "../pages/ReimbursementsPage";
import { ReimbursementsRoutePage } from "../pages/ReimbursementsRoutePage";

const apiMocks = vi.hoisted(() => ({
  createPerson: vi.fn(),
  confirmExtraction: vi.fn(),
  deleteDocument: vi.fn(),
  fetchFile: vi.fn(),
  getCaseCandidates: vi.fn(),
  getExtraction: vi.fn(),
  getSummary: vi.fn(),
  linkCase: vi.fn(),
  listCases: vi.fn(),
  listDocuments: vi.fn(),
  listPeople: vi.fn(),
  reprocessAll: vi.fn(),
  reprocessDocument: vi.fn(),
  uploadDocument: vi.fn(),
  listPayers: vi.fn(),
  previewCase: vi.fn(),
  unlinkCase: vi.fn(),
  fetchPage: vi.fn(),
}));

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ accessToken: "test-token" }),
}));

vi.mock("../household/HouseholdProvider", () => ({
  useHousehold: () => ({ selectedHouseholdId: "household-1" }),
}));

vi.mock("../lib/reimbursementsApi", () => ({
  createReimbursementPerson: apiMocks.createPerson,
  confirmReimbursementExtraction: apiMocks.confirmExtraction,
  deleteReimbursementDocument: apiMocks.deleteDocument,
  fetchReimbursementDocumentFile: apiMocks.fetchFile,
  getReimbursementCaseCandidates: apiMocks.getCaseCandidates,
  getReimbursementExtraction: apiMocks.getExtraction,
  getReimbursementSummary: apiMocks.getSummary,
  linkReimbursementDocumentCase: apiMocks.linkCase,
  listReimbursementCases: apiMocks.listCases,
  listReimbursementDocuments: apiMocks.listDocuments,
  listReimbursementPeople: apiMocks.listPeople,
  reprocessAllReimbursementDocuments: apiMocks.reprocessAll,
  reprocessReimbursementDocument: apiMocks.reprocessDocument,
  uploadReimbursementDocument: apiMocks.uploadDocument,
  listReimbursementPayers: apiMocks.listPayers,
  getReimbursementCasePreview: apiMocks.previewCase,
  unlinkReimbursementDocumentCase: apiMocks.unlinkCase,
  fetchReimbursementDocumentPage: apiMocks.fetchPage,
  reimbursementErrorMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Fehler",
  reimbursementDocumentsQueryKey: (householdId: string) => ["reimbursement-documents", householdId],
  listReimbursementMarks: vi.fn(async () => []),
  convertMarkToCase: vi.fn(async () => ({ case_id: "c1" })),
  removeReimbursementMark: vi.fn(async () => undefined),
  reimbursementMarksQueryKey: (householdId: string, onlyOpen: boolean) => [
    "reimbursement-marks",
    householdId,
    onlyOpen,
  ],
  listTransactionCandidates: vi.fn(async () => []),
  listCaseAllocations: vi.fn(async () => []),
  createAllocation: vi.fn(async () => ({})),
  deleteAllocation: vi.fn(async () => undefined),
  reimbursementCandidatesQueryKey: (householdId: string, caseId: string | null) => [
    "reimbursement-transaction-candidates",
    householdId,
    caseId,
  ],
  reimbursementAllocationsQueryKey: (householdId: string, caseId: string | null) => [
    "reimbursement-allocations",
    householdId,
    caseId,
  ],
}));

const people = [
  {
    id: "person-anna",
    display_name: "Anna",
    birth_date: null,
    linked_user_id: null,
    is_active: true,
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:00:00Z",
  },
  {
    id: "person-luis",
    display_name: "Luis",
    birth_date: null,
    linked_user_id: null,
    is_active: true,
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:00:00Z",
  },
];

const summary: ReimbursementSummary = {
  year: new Date().getFullYear(),
  currency: "EUR",
  actionable_count: 3,
  processing_count: 1,
  expected_minor: 30000,
  received_minor: 12000,
  final_own_share_minor: 2500,
  unresolved_minor: 18000,
};

const cases: ReimbursementCase[] = [
  {
    id: "case-anna",
    person_id: "person-anna",
    person_name: "Anna",
    provider_name: "Praxis am Park",
    invoice_number: "A-101",
    invoice_date: `${summary.year}-07-10`,
    due_date: `${summary.year}-08-15`,
    billed_minor: 18000,
    reimbursed_minor: 0,
    outstanding_minor: 18000,
    currency: "EUR",
    overall_status: "action_required",
    payment_status: "paid",
    submission_status: "not_submitted",
    decision_status: "missing",
    reimbursement_status: "not_received",
    next_action: "Rechnung einreichen",
    document_count: 1,
    updated_at: `${summary.year}-08-01T10:00:00Z`,
  },
];

const documents: ReimbursementDocumentView[] = [
  {
    id: "document-anna",
    insured_person_id: "person-anna",
    case_id: null,
    document_type: "invoice",
    original_filename: "anna-rechnung.pdf",
    mime_type: "application/pdf",
    size_bytes: 125_000,
    page_count: 2,
    processing_status: "needs_review",
    extraction_confidence: 0.91,
    error_message: null,
    created_at: `${summary.year}-08-02T10:00:00Z`,
  },
  {
    id: "document-luis",
    insured_person_id: "person-luis",
    case_id: null,
    document_type: "benefit_statement",
    original_filename: "luis-leistung.pdf",
    mime_type: "application/pdf",
    size_bytes: 82_000,
    page_count: 1,
    processing_status: "extracting",
    extraction_confidence: null,
    error_message: null,
    created_at: `${summary.year}-08-03T10:00:00Z`,
  },
];

const extraction: DocumentExtraction = {
  id: "extraction-1",
  document_id: "document-anna",
  model_name: "local-model",
  status: "needs_review",
  confidence: 0.91,
  structured_data: {
    document_type: "invoice",
    person_name: "Anna",
    provider_name: "Praxis am Park",
    invoice_number: "A-101",
    invoice_date: `${summary.year}-07-10`,
    due_date: `${summary.year}-08-15`,
    billed_minor: 18000,
    expected_minor: 15000,
    reimbursed_minor: 0,
    currency: "EUR",
    external_reference: null,
  },
  field_evidence: { provider_name: { source: "local_ai" }, invoice_date: { source: "text_rule" } },
  validation_warnings: [],
  confirmed_data: null,
  confirmed_at: null,
  created_at: `${summary.year}-08-03T10:00:00Z`,
};

function TestQueryProvider({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderRoute() {
  return render(<ReimbursementsRoutePage />, { wrapper: TestQueryProvider });
}

async function openDocuments(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "Wurde alles abgerechnet?" });
  await user.click(await screen.findByRole("tab", { name: /^Dokumente/ }));
  return screen.findByRole("heading", { name: "Private Dokumentablage" });
}

async function openInspector(user: ReturnType<typeof userEvent.setup>) {
  await openDocuments(user);
  await user.click(screen.getByRole("button", { name: /anna-rechnung\.pdf/ }));
  return screen.findByRole("complementary", { name: "Dokument anna-rechnung.pdf" });
}

describe("ReimbursementsRoutePage", () => {
  beforeEach(() => {
    for (const mock of Object.values(apiMocks)) mock.mockReset();
    apiMocks.listPeople.mockResolvedValue(people);
    apiMocks.listCases.mockResolvedValue(cases);
    apiMocks.getSummary.mockResolvedValue(summary);
    apiMocks.listDocuments.mockResolvedValue(documents);
    apiMocks.getExtraction.mockResolvedValue(extraction);
    apiMocks.confirmExtraction.mockResolvedValue({ extraction, case: cases[0] });
    apiMocks.getCaseCandidates.mockResolvedValue([
      {
        case_id: "case-anna",
        person_id: "person-anna",
        person_name: "Anna",
        provider_name: "Praxis am Park",
        invoice_number: "A-101",
        invoice_date: `${summary.year}-07-10`,
        billed_minor: 18000,
        reimbursed_minor: 0,
        currency: "EUR",
        overall_status: "action_required",
        score: 0.87,
        evidence: ["Betrag stimmt überein", "Behandelte Person stimmt überein"],
      },
    ]);
    apiMocks.linkCase.mockResolvedValue({ document: documents[0], case: cases[0] });
    apiMocks.listPayers.mockResolvedValue([]);
    apiMocks.previewCase.mockResolvedValue({
      case_id: "case-anna",
      case_version: 1,
      expected_before_minor: 0,
      expected_after_minor: 0,
      received_minor: 0,
      outstanding_after_minor: 0,
      approved_minor: null,
      currency: "EUR",
    });
    apiMocks.fetchPage.mockResolvedValue(new Blob(["page"], { type: "image/png" }));
    apiMocks.unlinkCase.mockResolvedValue(documents[0]);
    apiMocks.reprocessAll.mockResolvedValue({ queued: 2, task_id: "task-3" });
    apiMocks.createPerson.mockResolvedValue(people[0]);
    apiMocks.deleteDocument.mockResolvedValue(undefined);
    apiMocks.reprocessDocument.mockResolvedValue({
      document: { id: "document-anna", processing_status: "queued" },
      duplicate: false,
      task_id: "task-2",
    });
    apiMocks.fetchFile.mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));
    apiMocks.uploadDocument.mockResolvedValue({
      document: { id: "uploaded", processing_status: "queued" },
      duplicate: false,
      task_id: "task-1",
    });
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:test-original"),
      revokeObjectURL: vi.fn(),
    });
  });

  it("zeigt die Mehrzahl im echten Routenfluss und verständliche Dokumentstatus", async () => {
    renderRoute();
    const user = userEvent.setup();

    expect(
      await screen.findByRole("heading", { name: "3 Vorgänge brauchen dich" }),
    ).toBeInTheDocument();
    await openDocuments(user);
    expect(screen.getByText("Bitte prüfen")).toBeInTheDocument();
    expect(screen.getByText("Wird ausgewertet")).toBeInTheDocument();
  });

  it("scopt Dokumente nach Person und nennt ihre Person in der Gesamtansicht", async () => {
    renderRoute();
    const user = userEvent.setup();
    await openDocuments(user);

    expect(screen.getByText("anna-rechnung.pdf")).toBeInTheDocument();
    expect(screen.getByText("luis-leistung.pdf")).toBeInTheDocument();
    expect(screen.getByText(/Anna ·/)).toBeInTheDocument();
    expect(screen.getByText(/Luis ·/)).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Person" }), "person-anna");
    expect(screen.getByText("anna-rechnung.pdf")).toBeInTheDocument();
    expect(screen.queryByText("luis-leistung.pdf")).not.toBeInTheDocument();
  });

  it("zeigt gelesene Felder, ihre Herkunft und das Original ohne Dialog", async () => {
    renderRoute();
    const user = userEvent.setup();
    const inspector = await openInspector(user);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(within(inspector).getByRole("combobox", { name: /Dokumenttyp/ })).toBeInTheDocument();
    expect(within(inspector).getByRole("textbox", { name: /Anbieter \/ Praxis/ })).toHaveValue(
      "Praxis am Park",
    );
    // Herkunft steht am Feld: Modellwert gegen Regelwert unterscheidbar.
    expect(within(inspector).getByText("KI")).toBeInTheDocument();
    expect(within(inspector).getByText("Regel")).toBeInTheDocument();

    const frame = await within(inspector).findByTitle("Original anna-rechnung.pdf");
    expect(frame).toHaveAttribute("src", "blob:test-original");
    expect(apiMocks.fetchFile).toHaveBeenCalledWith("test-token", "household-1", "document-anna");
  });

  it("validiert die Felder in der Arbeitsspalte vor der Bestätigung", async () => {
    renderRoute();
    const user = userEvent.setup();
    const inspector = await openInspector(user);

    const provider = within(inspector).getByRole("textbox", { name: /Anbieter \/ Praxis/ });
    await user.clear(provider);
    await user.click(within(inspector).getByRole("button", { name: "Felder bestätigen" }));
    expect(within(inspector).getByRole("alert")).toHaveTextContent(
      "Für eine Rechnung sind Anbieter, Rechnungsdatum und Betrag erforderlich",
    );
    expect(apiMocks.confirmExtraction).not.toHaveBeenCalled();

    await user.type(provider, "Praxis korrigiert");
    await user.click(within(inspector).getByRole("button", { name: "Felder bestätigen" }));
    await waitFor(() =>
      expect(apiMocks.confirmExtraction).toHaveBeenCalledWith(
        "test-token",
        "household-1",
        "document-anna",
        expect.objectContaining({ provider_name: "Praxis korrigiert", billed_minor: 18000 }),
      ),
    );
  });

  it("schlägt passende Vorgänge mit Begründung vor und ordnet auf Klick zu", async () => {
    renderRoute();
    const user = userEvent.setup();
    const inspector = await openInspector(user);

    const vorschlag = await within(inspector).findByRole("button", { name: /Praxis am Park/ });
    // Die Punkte sortieren, der Klartext begruendet.
    expect(vorschlag).toHaveTextContent("87 %");
    expect(vorschlag).toHaveTextContent("Betrag stimmt überein");
    expect(apiMocks.linkCase).not.toHaveBeenCalled();

    await user.click(vorschlag);
    expect(apiMocks.linkCase).not.toHaveBeenCalled();
    await user.click(
      await within(inspector).findByRole("button", { name: "Zuordnung übernehmen" }),
    );
    await waitFor(() =>
      expect(apiMocks.linkCase).toHaveBeenCalledWith(
        "test-token",
        "household-1",
        "document-anna",
        "case-anna",
        expect.objectContaining({ apply_decision: false, expected_case_version: 1 }),
      ),
    );
  });

  it("fragt keine Vorschläge ab, wenn das Dokument schon zugeordnet ist", async () => {
    apiMocks.listDocuments.mockResolvedValue([
      { ...documents[0], case_id: "case-anna" },
      documents[1],
    ]);
    renderRoute();
    const user = userEvent.setup();
    const inspector = await openInspector(user);

    expect(within(inspector).getByText(/Zugeordnet zu/)).toHaveTextContent("Praxis am Park");
    expect(apiMocks.getCaseCandidates).not.toHaveBeenCalled();
  });

  it("wertet auf Wunsch alle Dokumente erneut aus, aber erst nach Rückfrage", async () => {
    renderRoute();
    const user = userEvent.setup();
    await openDocuments(user);

    await user.click(screen.getByRole("button", { name: "Alle neu auswerten" }));
    expect(apiMocks.reprocessAll).not.toHaveBeenCalled();
    expect(screen.getByText("2 neu auswerten?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Ja, alle" }));
    await waitFor(() =>
      expect(apiMocks.reprocessAll).toHaveBeenCalledWith("test-token", "household-1"),
    );
  });

  it("löscht ein Dokument erst nach ausdrücklicher Rückfrage", async () => {
    renderRoute();
    const user = userEvent.setup();
    const inspector = await openInspector(user);

    await user.click(within(inspector).getByRole("button", { name: "Löschen" }));
    expect(apiMocks.deleteDocument).not.toHaveBeenCalled();
    expect(within(inspector).getByText("Wirklich löschen?")).toBeInTheDocument();

    await user.click(within(inspector).getByRole("button", { name: "Ja, endgültig" }));
    await waitFor(() =>
      expect(apiMocks.deleteDocument).toHaveBeenCalledWith(
        "test-token",
        "household-1",
        "document-anna",
      ),
    );
  });

  it("reiht ein Dokument auf Wunsch erneut zur Auswertung ein", async () => {
    renderRoute();
    const user = userEvent.setup();
    const inspector = await openInspector(user);

    await user.click(within(inspector).getByRole("button", { name: "Neu auswerten" }));
    await waitFor(() =>
      expect(apiMocks.reprocessDocument).toHaveBeenCalledWith(
        "test-token",
        "household-1",
        "document-anna",
      ),
    );
  });

  it("sagt bei laufender Auswertung, dass noch keine Felder vorliegen", async () => {
    renderRoute();
    const user = userEvent.setup();
    await openDocuments(user);
    await user.click(screen.getByRole("button", { name: /luis-leistung\.pdf/ }));

    const inspector = await screen.findByRole("complementary", {
      name: "Dokument luis-leistung.pdf",
    });
    expect(within(inspector).getByRole("alert")).toHaveTextContent(
      "Das Dokument wird gerade ausgewertet.",
    );
    expect(apiMocks.getExtraction).not.toHaveBeenCalled();
  });

  it("legt jederzeit eine weitere versicherte Person an", async () => {
    renderRoute();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Wurde alles abgerechnet?" });

    await user.click(screen.getByRole("button", { name: /Person$/ }));
    await user.type(screen.getByRole("textbox", { name: "Anzeigename" }), "Mika");
    await user.click(screen.getByRole("button", { name: "Person anlegen" }));

    await waitFor(() =>
      expect(apiMocks.createPerson).toHaveBeenCalledWith("test-token", "household-1", "Mika"),
    );
  });

  it("fragt beim Upload nach der Person, statt den Vorgang abzubrechen", async () => {
    const { container } = renderRoute();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Wurde alles abgerechnet?" });

    const input = container.querySelector<HTMLInputElement>("#reimbursement-document-input");
    expect(input).not.toBeNull();
    await user.upload(input!, [new File(["invoice"], "rechnung.pdf", { type: "application/pdf" })]);

    expect(await screen.findByText("1 Dokument – zu wem gehört das?")).toBeInTheDocument();
    expect(apiMocks.uploadDocument).not.toHaveBeenCalled();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Versicherte Person" }),
      "person-luis",
    );
    await user.click(screen.getByRole("button", { name: "Hochladen" }));

    await waitFor(() =>
      expect(apiMocks.uploadDocument).toHaveBeenCalledWith(
        "test-token",
        "household-1",
        "person-luis",
        expect.any(File),
      ),
    );
  });

  it("führt ohne versicherte Person durch das fokussierte Onboarding", async () => {
    apiMocks.listPeople.mockResolvedValue([]);
    renderRoute();
    const user = userEvent.setup();

    const nameInput = await screen.findByRole("textbox", { name: "Anzeigename" });
    expect(nameInput).toHaveFocus();
    await user.type(nameInput, "Mika");
    await user.click(screen.getByRole("button", { name: "Tracker starten" }));

    await waitFor(() =>
      expect(apiMocks.createPerson).toHaveBeenCalledWith("test-token", "household-1", "Mika"),
    );
  });
});
