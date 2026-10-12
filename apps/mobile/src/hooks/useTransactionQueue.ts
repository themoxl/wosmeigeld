import { useEffect, useSyncExternalStore } from "react";
import { AppState, type AppStateStatus } from "react-native";
import { useAuth } from "../auth/AuthProvider";
import { configureQueueProcessor, flush } from "../offline/queueProcessor";
import { nextAttemptDueAt } from "../offline/queueReducer";
import { clearQueue, getQueueSnapshot, subscribeQueue } from "../offline/queueStore";
import type { Queue } from "../offline/queueTypes";
import { useConnectivity } from "./useConnectivity";

/** Taktrate des Retry-Timers - der eigentliche Abstand kommt aus dem Backoff. */
const RETRY_TICK_MS = 10_000;

/**
 * Phase 9 (Offline-Write-Queue): genau EINMAL nahe der Wurzel montieren
 * (siehe app/_layout.tsx). Verkabelt den Queue-Processor mit dem jeweils
 * aktuellen Access-Token und stoesst einen Flush an bei
 *   - Netzwerk-Reconnect,
 *   - Rueckkehr der App in den Vordergrund,
 *   - und, solange noch etwas wartet, periodisch (Backoff-Faelligkeit).
 * Liefert den aktuellen Queue-Stand zurueck.
 */
export function useTransactionQueue(): Queue {
  const { accessToken, status: authStatus } = useAuth();
  const isConnected = useConnectivity();
  const queue = useSyncExternalStore(subscribeQueue, getQueueSnapshot, getQueueSnapshot);

  // Bei jeder Token-Aenderung (z. B. Refresh durch Supabase) neu verkabeln,
  // statt den Wert waehrend des Renderns aus einer Ref zu lesen - die in
  // diesem Projekt aktive react-hooks-Regel verbietet Ref-Zugriffe ausserhalb
  // von Effekten/Handlern.
  useEffect(() => {
    configureQueueProcessor(() => accessToken);
  }, [accessToken]);

  // Mandantentrennung: nach dem Abmelden duerfen keine Schreibvorgaenge des
  // vorherigen Nutzers auf dem Geraet liegen bleiben. Sie mit dem naechsten
  // Token eines anderen Nutzers zu senden waere schlimmer als sie zu
  // verlieren.
  useEffect(() => {
    if (authStatus === "signedOut") void clearQueue();
  }, [authStatus]);

  useEffect(() => {
    if (isConnected) void flush();
  }, [isConnected]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state: AppStateStatus) => {
      if (state === "active") void flush();
    });
    return () => subscription.remove();
  }, []);

  // Nur takten, wenn ueberhaupt etwas wartet - ein leerer Timer im
  // Hintergrund kostet sonst dauerhaft Akku.
  const hasWaitingEntries = nextAttemptDueAt(queue) !== null;
  useEffect(() => {
    if (!hasWaitingEntries) return;
    const interval = setInterval(() => void flush(), RETRY_TICK_MS);
    return () => clearInterval(interval);
  }, [hasWaitingEntries]);

  return queue;
}

/**
 * Nur-Lese-Zugriff auf den Queue-Stand (z. B. fuer einen Konflikt-Badge),
 * ohne den Processor erneut zu verkabeln. useTransactionQueue() darf nur
 * einmal gemountet sein, sonst laufen Timer und Listener doppelt.
 */
export function useQueueEntries(): Queue {
  return useSyncExternalStore(subscribeQueue, getQueueSnapshot, getQueueSnapshot);
}

/** Eintraege, die auf eine Entscheidung der Nutzerin warten. */
export function useQueueConflicts(): Queue {
  return useQueueEntries().filter((entry) => entry.status === "conflict");
}
