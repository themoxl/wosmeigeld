"""Kritische Abnahmefaelle fuer Gesundheit & Erstattung."""

import io
import json
import os
import uuid
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

from app.core.config import Settings
from app.main import app
from app.schemas.ai_task import AITaskCreate
from app.services import reimbursement_service
from app.services.health_document_security import validate_health_document
from app.services.health_document_storage import HealthDocumentStorage
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)
_DSN = os.environ.get(
    "TEST_DIRECT_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:55322/postgres",
)


def _pdf_bytes(*, encrypted: bool = False) -> bytes:
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    if encrypted:
        writer.encrypt("privat")
    writer.write(stream)
    return stream.getvalue()


def _annotated_pdf(subtype: str, action: DictionaryObject | None = None) -> bytes:
    annotation = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject(subtype),
            NameObject("/Rect"): ArrayObject([NumberObject(value) for value in (0, 0, 72, 24)]),
        }
    )
    if action is not None:
        annotation[NameObject("/A")] = action
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_annotation(page_number=0, annotation=annotation)
    writer.write(stream)
    return stream.getvalue()


def _action(action_type: str) -> DictionaryObject:
    return DictionaryObject({NameObject("/S"): NameObject(action_type)})


@pytest.fixture()
def reimbursement_headers() -> dict[str, str]:
    token = _signup(
        f"reimbursements-{uuid.uuid4().hex}@finanzplaner.test",
        "correct-horse-battery-2",
    )
    household = client.post(
        "/v1/households",
        json={"name": "Erstattungs-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _person(headers: dict[str, str]) -> dict[str, object]:
    response = client.post(
        "/v1/reimbursements/people",
        json={"display_name": "Testperson"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_manual_case_keeps_independent_statuses_and_summary(
    reimbursement_headers: dict[str, str],
) -> None:
    person = _person(reimbursement_headers)
    response = client.post(
        "/v1/reimbursements/cases",
        json={
            "insured_person_id": person["id"],
            "provider_name": "Praxis Beispiel",
            "invoice_number": "RE-2026-17",
            "invoice_date": "2026-08-01",
            "billed_minor": 25000,
            "expected_minor": 20000,
            "payment_status": "paid",
            "submission_status": "submitted",
            "decision_status": "processing",
            "reimbursement_status": "expected",
        },
        headers=reimbursement_headers,
    )
    assert response.status_code == 201, response.text
    case = response.json()
    assert case["overall_status"] == "waiting"
    assert case["payment_status"] == "paid"
    assert case["submission_status"] == "submitted"

    summary = client.get(
        "/v1/reimbursements/summary?year=2026", headers=reimbursement_headers
    )
    assert summary.status_code == 200
    assert summary.json()["processing_count"] == 1
    assert summary.json()["expected_minor"] == 20000


def test_private_pdf_upload_is_deduplicated_and_only_queues_document_id(
    reimbursement_headers: dict[str, str],
) -> None:
    person = _person(reimbursement_headers)
    content = _pdf_bytes()
    storage_put = AsyncMock()
    storage_delete = AsyncMock()
    with (
        patch.object(HealthDocumentStorage, "put", storage_put),
        patch.object(HealthDocumentStorage, "delete", storage_delete),
        patch("app.api.v1.routes.reimbursements.drain_queue_actor.send") as send,
    ):
        first = client.post(
            "/v1/reimbursements/documents",
            data={"person_id": person["id"]},
            files={"file": ("Rechnung.pdf", content, "text/plain")},
            headers=reimbursement_headers,
        )
        second = client.post(
            "/v1/reimbursements/documents",
            data={"person_id": person["id"]},
            files={"file": ("Kopie.pdf", content, "application/pdf")},
            headers=reimbursement_headers,
        )

    assert first.status_code == 201, first.text
    assert first.json()["document"]["mime_type"] == "application/pdf"
    assert first.json()["duplicate"] is False
    assert first.json()["task_id"] is not None
    assert second.status_code == 201
    assert second.json()["duplicate"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    storage_put.assert_awaited_once()
    storage_delete.assert_not_awaited()
    send.assert_called_once()

    task = client.get(
        f"/v1/ai/tasks/{first.json()['task_id']}", headers=reimbursement_headers
    ).json()
    assert task["task_type"] == "health_document_extraction"
    assert task["input"] == {"document_id": first.json()["document"]["id"]}
    assert "pdf" not in str(task["input"]).lower()

    document_id = first.json()["document"]["id"]
    with psycopg.connect(_DSN) as connection:
        connection.execute(
            "update reimbursement_documents set processing_status = 'needs_review' "
            "where id = %(document_id)s",
            {"document_id": document_id},
        )
        connection.execute(
            "insert into document_extractions "
            "(household_id, insured_person_id, document_id, extractor_version, schema_version, "
            "status, confidence, structured_data) values "
            "(%(household_id)s, %(person_id)s, %(document_id)s, 'test', 'test', "
            "'needs_review', 0.91, %(data)s::jsonb)",
            {
                "household_id": reimbursement_headers["X-Household-Id"],
                "person_id": person["id"],
                "document_id": document_id,
                "data": json.dumps(
                    {
                        "document_type": "invoice",
                        "provider_name": "Praxis Beispiel",
                        "invoice_number": "RE-42",
                        "invoice_date": "2026-08-02",
                        "billed_minor": 12345,
                        "expected_minor": 9000,
                        "currency": "EUR",
                    }
                ),
            },
        )
        connection.commit()

    confirmed = client.post(
        f"/v1/reimbursements/documents/{document_id}/confirm-extraction",
        json={
            "create_case": True,
            "structured_data": {
                "document_type": "invoice",
                "provider_name": "Praxis Beispiel korrigiert",
                "invoice_number": "RE-42",
                "invoice_date": "2026-08-02",
                "billed_minor": 12345,
                "expected_minor": 9000,
                "currency": "EUR",
            },
        },
        headers=reimbursement_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["case"]["provider_name"] == "Praxis Beispiel korrigiert"
    assert confirmed.json()["case"]["overall_status"] == "action_required"
    assert confirmed.json()["extraction"]["confirmed_data"]["billed_minor"] == 12345


def test_document_security_rejects_encryption_and_spoofed_content() -> None:
    settings = Settings(health_document_max_bytes=2 * 1024 * 1024)
    with pytest.raises(HTTPException) as encrypted:
        validate_health_document(_pdf_bytes(encrypted=True), "privat.pdf", settings)
    assert encrypted.value.detail == "health_document_encrypted"

    with pytest.raises(HTTPException) as spoofed:
        validate_health_document(b"not a pdf", "rechnung.pdf", settings)
    assert spoofed.value.detail == "health_document_unsupported_type"

    task = AITaskCreate(
        task_type="health_document_extraction",
        input={"document_id": str(uuid.uuid4())},
    )
    assert set(task.input) == {"document_id"}


def test_reference_keys_cover_the_transposed_insurer_notation() -> None:
    """Die Abrechnungsnummer steht im Verwendungszweck mit vertauschtem Jahr/Monat.

    Abrechnung "25 00 00001" erscheint dort als "0025 00001". Beide
    Schreibweisen muessen als Treffer zaehlen, sonst findet der Bankabgleich
    genau die Buchungen nicht, die eindeutig zuzuordnen waeren.
    """
    keys = reimbursement_service._reference_keys("250000001")
    assert "250000001" in keys
    assert "002500001" in keys

    # Kurze Nummern taugen nicht: fuenf Ziffern finden sich zufaellig in fast
    # jedem Verwendungszweck.
    assert reimbursement_service._reference_keys("12345") == set()
    assert reimbursement_service._reference_keys(None) == set()


def test_case_candidates_rank_by_amount_number_and_person(
    reimbursement_headers: dict[str, str],
) -> None:
    person = _person(reimbursement_headers)
    treffer = client.post(
        "/v1/reimbursements/cases",
        json={
            "insured_person_id": person["id"],
            "provider_name": "Muster-Apotheke",
            "invoice_number": "250000002",
            "invoice_date": "2025-11-26",
            "billed_minor": 7500,
        },
        headers=reimbursement_headers,
    )
    assert treffer.status_code == 201, treffer.text
    daneben = client.post(
        "/v1/reimbursements/cases",
        json={
            "insured_person_id": person["id"],
            "provider_name": "Praxis Irgendwo",
            "invoice_date": "2025-11-26",
            "billed_minor": 999_00,
        },
        headers=reimbursement_headers,
    )
    assert daneben.status_code == 201, daneben.text

    with (
        patch.object(HealthDocumentStorage, "put", AsyncMock()),
        patch("app.api.v1.routes.reimbursements.drain_queue_actor.send"),
    ):
        upload = client.post(
            "/v1/reimbursements/documents",
            data={"person_id": person["id"]},
            files={"file": ("Abrechnung.pdf", _pdf_bytes(), "application/pdf")},
            headers=reimbursement_headers,
        )
    document_id = upload.json()["document"]["id"]
    with psycopg.connect(_DSN) as connection:
        connection.execute(
            "update reimbursement_documents set processing_status = 'needs_review', "
            "document_type = 'benefit_statement' where id = %(document_id)s",
            {"document_id": document_id},
        )
        connection.execute(
            "insert into document_extractions "
            "(household_id, insured_person_id, document_id, extractor_version, schema_version, "
            "status, confidence, structured_data) select household_id, insured_person_id, id, "
            "'test', 'test', 'needs_review', 0.9, %(data)s::jsonb from reimbursement_documents "
            "where id = %(document_id)s",
            {
                "document_id": document_id,
                "data": json.dumps(
                    {
                        "document_type": "benefit_statement",
                        "person_name": "Testperson",
                        "provider_name": None,
                        "invoice_number": "250000002",
                        "invoice_date": "2025-12-04",
                        "due_date": None,
                        "billed_minor": 7500,
                        "expected_minor": 7500,
                        "reimbursed_minor": 7500,
                        "currency": "EUR",
                        "external_reference": None,
                    }
                ),
            },
        )
        connection.commit()

    candidates = client.get(
        f"/v1/reimbursements/documents/{document_id}/case-candidates",
        headers=reimbursement_headers,
    )
    assert candidates.status_code == 200, candidates.text
    ranked = candidates.json()
    assert ranked, "Der passende Vorgang muss vorgeschlagen werden"
    assert ranked[0]["provider_name"] == "Muster-Apotheke"
    assert "Belegnummer stimmt überein" in ranked[0]["evidence"]
    assert "Betrag stimmt überein" in ranked[0]["evidence"]
    # Der unpassende Vorgang darf gar nicht erst auftauchen.
    assert all(item["provider_name"] != "Praxis Irgendwo" for item in ranked)

    linked = client.post(
        f"/v1/reimbursements/documents/{document_id}/case",
        json={"case_id": ranked[0]["case_id"], "apply_decision": True},
        headers=reimbursement_headers,
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["document"]["case_id"] == ranked[0]["case_id"]
    # Die Abrechnung ist die Entscheidung des Versicherers: sie traegt sie ein.
    assert linked.json()["case"]["reimbursed_minor"] == 7500
    assert linked.json()["case"]["decision_status"] == "received"


def test_bulk_reprocess_queues_resting_documents_once(
    reimbursement_headers: dict[str, str],
) -> None:
    person = _person(reimbursement_headers)
    with (
        patch.object(HealthDocumentStorage, "put", AsyncMock()),
        patch("app.api.v1.routes.reimbursements.drain_queue_actor.send"),
    ):
        upload = client.post(
            "/v1/reimbursements/documents",
            data={"person_id": person["id"]},
            files={"file": ("Rechnung.pdf", _pdf_bytes(), "application/pdf")},
            headers=reimbursement_headers,
        )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document"]["id"]

    # Frisch hochgeladen steht das Dokument auf 'queued' und darf dem Worker
    # nicht unter den Fingern weggezogen werden.
    with patch("app.api.v1.routes.reimbursements.drain_queue_actor.send"):
        untouched = client.post(
            "/v1/reimbursements/documents/reprocess-all", headers=reimbursement_headers
        )
    assert untouched.status_code == 200, untouched.text
    assert untouched.json() == {"queued": 0, "task_id": None}

    with psycopg.connect(_DSN) as connection:
        connection.execute(
            "update reimbursement_documents set processing_status = 'needs_review' "
            "where id = %(document_id)s",
            {"document_id": document_id},
        )
        connection.commit()

    with patch("app.api.v1.routes.reimbursements.drain_queue_actor.send") as send:
        bulk = client.post(
            "/v1/reimbursements/documents/reprocess-all", headers=reimbursement_headers
        )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["queued"] == 1
    assert bulk.json()["task_id"] is not None
    send.assert_called_once()

    document = client.get(
        f"/v1/reimbursements/documents/{document_id}", headers=reimbursement_headers
    ).json()
    assert document["processing_status"] == "queued"
    assert document["error_message"] is None


def test_document_security_allows_links_but_blocks_executable_annotations() -> None:
    """Ein Fussnoten-Hyperlink ist kein aktiver Inhalt.

    Versicherungs-PDFs (z. B. Leistungsabrechnungen) tragen praktisch immer
    /Link-Annotationen. Wuerde deren blosses Vorhandensein zur Ablehnung
    fuehren, waere der Upload fuer echte Dokumente unbenutzbar.
    """
    settings = Settings(health_document_max_bytes=2 * 1024 * 1024)

    harmless = validate_health_document(
        _annotated_pdf("/Link", _action("/URI")), "leistungsabrechnung.pdf", settings
    )
    assert harmless.mime_type == "application/pdf"
    assert harmless.page_count == 1

    for subtype, action in (
        ("/Widget", None),
        ("/FileAttachment", None),
        ("/Link", _action("/JavaScript")),
        ("/Link", _action("/Launch")),
        ("/Link", _action("/SubmitForm")),
    ):
        with pytest.raises(HTTPException) as rejected:
            validate_health_document(_annotated_pdf(subtype, action), "aktiv.pdf", settings)
        assert rejected.value.detail == "health_document_active_content", (subtype, action)
