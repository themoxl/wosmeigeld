import type {
  ForecastDay,
  ForecastDayGroup,
  ForecastQualityLevel,
  ForecastResponse,
} from "../api/types";
import { formatDueDate, formatMonthLabel, formatShortDate } from "./dates";
import { formatMinorUnits } from "./money";

// Reine, ohne Renderer testbare Aufbereitung der Liquiditaetsvorschau
// (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 5; Vorbild
// budgetPresentation.ts). Karte auf "Heute" und Screen "Vorschau" teilen sich
// dieselbe Aussage - stuende die Rangfolge in beiden Komponenten, koennten
// Karte und Screen fuer denselben Haushalt Verschiedenes behaupten.

/** Farbe ist nie alleiniger Bedeutungstraeger - der Ton steuert sie nur zusaetzlich. */
export type ForecastTone = "critical" | "watch" | "positive" | "neutral";

export type ForecastVerdictKind =
  "no_income" | "stale_data" | "already_negative" | "shortfall" | "below_buffer" | "healthy";

export interface ForecastVerdict {
  kind: ForecastVerdictKind;
  /**
   * Ueberschrift des Blocks. "Naechster Engpass" ueber einer Aufforderung,
   * Kontodaten zu aktualisieren, waere eine Behauptung ueber etwas, das die
   * Vorschau in diesem Zustand gerade NICHT weiss.
   */
  title: string;
  /** Die EINE Aussage. Kurz genug fuer die Karte auf "Heute". */
  headline: string;
  /** Ein Satz Begruendung - nie eine Wiederholung der Ueberschrift. */
  detail: string;
  tone: ForecastTone;
  /**
   * Ausformuliert fuer Vorlesehilfen, mit ausgeschriebenen Datumsangaben:
   * "22.10." liest eine Sprachausgabe als "22 Punkt 10 Punkt" vor.
   */
  accessibilityLabel: string;
  /**
   * true, wenn aus dieser Vorschau keine Zahlen gezeigt werden duerfen (Plan
   * Abschnitt 8.3: "Vorschau kennt nur Ausgaben -> Sperrhinweis statt
   * Zahlen"). Veraltete Kontodaten sperren dagegen NICHT: dort ist die
   * Rechnung vollstaendig, nur ihre Grundlage alt - das sagt die
   * Datenqualitaetszeile.
   */
  blocksNumbers: boolean;
}

function hasWarning(forecast: ForecastResponse, code: string): boolean {
  return forecast.warnings.some((warning) => warning.code === code);
}

/** Laenge des tagesgenauen Fensters in Worten, z. B. "in den nächsten 90 Tagen". */
function horizonPhrase(forecast: ForecastResponse): string {
  if (forecast.daily.length > 0) {
    return `in den nächsten ${forecast.daily.length} Tagen`;
  }
  const months = forecast.horizon_months;
  return `in den nächsten ${months} ${months === 1 ? "Monat" : "Monaten"}`;
}

/**
 * Genau eine Aussage, in fester Rangfolge. Reihenfolge und Begruendung:
 *
 * 1. Datenqualitaet `insufficient` - eine Prognose auf ungenuegender
 *    Grundlage ist eine Luege (Leitsatz 6). Der Text richtet sich nach der
 *    Ursache: `no_income_sources` und `stale_account_data` fuehren beide zu
 *    `insufficient` (household_forecast_service._quality_level), verlangen
 *    aber gegensaetzliche Handlungen. "Kontodaten aktualisieren" waere bei
 *    fehlender Einnahmequelle eine falsche Anweisung.
 * 2. Bereits heute unter null - Ist-Befund statt Prognosealarm (Plan 8.3).
 *    `first_negative_on` waere hier der heutige Tag; "Am 14.08. fehlen ..."
 *    kuendigte etwas an, das schon eingetreten ist.
 * 3. `first_negative_on`, 4. `first_below_buffer_on`, 5. der gesunde Fall.
 */
