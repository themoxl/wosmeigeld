import {
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { BankReconciliationPanel } from "../components/reimbursements/BankReconciliationPanel";
import { ReimbursementModal } from "../components/reimbursements/ReimbursementModal";
import { useFormatDate } from "../hooks/useProfile";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type {
  ReimbursementAllocation,
  ReimbursementTransactionCandidate,
} from "../lib/reimbursementsApi";
import "./reimbursements.css";

export interface ReimbursementSummary {
  year: number;
  currency: string;
  actionable_count: number;
  processing_count: number;
  expected_minor: number;
  received_minor: number;
  final_own_share_minor: number;
  unresolved_minor: number;
}

export interface ReimbursementCase {
  id: string;
  person_id: string;
  person_name: string;
  provider_name: string;
  invoice_number: string | null;
  invoice_date: string;
  due_date: string | null;
  billed_minor: number;
  reimbursed_minor: number;
  outstanding_minor: number;
  currency: string;
  overall_status: "draft" | "action_required" | "waiting" | "reconciled";
  payment_status: string;
  submission_status: string;
  decision_status: string;
  reimbursement_status: string;
  next_action: string | null;
  document_count: number;
  updated_at: string;
  expected_minor?: number;
  unverified_reimbursed_minor?: number;
  version?: number;
}

export interface ReimbursementDocumentView {
  id: string;
  insured_person_id: string;
  /** Optional, weil ältere API-Antworten nur die Personen-ID enthalten. */
  insured_person_name?: string | null;
  case_id: string | null;
  document_type:
    | "unknown"
    | "invoice"
    | "prescription"
    | "submission_receipt"
    | "benefit_statement"
    | "request_for_documents"
    | "other";
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  processing_status:
    "queued" | "extracting" | "needs_ocr" | "needs_review" | "confirmed" | "failed" | "duplicate";
  extraction_confidence: number | null;
  error_message: string | null;
  created_at: string;
  linked_case_ids?: string[];
  linked_items?: { item_index: number | null; case_id: string }[];
}

export interface ReimbursementPersonOption {
  id: string;
  name: string;
}

export interface ReimbursementsPageProps {
  summary?: ReimbursementSummary;
  cases?: ReimbursementCase[];
  loading?: boolean;
  error?: string | null;
  people?: ReimbursementPersonOption[];
  documents?: ReimbursementDocumentView[];
  onReload?: () => void;
  onUpload?: (files: File[], personId: string) => Promise<void>;
  /** Ausgewaehltes Dokument der Arbeitsspalte; `null` blendet sie aus. */
  selectedDocumentId?: string | null;
  onSelectDocument?: (documentId: string | null) => void;
  /** Rechte Arbeitsspalte. Die Route liefert sie fertig verdrahtet. */
  documentInspector?: ReactNode;
  onCreatePerson?: (displayName: string) => Promise<void>;
  createPersonPending?: boolean;
  /** Sammel-Neuauswertung aller ruhenden Dokumente. */
  onReprocessAllDocuments?: () => void;
  reprocessAllPending?: boolean;
  /** Bankabgleich: Auswahl und Daten des gerade offenen Vorgangs. Die Route
   *  laedt Kandidaten und Zuordnungen erst, wenn einer ausgewaehlt ist. */
  selectedBankCaseId?: string | null;
  onSelectBankCase?: (caseId: string | null) => void;
  bankCandidates?: ReimbursementTransactionCandidate[];
  bankAllocations?: ReimbursementAllocation[];
  bankDetailLoading?: boolean;
  bankBusy?: boolean;
  bankError?: string | null;
  onAllocate?: (caseId: string, transactionId: string, amountMinor: number) => void;
  onRemoveAllocation?: (caseId: string, allocationId: string) => void;
}

type MainTab = "cases" | "documents" | "bank";
type UploadState = "idle" | "selected" | "person" | "uploading" | "success" | "error";
type StepState = "done" | "current" | "open";

const STATUS_META: Record<
  ReimbursementCase["overall_status"],
  { label: string; symbol: string; hint: string }
> = {
  action_required: {
    label: "Handlungsbedarf",
    symbol: "!",
    hint: "Eine konkrete Aktion ist offen",
  },
  waiting: { label: "Wartet", symbol: "◷", hint: "Bei Versicherer oder Bank in Bearbeitung" },
  reconciled: { label: "Abgeglichen", symbol: "✓", hint: "Entscheidung und Geldfluss stimmen" },
  draft: { label: "Entwurf", symbol: "○", hint: "Noch nicht zur Einreichung freigegeben" },
};

const MAIN_TABS: { id: MainTab; label: string }[] = [
  { id: "cases", label: "Vorgänge" },
  { id: "documents", label: "Dokumente" },
  { id: "bank", label: "Bankabgleich" },
];

const STATUS_OPTIONS: { value: "all" | ReimbursementCase["overall_status"]; label: string }[] = [
  { value: "all", label: "Alle Status" },
  { value: "action_required", label: "Handlungsbedarf" },
  { value: "waiting", label: "Wartet" },
  { value: "reconciled", label: "Abgeglichen" },
  { value: "draft", label: "Entwurf" },
];

const COMPLETE_STATUS_WORDS = [
  "paid",
  "completed",
  "submitted",
  "approved",
  "partial",
  "rejected",
  "received",
  "reconciled",
  "not_required",
  "not_expected",
  "bezahlt",
  "eingereicht",
  "entschieden",
  "eingegangen",
  "abgeglichen",
];

const OPEN_STATUS_WORDS = [
  "open",
  "missing",
  "not_submitted",
  "not_received",
  "overdue",
  "offen",
  "fehlt",
];

function formatMoney(minorUnits: number, currency: string): string {
  return formatMinorUnitsAsEuro(minorUnits, currency || "EUR");
}

function caseYear(item: ReimbursementCase): number {
  const year = Number(item.invoice_date.slice(0, 4));
  return Number.isFinite(year) ? year : new Date().getFullYear();
}

function ownShare(item: ReimbursementCase): number {
  return Math.max(0, item.billed_minor - item.reimbursed_minor - item.outstanding_minor);
}

function deriveSummary(
  items: ReimbursementCase[],
  year: number,
  currency: string,
): ReimbursementSummary {
  return items.reduce<ReimbursementSummary>(
    (result, item) => {
      if (item.overall_status === "action_required") result.actionable_count += 1;
      if (item.overall_status === "waiting") result.processing_count += 1;
      if (item.overall_status !== "reconciled") {
        result.expected_minor += Math.max(0, item.outstanding_minor);
      }
      if (item.overall_status === "action_required") {
        result.unresolved_minor += Math.max(0, item.outstanding_minor);
      }
      result.received_minor += Math.max(0, item.reimbursed_minor);
      result.final_own_share_minor += ownShare(item);
      return result;
    },
    {
      year,
      currency,
      actionable_count: 0,
      processing_count: 0,
      expected_minor: 0,
      received_minor: 0,
      final_own_share_minor: 0,
      unresolved_minor: 0,
    },
  );
}

function normalizedSearchValue(value: string): string {
  return value.trim().toLocaleLowerCase("de-DE");
}

function urgencyTime(item: ReimbursementCase): number {
  const source = item.due_date ?? item.updated_at;
  const time = new Date(source).getTime();
  return Number.isNaN(time) ? Number.MAX_SAFE_INTEGER : time;
}

function urgencyLabel(item: ReimbursementCase, formatDate: (value: string) => string): string {
  if (item.overall_status === "waiting") {
    return `Wartet · Stand ${formatDate(item.updated_at)}`;
  }
  if (!item.due_date) return "Jetzt prüfen";

  const due = new Date(`${item.due_date.slice(0, 10)}T12:00:00`);
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  const days = Math.round((due.getTime() - today.getTime()) / 86_400_000);
  if (days < 0) return `${Math.abs(days)} Tag${Math.abs(days) === 1 ? "" : "e"} überfällig`;
  if (days === 0) return "Heute fällig";
  return `Fällig in ${days} Tagen`;
}

function readableStatus(status: string): string {
  const labels: Record<string, string> = {
    paid: "Bezahlt",
    open: "Offen",
    overdue: "Überfällig",
    not_paid: "Nicht bezahlt",
    submitted: "Eingereicht",
    not_submitted: "Nicht eingereicht",
    pending: "In Bearbeitung",
    in_review: "In Prüfung",
    approved: "Anerkannt",
    partial: "Teilweise anerkannt",
    rejected: "Abgelehnt",
    received: "Eingegangen",
    not_received: "Noch nicht eingegangen",
    reconciled: "Abgeglichen",
    not_reconciled: "Nicht abgeglichen",
    not_required: "Nicht erforderlich",
    not_expected: "Keine Zahlung erwartet",
    missing: "Fehlt",
    draft: "Entwurf",
  };
  return labels[status] ?? status.replaceAll("_", " ");
}

function timelineState(status: string): StepState {
  const normalized = status.toLocaleLowerCase("de-DE");
  // Negierte Serverwerte wie `not_received` enthalten ebenfalls das Wort
  // `received`. Die offenen Zustände müssen deshalb zuerst geprüft werden.
  if (OPEN_STATUS_WORDS.some((word) => normalized.includes(word))) return "open";
  if (COMPLETE_STATUS_WORDS.some((word) => normalized.includes(word))) return "done";
  return "current";
}

function StatusChip({ status }: { status: ReimbursementCase["overall_status"] }) {
  const meta = STATUS_META[status];
  return (
    <span
      className={`reimbursement-status reimbursement-status--${status}`}
      title={meta.hint}
      data-status={status}
    >
      <span className="reimbursement-status-symbol" aria-hidden="true">
        {meta.symbol}
      </span>
      {meta.label}
    </span>
  );
}

function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone: "action" | "waiting" | "expected" | "settled" | "own";
}) {
  return (
    <article className={`card metric-card reimbursement-metric reimbursement-metric--${tone}`}>
      <span className="metric-label">{label}</span>
      <strong className="metric-value reimbursement-metric-value">{value}</strong>
      <span className="metric-hint">{hint}</span>
    </article>
  );
}

