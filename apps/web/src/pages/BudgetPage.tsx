import { Fragment, useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  closeBudgetMonth,
  getAnnualMatrix,
  getBudgetSummary,
  updateBudgetAllocations,
} from "../lib/api";
import { ProgressBar } from "../components/ProgressBar";
import { TransactionDrilldown } from "../components/analytics/TransactionDrilldown";
import { ReservesLedger } from "../components/reserves/ReservesLedger";
import { AnnualMatrixTable, hasAnnualActivity } from "../components/budget/AnnualMatrixTable";
import { BucketSplit } from "../components/budget/BucketSplit";
import { BudgetFlow } from "../components/budget/BudgetFlow";
import { useFormatDate } from "../hooks/useProfile";
import { monthRange, rowsQuery } from "../lib/analytics";
import { formatMonthYear } from "../lib/dates";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import type { AnnualMatrixResponse, BudgetSummary, CategoryAllocation } from "../lib/types";
import "./budget.css";

const GOAL_RESERVES_STORAGE_KEY = "finanzplaner:budget:include-goal-reserves";

function readGoalReservesPreference(): boolean {
  try {
    return window.localStorage.getItem(GOAL_RESERVES_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

/**
 * Zwei Betragszellen der Kategorietabelle sind als Schaltflaeche angekuendigt
 * (role/tabIndex) und muessen deshalb auch per Tastatur ausloesen - sonst ist
 * die Zelle fokussierbar, aber tot. Ein echtes <button> scheidet hier aus: die
 * Betraege stehen in einer Monospalte an der Spaltenkante, ein Buttonelement
 * bricht die Ziffernflucht. In den neuen Budgetkomponenten sind es dagegen
 * echte Buttons, weil dort kein Zahlenraster daran haengt.
 */
function activateOnKey(handler: () => void) {
  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handler();
    }
  };
}

/**
 * "Bewegung" heisst: die Zeile traegt irgendeine Zahl - Planbetrag, Uebertrag,
 * gebuchte Ausgabe, gebuchte Einnahme oder ein im Monat faelliger Vertrag.
 * Alles andere ist eine Nullzeile, die nur Platz kostet. Bewusst inklusive
 * Planbetrag: eine Kategorie, fuer die man 200 EUR eingeplant und noch nichts
 * ausgegeben hat, ist der interessante Fall und darf nicht verschwinden.
 * Bewusst auch inklusive contract_forecast_minor: sonst waere die im Monat
 * faellige Jahresversicherung (siehe budget_service.get_budget_summary)
 * ausgerechnet in dem Monat unsichtbar, in dem der Forecast am meisten bringt.
 */
function hasMonthlyActivity(category: CategoryAllocation): boolean {
  return (
    category.planned_minor !== 0 ||
    category.rollover_minor !== 0 ||
    category.spent_minor !== 0 ||
    category.income_actual_minor !== 0 ||
    category.contract_forecast_minor !== 0
  );
}

type CategoryFilter = "active" | "everActive" | "all";

/**
 * Dritte Filterstufe zusaetzlich zu hasMonthlyActivity: "everActive" rettet
 * Kategorien mit einer nur saisonalen Faelligkeit (z. B. eine jaehrliche
 * Versicherung) in den Monaten, in denen weder diesen Monat etwas passiert
 * noch ein Vertrag faellig ist - has_activity_ever kommt vorberechnet vom
 * Backend (Lebenszeit, nicht nur dieses Jahr).
 */
function matchesCategoryFilter(category: CategoryAllocation, filter: CategoryFilter): boolean {
  if (filter === "all") return true;
  if (filter === "everActive") return hasMonthlyActivity(category) || category.has_activity_ever;
  return hasMonthlyActivity(category);
}

function prevMonthOf(year: number, month: number): { year: number; month: number } {
  return month === 1 ? { year: year - 1, month: 12 } : { year, month: month - 1 };
}

/**
 * Hover-Erklaerung fuer die Erwartet-Spalte: welcher Vertrag mit welcher
 * Faelligkeit dahintersteckt, statt nur der Summe. Ohne Quellen (Forecast 0)
 * erklaert der Text stattdessen, warum nichts erwartet wird.
 */
function forecastTitle(cat: CategoryAllocation, formatDate: (value: string) => string): string {
  const sources = cat.contract_forecast_sources ?? [];
  if (sources.length === 0) {
    return "Kein Vertrag mit dieser Kategorie ist diesen Monat fällig.";
  }
  return sources
    .map(
      (s) =>
        `${s.contract_name}: ${formatMinorUnitsAsEuro(s.amount_minor)} · fällig ${formatDate(s.due_date)}`,
    )
    .join("\n");
}

/**
 * Plan-, Ist- und Restzelle einer Gruppenzeile.
 *
 * Ausgaben und Einnahmen bleiben zwei getrennte Zahlen und werden NIE addiert.
 * Das Backend fuehrt sie bewusst getrennt: total_planned_minor / _spent_ /
 * _remaining_ enthalten ausschliesslich Ausgabenkategorien, die drei
 * *_income_*-Felder ausschliesslich Einnahmekategorien (services/api/app/
 * schemas/budgets.py). Eine Gruppe mit BEIDEN Arten zeigt deshalb beide Zeilen
 * untereinander statt einer Mischsumme, die weder Budgettopf noch Zufluss
 * waere - genau daran scheiterte die alte Ansicht, die den Einnahmenplan
 * einfach verschwieg.
 *
 * Die Einnahmezeile traegt zusaetzlich den positiven Ton und das Wort
 * "Einnahmen", damit ein geplanter Zufluss nicht als Ausgabenbudget gelesen
 * wird.
 */
function GroupAmounts({
  expenseMinor,
  incomeMinor,
  showExpense,
  showIncome,
  /** "signed" faerbt nach Vorzeichen (Restspalte), "plain" laesst die Zahl ruhig. */
  expenseTone = "plain",
}: {
  expenseMinor: number;
  incomeMinor: number;
  showExpense: boolean;
  showIncome: boolean;
  expenseTone?: "plain" | "signed";
}) {
  return (
    <div className="budget-group-amounts">
      {showExpense && (
        <span
          className={`budget-group-amounts__value ${
            expenseTone === "signed"
              ? expenseMinor < 0
                ? "transaction-amount--negative"
                : "transaction-amount--positive"
              : ""
          }`}
        >
          {formatMinorUnitsAsEuro(expenseMinor)}
        </span>
      )}
      {showIncome && (
        <span className="budget-group-amounts__value transaction-amount--positive">
          <span className="budget-group-amounts__tag">Einnahmen</span>
          {formatMinorUnitsAsEuro(incomeMinor)}
        </span>
      )}
    </div>
  );
}

export function BudgetPage() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();
  const [searchParams, setSearchParams] = useSearchParams();

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const currentDate = new Date();
  const urlYear = Number(searchParams.get("year"));
  const urlMonth = Number(searchParams.get("month"));
  const [year, setYear] = useState<number>(
    urlYear >= 2000 && urlYear <= 2200 ? urlYear : currentDate.getFullYear(),
  );
  const [month, setMonth] = useState<number>(
    urlMonth >= 1 && urlMonth <= 12 ? urlMonth : currentDate.getMonth() + 1,
  );
  // Ein Ist-Wert ohne Weg zu den Buchungen dahinter laesst sich nicht
  // pruefen - "warum sind das 412 EUR?" ist die haeufigste Frage an eine
  // Budgetzeile.
  const [drilldown, setDrilldown] = useState<{
    title: string;
    categoryId: string | null;
    year: number;
    month: number;
  } | null>(null);
  const initialTab = searchParams.get("tab");
  const [activeTab, setActiveTab] = useState<"monthly" | "reserves" | "matrix">(
    initialTab === "reserves" || initialTab === "matrix" ? initialTab : "monthly",
  );
  const [editingPlanned, setEditingPlanned] = useState<Record<string, string>>({});
  // Jahresmatrix-Tab: Standardmaessig nur Kategorien mit Bewegung IRGENDWO im
  // Jahr. Zum Anlegen eines neuen Planbetrags braucht man die leeren Zeilen
  // aber - dafuer der Schalter, nicht fuer eine dauerhaft lange Liste.
  const [showEmptyCategories, setShowEmptyCategories] = useState(false);
  // Monatsbudget-Tab: dieselbe Idee, aber dreistufig statt binaer - "diesen
  // Monat" ist zu eng fuer saisonale Kategorien (siehe matchesCategoryFilter).
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("active");
  const [includeGoalReserves, setIncludeGoalReserves] = useState(readGoalReservesPreference);

  useEffect(() => {
    try {
      window.localStorage.setItem(GOAL_RESERVES_STORAGE_KEY, String(includeGoalReserves));
    } catch {
      // Lokale Anzeigepräferenz; Budgetdaten bleiben davon unberührt.
    }
  }, [includeGoalReserves]);

  // Bewusst die Aktualisierungsform: ein vollstaendiges Objekt wuerde jeden
  // fremden Abfrageparameter auf /budget stillschweigend wegwerfen (z. B. eine
  // Verlinkung aus einem Bericht, die zusaetzlich eine Kategorie mitbringt).
  useEffect(() => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.set("tab", activeTab);
        next.set("year", String(year));
        next.set("month", String(month));
        return next;
      },
      { replace: true },
    );
  }, [activeTab, month, setSearchParams, year]);

  const {
    data: budget,
    isLoading: isBudgetLoading,
    error: budgetError,
  } = useQuery<BudgetSummary>({
    queryKey: ["budget", householdId, year, month],
    queryFn: () => getBudgetSummary(token!, householdId, year, month),
    enabled: Boolean(token && householdId),
  });

  const { data: matrix, isLoading: isMatrixLoading } = useQuery<AnnualMatrixResponse>({
    queryKey: ["annual-matrix", householdId, year],
    queryFn: () => getAnnualMatrix(token!, householdId, year),
    enabled: Boolean(token && householdId && activeTab === "matrix"),
  });

  // Gefiltert wird ausschliesslich die ANZEIGE. Die Gruppen- und Gesamtsummen
  // kommen unveraendert vom Backend und enthalten weiterhin alle Kategorien -
  // ausgeblendete Nullzeilen tragen ohnehin 0 bei, die Summen bleiben also
  // korrekt und nachrechenbar.
  const visibleGroups = useMemo(() => {
    if (!budget) return [];
    const decorated = budget.groups.map((group) => ({
      ...group,
      // hasIncome/hasExpense richten sich nach den ECHTEN Kategorien der
      // Gruppe, nicht nach den gefilterten: sonst verschwaende ein Filter die
      // Einnahmezeile, waehrend die Gruppensumme darueber sie weiter enthaelt.
      hasIncome: group.categories.some((c) => c.default_type === "income"),
      hasExpense: group.categories.some((c) => c.default_type !== "income"),
      categories: group.categories.filter(
        (c) => categoryFilter === "all" || matchesCategoryFilter(c, categoryFilter),
      ),
    }));
    return categoryFilter === "all"
      ? decorated
      : decorated.filter((group) => group.categories.length > 0);
  }, [budget, categoryFilter]);

  const hiddenCategoryCount = useMemo(() => {
    if (!budget) return 0;
    const total = budget.groups.reduce((sum, group) => sum + group.categories.length, 0);
    const shown = budget.groups.reduce(
      (sum, group) =>
        sum + group.categories.filter((c) => matchesCategoryFilter(c, categoryFilter)).length,
      0,
    );
    return total - shown;
  }, [budget, categoryFilter]);

  const hiddenMatrixRowCount = useMemo(() => {
    if (!matrix) return 0;
    return matrix.rows.length - matrix.rows.filter(hasAnnualActivity).length;
  }, [matrix]);

  const hiddenCount = activeTab === "matrix" ? hiddenMatrixRowCount : hiddenCategoryCount;

  const updateAllocationMutation = useMutation({
    mutationFn: (
      allocations: Array<{
        category_id: string;
        planned_minor: number;
        rollover_rule?: "none" | "accumulate" | "reset";
      }>,
    ) => updateBudgetAllocations(token!, householdId, year, month, allocations),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["budget", householdId, year, month] });
      void queryClient.invalidateQueries({ queryKey: ["overview-report", householdId] });
    },
  });

  const closeMonthMutation = useMutation({
    mutationFn: () => closeBudgetMonth(token!, householdId, year, month),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["budget", householdId, year, month] });
      void queryClient.invalidateQueries({ queryKey: ["overview-report", householdId] });
    },
  });

  // Uebernimmt Planbetraege des Vormonats - bewusst NUR fuer Kategorien, die
  // diesen Monat noch bei 0 stehen. Ein bereits von Hand eingetragener
  // Planbetrag wird nie ueberschrieben, sonst loescht ein Klick versehentlich
  // eine gerade erst vorgenommene Korrektur.
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null);
  const copyFromPrevMonthMutation = useMutation({
    mutationFn: async () => {
      const prev = prevMonthOf(year, month);
      const prevBudget = await getBudgetSummary(token!, householdId, prev.year, prev.month);
      const currentPlanned = new Map(
        (budget?.groups ?? [])
          .flatMap((g) => g.categories)
          .map((c) => [c.category_id, c.planned_minor]),
      );
      const allocations = prevBudget.groups
        .flatMap((g) => g.categories)
        .filter((c) => c.planned_minor !== 0 && (currentPlanned.get(c.category_id) ?? 0) === 0)
        .map((c) => ({
          category_id: c.category_id,
          planned_minor: c.planned_minor,
          rollover_rule: c.rollover_rule,
        }));
      if (allocations.length > 0) {
        await updateBudgetAllocations(token!, householdId, year, month, allocations);
      }
      return allocations.length;
    },
    onSuccess: (count) => {
      void queryClient.invalidateQueries({ queryKey: ["budget", householdId, year, month] });
      void queryClient.invalidateQueries({ queryKey: ["overview-report", householdId] });
      setCopyFeedback(
        count > 0
          ? `${count} Planbetrag${count === 1 ? "" : "e"} aus dem Vormonat übernommen.`
          : "Nichts zu übernehmen - der Vormonat war leer oder alle Kategorien haben schon einen Planbetrag.",
      );
    },
  });

  const handlePrevMonth = () => {
    setCopyFeedback(null);
    if (month === 1) {
      setMonth(12);
      setYear((y) => y - 1);
    } else {
      setMonth((m) => m - 1);
    }
  };

  const handleNextMonth = () => {
    setCopyFeedback(null);
    if (month === 12) {
      setMonth(1);
      setYear((y) => y + 1);
    } else {
      setMonth((m) => m + 1);
    }
  };

  const handlePlannedBlur = (
    categoryId: string,
    originalPlannedMinor: number,
    rolloverRule: "none" | "accumulate" | "reset",
  ) => {
    const rawVal = editingPlanned[categoryId];
    if (rawVal === undefined) return;

    const newMinor = parseAmountToMinorUnits(rawVal);
    if (newMinor !== null && newMinor !== originalPlannedMinor) {
      updateAllocationMutation.mutate([
        { category_id: categoryId, planned_minor: newMinor, rollover_rule: rolloverRule },
      ]);
    }
  };

  const handleRolloverRuleChange = (
    categoryId: string,
    plannedMinor: number,
    rolloverRule: "none" | "accumulate" | "reset",
  ) => {
    updateAllocationMutation.mutate([
      { category_id: categoryId, planned_minor: plannedMinor, rollover_rule: rolloverRule },
    ]);
  };

  const getProgressBarTone = (percentage: number) => {
    if (percentage >= 100) return "warning" as const;
    if (percentage >= 80) return "highlight" as const;
    return "positive" as const;
  };

  return (
    <section className="page" data-testid="page-budget">
      {/* Header mit Monatsauswahl */}
      <div className="page-header">
        <div>
          <h1 className="page-heading">Budget</h1>
          <p className="placeholder-note">
            Von den Einnahmen über Verträge und Rücklagen zu dem, was wirklich frei bleibt – danach
            die 50/30/20-Verteilung und die Kategorien.
          </p>
        </div>

        <div
          className="budget-header-actions"
          style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}
        >
          <div
            className="budget-month-navigation"
            style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}
          >
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
              {formatMonthYear(new Date(year, month - 1, 1))}
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

          {activeTab === "monthly" && budget && (
            <>
              <button
                type="button"
                className="btn-secondary"
                disabled={budget.status === "closed" || copyFromPrevMonthMutation.isPending}
                title="Übernimmt Planbeträge des Vormonats für Kategorien, die diesen Monat noch leer sind"
                onClick={() => {
                  setCopyFeedback(null);
                  copyFromPrevMonthMutation.mutate();
                }}
              >
                Werte aus Vormonat übernehmen
              </button>
              <button
                type="button"
                className={budget.status === "closed" ? "btn-secondary" : "btn-primary"}
                disabled={budget.status === "closed" || closeMonthMutation.isPending}
                onClick={() => closeMonthMutation.mutate()}
              >
                {budget.status === "closed" ? "Monat abgeschlossen ✓" : "Monat abschließen"}
              </button>
            </>
          )}
        </div>
      </div>
      {activeTab === "monthly" && copyFeedback && (
        <p className="placeholder-note" style={{ marginTop: "calc(-1 * var(--space-3))" }}>
          {copyFeedback}
        </p>
      )}

      {/* Tabs */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "var(--space-3)",
          marginBottom: "var(--space-4)",
        }}
      >
        <div className="direction-toggle">
          <button
            type="button"
            className={`direction-btn ${activeTab === "monthly" ? "direction-btn--active" : ""}`}
            onClick={() => setActiveTab("monthly")}
          >
            Monatsbudget
          </button>
          <button
            type="button"
            className={`direction-btn ${activeTab === "reserves" ? "direction-btn--active" : ""}`}
            onClick={() => setActiveTab("reserves")}
          >
            Rücklagen
          </button>
          <button
            type="button"
            className={`direction-btn ${activeTab === "matrix" ? "direction-btn--active" : ""}`}
            onClick={() => setActiveTab("matrix")}
          >
            Jahresmatrix ({year})
          </button>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          {activeTab === "matrix" ? (
            <>
              <input
                type="checkbox"
                id="budget-show-empty"
                data-testid="budget-show-empty"
                checked={showEmptyCategories}
                onChange={(event) => setShowEmptyCategories(event.target.checked)}
              />
              <label htmlFor="budget-show-empty" style={{ fontSize: "13px", cursor: "pointer" }}>
                Kategorien ohne Bewegung anzeigen
                {!showEmptyCategories && hiddenCount > 0 && (
                  <span className="txn-note"> ({hiddenCount} ausgeblendet)</span>
                )}
              </label>
            </>
          ) : activeTab === "monthly" ? (
            <>
              <div className="direction-toggle" data-testid="budget-category-filter">
                <button
                  type="button"
                  className={`direction-btn ${categoryFilter === "active" ? "direction-btn--active" : ""}`}
                  title="Nur Kategorien mit Planbetrag, Buchung oder faelligem Vertrag in diesem Monat"
                  onClick={() => setCategoryFilter("active")}
                >
                  Diesen Monat
                </button>
                <button
                  type="button"
                  className={`direction-btn ${categoryFilter === "everActive" ? "direction-btn--active" : ""}`}
                  title="Auch Kategorien, die irgendwann schonmal Bewegung hatten - z. B. eine jaehrliche Versicherung in den ruhigen Monaten"
                  onClick={() => setCategoryFilter("everActive")}
                >
                  Jemals
                </button>
                <button
                  type="button"
                  className={`direction-btn ${categoryFilter === "all" ? "direction-btn--active" : ""}`}
                  title="Alle Kategorien, auch nie genutzte"
                  onClick={() => setCategoryFilter("all")}
                >
                  Alle
                </button>
              </div>
              {categoryFilter !== "all" && hiddenCount > 0 && (
                <span className="txn-note">{hiddenCount} ausgeblendet</span>
              )}
            </>
          ) : null}
        </div>
      </div>

      {/* Transferkategorien blendet bereits das Backend aus - eine interne
          Umbuchung ist keine Ausgabe. Welche Kategorie darueber hinaus im
          Budget erscheint, steuert der Schalter "Im Budget anzeigen" in der
          Kategorienverwaltung. */}

      {activeTab === "monthly" && (
        <>
          {isBudgetLoading && <p className="placeholder-note">Lade Monatsbudget...</p>}

          {budgetError && (
            <div className="card status-error" style={{ marginBottom: "var(--space-4)" }}>
              Fehler beim Laden des Budgets für {formatMonthYear(new Date(year, month - 1, 1))}.
            </div>
          )}

          {!isBudgetLoading && budget && (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
              {/* Roter Faden der Seite, von oben nach unten:
                  1. BudgetFlow  - wo das Geld herkommt und was davon frei bleibt
                  2. BucketSplit - wie sich der geplante und der gebuchte Teil verteilt
                  3. Tabelle     - welche Kategorie das im Einzelnen ist
                  Die Reihenfolge ist die Aussage; sie geht von der Gesamtsicht in
                  die Einzelheit und nie zurueck. */}
              <BudgetFlow
                budget={budget}
                includeReserves={includeGoalReserves}
                onIncludeReservesChange={setIncludeGoalReserves}
                // Der Tab liegt im Zustand dieser Seite und wird aus der URL
                // nur beim ersten Rendern gelesen; ein blosser Link liefe
                // deshalb ins Leere und der Effekt unten schriebe die Adresse
                // sofort wieder auf "monthly" zurueck.
                onOpenReserves={() => setActiveTab("reserves")}
                year={year}
                month={month}
              />

              {/* Bewusst ohne onShowCategories: die Kategorietabelle darunter
                  kann (noch) nicht nach Bucket filtern. Ein Klickziel, das nur
                  scrollt, waere ein Versprechen ohne Deckung. */}
              <BucketSplit budget={budget} />

              {/* Dichte Kategoriegruppen-Tabelle */}
              {visibleGroups.length === 0 ? (
                <p className="placeholder-note">
                  In {formatMonthYear(new Date(year, month - 1, 1))} ist noch nichts geplant und
                  nichts gebucht. Wähle oben "Alle", um alle Kategorien einzublenden und Planbeträge
                  zu vergeben.
                </p>
              ) : (
                <div className="txn-table-wrapper">
                  <table className="txn-table">
                    <thead>
                      <tr>
                        <th className="txn-th">Kategorie</th>
                        <th className="txn-th">Bucket</th>
                        <th
                          className="txn-th txn-th--amount"
                          title="Vormonat (Ist): Ist des Vormonats, zum Vergleich beim Planen"
                        >
                          Vormonat
                        </th>
                        <th className="txn-th txn-th--amount">Planbetrag (€)</th>
                        <th
                          className="txn-th txn-th--amount"
                          title="Erwartet (Vertrag): voller erwarteter Betrag aus einem in diesem Monat faelligen Vertrag"
                        >
                          Erwartet
                        </th>
                        <th className="txn-th txn-th--amount" title="Ist (Ausgabe bzw. Einnahme)">
                          Ist
                        </th>
                        <th className="txn-th txn-th--amount">Rest</th>
                        <th className="txn-th">Fortschritt</th>
                        <th className="txn-th" style={{ textAlign: "right" }}>
                          Rollover
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleGroups.map((group) => (
                        <Fragment key={group.group_id}>
                          <tr
                            className={`txn-tr budget-group-row ${
                              group.hasIncome ? "budget-group-row--income" : ""
                            }`}
                            data-testid={`budget-group-${group.group_id}`}
                          >
                            <td colSpan={2} className="txn-td budget-group-row__name">
                              {group.group_name}
                              {/* Nur bei gemischten Gruppen noetig: eine reine
                                Einnahmegruppe ist bereits durch die gruene
                                Kante und das Wort an den Betraegen erkennbar. */}
                              {group.hasIncome && group.hasExpense && (
                                <span className="status-badge status-badge--positive">
                                  auch Einnahmen
                                </span>
                              )}
                            </td>
                            {/* Vormonat und Forecast sind reine Kategoriezahlen
                              ohne Gruppen-Aggregat vom Backend - Zelle bleibt leer. */}
                            <td className="txn-td txn-td--amount" />
                            <td className="txn-td txn-td--amount">
                              <GroupAmounts
                                expenseMinor={group.total_planned_minor}
                                incomeMinor={group.total_planned_income_minor}
                                showExpense={group.hasExpense || !group.hasIncome}
                                showIncome={group.hasIncome}
                              />
                            </td>
                            <td className="txn-td txn-td--amount" />
                            <td className="txn-td txn-td--amount">
                              <GroupAmounts
                                expenseMinor={group.total_spent_minor}
                                incomeMinor={group.total_income_actual_minor}
                                showExpense={group.hasExpense || !group.hasIncome}
                                showIncome={group.hasIncome}
                              />
                            </td>
                            <td
                              className="txn-td txn-td--amount"
                              title={
                                group.hasIncome
                                  ? "Ausgaben: Plan plus Übertrag minus Ist. Einnahmen: noch ausstehender Zufluss gegenüber Plan."
                                  : "Plan plus Übertrag minus Ist"
                              }
                            >
                              <GroupAmounts
                                expenseMinor={group.total_remaining_minor}
                                incomeMinor={group.total_income_remaining_minor}
                                showExpense={group.hasExpense || !group.hasIncome}
                                showIncome={group.hasIncome}
                                expenseTone="signed"
                              />
                            </td>
                            {/* Der Uebertrag steckt rechnerisch im Rest, war aber
                              bisher auf Gruppenebene nirgends ablesbar. */}
                            <td colSpan={2} className="txn-td budget-group-row__note">
                              {group.total_rollover_minor !== 0 && (
                                <span className="txn-note">
                                  Übertrag {formatMinorUnitsAsEuro(group.total_rollover_minor)}
                                </span>
                              )}
                            </td>
                          </tr>
                          {group.categories?.map((cat) => {
                            // Einnahmekategorien fuehren ihren Istwert getrennt in
                            // income_actual_minor; spent_minor ist dort immer 0.
                            const isIncome = cat.default_type === "income";
                            const actualMinor = isIncome
                              ? cat.income_actual_minor
                              : cat.spent_minor;
                            // percent_used kommt fertig vom Backend (Einnahmen
                            // gegen Plan, Ausgaben gegen Plan). Ohne Planbetrag
                            // gibt es keine Quote - dann steht ein gebuchter
                            // Betrag fuer 100 % "ausserhalb des Plans".
                            const spentPct =
                              cat.planned_minor > 0
                                ? Math.min(100, Math.max(0, Math.round(cat.percent_used)))
                                : actualMinor !== 0
                                  ? 100
                                  : 0;
                            const barTone = getProgressBarTone(spentPct);
                            // Einmal definiert, zweimal verwendet (Maus und
                            // Tastatur) - zwei Kopien desselben Rumpfs waeren
                            // zwei Stellen, an denen der Titel auseinanderlaeuft.
                            const openPrevMonthDrilldown = () => {
                              const prev = prevMonthOf(year, month);
                              setDrilldown({
                                title: `${cat.category_name} · ${formatMonthYear(new Date(prev.year, prev.month - 1, 1))}`,
                                categoryId: cat.category_id,
                                year: prev.year,
                                month: prev.month,
                              });
                            };
                            const openDrilldown = () => {
                              setDrilldown({
                                title: cat.category_name,
                                categoryId: cat.category_id,
                                year,
                                month,
                              });
                            };

                            return (
                              <tr key={cat.category_id} className="txn-tr">
                                <td className="txn-td" style={{ paddingLeft: "var(--space-4)" }}>
                                  {cat.category_name}
                                </td>
                                <td className="txn-td">
                                  {isIncome ? (
                                    <span
                                      className="status-badge status-badge--positive"
                                      style={{ fontSize: "10px" }}
                                    >
                                      Einnahme
                                    </span>
                                  ) : (
                                    cat.budget_bucket && (
                                      <span
                                        className="status-badge status-badge--neutral"
                                        style={{ fontSize: "10px" }}
                                      >
                                        {cat.budget_bucket}
                                      </span>
                                    )
                                  )}
                                </td>
                                <td
                                  className={`txn-td txn-td--amount txn-note ${cat.prev_month_actual_minor !== 0 ? "explorer-row" : ""}`}
                                  style={{ fontFamily: "var(--font-mono)" }}
                                  title="Ist des Vormonats - anklicken für die Buchungen"
                                  role={cat.prev_month_actual_minor !== 0 ? "button" : undefined}
                                  tabIndex={cat.prev_month_actual_minor !== 0 ? 0 : undefined}
                                  aria-label={
                                    cat.prev_month_actual_minor !== 0
                                      ? `Buchungen des Vormonats zu ${cat.category_name} anzeigen`
                                      : undefined
                                  }
                                  onClick={
                                    cat.prev_month_actual_minor !== 0
                                      ? openPrevMonthDrilldown
                                      : undefined
                                  }
                                  onKeyDown={
                                    cat.prev_month_actual_minor !== 0
                                      ? activateOnKey(openPrevMonthDrilldown)
                                      : undefined
                                  }
                                >
                                  {cat.prev_month_actual_minor !== 0
                                    ? formatMinorUnitsAsEuro(cat.prev_month_actual_minor)
                                    : "—"}
                                </td>
                                <td className="txn-td txn-td--amount">
                                  <input
                                    type="text"
                                    className="cell-input"
                                    style={{ textAlign: "right", width: "90px" }}
                                    value={
                                      editingPlanned[cat.category_id] ??
                                      (cat.planned_minor / 100).toFixed(2).replace(".", ",")
                                    }
                                    disabled={budget.status === "closed"}
                                    onChange={(e) =>
                                      setEditingPlanned({
                                        ...editingPlanned,
                                        [cat.category_id]: e.target.value,
                                      })
                                    }
                                    onBlur={() =>
                                      handlePlannedBlur(
                                        cat.category_id,
                                        cat.planned_minor,
                                        cat.rollover_rule,
                                      )
                                    }
                                  />
                                </td>
                                <td
                                  className="txn-td txn-td--amount txn-note"
                                  style={{ fontFamily: "var(--font-mono)" }}
                                  title={forecastTitle(cat, formatDate)}
                                  data-testid={`budget-forecast-${cat.category_id}`}
                                >
                                  {cat.contract_forecast_minor !== 0
                                    ? formatMinorUnitsAsEuro(cat.contract_forecast_minor)
                                    : "—"}
                                </td>
                                <td
                                  className={`txn-td txn-td--amount explorer-row ${isIncome ? "transaction-amount--positive" : ""}`}
                                  style={{ fontFamily: "var(--font-mono)" }}
                                  role="button"
                                  tabIndex={0}
                                  aria-label={`Buchungen zu ${cat.category_name} anzeigen`}
                                  onClick={openDrilldown}
                                  onKeyDown={activateOnKey(openDrilldown)}
                                >
                                  {formatMinorUnitsAsEuro(actualMinor)}
                                </td>
                                <td
                                  className={`txn-td txn-td--amount ${cat.remaining_minor < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
                                  style={{ fontFamily: "var(--font-mono)" }}
                                  title={
                                    isIncome
                                      ? "Noch ausstehende Einnahme gegenüber Plan"
                                      : "Plan plus Übertrag minus Ist"
                                  }
                                >
                                  {formatMinorUnitsAsEuro(cat.remaining_minor)}
                                </td>
                                <td className="txn-td">
                                  <ProgressBar
                                    value={spentPct}
                                    tone={barTone}
                                    showValue
                                    testId={`budget-row-progress-${cat.category_id}`}
                                  />
                                </td>
                                <td
                                  className="txn-td"
                                  style={{ textAlign: "right", fontSize: "12px" }}
                                >
                                  <div
                                    style={{
                                      display: "flex",
                                      flexDirection: "column",
                                      alignItems: "flex-end",
                                      gap: "2px",
                                    }}
                                  >
                                    {cat.rollover_minor !== 0 && (
                                      <span
                                        className="status-badge status-badge--highlight"
                                        style={{ fontFamily: "var(--font-mono)" }}
                                      >
                                        {formatMinorUnitsAsEuro(cat.rollover_minor)}
                                      </span>
                                    )}
                                    <select
                                      className="form-input"
                                      style={{ fontSize: "11px", padding: "2px 4px" }}
                                      value={cat.rollover_rule}
                                      disabled={budget.status === "closed"}
                                      onChange={(e) =>
                                        handleRolloverRuleChange(
                                          cat.category_id,
                                          cat.planned_minor,
                                          e.target.value as "none" | "accumulate" | "reset",
                                        )
                                      }
                                    >
                                      <option value="none">Kein Übertrag</option>
                                      <option value="accumulate">Übertrag ansammeln</option>
                                      <option value="reset">Zurücksetzen</option>
                                    </select>
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {activeTab === "reserves" && budget && token && householdId && (
        <ReservesLedger
          token={token}
          householdId={householdId}
          year={year}
          month={month}
          budget={budget}
          includeInPlan={includeGoalReserves}
          onIncludeInPlanChange={setIncludeGoalReserves}
        />
      )}

      {activeTab === "matrix" && (
        <>
          {isMatrixLoading && <p className="placeholder-note">Lade Jahresmatrix {year}...</p>}

          {/* Den Leerzustand rendert die Komponente selbst - sie kennt das
              Ergebnis ihres eigenen Filters, die Seite nur dessen Zaehlung. */}
          {!isMatrixLoading && matrix && (
            <AnnualMatrixTable
              matrix={matrix}
              year={year}
              showEmpty={showEmptyCategories}
              onDrilldown={({ categoryId, categoryName, month: cellMonth }) =>
                setDrilldown({
                  title: `${categoryName} · ${String(cellMonth).padStart(2, "0")}/${year}`,
                  categoryId,
                  year,
                  month: cellMonth,
                })
              }
            />
          )}
        </>
      )}

      {drilldown && (
        <TransactionDrilldown
          title={drilldown.title}
          subtitle="Buchungen hinter diesem Ist-Wert"
          query={rowsQuery({
            field: "category",
            value: drilldown.categoryId,
            from_date: monthRange(drilldown.year, drilldown.month).from,
            to_date: monthRange(drilldown.year, drilldown.month).to,
          })}
          onClose={() => setDrilldown(null)}
        />
      )}
    </section>
  );
}