export function describeForecastVerdict(forecast: ForecastResponse): ForecastVerdict {
  const metrics = forecast.metrics;

  if (forecast.quality.level === "insufficient") {
    if (hasWarning(forecast, "no_income_sources")) {
      const headline = "Keine Einnahmequelle gepflegt";
      const detail =
        "Ohne Einnahmen kennt die Vorschau nur Ausgaben. Trag dein Gehalt als Einnahmevertrag ein.";
      return {
        kind: "no_income",
        title: "Datenlage",
        headline,
        detail,
        tone: "critical",
        accessibilityLabel: `${headline}. ${detail}`,
        blocksNumbers: true,
      };
    }
    const headline = "Kontodaten aktualisieren";
    const detail = forecast.quality.last_booking_on
      ? `Letzte Buchung am ${formatDueDate(forecast.quality.last_booking_on)}.`
      : "Für die einbezogenen Konten ist noch keine Buchung erfasst.";
    return {
      kind: "stale_data",
      title: "Datenlage",
      headline,
      detail,
      tone: "watch",
      accessibilityLabel: `${headline}. ${detail}`,
      blocksNumbers: false,
    };
  }

  if (forecast.opening_liquidity_minor < 0) {
    const missing =
      metrics.needed_against_negative_minor > 0
        ? metrics.needed_against_negative_minor
        : Math.abs(forecast.opening_liquidity_minor);
    const headline = "Dein Konto ist heute schon im Minus";
    const detail = `Es fehlen ${formatMinorUnits(missing)} bis null.`;
    return {
      kind: "already_negative",
      title: "Aktueller Stand",
      headline,
      detail,
      tone: "critical",
      accessibilityLabel: `${headline}. ${detail}`,
      blocksNumbers: false,
    };
  }

  if (metrics.first_negative_on) {
    const headline = `Am ${formatShortDate(metrics.first_negative_on)} fehlen ${formatMinorUnits(
      metrics.needed_against_negative_minor,
    )}`;
    // Kein Satzpunkt: formatShortDate() endet bereits auf einen Punkt
    // ("28.10."), sonst stuenden zwei nebeneinander.
    const detail = `Tiefster Stand ${formatMinorUnits(
      metrics.lowest_balance_minor,
    )} am ${formatShortDate(metrics.lowest_balance_on)}`;
    return {
      kind: "shortfall",
      title: "Nächster Engpass",
      headline,
      detail,
      tone: "critical",
      accessibilityLabel:
        `Am ${formatDueDate(metrics.first_negative_on)} fehlen ` +
        `${formatMinorUnits(metrics.needed_against_negative_minor)}. Tiefster Stand ` +
        `${formatMinorUnits(metrics.lowest_balance_minor)} am ${formatDueDate(
          metrics.lowest_balance_on,
        )}.`,
      blocksNumbers: false,
    };
  }

  if (metrics.first_below_buffer_on) {
    const headline = `Unter Sicherheitspuffer am ${formatShortDate(metrics.first_below_buffer_on)}`;
    const detail = `Es fehlen ${formatMinorUnits(
      metrics.needed_for_buffer_minor,
    )} für den Puffer von ${formatMinorUnits(forecast.safety_buffer_minor)}.`;
    return {
      kind: "below_buffer",
      title: "Nächster Engpass",
      headline,
      detail,
      tone: "watch",
      accessibilityLabel: `Unter Sicherheitspuffer am ${formatDueDate(metrics.first_below_buffer_on)}. ${detail}`,
      blocksNumbers: false,
    };
  }

  const headline = `Niedrigster Stand ${horizonPhrase(forecast)}: ${formatMinorUnits(
    metrics.lowest_balance_minor,
  )}`;
  const detail = `Am ${formatShortDate(metrics.lowest_balance_on)} – kein Engpass im Zeitraum.`;
  return {
    kind: "healthy",
    title: "Kein Engpass",
    headline,
    detail,
    tone: "positive",
    accessibilityLabel:
      `Niedrigster Stand ${horizonPhrase(forecast)}: ` +
      `${formatMinorUnits(metrics.lowest_balance_minor)} am ${formatDueDate(
        metrics.lowest_balance_on,
      )}. Kein Engpass im Zeitraum.`,
    blocksNumbers: false,
  };
}