function Verdict({ summary }: { summary: ReimbursementSummary }) {
  const settled = summary.actionable_count === 0 && summary.unresolved_minor === 0;
  return (
    <section
      className={`reimbursement-verdict reimbursement-verdict--${settled ? "settled" : "action"}`}
      aria-labelledby="reimbursement-verdict-title"
      aria-live="polite"
    >
      <span className="reimbursement-verdict-mark" aria-hidden="true">
        {settled ? "✓" : "!"}
      </span>
      <div className="reimbursement-verdict-copy">
        <span className="reimbursement-verdict-kicker">
          {settled ? "ABGLEICH VOLLSTÄNDIG" : "ABGLEICH OFFEN"}
        </span>
        <h2 id="reimbursement-verdict-title">
          {settled
            ? "Alles abgeglichen"
            : summary.actionable_count === 1
              ? "1 Vorgang braucht dich"
              : `${summary.actionable_count} Vorgänge brauchen dich`}
        </h2>
        <p>
          {settled
            ? "Alle erfassten Rechnungen, Entscheidungen und Kontobewegungen passen zusammen."
            : "Rechnung, Einreichung und Bank werden getrennt geprüft – damit kein offener Schritt hinter einem grünen Gesamtstatus verschwindet."}
        </p>
      </div>
      <div className="reimbursement-verdict-amount">
        <span>{settled ? "Noch ungeklärt" : "Ungeklärter Betrag"}</span>
        <strong>{formatMoney(summary.unresolved_minor, summary.currency)}</strong>
      </div>
    </section>
  );
}

function EmptyState({
  title,
  children,
  symbol = "✓",
}: {
  title: string;
  children: string;
  symbol?: string;
}) {
  return (
    <div className="reimbursement-empty">
      <span className="reimbursement-empty-symbol" aria-hidden="true">
        {symbol}
      </span>
      <div>
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
    </div>
  );
}

