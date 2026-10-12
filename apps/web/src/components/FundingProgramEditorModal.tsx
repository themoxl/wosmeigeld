import { useState, type FormEvent } from "react";
import { useFundingProgram, useUpdateFundingProgramTerms } from "../hooks/useFunding";
import type { FundingProgramDetail, FundingProgramTermsUpdate } from "../lib/types";
import { Modal } from "./Modal";

interface FundingProgramEditorModalProps {
  programId: string;
  onClose: () => void;
}

interface TierFormRow {
  min: string;
  max: string;
  amountEuro: string;
}

interface VariantRateRow {
  id: string;
  label: string;
  nominalPercent: string;
  effectivePercent: string;
}

interface RateOptionRow extends VariantRateRow {
  fixedRateMonths: number;
}

type JsonObject = Record<string, unknown>;

function numericValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function minorToEuro(value: unknown): string {
  const number = numericValue(value);
  return number === null ? "" : String(number / 100).replace(".", ",");
}

function bpsToPercent(value: unknown): string {
  const number = numericValue(value);
  return number === null ? "" : String(number / 100).replace(".", ",");
}

function monthsToYears(value: unknown): string {
  const number = numericValue(value);
  return number === null ? "" : String(number / 12).replace(".", ",");
}

function parseDecimal(value: string, label: string, optional = true): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    if (optional) return undefined;
    throw new Error(`${label} ist erforderlich.`);
  }
  const parsed = Number(trimmed.replace(",", "."));
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${label} muss eine positive Zahl sein.`);
  }
  return parsed;
}

function setOptionalNumber(target: JsonObject, key: string, value: number | undefined): void {
  if (value === undefined) delete target[key];
  else target[key] = value;
}

function defaultManualVersion(sourceVersion: string): string {
  const today = new Date().toISOString().slice(0, 10);
  return `${sourceVersion} · manuell ${today}`.slice(0, 80);
}

function dateInput(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : "";
}

function cloneJson(value: JsonObject): JsonObject {
  return JSON.parse(JSON.stringify(value)) as JsonObject;
}

function updateBenefitComponent(root: JsonObject, path: number[], patch: JsonObject): JsonObject {
  if (path.length === 0) return { ...root, ...patch };
  const [index, ...rest] = path;
  if (index === undefined) return root;
  const components = Array.isArray(root.components) ? [...root.components] : [];
  const current =
    components[index] && typeof components[index] === "object"
      ? (components[index] as JsonObject)
      : {};
  components[index] = updateBenefitComponent(current, rest, patch);
  return { ...root, components };
}

export function FundingProgramEditorModal({ programId, onClose }: FundingProgramEditorModalProps) {
  const programQuery = useFundingProgram(programId);

  return (
    <Modal title="Fördervorlage bearbeiten" onClose={onClose} size="wide">
      {programQuery.isLoading && <p className="placeholder-note">Lade Fördervorlage…</p>}
      {programQuery.isError && (
        <p className="funding-editor-error">Fördervorlage konnte nicht geladen werden.</p>
      )}
      {programQuery.data?.current_version && (
        <FundingProgramEditorForm
          key={programQuery.data.current_version.id}
          program={programQuery.data}
          version={programQuery.data.current_version}
          onClose={onClose}
        />
      )}
    </Modal>
  );
}

type FundingProgramVersion = NonNullable<FundingProgramDetail["current_version"]>;

function FundingProgramEditorForm({
  program,
  version,
  onClose,
}: {
  program: FundingProgramDetail;
  version: FundingProgramVersion;
  onClose: () => void;
}) {
  const updateTerms = useUpdateFundingProgramTerms();
  const benefit = version.benefit_json as JsonObject;
  const loan = version.loan_terms_json as JsonObject;
  const [sourceVersion, setSourceVersion] = useState(() =>
    defaultManualVersion(version.source_version),
  );
  const [reviewDueOn, setReviewDueOn] = useState(() => dateInput(version.review_due_at));
  const [sourceUrl, setSourceUrl] = useState(version.source_url);
  const [amountEuro, setAmountEuro] = useState(() =>
    minorToEuro(benefit.amount_minor ?? benefit.amount_per_unit_minor),
  );
  const [ratePercent, setRatePercent] = useState(() => bpsToPercent(benefit.rate_bps));
  const [capEuro, setCapEuro] = useState(() => minorToEuro(benefit.cap_minor));
  const [combinedBenefit, setCombinedBenefit] = useState<JsonObject>(() => cloneJson(benefit));
  const [tiers, setTiers] = useState<TierFormRow[]>(() =>
    Array.isArray(benefit.tiers)
      ? benefit.tiers.map((raw) => {
          const tier = raw as JsonObject;
          return {
            min: numericValue(tier.min) === null ? "" : String(tier.min),
            max: numericValue(tier.max) === null ? "" : String(tier.max),
            amountEuro: minorToEuro(tier.amount_minor),
          };
        })
      : [],
  );
  const [nominalRatePercent, setNominalRatePercent] = useState(() =>
    bpsToPercent(loan.nominal_rate_bps_annual),
  );
  const [termYears, setTermYears] = useState(() => monthsToYears(loan.max_term_months));
  const [fixedRateYears, setFixedRateYears] = useState(() => monthsToYears(loan.fixed_rate_months));
  const [repaymentFreeYears, setRepaymentFreeYears] = useState(() =>
    monthsToYears(loan.max_repayment_free_months),
  );
  const [initialRepaymentPercent, setInitialRepaymentPercent] = useState(() =>
    bpsToPercent(loan.initial_repayment_rate_bps),
  );
  const [subsidizedRatePercent, setSubsidizedRatePercent] = useState(() =>
    bpsToPercent(loan.subsidized_rate_bps_annual),
  );
  const [subsidizedYears, setSubsidizedYears] = useState(() =>
    monthsToYears(loan.subsidized_months),
  );
  const [note, setNote] = useState(() => (typeof loan.note === "string" ? loan.note : ""));
  const [variantRates, setVariantRates] = useState<VariantRateRow[]>(() =>
    Array.isArray(loan.variants)
      ? loan.variants.flatMap((raw) => {
          if (!raw || typeof raw !== "object") return [];
          const variant = raw as JsonObject;
          if (typeof variant.id !== "string" || typeof variant.label !== "string") return [];
          return [
            {
              id: variant.id,
              label: variant.label,
              nominalPercent: bpsToPercent(variant.nominal_rate_bps_annual),
              effectivePercent: bpsToPercent(variant.effective_rate_bps_annual),
            },
          ];
        })
      : [],
  );
  const [rateOptionRates, setRateOptionRates] = useState<RateOptionRow[]>(() =>
    Array.isArray(loan.rate_options)
      ? loan.rate_options.flatMap((raw) => {
          if (!raw || typeof raw !== "object") return [];
          const option = raw as JsonObject;
          if (
            typeof option.id !== "string" ||
            typeof option.label !== "string" ||
            typeof option.fixed_rate_months !== "number"
          )
            return [];
          return [
            {
              id: option.id,
              label: option.label,
              fixedRateMonths: option.fixed_rate_months,
              nominalPercent: bpsToPercent(option.nominal_rate_bps_annual),
              effectivePercent: bpsToPercent(option.effective_rate_bps_annual),
            },
          ];
        })
      : [],
  );
  const [formError, setFormError] = useState<string | null>(null);
  const rateAsOf = typeof loan.rate_as_of === "string" ? loan.rate_as_of : null;

  const benefitKind = typeof benefit.type === "string" ? benefit.type : "unknown";
  const isLoan = program.benefit_type === "loan" || program.benefit_type === "loan_with_grant";
  const programId = program.id;

  const updateTier = (index: number, patch: Partial<TierFormRow>) => {
    setTiers((current) => current.map((tier, i) => (i === index ? { ...tier, ...patch } : tier)));
  };

  const buildBenefit = (): JsonObject => {
    if (benefitKind === "combined") return combinedBenefit;
    const next = { ...benefit };
    if (benefitKind === "loan_fixed" || benefitKind === "fixed_grant") {
      const amount = parseDecimal(amountEuro, "Förderbetrag", false);
      next.amount_minor = Math.round((amount ?? 0) * 100);
    } else if (benefitKind === "loan_tiered") {
      if (tiers.length === 0) throw new Error("Mindestens eine Förderstaffel ist erforderlich.");
      next.tiers = tiers.map((tier, index) => {
        const min = parseDecimal(tier.min, `Staffel ${index + 1}: Untergrenze`, false);
        const max = parseDecimal(tier.max, `Staffel ${index + 1}: Obergrenze`);
        const amount = parseDecimal(tier.amountEuro, `Staffel ${index + 1}: Betrag`, false);
        return {
          min: Math.round(min ?? 0),
          ...(max === undefined ? {} : { max: Math.round(max) }),
          amount_minor: Math.round((amount ?? 0) * 100),
        };
      });
    } else if (benefitKind === "percent_of_costs" || benefitKind === "loan_percent_of_costs") {
      const rate = parseDecimal(ratePercent, "Fördersatz", false);
      const cap = parseDecimal(capEuro, "Höchstbetrag");
      next.rate_bps = Math.round((rate ?? 0) * 100);
      setOptionalNumber(next, "cap_minor", cap === undefined ? undefined : Math.round(cap * 100));
    } else if (benefitKind === "per_unit") {
      const amount = parseDecimal(amountEuro, "Betrag je Einheit", false);
      const cap = parseDecimal(capEuro, "Höchstbetrag");
      next.amount_per_unit_minor = Math.round((amount ?? 0) * 100);
      setOptionalNumber(next, "cap_minor", cap === undefined ? undefined : Math.round(cap * 100));
    }
    return next;
  };

  const buildLoanTerms = (): JsonObject => {
    const next = { ...(version.loan_terms_json as JsonObject) };
    const nominal = parseDecimal(nominalRatePercent, "Nominalzins");
    const term = parseDecimal(termYears, "Laufzeit");
    const fixed = parseDecimal(fixedRateYears, "Zinsbindung");
    const repaymentFree = parseDecimal(repaymentFreeYears, "Tilgungsfreie Zeit");
    const initialRepayment = parseDecimal(initialRepaymentPercent, "Anfängliche Tilgung");
    const subsidizedRate = parseDecimal(subsidizedRatePercent, "Vergünstigter Zinssatz");
    const subsidizedDuration = parseDecimal(subsidizedYears, "Dauer der Zinsvergünstigung");

    if ((subsidizedRate === undefined) !== (subsidizedDuration === undefined)) {
      throw new Error("Vergünstigter Zinssatz und dessen Dauer gehören zusammen.");
    }
    setOptionalNumber(
      next,
      "nominal_rate_bps_annual",
      nominal === undefined ? undefined : Math.round(nominal * 100),
    );
    setOptionalNumber(
      next,
      "max_term_months",
      term === undefined ? undefined : Math.round(term * 12),
    );
    setOptionalNumber(
      next,
      "fixed_rate_months",
      fixed === undefined ? undefined : Math.round(fixed * 12),
    );
    setOptionalNumber(
      next,
      "max_repayment_free_months",
      repaymentFree === undefined ? undefined : Math.round(repaymentFree * 12),
    );
    setOptionalNumber(
      next,
      "initial_repayment_rate_bps",
      initialRepayment === undefined ? undefined : Math.round(initialRepayment * 100),
    );
    setOptionalNumber(
      next,
      "subsidized_rate_bps_annual",
      subsidizedRate === undefined ? undefined : Math.round(subsidizedRate * 100),
    );
    setOptionalNumber(
      next,
      "subsidized_months",
      subsidizedDuration === undefined ? undefined : Math.round(subsidizedDuration * 12),
    );
    if (note.trim()) next.note = note.trim();
    else delete next.note;
    if (Array.isArray(loan.variants)) {
      next.variants = loan.variants.map((raw) => {
        if (!raw || typeof raw !== "object") return raw;
        const variant = raw as JsonObject;
        const row = variantRates.find((item) => item.id === variant.id);
        if (!row) return raw;
        const nominal = parseDecimal(row.nominalPercent, `${row.label}: Nominalzins`, false);
        const effective = parseDecimal(row.effectivePercent, `${row.label}: Effektivzins`, false);
        return {
          ...variant,
          nominal_rate_bps_annual: Math.round((nominal ?? 0) * 100),
          effective_rate_bps_annual: Math.round((effective ?? 0) * 100),
        };
      });
    }
    if (Array.isArray(loan.rate_options)) {
      next.rate_options = loan.rate_options.map((raw) => {
        if (!raw || typeof raw !== "object") return raw;
        const option = raw as JsonObject;
        const row = rateOptionRates.find((item) => item.id === option.id);
        if (!row) return raw;
        const nominal = parseDecimal(row.nominalPercent, `${row.label}: Nominalzins`, false);
        const effective = parseDecimal(row.effectivePercent, `${row.label}: Effektivzins`, false);
        return {
          ...option,
          nominal_rate_bps_annual: Math.round((nominal ?? 0) * 100),
          effective_rate_bps_annual: Math.round((effective ?? 0) * 100),
        };
      });
    }
    return next;
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!version) return;
    setFormError(null);
    try {
      if (!sourceVersion.trim()) throw new Error("Bitte einen Namen für den neuen Stand angeben.");
      if (!reviewDueOn)
        throw new Error("Bitte festlegen, wann die Konditionen erneut geprüft werden.");
      if (!sourceUrl.trim()) throw new Error("Eine Quelle ist für Förderkonditionen erforderlich.");
      const input: FundingProgramTermsUpdate = {
        source_version: sourceVersion.trim(),
        review_due_on: reviewDueOn,
        source_url: sourceUrl.trim(),
        benefit_json: buildBenefit(),
        loan_terms_json: isLoan ? buildLoanTerms() : version.loan_terms_json,
      };
      updateTerms.mutate(
        { programId, input },
        {
          onSuccess: onClose,
          onError: (error) => setFormError(error.message),
        },
      );
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Konditionen sind ungültig.");
    }
  };

  return (
    <form className="funding-editor" onSubmit={submit}>
      <div>
        <h3 className="card-heading">{program.title}</h3>
        <p className="metric-hint">
          Speichern erzeugt eine neue Version. Bereits in Vorhaben übernommene Finanzierungen
          behalten ihre bisherigen Konditionen.
        </p>
      </div>

      <fieldset className="funding-editor-section">
        <legend>Stand und Quelle</legend>
        <div className="form-grid-2">
          <div className="form-field">
            <label className="form-label" htmlFor="funding-source-version">
              Bezeichnung des neuen Stands
            </label>
            <input
              id="funding-source-version"
              className="form-input"
              value={sourceVersion}
              maxLength={80}
              onChange={(event) => setSourceVersion(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="funding-review-due">
              Erneut prüfen am
            </label>
            <input
              id="funding-review-due"
              type="date"
              className="form-input"
              value={reviewDueOn}
              onChange={(event) => setReviewDueOn(event.target.value)}
            />
          </div>
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="funding-source-url">
            Quelle
          </label>
          <input
            id="funding-source-url"
            type="url"
            className="form-input"
            value={sourceUrl}
            onChange={(event) => setSourceUrl(event.target.value)}
          />
        </div>
      </fieldset>

      <fieldset className="funding-editor-section">
        <legend>Förderbetrag</legend>
        {(benefitKind === "loan_fixed" || benefitKind === "fixed_grant") && (
          <MoneyField
            id="funding-amount"
            label="Maximaler Betrag (€)"
            value={amountEuro}
            onChange={setAmountEuro}
          />
        )}
        {benefitKind === "per_unit" && (
          <div className="form-grid-2">
            <MoneyField
              id="funding-per-unit"
              label="Betrag je Einheit (€)"
              value={amountEuro}
              onChange={setAmountEuro}
            />
            <MoneyField
              id="funding-cap"
              label="Höchstbetrag (€)"
              value={capEuro}
              onChange={setCapEuro}
            />
          </div>
        )}
        {(benefitKind === "percent_of_costs" || benefitKind === "loan_percent_of_costs") && (
          <div className="form-grid-2">
            <TextField
              id="funding-rate"
              label="Fördersatz (%)"
              value={ratePercent}
              onChange={setRatePercent}
            />
            <MoneyField
              id="funding-cap"
              label="Höchstbetrag (€)"
              value={capEuro}
              onChange={setCapEuro}
            />
          </div>
        )}
        {benefitKind === "loan_tiered" && (
          <div className="funding-tier-list">
            {tiers.map((tier, index) => (
              <div className="funding-tier-row" key={index}>
                <TextField
                  id={`funding-tier-min-${index}`}
                  label="ab"
                  value={tier.min}
                  onChange={(value) => updateTier(index, { min: value })}
                />
                <TextField
                  id={`funding-tier-max-${index}`}
                  label="bis (leer = offen)"
                  value={tier.max}
                  onChange={(value) => updateTier(index, { max: value })}
                />
                <MoneyField
                  id={`funding-tier-amount-${index}`}
                  label="Maximalbetrag (€)"
                  value={tier.amountEuro}
                  onChange={(value) => updateTier(index, { amountEuro: value })}
                />
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setTiers((current) => current.filter((_, i) => i !== index))}
                >
                  Entfernen
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                setTiers((current) => [...current, { min: "", max: "", amountEuro: "" }])
              }
            >
              + Staffel
            </button>
          </div>
        )}
        {benefitKind === "combined" && (
          <CombinedBenefitFields
            benefit={combinedBenefit}
            onChange={(path, patch) =>
              setCombinedBenefit((current) => updateBenefitComponent(current, path, patch))
            }
          />
        )}
        {![
          "loan_fixed",
          "fixed_grant",
          "per_unit",
          "percent_of_costs",
          "loan_percent_of_costs",
          "loan_tiered",
          "combined",
        ].includes(benefitKind) && (
          <p className="metric-hint">
            Der zusammengesetzte Förderbetrag bleibt unverändert; die Kreditkonditionen können
            trotzdem gepflegt werden.
          </p>
        )}
      </fieldset>

      {isLoan && (
        <fieldset className="funding-editor-section">
          <legend>Darlehenskonditionen</legend>
          {rateOptionRates.length > 0 && (
            <div className="funding-variant-rate-editor">
              <div>
                <h4 className="card-heading">Zinsbindungsoptionen</h4>
                {rateAsOf && (
                  <p className="metric-hint">
                    Konditionsstand: {new Date(rateAsOf).toLocaleDateString("de-DE")}
                  </p>
                )}
              </div>
              <div className="funding-variant-rate-editor__header" aria-hidden="true">
                <span>Zinsbindung</span>
                <span>Nominalzins p.a.</span>
                <span>Effektivzins p.a.</span>
              </div>
              {rateOptionRates.map((row) => (
                <div className="funding-variant-rate-editor__row" key={row.id}>
                  <strong>
                    {row.label}
                    <span className="metric-hint">{row.fixedRateMonths / 12} Jahre fest</span>
                  </strong>
                  <TextField
                    id={`funding-rate-option-nominal-${row.id}`}
                    label="Nominal (%)"
                    value={row.nominalPercent}
                    onChange={(value) =>
                      setRateOptionRates((current) =>
                        current.map((item) =>
                          item.id === row.id ? { ...item, nominalPercent: value } : item,
                        ),
                      )
                    }
                  />
                  <TextField
                    id={`funding-rate-option-effective-${row.id}`}
                    label="Effektiv (%)"
                    value={row.effectivePercent}
                    onChange={(value) =>
                      setRateOptionRates((current) =>
                        current.map((item) =>
                          item.id === row.id ? { ...item, effectivePercent: value } : item,
                        ),
                      )
                    }
                  />
                </div>
              ))}
              <TextField
                id="funding-initial-repayment-options"
                label="Anfängliche Tilgung (%)"
                value={initialRepaymentPercent}
                onChange={setInitialRepaymentPercent}
              />
              <p className="metric-hint">
                Die Gesamtlaufzeit ist nicht vorgegeben. Nach der gewählten Zinsbindung wird im
                Vorhaben mit einer klar gekennzeichneten Anschlusszins-Annahme weitergerechnet.
              </p>
            </div>
          )}
          {variantRates.length > 0 && (
            <div className="funding-variant-rate-editor">
              {rateAsOf && (
                <p className="metric-hint">
                  Konditionsstand: {new Date(rateAsOf).toLocaleDateString("de-DE")}
                </p>
              )}
              <div className="funding-variant-rate-editor__header" aria-hidden="true">
                <span>Variante</span>
                <span>Sollzins p.a.</span>
                <span>Effektivzins p.a.</span>
              </div>
              {variantRates.map((row) => (
                <div className="funding-variant-rate-editor__row" key={row.id}>
                  <strong>{row.label}</strong>
                  <TextField
                    id={`funding-variant-nominal-${row.id}`}
                    label="Sollzins (%)"
                    value={row.nominalPercent}
                    onChange={(value) =>
                      setVariantRates((current) =>
                        current.map((item) =>
                          item.id === row.id ? { ...item, nominalPercent: value } : item,
                        ),
                      )
                    }
                  />
                  <TextField
                    id={`funding-variant-effective-${row.id}`}
                    label="Effektivzins (%)"
                    value={row.effectivePercent}
                    onChange={(value) =>
                      setVariantRates((current) =>
                        current.map((item) =>
                          item.id === row.id ? { ...item, effectivePercent: value } : item,
                        ),
                      )
                    }
                  />
                </div>
              ))}
              <p className="metric-hint">
                Laufzeit und Anlaufzeit bleiben je Variante gekoppelt. Hier werden nur die
                veröffentlichten Zinssätze eines neuen Konditionsstands gepflegt.
              </p>
            </div>
          )}
          {variantRates.length === 0 && rateOptionRates.length === 0 && (
            <div className="form-grid-3">
              <TextField
                id="funding-nominal-rate"
                label="Nominalzins p.a. (%)"
                value={nominalRatePercent}
                onChange={setNominalRatePercent}
              />
              <TextField
                id="funding-term"
                label="Maximale Laufzeit (Jahre)"
                value={termYears}
                onChange={setTermYears}
              />
              <TextField
                id="funding-fixed-rate"
                label="Zinsbindung (Jahre)"
                value={fixedRateYears}
                onChange={setFixedRateYears}
              />
              <TextField
                id="funding-repayment-free"
                label="Tilgungsfreie Zeit (Jahre)"
                value={repaymentFreeYears}
                onChange={setRepaymentFreeYears}
              />
              <TextField
                id="funding-initial-repayment"
                label="Anfängliche Tilgung (%)"
                value={initialRepaymentPercent}
                onChange={setInitialRepaymentPercent}
              />
            </div>
          )}
          {variantRates.length === 0 && rateOptionRates.length === 0 && (
            <div className="form-grid-2">
              <TextField
                id="funding-subsidized-rate"
                label="Vergünstigter Zinssatz p.a. (%)"
                value={subsidizedRatePercent}
                onChange={setSubsidizedRatePercent}
              />
              <TextField
                id="funding-subsidized-years"
                label="Dauer der Vergünstigung (Jahre)"
                value={subsidizedYears}
                onChange={setSubsidizedYears}
              />
            </div>
          )}
          <div className="form-field">
            <label className="form-label" htmlFor="funding-note">
              Hinweis zu den Konditionen
            </label>
            <textarea
              id="funding-note"
              className="form-input funding-editor-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          <p className="metric-hint">
            Ein Katalogzins ist ein manueller Richtwert. Im Vorhaben kann und soll später der
            tatsächlich zugesagte Bankzins eingetragen werden.
          </p>
        </fieldset>
      )}

      {formError && <p className="funding-editor-error">{formError}</p>}
      <div className="modal-actions">
        <button type="button" className="btn-secondary" onClick={onClose}>
          Abbrechen
        </button>
        <button type="submit" className="btn-primary" disabled={updateTerms.isPending}>
          {updateTerms.isPending ? "Speichere…" : "Als neue Version speichern"}
        </button>
      </div>
    </form>
  );
}

function CombinedBenefitFields({
  benefit,
  path = [],
  onChange,
}: {
  benefit: JsonObject;
  path?: number[];
  onChange: (path: number[], patch: JsonObject) => void;
}) {
  const components = Array.isArray(benefit.components) ? benefit.components : [];
  return (
    <div className="funding-combined-benefit">
      {components.map((raw, index) => {
        if (!raw || typeof raw !== "object") return null;
        const component = raw as JsonObject;
        const componentPath = [...path, index];
        const type = component.type;
        if (type === "combined") {
          return (
            <fieldset className="funding-editor-section" key={componentPath.join("-")}>
              <legend>Weiterer Förderbaustein</legend>
              <CombinedBenefitFields benefit={component} path={componentPath} onChange={onChange} />
            </fieldset>
          );
        }
        if (type === "loan_percent_of_costs") {
          return (
            <div className="funding-benefit-component" key={componentPath.join("-")}>
              <div>
                <strong>Darlehensanteil an förderfähigen Kosten</strong>
                <p className="metric-hint">Darlehensbaustein des Gesamtpakets</p>
              </div>
              <TextField
                id={`funding-component-rate-${componentPath.join("-")}`}
                label="Anteil (%)"
                value={bpsToPercent(component.rate_bps)}
                onChange={(value) => {
                  const parsed = Number(value.replace(",", "."));
                  if (value.trim() === "") onChange(componentPath, { rate_bps: undefined });
                  else if (Number.isFinite(parsed))
                    onChange(componentPath, { rate_bps: Math.round(parsed * 100) });
                }}
              />
              <MoneyField
                id={`funding-component-cap-${componentPath.join("-")}`}
                label="Optionaler Höchstbetrag (€)"
                value={minorToEuro(component.cap_minor)}
                onChange={(value) => {
                  const parsed = Number(value.replace(",", "."));
                  if (value.trim() === "") onChange(componentPath, { cap_minor: undefined });
                  else if (Number.isFinite(parsed))
                    onChange(componentPath, { cap_minor: Math.round(parsed * 100) });
                }}
              />
            </div>
          );
        }
        if (type === "per_unit") {
          const isChildGrant = component.unit_field === "children_count";
          return (
            <div className="funding-benefit-component" key={componentPath.join("-")}>
              <div>
                <strong>{isChildGrant ? "Kinderzuschuss je Kind" : "Zuschuss je Einheit"}</strong>
                <p className="metric-hint">Zuschussbaustein des Gesamtpakets</p>
              </div>
              <MoneyField
                id={`funding-component-unit-${componentPath.join("-")}`}
                label={isChildGrant ? "Betrag je Kind (€)" : "Betrag je Einheit (€)"}
                value={minorToEuro(component.amount_per_unit_minor)}
                onChange={(value) => {
                  const parsed = Number(value.replace(",", "."));
                  if (value.trim() === "")
                    onChange(componentPath, { amount_per_unit_minor: undefined });
                  else if (Number.isFinite(parsed))
                    onChange(componentPath, {
                      amount_per_unit_minor: Math.round(parsed * 100),
                    });
                }}
              />
              <MoneyField
                id={`funding-component-unit-cap-${componentPath.join("-")}`}
                label="Optionaler Höchstbetrag (€)"
                value={minorToEuro(component.cap_minor)}
                onChange={(value) => {
                  const parsed = Number(value.replace(",", "."));
                  if (value.trim() === "") onChange(componentPath, { cap_minor: undefined });
                  else if (Number.isFinite(parsed))
                    onChange(componentPath, { cap_minor: Math.round(parsed * 100) });
                }}
              />
            </div>
          );
        }
        return (
          <p className="metric-hint" key={componentPath.join("-")}>
            Unbekannter Förderbaustein „{String(type)}“ bleibt unverändert erhalten.
          </p>
        );
      })}
    </div>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="form-field">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        inputMode="decimal"
        className="form-input form-input--mono"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function MoneyField(props: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return <TextField {...props} />;
}
