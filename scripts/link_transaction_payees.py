"""Nachtraegliche Empfaengerverknuepfung fuer den Buchungsbestand.

docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 19.2. Import und Handanlage
verknuepfen Buchungen inzwischen selbst mit `payees`; der bereits vorhandene
Bestand blieb dabei liegen. Dieses Skript traegt die Verknuepfung nach - die
Fachlogik steckt vollstaendig in `app.services.payee_link_service`, hier steht
nur die Kommandozeile darum.

Standardmaessig ein Dry-Run: es wird nur GEMELDET, was der Lauf taete. Nichts
wird geschrieben, bis `--apply` ausdruecklich gesetzt ist. Jeder Haushalt
laeuft in einer eigenen Transaktion, der Lauf ist idempotent (verknuepft wird
nur, wo `payee_id` leer ist) und ueberschreibt niemals eine bestehende
Zuordnung.

Aufruf (PowerShell)
--------------------
    # Vorschau fuer einen Haushalt
    services\\api\\.venv\\Scripts\\python.exe scripts\\link_transaction_payees.py `
        --household-id <uuid>

    # Tatsaechlich schreiben
    services\\api\\.venv\\Scripts\\python.exe scripts\\link_transaction_payees.py `
        --household-id <uuid> --apply

    # Alle Haushalte mit unverknuepften Buchungen und mindestens einem Empfaenger
    services\\api\\.venv\\Scripts\\python.exe scripts\\link_transaction_payees.py `
        --all-households --apply

Verbindet direkt per SQLAlchemy-Engine der API (app.core.db), ohne
RLS-Kontext - wie der Worker filtert jede Abfrage in `payee_link_service`
deshalb explizit nach household_id.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

if sys.platform == "win32":
    # Wie in app/main.py und scripts/repair_legacy_contracts.py: psycopg
    # verweigert unter dem Windows-Standardloop den Dienst ("Psycopg cannot use
    # the 'ProactorEventLoop' to run in async mode"). Dieses Skript erzeugt
    # seinen Loop selbst per asyncio.run() und importiert app.main nicht - ohne
    # diese Zeile bricht schon der Dry-Run ab, bevor die erste Abfrage laeuft.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# app.* lebt in services/api, dieses Skript in scripts/ - derselbe
# sys.path-Kniff wie in scripts/repair_legacy_contracts.py.
api_dir = Path(__file__).resolve().parent.parent / "services" / "api"
if str(api_dir) not in sys.path:
    sys.path.insert(0, str(api_dir))

from app.core.db import get_session_factory  # noqa: E402
from app.services.payee_link_service import (  # noqa: E402
    REASON_TEXTS,
    PayeeLinkPlan,
    analyze_household,
    apply_household,
    list_linkable_households,
)

# Wie viele Empfaenger- bzw. Grundzeilen die Ausgabe hoechstens auflistet. Die
# Zaehler oben bleiben davon unberuehrt und sind immer vollstaendig.
_DETAIL_LIMIT = 25


def _print_plan(household_id: UUID, plan: PayeeLinkPlan, *, applied: bool) -> None:
    verb = "Angewendet" if applied else "Vorschau"
    print(f"\n=== Haushalt {household_id} ({verb}) ===")
    print(f"  transactions_scanned: {plan.transactions_scanned}")
    print(f"  already_linked:       {plan.already_linked}")
    print(f"  linkable:             {plan.linkable}")
    print(f"  ambiguous:            {plan.ambiguous}")
    print(f"  unchanged:            {plan.unchanged}")
    if applied:
        print(f"  linked:               {plan.linked}")

    if plan.link_groups:
        print("  Verknuepfbar je Empfaenger:")
        for group in plan.link_groups[:_DETAIL_LIMIT]:
            print(
                f"    - {group.payee_name} ({group.payee_id}): "
                f"{group.transactions} Buchungen, {REASON_TEXTS[group.reason]}"
            )
        rest = len(plan.link_groups) - _DETAIL_LIMIT
        if rest > 0:
            print(f"    ... und {rest} weitere Empfaenger")

    if plan.unchanged_groups:
        print("  Unveraendert (Gruende):")
        for item in plan.unchanged_groups[:_DETAIL_LIMIT]:
            line = f"    - {item.describe()}: {item.transactions} Buchungen"
            if item.candidates:
                joined = ", ".join(str(candidate) for candidate in item.candidates)
                line += f" [Kandidaten: {joined}]"
            print(line)
        rest = len(plan.unchanged_groups) - _DETAIL_LIMIT
        if rest > 0:
            print(f"    ... und {rest} weitere Varianten")


async def _run_household(household_id: UUID, *, apply: bool) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        if apply:
            plan = await apply_household(session, household_id)
            await session.commit()
        else:
            plan = await analyze_household(session, household_id)
        _print_plan(household_id, plan, applied=apply)


async def _list_households() -> list[UUID]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await list_linkable_households(session)


async def _main(args: argparse.Namespace) -> None:
    if args.all_households:
        household_ids = await _list_households()
        if not household_ids:
            print("Kein Haushalt mit unverknuepften Buchungen gefunden.")
            return
        print(f"{len(household_ids)} Haushalt(e) mit unverknuepften Buchungen gefunden.")
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
        help="Alle Haushalte mit unverknuepften Buchungen und mindestens einem Empfaenger",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Tatsaechlich schreiben. Ohne dieses Flag laeuft nur eine Vorschau (Standard).",
    )
    # Nimmt den im Plan genannten Schalter ausdruecklich entgegen, obwohl er der
    # Standard ist: wer ihn aus Vorsicht hinschreibt, soll keinen Fehler
    # bekommen. Zusammen mit --apply ist er ein Widerspruch und wird abgelehnt.
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur eine Vorschau erzeugen (Standardverhalten, hier nur zur Verdeutlichung).",
    )
    args = parser.parse_args()
    if args.dry_run and args.apply:
        parser.error("--dry-run und --apply schliessen sich aus.")
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
