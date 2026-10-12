"""API-Vertrag der persistenten, haushaltsweiten KI-Warteschlange."""

import os
import uuid
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)
_DSN = os.environ.get(
    "TEST_DIRECT_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:55322/postgres",
)


def _db_exec(sql: str, params: dict[str, Any]) -> None:
    with psycopg.connect(_DSN) as conn:
        conn.execute(sql, params)
        conn.commit()


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"ai-tasks-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households",
        json={"name": "KI-Queue-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create(
    headers: dict[str, str],
    task_type: str,
    *,
    request_id: str | None = None,
    task_input: dict[str, Any] | None = None,
) -> Any:
    payload: dict[str, Any] = {"task_type": task_type, "input": task_input or {}}
    if request_id:
        payload["request_id"] = request_id
    return client.post("/v1/ai/tasks", json=payload, headers=headers)


def test_tasks_keep_click_order_and_request_replays_are_idempotent(
    household_headers: dict[str, str],
) -> None:
    request_id = uuid.uuid4().hex
    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        first = _create(household_headers, "category_suggestions", request_id=request_id)
        second = _create(household_headers, "contract_duplicate_crosscheck")
        replay = _create(household_headers, "category_suggestions", request_id=request_id)

    assert first.status_code == 201
    assert second.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["queue_order"] < second.json()["queue_order"]

    listed = client.get("/v1/ai/tasks", headers=household_headers)
    assert listed.status_code == 200
    queued = [item for item in listed.json() if item["status"] == "queued"]
    assert [item["id"] for item in queued] == [first.json()["id"], second.json()["id"]]


def test_same_open_task_type_is_coalesced(household_headers: dict[str, str]) -> None:
    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        first = _create(household_headers, "category_suggestions")
        second = _create(household_headers, "category_suggestions")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_queued_cancel_is_immediate_and_running_cancel_is_cooperative(
    household_headers: dict[str, str],
) -> None:
    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        queued = _create(household_headers, "category_suggestions").json()

    cancelled = client.post(
        f"/v1/ai/tasks/{queued['id']}/cancel", headers=household_headers
    ).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert cancelled["completed_at"] is not None

    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        running = _create(household_headers, "contract_duplicate_crosscheck").json()
    _db_exec(
        "update ai_tasks set status = 'running', heartbeat_at = now() where id = %(id)s",
        {"id": running["id"]},
    )
    requested = client.post(
        f"/v1/ai/tasks/{running['id']}/cancel", headers=household_headers
    ).json()
    assert requested["status"] == "running"
    assert requested["cancel_requested"] is True
    assert requested["completed_at"] is None


def test_force_cancel_ends_a_running_task_but_only_after_a_request(
    household_headers: dict[str, str],
) -> None:
    """Die Notbremse setzt die schonende Bitte voraus.

    Ein einzelner Modellaufruf darf so lange dauern wie das Zeitbudget je
    Anfrage (request_timeout_seconds) - so lange stand die Aufgabe trotz
    Abbruchbitte weiter auf 'Laeuft'. Der
    Sofortabbruch beendet die Zeile stattdessen hier und gibt die
    Warteschlange des Haushalts frei."""
    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        task = _create(household_headers, "contract_duplicate_crosscheck").json()
    _db_exec(
        "update ai_tasks set status = 'running', heartbeat_at = now() where id = %(id)s",
        {"id": task["id"]},
    )

    too_early = client.post(
        f"/v1/ai/tasks/{task['id']}/cancel?force=true", headers=household_headers
    )
    assert too_early.status_code == 409
    assert too_early.json()["detail"] == "ai_task_cancel_not_requested"

    requested = client.post(
        f"/v1/ai/tasks/{task['id']}/cancel", headers=household_headers
    ).json()
    assert requested["status"] == "running"
    assert requested["cancel_requested_at"] is not None

    forced = client.post(
        f"/v1/ai/tasks/{task['id']}/cancel?force=true", headers=household_headers
    ).json()
    assert forced["status"] == "cancelled"
    assert forced["completed_at"] is not None
    assert forced["status_message"] == "Sofort beendet"

    detail = client.get(f"/v1/ai/tasks/{task['id']}", headers=household_headers).json()
    assert any(
        event["level"] == "warning" and "sofort beendet" in event["message"]
        for event in detail["events"]
    )


