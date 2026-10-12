// Benannte Gruende des KI-Kerns (services/api/app/llm/reasons.py) fuer die
// Oberflaeche - EINE Zuordnung fuer alle KI-Funktionen: Klartext, Stufe und
// die passende Aktion (Link zu Einstellungen › KI, Hinweis "Modell in LM
// Studio laden", im KI-Feld "Verdichten…" bzw. "Ohne Denken wiederholen").
//
// Angezeigt wird immer zuerst der Klartext des Servers (`message`,
// `detail.message`, `error_message`) - er traegt den konkreten Befund. Die
// Saetze hier sind wortgleich mit reasons.py und springen nur ein, wo der
// Server nur den Schluessel liefert (z. B. Warnungen der
// Gesundheitsdokumente). Ein Schluessel allein ist nie die ganze Anzeige.
//
// Rein (ohne React, ohne Import von api.ts), damit die Komponenten-Dateien
// nur Komponenten exportieren und Tests, die lib/api mocken, nicht brechen.

import type { MappingSuggestionReason } from "./types";

/**
 * Alle Gruende des KI-Kerns. Abgeleitet aus dem generierten Vertrag des
 * KI-Spaltenvorschlags: der fuehrt laut Backend-Test JEDEN Grund des Kerns.
 * Kommt dort ein Grund hinzu, scheitert `tsc` hier, bis der Klartext steht.
 */
export type AiReasonCode = Exclude<
  MappingSuggestionReason,
  "ok" | "no_sample_rows" | "suggestion_incomplete"
>;

export type AiReasonLevel = "info" | "warnung" | "fehler";

export interface AiReasonInfo {
  level: AiReasonLevel;
  /** Satz fuer Menschen, wortgleich mit reasons.py */
  text: string;
  /** Zustaendige Einstellung oder Handlung; null, wenn es keine gibt. */
  hint: string | null;
}

/** Pfad der KI-Einstellungen; `tab=ki` waehlt den Reiter. */
export const AI_SETTINGS_PATH = "/einstellungen?tab=ki";

const SETTINGS = "Einstellungen › KI";

