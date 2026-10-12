import { useState } from "react";
import type {
  ReimbursementAllocation,
  ReimbursementTransactionCandidate,
} from "../../lib/reimbursementsApi";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";

/** Nur das, was der Bankabgleich vom Vorgang wirklich braucht. Bewusst nicht
 *  der volle Vorgangstyp aus der Seite - sonst zoege die Komponente die Seite
 *  hinter sich her, die sie selbst einbindet. */
export interface BankReconciliationCase {
  id: string;
  person_name: string;
  provider_name: string;
  invoice_date: string;
  expected_minor: number;
  reimbursed_minor: number;
  outstanding_minor: number;
  unverified_reimbursed_minor?: number;
}

export interface BankReconciliationPanelProps {
  cases: BankReconciliationCase[];
  selectedCaseId: string | null;
  selectedCase?: BankReconciliationCase | null;
  onSelectCase: (caseId: string | null) => void;
  candidates: ReimbursementTransactionCandidate[];
  allocations: ReimbursementAllocation[];
  loadingDetail?: boolean;
  busy?: boolean;
  error?: string | null;
  onAllocate: (caseId: string, transactionId: string, amountMinor: number) => void;
  onRemoveAllocation: (caseId: string, allocationId: string) => void;
}

/**
 * Die Stelle, an der aus einer erwarteten Erstattung eine eingegangene wird.
 *
 * Bis hierher endete der Weg an der Erwartung: der Vorgang wusste, dass Geld
 * kommen soll, aber nichts verband ihn mit der Zahlung, die tatsaechlich auf
 * dem Konto landete. Die API konnte den Abgleich vollstaendig - nur rief ihn
 * niemand auf.
 *
 * Zwei Regeln bestimmen den Aufbau: eine Buchung kann mehrere Vorgaenge decken
 * (Sammelueberweisung), und eine Zuordnung muss zuruecknehmbar sein. Beides
 * waere mit einem simplen "passt/passt nicht" nicht abbildbar.
 */