// ---------------------------------------------------------------------------
// Kennzahlen

export interface ForecastFigure {
  key: string;
  label: string;
  value: string;
  hint: string;
  tone: ForecastTone;
}

function formatMonths(value: number): string {
  const rounded = value.toFixed(1).replace(".", ",");
  return `${rounded} Monate`;
}

/**
 * Die vier Zahlen, die "wie viel fehlt" ohne Kopfrechnen beantworten
 * (Plan 8.2). "Reserve ohne Einnahmen" heisst bewusst nicht mehr "Fixkosten
 * gedeckt fuer X Monate" - das wurde als Kennzahl MIT Einkommen missverstanden
 * (Plan 15.1).
 */
export function describeForecastFigures(forecast: ForecastResponse): ForecastFigure[] {
  const metrics = forecast.metrics;
  return [
    {
      key: "lowest",
      label: "Tiefster Stand",
      value: formatMinorUnits(metrics.lowest_balance_minor),
      hint: `am ${formatShortDate(metrics.lowest_balance_on)}`,
      tone: metrics.lowest_balance_minor < 0 ? "critical" : "neutral",
    },
    {
      key: "needed-negative",
      label: "Nötig gegen das Minus",
      value: formatMinorUnits(metrics.needed_against_negative_minor),
      hint: metrics.first_negative_on
        ? `einmalig vor dem ${formatShortDate(metrics.first_negative_on)}`
        : "kein Minus im Zeitraum",
      tone: metrics.needed_against_negative_minor > 0 ? "critical" : "neutral",
    },
    {
      key: "needed-buffer",
      label: "Nötig für den Puffer",
      value: formatMinorUnits(metrics.needed_for_buffer_minor),
      hint: `um ${formatMinorUnits(forecast.safety_buffer_minor)} zu halten`,
      tone: metrics.needed_for_buffer_minor > 0 ? "watch" : "neutral",
    },
    {
      key: "reserve",
      label: "Reserve ohne Einnahmen",
      value: formatMonths(metrics.reserve_without_income_months),
      hint: "wenn ab heute nichts hereinkommt",
      tone: "neutral",
    },
  ];
}

// ---------------------------------------------------------------------------
// Ursachenkette

export interface ForecastChainStep {
  key: string;
  /** Kurzes Datum, z. B. "01.10." */
  when: string;
  what: string;
  amount: string | null;
  tone: ForecastTone;
  accessibilityLabel: string;
}

/** Hoechstens zwei Posten ausschreiben - danach wird die Zeile unlesbar. */
function describeCauseFlows(labels: string[]): string {
  if (labels.length === 0) return "Größter Abfluss";
  if (labels.length <= 2) return labels.join(" + ");
  return `${labels.slice(0, 2).join(" + ")} und ${labels.length - 2} weitere`;
}

/**
 * Ausloesetag -> unter null -> Tiefpunkt -> wieder im Plus. Ohne die Kette
 * wirken Ausloeser und Wirkung willkuerlich: im Referenzhaushalt liegen sie
 * drei Wochen auseinander (Plan 15.1).
 *
 * Leer, wenn kein Ausloesetag bekannt ist - eine Kette, die mit "Konto unter
 * null" beginnt, erklaert nichts.
 */
