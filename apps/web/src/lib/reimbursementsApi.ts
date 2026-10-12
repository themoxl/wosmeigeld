import type {
  ReimbursementCase,
  ReimbursementDocumentView,
  ReimbursementSummary,
} from "../pages/ReimbursementsPage";
import { API_BASE, ApiError, apiFetch } from "./api";

export interface ReimbursementPerson {
  id: string;
  display_name: string;
  birth_date: string | null;
  linked_user_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ReimbursementDocumentUpload {
  document: { id: string; processing_status: string };
  duplicate: boolean;
  task_id: string | null;
}

export interface ExtractedStatementItem {
  person_name: string | null;
  provider_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  billed_minor: number | null;
  approved_minor: number | null;
  deductible_minor: number | null;
  noncovered_minor: number | null;
  page_number: number | null;
  source_index?: number | null;
}

export interface ExtractedDocumentData {
  document_type: ReimbursementDocumentView["document_type"];
  person_name: string | null;
  provider_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  billed_minor: number | null;
  expected_minor: number | null;
  reimbursed_minor: number | null;
  currency: string;
  external_reference: string | null;
  statement_number?: string | null;
  statement_date?: string | null;
  submission_date?: string | null;
  payer_name?: string | null;
  payer_type?: "pkv" | "beihilfe" | "supplementary" | "other" | null;
  approved_minor?: number | null;
  deductible_minor?: number | null;
  noncovered_minor?: number | null;
  items?: ExtractedStatementItem[];
}

export interface DocumentExtraction {
  id: string;
  document_id: string;
  model_name: string | null;
  status: "success" | "needs_review" | "failed" | "needs_ocr";
  confidence: number | null;
  structured_data: ExtractedDocumentData;
  field_evidence: Record<string, unknown>;
  validation_warnings: unknown[];
  confirmed_data: ExtractedDocumentData | null;
  confirmed_at: string | null;
  created_at: string;
  previous_confirmed_data?: ExtractedDocumentData | null;
}

export const reimbursementDocumentsQueryKey = (householdId: string) =>
  ["reimbursement-documents", householdId] as const;

export function listReimbursementPeople(
  token: string,
  householdId: string,
): Promise<ReimbursementPerson[]> {
  return apiFetch<ReimbursementPerson[]>("/v1/reimbursements/people", {
    token,
    householdId,
  });
}

export function createReimbursementPerson(
  token: string,
  householdId: string,
  displayName: string,
): Promise<ReimbursementPerson> {
  return apiFetch<ReimbursementPerson>("/v1/reimbursements/people", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ display_name: displayName }),
  });
}

export function listReimbursementCases(
  token: string,
  householdId: string,
): Promise<ReimbursementCase[]> {
  return apiFetch<ReimbursementCase[]>("/v1/reimbursements/cases", { token, householdId });
}

export function getReimbursementSummary(
  token: string,
  householdId: string,
  year: number,
): Promise<ReimbursementSummary> {
  return apiFetch<ReimbursementSummary>(`/v1/reimbursements/summary?year=${year}`, {
    token,
    householdId,
  });
}

export function uploadReimbursementDocument(
  token: string,
  householdId: string,
  personId: string,
  file: File,
): Promise<ReimbursementDocumentUpload> {
  const form = new FormData();
  form.set("person_id", personId);
  form.set("file", file, file.name);
  return apiFetch<ReimbursementDocumentUpload>("/v1/reimbursements/documents", {
    token,
    householdId,
    method: "POST",
    body: form,
  });
}

export function listReimbursementDocuments(
  token: string,
  householdId: string,
): Promise<ReimbursementDocumentView[]> {
  return apiFetch<ReimbursementDocumentView[]>("/v1/reimbursements/documents", {
    token,
    householdId,
  });
}

/**
 * Laedt das Original als Blob.
 *
 * Bewusst kein <iframe src="/v1/...">: der Endpunkt verlangt Bearer-Token und
 * X-Household-Id, die ein Browser bei einer Navigation nicht mitschickt. Die
 * Bytes bleiben im Speicher des Tabs und werden nur ueber eine Object-URL
 * angezeigt, die der Aufrufer wieder freigeben muss.
 */
