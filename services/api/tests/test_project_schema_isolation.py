"""P0-Abnahme (Plan 16, P0): Schema-/RLS-Isolationstests fuer das neue
Vorhaben-Datenmodell. Es gibt noch keine API-Routen (die kommen erst ab P1);
deshalb wird direkt gegen die Datenbank getestet -- mit einer psycopg-
Superuser-Verbindung fuer den Testaufbau (RLS umgangen) und ueber die
Async-Engine der App fuer die eigentliche RLS-/Rollenpruefung, genau wie
`test_household_isolation.py` es fuer Phase 1 vorgemacht hat.
"""

import json
import os
import uuid
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app  # noqa: F401  -- setzt beim Import die Windows-Event-Loop-Policy


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def _run_sql(statement: str, params: dict[str, Any] | None = None) -> None:
    with psycopg.connect(_dsn(), autocommit=True) as connection:
        connection.execute(statement, params or {})


def _new_household(name: str) -> UUID:
    household_id = uuid.uuid4()
    _run_sql(
        "insert into households (id, name, base_currency) values (%(id)s, %(name)s, 'EUR')",
        {"id": str(household_id), "name": name},
    )
    return household_id


def _new_major_project(household_id: UUID, name: str) -> UUID:
    project_id = uuid.uuid4()
    _run_sql(
        "insert into major_projects (id, household_id, name, project_type) "
        "values (%(id)s, %(household_id)s, %(name)s, 'other')",
        {"id": str(project_id), "household_id": str(household_id), "name": name},
    )
    return project_id


@pytest.fixture()
def two_households() -> tuple[UUID, UUID]:
    return _new_household("Vorhaben-Testhaushalt A"), _new_household("Vorhaben-Testhaushalt B")


# ---------------------------------------------------------------------------
# Zusammengesetzte Fremdschluessel
# ---------------------------------------------------------------------------


def test_scenario_composite_fk_rejects_foreign_household_project(
    two_households: tuple[UUID, UUID],
) -> None:
    household_a, household_b = two_households
    project_a = _new_major_project(household_a, "Hausbau A")

    with pytest.raises(ForeignKeyViolation):
        _run_sql(
            "insert into project_scenarios (id, household_id, project_id, name) "
            "values (%(id)s, %(household_id)s, %(project_id)s, 'Basis')",
            {
                "id": str(uuid.uuid4()),
                "household_id": str(household_b),
                "project_id": str(project_a),
            },
        )


def test_result_asset_id_composite_fk_rejects_foreign_household_asset(
    two_households: tuple[UUID, UUID],
) -> None:
    household_a, household_b = two_households
    foreign_asset_id = uuid.uuid4()
    _run_sql(
        "insert into assets_and_debts (id, household_id, name, type) "
        "values (%(id)s, %(household_id)s, 'Fremdes Haus', 'asset')",
        {"id": str(foreign_asset_id), "household_id": str(household_b)},
    )

    with pytest.raises(ForeignKeyViolation):
        _run_sql(
            "insert into major_projects (id, household_id, name, project_type, result_asset_id) "
            "values (%(id)s, %(household_id)s, 'Hausbau A', 'other', %(asset_id)s)",
            {
                "id": str(uuid.uuid4()),
                "household_id": str(household_a),
                "asset_id": str(foreign_asset_id),
            },
        )


# ---------------------------------------------------------------------------
# Row Level Security
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rls_blocks_cross_household_read_on_major_projects(
    two_households: tuple[UUID, UUID],
) -> None:
    household_a, household_b = two_households
    _new_major_project(household_a, "Sichtbar nur fuer A")

    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = json.dumps({"sub": str(uuid.uuid4()), "role": "authenticated"})
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"), {"claims": claims}
        )
        # Haushaltskontext auf B, obwohl der Nutzer in keinem der beiden
        # Haushalte Mitglied ist -- reiner DB-Test der Policy selbst.
        await session.execute(
            text("SELECT set_config('app.household_id', :household_id, true)"),
            {"household_id": str(household_b)},
        )
        rows_as_b = (await session.execute(text("select * from major_projects"))).fetchall()

        await session.execute(
            text("SELECT set_config('app.household_id', :household_id, true)"),
            {"household_id": str(household_a)},
        )
        rows_as_a_without_membership = (
            await session.execute(text("select * from major_projects"))
        ).fetchall()

    assert rows_as_b == []
    # Ohne Mitgliedschaft in household_members bleibt auch der eigene
    # Haushaltskontext unsichtbar -- RLS prueft is_household_member(), nicht
    # nur household_id = current_household_id().
    assert rows_as_a_without_membership == []


