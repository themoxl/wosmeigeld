import { useAiRuntime } from "../hooks/useAiTaskQueue";
import type { AIRuntimeGpu, AIRuntimeStatus } from "../lib/types";

// Beantwortet die Frage, die während eines Laufs offenblieb: womit wird
// gerade gearbeitet? Modell, Kontextfenster und GPU standen bisher nur
// verstreut in den Einstellungen bzw. gar nicht in der Oberfläche — sichtbar
// wurde das Modell erst nachträglich am fertigen Ergebnis.

const integer = new Intl.NumberFormat("de-DE");
const oneDecimal = new Intl.NumberFormat("de-DE", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function gigabytes(megabytes: number): string {
  return oneDecimal.format(megabytes / 1024);
}

const REASONING_LABEL: Record<string, string> = {
  off: "aus",
  none: "aus",
  minimal: "minimal",
  low: "niedrig",
  medium: "mittel",
  high: "hoch",
  xhigh: "sehr hoch",
  max: "maximal",
  on: "an",
};

function reasoningText(runtime: AIRuntimeStatus): string {
  if (!runtime.reasoning) return "Reasoning: Modellvorgabe";
  return `Reasoning: ${REASONING_LABEL[runtime.reasoning] ?? runtime.reasoning}`;
}

function modelText(runtime: AIRuntimeStatus): string {
  const model = runtime.active_model;
  if (!model) return runtime.configured_model ?? "Kein Modell ausgewählt";
  const detail = [model.parameters, model.quantization].filter(Boolean).join(" · ");
  const name = model.display_name ?? model.key;
  return detail ? `${name} · ${detail}` : name;
}

/** Quelle des Fensters in Worten statt als Schnittstellenpfad. */
function sourceText(source: string): string {
  if (source.startsWith("/api/v1/models") || source.startsWith("/api/v0/models")) {
    return "geladen in LM Studio";
  }
  if (source.startsWith("Ollama")) return "geladen in Ollama";
  if (source.startsWith("Kontext höchstens")) return "Kontext höchstens";
  return source;
}

/**
 * Wirksames Fenster W = min(geladenes Fenster, "Kontext höchstens") samt
 * Quelle. /v1/ai/runtime rechnet es mit derselben Funktion wie der KI-Kern
 * (app/llm/window) und liefert es als `window`; der Streifen zeigt es nur an.
 *
 * Bewusst nicht aus /v1/ai/health: der Status führt einen Vorabtest am Modell
 * aus und darf nicht im Takt dieses Streifens laufen.
 */
function contextText(runtime: AIRuntimeStatus): string | null {
  const window = runtime.window;
  if (!window) return null;
  if (window.effective == null) {
    return window.reason ? `Fenster unbekannt · ${window.reason}` : null;
  }
  const parts = [`${integer.format(window.effective)} Token wirksam`];
  if (window.source) parts.push(`Quelle: ${sourceText(window.source)}`);
  if (window.loaded != null && window.cap != null && window.cap < window.loaded) {
    parts.push(`geladen ${integer.format(window.loaded)}`);
  }
  if (window.max_context && window.max_context !== window.loaded) {
    parts.push(`Modell kann ${integer.format(window.max_context)}`);
  }
  return parts.join(" · ");
}

function gpuText(gpu: AIRuntimeGpu): string {
  const parts = [
    gpu.name,
    `${gigabytes(gpu.memory_used_mb)} / ${gigabytes(gpu.memory_total_mb)} GB`,
    `${gpu.utilization_percent} % Last`,
  ];
  if (gpu.temperature_celsius != null) parts.push(`${gpu.temperature_celsius} °C`);
  return parts.join(" · ");
}

function toneOf(runtime: AIRuntimeStatus): "ready" | "waiting" | "down" {
  if (!runtime.is_connected) return "down";
  return runtime.active_model ? "ready" : "waiting";
}

/** GPU-Zeilen - nur lokal: bei Claude rechnet keine GPU auf dieser Seite. */
function gpuRows(runtime: AIRuntimeStatus) {
  const gpus = runtime.gpus ?? [];
  if (gpus.length > 0) {
    return gpus.map((gpu, index) => (
      <p className="ai-runtime__row" key={`${gpu.name}-${index}`}>
        <span className="ai-runtime__label">GPU</span>
        <span>{gpuText(gpu)}</span>
      </p>
    ));
  }
  return (
    <p className="ai-runtime__row">
      <span className="ai-runtime__label">GPU</span>
      <span>{runtime.gpu_detail ?? "keine GPU-Daten"}</span>
    </p>
  );
}

export function AiRuntimeStrip({ active }: { active: boolean }) {
  const runtimeQuery = useAiRuntime(active);
  const runtime = runtimeQuery.data;

  if (runtimeQuery.isLoading && !runtime) {
    return (
      <section className="ai-runtime ai-runtime--waiting" aria-label="Laufzeit der lokalen KI">
        <p className="ai-runtime__row">Laufzeit wird geprüft …</p>
      </section>
    );
  }

  if (!runtime) {
    return (
      <section className="ai-runtime ai-runtime--down" aria-label="Laufzeit der lokalen KI">
        <p className="ai-runtime__row">Laufzeit der lokalen KI ist nicht abrufbar.</p>
      </section>
    );
  }

  const context = contextText(runtime);
  const cloud = runtime.provider === "anthropic";
  const name = cloud ? "Claude (Anthropic)" : "Lokale KI";
  return (
    <section
      className={`ai-runtime ai-runtime--${toneOf(runtime)}`}
      aria-label={cloud ? "Laufzeit der KI (Claude)" : "Laufzeit der lokalen KI"}
    >
      <p className="ai-runtime__row ai-runtime__row--head">
        <span className="ai-runtime__dot" aria-hidden="true" />
        <span>{runtime.is_connected ? name : `${name} nicht erreichbar`}</span>
        {runtime.base_url && <code className="ai-runtime__url">{runtime.base_url}</code>}
      </p>

      <p className="ai-runtime__row">
        <span className="ai-runtime__label">Modell</span>
        <span>{modelText(runtime)}</span>
      </p>

      {context && (
        <p className="ai-runtime__row">
          <span className="ai-runtime__label">Kontext</span>
          <span>
            {context} · {reasoningText(runtime)}
          </span>
        </p>
      )}

      {!cloud && gpuRows(runtime)}

      <p className="ai-runtime__note">{runtime.message}</p>
    </section>
  );
}
