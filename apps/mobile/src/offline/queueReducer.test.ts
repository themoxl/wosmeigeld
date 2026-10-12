/// <reference types="jest" />
import type { TransactionDetail } from "../api/types";
import {
  BACKOFF_BASE_MS,
  BACKOFF_MAX_MS,
  MAX_ATTEMPTS,
  backoffDelayMs,
  discardConflict,
  enqueue,
  markConflict,
  markDone,
  markRejected,
  markRetryLater,
  markSending,
  nextAttemptDueAt,
  nextToProcess,
  resetInFlightToPending,
  retryConflictWithLocalVersion,
} from "./queueReducer";
import type { Queue, QueueEntry } from "./queueTypes";

function createEntry(overrides: Partial<QueueEntry> = {}): QueueEntry {
  return {
    entryId: "entry-1",
    localId: "txn-1",
    householdId: "household-1",
    status: "pending",
    attempts: 0,
    createdAt: "2026-01-01T00:00:00.000Z",
    nextAttemptAt: 0,
    kind: "create",
    payload: { account_id: "acc-1", booked_date: "2026-01-01", amount_minor: -1000 },
    ...overrides,
  } as QueueEntry;
}

function serverDetail(version: number): TransactionDetail {
  return {
    id: "txn-1",
    account_id: "acc-1",
    booked_date: "2026-01-02",
    value_date: null,
    amount_minor: -2000,
    currency: "EUR",
    payee_label: "Server",
    note: null,
    status: "booked",
    is_transfer: false,
    category_id: null,
    is_split: false,
    version,
    is_archived: false,
    splits: [],
  };
}

describe("enqueue", () => {
  it("haengt eine Neuanlage an die leere Queue an", () => {
    const entry = createEntry();
    expect(enqueue([], entry)).toEqual([entry]);
  });

  it("annulliert eine noch nicht gesendete Neuanlage, wenn dieselbe Buchung geloescht wird", () => {
    const create = createEntry({ entryId: "e1" });
    const del = createEntry({ entryId: "e2", kind: "delete", payload: null });
    expect(enqueue([create], del)).toEqual([]);
  });

  it("annulliert eine bereits gesendete Neuanlage NICHT", () => {
    const create = createEntry({ entryId: "e1", status: "sending" });
    const del = createEntry({ entryId: "e2", kind: "delete", payload: null });
    expect(enqueue([create], del)).toEqual([create, del]);
  });

  it("verwirft gegenstandslose Bearbeitungen einer zu loeschenden Buchung", () => {
    const create = createEntry({ entryId: "e1", status: "sending" });
    const update = createEntry({
      entryId: "e2",
      kind: "update",
      payload: { note: "geaendert" },
      expectedVersion: 3,
    });
    const del = createEntry({ entryId: "e3", kind: "delete", payload: null });
    expect(enqueue([create, update], del)).toEqual([create, del]);
  });

  it("mischt eine zweite offene Detailaenderung feldweise in die erste", () => {
    const first = createEntry({
      entryId: "e1",
      kind: "update",
      payload: { note: "erste Aenderung" },
      expectedVersion: 3,
    });
    const second = createEntry({
      entryId: "e2",
      kind: "update",
      payload: { payee_label: "Lidl" },
      expectedVersion: 3,
    });
    const result = enqueue([first], second);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      entryId: "e1",
      payload: { note: "erste Aenderung", payee_label: "Lidl" },
    });
  });

  it("behaelt beim Mischen die aelteste bekannte Version (verhindert einen Selbstkonflikt)", () => {
    const first = createEntry({
      entryId: "e1",
      kind: "update",
      payload: { note: "erste" },
      expectedVersion: 3,
    });
    const second = createEntry({
      entryId: "e2",
      kind: "update",
      payload: { note: "zweite" },
      expectedVersion: 4,
    });
    const [merged] = enqueue([first], second);
    if (!merged || merged.kind !== "update") throw new Error("update-Eintrag erwartet");
    expect(merged.expectedVersion).toBe(3);
    expect(merged.payload.note).toBe("zweite");
  });

  it("ersetzt offene Splits vollstaendig, statt sie feldweise zu mischen", () => {
    const first = createEntry({
      entryId: "e1",
      kind: "splits",
      payload: { amount_minor: -1000, splits: [{ amount_minor: -1000 }] },
      expectedVersion: 2,
    });
    const second = createEntry({
      entryId: "e2",
      kind: "splits",
      payload: { amount_minor: -1000, splits: [{ amount_minor: -500 }, { amount_minor: -500 }] },
      expectedVersion: 2,
    });
    const result = enqueue([first], second);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ entryId: "e1", payload: second.payload });
  });

  it("kompaktiert nicht gegen einen Eintrag, der auf eine Entscheidung wartet", () => {
    const conflicted = createEntry({
      entryId: "e1",
      kind: "update",
      status: "conflict",
      payload: { note: "im Konflikt" },
      expectedVersion: 3,
      conflict: { kind: "version_conflict", currentVersion: 5, current: serverDetail(5) },
    });
    const fresh = createEntry({
      entryId: "e2",
      kind: "update",
      payload: { note: "neu" },
      expectedVersion: 5,
    });
    expect(enqueue([conflicted], fresh)).toEqual([conflicted, fresh]);
  });

  it("laesst Eintraege anderer Buchungen unberuehrt", () => {
    const create = createEntry({ entryId: "e1", localId: "txn-1" });
    const other = createEntry({ entryId: "e2", localId: "txn-2" });
    expect(enqueue([create], other)).toEqual([create, other]);
  });
});

