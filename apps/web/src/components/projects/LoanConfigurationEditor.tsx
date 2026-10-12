import { useMemo, useState } from "react";
import {
  useCreateFinancingConfiguration,
  useFinancingDrawPreview,
  useUpdateFinancingConfiguration,
} from "../../hooks/useProjects";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import { calculateLoanPreview } from "../../lib/loanPreview";
import type {
  AmortizationMode,
  DebtPlanConfigurationInput,
  FinancingSource,
  FinancingSourceStatus,
  FinancingSourceType,
} from "../../lib/types";

type DrawMode = "immediate" | "cost_plan" | "manual";

interface PeriodDraft {
  starts: string;
  ends: string;
  rate: string;
  kind: "contracted" | "assumed";
  label: string;
}

interface DrawDraft {
  month: string;
  amount: string;
}

interface PeriodFieldErrors {
  starts?: string;
  ends?: string;
  rate?: string;
}

type MappedRatePeriod = DebtPlanConfigurationInput["rate_periods"][number];

function validateRatePeriods(periods: PeriodDraft[]): {
  mapped: MappedRatePeriod[] | null;
  errors: Record<number, PeriodFieldErrors>;
} {
  const errors: Record<number, PeriodFieldErrors> = {};
  const mapped: MappedRatePeriod[] = [];

  periods.forEach((period, index) => {
    const periodErrors: PeriodFieldErrors = {};
    const rate = parsePercent(period.rate);
    const starts = period.starts.trim() === "" ? Number.NaN : Number(period.starts);
    const ends = period.ends.trim() === "" ? undefined : Number(period.ends);
    if (rate === null || rate < 0 || rate > 10_000) {
      periodErrors.rate = `Zinsphase ${index + 1}: Bitte einen gültigen Sollzins eintragen.`;
    }
    if (!Number.isInteger(starts) || starts < 0) {
      periodErrors.starts = `Zinsphase ${index + 1}: Bitte einen gültigen Startmonat eintragen.`;
    }
    if (ends !== undefined && (!Number.isInteger(ends) || ends < starts)) {
      periodErrors.ends = `Zinsphase ${index + 1}: Bitte einen gültigen Endmonat eintragen.`;
    }
    if (Object.keys(periodErrors).length > 0) {
      errors[index] = periodErrors;
      return;
    }
    mapped.push({
      starts_after_month: starts,
      ends_after_month: ends,
      nominal_rate_bps_annual: rate as number,
      rate_kind: period.kind,
      label: period.label || undefined,
    });
  });

  if (Object.keys(errors).length > 0) return { mapped: null, errors };
  if (mapped[0]?.starts_after_month !== 0) {
    errors[0] = { starts: "Die erste Zinsphase muss in Monat 0 beginnen." };
  }
  mapped.forEach((period, index) => {
    if (index === mapped.length - 1) return;
    if (period.ends_after_month === undefined || period.ends_after_month === null) {
      errors[index] = { ends: "Nur die letzte Zinsphase darf ohne Endmonat sein." };
      return;
    }
    const next = mapped[index + 1];
    if (next && next.starts_after_month !== period.ends_after_month + 1) {
      errors[index + 1] = {
        starts: `Zinsphase ${index + 2} muss direkt in Monat ${period.ends_after_month + 1} beginnen.`,
      };
    }
  });
  return Object.keys(errors).length > 0 ? { mapped: null, errors } : { mapped, errors };
}

const STATUS_OPTIONS: { value: FinancingSourceStatus; label: string }[] = [
  { value: "idea", label: "Idee" },
  { value: "requested", label: "Beantragt" },
  { value: "likely", label: "Wahrscheinlich" },
  { value: "approved", label: "Genehmigt" },
  { value: "contracted", label: "Vertraglich fixiert" },
  { value: "received", label: "Erhalten" },
  { value: "rejected", label: "Abgelehnt" },
];

function percentInput(bps: number | null | undefined): string {
  return bps === null || bps === undefined ? "" : String(bps / 100).replace(".", ",");
}

function euroInput(minor: number | null | undefined): string {
  return minor === null || minor === undefined ? "" : String(minor / 100).replace(".", ",");
}

