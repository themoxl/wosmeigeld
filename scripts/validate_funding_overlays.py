"""Validiert Foerderprogramme gegen die Regel-DSL.

Zwei Quellen werden geprueft:

* die mitgelieferten Foerderpakete unter `app/data/funding_packages/` -- bevor
  sie ueberhaupt in eine Datenbank kommen;
* die Programmversionen in der Datenbank (kuratierter Katalog aus
  `0001_schema.sql` plus alles, was ein Haushalt importiert hat).

`match_program()` faengt `FundingMatchingError` intern ab und liefert dafuer
einen `unknown`-Treffer zurueck (richtig fuer den Live-Betrieb: ein einzelnes
kaputtes Programm darf den Foerdercheck der anderen nicht mitreissen) - das
wuerde einen Autorenfehler in `criteria_json`/`benefit_json` aber stumm als
"keine Angabe" statt als das melden, was es ist.

Die eigentliche Pruefung liegt deshalb in
`app/services/funding_rule_validation.py` und wird von hier UND von der
Import-Vorschau benutzt - eine Kopie der Logik gaebe es sonst zweimal.

Bewusste Einschraenkung (konsistent mit der P3-Entscheidung in
`funding_import_service.py`): `FoerderdatenbankXmlAdapter.fetch()` ruft
absichtlich `NotImplementedError`, weil der reale Open-Data-Feed nicht gegen
die offizielle Quelle verifiziert wurde. Es gibt deshalb keinen woechentlichen
Katalog-Sync-Worker; Programme kommen ueber Pakete oder Dateiimport herein.

Aufruf:
    cd services/api
    .venv\\Scripts\\python.exe ..\\..\\scripts\\validate_funding_overlays.py
    .venv\\Scripts\\python.exe ..\\..\\scripts\\validate_funding_overlays.py --packages-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    # psycopg (async) kann nicht mit Windows' ProactorEventLoop laufen (siehe
    # run_dev.py) - muss VOR jeder Event-Loop-Erzeugung gesetzt sein.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres"
)
os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:55321")
os.environ.setdefault(
    "SUPABASE_JWT_SECRET", "super-secret-jwt-token-with-at-least-32-characters-long"
)

_API_ROOT = Path(__file__).resolve().parent.parent / "services" / "api"
sys.path.insert(0, str(_API_ROOT))

from sqlalchemy import text  # noqa: E402

from app.core.db import get_engine  # noqa: E402
from app.data import funding_packages  # noqa: E402
from app.schemas.funding import FundingPackagePayload  # noqa: E402
from app.services.funding_rule_validation import (  # noqa: E402
    validate_freshness,
    validate_rule_json,
)

_PARTIAL_HINT = (
    "normalization_status='rules_partial' - unvollstaendig recherchierte Regeln, "
    "erwartungsgemaess bis zur vollstaendigen Recherche (kein Fehler, nur Hinweis)"
)


def _check_packages() -> int:
    """Pakete pruefen, bevor sie irgendwo eingespielt werden."""
    exit_code = 0
    total = 0
    for raw in funding_packages.list_packages():
        package = FundingPackagePayload.model_validate(raw)
        print(f"Paket '{package.id}' ({package.name}, {len(package.programs)} Programme)")
        for program in package.programs:
            total += 1
            problems = validate_rule_json(program.criteria_json, program.benefit_json)
            # Ein noch nicht abgelaufener Frischeablauf ist im Paket Pflicht: ein
            # Paket, das am Tag der Auslieferung schon veraltet ist, waere sinnlos.
            problems += validate_freshness(program.review_due_on)
            if program.normalization_status == "rules_partial":
                problems.append(_PARTIAL_HINT)

            hard = [p for p in problems if p != _PARTIAL_HINT]
            label = "FEHLER" if hard else ("HINWEIS" if problems else "OK")
            print(f"  [{label}] {program.semantic_key} (Stand {program.source_version})")
            for problem in problems:
                print(f"      - {problem}")
            if hard:
                exit_code = 1
    print(f"\n{total} Programme aus mitgelieferten Paketen geprueft.")
    return exit_code


async def _load_versions() -> list[dict[str, Any]]:
    async with get_engine().connect() as conn:
        result = await conn.execute(
            text(
                "select p.semantic_key, p.provider_name, p.title, p.household_id, "
                "v.id, v.source_version, v.review_due_at, v.normalization_status, "
                "v.criteria_json, v.benefit_json, v.source_content_hash "
                "from funding_program_versions v "
                "join funding_programs p on p.id = v.program_id "
                "order by p.semantic_key, v.source_version"
            )
        )
        return [dict(row._mapping) for row in result]


def _validate_row(row: dict[str, Any]) -> list[str]:
    problems = validate_rule_json(row["criteria_json"], row["benefit_json"])

    review_due = row["review_due_at"]
    if review_due is not None and hasattr(review_due, "date"):
        review_due = review_due.date()
    if review_due is not None and review_due < date.today():
        days_overdue = (date.today() - review_due).days
        # Im Bestand nur ein Hinweis: die Engine liefert dafuer schon 'unknown'.
        # Die Quelle sollte trotzdem erneut geprueft werden (Plan 6.5).
        problems.append(
            f"review_due_at ueberschritten seit {days_overdue} Tagen ({review_due}) - "
            "Match-Engine liefert dafuer bereits 'unknown', aber die Quelle sollte "
            "erneut gegen die offizielle Foerderstelle geprueft werden (Plan 6.5)"
        )

    if row["normalization_status"] == "rules_partial":
        problems.append(_PARTIAL_HINT)

    return problems


async def _check_database() -> int:
    rows = await _load_versions()
    print(f"\nPruefe {len(rows)} Foerderprogramm-Versionen in der Datenbank...\n")

    exit_code = 0
    for row in rows:
        problems = _validate_row(row)
        hard_failures = [
            p
            for p in problems
            if p != _PARTIAL_HINT and not p.startswith("review_due_at ueberschritten")
        ]
        status_label = "FEHLER" if hard_failures else ("HINWEIS" if problems else "OK")
        owner = "eigen" if row["household_id"] is not None else "kuratiert"
        print(
            f"[{status_label}] {row['semantic_key']} ({owner}, Version "
            f"{row['source_version']}, Hash {row['source_content_hash'][:12]}...)"
        )
        for problem in problems:
            print(f"    - {problem}")
        if hard_failures:
            exit_code = 1

    print(
        f"\n{'FEHLGESCHLAGEN' if exit_code else 'Alle Programme strukturell gueltig'} "
        f"({len(rows)} geprueft)."
    )
    return exit_code


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--packages-only",
        action="store_true",
        help="Nur die mitgelieferten Pakete pruefen, ohne Datenbankzugriff.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Ohne Wirkung; das Skript schreibt ohnehin nie."
    )
    args = parser.parse_args()

    exit_code = _check_packages()
    if args.packages_only:
        return exit_code
    return max(exit_code, await _check_database())


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
