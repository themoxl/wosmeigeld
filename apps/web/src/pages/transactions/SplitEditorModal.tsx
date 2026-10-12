import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { getTransaction, replaceTransactionSplits } from "../../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import type {
  CategoryGroup,
  SavingsGoal,
  TransactionDetail,
  TransactionSplit,
} from "../../lib/types";
import { Modal } from "../../components/Modal";
import { CategoryPicker } from "../../components/CategoryPicker";
import { GoalSelect } from "./GoalSelect";

// Abschnitt 10.3 (Buchungsregister -> "Splits und Transferverknuepfung") und
// Abschnitt 9 (Splits muessen sich exakt zum Buchungsbetrag summieren).
// Der Editor bearbeitet ueber PUT .../splits sowohl den Gesamtbetrag als
// auch die Kategorie(n)/Splits einer Buchung (siehe Aufgabenstellung) und
// verhindert das Abschicken bereits clientseitig, wenn die Restsumme nicht
// null ist.

function minorToInputString(minor: number): string {
  const negative = minor < 0;
  const abs = Math.abs(minor);
  const euros = Math.floor(abs / 100);
  const cents = abs % 100;
  return `${negative ? "-" : ""}${euros},${String(cents).padStart(2, "0")}`;
}

interface SplitRow {
  key: string;
  category_id: string;
  goal_id: string;
  amountInput: string;
  note: string;
}

let rowKeySeq = 0;
function nextRowKey(): string {
  rowKeySeq += 1;
  return `split-${rowKeySeq}`;
}

function rowsFromSplits(splits: TransactionSplit[]): SplitRow[] {
  if (splits.length === 0) {
    return [{ key: nextRowKey(), category_id: "", goal_id: "", amountInput: "", note: "" }];
  }
  return splits.map((split) => ({
    key: nextRowKey(),
    category_id: split.category_id ?? "",
    goal_id: split.goal_id ?? "",
    amountInput: minorToInputString(split.amount_minor),
    note: split.note ?? "",
  }));
}

interface SplitEditorFormProps {
  detail: TransactionDetail;
  categoryGroups: CategoryGroup[] | undefined;
  goals: SavingsGoal[] | undefined;
  onClose: () => void;
}

