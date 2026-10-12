"""Endpunkte fuer Laeden, Preissichtungen und den Excel-Austausch.

Bewusst ein eigener Router neben grocery.py: dort steht der Essensplan, hier
die HERKUNFT von Preisen. Die beiden treffen sich an genau einer Stelle -
ingredient_prices.price_minor, der Bewertungspreis, aus dem der bestehende
Rechenkern seine Gerichts- und Wochenkosten zieht.

Zur Excel-Strecke: die Datei ist Transport, nicht Speicher. Der Server
erzeugt eine mit den bekannten Zutaten vorbefuellte Vorlage, liest sie wieder
ein und legt das Ergebnis als VORSCHAU vor. Gespeichert wird erst, wenn der
Nutzer bestaetigt hat - und dann noch einmal durch dieselbe Pruefung wie beim
Einlesen. Dieselbe Haltung wie beim KI-Spaltenmapping des Bankimports: der
Vorschlag wird nie automatisch angewendet.
"""

from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session, require_household_editor
from app.schemas.grocery_price import (
    IngredientPriceDetail,
    ObservationBulkCreate,
    ObservationBulkResult,
    ObservationCreate,
    ObservationResponse,
    PriceMatrixCell,
    PriceSettingsPayload,
    SheetPreviewResult,
    SheetPreviewRow,
    SheetProblem,
    StoreCreate,
    StoreResponse,
    StoreUpdate,
    ValuationState,
    ValuationUpdate,
)
from app.services import grocery_observation_service as observations
from app.services import grocery_price_sheet_service as sheet
from app.services.project_cost_import_security import (
    CostImportSecurityError,
    check_file_size,
    check_xlsx_structure,
)

router = APIRouter(tags=["grocery-prices"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]

_STORE_COLUMNS = "id, name, location, store_kind, is_archived, note, created_at"

_OBSERVATION_SELECT = (
    "o.id, o.ingredient_key, o.ingredient_name, o.store_id, o.brand, o.quality_tier, "
    "o.brand_tier, o.observed_on, o.is_promotion, o.offer_valid_from, o.offer_valid_to, "
    "o.price_minor, o.pack_quantity, o.pack_unit, o.base_family, o.unit_price_micro, "
    "o.source, o.source_ref, o.note, o.created_at, "
    "case when s.location is null or s.location = '' then s.name "
    "     else s.name || ' · ' || s.location end as store_label"
)


def _store_label(name: str, location: str | None) -> str:
    return f"{name} · {location}" if location else name


# ---------------------------------------------------------------------------
# Laeden
# ---------------------------------------------------------------------------


@router.get("/grocery-stores", response_model=list[StoreResponse])
async def list_stores(
    session_ctx: HouseholdSession,
    include_archived: bool = Query(default=False),
) -> list[StoreResponse]:
    session, household_id = session_ctx
    query = (
        f"select {', '.join('s.' + column for column in _STORE_COLUMNS.split(', '))}, "
        "count(o.id) as observation_count, max(o.observed_on) as last_observed_on "
        "from grocery_stores s "
        "left join ingredient_price_observations o on o.store_id = s.id "
        "where s.household_id = :household_id "
    )
    if not include_archived:
        query += "and s.is_archived = false "
    query += "group by s.id order by s.is_archived, lower(s.name), lower(coalesce(s.location, ''))"
    rows = (await session.execute(text(query), {"household_id": str(household_id)})).all()
    return [
        StoreResponse(
            id=row.id,
            name=row.name,
            location=row.location,
            store_kind=row.store_kind,
            is_archived=row.is_archived,
            note=row.note,
            observation_count=row.observation_count,
            last_observed_on=row.last_observed_on,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/grocery-stores", response_model=StoreResponse, status_code=status.HTTP_201_CREATED)
async def create_store(
    payload: StoreCreate, session_ctx: HouseholdEditorSession
) -> StoreResponse:
    session, household_id = session_ctx
    try:
        row = (
            await session.execute(
                text(
                    "insert into grocery_stores (household_id, name, location, store_kind, note) "
                    "values (:household_id, :name, :location, :store_kind, :note) "
                    f"returning {_STORE_COLUMNS}"
                ),
                {
                    "household_id": str(household_id),
                    "name": payload.name.strip(),
                    "location": (payload.location or "").strip() or None,
                    "store_kind": payload.store_kind,
                    "note": payload.note,
                },
            )
        ).one()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="grocery_store_already_exists"
        ) from exc
    return StoreResponse(
        id=row.id,
        name=row.name,
        location=row.location,
        store_kind=row.store_kind,
        is_archived=row.is_archived,
        note=row.note,
        observation_count=0,
        last_observed_on=None,
        created_at=row.created_at,
    )


@router.patch("/grocery-stores/{store_id}", response_model=StoreResponse)
async def update_store(
    store_id: UUID, payload: StoreUpdate, session_ctx: HouseholdEditorSession
) -> StoreResponse:
    session, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if updates:
        if "name" in updates and updates["name"]:
            updates["name"] = updates["name"].strip()
        if "location" in updates:
            updates["location"] = (updates["location"] or "").strip() or None
        set_clause = ", ".join(f"{field} = :{field}" for field in updates)
        try:
            touched = (
                await session.execute(
                    text(
                        f"update grocery_stores set {set_clause}, updated_at = now() "
                        "where id = :id and household_id = :household_id returning id"
                    ),
                    {**updates, "id": str(store_id), "household_id": str(household_id)},
                )
            ).first()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="grocery_store_already_exists"
            ) from exc
        if touched is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="grocery_store_not_found"
            )

    stores = await list_stores(session_ctx, include_archived=True)
    found = next((item for item in stores if item.id == store_id), None)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="grocery_store_not_found"
        )
    return found


