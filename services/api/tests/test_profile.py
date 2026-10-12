"""GET/PATCH /v1/me: Profil laedt mit date_format-Default und laesst es sich aendern."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _signup_headers(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-5")
    return _auth_headers(token)


def test_get_me_defaults_date_format_to_de() -> None:
    headers = _signup_headers("profile-default")

    res = client.get("/v1/me", headers=headers)
    assert res.status_code == 200
    assert res.json()["date_format"] == "de"


def test_patch_me_updates_date_format() -> None:
    headers = _signup_headers("profile-update")

    patch_res = client.patch("/v1/me", json={"date_format": "iso"}, headers=headers)
    assert patch_res.status_code == 200
    assert patch_res.json()["date_format"] == "iso"

    get_res = client.get("/v1/me", headers=headers)
    assert get_res.json()["date_format"] == "iso"


def test_patch_me_rejects_invalid_date_format() -> None:
    headers = _signup_headers("profile-invalid")

    res = client.patch("/v1/me", json={"date_format": "not-a-format"}, headers=headers)
    assert res.status_code == 422
