"""Erstattungsfaehigkeit am Buchungsstrom - Marken setzen, lesen, zuruecknehmen.

Bewusst getrennt von `reimbursement_service.py`: dort geht es um Vorgaenge unter
der engeren Gesundheits-RLS, hier um eine Annotation am Buchungsstrom unter
normaler Haushalts-RLS. Die beiden Ebenen zu vermischen war der Fehler, den
docs/ERSTATTUNGSFAEHIGKEIT-PLAN.md, Abschnitt 3 und 4, ausdruecklich vermeidet.
"""

from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.reimbursements import (
    BulkReimbursementMarkInput,
    BulkReimbursementMarkItem,
    BulkReimbursementMarkResult,
    OpenReimbursementMark,
    ReimbursementMark,
    ReimbursementMarkInput,
)

# Der erwartete Betrag steht an genau einer Stelle. Waere er in der API noch
# einmal gerechnet, wichen Liste und Detail frueher oder spaeter um einen Cent
# voneinander ab. `round` auf numeric rundet kaufmaennisch.
_EXPECTED_MINOR_SQL = """
  case
    when m.state <> 'expected' then null
    when m.expectation_mode = 'full' then abs(t.amount_minor)
    when m.expectation_mode = 'quota'
      then round(abs(t.amount_minor) * m.expectation_rate)::bigint
    when m.expectation_mode = 'fixed' then m.expectation_minor
    else null
  end as expected_minor
"""

_MARK_COLUMNS = f"""
  m.id, m.transaction_id, m.state, m.expectation_mode, m.expectation_rate,
  m.expectation_minor, {_EXPECTED_MINOR_SQL}, m.source, m.rule_id,
  m.converted_at, m.created_at, m.updated_at
"""

# Wer darf wen ueberschreiben. Die Handentscheidung ist unantastbar - ohne diese
# Zusage waere jede Korrektur wertlos, weil der naechste Import sie kassiert.
_MAY_OVERWRITE: dict[str, set[str]] = {
    "manual": {"manual", "rule", "category"},
    "rule": {"rule", "category"},
    "category": {"category"},
}


class MarkNotFound(Exception):
    """An dieser Buchung haengt keine Marke."""


class MarkAlreadyConverted(Exception):
    """Aus dieser Marke ist bereits ein Vorgang geworden."""


class MarkNotConvertible(Exception):
    """Eine ausgenommene Buchung ergibt keinen Vorgang."""


class MarkConflict(Exception):
    """Eine staerkere Quelle haelt die Marke bereits."""

    def __init__(self, existing_source: str) -> None:
        super().__init__(existing_source)
        self.existing_source = existing_source


