"""Regressions for uploads that outrun their database commit and profile management."""

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from app.api.v1.routes import imports
from app.auth.dependencies import get_household_role, get_household_session
from app.auth.jwt import AuthenticatedUser
from app.main import app
from app.schemas.import_platform import ImportReparseInput


def pending_run() -> dict:
    return {
        "id": uuid4(),
        "account_id": None,
        "mapping_profile_id": None,
        "filename": "example.csv",
        "file_size_bytes": 42,
        "detected_format": None,
        "status": "pending",
        "total_rows": 0,
        "new_count": 0,
        "duplicate_count": 0,
        "transfer_candidate_count": 0,
        "error_count": 0,
        "error_message": None,
        "created_at": datetime.now(UTC),
        "completed_at": None,
    }


@pytest.mark.asyncio
async def test_upload_is_visible_to_an_immediately_starting_worker() -> None:
    run = pending_run()
    session = AsyncMock()
    inserted = MagicMock()
    inserted.scalar_one.return_value = run["id"]
    selected = MagicMock()
    selected.one.return_value = SimpleNamespace(_mapping=run)
    session.execute.side_effect = [inserted, selected]

    def start_worker(*args: object) -> None:
        # A worker starts when send() is called, not after the HTTP response.
        session.commit.assert_awaited_once()
        assert args[0] == str(run["id"])
        assert args[2]  # initial upload includes the file bytes

    with patch.object(imports.process_import_actor, "send", side_effect=start_worker):
        response = await imports.upload_import(
            (session, uuid4()),
            AuthenticatedUser(id=uuid4()),
            UploadFile(filename="example.csv", file=io.BytesIO(b"Datum;Betrag\n2026-09-01;1")),
            account_id=None,
            mapping_profile_id=None,
        )
    assert response.status == "pending"


@pytest.mark.asyncio
async def test_reparse_commits_corrected_mapping_before_dispatch() -> None:
    run = pending_run()
    session = AsyncMock()

    def start_worker(*args: object) -> None:
        session.commit.assert_awaited_once()
        assert args[2] == ""  # worker will read stored raw rows

    with (
        patch.object(imports, "reparse_import_run", AsyncMock(return_value=run)),
        patch.object(imports.process_import_actor, "send", side_effect=start_worker),
    ):
        response = await imports.reparse_import(
            run["id"],
            ImportReparseInput(column_mapping={"booked_date": "Date", "amount": "Sum"}),
            (session, uuid4()),
            AuthenticatedUser(id=uuid4()),
        )
    assert response.status == "pending"


@pytest.mark.asyncio
async def test_commit_failure_never_dispatches_unstored_upload() -> None:
    run = pending_run()
    session = AsyncMock()
    session.commit.side_effect = RuntimeError("database unavailable")
    with (
        patch.object(imports, "reparse_import_run", AsyncMock(return_value=run)),
        patch.object(imports.process_import_actor, "send") as send,
        pytest.raises(RuntimeError, match="database unavailable"),
    ):
        await imports.reparse_import(
            run["id"],
            ImportReparseInput(column_mapping={"booked_date": "Date", "amount": "Sum"}),
            (session, uuid4()),
            AuthenticatedUser(id=uuid4()),
        )
    send.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_broker_marks_run_failed_without_waiting_for_stale_timeout() -> None:
    run_id, household_id = uuid4(), uuid4()
    failure_session = AsyncMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=failure_session)
    session_context.__aexit__ = AsyncMock(return_value=False)
    transaction_context = MagicMock()
    transaction_context.__aenter__ = AsyncMock()
    transaction_context.__aexit__ = AsyncMock(return_value=False)
    failure_session.begin = MagicMock(return_value=transaction_context)
    factory = MagicMock(return_value=session_context)
    with (
        patch.object(imports.process_import_actor, "send", side_effect=ConnectionError("offline")),
        patch.object(imports, "get_session_factory", return_value=factory),
        pytest.raises(HTTPException) as error,
    ):
        await imports._dispatch_import(run_id, household_id, "example.csv", "")
    assert error.value.status_code == 503
    statement, values = failure_session.execute.await_args.args
    assert "status = 'failed'" in str(statement)
    assert values["id"] == str(run_id)
    assert values["household_id"] == str(household_id)
    assert "erneut" in values["message"]


