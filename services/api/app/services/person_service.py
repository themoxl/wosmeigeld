"""Fachlogik der Personenverwaltung (Liquiditaetsplan, Stufe 6).

Muster wie `contract_service`: SQLAlchemy Core mit `text()`, kein ORM, und
kein `commit()` - die Transaktion umschliesst den gesamten Request
(`auth/dependencies.get_household_session`).

Zwei Festlegungen praegen diesen Dienst:

* **Namen sind je Haushalt eindeutig, ohne Ruecksicht auf Gross- und
  Kleinschreibung.** Das ist der Kern der Stufe: "Anna", "anna" und "Anna M."
  waeren als Freitext drei Personen, und die Gruppierung der Einnahmen damit
  wertlos. Der partielle Unique-Index `household_people_name_uniq` ist die
  Absicherung; die Vorpruefung hier existiert, damit der Normalfall eine
  saubere 409 mit erklaerender Meldung liefert statt eines abgebrochenen
  Statements. Der Index wird trotzdem zusaetzlich abgefangen: zwischen
  Pruefung und INSERT kann ein paralleler Request denselben Namen belegen,
  und ein Unique-Verstoss darf unter keinen Umstaenden als 500 beim Nutzer
  ankommen.
* **Loeschen ist ein Soft-Delete.** Ein `person_id` an einem Vertrag darf
  nicht ins Leere zeigen - der Fremdschluessel wuerde ihn sonst auf `null`
  setzen und die Zuordnung waere unwiederbringlich weg. `is_active = false`
  nimmt die Zeile zugleich aus dem partiellen Unique-Index heraus und gibt
  den Namen fuer eine neue Person wieder frei.
"""

from typing import Any, Final
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.person import Person, PersonCreate, PersonUpdate

PERSON_NOT_FOUND: Final = "person_not_found"
PERSON_MEMBER_UNKNOWN: Final = "person_member_unknown"
PERSON_INACTIVE: Final = "person_inactive"

# Ausgeschriebener Satz statt einer blossen Kennung: der Nutzer soll erfahren,
# WARUM "anna" neben "Anna" abgelehnt wird. Ein Fehlercode allein liest sich
# an dieser Stelle wie ein Defekt der Anwendung.
PERSON_DUPLICATE_NAME: Final = (
    "Es gibt in diesem Haushalt bereits eine Person mit diesem Namen - "
    "Gross- und Kleinschreibung zaehlt dabei nicht. Einnahmen sollen sich nach "
    "Person gruppieren lassen, dafuer darf derselbe Mensch nur einmal vorkommen."
)

_PERSON_COLUMNS: Final = (
    "id, household_id, name, member_user_id, sort_order, is_active, created_at, updated_at"
)

# Spalten, die `PATCH` schreiben darf. Feste Liste statt der Payload-
# Schluessel, damit kein Feldname ungeprueft in ein SET-Fragment wandert
# (gleiches Muster wie routes/contracts.py und routes/categories.py).
_UPDATABLE_COLUMNS: Final = ("name", "member_user_id", "sort_order")

_UUID_COLUMNS: Final = frozenset({"member_user_id"})


def _as_person(row: Any) -> Person:
    return Person(**row._mapping)


def _duplicate_name_conflict() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PERSON_DUPLICATE_NAME)


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


async def list_people(
    session: AsyncSession, household_id: UUID, *, include_inactive: bool = False
) -> list[Person]:
    """Personen des Haushalts, standardmaessig nur die aktiven.

    `include_inactive` ist fuer genau einen Fall da: ein Vertrag zeigt noch auf
    eine geloeschte Person, und die Oberflaeche braucht deren Namen, um die
    Einnahme weiterhin richtig zu beschriften. Ohne diese Option landete sie
    unter "Ohne Person" - eine Aussage, die nicht stimmt.
    """
    conditions = ["household_id = :household_id"]
    if not include_inactive:
        conditions.append("is_active")

    result = await session.execute(
        text(
            f"select {_PERSON_COLUMNS} from household_people where "
            + " and ".join(conditions)
            + " order by sort_order asc, name asc"
        ),
        {"household_id": str(household_id)},
    )
    return [_as_person(row) for row in result]


async def load_person(
    session: AsyncSession, household_id: UUID, person_id: UUID
) -> Person | None:
    result = await session.execute(
        text(
            f"select {_PERSON_COLUMNS} from household_people "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(person_id), "household_id": str(household_id)},
    )
    row = result.first()
    return _as_person(row) if row is not None else None


async def require_person(session: AsyncSession, household_id: UUID, person_id: UUID) -> Person:
    person = await load_person(session, household_id, person_id)
    if person is None:
        # Bewusst 404 statt 403 bei fremdem Haushalt: die Existenz einer
        # fremden Ressource soll nicht verraten werden (gleiche Linie wie
        # contract_service.require_contract).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PERSON_NOT_FOUND)
    return person


async def assert_assignable(
    session: AsyncSession, household_id: UUID, person_id: UUID
) -> None:
    """Prueft, ob ein Vertrag dieser Person zugeordnet werden darf.

    Der zusammengesetzte Fremdschluessel `(person_id, household_id)` an
    `contracts` ist `deferrable initially deferred` - eine fremde UUID floege
    erst beim COMMIT auf, also lange nachdem der Handler seine Antwort gebaut
    hat, und erreichte den Client als 500. Diese Pruefung holt den Fehler in
    den Request zurueck.

    Einer bereits geloeschten Person wird nichts Neues mehr zugeordnet:
    bestehende Zuordnungen bleiben (Soft-Delete), neue waeren ein Wiederbeleben
    durch die Hintertuer.
    """
    person = await require_person(session, household_id, person_id)
    if not person.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PERSON_INACTIVE)


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


