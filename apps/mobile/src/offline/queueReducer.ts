import type { Queue, QueueConflict, QueueEntry } from "./queueTypes";

// Phase 9 (Offline-Write-Queue): reine Funktionen auf dem Queue-Zustand.
// Bewusst ohne Storage-/Netzwerkzugriff, damit das Zusammenspiel aus
// Kompaktierung, Backoff und Konfliktbehandlung ohne Mocks testbar bleibt
// (siehe queueReducer.test.ts).

/** Erster Wiederholversuch nach 5s, danach Verdopplung. */
export const BACKOFF_BASE_MS = 5_000;
/** Obergrenze, damit ein lange offline liegender Eintrag nicht stundenlang wartet. */
export const BACKOFF_MAX_MS = 5 * 60_000;
/**
 * Danach gilt ein Eintrag als dauerhaft gescheitert und wandert in die
 * Konfliktansicht, statt die Buchung endlos weiter zu blockieren.
 */
export const MAX_ATTEMPTS = 8;

/** Exponentieller Backoff mit Deckel; attempts ist die Zahl bisheriger Versuche. */
export function backoffDelayMs(attempts: number): number {
  if (attempts <= 1) return BACKOFF_BASE_MS;
  return Math.min(BACKOFF_BASE_MS * 2 ** (attempts - 1), BACKOFF_MAX_MS);
}

/**
 * Fuegt einen neuen Eintrag zur Queue hinzu. Kompaktiert dabei gegen bereits
 * vorhandene, noch nicht gesendete ("pending") Eintraege derselben localId.
 * Das ist kein reines Optimierungsdetail:
 *
 * - Ohne die update/splits-Kompaktierung wuerde ein zweites Offline-PATCH auf
 *   dieselbe Buchung mit derselben expectedVersion wie das erste
 *   serverseitig als Konflikt (409) abgelehnt, sobald das erste PATCH
 *   synchronisiert und die Serverversion dadurch hochgezaehlt wurde. Der
 *   zusammengefuehrte Eintrag behaelt deshalb die AELTESTE bekannte Version,
 *   nicht die des zuletzt eingereihten Eintrags.
 * - Eine Loeschung, die eine noch nicht gesendete Neuanlage derselben Buchung
 *   trifft, annulliert diese vollstaendig - es muss nie ein Request dafuer
 *   rausgehen.
 * - "category" und "splits" schreiben BEIDE die Kategoriezuordnung derselben
 *   Buchung. Blieben beide nebeneinander in der Queue, wuerde der zweite
 *   Request mit derselben (dann veralteten) expectedVersion garantiert einen
 *   409 erzeugen, obwohl die Nutzerin nur einmal etwas geaendert hat. Sie
 *   werden deshalb gegeneinander kompaktiert - siehe die beiden Faelle unten.
 *
 * Eintraege im Status "sending" oder "conflict" werden nie kompaktiert: bei
 * "sending" ist der Ausgang offen, bei "conflict" wartet der Eintrag auf eine
 * Entscheidung der Nutzerin und darf nicht klammheimlich veraendert werden.
 */
