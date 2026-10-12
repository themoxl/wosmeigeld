import { useState } from "react";
import {
  useAdoptFundingMatch,
  useEligibilityProfile,
  useEvaluateFundingCombinations,
  useFundingMatches,
  useFundingProgram,
  useRunFundingCheck,
  useSaveEligibilityProfile,
} from "../../hooks/useFunding";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import {
  APPLICANT_TYPE_OPTIONS,
  FUNDING_STATUS_BADGE_CLASS,
  FUNDING_STATUS_LABELS,
} from "../../lib/funding";
import type { EligibilityProfile } from "../../lib/types";
import type { FundingMatch } from "../../lib/types";
import { loanRateOptions, loanTermVariants } from "../../lib/fundingTerms";
import { Modal } from "../Modal";

interface FundingPanelProps {
  projectId: string;
  scenarioId: string;
}

export function FundingPanel({ projectId, scenarioId }: FundingPanelProps) {
  const { data: profile, isPending } = useEligibilityProfile(projectId);

  if (isPending) {
    return <p className="placeholder-note">Lade Förderprofil...</p>;
  }

  // Der Zustand des Formulars wird aus dem geladenen Profil vorbelegt. Deshalb erst
  // rendern, wenn die Abfrage entschieden ist -- sonst startet jedes Feld leer und
  // ueberschreibt beim Speichern still die gespeicherten Angaben.
  return (
    <FundingPanelBody
      key={projectId}
      projectId={projectId}
      scenarioId={scenarioId}
      profile={profile ?? null}
    />
  );
}

/** Leere Zeichenkette heisst "nicht angegeben" -- ein solches Feld wird nicht als
 * `null` gesendet, sondern weggelassen, damit die Engine zwischen "keine Angabe"
 * und "bewusst Null" unterscheiden kann. */
function optionalNumber(raw: string): number | undefined {
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  const parsed = Number(trimmed.replace(",", "."));
  return Number.isFinite(parsed) ? parsed : undefined;
}

