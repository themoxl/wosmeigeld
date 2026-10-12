import { useEffect, useMemo, useState } from "react";
import {
  useEligibilityProfile,
  useFundingProgram,
  useFundingPrograms,
} from "../../hooks/useFunding";
import {
  useCreateFinancingConfiguration,
  useCreateFinancingSource,
  useFinancingDrawPreview,
} from "../../hooks/useProjects";
import { useFormatDate } from "../../hooks/useProfile";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import { BENEFIT_TYPE_LABELS } from "../../lib/funding";
import {
  describeBenefit,
  describeLoanTerms,
  loanRateOptions,
  loanTermVariants,
  resolveBenefitAmountMinor,
} from "../../lib/fundingTerms";
import { calculateLoanPreview } from "../../lib/loanPreview";
import type { MajorProject } from "../../lib/types";
import { Modal } from "../Modal";

// Foerderung direkt in der Finanzierung anlegen (statt im getrennten
// Foerder-Tab): Programm waehlen, Konditionen anpassen, sofort sehen was es
// monatlich kostet. Der Foerdertopf ist eine Geldquelle - er gehoert dorthin,
// wo das Geld geplant wird, nicht in einen zweiten Tab, zwischen dem man
// hin- und herspringt.
//
// Es entsteht dabei nichts Neues am Backend: der Dialog setzt dieselben drei
// bestehenden Aufrufe ab wie die manuelle Anlage (Finanzierungsquelle ->
// Tilgungsplan -> Zinsphasen) und haengt nur die Programmversion daran, damit
// die Herkunft nachvollziehbar bleibt.

interface AddFundingSourceDialogProps {
  project: MajorProject;
  scenarioId: string;
  onClose: () => void;
}

