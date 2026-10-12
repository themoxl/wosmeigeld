"""Laufzeitsteuerung von LM Studio (app/services/ai_runtime.py).

Der Vertrag stammt aus Messungen gegen eine echte LM-Studio-Instanz
(03.08.2026); diese Tests halten ihn fest, damit ein Umbau nicht unbemerkt
gegen eine andere Antwortform laeuft.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import ai_runtime
from app.services.ai_runtime import LMStudioRuntimeError, ensure_loaded, parse_models

# Antwortform von GET /api/v1/models, gekuerzt auf die Felder, die gelesen
# werden. Genau so hat der echte Server geantwortet.
_MODELS_PAYLOAD: dict[str, Any] = {
    "models": [
        {
            "type": "llm",
            "key": "google/gemma-4-26b-a4b-qat",
            "max_context_length": 262144,
            "loaded_instances": [
                {"id": "google/gemma-4-26b-a4b-qat", "config": {"context_length": 8192}}
            ],
            "capabilities": {"reasoning": {"allowed_options": ["off", "on"], "default": "on"}},
        },
        {
            "type": "embedding",
            "key": "text-embedding-nomic-embed-text-v1.5",
            "max_context_length": 2048,
            "loaded_instances": [],
        },
    ]
}


def _response(status_code: int, payload: Any) -> MagicMock:
    response = MagicMock(status_code=status_code)
    response.json.return_value = payload
    return response


def test_parse_models_reads_instances_and_reasoning() -> None:
    models = parse_models(_MODELS_PAYLOAD)

    assert [model.key for model in models] == [
        "google/gemma-4-26b-a4b-qat",
        "text-embedding-nomic-embed-text-v1.5",
    ]
    gemma = models[0]
    assert gemma.is_loaded
    # Entscheidend fuer die Budgetrechnung: das GELADENE Fenster, nicht das
    # maximal moegliche.
    assert gemma.loaded_context_length() == 8192
    assert gemma.max_context_length == 262144
    assert gemma.reasoning_options == ["off", "on"]
    assert gemma.reasoning_default == "on"
    assert models[1].is_loaded is False


def test_parse_models_reads_the_descriptive_fields_of_the_loaded_model() -> None:
    """Womit gerechnet wird, steht in derselben Antwort: Anzeigename, Groesse,
    Quantisierung, Architektur. `quantization` kommt als Objekt - wer es als
    Zeichenkette liest, bekommt „[object Object]“ in die Oberflaeche."""
    models = parse_models(
        {
            "models": [
                {
                    "key": "google/gemma-4-26b-a4b-qat",
                    "display_name": "Gemma 4 26B A4B QAT",
                    "architecture": "gemma4",
                    "quantization": {"name": "Q4_0", "bits_per_weight": 4},
                    "params_string": "26B-A4B",
                    "size_bytes": 15634263816,
                }
            ]
        }
    )

    model = models[0]
    assert model.display_name == "Gemma 4 26B A4B QAT"
    assert model.architecture == "gemma4"
    assert model.quantization == "Q4_0"
    assert model.parameters == "26B-A4B"
    assert model.size_bytes == 15634263816


def test_parse_models_survives_missing_fields() -> None:
    """Eine aeltere LM-Studio-Version meldet weniger. Fehlendes bleibt None -
    geraten wird nichts, sonst stuende in der Oberflaeche eine Zahl, die
    niemand gemessen hat."""
    models = parse_models({"models": [{"key": "nur/name"}, {"ohne": "key"}]})

    assert len(models) == 1
    assert models[0].max_context_length is None
    assert models[0].reasoning_options == []
    assert models[0].reasoning_default is None
    assert models[0].loaded_context_length() is None


async def test_list_models_returns_empty_without_v1_api() -> None:
    """Alte Version ohne /api/v1 ist kein Fehlerfall: die Oberflaeche blendet
    dann Kontextfenster und Reasoning aus, statt eine Fehlermeldung zu zeigen."""
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=_response(404, {}))):
        assert await ai_runtime.list_models("http://localhost:1234/v1") == []


async def test_ensure_loaded_does_nothing_when_window_already_matches() -> None:
    """Laden kostet gemessen ueber 30 Sekunden - stimmt das Fenster bereits,
    darf kein Ladevorgang starten."""
    post = AsyncMock()
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_response(200, _MODELS_PAYLOAD))),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await ensure_loaded(
            "http://localhost:1234/v1", "google/gemma-4-26b-a4b-qat", 8192
        )

    assert result.changed is False
    assert result.context_length == 8192
    assert post.await_count == 0


async def test_ensure_loaded_unloads_before_loading() -> None:
    """LM Studio legt beim zweiten Laden eine ZWEITE Instanz an, statt die
    bestehende umzukonfigurieren - ohne vorheriges Entladen belegt dasselbe
    Modell den Speicher doppelt."""
    after = {
        "models": [
            {
                "type": "llm",
                "key": "google/gemma-4-26b-a4b-qat",
                "max_context_length": 262144,
                "loaded_instances": [
                    {
                        "id": "google/gemma-4-26b-a4b-qat:2",
                        "config": {"context_length": 32768},
                    }
                ],
            }
        ]
    }
    get = AsyncMock(
        side_effect=[_response(200, _MODELS_PAYLOAD), _response(200, after)]
    )
    post = AsyncMock(
        side_effect=[
            _response(200, {}),  # unload
            _response(
                200,
                {
                    "instance_id": "google/gemma-4-26b-a4b-qat:2",
                    "load_time_seconds": 38.7,
                    "status": "loaded",
                },
            ),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", get),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await ensure_loaded(
            "http://localhost:1234/v1", "google/gemma-4-26b-a4b-qat", 32768
        )

    assert result.changed is True
    assert result.context_length == 32768
    assert result.instance_id == "google/gemma-4-26b-a4b-qat:2"
    # Reihenfolge ist der Punkt dieses Tests.
    unload_call, load_call = post.await_args_list
    assert unload_call.args[0].endswith("/api/v1/models/unload")
    assert unload_call.kwargs["json"] == {"instance_id": "google/gemma-4-26b-a4b-qat"}
    assert load_call.args[0].endswith("/api/v1/models/load")
    assert load_call.kwargs["json"] == {
        "model": "google/gemma-4-26b-a4b-qat",
        "context_length": 32768,
    }


async def test_ensure_loaded_rejects_window_larger_than_the_model_supports() -> None:
    """Frueh und mit Zahlen ablehnen, statt LM Studio minutenlang laden zu
    lassen und dann an einer Speichermeldung zu scheitern."""
    with patch(
        "httpx.AsyncClient.get", AsyncMock(return_value=_response(200, _MODELS_PAYLOAD))
    ):
        with pytest.raises(LMStudioRuntimeError) as exc:
            await ensure_loaded(
                "http://localhost:1234/v1", "google/gemma-4-26b-a4b-qat", 999999
            )

    assert "262144" in str(exc.value)


async def test_ensure_loaded_names_the_available_models_when_one_is_missing() -> None:
    with patch(
        "httpx.AsyncClient.get", AsyncMock(return_value=_response(200, _MODELS_PAYLOAD))
    ):
        with pytest.raises(LMStudioRuntimeError) as exc:
            await ensure_loaded("http://localhost:1234/v1", "gibts/nicht", None)

    message = str(exc.value)
    assert "gibts/nicht" in message
    # Die Meldung soll weiterhelfen, nicht nur feststellen.
    assert "google/gemma-4-26b-a4b-qat" in message


async def test_load_failure_carries_the_servers_own_message() -> None:
    """LM Studios Grund ("nicht genug Speicher") ist fuer den Nutzer brauchbar -
    ein generisches "Laden fehlgeschlagen" waere es nicht."""
    get = AsyncMock(return_value=_response(200, _MODELS_PAYLOAD))
    post = AsyncMock(
        side_effect=[
            _response(200, {}),  # unload
            _response(500, {"error": {"message": "Not enough memory to load model"}}),
        ]
    )
    with patch("httpx.AsyncClient.get", get), patch("httpx.AsyncClient.post", post):
        with pytest.raises(LMStudioRuntimeError) as exc:
            await ensure_loaded(
                "http://localhost:1234/v1", "google/gemma-4-26b-a4b-qat", 262144
            )

    assert "Not enough memory" in str(exc.value)


def test_error_message_handles_both_error_shapes() -> None:
    """LM Studio antwortet mal {"error": "text"}, mal {"error": {"message": ...}}."""
    assert (
        ai_runtime._error_message(_response(400, {"error": "Unexpected endpoint"}))
        == "Unexpected endpoint"
    )
    assert (
        ai_runtime._error_message(_response(400, {"error": {"message": "Missing field"}}))
        == "Missing field"
    )
