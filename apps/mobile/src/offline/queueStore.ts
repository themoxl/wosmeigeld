import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Crypto from "expo-crypto";
import {
  discardConflict,
  enqueue,
  markConflict,
  markDone,
  markRejected,
  markRetryLater,
  markSending,
  resetInFlightToPending,
  retryConflictWithLocalVersion,
} from "./queueReducer";
import type { Queue, QueueConflict, QueueEntry } from "./queueTypes";

// Phase 9 (Offline-Write-Queue): AsyncStorage-Persistenz der Queue nach
// demselben Muster wie ../utils/transferLinkCache.ts (ein JSON-Blob unter
// einem festen Key), ergaenzt um einen In-Memory-Zwischenstand plus
// Subscriber-Mechanismus (useSyncExternalStore in den Hooks), damit die UI
// ohne eigenes Polling auf Queue-Aenderungen reagiert.
//
// Bewusst NICHT Teil des React-Query-Cache: dessen Persistenz ist auf
// minimierte Lesedaten beschraenkt und wird bei Abmeldung verworfen (siehe
// ../api/queryClient.ts). Noch nicht gesendete Schreibvorgaenge duerfen
// dagegen unter keinen Umstaenden verloren gehen.
const STORAGE_KEY = "finanzplaner:mobile:write-queue:v1";

let state: Queue = [];
let loadPromise: Promise<void> | null = null;
/** Serialisiert Zustandsaenderungen, damit sich zwei Schreiber nicht ueberholen. */
let writeChain: Promise<void> = Promise.resolve();
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

function parseQueue(raw: string | null): Queue {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Queue) : [];
  } catch {
    // Beschaedigter Eintrag: lieber mit leerer Queue starten als die App bei
    // jedem Start am selben Punkt scheitern zu lassen.
    return [];
  }
}

async function ensureLoaded(): Promise<void> {
  if (!loadPromise) {
    loadPromise = (async () => {
      const stored = parseQueue(await AsyncStorage.getItem(STORAGE_KEY));
      // App-Neustart, waehrend eine Anfrage unterwegs war: siehe
      // resetInFlightToPending() - PATCH/PUT/DELETE werden wiederholt, eine
      // unterbrochene Neuanlage wandert in die Konfliktansicht.
      state = resetInFlightToPending(stored);
      notify();
    })();
  }
  await loadPromise;
}

// Laden sofort anstossen, nicht erst beim ersten Hook-Aufruf - sonst lieferte
// der allererste getQueueSnapshot() eine leere Queue, obwohl bereits
// persistierte Eintraege existieren. NICHT waehrend expo-routers
// Node-Vorrender-Durchlauf fuer die Web-Ausgabe (app.json "web.output":
// "static"): dort gibt es kein globales "window", AsyncStorages
// Web-Implementierung greift aber synchron darauf zu und riss bisher den
// gesamten Metro-Prozess mit einem ungefangenen "window is not defined" ab -
// dieses Modul wird schon beim Laden von app/_layout.tsx importiert
// (useTransactionQueue), unabhaengig davon, ob RootNavigator ueberhaupt
// gerendert wird. Gleiches Muster wie isStaticRenderingPass in
// src/auth/supabase.ts.
if (typeof window !== "undefined") {
  void ensureLoaded();
}

function mutate(updater: (queue: Queue) => Queue): Promise<void> {
  writeChain = writeChain.then(async () => {
    await ensureLoaded();
    const next = updater(state);
    if (next === state) return;
    state = next;
    notify();
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  });
  return writeChain;
}

/** Fuer useSyncExternalStore: aktueller Queue-Stand (bis zum ersten Laden leer). */
export function getQueueSnapshot(): Queue {
  return state;
}

/** Fuer useSyncExternalStore: bei jeder Aenderung (inkl. initialem Laden) benachrichtigen. */
export function subscribeQueue(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Wartet, bis die persistierte Queue geladen ist (z. B. vor dem ersten Flush). */
export function whenQueueLoaded(): Promise<void> {
  return ensureLoaded();
}

export function generateId(): string {
  return Crypto.randomUUID();
}

export function enqueueEntry(entry: QueueEntry): Promise<void> {
  return mutate((queue) => enqueue(queue, entry));
}

/**
 * Mehrere Eintraege in EINEM Schreibvorgang einreihen.
 *
 * Die Mehrfachzuordnung von Kategorien darf bis zu 500 Buchungen umfassen
 * (docs/KATEGORISIERUNGSPLAN.md). Ueber enqueueEntry() waeren das 500
 * serialisierte AsyncStorage-Schreibvorgaenge auf eine mit jedem Schritt
 * groesser werdende Queue - spuerbar langsam und ohne Nutzen, weil ohnehin
 * erst am Ende geflusht wird. Die Kompaktierungsregeln aus enqueue() gelten
 * unveraendert, sie werden hier nur nacheinander auf denselben
 * Zwischenzustand angewendet.
 */
export function enqueueEntries(entries: QueueEntry[]): Promise<void> {
  if (entries.length === 0) return Promise.resolve();
  return mutate((queue) => entries.reduce(enqueue, queue));
}

export function markEntrySending(entryId: string): Promise<void> {
  return mutate((queue) => markSending(queue, entryId));
}

export function markEntryDone(entryId: string): Promise<void> {
  return mutate((queue) => markDone(queue, entryId));
}

export function markEntryRetryLater(entryId: string, now: number, message?: string): Promise<void> {
  return mutate((queue) => markRetryLater(queue, entryId, now, message));
}

export function markEntryRejected(entryId: string, status: number, message: string): Promise<void> {
  return mutate((queue) => markRejected(queue, entryId, status, message));
}

export function markEntryConflict(entryId: string, conflict: QueueConflict): Promise<void> {
  return mutate((queue) => markConflict(queue, entryId, conflict));
}

export function discardConflictedEntry(entryId: string): Promise<void> {
  return mutate((queue) => discardConflict(queue, entryId));
}

export function retryConflictedEntry(entryId: string, currentVersion?: number): Promise<void> {
  return mutate((queue) => retryConflictWithLocalVersion(queue, entryId, currentVersion));
}

/**
 * Alles verwerfen - beim Abmelden aufzurufen, damit keine Schreibvorgaenge
 * eines vorherigen Nutzers auf dem Geraet zurueckbleiben (Mandantentrennung,
 * Architekturplan Abschnitt 5).
 */
export function clearQueue(): Promise<void> {
  return mutate(() => []);
}

/**
 * Nur fuer Tests: verwirft den Speicherzustand, ohne die persistierten Daten
 * anzufassen - damit laesst sich ein App-Neustart nachstellen (naechster
 * Zugriff laedt wieder aus AsyncStorage).
 */
export function resetInMemoryQueueForTests(): void {
  state = [];
  loadPromise = null;
  writeChain = Promise.resolve();
  void ensureLoaded();
}
