import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContract,
  deleteContract,
  getContractForecast,
  getContractSummary,
  listContractHistory,
  listContracts,
  setContractStatus,
  updateContract,
} from "../api/client";
import type { CategoryGroup } from "../api/categoryTypes";
import type {
  Contract,
  ContractAmountEntry,
  ContractCreate,
  ContractStatusChange,
  ContractSummary,
  ContractUpcoming,
  ContractUpdate,
  Person,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { flattenCategories } from "../utils/categorySearch";

/**
 * Vertragsverwaltung auf Mobile (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Phase 1).
 *
 * Loest useRecurring ab. `/v1/recurring` und `/v1/subscriptions` bleiben nur
 * serverseitig als Kompatibilitaetsschicht bestehen.
 */

interface ApiContext {
  accessToken: string;
  householdId: string;
  enabled: boolean;
  canEdit: boolean;
}

export function useContractContext(): ApiContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const role = selectedHousehold?.role ?? null;
  return {
    accessToken: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
    canEdit: role === "owner" || role === "editor",
  };
}

export function useInvalidateContracts() {
  const queryClient = useQueryClient();
  const { householdId } = useContractContext();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["contracts", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["contract-summary", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["contract-forecast", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["contract-history", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["budget", householdId] });
    // Der Uebersichtsbericht traegt seit Phase 4 die Fixkostenquote und
    // "verfuegbar nach Fixkosten". Ohne diese Zeile zeigte er nach dem Beenden
    // eines Vertrags weiter die alte Quote (Schluessel wie in useBudget.ts).
    void queryClient.invalidateQueries({ queryKey: ["overviewReport", householdId] });
  }, [queryClient, householdId]);
}

export type FixedCostsTone = "positive" | "highlight" | "warning" | "neutral";

export interface FixedCostsRating {
  tone: FixedCostsTone;
  label: string;
}

/** Schwellen der Fixkostenampel (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, I7). */
export const FIXED_COSTS_TENSE_PERCENT = 50;
export const FIXED_COSTS_CRITICAL_PERCENT = 70;

/**
 * Ampel zur Fixkostenquote - dieselben Schwellen wie im Web.
 *
 * Der Sonderfall ist der Grund fuer die Funktion: das Backend liefert 0 %, wenn
 * es keine Bezugsgroesse gibt (weder gebuchte Einnahmen noch Einnahmevertrag).
 * Gruen waere dort die gefaehrlichste Antwort - sie behauptet Entwarnung, wo
 * schlicht nichts bekannt ist.
 */
export function rateFixedCostsRatio(
  ratioPercent: number,
  fixedCostsMonthlyMinor: number,
): FixedCostsRating {
  if (fixedCostsMonthlyMinor === 0) return { tone: "neutral", label: "Keine Verträge" };
  if (ratioPercent <= 0) return { tone: "neutral", label: "Ohne Einnahmen" };
  if (ratioPercent < FIXED_COSTS_TENSE_PERCENT) return { tone: "positive", label: "Gut" };
  if (ratioPercent <= FIXED_COSTS_CRITICAL_PERCENT) {
    return { tone: "highlight", label: "Angespannt" };
  }
  return { tone: "warning", label: "Kritisch" };
}

// -----------------------------------------------------------------------
// Kategoriegliederung fuer die Vertragsliste (Redesign-Plan Abschnitt 7:
// "FlatList wird zu SectionList ... Sections pro konkreter Kategorie").
// -----------------------------------------------------------------------

export interface ContractSection {
  /** category_id oder der feste Schluessel fuer "Ohne Kategorie". */
  key: string;
  title: string;
  data: Contract[];
}

/** Sammelschluessel fuer Vertraege ohne (oder mit einer im Kategoriebaum
 * nicht mehr auffindbaren, z. B. archivierten) Kategorie. */
export const UNCATEGORIZED_SECTION_KEY = "uncategorized";
export const UNCATEGORIZED_SECTION_LABEL = "Ohne Kategorie";

