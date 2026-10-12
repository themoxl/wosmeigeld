import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RunCard } from "../components/assistant/RunCard";
import { applyStreamEvent, createLiveRun } from "../lib/assistantLive";
import type { RunLog } from "../lib/assistantTypes";

const api = vi.hoisted(() => ({ getAssistantRun: vi.fn() }));

vi.mock("../lib/assistantApi", () => ({
  getAssistantRun: api.getAssistantRun,
}));
vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ session: { access_token: "tok" } }),
}));
vi.mock("../household/HouseholdProvider", () => ({
  useHousehold: () => ({ selectedHouseholdId: "hh-1" }),
}));

function runLog(): RunLog {
  return {
    id: "run-1",
    chat_id: "chat-1",
    message_id: "m-2",
    mode: "answer",
    status: "done",
    end_reason: "answer",
    model_configured: "google/gemma-4-26b",
    model_answering: "google/gemma-4-26b-a4b-qat",
    window: { loaded: 32_768, cap: null, effective: 32_768, source: "lmstudio_v1" },
    reasoning_sent: "off",
    started_at: "2026-10-03T10:00:00Z",
    finished_at: "2026-10-03T10:00:12.400Z",
    totals: {
      calls: 2,
      tool_calls: 1,
      prompt_tokens: 25_000,
      completion_tokens: 800,
      reasoning_tokens: 0,
      duration_ms: 12_400,
    },
    error_code: null,
    error_message: null,
    calls: [
      {
        nr: 1,
        step: "Antwort",
        retry_of: null,
        retry_reason: null,
        started_at: "2026-10-03T10:00:00Z",
        duration_ms: 4_200,
        ttft_ms: 900,
        finish_reason: "tool_calls",
        model: "google/gemma-4-26b-a4b-qat",
        budget: {
          W: 32_768,
          W_free: 32_768,
          E: 12_000,
          E_estimated: 12_400,
          exact: true,
          A: 0,
          R: 8_192,
          max_tokens: 12_576,
          parts: { system: 1_500, history: 8_000, tool_results: 0, tool_defs: 2_000, current: 500 },
        },
        tools_offered: ["query_analytics", "list_accounts"],
        usage: { prompt: 12_050, completion: 120, reasoning: null },
        tokens_per_s: 31.4,
        answer_text: null,
        tool_call_names: ["query_analytics"],
        error: null,
      },
      {
        nr: 2,
        step: "Antwort",
        retry_of: null,
        retry_reason: null,
        started_at: "2026-10-03T10:00:05Z",
        duration_ms: 7_000,
        ttft_ms: 700,
        finish_reason: "stop",
        model: "google/gemma-4-26b-a4b-qat",
        budget: null,
        tools_offered: [],
        usage: { prompt: 12_950, completion: 680, reasoning: null },
        tokens_per_s: 97.1,
        answer_text: "Im September 1.234,56 €.",
        tool_call_names: [],
        error: null,
      },
    ],
    tools: [
      {
        key: "W1",
        call_nr: 1,
        tool_name: "query_analytics",
        args: { month: "2026-09", group_by: "category" },
        status: "ok",
        result_text: '[{"category":"Miete","amount_eur":"1.234,56"}]',
        row_count: 12,
        duration_ms: 140,
      },
    ],
    events: [
      {
        seq: 6,
        kind: "compacted",
        level: "info",
        message: "Alte Werkzeugergebnisse verdichtet (W1).",
        details: null,
        call_nr: 2,
      },
      {
        seq: 7,
        kind: "reserve_tight",
        level: "warnung",
        message: "Die Antwortreserve war knapp.",
        details: null,
        call_nr: 2,
      },
    ],
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  api.getAssistantRun.mockReset();
});

