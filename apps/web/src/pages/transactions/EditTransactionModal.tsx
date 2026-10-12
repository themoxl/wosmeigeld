import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { setTransactionCategory, updateTransaction } from "../../lib/api";
import type { Account, CategoryGroup, Transaction } from "../../lib/types";
import { Modal } from "../../components/Modal";
import { CategoryPicker } from "../../components/CategoryPicker";
import { ProvenancePanel } from "../../components/ProvenancePanel";

// Abschnitt 10.3 (Buchungsregister -> "Aktionen: bearbeiten/loeschen") und
// docs/KATEGORISIERUNGSPLAN.md ("Buchung bearbeiten erhaelt direkt nach dem
// Zahlungsempfaenger das sichtbare Feld Kategorie").
//
// Der Dialog schloss die Kategorie frueher bewusst aus - genau deshalb wurde
// die Aenderungsmoeglichkeit als nicht vorhanden wahrgenommen. Sie ist jetzt
// sichtbar, aber weiterhin split-sicher: bei genau einem Split aendert das
// Feld dessen Kategorie ueber PUT /v1/transactions/{id}/category, bei
// mehreren Splits steht dort "Mehrere Kategorien" mit dem Weg in den
// Split-Editor. Eine Gesamtkategorie darf eine vorhandene Aufteilung niemals
// still ueberschreiben.
//
// status ist hier bewusst auf die zwei per PATCH erlaubten Werte beschraenkt
// (Backend: TransactionUpdate.status: Literal["pending","booked"]).
type EditableStatus = "pending" | "booked";

const STATUS_OPTIONS: { value: EditableStatus; label: string }[] = [
  { value: "pending", label: "Vorgemerkt" },
  { value: "booked", label: "Gebucht" },
];

interface EditTransactionModalProps {
  transaction: Transaction;
  accounts: Account[] | undefined;
  categoryGroups: CategoryGroup[] | undefined;
  /** Oeffnet den Split-Editor fuer dieselbe Buchung. */
  onEditSplits: () => void;
  onClose: () => void;
}

export function EditTransactionModal({
  transaction,
  accounts,
  categoryGroups,
  onEditSplits,
  onClose,
}: EditTransactionModalProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const [bookedDate, setBookedDate] = useState(transaction.booked_date);
  const [accountId, setAccountId] = useState(transaction.account_id);
  const [payeeLabel, setPayeeLabel] = useState(transaction.payee_label ?? "");
  const [categoryId, setCategoryId] = useState(transaction.category_id ?? "");
  const [note, setNote] = useState(transaction.note ?? "");
  const [status, setStatus] = useState<EditableStatus>(
    transaction.status === "pending" ? "pending" : "booked",
  );
  const [formError, setFormError] = useState<string | null>(null);

  const isSplit = transaction.is_split;
  const categoryChanged = !isSplit && categoryId !== (transaction.category_id ?? "");

  const saveMutation = useMutation({
    mutationFn: async () => {
      // Erst die Kategorie: PUT .../category prueft `expected_version` gegen
      // den zuletzt gelesenen Stand. Das nachfolgende PATCH erhoeht die
      // Version, ein umgekehrter Ablauf wuerde also faelschlich einen
      // Versionskonflikt melden.
      if (categoryChanged) {
        await setTransactionCategory(
          accessToken as string,
          selectedHouseholdId as string,
          transaction.id,
          { category_id: categoryId || null, expected_version: transaction.version },
        );
      }
      return updateTransaction(
        accessToken as string,
        selectedHouseholdId as string,
        transaction.id,
        {
          booked_date: bookedDate,
          account_id: accountId,
          payee_label: payeeLabel.trim() || null,
          note: note.trim() || null,
          status,
        },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["transaction-detail", transaction.id] });
      onClose();
    },
    onError: (error: unknown) => {
      setFormError(
        error instanceof Error ? error.message : "Buchung konnte nicht gespeichert werden.",
      );
    },
  });

  return (
    <Modal title="Buchung bearbeiten" onClose={onClose}>
      <form
        className="edit-transaction-form"
        onSubmit={(event) => {
          event.preventDefault();
          setFormError(null);
          saveMutation.mutate();
        }}
      >
        <label className="form-field">
          <span className="form-label">Datum</span>
          <input
            type="date"
            className="form-input"
            value={bookedDate}
            onChange={(event) => setBookedDate(event.target.value)}
            required
          />
        </label>

        <label className="form-field">
          <span className="form-label">Konto</span>
          <select
            className="form-input"
            value={accountId}
            onChange={(event) => setAccountId(event.target.value)}
            required
          >
            {accounts?.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </label>

        <label className="form-field">
          <span className="form-label">Zahlungsempfänger</span>
          <input
            className="form-input"
            value={payeeLabel}
            onChange={(event) => setPayeeLabel(event.target.value)}
          />
        </label>

        <div className="form-field">
          <label className="form-label" htmlFor="edit-transaction-category">
            Kategorie
          </label>
          {isSplit ? (
            <div className="edit-transaction-split-hint">
              <span className="status-badge status-badge--highlight">Mehrere Kategorien</span>
              <span className="placeholder-note">
                Diese Buchung ist auf mehrere Kategorien aufgeteilt. Eine Gesamtkategorie würde die
                Aufteilung überschreiben.
              </span>
              <button type="button" className="btn-secondary" onClick={onEditSplits}>
                Splits bearbeiten
              </button>
            </div>
          ) : (
            <CategoryPicker
              id="edit-transaction-category"
              categoryGroups={categoryGroups}
              value={categoryId}
              onChange={setCategoryId}
              amountMinor={transaction.amount_minor}
            />
          )}
        </div>

        <label className="form-field">
          <span className="form-label">Notiz</span>
          <input
            className="form-input"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>

        <div className="form-field">
          <span className="form-label">Status</span>
          <div className="direction-toggle" role="group" aria-label="Buchungsstatus">
            {STATUS_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={
                  status === option.value ? "direction-btn direction-btn--active" : "direction-btn"
                }
                aria-pressed={status === option.value}
                onClick={() => setStatus(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        {transaction.status === "reconciled" && (
          <p className="placeholder-note">
            Diese Buchung ist bereits abgestimmt. Beim Speichern wird sie auf „Gebucht“
            zurückgesetzt.
          </p>
        )}

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button type="submit" className="btn-primary" disabled={saveMutation.isPending}>
            {saveMutation.isPending ? "Wird gespeichert…" : "Speichern"}
          </button>
        </div>
      </form>

      {/* Ausserhalb des Formulars: die Herkunft ist eine Auskunft, kein
          Eingabefeld - und sie darf beim Absenden nichts mitschicken. */}
      <ProvenancePanel transactionId={transaction.id} />
    </Modal>
  );
}
