"""Regel-Import mit Duplikatpruefung und Sammellauf ueber alle Regeln.

Der Import erkannte frueher nur zeichengleiche Haendlermuster und ignorierte
die Zielkategorie. Dasselbe Paket liess sich damit beliebig oft installieren,
und ein Paket mit `(?i)\\b(AMAZON|EBAY)\\b` galt neben einer bestehenden Regel
`Amazon` als voellig neu. Diese Tests halten die drei Abstufungen der Pruefung
fest (duplicate/conflict/overlap) und den Sammellauf, der einen frisch
importierten Regelsatz in einem Rutsch auf den Bestand anwendet.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

# Namen aus dem deutschen Standardpaket (supabase/migrations/0001_schema.sql).
CAT_GROCERIES = "Lebensmittel"
CAT_CLOTHING = "Kleidung & Schuhe"


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"rulesio-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Regelimport-Haushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _category_id(headers: dict[str, str], name: str) -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Kategorie '{name}' nicht gefunden")


def _portable_rule(**overrides: Any) -> dict[str, Any]:
    rule = {
        "name": "Amazon",
        "match_payee_raw": "Amazon",
        "match_payee_exact_word": False,
        "match_purpose": None,
        "match_purpose_exact_word": False,
        "exclude_payee_raw": None,
        "exclude_purpose": None,
        "match_min_amount_minor": None,
        "match_max_amount_minor": None,
        "match_amount_mode": "inside",
        "action_category_key": CAT_GROCERIES,
        "action_set_transfer": False,
        "action_note": None,
        "priority": 10,
        "is_active": True,
    }
    rule.update(overrides)
    return rule


def _package(*rules: dict[str, Any], name: str = "Testpaket") -> dict[str, Any]:
    return {
        "format_version": "1.0",
        "name": name,
        "description": "",
        "author": "",
        "rules": list(rules),
    }


def _preview(headers: dict[str, str], package: dict[str, Any]) -> dict[str, Any]:
    res = client.post(
        "/v1/rules/import/preview", json={"package": package}, headers=headers
    )
    assert res.status_code == 200, res.text
    return res.json()


def _import(headers: dict[str, str], package: dict[str, Any], **extra: Any) -> dict[str, Any]:
    res = client.post(
        "/v1/rules/import", json={"package": package, **extra}, headers=headers
    )
    assert res.status_code == 200, res.text
    return res.json()


def _create_rule(headers: dict[str, str], **fields: Any) -> str:
    res = client.post("/v1/rules", json=fields, headers=headers)
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


# ---------- Duplikatpruefung ------------------------------------------------


def test_preview_marks_identical_rule_as_duplicate(household_headers: dict[str, str]) -> None:
    """Gleiche Bedingung, gleiche Aktion - der Import brauchte nichts."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _create_rule(
        household_headers,
        name="Amazon",
        match_payee_raw="Amazon",
        action_category_id=groceries,
    )

    preview = _preview(household_headers, _package(_portable_rule()))

    assert preview["duplicates"] == 1
    assert preview["recommended"] == 0
    item = preview["items"][0]
    assert item["status"] == "duplicate"
    assert item["is_duplicate"] is True
    assert item["recommended"] is False
    assert item["existing_rule_name"] == "Amazon"


def test_preview_ignores_case_and_padding_of_the_pattern(
    household_headers: dict[str, str],
) -> None:
    """Die Regel-Engine vergleicht ohne Ruecksicht auf Gross-/Kleinschreibung -
    die Duplikatpruefung muss dieselbe Sicht haben, sonst ist jede abweichende
    Schreibweise ein "neuer" Eintrag."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _create_rule(
        household_headers,
        name="Amazon",
        match_payee_raw="amazon",
        action_category_id=groceries,
    )

    preview = _preview(
        household_headers, _package(_portable_rule(match_payee_raw="  AMAZON  "))
    )

    assert preview["items"][0]["status"] == "duplicate"


def test_preview_marks_other_target_category_as_conflict(
    household_headers: dict[str, str],
) -> None:
    """Gleiche Bedingung, andere Kategorie: beide Regeln stritten um dieselben
    Buchungen."""
    clothing = _category_id(household_headers, CAT_CLOTHING)
    _create_rule(
        household_headers,
        name="Amazon (Kleidung)",
        match_payee_raw="Amazon",
        action_category_id=clothing,
    )

    preview = _preview(household_headers, _package(_portable_rule()))

    assert preview["conflicts"] == 1
    assert preview["duplicates"] == 0
    item = preview["items"][0]
    assert item["status"] == "conflict"
    assert item["is_duplicate"] is False
    assert item["recommended"] is False
    assert item["existing_category_name"] == CAT_CLOTHING


def test_preview_marks_similar_pattern_as_overlap(household_headers: dict[str, str]) -> None:
    """Der Fall "20x eine aehnliche Regel": ein Paket bringt einen Haendler
    mit, den eine bestehende Regel in ihrer Aufzaehlung laengst abdeckt."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _create_rule(
        household_headers,
        name="Online-Shopping",
        match_payee_raw="(?i)\\b(AMAZON|EBAY)\\b",
        action_category_id=groceries,
    )

    preview = _preview(household_headers, _package(_portable_rule()))

    assert preview["overlaps"] == 1
    item = preview["items"][0]
    assert item["status"] == "overlap"
    assert item["recommended"] is False
    assert item["shared_terms"] == ["amazon"]
    assert item["existing_rule_name"] == "Online-Shopping"


