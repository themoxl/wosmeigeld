"""Beteiligte, Objekte und Deckungsbausteine (docs/VERSICHERUNGEN-PLAN.md, P4).

Der Dienst haengt an vier Regeln, die fast jede Besonderheit dieser Datei
erklaeren:

1. **Es gibt keine zweite Personenliste.** Beteiligte kommen aus
   `health_insured_persons`, also aus dem Erstattungsmodul. Angelegt werden sie
   ausschliesslich ueber `reimbursement_service.create_person` - ein zweiter
   Schreibweg zu denselben Menschen waere eine zweite Wahrheit ueber sie. Fuer
   die Anzeige liefert die Datenbankfunktion `insured_person_label()` den Namen,
   ohne die Zeile freizugeben; sie ist zugleich die Existenzpruefung: "kein
   Name" und "gehoert nicht zu diesem Haushalt" sind derselbe Zustand.
2. **Der Gesundheitsschutz steht in der Datenbank, nicht hier.** Eine
   gesundheitsnahe Police mit fremder Person liefert schon in `_require_policy`
   kein Ergebnis, weil die RLS-Policy sie ausblendet - der Dienst antwortet
   dann 404 und nicht 403. Er prueft den Zugriff nicht ein zweites Mal und
   koennte ihn deshalb auch nicht versehentlich lockern.
3. **Ein Kennzeichenwechsel ist eine neue Zeile.** `POST /registrations`
   schliesst die laufende Zeile zum Vortag und oeffnet eine neue. Ueberschreiben
   wuerde die Zuordnung alter Buchungen und Schaeden verlieren.
4. **Hier entsteht kein Geld.** `sum_insured_minor` und `deductible_minor` sind
   Vertragsinhalt, kein Zahlungsstrom; sie tauchen in keiner Fixkostensumme auf.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Final, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Row, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import raise_version_conflict
from app.schemas.insurance import (
    InsuredObject,
    InsuredObjectCreate,
    InsuredObjectUpdate,
    ObjectRegistration,
    ObjectRegistrationCreate,
    PolicyBundle,
    PolicyBundleResult,
    PolicyCoverage,
    PolicyCoverageReplacement,
    PolicyObject,
    PolicyObjectCreate,
    PolicyPerson,
    PolicyPersonCreate,
)
from app.schemas.reimbursements import InsuredPersonCreate
from app.services import insurance_service, reimbursement_service

# ---------------------------------------------------------------------------
# Fehlercodes
# ---------------------------------------------------------------------------

INSURANCE_PERSON_NOT_FOUND: Final = "insurance_person_not_found"
INSURANCE_PERSON_LINK_NOT_FOUND: Final = "insurance_person_link_not_found"
INSURANCE_PERSON_ALREADY_LINKED: Final = "insurance_person_already_linked"
INSURANCE_POLICYHOLDER_ALREADY_SET: Final = "insurance_policyholder_already_set"
# Ein Bezugsrecht ueber 100 Prozent ist keine Feldverletzung, sondern ein
# Widerspruch zwischen mehreren Zeilen - ein CHECK kann ihn nicht sehen.
INSURANCE_BENEFICIARY_SHARE_EXCEEDED: Final = "insurance_beneficiary_share_exceeded"
INSURANCE_COVERAGE_KEY_UNKNOWN: Final = "insurance_coverage_key_unknown"
INSURANCE_OBJECT_NOT_FOUND: Final = "insurance_object_not_found"
INSURANCE_OBJECT_ALREADY_LINKED: Final = "insurance_object_already_linked"
INSURANCE_OBJECT_LINK_NOT_FOUND: Final = "insurance_object_link_not_found"
INSURANCE_OBJECT_IN_USE: Final = "insurance_object_in_use"
INSURANCE_REGISTRATION_OUT_OF_ORDER: Final = "insurance_registration_out_of_order"
# Aus dem Fehlerkatalog des Plans (Abschnitt 5): genau eine Buendelebene.
INSURANCE_POLICY_NESTING_TOO_DEEP: Final = "insurance_policy_nesting_too_deep"
INSURANCE_POLICY_SELF_REFERENCE: Final = "insurance_policy_self_reference"

# ---------------------------------------------------------------------------
# Spaltentupel - fest, nie aus Payload-Schluesseln gebaut
# ---------------------------------------------------------------------------

_OBJECT_COLUMNS: Final = """
  o.id, o.kind, o.label, o.asset_id, o.address_line, o.postal_code, o.city,
  o.living_area_sqm, o.build_year, o.attrs, o.acquired_on, o.disposed_on,
  o.is_active, o.version, o.created_at, o.updated_at,
  r.plate as current_plate,
  r.keeper_person_id as current_keeper_person_id,
  insured_person_label(r.keeper_person_id) as current_keeper_name,
  (select count(*) from insurance_policy_objects po
    where po.insured_object_id = o.id and po.household_id = o.household_id) as policy_count