function ActionInbox({
  items,
  indicatedCount,
  onOpen,
}: {
  items: ReimbursementCase[];
  indicatedCount: number;
  onOpen: (item: ReimbursementCase) => void;
}) {
  const { formatDate } = useFormatDate();
  return (
    <section
      className="reimbursement-section reimbursement-inbox"
      aria-labelledby="action-inbox-title"
    >
      <div className="reimbursement-section-heading">
        <div>
          <span className="reimbursement-section-index" aria-hidden="true">
            01
          </span>
          <div>
            <h2 id="action-inbox-title">Als Nächstes</h2>
            <p>Die dringlichsten Schritte, nach Fälligkeit sortiert.</p>
          </div>
        </div>
        {items.length > 0 && (
          <span className="reimbursement-count-label">{items.length} sichtbar</span>
        )}
      </div>

      {items.length === 0 ? (
        indicatedCount > 0 ? (
          <EmptyState title="Für diesen Filter kein Vorgang" symbol="i">
            Die Kennzahl enthält weiteren Handlungsbedarf außerhalb der derzeit sichtbaren Auswahl.
          </EmptyState>
        ) : (
          <EmptyState title="Nichts zu tun">
            Für die aktuelle Auswahl ist keine aktive Aufgabe offen.
          </EmptyState>
        )
      ) : (
        <ol className="reimbursement-action-list">
          {items.map((item) => (
            <li key={item.id} className="reimbursement-action-row">
              <span className="reimbursement-person-mark" title={item.person_name}>
                {item.person_name.slice(0, 1).toLocaleUpperCase("de-DE")}
              </span>
              <div className="reimbursement-action-copy">
                <div className="reimbursement-action-lead">
                  <strong>{item.next_action ?? "Vorgang prüfen"}</strong>
                  <span>{item.person_name}</span>
                </div>
                <span className="reimbursement-action-context">
                  {item.provider_name}
                  {item.invoice_number ? ` · Rechnung ${item.invoice_number}` : ""}
                </span>
              </div>
              <div className="reimbursement-action-money">
                <strong>
                  {formatMoney(
                    item.outstanding_minor > 0 ? item.outstanding_minor : item.billed_minor,
                    item.currency,
                  )}
                </strong>
                <span>{urgencyLabel(item, formatDate)}</span>
              </div>
              <button
                type="button"
                className="btn-secondary reimbursement-open-button"
                onClick={() => onOpen(item)}
                aria-label={`${item.provider_name} für ${item.person_name} öffnen`}
              >
                Öffnen <span aria-hidden="true">→</span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function CaseLedger({
  items,
  allScopeItems,
  search,
  onSearchChange,
  status,
  onStatusChange,
  onOpen,
}: {
  items: ReimbursementCase[];
  allScopeItems: ReimbursementCase[];
  search: string;
  onSearchChange: (value: string) => void;
  status: "all" | ReimbursementCase["overall_status"];
  onStatusChange: (value: "all" | ReimbursementCase["overall_status"]) => void;
  onOpen: (item: ReimbursementCase) => void;
}) {
  const { formatDate } = useFormatDate();
  return (
    <section className="reimbursement-ledger" aria-labelledby="case-ledger-title">
      <div className="reimbursement-ledger-heading">
        <div>
          <h2 id="case-ledger-title">Vorgangsledger</h2>
          <p>Vom Rechnungsbetrag bis zur bestätigten Kontobuchung.</p>
        </div>
        <span className="reimbursement-count-label">
          {items.length} von {allScopeItems.length}
        </span>
      </div>

      <div className="reimbursement-ledger-toolbar" aria-label="Vorgänge filtern">
        <label className="reimbursement-search-field">
          <span className="reimbursement-visually-hidden">Vorgänge durchsuchen</span>
          <span aria-hidden="true">⌕</span>
          <input
            type="search"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Person, Praxis oder Rechnungsnummer"
          />
        </label>
        <label className="reimbursement-filter-field">
          <span>Status</span>
          <select
            className="form-input"
            value={status}
            onChange={(event) =>
              onStatusChange(event.target.value as "all" | ReimbursementCase["overall_status"])
            }
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title={allScopeItems.length === 0 ? "Noch keine Vorgänge" : "Kein Treffer"}
          symbol="○"
        >
          {allScopeItems.length === 0
            ? "Sobald eine Rechnung erfasst ist, erscheint hier ihre vollständige Abrechnungskette."
            : "Passe Suche oder Statusfilter an, um wieder Vorgänge zu sehen."}
        </EmptyState>
      ) : (
        <>
          <div
            className="reimbursement-table-region"
            role="region"
            aria-label="Erstattungsvorgänge als Tabelle"
            tabIndex={0}
          >
            <table className="txn-table reimbursement-table">
              <thead>
                <tr>
                  <th className="txn-th">Person</th>
                  <th className="txn-th">Praxis / Rechnung</th>
                  <th className="txn-th">Datum</th>
                  <th className="txn-th txn-th--amount">Berechnet</th>
                  <th className="txn-th txn-th--amount">Erhalten</th>
                  <th className="txn-th txn-th--amount">Offen / Eigenanteil</th>
                  <th className="txn-th">Status</th>
                  <th className="txn-th">Nächster Schritt</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.id}
                    className={`txn-tr reimbursement-table-row reimbursement-table-row--${item.overall_status}`}
                  >
                    <td className="txn-td reimbursement-person-cell">{item.person_name}</td>
                    <td className="txn-td">
                      <button
                        type="button"
                        className="reimbursement-case-link"
                        onClick={() => onOpen(item)}
                      >
                        {item.provider_name}
                      </button>
                      <span className="reimbursement-cell-note">
                        {item.invoice_number
                          ? `Rechnung ${item.invoice_number}`
                          : "Ohne Rechnungsnummer"}
                        {` · ${item.document_count} Dok.`}
                      </span>
                    </td>
                    <td className="txn-td reimbursement-date-cell">
                      {formatDate(item.invoice_date)}
                    </td>
                    <td className="txn-td txn-td--amount reimbursement-money">
                      {formatMoney(item.billed_minor, item.currency)}
                    </td>
                    <td className="txn-td txn-td--amount reimbursement-money reimbursement-money--positive">
                      {formatMoney(item.reimbursed_minor, item.currency)}
                    </td>
                    <td className="txn-td txn-td--amount reimbursement-money">
                      <strong>{formatMoney(item.outstanding_minor, item.currency)}</strong>
                      <span className="reimbursement-cell-note">
                        Eigenanteil {formatMoney(ownShare(item), item.currency)}
                      </span>
                    </td>
                    <td className="txn-td">
                      <StatusChip status={item.overall_status} />
                    </td>
                    <td className="txn-td reimbursement-next-cell">
                      <span>{item.next_action ?? "Kein Schritt offen"}</span>
                      <button
                        type="button"
                        className="reimbursement-inline-open"
                        onClick={() => onOpen(item)}
                        aria-label={`${item.provider_name} öffnen`}
                      >
                        Öffnen
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="reimbursement-mobile-cases" aria-label="Erstattungsvorgänge als Karten">
            {items.map((item) => (
              <article
                key={item.id}
                className={`reimbursement-mobile-case reimbursement-mobile-case--${item.overall_status}`}
              >
                <div className="reimbursement-mobile-case-head">
                  <div>
                    <span>{item.person_name}</span>
                    <h3>{item.provider_name}</h3>
                    <p>
                      {formatDate(item.invoice_date)}
                      {item.invoice_number ? ` · ${item.invoice_number}` : ""}
                    </p>
                  </div>
                  <StatusChip status={item.overall_status} />
                </div>
                <dl className="reimbursement-mobile-money">
                  <div>
                    <dt>Berechnet</dt>
                    <dd>{formatMoney(item.billed_minor, item.currency)}</dd>
                  </div>
                  <div>
                    <dt>Erhalten</dt>
                    <dd>{formatMoney(item.reimbursed_minor, item.currency)}</dd>
                  </div>
                  <div>
                    <dt>Offen</dt>
                    <dd>{formatMoney(item.outstanding_minor, item.currency)}</dd>
                  </div>
                </dl>
                <div className="reimbursement-mobile-next">
                  <span>Nächster Schritt</span>
                  <strong>{item.next_action ?? "Kein Schritt offen"}</strong>
                </div>
                <button
                  type="button"
                  className="btn-secondary reimbursement-mobile-open"
                  onClick={() => onOpen(item)}
                >
                  Vorgang öffnen
                </button>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

const DOCUMENT_STATUS: Record<
  ReimbursementDocumentView["processing_status"],
  { label: string; tone: string }
> = {
  queued: { label: "Wartet", tone: "waiting" },
  extracting: { label: "Wird ausgewertet", tone: "waiting" },
  needs_ocr: { label: "OCR erforderlich", tone: "action" },
  needs_review: { label: "Bitte prüfen", tone: "action" },
  confirmed: { label: "Bestätigt", tone: "settled" },
  failed: { label: "Auswertung fehlgeschlagen", tone: "action" },
  duplicate: { label: "Dublette", tone: "neutral" },
};

function DocumentsPanel({
  documents,
  personNames,
  showPersonAttribution,
  selectedDocumentId,
  onSelect,
  onUpload,
  uploading,
  inspector,
  onReprocessAll,
  reprocessAllPending,
}: {
  documents: ReimbursementDocumentView[];
  personNames: ReadonlyMap<string, string>;
  showPersonAttribution: boolean;
  selectedDocumentId: string | null;
  onSelect?: (documentId: string | null) => void;
  onUpload: () => void;
  uploading: boolean;
  inspector?: ReactNode;
  onReprocessAll?: () => void;
  reprocessAllPending?: boolean;
}) {
  const [reprocessArmed, setReprocessArmed] = useState(false);
  const { formatDate } = useFormatDate();
  if (documents.length === 0) {
    return (
      <div className="reimbursement-foundation-panel">
        <EmptyState title="Noch keine Dokumente">
          Lade eine Rechnung oder Leistungsabrechnung hoch. Die Auswertung bleibt bis zu deiner
          Bestätigung ein Vorschlag.
        </EmptyState>
        <button type="button" className="btn-secondary" onClick={onUpload} disabled={uploading}>
          Erstes Dokument auswählen
        </button>
      </div>
    );
  }

  const showInspector = Boolean(selectedDocumentId && inspector);

  return (
    <div
      className={
        showInspector
          ? "reimbursement-documents-workspace reimbursement-documents-workspace--split"
          : "reimbursement-documents-workspace"
      }
    >
      <div className="reimbursement-documents-panel">
        <div className="reimbursement-documents-heading">
          <div>
            <h2>Private Dokumentablage</h2>
            <p>Ein Klick öffnet rechts die gelesenen Felder und das Original.</p>
          </div>
          <div className="reimbursement-documents-tools">
            <button type="button" className="btn-secondary" onClick={onUpload} disabled={uploading}>
              Dokument auswählen
            </button>
            {onReprocessAll &&
              (reprocessArmed ? (
                <span className="reimbursement-bulk-confirm" role="group">
                  <strong>{documents.length} neu auswerten?</strong>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={reprocessAllPending}
                    onClick={() => {
                      onReprocessAll();
                      setReprocessArmed(false);
                    }}
                  >
                    {reprocessAllPending ? "Wird eingereiht …" : "Ja, alle"}
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setReprocessArmed(false)}
                  >
                    Abbrechen
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setReprocessArmed(true)}
                  disabled={reprocessAllPending}
                >
                  Alle neu auswerten
                </button>
              ))}
          </div>
        </div>
        <ul className="reimbursement-document-list">
          {documents.map((document) => {
            const status = DOCUMENT_STATUS[document.processing_status];
            const pageLabel = document.page_count ? document.page_count + " Seite(n) · " : "";
            const personName =
              document.insured_person_name ??
              personNames.get(document.insured_person_id) ??
              "Unbekannte Person";
            const selected = document.id === selectedDocumentId;
            return (
              <li key={document.id}>
                <button
                  type="button"
                  className={
                    selected
                      ? "reimbursement-document-row reimbursement-document-row--selected"
                      : "reimbursement-document-row"
                  }
                  aria-current={selected ? "true" : undefined}
                  onClick={() => onSelect?.(selected ? null : document.id)}
                >
                  <span className="reimbursement-document-symbol" aria-hidden="true">
                    {document.mime_type === "application/pdf" ? "PDF" : "IMG"}
                  </span>
                  <span className="reimbursement-document-copy">
                    <span className="reimbursement-document-name">
                      {document.original_filename}
                    </span>
                    <span className="reimbursement-document-meta">
                      {showPersonAttribution && (
                        <span className="reimbursement-document-person">{personName} · </span>
                      )}
                      {pageLabel}
                      {new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(
                        document.size_bytes / 1024,
                      )}{" "}
                      KB · {formatDate(document.created_at)}
                    </span>
                    {document.error_message && (
                      <small className="reimbursement-document-error">
                        {document.error_message}
                      </small>
                    )}
                  </span>
                  <span
                    className={
                      "reimbursement-document-status reimbursement-document-status--" + status.tone
                    }
                  >
                    {status.label}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
      {showInspector && inspector}
    </div>
  );
}

function CaseDetailDialog({ item, onClose }: { item: ReimbursementCase; onClose: () => void }) {
  const { formatDate } = useFormatDate();
  const steps: { label: string; detail: string; state: StepState }[] = [
    { label: "Rechnung", detail: formatDate(item.invoice_date), state: "done" },
    {
      label: "Zahlung",
      detail: readableStatus(item.payment_status),
      state: timelineState(item.payment_status),
    },
    {
      label: "Einreichung",
      detail: readableStatus(item.submission_status),
      state: timelineState(item.submission_status),
    },
    {
      label: "Entscheidung",
      detail: readableStatus(item.decision_status),
      state: timelineState(item.decision_status),
    },
    {
      label: "Erstattung",
      detail: readableStatus(item.reimbursement_status),
      state: timelineState(item.reimbursement_status),
    },
    {
      label: "Abgleich",
      detail: STATUS_META[item.overall_status].label,
      state: item.overall_status === "reconciled" ? "done" : "open",
    },
  ];

  return (
    <ReimbursementModal
      titleId="reimbursement-dialog-title"
      descriptionId="reimbursement-dialog-description"
      onClose={onClose}
    >
      <header className="reimbursement-dialog-header">
        <div>
          <span className="reimbursement-dialog-eyebrow">{item.person_name}</span>
          <h2 id="reimbursement-dialog-title">{item.provider_name}</h2>
          <p id="reimbursement-dialog-description">
            {item.invoice_number ? `Rechnung ${item.invoice_number} · ` : ""}
            {formatDate(item.invoice_date)}
          </p>
        </div>
        <button
          type="button"
          className="reimbursement-dialog-close"
          onClick={onClose}
          aria-label="Vorgang schließen"
          data-reimbursement-modal-initial-focus
        >
          ×
        </button>
      </header>

      <div className="reimbursement-dialog-statusline">
        <StatusChip status={item.overall_status} />
        <span>Zuletzt aktualisiert: {formatDate(item.updated_at)}</span>
      </div>

      <section className="reimbursement-detail-section" aria-labelledby="financial-summary-title">
        <h3 id="financial-summary-title">Finanzieller Stand</h3>
        <dl className="reimbursement-detail-finances">
          <div>
            <dt>Rechnungsbetrag</dt>
            <dd>{formatMoney(item.billed_minor, item.currency)}</dd>
          </div>
          <div>
            <dt>Erstattet</dt>
            <dd className="reimbursement-positive-text">
              {formatMoney(item.reimbursed_minor, item.currency)}
            </dd>
          </div>
          <div>
            <dt>Noch offen</dt>
            <dd>{formatMoney(item.outstanding_minor, item.currency)}</dd>
          </div>
          <div>
            <dt>Eigenanteil</dt>
            <dd>{formatMoney(ownShare(item), item.currency)}</dd>
          </div>
        </dl>
      </section>

      <section className="reimbursement-detail-section" aria-labelledby="timeline-title">
        <div className="reimbursement-detail-heading">
          <h3 id="timeline-title">Abrechnungskette</h3>
          <span>6 Prüfpunkte</span>
        </div>
        <ol className="reimbursement-timeline">
          {steps.map((step, index) => (
            <li
              key={step.label}
              className={`reimbursement-timeline-step reimbursement-timeline-step--${step.state}`}
            >
              <span className="reimbursement-timeline-index" aria-hidden="true">
                {step.state === "done" ? "✓" : String(index + 1).padStart(2, "0")}
              </span>
              <strong>{step.label}</strong>
              <span>{step.detail}</span>
              <span className="reimbursement-visually-hidden">
                {step.state === "done"
                  ? "Erledigt"
                  : step.state === "current"
                    ? "In Bearbeitung"
                    : "Offen"}
              </span>
            </li>
          ))}
        </ol>
      </section>

      <div className="reimbursement-detail-grid">
        <section className="reimbursement-detail-section" aria-labelledby="sources-title">
          <h3 id="sources-title">Quellen & Dokumente</h3>
          <div className="reimbursement-source-line">
            <span className="reimbursement-source-icon" aria-hidden="true">
              ▤
            </span>
            <div>
              <strong>
                {item.document_count} Dokument{item.document_count === 1 ? "" : "e"} verknüpft
              </strong>
              <p>Rechnung, Entscheidung und Nachweise bleiben am Vorgang nachvollziehbar.</p>
            </div>
          </div>
          <p className="reimbursement-privacy-note">
            Diese Übersicht zeigt bewusst keine Diagnosen oder Behandlungsdetails.
          </p>
        </section>

        <aside className="reimbursement-next-panel" aria-labelledby="next-action-title">
          <span>NÄCHSTE AKTION</span>
          <h3 id="next-action-title">{item.next_action ?? "Kein Schritt offen"}</h3>
          <p>
            {item.next_action
              ? `${urgencyLabel(item, formatDate)}. Öffne die zugehörigen Dokumente, bevor du den Status bestätigst.`
              : "Der Vorgang benötigt derzeit keine weitere Bearbeitung."}
          </p>
        </aside>
      </div>
    </ReimbursementModal>
  );
}

export function ReimbursementsPage({
  summary,
  cases = [],
  loading = false,
  error = null,
  people: providedPeople = [],
  documents = [],
  onReload,
  onUpload,
  selectedDocumentId = null,
  onSelectDocument,
  documentInspector,
  onCreatePerson,
  createPersonPending = false,
  onReprocessAllDocuments,
  reprocessAllPending = false,
  selectedBankCaseId = null,
  onSelectBankCase,
  bankCandidates = [],
  bankAllocations = [],
  bankDetailLoading = false,
  bankBusy = false,
  bankError = null,
  onAllocate,
  onRemoveAllocation,
}: ReimbursementsPageProps = {}) {
  const caseYears = cases.map(caseYear);
  const initialYear =
    summary?.year ?? (caseYears.length > 0 ? Math.max(...caseYears) : new Date().getFullYear());
  const [selectedYear, setSelectedYear] = useState(initialYear);
  const [personId, setPersonId] = useState("all");
  const [activeTab, setActiveTab] = useState<MainTab>("cases");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | ReimbursementCase["overall_status"]>(
    "all",
  );
  const [openCase, setOpenCase] = useState<ReimbursementCase | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadPerson, setUploadPerson] = useState("");
  const [personFormOpen, setPersonFormOpen] = useState(false);
  const [newPersonName, setNewPersonName] = useState("");
  const [personError, setPersonError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const years = useMemo(() => {
    const values = new Set(cases.map(caseYear));
    if (summary) values.add(summary.year);
    if (values.size === 0) values.add(new Date().getFullYear());
    return [...values].sort((a, b) => b - a);
  }, [cases, summary]);

  const people = useMemo(() => {
    const found = new Map(providedPeople.map((person) => [person.id, person.name]));
    cases
      .filter((item) => caseYear(item) === selectedYear)
      .forEach((item) => found.set(item.person_id, item.person_name));
    return [...found.entries()].sort((a, b) => a[1].localeCompare(b[1], "de"));
  }, [cases, providedPeople, selectedYear]);
  const activePersonId =
    personId === "all" || people.some(([id]) => id === personId) ? personId : "all";

  const yearCases = useMemo(
    () => cases.filter((item) => caseYear(item) === selectedYear),
    [cases, selectedYear],
  );
  const scopeCases = useMemo(
    () => yearCases.filter((item) => activePersonId === "all" || item.person_id === activePersonId),
    [activePersonId, yearCases],
  );
  const personNames = useMemo(() => new Map(people), [people]);
  const scopedDocuments = useMemo(
    () =>
      documents.filter(
        (document) => activePersonId === "all" || document.insured_person_id === activePersonId,
      ),
    [activePersonId, documents],
  );

  const scopeCurrency = summary?.currency ?? scopeCases[0]?.currency ?? "EUR";
  const effectiveSummary = useMemo(() => {
    if (summary && selectedYear === summary.year && activePersonId === "all") return summary;
    return deriveSummary(scopeCases, selectedYear, scopeCurrency);
  }, [activePersonId, scopeCases, scopeCurrency, selectedYear, summary]);

  const filteredCases = useMemo(() => {
    const term = normalizedSearchValue(search);
    return scopeCases.filter((item) => {
      if (statusFilter !== "all" && item.overall_status !== statusFilter) return false;
      if (!term) return true;
      const haystack = normalizedSearchValue(
        `${item.person_name} ${item.provider_name} ${item.invoice_number ?? ""}`,
      );
      return haystack.includes(term);
    });
  }, [scopeCases, search, statusFilter]);

  const actionCases = useMemo(
    () =>
      scopeCases
        .filter((item) => item.overall_status === "action_required")
        .sort((a, b) => urgencyTime(a) - urgencyTime(b))
        .slice(0, 5),
    [scopeCases],
  );

  const documentCount = scopedDocuments.length;
  const unreconciledBankCases = scopeCases.filter(
    (item) =>
      item.overall_status !== "reconciled" && timelineState(item.reimbursement_status) === "done",
  );

  /** Alles, wo noch Geld aussteht. Bewusst weiter gefasst als
   *  `unreconciledBankCases`: dort geht es um Vorgaenge, die sich schon als
   *  erhalten ausgeben: hier um die, bei denen tatsaechlich noch etwas offen
   *  ist - genau die, denen man eine Zahlung zuordnen will. */
  const bankReconcileCases = useMemo(
    () =>
      scopeCases
        .filter(
          (item) =>
            item.outstanding_minor > 0 ||
            (item.unverified_reimbursed_minor ?? 0) > 0 ||
            item.reimbursement_status === "mismatch",
        )
        .sort((a, b) => b.outstanding_minor - a.outstanding_minor)
        .map((item) => ({
          id: item.id,
          person_name: item.person_name,
          provider_name: item.provider_name,
          invoice_date: item.invoice_date,
          // Der Web-Typ fuehrt kein expected_minor; die Erwartung ist genau
          // das, was schon da ist plus das, was noch aussteht.
          expected_minor: item.expected_minor ?? item.outstanding_minor + item.reimbursed_minor,
          reimbursed_minor: item.reimbursed_minor,
          outstanding_minor: item.outstanding_minor,
          unverified_reimbursed_minor: item.unverified_reimbursed_minor,
        })),
    [scopeCases],
  );

  const runUpload = async (files: File[], uploadPersonId: string) => {
    if (!onUpload) return;
    setUploadState("uploading");
    try {
      await onUpload(files, uploadPersonId);
      setUploadState("success");
    } catch (uploadFailure) {
      setUploadState("error");
      setUploadError(
        uploadFailure instanceof Error
          ? uploadFailure.message
          : "Die Dokumente konnten nicht hinzugefügt werden.",
      );
    }
  };

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;

    setSelectedFiles(files);
    setUploadError(null);
    if (!onUpload) {
      setUploadState("selected");
      return;
    }

    const uploadPersonId =
      activePersonId !== "all"
        ? activePersonId
        : people.length === 1
          ? (people[0]?.[0] ?? null)
          : null;
    if (!uploadPersonId) {
      // Mehrere Personen und kein Filter gesetzt: nicht abbrechen, sondern
      // genau eine Frage stellen. Die Auswahl bleibt an dieser Stelle, die
      // Dateien bleiben ausgewaehlt.
      setUploadPerson(people[0]?.[0] ?? "");
      setUploadState("person");
      return;
    }

    await runUpload(files, uploadPersonId);
  };

  const submitPendingUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!uploadPerson || selectedFiles.length === 0) return;
    await runUpload(selectedFiles, uploadPerson);
  };

  const resetUpload = () => {
    setSelectedFiles([]);
    setUploadState("idle");
    setUploadError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const submitPerson = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = newPersonName.trim();
    if (!value || !onCreatePerson) return;
    setPersonError(null);
    try {
      await onCreatePerson(value);
      setNewPersonName("");
      setPersonFormOpen(false);
    } catch (failure) {
      setPersonError(
        failure instanceof Error ? failure.message : "Die Person konnte nicht angelegt werden.",
      );
    }
  };

  const handleTabKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % MAIN_TABS.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + MAIN_TABS.length) % MAIN_TABS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = MAIN_TABS.length - 1;
    if (nextIndex === null) return;
    const nextTab = MAIN_TABS[nextIndex];
    if (!nextTab) return;
    event.preventDefault();
    setActiveTab(nextTab.id);
    tabRefs.current[nextIndex]?.focus();
  };

  const uploadMessage =
    uploadState === "uploading"
      ? `${selectedFiles.length} Dokument${selectedFiles.length === 1 ? " wird" : "e werden"} sicher verarbeitet …`
      : uploadState === "success"
        ? `${selectedFiles.length} Dokument${selectedFiles.length === 1 ? " wurde" : "e wurden"} hinzugefügt.`
        : uploadState === "selected"
          ? `${selectedFiles.length} Dokument${selectedFiles.length === 1 ? " ist" : "e sind"} ausgewählt. Der Upload wird mit der Backend-Anbindung aktiviert.`
          : uploadState === "person"
            ? `${selectedFiles.length} Dokument${selectedFiles.length === 1 ? "" : "e"} – zu wem gehört das?`
            : uploadState === "error"
              ? (uploadError ?? "Upload fehlgeschlagen.")
              : "";

  return (
    <section className="page reimbursement-page">
      <header className="page-header reimbursement-page-header">
        <div className="reimbursement-heading-copy">
          <span className="reimbursement-eyebrow">GESUNDHEIT &amp; ERSTATTUNG</span>
          <h1 className="page-heading">Wurde alles abgerechnet?</h1>
          <p>
            Rechnungen, Versicherungsentscheidungen und Kontobewegungen in einer belastbaren Kette.
          </p>
        </div>
        <div className="reimbursement-header-actions">
          <input
            ref={fileInputRef}
            id="reimbursement-document-input"
            className="reimbursement-file-input"
            type="file"
            aria-label="Gesundheitsdokumente auswählen"
            accept=".pdf,image/*"
            multiple
            onChange={handleFileChange}
            disabled={uploadState === "uploading"}
          />
          <button
            type="button"
            className="btn-primary reimbursement-upload-button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadState === "uploading"}
            aria-controls="reimbursement-document-input"
          >
            <span aria-hidden="true">＋</span>
            {uploadState === "uploading" ? "Wird verarbeitet …" : "Dokumente hinzufügen"}
          </button>
          <div className="reimbursement-scope-controls">
            <label>
              <span>Jahr</span>
              <select
                className="form-input"
                value={selectedYear}
                onChange={(event) => setSelectedYear(Number(event.target.value))}
              >
                {years.map((year) => (
                  <option key={year} value={year}>
                    {year}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Person</span>
              <select
                className="form-input"
                value={activePersonId}
                onChange={(event) => setPersonId(event.target.value)}
              >
                <option value="all">Alle Personen</option>
                {people.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            {onCreatePerson && (
              <button
                type="button"
                className="btn-secondary reimbursement-person-toggle"
                aria-expanded={personFormOpen}
                onClick={() => {
                  setPersonError(null);
                  setPersonFormOpen((open) => !open);
                }}
              >
                <span aria-hidden="true">＋</span> Person
              </button>
            )}
          </div>
        </div>
      </header>

      {onCreatePerson && personFormOpen && (
        <form className="reimbursement-person-form" onSubmit={submitPerson}>
          <div className="reimbursement-person-form-copy">
            <strong>Weitere versicherte Person anlegen</strong>
            <p>
              Der Anzeigename genügt. Er entscheidet nur, wem Dokumente und Vorgänge zugeordnet
              werden.
            </p>
          </div>
          <label>
            <span>Anzeigename</span>
            <input
              className="form-input"
              value={newPersonName}
              onChange={(event) => setNewPersonName(event.target.value)}
              maxLength={120}
              required
              autoFocus
              placeholder="z. B. Muster"
            />
          </label>
          <div className="reimbursement-person-form-actions">
            <button type="submit" className="btn-primary" disabled={createPersonPending}>
              {createPersonPending ? "Wird angelegt …" : "Person anlegen"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setPersonFormOpen(false);
                setPersonError(null);
              }}
            >
              Abbrechen
            </button>
          </div>
          {personError && (
            <p className="reimbursement-person-form-error" role="alert">
              {personError}
            </p>
          )}
        </form>
      )}

      <div
        className={`reimbursement-upload-status reimbursement-upload-status--${uploadState}`}
        aria-live="polite"
        role={uploadState === "error" ? "alert" : "status"}
        hidden={uploadState === "idle"}
      >
        <span>{uploadMessage}</span>
        {uploadState === "person" && (
          <form className="reimbursement-upload-person" onSubmit={submitPendingUpload}>
            <label>
              <span className="reimbursement-visually-hidden">Versicherte Person</span>
              <select
                className="form-input"
                value={uploadPerson}
                onChange={(event) => setUploadPerson(event.target.value)}
              >
                {people.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" className="btn-primary" disabled={!uploadPerson}>
              Hochladen
            </button>
          </form>
        )}
        {selectedFiles.length > 0 && uploadState !== "uploading" && (
          <button type="button" onClick={resetUpload}>
            Auswahl zurücksetzen
          </button>
        )}
      </div>

      {loading ? (
        <div className="reimbursement-loading" role="status" aria-live="polite">
          <span className="reimbursement-loading-line reimbursement-loading-line--wide" />
          <span className="reimbursement-loading-line" />
          <span>Erstattungen werden abgeglichen …</span>
        </div>
      ) : error ? (
        <div className="reimbursement-error" role="alert">
          <div>
            <strong>Die Erstattungen konnten nicht geladen werden.</strong>
            <p>{error}</p>
          </div>
          {onReload && (
            <button type="button" className="btn-secondary" onClick={onReload}>
              Erneut laden
            </button>
          )}
        </div>
      ) : (
        <>
          <Verdict summary={effectiveSummary} />

          <section className="reimbursement-metrics" aria-label="Abrechnungskennzahlen">
            <Metric
              label="Handlungsbedarf"
              value={String(effectiveSummary.actionable_count)}
              hint="Vorgänge mit deinem nächsten Schritt"
              tone="action"
            />
            <Metric
              label="Bei Versicherern"
              value={String(effectiveSummary.processing_count)}
              hint="Eingereicht und noch nicht entschieden"
              tone="waiting"
            />
            <Metric
              label="Erstattung erwartet"
              value={formatMoney(effectiveSummary.expected_minor, effectiveSummary.currency)}
              hint="Entschieden oder plausibel noch ausstehend"
              tone="expected"
            />
            <Metric
              label="Auf Konto bestätigt"
              value={formatMoney(effectiveSummary.received_minor, effectiveSummary.currency)}
              hint="Mit echten Bankbuchungen abgeglichen"
              tone="settled"
            />
            <Metric
              label="Endgültiger Eigenanteil"
              value={formatMoney(effectiveSummary.final_own_share_minor, effectiveSummary.currency)}
              hint="Nach abgeschlossenen Erstattungsentscheidungen"
              tone="own"
            />
          </section>

          <ActionInbox
            items={actionCases}
            indicatedCount={effectiveSummary.actionable_count}
            onOpen={setOpenCase}
          />

          <div className="reimbursement-main-tabs" role="tablist" aria-label="Erstattungsbereiche">
            {MAIN_TABS.map((tab, index) => (
              <button
                key={tab.id}
                ref={(node) => {
                  tabRefs.current[index] = node;
                }}
                id={`reimbursement-tab-${tab.id}`}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.id}
                aria-controls={`reimbursement-panel-${tab.id}`}
                tabIndex={activeTab === tab.id ? 0 : -1}
                className={
                  activeTab === tab.id
                    ? "reimbursement-main-tab reimbursement-main-tab--active"
                    : "reimbursement-main-tab"
                }
                onClick={() => setActiveTab(tab.id)}
                onKeyDown={(event) => handleTabKeyDown(event, index)}
              >
                {tab.label}
                {tab.id === "cases" && <span>{scopeCases.length}</span>}
                {tab.id === "documents" && <span>{documentCount}</span>}
              </button>
            ))}
          </div>

          {activeTab === "cases" && (
            <div
              id="reimbursement-panel-cases"
              role="tabpanel"
              aria-labelledby="reimbursement-tab-cases"
            >
              <CaseLedger
                items={filteredCases}
                allScopeItems={scopeCases}
                search={search}
                onSearchChange={setSearch}
                status={statusFilter}
                onStatusChange={setStatusFilter}
                onOpen={setOpenCase}
              />
            </div>
          )}

          {activeTab === "documents" && (
            <div
              id="reimbursement-panel-documents"
              role="tabpanel"
              aria-labelledby="reimbursement-tab-documents"
            >
              <DocumentsPanel
                documents={scopedDocuments}
                personNames={personNames}
                showPersonAttribution={activePersonId === "all"}
                selectedDocumentId={selectedDocumentId}
                onSelect={onSelectDocument}
                onUpload={() => fileInputRef.current?.click()}
                uploading={uploadState === "uploading"}
                inspector={documentInspector}
                onReprocessAll={onReprocessAllDocuments}
                reprocessAllPending={reprocessAllPending}
              />
            </div>
          )}

          {activeTab === "bank" && (
            <div
              id="reimbursement-panel-bank"
              role="tabpanel"
              aria-labelledby="reimbursement-tab-bank"
              className="reimbursement-foundation-panel"
            >
              <BankReconciliationPanel
                cases={bankReconcileCases}
                selectedCaseId={selectedBankCaseId ?? null}
                selectedCase={(() => {
                  const item = scopeCases.find((entry) => entry.id === selectedBankCaseId);
                  return item
                    ? {
                        id: item.id,
                        person_name: item.person_name,
                        provider_name: item.provider_name,
                        invoice_date: item.invoice_date,
                        expected_minor:
                          item.expected_minor ?? item.outstanding_minor + item.reimbursed_minor,
                        reimbursed_minor: item.reimbursed_minor,
                        outstanding_minor: item.outstanding_minor,
                        unverified_reimbursed_minor: item.unverified_reimbursed_minor,
                      }
                    : null;
                })()}
                onSelectCase={onSelectBankCase ?? (() => undefined)}
                candidates={bankCandidates}
                allocations={bankAllocations}
                loadingDetail={bankDetailLoading}
                busy={bankBusy}
                error={bankError}
                onAllocate={onAllocate ?? (() => undefined)}
                onRemoveAllocation={onRemoveAllocation ?? (() => undefined)}
              />
              {unreconciledBankCases.length > 0 && (
                <div className="reimbursement-foundation-summary reimbursement-foundation-summary--action">
                  <span className="reimbursement-foundation-number">
                    {unreconciledBankCases.length}
                  </span>
                  <div>
                    <h2>Als erhalten geführt, aber ohne Kontobewegung</h2>
                    <p>
                      Bei diesen Vorgängen steht die Erstattung auf „eingegangen“, ohne dass eine
                      Buchung daran hängt. Der Betrag ist damit behauptet, nicht belegt.
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {openCase && <CaseDetailDialog item={openCase} onClose={() => setOpenCase(null)} />}
    </section>
  );
}
