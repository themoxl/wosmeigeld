"""Kritischer Abnahmetest fuer Phase 1 (Architekturplan Abschnitt 17):
"zwei Testhaushalte koennen gegenseitig keinerlei Daten lesen".

Dazu die Rollen-Gates und die Fehlerabbildung (Abschnitt 5, Punkt 5:
"API-Endpunkte pruefen Rolle und Operation zusaetzlich"): ein `viewer` bekommt
auf schreibenden Endpunkten ein 403 - weder einen 500 aus einer
durchgereichten RLS-Ablehnung noch, schlimmer, ein stilles 204, das das
Frontend als erfolgreiche Loeschung rendert.

Laeuft gegen ein echtes lokales Supabase (`supabase start` im Ordner
supabase/) mit real signup-ten Nutzern - keine Mocks. Benoetigte
Umgebungsvariablen werden zentral in tests/conftest.py gesetzt (muss vor dem
Import von app.main geladen sein, siehe dortige Begruendung).
"""

import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.requests import Request

from app.auth.dependencies import get_household_session, require_household_editor
from app.core.db import get_session_factory
from app.core.errors import database_permission_error_handler, extract_sqlstate
from app.main import app

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9."
    "CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"
)


def _signup(email: str, password: str) -> str:
    response = httpx.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _user_id_from_token(token: str) -> str:
    payload = pyjwt.decode(token, options={"verify_signature": False})
    return str(payload["sub"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def user_a_token() -> str:
    return _signup(f"a-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-staple-1")


@pytest.fixture(scope="module")
def user_b_token() -> str:
    return _signup(f"b-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-staple-2")


def test_two_households_cannot_read_each_others_data(
    client: TestClient, user_a_token: str, user_b_token: str
) -> None:
    household_a = client.post(
        "/v1/households", json={"name": "Haushalt A"}, headers=_auth_headers(user_a_token)
    ).json()
    household_b = client.post(
        "/v1/households", json={"name": "Haushalt B"}, headers=_auth_headers(user_b_token)
    ).json()
    assert household_a["id"] != household_b["id"]

    account_a = client.post(
        "/v1/accounts",
        json={"name": "Girokonto A", "account_type": "checking"},
        headers={**_auth_headers(user_a_token), "X-Household-Id": household_a["id"]},
    ).json()
    tx_response = client.post(
        "/v1/transactions",
        json={"account_id": account_a["id"], "booked_date": "2026-07-01", "amount_minor": -1234},
        headers={**_auth_headers(user_a_token), "X-Household-Id": household_a["id"]},
    )
    assert tx_response.status_code == 201

    # Nutzer B versucht mit SEINEM Token Haushalt As Kontext zu setzen - muss
    # an der API-eigenen Mitgliedschaftspruefung scheitern (Abschnitt 5, Punkt 5).
    response = client.get(
        "/v1/accounts",
        headers={**_auth_headers(user_b_token), "X-Household-Id": household_a["id"]},
    )
    assert response.status_code == 403

    # Nutzer B in seinem EIGENEN (leeren) Haushalt sieht nichts von Haushalt A.
    response = client.get(
        "/v1/accounts",
        headers={**_auth_headers(user_b_token), "X-Household-Id": household_b["id"]},
    )
    assert response.status_code == 200
    assert response.json() == []
    response = client.get(
        "/v1/transactions",
        headers={**_auth_headers(user_b_token), "X-Household-Id": household_b["id"]},
    )
    assert response.status_code == 200
    assert response.json() == []

    # Nutzer A sieht weiterhin nur sein eigenes Konto/seine eigene Buchung.
    response = client.get(
        "/v1/accounts",
        headers={**_auth_headers(user_a_token), "X-Household-Id": household_a["id"]},
    )
    assert [row["id"] for row in response.json()] == [account_a["id"]]


@pytest.mark.asyncio
async def test_rls_blocks_cross_household_at_db_level_even_if_api_check_is_bypassed(
    client: TestClient, user_a_token: str, user_b_token: str
) -> None:
    """Zweite Schutzschicht (Abschnitt 5): simuliert eine fehlerhafte oder
    uebersprungene API-Pruefung und beweist, dass RLS allein - unabhaengig
    von der Python-Logik in app/auth/dependencies.py - den Zugriff blockt."""
    household_a = client.post(
        "/v1/households", json={"name": "RLS-Testhaushalt A"}, headers=_auth_headers(user_a_token)
    ).json()
    client.post(
        "/v1/accounts",
        json={"name": "RLS-Testkonto", "account_type": "checking"},
        headers={**_auth_headers(user_a_token), "X-Household-Id": household_a["id"]},
    )

    user_b_id = _user_id_from_token(user_b_token)
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = json.dumps({"sub": user_b_id, "role": "authenticated"})
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"), {"claims": claims}
        )
        # Haushaltskontext wird direkt auf Haushalt A gezwungen, OHNE die
        # Mitgliedschaftspruefung aus get_household_session() zu durchlaufen.
        await session.execute(
            text("SELECT set_config('app.household_id', :household_id, true)"),
            {"household_id": household_a["id"]},
        )
        accounts = (await session.execute(text("select * from accounts"))).fetchall()
        # households_member_read haengt bewusst NICHT vom app.household_id-GUC
        # ab (sonst koennte man ohne Kontext nie die eigene Haushaltsliste
        # laden) - die relevante Pruefung ist daher, ob Nutzer B die konkrete
        # Zeile von Haushalt A sehen kann, nicht ob die Gesamtliste leer ist.
        household_a_row = (
            await session.execute(
                text("select * from households where id = :id"), {"id": household_a["id"]}
            )
        ).fetchall()

    assert accounts == []
    assert household_a_row == []


# ---------------------------------------------------------------------------
# Rollen-Gates auf schreibenden Endpunkten (Defense-in-Depth)
# ---------------------------------------------------------------------------


def _household_headers(token: str, household_id: str) -> dict[str, str]:
    return {**_auth_headers(token), "X-Household-Id": household_id}


def _invite_member(
    client: TestClient, owner_headers: dict[str, str], household_id: str, role: str, prefix: str
) -> dict[str, str]:
    """Laedt einen frisch registrierten Nutzer per Einladungscode mit der
    gewuenschten Rolle in den Haushalt ein und liefert dessen Request-Header."""
    invite = client.post(
        "/v1/households/invites", json={"role": role}, headers=owner_headers
    ).json()
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-12")
    redeem = client.post(
        "/v1/households/invites/redeem",
        json={"code": invite["code"]},
        headers=_auth_headers(token),
    )
    assert redeem.status_code == 200
    return _household_headers(token, household_id)


@pytest.fixture(scope="module")
def role_setup(client: TestClient) -> dict[str, Any]:
    """Ein Haushalt mit owner/editor/viewer plus Beispieldaten mehrerer
    Ressourcen, dazu ein zweiter (fremder) Haushalt fuer die 404-Faelle.

    Bewusst modulweit: jeder Signup geht gegen das echte lokale Supabase und
    kostet spuerbar Zeit."""
    owner_token = _signup(
        f"role-owner-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-12"
    )
    household = client.post(
        "/v1/households", json={"name": "Rollen-Haushalt"}, headers=_auth_headers(owner_token)
    ).json()
    owner_headers = _household_headers(owner_token, household["id"])

    viewer_headers = _invite_member(
        client, owner_headers, household["id"], "viewer", "role-viewer"
    )
    editor_headers = _invite_member(
        client, owner_headers, household["id"], "editor", "role-editor"
    )

    account = client.post(
        "/v1/accounts",
        json={"name": "Rollenkonto", "account_type": "checking"},
        headers=owner_headers,
    ).json()
    transaction = client.post(
        "/v1/transactions",
        json={"account_id": account["id"], "booked_date": "2026-07-01", "amount_minor": -500},
        headers=owner_headers,
    ).json()
    goal = client.post(
        "/v1/goals",
        json={"name": "Rollen-Sparziel", "target_amount_minor": 100000},
        headers=owner_headers,
    ).json()
    wishlist_item = client.post(
        "/v1/wishlist",
        json={"name": "Rollen-Wunsch", "price_minor": 5000},
        headers=owner_headers,
    ).json()
    meal_week = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-08-03", "budget_minor": 10000},
        headers=owner_headers,
    ).json()

    # Fremder Haushalt: dessen IDs muessen aus dem Rollen-Haushalt heraus als
    # "nicht gefunden" gelten, nicht als "nicht erlaubt".
    foreign_token = _signup(
        f"role-foreign-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-13"
    )
    foreign_household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(foreign_token)
    ).json()
    foreign_headers = _household_headers(foreign_token, foreign_household["id"])
    foreign_goal = client.post(
        "/v1/goals",
        json={"name": "Fremdes Sparziel", "target_amount_minor": 100000},
        headers=foreign_headers,
    ).json()
    foreign_account = client.post(
        "/v1/accounts",
        json={"name": "Fremdkonto", "account_type": "checking"},
        headers=foreign_headers,
    ).json()
    foreign_meal_week = client.post(
        "/v1/meal-weeks",
        json={"week_start": "2026-08-10", "budget_minor": 5000},
        headers=foreign_headers,
    ).json()

    return {
        "household_id": household["id"],
        "owner_headers": owner_headers,
        "editor_headers": editor_headers,
        "viewer_headers": viewer_headers,
        "account": account,
        "transaction": transaction,
        "goal": goal,
        "wishlist_item": wishlist_item,
        "meal_week": meal_week,
        "foreign_goal": foreign_goal,
        "foreign_account": foreign_account,
        "foreign_meal_week": foreign_meal_week,
    }


