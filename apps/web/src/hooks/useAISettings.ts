import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  getAIHealth,
  getAIModels,
  getAISettings,
  loadAIModel,
  removeAIProviderKey,
  storeAIProviderKey,
  updateAISettings,
} from "../lib/api";
import type {
  AIHealthResponse,
  AIKeyStatus,
  AIModelCatalog,
  AIModelLoadResult,
  AISettings,
  AISettingsUpdate,
  ProviderId,
} from "../lib/types";

/**
 * KI-Einstellungen eines Haushalts (Migration 0011).
 *
 * Vorher lagen LM-Studio-URL und Schwellenwert ausschliesslich im
 * `localStorage` des Browsers - der Server hat sie nie gesehen. Damit war die
 * Maske faktisch wirkungslos: der Hintergrundlauf im Worker und die Mobile-App
 * wussten nichts davon. Jetzt liegen sie serverseitig je Haushalt.
 */

export const aiSettingsQueryKey = (householdId: string) => ["ai-settings", householdId] as const;
export const aiModelsQueryKey = (householdId: string) => ["ai-models", householdId] as const;
export const aiHealthQueryKey = (householdId: string) => ["ai-health", householdId] as const;

function useAIContext() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  return {
    token: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
  };
}

export function useAISettings() {
  const { token, householdId, enabled } = useAIContext();
  return useQuery<AISettings>({
    queryKey: aiSettingsQueryKey(householdId),
    queryFn: () => getAISettings(token, householdId),
    enabled,
  });
}

/**
 * Modellliste. Kurze `staleTime`, aber kein Dauerpolling: in LM Studio wird
 * ein Modell selten waehrend der Bedienung gewechselt, und jeder Aufruf geht
 * ueber die API an einen lokalen Dienst.
 */
export function useAIModels(provider?: ProviderId) {
  const { token, householdId, enabled } = useAIContext();
  return useQuery<AIModelCatalog>({
    // Gleiches Praefix wie aiModelsQueryKey: Invalidierungen treffen jede Liste.
    queryKey: [...aiModelsQueryKey(householdId), provider ?? "gespeichert"],
    queryFn: () => getAIModels(token, householdId, provider),
    enabled,
    staleTime: 30 * 1000,
  });
}

/**
 * Status inklusive wirksamem Fenster, Antwortreserve und Messung. Haengt an
 * den Einstellungen: nach dem Speichern muss er neu gelesen werden, sonst
 * zeigt er noch Deckel und Reserve des alten Stands.
 *
 * Achtung: der Status fuehrt einen Vorabtest am Modell aus. Deshalb kein
 * Polling - der Laufzeitstreifen der Warteschlange liest /v1/ai/runtime.
 */
export function useAIHealth() {
  const { token, householdId, enabled } = useAIContext();
  return useQuery<AIHealthResponse>({
    queryKey: aiHealthQueryKey(householdId),
    queryFn: () => getAIHealth(token, householdId),
    enabled,
    staleTime: 30 * 1000,
  });
}

export function useUpdateAISettings() {
  const { token, householdId } = useAIContext();
  const queryClient = useQueryClient();
  return useMutation<AISettings, Error, AISettingsUpdate>({
    mutationFn: (input) => updateAISettings(token, householdId, input),
    onSuccess: (data) => {
      queryClient.setQueryData(aiSettingsQueryKey(householdId), data);
      // Der Status rechnet das Kontextbudget aus genau diesen Werten - ohne
      // Invalidierung stuende dort weiter die alte Warnung.
      void queryClient.invalidateQueries({ queryKey: aiHealthQueryKey(householdId) });
      void queryClient.invalidateQueries({ queryKey: aiModelsQueryKey(householdId) });
    },
  });
}

/**
 * Laedt das eingestellte Modell mit dem eingestellten Kontextfenster in LM
 * Studio.
 *
 * Bewusst eine eigene Aktion und kein Nebeneffekt des Speicherns: Laden dauert
 * gemessen 40 Sekunden und mehr und verdraengt die laufende Instanz. Wer nur
 * den Schwellenwert korrigiert, soll nicht ungefragt das Modell neu laden.
 */
export function useLoadAIModel() {
  const { token, householdId } = useAIContext();
  const queryClient = useQueryClient();
  return useMutation<AIModelLoadResult, Error, void>({
    mutationFn: () => loadAIModel(token, householdId),
    onSuccess: () => {
      // Ladezustand und Kontextfenster haben sich geaendert - beide Ansichten
      // lesen sie frisch, statt den alten Stand weiterzuzeigen.
      void queryClient.invalidateQueries({ queryKey: aiModelsQueryKey(householdId) });
      void queryClient.invalidateQueries({ queryKey: aiHealthQueryKey(householdId) });
    },
  });
}

/**
 * Neuer Schluesselstand: sofort in die Einstellungen uebernehmen (die Anzeige
 * soll nicht auf das Nachladen warten) und alles neu lesen, was davon
 * abhaengt - ohne Schluessel gibt es bei Claude weder Modellliste noch Status.
 */
function applyKeyStatus(queryClient: QueryClient, householdId: string, status: AIKeyStatus) {
  queryClient.setQueryData<AISettings>(aiSettingsQueryKey(householdId), (old) => {
    if (!old) return old;
    const others = (old.provider_keys ?? []).filter((entry) => entry.provider !== status.provider);
    return { ...old, provider_keys: [...others, status] };
  });
  void queryClient.invalidateQueries({ queryKey: aiSettingsQueryKey(householdId) });
  void queryClient.invalidateQueries({ queryKey: aiModelsQueryKey(householdId) });
  void queryClient.invalidateQueries({ queryKey: aiHealthQueryKey(householdId) });
}

/**
 * Hinterlegt den API-Schluessel eines Anbieters (nur der Owner). Eigene
 * Aktion, nicht Teil von "Speichern": der Schluessel gehoert nicht in den
 * Entwurf der Einstellungen.
 *
 * `gcTime: 0`: der Schluessel steht als Variable in der Mutation - er soll
 * nicht laenger im Cache liegen als noetig.
 */
export function useStoreAIProviderKey() {
  const { token, householdId } = useAIContext();
  const queryClient = useQueryClient();
  return useMutation<AIKeyStatus, Error, { provider: ProviderId; apiKey: string }>({
    mutationFn: ({ provider, apiKey }) => storeAIProviderKey(token, householdId, provider, apiKey),
    gcTime: 0,
    onSuccess: (status) => applyKeyStatus(queryClient, householdId, status),
  });
}

/** Entfernt den API-Schluessel eines Anbieters (nur der Owner). */
export function useRemoveAIProviderKey() {
  const { token, householdId } = useAIContext();
  const queryClient = useQueryClient();
  return useMutation<AIKeyStatus, Error, ProviderId>({
    mutationFn: (provider) => removeAIProviderKey(token, householdId, provider),
    onSuccess: (status) => applyKeyStatus(queryClient, householdId, status),
  });
}
