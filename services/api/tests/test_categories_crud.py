"""Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md, Lieferabschnitt 1).

Abnahmekriterium des Abschnitts: "Ein Nutzer findet ÖPNV ... und kann eigene
Kategorien/Gruppen verwalten. Wiederholtes Paket-Anwenden erzeugt keine
Duplikate." Getestet wird gegen das echte lokale Supabase - Namensnormalisierung,
partielle Unique-Indizes, Versionstrigger und die Baum-Revision entstehen
vollstaendig in der Datenbank und liessen sich mit Mocks nicht nachweisen.
"""

import os
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)

# Aktuell freigegebene Paketversion. Migration 0010 hat v2 nachgelegt: neun
# Gruppen und 50 Kategorien aus v1 plus die Gruppe "Beruf & Arbeitsmittel" und
# sieben Kategorien, die in v1 gefehlt haben (Software/KI-Dienste,
# Arbeitsmittel, berufliche Reisekosten und Beitraege, Buecher & Presse,
# Nahrungsergaenzung, Ratenkaeufe).
STANDARD_TAXONOMY_VERSION = 2
STANDARD_GROUP_COUNT = 10
STANDARD_CATEGORY_COUNT = 57


def _run_sql(statement: str, params: dict[str, Any]) -> None:
    """Direkter Datenbankzugriff fuer einen Testaufbau, den die API bewusst
    nicht anbietet (einen Standard-Schluessel wieder entfernen).

    Bewusst synchron ueber psycopg statt ueber die Async-Engine der App: deren
    Verbindungspool haengt am Event-Loop, in dem der TestClient laeuft."""
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(statement, params)


