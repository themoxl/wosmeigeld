"""Real API/RLS integration for AI assessment of committed pairs.

Only target discovery (llm.resolve_target) and the HTTP request to the model
server are replaced; the gateway writes its llm_calls row into the real
database under the editor's RLS session."""

import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from app import llm
from app.llm import reasons
from app.llm.discovery import Target, TargetResult
from app.llm.window import WindowInfo
from app.services import booked_duplicate_ai_service as service
from tests import test_review_duplicates as duplicate_fixtures
from tests.test_household_isolation import _auth_headers, _signup, _user_id_from_token
from tests.test_review_duplicates import (  # noqa: F401 - shared pytest fixtures
    _bulk_pairs,
    _resolve_input,
    _sql,
    client,
)

kopf = duplicate_fixtures.kopf
konto_id = duplicate_fixtures.konto_id


def assessment_input(pairs):
    return {
        "items": [
            {k: v for k, v in _resolve_input(pair, "keep_both").items() if k != "action"}
            for pair in pairs
        ]
    }


def _target() -> TargetResult:
    found = Target(
        base_url="http://model.test/v1",
        model="test-model",
        window=WindowInfo(loaded=32768, cap=None, effective=32768, source="/api/v1/models"),
    )
    return TargetResult(target=found, base_url=found.base_url, models=[], detailed=True)


@pytest.fixture()
def model(monkeypatch):
    discovery = AsyncMock(return_value=_target())
    completion = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "urteile": [
                                        {
                                            "nummer": 1,
                                            "urteil": "same",
                                            "behalten": "second",
                                            "begruendung": "Die zweite Buchung enthält die Notiz.",
                                            "konfidenz": 0.99,
                                        },
                                        {
                                            "nummer": 2,
                                            "urteil": "different",
                                            "behalten": "none",
                                            "begruendung": "Unterschiedliche Zahlungsreferenzen.",
                                            "konfidenz": 0.99,
                                        },
                                        {
                                            "nummer": 3,
                                            "urteil": "unsure",
                                            "behalten": "none",
                                            "begruendung": "Kein eindeutiger Beleg.",
                                            "konfidenz": 0.5,
                                        },
                                    ]
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )
    )
    monkeypatch.setattr(llm, "resolve_target", discovery)
    monkeypatch.setattr(httpx.AsyncClient, "post", completion)
    return discovery, completion


