"""Gefaelschter OpenAI-kompatibler Modellserver fuer die Tests des KI-Felds.

httpx.MockTransport mit asynchronem Handler: Discovery (LM Studio
/api/v0/models und /api/v1/models mit geladenem Fenster), dazu
/chat/completions nach Drehbuch - blockierend (JSON) oder gestreamt (SSE),
je nachdem, was der Gateway anfragt. Ein Eintrag "block" haelt die Verbindung
offen, bis der Client sie schliesst; `closed` zaehlt, wie oft das geschah.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

MODEL = "fake-model"


@dataclass
class Reply:
    """Eine Antwort des Modells."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[tuple[str, dict[str, Any] | str]] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int | None = 900
    completion_tokens: int | None = 120
    status: int = 200
    error: str | None = None
    # Stuecke, in die Inhalt/Denktext beim Streamen zerlegt werden
    pieces: int = 3


BLOCK = "block"
BLOCK_AFTER_FIRST = "block_after_first"


def _split(text: str, pieces: int) -> list[str]:
    if not text:
        return []
    size = max(1, -(-len(text) // max(1, pieces)))
    return [text[index : index + size] for index in range(0, len(text), size)]


class FakeModelServer:
    def __init__(
        self,
        script: list[Reply | str | Callable[[dict[str, Any]], Reply]],
        *,
        window: int = 32768,
        reasoning: dict[str, Any] | None = None,
    ) -> None:
        self.script = list(script)
        self.window = window
        self.reasoning = reasoning or {"allowed_options": ["off", "on"], "default": "on"}
        self.bodies: list[dict[str, Any]] = []
        self.closed = 0
        self.started = asyncio.Event()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    # ------------------------------------------------------------ Discovery

    def _models(self, request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if path.endswith("/api/v0/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": MODEL,
                            "type": "llm",
                            "state": "loaded",
                            "max_context_length": self.window * 4,
                            "loaded_context_length": self.window,
                        }
                    ]
                },
            )
        if path.endswith("/api/v1/models"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "key": MODEL,
                            "max_context_length": self.window * 4,
                            "capabilities": {"reasoning": self.reasoning},
                            "loaded_instances": [
                                {"id": MODEL, "config": {"context_length": self.window}}
                            ],
                        }
                    ]
                },
            )
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        return None

    # ------------------------------------------------------------ Antworten

    async def handle(self, request: httpx.Request) -> httpx.Response:
        listed = self._models(request)
        if listed is not None:
            return listed
        body = json.loads(request.content)
        self.bodies.append(body)
        self.started.set()
        step = self.script.pop(0) if self.script else Reply(content="(Drehbuch leer)")
        if step == BLOCK:
            try:
                await asyncio.Event().wait()
            finally:
                self.closed += 1
            raise AssertionError("unerreichbar")
        if callable(step) and not isinstance(step, str):
            step = step(body)
        if step == BLOCK_AFTER_FIRST:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=self._blocking_stream(),
            )
        assert isinstance(step, Reply)
        if step.status != 200:
            return httpx.Response(step.status, json={"error": {"message": step.error or "Fehler"}})
        if body.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=self._sse(step, include_usage=bool(body.get("stream_options"))),
            )
        return httpx.Response(200, json=self._payload(step))

    def _calls(self, step: Reply) -> list[dict[str, Any]]:
        return [
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, str) else json.dumps(args),
                },
            }
            for index, (name, args) in enumerate(step.tool_calls)
        ]

    def _finish(self, step: Reply) -> str:
        return step.finish_reason or ("tool_calls" if step.tool_calls else "stop")

    def _usage(self, step: Reply) -> dict[str, Any] | None:
        if step.prompt_tokens is None:
            return None
        return {"prompt_tokens": step.prompt_tokens, "completion_tokens": step.completion_tokens}

    def _payload(self, step: Reply) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": step.content}
        if step.reasoning:
            message["reasoning_content"] = step.reasoning
        if step.tool_calls:
            message["tool_calls"] = self._calls(step)
        payload: dict[str, Any] = {
            "model": MODEL,
            "choices": [{"index": 0, "message": message, "finish_reason": self._finish(step)}],
        }
        usage = self._usage(step)
        if usage is not None:
            payload["usage"] = usage
        return payload

    async def _sse(self, step: Reply, *, include_usage: bool) -> AsyncIterator[bytes]:
        def chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
            data = {
                "model": MODEL,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(data)}\n\n".encode()

        try:
            for piece in _split(step.reasoning, step.pieces):
                yield chunk({"reasoning_content": piece})
                await asyncio.sleep(0)
            for piece in _split(step.content, step.pieces):
                yield chunk({"content": piece})
                await asyncio.sleep(0)
            for index, call in enumerate(self._calls(step)):
                yield chunk(
                    {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": call["id"],
                                "type": "function",
                                "function": {"name": call["function"]["name"], "arguments": ""},
                            }
                        ]
                    }
                )
                for piece in _split(call["function"]["arguments"], step.pieces):
                    yield chunk(
                        {"tool_calls": [{"index": index, "function": {"arguments": piece}}]}
                    )
            yield chunk({}, self._finish(step))
            usage = self._usage(step)
            if include_usage and usage is not None:
                yield f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except (GeneratorExit, asyncio.CancelledError):
            self.closed += 1
            raise

    async def _blocking_stream(self) -> AsyncIterator[bytes]:
        try:
            data = {"choices": [{"index": 0, "delta": {"reasoning_content": "Ich denke"}}]}
            yield f"data: {json.dumps(data)}\n\n".encode()
            await asyncio.Event().wait()
        finally:
            self.closed += 1
