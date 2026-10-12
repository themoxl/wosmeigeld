"""Anthropic (Claude) ueber die Messages-API - Uebersetzer und Transport.

Der Kern spricht intern die OpenAI-Form (Nachrichten mit system/user/
assistant/tool, Werkzeuge als {"type": "function", ...}, response_format,
reasoning_effort) - alle Aufrufstellen bauen sie so, und LM Studio versteht
sie direkt. Fuer Anthropic uebersetzt dieses Modul hin und zurueck, damit
Gateway, Budget, Messung, Protokoll und Gruende fuer beide Anbieter dieselben
bleiben:

* Hin: Systemnachrichten -> `system` (mit Cache-Marke), assistant.tool_calls
  -> tool_use-Bloecke, role "tool" -> tool_result-Bloecke in einer
  user-Nachricht, response_format (json_schema) -> output_config.format,
  Werkzeuge -> input_schema, tool_choice -> auto/any/none/tool, die
  Reasoning-Einstellung -> thinking/output_config.effort.
* Zurueck: die Antwort in genau der Form der blockierenden OpenAI-Antwort
  ({"choices": [{"message": ..., "finish_reason": ...}], "usage": ...}) -
  der Gateway wertet sie mit derselben Funktion aus wie die von LM Studio.

Bewusst die native Messages-API und nicht Anthropics OpenAI-kompatibler
Zugang (den SIA und der Audit Companion benutzen): der ist laut Anthropic zum
Ausprobieren gedacht, kennt kein Prompt-Caching und keine erzwungene
JSON-Ausgabe - beides zaehlt hier (Kategorisierung im Stapel, KI-Feld).

Denkbloecke werden NICHT in den Verlauf zurueckgegeben: das KI-Feld veraendert
seinen Verlauf zwischen zwei Aufrufen (Kontextzeile, Platzhalter,
Verdichtung), und ein zurueckgegebener Denkblock waere danach ungueltig. Ohne
sie denkt das Modell nach jedem Werkzeugergebnis neu (adaptives Denken).

Keine Zahl hier legt ein Budget, eine Laenge oder eine Frist fest: max_tokens
kommt aus dem Budget (app/llm/budget.py), das Zeitbudget aus der
Haushaltseinstellung, Fenster und Antwortgrenze aus der Modellliste.
"""

import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import anthropic
import httpx2
from anthropic import AsyncAnthropic

from app.core.config import get_settings
from app.llm import reasons
from app.llm.settings import LLMSettings
from app.llm.stream import StreamAccumulator, StreamEvent
from app.llm.window import SOURCE_ANTHROPIC, ReasoningCapability, WindowInfo, effective_window
from app.schemas.ai import AIModelInfo

Message = dict[str, Any]
Block = dict[str, Any]

# Effort-Stufen der Messages-API, aufsteigend. Welche ein Modell anbietet,
# steht in seiner Modellliste (capabilities.effort.<stufe>.supported).
EFFORT_LEVELS: Final = ("low", "medium", "high", "xhigh", "max")

# Fehler, die der Gateway wie die von httpx behandelt. Das SDK laeuft auf
# httpx2 und verpackt nur Fehler beim Absenden; bricht die Verbindung MITTEN
# im Strom ab, kommen die httpx2-Fehler roh heraus - sie gehoeren dazu.
# (APITimeoutError erbt von APIConnectionError: der Gateway prueft Zeitfehler
# zuerst.)
TIMEOUT_ERRORS: Final = (anthropic.APITimeoutError, httpx2.TimeoutException)
CONNECTION_ERRORS: Final = (anthropic.APIError, httpx2.TransportError)


class UnusableStream(Exception):
    """Der Strom kam an, liess sich aber nicht zu einer Antwort zusammensetzen
    (kaputtes Werkzeug-JSON, leerer Strom, unbekannte Antwortform)."""


_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]")

