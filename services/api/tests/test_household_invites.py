"""Stage 2e: Mitglieder-Einladung per Link/Code (Vorschau, Einloesung,
Widerruf, Owner-Restriktion, Fehlerzustaende)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _create_household_and_owner_headers(prefix: str) -> tuple[dict[str, str], str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-4")
    household = client.post(
        "/v1/households", json={"name": f"{prefix}-Haushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}, household["id"]


def test_owner_creates_invite_and_lists_it() -> None:
    owner_headers, _ = _create_household_and_owner_headers("owner")

    create_res = client.post("/v1/households/invites", json={}, headers=owner_headers)
    assert create_res.status_code == 201
    invite = create_res.json()
    assert invite["role"] == "editor"
    assert len(invite["code"]) == 8

    list_res = client.get("/v1/households/invites", headers=owner_headers)
    assert list_res.status_code == 200
    assert any(i["id"] == invite["id"] for i in list_res.json())


def test_non_owner_cannot_create_or_revoke_invite() -> None:
    owner_headers, household_id = _create_household_and_owner_headers("owner2")
    invite = client.post(
        "/v1/households/invites", json={"role": "viewer"}, headers=owner_headers
    ).json()

    # Zweiter Nutzer tritt ueber den Code als "editor" bei, versucht dann
    # selbst (als Nicht-Owner) Invites zu verwalten.
    member_token = _signup(
        f"member-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-5"
    )
    client.post(
        "/v1/households/invites/redeem",
        json={"code": invite["code"]},
        headers=_auth_headers(member_token),
    )
    member_headers = {**_auth_headers(member_token), "X-Household-Id": household_id}

    create_res = client.post("/v1/households/invites", json={}, headers=member_headers)
    assert create_res.status_code == 403

    another_invite = client.post(
        "/v1/households/invites", json={}, headers=owner_headers
    ).json()
    revoke_res = client.delete(
        f"/v1/households/invites/{another_invite['id']}", headers=member_headers
    )
    assert revoke_res.status_code == 403


def test_preview_and_redeem_invite_adds_member_with_configured_role() -> None:
    owner_headers, household_id = _create_household_and_owner_headers("owner3")
    invite = client.post(
        "/v1/households/invites", json={"role": "viewer"}, headers=owner_headers
    ).json()

    invitee_token = _signup(
        f"invitee-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-6"
    )
    invitee_headers = _auth_headers(invitee_token)

    preview_res = client.get(
        f"/v1/households/invites/redeem/{invite['code']}", headers=invitee_headers
    )
    assert preview_res.status_code == 200
    preview = preview_res.json()
    assert preview["household_id"] == household_id
    assert preview["role"] == "viewer"

    redeem_res = client.post(
        "/v1/households/invites/redeem", json={"code": invite["code"]}, headers=invitee_headers
    )
    assert redeem_res.status_code == 200
    assert redeem_res.json()["id"] == household_id

    me = client.get("/v1/me", headers=invitee_headers).json()
    assert any(
        h["household_id"] == household_id and h["role"] == "viewer" for h in me["households"]
    )

    members = client.get("/v1/households/members", headers={**owner_headers}).json()
    assert any(m["role"] == "viewer" for m in members)


def test_redeeming_already_used_code_fails() -> None:
    owner_headers, _ = _create_household_and_owner_headers("owner4")
    invite = client.post("/v1/households/invites", json={}, headers=owner_headers).json()

    first_token = _signup(
        f"first-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7"
    )
    client.post(
        "/v1/households/invites/redeem",
        json={"code": invite["code"]},
        headers=_auth_headers(first_token),
    )

    second_token = _signup(
        f"second-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-8"
    )
    second_res = client.post(
        "/v1/households/invites/redeem",
        json={"code": invite["code"]},
        headers=_auth_headers(second_token),
    )
    assert second_res.status_code == 410
    assert second_res.json()["detail"] == "invite_already_used"


def test_redeeming_unknown_code_returns_404() -> None:
    token = _signup(f"unknown-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    res = client.post(
        "/v1/households/invites/redeem", json={"code": "NOPE0000"}, headers=_auth_headers(token)
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "invite_not_found"


def test_revoked_invite_cannot_be_redeemed() -> None:
    owner_headers, _ = _create_household_and_owner_headers("owner5")
    invite = client.post("/v1/households/invites", json={}, headers=owner_headers).json()

    revoke_res = client.delete(f"/v1/households/invites/{invite['id']}", headers=owner_headers)
    assert revoke_res.status_code == 204

    token = _signup(f"revoked-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-10")
    res = client.post(
        "/v1/households/invites/redeem",
        json={"code": invite["code"]},
        headers=_auth_headers(token),
    )
    assert res.status_code == 410
    assert res.json()["detail"] == "invite_revoked"


async def _expire_invite(invite_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "update household_invites set expires_at = now() - interval '1 day' "
                "where id = :id"
            ),
            {"id": invite_id},
        )


@pytest.mark.asyncio
async def test_expired_invite_cannot_be_redeemed() -> None:
    owner_headers, _ = _create_household_and_owner_headers("owner6")
    invite = client.post("/v1/households/invites", json={}, headers=owner_headers).json()
    await _expire_invite(invite["id"])

    token = _signup(f"expired-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-11")
    res = client.post(
        "/v1/households/invites/redeem",
        json={"code": invite["code"]},
        headers=_auth_headers(token),
    )
    assert res.status_code == 410
    assert res.json()["detail"] == "invite_expired"
