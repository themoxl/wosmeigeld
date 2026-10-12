import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { useAiTaskCompletionEffects } from "../hooks/useAiTaskQueue";
import type { AITask } from "../lib/aiTaskTypes";
import { reimbursementDocumentsQueryKey } from "../lib/reimbursementsApi";

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ session: { access_token: "test-token" } }),
}));

vi.mock("../household/HouseholdProvider", () => ({
  useHousehold: () => ({ selectedHouseholdId: "household-1" }),
}));

function healthTask(status: AITask["status"]): AITask {
  return {
    id: "health-task-1",
    task_type: "health_document_extraction",
    status,
    queue_order: 1,
    request_id: null,
    input: { document_id: "document-1" },
    result: null,
    result_summary: {},
    cancel_requested_at: null,
    route_path: "/erstattungen",
    current_stage: status,
    progress_current: status === "completed" ? 1 : 0,
    progress_total: 1,
    status_message: status,
    model_name: null,
    error_message: null,
    cancel_requested: false,
    attempt_count: 1,
    category_operation_id: null,
    classification_run_id: null,
    created_at: "2026-08-05T10:00:00Z",
    started_at: "2026-08-05T10:00:01Z",
    completed_at: status === "completed" ? "2026-08-05T10:00:05Z" : null,
    heartbeat_at: null,
    updated_at: "2026-08-05T10:00:05Z",
    is_stale: false,
  };
}

function CompletionHarness({ tasks }: { tasks: AITask[] }) {
  useAiTaskCompletionEffects(tasks);
  return null;
}

describe("health_document_extraction Abschluss", () => {
  it("invalidiert Dokumente mit demselben Query-Key wie die Erstattungsroute", async () => {
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <CompletionHarness tasks={[healthTask("running")]} />
      </QueryClientProvider>,
    );

    rerender(
      <QueryClientProvider client={queryClient}>
        <CompletionHarness tasks={[healthTask("completed")]} />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: reimbursementDocumentsQueryKey("household-1"),
      }),
    );
  });
});
