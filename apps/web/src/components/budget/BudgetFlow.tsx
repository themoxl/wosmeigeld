import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ProgressBar } from "../ProgressBar";
import { rateFixedCostsRatio } from "../../hooks/useContracts";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { BudgetSummary } from "../../lib/types";
import "./BudgetFlow.css";

/**
 * BudgetFlow - der rote Faden der Budgetseite.
 *
 * WARUM ZWEI BLOECKE (und nicht eine einzige Kachelwand):
 * Auf der Budgetseite treffen zwei Geldbegriffe aufeinander, die niemals in
 * derselben Summe stehen duerfen.
 *
 * BLOCK 1 - "Vorausschau (Vertraege und Ruecklagen)": erwartete Einnahmen
 * minus regelmaessige Vertraege, geglaettete Quartals-/Jahreskosten, uebrige
 * Kategorie-Planbetraege und aktivierte Sparziele. Reine Erwartung, dazu
 * existiert keine einzige Buchung. Diese Kette rechnet das Backend fertig aus
 * (planning_expected_income_minor ... planning_truly_free_minor); hier wird sie
 * nur sichtbar gemacht, nicht nachgerechnet.
 *
 * BLOCK 2 - "Diesen Monat gebucht": Planbetraege der Ausgabenkategorien gegen
 * tatsaechlich gebuchte Ausgaben, dazu Rest und Sparquote. Gebuchte
 * Wirklichkeit, keine Erwartung.
 *
 * Beide Bloecke duerfen unterschiedliche Zahlen zeigen und duerfen NICHT
 * addiert werden: eine bereits abgebuchte Vertragsrate steckt gleichzeitig im
 * Vertragsbetrag (Block 1) und im Ist der Kategorie (Block 2).
 * total_planned_minor + fixed_costs_monthly_minor ist deshalb immer falsch.
 *
 * Merksatz zur Zuordnung eines Feldes: alles mit "planning", "contract" oder
 * "reserve" im Namen gehoert in Block 1, alles mit "spent", "remaining",
 * "rollover" oder "savings_rate" in Block 2.
 */

export interface BudgetFlowProps {
  /** Monatsantwort des Backends (GET /v1/budgets/{year}/{month}). */
  budget: BudgetSummary;
  /**
   * Steuert, ob der Wasserfall ueber Stufe 2 hinaus laeuft. Aus = die Seite
   * zeigt nur gebuchtes Geld plus Vertraege; an = zusaetzlich Ruecklagen,
   * Sparziele und "wirklich frei". Der Zustand liegt bewusst bei der Seite,
   * weil er dort persistiert wird.
   */
  includeReserves: boolean;
  /** Wird mit dem neuen Zustand des Schalters gerufen. */
  onIncludeReservesChange: (value: boolean) => void;
  /**
   * Oeffnet den Ruecklagen-Tab. Der Link daneben traegt zwar die passende
   * Adresse, aber der Tab der Budgetseite liegt in deren Zustand und liest die
   * Adresse nur beim ersten Rendern - ohne diesen Rueckruf wuerde ein Klick die
   * URL setzen und die Seite danach sofort wieder auf "monthly" zurueckschreiben.
   */
  onOpenReserves: () => void;
  /** Angezeigter Monat - nur fuer den Link in den Ruecklagen-Tab. */
  year: number;
  month: number;
}

type FlowTone = "positive" | "warning" | "highlight" | "neutral";

/**
 * Robustheit gegen unvollstaendige Antworten: ein fehlendes Feld darf keine
 * NaN-Kaskade in Betraegen und Balkenbreiten ausloesen. Lieber eine ehrliche
 * 0 als ein "NaN EUR" in einer Finanzoberflaeche.
 */