class MarkRejected(Exception):
    """Die Datenbank hat die Marke abgewiesen (Richtung, Betrag, Buchung)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MarkTransactionMissing(Exception):
    """Die Buchung gibt es in diesem Haushalt nicht.

    Bewusst nicht nach "existiert nicht" und "gehoert einem anderen Haushalt"
    unterschieden - die Unterscheidung waere genau die Information, die ein
    fremder Haushalt nicht bekommen darf.
    """


_DB_MESSAGES = {
    "REIMBURSEMENT_MARK_MUST_BE_OUTGOING": (
        "Nur Abgaenge lassen sich als erstattungsfaehig markieren."
    ),
    "REIMBURSEMENT_MARK_EXCEEDS_TRANSACTION": (
        "Der erwartete Betrag ist groesser als die Buchung."
    ),
    "REIMBURSEMENT_MARK_TRANSACTION_NOT_FOUND": "Die Buchung existiert nicht.",
}


def describe(code: str) -> str:
    return _DB_MESSAGES.get(code, code)


def _rejection_code(error: Exception) -> str | None:
    text_form = str(error)
    for code in _DB_MESSAGES:
        if code in text_form:
            return code
    return None


async def get_mark(
    session: AsyncSession, household_id: UUID, transaction_id: UUID
) -> ReimbursementMark | None:
    row = (
        await session.execute(
            text(
                f"select {_MARK_COLUMNS} from transaction_reimbursement_marks m "
                "join transactions t on t.id = m.transaction_id "
                "and t.household_id = m.household_id "
                "where m.household_id = :household_id and m.transaction_id = :transaction_id"
            ),
            {"household_id": str(household_id), "transaction_id": str(transaction_id)},
        )
    ).one_or_none()
    return ReimbursementMark(**row._mapping) if row else None


async def set_mark(
    session: AsyncSession,
    household_id: UUID,
    transaction_id: UUID,
    payload: ReimbursementMarkInput,
    *,
    source: Literal["manual", "rule", "category"] = "manual",
    rule_id: UUID | None = None,
) -> ReimbursementMark:
    """Setzt oder aendert die Marke, unter Beachtung des Vorrangs.

    Hand schlaegt Regel schlaegt Kategorie. Wer schwaecher ist als der Bestand,
    bekommt `MarkConflict` - der Aufrufer entscheidet, ob das ein Fehler ist
    (Handaufruf) oder ein uebersprungener Eintrag (Sammellauf).

    Eine Buchung aus einem fremden Haushalt fuehrt zu `MarkTransactionMissing`
    und damit zu genau derselben Antwort wie eine Buchung, die es gar nicht
    gibt. Ohne diese Vorpruefung wies erst der Trigger die Zeile ab, was als
    422 herauskam - und ein 422 verriete, dass die Buchung existiert und nur
    die Fachpruefung scheiterte.
    """
    exists = (
        await session.execute(
            text(
                "select 1 from transactions "
                "where id = :transaction_id and household_id = :household_id"
            ),
            {"transaction_id": str(transaction_id), "household_id": str(household_id)},
        )
    ).first()
    if exists is None:
        raise MarkTransactionMissing()

    existing = (
        await session.execute(
            text(
                "select source from transaction_reimbursement_marks "
                "where household_id = :household_id and transaction_id = :transaction_id "
                "for update"
            ),
            {"household_id": str(household_id), "transaction_id": str(transaction_id)},
        )
    ).one_or_none()
    if existing is not None and existing.source not in _MAY_OVERWRITE[source]:
        raise MarkConflict(existing.source)

    params = {
        "household_id": str(household_id),
        "transaction_id": str(transaction_id),
        "state": payload.state,
        "expectation_mode": payload.expectation_mode,
        "expectation_rate": payload.expectation_rate,
        "expectation_minor": payload.expectation_minor,
        "source": source,
        "rule_id": str(rule_id) if rule_id else None,
    }
    try:
        await session.execute(
            text(
                "insert into transaction_reimbursement_marks "
                "(household_id, transaction_id, state, expectation_mode, expectation_rate, "
                " expectation_minor, source, rule_id) values "
                "(:household_id, :transaction_id, :state, :expectation_mode, :expectation_rate, "
                " :expectation_minor, :source, :rule_id) "
                "on conflict (household_id, transaction_id) do update set "
                "state = excluded.state, expectation_mode = excluded.expectation_mode, "
                "expectation_rate = excluded.expectation_rate, "
                "expectation_minor = excluded.expectation_minor, "
                "source = excluded.source, rule_id = excluded.rule_id, updated_at = now()"
            ),
            params,
        )
    except Exception as error:  # noqa: BLE001 - Triggermeldung uebersetzen
        code = _rejection_code(error)
        if code:
            raise MarkRejected(code) from error
        raise

    mark = await get_mark(session, household_id, transaction_id)
    assert mark is not None
    return mark


async def remove_mark(
    session: AsyncSession, household_id: UUID, transaction_id: UUID
) -> bool:
    """Nimmt die Marke ganz zurueck - die Buchung ist danach unentschieden.

    Ausdruecklich etwas anderes als `state='excluded'`: nach dem Loeschen darf
    ein Regel- oder Kategorielauf wieder greifen, nach dem Ausnehmen nicht.
    Eine Marke, aus der ein Vorgang geworden ist, bleibt stehen - an ihm koennen
    Belege und Einreichungen haengen.
    """
    deleted = (
        await session.execute(
            text(
                "delete from transaction_reimbursement_marks "
                "where household_id = :household_id and transaction_id = :transaction_id "
                "and converted_at is null returning id"
            ),
            {"household_id": str(household_id), "transaction_id": str(transaction_id)},
        )
    ).first()
    return deleted is not None


async def list_marks(
    session: AsyncSession,
    household_id: UUID,
    *,
    only_open: bool = True,
    limit: int = 200,
) -> list[OpenReimbursementMark]:
    """Marken samt ihrer Buchung.

    `only_open` liefert die Liste, die heute fehlt: markiert, Geld erwartet,
    noch kein Vorgang. Ohne die Einschraenkung kommen auch ausgenommene und
    bereits umgewandelte Marken mit - die Buchungsliste braucht sie, um an
    jeder Zeile den richtigen Zustand zu zeigen.
    """
    rows = (
        await session.execute(
            text(
                f"select {_MARK_COLUMNS}, t.booked_date, t.amount_minor, t.payee_label, t.note "
                "from transaction_reimbursement_marks m "
                "join transactions t on t.id = m.transaction_id "
                "and t.household_id = m.household_id "
                "where m.household_id = :household_id and not t.is_archived "
                + (
                    "and m.state = 'expected' and m.converted_at is null "
                    if only_open
                    else ""
                )
                + "order by t.booked_date desc limit :limit"
            ),
            {"household_id": str(household_id), "limit": limit},
        )
    ).all()
    return [OpenReimbursementMark(**row._mapping) for row in rows]


async def bulk_set_marks(
    session: AsyncSession,
    household_id: UUID,
    payload: BulkReimbursementMarkInput,
    *,
    write: bool,
) -> BulkReimbursementMarkResult:
    """Mehrfachauswahl aus "Alle Buchungen" - mit Vorschau, wie ueberall im Haus.

    Ohne Vorschau markiert eine zu weit gefasste Auswahl hunderte Buchungen,
    bevor man sie gesehen hat.
    """
    single = ReimbursementMarkInput(
        state=payload.state,
        expectation_mode=payload.expectation_mode,
        expectation_rate=payload.expectation_rate,
        expectation_minor=payload.expectation_minor,
    )
    items: list[BulkReimbursementMarkItem] = []
    for transaction_id in payload.transaction_ids:
        before = await get_mark(session, household_id, transaction_id)
        if before is not None and (
            before.state == single.state
            and before.expectation_mode == single.expectation_mode
            and before.expectation_rate == single.expectation_rate
            and before.expectation_minor == single.expectation_minor
        ):
            items.append(
                BulkReimbursementMarkItem(transaction_id=transaction_id, status="unchanged")
            )
            continue
        try:
            # Der SAVEPOINT haelt eine Abweisung lokal: eine Eingangsbuchung in
            # der Auswahl darf nicht den ganzen Lauf zuruecknehmen.
            async with session.begin_nested():
                await set_mark(session, household_id, transaction_id, single)
                if not write:
                    raise _Rollback
        except _Rollback:
            items.append(
                BulkReimbursementMarkItem(transaction_id=transaction_id, status="applied")
            )
        except MarkConflict as conflict:
            items.append(
                BulkReimbursementMarkItem(
                    transaction_id=transaction_id,
                    status="conflict",
                    reason=f"Marke stammt aus Quelle '{conflict.existing_source}'",
                )
            )
        except MarkRejected as rejected:
            items.append(
                BulkReimbursementMarkItem(
                    transaction_id=transaction_id,
                    status="skipped",
                    reason=describe(rejected.code),
                )
            )
        except MarkTransactionMissing:
            # Eine unbekannte Buchung in der Auswahl ueberspringen, nicht den
            # ganzen Sammellauf abbrechen - sonst kostet eine inzwischen
            # geloeschte Zeile alle uebrigen Treffer.
            items.append(
                BulkReimbursementMarkItem(
                    transaction_id=transaction_id,
                    status="skipped",
                    reason="Buchung nicht gefunden",
                )
            )
        else:
            items.append(
                BulkReimbursementMarkItem(transaction_id=transaction_id, status="applied")
            )
    return BulkReimbursementMarkResult(
        applied=sum(1 for item in items if item.status == "applied"),
        unchanged=sum(1 for item in items if item.status == "unchanged"),
        skipped=sum(1 for item in items if item.status == "skipped"),
        conflict=sum(1 for item in items if item.status == "conflict"),
        items=items,
    )


class _Rollback(Exception):
    """Bricht den SAVEPOINT der Vorschau ab, ohne etwas zu schreiben."""


async def apply_rule(
    session: AsyncSession, household_id: UUID, transaction_id: UUID, rule: object
) -> bool:
    """Uebersetzt eine getroffene Regelaktion in eine Marke.

    Ein Fehlschlag ist ausdruecklich kein Fehler: die Buchung ist wichtiger als
    die Marke. Im Importpfad darf eine abgewiesene Erwartung nicht den ganzen
    Lauf kippen, und eine Handentscheidung an derselben Buchung hat Vorrang.
    """
    state = getattr(rule, "action_reimbursable", None)
    if state not in {"expected", "excluded"}:
        return False
    payload = ReimbursementMarkInput(
        state=state,
        expectation_mode=getattr(rule, "action_expectation_mode", None) or "full",
        expectation_rate=getattr(rule, "action_expectation_rate", None),
        expectation_minor=getattr(rule, "action_expectation_minor", None),
    )
    try:
        async with session.begin_nested():
            await set_mark(
                session,
                household_id,
                transaction_id,
                payload,
                source="rule",
                rule_id=getattr(rule, "id", None),
            )
    except (MarkConflict, MarkRejected, MarkTransactionMissing):
        return False
    return True


async def withdraw_rule_marks(
    session: AsyncSession, household_id: UUID, rule_id: UUID, *, write: bool
) -> dict[str, int]:
    """Sammelruecknahme aller Marken einer Regel.

    Zwei Dinge bleiben ausdruecklich stehen: Handentscheidungen, weil sie kein
    automatischer Lauf zurueckzunehmen hat, und Marken, aus denen ein Vorgang
    geworden ist - an ihm koennen Belege und Einreichungen haengen. Der Vorgang
    wird von Hand aufgeloest oder gar nicht.
    """
    zaehlung = (
        await session.execute(
            text(
                "select "
                "count(*) filter (where source = 'rule' and rule_id = :rule_id "
                "                 and converted_at is null) as withdrawable, "
                "count(*) filter (where source = 'rule' and rule_id = :rule_id "
                "                 and converted_at is not null) as kept_converted, "
                "count(*) filter (where source = 'manual') as kept_manual "
                "from transaction_reimbursement_marks where household_id = :household_id"
            ),
            {"household_id": str(household_id), "rule_id": str(rule_id)},
        )
    ).one()
    if write and zaehlung.withdrawable:
        await session.execute(
            text(
                "delete from transaction_reimbursement_marks "
                "where household_id = :household_id and source = 'rule' "
                "and rule_id = :rule_id and converted_at is null"
            ),
            {"household_id": str(household_id), "rule_id": str(rule_id)},
        )
    return {
        "withdrawn": int(zaehlung.withdrawable),
        "kept_manual": int(zaehlung.kept_manual),
        "kept_converted": int(zaehlung.kept_converted),
    }


async def convert_to_case(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    transaction_id: UUID,
    insured_person_id: UUID,
) -> UUID:
    """Macht aus einer Marke einen Erstattungsvorgang.

    Hier ueberquert der Vorgang die Zugriffsgrenze: die Marke traegt keine
    Person, der Vorgang verlangt eine. Deshalb kommt sie erst an dieser Stelle
    ins Spiel - und nur wer die Freigabe fuer sie hat, kommt hier durch.

    Vorbelegt wird aus der Buchung: Anbieter aus dem Zahlungsempfaenger, Datum
    aus dem Buchungsdatum, Rechnungsbetrag aus dem Abgang. `payment_status` ist
    'paid', denn die Buchung IST der Beleg dafuer. Zusammen mit einer Erwartung
    groesser null ergibt das `action_required` - genau die Meldung, die bisher
    nie kam, weil eine Rechnung allein keine Erwartung nennt.
    """
    from app.schemas.reimbursements import (
        ReimbursementCaseCreate,
        TransactionAllocationCreate,
    )
    from app.services import reimbursement_service

    row = (
        await session.execute(
            text(
                f"select {_MARK_COLUMNS}, t.booked_date, t.amount_minor, t.payee_label, t.note "
                "from transaction_reimbursement_marks m "
                "join transactions t on t.id = m.transaction_id "
                "and t.household_id = m.household_id "
                "where m.household_id = :household_id and m.transaction_id = :transaction_id "
                "for update of m"
            ),
            {"household_id": str(household_id), "transaction_id": str(transaction_id)},
        )
    ).one_or_none()
    if row is None:
        raise MarkNotFound
    if row.converted_at is not None:
        raise MarkAlreadyConverted
    if row.state != "expected":
        raise MarkNotConvertible

    billed_minor = abs(int(row.amount_minor))
    expected_minor = int(row.expected_minor or 0)
    case = await reimbursement_service.create_case(
        session,
        household_id,
        ReimbursementCaseCreate(
            insured_person_id=insured_person_id,
            provider_name=(row.payee_label or row.note or "Unbekannter Leistungserbringer")[:240],
            invoice_date=row.booked_date,
            billed_minor=billed_minor,
            expected_minor=expected_minor,
            # Die Buchung ist der Zahlungsbeleg - alles andere waere eine
            # Falschaussage ueber etwas, das nachweislich abgeflossen ist.
            payment_status="paid",
            reimbursement_status="expected" if expected_minor > 0 else "not_expected",
        ),
    )
    await reimbursement_service.create_allocation(
        session,
        household_id,
        user_id,
        case.id,
        TransactionAllocationCreate(
            transaction_id=transaction_id,
            allocation_type="provider_payment",
            amount_minor=billed_minor,
        ),
    )
    await session.execute(
        text(
            "update transaction_reimbursement_marks set converted_at = now(), updated_at = now() "
            "where household_id = :household_id and transaction_id = :transaction_id"
        ),
        {"household_id": str(household_id), "transaction_id": str(transaction_id)},
    )
    return case.id
