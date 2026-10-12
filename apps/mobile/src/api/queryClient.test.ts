/// <reference types="jest" />
import type { Query } from "@tanstack/react-query";
import { persistOptions } from "./queryClient";

// Der persistente Lesecache ist eine Allowlist (PERSISTED_QUERY_KEYS). Diese
// Tests halten die beiden Enden fest: was aufgenommen wurde, bleibt drin -
// und die Liquiditaetsvorschau bleibt draussen
// (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 9: "eine offline
// zwischengespeicherte Prognose ist per Definition veraltet").

const shouldDehydrateQuery = persistOptions.dehydrateOptions?.shouldDehydrateQuery;

function successQuery(queryKey: unknown[], data: unknown): Query {
  return { queryKey, state: { status: "success", data } } as unknown as Query;
}

describe("persistOptions.shouldDehydrateQuery", () => {
  it("ist ueberhaupt gesetzt - ohne Filter wuerde alles geschrieben", () => {
    expect(typeof shouldDehydrateQuery).toBe("function");
  });

  it("sortiert die Liquiditaetsvorschau aus", () => {
    // Beide Query-Formen aus ../hooks/useForecast.ts: Basisvorschau und die
    // nach Tagen gruppierten naechsten Zahlungen.
    const forecast = successQuery(["forecast", "household-1", 3, "day", false], {
      as_of: "2026-08-14",
      daily: [],
    });
    const upcoming = successQuery(["forecast", "household-1", "upcoming", 90], []);

    expect(shouldDehydrateQuery!(forecast)).toBe(false);
    // Die Zahlungsliste ist ein Array und wuerde die Array-Pruefung fuer
    // "transactions" passieren - der Familienfilter muss sie trotzdem fangen.
    expect(shouldDehydrateQuery!(upcoming)).toBe(false);
  });

  it("laesst die drei erlaubten Familien weiterhin durch", () => {
    expect(shouldDehydrateQuery!(successQuery(["accounts", "household-1"], [{ id: "a" }]))).toBe(
      true,
    );
    expect(shouldDehydrateQuery!(successQuery(["categories", "household-1"], [{ id: "c" }]))).toBe(
      true,
    );
    expect(
      shouldDehydrateQuery!(successQuery(["transactions", "household-1"], [{ id: "t" }])),
    ).toBe(true);
  });

  it("schreibt nichts, was noch nicht erfolgreich geladen ist", () => {
    const pending = {
      queryKey: ["accounts", "household-1"],
      state: { status: "pending", data: undefined },
    } as unknown as Query;
    expect(shouldDehydrateQuery!(pending)).toBe(false);
  });
});
