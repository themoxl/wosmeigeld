"""Laufzeitanzeige der KI-Warteschlange (GET /v1/ai/runtime, gpu_probe).

Waehrend eines Laufs stand in der Oberflaeche nur "Laeuft" samt Zaehler. Womit
gerechnet wurde - Modell, Kontextfenster, Reasoning, GPU - war nirgends
sichtbar; das Modell erschien erst nachtraeglich am fertigen Ergebnis. Diese
Tests halten fest, dass die Auskunft vollstaendig ist UND dass sie fehlende
Messwerte als solche benennt, statt sie zu erfinden.
"""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_runtime, gpu_probe
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

_LOADED = ai_runtime.RuntimeModel(
    key="google/gemma-4-26b-a4b-qat",
    kind="llm",
    max_context_length=262144,
    instances=[ai_runtime.LoadedInstance(instance_id="gemma", context_length=8192)],
    reasoning_options=["off", "on"],
    display_name="Gemma 4 26B A4B QAT",
    architecture="gemma4",
    quantization="Q4_0",
    parameters="26B-A4B",
    size_bytes=15634263816,
)
_EMBEDDINGS = ai_runtime.RuntimeModel(
    key="text-embedding-nomic-embed-text-v1.5",
    kind="embeddings",
    max_context_length=2048,
    instances=[],
    reasoning_options=[],
)

_GPU = gpu_probe.GpuStatus(
    gpus=[
        gpu_probe.GpuReading(
            name="NVIDIA GeForce RTX 5090",
            memory_used_mb=17245,
            memory_total_mb=32607,
            utilization_percent=12,
            temperature_celsius=35,
        )
    ],
    detail=None,
)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"runtime-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Laufzeit-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _resolved(model_name: str | None, cap: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        model_name=model_name,
        reasoning="off",
        reasoning_effort=lambda: "none",
        candidate_urls=lambda: ["http://127.0.0.1:1234/v1"],
        context_cap_tokens=cap,
    )


