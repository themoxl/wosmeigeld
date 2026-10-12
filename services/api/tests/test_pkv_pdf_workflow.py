"""PKV-PDF -> bestaetigter Bescheid -> echte Bankzuordnung mit PostgreSQL/RLS.

Laeuft auch in scripts/pkv_test_database.py-Datenbanken. Authentifizierung
liefert dort nur Testidentitaeten; Haushalts- und Gesundheitsrechte sowie
alle Transaktionen werden durch die echte API und PostgreSQL geprueft.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import psycopg
import pymupdf as fitz
import pytest
from fastapi import Header
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.jwt import AuthenticatedUser
from app.main import app
from app.services.health_document_storage import HealthDocumentStorage

DSN = os.environ.get("TEST_DIRECT_DATABASE_URL",
                     "postgresql://postgres:postgres@127.0.0.1:55322/postgres")


@pytest.fixture()
def env() -> Iterator[dict[str, Any]]:
    user_id = uuid4()
    with psycopg.connect(DSN) as connection:
        connection.execute("insert into auth.users (id, email) values (%s, %s)",
                           (user_id, f"pkv-{user_id}@example.test"))

    def identity(authorization: Annotated[str | None, Header()] = None) -> AuthenticatedUser:
        return AuthenticatedUser(id=UUID(authorization.removeprefix("Bearer "))
                                 if authorization else user_id)

    app.dependency_overrides[get_current_user] = identity
    storage: dict[str, bytes] = {}

    async def put(key: str, content: bytes, mime: str) -> None:
        storage[key] = content

    async def get(key: str) -> bytes:
        return storage[key]

    with (TestClient(app) as client,
          patch.object(HealthDocumentStorage, "put", AsyncMock(side_effect=put)),
          patch.object(HealthDocumentStorage, "get", AsyncMock(side_effect=get)),
          patch.object(HealthDocumentStorage, "delete", AsyncMock()),
          patch("app.api.v1.routes.reimbursements.drain_queue_actor.send")):
        headers = {"Authorization": f"Bearer {user_id}"}
        household = client.post("/v1/households", headers=headers,
                                json={"name": "Isolierter PKV-Test"})
        assert household.status_code == 201, household.text
        headers["X-Household-Id"] = household.json()["id"]
        person = client.post("/v1/reimbursements/people", headers=headers,
                             json={"display_name": "Anna Muster"})
        assert person.status_code == 201, person.text
        account = client.post("/v1/accounts", headers=headers,
                              json={"name": "Testkonto", "account_type": "checking"})
        assert account.status_code == 201, account.text
        yield {"client": client, "headers": headers, "household": household.json()["id"],
               "person": person.json()["id"], "account": account.json()["id"], "storage": storage,
               "user": user_id}
    app.dependency_overrides.pop(get_current_user, None)


def invoice(env: dict[str, Any], number: str = "R-100", amount: int = 40000) -> dict[str, Any]:
    result = env["client"].post("/v1/reimbursements/cases", headers=env["headers"], json={
        "insured_person_id": env["person"], "provider_name": "Praxis Beispiel",
        "invoice_number": number, "invoice_date": "2026-10-01", "billed_minor": amount,
        "payment_status": "paid",
    })
    assert result.status_code == 201, result.text
    return result.json()


def statement(env: dict[str, Any], *, approved: int | None = 25000,
              payer: str = "Muster PKV", reference: str = "B-100",
              items: list[dict[str, Any]] | None = None, confirm: bool = True) -> dict[str, Any]:
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), f"Anonymisierte Leistungsabrechnung {uuid4()}")
        content = pdf.tobytes()
    result = env["client"].post("/v1/reimbursements/documents", headers=env["headers"],
        data={"person_id": env["person"]},
        files={"file": ("Bescheid.pdf", content, "application/pdf")})
    assert result.status_code == 201, result.text
    document = result.json()["document"]
    data = {"document_type": "benefit_statement", "person_name": "Anna Muster",
            "invoice_number": "R-100", "invoice_date": "2026-10-01", "billed_minor": 40000,
            "approved_minor": approved, "expected_minor": approved, "reimbursed_minor": approved,
            "statement_number": reference, "payer_name": payer,
            "payer_type": "beihilfe" if payer == "Beihilfe" else "pkv", "currency": "EUR",
            "items": items or []}
    with psycopg.connect(DSN) as connection:
        connection.execute("update reimbursement_documents set processing_status = 'needs_review', "
                           "document_type = 'benefit_statement' where id = %s", (document["id"],))
        connection.execute("insert into document_extractions "
            "(household_id, insured_person_id, document_id, extractor_version, schema_version, "
            "status, structured_data) values "
            "(%s, %s, %s, 'test', '2.0', 'needs_review', %s::jsonb)",
            (env["household"], env["person"], document["id"], json.dumps(data)))
    if confirm:
        result = env["client"].post(
            f"/v1/reimbursements/documents/{document['id']}/confirm-extraction",
            headers=env["headers"], json={"structured_data": data, "create_case": False})
        assert result.status_code == 200, result.text
    return document


def link(env: dict[str, Any], document: dict[str, Any], case: dict[str, Any],
         **options: Any) -> Any:
    return env["client"].post(f"/v1/reimbursements/documents/{document['id']}/case",
        headers=env["headers"], json={"case_id": case["id"], **options})


def get_case(env: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    result = env["client"].get(f"/v1/reimbursements/cases/{case['id']}", headers=env["headers"])
    assert result.status_code == 200, result.text
    return result.json()


def allocate(env: dict[str, Any], case: dict[str, Any], amount: int) -> dict[str, Any]:
    transaction = env["client"].post("/v1/transactions", headers=env["headers"], json={
        "account_id": env["account"], "booked_date": "2026-10-11", "amount_minor": amount,
        "payee_label": "Muster PKV", "note": "Erstattung B-100",
    })
    assert transaction.status_code == 201, transaction.text
    result = env["client"].post(f"/v1/reimbursements/cases/{case['id']}/allocations",
        headers=env["headers"], json={"transaction_id": transaction.json()["id"],
                                    "allocation_type": "reimbursement", "amount_minor": amount})
    assert result.status_code == 201, result.text
    return result.json()


def test_pdf_does_not_create_cash_and_two_payments_close_the_case(env: dict[str, Any]) -> None:
    case = invoice(env)
    doc = statement(env)
    result = link(env, doc, case)
    assert result.status_code == 200, result.text
    current = result.json()["case"]
    assert current["expected_minor"] == current["outstanding_minor"] == 25000
    assert current["reimbursed_minor"] == 0 and current["overall_status"] == "waiting"
    allocate(env, case, 12500)
    assert get_case(env, case)["outstanding_minor"] == 12500
    allocate(env, case, 12500)
    current = get_case(env, case)
    assert current["reimbursed_minor"] == 25000
    assert current["overall_status"] == "reconciled"


def test_unknown_amount_and_unconfirmed_decision_do_not_change_case(env: dict[str, Any]) -> None:
    case = invoice(env)
    result = link(env, statement(env, approved=None), case)
    assert result.status_code == 422
    assert result.json()["detail"] == "statement_approved_amount_missing"
    result = link(env, statement(env, confirm=False), case)
    assert result.status_code == 422
    assert result.json()["detail"] == "statement_confirmation_required"
    assert get_case(env, case)["decision_status"] == "none"


def test_corrected_bescheid_updates_instead_of_adding_and_preserves_bank_cash(
    env: dict[str, Any],
) -> None:
    case = invoice(env)
    first = statement(env)
    assert link(env, first, case).status_code == 200
    assert link(env, first, case).json()["case"]["expected_minor"] == 25000
    allocate(env, case, 25000)
    corrected = statement(env, approved=20000)
    result = link(env, corrected, case)
    assert result.status_code == 200, result.text
    assert result.json()["case"]["expected_minor"] == 20000
    assert result.json()["case"]["reimbursed_minor"] == 25000
    assert result.json()["case"]["reimbursement_status"] == "mismatch"
    with psycopg.connect(DSN) as connection:
        row = connection.execute("select count(*), max(jsonb_array_length(decision_history)) "
                                 "from reimbursement_submissions where case_id = %s",
                                 (case["id"],)).fetchone()
    assert row[0] == 1 and row[1] >= 1


def test_pkv_and_beihilfe_decisions_are_aggregated_without_marking_received(
    env: dict[str, Any],
) -> None:
    case = invoice(env)
    assert link(env, statement(env, approved=25000), case).status_code == 200
    result = link(env, statement(env, approved=15000, payer="Beihilfe", reference="BH-1"), case)
    assert result.status_code == 200, result.text
    assert result.json()["case"]["expected_minor"] == 40000
    assert result.json()["case"]["reimbursed_minor"] == 0


def test_sammelabrechnung_links_two_invoices_and_never_uses_total_for_unknown_item(
    env: dict[str, Any],
) -> None:
    first, second = invoice(env, "R-100", 20000), invoice(env, "R-101", 20000)
    items = [{"person_name": "Anna Muster", "invoice_number": number, "billed_minor": 20000,
              "approved_minor": 12500} for number in ("R-100", "R-101")]
    doc = statement(env, items=items)
    for index, case in enumerate((first, second)):
        result = link(env, doc, case, item_index=index)
        assert result.status_code == 200, result.text
        assert result.json()["case"]["expected_minor"] == 12500
    assert len(result.json()["document"]["linked_case_ids"]) == 2
    unknown = statement(env, items=[{**items[0], "approved_minor": None}, items[1]])
    result = link(env, unknown, first, item_index=0)
    assert result.status_code == 422
    assert result.json()["detail"] == "statement_approved_amount_missing"


def test_preview_is_read_only_and_stale_version_is_rejected(env: dict[str, Any]) -> None:
    case = invoice(env)
    doc = statement(env)
    preview = env["client"].post(f"/v1/reimbursements/documents/{doc['id']}/case-preview",
        headers=env["headers"], json={"case_id": case["id"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["expected_after_minor"] == 25000
    assert get_case(env, case)["expected_minor"] == 0
    result = link(env, doc, case, expected_case_version=99)
    assert result.status_code == 409
    assert get_case(env, case)["expected_minor"] == 0


def test_document_page_requires_household_and_person_access(env: dict[str, Any]) -> None:
    doc = statement(env)
    response = env["client"].get(f"/v1/reimbursements/documents/{doc['id']}/pages/1",
                                 headers=env["headers"])
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
    stranger = uuid4()
    with psycopg.connect(DSN) as connection:
        connection.execute("insert into auth.users (id) values (%s)", (stranger,))
        connection.execute("insert into household_members (household_id, user_id, role, status) "
                           "values (%s, %s, 'editor', 'active')", (env["household"], stranger))
    headers = {**env["headers"], "Authorization": f"Bearer {stranger}"}
    response = env["client"].get(
        f"/v1/reimbursements/documents/{doc['id']}/pages/1", headers=headers
    )
    assert response.status_code == 404


def test_legacy_document_cash_is_preserved_but_requires_bank_verification(
    env: dict[str, Any],
) -> None:
    case = invoice(env)
    doc = statement(env)
    with psycopg.connect(DSN) as connection:
        connection.execute("update reimbursement_documents set case_id = %s where id = %s",
                           (case["id"], doc["id"]))
        connection.execute("update health_expense_cases set expected_minor = 25000, "
                           "reimbursed_minor = 25000 where id = %s", (case["id"],))
        schema = (Path(__file__).resolve().parents[3]
                  / "supabase/migrations/0001_schema.sql").read_text("utf-8")
        block = schema.split("-- schema_sync: vorstufe PKV-Altwerte", 1)[1]
        block = block.split("\n", 1)[1].split("-- schema_sync: ende", 1)[0]
        connection.execute(block)
        stored = connection.execute(
            "select reimbursed_minor from health_expense_cases where id = %s",
            (case["id"],)).fetchone()[0]
    current = get_case(env, case)
    assert stored == 25000
    assert current["reimbursed_minor"] == 0
    assert current["unverified_reimbursed_minor"] == 25000
    assert current["outstanding_minor"] == 25000


def test_unlinking_decision_keeps_bank_assignments(env: dict[str, Any]) -> None:
    case = invoice(env)
    doc = statement(env)
    assert link(env, doc, case).status_code == 200
    allocate(env, case, 12500)
    response = env["client"].delete(f"/v1/reimbursements/documents/{doc['id']}/case",
                                   headers=env["headers"])
    assert response.status_code == 200, response.text
    assert get_case(env, case)["reimbursed_minor"] == 12500
    allocations = env["client"].get(f"/v1/reimbursements/cases/{case['id']}/allocations",
                                   headers=env["headers"])
    assert len(allocations.json()) == 1


def test_zero_is_a_decision_and_wrong_invoice_preview_is_rejected(env: dict[str, Any]) -> None:
    case = invoice(env)
    result = link(env, statement(env, approved=0), case)
    assert result.status_code == 200, result.text
    assert result.json()["case"]["decision_status"] == "received"
    assert result.json()["case"]["expected_minor"] == 0
    other = invoice(env, number="R-200")
    doc = statement(env)
    response = env["client"].post(f"/v1/reimbursements/documents/{doc['id']}/case-preview",
        headers=env["headers"], json={"case_id": other["id"]})
    assert response.status_code == 422
    assert response.json()["detail"] == "statement_case_number_mismatch"
    assert get_case(env, other)["decision_status"] == "none"


def test_linked_item_source_survives_correction_and_cannot_be_removed(env: dict[str, Any]) -> None:
    case = invoice(env, "R-101", 20000)
    items = [{"person_name": "Anna Muster", "invoice_number": number, "billed_minor": 20000,
              "approved_minor": 12500} for number in ("R-100", "R-101")]
    doc = statement(env, items=items)
    assert link(env, doc, case, item_index=1).status_code == 200
    path = f"/v1/reimbursements/documents/{doc['id']}"
    data = env["client"].get(f"{path}/extraction", headers=env["headers"]).json()["confirmed_data"]
    data["items"] = [data["items"][1]]
    data.update(billed_minor=20000, approved_minor=10000,
                expected_minor=10000, reimbursed_minor=10000)
    data["items"][0]["approved_minor"] = 10000
    result = env["client"].post(f"{path}/confirm-extraction", headers=env["headers"],
        json={"structured_data": data, "create_case": False})
    assert result.status_code == 200, result.text
    assert link(env, doc, case, item_index=0).json()["case"]["expected_minor"] == 10000
    document = env["client"].get(path, headers=env["headers"]).json()
    assert document["linked_items"][0]["item_index"] == 1
    data["items"] = []
    result = env["client"].post(f"{path}/confirm-extraction", headers=env["headers"],
        json={"structured_data": data, "create_case": False})
    assert result.status_code == 409
    assert result.json()["detail"] == "statement_linked_item_removed"


def test_foreign_currency_decision_is_rejected_without_cash(env: dict[str, Any]) -> None:
    case = invoice(env)
    doc = statement(env)
    with psycopg.connect(DSN) as connection:
        connection.execute("update health_expense_cases set currency = 'USD' where id = %s",
                           (case["id"],))
    result = link(env, doc, case)
    assert result.status_code == 422
    assert result.json()["detail"] == "statement_case_currency_mismatch"
    assert get_case(env, case)["reimbursed_minor"] == 0
