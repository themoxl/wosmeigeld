"""Einstellungen des relativen KI-Budgets gegen die echte Datenbank.

Die Vorgaben stehen NUR im Datenbankschema (Spaltenvorgaben von
ai_household_settings). Geprueft wird: jeder Haushalt hat seine Zeile
(Trigger), die API liefert Wert UND Schemavorgabe je Feld, Zuruecksetzen
heisst "auf die Spaltenvorgabe", Bereiche greifen in API und Datenbank, die
Umbenennung max_output_tokens -> legacy_max_output_tokens ist wiederholbar, und
die Messung wird in SQL genauso fortgeschrieben wie in budget.update_measurement.
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.llm.budget import update_measurement
from app.llm.measurements import load_measurement, record_measurement
from app.llm.settings import LLMSettings, load_settings, parse_column_default
from app.main import app
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)

_SCHEMA = Path(__file__).resolve().parents[3] / "supabase" / "migrations" / "0001_schema.sql"
_DSN = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")

T = TypeVar("T")


def _household_headers(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    household = client.post(
        "/v1/households", json={"name": "Budget-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def headers() -> dict[str, str]:
    return _household_headers("aib")


async def _in_session(work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Eigene Engine ohne Pool: keine Verbindung ueberlebt die Ereignisschleife
    dieses Tests. Laeuft als postgres (ohne RLS), wie der Worker."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            return await work(session)
    finally:
        await engine.dispose()


def _new_household() -> str:
    with psycopg.connect(_DSN, autocommit=True) as conn:
        row = conn.execute(
            "insert into households (name) values ('Budget-Trigger-Test') returning id"
        ).fetchone()
        assert row is not None
        return str(row[0])


# ---------------------------------------------------------------------------
# Eine Zeile je Haushalt, Vorgaben aus dem Schema
# ---------------------------------------------------------------------------


def test_every_new_household_gets_its_settings_row_with_schema_defaults() -> None:
    household_id = _new_household()
    with psycopg.connect(_DSN) as conn:
        row = conn.execute(
            "select answer_reserve_share, result_page_share, run_time_limit_minutes, "
            "start_chars_per_token, start_estimate_error, context_line_enabled, "
            "context_cap_tokens, request_timeout_seconds "
            "from ai_household_settings where household_id = %s",
            (household_id,),
        ).fetchone()

    assert row is not None
    assert [float(value) if value is not None else None for value in row[:5]] == [
        0.25,
        0.1,
        20.0,
        3.0,
        0.15,
    ]
    assert row[5] is True
    assert row[6] is None
    assert row[7] == 120


def test_the_api_reports_value_and_schema_default_per_field(headers: dict[str, str]) -> None:
    body = client.get("/v1/ai/settings", headers=headers).json()

    defaults = body["schema_defaults"]
    assert defaults["answer_reserve_share"] == 0.25
    assert defaults["result_page_share"] == 0.1
    assert defaults["run_time_limit_minutes"] == 20
    assert defaults["start_chars_per_token"] == 3.0
    assert defaults["start_estimate_error"] == 0.15
    assert defaults["context_line_enabled"] is True
    assert defaults["request_timeout_seconds"] == 120
    assert defaults["context_cap_tokens"] is None

    assert body["answer_reserve_share"] == 0.25
    assert body["context_cap_tokens"] is None
    assert body["request_timeout_seconds"] == 120
    assert body["request_timeout_seconds_source"] == "default"
    assert body["legacy_max_output_tokens"] is None
    # Das feste Ausgabebudget gibt es in der API nicht mehr.
    assert "max_output_tokens" not in body
    assert "max_output_tokens_source" not in body


def test_a_value_can_be_set_and_reset_to_the_schema_default(headers: dict[str, str]) -> None:
    saved = client.put(
        "/v1/ai/settings",
        json={
            "answer_reserve_share": 0.3,
            "context_cap_tokens": 16384,
            "request_timeout_seconds": 300,
            "context_line_enabled": False,
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["answer_reserve_share"] == 0.3
    assert body["context_cap_tokens"] == 16384
    assert body["request_timeout_seconds"] == 300
    assert body["request_timeout_seconds_source"] == "household"
    assert body["context_line_enabled"] is False

    reset = client.put(
        "/v1/ai/settings",
        json={
            "clear_fields": [
                "answer_reserve_share",
                "context_cap_tokens",
                "request_timeout_seconds",
                "context_line_enabled",
            ]
        },
        headers=headers,
    ).json()
    assert reset["answer_reserve_share"] == reset["schema_defaults"]["answer_reserve_share"]
    assert reset["context_cap_tokens"] is None
    assert reset["request_timeout_seconds"] == 120
    assert reset["request_timeout_seconds_source"] == "default"
    assert reset["context_line_enabled"] is True


def test_chat_retention_is_a_setting_with_its_schema_default(headers: dict[str, str]) -> None:
    """Aufbewahrung der Chats des KI-Felds (E6): Vorgabe 90 Tage nur im
    Schema, Bereich wie der CHECK (1 bis 3650), Zuruecksetzen auf die
    Spaltenvorgabe."""
    body = client.get("/v1/ai/settings", headers=headers).json()
    assert body["assistant_retention_days"] == 90
    assert body["schema_defaults"]["assistant_retention_days"] == 90

    saved = client.put(
        "/v1/ai/settings", json={"assistant_retention_days": 30}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["assistant_retention_days"] == 30
    with psycopg.connect(_DSN) as conn:
        row = conn.execute(
            "select assistant_retention_days from ai_household_settings where household_id = %s",
            (headers["X-Household-Id"],),
        ).fetchone()
    assert row == (30,)

    reset = client.put(
        "/v1/ai/settings", json={"clear_fields": ["assistant_retention_days"]}, headers=headers
    )
    assert reset.json()["assistant_retention_days"] == 90


def test_reset_wins_over_a_value_in_the_same_request(headers: dict[str, str]) -> None:
    result = client.put(
        "/v1/ai/settings",
        json={"answer_reserve_share": 0.4, "clear_fields": ["answer_reserve_share"]},
        headers=headers,
    ).json()

    assert result["answer_reserve_share"] == 0.25


@pytest.mark.parametrize(
    "payload",
    [
        {"answer_reserve_share": 0.95},
        {"answer_reserve_share": 0.01},
        {"result_page_share": 0.6},
        {"run_time_limit_minutes": 0},
        {"start_chars_per_token": 9},
        {"start_estimate_error": 1.5},
        {"context_cap_tokens": 1000},
        {"request_timeout_seconds": 1},
        {"assistant_retention_days": 0},
        {"assistant_retention_days": 3651},
    ],
)
def test_out_of_range_values_are_rejected_by_the_api(
    headers: dict[str, str], payload: dict[str, Any]
) -> None:
    assert client.put("/v1/ai/settings", json=payload, headers=headers).status_code == 422


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("answer_reserve_share", 0.95),
        ("result_page_share", 0.0),
        ("run_time_limit_minutes", 500),
        ("start_chars_per_token", 0.5),
        ("start_estimate_error", 2),
        ("context_cap_tokens", 100),
    ],
)
def test_the_database_enforces_the_ranges_itself(column: str, value: float) -> None:
    household_id = _new_household()
    with psycopg.connect(_DSN) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"update ai_household_settings set {column} = %s "  # noqa: S608
                "where household_id = %s",
                (value, household_id),
            )
        conn.rollback()


def test_the_old_output_budget_is_gone_from_the_update_contract(
    headers: dict[str, str],
) -> None:
    """Alte Clients schicken max_output_tokens weiter mit - das Feld wird
    ignoriert und wirkt nirgends mehr."""
    response = client.put("/v1/ai/settings", json={"max_output_tokens": 1200}, headers=headers)

    assert response.status_code == 200
    assert "max_output_tokens" not in response.json()


def test_viewers_may_not_change_budget_settings() -> None:
    owner = _household_headers("aibowner")
    viewer = _invite_member(client, owner, owner["X-Household-Id"], "viewer", "aibviewer")

    assert client.get("/v1/ai/settings", headers=viewer).status_code == 200
    assert (
        client.put(
            "/v1/ai/settings", json={"answer_reserve_share": 0.3}, headers=viewer
        ).status_code
        == 403
    )


async def test_the_core_reads_the_same_values() -> None:
    household_id = _new_household()

    settings: LLMSettings = await _in_session(
        lambda session: load_settings(session, uuid.UUID(household_id))
    )

    assert settings.answer_reserve_share == 0.25
    assert settings.start_chars_per_token == 3.0
    assert settings.start_estimate_error == 0.15
    assert settings.request_timeout_seconds == 120
    assert settings.context_cap_tokens is None
    assert settings.context_line_enabled is True


# ---------------------------------------------------------------------------
# Umbenennung des alten Ausgabebudgets
# ---------------------------------------------------------------------------


def _rename_block() -> str:
    schema = _SCHEMA.read_text(encoding="utf-8")
    match = re.search(
        r"do \$\$\nbegin\n  if exists \(.*?legacy_max_output_tokens.*?end \$\$;", schema, re.DOTALL
    )
    assert match is not None, "DO-Block der Umbenennung nicht gefunden"
    return (
        match.group(0)
        + "\nalter table ai_household_settings\n"
        + "  add column if not exists legacy_max_output_tokens int;"
    )


def test_the_legacy_rename_is_repeatable_and_keeps_the_old_value() -> None:
    household_id = _new_household()
    block = _rename_block()
    with psycopg.connect(_DSN) as conn:
        # Zustand vor der Umbenennung nachstellen - nur in dieser Transaktion.
        conn.execute(
            "alter table ai_household_settings "
            "rename column legacy_max_output_tokens to max_output_tokens"
        )
        conn.execute(
            "update ai_household_settings set max_output_tokens = 4000 where household_id = %s",
            (household_id,),
        )
        conn.execute(block)
        conn.execute(block)  # zweites Anwenden: keine Wirkung, kein Fehler
        columns = {
            row[0]
            for row in conn.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'public' and table_name = 'ai_household_settings'"
            ).fetchall()
        }
        kept = conn.execute(
            "select legacy_max_output_tokens from ai_household_settings where household_id = %s",
            (household_id,),
        ).fetchone()
        conn.rollback()

    assert "legacy_max_output_tokens" in columns
    assert "max_output_tokens" not in columns
    assert kept is not None and kept[0] == 4000


def test_the_legacy_value_is_shown_once_and_can_be_cleared(headers: dict[str, str]) -> None:
    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute(
            "update ai_household_settings set legacy_max_output_tokens = 4000 "
            "where household_id = %s",
            (headers["X-Household-Id"],),
        )

    assert client.get("/v1/ai/settings", headers=headers).json()["legacy_max_output_tokens"] == 4000
    cleared = client.put(
        "/v1/ai/settings", json={"clear_fields": ["legacy_max_output_tokens"]}, headers=headers
    ).json()
    assert cleared["legacy_max_output_tokens"] is None


# ---------------------------------------------------------------------------
# Spaltenvorgaben lesen und Messung fortschreiben
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("0.250", 0.25),
        ("20", 20),
        ("true", True),
        ("'model'::text", "model"),
        ("NULL::integer", None),
        (None, None),
        ("now()", "now()"),
    ],
)
def test_column_defaults_are_parsed(raw: str | None, value: Any) -> None:
    assert parse_column_default(raw) == value


async def test_the_sql_measurement_matches_the_python_rule() -> None:
    household_id = uuid.UUID(_new_household())
    calls = [(3000, 1000, 500), (3600, 1000, 300), (24, 18, 2), (33000, 10000, 0)]
    expected = None
    for chars, prompt, need in calls:
        expected = update_measurement(expected, chars, prompt, need)

    async def write_and_read(session: AsyncSession) -> Any:
        for chars, prompt, need in calls:
            await record_measurement(
                session,
                household_id,
                "http://127.0.0.1:1234/v1",
                "test-model",
                chars=chars,
                prompt_tokens=prompt,
                need_tokens=need,
            )
        settings = await load_settings(session, household_id)
        return await load_measurement(
            session, household_id, "http://127.0.0.1:1234", "test-model", settings
        )

    measurement = await _in_session(write_and_read)

    assert expected is not None and measurement.sums is not None
    assert measurement.sums.sum_chars == expected.sum_chars
    assert measurement.sums.sum_prompt_tokens == expected.sum_prompt_tokens
    assert measurement.sums.sum_checked_tokens == expected.sum_checked_tokens
    assert measurement.sums.sum_estimate_error == pytest.approx(expected.sum_estimate_error)
    assert measurement.sums.largest_need_tokens == 500
    assert measurement.calls == 4
    assert not measurement.is_start_value
    assert measurement.chars_per_token == pytest.approx(expected.chars_per_token)
    assert measurement.estimate_error == pytest.approx(expected.estimate_error)


async def test_without_a_measurement_the_start_values_apply() -> None:
    household_id = uuid.UUID(_new_household())

    async def read(session: AsyncSession) -> Any:
        settings = await load_settings(session, household_id)
        return await load_measurement(
            session, household_id, "http://127.0.0.1:1234/v1", "noch-nie-gemessen", settings
        )

    measurement = await _in_session(read)

    assert measurement.is_start_value and measurement.error_is_start_value
    assert measurement.chars_per_token == 3.0
    assert measurement.estimate_error == 0.15
