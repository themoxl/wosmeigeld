/// <reference types="jest" />
import type { AnalyticsResult, CashflowTrendResponse, FixedCostsMonthData } from "../api/types";
import {
  compareCategoryResults,
  describeCashflow,
  describeFixedCosts,
  describeProjectReports,
  describeScope,
  fairYearPeriods,
  rankDimensionResult,
  reportMonth,
} from "./reportsPresentation";

// Die Berichte sind Behauptungen ueber einen Buchungsbestand. Geprueft wird
// hier genau das, was sie falsch machen koennten, ohne dass es auffiele:
// welcher Monat als Massstab gilt, wie die Rangfolge zustande kommt und ob der
// Jahresvergleich gleich lange Zeitraeume gegenueberstellt.
//
// Ein festes Referenzdatum statt `new Date()`: sonst haengt die Auswahl des
// Entscheidungsmonats vom Testtag ab und die Suite waere am Monatsersten
// zufaellig rot.
const HEUTE = new Date(2026, 7, 15); // 15.08.2026

function trend(
  months: { year: number; month: number; income: number; expense: number }[],
): CashflowTrendResponse {
  return {
    months: months.map((entry) => ({
      year: entry.year,
      month: entry.month,
      income_minor: entry.income,
      expense_minor: entry.expense,
      net_minor: entry.income - entry.expense,
    })),
  };
}

function aggregateResult(
  rows: Record<string, string | number | null>[],
  periodFrom: string | null = null,
  periodTo: string | null = null,
): AnalyticsResult {
  return {
    grain: "aggregate",
    columns: [],
    rows,
    total_row: null,
    row_count: rows.length,
    truncated: false,
    applied_limits: [],
    next_cursor: null,
    query_id: "test",
    period_from: periodFrom,
    period_to: periodTo,
    months_covered: 0,
  };
}

describe("describeCashflow", () => {
  it("nimmt den juengsten abgeschlossenen Monat als Entscheidungsmonat, nicht den laufenden", () => {
    const overview = describeCashflow(
      trend([
        { year: 2026, month: 6, income: 300000, expense: 250000 },
        { year: 2026, month: 7, income: 320000, expense: 240000 },
        // Laufender Monat: erst halb gebucht, taugt nicht als Massstab.
        { year: 2026, month: 8, income: 10000, expense: 5000 },
      ]),
      HEUTE,
    );

    expect(overview?.decisionMonth.key).toBe("2026-07");
    expect(overview?.figures.find((figure) => figure.key === "income")?.value).toBe("3.200,00 €");
  });

  it("vergleicht mit dem vorherigen abgeschlossenen Monat", () => {
    const overview = describeCashflow(
      trend([
        { year: 2026, month: 6, income: 300000, expense: 250000 },
        { year: 2026, month: 7, income: 320000, expense: 240000 },
      ]),
      HEUTE,
    );

    expect(overview?.figures.find((figure) => figure.key === "income")?.comparison).toBe(
      "+200,00 € zum Vormonat",
    );
  });

  it("benennt einen fehlenden Vormonat, statt gegen null zu vergleichen", () => {
    const overview = describeCashflow(
      trend([{ year: 2026, month: 7, income: 320000, expense: 240000 }]),
      HEUTE,
    );

    expect(
      overview?.figures.every((figure) => figure.comparison.includes("Kein vollständiger")),
    ).toBe(true);
  });

  it("kennzeichnet den laufenden Monat in der Monatsreihe und stellt sie neuestes zuerst", () => {
    const overview = describeCashflow(
      trend([
        { year: 2026, month: 7, income: 320000, expense: 240000 },
        { year: 2026, month: 8, income: 10000, expense: 5000 },
      ]),
      HEUTE,
    );

    expect(overview?.months.map((month) => month.key)).toEqual(["2026-08", "2026-07"]);
    expect(overview?.months[0]?.isRunning).toBe(true);
    expect(overview?.months[1]?.isRunning).toBe(false);
  });

  it("liefert null statt Nullen, wenn es keinen einzigen Monat gibt", () => {
    expect(describeCashflow(trend([]), HEUTE)).toBeNull();
  });
});