export async function fetchReimbursementDocumentFile(
  token: string,
  householdId: string,
  documentId: string,
): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/v1/reimbursements/documents/${documentId}/download`, {
      headers: { Authorization: `Bearer ${token}`, "X-Household-Id": householdId },
    });
  } catch {
    throw new ApiError("Die API ist nicht erreichbar. Läuft der Backend-Dienst?", undefined, true);
  }
  if (!response.ok) {
    throw new ApiError(
      response.status === 410
        ? "Das Original liegt nicht mehr in der privaten Ablage."
        : `Das Dokument konnte nicht geladen werden (HTTP ${response.status}).`,
      response.status,
    );
  }
  return response.blob();
}

export async function fetchReimbursementDocumentPage(
  token: string,
  householdId: string,
  documentId: string,
  page: number,
): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/v1/reimbursements/documents/${documentId}/pages/${page}`,
    {
      headers: { Authorization: `Bearer ${token}`, "X-Household-Id": householdId },
    },
  );
  if (!response.ok)
    throw new ApiError("Die PDF-Seite konnte nicht geladen werden.", response.status);
  return response.blob();
}

export interface ReimbursementPayer {
  id: string;
  name: string;
  payer_type: "pkv" | "beihilfe" | "supplementary" | "other";
}

export function listReimbursementPayers(
  token: string,
  householdId: string,
): Promise<ReimbursementPayer[]> {
  return apiFetch("/v1/reimbursements/payers", { token, householdId });
}

export function deleteReimbursementDocument(
  token: string,
  householdId: string,
  documentId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/reimbursements/documents/${documentId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function reprocessReimbursementDocument(
  token: string,
  householdId: string,
  documentId: string,
): Promise<ReimbursementDocumentUpload> {
  return apiFetch<ReimbursementDocumentUpload>(
    `/v1/reimbursements/documents/${documentId}/reprocess`,
    { token, householdId, method: "POST" },
  );
}

export interface ReimbursementCaseCandidate {
  case_id: string;
  person_id: string;
  person_name: string;
  provider_name: string;
  invoice_number: string | null;
  invoice_date: string;
  billed_minor: number;
  reimbursed_minor: number;
  currency: string;
  overall_status: ReimbursementCase["overall_status"];
  score: number;
  evidence: string[];
  version?: number;
  expected_minor?: number;
  item_index?: number | null;
  manual?: boolean;
}

export function getReimbursementCaseCandidates(
  token: string,
  householdId: string,
  documentId: string,
  itemIndex?: number | null,
): Promise<ReimbursementCaseCandidate[]> {
  return apiFetch<ReimbursementCaseCandidate[]>(
    `/v1/reimbursements/documents/${documentId}/case-candidates${itemIndex == null ? "" : `?item_index=${itemIndex}`}`,
    { token, householdId },
  );
}

export interface DocumentLinkOptions {
  item_index?: number | null;
  payer_id?: string | null;
  expected_case_version?: number;
  apply_decision?: boolean;
}

export interface DocumentCasePreview {
  case_id: string;
  case_version: number;
  expected_before_minor: number;
  expected_after_minor: number;
  received_minor: number;
  outstanding_after_minor: number;
  approved_minor: number | null;
  currency: string;
}

export function reimbursementErrorMessage(error: unknown): string {
  const messages: Record<string, string> = {
    statement_approved_amount_missing:
      "Der bewilligte Betrag ist unbekannt. Bitte am Bescheid prüfen.",
    statement_payer_required: "Bitte den Kostenträger erfassen oder auswählen.",
    statement_confirmation_required: "Bitte zuerst die gelesenen Felder bestätigen.",
    statement_case_amount_mismatch:
      "Rechnungsbetrag und Vorgang stimmen nicht überein. Bitte die Zuordnung prüfen.",
    statement_case_number_mismatch: "Die Rechnungsnummer gehört zu einem anderen Vorgang.",
    statement_case_currency_mismatch: "Dokument und Vorgang haben unterschiedliche Währungen.",
    transaction_allocation_currency_mismatch:
      "Bankbuchung und Vorgang haben unterschiedliche Währungen.",
    statement_linked_item_removed:
      "Eine zugeordnete Rechnung fehlt. Bitte zuerst ihre Zuordnung lösen.",
    statement_totals_exceed_invoice:
      "Die Bescheide ergeben zusammen mehr als den Rechnungsbetrag. Bitte doppelte oder korrigierte Bescheide prüfen.",
    statement_approved_exceeds_billed: "Der bewilligte Betrag übersteigt den Rechnungsbetrag.",
    statement_item_already_linked:
      "Diese Rechnung ist bereits einem Vorgang zugeordnet. Zuerst die Zuordnung lösen.",
    reimbursement_case_changed:
      "Der Vorgang wurde inzwischen geändert. Bitte den Vergleich neu öffnen.",
    health_document_case_person_mismatch:
      "Die Person im Dokument stimmt nicht mit dem Vorgang überein.",
  };
  return error instanceof ApiError && error.detail && messages[error.detail]
    ? (messages[error.detail] ?? error.message)
    : error instanceof Error
      ? error.message
      : "Unbekannter Fehler";
}

export async function getReimbursementCasePreview(
  token: string,
  householdId: string,
  documentId: string,
  caseId: string,
  options: DocumentLinkOptions,
): Promise<DocumentCasePreview> {
  try {
    return await apiFetch(`/v1/reimbursements/documents/${documentId}/case-preview`, {
      token,
      householdId,
      method: "POST",
      body: JSON.stringify({ case_id: caseId, ...options }),
    });
  } catch (error) {
    throw new Error(reimbursementErrorMessage(error), { cause: error });
  }
}

export function unlinkReimbursementDocumentCase(
  token: string,
  householdId: string,
  documentId: string,
  itemIndex: number | null,
): Promise<ReimbursementDocumentView> {
  return apiFetch(
    `/v1/reimbursements/documents/${documentId}/case${itemIndex == null ? "" : `?item_index=${itemIndex}`}`,
    { token, householdId, method: "DELETE" },
  );
}

export function linkReimbursementDocumentCase(
  token: string,
  householdId: string,
  documentId: string,
  caseId: string,
  options: DocumentLinkOptions = {},
): Promise<{ document: ReimbursementDocumentView; case: ReimbursementCase }> {
  return apiFetch(`/v1/reimbursements/documents/${documentId}/case`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ case_id: caseId, apply_decision: true, ...options }),
  });
}