"""

# Die laufende Zeile der Halterhistorie, seitlich angehaengt: sie ist Teil der
# Antwort, aber kein Feld der Tabelle - genau das ist der Zweck der Historie.
_OBJECT_FROM: Final = """
  from insured_objects o
  left join lateral (
    select reg.plate, reg.keeper_person_id
      from insured_object_registrations reg
     where reg.insured_object_id = o.id
       and reg.household_id = o.household_id
       and reg.valid_to is null
     order by reg.valid_from desc
     limit 1
  ) r on true
"""

_OBJECT_INSERT_COLUMNS: Final = (
    "household_id",
    "kind",
    "label",
    "asset_id",
    "address_line",
    "postal_code",
    "city",
    "living_area_sqm",
    "build_year",
    "attrs",
    "acquired_on",
    "disposed_on",
)

_OBJECT_UPDATABLE_COLUMNS: Final = (
    "kind",
    "label",
    "asset_id",
    "address_line",
    "postal_code",
    "city",
    "living_area_sqm",
    "build_year",
    "attrs",
    "acquired_on",
    "disposed_on",
    "is_active",
)

_JSON_COLUMNS: Final = frozenset({"attrs"})
_UUID_COLUMNS: Final = frozenset({"asset_id"})


def _rowcount(result: Any) -> int:
    """SQLAlchemy typisiert das Ergebnis von execute() als Result[Any]; die
    Trefferzahl haengt am CursorResult (gleiches Muster wie
    category_operations_service)."""
    return cast(CursorResult[Any], result).rowcount


def _placeholder(column: str) -> str:
    if column in _JSON_COLUMNS:
        return f"cast(:{column} as jsonb)"
    if column in _UUID_COLUMNS:
        return f"cast(:{column} as uuid)"
    return f":{column}"


def _param_value(column: str, value: Any) -> Any:
    if column in _JSON_COLUMNS:
        return json.dumps(value or {})
    if column in _UUID_COLUMNS and value is not None:
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Gemeinsame Vorpruefungen
# ---------------------------------------------------------------------------


async def _require_policy(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> dict[str, Any]:
    """Existiert die Police - und darf der Anfragende sie ueberhaupt sehen?

    Beide Fragen beantwortet dieselbe Abfrage: eine gesundheitsnahe Police mit
    fremder versicherter Person blendet die RLS-Policy aus, hier kommt dann
    keine Zeile an. 404 statt 403 ist Absicht - die Existenz einer fremden
    Ressource soll nicht verraten werden.
    """
    row = (
        await session.execute(
            text(
                "select p.id, p.line_key, p.status, p.version, p.parent_policy_id, "
                "coalesce(c.version, 0) as contract_version "
                "from insurance_policies p "
                "left join contracts c on c.id = p.contract_id "
                "  and c.household_id = p.household_id and c.merged_into_id is null "
                "where p.id = :policy_id and p.household_id = :household_id"
            ),
            {"policy_id": str(policy_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=insurance_service.INSURANCE_POLICY_NOT_FOUND,
        )
    return dict(row._mapping)


async def _person_label(session: AsyncSession, person_id: UUID) -> str:
    """Name der Person - und zugleich die Existenzpruefung.

    Bewusst ueber `insured_person_label()` und nicht ueber ein SELECT auf
    `health_insured_persons`: die Read-Policy dort verlangt eine
    Gesundheitsfreigabe, und ein Editor ohne sie koennte in einer Hausratpolice
    sonst nicht einmal den Namen sehen, den er selbst zugeordnet hat.
    """
    label = (
        await session.execute(
            text("select insured_person_label(cast(:person_id as uuid)) as label"),
            {"person_id": str(person_id)},
        )
    ).scalar_one_or_none()
    if label is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_PERSON_NOT_FOUND
        )
    return str(label)


# ---------------------------------------------------------------------------
# Beteiligte
# ---------------------------------------------------------------------------

_PERSON_COLUMNS: Final = """
  pp.id, pp.policy_id, pp.insured_person_id,
  insured_person_label(pp.insured_person_id) as display_name,
  pp.role, pp.share_bp, pp.valid_from, pp.valid_to, pp.note, pp.created_at
