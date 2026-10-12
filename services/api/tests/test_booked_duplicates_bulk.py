"""Bulk decisions must be atomic, preserve a survivor and batch database work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.review import BulkResolveBookedDuplicatesInput, ResolveBookedDuplicateInput
from app.services import booked_duplicate_service, transaction_detach_service
from tests.test_booked_duplicates import Result, booking


def choice(first=1, second=2, action="archive_second", **versions):
    return ResolveBookedDuplicateInput(
        first_transaction_id=UUID(int=first),
        second_transaction_id=UUID(int=second),
        first_version=versions.get("first_version", 1),
        second_version=versions.get("second_version", 1),
        action=action,
    )


@pytest.mark.parametrize("items", [[], [choice(), choice(2, 1)], [choice(1, 1)], [choice()] * 501])
def test_invalid_batch_selection(items):
    with pytest.raises(ValidationError):
        BulkResolveBookedDuplicatesInput(items=items)


async def test_169_pairs_use_one_lock_one_detach_and_one_archive(monkeypatch):
    payload = BulkResolveBookedDuplicatesInput(items=[choice(i, i + 1) for i in range(1, 338, 2)])
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                Result([booking(id=UUID(int=i)) for i in range(1, 339)]),
                Result(),
            ]
        )
    )
    detach = AsyncMock(return_value={"vertrag_faelligkeit": 3})
    monkeypatch.setattr(transaction_detach_service, "detach_transactions", detach)
    result, detached = await booked_duplicate_service.resolve_bulk(session, uuid4(), payload)
    assert result.model_dump() == {"processed_count": 169, "archived_count": 169, "kept_count": 0}
    assert detached == {"vertrag_faelligkeit": 3}
    assert session.execute.await_count == 2
    assert "order by t.id for update of t" in str(session.execute.call_args_list[0].args[0])
    detach.assert_awaited_once()
    assert len(detach.call_args.args[2]) == 169


async def test_triple_copy_is_archived_once_and_keeps_original(monkeypatch):
    payload = BulkResolveBookedDuplicatesInput(items=[choice(1, 2), choice(1, 3), choice(2, 3)])
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                Result([booking(id=UUID(int=i)) for i in range(1, 4)]),
                Result(),
            ]
        )
    )
    detach = AsyncMock(return_value={})
    monkeypatch.setattr(transaction_detach_service, "detach_transactions", detach)
    result, _ = await booked_duplicate_service.resolve_bulk(session, uuid4(), payload)
    assert result.archived_count == 2
    assert result.processed_count == 3
    assert detach.call_args.args[2] == [str(UUID(int=2)), str(UUID(int=3))]


@pytest.mark.parametrize(
    "items",
    [
        [choice(1, 2), choice(2, 3), choice(1, 3, "archive_first")],
        [choice(1, 2), choice(2, 3, "keep_both")],
    ],
)
async def test_conflicting_decisions_never_write(items):
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await booked_duplicate_service.resolve_bulk(
            session, uuid4(), BulkResolveBookedDuplicatesInput(items=items)
        )
    assert error.value.status_code == 409
    session.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "rows,status",
    [
        ([booking(), booking(id=UUID(int=2)), booking(id=UUID(int=3))], 404),
        ([booking(id=UUID(int=i), version=2 if i == 4 else 1) for i in range(1, 5)], 409),
        (
            [booking(id=UUID(int=i), amount_minor=-200 if i == 4 else -1999) for i in range(1, 5)],
            409,
        ),
    ],
)
async def test_one_invalid_pair_prevents_all_writes(monkeypatch, rows, status):
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(rows)))
    detach = AsyncMock()
    monkeypatch.setattr(transaction_detach_service, "detach_transactions", detach)
    with pytest.raises(HTTPException) as error:
        await booked_duplicate_service.resolve_bulk(
            session, uuid4(), BulkResolveBookedDuplicatesInput(items=[choice(), choice(3, 4)])
        )
    assert error.value.status_code == status
    detach.assert_not_awaited()
    assert session.execute.await_count == 1


async def test_keep_both_inserts_all_decisions_in_one_statement(monkeypatch):
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                Result([booking(id=UUID(int=i)) for i in range(1, 5)]),
                Result(),
            ]
        )
    )
    detach = AsyncMock()
    monkeypatch.setattr(transaction_detach_service, "detach_transactions", detach)
    result, _ = await booked_duplicate_service.resolve_bulk(
        session,
        uuid4(),
        BulkResolveBookedDuplicatesInput(
            items=[choice(1, 2, "keep_both"), choice(3, 4, "keep_both")]
        ),
    )
    assert result.kept_count == 2
    assert result.archived_count == 0
    assert session.execute.await_count == 2
    assert "jsonb_to_recordset" in str(session.execute.call_args.args[0])
    detach.assert_not_awaited()
