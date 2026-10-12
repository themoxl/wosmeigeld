import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AiTaskQueueChip } from "../components/AiTaskQueueChip";
import { AiTaskQueuePanel } from "../components/AiTaskQueuePanel";
import type { AITask, AITaskDetail } from "../lib/aiTaskTypes";

const queueState = vi.hoisted(() => ({
  tasks: [] as AITask[],
  forceCancel: vi.fn(),
  deleteTask: vi.fn(),
  runtime: {
    is_connected: true,
    base_url: "http://127.0.0.1:1234/v1",
    configured_model: "google/gemma-4-26b-a4b-qat",
    active_model: {
      key: "google/gemma-4-26b-a4b-qat",
      display_name: "Gemma 4 26B A4B QAT",
      architecture: "gemma4",
      quantization: "Q4_0",
      parameters: "26B-A4B",
      size_bytes: 15634263816,
      max_context_tokens: 262144,
      loaded_context_tokens: 8192,
    },
    // Wie /v1/ai/runtime es liefert: dieselbe Rechnung wie im KI-Kern.
    window: {
      loaded: 8192,
      cap: null as number | null,
      effective: 8192,
      source: "/api/v1/models",
      reason: null as string | null,
      parallel: 4,
      max_context: 262144,
    },
    loaded_count: 1,
    chat_models_count: 4,
    reasoning: "off",
    reasoning_effort: "none",
    gpus: [
      {
        name: "NVIDIA GeForce RTX 5090",
        memory_used_mb: 17245,
        memory_total_mb: 32607,
        utilization_percent: 12,
        temperature_celsius: 35,
      },
    ],
    gpu_detail: null,
    message: "„google/gemma-4-26b-a4b-qat“ ist geladen und bereit.",
  },
}));

vi.mock("../hooks/useAiTaskQueue", () => ({
  useAiTasks: () => ({ data: queueState.tasks, isLoading: false, isError: false }),
  useAiRuntime: () => ({ data: queueState.runtime, isLoading: false, isError: false }),
  useAiTask: (taskId: string | null) => ({
    isLoading: false,
    data: taskId
      ? ({
          ...queueState.tasks.find((task) => task.id === taskId),
          events: [
            {
              id: 1,
              level: "info",
              stage: "queued",
              message: "Aufgabe wurde in die Warteschlange aufgenommen.",
              details: {},
              created_at: "2026-08-05T10:00:00Z",
            },
          ],
        } as AITaskDetail)
      : undefined,
  }),
  useCancelAiTask: () => ({ mutate: vi.fn(), isPending: false }),
  useForceCancelAiTask: () => ({ mutate: queueState.forceCancel, isPending: false }),
  useCompleteAiTask: () => ({ mutate: vi.fn(), isPending: false }),
  useRetryAiTask: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAiTask: () => ({ mutate: queueState.deleteTask, isPending: false }),
}));

function task(overrides: Partial<AITask>): AITask {
  return {
    id: "task-1",
    task_type: "category_suggestions",
    status: "queued",
    queue_order: 1,
    request_id: null,
    input: {},
    result: null,
    result_summary: {},
    route_path: "/kategorien?ai_task=task-1",
    current_stage: "queued",
    progress_current: 0,
    progress_total: null,
    status_message: "Wartet auf die Verarbeitung",
    model_name: null,
    error_message: null,
    cancel_requested: false,
    cancel_requested_at: null,
    attempt_count: 0,
    category_operation_id: null,
    classification_run_id: null,
    created_at: "2026-08-05T10:00:00Z",
    started_at: null,
    completed_at: null,
    heartbeat_at: null,
    updated_at: "2026-08-05T10:00:00Z",
    is_stale: false,
    ...overrides,
  };
}

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <MemoryRouter>
      <AiTaskQueueChip onOpen={() => setOpen(true)} />
      <AiTaskQueuePanel open={open} onClose={() => setOpen(false)} />
    </MemoryRouter>
  );
}

