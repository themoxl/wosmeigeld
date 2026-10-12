import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useFormatDate } from "../hooks/useProfile";
import { useHousehold } from "../household/HouseholdProvider";
import {
  createAccount,
  listAccounts,
  listReconciliations,
  reconcileAccount,
  updateAccount,
} from "../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import type { Account, AccountType, AccountUpdateInput, Reconciliation } from "../lib/types";

// Abschnitt 6.2 des Architekturplans: die acht erlaubten Kontotypen mit
// deutschen Anzeigenamen (siehe Kontotypen-Aufzaehlung).
const ACCOUNT_TYPE_LABEL: Record<AccountType, string> = {
  checking: "Girokonto",
  credit_card: "Kreditkarte",
  savings: "Tagesgeld/Sparen",
  cash: "Bargeld",
  brokerage: "Depot",
  loan: "Darlehen",
  real_estate: "Immobilie",
  other: "Sonstiges",
};

const ACCOUNT_TYPES = Object.keys(ACCOUNT_TYPE_LABEL) as AccountType[];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

// Phase 2 (Aufgabenstellung Punkt 2): Konten bearbeiten/schliessen ueber
// PATCH /v1/accounts/{id} und Kontoabstimmung ueber POST .../reconcile +
// GET .../reconciliations (Historie als Bonus).
export function AccountsPage() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();

  const [name, setName] = useState("");
  const [accountType, setAccountType] = useState<AccountType>("checking");
  const [institution, setInstitution] = useState("");
  const [openingBalance, setOpeningBalance] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const accountsQuery = useQuery({
    queryKey: ["accounts", selectedHouseholdId],
    queryFn: () => listAccounts(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const minorUnits = openingBalance.trim() ? parseAmountToMinorUnits(openingBalance) : 0;
      if (minorUnits === null) {
        throw new Error("Startsaldo ist keine gueltige Zahl.");
      }
      return createAccount(accessToken as string, selectedHouseholdId as string, {
        name: name.trim(),
        account_type: accountType,
        institution: institution.trim() || undefined,
        opening_balance_minor: minorUnits,
      });
    },
    onSuccess: () => {
      setName("");
      setInstitution("");
      setOpeningBalance("");
      setAccountType("checking");
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["accounts", selectedHouseholdId] });
    },
    onError: (error: unknown) => {
      setFormError(error instanceof Error ? error.message : "Konto konnte nicht angelegt werden.");
    },
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!name.trim()) {
      setFormError("Bitte einen Namen angeben.");
      return;
    }
    setFormError(null);
    createMutation.mutate();
  };

  // -------------------------------------------------------------------
  // Konto bearbeiten (Name/Institut) und schliessen.
  // -------------------------------------------------------------------

  const [editAccountId, setEditAccountId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editInstitution, setEditInstitution] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  const updateMutation = useMutation({
    mutationFn: (vars: { id: string; patch: AccountUpdateInput }) =>
      updateAccount(accessToken as string, selectedHouseholdId as string, vars.id, vars.patch),
    onSuccess: (_account, vars) => {
      setEditAccountId((current) => (current === vars.id ? null : current));
      setEditError(null);
      void queryClient.invalidateQueries({ queryKey: ["accounts", selectedHouseholdId] });
    },
    onError: (error: unknown) => {
      setEditError(
        error instanceof Error ? error.message : "Konto konnte nicht gespeichert werden.",
      );
    },
  });

  function beginEditAccount(account: Account) {
    setEditAccountId(account.id);
    setEditName(account.name);
    setEditInstitution(account.institution ?? "");
    setEditError(null);
  }

  function cancelEditAccount() {
    setEditAccountId(null);
    setEditError(null);
  }

  function saveEditAccount(account: Account) {
    if (!editName.trim()) {
      setEditError("Bitte einen Namen angeben.");
      return;
    }
    updateMutation.mutate({
      id: account.id,
      patch: { name: editName.trim(), institution: editInstitution.trim() || null },
    });
  }

  function closeAccount(account: Account) {
    if (window.confirm(`Konto „${account.name}“ wirklich schließen?`)) {
      updateMutation.mutate({ id: account.id, patch: { is_closed: true } });
    }
  }

  // -------------------------------------------------------------------
  // Kontoabstimmung (Abschnitt 6.2/10.3).
  // -------------------------------------------------------------------

  const [reconcileAccountId, setReconcileAccountId] = useState("");
  const [asOfDate, setAsOfDate] = useState(todayIso);
  const [statementBalanceInput, setStatementBalanceInput] = useState("");
  const [reconcileError, setReconcileError] = useState<string | null>(null);
  const [reconcileResult, setReconcileResult] = useState<Reconciliation | null>(null);

  const effectiveReconcileAccountId = reconcileAccountId || accountsQuery.data?.[0]?.id || "";

  const reconciliationsQuery = useQuery({
    queryKey: ["reconciliations", effectiveReconcileAccountId],
    queryFn: () =>
      listReconciliations(
        accessToken as string,
        selectedHouseholdId as string,
        effectiveReconcileAccountId,
      ),
    enabled: Boolean(accessToken && selectedHouseholdId && effectiveReconcileAccountId),
  });

  const reconcileMutation = useMutation({
    mutationFn: () => {
      const minorUnits = parseAmountToMinorUnits(statementBalanceInput);
      if (minorUnits === null) {
        throw new Error("Kontoauszugssaldo ist keine gueltige Zahl.");
      }
      if (!effectiveReconcileAccountId) {
        throw new Error("Bitte ein Konto auswaehlen.");
      }
      return reconcileAccount(
        accessToken as string,
        selectedHouseholdId as string,
        effectiveReconcileAccountId,
        {
          as_of_date: asOfDate,
          statement_balance_minor: minorUnits,
        },
      );
    },
    onSuccess: (result) => {
      setReconcileResult(result);
      setReconcileError(null);
      void queryClient.invalidateQueries({
        queryKey: ["reconciliations", effectiveReconcileAccountId],
      });
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
    },
    onError: (error: unknown) => {
      setReconcileResult(null);
      setReconcileError(
        error instanceof Error ? error.message : "Kontoabstimmung konnte nicht gespeichert werden.",
      );
    },
  });

  const handleReconcileSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setReconcileError(null);
    reconcileMutation.mutate();
  };

  return (
    <section className="page">
      <div className="page-header">
        <h1 className="page-heading">Konten</h1>
      </div>

      {accountsQuery.isLoading && <p role="status">Lade Konten…</p>}
      {accountsQuery.isError && (
        <p role="alert" className="status-error">
          Konten konnten nicht geladen werden.
        </p>
      )}

      {editError && (
        <p role="alert" className="status-error">
          {editError}
        </p>
      )}

      {accountsQuery.data && (
        <ul className="account-list">
          {accountsQuery.data.length === 0 && (
            <li className="account-row account-row--empty">Noch keine Konten angelegt.</li>
          )}
          {accountsQuery.data.map((account) =>
            editAccountId === account.id ? (
              <li key={account.id} className="account-row account-row--editing">
                <input
                  className="form-input account-row-name"
                  value={editName}
                  onChange={(event) => setEditName(event.target.value)}
                  aria-label="Kontoname"
                  autoFocus
                />
                <input
                  className="form-input account-row-institution"
                  value={editInstitution}
                  onChange={(event) => setEditInstitution(event.target.value)}
                  placeholder="Institut"
                  aria-label="Institut"
                />
                <span className="account-row-actions">
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => saveEditAccount(account)}
                    disabled={updateMutation.isPending}
                  >
                    Speichern
                  </button>
                  <button type="button" className="btn-secondary" onClick={cancelEditAccount}>
                    Abbrechen
                  </button>
                </span>
              </li>
            ) : (
              <li key={account.id} className="account-row">
                <span className="account-row-name">{account.name}</span>
                <span className="account-row-type">
                  {ACCOUNT_TYPE_LABEL[account.account_type as AccountType] ?? account.account_type}
                </span>
                <span className="account-row-institution">{account.institution ?? "—"}</span>
                <span className="account-row-budget">
                  {account.on_budget ? "On-Budget" : "Off-Budget"}
                </span>
                <span
                  className={
                    account.opening_balance_minor < 0
                      ? "account-row-balance account-row-balance--negative"
                      : "account-row-balance"
                  }
                >
                  {formatMinorUnitsAsEuro(account.opening_balance_minor, account.currency)}
                </span>
                <span className="account-row-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => beginEditAccount(account)}
                  >
                    Bearbeiten
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => closeAccount(account)}
                  >
                    Schließen
                  </button>
                </span>
              </li>
            ),
          )}
        </ul>
      )}

      <div className="card">
        <h2 className="card-heading">Neues Konto anlegen</h2>
        <form className="inline-form" onSubmit={handleSubmit}>
          <label className="form-field">
            <span className="form-label">Name</span>
            <input
              className="form-input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </label>

          <label className="form-field">
            <span className="form-label">Kontotyp</span>
            <select
              className="form-input"
              value={accountType}
              onChange={(event) => setAccountType(event.target.value as AccountType)}
            >
              {ACCOUNT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {ACCOUNT_TYPE_LABEL[type]}
                </option>
              ))}
            </select>
          </label>

          <label className="form-field">
            <span className="form-label">Institut (optional)</span>
            <input
              className="form-input"
              value={institution}
              onChange={(event) => setInstitution(event.target.value)}
            />
          </label>

          <label className="form-field">
            <span className="form-label">Startsaldo (optional)</span>
            <input
              className="form-input form-input--mono"
              placeholder="0,00"
              value={openingBalance}
              onChange={(event) => setOpeningBalance(event.target.value)}
            />
          </label>

          {formError && (
            <p role="alert" className="status-error">
              {formError}
            </p>
          )}

          <button type="submit" className="btn-primary" disabled={createMutation.isPending}>
            {createMutation.isPending ? "Wird angelegt…" : "Konto anlegen"}
          </button>
        </form>
      </div>

      <div className="card">
        <h2 className="card-heading">Kontoabstimmung</h2>
        <form className="inline-form" onSubmit={handleReconcileSubmit}>
          <label className="form-field">
            <span className="form-label">Konto</span>
            <select
              className="form-input"
              value={effectiveReconcileAccountId}
              onChange={(event) => {
                setReconcileAccountId(event.target.value);
                setReconcileResult(null);
                setReconcileError(null);
              }}
              required
            >
              <option value="" disabled>
                Konto wählen
              </option>
              {accountsQuery.data?.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </label>

          <label className="form-field">
            <span className="form-label">Stichtag</span>
            <input
              type="date"
              className="form-input"
              value={asOfDate}
              onChange={(event) => setAsOfDate(event.target.value)}
              required
            />
          </label>

          <label className="form-field">
            <span className="form-label">Kontoauszugssaldo</span>
            <input
              className="form-input form-input--mono"
              placeholder="0,00"
              value={statementBalanceInput}
              onChange={(event) => setStatementBalanceInput(event.target.value)}
              required
            />
          </label>

          {reconcileError && (
            <p role="alert" className="status-error">
              {reconcileError}
            </p>
          )}

          <button
            type="submit"
            className="btn-primary"
            disabled={reconcileMutation.isPending || !accountsQuery.data?.length}
          >
            {reconcileMutation.isPending ? "Wird geprüft…" : "Abstimmen"}
          </button>
        </form>

        {reconcileResult && (
          <p
            className={
              reconcileResult.status === "completed"
                ? "reconcile-result status-success"
                : "reconcile-result status-error"
            }
            role="status"
          >
            {reconcileResult.status === "completed"
              ? "Saldo stimmt überein. Betroffene Buchungen wurden als „abgestimmt“ markiert."
              : `Abweichung: ${formatMinorUnitsAsEuro(reconcileResult.difference_minor)} (berechnet ` +
                `${formatMinorUnitsAsEuro(reconcileResult.computed_balance_minor)}, Kontoauszug ` +
                `${formatMinorUnitsAsEuro(reconcileResult.statement_balance_minor)}).`}
          </p>
        )}

        {reconciliationsQuery.data && reconciliationsQuery.data.length > 0 && (
          <ul className="reconciliation-list">
            {reconciliationsQuery.data.map((entry) => (
              <li key={entry.id} className="reconciliation-row">
                <span className="reconciliation-date">{formatDate(entry.as_of_date)}</span>
                <span
                  className={
                    entry.status === "completed"
                      ? "status-badge status-badge--positive"
                      : "status-badge status-badge--warning"
                  }
                >
                  {entry.status === "completed" ? "Übereinstimmend" : "Abweichung"}
                </span>
                <span className="reconciliation-diff">
                  {formatMinorUnitsAsEuro(entry.difference_minor)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