@pytest.fixture()
def profile_client():
    session, household_id = AsyncMock(), uuid4()
    old_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_household_session] = lambda: (session, household_id)
    app.dependency_overrides[get_household_role] = lambda: "editor"
    try:
        with TestClient(app) as client:
            yield client, session, household_id
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_saved_mapping_can_be_deleted(profile_client) -> None:
    client, session, household_id = profile_client
    profile_id = uuid4()
    result = MagicMock()
    result.scalar_one_or_none.return_value = profile_id
    session.execute.return_value = result
    response = client.delete(f"/v1/imports/mapping-profiles/{profile_id}")
    assert response.status_code == 204
    assert response.content == b""
    assert session.execute.await_args.args[1]["household_id"] == str(household_id)


def test_unknown_or_other_household_mapping_returns_404(profile_client) -> None:
    client, session, _ = profile_client
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    response = client.delete(f"/v1/imports/mapping-profiles/{uuid4()}")
    assert response.status_code == 404


def test_readonly_household_cannot_delete_or_replace_mapping(profile_client) -> None:
    client, session, _ = profile_client
    app.dependency_overrides[get_household_role] = lambda: "viewer"
    path = f"/v1/imports/mapping-profiles/{uuid4()}"
    assert client.delete(path).status_code == 403
    assert (
        client.patch(
            path,
            json={
                "name": "Corrected",
                "bank_format": "csv_generic",
                "column_mapping": {},
            },
        ).status_code
        == 403
    )
    session.execute.assert_not_awaited()


def test_saved_mapping_can_be_replaced_without_creating_another_profile(profile_client) -> None:
    client, session, household_id = profile_client
    profile_id = uuid4()
    mapping = {
        "booked_date": "Buchungstag",
        "amount": "Betrag",
        "payee": "Name Zahlungsbeteiligter",
    }
    result = MagicMock()
    result.first.return_value = SimpleNamespace(
        _mapping={
            "id": profile_id,
            "household_id": household_id,
            "name": "MLP korrigiert",
            "bank_format": "csv_generic",
            "column_mapping": mapping,
            "created_at": datetime.now(UTC),
        }
    )
    session.execute.return_value = result
    response = client.patch(
        f"/v1/imports/mapping-profiles/{profile_id}",
        json={
            "name": "MLP korrigiert",
            "bank_format": "csv_generic",
            "column_mapping": mapping,
        },
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(profile_id)
    assert response.json()["column_mapping"] == mapping
    assert session.execute.await_args.args[1]["household_id"] == str(household_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True])
async def test_large_preview_reuses_rules_and_categories(archived: bool) -> None:
    run_id, account_id, category_id = uuid4(), uuid4(), uuid4()
    session = AsyncMock()
    run_result, rows_result, rules_result = MagicMock(), MagicMock(), MagicMock()
    run_result.first.return_value = SimpleNamespace(account_id=account_id)
    data = {
        "id": uuid4(),
        "import_run_id": run_id,
        "row_number": 1,
        "raw_data": {},
        "external_reference": None,
        "booked_date": "2026-09-04",
        "value_date": None,
        "amount_minor": -7500,
        "currency": "EUR",
        "payee_raw": "Muster Markt",
        "purpose": "Kartenzahlung",
        "bank_transaction_code": None,
        "fingerprint": None,
        "status": "new",
        "duplicate_of_transaction_id": None,
        "committed_transaction_id": None,
    }
    rows_result.all.return_value = [
        SimpleNamespace(status="new", _mapping={**data, "id": uuid4(), "row_number": i + 1})
        for i in range(200)
    ]
    rules_result.all.return_value = [
        SimpleNamespace(
            id=uuid4(),
            name="Markt",
            match_payee_raw="Muster",
            action_category_id=category_id,
            action_payee_id=None,
            action_set_transfer=False,
            action_note=None,
        )
    ]
    session.execute.side_effect = [run_result, rows_result, rules_result]
    with patch.object(
        imports.category_service,
        "load_category",
        AsyncMock(
            return_value={
                "name": "Lebensmittel",
                "is_archived": archived,
            }
        ),
    ) as load_category:
        response = await imports.list_import_rows(run_id, (session, uuid4()))
    assert len(response) == 200
    # Query count must not grow with the number of imported rows.
    assert session.execute.await_count == 3
    load_category.assert_awaited_once()
    assert all(row.category_source == ("none" if archived else "rule") for row in response)
    assert all(row.suggested_category_id == (None if archived else category_id) for row in response)
