import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo, useSyncExternalStore } from "react";
import { listTransactions, listTransactionsPage } from "../api/client";
import type {
  ListTransactionsPageParams,
  ListTransactionsParams,
  Transaction,
  TransactionDirection,
  TransactionSort,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  filterPendingTransactionsForPage,
  pendingCreatesAsTransactions,
} from "../offline/pendingTransactions";
import { getQueueSnapshot, subscribeQueue } from "../offline/queueStore";

function sortByBookedDateDesc(transactions: Transaction[]): Transaction[] {
  // Backend-Reihenfolge nicht garantiert - clientseitig zusaetzlich nach
  // Buchungsdatum absteigend sortieren ("neueste zuerst", Auftrag Punkt 6).
  return [...transactions].sort((a, b) =>
    a.booked_date < b.booked_date ? 1 : a.booked_date > b.booked_date ? -1 : 0,
  );
}

/** Buchungen des aktuell ausgewaehlten Haushalts (GET /v1/transactions), neueste zuerst. */
export function useTransactions(params?: ListTransactionsParams) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  // Phase 9 (Offline-Write-Queue): noch nicht synchronisierte Neuanlagen
  // leben nur in der Queue, nicht im React-Query-Cache (siehe
  // ../offline/pendingTransactions.ts) - hier bei jedem Render frisch
  // dazugemischt, damit sie sofort nach dem Anlegen in der Liste erscheinen
  // und einen Refetch ueberleben.
  const queue = useSyncExternalStore(subscribeQueue, getQueueSnapshot, getQueueSnapshot);

  const query = useQuery({
    queryKey: [
      "transactions",
      selectedHouseholdId,
      params?.accountId ?? null,
      // Mobile-Redesign-Plan Abschnitt 5, "bestehender Bug": fromDate/toDate
      // fehlten hier, obwohl app/mehr/einkaufsliste.tsx bereits beide setzt -
      // ein Wechsel des Zeitraums (z. B. andere Wochenauswahl) fand deshalb
      // keinen neuen Query-Key und blieb auf dem alten Ergebnis stehen.
      params?.fromDate ?? null,
      params?.toDate ?? null,
      params?.limit ?? null,
    ],
    queryFn: ({ signal }) =>
      listTransactions(accessToken as string, selectedHouseholdId as string, params, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });

  const data = useMemo(() => {
    const base = query.data ?? [];
    if (!selectedHouseholdId) return sortByBookedDateDesc(base);
    const pending = pendingCreatesAsTransactions(queue, selectedHouseholdId, params?.accountId);
    return sortByBookedDateDesc([...base, ...pending]);
  }, [query.data, queue, selectedHouseholdId, params?.accountId]);

  return { ...query, data };
}

// --- Mobile-Redesign: paginierte Buchungsliste (Abschnitt 5) ---------------

const TRANSACTIONS_PAGE_LIMIT = 50;

export interface TransactionsPageFilters {
  accountId?: string;
  fromDate?: string;
  toDate?: string;
  search?: string;
  categoryId?: string;
  uncategorized?: boolean;
  direction?: TransactionDirection;
  sort?: TransactionSort;
}

interface NormalizedTransactionsPageFilters {
  accountId: string | null;
  fromDate: string | null;
  toDate: string | null;
  search: string | null;
  categoryId: string | null;
  uncategorized: boolean;
  direction: TransactionDirection | null;
  sort: TransactionSort;
}

/**
 * Normalisiert die Filter fuer Query-Key UND Request: leere/undefined Werte
 * werden auf `null` vereinheitlicht (sonst waeren `undefined` und `""` zwei
 * verschiedene Query-Keys fuer denselben fachlichen Zustand), Suchtext wird
 * getrimmt. `category_id`/`uncategorized` bleiben exklusiv wie beim Server
 * (siehe list_transactions_page): ein gesetztes `uncategorized` gewinnt.
 */
export function normalizeTransactionsPageFilters(
  filters: TransactionsPageFilters,
): NormalizedTransactionsPageFilters {
  const search = (filters.search ?? "").trim();
  const uncategorized = filters.uncategorized === true;
  return {
    accountId: filters.accountId ?? null,
    fromDate: filters.fromDate ?? null,
    toDate: filters.toDate ?? null,
    search: search.length > 0 ? search : null,
    categoryId: uncategorized ? null : (filters.categoryId ?? null),
    uncategorized,
    direction: filters.direction ?? null,
    sort: filters.sort ?? "date_desc",
  };
}