describe("Laufkarte", () => {
  it("ist zugeklappt, nennt die Kurzfassung und laedt das Protokoll erst beim Aufklappen", async () => {
    api.getAssistantRun.mockResolvedValue(runLog());
    render(<RunCard runId="run-1" summary={{ queries: 1, warnings: 1, unverified_numbers: 2 }} />, {
      wrapper,
    });

    const toggle = screen.getByRole("button", {
      name: "KI-Lauf · 1 Abfrage · 1 Warnung · 2 Zahlen ohne Beleg",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(api.getAssistantRun).not.toHaveBeenCalled();

    fireEvent.click(toggle);
    expect(await screen.findByText("Antwort")).toBeInTheDocument();
    expect(api.getAssistantRun).toHaveBeenCalledWith("tok", "hh-1", "run-1");

    const card = screen.getByTestId("assistant-run-card");
    // Kopf und Messwerte
    expect(card).toHaveTextContent("google/gemma-4-26b-a4b-qat · fertig · Antwort gegeben");
    expect(card).toHaveTextContent("12,4 s");
    expect(card).toHaveTextContent("2 Modellaufrufe");
    expect(card).toHaveTextContent("Fenster 32.768 Token");

    // Warnungen stehen ueber der Zeitleiste.
    const warnings = screen.getByTestId("assistant-run-warnings");
    const timeline = screen.getByTestId("assistant-run-timeline");
    expect(warnings).toHaveTextContent("Die Antwortreserve war knapp.");
    expect(
      warnings.compareDocumentPosition(timeline) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // Budgetzeile je Aufruf, mit gemessener Eingabe
    expect(
      within(timeline).getByText(
        /Fenster 32\.768 · Eingabe 12\.000 exakt · Abstand 0 · Reserve 8\.192 · Antwortbudget 12\.576/,
      ),
    ).toHaveTextContent("gemessen 12.050");

    // Zeitleiste: Aufruf 1, sein Werkzeug, dann die Ereignisse vor Aufruf 2, dann Aufruf 2.
    const rows = within(timeline)
      .getAllByRole("listitem")
      .map((row) => row.textContent ?? "");
    expect(rows[0]).toContain("ruft query_analytics");
    expect(rows[1]).toContain("W1");
    expect(rows[1]).toContain("12 Zeilen");
    expect(rows[2]).toContain("Alte Werkzeugergebnisse verdichtet (W1).");
    expect(rows[4]).toContain("antwortet");
    // Das Werkzeugergebnis steht genau so da, wie das Modell es bekam.
    expect(
      within(timeline).getByText('[{"category":"Miete","amount_eur":"1.234,56"}]'),
    ).toBeInTheDocument();

    // Zu- und wieder aufklappen laedt nicht neu.
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    await screen.findByTestId("assistant-run-timeline");
    expect(api.getAssistantRun).toHaveBeenCalledTimes(1);
  });

  it("nennt Fehler des Laufs ganz oben und zeigt ein nicht lesbares Protokoll als Meldung", async () => {
    api.getAssistantRun.mockResolvedValueOnce({
      ...runLog(),
      status: "failed",
      end_reason: "failed",
      error_code: "model_budget_exhausted",
      error_message: "Das Denken hat das Antwortbudget verbraucht.",
    });
    const { unmount } = render(<RunCard runId="run-1" summary={null} />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "KI-Lauf" }));
    const warnings = await screen.findByTestId("assistant-run-warnings");
    expect(warnings.firstElementChild).toHaveTextContent(
      "Das Denken hat das Antwortbudget verbraucht. (Kennung: model_budget_exhausted)",
    );
    unmount();

    api.getAssistantRun.mockRejectedValueOnce(new Error("Nicht gefunden (HTTP 404)."));
    render(<RunCard runId="run-x" summary={null} />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "KI-Lauf" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Protokoll nicht lesbar: Nicht gefunden (HTTP 404).",
    );
  });

  it("zeigt waehrend des Laufs den Live-Stand mit Denktext, ohne das Protokoll zu laden", async () => {
    let live = createLiveRun("run-1");
    for (const event of [
      { type: "status", seq: 1, status: "running" },
      {
        type: "call_start",
        seq: 2,
        nr: 1,
        step: "Antwort",
        budget: {
          W: 32_768,
          W_free: 32_768,
          E: 9_000,
          E_estimated: 9_000,
          exact: false,
          A: 1_350,
          R: 8_192,
          max_tokens: 14_226,
          parts: { system: 1_500, history: 5_000, tool_results: 0, tool_defs: 2_000, current: 500 },
        },
      },
      { type: "think", chars: 24, text: "Erst die Miete abfragen." },
      { type: "tool", seq: 3, key: "W1", name: "query_analytics", args: { month: "2026-09" } },
    ] as const) {
      live = applyStreamEvent(live, event);
    }

    render(<RunCard runId="run-1" summary={null} live={live} />, { wrapper });
    const toggle = screen.getByRole("button", { name: /KI-Lauf läuft · Aufruf 1/ });
    fireEvent.click(toggle);

    expect(screen.getByText("● ruft query_analytics …")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-run-think")).toHaveTextContent(
      "Erst die Miete abfragen.Denktext: nur live sichtbar, wird nicht gespeichert",
    );
    expect(
      screen.getByText(/Fenster 32\.768 · Eingabe 9\.000 geschätzt · Abstand 1\.350/),
    ).toBeInTheDocument();
    await waitFor(() => expect(api.getAssistantRun).not.toHaveBeenCalled());
  });
});
