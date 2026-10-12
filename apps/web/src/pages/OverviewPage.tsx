import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { getOverviewReport, getProjectDashboardSummary } from "../lib/api";
import { formatMinorUnitsAsEuro } from "../lib/money";
import { ProgressBar } from "../components/ProgressBar";
import { rateFixedCostsRatio } from "../hooks/useContracts";
import type { OverviewReportResponse, ProjectDashboardSummary } from "../lib/types";

/**
 * Sind die Vertragskennzahlen in dieser Antwort ueberhaupt enthalten?
 *
 * Im Vertrag sind sie Pflichtfelder. Die Pruefung schuetzt trotzdem gegen eine
 * aeltere API - oder eine zwischengespeicherte Antwort davon: das Dashboard ist
 * die Startseite, und vier zusaetzliche Kennzahlen duerfen sie nicht mitreissen
 * (`undefined.toFixed` beendet sonst den ganzen Baum).
 */
function hasFixedCostsFields(report: OverviewReportResponse): boolean {
  return (
    Number.isFinite(report.fixed_costs_ratio_percent) &&
    Number.isFinite(report.fixed_costs_monthly_minor) &&
    Number.isFinite(report.available_after_fixed_minor) &&
    Number.isFinite(report.upcoming_due_count)
  );
}

/**
 * Fixkostenkennzahlen (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, I7 und I8).
 *
 * "Verfuegbar nach Fixkosten" beantwortet die Frage, die der Budgetrest nicht
 * beantwortet: was bleibt, wenn alles Vereinbarte abgebucht ist - auch das, was
 * diesen Monat noch aussteht. Die Ampel dahinter ist die Einordnung; eine
 * nackte Prozentzahl sagt niemandem, ob 58 % viel ist.
 */
function FixedCostsMetrics({ report }: { report: OverviewReportResponse }) {
  const rating = rateFixedCostsRatio(
    report.fixed_costs_ratio_percent,
    report.fixed_costs_monthly_minor,
  );
  const ratioKnown = rating.tone !== "neutral";

  return (
    <div className="metric-grid">
      <div className="card metric-card">
        <span className="metric-label">Verfügbar nach Fixkosten</span>
        <span
          className={`metric-value ${
            report.available_after_fixed_minor < 0
              ? "transaction-amount--negative"
              : "transaction-amount--positive"
          }`}
        >
          {formatMinorUnitsAsEuro(report.available_after_fixed_minor)}
        </span>
        <span className="metric-hint">Einnahmen minus laufende Verträge</span>
      </div>

      <div className="card metric-card">
        <span className="metric-label">Fixkostenquote</span>
        <span className="metric-value">
          {ratioKnown ? `${report.fixed_costs_ratio_percent.toFixed(1)} %` : "—"}
        </span>
        <ProgressBar
          value={Math.min(100, Math.max(0, report.fixed_costs_ratio_percent))}
          tone={rating.tone}
          testId="overview-fixed-costs-ratio"
        />
        <span className="metric-hint">
          <span className={`status-badge status-badge--${rating.tone}`}>{rating.label}</span>{" "}
          {rating.hint}
        </span>
      </div>

      <div className="card metric-card">
        <span className="metric-label">Monatliche Fixkosten</span>
        <span className="metric-value transaction-amount--negative">
          {formatMinorUnitsAsEuro(report.fixed_costs_monthly_minor)}
        </span>
        <span className="metric-hint">Alle laufenden Verträge, auf den Monat gerechnet</span>
      </div>

      <div className="card metric-card">
        <span className="metric-label">Fällig in 7 Tagen</span>
        <span className="metric-value">{report.upcoming_due_count}</span>
        <span className="metric-hint">Erwartete Abbuchungen aus Verträgen</span>
      </div>
    </div>
  );
}

/**
 * Sind die Vorhaben-Dashboardkennzahlen in dieser Antwort enthalten?
 *
 * Eigener Endpunkt (`/v1/projects/dashboard-summary`, docs/
 * VORHABEN-GROSSINVESTITIONEN-PLAN.md, 11.7), also derselbe Schutz wie bei
 * `hasFixedCostsFields`: eine zwischengespeicherte alte Antwort darf die
 * Startseite nicht mit `undefined.toFixed` abschiessen.
 */
function hasProjectDashboardFields(summary: ProjectDashboardSummary): boolean {
  return (
    Number.isFinite(summary.active_project_count) &&
    Number.isFinite(summary.total_secured_gap_minor)
  );
}

// Kurzbeschriftungen fuer die Warncodes aus calculate_financial_summary/
// calculate_scenario (siehe app/api/v1/routes/projects.py). Bewusst eine
// eigene, kleine Kopie statt einer geteilten Konstante: ProjectDetailPage.tsx
// und FinancingPanel.tsx pflegen aus demselben Grund je ihre eigene - die
// Codes sind Server-Vertrag, die deutschen Texte sind reine UI-Entscheidung
// je Ansicht.
const DASHBOARD_WARNING_LABELS: Record<string, string> = {
  funding_gap: "Geplante Finanzierung deckt die Projektkosten noch nicht.",
  secured_funding_gap: "Gesicherte Finanzierung deckt die Projektkosten noch nicht.",
  equity_overallocated: "Ein Konto ist mehrfach als Eigenkapital verplant.",
  liquidity_reserve_breached: "Die Mindestreserve wird in mindestens einem Monat unterschritten.",
  goal_protection_breached: "Eine geplante Entnahme verletzt das Schutzminimum eines Sparziels.",
  cost_schedule_incomplete: "Zahlungsplan unvollständig.",
  calculation_horizon_reached: "Der Rechenhorizont wurde erreicht.",
};