function compareStrings(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function compareDateThenId(a: Transaction, b: Transaction, ascending: boolean): number {
  const dateCompare = compareStrings(a.booked_date, b.booked_date);
  const primary = ascending ? dateCompare : -dateCompare;
  if (primary !== 0) return primary;
  const idCompare = compareStrings(a.id, b.id);
  return ascending ? idCompare : -idCompare;
}

/**
 * Vergleichsfunktion, die dieselbe Reihenfolge wie die jeweilige Server-
 * Sortierung erzeugt (services/api/app/api/v1/routes/transactions.py,
 * `_PAGE_SORT_SPECS`) - nur mit Datum/ID statt Datum/Erstellzeit/ID als
 * Tie-Breaker, weil Pending-Platzhalter kein `created_at` besitzen (siehe
 * pendingCreatesAsTransactions()). Wird zum Einsortieren gefilterter
 * Pending-Buchungen in die geladenen Seiten verwendet ("stabil einsortiert",
 * Redesign-Plan Abschnitt 5) und ist bewusst als reine Funktion exportiert,
 * damit sie unabhaengig vom Hook getestet werden kann.
 */
export function compareTransactionsForSort(
  a: Transaction,
  b: Transaction,
  sort: TransactionSort,
): number {
  switch (sort) {
    case "date_asc":
      return compareDateThenId(a, b, true);
    case "amount_asc":
      if (a.amount_minor !== b.amount_minor) return a.amount_minor - b.amount_minor;
      return compareDateThenId(a, b, true);
    case "amount_desc":
      if (a.amount_minor !== b.amount_minor) return b.amount_minor - a.amount_minor;
      return compareDateThenId(a, b, false);
    case "payee_asc": {
      const payeeCompare = compareStrings(
        (a.payee_label ?? "").toLowerCase(),
        (b.payee_label ?? "").toLowerCase(),
      );
      if (payeeCompare !== 0) return payeeCompare;
      return compareDateThenId(a, b, true);
    }
    case "date_desc":
    default:
      return compareDateThenId(a, b, false);
  }
}

function toPageParams(
  normalized: NormalizedTransactionsPageFilters,
  cursor: string | undefined,
): ListTransactionsPageParams {
  return {
    accountId: normalized.accountId ?? undefined,
    fromDate: normalized.fromDate ?? undefined,
    toDate: normalized.toDate ?? undefined,
    search: normalized.search ?? undefined,
    categoryId: normalized.categoryId ?? undefined,
    uncategorized: normalized.uncategorized || undefined,
    direction: normalized.direction ?? undefined,
    sort: normalized.sort,
    limit: TRANSACTIONS_PAGE_LIMIT,
    cursor,
  };
}

/**
 * Paginierte Buchungsliste fuer app/(tabs)/buchungen.tsx
 * (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5): `useInfiniteQuery` statt
 * einmalig `limit: 100` zu laden und lokal weiterzufiltern.
 *
 * Der Query-Key beginnt bewusst genauso wie bei useTransactions() oben
 * (`["transactions", householdId, ...]`), das dritte Element ist aber immer
 * das Literal `"page"` - dadurch bleiben beide Familien fuer React Query
 * unterscheidbare Query-Identitaeten (Heute behaelt seinen eigenen,
 * unveraenderten Key), aber jedes bestehende
 * `invalidateQueries({queryKey: ["transactions", ...]})` (Bulk-
 * Kategorisierung, Offline-Queue-Flush, Review, Import, KI-Klassifikation)
 * erfasst ueber den gemeinsamen Praefix automatisch auch diese Liste, ohne
 * dass jede dieser Stellen einzeln angepasst werden muss.
 *
 * Aus genau diesem gemeinsamen Praefix folgt aber auch: diese Query liegt
 * unter derselben Persistenz-Familie wie useTransactions() in
 * ../api/queryClient.ts. Ihre Daten sind als `{pages, pageParams}` keine
 * Array-Form - ../api/queryClient.ts filtert das beim Dehydrieren explizit
 * heraus, damit weder gefilterte noch ungefilterte Seiten unminimiert (inkl.
 * payee_label/note) auf der Platte landen (siehe Kommentar dort).
 */
export function useTransactionsPage(filters: TransactionsPageFilters) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queue = useSyncExternalStore(subscribeQueue, getQueueSnapshot, getQueueSnapshot);

  // Feldweise statt ueber `filters` selbst: buchungen.tsx uebergibt bei jedem
  // Render ein neues Objektliteral mit denselben Werten - ueber das ganze
  // Objekt zu haengen wuerde `normalized` (und damit den Query-Key) bei jedem
  // Tastendruck/Render neu erzeugen, auch wenn sich fachlich nichts geaendert
  // hat.
  const normalized = useMemo(
    () => normalizeTransactionsPageFilters(filters),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- feldweise Deps sind hier bewusst vollstaendig, siehe Kommentar oben.
    [
      filters.accountId,
      filters.fromDate,
      filters.toDate,
      filters.search,
      filters.categoryId,
      filters.uncategorized,
      filters.direction,
      filters.sort,
    ],
  );

  const query = useInfiniteQuery({
    queryKey: [
      "transactions",
      selectedHouseholdId,
      "page",
      normalized.accountId,
      normalized.fromDate,
      normalized.toDate,
      normalized.search,
      normalized.categoryId,
      normalized.uncategorized,
      normalized.direction,
      normalized.sort,
    ],
    queryFn: ({ pageParam, signal }) =>
      listTransactionsPage(
        accessToken as string,
        selectedHouseholdId as string,
        toPageParams(normalized, pageParam ?? undefined),
        signal,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: !!accessToken && !!selectedHouseholdId,
  });

  const loadedItems = useMemo(
    () => (query.data?.pages ?? []).flatMap((page) => page.items),
    [query.data],
  );

  const items = useMemo(() => {
    if (!selectedHouseholdId) return loadedItems;
    const pending = filterPendingTransactionsForPage(
      pendingCreatesAsTransactions(queue, selectedHouseholdId, normalized.accountId ?? undefined),
      normalized,
    );
    if (pending.length === 0) return loadedItems;
    // Stabil einsortiert statt vorangestellt: bei "Betrag absteigend"/
    // "Empfaenger A-Z" gehoert eine Pending-Buchung sonst nicht an den Anfang.
    return [...pending, ...loadedItems].sort((a, b) =>
      compareTransactionsForSort(a, b, normalized.sort),
    );
  }, [loadedItems, queue, selectedHouseholdId, normalized]);

  return { ...query, items };
}
