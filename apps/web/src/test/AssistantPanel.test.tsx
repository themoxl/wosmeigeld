import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantPanel } from "../components/assistant/AssistantPanel";
import { ApiError } from "../lib/api";
import type {
  AssistantStreamEvent,
  ChatDetail,
  ChatMessage,
  ContextMeter,
  MessageStatus,
  SendMessageInput,
  SendResponse,
} from "../lib/assistantTypes";

// Gefaelschter Server des KI-Felds: Chats im Speicher, je Lauf ein Strom, in
// den der Test Zeilen schiebt (docs/KI-FELD-SCHNITTSTELLE.md).

const api = vi.hoisted(() => ({
  listAssistantChats: vi.fn(),
  createAssistantChat: vi.fn(),
  getAssistantChat: vi.fn(),
  updateAssistantChat: vi.fn(),
  deleteAssistantChat: vi.fn(),
  sendAssistantMessage: vi.fn(),
  compactAssistantChat: vi.fn(),
  getAssistantRun: vi.fn(),
  cancelAssistantRun: vi.fn(),
  retryAssistantRun: vi.fn(),
  openAssistantRunStream: vi.fn(),
}));

vi.mock("../lib/assistantApi", () => api);
vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ session: { access_token: "tok" } }),
}));
vi.mock("../household/HouseholdProvider", () => ({
  useHousehold: () => ({ selectedHouseholdId: "hh-1" }),
}));

const encoder = new TextEncoder();

class FakeStream {
  private controller: ReadableStreamDefaultController<Uint8Array> | null = null;
  private pending: string[] = [];
  opens: (number | null)[] = [];

  open(after: number | null, signal: AbortSignal): Response {
    this.opens.push(after);
    const body = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.controller = controller;
        for (const text of this.pending) controller.enqueue(encoder.encode(text));
        this.pending = [];
        signal.addEventListener("abort", () => {
          try {
            controller.error(new DOMException("Aborted", "AbortError"));
          } catch {
            // schon geschlossen
          }
        });
      },
    });
    return { ok: true, status: 200, body } as unknown as Response;
  }

  push(event: AssistantStreamEvent) {
    const text = `${JSON.stringify(event)}\n`;
    if (this.controller) this.controller.enqueue(encoder.encode(text));
    else this.pending.push(text);
  }
}

const server = {
  chats: new Map<string, ChatDetail>(),
  streams: new Map<string, FakeStream>(),
  seq: 0,
  runs: 0,
};

function stream(runId: string): FakeStream {
  let existing = server.streams.get(runId);
  if (!existing) {
    existing = new FakeStream();
    server.streams.set(runId, existing);
  }
  return existing;
}

function msg(overrides: Partial<ChatMessage> & Pick<ChatMessage, "id" | "role">): ChatMessage {
  server.seq += 1;
  return {
    seq: server.seq,
    content: "",
    status: "done",
    run_id: null,
    created_at: "2026-10-03T10:00:00Z",
    error_code: null,
    error_message: null,
    numbers: null,
    context_meter: null,
    run_summary: null,
    ...overrides,
  };
}

function chat(id: string, overrides: Partial<ChatDetail> = {}): ChatDetail {
  const detail: ChatDetail = {
    id,
    title: `Chat ${id}`,
    reasoning_override: null,
    reasoning_capability: "switchable",
    summary: null,
    active_compaction_run_id: null,
    messages: [],
    context_meter: null,
    ...overrides,
  };
  server.chats.set(id, detail);
  return detail;
}

/** Eine abgeschlossene Frage mit Antwort. */
function exchange(question: string, answer: string, runId: string): ChatMessage[] {
  return [
    msg({ id: `q-${runId}`, role: "user", content: question }),
    msg({ id: `a-${runId}`, role: "assistant", content: answer, run_id: runId }),
  ];
}