export interface ReimbursementBulkReprocess {
  queued: number;
  task_id: string | null;
}

export function reprocessAllReimbursementDocuments(
  token: string,
  householdId: string,
): Promise<ReimbursementBulkReprocess> {
  return apiFetch<ReimbursementBulkReprocess>("/v1/reimbursements/documents/reprocess-all", {
    token,
    householdId,
    method: "POST",
  });
}

export function getReimbursementExtraction(
  token: string,
  householdId: string,
  documentId: string,
): Promise<DocumentExtraction> {
  return apiFetch<DocumentExtraction>(`/v1/reimbursements/documents/${documentId}/extraction`, {
    token,
    householdId,
  });
}

export function confirmReimbursementExtraction(
  token: string,
  householdId: string,
  documentId: string,
  structuredData: ExtractedDocumentData,
  expectedCaseVersion?: number,
): Promise<{ extraction: DocumentExtraction; case: ReimbursementCase | null }> {
  return apiFetch(`/v1/reimbursements/documents/${documentId}/confirm-extraction`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({
      structured_data: structuredData,
      create_case: true,
      expected_case_version: expectedCaseVersion,
    }),
  });
}

// ---------------------------------------------------------------------------
// Erstattungsfaehigkeit am Buchungsstrom
// ---------------------------------------------------------------------------

export type MarkState = "expected" | "excluded";
export type ExpectationMode = "full" | "quota" | "fixed" | "unknown";
export type MarkSource = "manual" | "rule" | "category";

export interface ReimbursementMark {
  id: string;
  transaction_id: string;
  state: MarkState;
  expectation_mode: ExpectationMode;
  expectation_rate: string | null;
  expectation_minor: number | null;
  /** Aus Modus und Buchungsbetrag gerechnet. null bei "unknown" und bei
   *  "excluded" - in beiden Faellen waere jede Zahl erfunden. */
  expected_minor: number | null;
  source: MarkSource;
  rule_id: string | null;
  converted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OpenReimbursementMark extends ReimbursementMark {
  booked_date: string;
  amount_minor: number;
  payee_label: string | null;
  note: string | null;
}

export interface ReimbursementMarkInput {
  state?: MarkState;
  expectation_mode?: ExpectationMode;
  expectation_rate?: string | null;
  expectation_minor?: number | null;
}

export interface BulkMarkItem {
  transaction_id: string;
  status: "applied" | "unchanged" | "skipped" | "conflict";
  reason: string | null;
}

export interface BulkMarkResult {
  applied: number;
  unchanged: number;
  skipped: number;
  conflict: number;
  items: BulkMarkItem[];
}

export interface RuleMarkWithdrawResult {
  withdrawn: number;
  kept_manual: number;
  kept_converted: number;
}

export function listReimbursementMarks(
  token: string,
  householdId: string,
  options: { onlyOpen?: boolean; limit?: number } = {},
): Promise<OpenReimbursementMark[]> {
  const params = new URLSearchParams();
  if (options.onlyOpen === false) params.set("only_open", "false");
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.toString();
  return apiFetch<OpenReimbursementMark[]>(
    `/v1/reimbursements/marks/open${query ? `?${query}` : ""}`,
    { token, householdId },
  );
}

export function setReimbursementMark(
  token: string,
  householdId: string,
  transactionId: string,
  input: ReimbursementMarkInput,
): Promise<ReimbursementMark> {
  return apiFetch<ReimbursementMark>(`/v1/reimbursements/marks/${transactionId}`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function removeReimbursementMark(
  token: string,
  householdId: string,
  transactionId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/reimbursements/marks/${transactionId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

/** Vorschau und Anwendung teilen sich Rumpf und Ergebnis - der einzige
 *  Unterschied ist der Pfad. Getrennte Funktionen wuerden nur denselben Code
 *  zweimal fuehren. */
export function bulkReimbursementMarks(
  token: string,
  householdId: string,
  transactionIds: string[],
  input: ReimbursementMarkInput,
  mode: "preview" | "apply",
): Promise<BulkMarkResult> {
  return apiFetch<BulkMarkResult>(`/v1/reimbursements/marks/bulk/${mode}`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ transaction_ids: transactionIds, ...input }),
  });
}

export function withdrawRuleMarks(
  token: string,
  householdId: string,
  ruleId: string,
  preview: boolean,
): Promise<RuleMarkWithdrawResult> {
  return apiFetch<RuleMarkWithdrawResult>(
    `/v1/reimbursements/marks/by-rule/${ruleId}/withdraw?preview=${preview}`,
    { token, householdId, method: "POST" },
  );
}

export const reimbursementMarksQueryKey = (householdId: string, onlyOpen: boolean) =>
  ["reimbursement-marks", householdId, onlyOpen] as const;

export function convertMarkToCase(
  token: string,
  householdId: string,
  transactionId: string,
  insuredPersonId: string,
): Promise<{ case_id: string }> {
  return apiFetch<{ case_id: string }>(`/v1/reimbursements/marks/${transactionId}/case`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ insured_person_id: insuredPersonId }),
  });
}

