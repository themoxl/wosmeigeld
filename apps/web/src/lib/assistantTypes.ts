// Typen des KI-Felds (Chat zu den eigenen Finanzen), wortgleich aus dem
// verbindlichen Vertrag docs/KI-FELD-SCHNITTSTELLE.md.
//
// Bewusst eine eigene Datei: Backend und Oberflaeche entstehen parallel. Sobald
// die Schemata in services/api stehen und packages/contracts neu generiert ist
// (pnpm run generate:api-types), werden die Schnittstellentypen hier durch
// Aliase auf components["schemas"][...] ersetzt - wie in lib/types.ts. Die
// Strom-Ereignisse (unten) stehen nicht im OpenAPI-Schema und bleiben hier.

export type MessageStatus = "queued" | "running" | "done" | "failed" | "cancelled";
/** null = Haushaltseinstellung */
export type ReasoningOverride = "on" | "off" | null;
export type ReasoningCapability = "switchable" | "always" | "none" | "unknown";

export interface ContextMeter {
  run_id: string;
  call_nr: number;
  /** E: Eingabe des letzten Modellaufrufs */
  input_tokens: number;
  /** W: wirksames Fenster */
  window_tokens: number;
  /** R: Antwortreserve; E > W - R heisst "voll" */
  reserve_tokens: number | null;
  /** true = vom Modellserver gemessen, false = geschaetzt */
  exact: boolean;
}

export interface NumberCheckItem {
  /** Zeichenpositionen in content */
  start: number;
  end: number;
  text: string;
  status: "belegt" | "unbelegt";
  /** Werkzeugergebnis-Kennungen, z. B. ["W3"] */
  sources: string[];
}

export interface NumberCheck {
  items: NumberCheckItem[];
  unverified: number;
}

export interface RunSummary {
  queries: number;
  warnings: number;
  unverified_numbers: number;
}

export interface ChatMessage {
  id: string;
  seq: number;
  role: "user" | "assistant";
  /** Antworttext ohne <think>-Bloecke */
  content: string;
  status: MessageStatus;
  /** nur assistant */
  run_id: string | null;
  created_at: string;
  /** z. B. model_budget_exhausted */
  error_code: string | null;
  /** deutscher Klartext mit Hinweis auf die Einstellung */
  error_message: string | null;
  numbers: NumberCheck | null;
  context_meter: ContextMeter | null;
  run_summary: RunSummary | null;
}

/** Verdichtung des Verlaufs */
export interface ChatSummary {
  nr: number;
  covers_until_message_id: string;
  text: string;
  run_id: string;
  tokens_before: number | null;
  tokens_after: number | null;
  unverified_numbers: number;
  created_at: string;
}

export interface ChatListItem {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
  message_count: number;
}

export interface ChatDetail {
  id: string;
  title: string;
  reasoning_override: ReasoningOverride;
  /** vom geladenen Modell */
  reasoning_capability: ReasoningCapability;
  summary: ChatSummary | null;
  /** laufende Verdichtung - auch nach dem Neuladen sichtbar; danach null */
  active_compaction_run_id: string | null;
  messages: ChatMessage[];
  /** = letzter bekannter Stand */
  context_meter: ContextMeter | null;
}

export interface SendResponse {
  user_message: ChatMessage;
  /** status "queued" oder "running" */
  assistant_message: ChatMessage;
  run_id: string;
  /** 0 = laeuft sofort */
  queue_position: number;
}

export interface RunBudget {
  W: number;
  W_free: number;
  E: number;
  E_estimated: number;
  exact: boolean;
  A: number;
  R: number;
  max_tokens: number;
  parts: {
    system: number;
    history: number;
    tool_results: number;
    tool_defs: number;
    current: number;
  };
}

export interface RunCall {
  nr: number;
  step: string;
  retry_of: number | null;
  retry_reason: string | null;
  started_at: string;
  duration_ms: number | null;
  ttft_ms: number | null;
  finish_reason: string | null;
  model: string | null;
  budget: RunBudget | null;
  tools_offered: string[];
  usage: { prompt: number | null; completion: number | null; reasoning: number | null };
  tokens_per_s: number | null;
  answer_text: string | null;
  tool_call_names: string[];
  error: string | null;
}