export function describeForecastChain(forecast: ForecastResponse): ForecastChainStep[] {
  const cause = forecast.cause;
  if (!cause) return [];
  const metrics = forecast.metrics;

  const causeWhat = describeCauseFlows(cause.flows.map((flow) => flow.label));
  const steps: ForecastChainStep[] = [
    {
      key: "cause",
      when: formatShortDate(cause.on),
      what: causeWhat,
      amount: formatMinorUnits(cause.total_minor),
      tone: "critical",
      accessibilityLabel: `${formatDueDate(cause.on)}: ${causeWhat}, ${formatMinorUnits(
        cause.total_minor,
      )}`,
    },
  ];

  if (metrics.first_negative_on) {
    steps.push({
      key: "below-zero",
      when: formatShortDate(metrics.first_negative_on),
      what: "Konto unter null",
      amount: null,
      tone: "critical",
      accessibilityLabel: `${formatDueDate(metrics.first_negative_on)}: Konto unter null`,
    });
  }

  steps.push({
    key: "lowest",
    when: formatShortDate(metrics.lowest_balance_on),
    what: "Tiefster Stand",
    amount: formatMinorUnits(metrics.lowest_balance_minor),
    tone: metrics.lowest_balance_minor < 0 ? "critical" : "watch",
    accessibilityLabel: `${formatDueDate(metrics.lowest_balance_on)}: Tiefster Stand ${formatMinorUnits(
      metrics.lowest_balance_minor,
    )}`,
  });

  if (metrics.recovery_on) {
    steps.push({
      key: "recovery",
      when: formatShortDate(metrics.recovery_on),
      what: "Wieder im Plus",
      amount: null,
      tone: "positive",
      accessibilityLabel: `${formatDueDate(metrics.recovery_on)}: Wieder im Plus`,
    });
  }

  return steps;
}

// ---------------------------------------------------------------------------
// Sparkline (View-basiert, kein Diagrammpaket)

export interface ForecastSparklineSegment {
  key: string;
  /** Alle Werte in Prozent des Zeichenfeldes - die Hoehe in dp kennt nur der Screen. */
  leftPercent: number;
  widthPercent: number;
  topPercent: number;
  heightPercent: number;
  /** true, wenn wenigstens einer der beiden Endpunkte unter null liegt. */
  belowZero: boolean;
}

export interface ForecastSparkline {
  segments: ForecastSparklineSegment[];
  /** Lage der Nulllinie in Prozent von oben; null, wenn null ausserhalb der Skala liegt. */
  zeroPercent: number | null;
  /** Lage der Pufferlinie; null, wenn kein Puffer gesetzt ist. */
  bufferPercent: number | null;
  lowestLabel: string;
  highestLabel: string;
  firstDayLabel: string;
  lastDayLabel: string;
  /** Vollstaendige Ansage - eine Linie aus Views ist fuer Vorlesehilfen sonst stumm. */
  accessibilityLabel: string;
}

/** Duenne Segmente wuerden bei waagerechtem Verlauf ganz verschwinden. */
const MIN_SEGMENT_HEIGHT_PERCENT = 2;

/**
 * Verbindet die Tagesstaende zu einem Streckenzug aus rechteckigen Views: je
 * zwei benachbarte Tage ergeben ein Segment. Bewusst kein Diagrammpaket - die
 * App hat keine Charts, und eine Sparkline ist keiner (Plan Abschnitt 9).
 */