def test_viewer_may_read_the_household(client: TestClient, role_setup: dict[str, Any]) -> None:
    """Kontrolle zum folgenden Test: der viewer ist echtes Mitglied und sieht
    die Daten - die 403 unten kommen also aus dem Rollen-Gate und nicht aus
    einer fehlgeschlagenen Mitgliedschaft."""
    viewer_headers = role_setup["viewer_headers"]

    accounts = client.get("/v1/accounts", headers=viewer_headers)
    assert accounts.status_code == 200
    assert any(row["id"] == role_setup["account"]["id"] for row in accounts.json())
    assert client.get("/v1/goals", headers=viewer_headers).status_code == 200


def test_viewer_gets_403_on_writes_instead_of_500_or_silent_success(
    client: TestClient, role_setup: dict[str, Any]
) -> None:
    """Deckt mehrere Ressourcen ab. Die DELETEs sind der eigentliche Kern:
    ohne Rollen-Gate und ohne rowcount-Pruefung antworteten sie mit 204,
    obwohl RLS null Zeilen geloescht hat."""
    viewer_headers = role_setup["viewer_headers"]
    goal_id = role_setup["goal"]["id"]
    account_id = role_setup["account"]["id"]

    writes = [
        client.post(
            "/v1/accounts",
            json={"name": "Viewer-Konto", "account_type": "checking"},
            headers=viewer_headers,
        ),
        client.patch(
            f"/v1/accounts/{account_id}", json={"name": "Umbenannt"}, headers=viewer_headers
        ),
        client.post(
            "/v1/transactions",
            json={"account_id": account_id, "booked_date": "2026-07-02", "amount_minor": -100},
            headers=viewer_headers,
        ),
        client.delete(
            f"/v1/transactions/{role_setup['transaction']['id']}", headers=viewer_headers
        ),
        client.post(
            "/v1/goals",
            json={"name": "Viewer-Ziel", "target_amount_minor": 1000},
            headers=viewer_headers,
        ),
        client.patch(
            f"/v1/goals/{goal_id}", json={"name": "Viewer-Umbenennung"}, headers=viewer_headers
        ),
        client.delete(f"/v1/goals/{goal_id}", headers=viewer_headers),
        client.delete(
            f"/v1/wishlist/{role_setup['wishlist_item']['id']}", headers=viewer_headers
        ),
        client.post(
            f"/v1/wishlist/{role_setup['wishlist_item']['id']}/start-savings-plan",
            json={
                "planning_mode": "monthly_amount",
                "monthly_contribution_minor": 1000,
            },
            headers=viewer_headers,
        ),
        client.delete(
            f"/v1/meal-weeks/{role_setup['meal_week']['id']}", headers=viewer_headers
        ),
        client.post(
            "/v1/rules",
            json={"name": "Viewer-Regel", "match_payee_raw": "REWE"},
            headers=viewer_headers,
        ),
        client.put(
            "/v1/budgets/2026/8/allocations", json={"allocations": []}, headers=viewer_headers
        ),
    ]

    assert [response.status_code for response in writes] == [403] * len(writes)
    assert all(response.json()["detail"] == "household_role_readonly" for response in writes)

    # Und nichts davon hat gewirkt.
    assert any(
        row["id"] == goal_id for row in client.get("/v1/goals", headers=viewer_headers).json()
    )
    assert client.get("/v1/accounts", headers=viewer_headers).json()[0]["name"] == "Rollenkonto"


