/// <reference types="jest" />
import type { ContractDirection, ContractFrequency, ContractStatus } from "../api/types";
import {
  AFTER_FIXED_COSTS_LABEL,
  BUDGET_REMAINING_LABEL,
  BUDGET_REMAINING_LABEL_LONG,
  CONTRACT_BALANCE_LABEL,
  formatContractDirection,
  formatContractStatus,
  formatFrequency,
  formatIncomeBasisSource,
  formatReimbursementSurplus,
  INCOME_BASIS_ACTUAL_LABEL,
  INCOME_BASIS_ESTIMATED_LABEL,
  INCOME_EXPECTED_LABEL,
  INCOME_RECEIVED_LABEL,
  INCOME_STILL_EXPECTED_LABEL,
  MONTH_BALANCE_LABEL,
  SPENDING_MIX_LABEL,
} from "./financeLabels";

// docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 2: die fuenf Budgetbegriffe
// duerfen nicht synonym verwendet werden - dieser Test haelt die verbindliche
// Schreibweise fest, damit ein versehentliches Umbenennen auffaellt.
describe("verbindliche Budgetbegriffe", () => {
  it("benennt Budgetrest ohne 'Verfuegbar'", () => {
    expect(BUDGET_REMAINING_LABEL).toBe("Budgetrest");
    expect(BUDGET_REMAINING_LABEL_LONG).toBe("Rest im Monatsplan");
    expect(BUDGET_REMAINING_LABEL_LONG.toLowerCase()).not.toContain("verfügbar");
  });

  it("benennt Nach Fixkosten fuer variable Ausgaben", () => {
    expect(AFTER_FIXED_COSTS_LABEL).toBe("Nach Fixkosten für variable Ausgaben");
  });

  it("benennt Vertragssaldo", () => {
    expect(CONTRACT_BALANCE_LABEL).toBe("Vertragssaldo");
  });

  it("benennt Monatssaldo", () => {
    expect(MONTH_BALANCE_LABEL).toBe("Monatssaldo");
  });

  it("benennt den Ist-Ausgabenmix statt 'Budgetstatus'", () => {
    expect(SPENDING_MIX_LABEL).toBe("Ist-Ausgabenmix");
    expect(SPENDING_MIX_LABEL.toLowerCase()).not.toContain("budgetstatus");
  });
});

describe("Einnahmekategorie-Labels", () => {
  it("verwendet Erwartet/Eingegangen/Noch erwartet statt Ausgegeben/Rest", () => {
    expect(INCOME_EXPECTED_LABEL).toBe("Erwartet");
    expect(INCOME_RECEIVED_LABEL).toBe("Eingegangen");
    expect(INCOME_STILL_EXPECTED_LABEL).toBe("Noch erwartet");
  });
});

describe("formatReimbursementSurplus", () => {
  it("formuliert einen negativen spent_minor als Erstattungsueberschuss", () => {
    expect(formatReimbursementSurplus("36,60 EUR")).toBe("36,60 EUR mehr erstattet als ausgegeben");
  });

  it("enthaelt kein negatives Vorzeichen im Text", () => {
    expect(formatReimbursementSurplus("36,60 EUR")).not.toContain("-");
  });
});

describe("formatIncomeBasisSource", () => {
  it("unterscheidet gebuchte Basis von Schaetzung", () => {
    expect(formatIncomeBasisSource(false)).toBe(INCOME_BASIS_ACTUAL_LABEL);
    expect(formatIncomeBasisSource(true)).toBe(INCOME_BASIS_ESTIMATED_LABEL);
    expect(INCOME_BASIS_ACTUAL_LABEL).toBe("auf Basis gebuchter Einnahmen");
    expect(INCOME_BASIS_ESTIMATED_LABEL).toBe("Schätzung aus Einnahmeverträgen");
  });
});

describe("formatFrequency", () => {
  const cases: [ContractFrequency, string][] = [
    ["weekly", "Wöchentlich"],
    ["monthly", "Monatlich"],
    ["quarterly", "Quartalsweise"],
    ["semiannual", "Halbjährlich"],
    ["yearly", "Jährlich"],
  ];

  it.each(cases)("formatiert %s als %s", (frequency, expected) => {
    expect(formatFrequency(frequency)).toBe(expected);
  });
});

describe("formatContractStatus", () => {
  const cases: [ContractStatus, string][] = [
    ["active", "Aktiv"],
    ["paused", "Pausiert"],
    ["cancelled", "Gekündigt"],
    ["ended", "Beendet"],
    ["not_a_contract", "Kein Vertrag"],
  ];

  it.each(cases)("formatiert %s als %s", (status, expected) => {
    expect(formatContractStatus(status)).toBe(expected);
  });
});

describe("formatContractDirection", () => {
  const cases: [ContractDirection, string][] = [
    ["expense", "Ausgaben"],
    ["income", "Einnahmen"],
  ];

  it.each(cases)("formatiert %s als %s", (direction, expected) => {
    expect(formatContractDirection(direction)).toBe(expected);
  });
});
