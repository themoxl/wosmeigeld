/// <reference types="jest" />
import {
  calcProgress,
  describeBudgetOverview,
  describeExpenseCategory,
  describeExpenseGroup,
  describeIncomeCategory,
  describePlanBasis,
} from "./budgetPresentation";

describe("describePlanBasis", () => {
  it("summiert Plan und Uebertrag zur Zielgroesse und beschriftet ohne Uebertrag nur 'Plan'", () => {
    const result = describePlanBasis(50000, 0);
    expect(result.totalMinor).toBe(50000);
    expect(result.totalLabel).toBe("500,00 €");
    expect(result.hasRollover).toBe(false);
    expect(result.rolloverLabel).toBeNull();
    expect(result.basisLabel).toBe("Plan");
  });

  it("zeigt den Uebertrag als eigenen Wert, wenn er ungleich null ist", () => {
    const result = describePlanBasis(50000, 2500);
    expect(result.totalMinor).toBe(52500);
    expect(result.totalLabel).toBe("525,00 €");
    expect(result.hasRollover).toBe(true);
    expect(result.rolloverLabel).toBe("25,00 €");
    expect(result.basisLabel).toBe("Plan + Übertrag");
  });

  it("zeigt einen negativen Uebertrag ebenfalls als eigenen Wert", () => {
    const result = describePlanBasis(50000, -1000);
    expect(result.totalMinor).toBe(49000);
    expect(result.hasRollover).toBe(true);
    expect(result.rolloverLabel).toBe("-10,00 €");
    expect(result.basisLabel).toBe("Plan + Übertrag");
  });
});

describe("calcProgress", () => {
  it("teilt Zaehler durch Nenner", () => {
    expect(calcProgress(2500, 5000)).toBe(0.5);
  });

  it("ist 0 bei einer nicht positiven Zielgroesse statt NaN/Infinity", () => {
    expect(calcProgress(2500, 0)).toBe(0);
    expect(calcProgress(2500, -100)).toBe(0);
  });

  it("ist 0 bei einem nicht positiven Zaehler (Erstattungsueberschuss)", () => {
    expect(calcProgress(-500, 5000)).toBe(0);
    expect(calcProgress(0, 5000)).toBe(0);
  });

  it("kann ueber 1 liegen (Ueberziehung), die ProgressBar zeigt das eigenstaendig an", () => {
    expect(calcProgress(6000, 5000)).toBe(1.2);
  });
});

describe("describeBudgetOverview", () => {
  const base = {
    total_planned_minor: 50000,
    total_rollover_minor: 0,
    total_spent_minor: 41250,
    total_remaining_minor: 8750,
  };

  it("formuliert den Normalfall als 'netto ausgegeben' mit Fortschritt gegen Plan + Uebertrag", () => {
    const result = describeBudgetOverview(base);
    expect(result.isSurplus).toBe(false);
    expect(result.headline).toBe("412,50 € netto ausgegeben");
    expect(result.planBasis.totalLabel).toBe("500,00 €");
    expect(result.planBasis.basisLabel).toBe("Plan");
    expect(result.progress).toBeCloseTo(0.825);
    expect(result.remainingLabel).toBe("87,50 €");
    expect(result.remainingIsNegative).toBe(false);
  });

  it("rechnet den Uebertrag in die Zielgroesse ein und benennt ihn im Basislabel", () => {
    const result = describeBudgetOverview({ ...base, total_rollover_minor: 5000 });
    expect(result.planBasis.totalMinor).toBe(55000);
    expect(result.planBasis.basisLabel).toBe("Plan + Übertrag");
  });

  it("zeigt bei negativem Nettoverbrauch einen Erstattungsueberschuss und startet den Balken bei null", () => {
    const result = describeBudgetOverview({
      ...base,
      total_spent_minor: -3660,
      total_remaining_minor: 53660,
    });
    expect(result.isSurplus).toBe(true);
    expect(result.headline).toBe("36,60 € Erstattungsüberschuss");
    expect(result.headline).not.toContain("-");
    expect(result.progress).toBe(0);
  });

  it("markiert einen negativen Budgetrest (Ueberziehung)", () => {
    const result = describeBudgetOverview({
      ...base,
      total_spent_minor: 60000,
      total_remaining_minor: -10000,
    });
    expect(result.remainingIsNegative).toBe(true);
    expect(result.remainingLabel).toBe("-100,00 €");
    expect(result.progress).toBeCloseTo(1.2);
  });
});