"""


def _as_person(row: Row[Any]) -> PolicyPerson:
    return PolicyPerson(**dict(row._mapping))


async def list_persons(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> list[PolicyPerson]:
    await _require_policy(session, household_id, policy_id)
    rows = (
        await session.execute(
            text(
                f"select {_PERSON_COLUMNS} from insurance_policy_persons pp "
                "where pp.household_id = :household_id and pp.policy_id = :policy_id "
                # Der Versicherungsnehmer zuerst: er ist der Vertragspartner,
                # nicht eine von mehreren gleichrangigen Rollen.
                "order by case pp.role when 'policyholder' then 0 else 1 end, "
                "  lower(coalesce(insured_person_label(pp.insured_person_id), '')), pp.role"
            ),
            {"household_id": str(household_id), "policy_id": str(policy_id)},
        )
    ).all()
    return [_as_person(row) for row in rows]


async def _assert_beneficiary_share(
    session: AsyncSession, household_id: UUID, policy_id: UUID, share_bp: int
) -> None:
    """Bezugsrechte einer Police duerfen zusammen 100 Prozent nicht ueberschreiten.

    Kein CHECK, weil die Bedingung ueber mehrere Zeilen laeuft. Bewusst "<=" und
    nicht "=": ein noch nicht vollstaendig erfasstes Bezugsrecht ist ein
    Zwischenstand, kein Fehler - der nicht vergebene Rest ist sichtbar.
    """
    vergeben = (
        await session.execute(
            text(
                "select coalesce(sum(share_bp), 0) from insurance_policy_persons "
                "where household_id = :household_id and policy_id = :policy_id "
                "and role = 'beneficiary'"
            ),
            {"household_id": str(household_id), "policy_id": str(policy_id)},
        )
    ).scalar_one()
    if int(vergeben) + share_bp > 10000:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_BENEFICIARY_SHARE_EXCEEDED
        )


async def add_person(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: PolicyPersonCreate,
    *,
    user_id: UUID,
) -> PolicyPerson:
    await _require_policy(session, household_id, policy_id)

    if payload.new_person is not None:
        # Ueber den Gesundheitszweig, nicht daneben: create_person legt in
        # derselben Transaktion die Admin-Freigabe des Erstellers an. Ein
        # eigenes INSERT hier erzeugte eine Person, die niemand lesen darf.
        person = await reimbursement_service.create_person(
            session,
            household_id,
            user_id,
            InsuredPersonCreate(
                display_name=payload.new_person.display_name,
                birth_date=payload.new_person.birth_date,
            ),
        )
        person_id = person.id
    else:
        assert payload.insured_person_id is not None  # vom Modellvalidator gesichert
        person_id = payload.insured_person_id
        await _person_label(session, person_id)

    if payload.role == "beneficiary" and payload.share_bp is not None:
        await _assert_beneficiary_share(session, household_id, policy_id, payload.share_bp)

    try:
        row = (
            await session.execute(
                text(
                    "insert into insurance_policy_persons "
                    "(household_id, policy_id, insured_person_id, role, share_bp, "
                    " valid_from, valid_to, note) values "
                    "(:household_id, :policy_id, :insured_person_id, :role, :share_bp, "
                    " :valid_from, :valid_to, :note) returning id"
                ),
                {
                    "household_id": str(household_id),
                    "policy_id": str(policy_id),
                    "insured_person_id": str(person_id),
                    "role": payload.role,
                    "share_bp": payload.share_bp,
                    "valid_from": payload.valid_from,
                    "valid_to": payload.valid_to,
                    "note": payload.note,
                },
            )
        ).first()
    except IntegrityError as exc:
        raise _translate_person_error(exc) from exc

    assert row is not None  # "returning id" liefert immer eine Zeile
    created = (
        await session.execute(
            text(
                f"select {_PERSON_COLUMNS} from insurance_policy_persons pp where pp.id = :id"
            ),
            {"id": str(row.id)},
        )
    ).one()
    return _as_person(created)


def _translate_person_error(exc: IntegrityError) -> HTTPException:
    message = str(getattr(exc, "orig", exc))
    if "insurance_policy_persons_holder_uniq" in message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_POLICYHOLDER_ALREADY_SET
        )
    if "insurance_policy_persons_household_id_policy_id_insured_per_key" in message:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_PERSON_ALREADY_LINKED
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insurance_person_link_rejected"
    )


async def remove_person(
    session: AsyncSession, household_id: UUID, policy_id: UUID, link_id: UUID
) -> None:
    await _require_policy(session, household_id, policy_id)
    result = await session.execute(
        text(
            "delete from insurance_policy_persons where id = :link_id "
            "and household_id = :household_id and policy_id = :policy_id"
        ),
        {
            "link_id": str(link_id),
            "household_id": str(household_id),
            "policy_id": str(policy_id),
        },
    )
    if _rowcount(result) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_PERSON_LINK_NOT_FOUND
        )


# ---------------------------------------------------------------------------
# Deckungsbausteine
# ---------------------------------------------------------------------------

_COVERAGE_COLUMNS: Final = """
  cov.id, cov.policy_id, cov.coverage_key, k.label, k.purpose_short,
  cov.is_included, cov.sum_insured_minor, cov.deductible_minor,
  cov.benefit_rate_bp, cov.waiting_until, cov.note, cov.created_at, cov.updated_at
