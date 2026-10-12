import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  acceptReviewProposal,
  acknowledgeContractEvent,
  addContractAmount,
  applyContractMerge,
  applyContractReassign,
  checkContractDuplicates,
  checkContractTransactionsWithAI,
  createContract,
  createContractScenario,
  crosscheckContracts,
  deleteContract,
  deleteContractScenario,
  getContractAmountEvidence,
  getContractCashflow,
  getContractCostTarget,
  getContractDuplicateSuggestions,
  getContractForecast,
  getContractFreeTransactions,
  getContractMergeUndoPreview,
  getContractProviderSuggestions,
  getContractSettings,
  getContractSummary,
  listContractEvents,
  listContractHistory,
  listContractOccurrences,
  listContractScenarios,
  listContracts,
  previewContractAssignment,
  previewContractMerge,
  previewContractReassign,
  recalculateContractAmountHistory,
  rescanContracts,
  setContractStatus,
  undoContractMerge,
  updateContract,
  updateContractCostTarget,
  updateContractScenario,
  updateContractSettings,
} from "../lib/api";
import type {
  CashflowGranularity,
  Contract,
  ContractAICrosscheckResponse,
  ContractAITransactionCheckResponse,
  ContractAmountEntry,
  ContractAmountEvidenceResponse,
  ContractAmountRecalculationResult,
  ContractAssignmentPreview,
  ContractCashflowResponse,
  ContractCostTarget,
  ContractCostTargetInput,
  ContractCreateInput,
  ContractDuplicateCandidate,
  ContractDuplicateSuggestionsResponse,
  ContractEvent,
  ContractFreeTransactionsResponse,
  ContractFrequency,
  ContractMergePreview,
  ContractMergeResult,
  ContractMergeUndoPreview,
  ContractMergeUndoResult,
  ContractOccurrence,
  ContractProviderSuggestion,
  ContractReassignPreview,
  ContractReassignResult,
  ContractRescanResult,
  ContractScenario,
  ContractScenarioCreateInput,
  ContractScenarioUpdateInput,
  ContractSettings,
  ContractStatusChangeInput,
  ContractSummary,
  ContractType,
  ContractUpcoming,
  ContractUpdateInput,
} from "../lib/types";

/**
 * Datenzugriff der Vertragsverwaltung (docs/VERTRAEGE-FIXKOSTEN-PLAN.md).
 *
 * Drei Caches, bewusst getrennt:
 * - `["contracts", householdId]` die Liste,
 * - `["contract-summary", householdId]` die Kopfkennzahlen,
 * - je Vertrag Verlauf, Faelligkeiten und Ereignisse.
 *
 * Jede Mutation invalidiert alle drei plus Budget und Empfehlungen: eine
 * Betragsaenderung oder ein Vertragsende verschiebt die Fixkostensumme sofort.
 */

export const contractsQueryKey = (householdId: string) => ["contracts", householdId] as const;
export const contractSummaryQueryKey = (householdId: string) =>
  ["contract-summary", householdId] as const;
export const contractSettingsQueryKey = (householdId: string) =>
  ["contract-settings", householdId] as const;
export const contractCostTargetQueryKey = (householdId: string) =>
  ["contract-cost-target", householdId] as const;
export const contractScenariosQueryKey = (householdId: string) =>
  ["contract-scenarios", householdId] as const;
/**
 * Schluesselbewusste Duplikatvorschlaege (Abschnitt 19.8/19.10).
 *
 * Steht bewusst hier oben bei den uebrigen Schluesseln und nicht erst bei
 * `useContractDuplicateSuggestions` weiter unten: `useInvalidateContracts`
 * braucht ihn, und ein Schluessel, der nur an seiner Abfrage haengt, wird beim
 * Invalidieren zuverlaessig vergessen - genau die Luecke aus 19.14 Punkt 9.
 */
export const contractDuplicateSuggestionsQueryKey = (householdId: string) =>
  ["contract-duplicate-suggestions", householdId] as const;