export function buildForecastSparkline(
  days: Pick<ForecastDay, "on" | "balance_minor">[],
  options: { bufferMinor?: number; maxDays?: number } = {},
): ForecastSparkline | null {
  const window = options.maxDays ? days.slice(0, options.maxDays) : days;
  const first = window[0];
  const last = window[window.length - 1];
  // Zwei Punkte sind das Minimum fuer eine Strecke; `first`/`last` sind damit
  // zugleich der Typnachweis fuer den Indexzugriff (noUncheckedIndexedAccess).
  if (window.length < 2 || !first || !last) return null;

  const values = window.map((day) => day.balance_minor);
  const buffer = options.bufferMinor ?? 0;
  // Null und Puffer gehoeren in die Skala, sonst haetten ihre Linien keinen
  // Platz und der Verlauf saehe unkritisch aus, obwohl er es nicht ist.
  const max = Math.max(...values, 0, buffer);
  const min = Math.min(...values, 0);
  const span = max - min;

  const yPercent = (value: number): number => (span === 0 ? 50 : ((max - value) / span) * 100);

  const step = 100 / (window.length - 1);
  const segments: ForecastSparklineSegment[] = [];
  for (let index = 1; index < window.length; index += 1) {
    const previous = window[index - 1];
    const current = window[index];
    if (!previous || !current) continue;
    const from = yPercent(previous.balance_minor);
    const to = yPercent(current.balance_minor);
    const top = Math.min(from, to);
    const height = Math.max(Math.abs(to - from), MIN_SEGMENT_HEIGHT_PERCENT);
    segments.push({
      key: current.on,
      leftPercent: (index - 1) * step,
      widthPercent: step,
      // Bei einer Spitze am unteren Rand wuerde die Mindesthoehe sonst aus dem
      // Feld herauslaufen.
      topPercent: Math.min(top, 100 - height),
      heightPercent: height,
      belowZero: previous.balance_minor < 0 || current.balance_minor < 0,
    });
  }

  const lowest = Math.min(...values);
  const highest = Math.max(...values);

  return {
    segments,
    zeroPercent: min <= 0 && max >= 0 ? yPercent(0) : null,
    bufferPercent: buffer > 0 && buffer >= min && buffer <= max ? yPercent(buffer) : null,
    lowestLabel: formatMinorUnits(lowest),
    highestLabel: formatMinorUnits(highest),
    firstDayLabel: formatShortDate(first.on),
    lastDayLabel: formatShortDate(last.on),
    accessibilityLabel:
      `Verlauf über ${window.length} Tage, von ${formatDueDate(first.on)} bis ` +
      `${formatDueDate(last.on)}. Zwischen ${formatMinorUnits(lowest)} und ` +
      `${formatMinorUnits(highest)}.`,
  };
}

// ---------------------------------------------------------------------------
// Naechste Zahlungen

/**
 * Die naechsten Zahlungen, nach Tagen gruppiert. Gezaehlt werden Posten, nicht
 * Tage - aber ein Tag wird nie halb gezeigt: an einem kritischen Tag ist
 * gerade die vollstaendige Gruppe die Antwort auf "wodurch" (Plan 8.2).
 */
export function selectNextPayments(
  groups: ForecastDayGroup[],
  minPayments = 5,
): ForecastDayGroup[] {
  const selected: ForecastDayGroup[] = [];
  let counted = 0;
  for (const group of groups) {
    if (counted >= minPayments) break;
    selected.push(group);
    counted += group.flows.length;
  }
  return selected;
}

export interface ForecastPaymentGroupDisplay {
  key: string;
  dayLabel: string;
  /** Tagessumme mit Vorzeichen, z. B. "-1.240,00 EUR". */
  totalLabel: string;
  totalIsNegative: boolean;
  isHeavy: boolean;
  balanceAfterLabel: string;
  balanceAfterIsNegative: boolean;
  accessibilityLabel: string;
  flows: { key: string; label: string; amountLabel: string; isNegative: boolean }[];
}

export function describePaymentGroups(groups: ForecastDayGroup[]): ForecastPaymentGroupDisplay[] {
  return groups.map((group) => ({
    key: group.on,
    dayLabel: formatShortDate(group.on),
    totalLabel: formatMinorUnits(group.total_minor),
    totalIsNegative: group.total_minor < 0,
    isHeavy: group.is_heavy,
    balanceAfterLabel: formatMinorUnits(group.balance_after_minor),
    balanceAfterIsNegative: group.balance_after_minor < 0,
    accessibilityLabel:
      `${formatDueDate(group.on)}: ${formatMinorUnits(group.total_minor)}, danach verfügbar ` +
      `${formatMinorUnits(group.balance_after_minor)}` +
      (group.is_heavy ? ", mehrere große Fälligkeiten" : ""),
    flows: group.flows.map((flow, index) => ({
      key: `${flow.label}-${flow.amount_minor}-${index}`,
      label: flow.label,
      amountLabel: formatMinorUnits(flow.amount_minor),
      isNegative: flow.amount_minor < 0,
    })),
  }));
}

// ---------------------------------------------------------------------------
// Monatskarten statt Tabelle

export interface ForecastMonthCard {
  key: string;
  title: string;
  isCritical: boolean;
  incomeLabel: string;
  fixedLabel: string;
  variableLabel: string;
  netLabel: string;
  netIsNegative: boolean;
  closingLabel: string;
  closingIsNegative: boolean;
  accessibilityLabel: string;
}