"""


async def list_coverages(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> list[PolicyCoverage]:
    await _require_policy(session, household_id, policy_id)
    rows = (
        await session.execute(
            text(
                f"select {_COVERAGE_COLUMNS} from insurance_coverages cov "
                "join insurance_coverage_keys k on k.coverage_key = cov.coverage_key "
                "where cov.household_id = :household_id and cov.policy_id = :policy_id "
                "order by k.sort_order, k.label"
            ),
            {"household_id": str(household_id), "policy_id": str(policy_id)},
        )
    ).all()
    return [PolicyCoverage(**dict(row._mapping)) for row in rows]


async def replace_coverages(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: PolicyCoverageReplacement,
) -> list[PolicyCoverage]:
    """Ersetzt die Bausteinmenge einer Police in einem Rundlauf.

    Kein Loeschen und Neuanlegen: bestehende Zeilen werden aktualisiert, damit
    `created_at` erhalten bleibt. Wer wissen will, seit wann ein Baustein
    erfasst ist, soll es nach jedem Speichern noch beantwortet bekommen.
    """
    await _require_policy(session, household_id, policy_id)
    schluessel = [eintrag.coverage_key for eintrag in payload.coverages]

    if schluessel:
        bekannt = {
            str(row.coverage_key)
            for row in (
                await session.execute(
                    text(
                        "select coverage_key from insurance_coverage_keys "
                        "where coverage_key = any(cast(:keys as text[]))"
                    ),
                    {"keys": schluessel},
                )
            ).all()
        }
        unbekannt = sorted(set(schluessel) - bekannt)
        if unbekannt:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": INSURANCE_COVERAGE_KEY_UNKNOWN, "coverage_keys": unbekannt},
            )

    # Zuerst raeumen, dann schreiben: die Reihenfolge macht "PUT ersetzt die
    # Menge" auch dann wahr, wenn die Menge leer ist.
    await session.execute(
        text(
            "delete from insurance_coverages where household_id = :household_id "
            "and policy_id = :policy_id "
            "and not (coverage_key = any(cast(:keys as text[])))"
        ),
        {
            "household_id": str(household_id),
            "policy_id": str(policy_id),
            "keys": schluessel,
        },
    )

    for eintrag in payload.coverages:
        await session.execute(
            text(
                "insert into insurance_coverages (household_id, policy_id, coverage_key, "
                " is_included, sum_insured_minor, deductible_minor, benefit_rate_bp, "
                " waiting_until, note) values "
                "(:household_id, :policy_id, :coverage_key, :is_included, :sum_insured_minor, "
                " :deductible_minor, :benefit_rate_bp, :waiting_until, :note) "
                "on conflict (household_id, policy_id, coverage_key) do update set "
                "  is_included = excluded.is_included, "
                "  sum_insured_minor = excluded.sum_insured_minor, "
                "  deductible_minor = excluded.deductible_minor, "
                "  benefit_rate_bp = excluded.benefit_rate_bp, "
                "  waiting_until = excluded.waiting_until, "
                "  note = excluded.note, "
                "  updated_at = now()"
            ),
            {
                "household_id": str(household_id),
                "policy_id": str(policy_id),
                "coverage_key": eintrag.coverage_key,
                "is_included": eintrag.is_included,
                "sum_insured_minor": eintrag.sum_insured_minor,
                "deductible_minor": eintrag.deductible_minor,
                "benefit_rate_bp": eintrag.benefit_rate_bp,
                "waiting_until": eintrag.waiting_until,
                "note": eintrag.note,
            },
        )

    return await list_coverages(session, household_id, policy_id)


# ---------------------------------------------------------------------------
# Objekte
# ---------------------------------------------------------------------------


def _as_object(row: Row[Any]) -> InsuredObject:
    data = dict(row._mapping)
    data["policy_count"] = int(data.get("policy_count") or 0)
    return InsuredObject(**data)


async def list_objects(
    session: AsyncSession,
    household_id: UUID,
    *,
    kind: str | None = None,
    include_disposed: bool = False,
) -> list[InsuredObject]:
    bedingungen = ["o.household_id = :household_id"]
    parameter: dict[str, Any] = {"household_id": str(household_id)}
    if kind is not None:
        bedingungen.append("o.kind = :kind")
        parameter["kind"] = kind
    if not include_disposed:
        # Ein abgegebenes Objekt bleibt erhalten - sonst verloeren alte Schaeden
        # ihren Bezug -, es steht nur nicht mehr in der Auswahl.
        bedingungen.append("o.is_active and o.disposed_on is null")
    rows = (
        await session.execute(
            text(
                f"select {_OBJECT_COLUMNS} {_OBJECT_FROM} where "
                + " and ".join(bedingungen)
                + " order by o.kind, lower(o.label)"
            ),
            parameter,
        )
    ).all()
    return [_as_object(row) for row in rows]


async def _require_object_row(
    session: AsyncSession, household_id: UUID, object_id: UUID
) -> Row[Any]:
    row = (
        await session.execute(
            text(
                f"select {_OBJECT_COLUMNS} {_OBJECT_FROM} "
                "where o.id = :object_id and o.household_id = :household_id"
            ),
            {"object_id": str(object_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_OBJECT_NOT_FOUND
        )
    return row


async def get_object(
    session: AsyncSession, household_id: UUID, object_id: UUID
) -> InsuredObject:
    return _as_object(await _require_object_row(session, household_id, object_id))


async def create_object(
    session: AsyncSession, household_id: UUID, payload: InsuredObjectCreate
) -> InsuredObject:
    werte: dict[str, Any] = {
        "household_id": str(household_id),
        "kind": payload.kind,
        "label": payload.label.strip(),
        "asset_id": str(payload.asset_id) if payload.asset_id else None,
        "address_line": payload.address_line,
        "postal_code": payload.postal_code,
        "city": payload.city,
        "living_area_sqm": payload.living_area_sqm,
        "build_year": payload.build_year,
        "attrs": json.dumps(payload.attrs or {}),
        "acquired_on": payload.acquired_on,
        "disposed_on": payload.disposed_on,
    }
    try:
        row = (
            await session.execute(
                text(
                    "insert into insured_objects ("
                    + ", ".join(_OBJECT_INSERT_COLUMNS)
                    + ") values ("
                    + ", ".join(_placeholder(column) for column in _OBJECT_INSERT_COLUMNS)
                    + ") returning id"
                ),
                werte,
            )
        ).first()
    except IntegrityError as exc:
        # Praktisch nur eine Ursache: eine asset_id aus einem fremden Haushalt.
        # Der zusammengesetzte Fremdschluessel faengt sie ab, bevor der Wert im
        # Vermoegensmodul auf etwas zeigt, das dem Haushalt nicht gehoert.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="insurance_object_asset_unknown",
        ) from exc
    assert row is not None  # "returning id" liefert immer eine Zeile
    return await get_object(session, household_id, row.id)


async def update_object(
    session: AsyncSession,
    household_id: UUID,
    object_id: UUID,
    payload: InsuredObjectUpdate,
    *,
    expected: int | None,
) -> InsuredObject:
    vorher = dict((await _require_object_row(session, household_id, object_id))._mapping)
    if expected is not None and int(vorher["version"]) != expected:
        raise_version_conflict(int(vorher["version"]))

    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    changes.pop("clear_fields", None)
    # Ein ausdruecklicher Loeschwunsch ist die spezifischere Angabe und gewinnt
    # gegen einen gleichzeitig gesetzten Wert (Muster aus routes/contracts.py).
    for geleert in payload.clear_fields:
        changes[geleert] = None

    felder = [column for column in _OBJECT_UPDATABLE_COLUMNS if column in changes]
    if not felder:
        # Ein leerer Patch bumpt nicht - der Versionstrigger vergleicht die
        # Zeile und laesst version und updated_at unveraendert.
        return _as_object(await _require_object_row(session, household_id, object_id))

    parameter: dict[str, Any] = {
        column: _param_value(column, changes[column]) for column in felder
    }
    parameter["object_id"] = str(object_id)
    parameter["household_id"] = str(household_id)
    try:
        await session.execute(
            text(
                "update insured_objects set "
                + ", ".join(f"{column} = {_placeholder(column)}" for column in felder)
                + " where id = :object_id and household_id = :household_id"
            ),
            parameter,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="insurance_object_rejected",
        ) from exc
    return await get_object(session, household_id, object_id)


async def delete_object(session: AsyncSession, household_id: UUID, object_id: UUID) -> None:
    await _require_object_row(session, household_id, object_id)
    try:
        await session.execute(
            text(
                "delete from insured_objects where id = :object_id "
                "and household_id = :household_id"
            ),
            {"object_id": str(object_id), "household_id": str(household_id)},
        )
    except IntegrityError as exc:
        # Der Fremdschluessel steht auf RESTRICT: ein Objekt, das noch in einer
        # Police steht, wird nicht weggeloescht. Der fachliche Ausweg ist
        # disposed_on, nicht das Loeschen - sonst verlieren alte Buchungen und
        # Schaeden ihren Bezug.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_OBJECT_IN_USE
        ) from exc


# ---------------------------------------------------------------------------
# Halter- und Kennzeichenhistorie
# ---------------------------------------------------------------------------

_REGISTRATION_COLUMNS: Final = """
  reg.id, reg.insured_object_id, reg.plate, reg.keeper_person_id,
  insured_person_label(reg.keeper_person_id) as keeper_name,
  reg.valid_from, reg.valid_to, reg.note, reg.created_at
