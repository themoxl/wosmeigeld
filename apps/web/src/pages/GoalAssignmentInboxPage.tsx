import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { assignGoalToSplits, listAccounts, listGoals, listUnassignedGoalSplits } from "../lib/api";
import { formatMinorUnitsAsEuro } from "../lib/money";
import { GoalSelect } from "./transactions/GoalSelect";

// Architekturplan 6.7, Option (c) neben der direkten Zuordnung am Split
// (SplitEditorModal): rueckwirkende Zuordnung bereits gebuchter Buchungen zu
// einem Sparziel, mit Mehrfachauswahl - anders als die Review-Inbox (dort nur
// Einzel-Aktionen), da hier haeufig viele alte Buchungen auf einmal
// zugeordnet werden.
export function GoalAssignmentInboxPage() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const [accountFilter, setAccountFilter] = useState("");
  const [selectedSplitIds, setSelectedSplitIds] = useState<Set<string>>(new Set());
  const [goalValue, setGoalValue] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const accountsQuery = useQuery({
    queryKey: ["accounts", selectedHouseholdId],
    queryFn: () => listAccounts(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const goalsQuery = useQuery({
    queryKey: ["goals", selectedHouseholdId],
    queryFn: () => listGoals(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const candidatesQuery = useQuery({
    queryKey: ["unassigned-goal-splits", selectedHouseholdId, accountFilter],
    queryFn: () =>
      listUnassignedGoalSplits(
        accessToken as string,
        selectedHouseholdId as string,
        accountFilter || undefined,
      ),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const accountsById = useMemo(
    () => new Map((accountsQuery.data ?? []).map((account) => [account.id, account.name])),
    [accountsQuery.data],
  );

  const assignMutation = useMutation({
    mutationFn: () =>
      assignGoalToSplits(accessToken as string, selectedHouseholdId as string, {
        split_ids: Array.from(selectedSplitIds),
        goal_id: goalValue || null,
      }),
    onSuccess: (result) => {
      setMessage(`${result.updated} Buchung(en) zugeordnet.`);
      setSelectedSplitIds(new Set());
      void queryClient.invalidateQueries({ queryKey: ["goals", selectedHouseholdId] });
      void queryClient.invalidateQueries({
        queryKey: ["unassigned-goal-splits", selectedHouseholdId],
      });
    },
  });

  const candidates = candidatesQuery.data ?? [];

  const toggleSplit = (splitId: string) => {
    setSelectedSplitIds((current) => {
      const next = new Set(current);
      if (next.has(splitId)) {
        next.delete(splitId);
      } else {
        next.add(splitId);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedSplitIds((current) =>
      current.size === candidates.length ? new Set() : new Set(candidates.map((c) => c.split_id)),
    );
  };

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Buchungen Sparzielen zuordnen</h1>
          <p className="placeholder-note">
            Bereits gebuchte Transaktionen ohne Sparziel-Zuordnung nachträglich einem Sparziel
            zuweisen.
          </p>
        </div>
        <label className="form-field">
          <span className="form-label">Konto-Filter</span>
          <select
            className="form-input"
            value={accountFilter}
            onChange={(e) => setAccountFilter(e.target.value)}
          >
            <option value="">Alle Konten</option>
            {accountsQuery.data?.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Link to="/sparziele" className="btn-secondary" style={{ alignSelf: "flex-start" }}>
        ← Zurück zu Sparziele
      </Link>

      {candidatesQuery.isLoading && <p className="placeholder-note">Lade Buchungen...</p>}
      {!candidatesQuery.isLoading && candidates.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
          <h2 className="card-heading">Keine offenen Buchungen</h2>
          <p className="card-body">
            Alle Buchungen sind entweder bereits einem Sparziel zugeordnet oder es gibt (noch)
            keine.
          </p>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th" style={{ width: "36px" }}>
                  <input
                    type="checkbox"
                    aria-label="Alle auswählen"
                    checked={selectedSplitIds.size === candidates.length}
                    onChange={toggleSelectAll}
                  />
                </th>
                <th className="txn-th">Datum</th>
                <th className="txn-th">Konto</th>
                <th className="txn-th">Zahlungsempfänger / Notiz</th>
                <th className="txn-th txn-th--amount">Betrag</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((candidate) => {
                const isSelected = selectedSplitIds.has(candidate.split_id);
                return (
                  <tr
                    key={candidate.split_id}
                    className={`txn-tr ${isSelected ? "txn-tr--selected" : ""}`}
                  >
                    <td className="txn-td">
                      <input
                        type="checkbox"
                        aria-label="Buchung auswählen"
                        checked={isSelected}
                        onChange={() => toggleSplit(candidate.split_id)}
                      />
                    </td>
                    <td className="txn-td transaction-date">{candidate.booked_date}</td>
                    <td className="txn-td">{accountsById.get(candidate.account_id) ?? "—"}</td>
                    <td className="txn-td txn-payee">
                      {candidate.payee_label || candidate.note || "—"}
                    </td>
                    <td
                      className={`txn-td txn-td--amount transaction-amount ${
                        candidate.amount_minor < 0
                          ? "transaction-amount--negative"
                          : "transaction-amount--positive"
                      }`}
                    >
                      {formatMinorUnitsAsEuro(candidate.amount_minor)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selectedSplitIds.size > 0 && (
        <div className="selection-bar">
          <span className="selection-bar-count">{selectedSplitIds.size} ausgewählt</span>

          <GoalSelect
            goals={goalsQuery.data}
            value={goalValue}
            onChange={setGoalValue}
            noneLabel="Sparziel wählen…"
            className="form-input"
          />
          <button
            type="button"
            className="btn-primary"
            onClick={() => assignMutation.mutate()}
            disabled={assignMutation.isPending || !goalValue}
          >
            {assignMutation.isPending ? "Wird zugeordnet…" : "Zuweisen"}
          </button>

          <button
            type="button"
            className="btn-secondary"
            onClick={() => setSelectedSplitIds(new Set())}
          >
            Auswahl aufheben
          </button>

          {message && <span className="selection-bar-message">{message}</span>}
        </div>
      )}
    </section>
  );
}
