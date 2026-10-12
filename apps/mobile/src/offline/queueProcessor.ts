import {
  ApiError,
  createTransaction,
  deleteTransaction,
  patchTransaction,
  putTransactionSplits,
  updateTransactionCategory,
} from "../api/client";
import { queryClient } from "../api/queryClient";
import type { TransactionConflict } from "../api/types";
import { TRANSACTION_DETAIL_QUERY_KEY } from "../hooks/useTransactionDetail";
import { nextToProcess } from "./queueReducer";
import {
  getQueueSnapshot,
  markEntryConflict,
  markEntryDone,
  markEntryRejected,
  markEntryRetryLater,
  markEntrySending,
  whenQueueLoaded,
} from "./queueStore";
import type { QueueEntry } from "./queueTypes";

// Phase 9 (Offline-Write-Queue): serielle Abarbeitung der Queue. Ein
// Token-Provider statt eines festen Tokens, weil Eintraege stundenlang warten
// koennen und der Access-Token in der Zwischenzeit erneuert worden sein kann
// (siehe ../hooks/useTransactionQueue.ts, das den Provider aus dem
// AuthProvider speist).
export type AccessTokenProvider = () => string | null;

let getAccessToken: AccessTokenProvider = () => null;

export function configureQueueProcessor(provider: AccessTokenProvider): void {
  getAccessToken = provider;
}

type SendResult =
  | { outcome: "done" }
  | { outcome: "conflict"; conflict: TransactionConflict }
  /** Vorlaeufig gescheitert - spaeter erneut versuchen (Backoff). */
  | { outcome: "retry"; message: string }
  /** Endgueltig abgelehnt - ein Retry wuerde daran nichts aendern. */
  | { outcome: "rejected"; status: number; message: string };

/**
 * Ein 4xx, das sich durch Warten nicht aendert, wird sofort als endgueltig
 * behandelt - sonst laege der Eintrag bis MAX_ATTEMPTS in der Queue und
 * blockierte alle spaeteren Aenderungen derselben Buchung.
 *
 * Bewusst NICHT dabei: 401 (der Token kann erneuert werden) und 429/5xx
 * (Serverzustand kann sich aendern).
 */
function isPermanentRejection(status: number): boolean {
  return status === 400 || status === 403 || status === 404 || status === 422;
}

async function sendEntry(entry: QueueEntry, accessToken: string): Promise<SendResult> {
  try {
    switch (entry.kind) {
      case "create":
        await createTransaction(accessToken, entry.householdId, entry.payload);
        return { outcome: "done" };
      case "update":
        await patchTransaction(accessToken, entry.householdId, entry.localId, {
          ...entry.payload,
          expected_version: entry.expectedVersion,
        });
        return { outcome: "done" };
      case "splits":
        await putTransactionSplits(accessToken, entry.householdId, entry.localId, {
          ...entry.payload,
          expected_version: entry.expectedVersion,
        });
        return { outcome: "done" };
      case "category":
        await updateTransactionCategory(accessToken, entry.householdId, entry.localId, {
          category_id: entry.payload.category_id,
          expected_version: entry.expectedVersion,
        });
        return { outcome: "done" };
      case "delete":
        await deleteTransaction(accessToken, entry.householdId, entry.localId);
        return { outcome: "done" };
    }
  } catch (cause) {
    if (cause instanceof ApiError) {
      if (cause.status === 409 && cause.conflict) {
        return { outcome: "conflict", conflict: cause.conflict };
      }
      // Eine bereits geloeschte Buchung noch einmal zu loeschen ist fachlich
      // der gewuenschte Endzustand - kein Fehler, den die Nutzerin klaeren
      // muesste.
      if (cause.status === 404 && entry.kind === "delete") {
        return { outcome: "done" };
      }
      // Ein 409 OHNE Versionsstand ist kein Nebenlaeufigkeitsproblem, sondern
      // eine fachliche Ablehnung, die sich durch Warten nicht aendert - etwa
      // `transaction_has_multiple_splits` (die Buchung wurde zwischenzeitlich
      // aufgeteilt) oder `category_inactive` (das Ziel wurde archiviert). Ohne
      // diesen Zweig liefe der Eintrag stumpf bis MAX_ATTEMPTS.
      if (cause.status === 409) {
        return { outcome: "rejected", status: 409, message: cause.message };
      }
      if (isPermanentRejection(cause.status)) {
        return { outcome: "rejected", status: cause.status, message: cause.message };
      }
      return { outcome: "retry", message: cause.message };
    }
    // Netzwerkfehler (authorizedFetch wirft dafuer einen einfachen Error).
    return {
      outcome: "retry",
      message: cause instanceof Error ? cause.message : "Unbekannter Netzwerkfehler.",
    };
  }
}

function invalidateAfterSuccess(): void {
  void queryClient.invalidateQueries({ queryKey: ["transactions"] });
  void queryClient.invalidateQueries({ queryKey: [TRANSACTION_DETAIL_QUERY_KEY] });
}

let flushing = false;

/**
 * Arbeitet die Queue ab, bis kein Eintrag mehr faellig ist.
 *
 * Ein fehlgeschlagener Eintrag beendet den Durchlauf nicht: er bekommt ein
 * Backoff-Fenster und wird von nextToProcess() bis dahin uebersprungen,
 * ebenso alle spaeteren Eintraege DERSELBEN Buchung (Reihenfolge). Eintraege
 * anderer Buchungen laufen weiter - ein einzelner Konflikt oder ein
 * dauerhaft abgelehnter Eintrag legt die Queue also nicht lahm.
 *
 * Reentranz ist ausgeschlossen (flushing-Flag): Reconnect, App-Vordergrund
 * und Retry-Timer koennen gleichzeitig ausloesen.
 */
export async function flush(): Promise<void> {
  if (flushing) return;
  flushing = true;
  try {
    await whenQueueLoaded();
    for (;;) {
      const entry = nextToProcess(getQueueSnapshot(), Date.now());
      if (!entry) return;

      const accessToken = getAccessToken();
      if (!accessToken) return;

      await markEntrySending(entry.entryId);
      const result = await sendEntry(entry, accessToken);

      switch (result.outcome) {
        case "done":
          await markEntryDone(entry.entryId);
          invalidateAfterSuccess();
          break;
        case "conflict":
          await markEntryConflict(entry.entryId, {
            kind: result.conflict.detail,
            currentVersion: result.conflict.current_version,
            // Der Server liefert den vollstaendigen Stand mit, damit die
            // Konfliktansicht beide Versionen zeigen kann, ohne nachzuladen.
            current: result.conflict.current,
          });
          break;
        case "rejected":
          await markEntryRejected(entry.entryId, result.status, result.message);
          break;
        case "retry":
          await markEntryRetryLater(entry.entryId, Date.now(), result.message);
          break;
      }
    }
  } finally {
    flushing = false;
  }
}
