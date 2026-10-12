import { useState } from "react";
import { AiReasonNotice } from "../components/AiReasonNotice";
import { TransactionDrilldown } from "../components/analytics/TransactionDrilldown";
import { useInsights } from "../hooks/useInsights";
import { rowsQuery } from "../lib/analytics";
import { formatMonthYear } from "../lib/dates";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type { Insight, InsightSeverity } from "../lib/types";

// Finanz-Empfehlungen: was aus den eigenen Zahlen konkret zu tun waere.
//
// Die Seite zeigt zwei fachlich verschiedene Dinge nebeneinander und trennt
// sie auch sichtbar:
//
// - Titel, Beschreibung, Betraege und Belege sind serverseitig gerechnet. Sie
//   stehen auch da, wenn kein Modell laeuft.
// - Der Handlungssatz einer KI ist ein optionaler Zusatz mit eigener Kennung.
//   Der Server verwirft jede Formulierung, die eigene Zahlen enthaelt - was
//   hier steht, ist also nie eine zweite Rechnung.
//
// Jede Empfehlung ist anklickbar: eine Kennzahl ohne Weg zu den Buchungen
// dahinter bleibt eine Behauptung.

const MONTH_OPTIONS = [6, 12, 24];

// Default-Anker: letzter vollstaendiger Kalendermonat vor heute - deckt sich
// mit dem Verhalten, das der Server ohne anchor_year/anchor_month waehlt
// (resolve_window), solange der Haushalt aktuelle Buchungen hat.
function lastCompleteMonth(): { year: number; month: number } {
  const now = new Date();
  const prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return { year: prev.getFullYear(), month: prev.getMonth() + 1 };
}

const SEVERITY_LABEL: Record<InsightSeverity, string> = {
  warning: "Achtung",
  opportunity: "Chance",
  info: "Einordnung",
};

const SEVERITY_CLASS: Record<InsightSeverity, string> = {
  warning: "status-badge status-badge--warning",
  opportunity: "status-badge status-badge--positive",
  info: "status-badge status-badge--neutral",
};

