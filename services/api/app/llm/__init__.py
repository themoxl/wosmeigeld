"""KI-Kern: der einzige Weg von Finanzplaner-Code zu einem Sprachmodell.

Gemeinsam fuer API (services/api) und Worker (services/worker, ueber
PYTHONPATH). Importiert NIE app.api, app.services oder fastapi - geprueft von
tests/test_llm_kern_grenze.py.

Leitlinien (docs/KI-BUDGET-UND-KI-FELD-PLAN.md):
* Kein festes Budget: jedes Antwortbudget kommt aus dem GELADENEN Fenster und
  dem Reserveanteil der Haushaltseinstellung (budget.py, window.py).
* Keine KI-Parameter im Code, auch nicht als Rueckfall: Vorgaben stehen nur
  im Datenbankschema (settings.py); fehlt etwas, scheitert der Aufruf mit
  benanntem Grund (reasons.py) statt mit einer erfundenen Zahl.
* Kein automatisches "ohne Denken wiederholen" (E1).

So ruft eine Aufrufstelle ein Modell auf::

    from app import llm

    settings = await llm.load_settings(session, household_id)    # oder ai_settings_service.resolve
    found = await llm.resolve_target(settings)                   # Server, Modell, Fenster
    if not found.ready:
        ...  # found.reason_code / found.message zeigen (ai_disabled - dann ohne
             # Serverkontakt -, model_unavailable, model_missing, model_context_unknown)
    result = await llm.chat(
        llm.ChatRequest(messages=[...], response_format={...}),
        target=found.target,
        settings=settings,
        scope=llm.CallScope(session=session, household_id=household_id,
                            stage="meine_stufe", prompt_version="meine-v1"),
    )
    if not result.ok:
        ...  # result.reason_code + result.message (Klartext mit Einstellungshinweis)
    text = result.content                      # result.reasoning_text nur im Speicher

Strukturierte Ausgabe mit dem bewussten Rueckfall (Server lehnt
response_format ab -> einmal ohne): llm.chat_structured(...) statt llm.chat;
result.schema_fallback sagt, welcher Weg galt.
Die Antwort pruefen (abgeschnitten -> model_budget_exhausted, JSON aus content
oder dem Denktext derselben Antwort, Vertrag der Aufrufstelle, Protokollzeile
markieren): llm.answers.ask(llm.answers.Ready(settings, target, scope), request,
accept=...). Lehnt der Aufrufer die Antwort danach fachlich ab, markiert er die
Protokollzeile mit llm.answers.reject(...).
Passt die Eingabe vorab an das Fenster an (z. B. weniger Material senden):
llm.plan_budget(messages, window=..., settings=..., measurement=...).fits.
Ergebnisseiten im KI-Feld: llm.page_chars(W, settings.result_page_share,
measurement.chars_per_token).
Live mitlesen (KI-Feld): llm.ChatHooks(on_stream=rueckruf) streamt per SSE;
rueckruf bekommt llm.StreamEvent (think/delta/tool/targs), das Ergebnis hat
dieselbe Form wie ohne Strom.
"""

from app.llm import answers, reasons
from app.llm.budget import (
    Basis,
    Budget,
    BudgetInputError,
    BudgetParts,
    MeasurementSums,
    chars_of,
    compute,
    page_chars,
    update_measurement,
)
from app.llm.calls_log import CallScope, update_call_outcome
from app.llm.discovery import (
    Target,
    TargetResult,
    discover,
    locate_target,
    resolve_target,
    select_model,
)
from app.llm.gateway import (
    CAPABILITY_REJECT_STATUS,
    CancelToken,
    ChatEvent,
    ChatHooks,
    ChatRequest,
    ChatResult,
    CompactHook,
    ToolCall,
    build_body,
    chat,
    chat_structured,
    measurement_for,
    plan_budget,
)
from app.llm.measurements import (
    Measurement,
    load_measurement,
    record_measurement,
    server_target_of,
    start_measurement,
)
from app.llm.settings import LLMSettings, SettingsRowMissingError, load_settings, schema_defaults
from app.llm.stream import StreamAccumulator, StreamCallback, StreamEvent
from app.llm.usage import UsageTokens, extract_usage_tokens
from app.llm.window import ReasoningCapability, WindowInfo, effective_window, read_window

__all__ = [
    "CAPABILITY_REJECT_STATUS",
    "Basis",
    "Budget",
    "BudgetInputError",
    "BudgetParts",
    "CallScope",
    "CancelToken",
    "ChatEvent",
    "ChatHooks",
    "ChatRequest",
    "ChatResult",
    "CompactHook",
    "LLMSettings",
    "Measurement",
    "MeasurementSums",
    "ReasoningCapability",
    "SettingsRowMissingError",
    "StreamAccumulator",
    "StreamCallback",
    "StreamEvent",
    "Target",
    "TargetResult",
    "ToolCall",
    "UsageTokens",
    "WindowInfo",
    "answers",
    "build_body",
    "chars_of",
    "chat",
    "chat_structured",
    "compute",
    "discover",
    "effective_window",
    "extract_usage_tokens",
    "load_measurement",
    "load_settings",
    "locate_target",
    "measurement_for",
    "page_chars",
    "plan_budget",
    "read_window",
    "reasons",
    "record_measurement",
    "resolve_target",
    "schema_defaults",
    "select_model",
    "server_target_of",
    "start_measurement",
    "update_call_outcome",
    "update_measurement",
]
