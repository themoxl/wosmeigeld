/// <reference types="jest" />
import AsyncStorage from "@react-native-async-storage/async-storage";
import type { TransactionDetail } from "../api/types";
import { pendingCreatesAsTransactions } from "./pendingTransactions";
import { configureQueueProcessor, flush } from "./queueProcessor";
import { MAX_ATTEMPTS } from "./queueReducer";
import {
  discardConflictedEntry,
  enqueueEntries,
  enqueueEntry,
  getQueueSnapshot,
  markEntrySending,
  resetInMemoryQueueForTests,
  retryConflictedEntry,
  whenQueueLoaded,
} from "./queueStore";
import type { QueueEntry } from "./queueTypes";

// Der Processor invalidiert nach jedem Erfolg Queries. useTransactionDetail
// zieht dafuer den kompletten Auth-/Supabase-Baum herein, der in der
// Node-Testumgebung nichts zu suchen hat - hier reicht der Schluessel selbst.
jest.mock("../hooks/useTransactionDetail", () => ({
  TRANSACTION_DETAIL_QUERY_KEY: "transactionDetail",
}));

const HOUSEHOLD_ID = "household-1";

type FetchMock = jest.Mock<Promise<Response>, [string, RequestInit?]>;

/** Antwortobjekt in der Form, die authorizedFetch() auswertet. */
function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

/** Kein Netz: fetch selbst wirft (so verhaelt sich React Native im Flugmodus). */
function networkFailure(): Promise<Response> {
  return Promise.reject(new TypeError("Network request failed"));
}

function serverDetail(overrides: Partial<TransactionDetail> = {}): TransactionDetail {
  return {
    id: "txn-1",
    account_id: "acc-1",
    booked_date: "2026-02-01",
    value_date: null,
    amount_minor: -2500,
    currency: "EUR",
    payee_label: "Anderes Geraet",
    note: null,
    status: "booked",
    is_transfer: false,
    category_id: null,
    is_split: false,
    version: 5,
    is_archived: false,
    splits: [],
    ...overrides,
  };
}

function createEntry(overrides: Partial<QueueEntry> = {}): QueueEntry {
  return {
    entryId: "entry-1",
    localId: "local-1",
    householdId: HOUSEHOLD_ID,
    status: "pending",
    attempts: 0,
    createdAt: "2026-02-01T10:00:00.000Z",
    nextAttemptAt: 0,
    kind: "create",
    payload: { account_id: "acc-1", booked_date: "2026-02-01", amount_minor: -1999 },
    ...overrides,
  } as QueueEntry;
}

/** Stellt einen App-Neustart nach: Speicherzustand weg, AsyncStorage bleibt. */
async function simulateAppRestart(): Promise<void> {
  resetInMemoryQueueForTests();
  await whenQueueLoaded();
}

