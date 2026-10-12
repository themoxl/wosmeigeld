import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiFetch,
  commitImportRun,
  deleteImportRun,
  suggestImportMapping,
  updateImportRun,
} from "../lib/api";

/**
 * Regressionstests zur Fehlerdarstellung: eine echte Netzwerkstoerung (fetch
 * wirft, es gibt keine Antwort) ist etwas anderes als eine HTTP-Fehlerantwort
 * mit Fehlerkoerper. Frueher wurde beides gleich behandelt, weshalb z. B. ein
 * fehlendes Zielkonto (HTTP 400) als "Die API ist nicht erreichbar" erschien.
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  }) as Response;
}

describe("apiFetch Fehlerbehandlung", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt bei einer HTTP-Fehlerantwort die Meldung des Servers", async () => {
    // Wortlaut wie im Backend (services/api/.../imports.py): seit es PATCH
    // /v1/imports/{id} gibt, nennt die Meldung den nachtraeglichen Weg.
    const detail =
      "Dem Importlauf ist kein Zielkonto zugewiesen. Bitte weise dem Importlauf ein Zielkonto zu - " +
      "das geht nachtraeglich, ein erneuter Upload ist dafuer nicht noetig.";
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(400, { detail }))),
    );

    const error = await commitImportRun("token", "household-1", "run-1").catch(
      (err: unknown) => err,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe(detail);
    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).isNetworkError).toBe(false);
  });

  it("gibt die 409-Statusmeldung des Servers unveraendert weiter", async () => {
    const detail =
      "Importlauf kann nicht uebernommen werden: Status ist 'pending', erwartet wird 'ready'. " +
      "Die Verarbeitung wurde noch nicht gestartet - bitte kurz warten.";
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(409, { detail }))),
    );

    const error = (await commitImportRun("token", "household-1", "run-1").catch(
      (err: unknown) => err,
    )) as ApiError;

    expect(error.message).toBe(detail);
    expect(error.status).toBe(409);
  });

  it("meldet 'nicht erreichbar' nur, wenn fetch selbst wirft", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );

    const error = (await apiFetch("/v1/imports", { token: "t" }).catch(
      (err: unknown) => err,
    )) as ApiError;

    expect(error.message).toContain("Die API ist nicht erreichbar");
    expect(error.isNetworkError).toBe(true);
    expect(error.status).toBeUndefined();
  });

  it("nennt einen 500er einen Serverfehler statt eine Erreichbarkeitsstoerung", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("", { status: 500 }) as Response)),
    );

    const error = (await apiFetch("/v1/imports", { token: "t" }).catch(
      (err: unknown) => err,
    )) as ApiError;

    expect(error.message).toContain("Serverfehler (HTTP 500)");
    expect(error.message).not.toContain("nicht erreichbar");
    expect(error.isNetworkError).toBe(false);
  });

  it("uebersetzt die 403-Kennungen in verstaendlichen Text und behaelt die Kennung", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(403, { detail: "household_role_readonly" }))),
    );

    const error = (await suggestImportMapping("token", "household-1", "run-1").catch(
      (err: unknown) => err,
    )) as ApiError;

    expect(error.message).toContain("auf Lesen beschraenkt");
    expect(error.message).toContain("household_role_readonly");
    expect(error.status).toBe(403);
  });

  it("fasst die Feldmeldungen einer 422-Antwort zusammen", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(422, {
            detail: [{ loc: ["body", "sample_rows"], msg: "List should have at most 20 items" }],
          }),
        ),
      ),
    );

    const error = (await apiFetch("/v1/imports", { token: "t" }).catch(
      (err: unknown) => err,
    )) as ApiError;

    expect(error.message).toContain("List should have at most 20 items");
  });

  it("verwirft eine Klartextantwort ohne detail nicht stillschweigend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response("upstream connect error", { status: 502 }) as Response),
      ),
    );

    const error = (await apiFetch("/v1/imports", { token: "t" }).catch(
      (err: unknown) => err,
    )) as ApiError;

    expect(error.message).toContain("Serverfehler (HTTP 502)");
    expect(error.message).toContain("upstream connect error");
  });
});

describe("updateImportRun / deleteImportRun", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("schickt beim nachtraeglichen Zielkonto nur das gesetzte Feld per PATCH", async () => {
    const run = { id: "run-1", account_id: "account-1", status: "ready" };
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(200, run)));
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateImportRun("token", "household-1", "run-1", {
      account_id: "account-1",
    });

    expect(result.account_id).toBe("account-1");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/v1/imports/run-1");
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe(JSON.stringify({ account_id: "account-1" }));
    const headers = init.headers as Headers;
    expect(headers.get("X-Household-Id")).toBe("household-1");
  });

  it("gibt die 409-Meldung des Servers beim Aendern unveraendert weiter", async () => {
    const detail =
      "Der Importlauf kann nicht mehr geaendert werden: Status ist 'completed', aenderbar ist nur " +
      "ein Lauf im Status 'ready'.";
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(409, { detail }))),
    );

    const error = (await updateImportRun("token", "household-1", "run-1", {
      account_id: "account-1",
    }).catch((err: unknown) => err)) as ApiError;

    expect(error.message).toBe(detail);
    expect(error.status).toBe(409);
    expect(error.isNetworkError).toBe(false);
  });

  it("entfernt einen Importlauf per DELETE und kommt mit 204 ohne Koerper klar", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 204 }) as Response));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteImportRun("token", "household-1", "run-1")).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/v1/imports/run-1");
    expect(init.method).toBe("DELETE");
    expect(init.body).toBeUndefined();
  });

  it("gibt die 409-Meldung des Servers beim Entfernen unveraendert weiter", async () => {
    const detail =
      "Ein bereits uebernommener Importlauf wird nicht entfernt. Aus ihm sind bereits 105 " +
      "Buchungen entstanden, die dadurch still ihre Herkunft verlieren wuerden.";
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(409, { detail }))),
    );

    const error = (await deleteImportRun("token", "household-1", "run-1").catch(
      (err: unknown) => err,
    )) as ApiError;

    expect(error.message).toBe(detail);
    expect(error.status).toBe(409);
  });
});

describe("suggestImportMapping", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("ruft den Vorschlagsendpunkt mit leerem Body auf und liefert das Ergebnis", async () => {
    const suggestion = {
      available: true,
      reason: "ok",
      message: "Spaltenvorschlag erstellt.",
      source_columns: ["Buchungstag"],
      sample_row_count: 3,
      column_mapping: { booked_date: "Buchungstag" },
      date_format: "%d.%m.%Y",
      decimal_separator: ",",
      currency: "EUR",
      confidence: 0.8,
      model_name: "google/gemma-4-26b-a4b-qat",
      prompt_version: "map-v1",
    };
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(200, suggestion)));
    vi.stubGlobal("fetch", fetchMock);

    const result = await suggestImportMapping("token", "household-1", "run-1");

    expect(result.column_mapping?.booked_date).toBe("Buchungstag");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/v1/imports/run-1/mapping-suggestion");
    expect(init.method).toBe("POST");
    expect(init.body).toBe("{}");
  });
});
