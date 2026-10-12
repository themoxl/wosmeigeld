"""Gespeicherte Haushaltsszenarien
(docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 7).

Hier wird NICHT gerechnet. Ein Szenario ist eine gespeicherte Aenderungsliste;
die Zahlen dazu liefert derselbe Rechenkern, den auch `POST /forecast/simulate`
benutzt (Leitsatz 2: genau ein Rechenkern). Dieses Modul laedt, schreibt und
reicht weiter - mehr nicht. Waere hier eine zweite Rechnung, gaebe es zwei
Wahrheiten fuer dieselbe Frage.

Ab hier sind Haushaltsszenarien das kanonische System (Plan Abschnitt 11).
`contract_scenarios` ist der Spezialfall "Fixkosten senken" und in der Tabelle
bereits uebernommen (`origin = 'contract_scenario'`); seine Endpunkte bleiben
lesbar, sind aber als `deprecated` markiert.

Drei Regeln, die man beim Lesen leicht uebersieht:

1. `include_in_forecast = false` filtert die Zeile HIER heraus, vor dem
   Rechenlauf. Der Kern kennt dieses Feld nicht und darf keine
   Ablageeigenschaft auswerten.
2. Ein Zielvertrag, der nicht mehr mitzaehlt, ist ein Fehler und kein stilles
   Weglassen. Wuerde die Zeile uebergangen, zeigte die Oberflaeche Zahlen, die
   zur gespeicherten Liste nicht passen (Leitsatz 6).
3. Die Fremdschluessel der Tabelle sind `deferrable initially deferred` - ein
   unbekannter Zielvertrag schlaege also erst beim COMMIT zu, lange nach der
   Antwort. Deshalb prueft `_assert_targets_exist` ausdruecklich vorher, statt
   sich auf die Datenbank zu verlassen.

Zur Nebenlaeufigkeit: die Transaktion umschliesst den gesamten Request
(`auth/dependencies.get_household_session`), deshalb committet hier nichts.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any, Final
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import raise_version_conflict
from app.schemas.forecast import (
    ForecastChange,
    ForecastResponse,
    ForecastSimulationResponse,
)
from app.schemas.scenario import (
    HouseholdScenario,
    HouseholdScenarioChange,
    HouseholdScenarioChangeInput,
    HouseholdScenarioCreate,
    HouseholdScenarioDetail,
    HouseholdScenarioUpdate,
    ScenarioComparisonEntry,
    ScenarioComparisonResponse,
)
from app.services import household_forecast_service

SCENARIO_NOT_FOUND: Final = "household_scenario_not_found"

# Mehr als vier Linien in einem Diagramm sind nicht mehr lesbar (Plan
# Abschnitt 3: "Vergleich von bis zu vier"). Die Grenze steht hier und nicht
# als Zahl in der Route.
MAX_COMPARE_SCENARIOS: Final = 4

# Anhang an den Namen einer Kopie. Der Name ist auf 120 Zeichen begrenzt,
# deshalb wird der Rumpf notfalls gekuerzt statt die Kopie scheitern zu lassen.
_COPY_SUFFIX: Final = " (Kopie)"
_MAX_NAME_LENGTH: Final = 120

# Feste Spaltenliste statt der Payload-Schluessel, damit kein Feldname
# ungeprueft in ein SET-Fragment wandert (Muster aus routes/contracts.py).
# `origin` und `legacy_contract_scenario_id` fehlen bewusst - sie sind Nachweis
# der Uebernahme, keine Nutzereingabe.
_UPDATABLE_COLUMNS: Final = ("name", "description", "status", "horizon_months", "color_key")

_SCENARIO_COLUMNS: Final = (
    "s.id, s.name, s.description, s.status, s.horizon_months, s.color_key, "
    "s.origin, s.created_at, s.updated_at, s.version, "
    "(select count(*) from household_scenario_changes c "
    "where c.household_id = s.household_id and c.scenario_id = s.id) as change_count"
)

# `person_id` steht bewusst in dieser Liste: `ForecastChange` traegt es seit
# der Personenzuordnung (Stufe 6). Wuerde es hier fehlen, ginge es beim
# Speichern still verloren und der Steuerklassenhinweis fiele auf "andere
# Person" zurueck - ein Fehler, den niemand bemerkt haette.
_CHANGE_COLUMNS: Final = (
    "id, change_type, target_contract_id, label, amount_mode, amount_minor, "
    "amount_percent, frequency, due_day, income_kind, person_id, starts_on, "
    "ends_on, confidence, include_in_forecast, sort_order, note"
)

_INSERT_CHANGE: Final = (
    "insert into household_scenario_changes ("
    "household_id, scenario_id, change_type, target_contract_id, label, "
    "amount_mode, amount_minor, amount_percent, frequency, due_day, income_kind, "
    "person_id, starts_on, ends_on, confidence, include_in_forecast, sort_order, "
    "note) "
    "values (:household_id, cast(:scenario_id as uuid), :change_type, "
    "cast(:target_contract_id as uuid), :label, :amount_mode, :amount_minor, "
    ":amount_percent, :frequency, :due_day, :income_kind, "
    "cast(:person_id as uuid), :starts_on, :ends_on, "
    ":confidence, :include_in_forecast, :sort_order, :note)"
)


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


def _row_to_scenario(row: Any) -> HouseholdScenario:
    return HouseholdScenario(
        id=UUID(str(row.id)),
        name=str(row.name),
        description=row.description,
        status=row.status,
        horizon_months=int(row.horizon_months) if row.horizon_months is not None else None,
        color_key=row.color_key,
        origin=row.origin,
        change_count=int(row.change_count),
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=int(row.version),
    )


def _row_to_change(row: Any) -> HouseholdScenarioChange:
    return HouseholdScenarioChange(
        id=UUID(str(row.id)),
        change_type=row.change_type,
        target_contract_id=(
            UUID(str(row.target_contract_id)) if row.target_contract_id is not None else None
        ),
        label=str(row.label),
        amount_mode=row.amount_mode,
        amount_minor=int(row.amount_minor) if row.amount_minor is not None else None,
        amount_percent=row.amount_percent,
        frequency=row.frequency,
        due_day=int(row.due_day) if row.due_day is not None else None,
        income_kind=row.income_kind,
        person_id=UUID(str(row.person_id)) if row.person_id is not None else None,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        confidence=row.confidence,
        include_in_forecast=bool(row.include_in_forecast),
        sort_order=int(row.sort_order),
        note=row.note,
    )


async def _fetch_scenario(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> HouseholdScenario:
    row = (
        await session.execute(
            text(
                f"select {_SCENARIO_COLUMNS} from household_scenarios s "
                "where s.household_id = :household_id and s.id = :scenario_id"
            ),
            {"household_id": str(household_id), "scenario_id": str(scenario_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_NOT_FOUND)
    return _row_to_scenario(row)


async def _fetch_changes(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    *,
    only_included: bool = False,
) -> tuple[HouseholdScenarioChange, ...]:
    """Die Zeilen eines Szenarios in Anzeigereihenfolge.

    `only_included` ist die Umsetzung von Regel 1: abgewaehlte Zeilen fallen
    HIER heraus und nicht im Rechenkern.
    """
    condition = " and include_in_forecast" if only_included else ""
    result = await session.execute(
        text(
            f"select {_CHANGE_COLUMNS} from household_scenario_changes "
            "where household_id = :household_id and scenario_id = :scenario_id"
            f"{condition} order by sort_order, created_at, id"
        ),
        {"household_id": str(household_id), "scenario_id": str(scenario_id)},
    )
    return tuple(_row_to_change(row) for row in result)


async def _detail(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> HouseholdScenarioDetail:
    scenario = await _fetch_scenario(session, household_id, scenario_id)
    changes = await _fetch_changes(session, household_id, scenario_id)
    return HouseholdScenarioDetail(**scenario.model_dump(), changes=changes)


async def list_scenarios(
    session: AsyncSession, household_id: UUID
) -> list[HouseholdScenario]:
    """Aktive zuerst, dann Entwuerfe, archivierte zuletzt - innerhalb einer
    Gruppe alphabetisch. Ohne die Statusordnung stuende ein archiviertes
    Szenario zwischen den beiden, mit denen tatsaechlich gearbeitet wird."""
    result = await session.execute(
        text(
            f"select {_SCENARIO_COLUMNS} from household_scenarios s "
            "where s.household_id = :household_id "
            "order by case s.status when 'active' then 0 when 'draft' then 1 else 2 end, "
            "lower(s.name)"
        ),
        {"household_id": str(household_id)},
    )
    return [_row_to_scenario(row) for row in result]


async def get_scenario(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> HouseholdScenarioDetail:
    return await _detail(session, household_id, scenario_id)


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


async def _assert_targets_exist(
    session: AsyncSession, household_id: UUID, changes: Sequence[ForecastChange]
) -> None:
    """Zielvertraege pruefen, BEVOR geschrieben wird.

    Der Fremdschluessel ist `deferrable initially deferred`; ohne diese Pruefung
    schlaege ein unbekannter Vertrag erst beim COMMIT zu - also nach der Antwort
    und damit als 500 statt als 400.
    """
    await _assert_references(
        session,
        household_id,
        table="contracts",
        ids={
            change.target_contract_id
            for change in changes
            if change.target_contract_id is not None
        },
        label="Zielvertrag",
    )
    # Dieselbe Falle bei der Personenzuordnung: auch dieser Fremdschluessel ist
    # aufgeschoben.
    await _assert_references(
        session,
        household_id,
        table="household_people",
        ids={
            change.person_id for change in changes if change.person_id is not None
        },
        label="Person",
    )


async def _assert_references(
    session: AsyncSession,
    household_id: UUID,
    *,
    table: str,
    ids: set[UUID],
    label: str,
) -> None:
    if not ids:
        return
    # `table` kommt ausschliesslich aus den beiden Aufrufen oben, nie aus einer
    # Eingabe.
    result = await session.execute(
        text(
            f"select id from {table} where household_id = :household_id "
            "and id = any(cast(:ids as uuid[]))"
        ),
        {"household_id": str(household_id), "ids": [str(item) for item in ids]},
    )
    missing = ids - {UUID(str(row.id)) for row in result}
    if missing:
        raise ValueError(
            f"Mindestens ein {label} gehoert nicht zu diesem Haushalt: "
            + ", ".join(sorted(str(item) for item in missing))
        )


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostics = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(diagnostics, "constraint_name", None)
    return str(name) if name else None


async def _insert_changes(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    changes: Sequence[HouseholdScenarioChangeInput],
) -> None:
    for index, change in enumerate(changes):
        try:
            await session.execute(
                text(_INSERT_CHANGE),
                {
                    "household_id": str(household_id),
                    "scenario_id": str(scenario_id),
                    "change_type": change.change_type,
                    "target_contract_id": (
                        str(change.target_contract_id)
                        if change.target_contract_id is not None
                        else None
                    ),
                    "label": change.label,
                    "amount_mode": change.amount_mode,
                    "amount_minor": change.amount_minor,
                    "amount_percent": change.amount_percent,
                    "frequency": change.frequency,
                    "due_day": change.due_day,
                    "income_kind": change.income_kind,
                    "person_id": (
                        str(change.person_id) if change.person_id is not None else None
                    ),
                    "starts_on": change.starts_on,
                    "ends_on": change.ends_on,
                    "confidence": change.confidence,
                    "include_in_forecast": change.include_in_forecast,
                    "sort_order": (
                        change.sort_order if change.sort_order is not None else index
                    ),
                    "note": change.note,
                },
            )
        except IntegrityError as exc:
            # Die CHECK-Constraints der Tabelle sind die zweite Instanz hinter
            # `ForecastChange` - sie greifen auch dort, wo das Eingabemodell
            # (noch) nichts prueft, etwa bei `one_off` mit `ends_on`. Eine
            # verletzte Regel ist ein Eingabefehler und muss als 400
            # ankommen, nicht als 500.
            constraint = _constraint_name(exc)
            detail = f" ({constraint})" if constraint else ""
            raise ValueError(
                f"Aenderung {index + 1} verletzt eine Regel der Szenariotabelle{detail}."
            ) from exc


async def _touch_scenario(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> None:
    """Hebt Version und Zeitstempel ueber den Trigger.

    Auch dann, wenn nur die Zeilen gewechselt haben: die Version soll den Plan
    beschreiben und nicht nur seine Ueberschrift - sonst haelt ein Client mit
    altem ETag eine laengst ersetzte Aenderungsliste fuer aktuell.
    """
    await session.execute(
        text(
            "update household_scenarios set updated_at = now() "
            "where household_id = :household_id and id = :scenario_id"
        ),
        {"household_id": str(household_id), "scenario_id": str(scenario_id)},
    )


async def create_scenario(
    session: AsyncSession,
    household_id: UUID,
    payload: HouseholdScenarioCreate,
    *,
    created_by: UUID | None = None,
) -> HouseholdScenarioDetail:
    changes = [
        HouseholdScenarioChangeInput.model_validate(change.model_dump())
        for change in payload.changes
    ]
    await _assert_targets_exist(session, household_id, changes)

    result = await session.execute(
        text(
            "insert into household_scenarios (household_id, name, description, status, "
            "horizon_months, color_key, origin, created_by) "
            "values (:household_id, :name, :description, :status, :horizon_months, "
            ":color_key, 'manual', cast(:created_by as uuid)) returning id"
        ),
        {
            "household_id": str(household_id),
            "name": payload.name.strip(),
            "description": payload.description,
            "status": payload.status,
            "horizon_months": payload.horizon_months,
            "color_key": payload.color_key,
            "created_by": str(created_by) if created_by is not None else None,
        },
    )
    scenario_id = UUID(str(result.scalar_one()))
    await _insert_changes(session, household_id, scenario_id, changes)
    return await _detail(session, household_id, scenario_id)


async def update_scenario(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    payload: HouseholdScenarioUpdate,
    *,
    expected_version: int | None = None,
) -> HouseholdScenarioDetail:
    current = await _fetch_scenario(session, household_id, scenario_id)
    if expected_version is not None and current.version != expected_version:
        raise_version_conflict(current.version)

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is None:
        # `name` ist NOT NULL - ein ausdrueckliches null heisst hier "nicht
        # anfassen" und nicht "Namen loeschen" (gleiche Lesart wie bei den
        # Kategorien).
        changes.pop("name", None)
    if changes.get("status") is None:
        changes.pop("status", None)

    assignments: list[str] = []
    params: dict[str, Any] = {
        "household_id": str(household_id),
        "scenario_id": str(scenario_id),
    }
    for column in _UPDATABLE_COLUMNS:
        if column not in changes:
            continue
        assignments.append(f"{column} = :{column}")
        params[column] = changes[column]

    if not assignments:
        # Ohne Aenderung keine neue Version - sonst entwertete ein leeres PATCH
        # das ETag aller anderen Clients.
        return await _detail(session, household_id, scenario_id)

    await session.execute(
        text(
            f"update household_scenarios set {', '.join(assignments)} "
            "where household_id = :household_id and id = :scenario_id"
        ),
        params,
    )
    return await _detail(session, household_id, scenario_id)


async def delete_scenario(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> None:
    await _fetch_scenario(session, household_id, scenario_id)
    await session.execute(
        text(
            "delete from household_scenarios "
            "where household_id = :household_id and id = :scenario_id"
        ),
        {"household_id": str(household_id), "scenario_id": str(scenario_id)},
    )


def _copy_name(name: str) -> str:
    room = _MAX_NAME_LENGTH - len(_COPY_SUFFIX)
    return f"{name[:room].rstrip()}{_COPY_SUFFIX}"


async def duplicate_scenario(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    *,
    created_by: UUID | None = None,
) -> HouseholdScenarioDetail:
    """Kopie mit allen Zeilen, aber ohne Herkunft und ohne Status.

    Die Kopie ist immer `manual` und immer ein Entwurf. Beides ist nicht
    Geschmackssache: `legacy_contract_scenario_id` traegt einen eindeutigen
    Index (ein Fixkostenszenario darf hoechstens einmal uebernommen sein), und
    eine zweite aktive Variante desselben Plans waere eine Aussage, die niemand
    getroffen hat.
    """
    source = await _fetch_scenario(session, household_id, scenario_id)
    result = await session.execute(
        text(
            "insert into household_scenarios (household_id, name, description, status, "
            "horizon_months, color_key, origin, created_by) "
            "values (:household_id, :name, :description, 'draft', :horizon_months, "
            ":color_key, 'manual', cast(:created_by as uuid)) returning id"
        ),
        {
            "household_id": str(household_id),
            "name": _copy_name(source.name),
            "description": source.description,
            "horizon_months": source.horizon_months,
            "color_key": source.color_key,
            "created_by": str(created_by) if created_by is not None else None,
        },
    )
    copy_id = UUID(str(result.scalar_one()))

    # Kopiert wird in der Datenbank, nicht ueber das Eingabemodell: die Quelle
    # darf Zeilen enthalten, die `ForecastChange` heute nicht mehr annaehme
    # (siehe HouseholdScenarioChange). Eine Kopie, die daran scheitert, waere
    # der falsche Ort fuer diese Strenge.
    await session.execute(
        text(
            "insert into household_scenario_changes ("
            "household_id, scenario_id, change_type, target_contract_id, label, "
            "amount_mode, amount_minor, amount_percent, frequency, due_day, "
            "income_kind, person_id, starts_on, ends_on, confidence, "
            "include_in_forecast, sort_order, note) "
            "select household_id, cast(:copy_id as uuid), change_type, "
            "target_contract_id, label, amount_mode, amount_minor, amount_percent, "
            "frequency, due_day, income_kind, person_id, starts_on, ends_on, "
            "confidence, include_in_forecast, sort_order, note "
            "from household_scenario_changes "
            "where household_id = :household_id and scenario_id = :scenario_id"
        ),
        {
            "household_id": str(household_id),
            "scenario_id": str(scenario_id),
            "copy_id": str(copy_id),
        },
    )
    return await _detail(session, household_id, copy_id)


async def replace_changes(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    changes: Sequence[HouseholdScenarioChangeInput],
) -> HouseholdScenarioDetail:
    """Ersetzt die Liste vollstaendig (Muster `PUT /budgets/.../allocations`).

    Kein Zusammenfuehren ueber `id`: eine Aenderungsliste ist ein Entwurf, den
    die Oberflaeche als Ganzes bearbeitet. Ein Teilabgleich brauchte stabile
    IDs im Editor und brauchte trotzdem eine Regel fuer Zeilen, die der Client
    gar nicht kennt.
    """
    await _fetch_scenario(session, household_id, scenario_id)
    await _assert_targets_exist(session, household_id, changes)

    await session.execute(
        text(
            "delete from household_scenario_changes "
            "where household_id = :household_id and scenario_id = :scenario_id"
        ),
        {"household_id": str(household_id), "scenario_id": str(scenario_id)},
    )
    await _insert_changes(session, household_id, scenario_id, changes)
    await _touch_scenario(session, household_id, scenario_id)
    return await _detail(session, household_id, scenario_id)


# ---------------------------------------------------------------------------
# Rechnen lassen - ueber denselben Kern wie POST /forecast/simulate
# ---------------------------------------------------------------------------


async def build_scenario_forecast(
    session: AsyncSession,
    household_id: UUID,
    scenario_id: UUID,
    *,
    as_of: date,
    include_savings: bool = False,
) -> ForecastSimulationResponse:
    """Basis, Simulation, Differenz und Beitrag je Aenderung - dieselbe Form
    wie `POST /forecast/simulate`, weil es dieselbe Rechnung ist."""
    scenario = await _fetch_scenario(session, household_id, scenario_id)
    changes = await _fetch_changes(
        session, household_id, scenario_id, only_included=True
    )
    # Bewusst `run_simulation` und nicht `simulate_forecast`: der Umweg ueber
    # `ForecastSimulationRequest` wuerde die gespeicherten Zeilen ein zweites
    # Mal durch die EINGABEpruefung schicken und dabei Zeilen ablehnen, die die
    # Tabelle erlaubt (`amount_minor = 0` aus der Uebernahme). Gerechnet wird
    # in beiden Faellen dasselbe.
    return await household_forecast_service.run_simulation(
        session,
        household_id,
        as_of=as_of,
        horizon_months=scenario.horizon_months,
        include_savings=include_savings,
        changes=tuple(changes),
    )


def _unique_in_order(scenario_ids: Sequence[UUID]) -> list[UUID]:
    seen: set[UUID] = set()
    ordered: list[UUID] = []
    for scenario_id in scenario_ids:
        if scenario_id in seen:
            continue
        seen.add(scenario_id)
        ordered.append(scenario_id)
    return ordered


async def compare_scenarios(
    session: AsyncSession,
    household_id: UUID,
    scenario_ids: Sequence[UUID],
    *,
    as_of: date,
    include_savings: bool = False,
) -> ScenarioComparisonResponse:
    """Mehrere Szenarien gegen EINE Basis.

    Die Eingaben werden genau einmal geladen und dann je Szenario mit
    `apply_changes` veraendert. Ein zweiter Ladelauf je Szenario waere nicht nur
    langsamer, er koennte auch eine andere Basis liefern - und ein Vergleich
    mit wandernder Basis ist keiner.

    Der Horizont der einzelnen Szenarien wird hier bewusst ignoriert: vier
    Linien in einem Diagramm brauchen eine gemeinsame Zeitachse. Es gilt der
    Horizont des Haushalts aus `forecast_settings`.
    """
    ordered = _unique_in_order(scenario_ids)
    if not ordered:
        raise ValueError("Bitte mindestens ein Szenario angeben (ids).")
    if len(ordered) > MAX_COMPARE_SCENARIOS:
        raise ValueError(
            f"Es lassen sich hoechstens {MAX_COMPARE_SCENARIOS} Szenarien "
            f"vergleichen, angefragt waren {len(ordered)}."
        )

    base_input = await household_forecast_service.load_forecast_input(
        session, household_id, as_of=as_of, include_savings=include_savings
    )
    base_result: ForecastResponse = household_forecast_service.calculate_forecast(
        base_input, granularity="day"
    )
    known_contracts = {term.contract_id for term in base_input.contract_terms}

    entries: list[ScenarioComparisonEntry] = []
    for scenario_id in ordered:
        scenario = await _fetch_scenario(session, household_id, scenario_id)
        changes = await _fetch_changes(
            session, household_id, scenario_id, only_included=True
        )
        missing = {
            change.target_contract_id
            for change in changes
            if change.target_contract_id is not None
        } - known_contracts
        if missing:
            raise ValueError(
                f"Szenario '{scenario.name}' zielt auf mindestens einen Vertrag, "
                "der nicht mehr mitzaehlt."
            )
        simulation = household_forecast_service.calculate_forecast(
            household_forecast_service.apply_changes(base_input, tuple(changes)),
            granularity="day",
        )
        entries.append(
            ScenarioComparisonEntry(
                scenario=scenario,
                simulation=simulation,
                comparison=household_forecast_service.compare_forecasts(
                    base_result, simulation
                ),
            )
        )
    return ScenarioComparisonResponse(base=base_result, entries=tuple(entries))
