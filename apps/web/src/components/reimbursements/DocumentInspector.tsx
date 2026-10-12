import { createContext, useContext, useState, type FormEvent } from "react";
import { EvidencePageViewer, type FieldLocation } from "./EvidencePageViewer";
import { StatementItemsEditor } from "./StatementItemsEditor";
import { DocumentCaseComparison } from "./DocumentCaseComparison";
import type {
  DocumentLinkOptions,
  DocumentCasePreview,
  ExtractedStatementItem,
  ReimbursementPayer,
} from "../../lib/reimbursementsApi";

const LocateContext = createContext<(field: string) => void>(() => {});
import type {
  DocumentExtraction,
  ExtractedDocumentData,
  ReimbursementCaseCandidate,
} from "../../lib/reimbursementsApi";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import { isAiReasonCode } from "../../lib/aiReasons";
import { AiReasonNotice } from "../AiReasonNotice";
import type { ReimbursementDocumentView } from "../../pages/ReimbursementsPage";

export interface DocumentInspectorProps {
  document: ReimbursementDocumentView;
  personName: string;
  extraction: DocumentExtraction | null;
  extractionLoading: boolean;
  extractionError: string | null;
  fileUrl: string | null;
  fileLoading: boolean;
  fileError: string | null;
  confirmPending: boolean;
  confirmError: string | null;
  reprocessPending: boolean;
  deletePending: boolean;
  onConfirm: (data: ExtractedDocumentData) => void;
  onReprocess: () => void;
  onDelete: () => void;
  onClose: () => void;
  /** Vorschläge, zu welchem Vorgang das Dokument gehört. */
  caseCandidates: ReimbursementCaseCandidate[];
  caseCandidatesLoading: boolean;
  linkedCaseLabel: string | null;
  linkPending: boolean;
  linkError: string | null;
  onLinkCase: (caseId: string, options?: DocumentLinkOptions) => void;
  onUnlinkCase?: () => void;
  loadPage?: (page: number) => Promise<Blob>;
  loadCasePreview?: (caseId: string, options: DocumentLinkOptions) => Promise<DocumentCasePreview>;
  payers?: ReimbursementPayer[];
  selectedItemIndex?: number | null;
  onSelectItem?: (index: number | null) => void;
  linkedCase?: ReimbursementCaseCandidate | null;
}

const DOCUMENT_TYPE_OPTIONS: { value: ExtractedDocumentData["document_type"]; label: string }[] = [
  { value: "invoice", label: "Rechnung" },
  { value: "benefit_statement", label: "Leistungsabrechnung" },
  { value: "prescription", label: "Rezept / Verordnung" },
  { value: "submission_receipt", label: "Einreichungsnachweis" },
  { value: "request_for_documents", label: "Unterlagenanforderung" },
  { value: "other", label: "Sonstiges" },
  { value: "unknown", label: "Unbekannt" },
];

const SOURCE_LABELS: Record<string, string> = {
  local_ai: "KI",
  text_rule: "Regel",
};

const FIELD_LABELS: Record<string, string> = {
  document_type: "Dokumenttyp",
  person_name: "Behandelte Person",
  provider_name: "Anbieter",
  invoice_number: "Rechnungsnummer",
  invoice_date: "Rechnungsdatum",
  due_date: "Fälligkeit",
  billed_minor: "Rechnungsbetrag",
  expected_minor: "Erwartete Erstattung",
  reimbursed_minor: "Erstattungsangabe im Dokument",
  approved_minor: "Bewilligt",
  deductible_minor: "Selbstbehalt",
  noncovered_minor: "Nicht gedeckte Kosten",
  statement_number: "Abrechnungsnummer",
  statement_date: "Bescheiddatum",
  submission_date: "Einreichungsdatum",
  external_reference: "Vorgangsreferenz",
  payer_name: "Kostenträger",
  payer_type: "Art des Kostenträgers",
  currency: "Währung",
  items: "Einzelrechnungen",
};

