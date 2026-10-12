"""Anbieterwahl und API-Schluessel ueber die API - gegen die echte Datenbank.

Was hier festgehalten wird:
* Der Schluessel geht nur hinein: keine Antwort, kein Datenbankfeld enthaelt
  ihn im Klartext; gezeigt wird "hinterlegt (...abcd)".
* Hinterlegen, Entfernen und den Anbieter wechseln darf nur der Owner -
  Editoren speichern ihre uebrigen Einstellungen weiter, Betrachter sehen den
  Stand.
* Ein nicht lesbarer Schluessel wird benannt, nicht verschwiegen.
* Die Modellliste von Anthropic ist gemockt; es geht kein Aufruf hinaus.
"""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm import anthropic_api, keys, providers, reasons
from app.llm.settings import LLMSettings
from app.main import app
from app.services import ai_settings_service
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)

_DSN = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
KEY = "sk-ant-api03-test-key-0123456789-ABCD"
TARGET = providers.target_of("https://api.anthropic.com")


def _household(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-6")
    household = client.post(
        "/v1/households", json={"name": "Anbieter-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def owner() -> dict[str, str]:
    return _household("aiprov")


def _ciphertext(household_id: str) -> str | None:
    with psycopg.connect(_DSN) as conn:
        row = conn.execute(
            "select ciphertext from ai_provider_keys where household_id = %s and target = %s",
            (household_id, TARGET),
        ).fetchone()
    return str(row[0]) if row else None


def test_owner_hinterlegt_schluessel_der_nie_zurueckkommt(owner: dict[str, str]) -> None:
    stored = client.put(
        "/v1/ai/provider-keys/anthropic", json={"api_key": f"  {KEY}\n"}, headers=owner
    )
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["stored"] is True
    assert body["ending"] == "…ABCD"
    assert body["target"] == "https://api.anthropic.com"
    assert KEY not in stored.text

    view = client.get("/v1/ai/settings", headers=owner)
    assert KEY not in view.text
    status = next(item for item in view.json()["provider_keys"] if item["provider"] == "anthropic")
    assert status["stored"] is True and status["problem"] is None

    blob = _ciphertext(owner["X-Household-Id"])
    assert blob is not None and KEY not in blob

    removed = client.delete("/v1/ai/provider-keys/anthropic", headers=owner)
    assert removed.status_code == 200
    assert removed.json()["stored"] is False
    assert _ciphertext(owner["X-Household-Id"]) is None


def test_unbrauchbarer_schluessel_wird_abgewiesen(owner: dict[str, str]) -> None:
    response = client.put(
        "/v1/ai/provider-keys/anthropic", json={"api_key": "sk ant mit leerzeichen"}, headers=owner
    )
    assert response.status_code == 400
    assert "Leer" in response.json()["detail"]
    assert client.put(
        "/v1/ai/provider-keys/local", json={"api_key": KEY}, headers=owner
    ).status_code == 400


def test_nur_der_owner_wechselt_anbieter_und_schluessel(owner: dict[str, str]) -> None:
    editor = _invite_member(client, owner, owner["X-Household-Id"], "editor", "aiproved")
    viewer = _invite_member(client, owner, owner["X-Household-Id"], "viewer", "aiprovvw")

    assert client.put(
        "/v1/ai/provider-keys/anthropic", json={"api_key": KEY}, headers=editor
    ).status_code == 403
    assert client.put(
        "/v1/ai/settings", json={"provider": "anthropic"}, headers=editor
    ).status_code == 403
    # Den unveraenderten Anbieter mitzuschicken ist kein Wechsel.
    assert client.put(
        "/v1/ai/settings", json={"provider": "local", "model_name": "x/y"}, headers=editor
    ).status_code == 200

    assert client.put(
        "/v1/ai/provider-keys/anthropic", json={"api_key": KEY}, headers=owner
    ).status_code == 200
    changed = client.put("/v1/ai/settings", json={"provider": "anthropic"}, headers=owner)
    assert changed.status_code == 200
    assert changed.json()["provider"] == "anthropic"

    seen = client.get("/v1/ai/settings", headers=viewer).json()
    assert seen["provider"] == "anthropic"
    status = next(item for item in seen["provider_keys"] if item["provider"] == "anthropic")
    assert status["stored"] is True and status["ending"] == "…ABCD"
    assert client.delete("/v1/ai/provider-keys/anthropic", headers=viewer).status_code == 403


async def _resolved(household_id: str) -> LLMSettings:
    from app.core.db import get_session_factory

    async with get_session_factory()() as session:
        return await ai_settings_service.resolve(session, uuid.UUID(household_id))


async def test_einstellungen_tragen_den_entschluesselten_schluessel(owner: dict[str, str]) -> None:
    client.put("/v1/ai/provider-keys/anthropic", json={"api_key": KEY}, headers=owner)
    client.put("/v1/ai/settings", json={"provider": "anthropic"}, headers=owner)

    resolved = await _resolved(owner["X-Household-Id"])
    assert resolved.provider == providers.ANTHROPIC
    assert resolved.api_key is not None and resolved.api_key.reveal() == KEY
    assert resolved.candidate_urls() == ["https://api.anthropic.com"]


async def test_nicht_lesbarer_schluessel_wird_benannt(owner: dict[str, str]) -> None:
    client.put("/v1/ai/provider-keys/anthropic", json={"api_key": KEY}, headers=owner)
    client.put("/v1/ai/settings", json={"provider": "anthropic"}, headers=owner)
    # Geheimtext eines anderen Haushalts: AES-GCM bindet den Haushalt ein.
    foreign = keys.encrypt(uuid.uuid4(), TARGET, KEY)
    with psycopg.connect(_DSN) as conn:
        conn.execute(
            "update ai_provider_keys set ciphertext = %s where household_id = %s",
            (foreign, owner["X-Household-Id"]),
        )

    resolved = await _resolved(owner["X-Household-Id"])
    assert resolved.api_key is None
    assert resolved.key_problem and "neu hinterlegen" in resolved.key_problem

    status = next(
        item
        for item in client.get("/v1/ai/settings", headers=owner).json()["provider_keys"]
        if item["provider"] == "anthropic"
    )
    assert status["stored"] is True and status["problem"]

    health = client.get("/v1/ai/health", headers=owner).json()
    assert health["provider"] == "anthropic"
    assert health["is_connected"] is False
    assert reasons.MODEL_KEY_MISSING in {warning["code"] for warning in health["warnings"]}


def test_claude_ohne_modellwahl_und_ohne_laden(
    owner: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def listing(settings: LLMSettings, base_url: str) -> list[Any]:
        assert settings.api_key is not None
        return [
            anthropic_api.model_from_api(
                {"id": "claude-test", "display_name": "Claude Test", "max_input_tokens": 200_000,
                 "max_tokens": 64_000, "capabilities": {}}
            )
        ]

    monkeypatch.setattr(anthropic_api, "list_models", listing)
    client.put("/v1/ai/provider-keys/anthropic", json={"api_key": KEY}, headers=owner)
    client.put("/v1/ai/settings", json={"provider": "anthropic"}, headers=owner)

    catalog = client.get("/v1/ai/models", headers=owner).json()
    assert catalog["provider"] == "anthropic"
    assert [model["id"] for model in catalog["models"]] == ["claude-test"]
    assert catalog["models"][0]["output_limit_tokens"] == 64_000

    health = client.get("/v1/ai/health", headers=owner).json()
    assert reasons.MODEL_NOT_CHOSEN in {warning["code"] for warning in health["warnings"]}

    runtime = client.get("/v1/ai/runtime", headers=owner).json()
    assert runtime["provider"] == "anthropic"
    assert runtime["gpus"] == []

    client.put("/v1/ai/settings", json={"model_name": "claude-test"}, headers=owner)
    assert client.post("/v1/ai/models/load", headers=owner).status_code == 400
    runtime = client.get("/v1/ai/runtime", headers=owner).json()
    assert runtime["active_model"]["key"] == "claude-test"
    assert runtime["window"]["effective"] == 200_000