@pytest.mark.asyncio
async def test_curated_funding_catalog_stays_unwritable_for_authenticated(
    two_households: tuple[UUID, UUID],
) -> None:
    """Der mitgelieferte Katalog (`household_id is null`) gehoert dem Code.

    Seit dem Foerderkatalog-Import duerfen Haushalte eigene Programme anlegen;
    die Tabellen sind deshalb nicht mehr pauschal schreibgeschuetzt. Was den
    kuratierten Bestand schuetzt, ist jetzt die Policy selbst:
    `household_id = current_household_id()` kann fuer NULL nie wahr werden.
    Genau das prueft dieser Test -- lesen ja, schreiben nein.
    """
    household_a, _ = two_households
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = json.dumps({"sub": str(uuid.uuid4()), "role": "authenticated"})
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"), {"claims": claims}
        )
        await session.execute(
            text("SELECT set_config('app.household_id', :household_id, true)"),
            {"household_id": str(household_a)},
        )

        # Lesen bleibt fuer jeden erlaubt: der kuratierte Katalog ist global.
        curated = (
            await session.execute(
                text("select count(*) as n from funding_programs where household_id is null")
            )
        ).scalar_one()
        assert curated > 0

        # Eine Zeile ohne household_id waere eine Erweiterung des kuratierten
        # Katalogs -- die Policy laesst sie nicht zu.
        with pytest.raises(Exception, match="row-level security|permission denied"):
            await session.execute(
                text(
                    "insert into funding_programs "
                    "(semantic_key, provider_key, provider_name, title, country_code, "
                    "scope, official_url) "
                    "values ('x', 'x', 'x', 'x', 'DE', 'national', 'https://example.test')"
                )
            )

    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = json.dumps({"sub": str(uuid.uuid4()), "role": "authenticated"})
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"), {"claims": claims}
        )
        await session.execute(
            text("SELECT set_config('app.household_id', :household_id, true)"),
            {"household_id": str(household_a)},
        )
        # Kuratierte Zeilen lassen sich auch nicht aendern oder loeschen: die
        # USING-Klausel der Schreib-Policy filtert sie vorher weg, das UPDATE
        # trifft null Zeilen statt heimlich zu wirken.
        updated = await session.execute(
            text("update funding_programs set title = 'gekapert' where household_id is null")
        )
        assert updated.rowcount == 0
        deleted = await session.execute(
            text("delete from funding_programs where household_id is null")
        )
        assert deleted.rowcount == 0


# ---------------------------------------------------------------------------
# CHECK-Constraints
# ---------------------------------------------------------------------------


def test_debt_plan_requires_at_least_one_owner(two_households: tuple[UUID, UUID]) -> None:
    household_a, _ = two_households
    with pytest.raises(CheckViolation):
        _run_sql(
            "insert into debt_plans (household_id, name, principal_limit_minor, starts_on, "
            "amortization_mode, fixed_payment_minor) "
            "values (%(household_id)s, 'Ohne Eigentuemer', 100000, current_date, "
            "'fixed_payment', 1000)",
            {"household_id": str(household_a)},
        )


def test_debt_plan_amortization_fields_are_mutually_exclusive(
    two_households: tuple[UUID, UUID],
) -> None:
    household_a, _ = two_households
    asset_id = uuid.uuid4()
    _run_sql(
        "insert into assets_and_debts (id, household_id, name, type) "
        "values (%(id)s, %(household_id)s, 'Testschuld', 'debt')",
        {"id": str(asset_id), "household_id": str(household_a)},
    )
    with pytest.raises(CheckViolation):
        _run_sql(
            "insert into debt_plans (household_id, asset_debt_id, name, principal_limit_minor, "
            "starts_on, amortization_mode, fixed_payment_minor, target_term_months) "
            "values (%(household_id)s, %(asset_id)s, 'Doppelt belegt', 100000, current_date, "
            "'fixed_payment', 1000, 120)",
            {"household_id": str(household_a), "asset_id": str(asset_id)},
        )


def test_only_one_selected_scenario_per_project(two_households: tuple[UUID, UUID]) -> None:
    household_a, _ = two_households
    project_id = _new_major_project(household_a, "Hausbau mit zwei Szenarien")
    _run_sql(
        "insert into project_scenarios (id, household_id, project_id, name, status) "
        "values (%(id)s, %(household_id)s, %(project_id)s, 'Basis', 'selected')",
        {"id": str(uuid.uuid4()), "household_id": str(household_a), "project_id": str(project_id)},
    )
    with pytest.raises(UniqueViolation):
        _run_sql(
            "insert into project_scenarios (id, household_id, project_id, name, status) "
            "values (%(id)s, %(household_id)s, %(project_id)s, 'Stress', 'selected')",
            {
                "id": str(uuid.uuid4()),
                "household_id": str(household_a),
                "project_id": str(project_id),
            },
        )


def test_cost_item_calculation_mode_fields_are_exclusive(
    two_households: tuple[UUID, UUID],
) -> None:
    household_a, _ = two_households
    project_id = _new_major_project(household_a, "Hausbau Kostenpruefung")
    scenario_id = uuid.uuid4()
    _run_sql(
        "insert into project_scenarios (id, household_id, project_id, name) "
        "values (%(id)s, %(household_id)s, %(project_id)s, 'Basis')",
        {"id": str(scenario_id), "household_id": str(household_a), "project_id": str(project_id)},
    )
    section_id = uuid.uuid4()
    _run_sql(
        "insert into project_cost_sections (id, household_id, scenario_id, name) "
        "values (%(id)s, %(household_id)s, %(scenario_id)s, 'Rohbau')",
        {"id": str(section_id), "household_id": str(household_a), "scenario_id": str(scenario_id)},
    )
    with pytest.raises(CheckViolation):
        _run_sql(
            "insert into project_cost_items "
            "(household_id, scenario_id, section_id, name, calculation_mode, "
            "estimate_manual_minor, quantity, unit_price_minor) "
            "values (%(household_id)s, %(scenario_id)s, %(section_id)s, 'Fehlerhafte Position', "
            "'fixed', 10000, 2, 500)",
            {
                "household_id": str(household_a),
                "scenario_id": str(scenario_id),
                "section_id": str(section_id),
            },
        )