/**
 * Sechs Spalten laufen auf dem Telefon aus dem Bild (Plan 15.1) - jede
 * Monatszeile wird deshalb zu einer Karte mit Beschriftung je Wert.
 */
export function describeForecastMonths(forecast: ForecastResponse, limit = 6): ForecastMonthCard[] {
  const criticalIso = forecast.metrics.first_negative_on;
  const criticalYear = criticalIso ? Number(criticalIso.slice(0, 4)) : null;
  const criticalMonth = criticalIso ? Number(criticalIso.slice(5, 7)) : null;

  return forecast.monthly.slice(0, limit).map((month) => {
    const isCritical = month.year === criticalYear && month.month === criticalMonth;
    const title = formatMonthLabel(new Date(month.year, month.month - 1, 1));
    return {
      key: `${month.year}-${String(month.month).padStart(2, "0")}`,
      title,
      isCritical,
      incomeLabel: formatMinorUnits(month.income_minor),
      fixedLabel: formatMinorUnits(month.fixed_minor),
      variableLabel: formatMinorUnits(month.variable_minor),
      netLabel: formatMinorUnits(month.net_minor),
      netIsNegative: month.net_minor < 0,
      closingLabel: formatMinorUnits(month.closing_balance_minor),
      closingIsNegative: month.closing_balance_minor < 0,
      accessibilityLabel: `${title}${isCritical ? ", kritischer Monat" : ""}: Einnahmen ${formatMinorUnits(
        month.income_minor,
      )}, Fixkosten ${formatMinorUnits(month.fixed_minor)}, variabel ${formatMinorUnits(
        month.variable_minor,
      )}, Saldo ${formatMinorUnits(month.net_minor)}, Endstand ${formatMinorUnits(
        month.closing_balance_minor,
      )}`,
    };
  });
}

// ---------------------------------------------------------------------------
// Datenqualitaet und Berechnungsgrundlage

const QUALITY_LABELS: Record<ForecastQualityLevel, string> = {
  good: "gut",
  limited: "eingeschränkt",
  insufficient: "unzureichend",
};

const QUALITY_TONES: Record<ForecastQualityLevel, ForecastTone> = {
  good: "positive",
  limited: "watch",
  insufficient: "critical",
};

export interface ForecastQualityDisplay {
  label: string;
  tone: ForecastTone;
  /** Die Fakten, aus denen die Ampel entsteht - Vertrauen vor zweiter Nachkommastelle. */
  facts: string[];
  accessibilityLabel: string;
}

export function describeForecastQuality(forecast: ForecastResponse): ForecastQualityDisplay {
  const quality = forecast.quality;
  const facts = [
    quality.last_booking_on
      ? `Kontostände bis ${formatShortDate(quality.last_booking_on)}`
      : "Keine Buchungen erfasst",
    `${quality.contract_count} Verträge, davon ${quality.income_count} Einnahmen`,
    quality.variable_basis === "history"
      ? "Variable Ausgaben aus der Historie"
      : quality.variable_basis === "max"
        ? "Variable Ausgaben aus Budget oder Historie, je nachdem was höher ist"
        : "Variable Ausgaben aus dem Budgetplan",
  ];
  if (quality.excluded_account_names.length > 0) {
    facts.push(`Nicht einbezogen: ${quality.excluded_account_names.join(", ")}`);
  }
  if (quality.contracts_ending_in_horizon > 0) {
    facts.push(`${quality.contracts_ending_in_horizon} Verträge enden im Zeitraum`);
  }

  const label = QUALITY_LABELS[quality.level];
  return {
    label,
    tone: QUALITY_TONES[quality.level],
    facts,
    accessibilityLabel: `Datenqualität ${label}. ${facts.join(". ")}.`,
  };
}