function FundingPanelBody({
  projectId,
  scenarioId,
  profile,
}: FundingPanelProps & { profile: EligibilityProfile | null }) {
  const { data: lastRun } = useFundingMatches(projectId, scenarioId);
  const saveProfile = useSaveEligibilityProfile();
  const runCheck = useRunFundingCheck();
  const evaluateCombinations = useEvaluateFundingCombinations();

  const facts = (profile?.facts ?? {}) as Record<string, unknown>;
  const [applicantType, setApplicantType] = useState(String(facts.applicant_type ?? ""));
  const [applicationDate, setApplicationDate] = useState(String(facts.application_date ?? ""));
  const [incomeEuro, setIncomeEuro] = useState(
    facts.household_income_year_minor
      ? String(Number(facts.household_income_year_minor) / 100).replace(".", ",")
      : "",
  );
  const [childrenCount, setChildrenCount] = useState(
    facts.children_count !== undefined ? String(facts.children_count) : "0",
  );
  const [disabilityPercent, setDisabilityPercent] = useState(
    facts.disability_severity_percent !== undefined
      ? String(facts.disability_severity_percent)
      : "",
  );
  const [selfOccupied, setSelfOccupied] = useState(Boolean(facts.self_occupied));
  const [existingHomeowner, setExistingHomeowner] = useState(Boolean(facts.existing_homeowner));
  const [confirmations, setConfirmations] = useState<string[]>(profile?.manual_confirmations ?? []);

  const [selectedMatchIds, setSelectedMatchIds] = useState<string[]>([]);
  const [combinationResult, setCombinationResult] = useState<string | null>(null);

  const collectFacts = () => {
    // `facts` ist serverseitig ein offenes Dict (Plan 4.10); openapi-typescript
    // kann dafuer keinen praezisen TS-Typ ableiten (Record<string, never>).
    const collected: Record<string, unknown> = {
      self_occupied: selfOccupied,
      existing_homeowner: existingHomeowner,
      children_count: optionalNumber(childrenCount) ?? 0,
    };
    if (applicantType) collected.applicant_type = applicantType;
    if (applicationDate) collected.application_date = applicationDate;
    const income = parseAmountToMinorUnits(incomeEuro);
    if (income !== null) collected.household_income_year_minor = income;
    const disability = optionalNumber(disabilityPercent);
    if (disability !== undefined) collected.disability_severity_percent = disability;
    return collected as unknown as Record<string, never>;
  };

  const handleSaveProfile = (e: React.FormEvent) => {
    e.preventDefault();
    saveProfile.mutate({
      projectId,
      input: { facts: collectFacts(), manual_confirmations: confirmations },
    });
  };

  /** Ein Haken bestaetigt eine Tatsache, keine Foerderzusage. Nach dem Speichern
   * laeuft der Check erneut, damit der Treffer sofort den neuen Stand zeigt. */
  const toggleConfirmation = (key: string) => {
    const next = confirmations.includes(key)
      ? confirmations.filter((entry) => entry !== key)
      : [...confirmations, key];
    setConfirmations(next);
    saveProfile.mutate(
      { projectId, input: { facts: collectFacts(), manual_confirmations: next } },
      { onSuccess: () => runCheck.mutate({ projectId, scenarioId }) },
    );
  };

  const toggleMatchSelection = (matchId: string) => {
    setSelectedMatchIds((prev) =>
      prev.includes(matchId) ? prev.filter((id) => id !== matchId) : [...prev, matchId],
    );
    setCombinationResult(null);
  };

  const handleEvaluateCombinations = () => {
    if (selectedMatchIds.length < 2) return;
    evaluateCombinations.mutate(
      { projectId, scenarioId, input: { match_ids: selectedMatchIds } },
      {
        onSuccess: (result) => {
          const summary = result.combinations
            .map((c) => `${c.program_titles.join(" + ")}: ${c.status}`)
            .join("; ");
          setCombinationResult(summary || "Keine Kombination auswertbar.");
        },
      },
    );
  };

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      <div className="card">
        <h2 className="card-heading">Förderprofil</h2>
        <p className="metric-hint">
          Diese Angaben verlassen die Installation nie an Förderstellen oder ein KI-Modell - das
          Matching läuft vollständig lokal gegen den Katalog (Plan 6.8).
        </p>
        <form
          onSubmit={handleSaveProfile}
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: "var(--space-3)",
          }}
        >
          <div className="form-field">
            <label className="form-label" htmlFor="funding-applicant-type">
              Antragstellerart
            </label>
            <select
              id="funding-applicant-type"
              className="form-input"
              value={applicantType}
              onChange={(e) => setApplicantType(e.target.value)}
            >
              <option value="">Keine Angabe</option>
              {APPLICANT_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="funding-application-date">
              Geplantes Antragsdatum
            </label>
            <input
              id="funding-application-date"
              type="date"
              className="form-input"
              value={applicationDate}
              onChange={(e) => setApplicationDate(e.target.value)}
            />
            <p className="metric-hint">
              Entscheidet, welche Programmfassung gilt. Ohne Angabe rechnet der Check mit heute.
            </p>
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="funding-income">
              Jahreshaushaltseinkommen (€)
            </label>
            <input
              id="funding-income"
              className="form-input form-input--mono"
              value={incomeEuro}
              onChange={(e) => setIncomeEuro(e.target.value)}
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="funding-children">
              Anzahl Kinder im Haushalt
            </label>
            <input
              id="funding-children"
              className="form-input form-input--mono"
              value={childrenCount}
              onChange={(e) => setChildrenCount(e.target.value)}
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="funding-disability">
              Grad der Behinderung (%)
            </label>
            <input
              id="funding-disability"
              className="form-input form-input--mono"
              value={disabilityPercent}
              onChange={(e) => setDisabilityPercent(e.target.value)}
              placeholder="optional"
            />
          </div>
          <div className="form-field" style={{ gridColumn: "1 / -1" }}>
            <label className="form-label">
              <input
                type="checkbox"
                checked={selfOccupied}
                onChange={(e) => setSelfOccupied(e.target.checked)}
                style={{ marginRight: "var(--space-2)" }}
              />
              Immobilie wird nach Erwerb/Bau selbst genutzt
            </label>
            <label className="form-label">
              <input
                type="checkbox"
                checked={existingHomeowner}
                onChange={(e) => setExistingHomeowner(e.target.checked)}
                style={{ marginRight: "var(--space-2)" }}
              />
              Es besteht bereits Wohneigentum
            </label>
          </div>
          {saveProfile.isError && (
            <p className="status-error" role="alert" style={{ gridColumn: "1 / -1" }}>
              Förderprofil konnte nicht gespeichert werden.
            </p>
          )}
          <button
            type="submit"
            className="btn-secondary"
            disabled={saveProfile.isPending}
            style={{ gridColumn: "1 / -1", justifySelf: "start" }}
          >
            Profil speichern
          </button>
        </form>
      </div>

      <div className="card" style={{ marginTop: "var(--space-4)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 className="card-heading">Fördercheck</h2>
          <button
            type="button"
            className="btn-primary"
            onClick={() => runCheck.mutate({ projectId, scenarioId })}
            disabled={runCheck.isPending}
          >
            Förderung prüfen
          </button>
        </div>
        {runCheck.isError && (
          <p className="status-error" role="alert">
            Fördercheck fehlgeschlagen.
          </p>
        )}

        {lastRun && lastRun.matches.length > 0 && (
          <>
            <div className="txn-table-wrapper">
              <table className="txn-table">
                <thead>
                  <tr>
                    <th className="txn-th"></th>
                    <th className="txn-th">Programm</th>
                    <th className="txn-th txn-th--status">Status</th>
                    <th className="txn-th txn-th--amount-wide">Zuschuss</th>
                    <th className="txn-th txn-th--amount-wide">Darlehen</th>
                    <th className="txn-th txn-th--actions-wide"></th>
                  </tr>
                </thead>
                <tbody>
                  {lastRun.matches.map((match) => (
                    <tr className="txn-tr" key={match.id}>
                      <td className="txn-td">
                        <input
                          type="checkbox"
                          checked={selectedMatchIds.includes(match.id)}
                          onChange={() => toggleMatchSelection(match.id)}
                          aria-label={`${match.program_title} für Kombinationsprüfung auswählen`}
                        />
                      </td>
                      <td className="txn-td">
                        <span className="txn-payee">{match.program_title}</span>
                        <div className="metric-hint">{match.provider_name}</div>
                        {match.criteria_manual.length > 0 && (
                          <div style={{ marginTop: "var(--space-2)" }}>
                            <div className="metric-hint">
                              Selbst zu prüfen — ein Haken bestätigt, dass die Bedingung auf Sie
                              zutrifft. Er ist keine Förderzusage:
                            </div>
                            {match.criteria_manual.map((criterion) => (
                              <label
                                key={criterion.key}
                                className="form-label"
                                style={{ display: "block", fontWeight: "normal" }}
                              >
                                <input
                                  type="checkbox"
                                  checked={confirmations.includes(criterion.key)}
                                  onChange={() => toggleConfirmation(criterion.key)}
                                  disabled={saveProfile.isPending || runCheck.isPending}
                                  style={{ marginRight: "var(--space-2)" }}
                                />
                                {criterion.label}
                              </label>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="txn-td txn-td--status">
                        <span
                          className={`status-badge ${FUNDING_STATUS_BADGE_CLASS[match.status]}`}
                        >
                          {FUNDING_STATUS_LABELS[match.status]}
                        </span>
                      </td>
                      <td className="txn-td txn-td--amount-wide">
                        {match.estimated_grant_max_minor != null
                          ? formatMinorUnitsAsEuro(match.estimated_grant_max_minor)
                          : "—"}
                      </td>
                      <td className="txn-td txn-td--amount-wide">
                        {match.estimated_loan_max_minor != null
                          ? formatMinorUnitsAsEuro(match.estimated_loan_max_minor)
                          : "—"}
                      </td>
                      <td className="txn-td txn-td--actions-wide">
                        <AdoptFundingMatchButton
                          projectId={projectId}
                          scenarioId={scenarioId}
                          match={match}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: "var(--space-3)" }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={handleEvaluateCombinations}
                disabled={selectedMatchIds.length < 2 || evaluateCombinations.isPending}
              >
                Kombinierbarkeit der ausgewählten Programme prüfen
              </button>
              {combinationResult && <p className="metric-hint">{combinationResult}</p>}
            </div>
            <p className="metric-hint">
              Diese Prüfung sagt, was dir zusteht — sie fügt dem Vorhaben kein Geld hinzu. Das
              passiert im Vorhaben unter „Finanzierung“ über „+ Förderung“, wo du Betrag und
              Konditionen festlegst und sofort siehst, was es monatlich kostet.
            </p>
          </>
        )}
        {lastRun && lastRun.matches.length === 0 && (
          <p className="metric-hint">
            Kein passendes Programm für Land/Region/Projektart gefunden.
          </p>
        )}
        {!lastRun && <p className="metric-hint">Noch kein Fördercheck durchgeführt.</p>}
      </div>
    </div>
  );
}

function AdoptFundingMatchButton({
  projectId,
  scenarioId,
  match,
}: {
  projectId: string;
  scenarioId: string;
  match: FundingMatch;
}) {
  const [open, setOpen] = useState(false);
  const programQuery = useFundingProgram(open ? match.program_id : null);
  const adopt = useAdoptFundingMatch();
  const [variantId, setVariantId] = useState("");
  const [rateOptionId, setRateOptionId] = useState("");
  const [termMonths, setTermMonths] = useState("");
  const [graceMonths, setGraceMonths] = useState("");
  const [followUpPercent, setFollowUpPercent] = useState("4,5");
  const [requestId] = useState(() => crypto.randomUUID());

  const terms = (programQuery.data?.current_version?.loan_terms_json ?? {}) as Record<
    string,
    unknown
  >;
  const variants = loanTermVariants(terms);
  const rateOptions = loanRateOptions(terms);
  const rateAsOf = typeof terms.rate_as_of === "string" ? terms.rate_as_of : null;
  const selected = variants.find((variant) => variant.id === variantId) ?? null;
  const selectedRateOption = rateOptions.find((option) => option.id === rateOptionId) ?? null;
  const needsFollowUp = Boolean(
    selectedRateOption || (selected && Number(termMonths) > selected.fixedRateMonths),
  );
  const isReady =
    (variants.length === 0 && rateOptions.length === 0) ||
    Boolean(selected && termMonths && graceMonths && (!needsFollowUp || followUpPercent)) ||
    Boolean(selectedRateOption && followUpPercent);

  const chooseVariant = (id: string) => {
    const variant = variants.find((item) => item.id === id);
    setVariantId(id);
    setTermMonths("");
    setGraceMonths(variant?.repaymentType === "bullet" ? "0" : "");
  };

  const submit = () => {
    if (!isReady) return;
    adopt.mutate(
      {
        projectId,
        matchId: match.id,
        input: {
          scenario_id: scenarioId,
          request_id: requestId,
          ...(selected
            ? {
                loan_term_variant_id: selected.id,
                term_months: Number(termMonths),
                repayment_free_months: Number(graceMonths),
                follow_up_rate_bps_annual: needsFollowUp
                  ? Math.round(Number(followUpPercent.replace(",", ".")) * 100)
                  : undefined,
              }
            : selectedRateOption
              ? {
                  loan_rate_option_id: selectedRateOption.id,
                  follow_up_rate_bps_annual: Math.round(
                    Number(followUpPercent.replace(",", ".")) * 100,
                  ),
                }
              : {}),
        },
      },
      { onSuccess: () => setOpen(false) },
    );
  };

  return (
    <>
      <button
        type="button"
        className="btn-secondary"
        onClick={() => setOpen(true)}
        disabled={match.status === "ineligible"}
      >
        Übernehmen
      </button>
      {open && (
        <Modal
          title={`Förderung übernehmen: ${match.program_title}`}
          size="wide"
          onClose={() => setOpen(false)}
        >
          {programQuery.isLoading && <p className="placeholder-note">Lade Konditionsvarianten…</p>}
          {programQuery.isError && (
            <p className="status-error" role="alert">
              Programmkonditionen konnten nicht geladen werden.
            </p>
          )}
          {!programQuery.isLoading && !programQuery.isError && (
            <div className="funding-editor">
              {variants.length > 0 && (
                <fieldset className="funding-variant-section">
                  <legend>Konditionsvariante auswählen</legend>
                  <div className="funding-variant-list" role="radiogroup">
                    {variants.map((variant) => (
                      <label
                        className={`funding-variant-option${variant.id === variantId ? " funding-variant-option--selected" : ""}`}
                        key={variant.id}
                      >
                        <input
                          type="radio"
                          name={`match-variant-${match.id}`}
                          checked={variant.id === variantId}
                          onChange={() => chooseVariant(variant.id)}
                        />
                        <span className="funding-variant-copy">
                          <strong>{variant.label}</strong>
                          <span>
                            {variant.repaymentType === "bullet"
                              ? "10 Jahre Zinsbindung · Nur Zinsen, Gesamttilgung am Ende"
                              : `10 Jahre Zinsbindung · ${variant.minRepaymentFreeMonths / 12}–${variant.maxRepaymentFreeMonths / 12} tilgungsfreie Jahre`}
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
                    ))}
                  </div>
                  {rateAsOf && (
                    <p className="funding-variant-asof">
                      Veröffentlichter Konditionsstand:{" "}
                      {new Date(rateAsOf).toLocaleDateString("de-DE")}
                    </p>
                  )}
                </fieldset>
              )}
              {rateOptions.length > 0 && (
                <fieldset className="funding-variant-section">
                  <legend>Zinsbindung auswählen</legend>
                  <p className="metric-hint">
                    Die Kondition wird automatisch übernommen; eine Gesamtlaufzeit ist nicht
                    vorgegeben.
                  </p>
                  <div className="funding-variant-list" role="radiogroup">
                    {rateOptions.map((option) => (
                      <label
                        className={`funding-variant-option${option.id === rateOptionId ? " funding-variant-option--selected" : ""}`}
                        key={option.id}
                      >
                        <input
                          type="radio"
                          name={`match-rate-option-${match.id}`}
                          checked={option.id === rateOptionId}
                          onChange={() => setRateOptionId(option.id)}
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
                    ))}
                  </div>
                  {rateAsOf && (
                    <p className="funding-variant-asof">
                      Veröffentlichter Konditionsstand:{" "}
                      {new Date(rateAsOf).toLocaleDateString("de-DE")}
                    </p>
                  )}
                </fieldset>
              )}
              {selected && (
                <div className="funding-variant-selection">
                  <label className="form-field">
                    <span className="form-label">Gesamtlaufzeit</span>
                    <select
                      className="form-input"
                      value={termMonths}
                      onChange={(event) => setTermMonths(event.target.value)}
                    >
                      <option value="">Bitte auswählen</option>
                      {Array.from(
                        {
                          length: (selected.maxTermMonths - selected.minTermMonths) / 12 + 1,
                        },
                        (_, index) => selected.minTermMonths + index * 12,
                      ).map((months) => (
                        <option key={months} value={months}>
                          {months / 12} Jahre
                        </option>
                      ))}
                    </select>
                  </label>
                  {selected.repaymentType === "annuity" && (
                    <label className="form-field">
                      <span className="form-label">Tilgungsfreie Anlaufzeit</span>
                      <select
                        className="form-input"
                        value={graceMonths}
                        onChange={(event) => setGraceMonths(event.target.value)}
                      >
                        <option value="">Bitte auswählen</option>
                        {Array.from(
                          {
                            length:
                              (selected.maxRepaymentFreeMonths - selected.minRepaymentFreeMonths) /
                                12 +
                              1,
                          },
                          (_, index) => selected.minRepaymentFreeMonths + index * 12,
                        ).map((months) => (
                          <option key={months} value={months}>
                            {months / 12} Jahre
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </div>
              )}
              {needsFollowUp && (
                <label className="form-field">
                  <span className="form-label">Anschlusszins (%) – Planungsannahme</span>
                  <input
                    className="form-input form-input--mono"
                    value={followUpPercent}
                    onChange={(event) => setFollowUpPercent(event.target.value)}
                  />
                  <span className="metric-hint">
                    Keine Förderbank-Zusage; nur für die Planung nach der Zinsbindung.
                  </span>
                </label>
              )}
              {adopt.isError && (
                <p className="status-error">Förderung konnte nicht übernommen werden.</p>
              )}
              <div className="modal-actions">
                <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
                  Abbrechen
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!isReady || adopt.isPending}
                  onClick={submit}
                >
                  Als Finanzierung übernehmen
                </button>
              </div>
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
