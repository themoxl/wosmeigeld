// NDJSON-Leser fuer den Strom eines KI-Laufs (docs/KI-FELD-SCHNITTSTELLE.md,
// Abschnitt "Strom").
//
// Rein und ohne React: `open` liefert die Antwort (in der App
// openAssistantRunStream, im Test ein gefaelschter ReadableStream), jedes
// Ereignis geht an `onEvent`, der Verbindungszustand an `onConnection`.
//
// - Zeilen koennen ueber Chunk-Grenzen gehen; erst ein "\n" schliesst sie ab.
// - `ping` haelt nur die Verbindung wach und wird nicht weitergereicht.
// - Jede gespeicherte Zeile traegt `seq`; die letzte wird gemerkt. Reisst der
//   Strom ab, wird mit `?after=<seq>` neu verbunden - der Server spielt ab
//   dort nach. Doppelt gelieferte seq werden verworfen.
// - Wiederholt wird begrenzt (Vorgabe 3 Versuche in Folge) und sichtbar
//   (onConnection "retrying" mit Versuch, Grund und Wartezeit). Danach endet
//   der Leser mit "lost" und der Grund steht in der Oberflaeche - kein stilles
//   Endlos-Wiederholen. Ein 4xx des Servers wird gar nicht wiederholt.
// - Kommt laenger als `idleTimeoutMs` nichts (der Server pingt alle 10 s),
//   gilt die Verbindung als tot und wird wie ein Abriss behandelt.
// - Ein AbortSignal beendet alles sofort (Ergebnis "aborted").

import { ApiError } from "./api";
import type { AssistantStreamEvent, ChatMessage } from "./assistantTypes";

/** Setzt NDJSON-Zeilen ueber Chunk-Grenzen zusammen. */
export function createNdjsonLineBuffer() {
  let rest = "";
  const clean = (line: string) => line.replace(/\r$/, "");
  return {
    /** Nimmt ein Textstueck an und liefert alle darin abgeschlossenen Zeilen. */
    push(chunk: string): string[] {
      rest += chunk;
      const parts = rest.split("\n");
      rest = parts.pop() ?? "";
      return parts.map(clean).filter((line) => line.trim() !== "");
    },
    /** Liefert eine letzte Zeile ohne abschliessendes "\n" (Stromende). */
    flush(): string[] {
      const last = clean(rest);
      rest = "";
      return last.trim() ? [last] : [];
    },
  };
}

/** Eine Zeile als Ereignis; null, wenn sie kein JSON-Objekt mit `type` ist. */
export function parseStreamLine(line: string): AssistantStreamEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  if (typeof (parsed as { type?: unknown }).type !== "string") return null;
  return parsed as AssistantStreamEvent;
}

export type StreamConnectionState =
  | { phase: "connecting"; after: number | null }
  | { phase: "open"; after: number | null }
  | {
      phase: "retrying";
      attempt: number;
      maxRetries: number;
      delayMs: number;
      reason: string;
      after: number | null;
    };

export type AssistantStreamOutcome =
  | { kind: "done"; message: ChatMessage; lastSeq: number | null }
  | {
      kind: "error";
      code: string;
      message: string;
      messageObj: ChatMessage | null;
      lastSeq: number | null;
    }
  | { kind: "aborted"; lastSeq: number | null }
  | {
      kind: "lost";
      reason: string;
      /** HTTP-Status, wenn der Server die Wiederaufnahme abgelehnt hat */
      status: number | null;
      attempts: number;
      lastSeq: number | null;
    };

export interface AssistantStreamOptions {
  /** Oeffnet die Verbindung; `after` = letzte gesehene seq oder null. */
  open: (after: number | null, signal: AbortSignal) => Promise<Response>;
  onEvent: (event: AssistantStreamEvent) => void;
  onConnection?: (state: StreamConnectionState) => void;
  /** Zeile, die kein gueltiges Ereignis ist - wird gezaehlt, nicht still verworfen. */
  onInvalidLine?: (line: string) => void;
  /** Fortsetzen ab dieser seq (z. B. nach Neuaufbau der Ansicht). */
  after?: number | null;
  signal?: AbortSignal;
  /** Wiederholungen in Folge ohne Fortschritt, bevor der Leser aufgibt. */
  maxRetries?: number;
  retryDelayMs?: (attempt: number) => number;
  /** Ohne jedes Byte so lange: Verbindung gilt als abgerissen. */
  idleTimeoutMs?: number;
}

export const STREAM_MAX_RETRIES = 3;
/** 3,5 Ping-Abstaende des Servers (10 s). */
export const STREAM_IDLE_TIMEOUT_MS = 35_000;

const defaultRetryDelay = (attempt: number) => 1000 * 2 ** (attempt - 1);