/**
 * Beschriftung der Vertragsarten - hier und nicht in einer der Seiten, weil
 * inzwischen drei Oberflaechen dieselben zwoelf Schluessel benennen muessen
 * (Vertragsliste, Fixkostenbericht, Prognosekarte). Zwei Kopien wuerden
 * auseinanderlaufen, sobald eine Vertragsart dazukommt.
 */
export const CONTRACT_TYPE_LABEL: Record<ContractType, string> = {
  subscription: "Abonnement",
  insurance: "Versicherung",
  rent: "Miete & Wohnen",
  loan: "Kredit",
  installment: "Ratenkauf",
  utility: "Energie",
  telecom: "Telekommunikation",
  membership: "Mitgliedschaft",
  donation: "Spende",
  savings: "Sparplan",
  income: "Einnahme",
  other: "Sonstiges",
};

export function contractTypeLabel(contractType: string): string {
  return CONTRACT_TYPE_LABEL[contractType as ContractType] ?? contractType;
}

const PERIODS_PER_YEAR: Record<ContractFrequency, number> = {
  weekly: 52,
  monthly: 12,
  quarterly: 4,
  semiannual: 2,
  yearly: 1,
};

/**
 * Auf einen Monat normierter Betrag.
 *
 * Hier und nicht in der Seite, weil inzwischen zwei Oberflaechen normieren
 * (Vertragsliste und Zielrechner) und der Server mit exakt dieser Rechnung
 * arbeitet (`contract_service.monthly_minor`). Eine zweite Kopie waere die
 * naechste Quelle fuer Abweichungen gegenueber der Fixkostensumme.
 */
export function monthlyMinorOf(amountMinor: number, frequency: ContractFrequency): number {
  return Math.round((amountMinor * PERIODS_PER_YEAR[frequency]) / 12);
}

export function yearlyMinorOf(amountMinor: number, frequency: ContractFrequency): number {
  return Math.round(amountMinor * PERIODS_PER_YEAR[frequency]);
}

export type FixedCostsTone = "positive" | "highlight" | "warning" | "neutral";

export interface FixedCostsRating {
  tone: FixedCostsTone;
  label: string;
  hint: string;
}

/** Schwellen der Fixkostenampel (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, I7). */
export const FIXED_COSTS_TENSE_PERCENT = 50;
export const FIXED_COSTS_CRITICAL_PERCENT = 70;

/**
 * Ampel zur Fixkostenquote.
 *
 * Die beiden Sonderfaelle sind der eigentliche Grund fuer diese Funktion: das
 * Backend liefert 0 %, wenn es keine Bezugsgroesse gibt (weder gebuchte
 * Einnahmen noch Einnahmevertraege). Gruen waere dort die gefaehrlichste
 * Antwort - sie behauptet Entwarnung, wo schlicht nichts bekannt ist. Deshalb
 * entscheidet nicht die Quote allein, sondern zusammen mit der Frage, ob
 * ueberhaupt Fixkosten erfasst sind.
 */
export function rateFixedCostsRatio(
  ratioPercent: number,
  fixedCostsMonthlyMinor: number,
): FixedCostsRating {
  if (fixedCostsMonthlyMinor === 0) {
    return {
      tone: "neutral",
      label: "Keine Verträge",
      hint: "Noch keine laufenden Verträge erfasst.",
    };
  }
  if (ratioPercent <= 0) {
    return {
      tone: "neutral",
      label: "Ohne Einnahmen",
      hint: "Ohne gebuchte Einnahmen oder Einnahmevertrag lässt sich keine Quote bilden.",
    };
  }
  if (ratioPercent < FIXED_COSTS_TENSE_PERCENT) {
    return { tone: "positive", label: "Gut", hint: "Unter 50 % der Einnahmen." };
  }
  if (ratioPercent <= FIXED_COSTS_CRITICAL_PERCENT) {
    return { tone: "highlight", label: "Angespannt", hint: "50 bis 70 % der Einnahmen." };
  }
  return { tone: "warning", label: "Kritisch", hint: "Über 70 % der Einnahmen." };
}

interface ContractContext {
  token: string;
  householdId: string;
  enabled: boolean;
  role: string | null;
  canEdit: boolean;
}