# stop_reason -> finish_reason der OpenAI-Form (der Gateway kennt "length"
# als abgeschnitten, "tool_calls" als Werkzeugrunde).
_FINISH: Final[dict[str, str]] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "refusal",
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def make_client(settings: LLMSettings, base_url: str, timeout_seconds: float) -> AsyncAnthropic:
    """Client fuer genau diese Adresse. Ohne Schluessel gibt es keinen - der
    Aufrufer prueft vorher (model_key_missing)."""
    if settings.api_key is None:
        raise ValueError("Kein API-Schlüssel für Anthropic hinterlegt.")
    # Keine stillen Wiederholungen des SDK: jeder Versuch ist eine
    # llm_calls-Zeile mit eigenem Grund, und das Zeitbudget je Anfrage soll
    # stimmen (das SDK wartete bei 429 sonst so lange, wie der Server will).
    return AsyncAnthropic(
        api_key=settings.api_key.reveal(),
        base_url=base_url,
        timeout=anthropic.Timeout(timeout_seconds),
        max_retries=0,
    )


# ---------------------------------------------------------------------------
# Modelle und Fenster
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeModel:
    """Ein Modell aus GET /v1/models samt dem, was der Kern daraus braucht."""

    info: AIModelInfo
    max_input_tokens: int | None
    max_output_tokens: int | None
    adaptive_thinking: bool
    effort_levels: tuple[str, ...]


