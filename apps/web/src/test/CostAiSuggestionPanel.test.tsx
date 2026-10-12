import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CostAiSuggestionPanel } from "../components/projects/CostAiSuggestionPanel";
import type { AITask } from "../lib/aiTaskTypes";

// KI-Kostenschaetzung: ein Fehlschlag traegt seit dem KI-Kern den Grund des
// Kerns in `error_code` (model_timeout statt cost_ai_timeout) und den
// Klartext in `error_message`. Beides muss samt Aktion ankommen.

const state = vi.hoisted(() => ({ task: null as AITask | null }));

vi.mock("../hooks/useAiTaskQueue", () => ({
  isAiTaskWorking: (task: AITask | null | undefined) =>
    task?.status === "queued" || task?.status === "running",
  useAiTask: () => ({ data: state.task, isLoading: false, isError: false }),
  useEnqueueAiTask: () => ({ mutate: vi.fn(), isPending: false }),
  useCompleteAiTask: () => ({ mutate: vi.fn() }),
}));
vi.mock("../hooks/useProjects", () => ({
  useApplyCostAiSuggestions: () => ({ mutate: vi.fn(), isPending: false }),
}));

function failedTask(overrides: Partial<AITask> = {}): AITask {
  return {
    id: "task-cost",
    task_type: "project_cost_estimate",
    status: "failed",
    queue_order: 1,
    request_id: null,
    input: {},
    result: null,
    result_summary: {},
    route_path: "/vorhaben/project-1",
    current_stage: null,
    progress_current: 0,
    progress_total: null,
    status_message: "Die KI-Kostenschaetzung ist technisch fehlgeschlagen.",
    model_name: "test-model",
    error_message: null,
    cancel_requested: false,
    cancel_requested_at: null,
    attempt_count: 1,
    category_operation_id: null,
    classification_run_id: null,
    created_at: "2026-10-03T10:00:00Z",
    started_at: "2026-10-03T10:00:01Z",
    completed_at: "2026-10-03T10:01:00Z",
    heartbeat_at: null,
    updated_at: "2026-10-03T10:01:00Z",
    is_stale: false,
    ...overrides,
  };
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <CostAiSuggestionPanel
        projectId="project-1"
        scenarioId="scenario-1"
        initialTaskId="task-cost"
        onClose={vi.fn()}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  state.task = null;
});

describe("CostAiSuggestionPanel", () => {
  it("zeigt den Klartext eines Fehlschlags samt Aktion aus error_code", () => {
    state.task = failedTask({
      error_message:
        "Der Modellaufruf hat das Zeitbudget überschritten. Abhilfe: Einstellungen › KI › Zeitbudget je Anfrage erhöhen oder ein schnelleres Modell wählen.",
      result: {
        operation_id: "op-1",
        generation_status: "timeout",
        error_code: "model_timeout",
        error_message: "Der Modellaufruf hat das Zeitbudget überschritten.",
        suggestions: [],
      },
    });
    renderPanel();

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Der Modellaufruf hat das Zeitbudget überschritten.");
    expect(alert).toHaveTextContent("Details und alle Modellschritte findest du im KI-Status.");
    expect(alert).not.toHaveTextContent("model_timeout");
    expect(
      screen.getByRole("link", { name: "Zeitbudget unter Einstellungen › KI anpassen" }),
    ).toHaveAttribute("href", "/einstellungen?tab=ki");
  });

  it("liest den Grund aus der Kennung, wenn kein Ergebnis gespeichert ist", () => {
    state.task = failedTask({
      error_message: "Die KI ist für diesen Haushalt ausgeschaltet. (Kennung: ai_disabled)",
    });
    renderPanel();

    expect(screen.getByRole("alert")).toHaveTextContent("Die KI ist für diesen Haushalt");
    expect(
      screen.getByRole("link", { name: "KI unter Einstellungen › KI einschalten" }),
    ).toBeInTheDocument();
  });
});
