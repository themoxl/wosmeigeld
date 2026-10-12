"""PATCH /v1/households (Haushalt umbenennen) - Nutzer hat sich beim Anlegen
vertippt und braucht eine Korrekturmoeglichkeit."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def test_owner_can_rename_household() -> None:
    token = _signup(f"rename-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-1")
    household = client.post(
        "/v1/households", json={"name": "Zuhaus (Tippfehler)"}, headers=_auth_headers(token)
    ).json()

    response = client.patch(
        "/v1/households",
        json={"name": "Zuhause"},
        headers={**_auth_headers(token), "X-Household-Id": household["id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == household["id"]
    assert body["name"] == "Zuhause"

    listed = client.get("/v1/households", headers=_auth_headers(token)).json()
    assert next(h for h in listed if h["id"] == household["id"])["name"] == "Zuhause"
