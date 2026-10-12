"""Sammeluebernahme sicherer KI-Vorschlaege (POST /v1/ai/proposals/preview|apply).

Laeuft wie die uebrigen Tests gegen ein echtes lokales Supabase - inklusive
RLS, des Versionstriggers `transactions_bump_version` und des Archiv-Triggers
`reject_archived_category_reference()`.

Die Vorschlaege werden bewusst direkt in `classification_decisions` gestellt
statt ueber einen echten LM-Studio-Lauf: nur so lassen sich Konfidenz,
Reihenfolge und no_match punktgenau setzen - und die Tests brauchen kein
laufendes Modell.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)

# Namen aus dem deutschen Standardpaket (Taxonomie v1), das ein neuer Haushalt
# beim Anlegen installiert - feste UUIDs gibt es also nicht.
GROCERY = "Lebensmittel"
RESTAURANT = "Restaurant"
FUEL = "Tanken & Laden"

MERCHANT_MATCH = "merchant_and_context_match"
NO_MATCHING_CATEGORY = "no_matching_category"


def _household(prefix: str) -> tuple[dict[str, str], str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    household = client.post(
        "/v1/households",
        json={"name": "KI-Vorschlags-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    headers = {**_auth_headers(token), "X-Household-Id": household["id"]}
    return headers, str(household["id"])


@pytest.fixture()
def env() -> dict[str, Any]:
    headers, household_id = _household("aiprop")
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return {
        "headers": headers,
        "household_id": household_id,
        "account_id": str(account["id"]),
    }


def _category_id(headers: dict[str, str], name: str) -> str:
    groups = client.get("/v1/categories", headers=headers).json()
    for group in groups:
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Standardkategorie '{name}' nicht gefunden")


def _new_transaction(
    env: dict[str, Any], *, amount_minor: int = -1000, category_id: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account_id": env["account_id"],
        "booked_date": "2026-07-01",
        "amount_minor": amount_minor,
    }
    if category_id is not None:
        payload["category_id"] = category_id
    response = client.post("/v1/transactions", json=payload, headers=env["headers"])
    assert response.status_code == 201, response.text
    return dict(response.json())


def _split_transaction(env: dict[str, Any]) -> dict[str, Any]:
    tx = _new_transaction(env, amount_minor=-5000)
    response = client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={
            "amount_minor": -5000,
            "splits": [{"amount_minor": -3000}, {"amount_minor": -2000}],
            "expected_version": tx["version"],
        },
        headers=env["headers"],
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _detail(env: dict[str, Any], transaction_id: str) -> dict[str, Any]:
    return dict(client.get(f"/v1/transactions/{transaction_id}", headers=env["headers"]).json())


async def _sql(query: str, params: dict[str, Any]) -> None:
    """Direkter Schreibzugriff fuer Zustaende, die kein Endpunkt herstellt
    (KI-Entscheide, Archivierung, Haushaltsschwelle). Die Session laeuft ohne
    SET ROLE authenticated, also als postgres mit BYPASSRLS."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(text(query), params)


async def _stage_decision(
    env: dict[str, Any],
    transaction_id: str,
    category_id: str | None,
    confidence: float,
    *,
    reason_code: str = MERCHANT_MATCH,
    age_minutes: int = 0,
    source: str = "ai",
    ai_task_id: str | None = None,
) -> None:
    """Stellt einen Vorschlag so ein, wie ihn der Klassifikationslauf schreiben
    wuerde. `age_minutes` macht die Reihenfolge zweier Entscheide
    deterministisch, statt sie der Uhr zu ueberlassen.

    `source='history'` ist der Vorfilter fuer bekannte Empfaenger
    (services/worker/worker/ai/known_payees.py): kein Modell beteiligt,
    deshalb auch kein `model_name`."""
    await _sql(
        "insert into classification_decisions (household_id, transaction_id, payee_name, "
        "category_id, confidence, reason_code, source, model_name, ai_task_id, created_at) values "
        "(:household_id, :transaction_id, 'REWE', :category_id, :confidence, :reason_code, "
        ":source, case when :source = 'ai' then 'test-model' end, :ai_task_id, "
        "now() - make_interval(mins => :age_minutes))",
        {
            "household_id": env["household_id"],
            "transaction_id": transaction_id,
            "category_id": category_id,
            "confidence": confidence,
            "reason_code": reason_code,
            "age_minutes": age_minutes,
            "source": source,
            "ai_task_id": ai_task_id,
        },
    )


