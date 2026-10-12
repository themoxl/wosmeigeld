import { useId } from "react";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { BudgetSummary } from "../../lib/types";
import "./BucketSplit.css";

export type BudgetBucketKey = "needs" | "wants" | "savings" | "unassigned";

export interface BucketSplitProps {
  /**
   * Monatszusammenfassung des Budgets. Gelesen werden ausschliesslich die vier
   * bucket_*_planned_minor, die vier bucket_*_minor, die drei bucket_*_percent
   * sowie income_basis_minor und fixed_costs_income_basis.
   */
  budget: BudgetSummary;
  /**
   * Optionaler Sprung in die Kategorien eines Topfes (z. B. Kategoriefilter
   * setzen). Ohne Handler bleiben die Zeilen reiner Text - die Karte ist dann
   * vollstaendig, nur nicht klickbar.
   */
  onShowCategories?: (bucket: BudgetBucketKey) => void;
}

type BucketRow = {
  key: BudgetBucketKey;
  label: string;
  hint: string;
  /** Zielanteil an den Einnahmen; null fuer "Ohne Bucket" - dafuer gibt es kein 50/30/20-Ziel. */
  targetShare: number | null;
  planned: number;
  actual: number;
  /**
   * Richtung, in die eine Abweichung schlecht ist. Bei Sparen ist zu WENIG das
   * Problem, bei Grundbeduerfnissen und Wuenschen zu viel.
   */
  moreIsBetter: boolean;
};

type DeviationTone = "positive" | "warning" | "neutral";

// Die 50/30/20-Regel bezieht sich auf die Einnahmen, nicht auf die Ausgaben -
// genau darin unterscheidet sich das ZIEL von den bucket_*_percent, die immer
// 100 % der gebuchten Ausgaben sind.
const TARGET_SHARES: Record<Exclude<BudgetBucketKey, "unassigned">, number> = {
  needs: 0.5,
  wants: 0.3,
  savings: 0.2,
};

/**
 * Haelt fehlende Zahlen aus der Skala heraus: eine API-Antwort ohne die neuen
 * bucket_*-Felder wuerde sonst als NaN durch jede Balkenbreite laufen und die
 * ganze Karte unlesbar machen.
 */
