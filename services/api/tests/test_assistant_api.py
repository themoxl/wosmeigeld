"""KI-Feld gegen die echte Datenbank: Endpunkte, Warteschlange, Lauf im
"Worker" (execute_run mit gefaelschtem Modellserver) und NDJSON-Strom.

Vertrag: docs/KI-FELD-SCHNITTSTELLE.md. Geprueft wird, was die Oberflaeche
voraussetzt: CRUD und Privatheit der Chats (fremder Chat 404, Betrachter
darf fragen), Einreihen mit Idempotenz ueber request_id, Abbrechen (wartend
sofort, laufend schliesst die Verbindung zum Modell), Wiederholen,
Verdichten, das Laufprotokoll und der Strom: Nachspielen ab `after`, live mit
fluechtigen Zeilen, streng steigende seq, done erst zum Schluss.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.assistant.events import MemoryBus, get_event_bus
from app.assistant.runner import execute_run
from app.assistant.streaming import run_stream
from app.core.config import get_settings
from app.main import app
from tests.assistant_fakes import BLOCK, FakeModelServer, Reply
from tests.test_household_isolation import (
    _auth_headers,
    _invite_member,
    _signup,
    _user_id_from_token,
)

client = TestClient(app)
_DSN = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
DRAIN = "app.api.v1.routes.assistant.drain_queue_actor.send"


def _sql(query: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    with psycopg.connect(_DSN) as conn:
        cursor = conn.execute(query, params or {})
        rows = cursor.fetchall() if cursor.description is not None else []
        conn.commit()
        return rows


@pytest.fixture()
def bus() -> Iterator[MemoryBus]:
    memory = MemoryBus()
    app.dependency_overrides[get_event_bus] = lambda: memory
    try:
        yield memory
    finally:
        app.dependency_overrides.pop(get_event_bus, None)


@pytest.fixture()
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # Eigene Engine in der Ereignisschleife des Tests (kein Pool) - die der
    # App gehoert zur Schleife des TestClient.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def world() -> dict[str, Any]:
    owner = _signup(f"kifeld-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-12")
    household = client.post(
        "/v1/households", json={"name": "KI-Feld-Test"}, headers=_auth_headers(owner)
    ).json()
    headers = {**_auth_headers(owner), "X-Household-Id": household["id"]}
    viewer_headers = _invite_member(client, headers, household["id"], "viewer", "kifeld-viewer")
    stranger = _signup(f"kifeld-x-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-12")
    other = client.post(
        "/v1/households", json={"name": "Fremd"}, headers=_auth_headers(stranger)
    ).json()
    return {
        "household": household["id"],
        "owner": headers,
        "owner_id": _user_id_from_token(owner),
        "viewer": viewer_headers,
        "stranger": {**_auth_headers(stranger), "X-Household-Id": other["id"]},
    }


def _new_chat(headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = client.post("/v1/assistant/chats", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _send(headers: dict[str, str], chat_id: str, content: str, **extra: Any) -> Any:
    body = {"content": content, "request_id": extra.pop("request_id", uuid.uuid4().hex), **extra}
    with patch(DRAIN) as drain:
        response = client.post(
            f"/v1/assistant/chats/{chat_id}/messages", json=body, headers=headers
        )
    return response, drain


def _stream_lines(headers: dict[str, str], run_id: str, after: int | None = None) -> list[Any]:
    url = f"/v1/assistant/runs/{run_id}/stream" + (f"?after={after}" if after is not None else "")
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _accounts_then_answer() -> list[Any]:
    return [
        Reply(tool_calls=[("list_accounts", {})], reasoning="Erst die Konten ansehen."),
        Reply(content="Du hast Konten mit zusammen 1.234,56 € Saldo und 777 € Dispo."),
    ]


# ---------------------------------------------------------------- Chats


def test_chat_crud_is_private_to_its_author(world: dict[str, Any], bus: MemoryBus) -> None:
    chat = _new_chat(world["owner"], title="Ausgaben")
    assert chat["title"] == "Ausgaben"
    assert chat["messages"] == [] and chat["summary"] is None
    assert chat["reasoning_override"] is None
    assert chat["active_compaction_run_id"] is None
    assert chat["reasoning_capability"] in ("switchable", "always", "none", "unknown")

    patched = client.patch(
        f"/v1/assistant/chats/{chat['id']}",
        json={"title": "Ausgaben 2026", "reasoning_override": "off"},
        headers=world["owner"],
    )
    assert patched.status_code == 200
    assert patched.json()["reasoning_override"] == "off"
    # null = zurueck auf die Haushaltseinstellung; ohne Feld bleibt der Wert
    assert (
        client.patch(
            f"/v1/assistant/chats/{chat['id']}", json={"title": "X"}, headers=world["owner"]
        ).json()["reasoning_override"]
        == "off"
    )
    reset = client.patch(
        f"/v1/assistant/chats/{chat['id']}",
        json={"reasoning_override": None},
        headers=world["owner"],
    )
    assert reset.json()["reasoning_override"] is None

    listed = client.get("/v1/assistant/chats", headers=world["owner"]).json()
    assert listed[0]["id"] == chat["id"] and listed[0]["message_count"] == 0

    # Fremder Haushalt, Betrachter im selben Haushalt: beide sehen den Chat nicht.
    for intruder in ("stranger", "viewer"):
        assert (
            client.get(f"/v1/assistant/chats/{chat['id']}", headers=world[intruder]).status_code
            == 404
        )
    assert all(item["id"] != chat["id"] for item in client.get(
        "/v1/assistant/chats", headers=world["viewer"]
    ).json())

    url = f"/v1/assistant/chats/{chat['id']}"
    assert client.delete(url, headers=world["owner"]).status_code == 204
    assert client.get(url, headers=world["owner"]).status_code == 404


# ---------------------------------------------------------------- Senden


def test_send_enqueues_once_per_request_id(world: dict[str, Any], bus: MemoryBus) -> None:
    chat = _new_chat(world["owner"])
    request_id = uuid.uuid4().hex
    first, drain = _send(
        world["owner"], chat["id"], "Wofür gebe ich am meisten aus?", request_id=request_id
    )
    assert first.status_code == 201, first.text
    assert drain.call_count == 1
    body = first.json()
    assert body["user_message"]["role"] == "user"
    assert body["assistant_message"]["status"] == "queued"
    assert body["assistant_message"]["run_id"] == body["run_id"]
    assert body["queue_position"] >= 0

    replay, drain_again = _send(world["owner"], chat["id"], "egal", request_id=request_id)
    assert replay.status_code == 200
    assert drain_again.call_count == 0
    assert replay.json()["run_id"] == body["run_id"]
    assert replay.json()["user_message"]["id"] == body["user_message"]["id"]

    detail = client.get(f"/v1/assistant/chats/{chat['id']}", headers=world["owner"]).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["title"].startswith("Wofür gebe ich")

    task = _sql(
        "select task_type, status, created_by::text, input->>'run_id' from ai_tasks "
        "where input->>'run_id' = %(run)s",
        {"run": body["run_id"]},
    )
    assert task == [("assistant_chat", "queued", world["owner_id"], body["run_id"])]

    # Zwei Fragen hintereinander duerfen gleichzeitig warten (Einreihen).
    second, _ = _send(world["owner"], chat["id"], "Und im August?")
    assert second.status_code == 201
    assert second.json()["run_id"] != body["run_id"]

    # Die erste Stromzeile steht schon: status queued mit Warteplatz.
    first_line = _sql(
        "select seq, payload from assistant_run_events where run_id = %(run)s order by seq",
        {"run": body["run_id"]},
    )
    assert first_line[0][0] == 1
    assert first_line[0][1]["type"] == "status" and first_line[0][1]["status"] == "queued"
    assert "queue_position" in first_line[0][1]

    # POST /v1/ai/tasks legt keine KI-Frage an (dafuer gibt es /assistant).
    refused = client.post(
        "/v1/ai/tasks", json={"task_type": "assistant_chat", "input": {}}, headers=world["owner"]
    )
    assert refused.status_code == 422
    listed = client.get("/v1/ai/tasks", headers=world["owner"])
    assert listed.status_code == 200
    assert any(item["task_type"] == "assistant_chat" for item in listed.json())


def test_ai_disabled_refuses_with_a_named_reason(world: dict[str, Any], bus: MemoryBus) -> None:
    chat = _new_chat(world["owner"])
    _sql(
        "update ai_household_settings set is_enabled = false where household_id = %(h)s",
        {"h": world["household"]},
    )
    try:
        response, drain = _send(world["owner"], chat["id"], "Hallo?")
    finally:
        _sql(
            "update ai_household_settings set is_enabled = true where household_id = %(h)s",
            {"h": world["household"]},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ai_disabled"
    assert drain.call_count == 0


def test_viewer_may_ask_in_own_chats(world: dict[str, Any], bus: MemoryBus) -> None:
    chat = _new_chat(world["viewer"])
    response, _ = _send(world["viewer"], chat["id"], "Was kostet mein Handyvertrag?")
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    # Abbrechen der eigenen Frage geht auch als Betrachter.
    cancelled = client.post(f"/v1/assistant/runs/{run_id}/cancel", headers=world["viewer"])
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    # Der Besitzer des Haushalts sieht den Lauf nicht.
    assert client.get(f"/v1/assistant/runs/{run_id}", headers=world["owner"]).status_code == 404
    assert (
        client.get(f"/v1/assistant/runs/{run_id}/stream", headers=world["owner"]).status_code
        == 404
    )


def test_cancel_of_a_waiting_run_ends_it_at_once(world: dict[str, Any], bus: MemoryBus) -> None:
    chat = _new_chat(world["owner"])
    response, _ = _send(world["owner"], chat["id"], "Wie hoch sind meine Fixkosten?")
    run_id = response.json()["run_id"]
    cancelled = client.post(f"/v1/assistant/runs/{run_id}/cancel", headers=world["owner"])
    assert cancelled.json() == {"status": "cancelled"}
    lines = _stream_lines(world["owner"], run_id)
    assert [line["type"] for line in lines] == ["status", "done"]
    assert lines[-1]["message"]["status"] == "cancelled"
    assert [line["seq"] for line in lines] == [1, 2]
    # nach dem Commit veroeffentlicht (Hintergrundaufgabe)
    assert any(event.get("type") == "done" for _channel, event in bus.published)
    assert _sql(
        "select status from ai_tasks where input->>'run_id' = %(run)s", {"run": run_id}
    ) == [("cancelled",)]


# ---------------------------------------------------------------- Lauf


async def test_a_run_answers_with_tools_and_is_logged(
    world: dict[str, Any], bus: MemoryBus, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    chat = _new_chat(world["owner"])
    response, _ = _send(world["owner"], chat["id"], "Wie viel Geld habe ich auf den Konten?")
    run_id = response.json()["run_id"]
    server = FakeModelServer(_accounts_then_answer())
    async with server.client() as http:
        result = await execute_run(
            uuid.UUID(run_id), session_factory=session_factory, bus=bus, client=http
        )
    assert result.status == "done", result

    detail = client.get(f"/v1/assistant/chats/{chat['id']}", headers=world["owner"]).json()
    answer = detail["messages"][-1]
    assert answer["status"] == "done"
    assert answer["content"].startswith("Du hast Konten")
    assert answer["numbers"]["unverified"] >= 1  # 1.234,56 steht in keinem Ergebnis
    assert answer["run_summary"]["queries"] == 1
    assert answer["context_meter"]["exact"] is True
    assert answer["context_meter"]["window_tokens"] == server.window
    assert detail["context_meter"] == answer["context_meter"]
    assert detail["reasoning_capability"] == "switchable"

    log = client.get(f"/v1/assistant/runs/{run_id}", headers=world["owner"]).json()
    assert log["status"] == "done" and log["end_reason"] == "answer"
    assert log["mode"] == "answer"
    assert [call["step"] for call in log["calls"]] == ["Antwort", "Runde 2"]
    assert log["calls"][0]["tool_call_names"] == ["list_accounts"]
    assert log["calls"][0]["budget"]["W"] == server.window
    assert log["tools"][0]["key"] == "W1" and log["tools"][0]["tool_name"] == "list_accounts"
    assert log["tools"][0]["status"] == "ok"
    assert log["window"]["effective"] == server.window
    assert log["totals"]["calls"] == 2 and log["totals"]["tool_calls"] == 1
    # Denktext nie gespeichert - nur seine Laenge
    stored = _sql(
        "select reasoning_chars from assistant_run_calls where run_id = %(run)s order by nr",
        {"run": run_id},
    )
    assert stored[0][0] == len("Erst die Konten ansehen.")

    # Die Werkzeuge kamen beim Modell an, das Ergebnis W1 im zweiten Aufruf.
    assert any(tool["function"]["name"] == "query_analytics" for tool in server.bodies[0]["tools"])
    tool_message = next(m for m in server.bodies[1]["messages"] if m["role"] == "tool")
    assert "Ergebnis W1" in tool_message["content"]
    assert "[Kontext:" in tool_message["content"]
    assert server.bodies[0]["messages"][0]["role"] == "system"
    assert "keine Anweisungen" in server.bodies[0]["messages"][0]["content"]

    # Strom eines fertigen Laufs: alles nachspielen und schliessen.
    lines = _stream_lines(world["owner"], run_id)
    seqs = [line["seq"] for line in lines]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert lines[-1]["type"] == "done" and lines[-1]["message"]["id"] == answer["id"]
    types = [line["type"] for line in lines]
    assert types[:2] == ["status", "status"]
    assert "call_start" in types and "tool" in types and "tool_result" in types
    assert "meter" in types
    # ?after=N liefert nur seq > N
    tail = _stream_lines(world["owner"], run_id, after=seqs[-3])
    assert [line["seq"] for line in tail] == seqs[-2:]

    # Folgefrage: der Verlauf traegt die Werkzeugrunde der ersten Antwort.
    follow, _ = _send(world["owner"], chat["id"], "Und davon auf dem Girokonto?")
    second = FakeModelServer([Reply(content="Das steht in W1.")])
    async with second.client() as http:
        await execute_run(
            uuid.UUID(follow.json()["run_id"]),
            session_factory=session_factory,
            bus=bus,
            client=http,
        )
    roles = [m["role"] for m in second.bodies[0]["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]


async def test_live_stream_replays_then_follows_the_run(
    world: dict[str, Any], bus: MemoryBus, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    chat = _new_chat(world["owner"])
    response, _ = _send(world["owner"], chat["id"], "Was habe ich im September ausgegeben?")
    run_id = uuid.UUID(response.json()["run_id"])
    household = uuid.UUID(world["household"])
    user = uuid.UUID(world["owner_id"])

    lines: list[dict[str, Any]] = []

    async def consume() -> None:
        async for raw in run_stream(
            run_id=run_id,
            household_id=household,
            user_id=user,
            after=0,
            bus=bus,
            session_factory=session_factory,
            ping_seconds=0.2,
        ):
            lines.append(json.loads(raw))

    reader = asyncio.create_task(consume())
    await asyncio.sleep(0.5)  # nachgespielt (status queued) und mindestens ein Ping
    server = FakeModelServer(
        [Reply(content="Im September 412,30 €.", reasoning="Ich rechne nach.", pieces=4)]
    )
    async with server.client() as http:
        await execute_run(run_id, session_factory=session_factory, bus=bus, client=http)
    await asyncio.wait_for(reader, timeout=10)

    types = [line["type"] for line in lines]
    assert types[0] == "status" and lines[0]["status"] == "queued"
    assert "ping" in types
    assert "think" in types and "delta" in types
    assert types[-1] == "done"
    stored = [line["seq"] for line in lines if "seq" in line]
    assert stored == list(range(1, len(stored) + 1))
    assert all("seq" not in line for line in lines if line["type"] in ("think", "delta", "ping"))
    think = [line for line in lines if line["type"] == "think"]
    assert think[-1]["chars"] == len("Ich rechne nach.")
    assert "".join(line["text"] for line in lines if line["type"] == "delta") == (
        "Im September 412,30 €."
    )


async def test_cancel_closes_a_blocking_model_connection(
    world: dict[str, Any], bus: MemoryBus, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    chat = _new_chat(world["owner"])
    response, _ = _send(world["owner"], chat["id"], "Rechne lange.")
    run_id = response.json()["run_id"]
    _sql(
        "update ai_tasks set status = 'running', started_at = now(), heartbeat_at = now() "
        "where input->>'run_id' = %(run)s",
        {"run": run_id},
    )
    server = FakeModelServer([BLOCK])
    async with server.client() as http:
        task = asyncio.create_task(
            execute_run(uuid.UUID(run_id), session_factory=session_factory, bus=bus, client=http)
        )
        await asyncio.wait_for(server.started.wait(), timeout=10)
        cancelled = await asyncio.to_thread(
            client.post, f"/v1/assistant/runs/{run_id}/cancel", headers=world["owner"]
        )
        assert cancelled.json() == {"status": "running"}
        result = await asyncio.wait_for(task, timeout=10)
    assert result.status == "cancelled"
    assert server.closed == 1  # die Verbindung zum Modell ist zu
    detail = client.get(f"/v1/assistant/chats/{chat['id']}", headers=world["owner"]).json()
    assert detail["messages"][-1]["status"] == "cancelled"
    lines = _stream_lines(world["owner"], run_id)
    assert lines[-1]["type"] == "done" and lines[-1]["message"]["status"] == "cancelled"
    assert _sql(
        "select cancel_requested from ai_tasks where input->>'run_id' = %(run)s", {"run": run_id}
    ) == [(True,)]


async def test_retry_creates_a_new_answer_to_the_same_question(
    world: dict[str, Any], bus: MemoryBus, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    chat = _new_chat(world["owner"])
    response, _ = _send(world["owner"], chat["id"], "Was ist mit meinem Budget?")
    first = response.json()
    server = FakeModelServer(
        [Reply(content="", reasoning="Ich denke und denke", finish_reason="length")] * 3
    )
    async with server.client() as http:
        result = await execute_run(
            uuid.UUID(first["run_id"]), session_factory=session_factory, bus=bus, client=http
        )
    assert result.status == "failed"
    assert result.error_code == "model_budget_exhausted"

    with patch(DRAIN):
        retried = client.post(
            f"/v1/assistant/runs/{first['run_id']}/retry",
            json={"reasoning_override": "off"},
            headers=world["owner"],
        )
    assert retried.status_code == 201, retried.text
    body = retried.json()
    assert body["user_message"]["id"] == first["user_message"]["id"]
    assert body["assistant_message"]["id"] != first["assistant_message"]["id"]
    assert body["run_id"] != first["run_id"]
    assert _sql(
        "select reasoning_override, retry_of_run_id::text from assistant_runs where id = %(id)s",
        {"id": body["run_id"]},
    ) == [("off", first["run_id"])]

    second = FakeModelServer([Reply(content="Dein Budget passt.")])
    async with second.client() as http:
        await execute_run(
            uuid.UUID(body["run_id"]), session_factory=session_factory, bus=bus, client=http
        )
    assert second.bodies[0]["reasoning_effort"] == "none"
    # Die gescheiterte Antwort gehoert nicht in den Verlauf.
    assert [m["role"] for m in second.bodies[0]["messages"]] == ["system", "user"]


async def test_compaction_runs_as_its_own_streamed_run(
    world: dict[str, Any], bus: MemoryBus, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    chat = _new_chat(world["owner"])
    too_early = client.post(
        f"/v1/assistant/chats/{chat['id']}/compact", json={}, headers=world["owner"]
    )
    assert too_early.status_code == 409
    assert too_early.json()["detail"]["code"] == "assistant_too_few_questions"

    answer_ids = []
    for question, answer in (("Frage eins?", "Antwort eins."), ("Frage zwei?", "Antwort zwei.")):
        response, _ = _send(world["owner"], chat["id"], question)
        server = FakeModelServer([Reply(content=answer)])
        async with server.client() as http:
            await execute_run(
                uuid.UUID(response.json()["run_id"]),
                session_factory=session_factory,
                bus=bus,
                client=http,
            )
        answer_ids.append(response.json()["assistant_message"]["id"])

    with patch(DRAIN):
        started = client.post(
            f"/v1/assistant/chats/{chat['id']}/compact",
            json={"focus": "Fixkosten"},
            headers=world["owner"],
        )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    busy = client.get(f"/v1/assistant/chats/{chat['id']}", headers=world["owner"]).json()
    assert busy["active_compaction_run_id"] == run_id
    # waehrend der Verdichtung keine zweite
    with patch(DRAIN):
        again = client.post(
            f"/v1/assistant/chats/{chat['id']}/compact", json={}, headers=world["owner"]
        )
    assert again.status_code == 409

    server = FakeModelServer([Reply(content="1. Auftrag: Fragen eins und zwei.\n2. Frage eins?")])
    async with server.client() as http:
        result = await execute_run(
            uuid.UUID(run_id), session_factory=session_factory, bus=bus, client=http
        )
    assert result.status == "done", result
    instruction = server.bodies[0]["messages"][0]["content"]
    assert "Alle Nutzernachrichten im Wortlaut" in instruction
    assert "Fixkosten" in instruction
    assert "[Nutzer] Frage eins?" in server.bodies[0]["messages"][1]["content"]
    assert "Frage zwei?" not in server.bodies[0]["messages"][1]["content"]

    detail = client.get(f"/v1/assistant/chats/{chat['id']}", headers=world["owner"]).json()
    assert detail["active_compaction_run_id"] is None
    summary = detail["summary"]
    assert summary["run_id"] == run_id and summary["nr"] == 1
    assert summary["covers_until_message_id"] == answer_ids[0]
    assert summary["text"].startswith("1. Auftrag")
    # Die Verdichtung erscheint nicht als Nachricht im Verlauf.
    assert len(detail["messages"]) == 4
    assert _stream_lines(world["owner"], run_id)[-1]["type"] == "done"

    # Die naechste Frage bekommt die Zusammenfassung statt des alten Anfangs.
    response, _ = _send(world["owner"], chat["id"], "Frage drei?")
    third = FakeModelServer([Reply(content="Antwort drei.")])
    async with third.client() as http:
        await execute_run(
            uuid.UUID(response.json()["run_id"]),
            session_factory=session_factory,
            bus=bus,
            client=http,
        )
    sent = third.bodies[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"].startswith("Zusammenfassung des bisherigen Verlaufs (Verdichtung V1)")
    assert "Frage zwei?" in sent[1]["content"]


async def test_chats_survive_a_backup_round_trip(
    bus: MemoryBus, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Die Tabellen des KI-Felds sind Haushaltstabellen: ohne
    Fremdschluessel-Zyklus (Loeschreihenfolge fuer Explorer, Leeren,
    Sicherung) und mit nullbaren Nutzerverweisen wie alle anderen."""
    import io

    token = _signup(f"kifeld-backup-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-12")
    household = client.post(
        "/v1/households", json={"name": "KI-Sicherung"}, headers=_auth_headers(token)
    ).json()
    headers = {**_auth_headers(token), "X-Household-Id": household["id"]}
    chat = _new_chat(headers)
    response, _ = _send(headers, chat["id"], "Wie viel Geld habe ich?")
    run_id = response.json()["run_id"]
    server = FakeModelServer(_accounts_then_answer())
    async with server.client() as http:
        await execute_run(uuid.UUID(run_id), session_factory=session_factory, bus=bus, client=http)

    exported = await asyncio.to_thread(client.get, "/v1/backup/export", headers=headers)
    assert exported.status_code == 200, exported.text
    purged = await asyncio.to_thread(
        client.post,
        "/v1/data-explorer/purge",
        json={"confirm_name": "KI-Sicherung"},
        headers=headers,
    )
    assert purged.status_code == 200, purged.text
    gone = await asyncio.to_thread(client.get, f"/v1/assistant/chats/{chat['id']}", headers=headers)
    assert gone.status_code == 404

    restored = await asyncio.to_thread(
        client.post,
        "/v1/backup/import",
        files={"file": ("sicherung.zip", io.BytesIO(exported.content), "application/zip")},
        data={"confirm_name": "KI-Sicherung"},
        headers=headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["imported_per_table"]["assistant_run_events"] > 0
    detail = client.get(f"/v1/assistant/chats/{chat['id']}", headers=headers).json()
    assert [m["status"] for m in detail["messages"]] == ["done", "done"]
    log = client.get(f"/v1/assistant/runs/{run_id}", headers=headers).json()
    assert log["tools"][0]["key"] == "W1"
    assert _stream_lines(headers, run_id)[-1]["type"] == "done"
