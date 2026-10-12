export type AITaskType =
  | "category_suggestions"
  | "transaction_classification"
  | "contract_duplicate_crosscheck"
  | "contract_transaction_check"
  | "project_cost_estimate"
  | "health_document_extraction"
  | "assistant_chat";

export type AITaskStatus =
  "queued" | "running" | "awaiting_confirmation" | "completed" | "failed" | "cancelled";

export interface AITaskCreateInput {
  task_type: AITaskType;
  request_id?: string;
  input?: Record<string, unknown>;
}

export interface AITaskEvent {
  id: number;
  level: "info" | "warning" | "error";
  stage: string | null;
  message: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface AITask {
  id: string;
  task_type: AITaskType;
  status: AITaskStatus;
  queue_order: number;
  request_id: string | null;
  input: Record<string, unknown>;
  result: Record<string, unknown> | null;
  result_summary: Record<string, unknown>;
  route_path: string;
  current_stage: string | null;
  progress_current: number;
  progress_total: number | null;
  status_message: string | null;
  model_name: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  cancel_requested_at: string | null;
  attempt_count: number;
  category_operation_id: string | null;
  classification_run_id: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  heartbeat_at: string | null;
  updated_at: string;
  is_stale: boolean;
}

export interface AITaskDetail extends AITask {
  events: AITaskEvent[];
}
