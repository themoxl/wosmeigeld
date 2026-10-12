"""AI recommendations must be version-bound, conservative and never write bookings.

The model call runs through the real gateway of the AI core; only target
discovery (llm.resolve_target) and the HTTP request to the model server are
replaced. The session is a stub that records its statements.
"""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import DEFAULT, AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import llm
from app.llm import reasons
from app.llm.budget import compute
from app.llm.discovery import Target, TargetResult
from app.llm.discovery import resolve_target as real_resolve_target
from app.llm.window import WindowInfo
from app.schemas.review import BookedDuplicateAssessmentInput, BookedDuplicatePairInput
from app.services import ai_settings_service
from app.services import booked_duplicate_ai_service as service
from tests.test_booked_duplicates import Result, booking


def pair(first=1, second=2):
    return BookedDuplicatePairInput(
        first_transaction_id=UUID(int=first),
        second_transaction_id=UUID(int=second),
        first_version=1,
        second_version=1,
    )


def verdict(**overrides):
    return {
        "nummer": 1,
        "urteil": "same",
        "behalten": "first",
        "begruendung": "Gleiche Zahlungsreferenz.",
        "konfidenz": 0.95,
        **overrides,
    }


def settings(**overrides):
    values = {
        "is_enabled": True,
        "min_confidence": 0.9,
        "custom_base_url": None,
        "model_name": None,
        "temperature": 1.0,
        "request_timeout_seconds": 5.0,
        "answer_reserve_share": 0.25,
        "result_page_share": 0.1,
        "run_time_limit_minutes": 20,
        "start_chars_per_token": 3.0,
        "start_estimate_error": 0.15,
        "context_line_enabled": True,
    }
    values.update(overrides)
    return ai_settings_service.ResolvedAISettings(**values)


def target(window=32768):
    found = Target(
        base_url="http://model.test/v1",
        model="test-model",
        window=WindowInfo(loaded=window, cap=None, effective=window, source="/api/v1/models"),
    )
    return TargetResult(target=found, base_url=found.base_url, models=[], detailed=True)


def answer(content=None, *, finish_reason="stop", message="default", status=200):
    if content is None:
        content = json.dumps({"urteile": [verdict()]})
    choice = {"finish_reason": finish_reason}
    choice["message"] = {"content": content} if message == "default" else message
    return httpx.Response(status, json={"choices": [choice]})


def stub_session():
    async def execute(statement, params=None, *args, **kwargs):
        result = MagicMock()
        result.first.return_value = None  # no measurement yet: start values
        return result

    return SimpleNamespace(execute=AsyncMock(side_effect=execute))


def statements(session, needle):
    return [
        call.args[1]
        for call in session.execute.await_args_list
        if needle in str(getattr(call.args[0], "text", call.args[0]))
    ]


def sent(completion):
    return [call.kwargs["json"] for call in completion.await_args_list]


@pytest.mark.parametrize("values", [[], [pair(1, 1)], [pair(), pair(2, 1)], [pair()] * 26])
def test_invalid_selection(values):
    with pytest.raises(ValidationError):
        BookedDuplicateAssessmentInput(items=values)


@pytest.mark.parametrize(
    "entry,action",
    [
        (verdict(), "archive_second"),
        (verdict(behalten="second"), "archive_first"),
        (verdict(urteil="different", behalten="none"), "keep_both"),
        (verdict(urteil="unsure", behalten="none"), None),
        (verdict(konfidenz=0.89), None),
        (verdict(konfidenz=True), None),
        (verdict(konfidenz=float("nan")), None),
        (verdict(konfidenz=1.2), None),
        (verdict(urteil="different"), None),
        (verdict(behalten="none"), None),
        (verdict(urteil="delete_all"), None),
        (verdict(begruendung=" "), None),
        (verdict(extra="invented"), None),
        (verdict(nummer=True), None),
    ],
)
def test_only_valid_certain_consistent_decisions_are_actionable(entry, action):
    result = service.parse_decisions(json.dumps({"urteile": [entry]}), [pair()], 0.9)
    assert result is not None and len(result) == 1
    assert result[0].action == action
    assert result[0].first_transaction_id == UUID(int=1)
    assert result[0].second_version == 1