export function BankReconciliationPanel({
  cases,
  selectedCaseId,
  selectedCase = null,
  onSelectCase,
  candidates,
  allocations,
  loadingDetail = false,
  busy = false,
  error = null,
  onAllocate,
  onRemoveAllocation,
}: BankReconciliationPanelProps) {
  const selected = cases.find((item) => item.id === selectedCaseId) ?? selectedCase;

  if (cases.length === 0 && !selected) {
    return (
      <section className="card" aria-labelledby="bank-reconcile-heading">
        <h2 className="card-heading" id="bank-reconcile-heading">
          Bankabgleich
        </h2>
        <p className="food-fill-note">
          Kein Vorgang wartet auf eine Zahlung. Sobald eine Erstattung erwartet wird, erscheint sie
          hier zusammen mit den passenden Kontobewegungen.
        </p>
      </section>
    );
  }

  return (
    <section className="card" aria-labelledby="bank-reconcile-heading">
      <div className="page-header">
        <div>
          <h2 className="card-heading" id="bank-reconcile-heading">
            Bankabgleich
          </h2>
          <p className="card-body">
            Vorgänge, bei denen Geld erwartet wird oder nur ein Teil angekommen ist. Wähle einen
            aus, um die passenden Kontobewegungen zu sehen.
          </p>
        </div>
        <div className="explorer-total">
          <span className="explorer-total__value">
            {formatMinorUnitsAsEuro(cases.reduce((sum, item) => sum + item.outstanding_minor, 0))}
          </span>
          <span className="explorer-total__label">offen · {cases.length} Vorgänge</span>
        </div>
      </div>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <div className="txn-table-wrapper">
        <table className="txn-table">
          <thead>
            <tr>
              <th className="txn-th">Vorgang</th>
              <th className="txn-th">Person</th>
              <th className="txn-th">Rechnung</th>
              <th className="txn-th">Erwartet</th>
              <th className="txn-th">Erhalten</th>
              <th className="txn-th">Offen</th>
              <th className="txn-th">Aktion</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((item) => {
              const isSelected = item.id === selectedCaseId;
              return (
                <tr
                  key={item.id}
                  className={isSelected ? "txn-tr txn-tr--selected" : "txn-tr"}
                  aria-selected={isSelected}
                >
                  <td className="txn-td">{item.provider_name}</td>
                  <td className="txn-td">{item.person_name}</td>
                  <td className="txn-td">{item.invoice_date}</td>
                  <td className="txn-td">{formatMinorUnitsAsEuro(item.expected_minor)}</td>
                  <td className="txn-td">
                    {formatMinorUnitsAsEuro(item.reimbursed_minor)}
                    {Boolean(item.unverified_reimbursed_minor) && (
                      <p>
                        Altwert {formatMinorUnitsAsEuro(item.unverified_reimbursed_minor ?? 0)} ohne
                        Banknachweis – bitte abgleichen.
                      </p>
                    )}
                  </td>
                  <td className="txn-td">{formatMinorUnitsAsEuro(item.outstanding_minor)}</td>
                  <td className="txn-td">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => onSelectCase(isSelected ? null : item.id)}
                    >
                      {isSelected ? "Schließen" : "Zahlungen zuordnen"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selected && (
        <CaseReconciler
          key={selected.id}
          item={selected}
          candidates={candidates}
          allocations={allocations}
          loading={loadingDetail}
          busy={busy}
          onAllocate={onAllocate}
          onRemoveAllocation={onRemoveAllocation}
        />
      )}
    </section>
  );
}

interface CaseReconcilerProps {
  item: BankReconciliationCase;
  candidates: ReimbursementTransactionCandidate[];
  allocations: ReimbursementAllocation[];
  loading: boolean;
  busy: boolean;
  onAllocate: (caseId: string, transactionId: string, amountMinor: number) => void;
  onRemoveAllocation: (caseId: string, allocationId: string) => void;
}

function CaseReconciler({
  item,
  candidates,
  allocations,
  loading,
  busy,
  onAllocate,
  onRemoveAllocation,
}: CaseReconcilerProps) {
  return (
    <div className="reimbursement-foundation-panel" style={{ marginTop: "var(--space-4)" }}>
      <h3 className="card-heading">
        {item.provider_name} · noch offen {formatMinorUnitsAsEuro(item.outstanding_minor)}
      </h3>

      {allocations.length > 0 && (
        <>
          <p className="card-body">Bereits zugeordnet</p>
          <div className="txn-table-wrapper">
            <table className="txn-table">
              <thead>
                <tr>
                  <th className="txn-th">Datum</th>
                  <th className="txn-th">Zahlungseingang</th>
                  <th className="txn-th">Buchung gesamt</th>
                  <th className="txn-th">Diesem Vorgang</th>
                  <th className="txn-th">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {allocations.map((allocation) => (
                  <tr key={allocation.id} className="txn-tr">
                    <td className="txn-td">{allocation.booked_date}</td>
                    <td className="txn-td">{allocation.payee_label || "—"}</td>
                    <td className="txn-td">
                      {formatMinorUnitsAsEuro(Math.abs(allocation.transaction_amount_minor))}
                    </td>
                    <td className="txn-td">{formatMinorUnitsAsEuro(allocation.amount_minor)}</td>
                    <td className="txn-td">
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={busy}
                        onClick={() => onRemoveAllocation(item.id, allocation.id)}
                      >
                        Zuordnung entfernen
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <p className="card-body">Passende Kontobewegungen</p>

      {loading ? (
        <p className="food-fill-note">Kontobewegungen werden gesucht …</p>
      ) : candidates.length === 0 ? (
        <p className="food-fill-note">
          Keine offene Eingangsbuchung gefunden, die zu diesem Vorgang passen könnte. Kommt die
          Zahlung erst noch, erscheint sie nach dem nächsten Kontoimport hier.
        </p>
      ) : (
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Datum</th>
                <th className="txn-th">Zahlungseingang</th>
                <th className="txn-th">Noch frei</th>
                <th className="txn-th">Warum vorgeschlagen</th>
                <th className="txn-th">Zuordnen</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((candidate) => (
                <CandidateRow
                  key={candidate.transaction_id}
                  caseId={item.id}
                  outstandingMinor={item.outstanding_minor}
                  candidate={candidate}
                  busy={busy}
                  onAllocate={onAllocate}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface CandidateRowProps {
  caseId: string;
  outstandingMinor: number;
  candidate: ReimbursementTransactionCandidate;
  busy: boolean;
  onAllocate: (caseId: string, transactionId: string, amountMinor: number) => void;
}

function CandidateRow({
  caseId,
  outstandingMinor,
  candidate,
  busy,
  onAllocate,
}: CandidateRowProps) {
  // Vorbelegt mit dem, was beide Seiten hergeben: mehr als offen ist waere
  // eine Ueberzahlung, mehr als frei ist weist die Datenbank ohnehin ab. Der
  // haeufige Fall ist damit ein Klick, der seltene eine Korrektur.
  const suggestedMinor = Math.min(candidate.remaining_minor, outstandingMinor);
  const [amountInput, setAmountInput] = useState(() => minorToInput(suggestedMinor));
  const [lastSuggested, setLastSuggested] = useState(suggestedMinor);

  // Beim Rendern nachziehen statt im Effect: aendert sich der freie Rest,
  // weil anderswo zugeordnet wurde, muss der Vorschlag mitgehen - sonst
  // stuende im Feld ein Betrag, den die Buchung nicht mehr hergibt. Ein
  // Effect wuerde hier eine zusaetzliche Renderrunde ausloesen.
  if (lastSuggested !== suggestedMinor) {
    setLastSuggested(suggestedMinor);
    setAmountInput(minorToInput(suggestedMinor));
  }

  const parsed = parseAmountToMinorUnits(amountInput);
  const invalid = parsed === null || parsed <= 0 || parsed > candidate.remaining_minor;

  return (
    <tr className="txn-tr">
      <td className="txn-td">{candidate.booked_date}</td>
      <td className="txn-td">{candidate.payee_label || candidate.note || "—"}</td>
      <td className="txn-td">{formatMinorUnitsAsEuro(candidate.remaining_minor)}</td>
      <td className="txn-td">
        {candidate.evidence.length > 0 ? (
          <ul className="food-proposal__reasons">
            {candidate.evidence.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        ) : (
          <span className="txn-muted">Betrag und Zeitraum passen ungefähr</span>
        )}
      </td>
      <td className="txn-td">
        <div className="food-proposal__actions">
          <label className="visually-hidden" htmlFor={`alloc-${candidate.transaction_id}`}>
            Betrag für {candidate.payee_label ?? "Buchung"}
          </label>
          <input
            id={`alloc-${candidate.transaction_id}`}
            className="form-input"
            inputMode="decimal"
            size={8}
            value={amountInput}
            disabled={busy}
            onChange={(event) => setAmountInput(event.target.value)}
          />
          <button
            type="button"
            className="btn-primary"
            disabled={busy || invalid}
            onClick={() => onAllocate(caseId, candidate.transaction_id, parsed as number)}
          >
            Zuordnen
          </button>
        </div>
        {invalid && (
          <p className="form-error">
            Betrag muss über 0 liegen und darf die freien{" "}
            {formatMinorUnitsAsEuro(candidate.remaining_minor)} nicht überschreiten.
          </p>
        )}
      </td>
    </tr>
  );
}

function minorToInput(minor: number): string {
  return (minor / 100).toFixed(2).replace(".", ",");
}
