"""Textpflege fuer die Agentenschleife des KI-Felds (Port aus
sia_public/src/core/ai.js).

* `<think>`-Bloecke im Inhalt (manche lokale Modelle denken dort statt in
  reasoning_content) sind nie Teil der Antwort - auch ein offener,
  abgeschnittener Block nicht.
* Format-Drift: Werkzeugaufrufe landen als TEXT im Inhalt statt als
  tool_calls - `<function=name><parameter=x>…</parameter></function>` (Qwen/
  Gemma, XML-Stil) oder `<tool_call>{"name": …, "arguments": {…}}</tool_call>`
  (Hermes/JSON-Stil). Solche Pseudoaufrufe werden erkannt und wie echte
  ausgefuehrt, nie als Antworttext beim Nutzer abgeladen.
* Chatvorlagen (Qwen, Gemma) nehmen Systemnachrichten nur an Position 0:
  mehrere werden in ihrer Reihenfolge zu einer zusammengefasst.

Reine Funktionen ohne Netz und DB.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import count
from typing import Any

Message = dict[str, Any]

_THINK_BLOCK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>[\s\S]*$", re.IGNORECASE)
_FUNCTION_XML = re.compile(r"<function=([\w.-]+)>([\s\S]*?)</function>", re.IGNORECASE)
_PARAMETER_XML = re.compile(r"<parameter=([\w.-]+)>\s*([\s\S]*?)\s*</parameter>", re.IGNORECASE)
_TOOL_CALL_JSON = re.compile(r"<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>", re.IGNORECASE)
_TOOL_CALL_REST = re.compile(r"<tool_call>[\s\S]*?(?:</tool_call>|$)", re.IGNORECASE)
_FUNCTION_REST = re.compile(r"<function=[\w.-]+>[\s\S]*?(?:</function>|$)", re.IGNORECASE)

_pseudo_ids = count(1)


def strip_think(text: str | None) -> str:
    """Denkbloecke entfernen, auch einen offenen am Ende."""
    value = _THINK_BLOCK.sub("", text or "")
    return _THINK_OPEN.sub("", value).strip()


def strip_pseudo(text: str | None) -> str:
    """Reste von Pseudo-Werkzeugaufrufen entfernen (auch unterminierte)."""
    value = _TOOL_CALL_REST.sub("", text or "")
    return _FUNCTION_REST.sub("", value).strip()


def clean_answer(text: str | None) -> str:
    """Antworttext fuer Nutzer und Speicher: ohne Denken, ohne Pseudoaufrufe."""
    return strip_pseudo(strip_think(text))


@dataclass(frozen=True)
class PseudoCall:
    id: str
    name: str
    # JSON-Text der Argumente (wie bei echten tool_calls)
    arguments: str
    # True: XML-Stil - alle Parameter kamen als Text (siehe coerce_arguments)
    xml: bool


def _scan(text: str) -> list[PseudoCall]:
    calls: list[PseudoCall] = []
    for match in _FUNCTION_XML.finditer(text):
        args = {name: value for name, value in _PARAMETER_XML.findall(match.group(2))}
        calls.append(
            PseudoCall(
                id=f"pseudo_{next(_pseudo_ids)}",
                name=match.group(1),
                arguments=json.dumps(args, ensure_ascii=False),
                xml=True,
            )
        )
    if calls:
        return calls
    for match in _TOOL_CALL_JSON.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            continue
        if not isinstance(payload, dict) or not payload.get("name"):
            continue
        raw = payload.get("arguments", payload.get("parameters", {}))
        # arguments kann schon ein JSON-TEXT sein (OpenAI-Serialisierung) -
        # dann nicht doppelt kodieren.
        arguments = raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)
        calls.append(
            PseudoCall(
                id=f"pseudo_{next(_pseudo_ids)}",
                name=str(payload["name"]),
                arguments=arguments,
                xml=False,
            )
        )
    return calls


def parse_pseudo_calls(text: str | None) -> list[PseudoCall]:
    """Pseudo-Werkzeugaufrufe im Inhalt.

    Bevorzugt AUSSERHALB von <think>: ein im Denken nur erwogener Aufruf darf
    eine fertige Antwort dahinter nicht verdraengen. Steht draussen nichts,
    zaehlt auch der Denkblock (der ganze Aufruf stand im Inline-Denken)."""
    raw = text or ""
    outside = _scan(strip_think(raw))
    return outside if outside else _scan(raw)


def coerce_arguments(args: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """XML-Pseudoaufrufe transportieren jeden Parameter als Text. Nur wo das
    angebotene Schema einen anderen Typ verlangt, wird JSON zurueckgelesen
    (z. B. "3" -> 3 fuer integer, '{"kind": …}' -> Objekt). Passt der Wert
    nicht, bleibt der Text - der Handler meldet dann einen konkreten Fehler."""
    properties = (schema or {}).get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return args
    result = dict(args)
    for key, value in args.items():
        spec = properties.get(key)
        if not isinstance(value, str) or not isinstance(spec, dict):
            continue
        declared = spec.get("type")
        types = declared if isinstance(declared, list) else [declared]
        if "string" in types or not any(types):
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            continue
        kind = (
            "null"
            if parsed is None
            else "array"
            if isinstance(parsed, list)
            else "object"
            if isinstance(parsed, dict)
            else "boolean"
            if isinstance(parsed, bool)
            else "integer"
            if isinstance(parsed, int)
            else "number"
            if isinstance(parsed, float)
            else "string"
        )
        if kind in types or (kind == "integer" and "number" in types):
            result[key] = parsed
    return result


def stable(value: Any) -> Any:
    """Werte mit sortierten Schluesseln - fuer den Vergleich von Argumenten."""
    if isinstance(value, dict):
        return {key: stable(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def system_first(messages: Sequence[Message]) -> list[Message]:
    """Alle Systemnachrichten in ihrer Reihenfolge zu EINER an Position 0
    (Jinja-Vorlagen von Qwen/Gemma: "System message must be at the
    beginning"). Gleicher Inhalt ergibt eine gleiche Nachricht - die exakte
    Zaehlung ueber Aufrufe hinweg bleibt moeglich."""
    systems = [message for message in messages if message.get("role") == "system"]
    if not systems or (len(systems) == 1 and messages and messages[0] is systems[0]):
        return list(messages)
    content = "\n\n".join(
        str(message.get("content")) for message in systems if message.get("content")
    )
    head: Message = {"role": "system", "content": content}
    return [head, *(message for message in messages if message.get("role") != "system")]
