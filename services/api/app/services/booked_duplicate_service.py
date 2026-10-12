"""Find duplicates in the current ledger, including committed imports and manual entries."""

import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.review import (
    BookedDuplicateScanResult,
    BookedDuplicateTransaction,
    BulkResolveBookedDuplicatesInput,
    BulkResolveBookedDuplicatesResult,
    ResolveBookedDuplicateInput,
    ReviewItemBookedDuplicate,
)

# The API image includes services/worker, as for worker_dispatch. Load only the
# pure matcher here: importing actors would also initialize the message broker.
_worker_dir = str(Path(__file__).resolve().parents[3] / "worker")
if _worker_dir not in sys.path:
    sys.path.insert(0, _worker_dir)
from worker.imports.dedup import classify_row, compute_fingerprint  # noqa: E402

_COLUMNS = """
    t.id, t.account_id, t.booked_date, t.amount_minor, t.currency, t.version,
    coalesce(nullif(t.payee_label, ''), p.name) as payee,
    t.purpose_raw as purpose, t.external_reference, t.fingerprint,
    t.source, a.name as account_name, r.filename as import_filename, t.created_at
"""
_JOINS = """
    from transactions t
    join accounts a on a.id = t.account_id and a.household_id = t.household_id
    left join payees p on p.id = t.payee_id and p.household_id = t.household_id
    left join import_runs r on r.id = t.import_run_id and r.household_id = t.household_id
"""


def match_pair(first: dict[str, Any], second: dict[str, Any]) -> str | None:
    # Never compare transfers between accounts or different currencies/dates.
    if any(
        first[k] != second[k] for k in ("account_id", "booked_date", "amount_minor", "currency")
    ):
        return None
    candidate = {**second, "payee_raw": second["payee"]}
    fingerprint = compute_fingerprint(
        second["account_id"],
        second["booked_date"],
        second["amount_minor"],
        second["payee"],
        second["purpose"],
    )
    result = classify_row(candidate, fingerprint, str(second["account_id"]), [first], [])
    return result.status if result.status.startswith("duplicate_") else None


def _booking(row: dict[str, Any]) -> BookedDuplicateTransaction:
    return BookedDuplicateTransaction(
        transaction_id=row["id"],
        version=row["version"],
        currency=row["currency"],
        booked_date=row["booked_date"],
        amount_minor=row["amount_minor"],
        payee_label=row["payee"],
        purpose=row["purpose"],
        source=row["source"],
        account_name=row["account_name"],
        import_filename=row["import_filename"],
    )