describe("KI-Warteschlange", () => {
  it("zeigt laufende, wartende und prüfbereite Aufgaben in Serverreihenfolge", () => {
    queueState.tasks = [
      task({ id: "running", status: "running", task_type: "category_suggestions" }),
      task({ id: "queued", queue_order: 2, task_type: "contract_duplicate_crosscheck" }),
      task({
        id: "review",
        status: "awaiting_confirmation",
        task_type: "transaction_classification",
      }),
    ];
    render(<Harness />);

    fireEvent.click(screen.getByTestId("ai-queue-chip"));
    const dialog = screen.getByRole("dialog", { name: "KI-Status & Verlauf" });
    const headings = within(dialog).getAllByRole("heading", { level: 3 });
    expect(headings.map((heading) => heading.textContent)).toEqual([
      "Kategorien vorschlagen",
      "Verträge gegenprüfen",
      "Offene Buchungen klassifizieren",
    ]);
    expect(within(dialog).getByRole("button", { name: "Ergebnis öffnen" })).toBeInTheDocument();
  });

  it("macht den gespeicherten Aktivitätsverlauf sichtbar", () => {
    queueState.tasks = [task({ id: "trace", status: "running" })];
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ai-queue-chip"));
    fireEvent.click(screen.getByRole("button", { name: "Aktivität anzeigen" }));

    expect(screen.getByText("Aufgabe wurde in die Warteschlange aufgenommen.")).toBeInTheDocument();
  });

  it("bietet nach der Abbruchbitte das sofortige Beenden an", () => {
    queueState.forceCancel.mockClear();
    queueState.tasks = [
      task({
        id: "cancelling",
        status: "running",
        task_type: "transaction_classification",
        cancel_requested: true,
        cancel_requested_at: new Date(Date.now() - 120_000).toISOString(),
        progress_current: 36,
        progress_total: 620,
      }),
    ];
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ai-queue-chip"));

    // Die alte Anzeige endete bei "Abbruch angefordert" und liess offen, wie
    // lange das noch dauert und was der Nutzer dagegen tun kann.
    expect(screen.getByText(/Abbruch angefordert vor 2 Minuten/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Abbrechen" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Sofort beenden" }));
    expect(queueState.forceCancel).toHaveBeenCalledWith("cancelling");
  });

  it("zeigt Modell, Kontextfenster und GPU der lokalen KI", () => {
    queueState.tasks = [task({ id: "running", status: "running" })];
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ai-queue-chip"));

    const runtime = screen.getByLabelText("Laufzeit der lokalen KI");
    expect(within(runtime).getByText(/Gemma 4 26B A4B QAT · 26B-A4B · Q4_0/)).toBeInTheDocument();
    expect(
      within(runtime).getByText(
        /8\.192 Token wirksam · Quelle: geladen in LM Studio · Modell kann 262\.144/,
      ),
    ).toBeInTheDocument();
    expect(within(runtime).getByText(/NVIDIA GeForce RTX 5090/)).toBeInTheDocument();
    expect(within(runtime).getByText(/16,8 \/ 31,8 GB/)).toBeInTheDocument();
  });

  it("nennt „Kontext höchstens“ als Quelle, wenn der Deckel greift", () => {
    queueState.tasks = [task({ id: "running", status: "running" })];
    const before = queueState.runtime.window;
    queueState.runtime.window = {
      ...before,
      cap: 4096,
      effective: 4096,
      source: "Kontext höchstens (Einstellung)",
    };
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ai-queue-chip"));

    const runtime = screen.getByLabelText("Laufzeit der lokalen KI");
    expect(
      within(runtime).getByText(
        /4\.096 Token wirksam · Quelle: Kontext höchstens · geladen 8\.192/,
      ),
    ).toBeInTheDocument();
    queueState.runtime.window = before;
  });

  it("zeigt einen fehlgeschlagenen letzten Lauf dauerhaft im Status-Chip", () => {
    queueState.tasks = [
      task({
        id: "failed",
        status: "failed",
        task_type: "project_cost_estimate",
        error_message: "Das Modell hat das Zeitlimit erreicht.",
      }),
    ];
    render(<Harness />);

    expect(screen.getByText("KI-Status · letzter Lauf fehlgeschlagen")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("ai-queue-chip"));
    expect(screen.getByText("Vorhabenkosten schätzen")).toBeInTheDocument();
    expect(screen.getByText("Das Modell hat das Zeitlimit erreicht.")).toBeInTheDocument();
  });

  it("löscht einen abgeschlossenen Verlaufseintrag nach Bestätigung", () => {
    queueState.deleteTask.mockClear();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    queueState.tasks = [
      task({ id: "failed", status: "failed", task_type: "project_cost_estimate" }),
    ];
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ai-queue-chip"));

    fireEvent.click(screen.getByRole("button", { name: "Löschen" }));
    expect(queueState.deleteTask).toHaveBeenCalledWith("failed");
    vi.restoreAllMocks();
  });
});
