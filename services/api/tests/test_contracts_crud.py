"""Vertragsverwaltung: CRUD, Nebenlaeufigkeit, Lebenszyklus, Zusammenfassung.

Laeuft wie alle API-Tests gegen ein laufendes lokales Supabase.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services import contract_service
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

CAT_GROCERIES = "Lebensmittel"


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"ct-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Vertrags-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _category_id(headers: dict[str, str]) -> str:
    groups = client.get("/v1/categories", headers=headers).json()
    for group in groups:
        for category in group["categories"]:
            if category["name"] == CAT_GROCERIES:
                return str(category["id"])
    raise AssertionError(f"Default-Kategorie '{CAT_GROCERIES}' nicht gefunden")


def _create(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Beispielvertrag",
        "amount_minor": 1999,
        "frequency": "monthly",
        "next_due_date": date.today().isoformat(),
    }
    body.update(overrides)
    response = client.post("/v1/contracts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _fetch_audit_events(household_id: str, entity_id: str) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        res = await session.execute(
            text(
                "select action, actor_user_id, entity_type, before, after from audit_log "
                "where household_id = :household_id and entity_id = :entity_id "
                "order by created_at asc"
            ),
            {"household_id": household_id, "entity_id": entity_id},
        )
        return [dict(row._mapping) for row in res]


# ---------------------------------------------------------------------------
# Grundlegendes CRUD
# ---------------------------------------------------------------------------


def test_create_list_and_get_contract(household_headers: dict[str, str]) -> None:
    created = _create(
        household_headers,
        name="Streaming Nordlicht",
        provider_label="Nordlicht Media GmbH",
        contract_type="subscription",
        amount_minor=1299,
        category_id=_category_id(household_headers),
    )
    assert created["version"] == 1
    assert created["direction"] == "expense"
    assert created["amount_minor"] == 1299
    assert created["status"] == "active"
    assert created["source"] == "manual"

    listed = client.get("/v1/contracts", headers=household_headers).json()
    assert [item["id"] for item in listed] == [created["id"]]

    single = client.get(f"/v1/contracts/{created['id']}", headers=household_headers)
    assert single.status_code == 200
    assert single.headers["ETag"] == '"1"'


def test_amount_is_always_positive_and_direction_carries_the_sign(
    household_headers: dict[str, str],
) -> None:
    """Kern der Vorzeichennormierung: eine Einnahme und eine Ausgabe speichern
    beide einen positiven Betrag und unterscheiden sich nur in `direction`."""
    ausgabe = _create(household_headers, name="Miete", amount_minor=115000)
    einnahme = _create(
        household_headers,
        name="Gehalt",
        amount_minor=320000,
        direction="income",
        contract_type="income",
    )
    assert ausgabe["amount_minor"] > 0
    assert einnahme["amount_minor"] > 0
    assert ausgabe["direction"] == "expense"
    assert einnahme["direction"] == "income"

    # Ein negativer Betrag ist gar nicht erst erfassbar.
    rejected = client.post(
        "/v1/contracts",
        json={
            "name": "Falschherum",
            "amount_minor": -500,
            "frequency": "monthly",
            "next_due_date": date.today().isoformat(),
        },
        headers=household_headers,
    )
    assert rejected.status_code == 422


def test_patch_requires_if_match_and_bumps_version(household_headers: dict[str, str]) -> None:
    created = _create(household_headers)

    without_header = client.patch(
        f"/v1/contracts/{created['id']}",
        json={"name": "Neuer Name"},
        headers=household_headers,
    )
    assert without_header.status_code == 428
    assert without_header.json()["detail"] == "if_match_required"

    stale = client.patch(
        f"/v1/contracts/{created['id']}",
        json={"name": "Neuer Name"},
        headers={**household_headers, "If-Match": '"7"'},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "version_conflict"
    assert stale.headers["ETag"] == '"1"'

    ok = client.patch(
        f"/v1/contracts/{created['id']}",
        json={"name": "Neuer Name", "tolerance_days": 9},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert ok.status_code == 200
    patched = ok.json()
    assert patched["name"] == "Neuer Name"
    assert patched["tolerance_days"] == 9
    assert patched["version"] == 2
    # Nicht mitgeschickte Felder bleiben unangetastet.
    assert patched["frequency"] == "monthly"
    assert ok.headers["ETag"] == '"2"'


def test_empty_patch_does_not_bump_version(household_headers: dict[str, str]) -> None:
    created = _create(household_headers)
    response = client.patch(
        f"/v1/contracts/{created['id']}",
        json={},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1


def test_non_monthly_expenses_default_to_reserve_and_ineligible_changes_disable_it(
    household_headers: dict[str, str],
) -> None:
    yearly = _create(
        household_headers,
        name="Hausrat Jahresbeitrag",
        amount_minor=120_000,
        frequency="yearly",
        next_due_date="2027-08-15",
    )
    assert yearly["reserve_enabled"] is True
    assert yearly["reserve_started_on"] == date.today().isoformat()
    assert yearly["reserve_starting_amount_minor"] == 0
    assert yearly["reserve_monthly_minor"] > 0

    opted_out = _create(
        household_headers,
        name="Jahresbeitrag ohne Glättung",
        amount_minor=60_000,
        frequency="yearly",
        next_due_date="2027-08-15",
        reserve_enabled=False,
    )
    assert opted_out["reserve_enabled"] is False
    assert opted_out["reserve_monthly_minor"] == 0

    updated = client.patch(
        f"/v1/contracts/{yearly['id']}",
        json={"frequency": "monthly", "reserve_enabled": True},
        headers={**household_headers, "If-Match": f'"{yearly["version"]}"'},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["frequency"] == "monthly"
    assert updated.json()["reserve_enabled"] is False
    assert updated.json()["reserve_starting_amount_minor"] == 0
    assert updated.json()["version"] == yearly["version"] + 1


def test_reserve_start_assumption_is_editable_with_optimistic_concurrency(
    household_headers: dict[str, str],
) -> None:
    contract = _create(
        household_headers,
        frequency="quarterly",
        amount_minor=30_000,
        next_due_date="2027-08-15",
        reserve_enabled=True,
        reserve_started_on="2027-06-03",
        reserve_starting_amount_minor=5_000,
    )
    updated = client.patch(
        f"/v1/contracts/{contract['id']}",
        json={
            "reserve_started_on": "2027-06-10",
            "reserve_starting_amount_minor": 10_000,
        },
        headers={**household_headers, "If-Match": f'"{contract["version"]}"'},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["reserve_started_on"] == "2027-06-10"
    assert updated.json()["reserve_starting_amount_minor"] == 10_000
    assert updated.json()["version"] == contract["version"] + 1


def test_clear_fields_empties_a_value(household_headers: dict[str, str]) -> None:
    created = _create(household_headers, note="Wichtige Notiz", ends_on="2027-01-31")
    response = client.patch(
        f"/v1/contracts/{created['id']}",
        json={"clear_fields": ["note", "ends_on"]},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert response.status_code == 200
    assert response.json()["note"] is None
    assert response.json()["ends_on"] is None


def test_patching_the_due_date_moves_the_target_day_with_it(
    household_headers: dict[str, str],
) -> None:
    """Das Bearbeiten-Formular schickt nur `next_due_date`, nie `due_day`.

    Bliebe der Zieltag auf dem alten Wert, rechnete der Abgleichsmotor mit ihm
    weiter (`contract_matching_service._advance`) und machte die Korrektur des
    Nutzers mit der ersten Fortschreibung wieder rueckgaengig: das Suchfenster
    laege einen halben Monat neben der tatsaechlichen Abbuchung.
    """
    created = _create(household_headers, next_due_date="2026-08-15")
    assert created["due_day"] == 15

    updated = client.patch(
        f"/v1/contracts/{created['id']}",
        json={"next_due_date": "2026-08-01"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["next_due_date"] == "2026-08-01"
    assert updated.json()["due_day"] == 1


def test_an_explicit_due_day_beats_the_derivation(household_headers: dict[str, str]) -> None:
    """Gegenprobe: wer den Zieltag selbst mitschickt, meint ihn auch so."""
    created = _create(household_headers, next_due_date="2026-08-15")

    updated = client.patch(
        f"/v1/contracts/{created['id']}",
        json={"next_due_date": "2026-08-01", "due_day": 28},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["due_day"] == 28

    cleared = client.patch(
        f"/v1/contracts/{created['id']}",
        json={"next_due_date": "2026-09-03", "clear_fields": ["due_day"]},
        headers={**household_headers, "If-Match": '"2"'},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["due_day"] is None


def test_unknown_contract_is_404(household_headers: dict[str, str]) -> None:
    missing = uuid.uuid4()
    assert client.get(f"/v1/contracts/{missing}", headers=household_headers).status_code == 404
    assert (
        client.delete(f"/v1/contracts/{missing}", headers=household_headers).status_code == 404
    )


@pytest.mark.asyncio
async def test_contract_changes_are_audited(household_headers: dict[str, str]) -> None:
    household_id = household_headers["X-Household-Id"]
    created = _create(household_headers)

    client.patch(
        f"/v1/contracts/{created['id']}",
        json={"name": "Umbenannt"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert (
        client.delete(f"/v1/contracts/{created['id']}", headers=household_headers).status_code
        == 204
    )

    events = await _fetch_audit_events(household_id, created["id"])
    assert [event["action"] for event in events] == ["create", "update", "delete"]
    assert {event["entity_type"] for event in events} == {"contract"}
    assert all(event["actor_user_id"] is not None for event in events)
    assert events[1]["before"]["version"] == 1
    assert events[1]["after"]["version"] == 2
    assert events[2]["after"] is None


# ---------------------------------------------------------------------------
# Lebenszyklus
# ---------------------------------------------------------------------------


def test_cancelled_contract_keeps_counting_until_its_end_date(
    household_headers: dict[str, str],
) -> None:
    """Gekuendigt ist nicht beendet: bis zum Enddatum kostet der Vertrag Geld."""
    today = date.today()
    created = _create(household_headers, name="Fitnessstudio", amount_minor=2990)

    response = client.post(
        f"/v1/contracts/{created['id']}/status",
        json={
            "status": "cancelled",
            "cancelled_on": today.isoformat(),
            "ends_on": (today + timedelta(days=45)).isoformat(),
        },
        headers=household_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    summary = client.get("/v1/contracts/summary", headers=household_headers).json()
    assert summary["monthly_expense_minor"] == 2990
    assert summary["cancelled_count"] == 1
    assert [item["contract_id"] for item in summary["ending_soon"]] == [created["id"]]

    # Nach dem Beenden faellt er aus der Summe.
    ended = client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "ended"},
        headers=household_headers,
    )
    assert ended.status_code == 200
    summary_after = client.get("/v1/contracts/summary", headers=household_headers).json()
    assert summary_after["monthly_expense_minor"] == 0
    assert summary_after["ended_count"] == 1


def test_invalid_status_transition_is_rejected(household_headers: dict[str, str]) -> None:
    created = _create(household_headers)
    client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "not_a_contract"},
        headers=household_headers,
    )
    # Aus einer verneinten Fehlerkennung fuehrt nur der Weg zurueck auf aktiv.
    response = client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "cancelled"},
        headers=household_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "contract_status_invalid"


@pytest.mark.asyncio
async def test_reactivating_clears_the_missed_counter(
    household_headers: dict[str, str],
) -> None:
    """Sonst wuerde die Automatik einen wieder aufgenommenen Vertrag sofort
    erneut beenden."""
    created = _create(household_headers)

    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text("update contracts set missed_periods = 3 where id = :id"),
            {"id": created["id"]},
        )
        await session.commit()

    response = client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "paused"},
        headers=household_headers,
    )
    assert response.status_code == 200
    assert response.json()["missed_periods"] == 0


def test_reactivating_pulls_the_due_date_into_the_future(
    household_headers: dict[str, str],
) -> None:
    """Der geloeschte Fehlzaehler allein genuegt nicht.

    Stuende `next_due_date` weiter ein halbes Jahr in der Vergangenheit, holte
    der naechtliche Abgleich dieselben Perioden erneut nach, zaehlte sie wieder
    als ausgeblieben und die Automatik beendete den Vertrag am Morgen nach der
    Reaktivierung erneut - mit frei erfundenen 'missing'-Faelligkeiten. Der
    Inbox-Pfad (routes/review.py) zieht das Datum dafuer ausdruecklich vor.
    """
    today = date.today()
    stale_due = today - timedelta(days=200)
    created = _create(household_headers, next_due_date=stale_due.isoformat())

    paused = client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "paused"},
        headers=household_headers,
    )
    assert paused.status_code == 200
    # Pausiert wird nicht abgeglichen - das Datum bleibt unangetastet.
    assert paused.json()["next_due_date"] == stale_due.isoformat()

    resumed = client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "active"},
        headers=household_headers,
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["missed_periods"] == 0
    assert date.fromisoformat(body["next_due_date"]) >= today
    # Auf dem Monatsraster geblieben, nicht auf "heute" gesetzt.
    assert date.fromisoformat(body["next_due_date"]).day == stale_due.day


def test_reactivating_drops_an_end_date_that_already_passed(
    household_headers: dict[str, str],
) -> None:
    """Sonst setzte der 'term_reached'-Zweig des Abgleichs den Vertrag im
    naechsten Lauf sofort wieder auf 'ended' - das Rueckgaengigmachen des Endes
    waere fuer den Nutzer nicht durchfuehrbar."""
    today = date.today()
    created = _create(
        household_headers,
        next_due_date=(today - timedelta(days=60)).isoformat(),
        status="cancelled",
        cancelled_on=(today - timedelta(days=90)).isoformat(),
        ends_on=(today - timedelta(days=30)).isoformat(),
    )
    assert (
        client.post(
            f"/v1/contracts/{created['id']}/status",
            json={"status": "ended"},
            headers=household_headers,
        ).status_code
        == 200
    )

    resumed = client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "active"},
        headers=household_headers,
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] == "active"
    assert body["ends_on"] is None
    assert body["cancelled_on"] is None
    assert date.fromisoformat(body["next_due_date"]) >= today


def test_reactivating_keeps_an_end_date_in_the_future(
    household_headers: dict[str, str],
) -> None:
    """Gegenprobe: nur ein bereits verstrichenes Ende wird geraeumt. Ein
    kuenftiges Enddatum ist eine gueltige Nutzerangabe."""
    today = date.today()
    future_end = today + timedelta(days=90)
    created = _create(household_headers, ends_on=future_end.isoformat())
    client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "paused"},
        headers=household_headers,
    )

    resumed = client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "active"},
        headers=household_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["ends_on"] == future_end.isoformat()


# ---------------------------------------------------------------------------
# Betragsverlauf
# ---------------------------------------------------------------------------


def test_amount_change_opens_a_new_price_step(household_headers: dict[str, str]) -> None:
    """Der Fall MaxEnergy: 147,00 EUR wird zu 123,00 EUR und der Verlauf zeigt
    beide Stufen statt zwei getrennter Vertraege."""
    created = _create(
        household_headers,
        name="MaxEnergy Strom",
        amount_minor=14700,
        next_due_date=(date.today() - timedelta(days=30)).isoformat(),
    )

    history = client.get(
        f"/v1/contracts/{created['id']}/history", headers=household_headers
    ).json()
    assert len(history) == 1
    assert history[0]["amount_minor"] == 14700
    assert history[0]["change_reason"] == "initial"
    assert history[0]["valid_to"] is None

    changed = client.patch(
        f"/v1/contracts/{created['id']}",
        json={"amount_minor": 12300},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert changed.status_code == 200
    assert changed.json()["amount_minor"] == 12300

    history_after = client.get(
        f"/v1/contracts/{created['id']}/history", headers=household_headers
    ).json()
    assert [entry["amount_minor"] for entry in history_after] == [14700, 12300]
    assert history_after[0]["valid_to"] is not None
    assert history_after[1]["valid_to"] is None
    assert history_after[1]["delta_minor"] == -2400

    events = client.get(
        f"/v1/contracts/{created['id']}/events", headers=household_headers
    ).json()
    assert "created" in [event["kind"] for event in events]


def test_manual_history_entry_records_a_price_change_event(
    household_headers: dict[str, str],
) -> None:
    created = _create(
        household_headers,
        amount_minor=5000,
        next_due_date=(date.today() - timedelta(days=30)).isoformat(),
    )
    response = client.post(
        f"/v1/contracts/{created['id']}/history",
        json={"amount_minor": 5500, "valid_from": date.today().isoformat()},
        headers=household_headers,
    )
    assert response.status_code == 201
    assert response.json()["amount_minor"] == 5500

    events = client.get(
        f"/v1/contracts/{created['id']}/events", headers=household_headers
    ).json()
    price_events = [event for event in events if event["kind"] == "price_change"]
    assert len(price_events) == 1
    assert price_events[0]["payload"]["delta_minor"] == 500


# ---------------------------------------------------------------------------
# Zusammenfassung, Prognose, Einstellungen, Duplikate
# ---------------------------------------------------------------------------


def test_summary_separates_income_from_expenses(household_headers: dict[str, str]) -> None:
    _create(household_headers, name="Miete", amount_minor=115000, contract_type="rent")
    _create(household_headers, name="Kfz-Steuer", amount_minor=12000, frequency="yearly")
    _create(
        household_headers,
        name="Gehalt",
        amount_minor=320000,
        direction="income",
        contract_type="income",
    )

    summary = client.get("/v1/contracts/summary", headers=household_headers).json()
    assert summary["monthly_expense_minor"] == 115000 + 1000
    assert summary["monthly_income_minor"] == 320000
    assert summary["monthly_net_minor"] == 320000 - 116000
    assert summary["yearly_expense_minor"] == 115000 * 12 + 12000
    assert summary["active_count"] == 3
    assert {item["contract_type"] for item in summary["by_type"]} == {
        "rent",
        "other",
        "income",
    }


def test_summary_groups_by_category_with_uncategorized_bucket(
    household_headers: dict[str, str],
) -> None:
    """by_category ist die fuehrende Gruppierung (statt Vertragsart): zwei
    Vertraege in derselben Kategorie zaehlen zusammen, ein Vertrag ohne
    Kategorie landet in einem eigenen `category_id: null`-Eintrag statt
    verworfen zu werden."""
    groceries_id = _category_id(household_headers)
    _create(
        household_headers,
        name="Bio-Kiste",
        amount_minor=3000,
        category_id=groceries_id,
    )
    _create(
        household_headers,
        name="Getraenkelieferant",
        amount_minor=1500,
        category_id=groceries_id,
    )
    _create(household_headers, name="Ohne Kategorie", amount_minor=999)

    summary = client.get("/v1/contracts/summary", headers=household_headers).json()
    by_category = {
        item["category_id"]: item for item in summary["by_category"]
    }
    assert by_category[groceries_id]["count"] == 2
    assert by_category[groceries_id]["monthly_minor"] == 4500
    assert by_category[None]["count"] == 1
    assert by_category[None]["monthly_minor"] == 999


async def test_future_contract_is_excluded_from_summary_until_it_starts(
    household_headers: dict[str, str],
) -> None:
    """Ein Vertrag mit `starts_on` in der Zukunft ist angelegt, aber
    verursacht noch keine Kosten (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt
    7 "Verträge, Datenkorrektur").

    Er bleibt ueber `GET /v1/contracts` sichtbar - dort steht `starts_on`, aus
    dem sich "Beginnt am ..." ableiten laesst -, zaehlt aber nicht in
    Monats-/Jahressummen, Kategorieauswertungen, der Rangliste oder
    "demnaechst faellig" mit. Ab seinem Startdatum zaehlt er dann mit.
    """
    household_id = household_headers["X-Household-Id"]
    today = date.today()
    future_start = today + timedelta(days=10)
    groceries_id = _category_id(household_headers)

    running = _create(
        household_headers,
        name="Laufender Vertrag",
        amount_minor=2000,
        category_id=groceries_id,
    )
    future = _create(
        household_headers,
        name="Neuer Vertrag",
        amount_minor=5000,
        starts_on=future_start.isoformat(),
        next_due_date=future_start.isoformat(),
        category_id=groceries_id,
    )

    summary = client.get("/v1/contracts/summary", headers=household_headers).json()
    assert summary["monthly_expense_minor"] == 2000
    assert summary["yearly_expense_minor"] == 2000 * 12
    assert {item["contract_id"] for item in summary["yearly_by_contract"]} == {running["id"]}
    by_category = {item["category_id"]: item for item in summary["by_category"]}
    assert by_category[groceries_id]["count"] == 1
    assert by_category[groceries_id]["monthly_minor"] == 2000
    assert future["id"] not in {item["contract_id"] for item in summary["upcoming"]}

    # Die volle Liste zeigt ihn weiterhin, mit lesbarem starts_on - daraus
    # baut die Oberflaeche "Beginnt am ...", ohne dass ein neues Feld noetig
    # waere.
    listed = {
        item["id"]: item for item in client.get("/v1/contracts", headers=household_headers).json()
    }
    assert listed[future["id"]]["starts_on"] == future_start.isoformat()

    # Der HTTP-Endpunkt rechnet immer mit dem Servertag; um "ab seinem
    # Startdatum" zu pruefen, ruft der Test build_summary direkt mit einem
    # vorgezogenen `today` auf (gleiches Muster wie `_reconcile` in
    # test_contracts_reference_cases.py: eigene Sitzung ohne RLS, household_id
    # steht explizit im Filter).
    session_factory = get_session_factory()
    async with session_factory() as session:
        summary_started = await contract_service.build_summary(
            session, uuid.UUID(household_id), today=future_start
        )
    assert summary_started.monthly_expense_minor == 2000 + 5000
    assert {item.contract_id for item in summary_started.yearly_by_contract} == {
        uuid.UUID(running["id"]),
        uuid.UUID(future["id"]),
    }
    assert uuid.UUID(future["id"]) in {item.contract_id for item in summary_started.upcoming}


def test_forecast_lists_repeated_future_due_dates(household_headers: dict[str, str]) -> None:
    today = date.today()
    _create(
        household_headers,
        name="Monatsbeitrag",
        amount_minor=1000,
        next_due_date=today.isoformat(),
    )
    forecast = client.get(
        "/v1/contracts/forecast", params={"days": 100}, headers=household_headers
    ).json()
    assert len(forecast) >= 3
    assert forecast == sorted(forecast, key=lambda item: item["due_date"])
    assert all(item["due_date"] >= today.isoformat() for item in forecast)


def test_settings_default_and_update(household_headers: dict[str, str]) -> None:
    assert (
        client.get("/v1/contracts/settings", headers=household_headers).json()[
            "auto_end_after_missed"
        ]
        == "3"
    )
    updated = client.put(
        "/v1/contracts/settings",
        json={"auto_end_after_missed": "off"},
        headers=household_headers,
    )
    assert updated.status_code == 200
    assert (
        client.get("/v1/contracts/settings", headers=household_headers).json()[
            "auto_end_after_missed"
        ]
        == "off"
    )


def test_the_three_push_switches_are_readable_and_writable(
    household_headers: dict[str, str],
) -> None:
    """Regressionstest: die Spalten aus Migration 0015 waren nur lesbar.

    Der Worker wertet `notify_price_change`, `notify_payment_missing` und
    `notify_term_ending` einzeln im Sendefilter aus
    (worker/notifications/contract_alerts.py). Ohne Schreibpfad standen sie
    dauerhaft auf `true`, und wem die taeglichen Preisaenderungs-Hinweise zu
    viel sind, dem blieb nur die Berechtigung des Betriebssystems - also genau
    das "alles oder nichts", das die Schalter beheben sollen.
    """
    defaults = client.get("/v1/contracts/settings", headers=household_headers).json()
    assert defaults["notify_price_change"] is True
    assert defaults["notify_payment_missing"] is True
    assert defaults["notify_term_ending"] is True

    updated = client.put(
        "/v1/contracts/settings",
        json={
            "auto_end_after_missed": "3",
            "notify_price_change": False,
            "notify_payment_missing": False,
            "notify_term_ending": True,
        },
        headers=household_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["notify_price_change"] is False

    stored = client.get("/v1/contracts/settings", headers=household_headers).json()
    assert stored["notify_price_change"] is False
    assert stored["notify_payment_missing"] is False
    assert stored["notify_term_ending"] is True
    assert stored["auto_end_after_missed"] == "3"


def test_duplicate_check_finds_a_similar_contract(household_headers: dict[str, str]) -> None:
    """Der Fall MaxEnergy: der manuell angelegte Vertrag soll den bereits
    erkannten finden, bevor ein zweiter Eintrag entsteht."""
    existing = _create(household_headers, name="MaxEnergy GmbH", amount_minor=12300)
    candidates = client.post(
        "/v1/contracts/duplicate-check",
        json={"name": "MaxEnergy Strom", "amount_minor": 12300, "frequency": "monthly"},
        headers=household_headers,
    ).json()
    assert [item["id"] for item in candidates] == [existing["id"]]
    assert candidates[0]["reason"] in {"similar_name", "similar_amount"}


def test_ended_contracts_are_hidden_unless_requested(
    household_headers: dict[str, str],
) -> None:
    created = _create(household_headers)
    client.post(
        f"/v1/contracts/{created['id']}/status",
        json={"status": "ended"},
        headers=household_headers,
    )
    assert client.get("/v1/contracts", headers=household_headers).json() == []
    with_ended = client.get(
        "/v1/contracts", params={"include_ended": True}, headers=household_headers
    ).json()
    assert [item["id"] for item in with_ended] == [created["id"]]


# ---------------------------------------------------------------------------
# Kompatibilitaetsschicht
# ---------------------------------------------------------------------------


def test_legacy_recurring_endpoint_reads_contracts(household_headers: dict[str, str]) -> None:
    created = _create(household_headers, name="Wohnungsmiete", amount_minor=115000)
    legacy = client.get("/v1/recurring", headers=household_headers).json()
    assert len(legacy) == 1
    # Die alte App kennt nur `note` als Anzeigetext und nur vorzeichenbehaftete
    # Betraege.
    assert legacy[0]["note"] == "Wohnungsmiete"
    assert legacy[0]["expected_amount_minor"] == -115000
    assert legacy[0]["id"] == created["id"]


def test_legacy_recurring_post_creates_a_contract(household_headers: dict[str, str]) -> None:
    response = client.post(
        "/v1/recurring",
        json={
            "expected_amount_minor": -4500,
            "frequency": "monthly",
            "next_due_date": date.today().isoformat(),
            "note": "Handyvertrag",
        },
        headers=household_headers,
    )
    assert response.status_code == 201
    contracts = client.get("/v1/contracts", headers=household_headers).json()
    assert len(contracts) == 1
    assert contracts[0]["name"] == "Handyvertrag"
    assert contracts[0]["amount_minor"] == 4500
    assert contracts[0]["direction"] == "expense"


def test_legacy_endpoints_do_not_overlap(household_headers: dict[str, str]) -> None:
    """Sonst zaehlt die alte App denselben Vertrag zweimal in ihre Monatssumme."""
    _create(household_headers, name="Abo", contract_type="subscription")
    _create(household_headers, name="Miete", contract_type="rent")

    recurring = client.get("/v1/recurring", headers=household_headers).json()
    subscriptions = client.get("/v1/subscriptions", headers=household_headers).json()
    assert [item["note"] for item in recurring] == ["Miete"]
    assert [item["provider"] for item in subscriptions] == ["Abo"]
