import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Modal } from "../Modal";
import { SavingsPlanningEquation } from "../SavingsPlanningEquation";
import {
  apiErrorDetail,
  createReserveDeposit,
  createReserveReallocation,
  createReserveWithdrawal,
  getReserveOverview,
  previewReserveDistribution,
  skipReserveMonth,
  undoSkipReserveMonth,
} from "../../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import type { BudgetSummary, ReservePot } from "../../lib/types";

interface ReservesLedgerProps {
  token: string;
  householdId: string;
  year: number;
  month: number;
  budget: BudgetSummary;
  includeInPlan: boolean;
  onIncludeInPlanChange: (next: boolean) => void;
}

type MoneyDialog =
  | { kind: "deposit"; pot: ReservePot; amount: string }
  | { kind: "withdraw"; pot: ReservePot; amount: string }
  | null;

type BulkDraft = Record<string, { selected: boolean; amount: string }>;

const STATUS_LABEL: Record<ReservePot["status"], string> = {
  open: "Offen",
  partial: "Teilweise",
  done: "Erledigt",
  skipped: "Diesen Monat nicht",
};

function localOccurredOn(year: number, month: number): string {
  const today = new Date();
  if (today.getFullYear() === year && today.getMonth() + 1 === month) {
    return `${year}-${String(month).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  }
  return `${year}-${String(month).padStart(2, "0")}-01`;
}

function errorMessage(error: unknown): string {
  const detail = apiErrorDetail(error);
  if (detail === "reserve_balance_insufficient")
    return "Der bestätigte Topfstand reicht nicht aus.";
  if (detail === "reserve_pot_not_found") return "Der Rücklagentopf wurde nicht gefunden.";
  return error instanceof Error
    ? error.message
    : "Die Rücklagenbewegung konnte nicht gespeichert werden.";
}

function ReserveRow({
  pot,
  onDeposit,
  onWithdraw,
  onSkip,
  onUndoSkip,
  disabled,
}: {
  pot: ReservePot;
  onDeposit: () => void;
  onWithdraw: () => void;
  onSkip: () => void;
  onUndoSkip: () => void;
  disabled: boolean;
}) {
  const meta =
    pot.kind === "contract"
      ? `${pot.frequency === "quarterly" ? "Vierteljährlich" : pot.frequency === "semiannual" ? "Halbjährlich" : "Jährlich"} · fällig ${pot.next_due_date ? new Intl.DateTimeFormat("de-DE").format(new Date(`${pot.next_due_date}T12:00:00`)) : "–"}`
      : pot.target_date
        ? `Ziel bis ${new Intl.DateTimeFormat("de-DE", { month: "short", year: "numeric" }).format(new Date(`${pot.target_date}T12:00:00`))}`
        : "Flexibler Spartopf";
  return (
    <article className="reserve-row" data-testid={`reserve-pot-${pot.kind}`}>
      <div className="reserve-row__identity">
        <strong>{pot.name}</strong>
        <span>{meta}</span>
      </div>
      <div className="reserve-row__metric" data-label="Soll diesen Monat">
        <span>Soll diesen Monat</span>
        <strong>{formatMinorUnitsAsEuro(pot.recommended_minor)}</strong>
      </div>
      <div className="reserve-row__metric" data-label="Tatsächlich eingezahlt">
        <span>Tatsächlich eingezahlt</span>
        <strong>{formatMinorUnitsAsEuro(pot.deposited_minor)}</strong>
      </div>
      <div className="reserve-row__metric" data-label="Topfstand">
        <span>Topfstand</span>
        <strong>{formatMinorUnitsAsEuro(pot.balance_minor)}</strong>
      </div>
      <div className="reserve-row__metric" data-label="Nächster Monat">
        <span>Nächster Monat</span>
        <strong>{formatMinorUnitsAsEuro(pot.next_month_recommended_minor)}</strong>
      </div>
      <div className="reserve-row__status">
        <span className={`reserve-status reserve-status--${pot.status}`}>
          {STATUS_LABEL[pot.status]}
        </span>
        {pot.open_minor > 0 && <small>{formatMinorUnitsAsEuro(pot.open_minor)} offen</small>}
      </div>
      <div className="reserve-row__actions">
        <button type="button" className="btn-primary" disabled={disabled} onClick={onDeposit}>
          Einzahlung
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={disabled || pot.balance_minor <= 0}
          onClick={onWithdraw}
        >
          Entnahme
        </button>
        {pot.recommended_minor > 0 && pot.status !== "skipped" && (
          <button type="button" className="btn-ghost" disabled={disabled} onClick={onSkip}>
            Diesen Monat nicht
          </button>
        )}
        {pot.status === "skipped" && (
          <button type="button" className="btn-secondary" disabled={disabled} onClick={onUndoSkip}>
            Auslassen rückgängig
          </button>
        )}
      </div>
      {(pot.movements?.length ?? 0) > 0 && (
        <details className="reserve-row__history">
          <summary>Letzte Bewegungen</summary>
          <ul>
            {(pot.movements ?? []).slice(0, 4).map((movement) => (
              <li key={movement.id}>
                <span>
                  {new Intl.DateTimeFormat("de-DE").format(
                    new Date(`${movement.occurred_on}T12:00:00`),
                  )}
                </span>
                <strong
                  className={
                    movement.amount_minor < 0
                      ? "transaction-amount--negative"
                      : "transaction-amount--positive"
                  }
                >
                  {movement.amount_minor > 0 ? "+" : ""}
                  {formatMinorUnitsAsEuro(movement.amount_minor)}
                </strong>
                <span>
                  {movement.note ??
                    (movement.kind === "reallocation" ? "Umverteilung" : "Manuell bestätigt")}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

export function ReservesLedger({
  token,
  householdId,
  year,
  month,
  budget,
  includeInPlan,
  onIncludeInPlanChange,
}: ReservesLedgerProps) {
  const queryClient = useQueryClient();
  const queryKey = ["reserves", householdId, year, month] as const;
  const [moneyDialog, setMoneyDialog] = useState<MoneyDialog>(null);
  const [largeAmount, setLargeAmount] = useState("");
  const [distribution, setDistribution] = useState<Record<string, string> | null>(null);
  const [distributionTotal, setDistributionTotal] = useState<number | null>(null);
  const [bulkDraft, setBulkDraft] = useState<BulkDraft | null>(null);
  const [suggestionPending, setSuggestionPending] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: () => getReserveOverview(token, householdId, year, month),
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey });
    await queryClient.invalidateQueries({ queryKey: ["goals", householdId] });
  };
  const depositMutation = useMutation({
    mutationFn: createReserveDeposit.bind(null, token, householdId),
    onSuccess: async () => {
      setMoneyDialog(null);
      setDistribution(null);
      setDistributionTotal(null);
      setBulkDraft(null);
      setLargeAmount("");
      setFeedback("Einzahlung wurde bestätigt und dem Rücklagenstand zugeordnet.");
      await refresh();
    },
  });
  const withdrawMutation = useMutation({
    mutationFn: createReserveWithdrawal.bind(null, token, householdId),
    onSuccess: async () => {
      setMoneyDialog(null);
      setFeedback("Entnahme wurde im Rücklagenstand erfasst.");
      await refresh();
    },
  });
  const skipMutation = useMutation({
    mutationFn: (potId: string) =>
      skipReserveMonth(token, householdId, { pot_id: potId, year, month }),
    onSuccess: async () => {
      setFeedback("Der Sollwert bleibt sichtbar; der Monat ist als ausgesetzt markiert.");
      await refresh();
    },
  });
  const undoSkipMutation = useMutation({
    mutationFn: (potId: string) =>
      undoSkipReserveMonth(token, householdId, { pot_id: potId, year, month }),
    onSuccess: async () => {
      setFeedback("Die Monatsentscheidung wurde zurückgenommen; der Sollwert ist wieder offen.");
      await refresh();
    },
  });
  const reallocateMutation = useMutation({
    mutationFn: createReserveReallocation.bind(null, token, householdId),
    onSuccess: async () => {
      setDistribution(null);
      setDistributionTotal(null);
      setLargeAmount("");
      setFeedback("Freie Rücklage wurde bestätigt auf die ausgewählten Töpfe verteilt.");
      await refresh();
    },
  });

  const groups = useMemo(() => {
    const pots = data?.pots ?? [];
    return {
      contracts: pots.filter((pot) => pot.kind === "contract"),
      goals: pots.filter((pot) => pot.kind === "goal"),
      free: pots.find((pot) => pot.kind === "free"),
    };
  }, [data]);
  const pending =
    depositMutation.isPending ||
    withdrawMutation.isPending ||
    skipMutation.isPending ||
    undoSkipMutation.isPending ||
    reallocateMutation.isPending;
  const mutationError =
    depositMutation.error ??
    withdrawMutation.error ??
    skipMutation.error ??
    undoSkipMutation.error ??
    reallocateMutation.error;
  const distributionAssigned = Object.values(distribution ?? {}).reduce(
    (sum, value) => sum + (parseAmountToMinorUnits(value) ?? 0),
    0,
  );
  const isLargeDistribution = distributionTotal !== null && largeAmount.length > 0;
  const distributionIsValid = isLargeDistribution
    ? distributionAssigned === distributionTotal
    : distributionAssigned > 0 && distributionAssigned <= (groups.free?.balance_minor ?? 0);
  const bulkTotal = Object.values(bulkDraft ?? {}).reduce(
    (sum, item) => sum + (item.selected ? (parseAmountToMinorUnits(item.amount) ?? 0) : 0),
    0,
  );

  const submitMoneyDialog = () => {
    if (!moneyDialog) return;
    const amount = parseAmountToMinorUnits(moneyDialog.amount);
    if (!amount || amount <= 0) return;
    if (moneyDialog.kind === "deposit") {
      depositMutation.mutate({
        occurred_on: localOccurredOn(year, month),
        total_minor: amount,
        allocations: [{ pot_id: moneyDialog.pot.id, amount_minor: amount }],
        note: "Manuell bestätigt",
      });
    } else {
      withdrawMutation.mutate({
        occurred_on: localOccurredOn(year, month),
        pot_id: moneyDialog.pot.id,
        amount_minor: amount,
        note: "Manuell bestätigt",
      });
    }
  };

  const openBulkConfirmation = () => {
    if (!data) return;
    const eligible = data.pots.filter(
      (pot) =>
        pot.kind !== "free" &&
        pot.open_minor > 0 &&
        (pot.kind === "contract" || pot.is_included_in_planning),
    );
    if (eligible.length === 0) return;
    setBulkDraft(
      Object.fromEntries(
        eligible.map((pot) => [
          pot.id,
          {
            selected: true,
            amount: (pot.open_minor / 100).toFixed(2).replace(".", ","),
          },
        ]),
      ),
    );
  };

  const submitBulkConfirmation = () => {
    if (!data || !bulkDraft || bulkTotal <= 0) return;
    const allocations = data.pots.flatMap((pot) => {
      const draft = bulkDraft[pot.id];
      const amount = draft?.selected ? (parseAmountToMinorUnits(draft.amount) ?? 0) : 0;
      return amount > 0 ? [{ pot_id: pot.id, amount_minor: amount }] : [];
    });
    depositMutation.mutate({
      occurred_on: localOccurredOn(year, month),
      total_minor: bulkTotal,
      allocations,
      note: "Ausgewählte offene Sollwerte",
    });
  };

  const openLargeAmountDialog = () => {
    const total = parseAmountToMinorUnits(largeAmount);
    if (!total || total <= 0 || !groups.free) return;
    setLocalError(null);
    setDistributionTotal(total);
    setDistribution({
      [groups.free.id]: (total / 100).toFixed(2).replace(".", ","),
    });
  };

  const applyDistributionSuggestion = async () => {
    const total = distributionTotal;
    if (!total || total <= 0 || !groups.free) return;
    try {
      setSuggestionPending(true);
      setLocalError(null);
      const preview = await previewReserveDistribution(token, householdId, {
        year,
        month,
        total_minor: total,
      });
      const next: Record<string, string> = Object.fromEntries(
        preview.allocations.map((item) => [
          item.pot_id,
          (item.amount_minor / 100).toFixed(2).replace(".", ","),
        ]),
      );
      next[groups.free.id] = (preview.free_remainder_minor / 100).toFixed(2).replace(".", ",");
      setDistribution(next);
    } catch (previewError) {
      setLocalError(errorMessage(previewError));
    } finally {
      setSuggestionPending(false);
    }
  };

  const submitDistribution = () => {
    if (!data || !distribution || !distributionTotal) return;
    const allocations = Object.entries(distribution)
      .map(([potId, raw]) => ({ pot_id: potId, amount_minor: parseAmountToMinorUnits(raw) ?? 0 }))
      .filter((item) => item.amount_minor > 0);
    if (allocations.reduce((sum, item) => sum + item.amount_minor, 0) !== distributionTotal) return;
    depositMutation.mutate({
      occurred_on: localOccurredOn(year, month),
      total_minor: distributionTotal,
      allocations,
      note: "Explizit verteilte Rücklage",
    });
  };

  const submitReallocation = () => {
    if (!distribution || !groups.free) return;
    const allocations = Object.entries(distribution)
      .filter(([potId]) => potId !== groups.free?.id)
      .map(([potId, raw]) => ({ pot_id: potId, amount_minor: parseAmountToMinorUnits(raw) ?? 0 }))
      .filter((item) => item.amount_minor > 0);
    const total = allocations.reduce((sum, item) => sum + item.amount_minor, 0);
    if (total <= 0 || total > groups.free.balance_minor) return;
    reallocateMutation.mutate({
      occurred_on: localOccurredOn(year, month),
      allocations,
      note: "Aus freier Rücklage umverteilt",
    });
  };

  if (isLoading) return <p className="placeholder-note">Lade Rücklagenstände…</p>;
  if (error || !data)
    return <div className="card status-error">Rücklagen konnten nicht geladen werden.</div>;

  return (
    <div className="reserves-page" data-testid="reserves-ledger">
      <section className="reserves-intro">
        <div>
          <span className="eyebrow">Bestätigte Zweckzuordnung</span>
          <h2>
            Rücklagen für{" "}
            {new Intl.DateTimeFormat("de-DE", { month: "long", year: "numeric" }).format(
              new Date(year, month - 1, 1),
            )}
          </h2>
          <p>
            Die Sollwerte sind Empfehlungen. Ein echter Stand entsteht erst, wenn du einen Startwert
            oder eine Bewegung bestätigst – ohne automatische Bankerkennung.
          </p>
        </div>
        <label className="planning-checkbox planning-checkbox--toggle">
          <input
            type="checkbox"
            checked={includeInPlan}
            onChange={(event) => onIncludeInPlanChange(event.target.checked)}
          />
          Rücklagen in den Monatsplan einrechnen
        </label>
      </section>

      {includeInPlan && <SavingsPlanningEquation budget={budget} />}

      <div className="reserve-metrics">
        <div>
          <span>Empfohlen</span>
          <strong>{formatMinorUnitsAsEuro(data.recommended_minor)}</strong>
        </div>
        <div>
          <span>Eingezahlt</span>
          <strong>{formatMinorUnitsAsEuro(data.deposited_minor)}</strong>
        </div>
        <div>
          <span>Noch offen</span>
          <strong>{formatMinorUnitsAsEuro(data.open_minor)}</strong>
        </div>
        <div className="reserve-metrics__balance">
          <span>Bestätigter Gesamtstand</span>
          <strong>{formatMinorUnitsAsEuro(data.total_balance_minor)}</strong>
        </div>
      </div>

      <div className="reserve-toolbar">
        <button
          type="button"
          className="btn-primary"
          disabled={pending || data.open_minor <= 0}
          onClick={openBulkConfirmation}
        >
          Alle offenen Sollwerte einzahlen
        </button>
        <span>Die Monats-Empfehlung bleibt danach als Referenz stehen.</span>
      </div>
      {feedback && (
        <div className="status-success" role="status">
          {feedback}
        </div>
      )}
      {mutationError && (
        <div className="status-error" role="alert">
          {errorMessage(mutationError)}
        </div>
      )}
      {localError && (
        <div className="status-error" role="alert">
          {localError}
        </div>
      )}

      {(["contracts", "goals"] as const).map((group) => (
        <section className="reserve-group" key={group}>
          <header>
            <div>
              <span className="eyebrow">
                {group === "contracts" ? "Fixkosten" : "Sparen & Wünsche"}
              </span>
              <h3>{group === "contracts" ? "Fixkosten-Rücklagen" : "Spartöpfe & Wünsche"}</h3>
            </div>
            <span>{groups[group].length} Töpfe</span>
          </header>
          {groups[group].length === 0 ? (
            <p className="placeholder-note">
              Hier gibt es für diesen Monat keine aktiven Rücklagentöpfe.
            </p>
          ) : (
            groups[group].map((pot) => (
              <ReserveRow
                key={pot.id}
                pot={pot}
                disabled={pending}
                onDeposit={() =>
                  setMoneyDialog({
                    kind: "deposit",
                    pot,
                    amount: ((pot.open_minor || pot.recommended_minor) / 100)
                      .toFixed(2)
                      .replace(".", ","),
                  })
                }
                onWithdraw={() => setMoneyDialog({ kind: "withdraw", pot, amount: "" })}
                onSkip={() => skipMutation.mutate(pot.id)}
                onUndoSkip={() => undoSkipMutation.mutate(pot.id)}
              />
            ))
          )}
        </section>
      ))}

      {groups.free && (
        <section className="reserve-group reserve-group--free">
          <header>
            <div>
              <span className="eyebrow">Ohne feste Verpflichtung</span>
              <h3>Freie Rücklage</h3>
            </div>
            <strong>{formatMinorUnitsAsEuro(groups.free.balance_minor)}</strong>
          </header>
          <p>
            Geld bleibt hier sichtbar, bis du es bewusst einem Zweck zuordnest. Es wird nie
            automatisch auf Töpfe verteilt.
          </p>
          <div className="reserve-free-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setMoneyDialog({ kind: "deposit", pot: groups.free!, amount: "" })}
            >
              Einzahlung erfassen
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={groups.free.balance_minor <= 0}
              onClick={() => {
                setDistributionTotal(null);
                setDistribution({});
                setLargeAmount("");
              }}
            >
              Freien Stand verteilen
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={groups.free.balance_minor <= 0}
              onClick={() => setMoneyDialog({ kind: "withdraw", pot: groups.free!, amount: "" })}
            >
              Entnahme
            </button>
          </div>
        </section>
      )}

      <section className="reserve-distribution-card">
        <div>
          <span className="eyebrow">Größere Summe</span>
          <h3>Betrag vor der Buchung verteilen</h3>
          <p>
            Zum Beispiel 20.000 €: Erst die bestätigte Aufteilung schreibt Bewegungen. Der freie
            Rest bleibt jederzeit sichtbar.
          </p>
        </div>
        <div className="reserve-distribution-card__input">
          <label htmlFor="reserve-large-amount">Gesamtbetrag</label>
          <input
            id="reserve-large-amount"
            className="field-input"
            inputMode="decimal"
            placeholder="20.000,00"
            value={largeAmount}
            onChange={(event) => setLargeAmount(event.target.value)}
          />
          <button type="button" className="btn-primary" onClick={openLargeAmountDialog}>
            Betrag zuordnen
          </button>
        </div>
      </section>

      {bulkDraft && (
        <Modal title="Offene Sollwerte bestätigen" onClose={() => setBulkDraft(null)} size="wide">
          <div className="reserve-dialog-form" data-testid="reserve-bulk-dialog">
            <p>
              Prüfe die vorausgewählten Töpfe und Beträge. Erst die verbindliche Bestätigung
              schreibt eine gemeinsame Einzahlung.
            </p>
            <div className="reserve-bulk-list">
              {data.pots
                .filter((pot) => bulkDraft[pot.id] !== undefined)
                .map((pot) => {
                  const draft = bulkDraft[pot.id]!;
                  const amount = draft.selected ? (parseAmountToMinorUnits(draft.amount) ?? 0) : 0;
                  return (
                    <div className="reserve-bulk-row" key={pot.id}>
                      <label className="reserve-bulk-row__select">
                        <input
                          type="checkbox"
                          checked={draft.selected}
                          onChange={(event) =>
                            setBulkDraft({
                              ...bulkDraft,
                              [pot.id]: { ...draft, selected: event.target.checked },
                            })
                          }
                        />
                        <span>
                          <strong>{pot.name}</strong>
                          <small>{formatMinorUnitsAsEuro(pot.open_minor)} offen</small>
                        </span>
                      </label>
                      <label>
                        <span>Betrag</span>
                        <input
                          className="field-input"
                          inputMode="decimal"
                          disabled={!draft.selected}
                          value={draft.amount}
                          onChange={(event) =>
                            setBulkDraft({
                              ...bulkDraft,
                              [pot.id]: { ...draft, amount: event.target.value },
                            })
                          }
                          aria-label={`Einzahlungsbetrag für ${pot.name}`}
                        />
                      </label>
                      <div>
                        <span>Neuer Topfstand</span>
                        <strong>{formatMinorUnitsAsEuro(pot.balance_minor + amount)}</strong>
                      </div>
                    </div>
                  );
                })}
            </div>
            <div className="reserve-bulk-total" aria-live="polite">
              <span>Gemeinsame Einzahlung</span>
              <strong>{formatMinorUnitsAsEuro(bulkTotal)}</strong>
            </div>
            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={() => setBulkDraft(null)}>
                Abbrechen
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={pending || bulkTotal <= 0}
                onClick={submitBulkConfirmation}
              >
                Verbindlich bestätigen
              </button>
            </div>
          </div>
        </Modal>
      )}

      {moneyDialog && (
        <Modal
          title={
            moneyDialog.kind === "deposit"
              ? `Einzahlung · ${moneyDialog.pot.name}`
              : `Entnahme · ${moneyDialog.pot.name}`
          }
          onClose={() => setMoneyDialog(null)}
        >
          <div className="reserve-dialog-form">
            <p>
              {moneyDialog.kind === "deposit"
                ? "Nur der bestätigte Betrag erhöht den echten Topfstand."
                : `Verfügbar: ${formatMinorUnitsAsEuro(moneyDialog.pot.balance_minor)}`}
            </p>
            <label className="field-label" htmlFor="reserve-dialog-amount">
              Betrag
            </label>
            <input
              autoFocus
              id="reserve-dialog-amount"
              className="field-input"
              inputMode="decimal"
              value={moneyDialog.amount}
              onChange={(event) => setMoneyDialog({ ...moneyDialog, amount: event.target.value })}
            />
            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={() => setMoneyDialog(null)}>
                Abbrechen
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={pending || !parseAmountToMinorUnits(moneyDialog.amount)}
                onClick={submitMoneyDialog}
              >
                Verbindlich bestätigen
              </button>
            </div>
          </div>
        </Modal>
      )}

      {distribution !== null && groups.free && (
        <Modal
          title={isLargeDistribution ? "Betrag explizit verteilen" : "Freie Rücklage verteilen"}
          onClose={() => {
            setDistribution(null);
            setDistributionTotal(null);
          }}
          size="wide"
        >
          <div className="reserve-dialog-form">
            <p>
              {isLargeDistribution && distributionTotal
                ? `Gesamtbetrag: ${formatMinorUnitsAsEuro(distributionTotal)}. Der Betrag bleibt zun\u00e4chst vollst\u00e4ndig als freier Rest sichtbar. Ordne ihn selbst zu oder fordere ausdr\u00fccklich einen Vorschlag an.`
                : `Frei verfügbar: ${formatMinorUnitsAsEuro(groups.free.balance_minor)}. Trage ein, wie viel davon du welchem Zweck zuordnest.`}
            </p>
            {isLargeDistribution && (
              <div className="reserve-suggestion-action">
                <div>
                  <strong>Optionale Hilfe</strong>
                  <span>Der Vorschlag orientiert sich an aktuell offenen Sollwerten.</span>
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={suggestionPending || pending}
                  onClick={() => void applyDistributionSuggestion()}
                >
                  {suggestionPending
                    ? "Vorschlag wird berechnet\u2026"
                    : "Nach offenen/f\u00e4lligen T\u00f6pfen vorschlagen"}
                </button>
              </div>
            )}
            {data.pots
              .filter((pot) => isLargeDistribution || pot.kind !== "free")
              .map((pot) => (
                <label className="reserve-allocation-row" key={pot.id}>
                  <span>
                    {pot.name}
                    <small>
                      {pot.kind === "free"
                        ? "Noch nicht zweckgebunden / freier Rest"
                        : `${formatMinorUnitsAsEuro(pot.open_minor)} aktuell offen`}
                    </small>
                  </span>
                  <input
                    className="field-input"
                    inputMode="decimal"
                    value={distribution[pot.id] ?? ""}
                    onChange={(event) =>
                      setDistribution({ ...distribution, [pot.id]: event.target.value })
                    }
                    aria-label={`Betrag für ${pot.name}`}
                  />
                </label>
              ))}
            <div className={distributionIsValid ? "status-success" : "status-warning"}>
              {isLargeDistribution && distributionTotal
                ? `${formatMinorUnitsAsEuro(distributionAssigned)} von ${formatMinorUnitsAsEuro(distributionTotal)} zugeordnet.`
                : `${formatMinorUnitsAsEuro(distributionAssigned)} von ${formatMinorUnitsAsEuro(groups.free.balance_minor)} freier Rücklage werden verteilt.`}
            </div>
            <div className="modal-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setDistribution(null);
                  setDistributionTotal(null);
                }}
              >
                Abbrechen
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={pending || !distributionIsValid}
                onClick={isLargeDistribution ? submitDistribution : submitReallocation}
              >
                Verteilung bestätigen
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
