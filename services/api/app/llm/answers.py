"""Antwort eines Modellaufrufs verwerten - EIN Weg fuer alle Aufrufstellen.

Der Gateway (gateway.py) liefert ein ChatResult mit benanntem Grund. Was
danach kommt, war in mehreren Aufrufstellen fast gleich nachgebaut
(ai_call_site, ai_answers, contract_ai_crosscheck._ModelRun,
project_cost._ask_model, Worker health_documents) und steht jetzt hier:

* ask(): ein Aufruf mit strukturierter Ausgabe (llm.chat_structured: lehnt
  der Server das Schema ab, genau einmal ohne) und seine Pruefung.
  - Eine bei finish_reason=length abgeschnittene Antwort ist keine Antwort
    (model_budget_exhausted) - auch wenn ihr Anfang wie JSON oder wie ein
    fertiger Satz aussieht. Kein automatisches "ohne Denken wiederholen" (E1).
  - Die Antwort steht in content. Nur wenn dort nichts Verwertbares steht,
    darf das JSON aus dem Denktext (reasoning_content) DERSELBEN, nicht
    abgeschnittenen Antwort kommen - ein abgebrochener Denktext ist ein
    Entwurf. Einen zweiten, anders eingestellten Aufruf ("Formatierer",
    "ohne Denken") gibt es nicht (E1, E2).
  - Mit `accept` prueft die Aufrufstelle das Objekt gegen ihren Vertrag;
    ohne `accept` bekommt sie den Text und prueft selbst.
* reject(): eine fachlich verworfene Antwort markiert die llm_calls-Zeile
  DESSELBEN Aufrufs (llm.update_call_outcome) - eine Zeile je Aufruf, nie
  zwei.
* ends_run(): beendet ein Grund einen Lauf aus mehreren Fragen?
* batches_by_fit(): Eintraege in Stapel, die samt Antwortreserve ins geladene
  Fenster passen - statt einer festen Hoechstzahl.
* log_without_call(): llm_calls-Zeile fuer einen Ausgang, der den Gateway nie
  erreicht hat (KI aus, kein Server, kein Modell, kein Material).
* json_schema_format(), json_object(), find_object(): Form und Lesen von
  JSON-Antworten.

Frei von FastAPI (Grenztest tests/test_llm_kern_grenze.py) - auch der Worker
benutzt es. Die HTTP-Form eines Fehlschlags (503 mit {code, message}) steht in
app/services/ai_answers.py. Hier steht keine Zahl, die ein Budget, eine
Textlaenge, eine Rundenzahl oder eine Frist festlegt.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import uuid4

from app.llm import gateway, providers, reasons
from app.llm.budget import BudgetInputError, number_de
from app.llm.calls_log import CallScope, CallStatus, status_for, update_call_outcome, write_call
from app.llm.discovery import Target
from app.llm.gateway import ChatHooks, ChatRequest, ChatResult
from app.llm.measurements import Measurement, server_target_of
from app.llm.settings import LLMSettings
from app.llm.usage import UsageTokens
from app.llm.window import WindowInfo

Message = dict[str, Any]

# Gruende, bei denen gar kein Modell gefunden wurde - in llm_calls 'no_model';
# alle anderen bewusst nicht gesendeten Aufrufe sind 'refused'.
_NO_MODEL_CODES: Final = frozenset(
    {reasons.MODEL_UNAVAILABLE, reasons.MODEL_MISSING, reasons.MODEL_NOT_CHOSEN}
)

RESCUED_NOTE: Final = (
    "Das JSON-Objekt stand nur im Denktext (reasoning_content) derselben Antwort und "
    "wurde von dort übernommen - kein weiterer Modellaufruf."
)

_BREACH: Final = "Die Antwort enthält kein JSON-Objekt nach dem geforderten Vertrag."


@dataclass(frozen=True)
class Ready:
    """Alles fuer einen Modellaufruf: Einstellungen, Ziel (Server, Modell,
    Fenster) und Protokoll."""

    settings: LLMSettings
    target: Target
    scope: CallScope


@dataclass
class Answer[T]:
    """Ergebnis von ask(): verwertbarer Text bzw. angenommenes Objekt - oder
    der benannte Grund.

    text   Inhalt der Antwort bzw. das aus dem Denktext uebernommene JSON
    value  nur mit `accept`: das angenommene Objekt
    rescued  True, wenn die Antwort aus dem Denktext derselben Antwort kam"""

    result: ChatResult
    text: str = ""
    value: T | None = None
    reason_code: str | None = None
    message: str | None = None
    rescued: bool = False

    @property
    def ok(self) -> bool:
        return self.reason_code is None


def json_schema_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """response_format fuer strukturierte Ausgabe in der Form, die
    OpenAI-kompatible Server (LM Studio) verlangen - {name, strict, schema}.
    Ohne diese Huelle greift das strenge Schema nie. `schema` ist das reine
    JSON-Schema des Antwortobjekts."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }


