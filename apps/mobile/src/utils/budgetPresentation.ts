import type { BudgetSummary, CategoryBudgetStatus, CategoryGroupBudgetStatus } from "../api/types";
import { BUDGET_REMAINING_LABEL, formatReimbursementSurplus } from "./financeLabels";
import { formatMinorUnits } from "./money";

// Reine, unabhaengig testbare Aufbereitung der Budget-Zahlen fuer die
// kompakte Zusammenfassung, die Gruppenzeilen und die Kategoriezeilen
// (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 6 "Seite Budget"). Der Screen
// selbst bleibt dadurch auf Layout beschraenkt - die fachliche Umrechnung
// (Plan + Uebertrag, Fortschritt, Erstattungsueberschuss) steht hier einmal
// statt mehrfach im JSX.

export { BUDGET_REMAINING_LABEL };

export interface PlanBasisDisplay {
  /** planned_minor + rollover_minor - die tatsaechliche Zielgroesse fuer Fortschritt/Vergleich. */
  totalMinor: number;
  totalLabel: string;
  rolloverMinor: number;
  /** Uebertrag wird nur als eigener Wert gezeigt, wenn er ungleich null ist. */
  hasRollover: boolean;
  rolloverLabel: string | null;
  /** "Plan" ohne Uebertrag, "Plan + Übertrag" mit Uebertrag. */
  basisLabel: string;
}

export function describePlanBasis(plannedMinor: number, rolloverMinor: number): PlanBasisDisplay {
  const totalMinor = plannedMinor + rolloverMinor;
  const hasRollover = rolloverMinor !== 0;
  return {
    totalMinor,
    totalLabel: formatMinorUnits(totalMinor),
    rolloverMinor,
    hasRollover,
    rolloverLabel: hasRollover ? formatMinorUnits(rolloverMinor) : null,
    basisLabel: hasRollover ? "Plan + Übertrag" : "Plan",
  };
}

/**
 * Fortschritt 0..1(+) fuer ProgressBar.tsx (die Komponente klemmt selbst auf
 * 0..1 und zeigt bei > 1 zusaetzlich eine Ueberziehungsspitze). Bei einer
 * nicht positiven Zielgroesse oder einem negativen Zaehler (Erstattungs-
 * ueberschuss) ist der Fortschritt 0, nie negativ oder NaN.
 */
export function calcProgress(numeratorMinor: number, denominatorMinor: number): number {
  if (denominatorMinor <= 0 || numeratorMinor <= 0) return 0;
  return numeratorMinor / denominatorMinor;
}

export interface BudgetOverviewDisplay {
  /** true, wenn der Nettoverbrauch negativ ist (mehr erstattet als ausgegeben). */
  isSurplus: boolean;
  /** "X EUR netto ausgegeben" bzw. "X EUR Erstattungsüberschuss". */
  headline: string;
  planBasis: PlanBasisDisplay;
  progress: number;
  remainingMinor: number;
  remainingLabel: string;
  remainingIsNegative: boolean;
}

/**
 * Kompakte Budgetzusammenfassung (Redesign-Plan Abschnitt 6): ersetzt die
 * bisherige Vier-Spalten-"Übersicht"-Karte. `total_spent_minor` enthaelt
 * bereits die unkategorisierten Ausgaben (budget_service.py-Invariante
 * total_spent = sum(group.total_spent) + uncategorized_spent).
 */
export function describeBudgetOverview(
  budget: Pick<
    BudgetSummary,
    "total_spent_minor" | "total_planned_minor" | "total_rollover_minor" | "total_remaining_minor"
  >,
): BudgetOverviewDisplay {
  const isSurplus = budget.total_spent_minor < 0;
  const planBasis = describePlanBasis(budget.total_planned_minor, budget.total_rollover_minor);
  const headline = isSurplus
    ? `${formatMinorUnits(Math.abs(budget.total_spent_minor))} Erstattungsüberschuss`
    : `${formatMinorUnits(budget.total_spent_minor)} netto ausgegeben`;

  return {
    isSurplus,
    headline,
    planBasis,
    progress: isSurplus ? 0 : calcProgress(budget.total_spent_minor, planBasis.totalMinor),
    remainingMinor: budget.total_remaining_minor,
    remainingLabel: formatMinorUnits(budget.total_remaining_minor),
    remainingIsNegative: budget.total_remaining_minor < 0,
  };
}