export function useContractContext(): ContractContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const role = selectedHousehold?.role ?? null;
  return {
    token: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
    role,
    // Viewer und Child lesen nur; sie bekommen gar keine Aktionsschaltflaechen
    // statt funktionsloser deaktivierter.
    canEdit: role === "owner" || role === "editor",
  };
}

export function useInvalidateContracts() {
  const queryClient = useQueryClient();
  const { householdId } = useContractContext();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: contractsQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: contractSummaryQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: ["contract-forecast", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["contract-cashflow", householdId] });
    // Ziel und Szenarien rechnen gegen die Fixkostensumme: eine
    // Betragsaenderung oder ein Vertragsende verschiebt Luecke und Ersparnis
    // sofort, auch ohne dass jemand am Ziel selbst etwas geaendert haette.
    void queryClient.invalidateQueries({ queryKey: contractCostTargetQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: contractScenariosQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: ["contract-history", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["contract-occurrences", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["contract-events", householdId] });
    // Belege je Preisstufe und die Liste der noch freien Buchungen (Abschnitt
    // 19.7). Beide haengen unmittelbar am Zuordnen: eine nachgetragene Buchung
    // ist danach nicht mehr frei und stuetzt dafuer eine Preisstufe. Ohne diese
    // beiden Zeilen zeigte der Betragsverlauf weiter "kein Beleg", waehrend die
    // Buchung schon am Vertrag hing - und die Auswahl bot dieselbe Buchung ein
    // zweites Mal an.
    void queryClient.invalidateQueries({ queryKey: ["contract-amount-evidence", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["contract-free-transactions", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["budget", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["insights", householdId] });
    // Uebersicht und Fixkostenbericht tragen seit Phase 4 Vertragszahlen
    // (Fixkostenquote, "Verfuegbar nach Fixkosten", Zeitreihe). Ohne diese
    // beiden Zeilen zeigte die Uebersicht nach dem Anlegen eines Vertrags
    // weiter die alte Quote.
    void queryClient.invalidateQueries({ queryKey: ["overview-report", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["fixed-costs-trend", householdId] });
    // Die Review-Inbox fragt bei mehrfach ausgebliebener Zahlung "laeuft der
    // noch?". Jede Statusaenderung und jeder Abgleichslauf beantwortet diese
    // Frage moeglicherweise schon - ohne diese Invalidierung stuende die
    // erledigte Frage dort weiter.
    void queryClient.invalidateQueries({ queryKey: ["review-inbox", householdId] });
    // Duplikatvorschlaege und Undo-Vorschau (Abschnitt 19.10, Planforderung
    // "Merge und Undo invalidieren sowohl Vertrags- als auch
    // Vorschlags-Queries"). Ohne diese beiden Zeilen stand die Duplikatkarte
    // nach dem Zusammenfuehren weiter mit dem inzwischen ausgeblendeten
    // Quellvertrag da und bot ein zweites Mal denselben Merge an - und die
    // Undo-Vorschau meldete nach dem Rueckgaengigmachen weiter `can_undo`.
    void queryClient.invalidateQueries({
      queryKey: contractDuplicateSuggestionsQueryKey(householdId),
    });
    void queryClient.invalidateQueries({
      queryKey: ["contract-merge-undo-preview", householdId],
    });
  }, [queryClient, householdId]);
}

export function useContractList(options: { includeEnded?: boolean } = {}) {
  const { token, householdId, enabled } = useContractContext();
  const includeEnded = options.includeEnded ?? false;
  return useQuery<Contract[]>({
    queryKey: [...contractsQueryKey(householdId), { includeEnded }],
    queryFn: () => listContracts(token, householdId, { includeEnded }),
    enabled,
  });
}

export function useContractSummary() {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractSummary>({
    queryKey: contractSummaryQueryKey(householdId),
    queryFn: () => getContractSummary(token, householdId),
    enabled,
  });
}

/**
 * Erwartete Faelligkeiten der naechsten `days` Tage.
 *
 * Der Endpunkt existiert seit Phase 1 und wird von Mobile genutzt; im Web war
 * er bis Phase 4 ungenutzt. Er liefert die Faelligkeiten einzeln - die
 * laufende Summe daraus bildet die Oberflaeche, damit sichtbar wird, an
 * welchem Tag es eng wird.
 */
export function useContractForecast(days = 30) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractUpcoming[]>({
    queryKey: ["contract-forecast", householdId, days],
    queryFn: () => getContractForecast(token, householdId, days),
    enabled,
  });
}

/**
 * Vertrags-Cashflow je Kalendermonat beziehungsweise -quartal.
 *
 * Ersetzt `useContractForecast` als Grundlage der Prognosekarte. Der
 * Unterschied ist nicht kosmetisch: im rollierenden 30-Tage-Fenster fiel ein
 * monatlicher Vertrag je nach Faelligkeitstag ein- oder zweimal, im
 * 90-Tage-Fenster drei- oder viermal. Im Kalenderraster deckt sich der erste
 * Eimer mit der Fixkostensumme in derselben Ansicht.
 *
 * `useContractForecast` bleibt bestehen - Mobile zeigt damit die naechsten
 * Faelligkeiten als flache Liste, und dafuer ist ein Tagesfenster richtig.
 */
export function useContractCashflow(granularity: CashflowGranularity = "month", periods = 6) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractCashflowResponse>({
    queryKey: ["contract-cashflow", householdId, granularity, periods],
    queryFn: () => getContractCashflow(token, householdId, granularity, periods),
    enabled,
  });
}

export function useContractCostTarget() {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractCostTarget>({
    queryKey: contractCostTargetQueryKey(householdId),
    queryFn: () => getContractCostTarget(token, householdId),
    enabled,
  });
}

export function useContractScenarios() {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractScenario[]>({
    queryKey: contractScenariosQueryKey(householdId),
    queryFn: () => listContractScenarios(token, householdId),
    enabled,
  });
}

/**
 * Invalidiert alles, was am Fixkostenziel haengt.
 *
 * Bewusst NICHT `useInvalidateContracts`: ein Szenario aendert keinen Vertrag.
 * Wuerde es die Vertrags- und Budgetcaches mitleeren, entstuende der Eindruck,
 * die geplante Ersparnis sei bereits eingetreten - genau die Verwechslung, die
 * das Datenmodell vermeidet.
 */
function useInvalidateTarget() {
  const queryClient = useQueryClient();
  const { householdId } = useContractContext();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: contractCostTargetQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: contractScenariosQueryKey(householdId) });
  }, [queryClient, householdId]);
}