"""


async def list_registrations(
    session: AsyncSession, household_id: UUID, object_id: UUID
) -> list[ObjectRegistration]:
    await _require_object_row(session, household_id, object_id)
    rows = (
        await session.execute(
            text(
                f"select {_REGISTRATION_COLUMNS} from insured_object_registrations reg "
                "where reg.household_id = :household_id "
                "and reg.insured_object_id = :object_id "
                "order by reg.valid_from desc, reg.created_at desc"
            ),
            {"household_id": str(household_id), "object_id": str(object_id)},
        )
    ).all()
    return [ObjectRegistration(**dict(row._mapping)) for row in rows]


async def add_registration(
    session: AsyncSession,
    household_id: UUID,
    object_id: UUID,
    payload: ObjectRegistrationCreate,
) -> ObjectRegistration:
    """Schliesst die laufende Zeile und oeffnet eine neue.

    Der Wechsel ist nie ein Ueberschreiben: die alte Zeile endet am Vortag des
    neuen Beginns, damit sich die Zeitraeume nicht ueberlappen und ein Schaden
    aus dem Vorjahr weiter dem damaligen Kennzeichen zugeordnet bleibt.
    """
    await _require_object_row(session, household_id, object_id)
    if payload.keeper_person_id is not None:
        await _person_label(session, payload.keeper_person_id)

    laufend = (
        await session.execute(
            text(
                "select id, valid_from from insured_object_registrations "
                "where household_id = :household_id and insured_object_id = :object_id "
                "and valid_to is null for update"
            ),
            {"household_id": str(household_id), "object_id": str(object_id)},
        )
    ).first()
    if laufend is not None:
        if payload.valid_from <= laufend.valid_from:
            # Sonst muesste die laufende Zeile am Tag vor ihrem eigenen Beginn
            # enden. Die Historie soll eine Reihenfolge behaupten, die sie auch
            # halten kann - deshalb eine Rueckfrage statt einer stillen
            # Korrektur.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=INSURANCE_REGISTRATION_OUT_OF_ORDER,
            )
        await session.execute(
            text(
                "update insured_object_registrations set valid_to = :valid_to "
                "where id = :id and household_id = :household_id"
            ),
            {
                "valid_to": payload.valid_from - timedelta(days=1),
                "id": str(laufend.id),
                "household_id": str(household_id),
            },
        )

    row = (
        await session.execute(
            text(
                "insert into insured_object_registrations (household_id, insured_object_id, "
                " plate, keeper_person_id, valid_from, note) values "
                "(:household_id, :object_id, :plate, cast(:keeper_person_id as uuid), "
                " :valid_from, :note) returning id"
            ),
            {
                "household_id": str(household_id),
                "object_id": str(object_id),
                "plate": payload.plate.strip() if payload.plate else None,
                "keeper_person_id": (
                    str(payload.keeper_person_id) if payload.keeper_person_id else None
                ),
                "valid_from": payload.valid_from,
                "note": payload.note,
            },
        )
    ).first()
    assert row is not None  # "returning id" liefert immer eine Zeile
    created = (
        await session.execute(
            text(
                f"select {_REGISTRATION_COLUMNS} from insured_object_registrations reg "
                "where reg.id = :id"
            ),
            {"id": str(row.id)},
        )
    ).one()
    return ObjectRegistration(**dict(created._mapping))


# ---------------------------------------------------------------------------
# Police zu Objekt
# ---------------------------------------------------------------------------

_POLICY_OBJECT_COLUMNS: Final = """
  po.id, po.policy_id, po.insured_object_id, o.kind, o.label,
  o.acquired_on, o.disposed_on,
  (select reg.plate from insured_object_registrations reg
    where reg.insured_object_id = o.id and reg.household_id = o.household_id
      and reg.valid_to is null
    order by reg.valid_from desc limit 1) as current_plate,
  po.note, po.created_at
