/// <reference types="jest" />
import type { Transaction } from "../api/types";
import {
  filterPendingTransactionsForPage,
  matchesTransactionsPageFilters,
  pendingCreatesAsTransactions,
  type PendingTransaction,
} from "./pendingTransactions";
import type { Queue } from "./queueTypes";

// docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5: "Pending-Offlinedaten
// durchlaufen lokal dieselben Konto-, Datums-, Such-, Kategorie- und
// Richtungsfilter". Diese Tests decken sowohl die reine Filterfunktion als
// auch die Kombination mit pendingCreatesAsTransactions() ab, wie sie
// useTransactionsPage() in ../hooks/useTransactions.ts verwendet.

function makeTransaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: "t1",
    account_id: "acc-1",
    booked_date: "2026-08-05",
    value_date: null,
    amount_minor: -1200,
    currency: "EUR",
    payee_label: "Rewe",
    note: null,
    status: "booked",
    is_transfer: false,
    category_id: "cat-1",
    is_split: false,
    version: 1,
    is_archived: false,
    ...overrides,
  };
}

describe("matchesTransactionsPageFilters", () => {
  it("laesst eine Buchung ohne gesetzte Filter durch", () => {
    expect(matchesTransactionsPageFilters(makeTransaction(), {})).toBe(true);
  });

  it("filtert nach Konto", () => {
    const transaction = makeTransaction({ account_id: "acc-1" });
    expect(matchesTransactionsPageFilters(transaction, { accountId: "acc-1" })).toBe(true);
    expect(matchesTransactionsPageFilters(transaction, { accountId: "acc-2" })).toBe(false);
  });

  it("filtert nach Datumsbereich", () => {
    const transaction = makeTransaction({ booked_date: "2026-08-05" });
    expect(
      matchesTransactionsPageFilters(transaction, {
        fromDate: "2026-08-01",
        toDate: "2026-08-31",
      }),
    ).toBe(true);
    expect(matchesTransactionsPageFilters(transaction, { fromDate: "2026-08-06" })).toBe(false);
    expect(matchesTransactionsPageFilters(transaction, { toDate: "2026-08-04" })).toBe(false);
  });

  it("findet Suchtext in Empfaenger oder Notiz, gross-/kleinschreibungsunabhaengig", () => {
    const transaction = makeTransaction({ payee_label: "Aldi Süd", note: "Wocheneinkauf" });
    expect(matchesTransactionsPageFilters(transaction, { search: "aldi" })).toBe(true);
    expect(matchesTransactionsPageFilters(transaction, { search: "einkauf" })).toBe(true);
    expect(matchesTransactionsPageFilters(transaction, { search: "lidl" })).toBe(false);
  });

  it("filtert nach Kategorie oder exklusiv nach 'Ohne Kategorie'", () => {
    const categorized = makeTransaction({ category_id: "cat-1" });
    const uncategorized = makeTransaction({ category_id: null });

    expect(matchesTransactionsPageFilters(categorized, { categoryId: "cat-1" })).toBe(true);
    expect(matchesTransactionsPageFilters(categorized, { categoryId: "cat-2" })).toBe(false);
    expect(matchesTransactionsPageFilters(uncategorized, { uncategorized: true })).toBe(true);
    expect(matchesTransactionsPageFilters(categorized, { uncategorized: true })).toBe(false);
    // uncategorized gewinnt, falls beides gesetzt ist (wie beim Server).
    expect(
      matchesTransactionsPageFilters(uncategorized, { uncategorized: true, categoryId: "cat-1" }),
    ).toBe(true);
  });

  it("filtert nach Richtung", () => {
    const income = makeTransaction({ amount_minor: 5000 });
    const expense = makeTransaction({ amount_minor: -5000 });

    expect(matchesTransactionsPageFilters(income, { direction: "income" })).toBe(true);
    expect(matchesTransactionsPageFilters(expense, { direction: "income" })).toBe(false);
    expect(matchesTransactionsPageFilters(expense, { direction: "expense" })).toBe(true);
    expect(matchesTransactionsPageFilters(income, { direction: "expense" })).toBe(false);
  });
});

describe("filterPendingTransactionsForPage", () => {
  const queue: Queue = [
    {
      entryId: "e1",
      localId: "local-1",
      householdId: "hh-1",
      status: "pending",
      attempts: 0,
      createdAt: "2026-08-05T10:00:00.000Z",
      nextAttemptAt: 0,
      kind: "create",
      payload: {
        account_id: "acc-1",
        booked_date: "2026-08-05",
        amount_minor: -1500,
        payee_label: "Bäckerei Schmidt",
        category_id: "cat-1",
      },
    },
    {
      entryId: "e2",
      localId: "local-2",
      householdId: "hh-1",
      status: "pending",
      attempts: 0,
      createdAt: "2026-08-06T10:00:00.000Z",
      nextAttemptAt: 0,
      kind: "create",
      payload: {
        account_id: "acc-2",
        booked_date: "2026-08-06",
        amount_minor: 20000,
        payee_label: "Gehalt",
      },
    },
  ];

  function pending(): PendingTransaction[] {
    return pendingCreatesAsTransactions(queue, "hh-1");
  }

  it("laesst beide Pending-Buchungen ohne Filter durch", () => {
    expect(filterPendingTransactionsForPage(pending(), {})).toHaveLength(2);
  });

  it("wendet den Kontofilter auf Pending-Buchungen an", () => {
    const result = filterPendingTransactionsForPage(pending(), { accountId: "acc-1" });
    expect(result.map((t) => t.id)).toEqual(["local-1"]);
  });

  it("wendet Such-, Kategorie- und Richtungsfilter kombiniert an", () => {
    const result = filterPendingTransactionsForPage(pending(), {
      search: "schmidt",
      categoryId: "cat-1",
      direction: "expense",
    });
    expect(result.map((t) => t.id)).toEqual(["local-1"]);
  });

  it("liefert eine leere Liste, wenn kein Pending-Eintrag passt", () => {
    const result = filterPendingTransactionsForPage(pending(), { uncategorized: true });
    // local-2 hat keine category_id (undefined -> null im Platzhalter), local-1 schon.
    expect(result.map((t) => t.id)).toEqual(["local-2"]);
  });
});