// Kategoriezuordnung offline (docs/KATEGORISIERUNGSPLAN.md, "Manuelle
// Mehrfachzuordnung"): eine eigene Eintragsart, weil PUT .../category eine
// Mehrfachsplit-Buchung ablehnt statt sie still zu ueberschreiben.
describe("enqueue: Kategoriezuordnung", () => {
  function categoryEntry(entryId: string, categoryId: string | null, expectedVersion = 2) {
    return createEntry({
      entryId,
      localId: "txn-1",
      kind: "category",
      payload: { category_id: categoryId },
      expectedVersion,
    });
  }

  it("haengt eine Kategoriezuordnung an", () => {
    const entry = categoryEntry("e1", "cat-1");
    expect(enqueue([], entry)).toEqual([entry]);
  });

  it("ersetzt eine offene Kategoriezuordnung und behaelt die aeltere Version", () => {
    const first = categoryEntry("e1", "cat-1", 2);
    const second = categoryEntry("e2", "cat-2", 4);
    const [merged] = enqueue([first], second);
    if (!merged || merged.kind !== "category") throw new Error("category-Eintrag erwartet");
    expect(merged.entryId).toBe("e1");
    expect(merged.expectedVersion).toBe(2);
    expect(merged.payload.category_id).toBe("cat-2");
  });

  it("zieht die Kategorie in ein offenes Splits-PUT mit genau einem Split", () => {
    const splits = createEntry({
      entryId: "e1",
      localId: "txn-1",
      kind: "splits",
      payload: { amount_minor: -1000, splits: [{ amount_minor: -1000, category_id: "cat-alt" }] },
      expectedVersion: 2,
    });
    const result = enqueue([splits], categoryEntry("e2", "cat-neu"));
    expect(result).toHaveLength(1);
    const [merged] = result;
    if (!merged || merged.kind !== "splits") throw new Error("splits-Eintrag erwartet");
    expect(merged.payload.splits).toEqual([{ amount_minor: -1000, category_id: "cat-neu" }]);
    expect(merged.expectedVersion).toBe(2);
  });

  it("fasst ein offenes Splits-PUT mit MEHREREN Splits nicht an", () => {
    // Welcher Split gemeint waere, ist nicht bestimmbar - eine Gesamtkategorie
    // darf eine Aufteilung nie still ueberschreiben.
    const splits = createEntry({
      entryId: "e1",
      localId: "txn-1",
      kind: "splits",
      payload: { amount_minor: -1000, splits: [{ amount_minor: -600 }, { amount_minor: -400 }] },
      expectedVersion: 2,
    });
    const category = categoryEntry("e2", "cat-neu");
    expect(enqueue([splits], category)).toEqual([splits, category]);
  });

  it("verwirft eine offene Kategoriezuordnung, wenn danach die Splits ersetzt werden", () => {
    const category = categoryEntry("e1", "cat-1");
    const splits = createEntry({
      entryId: "e2",
      localId: "txn-1",
      kind: "splits",
      payload: { amount_minor: -1000, splits: [{ amount_minor: -1000, category_id: "cat-9" }] },
      expectedVersion: 2,
    });
    expect(enqueue([category], splits)).toEqual([splits]);
  });

  it("kompaktiert nicht gegen eine Kategoriezuordnung im Konflikt", () => {
    const conflicted = createEntry({
      entryId: "e1",
      localId: "txn-1",
      kind: "category",
      status: "conflict",
      payload: { category_id: "cat-1" },
      expectedVersion: 2,
      conflict: { kind: "version_conflict", currentVersion: 5, current: serverDetail(5) },
    });
    const fresh = categoryEntry("e2", "cat-2", 5);
    expect(enqueue([conflicted], fresh)).toEqual([conflicted, fresh]);
  });

  it("wird von einer Loeschung derselben Buchung annulliert", () => {
    const category = categoryEntry("e1", "cat-1");
    const del = createEntry({ entryId: "e2", localId: "txn-1", kind: "delete", payload: null });
    expect(enqueue([category], del)).toEqual([del]);
  });

  it("laesst Kategoriezuordnungen anderer Buchungen unberuehrt", () => {
    const first = categoryEntry("e1", "cat-1");
    const other = createEntry({
      entryId: "e2",
      localId: "txn-2",
      kind: "category",
      payload: { category_id: "cat-1" },
      expectedVersion: 3,
    });
    expect(enqueue([first], other)).toEqual([first, other]);
  });
});

