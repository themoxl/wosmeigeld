import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.llm import reasons
from app.main import app
from app.services import ai_settings_service
from app.services.ai_service import (
    NO_MATCH,
    NO_MATCH_REASON_CODE,
    PROMPT_VERSION,
    STALE_CATEGORY_REASON_CODE,
    UsageTokens,
    build_system_prompt,
    check_lm_studio_health,
    classify_transaction_with_llm,
    extract_usage_tokens,
    load_categories,
    prompt_version_for,
)
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

_TEST_CATEGORY_ID = "22222222-2222-4222-8222-000000000001"
_TEST_TAXONOMY_VERSION = 1
_EXPECTED_PROMPT_VERSION = prompt_version_for(_TEST_TAXONOMY_VERSION)
# Fenster, mit dem das gefaelschte LM Studio das Modell "geladen" hat.
_LOADED_WINDOW = 32768

# Kandidatenzeile so, wie CANDIDATE_SQL sie liefert.
_TEST_CATEGORY_ROW = SimpleNamespace(
    id=_TEST_CATEGORY_ID,
    name="Lebensmittel",
    group_name="Variable Kosten",
    semantic_key="groceries",
    description="Wocheneinkauf im Supermarkt, Discounter und Bioladen.",
    synonyms=["Supermarkt", "Discounter"],
    default_type="expense",
    budget_bucket="needs",
    taxonomy_version=_TEST_TAXONOMY_VERSION,
)

# Einstellungszeile eines frisch angelegten Haushalts: die Werte sind die
# Spaltenvorgaben aus 0001_schema.sql (hier als Testdaten abgeschrieben).
_SETTINGS_ROW: dict[str, Any] = {
    "is_enabled": True,
    "auto_categorize_min_confidence": 0.85,
    "custom_base_url": None,
    "model_name": None,
    "legacy_max_output_tokens": None,
    "temperature": None,
    "sampling_mode": None,
    "top_p": None,
    "top_k": None,
    "repeat_penalty": None,
    "min_p": None,
    "presence_penalty": None,
    "request_timeout_seconds": 120,
    "context_length": None,
    "reasoning": None,
    "searxng_base_url": None,
    "context_cap_tokens": None,
    "answer_reserve_share": 0.25,
    "result_page_share": 0.1,
    "run_time_limit_minutes": 20,
    "start_chars_per_token": 3.0,
    "start_estimate_error": 0.15,
    "context_line_enabled": True,
}


def _resolved(**overrides: Any) -> ai_settings_service.ResolvedAISettings:
    """Einstellungen wie aus der Datenbank (Schemavorgaben), mit Abweichungen."""
    values: dict[str, Any] = {
        "is_enabled": True,
        "min_confidence": 0.85,
        "custom_base_url": None,
        "model_name": None,
        "temperature": 1.0,
        "request_timeout_seconds": 30.0,
        "answer_reserve_share": 0.25,
        "result_page_share": 0.1,
        "run_time_limit_minutes": 20,
        "start_chars_per_token": 3.0,
        "start_estimate_error": 0.15,
        "context_line_enabled": True,
    }
    values.update(overrides)
    return ai_settings_service.ResolvedAISettings(**values)


def _mock_session(
    categories: list[SimpleNamespace], resolved: SimpleNamespace | None = None
) -> AsyncMock:
    """Session-Attrappe fuer den Klassifikationspfad. Sie beantwortet die
    SELECTs (Einstellungen, Messung, Kandidatenliste, Vor-Schreib-Aufloesung)
    und quittiert alle INSERTs/UPDATEs mit einem leeren Platzhalter."""
    if resolved is None:
        resolved = SimpleNamespace(
            id=_TEST_CATEGORY_ID,
            is_archived=False,
            group_is_archived=False,
            merged_into_category_id=None,
        )
    resolved_row = MagicMock()
    resolved_row._mapping = {
        "id": resolved.id,
        "is_archived": resolved.is_archived,
        "group_is_archived": resolved.group_is_archived,
        "merged_into_category_id": resolved.merged_into_category_id,
    }
    settings_row = MagicMock()
    settings_row._mapping = dict(_SETTINGS_ROW)

    async def _execute(statement: Any, *args: Any, **kwargs: Any) -> MagicMock:
        sql = str(getattr(statement, "text", statement))
        result = MagicMock()
        lowered = sql.lstrip().lower()
        if lowered.startswith("select") and "from ai_household_settings" in sql:
            result.first.return_value = settings_row
        elif lowered.startswith("select") and "from ai_model_measurements" in sql:
            # Noch keine Messung: es gelten die Startwerte der Einstellung.
            result.first.return_value = None
        elif ":category_id" in sql:
            result.first.return_value = resolved_row
        elif lowered.startswith("select"):
            result.__iter__.return_value = iter(categories)
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    return session