export function useUpdateContractCostTarget() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateTarget();
  return useMutation<ContractCostTarget, Error, ContractCostTargetInput>({
    mutationFn: (input) => updateContractCostTarget(token, householdId, input),
    onSuccess: invalidate,
  });
}

export function useCreateContractScenario() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateTarget();
  return useMutation<ContractScenario, Error, ContractScenarioCreateInput>({
    mutationFn: (input) => createContractScenario(token, householdId, input),
    onSuccess: invalidate,
  });
}

export function useUpdateContractScenario() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateTarget();
  return useMutation<
    ContractScenario,
    Error,
    { scenarioId: string; input: ContractScenarioUpdateInput }
  >({
    mutationFn: ({ scenarioId, input }) =>
      updateContractScenario(token, householdId, scenarioId, input),
    onSuccess: invalidate,
  });
}

export function useDeleteContractScenario() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateTarget();
  return useMutation<void, Error, string>({
    mutationFn: (scenarioId) => deleteContractScenario(token, householdId, scenarioId),
    onSuccess: invalidate,
  });
}

/**
 * Anbietervorschlaege aus dem globalen Katalog.
 *
 * Bewusst NICHT bei jedem Tastendruck: `query` wird von der Oberflaeche erst
 * beim Verlassen des Feldes gesetzt (gleicher Ausloeser wie die
 * Dublettenpruefung). Der Katalog ist global und aendert sich nicht waehrend
 * einer Sitzung, deshalb eine grosszuegige staleTime statt einer Entprellung.
 */
