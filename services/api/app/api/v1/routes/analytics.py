"""API-Endpunkte fuer den Analyse-Abfragevertrag.

docs/ANALYSE-EXPLORER-PLAN.md, Baustein B. Alle Endpunkte sind rein lesend
und deshalb fuer jedes Haushaltsmitglied zugaenglich - auch fuer die Rolle
mit Nur-Lese-Recht.
"""

import csv
import io
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session
from app.schemas.analytics import (
    AnalyticsQuery,
    AnalyticsResult,
    FacetRequest,
    FacetResult,
    FieldCatalog,
    ValidationResult,
)
from app.services import analytics_service
from app.services.analytics_service import QueryCompilationError

router = APIRouter(prefix="/analytics", tags=["analytics"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]


def _reject(exc: QueryCompilationError) -> HTTPException:
    """Ein nicht kompilierbarer Abfragevertrag ist ein Eingabefehler des
    Aufrufers. 422 mit den strukturierten Einzelbefunden - eine Oberflaeche
    kann daraus alle Fehler auf einmal anzeigen, statt sie nacheinander zu
    entdecken."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "analytics_query_invalid",
            "issues": [issue.model_dump() for issue in exc.issues],
        },
    )


@router.get("/fields", response_model=FieldCatalog)
async def fetch_field_catalog(ctx: HouseholdSession) -> FieldCatalog:
    """Der Feldkatalog: welche Felder gruppierbar bzw. filterbar sind und
    welche Aggregate sie zulassen.

    Explorer-Oberflaeche und KI-Prompt beziehen ihr Schema aus genau dieser
    Antwort. Zwei getrennte Quellen waeren die sichere Art, den Prompt gegen
    einen veralteten Katalog laufen zu lassen."""
    return analytics_service.get_field_catalog()


@router.post("/validate", response_model=ValidationResult)
async def validate_analytics_query(
    query: AnalyticsQuery, ctx: HouseholdSession
) -> ValidationResult:
    """Kompiliert trocken und meldet Fehler, ohne die Datenbank anzufassen.

    Grundlage fuer "Abfrage pruefen" in der KI-Werkbank: das Modell bekommt
    strukturierte Fehlercodes zurueck und kann gezielt nachbessern, statt zu
    raten."""
    _, household_id = ctx
    return analytics_service.validate_query(query, household_id, date.today())


@router.post("", response_model=AnalyticsResult)
async def run_analytics_query(
    query: AnalyticsQuery, ctx: HouseholdSession
) -> AnalyticsResult:
    """Fuehrt eine Auswertung aus.

    Antwortet mit Spaltenbeschreibung, Zeilen, Gesamtzeile ueber die volle
    Filtermenge und - falls eine Grenze gegriffen hat - deren ausdruecklicher
    Nennung in `applied_limits`."""
    session, household_id = ctx
    try:
        return await analytics_service.run_query(session, household_id, query, date.today())
    except QueryCompilationError as exc:
        raise _reject(exc) from exc


@router.post("/facets", response_model=FacetResult)
async def fetch_facets(request: FacetRequest, ctx: HouseholdSession) -> FacetResult:
    """Werte eines Feldes unter Beruecksichtigung aller UEBRIGEN Filter.

    Die Werteliste einer Spalte respektiert die anderen Filter, aber nicht
    sich selbst - genau das Verhalten, das Nutzer aus Excel kennen und dessen
    Fehlen sofort auffaellt."""
    session, household_id = ctx
    try:
        return await analytics_service.run_facets(session, household_id, request, date.today())
    except QueryCompilationError as exc:
        raise _reject(exc) from exc


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    return str(value)


@router.post("/export")
async def export_analytics_query(
    query: AnalyticsQuery, ctx: HouseholdSession
) -> StreamingResponse:
    """Dieselbe Abfrage als CSV-Strom.

    Bewusst serverseitig gestroemt statt clientseitig gesammelt: der Export
    darf nicht an derselben Zeilengrenze haengen wie die Anzeige. Betraege
    bleiben in Minor Units - eine stille Umrechnung in Euro waere in einer
    Datei, mit der weitergerechnet wird, eine Fehlerquelle."""
    session, household_id = ctx

    export_query = query.model_copy(
        update={"limit": analytics_service.MAX_ROW_ROWS if query.grain == "rows" else None}
    )
    try:
        result = await analytics_service.run_query(
            session, household_id, export_query, date.today()
        )
    except QueryCompilationError as exc:
        raise _reject(exc) from exc

    async def stream() -> AsyncIterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL)

        # UTF-8-BOM und die sep-Zeile: ohne sie oeffnet deutsches Excel die
        # Datei einspaltig und zerlegt Umlaute.
        yield "﻿"
        yield "sep=;\r\n"

        writer.writerow([column.label for column in result.columns])
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

        for row in result.rows:
            writer.writerow([_csv_value(row.get(column.key)) for column in result.columns])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    return StreamingResponse(
        stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="auswertung-{result.query_id}.csv"'
        },
    )