describe("rankDimensionResult", () => {
  const result = aggregateResult(
    [
      {
        category: "cat-1",
        category_label: "Lebensmittel",
        category_group: "grp-1",
        category_group_label: "Haushalt",
        "date:month": "2026-06-01",
        sum_amount: -20000,
        count_amount: 4,
      },
      {
        category: "cat-1",
        category_label: "Lebensmittel",
        category_group: "grp-1",
        category_group_label: "Haushalt",
        "date:month": "2026-07-01",
        sum_amount: -30000,
        count_amount: 6,
      },
      {
        category: "cat-2",
        category_label: "Strom",
        category_group: "grp-2",
        category_group_label: "Wohnen",
        "date:month": "2026-07-01",
        sum_amount: -10000,
        count_amount: 1,
      },
    ],
    "2026-06-01",
    "2026-07-31",
  );

  it("sortiert nach Betragshoehe, groesste zuerst", () => {
    const ranked = rankDimensionResult(result, "category", "category_group", HEUTE);
    expect(ranked.rows.map((row) => row.label)).toEqual(["Lebensmittel", "Strom"]);
  });

  it("summiert ueber die Monate und mittelt ueber das gesamte Raster, nicht nur ueber belegte Monate", () => {
    const ranked = rankDimensionResult(result, "category", "category_group", HEUTE);
    const strom = ranked.rows.find((row) => row.label === "Strom");

    expect(strom?.sum).toBe(-10000);
    // Zwei Monate im Raster, nur einer belegt: der Schnitt ist die Haelfte.
    expect(strom?.average).toBe(-5000);
    expect(strom?.monthly.map((entry) => entry.value)).toEqual([0, -10000]);
  });

  it("berechnet den Anteil am Betragsvolumen aller Zeilen", () => {
    const ranked = rankDimensionResult(result, "category", "category_group", HEUTE);
    expect(ranked.rows[0]?.share).toBeCloseTo(50000 / 60000);
    expect(ranked.total).toBe(-60000);
  });

  it("uebernimmt die Gruppe als zweite Zeile und die Buchungsanzahl", () => {
    const ranked = rankDimensionResult(result, "category", "category_group", HEUTE);
    expect(ranked.rows[0]?.groupLabel).toBe("Haushalt");
    expect(ranked.rows[0]?.count).toBe(10);
  });

  it("nennt eine Zeile ohne Zuordnung beim Namen, statt sie wegzulassen", () => {
    const ranked = rankDimensionResult(
      aggregateResult(
        [{ category: null, "date:month": "2026-07-01", sum_amount: -5000, count_amount: 2 }],
        "2026-07-01",
        "2026-07-31",
      ),
      "category",
      undefined,
      HEUTE,
    );

    expect(ranked.rows[0]?.label).toBe("Ohne Zuordnung");
    expect(ranked.rows[0]?.id).toBeNull();
  });

  it("haelt zwei gleichnamige Dimensionswerte auseinander", () => {
    const ranked = rankDimensionResult(
      aggregateResult(
        [
          {
            category: "cat-a",
            category_label: "Versicherung",
            "date:month": "2026-07-01",
            sum_amount: -5000,
            count_amount: 1,
          },
          {
            category: "cat-b",
            category_label: "Versicherung",
            "date:month": "2026-07-01",
            sum_amount: -9000,
            count_amount: 1,
          },
        ],
        "2026-07-01",
        "2026-07-31",
      ),
      "category",
      undefined,
      HEUTE,
    );

    expect(ranked.rows).toHaveLength(2);
    expect(ranked.rows.map((row) => row.id)).toEqual(["cat-b", "cat-a"]);
  });
});

describe("fairYearPeriods", () => {
  it("vergleicht im laufenden Jahr nur abgeschlossene Monate - beidseitig", () => {
    const periods = fairYearPeriods(2026, HEUTE);

    expect(periods?.monthCount).toBe(7);
    expect(periods?.selectedFrom).toBe("2026-01-01");
    expect(periods?.selectedTo).toBe("2026-07-31");
    expect(periods?.previousFrom).toBe("2025-01-01");
    expect(periods?.previousTo).toBe("2025-07-31");
  });

  it("nimmt fuer ein vergangenes Jahr die vollen zwoelf Monate", () => {
    const periods = fairYearPeriods(2025, HEUTE);

    expect(periods?.monthCount).toBe(12);
    expect(periods?.selectedTo).toBe("2025-12-31");
    expect(periods?.previousTo).toBe("2024-12-31");
  });

  it("verweigert den Vergleich, solange im laufenden Jahr kein Monat abgeschlossen ist", () => {
    expect(fairYearPeriods(2026, new Date(2026, 0, 20))).toBeNull();
  });

  it("verweigert ein Jahr in der Zukunft", () => {
    expect(fairYearPeriods(2027, HEUTE)).toBeNull();
  });

  it("beruecksichtigt das Schaltjahr im Vorjahreszeitraum", () => {
    const periods = fairYearPeriods(2025, new Date(2025, 2, 5));
    // Bis Ende Februar: 2024 war ein Schaltjahr, 2025 nicht.
    expect(periods?.selectedTo).toBe("2025-02-28");
    expect(periods?.previousTo).toBe("2024-02-29");
  });
});