function comparisonValue(key: string, value: unknown): string {
  if (value == null || value === "") return "Unbekannt";
  if (key.endsWith("_minor") && typeof value === "number") return formatMinorUnitsAsEuro(value);
  if (key === "document_type")
    return DOCUMENT_TYPE_OPTIONS.find((item) => item.value === value)?.label ?? String(value);
  if (Array.isArray(value))
    return (
      value
        .map((item) => {
          const row = item as ExtractedStatementItem;
          return [
            row.invoice_number ?? "Ohne Rechnungsnummer",
            row.person_name,
            row.provider_name,
            row.invoice_date,
            `Rechnung ${comparisonValue("billed_minor", row.billed_minor)}`,
            `bewilligt ${comparisonValue("approved_minor", row.approved_minor)}`,
            `Selbstbehalt ${comparisonValue("deductible_minor", row.deductible_minor)}`,
            `nicht gedeckt ${comparisonValue("noncovered_minor", row.noncovered_minor)}`,
          ]
            .filter(Boolean)
            .join(" · ");
        })
        .join("; ") || "Keine Einzelrechnungen"
    );
  if (key === "payer_type")
    return (
      (
        {
          pkv: "PKV",
          beihilfe: "Beihilfe",
          supplementary: "Zusatzversicherung",
          other: "Sonstige",
        } as Record<string, string>
      )[String(value)] ?? String(value)
    );
  return String(value);
}

// Befunde der Auswertung selbst. Gruende des KI-Kerns (model_timeout,
// model_input_too_large, model_budget_exhausted, …) stehen nicht hier: sie
// kommen mit Klartext und Aktion aus lib/aiReasons.ts (DocumentWarning).
const WARNING_LABELS: Record<string, string> = {
  model_output_invalid: "Die KI-Antwort war unbrauchbar – es gelten die Regelwerte.",
  invoice_core_fields_incomplete: "Für eine Rechnung fehlen noch Kernfelder.",
  ocr_unavailable:
    "Gescannte Seiten blieben ungelesen: die lokale Texterkennung war nicht verfügbar.",
  ocr_failed: "Gescannte Seiten blieben ungelesen: die lokale Texterkennung ist gescheitert.",
  extraction_failed: "Das Dokument konnte technisch nicht ausgewertet werden.",
  semantic_duplicate: "Inhaltlich identisch mit einem bereits erfassten Beleg.",
  statement_total_mismatch: "Die Einzelbeträge stimmen nicht mit der Gesamtauszahlung überein.",
  vision_requires_review:
    "Eine Seite wurde aus dem Bild gelesen. Beträge und Daten am Original prüfen.",
  vision_unavailable: "Für eine ungelesene Seite steht kein lokales Bildmodell bereit.",
  ocr_text_empty: "Auf einer Seite konnte die Texterkennung keinen Text lesen.",
  ocr_languages_missing: "Die deutschen oder englischen OCR-Sprachdaten fehlen.",
  previous_confirmation_preserved:
    "Deine bestätigten Korrekturen wurden erhalten. Die neue Erkennung steht zum Vergleich bereit.",
  field_conflict: "Textregel und KI widersprechen sich bei diesem Feld.",
  text_ocr_conflict:
    "Die Textebene und die Lesung des Seitenbildes widersprechen sich bei diesem Feld.",
  field_unverified: "Für dieses Feld fehlt ein eindeutiger Nachweis im gelesenen Text.",
  field_low_confidence: "Die Zeichen dieses Feldes wurden unsicher erkannt.",
  field_reread_failed: "Die Fundstelle dieses Feldes konnte nicht erneut gelesen werden.",
  statement_billed_total_mismatch:
    "Die Einzelrechnungen stimmen nicht mit dem Gesamtrechnungsbetrag überein.",
  document_person_mismatch:
    "Die behandelte Person passt nicht zur Person, unter der das Dokument abgelegt wurde.",
  invoice_date_unlabelled: "Ein Rechnungsdatum ist im Dokument nicht eindeutig bezeichnet.",
  birth_date_used: "Ein Geburtsdatum wurde als Belegdatum vorgeschlagen und verworfen.",
  document_amounts_inconsistent:
    "Die erkannten Beträge passen nicht zusammen. Bitte am Original prüfen.",
  pdf_text_failed: "Die Textebene einer Seite konnte nicht gelesen werden.",
  pdf_image_check_failed: "Bildbereiche einer Seite konnten nicht zuverlässig geprüft werden.",
  vision_failed: "Die Bildlesung konnte eine Seite nicht auswerten.",
  model_confidence_invalid:
    "Die Sicherheit der KI-Antwort konnte nicht verlässlich bewertet werden.",
};

