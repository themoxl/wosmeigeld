import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import { coveragePercent } from "./coverage";
import type {
  Account,
  Contract,
  ForecastResponse,
  ForecastSettings,
  ForecastSettingsInput,
  ForecastVariableBasis,
} from "../../lib/types";

// Einrichtung der Vorschau (Plan Stufe 5b).
//
// Die Vorschau darf nicht wortlos mit schlechten Eingaben erscheinen: ohne
// Kontenauswahl, ohne gepflegte Einnahmen und ohne geprueftes Jahresraster
// rechnet sie zwar, aber sie rechnet Unsinn. Fuenf Schritte, jeder mit genau
// einer Entscheidung.

const STEPS = ["Konten", "Einnahmen", "Puffer", "Variable Ausgaben", "Jahreszahlungen"] as const;

const BASIS_OPTIONS: { value: ForecastVariableBasis; label: string; hint: string }[] = [
  { value: "budget", label: "Budgetplan", hint: "was du dir vorgenommen hast" },
  { value: "history", label: "Historie", hint: "was du tatsächlich ausgegeben hast" },
  { value: "max", label: "der größere Wert", hint: "vorsichtig gerechnet" },
];

export interface ForecastSetupProps {
  accounts: Account[];
  contracts: Contract[];
  settings: ForecastSettings;
  forecast: ForecastResponse;
  saving: boolean;
  onToggleAccount: (account: Account, liquid: boolean) => void;
  onFinish: (settings: ForecastSettingsInput) => void;
  onSkip: () => void;
}