/** Verstaendliche Texte fuer die stabilen Warncodes (Plan Abschnitt 10). */
const WARNING_LABELS: Record<string, string> = {
  below_zero: "Konto rutscht ins Minus",
  below_buffer: "Unter Sicherheitspuffer",
  stale_account_data: "Kontodaten veraltet",
  no_income_sources: "Keine Einnahmequelle gepflegt",
  income_below_booked: "Gepflegte Einnahmen unter dem gebuchten Schnitt",
  variable_from_history: "Variable Ausgaben aus der Historie",
  no_variable_basis: "Keine Grundlage für variable Ausgaben",
  horizon_beyond_contracts: "Zeitraum reicht über Vertragsenden hinaus",
};

export interface ForecastHint {
  key: string;
  label: string;
  tone: ForecastTone;
}

/**
 * Hinweise unter der Aussage. `below_zero` und `below_buffer` entfallen: die
 * Aussage selbst traegt sie bereits, ein zweites Mal waere nur Laerm.
 *
 * Ein Code ohne Text erscheint GAR NICHT, statt roh durchgereicht zu werden -
 * interne Codes sind fuer den Nutzer unsichtbar (Plan 8.2). Der Preis ist ein
 * stiller neuer Servercode; der ist beim naechsten Blick in diese Tabelle
 * behoben, ein "horizon_beyond_contracts" auf dem Bildschirm nie.
 */
export function describeForecastHints(forecast: ForecastResponse): ForecastHint[] {
  const hints: ForecastHint[] = [];
  for (const warning of forecast.warnings) {
    if (warning.code === "below_zero" || warning.code === "below_buffer") continue;
    const label = WARNING_LABELS[warning.code];
    if (!label) continue;
    hints.push({
      key: warning.code,
      label,
      tone:
        warning.level === "critical" ? "critical" : warning.level === "watch" ? "watch" : "neutral",
    });
  }
  return hints;
}

export interface ForecastBasisRow {
  key: string;
  label: string;
  value: string;
  hint: string | null;
  /** true fuer Geldwerte - nur die werden in der Monoschrift gesetzt. */
  isAmount: boolean;
}

/** "So wird gerechnet" in Nutzersprache - ohne interne Codes. */
export function describeForecastBasis(forecast: ForecastResponse): ForecastBasisRow[] {
  const quality = forecast.quality;
  const rows: ForecastBasisRow[] = [
    {
      key: "opening",
      label: "Startsaldo",
      value: formatMinorUnits(forecast.opening_liquidity_minor),
      hint: `Stand ${formatDueDate(forecast.as_of)}`,
      isAmount: true,
    },
    {
      key: "contracts",
      label: "Verträge",
      value: `${quality.contract_count}`,
      hint:
        quality.income_count > 0
          ? `davon ${quality.income_count} Einnahmen`
          : "keine Einnahmeverträge",
      isAmount: false,
    },
    {
      key: "variable",
      label: "Variable Ausgaben",
      value: formatMinorUnits(forecast.metrics.variable_monthly_minor),
      hint:
        quality.variable_basis === "history"
          ? "pro Monat, aus der Historie"
          : quality.variable_basis === "max"
            ? "pro Monat, aus Budget oder Historie – der größere Wert"
            : "pro Monat, aus dem Budgetplan",
      isAmount: true,
    },
    {
      key: "buffer",
      label: "Sicherheitspuffer",
      value: formatMinorUnits(forecast.safety_buffer_minor),
      hint: "im Web einstellbar",
      isAmount: true,
    },
    {
      key: "reserve",
      label: "Gebunden in Rücklagen",
      value: formatMinorUnits(forecast.bound_reserve_minor),
      // Solange Ruecklagentoepfe keinem Konto zugeordnet sind, wird der
      // gebundene Betrag ausgewiesen und NICHT abgezogen (Plan 4.4).
      hint: "ausgewiesen, nicht abgezogen",
      isAmount: true,
    },
    {
      key: "excluded",
      label: "Nicht einbezogene Konten",
      value:
        quality.excluded_account_names.length > 0
          ? quality.excluded_account_names.join(", ")
          : "keine",
      hint: "Sparen und Anlagen zählen nicht als verfügbar",
      isAmount: false,
    },
  ];
  return rows;
}