export const AI_REASONS: Readonly<Record<AiReasonCode, AiReasonInfo>> = {
  ai_disabled: {
    level: "fehler",
    text: "Die KI ist für diesen Haushalt ausgeschaltet.",
    hint: `${SETTINGS} › KI-Unterstützung einschalten.`,
  },
  model_unavailable: {
    level: "fehler",
    text: "Der Modellserver ist nicht erreichbar oder bietet kein chatfähiges Modell an.",
    hint:
      `LM Studio starten bzw. die Adresse unter ${SETTINGS} › Server prüfen; bei ` +
      "Claude die Internetverbindung des Servers prüfen.",
  },
  model_missing: {
    level: "fehler",
    text:
      "Das eingestellte Modell ist auf dem Modellserver nicht vorhanden. Es wird " +
      "bewusst kein anderes ersatzweise verwendet.",
    hint: `${SETTINGS} › Modell: ein vorhandenes Modell wählen.`,
  },
  model_not_loaded: {
    level: "warnung",
    text:
      "Das Modell ist auf dem Server nicht geladen; der erste Aufruf lädt es nach " +
      "und kann dabei ins Zeitbudget laufen.",
    hint: `${SETTINGS} › Modell laden.`,
  },
  model_context_unknown: {
    level: "fehler",
    text:
      "Das Kontextfenster des Modells ist unbekannt - ohne Fenster lässt sich kein " +
      "Antwortbudget berechnen, deshalb wird nicht gesendet.",
    hint: `Modell in LM Studio laden oder unter ${SETTINGS} › Kontext höchstens einen Wert eintragen.`,
  },
  model_input_too_large: {
    level: "fehler",
    text: "Die Eingabe passt samt Antwortreserve nicht in das Kontextfenster.",
    hint: `Modell mit größerem Fenster laden oder unter ${SETTINGS} › Antwortreserve verkleinern.`,
  },
  model_context_overflow: {
    level: "fehler",
    text: "Der Modellserver meldet, dass das Kontextfenster überläuft.",
    hint:
      `Modell mit größerem Fenster laden oder unter ${SETTINGS} › Kontext höchstens ` +
      "den tatsächlich geladenen Wert eintragen.",
  },
  model_budget_exhausted: {
    level: "fehler",
    text:
      "Das Antwortbudget war aufgebraucht, bevor eine Antwort stand - meist hat " +
      "das Denken (Reasoning) den freien Platz verbraucht.",
    hint: `Reasoning unter ${SETTINGS} ausschalten oder das Modell mit größerem Fenster laden.`,
  },
  model_output_unusable: {
    level: "fehler",
    text: "Die Antwort des Modells war nicht verwertbar.",
    hint: "Ein anderes Modell wählen, wenn das wiederholt auftritt.",
  },
  model_reserve_tight: {
    level: "info",
    text: "Die Antwort brauchte mehr als die Antwortreserve.",
    hint:
      "Bei vollerem Kontext wird früher verdichtet, wenn die Reserve unter " +
      `${SETTINGS} › Antwortreserve größer ist.`,
  },
  model_usage_unavailable: {
    level: "info",
    text:
      "Der Modellserver meldet keinen Tokenverbrauch - die Messung von Zeichen je " +
      "Token bleibt für diesen Aufruf aus.",
    hint: null,
  },
  model_reasoning_unsupported: {
    level: "warnung",
    text: "Das Modell bzw. der Server unterstützt die gewählte Reasoning-Einstellung nicht.",
    hint: `${SETTINGS} › Reasoning auf „Modellstandard“ stellen.`,
  },
  model_schema_unsupported: {
    level: "warnung",
    text: "Der Server lehnt die strukturierte Ausgabe (response_format/Werkzeuge) ab.",
    hint: null,
  },
  model_timeout: {
    level: "fehler",
    text: "Der Modellaufruf hat das Zeitbudget überschritten.",
    hint: `${SETTINGS} › Zeitbudget je Anfrage erhöhen oder ein schnelleres Modell wählen.`,
  },
  model_http_error: {
    level: "fehler",
    text: "Der Modellserver hat den Aufruf mit einem Fehler beantwortet.",
    hint: null,
  },
  model_cancelled: {
    level: "info",
    text: "Der Modellaufruf wurde abgebrochen.",
    hint: null,
  },
  model_settings_invalid: {
    level: "fehler",
    text: "Eine für das Antwortbudget nötige KI-Einstellung fehlt oder ist ungültig.",
    hint: `${SETTINGS} › Kontext und Budget prüfen.`,
  },
  model_cap_above_window: {
    level: "info",
    text: "„Kontext höchstens“ liegt über dem geladenen Fenster und wirkt deshalb nicht.",
    hint: `${SETTINGS} › Kontext höchstens.`,
  },
  // Anbieter mit API-Schluessel (app/llm/providers.py)
  model_not_chosen: {
    level: "fehler",
    text:
      "Für den gewählten Anbieter ist kein Modell ausgewählt. Bei einem Cloud-Anbieter " +
      "wählt der Finanzplaner bewusst keines selbst.",
    hint: `${SETTINGS} › Modell wählen.`,
  },
  model_key_missing: {
    level: "fehler",
    text:
      "Für den gewählten Anbieter ist kein lesbarer API-Schlüssel hinterlegt - es geht " +
      "kein Aufruf hinaus.",
    hint: `${SETTINGS} › API-Schlüssel hinterlegen (nur der Owner).`,
  },
  model_key_rejected: {
    level: "fehler",
    text: "Der Anbieter lehnt den API-Schlüssel ab (ungültig, widerrufen oder ohne Berechtigung).",
    hint:
      `Neuen Schlüssel in der Anthropic-Konsole erzeugen und unter ${SETTINGS} › ` +
      "API-Schlüssel hinterlegen.",
  },
  model_credit_exhausted: {
    level: "fehler",
    text: "Das Guthaben beim Anbieter ist aufgebraucht.",
    hint:
      "Guthaben in der Anthropic-Konsole prüfen oder unter " +
      `${SETTINGS} auf den lokalen Anbieter wechseln.`,
  },
  model_rate_limited: {
    level: "warnung",
    text: "Der Anbieter hat die Anfrage wegen zu vieler Aufrufe (Ratenlimit) abgewiesen.",
    hint: "Später erneut versuchen.",
  },
  model_refused: {
    level: "fehler",
    text: "Das Modell hat die Antwort verweigert (Sicherheitsfilter des Anbieters).",
    hint: "Frage umformulieren oder ein anderes Modell wählen.",
  },
};

export function isAiReasonCode(code: string | null | undefined): code is AiReasonCode {
  return typeof code === "string" && Object.prototype.hasOwnProperty.call(AI_REASONS, code);
}

export function aiReasonInfo(code: string | null | undefined): AiReasonInfo | null {
  return isAiReasonCode(code) ? AI_REASONS[code] : null;
}

/**
 * Klartext wie `describe()` im Backend: Satz, optional Befund, dann
 * "Abhilfe: …". Fuer Stellen, an denen der Server nur den Schluessel liefert.
 * Ein unbekannter Schluessel wird nicht zu einem erfundenen Satz, steht aber
 * auch nie allein da.
 */
export function describeAiReason(code: string, detail?: string | null): string {
  const reason = aiReasonInfo(code);
  if (!reason) {
    return detail
      ? `${detail} (Kennung: ${code})`
      : `Die KI-Anfrage ist fehlgeschlagen (Kennung: ${code}).`;
  }
  const parts = [reason.text];
  if (detail) parts.push(detail);
  if (reason.hint) parts.push(`Abhilfe: ${reason.hint}`);
  return parts.join(" ");
}

// ---------------------------------------------------------------------------
// Aktionen
// ---------------------------------------------------------------------------

export interface AiReasonActions {
  /** Link zu Einstellungen › KI */
  settings: boolean;
  /** Hinweis, das Modell in LM Studio zu laden bzw. LM Studio zu starten */
  loadModel: boolean;
  /** KI-Feld: "Verdichten…" anbieten */
  compact: boolean;
  /** KI-Feld: "Ohne Denken wiederholen" - nur auf Knopfdruck, nie automatisch */
  retryWithoutThinking: boolean;
}

