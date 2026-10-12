"""Strukturpruefung der Foerderregel-DSL.

`match_program()` faengt `FundingMatchingError` intern ab und liefert dafuer
einen `unknown`-Treffer -- richtig im Betrieb, weil ein kaputtes Programm den
Foerdercheck der anderen nicht mitreissen darf. Genau dieses Verhalten wuerde
aber einen Tippfehler in `criteria_json` still als "keine Angabe" tarnen.

Dieses Modul ruft die Auswertungsfunktionen deshalb direkt auf und macht echte
Autorenfehler sichtbar. Es wird an zwei Stellen gebraucht:

* in der Import-Vorschau, damit ein Programm mit unbekanntem Regelknoten den
  Status `invalid` bekommt statt still importiert zu werden;
* in `scripts/validate_funding_overlays.py`, das den kuratierten Katalog und die
  mitgelieferten Pakete prueft, bevor sie in eine Datenbank kommen.

Fruher lag diese Logik nur im Skript; sie ist hierher gewandert, damit es nicht
zwei Kopien derselben Pruefung gibt.
"""

from datetime import date
from typing import Any

from app.services.funding_matching_service import (
    EligibilityFacts,
    FundingMatchingError,
    _calculate_benefit,
    _evaluate_node,
)

# Zwei Faktenprofile pruefen die DSL an ihren Raendern: leer deckt jeden Zweig
# ab, der bei fehlenden Angaben "missing" statt eines Absturzes liefern muss
# (Plan 6.3); vollstaendig deckt jeden Vergleichs-/Stufen-/Betragsknoten mit
# echten Werten ab.
EMPTY_FACTS = EligibilityFacts()


def _full_facts(as_of: date) -> EligibilityFacts:
    return EligibilityFacts(
        application_date=as_of,
        applicant_type="private_individual",
        household_income_year_minor=50_000_00,
        children_count=3,
        self_occupied=True,
        existing_homeowner=False,
        disability_severity_percent=0,
        manual_confirmations=frozenset({"placeholder"}),
        extra={},
    )


def validate_rule_json(
    criteria_json: dict[str, Any],
    benefit_json: dict[str, Any],
    *,
    as_of: date | None = None,
) -> list[str]:
    """Klartextbefunde zur Regel-DSL. Leere Liste = strukturell in Ordnung."""
    today = as_of or date.today()
    problems: list[str] = []
    for label, facts in (
        ("leeren Angaben", EMPTY_FACTS),
        ("vollständigen Angaben", _full_facts(today)),
    ):
        try:
            _evaluate_node(criteria_json, facts)
        except FundingMatchingError as exc:
            problems.append(f"criteria_json bei {label}: {exc}")
        except Exception as exc:  # noqa: BLE001 - jeder Fehler ist hier ein Befund
            problems.append(f"criteria_json bei {label}: unerwarteter Fehler {exc!r}")

        try:
            _calculate_benefit(benefit_json, facts, eligible_cost_basis_minor=100_000_00)
        except FundingMatchingError as exc:
            problems.append(f"benefit_json bei {label}: {exc}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"benefit_json bei {label}: unerwarteter Fehler {exc!r}")

    return problems


def validate_freshness(review_due_on: date, *, as_of: date | None = None) -> list[str]:
    """Ein bereits abgelaufener Frischeablauf ist beim Import ein harter Befund.

    Im Bestand ist er nur ein Hinweis (die Engine stuft solche Treffer ohnehin
    auf `unknown`); beim Einspielen waere es dagegen sinnlos, einen Stand
    aufzunehmen, der am Tag des Imports schon als veraltet gilt.
    """
    today = as_of or date.today()
    if review_due_on < today:
        days = (today - review_due_on).days
        return [
            f"review_due_on liegt {days} Tage in der Vergangenheit ({review_due_on.isoformat()}) — "
            "der Fördercheck würde Treffer sofort auf „unklar“ zurückstufen"
        ]
    return []
