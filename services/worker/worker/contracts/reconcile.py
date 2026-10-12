"""Worker-seitiger Einstieg in den Vertragsabgleich
(docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 5).

Der Motor selbst liegt in `services/api/app/services/contract_matching_service.py`
und wird von hier nur angesteuert. Er ist zugleich der Schreibpfad des synchronen
`POST /v1/contracts/rescan`; eine zweite Kopie im Worker wuerde spaetestens bei
der ersten Aenderung an der Bewertungsformel auseinanderlaufen - und
`open_amount_stage` liesse sich ohnehin nicht duplizieren, ohne die Invariante
"genau eine offene Preisstufe je Vertrag" zu gefaehrden.

`app.*` (services/api) kommt ueber PYTHONPATH: `services/worker/Dockerfile`
setzt `PYTHONPATH=/app/services/api` und kopiert `services/api/app` mit,
`services/worker/pyproject.toml` traegt den Pfad fuer pytest ein und
`scripts/dev.py` fuer den nativen Start. Frueher haengte jedes Modul den Pfad
selbst an `sys.path`; seit der KI-Kern (app/llm) auf Modulebene importiert
wird, waere das zu spaet. Das Worker-Image installiert `fastapi` mit (die
Modulkette des Motors endet bei `from fastapi import HTTPException`). Fehlt
eines von beidem, brechen die Vertrags-Actors bei jedem Lauf mit
`ModuleNotFoundError` ab - und zwar lautlos, weil der naechtliche Lauf
niemandem antwortet.
"""

import logging
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Die Importe des Motors stehen bewusst in den Funktionen statt auf Modulebene:
# `worker.actors` wird auch vom API-Prozess (nur zum Einreihen von Nachrichten)
# und von den Worker-Tests importiert. Auf Modulebene zoege jeder dieser
# Importe den kompletten FastAPI-Zweig aus services/api mit - und ein
# Worker-Image ohne den oben beschriebenen Spiegel-COPY koennte dann nicht
# einmal mehr Importe oder Klassifikationslaeufe verarbeiten, statt nur den
# Vertragsabgleich zu verlieren.


async def list_reconcilable_household_ids(session: AsyncSession) -> list[UUID]:
    """Haushalte mit mindestens einem laufenden oder gekuendigten Vertrag."""
    from app.services.contract_matching_service import list_reconcilable_households

    household_ids: list[UUID] = await list_reconcilable_households(session)
    return household_ids


async def reconcile_contracts_for_household(
    session: AsyncSession, household_id: UUID, *, today: date | None = None
) -> None:
    """Gleicht alle Vertraege eines Haushalts gegen die Buchungen ab und
    schreibt anschliessend die faelligen Fristerinnerungen.

    Der Fristlauf gehoert hierher und nicht nur an `POST /v1/contracts/rescan`:
    `notify_contract_alerts` (08:30) liest `contract_events` mit
    kind='term_ending', und ohne diesen Aufruf entstuende so ein Ereignis
    ausschliesslich dann, wenn jemand von Hand "Abgleich starten" drueckt. Die
    Kuendigungsfrist verstriche also genau bei den Nutzern unbemerkt, die sich
    auf die Erinnerung verlassen.

    Reihenfolge wie im Rescan-Endpunkt: erst der Abgleich, dann der Fristlauf -
    ein im selben Lauf beendeter Vertrag soll keine Erinnerung mehr bekommen.

    Committet nicht - die Transaktionsklammer liegt im Orchestrator, damit der
    naechtliche Lauf je Haushalt einzeln committen kann.
    """
    from app.services.contract_matching_service import reconcile_household
    from app.services.contract_service import scan_term_deadlines

    result = await reconcile_household(session, household_id, today=today)
    term_ending_count = await scan_term_deadlines(
        session, household_id, today=today or date.today()
    )
    logger.info(
        "Vertragsabgleich (household_id=%s): %s Vertraege geprueft, %s Treffer, "
        "%s Preisaenderung(en), %s Fehlperiode(n), %s wieder aufgenommen, "
        "%s beendet, %s Ratenkauf/Ratenkaeufe abgeschlossen, "
        "%s Fristerinnerung(en).",
        household_id,
        result.contracts_examined,
        result.matched_count,
        result.price_change_count,
        result.missing_count,
        result.resumed_count,
        result.ended_count,
        result.installments_completed_count,
        term_ending_count,
    )