export function useContractProviderSuggestions(query: string) {
  const { token, householdId, enabled } = useContractContext();
  const trimmed = query.trim();
  return useQuery<ContractProviderSuggestion[]>({
    queryKey: ["contract-provider-suggestions", householdId, trimmed.toLowerCase()],
    queryFn: () => getContractProviderSuggestions(token, householdId, trimmed),
    // Ein Zeichen traefe halbe Anbieterlisten; der Endpunkt lehnt leer ab.
    enabled: enabled && trimmed.length >= 2,
    staleTime: 5 * 60 * 1000,
  });
}

export function useContractHistory(contractId: string | null) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractAmountEntry[]>({
    queryKey: ["contract-history", householdId, contractId],
    queryFn: () => listContractHistory(token, householdId, contractId as string),
    enabled: enabled && Boolean(contractId),
  });
}

export function useContractOccurrences(contractId: string | null) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractOccurrence[]>({
    queryKey: ["contract-occurrences", householdId, contractId],
    queryFn: () => listContractOccurrences(token, householdId, contractId as string),
    enabled: enabled && Boolean(contractId),
  });
}

export function useContractEvents(contractId: string | null) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractEvent[]>({
    queryKey: ["contract-events", householdId, contractId],
    queryFn: () => listContractEvents(token, householdId, contractId as string),
    enabled: enabled && Boolean(contractId),
  });
}

export function useContractSettings() {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractSettings>({
    queryKey: contractSettingsQueryKey(householdId),
    queryFn: () => getContractSettings(token, householdId),
    enabled,
  });
}

export function useCreateContract() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<Contract, Error, ContractCreateInput>({
    mutationFn: (input) => createContract(token, householdId, input),
    onSuccess: invalidate,
  });
}

export function useUpdateContract() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<
    Contract,
    Error,
    { contractId: string; version: number; input: ContractUpdateInput }
  >({
    mutationFn: ({ contractId, version, input }) =>
      updateContract(token, householdId, contractId, input, version),
    onSuccess: invalidate,
  });
}

export function useDeleteContract() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<void, Error, string>({
    mutationFn: (contractId) => deleteContract(token, householdId, contractId),
    onSuccess: invalidate,
  });
}

export function useSetContractStatus() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<Contract, Error, { contractId: string; input: ContractStatusChangeInput }>({
    mutationFn: ({ contractId, input }) => setContractStatus(token, householdId, contractId, input),
    onSuccess: invalidate,
  });
}

export function useAddContractAmount() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<
    Contract,
    Error,
    { contractId: string; amount_minor: number; valid_from: string }
  >({
    mutationFn: ({ contractId, ...input }) =>
      addContractAmount(token, householdId, contractId, input),
    onSuccess: invalidate,
  });
}

/**
 * Manueller Abgleich mit den Buchungen.
 *
 * Bewusst ohne `retry`: der Lauf ist zwar idempotent, kann aber je nach
 * Vertragszahl spuerbar dauern - ein stiller Wiederholungsversuch wuerde die
 * Wartezeit verdoppeln, ohne dass der Nutzer erfaehrt warum.
 */
export function useRescanContracts() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<ContractRescanResult, Error, void>({
    mutationFn: () => rescanContracts(token, householdId),
    retry: false,
    onSuccess: invalidate,
  });
}

export function useAcknowledgeContractEvent() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<void, Error, string>({
    mutationFn: (eventId) => acknowledgeContractEvent(token, householdId, eventId),
    onSuccess: invalidate,
  });
}

/**
 * Vorschau bewusst als Query, nicht als Mutation - gleiches Muster wie beim
 * Kategorien-Merge: sie laeuft beim Oeffnen des Dialogs, laesst sich nach einem
 * Konflikt gezielt erneuern und aendert nie den Zustand waehrend des Renderns.
 */
const PREVIEW_QUERY_OPTIONS = { gcTime: 0, staleTime: 0, retry: false } as const;