def _writes(session: AsyncMock, table: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Schreibende Statements in Reihenfolge, optional nur fuer eine Tabelle."""
    written: list[tuple[str, dict[str, Any]]] = []
    for call in session.execute.await_args_list:
        sql = str(call.args[0].text)
        lowered = sql.lstrip().lower()
        if not lowered.startswith(("insert", "update")):
            continue
        if table is not None and table not in sql:
            continue
        params = call.args[1]
        assert isinstance(params, dict)
        written.append((sql, params))
    return written


def _call_rows(session: AsyncMock) -> list[dict[str, Any]]:
    """Die llm_calls-Zeilen (INSERT) eines Laufs."""
    return [
        params for sql, params in _writes(session, "llm_calls") if sql.lstrip().startswith("insert")
    ]


# usage-Block im Wortlaut von LM Studio.
_LM_STUDIO_USAGE: dict[str, Any] = {
    "prompt_tokens": 7123,
    "completion_tokens": 384,
    "total_tokens": 7507,
    "completion_tokens_details": {"reasoning_tokens": 311},
}


def _chat_response(
    status_code: int,
    content: str,
    finish_reason: str = "stop",
    usage: dict[str, Any] | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    payload: dict[str, Any] = {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}]
    }
    # Ohne Argument fehlt der usage-Block ganz - das muss zu NULL fuehren, nicht zu 0.
    if usage is not None:
        payload["usage"] = usage
    resp.json.return_value = payload
    return resp


def _truncated_response(usage: dict[str, Any] | None = None) -> MagicMock:
    """Empirisch beobachtete Antwort eines Reasoning-Modells: der gesamte
    Output landete in reasoning_content, content blieb leer, finish_reason
    'length'."""
    resp = MagicMock()
    resp.status_code = 200
    payload: dict[str, Any] = {
        "choices": [
            {
                "message": {"content": "", "reasoning_content": "Der Haendler REWE ist ein..."},
                "finish_reason": "length",
            }
        ]
    }
    if usage is not None:
        payload["usage"] = usage
    resp.json.return_value = payload
    return resp


def _models_response(
    window: int | None = _LOADED_WINDOW, model: str = "test-model"
) -> MagicMock:
    """Dieselbe Antwort fuer jeden GET: LM Studio /api/v0/models mit einem
    geladenen Modell (ohne Fenster: nur die Modellliste)."""
    entry: dict[str, Any] = {"id": model, "type": "llm", "state": "loaded"}
    if window is not None:
        entry["loaded_context_length"] = window
        entry["max_context_length"] = 262144
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": [entry]}
    return resp


def _canary_answer() -> str:
    return (
        '{"payee_name": "REWE", "category_id": "00000000-0000-4000-8000-000000000001", '
        '"confidence": 0.9, "reason_code": "merchant_and_context_match"}'
    )


def _classify_answer() -> str:
    return (
        f'{{"payee_name": "REWE", "category_id": "{_TEST_CATEGORY_ID}", '
        f'"confidence": 0.88, "reason_code": "merchant_and_context_match"}}'
    )


def _decision(session: AsyncMock) -> dict[str, Any]:
    rows = _writes(session, "classification_decisions")
    assert len(rows) == 1
    return rows[0][1]


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p6-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Phase-6-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def test_ai_health_and_classify_fallback(household_headers: dict[str, str]) -> None:
    # 1. AI Health-Endpunkt
    h_res = client.get("/v1/ai/health", headers=household_headers)
    assert h_res.status_code == 200
    h_data = h_res.json()
    assert "status" in h_data
    assert "is_connected" in h_data
    assert "window" in h_data
    assert "max_output_tokens" not in h_data

    # 2. KI Klassifikation (Fallback wenn LM Studio offline oder kein Modell)
    c_res = client.post(
        "/v1/ai/classify",
        json={
            "payee_raw": "REWE Markt GmbH",
            "purpose": "Einkauf Lebensmittel",
            "amount_minor": -3550,
        },
        headers=household_headers,
    )
    assert c_res.status_code == 200
    c_data = c_res.json()
    assert "payee_name" in c_data
    assert "confidence" in c_data

    # 3. AI Decision Log
    d_res = client.get("/v1/ai/decisions", headers=household_headers)
    assert d_res.status_code == 200
    d_data = d_res.json()
    assert isinstance(d_data, list)


def test_create_classification_run_returns_pending_run_with_defaults(
    household_headers: dict[str, str],
) -> None:
    res = client.post("/v1/ai/classify-runs", json={}, headers=household_headers)
    assert res.status_code == 201
    data = res.json()
    assert data["status"] in ("pending", "running", "completed", "failed")
    assert data["total_count"] == 0
    assert data["processed_count"] == 0
    assert data["success_count"] == 0
    assert data["error_count"] == 0
    assert "id" in data
    assert "created_at" in data


def test_create_classification_run_accepts_legacy_months_param_but_uses_open_scope(
    household_headers: dict[str, str],
) -> None:
    # Alte Clients duerfen waehrend des Rollouts weiter starten. Das Feld ist
    # nicht mehr Teil des Schemas und beeinflusst den Lauf nicht mehr.
    res = client.post("/v1/ai/classify-runs", json={"months_back": 6}, headers=household_headers)
    assert res.status_code == 201


def test_create_classification_run_counts_old_open_transactions(
    household_headers: dict[str, str],
) -> None:
    account = client.post(
        "/v1/accounts",
        json={"name": "Altes Konto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    transaction = client.post(
        "/v1/transactions",
        json={
            "account_id": account["id"],
            "booked_date": "2020-01-15",
            "amount_minor": -1234,
            "payee_label": "Alter offener Umsatz",
        },
        headers=household_headers,
    )
    assert transaction.status_code == 201

    run = client.post("/v1/ai/classify-runs", json={}, headers=household_headers)

    assert run.status_code == 201
    assert run.json()["total_count"] == 1
    # Metadatum des Laufs, kein Filter: auch eine sechs Jahre alte offene
    # Buchung gehoert zum gestarteten Lauf.
    assert run.json()["since_date"] == "2020-01-15"


def test_get_classification_run_returns_404_for_unknown_run(
    household_headers: dict[str, str],
) -> None:
    res = client.get(f"/v1/ai/classify-runs/{uuid.uuid4()}", headers=household_headers)
    assert res.status_code == 404


def test_get_classification_run_enforces_household_isolation(
    household_headers: dict[str, str],
) -> None:
    create_res = client.post("/v1/ai/classify-runs", json={}, headers=household_headers)
    run_id = create_res.json()["id"]

    other_token = _signup(
        f"p6-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    other_household = client.post(
        "/v1/households", json={"name": "Anderer Haushalt"}, headers=_auth_headers(other_token)
    ).json()
    other_headers = {**_auth_headers(other_token), "X-Household-Id": other_household["id"]}

    res = client.get(f"/v1/ai/classify-runs/{run_id}", headers=other_headers)
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Capability-Canary: HTTP 200 allein genuegt nicht
# ---------------------------------------------------------------------------


async def test_canary_passes_only_with_schema_conform_answer() -> None:
    post = AsyncMock(return_value=_chat_response(200, _canary_answer()))
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        health = await check_lm_studio_health(_resolved())

    assert health.is_connected is True
    assert health.canary_passed is True
    assert health.supports_json_schema is True
    # Der Canary fordert dieselbe strukturierte Ausgabe an wie die Klassifikation
    # und bekommt sein Budget aus dem geladenen Fenster.
    canary_body = post.await_args_list[0].kwargs["json"]
    assert canary_body["response_format"]["type"] == "json_schema"
    assert 0 < canary_body["max_tokens"] < _LOADED_WINDOW
    assert health.context_tokens == _LOADED_WINDOW
    assert health.window is not None and health.window.source == "/api/v0/models"
    assert health.reserve_tokens == _LOADED_WINDOW // 4
    assert health.measurement is not None and health.measurement.is_start_value


async def test_canary_rejects_http_200_without_schema_conform_body() -> None:
    """Kern des Findings: frueher galt HTTP 200 als bestanden, ohne den Body
    zu pruefen."""
    post = AsyncMock(return_value=_chat_response(200, "Klar, wie kann ich helfen?"))
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        health = await check_lm_studio_health(_resolved())

    assert health.canary_passed is False
    assert health.supports_json_schema is False
    # Beide Wege probiert: strukturiert und Prompt-Fallback.
    assert post.await_count == 2
    assert health.canary_reason_code == reasons.MODEL_OUTPUT_UNUSABLE


async def test_canary_reports_prompt_fallback_when_response_format_is_rejected() -> None:
    post = AsyncMock(side_effect=[_chat_response(400, ""), _chat_response(200, _canary_answer())])
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        health = await check_lm_studio_health(_resolved())

    assert health.canary_passed is True
    assert health.supports_json_schema is False
    assert "Prompt-Fallback" in health.message


async def test_canary_failure_message_names_the_budget_and_the_setting() -> None:
    """Der Systemstatus soll dem Nutzer sagen, was zu tun ist - ohne festes
    Ausgabebudget nennt er das Antwortbudget aus dem Fenster."""
    post = AsyncMock(return_value=_truncated_response())
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        health = await check_lm_studio_health(_resolved())

    assert health.canary_passed is False
    assert health.canary_reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    assert "Antwortbudget" in health.message
    assert "Reasoning" in health.message
    # Abgeschnitten heisst: kein zweiter Versuch, der genauso ausfiele.
    assert post.await_count == 1
    assert [w.code for w in health.warnings] == [reasons.MODEL_BUDGET_EXHAUSTED]


async def test_without_a_loaded_window_no_canary_is_sent() -> None:
    post = AsyncMock(return_value=_chat_response(200, _canary_answer()))
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response(window=None))),
        patch("httpx.AsyncClient.post", post),
    ):
        health = await check_lm_studio_health(_resolved())

    assert post.await_count == 0
    assert health.context_tokens is None
    assert health.canary_passed is False
    assert reasons.MODEL_CONTEXT_UNKNOWN in [w.code for w in health.warnings]
    assert "Kontextfenster" in health.message


# ---------------------------------------------------------------------------
# Klassifikation: strukturierte Ausgabe, Capability-Persistenz, prompt_version
# ---------------------------------------------------------------------------


async def test_classify_sends_json_schema_and_persists_capability() -> None:
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer()),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await classify_transaction_with_llm(
            session,
            uuid.uuid4(),
            None,
            "REWE Markt GmbH",
            "Einkauf DE12345678901234567890",
            -1500,
        )

    assert str(result.category_id) == _TEST_CATEGORY_ID
    assert result.confidence == 0.88

    classify_body = post.await_args_list[1].kwargs["json"]
    schema = classify_body["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["category_id"]["enum"] == [_TEST_CATEGORY_ID, NO_MATCH]
    # Die Maskierung gilt auch auf dem strukturierten Pfad.
    user_prompt = classify_body["messages"][1]["content"]
    assert "DE12345678901234567890" not in user_prompt

    # Die Capability haengt am Vertrag, nicht an der Taxonomie.
    capability = _writes(session, "ai_model_capabilities")[0][1]
    assert capability["pv"] == PROMPT_VERSION
    assert capability["sjs"] is True
    call = _call_rows(session)[0]
    assert call["pv"] == _EXPECTED_PROMPT_VERSION
    assert call["stage"] == "classify"
    assert call["status"] == "success"
    assert _decision(session)["cat_id"] == _TEST_CATEGORY_ID


# ---------------------------------------------------------------------------
# Kandidatenliste und no_match (Leitplanke 8 "KI darf vorschlagen, nicht erfinden")
# ---------------------------------------------------------------------------


async def test_candidates_carry_path_key_synonyms_and_taxonomy_version() -> None:
    session = _mock_session([_TEST_CATEGORY_ROW])
    candidates = await load_categories(session, uuid.uuid4())

    entry = candidates.entries[0]
    assert entry["path"] == "Variable Kosten · Lebensmittel"
    assert entry["key"] == "groceries"
    assert entry["type"] == "expense"
    assert entry["bucket"] == "needs"
    assert entry["synonyms"] == ["Supermarkt", "Discounter"]
    assert "Wocheneinkauf" in entry["description"]
    assert candidates.taxonomy_version == _TEST_TAXONOMY_VERSION
    assert candidates.valid_ids == {_TEST_CATEGORY_ID}
    # Fassungen fuer kleine Fenster: ohne Beschreibung, dann ohne Synonyme.
    full, without_description, without_synonyms = candidates.all_variants()
    assert "description" not in without_description[0]
    assert "synonyms" in without_description[0]
    assert "synonyms" not in without_synonyms[0]

    candidate_sql = str(session.execute.await_args_list[0].args[0].text)
    assert "not c.is_archived" in candidate_sql
    assert "not cg.is_archived" in candidate_sql


def test_system_prompt_allows_no_match_and_forbids_a_catch_all_category() -> None:
    prompt = build_system_prompt(
        [{"id": _TEST_CATEGORY_ID, "path": "Variable Kosten · Lebensmittel"}],
        _TEST_TAXONOMY_VERSION,
    )
    assert NO_MATCH in prompt
    assert "Erfinde niemals" in prompt
    assert "Auffangbecken" in prompt
    assert "Taxonomieversion des Haushalts: 1" in prompt


async def test_no_match_is_recorded_as_a_successful_call_without_a_category() -> None:
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(
                200,
                '{"payee_name": "Unbekannt", "category_id": "no_match", '
                '"confidence": 0.2, "reason_code": "unclear_merchant"}',
            ),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "Unbekannter Haendler", "Zahlung", -900
        )

    assert result.category_id is None
    assert result.reason_code == NO_MATCH_REASON_CODE
    # Kein zweiter Versuch: ein Retry wuerde das Modell zum Raten draengen.
    assert post.await_count == 2
    assert _call_rows(session)[0]["status"] == "success"
    assert _decision(session)["cat_id"] is None
    assert _decision(session)["reason"] == NO_MATCH_REASON_CODE


async def test_merge_tombstone_is_canonicalised_before_the_decision_is_written() -> None:
    """classification_decisions traegt bewusst keinen Archiv-Trigger - der
    Resolver ist dort die einzige Schutzschicht."""
    target_id = "22222222-2222-4222-8222-000000000009"
    session = _mock_session([_TEST_CATEGORY_ROW])
    hops = iter(
        [
            {
                "id": _TEST_CATEGORY_ID,
                "is_archived": True,
                "group_is_archived": False,
                "merged_into_category_id": target_id,
            },
            {
                "id": target_id,
                "is_archived": False,
                "group_is_archived": False,
                "merged_into_category_id": None,
            },
        ]
    )
    original = session.execute.side_effect

    async def _execute(statement: Any, *args: Any, **kwargs: Any) -> MagicMock:
        sql = str(getattr(statement, "text", statement))
        if ":category_id" in sql:
            result = MagicMock()
            row = MagicMock()
            row._mapping = next(hops)
            result.first.return_value = row
            return result
        return await original(statement, *args, **kwargs)

    session.execute = AsyncMock(side_effect=_execute)
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer()),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    assert str(result.category_id) == target_id
    assert _decision(session)["cat_id"] == target_id


async def test_category_archived_since_the_candidate_load_is_not_written() -> None:
    session = _mock_session(
        [_TEST_CATEGORY_ROW],
        resolved=SimpleNamespace(
            id=_TEST_CATEGORY_ID,
            is_archived=True,
            group_is_archived=False,
            merged_into_category_id=None,
        ),
    )
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer()),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    assert result.category_id is None
    assert result.reason_code == STALE_CATEGORY_REASON_CODE
    assert result.confidence == 0.0
    assert _decision(session)["cat_id"] is None


# ---------------------------------------------------------------------------
# Budget aus dem geladenen Fenster statt einer Zahl im Code
# ---------------------------------------------------------------------------


def test_no_fixed_answer_budget_is_configured_anymore() -> None:
    """Frueher AI_MAX_TOKENS=4000 als Rueckfall. Das Antwortbudget kommt jetzt
    aus dem geladenen Fenster - eine Umgebungsvorgabe dafuer gibt es nicht."""
    fields = Settings.model_fields
    assert "ai_max_tokens" not in fields
    assert fields["ai_discovery_timeout_seconds"].default >= 2.0


async def test_requests_get_their_budget_from_the_loaded_window() -> None:
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer()),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    call = _call_rows(session)[0]
    sent = post.await_args_list[1].kwargs["json"]["max_tokens"]
    # max_tokens = W - E - A: der ganze freie Platz, protokolliert mit Budgetzeile.
    assert call["window_tokens"] == _LOADED_WINDOW
    assert sent == _LOADED_WINDOW - call["input_tokens"] - call["allowance"]
    assert call["max_tokens"] == sent
    assert call["reserve"] == _LOADED_WINDOW // 4


async def test_truncated_answer_is_reported_without_escalation() -> None:
    """Leerer content + finish_reason 'length' ist kein Schemabruch, sondern
    ein aufgebrauchtes Antwortbudget. Ein "groesseres" Budget gibt es nicht -
    es ist bereits der ganze freie Platz des Fensters. Deshalb kein zweiter
    Versuch und kein automatisches "ohne Denken" (E1), sondern ein benannter
    Grund mit Hinweis auf die zustaendige Einstellung."""
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _truncated_response(),
            _truncated_response(),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    assert result.category_id is None
    assert result.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    # 1x Canary + 1x Klassifikation - und danach Schluss.
    assert post.await_count == 2
    rows = _call_rows(session)
    assert len(rows) == 1
    assert rows[0]["reason"] == reasons.MODEL_BUDGET_EXHAUSTED
    assert "Reasoning" in rows[0]["err"]
    assert _writes(session, "classification_decisions") == []


async def test_reasoning_off_is_sent_as_reasoning_effort_none() -> None:
    """Gemessen an gemma-4-26b: mit Reasoning verbraucht ein Klassifikations-
    aufruf das ganze Budget, ohne Reasoning 39 Tokens. Der Schalter muss also
    wirklich am Draht ankommen."""
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer()),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
        patch(
            "app.services.ai_settings_service.resolve",
            AsyncMock(return_value=_resolved(reasoning="off", temperature=0.1)),
        ),
    ):
        await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    for call in post.await_args_list:
        assert call.kwargs["json"]["reasoning_effort"] == "none"


async def test_reasoning_unset_sends_no_parameter_at_all() -> None:
    """None heisst Modellstandard - kein Wert wird erfunden."""
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer()),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
        patch(
            "app.services.ai_settings_service.resolve",
            AsyncMock(return_value=_resolved(reasoning=None)),
        ),
    ):
        await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    for call in post.await_args_list:
        assert "reasoning_effort" not in call.kwargs["json"]


async def test_reasoning_on_is_mapped_to_supported_transport_value() -> None:
    """Die UI bleibt bei An/Aus; LM Studios OpenAI-Endpunkt erhaelt fuer An
    die akzeptierte Effort-Stufe high."""
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer()),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
        patch(
            "app.services.ai_settings_service.resolve",
            AsyncMock(return_value=_resolved(reasoning="on")),
        ),
    ):
        await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    for call in post.await_args_list:
        assert call.kwargs["json"]["reasoning_effort"] == "high"


async def test_disabled_ai_classifies_nothing_and_says_so() -> None:
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock()
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
        patch(
            "app.services.ai_settings_service.resolve",
            AsyncMock(return_value=_resolved(is_enabled=False)),
        ),
    ):
        result = await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    assert post.await_count == 0
    assert result.reason_code == reasons.AI_DISABLED
    row = _call_rows(session)[0]
    assert row["status"] == "refused"
    assert row["reason"] == reasons.AI_DISABLED


# ---------------------------------------------------------------------------
# Echte Tokenwerte statt einer Schaetzung
# ---------------------------------------------------------------------------


def test_usage_extraction_reads_all_three_reported_values() -> None:
    usage = extract_usage_tokens({"usage": _LM_STUDIO_USAGE})
    assert usage.prompt_tokens == 7123
    assert usage.completion_tokens == 384
    assert usage.reasoning_tokens == 311


def test_usage_extraction_keeps_missing_values_at_none_instead_of_zero() -> None:
    """Eine 0 waere eine Behauptung, die die Antwort nicht deckt."""
    assert extract_usage_tokens({}) == UsageTokens()
    assert extract_usage_tokens({"usage": {}}) == UsageTokens()
    teilweise = extract_usage_tokens({"usage": {"prompt_tokens": 12, "completion_tokens": 3}})
    assert teilweise == UsageTokens(prompt_tokens=12, completion_tokens=3, reasoning_tokens=None)


def test_usage_extraction_rejects_values_that_are_not_real_counts() -> None:
    """bool ist in Python ein int - ohne diese Pruefung landete ein true als
    'ein Token' in der Auswertung."""
    unbrauchbar = extract_usage_tokens(
        {
            "usage": {
                "prompt_tokens": True,
                "completion_tokens": "384",
                "completion_tokens_details": {"reasoning_tokens": -5},
            }
        }
    )
    assert unbrauchbar == UsageTokens()
    assert extract_usage_tokens({"usage": {"prompt_tokens": 7123.0}}).prompt_tokens == 7123


async def test_classify_writes_reported_usage_into_llm_calls() -> None:
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer(), usage=_LM_STUDIO_USAGE),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    params = _call_rows(session)[0]
    assert params["ptok"] == 7123
    assert params["ctok"] == 384
    assert params["rtok"] == 311
    # Die Budgetschaetzung der Eingabe steht daneben und ist etwas anderes als
    # der gemeldete Wert.
    assert params["tok"] > 0
    assert params["tok"] != params["ptok"]
    # Gemessen: die Messung des Modells wird aus prompt_tokens fortgeschrieben.
    measured = _writes(session, "ai_model_measurements")
    assert measured and measured[0][1]["prompt"] == 7123


async def test_classify_without_usage_block_writes_null_tokens() -> None:
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _chat_response(200, _classify_answer()),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    params = _call_rows(session)[0]
    assert params["ptok"] is None
    assert params["ctok"] is None
    assert params["rtok"] is None
    assert params["tok"] > 0
    assert _writes(session, "ai_model_measurements") == []


async def test_truncated_answer_still_records_its_reported_usage() -> None:
    """Der teuerste Aufruf darf nicht der einzige ohne Messwert sein."""
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            _truncated_response(usage=_LM_STUDIO_USAGE),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    assert result.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    row = _call_rows(session)[0]
    assert row["ptok"] == 7123
    assert row["rtok"] == 311


async def test_timeout_leaves_token_columns_empty() -> None:
    """Ohne Antwort gibt es nichts zu melden - NULL statt 0."""
    session = _mock_session([_TEST_CATEGORY_ROW])
    post = AsyncMock(
        side_effect=[
            _chat_response(200, _canary_answer()),
            httpx.TimeoutException("zu lange"),
        ]
    )
    with (
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_models_response())),
        patch("httpx.AsyncClient.post", post),
    ):
        result = await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE Markt GmbH", "Einkauf", -1500
        )

    assert result.reason_code == "llm_timeout"
    params = _call_rows(session)[0]
    assert params["status"] == "timeout"
    assert params["ptok"] is None
    assert params["ctok"] is None
    assert params["rtok"] is None


async def test_classify_logs_prompt_version_when_no_model_is_loaded() -> None:
    session = _mock_session([_TEST_CATEGORY_ROW])
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=OSError("refused"))):
        result = await classify_transaction_with_llm(
            session, uuid.uuid4(), None, "REWE", "Einkauf", -1500
        )

    assert result.category_id is None
    assert result.reason_code == "model_unavailable"
    row = _call_rows(session)[0]
    assert row["pv"] == _EXPECTED_PROMPT_VERSION
    assert row["status"] == "no_model"
