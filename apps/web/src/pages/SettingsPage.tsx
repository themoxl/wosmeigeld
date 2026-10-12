import { useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Tabs, TabPanel, type TabDefinition } from "../components/Tabs";
import { BackupPanel } from "../components/BackupPanel";
import { SystemStatusPanel } from "../components/SystemStatusPanel";
import { useAuth } from "../auth/AuthProvider";
import {
  useAIHealth,
  useAIModels,
  useAISettings,
  useLoadAIModel,
  useRemoveAIProviderKey,
  useStoreAIProviderKey,
  useUpdateAISettings,
} from "../hooks/useAISettings";
import { useFormatDate, useUpdateProfile } from "../hooks/useProfile";
import { useHousehold } from "../household/HouseholdProvider";
import {
  ApiError,
  createHouseholdInvite,
  listHouseholdInvites,
  listHouseholdMembers,
  renameHousehold,
  revokeHouseholdInvite,
} from "../lib/api";
import { DATE_FORMAT_LABELS, formatDate, type DateFormatPreference } from "../lib/dates";
import type {
  AIHealthResponse,
  AIKeyStatus,
  AIMeasurementInfo,
  AIModelInfo,
  AIProviderInfo,
  AISamplingProfile,
  AISettings,
  AISettingsField,
  AISettingsUpdate,
  AIWarning,
  HouseholdRole,
  ProviderId,
} from "../lib/types";
import { DesignCard } from "../components/DesignCard";

type ReasoningLevel = NonNullable<AISettings["reasoning"]>;
type SamplingMode = AISettings["sampling_mode"];

const REASONING_LEVEL_LABELS: Record<ReasoningLevel, string> = {
  on: "An",
  off: "Aus",
  minimal: "Minimal",
  low: "Niedrig",
  medium: "Mittel",
  high: "Hoch",
  xhigh: "Sehr hoch",
  max: "Maximal",
};

/** Abgestufte Denktiefe (Effort) - im Unterschied zum reinen An/Aus. */
const REASONING_EFFORT_LEVELS: ReadonlySet<ReasoningLevel> = new Set<ReasoningLevel>([
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
]);

const REASONING_LEVELS = new Set<ReasoningLevel>(
  Object.keys(REASONING_LEVEL_LABELS) as ReasoningLevel[],
);

function availableReasoningLevels(options: string[]): ReasoningLevel[] {
  const levels: ReasoningLevel[] = [];
  for (const rawOption of options) {
    const option = rawOption.toLowerCase() === "none" ? "off" : rawOption.toLowerCase();
    if (REASONING_LEVELS.has(option as ReasoningLevel)) {
      const level = option as ReasoningLevel;
      if (!levels.includes(level)) levels.push(level);
    }
  }
  return levels;
}

function reasoningLabel(value: string): string {
  if (value === "none") return "Aus";
  return REASONING_LEVEL_LABELS[value as ReasoningLevel] ?? value;
}