def test_preview_does_not_link_unrelated_rules(household_headers: dict[str, str]) -> None:
    """Gegenprobe zur Ueberschneidung: verschiedene Haendler bleiben getrennt."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _create_rule(
        household_headers,
        name="Rewe",
        match_payee_raw="(?i)\\b(REWE|PENNY)\\b",
        action_category_id=groceries,
    )

    preview = _preview(household_headers, _package(_portable_rule()))

    assert preview["items"][0]["status"] == "new"
    assert preview["recommended"] == 1


def test_preview_marks_duplicate_inside_the_package(
    household_headers: dict[str, str],
) -> None:
    """Ein Paket, das denselben Eintrag zweimal mitbringt, darf ihn nicht
    zweimal als neu ausweisen."""
    preview = _preview(
        household_headers, _package(_portable_rule(), _portable_rule(name="Amazon (2)"))
    )

    assert preview["recommended"] == 1
    assert preview["duplicates"] == 1
    assert preview["items"][0]["status"] == "new"
    second = preview["items"][1]
    assert second["status"] == "duplicate"
    assert second["in_package_duplicate"] is True
    assert second["index"] == 1


# ---------- Import ----------------------------------------------------------


def test_import_of_the_same_package_twice_creates_nothing(
    household_headers: dict[str, str],
) -> None:
    """Der Kern der Beschwerde: dasselbe Paket liess sich beliebig oft
    installieren."""
    package = client.get("/v1/rules/packages/supermarkt", headers=household_headers).json()

    first = _import(household_headers, package)
    assert first["created"] > 0
    assert first["errors"] == []

    second = _import(household_headers, package)
    assert second["created"] == 0
    assert second["skipped_duplicates"] == first["created"]

    rules = client.get("/v1/rules", headers=household_headers).json()
    assert len(rules) == first["created"]


def test_import_skips_deselected_positions(household_headers: dict[str, str]) -> None:
    package = _package(
        _portable_rule(name="Amazon", match_payee_raw="Amazon"),
        _portable_rule(name="Otto", match_payee_raw="Otto"),
        _portable_rule(name="Zalando", match_payee_raw="Zalando"),
    )

    result = _import(household_headers, package, skip_indices=[1])

    assert result["created"] == 2
    assert result["skipped_deselected"] == 1
    names = {rule["name"] for rule in client.get("/v1/rules", headers=household_headers).json()}
    assert names == {"Amazon", "Zalando"}


def test_import_survives_a_unique_index_collision(
    household_headers: dict[str, str],
) -> None:
    """Ohne Savepoint je Regel nahm ein einziger Indexkonflikt die ganze
    Transaktion mit - ein Paket aus drei Regeln bliebe wegen einer bereits
    vorhandenen komplett aussen vor."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _create_rule(
        household_headers,
        name="Amazon",
        match_payee_raw="Amazon",
        action_category_id=groceries,
    )

    package = _package(
        _portable_rule(name="Amazon", match_payee_raw="Amazon"),
        _portable_rule(name="Otto", match_payee_raw="Otto"),
        _portable_rule(name="Zalando", match_payee_raw="Zalando"),
    )
    # skip_duplicates=False laesst die Regel bis zur Datenbank durch, wo der
    # partielle Unique-Index classification_rules_payee_category_uniq greift.
    result = _import(household_headers, package, skip_duplicates=False)

    assert result["created"] == 2
    assert result["skipped_duplicates"] == 1
    assert result["errors"] == []


def test_package_list_reports_installed_rules(household_headers: dict[str, str]) -> None:
    package = client.get("/v1/rules/packages/supermarkt", headers=household_headers).json()

    before = client.get("/v1/rules/packages", headers=household_headers).json()
    assert {meta["installed_count"] for meta in before} == {0}

    _import(household_headers, package)

    after = client.get("/v1/rules/packages", headers=household_headers).json()
    supermarkt = next(meta for meta in after if meta["id"] == "supermarkt")
    assert supermarkt["installed_count"] == supermarkt["rule_count"]
    assert all(
        meta["installed_count"] == 0 for meta in after if meta["id"] != "supermarkt"
    )


# ---------- Sammellauf ------------------------------------------------------


@pytest.fixture()
def account_id(household_headers: dict[str, str]) -> str:
    acc = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return str(acc["id"])