describe("describeExpenseGroup", () => {
  it("stellt Ist gegen Plan + Uebertrag", () => {
    const result = describeExpenseGroup({
      total_spent_minor: 16000,
      total_planned_minor: 20000,
      total_rollover_minor: 0,
    });
    expect(result.isSurplus).toBe(false);
    expect(result.spentLabel).toBe("160,00 €");
    expect(result.planBasis.totalLabel).toBe("200,00 €");
    expect(result.progress).toBeCloseTo(0.8);
  });

  it("zeigt bei negativem Gruppen-Ist ebenfalls den Erstattungsueberschuss statt eines negativen Werts", () => {
    const result = describeExpenseGroup({
      total_spent_minor: -1000,
      total_planned_minor: 5000,
      total_rollover_minor: 0,
    });
    expect(result.isSurplus).toBe(true);
    expect(result.spentLabel).toBe("10,00 € mehr erstattet als ausgegeben");
    expect(result.spentLabel).not.toContain("-");
    expect(result.progress).toBe(0);
  });
});

describe("describeExpenseCategory", () => {
  it("formuliert 'X von Y ausgegeben' im Normalfall", () => {
    const result = describeExpenseCategory({
      spent_minor: 4500,
      planned_minor: 6000,
      rollover_minor: 0,
      remaining_minor: 1500,
    });
    expect(result.isSurplus).toBe(false);
    expect(result.headline).toBe("45,00 € von 60,00 € ausgegeben");
    expect(result.progress).toBeCloseTo(0.75);
    expect(result.remainingLabel).toBe("15,00 €");
    expect(result.remainingIsNegative).toBe(false);
  });

  it("zeigt bei negativem spent_minor keinen negativen Wert, sondern den Erstattungsueberschuss", () => {
    const result = describeExpenseCategory({
      spent_minor: -3660,
      planned_minor: 6000,
      rollover_minor: 0,
      remaining_minor: 9660,
    });
    expect(result.isSurplus).toBe(true);
    expect(result.headline).toBe("36,60 € mehr erstattet als ausgegeben");
    expect(result.headline).not.toContain("-");
    expect(result.progress).toBe(0);
  });

  it("markiert Ueberziehung am negativen Budgetrest", () => {
    const result = describeExpenseCategory({
      spent_minor: 7000,
      planned_minor: 6000,
      rollover_minor: 0,
      remaining_minor: -1000,
    });
    expect(result.remainingIsNegative).toBe(true);
    expect(result.progress).toBeCloseTo(7000 / 6000);
  });
});

describe("describeIncomeCategory", () => {
  it("formuliert 'X von Y eingegangen' und 'Noch erwartet' aus income_actual_minor", () => {
    const result = describeIncomeCategory({
      income_actual_minor: 200000,
      planned_minor: 250000,
      rollover_minor: 0,
      remaining_minor: 50000,
    });
    expect(result.headline).toBe("2.000,00 € von 2.500,00 € eingegangen");
    expect(result.stillExpectedLabel).toBe("500,00 €");
  });

  it("rechnet einen Uebertrag in die Erwartet-Zielgroesse ein", () => {
    const result = describeIncomeCategory({
      income_actual_minor: 0,
      planned_minor: 100000,
      rollover_minor: 2000,
      remaining_minor: 102000,
    });
    expect(result.planBasis.totalLabel).toBe("1.020,00 €");
    expect(result.headline).toBe("0,00 € von 1.020,00 € eingegangen");
  });
});