function num(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

function formatPercent(value: number): string {
  return `${num(value).toFixed(1).replace(".", ",")} %`;
}

/** Vorzeichenbehaftete Differenz; "±" macht sichtbar, dass 0 kein fehlender Wert ist. */
function formatSignedMinor(diff: number): string {
  if (diff === 0) {
    return `±${formatMinorUnitsAsEuro(0)}`;
  }
  return `${diff > 0 ? "+" : "−"}${formatMinorUnitsAsEuro(Math.abs(diff))}`;
}

function deviationTone(diff: number, moreIsBetter: boolean): DeviationTone {
  if (diff === 0) {
    return "neutral";
  }
  return diff > 0 === moreIsBetter ? "positive" : "warning";
}

function deviationWord(diff: number, reference: "Ziel" | "Plan"): string {
  if (diff === 0) {
    return `genau auf dem ${reference}`;
  }
  return diff > 0 ? `über dem ${reference}` : `unter dem ${reference}`;
}

/** Balkenbreite in Prozent der gemeinsamen Skala; negative Ist-Werte (Erstattungsüberschuss) ergeben keinen Balken. */
function scalePercent(value: number, scaleMax: number): number {
  if (scaleMax <= 0) {
    return 0;
  }
  return Math.min(100, (Math.max(0, value) / scaleMax) * 100);
}

export function BucketSplit({ budget, onShowCategories }: BucketSplitProps) {
  const headingId = useId();

  // Bezugsgroesse der Quoten. Kann 0 oder negativ sein (Monat ohne Einnahmen,
  // Erstattungsueberschuss) - dann gibt es kein Ziel in Euro, nur Plan und Ist.
  const incomeBasis = num(budget.income_basis_minor);
  const hasTarget = incomeBasis > 0;
  const basisFromContracts = budget.fixed_costs_income_basis === "contract";

  const unassignedPlanned = num(budget.bucket_unassigned_planned_minor);
  const unassignedActual = num(budget.bucket_unassigned_spent_minor);

  const rows: BucketRow[] = [
    {
      key: "needs",
      label: "Grundbedürfnisse",
      hint: "Wohnen, Energie, Lebensmittel, Versicherungen",
      targetShare: TARGET_SHARES.needs,
      planned: num(budget.bucket_needs_planned_minor),
      actual: num(budget.bucket_needs_minor),
      moreIsBetter: false,
    },
    {
      key: "wants",
      label: "Wünsche",
      hint: "Freizeit, Hobbys, alles Verzichtbare",
      targetShare: TARGET_SHARES.wants,
      planned: num(budget.bucket_wants_planned_minor),
      actual: num(budget.bucket_wants_minor),
      moreIsBetter: false,
    },
    {
      key: "savings",
      label: "Sparen & Tilgen",
      hint: "Rücklagen, Sparpläne, Sondertilgungen",
      targetShare: TARGET_SHARES.savings,
      planned: num(budget.bucket_savings_planned_minor),
      actual: num(budget.bucket_savings_minor),
      moreIsBetter: true,
    },
  ];

  // Die vierte Zeile erscheint nur, wenn es dort wirklich Geld gibt: eine Zeile
  // mit vier Nullen erklaert nichts und nimmt der Karte die Ruhe.
  if (unassignedPlanned !== 0 || unassignedActual !== 0) {
    rows.push({
      key: "unassigned",
      label: "Ohne Bucket",
      hint: "Kategorien ohne 50/30/20-Zuordnung",
      targetShare: null,
      planned: unassignedPlanned,
      actual: unassignedActual,
      moreIsBetter: false,
    });
  }

  // Eine Skala fuer alle Zeilen - sonst sind die Balken untereinander nicht
  // vergleichbar und die Karte macht denselben Fehler wie die alte Ansicht.
  const scaleMax = Math.max(
    0,
    ...rows.flatMap((row) => [
      row.planned,
      row.actual,
      hasTarget && row.targetShare !== null ? incomeBasis * row.targetShare : 0,
    ]),
  );

  const distribution = [
    { key: "needs" as const, label: "Grundbedürfnisse", percent: num(budget.bucket_needs_percent) },
    { key: "wants" as const, label: "Wünsche", percent: num(budget.bucket_wants_percent) },
    { key: "savings" as const, label: "Sparen", percent: num(budget.bucket_savings_percent) },
  ];
  const distributionTotal = distribution.reduce((sum, part) => sum + Math.max(0, part.percent), 0);
  const savingsPlanned = num(budget.bucket_savings_planned_minor);

  return (
    <section className="card bucket-split" aria-labelledby={headingId}>
      <header className="bucket-split__header">
        <div className="bucket-split__title">
          <h2 className="card-heading" id={headingId}>
            50/30/20 – Ziel, Plan und Ist
          </h2>
          {hasTarget ? (
            <p className="bucket-split__basis">
              Ziel bezogen auf <b>{formatMinorUnitsAsEuro(incomeBasis)}</b> Einnahmen –{" "}
              {basisFromContracts
                ? "normiert aus deinen Einnahmeverträgen, weil in diesem Monat noch nichts gebucht ist"
                : "die in diesem Monat gebuchten Einnahmen"}
              .
            </p>
          ) : (
            <p className="bucket-split__basis bucket-split__basis--empty">
              Ohne Einnahmen lässt sich kein 50/30/20-Ziel berechnen. Plan und Ist stehen trotzdem
              nebeneinander.
            </p>
          )}
        </div>
        <span
          className={`status-badge ${
            basisFromContracts ? "status-badge--highlight" : "status-badge--neutral"
          }`}
        >
          {basisFromContracts ? "Basis: Verträge" : "Basis: gebuchte Einnahmen"}
        </span>
      </header>

      <ul className="bucket-split__legend bucket-split__legend--marks" aria-hidden="true">
        <li>
          <span className="bucket-split__mark bucket-split__mark--plan" /> Plan
        </li>
        <li>
          <span className="bucket-split__mark bucket-split__mark--actual" /> Ist
        </li>
        <li>
          <span className="bucket-split__mark bucket-split__mark--target" /> Ziel
        </li>
      </ul>

      <div
        className="bucket-split__scroll"
        role="region"
        aria-label="50/30/20 im Vergleich"
        tabIndex={0}
      >
        <table className="bucket-split__table">
          <caption className="bucket-split__sr">
            Je Topf das Ziel aus der 50/30/20-Regel, der geplante und der gebuchte Betrag sowie
            beide Abweichungen. Die Balkenspalte nutzt für alle Zeilen dieselbe Skala.
          </caption>
          <thead>
            <tr>
              <th scope="col" className="bucket-split__th">
                Topf
              </th>
              {hasTarget && (
                <th scope="col" className="bucket-split__th bucket-split__th--num">
                  Ziel
                </th>
              )}
              <th scope="col" className="bucket-split__th bucket-split__th--num">
                Plan
              </th>
              {hasTarget && (
                <th scope="col" className="bucket-split__th bucket-split__th--num">
                  Plan zu Ziel
                </th>
              )}
              <th scope="col" className="bucket-split__th bucket-split__th--num">
                Ist
              </th>
              <th scope="col" className="bucket-split__th bucket-split__th--num">
                Ist zu Plan
              </th>
              <th scope="col" className="bucket-split__th bucket-split__th--scale">
                Gemeinsame Skala
                {scaleMax > 0 && (
                  <span className="bucket-split__scale-max">
                    0 bis {formatMinorUnitsAsEuro(scaleMax)}
                  </span>
                )}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const target =
                hasTarget && row.targetShare !== null
                  ? Math.round(incomeBasis * row.targetShare)
                  : null;
              const planVsTarget = target === null ? null : row.planned - target;
              const actualVsPlan = row.actual - row.planned;

              return (
                <tr
                  key={row.key}
                  className={`bucket-split__row${
                    row.key === "unassigned" ? " bucket-split__row--quiet" : ""
                  }`}
                  data-bucket={row.key}
                >
                  <th scope="row" className="bucket-split__name">
                    {onShowCategories ? (
                      <button
                        type="button"
                        className="bucket-split__name-btn"
                        onClick={() => onShowCategories(row.key)}
                        aria-label={`Kategorien im Topf ${row.label} anzeigen`}
                      >
                        <span className="bucket-split__swatch" aria-hidden="true" />
                        <span className="bucket-split__name-text">
                          <strong>{row.label}</strong>
                          <small>{row.hint}</small>
                        </span>
                      </button>
                    ) : (
                      <span className="bucket-split__name-static">
                        <span className="bucket-split__swatch" aria-hidden="true" />
                        <span className="bucket-split__name-text">
                          <strong>{row.label}</strong>
                          <small>{row.hint}</small>
                        </span>
                      </span>
                    )}
                  </th>

                  {hasTarget && (
                    <td className="bucket-split__num">
                      {target === null ? (
                        <span className="bucket-split__empty">—</span>
                      ) : (
                        <>
                          {formatMinorUnitsAsEuro(target)}
                          <small className="bucket-split__share">
                            {Math.round((row.targetShare ?? 0) * 100)} %
                          </small>
                        </>
                      )}
                    </td>
                  )}

                  <td className="bucket-split__num">{formatMinorUnitsAsEuro(row.planned)}</td>

                  {hasTarget && (
                    <td
                      className={`bucket-split__num bucket-split__delta bucket-split__delta--${
                        planVsTarget === null
                          ? "neutral"
                          : deviationTone(planVsTarget, row.moreIsBetter)
                      }`}
                    >
                      {planVsTarget === null ? (
                        <span className="bucket-split__empty">—</span>
                      ) : (
                        <>
                          {formatSignedMinor(planVsTarget)}
                          <span className="bucket-split__sr">
                            {" "}
                            {deviationWord(planVsTarget, "Ziel")}
                          </span>
                        </>
                      )}
                    </td>
                  )}

                  <td className="bucket-split__num bucket-split__num--strong">
                    {formatMinorUnitsAsEuro(row.actual)}
                  </td>

                  <td
                    className={`bucket-split__num bucket-split__delta bucket-split__delta--${deviationTone(
                      actualVsPlan,
                      row.moreIsBetter,
                    )}`}
                  >
                    {formatSignedMinor(actualVsPlan)}
                    <span className="bucket-split__sr"> {deviationWord(actualVsPlan, "Plan")}</span>
                  </td>

                  <td className="bucket-split__scale">
                    <div className="bucket-split__track" aria-hidden="true">
                      {target !== null && (
                        <span
                          className="bucket-split__marker"
                          style={{ left: `${scalePercent(target, scaleMax)}%` }}
                        >
                          <span className="bucket-split__marker-label">
                            {Math.round((row.targetShare ?? 0) * 100)} %
                          </span>
                        </span>
                      )}
                      <span className="bucket-split__rail">
                        <span
                          className="bucket-split__fill bucket-split__fill--plan"
                          style={{ width: `${scalePercent(row.planned, scaleMax)}%` }}
                          title={`Plan ${formatMinorUnitsAsEuro(row.planned)}`}
                        />
                      </span>
                      <span className="bucket-split__rail">
                        <span
                          className="bucket-split__fill bucket-split__fill--actual"
                          style={{ width: `${scalePercent(row.actual, scaleMax)}%` }}
                          title={`Ist ${formatMinorUnitsAsEuro(row.actual)}`}
                        />
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="bucket-split__footnote">
        <b>Plan</b> ist die Summe der Planbeträge aller Ausgabenkategorien, die du diesem Topf
        zugeordnet hast – kein Restbetrag und keine automatische Rechnung.{" "}
        {savingsPlanned > 0
          ? `Bei Sparen stehen ${formatMinorUnitsAsEuro(savingsPlanned)}, weil du genau diesen Betrag auf Sparkategorien verteilt hast. `
          : ""}
        <b>Ist</b> sind die Ausgaben, die in diesem Monat auf genau diesen Kategorien gebucht
        wurden.
      </p>

      <section className="bucket-split__distribution">
        <div className="bucket-split__distribution-head">
          <h3 className="bucket-split__subheading">Verteilung der gebuchten Ausgaben</h3>
          <span className="status-badge status-badge--neutral">Andere Frage</span>
        </div>
        <div
          className="bucket-split__strip"
          data-testid="budget-503020-distribution"
          role="img"
          aria-label={
            distributionTotal > 0
              ? distribution
                  .map((part) => `${part.label} ${formatPercent(part.percent)}`)
                  .join(", ")
              : "Noch keine zugeordneten Ausgaben gebucht"
          }
        >
          {distributionTotal > 0 &&
            distribution.map((part) => (
              <span
                key={part.key}
                className="bucket-split__strip-part"
                data-bucket={part.key}
                style={{ width: `${(Math.max(0, part.percent) / distributionTotal) * 100}%` }}
              />
            ))}
        </div>
        {distributionTotal > 0 ? (
          <ul className="bucket-split__legend">
            {distribution.map((part) => (
              <li key={part.key} data-bucket={part.key}>
                <span className="bucket-split__swatch" aria-hidden="true" />
                {part.label}
                <b>{formatPercent(part.percent)}</b>
              </li>
            ))}
          </ul>
        ) : (
          <p className="bucket-split__note">Noch keine zugeordneten Ausgaben gebucht.</p>
        )}
        <p className="bucket-split__note">
          Diese Anteile summieren sich immer auf 100 % der zugeordneten Ausgaben – sie sagen nicht,
          ob du das 50/30/20-Ziel erreichst. Dafür ist die Tabelle darüber zuständig.
        </p>
      </section>
    </section>
  );
}