def _create_transaction(
    headers: dict[str, str],
    account_id: str,
    *,
    payee_label: str,
    amount_minor: int = -2500,
    booked_date: str = "2026-03-02",
    category_id: str | None = None,
) -> str:
    body: dict[str, Any] = {
        "account_id": account_id,
        "booked_date": booked_date,
        "amount_minor": amount_minor,
        "note": payee_label,
        "payee_label": payee_label,
    }
    if category_id is not None:
        body["category_id"] = category_id
    res = client.post("/v1/transactions", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def _split_category(headers: dict[str, str], transaction_id: str) -> str | None:
    res = client.get(f"/v1/transactions/{transaction_id}", headers=headers)
    assert res.status_code == 200, res.text
    splits = res.json()["splits"]
    return splits[0]["category_id"] if splits else None


def test_apply_all_covers_every_rule_in_one_run(
    household_headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(household_headers, CAT_GROCERIES)
    clothing = _category_id(household_headers, CAT_CLOTHING)
    _create_rule(
        household_headers,
        name="Rewe",
        match_payee_raw="Rewe",
        action_category_id=groceries,
    )
    _create_rule(
        household_headers,
        name="Zalando",
        match_payee_raw="Zalando",
        action_category_id=clothing,
    )
    rewe = _create_transaction(household_headers, account_id, payee_label="REWE Markt GmbH")
    zalando = _create_transaction(household_headers, account_id, payee_label="ZALANDO SE")
    _create_transaction(household_headers, account_id, payee_label="Unbekannt AG")

    preview = client.post(
        "/v1/rules/apply-all/preview", json={}, headers=household_headers
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True
    assert body["applied"] == 2
    assert body["rules_evaluated"] == 2
    assert body["transactions_scanned"] == 3
    # Vorschau schreibt nicht.
    assert _split_category(household_headers, rewe) is None

    applied = client.post("/v1/rules/apply-all", json={}, headers=household_headers)
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["dry_run"] is False
    assert result["applied"] == 2
    assert _split_category(household_headers, rewe) == groceries
    assert _split_category(household_headers, zalando) == clothing

    by_name = {summary["rule_name"]: summary for summary in result["rules"]}
    assert by_name["Rewe"]["applied"] == 1
    assert by_name["Zalando"]["applied"] == 1


def test_apply_all_lets_the_highest_priority_rule_win(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Je Buchung entscheidet die erste passende Regel - wie beim Import. Ohne
    diese Reihenfolge ueberschriebe die zuletzt gelaufene Regel das Ergebnis
    der hoeher priorisierten."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    clothing = _category_id(household_headers, CAT_CLOTHING)
    _create_rule(
        household_headers,
        name="Allgemein",
        match_payee_raw="Amazon",
        action_category_id=clothing,
        priority=1,
    )
    _create_rule(
        household_headers,
        name="Spezialfall",
        match_payee_raw="Amazon Fresh",
        action_category_id=groceries,
        priority=100,
    )
    transaction = _create_transaction(
        household_headers, account_id, payee_label="AMAZON FRESH DE"
    )

    result = client.post("/v1/rules/apply-all", json={}, headers=household_headers).json()

    assert _split_category(household_headers, transaction) == groceries
    by_name = {summary["rule_name"]: summary for summary in result["rules"]}
    assert by_name["Spezialfall"]["applied"] == 1
    # Die niedriger priorisierte Regel hat die Buchung gar nicht erst gesehen.
    assert by_name["Allgemein"]["matched"] == 0


def test_apply_all_reports_why_a_rule_did_not_run(
    household_headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _create_rule(
        household_headers,
        name="Inaktiv",
        match_payee_raw="Rewe",
        action_category_id=groceries,
        is_active=False,
    )
    _create_rule(household_headers, name="Ohne Wirkung", match_payee_raw="Penny")

    result = client.post("/v1/rules/apply-all", json={}, headers=household_headers).json()

    reasons = {
        summary["rule_name"]: summary["excluded_reason"] for summary in result["rules"]
    }
    assert reasons == {"Inaktiv": "inactive", "Ohne Wirkung": "no_effect"}
    assert result["rules_total"] == 2
    assert result["rules_evaluated"] == 0


def test_apply_all_keeps_existing_categories_by_default(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Vorbelegt ist "nur Buchungen ohne Kategorie" - ein Sammellauf darf
    Handarbeit nicht stillschweigend ueberschreiben."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    clothing = _category_id(household_headers, CAT_CLOTHING)
    _create_rule(
        household_headers,
        name="Rewe",
        match_payee_raw="Rewe",
        action_category_id=groceries,
    )
    transaction = _create_transaction(
        household_headers, account_id, payee_label="REWE Markt", category_id=clothing
    )

    default_run = client.post(
        "/v1/rules/apply-all", json={}, headers=household_headers
    ).json()
    assert default_run["applied"] == 0
    assert default_run["skipped"] == 1
    assert _split_category(household_headers, transaction) == clothing

    overwrite = client.post(
        "/v1/rules/apply-all", json={"scope": "all"}, headers=household_headers
    ).json()
    assert overwrite["applied"] == 1
    assert _split_category(household_headers, transaction) == groceries
