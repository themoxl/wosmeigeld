"""API-Vertrag des KI-Felds (docs/KI-FELD-SCHNITTSTELLE.md).

Feldnamen und Formen wortgleich mit dem Vertrag und
apps/web/src/lib/assistantTypes.ts. Die Stromzeilen (NDJSON) stehen nicht
hier, sondern in app/assistant/store.py - sie sind kein OpenAPI-Schema.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MessageStatus = Literal["queued", "running", "done", "failed", "cancelled"]
ReasoningOverride = Literal["on", "off"] | None
ReasoningCapabilityKind = Literal["switchable", "always", "none", "unknown"]
RunEndReason = Literal["answer", "stall", "runtime", "cancelled", "failed"]
RunToolStatus = Literal["ok", "rejected", "error", "cancelled"]
RunEventLevel = Literal["info", "warnung", "fehler"]


class ContextMeter(BaseModel):
    run_id: str
    call_nr: int
    input_tokens: int
    window_tokens: int
    reserve_tokens: int | None
    exact: bool


class NumberCheckItem(BaseModel):
    start: int
    end: int
    text: str
    status: Literal["belegt", "unbelegt"]
    sources: list[str]


class NumberCheck(BaseModel):
    items: list[NumberCheckItem]
    unverified: int


class RunSummary(BaseModel):
    queries: int
    warnings: int
    unverified_numbers: int


class ChatMessage(BaseModel):
    id: str
    seq: int
    role: Literal["user", "assistant"]
    content: str
    status: MessageStatus
    run_id: str | None
    created_at: datetime
    error_code: str | None
    error_message: str | None
    numbers: NumberCheck | None
    context_meter: ContextMeter | None
    run_summary: RunSummary | None


class ChatSummary(BaseModel):
    nr: int
    covers_until_message_id: str
    text: str
    run_id: str
    tokens_before: int | None
    tokens_after: int | None
    unverified_numbers: int
    created_at: datetime


class ChatListItem(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None
    message_count: int


class ChatDetail(BaseModel):
    id: str
    title: str
    reasoning_override: ReasoningOverride
    reasoning_capability: ReasoningCapabilityKind
    summary: ChatSummary | None
    active_compaction_run_id: str | None
    messages: list[ChatMessage]
    context_meter: ContextMeter | None


class SendResponse(BaseModel):
    user_message: ChatMessage
    assistant_message: ChatMessage
    run_id: str
    queue_position: int


class BudgetParts(BaseModel):
    system: int
    history: int
    tool_results: int
    tool_defs: int
    current: int


class RunBudget(BaseModel):
    W: int
    W_free: int
    E: int
    E_estimated: int
    exact: bool
    A: int
    R: int
    max_tokens: int
    parts: BudgetParts


class RunCallUsage(BaseModel):
    prompt: int | None
    completion: int | None
    reasoning: int | None


class RunCall(BaseModel):
    nr: int
    step: str
    retry_of: int | None
    retry_reason: str | None
    started_at: datetime
    duration_ms: int | None
    ttft_ms: int | None
    finish_reason: str | None
    model: str | None
    budget: RunBudget | None
    tools_offered: list[str]
    usage: RunCallUsage
    tokens_per_s: float | None
    answer_text: str | None
    tool_call_names: list[str]
    error: str | None


class RunToolResult(BaseModel):
    key: str
    call_nr: int
    tool_name: str
    args: dict[str, Any]
    status: RunToolStatus
    result_text: str
    row_count: int | None
    duration_ms: int | None


class RunEvent(BaseModel):
    seq: int
    kind: str
    level: RunEventLevel
    message: str
    details: dict[str, Any] | None
    call_nr: int | None


class RunWindow(BaseModel):
    loaded: int | None
    cap: int | None
    effective: int | None
    source: str | None


class RunTotals(BaseModel):
    calls: int
    tool_calls: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    duration_ms: int


class RunLog(BaseModel):
    id: str
    chat_id: str
    message_id: str
    mode: Literal["answer", "compact"]
    status: MessageStatus
    end_reason: RunEndReason | None
    model_configured: str | None
    model_answering: str | None
    window: RunWindow
    reasoning_sent: str | None
    started_at: datetime | None
    finished_at: datetime | None
    totals: RunTotals
    error_code: str | None
    error_message: str | None
    calls: list[RunCall]
    tools: list[RunToolResult]
    events: list[RunEvent]


class ChatCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class ChatUpdate(BaseModel):
    """PATCH: nur gesendete Felder aendern (reasoning_override null =
    Haushaltseinstellung - deshalb zaehlt, ob das Feld mitkam)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_override: ReasoningOverride = None


class SendMessage(BaseModel):
    # Obergrenze nur als Schutz der API vor Riesenkoerpern; ob die Frage ins
    # Fenster passt, entscheidet die Budgetrechnung (model_input_too_large).
    content: str = Field(min_length=1, max_length=100_000)
    request_id: str = Field(min_length=1, max_length=120)
    reasoning_override: ReasoningOverride = None


class CompactRequest(BaseModel):
    focus: str | None = Field(default=None, max_length=4000)


class CompactResponse(BaseModel):
    run_id: str


class RetryRequest(BaseModel):
    reasoning_override: ReasoningOverride = None


class CancelResponse(BaseModel):
    status: MessageStatus
