"""API-Vertrag fuer die persistente, haushaltsweite KI-Warteschlange."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

AITaskType = Literal[
    "category_suggestions",
    "transaction_classification",
    "contract_duplicate_crosscheck",
    "contract_transaction_check",
    "project_cost_estimate",
    "health_document_extraction",
    # KI-Feld: entsteht nur ueber /v1/assistant (Chat, Lauf, Nachricht) -
    # POST /v1/ai/tasks lehnt den Typ ab.
    "assistant_chat",
]
AITaskStatus = Literal[
    "queued",
    "running",
    "awaiting_confirmation",
    "completed",
    "failed",
    "cancelled",
]


class AITaskCreate(BaseModel):
    task_type: AITaskType
    request_id: str | None = Field(default=None, max_length=120)
    input: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_input(self) -> "AITaskCreate":
        allowed: set[str]
        if self.task_type == "assistant_chat":
            raise ValueError(
                "KI-Fragen entstehen ueber /v1/assistant/chats/{id}/messages, nicht hier"
            )
        if self.task_type == "contract_duplicate_crosscheck":
            allowed = {"contract_ids"}
            values = self.input.get("contract_ids")
            if values is not None:
                if not isinstance(values, list) or len(values) > 24:
                    raise ValueError("contract_ids muss eine Liste mit hoechstens 24 IDs sein")
                self.input["contract_ids"] = [str(UUID(str(value))) for value in values]
        elif self.task_type == "contract_transaction_check":
            allowed = {"contract_id", "transaction_ids"}
            contract_id = self.input.get("contract_id")
            if contract_id is None:
                raise ValueError("contract_id ist fuer diese Aufgabe erforderlich")
            self.input["contract_id"] = str(UUID(str(contract_id)))
            values = self.input.get("transaction_ids")
            if values is not None:
                if not isinstance(values, list) or len(values) > 50:
                    raise ValueError("transaction_ids muss eine Liste mit hoechstens 50 IDs sein")
                self.input["transaction_ids"] = [str(UUID(str(value))) for value in values]
        elif self.task_type == "project_cost_estimate":
            allowed = {
                "project_id",
                "scenario_id",
                "description",
                "use_web_search",
            }
            for field in ("project_id", "scenario_id"):
                value = self.input.get(field)
                if value is None:
                    raise ValueError(f"{field} ist fuer diese Aufgabe erforderlich")
                self.input[field] = str(UUID(str(value)))
            description = self.input.get("description")
            if not isinstance(description, str) or not description.strip():
                raise ValueError("description ist fuer diese Aufgabe erforderlich")
            if len(description.strip()) > 4000:
                raise ValueError("description darf hoechstens 4000 Zeichen enthalten")
            self.input["description"] = description.strip()
            use_web_search = self.input.get("use_web_search", False)
            if not isinstance(use_web_search, bool):
                raise ValueError("use_web_search muss true oder false sein")
            self.input["use_web_search"] = use_web_search
        elif self.task_type == "health_document_extraction":
            allowed = {"document_id"}
            document_id = self.input.get("document_id")
            if document_id is None:
                raise ValueError("document_id ist fuer diese Aufgabe erforderlich")
            self.input["document_id"] = str(UUID(str(document_id)))
        else:
            allowed = set()

        unexpected = set(self.input) - allowed
        if unexpected:
            raise ValueError(f"Unbekannte Eingabefelder: {', '.join(sorted(unexpected))}")
        return self


class AITaskEvent(BaseModel):
    id: int
    level: Literal["info", "warning", "error"]
    stage: str | None
    message: str
    details: dict[str, Any]
    created_at: datetime


class AITask(BaseModel):
    id: UUID
    task_type: AITaskType
    status: AITaskStatus
    queue_order: int
    request_id: str | None
    input: dict[str, Any]
    result: dict[str, Any] | None
    result_summary: dict[str, Any]
    route_path: str
    current_stage: str | None
    progress_current: int
    progress_total: int | None
    status_message: str | None
    model_name: str | None
    error_message: str | None
    cancel_requested: bool
    cancel_requested_at: datetime | None = None
    attempt_count: int
    category_operation_id: UUID | None
    classification_run_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None
    updated_at: datetime
    is_stale: bool = False


class AITaskDetail(AITask):
    events: list[AITaskEvent] = Field(default_factory=list)
