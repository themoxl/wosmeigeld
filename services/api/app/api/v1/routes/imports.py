import base64
import hashlib
import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.core.db import get_session_factory
from app.schemas.import_platform import (
    ImportCommitInput,
    ImportCommitResult,
    ImportMappingSuggestion,
    ImportMappingSuggestionRequest,
    ImportReparseInput,
    ImportRevertInput,
    ImportRevertPreview,
    ImportRevertResult,
    ImportRow,
    ImportRun,
    ImportRunUpdate,
    MappingProfile,
    MappingProfileCreate,
)
from app.services import category_service
from app.services.import_mapping_ai import suggest_column_mapping
from app.services.import_service import (
    RUN_ACTIVE_TRANSACTION_COUNT_EXPRESSION,
    RUN_COMMITTED_COUNT_EXPRESSION,
    RUN_IS_STALE_EXPRESSION,
    STALE_RUN_AFTER_MINUTES,
    ImportRunAccountMissingError,
    ImportRunAccountUnknownError,
    ImportRunAlreadyCommittedError,
    ImportRunInProgressError,
    ImportRunMappingIncompleteError,
    ImportRunMappingProfileUnknownError,
    ImportRunNoStagedRowsError,
    ImportRunNotCancellableError,
    ImportRunNotEditableError,
    ImportRunNotFoundError,
    ImportRunNotReadyError,
    ImportRunNotReparsableError,
    ImportRunReparseCommittedError,
    cancel_import_run,
    commit_import_run,
    delete_import_run,
    preview_import_revert,
    reparse_import_run,
    revert_import_run,
    update_import_run,
)
from app.services.rules_service import load_active_classification_rules, match_rules_for_transaction
from app.services.transaction_detach_service import TransactionDetachForbiddenError
from app.services.worker_dispatch import (
    enqueue_contract_reconcile,
    process_import_actor,
)

logger = logging.getLogger(__name__)

# Die sys.path-Kopplung zu services/worker steht in
# app/services/worker_dispatch.py - dieser Import loest sie mit aus, weshalb
# `worker.imports.security` danach erreichbar ist.
from worker.imports.security import ImportSecurityError, check_file_size  # noqa: E402