function parsePercent(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value.replace(",", "."));
  return Number.isFinite(parsed) ? Math.round(parsed * 100) : null;
}

function defaultsFromSource(source: FinancingSource | null): {
  periods: PeriodDraft[];
  mode: AmortizationMode;
  repayment: string;
  payment: string;
  term: string;
  freeMonths: string;
} {
  const plan = source?.debt_plan;
  const terms = (source?.funding_loan_terms ?? {}) as Record<string, unknown>;
  const catalogRate =
    typeof terms.nominal_rate_bps_annual === "number" ? terms.nominal_rate_bps_annual : null;
  const catalogTerm = typeof terms.max_term_months === "number" ? terms.max_term_months : null;
  const catalogFree =
    typeof terms.max_repayment_free_months === "number" ? terms.max_repayment_free_months : null;
  const storedPeriods = plan?.rate_periods ?? [];
  const periods: PeriodDraft[] = storedPeriods.length
    ? storedPeriods.map((period) => ({
        starts: String(period.starts_after_month),
        ends:
          period.ends_after_month === null || period.ends_after_month === undefined
            ? ""
            : String(period.ends_after_month),
        rate: percentInput(period.nominal_rate_bps_annual),
        kind: period.rate_kind ?? "contracted",
        label: period.label ?? "",
      }))
    : [
        {
          starts: "0",
          ends: "119",
          rate: percentInput(catalogRate),
          kind:
            source?.source_type === "subsidized_loan"
              ? ("assumed" as const)
              : ("contracted" as const),
          label: "Sollzinsbindung",
        },
        { starts: "120", ends: "", rate: "4,5", kind: "assumed", label: "Anschlusszins" },
      ];
  return {
    periods,
    mode: plan?.amortization_mode ?? (catalogTerm ? "full_repayment_term" : "initial_repayment"),
    repayment: percentInput(plan?.initial_repayment_rate_bps ?? 200),
    payment: euroInput(plan?.fixed_payment_minor),
    term: String(plan?.target_term_months ?? catalogTerm ?? 240),
    freeMonths: String(plan?.repayment_free_months ?? catalogFree ?? 0),
  };
}