function samplingSummary(profile: {
  temperature?: number | null;
  top_p?: number | null;
  top_k?: number | null;
  repeat_penalty?: number | null;
  min_p?: number | null;
  presence_penalty?: number | null;
}): string {
  const parts = [
    profile.temperature != null ? `Temperatur ${profile.temperature}` : null,
    profile.top_p != null ? `Top P ${profile.top_p}` : null,
    profile.top_k != null ? `Top K ${profile.top_k}` : null,
    profile.repeat_penalty != null ? `Wiederholung ${profile.repeat_penalty}` : null,
    profile.min_p != null ? `Min P ${profile.min_p}` : null,
    profile.presence_penalty != null ? `Presence ${profile.presence_penalty}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Modell-/Serverstandard (keine Zahlen vorgegeben)";
}

function samplingProfileFor(
  modelId: string | undefined,
  reasoning: "" | ReasoningLevel,
  profiles: AISamplingProfile[],
): AISamplingProfile | undefined {
  if (!modelId) return undefined;
  const lowered = modelId.toLowerCase();
  const thinking = reasoning !== "off";
  return profiles.find((profile) => {
    const markers = profile.model_markers ?? [];
    const mode = profile.reasoning_mode;
    return (
      markers.some((marker) => lowered.includes(marker.toLowerCase())) &&
      (mode === "any" ||
        (mode === "thinking" && thinking) ||
        (mode === "non-thinking" && !thinking))
    );
  });
}

// ---------------------------------------------------------------------------
// Kontext und Budget (docs/KI-BUDGET-UND-KI-FELD-PLAN.md, Etappe 1)
// ---------------------------------------------------------------------------
// Ein festes Ausgabebudget gibt es nicht mehr: jeder Aufruf rechnet aus dem
// geladenen Kontextfenster W (hoechstens "Kontext hoechstens") und laesst die
// Antwortreserve R = W x Anteil frei. Die Vorgaben stehen ausschliesslich im
// Datenbankschema und kommen als `schema_defaults` mit - hier steht keine
// eigene Zahl, auch nicht als Rueckfall.

const INTEGER = new Intl.NumberFormat("de-DE");
const PERCENT = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
const RATIO = new Intl.NumberFormat("de-DE", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 2,
});

/** Anteil (0,25) -> Prozent-Eingabe ("25"). Eine Nachkommastelle genuegt:
 *  die Spalten halten drei Stellen (numeric(4,3)). */
function shareToPercentInput(share: number): string {
  return String(Math.round(share * 1000) / 10);
}

function percentInputToShare(input: string): number {
  return Math.round(Number(input) * 10) / 1000;
}

function formatPercent(share: number): string {
  return `${PERCENT.format(share * 100)} %`;
}

type BudgetField =
  | "context_cap_tokens"
  | "answer_reserve_share"
  | "result_page_share"
  | "run_time_limit_minutes"
  | "start_chars_per_token"
  | "start_estimate_error"
  | "assistant_retention_days";

interface BudgetSpec {
  field: BudgetField;
  /** Kurzname fuer Meldungen und den Zuruecksetzen-Knopf. */
  name: string;
  label: string;
  min: number;
  max: number;
  step: string;
  integer?: boolean;
  /** Leer heisst "keine Grenze" statt "fehlt" (nur "Kontext hoechstens"). */
  optional?: boolean;
  read: (settings: AISettings) => number | null | undefined;
  toInput: (value: number) => string;
  toApi: (input: string) => number;
  format: (value: number) => string;
  rangeError: string;
  note: string;
}

// Die Grenzen spiegeln die CHECKs im Schema (wie AISettingsUpdate), damit ein
// Tippfehler als deutsche Meldung statt als HTTP 422 zurueckkommt.
const BUDGET_FIELDS: BudgetSpec[] = [
  {
    field: "context_cap_tokens",
    name: "Kontext höchstens",
    label: "Kontext höchstens (Tokens)",
    min: 2048,
    max: 1048576,
    step: "1",
    integer: true,
    optional: true,
    read: (settings) => settings.context_cap_tokens,
    toInput: (value) => String(value),
    toApi: (input) => Number(input),
    format: (value) => `${INTEGER.format(value)} Tokens`,
    rangeError:
      "„Kontext höchstens“ muss leer sein oder zwischen 2.048 und 1.048.576 Tokens liegen.",
    note:
      "Gerechnet wird mit dem kleineren Wert aus geladenem Fenster und diesem. Leer = das " +
      "geladene Fenster gilt allein. Meldet der Server kein Fenster, gilt nur dieser Wert – " +
      "ohne beide ruft die KI kein Modell auf.",
  },
  {
    field: "answer_reserve_share",
    name: "Antwortreserve",
    label: "Antwortreserve (% des Fensters)",
    min: 5,
    max: 90,
    step: "any",
    read: (settings) => settings.answer_reserve_share,
    toInput: shareToPercentInput,
    toApi: percentInputToShare,
    format: formatPercent,
    rangeError: "Die Antwortreserve muss zwischen 5 und 90 % liegen.",
    note:
      "So viel vom Fenster bleibt für Antwort und Denken frei. Passt die Eingabe samt " +
      "Reserve nicht hinein, wird nicht gesendet, sondern benannt gemeldet.",
  },
  {
    field: "result_page_share",
    name: "Ergebnisseite",
    label: "Ergebnisseite (% des Fensters)",
    min: 1,
    max: 50,
    step: "any",
    read: (settings) => settings.result_page_share,
    toInput: shareToPercentInput,
    toApi: percentInputToShare,
    format: formatPercent,
    rangeError: "Die Ergebnisseite muss zwischen 1 und 50 % liegen.",
    note: "Größe einer Seite eines Werkzeugergebnisses im KI-Feld.",
  },
  {
    field: "run_time_limit_minutes",
    name: "Laufzeit je KI-Lauf",
    label: "Laufzeit je KI-Lauf (Minuten)",
    min: 1,
    max: 240,
    step: "1",
    integer: true,
    read: (settings) => settings.run_time_limit_minutes,
    toInput: (value) => String(value),
    toApi: (input) => Number(input),
    format: (value) => `${INTEGER.format(value)} Minuten`,
    rangeError: "Die Laufzeit je KI-Lauf muss eine ganze Zahl zwischen 1 und 240 Minuten sein.",
    note: "Höchstdauer eines mehrstufigen Laufs im KI-Feld.",
  },
  {
    field: "start_chars_per_token",
    name: "Startwert Zeichen je Token",
    label: "Startwert Zeichen je Token",
    min: 1,
    max: 8,
    step: "any",
    read: (settings) => settings.start_chars_per_token,
    toInput: (value) => String(value),
    toApi: (input) => Math.round(Number(input) * 100) / 100,
    format: (value) => RATIO.format(value),
    rangeError: "Der Startwert Zeichen je Token muss zwischen 1 und 8 liegen.",
    note:
      "Schätzt die Länge der Eingabe in Tokens, bis für das Modell eine Messung vorliegt. " +
      "Danach gilt der gemessene Wert.",
  },
  {
    field: "start_estimate_error",
    name: "Startwert Schätzabstand",
    label: "Startwert Schätzabstand (%)",
    min: 0,
    max: 100,
    step: "any",
    read: (settings) => settings.start_estimate_error,
    toInput: shareToPercentInput,
    toApi: percentInputToShare,
    format: formatPercent,
    rangeError: "Der Startwert Schätzabstand muss zwischen 0 und 100 % liegen.",
    note:
      "Sicherheitsaufschlag auf den geschätzten Teil der Eingabe, bis für das Modell eine " +
      "Messung vorliegt. Danach gilt der gemessene Abstand.",
  },
  {
    field: "assistant_retention_days",
    name: "Chats aufbewahren",
    label: "Chats im KI-Feld aufbewahren (Tage)",
    min: 1,
    max: 3650,
    step: "1",
    integer: true,
    read: (settings) => settings.assistant_retention_days,
    toInput: (value) => String(value),
    toApi: (input) => Number(input),
    format: (value) => `${INTEGER.format(value)} Tage`,
    rangeError: "Die Aufbewahrung der Chats muss eine ganze Zahl zwischen 1 und 3.650 Tagen sein.",
    note:
      "Chats im KI-Feld samt Laufprotokoll werden gelöscht, wenn sie so viele Tage nicht " +
      "mehr geändert wurden. Denktexte des Modells werden nie gespeichert.",
  },
];

/**
 * Vorgabe eines Felds aus dem Schema: Zahl, `null` (Spalte ohne Vorgabe, z. B.
 * "Kontext hoechstens" = leer) oder `undefined`, wenn die API keine nennt -
 * dann gibt es auch nichts, worauf zurueckgesetzt werden koennte.
 */
function schemaDefault(
  settings: AISettings | undefined,
  field: string,
): number | boolean | null | undefined {
  const value = settings?.schema_defaults?.[field];
  if (typeof value === "number" || typeof value === "boolean" || value === null) return value;
  return undefined;
}

function budgetInputsFrom(settings: AISettings | undefined): Record<BudgetField, string> {
  const inputs = {} as Record<BudgetField, string>;
  for (const spec of BUDGET_FIELDS) {
    const value = settings ? spec.read(settings) : undefined;
    inputs[spec.field] = value == null ? "" : spec.toInput(value);
  }
  return inputs;
}

function windowSourceLabel(source: string | null | undefined): string {
  if (!source) return "Quelle unbekannt";
  // "/api/v1/models" bzw. "/api/v0/models" sind die Verwaltungsschnittstellen
  // von LM Studio; Ollama und "Kontext höchstens" benennt der Server selbst.
  return source.startsWith("/api/v") ? `LM Studio ${source}` : source;
}

function measurementText(measurement: AIMeasurementInfo): string | null {
  if (measurement.chars_per_token == null) return null;
  const charsAreStart = measurement.is_start_value ?? true;
  const errorIsStart = measurement.error_is_start_value ?? true;
  const calls = measurement.calls ?? 0;
  let text = `${charsAreStart ? "Startwert" : "gemessen"} ${RATIO.format(measurement.chars_per_token)} Zeichen/Token`;
  if (measurement.estimate_error != null) {
    text += `, Abstand ${PERCENT.format(measurement.estimate_error * 100)} %`;
    if (errorIsStart && !charsAreStart) text += " (Startwert)";
  }
  text +=
    calls > 0
      ? ` aus ${INTEGER.format(calls)} ${calls === 1 ? "Aufruf" : "Aufrufen"}`
      : " (noch keine Messung)";
  return text;
}

/**
 * Live-Rechnung aus GET /v1/ai/health: wirksames Fenster samt Quelle,
 * Antwortreserve und Messung. Zeigt den gespeicherten Stand - nach dem
 * Speichern liest useUpdateAISettings den Status neu.
 */
function ContextBudgetLine({
  health,
  isLoading,
  isError,
}: {
  health: AIHealthResponse | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  if (isLoading) {
    return (
      <p role="status" className="placeholder-note" style={{ margin: 0 }}>
        Fenster wird ermittelt…
      </p>
    );
  }
  if (!health) {
    return (
      <p className="placeholder-note" style={{ margin: 0 }}>
        {isError
          ? "Der KI-Status ist nicht abrufbar – Fenster und Reserve sind unbekannt."
          : "Fenster und Reserve erscheinen, sobald der KI-Status vorliegt."}
      </p>
    );
  }
  // Der Status beschreibt den GESPEICHERTEN Anbieter - deshalb dessen Texte.
  const local = (health.provider ?? "local") === "local";
  if (!health.is_connected) {
    return (
      <p className="status-error" style={{ margin: 0 }}>
        {local
          ? "LM Studio ist nicht erreichbar – ohne Server gibt es kein Fenster, und die KI ruft kein Modell auf."
          : "Der Anbieter ist nicht erreichbar oder nicht bereit – ohne Modellliste gibt es kein Fenster, und die KI ruft kein Modell auf."}
      </p>
    );
  }

  const info = health.window;
  const effective = info?.effective ?? null;
  if (effective === null) {
    return (
      <div>
        <p className="status-error" style={{ margin: 0 }}>
          {local
            ? "Kein Kontextfenster bekannt – ohne Fenster ruft die KI kein Modell auf. Modell in LM Studio laden oder „Kontext höchstens“ setzen."
            : "Kein Kontextfenster bekannt – ohne Fenster ruft die KI kein Modell auf. Ein Modell wählen oder „Kontext höchstens“ setzen."}
        </p>
        {info?.reason && (
          <p className="placeholder-note" style={{ margin: 0 }}>
            {info.reason}
          </p>
        )}
      </div>
    );
  }

  const loaded = info?.loaded ?? null;
  const cap = info?.cap ?? null;
  // Greift der Deckel, nennt der Server weiter die Quelle des GELADENEN
  // Fensters - wirksam ist aber "Kontext hoechstens".
  const cappedFrom = cap !== null && loaded !== null && cap < loaded ? loaded : null;
  const source =
    cappedFrom !== null
      ? `Kontext höchstens; geladen ${INTEGER.format(cappedFrom)}`
      : windowSourceLabel(info?.source);
  const parts = [`Fenster ${INTEGER.format(effective)} (${source})`];
  if (health.reserve_tokens != null) parts.push(`Reserve ${INTEGER.format(health.reserve_tokens)}`);
  const measured = health.measurement ? measurementText(health.measurement) : null;
  if (measured) parts.push(measured);

  return (
    <p
      data-testid="ai-budget-line"
      style={{ margin: 0, fontFamily: "var(--font-mono)", fontSize: "13px" }}
    >
      {parts.join(" · ")}
    </p>
  );
}

const WARNING_LEVELS: Record<NonNullable<AIWarning["level"]>, { label: string; badge: string }> = {
  info: { label: "Info", badge: "status-badge--neutral" },
  warnung: { label: "Warnung", badge: "status-badge--warning" },
  fehler: { label: "Fehler", badge: "status-badge--error" },
};

/** Benannte Befunde des KI-Kerns mit Stufe - frueher reiner Fliesstext. */
function AIWarningList({ warnings }: { warnings: AIWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <ul
      aria-label="Hinweise zur KI"
      style={{
        listStyle: "none",
        padding: 0,
        margin: 0,
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        width: "100%",
        maxWidth: "760px",
      }}
    >
      {warnings.map((warning, index) => {
        const level = WARNING_LEVELS[warning.level ?? "warnung"];
        return (
          <li
            key={`${warning.code}-${index}`}
            title={warning.code}
            style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)" }}
          >
            <span className={`status-badge ${level.badge}`} style={{ flexShrink: 0 }}>
              {level.label}
            </span>
            <span>{warning.message}</span>
          </li>
        );
      })}
    </ul>
  );
}

interface BudgetInputProps {
  spec: BudgetSpec;
  value: string;
  defaultInput: string | undefined;
  defaultLabel: string | undefined;
  onChange: (value: string) => void;
  onReset: () => void;
}

function BudgetInput({
  spec,
  value,
  defaultInput,
  defaultLabel,
  onChange,
  onReset,
}: BudgetInputProps) {
  const id = `ai-budget-${spec.field}`;
  const canReset = defaultInput !== undefined && value.trim() !== defaultInput;
  return (
    <div className="form-field" style={{ width: "100%" }}>
      <label className="form-label" htmlFor={id}>
        {spec.label}
      </label>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <input
          id={id}
          className="form-input"
          type="number"
          min={spec.min}
          max={spec.max}
          step={spec.step}
          placeholder={spec.optional ? "leer = geladenes Fenster" : undefined}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          style={{ flex: 1, minWidth: 0 }}
        />
        <button
          type="button"
          className="btn-secondary"
          onClick={onReset}
          disabled={!canReset}
          aria-label={`${spec.name} zurücksetzen`}
        >
          Zurücksetzen
        </button>
      </div>
      <span className="placeholder-note">
        {defaultLabel !== undefined ? `Vorgabe: ${defaultLabel}. ` : ""}
        {spec.note}
      </span>
    </div>
  );
}

function BudgetSection({ children }: { children: ReactNode }) {
  return (
    <section
      aria-labelledby="ai-budget-heading"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-3)",
        width: "100%",
        maxWidth: "760px",
        paddingTop: "var(--space-3)",
        borderTop: "1px solid var(--color-border)",
      }}
    >
      <h3 id="ai-budget-heading" className="card-heading" style={{ margin: 0 }}>
        Kontext und Budget
      </h3>
      {children}
    </section>
  );
}

const ROLE_LABELS: Record<HouseholdRole, string> = {
  owner: "Eigentümer",
  editor: "Bearbeiter",
  viewer: "Betrachter",
  child: "Eingeschränkt",
};

interface HouseholdNameFormProps {
  householdId: string;
  initialName: string;
}

// Als eigene, per householdId geschluesselte Komponente: beim
// Haushaltswechsel im Sidebar-Picker remounted React die Komponente und der
// lokale State startet frisch mit dem neuen Namen - ganz ohne
// useEffect+setState-Kaskade (siehe HouseholdProvider.tsx fuer dasselbe
// Muster an anderer Stelle).
function HouseholdNameForm({ householdId, initialName }: HouseholdNameFormProps) {
  const { accessToken } = useAuth();
  const { refetch } = useHousehold();
  const [name, setName] = useState(initialName);
  const [formError, setFormError] = useState<string | null>(null);

  const renameMutation = useMutation({
    mutationFn: (newName: string) => renameHousehold(accessToken as string, householdId, newName),
    onSuccess: () => {
      setFormError(null);
      refetch();
    },
    onError: (error: unknown) => {
      setFormError(
        error instanceof Error ? error.message : "Haushalt konnte nicht umbenannt werden.",
      );
    },
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!name.trim()) {
      setFormError("Bitte einen Namen angeben.");
      return;
    }
    setFormError(null);
    renameMutation.mutate(name.trim());
  };

  return (
    <form className="inline-form" onSubmit={handleSubmit}>
      <label className="form-field">
        <span className="form-label">Name des Haushalts</span>
        <input
          className="form-input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
      </label>

      {formError && (
        <p role="alert" className="status-error">
          {formError}
        </p>
      )}
      {renameMutation.isSuccess && !formError && (
        <p role="status" className="status-success">
          Gespeichert.
        </p>
      )}

      <button type="submit" className="btn-primary" disabled={renameMutation.isPending}>
        {renameMutation.isPending ? "Wird gespeichert…" : "Speichern"}
      </button>
    </form>
  );
}

/**
 * KI-Einstellungen eines Haushalts.
 *
 * Vorher schrieb diese Maske ausschliesslich in den `localStorage` - der
 * Server hat die Werte nie gesehen, und Hintergrundlauf wie Mobile-App
 * arbeiteten weiter mit den Umgebungsvorgaben. Jetzt liegen sie serverseitig
 * je Haushalt (Migration 0011).
 *
 * Der fachlich wichtigste Teil ist "Kontext und Budget": Prompt und Antwort
 * teilen sich EIN Fenster. Ein festes Ausgabebudget gibt es nicht mehr - jeder
 * Aufruf rechnet aus dem geladenen Fenster und laesst die Antwortreserve frei.
 * Die Rechnung des Servers steht deshalb sichtbar daneben.
 */
interface AIDraft {
  isEnabled: boolean;
  /** Gewaehlter Anbieter. Ein Wechsel leert Modell und Reasoning - die
   *  Modelle des anderen Anbieters passen nicht. */
  provider: ProviderId;
  baseUrl: string;
  modelName: string;
  contextLength: string;
  reasoning: "" | ReasoningLevel;
  samplingMode: SamplingMode;
  temperature: string;
  topP: string;
  topK: string;
  repeatPenalty: string;
  minP: string;
  presencePenalty: string;
  threshold: number;
  searxngBaseUrl: string;
  budget: Record<BudgetField, string>;
  contextLineEnabled: boolean;
  /** Ausdruecklich zurueckgesetzte Felder - gehen als `clear_fields` an die
   *  API, die dann die Spaltenvorgabe des Schemas setzt. */
  resets: BudgetField[];
}

// ---------------------------------------------------------------------------
// Anbieter (services/api/app/llm/providers.py)
// ---------------------------------------------------------------------------

/**
 * Fehlertext einer Aktion, die nur der Owner darf. Der Server antwortet mit
 * 403 `household_owner_required`; daraus wird ein Satz, der sagt, wer es darf.
 */
function ownerAwareError(error: unknown, action: string, fallback: string): string {
  if (error instanceof ApiError && error.detail === "household_owner_required") {
    return `Nur der Owner kann ${action}.`;
  }
  return error instanceof Error ? error.message : fallback;
}

/** Warnsatz fuer einen Anbieter, bei dem die Daten den Haushalt verlassen. */
function leavesHouseText(provider: AIProviderInfo): string {
  if (provider.id === "anthropic") {
    return (
      "Mit Claude verlassen die Daten den Haushalt: Buchungen, Verträge, Dokumenttexte – auch " +
      "Gesundheitsdokumente – und Fragen aus dem KI-Feld gehen an Anthropic (Server in den " +
      "USA). Nur der Owner kann den Anbieter wechseln."
    );
  }
  return (
    `Mit „${provider.name}“ verlassen die Daten den Haushalt: Buchungen, Verträge, ` +
    "Dokumenttexte – auch Gesundheitsdokumente – und Fragen aus dem KI-Feld gehen an diesen " +
    "Dienst. Nur der Owner kann den Anbieter wechseln."
  );
}

/** "Claude Opus 5 · 1.000.000 Token Fenster, Antwort bis 128.000" */
function cloudModelLabel(model: AIModelInfo): string {
  const name = model.display_name || model.id;
  const window = model.loaded_context_tokens ?? model.max_context_tokens;
  const parts: string[] = [];
  if (window) parts.push(`${INTEGER.format(window)} Token Fenster`);
  if (model.output_limit_tokens) {
    parts.push(`Antwort bis ${INTEGER.format(model.output_limit_tokens)}`);
  }
  return parts.length ? `${name} · ${parts.join(", ")}` : name;
}

function keyStatusText(status: AIKeyStatus | undefined, format: (value: string) => string) {
  if (!status?.stored) return "Kein Schlüssel hinterlegt";
  const since = status.since ? ` seit ${format(status.since)}` : "";
  const ending = status.ending ? ` (${status.ending})` : "";
  return `Hinterlegt${since}${ending}`;
}

interface AIProviderKeySectionProps {
  provider: AIProviderInfo;
  status: AIKeyStatus | undefined;
  /** Rolle bekannt und nicht Owner: gesperrt. Der Server prueft ohnehin. */
  locked: boolean;
}

/**
 * API-Schluessel des gewaehlten Anbieters. Eigene Aktionen, nicht Teil von
 * "Einstellungen speichern": der Schluessel gehoert nicht in den Entwurf und
 * geht nur an PUT /v1/ai/provider-keys/{anbieter}. Zurueck kommt nur der
 * Stand ("…abcd"), nie der Wert.
 */
function AIProviderKeySection({ provider, status, locked }: AIProviderKeySectionProps) {
  const storeMutation = useStoreAIProviderKey();
  const removeMutation = useRemoveAIProviderKey();
  const { formatDate: formatKeyDate } = useFormatDate();
  const [apiKey, setApiKey] = useState("");
  const [keyMessage, setKeyMessage] = useState<string | null>(null);
  const [keyError, setKeyError] = useState<string | null>(null);
  const busy = storeMutation.isPending || removeMutation.isPending;

  const handleStore = () => {
    const value = apiKey.trim();
    if (!value || busy) return;
    setKeyMessage(null);
    setKeyError(null);
    storeMutation.mutate(
      { provider: provider.id, apiKey: value },
      {
        onSuccess: () => {
          setApiKey("");
          setKeyMessage("Schlüssel hinterlegt.");
        },
        onError: (error) =>
          setKeyError(
            ownerAwareError(
              error,
              "den API-Schlüssel hinterlegen",
              "Der Schlüssel konnte nicht hinterlegt werden.",
            ),
          ),
        // Der Schluessel steht als Variable in der Mutation - danach weg damit.
        onSettled: () => storeMutation.reset(),
      },
    );
  };

  const handleRemove = () => {
    setKeyMessage(null);
    setKeyError(null);
    removeMutation.mutate(provider.id, {
      onSuccess: () => setKeyMessage("Schlüssel entfernt."),
      onError: (error) =>
        setKeyError(
          ownerAwareError(
            error,
            "den API-Schlüssel entfernen",
            "Der Schlüssel konnte nicht entfernt werden.",
          ),
        ),
    });
  };

  return (
    <section
      aria-labelledby="ai-api-key-heading"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        width: "100%",
        maxWidth: "560px",
      }}
    >
      <h3 id="ai-api-key-heading" className="form-label" style={{ margin: 0 }}>
        API-Schlüssel
      </h3>
      <p data-testid="ai-api-key-status" style={{ margin: 0 }}>
        {keyStatusText(status, formatKeyDate)}
      </p>
      {status?.problem && (
        <p className="status-error" style={{ margin: 0 }}>
          {status.problem}
        </p>
      )}
      <label className="form-label" htmlFor="ai-api-key-input">
        Neuer API-Schlüssel
      </label>
      <div
        style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--space-2)" }}
      >
        <input
          id="ai-api-key-input"
          className="form-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={apiKey}
          disabled={locked}
          onChange={(event) => setApiKey(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              // Sonst wuerde Enter das ganze Formular speichern.
              event.preventDefault();
              handleStore();
            }
          }}
          style={{ flex: 1, minWidth: "200px" }}
        />
        <button
          type="button"
          className="btn-secondary"
          onClick={handleStore}
          disabled={locked || busy || !apiKey.trim()}
        >
          {storeMutation.isPending ? "Wird hinterlegt…" : "Hinterlegen"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={handleRemove}
          disabled={locked || busy || !status?.stored}
        >
          {removeMutation.isPending ? "Wird entfernt…" : "Entfernen"}
        </button>
      </div>
      <span className="placeholder-note">
        Der Schlüssel wird verschlüsselt auf dem Server abgelegt und nie wieder angezeigt – zu sehen
        sind danach nur seine letzten Zeichen. Erzeugt wird er in der Anthropic-Konsole.
        {locked ? " Hinterlegen und entfernen kann ihn nur der Owner." : ""}
      </span>
      {keyMessage && (
        <p role="status" className="status-success" style={{ margin: 0 }}>
          {keyMessage}
        </p>
      )}
      {keyError && (
        <p role="alert" className="status-error" style={{ margin: 0 }}>
          {keyError}
        </p>
      )}
    </section>
  );
}

type LocalOnlyField =
  | "base_url"
  | "context_length"
  | "sampling_mode"
  | "temperature"
  | "top_p"
  | "top_k"
  | "repeat_penalty"
  | "min_p"
  | "presence_penalty";

type LocalFieldsResult =
  { error: string } | { fields: Pick<AISettingsUpdate, LocalOnlyField>; clear: AISettingsField[] };

/**
 * Felder, die nur der lokale Anbieter zeigt: Server-URL, Fenster beim Laden
 * und Sampling (neuere Claude-Modelle lehnen Sampling-Werte ab). Bei einem
 * Cloud-Anbieter sind sie ausgeblendet und werden weder gesendet noch per
 * `clear_fields` zurueckgesetzt - der gespeicherte Stand bleibt fuer einen
 * spaeteren Wechsel zurueck erhalten.
 */
function localFieldsFrom(draft: AIDraft, model: AIModelInfo | undefined): LocalFieldsResult {
  const contextRaw = draft.contextLength.trim();
  const contextValue = contextRaw ? Number(contextRaw) : null;
  if (contextValue !== null && (!Number.isFinite(contextValue) || contextValue < 2048)) {
    return { error: "Das Kontextfenster muss mindestens 2.048 Tokens betragen." };
  }
  if (
    contextValue !== null &&
    model?.max_context_tokens &&
    contextValue > model.max_context_tokens
  ) {
    return {
      error:
        `„${draft.modelName || model.id}“ unterstützt höchstens ` +
        `${model.max_context_tokens.toLocaleString("de-DE")} Tokens Kontext.`,
    };
  }

  const temperature = Number(draft.temperature);
  const topP = Number(draft.topP);
  const topK = Number(draft.topK);
  const repeatPenalty = Number(draft.repeatPenalty);
  const minP = Number(draft.minP);
  const presencePenalty = Number(draft.presencePenalty);
  if (!Number.isFinite(temperature) || temperature < 0 || temperature > 2) {
    return { error: "Die Temperatur muss zwischen 0 und 2 liegen." };
  }
  if (!Number.isFinite(topP) || topP <= 0 || topP > 1) {
    return { error: "Top P muss größer als 0 und höchstens 1 sein." };
  }
  if (!Number.isInteger(topK) || topK < 0 || topK > 1000) {
    return { error: "Top K muss eine ganze Zahl zwischen 0 und 1.000 sein." };
  }
  if (!Number.isFinite(repeatPenalty) || repeatPenalty < 0 || repeatPenalty > 2) {
    return { error: "Die Wiederholungsstrafe muss zwischen 0 und 2 liegen." };
  }
  if (!Number.isFinite(minP) || minP < 0 || minP > 1) {
    return { error: "Min P muss zwischen 0 und 1 liegen." };
  }
  if (!Number.isFinite(presencePenalty) || presencePenalty < -2 || presencePenalty > 2) {
    return { error: "Die Presence-Strafe muss zwischen -2 und 2 liegen." };
  }

  // Leeres Feld heisst ausdruecklich "auf Vorgabe zuruecksetzen".
  const baseUrl = draft.baseUrl.trim();
  const clear: AISettingsField[] = [];
  if (!baseUrl) clear.push("custom_base_url");
  if (contextValue === null) clear.push("context_length");
  return {
    fields: {
      ...(baseUrl ? { base_url: baseUrl } : {}),
      sampling_mode: draft.samplingMode,
      temperature,
      top_p: topP,
      top_k: topK,
      repeat_penalty: repeatPenalty,
      min_p: minP,
      presence_penalty: presencePenalty,
      ...(contextValue !== null ? { context_length: contextValue } : {}),
    },
    clear,
  };
}

function AISettingsForm() {
  const settingsQuery = useAISettings();
  const healthQuery = useAIHealth();
  const updateMutation = useUpdateAISettings();
  const loadMutation = useLoadAIModel();
  const { selectedHousehold } = useHousehold();

  const settings = settingsQuery.data;
  const [draft, setDraft] = useState<AIDraft | null>(null);
  // Die Modellliste folgt dem Anbieter im Entwurf, nicht dem gespeicherten:
  // nach einem Wechsel stehen die Modelle des neuen Anbieters sofort zur Wahl.
  const modelsQuery = useAIModels(draft?.provider ?? settings?.provider);
  const [formError, setFormError] = useState<string | null>(null);
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [loadMessage, setLoadMessage] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Entwurf einmal aus dem Serverstand ableiten, sobald er da ist. Bewusst
  // nicht bei jedem Render: sonst wuerde eine Eingabe beim naechsten Refetch
  // ueberschrieben.
  const current: AIDraft = draft ?? {
    isEnabled: settings?.is_enabled ?? false,
    provider: settings?.provider ?? "local",
    baseUrl: settings?.base_url ?? "",
    modelName: settings?.model_name ?? "",
    contextLength: settings?.context_length ? String(settings.context_length) : "",
    reasoning: (settings?.reasoning ?? "") as "" | ReasoningLevel,
    samplingMode: settings?.sampling_mode ?? "model",
    temperature: String(settings?.temperature ?? 1),
    topP: String(settings?.top_p ?? 0.95),
    topK: String(settings?.top_k ?? 64),
    repeatPenalty: String(settings?.repeat_penalty ?? 1),
    minP: String(settings?.min_p ?? 0),
    presencePenalty: String(settings?.presence_penalty ?? 0),
    threshold: Math.round((settings?.auto_categorize_min_confidence ?? 0.85) * 100),
    searxngBaseUrl: settings?.searxng_base_url ?? "",
    budget: budgetInputsFrom(settings),
    contextLineEnabled: settings?.context_line_enabled ?? false,
    resets: [],
  };
  const patch = (change: Partial<AIDraft>) => setDraft({ ...current, ...change });
  // Eine Eingabe nach "Zuruecksetzen" ist wieder ein eigener Wert.
  const patchBudget = (field: BudgetField, value: string) =>
    setDraft({
      ...current,
      budget: { ...current.budget, [field]: value },
      resets: current.resets.filter((item) => item !== field),
    });
  const resetBudget = (field: BudgetField, defaultInput: string) =>
    setDraft({
      ...current,
      budget: { ...current.budget, [field]: defaultInput },
      resets: current.resets.includes(field) ? current.resets : [...current.resets, field],
    });

  const budgetDefaults = BUDGET_FIELDS.map((spec) => {
    const value = schemaDefault(settings, spec.field);
    if (value === null && spec.optional) {
      return { input: "", label: "leer – das geladene Fenster gilt" };
    }
    if (typeof value === "number") return { input: spec.toInput(value), label: spec.format(value) };
    return { input: undefined, label: undefined };
  });
  const contextLineDefault = schemaDefault(settings, "context_line_enabled");
  const legacyMaxOutputTokens = settings?.legacy_max_output_tokens ?? null;

  // Anbieter: lokal (LM Studio & Co.) oder ein Cloud-Anbieter wie Claude.
  const providers = settings?.providers ?? [];
  const savedProvider: ProviderId = settings?.provider ?? "local";
  const providerInfo = providers.find((entry) => entry.id === current.provider);
  const providerName = providerInfo?.name ?? current.provider;
  // Server-URL, Fenster beim Laden, "In LM Studio laden" und Sampling gibt es
  // nur beim lokalen Anbieter.
  const isLocal = current.provider === "local";
  const keyStatus = settings?.provider_keys?.find((entry) => entry.provider === current.provider);
  // Rolle unbekannt sperrt nichts - der Server prueft ohnehin.
  const ownerOnlyLocked = selectedHousehold != null && selectedHousehold.role !== "owner";

  const handleProviderChange = (next: ProviderId) => {
    // Die Modelle des anderen Anbieters passen nicht: Modell und Reasoning
    // leeren. Zurueck beim gespeicherten Anbieter gilt wieder dessen Wahl.
    const back = next === savedProvider;
    patch({
      provider: next,
      modelName: back ? (settings?.model_name ?? "") : "",
      reasoning: back ? ((settings?.reasoning ?? "") as "" | ReasoningLevel) : "",
    });
  };

  // Die Modellliste gehoert zum Anbieter im Entwurf. Waehrend sie nach einem
  // Wechsel noch laedt, steht keine fremde Liste zur Wahl.
  const catalogMatches = (modelsQuery.data?.provider ?? "local") === current.provider;
  const models = catalogMatches ? (modelsQuery.data?.models ?? []) : [];
  const chatModels = models.filter((model) => model.is_chat_capable);
  const health = healthQuery.data;
  const selectedModel = models.find((model) => model.id === current.modelName);
  // Auch bei "Automatisch" ist ein konkretes Modell aktiv. Zuvor war der
  // Reasoning-Schalter ausgerechnet dann unsichtbar, obwohl der Server das
  // aktive Modell samt Reasoning-Faehigkeit bereits gemeldet hatte. Bei einem
  // Cloud-Anbieter gibt es kein "Automatisch".
  const effectiveModel =
    selectedModel ??
    (!current.modelName && isLocal
      ? models.find((model) => model.id === health?.active_model)
      : undefined);
  // Der Reasoning-Schalter erscheint nur, wenn der Server fuer dieses Modell
  // ueberhaupt Optionen meldet. Einen Regler anzubieten, den der Server mit
  // HTTP 400 quittiert, waere schlimmer als keiner.
  const reasoningOptions = effectiveModel?.reasoning_options ?? [];
  const availableReasoning = availableReasoningLevels(reasoningOptions);
  const supportsReasoning = availableReasoning.length > 0;
  const supportsReasoningEffort = availableReasoning.some((level) =>
    REASONING_EFFORT_LEVELS.has(level),
  );
  const modelNote = modelsQuery.isLoading
    ? "Modelle werden gesucht…"
    : !catalogMatches
      ? `Die Modelle von „${providerName}“ werden gesucht…`
      : (modelsQuery.data?.message ??
        (isLocal ? "LM Studio nicht erreichbar." : `${providerName} nicht erreichbar.`));
  const samplingProfile = samplingProfileFor(
    effectiveModel?.id,
    current.reasoning,
    modelsQuery.data?.sampling_profiles ?? [],
  );

  const handleSave = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    setSavedSuccess(false);
    // Ohne geladenen Serverstand wuerde der Entwurf leere Felder und eine
    // ausgeschaltete KI speichern.
    if (!settings) {
      setFormError("Die KI-Einstellungen sind nicht geladen – Speichern ist erst danach möglich.");
      return;
    }
    // Server-URL, Fenster beim Laden und Sampling nur beim lokalen Anbieter.
    // Bei einem Cloud-Anbieter sind sie ausgeblendet und bleiben, wie sie
    // sind: weder gesendet noch per clear_fields zurueckgesetzt.
    const local: LocalFieldsResult = isLocal
      ? localFieldsFrom(current, effectiveModel)
      : { fields: {}, clear: [] };
    if ("error" in local) {
      setFormError(local.error);
      return;
    }
    if (current.reasoning && supportsReasoning && !availableReasoning.includes(current.reasoning)) {
      setFormError(
        `„${reasoningLabel(current.reasoning)}“ wird vom gewählten Modell nicht angeboten. ` +
          "Bitte Modellstandard oder eine der gemeldeten Stufen wählen.",
      );
      return;
    }

    // Leeres Feld heisst ausdruecklich "auf Vorgabe zuruecksetzen" - `null`
    // allein hiesse "nicht angefasst". Alle Ruecksetzungen wandern in EIN
    // Array: frueher haben zwei getrennte `clear_fields`-Spreads einander
    // ueberschrieben, sodass beim gleichzeitigen Leeren von URL und Modell nur
    // das Modell zurueckgesetzt wurde.
    const clearFields: AISettingsField[] = [...local.clear];
    if (!current.modelName) clearFields.push("model_name");
    if (!current.reasoning) clearFields.push("reasoning");
    if (!current.searxngBaseUrl.trim()) clearFields.push("searxng_base_url");

    // Kontext und Budget: Zuruecksetzen geht ausdruecklich ueber
    // clear_fields - die API setzt dann die Spaltenvorgabe des Schemas, statt
    // eine Zahl aus der Oberflaeche zu uebernehmen.
    const budget: Pick<AISettingsUpdate, BudgetField> = {};
    for (const spec of BUDGET_FIELDS) {
      if (current.resets.includes(spec.field)) {
        clearFields.push(spec.field);
        continue;
      }
      const raw = current.budget[spec.field].trim();
      if (!raw) {
        if (spec.optional) {
          clearFields.push(spec.field);
          continue;
        }
        setFormError(`${spec.name}: bitte einen Wert eintragen.`);
        return;
      }
      const number = Number(raw);
      if (
        !Number.isFinite(number) ||
        number < spec.min ||
        number > spec.max ||
        (spec.integer && !Number.isInteger(number))
      ) {
        setFormError(spec.rangeError);
        return;
      }
      budget[spec.field] = spec.toApi(raw);
    }
    // Der Hinweis auf das fruehere feste Ausgabebudget erscheint einmal; wer
    // mit ihm vor Augen speichert, hat ihn gesehen.
    if (legacyMaxOutputTokens !== null) clearFields.push("legacy_max_output_tokens");

    updateMutation.mutate(
      {
        is_enabled: current.isEnabled,
        // Immer mitsenden: unveraendert braucht es kein Owner-Recht, ein
        // Wechsel schon (403 household_owner_required).
        provider: current.provider,
        ...local.fields,
        model_name: current.modelName || null,
        ...budget,
        context_line_enabled: current.contextLineEnabled,
        ...(current.reasoning ? { reasoning: current.reasoning } : {}),
        ...(current.searxngBaseUrl.trim()
          ? { searxng_base_url: current.searxngBaseUrl.trim() }
          : {}),
        auto_categorize_min_confidence: current.threshold / 100,
        clear_fields: clearFields,
      },
      {
        onSuccess: () => {
          setDraft(null);
          setSavedSuccess(true);
        },
        onError: (error) =>
          setFormError(ownerAwareError(error, "den Anbieter wechseln", "Speichern nicht möglich.")),
      },
    );
  };

  const handleLoadModel = () => {
    setLoadMessage(null);
    setLoadError(null);
    loadMutation.mutate(undefined, {
      onSuccess: (result) => setLoadMessage(result.message),
      onError: (error) =>
        setLoadError(
          error instanceof Error ? error.message : "Das Modell konnte nicht geladen werden.",
        ),
    });
  };

  // Nur den Hinweis quittieren - der Entwurf der uebrigen Felder bleibt.
  const handleDismissLegacy = () => {
    setFormError(null);
    updateMutation.mutate(
      { clear_fields: ["legacy_max_output_tokens"] },
      {
        onError: (error) =>
          setFormError(error instanceof Error ? error.message : "Speichern nicht möglich."),
      },
    );
  };

  if (settingsQuery.isLoading) {
    return <p role="status">Einstellungen werden geladen…</p>;
  }

  return (
    <form
      className="inline-form"
      onSubmit={handleSave}
      style={{ flexDirection: "column", alignItems: "flex-start", gap: "var(--space-4)" }}
    >
      {settingsQuery.isError && (
        <p role="alert" className="status-error" style={{ margin: 0 }}>
          Die KI-Einstellungen konnten nicht geladen werden.
        </p>
      )}

      <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
        <label
          htmlFor="ai-enabled-input"
          style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}
        >
          <input
            id="ai-enabled-input"
            type="checkbox"
            checked={current.isEnabled}
            onChange={(event) => patch({ isEnabled: event.target.checked })}
          />
          <span className="form-label" style={{ margin: 0 }}>
            KI in diesem Haushalt verwenden
          </span>
        </label>
        <span className="placeholder-note">
          {current.isEnabled
            ? "Eingeschaltet dürfen die KI-Funktionen das eingestellte Modell aufrufen."
            : "Ausgeschaltet ruft keine KI-Funktion ein Modell auf. Die übrigen Einstellungen bleiben erhalten."}
        </span>
      </div>

      {providers.length > 0 && (
        <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
          <label className="form-label" htmlFor="ai-provider-select">
            Anbieter
          </label>
          <select
            id="ai-provider-select"
            className="form-input"
            value={current.provider}
            disabled={ownerOnlyLocked}
            onChange={(event) => handleProviderChange(event.target.value as ProviderId)}
          >
            {providers.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.name}
              </option>
            ))}
          </select>
          {providerInfo && !providerInfo.leaves_house && (
            <span className="placeholder-note">{providerInfo.hint}</span>
          )}
          {ownerOnlyLocked && (
            <span className="placeholder-note">Den Anbieter wechseln kann nur der Owner.</span>
          )}
          {current.provider !== savedProvider && (
            <span className="placeholder-note">
              Wirksam erst mit „Einstellungen speichern“ – danach erscheinen die Modelle des neuen
              Anbieters zur Auswahl.
            </span>
          )}
        </div>
      )}

      {providerInfo?.leaves_house && (
        <div
          role="note"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
            width: "100%",
            maxWidth: "760px",
            padding: "var(--space-3)",
            border: "1px solid var(--color-warning)",
            borderRadius: "var(--radius-sm)",
            backgroundColor: "var(--color-warning-muted)",
          }}
        >
          <p style={{ margin: 0, fontWeight: 600 }}>{leavesHouseText(providerInfo)}</p>
          <p className="placeholder-note" style={{ margin: 0 }}>
            {providerInfo.hint}
          </p>
        </div>
      )}

      {/* key je Anbieter: keine halbe Eingabe, die nach einem Wechsel an den
          falschen Anbieter ginge. */}
      {providerInfo?.key_required && (
        <AIProviderKeySection
          key={providerInfo.id}
          provider={providerInfo}
          status={keyStatus}
          locked={ownerOnlyLocked}
        />
      )}

      {legacyMaxOutputTokens !== null && (
        <div
          role="note"
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: "var(--space-2)",
            width: "100%",
            maxWidth: "760px",
            padding: "var(--space-3)",
            border: "1px solid var(--color-highlight)",
            borderRadius: "var(--radius-sm)",
            backgroundColor: "var(--color-highlight-muted)",
          }}
        >
          <p style={{ margin: 0 }}>
            Das frühere feste Ausgabebudget ({INTEGER.format(legacyMaxOutputTokens)} Tokens) gilt
            nicht mehr. Jeder KI-Aufruf richtet sich jetzt nach dem geladenen Kontextfenster und
            lässt die Antwortreserve frei – einstellbar unter „Kontext und Budget“.
          </p>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleDismissLegacy}
            disabled={updateMutation.isPending}
          >
            Verstanden
          </button>
        </div>
      )}

      {isLocal && (
        <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
          <label className="form-label" htmlFor="lm-studio-url-input">
            LM-Studio URL
          </label>
          <input
            id="lm-studio-url-input"
            className="form-input"
            value={current.baseUrl}
            onChange={(event) => patch({ baseUrl: event.target.value })}
            placeholder="http://localhost:1234/v1"
          />
          <span className="placeholder-note">
            {settings?.base_url_source === "household"
              ? "Eigene Adresse dieses Haushalts. Leeren setzt auf die Servervorgabe zurück."
              : "Vorgabe aus der Serverkonfiguration."}
          </span>
        </div>
      )}

      <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
        <label className="form-label" htmlFor="searxng-url-input">
          SearXNG-Adresse (optional)
        </label>
        <input
          id="searxng-url-input"
          className="form-input"
          value={current.searxngBaseUrl}
          onChange={(event) => patch({ searxngBaseUrl: event.target.value })}
          placeholder="http://localhost:8080"
        />
        <span className="placeholder-note">
          Selbstgehostete Suchinstanz für die KI-Kostenschätzung bei Vorhaben. Leer = die
          Kostenschätzung läuft ohne Websuche, nur mit der eigenen Beschreibung.
        </span>
      </div>

      <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
        <label className="form-label" htmlFor="ai-model-select">
          Modell
        </label>
        <select
          id="ai-model-select"
          className="form-input"
          value={current.modelName}
          onChange={(event) => patch({ modelName: event.target.value })}
        >
          {/* Bei einem Cloud-Anbieter waehlt der Finanzplaner nie selbst ein
              (womoeglich teures) Modell - deshalb kein "Automatisch". */}
          <option value="">
            {isLocal ? "Automatisch (erstes geladenes Modell)" : "Bitte Modell wählen"}
          </option>
          {chatModels.map((model) =>
            isLocal ? (
              <option key={model.id} value={model.id}>
                {model.id}
                {model.state === "loaded" ? " · geladen" : ""}
                {model.loaded_context_tokens || model.max_context_tokens
                  ? ` · ${(model.loaded_context_tokens ?? model.max_context_tokens)?.toLocaleString("de-DE")} Tokens Kontext`
                  : ""}
              </option>
            ) : (
              <option key={model.id} value={model.id}>
                {cloudModelLabel(model)}
              </option>
            ),
          )}
          {/* Ein eingestelltes, aber verschwundenes Modell bleibt sichtbar -
              sonst sähe die Auswahl aus, als stünde dort „automatisch". */}
          {current.modelName && !chatModels.some((model) => model.id === current.modelName) && (
            <option value={current.modelName}>{current.modelName} · nicht gefunden</option>
          )}
        </select>
        <span className="placeholder-note">{modelNote}</span>
        {providerInfo?.model_required && !current.modelName && (
          <span className="placeholder-note">
            Bei „{providerName}“ wählt der Finanzplaner bewusst kein Modell selbst – ohne Wahl ruft
            die KI kein Modell auf.
          </span>
        )}
        {models.some((model) => !model.is_chat_capable) && (
          <span className="placeholder-note">
            Einbettungsmodelle stehen nicht zur Wahl – sie beherrschen keine Chat-Antworten und
            können deshalb nicht klassifizieren.
          </span>
        )}
      </div>

      {/* Das Kontextfenster ist ein Ladeparameter. Reasoning und Sampling
          werden dagegen mit jeder Anfrage gesendet. Bei einem Cloud-Anbieter
          gibt es nichts zu laden - das Fenster steht in der Modellliste. */}
      {isLocal && (
        <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
          <label className="form-label" htmlFor="ai-context-length-input">
            Kontextfenster (Tokens)
          </label>
          <input
            id="ai-context-length-input"
            className="form-input"
            type="number"
            min={2048}
            max={effectiveModel?.max_context_tokens ?? 1048576}
            step={1}
            placeholder="leer = so lassen, wie geladen"
            value={current.contextLength}
            onChange={(event) => patch({ contextLength: event.target.value })}
          />
          <span className="placeholder-note">
            {effectiveModel?.max_context_tokens
              ? `„${effectiveModel.id}“ kann bis ${effectiveModel.max_context_tokens.toLocaleString("de-DE")} Tokens.`
              : "Wie viel das Modell kann, meldet LM Studio erst, wenn es erreichbar ist."}
            {effectiveModel?.loaded_context_tokens
              ? ` Aktuell geladen mit ${effectiveModel.loaded_context_tokens.toLocaleString("de-DE")}.`
              : ""}
          </span>
          <span className="placeholder-note">
            Der Wert ist ein Wunsch, kein Zustand: wirksam wird er erst mit „In LM Studio laden“.
            Großer Speicherbedarf – ein weites Fenster kann am Arbeitsspeicher scheitern, LM Studio
            sagt dann, warum. Das ist das Fenster beim Laden; „Kontext höchstens“ unter „Kontext und
            Budget“ begrenzt nur, wie viel davon genutzt wird.
          </span>
        </div>
      )}

      {supportsReasoning && (
        <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
          <label className="form-label" htmlFor="ai-reasoning-select">
            Lautes Denken (Reasoning)
          </label>
          <select
            id="ai-reasoning-select"
            className="form-input"
            value={current.reasoning}
            onChange={(event) => patch({ reasoning: event.target.value as "" | ReasoningLevel })}
          >
            <option value="">
              Modellstandard
              {effectiveModel?.reasoning_default
                ? ` (laut ${isLocal ? "LM Studio" : providerName}: ${reasoningLabel(effectiveModel.reasoning_default)})`
                : ""}
            </option>
            {current.reasoning && !availableReasoning.includes(current.reasoning) && (
              <option value={current.reasoning} disabled>
                {reasoningLabel(current.reasoning)} · vom Modell nicht gemeldet
              </option>
            )}
            {availableReasoning.map((value) => (
              <option key={value} value={value}>
                {REASONING_LEVEL_LABELS[value]}
              </option>
            ))}
          </select>
          {isLocal ? (
            <span className="placeholder-note">
              „Modellstandard“ sendet keinen Override. Angezeigt werden ausschließlich die Optionen,
              die LM Studio für das gewählte Modell meldet.
              {supportsReasoningEffort
                ? " Dieses Modell bietet abgestufte Denktiefe; Minimal bis Sehr hoch werden unverändert gesendet."
                : " Dieses Modell bietet nur An/Aus."}{" "}
              „Aus“ wird technisch als <code>reasoning_effort=none</code> gesendet. Der
              OpenAI-Endpunkt akzeptiert den booleschen Capability-Text „on“ nicht; deshalb wird
              „An“ transparent als <code>reasoning_effort=high</code> transportiert und von LM
              Studio auf den On-Modus des Modells abgebildet. Die Wahl gilt unverändert für den
              gesamten Erstattungsaufruf; es gibt keinen zweiten Aufruf mit anderer
              Reasoning-Einstellung.
            </span>
          ) : (
            <span className="placeholder-note">
              „Modellstandard“ sendet keine Vorgabe. Angezeigt werden ausschließlich die Stufen, die{" "}
              {providerName} für das gewählte Modell meldet: „An“ schaltet adaptives Denken ein,
              Niedrig bis Maximal setzen die Denktiefe (Effort).
              {availableReasoning.includes("off")
                ? " „Aus“ schaltet das Denken ab."
                : " Abschalten lässt sich das Denken bei diesem Modell nicht."}
            </span>
          )}
        </div>
      )}

      {/* Sampling nur lokal: neuere Claude-Modelle lehnen Sampling-Werte ab.
          Die gespeicherten Werte bleiben unangetastet. */}
      {isLocal && (
        <div className="form-field" style={{ width: "100%", maxWidth: "760px" }}>
          <label className="form-label" htmlFor="ai-sampling-mode-select">
            Sampling
          </label>
          <select
            id="ai-sampling-mode-select"
            className="form-input"
            value={current.samplingMode}
            onChange={(event) => patch({ samplingMode: event.target.value as SamplingMode })}
          >
            <option value="model">Automatisch – Modellprofil</option>
            <option value="manual">Manuell – meine Werte exakt senden</option>
          </select>
          {current.samplingMode === "model" ? (
            <>
              <span className="placeholder-note">
                {samplingProfile
                  ? `Wirksam für „${effectiveModel?.id}“: ${samplingSummary(samplingProfile)}.`
                  : "Für dieses Modell ist keine belastbare Zahlenempfehlung hinterlegt. Die Anwendung sendet dann keine Sampling-Werte; Modell und Server behalten ihre Standards."}
              </span>
              {samplingProfile && (
                <span className="placeholder-note">
                  {samplingProfile.note}{" "}
                  <a href={samplingProfile.source_url} target="_blank" rel="noreferrer">
                    Herstellerquelle
                  </a>
                </span>
              )}
            </>
          ) : (
            <>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(155px, 1fr))",
                  gap: "var(--space-3)",
                  width: "100%",
                }}
              >
                <label className="form-field">
                  <span className="form-label">Temperatur</span>
                  <input
                    className="form-input"
                    aria-label="Sampling Temperatur"
                    type="number"
                    min="0"
                    max="2"
                    step="0.01"
                    value={current.temperature}
                    onChange={(event) => patch({ temperature: event.target.value })}
                  />
                </label>
                <label className="form-field">
                  <span className="form-label">Top P</span>
                  <input
                    className="form-input"
                    aria-label="Sampling Top P"
                    type="number"
                    min="0.001"
                    max="1"
                    step="any"
                    value={current.topP}
                    onChange={(event) => patch({ topP: event.target.value })}
                  />
                </label>
                <label className="form-field">
                  <span className="form-label">Top K</span>
                  <input
                    className="form-input"
                    aria-label="Sampling Top K"
                    type="number"
                    min="0"
                    max="1000"
                    step="1"
                    value={current.topK}
                    onChange={(event) => patch({ topK: event.target.value })}
                  />
                </label>
                <label className="form-field">
                  <span className="form-label">Wiederholungsstrafe</span>
                  <input
                    className="form-input"
                    aria-label="Sampling Wiederholungsstrafe"
                    type="number"
                    min="0"
                    max="2"
                    step="0.01"
                    value={current.repeatPenalty}
                    onChange={(event) => patch({ repeatPenalty: event.target.value })}
                  />
                </label>
                <label className="form-field">
                  <span className="form-label">Min P</span>
                  <input
                    className="form-input"
                    aria-label="Sampling Min P"
                    type="number"
                    min="0"
                    max="1"
                    step="any"
                    value={current.minP}
                    onChange={(event) => patch({ minP: event.target.value })}
                  />
                </label>
                <label className="form-field">
                  <span className="form-label">Presence-Strafe</span>
                  <input
                    className="form-input"
                    aria-label="Sampling Presence-Strafe"
                    type="number"
                    min="-2"
                    max="2"
                    step="0.01"
                    value={current.presencePenalty}
                    onChange={(event) => patch({ presencePenalty: event.target.value })}
                  />
                </label>
              </div>
              <span className="placeholder-note">
                Temperatur steuert die Zufälligkeit; sehr niedrige Werte können Reasoning festfahren
                lassen. Top P begrenzt die Wahrscheinlichkeitsmasse, Top K die Zahl der Kandidaten.
                Die Wiederholungsstrafe ist bei 1 neutral; Werte knapp über 1 können Schleifen
                hemmen, zu hohe Werte beschädigen aber Namen, Nummern und wiederkehrende
                JSON-Felder. Min P filtert Token relativ zum wahrscheinlichsten Kandidaten. Eine
                positive Presence-Strafe drängt zu neuen Token; Qwen empfiehlt sie gegen
                Endlosschleifen, für andere Modelle bleibt 0 neutral.
              </span>
            </>
          )}

          <details style={{ width: "100%" }}>
            <summary>Profile für bekannte lokale Modelle anzeigen</summary>
            <div style={{ overflowX: "auto", marginTop: "var(--space-2)" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "6px" }}>Modellfamilie</th>
                    <th style={{ textAlign: "left", padding: "6px" }}>Reasoning</th>
                    <th style={{ textAlign: "left", padding: "6px" }}>Profil</th>
                  </tr>
                </thead>
                <tbody>
                  {(modelsQuery.data?.sampling_profiles ?? []).map((profile) => (
                    <tr key={profile.id}>
                      <td style={{ padding: "6px", verticalAlign: "top" }}>
                        <a href={profile.source_url} target="_blank" rel="noreferrer">
                          {profile.label}
                        </a>
                      </td>
                      <td style={{ padding: "6px", verticalAlign: "top" }}>
                        {profile.reasoning_mode === "thinking"
                          ? "An"
                          : profile.reasoning_mode === "non-thinking"
                            ? "Aus"
                            : "beide"}
                      </td>
                      <td style={{ padding: "6px", verticalAlign: "top" }}>
                        {samplingSummary(profile)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <span className="placeholder-note">
              Das sind veröffentlichte Startwerte, keine Qualitätsgarantie. Quantisierung, Prompt
              und LM-Studio-Version können das Ergebnis verändern. Wo ein Hersteller keine Zahlen
              nennt, erfindet die Anwendung keine.
            </span>
          </details>
        </div>
      )}

      {isLocal && (
        <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleLoadModel}
            disabled={loadMutation.isPending || !current.modelName}
          >
            {loadMutation.isPending ? "Wird geladen…" : "In LM Studio laden"}
          </button>
          <span className="placeholder-note">
            {current.modelName
              ? "Lädt das gewählte Modell mit dem eingestellten Kontextfenster neu. Dauert je nach Modellgröße eine halbe Minute und länger; die bisher geladene Instanz wird vorher entladen."
              : "Erst ein Modell auswählen – bei „Automatisch“ gibt es nichts zu laden."}
          </span>
          {loadMessage && (
            <p role="status" className="status-success">
              {loadMessage}
            </p>
          )}
          {loadError && (
            <p role="alert" className="status-error">
              {loadError}
            </p>
          )}
        </div>
      )}

      <BudgetSection>
        <p className="placeholder-note" style={{ margin: 0 }}>
          Ein festes Ausgabebudget gibt es nicht: jeder KI-Aufruf rechnet mit dem wirksamen
          Kontextfenster und lässt davon die Antwortreserve frei. Was nicht hineinpasst, wird nicht
          gesendet, sondern als Hinweis benannt.
        </p>
        <ContextBudgetLine
          health={health}
          isLoading={healthQuery.isLoading}
          isError={healthQuery.isError}
        />
        {/* step bewusst "1"/"any": eine groebere Schrittweite machte
            dazwischenliegende Werte formal ungueltig, und der Browser
            verweigerte das Absenden - sichtbar nur als "Knopf tut nichts". */}
        {BUDGET_FIELDS.map((spec, index) => (
          <BudgetInput
            key={spec.field}
            spec={spec}
            value={current.budget[spec.field]}
            defaultInput={budgetDefaults[index]?.input}
            defaultLabel={budgetDefaults[index]?.label}
            onChange={(value) => patchBudget(spec.field, value)}
            onReset={() => {
              const defaultInput = budgetDefaults[index]?.input;
              if (defaultInput !== undefined) resetBudget(spec.field, defaultInput);
            }}
          />
        ))}
        <div className="form-field" style={{ width: "100%" }}>
          <label
            htmlFor="ai-context-line-input"
            style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}
          >
            <input
              id="ai-context-line-input"
              type="checkbox"
              checked={current.contextLineEnabled}
              onChange={(event) => patch({ contextLineEnabled: event.target.checked })}
            />
            <span className="form-label" style={{ margin: 0 }}>
              Kontextzeile an das Modell
            </span>
          </label>
          <span className="placeholder-note">
            {typeof contextLineDefault === "boolean"
              ? `Vorgabe: ${contextLineDefault ? "an" : "aus"}. `
              : ""}
            Das KI-Feld zeigt die Belegung des Fensters („Kontext: E von W Token belegt“) und sendet
            sie dem Modell mit.
          </span>
        </div>
      </BudgetSection>

      <div className="form-field" style={{ width: "100%", maxWidth: "560px" }}>
        <label className="form-label" htmlFor="ai-threshold-input">
          Schwellenwert für automatische Übernahme ({current.threshold}%)
        </label>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
          <input
            id="ai-threshold-input"
            type="range"
            min="50"
            max="100"
            step="5"
            value={current.threshold}
            onChange={(event) => patch({ threshold: Number(event.target.value) })}
            style={{ flex: 1 }}
          />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "13px", minWidth: "40px" }}>
            {current.threshold}%
          </span>
        </div>
      </div>

      <AIWarningList warnings={health?.warnings ?? []} />

      {health && (
        <p className="placeholder-note" style={{ margin: 0 }}>
          Status: {health.message}
        </p>
      )}

      {formError && (
        <p role="alert" className="status-error" style={{ margin: 0 }}>
          {formError}
        </p>
      )}

      {savedSuccess && (
        <p role="status" className="status-success" style={{ margin: 0 }}>
          Gespeichert.
        </p>
      )}

      <button type="submit" className="btn-primary" disabled={updateMutation.isPending}>
        {updateMutation.isPending ? "Wird gespeichert…" : "Einstellungen speichern"}
      </button>
    </form>
  );
}

interface HouseholdMembersCardProps {
  householdId: string;
  isOwner: boolean;
}

function HouseholdMembersCard({ householdId, isOwner }: HouseholdMembersCardProps) {
  const { accessToken } = useAuth();
  const queryClient = useQueryClient();
  const { formatDate: formatMemberDate } = useFormatDate();
  const [inviteRole, setInviteRole] = useState<HouseholdRole>("editor");
  const [copiedInviteId, setCopiedInviteId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  const membersQuery = useQuery({
    queryKey: ["household-members", householdId],
    queryFn: () => listHouseholdMembers(accessToken as string, householdId),
    enabled: Boolean(accessToken),
  });

  const invitesQuery = useQuery({
    queryKey: ["household-invites", householdId],
    queryFn: () => listHouseholdInvites(accessToken as string, householdId),
    enabled: Boolean(accessToken) && isOwner,
  });

  const createInviteMutation = useMutation({
    mutationFn: (role: HouseholdRole) =>
      createHouseholdInvite(accessToken as string, householdId, role),
    onSuccess: () => {
      setCreateError(null);
      void queryClient.invalidateQueries({ queryKey: ["household-invites", householdId] });
    },
    onError: (error: unknown) => {
      setCreateError(
        error instanceof Error ? error.message : "Einladungscode konnte nicht erstellt werden.",
      );
    },
  });

  const revokeInviteMutation = useMutation({
    mutationFn: (inviteId: string) =>
      revokeHouseholdInvite(accessToken as string, householdId, inviteId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["household-invites", householdId] });
    },
  });

  const openInvites = (invitesQuery.data ?? []).filter(
    (invite) => !invite.used_at && !invite.revoked_at,
  );

  const handleCopy = async (invite: { id: string; code: string }) => {
    const link = `${window.location.origin}/beitreten?code=${encodeURIComponent(invite.code)}`;
    try {
      await navigator.clipboard.writeText(link);
      setCopiedInviteId(invite.id);
      setTimeout(
        () => setCopiedInviteId((current) => (current === invite.id ? null : current)),
        2000,
      );
    } catch {
      // Clipboard evtl. nicht verfuegbar - Code bleibt sichtbar zum manuellen Kopieren.
    }
  };

  return (
    <div className="card" style={{ marginTop: "var(--space-4)" }}>
      <h2 className="card-heading">Mitglieder & Einladungen</h2>

      {membersQuery.isPending ? (
        <p className="card-body">Mitglieder werden geladen...</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: "0 0 var(--space-4) 0" }}>
          {(membersQuery.data ?? []).map((member) => (
            <li
              key={member.user_id}
              style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}
            >
              <span>{member.display_name || member.user_id}</span>
              <span className="text-secondary">
                {ROLE_LABELS[member.role as HouseholdRole] ?? member.role}
              </span>
            </li>
          ))}
        </ul>
      )}

      <p className="card-body">
        <Link to="/beitreten">Einladungscode einlösen</Link> (um einem weiteren Haushalt
        beizutreten).
      </p>

      {isOwner && (
        <>
          <div className="inline-form" style={{ marginTop: "var(--space-3)" }}>
            <label className="form-field">
              <span className="form-label">Rolle für neue Einladung</span>
              <select
                className="form-input"
                value={inviteRole}
                onChange={(event) => setInviteRole(event.target.value as HouseholdRole)}
              >
                <option value="editor">Bearbeiter</option>
                <option value="viewer">Betrachter</option>
                <option value="child">Eingeschränkt</option>
              </select>
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={createInviteMutation.isPending}
              onClick={() => createInviteMutation.mutate(inviteRole)}
            >
              {createInviteMutation.isPending ? "Wird erstellt…" : "Code erstellen"}
            </button>
          </div>

          {createError && (
            <p role="alert" className="status-error">
              {createError}
            </p>
          )}

          {openInvites.length > 0 && (
            <ul style={{ listStyle: "none", padding: 0, marginTop: "var(--space-3)" }}>
              {openInvites.map((invite) => (
                <li
                  key={invite.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--space-2)",
                    padding: "4px 0",
                  }}
                >
                  <code>{invite.code}</code>
                  <span className="text-secondary">
                    {ROLE_LABELS[invite.role as HouseholdRole] ?? invite.role} · gültig bis{" "}
                    {formatMemberDate(invite.expires_at)}
                  </span>
                  <button
                    type="button"
                    className="btn-secondary text-xs"
                    onClick={() => void handleCopy(invite)}
                  >
                    {copiedInviteId === invite.id ? "Kopiert!" : "Kopieren"}
                  </button>
                  <button
                    type="button"
                    className="btn-secondary text-xs"
                    disabled={revokeInviteMutation.isPending}
                    onClick={() => revokeInviteMutation.mutate(invite.id)}
                  >
                    Widerrufen
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function DateFormatCard() {
  const { dateFormat } = useHousehold();
  const updateProfile = useUpdateProfile();
  const today = useMemo(() => new Date(), []);

  const handleChange = (event: ChangeEvent<HTMLSelectElement>) => {
    updateProfile.mutate({ date_format: event.target.value as DateFormatPreference });
  };

  return (
    <div className="card" style={{ marginTop: "var(--space-4)" }}>
      <h2 className="card-heading">Anzeige</h2>
      <p className="card-body" style={{ marginBottom: "var(--space-4)" }}>
        Reihenfolge von Tag, Monat und Jahr für Datumsanzeigen in der gesamten App.
      </p>
      <label className="field">
        <span>Datumsformat</span>
        <select value={dateFormat} onChange={handleChange} disabled={updateProfile.isPending}>
          {(Object.keys(DATE_FORMAT_LABELS) as DateFormatPreference[]).map((option) => (
            <option key={option} value={option}>
              {DATE_FORMAT_LABELS[option]}
            </option>
          ))}
        </select>
      </label>
      <p className="card-body" style={{ marginTop: "var(--space-2)" }}>
        Beispiel (heute): {formatDate(today, dateFormat)}
      </p>
      {updateProfile.isError && (
        <p className="form-error">Datumsformat konnte nicht gespeichert werden.</p>
      )}
    </div>
  );
}

type SettingsTab = "haushalt" | "mitglieder" | "anzeige" | "ki" | "sicherung" | "system";

const SETTINGS_TABS: TabDefinition<SettingsTab>[] = [
  { key: "haushalt", label: "Haushalt" },
  { key: "mitglieder", label: "Mitglieder" },
  { key: "anzeige", label: "Anzeige" },
  { key: "ki", label: "KI" },
  { key: "sicherung", label: "Sicherung" },
  { key: "system", label: "System" },
];

function isSettingsTab(value: string | null): value is SettingsTab {
  return SETTINGS_TABS.some((tab) => tab.key === value);
}

export function SettingsPage() {
  const { selectedHousehold, selectedHouseholdId } = useHousehold();
  // Der Reiter steht in der Adresse (?tab=ki): Hinweise anderer Seiten
  // ("Einstellungen > KI") verlinken so direkt auf den richtigen Reiter, und
  // ein Neuladen bleibt dort.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const activeTab: SettingsTab = isSettingsTab(requestedTab) ? requestedTab : "haushalt";
  const setActiveTab = (tab: SettingsTab) => {
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (tab === "haushalt") next.delete("tab");
        else next.set("tab", tab);
        return next;
      },
      { replace: true },
    );
  };

  return (
    <section className="page">
      <div className="page-header">
        <h1 className="page-heading">Einstellungen</h1>
      </div>

      <Tabs
        idPrefix="settings"
        label="Einstellungsbereiche"
        tabs={SETTINGS_TABS}
        active={activeTab}
        onChange={setActiveTab}
      />

      <TabPanel idPrefix="settings" tabKey="haushalt" active={activeTab}>
        <div className="card" style={{ marginTop: "var(--space-4)" }}>
          <h2 className="card-heading">Haushalt umbenennen</h2>
          {selectedHousehold && selectedHouseholdId ? (
            <HouseholdNameForm
              key={selectedHouseholdId}
              householdId={selectedHouseholdId}
              initialName={selectedHousehold.name}
            />
          ) : (
            <p>Kein Haushalt ausgewaehlt.</p>
          )}
        </div>
      </TabPanel>

      <TabPanel idPrefix="settings" tabKey="mitglieder" active={activeTab}>
        {selectedHousehold && selectedHouseholdId ? (
          <HouseholdMembersCard
            key={selectedHouseholdId}
            householdId={selectedHouseholdId}
            isOwner={selectedHousehold.role === "owner"}
          />
        ) : (
          <p className="card-body" style={{ marginTop: "var(--space-4)" }}>
            Kein Haushalt ausgewaehlt.
          </p>
        )}
      </TabPanel>

      <TabPanel idPrefix="settings" tabKey="anzeige" active={activeTab}>
        <DesignCard />
        <DateFormatCard />
      </TabPanel>

      <TabPanel idPrefix="settings" tabKey="ki" active={activeTab}>
        <div className="card" style={{ marginTop: "var(--space-4)" }}>
          <h2 className="card-heading">KI-Anbieter und Modell</h2>
          <p className="card-body" style={{ marginBottom: "var(--space-4)" }}>
            KI-Schalter, Anbieter (lokal oder Claude) und Modell, Kontext und Budget sowie der
            Schwellenwert für die automatische Übernahme.
          </p>
          <AISettingsForm />
        </div>
      </TabPanel>

      <TabPanel idPrefix="settings" tabKey="sicherung" active={activeTab}>
        <BackupPanel />
      </TabPanel>

      <TabPanel idPrefix="settings" tabKey="system" active={activeTab}>
        <div className="card" style={{ marginTop: "var(--space-4)" }}>
          <h2 className="card-heading">Systemstatus</h2>
          <SystemStatusPanel />
        </div>
      </TabPanel>
    </section>
  );
}
