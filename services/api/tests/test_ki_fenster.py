"""Fensterleser des KI-Kerns (app/llm/window.py) gegen gefaelschte Server.

Kein LM Studio, kein Netz: ein echter httpx-Client spricht mit einem
httpx.MockTransport, der je Pfad antwortet. Geprueft wird die Reihenfolge der
Leser (LM Studio v1, v0, Ollama, OpenAI-kompatibel), dass NUR das geladene
Fenster zaehlt (nie max_context_length) und wie der Deckel "Kontext
hoechstens" wirkt.
"""

from collections.abc import Callable
from typing import Any

import httpx

from app.llm.window import SOURCE_CAP, read_window, reasoning_capability_of

BASE = "http://lm.test:1234/v1"
MODEL = "google/gemma-4-26b-a4b-qat"


def _server(routes: dict[str, Any]) -> tuple[httpx.AsyncClient, list[str]]:
    """Antwortet je Pfad mit dem hinterlegten JSON (oder Status); alles andere
    ist 404 - wie ein Server, der diesen Weg nicht kennt."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        answer = routes.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"error": "unbekannter Pfad"})
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, json=answer)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle)), asked


async def _read(routes: dict[str, Any], cap: int | None = None, model: str = MODEL) -> Any:
    client, _asked = _server(routes)
    async with client:
        return await read_window(BASE, model, cap=cap, client=client)


def _v1(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"models": list(entries)}


def _v1_entry(
    instances: list[dict[str, Any]],
    *,
    key: str = MODEL,
    reasoning: Any = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": "llm",
        "key": key,
        "max_context_length": 262144,
        "loaded_instances": instances,
        "capabilities": {},
    }
    if reasoning is not None:
        entry["capabilities"]["reasoning"] = reasoning
    return entry


async def test_lm_studio_v1_reports_the_loaded_instance_window() -> None:
    window = await _read(
        {
            "/api/v1/models": _v1(
                _v1_entry(
                    [{"id": MODEL, "config": {"context_length": 8192, "parallel": 4}}],
                    reasoning={"allowed_options": ["off", "on"], "default": "on"},
                )
            )
        }
    )

    assert window.loaded == 8192
    assert window.effective == 8192
    assert window.source == "/api/v1/models"
    assert window.parallel == 4
    # Nur zur Information - nie das Fenster.
    assert window.max_context == 262144
    assert window.reasoning_capability.kind == "switchable"
    assert window.reasoning_capability.default == "on"
    assert window.model_loaded is True


async def test_v1_addresses_the_instance_by_id_and_otherwise_takes_the_smallest() -> None:
    instances = [
        {"id": f"{MODEL}:1", "config": {"context_length": 32768}},
        {"id": f"{MODEL}:2", "config": {"context_length": 8192}},
    ]
    routes = {"/api/v1/models": _v1(_v1_entry(instances))}

    first = await _read(routes, model=f"{MODEL}:1")
    second = await _read(routes, model=f"{MODEL}:2")
    by_key = await _read(routes, model=MODEL)

    assert first.loaded == 32768
    assert second.loaded == 8192
    # Mehrere Instanzen, adressiert nur ueber den Schluessel: welche LM Studio
    # nimmt, ist offen - also das kleinste Fenster, mit Hinweis.
    assert by_key.loaded == 8192
    assert "Instanzen" in (by_key.reason or "")


async def test_v1_without_loaded_instance_means_no_window_even_if_v0_has_numbers() -> None:
    """LM Studio v1 ist massgeblich: ein nicht geladenes Modell hat kein
    Fenster - max_context_length wird NIE als Fenster genommen."""
    window = await _read(
        {
            "/api/v1/models": _v1(_v1_entry([])),
            "/api/v0/models": {
                "data": [
                    {
                        "id": MODEL,
                        "state": "loaded",
                        "max_context_length": 262144,
                        "loaded_context_length": 8192,
                    }
                ]
            },
        }
    )

    assert window.loaded is None
    assert window.effective is None
    assert not window.known
    assert window.model_loaded is False
    assert "nicht geladen" in (window.reason or "")


async def test_v0_counts_only_when_the_model_is_loaded() -> None:
    loaded = await _read(
        {
            "/api/v0/models": {
                "data": [
                    {
                        "id": MODEL,
                        "type": "llm",
                        "state": "loaded",
                        "max_context_length": 262144,
                        "loaded_context_length": 16384,
                    }
                ]
            }
        }
    )
    not_loaded = await _read(
        {
            "/api/v0/models": {
                "data": [
                    {
                        "id": MODEL,
                        "type": "llm",
                        "state": "not-loaded",
                        "max_context_length": 262144,
                        # Aeltere Fassungen lassen das Feld stehen - es gilt nicht.
                        "loaded_context_length": 16384,
                    }
                ]
            },
            # Auch eine OpenAI-Liste mit Zahl rettet das nicht: v0 hat
            # ausdruecklich "nicht geladen" gesagt.
            "/v1/models": {"data": [{"id": MODEL, "context_length": 4096}]},
        }
    )

    assert loaded.effective == 16384
    assert loaded.source == "/api/v0/models"
    assert loaded.reasoning_capability.kind == "unknown"
    assert not not_loaded.known
    assert not_loaded.max_context == 262144


async def test_ollama_reports_the_running_models_window() -> None:
    window = await _read(
        {
            "/api/ps": {
                "models": [{"name": "qwen3:8b", "model": "qwen3:8b", "context_length": 40960}]
            }
        },
        model="qwen3:8b",
    )
    missing = await _read({"/api/ps": {"models": []}}, model="qwen3:8b")

    assert window.effective == 40960
    assert window.source == "Ollama /api/ps"
    assert not missing.known
    assert "ollama run qwen3:8b" in (missing.reason or "")


async def test_openai_compatible_fields_in_their_order() -> None:
    vllm = await _read({"/v1/models": {"data": [{"id": MODEL, "max_model_len": 32768}]}})
    other = await _read({"/v1/models": {"data": [{"id": MODEL, "context_window": 65536}]}})
    nothing = await _read(
        {"/v1/models": {"data": [{"id": MODEL, "max_context_length": 262144}]}}
    )

    assert vllm.effective == 32768
    assert vllm.source == "/models (max_model_len)"
    assert other.effective == 65536
    assert other.source == "/models (context_window)"
    # max_context_length ist auch hier kein Fenster.
    assert not nothing.known
    assert "kein Kontextfenster" in (nothing.reason or "")


async def test_the_cap_limits_the_loaded_window() -> None:
    routes = {
        "/api/v1/models": _v1(_v1_entry([{"id": MODEL, "config": {"context_length": 32768}}]))
    }

    capped = await _read(routes, cap=16384)
    above = await _read(routes, cap=65536)

    assert (capped.loaded, capped.cap, capped.effective) == (32768, 16384, 16384)
    assert (above.loaded, above.cap, above.effective) == (32768, 65536, 32768)
    assert capped.as_dict() == {
        "loaded": 32768,
        "cap": 16384,
        "effective": 16384,
        "source": "/api/v1/models",
    }


async def test_the_cap_stands_in_when_the_server_reports_no_window() -> None:
    window = await _read({"/v1/models": {"data": [{"id": MODEL}]}}, cap=8192)

    assert window.effective == 8192
    assert window.loaded is None
    assert window.source == SOURCE_CAP
    assert "Kontext höchstens" in (window.reason or "")


async def test_an_unreachable_server_gives_an_unknown_window_with_reasons() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("verweigert", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        window = await read_window(BASE, MODEL, cap=None, client=client)

    assert not window.known
    assert window.reason is not None
    assert "ConnectError" in window.reason


async def test_readers_are_asked_in_order_and_stop_at_the_first_window() -> None:
    client, asked = _server(
        {
            "/api/v0/models": {
                "data": [{"id": MODEL, "state": "loaded", "loaded_context_length": 4096}]
            },
            "/api/ps": {"models": [{"name": MODEL, "context_length": 999999}]},
        }
    )
    async with client:
        window = await read_window(BASE, MODEL, client=client)

    assert window.effective == 4096
    assert asked == ["/api/v1/models", "/api/v0/models"]


def test_reasoning_capability_forms() -> None:
    reader: Callable[[Any], Any] = reasoning_capability_of
    assert reader(None).kind == "none"
    assert reader({"allowed_options": ["off", "on"]}).kind == "switchable"
    assert reader({"allowed_options": ["low", "medium", "high"]}).kind == "always"
    assert reader({"allowed_options": "kaputt"}).kind == "unknown"
    assert reader("seltsam").kind == "unknown"
