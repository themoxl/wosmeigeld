"""Streaming eines Chat-Aufrufs: SSE eines OpenAI-kompatiblen Servers lesen.

Port von sia_public/server/jet_server.py (_stream_ai). Der Gateway
(app/llm/gateway.py) fragt mit `stream: true` und
`stream_options.include_usage`; dieses Modul setzt die Stuecke wieder zu
GENAU der Antwortform zusammen, die der blockierende Aufruf liefert
({"choices": [{"message": ..., "finish_reason": ...}], "usage": ...}). Damit
wertet der Gateway beide Wege mit derselben Funktion aus - Budget,
Messung, Gruende und Ereignisse unterscheiden sich nicht.

Was unterwegs entsteht, geht als StreamEvent an einen Rueckruf (nur live,
nie gespeichert):

  think  neues Stueck Denktext; `chars` = Denkzeichen des Aufrufs bisher
  delta  neues Stueck Antworttext
  tool   ein Werkzeugaufruf entsteht (`index`, `name`)
  targs  Stueck seiner Argumente (`index`, `text`)

Kein Netz, keine DB: reine Zustandsmaschine ueber Textzeilen.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

StreamKind = Literal["think", "delta", "tool", "targs"]


@dataclass(frozen=True)
class StreamEvent:
    """Ein live gemeldetes Stueck eines laufenden Aufrufs."""

    kind: StreamKind
    text: str = ""
    # think: Denkzeichen des Aufrufs insgesamt (einschliesslich dieses Stuecks)
    chars: int = 0
    # tool/targs: Position des Werkzeugaufrufs in der Antwort
    index: int | None = None
    name: str | None = None


StreamCallback = Callable[[StreamEvent], Awaitable[None]]


def _error_message(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        return str(message) if message else json.dumps(error, ensure_ascii=False)
    return str(error)


@dataclass
class _ToolSlot:
    id: str | None = None
    name: str = ""
    arguments: str = ""


@dataclass
class StreamAccumulator:
    """Sammelt die SSE-Zeilen eines Aufrufs.

    feed_line() nimmt eine Zeile und liefert die Live-Ereignisse, die sie
    enthaelt. Nach dem Ende liefert payload() die blockierende Antwortform;
    `error` ist gesetzt, wenn der Server mitten im Strom einen Fehler meldet
    (LM Studio schickt einen Fensterueberlauf teils erst als SSE-Ereignis)."""

    content_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    reasoning_chars: int = 0
    tools: dict[int, _ToolSlot] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    model: str | None = None
    saw_data: bool = False
    done: bool = False
    error: str | None = None
    # Zeilen ohne "data:" vor dem ersten Ereignis: ein Server, der den Strom
    # nicht kennt, antwortet mit dem ganzen JSON (oder einem Fehler-JSON).
    other_lines: list[str] = field(default_factory=list)

    @property
    def content(self) -> str:
        return "".join(self.content_parts)

    @property
    def reasoning(self) -> str:
        return "".join(self.reasoning_parts)

    def feed_line(self, raw: str) -> list[StreamEvent]:
        line = raw.strip()
        if not line or line.startswith(":"):
            return []
        if not line.startswith("data:"):
            if not self.saw_data:
                self.other_lines.append(line)
            return []
        self.saw_data = True
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            self.done = True
            return []
        try:
            chunk = json.loads(data)
        except ValueError:
            return []
        if not isinstance(chunk, dict):
            return []
        if chunk.get("error") and not chunk.get("choices"):
            self.error = _error_message(chunk["error"])
            self.done = True
            return []
        if chunk.get("model"):
            self.model = str(chunk["model"])
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return []
        choice = choices[0]
        if choice.get("finish_reason"):
            self.finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta") or choice.get("message") or {}
        if not isinstance(delta, dict):
            return []
        events: list[StreamEvent] = []
        thinking = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if isinstance(thinking, str) and thinking:
            self.reasoning_parts.append(thinking)
            self.reasoning_chars += len(thinking)
            events.append(StreamEvent(kind="think", text=thinking, chars=self.reasoning_chars))
        text = delta.get("content") or ""
        if isinstance(text, str) and text:
            self.content_parts.append(text)
            events.append(StreamEvent(kind="delta", text=text))
        for raw_call in delta.get("tool_calls") or []:
            if not isinstance(raw_call, dict):
                continue
            index = raw_call.get("index")
            position = index if isinstance(index, int) else len(self.tools)
            slot = self.tools.setdefault(position, _ToolSlot())
            if raw_call.get("id"):
                slot.id = str(raw_call["id"])
            function = raw_call.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if isinstance(name, str) and name and not slot.name:
                slot.name = name
                events.append(StreamEvent(kind="tool", index=position, name=name))
            arguments = function.get("arguments")
            if isinstance(arguments, str) and arguments:
                slot.arguments += arguments
                events.append(StreamEvent(kind="targs", index=position, text=arguments))
            elif isinstance(arguments, dict):
                text_args = json.dumps(arguments, ensure_ascii=False)
                slot.arguments += text_args
                events.append(StreamEvent(kind="targs", index=position, text=text_args))
        return events

    # --- Fuettern ohne SSE-Zeilen (Anthropic, app/llm/anthropic_api.py) ---
    # Dieselben Live-Stuecke und derselbe Zwischenstand wie bei feed_line(),
    # damit ein Abbruch auch dort die Teilantwort sichtbar laesst.

    def add_content(self, text: str) -> list[StreamEvent]:
        if not text:
            return []
        self.saw_data = True
        self.content_parts.append(text)
        return [StreamEvent(kind="delta", text=text)]

    def add_reasoning(self, text: str) -> list[StreamEvent]:
        if not text:
            return []
        self.saw_data = True
        self.reasoning_parts.append(text)
        self.reasoning_chars += len(text)
        return [StreamEvent(kind="think", text=text, chars=self.reasoning_chars)]

    def start_tool(self, index: int, call_id: str, name: str) -> list[StreamEvent]:
        self.saw_data = True
        slot = self.tools.setdefault(index, _ToolSlot())
        if call_id:
            slot.id = call_id
        if name and not slot.name:
            slot.name = name
            return [StreamEvent(kind="tool", index=index, name=name)]
        return []

    def add_tool_args(self, index: int, text: str) -> list[StreamEvent]:
        if not text:
            return []
        self.saw_data = True
        slot = self.tools.setdefault(index, _ToolSlot())
        slot.arguments += text
        return [StreamEvent(kind="targs", index=index, text=text)]

    def fallback_payload(self) -> dict[str, Any] | None:
        """Kein einziges SSE-Ereignis: der Koerper ist eine gewoehnliche
        JSON-Antwort (Server ohne Streaming) oder ein Fehler-JSON."""
        if self.saw_data or not self.other_lines:
            return None
        try:
            payload = json.loads("\n".join(self.other_lines))
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def payload(self) -> dict[str, Any]:
        """Die Antwort in der Form des blockierenden Aufrufs."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.reasoning_parts:
            message["reasoning_content"] = self.reasoning
        calls = []
        for position in sorted(self.tools):
            slot = self.tools[position]
            calls.append(
                {
                    "id": slot.id or f"call_stream_{position}",
                    "type": "function",
                    "function": {"name": slot.name, "arguments": slot.arguments},
                }
            )
        if calls:
            message["tool_calls"] = calls
        payload: dict[str, Any] = {
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish_reason}]
        }
        if self.model:
            payload["model"] = self.model
        if self.usage is not None:
            payload["usage"] = self.usage
        return payload
