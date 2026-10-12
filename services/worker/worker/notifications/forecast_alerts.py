"""Push-Benachrichtigungen zur Liquiditaetsvorschau (Stufe 5d des
docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Regel aus Abschnitt 9).

Aufbau bewusst wie `contract_alerts.py`: ein Lauf, eine Auswahl, ein Versand,
eine Markierung. Fuenf Typen, weil alle fuenf aus DEMSELBEN Rechenlauf fallen -
fuenf Actors waeren fuenf Vorschauberechnungen je Haushalt und Nacht.

| data.type                 | Zustandswechsel                                    |
| ------------------------- | -------------------------------------------------- |
| forecast_shortfall        | first_negative_on gesetzt, beim letzten Lauf nicht  |
| forecast_below_buffer     | first_below_buffer_on gesetzt, letzten Lauf nicht   |
| forecast_month_critical   | Monatsendstand faellt unter den Puffer              |
| forecast_income_missing   | Einnahmevertrag mit missed_periods >= 1             |
| forecast_data_stale       | Datenqualitaet auf 'insufficient' gefallen          |

**Push nur bei Zustandswechsel, nie als wiederkehrende Zusammenfassung.** Die
Vorschau selbst ist eine reine Ableitung und aendert sich taeglich um
Kleinigkeiten; eine naechtliche Lagemeldung waere binnen einer Woche
abgeschaltet. Deshalb merkt `forecast_alert_state` je Haushalt den zuletzt
BEOBACHTETEN Zustand, und gesendet wird ausschliesslich die Verschlechterung
gegenueber diesem Stand.

Vier Regeln, die aus dieser einen Regel folgen und ohne die sie nicht traegt:

1. **Der Zustand wird immer zurueckgeschrieben, auch ohne Versand.** Erholt
   sich die Lage, muss `first_negative_on` wieder NULL werden - sonst meldet
   sich derselbe Engpass nach dem naechsten Absturz nie wieder.
2. **Der erste Lauf eines Haushalts sendet nichts.** Ohne vorherigen Stand ist
   "war es beim letzten Lauf nicht" nicht beantwortbar; die Alternative waere
   ein Schwall ueber den gesamten Bestand am Tag des Deployments - genau das,
   wogegen `contract_alerts._MAX_EVENT_AGE_DAYS` dort eine Grenze zieht.
3. **Hoechstens EINE Nachricht zur Kontolage je Lauf.** Ein Verlauf, der ins
   Minus geht, liegt zwangslaeufig auch unter dem Puffer (dieser ist per CHECK
   >= 0) und macht in aller Regel zugleich einen Monat kritisch. Ohne
   Vorrangkette (Minus > Puffer > Monat) kaeme eine einzige Verschlechterung
   als Dreiertraube auf dem Sperrbildschirm an.
4. **Bei Datenqualitaet 'insufficient' schweigt die Kontolage** - und wird
   auch nicht gemerkt. Leitsatz 6: eine Prognose auf veralteten Daten ist eine
   Luege. Abschnitt 9 formuliert den Ausloeser genau so - "die Daten sind zu
   alt, um noch zu warnen": statt vor einem gerechneten Engpass wird vor der
   Grundlage gewarnt. Wuerde der unbelastbar gerechnete Engpass trotzdem
   abgelegt, gaelte er nach dem Auffrischen der Daten als "schon gemeldet" -
   der erste belastbare Engpass fiele dann aus.

Architekturregel (Architekturplan Abschnitt 13): KEINE Betraege, Prozentsaetze
oder Fristdaten im Push-Text. Der Text nennt die Lage, nicht die Zahl. `data`
traegt nur `type` und - wo vorhanden - `forecast_on`; die Zahlen stehen in der
Vorschau, auf die die Nachricht routet (/mehr/vorschau).

Der Worker laeuft ohne RLS (siehe worker/db.py): jede Abfrage hier filtert
ausdruecklich nach `household_id`.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from worker.db import get_session_factory
from worker.notifications.push import PushMessage, send_expo_push_batch

logger = logging.getLogger(__name__)

# Dieselbe Bruecke wie in worker/contracts/reconcile.py: der Rechenkern der
# Vorschau liegt in services/api und wird von hier nur angesteuert (app.* kommt
# ueber PYTHONPATH). Eine zweite Kopie im Worker liefe spaetestens bei der
# ersten Aenderung an der Rundungsreihenfolge auseinander - und die Vorschau
# muss zeichenweise dasselbe rechnen wie die Seite, auf die die Nachricht routet.

# Schon die ERSTE ausgebliebene Einnahme ist eine Nachricht wert - anders als
# bei Ausgaben (contract_alerts._MISSING_MIN_PERIODS = 2). Eine nicht
# abgebuchte Lastschrift kostet den Nutzer nichts, ein nicht eingegangenes
# Gehalt ist der Anfang eines Engpasses; einen Monat abzuwarten hiesse, die
# Warnung genau um die Zeit zu verzoegern, in der sie noch nuetzt.
_INCOME_MISSING_MIN_PERIODS: Final = 1

# Rangfolge der Qualitaetsstufen. 'insufficient' ist die einzige Stufe, die
# eine Nachricht ausloest; die uebrigen dienen nur der Frage "war es vorher
# besser?".
_QUALITY_RANK: Final[dict[str, int]] = {"insufficient": 0, "limited": 1, "good": 2}

# Reihenfolge = Vorrang (Regel 3 im Modulkopf). Steht bewusst als Liste und
# nicht als Bedingungskette im Code, damit der Vorrang lesbar bleibt.
_BALANCE_KINDS: Final[tuple[str, ...]] = ("shortfall", "below_buffer", "month_critical")

_PUSH_TYPE_BY_KIND: Final[dict[str, str]] = {
    "shortfall": "forecast_shortfall",
    "below_buffer": "forecast_below_buffer",
    "month_critical": "forecast_month_critical",
    "income_missing": "forecast_income_missing",
    "data_stale": "forecast_data_stale",
}

# Titel und Rumpf je Art. Kein Betrag, kein Prozentsatz, kein Datum - der Text
# nennt die Lage, die Zahl steht in der Vorschau.
_TEXT_BY_KIND: Final[dict[str, tuple[str, str]]] = {
    "shortfall": ("Engpass in Sicht", "Deine Vorschau rutscht ins Minus."),
    "below_buffer": (
        "Puffer in Gefahr",
        "Deine Vorschau faellt unter den Sicherheitspuffer.",
    ),
    "month_critical": (
        "Ein Monat wird knapp",
        "Ein bisher unauffaelliger Monat rutscht unter den Puffer.",
    ),
    "income_missing": ("Einnahme ausgeblieben", "{label} ist diesmal nicht eingegangen."),
    "data_stale": (
        "Vorschau nicht belastbar",
        "Die Vorschau steht auf veralteten Daten.",
    ),
}

_FALLBACK_LABEL: Final = "Eine Einnahme"


@dataclass(frozen=True)
class ForecastAlert:
    """Eine versandreife Meldung. `forecast_on` ist der Tag, auf den sich die
    Lage bezieht - ein ISO-Datum ist als Routing-Nutzlast zulaessig, ein Betrag
    waere es nicht."""

    household_id: str
    kind: str
    forecast_on: date | None = None
    # Nur bei 'income_missing' gesetzt: der Vertrag, dessen Zahlung ausblieb.
    label: str | None = None


@dataclass(frozen=True)
class ForecastState:
    """Der beobachtete Zustand eines Haushalts - gelesen aus
    `forecast_alert_state` bzw. nach dem Lauf dorthin zurueckgeschrieben."""

    first_negative_on: date | None = None
    first_below_buffer_on: date | None = None
    quality_level: str | None = None
    critical_months: tuple[str, ...] = ()
    missing_income_contract_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForecastAlertBatch:
    """Was ein Lauf fuer EINEN Haushalt ergibt.

    Meldungen und Zustand gehoeren zusammen: der Zustand wird auch dann
    geschrieben, wenn `alerts` leer ist (Regel 1 im Modulkopf), und nur dann
    mit `notified_at`, wenn tatsaechlich etwas hinausging.
    """

    household_id: str
    state: ForecastState
    alerts: tuple[ForecastAlert, ...] = field(default_factory=tuple)


# Haushalte mit mindestens einem aktiven Mitglied, das ein gueltiges Geraet
# registriert hat. Dieselbe Vorfilterung wie in contract_alerts._PENDING_QUERY
# und aus demselben Grund: ein Haushalt ohne Empfaenger bekaeme sonst seinen
# Zustand fortgeschrieben, ohne dass je etwas zugestellt wurde - die erste
# Nachricht nach dem Registrieren eines Geraets fiele damit aus. Nebenbei ist
# es die wirksamste Mengenbegrenzung: die Vorschau ist der teuerste Lauf im
# Haus, und ohne Geraet ist sie hier zwecklos.
#
# Die drei Schalter kommen ueber einen LEFT JOIN mit coalesce(..., true) -
# forecast_settings entsteht erst beim ersten PUT, ein Haushalt ohne Datensatz
# ist also nicht abgeschaltet, sondern schlicht unkonfiguriert.
_CANDIDATES_QUERY = text(
    "select distinct hm.household_id as household_id, "
    "  coalesce(fs.notify_shortfall, true) as notify_shortfall, "
    "  coalesce(fs.notify_below_buffer, true) as notify_below_buffer, "
    "  coalesce(fs.notify_income_missing, true) as notify_income_missing "
    "from household_members hm "
    "join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "left join forecast_settings fs on fs.household_id = hm.household_id "
    "where hm.status = 'active' "
    "order by hm.household_id"
)

_STATE_QUERY = text(
    "select household_id, first_negative_on, first_below_buffer_on, quality_level, "
    "  critical_months, missing_income_contract_ids, updated_at "
    "from forecast_alert_state "
    "where household_id = any(cast(:household_ids as uuid[]))"
)

# Ausgebliebene Einnahmen. Bewusst aus `contracts` und nicht aus
# `contract_events`: der Dedup-Anker dort (notified_at) gehoert
# notify_contract_alerts, ein zweiter Verbraucher wuerde Nachrichten
# gegenseitig verschlucken (siehe Kopf von contract_alerts.py). Hier ist der
# Anker die Liste in forecast_alert_state.
_MISSING_INCOME_QUERY = text(
    "select c.household_id as household_id, c.id as contract_id, "
    "  coalesce(nullif(btrim(c.name), ''), nullif(btrim(c.provider_label), ''), p.name) "
    "    as label "
    "from contracts c "
    "left join payees p on p.id = c.payee_id "
    "where c.household_id = any(cast(:household_ids as uuid[])) "
    "and c.direction = 'income' "
    "and c.status = 'active' "
    "and c.missed_periods >= cast(:min_missed as int) "
    "order by c.household_id, c.id"
)

_TOKEN_QUERY = text(
    "select hm.household_id as household_id, dpt.expo_token as expo_token "
    "from household_members hm "
    "join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "where hm.status = 'active' "
    "and hm.household_id = any(cast(:household_ids as uuid[]))"
)

# `notified_at` wird nur gesetzt, wenn dieser Lauf etwas gesendet hat, und
# sonst auf dem alten Wert belassen - `updated_at` zieht dagegen jeder Lauf
# hoch. Die beiden beantworten verschiedene Fragen, und `updated_at` ist
# zugleich der Beleg dafuer, bis wohin der Horizont beim letzten Lauf reichte
# (siehe _newly_critical_months).
_UPSERT_STATE_QUERY = text(
    "insert into forecast_alert_state (household_id, first_negative_on, "
    "  first_below_buffer_on, quality_level, critical_months, "
    "  missing_income_contract_ids, notified_at, updated_at) "
    "values (:household_id, :first_negative_on, :first_below_buffer_on, :quality_level, "
    "  cast(:critical_months as text[]), cast(:missing_income_ids as uuid[]), "
    "  case when :notified then now() else null end, now()) "
    "on conflict (household_id) do update set "
    "  first_negative_on = excluded.first_negative_on, "
    "  first_below_buffer_on = excluded.first_below_buffer_on, "
    "  quality_level = excluded.quality_level, "
    "  critical_months = excluded.critical_months, "
    "  missing_income_contract_ids = excluded.missing_income_contract_ids, "
    "  notified_at = coalesce(excluded.notified_at, forecast_alert_state.notified_at), "
    "  updated_at = now()"
)


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _critical_months(result: Any) -> tuple[str, ...]:
    """Monate, deren Endstand unter dem Sicherheitspuffer liegt."""
    return tuple(
        _month_key(month.year, month.month)
        for month in result.monthly
        if month.closing_balance_minor < result.safety_buffer_minor
    )


def _newly_critical_months(
    current: tuple[str, ...], previous: ForecastState, previous_run_on: date, horizon_months: int
) -> tuple[str, ...]:
    """Monate, die beim letzten Lauf ueber dem Puffer lagen und jetzt darunter.

    Der Horizont rollt mit: jeden Monat waechst am Ende ein neuer Monat in die
    Vorschau hinein, den es beim letzten Lauf noch gar nicht gab. Ein solcher
    Monat war nie "unauffaellig" - ohne diese Grenze schickte der Lauf einmal
    im Monat verlaesslich eine Nachricht ueber den jeweils hintersten Monat,
    also genau die wiederkehrende Zusammenfassung, die es nicht geben soll.

    Die Grenze wird aus `updated_at` des gemerkten Zustands abgeleitet: bis
    dorthin plus Horizont reichte der letzte Lauf. Im Zweifel schweigt sie -
    ein zu enger Rand verschluckt hoechstens eine Meldung, ein zu weiter
    erzeugt eine falsche.
    """
    limit = _month_index(previous_run_on.year, previous_run_on.month) + horizon_months
    known = set(previous.critical_months)
    return tuple(
        month
        for month in current
        if month not in known
        and _month_index(int(month[:4]), int(month[5:7])) <= limit
    )


def _evaluate_household(
    household_id: str,
    result: Any,
    previous: ForecastState | None,
    *,
    previous_run_on: date | None,
    missing_income: list[tuple[str, str]],
    switches: dict[str, bool],
) -> ForecastAlertBatch:
    """Vergleicht den frisch gerechneten Zustand mit dem zuletzt gemerkten.

    Der zurueckgegebene Zustand ist immer der BEOBACHTETE, nie der gesendete:
    ein abgeschalteter Schalter unterdrueckt die Nachricht, er darf aber keinen
    Rueckstand aufbauen, der beim Wiedereinschalten als Schwall herauskommt.
    """
    quality_level: str = str(result.quality.level)
    metrics = result.metrics
    missing_ids = tuple(contract_id for contract_id, _ in missing_income)
    if quality_level == "insufficient":
        # Regel 4, zweite Haelfte: aus einer unbelastbaren Grundlage werden
        # auch keine Zahlen gemerkt. Wuerde hier der gerechnete Engpass
        # abgelegt, gaelte er nach dem Auffrischen der Daten als "schon
        # gemeldet" - und der erste belastbare Engpass fiele aus. Die
        # ausgebliebene Einnahme bleibt davon unberuehrt: sie ist eine
        # Tatsache aus den Buchungen, keine Prognose.
        state = ForecastState(
            first_negative_on=previous.first_negative_on if previous else None,
            first_below_buffer_on=previous.first_below_buffer_on if previous else None,
            quality_level=quality_level,
            critical_months=previous.critical_months if previous else (),
            missing_income_contract_ids=missing_ids,
        )
    else:
        state = ForecastState(
            first_negative_on=metrics.first_negative_on,
            first_below_buffer_on=metrics.first_below_buffer_on,
            quality_level=quality_level,
            critical_months=_critical_months(result),
            missing_income_contract_ids=missing_ids,
        )

    # Regel 2: ohne vorherigen Stand gibt es keinen Wechsel, nur eine Grundlage.
    if previous is None or previous_run_on is None:
        return ForecastAlertBatch(household_id=household_id, state=state)

    alerts: list[ForecastAlert] = []

    if quality_level == "insufficient":
        # Regel 4: statt vor einem gerechneten Engpass wird vor der Grundlage
        # gewarnt - und nur beim Wechsel dorthin.
        previous_rank = _QUALITY_RANK.get(previous.quality_level or "", -1)
        if previous_rank > _QUALITY_RANK["insufficient"]:
            alerts.append(ForecastAlert(household_id=household_id, kind="data_stale"))
    else:
        # Regel 3: die Vorrangkette laeuft VOR den Schaltern. Andernfalls
        # traete bei abgeschaltetem notify_shortfall dieselbe Nachricht
        # ersatzweise als "below_buffer" heraus.
        kind: str | None = None
        forecast_on: date | None = None
        if metrics.first_negative_on is not None and previous.first_negative_on is None:
            kind, forecast_on = "shortfall", metrics.first_negative_on
        elif (
            metrics.first_below_buffer_on is not None
            and previous.first_below_buffer_on is None
        ):
            kind, forecast_on = "below_buffer", metrics.first_below_buffer_on
        else:
            newly_critical = _newly_critical_months(
                state.critical_months, previous, previous_run_on, int(result.horizon_months)
            )
            if newly_critical:
                first = newly_critical[0]
                kind = "month_critical"
                # Der Monatserste ist der Anker, auf den die App springt - ein
                # Datum, kein Betrag.
                forecast_on = date(int(first[:4]), int(first[5:7]), 1)
        if kind is not None:
            alerts.append(
                ForecastAlert(household_id=household_id, kind=kind, forecast_on=forecast_on)
            )

    # Unabhaengig von der Qualitaet: eine ausgebliebene Einnahme ist eine
    # Tatsache aus den Buchungen, keine Prognose.
    known_missing = set(previous.missing_income_contract_ids)
    alerts.extend(
        ForecastAlert(household_id=household_id, kind="income_missing", label=label)
        for contract_id, label in missing_income
        if contract_id not in known_missing
    )

    return ForecastAlertBatch(
        household_id=household_id,
        state=state,
        alerts=tuple(alert for alert in alerts if _is_enabled(alert.kind, switches)),
    )


def _is_enabled(kind: str, switches: dict[str, bool]) -> bool:
    """Welcher Schalter welche Art stumm schaltet.

    'month_critical' haengt an notify_below_buffer: der Ausloeser IST das
    Unterschreiten des Puffers, nur monatsweise statt taggenau - zwei Schalter
    fuer dieselbe Frage waeren zwei Wahrheiten. 'data_stale' hat keinen eigenen
    Schalter (der Plan sieht drei vor) und schweigt erst, wenn der Nutzer alle
    Vorschau-Benachrichtigungen abgeschaltet hat: es ist die Meldung, dass die
    uebrigen gerade nicht kommen koennen.
    """
    if kind == "shortfall":
        return switches["notify_shortfall"]
    if kind in ("below_buffer", "month_critical"):
        return switches["notify_below_buffer"]
    if kind == "income_missing":
        return switches["notify_income_missing"]
    return any(switches.values())


async def _load_states(
    session: AsyncSession, household_ids: list[str]
) -> dict[str, tuple[ForecastState, date]]:
    """Gemerkter Zustand je Haushalt, dazu der Tag des letzten Laufs."""
    if not household_ids:
        return {}
    result = await session.execute(_STATE_QUERY, {"household_ids": household_ids})
    states: dict[str, tuple[ForecastState, date]] = {}
    for row in result.mappings().all():
        states[str(row["household_id"])] = (
            ForecastState(
                first_negative_on=row["first_negative_on"],
                first_below_buffer_on=row["first_below_buffer_on"],
                quality_level=row["quality_level"],
                critical_months=tuple(row["critical_months"] or ()),
                missing_income_contract_ids=tuple(
                    str(value) for value in (row["missing_income_contract_ids"] or ())
                ),
            ),
            row["updated_at"].date(),
        )
    return states


async def _load_missing_income(
    session: AsyncSession, household_ids: list[str]
) -> dict[str, list[tuple[str, str]]]:
    """Ausgebliebene Einnahmen je Haushalt als (contract_id, label)."""
    if not household_ids:
        return {}
    result = await session.execute(
        _MISSING_INCOME_QUERY,
        {"household_ids": household_ids, "min_missed": _INCOME_MISSING_MIN_PERIODS},
    )
    missing: dict[str, list[tuple[str, str]]] = {}
    for row in result.mappings().all():
        missing.setdefault(str(row["household_id"]), []).append(
            (str(row["contract_id"]), str(row["label"]) if row["label"] else _FALLBACK_LABEL)
        )
    return missing


async def select_pending_alerts(
    session: AsyncSession, *, today: date
) -> list[ForecastAlertBatch]:
    """Rechnet je Haushalt mit Empfaenger die Vorschau und vergleicht sie mit
    dem zuletzt gemerkten Zustand.

    `today` kommt von aussen statt aus `date.today()` - derselbe Grund wie in
    contract_alerts.select_pending_alerts und im Rechenkern selbst
    (household_forecast_service: `as_of` wird immer hereingereicht, sonst ist
    kein Fall reproduzierbar).

    Liefert einen Eintrag je Haushalt, auch ohne Meldung: der beobachtete
    Zustand muss auch dann zurueckgeschrieben werden (Regel 1 im Modulkopf).
    """
    # Der Import steht IN der Funktion, nicht auf Modulebene - wie in
    # worker/contracts/reconcile.py: `worker.actors` wird auch vom API-Prozess
    # und von den Worker-Tests importiert, und auf Modulebene zoege dieser
    # Import den kompletten FastAPI-Zweig aus services/api mit.
    from app.services.household_forecast_service import build_forecast

    candidates = (await session.execute(_CANDIDATES_QUERY)).mappings().all()
    household_ids = [str(row["household_id"]) for row in candidates]
    if not household_ids:
        return []

    states = await _load_states(session, household_ids)
    missing_income = await _load_missing_income(session, household_ids)

    batches: list[ForecastAlertBatch] = []
    for row in candidates:
        household_id = str(row["household_id"])
        try:
            result = await build_forecast(
                session, UUID(household_id), as_of=today, granularity="month"
            )
        except Exception:
            # Ein Haushalt mit kaputten Eingaben darf nicht den gesamten Lauf
            # verschlucken - die uebrigen bekaemen sonst wortlos keine
            # Nachricht. Rollback, weil eine gescheiterte Abfrage die
            # Transaktion sonst fuer alle folgenden sperrt.
            logger.warning(
                "Vorschau fuer Haushalt %s nicht berechenbar - uebersprungen.",
                household_id,
                exc_info=True,
            )
            await session.rollback()
            continue
        previous = states.get(household_id)
        batches.append(
            _evaluate_household(
                household_id,
                result,
                previous[0] if previous else None,
                previous_run_on=previous[1] if previous else None,
                missing_income=missing_income.get(household_id, []),
                switches={
                    "notify_shortfall": bool(row["notify_shortfall"]),
                    "notify_below_buffer": bool(row["notify_below_buffer"]),
                    "notify_income_missing": bool(row["notify_income_missing"]),
                },
            )
        )
    return batches


async def load_push_tokens(
    session: AsyncSession, household_ids: list[str]
) -> dict[str, list[str]]:
    """Gueltige Expo-Token je Haushalt ueber dessen aktive Mitglieder."""
    if not household_ids:
        return {}
    result = await session.execute(_TOKEN_QUERY, {"household_ids": household_ids})
    tokens: dict[str, list[str]] = {}
    for row in result.mappings().all():
        tokens.setdefault(str(row["household_id"]), []).append(str(row["expo_token"]))
    return tokens


def build_push_messages(
    batches: list[ForecastAlertBatch], tokens_by_household: dict[str, list[str]]
) -> list[PushMessage]:
    """Baut je Meldung und Geraet eine Nachricht.

    `data` traegt nur `type` und - wo es einen gibt - `forecast_on`. Ein Datum
    als ISO-String ist zulaessig und routet die App auf den richtigen Tag; ein
    Betrag, ein Prozentsatz oder ein Fehlbetrag waeren es nicht (Architekturplan
    Abschnitt 13). Alle fuenf Typen fuehren in der App auf /mehr/vorschau.
    """
    messages: list[PushMessage] = []
    for batch in batches:
        for alert in batch.alerts:
            title, body_template = _TEXT_BY_KIND[alert.kind]
            data: dict[str, Any] = {"type": _PUSH_TYPE_BY_KIND[alert.kind]}
            if alert.forecast_on is not None:
                data["forecast_on"] = alert.forecast_on.isoformat()
            for token in tokens_by_household.get(alert.household_id, []):
                messages.append(
                    PushMessage(
                        to=token,
                        title=title,
                        body=body_template.format(label=alert.label or _FALLBACK_LABEL),
                        data=dict(data),
                    )
                )
    return messages


async def mark_alerts_notified(
    session: AsyncSession, batches: list[ForecastAlertBatch]
) -> None:
    """Schreibt den beobachteten Zustand je Haushalt zurueck.

    Auch fuer Haushalte OHNE Meldung - das ist der Ruecksetzweg (Regel 1 im
    Modulkopf). Ein Lauf, der nur nach dem Senden markierte, koennte eine
    ueberstandene Lage nie wieder melden.
    """
    for batch in batches:
        await session.execute(
            _UPSERT_STATE_QUERY,
            {
                "household_id": batch.household_id,
                "first_negative_on": batch.state.first_negative_on,
                "first_below_buffer_on": batch.state.first_below_buffer_on,
                "quality_level": batch.state.quality_level,
                "critical_months": list(batch.state.critical_months),
                "missing_income_ids": list(batch.state.missing_income_contract_ids),
                "notified": bool(batch.alerts),
            },
        )


async def check_forecast_alerts(today: date | None = None) -> None:
    """Versendet die faelligen Vorschau-Hinweise und schreibt den Zustand fort.

    Reihenfolge ist Absicht und dieselbe wie in contract_alerts: erst senden,
    dann markieren. Andersherum verschluckte ein Abbruch mitten im Batch die
    Nachricht dauerhaft - so wird sie im schlechtesten Fall einmal doppelt
    zugestellt, und genau deshalb steht der Actor auf max_retries=0
    (worker/actors.py).
    """
    stichtag = today or date.today()
    session_factory = get_session_factory()
    async with session_factory() as session:
        batches = await select_pending_alerts(session, today=stichtag)
        if not batches:
            logger.info("Keine Haushalte mit Empfaenger fuer Vorschau-Hinweise.")
            return

        with_alerts = [batch for batch in batches if batch.alerts]
        messages: list[PushMessage] = []
        if with_alerts:
            tokens_by_household = await load_push_tokens(
                session, sorted({batch.household_id for batch in with_alerts})
            )
            messages = build_push_messages(with_alerts, tokens_by_household)

        if messages:
            tickets = await send_expo_push_batch(messages)
            for ticket in tickets:
                if ticket.get("status") == "error":
                    logger.warning("Expo-Push-Ticket-Fehler: %s", ticket.get("message"))

        await mark_alerts_notified(session, batches)
        await session.commit()

        logger.info(
            "Vorschau-Hinweis-Push verarbeitet: %s Haushalt(e) geprueft, "
            "%s mit Meldung, %s Nachricht(en).",
            len(batches),
            len(with_alerts),
            len(messages),
        )