def _positive(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value > 0 else None


def _supported(tree: Any, *path: str) -> bool:
    """capabilities ist ein ungetypter Baum mit {"supported": bool} an jedem
    Blatt. Fehlt ein Zweig, gilt "nicht unterstuetzt" - nie geraten."""
    node = tree
    for key in path:
        if not isinstance(node, dict):
            return False
        node = node.get(key)
    return isinstance(node, dict) and node.get("supported") is True


def _capabilities_of(raw: Any) -> dict[str, Any]:
    caps = getattr(raw, "capabilities", None)
    if caps is None and isinstance(raw, dict):
        caps = raw.get("capabilities")
    dump = getattr(caps, "model_dump", None)
    if callable(dump):
        caps = dump()
    return caps if isinstance(caps, dict) else {}


def _field(raw: Any, name: str) -> Any:
    if isinstance(raw, dict):
        return raw.get(name)
    return getattr(raw, name, None)


def model_from_api(raw: Any) -> ClaudeModel:
    """Eintrag aus GET /v1/models -> ClaudeModel.

    max_input_tokens ist das Fenster, max_tokens die hoechste Antwortlaenge.
    Denkoptionen fuer die Oberflaeche: "off" (nur wenn das Modell Denken
    abschalten laesst - Opus 5.5 und Fable lassen es nicht), "on" (adaptiv),
    dazu die Effort-Stufen, die das Modell meldet."""
    caps = _capabilities_of(raw)
    model_id = str(_field(raw, "id") or "")
    max_input = _positive(_field(raw, "max_input_tokens"))
    max_output = _positive(_field(raw, "max_tokens"))
    adaptive = _supported(caps, "thinking", "types", "adaptive")
    can_disable = _supported(caps, "thinking", "types", "disabled")
    efforts = (
        tuple(level for level in EFFORT_LEVELS if _supported(caps, "effort", level))
        if _supported(caps, "effort")
        else ()
    )
    names: list[str] = []
    if adaptive:
        names.append("thinking:adaptive")
    if can_disable:
        names.append("thinking:disabled")
    names.extend(f"effort:{level}" for level in efforts)
    if _supported(caps, "structured_outputs"):
        names.append("structured_outputs")
    if _supported(caps, "image_input"):
        names.append("image_input")
    options = (["off"] if can_disable else []) + (["on"] if adaptive else []) + list(efforts)
    display = _field(raw, "display_name")
    if display and _field(raw, "lifecycle") == "deprecated":
        display = f"{display} (abgekündigt)"
    info = AIModelInfo(
        id=model_id,
        kind="llm",
        state="loaded",
        publisher="Anthropic",
        max_context_tokens=max_input,
        loaded_context_tokens=max_input,
        capabilities=names,
        is_chat_capable=True,
        reasoning_options=options,
        reasoning_default=None,
        display_name=str(display) if display else None,
        output_limit_tokens=max_output,
    )
    return ClaudeModel(
        info=info,
        max_input_tokens=max_input,
        max_output_tokens=max_output,
        adaptive_thinking=adaptive,
        effort_levels=efforts,
    )


async def list_models(settings: LLMSettings, base_url: str) -> list[ClaudeModel]:
    """Alle Modelle, die der Schluessel erreicht. Wirft die Fehler des SDK
    (AuthenticationError, APIConnectionError, ...) - der Aufrufer benennt sie."""
    timeout = get_settings().ai_discovery_timeout_seconds
    async with make_client(settings, base_url, timeout) as client:
        found: list[ClaudeModel] = []
        async for raw in client.models.list():
            # Ausgemusterte Modelle scheiterten bei jedem Aufruf - nicht anbieten.
            if _field(raw, "lifecycle") == "retired":
                continue
            model = model_from_api(raw)
            if model.info.id:
                found.append(model)
        return found


def window_of(model: ClaudeModel, cap: int | None) -> WindowInfo:
    """Fenster eines Claude-Modells nach derselben Regel wie lokal:
    W = min(max_input_tokens, "Kontext hoechstens"); Antwortgrenze aus
    max_tokens der Modellliste."""
    if "thinking:disabled" in model.info.capabilities:
        kind: Any = "switchable"
    elif model.adaptive_thinking or model.effort_levels:
        kind = "always"
    else:
        kind = "none"
    return effective_window(
        model.max_input_tokens,
        cap,
        source=SOURCE_ANTHROPIC,
        why_unknown=f"Anthropic nennt für „{model.info.id}“ kein Kontextfenster",
        reasoning_capability=ReasoningCapability(
            kind=kind, allowed_options=tuple(model.info.reasoning_options), default=None
        ),
        max_context=model.max_input_tokens,
        model_loaded=True,
        output_limit=model.max_output_tokens,
    )


def failure_of(exc: Exception) -> tuple[str, str]:
    """Fehler beim Lesen der Modellliste -> (Grund, Befund)."""
    detail = f"{type(exc).__name__}: {error_text(exc)}"
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return reasons.MODEL_KEY_REJECTED, detail
    if isinstance(exc, anthropic.RateLimitError):
        return reasons.MODEL_RATE_LIMITED, detail
    return reasons.MODEL_UNAVAILABLE, detail


# ---------------------------------------------------------------------------
# Reasoning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReasoningChoice:
    """Was zum Denken gesendet wird.

    thinking  thinking-Parameter oder None (Modellstandard)
    effort    output_config.effort oder None
    label     Kurzform fuer llm_calls.reasoning_sent
    skipped   Satz, wenn die Einstellung bei diesem Modell nicht gesendet
              werden kann (Info-Ereignis model_reasoning_unsupported)"""

    thinking: dict[str, Any] | None = None
    effort: str | None = None
    label: str | None = None
    skipped: str | None = None


def reasoning_choice(
    settings: LLMSettings, override: str | None, capabilities: Sequence[str]
) -> ReasoningChoice:
    """Die Reasoning-Einstellung fuer Claude.

    Ohne Einstellung wird nichts gesendet (Modellstandard). "off" schaltet
    das Denken ab (wo das Modell es zulaesst), "on" adaptiv an; eine
    Effort-Stufe setzt diese Stufe mit adaptivem Denken. Was das Modell laut
    Modellliste nicht anbietet, wird nicht gesendet und als Info gesagt.
    Denktext wird als Zusammenfassung angefordert, damit das KI-Feld ihn live
    zeigen kann - die Denktiefe aendert das nicht."""
    chosen = settings.effective_reasoning(override)
    if not chosen:
        return ReasoningChoice()
    adaptive = "thinking:adaptive" in capabilities
    efforts = {name.split(":", 1)[1] for name in capabilities if name.startswith("effort:")}
    thinking_on = {"type": "adaptive", "display": "summarized"}
    if chosen == "off" and "thinking:disabled" in capabilities:
        return ReasoningChoice(thinking={"type": "disabled"}, label="thinking=disabled")
    if chosen == "on" and adaptive:
        return ReasoningChoice(thinking=thinking_on, label="thinking=adaptive")
    if chosen in efforts:
        return ReasoningChoice(
            thinking=thinking_on if adaptive else None,
            effort=chosen,
            label=f"effort={chosen}",
        )
    return ReasoningChoice(
        skipped=(
            f"Das Modell bietet die Denkstufe „{chosen}“ nicht an; es wird nichts gesendet "
            "(Modellstandard)."
        )
    )


# ---------------------------------------------------------------------------
# Anfrage
# ---------------------------------------------------------------------------


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


# Bildformate, die die Messages-API annimmt.
_IMAGE_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


def _image_block(part: dict[str, Any]) -> Block:
    """image_url (OpenAI-Form) -> image-Block. Ein Bild, das sich nicht
    uebertragen laesst, bleibt als Satz sichtbar statt still zu fehlen."""
    image = part.get("image_url")
    url = str((image.get("url") if isinstance(image, dict) else image) or "")
    if url.startswith("data:") and ";base64," in url:
        media, data = url.removeprefix("data:").split(";base64,", 1)
        if media in _IMAGE_TYPES and data:
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": media, "data": data},
            }
        shown = media or "unbekannt"
        return {"type": "text", "text": f"[Bild im Format {shown} nicht übertragbar]"}
    if url.startswith(("https://", "http://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "text", "text": "[Bild ohne lesbare Adresse nicht übertragbar]"}


def _user_blocks(content: Any) -> list[Block]:
    """Inhalt einer Nutzernachricht -> Bloecke: Text und Bilder in ihrer
    Reihenfolge; leere Texte entfallen."""
    if not isinstance(content, list):
        text = _text_of(content)
        return [{"type": "text", "text": text}] if text.strip() else []
    blocks: list[Block] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            blocks.append(_image_block(part))
            continue
        text = str(part.get("text") or "") if isinstance(part, dict) else str(part)
        if text.strip():
            blocks.append({"type": "text", "text": text})
    return blocks


def _tool_input(arguments: Any) -> dict[str, Any]:
    """Argumente eines Werkzeugaufrufs als Objekt (die Messages-API verlangt
    eins). Unlesbares JSON bleibt als Text sichtbar statt zu verschwinden."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except ValueError:
            return {"_unlesbare_argumente": arguments}
        return parsed if isinstance(parsed, dict) else {"wert": parsed}
    return {}


class _ToolIds:
    """Werkzeugkennungen fuer die Messages-API: nur [A-Za-z0-9_-] und
    eindeutig im Verlauf. Als Text erkannte Aufrufe (pseudo_N) koennen nach
    einem Neustart des Workers doppelt vorkommen - das Ergebnis gehoert dann
    zum juengsten Aufruf mit dieser Kennung."""

    def __init__(self) -> None:
        self._used: set[str] = set()
        self._current: dict[str, str] = {}

    def issue(self, raw: Any) -> str:
        base = _ID_CHARS.sub("_", str(raw or "")) or "aufruf"
        candidate = base
        number = 1
        while candidate in self._used:
            number += 1
            candidate = f"{base}_{number}"
        self._used.add(candidate)
        self._current[str(raw or "")] = candidate
        return candidate

    def lookup(self, raw: Any) -> str | None:
        return self._current.get(str(raw or ""))


def convert_messages(
    messages: Sequence[Message], *, with_tools: bool
) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI-Nachrichten -> (system, Messages-API-Nachrichten).

    Ohne Werkzeuge in der Anfrage (Schlussantwort, Verdichtung) werden
    Werkzeugaufrufe und -ergebnisse des Verlaufs zu Text - die Messages-API
    nimmt tool_use-Bloecke nur zusammen mit Werkzeugdefinitionen an."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    ids = _ToolIds()

    def add(role: str, blocks: list[Block]) -> None:
        if not blocks:
            return
        previous = out[-1] if out else None
        # Auf eine Werkzeugrunde folgt nie direkt weiterer Assistententext in
        # derselben Nachricht - _repair setzt die fehlenden Ergebnisse dazwischen.
        open_round = previous is not None and any(
            block.get("type") == "tool_use" for block in previous["content"]
        )
        if previous is not None and previous["role"] == role and not (
            role == "assistant" and open_round
        ):
            previous["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for message in messages:
        if not message:
            continue
        role = message.get("role")
        content = _text_of(message.get("content"))
        if role == "system":
            if content.strip():
                system_parts.append(content)
            continue
        if role == "assistant":
            blocks: list[Block] = []
            if content.strip():
                blocks.append({"type": "text", "text": content})
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                name = str(function.get("name") or "") if isinstance(function, dict) else ""
                arguments = function.get("arguments") if isinstance(function, dict) else None
                if with_tools and name:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": ids.issue(call.get("id")),
                            "name": name,
                            "input": _tool_input(arguments),
                        }
                    )
                else:
                    shown = arguments if isinstance(arguments, str) else json.dumps(
                        arguments, ensure_ascii=False
                    )
                    blocks.append(
                        {"type": "text", "text": f"[Werkzeugaufruf {name or '?'}: {shown}]"}
                    )
            add("assistant", blocks)
            continue
        if role == "tool":
            result = content if content.strip() else "(leer)"
            known = ids.lookup(message.get("tool_call_id")) if with_tools else None
            if known is not None:
                add("user", [{"type": "tool_result", "tool_use_id": known, "content": result}])
            else:
                label = message.get("name") or message.get("tool_call_id") or "?"
                add("user", [{"type": "text", "text": f"[Werkzeugergebnis {label}]\n{result}"}])
            continue
        add("user", _user_blocks(message.get("content")))

    return "\n\n".join(system_parts), _repair(out)


_MISSING_RESULT: Final = "Kein Ergebnis vorhanden."


def _repair(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Die Messages-API verlangt: erste Nachricht vom Nutzer; auf jede
    Werkzeugrunde folgt eine user-Nachricht mit genau ihren tool_result-
    Bloecken, und zwar vorn. Ein fehlendes Ergebnis (Verlauf aus der
    Datenbank, Ergebnis nicht mehr da) wird als Fehlerergebnis benannt; ein
    Ergebnis ohne passenden Aufruf wird zu Text."""
    fixed: list[dict[str, Any]] = []

    def pending(previous: dict[str, Any] | None) -> list[str]:
        if previous is None or previous["role"] != "assistant":
            return []
        return [block["id"] for block in previous["content"] if block.get("type") == "tool_use"]

    def results_for(expected: list[str], blocks: list[Block]) -> list[Block]:
        seen: set[str] = set()
        results: list[Block] = []
        others: list[Block] = []
        for block in blocks:
            if block.get("type") == "tool_result":
                tool_id = str(block.get("tool_use_id"))
                if tool_id in expected and tool_id not in seen:
                    seen.add(tool_id)
                    results.append(block)
                else:
                    others.append(
                        {"type": "text", "text": f"[Werkzeugergebnis]\n{block.get('content')}"}
                    )
            else:
                others.append(block)
        for tool_id in expected:
            if tool_id not in seen:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": _MISSING_RESULT,
                        "is_error": True,
                    }
                )
        return results + others

    for message in messages:
        previous = fixed[-1] if fixed else None
        if message["role"] == "user":
            fixed.append(
                {"role": "user", "content": results_for(pending(previous), message["content"])}
            )
            continue
        if previous is None:
            fixed.append(
                {"role": "user", "content": [{"type": "text", "text": "(Fortsetzung)"}]}
            )
        elif pending(previous):
            fixed.append({"role": "user", "content": results_for(pending(previous), [])})
        fixed.append(message)
    if pending(fixed[-1] if fixed else None):
        fixed.append({"role": "user", "content": results_for(pending(fixed[-1]), [])})
    return fixed


