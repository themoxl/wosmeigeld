import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelClassificationRun,
  classifyWithAI,
  createClassificationRun,
  getAIDecisions,
  getAIHealth,
  getClassificationRun,
  getLatestClassificationRun,
} from "../api/client";
import type {
  AIClassifyInput,
  AIClassifyResult,
  AIDecisionLog,
  AIHealthResponse,
  ClassificationRun,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/** LM Studio KI-Health (GET /v1/ai/health). */
export function useAIHealth() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<AIHealthResponse>({
    queryKey: ["aiHealth", selectedHouseholdId],
    queryFn: ({ signal }) =>
      getAIHealth(accessToken as string, selectedHouseholdId ?? undefined, signal),
    enabled: !!accessToken,
    refetchInterval: 30_000,
  });
}

/** KI-Klassifizierung ausfuehren (POST /v1/ai/classify). */
export function useClassifyWithAI() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation<AIClassifyResult, Error, AIClassifyInput>({
    mutationFn: (input: AIClassifyInput) =>
      classifyWithAI(accessToken as string, selectedHouseholdId as string, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["review", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
    },
  });
}

/** KI-Entscheidungsprotokoll (GET /v1/ai/decisions) - liefert Konfidenz-Badges
 * auch fuer Buchungen, die per Batch-Lauf statt per Einzel-Tap klassifiziert
 * wurden (siehe useClassificationRun unten). */
export function useAIDecisions() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<AIDecisionLog[]>({
    queryKey: ["aiDecisions", selectedHouseholdId],
    queryFn: ({ signal }) =>
      getAIDecisions(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/** Fortschritt eines einzelnen Laufs (GET /v1/ai/classify-runs/{id}) - fuer
 * gezielte Abfragen, etwa aus einer Laufhistorie. Der laufende Zustand kommt
 * dagegen aus useLatestClassificationRun() unten. */
export function useClassificationRun(runId: string | null) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<ClassificationRun>({
    queryKey: ["classificationRun", selectedHouseholdId, runId],
    queryFn: ({ signal }) =>
      getClassificationRun(
        accessToken as string,
        selectedHouseholdId as string,
        runId as string,
        signal,
      ),
    enabled: !!accessToken && !!selectedHouseholdId && !!runId,
    refetchInterval: (query) =>
      isClassificationRunActive(query.state.data) ? CLASSIFICATION_RUN_POLL_MS : false,
  });
}

export const CLASSIFICATION_RUN_POLL_MS = 2000;

export const classificationRunQueryKey = (householdId: string | null) =>
  ["classificationRunLatest", householdId] as const;

export function isClassificationRunActive(run: ClassificationRun | null | undefined): boolean {
  return run?.status === "pending" || run?.status === "running";
}

/**
 * Juengster KI-Lauf des Haushalts (GET /v1/ai/classify-runs?limit=1).
 *
 * Ausbaustufe 0 des KI-Plans: der Lauf laeuft im Worker weiter, waehrend die
 * App im Hintergrund ist oder neu startet. Frueher merkte sich nur der
 * Review-Screen die Run-ID in `useState` - nach einem Neustart war der
 * laufende Lauf unauffindbar und ein zweiter Start erzeugte einen zweiten Job.
 */
export function useLatestClassificationRun() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<ClassificationRun | null>({
    queryKey: classificationRunQueryKey(selectedHouseholdId),
    queryFn: ({ signal }) =>
      getLatestClassificationRun(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
    refetchInterval: (query) =>
      isClassificationRunActive(query.state.data) ? CLASSIFICATION_RUN_POLL_MS : false,
  });
}

/** Eindeutiger Idempotenzschluessel fuer den Start; ohne Web-Crypto-Abhaengigkeit,
 * weil `crypto.randomUUID` in React Native nicht ueberall verfuegbar ist. */
function newRequestId(): string {
  return `run-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Startet einen Batch-Klassifikationslauf (POST /v1/ai/classify-runs).
 * Laeuft bereits einer, liefert der Server ihn zurueck, statt einen zweiten
 * zu starten - die Antwort ist also immer der massgebliche Lauf. */
export function useStartClassificationRun() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation<ClassificationRun, Error, void>({
    mutationFn: () =>
      createClassificationRun(accessToken as string, selectedHouseholdId as string, newRequestId()),
    onSuccess: (run) => {
      queryClient.setQueryData(classificationRunQueryKey(selectedHouseholdId), run);
    },
  });
}

/** Bricht einen Lauf ab (POST /v1/ai/classify-runs/{id}/cancel). */
export function useCancelClassificationRun() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation<ClassificationRun, Error, string>({
    mutationFn: (runId: string) =>
      cancelClassificationRun(accessToken as string, selectedHouseholdId as string, runId),
    onSuccess: (run) => {
      queryClient.setQueryData(classificationRunQueryKey(selectedHouseholdId), run);
    },
  });
}
