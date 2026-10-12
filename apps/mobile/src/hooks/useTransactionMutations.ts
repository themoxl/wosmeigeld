import { useMutation } from "@tanstack/react-query";
import type { CreateTransactionInput } from "../api/types";
import { useHousehold } from "../household/HouseholdProvider";
import { flush } from "../offline/queueProcessor";
import { enqueueEntry, generateId } from "../offline/queueStore";
import type { QueueEntry, QueueSplitsPayload, QueueUpdatePayload } from "../offline/queueTypes";

// Phase 9 (Offline-Write-Queue): ALLE Buchungs-Schreibvorgaenge laufen ueber
// dieselbe Queue - es gibt bewusst keinen zweiten, direkten Online-Codepfad.
// Ein solcher waere die haeufigste Fehlerquelle: er wuerde nur unter guten
// Bedingungen getestet und im schlechten Fall die Reihenfolge gegenueber der
// Queue vertauschen.
//
// Die Mutation wartet nur auf den (schnellen) lokalen Schreibvorgang, nicht
// auf die Serverantwort - der aufrufende Screen kann direkt danach
// navigieren; flush() laeuft im Hintergrund weiter.

function nowIso(): string {
  return new Date().toISOString();
}

function baseEntry(householdId: string, localId: string) {
  return {
    entryId: generateId(),
    localId,
    householdId,
    status: "pending" as const,
    attempts: 0,
    createdAt: nowIso(),
    nextAttemptAt: 0,
  };
}

function useHouseholdIdOrThrow(): () => string {
  const { selectedHouseholdId } = useHousehold();
  return () => {
    if (!selectedHouseholdId) throw new Error("Kein Haushalt ausgewählt.");
    return selectedHouseholdId;
  };
}

/**
 * Neue Buchung. Gibt die lokale Platzhalter-ID zurueck - die endgueltige ID
 * vergibt der Server beim Synchronisieren (der Vertrag kennt keine
 * client-seitig vorgegebene ID).
 */
export function useCreateTransaction() {
  const requireHousehold = useHouseholdIdOrThrow();

  return useMutation({
    mutationFn: async (input: CreateTransactionInput): Promise<string> => {
      const householdId = requireHousehold();
      const localId = generateId();
      const entry: QueueEntry = {
        ...baseEntry(householdId, localId),
        kind: "create",
        payload: input,
      };
      await enqueueEntry(entry);
      void flush();
      return localId;
    },
  });
}

export interface UpdateTransactionArgs {
  transactionId: string;
  /** Zuletzt gesehene Serverversion (Transaction.version) - siehe Konfliktvertrag. */
  expectedVersion: number;
  patch: QueueUpdatePayload;
}

export function useUpdateTransaction() {
  const requireHousehold = useHouseholdIdOrThrow();

  return useMutation({
    mutationFn: async (args: UpdateTransactionArgs): Promise<void> => {
      const householdId = requireHousehold();
      const entry: QueueEntry = {
        ...baseEntry(householdId, args.transactionId),
        kind: "update",
        payload: args.patch,
        expectedVersion: args.expectedVersion,
      };
      await enqueueEntry(entry);
      void flush();
    },
  });
}

export interface UpdateTransactionSplitsArgs {
  transactionId: string;
  expectedVersion: number;
  splits: QueueSplitsPayload;
}

export function useUpdateTransactionSplits() {
  const requireHousehold = useHouseholdIdOrThrow();

  return useMutation({
    mutationFn: async (args: UpdateTransactionSplitsArgs): Promise<void> => {
      const householdId = requireHousehold();
      const entry: QueueEntry = {
        ...baseEntry(householdId, args.transactionId),
        kind: "splits",
        payload: args.splits,
        expectedVersion: args.expectedVersion,
      };
      await enqueueEntry(entry);
      void flush();
    },
  });
}

export function useDeleteTransaction() {
  const requireHousehold = useHouseholdIdOrThrow();

  return useMutation({
    mutationFn: async (transactionId: string): Promise<void> => {
      const householdId = requireHousehold();
      const entry: QueueEntry = {
        ...baseEntry(householdId, transactionId),
        kind: "delete",
        payload: null,
      };
      await enqueueEntry(entry);
      void flush();
    },
  });
}
