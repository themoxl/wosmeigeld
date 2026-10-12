"""Die fuenf Vorschau-Push-Typen aus Stufe 5d gegen ein echtes lokales
Supabase-Postgres.

Der Schwerpunkt liegt auf der einen Regel, die diesen Lauf traegt: **Push nur
bei Zustandswechsel.** Die Vorschau wird jede Nacht neu gerechnet und aendert
sich taeglich um Kleinigkeiten - ein Lauf, der die Lage meldet statt ihrer
Veraenderung, waere binnen einer Woche abgeschaltet. Geprueft wird deshalb zu
jedem Typ dasselbe Dreieck:

1. die Verschlechterung meldet sich,
2. der naechste Lauf ohne Aenderung schweigt,
3. nach einer Erholung meldet sich dieselbe Lage erneut.

Dazu die Faelle, in denen ausdruecklich NICHTS herausgehen darf: der erste
Lauf eines Haushalts (ohne Vorstand gibt es keinen Wechsel), ein Haushalt ohne
Geraet, abgeschaltete Schalter, und die Kontolage bei unbelastbarer
Datengrundlage.

Der Versand selbst ist ersetzt - die Expo-API ist nicht Gegenstand dieses
Tests (und waere im CI ein Netzwerkaufruf).
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from worker.db import get_session_factory
from worker.notifications import forecast_alerts
from worker.notifications.forecast_alerts import (
    ForecastAlert,
    ForecastAlertBatch,
    ForecastState,
    _newly_critical_months,
    build_push_messages,
    check_forecast_alerts,
)
from worker.notifications.push import PushMessage, PushTicket

TODAY = date(2026, 6, 15)

# Ein Haushalt, dessen Vorschau ohne Zutun gesund bleibt: 5.000 EUR Startgeld,
# 3.000 EUR Einnahmen und 4.000 EUR Fixkosten je Monat. Der Verlauf sinkt um
# 1.000 EUR im Monat und liegt nach 24 Monaten immer noch deutlich ueber null.
_GESUND_MINOR = 5_000_000
# Dasselbe Haushaltsmuster mit 5.000 EUR weniger Startgeld - der Verlauf
# kippt damit innerhalb des Horizonts ins Minus.
_ENGPASS_MINOR = 500_000

_EINNAHME_MINOR = 300_000
_FIXKOSTEN_MINOR = 400_000


@dataclass
class Haushalt:
    """Was ein Test zum Steuern der Vorschau in der Hand haelt."""

    household_id: str
    user_id: str
    token: str
    account_id: str
    income_contract_id: str


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


async def _create_household(
    *, with_token: bool = True, opening_balance_minor: int = _GESUND_MINOR
) -> Haushalt:
    """Haushalt mit Geraet, Girokonto, je einem Einnahme- und Fixkostenvertrag
    und einer frischen Buchung.

    Die frische Buchung ist kein Beiwerk: ohne sie ist der juengste Umsatz
    aelter als 14 Tage, die Datenqualitaet faellt auf 'insufficient' und die
    Vorschau schweigt zur Kontolage (Leitsatz 6).
    """
    household_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    token = f"ExponentPushToken[{user_id}]"
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text("insert into auth.users (id, email) values (:id, :email)"),
            {"id": user_id, "email": f"forecast-{user_id}@example.test"},
        )
        await session.execute(
            text("insert into households (id, name) values (:id, 'Vorschau-Test-Haushalt')"),
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
                {"user_id": user_id, "token": token},
            )
        await session.execute(
            text(
                "insert into accounts (id, household_id, name, account_type, "
                "opening_balance_minor) "
                "values (:id, :household_id, 'Girokonto', 'checking', :opening)"
            ),
            {
                "id": account_id,
                "household_id": household_id,
                "opening": opening_balance_minor,
            },
        )
        await session.execute(
            text(
                "insert into transactions (household_id, account_id, booked_date, "
                "amount_minor, currency, source) "
                "values (:household_id, :account_id, :booked_date, -1000, 'EUR', 'manual')"
            ),
            {
                "household_id": household_id,
                "account_id": account_id,
                "booked_date": TODAY - timedelta(days=2),
            },
        )
    income_contract_id = await _create_contract(
        household_id,
        name="Gehalt",
        direction="income",
        amount_minor=_EINNAHME_MINOR,
        next_due_date=date(2026, 7, 1),
    )
    await _create_contract(
        household_id,
        name="Miete",
        direction="expense",
        amount_minor=_FIXKOSTEN_MINOR,
        next_due_date=date(2026, 6, 20),
    )
    return Haushalt(
        household_id=household_id,
        user_id=user_id,
        token=token,
        account_id=account_id,
        income_contract_id=income_contract_id,
    )


async def _create_contract(household_id: str, **overrides: Any) -> str:
    contract_id = str(uuid.uuid4())
    values: dict[str, Any] = {
        "id": contract_id,
        "household_id": household_id,
        "name": "Testvertrag",
        "direction": "expense",
        "amount_minor": 10_000,
        "frequency": "monthly",
        "next_due_date": TODAY,
        "status": "active",
        "missed_periods": 0,
    }
    values.update(overrides)
    values["due_day"] = values["next_due_date"].day
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into contracts (id, household_id, name, direction, amount_minor, "
                "frequency, next_due_date, due_day, status, missed_periods) "
                "values (:id, :household_id, :name, :direction, :amount_minor, :frequency, "
                ":next_due_date, :due_day, :status, :missed_periods)"
            ),
            values,
        )
    return contract_id


async def _add_device(user_id: str) -> str:
    token = f"ExponentPushToken[{uuid.uuid4()}]"
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into device_push_tokens (user_id, expo_token, platform) "
                "values (:user_id, :token, 'android')"
            ),
            {"user_id": user_id, "token": token},
        )
    return token


async def _set_opening_balance(account_id: str, minor: int) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text("update accounts set opening_balance_minor = :minor where id = :id"),
            {"minor": minor, "id": account_id},
        )


async def _set_settings(household_id: str, **values: Any) -> None:
    """Legt forecast_settings an oder aendert einzelne Felder."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into forecast_settings (household_id) values (:household_id) "
                "on conflict (household_id) do nothing"
            ),
            {"household_id": household_id},
        )
        if values:
            columns = ", ".join(f"{name} = :{name}" for name in values)
            await session.execute(
                text(
                    f"update forecast_settings set {columns} where household_id = :household_id"
                ),
                {"household_id": household_id, **values},
            )


