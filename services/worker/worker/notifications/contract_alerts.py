"""Push-Benachrichtigungen zu Vertragsereignissen (Phase 4 des Vertragsplans,
docs/VERTRAEGE-FIXKOSTEN-PLAN.md Abschnitt 11).

Drei Typen, ein Lauf. Getrennte Actors waeren drei Fehlerquellen fuer eine
einzige Abfrage: alle drei lesen dieselbe Tabelle, teilen denselben
Dedup-Anker und denselben Empfaengerweg.

| data.type                | Ereignis          | Zusaetzliche Bedingung             |
| ------------------------ | ----------------- | ---------------------------------- |
| contract_price_change    | 'price_change'    | Vertrag laeuft noch                |
| contract_payment_missing | 'payment_missing' | missed_periods >= 2                |
| contract_term_ending     | 'term_ending'     | Vertrag laeuft und ist ungekuendigt|

Bestaetigte Ereignisse (`acknowledged_at`) fallen bei allen drei Arten heraus:
wer den Hinweis in der App schon gesehen hat, braucht ihn abends nicht noch
einmal auf dem Sperrbildschirm. Seit der Rueckfrage bei geaendertem Betrag
setzt auch die Review-Inbox diese Spalte (api/v1/routes/review.py,
`_confirm_contract_amount_change`) - eine dort bestaetigte Preisaenderung wird
hier also bewusst nicht mehr gesendet.

Der Dedup haengt an `contract_events.notified_at` und ausdruecklich NICHT an
`contracts.notified_at`: die Spalte gehoert `recurring_due` und wird dort je
Faelligkeit ueberschrieben (due_recurring.py) - ein zweiter Verbraucher wuerde
Nachrichten gegenseitig verschlucken. `contract_events` liefert dagegen je
Tatsache genau eine Zeile, und fuer die Fristerinnerung sichert
`contract_events_term_ending_uniq` (Migration 0015) schon das Entstehen eines
zweiten Ereignisses zu (Vertrag, Termin, Stufe) ab.

Architekturregel (Architekturplan Abschnitt 13): KEINE Betraege im Push-Text.
Ein Sperrbildschirm ist kein vertraulicher Ort; der genaue Betrag steht im
Vertrag selbst, auf den die Nachricht routet.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from worker.db import get_session_factory
from worker.notifications.push import PushMessage, send_expo_push_batch

logger = logging.getLogger(__name__)

# Aelteres bleibt liegen. Ohne diese Grenze loeste der allererste Lauf nach dem
# Deployment einen Schwall ueber die gesamte seit Phase 2 geschriebene
# Ereignishistorie aus - alle diese Zeilen haben notified_at is null, weil sie
# bisher niemand gelesen hat.
_MAX_EVENT_AGE_DAYS: Final = 14

# Erst die zweite ausgebliebene Periode ist eine Nachricht wert. Eine einzelne
# kann schlicht eine verspaetete Lastschrift sein; zwei hintereinander sind ein
# Signal. Bewusst dieselbe Schwelle wie die Review-Inbox
# (rules_service._CONTRACT_ENDED_CHECK_MIN_MISSED) - eine abweichende Zahl waere
# eine zweite Wahrheit zur selben Frage.
_MISSING_MIN_PERIODS: Final = 2

_PUSH_TYPE_BY_KIND: Final[dict[str, str]] = {
    "price_change": "contract_price_change",
    "payment_missing": "contract_payment_missing",
    "term_ending": "contract_term_ending",
}

# Titel und Rumpf je Ereignisart. Der Rumpf traegt nur das Vertragslabel, nie
# einen Betrag, nie ein Datum.
#
# Der Rumpf zu 'price_change' ist richtungsneutral: eine Preisaenderung geht in
# BEIDE Richtungen, und ohne verwertbares Vorzeichen im Ereignis darf hier
# keine Richtung behauptet werden. Steht das Vorzeichen fest, gewinnt
# _PRICE_CHANGE_BODY_BY_SIGN.
_TEXT_BY_KIND: Final[dict[str, tuple[str, str]]] = {
    "price_change": ("Preis geaendert", "{label} hat einen neuen Preis."),
    "payment_missing": ("Zahlung ausgeblieben", "{label} wurde diesmal nicht abgebucht."),
    "term_ending": ("Kuendigungsfrist laeuft", "{label}: die Frist endet bald."),
}

# Frueher stand hier unbedingt "{label} kostet ab sofort mehr." - auch fuer
# Senkungen, von denen ein gewachsener Bestand messbar viele traegt: gut jedes
# fuenfte `price_change`-Ereignis hat ein negatives Delta.
# Eine Nachricht, die dem Nutzer eine Erhoehung meldet, wo sein Anbieter den
# Preis gesenkt hat, ist keine Ungenauigkeit, sondern eine falsche
# Tatsachenbehauptung ueber sein Geld.
#
# Das Vorzeichen kommt aus `payload->>'delta_minor'` (neu minus alt, geschrieben
# in contract_matching_service._hit_outcome). Der Betrag selbst bleibt wie
# gehabt draussen - Architekturplan Abschnitt 13.
_PRICE_CHANGE_BODY_BY_SIGN: Final[dict[str, str]] = {
    "up": "{label} kostet ab sofort mehr.",
    "down": "{label} kostet ab sofort weniger.",
}

_FALLBACK_LABEL: Final = "Ein Vertrag"


@dataclass(frozen=True)
class ContractAlert:
    """Ein versandreifes Ereignis - je Vertrag und Ereignisart hoechstens eines."""

    event_id: str
    household_id: str
    contract_id: str
    kind: str
    label: str
    # Nur bei 'price_change' gesetzt und auch dort nur, wenn die Nutzlast eine
    # lesbare ganze Zahl traegt. `None` heisst "Richtung unbekannt" und fuehrt
    # bewusst zum neutralen Text, nicht zu einer geratenen Richtung.
    delta_minor: int | None = None


# `distinct on (contract_id, kind)`: nur das juengste Ereignis je Vertrag und
# Art geht raus. Ein Nachholabgleich ueber sechs Monate schreibt sonst sechs
# 'payment_missing'-Zeilen und der Nutzer bekaeme sechs Nachrichten zu einer
# einzigen Tatsache. Die aelteren werden im selben Lauf mitmarkiert
# (_MARK_NOTIFIED_QUERY), sie sind ueberholt und nicht mehr sendenswert.
#
# `exists (...)` auf die Geraete filtert Haushalte ohne Push-Token schon hier
# heraus, statt sie spaeter beim Bauen der Nachrichten zu verlieren: sonst
# wuerde der Lauf ihre Ereignisse als "benachrichtigt" markieren, ohne dass je
# etwas zugestellt wurde.
#
# Die Altersgrenze haengt je Art an einem anderen Datum, weil `occurred_on` je
# Art etwas anderes bedeutet. Bei 'price_change' (Buchungstag) und
# 'term_ending' (Tag des Fristlaufs) ist es der Tag der Nachricht selbst. Bei
# 'payment_missing' ist es die ERSTE ausgebliebene Faelligkeit - die Schwelle
# von zwei Perioden ist per Definition erst eine volle Periode spaeter erreicht,
# ein Fenster auf `occurred_on` wuerde also genau die Vertraege verschweigen,
# fuer die es diesen Typ gibt. Dort zaehlt deshalb die juengste ausgebliebene
# Faelligkeit aus contract_occurrences: sie beantwortet "ist der Vertrag JETZT
# still?" statt "wann fing es an?".
_PENDING_QUERY = text(
    "select distinct on (ce.contract_id, ce.kind) "
    "  ce.id as event_id, ce.household_id as household_id, "
    "  ce.contract_id as contract_id, ce.kind as kind, "
    "  coalesce(nullif(btrim(c.name), ''), nullif(btrim(c.provider_label), ''), p.name) "
    "    as label, "
    # Der Regex-Schutz steht IM case und nicht als eigene Bedingung: ein
    # ungeschuetzter Cast auf eine unerwartete Nutzlast wuerde den gesamten
    # naechtlichen Lauf mit 22P02 abbrechen, statt nur diese eine Richtung
    # offenzulassen.
    "  case when ce.payload ->> 'delta_minor' ~ '^-?[0-9]+$' "
    "       then (ce.payload ->> 'delta_minor')::bigint end as delta_minor "
    "from contract_events ce "
    "join contracts c on c.id = ce.contract_id "
    "left join payees p on p.id = c.payee_id "
    "left join contract_settings cs on cs.household_id = ce.household_id "
    "where ce.notified_at is null "
    "and ce.acknowledged_at is null "
    "and ce.kind in ('price_change', 'payment_missing', 'term_ending') "
    "and exists ("
    "  select 1 from household_members hm "
    "  join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "  where hm.household_id = ce.household_id and hm.status = 'active') "
    "and case ce.kind "
    "  when 'price_change' then "
    "    coalesce(cs.notify_price_change, true) "
    "    and c.status in ('active', 'cancelled') "
    "    and (c.ends_on is null or c.ends_on >= cast(:today as date)) "
    "    and ce.occurred_on >= cast(:today as date) - cast(:max_age_days as int) "
    "  when 'payment_missing' then "
    "    coalesce(cs.notify_payment_missing, true) "
    "    and c.status = 'active' "
    "    and c.missed_periods >= cast(:min_missed as int) "
    "    and coalesce("
    "      (select max(o.due_date) from contract_occurrences o "
    "       where o.contract_id = ce.contract_id and o.household_id = ce.household_id "
    "       and o.status = 'missing'), ce.occurred_on) "
    "      >= cast(:today as date) - cast(:max_age_days as int) "
    "  when 'term_ending' then "
    "    coalesce(cs.notify_term_ending, true) "
    "    and c.status in ('active', 'paused') "
    "    and c.cancelled_on is null "
    "    and ce.occurred_on >= cast(:today as date) - cast(:max_age_days as int) "
    "  else false "
    "end "
    "order by ce.contract_id, ce.kind, ce.occurred_on desc, ce.created_at desc"
)

_TOKEN_QUERY = text(
    "select hm.household_id as household_id, dpt.expo_token as expo_token "
    "from household_members hm "
    "join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "where hm.status = 'active' "
    "and hm.household_id = any(cast(:household_ids as uuid[]))"
)

# Ein Update je Ereignis, nicht je Geraet: ein Haushalt mit drei Geraeten
# bekommt drei Nachrichten zu einem Ereignis, nie zwei Ereignisse zu derselben
# Tatsache. Der household_id steht ausdruecklich in der Bedingung - der Worker
# laeuft ohne RLS, hier filtert nichts von allein.
_MARK_NOTIFIED_QUERY = text(
    "update contract_events ce set notified_at = now() "
    "from unnest(cast(:household_ids as uuid[]), cast(:contract_ids as uuid[]), "
    "            cast(:kinds as text[])) as sel(household_id, contract_id, kind) "
    "where ce.household_id = sel.household_id "
    "and ce.contract_id = sel.contract_id "
    "and ce.kind = sel.kind "
    "and ce.notified_at is null"
)


async def select_pending_alerts(session: AsyncSession, *, today: date) -> list[ContractAlert]:
    """Liefert je Vertrag und Ereignisart hoechstens ein versandreifes Ereignis.

    `today` kommt von aussen statt aus `current_date`: die Prozesszeitzone des
    Workers und die Serverzeitzone der Datenbank muessen sich an der
    Tagesgrenze nicht einig sein, und der Abgleichslauf uebergibt seinen
    Stichtag aus demselben Grund bereits explizit
    (worker/contracts/orchestrator.py).
    """
    result = await session.execute(
        _PENDING_QUERY,
        {
            "today": today,
            "max_age_days": _MAX_EVENT_AGE_DAYS,
            "min_missed": _MISSING_MIN_PERIODS,
        },
    )
    return [
        ContractAlert(
            event_id=str(row["event_id"]),
            household_id=str(row["household_id"]),
            contract_id=str(row["contract_id"]),
            kind=str(row["kind"]),
            label=str(row["label"]) if row["label"] else _FALLBACK_LABEL,
            delta_minor=(
                int(row["delta_minor"]) if row["delta_minor"] is not None else None
            ),
        )
        for row in result.mappings().all()
    ]


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
    alerts: list[ContractAlert], tokens_by_household: dict[str, list[str]]
) -> list[PushMessage]:
    """Baut je Ereignis und Geraet eine Nachricht.

    `contract_id` und `event_id` in `data` sind der gesamte Nutzlastumfang: das
    Routing braucht den Vertrag (apps/mobile/src/notifications/
    useNotificationResponseRouter.ts), die Ereignis-ID macht eine zugestellte
    Nachricht im Nachhinein einer Zeile zuordenbar. Betraege, Prozentsaetze und
    Fristdaten bleiben draussen - sie stehen im Vertrag, nicht auf dem
    Sperrbildschirm.
    """
    messages: list[PushMessage] = []
    for alert in alerts:
        title, body_template = _TEXT_BY_KIND[alert.kind]
        if alert.kind == "price_change" and alert.delta_minor:
            # `and alert.delta_minor` faengt None UND 0 ab: eine Aenderung um
            # null ist keine Richtung.
            body_template = _PRICE_CHANGE_BODY_BY_SIGN[
                "up" if alert.delta_minor > 0 else "down"
            ]
        for token in tokens_by_household.get(alert.household_id, []):
            messages.append(
                PushMessage(
                    to=token,
                    title=title,
                    body=body_template.format(label=alert.label),
                    data={
                        "type": _PUSH_TYPE_BY_KIND[alert.kind],
                        "contract_id": alert.contract_id,
                        "event_id": alert.event_id,
                    },
                )
            )
    return messages


async def mark_alerts_notified(session: AsyncSession, alerts: list[ContractAlert]) -> None:
    """Markiert die versendeten Ereignisse UND ihre ueberholten Vorgaenger
    derselben (Vertrag, Art)-Kombination als benachrichtigt.

    Auch die Vorgaenger: sie beschreiben dieselbe Tatsache in einem aelteren
    Zustand. Blieben sie offen, waere die naechste Nacht ein zweiter Versand.
    """
    if not alerts:
        return
    await session.execute(
        _MARK_NOTIFIED_QUERY,
        {
            "household_ids": [alert.household_id for alert in alerts],
            "contract_ids": [alert.contract_id for alert in alerts],
            "kinds": [alert.kind for alert in alerts],
        },
    )


async def check_contract_alerts(today: date | None = None) -> None:
    """Versendet die faelligen Vertragshinweise und markiert sie.

    Reihenfolge ist Absicht: erst senden, dann markieren. Andersherum wuerde ein
    Abbruch mitten im Batch die Nachricht dauerhaft verschlucken - so wird sie
    im schlechtesten Fall einmal doppelt zugestellt, und genau deshalb ist der
    Actor auf max_retries=0 gesetzt (worker/actors.py).
    """
    stichtag = today or date.today()
    session_factory = get_session_factory()
    async with session_factory() as session:
        alerts = await select_pending_alerts(session, today=stichtag)
        if not alerts:
            logger.info("Keine offenen Vertragshinweise fuer Push-Benachrichtigungen.")
            return

        household_ids = sorted({alert.household_id for alert in alerts})
        tokens_by_household = await load_push_tokens(session, household_ids)
        messages = build_push_messages(alerts, tokens_by_household)

        if messages:
            tickets = await send_expo_push_batch(messages)
            for ticket in tickets:
                if ticket.get("status") == "error":
                    logger.warning("Expo-Push-Ticket-Fehler: %s", ticket.get("message"))

        await mark_alerts_notified(session, alerts)
        await session.commit()

        logger.info(
            "Vertragshinweis-Push verarbeitet: %s Ereignis(se) in %s Haushalt(en), "
            "%s Nachricht(en).",
            len(alerts),
            len(household_ids),
            len(messages),
        )
