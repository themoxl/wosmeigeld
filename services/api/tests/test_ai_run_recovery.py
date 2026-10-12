"""KI-Finanzassistent, Ausbaustufe 0: ein laufender KI-Lauf muss Navigation,
Reload und App-Neustart ueberleben (siehe docs/KI-FINANZASSISTENT-PLAN.md).

Der fachliche Kern dieser Tests: die Run-ID darf nicht laenger im lokalen
Client-State leben. Der Server muss den aktiven Lauf jederzeit selbst liefern,
pro Haushalt hoechstens einen aktiven Lauf zulassen und einen haengenden Lauf
wieder aufloesen koennen - sonst blockiert er den Haushalt dauerhaft.
"""

import uuid
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from fastapi.testclient import TestClient

import app.api.v1.routes.ai as ai_routes
from app.api.v1.routes.ai import STALE_RUN_AFTER_MINUTES
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

# Direktzugriff fuer Zustaende, die ueber die API nicht herstellbar sind (ein
# laufender bzw. ein haengender Lauf) - dieselbe lokale Supabase-Instanz, die
# auch die API benutzt (siehe tests/conftest.py).
_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"


def _db_exec(sql: str, params: dict[str, Any]) -> None:
    with psycopg.connect(_DSN) as conn:
        conn.execute(sql, params)
        conn.commit()


def _db_scalar(sql: str, params: dict[str, Any]) -> Any:
    with psycopg.connect(_DSN) as conn:
        row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"run0-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "KI-Lauf-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _start_run(headers: dict[str, str], **payload: Any) -> Any:
    return client.post("/v1/ai/classify-runs", json=payload, headers=headers)


def _active_runs(headers: dict[str, str]) -> list[dict[str, Any]]:
    res = client.get("/v1/ai/classify-runs?status=active", headers=headers)
    assert res.status_code == 200
    return list(res.json())


# ---------------------------------------------------------------------------
# Wiederfinden nach Navigation/Reload
# ---------------------------------------------------------------------------


def test_active_run_is_recoverable_without_client_state(
    household_headers: dict[str, str],
) -> None:
    """Genau der Fehlerfall aus dem Plan: die Seite wird neu geladen, der Client
    kennt keine Run-ID mehr - der Server muss sie liefern."""
    started = _start_run(household_headers)
    assert started.status_code == 201
    run = started.json()

    active = _active_runs(household_headers)
    assert [r["id"] for r in active] == [run["id"]]
    assert active[0]["status"] in ("pending", "running")
    assert active[0]["is_stale"] is False
    assert active[0]["run_type"] == "classification"
    assert active[0]["cancel_requested"] is False


def test_history_returns_newest_run_first(household_headers: dict[str, str]) -> None:
    first = _start_run(household_headers).json()
    client.post(f"/v1/ai/classify-runs/{first['id']}/cancel", headers=household_headers)
    second = _start_run(household_headers).json()
    assert second["id"] != first["id"]

    res = client.get("/v1/ai/classify-runs?limit=1", headers=household_headers)
    assert res.status_code == 200
    # limit=1 ist der Abruf, mit dem beide Clients ihren Statuschip fuellen:
    # der juengste Lauf, unabhaengig vom Status.
    assert [r["id"] for r in res.json()] == [second["id"]]

    full = client.get("/v1/ai/classify-runs", headers=household_headers).json()
    assert [r["id"] for r in full] == [second["id"], first["id"]]
    assert full[1]["status"] == "cancelled"


def test_run_list_is_household_isolated(household_headers: dict[str, str]) -> None:
    _start_run(household_headers)

    other_token = _signup(
        f"run0-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    other_household = client.post(
        "/v1/households", json={"name": "Anderer Haushalt"}, headers=_auth_headers(other_token)
    ).json()
    other_headers = {**_auth_headers(other_token), "X-Household-Id": other_household["id"]}

    assert client.get("/v1/ai/classify-runs", headers=other_headers).json() == []


# ---------------------------------------------------------------------------
# Ein aktiver Lauf je Haushalt
# ---------------------------------------------------------------------------


def test_second_start_returns_the_running_run_instead_of_a_second_job(
    household_headers: dict[str, str],
) -> None:
    first = _start_run(household_headers)
    assert first.status_code == 201

    second = _start_run(household_headers)
    # 200 statt 201: es entstand kein neuer Lauf - genau das verhindert die
    # doppelten Modellaufrufe eines Doppelklicks.
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]

    household_id = household_headers["X-Household-Id"]
    assert (
        _db_scalar(
            "select count(*) from classification_runs "
            "where household_id = %(hid)s and status in ('pending', 'running')",
            {"hid": household_id},
        )
        == 1
    )