/** Liest einen Zahlenwert aus loan_terms_json, sonst null. */
function termNumber(terms: Record<string, unknown> | undefined, key: string): number | null {
  const value = terms?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bpsToPercentInput(bps: number | null): string {
  return bps === null ? "" : String(bps / 100).replace(".", ",");
}

function resolveGrantAmountMinor(
  benefit: Record<string, unknown>,
  facts: Record<string, unknown> | undefined,
): number {
  const type = benefit.type;
  if (type === "fixed_grant") return termNumber(benefit, "amount_minor") ?? 0;
  if (type === "per_unit") {
    const field = typeof benefit.unit_field === "string" ? benefit.unit_field : "";
    const units = field && typeof facts?.[field] === "number" ? facts[field] : 0;
    return (termNumber(benefit, "amount_per_unit_minor") ?? 0) * Number(units);
  }
  if (type === "combined" && Array.isArray(benefit.components)) {
    return benefit.components.reduce(
      (sum: number, component: unknown) =>
        sum +
        (component && typeof component === "object"
          ? resolveGrantAmountMinor(component as Record<string, unknown>, facts)
          : 0),
      0,
    );
  }
  return 0;
}

export function AddFundingSourceDialog({
  project,
  scenarioId,
  onClose,
}: AddFundingSourceDialogProps) {
  const programsQuery = useFundingPrograms({
    countryCode: project.country_code,
    regionCode: project.region_code ?? undefined,
    projectType: project.project_type,
  });
  const [programId, setProgramId] = useState<string | null>(null);
  const detailQuery = useFundingProgram(programId);
  const profileQuery = useEligibilityProfile(project.id);

  useEffect(() => {
    if (!programId || !detailQuery.data) return;
    requestAnimationFrame(() => {
      const editor = document.getElementById("funding-inline-editor");
      if (typeof editor?.scrollIntoView === "function") {
        editor.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }, [programId, detailQuery.data]);

  if (!programId) {
    return (
      <Modal title="Förderung hinzufügen" size="wide" onClose={onClose}>
        {programsQuery.isLoading && <p className="placeholder-note">Lade Förderkatalog...</p>}
        {!programsQuery.isLoading && (programsQuery.data ?? []).length === 0 && (
          <p className="placeholder-note">
            Im Katalog steht noch kein Programm für dieses Vorhaben. Unter „Förderungen“ lassen sich
            Pakete importieren.
          </p>
        )}
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Programm</th>
                <th className="txn-th txn-th--calc-mode">Art</th>
                <th className="txn-th txn-th--actions-wide"></th>
              </tr>
            </thead>
            <tbody>
              {(programsQuery.data ?? []).map((program) => (
                <tr className="txn-tr" key={program.id}>
                  <td className="txn-td">
                    <span className="txn-payee">{program.title}</span>
                    <div className="metric-hint">
                      {program.provider_name}
                      {program.is_stale && " · Katalogstand veraltet"}
                    </div>
                  </td>
                  <td className="txn-td txn-td--calc-mode">
                    {program.benefit_type
                      ? (BENEFIT_TYPE_LABELS[program.benefit_type] ?? program.benefit_type)
                      : "—"}
                  </td>
                  <td className="txn-td txn-td--actions-wide">
                    <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => setProgramId(program.id)}
                      >
                        Auswählen
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Modal>
    );
  }

  const detail = detailQuery.data;
  const version = detail?.current_version;
  if (detailQuery.isLoading || !detail || !version) {
    return (
      <section className="funding-inline-editor card" aria-live="polite">
        <p className="placeholder-note">Lade Programmkonditionen...</p>
      </section>
    );
  }

  return (
    <FundingConditionsForm
      key={version.id}
      project={project}
      scenarioId={scenarioId}
      programTitle={detail.title}
      providerName={detail.provider_name}
      versionId={version.id}
      benefitJson={version.benefit_json as Record<string, unknown>}
      loanTermsJson={version.loan_terms_json as Record<string, unknown>}
      isGrant={version.benefit_type === "grant"}
      hasGrant={version.benefit_type === "loan_with_grant"}
      eligibilityFacts={
        (profileQuery.data?.facts ?? undefined) as Record<string, unknown> | undefined
      }
      onBack={() => setProgramId(null)}
      onClose={onClose}
    />
  );
}

function FundingConditionsForm({
  project,
  scenarioId,
  programTitle,
  providerName,
  versionId,
  benefitJson,
  loanTermsJson,
  isGrant,
  hasGrant,
  eligibilityFacts,
  onBack,
  onClose,
}: {
  project: MajorProject;
  scenarioId: string;
  programTitle: string;
  providerName: string;
  versionId: string;
  benefitJson: Record<string, unknown>;
  loanTermsJson: Record<string, unknown>;
  isGrant: boolean;
  hasGrant: boolean;
  eligibilityFacts: Record<string, unknown> | undefined;
  onBack: () => void;
  onClose: () => void;
}) {
  const createConfiguration = useCreateFinancingConfiguration();
  const createGrantSource = useCreateFinancingSource();
  const { formatDate } = useFormatDate();
  const isPending = createConfiguration.isPending || createGrantSource.isPending;

  const catalogAmount = resolveBenefitAmountMinor(benefitJson, eligibilityFacts);
  const catalogGrantAmount = resolveGrantAmountMinor(benefitJson, eligibilityFacts);
  const variants = useMemo(() => loanTermVariants(loanTermsJson), [loanTermsJson]);
  const rateOptions = useMemo(() => loanRateOptions(loanTermsJson), [loanTermsJson]);
  const catalogRateBps = termNumber(loanTermsJson, "nominal_rate_bps_annual");
  const catalogFixedMonths = termNumber(loanTermsJson, "fixed_rate_months");
  const catalogFreeMonths = termNumber(loanTermsJson, "max_repayment_free_months");
  const catalogRepaymentBps = termNumber(loanTermsJson, "initial_repayment_rate_bps");
  const catalogNote = typeof loanTermsJson["note"] === "string" ? loanTermsJson["note"] : null;

  const [amountEuro, setAmountEuro] = useState(
    catalogAmount !== null ? String(catalogAmount / 100).replace(".", ",") : "",
  );
  const [ratePercent, setRatePercent] = useState(bpsToPercentInput(catalogRateBps));
  const [repaymentPercent, setRepaymentPercent] = useState(
    bpsToPercentInput(catalogRepaymentBps) || "2,0",
  );
  const [repaymentFreeMonths, setRepaymentFreeMonths] = useState(
    catalogFreeMonths !== null ? String(catalogFreeMonths) : "0",
  );
  const [fixedRateMonths, setFixedRateMonths] = useState(
    catalogFixedMonths !== null ? String(catalogFixedMonths) : "120",
  );
  const [followUpPercent, setFollowUpPercent] = useState("4,5");
  const [startsOn, setStartsOn] = useState(new Date().toISOString().slice(0, 10));
  const [showCatalogFacts, setShowCatalogFacts] = useState(false);
  const [selectedVariantId, setSelectedVariantId] = useState<string | null>(null);
  const [selectedRateOptionId, setSelectedRateOptionId] = useState<string | null>(null);
  const [termMonths, setTermMonths] = useState("");
  const [drawMode, setDrawMode] = useState<"immediate" | "cost_plan">("cost_plan");

  const selectedVariant = variants.find((variant) => variant.id === selectedVariantId) ?? null;
  const selectedRateOption =
    rateOptions.find((option) => option.id === selectedRateOptionId) ?? null;
  const rateAsOf =
    typeof loanTermsJson["rate_as_of"] === "string" ? loanTermsJson["rate_as_of"] : null;

  const selectVariant = (variantId: string) => {
    const variant = variants.find((item) => item.id === variantId);
    setSelectedVariantId(variantId);
    setTermMonths("");
    if (!variant) return;
    setRatePercent(bpsToPercentInput(variant.nominalRateBpsAnnual));
    setFixedRateMonths(String(variant.fixedRateMonths));
    setRepaymentFreeMonths(variant.repaymentType === "bullet" ? "0" : "");
  };

  const selectRateOption = (optionId: string) => {
    const option = rateOptions.find((item) => item.id === optionId);
    setSelectedRateOptionId(optionId);
    if (!option) return;
    setRatePercent(bpsToPercentInput(option.nominalRateBpsAnnual));
    setFixedRateMonths(String(option.fixedRateMonths));
  };

  const catalogFacts = useMemo(
    () => [...describeBenefit(benefitJson), ...describeLoanTerms(loanTermsJson)],
    [benefitJson, loanTermsJson],
  );

  const amountMinor = parseAmountToMinorUnits(amountEuro) ?? 0;
  const rateBps = ratePercent ? Math.round(Number(ratePercent.replace(",", ".")) * 100) : null;
  const repaymentBps = Math.round(Number(repaymentPercent.replace(",", ".")) * 100) || 0;
  const costPreview = useFinancingDrawPreview(
    project.id,
    scenarioId,
    {
      principal_limit_minor: Math.max(amountMinor, 1),
      starts_on: startsOn,
      draw_mode: drawMode,
      draws: [],
    },
    !isGrant && drawMode === "cost_plan" && amountMinor > 0 && Boolean(startsOn),
  );

  const preview = useMemo(() => {
    if (
      isGrant ||
      amountMinor <= 0 ||
      rateBps === null ||
      (selectedVariant && (!termMonths || !repaymentFreeMonths))
    )
      return null;
    return calculateLoanPreview({
      principalMinor: amountMinor,
      nominalRateBpsAnnual: rateBps,
      initialRepaymentRateBps: repaymentBps,
      repaymentFreeMonths: Number(repaymentFreeMonths) || 0,
      fixedRateMonths: Number(fixedRateMonths) || 0,
      followUpRateBpsAnnual: Math.round(Number(followUpPercent.replace(",", ".")) * 100) || 0,
      targetTermMonths: selectedVariant ? Number(termMonths) || undefined : undefined,
      repaymentType: selectedVariant?.repaymentType,
    });
  }, [
    isGrant,
    amountMinor,
    rateBps,
    repaymentBps,
    repaymentFreeMonths,
    fixedRateMonths,
    followUpPercent,
    selectedVariant,
    termMonths,
  ]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (amountMinor <= 0) return;
    if (variants.length > 0 && (!selectedVariant || !termMonths || !repaymentFreeMonths)) return;
    if (rateOptions.length > 0 && !selectedRateOption) return;

    const finishWithGrant = () => {
      if (!hasGrant || catalogGrantAmount <= 0) {
        onClose();
        return;
      }
      createGrantSource.mutate(
        {
          projectId: project.id,
          scenarioId,
          input: {
            name: `${programTitle} (Kinderzuschuss)`,
            source_type: "public_grant",
            status: "idea",
            planned_amount_minor: catalogGrantAmount,
            funding_program_version_id: versionId,
            note: `Zuschuss aus dem Förderkatalog übernommen (${providerName}).`,
          },
        },
        { onSuccess: onClose },
      );
    };

    if (isGrant) {
      createGrantSource.mutate(
        {
          projectId: project.id,
          scenarioId,
          input: {
            name: programTitle,
            source_type: "public_grant",
            status: "idea",
            planned_amount_minor: amountMinor,
            funding_program_version_id: versionId,
            note: `Aus dem Förderkatalog übernommen (${providerName}).`,
          },
        },
        { onSuccess: onClose },
      );
      return;
    }
    if (rateBps === null) return;

    const bindingMonths = Number(fixedRateMonths) || 0;
    const followUpBps = Math.round(Number(followUpPercent.replace(",", ".")) * 100) || rateBps;
    createConfiguration.mutate(
      {
        projectId: project.id,
        scenarioId,
        input: {
          source: {
            name: programTitle,
            source_type: "subsidized_loan",
            status: "idea",
            planned_amount_minor: amountMinor,
            principal_limit_minor: amountMinor,
            available_on: startsOn,
            funding_program_version_id: versionId,
            note: `Aus dem Förderkatalog übernommen (${providerName}).`,
          },
          debt_plan: {
            name: programTitle,
            principal_limit_minor: amountMinor,
            starts_on: startsOn,
            amortization_mode:
              selectedVariant?.repaymentType === "annuity"
                ? "full_repayment_term"
                : "initial_repayment",
            initial_repayment_rate_bps:
              selectedVariant?.repaymentType === "annuity"
                ? undefined
                : selectedVariant?.repaymentType === "bullet"
                  ? 0
                  : repaymentBps,
            target_term_months:
              selectedVariant?.repaymentType === "annuity"
                ? Number(termMonths) - Number(repaymentFreeMonths)
                : undefined,
            repayment_free_months: Number(repaymentFreeMonths) || 0,
            payment_interval_months: 1,
            draw_mode: drawMode,
            rate_periods:
              bindingMonths > 0
                ? [
                    {
                      starts_after_month: 0,
                      ends_after_month: bindingMonths - 1,
                      nominal_rate_bps_annual: rateBps,
                      rate_kind: selectedVariant || selectedRateOption ? "contracted" : "assumed",
                      label: selectedRateOption
                        ? "Veröffentlichte BayernLabo-Kondition"
                        : "Sollzinsbindung",
                    },
                    {
                      starts_after_month: bindingMonths,
                      nominal_rate_bps_annual: followUpBps,
                      rate_kind: "assumed",
                      label: "Anschlusszins (Annahme)",
                    },
                  ]
                : [
                    {
                      starts_after_month: 0,
                      nominal_rate_bps_annual: rateBps,
                      rate_kind: "assumed",
                    },
                  ],
            special_repayments:
              selectedVariant?.repaymentType === "bullet"
                ? [
                    {
                      occurs_after_month: Number(termMonths) - 1,
                      amount_minor: amountMinor,
                      status: "planned",
                      label: "Endfällige Gesamttilgung",
                    },
                  ]
                : [],
          },
          selected_loan_terms: {
            variant_id: selectedVariantId,
            rate_option_id: selectedRateOptionId,
            nominal_rate_bps_annual: rateBps,
            fixed_rate_months: bindingMonths,
            follow_up_rate_bps_annual: followUpBps,
            repayment_free_months: Number(repaymentFreeMonths) || 0,
            target_term_months: Number(termMonths) || null,
          } as never,
        },
      },
      { onSuccess: finishWithGrant },
    );
  };

  return (
    <section
      id="funding-inline-editor"
      className="funding-inline-editor card"
      aria-labelledby="funding-inline-editor-title"
    >
      <header className="funding-inline-editor__header">
        <div>
          <span className="eyebrow">Aus Förderkatalog übernommen</span>
          <h2 id="funding-inline-editor-title">Förderung: {programTitle}</h2>
        </div>
        <button type="button" className="btn-secondary" onClick={onClose}>
          Abbrechen
        </button>
      </header>
      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
      >
        {/* Was der Katalog zu diesem Programm hergibt - Hoechstbetraege,
            Fristen, Staffeln. Steht seit jeher in benefit_json/loan_terms_json,
            war aber nirgends sichtbar: wer ein Programm auswaehlte, sah nicht,
            welche Grenzen dahinterstehen, und musste raten, ob sein Betrag
            ueberhaupt zulaessig ist. */}
        <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)" }}>
          <p className="metric-hint" style={{ flex: 1 }}>
            {providerName}
          </p>
          <button
            type="button"
            className="btn-secondary"
            aria-expanded={showCatalogFacts}
            onClick={() => setShowCatalogFacts((value) => !value)}
          >
            {showCatalogFacts ? "Katalogwerte ausblenden" : "ⓘ Was steht im Katalog?"}
          </button>
        </div>

        {showCatalogFacts && (
          <div className="card">
            {catalogFacts.length === 0 ? (
              <p className="metric-hint">
                Zu diesem Programm sind keine Konditionen hinterlegt. Unter „Förderungen“ lassen sie
                sich über „Konditionen“ eintragen — danach stehen sie hier.
              </p>
            ) : (
              <div className="txn-table-wrapper">
                <table className="txn-table">
                  <tbody>
                    {catalogFacts.map((fact) => (
                      <tr className="txn-tr" key={`${fact.label}-${fact.value}`}>
                        <td className="txn-td">{fact.label}</td>
                        <td className="txn-td txn-td--amount-wide">
                          {fact.value}
                          {fact.hint && <div className="metric-hint">{fact.hint}</div>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {catalogNote && (
              <p className="metric-hint" style={{ marginTop: "var(--space-2)" }}>
                {catalogNote}
              </p>
            )}
          </div>
        )}

        {!isGrant && variants.length > 0 && (
          <fieldset className="funding-variant-section">
            <legend>Konditionsvariante auswählen</legend>
            <p className="metric-hint">
              Jede Option hält Laufzeit, Anlaufzeit und KfW-Zins zusammen. Die drei
              Annuitätenmodelle sind Alternativen – keine Aufteilung des Darlehens.
            </p>
            <div
              className="funding-variant-list"
              role="radiogroup"
              aria-label="KfW-Konditionsvarianten"
            >
              {variants.map((variant, index) => {
                const selected = selectedVariantId === variant.id;
                const startsBulletGroup =
                  variant.repaymentType === "bullet" &&
                  variants[index - 1]?.repaymentType !== "bullet";
                return (
                  <label
                    key={variant.id}
                    className={`funding-variant-option${selected ? " funding-variant-option--selected" : ""}${startsBulletGroup ? " funding-variant-option--separate" : ""}`}
                  >
                    <input
                      type="radio"
                      name="funding-term-variant"
                      value={variant.id}
                      checked={selected}
                      onChange={() => selectVariant(variant.id)}
                    />
                    <span className="funding-variant-copy">
                      <strong>{variant.label}</strong>
                      <span>
                        {variant.repaymentType === "annuity"
                          ? `10 Jahre Zinsbindung · ${variant.minRepaymentFreeMonths / 12}–${variant.maxRepaymentFreeMonths / 12} tilgungsfreie Jahre`
                          : "10 Jahre Zinsbindung · Nur Zinsen, Gesamttilgung am Ende"}
                      </span>
                    </span>
                    <span className="funding-variant-rate">
                      <strong>
                        {(variant.nominalRateBpsAnnual / 100).toLocaleString("de-DE", {
                          minimumFractionDigits: 2,
                        })}{" "}
                        %
                      </strong>
                      <span>
                        Sollzins ·{" "}
                        {(variant.effectiveRateBpsAnnual / 100).toLocaleString("de-DE", {
                          minimumFractionDigits: 2,
                        })}{" "}
                        % effektiv
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
            {rateAsOf && (
              <p className="funding-variant-asof">
                Veröffentlichter Konditionsstand: {formatDate(rateAsOf)}
              </p>
            )}
          </fieldset>
        )}

        {!isGrant && rateOptions.length > 0 && (
          <fieldset className="funding-variant-section">
            <legend>Zinsbindung auswählen</legend>
            <p className="metric-hint">
              Soll- und Effektivzins werden gemeinsam aus dem offiziellen Konditionsstand
              übernommen. Eine Gesamtlaufzeit gibt die BayernLabo hier nicht vor.
            </p>
            <div
              className="funding-variant-list"
              role="radiogroup"
              aria-label="BayernLabo-Zinsbindungsoptionen"
            >
              {rateOptions.map((option) => {
                const selected = selectedRateOptionId === option.id;
                return (
                  <label
                    key={option.id}
                    className={`funding-variant-option${selected ? " funding-variant-option--selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name="funding-rate-option"
                      value={option.id}
                      checked={selected}
                      onChange={() => selectRateOption(option.id)}
                    />
                    <span className="funding-variant-copy">
                      <strong>{option.label}</strong>
                      <span>{option.fixedRateMonths / 12} Jahre festgeschrieben</span>
                    </span>
                    <span className="funding-variant-rate">
                      <strong>
                        {(option.nominalRateBpsAnnual / 100).toLocaleString("de-DE", {
                          minimumFractionDigits: 2,
                        })}{" "}
                        %
                      </strong>
                      <span>
                        nominal ·{" "}
                        {(option.effectiveRateBpsAnnual / 100).toLocaleString("de-DE", {
                          minimumFractionDigits: 2,
                        })}{" "}
                        % effektiv
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
            {rateAsOf && (
              <p className="funding-variant-asof">
                Veröffentlichter Konditionsstand: {formatDate(rateAsOf)}
              </p>
            )}
          </fieldset>
        )}

        {selectedVariant && (
          <div className="funding-variant-selection">
            <div className="form-field">
              <label className="form-label" htmlFor="funding-term-months">
                Gesamtlaufzeit
              </label>
              <select
                id="funding-term-months"
                className="form-input form-input--mono"
                value={termMonths}
                onChange={(event) => setTermMonths(event.target.value)}
                required
              >
                <option value="">Bitte auswählen</option>
                {Array.from(
                  {
                    length:
                      (selectedVariant.maxTermMonths - selectedVariant.minTermMonths) / 12 + 1,
                  },
                  (_, index) => selectedVariant.minTermMonths + index * 12,
                ).map((months) => (
                  <option key={months} value={months}>
                    {months / 12} Jahre
                  </option>
                ))}
              </select>
            </div>
            {selectedVariant.repaymentType === "annuity" ? (
              <div className="form-field">
                <label className="form-label" htmlFor="funding-free">
                  Tilgungsfreie Anlaufzeit
                </label>
                <select
                  id="funding-free"
                  className="form-input form-input--mono"
                  value={repaymentFreeMonths}
                  onChange={(event) => setRepaymentFreeMonths(event.target.value)}
                  required
                >
                  <option value="">Bitte auswählen</option>
                  {Array.from(
                    {
                      length:
                        (selectedVariant.maxRepaymentFreeMonths -
                          selectedVariant.minRepaymentFreeMonths) /
                          12 +
                        1,
                    },
                    (_, index) => selectedVariant.minRepaymentFreeMonths + index * 12,
                  ).map((months) => (
                    <option key={months} value={months}>
                      {months / 12} {months === 12 ? "Jahr" : "Jahre"}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div className="funding-bullet-explanation">
                <strong>Endfällige Rückzahlung</strong>
                <span>Während der Laufzeit nur Zinsen, Gesamttilgung im letzten Monat.</span>
              </div>
            )}
          </div>
        )}

        <div
          className="funding-conditions-grid"
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}
        >
          <div className="form-field">
            <label className="form-label" htmlFor="funding-amount">
              {isGrant ? "Zuschuss (€)" : "Darlehensbetrag (€)"}
            </label>
            <input
              id="funding-amount"
              className="form-input form-input--mono"
              value={amountEuro}
              onChange={(e) => setAmountEuro(e.target.value)}
              required
            />
            {catalogAmount !== null && (
              <span className="metric-hint">Katalog: {formatMinorUnitsAsEuro(catalogAmount)}</span>
            )}
          </div>
          {!isGrant && (
            <div className="form-field">
              <label className="form-label" htmlFor="funding-rate">
                Sollzins (% p. a.)
              </label>
              <input
                id="funding-rate"
                className="form-input form-input--mono"
                value={ratePercent}
                onChange={(e) => setRatePercent(e.target.value)}
                placeholder="z. B. 3,12"
                readOnly={Boolean(selectedVariant || selectedRateOption)}
                // Pflicht bei jedem Darlehen: ohne Zinssatz entstand zwar die
                // Finanzierungsquelle, aber kein Tilgungsplan - und zwar
                // stillschweigend. Der fehlende Plan fiel erst viel spaeter
                // auf. Lieber hier eine klare Pflichtangabe als spaeter eine
                // Quelle ohne Verlauf.
                required
              />
              {(selectedVariant || selectedRateOption) && (
                <span className="metric-hint">
                  Automatisch aus der gewählten Kondition · Effektivzins{" "}
                  {(
                    (selectedVariant ?? selectedRateOption)!.effectiveRateBpsAnnual / 100
                  ).toLocaleString("de-DE", { minimumFractionDigits: 2 })}{" "}
                  %
                </span>
              )}
              {catalogRateBps === null && rateOptions.length === 0 && (
                <span className="metric-hint">
                  Der Katalog nennt keinen Zins — Förderbanken setzen ihn täglich neu fest. Einmal
                  unter „Förderungen“ eingetragen, steht er hier künftig.
                </span>
              )}
            </div>
          )}
        </div>

        {!isGrant && (
          <>
            {!selectedVariant && !selectedRateOption && (
              <div
                className="funding-conditions-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr",
                  gap: "var(--space-3)",
                }}
              >
                <div className="form-field">
                  <label className="form-label" htmlFor="funding-repayment">
                    Anfangstilgung (%)
                  </label>
                  <input
                    id="funding-repayment"
                    className="form-input form-input--mono"
                    value={repaymentPercent}
                    onChange={(e) => setRepaymentPercent(e.target.value)}
                  />
                </div>
                <div className="form-field">
                  <label className="form-label" htmlFor="funding-free">
                    Tilgungsfrei (Monate)
                  </label>
                  <input
                    id="funding-free"
                    className="form-input form-input--mono"
                    value={repaymentFreeMonths}
                    onChange={(e) => setRepaymentFreeMonths(e.target.value)}
                  />
                  {catalogFreeMonths !== null && (
                    <span className="metric-hint">Katalog erlaubt bis {catalogFreeMonths}</span>
                  )}
                </div>
                <div className="form-field">
                  <label className="form-label" htmlFor="funding-fixed">
                    Zinsbindung (Monate)
                  </label>
                  <input
                    id="funding-fixed"
                    className="form-input form-input--mono"
                    value={fixedRateMonths}
                    onChange={(e) => setFixedRateMonths(e.target.value)}
                  />
                </div>
              </div>
            )}
            {selectedRateOption && (
              <div className="funding-variant-selection">
                <div>
                  <span className="form-label">Anfängliche Tilgung</span>
                  <strong className="form-input--mono">{repaymentPercent} %</strong>
                  <p className="metric-hint">Automatisch aus dem BayernLabo-Stand</p>
                </div>
                <div>
                  <span className="form-label">Tilgungsfreie Anlaufzeit</span>
                  <strong className="form-input--mono">{repaymentFreeMonths} Monate</strong>
                  <p className="metric-hint">Im Katalog hinterlegte Höchstdauer</p>
                </div>
              </div>
            )}
            <div
              className="funding-conditions-grid"
              style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}
            >
              {(!selectedVariant || Number(termMonths) > selectedVariant.fixedRateMonths) && (
                <div className="form-field">
                  <label className="form-label" htmlFor="funding-followup">
                    Anschlusszins nach der Bindung (%) – Planungsannahme
                  </label>
                  <input
                    id="funding-followup"
                    className="form-input form-input--mono"
                    value={followUpPercent}
                    onChange={(e) => setFollowUpPercent(e.target.value)}
                  />
                  {(selectedVariant || selectedRateOption) && (
                    <span className="metric-hint">
                      Änderbare Annahme ab Jahr{" "}
                      {(selectedVariant ?? selectedRateOption)!.fixedRateMonths / 12}; keine
                      Förderbank-Zusage.
                    </span>
                  )}
                </div>
              )}
              <div className="form-field">
                <label className="form-label" htmlFor="funding-start">
                  Auszahlung am
                </label>
                <input
                  id="funding-start"
                  type="date"
                  className="form-input"
                  value={startsOn}
                  onChange={(e) => setStartsOn(e.target.value)}
                />
              </div>
            </div>
            <fieldset className="funding-variant-section">
              <legend>Wie wird der Kredit ausgezahlt?</legend>
              <div className="loan-choice-row">
                <label
                  className={`loan-choice loan-choice--stacked ${drawMode === "cost_plan" ? "loan-choice--active" : ""}`}
                >
                  <input
                    type="radio"
                    name="funding-draw-mode"
                    checked={drawMode === "cost_plan"}
                    onChange={() => setDrawMode("cost_plan")}
                  />
                  <span>
                    <strong>Nach Kostenplan abrufen</strong>
                    <small>Kauf zuerst, spätere Maßnahmen in ihren Fälligkeitsmonaten</small>
                  </span>
                </label>
                <label
                  className={`loan-choice loan-choice--stacked ${drawMode === "immediate" ? "loan-choice--active" : ""}`}
                >
                  <input
                    type="radio"
                    name="funding-draw-mode"
                    checked={drawMode === "immediate"}
                    onChange={() => setDrawMode("immediate")}
                  />
                  <span>
                    <strong>Gesamter Betrag sofort</strong>
                    <small>Komplette Auszahlung zum Kreditstart</small>
                  </span>
                </label>
              </div>
            </fieldset>
            {drawMode === "cost_plan" && (
              <div className="draw-preview" aria-live="polite">
                <strong>Automatische Abruf-Vorschau</strong>
                {costPreview.isFetching && !costPreview.data && (
                  <p className="metric-hint">Abrufe werden für die aktuellen Eingaben berechnet…</p>
                )}
                {costPreview.data && (
                  <>
                    <ol className="draw-timeline" aria-label="Abrufe entlang des Kostenplans">
                      {(costPreview.data.draws ?? []).map((draw, index) => (
                        <li key={`${draw.occurs_after_month}-${index}`}>
                          <span className="draw-timeline__dot" />
                          <span>
                            {draw.occurs_after_month === 0
                              ? "Kauf / Start"
                              : `Jahr ${Math.floor(draw.occurs_after_month / 12) + 1}`}
                          </span>
                          <strong>{formatMinorUnitsAsEuro(draw.amount_minor)}</strong>
                        </li>
                      ))}
                    </ol>
                    <div className="draw-preview__balance">
                      <span>
                        Belegt: {formatMinorUnitsAsEuro(costPreview.data.allocated_minor ?? 0)}
                      </span>
                      <span>
                        Restmittel:{" "}
                        {formatMinorUnitsAsEuro(costPreview.data.unallocated_credit_minor ?? 0)}
                      </span>
                      <span
                        className={
                          (costPreview.data.uncovered_cost_minor ?? 0) > 0 ? "status-error" : ""
                        }
                      >
                        Ungedeckte Kosten:{" "}
                        {formatMinorUnitsAsEuro(costPreview.data.uncovered_cost_minor ?? 0)}
                      </span>
                    </div>
                  </>
                )}
                {costPreview.isError && (
                  <p className="field-error" role="alert">
                    Abruf-Vorschau konnte nicht berechnet werden.
                  </p>
                )}
              </div>
            )}
          </>
        )}

        {/* Ohne Zinssatz laesst sich kein Verlauf rechnen. Frueher entstand
            dann stillschweigend nur die Quelle, und der fehlende Tilgungsplan
            fiel erst spaeter auf - deshalb steht es jetzt vorher da. */}
        {!isGrant && rateBps === null && (
          <p className="metric-hint">
            Ohne Sollzins wird <strong>kein Tilgungsplan</strong> angelegt — die Quelle erscheint
            dann nur mit ihrem Betrag in der Finanzierung. Du kannst den Plan später über
            „Tilgungsplan anlegen“ in der Zeile nachtragen oder den Zins einmalig unter
            „Förderungen“ hinterlegen.
          </p>
        )}

        {preview && (
          <div className="card" style={{ background: "var(--color-surface-raised, transparent)" }}>
            <h3 className="card-heading">So schlägt das zu Buche</h3>
            <div className="metric-grid">
              <div className="metric-card">
                <span className="metric-label">Monatsrate</span>
                <span className="metric-value">{formatMinorUnitsAsEuro(preview.paymentMinor)}</span>
                {preview.paymentDuringRepaymentFreeMinor > 0 && (
                  <span className="metric-hint">
                    Erst {formatMinorUnitsAsEuro(preview.paymentDuringRepaymentFreeMinor)} während
                    der tilgungsfreien Zeit
                  </span>
                )}
              </div>
              <div className="metric-card">
                <span className="metric-label">Im Jahr</span>
                <span className="metric-value">
                  {formatMinorUnitsAsEuro(preview.yearlyPaymentMinor)}
                </span>
              </div>
              <div className="metric-card">
                <span className="metric-label">Zinsen bis Bindungsende</span>
                <span className="metric-value">
                  {formatMinorUnitsAsEuro(preview.interestUntilFixedEndMinor)}
                </span>
              </div>
              <div className="metric-card">
                <span className="metric-label">Restschuld danach</span>
                <span className="metric-value">
                  {formatMinorUnitsAsEuro(preview.balanceAtFixedEndMinor)}
                </span>
                <span className="metric-hint">
                  {preview.payoffMonth === null
                    ? "Anschlussfinanzierung nötig"
                    : `Getilgt nach ${Math.floor(preview.payoffMonth / 12)} Jahren`}
                </span>
              </div>
            </div>
            <p className="metric-hint" style={{ marginTop: "var(--space-2)" }}>
              Überschlag ohne Abrufplan, Gebühren und Sondertilgungen. Der verbindliche Verlauf
              steht nach dem Speichern unter „Verlauf“.
            </p>
          </div>
        )}

        {(createConfiguration.isError || createGrantSource.isError) && (
          <p className="status-error" role="alert">
            Förderung konnte nicht angelegt werden.
          </p>
        )}

        <div className="funding-dialog-actions">
          <button type="button" className="btn-secondary" onClick={onBack}>
            Zurück zur Liste
          </button>
          <button
            type="submit"
            className="btn-primary"
            disabled={
              isPending ||
              (variants.length > 0 && (!selectedVariant || !termMonths || !repaymentFreeMonths)) ||
              (rateOptions.length > 0 && !selectedRateOption)
            }
          >
            Als Finanzierungsquelle übernehmen
          </button>
        </div>
      </form>
    </section>
  );
}
