import { useState } from "react";
import {
  useActuals,
  useCreateActual,
  useDeleteActual,
  useTransactionCandidates,
} from "../../hooks/useProjectActuals";
import { apiErrorDetail } from "../../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import type { CostActual, CostItem, TransactionCandidate } from "../../lib/types";

// Ist-Kosten-Zuordnung je Kostenposition (Plan 4.6/7.2, P4-Abnahme). Wird als
// Modalinhalt aus CostPlanTable.tsx geoeffnet - Istkosten haengen an genau
// einer Position, ein eigener Tab wuerde die Zuordnung unnoetig vom
// Kostenplan trennen.

const SOURCE_LABELS: Record<CostActual["source"], string> = {
  transaction: "Buchung",
  manual: "Manuell",
};

function describeCreateError(error: unknown): string {
  if (apiErrorDetail(error) === "actuals_overbooking") {
    return "Dieser Betrag übersteigt den noch verfügbaren Betrag dieser Buchung.";
  }
  return "Zuordnung konnte nicht gespeichert werden.";
}

interface ActualsPanelProps {
  projectId: string;
  item: CostItem;
}

export function ActualsPanel({ projectId, item }: ActualsPanelProps) {
  const actualsQuery = useActuals(projectId, item.id);
  const createActual = useCreateActual();
  const deleteActualMutation = useDeleteActual();

  const [manualAmountEuro, setManualAmountEuro] = useState("");
  const [manualDate, setManualDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [manualNote, setManualNote] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [candidateAmounts, setCandidateAmounts] = useState<Record<string, string>>({});

  const candidatesQuery = useTransactionCandidates(projectId, {
    search: search.trim() || undefined,
  });

  const handleManualSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const amountMinor = parseAmountToMinorUnits(manualAmountEuro);
    if (amountMinor === null || amountMinor <= 0 || !manualDate) return;
    setCreateError(null);
    createActual.mutate(
      {
        projectId,
        itemId: item.id,
        scenarioId: item.scenario_id,
        input: {
          source: "manual",
          amount_minor: amountMinor,
          occurred_on: manualDate,
          note: manualNote.trim() || undefined,
        },
      },
      {
        onSuccess: () => {
          setManualAmountEuro("");
          setManualNote("");
        },
        onError: (error) => setCreateError(describeCreateError(error)),
      },
    );
  };

  const handleLinkCandidate = (candidate: TransactionCandidate) => {
    const raw = candidateAmounts[candidate.transaction_id];
    const amountMinor =
      raw !== undefined && raw !== "" ? parseAmountToMinorUnits(raw) : candidate.remaining_minor;
    if (amountMinor === null || amountMinor <= 0) {
      setCreateError("Bitte einen gültigen Zuordnungsbetrag größer null angeben.");
      return;
    }
    setCreateError(null);
    createActual.mutate(
      {
        projectId,
        itemId: item.id,
        scenarioId: item.scenario_id,
        input: {
          source: "transaction",
          amount_minor: amountMinor,
          transaction_id: candidate.transaction_id,
        },
      },
      {
        onSuccess: () => {
          setCandidateAmounts((prev) => {
            const next = { ...prev };
            delete next[candidate.transaction_id];
            return next;
          });
        },
        onError: (error) => setCreateError(describeCreateError(error)),
      },
    );
  };

  const actuals = actualsQuery.data ?? [];
  const candidates = candidatesQuery.data ?? [];

  return (
    <div>
      <div className="card">
        <h2 className="card-heading">Vorhandene Zuordnungen</h2>
        {actualsQuery.isError && (
          <p className="status-error" role="alert">
            Zuordnungen konnten nicht geladen werden.
          </p>
        )}
        {actualsQuery.isLoading && <p className="metric-hint">Lade Zuordnungen...</p>}
        {!actualsQuery.isLoading && actuals.length === 0 && (
          <p className="metric-hint">Noch keine Ist-Kosten zugeordnet.</p>
        )}
        {actuals.length > 0 && (
          <div className="txn-table-wrapper">
            <table className="txn-table">
              <thead>
                <tr>
                  <th className="txn-th">Quelle</th>
                  <th className="txn-th txn-th--date">Datum</th>
                  <th className="txn-th txn-th--amount-wide">Betrag</th>
                  <th className="txn-th">Notiz</th>
                  <th className="txn-th txn-th--actions-wide"></th>
                </tr>
              </thead>
              <tbody>
                {actuals.map((actual) => (
                  <tr className="txn-tr" key={actual.id}>
                    <td className="txn-td">
                      {SOURCE_LABELS[actual.source]}
                      {actual.is_archived_link && (
                        <span
                          className="status-badge status-badge--warning"
                          style={{ marginLeft: "var(--space-2)" }}
                        >
                          archivierte Buchung
                        </span>
                      )}
                    </td>
                    <td className="txn-td txn-td--date">{actual.occurred_on ?? "—"}</td>
                    <td className="txn-td txn-td--amount-wide">
                      {formatMinorUnitsAsEuro(actual.amount_minor)}
                    </td>
                    <td className="txn-td">{actual.note ?? "—"}</td>
                    <td className="txn-td txn-td--actions-wide">
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() =>
                          deleteActualMutation.mutate({
                            projectId,
                            actualId: actual.id,
                            itemId: item.id,
                            scenarioId: item.scenario_id,
                          })
                        }
                        disabled={deleteActualMutation.isPending}
                      >
                        Entfernen
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: "var(--space-4)" }}>
        <h2 className="card-heading">Manuell hinzufügen</h2>
        <form
          onSubmit={handleManualSubmit}
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--space-3)" }}
        >
          <div className="form-field">
            <label className="form-label" htmlFor="actual-amount">
              Betrag (€) *
            </label>
            <input
              id="actual-amount"
              className="form-input form-input--mono"
              value={manualAmountEuro}
              onChange={(e) => setManualAmountEuro(e.target.value)}
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="actual-date">
              Datum *
            </label>
            <input
              id="actual-date"
              type="date"
              className="form-input"
              value={manualDate}
              onChange={(e) => setManualDate(e.target.value)}
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="actual-note">
              Notiz
            </label>
            <input
              id="actual-note"
              className="form-input"
              value={manualNote}
              onChange={(e) => setManualNote(e.target.value)}
            />
          </div>
          <button
            type="submit"
            className="btn-primary"
            disabled={createActual.isPending}
            style={{ gridColumn: "1 / -1", justifySelf: "start" }}
          >
            Hinzufügen
          </button>
        </form>
      </div>

      <div className="card" style={{ marginTop: "var(--space-4)" }}>
        <h2 className="card-heading">Buchung zuordnen</h2>
        <div className="form-field">
          <label className="form-label" htmlFor="candidate-search">
            Suche (Empfänger/Notiz)
          </label>
          <input
            id="candidate-search"
            className="form-input"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="z. B. Baumarkt"
          />
        </div>
        <p className="metric-hint">
          Der Zuordnungsbetrag ist vorbelegt mit dem noch verfügbaren Betrag der Buchung, lässt sich
          aber verringern - so kann eine Buchung auf mehrere Positionen verteilt werden, ohne ihren
          Betrag zu überschreiten.
        </p>
        {candidatesQuery.isError && (
          <p className="status-error" role="alert">
            Buchungen konnten nicht geladen werden.
          </p>
        )}
        {candidatesQuery.isLoading && <p className="metric-hint">Lade Buchungen...</p>}
        {!candidatesQuery.isLoading && candidates.length === 0 && (
          <p className="metric-hint">Keine passenden Buchungen gefunden.</p>
        )}
        {candidates.length > 0 && (
          <div className="txn-table-wrapper">
            <table className="txn-table">
              <thead>
                <tr>
                  <th className="txn-th txn-th--date">Datum</th>
                  <th className="txn-th">Empfänger</th>
                  <th className="txn-th txn-th--amount-wide">Buchungsbetrag</th>
                  <th className="txn-th txn-th--amount-wide">Verfügbar</th>
                  <th className="txn-th txn-th--amount-wide">Zuordnungsbetrag</th>
                  <th className="txn-th txn-th--actions-wide"></th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((candidate) => (
                  <tr className="txn-tr" key={candidate.transaction_id}>
                    <td className="txn-td txn-td--date">{candidate.booked_date}</td>
                    <td className="txn-td txn-payee">{candidate.payee_label ?? "—"}</td>
                    <td className="txn-td txn-td--amount-wide">
                      {formatMinorUnitsAsEuro(candidate.amount_minor)}
                    </td>
                    <td className="txn-td txn-td--amount-wide">
                      {formatMinorUnitsAsEuro(candidate.remaining_minor)}
                    </td>
                    <td className="txn-td txn-td--amount-wide">
                      <input
                        className="form-input form-input--mono"
                        aria-label={`Zuordnungsbetrag für Buchung vom ${candidate.booked_date}`}
                        placeholder={String(candidate.remaining_minor / 100).replace(".", ",")}
                        value={candidateAmounts[candidate.transaction_id] ?? ""}
                        onChange={(e) =>
                          setCandidateAmounts((prev) => ({
                            ...prev,
                            [candidate.transaction_id]: e.target.value,
                          }))
                        }
                        style={{ width: "100px" }}
                      />
                    </td>
                    <td className="txn-td txn-td--actions-wide">
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => handleLinkCandidate(candidate)}
                        disabled={createActual.isPending || candidate.remaining_minor <= 0}
                      >
                        Zuordnen
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {createError && (
        <p className="status-error" role="alert" style={{ marginTop: "var(--space-3)" }}>
          {createError}
        </p>
      )}
    </div>
  );
}