describe("Statusuebergaenge", () => {
  it("zaehlt beim Senden die Versuche hoch", () => {
    const result = markSending([createEntry({ attempts: 0 })], "entry-1");
    expect(result[0]).toMatchObject({ status: "sending", attempts: 1 });
  });

  it("entfernt einen erfolgreich uebertragenen Eintrag", () => {
    expect(markDone([createEntry()], "entry-1")).toEqual([]);
  });

  it("setzt einen Fehlversuch mit Backoff-Fenster zurueck auf pending", () => {
    const queue: Queue = [createEntry({ status: "sending", attempts: 1 })];
    const [entry] = markRetryLater(queue, "entry-1", 1_000_000, "kein Netz");
    expect(entry).toMatchObject({ status: "pending", lastError: "kein Netz" });
    expect(entry?.nextAttemptAt).toBe(1_000_000 + BACKOFF_BASE_MS);
  });

  it("waechst der Backoff exponentiell und wird gedeckelt", () => {
    expect(backoffDelayMs(1)).toBe(BACKOFF_BASE_MS);
    expect(backoffDelayMs(2)).toBe(BACKOFF_BASE_MS * 2);
    expect(backoffDelayMs(3)).toBe(BACKOFF_BASE_MS * 4);
    expect(backoffDelayMs(99)).toBe(BACKOFF_MAX_MS);
  });

  it("gibt nach MAX_ATTEMPTS auf, statt endlos weiter zu versuchen", () => {
    const queue: Queue = [createEntry({ status: "sending", attempts: MAX_ATTEMPTS })];
    const [entry] = markRetryLater(queue, "entry-1", 1_000_000, "Server unten");
    expect(entry?.status).toBe("conflict");
    expect(entry?.conflict).toMatchObject({ kind: "rejected" });
    // Und danach ist der Eintrag fuer den Processor unsichtbar.
    expect(nextToProcess(markRetryLater(queue, "entry-1", 1_000_000), 9_999_999)).toBeNull();
  });

  it("markiert eine fachliche Ablehnung sofort als endgueltig", () => {
    const [entry] = markRejected([createEntry({ status: "sending" })], "entry-1", 404, "weg");
    expect(entry).toMatchObject({
      status: "conflict",
      conflict: { kind: "rejected", status: 404 },
    });
  });

  it("haelt bei einem Konflikt den vollstaendigen Serverstand fest", () => {
    const queue: Queue = [createEntry({ status: "sending" })];
    const [entry] = markConflict(queue, "entry-1", {
      kind: "version_conflict",
      currentVersion: 7,
      current: serverDetail(7),
    });
    expect(entry?.conflict).toMatchObject({ kind: "version_conflict", currentVersion: 7 });
  });

  it("verwirft einen Konflikteintrag auf Wunsch", () => {
    expect(discardConflict([createEntry({ status: "conflict" })], "entry-1")).toEqual([]);
  });

  it("sendet 'meine Version behalten' mit der aktuellen Serverversion erneut", () => {
    const queue: Queue = [
      createEntry({
        kind: "update",
        status: "conflict",
        attempts: 3,
        nextAttemptAt: 99_999,
        payload: { note: "lokal" },
        expectedVersion: 3,
        conflict: { kind: "version_conflict", currentVersion: 7, current: serverDetail(7) },
      }),
    ];
    const [entry] = retryConflictWithLocalVersion(queue, "entry-1", 7);
    expect(entry).toMatchObject({ status: "pending", attempts: 0, nextAttemptAt: 0 });
    expect(entry?.conflict).toBeUndefined();
    if (!entry || entry.kind !== "update") throw new Error("update-Eintrag erwartet");
    expect(entry.expectedVersion).toBe(7);
  });
});