export function enqueue(queue: Queue, entry: QueueEntry): Queue {
  if (entry.kind === "delete") {
    const hasPendingCreate = queue.some(
      (e) => e.localId === entry.localId && e.kind === "create" && e.status === "pending",
    );
    if (hasPendingCreate) {
      return queue.filter((e) => e.localId !== entry.localId);
    }
    const withoutMootEdits = queue.filter(
      (e) => !(e.localId === entry.localId && e.status === "pending" && e.kind !== "create"),
    );
    return [...withoutMootEdits, entry];
  }

  if (entry.kind === "category") {
    // Eine zweite Kategorieaenderung ersetzt die erste vollstaendig (es gibt
    // nur ein Feld), behaelt aber deren aeltere expectedVersion.
    const existingIndex = queue.findIndex(
      (e) => e.localId === entry.localId && e.kind === "category" && e.status === "pending",
    );
    const existing = existingIndex === -1 ? undefined : queue[existingIndex];
    if (existing && existing.kind === "category") {
      return replaceAt(queue, existingIndex, { ...existing, payload: entry.payload });
    }

    // Liegt fuer dieselbe Buchung noch ein ungesendetes Splits-PUT mit genau
    // EINEM Split, ist die Kategorieaenderung exakt eine Aenderung an diesem
    // Split - sie wird hineingezogen statt als zweiter, konfliktsicherer
    // Request angehaengt. Bei mehreren Splits passiert das bewusst NICHT:
    // welcher Split gemeint waere, ist nicht bestimmbar, und eine
    // Gesamtkategorie darf eine Aufteilung nie still ueberschreiben.
    const splitsIndex = queue.findIndex(
      (e) =>
        e.localId === entry.localId &&
        e.kind === "splits" &&
        e.status === "pending" &&
        e.payload.splits.length === 1,
    );
    const pendingSplits = splitsIndex === -1 ? undefined : queue[splitsIndex];
    if (pendingSplits && pendingSplits.kind === "splits") {
      const [onlySplit] = pendingSplits.payload.splits;
      if (onlySplit) {
        return replaceAt(queue, splitsIndex, {
          ...pendingSplits,
          payload: {
            ...pendingSplits.payload,
            splits: [{ ...onlySplit, category_id: entry.payload.category_id }],
          },
        });
      }
    }

    return [...queue, entry];
  }

  if (entry.kind === "update" || entry.kind === "splits") {
    // Das Splits-PUT ersetzt die Zuordnung ohnehin vollstaendig - eine noch
    // wartende reine Kategorieaenderung derselben Buchung waere danach
    // gegenstandslos und wuerde nur einen Versionskonflikt erzeugen.
    const base =
      entry.kind === "splits"
        ? queue.filter(
            (e) =>
              !(e.localId === entry.localId && e.kind === "category" && e.status === "pending"),
          )
        : queue;

    const existingIndex = base.findIndex(
      (e) => e.localId === entry.localId && e.kind === entry.kind && e.status === "pending",
    );
    const existing = existingIndex === -1 ? undefined : base[existingIndex];
    if (existing && existing.kind === "update" && entry.kind === "update") {
      // Feldweise mischen: ein zweites Formular-Speichern soll die Felder des
      // ersten nicht auf "unveraendert" zuruecksetzen.
      const merged: QueueEntry = {
        ...existing,
        payload: { ...existing.payload, ...entry.payload },
      };
      return replaceAt(base, existingIndex, merged);
    }
    if (existing && existing.kind === "splits" && entry.kind === "splits") {
      // PUT ersetzt die Splits immer vollstaendig - hier darf NICHT feldweise
      // gemischt werden, sonst entstuende eine Split-Liste, die es so nie gab.
      const merged: QueueEntry = { ...existing, payload: entry.payload };
      return replaceAt(base, existingIndex, merged);
    }
    return [...base, entry];
  }

  return [...queue, entry];
}

function replaceAt(queue: Queue, index: number, entry: QueueEntry): Queue {
  return [...queue.slice(0, index), entry, ...queue.slice(index + 1)];
}

function updateEntry(
  queue: Queue,
  entryId: string,
  patch: (entry: QueueEntry) => QueueEntry,
): Queue {
  return queue.map((e) => (e.entryId === entryId ? patch(e) : e));
}

export function markSending(queue: Queue, entryId: string): Queue {
  return updateEntry(queue, entryId, (e) => ({
    ...e,
    status: "sending",
    attempts: e.attempts + 1,
  }));
}

/** Erfolgreich synchronisiert - der Eintrag wird aus der Queue entfernt. */
export function markDone(queue: Queue, entryId: string): Queue {
  return queue.filter((e) => e.entryId !== entryId);
}

/**
 * Vorlaeufiger Fehlschlag (kein Netz, 5xx, Timeout): erneut versuchen, aber
 * erst nach dem Backoff-Fenster. Nach MAX_ATTEMPTS wandert der Eintrag in die
 * Konfliktansicht - genau das verhindert die Endlosschleife bei einem
 * dauerhaft scheiternden Eintrag.
 */
export function markRetryLater(
  queue: Queue,
  entryId: string,
  now: number,
  message?: string,
): Queue {
  return updateEntry(queue, entryId, (e) => {
    if (e.attempts >= MAX_ATTEMPTS) {
      return {
        ...e,
        status: "conflict",
        lastError: message,
        conflict: {
          kind: "rejected",
          status: 0,
          message:
            message ??
            `Nach ${MAX_ATTEMPTS} Versuchen nicht uebertragbar (Server oder Netzwerk dauerhaft nicht erreichbar).`,
        },
      };
    }
    return {
      ...e,
      status: "pending",
      lastError: message,
      nextAttemptAt: now + backoffDelayMs(e.attempts),
    };
  });
}

/**
 * Endgueltige Ablehnung durch den Server (fachlicher 4xx, z. B. Konto
 * geloescht oder Leserolle). Ein Retry wuerde daran nichts aendern.
 */
export function markRejected(
  queue: Queue,
  entryId: string,
  status: number,
  message: string,
): Queue {
  return updateEntry(queue, entryId, (e) => ({
    ...e,
    status: "conflict",
    lastError: message,
    conflict: { kind: "rejected", status, message },
  }));
}

