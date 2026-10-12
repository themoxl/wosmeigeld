"""Unit-Tests der Klassifikation im Worker (worker/ai/classifier.py).

Seit Etappe 0 ist der Worker nur noch Orchestrierung: Vertrag, Transport,
Fenster, relatives Budget und Messung kommen aus dem KI-Kern
(services/api/app/llm). Geprueft wird hier, was der Batch-Lauf davon sieht:
PII-Maskierung, Kandidatenliste nach Passung ins Fenster, strukturierte
Ausgabe samt bewusstem Capability-Fallback, Happy Path, Schema-Validierung mit
GENAU EINEM Korrekturversuch, Timeout- vs. Verbindungsfehler, benannte Gruende
statt Eskalation, Reasoning und Sampling am Draht sowie Discovery mit Fenster.
LM-Studio-Aufrufe werden ueber unittest.mock auf httpx.AsyncClient gemockt -
kein echtes LM Studio, keine echte DB-Session (eine Attrappe protokolliert die
Statements)."""

import json
import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from app.llm import reasons
from app.llm.classification import (
    ClassificationSchemaError,
    select_variant,
)
from app.llm.discovery import Target
from app.llm.measurements import start_measurement
from app.llm.settings import LLMSettings
from app.llm.window import WindowInfo

from worker.ai.classifier import (
    CANDIDATE_SQL,
    MAX_SCHEMA_ATTEMPTS,
    NO_MATCH,
    NO_MATCH_REASON_CODE,
    PROMPT_VERSION,
    STALE_CATEGORY_REASON_CODE,
    CategoryCandidates,
    ClassificationResult,
    UsageTokens,
    build_candidate_entries,
    build_candidate_variants,
    build_classification_json_schema,
    build_system_prompt,
    classify_transaction,
    extract_usage_tokens,
    mask_pii_text,
    parse_classification_content,
    probe_model_capability,
    prompt_version_for,
    resolve_lm_studio_endpoint,
)

CATEGORY_ID = str(uuid4())
TAXONOMY_VERSION = 1
WINDOW = 32768
BASE_URL = "http://localhost:1234/v1"
CATEGORIES: list[dict[str, Any]] = [
    {
        "id": CATEGORY_ID,
        "path": "Variable Kosten · Lebensmittel",
        "key": "groceries",
        "type": "expense",
        "bucket": "needs",
        "synonyms": ["Supermarkt", "Discounter"],
    }
]
VALID_CAT_IDS = {CATEGORY_ID}
CANDIDATES = CategoryCandidates(
    entries=CATEGORIES, valid_ids=VALID_CAT_IDS, taxonomy_version=TAXONOMY_VERSION
)
EXPECTED_PROMPT_VERSION = prompt_version_for(TAXONOMY_VERSION)

_ACTIVE_CATEGORY_ROW = SimpleNamespace(
    id=CATEGORY_ID, is_archived=False, merged_into_category_id=None, group_is_archived=False
)


def _settings(**overrides: Any) -> LLMSettings:
    """Einstellungen wie aus der Datenbank: die Werte unten sind die
    Spaltenvorgaben aus 0001_schema.sql, hier als Testdaten."""
    values: dict[str, Any] = {
        "is_enabled": True,
        "min_confidence": 0.85,
        "custom_base_url": None,
        "model_name": None,
        "temperature": 1.0,
        "request_timeout_seconds": 120.0,
        "answer_reserve_share": 0.25,
        "result_page_share": 0.1,
        "run_time_limit_minutes": 20,
        "start_chars_per_token": 3.0,
        "start_estimate_error": 0.15,
        "context_line_enabled": True,
    }
    values.update(overrides)
    return LLMSettings(**values)


def _target(model: str = "test-model", window: int | None = WINDOW) -> Target:
    return Target(
        base_url=BASE_URL,
        model=model,
        window=WindowInfo(
            loaded=window, cap=None, effective=window, source="/api/v1/models" if window else None
        ),
    )


def _make_session(resolve_row: Any = _ACTIVE_CATEGORY_ROW) -> AsyncMock:
    """Session-Attrappe: beantwortet die Vor-Schreib-Aufloesung und die
    (leere) Messung, protokolliert alle uebrigen Statements."""

    async def _execute(statement: Any, params: Any = None, *args: Any, **kwargs: Any) -> MagicMock:
        result = MagicMock()
        sql = str(statement)
        if "merged_into_category_id" in sql:
            result.first.return_value = resolve_row
        elif "from ai_model_measurements" in sql:
            result.first.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    return session


_LM_STUDIO_USAGE: dict[str, Any] = {
    "prompt_tokens": 7123,
    "completion_tokens": 384,
    "total_tokens": 7507,
    "completion_tokens_details": {"reasoning_tokens": 311},
}