def json_object(text: str) -> dict[str, Any]:
    """Das JSON-Objekt eines Modelltexts: erste oeffnende bis letzte
    schliessende Klammer. Ohne response_format setzen Modelle gern Fliesstext
    oder einen Codezaun davor oder dahinter. Wirft ValueError mit einem Satz
    fuer die Meldung."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Kein JSON-Objekt in der Modellantwort gefunden.")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Die Modellantwort ist kein gültiges JSON ({exc.msg}).") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Die Modellantwort ist kein JSON-Objekt.")
    return cast(dict[str, Any], parsed)


def _objects_from_end(text: str) -> list[Any]:
    """Alle vollstaendigen JSON-Werte, die an einer '{' beginnen - das zuletzt
    geschriebene zuerst. Ein Denktext enthaelt Zwischenstaende; es gilt das
    letzte Objekt, das zum Vertrag passt."""
    decoder = json.JSONDecoder()
    found: list[Any] = []
    index = text.find("{")
    while index >= 0:
        try:
            value, _end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            pass
        else:
            found.append(value)
        index = text.find("{", index + 1)
    found.reverse()
    return found


def find_object[T](text: str, accept: Callable[[dict[str, Any]], T | None]) -> T | None:
    """Das Antwortobjekt aus einem Modelltext: zuerst die Spanne von der
    ersten '{' bis zur letzten '}' (der Normalfall, auch mit einem Satz
    davor), sonst das zuletzt geschriebene vollstaendige Objekt, das `accept`
    annimmt."""
    try:
        whole: dict[str, Any] | None = json_object(text)
    except ValueError:
        whole = None
    if whole is not None:
        value = accept(whole)
        if value is not None:
            return value
    for candidate in _objects_from_end(text):
        if isinstance(candidate, dict):
            value = accept(candidate)
            if value is not None:
                return value
    return None


def ends_run(code: str | None) -> bool:
    """Beendet dieser Grund einen Lauf aus mehreren Fragen?

    Nur eine einzelne unbrauchbare oder vom Anbieter verweigerte Antwort
    betrifft genau diesen Aufruf. Alles
    andere beschreibt den Zustand von Server, Modell oder Einstellung (KI aus,
    Fenster unbekannt, Eingabe zu gross, Zeitbudget, HTTP-Fehler,
    Antwortbudget vom Denken aufgebraucht) und traefe den naechsten Aufruf
    genauso - weiterzufragen kostete nur Zeit und verdeckte den Grund."""
    return code is not None and code not in (reasons.MODEL_OUTPUT_UNUSABLE, reasons.MODEL_REFUSED)


async def log_without_call(
    scope: CallScope,
    reason_code: str,
    message: str,
    *,
    model: str | None = None,
    window: WindowInfo | None = None,
    base_url: str | None = None,
) -> None:
    """llm_calls-Zeile fuer einen Ausgang, der den Gateway nie erreicht hat
    (KI aus, kein Server, kein Modell, kein Fenster, kein Material). Ohne
    Modell 'no_model', sonst wie der Gateway ('refused' ...)."""
    status: CallStatus = (
        "no_model" if reason_code in _NO_MODEL_CODES else status_for(reason_code, None)
    )
    await write_call(
        scope,
        call_id=uuid4(),
        model=model or "none",
        status=status,
        duration_ms=0,
        reason_code=reason_code,
        error_message=message,
        budget=None,
        window=window,
        usage=UsageTokens(),
        finish_reason=None,
        http_status=None,
        server_target=server_target_of(base_url) if base_url else None,
        reasoning_sent=None,
        max_tokens_sent=None,
    )


def batches_by_fit[T](
    items: Sequence[T],
    messages_for: Callable[[Sequence[T]], list[Message]],
    *,
    ready: Ready,
    measurement: Measurement,
) -> list[list[T]]:
    """Eintraege der Reihe nach in Stapel, die samt Antwortreserve ins
    geladene Fenster passen (dieselbe Rechnung wie im Gateway).

    Ein Eintrag, der schon allein nicht passt, bildet einen eigenen Stapel:
    der Gateway lehnt ihn dann mit model_input_too_large und Aufschluesselung
    ab, statt dass hier still gekuerzt wird. Fehlt eine Groesse der Rechnung,
    bleibt es bei einem Stapel - der Gateway nennt den Grund
    (model_settings_invalid)."""
    batches: list[list[T]] = []
    current: list[T] = []
    for item in items:
        candidate = [*current, item]
        if current:
            try:
                fits = gateway.plan_budget(
                    messages_for(candidate),
                    window=ready.target.window,
                    settings=ready.settings,
                    measurement=measurement,
                ).fits
            except BudgetInputError:
                return [list(items)]
            if not fits:
                batches.append(current)
                candidate = [item]
        current = candidate
    if current:
        batches.append(current)
    return batches


async def reject(scope: CallScope, result: ChatResult, code: str, detail: str) -> Answer[Any]:
    """Fachlich verworfene Antwort: die Protokollzeile desselben Aufrufs
    markieren und den Grund als Answer zurueckgeben."""
    message = reasons.describe(code, detail)
    await update_call_outcome(
        scope.session,
        scope.household_id,
        result.call_id,
        status="error",
        reason_code=code,
        error_message=message,
    )
    return Answer(result=result, reason_code=code, message=message)


def _truncated_detail(result: ChatResult) -> str:
    detail = "Die Antwort endet mit finish_reason=length und wird nicht verwendet"
    budget = result.budget
    if budget is not None:
        detail += (
            f" (Antwortbudget {number_de(budget.max_tokens)} Token bei Fenster "
            f"{number_de(budget.W)}, Eingabe {number_de(budget.E)})"
        )
    return detail + "."


async def ask[T](
    ready: Ready,
    request: ChatRequest,
    *,
    accept: Callable[[dict[str, Any]], T | None] | None = None,
    breach: str | None = None,
    hooks: ChatHooks | None = None,
    measurement: Measurement | None = None,
) -> Answer[T]:
    """Ein Aufruf ueber den Gateway (mit dem bewussten Schema-Rueckfall von
    chat_structured) und seine Pruefung - siehe Moduldoc.

    accept   Vertrag der Aufrufstelle: Objekt -> Wert oder None. Ohne accept
             bekommt der Aufrufer den Text und prueft selbst (reject()).
    breach   Satz fuer die Meldung, wenn keine Antwort den Vertrag erfuellt.

    Jeder Grund des Gateways (Fenster, Platz, Zeit, HTTP, ...) kommt
    unveraendert zurueck; eine verworfene Antwort traegt ihren Grund auch in
    llm_calls."""
    scope = ready.scope
    # Ueber das Modul, nicht ueber den Namen: Tests ersetzen den Transport an
    # dieser einen Stelle (gateway.chat_structured).
    result = await gateway.chat_structured(
        request,
        target=ready.target,
        settings=ready.settings,
        scope=scope,
        hooks=hooks,
        measurement=measurement,
    )
    if result.ok and result.truncated:
        # Ein Anfang, der wie ein fertiges JSON oder ein ganzer Satz aussieht,
        # ist trotzdem abgeschnitten.
        return await reject(
            scope, result, reasons.MODEL_BUDGET_EXHAUSTED, _truncated_detail(result)
        )
    if result.ok:
        if accept is None:
            return Answer(result=result, text=result.content)
        value = find_object(result.content, accept)
        if value is not None:
            return Answer(result=result, text=result.content, value=value)

    # Denktext derselben Antwort: nur wenn sie gelesen wurde (HTTP 200), nicht
    # abgeschnitten ist und in content nichts Verwertbares stand. Ein
    # abgeschnittener Aufruf ohne Antworttext kommt vom Gateway als
    # model_budget_exhausted und wird hier nicht gerettet.
    readable = result.ok or result.reason_code == reasons.MODEL_OUTPUT_UNUSABLE
    # Claude liefert den Denktext nur als Zusammenfassung - ein JSON darin ist
    # ein Entwurf, keine Antwort. Dort wird nicht gerettet.
    summarized = ready.settings.provider == providers.ANTHROPIC
    if readable and not summarized and not result.truncated and result.reasoning_text.strip():
        rescued = _rescue(result.reasoning_text, accept)
        if rescued is not None:
            obj, value = rescued
            await update_call_outcome(
                scope.session,
                scope.household_id,
                result.call_id,
                status="success",
                reason_code=None,
                error_message=RESCUED_NOTE,
            )
            return Answer(
                result=result,
                text=json.dumps(obj, ensure_ascii=False),
                value=value,
                rescued=True,
            )

    if result.ok:
        # HTTP 200 mit Text, aber kein Objekt nach Vertrag: die Zeile des
        # Gateways sagt "success" - fachlich war es keins.
        return await reject(scope, result, reasons.MODEL_OUTPUT_UNUSABLE, breach or _BREACH)
    code = result.reason_code or reasons.MODEL_OUTPUT_UNUSABLE
    return Answer(
        result=result, reason_code=code, message=result.message or reasons.describe(code)
    )


def _rescue[T](
    reasoning: str, accept: Callable[[dict[str, Any]], T | None] | None
) -> tuple[dict[str, Any], T | None] | None:
    """(Objekt, angenommener Wert) aus dem Denktext - oder None.

    Ohne Vertrag gilt nur die ganze Spanne als Antwort: ein einzelnes
    Teilobjekt aus einem Zwischenstand waere geraten. Mit Vertrag gilt das
    zuletzt geschriebene Objekt, das ihn erfuellt."""
    if accept is None:
        try:
            return json_object(reasoning), None
        except ValueError:
            return None
    found: list[dict[str, Any]] = []

    def remember(candidate: dict[str, Any]) -> T | None:
        value = accept(candidate)
        if value is not None:
            found.append(candidate)
        return value

    value = find_object(reasoning, remember)
    if value is None:
        return None
    return found[-1], value