export function ForecastSetup({
  accounts,
  contracts,
  settings,
  forecast,
  saving,
  onToggleAccount,
  onFinish,
  onSkip,
}: ForecastSetupProps) {
  const [step, setStep] = useState(0);
  const [bufferText, setBufferText] = useState(
    (settings.safety_buffer_minor / 100).toLocaleString("de-DE", { minimumFractionDigits: 2 }),
  );
  const [basis, setBasis] = useState<ForecastVariableBasis>(settings.variable_basis);

  const incomeContracts = useMemo(
    () => contracts.filter((c) => c.direction === "income"),
    [contracts],
  );
  const periodic = useMemo(
    () => contracts.filter((c) => c.direction === "expense" && c.frequency !== "monthly"),
    [contracts],
  );
  const liquid = accounts.filter(
    (a) => a.forecast_liquidity ?? ["checking", "cash"].includes(a.account_type),
  );

  const finish = () => {
    const buffer = parseAmountToMinorUnits(bufferText) ?? 0;
    onFinish({
      safety_buffer_minor: Math.max(0, buffer),
      variable_basis: basis,
      variable_lookback_months: settings.variable_lookback_months,
      horizon_months: settings.horizon_months,
    });
  };

  return (
    <div className="card forecast-setup" data-testid="forecast-setup">
      <div className="forecast-setup-head">
        <h2 className="card-heading">Vorschau einrichten</h2>
        <button type="button" className="btn-secondary" onClick={onSkip}>
          Später
        </button>
      </div>

      <ol className="forecast-setup-steps">
        {STEPS.map((label, index) => (
          <li key={label} className={index === step ? "forecast-setup-step--active" : ""}>
            <span className="forecast-setup-step-no">{index + 1}</span>
            {label}
          </li>
        ))}
      </ol>

      <div className="forecast-setup-body">
        {step === 0 ? (
          <>
            <p className="card-note">Welche Konten sind sofort verfügbar?</p>
            <ul className="forecast-setup-list">
              {accounts.map((account) => {
                const isLiquid =
                  account.forecast_liquidity ?? ["checking", "cash"].includes(account.account_type);
                return (
                  <li key={account.id}>
                    <label>
                      <input
                        type="checkbox"
                        checked={isLiquid}
                        onChange={(event) => onToggleAccount(account, event.target.checked)}
                      />
                      <span>{account.name}</span>
                      <span className="forecast-setup-meta">{account.account_type}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
            {liquid.length === 0 ? (
              <p className="forecast-row-error" role="alert">
                Ohne ein verfügbares Konto kann die Vorschau nicht rechnen.
              </p>
            ) : null}
          </>
        ) : null}

        {step === 1 ? (
          <>
            <p className="card-note">Erkannte regelmäßige Einnahmen:</p>
            {incomeContracts.length === 0 ? (
              <p className="forecast-row-error" role="alert">
                Keine Einnahme gepflegt — die Vorschau kennt dann nur Ausgaben.{" "}
                <Link to="/fixkosten?richtung=einnahmen">Jetzt anlegen</Link>
              </p>
            ) : (
              <ul className="forecast-setup-list">
                {incomeContracts.map((c) => (
                  <li key={c.id}>
                    <span>{c.name}</span>
                    <span className="forecast-setup-meta">
                      {formatMinorUnitsAsEuro(c.amount_minor)} · {c.frequency}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="card-note">
              <Link to="/fixkosten?richtung=einnahmen">Einnahmen bearbeiten</Link>
            </p>
          </>
        ) : null}

        {step === 2 ? (
          <>
            <p className="card-note">
              Unterschreitung ist eine Warnung, kein Fehler. 0 € schaltet sie ab.
            </p>
            <label className="forecast-field">
              <span className="forecast-field-label">Sicherheitspuffer</span>
              <span className="forecast-field-unit">
                <input
                  type="text"
                  className="form-input form-input--mono"
                  value={bufferText}
                  inputMode="decimal"
                  aria-label="Sicherheitspuffer"
                  onChange={(event) => setBufferText(event.target.value)}
                />
                <span aria-hidden="true">€</span>
              </span>
            </label>
          </>
        ) : null}

        {step === 3 ? (
          <>
            <p className="card-note">Woher kommen Ausgaben ohne Vertrag?</p>
            <div className="forecast-setup-choices">
              {BASIS_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className={basis === option.value ? "forecast-choice forecast-choice--active" : "forecast-choice"}
                >
                  <input
                    type="radio"
                    name="variable-basis"
                    value={option.value}
                    checked={basis === option.value}
                    onChange={() => setBasis(option.value)}
                  />
                  <span className="forecast-choice-label">{option.label}</span>
                  <span className="forecast-setup-meta">{option.hint}</span>
                </label>
              ))}
            </div>
          </>
        ) : null}

        {step === 4 ? (
          <>
            <p className="card-note">
              Jahres- und Quartalszahlungen entscheiden über den Engpass. Stimmen Betrag und
              Fälligkeit?
            </p>
            {periodic.length === 0 ? (
              <p className="placeholder-note">Keine erkannt.</p>
            ) : (
              <ul className="forecast-setup-list">
                {periodic.map((c) => (
                  <li key={c.id}>
                    <span>{c.name}</span>
                    <span className="forecast-setup-meta">
                      {formatMinorUnitsAsEuro(c.amount_minor)} · {c.frequency} · nächste Fälligkeit{" "}
                      {c.next_due_date}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="forecast-setup-verdict">
              Die Vorschau kennt <strong>{coveragePercent(forecast)} %</strong> deiner regelmäßigen
              Zahlungsströme.
            </p>
          </>
        ) : null}
      </div>

      <div className="forecast-setup-actions">
        <button
          type="button"
          className="btn-secondary"
          disabled={step === 0}
          onClick={() => setStep((s) => Math.max(0, s - 1))}
        >
          Zurück
        </button>
        {step < STEPS.length - 1 ? (
          <button type="button" className="btn-primary" onClick={() => setStep((s) => s + 1)}>
            Weiter
          </button>
        ) : (
          <button type="button" className="btn-primary" disabled={saving} onClick={finish}>
            {saving ? "Wird gespeichert…" : "Fertig"}
          </button>
        )}
      </div>
    </div>
  );
}