def _new_household(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    household = client.post(
        "/v1/households", json={"name": "Kategorien-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def household_headers() -> dict[str, str]:
    return _new_household("cat")


def _catalog(headers: dict[str, str]) -> dict[str, Any]:
    response = client.get("/v1/categories/catalog", headers=headers)
    assert response.status_code == 200
    return dict(response.json())


def test_fresh_household_gets_the_full_standard_package(
    household_headers: dict[str, str],
) -> None:
    response = client.get("/v1/categories", headers=household_headers)
    assert response.status_code == 200
    groups = response.json()
    assert len(groups) == STANDARD_GROUP_COUNT

    categories = [category for group in groups for category in group["categories"]]
    assert len(categories) == STANDARD_CATEGORY_COUNT

    # Kernabnahme: die konkrete Kategorie aus dem Plan ist auffindbar - inklusive
    # Umlaut und Synonymen fuer den Suchpicker.
    mobility = next(group for group in groups if group["name"] == "Mobilität")
    public_transport = next(
        category for category in mobility["categories"] if category["name"] == "ÖPNV & Bahn"
    )
    assert public_transport["semantic_key"] == "public_transport"
    assert "bahn" in public_transport["synonyms"]

    catalog = _catalog(household_headers)
    assert catalog["installed_taxonomy_version"] == STANDARD_TAXONOMY_VERSION
    assert catalog["taxonomy_revision"] > 0
    assert len(catalog["groups"]) == STANDARD_GROUP_COUNT

    with_usage = client.get(
        "/v1/categories/catalog",
        params={"include_archived": "false", "include_usage": "true"},
        headers=household_headers,
    )
    assert with_usage.status_code == 200
    usage_categories = [
        category for group in with_usage.json()["groups"] for category in group["categories"]
    ]
    assert len(usage_categories) == STANDARD_CATEGORY_COUNT
    # Ohne Referenzen liefert die Nutzungsabfrage keine Zeile - dann bleibt
    # usage leer statt eine Null-Zaehlung vorzutaeuschen.
    assert all(category["usage"] is None for category in usage_categories)


def test_group_and_category_crud_with_normalized_duplicate_detection(
    household_headers: dict[str, str],
) -> None:
    created = client.post(
        "/v1/categories/groups",
        json={"name": "Büro & Arbeit", "icon": "🗂️"},
        headers=household_headers,
    )
    assert created.status_code == 201
    group = created.json()
    assert group["version"] == 1
    assert group["origin"] == "user"
    assert created.headers["ETag"] == '"1"'

    # Gross-/Kleinschreibung, Umlautumschrift und Mehrfachleerzeichen gelten als
    # derselbe Name (category_normalize_name()).
    duplicate_group = client.post(
        "/v1/categories/groups", json={"name": "buero  &   ARBEIT"}, headers=household_headers
    )
    assert duplicate_group.status_code == 409
    assert duplicate_group.json()["detail"] == "category_group_duplicate_name"

    created_category = client.post(
        "/v1/categories",
        json={"group_id": group["id"], "name": "Fachliteratur", "budget_bucket": "wants"},
        headers=household_headers,
    )
    assert created_category.status_code == 201
    category = created_category.json()
    assert category["group_id"] == group["id"]
    assert category["default_type"] == "expense"
    assert category["origin"] == "user"
    assert created_category.headers["ETag"] == '"1"'

    duplicate_category = client.post(
        "/v1/categories",
        json={"group_id": group["id"], "name": "FACHLITERATUR"},
        headers=household_headers,
    )
    assert duplicate_category.status_code == 409
    assert duplicate_category.json()["detail"] == "category_duplicate_name"

    # Derselbe Name in einer anderen Gruppe bleibt erlaubt.
    other_group = client.post(
        "/v1/categories/groups", json={"name": "Zweitgruppe"}, headers=household_headers
    ).json()
    assert (
        client.post(
            "/v1/categories",
            json={"group_id": other_group["id"], "name": "Fachliteratur"},
            headers=household_headers,
        ).status_code
        == 201
    )

    renamed = client.patch(
        f"/v1/categories/{category['id']}",
        json={"name": "Fachbücher", "clear_fields": ["budget_bucket"]},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Fachbücher"
    assert renamed.json()["budget_bucket"] is None
    assert renamed.json()["version"] == 2

    renamed_group = client.patch(
        f"/v1/categories/groups/{group['id']}",
        json={"name": "Büro, Arbeit & Weiterbildung"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert renamed_group.status_code == 200
    assert renamed_group.json()["name"] == "Büro, Arbeit & Weiterbildung"
    assert renamed_group.json()["active_category_count"] == 1

    # Umbenennen auf einen bereits belegten Gruppennamen bleibt ein Konflikt.
    collision = client.patch(
        f"/v1/categories/groups/{other_group['id']}",
        json={"name": "büro, arbeit & weiterbildung"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert collision.status_code == 409
    assert collision.json()["detail"] == "category_group_duplicate_name"


def test_patch_requires_a_current_if_match(household_headers: dict[str, str]) -> None:
    group = client.post(
        "/v1/categories/groups", json={"name": "Versionstest"}, headers=household_headers
    ).json()
    url = f"/v1/categories/groups/{group['id']}"

    missing = client.patch(url, json={"name": "Ohne Header"}, headers=household_headers)
    assert missing.status_code == 428
    assert missing.json()["detail"] == "if_match_required"

    stale = client.patch(
        url, json={"name": "Veraltet"}, headers={**household_headers, "If-Match": '"99"'}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "version_conflict"
    assert stale.headers["ETag"] == '"1"'

    fresh = client.patch(
        url, json={"name": "Aktuell"}, headers={**household_headers, "If-Match": '"1"'}
    )
    assert fresh.status_code == 200
    assert fresh.json()["version"] == 2
    assert fresh.headers["ETag"] == '"2"'

    # Derselbe Header ein zweites Mal ist jetzt veraltet.
    replay = client.patch(
        url, json={"name": "Nochmal"}, headers={**household_headers, "If-Match": '"1"'}
    )
    assert replay.status_code == 409


def test_reorder_is_guarded_by_the_tree_revision(household_headers: dict[str, str]) -> None:
    catalog = _catalog(household_headers)
    revision = catalog["taxonomy_revision"]
    reversed_group_ids = [group["id"] for group in catalog["groups"]][::-1]

    stale = client.post(
        "/v1/categories/groups/reorder",
        json={"taxonomy_revision": revision - 1, "ordered_ids": reversed_group_ids},
        headers=household_headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "category_tree_revision_conflict"

    applied = client.post(
        "/v1/categories/groups/reorder",
        json={"taxonomy_revision": revision, "ordered_ids": reversed_group_ids},
        headers=household_headers,
    )
    assert applied.status_code == 200
    assert [group["id"] for group in applied.json()["groups"]] == reversed_group_ids
    assert applied.json()["taxonomy_revision"] > revision

    # Fremde/unbekannte IDs in der Reihenfolge sind 404, nicht "still ignoriert".
    unknown = client.post(
        "/v1/categories/groups/reorder",
        json={
            "taxonomy_revision": applied.json()["taxonomy_revision"],
            "ordered_ids": [str(uuid.uuid4())],
        },
        headers=household_headers,
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "category_group_not_found"

    # Kategorien innerhalb einer Gruppe.
    catalog = _catalog(household_headers)
    mobility = next(group for group in catalog["groups"] if group["name"] == "Mobilität")
    reversed_category_ids = [category["id"] for category in mobility["categories"]][::-1]
    reordered = client.post(
        "/v1/categories/reorder",
        json={
            "taxonomy_revision": catalog["taxonomy_revision"],
            "group_id": mobility["id"],
            "ordered_ids": reversed_category_ids,
        },
        headers=household_headers,
    )
    assert reordered.status_code == 200
    updated_group = next(
        group for group in reordered.json()["groups"] if group["id"] == mobility["id"]
    )
    assert [category["id"] for category in updated_group["categories"]] == reversed_category_ids

    # Ohne group_id ist die Kategoriesortierung nicht eindeutig.
    without_group = client.post(
        "/v1/categories/reorder",
        json={
            "taxonomy_revision": reordered.json()["taxonomy_revision"],
            "ordered_ids": reversed_category_ids,
        },
        headers=household_headers,
    )
    assert without_group.status_code == 422
    assert without_group.json()["detail"] == "category_group_required"


def test_viewer_may_read_but_not_manage_categories(household_headers: dict[str, str]) -> None:
    household_id = household_headers["X-Household-Id"]
    viewer_headers = _invite_member(
        client, household_headers, household_id, "viewer", "cat-viewer"
    )

    assert client.get("/v1/categories", headers=viewer_headers).status_code == 200
    assert client.get("/v1/categories/catalog", headers=viewer_headers).status_code == 200
    assert (
        client.get("/v1/categories/standard-package", headers=viewer_headers).status_code == 200
    )

    catalog = _catalog(viewer_headers)
    group = catalog["groups"][0]
    writes = [
        client.post(
            "/v1/categories/groups", json={"name": "Viewer-Gruppe"}, headers=viewer_headers
        ),
        client.post(
            "/v1/categories",
            json={"group_id": group["id"], "name": "Viewer-Kategorie"},
            headers=viewer_headers,
        ),
        client.patch(
            f"/v1/categories/groups/{group['id']}",
            json={"name": "Gekapert"},
            headers={**viewer_headers, "If-Match": f'"{group["version"]}"'},
        ),
        client.post(
            "/v1/categories/groups/reorder",
            json={
                "taxonomy_revision": catalog["taxonomy_revision"],
                "ordered_ids": [group["id"]],
            },
            headers=viewer_headers,
        ),
        client.post("/v1/categories/standard-package/preview", headers=viewer_headers),
    ]
    assert [response.status_code for response in writes] == [403] * len(writes)
    assert all(response.json()["detail"] == "household_role_readonly" for response in writes)

    # Und nichts davon hat gewirkt.
    assert len(_catalog(household_headers)["groups"]) == STANDARD_GROUP_COUNT


def test_editor_may_manage_but_not_apply_the_standard_package(
    household_headers: dict[str, str],
) -> None:
    household_id = household_headers["X-Household-Id"]
    editor_headers = _invite_member(
        client, household_headers, household_id, "editor", "cat-editor"
    )

    created = client.post(
        "/v1/categories/groups", json={"name": "Editor-Gruppe"}, headers=editor_headers
    )
    assert created.status_code == 201

    preview = client.post("/v1/categories/standard-package/preview", headers=editor_headers)
    assert preview.status_code == 200

    applied = client.post(
        "/v1/categories/standard-package/apply",
        json={"operation_id": preview.json()["operation_id"]},
        headers=editor_headers,
    )
    assert applied.status_code == 403
    assert applied.json()["detail"] == "household_owner_required"


def test_foreign_ids_are_not_found(household_headers: dict[str, str]) -> None:
    foreign_headers = _new_household("cat-foreign")
    foreign_catalog = _catalog(foreign_headers)
    foreign_group = foreign_catalog["groups"][0]
    foreign_category = foreign_group["categories"][0]

    category_patch = client.patch(
        f"/v1/categories/{foreign_category['id']}",
        json={"name": "Fremdzugriff"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert category_patch.status_code == 404
    assert category_patch.json()["detail"] == "category_not_found"

    group_patch = client.patch(
        f"/v1/categories/groups/{foreign_group['id']}",
        json={"name": "Fremdzugriff"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert group_patch.status_code == 404
    assert group_patch.json()["detail"] == "category_group_not_found"

    # Anlegen in einer fremden Gruppe ebenfalls 404 statt 403.
    create = client.post(
        "/v1/categories",
        json={"group_id": foreign_group["id"], "name": "Fremdkategorie"},
        headers=household_headers,
    )
    assert create.status_code == 404
    assert create.json()["detail"] == "category_group_not_found"

    # Der fremde Haushalt ist unveraendert.
    assert len(_catalog(foreign_headers)["groups"]) == STANDARD_GROUP_COUNT


def test_standard_package_preview_and_apply_are_idempotent(
    household_headers: dict[str, str],
) -> None:
    status_response = client.get("/v1/categories/standard-package", headers=household_headers)
    assert status_response.status_code == 200
    package_status = status_response.json()
    assert package_status["installed_version"] == STANDARD_TAXONOMY_VERSION
    assert package_status["available_version"] == STANDARD_TAXONOMY_VERSION
    assert package_status["upgrade_available"] is False

    preview = client.post("/v1/categories/standard-package/preview", headers=household_headers)
    assert preview.status_code == 200
    plan = preview.json()
    assert plan["taxonomy_version"] == STANDARD_TAXONOMY_VERSION
    assert plan["new_groups"] == []
    assert plan["already_present_count"] == STANDARD_CATEGORY_COUNT
    assert plan["new_count"] == 0
    assert plan["possible_match_count"] == 0
    assert plan["name_conflict_count"] == 0
    assert all(entry["status"] == "already_present" for entry in plan["entries"])

    body = {"operation_id": plan["operation_id"], "request_id": "standardpaket-1"}
    first = client.post(
        "/v1/categories/standard-package/apply", json=body, headers=household_headers
    )
    assert first.status_code == 200
    assert first.json() == {
        "taxonomy_version": STANDARD_TAXONOMY_VERSION,
        "created_groups": 0,
        "created_categories": 0,
        "linked_existing": 0,
        "skipped_name_conflicts": 0,
        "idempotent_replay": False,
    }

    # Wiederholung mit derselben request_id liefert das gespeicherte Ergebnis
    # statt einer zweiten Anwendung.
    second = client.post(
        "/v1/categories/standard-package/apply", json=body, headers=household_headers
    )
    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True

    # Keine Duplikate entstanden.
    catalog = _catalog(household_headers)
    categories = [category for group in catalog["groups"] for category in group["categories"]]
    assert len(catalog["groups"]) == STANDARD_GROUP_COUNT
    assert len(categories) == STANDARD_CATEGORY_COUNT

    # Eine bereits angewendete Vorschau ist ohne request_id nicht erneut
    # verwendbar.
    stale = client.post(
        "/v1/categories/standard-package/apply",
        json={"operation_id": plan["operation_id"]},
        headers=household_headers,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "category_preview_stale"

    unknown = client.post(
        "/v1/categories/standard-package/apply",
        json={"operation_id": str(uuid.uuid4())},
        headers=household_headers,
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "category_operation_not_found"


def test_standard_package_preview_reports_matches_for_a_reduced_household(
    household_headers: dict[str, str],
) -> None:
    """Die drei nicht-trivialen Vorschaustatus entstehen nur gegen einen
    Bestand, der vom Paket abweicht - hier wird der Standardstand deshalb
    gezielt aufgebrochen."""
    catalog = _catalog(household_headers)
    food = next(
        group for group in catalog["groups"] if group["name"] == "Lebensmittel & Gastronomie"
    )
    groceries = next(
        category for category in food["categories"] if category["semantic_key"] == "groceries"
    )
    bakery = next(
        category for category in food["categories"] if category["semantic_key"] == "bakery_cafe"
    )

    # semantic_key entfernen: aus der Standardkategorie wird eine eigene, die
    # das Paket nur noch als "moegliche Entsprechung" erkennt.
    _run_sql(
        "update categories set semantic_key = null, origin = 'user', "
        "name = 'Lebensmittel' where id = %(id)s",
        {"id": groceries["id"]},
    )
    _run_sql("delete from categories where id = %(id)s", {"id": bakery["id"]})

    preview = client.post("/v1/categories/standard-package/preview", headers=household_headers)
    assert preview.status_code == 200
    plan = preview.json()
    entries = {entry["semantic_key"]: entry for entry in plan["entries"]}

    # "lebensmittel" ist auch als Synonym der gleichnamigen Kategorie hinterlegt.
    assert entries["groceries"]["status"] == "possible_match"
    assert entries["groceries"]["existing_category_id"] == groceries["id"]
    assert entries["bakery_cafe"]["status"] == "new"
    assert plan["possible_match_count"] == 1
    assert plan["new_count"] == 1
    assert plan["already_present_count"] == STANDARD_CATEGORY_COUNT - 2

    applied = client.post(
        "/v1/categories/standard-package/apply",
        json={
            "operation_id": plan["operation_id"],
            "request_id": "teilabgleich-1",
            "confirmed_matches": {"groceries": groceries["id"]},
        },
        headers=household_headers,
    )
    assert applied.status_code == 200
    result = applied.json()
    assert result["linked_existing"] == 1
    assert result["created_categories"] == 1  # nur die geloeschte Baeckerei
    assert result["created_groups"] == 0

    # Die bestaetigte Kategorie behaelt ihren Namen und bekommt nur den
    # Standard-Schluessel - umbenannt wird nichts.
    catalog = _catalog(household_headers)
    linked = next(
        category
        for group in catalog["groups"]
        for category in group["categories"]
        if category["id"] == groceries["id"]
    )
    assert linked["name"] == "Lebensmittel"
    assert linked["semantic_key"] == "groceries"
