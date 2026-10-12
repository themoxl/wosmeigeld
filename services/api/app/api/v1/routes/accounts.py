from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session, require_household_editor
from app.schemas.account import (
    Account,
    AccountCreate,
    AccountUpdate,
    ReconcileInput,
    Reconciliation,
)
from app.services.account_balance_service import fetch_account_balance_as_of

router = APIRouter(prefix="/accounts", tags=["accounts"])

_ACCOUNT_COLUMNS = (
    "id, name, account_type, institution, currency, on_budget, "
    "opening_balance_minor, opening_balance_date, is_closed, forecast_liquidity"
)

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Schreibende Endpunkte: 403 statt stillem Nichtstun, wenn die Rolle nicht
# schreiben darf (siehe app/auth/dependencies.py).
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]


@router.get("", response_model=list[Account])
async def list_accounts(ctx: HouseholdSession) -> list[Account]:
    session, household_id = ctx
    result = await session.execute(
        text(
            f"select {_ACCOUNT_COLUMNS} from accounts "
            "where household_id = :household_id and not is_closed "
            "order by created_at"
        ),
        {"household_id": str(household_id)},
    )
    return [Account(**row._mapping) for row in result]


@router.post("", response_model=Account, status_code=201)
async def create_account(payload: AccountCreate, ctx: HouseholdEditorSession) -> Account:
    session, household_id = ctx
    row = (
        await session.execute(
            text(
                "insert into accounts (household_id, name, account_type, institution, currency, "
                "on_budget, opening_balance_minor, opening_balance_date, forecast_liquidity) "
                "values (:household_id, :name, :account_type, :institution, :currency, "
                ":on_budget, :opening_balance_minor, :opening_balance_date, "
                ":forecast_liquidity) "
                f"returning {_ACCOUNT_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "name": payload.name,
                "account_type": payload.account_type,
                "institution": payload.institution,
                "currency": payload.currency,
                "on_budget": payload.on_budget,
                "opening_balance_minor": payload.opening_balance_minor,
                "opening_balance_date": payload.opening_balance_date,
                "forecast_liquidity": payload.forecast_liquidity,
            },
        )
    ).one()
    return Account(**row._mapping)


@router.patch("/{account_id}", response_model=Account)
async def update_account(
    account_id: UUID, payload: AccountUpdate, ctx: HouseholdEditorSession
) -> Account:
    session, household_id = ctx
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        row = (
            await session.execute(
                text(
                    f"select {_ACCOUNT_COLUMNS} from accounts "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(account_id), "household_id": str(household_id)},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")
        return Account(**row._mapping)

    set_clause = ", ".join(f"{column} = :{column}" for column in updates)
    updates["id"] = str(account_id)
    updates["household_id"] = str(household_id)
    row = (
        await session.execute(
            text(
                f"update accounts set {set_clause}, updated_at = now() "
                "where id = :id and household_id = :household_id "
                f"returning {_ACCOUNT_COLUMNS}"
            ),
            updates,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")
    return Account(**row._mapping)


@router.get("/{account_id}/reconciliations", response_model=list[Reconciliation])
async def list_reconciliations(account_id: UUID, ctx: HouseholdSession) -> list[Reconciliation]:
    session, household_id = ctx
    result = await session.execute(
        text(
            "select id, account_id, as_of_date, statement_balance_minor, computed_balance_minor, "
            "difference_minor, status from reconciliations "
            "where account_id = :account_id and household_id = :household_id "
            "order by as_of_date desc"
        ),
        {"account_id": str(account_id), "household_id": str(household_id)},
    )
    return [Reconciliation(**row._mapping) for row in result]


@router.post("/{account_id}/reconcile", response_model=Reconciliation, status_code=201)
async def reconcile_account(
    account_id: UUID, payload: ReconcileInput, ctx: HouseholdEditorSession
) -> Reconciliation:
    """Kontoabstimmung (Abschnitt 6.2/10.3): vergleicht den vom Nutzer
    eingegebenen Kontoauszugssaldo mit dem aus den Buchungen berechneten
    Saldo zum Stichtag. Bei Uebereinstimmung werden die betroffenen
    Buchungen als 'reconciled' markiert (schreibgeschuetzt-Signal fuers UI,
    keine harte DB-Sperre in Phase 2)."""
    session, household_id = ctx

    computed_balance_minor = await fetch_account_balance_as_of(
        session, household_id, account_id, payload.as_of_date
    )
    if computed_balance_minor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")
    difference_minor = payload.statement_balance_minor - computed_balance_minor
    result_status = "completed" if difference_minor == 0 else "discrepancy"

    row = (
        await session.execute(
            text(
                "insert into reconciliations (household_id, account_id, as_of_date, "
                "statement_balance_minor, computed_balance_minor, difference_minor, status) "
                "values (:household_id, :account_id, :as_of_date, :statement_balance_minor, "
                ":computed_balance_minor, :difference_minor, :status) "
                "on conflict (account_id, as_of_date) do update set "
                "statement_balance_minor = excluded.statement_balance_minor, "
                "computed_balance_minor = excluded.computed_balance_minor, "
                "difference_minor = excluded.difference_minor, status = excluded.status "
                "returning id, account_id, as_of_date, statement_balance_minor, "
                "computed_balance_minor, difference_minor, status"
            ),
            {
                "household_id": str(household_id),
                "account_id": str(account_id),
                "as_of_date": payload.as_of_date,
                "statement_balance_minor": payload.statement_balance_minor,
                "computed_balance_minor": computed_balance_minor,
                "difference_minor": difference_minor,
                "status": result_status,
            },
        )
    ).one()

    if result_status == "completed":
        await session.execute(
            text(
                "update transactions set status = 'reconciled', updated_at = now() "
                "where account_id = :account_id and household_id = :household_id "
                "and booked_date <= :as_of_date and not is_archived and status <> 'reconciled'"
            ),
            {
                "account_id": str(account_id),
                "household_id": str(household_id),
                "as_of_date": payload.as_of_date,
            },
        )

    return Reconciliation(**row._mapping)
