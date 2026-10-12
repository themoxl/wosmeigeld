"""Die drei Vertrags-Push-Typen aus Phase 4 gegen ein echtes lokales
Supabase-Postgres.

Der Schwerpunkt liegt auf dem Dedup: eine Benachrichtigung, die jede Nacht
erneut kommt, ist schlimmer als gar keine - der Nutzer schaltet dann den
gesamten Kanal ab. Geprueft werden deshalb vor allem die Faelle, in denen
NICHTS gesendet werden darf (bereits benachrichtigt, bestaetigt, zu alt,
abgeschaltet, noch nicht bestaetigt genug), sowie die Markierung der
ueberholten Vorgaengerereignisse.

Der Versand selbst ist ersetzt: die Expo-API ist nicht Gegenstand dieses Tests
(und waere im CI ein Netzwerkaufruf).
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from worker.db import get_session_factory
from worker.notifications import contract_alerts
from worker.notifications.contract_alerts import (
    ContractAlert,
    build_push_messages,
    check_contract_alerts,
    select_pending_alerts,
)
from worker.notifications.push import PushMessage, PushTicket

TODAY = date(2026, 6, 15)


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


async def _create_household_with_device(*, with_token: bool = True) -> tuple[str, str]:
    """Haushalt mit einem aktiven Mitglied und (optional) einem Push-Geraet."""
    household_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text("insert into auth.users (id, email) values (:id, :email)"),
            {"id": user_id, "email": f"alerts-{user_id}@example.test"},
        )
        await session.execute(
            text("insert into households (id, name) values (:id, 'Alarm-Test-Haushalt')"),
            {"id": household_id},
        )
        await session.execute(
            text(
                "insert into household_members (household_id, user_id, role, status) "
                "values (:household_id, :user_id, 'owner', 'active')"
            ),
            {"household_id": household_id, "user_id": user_id},
        )
        if with_token:
            await session.execute(
                text(
                    "insert into device_push_tokens (user_id, expo_token, platform) "
                    "values (:user_id, :token, 'ios')"
                ),
                {"user_id": user_id, "token": f"ExponentPushToken[{user_id}]"},
            )
    return household_id, user_id


async def _add_device(user_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into device_push_tokens (user_id, expo_token, platform) "
                "values (:user_id, :token, 'android')"
            ),
            {"user_id": user_id, "token": f"ExponentPushToken[{uuid.uuid4()}]"},
        )


async def _create_contract(household_id: str, **overrides: Any) -> str:
    contract_id = str(uuid.uuid4())
    values: dict[str, Any] = {
        "id": contract_id,
        "household_id": household_id,
        "name": "Testvertrag Streamdienst",
        "amount_minor": 1299,
        "next_due_date": TODAY,
        "status": "active",
        "missed_periods": 0,
        "cancelled_on": None,
        "ends_on": None,
    }
    values.update(overrides)
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into contracts (id, household_id, name, amount_minor, next_due_date, "
                "status, missed_periods, cancelled_on, ends_on) "
                "values (:id, :household_id, :name, :amount_minor, :next_due_date, "
                ":status, :missed_periods, :cancelled_on, :ends_on)"
            ),
            values,
        )
    return contract_id


async def _add_event(
    household_id: str,
    contract_id: str,
    kind: str,
    *,
    occurred_on: date,
    payload: str = "{}",
    acknowledged: bool = False,
    notified: bool = False,
) -> str:
    event_id = str(uuid.uuid4())
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into contract_events (id, household_id, contract_id, kind, "
                "occurred_on, payload, acknowledged_at, notified_at) "
                "values (:id, :household_id, :contract_id, :kind, :occurred_on, "
                "cast(:payload as jsonb), "
                "case when :acknowledged then now() else null end, "
                "case when :notified then now() else null end)"
            ),
            {
                "id": event_id,
                "household_id": household_id,
                "contract_id": contract_id,
                "kind": kind,
                "occurred_on": occurred_on,
                "payload": payload,
                "acknowledged": acknowledged,
                "notified": notified,
            },
        )
    return event_id


async def _add_missing_occurrence(
    household_id: str, contract_id: str, due_date: date
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into contract_occurrences "
                "(household_id, contract_id, due_date, status) "
                "values (:household_id, :contract_id, :due_date, 'missing')"
            ),
            {
                "household_id": household_id,
                "contract_id": contract_id,
                "due_date": due_date,
            },
        )


async def _set_notify_flags(household_id: str, **flags: bool) -> None:
    columns = ", ".join(f"{name} = :{name}" for name in flags)
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into contract_settings (household_id) values (:household_id) "
                "on conflict (household_id) do nothing"
            ),
            {"household_id": household_id},
        )
        await session.execute(
            text(f"update contract_settings set {columns} where household_id = :household_id"),
            {"household_id": household_id, **flags},
        )


async def _pending_for(household_id: str, *, today: date = TODAY) -> list[ContractAlert]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        alerts = await select_pending_alerts(session, today=today)
    return [alert for alert in alerts if alert.household_id == household_id]


async def _event_ids(household_id: str, *, notified: bool) -> set[str]:
    condition = "notified_at is not null" if notified else "notified_at is null"
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            text(
                "select id from contract_events "
                f"where household_id = :household_id and {condition}"
            ),
            {"household_id": household_id},
        )
        return {str(row[0]) for row in result}


def _pushes_for(messages: list[PushMessage], contract_id: str) -> list[PushMessage]:
    """Der Lauf arbeitet haushaltuebergreifend; die Datenbank traegt die
    Vertraege der uebrigen Tests. Bewertet wird deshalb nur der eigene."""
    return [message for message in messages if message["data"]["contract_id"] == contract_id]


@pytest.fixture
def captured_pushes(monkeypatch: pytest.MonkeyPatch) -> list[PushMessage]:
    """Faengt den Expo-Versand ab und liefert die gesendeten Nachrichten."""
    sent: list[PushMessage] = []

    async def _fake_send(messages: list[PushMessage]) -> list[PushTicket]:
        sent.extend(messages)
        return [PushTicket(status="ok", id=str(uuid.uuid4())) for _ in messages]

    monkeypatch.setattr(contract_alerts, "send_expo_push_batch", _fake_send)
    return sent


# ---------------------------------------------------------------------------
# Nachrichtentexte
# ---------------------------------------------------------------------------


def test_push_texts_name_the_contract_but_never_an_amount() -> None:
    """Architekturregel (Architekturplan Abschnitt 13): keine Betraege im
    Push-Text. Ein Sperrbildschirm ist kein vertraulicher Ort."""
    household_id = str(uuid.uuid4())
    alerts = [
        ContractAlert(
            event_id=str(uuid.uuid4()),
            household_id=household_id,
            contract_id="11111111-1111-1111-1111-111111111111",
            kind=kind,
            label="Netflix",
        )
        for kind in ("price_change", "payment_missing", "term_ending")
    ]

    # Ohne `delta_minor` bleibt der Preistext richtungsneutral; die beiden
    # gerichteten Varianten deckt
    # test_a_price_cut_is_never_announced_as_an_increase ab.
    messages = build_push_messages(alerts, {household_id: ["ExponentPushToken[abc]"]})

    assert [message["data"]["type"] for message in messages] == [
        "contract_price_change",
        "contract_payment_missing",
        "contract_term_ending",
    ]
    for message in messages:
        assert "Netflix" in message["body"]
        # Kein Betrag, kein Prozentsatz, kein Fristdatum - keine Ziffer.
        assert not any(char.isdigit() for char in message["title"] + message["body"])
        assert message["data"]["contract_id"] == "11111111-1111-1111-1111-111111111111"
        assert message["data"]["event_id"]


def test_a_price_cut_is_never_announced_as_an_increase() -> None:
    """Der Text folgt dem Vorzeichen von `delta_minor`.

    Frueher meldete jede Preisaenderung "kostet ab sofort mehr" - auch die
    Senkungen, von denen der Bestand messbar viele traegt. Eine Nachricht, die
    dem Nutzer eine Erhoehung behauptet, wo sein Anbieter den Preis gesenkt
    hat, ist keine Ungenauigkeit, sondern eine falsche Aussage ueber sein Geld.
    """
    household_id = str(uuid.uuid4())

    def _body(delta_minor: int | None) -> str:
        alert = ContractAlert(
            event_id=str(uuid.uuid4()),
            household_id=household_id,
            contract_id=str(uuid.uuid4()),
            kind="price_change",
            label="Musterfunk Festnetz",
            delta_minor=delta_minor,
        )
        [message] = build_push_messages([alert], {household_id: ["ExponentPushToken[a]"]})
        return str(message["body"])

    assert _body(-1000) == "Musterfunk Festnetz kostet ab sofort weniger."
    assert _body(1000) == "Musterfunk Festnetz kostet ab sofort mehr."
    # Unbekannte oder nicht vorhandene Richtung: keine geratene Behauptung.
    assert _body(None) == "Musterfunk Festnetz hat einen neuen Preis."
    assert _body(0) == "Musterfunk Festnetz hat einen neuen Preis."

    # Die Architekturregel gilt weiter: kein Betrag im Text.
    for delta in (-1000, 1000, None, 0):
        assert not any(char.isdigit() for char in _body(delta))


def test_every_device_of_the_household_gets_its_own_message() -> None:
    household_id = str(uuid.uuid4())
    alert = ContractAlert(
        event_id=str(uuid.uuid4()),
        household_id=household_id,
        contract_id=str(uuid.uuid4()),
        kind="term_ending",
        label="Fitnessstudio",
    )

    messages = build_push_messages(
        [alert], {household_id: ["ExponentPushToken[a]", "ExponentPushToken[b]"]}
    )

    assert [message["to"] for message in messages] == [
        "ExponentPushToken[a]",
        "ExponentPushToken[b]",
    ]


def test_an_alert_without_a_registered_device_produces_nothing() -> None:
    alert = ContractAlert(
        event_id=str(uuid.uuid4()),
        household_id=str(uuid.uuid4()),
        contract_id=str(uuid.uuid4()),
        kind="price_change",
        label="Stadtwerke",
    )

    assert build_push_messages([alert], {}) == []


# ---------------------------------------------------------------------------
# Auswahl
# ---------------------------------------------------------------------------


async def test_a_fresh_price_change_is_selected_once_per_contract() -> None:
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id, name="Stadtwerke Strom")
    await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY)

    alerts = await _pending_for(household_id)

    assert [(alert.kind, alert.label) for alert in alerts] == [
        ("price_change", "Stadtwerke Strom")
    ]


async def test_the_direction_of_a_price_change_survives_the_query(
    captured_pushes: list[PushMessage],
) -> None:
    """Die Auswahl muss das Vorzeichen mitbringen, sonst nuetzt der richtige
    Text nichts. Eine unlesbare Nutzlast darf den Lauf nicht abbrechen - sie
    fuehrt nur zum richtungsneutralen Satz."""
    household_id, _ = await _create_household_with_device()
    lowered = await _create_contract(household_id, name="Musterfunk Festnetz")
    await _add_event(
        household_id,
        lowered,
        "price_change",
        occurred_on=TODAY,
        payload='{"old_minor": 4995, "new_minor": 3995, "delta_minor": -1000, "pct": -20.02}',
    )
    unreadable = await _create_contract(household_id, name="Alte Nutzlast")
    await _add_event(
        household_id,
        unreadable,
        "price_change",
        occurred_on=TODAY,
        payload='{"delta_minor": "unbekannt"}',
    )

    alerts = await _pending_for(household_id)
    deltas = {alert.contract_id: alert.delta_minor for alert in alerts}
    assert deltas == {lowered: -1000, unreadable: None}

    await check_contract_alerts(TODAY)

    [lowered_message] = _pushes_for(captured_pushes, lowered)
    assert lowered_message["body"] == "Musterfunk Festnetz kostet ab sofort weniger."
    [unreadable_message] = _pushes_for(captured_pushes, unreadable)
    assert unreadable_message["body"] == "Alte Nutzlast hat einen neuen Preis."


async def test_only_the_newest_event_per_contract_and_kind_is_sent(
    captured_pushes: list[PushMessage],
) -> None:
    """Ein Nachholabgleich schreibt mehrere gleichartige Ereignisse. Der Nutzer
    bekommt eine Nachricht, nicht drei - und die ueberholten Vorgaenger duerfen
    nicht bis zur naechsten Nacht offen liegen bleiben."""
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id)
    older = await _add_event(
        household_id, contract_id, "price_change", occurred_on=TODAY - timedelta(days=10)
    )
    middle = await _add_event(
        household_id, contract_id, "price_change", occurred_on=TODAY - timedelta(days=5)
    )
    newest = await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY)

    alerts = await _pending_for(household_id)
    assert [alert.event_id for alert in alerts] == [newest]

    await check_contract_alerts(TODAY)

    assert len(_pushes_for(captured_pushes, contract_id)) == 1
    # Auch die ueberholten Vorgaenger sind erledigt.
    assert await _event_ids(household_id, notified=True) == {older, middle, newest}


async def test_payment_missing_waits_for_the_second_period() -> None:
    """Eine einzelne ausgebliebene Lastschrift kann eine Verzoegerung sein. Der
    Motor schreibt das Ereignis schon beim ersten Fehltreffer; gesendet wird es
    erst, wenn der Zaehler die Inbox-Schwelle erreicht."""
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id, missed_periods=1)
    await _add_event(
        household_id, contract_id, "payment_missing", occurred_on=TODAY - timedelta(days=40)
    )
    await _add_missing_occurrence(household_id, contract_id, TODAY - timedelta(days=40))

    assert await _pending_for(household_id) == []

    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text("update contracts set missed_periods = 2 where id = :id"),
            {"id": contract_id},
        )
    # Zweite Fehlperiode: das Ereignis selbst ist laengst aelter als das
    # Altersfenster, die juengste ausgebliebene Faelligkeit aber nicht.
    await _add_missing_occurrence(household_id, contract_id, TODAY - timedelta(days=10))

    alerts = await _pending_for(household_id)
    assert [alert.kind for alert in alerts] == ["payment_missing"]


async def test_a_long_silent_contract_does_not_ring_again_years_later() -> None:
    """Ohne Altersgrenze meldete der erste Lauf nach dem Deployment die gesamte
    seit Phase 2 geschriebene Ereignishistorie."""
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id, missed_periods=5)
    await _add_event(
        household_id, contract_id, "payment_missing", occurred_on=TODAY - timedelta(days=800)
    )
    await _add_missing_occurrence(household_id, contract_id, TODAY - timedelta(days=800))

    assert await _pending_for(household_id) == []


async def test_stale_price_change_and_term_ending_events_stay_silent() -> None:
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id)
    await _add_event(
        household_id, contract_id, "price_change", occurred_on=TODAY - timedelta(days=15)
    )
    await _add_event(
        household_id,
        contract_id,
        "term_ending",
        occurred_on=TODAY - timedelta(days=15),
        payload='{"stage": "30", "deadline": "2026-07-01"}',
    )

    assert await _pending_for(household_id) == []


async def test_acknowledged_events_are_not_pushed() -> None:
    """Wer den Hinweis in der App bereits bestaetigt hat, braucht ihn abends
    nicht noch einmal auf dem Sperrbildschirm."""
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id)
    await _add_event(
        household_id, contract_id, "price_change", occurred_on=TODAY, acknowledged=True
    )

    assert await _pending_for(household_id) == []


async def test_already_notified_events_are_not_pushed_again() -> None:
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id)
    await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY, notified=True)

    assert await _pending_for(household_id) == []


async def test_each_notify_switch_silences_only_its_own_type() -> None:
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id, missed_periods=2)
    await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY)
    await _add_event(household_id, contract_id, "payment_missing", occurred_on=TODAY)
    await _add_missing_occurrence(household_id, contract_id, TODAY - timedelta(days=2))
    await _add_event(
        household_id,
        contract_id,
        "term_ending",
        occurred_on=TODAY,
        payload='{"stage": "30", "deadline": "2026-07-15"}',
    )

    assert {alert.kind for alert in await _pending_for(household_id)} == {
        "price_change",
        "payment_missing",
        "term_ending",
    }

    await _set_notify_flags(household_id, notify_price_change=False)
    assert {alert.kind for alert in await _pending_for(household_id)} == {
        "payment_missing",
        "term_ending",
    }

    await _set_notify_flags(household_id, notify_term_ending=False)
    assert {alert.kind for alert in await _pending_for(household_id)} == {"payment_missing"}

    await _set_notify_flags(household_id, notify_payment_missing=False)
    assert await _pending_for(household_id) == []


async def test_a_cancelled_contract_still_reports_a_price_change_but_no_deadline() -> None:
    """Gekuendigt ist nicht beendet: bis zum Enddatum kostet der Vertrag weiter
    Geld, eine Preiserhoehung trifft den Nutzer also noch. Eine
    Kuendigungsfrist dagegen ist fuer ihn erledigt."""
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(
        household_id,
        status="cancelled",
        cancelled_on=TODAY - timedelta(days=5),
        ends_on=TODAY + timedelta(days=60),
    )
    await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY)
    await _add_event(
        household_id,
        contract_id,
        "term_ending",
        occurred_on=TODAY,
        payload='{"stage": "30", "deadline": "2026-07-15"}',
    )

    assert [alert.kind for alert in await _pending_for(household_id)] == ["price_change"]


async def test_an_ended_contract_is_silent() -> None:
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id, status="ended", missed_periods=3)
    await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY)
    await _add_event(household_id, contract_id, "payment_missing", occurred_on=TODAY)
    await _add_missing_occurrence(household_id, contract_id, TODAY)

    assert await _pending_for(household_id) == []


async def test_a_household_without_a_device_keeps_its_events_open(
    captured_pushes: list[PushMessage],
) -> None:
    """Sonst markierte der Lauf Ereignisse als benachrichtigt, ohne dass je
    etwas zugestellt wurde - der Hinweis waere fuer immer verloren."""
    household_id, _ = await _create_household_with_device(with_token=False)
    contract_id = await _create_contract(household_id)
    event_id = await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY)

    assert await _pending_for(household_id) == []

    await check_contract_alerts(TODAY)

    assert _pushes_for(captured_pushes, contract_id) == []
    assert await _event_ids(household_id, notified=False) == {event_id}


# ---------------------------------------------------------------------------
# Gesamtlauf
# ---------------------------------------------------------------------------


async def test_the_run_sends_one_message_per_device_and_marks_the_events(
    captured_pushes: list[PushMessage],
) -> None:
    household_id, user_id = await _create_household_with_device()
    await _add_device(user_id)
    contract_id = await _create_contract(household_id, name="Allianz Hausrat")
    await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY)

    await check_contract_alerts(TODAY)

    mine = _pushes_for(captured_pushes, contract_id)
    assert len(mine) == 2
    assert {message["title"] for message in mine} == {"Preis geaendert"}
    assert "Allianz Hausrat" in mine[0]["body"]
    assert await _event_ids(household_id, notified=False) == set()


async def test_a_second_run_on_the_same_day_stays_quiet(
    captured_pushes: list[PushMessage],
) -> None:
    """Der eigentliche Zweck von notified_at: derselbe Hinweis darf nicht jede
    Nacht erneut zugestellt werden."""
    household_id, _ = await _create_household_with_device()
    contract_id = await _create_contract(household_id)
    await _add_event(household_id, contract_id, "price_change", occurred_on=TODAY)

    await check_contract_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, contract_id)) == 1

    await check_contract_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, contract_id)) == 1