async def test_assess_then_apply_mixed_decisions_keeps_unclear_pair(kopf, konto_id, model):
    pairs = await _bulk_pairs(kopf, konto_id, 3)
    response = client.post(
        "/v1/review/duplicates/assess-booked", headers=kopf, json=assessment_input(pairs)
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["available"]
    assert [item["action"] for item in result["assessments"]] == [
        "archive_first",
        "keep_both",
        None,
    ]
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json()["scanned_count"] == 6
    audit = (
        await _sql(
            "select stage, prompt_version, status, window_tokens, max_tokens_sent "
            "from llm_calls where household_id = :h",
            {"h": kopf["X-Household-Id"]},
        )
    ).one()
    assert (
        audit.stage == "booked_duplicate_assess" and audit.prompt_version == service.PROMPT_VERSION
    )
    # The gateway's row carries the budget line of the call.
    assert audit.status == "success"
    assert audit.window_tokens == 32768 and audit.max_tokens_sent > 0
    decisions = [
        _resolve_input(pair, item["action"])
        for pair, item in zip(pairs, result["assessments"], strict=True)
        if item["action"] is not None
    ]
    response = client.post(
        "/v1/review/duplicates/resolve-bulk", headers=kopf, json={"items": decisions}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"processed_count": 2, "archived_count": 1, "kept_count": 1}
    scan = client.get("/v1/review/duplicates/scan", headers=kopf).json()
    assert scan["scanned_count"] == 5 and scan["total_count"] == 1
    assert scan["items"][0] == pairs[2]


async def test_ai_decision_cannot_apply_after_booking_changes(kopf, konto_id, model):
    pairs = await _bulk_pairs(kopf, konto_id, 1)
    response = client.post(
        "/v1/review/duplicates/assess-booked", headers=kopf, json=assessment_input(pairs)
    )
    assert response.status_code == 200, response.text
    await _sql(
        "update transactions set note = 'Neue Notiz' where id = :id",
        {"id": pairs[0]["first"]["transaction_id"]},
    )
    response = client.post(
        "/v1/review/duplicates/resolve-bulk",
        headers=kopf,
        json={"items": [_resolve_input(pairs[0], response.json()["assessments"][0]["action"])]},
    )
    assert response.status_code == 409
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json()["scanned_count"] == 2


async def test_changed_pair_is_rejected_before_contacting_model(kopf, konto_id, model):
    pairs = await _bulk_pairs(kopf, konto_id, 1)
    payload = assessment_input(pairs)
    payload["items"][0]["second_version"] += 1
    response = client.post("/v1/review/duplicates/assess-booked", headers=kopf, json=payload)
    assert response.status_code == 409
    model[0].assert_not_awaited()
    model[1].assert_not_awaited()


async def test_foreign_household_pairs_never_reach_model(kopf, konto_id, model):
    pairs = await _bulk_pairs(kopf, konto_id, 1)
    token = _signup(f"ai-other-{uuid.uuid4().hex}@finanzplaner.test", "test-password-long")
    household = client.post(
        "/v1/households", headers=_auth_headers(token), json={"name": "Anderer Haushalt"}
    ).json()
    response = client.post(
        "/v1/review/duplicates/assess-booked",
        headers={**_auth_headers(token), "X-Household-Id": household["id"]},
        json=assessment_input(pairs),
    )
    assert response.status_code == 404
    model[0].assert_not_awaited()


async def test_viewer_cannot_invoke_billable_ai_assessment(kopf, konto_id, model):
    pairs = await _bulk_pairs(kopf, konto_id, 1)
    token = _signup(f"ai-viewer-{uuid.uuid4().hex}@finanzplaner.test", "test-password-long")
    await _sql(
        "insert into household_members (household_id, user_id, role, status) "
        "values (:h, :u, 'viewer', 'active')",
        {"h": kopf["X-Household-Id"], "u": _user_id_from_token(token)},
    )
    response = client.post(
        "/v1/review/duplicates/assess-booked",
        headers={**_auth_headers(token), "X-Household-Id": kopf["X-Household-Id"]},
        json=assessment_input(pairs),
    )
    assert response.status_code == 403
    model[0].assert_not_awaited()


async def test_real_query_includes_notes_and_reference_context(kopf, konto_id, model):
    pairs = await _bulk_pairs(kopf, konto_id, 1)
    await _sql(
        "update transactions set note = 'Schon kontrolliert' where id = :id",
        {"id": pairs[0]["second"]["transaction_id"]},
    )
    pairs = client.get("/v1/review/duplicates/scan", headers=kopf).json()["items"]
    response = client.post(
        "/v1/review/duplicates/assess-booked", headers=kopf, json=assessment_input(pairs)
    )
    assert response.status_code == 200, response.text
    prompt = json.loads(model[1].call_args.kwargs["json"]["messages"][1]["content"])
    assert prompt["paare"][0]["second"]["notiz"] == "Schon kontrolliert"
    assert "verknuepfte_bereiche" in prompt["paare"][0]["first"]


async def test_a_null_message_from_the_model_is_a_named_200_not_a_500(kopf, konto_id, model):
    """Finding: choice["message"] = null raised a TypeError -> HTTP 500."""
    pairs = await _bulk_pairs(kopf, konto_id, 1)
    model[1].return_value = httpx.Response(
        200, json={"choices": [{"message": None, "finish_reason": "stop"}]}
    )
    response = client.post(
        "/v1/review/duplicates/assess-booked", headers=kopf, json=assessment_input(pairs)
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["available"] is False
    assert result["reason"] == reasons.MODEL_OUTPUT_UNUSABLE
    assert result["assessments"] == []
    audit = (
        await _sql(
            "select status, reason_code from llm_calls where household_id = :h",
            {"h": kopf["X-Household-Id"]},
        )
    ).one()
    assert (audit.status, audit.reason_code) == ("error", reasons.MODEL_OUTPUT_UNUSABLE)
    # Nothing was applied.
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json()["scanned_count"] == 2
