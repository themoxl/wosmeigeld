// Texte und Rechnungen fuer die Oberflaeche des KI-Felds. Rein, damit die
// Komponenten-Dateien nur Komponenten exportieren (react-refresh) und die
// Regeln ohne DOM pruefbar sind.

import { aiLoadModelHint, aiReasonActions } from "./aiReasons";
import type {
  ChatDetail,
  ChatMessage,
  ContextMeter,
  MessageStatus,
  NumberCheckItem,
  ReasoningCapability,
  ReasoningOverride,
  RunBudget,
} from "./assistantTypes";

export const formatCount = (value: number | null | undefined): string =>
  value === null || value === undefined ? "–" : value.toLocaleString("de-DE");

export const formatSeconds = (ms: number | null | undefined): string =>
  ms === null || ms === undefined
    ? "–"
    : `${(ms / 1000).toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`;

export function plural(count: number, one: string, many: string): string {
  return `${formatCount(count)} ${count === 1 ? one : many}`;
}

export function formatDateTimeShort(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Kontextbalken
// ---------------------------------------------------------------------------

export interface MeterView {
  percent: number;
  /** E > W - R: die Eingabe reichte in die Antwortreserve */
  full: boolean;
  label: string;
  tooltip: string;
}

export function describeMeter(meter: ContextMeter | null | undefined): MeterView | null {
  if (!meter || !(meter.window_tokens > 0) || !(meter.input_tokens >= 0)) return null;
  const { input_tokens: e, window_tokens: w, reserve_tokens: r } = meter;
  const percent = Math.round((e / w) * 100);
  const full = r !== null && e > w - r;
  const origin = meter.exact
    ? "vom Modellserver gemessen"
    : "geschätzt – der Modellserver meldete keine Token";
  const threshold =
    r !== null
      ? `; Verdichtungsschwelle ${formatCount(w - r)} Token (Fenster ${formatCount(w)} − Antwortreserve ${formatCount(r)})`
      : "; die Antwortreserve ist unbekannt";
  return {
    percent,
    full,
    label: `${percent} % · ${formatCount(e)} / ${formatCount(w)}`,
    tooltip:
      `Eingabe des letzten Modellaufrufs (Aufruf ${meter.call_nr}), ${origin}, ` +
      `im Fenster von ${formatCount(w)} Token${threshold}. Die nächste Frage kommt hinzu.`,
  };
}

/** "Fenster W · Eingabe E exakt/geschätzt · Abstand A · Reserve R · Antwortbudget" */
export function describeBudget(budget: RunBudget): string {
  return [
    `Fenster ${formatCount(budget.W)}`,
    `Eingabe ${formatCount(budget.E)} ${budget.exact ? "exakt" : "geschätzt"}`,
    `Abstand ${formatCount(budget.A)}`,
    `Reserve ${formatCount(budget.R)}`,
    `Antwortbudget ${formatCount(budget.max_tokens)}`,
  ].join(" · ");
}

export function describeBudgetParts(budget: RunBudget): string {
  const { system, history, tool_results, tool_defs, current } = budget.parts;
  const origin = budget.exact
    ? `exakt: Grundlage ist die gemessene Eingabe des Aufrufs davor, geschätzt ist nur das Neue (ganz geschätzt wären es ${formatCount(budget.E_estimated)} Token)`
    : "geschätzt aus den Zeichen des Auftrags";
  return (
    `System ${formatCount(system)} + Verlauf ${formatCount(history)} + Werkzeugergebnisse ${formatCount(tool_results)}` +
    ` + Werkzeugbeschreibungen ${formatCount(tool_defs)} + aktuelle Frage ${formatCount(current)} Token. ` +
    `Eingabe ${origin}. Frei im Fenster: ${formatCount(budget.W_free)} Token.`
  );
}

// ---------------------------------------------------------------------------
// Denken-Schalter
// ---------------------------------------------------------------------------

export function reasoningHint(
  value: ReasoningOverride,
  capability: ReasoningCapability,
): { text: string; locked: boolean } {
  if (capability === "none") {
    return {
      text: "Das geladene Modell kann nicht denken (kein Reasoning) – der Schalter wirkt hier nicht.",
      locked: true,
    };
  }
  if (capability === "always") {
    return {
      text: "Das geladene Modell denkt immer – abschalten lässt es sich nicht.",
      locked: true,
    };
  }
  const base =
    value === "on"
      ? "gründlicher, aber deutlich langsamer"
      : value === "off"
        ? "aus: schnellere Antworten"
        : "wie in Einstellungen → KI eingestellt";
  return {
    text: capability === "unknown" ? `${base} · ob das Modell denken kann, ist unbekannt` : base,
    locked: false,
  };
}

// ---------------------------------------------------------------------------
// Chat-Zustand
// ---------------------------------------------------------------------------

/** Vorderster aktiver Lauf eines Chats: der laufende, sonst der erste wartende. */
export function frontActiveMessage(messages: ChatMessage[]): ChatMessage | null {
  const assistant = messages.filter((message) => message.role === "assistant" && message.run_id);
  return (
    assistant.find((message) => message.status === "running") ??
    assistant.find((message) => message.status === "queued") ??
    null
  );
}

export function countQuestions(messages: ChatMessage[]): number {
  return messages.filter((message) => message.role === "user").length;
}

/** Warum Verdichten gerade nicht geht - null, wenn es geht. */
export function compactBlockReason(
  chat: ChatDetail | null,
  { answerActive, compacting }: { answerActive: boolean; compacting: boolean },
): string | null {
  if (!chat) return "Erst eine Frage stellen – ohne Verlauf gibt es nichts zu verdichten.";
  if (compacting) return "Die Verdichtung läuft gerade.";
  if (answerActive) return "Verdichten geht, wenn die Aufträge dieses Chats fertig sind.";
  if (countQuestions(chat.messages) < 2) {
    return "Zusammengefasst wird, was vor der letzten Frage steht – dafür braucht der Chat mindestens zwei Fragen.";
  }
  return null;
}

// ---------------------------------------------------------------------------
// Fehlerantworten
// ---------------------------------------------------------------------------

export interface ErrorView {
  headline: string;
  /** "Ohne Denken wiederholen" anbieten */
  retryWithoutThinking: boolean;
  /** Verweis auf Einstellungen › KI */
  settings: boolean;
  /** "Verdichten…" anbieten */
  compact: boolean;
  /** Hinweis zum Modellserver ("Modell in LM Studio laden"), sonst null */
  loadModelHint: string | null;
}

// Ueberschriften in der Sprache des Chats. Welche Aktion zu welchem Grund
// gehoert, steht fuer alle KI-Funktionen an EINER Stelle (lib/aiReasons.ts).
const ERROR_HEADLINES: Record<string, string> = {
  model_budget_exhausted: "Das Antwortbudget war aufgebraucht, bevor eine Antwort stand.",
  model_context_unknown: "Das Kontextfenster des Modells ist unbekannt.",
  model_input_too_large: "Die Frage samt Verlauf passt nicht ins Kontextfenster.",
  model_context_overflow: "Der Verlauf passt nicht mehr ins Kontextfenster.",
  model_output_unusable: "Die Antwort des Modells war nicht verwertbar.",
  model_reserve_tight: "Für die Antwort blieb zu wenig Platz im Kontextfenster.",
  model_reasoning_unsupported: "Das Modell kann den gewünschten Denkmodus nicht.",
  ai_disabled: "Die KI ist ausgeschaltet.",
  model_not_chosen: "Für den KI-Anbieter ist kein Modell gewählt.",
  model_key_missing: "Für den KI-Anbieter ist kein API-Schlüssel hinterlegt.",
  model_key_rejected: "Der Anbieter lehnt den API-Schlüssel ab.",
  model_credit_exhausted: "Das Guthaben beim Anbieter ist aufgebraucht.",
  model_rate_limited: "Zu viele Anfragen – der Anbieter bremst gerade (Ratenlimit).",
  model_refused: "Das Modell hat die Antwort verweigert.",
};

export function describeError(status: MessageStatus, code: string | null): ErrorView {
  if (status === "cancelled") {
    return {
      headline: "Abgebrochen.",
      retryWithoutThinking: false,
      settings: false,
      compact: false,
      loadModelHint: null,
    };
  }
  const actions = aiReasonActions(code);
  return {
    headline: (code && ERROR_HEADLINES[code]) || "Die Antwort ist fehlgeschlagen.",
    retryWithoutThinking: actions.retryWithoutThinking,
    settings: actions.settings,
    compact: actions.compact,
    loadModelHint: actions.loadModel ? aiLoadModelHint(code) : null,
  };
}

// ---------------------------------------------------------------------------
// Markierte Zahlen
// ---------------------------------------------------------------------------

export interface TextSegment {
  text: string;
  item: NumberCheckItem | null;
}

/** Zeichenposition in Codepunkten (Python) als UTF-16-Index (JavaScript). */
function codePointToUtf16(content: string, index: number): number {
  let units = 0;
  let points = 0;
  for (const char of content) {
    if (points === index) return units;
    units += char.length;
    points += 1;
  }
  return units;
}

/** Eine gepruefte Zahl als Bereich [start, end) im Antworttext (UTF-16). */
export interface NumberRange {
  start: number;
  end: number;
  item: NumberCheckItem;
}

/**
 * Bereiche der geprueften Zahlen im Antworttext. Die Positionen kommen vom
 * Server; passen sie nicht zum Text (andere Zaehlweise, Codepunkte statt
 * UTF-16), wird umgerechnet bzw. der Zahltext ab der letzten Stelle gesucht.
 * Ueberlappende oder unauffindbare Eintraege bleiben unmarkiert.
 */
export function numberRanges(content: string, items: NumberCheckItem[]): NumberRange[] {
  const sorted = [...items].sort((left, right) => left.start - right.start);
  const ranges: NumberRange[] = [];
  let cursor = 0;
  for (const item of sorted) {
    let range: [number, number] | null = null;
    if (item.end > item.start && content.slice(item.start, item.end) === item.text) {
      range = [item.start, item.end];
    } else {
      const start = codePointToUtf16(content, item.start);
      const end = codePointToUtf16(content, item.end);
      if (end > start && content.slice(start, end) === item.text) {
        range = [start, end];
      } else if (item.text) {
        const found = content.indexOf(item.text, cursor);
        if (found >= 0) range = [found, found + item.text.length];
      }
    }
    if (!range || range[0] < cursor) continue;
    ranges.push({ start: range[0], end: range[1], item });
    cursor = range[1];
  }
  return ranges;
}

/**
 * Teilt den Text zwischen `from` und `to` an den geprueften Zahlen. Eine Zahl,
 * die nur teilweise im Bereich liegt (z. B. von Auszeichnung zerschnitten),
 * bleibt dort unmarkiert.
 */
export function segmentRange(
  content: string,
  ranges: NumberRange[],
  from: number,
  to: number,
): TextSegment[] {
  const segments: TextSegment[] = [];
  let cursor = from;
  for (const range of ranges) {
    if (range.start < from || range.end > to) continue;
    if (range.start > cursor)
      segments.push({ text: content.slice(cursor, range.start), item: null });
    segments.push({ text: content.slice(range.start, range.end), item: range.item });
    cursor = range.end;
  }
  if (cursor < to) segments.push({ text: content.slice(cursor, to), item: null });
  return segments;
}

/** Der ganze Antworttext, geteilt an den geprueften Zahlen. */
export function segmentNumbers(content: string, items: NumberCheckItem[]): TextSegment[] {
  return segmentRange(content, numberRanges(content, items), 0, content.length);
}

export function numberTooltip(item: NumberCheckItem): string {
  if (item.status === "belegt") {
    const sources = item.sources.length ? item.sources.join(", ") : "ein Werkzeugergebnis";
    return `Belegt durch ${sources} – die Zahl steht in den Daten, die die KI abgefragt hat.`;
  }
  return "Ohne Beleg – die Zahl steht in keinem Werkzeugergebnis dieses Laufs. Bitte selbst prüfen.";
}