function updateMessage(chatId: string, messageId: string, patch: Partial<ChatMessage>) {
  const detail = server.chats.get(chatId)!;
  detail.messages = detail.messages.map((message) =>
    message.id === messageId ? { ...message, ...patch } : message,
  );
}

function meter(input: number): ContextMeter {
  return {
    run_id: "run-1",
    call_nr: 1,
    input_tokens: input,
    window_tokens: 32_768,
    reserve_tokens: 8_192,
    exact: true,
  };
}

function newRun(chatId: string, questionId: string | null, content: string): SendResponse {
  const detail = server.chats.get(chatId)!;
  const busy = detail.messages.some(
    (message) => message.status === "running" || message.status === "queued",
  );
  server.runs += 1;
  const runId = `run-${server.runs}`;
  const status: MessageStatus = busy ? "queued" : "running";
  const user =
    detail.messages.find((message) => message.id === questionId) ??
    msg({ id: `q-${runId}`, role: "user", content });
  const assistant = msg({ id: `a-${runId}`, role: "assistant", status, run_id: runId });
  detail.messages = [
    ...detail.messages.filter((message) => message.id !== user.id),
    user,
    assistant,
  ];
  return {
    user_message: user,
    assistant_message: assistant,
    run_id: runId,
    queue_position: busy ? 1 : 0,
  };
}

beforeEach(() => {
  server.chats.clear();
  server.streams.clear();
  server.seq = 0;
  server.runs = 0;
  for (const mock of Object.values(api)) mock.mockReset();

  api.listAssistantChats.mockImplementation(() =>
    Promise.resolve(
      [...server.chats.values()].map((detail) => ({
        id: detail.id,
        title: detail.title,
        created_at: "2026-10-03T10:00:00Z",
        updated_at: "2026-10-03T10:00:00Z",
        last_message_at: null,
        message_count: detail.messages.length,
      })),
    ),
  );
  api.getAssistantChat.mockImplementation((_token: string, _hh: string, chatId: string) => {
    const detail = server.chats.get(chatId);
    return detail
      ? Promise.resolve(structuredClone(detail))
      : Promise.reject(new ApiError("Nicht gefunden (HTTP 404).", 404));
  });
  api.createAssistantChat.mockImplementation(() =>
    Promise.resolve(structuredClone(chat("chat-new", { title: "" }))),
  );
  api.updateAssistantChat.mockImplementation(
    (_token: string, _hh: string, chatId: string, input: Partial<ChatDetail>) => {
      const detail = { ...server.chats.get(chatId)!, ...input };
      server.chats.set(chatId, detail);
      return Promise.resolve(structuredClone(detail));
    },
  );
  api.sendAssistantMessage.mockImplementation(
    (_token: string, _hh: string, chatId: string, input: SendMessageInput) =>
      Promise.resolve(newRun(chatId, null, input.content)),
  );
  api.retryAssistantRun.mockImplementation((_token: string, _hh: string, runId: string) => {
    const [chatId, detail] = [...server.chats.entries()].find(([, value]) =>
      value.messages.some((message) => message.run_id === runId),
    )!;
    const index = detail.messages.findIndex((message) => message.run_id === runId);
    const question = detail.messages
      .slice(0, index)
      .reverse()
      .find((message) => message.role === "user")!;
    return Promise.resolve(newRun(chatId, question.id, question.content));
  });
  api.cancelAssistantRun.mockResolvedValue({ status: "cancelled" });
  api.compactAssistantChat.mockResolvedValue({ run_id: "run-c" });
  api.openAssistantRunStream.mockImplementation(
    (_token: string, _hh: string, runId: string, after: number | null, signal: AbortSignal) =>
      Promise.resolve(stream(runId).open(after, signal)),
  );
});

function Harness({ initialChatId }: { initialChatId: string | null }) {
  const [chatId, setChatId] = useState(initialChatId);
  return <AssistantPanel chatId={chatId} onSelectChat={setChatId} onClose={() => undefined} />;
}