/** Naechste Faelligkeit zuerst, dann Name (Redesign-Plan Abschnitt 7). */
function compareContractsWithinSection(a: Contract, b: Contract): number {
  if (a.next_due_date !== b.next_due_date) {
    return a.next_due_date < b.next_due_date ? -1 : 1;
  }
  return a.name.localeCompare(b.name, "de");
}

/**
 * Gliedert Vertraege nach konkreter Kategorie fuer die SectionList in
 * app/(tabs)/vertraege.tsx: Kategoriegruppen-Sortierung, dann
 * Kategorie-Sortierung; "Ohne Kategorie" steht immer zuletzt, unabhaengig
 * von einer errechneten Sortierposition. Ein `category_id`, das im
 * (aktiven) Kategoriebaum nicht mehr auftaucht - etwa eine inzwischen
 * archivierte Kategorie -, faellt bewusst ebenfalls unter "Ohne Kategorie"
 * statt eine leere Section zu erzeugen.
 *
 * Die Sortierreihenfolge kommt bewusst aus `flattenCategories` (bereits fuer
 * die Kategorieauswahl gebaut und getestet, siehe utils/categorySearch.ts)
 * statt hier ein zweites Mal aus `sort_order` berechnet zu werden: die
 * Position einer Kategorie in der flachen, sortierten Liste IST schon die
 * gewuenschte Kategoriegruppen- dann Kategorie-Reihenfolge.
 */
export function groupContractsByCategory(
  contracts: Contract[],
  categoryGroups: CategoryGroup[],
): ContractSection[] {
  const flatCategories = flattenCategories(categoryGroups);
  const orderById = new Map(flatCategories.map((category, index) => [category.id, index]));
  const nameById = new Map(flatCategories.map((category) => [category.id, category.name]));

  interface Bucket {
    title: string;
    order: number;
    data: Contract[];
  }
  const buckets = new Map<string, Bucket>();

  for (const contract of contracts) {
    const order = contract.category_id ? orderById.get(contract.category_id) : undefined;
    const key =
      contract.category_id && order !== undefined
        ? contract.category_id
        : UNCATEGORIZED_SECTION_KEY;
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = {
        title: key === UNCATEGORIZED_SECTION_KEY ? UNCATEGORIZED_SECTION_LABEL : nameById.get(key)!,
        order: order ?? Number.POSITIVE_INFINITY,
        data: [],
      };
      buckets.set(key, bucket);
    }
    bucket.data.push(contract);
  }

  return Array.from(buckets.entries())
    .sort(([keyA, a], [keyB, b]) => {
      if (keyA === UNCATEGORIZED_SECTION_KEY) return 1;
      if (keyB === UNCATEGORIZED_SECTION_KEY) return -1;
      return a.order - b.order;
    })
    .map(([key, bucket]) => ({
      key,
      title: bucket.title,
      data: [...bucket.data].sort(compareContractsWithinSection),
    }));
}

/** Sammelschluessel fuer Einnahmen ohne Personenzuordnung. */
export const UNASSIGNED_PERSON_SECTION_KEY = "unassigned-person";
export const UNASSIGNED_PERSON_SECTION_LABEL = "Ohne Person";

/**
 * Gliedert Einnahmen nach Person fuer die SectionList in
 * app/(tabs)/vertraege.tsx (Liquiditaetsplan Stufe 6).
 *
 * Die Reihenfolge kommt aus der Personenliste des Servers (`sort_order`, dann
 * Name) und nicht aus den Vertragsdaten: sonst haette dieselbe Person je nach
 * Faelligkeit ihrer Vertraege mal die erste und mal die letzte Section, und
 * die Gliederung waere nicht wiedererkennbar. "Ohne Person" steht immer
 * zuletzt - es ist eine offene Zuordnung, keine Person.
 *
 * Ein `person_id`, zu dem der Server keine Person liefert, faellt ebenfalls
 * unter "Ohne Person" statt eine Section ohne Titel zu erzeugen. Das ist der
 * Grund, warum `listPeople` geloeschte Personen mitlaedt: sie behalten ihre
 * Vertraege (Soft-Delete) und sollen weiter mit ihrem Namen erscheinen.
 */
