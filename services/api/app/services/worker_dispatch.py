"""Zugang zu den dramatiq-Actors aus services/worker.

Die Actors leben in einer anderen Codebase (services/worker) und werden ueber
eine sys.path-Kopplung erreichbar gemacht. Diese Kopplung steht bewusst nur hier:
sie stand vorher in routes/imports.py mit dem Vermerk "hier der einzige
verbliebene API-seitige Verbraucher" - das stimmt nicht mehr, seit auch das
Archivieren einer Buchung einen Vertragsabgleich ausloesen kann.

Die Einreih-Hilfen schlucken Fehler bewusst: der Abgleich ist Nachbereitung. Ist
Redis nicht erreichbar, darf das nicht die bereits geschriebene fachliche
Aenderung mitreissen - der naechtliche Lauf (worker.actors.reconcile_all_contracts)
holt es nach.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

worker_dir = Path(__file__).resolve().parents[3] / "worker"
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

from worker.actors import process_import as process_import_actor  # noqa: E402
from worker.actors import reconcile_contracts as reconcile_contracts_actor  # noqa: E402

__all__ = [
    "enqueue_contract_reconcile",
    "process_import_actor",
    "reconcile_contracts_actor",
]


def enqueue_contract_reconcile(household_id: UUID, *, context: str) -> None:
    """Reiht den Vertragsabgleich ein - AUSSERHALB der Request-Transaktion.

    Als FastAPI-Hintergrundaufgabe aufzurufen, nicht direkt im Handler: FastAPI
    schliesst den Abhaengigkeits-Stack (und damit `session.begin()` in
    get_household_session, wo der Commit stattfindet) VOR den Hintergrundaufgaben.
    Direkt im Handler eingereiht koennte der Worker seine eigene Sitzung oeffnen,
    bevor der Commit geschrieben ist - er saehe den alten Stand und schriebe
    daraus falsche Faelligkeiten fest.
    """
    try:
        reconcile_contracts_actor.send(str(household_id))
    except Exception:
        logger.exception(
            "Vertragsabgleich konnte nicht eingereiht werden (%s, household_id=%s)",
            context,
            household_id,
        )
