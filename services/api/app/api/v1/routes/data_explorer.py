"""Datenbank-Explorer: alle Haushaltstabellen sehen und aufraeumen.

Die Anwendung hat 79 haushaltsbezogene Tabellen, aber nur fuer einen Teil davon
eine Oberflaeche mit Loeschfunktion. Was ueber einen Import, einen KI-Lauf oder
einen abgebrochenen Versuch hineingekommen ist, war bisher teilweise nur mit
einem SQL-Werkzeug von aussen wieder herauszubekommen.

Alle Endpunkte sind haushaltsgebunden; Loeschen zusaetzlich auf die
Editorrolle. Tabellen- und Spaltennamen werden gegen den Katalog der Datenbank
geprueft, bevor sie in eine Abfrage kommen - siehe Modulkopf von
data_explorer_service.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.schemas.data_explorer import (
    CascadeEffect,
    ColumnInfo,
    DeletePreviewResponse,
    DeleteRequest,
    DeleteResponse,
    PurgePreviewResponse,
    PurgeRequest,
    PurgeResponse,
    RowPageResponse,
    TableListResponse,
    TableSummary,
)
from app.services import audit_service
from app.services import data_explorer_service as explorer

router = APIRouter(prefix="/data-explorer", tags=["data-explorer"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def _bad_request(error: explorer.ExplorerError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.get("/tables", response_model=TableListResponse)
async def list_tables(session_ctx: HouseholdSession) -> TableListResponse:
    """Alle Haushaltstabellen mit ihrer Zeilenzahl in diesem Haushalt."""
    session, household_id = session_ctx
    catalog = await explorer.load_catalog(session)
    counts = await explorer.table_counts(session, household_id)

    tables = [
        TableSummary(
            name=table.name,
            group=table.group,
            row_count=counts.get(table.name, 0),
            is_deletable=table.is_deletable,
            primary_key=table.primary_key,
        )
        for table in catalog.tables.values()
    ]
    # Innerhalb der Gruppe die vollen Tabellen zuerst: leere Tabellen sind die
    # uninteressantesten und muessen nicht oben stehen.
    tables.sort(key=lambda item: (item.group, -item.row_count, item.name))
    return TableListResponse(tables=tables, total_rows=sum(counts.values()))


async def _household_name(session: AsyncSession, household_id: UUID) -> str:
    row = (
        await session.execute(
            text("select name from households where id = :id"), {"id": str(household_id)}
        )
    ).one_or_none()
    if row is None:  # pragma: no cover - der Haushaltskontext ist bereits geprueft
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="household_not_found"
        )
    return str(row.name)


@router.get("/purge-preview", response_model=PurgePreviewResponse)
async def preview_purge(session_ctx: HouseholdSession) -> PurgePreviewResponse:
    """Was ein Leeren dieses Haushalts wegnehmen wuerde."""
    session, household_id = session_ctx
    catalog = await explorer.load_catalog(session)
    counts = await explorer.table_counts(session, household_id)

    tables = [
        TableSummary(
            name=table.name,
            group=table.group,
            row_count=counts.get(table.name, 0),
            is_deletable=table.is_deletable,
            primary_key=table.primary_key,
        )
        for table in catalog.tables.values()
        if counts.get(table.name, 0) > 0 and table.name not in explorer.PURGE_KEEP
    ]
    tables.sort(key=lambda item: -item.row_count)

    return PurgePreviewResponse(
        household_name=await _household_name(session, household_id),
        tables=tables,
        total_rows=sum(item.row_count for item in tables),
        kept_tables=sorted(explorer.PURGE_KEEP),
    )


@router.post("/purge", response_model=PurgeResponse)
async def purge_household(
    payload: PurgeRequest, session_ctx: HouseholdEditorSession, user: CurrentUser
) -> PurgeResponse:
    """Leert alle Bewegungsdaten dieses Haushalts.

    Was bleibt: die Zugehoerigkeit, der Kategorienbaum und die anfuegenden
    Protokolle (siehe PURGE_KEEP im Dienst - die Auswahl folgt dem, was das
    Schema ohnehin schuetzt). Der Haushalt ist danach leer, aber sofort wieder
    benutzbar; Buchungen lassen sich zuordnen, ohne dass erst Kategorien
    angelegt werden muessen.

    Der Haushaltsname muss mitgeschickt werden und wird hier verglichen - bei
    hunderten Testhaushalten neben einem echten ist die Verwechslung der
    teuerste denkbare Fehler, und eine reine Oberflaechenpruefung waere
    dagegen keine Sicherung.

    Alles laeuft in EINER Transaktion. Bricht etwas ab, ist nichts geloescht -
    ein halb geraeumter Haushalt mit Buchungen ohne Konten waere schlimmer als
    ein fehlgeschlagener Aufruf."""
    session, household_id = session_ctx
    name = await _household_name(session, household_id)
    if payload.confirm_name.strip() != name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="household_name_mismatch",
        )

    try:
        entfernt = await explorer.purge_household(session, household_id)
    except explorer.ExplorerError as error:
        raise _bad_request(error) from error

    gesamt = sum(entfernt.values())
    # Nach dem Leeren geschrieben, nicht davor: audit_log ist selbst eine
    # Haushaltstabelle und waere sonst gleich wieder weg. Uebrig bleibt genau
    # ein Eintrag - der ueber diesen Vorgang.
    audit_id = uuid4()
    await audit_service.record_audit_event(
        session,
        household_id,
        user.id,
        entity_type="household",
        entity_id=household_id,
        action="delete",
        metadata={
            "vorgang": "haushalt_geleert",
            "geloeschte_zeilen": gesamt,
            "je_tabelle": entfernt,
        },
    )

    return PurgeResponse(
        household_name=name,
        deleted_rows=gesamt,
        deleted_per_table=entfernt,
        audit_event_id=audit_id,
    )


@router.get("/tables/{table_name}/rows", response_model=RowPageResponse)
async def read_rows(
    table_name: str,
    session_ctx: HouseholdSession,
    limit: int = Query(default=explorer.DEFAULT_PAGE_SIZE, ge=1, le=explorer.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    order_by: str | None = Query(default=None),
    descending: bool = Query(default=True),
    search: str | None = Query(default=None, max_length=200),
) -> RowPageResponse:
    session, household_id = session_ctx
    try:
        table = await explorer.require_table(session, table_name)
        page = await explorer.fetch_rows(
            session,
            household_id,
            table_name,
            limit=limit,
            offset=offset,
            order_by=order_by,
            descending=descending,
            search=search,
        )
    except explorer.ExplorerError as error:
        raise _bad_request(error) from error

    return RowPageResponse(
        table=table.name,
        columns=[
            ColumnInfo(
                name=column.name,
                data_type=column.data_type,
                is_nullable=column.is_nullable,
                is_primary_key=column.is_primary_key,
            )
            for column in table.columns
        ],
        primary_key=table.primary_key,
        rows=page.rows,
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/tables/{table_name}/delete-preview", response_model=DeletePreviewResponse)
async def preview_delete(
    table_name: str, payload: DeleteRequest, session_ctx: HouseholdSession
) -> DeletePreviewResponse:
    """Was ein Loeschen ausser den gewaehlten Zeilen noch mitnimmt.

    Pflichtschritt vor dem Loeschen, nicht Beiwerk: `on delete cascade` reicht
    in diesem Schema bis zu vier Ebenen. Eine Essenswoche nimmt Mahlzeiten,
    Einkaufslisten, deren Posten und die Kaufverknuepfungen mit."""
    session, household_id = session_ctx
    try:
        effects = await explorer.preview_delete(session, household_id, table_name, payload.ids)
        vorhandene = await explorer.fetch_rows_by_ids(
            session, household_id, table_name, payload.ids
        )
    except explorer.ExplorerError as error:
        raise _bad_request(error) from error

    return DeletePreviewResponse(
        table=table_name,
        direct_rows=len(vorhandene),
        effects=[
            CascadeEffect(
                table=effect.table,
                rows=effect.rows,
                effect=effect.effect,  # type: ignore[arg-type]
                via_column=effect.via_column,
                depth=effect.depth,
            )
            for effect in effects
        ],
        cascade_total=sum(e.rows for e in effects if e.effect == "delete"),
        detach_total=sum(e.rows for e in effects if e.effect == "detach"),
    )


@router.post("/tables/{table_name}/delete", response_model=DeleteResponse)
async def delete_rows(
    table_name: str,
    payload: DeleteRequest,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> DeleteResponse:
    """Loescht die gewaehlten Zeilen samt allem, was daran haengt.

    Vor dem Loeschen wird die Kaskade noch einmal gezaehlt und ein
    Schnappschuss der betroffenen Zeilen ins Audit-Log geschrieben. Danach
    liesse sich beides nicht mehr ermitteln - und "was war da eigentlich
    drin" ist genau die Frage, die man nach einem Loeschen stellt."""
    session, household_id = session_ctx
    try:
        table = await explorer.require_table(session, table_name)
        effects = await explorer.preview_delete(session, household_id, table_name, payload.ids)
        snapshot = await explorer.fetch_rows_by_ids(
            session, household_id, table_name, payload.ids
        )
        deleted = await explorer.delete_rows(session, household_id, table_name, payload.ids)
    except explorer.ExplorerError as error:
        raise _bad_request(error) from error

    cascade_total = sum(effect.rows for effect in effects if effect.effect == "delete")

    audit_id: UUID | None = None
    if deleted:
        # Eine Klammer ueber den ganzen Vorgang, nicht ein Ereignis je Zeile:
        # "am 5.8. wurden 12 Zeilen aus meal_weeks geloescht, samt 148
        # abhaengigen" ist die Auskunft, die spaeter jemand braucht.
        audit_id = uuid4()
        await audit_service.record_audit_event(
            session,
            household_id,
            user.id,
            entity_type=f"data_explorer:{table.name}",
            entity_id=audit_id,
            action="delete",
            before={"rows": snapshot},
            metadata={
                "table": table.name,
                "deleted": deleted,
                "cascade": [
                    {"table": e.table, "rows": e.rows, "effect": e.effect} for e in effects
                ],
            },
        )

    return DeleteResponse(
        table=table.name,
        deleted=deleted,
        cascade_total=cascade_total,
        audit_event_id=audit_id,
    )