describe("compareCategoryResults", () => {
  const selected = aggregateResult([
    { category: "cat-1", category_label: "Strom", sum_amount: -120000 },
    { category: "cat-2", category_label: "Urlaub", sum_amount: -50000 },
  ]);
  const previous = aggregateResult([
    { category: "cat-1", category_label: "Strom", sum_amount: -100000 },
  ]);

  it("stellt beide Zeitraeume gegenueber, groesste Veraenderung zuerst", () => {
    const entries = compareCategoryResults(selected, previous);
    expect(entries.map((entry) => entry.label)).toEqual(["Urlaub", "Strom"]);
  });

  it("laesst den Prozentwert weg, wenn es keinen Vorjahreswert gibt", () => {
    const entries = compareCategoryResults(selected, previous);
    const urlaub = entries.find((entry) => entry.label === "Urlaub");

    expect(urlaub?.previous).toBe(0);
    expect(urlaub?.deltaPercent).toBeNull();
    expect(urlaub?.deltaPercentLabel).toBe("kein Vorjahreswert");
  });

  it("rechnet die Veraenderung gegen den Betrag des Vorjahres", () => {
    const strom = compareCategoryResults(selected, previous).find(
      (entry) => entry.label === "Strom",
    );

    expect(strom?.delta).toBe(-20000);
    expect(strom?.deltaPercent).toBeCloseTo(-20);
  });

  it("nimmt auch Kategorien auf, die es nur im Vorjahr gab", () => {
    const entries = compareCategoryResults(
      aggregateResult([]),
      aggregateResult([{ category: "cat-9", category_label: "Abgeschafft", sum_amount: -7000 }]),
    );

    expect(entries).toHaveLength(1);
    expect(entries[0]?.selected).toBe(0);
    expect(entries[0]?.delta).toBe(7000);
  });
});

describe("describeFixedCosts", () => {
  const months: FixedCostsMonthData[] = [
    {
      year: 2026,
      month: 6,
      fixed_costs_minor: 100000,
      contract_count: 5,
      yoy_percent: null,
      by_type: [],
    },
    {
      year: 2026,
      month: 7,
      fixed_costs_minor: 120000,
      contract_count: 6,
      yoy_percent: 8.5,
      by_type: [
        { contract_type: "insurance", amount_minor: 30000 },
        { contract_type: "rent", amount_minor: 90000 },
      ],
    },
  ];

  it("bezieht die Kennzahlen auf den letzten gelieferten Monat", () => {
    const overview = describeFixedCosts(months);
    expect(overview?.latestMonthLabel).toBe("Juli 2026");
    expect(overview?.figures[0]?.value).toBe("1.200,00 €");
    expect(overview?.figures[1]?.value).toBe("14.400,00 €");
  });

  it("sortiert die Vertragsarten nach Betrag und uebersetzt die Schluessel", () => {
    const overview = describeFixedCosts(months);
    expect(overview?.types.map((entry) => entry.label)).toEqual(["Miete & Wohnen", "Versicherung"]);
    expect(overview?.types[0]?.shareLabel).toBe("75 %");
  });

  it("benennt einen fehlenden Vorjahresmonat, statt eine Steigerung zu erfinden", () => {
    const overview = describeFixedCosts([months[0]!]);
    expect(overview?.figures[0]?.comparison).toBe("Vorjahresmonat nicht bekannt");
  });

  it("liefert null ohne Vertraege", () => {
    expect(describeFixedCosts([])).toBeNull();
  });
});

describe("describeProjectReports", () => {
  it("summiert ueber alle Vorhaben und uebernimmt die Abweichung unveraendert", () => {
    const overview = describeProjectReports([
      {
        project_id: "p1",
        project_name: "Haus",
        project_type: "home_build",
        scenario_id: "s1",
        scenario_name: "Basis",
        forecast_cost_minor: 50000000,
        actual_cost_minor: 20000000,
        variance_minor: 30000000,
        financing_cost_minor: 8000000,
        warnings: ["Kostenposition ohne Ist"],
      },
    ]);

    expect(overview?.projectCount).toBe(1);
    expect(overview?.figures.find((figure) => figure.key === "forecast")?.value).toBe(
      "500.000,00 €",
    );
    expect(overview?.entries[0]?.varianceLabel).toBe("+300.000,00 €");
    expect(overview?.entries[0]?.warningCount).toBe(1);
  });

  it("liefert null ohne aktives Vorhaben", () => {
    expect(describeProjectReports([])).toBeNull();
  });
});

describe("describeScope", () => {
  it("nennt fuer Ist-Register Zeitraum, Transferregel und Kontenauswahl", () => {
    expect(describeScope("ueberblick", 12)).toBe(
      "Letzte 12 Monate · Transfers aus · nur Budgetkonten",
    );
  });

  it("unterscheidet vereinbarte Fixkosten von gebuchten Betraegen", () => {
    expect(describeScope("fixkosten", 24)).toBe("Letzte 24 Monate · vereinbarte Vertragsbeträge");
  });

  it("uebernimmt beim Jahresvergleich den Zeitraum aus fairYearPeriods", () => {
    expect(describeScope("vergleich", 12, "Januar–Juli 2026 gegen denselben Zeitraum 2025")).toBe(
      "Januar–Juli 2026 gegen denselben Zeitraum 2025 · nur vollständige Monate",
    );
  });
});

describe("reportMonth", () => {
  it("baut Kalendergrenzen ohne Zeitzonenversatz", () => {
    const month = reportMonth(2026, 2, HEUTE);
    expect(month.from).toBe("2026-02-01");
    expect(month.to).toBe("2026-02-28");
    expect(month.shortLabel).toBe("02/26");
    expect(month.isCurrent).toBe(false);
  });

  it("erkennt den laufenden Monat", () => {
    expect(reportMonth(2026, 8, HEUTE).isCurrent).toBe(true);
  });
});