// ---------------------------------------------------------------------------
// Bankabgleich: von der erwarteten Erstattung zur eingegangenen Zahlung
// ---------------------------------------------------------------------------

/** Ein Vorschlag, welche Kontobewegung zu einem Vorgang gehoert.
 *
 *  `score` sortiert, `evidence` begruendet. Die Oberflaeche zeigt beides -
 *  eine Trefferliste ohne Begruendung waere nicht nachpruefbar, und zugeordnet
 *  wird ohnehin nur, was jemand angeklickt hat. */
export interface ReimbursementTransactionCandidate {
  transaction_id: string;
  booked_date: string;
  amount_minor: number;
  payee_label: string | null;
  note: string | null;
  score: number;
  evidence: string[];
  /** Was von der Buchung noch frei ist - eine Sammelueberweisung deckt mehrere
   *  Vorgaenge, und der Rest muss sichtbar bleiben. */
  remaining_minor: number;
}

export interface ReimbursementAllocation {
  id: string;
  case_id: string;
  submission_id: string | null;
  transaction_id: string;
  allocation_type: "provider_payment" | "reimbursement";
  amount_minor: number;
  source: string;
  confidence: number | null;
  confirmed_by: string | null;
  created_at: string;
  booked_date: string;
  payee_label: string | null;
  transaction_amount_minor: number;
}

export const reimbursementCandidatesQueryKey = (householdId: string, caseId: string | null) =>
  ["reimbursement-transaction-candidates", householdId, caseId] as const;

export const reimbursementAllocationsQueryKey = (householdId: string, caseId: string | null) =>
  ["reimbursement-allocations", householdId, caseId] as const;

export function listTransactionCandidates(
  token: string,
  householdId: string,
  caseId: string,
): Promise<ReimbursementTransactionCandidate[]> {
  return apiFetch<ReimbursementTransactionCandidate[]>(
    `/v1/reimbursements/cases/${caseId}/transaction-candidates?allocation_type=reimbursement`,
    { token, householdId },
  );
}

export function listCaseAllocations(
  token: string,
  householdId: string,
  caseId: string,
): Promise<ReimbursementAllocation[]> {
  return apiFetch<ReimbursementAllocation[]>(`/v1/reimbursements/cases/${caseId}/allocations`, {
    token,
    householdId,
  });
}

export function createAllocation(
  token: string,
  householdId: string,
  caseId: string,
  transactionId: string,
  amountMinor: number,
): Promise<ReimbursementAllocation> {
  return apiFetch<ReimbursementAllocation>(`/v1/reimbursements/cases/${caseId}/allocations`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({
      transaction_id: transactionId,
      allocation_type: "reimbursement",
      amount_minor: amountMinor,
    }),
  });
}

export function deleteAllocation(
  token: string,
  householdId: string,
  caseId: string,
  allocationId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/reimbursements/cases/${caseId}/allocations/${allocationId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}
