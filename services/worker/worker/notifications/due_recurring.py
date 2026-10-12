"""Erste Push-Benachrichtigungsart (Phase 9): "Fixkosten in Kuerze faellig".

Ausgewaehlt statt "offene Importpruefung", weil die Faelligkeit eines Vertrags
eine eindeutige, einfache Grenze mit einem natuerlichen Dedup-Schluessel
liefert (Vertrags-ID + notified_at vs. next_due_date). Die
Review-Inbox-Anzahl schwankt dagegen laufend (kontinuierliche Importe) - das
ist der naheliegende naechste Benachrichtigungstyp, sobald ein
Haushalts-Dedup existiert.

Seit Migration 0014 liest der Lauf `contracts` statt `recurring_rules`.
Gekuendigte Vertraege bleiben eingeschlossen: sie kosten bis zum Enddatum
weiter Geld, und genau diese letzte Abbuchung ueberrascht sonst.

Der Nachrichtentyp heisst weiterhin `recurring_due`, damit bereits
ausgelieferte App-Versionen die Benachrichtigung wie bisher auf den
Fixkostenbereich routen (apps/mobile/src/notifications/useNotificationResponseRouter.ts).
"""

import logging

from sqlalchemy import text

from worker.db import get_session_factory
from worker.notifications.push import PushMessage, send_expo_push_batch

logger = logging.getLogger(__name__)

_DUE_WINDOW_DAYS = 3

_DUE_QUERY = text(
    "select c.id as contract_id, coalesce(nullif(btrim(c.name), ''), p.name) as label, "
    "dpt.expo_token "
    "from contracts c "
    "join household_members hm on hm.household_id = c.household_id and hm.status = 'active' "
    "join device_push_tokens dpt on dpt.user_id = hm.user_id and dpt.is_valid "
    "left join payees p on p.id = c.payee_id "
    "where c.status in ('active', 'cancelled') "
    "and (c.ends_on is null or c.ends_on >= current_date) "
    f"and c.next_due_date between current_date and current_date + {_DUE_WINDOW_DAYS} "
    "and (c.notified_at is null or c.notified_at < c.next_due_date)"
)


async def check_due_recurring() -> None:
    """Sucht faellige Vertraege ohne bereits gesendete Benachrichtigung fuer
    dieses Vorkommen, sendet eine Push-Nachricht je registriertem
    Geraet der aktiven Haushaltsmitglieder und markiert den Vertrag
    anschliessend als benachrichtigt (Architekturplan Abschnitt 13: keine
    konkreten Betraege im Push-Inhalt)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(_DUE_QUERY)
        rows = result.mappings().all()

        if not rows:
            logger.info("Keine faelligen Vertraege fuer Push-Benachrichtigungen gefunden.")
            return

        messages: list[PushMessage] = []
        due_contract_ids: set[str] = set()
        for row in rows:
            due_contract_ids.add(str(row["contract_id"]))
            label = row["label"] or "Eine Zahlung"
            messages.append(
                PushMessage(
                    to=row["expo_token"],
                    title="Fixkosten in Kuerze faellig",
                    body=f"{label} wird bald faellig.",
                    data={
                        "type": "recurring_due",
                        "contract_id": str(row["contract_id"]),
                        # Alter Schluessel fuer bereits ausgelieferte Apps.
                        "recurring_rule_id": str(row["contract_id"]),
                    },
                )
            )

        tickets = await send_expo_push_batch(messages)
        for ticket in tickets:
            if ticket.get("status") == "error":
                logger.warning("Expo-Push-Ticket-Fehler: %s", ticket.get("message"))

        for contract_id in due_contract_ids:
            await session.execute(
                text("update contracts set notified_at = now() where id = :id"),
                {"id": contract_id},
            )
        await session.commit()

        logger.info(
            "Fixkosten-Push verarbeitet: %s Vertrag/Vertraege, %s Nachricht(en).",
            len(due_contract_ids),
            len(messages),
        )
