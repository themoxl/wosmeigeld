"""Regression coverage for duplicates that have already reached the ledger."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.schemas.review import ResolveBookedDuplicateInput
from app.services.booked_duplicate_service import (
    match_pair,
    scan_booked_duplicates,
    validate_pair,
)


def booking(**overrides):
    return {
        "id": UUID(int=1),
        "account_id": UUID(int=10),
        "booked_date": date(2026, 4, 2),
        "amount_minor": -1999,
        "currency": "EUR",
        "version": 1,
        "payee": "Musterkasse AG",
        "purpose": "Beitrag April",
        "external_reference": None,
        "fingerprint": None,
        "source": "import",
        "account_name": "Girokonto",
        "import_filename": "april.csv",
        **overrides,
    }


@pytest.mark.parametrize(
    "changed",
    [
        {"payee": None},
        {"payee": "MUSTERKASSE"},
        {"purpose": "Beitrag April Versicherung"},
        {"source": "manual"},
    ],
)
def test_detects_alternate_import_formats_and_manual_duplicates(changed):
    assert match_pair(booking(), booking(id=UUID(int=2), **changed)) == "duplicate_possible"


@pytest.mark.parametrize(
    "changed",
    [
        {"account_id": UUID(int=11)},
        {"currency": "USD"},
        {"booked_date": date(2026, 4, 3)},
        {"amount_minor": 1999},
        {"payee": "Anderer Empfänger", "purpose": "Andere Zahlung"},
    ],
)
def test_does_not_conflate_distinct_bookings(changed):
    assert match_pair(booking(), booking(id=UUID(int=2), **changed)) is None


class Result:
    def __init__(self, rows=(), count=0):
        self.rows, self.count = rows, count

    def scalar_one(self):
        return self.count

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


async def test_scan_is_read_only_idempotent_and_suppresses_reviewed_versions():
    rows = [booking(), booking(id=UUID(int=2)), booking(id=UUID(int=3), version=2)]
    dismissed = SimpleNamespace(
        first_transaction_id=UUID(int=1),
        second_transaction_id=UUID(int=2),
        first_version=1,
        second_version=1,
    )
    # The third row's previous version was reviewed; its changed version is shown again.
    old = SimpleNamespace(
        first_transaction_id=UUID(int=1),
        second_transaction_id=UUID(int=3),
        first_version=1,
        second_version=1,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                Result(count=3),
                Result(rows),
                Result([dismissed, old]),
                Result(count=3),
                Result(rows),
                Result([dismissed, old]),
            ]
        )
    )
    household_id = uuid4()
    first = await scan_booked_duplicates(session, household_id)
    second = await scan_booked_duplicates(session, household_id)
    assert first == second
    assert first.scanned_count == 3
    assert first.total_count == 2
    assert [(i.first.transaction_id.int, i.second.transaction_id.int) for i in first.items] == [
        (1, 3),
        (2, 3),
    ]
    assert all(
        str(call.args[0]).lstrip().startswith(("select", "with"))
        and call.args[1]["household_id"] == str(household_id)
        for call in session.execute.call_args_list
    )


async def test_scan_reports_full_count_beyond_display_limit():
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                Result(count=33),
                Result([booking(id=UUID(int=i)) for i in range(1, 34)]),
                Result(),
            ]
        )
    )
    result = await scan_booked_duplicates(session, uuid4())
    assert result.total_count == 528
    assert len(result.items) == 500


@pytest.mark.parametrize(
    "rows,status",
    [
        ([booking()], 404),
        ([booking(), booking(id=UUID(int=2), version=2)], 409),
        ([booking(), booking(id=UUID(int=2), amount_minor=-2999)], 409),
    ],
)
async def test_resolution_rejects_missing_changed_or_no_longer_matching_bookings(rows, status):
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(rows)))
    payload = ResolveBookedDuplicateInput(
        first_transaction_id=UUID(int=1),
        second_transaction_id=UUID(int=2),
        first_version=1,
        second_version=1,
        action="archive_second",
    )
    with pytest.raises(HTTPException) as error:
        await validate_pair(session, uuid4(), payload)
    assert error.value.status_code == status
    assert "for update of t" in str(session.execute.call_args.args[0])