async def _assert_name_free(
    session: AsyncSession, household_id: UUID, name: str, *, exclude_id: UUID | None = None
) -> None:
    """Vorpruefung gegen `household_people_name_uniq`.

    `lower(name)` steht hier genauso wie im Index - eine abweichende
    Normalisierung wuerde entweder zu frueh ablehnen oder zu spaet, und der
    Nutzer bekaeme fuer denselben Sachverhalt zwei verschiedene Antworten.
    """
    result = await session.execute(
        text(
            "select 1 from household_people where household_id = :household_id "
            "and is_active and lower(name) = lower(:name) "
            "and (cast(:exclude_id as uuid) is null or id <> cast(:exclude_id as uuid)) "
            "limit 1"
        ),
        {
            "household_id": str(household_id),
            "name": name,
            "exclude_id": str(exclude_id) if exclude_id else None,
        },
    )
    if result.first() is not None:
        raise _duplicate_name_conflict()


async def _assert_member(
    session: AsyncSession, household_id: UUID, member_user_id: UUID
) -> None:
    """`member_user_id` darf nur auf ein aktives Mitglied DIESES Haushalts
    zeigen. Der Fremdschluessel geht auf `auth.users` und liesse jeden
    beliebigen fremden Nutzer zu; fachlich waere das eine Person, die im
    Haushalt niemand kennt."""
    result = await session.execute(
        text(
            "select 1 from household_members where household_id = :household_id "
            "and user_id = :user_id and status = 'active' limit 1"
        ),
        {"household_id": str(household_id), "user_id": str(member_user_id)},
    )
    if result.first() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=PERSON_MEMBER_UNKNOWN
        )


async def create_person(
    session: AsyncSession, household_id: UUID, *, payload: PersonCreate
) -> Person:
    await _assert_name_free(session, household_id, payload.name)
    if payload.member_user_id is not None:
        await _assert_member(session, household_id, payload.member_user_id)

    try:
        result = await session.execute(
            text(
                "insert into household_people "
                "(household_id, name, member_user_id, sort_order) "
                "values (:household_id, :name, cast(:member_user_id as uuid), :sort_order) "
                f"returning {_PERSON_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "name": payload.name,
                "member_user_id": (
                    str(payload.member_user_id) if payload.member_user_id else None
                ),
                "sort_order": payload.sort_order,
            },
        )
    except IntegrityError as exc:
        raise _duplicate_name_conflict() from exc

    return _as_person(result.one())


async def update_person(
    session: AsyncSession, household_id: UUID, person_id: UUID, *, payload: PersonUpdate
) -> Person:
    before = await require_person(session, household_id, person_id)
    changes = payload.model_dump(exclude_unset=True)
    # `name` und `sort_order` sind nicht leerbar - ein mitgeschicktes `null`
    # ist dort eine Fehlbedienung des Clients und wird ignoriert, statt eine
    # NOT-NULL-Verletzung auszuloesen. Bei `member_user_id` ist `null` dagegen
    # die ausdrueckliche Loeschung der Verknuepfung.
    for not_nullable in ("name", "sort_order"):
        if changes.get(not_nullable) is None:
            changes.pop(not_nullable, None)

    if "name" in changes and before.is_active:
        # Bei einer geloeschten Person greift der partielle Index nicht; eine
        # Umbenennung dort kann also keinen Konflikt ausloesen.
        await _assert_name_free(
            session, household_id, str(changes["name"]), exclude_id=person_id
        )
    member_change = changes.get("member_user_id")
    if member_change is not None:
        await _assert_member(session, household_id, UUID(str(member_change)))

    assignments: list[str] = []
    params: dict[str, Any] = {"id": str(person_id), "household_id": str(household_id)}
    for column in _UPDATABLE_COLUMNS:
        if column not in changes:
            continue
        value = changes[column]
        if column in _UUID_COLUMNS:
            assignments.append(f"{column} = cast(:{column} as uuid)")
            params[column] = str(value) if value is not None else None
        else:
            assignments.append(f"{column} = :{column}")
            params[column] = value

    if not assignments:
        return before

    # `household_people` haengt an keinem Trigger fuer `updated_at` - die
    # Spalte wird hier ausdruecklich mitgeschrieben, sonst bliebe sie auf dem
    # Anlegezeitpunkt stehen und waere als Ordnungsmerkmal wertlos.
    assignments.append("updated_at = now()")

    try:
        await session.execute(
            text(
                "update household_people set "
                + ", ".join(assignments)
                + " where id = :id and household_id = :household_id"
            ),
            params,
        )
    except IntegrityError as exc:
        raise _duplicate_name_conflict() from exc

    return await require_person(session, household_id, person_id)


async def deactivate_person(
    session: AsyncSession, household_id: UUID, person_id: UUID
) -> None:
    """Soft-Delete. Idempotent: eine bereits geloeschte Person bleibt geloescht.

    Die Vertraege behalten ihr `person_id`. Ein hartes DELETE liesse der
    Fremdschluessel (`on delete set null`) alle Zuordnungen dieser Person
    stillschweigend fallen - genau die Auswertung, um die es in dieser Stufe
    geht, waere danach still falsch.
    """
    await require_person(session, household_id, person_id)
    await session.execute(
        text(
            "update household_people set is_active = false, updated_at = now() "
            "where id = :id and household_id = :household_id and is_active"
        ),
        {"id": str(person_id), "household_id": str(household_id)},
    )
