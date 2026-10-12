import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Chart } from "../components/charts/Chart";
import { forecastBalanceOption, forecastCompareOption } from "../components/charts/buildOptions";
import type { ForecastPoint } from "../components/charts/buildOptions";
import { ChangeEditor } from "../components/forecast/ChangeEditor";
import { ForecastSetup } from "../components/forecast/ForecastSetup";
import { coveragePercent } from "../components/forecast/coverage";
import {
  changesToDrafts,
  nextMonthValue,
  validateDraft,
  type ChangeDraft,
} from "../components/forecast/changeDrafts";
import { OpeningBreakdown } from "../components/forecast/OpeningBreakdown";
import { excludedTotalMinor, oldestIncludedBooking } from "../components/forecast/openingHelpers";
import { ScenarioBar } from "../components/forecast/ScenarioBar";
import { ScenarioComparison } from "../components/forecast/ScenarioComparison";
import { MAX_COMPARED_SCENARIOS, useScenario } from "../hooks/useScenarios";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { useFormatDate } from "../hooks/useProfile";
import {
  useForecast,
  useForecastSettings,
  useForecastSimulation,
  useForecastUpcoming,
  useUpdateForecastSettings,
} from "../hooks/useForecast";
import { ApiError, listAccounts, listContracts, updateAccount } from "../lib/api";
import { formatMinorUnitsAsEuro } from "../lib/money";
import { monthNameShort } from "../lib/dates";
import type {
  Account,
  Contract,
  ForecastDayGroup,
  ForecastResponse,
  ForecastSimulationResponse,
  ForecastWarning,
} from "../lib/types";
import "./forecast.css";

// Entscheidungshilfe, kein Dashboard: was passiert, warum, wie viel fehlt, was
// loest es. Deshalb kaum Fliesstext - Zahlen, Kette und Tabellen sagen es
// genauer als ein Satz darueber.

type Scale = "30" | "90" | "365" | "m12" | "m24";

/**
 * Ruhepause, bevor getippter Text gerechnet wird. Jeder Lauf kostet den
 * Server eine volle Vorschau je Zeile - ein Aufruf pro Tastendruck ist auf
 * einer NAS spuerbar.
 */
const SIM_DEBOUNCE_MS = 350;

const DAY_SCALES: { value: Scale; label: string }[] = [
  { value: "30", label: "30 Tage" },
  { value: "90", label: "90 Tage" },
  { value: "365", label: "365 Tage" },
];
const MONTH_SCALES: { value: Scale; label: string }[] = [
  { value: "m12", label: "12 Monate" },
  { value: "m24", label: "24 Monate" },
];

const WARNING_LABELS: Record<string, string> = {
  below_zero: "Konto rutscht ins Minus",
  below_buffer: "Unter Sicherheitspuffer",
  stale_account_data: "Kontodaten veraltet",
  no_income_sources: "Keine Einnahmequelle gepflegt",
  income_below_booked: "Gepflegte Einnahmen unter dem gebuchten Schnitt",
  variable_from_history: "Variable Ausgaben aus der Historie",
  no_variable_basis: "Keine Grundlage für variable Ausgaben",
  horizon_beyond_contracts: "Horizont reicht über Vertragsenden hinaus",
  simulation_tax_class_hint: "Zweites Gehalt ändert die Steuerklasse",
};

const QUALITY_LABELS = { good: "gut", limited: "eingeschränkt", insufficient: "unzureichend" };

const eur = formatMinorUnitsAsEuro;
const signedEur = (minor: number) => (minor > 0 ? "+" : "") + eur(minor);
const percent = (value: number) => `${value.toFixed(1).replace(".", ",")} %`;
const months1 = (value: number) => `${value.toFixed(1).replace(".", ",")} Mon.`;

function isoToDate(value: string): Date {
  return new Date(`${value}T12:00:00`);
}

function shortDay(value: string): string {
  const date = isoToDate(value);
  return `${String(date.getDate()).padStart(2, "0")}.${String(date.getMonth() + 1).padStart(2, "0")}.`;
}

function hasWarning(forecast: ForecastResponse, code: string): boolean {
  return forecast.warnings.some((w) => w.code === code);
}