export function useContractMergePreview(targetId: string | null, sourceIds: string[]) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractMergePreview>({
    queryKey: ["contract-merge-preview", householdId, targetId, [...sourceIds].sort()],
    queryFn: () =>
      previewContractMerge(token, householdId, {
        target_id: targetId as string,
        source_ids: sourceIds,
      }),
    enabled: enabled && Boolean(targetId) && sourceIds.length > 0,
    ...PREVIEW_QUERY_OPTIONS,
  });
}

export function useApplyContractMerge() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<
    ContractMergeResult,
    Error,
    { target_id: string; source_ids: string[]; request_id: string }
  >({
    mutationFn: (input) => applyContractMerge(token, householdId, input),
    onSuccess: invalidate,
  });
}

/**
 * Sicherheitsleine fuer eine Zusammenfuehrung (Abschnitt 19.7): sofort nach
 * dem Merge und spaeter aus dem Ereignisverlauf erreichbar.
 */
export function useContractMergeUndoPreview(mergeId: string | null) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractMergeUndoPreview>({
    queryKey: ["contract-merge-undo-preview", householdId, mergeId],
    queryFn: () => getContractMergeUndoPreview(token, householdId, mergeId as string),
    enabled: enabled && Boolean(mergeId),
    ...PREVIEW_QUERY_OPTIONS,
  });
}

export function useUndoContractMerge() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<ContractMergeUndoResult, Error, string>({
    mutationFn: (mergeId) => undoContractMerge(token, householdId, mergeId),
    onSuccess: invalidate,
  });
}

/** Karte auf der Vertraege-Seite; Schluessel siehe oben. */
export function useContractDuplicateSuggestions() {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractDuplicateSuggestionsResponse>({
    queryKey: contractDuplicateSuggestionsQueryKey(householdId),
    queryFn: () => getContractDuplicateSuggestions(token, householdId),
    enabled,
  });
}

/**
 * "Gehören nicht zusammen": merkt die Verneinung dauerhaft (contract_events,
 * kind='dismissed'), ueber denselben Endpunkt wie die Review-Inbox.
 *
 * Invalidiert seit 19.10 nicht mehr nur Vorschlaege und Inbox, sondern alles:
 * die Verneinung schreibt ein Vertragsereignis, das im Ereignisverlauf des
 * Vertrags stehen muss, und die Gegenpruefung schliesst verneinte Paare
 * serverseitig aus - beide Ansichten waeren sonst aelter als die Datenlage.
 */
export function useDismissContractDuplicate() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<void, Error, { targetId: string; sourceIds: string[] }>({
    mutationFn: ({ targetId, sourceIds }) =>
      acceptReviewProposal(token, householdId, {
        type: "contract_duplicate_suggestion",
        target_id: targetId,
        source_ids: sourceIds,
        dismiss: true,
      }),
    onSuccess: invalidate,
  });
}

/**
 * Gegenpruefung durch das lokale Sprachmodell (Abschnitt 19.11).
 *
 * Bewusst eine Mutation, obwohl nichts geschrieben wird: der Lauf ist teuer
 * (mehrere Modellaufrufe nacheinander im Request), soll ausschliesslich auf
 * Knopfdruck starten und niemals im Hintergrund erneuert werden. Als Query
 * wuerde React Query ihn beim Fensterwechsel wiederholen.
 *
 * `retry: false` aus demselben Grund wie beim Abgleich: ein stiller zweiter
 * Versuch verdoppelte die Wartezeit, ohne dass jemand erfaehrt warum.
 *
 * Das Ergebnis wird nicht zwischengespeichert - es ist ein Pruefvorschlag zu
 * einem Zeitpunkt, kein Zustand. Invalidiert werden trotzdem die
 * Vertrags-Caches: die Vorfilterung liest denselben Bestand, den die Seite
 * anzeigt, und ein Lauf ist der Moment, in dem der Nutzer beides vergleicht.
 */
export function useCrosscheckContracts() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<ContractAICrosscheckResponse, Error, { contractIds?: string[] } | void>({
    mutationFn: (input) => crosscheckContracts(token, householdId, input?.contractIds),
    retry: false,
    onSuccess: invalidate,
  });
}