export function LoanConfigurationEditor({
  projectId,
  scenarioId,
  source,
  sourceType = "commercial_loan",
  onCancel,
  onSaved,
}: {
  projectId: string;
  scenarioId: string;
  source: FinancingSource | null;
  sourceType?: Extract<FinancingSourceType, "commercial_loan" | "subsidized_loan">;
  onCancel: () => void;
  onSaved: (sourceId: string) => void;
}) {
  const defaults = useMemo(() => defaultsFromSource(source), [source]);
  const plan = source?.debt_plan;
  const createMutation = useCreateFinancingConfiguration();
  const updateMutation = useUpdateFinancingConfiguration();
  const [name, setName] = useState(source?.name ?? "Neues Bankdarlehen");
  const [status, setStatus] = useState<FinancingSourceStatus>(source?.status ?? "idea");
  const [amount, setAmount] = useState(euroInput(source?.planned_amount_minor));
  const [startsOn, setStartsOn] = useState(
    plan?.starts_on ?? source?.available_on ?? new Date().toISOString().slice(0, 10),
  );
  const [mode, setMode] = useState<AmortizationMode>(defaults.mode);
  const [fixedPayment, setFixedPayment] = useState(defaults.payment);
  const [initialRepayment, setInitialRepayment] = useState(defaults.repayment);
  const [targetTerm, setTargetTerm] = useState(defaults.term);
  const [repaymentFree, setRepaymentFree] = useState(defaults.freeMonths);
  const [drawMode, setDrawMode] = useState<DrawMode>(plan?.draw_mode ?? "immediate");
  const [periods, setPeriods] = useState<PeriodDraft[]>(defaults.periods);
  const [draws, setDraws] = useState<DrawDraft[]>(
    (plan?.draws ?? []).map((draw) => ({
      month: String(draw.occurs_after_month),
      amount: euroInput(draw.amount_minor),
    })),
  );
  const [showMore, setShowMore] = useState((plan?.rate_periods?.length ?? 0) > 2);
  const [error, setError] = useState<string | null>(null);
  const [periodErrors, setPeriodErrors] = useState<Record<number, PeriodFieldErrors>>({});

  const amountMinor = parseAmountToMinorUnits(amount) ?? 0;
  const firstRateBps = parsePercent(periods[0]?.rate ?? "");
  const firstBindingMonths = periods[0]?.ends ? Number(periods[0].ends) + 1 : 0;
  const followUpBps = parsePercent(periods[1]?.rate ?? "") ?? firstRateBps;
  const previewInput = useMemo(
    () => ({
      principal_limit_minor: Math.max(amountMinor, 1),
      starts_on: startsOn,
      draw_mode: drawMode,
      draws: [],
    }),
    [amountMinor, startsOn, drawMode],
  );
  const liveDrawPreview = useFinancingDrawPreview(
    projectId,
    scenarioId,
    previewInput,
    drawMode === "cost_plan" && amountMinor > 0 && Boolean(startsOn),
  );
  const persistedPreviewMatches =
    source?.planned_amount_minor === amountMinor &&
    plan?.starts_on === startsOn &&
    plan.draw_mode === drawMode;
  const autoPreview =
    liveDrawPreview.data ?? (persistedPreviewMatches ? source?.cost_draw_preview : undefined);
  const shownDraws =
    drawMode === "cost_plan"
      ? (autoPreview?.draws ?? []).map((draw) => ({
          month: String(draw.occurs_after_month),
          amount: euroInput(draw.amount_minor),
        }))
      : draws;
  const preview = useMemo(() => {
    if (amountMinor <= 0 || firstRateBps === null) return null;
    const repaymentBps = parsePercent(initialRepayment) ?? 0;
    return calculateLoanPreview({
      principalMinor: amountMinor,
      nominalRateBpsAnnual: firstRateBps,
      initialRepaymentRateBps: repaymentBps,
      repaymentFreeMonths: Number(repaymentFree) || 0,
      fixedRateMonths: firstBindingMonths || 600,
      followUpRateBpsAnnual: followUpBps ?? firstRateBps,
      targetTermMonths:
        mode === "full_repayment_term" ? Number(targetTerm) || undefined : undefined,
    });
  }, [
    amountMinor,
    firstRateBps,
    initialRepayment,
    repaymentFree,
    firstBindingMonths,
    followUpBps,
    mode,
    targetTerm,
  ]);

  const updatePeriod = (index: number, patch: Partial<PeriodDraft>) => {
    setPeriods((current) =>
      current.map((period, periodIndex) =>
        periodIndex === index ? { ...period, ...patch } : period,
      ),
    );
  };

  const setBindingMonths = (value: string) => {
    const months = Number(value);
    setPeriods((current) => {
      const next = [...current];
      const first = next[0] ?? {
        starts: "0",
        ends: "",
        rate: "",
        kind: "assumed" as const,
        label: "Sollzinsbindung",
      };
      next[0] = { ...first, ends: months > 0 ? String(months - 1) : "" };
      if (months > 0 && !next[1]) {
        next.push({
          starts: String(months),
          ends: "",
          rate: "4,5",
          kind: "assumed",
          label: "Anschlusszins",
        });
      } else if (months > 0 && next[1]) {
        next[1] = { ...next[1], starts: String(months) };
      }
      return next;
    });
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setPeriodErrors({});
    if (!name.trim()) return setError("Bitte einen Namen für den Kredit eintragen.");
    if (amountMinor <= 0) return setError("Der Darlehensrahmen muss größer als 0 € sein.");
    if (!startsOn) return setError("Bitte ein Startdatum auswählen.");
    if (periods.length === 0) return setError("Mindestens eine Zinsphase ist erforderlich.");

    const rateValidation = validateRatePeriods(periods);
    if (!rateValidation.mapped) {
      setPeriodErrors(rateValidation.errors);
      setShowMore(true);
      return;
    }
    const mappedPeriods = rateValidation.mapped;

    let mappedDraws: NonNullable<DebtPlanConfigurationInput["draws"]> = [];
    if (drawMode === "manual") {
      try {
        mappedDraws = draws.map((draw, index) => {
          const amountValue = parseAmountToMinorUnits(draw.amount) ?? 0;
          const month = Number(draw.month);
          if (amountValue <= 0 || !Number.isInteger(month) || month < 0) {
            throw new Error(`Tranche ${index + 1}: Monat und Betrag sind ungültig.`);
          }
          return { occurs_after_month: month, amount_minor: amountValue, status: "planned" };
        });
      } catch (drawError) {
        return setError(drawError instanceof Error ? drawError.message : "Abrufe sind ungültig.");
      }
      if (mappedDraws.reduce((sum, draw) => sum + draw.amount_minor, 0) > amountMinor) {
        return setError("Die Summe der Tranchen darf den Darlehensrahmen nicht überschreiten.");
      }
    }

    const freeMonths = Number(repaymentFree) || 0;
    const repaymentBps = parsePercent(initialRepayment);
    const fixedPaymentMinor = parseAmountToMinorUnits(fixedPayment);
    const termMonths = Number(targetTerm);
    if (mode === "fixed_payment" && (!fixedPaymentMinor || fixedPaymentMinor <= 0))
      return setError("Bitte eine gültige Monatsrate eintragen.");
    if (mode === "initial_repayment" && (repaymentBps === null || repaymentBps < 0))
      return setError("Bitte eine gültige Anfangstilgung eintragen.");
    if (mode === "full_repayment_term" && (!Number.isInteger(termMonths) || termMonths < 1))
      return setError("Bitte eine gültige Ziel-Laufzeit eintragen.");

    const debtPlan: DebtPlanConfigurationInput = {
      name: name.trim(),
      status: status === "contracted" || status === "received" ? "active" : "draft",
      principal_limit_minor: amountMinor,
      starts_on: startsOn,
      payment_interval_months: 1,
      amortization_mode: mode,
      fixed_payment_minor: mode === "fixed_payment" ? fixedPaymentMinor : undefined,
      initial_repayment_rate_bps: mode === "initial_repayment" ? repaymentBps : undefined,
      target_term_months: mode === "full_repayment_term" ? termMonths : undefined,
      repayment_free_months: freeMonths,
      draw_mode: drawMode,
      rate_periods: mappedPeriods,
      draws: mappedDraws,
      fees: (plan?.fees ?? []).map(({ id: _id, ...fee }) => fee),
      special_repayments: (plan?.special_repayments ?? []).map(
        ({ id: _id, ...repayment }) => repayment,
      ),
      version: plan?.version,
    };

    if (source) {
      updateMutation.mutate(
        {
          projectId,
          scenarioId,
          sourceId: source.id,
          version: source.version,
          input: {
            source: {
              name: name.trim(),
              status,
              planned_amount_minor: amountMinor,
              principal_limit_minor: amountMinor,
              available_on: startsOn,
            },
            debt_plan: debtPlan,
          },
        },
        { onSuccess: (result) => onSaved(result.source.id) },
      );
    } else {
      createMutation.mutate(
        {
          projectId,
          scenarioId,
          input: {
            source: {
              name: name.trim(),
              source_type: sourceType,
              status,
              planned_amount_minor: amountMinor,
              principal_limit_minor: amountMinor,
              available_on: startsOn,
            },
            debt_plan: debtPlan,
          },
        },
        { onSuccess: (result) => onSaved(result.source.id) },
      );
    }
  };

  const pending = createMutation.isPending || updateMutation.isPending;
  const mutationError = createMutation.error ?? updateMutation.error;

  return (
    <form className="loan-editor" onSubmit={submit}>
      <fieldset className="loan-editor__section">
        <legend>
          <span>1</span> Rahmen
        </legend>
        <div className="form-grid-3">
          <label className="form-field">
            <span className="form-label">Bezeichnung</span>
            <input
              className="form-input"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="form-field">
            <span className="form-label">Darlehensrahmen (€)</span>
            <input
              className="form-input form-input--mono"
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </label>
          <label className="form-field">
            <span className="form-label">Status</span>
            <select
              className="form-input"
              value={status}
              onChange={(event) => setStatus(event.target.value as FinancingSourceStatus)}
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="form-field">
            <span className="form-label">Auszahlung ab</span>
            <input
              className="form-input"
              type="date"
              value={startsOn}
              onChange={(event) => setStartsOn(event.target.value)}
            />
          </label>
        </div>
      </fieldset>

      <fieldset className="loan-editor__section">
        <legend>
          <span>2</span> Konditionen
        </legend>
        <div className="form-grid-3">
          <label className="form-field">
            <span className="form-label">Sollzins (% p. a.)</span>
            <input
              className="form-input form-input--mono"
              inputMode="decimal"
              aria-label="Sollzins (% p. a.)"
              value={periods[0]?.rate ?? ""}
              onChange={(event) => {
                updatePeriod(0, { rate: event.target.value });
                setPeriodErrors((current) => ({
                  ...current,
                  0: { ...current[0], rate: undefined },
                }));
              }}
              aria-invalid={Boolean(periodErrors[0]?.rate)}
              aria-describedby={periodErrors[0]?.rate ? "loan-rate-error-0" : undefined}
            />
            {periodErrors[0]?.rate && (
              <small id="loan-rate-error-0" className="field-error">
                {periodErrors[0].rate}
              </small>
            )}
            <small className="metric-hint">
              {periods[0]?.kind === "contracted" ? "Vertraglich" : "Planungsannahme"}
            </small>
          </label>
          <label className="form-field">
            <span className="form-label">Zinsbindung (Monate)</span>
            <input
              className="form-input form-input--mono"
              inputMode="numeric"
              value={firstBindingMonths || ""}
              onChange={(event) => setBindingMonths(event.target.value)}
            />
          </label>
          {firstBindingMonths > 0 && (
            <label className="form-field">
              <span className="form-label">Anschlusszins-Annahme (%)</span>
              <input
                className="form-input form-input--mono"
                inputMode="decimal"
                value={periods[1]?.rate ?? ""}
                onChange={(event) => updatePeriod(1, { rate: event.target.value, kind: "assumed" })}
                aria-invalid={Boolean(periodErrors[1]?.rate)}
                aria-describedby={periodErrors[1]?.rate ? "loan-rate-error-1" : undefined}
              />
              {periodErrors[1]?.rate && (
                <small id="loan-rate-error-1" className="field-error">
                  {periodErrors[1].rate}
                </small>
              )}
              <small className="metric-hint">Keine Kreditzusage</small>
            </label>
          )}
        </div>
      </fieldset>

      <fieldset className="loan-editor__section">
        <legend>
          <span>3</span> Rückzahlung
        </legend>
        <div className="loan-choice-row" role="radiogroup" aria-label="Tilgungsart">
          {(
            [
              ["initial_repayment", "Anfangstilgung"],
              ["full_repayment_term", "Ziel-Laufzeit"],
              ["fixed_payment", "Feste Monatsrate"],
            ] as const
          ).map(([value, label]) => (
            <label
              key={value}
              className={`loan-choice ${mode === value ? "loan-choice--active" : ""}`}
            >
              <input
                type="radio"
                name={`amortization-${source?.id ?? "new"}`}
                value={value}
                checked={mode === value}
                onChange={() => setMode(value)}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
        <div className="form-grid-3">
          {mode === "initial_repayment" && (
            <label className="form-field">
              <span className="form-label">Anfangstilgung (% p. a.)</span>
              <input
                className="form-input form-input--mono"
                value={initialRepayment}
                onChange={(event) => setInitialRepayment(event.target.value)}
              />
            </label>
          )}
          {mode === "full_repayment_term" && (
            <label className="form-field">
              <span className="form-label">Ziel-Laufzeit (Monate)</span>
              <input
                className="form-input form-input--mono"
                value={targetTerm}
                onChange={(event) => setTargetTerm(event.target.value)}
              />
            </label>
          )}
          {mode === "fixed_payment" && (
            <label className="form-field">
              <span className="form-label">Monatsrate (€)</span>
              <input
                className="form-input form-input--mono"
                value={fixedPayment}
                onChange={(event) => setFixedPayment(event.target.value)}
              />
            </label>
          )}
          <label className="form-field">
            <span className="form-label">Tilgungsfreie Monate</span>
            <input
              className="form-input form-input--mono"
              value={repaymentFree}
              onChange={(event) => setRepaymentFree(event.target.value)}
            />
            <small className="metric-hint">Zinsen laufen, Tilgung startet später.</small>
          </label>
        </div>
        {preview && (
          <div className="loan-editor__result" aria-label="Kreditvorschau">
            <div>
              <span>Rate danach</span>
              <strong>
                {formatMinorUnitsAsEuro(
                  mode === "fixed_payment"
                    ? (parseAmountToMinorUnits(fixedPayment) ?? 0)
                    : preview.paymentMinor,
                )}
              </strong>
            </div>
            <div>
              <span>Rate tilgungsfrei</span>
              <strong>{formatMinorUnitsAsEuro(preview.paymentDuringRepaymentFreeMinor)}</strong>
            </div>
            <div>
              <span>Restschuld nach Bindung</span>
              <strong>{formatMinorUnitsAsEuro(preview.balanceAtFixedEndMinor)}</strong>
            </div>
          </div>
        )}
      </fieldset>

      <fieldset className="loan-editor__section">
        <legend>
          <span>4</span> Verwendung &amp; Abrufe
        </legend>
        <div className="loan-choice-row" role="radiogroup" aria-label="Auszahlungsmodus">
          {(
            [
              ["immediate", "Gesamter Betrag sofort", "Kauf und Startkosten"],
              ["cost_plan", "Nach Kostenplan abrufen", "Kauf zuerst, Maßnahmen später"],
              ["manual", "Tranchen selbst festlegen", "Eigener Abrufplan"],
            ] as const
          ).map(([value, label, hint]) => (
            <label
              key={value}
              className={`loan-choice loan-choice--stacked ${drawMode === value ? "loan-choice--active" : ""}`}
            >
              <input
                type="radio"
                name={`draw-mode-${source?.id ?? "new"}`}
                value={value}
                checked={drawMode === value}
                onChange={() => setDrawMode(value)}
              />
              <span>
                <strong>{label}</strong>
                <small>{hint}</small>
              </span>
            </label>
          ))}
        </div>
        {drawMode === "cost_plan" && (
          <div className="draw-preview">
            <strong>Automatische Abruf-Vorschau</strong>
            <p className="metric-hint">
              Geplante Zahlungen werden chronologisch belegt. Der Kreditrahmen wird nie
              überschritten.
            </p>
            <DrawTimeline draws={shownDraws} />
            {liveDrawPreview.isFetching && !autoPreview && (
              <p className="metric-hint" role="status">
                Abruf-Vorschau wird berechnet…
              </p>
            )}
            {liveDrawPreview.isError && (
              <p className="field-error" role="alert">
                Abruf-Vorschau konnte nicht berechnet werden.
              </p>
            )}
            {autoPreview && (
              <div className="draw-preview__balance">
                <span>Belegt: {formatMinorUnitsAsEuro(autoPreview.allocated_minor ?? 0)}</span>
                <span>
                  Restmittel: {formatMinorUnitsAsEuro(autoPreview.unallocated_credit_minor ?? 0)}
                </span>
                <span className={(autoPreview.uncovered_cost_minor ?? 0) > 0 ? "status-error" : ""}>
                  Ungedeckte Kosten: {formatMinorUnitsAsEuro(autoPreview.uncovered_cost_minor ?? 0)}
                </span>
              </div>
            )}
          </div>
        )}
        {drawMode === "manual" && (
          <div className="draw-list">
            {draws.map((draw, index) => (
              <div className="draw-list__row" key={index}>
                <label className="form-field">
                  <span className="form-label">Monat ab Start</span>
                  <input
                    className="form-input form-input--mono"
                    value={draw.month}
                    onChange={(event) =>
                      setDraws((current) =>
                        current.map((item, itemIndex) =>
                          itemIndex === index ? { ...item, month: event.target.value } : item,
                        ),
                      )
                    }
                  />
                </label>
                <label className="form-field">
                  <span className="form-label">Tranche (€)</span>
                  <input
                    className="form-input form-input--mono"
                    value={draw.amount}
                    onChange={(event) =>
                      setDraws((current) =>
                        current.map((item, itemIndex) =>
                          itemIndex === index ? { ...item, amount: event.target.value } : item,
                        ),
                      )
                    }
                  />
                </label>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() =>
                    setDraws((current) => current.filter((_, itemIndex) => itemIndex !== index))
                  }
                >
                  Entfernen
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setDraws((current) => [...current, { month: "0", amount: "" }])}
            >
              + Tranche
            </button>
          </div>
        )}
      </fieldset>

      <div className="loan-editor__more">
        <button
          type="button"
          className="btn-link"
          aria-expanded={showMore}
          onClick={() => setShowMore((value) => !value)}
        >
          {showMore ? "Weitere Konditionen schließen" : "Weitere Konditionen"}
        </button>
        {showMore && (
          <div className="rate-period-list">
            {periods.map((period, index) => (
              <div className="rate-period-row" key={index}>
                <label className="form-field">
                  <span className="form-label">Ab Monat</span>
                  <input
                    className="form-input form-input--mono"
                    value={period.starts}
                    onChange={(event) => updatePeriod(index, { starts: event.target.value })}
                    aria-invalid={Boolean(periodErrors[index]?.starts)}
                  />
                  {periodErrors[index]?.starts && (
                    <small className="field-error">{periodErrors[index].starts}</small>
                  )}
                </label>
                <label className="form-field">
                  <span className="form-label">Bis Monat</span>
                  <input
                    className="form-input form-input--mono"
                    value={period.ends}
                    placeholder="offen"
                    onChange={(event) => updatePeriod(index, { ends: event.target.value })}
                    aria-invalid={Boolean(periodErrors[index]?.ends)}
                  />
                  {periodErrors[index]?.ends && (
                    <small className="field-error">{periodErrors[index].ends}</small>
                  )}
                </label>
                <label className="form-field">
                  <span className="form-label">Sollzins (%)</span>
                  <input
                    className="form-input form-input--mono"
                    value={period.rate}
                    onChange={(event) => updatePeriod(index, { rate: event.target.value })}
                    aria-invalid={Boolean(periodErrors[index]?.rate)}
                  />
                  {periodErrors[index]?.rate && (
                    <small className="field-error">{periodErrors[index].rate}</small>
                  )}
                </label>
                <label className="form-field">
                  <span className="form-label">Herkunft</span>
                  <select
                    className="form-input"
                    value={period.kind}
                    onChange={(event) =>
                      updatePeriod(index, { kind: event.target.value as PeriodDraft["kind"] })
                    }
                  >
                    <option value="contracted">Vertraglich</option>
                    <option value="assumed">Annahme</option>
                  </select>
                </label>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={periods.length === 1}
                  onClick={() =>
                    setPeriods((current) =>
                      current.filter((_, periodIndex) => periodIndex !== index),
                    )
                  }
                >
                  Entfernen
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                setPeriods((current) => [
                  ...current,
                  {
                    starts: String(Number(current.at(-1)?.ends || current.at(-1)?.starts || 0) + 1),
                    ends: "",
                    rate: "",
                    kind: "assumed",
                    label: "Weitere Zinsphase",
                  },
                ])
              }
            >
              + Zinsphase
            </button>
          </div>
        )}
      </div>

      {(error || mutationError) && (
        <p className="status-error" role="alert">
          {error ??
            (mutationError instanceof Error
              ? mutationError.message
              : "Kredit konnte nicht gespeichert werden.")}
        </p>
      )}
      <div className="loan-editor__actions">
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={pending}>
          Abbrechen
        </button>
        <button type="submit" className="btn-primary" disabled={pending}>
          {pending ? "Speichert…" : plan ? "Änderungen speichern" : "Kredit & Tilgungsplan anlegen"}
        </button>
      </div>
    </form>
  );
}

function DrawTimeline({ draws }: { draws: DrawDraft[] }) {
  if (draws.length === 0)
    return (
      <p className="placeholder-note">
        Im Kostenplan sind noch keine geplanten Zahlungen hinterlegt.
      </p>
    );
  return (
    <ol className="draw-timeline" aria-label="Abrufe entlang des Kostenplans">
      {draws.map((draw, index) => {
        const month = Number(draw.month) || 0;
        return (
          <li key={`${draw.month}-${index}`}>
            <span className="draw-timeline__dot" />
            <span>{month === 0 ? "Kauf / Start" : `Jahr ${Math.floor(month / 12) + 1}`}</span>
            <strong>{formatMinorUnitsAsEuro(parseAmountToMinorUnits(draw.amount) ?? 0)}</strong>
          </li>
        );
      })}
    </ol>
  );
}