describe("Offline-Kern: Erfassen ohne Netz bis zur Synchronisation", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: FetchMock;

  beforeEach(async () => {
    await AsyncStorage.clear();
    await simulateAppRestart();
    fetchMock = jest.fn() as FetchMock;
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    configureQueueProcessor(() => "test-token");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("haelt eine offline erfasste Buchung fest, ueberlebt den App-Neustart und uebertraegt sie beim Reconnect", async () => {
    // 1) Offline erfassen: der Sendeversuch scheitert, der Eintrag bleibt.
    fetchMock.mockImplementation(networkFailure);
    await enqueueEntry(createEntry());
    await flush();

    expect(getQueueSnapshot()).toHaveLength(1);
    expect(getQueueSnapshot()[0]).toMatchObject({ status: "pending", attempts: 1 });

    // Und sie ist in der Buchungsliste sichtbar, obwohl sie nur lokal existiert.
    const placeholders = pendingCreatesAsTransactions(getQueueSnapshot(), HOUSEHOLD_ID);
    expect(placeholders).toHaveLength(1);
    expect(placeholders[0]).toMatchObject({ amount_minor: -1999, __pendingSync: true });

    // 2) App-Neustart - der Eintrag kommt aus dem Speicher zurueck.
    await simulateAppRestart();
    expect(getQueueSnapshot()).toHaveLength(1);
    expect(getQueueSnapshot()[0]?.status).toBe("pending");

    // 3) Wieder online: die Queue laeuft ab.
    fetchMock.mockImplementation(async () =>
      jsonResponse(201, { ...serverDetail({ id: "server-1", version: 1 }), splits: undefined }),
    );
    await flush();

    expect(getQueueSnapshot()).toHaveLength(0);
    const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1] ?? [];
    expect(url).toContain("/v1/transactions");
    expect(init?.method).toBe("POST");
  });

  it("legt einen 409 als sichtbaren Konflikt ab und uebertraegt ihn nach der Entscheidung mit der Serverversion", async () => {
    const entry = createEntry({
      entryId: "entry-update",
      localId: "txn-1",
      kind: "update",
      payload: { payee_label: "Meine Aenderung" },
      expectedVersion: 3,
    });
    await enqueueEntry(entry);

    fetchMock.mockImplementation(async () =>
      jsonResponse(409, {
        detail: "version_conflict",
        expected_version: 3,
        current_version: 5,
        current: serverDetail(),
      }),
    );
    await flush();

    const conflicted = getQueueSnapshot()[0];
    expect(conflicted?.status).toBe("conflict");
    expect(conflicted?.conflict).toMatchObject({ kind: "version_conflict", currentVersion: 5 });
    // Der Serverstand liegt vollstaendig vor - die Konfliktansicht muss nichts nachladen.
    expect(conflicted?.conflict).toMatchObject({ current: { payee_label: "Anderes Geraet" } });

    // Ein weiterer Flush ruehrt den Konflikt nicht an.
    const callsAfterConflict = fetchMock.mock.calls.length;
    await flush();
    expect(fetchMock.mock.calls.length).toBe(callsAfterConflict);

    // Entscheidung "meine Version behalten": erneut senden, diesmal mit Version 5.
    fetchMock.mockImplementation(async () => jsonResponse(200, serverDetail({ version: 6 })));
    await retryConflictedEntry("entry-update", 5);
    await flush();

    expect(getQueueSnapshot()).toHaveLength(0);
    const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
    expect(lastCall?.[1]?.method).toBe("PATCH");
    expect(JSON.parse(String(lastCall?.[1]?.body))).toMatchObject({
      payee_label: "Meine Aenderung",
      expected_version: 5,
    });
  });

  it("meldet eine anderswo geloeschte Buchung als eigenen Konfliktgrund", async () => {
    await enqueueEntry(
      createEntry({
        entryId: "entry-archived",
        localId: "txn-1",
        kind: "update",
        payload: { note: "noch schnell" },
        expectedVersion: 3,
      }),
    );
    fetchMock.mockImplementation(async () =>
      jsonResponse(409, {
        detail: "transaction_archived",
        expected_version: 3,
        current_version: 4,
        current: serverDetail({ is_archived: true, version: 4 }),
      }),
    );

    await flush();
    expect(getQueueSnapshot()[0]?.conflict).toMatchObject({ kind: "transaction_archived" });

    // Verwerfen entfernt den Eintrag endgueltig.
    await discardConflictedEntry("entry-archived");
    expect(getQueueSnapshot()).toHaveLength(0);
  });

  it("legt eine beim App-Ende in der Uebertragung haengende Neuanlage zur Entscheidung vor, statt sie zu verdoppeln", async () => {
    // Zustand nachstellen, den ein Absturz waehrend des POST hinterlaesst:
    // der Request ist raus, die Antwort kam nie an. Bewusst ueber
    // markEntrySending() statt ueber einen nie aufloesenden fetch-Mock - ein
    // haengendes Promise wuerde in die folgenden Tests hineinlecken.
    await enqueueEntry(createEntry());
    await markEntrySending("entry-1");
    expect(getQueueSnapshot()[0]?.status).toBe("sending");

    await simulateAppRestart();

    const restored = getQueueSnapshot()[0];
    expect(restored).toMatchObject({
      status: "conflict",
      conflict: { kind: "uncertain_create" },
    });
    // Nichts wird automatisch nachgesendet: POST /v1/transactions ist nicht idempotent.
    const callsBefore = fetchMock.mock.calls.length;
    await flush();
    expect(fetchMock.mock.calls.length).toBe(callsBefore);
  });

  it("gibt einen dauerhaft scheiternden Eintrag nach MAX_ATTEMPTS auf, statt endlos zu senden", async () => {
    const realNow = Date.now;
    let clock = 1_000_000;
    jest.spyOn(Date, "now").mockImplementation(() => clock);

    try {
      fetchMock.mockImplementation(async () => jsonResponse(503, { detail: "unavailable" }));
      await enqueueEntry(createEntry());

      // Immer wieder flushen und die Uhr weit genug vorstellen, damit das
      // Backoff-Fenster jeweils abgelaufen ist.
      for (let i = 0; i < MAX_ATTEMPTS + 2; i += 1) {
        await flush();
        clock += 60 * 60_000;
      }

      expect(fetchMock).toHaveBeenCalledTimes(MAX_ATTEMPTS);
      expect(getQueueSnapshot()[0]).toMatchObject({
        status: "conflict",
        attempts: MAX_ATTEMPTS,
        conflict: { kind: "rejected" },
      });
    } finally {
      Date.now = realNow;
    }
  });

  it("behandelt eine fachliche Ablehnung sofort als endgueltig", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(403, { detail: "household_role_readonly" }),
    );
    await enqueueEntry(createEntry());
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getQueueSnapshot()[0]).toMatchObject({
      status: "conflict",
      conflict: { kind: "rejected", status: 403 },
    });
  });

  it("wertet ein DELETE auf eine bereits geloeschte Buchung als erledigt", async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(404, { detail: "transaction_not_found" }),
    );
    await enqueueEntry(
      createEntry({ entryId: "entry-delete", localId: "txn-1", kind: "delete", payload: null }),
    );
    await flush();

    expect(getQueueSnapshot()).toHaveLength(0);
  });

  it("uebertraegt eine offline gesetzte Kategorie ueber PUT .../category", async () => {
    // Bulk-Zuordnung ohne Netz: pro Buchung ein Eintrag (siehe
    // docs/KATEGORISIERUNGSPLAN.md, "Manuelle Mehrfachzuordnung").
    fetchMock.mockImplementation(networkFailure);
    await enqueueEntry(
      createEntry({
        entryId: "entry-cat-1",
        localId: "txn-1",
        kind: "category",
        payload: { category_id: "cat-oepnv" },
        expectedVersion: 4,
      }),
    );
    await enqueueEntry(
      createEntry({
        entryId: "entry-cat-2",
        localId: "txn-2",
        kind: "category",
        payload: { category_id: "cat-oepnv" },
        expectedVersion: 2,
      }),
    );
    const realNow = Date.now;
    let clock = 2_000_000;
    jest.spyOn(Date, "now").mockImplementation(() => clock);
    try {
      await flush();
      expect(getQueueSnapshot()).toHaveLength(2);
      expect(getQueueSnapshot().every((e) => e.status === "pending")).toBe(true);

      // Wieder online - die Uhr ueber das Backoff-Fenster hinaus stellen.
      clock += 60 * 60_000;
      fetchMock.mockImplementation(async () => jsonResponse(200, serverDetail({ version: 6 })));
      await flush();
    } finally {
      Date.now = realNow;
    }

    expect(getQueueSnapshot()).toHaveLength(0);
    const firstCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/txn-1/category"));
    expect(firstCall?.[1]?.method).toBe("PUT");
    expect(JSON.parse(String(firstCall?.[1]?.body))).toEqual({
      category_id: "cat-oepnv",
      expected_version: 4,
    });
  });

  it("reiht eine Mehrfachzuordnung in einem Schreibvorgang ein und ueberlebt den Neustart", async () => {
    // AsyncStorage.setItem ist im Mock bereits eine jest.fn - jest.spyOn gibt
    // sie dann unveraendert zurueck, inklusive der Aufrufe frueherer Tests.
    const setItem = jest.spyOn(AsyncStorage, "setItem");
    setItem.mockClear();
    await enqueueEntries(
      Array.from({ length: 25 }, (_, index) =>
        createEntry({
          entryId: `bulk-${index}`,
          localId: `txn-${index}`,
          kind: "category",
          payload: { category_id: "cat-supermarkt" },
          expectedVersion: 1,
        }),
      ),
    );

    expect(getQueueSnapshot()).toHaveLength(25);
    expect(setItem).toHaveBeenCalledTimes(1);

    await simulateAppRestart();
    expect(getQueueSnapshot()).toHaveLength(25);
    setItem.mockRestore();
  });

  it("legt einen Versionskonflikt der Kategoriezuordnung in die Konfliktansicht", async () => {
    await enqueueEntry(
      createEntry({
        entryId: "entry-cat",
        localId: "txn-1",
        kind: "category",
        payload: { category_id: "cat-1" },
        expectedVersion: 3,
      }),
    );
    fetchMock.mockImplementation(async () =>
      jsonResponse(409, {
        detail: "version_conflict",
        expected_version: 3,
        current_version: 5,
        current: serverDetail(),
      }),
    );
    await flush();

    expect(getQueueSnapshot()[0]).toMatchObject({
      status: "conflict",
      conflict: { kind: "version_conflict", currentVersion: 5 },
    });
  });

  it("behandelt eine zwischenzeitlich aufgeteilte Buchung als endgueltige Ablehnung", async () => {
    // 409 OHNE Versionsstand: kein Nebenlaeufigkeitsproblem, sondern eine
    // fachliche Ablehnung - Wiederholen wuerde daran nichts aendern.
    await enqueueEntry(
      createEntry({
        entryId: "entry-cat-split",
        localId: "txn-1",
        kind: "category",
        payload: { category_id: "cat-1" },
        expectedVersion: 3,
      }),
    );
    fetchMock.mockImplementation(async () =>
      jsonResponse(409, { detail: "transaction_has_multiple_splits" }),
    );
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getQueueSnapshot()[0]).toMatchObject({
      status: "conflict",
      conflict: { kind: "rejected", status: 409 },
    });
  });

  it("sendet nichts, solange kein Access-Token vorliegt", async () => {
    configureQueueProcessor(() => null);
    fetchMock.mockImplementation(async () => jsonResponse(201, serverDetail()));
    await enqueueEntry(createEntry());
    await flush();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(getQueueSnapshot()).toHaveLength(1);
  });
});
