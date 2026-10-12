import { Fragment, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  createAssetOrDebt,
  deleteAssetOrDebt,
  getNetWorthReport,
  listAssetValuations,
  listAssetsAndDebts,
  updateAssetOrDebt,
} from "../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import type {
  AssetClass,
  AssetDebt,
  AssetDebtUpdateInput,
  AssetType,
  AssetValuation,
  NetWorthReportResponse,
} from "../lib/types";

const ASSET_CLASS_LABELS: Record<AssetClass, string> = {
  real_estate: "Immobilie",
  vehicle: "Fahrzeug",
  depot: "Depot",
  cash: "Bargeld / Konto",
  loan: "Kredit",
  credit: "Kreditkarte",
  other: "Sonstiges",
};

const CLASS_OPTIONS: Record<AssetType, AssetClass[]> = {
  asset: ["real_estate", "vehicle", "depot", "cash", "other"],
  debt: ["loan", "credit", "other"],
};

function amountDraft(value: number): string {
  return (value / 100).toFixed(2).replace(".", ",");
}

function interestDraft(value: number): string {
  return String(value).replace(".", ",");
}

function handleCellKeyDown(event: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement>): void {
  if (event.key === "Enter") {
    event.preventDefault();
    event.currentTarget.blur();
  }
  if (event.key === "Escape") {
    event.preventDefault();
    event.currentTarget.value = event.currentTarget.dataset.original ?? "";
    event.currentTarget.blur();
  }
}