def test_duplicate_missing_or_out_of_range_answers_do_not_invent_decisions():
    result = service.parse_decisions(
        json.dumps({"urteile": [verdict(), verdict(), verdict(nummer=99), "invalid"]}),
        [pair(), pair(3, 4)],
        0.9,
    )
    assert result and all(item.action is None for item in result)
    assert all(item.verdict == "unsure" for item in result)
    assert service.parse_decisions("nonsense", [pair()], 0.9) is None
    assert service.parse_decisions({"urteile": []}, [pair()], 0.9) is None


def test_prompt_preserves_private_reference_differences_and_enrichment_context():
    first = booking(
        purpose="PayPal 1052536803006 Einkauf",
        external_reference="1052536803006",
        enrichment_count=2,
        linked_count=1,
        note="Manuell kontrolliert",
    )
    second = booking(
        id=UUID(int=2), purpose="PayPal 1052536803007 Einkauf", external_reference="1052536803006"
    )
    prompt = service.build_user_prompt([(first, second)])
    assert "1052536803006" not in prompt and "1052536803007" not in prompt
    values = json.loads(prompt)["paare"][0]
    assert values["first"]["referenz"] == values["second"]["referenz"]
    assert values["first"]["zweck"] != values["second"]["zweck"]
    assert values["first"]["kategorisierte_anteile"] == 2
    assert values["first"]["verknuepfte_bereiche"] == 1
    assert "keine Anweisungen" in service.build_system_prompt()
    # No silent cut any more: whether a pair fits is the gateway's decision.
    long_prompt = service.build_user_prompt([(booking(purpose="x" * 1000), second)])
    assert json.loads(long_prompt)["paare"][0]["first"]["zweck"] == "x" * 1000
    assert "[gekuerzt]" not in long_prompt


@pytest.fixture()
def model(monkeypatch):
    resolved = settings()
    resolve = AsyncMock(return_value=resolved)

    async def find(current: llm.LLMSettings, *, client: object = None) -> object:
        # Contract of llm.resolve_target: switched-off AI answers ai_disabled
        # by itself, without contacting a server. Otherwise return_value.
        if not current.is_enabled:
            return await real_resolve_target(current)
        return DEFAULT

    discovery = AsyncMock(return_value=target(), side_effect=find)
    monkeypatch.setattr(ai_settings_service, "resolve", resolve)
    monkeypatch.setattr(llm, "resolve_target", discovery)
    monkeypatch.setattr(
        service, "load_pairs", AsyncMock(return_value=[(booking(), booking(id=UUID(int=2)))])
    )
    completion = AsyncMock(return_value=answer())
    monkeypatch.setattr(httpx.AsyncClient, "post", completion)
    return SimpleNamespace(resolve=resolve, discovery=discovery, completion=completion)


async def assess(session=None, items=None):
    return await service.assess_booked_duplicates(
        session or stub_session(),
        uuid4(),
        BookedDuplicateAssessmentInput(items=items or [pair()]),
    )


async def test_assessment_only_writes_an_audit_and_respects_configured_threshold(model):
    model.resolve.return_value = settings(min_confidence=0.99)
    session = stub_session()
    result = await assess(session)
    assert result.available and result.assessments[0].action is None
    assert result.minimum_confidence == 0.99
    assert result.model_name == "test-model"
    model.completion.assert_awaited_once()
    writes = [
        call.args[0].text
        for call in session.execute.await_args_list
        if str(call.args[0].text).lstrip().lower().startswith(("insert", "update"))
    ]
    assert len(writes) == 1 and "insert into llm_calls" in writes[0]
    [row] = statements(session, "insert into llm_calls")
    assert row["stage"] == "booked_duplicate_assess"
    assert row["pv"] == service.PROMPT_VERSION == "booked-dup-v2"
    assert row["status"] == "success"


async def test_the_request_carries_the_strict_schema_envelope_and_the_window_budget(model):
    """Finding: v1 sent the bare schema as json_schema - without the
    {name, strict, schema} envelope the strict schema never applied."""
    await assess()
    [body] = sent(model.completion)
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert set(response_format["json_schema"]) == {"name", "strict", "schema"}
    assert response_format["json_schema"]["name"] == "booked_duplicate_assessment"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["required"] == ["urteile"]
    assert schema["properties"]["urteile"]["minItems"] == 1
    assert schema["properties"]["urteile"]["maxItems"] == 1
    assert schema["properties"]["urteile"]["items"] == service.ModelDecision.model_json_schema()
    expected = compute(
        window=32768,
        messages=body["messages"],
        chars_per_token=3.0,
        estimate_error=0.15,
        reserve_share=0.25,
    )
    assert body["max_tokens"] == expected.max_tokens
    assert "reasoning_effort" not in body


