import type { Transaction, TransactionDirection } from "../api/types";
import type { Queue } from "./queueTypes";

/**
 * Client-only Marker fuer noch nicht synchronisierte Neuanlagen (Phase 9).
 *
 * Diese werden bewusst NICHT in den React-Query-Cache geschrieben: ein
 * Refetch oder Pull-to-Refresh wuerde sie sonst kommentarlos wieder aus der
 * Liste werfen, bis die Queue synchronisiert und invalidiert. Stattdessen
 * mischt useTransactions() sie bei jedem Render frisch aus der Queue dazu.
 */
export interface PendingTransaction extends Transaction {
  __pendingSync: true;
}

export function isPendingTransaction(transaction: Transaction): transaction is PendingTransaction {
  return (transaction as Partial<PendingTransaction>).__pendingSync === true;
}

/** Noch nicht synchronisierte Neuanlagen eines Haushalts als Transaction-foermige Platzhalter. */
export function pendingCreatesAsTransactions(
  queue: Queue,
  householdId: string,
  accountId?: string,
): PendingTransaction[] {
  return queue
    .filter(
      (entry): entry is Extract<Queue[number], { kind: "create" }> =>
        entry.kind === "create" &&
        entry.householdId === householdId &&
        (!accountId || entry.payload.account_id === accountId),
    )
    .map((entry): PendingTransaction => {
      const { payload } = entry;
      return {
        id: entry.localId,
        account_id: payload.account_id,
        booked_date: payload.booked_date,
        value_date: null,
        amount_minor: payload.amount_minor,
        currency: payload.currency ?? "EUR",
        payee_label: payload.payee_label ?? null,
        note: payload.note ?? null,
        status: "pending",
        is_transfer: false,
        category_id: payload.category_id ?? null,
        is_split: false,
        // Es gibt serverseitig noch keine Buchung, also auch keine Version -
        // 0 macht sichtbar, dass hier nichts zu vergleichen ist.
        version: 0,
        is_archived: false,
        __pendingSync: true,
      };
    });
}

// --- Mobile-Redesign: Filter der paginierten Buchungsliste (Abschnitt 5) ---
//
// Pending-Neuanlagen existieren nur lokal und koennen deshalb nicht Teil der
// Server-Antwort von GET /v1/transactions/page sein. Damit sie trotzdem nur
// dort auftauchen, wo sie laut aktivem Filter hingehoeren ("Pending-
// Offlinedaten durchlaufen lokal dieselben Konto-, Datums-, Such-,
// Kategorie- und Richtungsfilter"), laufen sie hier durch dieselben
// fachlichen Kriterien wie die Serverabfrage - nur eben clientseitig auf dem
// kleinen Pending-Bestand statt per SQL.

/** Filterkriterien der paginierten Buchungsliste, nullable fuer "kein Filter gesetzt". */
export interface TransactionsPageFilterCriteria {
  accountId?: string | null;
  fromDate?: string | null;
  toDate?: string | null;
  search?: string | null;
  categoryId?: string | null;
  uncategorized?: boolean;
  direction?: TransactionDirection | null;
}

/** true, wenn `transaction` allen gesetzten Kriterien genuegt (ungesetzte Kriterien sind kein Filter). */
export function matchesTransactionsPageFilters(
  transaction: Transaction,
  filters: TransactionsPageFilterCriteria,
): boolean {
  if (filters.accountId && transaction.account_id !== filters.accountId) return false;
  if (filters.fromDate && transaction.booked_date < filters.fromDate) return false;
  if (filters.toDate && transaction.booked_date > filters.toDate) return false;

  const search = (filters.search ?? "").trim().toLowerCase();
  if (search) {
    // Dieselben Textfelder wie die Serversuche (Empfaenger, Notiz) -
    // purpose_raw/payee_raw kennt der lokale Platzhalter nicht, siehe
    // pendingCreatesAsTransactions() oben.
    const haystack = `${transaction.payee_label ?? ""} ${transaction.note ?? ""}`.toLowerCase();
    if (!haystack.includes(search)) return false;
  }

  if (filters.uncategorized) {
    if (transaction.category_id !== null) return false;
  } else if (filters.categoryId && transaction.category_id !== filters.categoryId) {
    return false;
  }

  if (filters.direction === "income" && transaction.amount_minor <= 0) return false;
  if (filters.direction === "expense" && transaction.amount_minor >= 0) return false;

  return true;
}

/** Filtert Pending-Platzhalter auf dieselben Kriterien wie die Serverabfrage. */
export function filterPendingTransactionsForPage<T extends Transaction>(
  pending: T[],
  filters: TransactionsPageFilterCriteria,
): T[] {
  return pending.filter((transaction) => matchesTransactionsPageFilters(transaction, filters));
}
