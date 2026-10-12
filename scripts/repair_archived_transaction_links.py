"""Loest Verweise, die noch auf laengst archivierte Buchungen zeigen.

Buchungen werden nie geloescht, sondern archiviert (`transactions.is_archived`).
Bis zur Einfuehrung von app/services/transaction_detach_service.py raeumte der
Loeschpfad dabei nur Umbuchungspaare und Einkaufsverknuepfungen ab. Alles andere
zeigte weiter:

* eine Vertragsfaelligkeit blieb auf 'matched' und behauptete eine Zahlung,
  deren Beleg nicht mehr im Bestand steht - die Vertragsseite nannte damit eine
  zu hohe Summe unter "Bisher gezahlt",
* eine Erstattungszuordnung zaehlte weiter in `reimbursed_minor`, der Vorgang
  galt als abgeschlossen,
* eine Versicherungszahlung zaehlte weiter in `settled_minor`, der Schaden galt
  als reguliert,
* eine noch offene Importzeile warnte weiter vor einer Dublette zu einer
  Buchung, die niemand mehr findet.

Der neue Code verhindert das kuenftig. Fuer Bestaende, in denen schon geloescht
wurde, holt dieses Skript es einmalig nach - es fuehrt dieselbe Aufraeumroutine
ueber alle bereits archivierten Buchungen aus.

    python scripts/repair_archived_transaction_links.py
    python scripts/repair_archived_transaction_links.py --apply
    python scripts/repair_archived_transaction_links.py --apply --household <uuid>

Ohne --apply passiert nichts; das Skript zeigt nur, was es aendern wuerde.
Betroffene Vertraege und Vorgaenge werden anschliessend neu gerechnet - fuer die
Vertraege heisst das: `next_due_date` faellt auf die frueheste wieder offene
Faelligkeit zurueck, damit der naechtliche Abgleich sie erneut aufgreift.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.sql import text  # noqa: E402

from app.services.transaction_detach_service import (  # noqa: E402
    DETACH_LABELS,
    detach_transactions,
)

DEFAULT_DSN = "postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres"

# Eine Zeile je Haushalt: welche archivierten Buchungen haengen noch irgendwo
# dran? Bewusst dieselbe Aufzaehlung wie im Dienst - waere sie hier kuerzer,
# liesse das Skript genau die Faelle stehen, die es beheben soll.
_BETROFFENE_SQL = """
select distinct t.household_id, t.id
  from transactions t
 where t.is_archived
   and (
     exists (select 1 from contract_occurrences o where o.transaction_id = t.id)
     or exists (select 1 from contract_amount_history h
                 where h.source_transaction_id = t.id)
     or exists (select 1 from contract_assignment_overrides a
                 where a.transaction_id = t.id)
     or exists (select 1 from health_transaction_allocations ha
                 where ha.transaction_id = t.id)
     or exists (select 1 from insurance_claim_payments p
                 where p.transaction_id = t.id)
     or exists (select 1 from shopping_purchase_links s
                 where s.transaction_id = t.id)
     or exists (select 1 from transfer_links l
                 where l.outgoing_transaction_id = t.id
                    or l.incoming_transaction_id = t.id)
     or exists (select 1 from import_rows r
                 where r.duplicate_of_transaction_id = t.id
                   and r.status in ('duplicate_exact', 'duplicate_possible',
                                    'transfer_candidate'))
   )
"""


def _dsn() -> str:
    roh = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    if roh.startswith("postgresql://"):
        return roh.replace("postgresql://", "postgresql+psycopg://", 1)
    return roh


async def _lauf(apply_changes: bool, household: UUID | None) -> int:
    engine = create_async_engine(_dsn())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        abfrage = _BETROFFENE_SQL
        params: dict[str, object] = {}
        if household is not None:
            abfrage += " and t.household_id = :household_id"
            params["household_id"] = str(household)
        zeilen = (await session.execute(text(abfrage), params)).all()

        if not zeilen:
            print("Nichts zu tun - kein Verweis zeigt auf eine archivierte Buchung.")
            await engine.dispose()
            return 0

        nach_haushalt: dict[str, list[str]] = {}
        for zeile in zeilen:
            nach_haushalt.setdefault(str(zeile.household_id), []).append(str(zeile.id))

        print(
            f"{len(zeilen)} archivierte Buchungen in {len(nach_haushalt)} Haushalt(en) "
            "haengen noch irgendwo dran."
        )

        if not apply_changes:
            print("\nProbelauf - es wurde nichts geaendert. Mit --apply ausfuehren.")
            await engine.dispose()
            return 0

        gesamt: dict[str, int] = {}
        for household_id, tx_ids in sorted(nach_haushalt.items()):
            # RLS greift hier nicht: das Skript verbindet sich als postgres
            # (BYPASSRLS), deshalb muss die Haushaltsgrenze in jeder Abfrage
            # selbst stehen - genau das tut detach_transactions.
            geloest = await detach_transactions(session, UUID(household_id), tx_ids)
            for schluessel, anzahl in geloest.items():
                gesamt[schluessel] = gesamt.get(schluessel, 0) + anzahl
            print(f"  {household_id}: {len(tx_ids)} Buchungen, {sum(geloest.values())} Verweise")
        await session.commit()

        print("\nGeloest:")
        for schluessel, anzahl in sorted(gesamt.items()):
            print(f"  {anzahl:6d}  {DETACH_LABELS.get(schluessel, schluessel)}")
        print(
            "\nDer naechtliche Vertragsabgleich (worker.actors.reconcile_all_contracts) "
            "greift die wieder offenen Faelligkeiten auf. Wer nicht warten will, "
            "startet ihn ueber 'Jetzt abgleichen' auf der Vertragsseite."
        )

    await engine.dispose()
    return 0


def main() -> int:
    if sys.platform == "win32":
        # psycopg kann die Windows-Standardschleife (ProactorEventLoop) nicht
        # asynchron bedienen und bricht sonst schon beim Verbinden ab.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Aenderungen wirklich schreiben"
    )
    parser.add_argument(
        "--household", type=UUID, default=None, help="Nur diesen Haushalt bearbeiten"
    )
    args = parser.parse_args()
    return asyncio.run(_lauf(args.apply, args.household))


if __name__ == "__main__":
    raise SystemExit(main())