async def _set_missed_periods(contract_id: str, missed: int) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text("update contracts set missed_periods = :missed where id = :id"),
            {"missed": missed, "id": contract_id},
        )


async def _age_last_booking(household_id: str, days: int) -> None:
    """Schiebt die juengste Buchung nach hinten - so altert die Datengrundlage."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "update transactions set booked_date = :booked_date "
                "where household_id = :household_id"
            ),
            {
                "booked_date": TODAY - timedelta(days=days),
                "household_id": household_id,
            },
        )


async def _state(household_id: str) -> dict[str, Any] | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "select first_negative_on, first_below_buffer_on, quality_level, "
                    "critical_months, missing_income_contract_ids, notified_at "
                    "from forecast_alert_state where household_id = :household_id"
                ),
                {"household_id": household_id},
            )
        ).mappings().first()
    return dict(row) if row is not None else None


def _pushes_for(messages: list[PushMessage], token: str) -> list[PushMessage]:
    """Der Lauf arbeitet haushaltuebergreifend; die Nutzlast traegt bewusst
    keine Haushalts-ID (Architekturplan Abschnitt 13). Zugeordnet wird deshalb
    ueber den Geraetetoken."""
    return [message for message in messages if message["to"] == token]


def _types(messages: list[PushMessage]) -> list[str]:
    return [str(message["data"]["type"]) for message in messages]


@pytest.fixture
def captured_pushes(monkeypatch: pytest.MonkeyPatch) -> list[PushMessage]:
    """Faengt den Expo-Versand ab und liefert die gesendeten Nachrichten."""
    sent: list[PushMessage] = []

    async def _fake_send(messages: list[PushMessage]) -> list[PushTicket]:
        sent.extend(messages)
        return [PushTicket(status="ok", id=str(uuid.uuid4())) for _ in messages]

    monkeypatch.setattr(forecast_alerts, "send_expo_push_batch", _fake_send)
    return sent


# ---------------------------------------------------------------------------
# Nachrichtentexte
# ---------------------------------------------------------------------------


def _batch(*kinds: str, label: str | None = None) -> ForecastAlertBatch:
    household_id = str(uuid.uuid4())
    return ForecastAlertBatch(
        household_id=household_id,
        state=ForecastState(),
        alerts=tuple(
            ForecastAlert(
                household_id=household_id,
                kind=kind,
                forecast_on=date(2026, 9, 23) if kind != "income_missing" else None,
                label=label,
            )
            for kind in kinds
        ),
    )


def test_push_texts_name_the_situation_but_never_a_number() -> None:
    """Architekturregel (Architekturplan Abschnitt 13): keine Betraege,
    Prozentsaetze oder Fristdaten im Push-Text. Ein Sperrbildschirm ist kein
    vertraulicher Ort - der Text nennt die Lage, die Zahl steht in der
    Vorschau."""
    batch = _batch(
        "shortfall",
        "below_buffer",
        "month_critical",
        "income_missing",
        "data_stale",
        label="Gehalt",
    )

    messages = build_push_messages([batch], {batch.household_id: ["ExponentPushToken[abc]"]})

    assert _types(messages) == [
        "forecast_shortfall",
        "forecast_below_buffer",
        "forecast_month_critical",
        "forecast_income_missing",
        "forecast_data_stale",
    ]
    for message in messages:
        assert not any(char.isdigit() for char in message["title"] + message["body"])


def test_the_payload_carries_a_type_and_at_most_a_date() -> None:
    """`data` ist der gesamte Nutzlastumfang: `type` fuer das Routing (alle
    fuenf Typen fuehren auf /mehr/vorschau) und hoechstens `forecast_on`. Ein
    Datum als ISO-String ist zulaessig, ein Betrag waere es nicht."""
    batch = _batch("shortfall", "income_missing", label="Gehalt")

    messages = build_push_messages([batch], {batch.household_id: ["ExponentPushToken[abc]"]})

    assert messages[0]["data"] == {
        "type": "forecast_shortfall",
        "forecast_on": "2026-09-23",
    }
    # Ohne Datum bleibt der Schluessel ganz weg statt als null durchzugehen.
    assert messages[1]["data"] == {"type": "forecast_income_missing"}
    assert messages[1]["body"] == "Gehalt ist diesmal nicht eingegangen."


def test_every_device_of_the_household_gets_its_own_message() -> None:
    batch = _batch("shortfall")

    messages = build_push_messages(
        [batch], {batch.household_id: ["ExponentPushToken[a]", "ExponentPushToken[b]"]}
    )

    assert [message["to"] for message in messages] == [
        "ExponentPushToken[a]",
        "ExponentPushToken[b]",
    ]


def test_a_batch_without_alerts_produces_nothing() -> None:
    """Der Regelfall: der Lauf schreibt den Zustand fort und sendet nichts."""
    household_id = str(uuid.uuid4())
    batch = ForecastAlertBatch(household_id=household_id, state=ForecastState())

    assert build_push_messages([batch], {household_id: ["ExponentPushToken[a]"]}) == []


# ---------------------------------------------------------------------------
# Rollender Horizont
# ---------------------------------------------------------------------------


def test_a_month_that_only_just_entered_the_horizon_is_not_newly_critical() -> None:
    """Jeden Monat waechst hinten ein Monat in die Vorschau hinein, den es
    beim letzten Lauf noch nicht gab. Ein solcher Monat war nie
    'unauffaellig' - ohne diese Grenze schickte der Lauf einmal im Monat
    verlaesslich eine Nachricht ueber den jeweils hintersten Monat."""
    previous = ForecastState(critical_months=("2027-01",))

    neu = _newly_critical_months(
        ("2026-12", "2027-01", "2028-06", "2028-07"),
        previous,
        date(2026, 6, 1),
        horizon_months=24,
    )

    # Der letzte Lauf am 01.06.2026 reichte bis 2028-06. Alles bis dorthin war
    # beurteilt und kann neu kritisch werden; 2027-01 war es bereits, 2028-07
    # lag ausserhalb und ist deshalb kein Wechsel, sondern ein Zuwachs.
    assert neu == ("2026-12", "2028-06")


# ---------------------------------------------------------------------------
# Engpass
# ---------------------------------------------------------------------------


async def test_the_first_run_only_records_the_baseline_and_stays_quiet(
    captured_pushes: list[PushMessage],
) -> None:
    """Ohne vorherigen Stand ist "war es beim letzten Lauf nicht" nicht
    beantwortbar. Die Alternative waere ein Schwall ueber den gesamten Bestand
    am Tag des Deployments."""
    haushalt = await _create_household(opening_balance_minor=_ENGPASS_MINOR)

    await check_forecast_alerts(TODAY)

    assert _pushes_for(captured_pushes, haushalt.token) == []
    zustand = await _state(haushalt.household_id)
    assert zustand is not None
    assert zustand["first_negative_on"] is not None
    assert zustand["notified_at"] is None


async def test_a_new_shortfall_is_reported_once_and_then_stays_quiet(
    captured_pushes: list[PushMessage],
) -> None:
    haushalt = await _create_household()
    await check_forecast_alerts(TODAY)
    assert _pushes_for(captured_pushes, haushalt.token) == []

    await _set_opening_balance(haushalt.account_id, _ENGPASS_MINOR)
    await check_forecast_alerts(TODAY)

    meine = _pushes_for(captured_pushes, haushalt.token)
    assert _types(meine) == ["forecast_shortfall"]
    assert meine[0]["title"] == "Engpass in Sicht"
    zustand = await _state(haushalt.household_id)
    assert zustand is not None
    assert meine[0]["data"]["forecast_on"] == zustand["first_negative_on"].isoformat()
    assert zustand["notified_at"] is not None

    # Zweiter Lauf ohne Aenderung: derselbe Engpass ist keine Neuigkeit.
    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 1


async def test_after_a_recovery_the_same_shortfall_is_reported_again(
    captured_pushes: list[PushMessage],
) -> None:
    """Der Grund, warum der Zustand auch ohne Versand zurueckgeschrieben wird:
    ohne Ruecksetzung meldete sich dieselbe Lage nie wieder."""
    haushalt = await _create_household()
    await check_forecast_alerts(TODAY)
    await _set_opening_balance(haushalt.account_id, _ENGPASS_MINOR)
    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 1

    # Erholung: keine Nachricht, aber der gemerkte Stand faellt zurueck.
    await _set_opening_balance(haushalt.account_id, _GESUND_MINOR)
    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 1
    zustand = await _state(haushalt.household_id)
    assert zustand is not None
    assert zustand["first_negative_on"] is None

    # Und derselbe Engpass meldet sich erneut.
    await _set_opening_balance(haushalt.account_id, _ENGPASS_MINOR)
    await check_forecast_alerts(TODAY)
    assert _types(_pushes_for(captured_pushes, haushalt.token)) == [
        "forecast_shortfall",
        "forecast_shortfall",
    ]


# ---------------------------------------------------------------------------
# Sicherheitspuffer und kritischer Monat
# ---------------------------------------------------------------------------


async def test_falling_below_the_buffer_is_its_own_message(
    captured_pushes: list[PushMessage],
) -> None:
    haushalt = await _create_household()
    await _set_settings(haushalt.household_id, safety_buffer_minor=0)
    await check_forecast_alerts(TODAY)
    assert _pushes_for(captured_pushes, haushalt.token) == []

    # 30.000 EUR Puffer: der Verlauf faellt darunter, bleibt aber positiv.
    await _set_settings(haushalt.household_id, safety_buffer_minor=3_000_000)
    await check_forecast_alerts(TODAY)

    meine = _pushes_for(captured_pushes, haushalt.token)
    assert _types(meine) == ["forecast_below_buffer"]
    zustand = await _state(haushalt.household_id)
    assert zustand is not None
    assert zustand["first_negative_on"] is None
    assert zustand["first_below_buffer_on"] is not None

    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 1


async def test_a_shortfall_supersedes_the_buffer_message(
    captured_pushes: list[PushMessage],
) -> None:
    """Ein Verlauf, der ins Minus geht, liegt zwangslaeufig auch unter dem
    Puffer (dieser ist per CHECK >= 0). Ohne Vorrang kaeme eine einzige
    Verschlechterung als Zweiertraube auf dem Sperrbildschirm an."""
    haushalt = await _create_household()
    await _set_settings(haushalt.household_id, safety_buffer_minor=300_000)
    await check_forecast_alerts(TODAY)
    assert _pushes_for(captured_pushes, haushalt.token) == []

    await _set_opening_balance(haushalt.account_id, _ENGPASS_MINOR)
    await check_forecast_alerts(TODAY)

    assert _types(_pushes_for(captured_pushes, haushalt.token)) == ["forecast_shortfall"]
    # Beobachtet wurde beides - gesendet nur das Schwerere.
    zustand = await _state(haushalt.household_id)
    assert zustand is not None
    assert zustand["first_below_buffer_on"] is not None


async def test_a_month_turning_critical_is_reported_on_its_own(
    captured_pushes: list[PushMessage],
) -> None:
    """Der Puffer ist hier schon vor dem ersten Lauf unterschritten - es gibt
    also keinen Wechsel bei `first_below_buffer_on`. Neu ist allein, dass ein
    bisher unauffaelliger Monat unter den Puffer rutscht."""
    haushalt = await _create_household(opening_balance_minor=4_000_000)
    await _set_settings(haushalt.household_id, safety_buffer_minor=3_000_000)
    await check_forecast_alerts(TODAY)
    assert _pushes_for(captured_pushes, haushalt.token) == []
    vorher = await _state(haushalt.household_id)
    assert vorher is not None
    assert vorher["first_below_buffer_on"] is not None
    assert "2026-07" not in vorher["critical_months"]

    # Eine Jahresrechnung im Juli zieht die naechsten Monate unter den Puffer.
    await _create_contract(
        haushalt.household_id,
        name="Kfz-Versicherung",
        direction="expense",
        amount_minor=600_000,
        frequency="yearly",
        next_due_date=date(2026, 7, 10),
    )
    await check_forecast_alerts(TODAY)

    meine = _pushes_for(captured_pushes, haushalt.token)
    assert _types(meine) == ["forecast_month_critical"]
    assert meine[0]["data"]["forecast_on"] == "2026-07-01"
    nachher = await _state(haushalt.household_id)
    assert nachher is not None
    assert "2026-07" in nachher["critical_months"]

    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 1


# ---------------------------------------------------------------------------
# Ausgebliebene Einnahme
# ---------------------------------------------------------------------------


async def test_a_missing_income_is_reported_after_the_first_period(
    captured_pushes: list[PushMessage],
) -> None:
    """Schon die ERSTE ausgebliebene Einnahme ist eine Nachricht wert - anders
    als bei Ausgaben (contract_alerts wartet auf die zweite Periode). Eine
    nicht abgebuchte Lastschrift kostet nichts, ein nicht eingegangenes Gehalt
    ist der Anfang eines Engpasses."""
    haushalt = await _create_household()
    await check_forecast_alerts(TODAY)
    assert _pushes_for(captured_pushes, haushalt.token) == []

    await _set_missed_periods(haushalt.income_contract_id, 1)
    await check_forecast_alerts(TODAY)

    meine = _pushes_for(captured_pushes, haushalt.token)
    assert _types(meine) == ["forecast_income_missing"]
    assert meine[0]["body"] == "Gehalt ist diesmal nicht eingegangen."
    zustand = await _state(haushalt.household_id)
    assert zustand is not None
    assert [str(value) for value in zustand["missing_income_contract_ids"]] == [
        haushalt.income_contract_id
    ]

    # Zweiter Lauf ohne Aenderung: stumm.
    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 1

    # Nach Zahlungseingang wird zurueckgesetzt und danach erneut gemeldet.
    await _set_missed_periods(haushalt.income_contract_id, 0)
    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 1
    await _set_missed_periods(haushalt.income_contract_id, 1)
    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 2


async def test_a_missing_expense_is_none_of_this_runs_business(
    captured_pushes: list[PushMessage],
) -> None:
    """Ausgebliebene AUSGABEN gehoeren notify_contract_alerts. Zwei Laeufe zu
    derselben Tatsache waeren zwei Nachrichten."""
    haushalt = await _create_household()
    ausgabe = await _create_contract(
        haushalt.household_id,
        name="Fitnessstudio",
        direction="expense",
        amount_minor=3_000,
        next_due_date=date(2026, 6, 25),
    )
    await check_forecast_alerts(TODAY)
    await _set_missed_periods(ausgabe, 3)

    await check_forecast_alerts(TODAY)

    assert _pushes_for(captured_pushes, haushalt.token) == []


# ---------------------------------------------------------------------------
# Datenqualitaet
# ---------------------------------------------------------------------------


async def test_stale_data_silences_the_balance_and_warns_about_the_ground(
    captured_pushes: list[PushMessage],
) -> None:
    """Leitsatz 6: eine Prognose auf veralteten Daten ist eine Luege. Statt vor
    einem gerechneten Engpass wird vor der Grundlage gewarnt - und die
    unbelastbaren Zahlen werden gar nicht erst gemerkt, damit die Meldung nach
    dem Auffrischen der Daten nicht verschluckt wird."""
    haushalt = await _create_household()
    await check_forecast_alerts(TODAY)
    assert _pushes_for(captured_pushes, haushalt.token) == []

    # Beides auf einmal: veraltete Buchungen UND ein Engpass.
    await _age_last_booking(haushalt.household_id, 100)
    await _set_opening_balance(haushalt.account_id, _ENGPASS_MINOR)
    await check_forecast_alerts(TODAY)

    meine = _pushes_for(captured_pushes, haushalt.token)
    assert _types(meine) == ["forecast_data_stale"]
    zustand = await _state(haushalt.household_id)
    assert zustand is not None
    assert zustand["quality_level"] == "insufficient"
    assert zustand["first_negative_on"] is None

    await check_forecast_alerts(TODAY)
    assert len(_pushes_for(captured_pushes, haushalt.token)) == 1

    # Frische Buchungen: jetzt ist der Engpass belastbar und wird gemeldet.
    await _age_last_booking(haushalt.household_id, 1)
    await check_forecast_alerts(TODAY)
    assert _types(_pushes_for(captured_pushes, haushalt.token)) == [
        "forecast_data_stale",
        "forecast_shortfall",
    ]


# ---------------------------------------------------------------------------
# Schalter und Empfaenger
# ---------------------------------------------------------------------------


async def test_each_notify_switch_silences_only_its_own_type(
    captured_pushes: list[PushMessage],
) -> None:
    haushalt = await _create_household()
    await _set_settings(
        haushalt.household_id, safety_buffer_minor=300_000, notify_shortfall=False
    )
    await check_forecast_alerts(TODAY)

    # Abgeschalteter Engpass: die Vorrangkette laeuft trotzdem zuerst, sonst
    # traete dieselbe Nachricht ersatzweise als "below_buffer" heraus.
    await _set_opening_balance(haushalt.account_id, _ENGPASS_MINOR)
    await _set_missed_periods(haushalt.income_contract_id, 1)
    await check_forecast_alerts(TODAY)

    assert _types(_pushes_for(captured_pushes, haushalt.token)) == ["forecast_income_missing"]

    # Und der Zustand wurde trotz Stummschaltung fortgeschrieben: ein
    # abgeschalteter Schalter darf keinen Rueckstand aufbauen, der beim
    # Wiedereinschalten als Schwall herauskommt.
    zustand = await _state(haushalt.household_id)
    assert zustand is not None
    assert zustand["first_negative_on"] is not None


async def test_a_household_without_a_device_is_not_evaluated_at_all(
    captured_pushes: list[PushMessage],
) -> None:
    """Sonst schriebe der Lauf den Zustand fort, ohne dass je etwas zugestellt
    wurde - die erste Nachricht nach dem Registrieren eines Geraets fiele aus.
    Nebenbei ist es die wirksamste Mengenbegrenzung: die Vorschau ist der
    teuerste Lauf im Haus."""
    haushalt = await _create_household(with_token=False, opening_balance_minor=_ENGPASS_MINOR)

    await check_forecast_alerts(TODAY)
    await check_forecast_alerts(TODAY)

    assert captured_pushes == []
    assert await _state(haushalt.household_id) is None


async def test_the_run_sends_one_message_per_device(
    captured_pushes: list[PushMessage],
) -> None:
    haushalt = await _create_household()
    zweites_geraet = await _add_device(haushalt.user_id)
    await check_forecast_alerts(TODAY)

    await _set_opening_balance(haushalt.account_id, _ENGPASS_MINOR)
    await check_forecast_alerts(TODAY)

    assert _types(_pushes_for(captured_pushes, haushalt.token)) == ["forecast_shortfall"]
    assert _types(_pushes_for(captured_pushes, zweites_geraet)) == ["forecast_shortfall"]