def test_truly_concurrent_starts_fall_back_to_the_existing_run(
    household_headers: dict[str, str],
) -> None:
    """Zwei exakt gleichzeitige Starts sehen einander in ihrer jeweiligen
    Transaktion noch nicht - dann muss der Unique-Index den zweiten stoppen,
    ohne dass der Nutzer einen Fehler sieht. Das Wettrennen wird simuliert,
    indem die Suche nach dem aktiven Lauf genau einmal blind ist."""
    first = _start_run(household_headers).json()

    original_select = ai_routes._select_run
    blind_lookups = {"count": 0}

    async def _blind_first_active_lookup(
        db: Any, household_id: Any, condition: str, extra_params: Any = None
    ) -> Any:
        if "status in" in condition:
            blind_lookups["count"] += 1
            if blind_lookups["count"] == 1:
                return None
        return await original_select(db, household_id, condition, extra_params)

    with patch.object(ai_routes, "_select_run", _blind_first_active_lookup):
        second = _start_run(household_headers)

    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert (
        _db_scalar(
            "select count(*) from classification_runs "
            "where household_id = %(hid)s and status in ('pending', 'running')",
            {"hid": household_headers["X-Household-Id"]},
        )
        == 1
    )


def test_request_id_makes_the_start_idempotent(household_headers: dict[str, str]) -> None:
    request_id = uuid.uuid4().hex
    first = _start_run(household_headers, request_id=request_id)
    assert first.status_code == 201
    run_id = first.json()["id"]

    retried = _start_run(household_headers, request_id=request_id)
    assert retried.status_code == 200
    assert retried.json()["id"] == run_id

    # Auch nach dem Ende des Laufs bleibt derselbe Schluessel derselbe Lauf -
    # ein spaeter eintreffender Retry darf keinen zweiten Lauf ausloesen.
    client.post(f"/v1/ai/classify-runs/{run_id}/cancel", headers=household_headers)
    late_retry = _start_run(household_headers, request_id=request_id)
    assert late_retry.status_code == 200
    assert late_retry.json()["id"] == run_id
    assert late_retry.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Abbrechen und haengende Laeufe
# ---------------------------------------------------------------------------


def test_cancel_pending_run_frees_the_household_for_a_new_start(
    household_headers: dict[str, str],
) -> None:
    run_id = _start_run(household_headers).json()["id"]

    cancelled = client.post(
        f"/v1/ai/classify-runs/{run_id}/cancel", headers=household_headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["completed_at"] is not None
    assert _active_runs(household_headers) == []

    fresh = _start_run(household_headers)
    assert fresh.status_code == 201
    assert fresh.json()["id"] != run_id


def test_cancel_of_a_working_run_only_requests_the_stop(
    household_headers: dict[str, str],
) -> None:
    """Ein arbeitender Lauf wird nicht hart abgeschnitten: der Worker beendet
    ihn zwischen zwei Buchungen, damit keine halbe Klassifikation
    zurueckbleibt."""
    run_id = _start_run(household_headers).json()["id"]
    _db_exec(
        "update classification_runs set status = 'running', heartbeat_at = now() "
        "where id = %(id)s",
        {"id": run_id},
    )

    cancelled = client.post(
        f"/v1/ai/classify-runs/{run_id}/cancel", headers=household_headers
    ).json()
    assert cancelled["status"] == "running"
    assert cancelled["cancel_requested"] is True
    assert cancelled["completed_at"] is None


def test_cancel_is_idempotent_and_404s_for_unknown_runs(
    household_headers: dict[str, str],
) -> None:
    run_id = _start_run(household_headers).json()["id"]
    first = client.post(f"/v1/ai/classify-runs/{run_id}/cancel", headers=household_headers)
    second = client.post(f"/v1/ai/classify-runs/{run_id}/cancel", headers=household_headers)
    assert first.json()["status"] == second.json()["status"] == "cancelled"
    assert first.json()["error_message"] == second.json()["error_message"]

    unknown = client.post(
        f"/v1/ai/classify-runs/{uuid.uuid4()}/cancel", headers=household_headers
    )
    assert unknown.status_code == 404


def test_stale_run_is_reported_and_does_not_block_a_new_start(
    household_headers: dict[str, str],
) -> None:
    """Ohne diese Aufloesung wuerde ein abgestuerzter Worker den Haushalt
    dauerhaft blockieren - die Ein-aktiver-Lauf-Regel waere dann schlimmer als
    das Problem, das sie loest."""
    stuck_id = _start_run(household_headers).json()["id"]
    _db_exec(
        "update classification_runs set status = 'running', "
        "heartbeat_at = now() - make_interval(mins => %(mins)s), "
        "created_at = now() - make_interval(mins => %(mins)s) where id = %(id)s",
        {"id": stuck_id, "mins": STALE_RUN_AFTER_MINUTES + 5},
    )

    active = _active_runs(household_headers)
    assert active[0]["is_stale"] is True

    fresh = _start_run(household_headers)
    assert fresh.status_code == 201
    assert fresh.json()["id"] != stuck_id

    stuck = client.get(f"/v1/ai/classify-runs/{stuck_id}", headers=household_headers).json()
    assert stuck["status"] == "cancelled"
    assert "Lebenszeichen" in (stuck["error_message"] or "")
    assert [r["id"] for r in _active_runs(household_headers)] == [fresh.json()["id"]]
