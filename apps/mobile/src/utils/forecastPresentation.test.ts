/// <reference types="jest" />
import type {
  ForecastDayGroup,
  ForecastMetrics,
  ForecastQuality,
  ForecastResponse,
  ForecastWarning,
} from "../api/types";
import {
  buildForecastSparkline,
  describeForecastBasis,
  describeForecastChain,
  describeForecastFigures,
  describeForecastHints,
  describeForecastMonths,
  describeForecastQuality,
  describeForecastVerdict,
  describePaymentGroups,
  selectNextPayments,
} from "./forecastPresentation";

// Reine Logik, ohne Renderer geprueft (Vorbild budgetPresentation.test.ts).
// Der Schwerpunkt liegt auf der RANGFOLGE der einen Aussage: sie ist die
// einzige Stelle, an der Karte und Screen fachlich auseinanderlaufen koennten.

function makeMetrics(overrides: Partial<ForecastMetrics> = {}): ForecastMetrics {
  return {
    lowest_balance_minor: 120000,
    lowest_balance_on: "2026-09-30",
    first_negative_on: null,
    first_below_buffer_on: null,
    recovery_on: null,
    days_below_zero: 0,
    days_below_buffer: 0,
    needed_against_negative_minor: 0,
    needed_for_buffer_minor: 0,
    average_monthly_surplus_minor: 45000,
    income_monthly_minor: 320000,
    fixed_monthly_minor: 180000,
    variable_monthly_minor: 95000,
    savings_rate_percent: 14,
    fixed_cost_ratio_percent: 56,
    reserve_without_income_months: 3.4,
    balance_in_3_months_minor: 150000,
    balance_in_6_months_minor: 200000,
    balance_in_12_months_minor: 300000,
    balance_in_24_months_minor: 500000,
    ...overrides,
  };
}

function makeQuality(overrides: Partial<ForecastQuality> = {}): ForecastQuality {
  return {
    level: "good",
    last_booking_on: "2026-08-12",
    contract_count: 12,
    income_count: 2,
    variable_basis: "budget",
    excluded_account_names: [],
    contracts_ending_in_horizon: 0,
    ...overrides,
  };
}

function makeForecast(overrides: Partial<ForecastResponse> = {}): ForecastResponse {
  return {
    as_of: "2026-08-14",
    horizon_months: 3,
    granularity: "day",
    opening_liquidity_minor: 240000,
    bound_reserve_minor: 50000,
    safety_buffer_minor: 100000,
    daily: [],
    monthly: [],
    metrics: makeMetrics(),
    cause: null,
    warnings: [],
    quality: makeQuality(),
    ...overrides,
  };
}

function warning(code: string, level: ForecastWarning["level"] = "critical"): ForecastWarning {
  return { code, level, context: {} };
}

function ninetyDays(balances: number[]): ForecastResponse["daily"] {
  return balances.map((balance, index) => ({
    on: `2026-08-${String(15 + index).padStart(2, "0")}`,
    opening_balance_minor: balance,
    variable_minor: 0,
    balance_minor: balance,
    is_event: false,
    flows: [],
  }));
}

