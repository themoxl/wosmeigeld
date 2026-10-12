"""Lasttest fuer ein einzelnes Vorhaben-Szenario (VORHABEN-GROSSINVESTITIONEN-
PLAN.md, P5-Abnahme): 2.000 Kostenpositionen, 20 Darlehen, 600 Monate
Cashflow-Horizont. Misst die serverseitige Rechenzeit von `calculate_scenario`
(Kostenaggregation) und `get_timeline` (600-Monats-Cashflow-Projektion) gegen
ein dokumentiertes Performanceziel.

Bewusste Entscheidung: laeuft ueber FastAPIs TestClient (In-Prozess, echte DB,
echte Geschaeftslogik, echtes Pydantic), nicht ueber einen separat gestarteten
uvicorn-Prozess samt echtem HTTP/TCP. Die Abnahme verlangt ausdruecklich eine
SERVERSEITIGE Zeitmessung ("berechnet serverseitig unter dem festgelegten
Performanceziel") - Netzwerklatenz eines echten HTTP-Roundtrips waere hier
zusaetzliches Rauschen, keine zusaetzliche Aussagekraft, und ein separater
Serverprozess braeuchte eine fragile Start-/Wartelogik in einem Einwegskript.

Performanceziel: dieselbe Groessenordnung wie die einzige andere numerische
Zielgroesse im Plan (Abschnitt 12.3: "Fehlergrenze > 2 s" fuer den
Foerderkatalog-Lauf) - hier festgelegt auf jeweils <= 2000 ms fuer
calculate_scenario und get_timeline. Kein anderer Wert steht im Plan; diese
Wahl ist eine bewusste, hier dokumentierte Festlegung, keine Vorgabe aus dem
Dokument selbst.

Aufruf:
    cd services/api
    .venv\\Scripts\\python.exe ..\\..\\scripts\\load_test_project.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres"
)
os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:55321")
os.environ.setdefault(
    "SUPABASE_JWT_SECRET", "super-secret-jwt-token-with-at-least-32-characters-long"
)

# Damit `import app...` funktioniert, egal von wo das Skript aufgerufen wird.
_API_ROOT = Path(__file__).resolve().parent.parent / "services" / "api"
sys.path.insert(0, str(_API_ROOT))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

COST_ITEM_COUNT = 2_000
DEBT_PLAN_COUNT = 20
CALCULATION_HORIZON_MONTHS = 600
COST_SECTION_COUNT = 10

PERFORMANCE_TARGET_MS = 2_000

_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9."
    "CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"
)

client = TestClient(app)


def _signup(email: str, password: str) -> str:
    response = httpx.post(
        f"{os.environ['SUPABASE_URL']}/auth/v1/signup",
        headers={"apikey": _SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _post(path: str, headers: dict[str, str], **kwargs: Any) -> dict[str, Any]:
    response = client.post(path, headers=headers, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} -> {response.status_code}: {response.text}")
    return dict(response.json()) if response.content else {}


def _put(path: str, headers: dict[str, str], **kwargs: Any) -> Any:
    response = client.put(path, headers=headers, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"PUT {path} -> {response.status_code}: {response.text}")
    return response.json() if response.content else None


def main() -> None:
    print(f"Lasttest: {COST_ITEM_COUNT} Kostenpositionen, {DEBT_PLAN_COUNT} Darlehen, "
          f"{CALCULATION_HORIZON_MONTHS} Monate Horizont, Ziel <= {PERFORMANCE_TARGET_MS} ms.\n")

    token = _signup(f"loadtest-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-load1")
    headers = {"Authorization": f"Bearer {token}"}
    household = _post("/v1/households", headers, json={"name": "Lasttest-Haushalt"})
    headers["X-Household-Id"] = household["id"]

    project = _post(
        "/v1/projects", headers, json={"name": "Lasttest-Vorhaben", "project_type": "home_build"}
    )
    scenario = client.get(f"/v1/projects/{project['id']}/scenarios", headers=headers).json()[0]

    scenario = client.patch(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}",
        headers={**headers, "If-Match": f'"{scenario["version"]}"'},
        json={"calculation_horizon_months": CALCULATION_HORIZON_MONTHS},
    ).json()

    _put(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/assumptions",
        headers,
        json={
            "starting_liquidity_minor": 50_000_00,
            "monthly_net_income_minor": 6_000_00,
            "monthly_living_costs_minor": 2_500_00,
            "monthly_current_housing_minor": 900_00,
            "monthly_existing_debt_service_minor": 0,
            "monthly_other_commitments_minor": 0,
        },
    )

    print(f"Lege {COST_SECTION_COUNT} Abschnitte und {COST_ITEM_COUNT} Kostenpositionen an...")
    section_ids: list[str] = []
    for i in range(COST_SECTION_COUNT):
        section = _post(
            f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-sections",
            headers,
            json={"name": f"Abschnitt {i + 1}", "cost_class": "direct"},
        )
        section_ids.append(section["id"])

    setup_start = time.perf_counter()
    for i in range(COST_ITEM_COUNT):
        section_id = section_ids[i % COST_SECTION_COUNT]
        _post(
            f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
            headers,
            json={
                "name": f"Position {i + 1}",
                "section_id": section_id,
                "calculation_mode": "fixed",
                "estimate_manual_minor": 100_00 + (i % 50) * 10_00,
            },
        )
        if (i + 1) % 200 == 0:
            elapsed = time.perf_counter() - setup_start
            print(f"  {i + 1}/{COST_ITEM_COUNT} Positionen angelegt ({elapsed:.1f}s)")

    print(f"\nLege {DEBT_PLAN_COUNT} Finanzierungsquellen mit Darlehen an...")
    account = _post(
        "/v1/accounts", headers, json={"name": "Lasttest-Konto", "account_type": "checking"}
    )
    for i in range(DEBT_PLAN_COUNT):
        source = _post(
            f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
            headers,
            json={
                "name": f"Darlehen {i + 1}",
                "source_type": "commercial_loan",
                "planned_amount_minor": 50_000_00,
            },
        )
        plan = _post(
            "/v1/debt-plans",
            headers,
            json={
                "project_financing_source_id": source["id"],
                "name": f"Darlehen {i + 1}",
                "principal_limit_minor": 50_000_00,
                "opening_balance_minor": 50_000_00,
                "starts_on": "2027-01-01",
                "amortization_mode": "fixed_payment",
                "fixed_payment_minor": 300_00,
            },
        )
        _put(
            f"/v1/debt-plans/{plan['id']}/rate-periods",
            headers,
            json={"periods": [{"starts_after_month": 0, "nominal_rate_bps_annual": 350}]},
        )

    # Konto als Eigenkapitalquelle, damit die Finanzierungsluecke nicht komplett offen ist.
    _post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers,
        json={
            "name": "Eigenkapital",
            "source_type": "cash_equity",
            "planned_amount_minor": 20_000_00,
            "account_id": account["id"],
        },
    )

    print("\nMessung: calculate_scenario (Kostenaggregation)...")
    calc_start = time.perf_counter()
    calc_response = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/calculate", headers=headers
    )
    calc_ms = (time.perf_counter() - calc_start) * 1000
    if calc_response.status_code != 200:
        raise RuntimeError(f"calculate_scenario fehlgeschlagen: {calc_response.text}")
    calc_body = calc_response.json()
    cash_cost_eur = calc_body["project_cash_cost_minor"] / 100
    print(f"  {calc_ms:.0f} ms - Projekt-Barkosten: {cash_cost_eur:,.2f} EUR")

    print("\nMessung: get_timeline (600-Monats-Cashflow-Projektion)...")
    timeline_start = time.perf_counter()
    timeline_response = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/timeline", headers=headers
    )
    timeline_ms = (time.perf_counter() - timeline_start) * 1000
    if timeline_response.status_code != 200:
        raise RuntimeError(f"get_timeline fehlgeschlagen: {timeline_response.text}")
    timeline_body = timeline_response.json()
    print(f"  {timeline_ms:.0f} ms - {len(timeline_body['months'])} Monate berechnet")

    print("\n--- Ergebnis ---")
    calc_ok = calc_ms <= PERFORMANCE_TARGET_MS
    timeline_ok = timeline_ms <= PERFORMANCE_TARGET_MS
    print(f"calculate_scenario: {calc_ms:.0f} ms {'OK' if calc_ok else 'UEBER ZIEL'} "
          f"(Ziel <= {PERFORMANCE_TARGET_MS} ms)")
    print(f"get_timeline:       {timeline_ms:.0f} ms {'OK' if timeline_ok else 'UEBER ZIEL'} "
          f"(Ziel <= {PERFORMANCE_TARGET_MS} ms)")
    print(f"\nProjekt-ID (zum manuellen Nachschauen, Haushalt wird nicht automatisch geloescht): "
          f"{project['id']}")

    if not (calc_ok and timeline_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