def convert_tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-Werkzeuge -> Messages-API (name, description, input_schema)."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(function, dict) or not function.get("name"):
            continue
        schema = function.get("parameters")
        converted.append(
            {
                "name": str(function["name"]),
                "description": str(function.get("description") or ""),
                "input_schema": (
                    schema
                    if isinstance(schema, dict) and schema
                    else {"type": "object", "properties": {}}
                ),
            }
        )
    return converted


def convert_tool_choice(choice: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if choice is None:
        return None
    if isinstance(choice, str):
        named = {"auto": "auto", "none": "none", "required": "any"}.get(choice)
        return {"type": named} if named else None
    function = choice.get("function") if isinstance(choice, dict) else None
    if isinstance(function, dict) and function.get("name"):
        return {"type": "tool", "name": str(function["name"])}
    return None


def _closed(schema: Any) -> bool:
    """Erzwingen kann die Messages-API nur Objekte mit festen Feldern
    (additionalProperties: false). Ein Objekt mit freien Schluesseln wuerde
    beim Umformen zu einem leeren Objekt - dann lieber nicht erzwingen."""
    if isinstance(schema, list):
        return all(_closed(item) for item in schema)
    if not isinstance(schema, dict):
        return True
    is_object = schema.get("type") == "object" or "properties" in schema
    if is_object:
        extra = schema.get("additionalProperties")
        if extra not in (None, False):
            return False
        if not isinstance(schema.get("properties"), dict):
            return False
    return all(_closed(value) for value in schema.values() if isinstance(value, dict | list))


def convert_format(
    response_format: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """response_format -> output_config.format, oder (None, Satz), wenn es
    kein Gegenstueck gibt. Ohne Format prueft der Aufrufer die Antwort wie
    sonst gegen seinen Vertrag (answers.ask)."""
    if not response_format:
        return None, None
    if response_format.get("type") != "json_schema":
        return None, None
    wrapper = response_format.get("json_schema")
    schema = wrapper.get("schema") if isinstance(wrapper, dict) else None
    unenforced = (
        "Claude erzwingt dieses Antwortschema nicht ({}); die Antwort wird nach dem Vertrag "
        "der Aufrufstelle geprüft."
    )
    if not isinstance(schema, dict) or not _closed(schema):
        return None, unenforced.format("Objekte mit freien Schlüsseln")
    try:
        transformed = anthropic.transform_schema(schema)
    except (ValueError, AssertionError, TypeError) as exc:
        return None, unenforced.format(f"nicht umformbar: {type(exc).__name__}")
    return {"type": "json_schema", "schema": transformed}, None


@dataclass(frozen=True)
class Request:
    """Fertige Anfrage samt Hinweisen fuer das Laufprotokoll."""

    params: dict[str, Any]
    notes: tuple[str, ...] = ()


def build_request(
    *,
    model: str,
    messages: Sequence[Message],
    settings: LLMSettings,
    max_tokens: int,
    response_format: dict[str, Any] | None,
    tools: Sequence[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
    reasoning: ReasoningChoice,
    reasoning_override: str | None,
) -> Request:
    """Anfrage an POST /v1/messages.

    Cache: der Systemtext traegt eine Cache-Marke (er ist ueber viele Aufrufe
    gleich - Kategorienliste, KI-Feld-Regeln); mit Werkzeugen cacht die
    Anfrage zusaetzlich ihr Ende, weil jede Runde der Werkzeugschleife den
    Verlauf erneut schickt. Das aendert keine Antwort, nur den Preis."""
    notes: list[str] = []
    system, converted = convert_messages(messages, with_tools=bool(tools))
    params: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": converted}
    if system:
        params["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
    # Sampling-Werte gehen nicht an Claude: die Einstellungen zeigen sie bei
    # diesem Anbieter nicht (neuere Modelle lehnen temperature/top_p/top_k ab),
    # und die Herstellerprofile gelten lokalen Modellen. Ein gespeicherter
    # manueller Modus wird deshalb nicht still angewandt, sondern benannt.
    if settings.sampling_mode == "manual":
        notes.append(
            "Die manuellen Sampling-Werte gelten nur für lokale Modelle – an Claude nicht "
            "gesendet (Modellstandard)."
        )
    if tools:
        params["tools"] = convert_tools(tools)
        params["cache_control"] = {"type": "ephemeral"}
        choice = convert_tool_choice(tool_choice)
        if choice is not None:
            params["tool_choice"] = choice
    output_config: dict[str, Any] = {}
    output_format, format_note = convert_format(response_format)
    if output_format is not None:
        output_config["format"] = output_format
    if format_note:
        notes.append(format_note)
    if reasoning.effort:
        output_config["effort"] = reasoning.effort
    if output_config:
        params["output_config"] = output_config
    if reasoning.thinking is not None:
        params["thinking"] = reasoning.thinking
    return Request(params=params, notes=tuple(notes))


# ---------------------------------------------------------------------------
# Antwort
# ---------------------------------------------------------------------------


def error_text(exc: BaseException) -> str:
    """Fehlermeldung des Anbieters: der Satz aus dem Fehlerobjekt, sonst die
    Meldung des SDK."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            kind = error.get("type")
            return f"{kind}: {error['message']}" if kind else str(error["message"])
    message = getattr(exc, "message", None)
    return str(message or exc)


_SCHEMA_WORDS = re.compile(
    r"output_config|output_format|\bformat\b|schema|\btools?\b|tool_choice|input_schema",
    re.IGNORECASE,
)


def mentions_schema(text: str) -> bool:
    """Nennt ein 400 der Messages-API das Schema oder die Werkzeuge? Nur dann
    ist es "Schema nicht unterstuetzt" - ein anderer 400 (Datenaufbewahrung,
    Rollenfolge, Bildgroesse) darf nicht als Faehigkeit gespeichert werden."""
    return bool(_SCHEMA_WORDS.search(text))


def error_code(status: int, text: str) -> str | None:
    """Fehler, die einen eigenen Grund haben; alles andere wertet der Gateway
    wie bei jedem Server aus (Ueberlauf, Reasoning, Schema, HTTP-Fehler)."""
    lowered = text.lower()
    if status == 401 or "authentication_error" in lowered:
        return reasons.MODEL_KEY_REJECTED
    if status == 403 or "permission_error" in lowered:
        return reasons.MODEL_KEY_REJECTED
    if "credit balance" in lowered or "billing" in lowered:
        return reasons.MODEL_CREDIT_EXHAUSTED
    if status == 429 or "rate_limit_error" in lowered:
        return reasons.MODEL_RATE_LIMITED
    if status == 404 and "model" in lowered:
        return reasons.MODEL_MISSING
    if status in (500, 502, 503, 504, 529) or any(
        kind in lowered for kind in ("overloaded_error", "api_error")
    ):
        return reasons.MODEL_UNAVAILABLE
    return None


def _block_type(block: Any) -> str:
    return str(_field(block, "type") or "")


def payload_of(message: Any) -> dict[str, Any]:
    """Fertige Antwort -> Form der blockierenden OpenAI-Antwort.

    usage.prompt_tokens ist die GANZE Eingabe, die das Modell gesehen hat -
    auch der Teil aus dem Cache. Nur so stimmt die Messung von Zeichen je
    Token und die exakte Zaehlung des naechsten Aufrufs."""
    content = _field(message, "content") or []
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in content:
        kind = _block_type(block)
        if kind == "text":
            text_parts.append(str(_field(block, "text") or ""))
        elif kind == "thinking":
            thinking_parts.append(str(_field(block, "thinking") or ""))
        elif kind == "tool_use":
            calls.append(
                {
                    "id": str(_field(block, "id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(_field(block, "name") or ""),
                        "arguments": json.dumps(_field(block, "input") or {}, ensure_ascii=False),
                    },
                }
            )
    out_message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    thinking = "".join(thinking_parts)
    if thinking:
        out_message["reasoning_content"] = thinking
    if calls:
        out_message["tool_calls"] = calls
    stop_reason = _field(message, "stop_reason")
    payload: dict[str, Any] = {
        "model": _field(message, "model"),
        "choices": [
            {
                "index": 0,
                "message": out_message,
                "finish_reason": _FINISH.get(str(stop_reason), stop_reason),
            }
        ],
    }
    usage = _field(message, "usage")
    if usage is not None:
        given = _positive(_field(usage, "input_tokens")) or 0
        read = _positive(_field(usage, "cache_read_input_tokens")) or 0
        written = _positive(_field(usage, "cache_creation_input_tokens")) or 0
        output = _field(usage, "output_tokens")
        payload["usage"] = {
            "prompt_tokens": given + read + written,
            "completion_tokens": output if isinstance(output, int) else None,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": written,
        }
    details = _field(message, "stop_details")
    if details is not None:
        payload["stop_details"] = {
            "category": _field(details, "category"),
            "explanation": _field(details, "explanation"),
        }
    return payload


@dataclass
class Outcome:
    """Ergebnis eines Versuchs: Antwort (payload) oder Fehler des Anbieters."""

    status: int
    error: str | None
    payload: dict[str, Any] | None
    ttft_ms: int | None = None


def _live(
    event: Any, accumulator: StreamAccumulator, tool_numbers: dict[int, int]
) -> list[StreamEvent]:
    """Rohereignis des Stroms -> Live-Stuecke (dieselben wie bei LM Studio).

    Werkzeugaufrufe tragen ihre laufende Nummer (0, 1, ...) wie bei LM Studio -
    nicht den Index des Inhaltsblocks, vor dem Denk- und Textbloecke stehen
    koennen. Das KI-Feld ordnet die live gezeigten Argumente danach zu."""
    kind = _field(event, "type")
    index = _field(event, "index")
    block_index = index if isinstance(index, int) else 0
    if kind == "content_block_start":
        block = _field(event, "content_block")
        if _block_type(block) == "tool_use":
            number = tool_numbers.setdefault(block_index, len(tool_numbers))
            return accumulator.start_tool(
                number, str(_field(block, "id") or ""), str(_field(block, "name") or "")
            )
        return []
    if kind != "content_block_delta":
        return []
    delta = _field(event, "delta")
    delta_kind = _block_type(delta)
    if delta_kind == "text_delta":
        return accumulator.add_content(str(_field(delta, "text") or ""))
    if delta_kind == "thinking_delta":
        return accumulator.add_reasoning(str(_field(delta, "thinking") or ""))
    if delta_kind == "input_json_delta":
        number = tool_numbers.setdefault(block_index, len(tool_numbers))
        return accumulator.add_tool_args(number, str(_field(delta, "partial_json") or ""))
    return []


async def attempt(
    client: AsyncAnthropic,
    params: dict[str, Any],
    accumulator: StreamAccumulator,
    emit: Callable[[StreamEvent], Awaitable[None]],
    started: float,
    first_seen: list[float],
    mask: Callable[[str], str],
) -> Outcome:
    """Ein Aufruf, immer gestreamt (lange Antworten laufen sonst in das
    Zeitbudget einer einzelnen HTTP-Antwort). Was bis zu einem Abbruch kam,
    steht im accumulator; Zeitueberschreitung und Verbindungsfehler wirft
    das SDK weiter - der Gateway benennt sie. Ein Strom, der sich nicht zu
    einer Antwort zusammensetzen laesst, wirft UnusableStream."""
    tool_numbers: dict[int, int] = {}
    try:
        async with client.messages.stream(**params) as stream:
            async for event in stream:
                pieces = _live(event, accumulator, tool_numbers)
                if pieces and not first_seen:
                    first_seen.append(time.monotonic())
                for piece in pieces:
                    await emit(piece)
            final = await stream.get_final_message()
    except anthropic.APIStatusError as exc:
        ttft = int((first_seen[0] - started) * 1000) if first_seen else None
        return Outcome(
            status=int(exc.status_code), error=mask(error_text(exc)), payload=None, ttft_ms=ttft
        )
    except (anthropic.APIResponseValidationError, ValueError, AssertionError) as exc:
        # SDK-Fehler beim Zusammensetzen (kaputtes Werkzeug-JSON, leerer
        # Strom) - keine Verbindungs-, sondern eine Antwortfrage.
        raise UnusableStream(mask(f"{type(exc).__name__}: {exc}")) from exc
    ttft = int((first_seen[0] - started) * 1000) if first_seen else None
    return Outcome(status=200, error=None, payload=payload_of(final), ttft_ms=ttft)
