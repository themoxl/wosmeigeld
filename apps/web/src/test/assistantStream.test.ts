import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { openAssistantRunStream } from "../lib/assistantApi";
import {
  type StreamConnectionState,
  createNdjsonLineBuffer,
  readAssistantStream,
} from "../lib/assistantStream";
import type { AssistantStreamEvent, ChatMessage } from "../lib/assistantTypes";

const encoder = new TextEncoder();

function message(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "m-2",
    seq: 2,
    role: "assistant",
    content: "Im September 1.234,56 €.",
    status: "done",
    run_id: "run-1",
    created_at: "2026-10-03T10:00:00Z",
    error_code: null,
    error_message: null,
    numbers: null,
    context_meter: null,
    run_summary: null,
    ...overrides,
  };
}

const line = (value: unknown) => `${JSON.stringify(value)}\n`;

/** Antwort mit einem Strom aus genau diesen Byte-Stuecken; danach Ende oder Abriss. */
function response(chunks: Uint8Array[], end: "close" | "error" = "close"): Response {
  // Stueck fuer Stueck auf Nachfrage: ein error() im start() wuerfe sonst
  // auch die schon eingereihten Stuecke weg.
  const queue = [...chunks];
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      const next = queue.shift();
      if (next) controller.enqueue(next);
      else if (end === "close") controller.close();
      else controller.error(new TypeError("network error"));
    },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

/** Strom, der offen bleibt, bis die Verbindung abgebrochen wird. */
function hangingResponse(signal: AbortSignal, first: string[] = []): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const text of first) controller.enqueue(encoder.encode(text));
      signal.addEventListener("abort", () =>
        controller.error(new DOMException("Aborted", "AbortError")),
      );
    },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

/** Zerlegt Text an beliebigen Byte-Grenzen - auch mitten in Zeichen. */
function splitBytes(text: string, sizes: number[]): Uint8Array[] {
  const bytes = encoder.encode(text);
  const parts: Uint8Array[] = [];
  let offset = 0;
  for (const size of sizes) {
    parts.push(bytes.slice(offset, offset + size));
    offset += size;
  }
  if (offset < bytes.length) parts.push(bytes.slice(offset));
  return parts;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NDJSON-Zeilenpuffer", () => {
  it("setzt Zeilen ueber Chunk-Grenzen zusammen und liefert die letzte beim Abschluss", () => {
    const buffer = createNdjsonLineBuffer();
    expect(buffer.push('{"a":')).toEqual([]);
    expect(buffer.push('1}\n{"b"')).toEqual(['{"a":1}']);
    expect(buffer.push(":2}\r\n\n")).toEqual(['{"b":2}']);
    expect(buffer.push('{"c":3}')).toEqual([]);
    expect(buffer.flush()).toEqual(['{"c":3}']);
    expect(buffer.flush()).toEqual([]);
  });
});

