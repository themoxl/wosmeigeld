import { useEffect, useState } from "react";
import type {
  DocumentCasePreview,
  DocumentLinkOptions,
  ExtractedDocumentData,
  ReimbursementCaseCandidate,
  ReimbursementPayer,
} from "../../lib/reimbursementsApi";
import { formatMinorUnitsAsEuro } from "../../lib/money";

export function DocumentCaseComparison({
  candidates,
  loading,
  linkedCaseLabel,
  pending,
  errorText,
  onLink,
  data,
  confirmed,
  itemIndex,
  payers,
  loadPreview,
  onUnlink,
  linkedCase,
}: {
  candidates: ReimbursementCaseCandidate[];
  loading: boolean;
  linkedCaseLabel: string | null;
  pending: boolean;
  errorText: string | null;
  onLink: (caseId: string, options?: DocumentLinkOptions) => void;
  data: ExtractedDocumentData;
  confirmed: boolean;
  itemIndex: number | null;
  payers: ReimbursementPayer[];
  loadPreview?: (caseId: string, options: DocumentLinkOptions) => Promise<DocumentCasePreview>;
  onUnlink?: () => void;
  linkedCase?: ReimbursementCaseCandidate | null;
}) {
  const [reviewLinked, setReviewLinked] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [payerId, setPayerId] = useState<string>("");
  const [applyDecision, setApplyDecision] = useState(true);
  const [preview, setPreview] = useState<DocumentCasePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const available = linkedCase
    ? [linkedCase, ...candidates.filter((candidate) => candidate.case_id !== linkedCase.case_id)]
    : candidates;
  const selected = available.find((candidate) => candidate.case_id === selectedId);
  const statement = data.document_type === "benefit_statement";
  const item = itemIndex == null ? data : data.items?.[itemIndex];
  const approved =
    itemIndex == null
      ? (data.approved_minor ?? data.expected_minor ?? data.reimbursed_minor)
      : item?.approved_minor;
  useEffect(() => {
    let current = true;
    if (!selectedId || !loadPreview) return;
    loadPreview(selectedId, {
      item_index: itemIndex,
      payer_id: payerId || null,
      apply_decision: statement && applyDecision,
    })
      .then((result) => {
        if (current) {
          setPreview(result);
          setPreviewError(null);
        }
      })
      .catch((error: unknown) => {
        if (current) {
          setPreview(null);
          setPreviewError(
            error instanceof Error ? error.message : "Der Vergleich konnte nicht geladen werden.",
          );
        }
      });
    return () => {
      current = false;
    };
  }, [selectedId, loadPreview, itemIndex, payerId, applyDecision, statement]);
  if (linkedCaseLabel && !reviewLinked)
    return (
      <div className="reimbursement-inspector-linked">
        Zugeordnet zu <strong>{linkedCaseLabel}</strong>
        {onUnlink && (
          <button type="button" className="btn-secondary" disabled={pending} onClick={onUnlink}>
            Zuordnung lösen
          </button>
        )}
        {linkedCase && statement && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setReviewLinked(true);
              setSelectedId(linkedCase.case_id);
            }}
          >
            Bescheid erneut vergleichen
          </button>
        )}
      </div>
    );
  if (loading) return <p role="status">Passende Vorgänge werden gesucht …</p>;
  return (
    <>
      {statement && itemIndex != null && <p>Zuordnung für Rechnung {itemIndex + 1}</p>}
      {available.length === 0 ? (
        <p>
          Kein passender Vorgang gefunden. Rechnungsnummer, Person und Betrag prüfen.
          {data.document_type === "invoice" &&
            " Nach der Feldbestätigung kann ein neuer Vorgang entstehen."}
        </p>
      ) : (
        <ul className="reimbursement-candidate-list">
          {available.map((candidate) => (
            <li key={candidate.case_id}>
              <button
                type="button"
                className="reimbursement-candidate"
                disabled={pending}
                aria-pressed={candidate.case_id === selectedId}
                onClick={() => {
                  setSelectedId(candidate.case_id);
                  setPreview(null);
                  setPreviewError(null);
                }}
              >
                <span className="reimbursement-candidate-head">
                  <strong>{candidate.provider_name}</strong>
                  <span>
                    {candidate.case_id === linkedCase?.case_id
                      ? "Bestehende Zuordnung"
                      : candidate.manual
                        ? "Von Hand prüfen"
                        : `${Math.round(candidate.score * 100)} % Übereinstimmung`}
                  </span>
                </span>
                <span>
                  {candidate.person_name} ·{" "}
                  {formatMinorUnitsAsEuro(candidate.billed_minor, candidate.currency)} ·{" "}
                  {candidate.invoice_date}
                </span>
                <span>{candidate.evidence.join(" · ")}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {selected && (
        <div className="reimbursement-case-comparison">
          <h5>Dokument mit Vorgang vergleichen</h5>
          {statement && (
            <>
              <label>
                Kostenträger
                <select
                  className="form-input"
                  value={payerId}
                  onChange={(event) => {
                    setPayerId(event.target.value);
                    setPreview(null);
                  }}
                >
                  <option value="">
                    {data.payer_name
                      ? `${data.payer_name} aus dem Dokument`
                      : "Kostenträger auswählen"}
                  </option>
                  {payers.map((payer) => (
                    <option key={payer.id} value={payer.id}>
                      {payer.name} (
                      {payer.payer_type === "beihilfe" ? "Beihilfe" : "PKV / Versicherung"})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={applyDecision}
                  onChange={(event) => {
                    setApplyDecision(event.target.checked);
                    setPreview(null);
                  }}
                />{" "}
                Bewilligten Betrag übernehmen
              </label>
            </>
          )}
          <table className="reimbursement-comparison-table">
            <thead>
              <tr>
                <th>Feld</th>
                <th>Vorgang</th>
                <th>Dokument / danach</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>Rechnungsnummer</th>
                <td>{selected.invoice_number || "Unbekannt"}</td>
                <td>{item?.invoice_number || "Unbekannt"}</td>
              </tr>
              <tr>
                <th>Rechnungsbetrag</th>
                <td>{formatMinorUnitsAsEuro(selected.billed_minor, selected.currency)}</td>
                <td>
                  {item?.billed_minor == null
                    ? "Unbekannt"
                    : formatMinorUnitsAsEuro(item.billed_minor, selected.currency)}
                </td>
              </tr>
              {statement && (
                <tr>
                  <th>Bewilligt im Bescheid</th>
                  <td>—</td>
                  <td>
                    {approved == null
                      ? "Unbekannt"
                      : formatMinorUnitsAsEuro(approved, selected.currency)}
                  </td>
                </tr>
              )}
              {preview && (
                <>
                  <tr>
                    <th>Erstattung insgesamt erwartet</th>
                    <td>
                      {formatMinorUnitsAsEuro(preview.expected_before_minor, preview.currency)}
                    </td>
                    <td>
                      {formatMinorUnitsAsEuro(preview.expected_after_minor, preview.currency)}
                    </td>
                  </tr>
                  <tr>
                    <th>Bankeingang</th>
                    <td>{formatMinorUnitsAsEuro(preview.received_minor, preview.currency)}</td>
                    <td>{formatMinorUnitsAsEuro(preview.received_minor, preview.currency)}</td>
                  </tr>
                  <tr>
                    <th>Danach noch offen</th>
                    <td>—</td>
                    <td>
                      {formatMinorUnitsAsEuro(preview.outstanding_after_minor, preview.currency)}
                    </td>
                  </tr>
                </>
              )}
            </tbody>
          </table>
          {preview && preview.received_minor > preview.expected_after_minor && (
            <p role="status">
              Der erfasste Eingang übersteigt die neue Bewilligung um{" "}
              {formatMinorUnitsAsEuro(
                preview.received_minor - preview.expected_after_minor,
                preview.currency,
              )}
              . Der Vorgang bleibt zur Prüfung offen.
            </p>
          )}
          {statement && (
            <p>
              Der Bescheid ändert die bewilligte Erstattung. Ein Zahlungseingang wird beim
              Bankabgleich erfasst.
            </p>
          )}
          {!confirmed && statement && applyDecision && (
            <p>Bitte zuerst die gelesenen Felder bestätigen.</p>
          )}
          {(errorText || previewError) && <p role="alert">{errorText ?? previewError}</p>}
          <button
            type="button"
            className="btn-primary"
            disabled={
              pending ||
              (Boolean(loadPreview) && !preview) ||
              (statement &&
                applyDecision &&
                (!confirmed || approved == null || (!payerId && !data.payer_name)))
            }
            onClick={() =>
              onLink(selected.case_id, {
                item_index: itemIndex,
                payer_id: payerId || null,
                expected_case_version: preview?.case_version ?? selected.version,
                apply_decision: statement && applyDecision,
              })
            }
          >
            {pending ? "Wird übernommen …" : "Zuordnung übernehmen"}
          </button>
        </div>
      )}
    </>
  );
}