async def _stage_classification_task(env: dict[str, Any]) -> tuple[str, str]:
    run_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    await _sql(
        "insert into classification_runs (id, household_id, status, since_date, completed_at) "
        "values (:run_id, :household_id, 'completed', current_date, now())",
        {"run_id": run_id, "household_id": env["household_id"]},
    )
    await _sql(
        "insert into ai_tasks (id, household_id, task_type, status, route_path, "
        "classification_run_id, completed_at) values (:task_id, :household_id, "
        "'transaction_classification', 'completed', '/review', :run_id, now())",
        {
            "run_id": run_id,
            "task_id": task_id,
            "household_id": env["household_id"],
        },
    )
    return run_id, task_id


async def _archive_category(category_id: str) -> None:
    await _sql(
        "update categories set is_archived = true, archived_at = now() where id = :id",
        {"id": category_id},
    )


async def _set_household_threshold(household_id: str, value: float) -> None:
    await _sql(
        "insert into ai_household_settings (household_id, auto_categorize_min_confidence) "
        "values (:household_id, :value) on conflict (household_id) do update "
        "set auto_categorize_min_confidence = excluded.auto_categorize_min_confidence",
        {"household_id": household_id, "value": value},
    )


def _preview(env: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/ai/proposals/preview", json=payload, headers=env["headers"])
    assert response.status_code == 200, response.text
    return dict(response.json())


def _apply(env: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/ai/proposals/apply", json=payload, headers=env["headers"])
    assert response.status_code == 200, response.text
    return dict(response.json())


def _items_by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["transaction_id"]: item for item in result["items"]}


async def test_preview_is_strictly_scoped_to_requested_classification_run(
    env: dict[str, Any],
) -> None:
    grocery = _category_id(env["headers"], GROCERY)
    restaurant = _category_id(env["headers"], RESTAURANT)
    tx = _new_transaction(env)
    run_a, task_a = await _stage_classification_task(env)
    run_b, task_b = await _stage_classification_task(env)
    await _stage_decision(env, tx["id"], grocery, 0.99, ai_task_id=task_a)
    await _stage_decision(
        env, tx["id"], restaurant, 0.95, age_minutes=1, ai_task_id=task_b
    )

    # Obwohl A neuer ist, darf die Vorschau fuer B ausschliesslich B zeigen.
    scoped = _preview(env, {"classification_run_id": run_b})
    assert scoped["applied"] == 1
    assert scoped["items"][0]["category_id"] == restaurant
    assert scoped["items"][0]["source"] == "ai"

    assert _preview(env, {"classification_run_id": run_a})["items"][0][
        "category_id"
    ] == grocery


# ---------------------------------------------------------------------------
# Uebernahme des sicheren Falls
# ---------------------------------------------------------------------------


async def test_high_confidence_proposal_is_applied_and_bumps_version_by_one(
    env: dict[str, Any],
) -> None:
    grocery = _category_id(env["headers"], GROCERY)
    tx = _new_transaction(env)
    await _stage_decision(env, tx["id"], grocery, 0.95)

    payload = {"transaction_ids": [tx["id"]]}
    preview = _preview(env, payload)
    assert preview["min_confidence"] == 0.85  # Spaltendefault, kein Haushaltswert gesetzt
    assert preview["applied"] == 1
    item = preview["items"][0]
    assert item["status"] == "applied"
    assert item["reason"] is None
    assert item["category_id"] == grocery
    assert item["category_name"] == GROCERY
    assert item["confidence"] == 0.95
    assert item["reason_code"] == MERCHANT_MATCH

    # Die Vorschau darf nichts geschrieben haben - die Request-Transaktion wird
    # committet, ein versehentliches UPDATE bliebe also bestehen.
    untouched = _detail(env, tx["id"])
    assert untouched["category_id"] is None
    assert untouched["version"] == tx["version"]

    applied = _apply(env, payload)
    assert applied["applied"] == 1
    assert applied["items"][0]["current_version"] == tx["version"] + 1

    after = _detail(env, tx["id"])
    assert after["category_id"] == grocery
    # Genau ein Schritt, damit andere Geraete keine Phantomkonflikte sehen.
    assert after["version"] == tx["version"] + 1

    # Die Herkunft weist die KI aus, nicht eine manuelle Zuordnung.
    provenance = client.get(
        f"/v1/transactions/{tx['id']}/provenance", headers=env["headers"]
    ).json()
    split = provenance["splits"][0]
    assert split["category_source"] == "ai"
    assert split["confidence"] == 0.95
    assert split["decision_id"] is not None


async def test_history_proposal_is_applied_but_not_credited_to_the_ai(
    env: dict[str, Any],
) -> None:
    """Der Vorfilter fuer bekannte Empfaenger erzeugt denselben Vorschlag wie
    das Modell, nur ohne Modellaufruf (source='history'). Uebernommen wird er
    auf demselben Weg - aber an der Buchung darf er nicht als KI-Ergebnis
    erscheinen: es lief kein Modell, und die Herkunftsansicht wuerde eine
    Aussage der KI behaupten, die es nie gab."""
    grocery = _category_id(env["headers"], GROCERY)
    tx = _new_transaction(env)
    await _stage_decision(env, tx["id"], grocery, 0.95, reason_code="known_payee", source="history")

    applied = _apply(env, {"transaction_ids": [tx["id"]]})
    assert applied["applied"] == 1
    assert applied["items"][0]["reason_code"] == "known_payee"
    assert _detail(env, tx["id"])["category_id"] == grocery

    provenance = client.get(
        f"/v1/transactions/{tx['id']}/provenance", headers=env["headers"]
    ).json()
    split = provenance["splits"][0]
    assert split["category_source"] == "history"
    assert split["decision_id"] is not None
    assert split["confidence"] == 0.95
    # Kein Modellname, weil kein Modell beteiligt war.
    assert split["model_name"] is None


async def test_preview_is_repeatable_and_writes_nothing(env: dict[str, Any]) -> None:
    grocery = _category_id(env["headers"], GROCERY)
    tx = _new_transaction(env)
    await _stage_decision(env, tx["id"], grocery, 0.99)

    first = _preview(env, {"transaction_ids": [tx["id"]]})
    second = _preview(env, {"transaction_ids": [tx["id"]]})
    assert first["applied"] == second["applied"] == 1

    after = _detail(env, tx["id"])
    assert after["category_id"] is None
    assert after["version"] == tx["version"]


# ---------------------------------------------------------------------------
# Was NICHT uebernommen werden darf
# ---------------------------------------------------------------------------


async def test_low_confidence_and_no_match_are_skipped(env: dict[str, Any]) -> None:
    grocery = _category_id(env["headers"], GROCERY)
    unsure = _new_transaction(env)
    no_match = _new_transaction(env)
    await _stage_decision(env, unsure["id"], grocery, 0.50)
    # no_match: gueltige Antwort der KI, category_id ist NULL - und selbst eine
    # hohe Konfidenz darf daraus keine erzwungene Kategorie machen.
    await _stage_decision(
        env, no_match["id"], None, 0.99, reason_code=NO_MATCHING_CATEGORY
    )

    result = _apply(env, {"transaction_ids": [unsure["id"], no_match["id"]]})
    items = _items_by_id(result)
    assert result["applied"] == 0
    assert result["skipped"] == 2
    assert items[unsure["id"]]["status"] == "skipped"
    assert items[unsure["id"]]["reason"] == "below_confidence"
    assert items[no_match["id"]]["reason"] == "no_match"
    assert items[no_match["id"]]["category_id"] is None
    assert items[no_match["id"]]["reason_code"] == NO_MATCHING_CATEGORY

    for tx in (unsure, no_match):
        assert _detail(env, tx["id"])["category_id"] is None
        assert _detail(env, tx["id"])["version"] == tx["version"]


async def test_human_decisions_and_split_bookings_are_never_overwritten(
    env: dict[str, Any],
) -> None:
    grocery = _category_id(env["headers"], GROCERY)
    restaurant = _category_id(env["headers"], RESTAURANT)

    already = _new_transaction(env, category_id=restaurant)
    split_tx = _split_transaction(env)
    archived = _new_transaction(env)
    client.delete(f"/v1/transactions/{archived['id']}", headers=env["headers"])

    for transaction_id in (already["id"], split_tx["id"], archived["id"]):
        await _stage_decision(env, transaction_id, grocery, 0.99)

    result = _apply(
        env, {"transaction_ids": [already["id"], split_tx["id"], archived["id"]]}
    )
    items = _items_by_id(result)
    assert result["applied"] == 0
    assert result["unchanged"] == 1
    assert result["skipped"] == 2
    assert items[already["id"]]["status"] == "unchanged"
    assert items[already["id"]]["reason"] == "already_categorized"
    assert items[split_tx["id"]]["reason"] == "split_transaction"
    assert items[archived["id"]]["reason"] == "archived_transaction"

    # Die manuelle Zuordnung steht unveraendert, die Version ist nicht gestiegen.
    unchanged = _detail(env, already["id"])
    assert unchanged["category_id"] == restaurant
    assert unchanged["version"] == already["version"]
    assert all(s["category_id"] is None for s in _detail(env, split_tx["id"])["splits"])


async def test_archived_target_category_is_skipped_while_the_rest_is_applied(
    env: dict[str, Any],
) -> None:
    """Eine seit dem Vorschlag archivierte Kategorie darf den Vorgang nicht
    abbrechen - sie kostet genau eine Buchung."""
    grocery = _category_id(env["headers"], GROCERY)
    fuel = _category_id(env["headers"], FUEL)
    stale = _new_transaction(env)
    fine = _new_transaction(env)
    await _stage_decision(env, stale["id"], fuel, 0.99)
    await _stage_decision(env, fine["id"], grocery, 0.99)
    await _archive_category(fuel)

    result = _apply(env, {"transaction_ids": [stale["id"], fine["id"]]})
    items = _items_by_id(result)
    assert result["applied"] == 1
    assert result["skipped"] == 1
    assert items[stale["id"]]["reason"] == "category_inactive"
    assert items[fine["id"]]["status"] == "applied"

    assert _detail(env, stale["id"])["category_id"] is None
    assert _detail(env, stale["id"])["version"] == stale["version"]
    assert _detail(env, fine["id"])["category_id"] == grocery


async def test_only_the_newest_decision_per_transaction_counts(env: dict[str, Any]) -> None:
    """classification_decisions ist append-only: ein aelterer Vorschlag darf
    einen neueren nie ueberstimmen."""
    grocery = _category_id(env["headers"], GROCERY)
    restaurant = _category_id(env["headers"], RESTAURANT)
    tx = _new_transaction(env)
    await _stage_decision(env, tx["id"], restaurant, 0.99, age_minutes=30)
    await _stage_decision(env, tx["id"], grocery, 0.99, age_minutes=0)

    result = _apply(env, {"transaction_ids": [tx["id"]]})
    assert result["items"][0]["category_id"] == grocery
    assert _detail(env, tx["id"])["category_id"] == grocery


async def test_unknown_and_foreign_transactions_are_reported_as_not_found(
    env: dict[str, Any],
) -> None:
    foreign_headers, _ = _household("aiprop-foreign")
    foreign_account = client.post(
        "/v1/accounts",
        json={"name": "Fremdkonto", "account_type": "checking"},
        headers=foreign_headers,
    ).json()
    foreign_tx = client.post(
        "/v1/transactions",
        json={
            "account_id": foreign_account["id"],
            "booked_date": "2026-07-01",
            "amount_minor": -1000,
        },
        headers=foreign_headers,
    ).json()
    unknown_id = str(uuid.uuid4())

    result = _apply(env, {"transaction_ids": [unknown_id, str(foreign_tx["id"])]})
    items = _items_by_id(result)
    assert result["skipped"] == 2
    # Fremde und unbekannte Buchungen sind ununterscheidbar - die Existenz einer
    # fremden Buchung wird nicht verraten.
    assert items[unknown_id]["reason"] == "transaction_not_found"
    assert items[str(foreign_tx["id"])]["reason"] == "transaction_not_found"


# ---------------------------------------------------------------------------
# Schwelle, Idempotenz, Auswahl
# ---------------------------------------------------------------------------


async def test_explicit_min_confidence_overrides_household_setting(
    env: dict[str, Any],
) -> None:
    grocery = _category_id(env["headers"], GROCERY)
    tx = _new_transaction(env)
    await _stage_decision(env, tx["id"], grocery, 0.90)
    await _set_household_threshold(env["household_id"], 0.95)

    strict = _preview(env, {"transaction_ids": [tx["id"]]})
    assert strict["min_confidence"] == 0.95
    assert strict["items"][0]["reason"] == "below_confidence"

    relaxed = _apply(env, {"transaction_ids": [tx["id"]], "min_confidence": 0.5})
    assert relaxed["min_confidence"] == 0.5
    assert relaxed["applied"] == 1
    assert _detail(env, tx["id"])["category_id"] == grocery


async def test_apply_twice_never_writes_twice(env: dict[str, Any]) -> None:
    grocery = _category_id(env["headers"], GROCERY)
    replayed = _new_transaction(env)
    retried = _new_transaction(env)
    await _stage_decision(env, replayed["id"], grocery, 0.99)
    await _stage_decision(env, retried["id"], grocery, 0.99)

    # a) Mit request_id: der zweite Aufruf liefert ausdruecklich das Ergebnis
    #    des ersten zurueck, statt erneut zu bewerten.
    payload = {"transaction_ids": [replayed["id"]], "request_id": "batch-42"}
    first = _apply(env, payload)
    assert first["applied"] == 1
    assert first["idempotent_replay"] is False

    second = _apply(env, payload)
    assert second["idempotent_replay"] is True
    assert second["applied"] == 1
    assert second["items"] == first["items"]
    assert _detail(env, replayed["id"])["version"] == replayed["version"] + 1

    # b) Ohne request_id greift die zweite Schutzschicht: die Buchung ist
    #    inzwischen kategorisiert und bleibt unveraendert.
    plain = {"transaction_ids": [retried["id"]]}
    assert _apply(env, plain)["applied"] == 1
    repeat = _apply(env, plain)
    assert repeat["applied"] == 0
    assert repeat["unchanged"] == 1
    assert repeat["items"][0]["reason"] == "already_categorized"
    assert _detail(env, retried["id"])["version"] == retried["version"] + 1


async def test_without_transaction_ids_all_open_proposals_are_taken(
    env: dict[str, Any],
) -> None:
    grocery = _category_id(env["headers"], GROCERY)
    restaurant = _category_id(env["headers"], RESTAURANT)
    open_a = _new_transaction(env)
    open_b = _new_transaction(env)
    settled = _new_transaction(env, category_id=restaurant)
    without_proposal = _new_transaction(env)
    for transaction_id in (open_a["id"], open_b["id"], settled["id"]):
        await _stage_decision(env, transaction_id, grocery, 0.99)

    result = _apply(env, {})
    items = _items_by_id(result)
    assert result["applied"] == 2
    assert set(items) == {open_a["id"], open_b["id"]}
    # Bereits kategorisierte Buchungen und Buchungen ohne Vorschlag gehoeren
    # nicht zu den "offenen Vorschlaegen" und tauchen gar nicht erst auf.
    assert settled["id"] not in items
    assert without_proposal["id"] not in items
    assert _detail(env, settled["id"])["category_id"] == restaurant


def test_more_than_the_batch_limit_is_rejected(env: dict[str, Any]) -> None:
    response = client.post(
        "/v1/ai/proposals/apply",
        json={"transaction_ids": [str(uuid.uuid4()) for _ in range(501)]},
        headers=env["headers"],
    )
    assert response.status_code == 422


def test_viewer_may_neither_preview_nor_apply(env: dict[str, Any]) -> None:
    viewer_headers = _invite_member(
        client, env["headers"], env["household_id"], "viewer", "aiprop-viewer"
    )
    for endpoint in ("preview", "apply"):
        response = client.post(
            f"/v1/ai/proposals/{endpoint}", json={}, headers=viewer_headers
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "household_role_readonly"