export type RunToolStatus = "ok" | "rejected" | "error" | "cancelled";

export interface RunToolResult {
  /** "W1", "W2", ... eindeutig je Chat */
  key: string;
  call_nr: number;
  tool_name: string;
  args: Record<string, unknown>;
  status: RunToolStatus;
  /** genau so, wie das Modell es bekam */
  result_text: string;
  row_count: number | null;
  duration_ms: number | null;
}

export type RunEventLevel = "info" | "warnung" | "fehler";

export interface RunEvent {
  seq: number;
  kind: string;
  level: RunEventLevel;
  message: string;
  details: Record<string, unknown> | null;
  call_nr: number | null;
}

export interface RunLog {
  id: string;
  chat_id: string;
  message_id: string;
  mode: "answer" | "compact";
  status: MessageStatus;
  end_reason: "answer" | "stall" | "runtime" | "cancelled" | "failed" | null;
  model_configured: string | null;
  model_answering: string | null;
  window: {
    loaded: number | null;
    cap: number | null;
    effective: number | null;
    source: string | null;
  };
  reasoning_sent: string | null;
  started_at: string | null;
  finished_at: string | null;
  totals: {
    calls: number;
    tool_calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    reasoning_tokens: number;
    duration_ms: number;
  };
  error_code: string | null;
  error_message: string | null;
  calls: RunCall[];
  tools: RunToolResult[];
  events: RunEvent[];
}

// ---------------------------------------------------------------------------
// Koerper der Anfragen
// ---------------------------------------------------------------------------

export interface ChatCreateInput {
  title?: string;
}

export interface ChatUpdateInput {
  title?: string;
  reasoning_override?: ReasoningOverride;
}

export interface SendMessageInput {
  content: string;
  request_id: string;
  reasoning_override?: ReasoningOverride;
}

export interface CompactInput {
  focus?: string;
}

export interface RetryInput {
  reasoning_override?: ReasoningOverride;
}

// ---------------------------------------------------------------------------
// Strom GET /assistant/runs/{run_id}/stream (application/x-ndjson)
// ---------------------------------------------------------------------------
//
// Zeilen mit `seq` sind gespeichert und werden nach `?after=<seq>`
// nachgespielt; `ping`, `think`, `delta` und `targs` sind fluechtig.

export interface StreamPing {
  type: "ping";
}

export interface StreamStatus {
  type: "status";
  seq: number;
  status: MessageStatus;
  queue_position?: number;
}

export interface StreamCallStart {
  type: "call_start";
  seq: number;
  nr: number;
  step: string;
  budget: RunBudget | null;
}

export interface StreamThink {
  type: "think";
  /** Denkzeichen des laufenden Aufrufs insgesamt */
  chars: number;
  /** neues Stueck Denktext - nur live, wird nie gespeichert */
  text: string;
}

export interface StreamDelta {
  type: "delta";
  text: string;
}

export interface StreamTool {
  type: "tool";
  seq: number;
  key: string;
  name: string;
  args: Record<string, unknown>;
}

export interface StreamToolArgs {
  type: "targs";
  key: string;
  text: string;
}

export interface StreamToolResult {
  type: "tool_result";
  seq: number;
  key: string;
  status: RunToolStatus;
  row_count: number | null;
  preview: string;
}

export interface StreamRunEvent {
  type: "event";
  seq: number;
  kind: string;
  level: RunEventLevel;
  message: string;
  details?: Record<string, unknown> | null;
  call_nr?: number | null;
}

export interface StreamMeter extends ContextMeter {
  type: "meter";
  seq: number;
}

export interface StreamDone {
  type: "done";
  seq: number;
  message: ChatMessage;
}

export interface StreamError {
  type: "error";
  seq: number;
  code: string;
  message: string;
  message_obj: ChatMessage | null;
}

export type AssistantStreamEvent =
  | StreamPing
  | StreamStatus
  | StreamCallStart
  | StreamThink
  | StreamDelta
  | StreamTool
  | StreamToolArgs
  | StreamToolResult
  | StreamRunEvent
  | StreamMeter
  | StreamDone
  | StreamError;