def test_editor_may_write_and_delete(client: TestClient, role_setup: dict[str, Any]) -> None:
    editor_headers = role_setup["editor_headers"]

    created = client.post(
        "/v1/goals",
        json={"name": "Editor-Sparziel", "target_amount_minor": 4200},
        headers=editor_headers,
    )
    assert created.status_code == 201

    account = client.post(
        "/v1/accounts",
        json={"name": "Editor-Konto", "account_type": "savings"},
        headers=editor_headers,
    )
    assert account.status_code == 201

    deleted = client.delete(f"/v1/goals/{created.json()['id']}", headers=editor_headers)
    assert deleted.status_code == 204


def test_write_on_foreign_resource_is_404_not_403_or_silent_204(
    client: TestClient, role_setup: dict[str, Any]
) -> None:
    """Ein Editor mit gueltigem eigenem Haushaltskontext, der die ID einer
    fremden Ressource schickt: die Ressource existiert fuer ihn schlicht
    nicht. 404 - und keinesfalls 204."""
    editor_headers = role_setup["editor_headers"]

    goal_delete = client.delete(
        f"/v1/goals/{role_setup['foreign_goal']['id']}", headers=editor_headers
    )
    assert goal_delete.status_code == 404
    assert goal_delete.json()["detail"] == "goal_not_found"

    meal_week_delete = client.delete(
        f"/v1/meal-weeks/{role_setup['foreign_meal_week']['id']}", headers=editor_headers
    )
    assert meal_week_delete.status_code == 404

    account_patch = client.patch(
        f"/v1/accounts/{role_setup['foreign_account']['id']}",
        json={"name": "Fremdzugriff"},
        headers=editor_headers,
    )
    assert account_patch.status_code == 404

    unknown_delete = client.delete(f"/v1/goals/{uuid.uuid4()}", headers=editor_headers)
    assert unknown_delete.status_code == 404


