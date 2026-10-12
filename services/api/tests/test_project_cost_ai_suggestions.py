"""KI-Kostenschaetzung fuer Vorhaben (Nachtrag zum Plan nach P0-P5, siehe
VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt "Nachtrag").

Ersetzt sind Discovery, Modellserver (tests/ki_attrappe.py) und die
SearXNG-Suche, alles andere laeuft echt: der Gateway des KI-Kerns (Budget aus
dem Fenster, Gruende, llm_calls), Laden der Vorlagen-Kostenpositionen, Ablage
der Vorschau als Operation, Uebernahme mit Provenienz und der Rueckfall der
Provenienz bei einer spaeteren manuellen Bearbeitung. Genau dort sitzen die
Zusicherungen, die ein Mock nicht nachweisen koennte.
"""

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm import reasons
from app.main import app
from app.services import project_cost_ai_suggestion_service, searxng_service
from tests import ki_attrappe
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _household_headers() -> dict[str, str]:
    token = _signup(f"cost-ai-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-11")
    household = client.post(
        "/v1/households",
        json={"name": "KI-Kostenschaetzung-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def headers() -> dict[str, str]:
    return _household_headers()


def _create_project_from_template(headers: dict[str, str]) -> tuple[str, str]:
    project = client.post(
        "/v1/projects",
        json={
            "name": "Hausbau Musterstrasse",
            "project_type": "home_build",
            "template_key": "home_build_de",
        },
        headers=headers,
    ).json()
    scenarios = client.get(f"/v1/projects/{project['id']}/scenarios", headers=headers).json()
    scenario = scenarios[0]
    return str(project["id"]), str(scenario["id"])


def _cost_items(headers: dict[str, str], project_id: str, scenario_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-items", headers=headers
    )
    assert response.status_code == 200, response.text
    return list(response.json())


def _item_by_semantic_key(items: list[dict[str, Any]], semantic_key: str) -> dict[str, Any]:
    return next(i for i in items if i["semantic_key"] == semantic_key)


def _stub_model(
    monkeypatch: pytest.MonkeyPatch,
    answer: dict[str, Any] | None,
    found: Any = None,
) -> list[str]:
    """Ersetzt Discovery und Modellserver; der Gateway laeuft echt.
    Zurueckgegeben werden die tatsaechlich gestellten Anfragen als
    "Systemprompt --- Nutzerprompt", damit ein Test pruefen kann, ob die
    richtigen Positionen abgefragt wurden."""
    asked: list[str] = []

    def reply(body: dict[str, Any]) -> httpx.Response:
        messages = body["messages"]
        asked.append(f"{messages[0]['content']}\n---\n{messages[-1]['content']}")
        return ki_attrappe.completion(
            "" if answer is None else json.dumps(answer, ensure_ascii=False)
        )

    ki_attrappe.install(monkeypatch, ki_attrappe.ModelServer(reply), found)
    return asked


def _install(
    monkeypatch: pytest.MonkeyPatch, reply: Any, found: Any = None
) -> ki_attrappe.ModelServer:
    return ki_attrappe.install(monkeypatch, ki_attrappe.ModelServer(reply), found)


def _preview(
    headers: dict[str, str], project_id: str, scenario_id: str, **body: Any
) -> dict[str, Any]:
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={"description": "Normaler Hausbau.", **body},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------


def test_preview_needs_a_reachable_model(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Modell ist eine leere Vorschlagsliste die Aussage "es fehlt
    nichts" - und damit schlicht falsch. Deshalb 503."""
    project_id, scenario_id = _create_project_from_template(headers)
    _stub_model(monkeypatch, {"items": []}, ki_attrappe.offline())

    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={"description": "Normaler Hausbau in Muenchen."},
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "ai_model_unavailable"


def test_preview_with_no_fixed_cost_items_skips_the_model(headers: dict[str, str]) -> None:
    """Ein Szenario ohne Vorlage hat keine Kostenpositionen - dann darf der
    Aufruf nicht am fehlenden Modell scheitern, es gibt schlicht nichts zu
    schaetzen (dieselbe Gate-Logik wie bei den Kategorienclustern)."""
    project = client.post(
        "/v1/projects",
        json={"name": "Ohne Vorlage", "project_type": "other"},
        headers=headers,
    ).json()
    scenario = client.get(f"/v1/projects/{project['id']}/scenarios", headers=headers).json()[0]

    response = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-ai-suggestions/preview",
        json={"description": "Irrelevant."},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggestions"] == []
    assert body["model_name"] is None


def test_preview_proposes_estimates_for_template_items(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, scenario_id = _create_project_from_template(headers)
    items = _cost_items(headers, project_id, scenario_id)
    land_item = _item_by_semantic_key(items, "grundstueck_kaufpreis")
    rohbau_item = _item_by_semantic_key(items, "rohbau_position")

    answer = {
        "items": [
            {
                "item_id": land_item["id"],
                "estimate_minor": 0,
                "confidence": 0.95,
                "rationale": "Grundstueck laut Beschreibung geerbt, kein Kaufpreis.",
            },
            {
                "item_id": rohbau_item["id"],
                "estimate_minor": 25_000_000,
                "confidence": 0.6,
                "rationale": "Rohbau fuer ein freistehendes Einfamilienhaus, grobe Schaetzung.",
            },
        ]
    }
    asked = _stub_model(monkeypatch, answer)

    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={
            "description": "Grundstueck geerbt, kein Kaufpreis noetig. Sonst normaler Hausbau.",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_name"] == "test-model"
    assert body["search_requested"] is False
    assert body["search_performed"] is False
    assert len(asked) == 1
    assert "Grundstueck geerbt" in asked[0]

    suggestions = {s["cost_item_id"]: s for s in body["suggestions"]}
    assert len(suggestions) == len(items)
    assert suggestions[land_item["id"]]["proposed_estimate_minor"] == 0
    assert suggestions[rohbau_item["id"]]["proposed_estimate_minor"] == 25_000_000
    # Positionen, die das Modell nicht beantwortet hat, bleiben mit
    # proposed_estimate_minor=None sichtbar statt zu verschwinden.
    untouched = next(s for s in body["suggestions"] if s["cost_item_id"] not in (
        land_item["id"], rohbau_item["id"]
    ))
    assert untouched["proposed_estimate_minor"] is None


def test_preview_reports_missing_searxng_url(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, scenario_id = _create_project_from_template(headers)
    _stub_model(monkeypatch, {"items": []})

    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={"description": "Irrelevant.", "use_web_search": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["search_requested"] is True
    assert body["search_performed"] is False
    assert body["search_unavailable_reason"]


async def test_preview_reports_a_timeout_as_technical_failure(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Gateway benennt das abgelaufene Zeitbudget (model_timeout); die
    Vorschau zeigt es als technischen Fehlschlag, nicht als "nichts zu
    schaetzen"."""
    project_id, scenario_id = _create_project_from_template(headers)
    _install(monkeypatch, lambda body: httpx.ReadTimeout("zu langsam"))

    body = _preview(headers, project_id, scenario_id)

    assert body["generation_status"] == "timeout"
    assert body["error_code"] == reasons.MODEL_TIMEOUT
    assert "Zeitbudget" in body["error_message"]
    assert body["suggestions"]
    assert all(item["proposed_estimate_minor"] is None for item in body["suggestions"])
    [call] = await ki_attrappe.llm_calls(headers["X-Household-Id"])
    assert call.status == "timeout"
    assert call.reason_code == reasons.MODEL_TIMEOUT


def test_preview_uses_configured_searxng(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    put = client.put(
        "/v1/ai/settings",
        json={"searxng_base_url": "http://searxng.local:8080"},
        headers=headers,
    )
    assert put.status_code == 200, put.text

    project_id, scenario_id = _create_project_from_template(headers)
    _stub_model(monkeypatch, {"items": []})

    async def fake_search(base_url: str, query: str) -> list[searxng_service.SearxngResult]:
        assert base_url == "http://searxng.local:8080"
        return [
            searxng_service.SearxngResult(title="Baupreise 2026", url="https://x", snippet="...")
        ]

    monkeypatch.setattr(searxng_service, "search", fake_search)

    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={"description": "Irrelevant.", "use_web_search": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["search_performed"] is True
    assert body["search_unavailable_reason"] is None
    assert len(body["search_snippets"]) == 1
    assert body["search_snippets"][0]["title"] == "Baupreise 2026"


# ---------------------------------------------------------------------------
# Anwenden
# ---------------------------------------------------------------------------


def test_apply_updates_estimate_and_records_provenance(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, scenario_id = _create_project_from_template(headers)
    items = _cost_items(headers, project_id, scenario_id)
    land_item = _item_by_semantic_key(items, "grundstueck_kaufpreis")

    answer = {
        "items": [
            {
                "item_id": land_item["id"],
                "estimate_minor": 0,
                "confidence": 0.95,
                "rationale": "Geerbtes Grundstueck, kein Kaufpreis.",
            }
        ]
    }
    _stub_model(monkeypatch, answer)

    preview = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={"description": "Grundstueck geerbt."},
        headers=headers,
    ).json()

    applied = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "request_id": "ki-kosten-1",
            "accepted": [{"cost_item_id": land_item["id"], "estimate_minor": 0}],
        },
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["updated_count"] == 1
    assert result["items"][0]["applied"] is True

    updated = _item_by_semantic_key(
        _cost_items(headers, project_id, scenario_id), "grundstueck_kaufpreis"
    )
    assert updated["estimate_source"] == "ai"
    assert updated["ai_confidence"] == 0.95
    assert "Geerbtes Grundstueck" in updated["ai_rationale"]

    # Wiederholung mit derselben request_id liefert das gespeicherte Ergebnis.
    replay = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "request_id": "ki-kosten-1",
            "accepted": [{"cost_item_id": land_item["id"], "estimate_minor": 0}],
        },
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True


def test_apply_skips_items_the_model_did_not_address(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, scenario_id = _create_project_from_template(headers)
    items = _cost_items(headers, project_id, scenario_id)
    land_item = _item_by_semantic_key(items, "grundstueck_kaufpreis")
    rohbau_item = _item_by_semantic_key(items, "rohbau_position")

    _stub_model(
        monkeypatch,
        {
            "items": [
                {
                    "item_id": land_item["id"],
                    "estimate_minor": 0,
                    "confidence": 0.9,
                    "rationale": "Geerbt.",
                }
            ]
        },
    )
    preview = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={"description": "Grundstueck geerbt."},
        headers=headers,
    ).json()

    applied = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "accepted": [{"cost_item_id": rohbau_item["id"], "estimate_minor": 100_000}],
        },
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["updated_count"] == 0
    assert result["items"][0]["skipped_reason"] == "not_in_preview"


def test_apply_skips_items_whose_calculation_mode_changed(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, scenario_id = _create_project_from_template(headers)
    items = _cost_items(headers, project_id, scenario_id)
    land_item = _item_by_semantic_key(items, "grundstueck_kaufpreis")

    _stub_model(
        monkeypatch,
        {
            "items": [
                {
                    "item_id": land_item["id"],
                    "estimate_minor": 12_000_000,
                    "confidence": 0.7,
                    "rationale": "Marktueblicher Grundstueckspreis laut Beschreibung.",
                }
            ]
        },
    )
    preview = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={"description": "Normaler Grundstueckskauf."},
        headers=headers,
    ).json()

    # Zwischen Vorschau und Anwenden stellt der Nutzer die Position auf eine
    # Prozentposition um - dann darf die KI keinen manuellen Wert mehr setzen.
    patch = client.patch(
        f"/v1/projects/{project_id}/cost-items/{land_item['id']}",
        json={
            "calculation_mode": "percentage",
            "percentage_rate_bps": 500,
            "percentage_basis": "project_direct",
            "clear_fields": ["estimate_manual_minor"],
        },
        headers={**headers, "If-Match": f'"{land_item["version"]}"'},
    )
    assert patch.status_code == 200, patch.text

    applied = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "accepted": [{"cost_item_id": land_item["id"], "estimate_minor": 12_000_000}],
        },
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["updated_count"] == 0
    assert result["items"][0]["skipped_reason"] == "calculation_mode_changed"


def test_manual_edit_after_ai_apply_resets_provenance(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, scenario_id = _create_project_from_template(headers)
    items = _cost_items(headers, project_id, scenario_id)
    land_item = _item_by_semantic_key(items, "grundstueck_kaufpreis")

    _stub_model(
        monkeypatch,
        {
            "items": [
                {
                    "item_id": land_item["id"],
                    "estimate_minor": 5_000_000,
                    "confidence": 0.8,
                    "rationale": "Schaetzung laut Beschreibung.",
                }
            ]
        },
    )
    preview = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/preview",
        json={"description": "Normaler Grundstueckskauf."},
        headers=headers,
    ).json()
    client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-ai-suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "accepted": [{"cost_item_id": land_item["id"], "estimate_minor": 5_000_000}],
        },
        headers=headers,
    )
    after_apply = _item_by_semantic_key(
        _cost_items(headers, project_id, scenario_id), "grundstueck_kaufpreis"
    )
    assert after_apply["estimate_source"] == "ai"

    manual = client.patch(
        f"/v1/projects/{project_id}/cost-items/{land_item['id']}",
        json={"estimate_manual_minor": 6_000_000},
        headers={**headers, "If-Match": f'"{after_apply["version"]}"'},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["estimate_source"] == "manual"
    assert manual.json()["ai_operation_id"] is None


async def test_preview_sends_the_household_reasoning_in_one_call_with_the_window_budget(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frueher: Analyseaufruf mit hart gedeckeltem Memo (hoechstens 9000 Token)
    plus Formatierer mit reasoning="off". Jetzt EIN Aufruf mit der
    Reasoning-Einstellung des Haushalts und dem ganzen freien Platz des
    geladenen Fensters - gerechnet vom Gateway, nicht von dieser Datei."""
    put = client.put("/v1/ai/settings", json={"reasoning": "high"}, headers=headers)
    assert put.status_code == 200, put.text
    project_id, scenario_id = _create_project_from_template(headers)
    items = _cost_items(headers, project_id, scenario_id)

    server = _install(monkeypatch, lambda body: ki_attrappe.completion('{"items": []}'))
    body = _preview(headers, project_id, scenario_id)

    assert len(items) > 1
    [request] = server.bodies
    assert request["reasoning_effort"] == "high"
    reserve = ki_attrappe.WINDOW * 0.25
    assert request["max_tokens"] > ki_attrappe.WINDOW - reserve
    schema = request["response_format"]["json_schema"]["schema"]
    # Mit Reasoning ohne Gesamtsummen-Anker (siehe build_cost_ai_json_schema).
    assert "total_estimate_minor" not in schema["properties"]
    assert body["generation_status"] == "no_suggestions"
    assert body["error_code"] is None

    [call] = await ki_attrappe.llm_calls(headers["X-Household-Id"])
    assert call.stage == project_cost_ai_suggestion_service.COST_AI_STAGE
    assert call.status == "success"
    assert call.reasoning_sent == "high"
    assert call.max_tokens_sent == request["max_tokens"]
    assert call.prompt_version.startswith(project_cost_ai_suggestion_service.COST_AI_PROMPT_VERSION)


async def test_a_reasoning_memo_without_answer_is_named_not_reformatted(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Endet der Aufruf im Denken ohne Antwort, gibt es keinen zweiten
    "Formatierer"-Aufruf mit Reasoning aus (E2) und kein "ohne Denken
    wiederholen" (E1): genau ein Aufruf, benannter Grund, keine Vorschlaege."""
    put = client.put("/v1/ai/settings", json={"reasoning": "high"}, headers=headers)
    assert put.status_code == 200, put.text
    project_id, scenario_id = _create_project_from_template(headers)
    land_item = _item_by_semantic_key(
        _cost_items(headers, project_id, scenario_id), "grundstueck_kaufpreis"
    )

    server = _install(
        monkeypatch,
        lambda body: ki_attrappe.completion(
            reasoning=f"Position {land_item['id']} ist geerbt; Ergebnis 0 Cent. Final check ...",
            finish_reason="length",
        ),
    )
    body = _preview(headers, project_id, scenario_id, description="Grundstueck geerbt.")

    assert len(server.bodies) == 1
    assert body["generation_status"] == "invalid_response"
    assert body["error_code"] == reasons.MODEL_BUDGET_EXHAUSTED
    assert "Antwortbudget" in body["error_message"]
    assert all(item["proposed_estimate_minor"] is None for item in body["suggestions"])
    [call] = await ki_attrappe.llm_calls(headers["X-Household-Id"])
    assert call.status == "error"
    assert call.reason_code == reasons.MODEL_BUDGET_EXHAUSTED


async def test_json_in_the_reasoning_of_the_same_answer_is_used(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Steht das fertige JSON nur im Denktext derselben Antwort, wird es von
    dort gelesen - ohne Zusatzaufruf; die Protokollzeile sagt das."""
    project_id, scenario_id = _create_project_from_template(headers)
    land_item = _item_by_semantic_key(
        _cost_items(headers, project_id, scenario_id), "grundstueck_kaufpreis"
    )
    answer = {
        "items": [
            {
                "item_id": land_item["id"],
                "estimate_minor": 0,
                "confidence": 0.95,
                "rationale": "Grundstueck ist geerbt.",
            }
        ]
    }
    server = _install(
        monkeypatch, lambda body: ki_attrappe.completion(reasoning=json.dumps(answer))
    )
    body = _preview(headers, project_id, scenario_id, description="Grundstueck geerbt.")

    assert len(server.bodies) == 1
    assert body["generation_status"] == "success"
    suggestion = next(
        item for item in body["suggestions"] if item["cost_item_id"] == land_item["id"]
    )
    assert suggestion["proposed_estimate_minor"] == 0
    [call] = await ki_attrappe.llm_calls(headers["X-Household-Id"])
    assert call.status == "success"
    assert "Denktext" in call.error_message


def test_an_answer_without_items_is_unusable_not_empty(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein JSON ohne "items" ist keine Enthaltung, sondern ein Vertragsbruch -
    sonst saehe eine kaputte Antwort aus wie "nichts zu schaetzen"."""
    project_id, scenario_id = _create_project_from_template(headers)
    _install(monkeypatch, lambda body: ki_attrappe.completion('{"total_estimate_minor": 5}'))

    body = _preview(headers, project_id, scenario_id)

    assert body["generation_status"] == "invalid_response"
    assert body["error_code"] == reasons.MODEL_OUTPUT_UNUSABLE


def test_switched_off_ai_is_named_instead_of_searching_for_a_model(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_enabled wirkt: kein Discovery-Versuch (sonst hier 503), kein Aufruf,
    und die Vorschau nennt den Grund statt einer leeren Liste."""
    put = client.put("/v1/ai/settings", json={"is_enabled": False}, headers=headers)
    assert put.status_code == 200, put.text
    project_id, scenario_id = _create_project_from_template(headers)
    asked = _stub_model(monkeypatch, {"items": []}, ki_attrappe.offline())

    body = _preview(headers, project_id, scenario_id)

    assert asked == []
    assert body["generation_status"] == "model_error"
    assert body["error_code"] == reasons.AI_DISABLED
    assert "ausgeschaltet" in body["error_message"]
    assert body["model_name"] is None


async def test_an_unknown_window_is_named_and_nothing_is_sent(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, scenario_id = _create_project_from_template(headers)
    asked = _stub_model(monkeypatch, {"items": []}, ki_attrappe.target_result(window=None))

    body = _preview(headers, project_id, scenario_id)

    assert asked == []
    assert body["generation_status"] == "model_error"
    assert body["error_code"] == reasons.MODEL_CONTEXT_UNKNOWN
    [call] = await ki_attrappe.llm_calls(headers["X-Household-Id"])
    assert call.status == "refused"
    assert call.reason_code == reasons.MODEL_CONTEXT_UNKNOWN


def test_all_fixed_items_are_asked_not_only_the_first_sixty(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frueher fielen ab der 61. Position alle weiteren still weg. Jetzt geht
    jede feste Position in die Frage; ob das ins Fenster passt, prueft der
    Gateway."""
    project_id, scenario_id = _create_project_from_template(headers)
    items = _cost_items(headers, project_id, scenario_id)
    section_id = items[0]["section_id"]
    for index in range(70 - len(items)):
        created = client.post(
            f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-items",
            json={
                "section_id": section_id,
                "name": f"Zusatzposition {index + 1}",
                "estimate_manual_minor": 100_000,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
    fixed = [
        item
        for item in _cost_items(headers, project_id, scenario_id)
        if item["calculation_mode"] == "fixed"
    ]
    assert len(fixed) > 60

    asked = _stub_model(monkeypatch, {"items": []})
    body = _preview(headers, project_id, scenario_id)

    [prompt] = asked
    assert all(item["id"] in prompt for item in fixed)
    assert len(body["suggestions"]) == len(fixed)


def test_search_hits_that_do_not_fit_are_dropped_and_named(
    headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Websuchtreffer sind Zusatzmaterial: passen sie nicht mehr samt
    Antwortreserve ins Fenster, fallen sie von hinten weg - die Positionen
    bleiben, und die Vorschau sagt, was nicht mitging."""
    put = client.put(
        "/v1/ai/settings", json={"searxng_base_url": "http://searxng.local:8080"}, headers=headers
    )
    assert put.status_code == 200, put.text
    project_id, scenario_id = _create_project_from_template(headers)

    async def big_hits(base_url: str, query: str) -> list[searxng_service.SearxngResult]:
        # Je Treffer rund 5.000 Token (Startwert 3 Zeichen je Token).
        return [
            searxng_service.SearxngResult(
                title=f"Treffer {index}", url=f"https://x/{index}", snippet="Baupreis " * 1_667
            )
            for index in range(3)
        ]

    monkeypatch.setattr(searxng_service, "search", big_hits)
    # Fenster 20.000: Reserve 5.000; zwei Treffer passen samt Positionen,
    # drei nicht.
    asked = _stub_model(monkeypatch, {"items": []}, ki_attrappe.target_result(window=20_000))
    body = _preview(headers, project_id, scenario_id, use_web_search=True)

    [prompt] = asked
    assert "Treffer 0" in prompt
    assert "Treffer 1" in prompt
    assert "Treffer 2" not in prompt
    assert body["search_performed"] is True
    assert [snippet["title"] for snippet in body["search_snippets"]] == ["Treffer 0", "Treffer 1"]
    assert "1 von 3" in body["search_unavailable_reason"]