describe("describeForecastVerdict", () => {
  it("fordert zuerst aktuelle Kontodaten, auch wenn ein Engpass gerechnet wurde", () => {
    const verdict = describeForecastVerdict(
      makeForecast({
        quality: makeQuality({ level: "insufficient", last_booking_on: "2026-07-02" }),
        warnings: [warning("stale_account_data", "watch"), warning("below_zero")],
        metrics: makeMetrics({
          first_negative_on: "2026-10-22",
          needed_against_negative_minor: 24500,
        }),
      }),
    );

    expect(verdict.kind).toBe("stale_data");
    expect(verdict.headline).toBe("Kontodaten aktualisieren");
    expect(verdict.detail).toBe("Letzte Buchung am 2. Juli 2026.");
    expect(verdict.blocksNumbers).toBe(false);
  });

  it("nennt bei fehlender Einnahmequelle die richtige Ursache statt 'Kontodaten aktualisieren'", () => {
    // Beide Codes fuehren serverseitig zu quality.level = "insufficient" -
    // eine Aufforderung, Kontodaten zu aktualisieren, waere hier falsch.
    const verdict = describeForecastVerdict(
      makeForecast({
        quality: makeQuality({ level: "insufficient", income_count: 0 }),
        warnings: [warning("no_income_sources")],
      }),
    );

    expect(verdict.kind).toBe("no_income");
    expect(verdict.headline).toBe("Keine Einnahmequelle gepflegt");
    // Sperrhinweis statt Zahlen (Plan 8.3).
    expect(verdict.blocksNumbers).toBe(true);
  });

  it("meldet ein heute schon negatives Konto als Ist-Befund, nicht als Prognose", () => {
    const verdict = describeForecastVerdict(
      makeForecast({
        opening_liquidity_minor: -12000,
        metrics: makeMetrics({
          first_negative_on: "2026-08-14",
          needed_against_negative_minor: 12000,
          lowest_balance_minor: -30000,
          lowest_balance_on: "2026-08-28",
        }),
        warnings: [warning("below_zero")],
      }),
    );

    expect(verdict.kind).toBe("already_negative");
    expect(verdict.headline).toBe("Dein Konto ist heute schon im Minus");
    expect(verdict.detail).toBe("Es fehlen 120,00 € bis null.");
    expect(verdict.headline).not.toMatch(/^Am /);
  });

  it("nennt Tag und Fehlbetrag des Engpasses", () => {
    const verdict = describeForecastVerdict(
      makeForecast({
        metrics: makeMetrics({
          first_negative_on: "2026-10-22",
          needed_against_negative_minor: 24500,
          lowest_balance_minor: -18000,
          lowest_balance_on: "2026-10-28",
        }),
        warnings: [warning("below_zero")],
      }),
    );

    expect(verdict.kind).toBe("shortfall");
    expect(verdict.headline).toBe("Am 22.10. fehlen 245,00 €");
    expect(verdict.detail).toBe("Tiefster Stand -180,00 € am 28.10.");
    // Vorlesehilfen bekommen das Datum ausgeschrieben.
    expect(verdict.accessibilityLabel).toContain("22. Oktober 2026");
  });

  it("meldet den Sicherheitspuffer, wenn kein Minus, aber eine Unterschreitung ansteht", () => {
    const verdict = describeForecastVerdict(
      makeForecast({
        metrics: makeMetrics({
          first_below_buffer_on: "2026-09-23",
          needed_for_buffer_minor: 34000,
        }),
        warnings: [warning("below_buffer", "watch")],
      }),
    );

    expect(verdict.kind).toBe("below_buffer");
    expect(verdict.headline).toBe("Unter Sicherheitspuffer am 23.09.");
    expect(verdict.detail).toBe("Es fehlen 340,00 € für den Puffer von 1.000,00 €.");
  });

  it("nennt im gesunden Fall den niedrigsten Stand mit der Laenge des Tagesfensters", () => {
    const verdict = describeForecastVerdict(
      makeForecast({
        daily: ninetyDays(Array.from({ length: 90 }, () => 200000)),
        metrics: makeMetrics({ lowest_balance_minor: 120000, lowest_balance_on: "2026-09-30" }),
      }),
    );

    expect(verdict.kind).toBe("healthy");
    expect(verdict.headline).toBe("Niedrigster Stand in den nächsten 90 Tagen: 1.200,00 €");
    expect(verdict.tone).toBe("positive");
  });

  it("faellt ohne Tagesreihe auf den Monatshorizont zurueck statt '0 Tage' zu behaupten", () => {
    const verdict = describeForecastVerdict(
      makeForecast({ granularity: "month", horizon_months: 24, daily: [] }),
    );

    expect(verdict.headline).toContain("in den nächsten 24 Monaten");
  });
});