function num(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/**
 * Anteil an der Bezugsgroesse in Prozent, hart auf 0-100 begrenzt.
 * Gibt `null` zurueck, wenn es keine sinnvolle Bezugsgroesse gibt (0 oder
 * negativ) - dann darf gar kein Balken behauptet werden.
 */
function sharePercent(value: number, basis: number): number | null {
  if (basis <= 0) {
    return null;
  }
  const percent = (value / basis) * 100;
  if (!Number.isFinite(percent)) {
    return null;
  }
  return Math.min(100, Math.max(0, percent));
}

function formatShare(percent: number | null): string {
  return percent === null ? "–" : `${Math.round(percent)} % der Einnahmen`;
}

/**
 * Balken der Kette: bewusst handgemacht statt <ProgressBar>. Die Kette
 * braucht zwei Hoehen (Stufe kraeftig, Abzug leise), eine Beschriftung
 * darunter und keine eigene ARIA-Rolle - der Prozentwert steht als echter Text
 * daneben, ein zweites role="progressbar" je Zeile waere fuer Screenreader nur
 * Laerm. <ProgressBar> bleibt dort, wo es hingehoert: Fixkostenquote und
 * Gebucht/Geplant, beide mit genau einer Bedeutung.
 */
function FlowMeter({ percent, tone }: { percent: number | null; tone: FlowTone }) {
  return (
    <div className="budget-flow__meter">
      <div className="budget-flow__bar" aria-hidden="true">
        <div
          className={`budget-flow__bar-fill budget-flow__bar-fill--${tone}`}
          style={{ width: `${percent ?? 0}%` }}
        />
      </div>
      <span className="budget-flow__share">{formatShare(percent)}</span>
    </div>
  );
}

function FlowStage({
  rank,
  label,
  hint,
  amount,
  basis,
  tone,
  variant = "step",
  testId,
  children,
}: {
  rank: number;
  label: string;
  hint: string;
  amount: number;
  basis: number;
  tone: FlowTone;
  /** "result" markiert das Endergebnis, "gap" zusaetzlich eine Unterdeckung. */
  variant?: "step" | "result" | "gap";
  testId: string;
  children?: ReactNode;
}) {
  const amountTone =
    variant === "gap" ? "warning" : variant === "result" || tone === "positive" ? "positive" : "";
  return (
    <li
      className={`budget-flow__row budget-flow__row--stage budget-flow__row--${variant}`}
      data-testid={testId}
    >
      <span className="budget-flow__rank" aria-hidden="true">
        {rank}
      </span>
      <div className="budget-flow__main">
        <strong className="budget-flow__label">{label}</strong>
        <small className="budget-flow__hint">{hint}</small>
        {children}
      </div>
      <FlowMeter percent={sharePercent(amount, basis)} tone={tone} />
      <span
        className={`budget-flow__amount ${amountTone ? `budget-flow__amount--${amountTone}` : ""}`}
      >
        {formatMinorUnitsAsEuro(amount)}
      </span>
    </li>
  );
}

function FlowDeduction({
  label,
  hint,
  amount,
  basis,
  tone,
  reserve = false,
  testId,
  children,
}: {
  label: string;
  hint: string;
  amount: number;
  basis: number;
  tone: FlowTone;
  /** Gestrichelte Goldkante = Ruecklage, wie in .planning-equation__row--reserve. */
  reserve?: boolean;
  testId: string;
  children?: ReactNode;
}) {
  return (
    <li
      className={`budget-flow__row budget-flow__row--deduction ${
        reserve ? "budget-flow__row--reserve" : ""
      }`}
      data-testid={testId}
    >
      <span className="budget-flow__sign" aria-hidden="true">
        −
      </span>
      <div className="budget-flow__main">
        <span className="budget-flow__label budget-flow__label--quiet">{label}</span>
        <small className="budget-flow__hint">{hint}</small>
        {children}
      </div>
      <FlowMeter percent={sharePercent(amount, basis)} tone={tone} />
      <span className="budget-flow__amount budget-flow__amount--quiet">
        −{formatMinorUnitsAsEuro(amount)}
      </span>
    </li>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="budget-flow__fact">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function BudgetFlow({
  budget,
  includeReserves,
  onIncludeReservesChange,
  onOpenReserves,
  year,
  month,
}: BudgetFlowProps) {
  // --- Block 1: Vorausschau -------------------------------------------------
  //
  // Anker der Kette ist planning_expected_income_minor, NICHT income_basis_minor.
  // Die beiden Groessen sind absichtlich verschieden: die Planung bevorzugt den
  // Vertragswert (fällt nur ohne Einnahmevertrag auf das Monats-Ist zurück),
  // die Quotenbasis bevorzugt umgekehrt das Ist. Nur mit dem Planungsanker
  // treffen die vom Backend gelieferten Zwischenergebnisse exakt zu.
  const expectedIncome = num(budget.planning_expected_income_minor);
  const regularFixed = num(budget.regular_fixed_costs_monthly_minor);
  const fixedReserves = num(budget.fixed_cost_reserves_monthly_minor);
  const variablePlanned = num(budget.variable_planned_budget_minor);
  const goalReserves = num(budget.goal_reserves_monthly_minor);
  const surplusBeforeGoals = num(budget.surplus_before_goal_reserves_minor);
  const trulyFree = num(budget.planning_truly_free_minor);
  const incomeBasis = num(budget.income_basis_minor);

  // Reihenfolge der Abzuege bewusst wie im Backend
  // (budget_service.get_budget_summary):
  //   erwartete Einnahmen − regelmaessige Fixkosten − Fixkosten-Ruecklagen
  //   − variable Planbetraege = surplus_before_goal_reserves_minor
  //   − aktivierte Sparziele = planning_truly_free_minor
  // Die Fixkosten-Ruecklagen stehen also VOR und nicht nach der Stufe "frei vor
  // Sparzielen" - zoege man sie darunter, waere die angezeigte Stufe 3 um genau
  // diesen Betrag falsch.
  //
  // Stufe 2 wird hier gerechnet und nicht aus available_after_fixed_minor
  // gelesen: jenes Feld ist income_basis_minor − fixed_costs_monthly_minor,
  // also eine ANDERE Einnahmenbasis minus ALLE auf den Monat normierten
  // Vertraege. In dieser Kette wuerden dadurch Quartals- und Jahresvertraege
  // doppelt zaehlen (einmal normiert, einmal als Ruecklage). Es steht deshalb
  // als klar benannte Nebenangabe im Hinweis der Fixkostenzeile.
  const afterRegularFixed = expectedIncome - regularFixed;

  const ratio = rateFixedCostsRatio(
    num(budget.fixed_costs_ratio_percent),
    num(budget.fixed_costs_monthly_minor),
  );
  const ratioKnown = ratio.tone !== "neutral";
  // Am Monatsersten ist noch nichts gebucht; dann rechnet das Backend gegen die
  // Einnahmevertraege. Ohne diesen Satz ist die Quote nicht nachrechenbar.
  const ratioBasisHint =
    budget.fixed_costs_income_basis === "contract"
      ? "Bezug: Einnahmen aus Verträgen (noch nichts gebucht)"
      : "Bezug: gebuchte Einnahmen dieses Monats";
  const incomeSourceHint =
    budget.planning_income_basis === "contract"
      ? "aus Einnahmeverträgen"
      : "aus dem Monats-Ist (keine Einnahmeverträge hinterlegt)";

  const plannedIncome = num(budget.total_planned_income_minor);
  const rolloverIncome = num(budget.total_rollover_income_minor);
  const incomeRemaining = num(budget.total_income_remaining_minor);
  // Das Ist der EINNAHMEKATEGORIEN, aus der Backend-Identitaet
  // total_income_remaining = Plan + Uebertrag - Ist zurueckgerechnet. Bewusst
  // NICHT total_income_minor: dort stecken zusaetzlich Zufluesse ohne
  // Kategorie, die zu keinem Plan gehoeren. Stuende der groessere Wert hier,
  // ergaeben Plan, Ist und "noch ausstehend" auf derselben Karte keine
  // Rechnung mehr - 6.899 Plan, 6.900 eingegangen und trotzdem 99 ausstehend.
  const incomeOnCategories = plannedIncome + rolloverIncome - incomeRemaining;
  // Genau diese Differenz ist der Grund, warum die Bezugsgroesse groesser sein
  // kann als das Ist der Einnahmekategorien.
  const uncategorizedIncome = num(budget.total_income_minor) - incomeOnCategories;

  const excludedContract = num(budget.variable_planned_excluded_contract_minor);
  const excludedGoalSavings = num(budget.variable_planned_excluded_goal_savings_minor);
  // Herausgerechnet wird der VOLLE Planbetrag der betroffenen Kategorie, nicht
  // der Vertragsbetrag. "Steckt schon im Vertrag" waere deshalb falsch, sobald
  // der Plan groesser ist als der Vertrag (600 EUR Lebensmittelplan, 20 EUR
  // Abo-Vertrag auf derselben Kategorie).
  const excludedHints = [
    excludedContract !== 0
      ? `${formatMinorUnitsAsEuro(excludedContract)} Planbeträge liegen auf Kategorien mit gezähltem Vertrag und bleiben hier außen vor`
      : null,
    excludedGoalSavings !== 0
      ? `${formatMinorUnitsAsEuro(excludedGoalSavings)} Planbeträge auf Sparkategorien bleiben außen vor, weil die Sparziele unten stehen`
      : null,
  ].filter((entry): entry is string => entry !== null);

  // --- Block 2: gebuchtes Geld ---------------------------------------------
  const totalPlanned = num(budget.total_planned_minor);
  const totalRollover = num(budget.total_rollover_minor);
  const totalSpent = num(budget.total_spent_minor);
  const totalRemaining = num(budget.total_remaining_minor);
  const uncategorized = num(budget.uncategorized_spent_minor);
  const planBasis = totalPlanned + totalRollover;
  const bookedOverspent = planBasis > 0 && totalSpent > planBasis;

  return (
    <div className="budget-flow" data-testid="budget-flow">
      <section
        className="budget-flow__panel budget-flow__panel--forecast"
        aria-label="Vorausschau aus Verträgen und Rücklagen"
      >
        <header className="budget-flow__panel-header">
          <div>
            <span className="status-badge status-badge--highlight">Kalkulatorisch</span>
            <h2 className="budget-flow__title">Vorausschau (Verträge und Rücklagen)</h2>
          </div>
          <span className="budget-flow__panel-note">
            Erwartungen, keine Buchungen · Bezugsgröße der Quoten{" "}
            {formatMinorUnitsAsEuro(incomeBasis)}
          </span>
        </header>

        <ol className="budget-flow__ledger">
          <FlowStage
            rank={1}
            label="Einnahmen"
            hint={`Erwartet ${incomeSourceHint}`}
            amount={expectedIncome}
            basis={expectedIncome}
            tone="positive"
            testId="budget-flow-stage-income"
          >
            <dl className="budget-flow__facts">
              <Fact
                label="Plan der Einnahmekategorien"
                value={formatMinorUnitsAsEuro(plannedIncome)}
              />
              {/* Nicht "Diesen Monat gebucht": genau dieser Wortlaut ist weiter
                  unten die Ueberschrift des Ausgabenblocks. Zwei gleich
                  benannte Dinge mit verschiedener Bedeutung auf einer Seite
                  sind der Fehler, den diese Seite gerade loswerden soll. */}
              <Fact
                label="Auf Einnahmekategorien gebucht"
                value={formatMinorUnitsAsEuro(incomeOnCategories)}
              />
              {(plannedIncome !== 0 || rolloverIncome !== 0) && (
                <Fact
                  label={incomeRemaining >= 0 ? "Noch ausstehend" : "Mehr als geplant"}
                  value={formatMinorUnitsAsEuro(Math.abs(incomeRemaining))}
                />
              )}
              {uncategorizedIncome !== 0 && (
                <Fact
                  label="Zusätzlich ohne Kategorie eingegangen"
                  value={formatMinorUnitsAsEuro(uncategorizedIncome)}
                />
              )}
              <Fact label="Bezugsgröße aller Quoten" value={formatMinorUnitsAsEuro(incomeBasis)} />
            </dl>
          </FlowStage>

          <FlowDeduction
            label="Regelmäßige Fixkosten"
            // fixed_costs_contract_count zaehlt ALLE laufenden
            // Ausgabenvertraege, der Betrag daneben nur die woechentlichen und
            // monatlichen. Die Zahl darf deshalb nicht so klingen, als gehoere
            // sie zu diesem Betrag.
            hint={`Nur wöchentliche und monatliche Ausgabenverträge · insgesamt ${budget.fixed_costs_contract_count} laufende Ausgabenverträge`}
            amount={regularFixed}
            basis={expectedIncome}
            tone="warning"
            testId="budget-flow-deduction-fixed"
          >
            <div className="budget-flow__ratio">
              <span className="budget-flow__ratio-label">Fixkostenquote</span>
              <span className="budget-flow__ratio-value" data-testid="budget-fixed-costs-ratio">
                {ratioKnown ? `${budget.fixed_costs_ratio_percent.toFixed(1)} %` : "—"}
              </span>
              <ProgressBar
                className="budget-flow__ratio-bar"
                value={Math.min(100, Math.max(0, num(budget.fixed_costs_ratio_percent)))}
                tone={ratio.tone}
                testId="budget-fixed-costs-ratio-bar"
              />
              <span className={`status-badge status-badge--${ratio.tone}`}>{ratio.label}</span>
            </div>
            <small className="budget-flow__hint">
              {ratioBasisHint} · Die Quote misst <em>alle</em> Verträge auf den Monat normiert (
              {formatMinorUnitsAsEuro(num(budget.fixed_costs_monthly_minor))}); davon bleiben{" "}
              {formatMinorUnitsAsEuro(num(budget.available_after_fixed_minor))} übrig. Diese Kette
              zieht nur die regelmäßigen ab, damit Quartals- und Jahresverträge nicht doppelt
              zählen.
            </small>
          </FlowDeduction>

          <FlowStage
            rank={2}
            label="Nach regelmäßigen Fixkosten"
            hint="Erwartete Einnahmen minus wöchentliche und monatliche Verträge"
            amount={afterRegularFixed}
            basis={expectedIncome}
            tone="neutral"
            variant={afterRegularFixed < 0 ? "gap" : "step"}
            testId="budget-flow-stage-after-fixed"
          />

          {includeReserves && (
            <>
              <FlowDeduction
                label="Fixkosten-Rücklagen"
                hint={`${budget.fixed_cost_reserve_count} geglättete Quartals-, Halbjahres- oder Jahreskosten`}
                amount={fixedReserves}
                basis={expectedIncome}
                tone="highlight"
                reserve
                testId="budget-flow-deduction-fixed-reserves"
              />

              <FlowDeduction
                label="Variable Planbeträge"
                hint={
                  excludedHints.length > 0
                    ? `Übrige Kategorie-Planbeträge · ${excludedHints.join(" · ")}`
                    : "Übrige Kategorie-Planbeträge; nichts wurde herausgerechnet"
                }
                amount={variablePlanned}
                basis={expectedIncome}
                tone="neutral"
                testId="budget-flow-deduction-variable"
              />

              <FlowStage
                rank={3}
                label="Frei vor Sparzielen"
                hint="Nach Verträgen, Rücklagen und variablen Planbeträgen"
                amount={surplusBeforeGoals}
                basis={expectedIncome}
                tone="neutral"
                variant={surplusBeforeGoals < 0 ? "gap" : "step"}
                testId="budget-flow-stage-surplus"
              />

              <FlowDeduction
                label="Aktivierte Sparziele"
                hint={`${budget.goal_reserve_count} Sparziel(e) im Monatsplan`}
                amount={goalReserves}
                basis={expectedIncome}
                tone="highlight"
                reserve
                testId="budget-flow-deduction-goals"
              />

              <FlowStage
                rank={4}
                label="Wirklich frei nach Rücklagen"
                hint={
                  trulyFree < 0
                    ? "Die Planung hat eine monatliche Lücke – so viel fehlt jeden Monat"
                    : "Nach allen Planungsschichten übrig"
                }
                amount={trulyFree}
                basis={expectedIncome}
                tone={trulyFree < 0 ? "warning" : "positive"}
                variant={trulyFree < 0 ? "gap" : "result"}
                testId="budget-flow-stage-truly-free"
              />
            </>
          )}
        </ol>

        <div className="budget-flow__toggle-row" data-testid="budget-flow-reserves-toggle">
          <div className="budget-flow__toggle-main">
            <label className="planning-checkbox">
              <input
                type="checkbox"
                checked={includeReserves}
                onChange={(event) => onIncludeReservesChange(event.target.checked)}
              />
              Rücklagen in den Monatsplan einrechnen
            </label>
            <p className="budget-flow__toggle-hint">
              {/* Der Schalter blendet nicht nur die beiden Rücklagenzeilen ein,
                  sondern die ganze Kette ab Stufe 3 - inklusive der variablen
                  Planbeträge, die keine Rücklage sind. Das muss dranstehen,
                  sonst sucht man den Posten vergeblich. */}
              {includeReserves
                ? "Die Kette läuft bis „wirklich frei“ durch, inklusive der variablen Planbeträge. Nur diese Ansicht ändert sich – Buchungen, Kontostände und Sparquote bleiben gleich."
                : "Einrechnen, um Planbeträge, Fixkosten-Rücklagen und Sparziele abzuziehen und zu sehen, was wirklich frei bleibt."}
            </p>
          </div>
          {includeReserves && (
            <div className="budget-flow__toggle-aside">
              <div className="budget-flow__aside-fact">
                <span>Empfohlene Rücklagen</span>
                <strong>
                  {formatMinorUnitsAsEuro(num(budget.total_calculational_reserves_minor))}
                </strong>
              </div>
              {/* Echter Link (Adresse teilbar, mittlere Maustaste oeffnet den
                  Tab) plus Rueckruf: die Budgetseite haelt den aktiven Tab im
                  Zustand und liest die Adresse nur beim Mounten. */}
              <Link
                className="btn-secondary"
                to={`/budget?tab=reserves&year=${year}&month=${month}`}
                onClick={onOpenReserves}
              >
                Rücklagen öffnen
              </Link>
            </div>
          )}
        </div>
      </section>

      <section
        className="budget-flow__panel budget-flow__panel--booked"
        aria-label="Gebuchte Ausgaben dieses Monats"
      >
        <header className="budget-flow__panel-header">
          <div>
            <span className="status-badge status-badge--neutral">Gebucht</span>
            <h2 className="budget-flow__title">Diesen Monat gebucht</h2>
          </div>
        </header>
        <p className="budget-flow__panel-note budget-flow__panel-note--block">
          Andere Zahlen als oben, und das ist richtig so: oben stehen Erwartungen aus Verträgen und
          Rücklagen, hier ausschließlich gebuchtes Geld. Addieren darf man beides nicht – eine
          abgebuchte Vertragsrate steckt in beiden Blöcken.
        </p>

        <div className="budget-flow__booked-grid">
          <div className="budget-flow__stat">
            <span className="budget-flow__stat-label">Geplante Ausgaben</span>
            <span className="budget-flow__stat-value">{formatMinorUnitsAsEuro(totalPlanned)}</span>
            <span className="budget-flow__stat-hint">
              {totalRollover !== 0
                ? `zzgl. Übertrag ${formatMinorUnitsAsEuro(totalRollover)} = ${formatMinorUnitsAsEuro(planBasis)}`
                : "Summe der Kategorie-Planbeträge"}
            </span>
          </div>
          <div className="budget-flow__stat">
            <span className="budget-flow__stat-label">Gebucht</span>
            <span className="budget-flow__stat-value">{formatMinorUnitsAsEuro(totalSpent)}</span>
            <span className="budget-flow__stat-hint">Tatsächliche Ausgaben, ohne Einnahmen</span>
          </div>
          <div className="budget-flow__stat">
            <span className="budget-flow__stat-label">Verbleibend</span>
            <span
              className={`budget-flow__stat-value ${
                totalRemaining < 0
                  ? "budget-flow__amount--warning"
                  : "budget-flow__amount--positive"
              }`}
            >
              {formatMinorUnitsAsEuro(totalRemaining)}
            </span>
            <span className="budget-flow__stat-hint">Plan plus Übertrag minus Gebucht</span>
          </div>
          <div className="budget-flow__stat">
            <span className="budget-flow__stat-label">Sparquote</span>
            <span className="budget-flow__stat-value">
              {num(budget.savings_rate_percent).toFixed(1)} %
            </span>
            <span className="budget-flow__stat-hint">Einnahmen minus Ausgaben, je Einnahmen</span>
          </div>
        </div>

        <div className="budget-flow__booked-progress">
          <ProgressBar
            value={totalSpent}
            max={planBasis}
            tone={bookedOverspent ? "warning" : "positive"}
            testId="budget-booked-progress"
          />
          <span className="budget-flow__share">
            {planBasis > 0
              ? `${formatMinorUnitsAsEuro(totalSpent)} von ${formatMinorUnitsAsEuro(planBasis)} gebucht`
              : "Noch kein Planbetrag hinterlegt – ohne Plan gibt es keinen Fortschritt"}
          </span>
        </div>

        {uncategorized !== 0 && (
          <p className="budget-flow__warn" data-testid="budget-flow-uncategorized">
            <span className="status-badge status-badge--warning">Ohne Kategorie</span>
            davon {formatMinorUnitsAsEuro(uncategorized)} noch ohne Kategorie – genau diese
            Differenz fehlt in der Summe der Gruppen weiter unten.
          </p>
        )}
      </section>
    </div>
  );
}