export function InsightsPage() {
  const [months, setMonths] = useState(12);
  const [withAi, setWithAi] = useState(false);
  const [drilldown, setDrilldown] = useState<Insight | null>(null);
  const [anchor, setAnchor] = useState(lastCompleteMonth);

  const { data, isLoading, isError, isFetching } = useInsights(months, withAi, anchor);

  const handlePrevMonth = () => {
    setAnchor((current) =>
      current.month === 1
        ? { year: current.year - 1, month: 12 }
        : { year: current.year, month: current.month - 1 },
    );
  };

  const handleNextMonth = () => {
    setAnchor((current) =>
      current.month === 12
        ? { year: current.year + 1, month: 1 }
        : { year: current.year, month: current.month + 1 },
    );
  };

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Empfehlungen</h1>
          <p className="placeholder-note">
            Aus deinen Buchungen gerechnet – jede Zahl lässt sich anklicken und bis zur einzelnen
            Buchung zurückverfolgen.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={handlePrevMonth}
            aria-label="Vorheriger Monat"
          >
            &larr;
          </button>
          <span
            style={{ fontWeight: 600, fontSize: "15px", minWidth: "140px", textAlign: "center" }}
          >
            {formatMonthYear(new Date(anchor.year, anchor.month - 1, 1))}
          </span>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleNextMonth}
            aria-label="Nächster Monat"
          >
            &rarr;
          </button>
        </div>
      </div>

      <div className="category-toolbar">
        <div className="filter-tabs" role="group" aria-label="Betrachtungszeitraum">
          {MONTH_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              className={months === option ? "filter-tab filter-tab--active" : "filter-tab"}
              aria-pressed={months === option}
              onClick={() => setMonths(option)}
            >
              {option} Monate
            </button>
          ))}
        </div>

        <div className="category-toolbar-actions">
          <button
            type="button"
            className="btn-secondary"
            disabled={withAi && isFetching}
            onClick={() => setWithAi(true)}
          >
            {withAi && isFetching ? "KI formuliert…" : "Von der KI kommentieren lassen"}
          </button>
        </div>
      </div>

      {isLoading && <p role="status">Empfehlungen werden gerechnet…</p>}
      {isError && (
        <p role="alert" className="status-error">
          Die Empfehlungen konnten nicht geladen werden.
        </p>
      )}

      {data && data.months_covered === 0 && (
        <p className="placeholder-note">
          Noch keine abgeschlossenen Monate mit Buchungen. Sobald ein voller Monat vorliegt,
          entstehen hier Empfehlungen.
        </p>
      )}

      {data && data.months_covered > 0 && (
        <>
          <div className="insight-summary">
            <SummaryTile
              label="Einnahmen Ø/Monat"
              value={formatMinorUnitsAsEuro(data.monthly_income_minor)}
            />
            <SummaryTile
              label="Ausgaben Ø/Monat"
              value={formatMinorUnitsAsEuro(data.monthly_expense_minor)}
            />
            <SummaryTile
              label="Bleibt Ø/Monat"
              value={formatMinorUnitsAsEuro(data.monthly_net_minor)}
              tone={data.monthly_net_minor < 0 ? "negative" : "positive"}
            />
            <SummaryTile
              label="Erkanntes Potenzial Ø/Monat"
              value={formatMinorUnitsAsEuro(data.monthly_potential_minor)}
              tone={data.monthly_potential_minor > 0 ? "positive" : undefined}
            />
          </div>

          <p className="placeholder-note">
            Zeitraum {data.from_date} bis {data.to_date} ({data.months_covered} vollständige Monate
            bis einschließlich {formatMonthYear(new Date(anchor.year, anchor.month - 1, 1))}
            ).{data.model_name ? ` Kommentare formuliert von ${data.model_name}.` : ""}
          </p>
          {/* Fehlt die Formulierung der KI, bleibt der Bericht vollstaendig -
              aber das Weglassen wird benannt, damit ein fehlendes Modell
              nicht wie "nichts zu sagen" aussieht. */}
          {(data.ai_warnings ?? []).map((warning, index) => (
            <AiReasonNotice
              key={`${warning.code}-${index}`}
              className="placeholder-note"
              prefix="KI-Formulierung nicht verfügbar:"
              code={warning.code}
              message={warning.message}
            />
          ))}

          {(data.insights ?? []).length === 0 ? (
            <p className="placeholder-note">
              Nichts Auffälliges gefunden. Keine Kategorie liegt deutlich über ihrer eigenen
              Grundlinie, und es gibt keine unkategorisierten Buchungen.
            </p>
          ) : (
            <ul className="insight-list">
              {(data.insights ?? []).map((insight) => (
                <li key={insight.key} className="card insight-card">
                  <div className="insight-head">
                    <span className={SEVERITY_CLASS[insight.severity]}>
                      {SEVERITY_LABEL[insight.severity]}
                    </span>
                    <h2 className="card-heading">{insight.title}</h2>
                    {insight.monthly_potential_minor !== null &&
                      insight.monthly_potential_minor !== undefined && (
                        <span className="insight-potential">
                          {formatMinorUnitsAsEuro(insight.monthly_potential_minor)} / Monat
                        </span>
                      )}
                  </div>

                  <p className="card-body">{insight.detail}</p>

                  {(insight.evidence ?? []).length > 0 && (
                    <dl className="insight-evidence">
                      {(insight.evidence ?? []).map((item) => (
                        <div key={item.label}>
                          <dt>{item.label}</dt>
                          <dd>
                            {item.amount_minor !== null && item.amount_minor !== undefined
                              ? formatMinorUnitsAsEuro(item.amount_minor)
                              : (item.count ?? "—")}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}

                  {/* Klar als Zusatz gekennzeichnet: der Rat kommt vom Modell,
                      die Zahlen darüber nicht. */}
                  {insight.ai_advice && (
                    <p className="insight-advice">
                      <span className="status-badge status-badge--neutral">KI-Rat</span>{" "}
                      {insight.ai_advice}
                    </p>
                  )}

                  {insight.drilldown && (
                    <div className="category-actions">
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => setDrilldown(insight)}
                      >
                        Buchungen ansehen
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {drilldown?.drilldown && (
        <TransactionDrilldown
          title={drilldown.title}
          subtitle={`${drilldown.drilldown.from_date} bis ${drilldown.drilldown.to_date}`}
          query={rowsQuery(drilldown.drilldown)}
          onClose={() => setDrilldown(null)}
        />
      )}
    </section>
  );
}

function SummaryTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative";
}) {
  return (
    <div className="insight-tile">
      <span className="insight-tile-label">{label}</span>
      <strong
        className={
          tone === "negative"
            ? "transaction-amount--negative"
            : tone === "positive"
              ? "transaction-amount--positive"
              : undefined
        }
      >
        {value}
      </strong>
    </div>
  );
}
