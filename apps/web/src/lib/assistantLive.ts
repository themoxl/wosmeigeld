// Live-Zustand eines laufenden KI-Laufs, gebaut aus den Strom-Ereignissen
// (lib/assistantStream.ts). Rein: jede Funktion liefert einen neuen Zustand.
//
// Der Denktext existiert nur hier - der Server speichert ihn nie
// (docs/KI-BUDGET-UND-KI-FELD-PLAN.md, E6). Nach `done` gilt die gespeicherte
// Nachricht; dieser Zustand zeigt nur, was bis dahin passiert.

import type {
  AssistantStreamEvent,
  ChatMessage,
  ContextMeter,
  MessageStatus,
  RunBudget,
  RunEvent,
  RunToolStatus,
} from "./assistantTypes";
import type { AssistantStreamOutcome, StreamConnectionState } from "./assistantStream";

/** Denktext wird nur als Schwanz gehalten; gezeigt werden ohnehin die letzten Zeilen. */
const THINK_TAIL_CHARS = 8000;

export interface LiveCall {
  nr: number;
  step: string;
  budget: RunBudget | null;
  /** Antworttext dieses Aufrufs aus `delta` */
  answerText: string;
  /** Schwanz des Denktexts aus `think` (nur live) */
  thinkText: string;
  /** Denkzeichen insgesamt laut Server */
  thinkChars: number;
}

export interface LiveTool {
  key: string;
  name: string;
  args: Record<string, unknown> | null;
  /** Argumente im Entstehen aus `targs` */
  argsText: string;
  /** "forming": Argumente entstehen noch, "running": aufgerufen, Ergebnis fehlt */
  status: "forming" | "running" | RunToolStatus;
  rowCount: number | null;
  preview: string | null;
  callNr: number | null;
}

export type LiveConnection = "connecting" | "open" | "retrying" | "lost" | "closed";

export type LiveFinal =
  | { kind: "done"; message: ChatMessage }
  | { kind: "error"; code: string; message: string; messageObj: ChatMessage | null };

export interface LiveRunState {
  runId: string;
  status: MessageStatus | null;
  queuePosition: number | null;
  calls: LiveCall[];
  tools: LiveTool[];
  events: RunEvent[];
  meter: ContextMeter | null;
  lastSeq: number | null;
  connection: LiveConnection;
  /** Klartext zur Verbindung (Wiederholung, Abriss), sonst null */
  connectionNote: string | null;
  final: LiveFinal | null;
  /** Zeilen, die kein gueltiges Ereignis waren */
  invalidLines: number;
}

export function createLiveRun(
  runId: string,
  seed: { status?: MessageStatus | null; queuePosition?: number | null } = {},
): LiveRunState {
  return {
    runId,
    status: seed.status ?? null,
    queuePosition: seed.queuePosition ?? null,
    calls: [],
    tools: [],
    events: [],
    meter: null,
    lastSeq: null,
    connection: "connecting",
    connectionNote: null,
    final: null,
    invalidLines: 0,
  };
}

function withSeq(state: LiveRunState, seq: number | undefined): LiveRunState {
  if (typeof seq !== "number" || (state.lastSeq !== null && seq <= state.lastSeq)) return state;
  return { ...state, lastSeq: seq };
}

function currentCallNr(state: LiveRunState): number | null {
  return state.calls.length ? state.calls[state.calls.length - 1]!.nr : null;
}

/** Aendert den laufenden Aufruf; gibt es noch keinen, entsteht Aufruf 0. */
function updateCurrentCall(
  state: LiveRunState,
  update: (call: LiveCall) => LiveCall,
): LiveRunState {
  const calls =
    state.calls.length > 0
      ? state.calls
      : [{ nr: 0, step: "", budget: null, answerText: "", thinkText: "", thinkChars: 0 }];
  const last = calls[calls.length - 1]!;
  return { ...state, calls: [...calls.slice(0, -1), update(last)] };
}

function upsertTool(
  state: LiveRunState,
  key: string,
  update: (tool: LiveTool) => LiveTool,
): LiveRunState {
  const index = state.tools.findIndex((tool) => tool.key === key);
  const base: LiveTool =
    index >= 0
      ? state.tools[index]!
      : {
          key,
          name: "",
          args: null,
          argsText: "",
          status: "forming",
          rowCount: null,
          preview: null,
          callNr: currentCallNr(state),
        };
  const next = update(base);
  const tools =
    index >= 0 ? state.tools.map((tool, i) => (i === index ? next : tool)) : [...state.tools, next];
  return { ...state, tools };
}