function displayAmount(minor: number | null): string {
  return minor == null ? "" : (minor / 100).toFixed(2).replace(".", ",");
}

/**
 * Eine Warnung der Auswertung. Der Worker speichert nur Schluessel; ein Grund
 * des KI-Kerns heisst: das Modell lieferte nichts, es gelten die Textregeln.
 */
function DocumentWarning({ value }: { value: unknown }) {
  const key = String(value);
  const warningKey = key.split(":")[0] ?? key;
  if (isAiReasonCode(key)) {
    return (
      <AiReasonNotice
        as="li"
        code={key}
        prefix="Ohne KI-Werte – hier stehen nur die Werte aus den Textregeln:"
      />
    );
  }
  const label =
    WARNING_LABELS[key] ??
    (WARNING_LABELS[warningKey]
      ? `${WARNING_LABELS[warningKey]} ${
          /^\d+$/.test(key.split(":")[1] ?? "")
            ? `(Seite ${key.split(":")[1]})`
            : (FIELD_LABELS[key.split(":").at(-1) ?? ""] ?? "")
        }`
      : null) ??
    (/^[a-z0-9_]+$/.test(key) ? `Unbekannter Befund der Auswertung (Kennung: ${key}).` : key);
  return <li>{label}</li>;
}

/** Herkunft eines Feldes, wie sie der Worker in `field_evidence` hinterlegt hat. */
function FieldSource({ evidence, field }: { evidence: Record<string, unknown>; field: string }) {
  const locate = useContext(LocateContext);
  const entry = evidence[field];
  const source =
    entry && typeof entry === "object" && "source" in entry
      ? String((entry as { source: unknown }).source)
      : null;
  if (!source) return null;
  const locations =
    entry && typeof entry === "object" && "locations" in entry
      ? (entry as { locations?: FieldLocation[] }).locations
      : undefined;
  const conflict = entry as
    | {
        conflict?: boolean;
        rule_value?: unknown;
        model_value?: unknown;
        rule_locations?: FieldLocation[];
      }
    | undefined;
  const showValue = (value: unknown) =>
    typeof value === "number" && field.endsWith("_minor")
      ? formatMinorUnitsAsEuro(value)
      : String(value ?? "Unbekannt");
  return (
    <span className={`reimbursement-field-source reimbursement-field-source--${source}`}>
      {SOURCE_LABELS[source] ?? source}
      {locations?.[0] && (
        <button
          type="button"
          className="reimbursement-location-link"
          title="Fundstelle der erkannten Angabe im Original anzeigen"
          onClick={() => locate(field)}
        >
          Seite {locations[0].page_number}
        </button>
      )}
      {conflict?.conflict && (
        <span className="reimbursement-field-conflict">
          Regel: {showValue(conflict.rule_value)} · KI: {showValue(conflict.model_value)}
          {conflict.rule_locations?.[0] && (
            <button
              type="button"
              className="reimbursement-location-link"
              onClick={() => locate(`rule:${field}`)}
            >
              Regel am Original prüfen
            </button>
          )}
        </span>
      )}
    </span>
  );
}