@router.delete("/grocery-stores/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(store_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    """Loescht den Laden. Die Sichtungen bleiben stehen und verlieren nur ihre
    Ortsangabe (on delete set null) - sie zaehlen weiter fuer den Preisverlauf,
    nur nicht mehr fuer den Ladenvergleich. Preisgeschichte wegzuwerfen, weil
    ein Laden aus der Liste soll, waere ein sehr teurer Nebeneffekt; zum
    Ausblenden gibt es is_archived."""
    session, household_id = session_ctx
    removed = (
        await session.execute(
            text(
                "delete from grocery_stores where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(store_id), "household_id": str(household_id)},
        )
    ).first()
    if removed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="grocery_store_not_found"
        )
    await observations.refresh_valuation_prices(session, household_id)


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


@router.get("/grocery-price-settings", response_model=PriceSettingsPayload)
async def get_price_settings(session_ctx: HouseholdSession) -> PriceSettingsPayload:
    session, household_id = session_ctx
    settings = await observations.load_settings(session, household_id)
    return PriceSettingsPayload(
        default_strategy=settings.default_strategy,  # type: ignore[arg-type]
        preferred_store_id=settings.preferred_store_id,
        max_age_days=settings.max_age_days,
    )


@router.put("/grocery-price-settings", response_model=PriceSettingsPayload)
async def put_price_settings(
    payload: PriceSettingsPayload, session_ctx: HouseholdEditorSession
) -> PriceSettingsPayload:
    session, household_id = session_ctx
    await session.execute(
        text(
            "insert into grocery_price_settings "
            "(household_id, default_strategy, preferred_store_id, max_age_days) "
            "values (:household_id, :strategy, :store_id, :max_age) "
            "on conflict (household_id) do update set "
            "default_strategy = excluded.default_strategy, "
            "preferred_store_id = excluded.preferred_store_id, "
            "max_age_days = excluded.max_age_days, updated_at = now()"
        ),
        {
            "household_id": str(household_id),
            "strategy": payload.default_strategy,
            "store_id": str(payload.preferred_store_id) if payload.preferred_store_id else None,
            "max_age": payload.max_age_days,
        },
    )
    # Die Vorgaben wirken sofort auf jede abgeleitete Zeile - sonst haette der
    # Nutzer eine Einstellung geaendert und saehe dieselben Zahlen wie vorher.
    await observations.refresh_valuation_prices(session, household_id)
    return payload


# ---------------------------------------------------------------------------
# Sichtungen
# ---------------------------------------------------------------------------


async def _fetch_observations(
    session: AsyncSession, household_id: UUID, ingredient_key: str
) -> Sequence[Any]:
    return (
        await session.execute(
            text(
                f"select {_OBSERVATION_SELECT}, p.price_observation_id as current_id "
                "from ingredient_price_observations o "
                "left join grocery_stores s on s.id = o.store_id "
                "left join ingredient_prices p on p.household_id = o.household_id "
                "  and lower(btrim(p.name)) = o.ingredient_key "
                "where o.household_id = :household_id and o.ingredient_key = :key "
                "order by o.observed_on desc, o.created_at desc"
            ),
            {"household_id": str(household_id), "key": ingredient_key},
        )
    ).all()


def _to_observation_response(row: Any) -> ObservationResponse:
    return ObservationResponse(
        id=row.id,
        ingredient_key=row.ingredient_key,
        ingredient_name=row.ingredient_name,
        store_id=row.store_id,
        store_label=row.store_label,
        brand=row.brand,
        quality_tier=row.quality_tier,
        brand_tier=row.brand_tier,
        observed_on=row.observed_on,
        is_promotion=row.is_promotion,
        offer_valid_from=row.offer_valid_from,
        offer_valid_to=row.offer_valid_to,
        price_minor=row.price_minor,
        pack_quantity=row.pack_quantity,
        pack_unit=row.pack_unit,
        base_family=row.base_family,
        unit_price_micro=row.unit_price_micro,
        source=row.source,
        source_ref=row.source_ref,
        note=row.note,
        is_current_valuation=bool(getattr(row, "current_id", None) == row.id),
        created_at=row.created_at,
    )


@router.get("/ingredient-prices/{ingredient_key}/detail", response_model=IngredientPriceDetail)
async def get_ingredient_price_detail(
    ingredient_key: str, session_ctx: HouseholdSession
) -> IngredientPriceDetail:
    """Alles zu EINER Zutat: woher die geltende Zahl kommt, was die Laeden
    aktuell verlangen, und der vollstaendige Verlauf."""
    session, household_id = session_ctx
    key = ingredient_key.strip().lower()

    price_row = (
        await session.execute(
            text(
                "select name, price_source, price_strategy, preferred_store_id, "
                "quality_preference, planning_base_family, price_observation_id, "
                "price_observed_on, price_is_stale from ingredient_prices "
                "where household_id = :household_id and lower(btrim(name)) = :key"
            ),
            {"household_id": str(household_id), "key": key},
        )
    ).one_or_none()
    if price_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ingredient_price_not_found"
        )

    rows = await _fetch_observations(session, household_id, key)
    history = [_to_observation_response(row) for row in rows]

    # Kreuztabelle: je (Laden, Qualitaet) die juengste Sichtung. Angebote
    # zaehlen hier mit - der Nutzer soll ja gerade sehen, wo diese Woche etwas
    # im Angebot ist; in den Bewertungspreis kommen sie trotzdem nicht.
    matrix: list[PriceMatrixCell] = []
    seen: set[tuple[UUID | None, str]] = set()
    for row in rows:
        bucket = (row.store_id, row.quality_tier)
        if bucket in seen:
            continue
        seen.add(bucket)
        matrix.append(
            PriceMatrixCell(
                store_id=row.store_id,
                store_label=row.store_label or "irgendwo",
                quality_tier=row.quality_tier,
                brand_tier=row.brand_tier,
                observation_id=row.id,
                price_minor=row.price_minor,
                pack_quantity=row.pack_quantity,
                pack_unit=row.pack_unit,
                unit_price_micro=row.unit_price_micro,
                observed_on=row.observed_on,
                is_promotion=row.is_promotion,
                offer_valid_to=row.offer_valid_to,
                is_current_valuation=bool(
                    getattr(row, "current_id", None) == row.id
                ),
            )
        )

    # Spanne nur innerhalb EINER Einheitenfamilie: "0,89 fuer 500 g" gegen
    # "2,49 fuer 1 Pkg" zu vergleichen waere geraten.
    family = price_row.planning_base_family or (rows[0].base_family if rows else None)
    in_family = [row.unit_price_micro for row in rows if row.base_family == family]

    return IngredientPriceDetail(
        ingredient_key=key,
        ingredient_name=price_row.name,
        valuation=ValuationState(
            price_source=price_row.price_source,
            price_strategy=price_row.price_strategy,
            preferred_store_id=price_row.preferred_store_id,
            quality_preference=price_row.quality_preference,
            planning_base_family=price_row.planning_base_family,
            price_observation_id=price_row.price_observation_id,
            price_observed_on=price_row.price_observed_on,
            price_is_stale=price_row.price_is_stale,
        ),
        matrix=matrix,
        history=history,
        cheapest_unit_price_micro=min(in_family) if in_family else None,
        dearest_unit_price_micro=max(in_family) if in_family else None,
        base_family=family,
    )


async def _persist(
    session: AsyncSession,
    household_id: UUID,
    payloads: list[ObservationCreate],
    *,
    source: str,
    source_ref: str | None,
) -> ObservationBulkResult:
    """Gemeinsamer Weg fuer Handeingabe und Uebernahme aus der Vorschau.

    Jede Zeile laeuft durch dieselbe Pruefung wie beim Dateieinlesen. Der
    Client schickt Eingaben, keine Rechenergebnisse - Basiseinheit und
    Fingerabdruck entstehen ausschliesslich hier."""
    parsed: list[sheet.ParsedObservation] = []
    rejected: list[str] = []
    store_ids: dict[int, UUID | None] = {}

    for index, payload in enumerate(payloads, start=1):
        try:
            observation = sheet.make_observation(
                row_number=index,
                ingredient=payload.ingredient_name,
                price=payload.price_minor / 100,
                pack_quantity=payload.pack_quantity,
                pack_unit=payload.pack_unit,
                store=payload.store_name,
                location=payload.store_location,
                brand=payload.brand,
                organic=payload.is_organic,
                promotion=payload.is_promotion,
                valid_from=payload.offer_valid_from,
                valid_to=payload.offer_valid_to,
                observed_on=payload.observed_on,
                note=payload.note,
            )
        except sheet.RowValidationError as error:
            rejected.append(f"{payload.ingredient_name}: {error}")
            continue
        parsed.append(observation)
        if payload.store_id is not None:
            store_ids[observation.row_number] = payload.store_id

    if not parsed:
        return ObservationBulkResult(
            inserted=0,
            skipped_duplicates=0,
            rejected=rejected,
            created_price_rows=0,
            updated_valuations=0,
            blocked_by_manual=0,
        )

    keys = sorted({item.ingredient_key for item in parsed})
    created_rows = await observations.ensure_price_rows(session, household_id, parsed)
    inserted, skipped = await observations.insert_observations(
        session, household_id, parsed, source=source, source_ref=source_ref, store_ids=store_ids
    )
    updated = await observations.refresh_valuation_prices(
        session, household_id, ingredient_keys=keys
    )
    blocked = await observations.count_manual_rows_with_observations(session, household_id)
    return ObservationBulkResult(
        inserted=inserted,
        skipped_duplicates=skipped,
        rejected=rejected,
        created_price_rows=created_rows,
        updated_valuations=updated,
        blocked_by_manual=blocked,
    )


@router.post(
    "/price-observations",
    response_model=ObservationBulkResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_observation(
    payload: ObservationCreate, session_ctx: HouseholdEditorSession
) -> ObservationBulkResult:
    session, household_id = session_ctx
    return await _persist(session, household_id, [payload], source="manual", source_ref=None)


@router.post(
    "/price-observations/bulk",
    response_model=ObservationBulkResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_observations_bulk(
    payload: ObservationBulkCreate, session_ctx: HouseholdEditorSession
) -> ObservationBulkResult:
    """Uebernahme der in der Vorschau angehakten Zeilen."""
    session, household_id = session_ctx
    return await _persist(
        session,
        household_id,
        payload.observations,
        source=payload.source,
        source_ref=payload.source_ref,
    )


@router.delete("/price-observations/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_observation(
    observation_id: UUID, session_ctx: HouseholdEditorSession
) -> None:
    """Loeschen statt aendern: das Log ist anhaengend. Eine Korrektur ist eine
    neue Sichtung, ein Tippfehler wird entfernt."""
    session, household_id = session_ctx
    row = (
        await session.execute(
            text(
                "delete from ingredient_price_observations "
                "where id = :id and household_id = :household_id returning ingredient_key"
            ),
            {"id": str(observation_id), "household_id": str(household_id)},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="price_observation_not_found"
        )
    await observations.refresh_valuation_prices(
        session, household_id, ingredient_keys=[row.ingredient_key]
    )


@router.patch("/ingredient-prices/{ingredient_key}/valuation", response_model=ValuationState)
async def update_valuation(
    ingredient_key: str, payload: ValuationUpdate, session_ctx: HouseholdEditorSession
) -> ValuationState:
    """Steuert, wie der Bewertungspreis dieser Zutat zustande kommt."""
    session, household_id = session_ctx
    key = ingredient_key.strip().lower()
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="no_fields_to_update"
        )
    if updates.get("price_source") == "pinned" and not updates.get("price_observation_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="pinned_requires_observation"
        )

    columns = {
        field: (str(value) if isinstance(value, UUID) else value)
        for field, value in updates.items()
    }
    set_clause = ", ".join(f"{field} = :{field}" for field in columns)
    touched = (
        await session.execute(
            text(
                f"update ingredient_prices set {set_clause}, updated_at = now() "
                "where household_id = :household_id and lower(btrim(name)) = :key "
                "returning id"
            ),
            {**columns, "household_id": str(household_id), "key": key},
        )
    ).first()
    if touched is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ingredient_price_not_found"
        )
    await observations.refresh_valuation_prices(session, household_id, ingredient_keys=[key])

    detail = await get_ingredient_price_detail(ingredient_key, session_ctx)
    return detail.valuation


# ---------------------------------------------------------------------------
# Excel-Austausch
# ---------------------------------------------------------------------------


@router.get("/price-sheet/template")
async def download_price_sheet_template(session_ctx: HouseholdSession) -> Response:
    """Vorlage mit den bekannten Zutaten als Zeilen, Preisspalte leer.

    Vorbefuellt, weil die Zuordnung ueber den Namen laeuft: wer den Namen
    selbst tippt, erzeugt frueher oder spaeter "Gerolsteiner Medium" neben
    "Gerolsteiner med." - zwei Preisreihen, von denen keine einen Verlauf
    zeigt. Mit aufgenommen werden auch Zutaten, die nur in Gerichten
    vorkommen und noch gar keinen Preis haben: genau die will man bepreisen."""
    session, household_id = session_ctx
    rows = (
        await session.execute(
            text(
                "select name, pack_quantity, pack_unit, category from ingredient_prices "
                "where household_id = :household_id "
                "union "
                "select distinct on (lower(btrim(name))) name, null, unit, category "
                "from dish_ingredients where household_id = :household_id "
                "and lower(btrim(name)) not in ("
                "  select lower(btrim(name)) from ingredient_prices "
                "  where household_id = :household_id)"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    stores = (
        await session.execute(
            text(
                "select name from grocery_stores where household_id = :household_id "
                "and is_archived = false order by lower(name)"
            ),
            {"household_id": str(household_id)},
        )
    ).all()

    workbook = sheet.build_template_workbook(
        [
            sheet.TemplateIngredient(
                name=row[0], pack_quantity=row[1], pack_unit=row[2], category=row[3]
            )
            for row in rows
        ],
        [store.name for store in stores],
    )
    filename = f"Preisvorlage-{date.today().isoformat()}.xlsx"
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/price-sheet/preview", response_model=SheetPreviewResult)
async def preview_price_sheet(
    session_ctx: HouseholdEditorSession,
    file: UploadFile = File(...),  # noqa: B008
) -> SheetPreviewResult:
    """Liest eine ausgefuellte Vorlage und legt das Ergebnis vor. SPEICHERT
    NICHTS. Uebernommen wird ueber /price-observations/bulk, nachdem der
    Nutzer ausgewaehlt hat."""
    session, household_id = session_ctx
    filename = file.filename or "Preise.xlsx"
    contents = await file.read()

    try:
        check_file_size(contents)
        check_xlsx_structure(contents, filename)
    except CostImportSecurityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        parsed = sheet.parse_workbook(contents)
    except sheet.PriceSheetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    keys = sorted({item.ingredient_key for item in parsed.observations})
    known = {
        row.key
        for row in (
            await session.execute(
                text(
                    "select lower(btrim(name)) as key from ingredient_prices "
                    "where household_id = :household_id and lower(btrim(name)) = any(:keys)"
                ),
                {"household_id": str(household_id), "keys": keys},
            )
        ).all()
    } if keys else set()

    existing_fingerprints = {
        row.fingerprint
        for row in (
            await session.execute(
                text(
                    "select fingerprint from ingredient_price_observations "
                    "where household_id = :household_id and fingerprint = any(:prints)"
                ),
                {
                    "household_id": str(household_id),
                    "prints": [item.fingerprint for item in parsed.observations],
                },
            )
        ).all()
    } if parsed.observations else set()

    # Bisheriger Grundpreis je Zutat: macht einen Zahlendreher sichtbar,
    # BEVOR er in der Kostenrechnung landet.
    previous: dict[str, int] = {}
    if keys:
        for row in (
            await session.execute(
                text(
                    "select distinct on (ingredient_key) ingredient_key, unit_price_micro "
                    "from ingredient_price_observations "
                    "where household_id = :household_id and ingredient_key = any(:keys) "
                    "order by ingredient_key, observed_on desc, created_at desc"
                ),
                {"household_id": str(household_id), "keys": keys},
            )
        ).all():
            previous[row.ingredient_key] = row.unit_price_micro

    return SheetPreviewResult(
        file_name=filename,
        sheet_name=parsed.sheet_name,
        header_row=parsed.header_row,
        rows=[
            SheetPreviewRow(
                row_number=item.row_number,
                ingredient_name=item.ingredient_name,
                ingredient_is_new=item.ingredient_key not in known,
                store_name=item.store_name,
                store_location=item.store_location,
                brand=item.brand,
                quality_tier=item.quality_tier,  # type: ignore[arg-type]
                brand_tier=item.brand_tier,  # type: ignore[arg-type]
                price_minor=item.price_minor,
                pack_quantity=item.pack_quantity,
                pack_unit=item.pack_unit,
                base_family=item.base_family,
                unit_price_micro=int(item.unit_price_minor_per_base * 1_000_000),
                is_promotion=item.is_promotion,
                offer_valid_from=item.offer_valid_from,
                offer_valid_to=item.offer_valid_to,
                observed_on=item.observed_on,
                note=item.note,
                is_duplicate=item.fingerprint in existing_fingerprints,
                previous_unit_price_micro=previous.get(item.ingredient_key),
            )
            for item in parsed.observations
        ],
        problems=[
            SheetProblem(
                row_number=problem.row_number,
                ingredient_name=problem.ingredient_name,
                message=problem.message,
            )
            for problem in parsed.problems
        ],
        untouched_rows=parsed.untouched_rows,
        unmapped_headers=parsed.unmapped_headers,
    )
