"""Modellwahl, Einstellungen und Kontextfenster.

Der Kern: bis hierher hat die Anwendung `models[0]` genommen. Diese Tests
halten fest, was stattdessen gilt - und decken vor allem den Fall ab, der
frueher stillschweigend kaputtging: ein Einbettungsmodell an erster Stelle.
Seit dem relativen KI-Budget gibt es kein festes Ausgabebudget mehr; der
Statusendpunkt meldet stattdessen das geladene Fenster, die Antwortreserve
und die Messung (Einstellungen mit Schemavorgaben: test_ai_settings_budget.py).

Die Modelliste ist gemockt (kein laufendes LM Studio); Einstellungen und ihre
Herkunft laufen echt gegen die Datenbank.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.llm import reasons
from app.main import app
from app.schemas.ai import AIModelInfo
from app.services import ai_discovery, ai_settings_service
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)


def _household_headers(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-6")
    household = client.post(
        "/v1/households",
        json={"name": "KI-Einstellungs-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def headers() -> dict[str, str]:
    return _household_headers("ais")


def _model(
    model_id: str,
    *,
    kind: str = "llm",
    state: str = "loaded",
    max_context: int | None = 262144,
    loaded_context: int | None = 8192,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": model_id, "type": kind, "state": state}
    if max_context is not None:
        entry["max_context_length"] = max_context
    if loaded_context is not None and state == "loaded":
        entry["loaded_context_length"] = loaded_context
    return entry


def _models_response(*entries: dict[str, Any]) -> MagicMock:
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": list(entries)}
    return response


# ---------------------------------------------------------------------------
# Modellwahl
# ---------------------------------------------------------------------------


def _infos(*entries: dict[str, Any]) -> list[AIModelInfo]:
    return [ai_discovery.parse_detailed_model(entry) for entry in entries]


def test_an_embedding_model_is_never_chosen() -> None:
    """Der Fehler, den die alte `models[0]`-Logik jederzeit machen konnte: ein
    Einbettungsmodell beherrscht keine Chat-Completion."""
    models = _infos(
        _model("text-embedding-nomic-embed-text-v1.5", kind="embeddings"),
        _model("qwen/qwen3-8b"),
    )
    selected = ai_discovery.select_model(models, None)
    assert selected is not None
    assert selected.id == "qwen/qwen3-8b"


def test_a_loaded_model_beats_a_merely_installed_one() -> None:
    """Ein nicht geladenes Modell muesste LM Studio erst in den Speicher
    holen - das kostet beim ersten Aufruf das Zeitbudget."""
    models = _infos(
        _model("a/gross", state="not-loaded", loaded_context=None),
        _model("b/klein", state="loaded"),
    )
    selected = ai_discovery.select_model(models, None)
    assert selected is not None
    assert selected.id == "b/klein"


def test_an_explicit_choice_wins_over_the_loaded_model() -> None:
    models = _infos(
        _model("a/gross", state="not-loaded", loaded_context=None),
        _model("b/klein", state="loaded"),
    )
    selected = ai_discovery.select_model(models, "a/gross")
    assert selected is not None
    assert selected.id == "a/gross"


def test_a_missing_chosen_model_is_not_silently_replaced() -> None:
    """Sonst traegt die Auswertung einen Modellnamen, der nie gerechnet hat."""
    models = _infos(_model("b/klein"))
    assert ai_discovery.select_model(models, "a/verschwunden") is None


def test_an_unknown_model_type_stays_selectable() -> None:
    """Ausgeschlossen wird nur, was nachweislich nicht chatten kann."""
    models = _infos({"id": "irgendein/modell", "state": "loaded"})
    selected = ai_discovery.select_model(models, None)
    assert selected is not None
    assert selected.id == "irgendein/modell"


def test_a_not_loaded_model_has_no_loaded_window() -> None:
    """Befund: das Fenster fiel frueher auf max_context_length zurueck und
    ueberschaetzte es bei nicht geladenen Modellen. Ein Fenster, das niemand
    geladen hat, ist keins - auch wenn das Feld in der Antwort steht."""
    loaded = _infos(_model("a", max_context=262144, loaded_context=8192))[0]
    stale = ai_discovery.parse_detailed_model(
        {
            "id": "b",
            "type": "llm",
            "state": "not-loaded",
            "max_context_length": 262144,
            "loaded_context_length": 8192,
        }
    )
    assert loaded.loaded_context_tokens == 8192
    assert stale.loaded_context_tokens is None
    assert stale.max_context_tokens == 262144
    assert not hasattr(ai_discovery, "context_tokens_of")


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


def test_defaults_come_from_the_schema_and_say_so(headers: dict[str, str]) -> None:
    response = client.get("/v1/ai/settings", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_name"] is None
    # Ohne Wahl bestimmt die Discovery das Modell.
    assert body["model_source"] == "automatic"
    assert body["base_url_source"] == "environment"
    assert body["sampling_mode"] == "model"
    assert body["sampling_mode_source"] == "automatic"
    assert body["request_timeout_seconds_source"] == "default"
    assert body["schema_defaults"]["answer_reserve_share"] == 0.25


def test_a_saved_setting_is_marked_as_the_households_own(headers: dict[str, str]) -> None:
    saved = client.put(
        "/v1/ai/settings",
        json={"model_name": "qwen/qwen3-8b", "context_cap_tokens": 16384},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["model_name"] == "qwen/qwen3-8b"
    assert body["model_source"] == "household"
    assert body["context_cap_tokens"] == 16384

    # Und sie ueberlebt den naechsten Read.
    assert client.get("/v1/ai/settings", headers=headers).json()["context_cap_tokens"] == 16384


def test_clearing_a_field_returns_to_its_default(headers: dict[str, str]) -> None:
    """`null` allein hiesse "nicht angefasst" - Zuruecksetzen braucht deshalb
    eine eigene Angabe."""
    client.put("/v1/ai/settings", json={"model_name": "qwen/qwen3-8b"}, headers=headers)
    cleared = client.put(
        "/v1/ai/settings", json={"clear_fields": ["model_name"]}, headers=headers
    ).json()
    assert cleared["model_name"] is None
    assert cleared["model_source"] == "automatic"


def test_clearing_wins_over_a_value_sent_in_the_same_request(headers: dict[str, str]) -> None:
    """Sonst haenge das Ergebnis an der Reihenfolge im JSON."""
    result = client.put(
        "/v1/ai/settings",
        json={"model_name": "qwen/qwen3-8b", "clear_fields": ["model_name"]},
        headers=headers,
    ).json()
    assert result["model_source"] == "automatic"


def test_out_of_range_values_are_rejected(headers: dict[str, str]) -> None:
    assert (
        client.put("/v1/ai/settings", json={"temperature": 5}, headers=headers).status_code == 422
    )
    assert client.put("/v1/ai/settings", json={"top_p": 0}, headers=headers).status_code == 422
    assert client.put("/v1/ai/settings", json={"top_k": 1001}, headers=headers).status_code == 422
    assert (
        client.put("/v1/ai/settings", json={"repeat_penalty": 3}, headers=headers).status_code
        == 422
    )
    assert client.put("/v1/ai/settings", json={"min_p": 2}, headers=headers).status_code == 422
    assert (
        client.put("/v1/ai/settings", json={"presence_penalty": -3}, headers=headers).status_code
        == 422
    )
    assert (
        client.put(
            "/v1/ai/settings", json={"answer_reserve_share": 0.99}, headers=headers
        ).status_code
        == 422
    )


def test_viewers_may_read_but_not_change_ai_settings() -> None:
    owner = _household_headers("aisowner")
    viewer = _invite_member(client, owner, owner["X-Household-Id"], "viewer", "aisviewer")

    assert client.get("/v1/ai/settings", headers=viewer).status_code == 200
    assert (
        client.put("/v1/ai/settings", json={"model_name": "x/y"}, headers=viewer).status_code
        == 403
    )


def test_the_custom_base_url_is_tried_first(headers: dict[str, str]) -> None:
    """`custom_base_url` gab es seit Migration 0001, gelesen hat sie niemand."""
    client.put("/v1/ai/settings", json={"base_url": "http://mein-server:9999/v1"}, headers=headers)

    settings_view = client.get("/v1/ai/settings", headers=headers).json()
    assert settings_view["base_url"] == "http://mein-server:9999/v1"
    assert settings_view["base_url_source"] == "household"

    resolved = ai_settings_service.ResolvedAISettings(
        is_enabled=True,
        min_confidence=0.85,
        custom_base_url="http://mein-server:9999/v1",
        model_name=None,
        temperature=0.1,
        request_timeout_seconds=120.0,
    )
    assert resolved.candidate_urls()[0] == "http://mein-server:9999/v1"


# ---------------------------------------------------------------------------
# Fenster, Reserve und Messung im Statusendpunkt
# ---------------------------------------------------------------------------


def test_health_reports_window_reserve_and_measurement(headers: dict[str, str]) -> None:
    with patch(
        "httpx.AsyncClient.get",
        AsyncMock(return_value=_models_response(_model("a/klein", loaded_context=8192))),
    ):
        with patch("httpx.AsyncClient.post", AsyncMock(side_effect=OSError("kein Chat"))):
            body = client.get("/v1/ai/health", headers=headers).json()

    assert body["context_tokens"] == 8192
    assert body["window"]["loaded"] == 8192
    assert body["window"]["effective"] == 8192
    assert body["window"]["source"] == "/api/v0/models"
    # Antwortreserve = Anteil des wirksamen Fensters (Vorgabe 25 % im Schema).
    assert body["reserve_tokens"] == 2048
    assert body["measurement"]["is_start_value"] is True
    assert body["measurement"]["chars_per_token"] == 3.0
    assert "max_output_tokens" not in body
    assert "prompt_budget_tokens" not in body


def test_health_applies_the_context_cap(headers: dict[str, str]) -> None:
    client.put("/v1/ai/settings", json={"context_cap_tokens": 4096}, headers=headers)
    with patch(
        "httpx.AsyncClient.get",
        AsyncMock(return_value=_models_response(_model("a/klein", loaded_context=8192))),
    ):
        with patch("httpx.AsyncClient.post", AsyncMock(side_effect=OSError("kein Chat"))):
            body = client.get("/v1/ai/health", headers=headers).json()

    assert body["window"]["loaded"] == 8192
    assert body["window"]["cap"] == 4096
    assert body["window"]["effective"] == 4096
    assert body["reserve_tokens"] == 1024


def test_health_warns_when_the_chosen_model_is_not_loaded(headers: dict[str, str]) -> None:
    client.put("/v1/ai/settings", json={"model_name": "a/gross"}, headers=headers)
    with patch(
        "httpx.AsyncClient.get",
        AsyncMock(
            return_value=_models_response(
                _model("a/gross", state="not-loaded", loaded_context=None)
            )
        ),
    ):
        with patch("httpx.AsyncClient.post", AsyncMock(side_effect=OSError("kein Chat"))):
            body = client.get("/v1/ai/health", headers=headers).json()

    assert body["model_state"] == "not-loaded"
    codes = [warning["code"] for warning in body["warnings"]]
    assert reasons.MODEL_NOT_LOADED in codes
    # Ohne geladenes Fenster laesst sich kein Budget rechnen - benannt, nicht geraten.
    assert reasons.MODEL_CONTEXT_UNKNOWN in codes
    assert body["context_tokens"] is None
    assert body["canary_passed"] is False
    assert all(warning["message"] for warning in body["warnings"])


def test_health_names_a_missing_chosen_model_instead_of_using_another(
    headers: dict[str, str],
) -> None:
    client.put("/v1/ai/settings", json={"model_name": "a/verschwunden"}, headers=headers)
    with patch(
        "httpx.AsyncClient.get",
        AsyncMock(return_value=_models_response(_model("b/vorhanden"))),
    ):
        body = client.get("/v1/ai/health", headers=headers).json()

    assert body["status"] == "model_missing"
    assert body["active_model"] is None
    assert "a/verschwunden" in body["message"]


def test_the_model_catalog_marks_embeddings_as_unusable(headers: dict[str, str]) -> None:
    with patch(
        "httpx.AsyncClient.get",
        AsyncMock(
            return_value=_models_response(
                _model("text-embedding-nomic", kind="embeddings"),
                _model("qwen/qwen3-8b"),
            )
        ),
    ):
        body = client.get("/v1/ai/models", headers=headers).json()

    assert body["is_connected"] is True
    assert body["supports_detail_api"] is True
    by_id = {model["id"]: model for model in body["models"]}
    assert by_id["text-embedding-nomic"]["is_chat_capable"] is False
    assert by_id["qwen/qwen3-8b"]["is_chat_capable"] is True
    assert by_id["qwen/qwen3-8b"]["loaded_context_tokens"] == 8192