export function useContractReassignPreview(
  transactionId: string | null,
  targetContractId: string | null,
) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractReassignPreview>({
    queryKey: ["contract-reassign-preview", householdId, transactionId, targetContractId],
    queryFn: () =>
      previewContractReassign(token, householdId, transactionId as string, targetContractId),
    enabled: enabled && Boolean(transactionId),
    ...PREVIEW_QUERY_OPTIONS,
  });
}

export function useApplyContractReassign() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<
    ContractReassignResult,
    Error,
    { transactionId: string; targetContractId: string | null }
  >({
    mutationFn: ({ transactionId, targetContractId }) =>
      applyContractReassign(token, householdId, transactionId, targetContractId),
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------------------
// Belege, freie Buchungen, Nachtragen (Abschnitt 19.7/19.11)
// ---------------------------------------------------------------------------

/**
 * Belege je Preisstufe.
 *
 * Eigener Cache statt einer Erweiterung von `useContractHistory`: der Verlauf
 * allein wird an mehreren Stellen gebraucht und ist billig, die Belege sind es
 * nicht (je Stufe eine Buchungsabfrage). `useInvalidateContracts` raeumt beide.
 */
export function useContractAmountEvidence(contractId: string | null) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractAmountEvidenceResponse>({
    queryKey: ["contract-amount-evidence", householdId, contractId],
    queryFn: () => getContractAmountEvidence(token, householdId, contractId as string),
    enabled: enabled && Boolean(contractId),
  });
}

/**
 * Suchparameter der freien Buchungen.
 *
 * `null` heisst durchgaengig "nicht gesetzt, Vorbelegung des Servers gilt" -
 * nicht "leer". Deshalb ist `search: null` etwas anderes als `search: ""`, und
 * `useContractDefaults: false` ist der ausdrueckliche Verzicht auf die
 * Vorbelegung, nicht ihr Nebeneffekt.
 */
export interface ContractFreeTransactionQuery {
  dateFrom: string | null;
  dateTo: string | null;
  amountMinMinor: number | null;
  amountMaxMinor: number | null;
  search: string | null;
  useContractDefaults: boolean;
  limit: number;
  offset: number;
}

export const FREE_TRANSACTIONS_PAGE_SIZE = 25;

export const EMPTY_FREE_TRANSACTION_QUERY: ContractFreeTransactionQuery = {
  dateFrom: null,
  dateTo: null,
  amountMinMinor: null,
  amountMaxMinor: null,
  search: null,
  useContractDefaults: true,
  limit: FREE_TRANSACTIONS_PAGE_SIZE,
  offset: 0,
};

/**
 * Noch keinem Vertrag zugeordnete Buchungen, gefiltert.
 *
 * `enabled` liegt beim Aufrufer: die Abfrage laeuft erst, wenn der Nutzer den
 * Bereich "Fehlende Buchungen nachtragen" wirklich geoeffnet hat. Sonst zoege
 * jedes Oeffnen eines Vertragsdialogs eine Volltextsuche ueber den gesamten
 * Buchungsbestand nach sich.
 */
export function useContractFreeTransactions(
  contractId: string | null,
  query: ContractFreeTransactionQuery,
  options: { enabled?: boolean } = {},
) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractFreeTransactionsResponse>({
    queryKey: ["contract-free-transactions", householdId, contractId, query],
    queryFn: () =>
      getContractFreeTransactions(token, householdId, contractId as string, {
        dateFrom: query.dateFrom,
        dateTo: query.dateTo,
        amountMinMinor: query.amountMinMinor,
        amountMaxMinor: query.amountMaxMinor,
        search: query.search,
        useContractDefaults: query.useContractDefaults,
        limit: query.limit,
        offset: query.offset,
      }),
    enabled: enabled && Boolean(contractId) && (options.enabled ?? true),
  });
}

/**
 * Vorschau des Zuordnens - Query nach demselben Muster wie die Merge-Vorschau:
 * sie laeuft, sobald eine Auswahl zur Pruefung freigegeben ist, und wird nie
 * zwischengespeichert.
 */