describe("resetInFlightToPending (App-Neustart)", () => {
  it("wiederholt unterbrochene PATCH/PUT/DELETE blind - die sind idempotent", () => {
    const queue: Queue = [
      createEntry({ entryId: "e1", kind: "delete", payload: null, status: "sending" }),
      createEntry({
        entryId: "e2",
        kind: "update",
        payload: { note: "x" },
        expectedVersion: 2,
        status: "sending",
      }),
    ];
    expect(resetInFlightToPending(queue).map((e) => e.status)).toEqual(["pending", "pending"]);
  });

  it("legt eine unterbrochene Neuanlage der Nutzerin vor, statt sie zu verdoppeln", () => {
    const [entry] = resetInFlightToPending([createEntry({ status: "sending" })]);
    expect(entry).toMatchObject({ status: "conflict", conflict: { kind: "uncertain_create" } });
  });

  it("laesst bereits bestehende Konflikte unveraendert", () => {
    const conflicted = createEntry({ entryId: "e1", status: "conflict" });
    expect(resetInFlightToPending([conflicted])).toEqual([conflicted]);
  });
});

describe("nextToProcess", () => {
  it("liefert null fuer eine leere Queue", () => {
    expect(nextToProcess([], 0)).toBeNull();
  });

  it("ueberspringt Eintraege, deren Backoff-Fenster noch laeuft", () => {
    const queue: Queue = [createEntry({ entryId: "e1", nextAttemptAt: 5_000 })];
    expect(nextToProcess(queue, 4_999)).toBeNull();
    expect(nextToProcess(queue, 5_000)?.entryId).toBe("e1");
  });

  it("laesst einen Konflikt nur die eigene Buchung blockieren, nicht die ganze Queue", () => {
    const queue: Queue = [
      createEntry({ entryId: "e1", localId: "txn-1", status: "conflict" }),
      createEntry({ entryId: "e2", localId: "txn-1", kind: "delete", payload: null }),
      createEntry({ entryId: "e3", localId: "txn-2" }),
    ];
    // e2 betrifft dieselbe Buchung wie der Konflikt und muss warten,
    // e3 gehoert zu einer anderen Buchung und darf laufen.
    expect(nextToProcess(queue, 0)?.entryId).toBe("e3");
  });

  it("haelt die Reihenfolge je Buchung ein, wenn ein frueherer Eintrag wartet", () => {
    const queue: Queue = [
      createEntry({ entryId: "e1", localId: "txn-1", nextAttemptAt: 10_000 }),
      createEntry({ entryId: "e2", localId: "txn-1", kind: "delete", payload: null }),
    ];
    expect(nextToProcess(queue, 0)).toBeNull();
  });

  it("liefert den ersten faelligen Eintrag in Queue-Reihenfolge", () => {
    const queue: Queue = [
      createEntry({ entryId: "e1", localId: "txn-1" }),
      createEntry({ entryId: "e2", localId: "txn-2" }),
    ];
    expect(nextToProcess(queue, 0)?.entryId).toBe("e1");
  });
});

describe("nextAttemptDueAt", () => {
  it("liefert null, wenn nichts mehr wartet", () => {
    expect(nextAttemptDueAt([])).toBeNull();
    expect(nextAttemptDueAt([createEntry({ status: "conflict" })])).toBeNull();
  });

  it("liefert die fruehste Faelligkeit", () => {
    const queue: Queue = [
      createEntry({ entryId: "e1", nextAttemptAt: 9_000 }),
      createEntry({ entryId: "e2", localId: "txn-2", nextAttemptAt: 3_000 }),
    ];
    expect(nextAttemptDueAt(queue)).toBe(3_000);
  });
});
