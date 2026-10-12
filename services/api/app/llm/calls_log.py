"""Protokollzeile je Modellaufruf (llm_calls) mit Budgetzeile.

Der Gateway schreibt fuer JEDEN Aufruf genau eine Zeile - auch fuer einen,
den er bewusst nicht sendet (status 'refused': Fenster unbekannt, Eingabe
passt nicht, KI aus). So laesst sich jede abgeschnittene oder abgelehnte
Antwort spaeter nachvollziehen: mit welchem Fenster, welcher Eingabe und
welchem max_tokens gerechnet wurde.

Der Aufrufer gibt Ort und Zweck ueber CallScope vor. Stellt er nach dem
Aufruf fest, dass die Antwort fachlich unbrauchbar war (z. B. kein gueltiges
JSON), markiert er dieselbe Zeile mit update_call_outcome() - eine Zeile je
Aufruf, nie zwei.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import reasons

if TYPE_CHECKING:
    from app.llm.budget import Budget
    from app.llm.usage import UsageTokens
    from app.llm.window import WindowInfo

CallStatus = Literal["success", "error", "timeout", "no_model", "refused", "cancelled"]

# Gruende, bei denen der Kern bewusst NICHT gesendet hat.
REFUSAL_CODES: Final = frozenset(
    {
        reasons.AI_DISABLED,
        reasons.MODEL_CONTEXT_UNKNOWN,
        reasons.MODEL_INPUT_TOO_LARGE,
        reasons.MODEL_SETTINGS_INVALID,
        reasons.MODEL_KEY_MISSING,
    }
)


@dataclass(frozen=True)
class CallScope:
    """Wer ruft auf - und wohin wird protokolliert und gemessen.

    session      Sitzung mit Schreibrecht auf llm_calls und
                 ai_model_measurements (API: Editor-Sitzung; Worker: ohne RLS)
    household_id Haushalt
    stage        llm_calls.stage, z. B. "classify", "mapping_suggest"
    prompt_version  Vertrags-/Promptversion fuer Auswertungen
    ai_task_id   optional: Aufgabe der KI-Warteschlange
    log          False: keine llm_calls-Zeile (nur fuer Verbindungstests)
    measure      False: Messung nur lesen, nicht fortschreiben (Kanarienvogel)
    """

    session: AsyncSession
    household_id: UUID
    stage: str
    prompt_version: str
    ai_task_id: UUID | None = None
    log: bool = True
    measure: bool = True


def status_for(reason_code: str | None, http_status: int | None) -> CallStatus:
    """llm_calls.status aus dem Grund ableiten."""
    if reason_code is None:
        return "success"
    if reason_code in REFUSAL_CODES:
        return "refused"
    if reason_code == reasons.MODEL_CANCELLED:
        return "cancelled"
    if reason_code == reasons.MODEL_TIMEOUT:
        return "timeout"
    # Auch ein Verbindungsfehler mitten im Aufruf ist 'error'; 'no_model'
    # bleibt Zeilen vorbehalten, die gar kein Modell gefunden haben (die
    # schreibt der Aufrufer selbst, bevor er den Gateway ruft).
    del http_status
    return "error"


_INSERT_SQL: Final = text(
    "insert into llm_calls "
    "(id, household_id, stage, model_name, status, duration_ms, tokens_estimated, "
    "prompt_version, error_message, prompt_tokens, completion_tokens, reasoning_tokens, "
    "ai_task_id, prompt_chars, input_tokens_estimated, estimate_exact, estimated_part_tokens, "
    "window_tokens, window_loaded_tokens, window_cap_tokens, window_source, reserve_tokens, "
    "error_allowance_tokens, max_tokens_sent, chars_per_token, estimate_error, budget_parts, "
    "finish_reason, reason_code, http_status, server_target, reasoning_sent) "
    "values (:id, :household_id, :stage, :model, :status, :dur, :tok, :pv, :err, "
    ":ptok, :ctok, :rtok, :ai_task_id, :prompt_chars, :input_tokens, :exact, :estimated_part, "
    ":window_tokens, :window_loaded, :window_cap, :window_source, :reserve, :allowance, "
    ":max_tokens, :ratio, :error_share, cast(:parts as jsonb), :finish, :reason, :http, "
    ":target, :reasoning_sent)"
)

_UPDATE_SQL: Final = text(
    "update llm_calls set status = :status, reason_code = :reason, "
    "error_message = coalesce(:err, error_message) "
    "where id = :id and household_id = :household_id"
)


async def write_call(
    scope: CallScope,
    *,
    call_id: UUID,
    model: str,
    status: CallStatus,
    duration_ms: int,
    reason_code: str | None,
    error_message: str | None,
    budget: "Budget | None",
    window: "WindowInfo | None",
    usage: "UsageTokens",
    finish_reason: str | None,
    http_status: int | None,
    server_target: str | None,
    reasoning_sent: str | None,
    max_tokens_sent: int | None,
) -> None:
    """Eine Zeile in llm_calls. Ohne scope.log wird nichts geschrieben."""
    if not scope.log:
        return
    await scope.session.execute(
        _INSERT_SQL,
        {
            "id": str(call_id),
            "household_id": str(scope.household_id),
            "stage": scope.stage,
            "model": model or "none",
            "status": status,
            "dur": max(0, duration_ms),
            # tokens_estimated ist NOT NULL (Bestand); neue Zeilen tragen die
            # Budgetschaetzung E, ohne Budget 0.
            "tok": budget.E if budget is not None else 0,
            "pv": scope.prompt_version,
            "err": error_message,
            "ptok": usage.prompt_tokens,
            "ctok": usage.completion_tokens,
            "rtok": usage.reasoning_tokens,
            "ai_task_id": str(scope.ai_task_id) if scope.ai_task_id else None,
            "prompt_chars": budget.chars if budget is not None else None,
            "input_tokens": budget.E if budget is not None else None,
            "exact": budget.exact if budget is not None else None,
            "estimated_part": budget.estimated_part if budget is not None else None,
            "window_tokens": window.effective if window is not None else None,
            "window_loaded": window.loaded if window is not None else None,
            "window_cap": window.cap if window is not None else None,
            "window_source": window.source if window is not None else None,
            "reserve": budget.R if budget is not None else None,
            "allowance": budget.A if budget is not None else None,
            "max_tokens": max_tokens_sent,
            "ratio": round(budget.chars_per_token, 3) if budget is not None else None,
            "error_share": round(budget.estimate_error, 4) if budget is not None else None,
            "parts": json.dumps(budget.parts.as_dict()) if budget is not None else None,
            "finish": finish_reason,
            "reason": reason_code,
            "http": http_status,
            "target": server_target,
            "reasoning_sent": reasoning_sent,
        },
    )


async def update_call_outcome(
    session: AsyncSession,
    household_id: UUID,
    call_id: UUID | None,
    *,
    status: CallStatus,
    reason_code: str | None,
    error_message: str | None = None,
) -> None:
    """Fachliches Ergebnis nachtragen, wenn der Aufrufer die Antwort
    verwirft (z. B. Schemabruch). Ohne call_id (nicht protokolliert) nichts."""
    if call_id is None:
        return
    await session.execute(
        _UPDATE_SQL,
        {
            "status": status,
            "reason": reason_code,
            "err": error_message,
            "id": str(call_id),
            "household_id": str(household_id),
        },
    )