const SETTINGS_CODES: ReadonlySet<string> = new Set<AiReasonCode>([
  "ai_disabled",
  "model_context_unknown",
  "model_settings_invalid",
  "model_reasoning_unsupported",
  "model_budget_exhausted",
  "model_missing",
  "model_timeout",
  // Anbieter mit API-Schluessel: Modell, Schluessel und Anbieter stehen
  // unter Einstellungen › KI. Ratenlimit und Verweigerung haben dort nichts
  // zu korrigieren (spaeter erneut bzw. Frage umformulieren).
  "model_not_chosen",
  "model_key_missing",
  "model_key_rejected",
  "model_credit_exhausted",
]);

const LOAD_MODEL_CODES: ReadonlySet<string> = new Set<AiReasonCode>([
  "model_unavailable",
  "model_missing",
  "model_not_loaded",
]);

const COMPACT_CODES: ReadonlySet<string> = new Set<AiReasonCode>([
  "model_input_too_large",
  "model_context_overflow",
  "model_reserve_tight",
]);

export function aiReasonActions(code: string | null | undefined): AiReasonActions {
  const known = typeof code === "string" ? code : "";
  return {
    settings: SETTINGS_CODES.has(known),
    loadModel: LOAD_MODEL_CODES.has(known),
    compact: COMPACT_CODES.has(known),
    retryWithoutThinking: known === "model_budget_exhausted",
  };
}

const SETTINGS_LABELS: Partial<Record<AiReasonCode, string>> = {
  ai_disabled: "KI unter Einstellungen › KI einschalten",
  model_budget_exhausted: "Reasoning unter Einstellungen › KI umstellen",
  model_reasoning_unsupported: "Reasoning unter Einstellungen › KI umstellen",
  model_missing: "Modell unter Einstellungen › KI wählen",
  model_timeout: "Zeitbudget unter Einstellungen › KI anpassen",
  model_not_chosen: "Modell unter Einstellungen › KI wählen",
  model_key_missing: "API-Schlüssel unter Einstellungen › KI hinterlegen",
  model_key_rejected: "API-Schlüssel unter Einstellungen › KI erneuern",
  model_credit_exhausted: "Anbieter unter Einstellungen › KI prüfen",
};

/** Beschriftung des Links zu den Einstellungen. */
export function aiSettingsLabel(code: string | null | undefined): string {
  return (isAiReasonCode(code) && SETTINGS_LABELS[code]) || "Einstellungen › KI öffnen";
}

/** Hinweis zum Modellserver, sofern der Grund dort zu beheben ist. */
export function aiLoadModelHint(code: string | null | undefined): string | null {
  if (code === "model_unavailable") return "LM Studio starten und ein Modell laden.";
  return LOAD_MODEL_CODES.has(code ?? "") ? "Modell in LM Studio laden." : null;
}

// ---------------------------------------------------------------------------
// Gruende aus Fehlern und Texten
// ---------------------------------------------------------------------------

export interface AiReason {
  code: AiReasonCode;
  /** Klartext des Servers */
  message: string;
}

/**
 * KI-Grund aus einer Fehlerantwort ({"detail": {"code", "message"}}, HTTP 503
 * bzw. 409 bei ai_disabled). Bewusst ueber die Form statt `instanceof
 * ApiError`: so braucht dieses Modul api.ts nicht zur Laufzeit.
 */
export function aiReasonFromError(error: unknown): AiReason | null {
  if (!(error instanceof Error)) return null;
  const code = (error as { code?: unknown }).code;
  if (typeof code !== "string" || !isAiReasonCode(code)) return null;
  return { code, message: error.message };
}

const CODE_PATTERN = /\b(ai_disabled|model_[a-z_]+)\b/g;

/**
 * Erster bekannter Grund in einem Servertext - etwa "… (Kennung:
 * model_timeout)" in `error_message` einer KI-Aufgabe oder "(model_timeout
 * ×2, model_output_unusable)" in der `meldung` des Vertragsabgleichs. Der
 * Text selbst bleibt die Anzeige; der Grund liefert nur die Aktion.
 */
export function aiReasonCodeIn(text: string | null | undefined): AiReasonCode | null {
  if (!text) return null;
  for (const match of text.matchAll(CODE_PATTERN)) {
    if (isAiReasonCode(match[1])) return match[1];
  }
  return null;
}

/**
 * Teilergebnis einer Dublettenpruefung: Paare, zu denen das Modell nach
 * einem Abbruch gar nicht mehr gefragt wurde, kommen als `unsure` mit
 * "Nicht beurteilt: …" (Importzeilen) bzw. "Nicht bewertet: …" (gebuchte
 * Paare). Sie sind KEIN Urteil und zaehlen nicht als beurteilt.
 */
export function isUnassessedVerdict(verdict: string, reason: string): boolean {
  return verdict === "unsure" && /^Nicht (beurteilt|bewertet):/.test(reason.trim());
}
