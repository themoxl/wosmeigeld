"""Sicherung eines Haushalts herunterladen und wieder einspielen.

Die Datei ist ein ZIP und laesst sich ohne jedes Werkzeug auf dem Rechner
erzeugen und zurueckspielen - genau darin unterscheidet sich dieser Weg von
`scripts/db_snapshot.py` (ganze Entwicklungsdatenbank, braucht Docker) und
`infra/backup/` (ganze Maschine, verschluesselt, mit Restoretest).

Rechte, und warum sie sich unterscheiden:

- Herunterladen verlangt die Editorrolle. Eine Sicherung ist der gesamte
  Haushalt in einer Datei; wer nur lesen darf, soll ihn nicht am Stueck
  mitnehmen koennen.
- Einspielen verlangt die Eigentuemerrolle. Es ersetzt die Daten aller
  Mitglieder - das ist die weitreichendste Aenderung, die die Anwendung kennt.
"""

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    require_household_editor,
    require_household_owner,
)
from app.auth.jwt import AuthenticatedUser
from app.schemas.backup import (
    BackupInfoResponse,
    BackupTableStat,
    ImportPreviewResponse,
    ImportResponse,
    TablePreview,
)
from app.services import audit_service, backup_service
from app.services import data_explorer_service as explorer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"])

HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
HouseholdOwnerSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_owner)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

# Was eine Haushaltssicherung ausdruecklich nicht enthaelt. Steht in der
# Antwort, damit die Oberflaeche es nennen kann.
NICHT_ENTHALTEN = [
    "Hochgeladene Dokumente (Rechnungen, Belege) - die liegen im Dateispeicher, "
    "nicht in der Datenbank.",
    "Zugangsdaten und Nutzerkonten.",
    "Andere Haushalte.",
]


def _fehler(error: backup_service.BackupError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


async def _lies_datei(file: UploadFile) -> bytes:
    daten = await file.read()
    if not daten:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Die Datei ist leer."
        )
    return daten


@router.get("/info", response_model=BackupInfoResponse)
async def backup_info(ctx: HouseholdEditorSession) -> BackupInfoResponse:
    """Was eine Sicherung dieses Haushalts jetzt enthalten wuerde.

    Wird vor dem Herunterladen angezeigt, damit "Sicherung" eine Zahl hat und
    nicht nur ein Versprechen ist."""
    session, household_id = ctx
    bestand = await explorer.table_counts(session, household_id)
    gefuellt = {name: anzahl for name, anzahl in bestand.items() if anzahl > 0}

    return BackupInfoResponse(
        household_name=await backup_service.haushaltsname(session, household_id),
        table_count=len(gefuellt),
        total_rows=sum(gefuellt.values()),
        tables=[
            BackupTableStat(table=name, rows=anzahl)
            for name, anzahl in sorted(gefuellt.items(), key=lambda p: (-p[1], p[0]))
        ],
        excluded=NICHT_ENTHALTEN,
    )


@router.get("/export")
async def export_backup(ctx: HouseholdEditorSession) -> Response:
    """Laedt den ganzen Haushalt als ZIP herunter."""
    session, household_id = ctx
    daten, _ = await backup_service.exportiere_haushalt(session, household_id)

    name = await backup_service.haushaltsname(session, household_id)
    # Nur Unbedenkliches in den Dateinamen: er landet in einem HTTP-Kopf, und
    # ein Haushalt darf beliebig heissen - ein Anfuehrungszeichen im Namen
    # zerlegte sonst den Content-Disposition-Kopf.
    sicher = "".join(z if z.isalnum() or z in "-_" else "-" for z in name)[:40].strip("-")
    stempel = datetime.now().strftime("%Y%m%d-%H%M")
    dateiname = f"finanzplaner-{sicher or 'haushalt'}-{stempel}.zip"

    return Response(
        content=daten,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{dateiname}"'},
    )


@router.post("/preview", response_model=ImportPreviewResponse)
async def preview_import(
    ctx: HouseholdOwnerSession,
    file: UploadFile = File(...),  # noqa: B008
) -> ImportPreviewResponse:
    """Was ein Einspielen dieser Datei bewirken wuerde. Aendert nichts.

    Pflichtschritt: die Zahl, die zaehlt, ist nicht "wie viel kommt herein",
    sondern "wie viel ist danach weg"."""
    session, household_id = ctx
    daten = await _lies_datei(file)
    try:
        vorschau = await backup_service.vorschau_einspielen(session, household_id, daten)
    except backup_service.BackupError as error:
        raise _fehler(error) from error

    return ImportPreviewResponse(
        household_name=vorschau.haushalt_name,
        source_household_name=vorschau.quelle_haushalt_name,
        source_household_id=vorschau.quelle_haushalt_id,
        created_at=vorschau.erstellt_am,
        schema_matches=vorschau.schema_passt,
        rows_in_file=vorschau.zeilen_in_datei,
        rows_in_household=vorschau.zeilen_im_haushalt,
        tables=[
            TablePreview(
                table=eintrag.tabelle,
                rows_in_file=eintrag.zeilen_in_datei,
                rows_in_household=eintrag.zeilen_im_haushalt,
                handling=eintrag.behandlung,  # type: ignore[arg-type]
                note=eintrag.hinweis,
            )
            for eintrag in vorschau.tabellen
        ],
        unknown_tables=vorschau.unbekannte_tabellen,
    )


@router.post("/import", response_model=ImportResponse)
async def import_backup(
    ctx: HouseholdOwnerSession,
    user: CurrentUser,
    file: UploadFile = File(...),  # noqa: B008
    confirm_name: str = Form(...),  # noqa: B008
) -> ImportResponse:
    """Ersetzt die Daten dieses Haushalts durch den Stand aus der Datei.

    Der Haushaltsname muss mitgeschickt werden und wird hier verglichen -
    dieselbe Absicherung wie beim Leeren des Haushalts. Bei hunderten
    Testhaushalten neben einem echten waere eine reine Oberflaechenpruefung
    keine Sicherung.

    Alles laeuft in EINER Transaktion: bricht etwas ab, ist nichts geaendert."""
    session, household_id = ctx
    daten = await _lies_datei(file)

    name = await backup_service.haushaltsname(session, household_id)
    if confirm_name.strip() != name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="household_name_mismatch"
        )

    try:
        ergebnis = await backup_service.spiele_ein(session, household_id, daten)
    except backup_service.BackupError as error:
        raise _fehler(error) from error

    # Nach dem Einspielen geschrieben, nicht davor: audit_log ist selbst eine
    # Haushaltstabelle. Es wird zwar nicht geleert (anfuegendes Protokoll),
    # aber der Eintrag gehoert hinter den Vorgang, den er beschreibt.
    await audit_service.record_audit_event(
        session,
        household_id,
        user.id,
        entity_type="household",
        entity_id=household_id,
        action="update",
        metadata={
            "vorgang": "sicherung_eingespielt",
            "geloeschte_zeilen": ergebnis.geloeschte_zeilen,
            "eingespielte_zeilen": ergebnis.eingespielte_zeilen,
            "je_tabelle": ergebnis.je_tabelle,
            "schema_passt": ergebnis.schema_passt,
        },
    )

    return ImportResponse(
        household_name=ergebnis.haushalt_name,
        deleted_rows=ergebnis.geloeschte_zeilen,
        imported_rows=ergebnis.eingespielte_zeilen,
        imported_per_table=ergebnis.je_tabelle,
        skipped_tables=ergebnis.uebersprungene_tabellen,
        cleared_user_references=ergebnis.geleerte_nutzerverweise,
        schema_matches=ergebnis.schema_passt,
    )