function renderPanel(initialChatId: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Harness initialChatId={initialChatId} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const input = () => screen.getByRole("textbox", { name: "Frage an die KI" });

function typeAndEnter(text: string) {
  fireEvent.change(input(), { target: { value: text } });
  fireEvent.keyDown(input(), { key: "Enter" });
}

describe("KI-Feld", () => {
  it("legt mit der ersten Frage den Chat an, sendet und zeigt die Antwort live aus delta", async () => {
    renderPanel(null);
    expect(screen.getByRole("button", { name: "Senden" })).toBeDisabled();

    // Shift+Enter bricht nur um und sendet nicht.
    fireEvent.change(input(), { target: { value: "Wofür habe ich im September" } });
    fireEvent.keyDown(input(), { key: "Enter", shiftKey: true });
    expect(api.createAssistantChat).not.toHaveBeenCalled();

    typeAndEnter("Wofür habe ich im September am meisten ausgegeben?");

    await waitFor(() => expect(api.sendAssistantMessage).toHaveBeenCalledTimes(1));
    expect(api.createAssistantChat).toHaveBeenCalledWith("tok", "hh-1", {});
    expect(api.sendAssistantMessage).toHaveBeenCalledWith("tok", "hh-1", "chat-new", {
      content: "Wofür habe ich im September am meisten ausgegeben?",
      request_id: expect.any(String),
    });
    await waitFor(() => expect(input()).toHaveValue(""));
    expect(
      await screen.findByText("Wofür habe ich im September am meisten ausgegeben?"),
    ).toBeInTheDocument();

    // Der Strom des Laufs ist offen; waehrenddessen wird eingereiht.
    await waitFor(() => expect(api.openAssistantRunStream).toHaveBeenCalled());
    expect(api.openAssistantRunStream.mock.calls[0]!.slice(0, 4)).toEqual([
      "tok",
      "hh-1",
      "run-1",
      null,
    ]);
    expect(screen.getByRole("button", { name: "Einreihen" })).toBeInTheDocument();

    const live = stream("run-1");
    act(() => {
      live.push({ type: "ping" });
      live.push({ type: "status", seq: 1, status: "running" });
      live.push({ type: "call_start", seq: 2, nr: 1, step: "Antwort", budget: null });
      live.push({ type: "tool", seq: 3, key: "W1", name: "query_analytics", args: {} });
    });
    await waitFor(() =>
      expect(screen.getByTestId("assistant-live-status")).toHaveTextContent(
        "ruft query_analytics …",
      ),
    );
    act(() => {
      live.push({
        type: "tool_result",
        seq: 4,
        key: "W1",
        status: "ok",
        row_count: 12,
        preview: "",
      });
      live.push({ type: "meter", seq: 5, ...meter(25_000) });
      live.push({ type: "delta", text: "Am meisten ging im September " });
      live.push({ type: "delta", text: "an die Miete: 1.234,56 €." });
    });
    await waitFor(() =>
      expect(screen.getByTestId("assistant-live-text")).toHaveTextContent(
        "Am meisten ging im September an die Miete: 1.234,56 €.",
      ),
    );
    expect(screen.getByTestId("assistant-live-status")).toHaveTextContent("schreibt die Antwort …");
    // Der Kontextbalken folgt dem Lauf live - hier schon in der Reserve.
    expect(screen.getByRole("meter", { name: "Kontext" })).toHaveClass("voll");

    // Abschluss: gespeicherte Antwort mit Zahlenpruefung und Laufkarte.
    const content = "Am meisten ging im September an die Miete: 1.234,56 €.";
    const final: Partial<ChatMessage> = {
      status: "done",
      content,
      numbers: {
        items: [
          {
            start: content.indexOf("1.234,56 €"),
            end: content.indexOf("1.234,56 €") + 10,
            text: "1.234,56 €",
            status: "belegt",
            sources: ["W1"],
          },
        ],
        unverified: 0,
      },
      run_summary: { queries: 1, warnings: 0, unverified_numbers: 0 },
      context_meter: meter(12_000),
    };
    updateMessage("chat-new", "a-run-1", final);
    server.chats.get("chat-new")!.context_meter = meter(12_000);
    act(() => {
      live.push({
        type: "done",
        seq: 6,
        message: server.chats.get("chat-new")!.messages.find((m) => m.id === "a-run-1")!,
      });
    });

    const mark = await screen.findByText("1.234,56 €", { selector: "mark" });
    expect(mark).toHaveAttribute("data-status", "belegt");
    expect(
      screen.getByRole("button", {
        name: "KI-Lauf · 1 Abfrage · 0 Warnungen · 0 Zahlen ohne Beleg",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("assistant-live-text")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Senden" })).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Kontext" })).not.toHaveClass("voll");
  });

  it("reiht eine Frage ein, solange ein Auftrag laeuft, und bricht den laufenden ab", async () => {
    chat("chat-1", {
      messages: [
        msg({ id: "q-1", role: "user", content: "Was kostet mich das Auto?" }),
        msg({ id: "a-1", role: "assistant", status: "running", run_id: "run-1" }),
      ],
    });
    server.runs = 1;
    renderPanel("chat-1");

    const enqueue = await screen.findByRole("button", { name: "Einreihen" });
    fireEvent.change(input(), { target: { value: "Und im August?" } });
    fireEvent.click(enqueue);

    await waitFor(() => expect(api.sendAssistantMessage).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Und im August?")).toBeInTheDocument();
    expect(await screen.findByText(/wartet in der Warteschlange/)).toBeInTheDocument();
    // Nur der vorderste Lauf wird gestreamt.
    expect(api.openAssistantRunStream.mock.calls.map((call) => call[2])).toEqual(["run-1"]);

    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    await waitFor(() =>
      expect(api.cancelAssistantRun).toHaveBeenCalledWith("tok", "hh-1", "run-1"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Aus der Warteschlange nehmen" }));
    await waitFor(() =>
      expect(api.cancelAssistantRun).toHaveBeenLastCalledWith("tok", "hh-1", "run-2"),
    );
  });

  it("zeigt einen Fehler aus dem Strom als Klartext und wiederholt ohne Denken", async () => {
    chat("chat-1", {
      messages: [
        msg({ id: "q-1", role: "user", content: "Lohnt sich die Umschuldung?" }),
        msg({ id: "a-1", role: "assistant", status: "running", run_id: "run-1" }),
      ],
    });
    server.runs = 1;
    renderPanel("chat-1");
    await waitFor(() => expect(api.openAssistantRunStream).toHaveBeenCalled());

    const failed: Partial<ChatMessage> = {
      status: "failed",
      error_code: "model_budget_exhausted",
      error_message:
        "Das Denken hat das ganze Antwortbudget verbraucht. Ohne Denken wiederholen oder die Antwortreserve unter Einstellungen → KI erhöhen.",
    };
    updateMessage("chat-1", "a-1", failed);
    act(() => {
      stream("run-1").push({ type: "status", seq: 1, status: "running" });
      stream("run-1").push({
        type: "error",
        seq: 2,
        code: "model_budget_exhausted",
        message: failed.error_message!,
        message_obj: server.chats.get("chat-1")!.messages.find((m) => m.id === "a-1")!,
      });
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Das Antwortbudget war aufgebraucht");
    expect(alert).toHaveTextContent("Ohne Denken wiederholen oder die Antwortreserve");
    expect(alert).toHaveTextContent("Kennung: model_budget_exhausted");

    fireEvent.click(within(alert).getByRole("button", { name: "Ohne Denken wiederholen" }));
    await waitFor(() =>
      expect(api.retryAssistantRun).toHaveBeenCalledWith("tok", "hh-1", "run-1", {
        reasoning_override: "off",
      }),
    );
    // Die neue Antwort zur selben Frage laeuft; ihr Strom wird geoeffnet.
    await waitFor(() =>
      expect(api.openAssistantRunStream.mock.calls.map((call) => call[2])).toContain("run-2"),
    );
    expect(screen.getAllByText("Lohnt sich die Umschuldung?")).toHaveLength(1);
  });

  it("stellt das Denken je Chat um und erklaert gesperrte Modelle", async () => {
    chat("chat-1", { messages: exchange("Frage", "Antwort", "run-1") });
    chat("chat-2", { reasoning_capability: "none" });
    renderPanel("chat-1");
    await screen.findByText("Antwort");

    const group = await screen.findByRole("group", { name: "Denken" });
    expect(within(group).getByRole("button", { name: "Modellstandard" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(within(group).getByRole("button", { name: "Aus" }));
    await waitFor(() =>
      expect(api.updateAssistantChat).toHaveBeenCalledWith("tok", "hh-1", "chat-1", {
        reasoning_override: "off",
      }),
    );
    expect(within(group).getByRole("button", { name: "Aus" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("aus: schnellere Antworten")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Chat wählen" }), {
      target: { value: "chat-2" },
    });
    await waitFor(() =>
      expect(screen.getByText(/kann nicht denken \(kein Reasoning\)/)).toBeInTheDocument(),
    );
    const locked = screen.getByRole("group", { name: "Denken" });
    for (const button of within(locked).getAllByRole("button")) expect(button).toBeDisabled();
  });

  it("sperrt Verdichten mit Grund und startet es mit Fokus, sobald es geht", async () => {
    chat("chat-1", { messages: exchange("Erste Frage", "Erste Antwort", "run-1") });
    chat("chat-2", {
      messages: [
        ...exchange("Erste Frage", "Erste Antwort", "run-1"),
        ...exchange("Zweite Frage", "Zweite Antwort", "run-2"),
      ],
    });
    renderPanel("chat-1");
    await screen.findByText("Erste Antwort");

    fireEvent.click(screen.getByRole("button", { name: "Verdichten…" }));
    expect(screen.getByTestId("assistant-compact-blocked")).toHaveTextContent(
      "mindestens zwei Fragen",
    );
    expect(screen.getByRole("button", { name: "Jetzt verdichten" })).toBeDisabled();

    fireEvent.change(screen.getByRole("combobox", { name: "Chat wählen" }), {
      target: { value: "chat-2" },
    });
    await screen.findByText("Zweite Antwort");
    fireEvent.click(screen.getByRole("button", { name: "Verdichten…" }));
    expect(screen.queryByTestId("assistant-compact-blocked")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Worauf soll die Zusammenfassung achten? (optional)"), {
      target: { value: "Fixkosten" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Jetzt verdichten" }));

    await waitFor(() =>
      expect(api.compactAssistantChat).toHaveBeenCalledWith("tok", "hh-1", "chat-2", {
        focus: "Fixkosten",
      }),
    );
    // Die Verdichtung ist ein Lauf mit eigenem Strom; solange sie laeuft, ist
    // Verdichten gesperrt und die Eingabe reiht ein.
    await waitFor(() =>
      expect(api.openAssistantRunStream.mock.calls.map((call) => call[2])).toContain("run-c"),
    );
    expect(await screen.findByText("verdichtet den Verlauf …")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Einreihen" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "verdichtet …" }));
    expect(screen.getByTestId("assistant-compact-blocked")).toHaveTextContent(
      "Die Verdichtung läuft gerade.",
    );

    // Fertig: der Chat traegt die Zusammenfassung, der Strich steht im Verlauf.
    const detail = server.chats.get("chat-2")!;
    detail.summary = {
      nr: 1,
      covers_until_message_id: "a-run-1",
      text: "Abschnitt 1: Fixkosten …",
      run_id: "run-c",
      tokens_before: 5_000,
      tokens_after: 800,
      unverified_numbers: 0,
      created_at: "2026-10-03T10:05:00Z",
    };
    act(() => {
      stream("run-c").push({
        type: "done",
        seq: 3,
        message: msg({ id: "c-1", role: "assistant", run_id: "run-c" }),
      });
    });
    expect(await screen.findByTestId("assistant-summary-line")).toHaveTextContent(
      "Frage 1 zusammengefasst · 5.000 → 800 Token · Zusammenfassung ansehen",
    );
    expect(screen.getByRole("button", { name: "Senden" })).toBeInTheDocument();
  });

  it("sperrt Verdichten, solange ein Auftrag des Chats laeuft", async () => {
    chat("chat-1", {
      messages: [
        ...exchange("Erste Frage", "Erste Antwort", "run-1"),
        msg({ id: "q-2", role: "user", content: "Zweite Frage" }),
        msg({ id: "a-2", role: "assistant", status: "running", run_id: "run-2" }),
      ],
    });
    renderPanel("chat-1");
    await screen.findByText("Erste Antwort");
    fireEvent.click(screen.getByRole("button", { name: "Verdichten…" }));
    expect(screen.getByTestId("assistant-compact-blocked")).toHaveTextContent(
      "wenn die Aufträge dieses Chats fertig sind",
    );
  });

  it("zeigt eine laufende Verdichtung auch nach dem Neuladen und bietet ihren Abbruch an", async () => {
    chat("chat-1", {
      messages: [
        ...exchange("Erste Frage", "Erste Antwort", "run-1"),
        ...exchange("Zweite Frage", "Zweite Antwort", "run-2"),
      ],
      active_compaction_run_id: "run-c",
    });
    renderPanel("chat-1");
    await screen.findByText("Zweite Antwort");

    // Kein Klick auf "Verdichten…": der Lauf kommt allein aus ChatDetail.
    expect(await screen.findByText("verdichtet den Verlauf …")).toBeInTheDocument();
    await waitFor(() =>
      expect(api.openAssistantRunStream.mock.calls.map((call) => call[2])).toContain("run-c"),
    );
    expect(api.compactAssistantChat).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "verdichtet …" }));
    expect(screen.getByTestId("assistant-compact-blocked")).toHaveTextContent(
      "Die Verdichtung läuft gerade.",
    );

    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    await waitFor(() =>
      expect(api.cancelAssistantRun).toHaveBeenCalledWith("tok", "hh-1", "run-c"),
    );

    // Fertig: der Server meldet keine laufende Verdichtung mehr.
    server.chats.get("chat-1")!.active_compaction_run_id = null;
    act(() => {
      stream("run-c").push({
        type: "done",
        seq: 2,
        message: msg({ id: "c-1", role: "assistant", run_id: "run-c", status: "cancelled" }),
      });
    });
    await waitFor(() =>
      expect(screen.queryByText("verdichtet den Verlauf …")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Senden" })).toBeInTheDocument();
  });

  it("verweist bei fehlendem Modell auf LM Studio und die KI-Einstellungen", async () => {
    chat("chat-1", {
      messages: [
        msg({ id: "q-1", role: "user", content: "Was kostet mich das Auto?" }),
        msg({
          id: "a-1",
          role: "assistant",
          status: "failed",
          run_id: "run-1",
          error_code: "model_missing",
          error_message:
            "Das eingestellte Modell ist auf dem Modellserver nicht vorhanden. Abhilfe: Einstellungen › KI › Modell: ein vorhandenes Modell wählen.",
        }),
      ],
    });
    renderPanel("chat-1");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Das eingestellte Modell ist auf dem Modellserver");
    expect(alert).toHaveTextContent("Modell in LM Studio laden.");
    expect(
      within(alert).getByRole("link", { name: "Modell unter Einstellungen › KI wählen" }),
    ).toHaveAttribute("href", "/einstellungen?tab=ki");
    expect(
      within(alert).queryByRole("button", { name: "Ohne Denken wiederholen" }),
    ).not.toBeInTheDocument();
  });
});