export function ForecastPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { formatDate } = useFormatDate();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  const tab = searchParams.get("tab") === "simulation" ? "simulation" : "vorschau";
  const scale = (searchParams.get("zeitraum") as Scale | null) ?? "90";
  const [includeSavings, setIncludeSavings] = useState(false);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [simScale, setSimScale] = useState<Scale>("365");
  const [simMode, setSimMode] = useState<"balance" | "diff">("balance");
  const [drafts, setDrafts] = useState<ChangeDraft[]>([]);
  const [setupSkipped, setSetupSkipped] = useState(false);
  const [activeScenarioId, setActiveScenarioId] = useState<string | null>(null);
  const [comparedIds, setComparedIds] = useState<string[]>([]);
  const [loadedScenarioId, setLoadedScenarioId] = useState<string | null>(null);
  // Das zuletzt vollstaendig gerechnete Ergebnis. react-query wirft `data`
  // schon beim Start des naechsten Laufs weg - ohne diese Kopie klappt die
  // halbe Seite bei jedem Tastendruck weg und wieder auf.
  const [lastResult, setLastResult] = useState<ForecastSimulationResponse | null>(null);
  const [lastSignature, setLastSignature] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);
  const runRef = useRef(0);
  const simulatedScenarioRef = useRef<string | null>(null);

  const isDaily = !scale.startsWith("m");
  const horizonMonths = scale === "m24" ? 24 : scale === "m12" ? 12 : undefined;

  const forecastQuery = useForecast(null, {
    horizonMonths,
    granularity: isDaily ? "day" : "month",
    includeSavings,
  });
  const upcomingQuery = useForecastUpcoming(90);
  const settingsQuery = useForecastSettings();
  const simulation = useForecastSimulation();
  const saveSettings = useUpdateForecastSettings();

  const enabled = Boolean(accessToken && selectedHouseholdId);
  const accountsQuery = useQuery<Account[]>({
    queryKey: ["accounts", selectedHouseholdId],
    queryFn: () => listAccounts(accessToken as string, selectedHouseholdId as string),
    enabled,
  });
  const contractsQuery = useQuery<Contract[]>({
    queryKey: ["contracts", selectedHouseholdId, "forecast"],
    queryFn: () => listContracts(accessToken as string, selectedHouseholdId as string, {}),
    enabled,
  });

  const scenarioQuery = useScenario(activeScenarioId);

  // Ein geladenes Szenario ersetzt den Entwurf genau einmal - danach ist es
  // wieder ein normaler Entwurf, den der Nutzer weiterbearbeitet.
  if (
    scenarioQuery.data &&
    scenarioQuery.data.id === activeScenarioId &&
    loadedScenarioId !== activeScenarioId
  ) {
    setLoadedScenarioId(activeScenarioId);
    setDrafts(changesToDrafts(scenarioQuery.data.changes ?? []));
  }

  const forecast = forecastQuery.data;
  const settings = settingsQuery.data;
  const startMonth = nextMonthValue(new Date());

  // Nie eingerichtet: `updated_at` bleibt null, bis zum ersten PUT.
  const needsSetup = Boolean(settings && !settings.updated_at) && !setupSkipped;

  const points = useMemo<ForecastPoint[]>(() => {
    if (!forecast) return [];
    if (!isDaily) {
      return forecast.monthly.map((m) => ({
        label: `${monthNameShort(m.month - 1)} ${String(m.year).slice(2)}`,
        balance: m.closing_balance_minor,
        net: m.net_minor,
      }));
    }
    return forecast.daily.slice(0, Number(scale)).map((d) => ({
      label: shortDay(d.on),
      balance: d.balance_minor,
      net: (d.flows ?? []).reduce((sum, f) => sum + f.amount_minor, 0),
      heavy: (d.flows ?? []).filter((f) => Math.abs(f.amount_minor) >= 10000).length >= 2,
    }));
  }, [forecast, isDaily, scale]);

  const markers = useMemo(() => {
    if (!forecast || !isDaily) return [];
    const window = forecast.daily.slice(0, Number(scale));
    const indexOf = (iso: string | null) => (iso ? window.findIndex((d) => d.on === iso) : -1);
    const list: { index: number; label: string; warn?: boolean }[] = [];
    const buffer = indexOf(forecast.metrics.first_below_buffer_on);
    if (buffer > 0) list.push({ index: buffer, label: "unter Puffer" });
    const negative = indexOf(forecast.metrics.first_negative_on);
    if (negative > 0) list.push({ index: negative, label: "unter null", warn: true });
    return list;
  }, [forecast, isDaily, scale]);

  const setParam = (name: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set(name, value);
    setSearchParams(next, { replace: true });
  };

  const runSimulation = (
    list: ChangeDraft[],
    options?: { sofort?: boolean; includeSavings?: boolean },
  ) => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }

    const usable = list.filter((d) => d.enabled && validateDraft(d) === null);
    if (usable.length === 0) {
      // Nichts Rechenbares: auch das letzte Ergebnis muss weg, sonst stehen
      // Zahlen zu Zeilen da, die es nicht mehr gibt.
      runRef.current += 1;
      simulation.reset();
      setLastResult(null);
      setLastSignature(null);
      return;
    }

    const payload = {
      changes: usable.map((d) => d.change),
      include_savings: options?.includeSavings ?? includeSavings,
    };
    const signature = JSON.stringify(payload);
    // Aenderungen, die die Nutzlast nicht betreffen (Bezeichnung tippen,
    // Zeile umsortieren), brauchen keinen zweiten Lauf.
    if (signature === lastSignature && !simulation.isError) return;

    const start = () => {
      debounceRef.current = null;
      runRef.current += 1;
      const run = runRef.current;
      simulation.mutate(payload, {
        onSuccess: (data) => {
          // Eine spaet eintreffende Antwort darf ein neueres Ergebnis nicht
          // ueberschreiben.
          if (run !== runRef.current) return;
          setLastResult(data);
          setLastSignature(signature);
        },
      });
    };

    if (options?.sofort) start();
    else debounceRef.current = window.setTimeout(start, SIM_DEBOUNCE_MS);
  };

  // Der Aufruf liegt bewusst NEBEN setDrafts, nicht in dessen Updater: ein
  // State-Updater muss frei von Seiteneffekten sein, sonst schickt React im
  // StrictMode jede Aenderung zweimal an den Server.
  const changeDrafts = (next: ChangeDraft[], options?: { sofort?: boolean }) => {
    setDrafts(next);
    runSimulation(next, options);
  };

  // Ein geladenes Szenario muss gerechnet werden, ohne dass der Nutzer erst
  // etwas antippt - sonst stand "Keine Änderung aktiv." ueber acht sichtbaren
  // Zeilen. Der Lauf gehoert in einen Effekt, weil die Zeilen erst nach dem
  // Uebernehmen des Szenarios im Zustand stehen.
  useEffect(() => {
    if (loadedScenarioId === null) return;
    if (simulatedScenarioRef.current === loadedScenarioId) return;
    simulatedScenarioRef.current = loadedScenarioId;
    runSimulation(drafts, { sofort: true });
  });

  useEffect(
    () => () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    },
    [],
  );

  // Ein festgehaltenes Ergebnis passt nicht mehr zu den Zeilen, sobald sich
  // die Nutzlast geaendert hat. Es bleibt sichtbar, aber abgeblendet - besser
  // als ein leerer Bildschirm, solange klar ist, dass es der alte Stand ist.
  const currentSignature = JSON.stringify({
    changes: drafts.filter((d) => d.enabled && validateDraft(d) === null).map((d) => d.change),
    include_savings: includeSavings,
  });
  const isStale = simulation.isPending || simulation.isError || currentSignature !== lastSignature;

  if (forecastQuery.isLoading || settingsQuery.isLoading) {
    return (
      <section className="page" data-testid="page-vorschau">
        <div className="page-header">
          <h1 className="page-heading">Vorschau</h1>
        </div>
        <p className="placeholder-note" role="status">
          Vorschau wird berechnet…
        </p>
      </section>
    );
  }

  if (forecastQuery.isError || !forecast) {
    return (
      <section className="page" data-testid="page-vorschau">
        <div className="page-header">
          <h1 className="page-heading">Vorschau</h1>
        </div>
        <div className="card status-error" role="alert">
          Vorschau konnte nicht berechnet werden.
          <button
            type="button"
            className="btn-secondary"
            style={{ marginLeft: "var(--space-3)" }}
            onClick={() => void forecastQuery.refetch()}
          >
            Erneut versuchen
          </button>
        </div>
      </section>
    );
  }

  if (needsSetup && settings) {
    return (
      <section className="page" data-testid="page-vorschau">
        <div className="page-header">
          <div>
            <h1 className="page-heading">Vorschau</h1>
            <p className="placeholder-note">Einmalige Einrichtung</p>
          </div>
        </div>
        <ForecastSetup
          accounts={accountsQuery.data ?? []}
          contracts={contractsQuery.data ?? []}
          settings={settings}
          forecast={forecast}
          saving={saveSettings.isPending}
          onToggleAccount={(account, liquid) => {
            void updateAccount(accessToken as string, selectedHouseholdId as string, account.id, {
              forecast_liquidity: liquid,
            }).then(() => {
              void accountsQuery.refetch();
              void forecastQuery.refetch();
            });
          }}
          onFinish={(input) => {
            saveSettings.mutate(input, {
              onSuccess: () => {
                void settingsQuery.refetch();
                void forecastQuery.refetch();
              },
            });
          }}
          onSkip={() => setSetupSkipped(true)}
        />
      </section>
    );
  }

  return (
    <section className="page" data-testid="page-vorschau">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Vorschau</h1>
          <p className="placeholder-note">Stand {formatDate(forecast.as_of)}</p>
        </div>
      </div>

      <QualityBar forecast={forecast} formatDate={formatDate} />

      <div className="filter-tabs" role="tablist" style={{ margin: "var(--space-4) 0" }}>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "vorschau"}
          className={tab === "vorschau" ? "filter-tab filter-tab--active" : "filter-tab"}
          onClick={() => setParam("tab", "vorschau")}
        >
          Vorschau
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "simulation"}
          className={tab === "simulation" ? "filter-tab filter-tab--active" : "filter-tab"}
          onClick={() => setParam("tab", "simulation")}
        >
          Was wäre wenn
        </button>
      </div>

      {tab === "vorschau" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <VerdictCard
            forecast={forecast}
            formatDate={formatDate}
            onShowCause={() => {
              if (forecast.cause) {
                setParam("zeitraum", "90");
                setSelectedDay(forecast.cause.on);
              }
            }}
            onSimulate={() => setParam("tab", "simulation")}
          />

          <Hints warnings={forecast.warnings} forecast={forecast} formatDate={formatDate} />

          <div className="card">
            <div className="forecast-card-head">
              <h2 className="card-heading">Erwarteter Kontostand</h2>
              <div className="forecast-scales">
                <div className="forecast-scale-group">
                  <span className="forecast-scale-label">Liquidität · tagesgenau</span>
                  <div className="direction-toggle">
                    {DAY_SCALES.map((s) => (
                      <button
                        key={s.value}
                        type="button"
                        className={`direction-btn ${scale === s.value ? "direction-btn--active" : ""}`}
                        aria-pressed={scale === s.value}
                        onClick={() => setParam("zeitraum", s.value)}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="forecast-scale-group">
                  <span className="forecast-scale-label">Planung · monatlich</span>
                  <div className="direction-toggle">
                    {MONTH_SCALES.map((s) => (
                      <button
                        key={s.value}
                        type="button"
                        className={`direction-btn ${scale === s.value ? "direction-btn--active" : ""}`}
                        aria-pressed={scale === s.value}
                        onClick={() => setParam("zeitraum", s.value)}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="forecast-scale-group">
                  <span className="forecast-scale-label">Konten</span>
                  <label style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                    <input
                      type="checkbox"
                      checked={includeSavings}
                      onChange={(event) => {
                        const next = event.target.checked;
                        setIncludeSavings(next);
                        // Der Schalter steckt in der Nutzlast der Simulation;
                        // ohne Neuberechnung zeigt sie etwas anderes an, als
                        // der Schalter behauptet.
                        if (drafts.length > 0) {
                          runSimulation(drafts, { sofort: true, includeSavings: next });
                        }
                      }}
                    />
                    <span style={{ fontSize: 12 }}>Sparkonten einbeziehen</span>
                  </label>
                </div>
              </div>
            </div>

            <Chart
              option={forecastBalanceOption(points, {
                bufferMinor: forecast.safety_buffer_minor,
                markers,
              })}
              height={280}
              description={`Erwarteter Kontostand über ${points.length} ${isDaily ? "Tage" : "Monate"}`}
              tableFallback={<BalanceTable forecast={forecast} />}
              onSegmentClick={(index) => {
                if (isDaily) setSelectedDay(forecast.daily[index]?.on ?? null);
              }}
            />

            <DayDetail
              forecast={forecast}
              groups={upcomingQuery.data ?? []}
              day={selectedDay ?? forecast.cause?.on ?? null}
              formatDate={formatDate}
            />
          </div>

          <div className="section-grid">
            <div className="card">
              <h2 className="card-heading">Monate</h2>
              <MonthTable forecast={forecast} />
            </div>
            <div className="card">
              <h2 className="card-heading">Nächste Zahlungen</h2>
              <UpcomingList
                groups={upcomingQuery.data ?? []}
                onPick={(iso) => {
                  setParam("zeitraum", "90");
                  setSelectedDay(iso);
                }}
              />
            </div>
          </div>

          <details className="card forecast-basis" data-testid="forecast-basis">
            <summary>So wird gerechnet</summary>
            <h3 className="card-heading" style={{ marginTop: "var(--space-3)" }}>
              Woraus der Startpunkt besteht
            </h3>
            <OpeningBreakdown forecast={forecast} formatDate={formatDate} />
            <h3 className="card-heading" style={{ marginTop: "var(--space-4)" }}>
              Was danach kommt
            </h3>
            <BasisTable forecast={forecast} formatDate={formatDate} />
          </details>
        </div>
      ) : (
        <SimulationView
          drafts={drafts}
          onChange={changeDrafts}
          activeScenarioId={activeScenarioId}
          onLoadScenario={(id) => {
            setActiveScenarioId(id);
            if (id === null) {
              setLoadedScenarioId(null);
              changeDrafts([]);
            }
          }}
          comparedIds={comparedIds}
          onCompare={setComparedIds}
          includeSavings={includeSavings}
          contracts={contractsQuery.data ?? []}
          simulation={simulation}
          lastResult={lastResult}
          isStale={isStale}
          onRetry={() => runSimulation(drafts, { sofort: true })}
          base={forecast}
          startMonth={startMonth}
          scale={simScale}
          onScale={setSimScale}
          mode={simMode}
          onMode={setSimMode}
          formatDate={formatDate}
        />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

function QualityBar({
  forecast,
  formatDate,
}: {
  forecast: ForecastResponse;
  formatDate: (value: string | Date) => string;
}) {
  const q = forecast.quality;
  const facts = [
    q.last_booking_on ? `Kontostände bis ${formatDate(q.last_booking_on)}` : "Keine Buchungen",
    `${q.contract_count} Verträge`,
    `${q.income_count} Einnahmen`,
    q.variable_basis === "history" ? "Variable aus Historie" : "Variable aus Budget",
    q.excluded_account_names.length
      ? `${q.excluded_account_names.join(", ")} nicht einbezogen`
      : "Alle Konten einbezogen",
    q.contracts_ending_in_horizon > 0
      ? `${q.contracts_ending_in_horizon} Verträge enden im Horizont`
      : "Keine Vertragsenden im Horizont",
    `${coveragePercent(forecast)} % der Zahlungsströme modelliert`,
  ];
  return (
    <div className="forecast-quality" data-testid="forecast-quality">
      <span className="forecast-quality-chip">
        <span className={`forecast-quality-dot forecast-quality-dot--${q.level}`} />
        Datenqualität: {QUALITY_LABELS[q.level]}
      </span>
      <span className="forecast-quality-facts">
        {facts.map((f) => (
          <span key={f}>{f}</span>
        ))}
      </span>
    </div>
  );
}

function VerdictCard({
  forecast,
  formatDate,
  onShowCause,
  onSimulate,
}: {
  forecast: ForecastResponse;
  formatDate: (value: string | Date) => string;
  onShowCause: () => void;
  onSimulate: () => void;
}) {
  const m = forecast.metrics;

  // Ohne Einnahmequelle kennt die Vorschau nur Ausgaben - dann ist jede
  // Aussage ueber einen Engpass wertlos, nicht nur ungenau.
  if (hasWarning(forecast, "no_income_sources")) {
    return (
      <div className="forecast-verdict forecast-verdict--blocked" data-testid="forecast-verdict">
        <div className="forecast-verdict-top">
          <div className="forecast-verdict-lead">
            <div className="forecast-verdict-kicker">Grundlage fehlt</div>
            <h2 className="forecast-verdict-headline">
              Ohne eine gepflegte Einnahme kennt die Vorschau nur Ausgaben.
            </h2>
          </div>
        </div>
        <div className="forecast-verdict-actions">
          <Link className="btn-primary" to="/fixkosten?richtung=einnahmen">
            Einnahmen anlegen
          </Link>
        </div>
      </div>
    );
  }

  // Schon heute im Minus: das ist ein Ist-Befund, keine Prognose.
  const today = forecast.daily[0];
  if (today && today.balance_minor < 0) {
    return (
      <div className="forecast-verdict" data-testid="forecast-verdict">
        <div className="forecast-verdict-top">
          <div className="forecast-verdict-lead">
            <div className="forecast-verdict-kicker">Bereits im Minus</div>
            <h2 className="forecast-verdict-headline">
              Dein Konto steht heute bei {eur(today.balance_minor)}.
            </h2>
          </div>
          <div className="forecast-figures">
            <Figure
              label="Nötig für null"
              value={eur(-today.balance_minor)}
              hint="sofort"
              tone="negative"
            />
            {/* Bei Puffer 0 waere das dieselbe Zahl wie darueber. */}
            {forecast.safety_buffer_minor > 0 ? (
              <Figure
                label="Nötig für den Puffer"
                value={eur(forecast.safety_buffer_minor - today.balance_minor)}
                hint={`Puffer ${eur(forecast.safety_buffer_minor)}`}
              />
            ) : null}
            <Figure
              label="Wieder im Plus"
              value={m.recovery_on ? formatDate(m.recovery_on) : "nicht im Horizont"}
              hint="ohne Eingriff"
            />
          </div>
        </div>
        <div className="forecast-verdict-actions">
          <button type="button" className="btn-primary" onClick={onSimulate}>
            Lösung simulieren
          </button>
        </div>
      </div>
    );
  }

  const negative = m.first_negative_on;

  if (!negative) {
    const watch = Boolean(m.first_below_buffer_on);
    return (
      <div
        className={`forecast-verdict ${watch ? "forecast-verdict--watch" : "forecast-verdict--ok"}`}
        data-testid="forecast-verdict"
      >
        <div className="forecast-verdict-top">
          <div className="forecast-verdict-lead">
            <div className="forecast-verdict-kicker">
              {watch ? "Knapp, aber im Plus" : "Kein Engpass"}
            </div>
            <h2 className="forecast-verdict-headline">
              Tiefster Stand {eur(m.lowest_balance_minor)} am {formatDate(m.lowest_balance_on)}
            </h2>
          </div>
          <div className="forecast-figures">
            <Figure
              label="Abstand zum Puffer"
              value={eur(m.lowest_balance_minor - forecast.safety_buffer_minor)}
              hint={`Puffer ${eur(forecast.safety_buffer_minor)}`}
            />
            <Figure
              label="Überschuss / Monat"
              value={eur(m.average_monthly_surplus_minor)}
              hint={`Sparquote ${percent(m.savings_rate_percent)}`}
              tone="positive"
            />
            <Figure
              label="Reserve ohne Einnahmen"
              value={months1(m.reserve_without_income_months)}
              hint="wenn ab heute nichts hereinkommt"
            />
            <Figure
              label="Gebunden in Rücklagen"
              value={eur(forecast.bound_reserve_minor)}
              hint="ausgewiesen, nicht abgezogen"
            />
          </div>
        </div>
        <div className="forecast-verdict-actions">
          <button type="button" className="btn-primary" onClick={onSimulate}>
            Änderung durchspielen
          </button>
        </div>
      </div>
    );
  }

  const chain: { when: string; what: string; amount: string; tone: "bad" | "good" }[] = [];
  if (forecast.cause) {
    chain.push({
      when: shortDay(forecast.cause.on),
      what: forecast.cause.flows.map((f) => f.label).join(" + "),
      amount: eur(forecast.cause.total_minor),
      tone: "bad",
    });
  }
  chain.push({ when: shortDay(negative), what: "Konto unter null", amount: "", tone: "bad" });
  chain.push({
    when: shortDay(m.lowest_balance_on),
    what: "Tiefster Stand",
    amount: eur(m.lowest_balance_minor),
    tone: "bad",
  });
  if (m.recovery_on) {
    chain.push({ when: shortDay(m.recovery_on), what: "Wieder im Plus", amount: "", tone: "good" });
  }

  return (
    <div className="forecast-verdict" data-testid="forecast-verdict">
      <div className="forecast-verdict-top">
        <div className="forecast-verdict-lead">
          <div className="forecast-verdict-kicker">Engpass erkannt</div>
          <h2 className="forecast-verdict-headline">
            Am {formatDate(negative)} rutscht dein Konto ins Minus.
          </h2>
        </div>
        <div className="forecast-figures">
          <Figure
            label="Tiefster Stand"
            value={eur(m.lowest_balance_minor)}
            hint={`am ${formatDate(m.lowest_balance_on)}`}
            tone="negative"
          />
          <Figure
            label="Nötig gegen das Minus"
            value={eur(m.needed_against_negative_minor)}
            hint={`einmalig vor dem ${shortDay(negative)}`}
          />
          {forecast.safety_buffer_minor > 0 ? (
            <Figure
              label="Nötig für den Puffer"
              value={eur(m.needed_for_buffer_minor)}
              hint={`um ${eur(forecast.safety_buffer_minor)} zu halten`}
            />
          ) : null}
          <Figure
            label="Tage im Minus"
            value={String(m.days_below_zero)}
            hint={`von ${forecast.daily.length} im Horizont`}
          />
        </div>
      </div>
      <div className="forecast-chain">
        {chain.map((step) => (
          <div
            key={step.when + step.what}
            className={`forecast-chain-step forecast-chain-step--${step.tone}`}
          >
            <div className="forecast-chain-when">{step.when}</div>
            <div className="forecast-chain-what">{step.what}</div>
            {step.amount ? <div className="forecast-chain-amount">{step.amount}</div> : null}
          </div>
        ))}
      </div>
      <div className="forecast-verdict-actions">
        {forecast.cause ? (
          <button type="button" className="btn-secondary" onClick={onShowCause}>
            Auslösenden Tag zeigen
          </button>
        ) : null}
        <button type="button" className="btn-primary" onClick={onSimulate}>
          Lösung simulieren
        </button>
      </div>
    </div>
  );
}

function Figure({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "positive" | "negative";
}) {
  const className =
    tone === "negative"
      ? "forecast-figure-value transaction-amount--negative"
      : tone === "positive"
        ? "forecast-figure-value transaction-amount--positive"
        : "forecast-figure-value";
  return (
    <div className="forecast-figure">
      <span className="forecast-figure-label">{label}</span>
      <span className={className}>{value}</span>
      <span className="forecast-figure-hint">{hint}</span>
    </div>
  );
}

function Hints({
  warnings,
  forecast,
  formatDate,
}: {
  warnings: ForecastWarning[];
  forecast: ForecastResponse;
  formatDate: (value: string | Date) => string;
}) {
  // below_zero und no_income_sources traegt die Befundkarte bereits.
  const rest = warnings.filter((w) => w.code !== "below_zero" && w.code !== "no_income_sources");
  // Ausgeschlossene Konten mit Minus sind kein Fehler, aber sie fehlen im
  // Verlauf - ein Kreditkartensaldo wird irgendwann abgebucht.
  const excluded = excludedTotalMinor(forecast.opening_accounts ?? []);
  const excludedNames = (forecast.opening_accounts ?? [])
    .filter((a) => !a.included && a.balance_minor < 0)
    .map((a) => a.name);
  const showExcluded = excluded < 0 && excludedNames.length > 0;
  const oldest = oldestIncludedBooking(forecast.opening_accounts ?? []);
  if (rest.length === 0 && !showExcluded) return null;
  return (
    <div className="forecast-hints">
      {showExcluded ? (
        <div className="forecast-hint forecast-hint--watch">
          <strong>Nicht einbezogen: {eur(excluded)}</strong> auf {excludedNames.join(", ")} — diese
          Salden fehlen im Verlauf, werden aber irgendwann fällig.
        </div>
      ) : null}
      {rest.map((w) => (
        <div
          key={w.code}
          className={`forecast-hint ${w.level === "watch" ? "forecast-hint--watch" : ""}`}
        >
          <strong>{WARNING_LABELS[w.code] ?? w.code}</strong>
          {w.code === "stale_account_data" && oldest
            ? ` — ältester Kontostand vom ${formatDate(oldest)}`
            : ""}
          {w.code === "below_buffer" && forecast.metrics.first_below_buffer_on
            ? ` — ab ${formatDate(forecast.metrics.first_below_buffer_on)}, an ${forecast.metrics.days_below_buffer} Tagen`
            : ""}
          {w.code === "income_below_booked" ? (
            <>
              {" — "}
              <Link to="/fixkosten?richtung=einnahmen">Einnahmen prüfen</Link>
            </>
          ) : null}
          {w.code === "no_variable_basis" ? (
            <>
              {" — "}
              <Link to="/budget">Budget anlegen</Link>
            </>
          ) : null}
          {w.code === "stale_account_data" ? (
            <>
              {" — "}
              <Link to="/importe">Umsätze importieren</Link>
            </>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function BalanceTable({ forecast }: { forecast: ForecastResponse }) {
  return (
    <div className="txn-table-wrapper">
      <table className="txn-table">
        <thead>
          <tr>
            <th className="txn-th txn-th--date">Datum</th>
            <th className="txn-th txn-th--amount">Kontostand</th>
          </tr>
        </thead>
        <tbody>
          {forecast.daily.map((d) => (
            <tr key={d.on}>
              <td className="txn-td txn-td--date">{d.on}</td>
              <td
                className={`txn-td txn-td--amount ${d.balance_minor < 0 ? "transaction-amount--negative" : ""}`}
              >
                {eur(d.balance_minor)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MonthTable({ forecast }: { forecast: ForecastResponse }) {
  const criticalIso = forecast.metrics.first_negative_on;
  const critical = criticalIso ? isoToDate(criticalIso) : null;
  return (
    <div className="txn-table-wrapper">
      <table className="txn-table" data-testid="forecast-months">
        <thead>
          <tr>
            <th className="txn-th">Monat</th>
            <th className="txn-th txn-th--amount">Einnahmen</th>
            <th className="txn-th txn-th--amount">Fixkosten</th>
            <th className="txn-th txn-th--amount">Variabel</th>
            <th className="txn-th txn-th--amount">Saldo</th>
            <th className="txn-th txn-th--amount">Endstand</th>
          </tr>
        </thead>
        <tbody>
          {forecast.monthly.slice(0, 12).map((m) => {
            const isCritical =
              critical !== null &&
              critical.getFullYear() === m.year &&
              critical.getMonth() + 1 === m.month;
            return (
              <tr key={`${m.year}-${m.month}`}>
                <td className="txn-td">
                  {isCritical ? "▲ " : ""}
                  {monthNameShort(m.month - 1)} {m.year}
                </td>
                <td className="txn-td txn-td--amount transaction-amount--positive">
                  {eur(m.income_minor)}
                </td>
                <td className="txn-td txn-td--amount">{eur(m.fixed_minor)}</td>
                <td className="txn-td txn-td--amount">{eur(m.variable_minor)}</td>
                <td
                  className={`txn-td txn-td--amount ${m.net_minor < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
                >
                  {signedEur(m.net_minor)}
                </td>
                <td
                  className={`txn-td txn-td--amount ${m.closing_balance_minor < 0 ? "transaction-amount--negative" : ""}`}
                >
                  <strong>{eur(m.closing_balance_minor)}</strong>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function BasisTable({
  forecast,
  formatDate,
}: {
  forecast: ForecastResponse;
  formatDate: (value: string | Date) => string;
}) {
  const q = forecast.quality;
  const m = forecast.metrics;
  const rows: [string, string, string][] = [
    ["Startsaldo", `zum ${formatDate(forecast.as_of)}`, eur(forecast.opening_liquidity_minor)],
    [
      "Nicht einbezogen",
      q.excluded_account_names.length ? q.excluded_account_names.join(", ") : "nichts",
      "—",
    ],
    ["Einnahmen", `${q.income_count} Verträge`, `${eur(m.income_monthly_minor)} / Monat`],
    ["Fixkosten", `${q.contract_count} Verträge`, `${eur(m.fixed_monthly_minor)} / Monat`],
    [
      "Variable Ausgaben",
      q.variable_basis === "history" ? "aus der Historie" : "aus dem Budgetplan",
      `${eur(m.variable_monthly_minor)} / Monat`,
    ],
    ["Gebunden in Rücklagen", "ausgewiesen, nicht abgezogen", eur(forecast.bound_reserve_minor)],
    ["Sicherheitspuffer", "Unterschreitung ist eine Warnung", eur(forecast.safety_buffer_minor)],
    ["Horizont", `${forecast.horizon_months} Monate`, `${forecast.daily.length} Tage berechnet`],
  ];
  return (
    <div className="txn-table-wrapper">
      <table className="txn-table">
        <tbody>
          {rows.map(([a, b, c]) => (
            <tr key={a}>
              <td className="txn-td">
                <strong>{a}</strong>
              </td>
              <td className="txn-td" style={{ color: "var(--color-text-secondary)" }}>
                {b}
              </td>
              <td className="txn-td txn-td--amount">{c}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DayDetail({
  forecast,
  groups,
  day,
  formatDate,
}: {
  forecast: ForecastResponse;
  groups: ForecastDayGroup[];
  day: string | null;
  formatDate: (value: string | Date) => string;
}) {
  if (!day) return null;
  const point = forecast.daily.find((d) => d.on === day);
  if (!point) return null;
  // Startstand, Tagesposten und der geschaetzte variable Anteil kommen vom
  // Server - zurueckrechnen waere eine zweite Wahrheit.
  const group = groups.find((g) => g.on === day);

  return (
    <div className="forecast-day" data-testid="forecast-day-detail">
      <div className="forecast-day-head">
        <span className="forecast-day-date">{formatDate(day)}</span>
        {group?.is_heavy ? (
          <span className="status-badge status-badge--warning">Mehrere große Fälligkeiten</span>
        ) : null}
      </div>
      <div className="forecast-day-rows">
        <div className="forecast-day-row forecast-day-row--open">
          <span>Startstand</span>
          <span>{eur(point.opening_balance_minor)}</span>
        </div>
        {(point.flows ?? []).map((f) => (
          <div key={f.label + f.amount_minor} className="forecast-day-row">
            <span>{f.label}</span>
            <span
              className={
                f.amount_minor < 0 ? "transaction-amount--negative" : "transaction-amount--positive"
              }
            >
              {signedEur(f.amount_minor)}
            </span>
          </div>
        ))}
        <div className="forecast-day-row forecast-day-row--estimate">
          <span>Variable Tagesausgaben (geschätzt)</span>
          <span>{signedEur(-point.variable_minor)}</span>
        </div>
        <div className="forecast-day-row forecast-day-row--sum">
          <span>Endstand</span>
          <span className={point.balance_minor < 0 ? "transaction-amount--negative" : ""}>
            {eur(point.balance_minor)}
          </span>
        </div>
      </div>
    </div>
  );
}

function UpcomingList({
  groups,
  onPick,
}: {
  groups: ForecastDayGroup[];
  onPick: (iso: string) => void;
}) {
  if (groups.length === 0) {
    return <p className="placeholder-note">Keine erwarteten Zahlungen im Zeitraum.</p>;
  }
  return (
    <div data-testid="forecast-upcoming">
      {groups.slice(0, 8).map((g) => (
        <div key={g.on} className={`forecast-group ${g.is_heavy ? "forecast-group--heavy" : ""}`}>
          <button type="button" className="forecast-group-head" onClick={() => onPick(g.on)}>
            <span className="forecast-group-date">
              {g.is_heavy ? "▲ " : ""}
              {shortDay(g.on)}
            </span>
            <span
              className={`forecast-group-sum ${g.total_minor < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
            >
              {signedEur(g.total_minor)}
            </span>
          </button>
          <div className="forecast-group-items">
            {g.flows.map((f) => (
              <div key={f.label + f.amount_minor} className="forecast-group-item">
                <span>{f.label}</span>
                <span
                  className={
                    f.amount_minor < 0
                      ? "transaction-amount--negative"
                      : "transaction-amount--positive"
                  }
                >
                  {signedEur(f.amount_minor)}
                </span>
              </div>
            ))}
          </div>
          <div className="forecast-group-after">
            <span>Danach verfügbar</span>
            <span className={g.balance_after_minor < 0 ? "transaction-amount--negative" : ""}>
              {eur(g.balance_after_minor)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

function SimulationView({
  drafts,
  onChange,
  activeScenarioId,
  onLoadScenario,
  comparedIds,
  onCompare,
  includeSavings,
  contracts,
  simulation,
  lastResult,
  isStale,
  onRetry,
  base,
  startMonth,
  scale,
  onScale,
  mode,
  onMode,
  formatDate,
}: {
  drafts: ChangeDraft[];
  onChange: (next: ChangeDraft[], options?: { sofort?: boolean }) => void;
  activeScenarioId: string | null;
  onLoadScenario: (id: string | null) => void;
  comparedIds: string[];
  onCompare: (ids: string[]) => void;
  includeSavings: boolean;
  contracts: Contract[];
  simulation: ReturnType<typeof useForecastSimulation>;
  /** Letztes vollstaendiges Ergebnis, auch waehrend ein Lauf unterwegs ist. */
  lastResult: ForecastSimulationResponse | null;
  /** Gehoert `lastResult` noch zum aktuellen Stand der Zeilen? */
  isStale: boolean;
  onRetry: () => void;
  base: ForecastResponse;
  startMonth: string;
  scale: Scale;
  onScale: (value: Scale) => void;
  mode: "balance" | "diff";
  onMode: (value: "balance" | "diff") => void;
  formatDate: (value: string | Date) => string;
}) {
  const result = simulation.data ?? lastResult;
  const active = drafts.filter((d) => d.enabled && validateDraft(d) === null);
  const enabledCount = drafts.filter((d) => d.enabled).length;

  /**
   * Genau ein Zustand statt dreier unabhaengiger Bedingungen. Vorher konnten
   * "Simulation fehlgeschlagen." und "Keine Änderung aktiv." zusammen
   * erscheinen, weil react-query im Fehlerfall `data` leert und damit beide
   * Bedingungen zugleich wahr wurden.
   */
  const status: "fehler" | "rechnet" | "ergebnis" | "unvollstaendig" | "leer" = simulation.isError
    ? "fehler"
    : simulation.isPending && !result
      ? "rechnet"
      : result
        ? "ergebnis"
        : enabledCount > 0
          ? "unvollstaendig"
          : "leer";

  const seriesFor = (forecast: ForecastResponse) =>
    scale.startsWith("m")
      ? forecast.monthly.slice(0, scale === "m12" ? 12 : 24).map((m) => m.closing_balance_minor)
      : forecast.daily.slice(0, Number(scale)).map((d) => d.balance_minor);

  const labels = scale.startsWith("m")
    ? base.monthly
        .slice(0, scale === "m12" ? 12 : 24)
        .map((m) => `${monthNameShort(m.month - 1)} ${String(m.year).slice(2)}`)
    : base.daily.slice(0, Number(scale)).map((d) => shortDay(d.on));

  return (
    <div className="forecast-sim">
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)", minWidth: 0 }}>
        <div className="card">
          <ScenarioBar
            drafts={drafts}
            activeId={activeScenarioId}
            onLoad={onLoadScenario}
            compared={comparedIds}
            onCompare={onCompare}
            maxCompared={MAX_COMPARED_SCENARIOS}
            canEdit
          />
        </div>

        <ScenarioComparison scenarioIds={comparedIds} includeSavings={includeSavings} />

        <div className="card">
          <h2 className="card-heading">Änderungen</h2>
          <ChangeEditor
            drafts={drafts}
            onChange={onChange}
            contracts={contracts}
            contributions={result?.contributions ?? []}
            contributionsFresh={!isStale}
            startMonth={startMonth}
            formatMoney={eur}
          />
        </div>

        {result ? (
          <div className={isStale ? "forecast-stale" : undefined}>
            <div className="card">
              <h2 className="card-heading">Wie der neue Überschuss entsteht</h2>
              <Waterfall base={base} result={result} drafts={active} />
            </div>

            <div className="card">
              <div className="forecast-card-head">
                <h2 className="card-heading">Basis und Simulation</h2>
                <div className="forecast-scales">
                  <div className="direction-toggle">
                    {(["90", "365", "m24"] as Scale[]).map((s) => (
                      <button
                        key={s}
                        type="button"
                        className={`direction-btn ${scale === s ? "direction-btn--active" : ""}`}
                        aria-pressed={scale === s}
                        onClick={() => onScale(s)}
                      >
                        {s === "m24" ? "24 Monate" : `${s} Tage`}
                      </button>
                    ))}
                  </div>
                  <div className="direction-toggle">
                    {(["balance", "diff"] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        className={`direction-btn ${mode === m ? "direction-btn--active" : ""}`}
                        aria-pressed={mode === m}
                        onClick={() => onMode(m)}
                      >
                        {m === "balance" ? "Kontostand" : "Differenz"}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <Chart
                option={forecastCompareOption(
                  labels,
                  seriesFor(result.base),
                  seriesFor(result.simulation),
                  mode,
                )}
                height={260}
                description="Vergleich von Basis und Simulation"
                tableFallback={
                  <CompareTable
                    labels={labels}
                    base={seriesFor(result.base)}
                    sim={seriesFor(result.simulation)}
                  />
                }
              />
            </div>
          </div>
        ) : null}
      </div>

      <div className="forecast-sim-result" role="status" aria-live="polite">
        {status === "fehler" ? (
          <div className="card status-error" role="alert">
            <p className="card-body">
              {simulation.error instanceof ApiError && simulation.error.message
                ? simulation.error.message
                : "Simulation fehlgeschlagen."}
            </p>
            <button type="button" className="btn-secondary" onClick={onRetry}>
              Erneut versuchen
            </button>
          </div>
        ) : null}
        {status === "rechnet" ? <p className="placeholder-note">Wird gerechnet…</p> : null}
        {status === "unvollstaendig" ? (
          <div className="card">
            <p className="card-body">
              Eine Änderung ist noch unvollständig — sie wird nicht mitgerechnet.
            </p>
          </div>
        ) : null}
        {status === "leer" ? (
          <div className="card">
            <p className="card-body">Keine Änderung aktiv.</p>
          </div>
        ) : null}
        {result ? (
          <div className={isStale ? "forecast-stale" : undefined}>
            {isStale ? (
              <p className="forecast-stale-note">Stand vor der letzten Änderung.</p>
            ) : null}
            <SimVerdict result={result} formatDate={formatDate} />

            <div className="card">
              <h2 className="card-heading">Wirkung</h2>
              <div className="forecast-deltas">
                <Delta
                  label="Überschuss / Monat"
                  from={result.base.metrics.average_monthly_surplus_minor}
                  to={result.simulation.metrics.average_monthly_surplus_minor}
                  format={eur}
                  better="up"
                />
                <Delta
                  label="Sparquote"
                  from={result.base.metrics.savings_rate_percent}
                  to={result.simulation.metrics.savings_rate_percent}
                  format={percent}
                  better="up"
                />
                <Delta
                  label="Fixkostenquote"
                  from={result.base.metrics.fixed_cost_ratio_percent}
                  to={result.simulation.metrics.fixed_cost_ratio_percent}
                  format={percent}
                  better="down"
                />
                <Delta
                  label="Reserve ohne Einnahmen"
                  from={result.base.metrics.reserve_without_income_months}
                  to={result.simulation.metrics.reserve_without_income_months}
                  format={months1}
                  better="up"
                />
              </div>
              <div className="forecast-milestones" style={{ marginTop: "var(--space-4)" }}>
                {(
                  [
                    [
                      "3 Mon.",
                      result.base.metrics.balance_in_3_months_minor,
                      result.simulation.metrics.balance_in_3_months_minor,
                    ],
                    [
                      "6 Mon.",
                      result.base.metrics.balance_in_6_months_minor,
                      result.simulation.metrics.balance_in_6_months_minor,
                    ],
                    [
                      "12 Mon.",
                      result.base.metrics.balance_in_12_months_minor,
                      result.simulation.metrics.balance_in_12_months_minor,
                    ],
                    [
                      "24 Mon.",
                      result.base.metrics.balance_in_24_months_minor,
                      result.simulation.metrics.balance_in_24_months_minor,
                    ],
                  ] as [string, number, number][]
                ).map(([when, from, to]) => (
                  <div key={when} className="forecast-milestone">
                    <div className="forecast-milestone-when">{when}</div>
                    <div
                      className={`forecast-milestone-value ${to < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
                    >
                      {eur(to)}
                    </div>
                    <div
                      className="forecast-milestone-when"
                      style={{ textTransform: "none", fontWeight: 400 }}
                    >
                      statt {eur(from)}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {result.simulation.warnings.some((w) => w.code === "simulation_tax_class_hint") ? (
              <div className="forecast-hints">
                <div className="forecast-hint forecast-hint--watch">
                  <strong>{WARNING_LABELS.simulation_tax_class_hint}</strong> — beide Nettobeträge
                  prüfen. Der Finanzplaner rechnet ausschließlich mit Netto.
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function SimVerdict({
  result,
  formatDate,
}: {
  result: ForecastSimulationResponse;
  formatDate: (value: string | Date) => string;
}) {
  const baseNeg = result.base.metrics.first_negative_on;
  const simNeg = result.simulation.metrics.first_negative_on;

  let kicker: string;
  let tone: string;
  let text: string;

  if (!baseNeg && simNeg) {
    kicker = "Verschlechterung";
    tone = "";
    text = `Diese Änderungen erzeugen einen Engpass am ${formatDate(simNeg)}.`;
  } else if (baseNeg && !simNeg) {
    kicker = "Gelöst";
    tone = "forecast-verdict--ok";
    text = `Kein Tag im Minus mehr. Tiefster Stand ${eur(
      result.simulation.metrics.lowest_balance_minor,
    )} am ${formatDate(result.simulation.metrics.lowest_balance_on)}.`;
  } else if (baseNeg && simNeg) {
    kicker = "Teilweise";
    tone = "forecast-verdict--watch";
    const shift = result.comparison.first_negative_shift_days;
    text = shift
      ? `Engpass verschiebt sich um ${shift} Tage auf den ${formatDate(simNeg)}, bleibt aber.`
      : `Engpass bleibt am ${formatDate(simNeg)}. Was du änderst, wirkt erst danach.`;
  } else {
    kicker = "Kein Engpass";
    tone = "forecast-verdict--ok";
    text = `Kontostand nach 24 Monaten ${signedEur(result.comparison.balance_delta_24_months_minor)}.`;
  }

  return (
    <div className={`forecast-verdict ${tone}`} data-testid="forecast-sim-verdict">
      <div className="forecast-verdict-top" style={{ padding: "var(--space-4)" }}>
        <div className="forecast-verdict-lead">
          <div className="forecast-verdict-kicker">{kicker}</div>
          <p style={{ margin: 0, fontSize: 13.5 }}>{text}</p>
        </div>
      </div>
    </div>
  );
}

function Delta({
  label,
  from,
  to,
  format,
  better,
}: {
  label: string;
  from: number;
  to: number;
  format: (value: number) => string;
  better: "up" | "down";
}) {
  const diff = to - from;
  const good = better === "up" ? diff > 0 : diff < 0;
  const className =
    Math.abs(diff) < 0.005
      ? "forecast-delta-diff"
      : good
        ? "forecast-delta-diff transaction-amount--positive"
        : "forecast-delta-diff transaction-amount--negative";
  const mark = Math.abs(diff) < 0.005 ? "→" : good ? "▲" : "▼";
  return (
    <div className="forecast-delta">
      <span className="forecast-delta-label">{label}</span>
      <span className="forecast-delta-pair">
        <span className="forecast-delta-from">{format(from)}</span>
        <span className="forecast-delta-to">{format(to)}</span>
      </span>
      <span className={className}>
        {mark} {diff > 0 ? "+" : ""}
        {format(diff)}
      </span>
    </div>
  );
}

function Waterfall({
  base,
  result,
  drafts,
}: {
  base: ForecastResponse;
  result: ForecastSimulationResponse;
  drafts: ChangeDraft[];
}) {
  const rows = [
    { label: "Basisüberschuss", value: base.metrics.average_monthly_surplus_minor, base: true },
    ...drafts.map((d, index) => ({
      label: d.change.label,
      value: result.contributions.find((c) => c.change_index === index)?.monthly_minor ?? 0,
      base: false,
    })),
  ];
  const total = rows.reduce((sum, r) => sum + r.value, 0);
  const cumulative: number[] = [0];
  rows.forEach((r) => cumulative.push((cumulative[cumulative.length - 1] ?? 0) + r.value));
  const low = Math.min(0, ...cumulative);
  const high = Math.max(0, ...cumulative);
  const span = Math.max(1, high - low);
  const pos = (value: number) => ((value - low) / span) * 100;

  let running = 0;
  return (
    <div className="forecast-waterfall" data-testid="forecast-waterfall">
      {rows.map((r, index) => {
        const from = running;
        running += r.value;
        const a = pos(Math.min(from, running));
        const b = pos(Math.max(from, running));
        return (
          <div
            key={`${r.label}-${index}`}
            className={`forecast-wf-row ${r.base ? "forecast-wf-row--base" : ""}`}
          >
            <span className="forecast-wf-label">{r.label}</span>
            <span className="forecast-wf-track">
              <span className="forecast-wf-zero" style={{ left: `${pos(0)}%` }} />
              <span
                className={`forecast-wf-bar ${r.value < 0 ? "forecast-wf-bar--neg" : "forecast-wf-bar--pos"}`}
                style={{ left: `${a}%`, width: `${Math.max(b - a, 0.8)}%` }}
              />
            </span>
            <span
              className={`forecast-wf-amount ${r.value < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
            >
              {signedEur(r.value)}
            </span>
          </div>
        );
      })}
      <div className="forecast-wf-row forecast-wf-row--total">
        <span className="forecast-wf-label">Neuer Überschuss</span>
        <span className="forecast-wf-track">
          <span className="forecast-wf-zero" style={{ left: `${pos(0)}%` }} />
          <span
            className="forecast-wf-bar forecast-wf-bar--total"
            style={{
              left: `${Math.min(pos(0), pos(total))}%`,
              width: `${Math.max(Math.abs(pos(total) - pos(0)), 0.8)}%`,
            }}
          />
        </span>
        <span className="forecast-wf-amount">{signedEur(total)}</span>
      </div>
    </div>
  );
}

function CompareTable({ labels, base, sim }: { labels: string[]; base: number[]; sim: number[] }) {
  return (
    <div className="txn-table-wrapper">
      <table className="txn-table">
        <thead>
          <tr>
            <th className="txn-th">Zeitpunkt</th>
            <th className="txn-th txn-th--amount">Basis</th>
            <th className="txn-th txn-th--amount">Simulation</th>
          </tr>
        </thead>
        <tbody>
          {labels.map((label, index) => (
            <tr key={label + index}>
              <td className="txn-td">{label}</td>
              <td className="txn-td txn-td--amount">{eur(base[index] ?? 0)}</td>
              <td className="txn-td txn-td--amount">{eur(sim[index] ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default ForecastPage;