export interface GroupDisplay {
  planBasis: PlanBasisDisplay;
  /** true, wenn die Gruppe insgesamt mehr erstattet als ausgegeben hat. */
  isSurplus: boolean;
  /** Betrag ("250,00 EUR") bzw. bei Erstattungsueberschuss der volle Satz aus financeLabels.ts. */
  spentLabel: string;
  progress: number;
}

/**
 * Gruppenzeile fuer Ausgabengruppen. `total_planned_minor`/`total_rollover_minor`/
 * `total_spent_minor` fassen laut Backend-Modell ausschliesslich Ausgaben-
 * kategorien zusammen (Einnahmekategorien tragen nur zu
 * `total_income_actual_minor` bei) - fuer Einnahmegruppen gibt es deshalb
 * bewusst keine gleichwertige Funktion.
 *
 * `total_spent_minor` ist dieselbe Groesse wie `spent_minor` je Kategorie,
 * nur aufsummiert - ein negativer Wert ist deshalb genauso ein
 * Erstattungsueberschuss und kein negativer Einkauf (Redesign-Plan
 * Abschnitt 2), auch auf Gruppenebene.
 */
export function describeExpenseGroup(
  group: Pick<
    CategoryGroupBudgetStatus,
    "total_spent_minor" | "total_planned_minor" | "total_rollover_minor"
  >,
): GroupDisplay {
  const planBasis = describePlanBasis(group.total_planned_minor, group.total_rollover_minor);
  const isSurplus = group.total_spent_minor < 0;
  const spentLabel = isSurplus
    ? formatReimbursementSurplus(formatMinorUnits(Math.abs(group.total_spent_minor)))
    : formatMinorUnits(group.total_spent_minor);
  return {
    planBasis,
    isSurplus,
    spentLabel,
    progress: isSurplus ? 0 : calcProgress(group.total_spent_minor, planBasis.totalMinor),
  };
}

export interface ExpenseCategoryDisplay {
  planBasis: PlanBasisDisplay;
  isSurplus: boolean;
  /** "X von Y ausgegeben" bzw. der Erstattungsueberschuss-Satz aus financeLabels.ts. */
  headline: string;
  progress: number;
  remainingMinor: number;
  remainingLabel: string;
  remainingIsNegative: boolean;
}

export function describeExpenseCategory(
  category: Pick<
    CategoryBudgetStatus,
    "spent_minor" | "planned_minor" | "rollover_minor" | "remaining_minor"
  >,
): ExpenseCategoryDisplay {
  const planBasis = describePlanBasis(category.planned_minor, category.rollover_minor);
  const isSurplus = category.spent_minor < 0;
  const headline = isSurplus
    ? formatReimbursementSurplus(formatMinorUnits(Math.abs(category.spent_minor)))
    : `${formatMinorUnits(category.spent_minor)} von ${planBasis.totalLabel} ausgegeben`;

  return {
    planBasis,
    isSurplus,
    headline,
    progress: isSurplus ? 0 : calcProgress(category.spent_minor, planBasis.totalMinor),
    remainingMinor: category.remaining_minor,
    remainingLabel: formatMinorUnits(category.remaining_minor),
    remainingIsNegative: category.remaining_minor < 0,
  };
}

export interface IncomeCategoryDisplay {
  /** "Erwartet"-Zielgroesse (planned_minor + rollover_minor). */
  planBasis: PlanBasisDisplay;
  /** "X von Y eingegangen". */
  headline: string;
  /** remaining_minor = Erwartet - Eingegangen, formatiert fuer "Noch erwartet". */
  stillExpectedLabel: string;
}

export function describeIncomeCategory(
  category: Pick<
    CategoryBudgetStatus,
    "income_actual_minor" | "planned_minor" | "rollover_minor" | "remaining_minor"
  >,
): IncomeCategoryDisplay {
  const planBasis = describePlanBasis(category.planned_minor, category.rollover_minor);
  return {
    planBasis,
    headline: `${formatMinorUnits(category.income_actual_minor)} von ${planBasis.totalLabel} eingegangen`,
    stillExpectedLabel: formatMinorUnits(category.remaining_minor),
  };
}