function ExtractionForm({
  extraction,
  pending,
  errorText,
  onConfirm,
  linkedIndices = [],
}: {
  extraction: DocumentExtraction;
  pending: boolean;
  errorText: string | null;
  onConfirm: (data: ExtractedDocumentData) => void;
  linkedIndices?: number[];
}) {
  const source =
    extraction.confirmed_data ?? extraction.previous_confirmed_data ?? extraction.structured_data;
  const evidence = extraction.field_evidence ?? {};
  const [documentType, setDocumentType] = useState(source.document_type);
  const [provider, setProvider] = useState(source.provider_name ?? "");
  const [invoiceNumber, setInvoiceNumber] = useState(source.invoice_number ?? "");
  const [invoiceDate, setInvoiceDate] = useState(source.invoice_date ?? "");
  const [dueDate, setDueDate] = useState(source.due_date ?? "");
  const [billed, setBilled] = useState(displayAmount(source.billed_minor));
  const [expected, setExpected] = useState(displayAmount(source.expected_minor));
  const [reimbursed, setReimbursed] = useState(displayAmount(source.reimbursed_minor));
  const [validationError, setValidationError] = useState<string | null>(null);
  const locate = useContext(LocateContext);
  const [person, setPerson] = useState(source.person_name ?? "");
  const [payerName, setPayerName] = useState(source.payer_name ?? "");
  const [payerType, setPayerType] = useState(source.payer_type ?? "pkv");
  const [statementNumber, setStatementNumber] = useState(source.statement_number ?? "");
  const [statementDate, setStatementDate] = useState(source.statement_date ?? "");
  const [submissionDate, setSubmissionDate] = useState(
    source.submission_date ??
      (source.document_type === "submission_receipt" ? (source.invoice_date ?? "") : ""),
  );
  const [externalReference, setExternalReference] = useState(source.external_reference ?? "");
  const [approved, setApproved] = useState(
    displayAmount(source.approved_minor ?? source.expected_minor ?? source.reimbursed_minor),
  );
  const [deductible, setDeductible] = useState(displayAmount(source.deductible_minor ?? null));
  const [noncovered, setNoncovered] = useState(displayAmount(source.noncovered_minor ?? null));
  const [items, setItems] = useState<ExtractedStatementItem[]>(
    (source.items ?? []).map((item, index) => ({
      ...item,
      source_index: item.source_index === undefined ? index : item.source_index,
    })),
  );
  const [itemsValid, setItemsValid] = useState(true);
  const [itemsRevision, setItemsRevision] = useState(0);
  const statement = documentType === "benefit_statement";

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const billedMinor = billed.trim() ? parseAmountToMinorUnits(billed) : null;
    const expectedMinor = expected.trim() ? parseAmountToMinorUnits(expected) : null;
    const reimbursedMinor = reimbursed.trim() ? parseAmountToMinorUnits(reimbursed) : null;
    const approvedMinor = approved.trim() ? parseAmountToMinorUnits(approved) : null;
    const deductibleMinor = deductible.trim() ? parseAmountToMinorUnits(deductible) : null;
    const noncoveredMinor = noncovered.trim() ? parseAmountToMinorUnits(noncovered) : null;
    if (statement && items.length) {
      if (
        items.every((item) => item.billed_minor != null) &&
        billedMinor != null &&
        items.reduce((sum, item) => sum + (item.billed_minor ?? 0), 0) !== billedMinor
      ) {
        setValidationError(
          "Die Einzelrechnungen stimmen nicht mit dem Gesamtrechnungsbetrag überein.",
        );
        return;
      }
      if (
        items.every((item) => item.approved_minor != null) &&
        approvedMinor != null &&
        items.reduce((sum, item) => sum + (item.approved_minor ?? 0), 0) !== approvedMinor
      ) {
        setValidationError(
          "Die bewilligten Einzelbeträge stimmen nicht mit der Gesamtauszahlung überein.",
        );
        return;
      }
    }
    if (
      (billed.trim() && billedMinor == null) ||
      (expected.trim() && expectedMinor == null) ||
      (reimbursed.trim() && reimbursedMinor == null) ||
      (statement && approved.trim() && (approvedMinor == null || approvedMinor < 0)) ||
      (statement && deductible.trim() && (deductibleMinor == null || deductibleMinor < 0)) ||
      (statement && noncovered.trim() && (noncoveredMinor == null || noncoveredMinor < 0)) ||
      !itemsValid
    ) {
      setValidationError("Bitte prüfe die Geldbeträge.");
      return;
    }
    if (documentType === "invoice" && (!provider.trim() || !invoiceDate || billedMinor == null)) {
      setValidationError(
        "Für eine Rechnung sind Anbieter, Rechnungsdatum und Betrag erforderlich.",
      );
      return;
    }
    setValidationError(null);
    onConfirm({
      ...source,
      document_type: documentType,
      provider_name: provider.trim() || null,
      person_name: person.trim() || null,
      invoice_number: invoiceNumber.trim() || null,
      invoice_date: documentType === "submission_receipt" ? null : invoiceDate || null,
      due_date: dueDate || null,
      billed_minor: billedMinor,
      expected_minor: statement ? approvedMinor : expectedMinor,
      reimbursed_minor: statement ? approvedMinor : reimbursedMinor,
      currency: source.currency,
      statement_number: statementNumber.trim() || null,
      statement_date: statementDate || null,
      submission_date: submissionDate || null,
      external_reference: externalReference.trim() || null,
      approved_minor: statement ? approvedMinor : source.approved_minor,
      deductible_minor: statement ? deductibleMinor : source.deductible_minor,
      noncovered_minor: statement ? noncoveredMinor : source.noncovered_minor,
      payer_name: payerName.trim() || null,
      payer_type: statement ? payerType : source.payer_type,
      items: statement ? items : [],
    });
  };

  const warnings = extraction.validation_warnings ?? [];
  const confirmed = extraction.confirmed_at != null;

  return (
    <form className="reimbursement-inspector-form" onSubmit={submit}>
      <div className="reimbursement-inspector-readout">
        <span>
          Konfidenz{" "}
          <strong>
            {extraction.confidence == null
              ? "nicht angegeben"
              : Math.round(extraction.confidence * 100) + " %"}
          </strong>
        </span>
        <span>
          Modell <strong>{extraction.model_name ?? "nur Textregeln"}</strong>
        </span>
        {confirmed && <span className="reimbursement-inspector-confirmed">Bereits bestätigt</span>}
      </div>

      {warnings.length > 0 && (
        <ul className="reimbursement-inspector-warnings">
          {warnings.map((warning, index) => (
            <DocumentWarning key={index} value={warning} />
          ))}
        </ul>
      )}

      {(extraction.confirmed_data || extraction.previous_confirmed_data) &&
        JSON.stringify(source) !== JSON.stringify(extraction.structured_data) && (
          <details className="reimbursement-extraction-diff" open>
            <summary>Bestätigte Werte mit der Erkennung vergleichen</summary>
            <p>
              Die Felder zeigen deine bestätigten Werte. Eine neue Erkennung überschreibt sie erst
              nach deiner Entscheidung.
            </p>
            <table className="reimbursement-comparison-table">
              <thead>
                <tr>
                  <th>Feld</th>
                  <th>Bestätigt</th>
                  <th>Erkannt</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(source)
                  .filter(
                    (key) =>
                      JSON.stringify(source[key as keyof ExtractedDocumentData]) !==
                      JSON.stringify(
                        extraction.structured_data[key as keyof ExtractedDocumentData],
                      ),
                  )
                  .map((key) => (
                    <tr key={key}>
                      <th>{FIELD_LABELS[key] ?? key}</th>
                      <td>{comparisonValue(key, source[key as keyof ExtractedDocumentData])}</td>
                      <td>
                        {comparisonValue(
                          key,
                          extraction.structured_data[key as keyof ExtractedDocumentData],
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                const next = extraction.structured_data;
                setDocumentType(next.document_type);
                setProvider(next.provider_name ?? "");
                setPerson(next.person_name ?? "");
                setInvoiceNumber(next.invoice_number ?? "");
                setInvoiceDate(next.invoice_date ?? "");
                setDueDate(next.due_date ?? "");
                setBilled(displayAmount(next.billed_minor));
                setExpected(displayAmount(next.expected_minor));
                setReimbursed(displayAmount(next.reimbursed_minor));
                setApproved(
                  displayAmount(
                    next.approved_minor ?? next.expected_minor ?? next.reimbursed_minor,
                  ),
                );
                setPayerName(next.payer_name ?? "");
                setPayerType(next.payer_type ?? "pkv");
                setDeductible(displayAmount(next.deductible_minor ?? null));
                setNoncovered(displayAmount(next.noncovered_minor ?? null));
                setStatementNumber(next.statement_number ?? "");
                setStatementDate(next.statement_date ?? "");
                setSubmissionDate(next.submission_date ?? "");
                setExternalReference(next.external_reference ?? "");
                setItems(next.items ?? []);
                setItemsValid(true);
                setItemsRevision(itemsRevision + 1);
              }}
            >
              Neue Erkennung in die Felder laden
            </button>
          </details>
        )}

      <label className="reimbursement-inspector-field">
        <span>
          Dokumenttyp
          <FieldSource evidence={evidence} field="document_type" />
        </span>
        <select
          className="form-input"
          value={documentType}
          onChange={(event) =>
            setDocumentType(event.target.value as ExtractedDocumentData["document_type"])
          }
        >
          {DOCUMENT_TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label className="reimbursement-inspector-field">
        <span>
          Behandelte Person <FieldSource evidence={evidence} field="person_name" />
        </span>
        <input
          className="form-input"
          value={person}
          onChange={(event) => setPerson(event.target.value)}
          maxLength={120}
        />
      </label>
      {statement && (
        <fieldset className="reimbursement-statement-header">
          <legend>PKV / Beihilfe: Entscheidung laut Bescheid</legend>
          <label>
            Kostenträger
            <input
              className="form-input"
              value={payerName}
              onChange={(event) => setPayerName(event.target.value)}
              maxLength={160}
            />
          </label>
          <label>
            Art des Kostenträgers
            <select
              className="form-input"
              value={payerType}
              onChange={(event) => setPayerType(event.target.value as typeof payerType)}
            >
              <option value="pkv">PKV</option>
              <option value="beihilfe">Beihilfe</option>
              <option value="supplementary">Zusatzversicherung</option>
              <option value="other">Sonstige</option>
            </select>
          </label>
          <label>
            Abrechnungsnummer
            <input
              className="form-input"
              value={statementNumber}
              onChange={(event) => setStatementNumber(event.target.value)}
              maxLength={160}
            />
          </label>
          <label>
            Bescheiddatum
            <input
              className="form-input"
              type="date"
              value={statementDate}
              onChange={(event) => setStatementDate(event.target.value)}
            />
          </label>
          <label>
            Bewilligte Auszahlung
            <input
              className="form-input"
              inputMode="decimal"
              value={approved}
              onChange={(event) => setApproved(event.target.value)}
            />
            <FieldSource evidence={evidence} field="approved_minor" />
          </label>
          <label>
            Selbstbehalt
            <input
              className="form-input"
              inputMode="decimal"
              value={deductible}
              onChange={(event) => setDeductible(event.target.value)}
            />
            <FieldSource evidence={evidence} field="deductible_minor" />
          </label>
          <label>
            Nicht gedeckte Kosten
            <input
              className="form-input"
              inputMode="decimal"
              value={noncovered}
              onChange={(event) => setNoncovered(event.target.value)}
            />
            <FieldSource evidence={evidence} field="noncovered_minor" />
          </label>
          <StatementItemsEditor
            key={itemsRevision}
            items={items}
            onChange={setItems}
            onValidity={setItemsValid}
            onLocate={locate}
            linkedIndices={linkedIndices}
          />
        </fieldset>
      )}
      {documentType === "submission_receipt" && (
        <fieldset className="reimbursement-statement-header">
          <legend>Einreichungsnachweis</legend>
          <label>
            Einreichungsreferenz
            <input
              className="form-input"
              value={externalReference}
              onChange={(event) => setExternalReference(event.target.value)}
              maxLength={160}
            />
            <FieldSource evidence={evidence} field="external_reference" />
          </label>
          <label>
            Eingangsdatum
            <input
              type="date"
              className="form-input"
              value={submissionDate}
              onChange={(event) => setSubmissionDate(event.target.value)}
            />
            <FieldSource evidence={evidence} field="submission_date" />
          </label>
        </fieldset>
      )}
      {(!statement || !items.length) && (
        <>
          <label className="reimbursement-inspector-field reimbursement-inspector-field--wide">
            <span>
              Anbieter / Praxis
              <FieldSource evidence={evidence} field="provider_name" />
            </span>
            <input
              className="form-input"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
              maxLength={240}
            />
          </label>
          <label className="reimbursement-inspector-field">
            <span>
              {statement ? "Rechnungsnummer, falls eindeutig" : "Rechnungsnummer"}
              <FieldSource evidence={evidence} field="invoice_number" />
            </span>
            <input
              className="form-input"
              value={invoiceNumber}
              onChange={(event) => setInvoiceNumber(event.target.value)}
              maxLength={160}
            />
          </label>
          <label className="reimbursement-inspector-field">
            <span>
              {statement ? "Rechnungsdatum, falls eindeutig" : "Rechnungsdatum"}
              <FieldSource evidence={evidence} field="invoice_date" />
            </span>
            <input
              className="form-input"
              type="date"
              value={invoiceDate}
              onChange={(event) => setInvoiceDate(event.target.value)}
            />
          </label>
          <label className="reimbursement-inspector-field">
            <span>
              Fällig am
              <FieldSource evidence={evidence} field="due_date" />
            </span>
            <input
              className="form-input"
              type="date"
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
            />
          </label>
        </>
      )}
      <label className="reimbursement-inspector-field">
        <span>
          Rechnungsbetrag
          <FieldSource evidence={evidence} field="billed_minor" />
        </span>
        <input
          className="form-input"
          inputMode="decimal"
          value={billed}
          onChange={(event) => setBilled(event.target.value)}
          placeholder="0,00"
        />
      </label>
      {!statement && (
        <label className="reimbursement-inspector-field">
          <span>
            Erstattung erwartet
            <FieldSource evidence={evidence} field="expected_minor" />
          </span>
          <input
            className="form-input"
            inputMode="decimal"
            value={expected}
            onChange={(event) => setExpected(event.target.value)}
            placeholder="0,00"
          />
        </label>
      )}
      {!statement && (
        <label className="reimbursement-inspector-field">
          <span>
            Auszahlungsbetrag im Dokument
            <FieldSource evidence={evidence} field="reimbursed_minor" />
          </span>
          <input
            className="form-input"
            inputMode="decimal"
            value={reimbursed}
            onChange={(event) => setReimbursed(event.target.value)}
            placeholder="0,00"
          />
        </label>
      )}

      {Boolean(validationError || errorText) && (
        <p className="reimbursement-inspector-error" role="alert">
          {validationError ?? errorText}
        </p>
      )}
      <div className="reimbursement-inspector-submit">
        <button type="submit" className="btn-primary" disabled={pending}>
          {pending ? "Wird übernommen …" : confirmed ? "Erneut bestätigen" : "Felder bestätigen"}
        </button>
      </div>
    </form>
  );
}

/**
 * Rechte Arbeitsspalte der Dokumentablage: erst was die Auswertung gelesen
 * hat, darunter das Original zum Gegenlesen.
 *
 * Bewusst kein Dialog: Liste und Prüfung bleiben gleichzeitig sichtbar, und
 * die Auswahl in der Liste ist die einzige Navigation.
 */
export function DocumentInspector({
  document,
  personName,
  extraction,
  extractionLoading,
  extractionError,
  fileUrl,
  fileLoading,
  fileError,
  confirmPending,
  confirmError,
  reprocessPending,
  deletePending,
  onConfirm,
  onReprocess,
  onDelete,
  onClose,
  caseCandidates,
  caseCandidatesLoading,
  linkedCaseLabel,
  linkPending,
  linkError,
  onLinkCase,
  onUnlinkCase,
  loadPage,
  loadCasePreview,
  payers = [],
  selectedItemIndex = null,
  onSelectItem,
  linkedCase,
}: DocumentInspectorProps) {
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [location, setLocation] = useState<FieldLocation | null>(null);
  const [locationMessage, setLocationMessage] = useState<string | null>(null);
  const data =
    extraction?.confirmed_data ??
    extraction?.previous_confirmed_data ??
    extraction?.structured_data;
  const locate = (field: string) => {
    const rule = field.startsWith("rule:");
    const entry = extraction?.field_evidence[rule ? field.slice(5) : field] as
      | {
          locations?: FieldLocation[];
          rule_locations?: FieldLocation[];
        }
      | undefined;
    const found = rule ? entry?.rule_locations?.[0] : entry?.locations?.[0];
    if (found) {
      setLocation(found);
      setLocationMessage(null);
    } else
      setLocationMessage(
        "Für dieses Feld ist keine eindeutige Fundstelle vorhanden. Bitte im Original prüfen.",
      );
  };
  const pages = extraction?.field_evidence._pages as
    { page_number: number; rotation: number }[] | undefined;
  const rotation = pages?.find((page) => page.page_number === location?.page_number)?.rotation ?? 0;

  return (
    <LocateContext.Provider value={locate}>
      <aside
        className="reimbursement-inspector"
        aria-label={`Dokument ${document.original_filename}`}
      >
        <header className="reimbursement-inspector-header">
          <div className="reimbursement-inspector-title">
            <span className="reimbursement-inspector-eyebrow">{personName}</span>
            <h3 title={document.original_filename}>{document.original_filename}</h3>
          </div>
          <button
            type="button"
            className="reimbursement-inspector-close"
            onClick={onClose}
            aria-label="Dokumentansicht schließen"
          >
            ×
          </button>
        </header>

        <div className="reimbursement-inspector-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onReprocess}
            disabled={reprocessPending}
          >
            {reprocessPending ? "Wird eingereiht …" : "Neu auswerten"}
          </button>
          {fileUrl && (
            <a className="btn-secondary" href={fileUrl} target="_blank" rel="noreferrer">
              In neuem Tab
            </a>
          )}
          {deleteArmed ? (
            <span
              className="reimbursement-inspector-danger"
              role="group"
              aria-label="Löschen bestätigen"
            >
              <strong>Wirklich löschen?</strong>
              <button
                type="button"
                className="btn-danger"
                onClick={onDelete}
                disabled={deletePending}
              >
                {deletePending ? "Wird gelöscht …" : "Ja, endgültig"}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setDeleteArmed(false)}>
                Abbrechen
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="reimbursement-inspector-delete"
              onClick={() => setDeleteArmed(true)}
            >
              Löschen
            </button>
          )}
        </div>

        <section className="reimbursement-inspector-fields" aria-label="Gelesene Abrechnungsfelder">
          {extractionLoading ? (
            <p className="reimbursement-inspector-note" role="status">
              Gelesene Felder werden geladen …
            </p>
          ) : extractionError ? (
            <p className="reimbursement-inspector-note" role="alert">
              {extractionError}
            </p>
          ) : extraction ? (
            <ExtractionForm
              key={extraction.id}
              extraction={extraction}
              pending={confirmPending}
              errorText={confirmError}
              onConfirm={onConfirm}
              linkedIndices={(document.linked_items ?? []).flatMap((item) =>
                item.item_index == null ? [] : [item.item_index],
              )}
            />
          ) : (
            <p className="reimbursement-inspector-note">
              Für dieses Dokument liegt noch keine Auswertung vor.
            </p>
          )}
        </section>

        <section className="reimbursement-inspector-cases" aria-label="Zugehöriger Vorgang">
          <h4>Gehört zu Vorgang</h4>
          {data?.items?.length && onSelectItem ? (
            <label>
              Rechnung im Bescheid
              <select
                className="form-input"
                value={selectedItemIndex ?? 0}
                onChange={(event) => onSelectItem(Number(event.target.value))}
              >
                {data.items.map((item, index) => (
                  <option key={index} value={index}>
                    {index + 1}: {item.invoice_number || "Nummer unbekannt"} ·{" "}
                    {item.person_name || personName}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {data && (
            <DocumentCaseComparison
              key={`${extraction?.id}:${extraction?.confirmed_at}:${selectedItemIndex}`}
              data={data}
              confirmed={Boolean(extraction?.confirmed_at)}
              itemIndex={selectedItemIndex}
              payers={payers}
              loadPreview={loadCasePreview}
              linkedCase={linkedCase}
              onUnlink={onUnlinkCase}
              candidates={caseCandidates}
              loading={caseCandidatesLoading}
              linkedCaseLabel={linkedCaseLabel}
              pending={linkPending}
              errorText={linkError}
              onLink={onLinkCase}
            />
          )}
        </section>

        <section className="reimbursement-inspector-preview" aria-label="Originaldokument">
          {location && (
            <button type="button" className="btn-secondary" onClick={() => setLocation(null)}>
              Gesamtes PDF anzeigen
            </button>
          )}
          {locationMessage && <p role="status">{locationMessage}</p>}
          {location && loadPage && document.mime_type === "application/pdf" && (
            <EvidencePageViewer
              location={location}
              pageCount={document.page_count ?? 1}
              rotation={rotation}
              loadPage={loadPage}
              onPage={(page) => setLocation({ page_number: page, box: [0, 0, 0, 0] })}
            />
          )}
          {fileLoading ? (
            <p className="reimbursement-inspector-note" role="status">
              Original wird aus der privaten Ablage geladen …
            </p>
          ) : fileError ? (
            <p className="reimbursement-inspector-note" role="alert">
              {fileError}
            </p>
          ) : fileUrl ? (
            document.mime_type === "application/pdf" ? (
              <iframe
                hidden={Boolean(location && loadPage)}
                className="reimbursement-inspector-frame"
                src={location ? `${fileUrl}#page=${location.page_number}` : fileUrl}
                title={`Original ${document.original_filename}`}
              />
            ) : (
              <img
                className="reimbursement-inspector-image"
                src={location ? `${fileUrl}#page=${location.page_number}` : fileUrl}
                alt={`Original ${document.original_filename}`}
              />
            )
          ) : (
            <p className="reimbursement-inspector-note">Kein Original zur Anzeige verfügbar.</p>
          )}
        </section>
      </aside>
    </LocateContext.Provider>
  );
}
