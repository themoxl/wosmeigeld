from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_household_editor
from app.schemas.transaction import TransferLink, TransferLinkCreate

router = APIRouter(prefix="/transfers", tags=["transfers"])

# Beide Endpunkte schreiben - 403 statt stillem Nichtstun, wenn die Rolle
# nicht schreiben darf (siehe app/auth/dependencies.py).
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]


@router.post("/link", response_model=TransferLink, status_code=201)
async def link_transfer(payload: TransferLinkCreate, ctx: HouseholdEditorSession) -> TransferLink:
    """Verknuepft zwei Buchungen als internen Transfer (Abschnitt 9:
    Transferpaare sind budgetneutral, keine Einnahme/Ausgabe)."""
    session, household_id = ctx

    if payload.outgoing_transaction_id == payload.incoming_transaction_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="transactions_must_differ"
        )

    rows = (
        await session.execute(
            text(
                "select id, account_id, amount_minor, is_transfer from transactions "
                "where household_id = :household_id and id = any(:ids)"
            ),
            {
                "household_id": str(household_id),
                "ids": [str(payload.outgoing_transaction_id), str(payload.incoming_transaction_id)],
            },
        )
    ).all()
    if len(rows) != 2:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found")

    by_id = {str(row.id): row for row in rows}
    outgoing = by_id[str(payload.outgoing_transaction_id)]
    incoming = by_id[str(payload.incoming_transaction_id)]
    if outgoing.is_transfer or incoming.is_transfer:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="transaction_already_linked"
        )
    if outgoing.account_id == incoming.account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="transfer_requires_different_accounts"
        )

    row = (
        await session.execute(
            text(
                "insert into transfer_links "
                "(household_id, outgoing_transaction_id, incoming_transaction_id, match_status) "
                "values (:household_id, :outgoing_id, :incoming_id, 'manual') "
                "returning id, outgoing_transaction_id, incoming_transaction_id, match_status"
            ),
            {
                "household_id": str(household_id),
                "outgoing_id": str(payload.outgoing_transaction_id),
                "incoming_id": str(payload.incoming_transaction_id),
            },
        )
    ).one()

    await session.execute(
        text(
            "update transactions set is_transfer = true "
            "where id = any(:ids) and household_id = :household_id"
        ),
        {
            "ids": [str(payload.outgoing_transaction_id), str(payload.incoming_transaction_id)],
            "household_id": str(household_id),
        },
    )

    return TransferLink(**row._mapping)


@router.delete("/link/{transfer_link_id}", status_code=204)
async def unlink_transfer(transfer_link_id: UUID, ctx: HouseholdEditorSession) -> None:
    session, household_id = ctx

    row = (
        await session.execute(
            text(
                "delete from transfer_links where id = :id and household_id = :household_id "
                "returning outgoing_transaction_id, incoming_transaction_id"
            ),
            {"id": str(transfer_link_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transfer_link_not_found")

    await session.execute(
        text("update transactions set is_transfer = false where id = any(:ids)"),
        {"ids": [str(row.outgoing_transaction_id), str(row.incoming_transaction_id)]},
    )