describe("describeForecastFigures", () => {
  it("beantwortet 'wie viel fehlt' ohne Kopfrechnen", () => {
    const figures = describeForecastFigures(
      makeForecast({
        metrics: makeMetrics({
          lowest_balance_minor: -18000,
          lowest_balance_on: "2026-10-28",
          first_negative_on: "2026-10-22",
          needed_against_negative_minor: 24500,
          needed_for_buffer_minor: 124500,
          reserve_without_income_months: 2.25,
        }),
      }),
    );

    expect(figures.map((figure) => figure.label)).toEqual([
      "Tiefster Stand",
      "Nötig gegen das Minus",
      "Nötig für den Puffer",
      "Reserve ohne Einnahmen",
    ]);
    expect(figures[1]?.value).toBe("245,00 €");
    expect(figures[1]?.hint).toBe("einmalig vor dem 22.10.");
    // Nicht "Fixkosten gedeckt fuer X Monate" - das wurde als Kennzahl MIT
    // Einkommen missverstanden (Plan 15.1).
    expect(figures[3]?.value).toBe("2,3 Monate");
    expect(figures[3]?.hint).toBe("wenn ab heute nichts hereinkommt");
  });

  it("sagt ausdruecklich 'kein Minus im Zeitraum' statt eines leeren Hinweises", () => {
    const figures = describeForecastFigures(makeForecast());
    expect(figures[1]?.hint).toBe("kein Minus im Zeitraum");
  });
});

describe("describeForecastChain", () => {
  it("bleibt leer, solange kein Ausloesetag bekannt ist", () => {
    expect(describeForecastChain(makeForecast())).toEqual([]);
  });

  it("verbindet Ausloesetag, Minustag, Tiefpunkt und Ausgleich", () => {
    const chain = describeForecastChain(
      makeForecast({
        cause: {
          on: "2026-10-01",
          total_minor: -132000,
          flows: [
            { label: "Miete", amount_minor: -95000, source_kind: "contract", source_id: null },
            {
              label: "KFZ-Versicherung",
              amount_minor: -37000,
              source_kind: "contract",
              source_id: null,
            },
          ],
        },
        metrics: makeMetrics({
          first_negative_on: "2026-10-22",
          lowest_balance_minor: -18000,
          lowest_balance_on: "2026-10-28",
          recovery_on: "2026-11-01",
        }),
      }),
    );

    expect(chain.map((step) => step.what)).toEqual([
      "Miete + KFZ-Versicherung",
      "Konto unter null",
      "Tiefster Stand",
      "Wieder im Plus",
    ]);
    expect(chain[0]?.when).toBe("01.10.");
    expect(chain[0]?.amount).toBe("-1.320,00 €");
    expect(chain[3]?.tone).toBe("positive");
  });

  it("kuerzt mehr als zwei Ausloeseposten, statt die Zeile unlesbar zu machen", () => {
    const chain = describeForecastChain(
      makeForecast({
        cause: {
          on: "2026-10-01",
          total_minor: -180000,
          flows: ["Miete", "Strom", "Versicherung", "Kredit"].map((label) => ({
            label,
            amount_minor: -45000,
            source_kind: "contract" as const,
            source_id: null,
          })),
        },
        metrics: makeMetrics({ first_negative_on: "2026-10-22" }),
      }),
    );

    expect(chain[0]?.what).toBe("Miete + Strom und 2 weitere");
  });
});