async def scan_booked_duplicates(
    session: AsyncSession,
    household_id: UUID,
) -> BookedDuplicateScanResult:
    params = {"household_id": str(household_id)}
    scanned_count = int(
        (
            await session.execute(
                text(
                    "select count(*) from transactions where household_id = :household_id "
                    "and not is_archived"
                ),
                params,
            )
        ).scalar_one()
    )
    # Reduce comparisons to collision groups before loading ledger details.
    rows = (
        (
            await session.execute(
                text(
                    "with collisions as (select account_id, booked_date, amount_minor, currency "
                    "from transactions where household_id = :household_id and not is_archived "
                    "group by account_id, booked_date, amount_minor, currency having count(*) > 1) "
                    f"select {_COLUMNS} {_JOINS} "
                    "join collisions c on "
                    "(t.account_id, t.booked_date, t.amount_minor, t.currency) = "
                    "(c.account_id, c.booked_date, c.amount_minor, c.currency) "
                    "where t.household_id = :household_id and not t.is_archived "
                    "order by t.booked_date desc, t.created_at, t.id"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    dismissed = {
        (r.first_transaction_id, r.second_transaction_id, r.first_version, r.second_version)
        for r in (
            await session.execute(
                text(
                    "select first_transaction_id, second_transaction_id, "
                    "first_version, second_version "
                    "from transaction_duplicate_dismissals where household_id = :household_id"
                ),
                params,
            )
        )
    }
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            tuple(row[k] for k in ("account_id", "booked_date", "amount_minor", "currency"))
        ].append(dict(row))
    items: list[ReviewItemBookedDuplicate] = []
    total = 0
    for group in groups.values():
        for first, second in combinations(group, 2):
            low, high = sorted((first, second), key=lambda row: row["id"])
            if (low["id"], high["id"], low["version"], high["version"]) in dismissed:
                continue
            match = match_pair(first, second)
            if match is None:
                continue
            total += 1
            if len(items) < 500:
                items.append(
                    ReviewItemBookedDuplicate(
                        first=_booking(first),
                        second=_booking(second),
                        match=cast(Literal["duplicate_exact", "duplicate_possible"], match),
                    )
                )
    return BookedDuplicateScanResult(scanned_count=scanned_count, total_count=total, items=items)


async def validate_pair(
    session: AsyncSession,
    household_id: UUID,
    payload: ResolveBookedDuplicateInput,
) -> None:
    # Lock both rows in stable order, then check the exact versions displayed.
    rows = (
        (
            await session.execute(
                text(
                    f"select {_COLUMNS} {_JOINS} "
                    "where t.household_id = :household_id and not t.is_archived "
                    "and t.id in (:first, :second) order by t.id for update of t"
                ),
                {
                    "household_id": str(household_id),
                    "first": str(payload.first_transaction_id),
                    "second": str(payload.second_transaction_id),
                },
            )
        )
        .mappings()
        .all()
    )
    by_id = {r["id"]: dict(r) for r in rows}
    if len(by_id) != 2:
        raise HTTPException(404, "Das Buchungspaar ist nicht mehr vorhanden.")
    first, second = by_id[payload.first_transaction_id], by_id[payload.second_transaction_id]
    if (
        first["version"] != payload.first_version
        or second["version"] != payload.second_version
        or match_pair(first, second) is None
    ):
        raise HTTPException(409, "Die Buchungen wurden geändert. Bitte erneut prüfen.")


async def dismiss_pair(
    session: AsyncSession,
    household_id: UUID,
    payload: ResolveBookedDuplicateInput,
) -> None:
    await _dismiss_pairs(session, household_id, [payload])


async def _dismiss_pairs(
    session: AsyncSession,
    household_id: UUID,
    items: list[ResolveBookedDuplicateInput],
) -> None:
    if not items:
        return
    pairs = []
    for item in items:
        first, second = sorted(
            [
                (item.first_transaction_id, item.first_version),
                (item.second_transaction_id, item.second_version),
            ]
        )
        pairs.append(
            {
                "first_id": str(first[0]),
                "second_id": str(second[0]),
                "first_version": first[1],
                "second_version": second[1],
            }
        )
    await session.execute(
        text(
            "insert into transaction_duplicate_dismissals "
            "(household_id, first_transaction_id, second_transaction_id, "
            "first_version, second_version) "
            "select cast(:household_id as uuid), d.first_id, d.second_id, "
            "d.first_version, d.second_version "
            "from jsonb_to_recordset(cast(:pairs as jsonb)) "
            "as d(first_id uuid, second_id uuid, first_version integer, second_version integer) "
            "on conflict (household_id, first_transaction_id, second_transaction_id) "
            "do update set first_version = excluded.first_version, "
            "second_version = excluded.second_version, dismissed_at = now()"
        ),
        {
            "household_id": str(household_id),
            "pairs": json.dumps(pairs),
        },
    )


def _archive_targets(items: list[ResolveBookedDuplicateInput]) -> set[UUID]:
    """Coalesce triple copies and reject contradictory choices before any writes."""
    targets: set[UUID] = set()
    kept: set[UUID] = set()
    neighbours: dict[UUID, set[UUID]] = defaultdict(set)
    for item in items:
        first, second = item.first_transaction_id, item.second_transaction_id
        neighbours[first].add(second)
        neighbours[second].add(first)
        if item.action == "keep_both":
            kept.update((first, second))
        else:
            targets.add(first if item.action == "archive_first" else second)
    if targets & kept:
        raise HTTPException(409, "Eine Buchung ist zugleich zum Behalten und Entfernen ausgewählt.")
    unseen = set(neighbours)
    while unseen:
        pending = [unseen.pop()]
        component: set[UUID] = set(pending)
        while pending:
            for neighbour in neighbours[pending.pop()] & unseen:
                unseen.remove(neighbour)
                component.add(neighbour)
                pending.append(neighbour)
        if component <= targets:
            raise HTTPException(
                409,
                "Mindestens eine Buchung jeder Duplikatgruppe muss erhalten bleiben. "
                "Bitte die Auswahl ändern.",
            )
    return targets


async def resolve_bulk(
    session: AsyncSession,
    household_id: UUID,
    payload: BulkResolveBookedDuplicatesInput,
) -> tuple[BulkResolveBookedDuplicatesResult, dict[str, int]]:
    """Validate and lock the whole selection, then detach/archive each target once.

    The request's transaction makes this all-or-nothing, including reference cleanup.
    No per-pair database lookups, updates or reconciliation tasks are needed.
    """
    from app.services.transaction_detach_service import (
        TransactionDetachForbiddenError,
        detach_transactions,
    )

    targets = _archive_targets(payload.items)
    ids = sorted(
        {
            tx
            for item in payload.items
            for tx in (item.first_transaction_id, item.second_transaction_id)
        }
    )
    rows = (
        (
            await session.execute(
                text(
                    f"select {_COLUMNS} {_JOINS} "
                    "where t.household_id = :household_id and not t.is_archived "
                    "and t.id = any(cast(:ids as uuid[])) order by t.id for update of t"
                ),
                {"household_id": str(household_id), "ids": [str(tx) for tx in ids]},
            )
        )
        .mappings()
        .all()
    )
    by_id = {row["id"]: dict(row) for row in rows}
    if len(by_id) != len(ids):
        raise HTTPException(
            404, "Mindestens eine Buchung ist nicht mehr vorhanden. Bitte erneut prüfen."
        )
    for item in payload.items:
        first, second = by_id[item.first_transaction_id], by_id[item.second_transaction_id]
        if (
            first["version"] != item.first_version
            or second["version"] != item.second_version
            or match_pair(first, second) is None
        ):
            raise HTTPException(409, "Die Buchungen wurden geändert. Bitte erneut prüfen.")

    detached: dict[str, int] = {}
    if targets:
        target_ids = [str(tx) for tx in sorted(targets)]
        try:
            detached = await detach_transactions(session, household_id, target_ids)
        except TransactionDetachForbiddenError as exc:
            raise HTTPException(403, str(exc)) from exc
        await session.execute(
            text(
                "update transactions set is_archived = true "
                "where household_id = :household_id and id = any(cast(:ids as uuid[]))"
            ),
            {"household_id": str(household_id), "ids": target_ids},
        )
    kept_pairs = [item for item in payload.items if item.action == "keep_both"]
    await _dismiss_pairs(session, household_id, kept_pairs)
    return BulkResolveBookedDuplicatesResult(
        processed_count=len(payload.items),
        archived_count=len(targets),
        kept_count=len(kept_pairs),
    ), detached