export function applyStreamEvent(state: LiveRunState, event: AssistantStreamEvent): LiveRunState {
  switch (event.type) {
    case "ping":
      return state;
    case "status":
      return withSeq(
        {
          ...state,
          status: event.status,
          queuePosition:
            event.status === "queued" ? (event.queue_position ?? state.queuePosition) : null,
        },
        event.seq,
      );
    case "call_start":
      return withSeq(
        {
          ...state,
          status: state.status === "queued" || state.status === null ? "running" : state.status,
          queuePosition: null,
          calls: [
            ...state.calls,
            {
              nr: event.nr,
              step: event.step,
              budget: event.budget ?? null,
              answerText: "",
              thinkText: "",
              thinkChars: 0,
            },
          ],
        },
        event.seq,
      );
    case "think":
      return updateCurrentCall(state, (call) => {
        const text = call.thinkText + event.text;
        return {
          ...call,
          thinkText: text.length > THINK_TAIL_CHARS ? text.slice(-THINK_TAIL_CHARS) : text,
          thinkChars: Math.max(call.thinkChars + event.text.length, event.chars ?? 0),
        };
      });
    case "delta":
      return updateCurrentCall(state, (call) => ({
        ...call,
        answerText: call.answerText + event.text,
      }));
    case "targs":
      return upsertTool(state, event.key, (tool) => ({
        ...tool,
        argsText: tool.argsText + event.text,
      }));
    case "tool":
      return withSeq(
        upsertTool(state, event.key, (tool) => ({
          ...tool,
          name: event.name,
          args: event.args ?? {},
          status: "running",
        })),
        event.seq,
      );
    case "tool_result":
      return withSeq(
        upsertTool(state, event.key, (tool) => ({
          ...tool,
          status: event.status,
          rowCount: event.row_count ?? null,
          preview: event.preview ?? null,
        })),
        event.seq,
      );
    case "event":
      return withSeq(
        {
          ...state,
          events: [
            ...state.events,
            {
              seq: event.seq,
              kind: event.kind,
              level: event.level,
              message: event.message,
              details: event.details ?? null,
              call_nr: event.call_nr ?? null,
            },
          ],
        },
        event.seq,
      );
    case "meter": {
      const { type: _type, seq, ...meter } = event;
      return withSeq({ ...state, meter }, seq);
    }
    case "done":
      return withSeq(
        {
          ...state,
          status: event.message.status,
          final: { kind: "done", message: event.message },
        },
        event.seq,
      );
    case "error":
      return withSeq(
        {
          ...state,
          status: event.message_obj?.status ?? "failed",
          final: {
            kind: "error",
            code: event.code,
            message: event.message,
            messageObj: event.message_obj ?? null,
          },
        },
        event.seq,
      );
    default:
      // Unbekannte Ereignisart (neuere API): seq trotzdem mitnehmen, damit
      // die Wiederaufnahme nicht hinter ihr ansetzt.
      return withSeq(state, (event as { seq?: number }).seq);
  }
}

const seconds = (ms: number) =>
  `${(ms / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} s`;

export function applyConnection(
  state: LiveRunState,
  connection: StreamConnectionState,
): LiveRunState {
  if (connection.phase === "retrying") {
    return {
      ...state,
      connection: "retrying",
      connectionNote: `Verbindung unterbrochen (${connection.reason}) – neuer Versuch ${connection.attempt}/${connection.maxRetries} in ${seconds(connection.delayMs)}.`,
    };
  }
  if (connection.phase === "open") {
    return { ...state, connection: "open", connectionNote: null };
  }
  // "connecting": eine laufende Wiederholung bleibt sichtbar, bis der Strom steht.
  return state.connection === "retrying" ? state : { ...state, connection: "connecting" };
}

export function applyOutcome(state: LiveRunState, outcome: AssistantStreamOutcome): LiveRunState {
  switch (outcome.kind) {
    case "done":
      return state.final
        ? { ...state, connection: "closed", connectionNote: null }
        : {
            ...state,
            connection: "closed",
            connectionNote: null,
            status: outcome.message.status,
            final: { kind: "done", message: outcome.message },
          };
    case "error":
      return state.final
        ? { ...state, connection: "closed", connectionNote: null }
        : {
            ...state,
            connection: "closed",
            connectionNote: null,
            status: outcome.messageObj?.status ?? "failed",
            final: {
              kind: "error",
              code: outcome.code,
              message: outcome.message,
              messageObj: outcome.messageObj,
            },
          };
    case "aborted":
      return { ...state, connection: "closed", connectionNote: null };
    case "lost":
      return {
        ...state,
        connection: "lost",
        connectionNote:
          outcome.status !== null
            ? `Der Server lehnt die Verbindung zu diesem Lauf ab: ${outcome.reason}`
            : `Verbindung zum Lauf verloren (${outcome.reason}). ${outcome.attempts} neue Versuche blieben ohne Erfolg.`,
      };
  }
}

/** Antworttext, der gerade entsteht: der des letzten Aufrufs. */
export function liveAnswerText(state: LiveRunState): string {
  return state.calls.length ? state.calls[state.calls.length - 1]!.answerText : "";
}

const number = (value: number) => value.toLocaleString("de-DE");

/** Eine Zeile, was der Lauf gerade tut. */
export function liveActivity(state: LiveRunState): string {
  if (state.final) return "abgeschlossen";
  if (state.status === "queued" || (state.status === null && state.calls.length === 0)) {
    if (state.queuePosition !== null && state.queuePosition > 0) {
      return `wartet in der Warteschlange · Platz ${number(state.queuePosition)}`;
    }
    return state.status === "queued" ? "wartet in der Warteschlange" : "verbindet …";
  }
  const nr = currentCallNr(state);
  const forming = state.tools.filter(
    (tool) => tool.callNr === nr && (tool.status === "forming" || tool.status === "running"),
  );
  if (forming.length) {
    const names = forming.map((tool) => tool.name || tool.key).join(", ");
    return `ruft ${names} …`;
  }
  const call = state.calls[state.calls.length - 1];
  if (call?.answerText) return "schreibt die Antwort …";
  if (call?.thinkChars) return `denkt … (${number(call.thinkChars)} Zeichen)`;
  return "wartet auf das Modell …";
}