describe("buildForecastSparkline", () => {
  it("liefert nichts, solange keine Strecke entsteht", () => {
    expect(buildForecastSparkline([])).toBeNull();
    expect(buildForecastSparkline(ninetyDays([1000]))).toBeNull();
  });

  it("verbindet je zwei Tage zu einem Segment und bleibt im Zeichenfeld", () => {
    const sparkline = buildForecastSparkline(ninetyDays([10000, 5000, -5000, 0]), {
      bufferMinor: 2000,
    })!;

    expect(sparkline.segments).toHaveLength(3);
    for (const segment of sparkline.segments) {
      expect(segment.topPercent).toBeGreaterThanOrEqual(0);
      expect(segment.topPercent + segment.heightPercent).toBeLessThanOrEqual(100.0001);
      expect(segment.widthPercent).toBeCloseTo(100 / 3);
    }
    // Der Hoechstwert liegt oben, der Tiefstwert unten.
    expect(sparkline.segments[0]?.topPercent).toBeCloseTo(0);
    expect(sparkline.segments[1]?.belowZero).toBe(true);
    expect(sparkline.segments[0]?.belowZero).toBe(false);
  });

  it("haelt Null- und Pufferlinie in der Skala, damit sie sichtbar bleiben", () => {
    const sparkline = buildForecastSparkline(ninetyDays([30000, 25000, 20000]), {
      bufferMinor: 10000,
    })!;

    // Alle Werte liegen ueber null; die Skala reicht trotzdem bis null.
    expect(sparkline.zeroPercent).toBeCloseTo(100);
    expect(sparkline.bufferPercent).toBeCloseTo(200 / 3);
    expect(sparkline.lowestLabel).toBe("200,00 €");
    expect(sparkline.highestLabel).toBe("300,00 €");
  });

  it("gibt einem waagerechten Verlauf eine sichtbare Mindesthoehe", () => {
    const sparkline = buildForecastSparkline(ninetyDays([5000, 5000, 5000]))!;
    for (const segment of sparkline.segments) {
      expect(segment.heightPercent).toBeGreaterThan(0);
    }
  });

  it("beschneidet das Fenster auf die gewuenschte Tageszahl", () => {
    const sparkline = buildForecastSparkline(
      ninetyDays(Array.from({ length: 10 }, (_, index) => index * 1000)),
      { maxDays: 4 },
    )!;
    expect(sparkline.segments).toHaveLength(3);
    expect(sparkline.accessibilityLabel).toContain("über 4 Tage");
  });
});

describe("selectNextPayments", () => {
  function group(on: string, flows: number): ForecastDayGroup {
    return {
      on,
      total_minor: -1000 * flows,
      balance_after_minor: 100000,
      is_heavy: false,
      flows: Array.from({ length: flows }, (_, index) => ({
        label: `Posten ${index + 1}`,
        amount_minor: -1000,
        source_kind: "contract" as const,
        source_id: null,
      })),
    };
  }

  it("zeigt einen Tag nie halb, auch wenn dabei mehr als fuenf Posten entstehen", () => {
    const selected = selectNextPayments([group("2026-08-20", 2), group("2026-08-25", 6)], 5);

    expect(selected).toHaveLength(2);
    expect(selected[1]?.flows).toHaveLength(6);
  });

  it("hoert auf, sobald fuenf Posten beisammen sind", () => {
    const selected = selectNextPayments(
      [group("2026-08-20", 3), group("2026-08-21", 2), group("2026-08-22", 1)],
      5,
    );

    expect(selected.map((entry) => entry.on)).toEqual(["2026-08-20", "2026-08-21"]);
  });
});

describe("describePaymentGroups", () => {
  it("liefert Tagessumme, Posten und den Stand danach", () => {
    const [display] = describePaymentGroups([
      {
        on: "2026-10-01",
        total_minor: -132000,
        balance_after_minor: -4500,
        is_heavy: true,
        flows: [
          { label: "Miete", amount_minor: -95000, source_kind: "contract", source_id: null },
          { label: "Gehalt", amount_minor: 320000, source_kind: "contract", source_id: null },
        ],
      },
    ]);

    expect(display?.dayLabel).toBe("01.10.");
    expect(display?.totalLabel).toBe("-1.320,00 €");
    expect(display?.balanceAfterLabel).toBe("-45,00 €");
    expect(display?.balanceAfterIsNegative).toBe(true);
    expect(display?.flows[1]?.isNegative).toBe(false);
    expect(display?.accessibilityLabel).toContain("mehrere große Fälligkeiten");
  });
});