/** 409 vom Server - der Eintrag wartet auf eine Entscheidung der Nutzerin. */
export function markConflict(queue: Queue, entryId: string, conflict: QueueConflict): Queue {
  return updateEntry(queue, entryId, (e) => ({ ...e, status: "conflict", conflict }));
}

/** Konflikt verwerfen - die lokale Aenderung wird nicht mehr gesendet. */
export function discardConflict(queue: Queue, entryId: string): Queue {
  return queue.filter((e) => e.entryId !== entryId);
}

/**
 * "Meine Version behalten": erneut senden, diesmal mit der vom Server
 * gemeldeten aktuellen Version als expectedVersion. Der Schreibvorgang
 * ueberschreibt dann bewusst den fremden Stand.
 *
 * Fuer create/delete gibt es kein Versionstoken; dort wird lediglich der
 * Konflikt zurueckgesetzt und erneut versucht (bei "uncertain_create" ist das
 * die bewusste Entscheidung "lieber doppelt als verloren").
 */
export function retryConflictWithLocalVersion(
  queue: Queue,
  entryId: string,
  currentVersion?: number,
): Queue {
  return updateEntry(queue, entryId, (e) => {
    const base = {
      ...e,
      status: "pending" as const,
      attempts: 0,
      nextAttemptAt: 0,
      conflict: undefined,
      lastError: undefined,
    };
    if ((e.kind === "update" || e.kind === "splits") && currentVersion !== undefined) {
      return { ...base, kind: e.kind, expectedVersion: currentVersion } as QueueEntry;
    }
    return base;
  });
}

/**
 * Naechster abzuarbeitender Eintrag in Queue-Reihenfolge.
 *
 * Uebersprungen wird ein Eintrag, dessen Backoff-Fenster noch laeuft. Sobald
 * eine Buchung (localId) einen wartenden oder konfliktbehafteten Eintrag hat,
 * werden auch alle spaeteren Eintraege DERSELBEN Buchung uebersprungen - sonst
 * wuerde z. B. ein Loeschen vor der noch ausstehenden Neuanlage rausgehen.
 * Eintraege anderer Buchungen laufen weiter; ein einzelner Konflikt legt die
 * Queue also nicht lahm.
 *
 * "sending" zaehlt mit: nach einem Neustart der App ist der Ausgang unbekannt,
 * und resetInFlightToPending() hat den Fall bereits einsortiert.
 */
export function nextToProcess(queue: Queue, now: number): QueueEntry | null {
  const blockedLocalIds = new Set<string>();
  for (const entry of queue) {
    if (entry.status === "conflict") {
      blockedLocalIds.add(entry.localId);
      continue;
    }
    if (blockedLocalIds.has(entry.localId)) continue;
    if (entry.nextAttemptAt > now) {
      blockedLocalIds.add(entry.localId);
      continue;
    }
    return entry;
  }
  return null;
}

/** Wann darf fruehestens wieder geflusht werden? null = nichts wartet. */
export function nextAttemptDueAt(queue: Queue): number | null {
  const waiting = queue.filter((e) => e.status === "pending" || e.status === "sending");
  if (waiting.length === 0) return null;
  return Math.min(...waiting.map((e) => e.nextAttemptAt));
}

/**
 * Nach einem App-Neustart: Eintraege, die im Status "sending" eingefroren
 * wurden, wieder einsortieren.
 *
 * PATCH/PUT sind dank expectedVersion idempotent (ein zweites Mal zugestellt
 * liefert entweder denselben Stand oder einen sichtbaren 409), DELETE ist es
 * ohnehin - diese drei duerfen blind wiederholt werden. POST /v1/transactions
 * ist es NICHT (der Server vergibt die ID, der Vertrag kennt keinen
 * Idempotency-Key), deshalb landet eine unterbrochene Neuanlage in der
 * Konfliktansicht statt still verdoppelt zu werden.
 */
export function resetInFlightToPending(queue: Queue): Queue {
  return queue.map((entry) => {
    if (entry.status === "pending") {
      // Ein Neustart ist ein starkes Signal, dass sich die Lage geaendert
      // haben kann - also nicht erst das alte Backoff-Fenster abwarten. Die
      // Versuchszaehlung bleibt erhalten, damit MAX_ATTEMPTS weiterhin greift
      // und ein hoffnungsloser Eintrag nicht ewig weiterlaeuft.
      return entry.nextAttemptAt === 0 ? entry : { ...entry, nextAttemptAt: 0 };
    }
    if (entry.status !== "sending") return entry;
    if (entry.kind === "create") {
      return {
        ...entry,
        status: "conflict" as const,
        conflict: { kind: "uncertain_create" as const },
      };
    }
    return { ...entry, status: "pending" as const, nextAttemptAt: 0 };
  });
}
