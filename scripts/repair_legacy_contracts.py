"""Haushaltsweiter Reparaturlauf fuer Legacy-Vertraege (P5.1b).

docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 19.9. Reichert Vertraege an, die
aus `recurring_rules`/`subscriptions` uebernommen wurden und deshalb ohne
Vertragsschluessel und ohne Zeitraum dastehen (`contracts.first_seen_date is
null`) - die Fachlogik steckt vollstaendig in
`app.services.contract_legacy_repair_service`, dieses Skript ist nur die
Kommandozeile darum.

Standardmaessig ein Dry-Run: es wird nur GEMELDET, was der Lauf taete. Nichts
wird geschrieben, bis `--apply` ausdruecklich gesetzt ist. Jeder Haushalt
laeuft in einer eigenen Transaktion.

Aufruf (PowerShell)
--------------------
    # Vorschau fuer einen Haushalt
    services\\api\\.venv\\Scripts\\python.exe scripts\\repair_legacy_contracts.py `
        --household-id <uuid>

    # Tatsaechlich schreiben
    services\\api\\.venv\\Scripts\\python.exe scripts\\repair_legacy_contracts.py `
        --household-id <uuid> --apply

    # Alle Haushalte mit mindestens einem betroffenen Legacy-Vertrag
    services\\api\\.venv\\Scripts\\python.exe scripts\\repair_legacy_contracts.py `
        --all-households --apply

Verbindet direkt per SQLAlchemy-Engine der API (app.core.db), ohne
RLS-Kontext - wie der Worker filtert jede Abfrage in
contract_legacy_repair_service deshalb explizit nach household_id.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

if sys.platform == "win32":
    # Wie in app/main.py: psycopg verweigert unter dem Windows-Standardloop
    # ("Psycopg cannot use the 'ProactorEventLoop' to run in async mode") den
    # Dienst. Hier muss die Policy stehen, weil dieses Skript seinen Loop
    # selbst per asyncio.run() erzeugt und app.main gar nicht importiert -
    # ohne diese Zeile bricht schon der Dry-Run auf einem Entwicklungsrechner
    # mit Windows ab, bevor die erste Abfrage laeuft.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# app.* lebt in services/api, dieses Skript in scripts/ - derselbe
# sys.path-Kniff wie in services/api/app/api/v1/routes/imports.py fuer den
# umgekehrten Fall (API importiert worker.*).
api_dir = Path(__file__).resolve().parent.parent / "services" / "api"
if str(api_dir) not in sys.path:
    sys.path.insert(0, str(api_dir))

from app.core.db import get_session_factory  # noqa: E402
from app.services.contract_legacy_repair_service import (  # noqa: E402
    LegacyRepairPlan,
    analyze_household,
    apply_household,
)

_LIST_HOUSEHOLDS_SQL = """
select distinct household_id from contracts
where merged_into_id is null and first_seen_date is null
  and (legacy_recurring_rule_id is not null or legacy_subscription_id is not null)
order by household_id
"""


def _print_plan(household_id: UUID, plan: LegacyRepairPlan, *, applied: bool) -> None:
    verb = "Angewendet" if applied else "Vorschau"
    print(f"\n=== Haushalt {household_id} ({verb}) ===")
    print(f"  contracts_scanned:  {plan.contracts_scanned}")
    print(f"  contracts_enriched: {plan.contracts_enriched}")
    print(f"  keys_enriched:      {plan.keys_enriched}")
    print(f"  ambiguous:          {plan.ambiguous}")
    print(f"  skipped_manual:     {plan.skipped_manual}")

    if plan.enriched_contracts:
        print("  Angereicherte Vertraege:")
        for enriched in plan.enriched_contracts:
            key = (
                f"{enriched.contract_key!r} ({enriched.contract_key_source})"
                if enriched.contract_key
                else "keiner (nur Zeitraum)"
            )
            print(
                f"    - {enriched.name} ({enriched.contract_id}): Schluessel={key}, "
                f"Zeitraum={enriched.first_seen_date}..{enriched.last_seen_date}, "
                f"{enriched.occurrences_written} Faelligkeiten"
            )

    if plan.skipped_manual_contracts:
        # Abschnitt 19.9: das Ergebnis muss nennen, WELCHE Vertraege wegen
        # manueller Hoheit unangetastet blieben - eine blosse Zahl laesst den
        # Nutzer nicht nachpruefen, ob der Lauf das Richtige ausgelassen hat.
        print("  Uebersprungen (manuelle Hoheit):")
        for skipped in plan.skipped_manual_contracts:
            print(f"    - {skipped.name} ({skipped.contract_id}): {skipped.reason}")

    if plan.merge_suggestions:
        print("  Merge-Vorschlaege (nicht automatisch ausgefuehrt):")
        for suggestion in plan.merge_suggestions:
            sources = ", ".join(str(item) for item in suggestion.source_ids)
            print(f"    - Ziel {suggestion.target_id} <- Quellen [{sources}]")

    if plan.ambiguous_contracts:
        print("  Mehrdeutig (unveraendert):")
        for ambiguous in plan.ambiguous_contracts:
            print(f"    - {ambiguous.name} ({ambiguous.contract_id}): {ambiguous.reason}")


async def _run_household(household_id: UUID, *, apply: bool) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        if apply:
            plan = await apply_household(session, household_id)
            await session.commit()
        else:
            plan = await analyze_household(session, household_id)
        _print_plan(household_id, plan, applied=apply)


async def _list_households_with_legacy_contracts() -> list[UUID]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(text(_LIST_HOUSEHOLDS_SQL))
        return [UUID(str(row.household_id)) for row in result]


async def _main(args: argparse.Namespace) -> None:
    if args.all_households:
        household_ids = await _list_households_with_legacy_contracts()
        if not household_ids:
            print("Kein Haushalt mit reparaturbeduerftigen Legacy-Vertraegen gefunden.")
            return
        print(f"{len(household_ids)} Haushalt(e) mit Legacy-Vertraegen gefunden.")
    else:
        household_ids = [args.household_id]

    for household_id in household_ids:
        await _run_household(household_id, apply=args.apply)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--household-id", type=UUID, help="Ein einzelner Haushalt")
    target.add_argument(
        "--all-households",
        action="store_true",
        help="Alle Haushalte mit mindestens einem betroffenen Legacy-Vertrag",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Tatsaechlich schreiben. Ohne dieses Flag laeuft nur eine Vorschau (Standard).",
    )
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