describe("readAssistantStream", () => {
  it("liest Zeilen ueber Chunk-Grenzen (auch mitten im €), laesst ping weg und endet mit done", async () => {
    const final = message();
    const text =
      line({ type: "ping" }) +
      line({ type: "status", seq: 1, status: "running" }) +
      line({ type: "call_start", seq: 2, nr: 1, step: "Antwort", budget: null }) +
      line({ type: "delta", text: "Im September " }) +
      line({ type: "ping" }) +
      line({ type: "delta", text: "1.234,56 €." }) +
      line({ type: "done", seq: 3, message: final });
    // Grenzen so gelegt, dass Zeilen und das Euro-Zeichen (3 Byte) zerschnitten werden.
    const euro = encoder.encode(text).findIndex((byte) => byte === 0xe2);
    const chunks = splitBytes(text, [7, 30, 41, euro - 78 + 1, 1, 5]);

    const events: AssistantStreamEvent[] = [];
    const open = vi.fn((_after: number | null) => Promise.resolve(response(chunks)));
    const outcome = await readAssistantStream({ open, onEvent: (event) => events.push(event) });

    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0]![0]).toBeNull();
    expect(events.map((event) => event.type)).toEqual([
      "status",
      "call_start",
      "delta",
      "delta",
      "done",
    ]);
    expect(events.filter((event) => event.type === "delta").map((event) => event.text)).toEqual([
      "Im September ",
      "1.234,56 €.",
    ]);
    expect(outcome).toEqual({ kind: "done", message: final, lastSeq: 3 });
  });

  it("verbindet nach einem Abriss mit after=<letzte seq> neu und verwirft doppelte seq", async () => {
    const final = message();
    const opens: (number | null)[] = [];
    const connections: StreamConnectionState[] = [];
    const events: AssistantStreamEvent[] = [];
    const open = vi.fn((after: number | null) => {
      opens.push(after);
      if (opens.length === 1) {
        return Promise.resolve(
          response(
            [
              encoder.encode(
                line({ type: "status", seq: 1, status: "running" }) +
                  line({ type: "call_start", seq: 2, nr: 1, step: "Antwort", budget: null }) +
                  '{"type":"delta","te',
              ),
            ],
            "error",
          ),
        );
      }
      return Promise.resolve(
        response([
          encoder.encode(
            line({ type: "call_start", seq: 2, nr: 1, step: "Antwort", budget: null }) +
              line({ type: "tool", seq: 3, key: "W1", name: "query_analytics", args: {} }) +
              line({ type: "done", seq: 4, message: final }),
          ),
        ]),
      );
    });

    const outcome = await readAssistantStream({
      open,
      onEvent: (event) => events.push(event),
      onConnection: (state) => connections.push(state),
      retryDelayMs: () => 0,
    });

    expect(opens).toEqual([null, 2]);
    expect(events.map((event) => event.type)).toEqual(["status", "call_start", "tool", "done"]);
    const retry = connections.find((state) => state.phase === "retrying");
    expect(retry).toMatchObject({ phase: "retrying", attempt: 1, maxRetries: 3, after: 2 });
    expect(outcome).toMatchObject({ kind: "done", lastSeq: 4 });
  });

  it("setzt mit einer vorgegebenen seq fort", async () => {
    const open = vi.fn((_after: number | null) =>
      Promise.resolve(
        response([encoder.encode(line({ type: "done", seq: 8, message: message() }))]),
      ),
    );
    await readAssistantStream({ open, onEvent: () => undefined, after: 7 });
    expect(open.mock.calls[0]![0]).toBe(7);
  });

  it("liefert error mit Kennung, Klartext und Nachricht", async () => {
    const failed = message({
      status: "failed",
      content: "",
      error_code: "model_budget_exhausted",
      error_message: "Das Denken hat das Antwortbudget verbraucht.",
    });
    const open = vi.fn(() =>
      Promise.resolve(
        response([
          encoder.encode(
            line({
              type: "error",
              seq: 9,
              code: "model_budget_exhausted",
              message: "Das Denken hat das Antwortbudget verbraucht.",
              message_obj: failed,
            }),
          ),
        ]),
      ),
    );
    const outcome = await readAssistantStream({ open, onEvent: () => undefined });
    expect(outcome).toEqual({
      kind: "error",
      code: "model_budget_exhausted",
      message: "Das Denken hat das Antwortbudget verbraucht.",
      messageObj: failed,
      lastSeq: 9,
    });
  });

  it("gibt nach begrenzten, sichtbaren Versuchen auf statt endlos zu wiederholen", async () => {
    const connections: StreamConnectionState[] = [];
    const open = vi.fn(() =>
      Promise.reject(new ApiError("Die API ist nicht erreichbar.", undefined, true)),
    );
    const outcome = await readAssistantStream({
      open,
      onEvent: () => undefined,
      onConnection: (state) => connections.push(state),
      retryDelayMs: () => 0,
      maxRetries: 3,
    });

    expect(open).toHaveBeenCalledTimes(4);
    expect(
      connections.filter((state) => state.phase === "retrying").map((state) => state.attempt),
    ).toEqual([1, 2, 3]);
    expect(outcome).toMatchObject({
      kind: "lost",
      attempts: 3,
      status: null,
      reason: "Die API ist nicht erreichbar.",
    });
  });

  it("wiederholt eine Ablehnung des Servers (4xx) nicht", async () => {
    const open = vi.fn(() => Promise.reject(new ApiError("Nicht gefunden (HTTP 404).", 404)));
    const outcome = await readAssistantStream({
      open,
      onEvent: () => undefined,
      retryDelayMs: () => 0,
    });
    expect(open).toHaveBeenCalledTimes(1);
    expect(outcome).toMatchObject({ kind: "lost", status: 404 });
  });

  it("behandelt Stille laenger als die Ping-Frist wie einen Abriss", async () => {
    const open = vi.fn((_after: number | null, signal: AbortSignal) =>
      Promise.resolve(hangingResponse(signal)),
    );
    const outcome = await readAssistantStream({
      open,
      onEvent: () => undefined,
      idleTimeoutMs: 20,
      maxRetries: 1,
      retryDelayMs: () => 0,
    });
    expect(open).toHaveBeenCalledTimes(2);
    expect(outcome).toMatchObject({ kind: "lost" });
    expect(outcome.kind === "lost" && outcome.reason).toMatch(/kam nichts mehr vom Server/);
  });

  it("beendet sich ueber den AbortController sofort und ohne neuen Versuch", async () => {
    const controller = new AbortController();
    const events: AssistantStreamEvent[] = [];
    const open = vi.fn((_after: number | null, signal: AbortSignal) =>
      Promise.resolve(
        hangingResponse(signal, [
          line({ type: "status", seq: 1, status: "queued", queue_position: 2 }),
        ]),
      ),
    );
    const reading = readAssistantStream({
      open,
      onEvent: (event) => {
        events.push(event);
        controller.abort();
      },
      signal: controller.signal,
      retryDelayMs: () => 0,
    });
    const outcome = await reading;
    expect(outcome).toEqual({ kind: "aborted", lastSeq: 1 });
    expect(open).toHaveBeenCalledTimes(1);
    expect(events).toEqual([{ type: "status", seq: 1, status: "queued", queue_position: 2 }]);
  });

  it("zaehlt kaputte Zeilen, statt sie still zu verwerfen", async () => {
    const invalid: string[] = [];
    const open = vi.fn(() =>
      Promise.resolve(
        response([
          encoder.encode(
            "kein json\n" +
              line({ ohne: "type" }) +
              line({ type: "done", seq: 1, message: message() }),
          ),
        ]),
      ),
    );
    const outcome = await readAssistantStream({
      open,
      onEvent: () => undefined,
      onInvalidLine: (text) => invalid.push(text),
    });
    expect(invalid).toEqual(["kein json", '{"ohne":"type"}']);
    expect(outcome.kind).toBe("done");
  });
});

describe("openAssistantRunStream", () => {
  it("fragt NDJSON mit Anmeldung, Haushalt und after an", async () => {
    const fetchMock = vi.fn((_url: string, _init: RequestInit) =>
      Promise.resolve({ ok: true, status: 200, body: null } as unknown as Response),
    );
    vi.stubGlobal("fetch", fetchMock);
    await openAssistantRunStream("tok", "hh-1", "run-1", 5, new AbortController().signal);

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toMatch(/\/v1\/assistant\/runs\/run-1\/stream\?after=5$/);
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/x-ndjson");
    expect(headers.get("Authorization")).toBe("Bearer tok");
    expect(headers.get("X-Household-Id")).toBe("hh-1");
  });
});