export function groupContractsByPerson(contracts: Contract[], people: Person[]): ContractSection[] {
  const orderById = new Map(people.map((person, index) => [person.id, index]));
  const nameById = new Map(people.map((person) => [person.id, person.name]));

  interface Bucket {
    title: string;
    order: number;
    data: Contract[];
  }
  const buckets = new Map<string, Bucket>();

  for (const contract of contracts) {
    const order = contract.person_id ? orderById.get(contract.person_id) : undefined;
    const key =
      contract.person_id && order !== undefined
        ? contract.person_id
        : UNASSIGNED_PERSON_SECTION_KEY;
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = {
        title:
          key === UNASSIGNED_PERSON_SECTION_KEY
            ? UNASSIGNED_PERSON_SECTION_LABEL
            : nameById.get(key)!,
        order: order ?? Number.POSITIVE_INFINITY,
        data: [],
      };
      buckets.set(key, bucket);
    }
    bucket.data.push(contract);
  }

  return Array.from(buckets.entries())
    .sort(([keyA, a], [keyB, b]) => {
      if (keyA === UNASSIGNED_PERSON_SECTION_KEY) return 1;
      if (keyB === UNASSIGNED_PERSON_SECTION_KEY) return -1;
      return a.order - b.order;
    })
    .map(([key, bucket]) => ({
      key,
      title: bucket.title,
      data: [...bucket.data].sort(compareContractsWithinSection),
    }));
}

export function useContracts(options: { includeEnded?: boolean } = {}) {
  const { accessToken, householdId, enabled } = useContractContext();
  const includeEnded = options.includeEnded ?? false;
  return useQuery<Contract[]>({
    queryKey: ["contracts", householdId, includeEnded],
    queryFn: ({ signal }) => listContracts(accessToken, householdId, signal, { includeEnded }),
    enabled,
  });
}

export function useContractSummary() {
  const { accessToken, householdId, enabled } = useContractContext();
  return useQuery<ContractSummary>({
    queryKey: ["contract-summary", householdId],
    queryFn: ({ signal }) => getContractSummary(accessToken, householdId, signal),
    enabled,
  });
}

/** Erwartete Faelligkeiten - ersetzt die frueher aus zwei Listen
 * zusammengebaute Kachel "Naechste Abbuchungen". */
export function useContractForecast(days = 30) {
  const { accessToken, householdId, enabled } = useContractContext();
  return useQuery<ContractUpcoming[]>({
    queryKey: ["contract-forecast", householdId, days],
    queryFn: ({ signal }) => getContractForecast(accessToken, householdId, signal, days),
    enabled,
  });
}

export function useContractHistory(contractId: string | null) {
  const { accessToken, householdId, enabled } = useContractContext();
  return useQuery<ContractAmountEntry[]>({
    queryKey: ["contract-history", householdId, contractId],
    queryFn: ({ signal }) =>
      listContractHistory(accessToken, householdId, contractId as string, signal),
    enabled: enabled && Boolean(contractId),
  });
}

export function useCreateContract() {
  const { accessToken, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<Contract, Error, ContractCreate>({
    mutationFn: (input) => createContract(accessToken, householdId, input),
    onSuccess: invalidate,
  });
}

export function useUpdateContract() {
  const { accessToken, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<
    Contract,
    Error,
    { contractId: string; version: number; patch: ContractUpdate }
  >({
    mutationFn: ({ contractId, version, patch }) =>
      updateContract(accessToken, householdId, contractId, patch, version),
    onSuccess: invalidate,
  });
}

export function useSetContractStatus() {
  const { accessToken, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<Contract, Error, { contractId: string; input: ContractStatusChange }>({
    mutationFn: ({ contractId, input }) =>
      setContractStatus(accessToken, householdId, contractId, input),
    onSuccess: invalidate,
  });
}

export function useDeleteContract() {
  const { accessToken, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<void, Error, string>({
    mutationFn: (contractId) => deleteContract(accessToken, householdId, contractId),
    onSuccess: invalidate,
  });
}
