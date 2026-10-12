"""Session- und Transaktionsklammer um den Vertragsabgleich (Vorbild:
`worker/imports/orchestrator.py`).

Der Worker committet ausdruecklich: anders als im API-Prozess, wo die
Transaktion den gesamten Request umschliesst (`app/auth/dependencies.py`),
gibt es hier keine umgebende Klammer. Der Motor selbst committet nie.
"""

import logging
from datetime import date
from uuid import UUID

from worker.contracts.reconcile import (
    list_reconcilable_household_ids,
    reconcile_contracts_for_household,
)
from worker.db import get_session_factory

logger = logging.getLogger(__name__)


async def run_reconcile(household_id: str | UUID) -> None:
    """Abgleich eines einzelnen Haushalts, ausgeloest von einem Import-Commit."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await reconcile_contracts_for_household(
            session, UUID(str(household_id)), today=date.today()
        )
        await session.commit()


async def run_reconcile_all() -> None:
    """Naechtlicher Lauf ueber alle Haushalte mit Vertraegen.

    Je Haushalt eine eigene Session und ein eigener Commit. Drei Gruende:
    ein Haushalt mit unerwarteten Daten darf weder die bereits geschriebenen
    Ergebnisse der anderen zurueckrollen noch den restlichen Lauf abbrechen;
    und eine einzige Transaktion ueber alle Haushalte hielte ihre Sperren und
    ihren Snapshot ueber den gesamten Lauf.

    Das Datum wird einmal bestimmt, damit ein ueber Mitternacht laufender
    Durchgang nicht in der Mitte den Faelligkeitsstichtag wechselt.
    """
    today = date.today()
    session_factory = get_session_factory()

    async with session_factory() as session:
        household_ids = await list_reconcilable_household_ids(session)

    logger.info("Naechtlicher Vertragsabgleich startet fuer %s Haushalt(e).", len(household_ids))

    failed = 0
    for household_id in household_ids:
        async with session_factory() as session:
            try:
                await reconcile_contracts_for_household(session, household_id, today=today)
                await session.commit()
            except Exception:
                # Bewusst kein erneutes Werfen: ein Retry des Actors wuerde alle
                # bereits erfolgreich abgeglichenen Haushalte noch einmal
                # durchlaufen. Der Lauf ist zwar idempotent, aber der naechste
                # Durchgang in 24 Stunden holt den einen Haushalt ohnehin nach.
                await session.rollback()
                failed += 1
                logger.exception(
                    "Vertragsabgleich fuer Haushalt %s fehlgeschlagen.", household_id
                )

    if failed:
        logger.warning(
            "Naechtlicher Vertragsabgleich: %s von %s Haushalt(en) fehlgeschlagen.",
            failed,
            len(household_ids),
        )