function ValuationHistory({ itemId, columnCount }: { itemId: string; columnCount: number }) {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const {
    data: valuations,
    isLoading,
    error,
  } = useQuery<AssetValuation[]>({
    queryKey: ["asset-valuations", householdId, itemId],
    queryFn: () => listAssetValuations(token!, householdId, itemId),
    enabled: Boolean(token && householdId),
  });

  return (
    <tr className="txn-tr asset-history-row">
      <td className="txn-td" colSpan={columnCount}>
        <div className="asset-history-panel">
          <h3 className="card-heading">Wertverlauf</h3>
          {isLoading && <p className="metric-hint">Wertverlauf wird geladen …</p>}
          {error && (
            <p className="status-error" role="alert">
              {error instanceof Error ? error.message : "Wertverlauf konnte nicht geladen werden."}
            </p>
          )}
          {!isLoading && !error && (valuations?.length ?? 0) === 0 && (
            <p className="metric-hint">Noch keine Bewertung erfasst.</p>
          )}
          {valuations && valuations.length > 0 && (
            <ul className="asset-history-list">
              {valuations.map((valuation) => (
                <li key={valuation.id}>
                  <span>{valuation.valued_on}</span>
                  <span>{formatMinorUnitsAsEuro(valuation.value_minor)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </td>
    </tr>
  );
}

interface AssetsDebtsViewProps {
  type: AssetType;
  title: string;
  description: string;
}

function AssetsDebtsView({ type, title, description }: AssetsDebtsViewProps) {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const addNameRef = useRef<HTMLInputElement>(null);

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";
  const isDebt = type === "debt";
  const rowLabel = isDebt ? "Schuld" : "Vermögenswert";
  const newRowLabel = isDebt ? "Neue Schuld" : "Neuer Vermögenswert";
  const formId = isDebt ? "new-debt-form" : "new-asset-form";

  const [expandedItemId, setExpandedItemId] = useState<string | null>(null);
  const [deleteConfirmationId, setDeleteConfirmationId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [assetClass, setAssetClass] = useState<AssetClass>(isDebt ? "loan" : "cash");
  const [valueEuro, setValueEuro] = useState("");
  const [interestRate, setInterestRate] = useState("");
  const [monthlyPaymentEuro, setMonthlyPaymentEuro] = useState("");
  const [addValidationError, setAddValidationError] = useState("");
  const [rowValidationErrors, setRowValidationErrors] = useState<Record<string, string>>({});

  const {
    data: items,
    isLoading,
    isError,
  } = useQuery<AssetDebt[]>({
    queryKey: ["assets", householdId],
    queryFn: () => listAssetsAndDebts(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const { data: netWorth } = useQuery<NetWorthReportResponse>({
    queryKey: ["net-worth", householdId],
    queryFn: () => getNetWorthReport(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const invalidateOverview = () => {
    void queryClient.invalidateQueries({ queryKey: ["assets", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["net-worth", householdId] });
  };

  const clearRowValidationError = (itemId: string) => {
    setRowValidationErrors((current) => {
      if (!(itemId in current)) return current;
      const next = { ...current };
      delete next[itemId];
      return next;
    });
  };

  const createMutation = useMutation({
    mutationFn: (input: {
      name: string;
      type: AssetType;
      asset_class: AssetClass;
      current_value_minor: number;
      interest_rate_percent?: number;
      monthly_payment_minor?: number;
    }) => createAssetOrDebt(token!, householdId, input),
    onSuccess: () => {
      invalidateOverview();
      setName("");
      setAssetClass(isDebt ? "loan" : "cash");
      setValueEuro("");
      setInterestRate("");
      setMonthlyPaymentEuro("");
      setAddValidationError("");
      addNameRef.current?.focus();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ itemId, patch }: { itemId: string; patch: AssetDebtUpdateInput }) =>
      updateAssetOrDebt(token!, householdId, itemId, patch),
    onMutate: ({ itemId }) => clearRowValidationError(itemId),
    onSuccess: (_item, { itemId, patch }) => {
      invalidateOverview();
      clearRowValidationError(itemId);
      if (Object.hasOwn(patch, "current_value_minor")) {
        void queryClient.invalidateQueries({
          queryKey: ["asset-valuations", householdId, itemId],
        });
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (itemId: string) => deleteAssetOrDebt(token!, householdId, itemId),
    onSuccess: (_result, itemId) => {
      invalidateOverview();
      setDeleteConfirmationId(null);
      setExpandedItemId((current) => (current === itemId ? null : current));
    },
  });

  const showRowValidationError = (itemId: string, message: string) => {
    setRowValidationErrors((current) => ({ ...current, [itemId]: message }));
  };

  const commitItemField = (
    item: AssetDebt,
    field: keyof AssetDebtUpdateInput,
    rawValue: string,
  ) => {
    let value: string | number | null = rawValue;

    if (field === "name") {
      value = rawValue.trim();
      if (!value) {
        showRowValidationError(item.id, "Name fehlt");
        return;
      }
    } else if (field === "asset_class") {
      value = rawValue as AssetClass;
    } else if (field === "current_value_minor" || field === "monthly_payment_minor") {
      value = parseAmountToMinorUnits(rawValue);
      if (value == null || value < 0) {
        showRowValidationError(
          item.id,
          field === "current_value_minor" ? "Wert ungültig" : "Rate ungültig",
        );
        return;
      }
    } else if (field === "interest_rate_percent") {
      value = Number(rawValue.replace(",", "."));
      if (!rawValue.trim() || !Number.isFinite(value) || value < 0) {
        showRowValidationError(item.id, "Zinssatz ungültig");
        return;
      }
    }

    const previous = item[field as keyof AssetDebt];
    if (value === previous || (value == null && previous == null)) {
      clearRowValidationError(item.id);
      return;
    }

    updateMutation.mutate({ itemId: item.id, patch: { [field]: value } });
  };

  const handleCreate = (event: React.FormEvent) => {
    event.preventDefault();
    const normalizedName = name.trim();
    const valueMinor = parseAmountToMinorUnits(valueEuro);
    const normalizedInterest = interestRate.trim() ? Number(interestRate.replace(",", ".")) : 0;
    const monthlyPaymentMinor = monthlyPaymentEuro.trim()
      ? parseAmountToMinorUnits(monthlyPaymentEuro)
      : 0;

    if (!normalizedName) {
      setAddValidationError("Bezeichnung fehlt");
      addNameRef.current?.focus();
      return;
    }
    if (valueMinor == null || valueMinor < 0) {
      setAddValidationError(isDebt ? "Restschuld ungültig" : "Wert ungültig");
      return;
    }
    if (isDebt && (!Number.isFinite(normalizedInterest) || normalizedInterest < 0)) {
      setAddValidationError("Zinssatz ungültig");
      return;
    }
    if (isDebt && (monthlyPaymentMinor == null || monthlyPaymentMinor < 0)) {
      setAddValidationError("Rate ungültig");
      return;
    }

    setAddValidationError("");
    createMutation.mutate({
      name: normalizedName,
      type,
      asset_class: assetClass,
      current_value_minor: valueMinor,
      ...(isDebt
        ? {
            interest_rate_percent: normalizedInterest,
            monthly_payment_minor: monthlyPaymentMinor ?? 0,
          }
        : {}),
    });
  };

  const filtered = (items ?? []).filter((item) => item.type === type);
  const columnCount = isDebt ? 7 : 4;
  const total = isDebt ? netWorth?.total_debts_minor : netWorth?.total_assets_minor;
  const byClass = isDebt ? netWorth?.debts_by_class : netWorth?.assets_by_class;

  return (
    <section className="page assets-page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">{title}</h1>
          <p className="placeholder-note">{description}</p>
        </div>
      </div>

      <div className="metric-grid" aria-label={`Kennzahlen für ${title}`}>
        <div className="card metric-card">
          <span className="metric-label">{isDebt ? "Gesamtschulden" : "Gesamtvermögen"}</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(total ?? 0)}</span>
          <span className="metric-hint">Inklusive Konten laut Nettovermögensbericht</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Nettovermögen</span>
          <span
            className={`metric-value ${(netWorth?.net_worth_minor ?? 0) < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
          >
            {formatMinorUnitsAsEuro(netWorth?.net_worth_minor ?? 0)}
          </span>
          <span className="metric-hint">Vermögen minus Schulden</span>
        </div>
      </div>

      {byClass && Object.keys(byClass).length > 0 && (
        <section className="card asset-class-summary" aria-labelledby={`${type}-class-title`}>
          <h2 id={`${type}-class-title`} className="card-heading">
            Aufteilung nach Klasse
          </h2>
          <div className="asset-class-summary__items">
            {Object.entries(byClass).map(([assetClassKey, minor]) => (
              <span key={assetClassKey} className="status-badge status-badge--neutral">
                {ASSET_CLASS_LABELS[assetClassKey as AssetClass] ?? assetClassKey}:{" "}
                {formatMinorUnitsAsEuro(minor)}
              </span>
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby={`${type}-ledger-title`}>
        <div className="asset-ledger-toolbar">
          <div>
            <h2 id={`${type}-ledger-title`} className="goal-section-title">
              {isDebt ? "Schulden-Ledger" : "Vermögens-Ledger"}
            </h2>
            <p className="goal-section-hint">
              Zelle wählen, ändern und mit Enter oder Klick außerhalb speichern.
            </p>
          </div>
          <span className="goal-keyboard-hint">Tab · Enter · Esc</span>
        </div>
        <p className="asset-scroll-hint">Tabelle seitlich scrollen für weitere Felder →</p>

        <form id={formId} onSubmit={handleCreate} />
        <div className="txn-table-wrapper asset-ledger-wrap">
          <table className={`txn-table asset-ledger asset-ledger--${type}`}>
            <thead>
              <tr>
                <th className="txn-th">Bezeichnung</th>
                <th className="txn-th">Klasse</th>
                {isDebt && <th className="txn-th">Zinssatz</th>}
                {isDebt && <th className="txn-th">Rate/Monat</th>}
                {isDebt && <th className="txn-th">Tilgung (Monate)</th>}
                <th className="txn-th">{isDebt ? "Restschuld" : "Aktueller Wert"}</th>
                <th className="txn-th">Wertverlauf / Aktion</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td className="asset-table-message" colSpan={columnCount}>
                    Daten werden geladen …
                  </td>
                </tr>
              )}
              {isError && (
                <tr>
                  <td
                    className="asset-table-message asset-table-message--error"
                    colSpan={columnCount}
                  >
                    Daten konnten nicht geladen werden.
                  </td>
                </tr>
              )}
              {!isLoading && !isError && filtered.length === 0 && (
                <tr>
                  <td className="asset-table-message" colSpan={columnCount}>
                    Noch keine Einträge — der erste Eintrag kann direkt unten erfasst werden.
                  </td>
                </tr>
              )}
              {filtered.map((item) => {
                const rowIsSaving =
                  updateMutation.isPending && updateMutation.variables?.itemId === item.id;
                const rowHasError =
                  (updateMutation.isError && updateMutation.variables?.itemId === item.id) ||
                  (deleteMutation.isError && deleteMutation.variables === item.id);
                const rowWasSaved =
                  updateMutation.isSuccess && updateMutation.variables?.itemId === item.id;
                const rowValidationError = rowValidationErrors[item.id];
                const deleteIsPending =
                  deleteMutation.isPending && deleteMutation.variables === item.id;
                const fieldProps = (original: string) => ({
                  "data-original": original,
                  onKeyDown: handleCellKeyDown,
                });

                return (
                  <Fragment key={item.id}>
                    <tr className="txn-tr asset-edit-row">
                      <td className="txn-td">
                        <input
                          className="asset-cell-input asset-cell-input--strong"
                          aria-label={`${item.name}: Bezeichnung`}
                          title={item.name}
                          defaultValue={item.name}
                          onBlur={(event) =>
                            commitItemField(item, "name", event.currentTarget.value)
                          }
                          {...fieldProps(item.name)}
                        />
                      </td>
                      <td className="txn-td">
                        <select
                          className="asset-cell-input"
                          aria-label={`${item.name}: Klasse`}
                          defaultValue={item.asset_class}
                          onBlur={(event) =>
                            commitItemField(item, "asset_class", event.currentTarget.value)
                          }
                          {...fieldProps(item.asset_class)}
                        >
                          {!CLASS_OPTIONS[type].includes(item.asset_class) && (
                            <option value={item.asset_class}>
                              {ASSET_CLASS_LABELS[item.asset_class]} (Bestand)
                            </option>
                          )}
                          {CLASS_OPTIONS[type].map((classValue) => (
                            <option key={classValue} value={classValue}>
                              {ASSET_CLASS_LABELS[classValue]}
                            </option>
                          ))}
                        </select>
                      </td>
                      {isDebt && (
                        <td className="txn-td">
                          <input
                            inputMode="decimal"
                            className="asset-cell-input asset-cell-input--mono"
                            aria-label={`${item.name}: Zinssatz`}
                            defaultValue={interestDraft(item.interest_rate_percent)}
                            onBlur={(event) =>
                              commitItemField(
                                item,
                                "interest_rate_percent",
                                event.currentTarget.value,
                              )
                            }
                            {...fieldProps(interestDraft(item.interest_rate_percent))}
                          />
                        </td>
                      )}
                      {isDebt && (
                        <td className="txn-td">
                          <input
                            inputMode="decimal"
                            className="asset-cell-input asset-cell-input--mono"
                            aria-label={`${item.name}: Rate pro Monat`}
                            defaultValue={amountDraft(item.monthly_payment_minor)}
                            onBlur={(event) =>
                              commitItemField(
                                item,
                                "monthly_payment_minor",
                                event.currentTarget.value,
                              )
                            }
                            {...fieldProps(amountDraft(item.monthly_payment_minor))}
                          />
                        </td>
                      )}
                      {isDebt && (
                        <td className="txn-td asset-read-cell">
                          <span className="asset-derived-value">
                            {item.payoff_months_projected ?? "—"}
                          </span>
                        </td>
                      )}
                      <td className="txn-td">
                        <input
                          inputMode="decimal"
                          className="asset-cell-input asset-cell-input--mono"
                          aria-label={`${item.name}: ${isDebt ? "Restschuld" : "Aktueller Wert"}`}
                          defaultValue={amountDraft(item.current_value_minor)}
                          onBlur={(event) =>
                            commitItemField(item, "current_value_minor", event.currentTarget.value)
                          }
                          {...fieldProps(amountDraft(item.current_value_minor))}
                        />
                      </td>
                      <td className="txn-td asset-action-cell">
                        <div className="asset-row-actions">
                          <span
                            className={`asset-save-state ${rowValidationError || rowHasError ? "asset-save-state--error" : ""}`}
                            role={rowValidationError || rowHasError ? "alert" : "status"}
                          >
                            {rowValidationError ??
                              (rowIsSaving || deleteIsPending
                                ? rowIsSaving
                                  ? "Speichert …"
                                  : "Löscht …"
                                : rowHasError
                                  ? "Aktion fehlgeschlagen"
                                  : rowWasSaved
                                    ? "Gespeichert"
                                    : "")}
                          </span>
                          <button
                            type="button"
                            className="asset-row-button"
                            aria-label={`Wertverlauf für ${item.name} ${expandedItemId === item.id ? "ausblenden" : "anzeigen"}`}
                            aria-expanded={expandedItemId === item.id}
                            onClick={() =>
                              setExpandedItemId((current) => (current === item.id ? null : item.id))
                            }
                          >
                            {expandedItemId === item.id ? "Ausblenden" : "Verlauf"}
                          </button>
                          {deleteConfirmationId === item.id ? (
                            <span className="asset-delete-confirmation">
                              <button
                                type="button"
                                className="asset-delete-confirm"
                                aria-label={`${rowLabel} ${item.name} wirklich löschen`}
                                disabled={deleteIsPending}
                                onClick={() => deleteMutation.mutate(item.id)}
                              >
                                Wirklich löschen?
                              </button>
                              <button
                                type="button"
                                className="asset-delete-cancel"
                                aria-label={`Löschen von ${item.name} abbrechen`}
                                onClick={() => setDeleteConfirmationId(null)}
                              >
                                Abbrechen
                              </button>
                            </span>
                          ) : (
                            <button
                              type="button"
                              className="asset-delete-trigger"
                              aria-label={`${rowLabel} ${item.name} löschen`}
                              onClick={() => setDeleteConfirmationId(item.id)}
                            >
                              Löschen
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {expandedItemId === item.id && (
                      <ValuationHistory itemId={item.id} columnCount={columnCount} />
                    )}
                  </Fragment>
                );
              })}
              <tr className="asset-add-row">
                <td className="txn-td">
                  <input
                    ref={addNameRef}
                    form={formId}
                    className="asset-cell-input asset-cell-input--strong"
                    aria-label={`${newRowLabel}: Bezeichnung`}
                    placeholder={isDebt ? "z. B. Autokredit" : "z. B. Tagesgeld"}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </td>
                <td className="txn-td">
                  <select
                    form={formId}
                    className="asset-cell-input"
                    aria-label={`${newRowLabel}: Klasse`}
                    value={assetClass}
                    onChange={(event) => setAssetClass(event.target.value as AssetClass)}
                  >
                    {CLASS_OPTIONS[type].map((classValue) => (
                      <option key={classValue} value={classValue}>
                        {ASSET_CLASS_LABELS[classValue]}
                      </option>
                    ))}
                  </select>
                </td>
                {isDebt && (
                  <td className="txn-td">
                    <input
                      form={formId}
                      inputMode="decimal"
                      className="asset-cell-input asset-cell-input--mono"
                      aria-label={`${newRowLabel}: Zinssatz`}
                      placeholder="0,00"
                      value={interestRate}
                      onChange={(event) => setInterestRate(event.target.value)}
                    />
                  </td>
                )}
                {isDebt && (
                  <td className="txn-td">
                    <input
                      form={formId}
                      inputMode="decimal"
                      className="asset-cell-input asset-cell-input--mono"
                      aria-label={`${newRowLabel}: Rate pro Monat`}
                      placeholder="0,00"
                      value={monthlyPaymentEuro}
                      onChange={(event) => setMonthlyPaymentEuro(event.target.value)}
                    />
                  </td>
                )}
                {isDebt && <td className="txn-td asset-add-row__hint">Wird berechnet</td>}
                <td className="txn-td">
                  <input
                    form={formId}
                    inputMode="decimal"
                    className="asset-cell-input asset-cell-input--mono"
                    aria-label={`${newRowLabel}: ${isDebt ? "Restschuld" : "Aktueller Wert"}`}
                    placeholder="0,00"
                    value={valueEuro}
                    onChange={(event) => setValueEuro(event.target.value)}
                  />
                </td>
                <td className="txn-td asset-action-cell">
                  <div className="asset-row-actions">
                    <span
                      className={`asset-save-state ${addValidationError || createMutation.isError ? "asset-save-state--error" : ""}`}
                      role={addValidationError || createMutation.isError ? "alert" : "status"}
                    >
                      {createMutation.isPending
                        ? "Wird angelegt …"
                        : addValidationError ||
                          (createMutation.isError ? "Anlegen fehlgeschlagen" : "Neue Zeile")}
                    </span>
                    <button
                      form={formId}
                      type="submit"
                      className="btn-primary asset-add-button"
                      disabled={createMutation.isPending}
                    >
                      + Hinzufügen
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

export function AssetsPage() {
  return (
    <AssetsDebtsView
      type="asset"
      title="Vermögen"
      description="Immobilien, Fahrzeuge, Depots und weitere Vermögenswerte."
    />
  );
}

export function DebtsPage() {
  return (
    <AssetsDebtsView
      type="debt"
      title="Schulden"
      description="Kredite und Verbindlichkeiten mit Tilgungsprognose."
    />
  );
}
