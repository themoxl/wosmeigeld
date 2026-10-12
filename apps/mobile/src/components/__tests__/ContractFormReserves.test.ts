/// <reference types="jest" />
import {
  EMPTY_CONTRACT_FORM,
  isReserveEligible,
  validateContractForm,
  type ContractFormValues,
} from "../ContractForm";

function validValues(overrides: Partial<ContractFormValues> = {}): ContractFormValues {
  return {
    ...EMPTY_CONTRACT_FORM,
    name: "Jahresversicherung",
    amount: "1.200,00",
    frequency: "yearly",
    reserveEnabled: true,
    reserveStartedOn: "2026-08-01",
    reserveStartingAmount: "200,00",
    ...overrides,
  };
}

describe("ContractForm reserve planning", () => {
  it.each(["quarterly", "semiannual", "yearly"] as const)(
    "allows an expense with %s frequency",
    (frequency) => {
      expect(isReserveEligible({ direction: "expense", frequency })).toBe(true);
    },
  );

  it("rejects monthly/weekly expenses and every income contract", () => {
    expect(isReserveEligible({ direction: "expense", frequency: "monthly" })).toBe(false);
    expect(isReserveEligible({ direction: "expense", frequency: "weekly" })).toBe(false);
    expect(isReserveEligible({ direction: "income", frequency: "yearly" })).toBe(false);
  });

  it("validates confirmed start date and starting balance only when reserve planning is active", () => {
    expect(validateContractForm(validValues())).toBeNull();
    expect(validateContractForm(validValues({ reserveStartedOn: "August" }))).toBe(
      "Rücklagen-Startdatum im Format JJJJ-MM-TT eintragen.",
    );
    expect(validateContractForm(validValues({ reserveStartingAmount: "-1,00" }))).toBe(
      "Der bestätigte Anfangsbetrag darf nicht negativ sein.",
    );
    expect(
      validateContractForm(
        validValues({
          frequency: "monthly",
          reserveStartedOn: "August",
          reserveStartingAmount: "-1,00",
        }),
      ),
    ).toBeNull();
  });
});
