import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  cancelAiTask,
  completeAiTask,
  createAiTask,
  deleteAiTask,
  getAiRuntime,
  getAiTask,
  listAiTasks,
  retryAiTask,
} from "../lib/api";
import type { AITask, AITaskCreateInput, AITaskDetail, AITaskType } from "../lib/aiTaskTypes";
import type { AIRuntimeStatus } from "../lib/types";
import { reimbursementDocumentsQueryKey } from "../lib/reimbursementsApi";

export const aiTasksQueryKey = (householdId: string) => ["ai-tasks", householdId] as const;
export const aiTaskQueryKey = (householdId: string, taskId: string) =>
  ["ai-task", householdId, taskId] as const;
export const aiRuntimeQueryKey = (householdId: string) => ["ai-runtime", householdId] as const;

const POLL_INTERVAL_MS = 2_000;
// Der Laufzeitstreifen misst GPU und Modellzustand. Er darf mitlaufen, aber
// nicht die Maschine beschaeftigen, die gerade klassifiziert.
const RUNTIME_POLL_INTERVAL_MS = 5_000;

export function isAiTaskWorking(task: AITask | null | undefined): boolean {
  return task?.status === "queued" || task?.status === "running";
}

export function newAiTaskRequestId(): string {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `ai-task-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function mergeTask(tasks: AITask[] | undefined, task: AITask): AITask[] {
  const next = [task, ...(tasks ?? []).filter((item) => item.id !== task.id)];
  return next.sort((left, right) => {
    const rank = (value: AITask) => {
      if (value.status === "running") return 0;
      if (value.status === "queued") return 1;
      if (value.status === "awaiting_confirmation") return 2;
      return 3;
    };
    const rankDifference = rank(left) - rank(right);
    if (rankDifference !== 0) return rankDifference;
    if (left.status === "queued" && right.status === "queued") {
      return left.queue_order - right.queue_order;
    }
    return right.created_at.localeCompare(left.created_at);
  });
}

function useTaskContext() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  return {
    token: session?.access_token ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(session?.access_token && selectedHouseholdId),
  };
}

export function useAiTasks() {
  const { token, householdId, enabled } = useTaskContext();
  return useQuery<AITask[]>({
    queryKey: aiTasksQueryKey(householdId),
    queryFn: () => listAiTasks(token, householdId, { limit: 50 }),
    enabled,
    refetchInterval: (query) =>
      query.state.data?.some(isAiTaskWorking) ? POLL_INTERVAL_MS : false,
  });
}

export function useAiTask(taskId: string | null | undefined) {
  const { token, householdId, enabled } = useTaskContext();
  return useQuery<AITaskDetail>({
    queryKey: aiTaskQueryKey(householdId, taskId ?? ""),
    queryFn: () => getAiTask(token, householdId, taskId as string),
    enabled: enabled && Boolean(taskId),
    refetchInterval: (query) => (isAiTaskWorking(query.state.data) ? POLL_INTERVAL_MS : false),
  });
}

/**
 * Zustand der lokalen KI, solange das Aktivitätsfenster offen ist: geladenes
 * Modell, Kontextfenster und GPU. Geschlossen fragt niemand nach.
 */
export function useAiRuntime(enabled: boolean) {
  const { token, householdId, enabled: hasContext } = useTaskContext();
  return useQuery<AIRuntimeStatus>({
    queryKey: aiRuntimeQueryKey(householdId),
    queryFn: () => getAiRuntime(token, householdId),
    enabled: enabled && hasContext,
    refetchInterval: enabled ? RUNTIME_POLL_INTERVAL_MS : false,
    staleTime: RUNTIME_POLL_INTERVAL_MS,
  });
}

function useTaskMutation(
  action: (token: string, householdId: string, taskId: string) => Promise<AITask>,
) {
  const { token, householdId } = useTaskContext();
  const queryClient = useQueryClient();
  return useMutation<AITask, Error, string>({
    mutationFn: (taskId) => action(token, householdId, taskId),
    onSuccess: (task) => {
      queryClient.setQueryData<AITask[]>(aiTasksQueryKey(householdId), (current) =>
        mergeTask(current, task),
      );
      queryClient.setQueryData(aiTaskQueryKey(householdId, task.id), (current: unknown) => ({
        ...(typeof current === "object" && current ? current : {}),
        ...task,
      }));
    },
  });
}

export function useEnqueueAiTask() {
  const { token, householdId } = useTaskContext();
  const queryClient = useQueryClient();
  return useMutation<AITask, Error, AITaskCreateInput>({
    mutationFn: (input) =>
      createAiTask(token, householdId, {
        ...input,
        request_id: input.request_id ?? newAiTaskRequestId(),
      }),
    onSuccess: (task) => {
      queryClient.setQueryData<AITask[]>(aiTasksQueryKey(householdId), (current) =>
        mergeTask(current, task),
      );
      queryClient.setQueryData<AITaskDetail>(aiTaskQueryKey(householdId, task.id), {
        ...task,
        events: [],
      });
      if (task.task_type === "transaction_classification") {
        void queryClient.invalidateQueries({
          queryKey: ["classification-run-latest", householdId],
        });
      }
    },
  });
}

export function useCancelAiTask() {
  return useTaskMutation(cancelAiTask);
}

/**
 * Notbremse für eine Aufgabe, die nach der Abbruchbitte weiterläuft: beendet
 * die Aufgabe sofort, statt auf das Ende des laufenden Modellaufrufs zu
 * warten. Ein bereits geschriebenes Teilergebnis bleibt erhalten.
 */
export function useForceCancelAiTask() {
  return useTaskMutation((token, householdId, taskId) =>
    cancelAiTask(token, householdId, taskId, { force: true }),
  );
}

export function useCompleteAiTask() {
  return useTaskMutation(completeAiTask);
}

export function useRetryAiTask() {
  return useTaskMutation(retryAiTask);
}

/** Entfernt einen abgeschlossenen Verlaufseintrag dauerhaft aus der Liste. */
export function useDeleteAiTask() {
  const { token, householdId } = useTaskContext();
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (taskId) => deleteAiTask(token, householdId, taskId),
    onSuccess: (_result, taskId) => {
      queryClient.setQueryData<AITask[]>(aiTasksQueryKey(householdId), (current) =>
        current?.filter((task) => task.id !== taskId),
      );
      queryClient.removeQueries({ queryKey: aiTaskQueryKey(householdId, taskId) });
    },
  });
}

export function latestAiTaskOfType(
  tasks: AITask[] | undefined,
  taskType: AITaskType,
): AITask | null {
  return (
    tasks
      ?.filter((task) => task.task_type === taskType)
      .sort((left, right) => right.created_at.localeCompare(left.created_at))[0] ?? null
  );
}

/**
 * Einmal global in Layout montiert: wenn ein Task waehrend eines
 * Seitenwechsels endet, werden die fachlichen Ansichten trotzdem aktualisiert.
 */
export function useAiTaskCompletionEffects(tasks: AITask[] | undefined) {
  const { householdId } = useTaskContext();
  const queryClient = useQueryClient();
  const observed = useRef(new Map<string, AITask["status"]>());

  useEffect(() => {
    if (!tasks) return;
    for (const task of tasks) {
      const previous = observed.current.get(task.id);
      observed.current.set(task.id, task.status);
      if (!previous || previous === task.status || isAiTaskWorking(task)) continue;

      if (task.task_type === "transaction_classification") {
        void queryClient.invalidateQueries({ queryKey: ["ai-decisions", householdId] });
        void queryClient.invalidateQueries({ queryKey: ["review-inbox", householdId] });
        void queryClient.invalidateQueries({ queryKey: ["transactions", householdId] });
        void queryClient.invalidateQueries({ queryKey: ["overview-report", householdId] });
      } else if (task.task_type === "category_suggestions") {
        void queryClient.invalidateQueries({ queryKey: ["categories", householdId] });
        void queryClient.invalidateQueries({ queryKey: ["category-catalog", householdId] });
      } else if (task.task_type === "project_cost_estimate") {
        const scenarioId = String(task.input.scenario_id ?? "");
        const projectId = String(task.input.project_id ?? "");
        if (scenarioId) {
          void queryClient.invalidateQueries({
            queryKey: ["cost-sections", householdId, scenarioId],
          });
          void queryClient.invalidateQueries({
            queryKey: ["cost-items", householdId, scenarioId],
          });
          void queryClient.invalidateQueries({
            queryKey: ["scenario-calculation", householdId, projectId, scenarioId],
          });
        }
      } else if (task.task_type === "health_document_extraction") {
        void queryClient.invalidateQueries({
          queryKey: reimbursementDocumentsQueryKey(householdId),
        });
        void queryClient.invalidateQueries({
          queryKey: ["reimbursement-cases", householdId],
        });
        void queryClient.invalidateQueries({
          queryKey: ["reimbursement-summary", householdId],
        });
      } else {
        void queryClient.invalidateQueries({ queryKey: ["contracts", householdId] });
        void queryClient.invalidateQueries({ queryKey: ["contract-summary", householdId] });
        void queryClient.invalidateQueries({
          queryKey: ["contract-free-transactions", householdId],
        });
      }
    }
  }, [tasks, householdId, queryClient]);
}
