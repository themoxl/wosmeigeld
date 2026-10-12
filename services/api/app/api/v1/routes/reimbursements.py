"""API fuer PKV-/Beihilfe-Vorgaenge, Dokumente und Bankabgleich."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession
from worker.actors import drain_ai_task_queue as drain_queue_actor

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.core.config import get_settings
from app.schemas.reimbursements import (
    BulkReimbursementMarkInput,
    BulkReimbursementMarkResult,
    CaseCandidate,
    DocumentCaseLink,
    DocumentCaseLinkResult,
    DocumentCasePreview,
    DocumentExtraction,
    ExtractionConfirmation,
    ExtractionConfirmationResult,
    InsuredPerson,
    InsuredPersonCreate,
    MarkToCaseInput,
    MarkToCaseResult,
    OpenReimbursementMark,
    ReimbursementBulkReprocess,
    ReimbursementCase,
    ReimbursementCaseCreate,
    ReimbursementCaseUpdate,
    ReimbursementDecision,
    ReimbursementDocument,
    ReimbursementDocumentUpload,
    ReimbursementMark,
    ReimbursementMarkInput,
    ReimbursementPayer,
    ReimbursementPayerCreate,
    ReimbursementSummary,
    RuleMarkWithdrawResult,
    TransactionAllocation,
    TransactionAllocationCreate,
    TransactionAllocationView,
    TransactionCandidate,
)
from app.services import (
    health_document_service,
    reimbursement_mark_service,
    reimbursement_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reimbursements", tags=["reimbursements"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[tuple[AsyncSession, UUID], Depends(require_household_editor)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.get("/people", response_model=list[InsuredPerson])
async def list_people(session_ctx: HouseholdSession) -> list[InsuredPerson]:
    db, household_id = session_ctx
    return await reimbursement_service.list_people(db, household_id)


@router.post("/people", response_model=InsuredPerson, status_code=status.HTTP_201_CREATED)
async def create_person(
    payload: InsuredPersonCreate,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> InsuredPerson:
    db, household_id = session_ctx
    return await reimbursement_service.create_person(db, household_id, user.id, payload)


@router.get("/payers", response_model=list[ReimbursementPayer])
async def list_payers(session_ctx: HouseholdSession) -> list[ReimbursementPayer]:
    db, household_id = session_ctx
    return await reimbursement_service.list_payers(db, household_id)


@router.post("/payers", response_model=ReimbursementPayer, status_code=status.HTTP_201_CREATED)
async def create_payer(
    payload: ReimbursementPayerCreate, session_ctx: HouseholdEditorSession
) -> ReimbursementPayer:
    db, household_id = session_ctx
    return await reimbursement_service.create_payer(db, household_id, payload)


@router.get("/summary", response_model=ReimbursementSummary)
async def get_summary(
    session_ctx: HouseholdSession,
    year: int = Query(default_factory=lambda: date.today().year, ge=2000, le=2200),
) -> ReimbursementSummary:
    db, household_id = session_ctx
    return await reimbursement_service.get_summary(db, household_id, year)


@router.get("/cases", response_model=list[ReimbursementCase])
async def list_cases(
    session_ctx: HouseholdSession,
    year: int | None = Query(default=None, ge=2000, le=2200),
    person_id: UUID | None = None,
    overall_status: Literal["draft", "action_required", "waiting", "reconciled"] | None = None,
    search: str | None = Query(default=None, max_length=160),
) -> list[ReimbursementCase]:
    db, household_id = session_ctx
    return await reimbursement_service.list_cases(
        db,
        household_id,
        year=year,
        person_id=person_id,
        overall_status=overall_status,
        search=search,
    )


@router.post("/cases", response_model=ReimbursementCase, status_code=status.HTTP_201_CREATED)
async def create_case(
    payload: ReimbursementCaseCreate, session_ctx: HouseholdEditorSession
) -> ReimbursementCase:
    db, household_id = session_ctx
    return await reimbursement_service.create_case(db, household_id, payload)


@router.get("/cases/{case_id}", response_model=ReimbursementCase)
async def get_case(case_id: UUID, session_ctx: HouseholdSession) -> ReimbursementCase:
    db, household_id = session_ctx
    return await reimbursement_service.get_case(db, household_id, case_id)


@router.patch("/cases/{case_id}", response_model=ReimbursementCase)
async def update_case(
    case_id: UUID,
    payload: ReimbursementCaseUpdate,
    session_ctx: HouseholdEditorSession,
) -> ReimbursementCase:
    db, household_id = session_ctx
    return await reimbursement_service.update_case(db, household_id, case_id, payload)


@router.get("/documents", response_model=list[ReimbursementDocument])
async def list_documents(
    session_ctx: HouseholdSession,
    person_id: UUID | None = None,
    case_id: UUID | None = None,
) -> list[ReimbursementDocument]:
    db, household_id = session_ctx
    return await health_document_service.list_documents(
        db, household_id, person_id=person_id, case_id=case_id
    )


async def _read_limited(upload: UploadFile) -> bytes:
    maximum = get_settings().health_document_max_bytes
    chunks: list[bytes] = []
    consumed = 0
    while chunk := await upload.read(min(1024 * 1024, maximum + 1 - consumed)):
        consumed += len(chunk)
        if consumed > maximum:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="health_document_too_large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/documents", response_model=ReimbursementDocumentUpload, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
    person_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    case_id: Annotated[UUID | None, Form()] = None,
) -> ReimbursementDocumentUpload:
    db, household_id = session_ctx
    content = await _read_limited(file)
    result = await health_document_service.upload_document(
        db, household_id, user.id, person_id, case_id, file.filename, content
    )
    if result.task_id is not None:
        try:
            drain_queue_actor.send(str(household_id))
        except Exception:
            # Der persistente Task bleibt erhalten und wird vom periodischen
            # Queue-Recovery geweckt. Der Upload darf deshalb nicht als
            # fehlgeschlagen erscheinen und ein privates Storage-Objekt
            # verwaisen lassen.
            logger.exception("Dokument-Queue konnte nicht sofort geweckt werden")
    return result


@router.post("/documents/reprocess-all", response_model=ReimbursementBulkReprocess)
async def reprocess_all_documents(
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ReimbursementBulkReprocess:
    db, household_id = session_ctx
    result = await health_document_service.reprocess_all_documents(db, household_id, user.id)
    if result.task_id is not None:
        try:
            drain_queue_actor.send(str(household_id))
        except Exception:
            logger.exception("Dokument-Queue konnte nicht sofort geweckt werden")
    return result


@router.get("/documents/{document_id}", response_model=ReimbursementDocument)
async def get_document(document_id: UUID, session_ctx: HouseholdSession) -> ReimbursementDocument:
    db, household_id = session_ctx
    return await health_document_service.get_document(db, household_id, document_id)


@router.post(
    "/documents/{document_id}/reprocess", response_model=ReimbursementDocumentUpload
)
async def reprocess_document(
    document_id: UUID,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ReimbursementDocumentUpload:
    db, household_id = session_ctx
    result = await health_document_service.reprocess_document(
        db, household_id, user.id, document_id
    )
    try:
        drain_queue_actor.send(str(household_id))
    except Exception:
        logger.exception("Dokument-Queue konnte nicht sofort geweckt werden")
    return result


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID, session_ctx: HouseholdEditorSession
) -> Response:
    db, household_id = session_ctx
    await health_document_service.delete_document(db, household_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}/download")
async def download_document(document_id: UUID, session_ctx: HouseholdSession) -> Response:
    db, household_id = session_ctx
    result = await health_document_service.get_authorized_content(db, household_id, document_id)
    filename = quote(result.document.original_filename, safe="")
    return Response(
        content=result.content,
        media_type=result.document.mime_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get("/documents/{document_id}/extraction", response_model=DocumentExtraction)
async def get_extraction(document_id: UUID, session_ctx: HouseholdSession) -> DocumentExtraction:
    db, household_id = session_ctx
    return await health_document_service.latest_extraction(db, household_id, document_id)


@router.get("/documents/{document_id}/pages/{page_number}")
async def document_page(
    document_id: UUID, page_number: int, session_ctx: HouseholdSession
) -> Response:
    from worker.health_documents.reading import render_image

    db, household_id = session_ctx
    result = await health_document_service.get_authorized_content(db, household_id, document_id)
    if result.document.mime_type != "application/pdf" or page_number < 1 \
            or page_number > (result.document.page_count or 0):
        raise HTTPException(status_code=404, detail="health_document_page_not_found")
    png = await asyncio.to_thread(render_image, result.content, page_number)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, no-store",
                             "X-Content-Type-Options": "nosniff"})


@router.post(
    "/documents/{document_id}/confirm-extraction",
    response_model=ExtractionConfirmationResult,
)
async def confirm_extraction(
    document_id: UUID,
    payload: ExtractionConfirmation,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ExtractionConfirmationResult:
    db, household_id = session_ctx
    return await health_document_service.confirm_extraction(
        db, household_id, user.id, document_id, payload
    )


@router.get("/documents/{document_id}/case-candidates", response_model=list[CaseCandidate])
async def document_case_candidates(
    document_id: UUID, session_ctx: HouseholdSession,
    item_index: int | None = Query(default=None, ge=0, le=199),
) -> list[CaseCandidate]:
    db, household_id = session_ctx
    return await health_document_service.case_candidates(db, household_id, document_id, item_index)


@router.get("/cases/{case_id}/decisions", response_model=list[ReimbursementDecision])
async def case_decisions(
    case_id: UUID, session_ctx: HouseholdSession,
) -> list[ReimbursementDecision]:
    from sqlalchemy import text

    db, household_id = session_ctx
    await reimbursement_service.get_case(db, household_id, case_id)
    rows = (await db.execute(text(
        "select s.id, s.case_id, s.payer_id, p.name as payer_name, p.payer_type, "
        "s.source_document_id, s.source_item_index, s.external_reference, s.approved_minor, "
        "s.deductible_minor, s.noncovered_minor, s.status "
        "from reimbursement_submissions s join reimbursement_payers p on p.id = s.payer_id "
        "where s.household_id = :household_id and s.case_id = :case_id "
        "order by s.created_at"
    ), {"household_id": str(household_id), "case_id": str(case_id)})).all()
    return [ReimbursementDecision(**row._mapping) for row in rows]


@router.post("/documents/{document_id}/case", response_model=DocumentCaseLinkResult)
async def link_document_case(
    document_id: UUID,
    payload: DocumentCaseLink,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> DocumentCaseLinkResult:
    db, household_id = session_ctx
    return await health_document_service.link_document_to_case(
        db, household_id, user.id, document_id, payload
    )


@router.post("/documents/{document_id}/case-preview", response_model=DocumentCasePreview)
async def preview_document_case(
    document_id: UUID, payload: DocumentCaseLink, session_ctx: HouseholdSession
) -> DocumentCasePreview:
    db, household_id = session_ctx
    return await health_document_service.preview_document_case(
        db, household_id, document_id, payload
    )


@router.delete("/documents/{document_id}/case", response_model=ReimbursementDocument)
async def unlink_document_case(
    document_id: UUID, session_ctx: HouseholdEditorSession,
    item_index: int | None = Query(default=None, ge=0, le=199),
) -> ReimbursementDocument:
    db, household_id = session_ctx
    return await health_document_service.unlink_document_case(
        db, household_id, document_id, item_index
    )


@router.get("/cases/{case_id}/transaction-candidates", response_model=list[TransactionCandidate])
async def transaction_candidates(
    case_id: UUID,
    session_ctx: HouseholdSession,
    allocation_type: Literal["provider_payment", "reimbursement"] = "reimbursement",
) -> list[TransactionCandidate]:
    db, household_id = session_ctx
    return await reimbursement_service.transaction_candidates(
        db, household_id, case_id, allocation_type
    )


@router.post(
    "/cases/{case_id}/allocations",
    response_model=TransactionAllocation,
    status_code=status.HTTP_201_CREATED,
)
async def create_allocation(
    case_id: UUID,
    payload: TransactionAllocationCreate,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> TransactionAllocation:
    db, household_id = session_ctx
    return await reimbursement_service.create_allocation(
        db, household_id, user.id, case_id, payload
    )


@router.get("/cases/{case_id}/allocations", response_model=list[TransactionAllocationView])
async def list_allocations(
    case_id: UUID,
    session_ctx: HouseholdSession,
) -> list[TransactionAllocationView]:
    db, household_id = session_ctx
    return await reimbursement_service.list_allocations(db, household_id, case_id)


@router.delete(
    "/cases/{case_id}/allocations/{allocation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_allocation(
    case_id: UUID,
    allocation_id: UUID,
    session_ctx: HouseholdEditorSession,
) -> Response:
    db, household_id = session_ctx
    await reimbursement_service.delete_allocation(db, household_id, case_id, allocation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Erstattungsfaehigkeit am Buchungsstrom
# ---------------------------------------------------------------------------
# Diese Endpunkte stehen bewusst NICHT unter der Gesundheits-RLS: eine Marke
# beschreibt eine Bankbuchung, die jedes Haushaltsmitglied ohnehin sieht.
# "/marks/open" steht vor "/marks/{transaction_id}", damit das feste Segment
# nicht als UUID gelesen wird.


@router.get("/marks/open", response_model=list[OpenReimbursementMark])
async def list_open_marks(
    session_ctx: HouseholdSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    only_open: bool = True,
) -> list[OpenReimbursementMark]:
    """Marken samt Buchung. Standard ist die offene Liste.

    `only_open=false` liefert auch ausgenommene und bereits umgewandelte -
    die Buchungsliste braucht sie, um an jeder Zeile den Zustand zu zeigen.
    """
    db, household_id = session_ctx
    return await reimbursement_mark_service.list_marks(
        db, household_id, only_open=only_open, limit=limit
    )


@router.post("/marks/bulk/preview", response_model=BulkReimbursementMarkResult)
async def preview_bulk_marks(
    payload: BulkReimbursementMarkInput, session_ctx: HouseholdEditorSession
) -> BulkReimbursementMarkResult:
    """Zeigt je Buchung, was das Anwenden taete - ohne etwas zu schreiben."""
    db, household_id = session_ctx
    return await reimbursement_mark_service.bulk_set_marks(db, household_id, payload, write=False)


@router.post("/marks/bulk/apply", response_model=BulkReimbursementMarkResult)
async def apply_bulk_marks(
    payload: BulkReimbursementMarkInput, session_ctx: HouseholdEditorSession
) -> BulkReimbursementMarkResult:
    """Wendet die Mehrfachauswahl an und meldet jede Buchung einzeln."""
    db, household_id = session_ctx
    return await reimbursement_mark_service.bulk_set_marks(db, household_id, payload, write=True)


@router.put("/marks/{transaction_id}", response_model=ReimbursementMark)
async def set_mark(
    transaction_id: UUID,
    payload: ReimbursementMarkInput,
    session_ctx: HouseholdEditorSession,
) -> ReimbursementMark:
    """Markiert eine Buchung von Hand - schlaegt jede Automatik."""
    db, household_id = session_ctx
    try:
        return await reimbursement_mark_service.set_mark(
            db, household_id, transaction_id, payload
        )
    except reimbursement_mark_service.MarkTransactionMissing as missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="transaction_not_found",
        ) from missing
    except reimbursement_mark_service.MarkRejected as rejected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=reimbursement_mark_service.describe(rejected.code),
        ) from rejected


@router.delete("/marks/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_mark(
    transaction_id: UUID, session_ctx: HouseholdEditorSession
) -> Response:
    """Nimmt die Markierung ganz zurueck - Regeln duerfen danach wieder greifen.

    Ausdruecklich etwas anderes als "ausnehmen": dafuer wird die Marke auf
    `state='excluded'` gesetzt und bleibt dann unantastbar.
    """
    db, household_id = session_ctx
    removed = await reimbursement_mark_service.remove_mark(db, household_id, transaction_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Keine zuruecknehmbare Markierung an dieser Buchung.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/marks/by-rule/{rule_id}/withdraw", response_model=RuleMarkWithdrawResult)
async def withdraw_rule_marks(
    rule_id: UUID,
    session_ctx: HouseholdEditorSession,
    preview: bool = True,
) -> RuleMarkWithdrawResult:
    """Nimmt alle Marken einer Regel zurueck - Handentscheidungen bleiben.

    Standard ist die Vorschau. Eine Ruecknahme, die ungefragt hunderte Marken
    entfernt, ist genauso unangenehm wie eine Regel, die ungefragt hunderte
    setzt.
    """
    db, household_id = session_ctx
    return RuleMarkWithdrawResult(
        **await reimbursement_mark_service.withdraw_rule_marks(
            db, household_id, rule_id, write=not preview
        )
    )


@router.post("/marks/{transaction_id}/case", response_model=MarkToCaseResult)
async def convert_mark_to_case(
    transaction_id: UUID,
    payload: MarkToCaseInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> MarkToCaseResult:
    """Macht aus einer Markierung einen Erstattungsvorgang.

    Danach steht der Vorgang mit `payment_status='paid'` und einer Erwartung da -
    und die Statusableitung meldet ihn als `action_required`. Genau das kam
    bisher nie, weil eine Arztrechnung keinen Erstattungsbetrag nennt.
    """
    db, household_id = session_ctx
    try:
        case_id = await reimbursement_mark_service.convert_to_case(
            db, household_id, user.id, transaction_id, payload.insured_person_id
        )
    except reimbursement_mark_service.MarkNotFound as missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="An dieser Buchung haengt keine Markierung.",
        ) from missing
    except reimbursement_mark_service.MarkAlreadyConverted as done:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Aus dieser Markierung ist bereits ein Vorgang geworden.",
        ) from done
    except reimbursement_mark_service.MarkNotConvertible as wrong:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Eine ausgenommene Buchung ergibt keinen Vorgang.",
        ) from wrong
    return MarkToCaseResult(case_id=case_id)
