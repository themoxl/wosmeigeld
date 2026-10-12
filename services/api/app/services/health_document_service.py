"""Lebenszyklus privater Gesundheitsdokumente und bestaetigter Extraktionen."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.schemas.reimbursements import (
    CaseCandidate,
    DocumentCaseLink,
    DocumentCaseLinkResult,
    DocumentCasePreview,
    DocumentExtraction,
    ExtractionConfirmation,
    ExtractionConfirmationResult,
    ReimbursementBulkReprocess,
    ReimbursementCase,
    ReimbursementCaseCreate,
    ReimbursementCaseUpdate,
    ReimbursementDocument,
    ReimbursementDocumentUpload,
)
from app.services import reimbursement_decisions, reimbursement_service
from app.services.health_document_security import validate_health_document
from app.services.health_document_storage import HealthDocumentStorage

_DOCUMENT_COLUMNS = (
    "id, insured_person_id, case_id, document_type, original_filename, sha256, "
    "mime_type, size_bytes, page_count, processing_status, extraction_confidence, "
    "error_code, error_message, created_at, updated_at, "
    "coalesce((select jsonb_agg(distinct l.case_id) from reimbursement_document_links l "
    "where l.document_id = reimbursement_documents.id), '[]'::jsonb) as linked_case_ids, "
    "coalesce((select jsonb_agg(jsonb_build_object('case_id', l.case_id, "
    "'item_index', nullif(l.item_index, -1))) from reimbursement_document_links l "
    "where l.document_id = reimbursement_documents.id), '[]'::jsonb) as linked_items"
)
_EXTRACTION_COLUMNS = (
    "id, document_id, extractor_version, schema_version, model_name, status, confidence, "
    "structured_data, field_evidence, validation_warnings, confirmed_data, confirmed_by, "
    "confirmed_at, created_at, previous_confirmed_data"
)


@dataclass(frozen=True)
class AuthorizedDocumentContent:
    document: ReimbursementDocument
    content: bytes


async def _document_row(
    session: AsyncSession, household_id: UUID, document_id: UUID
) -> Row[Any] | None:
    return (
        await session.execute(
            text(
                f"select {_DOCUMENT_COLUMNS} from reimbursement_documents "
                "where household_id = :household_id and id = :document_id"
            ),
            {"household_id": str(household_id), "document_id": str(document_id)},
        )
    ).first()


async def _require_person(session: AsyncSession, household_id: UUID, person_id: UUID) -> None:
    row = (
        await session.execute(
            text(
                "select id from health_insured_persons where household_id = :household_id "
                "and id = :person_id and is_active"
            ),
            {"household_id": str(household_id), "person_id": str(person_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="insured_person_not_found"
        )


async def list_documents(
    session: AsyncSession,
    household_id: UUID,
    *,
    person_id: UUID | None = None,
    case_id: UUID | None = None,
) -> list[ReimbursementDocument]:
    conditions = ["household_id = :household_id"]
    params: dict[str, object] = {"household_id": str(household_id)}
    if person_id is not None:
        conditions.append("insured_person_id = :person_id")
        params["person_id"] = str(person_id)
    if case_id is not None:
        conditions.append("(case_id = :case_id or exists (select 1 from "
                          "reimbursement_document_links l where l.document_id = "
                          "reimbursement_documents.id and l.case_id = :case_id))")
        params["case_id"] = str(case_id)
    rows = (
        await session.execute(
            text(
                f"select {_DOCUMENT_COLUMNS} from reimbursement_documents where "
                + " and ".join(conditions)
                + " order by created_at desc"
            ),
            params,
        )
    ).all()
    return [ReimbursementDocument(**row._mapping) for row in rows]


async def get_document(
    session: AsyncSession, household_id: UUID, document_id: UUID
) -> ReimbursementDocument:
    row = await _document_row(session, household_id, document_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="health_document_not_found"
        )
    return ReimbursementDocument(**row._mapping)


async def _enqueue_extraction_task(
    session: AsyncSession, household_id: UUID, user_id: UUID, document_id: UUID
) -> UUID:
    # Derselbe Advisory-Key wie der Worker-Claim. Der Upload-Commit und das
    # finale "Queue leer" des Workers koennen dadurch nicht aneinander
    # vorbeilaufen und ein spaet hochgeladenes Dokument liegen lassen.
    await session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:household_id, 0))"),
        {"household_id": str(household_id)},
    )
    existing = (
        await session.execute(
            text(
                "select id from ai_tasks where household_id = :household_id "
                "and task_type = 'health_document_extraction' "
                "and status in ('queued', 'running') order by queue_order limit 1"
            ),
            {"household_id": str(household_id)},
        )
    ).first()
    if existing is not None:
        return UUID(str(existing.id))

    task_id = UUID(
        str(
            (
                await session.execute(
                    text(
                        "insert into ai_tasks "
                        "(household_id, task_type, input, route_path, created_by, status_message) "
                        "values (:household_id, 'health_document_extraction', "
                        "cast(:input as jsonb), '/erstattungen', :created_by, "
                        "'Dokument wartet auf die sichere Auswertung') returning id"
                    ),
                    {
                        "household_id": str(household_id),
                        "input": json.dumps({"document_id": str(document_id)}),
                        "created_by": str(user_id),
                    },
                )
            ).scalar_one()
        )
    )
    await session.execute(
        text("update ai_tasks set route_path = :route where id = :task_id"),
        {"task_id": str(task_id), "route": f"/erstattungen?ai_task={task_id}&tab=dokumente"},
    )
    await session.execute(
        text(
            "insert into ai_task_events (task_id, household_id, stage, message) "
            "values (:task_id, :household_id, 'queued', "
            "'Dokumentauswertung wurde in die Warteschlange aufgenommen.')"
        ),
        {"task_id": str(task_id), "household_id": str(household_id)},
    )
    return task_id


async def upload_document(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    person_id: UUID,
    case_id: UUID | None,
    filename: str | None,
    content: bytes,
) -> ReimbursementDocumentUpload:
    await _require_person(session, household_id, person_id)
    if case_id is not None:
        case = await reimbursement_service.get_case(session, household_id, case_id)
        if case.person_id != person_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="health_document_case_person_mismatch",
            )

    settings = get_settings()
    validated = validate_health_document(content, filename, settings)
    sha256 = hashlib.sha256(content).hexdigest()
    duplicate = (
        await session.execute(
            text(
                f"select {_DOCUMENT_COLUMNS} from reimbursement_documents "
                "where household_id = :household_id and insured_person_id = :person_id "
                "and sha256 = :sha256"
            ),
            {
                "household_id": str(household_id),
                "person_id": str(person_id),
                "sha256": sha256,
            },
        )
    ).first()
    if duplicate is not None:
        return ReimbursementDocumentUpload(
            document=ReimbursementDocument(**duplicate._mapping), duplicate=True
        )

    document_id = uuid4()
    object_key = (
        f"households/{household_id}/health/{person_id}/{document_id}/original{validated.extension}"
    )
    storage = HealthDocumentStorage(settings)
    await storage.put(object_key, content, validated.mime_type)
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        "insert into reimbursement_documents "
                        "(id, household_id, insured_person_id, case_id, original_filename, "
                        "object_key, sha256, mime_type, size_bytes, page_count, created_by) "
                        "values (:id, :household_id, :person_id, :case_id, :filename, "
                        ":object_key, :sha256, :mime_type, :size_bytes, :page_count, :user_id) "
                        f"returning {_DOCUMENT_COLUMNS}"
                    ),
                    {
                        "id": str(document_id),
                        "household_id": str(household_id),
                        "person_id": str(person_id),
                        "case_id": str(case_id) if case_id else None,
                        "filename": validated.filename,
                        "object_key": object_key,
                        "sha256": sha256,
                        "mime_type": validated.mime_type,
                        "size_bytes": len(content),
                        "page_count": validated.page_count,
                        "user_id": str(user_id),
                    },
                )
            ).one()
    except IntegrityError:
        await storage.delete(object_key)
        duplicate = (
            await session.execute(
                text(
                    f"select {_DOCUMENT_COLUMNS} from reimbursement_documents "
                    "where household_id = :household_id and insured_person_id = :person_id "
                    "and sha256 = :sha256"
                ),
                {
                    "household_id": str(household_id),
                    "person_id": str(person_id),
                    "sha256": sha256,
                },
            )
        ).first()
        if duplicate is None:
            raise
        return ReimbursementDocumentUpload(
            document=ReimbursementDocument(**duplicate._mapping), duplicate=True
        )
    except Exception:
        await storage.delete(object_key)
        raise

    task_id = await _enqueue_extraction_task(session, household_id, user_id, document_id)
    return ReimbursementDocumentUpload(
        document=ReimbursementDocument(**row._mapping), task_id=task_id
    )


async def get_authorized_content(
    session: AsyncSession, household_id: UUID, document_id: UUID
) -> AuthorizedDocumentContent:
    row = (
        await session.execute(
            text(
                f"select {_DOCUMENT_COLUMNS}, object_key from reimbursement_documents "
                "where household_id = :household_id and id = :document_id"
            ),
            {"household_id": str(household_id), "document_id": str(document_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="health_document_not_found"
        )
    try:
        content = await HealthDocumentStorage(get_settings()).get(str(row.object_key))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="health_document_object_missing"
        ) from exc
    return AuthorizedDocumentContent(
        document=ReimbursementDocument(**row._mapping), content=content
    )


async def reprocess_document(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    document_id: UUID,
) -> ReimbursementDocumentUpload:
    document = await get_document(session, household_id, document_id)
    if document.processing_status in {"queued", "extracting"}:
        task_id = await _enqueue_extraction_task(
            session, household_id, user_id, document_id
        )
        return ReimbursementDocumentUpload(document=document, task_id=task_id)
    await session.execute(
        text(
            "update reimbursement_documents set processing_status = 'queued', "
            "error_code = null, error_message = null, updated_at = now() "
            "where id = :document_id and household_id = :household_id"
        ),
        {"document_id": str(document_id), "household_id": str(household_id)},
    )
    task_id = await _enqueue_extraction_task(session, household_id, user_id, document_id)
    queued = await get_document(session, household_id, document_id)
    return ReimbursementDocumentUpload(document=queued, task_id=task_id)


async def reprocess_all_documents(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
) -> ReimbursementBulkReprocess:
    """Stellt alle ruhenden Dokumente eines Haushalts erneut in die Warteschlange.

    Bereits wartende oder laufende Dokumente bleiben unangetastet - sie sind
    ohnehin schon eingereiht, und ein Zuruecksetzen wuerde dem Worker das
    Dokument unter den Fingern wegziehen.
    """
    rows = (
        await session.execute(
            text(
                "update reimbursement_documents set processing_status = 'queued', "
                "error_code = null, error_message = null, updated_at = now() "
                "where household_id = :household_id "
                "and processing_status not in ('queued', 'extracting') returning id"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    if not rows:
        return ReimbursementBulkReprocess(queued=0, task_id=None)
    task_id = await _enqueue_extraction_task(
        session, household_id, user_id, UUID(str(rows[0].id))
    )
    return ReimbursementBulkReprocess(queued=len(rows), task_id=task_id)


async def delete_document(
    session: AsyncSession, household_id: UUID, document_id: UUID
) -> None:
    row = (
        await session.execute(
            text(
                "select object_key from reimbursement_documents "
                "where household_id = :household_id and id = :document_id"
            ),
            {"household_id": str(household_id), "document_id": str(document_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="health_document_not_found"
        )
    await HealthDocumentStorage(get_settings()).delete(str(row.object_key))
    deleted = await session.execute(
        text(
            "delete from reimbursement_documents where household_id = :household_id "
            "and id = :document_id returning id"
        ),
        {"household_id": str(household_id), "document_id": str(document_id)},
    )
    if deleted.first() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="health_document_delete_conflict"
        )


async def latest_extraction(
    session: AsyncSession, household_id: UUID, document_id: UUID
) -> DocumentExtraction:
    await get_document(session, household_id, document_id)
    row = (
        await session.execute(
            text(
                f"select {_EXTRACTION_COLUMNS} from document_extractions "
                "where household_id = :household_id and document_id = :document_id "
                "order by created_at desc limit 1"
            ),
            {"household_id": str(household_id), "document_id": str(document_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="document_extraction_not_found"
        )
    return DocumentExtraction(**row._mapping)


def _effective_data(extraction: DocumentExtraction) -> dict[str, Any]:
    """Bestaetigte Felder schlagen den Vorschlag, sonst der Vorschlag."""
    source = (extraction.confirmed_data or extraction.previous_confirmed_data
              or extraction.structured_data)
    return source.model_dump(mode="json") if hasattr(source, "model_dump") else dict(source)


async def case_candidates(
    session: AsyncSession, household_id: UUID, document_id: UUID, item_index: int | None = None
) -> list[CaseCandidate]:
    extraction = await latest_extraction(session, household_id, document_id)
    data = reimbursement_decisions.decision_data(_effective_data(extraction), item_index)
    if item_index is None:
        data["_insured_person_id"] = (
            await get_document(session, household_id, document_id)
        ).insured_person_id
    # Eine Abrechnungsnummer ist keine Rechnungsnummer.
    if data.get("statement_number") == data.get("invoice_number"):
        data["invoice_number"] = None
    candidates = await reimbursement_service.case_candidates(
        session, household_id, document_id, data
    )
    for candidate in candidates:
        candidate.item_index = item_index
    return candidates


async def link_document_to_case(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    document_id: UUID,
    payload: DocumentCaseLink,
) -> DocumentCaseLinkResult:
    """Haengt ein Dokument an einen Vorgang und traegt seine Entscheidung ein.

    Nur eine Leistungsabrechnung veraendert dabei den Vorgang: sie ist die
    Entscheidung des Versicherers. Eine Einreichung belegt lediglich, dass
    etwas eingereicht wurde.
    """
    document = await get_document(session, household_id, document_id)
    locked = await reimbursement_service._require_case(
        session, household_id, payload.case_id, for_update=True
    )
    case = ReimbursementCase(**locked._mapping)
    if payload.expected_case_version is not None and payload.expected_case_version != case.version:
        raise HTTPException(status_code=409, detail="reimbursement_case_changed")
    extraction = await latest_extraction(session, household_id, document_id)
    data = reimbursement_decisions.decision_data(_effective_data(extraction), payload.item_index)
    if case.person_id != document.insured_person_id and payload.item_index is None:
        raise HTTPException(status_code=422, detail="health_document_case_person_mismatch")
    if reimbursement_service._person_name_match(data.get("person_name"), case.person_name) is False:
        raise HTTPException(status_code=422, detail="health_document_case_person_mismatch")
    if payload.apply_decision and data.get("document_type") == "benefit_statement":
        if extraction.confirmed_at is None:
            raise HTTPException(status_code=422, detail="statement_confirmation_required")
        case = await reimbursement_decisions.apply_decision(
            session, household_id, user_id, document_id, case, data,
            data.get("source_index") if data.get("source_index") is not None
            else payload.item_index,
            payload.payer_id,
        )
    elif extraction.confirmed_at is not None and data.get("document_type") == "submission_receipt":
        if case.decision_status in {"none", "processing"}:
            case = await reimbursement_service.update_case(
                session, household_id, case.id,
                ReimbursementCaseUpdate(submission_status="submitted", decision_status="processing")
            )
    elif extraction.confirmed_at is not None \
            and data.get("document_type") == "request_for_documents":
        case = await reimbursement_service.update_case(session, household_id, case.id,
            ReimbursementCaseUpdate(submission_status="documents_requested"))
    await reimbursement_decisions.link_item(
        session, household_id, document_id, case,
        data.get("source_index") if data.get("source_index") is not None else payload.item_index,
    )
    # Der alte 1:1-Verweis bleibt fuer einzelne Dokumente kompatibel. Eine
    # Sammelabrechnung hat ausschliesslich die expliziten Positionsverweise.
    if payload.item_index is None and case.person_id == document.insured_person_id:
        await session.execute(
            text("update reimbursement_documents set case_id = :case_id, updated_at = now() "
                 "where id = :document_id and household_id = :household_id"),
            {"case_id": str(case.id), "document_id": str(document_id),
             "household_id": str(household_id)},
        )

    return DocumentCaseLinkResult(
        document=await get_document(session, household_id, document_id), case=case
    )


async def preview_document_case(
    session: AsyncSession, household_id: UUID, document_id: UUID, payload: DocumentCaseLink
) -> DocumentCasePreview:
    document = await get_document(session, household_id, document_id)
    case = await reimbursement_service.get_case(session, household_id, payload.case_id)
    data = reimbursement_decisions.decision_data(
        _effective_data(await latest_extraction(session, household_id, document_id)),
        payload.item_index,
    )
    if case.person_id != document.insured_person_id and payload.item_index is None:
        raise HTTPException(status_code=422, detail="health_document_case_person_mismatch")
    if reimbursement_service._person_name_match(data.get("person_name"), case.person_name) is False:
        raise HTTPException(status_code=422, detail="health_document_case_person_mismatch")
    if data.get("document_type") != "benefit_statement" or not payload.apply_decision:
        return DocumentCasePreview(
            case_id=case.id, case_version=case.version,
            expected_before_minor=case.expected_minor, expected_after_minor=case.expected_minor,
            received_minor=case.reimbursed_minor, outstanding_after_minor=case.outstanding_minor,
            approved_minor=None, currency=case.currency,
        )
    return await reimbursement_decisions.preview_decision(
        session, household_id, document_id, case, data, payload.payer_id
    )


async def unlink_document_case(
    session: AsyncSession, household_id: UUID, document_id: UUID, item_index: int | None
) -> ReimbursementDocument:
    document = await get_document(session, household_id, document_id)
    row = (await session.execute(text(
        "select case_id from reimbursement_document_links where household_id = :household_id "
        "and document_id = :document_id and item_index = :item_index"
    ), {"household_id": str(household_id), "document_id": str(document_id),
        "item_index": item_index if item_index is not None else -1})).first()
    case_id = UUID(str(row.case_id)) if row else document.case_id if item_index is None else None
    if case_id is None:
        raise HTTPException(status_code=404, detail="statement_item_not_linked")
    locked = await reimbursement_service._require_case(
        session, household_id, case_id, for_update=True
    )
    case = ReimbursementCase(**locked._mapping)
    params = {"household_id": str(household_id), "document_id": str(document_id),
              "item_index": item_index if item_index is not None else -1}
    await session.execute(text(
        "delete from reimbursement_document_links where household_id = :household_id "
        "and document_id = :document_id and item_index = :item_index"
    ), params)
    await session.execute(text(
        "update reimbursement_submissions set status = 'draft', updated_at = now() "
        "where household_id = :household_id "
        "and source_document_id = :document_id and coalesce(source_item_index, -1) = :item_index"
    ), params)
    if item_index is None:
        await session.execute(text(
            "update reimbursement_documents set case_id = null, updated_at = now() "
            "where id = :document_id and household_id = :household_id"
        ), {"document_id": str(document_id), "household_id": str(household_id)})
    if document.document_type == "benefit_statement":
        await reimbursement_decisions.refresh_case(session, household_id, case)
    await reimbursement_service._event(session, case.id, case.person_id,
                                        "document_unlinked", "Dokumentzuordnung geloest",
                                        {"document_id": str(document_id), "item_index": item_index})
    return await get_document(session, household_id, document_id)


async def confirm_extraction(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    document_id: UUID,
    payload: ExtractionConfirmation,
) -> ExtractionConfirmationResult:
    document = await get_document(session, household_id, document_id)
    extraction = await latest_extraction(session, household_id, document_id)
    data = payload.structured_data.model_dump(mode="json")
    source_indices = {item["source_index"] for item in data["items"]
                      if item.get("source_index") is not None}
    source_indices.update(link.item_index for link in document.linked_items
                          if link.item_index is not None)
    for item in data["items"]:
        if item.get("source_index") is None:
            available = next((index for index in range(200) if index not in source_indices), None)
            if available is None:
                raise HTTPException(status_code=422, detail="statement_item_limit_exceeded")
            item["source_index"] = available
            source_indices.add(available)
    linked_indices = {link.item_index for link in document.linked_items
                      if link.item_index is not None}
    if not linked_indices.issubset({item["source_index"] for item in data["items"]}):
        raise HTTPException(status_code=409, detail="statement_linked_item_removed")
    if data["document_type"] in {"invoice", "prescription"} and data.get("person_name"):
        person = (await session.execute(text(
            "select display_name from health_insured_persons where id = :id "
            "and household_id = :household_id"
        ), {"id": str(document.insured_person_id), "household_id": str(household_id)})).one()
        if reimbursement_service._person_name_match(
            data["person_name"], person.display_name
        ) is False:
            raise HTTPException(status_code=422, detail="health_document_case_person_mismatch")

    case: ReimbursementCase | None = None
    if document.case_id is not None:
        case = ReimbursementCase(**(await reimbursement_service._require_case(
            session, household_id, document.case_id, for_update=True
        ))._mapping)
        if payload.expected_case_version is not None \
                and payload.expected_case_version != case.version:
            raise HTTPException(status_code=409, detail="reimbursement_case_changed")
        if data.get("document_type") in {"invoice", "prescription"}:
            if not data.get("provider_name") or not data.get("invoice_date") \
                    or data.get("billed_minor") is None:
                raise HTTPException(
                    status_code=422, detail="confirmed_invoice_requires_core_fields"
                )
            case = await reimbursement_service.update_case(
                session, household_id, case.id, ReimbursementCaseUpdate.model_validate({
                    "provider_name": data["provider_name"], "invoice_date": data["invoice_date"],
                    "invoice_number": data.get("invoice_number"),
                    "billed_minor": data["billed_minor"],
                    "due_date": data.get("due_date"),
                    "clear_fields": [key for key in ("invoice_number", "due_date")
                                     if data.get(key) is None],
                }),
            )
        elif data.get("document_type") == "submission_receipt" and case.decision_status in {
            "none", "processing"
        }:
            case = await reimbursement_service.update_case(
                session, household_id, case.id,
                ReimbursementCaseUpdate(submission_status="submitted", decision_status="processing")
            )
        elif data.get("document_type") == "request_for_documents":
            case = await reimbursement_service.update_case(session, household_id, case.id,
                ReimbursementCaseUpdate(submission_status="documents_requested"))
    elif payload.create_case and data.get("document_type") in {"invoice", "prescription"}:
        if not data.get("provider_name") or not data.get("invoice_date"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="confirmed_invoice_requires_provider_and_date",
            )
        billed_minor = data.get("billed_minor")
        if billed_minor is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="confirmed_invoice_requires_amount",
            )
        existing = (
            await session.execute(
                text(
                    "select id from health_expense_cases where household_id = :household_id "
                    "and insured_person_id = :person_id "
                    "and lower(provider_name) = lower(:provider) "
                    "and invoice_date = :invoice_date and billed_minor = :billed_minor "
                    "and coalesce(invoice_number, '') = coalesce(:invoice_number, '') limit 1"
                ),
                {
                    "household_id": str(household_id),
                    "person_id": str(document.insured_person_id),
                    "provider": data["provider_name"],
                    "invoice_date": data["invoice_date"],
                    "billed_minor": billed_minor,
                    "invoice_number": data.get("invoice_number"),
                },
            )
        ).first()
        if existing is not None:
            case = await reimbursement_service.get_case(
                session, household_id, UUID(str(existing.id))
            )
        else:
            case = await reimbursement_service.create_case(
                session,
                household_id,
                ReimbursementCaseCreate(
                    insured_person_id=document.insured_person_id,
                    provider_name=str(data["provider_name"]),
                    invoice_number=data.get("invoice_number"),
                    invoice_date=data["invoice_date"],
                    due_date=data.get("due_date"),
                    billed_minor=int(billed_minor),
                    currency=str(data.get("currency") or "EUR"),
                    expected_minor=int(data.get("expected_minor") or 0),
                    reimbursed_minor=0,
                    reimbursement_status="expected" if int(data.get("expected_minor") or 0) > 0
                    else "not_expected",
                ),
            )

    await session.execute(
        text(
            "update document_extractions set confirmed_data = cast(:data as jsonb), "
            "confirmed_by = :user_id, confirmed_at = now() where id = :extraction_id "
            "and household_id = :household_id"
        ),
        {
            "data": json.dumps(data, ensure_ascii=False),
            "user_id": str(user_id),
            "extraction_id": str(extraction.id),
            "household_id": str(household_id),
        },
    )
    await session.execute(
        text(
            "update reimbursement_documents set processing_status = 'confirmed', "
            "document_type = :document_type, case_id = coalesce(:case_id, case_id), "
            "updated_at = now() where id = :document_id and household_id = :household_id"
        ),
        {
            "document_type": data.get("document_type") or "unknown",
            "case_id": str(case.id) if case else None,
            "document_id": str(document_id),
            "household_id": str(household_id),
        },
    )
    confirmed = await latest_extraction(session, household_id, document_id)
    return ExtractionConfirmationResult(extraction=confirmed, case=case)