router = APIRouter(prefix="/imports", tags=["imports"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Upload, Mapping-Profil und Commit schreiben - 403 statt stillem Nichtstun,
# wenn die Rolle nicht schreiben darf.
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

# started_at/cancel_requested/detected_columns/sample_raw sowie die berechnete
# Haengerkennung gehoeren in jede Laufantwort: ohne sie zeigte die Oberflaeche
# fuer einen Lauf ohne Worker einen Fortschritt, der nie endet, und der
# Mapping-Dialog haette im Zustand 'needs_mapping' nichts anzuzeigen (dort
# existieren noch keine import_rows). Jede Abfrage mit dieser Spaltenliste muss
# `stale_minutes` binden.
_RUN_COLUMNS = (
    "id, account_id, mapping_profile_id, filename, file_size_bytes, detected_format, "
    "status, total_rows, new_count, duplicate_count, transfer_candidate_count, "
    "error_count, error_message, created_at, started_at, completed_at, "
    f"cancel_requested, detected_columns, sample_raw, {RUN_IS_STALE_EXPRESSION}, "
    f"{RUN_COMMITTED_COUNT_EXPRESSION}, {RUN_ACTIVE_TRANSACTION_COUNT_EXPRESSION}"
)


@router.post("/upload", response_model=ImportRun, status_code=201)
async def upload_import(
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    file: UploadFile = File(...),  # noqa: B008
    account_id: UUID | None = Form(default=None),  # noqa: B008
    mapping_profile_id: UUID | None = Form(default=None),  # noqa: B008
) -> ImportRun:
    """Laedt eine Bankdatei hoch und reiht sie zur asynchronen Verarbeitung im
    Worker ein (siehe worker.actors.process_import). Die zurueckgegebene
    Run-Zeile hat Status 'pending' - der Client pollt bis 'ready'/'failed'."""
    session, household_id = ctx
    filename = file.filename or "import.csv"
    contents = await file.read()
    file_hash = hashlib.sha256(contents).hexdigest()

    try:
        check_file_size(contents)
    except ImportSecurityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if account_id is not None:
        row = (
            await session.execute(
                text(
                    "select 1 from accounts where id = :id and household_id = :household_id"
                ),
                {"id": str(account_id), "household_id": str(household_id)},
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found"
            )

    if mapping_profile_id is not None:
        profile_row = (
            await session.execute(
                text(
                    "select 1 from import_mapping_profiles "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(mapping_profile_id), "household_id": str(household_id)},
            )
        ).first()
        if profile_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="mapping_profile_not_found"
            )

    run_id = (
        await session.execute(
            text(
                "insert into import_runs (household_id, account_id, mapping_profile_id, "
                "filename, file_hash, file_size_bytes, started_by, status) "
                "values (:household_id, :account_id, :mapping_profile_id, :filename, "
                ":file_hash, :file_size_bytes, :started_by, 'pending') returning id"
            ),
            {
                "household_id": str(household_id),
                "account_id": str(account_id) if account_id else None,
                "mapping_profile_id": str(mapping_profile_id) if mapping_profile_id else None,
                "filename": filename,
                "file_hash": file_hash,
                "file_size_bytes": len(contents),
                "started_by": str(user.id),
            },
        )
    ).scalar_one()

    run = (
        await session.execute(
            text(
                f"select {_RUN_COLUMNS} from import_runs "
                "where id = :id and household_id = :household_id"
            ),
            {
                "id": str(run_id),
                "household_id": str(household_id),
                "stale_minutes": STALE_RUN_AFTER_MINUTES,
            },
        )
    ).one()
    result = ImportRun(**run._mapping)
    # Der Worker darf den Lauf erst nach dem Commit sehen. Andernfalls kann
    # er die Nachricht bereits quittieren, bevor import_runs sichtbar ist.
    # Nach diesem Commit keine Abfragen mehr auf der Request-Transaktion.
    await session.commit()
    await _dispatch_import(run_id, household_id, filename, base64.b64encode(contents).decode())
    return result


@router.get("", response_model=list[ImportRun])
async def list_import_runs(ctx: HouseholdSession) -> list[ImportRun]:
    session, household_id = ctx
    rows = (
        await session.execute(
            text(
                f"select {_RUN_COLUMNS} from import_runs "
                "where household_id = :household_id order by created_at desc limit 50"
            ),
            {
                "household_id": str(household_id),
                "stale_minutes": STALE_RUN_AFTER_MINUTES,
            },
        )
    ).all()
    return [ImportRun(**r._mapping) for r in rows]


@router.get("/mapping-profiles", response_model=list[MappingProfile])
async def list_mapping_profiles(ctx: HouseholdSession) -> list[MappingProfile]:
    session, household_id = ctx
    rows = (
        await session.execute(
            text(
                "select id, household_id, name, bank_format, column_mapping, created_at "
                "from import_mapping_profiles where household_id = :household_id order by name"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    return [MappingProfile(**r._mapping) for r in rows]


@router.post("/mapping-profiles", response_model=MappingProfile, status_code=201)
async def create_mapping_profile(
    payload: MappingProfileCreate, ctx: HouseholdEditorSession
) -> MappingProfile:
    session, household_id = ctx
    row = (
        await session.execute(
            text(
                "insert into import_mapping_profiles "
                "(household_id, name, bank_format, column_mapping) "
                "values (:household_id, :name, :bank_format, :column_mapping) "
                "returning id, household_id, name, bank_format, column_mapping, created_at"
            ),
            {
                "household_id": str(household_id),
                "name": payload.name,
                "bank_format": payload.bank_format,
                "column_mapping": json.dumps(payload.column_mapping),
            },
        )
    ).one()
    return MappingProfile(**row._mapping)


@router.patch("/mapping-profiles/{profile_id}", response_model=MappingProfile)
async def update_mapping_profile(
    profile_id: UUID, payload: MappingProfileCreate, ctx: HouseholdEditorSession
) -> MappingProfile:
    session, household_id = ctx
    row = (
        await session.execute(
            text(
                "update import_mapping_profiles set name = :name, bank_format = :bank_format, "
                "column_mapping = cast(:column_mapping as jsonb) "
                "where id = :id and household_id = :household_id "
                "returning id, household_id, name, bank_format, column_mapping, created_at"
            ),
            {
                "id": str(profile_id),
                "household_id": str(household_id),
                "name": payload.name,
                "bank_format": payload.bank_format,
                "column_mapping": json.dumps(payload.column_mapping),
            },
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="mapping_profile_not_found")
    return MappingProfile(**row._mapping)


@router.delete("/mapping-profiles/{profile_id}", status_code=204)
async def delete_mapping_profile(profile_id: UUID, ctx: HouseholdEditorSession) -> Response:
    session, household_id = ctx
    deleted_id = (
        await session.execute(
            text(
                "delete from import_mapping_profiles "
                "where id = :id and household_id = :household_id returning id"
            ),
            {"id": str(profile_id), "household_id": str(household_id)},
        )
    ).scalar_one_or_none()
    if deleted_id is None:
        raise HTTPException(status_code=404, detail="mapping_profile_not_found")
    # import_runs.mapping_profile_id uses ON DELETE SET NULL. Staged rows and
    # already imported transactions retain their data and provenance.
    return Response(status_code=204)


@router.get("/{run_id}", response_model=ImportRun)
async def get_import_run(run_id: UUID, ctx: HouseholdSession) -> ImportRun:
    session, household_id = ctx
    run = (
        await session.execute(
            text(
                f"select {_RUN_COLUMNS} from import_runs "
                "where id = :id and household_id = :household_id"
            ),
            {
                "id": str(run_id),
                "household_id": str(household_id),
                "stale_minutes": STALE_RUN_AFTER_MINUTES,
            },
        )
    ).first()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="import_run_not_found"
        )
    return ImportRun(**run._mapping)


@router.patch("/{run_id}", response_model=ImportRun)
async def update_import_run_endpoint(
    run_id: UUID, payload: ImportRunUpdate, ctx: HouseholdEditorSession, user: CurrentUser
) -> ImportRun:
    """Weist einem noch nicht uebernommenen Importlauf nachtraeglich ein
    Zielkonto (und/oder ein Mapping-Profil) zu.

    Bis hierher war ein ohne Zielkonto hochgeladener Lauf dauerhaft
    unbrauchbar: uebernehmen ging nicht, aendern war nicht vorgesehen. Danach
    funktioniert POST /{run_id}/commit ohne erneuten Upload.

    Nur die im Body enthaltenen Felder werden geaendert:
      404 - Lauf unbekannt, oder Konto/Mapping-Profil gehoert nicht zum Haushalt
      409 - Lauf ist nicht (mehr) im Status 'ready' (tatsaechlicher Status in detail)"""
    session, household_id = ctx
    try:
        run = await update_import_run(
            session,
            run_id,
            household_id,
            user.id,
            payload.model_dump(exclude_unset=True),
        )
    except ImportRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ImportRunAccountUnknownError, ImportRunMappingProfileUnknownError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ImportRunNotEditableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ImportRun(**run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_import_run_endpoint(
    run_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> None:
    """Entfernt einen nie uebernommenen Importlauf samt Staging-Zeilen und
    schreibt den Vorgang ins audit_log.

      204 - entfernt (import_rows verschwinden per ON DELETE CASCADE mit)
      404 - Lauf unbekannt
      409 - Lauf wird gerade verarbeitet, oder er wurde bereits uebernommen
            (dann bleibt er bestehen, damit die entstandenen Buchungen ihre
            Herkunft behalten - transactions.import_run_id ist ON DELETE SET
            NULL, ein Loeschen naehme sie ihnen still)

    "Wird gerade verarbeitet" gilt nur, solange der Lauf ein Lebenszeichen hat:
    ein haengender Lauf (siehe import_service.STALE_RUN_AFTER_MINUTES) laesst
    sich entfernen, sonst bliebe er ohne laufenden Worker fuer immer stehen."""
    session, household_id = ctx
    try:
        await delete_import_run(session, run_id, household_id, user.id)
    except ImportRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ImportRunInProgressError, ImportRunAlreadyCommittedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{run_id}/cancel", response_model=ImportRun)
async def cancel_import(
    run_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> ImportRun:
    """Bricht einen Importlauf ab, der noch verarbeitet wird.

    Der Unterschied zu DELETE ist der Zeitpunkt: Loeschen verlangt einen Lauf,
    der NICHT in Arbeit ist - genau der Zustand, aus dem ein Lauf ohne Worker
    nie herauskam. Abbrechen greift ausschliesslich in Arbeit:
      200 - 'pending' ist sofort beendet; bei 'analysing' ist der Abbruch
            vorgemerkt (cancel_requested), der Worker steigt an seinem
            naechsten Pruefpunkt aus und setzt den Endstatus selbst
      404 - Lauf unbekannt
      409 - Lauf ist nicht (mehr) in Arbeit (tatsaechlicher Status in detail)"""
    session, household_id = ctx
    try:
        run = await cancel_import_run(session, run_id, household_id, user.id)
    except ImportRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ImportRunNotCancellableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ImportRun(**run)


async def _dispatch_import(
    run_id: UUID, household_id: UUID, filename: str, content_base64: str
) -> None:
    """Nach dem DB-Commit einreihen; Fehler sofort sichtbar machen.

    Redis ist synchron: im Threadpool blockiert eine langsame Verbindung nicht
    gleichzeitig die Statusabfragen aller laufenden Uploads. Ein leerer Inhalt
    bedeutet erneutes Einlesen aus den gespeicherten Rohzeilen.
    """
    try:
        await run_in_threadpool(process_import_actor.send, str(run_id), filename, content_base64)
    except Exception as exc:
        message = "Die Verarbeitung konnte nicht gestartet werden. Bitte erneut versuchen."
        logger.exception("Import konnte nicht eingereiht werden (run_id=%s)", run_id)
        # Eigene, eng auf Haushalt und Lauf begrenzte Transaktion: die
        # Request-Transaktion ist bereits geschlossen. Rohzeilen bleiben fuer
        # einen erneuten Zuordnungsversuch erhalten.
        async with get_session_factory()() as failure_session, failure_session.begin():
            await failure_session.execute(
                text(
                    "update import_runs set status = 'failed', error_message = :message, "
                    "completed_at = now() where id = :id and household_id = :household_id "
                    "and status = 'pending'"
                ),
                {"id": str(run_id), "household_id": str(household_id), "message": message},
            )
        raise HTTPException(status_code=503, detail=message) from exc


@router.post("/{run_id}/reparse", response_model=ImportRun)
async def reparse_import(
    run_id: UUID,
    payload: ImportReparseInput,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ImportRun:
    """Liest denselben Lauf mit einer korrigierten Spaltenzuordnung erneut ein.

    Der Unterschied zu PATCH /{run_id}: dort wird ausschliesslich die
    Verknuepfung zum Mapping-Profil geaendert und NICHTS neu geparst - die
    Oberflaeche versprach damit eine Wirkung, die nicht eintrat. Hier wird die
    Zuordnung am Lauf vermerkt und die Verarbeitung erneut eingereiht; der
    Worker baut die Staging-Zeilen aus den bereits gespeicherten Rohzeilen neu
    auf, ein erneuter Upload ist nicht noetig.

      200 - Lauf steht wieder auf 'pending', der Client pollt wie nach dem Upload
      400 - Zuordnung ohne Datums- oder Betragsspalte (damit laesst sich keine
            Buchung bilden)
      404 - Lauf unbekannt
      409 - Status laesst kein erneutes Einlesen zu, aus dem Lauf wurden bereits
            Zeilen uebernommen, oder es gibt keine Rohzeilen mehr"""
    session, household_id = ctx
    try:
        run = await reparse_import_run(
            session, run_id, household_id, user.id, payload.column_mapping
        )
    except ImportRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ImportRunMappingIncompleteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (
        ImportRunNotReparsableError,
        ImportRunReparseCommittedError,
        ImportRunNoStagedRowsError,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    result = ImportRun(**run)
    await session.commit()
    await _dispatch_import(run_id, household_id, str(run["filename"]), "")
    return result


@router.get("/{run_id}/rows", response_model=list[ImportRow])
async def list_import_rows(run_id: UUID, ctx: HouseholdSession) -> list[ImportRow]:
    session, household_id = ctx
    run = (
        await session.execute(
            text(
                "select account_id from import_runs "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(run_id), "household_id": str(household_id)},
        )
    ).first()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="import_run_not_found"
        )
    account_id = run.account_id

    rows = (
        await session.execute(
            text(
                "select id, import_run_id, row_number, raw_data, external_reference, "
                "booked_date, value_date, amount_minor, currency, payee_raw, purpose, "
                "bank_transaction_code, fingerprint, status, duplicate_of_transaction_id, "
                "committed_transaction_id from import_rows "
                "where import_run_id = :run_id order by row_number"
            ),
            {"run_id": str(run_id)},
        )
    ).all()

    # Kategorieherkunft je Staging-Zeile: welche aktive Regel wuerde greifen?
    # Der Client zeigt damit "durch Regel vorgeschlagen" bzw. "ohne Kategorie"
    # an und kann pro Zeile manuell ueberschreiben (Kategorisierungsplan,
    # "Review und Import"). Nur fuer noch nicht committete Zeilen sinnvoll -
    # eine bereits gebuchte Zeile traegt ihre Kategorie am Split.
    result: list[ImportRow] = []
    rules = (
        await load_active_classification_rules(session, household_id)
        if any(row.status != "committed" for row in rows)
        else []
    )
    category_names: dict[UUID, str | None] = {}
    for row in rows:
        data = dict(row._mapping)
        suggested_id: UUID | None = None
        if data["status"] != "committed":
            match = await match_rules_for_transaction(
                session,
                household_id,
                data["payee_raw"],
                data["purpose"],
                data["amount_minor"] or 0,
                account_id,
                rules=rules,
            )
            if match is not None:
                suggested_id = match["category_id"]
        if suggested_id is not None:
            if suggested_id not in category_names:
                category = await category_service.load_category(session, household_id, suggested_id)
                # Archivierte Kategorien auch aus dem vorgeladenen Regelsatz
                # nicht anbieten. Der Cache lebt nur fuer diesen Request.
                category_names[suggested_id] = (
                    category["name"]
                    if category is not None and not category["is_archived"]
                    else None
                )
            if category_names[suggested_id] is not None:
                data["suggested_category_id"] = suggested_id
                data["suggested_category_name"] = category_names[suggested_id]
                data["category_source"] = "rule"
        result.append(ImportRow(**data))
    return result


@router.post("/{run_id}/commit", response_model=ImportCommitResult)
async def commit_import(
    run_id: UUID,
    payload: ImportCommitInput,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
) -> ImportCommitResult:
    """Uebernimmt den Importlauf. Die drei fachlichen Ablehnungsgruende werden
    unterschieden, statt sie - wie frueher - als eine einzige Ausnahme und
    damit als HTTP 500 auszuliefern (im Web las der Nutzer daraufhin "Die API
    ist nicht erreichbar", obwohl nur das Zielkonto fehlte):
      404 - Lauf existiert nicht (oder gehoert einem anderen Haushalt)
      409 - Lauf ist nicht im Status 'ready' (tatsaechlicher Status in detail)
      400 - Lauf hat kein Zielkonto"""
    session, household_id = ctx
    try:
        committed = await commit_import_run(
            session,
            run_id,
            household_id,
            payload.row_ids,
            payload.category_by_row,
            committed_by=user.id,
        )
    except ImportRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ImportRunNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ImportRunAccountMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if committed:
        background_tasks.add_task(
            enqueue_contract_reconcile, household_id, context=f"import_run={run_id}"
        )
    return ImportCommitResult(committed=committed)


@router.get("/{run_id}/revert-preview", response_model=ImportRevertPreview)
async def preview_import_revert_endpoint(
    run_id: UUID, ctx: HouseholdSession
) -> ImportRevertPreview:
    """Was wuerde ein Zuruecknehmen dieses Laufs anfassen? Aendert nichts.

      200 - Vorschau (auch wenn nichts zurueckzunehmen ist: revertable = 0)
      404 - Lauf unbekannt

    Ein GET, weil es eine Frage ist und keine Handlung - die Oberflaeche darf
    ihn beim Oeffnen des Bestaetigungsdialogs frei stellen."""
    session, household_id = ctx
    try:
        return ImportRevertPreview(**await preview_import_revert(session, run_id, household_id))
    except ImportRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{run_id}/revert", response_model=ImportRevertResult)
async def revert_import_endpoint(
    run_id: UUID,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    background_tasks: BackgroundTasks,
    payload: ImportRevertInput | None = None,
) -> ImportRevertResult:
    """Nimmt die Buchungen eines uebernommenen Laufs wieder aus dem Bestand.

      200 - zurueckgenommen (die Antwort nennt auch, was stehen blieb)
      404 - Lauf unbekannt

    Das Gegenstueck zu DELETE /{run_id}, das einen uebernommenen Lauf
    ausdruecklich ablehnt: dort ginge die Herkunft der Buchungen verloren, hier
    bleiben Lauf UND Buchungen bestehen - letztere nur archiviert. Deshalb auch
    kein 409 fuer einen nicht uebernommenen Lauf: dann ist schlicht nichts
    zurueckzunehmen, und die Antwort sagt das mit reverted = 0.

    Der Rumpf ist optional: ohne ihn bleiben verknuepfte Buchungen stehen."""
    session, household_id = ctx
    include_linked = payload.include_linked if payload is not None else False
    try:
        result = await revert_import_run(
            session, run_id, household_id, user.id, include_linked=include_linked
        )
    except ImportRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TransactionDetachForbiddenError as exc:
        # Siehe DELETE /v1/transactions/{id}: wer nicht aufraeumen darf,
        # nimmt auch nichts zurueck. Halb waere schlimmer als gar nicht.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    # Wurden Vertragsfaelligkeiten wieder geoeffnet, muss der Abgleich
    # next_due_date und die Fehlzaehler nachziehen. Aus demselben Grund wie beim
    # Uebernehmen als Hintergrundaufgabe: die Nachricht darf erst raus, wenn die
    # Aenderung committet ist - sonst saehe der Worker die Faelligkeit noch als
    # zugeordnet. Siehe worker_dispatch.enqueue_contract_reconcile.
    if any(item["kind"].startswith("vertrag") for item in result["untangled"]):
        background_tasks.add_task(
            enqueue_contract_reconcile, household_id, context=f"import_run={run_id}"
        )

    return ImportRevertResult(**result)


@router.post("/{run_id}/mapping-suggestion", response_model=ImportMappingSuggestion)
async def suggest_import_mapping(
    run_id: UUID, payload: ImportMappingSuggestionRequest, ctx: HouseholdEditorSession
) -> ImportMappingSuggestion:
    """Laesst die lokale KI ein Spalten-Mapping VORSCHLAGEN (Datum, Betrag,
    Verwendungszweck, Empfaenger, Waehrung, Datumsformat, Dezimaltrennzeichen).

    Der Vorschlag wird nie angewendet - die Antwort ist reine Information fuer
    den Mapping-Dialog; deterministischer Parser und gespeichertes
    Mapping-Profil behalten den Vorrang als Vorbelegung. Ein KI-Ausfall ist
    kein Fehler des Imports: die Antwort bleibt HTTP 200 mit
    available=false und einer Begruendung. Der Endpunkt schreibt (ausser der
    Audit-Zeile in llm_calls) nichts - er verlangt trotzdem Editor-Rechte,
    weil er einen Modellaufruf ausloest und llm_calls nur mit Schreibrecht
    beschrieben werden darf."""
    session, household_id = ctx
    try:
        return await suggest_column_mapping(session, household_id, run_id, payload.sample_rows)
    except ImportRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