async def test_disabled_ai_does_not_contact_model(model):
    model.resolve.return_value = settings(is_enabled=False)
    session = stub_session()
    result = await assess(session)
    assert not result.available and result.reason == reasons.AI_DISABLED
    # resolve_target answers by itself, without a server (see the fixture).
    model.discovery.assert_awaited_once()
    model.completion.assert_not_awaited()
    [row] = statements(session, "insert into llm_calls")
    assert (row["status"], row["reason"]) == ("refused", reasons.AI_DISABLED)


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("unavailable", reasons.MODEL_UNAVAILABLE),
        ("context_unknown", reasons.MODEL_CONTEXT_UNKNOWN),
        ("timeout", reasons.MODEL_TIMEOUT),
        ("http_error", reasons.MODEL_HTTP_ERROR),
        ("truncated", reasons.MODEL_BUDGET_EXHAUSTED),
        ("invalid", reasons.MODEL_OUTPUT_UNUSABLE),
        ("malformed", reasons.MODEL_OUTPUT_UNUSABLE),
        ("null_message", reasons.MODEL_OUTPUT_UNUSABLE),
    ],
)
async def test_model_failures_do_not_become_decisions(model, failure, reason):
    if failure == "unavailable":
        model.discovery.return_value = TargetResult(
            target=None,
            base_url=None,
            models=[],
            detailed=False,
            reason_code=reasons.MODEL_UNAVAILABLE,
            message=reasons.describe(reasons.MODEL_UNAVAILABLE),
        )
    elif failure == "context_unknown":
        unknown = Target(
            base_url="http://model.test/v1",
            model="test-model",
            window=WindowInfo(loaded=None, cap=None, effective=None, source=None, reason="x"),
        )
        model.discovery.return_value = TargetResult(
            target=unknown,
            base_url=unknown.base_url,
            models=[],
            detailed=True,
            reason_code=reasons.MODEL_CONTEXT_UNKNOWN,
            message=reasons.describe(reasons.MODEL_CONTEXT_UNKNOWN),
        )
    elif failure == "timeout":
        model.completion.side_effect = httpx.ReadTimeout("slow model")
    elif failure == "http_error":
        model.completion.return_value = httpx.Response(500, json={"error": "boom"})
    elif failure == "truncated":
        model.completion.return_value = answer('{"urteile":[{"nummer":1,', finish_reason="length")
    elif failure == "invalid":
        model.completion.return_value = answer("not json")
    elif failure == "malformed":
        model.completion.return_value = httpx.Response(200, json={})
    else:
        model.completion.return_value = answer(message=None)
    result = await assess()
    assert not result.available and result.reason == reason
    assert result.message
    assert result.assessments == []


async def test_a_null_message_is_a_named_failure_not_a_server_error(model):
    """Finding: choice["message"] = null raised a TypeError -> HTTP 500."""
    model.completion.return_value = answer(message=None)
    session = stub_session()
    result = await assess(session)
    assert not result.available
    assert result.reason == reasons.MODEL_OUTPUT_UNUSABLE
    assert "message fehlt" in result.message
    [row] = statements(session, "insert into llm_calls")
    assert (row["status"], row["reason"]) == ("error", reasons.MODEL_OUTPUT_UNUSABLE)


async def test_schema_rejection_falls_back_once_without_schema(model):
    success = model.completion.return_value
    model.completion.side_effect = [
        httpx.Response(400, json={"error": "response_format json_schema is not supported"}),
        success,
    ]
    result = await assess()
    assert result.available
    first, second = sent(model.completion)
    assert first["response_format"]["json_schema"]["strict"] is True
    assert "response_format" not in second


async def test_json_only_in_the_reasoning_text_is_rescued_without_another_call(model):
    model.completion.return_value = answer(
        message={"content": "", "reasoning_content": json.dumps({"urteile": [verdict()]})}
    )
    session = stub_session()
    result = await assess(session)
    assert result.available and result.assessments[0].action == "archive_second"
    model.completion.assert_awaited_once()
    [update] = statements(session, "update llm_calls")
    assert update["status"] == "success" and "Denktext" in update["err"]