def _runtime(
    household_headers: dict[str, str],
    resolved: SimpleNamespace,
    models: list[ai_runtime.RuntimeModel],
) -> dict[str, Any]:
    with (
        patch(
            "app.api.v1.routes.ai.ai_settings_service.resolve",
            AsyncMock(return_value=resolved),
        ),
        patch("app.api.v1.routes.ai.ai_runtime.list_models", AsyncMock(return_value=models)),
        patch("app.api.v1.routes.ai.discover", AsyncMock(return_value=(None, [], False))),
        patch("app.api.v1.routes.ai.gpu_probe.probe", AsyncMock(return_value=_GPU)),
    ):
        response = client.get("/v1/ai/runtime", headers=household_headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def test_runtime_names_model_context_and_gpu(household_headers: dict[str, str]) -> None:
    with (
        patch(
            "app.api.v1.routes.ai.ai_settings_service.resolve",
            AsyncMock(return_value=_resolved("google/gemma-4-26b-a4b-qat")),
        ),
        patch(
            "app.api.v1.routes.ai.ai_runtime.list_models",
            AsyncMock(return_value=[_LOADED, _EMBEDDINGS]),
        ),
        patch("app.api.v1.routes.ai.gpu_probe.probe", AsyncMock(return_value=_GPU)),
    ):
        response = client.get("/v1/ai/runtime", headers=household_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["is_connected"] is True
    assert data["active_model"]["display_name"] == "Gemma 4 26B A4B QAT"
    # Das GELADENE Fenster, nicht das maximal moegliche - genau daran haengt,
    # wie viel Kontext ein Lauf tatsaechlich hat.
    assert data["active_model"]["loaded_context_tokens"] == 8192
    assert data["active_model"]["max_context_tokens"] == 262144
    assert data["reasoning_effort"] == "none"
    # Einbettungsmodelle zaehlen nicht als Klassifikationsmodell.
    assert data["chat_models_count"] == 1
    assert data["gpus"][0]["name"] == "NVIDIA GeForce RTX 5090"


def test_runtime_flags_a_loaded_model_that_is_not_the_configured_one(
    household_headers: dict[str, str],
) -> None:
    """Ein anderes geladenes Modell ist keine Antwort auf "womit wird
    gearbeitet" - der naechste Lauf laedt das eingestellte nach."""
    with (
        patch(
            "app.api.v1.routes.ai.ai_settings_service.resolve",
            AsyncMock(return_value=_resolved("qwen/qwen3.6-35b-a3b")),
        ),
        patch(
            "app.api.v1.routes.ai.ai_runtime.list_models", AsyncMock(return_value=[_LOADED])
        ),
        patch("app.api.v1.routes.ai.gpu_probe.probe", AsyncMock(return_value=_GPU)),
    ):
        data = client.get("/v1/ai/runtime", headers=household_headers).json()

    assert data["active_model"] is None
    assert "qwen/qwen3.6-35b-a3b" in data["message"]
    assert data["loaded_count"] == 1


def test_runtime_separates_an_unreachable_server_from_a_missing_admin_api(
    household_headers: dict[str, str],
) -> None:
    with (
        patch(
            "app.api.v1.routes.ai.ai_settings_service.resolve",
            AsyncMock(return_value=_resolved(None)),
        ),
        patch("app.api.v1.routes.ai.ai_runtime.list_models", AsyncMock(return_value=[])),
        patch(
            "app.api.v1.routes.ai.discover", AsyncMock(return_value=(None, [], False))
        ),
        patch(
            "app.api.v1.routes.ai.gpu_probe.probe",
            AsyncMock(return_value=gpu_probe.GpuStatus(gpus=[], detail="kein nvidia-smi")),
        ),
    ):
        offline = client.get("/v1/ai/runtime", headers=household_headers).json()

    assert offline["is_connected"] is False
    assert offline["message"] == "LM Studio ist nicht erreichbar."
    # Fehlende GPU-Werte werden begruendet, nicht als 0 dargestellt.
    assert offline["gpus"] == []
    assert offline["gpu_detail"] == "kein nvidia-smi"

    with (
        patch(
            "app.api.v1.routes.ai.ai_settings_service.resolve",
            AsyncMock(return_value=_resolved(None)),
        ),
        patch("app.api.v1.routes.ai.ai_runtime.list_models", AsyncMock(return_value=[])),
        patch(
            "app.api.v1.routes.ai.discover",
            AsyncMock(return_value=("http://127.0.0.1:1234/v1", [], True)),
        ),
        patch(
            "app.api.v1.routes.ai.gpu_probe.probe",
            AsyncMock(return_value=gpu_probe.GpuStatus(gpus=[], detail=None)),
        ),
    ):
        old_version = client.get("/v1/ai/runtime", headers=household_headers).json()

    assert old_version["is_connected"] is True
    assert "/api/v1/models" in old_version["message"]


def test_runtime_reports_the_effective_window_like_the_core(
    household_headers: dict[str, str],
) -> None:
    """W = min(geladenes Fenster, "Kontext hoechstens") - dieselbe Rechnung
    wie der KI-Kern, samt Quelle. Ein Deckel ueber dem geladenen Fenster wirkt
    nicht; ohne geladenes Modell gilt der Deckel, ohne beides ist W unbekannt
    und der Grund steht dabei."""
    gemma = "google/gemma-4-26b-a4b-qat"

    loaded = _runtime(household_headers, _resolved(gemma), [_LOADED])
    assert loaded["window"]["loaded"] == 8192
    assert loaded["window"]["cap"] is None
    assert loaded["window"]["effective"] == 8192
    assert loaded["window"]["source"] == "/api/v1/models"
    assert loaded["window"]["max_context"] == 262144

    capped = _runtime(household_headers, _resolved(gemma, cap=4096), [_LOADED])
    assert (capped["window"]["effective"], capped["window"]["cap"]) == (4096, 4096)
    assert capped["window"]["source"] == "/api/v1/models"

    above = _runtime(household_headers, _resolved(gemma, cap=16384), [_LOADED])
    assert above["window"]["effective"] == 8192

    not_loaded = _runtime(household_headers, _resolved("qwen/qwen3", cap=16384), [_LOADED])
    assert not_loaded["window"]["loaded"] is None
    assert not_loaded["window"]["effective"] == 16384
    assert not_loaded["window"]["source"] == "Kontext höchstens (Einstellung)"
    assert "qwen/qwen3" in not_loaded["window"]["reason"]

    unknown = _runtime(household_headers, _resolved("qwen/qwen3"), [_LOADED])
    assert unknown["window"]["effective"] is None
    assert unknown["window"]["source"] is None
    assert "nicht geladen" in unknown["window"]["reason"]

    offline = _runtime(household_headers, _resolved(gemma, cap=8192), [])
    assert offline["window"]["effective"] == 8192
    assert "nicht erreichbar" in offline["window"]["reason"]


def test_runtime_window_takes_the_smallest_of_several_instances(
    household_headers: dict[str, str],
) -> None:
    """Wie der Leser des Kerns: mehrere Instanzen ohne ausdrueckliche Adresse
    -> die kleinste, denn welche LM Studio waehlt, ist nicht festgelegt."""
    twice = ai_runtime.RuntimeModel(
        key="google/gemma-4-26b-a4b-qat",
        kind="llm",
        max_context_length=262144,
        instances=[
            ai_runtime.LoadedInstance(instance_id="gemma", context_length=16384),
            ai_runtime.LoadedInstance(instance_id="gemma:2", context_length=8192),
        ],
        reasoning_options=[],
    )
    data = _runtime(household_headers, _resolved("google/gemma-4-26b-a4b-qat"), [twice])
    assert data["window"]["effective"] == 8192


def test_gpu_probe_parses_nvidia_smi_and_skips_unreadable_rows() -> None:
    readings = gpu_probe._parse(
        "NVIDIA GeForce RTX 5090, 17245, 32607, 12, 35\n"
        "NVIDIA A100, [N/A], 40960, 0, [N/A]\n"
        "\n"
    )

    assert len(readings) == 1
    assert readings[0].name == "NVIDIA GeForce RTX 5090"
    assert readings[0].memory_used_mb == 17245
    assert readings[0].temperature_celsius == 35


async def test_gpu_probe_survives_an_event_loop_without_subprocess_support() -> None:
    """uvicorn schaltet unter Windows im Reload-Modus auf den Selector-Loop um,
    und der kennt keine Kindprozesse: `create_subprocess_exec` warf dort ein
    `NotImplementedError` ganz ohne Text - im Statusstreifen stand deshalb
    "nvidia-smi liess sich nicht starten: " mit leerer Begruendung, obwohl die
    GPU vorhanden war. Der Messweg laeuft seither synchron im Thread; bleibt
    eine Begruendung leer, tritt wenigstens die Fehlerklasse an ihre Stelle."""
    gpu_probe.reset_cache()
    with (
        patch("app.services.gpu_probe.shutil.which", return_value="nvidia-smi"),
        patch(
            "app.services.gpu_probe.subprocess.run", side_effect=NotImplementedError()
        ),
    ):
        status = await gpu_probe.probe()
    gpu_probe.reset_cache()

    assert status.detail == "nvidia-smi liess sich nicht starten: NotImplementedError"


async def test_gpu_probe_reads_values_through_a_plain_subprocess() -> None:
    """Der Messweg muss ohne asyncio-Kindprozess auskommen - genau daran
    scheiterte die Anzeige zuvor."""
    completed = SimpleNamespace(
        returncode=0,
        stdout=b"NVIDIA GeForce RTX 5090, 17245, 32607, 12, 35\n",
        stderr=b"",
    )
    gpu_probe.reset_cache()
    with (
        patch("app.services.gpu_probe.shutil.which", return_value="nvidia-smi"),
        patch("app.services.gpu_probe.subprocess.run", return_value=completed),
    ):
        status = await gpu_probe.probe()
    gpu_probe.reset_cache()

    assert status.detail is None
    assert status.gpus[0].memory_used_mb == 17245


async def test_gpu_probe_reports_the_reason_instead_of_zeros() -> None:
    """Laeuft die API im Container ohne durchgereichte GPU, gibt es keine
    Messwerte - obwohl LM Studio auf dem Host sehr wohl auf der GPU rechnet.
    Eine geratene Null waere schlechter als die Begruendung."""
    gpu_probe.reset_cache()
    with patch("app.services.gpu_probe.shutil.which", return_value=None):
        status = await gpu_probe.probe()
    gpu_probe.reset_cache()

    assert status.gpus == []
    assert status.detail is not None
    assert "nvidia-smi" in status.detail
