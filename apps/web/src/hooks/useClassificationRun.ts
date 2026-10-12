import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  cancelClassificationRun,
  createClassificationRun,
  getLatestClassificationRun,
} from "../lib/api";
import type { ClassificationRun } from "../lib/types";

/**
 * Zustand des KI-Laufs - haushaltsbezogen und ausschliesslich serverseitig
 * gehalten (siehe docs/KI-FINANZASSISTENT-PLAN.md, Ausbaustufe 0).
 *
 * Vorher lebte die Run-ID in einem `useState` der Review-Seite: der
 * Hintergrundjob lief nach einem Seitenwechsel zwar weiter, die Oberflaeche
 * zeigte aber wieder den Startknopf und ein zweiter Klick erzeugte einen
 * zweiten Lauf. Der Query-Key haengt nur am Haushalt, nicht an einer Run-ID -
 * jede Komponente, die diesen Hook nutzt, sieht denselben Lauf, und Reload
 * oder ein zweites Geraet finden ihn ueber GET /v1/ai/classify-runs?limit=1
 * wieder.
 */
export const classificationRunQueryKey = (householdId: string) =>
  ["classification-run-latest", householdId] as const;

const ACTIVE_POLL_INTERVAL_MS = 2000;

export function isClassificationRunActive(run: ClassificationRun | null | undefined): boolean {
  return run?.status === "pending" || run?.status === "running";
}

/** Eindeutiger Idempotenzschluessel; `crypto.randomUUID` fehlt in aelteren
 * Testumgebungen, deshalb mit Rueckfallebene. */
function newRequestId(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  return `run-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useLatestClassificationRun() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  return useQuery<ClassificationRun | null>({
    queryKey: classificationRunQueryKey(householdId),
    queryFn: () => getLatestClassificationRun(token!, householdId),
    enabled: Boolean(token && householdId),
    // Waehrend eines Laufs eng pollen, danach gar nicht - der abgeschlossene
    // Lauf aendert sich nicht mehr von allein.
    refetchInterval: (query) =>
      isClassificationRunActive(query.state.data) ? ACTIVE_POLL_INTERVAL_MS : false,
  });
}

export function useStartClassificationRun() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  return useMutation<ClassificationRun, Error, void>({
    mutationFn: () => createClassificationRun(token!, householdId, newRequestId()),
    // Die Antwort ist der maßgebliche Lauf - auch dann, wenn der Server statt
    // eines neuen Laufs den bereits aktiven zurueckgegeben hat (HTTP 200).
    onSuccess: (run) => {
      queryClient.setQueryData(classificationRunQueryKey(householdId), run);
    },
  });
}

export function useCancelClassificationRun() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  return useMutation<ClassificationRun, Error, string>({
    mutationFn: (runId: string) => cancelClassificationRun(token!, householdId, runId),
    onSuccess: (run) => {
      queryClient.setQueryData(classificationRunQueryKey(householdId), run);
    },
  });
}

/**
 * Laedt Review-Inbox und KI-Protokoll neu, sobald ein Lauf endet. Bewusst nur
 * an EINER Stelle einhaengen (Review-Seite), nicht in jeder Komponente, die
 * den Laufstatus anzeigt.
 */
export function useClassificationRunCompletionEffect(run: ClassificationRun | null | undefined) {
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const householdId = selectedHouseholdId ?? "";
  const lastHandled = useRef<string | null>(null);

  useEffect(() => {
    if (!run) return;
    const marker = `${run.id}:${run.status}`;
    if (marker === lastHandled.current) return;
    // Beim ersten Blick nichts nachladen: ein laengst beendeter Lauf ist kein
    // Ereignis, die Inbox wird beim Mount ohnehin geladen.
    const isFirstObservation = lastHandled.current === null;
    lastHandled.current = marker;
    if (!isFirstObservation && !isClassificationRunActive(run)) {
      void queryClient.invalidateQueries({ queryKey: ["ai-decisions", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["review-inbox", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["transactions", householdId] });
    }
  }, [run, householdId, queryClient]);
}