/**
 * Vorhaben-Uebersichtskarte (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, 11.7).
 *
 * Eigene Reihe unter den Fixkosten statt vermischt mit Einnahmen/Ausgaben/
 * Budgetrest: eine Bausumme ist weder eine Buchung noch ein vereinbarter
 * Fixbetrag, sondern eine Projektkennzahl aus dem Vorhaben-Modul. Jede Zahl
 * kommt unveraendert vom Server (`calculate_financial_summary`/
 * `calculate_scenario` je Projekt), damit sie nie von der Vorhaben-
 * Detailseite desselben Projekts abweicht.
 */
function ProjectDashboardMetrics({ summary }: { summary: ProjectDashboardSummary }) {
  return (
    <div className="metric-grid">
      <div className="card metric-card">
        <span className="metric-label">Aktive Vorhaben</span>
        <span className="metric-value">{summary.active_project_count}</span>
        <span className="metric-hint">Noch nicht abgeschlossen, storniert oder archiviert</span>
      </div>

      <div className="card metric-card">
        <span className="metric-label">Nächste große Zahlung</span>
        {summary.next_payment ? (
          <>
            <span className="metric-value">
              {formatMinorUnitsAsEuro(summary.next_payment.amount_minor)}
            </span>
            <span className="metric-hint">
              {summary.next_payment.project_name} · {summary.next_payment.cost_item_name} ·{" "}
              {summary.next_payment.due_on}
            </span>
          </>
        ) : (
          <>
            <span className="metric-value">—</span>
            <span className="metric-hint">Keine geplante Zahlung offen</span>
          </>
        )}
      </div>

      <div className="card metric-card">
        <span className="metric-label">Gesicherte Lücke</span>
        <span
          className={`metric-value ${
            summary.total_secured_gap_minor > 0
              ? "transaction-amount--negative"
              : "transaction-amount--positive"
          }`}
        >
          {formatMinorUnitsAsEuro(summary.total_secured_gap_minor)}
        </span>
        <span className="metric-hint">Cashbedarf minus gesicherte Finanzierung je Vorhaben</span>
      </div>

      <div className="card metric-card">
        <span className="metric-label">Wichtigste Warnung</span>
        {summary.top_warning ? (
          <>
            <span className="metric-value" style={{ fontSize: "14px" }}>
              {DASHBOARD_WARNING_LABELS[summary.top_warning.code] ?? summary.top_warning.code}
            </span>
            <span className="metric-hint">{summary.top_warning.project_name}</span>
          </>
        ) : (
          <>
            <span className="metric-value">—</span>
            <span className="metric-hint">Keine offenen Warnungen</span>
          </>
        )}
      </div>
    </div>
  );
}