function waitUnlessAborted(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    }
    signal?.addEventListener("abort", done, { once: true });
  });
}

function reasonOf(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Die Verbindung zum Server ist abgerissen.";
}

/** Darf nach diesem Fehler neu verbunden werden? 4xx (ausser 408/429) nicht. */
function isRetriable(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.isNetworkError || error.status === undefined) {
    return true;
  }
  return error.status >= 500 || error.status === 408 || error.status === 429;
}

export async function readAssistantStream(
  options: AssistantStreamOptions,
): Promise<AssistantStreamOutcome> {
  const {
    open,
    onEvent,
    onConnection,
    onInvalidLine,
    signal,
    maxRetries = STREAM_MAX_RETRIES,
    retryDelayMs = defaultRetryDelay,
    idleTimeoutMs = STREAM_IDLE_TIMEOUT_MS,
  } = options;
  let lastSeq = options.after ?? null;
  let failures = 0;

  for (;;) {
    if (signal?.aborted) return { kind: "aborted", lastSeq };
    onConnection?.({ phase: "connecting", after: lastSeq });

    // Je Verbindung ein eigener Abbruch: aussen (Nutzer) oder Stille (Waechter).
    const attempt = new AbortController();
    const abortAttempt = () => attempt.abort();
    signal?.addEventListener("abort", abortAttempt, { once: true });
    let idle = false;
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    const armIdleWatch = () => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        idle = true;
        attempt.abort();
      }, idleTimeoutMs);
    };

    // Als Huelle, weil handleLine es setzt (TS verfolgt Zuweisungen in
    // Funktionen nicht und hielte `finished` sonst fuer immer null).
    const result: { finished: AssistantStreamOutcome | null } = { finished: null };
    // Fortschritt = ein Ereignis ausser ping. Nur dann zaehlt der Abriss
    // danach wieder als erster Versuch.
    let progressed = false;
    let reason = "Der Strom endete, bevor der Lauf abgeschlossen war.";

    const handleLine = (line: string) => {
      const event = parseStreamLine(line);
      if (!event) {
        onInvalidLine?.(line);
        return;
      }
      if (event.type === "ping") return;
      if ("seq" in event && typeof event.seq === "number") {
        if (lastSeq !== null && event.seq <= lastSeq) return;
        lastSeq = event.seq;
      }
      progressed = true;
      onEvent(event);
      if (event.type === "done") {
        result.finished = { kind: "done", message: event.message, lastSeq };
      } else if (event.type === "error") {
        result.finished = {
          kind: "error",
          code: event.code,
          message: event.message,
          messageObj: event.message_obj ?? null,
          lastSeq,
        };
      }
    };

    try {
      armIdleWatch();
      const response = await open(lastSeq, attempt.signal);
      if (!response.body) {
        throw new Error("Der Server hat keinen Strom geliefert.");
      }
      onConnection?.({ phase: "open", after: lastSeq });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const lines = createNdjsonLineBuffer();
      try {
        while (!result.finished) {
          const { value, done } = await reader.read();
          if (done) break;
          armIdleWatch();
          for (const line of lines.push(decoder.decode(value, { stream: true }))) {
            handleLine(line);
            if (result.finished) break;
          }
        }
        if (!result.finished) {
          for (const line of [...lines.push(decoder.decode()), ...lines.flush()]) {
            handleLine(line);
            if (result.finished) break;
          }
        }
      } finally {
        if (result.finished) {
          // Der Server schliesst nach done/error selbst; das hier raeumt nur auf.
          reader.cancel().catch(() => undefined);
        }
      }
    } catch (error) {
      if (signal?.aborted) return { kind: "aborted", lastSeq };
      if (!isRetriable(error)) {
        return {
          kind: "lost",
          reason: reasonOf(error),
          status: error instanceof ApiError ? (error.status ?? null) : null,
          attempts: failures,
          lastSeq,
        };
      }
      reason = idle
        ? `Seit ${Math.round(idleTimeoutMs / 1000)} s kam nichts mehr vom Server.`
        : reasonOf(error);
    } finally {
      clearTimeout(idleTimer);
      signal?.removeEventListener("abort", abortAttempt);
    }

    if (result.finished) return result.finished;
    if (signal?.aborted) return { kind: "aborted", lastSeq };

    failures = progressed ? 1 : failures + 1;
    if (failures > maxRetries) {
      return { kind: "lost", reason, status: null, attempts: maxRetries, lastSeq };
    }
    const delayMs = retryDelayMs(failures);
    onConnection?.({
      phase: "retrying",
      attempt: failures,
      maxRetries,
      delayMs,
      reason,
      after: lastSeq,
    });
    await waitUnlessAborted(delayMs, signal);
  }
}