def _llm_response(
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
    if usage is not None:
        payload["usage"] = usage
    resp.json.return_value = payload
    return resp


def _truncated_response(usage: dict[str, Any] | None = None) -> MagicMock:
    """Antwort eines Reasoning-Modells mit aufgebrauchtem Budget: alles floss
    in reasoning_content, content blieb leer, finish_reason 'length'."""
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


def _valid_json(category_id: str = CATEGORY_ID, confidence: float = 0.9) -> str:
    return (
        f'{{"payee_name": "REWE", "category_id": "{category_id}", '
        f'"confidence": {confidence}, "reason_code": "merchant_and_context_match"}}'
    )


async def _classify(
    session: AsyncMock | None = None, **overrides: Any
) -> tuple[AsyncMock, ClassificationResult]:
    session = session or _make_session()
    defaults: dict[str, Any] = {
        "session": session,
        "household_id": uuid4(),
        "transaction_id": None,
        "payee_raw": "REWE Markt GmbH",
        "purpose": "Einkauf Lebensmittel",
        "amount_minor": -1500,
        "target": _target(),
        "candidates": CANDIDATES,
        "supports_json_schema": True,
        "settings": _settings(),
    }
    defaults.update(overrides)
    result = await classify_transaction(**defaults)
    return session, result


def _request_bodies(post_mock: AsyncMock) -> list[dict[str, Any]]:
    return [call.kwargs["json"] for call in post_mock.await_args_list]


def _writes(session: AsyncMock, table: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Schreibende Statements in Reihenfolge (INSERT und UPDATE), optional nur
    fuer eine Tabelle."""
    written: list[tuple[str, dict[str, Any]]] = []
    for call in session.execute.await_args_list:
        sql = str(call.args[0].text)
        if not sql.lstrip().lower().startswith(("insert", "update")):
            continue
        if table is not None and table not in sql:
            continue
        params = call.args[1]
        assert isinstance(params, dict)
        written.append((sql, params))
    return written


def _calls(session: AsyncMock) -> list[dict[str, Any]]:
    """Die llm_calls-Zeilen (eine je Modellaufruf)."""
    return [p for sql, p in _writes(session, "llm_calls") if sql.lstrip().startswith("insert")]


def _outcome_updates(session: AsyncMock) -> list[dict[str, Any]]:
    """Nachtraege, mit denen die Klassifikation eine Antwort verworfen hat."""
    return [p for sql, p in _writes(session, "llm_calls") if sql.lstrip().startswith("update")]


def _decisions(session: AsyncMock) -> list[dict[str, Any]]:
    return [p for _sql, p in _writes(session, "classification_decisions")]


# ---------------------------------------------------------------------------
# PII-Maskierung
# ---------------------------------------------------------------------------


def test_pii_masking_redacts_ibans_and_account_numbers() -> None:
    masked = mask_pii_text("Ueberweisung von DE12345678901234567890 an Miete")
    assert "DE12345678901234567890" not in masked
    assert "DE12************7890" in masked


def test_pii_masking_handles_multiple_ibans_and_account_numbers_in_one_string() -> None:
    text = (
        "Transfer von DE12345678901234567890 an DE98765432109876543210, "
        "Referenz Kontonummer 1234567890123456"
    )
    masked = mask_pii_text(text)
    assert "DE12345678901234567890" not in masked
    assert "DE98765432109876543210" not in masked
    assert "1234567890123456" not in masked


def test_pii_masking_handles_empty_and_none_input() -> None:
    assert mask_pii_text(None) == ""
    assert mask_pii_text("") == ""


async def test_structured_path_sends_only_masked_payload() -> None:
    """Das Schema beeinflusst nur die Antwortform, nie den gesendeten Inhalt."""
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        await _classify(
            payee_raw="Ueberweisung DE12345678901234567890",
            purpose="Kontonummer 1234567890123456",
        )

    user_prompt = _request_bodies(post)[0]["messages"][1]["content"]
    assert "DE12345678901234567890" not in user_prompt
    assert "1234567890123456" not in user_prompt
    assert "[MASKED]" in user_prompt


# ---------------------------------------------------------------------------
# Kandidatenliste nach Passung ins Fenster (statt fester Zeichengrenze)
# ---------------------------------------------------------------------------


def _candidate_row(**overrides: Any) -> SimpleNamespace:
    row = {
        "id": CATEGORY_ID,
        "name": "Lebensmittel",
        "group_name": "Variable Kosten",
        "semantic_key": "groceries",
        "description": "Wocheneinkauf im Supermarkt, Discounter und Bioladen.",
        "synonyms": ["Supermarkt", "Discounter"],
        "default_type": "expense",
        "budget_bucket": "needs",
        "taxonomy_version": 1,
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def test_candidate_entry_carries_path_key_synonyms_type_and_bucket() -> None:
    entry = build_candidate_entries([_candidate_row()])[0]
    assert entry["id"] == CATEGORY_ID
    assert entry["path"] == "Variable Kosten · Lebensmittel"
    assert entry["key"] == "groceries"
    assert entry["type"] == "expense"
    assert entry["bucket"] == "needs"
    assert entry["synonyms"] == ["Supermarkt", "Discounter"]
    assert "Wocheneinkauf" in entry["description"]


def test_candidate_entry_omits_empty_fields() -> None:
    entry = build_candidate_entries(
        [_candidate_row(semantic_key=None, description=None, synonyms=[], budget_bucket=None)]
    )[0]
    assert set(entry) == {"id", "path", "type"}


def _large_candidates() -> CategoryCandidates:
    rows = [
        _candidate_row(
            id=str(uuid4()),
            name=f"Kategorie {i}",
            description="B" * 400,
            synonyms=[f"Synonym {i}-{j} " + "s" * 30 for j in range(6)],
        )
        for i in range(80)
    ]
    variants = build_candidate_variants(rows)
    return CategoryCandidates(
        entries=variants[0],
        valid_ids={entry["id"] for entry in variants[0]},
        taxonomy_version=1,
        variants=variants,
    )


def _variant_for(window: int) -> list[dict[str, Any]]:
    settings = _settings()
    return select_variant(
        _large_candidates(),
        "Buchung: Empfaenger/Haendler: 'REWE', Verwendungszweck: 'x', Betrag_Cent: -1",
        target=_target(window=window),
        settings=settings,
        measurement=start_measurement(settings),
    )


def test_the_candidate_list_shrinks_by_what_fits_the_window_never_by_dropping_candidates() -> None:
    """Statt einer festen Zeichengrenze entscheidet das geladene Fenster: voll,
    ohne Beschreibungen, ohne Synonyme. Kandidaten fallen nie weg - eine
    fehlende Kategorie waere ein erzwungener Fehltreffer."""
    full = _variant_for(262144)
    medium = _variant_for(32768)
    small = _variant_for(16384)

    assert len(full) == len(medium) == len(small) == 80
    assert all("description" in entry for entry in full)
    assert all("description" not in entry and "synonyms" in entry for entry in medium)
    assert all("synonyms" not in entry for entry in small)
    assert len(json.dumps(small)) < len(json.dumps(medium)) < len(json.dumps(full))


async def test_a_list_that_fits_no_window_is_refused_by_name() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify(
            candidates=_large_candidates(), target=_target(window=4096)
        )

    assert post.await_count == 0
    assert result.reason_code == reasons.MODEL_INPUT_TOO_LARGE
    assert _calls(session)[0]["status"] == "refused"


def test_candidate_query_excludes_archived_categories_and_archived_groups() -> None:
    assert "not c.is_archived" in CANDIDATE_SQL
    assert "not cg.is_archived" in CANDIDATE_SQL
    # Der Worker laeuft ohne RLS - der Haushaltsfilter muss ausdruecklich
    # fuer Kategorie UND Gruppe im Statement stehen.
    assert "c.household_id = :household_id" in CANDIDATE_SQL
    assert "cg.household_id = c.household_id" in CANDIDATE_SQL


def test_prompt_version_carries_contract_and_taxonomy_version() -> None:
    assert prompt_version_for(1) == f"{PROMPT_VERSION}-tax1"
    assert prompt_version_for(None) == f"{PROMPT_VERSION}-tax0"


def test_system_prompt_states_that_no_match_is_correct_and_forbids_catch_all() -> None:
    prompt = build_system_prompt(CATEGORIES, TAXONOMY_VERSION)
    assert NO_MATCH in prompt
    assert "Erfinde niemals" in prompt
    assert "Auffangbecken" in prompt
    assert "Freizeit" in prompt
    assert "Taxonomieversion des Haushalts: 1" in prompt
    assert "Variable Kosten · Lebensmittel" in prompt
    assert "Discounter" in prompt


# ---------------------------------------------------------------------------
# no_match als vollwertige Antwort (Leitplanke 8)
# ---------------------------------------------------------------------------


def _no_match_json(confidence: float = 0.2) -> str:
    return (
        f'{{"payee_name": "Unbekannt", "category_id": "{NO_MATCH}", '
        f'"confidence": {confidence}, "reason_code": "unclear_merchant"}}'
    )


async def test_no_match_is_a_successful_call_with_a_distinct_reason_code() -> None:
    with patch(
        "httpx.AsyncClient.post", AsyncMock(return_value=_llm_response(200, _no_match_json()))
    ):
        session, result = await _classify()

    assert result.category_id is None
    assert result.reason_code == NO_MATCH_REASON_CODE
    call = _calls(session)[0]
    assert call["status"] == "success"
    assert call["pv"] == EXPECTED_PROMPT_VERSION
    decision = _decisions(session)[0]
    assert decision["cat_id"] is None
    assert decision["reason"] == NO_MATCH_REASON_CODE


async def test_no_match_is_not_retried() -> None:
    post = AsyncMock(return_value=_llm_response(200, _no_match_json()))
    with patch("httpx.AsyncClient.post", post):
        await _classify()

    assert post.await_count == 1


def test_parse_accepts_json_null_as_no_match() -> None:
    parsed = parse_classification_content(
        '{"payee_name": "X", "category_id": null, "confidence": 0.1, "reason_code": "x"}',
        VALID_CAT_IDS,
    )
    assert parsed["category_id"] == NO_MATCH


def test_parse_rejects_a_missing_category_id_field() -> None:
    with pytest.raises(ClassificationSchemaError, match="category_id fehlt"):
        parse_classification_content('{"payee_name": "X"}', VALID_CAT_IDS)


# ---------------------------------------------------------------------------
# Vor-Schreib-Aufloesung (Zwilling von category_service.resolve_category_id)
# ---------------------------------------------------------------------------


async def test_merge_tombstone_is_canonicalised_before_the_decision_is_written() -> None:
    target_id = str(uuid4())
    rows = [
        SimpleNamespace(
            id=CATEGORY_ID,
            is_archived=True,
            merged_into_category_id=target_id,
            group_is_archived=False,
        ),
        SimpleNamespace(
            id=target_id, is_archived=False, merged_into_category_id=None, group_is_archived=False
        ),
    ]
    calls = {"n": 0}

    async def _execute(statement: Any, params: Any = None, *a: Any, **k: Any) -> MagicMock:
        result = MagicMock()
        if "merged_into_category_id" in str(statement):
            result.first.return_value = rows[min(calls["n"], len(rows) - 1)]
            calls["n"] += 1
        elif "from ai_model_measurements" in str(statement):
            result.first.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_llm_response(200, _valid_json()))):
        _, result = await _classify(session=session)

    assert str(result.category_id) == target_id
    assert _decisions(session)[0]["cat_id"] == target_id


async def test_category_archived_during_the_run_is_not_written() -> None:
    archived = SimpleNamespace(
        id=CATEGORY_ID, is_archived=True, merged_into_category_id=None, group_is_archived=False
    )
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_llm_response(200, _valid_json()))):
        session, result = await _classify(session=_make_session(resolve_row=archived))

    assert result.category_id is None
    assert result.reason_code == STALE_CATEGORY_REASON_CODE
    assert result.confidence == 0.0
    assert _decisions(session)[0]["cat_id"] is None


async def test_category_in_an_archived_group_is_not_written() -> None:
    hidden = SimpleNamespace(
        id=CATEGORY_ID, is_archived=False, merged_into_category_id=None, group_is_archived=True
    )
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_llm_response(200, _valid_json()))):
        _, result = await _classify(session=_make_session(resolve_row=hidden))

    assert result.category_id is None
    assert result.reason_code == STALE_CATEGORY_REASON_CODE


# ---------------------------------------------------------------------------
# Strukturierte Ausgabe (response_format) + Capability-Fallback
# ---------------------------------------------------------------------------


def test_json_schema_restricts_category_id_to_allowed_enum_plus_no_match() -> None:
    schema = build_classification_json_schema(VALID_CAT_IDS)
    assert schema["name"] == "transaction_classification"
    properties = schema["schema"]["properties"]
    assert properties["category_id"]["enum"] == [*sorted(VALID_CAT_IDS), NO_MATCH]
    assert schema["schema"]["required"] == [
        "payee_name",
        "category_id",
        "confidence",
        "reason_code",
    ]


async def test_classify_sends_response_format_when_model_supports_json_schema() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        await _classify(supports_json_schema=True)

    body = _request_bodies(post)[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["properties"]["category_id"][
        "enum"
    ] == [CATEGORY_ID, NO_MATCH]


async def test_classify_omits_response_format_when_capability_is_absent() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        _, result = await _classify(supports_json_schema=False)

    assert "response_format" not in _request_bodies(post)[0]
    assert result.category_id == UUID(CATEGORY_ID)


async def test_classify_falls_back_to_prompt_path_when_response_format_is_rejected() -> None:
    """HTTP 400 ohne Ueberlaufmeldung = Parameter nicht unterstuetzt:
    bewusster, protokollierter Wechsel auf Prompt+Parse."""
    rejected = _llm_response(400, "")
    rejected.json.return_value = {"error": "response_format not supported"}
    post = AsyncMock(side_effect=[rejected, _llm_response(200, _valid_json())])
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify(supports_json_schema=True)

    bodies = _request_bodies(post)
    assert "response_format" in bodies[0]
    assert "response_format" not in bodies[1]
    assert result.category_id == UUID(CATEGORY_ID)

    calls = _calls(session)
    assert [call["reason"] for call in calls] == [reasons.MODEL_SCHEMA_UNSUPPORTED, None]
    capability = _writes(session, "ai_model_capabilities")[0][1]
    assert capability["sjs"] is False
    assert "capability_fallback" in capability["err"]


async def test_a_context_overflow_is_not_recorded_as_missing_schema_capability() -> None:
    """Befund: frueher galt jeder HTTP 400 als "kann kein Schema" und wurde so
    persistiert - auch "Context size has been exceeded"."""
    overflow = _llm_response(400, "")
    overflow.json.return_value = {"error": "Context size has been exceeded."}
    post = AsyncMock(return_value=overflow)
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify(supports_json_schema=True)

    assert post.await_count == 1
    assert result.reason_code == reasons.MODEL_CONTEXT_OVERFLOW
    assert _writes(session, "ai_model_capabilities") == []


async def test_classify_does_not_degrade_capability_on_server_error() -> None:
    post = AsyncMock(return_value=_llm_response(500, ""))
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify(supports_json_schema=True)

    assert post.await_count == 1
    assert result.reason_code == "llm_execution_error"
    assert len(_calls(session)) == 1
    assert _writes(session, "ai_model_capabilities") == []


# ---------------------------------------------------------------------------
# Capability-Canary (probe_model_capability)
# ---------------------------------------------------------------------------

_CANARY_ANSWER = (
    '{"payee_name": "REWE", "category_id": "00000000-0000-4000-8000-000000000001", '
    '"confidence": 0.9, "reason_code": "merchant_and_context_match"}'
)


async def test_canary_passes_only_with_schema_conform_answer() -> None:
    post = AsyncMock(return_value=_llm_response(200, _CANARY_ANSWER))
    with patch("httpx.AsyncClient.post", post):
        canary = await probe_model_capability(_target(), _settings())

    assert canary.passed is True
    assert canary.supports_json_schema is True
    assert post.await_count == 1


async def test_canary_http_200_without_valid_json_is_not_enough() -> None:
    post = AsyncMock(return_value=_llm_response(200, "Klar, ich helfe gern!"))
    with patch("httpx.AsyncClient.post", post):
        canary = await probe_model_capability(_target(), _settings())

    assert canary.passed is False
    assert canary.supports_json_schema is False
    # Beide Wege probiert: strukturiert und Prompt-Fallback.
    assert post.await_count == 2


async def test_canary_reports_prompt_fallback_when_schema_is_rejected() -> None:
    post = AsyncMock(side_effect=[_llm_response(400, ""), _llm_response(200, _CANARY_ANSWER)])
    with patch("httpx.AsyncClient.post", post):
        canary = await probe_model_capability(_target(), _settings())

    assert canary.passed is True
    assert canary.supports_json_schema is False


async def test_canary_does_not_pass_when_the_model_answers_no_match() -> None:
    post = AsyncMock(
        return_value=_llm_response(
            200,
            '{"payee_name": "REWE", "category_id": "no_match", '
            '"confidence": 0.4, "reason_code": "unclear"}',
        )
    )
    with patch("httpx.AsyncClient.post", post):
        canary = await probe_model_capability(_target(), _settings())

    assert canary.passed is False
    assert canary.supports_json_schema is False


async def test_truncated_canary_fails_by_name_without_a_second_try() -> None:
    post = AsyncMock(return_value=_truncated_response())
    with patch("httpx.AsyncClient.post", post):
        canary = await probe_model_capability(_target(), _settings())

    assert canary.passed is False
    assert canary.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    assert post.await_count == 1


async def test_the_batch_canary_uses_the_real_candidate_list_and_reads_the_measurement() -> None:
    session = _make_session()
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        canary = await probe_model_capability(
            _target(), _settings(), CANDIDATES, session=session, household_id=uuid4()
        )

    assert canary.passed is True
    system = _request_bodies(post)[0]["messages"][0]["content"]
    assert "Variable Kosten · Lebensmittel" in system
    # Gelesen ja (Budget), geschrieben nein: der Vorabtest misst nicht und
    # protokolliert nicht.
    assert any(
        "from ai_model_measurements" in str(call.args[0])
        for call in session.execute.await_args_list
    )
    assert _writes(session) == []


# ---------------------------------------------------------------------------
# classify_transaction: Happy Path, Budget, Messung
# ---------------------------------------------------------------------------


async def test_classify_happy_path_returns_parsed_suggestion() -> None:
    with patch(
        "httpx.AsyncClient.post",
        AsyncMock(return_value=_llm_response(200, _valid_json())),
    ):
        session, result = await _classify()

    assert result.category_id == UUID(CATEGORY_ID)
    assert result.confidence == 0.9
    assert result.reason_code == "merchant_and_context_match"
    assert result.source == "ai"
    assert result.payee_name == "REWE"
    assert len(_calls(session)) == 1
    assert len(_decisions(session)) == 1


async def test_the_answer_budget_comes_from_the_loaded_window() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        session, _ = await _classify(target=_target(window=8192))

    call = _calls(session)[0]
    sent = _request_bodies(post)[0]["max_tokens"]
    assert call["window_tokens"] == 8192
    assert call["reserve"] == 2048
    assert sent == 8192 - call["input_tokens"] - call["allowance"]
    assert call["max_tokens"] == sent


async def test_classify_writes_prompt_version_and_ai_task_into_llm_calls() -> None:
    task_id = uuid4()
    with patch(
        "httpx.AsyncClient.post",
        AsyncMock(return_value=_llm_response(200, _valid_json())),
    ):
        session, _ = await _classify(ai_task_id=task_id)

    call = _calls(session)[0]
    assert call["pv"] == EXPECTED_PROMPT_VERSION
    assert call["stage"] == "classify"
    assert call["ai_task_id"] == str(task_id)
    assert _decisions(session)[0]["ai_task_id"] == str(task_id)


def test_usage_extraction_reads_all_three_reported_values() -> None:
    usage = extract_usage_tokens({"usage": _LM_STUDIO_USAGE})
    assert usage.prompt_tokens == 7123
    assert usage.completion_tokens == 384
    assert usage.reasoning_tokens == 311


def test_usage_extraction_keeps_missing_values_at_none_instead_of_zero() -> None:
    assert extract_usage_tokens({}) == UsageTokens()
    assert extract_usage_tokens({"usage": {}}) == UsageTokens()
    teilweise = extract_usage_tokens({"usage": {"prompt_tokens": 12, "completion_tokens": 3}})
    assert teilweise == UsageTokens(prompt_tokens=12, completion_tokens=3, reasoning_tokens=None)


async def test_classify_writes_reported_usage_and_measures() -> None:
    with patch(
        "httpx.AsyncClient.post",
        AsyncMock(return_value=_llm_response(200, _valid_json(), usage=_LM_STUDIO_USAGE)),
    ):
        session, _ = await _classify()

    params = _calls(session)[0]
    assert params["ptok"] == 7123
    assert params["ctok"] == 384
    assert params["rtok"] == 311
    assert params["tok"] > 0 and params["tok"] != params["ptok"]
    measurement = _writes(session, "ai_model_measurements")[0][1]
    assert measurement["prompt"] == 7123
    assert measurement["model"] == "test-model"


async def test_classify_without_usage_block_writes_null_tokens() -> None:
    with patch(
        "httpx.AsyncClient.post",
        AsyncMock(return_value=_llm_response(200, _valid_json())),
    ):
        session, _ = await _classify()

    params = _calls(session)[0]
    assert params["ptok"] is None
    assert params["ctok"] is None
    assert params["rtok"] is None
    assert _writes(session, "ai_model_measurements") == []


async def test_classify_missing_fields_default_sensibly() -> None:
    content = f'{{"category_id": "{CATEGORY_ID}"}}'
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_llm_response(200, content))):
        _, result = await _classify()

    assert result.category_id == UUID(CATEGORY_ID)
    assert result.confidence == 0.5
    assert result.reason_code == "merchant_and_context_match"


async def test_classify_confidence_is_clamped_to_range() -> None:
    with patch(
        "httpx.AsyncClient.post",
        AsyncMock(return_value=_llm_response(200, _valid_json(confidence=1.7))),
    ):
        _, high = await _classify()
    with patch(
        "httpx.AsyncClient.post",
        AsyncMock(return_value=_llm_response(200, _valid_json(confidence=-0.3))),
    ):
        _, low = await _classify()

    assert high.confidence == 1.0
    assert low.confidence == 0.0


# ---------------------------------------------------------------------------
# Schema-Fehler + GENAU EIN Korrekturversuch
# ---------------------------------------------------------------------------


def test_exactly_one_controlled_retry_is_configured() -> None:
    assert MAX_SCHEMA_ATTEMPTS == 2


def test_parse_rejects_unknown_category_id() -> None:
    with pytest.raises(ClassificationSchemaError, match="Unbekannte category_id"):
        parse_classification_content(_valid_json(category_id=str(uuid4())), VALID_CAT_IDS)


async def test_classify_malformed_json_falls_back_after_exhausting_retries() -> None:
    post = AsyncMock(return_value=_llm_response(200, "das ist kein JSON"))
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify()

    assert result.category_id is None
    assert result.confidence == 0.0
    assert result.reason_code == "llm_schema_invalid"
    assert post.await_count == MAX_SCHEMA_ATTEMPTS == 2
    # Je Versuch eine Zeile, beide nachtraeglich als unbrauchbar markiert.
    assert len(_calls(session)) == 2
    updates = _outcome_updates(session)
    assert [update["reason"] for update in updates] == [reasons.MODEL_OUTPUT_UNUSABLE] * 2
    assert _decisions(session) == []


async def test_classify_invalid_category_id_triggers_fallback_after_retries() -> None:
    unknown_category = str(uuid4())
    with patch(
        "httpx.AsyncClient.post",
        AsyncMock(return_value=_llm_response(200, _valid_json(category_id=unknown_category))),
    ):
        _, result = await _classify()

    assert result.category_id is None
    assert result.confidence == 0.0
    assert result.reason_code == "llm_schema_invalid"


async def test_classify_retries_then_succeeds_on_second_attempt() -> None:
    bad = _llm_response(200, "kaputtes JSON")
    good = _llm_response(200, _valid_json())
    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=[bad, good])):
        session, result = await _classify()

    assert result.category_id == UUID(CATEGORY_ID)
    assert len(_calls(session)) == 2
    assert len(_decisions(session)) == 1


async def test_classify_retry_prompt_contains_correction_hint() -> None:
    post = AsyncMock(
        side_effect=[_llm_response(200, "kaputtes JSON"), _llm_response(200, _valid_json())]
    )
    with patch("httpx.AsyncClient.post", post):
        await _classify()

    bodies = _request_bodies(post)
    assert "HINWEIS" not in bodies[0]["messages"][1]["content"]
    assert "HINWEIS" in bodies[1]["messages"][1]["content"]


# ---------------------------------------------------------------------------
# Netzwerk-/HTTP-Fehler und benannte Gruende (kein Retry)
# ---------------------------------------------------------------------------


async def test_classify_non_200_http_status_treated_as_error_without_retry() -> None:
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_llm_response(500, ""))):
        session, result = await _classify()

    assert result.category_id is None
    assert result.reason_code == "llm_execution_error"
    assert len(_calls(session)) == 1


async def test_classify_timeout_sets_distinct_llm_calls_status() -> None:
    post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify()

    assert result.reason_code == "llm_timeout"
    assert post.await_count == 1
    call = _calls(session)[0]
    assert call["status"] == "timeout"
    assert call["pv"] == EXPECTED_PROMPT_VERSION
    assert call["ptok"] is None


async def test_classify_connection_error_sets_generic_error_status() -> None:
    post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify()

    assert result.reason_code == "llm_execution_error"
    assert post.await_count == 1
    assert _calls(session)[0]["reason"] == reasons.MODEL_UNAVAILABLE
    assert _calls(session)[0]["status"] == "error"


async def test_a_truncated_answer_is_named_and_not_escalated() -> None:
    """Leerer content + finish_reason 'length': das Antwortbudget war der ganze
    freie Platz - ein groesseres gibt es nicht. Kein zweiter Versuch, kein
    automatisches "ohne Denken" (E1), sondern model_budget_exhausted mit
    Hinweis auf die zustaendige Einstellung."""
    post = AsyncMock(return_value=_truncated_response(usage=_LM_STUDIO_USAGE))
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify()

    assert result.category_id is None
    assert result.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    assert post.await_count == 1
    call = _calls(session)[0]
    assert call["reason"] == reasons.MODEL_BUDGET_EXHAUSTED
    assert call["ptok"] == 7123
    assert "Reasoning" in call["err"]


async def test_an_unknown_window_is_refused_without_a_call() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        session, result = await _classify(target=_target(window=None))

    assert post.await_count == 0
    assert result.reason_code == reasons.MODEL_CONTEXT_UNKNOWN
    assert _calls(session)[0]["status"] == "refused"


async def test_disabled_ai_is_refused_without_a_call() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        _, result = await _classify(settings=_settings(is_enabled=False))

    assert post.await_count == 0
    assert result.reason_code == reasons.AI_DISABLED


# ---------------------------------------------------------------------------
# Reasoning und Sampling am Draht
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reasoning", "sent"),
    [("off", "none"), ("on", "high"), ("medium", "medium"), (None, None)],
)
async def test_reasoning_reaches_lm_studio_in_the_batch_run(
    reasoning: str | None, sent: str | None
) -> None:
    """Der Hintergrundlauf sendet denselben Schalter wie der Einzeltest -
    und ohne Einstellung gar nichts (Modellstandard)."""
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        await _classify(settings=_settings(reasoning=reasoning))

    assert _request_bodies(post)[0].get("reasoning_effort") == sent


async def test_gemma_model_profile_reaches_lm_studio_in_the_batch_run() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        await _classify(
            target=_target(model="google/gemma-4-26b-a4b-qat"),
            settings=_settings(sampling_mode="model"),
        )

    body = _request_bodies(post)[0]
    assert body["temperature"] == 1.0
    assert body["top_p"] == 0.95
    assert body["top_k"] == 64
    assert "repeat_penalty" not in body


async def test_qwen36_profile_includes_anti_loop_sampling_in_the_batch_run() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    with patch("httpx.AsyncClient.post", post):
        await _classify(
            target=_target(model="qwen/qwen3.6-35b-a3b"),
            settings=_settings(sampling_mode="model", reasoning="on"),
        )

    body = _request_bodies(post)[0]
    assert body["temperature"] == 1.0
    assert body["top_p"] == 0.95
    assert body["top_k"] == 20
    assert body["min_p"] == 0.0
    assert body["presence_penalty"] == 1.5


async def test_manual_sampling_reaches_lm_studio_verbatim_in_the_batch_run() -> None:
    post = AsyncMock(return_value=_llm_response(200, _valid_json()))
    settings = _settings(
        sampling_mode="manual",
        temperature=0.42,
        top_p=0.81,
        top_k=37,
        repeat_penalty=1.07,
        min_p=0.03,
        presence_penalty=0.25,
    )
    with patch("httpx.AsyncClient.post", post):
        await _classify(settings=settings)

    body = _request_bodies(post)[0]
    assert {
        key: body[key]
        for key in ("temperature", "top_p", "top_k", "repeat_penalty", "min_p", "presence_penalty")
    } == {
        "temperature": 0.42,
        "top_p": 0.81,
        "top_k": 37,
        "repeat_penalty": 1.07,
        "min_p": 0.03,
        "presence_penalty": 0.25,
    }


# ---------------------------------------------------------------------------
# Discovery mit Fenster (resolve_lm_studio_endpoint)
# ---------------------------------------------------------------------------


def _get_by_url(routes: dict[str, Any]) -> AsyncMock:
    """GET-Attrappe nach URL-Endung; alles andere ist nicht erreichbar."""

    async def _get(url: str, *args: Any, **kwargs: Any) -> MagicMock:
        for suffix, answer in routes.items():
            if str(url).endswith(suffix) and answer is not None:
                if isinstance(answer, Exception):
                    raise answer
                response = MagicMock(status_code=200)
                response.json.return_value = answer
                return response
        raise httpx.ConnectError("refused")

    return AsyncMock(side_effect=_get)


async def test_resolve_endpoint_reports_the_loaded_window() -> None:
    """Gemeldet wird das GELADENE Fenster, nicht das maximal moegliche."""
    v0 = {
        "data": [
            {
                "id": "google/gemma-4-26b",
                "type": "llm",
                "state": "loaded",
                "max_context_length": 262144,
                "loaded_context_length": 8192,
            }
        ]
    }
    with patch("httpx.AsyncClient.get", _get_by_url({"localhost:1234/api/v0/models": v0})):
        found = await resolve_lm_studio_endpoint(_settings())

    assert found.ready
    assert found.target is not None
    assert found.target.base_url == "http://localhost:1234/v1"
    assert found.target.model == "google/gemma-4-26b"
    assert found.target.window.effective == 8192
    assert found.target.window.max_context == 262144


async def test_resolve_endpoint_names_an_unknown_window() -> None:
    v0 = {"data": [{"id": "qwen/qwen3-8b", "type": "llm", "state": "not-loaded"}]}
    with patch("httpx.AsyncClient.get", _get_by_url({"localhost:1234/api/v0/models": v0})):
        found = await resolve_lm_studio_endpoint(_settings())

    assert not found.ready
    assert found.reason_code == reasons.MODEL_CONTEXT_UNKNOWN
    assert found.target is not None and found.target.model == "qwen/qwen3-8b"
    assert found.message is not None and "Kontext höchstens" in found.message


async def test_resolve_endpoint_never_selects_an_embedding_model() -> None:
    v0 = {
        "data": [
            {"id": "text-embedding-nomic", "type": "embeddings", "state": "loaded"},
            {
                "id": "qwen/qwen3-8b",
                "type": "llm",
                "state": "loaded",
                "loaded_context_length": 16384,
            },
        ]
    }
    with patch("httpx.AsyncClient.get", _get_by_url({"localhost:1234/api/v0/models": v0})):
        found = await resolve_lm_studio_endpoint(_settings())

    assert found.target is not None and found.target.model == "qwen/qwen3-8b"


async def test_a_missing_chosen_model_is_not_silently_replaced() -> None:
    v0 = {
        "data": [
            {"id": "b/klein", "type": "llm", "state": "loaded", "loaded_context_length": 4096}
        ]
    }
    with patch("httpx.AsyncClient.get", _get_by_url({"localhost:1234/api/v0/models": v0})):
        found = await resolve_lm_studio_endpoint(_settings(model_name="a/verschwunden"))

    assert found.target is None
    assert found.reason_code == reasons.MODEL_MISSING
    assert found.message is not None and "a/verschwunden" in found.message


async def test_resolve_endpoint_reports_an_unreachable_server() -> None:
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectError("refused"))):
        found = await resolve_lm_studio_endpoint(_settings())

    assert found.target is None
    assert found.base_url is None
    assert found.reason_code == reasons.MODEL_UNAVAILABLE


def test_the_budget_formula_is_the_cores() -> None:
    """Kein eigener Rechenweg im Worker mehr: max_tokens = W - E - A."""
    settings = _settings()
    measurement = start_measurement(settings)
    assert measurement.chars_per_token == 3.0
    assert math.isclose(measurement.estimate_error or 0, 0.15)
