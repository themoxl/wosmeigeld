"""Integrationstest fuer den kompletten Import-Ablauf gegen ein echtes
lokales Supabase-Postgres (kein Mock). Deckt das Phase-3-Abnahmekriterium
ab: "vorhandene anonymisierte Banking-Dateien werden ohne Doppelzaehlung
importiert" und "Wiederholungsimport erzeugt keine zweite Buchung"
(Architekturplan Abschnitt 17)."""

import base64
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from worker.db import get_session_factory
from worker.imports.orchestrator import process_import_run

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "packages" / "test-fixtures" / "imports"


async def _create_household_and_account() -> tuple[str, str]:
    household_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text("insert into households (id, name) values (:id, 'Import-Test-Haushalt')"),
            {"id": household_id},
        )
        await session.execute(
            text(
                "insert into accounts (id, household_id, name, account_type) "
                "values (:id, :household_id, 'Testkonto', 'checking')"
            ),
            {"id": account_id, "household_id": household_id},
        )
    return household_id, account_id


async def _create_import_run(household_id: str, account_id: str, filename: str) -> str:
    import_run_id = str(uuid.uuid4())
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "insert into import_runs "
                "(id, household_id, account_id, filename, file_hash, file_size_bytes) "
                "values (:id, :household_id, :account_id, :filename, 'testhash', 100)"
            ),
            {
                "id": import_run_id,
                "household_id": household_id,
                "account_id": account_id,
                "filename": filename,
            },
        )
    return import_run_id


async def _fetch_import_run(import_run_id: str) -> dict:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "select status, total_rows, new_count, duplicate_count, "
                    "transfer_candidate_count, error_message "
                    "from import_runs where id = :id"
                ),
                {"id": import_run_id},
            )
        ).one()
        return dict(row._mapping)


async def _fetch_import_rows(import_run_id: str) -> list[dict]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            text(
                "select row_number, status, amount_minor, external_reference, fingerprint "
                "from import_rows where import_run_id = :id order by row_number"
            ),
            {"id": import_run_id},
        )
        return [dict(row._mapping) for row in result]


@pytest.mark.asyncio
async def test_first_import_stages_all_rows_as_new() -> None:
    household_id, account_id = await _create_household_and_account()
    file_bytes = (FIXTURES_DIR / "mt940_sample.mta").read_bytes()
    file_b64 = base64.b64encode(file_bytes).decode("ascii")

    import_run_id = await _create_import_run(household_id, account_id, "mt940_sample.mta")
    await process_import_run(import_run_id, "mt940_sample.mta", file_b64)

    run = await _fetch_import_run(import_run_id)
    assert run["status"] == "ready"
    assert run["total_rows"] == 2
    assert run["new_count"] == 2
    assert run["duplicate_count"] == 0

    rows = await _fetch_import_rows(import_run_id)
    assert [r["status"] for r in rows] == ["new", "new"]


@pytest.mark.asyncio
async def test_reimport_of_same_file_is_fully_detected_as_duplicate() -> None:
    household_id, account_id = await _create_household_and_account()
    file_bytes = (FIXTURES_DIR / "mt940_sample.mta").read_bytes()
    file_b64 = base64.b64encode(file_bytes).decode("ascii")

    # Simuliert einen bereits VORHER committeten Import: zwei echte
    # Buchungen mit denselben externen Referenzen wie in der Fixture.
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        for external_reference, amount_minor, booked_date in (
            ("SYNTH0001", -4250, "2026-01-05"),
            ("SYNTH0002", 250000, "2026-01-06"),
        ):
            await session.execute(
                text(
                    "insert into transactions "
                    "(household_id, account_id, booked_date, amount_minor, currency, "
                    "external_reference, source) "
                    "values (:household_id, :account_id, :booked_date, :amount_minor, 'EUR', "
                    ":external_reference, 'import')"
                ),
                {
                    "household_id": household_id,
                    "account_id": account_id,
                    "booked_date": booked_date,
                    "amount_minor": amount_minor,
                    "external_reference": external_reference,
                },
            )

    import_run_id = await _create_import_run(household_id, account_id, "mt940_sample.mta")
    await process_import_run(import_run_id, "mt940_sample.mta", file_b64)

    run = await _fetch_import_run(import_run_id)
    assert run["status"] == "ready"
    assert run["new_count"] == 0
    assert run["duplicate_count"] == 2

    rows = await _fetch_import_rows(import_run_id)
    assert [r["status"] for r in rows] == ["duplicate_exact", "duplicate_exact"]


@pytest.mark.asyncio
async def test_unrecognizable_file_marks_run_as_failed() -> None:
    household_id, account_id = await _create_household_and_account()
    file_b64 = base64.b64encode(b"dies ist weder CSV noch MT940 noch CAMT").decode("ascii")

    import_run_id = await _create_import_run(household_id, account_id, "mystery.dat")
    await process_import_run(import_run_id, "mystery.dat", file_b64)

    run = await _fetch_import_run(import_run_id)
    assert run["status"] == "failed"
    assert run["error_message"]