describe("describeForecastMonths", () => {
  it("markiert den kritischen Monat und beschriftet jeden Wert", () => {
    const months = describeForecastMonths(
      makeForecast({
        metrics: makeMetrics({ first_negative_on: "2026-10-22" }),
        monthly: [
          {
            year: 2026,
            month: 9,
            income_minor: 320000,
            fixed_minor: -180000,
            variable_minor: -95000,
            net_minor: 45000,
            closing_balance_minor: 285000,
          },
          {
            year: 2026,
            month: 10,
            income_minor: 320000,
            fixed_minor: -312000,
            variable_minor: -95000,
            net_minor: -87000,
            closing_balance_minor: -18000,
          },
        ],
      }),
    );

    expect(months.map((month) => month.title)).toEqual(["September 2026", "Oktober 2026"]);
    expect(months[0]?.isCritical).toBe(false);
    expect(months[1]?.isCritical).toBe(true);
    expect(months[1]?.closingIsNegative).toBe(true);
    expect(months[1]?.accessibilityLabel).toContain("kritischer Monat");
  });

  it("zeigt hoechstens die gewuenschte Zahl an Monatskarten", () => {
    const monthly = Array.from({ length: 12 }, (_, index) => ({
      year: 2026,
      month: index + 1,
      income_minor: 0,
      fixed_minor: 0,
      variable_minor: 0,
      net_minor: 0,
      closing_balance_minor: 0,
    }));
    expect(describeForecastMonths(makeForecast({ monthly }))).toHaveLength(6);
  });
});

describe("describeForecastQuality", () => {
  it("nennt die Fakten, aus denen die Ampel entsteht", () => {
    const quality = describeForecastQuality(
      makeForecast({
        quality: makeQuality({
          level: "limited",
          variable_basis: "history",
          excluded_account_names: ["Tagesgeld"],
          contracts_ending_in_horizon: 2,
        }),
      }),
    );

    expect(quality.label).toBe("eingeschränkt");
    expect(quality.tone).toBe("watch");
    expect(quality.facts).toEqual([
      "Kontostände bis 12.08.",
      "12 Verträge, davon 2 Einnahmen",
      "Variable Ausgaben aus der Historie",
      "Nicht einbezogen: Tagesgeld",
      "2 Verträge enden im Zeitraum",
    ]);
  });
});

describe("describeForecastHints", () => {
  it("wiederholt die Aussage nicht und zeigt keine internen Codes", () => {
    const hints = describeForecastHints(
      makeForecast({
        warnings: [
          warning("below_zero"),
          warning("below_buffer", "watch"),
          warning("variable_from_history", "info"),
          warning("brandneuer_code_vom_server", "watch"),
        ],
      }),
    );

    expect(hints.map((hint) => hint.key)).toEqual(["variable_from_history"]);
    expect(hints[0]?.label).toBe("Variable Ausgaben aus der Historie");
  });
});

describe("describeForecastBasis", () => {
  it("erklaert die Grundlage in Nutzersprache und setzt nur Geldwerte in Monoschrift", () => {
    const rows = describeForecastBasis(
      makeForecast({ quality: makeQuality({ excluded_account_names: ["Tagesgeld", "Depot"] }) }),
    );

    const byKey = Object.fromEntries(rows.map((row) => [row.key, row]));
    expect(byKey.opening?.value).toBe("2.400,00 €");
    expect(byKey.opening?.hint).toBe("Stand 14. August 2026");
    // Ruecklagen werden ausgewiesen, nicht abgezogen (Plan 4.4).
    expect(byKey.reserve?.hint).toBe("ausgewiesen, nicht abgezogen");
    expect(byKey.excluded?.value).toBe("Tagesgeld, Depot");
    expect(byKey.excluded?.isAmount).toBe(false);
    expect(byKey.buffer?.isAmount).toBe(true);
  });
});
