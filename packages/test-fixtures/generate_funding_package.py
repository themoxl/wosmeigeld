"""Erzeugt ein Beispiel-Foerderpaket mit bewusst gemischten Faellen.

Zum Ausprobieren der Import-Vorschau unter Förderungen → Importieren /
Exportieren → 📁 Importieren. Das Paket enthaelt absichtlich je einen Fall pro
Vorschaustatus, damit sichtbar wird, wie der Dialog damit umgeht:

* **neu** -- ein sauberes Programm, das es noch nicht gibt;
* **ungueltig (Regelknoten)** -- ein erfundener Betragstyp; die Vorschau muss ihn
  als `invalid` melden statt ihn stumm zu importieren;
* **ungueltig (Frischeablauf)** -- `review_due_on` in der Vergangenheit;
* **Konflikt** -- derselbe `semantic_key` wie ein mitgeliefertes Programm.

Ein zweiter Lauf mit `--updated` aendert den Foerderbetrag des ersten Programms,
damit sich der Weg "importieren -> aktualisieren -> alte Version bleibt stehen"
durchspielen laesst.

    python packages/test-fixtures/generate_funding_package.py
    python packages/test-fixtures/generate_funding_package.py --updated
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT = Path("Foerderpaket-Beispiel.json")


def _program(
    semantic_key: str,
    title: str,
    *,
    benefit: dict[str, Any],
    review_due_on: date,
    normalization_status: str = "rules_complete",
) -> dict[str, Any]:
    return {
        "semantic_key": semantic_key,
        "provider_key": "beispielbank",
        "provider_name": "Beispielbank",
        "program_code": None,
        "title": title,
        "country_code": "DE",
        "scope": "national",
        "region_codes": [],
        "official_url": "https://example.test/foerderung",
        "source_url": "https://example.test/foerderung",
        "source_version": f"beispiel-{date.today().isoformat()}",
        "valid_from": "2026-01-01",
        "valid_to": None,
        "review_due_on": review_due_on.isoformat(),
        "benefit_type": "loan" if benefit.get("type", "").startswith("loan") else "grant",
        "applicant_types": ["private_individual"],
        "project_types": ["home_build", "property_purchase"],
        "criteria_json": {
            "compare": {
                "field": "self_occupied",
                "op": "eq",
                "value": True,
                "label": "Selbstnutzung nach Erwerb oder Bau",
            }
        },
        "benefit_json": benefit,
        "loan_terms_json": (
            {
                "max_term_months": 300,
                "max_repayment_free_months": 24,
                "nominal_rate_bps_annual": 275,
            }
            if benefit.get("type", "").startswith("loan")
            else {}
        ),
        "combination_rules_json": {},
        "manual_checks_json": [],
        "license_name": "Beispieldaten, keine echte Foerderung",
        "attribution": "Testdaten",
        "normalization_status": normalization_status,
    }


def build_package(updated: bool) -> dict[str, Any]:
    today = date.today()
    fresh = today + timedelta(days=90)
    expired = today - timedelta(days=30)

    return {
        "format_version": "1.0",
        "id": "beispielpaket",
        "name": "Beispielpaket (Testdaten)",
        "description": "Erfundene Programme zum Ausprobieren der Import-Vorschau.",
        "icon": "🧪",
        "publisher": "Testdaten",
        "package_version": today.isoformat(),
        "researched_on": today.isoformat(),
        "programs": [
            _program(
                "beispiel_basisdarlehen",
                "Beispiel-Basisdarlehen",
                # Beim zweiten Lauf ein anderer Betrag -> die Vorschau meldet
                # `updated` und nennt "Foerderbetrag" als geaendertes Feld.
                benefit={
                    "type": "loan_fixed",
                    "amount_minor": 6_000_000 if updated else 5_000_000,
                },
                review_due_on=fresh,
            ),
            _program(
                "beispiel_kaputter_betragstyp",
                "Beispiel mit unbekanntem Betragstyp",
                # Es gibt keinen Typ `tilgungszuschuss` -- genau das soll die
                # Vorschau als `invalid` melden, statt es stumm zu schlucken.
                benefit={"type": "tilgungszuschuss", "rate_bps": 2000},
                review_due_on=fresh,
                normalization_status="rules_partial",
            ),
            _program(
                "beispiel_abgelaufen",
                "Beispiel mit abgelaufenem Frischeablauf",
                benefit={"type": "fixed_grant", "amount_minor": 250_000},
                review_due_on=expired,
            ),
            _program(
                # Kollidiert absichtlich mit dem kuratierten Katalog.
                "kfw_124_wohneigentumsprogramm",
                "Beispiel-Kollision mit einem mitgelieferten Programm",
                benefit={"type": "loan_fixed", "amount_minor": 9_900_000},
                review_due_on=fresh,
            ),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--updated",
        action="store_true",
        help="Zweiter Stand: anderer Foerderbetrag beim ersten Programm.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    package = build_package(args.updated)
    args.out.write_text(
        json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"Geschrieben: {args.out.resolve()}")
    print(f"{len(package['programs'])} Programme:")
    for program in package["programs"]:
        print(f"  - {program['semantic_key']} ({program['normalization_status']})")
    print(
        "\nErwartete Vorschau: 1x neu bzw. aktualisiert, 2x ungueltig "
        "(unbekannter Betragstyp, abgelaufener Frischeablauf), 1x Konflikt."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
