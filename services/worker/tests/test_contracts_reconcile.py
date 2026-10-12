"""Der Worker muss den Vertragsabgleich ueberhaupt starten koennen - und dabei
auch die Fristerinnerungen schreiben.



Der Motor selbst liegt in `services/api/app/services/contract_matching_service.py`
und wird von `worker/contracts/reconcile.py` per `sys.path` eingebunden. Fehlt
im Worker-Umfeld eine Abhaengigkeit dieser Modulkette (sie endet bei
`from fastapi import HTTPException`) oder stimmt die Verzeichnistiefe nicht,
brechen `reconcile_contracts` (nach jedem Import-Commit) und
`reconcile_all_contracts` (naechtlich 03:00) bei JEDER Ausfuehrung mit
`ModuleNotFoundError` ab - lautlos, denn der naechtliche Lauf antwortet
niemandem. `contract_occurrences`, `contract_amount_history` und
`contract_events` blieben dann ausserhalb des synchronen
`POST /v1/contracts/rescan` fuer immer leer.

Der Verpackungstest ist deshalb bewusst nur ein Import: er prueft die
Verpackung, nicht die Bewertungsformel (die steht in
services/api/tests/test_contract_matching.py).

Der zweite Schwerpunkt ist der Fristlauf. `notify_contract_alerts` (08:30)
liest `contract_events` mit kind='term_ending' - geschrieben werden diese
Zeilen aber nur von `contract_service.scan_term_deadlines`. Laeuft der nicht im
naechtlichen Abgleich mit, entsteht ausserhalb des manuellen
`POST /v1/contracts/rescan` nie ein solches Ereignis, und die Erinnerung an die
Kuendigungsfrist feuert im Automatikbetrieb ueberhaupt nicht.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from worker.contracts.reconcile import (
    list_reconcilable_household_ids,
    reconcile_contracts_for_household,
)
from worker.db import get_session_factory


def test_the_matching_engine_is_importable_from_the_worker() -> None:
    from app.services.contract_matching_service import (  # noqa: PLC0415
        reconcile_household,
    )

    assert callable(reconcile_household)


async def test_the_reconcile_actor_path_runs_against_the_database() -> None:
    """Ein Haushalt ohne Vertraege ist der billigste vollstaendige Durchlauf:
    er beweist, dass Pfad, Abhaengigkeiten und SQL zusammenpassen."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        household_ids = await list_reconcilable_household_ids(session)
        assert isinstance(household_ids, list)

    async with session_factory() as session:
        await reconcile_contracts_for_household(session, uuid.uuid4())
        await session.rollback()


async def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row._mapping) for row in result]


async def test_the_nightly_run_writes_the_term_ending_events() -> None:
    """Regressionstest: der naechtliche Lauf rief `scan_term_deadlines` nie auf.

    Ein Vertrag mit zwoelf Monaten Mindestlaufzeit und dreissig Tagen
    Kuendigungsfrist steht zehn Tage vor seinem letzten Kuendigungstermin. Genau
    dafuer gibt es den Push `contract_term_ending` - er feuerte aber nur, wenn
    jemand von Hand "Abgleich starten" drueckte.
    """
    household_id = str(uuid.uuid4())
    contract_id = str(uuid.uuid4())
    today = date.today()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text("insert into households (id, name) values (:id, 'Frist-Testhaushalt')"),
            {"id": household_id},
        )
        await session.execute(
            text(
                "insert into contracts (id, household_id, name, amount_minor, "
                "next_due_date, status, starts_on, min_term_months, "
                "cancellation_period_days) values (:id, :household_id, 'Fitness First', "
                "4900, :next_due, 'active', :starts_on, 12, 30)"
            ),
            {
                "id": contract_id,
                "household_id": household_id,
                "next_due": today + timedelta(days=5),
                "starts_on": today - timedelta(days=324),
            },
        )

    async with session_factory() as session, session.begin():
        await reconcile_contracts_for_household(session, uuid.UUID(household_id))

    events = await _rows(
        "select kind, payload from contract_events where contract_id = :id",
        id=contract_id,
    )
    assert [event["kind"] for event in events] == ["term_ending"]
    assert events[0]["payload"]["stage"] == "30"

    # Zweiter Lauf am selben Tag: der Unique-Index haelt die Erinnerung
    # dedupliziert, sonst kaeme sie jede Nacht erneut.
    async with session_factory() as session, session.begin():
        await reconcile_contracts_for_household(session, uuid.UUID(household_id))
    assert len(await _rows(
        "select kind from contract_events where contract_id = :id", id=contract_id
    )) == 1