export function OverviewPage() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const {
    data: report,
    isLoading,
    error,
  } = useQuery<OverviewReportResponse>({
    queryKey: ["overview-report", householdId],
    queryFn: () => getOverviewReport(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const { data: projectDashboard } = useQuery<ProjectDashboardSummary>({
    queryKey: ["project-dashboard-summary", householdId],
    queryFn: () => getProjectDashboardSummary(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  return (
    <section className="page" data-testid="page-overview">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Übersicht</h1>
          <p className="placeholder-note">
            Ihr zentrales Finanz-Dashboard mit Kennzahlen, Cashflow und Budgetstatus.
          </p>
        </div>
      </div>

      {/* Hinweis-Banner bei offenen Review-Fällen */}
      {report && report.open_review_count > 0 && (
        <div
          className="selection-bar"
          style={{
            marginBottom: "var(--space-5)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <span className="selection-bar-count">
              ⚠️ {report.open_review_count} offene Review-Fälle
            </span>
            <span className="selection-bar-message" style={{ marginLeft: "var(--space-2)" }}>
              Es gibt unklassifizierte Umsätze oder offene Abo-/Dubletten-Vorschläge.
            </span>
          </div>
          <Link
            to="/review"
            className="btn-primary"
            style={{ padding: "4px 10px", fontSize: "12px", textDecoration: "none" }}
          >
            Zur Review-Inbox &rarr;
          </Link>
        </div>
      )}

      {isLoading && <p className="placeholder-note">Lade Dashboard-Daten...</p>}

      {error && (
        <div className="card status-error" style={{ marginBottom: "var(--space-4)" }}>
          Fehler beim Laden des Übersicht-Berichts.
        </div>
      )}

      {!isLoading && report && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
          {/* Metrik-Karten */}
          <div className="metric-grid">
            <div className="card metric-card">
              <span className="metric-label">Einnahmen</span>
              <span className="metric-value transaction-amount--positive">
                {formatMinorUnitsAsEuro(report.total_income_minor)}
              </span>
              <span className="metric-hint">Gesamte Einnahmen im Zeitraum</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Ausgaben</span>
              <span className="metric-value transaction-amount--negative">
                {formatMinorUnitsAsEuro(report.total_expense_minor)}
              </span>
              <span className="metric-hint">Gesamte Ausgaben im Zeitraum</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Netto-Ersparnis</span>
              <span
                className={`metric-value ${report.net_savings_minor >= 0 ? "transaction-amount--positive" : "transaction-amount--negative"}`}
              >
                {formatMinorUnitsAsEuro(report.net_savings_minor)}
              </span>
              <span className="metric-hint">Einnahmen minus Ausgaben</span>
            </div>
            {/* budget_remaining_minor ist total_remaining_minor der
                Monatsuebersicht: Plan plus Uebertrag minus Ist, ausschliesslich
                ueber Ausgabenkategorien (Einnahmen zaehlen dort nicht mit). */}
            <div className="card metric-card">
              <span className="metric-label">Budgetrest</span>
              <span
                className={`metric-value ${report.budget_remaining_minor < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
              >
                {formatMinorUnitsAsEuro(report.budget_remaining_minor)}
              </span>
              <span className="metric-hint">Plan plus Übertrag minus Ist</span>
            </div>
          </div>

          {/* Fixkosten stehen bewusst in einer eigenen Reihe unter den
              Ist-Kennzahlen: sie sind Erwartungen aus Vertraegen, keine
              gebuchten Betraege. In dieselbe Reihe gemischt lasen sie sich wie
              eine sechste Ist-Zahl. */}
          {hasFixedCostsFields(report) && <FixedCostsMetrics report={report} />}

          {/* Vorhaben stehen aus demselben Grund in einer eigenen Reihe wie
              die Fixkosten oben: eine Bausumme ist kein Ist-Wert dieses
              Berichts und keine vereinbarte Fixkostenzahl, sondern eine
              eigene Projektkennzahl aus dem Vorhaben-Modul. */}
          {projectDashboard && hasProjectDashboardFields(projectDashboard) && (
            <ProjectDashboardMetrics summary={projectDashboard} />
          )}

          <div className="section-grid">
            {/* Top-Ausgabenkategorien */}
            <div className="card">
              <h2 className="card-heading">Top-Ausgabenkategorien</h2>
              {report.top_expense_categories.length === 0 ? (
                <p className="card-body">Keine Ausgaben im aktuellen Zeitraum.</p>
              ) : (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "var(--space-3)",
                    marginTop: "var(--space-3)",
                  }}
                >
                  {report.top_expense_categories.map((cat) => (
                    <div
                      key={cat.category_id}
                      style={{ display: "flex", flexDirection: "column", gap: "2px" }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          fontSize: "13px",
                        }}
                      >
                        <span style={{ fontWeight: 500 }}>{cat.category_name}</span>
                        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                          {formatMinorUnitsAsEuro(cat.amount_minor)} ({Math.round(cat.percentage)}%)
                        </span>
                      </div>
                      <ProgressBar
                        value={Math.min(100, Math.max(0, cat.percentage))}
                        tone="warning"
                        testId={`overview-category-share-${cat.category_id}`}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Cashflow-Monatsverlauf */}
            <div className="card">
              <h2 className="card-heading">Cashflow-Monatsverlauf</h2>
              {report.cashflow_last_6_months.length === 0 ? (
                <p className="card-body">Keine Cashflow-Daten vorhanden.</p>
              ) : (
                <div
                  className="txn-table-wrapper"
                  style={{ marginTop: "var(--space-3)", border: "none" }}
                >
                  <table className="txn-table">
                    <thead>
                      <tr>
                        <th className="txn-th">Monat</th>
                        <th className="txn-th txn-th--amount">Einnahmen</th>
                        <th className="txn-th txn-th--amount">Ausgaben</th>
                        <th className="txn-th txn-th--amount">Netto</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.cashflow_last_6_months.map((cf) => (
                        <tr key={`${cf.year}-${cf.month}`} className="txn-tr">
                          <td className="txn-td" style={{ fontFamily: "var(--font-mono)" }}>
                            {String(cf.month).padStart(2, "0")}/{cf.year}
                          </td>
                          <td
                            className="txn-td txn-td--amount transaction-amount--positive"
                            style={{ fontFamily: "var(--font-mono)" }}
                          >
                            {formatMinorUnitsAsEuro(cf.income_minor)}
                          </td>
                          <td
                            className="txn-td txn-td--amount transaction-amount--negative"
                            style={{ fontFamily: "var(--font-mono)" }}
                          >
                            {formatMinorUnitsAsEuro(cf.expense_minor)}
                          </td>
                          <td
                            className={`txn-td txn-td--amount ${cf.net_minor >= 0 ? "transaction-amount--positive" : "transaction-amount--negative"}`}
                            style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
                          >
                            {formatMinorUnitsAsEuro(cf.net_minor)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