"""


async def list_policy_objects(
    session: AsyncSession, household_id: UUID, policy_id: UUID
) -> list[PolicyObject]:
    await _require_policy(session, household_id, policy_id)
    rows = (
        await session.execute(
            text(
                f"select {_POLICY_OBJECT_COLUMNS} from insurance_policy_objects po "
                "join insured_objects o on o.id = po.insured_object_id "
                "  and o.household_id = po.household_id "
                "where po.household_id = :household_id and po.policy_id = :policy_id "
                "order by o.kind, lower(o.label)"
            ),
            {"household_id": str(household_id), "policy_id": str(policy_id)},
        )
    ).all()
    return [PolicyObject(**dict(row._mapping)) for row in rows]


async def attach_object(
    session: AsyncSession, household_id: UUID, policy_id: UUID, payload: PolicyObjectCreate
) -> PolicyObject:
    await _require_policy(session, household_id, policy_id)
    if payload.new_object is not None:
        object_id = (await create_object(session, household_id, payload.new_object)).id
    else:
        assert payload.insured_object_id is not None  # vom Modellvalidator gesichert
        object_id = payload.insured_object_id
        await _require_object_row(session, household_id, object_id)

    try:
        row = (
            await session.execute(
                text(
                    "insert into insurance_policy_objects (household_id, policy_id, "
                    " insured_object_id, note) values "
                    "(:household_id, :policy_id, :object_id, :note) returning id"
                ),
                {
                    "household_id": str(household_id),
                    "policy_id": str(policy_id),
                    "object_id": str(object_id),
                    "note": payload.note,
                },
            )
        ).first()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=INSURANCE_OBJECT_ALREADY_LINKED
        ) from exc

    assert row is not None  # "returning id" liefert immer eine Zeile
    created = (
        await session.execute(
            text(
                f"select {_POLICY_OBJECT_COLUMNS} from insurance_policy_objects po "
                "join insured_objects o on o.id = po.insured_object_id "
                "  and o.household_id = po.household_id "
                "where po.id = :id"
            ),
            {"id": str(row.id)},
        )
    ).one()
    return PolicyObject(**dict(created._mapping))


async def detach_object(
    session: AsyncSession, household_id: UUID, policy_id: UUID, object_id: UUID
) -> None:
    """Loest die Zuordnung, nicht das Objekt.

    Das Objekt bleibt mit seiner Historie bestehen: es kann an einer zweiten
    Police haengen, und seine Kennzeichenzeilen tragen die Zuordnung alter
    Vorgaenge.
    """
    await _require_policy(session, household_id, policy_id)
    result = await session.execute(
        text(
            "delete from insurance_policy_objects where household_id = :household_id "
            "and policy_id = :policy_id and insured_object_id = :object_id"
        ),
        {
            "household_id": str(household_id),
            "policy_id": str(policy_id),
            "object_id": str(object_id),
        },
    )
    if _rowcount(result) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=INSURANCE_OBJECT_LINK_NOT_FOUND
        )


# ---------------------------------------------------------------------------
# Buendel (Plan 3.9)
# ---------------------------------------------------------------------------


async def set_bundle_parent(
    session: AsyncSession,
    household_id: UUID,
    policy_id: UUID,
    payload: PolicyBundle,
    *,
    today: date,
    expected: tuple[int, int] | None,
) -> tuple[PolicyBundleResult, str]:
    """Haengt eine Police unter eine Hauptpolice - oder loest das Buendel.

    Eigene Einfeldaktion und kein Feld im PATCH: die Zuordnung aendert, welche
    Police der Nummern-Index als Wurzel sieht, und damit die Frage, ob eine
    zweite Police dieselbe Versicherungsscheinnummer tragen darf. Das ist eine
    Entscheidung, keine Feldkorrektur.
    """
    vorher = await _require_policy(session, household_id, policy_id)
    if expected is not None:
        aktuell = (int(vorher["contract_version"]), int(vorher["version"]))
        if aktuell != expected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="version_conflict",
                headers={"ETag": insurance_service.policy_etag(*aktuell)},
            )

    if payload.parent_policy_id is not None:
        if payload.parent_policy_id == policy_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=INSURANCE_POLICY_SELF_REFERENCE,
            )
        # Eigene Vorpruefung statt Verlass auf den Fremdschluessel: eine fremde
        # oder gesundheitsgeschuetzte Police soll 404 liefern und nicht 23503.
        await _require_policy(session, household_id, payload.parent_policy_id)

    try:
        await session.execute(
            text(
                "update insurance_policies set parent_policy_id = cast(:parent as uuid) "
                "where id = :policy_id and household_id = :household_id"
            ),
            {
                "parent": (
                    str(payload.parent_policy_id) if payload.parent_policy_id else None
                ),
                "policy_id": str(policy_id),
                "household_id": str(household_id),
            },
        )
    except IntegrityError as exc:
        message = str(getattr(exc, "orig", exc))
        if "INSURANCE_POLICY_NESTING_TOO_DEEP" in message:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=INSURANCE_POLICY_NESTING_TOO_DEEP,
            ) from exc
        if "insurance_policies_number_uniq" in message:
            # Beim Aufloesen eines Buendels wird die Kindpolice wieder Wurzel -
            # und teilt sich die Nummer dann mit ihrer bisherigen Hauptpolice.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=insurance_service.INSURANCE_POLICY_NUMBER_DUPLICATE,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insurance_bundle_rejected"
        ) from exc

    aktualisiert = await insurance_service.get_policy(
        session, household_id, policy_id, today=today
    )
    hinweis = (
        "Die Versicherungsscheinnummer dieser Police gehoert jetzt zum Buendel und muss "
        "nicht mehr eindeutig sein."
        if payload.parent_policy_id is not None
        else "Die Police ist wieder eigenstaendig; ihre Versicherungsscheinnummer muss "
        "wieder eindeutig sein."
    )
    return (
        PolicyBundleResult(
            policy=aktualisiert.policy,
            parent_policy_id=payload.parent_policy_id,
            hint=hinweis,
        ),
        aktualisiert.etag,
    )