def test_viewer_cannot_manage_invites(client: TestClient, role_setup: dict[str, Any]) -> None:
    """Owner-Gate: Einladungen und Umbenennen bleiben dem Owner vorbehalten."""
    editor_headers = role_setup["editor_headers"]

    invite = client.post("/v1/households/invites", json={}, headers=editor_headers)
    assert invite.status_code == 403
    assert invite.json()["detail"] == "household_owner_required"

    rename = client.patch("/v1/households", json={"name": "Gekapert"}, headers=editor_headers)
    assert rename.status_code == 403


@pytest.fixture()
def without_editor_gate() -> Iterator[None]:
    """Ersetzt das API-seitige Rollen-Gate durch die ungepruefte Session, um
    die zweite Schicht isoliert zu testen: RLS muss den Schreibzugriff dann
    selbst ablehnen."""
    app.dependency_overrides[require_household_editor] = get_household_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_household_editor, None)


def test_rls_denial_is_mapped_to_403_and_leaks_nothing(
    client: TestClient, role_setup: dict[str, Any], without_editor_gate: None
) -> None:
    """Ohne API-Gate laeuft der INSERT bis in die Datenbank und wird dort von
    accounts_write abgelehnt (SQLSTATE 42501). Der globale Handler aus
    app/core/errors.py macht daraus ein 403 statt eines 500 - und die Antwort
    enthaelt weder die Postgres-Meldung noch den Tabellennamen."""
    response = client.post(
        "/v1/accounts",
        json={"name": "RLS-Test", "account_type": "checking"},
        headers=role_setup["viewer_headers"],
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "insufficient_privilege"}
    body = response.text.lower()
    assert "row-level security" not in body
    assert "accounts" not in body


class _FakeDriverError(Exception):
    """Steht fuer den psycopg-Fehler, den SQLAlchemy als `orig` mitfuehrt."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__("relation accounts, IBAN DE89370400440532013000")
        self.sqlstate = sqlstate


class _FakeDatabaseError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("statement fehlgeschlagen")
        self.orig = _FakeDriverError(sqlstate)


def _dummy_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/accounts",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


@pytest.mark.asyncio
async def test_only_insufficient_privilege_is_translated_to_403() -> None:
    """Der Handler darf ausschliesslich 42501 uebersetzen. Wuerde er jeden
    Datenbankfehler auf 403 abbilden, verschwaende ein echter Defekt (hier:
    Unique-Verletzung 23505) hinter einem plausibel aussehenden 403."""
    assert extract_sqlstate(_FakeDatabaseError("42501")) == "42501"

    denied = await database_permission_error_handler(
        _dummy_request(), _FakeDatabaseError("42501")
    )
    assert denied.status_code == 403

    with pytest.raises(_FakeDatabaseError):
        await database_permission_error_handler(_dummy_request(), _FakeDatabaseError("23505"))
