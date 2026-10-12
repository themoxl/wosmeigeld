/// <reference types="jest" />
import {
  formatDueDate,
  formatMonthLabel,
  formatShortDate,
  formatTransactionDayLabel,
  isContractNotStarted,
  parseIsoDate,
} from "./dates";

describe("parseIsoDate", () => {
  it("parses a plain ISO date as a local calendar day", () => {
    const parsed = parseIsoDate("2026-08-09");
    expect(parsed.getFullYear()).toBe(2026);
    expect(parsed.getMonth()).toBe(7); // August ist Index 7
    expect(parsed.getDate()).toBe(9);
  });

  it("falls back to native parsing for non-ISO strings", () => {
    const parsed = parseIsoDate("2026-08-09T10:00:00.000Z");
    expect(Number.isNaN(parsed.getTime())).toBe(false);
  });
});

describe("formatMonthLabel", () => {
  it("formats a Date as German month and year", () => {
    expect(formatMonthLabel(new Date(2026, 7, 9))).toBe("August 2026");
  });

  it("formats an ISO date string as German month and year", () => {
    expect(formatMonthLabel("2026-03-01")).toBe("März 2026");
  });

  it("formats December correctly across the year boundary", () => {
    expect(formatMonthLabel("2025-12-24")).toBe("Dezember 2025");
  });
});

describe("formatDueDate", () => {
  it("formats an ISO date string as a long German date", () => {
    expect(formatDueDate("2026-08-09")).toBe("9. August 2026");
  });

  it("formats a Date instance the same way", () => {
    expect(formatDueDate(new Date(2026, 0, 1))).toBe("1. Januar 2026");
  });
});

describe("formatShortDate", () => {
  it("formatiert einen ISO-Tag zweistellig ohne Jahr", () => {
    expect(formatShortDate("2026-10-22")).toBe("22.10.");
    expect(formatShortDate("2026-01-05")).toBe("05.01.");
  });

  it("nimmt denselben Kalendertag wie parseIsoDate, nicht UTC-Mitternacht", () => {
    expect(formatShortDate("2026-08-01")).toBe("01.08.");
  });

  it("endet auf einen Punkt - Aufrufer duerfen keinen zweiten anhaengen", () => {
    expect(formatShortDate(new Date(2026, 11, 24)).endsWith(".")).toBe(true);
  });
});

describe("isContractNotStarted", () => {
  const reference = new Date(2026, 7, 9); // 9. August 2026

  it("ist false ohne Startdatum", () => {
    expect(isContractNotStarted(null, reference)).toBe(false);
  });

  it("ist false, wenn das Startdatum das Referenzdatum selbst ist", () => {
    expect(isContractNotStarted("2026-08-09", reference)).toBe(false);
  });

  it("ist false fuer ein Startdatum in der Vergangenheit", () => {
    expect(isContractNotStarted("2026-08-01", reference)).toBe(false);
  });

  it("ist true fuer ein Startdatum in der Zukunft", () => {
    expect(isContractNotStarted("2026-08-10", reference)).toBe(true);
  });
});

describe("formatTransactionDayLabel", () => {
  const reference = new Date(2026, 7, 9); // Sonntag, 9. August 2026

  it('returns "Heute" for the reference date itself', () => {
    expect(formatTransactionDayLabel("2026-08-09", reference)).toBe("Heute");
  });

  it('returns "Gestern" for the day before the reference date', () => {
    expect(formatTransactionDayLabel("2026-08-08", reference)).toBe("Gestern");
  });

  it("returns a localized long date for older days", () => {
    expect(formatTransactionDayLabel("2026-08-01", reference)).toBe("1. August 2026");
  });

  it("returns a localized long date for future days", () => {
    expect(formatTransactionDayLabel("2026-08-10", reference)).toBe("10. August 2026");
  });

  it('crosses a month boundary correctly for "Gestern"', () => {
    const firstOfMonth = new Date(2026, 7, 1); // 1. August 2026
    expect(formatTransactionDayLabel("2026-07-31", firstOfMonth)).toBe("Gestern");
  });

  it("ignores the time component of the reference date", () => {
    const withTime = new Date(2026, 7, 9, 23, 59);
    expect(formatTransactionDayLabel("2026-08-09", withTime)).toBe("Heute");
  });
});