function SplitEditorForm({ detail, categoryGroups, goals, onClose }: SplitEditorFormProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const [totalInput, setTotalInput] = useState(minorToInputString(detail.amount_minor));
  const [rows, setRows] = useState<SplitRow[]>(() => rowsFromSplits(detail.splits));
  const [formError, setFormError] = useState<string | null>(null);

  const totalMinor = parseAmountToMinorUnits(totalInput);
  const parsedRows = rows.map((row) => ({
    row,
    amountMinor: parseAmountToMinorUnits(row.amountInput),
  }));
  const hasInvalidRowAmount = parsedRows.some(({ amountMinor }) => amountMinor === null);
  const splitsSum = parsedRows.reduce((sum, { amountMinor }) => sum + (amountMinor ?? 0), 0);
  const remaining = totalMinor === null ? null : totalMinor - splitsSum;
  const canSubmit =
    totalMinor !== null && !hasInvalidRowAmount && remaining === 0 && rows.length > 0;

  const saveMutation = useMutation({
    mutationFn: () => {
      if (totalMinor === null) {
        throw new Error("Gesamtbetrag ist keine gueltige Zahl.");
      }
      if (hasInvalidRowAmount) {
        throw new Error("Mindestens ein Split-Betrag ist keine gueltige Zahl.");
      }
      if (remaining !== 0) {
        throw new Error(
          `Die Splits ergeben ${formatMinorUnitsAsEuro(splitsSum)}, die Buchung aber ` +
            `${formatMinorUnitsAsEuro(totalMinor)}. Restsumme muss 0,00 € sein.`,
        );
      }
      return replaceTransactionSplits(
        accessToken as string,
        selectedHouseholdId as string,
        detail.id,
        {
          amount_minor: totalMinor,
          splits: rows.map(({ category_id, goal_id, amountInput, note }) => ({
            category_id: category_id || null,
            goal_id: goal_id || null,
            amount_minor: parseAmountToMinorUnits(amountInput) as number,
            note: note.trim() || undefined,
          })),
        },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["transaction-detail", detail.id] });
      onClose();
    },
    onError: (error: unknown) => {
      setFormError(
        error instanceof Error ? error.message : "Splits konnten nicht gespeichert werden.",
      );
    },
  });

  const updateRow = (key: string, patch: Partial<SplitRow>) => {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  };

  const addRow = () => {
    setRows((current) => [
      ...current,
      { key: nextRowKey(), category_id: "", goal_id: "", amountInput: "", note: "" },
    ]);
  };

  const removeRow = (key: string) => {
    setRows((current) => (current.length > 1 ? current.filter((row) => row.key !== key) : current));
  };

  return (
    <form
      className="split-editor-form"
      onSubmit={(event) => {
        event.preventDefault();
        setFormError(null);
        if (!canSubmit) {
          setFormError("Die Restsumme muss 0,00 € sein, bevor gespeichert werden kann.");
          return;
        }
        saveMutation.mutate();
      }}
    >
      <label className="form-field">
        <span className="form-label">Gesamtbetrag</span>
        <input
          className="form-input form-input--mono"
          value={totalInput}
          onChange={(event) => setTotalInput(event.target.value)}
        />
      </label>

      <div className="split-rows">
        {rows.map((row) => (
          <div className="split-row" key={row.key}>
            <CategoryPicker
              categoryGroups={categoryGroups}
              value={row.category_id}
              onChange={(value) => updateRow(row.key, { category_id: value })}
              className="split-row-category"
              aria-label="Kategorie dieses Splits"
              amountMinor={parseAmountToMinorUnits(row.amountInput)}
            />
            <GoalSelect
              goals={goals}
              value={row.goal_id}
              onChange={(value) => updateRow(row.key, { goal_id: value })}
              className="form-input split-row-goal"
            />
            <input
              className="form-input form-input--mono split-row-amount"
              placeholder="0,00"
              value={row.amountInput}
              onChange={(event) => updateRow(row.key, { amountInput: event.target.value })}
            />
            <input
              className="form-input split-row-note"
              placeholder="Notiz (optional)"
              value={row.note}
              onChange={(event) => updateRow(row.key, { note: event.target.value })}
            />
            <button
              type="button"
              className="icon-btn"
              onClick={() => removeRow(row.key)}
              disabled={rows.length <= 1}
              aria-label="Split entfernen"
              title="Split entfernen"
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <button type="button" className="btn-secondary" onClick={addRow}>
        + Split hinzufügen
      </button>

      <p
        className={`split-remaining ${remaining === 0 ? "split-remaining--ok" : "split-remaining--error"}`}
      >
        Restsumme: {remaining === null ? "—" : formatMinorUnitsAsEuro(remaining)}
      </p>

      {formError && (
        <p role="alert" className="status-error">
          {formError}
        </p>
      )}

      <div className="modal-actions">
        <button type="button" className="btn-secondary" onClick={onClose}>
          Abbrechen
        </button>
        <button
          type="submit"
          className="btn-primary"
          disabled={!canSubmit || saveMutation.isPending}
        >
          {saveMutation.isPending ? "Wird gespeichert…" : "Speichern"}
        </button>
      </div>
    </form>
  );
}

interface SplitEditorModalProps {
  transactionId: string;
  categoryGroups: CategoryGroup[] | undefined;
  goals: SavingsGoal[] | undefined;
  onClose: () => void;
}

export function SplitEditorModal({
  transactionId,
  categoryGroups,
  goals,
  onClose,
}: SplitEditorModalProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  const detailQuery = useQuery({
    queryKey: ["transaction-detail", transactionId],
    queryFn: () =>
      getTransaction(accessToken as string, selectedHouseholdId as string, transactionId),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const title = useMemo(() => "Splits bearbeiten", []);

  return (
    <Modal title={title} onClose={onClose}>
      {detailQuery.isLoading && <p role="status">Lade Buchung…</p>}
      {detailQuery.isError && (
        <p role="alert" className="status-error">
          Buchung konnte nicht geladen werden.
        </p>
      )}
      {detailQuery.data && (
        <SplitEditorForm
          detail={detailQuery.data}
          categoryGroups={categoryGroups}
          goals={goals}
          onClose={onClose}
        />
      )}
    </Modal>
  );
}