export function useContractAssignmentPreview(contractId: string | null, transactionIds: string[]) {
  const { token, householdId, enabled } = useContractContext();
  return useQuery<ContractAssignmentPreview>({
    queryKey: ["contract-assignment-preview", householdId, contractId, [...transactionIds].sort()],
    queryFn: () =>
      previewContractAssignment(token, householdId, contractId as string, transactionIds),
    enabled: enabled && Boolean(contractId) && transactionIds.length > 0,
    ...PREVIEW_QUERY_OPTIONS,
  });
}

/**
 * Zuordnen und danach genau einmal neu rechnen.
 *
 * Bewusst EINE Mutation statt n+1 Aufrufen aus der Oberflaeche heraus. Zwei
 * Gruende: die Neuberechnung darf erst laufen, wenn alle Buchungen haengen -
 * sonst entstuende die Preistreppe schrittweise und der Nutzer saehe
 * Zwischenstaende, die es nie geben sollte. Und die Invalidierung passiert so
 * garantiert einmal am Ende statt nach jeder einzelnen Zuordnung.
 *
 * Nacheinander, nicht parallel: `reassign` schreibt am selben Vertrag: Zeitraum
 * und Faelligkeiten. Nebenlaeufige Schreibvorgaenge auf denselben Datensatz
 * waeren ein Versionskonflikt mit Ansage.
 */
export function useAssignTransactionsToContract() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<
    ContractAmountRecalculationResult,
    Error,
    { contractId: string; transactionIds: string[] }
  >({
    mutationFn: async ({ contractId, transactionIds }) => {
      for (const transactionId of transactionIds) {
        await applyContractReassign(token, householdId, transactionId, contractId);
      }
      return recalculateContractAmountHistory(token, householdId, contractId);
    },
    retry: false,
    onSuccess: invalidate,
  });
}

/**
 * Betragsverlauf ohne vorheriges Zuordnen neu rechnen.
 *
 * Der eigene Weg fuer den Fall aus 19.1: die Buchungen haengen laengst am
 * Vertrag, nur die Preisstufen stammen noch aus der Datenuebernahme. Dann ist
 * nichts nachzutragen, sondern nur nachzurechnen.
 */
export function useRecalculateContractAmountHistory() {
  const { token, householdId } = useContractContext();
  const invalidate = useInvalidateContracts();
  return useMutation<ContractAmountRecalculationResult, Error, string>({
    mutationFn: (contractId) => recalculateContractAmountHistory(token, householdId, contractId),
    retry: false,
    onSuccess: invalidate,
  });
}

/**
 * Buchungspruefung durch das lokale Sprachmodell (Abschnitt 19.11).
 *
 * Mutation aus denselben Gruenden wie `useCrosscheckContracts`: teuer, nur auf
 * Knopfdruck, niemals im Hintergrund erneuert. Der Lauf schreibt nichts -
 * deshalb wird hier auch NICHTS invalidiert; das Ergebnis ist ein Vorschlag,
 * den der Nutzer erst ueber den Zuordnen-Weg bestaetigt.
 */
export function useCheckContractTransactionsAI() {
  const { token, householdId } = useContractContext();
  return useMutation<
    ContractAITransactionCheckResponse,
    Error,
    { contractId: string; transactionIds?: string[] }
  >({
    mutationFn: ({ contractId, transactionIds }) =>
      checkContractTransactionsWithAI(token, householdId, contractId, transactionIds),
    retry: false,
  });
}

export function useContractDuplicateCheck() {
  const { token, householdId } = useContractContext();
  return useMutation<
    ContractDuplicateCandidate[],
    Error,
    { name: string; amount_minor?: number; frequency?: ContractFrequency; exclude_id?: string }
  >({
    mutationFn: (input) => checkContractDuplicates(token, householdId, input),
  });
}

export function useUpdateContractSettings() {
  const { token, householdId } = useContractContext();
  const queryClient = useQueryClient();
  return useMutation<ContractSettings, Error, ContractSettings>({
    mutationFn: (input) => updateContractSettings(token, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: contractSettingsQueryKey(householdId) });
    },
  });
}

/** Stabiler Idempotenzschluessel fuer das Zusammenfuehren. */
export function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