def test_cancel_ends_a_task_without_worker_heartbeat_immediately(
    household_headers: dict[str, str],
) -> None:
    """Ohne Lebenszeichen gibt es nichts mehr, das kooperativ aufhoeren
    koennte - die Bitte bliebe fuer immer stehen und blockierte den
    Haushalt."""
    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        task = _create(household_headers, "contract_duplicate_crosscheck").json()
    _db_exec(
        "update ai_tasks set status = 'running', started_at = now() - interval '2 hours', "
        "heartbeat_at = now() - interval '2 hours' where id = %(id)s",
        {"id": task["id"]},
    )

    cancelled = client.post(
        f"/v1/ai/tasks/{task['id']}/cancel", headers=household_headers
    ).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["completed_at"] is not None


def test_result_acknowledgement_does_not_write_domain_data(
    household_headers: dict[str, str],
) -> None:
    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        task = _create(household_headers, "contract_duplicate_crosscheck").json()
    _db_exec(
        "update ai_tasks set status = 'awaiting_confirmation', result = '{\"items\": []}'::jsonb "
        "where id = %(id)s",
        {"id": task["id"]},
    )

    completed = client.post(
        f"/v1/ai/tasks/{task['id']}/complete", headers=household_headers
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    detail = client.get(f"/v1/ai/tasks/{task['id']}", headers=household_headers).json()
    assert detail["result"] == {"items": []}
    assert detail["events"][-1]["stage"] == "completed"


def test_task_input_is_validated_before_enqueue(household_headers: dict[str, str]) -> None:
    missing_contract = client.post(
        "/v1/ai/tasks",
        json={"task_type": "contract_transaction_check", "input": {}},
        headers=household_headers,
    )
    assert missing_contract.status_code == 422

    unknown_input = client.post(
        "/v1/ai/tasks",
        json={"task_type": "category_suggestions", "input": {"prompt": "never persist me"}},
        headers=household_headers,
    )
    assert unknown_input.status_code == 422


def test_project_cost_task_persists_context_and_result_route(
    household_headers: dict[str, str],
) -> None:
    project_id = str(uuid.uuid4())
    scenario_id = str(uuid.uuid4())
    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        created = _create(
            household_headers,
            "project_cost_estimate",
            task_input={
                "project_id": project_id,
                "scenario_id": scenario_id,
                "description": "Geerbtes Grundstück, Neubau eines Einfamilienhauses.",
                "use_web_search": False,
            },
        )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["task_type"] == "project_cost_estimate"
    assert body["input"]["description"].startswith("Geerbtes Grundstück")
    assert body["route_path"] == (
        f"/vorhaben/{project_id}?ai_task={body['id']}"
        f"&tab=kosten&scenario_id={scenario_id}"
    )

    invalid = client.post(
        "/v1/ai/tasks",
        json={
            "task_type": "project_cost_estimate",
            "input": {
                "project_id": project_id,
                "scenario_id": scenario_id,
                "description": "   ",
            },
        },
        headers=household_headers,
    )
    assert invalid.status_code == 422


def test_finished_task_can_be_deleted_but_active_task_cannot(
    household_headers: dict[str, str],
) -> None:
    with patch("app.api.v1.routes.ai_tasks.drain_queue_actor.send"):
        queued = _create(household_headers, "category_suggestions").json()
    active_delete = client.delete(f"/v1/ai/tasks/{queued['id']}", headers=household_headers)
    assert active_delete.status_code == 409
    assert active_delete.json()["detail"] == "ai_task_not_deletable"

    client.post(f"/v1/ai/tasks/{queued['id']}/cancel", headers=household_headers)
    finished_delete = client.delete(f"/v1/ai/tasks/{queued['id']}", headers=household_headers)
    assert finished_delete.status_code == 204

    missing = client.get(f"/v1/ai/tasks/{queued['id']}", headers=household_headers)
    assert missing.status_code == 404

    listed = client.get("/v1/ai/tasks", headers=household_headers)
    assert queued["id"] not in [item["id"] for item in listed.json()]

    again = client.delete(f"/v1/ai/tasks/{queued['id']}", headers=household_headers)
    assert again.status_code == 404