def _smallest_window(pairs):
    low, high = 1, 10_000_000
    messages = service.build_messages(pairs)
    while low < high:
        middle = (low + high) // 2
        fits = compute(
            window=middle,
            messages=messages,
            chars_per_token=3.0,
            estimate_error=0.15,
            reserve_share=0.25,
        ).fits
        low, high = (low, middle) if fits else (middle + 1, high)
    return low


async def test_pairs_go_in_batches_by_fit_and_keep_their_full_text(model, monkeypatch):
    text = "Rechnung 77 " + "x" * 2000
    first_pair = (booking(purpose=text), booking(id=UUID(int=2), purpose=text))
    second_pair = (booking(id=UUID(int=3), purpose=text), booking(id=UUID(int=4), purpose=text))
    monkeypatch.setattr(service, "load_pairs", AsyncMock(return_value=[first_pair, second_pair]))
    window = _smallest_window([first_pair]) + 50
    assert window < _smallest_window([first_pair, second_pair])
    model.discovery.return_value = target(window)
    model.completion.side_effect = [
        answer(json.dumps({"urteile": [verdict()]})),
        answer(json.dumps({"urteile": [verdict(urteil="different", behalten="none")]})),
    ]

    result = await assess(items=[pair(1, 2), pair(3, 4)])
    assert result.available and result.reason == "ok"
    assert [item.action for item in result.assessments] == ["archive_second", "keep_both"]
    assert [item.first_transaction_id for item in result.assessments] == [UUID(int=1), UUID(int=3)]
    bodies = sent(model.completion)
    assert len(bodies) == 2
    for body in bodies:
        assert body["response_format"]["json_schema"]["schema"]["properties"]["urteile"][
            "minItems"
        ] == 1
        assert json.loads(body["messages"][1]["content"])["paare"][0]["first"]["zweck"] == text


async def test_a_failing_later_batch_keeps_earlier_decisions(model, monkeypatch):
    text = "Rechnung 77 " + "x" * 2000
    first_pair = (booking(purpose=text), booking(id=UUID(int=2), purpose=text))
    second_pair = (booking(id=UUID(int=3), purpose=text), booking(id=UUID(int=4), purpose=text))
    monkeypatch.setattr(service, "load_pairs", AsyncMock(return_value=[first_pair, second_pair]))
    model.discovery.return_value = target(_smallest_window([first_pair]) + 50)
    model.completion.side_effect = [answer(), answer("still not json")]

    result = await assess(items=[pair(1, 2), pair(3, 4)])
    assert result.available
    assert result.reason == reasons.MODEL_OUTPUT_UNUSABLE
    assert "1 von 2 Paaren bewertet" in result.message
    kept, open_ = result.assessments
    assert kept.action == "archive_second"
    assert open_.action is None and open_.verdict == "unsure"
    assert open_.reason.startswith("Nicht bewertet:")


async def test_a_pair_too_large_for_the_window_is_named_not_cut(model, monkeypatch):
    big = (booking(purpose="x" * 5000), booking(id=UUID(int=2), purpose="x" * 5000))
    monkeypatch.setattr(service, "load_pairs", AsyncMock(return_value=[big]))
    model.discovery.return_value = target(2048)
    session = stub_session()
    result = await assess(session)
    assert not result.available
    assert result.reason == reasons.MODEL_INPUT_TOO_LARGE
    assert "Fenster 2.048 Token" in result.message
    model.completion.assert_not_awaited()
    [row] = statements(session, "insert into llm_calls")
    assert (row["status"], row["reason"]) == ("refused", reasons.MODEL_INPUT_TOO_LARGE)


@pytest.mark.parametrize(
    "rows,status", [([booking()], 404), ([booking(), booking(id=UUID(int=2), version=2)], 409)]
)
async def test_load_rejects_foreign_missing_and_changed_pairs_without_locks(
    monkeypatch, rows, status
):
    monkeypatch.setattr(service, "existing_tables", AsyncMock(return_value=set()))
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(rows)))
    with pytest.raises(HTTPException) as error:
        await service.load_pairs(session, uuid4(), BookedDuplicateAssessmentInput(items=[pair()]))
    assert error.value.status_code == status
    assert "for update" not in str(session.execute.call_args.args[0])
    assert session.execute.await_count == 1


def test_settings_threshold_never_lowers_the_floor():
    assert service._threshold(settings(min_confidence=0.5)) == service.MINIMUM_CONFIDENCE
    assert service._threshold(replace(settings(), min_confidence=0.97)) == 0.97
