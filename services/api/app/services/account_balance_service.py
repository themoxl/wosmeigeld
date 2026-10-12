"""Gemeinsamer Kontostand-Dienst (Plan 1.1/11.3): extrahiert die bisher
doppelt gepflegte Formel `opening_balance_minor + Summe nicht archivierter
Transaktionen` aus `goal_service.fetch_net_worth_report` und
`routes/accounts.reconcile_account` in einen einzigen Dienst. Nettovermoegen,
Vorhaben-Liquiditaetsvorschlaege (P2) und Kontoabstimmung lesen ab jetzt
denselben Code, keine zweite Wahrheit."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class AccountBalance:
    account_id: UUID
    account_type: str
    balance_minor: int


async def fetch_current_account_balances(
    session: AsyncSession, household_id: UUID, *, include_closed: bool = False
) -> list[AccountBalance]:
    """Aktueller Saldo je Konto: `opening_balance_minor` plus Summe aller
    nicht archivierten Buchungen, ohne Stichtagsgrenze."""
    result = await session.execute(
        text(
            "select a.id as account_id, a.account_type, "
            "a.opening_balance_minor + coalesce(sum(t.amount_minor), 0) as balance_minor "
            "from accounts a "
            "left join transactions t on t.account_id = a.id and not t.is_archived "
            "where a.household_id = :household_id "
            + ("" if include_closed else "and not a.is_closed ")
            + "group by a.id, a.account_type, a.opening_balance_minor"
        ),
        {"household_id": str(household_id)},
    )
    return [
        AccountBalance(
            account_id=row.account_id,
            account_type=row.account_type,
            balance_minor=int(row.balance_minor),
        )
        for row in result
    ]


async def fetch_account_balances_as_of(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of_date: date,
    account_ids: list[UUID] | None = None,
    include_closed: bool = False,
) -> list[AccountBalance]:
    """Salden mehrerer Konten zu einem Stichtag.

    Die Mehrkonten-Variante von `fetch_account_balance_as_of`. Bewusst eine
    eigene Funktion und keine Erweiterung von
    `fetch_current_account_balances`: jene summiert ausdruecklich "ohne
    Stichtagsgrenze" und hat mit Nettovermoegen und Kontoabstimmung andere
    Aufrufer, die genau das brauchen.

    Fuer die Liquiditaetsvorschau ist die Stichtagsgrenze zwingend: ohne sie
    steckte jede vorgemerkte Buchung schon im Startsaldo und zaehlte als
    Forecast-Flow ein zweites Mal (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md,
    Abschnitt 4.1).

    `account_ids=None` bedeutet "alle Konten"; eine leere Liste liefert
    entsprechend nichts.
    """
    if account_ids is not None and not account_ids:
        return []

    result = await session.execute(
        text(
            "select a.id as account_id, a.account_type, "
            "a.opening_balance_minor + coalesce(sum(t.amount_minor), 0) as balance_minor "
            "from accounts a "
            "left join transactions t on t.account_id = a.id and not t.is_archived "
            "and t.booked_date <= :as_of_date "
            "where a.household_id = :household_id "
            + ("" if include_closed else "and not a.is_closed ")
            + (
                ""
                if account_ids is None
                else "and a.id = any(cast(:account_ids as uuid[])) "
            )
            + "group by a.id, a.account_type, a.opening_balance_minor"
        ),
        {
            "household_id": str(household_id),
            "as_of_date": as_of_date,
            **(
                {}
                if account_ids is None
                else {"account_ids": [str(item) for item in account_ids]}
            ),
        },
    )
    return [
        AccountBalance(
            account_id=row.account_id,
            account_type=row.account_type,
            balance_minor=int(row.balance_minor),
        )
        for row in result
    ]


async def fetch_account_balance_as_of(
    session: AsyncSession, household_id: UUID, account_id: UUID, as_of_date: date
) -> int | None:
    """Saldo eines einzelnen Kontos zu einem Stichtag (nur gebuchte,
    nicht archivierte Buchungen bis einschliesslich `as_of_date`). `None`,
    wenn das Konto in diesem Haushalt nicht existiert."""
    account_row = (
        await session.execute(
            text(
                "select opening_balance_minor from accounts "
                "where id = :account_id and household_id = :household_id"
            ),
            {"account_id": str(account_id), "household_id": str(household_id)},
        )
    ).first()
    if account_row is None:
        return None

    booked_sum = (
        await session.execute(
            text(
                "select coalesce(sum(amount_minor), 0) as total from transactions "
                "where account_id = :account_id and household_id = :household_id "
                "and booked_date <= :as_of_date and not is_archived"
            ),
            {
                "account_id": str(account_id),
                "household_id": str(household_id),
                "as_of_date": as_of_date,
            },
        )
    ).scalar_one()

    return int(account_row.opening_balance_minor) + int(booked_sum)
