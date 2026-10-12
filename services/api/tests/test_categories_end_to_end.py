"""End-to-End-Kette der Kategorienverwaltung.

Deckt Punkt 10 der Verifikationsliste in docs/KATEGORISIERUNGSPLAN.md ab:
Kategorie anlegen -> im Picker finden -> Buchung zuweisen -> Regel erstellen ->
verschieben -> archivieren -> Historie pruefen -> wiederherstellen -> mergen ->
Summen pruefen.

Bewusst EIN durchgehender Ablauf statt vieler isolierter Faelle: die
Einzeloperationen sind in test_categories_crud.py, test_categories_lifecycle.py
und test_categories_assignment.py jeweils fuer sich geprueft. Hier geht es
darum, dass sie in Folge auf demselben Datenbestand zusammenpassen - genau
dort entstehen die Fehler, die einzelne Tests nicht sehen (veraltete
Vorschauen, doppelte Versionsspruenge, verschwundene Summen).
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

GROCERY = "Lebensmittel"
RESTAURANT = "Restaurant"
FOOD_GROUP = "Lebensmittel & Gastronomie"
LIFESTYLE_GROUP = "Freizeit, Konsum & Reisen"


@pytest.fixture()
def headers() -> dict[str, str]:
    token = _signup(f"e2e-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households",
        json={"name": "Kategorien-E2E"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(headers: dict[str, str]) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return str(account["id"])


def _picker(headers: dict[str, str]) -> list[dict[str, Any]]:
    response = client.get("/v1/categories", headers=headers)
    assert response.status_code == 200
    return list(response.json())


def _picker_names(headers: dict[str, str]) -> set[str]:
    return {c["name"] for g in _picker(headers) for c in g["categories"]}


def _find(headers: dict[str, str], name: str) -> dict[str, Any]:
    for group in _picker(headers):
        for category in group["categories"]:
            if category["name"] == name:
                return dict(category)
    raise AssertionError(f"Kategorie '{name}' nicht im aktiven Picker")


def _group_id(headers: dict[str, str], name: str) -> str:
    for group in _picker(headers):
        if group["name"] == name:
            return str(group["id"])
    raise AssertionError(f"Gruppe '{name}' nicht gefunden")


def _catalog(headers: dict[str, str]) -> dict[str, Any]:
    response = client.get(
        "/v1/categories/catalog?include_archived=true&include_usage=true", headers=headers
    )
    assert response.status_code == 200
    return dict(response.json())


def _catalog_category(headers: dict[str, str], category_id: str) -> dict[str, Any]:
    for group in _catalog(headers)["groups"]:
        for category in group["categories"]:
            if str(category["id"]) == category_id:
                return dict(category)
    raise AssertionError(f"Kategorie {category_id} nicht im Katalog")


def _book(
    headers: dict[str, str], account_id: str, booked_date: str, amount_minor: int, category_id: str
) -> dict[str, Any]:
    response = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": booked_date,
            "amount_minor": amount_minor,
            "category_id": category_id,
            "payee_label": "REWE Markt",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _budget_totals(headers: dict[str, str], year: int, month: int) -> dict[str, int]:
    summary = client.get(f"/v1/budgets/{year}/{month}", headers=headers).json()
    return {
        "income": summary["total_income_minor"],
        "spent": summary["total_spent_minor"],
        "planned": summary["total_planned_minor"],
        "rollover": summary["total_rollover_minor"],
    }


def test_category_lifecycle_end_to_end(headers: dict[str, str], account_id: str) -> None:
    # ---- 1. Standardpaket ist installiert und fachlich brauchbar -----------
    groups = _picker(headers)
    # Paketversion 2 (Migration 0010): die neun Gruppen aus v1 plus "Beruf &
    # Arbeitsmittel", 50 Kategorien plus sieben Nachzuegler.
    assert len(groups) == 10
    names = _picker_names(headers)
    assert len(names) == 57
    # Der konkrete Schmerzpunkt aus dem Plan: OEPNV muss ein eigenes Ziel
    # haben und nicht in "Freizeit" landen.
    assert "ÖPNV & Bahn" in names
    assert "Freizeit" not in names
    # Derselbe Schmerzpunkt fuer beruflich genutzte Software: sie darf nicht in
    # "Streaming & digitale Medien" landen, weil es sonst nichts Passendes gibt.
    assert "Software, KI-Dienste & Lizenzen" in names

    # ---- 2. Eigene Kategorie anlegen und im Picker finden ------------------
    food_group_id = _group_id(headers, FOOD_GROUP)
    created = client.post(
        "/v1/categories",
        json={
            "group_id": food_group_id,
            "name": "Hofladen",
            "default_type": "expense",
            "budget_bucket": "needs",
            "synonyms": ["direktvermarkter", "bauernhof"],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    own = created.json()
    own_id = str(own["id"])
    assert created.headers.get("ETag") == f'"{own["version"]}"'
    assert "Hofladen" in _picker_names(headers)

    # ---- 3. Buchung anlegen und die Kategorie sichtbar aendern -------------
    transaction = _book(headers, account_id, "2026-03-05", -2500, _find(headers, GROCERY)["id"])
    assigned = client.put(
        f"/v1/transactions/{transaction['id']}/category",
        json={"category_id": own_id, "expected_version": transaction["version"]},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["category_id"] == own_id
    # Genau ein Versionssprung, nicht zwei.
    assert assigned.json()["version"] == transaction["version"] + 1

    # ---- 4. Regel fuer denselben Zahlungsempfaenger anlegen ----------------
    rule = client.post(
        "/v1/rules",
        json={
            "name": "REWE -> Hofladen",
            "match_payee_raw": "REWE Markt",
            "action_category_id": own_id,
        },
        headers=headers,
    )
    assert rule.status_code == 201, rule.text
    rule_id = str(rule.json()["id"])

    # ---- 5. Verschieben: Vorschau zeigt die Wirkung, danach anwenden -------
    lifestyle_group_id = _group_id(headers, LIFESTYLE_GROUP)
    preview = client.post(
        f"/v1/categories/{own_id}/move-preview",
        json={"target_group_id": lifestyle_group_id},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    move_preview = preview.json()
    assert move_preview["affected_splits"] == 1
    assert move_preview["source_group_name"] == FOOD_GROUP
    assert move_preview["target_group_name"] == LIFESTYLE_GROUP

    moved = client.post(
        f"/v1/categories/{own_id}/move",
        json={
            "operation_id": move_preview["operation_id"],
            "target_group_id": lifestyle_group_id,
        },
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    assert str(moved.json()["group_id"]) == lifestyle_group_id
    # UUID und Zuordnung der Buchung bleiben unveraendert.
    detail = client.get(f"/v1/transactions/{transaction['id']}", headers=headers).json()
    assert detail["category_id"] == own_id

    # Dieselbe Vorschau ein zweites Mal anzuwenden muss scheitern.
    replay = client.post(
        f"/v1/categories/{own_id}/move",
        json={
            "operation_id": move_preview["operation_id"],
            "target_group_id": lifestyle_group_id,
        },
        headers=headers,
    )
    assert replay.status_code == 409
    assert replay.json()["detail"] == "category_preview_stale"

    # ---- 6. Archivieren mit Ersatz: Historie bleibt, Zukunft wandert -------
    totals_before = _budget_totals(headers, 2026, 3)
    replacement_id = _find(headers, RESTAURANT)["id"]

    archive_preview = client.post(f"/v1/categories/{own_id}/archive-preview", headers=headers)
    assert archive_preview.status_code == 200, archive_preview.text
    impact = archive_preview.json()
    assert impact["historical"]["transaction_splits"] == 1
    assert impact["forward"]["classification_rules"] == 1
    assert impact["requires_resolution"] is True

    archived = client.post(
        f"/v1/categories/{own_id}/archive",
        json={
            "operation_id": impact["operation_id"],
            "replacement_category_id": replacement_id,
        },
        headers=headers,
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["is_archived"] is True

    # Nicht mehr neu waehlbar ...
    assert "Hofladen" not in _picker_names(headers)
    # ... aber die historische Zuordnung bleibt lesbar ...
    detail = client.get(f"/v1/transactions/{transaction['id']}", headers=headers).json()
    assert detail["category_id"] == own_id
    # ... und die Summen aendern sich durch reine Stammdatenpflege nicht.
    assert _budget_totals(headers, 2026, 3) == totals_before
    # Die Regel schreibt nicht weiter auf die archivierte Kategorie.
    rule_after = next(
        r for r in client.get("/v1/rules", headers=headers).json() if str(r["id"]) == rule_id
    )
    assert str(rule_after["action_category_id"]) == str(replacement_id)
    # Und neu zuweisen laesst sie sich nicht mehr.
    blocked = client.put(
        f"/v1/transactions/{transaction['id']}/category",
        json={"category_id": own_id},
        headers=headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "category_inactive"

    # ---- 7. Wiederherstellen -----------------------------------------------
    restored = client.post(f"/v1/categories/{own_id}/restore", json={}, headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_archived"] is False
    assert "Hofladen" in _picker_names(headers)

    # ---- 8. Merge: Summen bleiben identisch, Tombstone kanonisiert ---------
    totals_before_merge = _budget_totals(headers, 2026, 3)
    target_id = _find(headers, RESTAURANT)["id"]

    merge_preview = client.post(
        "/v1/categories/merge-preview",
        json={"source_category_id": own_id, "target_category_id": target_id},
        headers=headers,
    )
    assert merge_preview.status_code == 200, merge_preview.text
    merge_plan = merge_preview.json()
    assert merge_plan["references"]["transaction_splits"] == 1
    assert merge_plan["transactions_to_bump"] == 1

    version_before = client.get(
        f"/v1/transactions/{transaction['id']}", headers=headers
    ).json()["version"]

    merged = client.post(
        "/v1/categories/merge",
        json={"operation_id": merge_plan["operation_id"], "request_id": "e2e-merge-1"},
        headers=headers,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["bumped_transactions"] == 1

    detail = client.get(f"/v1/transactions/{transaction['id']}", headers=headers).json()
    assert str(detail["category_id"]) == str(target_id)
    # Genau ein Versionssprung fuer die betroffene Buchung.
    assert detail["version"] == version_before + 1
    # Summenneutral.
    assert _budget_totals(headers, 2026, 3) == totals_before_merge

    # Quelle ist ein nicht wiederherstellbarer Tombstone ...
    tombstone = _catalog_category(headers, own_id)
    assert tombstone["is_archived"] is True
    assert str(tombstone["merged_into_category_id"]) == str(target_id)
    not_restorable = client.post(f"/v1/categories/{own_id}/restore", json={}, headers=headers)
    assert not_restorable.status_code == 409
    assert not_restorable.json()["detail"] == "category_merged_not_restorable"

    # ... auf den ein verspaeteter Offline-Request weiterhin kanonisiert wird.
    late = client.put(
        f"/v1/transactions/{transaction['id']}/category",
        json={"category_id": own_id},
        headers=headers,
    )
    assert late.status_code == 200, late.text
    assert str(late.json()["category_id"]) == str(target_id)

    # Wiederholter Merge mit derselben request_id liefert dasselbe Ergebnis.
    idempotent = client.post(
        "/v1/categories/merge",
        json={"operation_id": merge_plan["operation_id"], "request_id": "e2e-merge-1"},
        headers=headers,
    )
    assert idempotent.status_code == 200, idempotent.text
    assert idempotent.json()["idempotent_replay"] is True


def test_no_api_path_can_hard_delete_a_category(headers: dict[str, str]) -> None:
    """Abnahmekriterium: "Es gibt keinen UI- oder API-Weg, eine referenzierte
    Kategorie hart zu loeschen." Geprueft wird die Abwesenheit des Verbs am
    tatsaechlich veroeffentlichten Vertrag, nicht an der Implementierung."""
    category_id = _find(headers, GROCERY)["id"]
    response = client.delete(f"/v1/categories/{category_id}", headers=headers)
    assert response.status_code == 405

    paths = app.openapi()["paths"]
    category_paths = [p for p in paths if p.startswith("/v1/categories")]
    assert category_paths, "Kategorienendpunkte fehlen"
    for path in category_paths:
        assert "delete" not in paths[path], f"{path} bietet ein DELETE an"
