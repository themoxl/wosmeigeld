"""Lebenszyklus einer Kategorie (docs/KATEGORISIERUNGSPLAN.md, Abschnitte
"Lebenszyklus einer Kategorie", "Historie, Budgets und Berichte" sowie
Lieferabschnitte 2 und 4).

Die fachlichen Zusagen, die hier abgesichert werden:

* Verschieben aendert Gruppe und Sortierung, aber keinen einzigen Split.
* Archivieren loest zukunftswirksame Referenzen auf, laesst die Historie in
  Ruhe und hinterlaesst keine aktive Regel, die weiter auf die archivierte
  Kategorie schreibt.
* Wiederherstellen scheitert an einer archivierten Gruppe und an einem
  belegten Namen - beides mit einer verstaendlichen 409 statt eines
  Indexfehlers.
* Merge ist owner-only, idempotent, haushaltsisoliert und summenneutral und
  hebt die Version jeder betroffenen Buchung um genau eins.
* Eine veraltete Vorschau ueberschreibt nie eine spaetere Nutzeraenderung.

Laeuft gegen das echte lokale Supabase (siehe tests/conftest.py). Fuer
Ausgangszustaende, die es ueber die API nicht (oder nur umstaendlich) gibt -
Regeln mit kollidierendem Haendlermuster, Rollover-Zeilen, mehrere Splits an
einer Buchung - wird dieselbe Datenbank direkt beschrieben; das ist derselbe
Zugriffsweg wie in tests/test_ai_run_recovery.py.
"""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)

_DSN = "postgresql://postgres:postgres@127.0.0.1:55322/postgres"


# ---------------------------------------------------------------------------
# Direktzugriff auf dieselbe lokale Datenbank
# ---------------------------------------------------------------------------


def _db_exec(sql: str, params: dict[str, Any] | None = None) -> None:
    with psycopg.connect(_DSN) as conn:
        conn.execute(sql, params or {})
        conn.commit()


def _db_scalar(sql: str, params: dict[str, Any] | None = None) -> Any:
    with psycopg.connect(_DSN) as conn:
        row = conn.execute(sql, params or {}).fetchone()
    return None if row is None else row[0]


def _db_row(sql: str, params: dict[str, Any] | None = None) -> Any:
    with psycopg.connect(_DSN) as conn:
        return conn.execute(sql, params or {}).fetchone()


def _db_rows(sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    with psycopg.connect(_DSN) as conn:
        return list(conn.execute(sql, params or {}).fetchall())


def _new_group(household_id: str, name: str) -> str:
    return str(
        _db_scalar(
            "insert into category_groups (household_id, name, sort_order) "
            "values (%(hh)s, %(name)s, 0) returning id",
            {"hh": household_id, "name": name},
        )
    )


def _new_category(
    household_id: str, group_id: str, name: str, default_type: str | None = "expense"
) -> str:
    return str(
        _db_scalar(
            "insert into categories (household_id, group_id, name, default_type) "
            "values (%(hh)s, %(g)s, %(name)s, %(t)s) returning id",
            {"hh": household_id, "g": group_id, "name": name, "t": default_type},
        )
    )


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _sums(household_id: str) -> dict[str, int]:
    """Die Invarianten aus dem Plan: Einnahmen, Ausgaben, Budgetplanung und
    Uebertraege muessen vor und nach Archivierung/Merge identisch sein."""
    row = _db_row(
        "select "
        "coalesce((select sum(s.amount_minor) from transaction_splits s "
        "  join transactions t on t.id = s.transaction_id "
        "  where s.household_id = %(hh)s and not t.is_archived "
        "  and s.amount_minor > 0), 0) as income, "
        "coalesce((select sum(s.amount_minor) from transaction_splits s "
        "  join transactions t on t.id = s.transaction_id "
        "  where s.household_id = %(hh)s and not t.is_archived "
        "  and s.amount_minor < 0), 0) as expense, "
        "coalesce((select sum(planned_minor) from budget_allocations "
        "  where household_id = %(hh)s), 0) as planned, "
        "coalesce((select sum(amount_minor) from budget_rollovers "
        "  where household_id = %(hh)s), 0) as rollovers",
        {"hh": household_id},
    )
    return {
        "income": int(row[0]),
        "expense": int(row[1]),
        "planned": int(row[2]),
        "rollovers": int(row[3]),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def owner() -> dict[str, Any]:
    """Ein Haushalt mit Owner und Konto. Modulweit, weil jeder Signup gegen das
    echte Supabase geht; die Tests legen sich ihre Kategorien jeweils frisch
    mit eindeutigen Namen an und stehen sich damit nicht im Weg."""
    token = _signup(f"cat-life-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    household = client.post(
        "/v1/households", json={"name": "Kategorien-Lebenszyklus"}, headers=_auth_headers(token)
    ).json()
    headers = {**_auth_headers(token), "X-Household-Id": household["id"]}
    account = client.post(
        "/v1/accounts",
        json={"name": "Lebenszykluskonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return {
        "token": token,
        "household_id": str(household["id"]),
        "headers": headers,
        "account_id": str(account["id"]),
    }


def _booking(owner: dict[str, Any], amount_minor: int, category_id: str, booked_date: str) -> str:
    response = client.post(
        "/v1/transactions",
        json={
            "account_id": owner["account_id"],
            "booked_date": booked_date,
            "amount_minor": amount_minor,
            "category_id": category_id,
        },
        headers=owner["headers"],
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _budget_id(owner: dict[str, Any], year: int, month: int) -> str:
    response = client.get(f"/v1/budgets/{year}/{month}", headers=owner["headers"])
    assert response.status_code == 200
    return str(
        _db_scalar(
            "select id from budgets where household_id = %(hh)s and year = %(y)s "
            "and month = %(m)s",
            {"hh": owner["household_id"], "y": year, "m": month},
        )
    )


def _allocate(
    owner: dict[str, Any],
    year: int,
    month: int,
    entries: list[dict[str, Any]],
) -> None:
    response = client.put(
        f"/v1/budgets/{year}/{month}/allocations",
        json={"allocations": entries},
        headers=owner["headers"],
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Verschieben
# ---------------------------------------------------------------------------


def test_move_preview_reports_history_and_apply_keeps_splits(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    source_group = _new_group(household_id, _unique("Quellgruppe"))
    target_group = _new_group(household_id, _unique("Zielgruppe"))
    category = _new_category(household_id, source_group, _unique("Umzugskategorie"))

    _booking(owner, -1500, category, "2026-03-05")
    _booking(owner, -2500, category, "2026-05-20")
    _allocate(owner, 2026, 4, [{"category_id": category, "planned_minor": 12000}])

    preview = client.post(
        f"/v1/categories/{category}/move-preview",
        json={"target_group_id": target_group},
        headers=owner["headers"],
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["affected_splits"] == 2
    assert body["first_booked_date"] == "2026-03-05"
    assert body["last_booked_date"] == "2026-05-20"
    assert body["affected_budget_allocations"] == 1
    assert body["source_group_id"] == source_group
    assert body["target_group_id"] == target_group
    # Der Plan verlangt den konkreten Hinweis auf die rueckwirkende Wirkung.
    assert "rueckwirkend" in body["hint"]

    split_ids_before = sorted(
        str(row[0])
        for row in _db_rows(
            "select id from transaction_splits where household_id = %(hh)s "
            "and category_id = %(c)s",
            {"hh": household_id, "c": category},
        )
    )

    applied = client.post(
        f"/v1/categories/{category}/move",
        json={"operation_id": body["operation_id"], "target_group_id": target_group},
        headers=owner["headers"],
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["group_id"] == target_group
    assert applied.headers["ETag"] == f'"{applied.json()["version"]}"'

    split_ids_after = sorted(
        str(row[0])
        for row in _db_rows(
            "select id from transaction_splits where household_id = %(hh)s "
            "and category_id = %(c)s",
            {"hh": household_id, "c": category},
        )
    )
    assert split_ids_after == split_ids_before

    # Dieselbe Vorschau ein zweites Mal anwenden: der Vorgang ist verbraucht.
    replay = client.post(
        f"/v1/categories/{category}/move",
        json={"operation_id": body["operation_id"], "target_group_id": target_group},
        headers=owner["headers"],
    )
    assert replay.status_code == 409
    assert replay.json()["detail"] == "category_preview_stale"


def test_move_into_group_with_same_name_is_rejected(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    source_group = _new_group(household_id, _unique("Kollisionsquelle"))
    target_group = _new_group(household_id, _unique("Kollisionsziel"))
    name = _unique("Doppelname")
    category = _new_category(household_id, source_group, name)
    _new_category(household_id, target_group, name)

    response = client.post(
        f"/v1/categories/{category}/move-preview",
        json={"target_group_id": target_group},
        headers=owner["headers"],
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "category_duplicate_name"


# ---------------------------------------------------------------------------
# Archivieren
# ---------------------------------------------------------------------------


def _seed_forward_references(
    household_id: str, category_id: str, payee_name: str
) -> dict[str, str]:
    """Je eine zukunftswirksame Referenz pro Tabelle.

    Rueckgabe sind die konkreten IDs: der Haushalt wird modulweit
    wiederverwendet, eine Pruefung ueber `limit 1` wuerde Zeilen anderer Tests
    treffen."""
    return {
        "payee": str(
            _db_scalar(
                "insert into payees (household_id, name, default_category_id) "
                "values (%(hh)s, %(name)s, %(c)s) returning id",
                {"hh": household_id, "name": payee_name, "c": category_id},
            )
        ),
        # Seit 0014 ein Vertrag statt der frueheren recurring_rule und
        # subscription - beide Toepfe sind in `contracts` aufgegangen.
        "contract": str(
            _db_scalar(
                "insert into contracts (household_id, name, category_id, amount_minor, "
                "next_due_date) "
                "values (%(hh)s, %(n)s, %(c)s, 1000, date '2026-09-01') returning id",
                {"hh": household_id, "n": _unique("Vertrag"), "c": category_id},
            )
        ),
        "goal": str(
            _db_scalar(
                "insert into savings_goals (household_id, name, target_amount_minor, "
                "category_id) values (%(hh)s, %(n)s, 100000, %(c)s) returning id",
                {"hh": household_id, "n": _unique("Ziel"), "c": category_id},
            )
        ),
        "rule": str(
            _db_scalar(
                "insert into classification_rules (household_id, name, match_payee_raw, "
                "action_category_id) values (%(hh)s, %(n)s, %(p)s, %(c)s) returning id",
                {"hh": household_id, "n": _unique("Regel"), "p": payee_name, "c": category_id},
            )
        ),
    }


def test_archive_with_replacement_repoints_forward_references(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Ersatzgruppe"))
    old = _new_category(household_id, group, _unique("Alt"))
    new = _new_category(household_id, group, _unique("Neu"))

    merchant = _unique("Haendler")
    seeded = _seed_forward_references(household_id, old, _unique("Zahlungsempfaenger"))
    # Zwei identische einfache Haendlerregeln - eine fuer die alte, eine fuer
    # die neue Kategorie. Ein blindes Umhaengen wuerde den partiellen
    # Unique-Index verletzen.
    duplicate_source_rule = str(
        _db_scalar(
            "insert into classification_rules (household_id, name, match_payee_raw, "
            "action_category_id) values (%(hh)s, %(n)s, %(p)s, %(c)s) returning id",
            {"hh": household_id, "n": _unique("Regel-alt"), "p": merchant, "c": old},
        )
    )
    duplicate_target_rule = str(
        _db_scalar(
            "insert into classification_rules (household_id, name, match_payee_raw, "
            "action_category_id) values (%(hh)s, %(n)s, %(p)s, %(c)s) returning id",
            {"hh": household_id, "n": _unique("Regel-neu"), "p": merchant, "c": new},
        )
    )

    _booking(owner, -4200, old, "2026-02-11")
    _allocate(owner, 2026, 2, [{"category_id": old, "planned_minor": 20000}])
    before = _sums(household_id)

    preview = client.post(
        f"/v1/categories/{old}/archive-preview", headers=owner["headers"]
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["requires_resolution"] is True
    assert body["forward"]["payee_defaults"] == 1
    assert body["forward"]["contracts"] == 1
    assert body["forward"]["savings_goals"] == 1
    assert body["forward"]["classification_rules"] == 2
    assert body["historical"]["transaction_splits"] == 1
    assert body["historical"]["budget_allocations"] == 1

    applied = client.post(
        f"/v1/categories/{old}/archive",
        json={"operation_id": body["operation_id"], "replacement_category_id": new},
        headers=owner["headers"],
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["is_archived"] is True

    for sql, key in (
        ("select default_category_id from payees where id = %(i)s", "payee"),
        ("select category_id from contracts where id = %(i)s", "contract"),
        ("select category_id from savings_goals where id = %(i)s", "goal"),
        ("select action_category_id from classification_rules where id = %(i)s", "rule"),
    ):
        assert str(_db_scalar(sql, {"i": seeded[key]})) == new, key
    # Die redundante Quellregel bleibt erhalten, wird aber deaktiviert und
    # zeigt bewusst weiter auf die archivierte Kategorie.
    duplicate = _db_row(
        "select is_active, action_category_id from classification_rules where id = %(r)s",
        {"r": duplicate_source_rule},
    )
    assert duplicate[0] is False
    assert str(duplicate[1]) == old
    target_rule = _db_row(
        "select is_active, action_category_id from classification_rules where id = %(r)s",
        {"r": duplicate_target_rule},
    )
    assert target_rule[0] is True
    assert str(target_rule[1]) == new

    # Historie bleibt unangetastet - Split und Budgetzeile zeigen weiter auf
    # die archivierte Kategorie, die Summen sind identisch.
    assert (
        int(
            _db_scalar(
                "select count(*) from transaction_splits where household_id = %(hh)s "
                "and category_id = %(c)s",
                {"hh": household_id, "c": old},
            )
        )
        == 1
    )
    assert _sums(household_id) == before


def test_archive_without_replacement_deactivates_rules_and_keeps_history(
    owner: dict[str, Any],
) -> None:
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Loesegruppe"))
    category = _new_category(household_id, group, _unique("Aufzuloesen"))
    seeded = _seed_forward_references(household_id, category, _unique("Empfaenger"))

    _booking(owner, -3300, category, "2026-06-02")
    _allocate(owner, 2026, 6, [{"category_id": category, "planned_minor": 15000}])
    _db_exec(
        "insert into budget_rollovers (household_id, budget_id, category_id, amount_minor) "
        "values (%(hh)s, %(b)s, %(c)s, 400)",
        {"hh": household_id, "b": _budget_id(owner, 2026, 6), "c": category},
    )
    before = _sums(household_id)

    preview = client.post(
        f"/v1/categories/{category}/archive-preview", headers=owner["headers"]
    ).json()
    assert preview["historical"]["budget_rollovers"] == 1

    applied = client.post(
        f"/v1/categories/{category}/archive",
        json={"operation_id": preview["operation_id"]},
        headers=owner["headers"],
    )
    assert applied.status_code == 200, applied.text

    rule = _db_row(
        "select is_active, action_category_id from classification_rules where id = %(r)s",
        {"r": seeded["rule"]},
    )
    # Eine Regel ohne Ersatz darf nicht weiterlaufen (Plan: "Regeln ohne
    # Ersatz werden deaktiviert").
    assert rule[0] is False
    assert rule[1] is None
    for sql, key in (
        ("select default_category_id from payees where id = %(i)s", "payee"),
        ("select category_id from contracts where id = %(i)s", "contract"),
        ("select category_id from savings_goals where id = %(i)s", "goal"),
    ):
        assert _db_scalar(sql, {"i": seeded[key]}) is None, key
    # Historie: Split, Planwert und Uebertrag bleiben auf der archivierten
    # Kategorie stehen, alle Summen sind unveraendert.
    assert (
        _db_scalar(
            "select count(*) from budget_rollovers where household_id = %(hh)s "
            "and category_id = %(c)s",
            {"hh": household_id, "c": category},
        )
        == 1
    )
    assert _sums(household_id) == before


def test_group_archive_needs_confirmation_before_cascading(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Sammelgruppe"))
    first = _new_category(household_id, group, _unique("Erste"))
    second = _new_category(household_id, group, _unique("Zweite"))

    preview = client.post(
        f"/v1/categories/groups/{group}/archive-preview", headers=owner["headers"]
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["requires_resolution"] is True
    assert sorted(body["active_category_ids"]) == sorted([first, second])

    refused = client.post(
        f"/v1/categories/groups/{group}/archive",
        json={"operation_id": body["operation_id"]},
        headers=owner["headers"],
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "category_group_has_active_categories"

    confirmed = client.post(
        f"/v1/categories/groups/{group}/archive",
        json={"operation_id": body["operation_id"], "archive_categories": True},
        headers=owner["headers"],
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["is_archived"] is True
    assert (
        _db_scalar(
            "select count(*) from categories where group_id = %(g)s and not is_archived",
            {"g": group},
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Wiederherstellen
# ---------------------------------------------------------------------------


def _archive_category(owner: dict[str, Any], category_id: str) -> None:
    preview = client.post(
        f"/v1/categories/{category_id}/archive-preview", headers=owner["headers"]
    )
    assert preview.status_code == 200, preview.text
    applied = client.post(
        f"/v1/categories/{category_id}/archive",
        json={"operation_id": preview.json()["operation_id"]},
        headers=owner["headers"],
    )
    assert applied.status_code == 200, applied.text


def test_restore_requires_active_group_and_free_name(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Wiedergruppe"))
    haven = _new_group(household_id, _unique("Auffanggruppe"))
    first = _new_category(household_id, group, _unique("Wiederkategorie"))
    second = _new_category(household_id, group, _unique("Zweitkategorie"))
    _archive_category(owner, first)
    _archive_category(owner, second)

    group_preview = client.post(
        f"/v1/categories/groups/{group}/archive-preview", headers=owner["headers"]
    ).json()
    # Alle Kategorien sind bereits archiviert - die Gruppe braucht dafuer keine
    # zusaetzliche Bestaetigung.
    assert group_preview["requires_resolution"] is False
    archived_group = client.post(
        f"/v1/categories/groups/{group}/archive",
        json={"operation_id": group_preview["operation_id"]},
        headers=owner["headers"],
    )
    assert archived_group.status_code == 200, archived_group.text

    blocked = client.post(
        f"/v1/categories/{first}/restore", json={}, headers=owner["headers"]
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "category_group_inactive"

    # Variante 1: in eine aktive Zielgruppe wiederherstellen.
    relocated = client.post(
        f"/v1/categories/{first}/restore",
        json={"target_group_id": haven},
        headers=owner["headers"],
    )
    assert relocated.status_code == 200, relocated.text
    assert relocated.json()["group_id"] == haven
    assert relocated.json()["is_archived"] is False

    # Variante 2: die Gruppe mit wiederherstellen.
    restored = client.post(
        f"/v1/categories/{second}/restore",
        json={"restore_group": True},
        headers=owner["headers"],
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_archived"] is False
    assert (
        _db_scalar("select is_archived from category_groups where id = %(g)s", {"g": group})
        is False
    )


def test_group_restore_endpoint_reactivates_an_empty_group(owner: dict[str, Any]) -> None:
    group = _new_group(owner["household_id"], _unique("Leergruppe"))
    preview = client.post(
        f"/v1/categories/groups/{group}/archive-preview", headers=owner["headers"]
    ).json()
    assert preview["active_category_ids"] == []
    assert (
        client.post(
            f"/v1/categories/groups/{group}/archive",
            json={"operation_id": preview["operation_id"]},
            headers=owner["headers"],
        ).status_code
        == 200
    )

    restored = client.post(
        f"/v1/categories/groups/{group}/restore", headers=owner["headers"]
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_archived"] is False


def test_restore_reports_name_collision_until_renamed(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Namensgruppe"))
    name = _unique("Belegter Name")
    original = _new_category(household_id, group, name)
    _archive_category(owner, original)
    # Der partielle Unique-Index gilt nur fuer aktive Zeilen: derselbe Name
    # darf nach der Archivierung neu vergeben werden.
    _new_category(household_id, group, name)

    blocked = client.post(
        f"/v1/categories/{original}/restore", json={}, headers=owner["headers"]
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "category_duplicate_name"

    renamed = client.post(
        f"/v1/categories/{original}/restore",
        json={"new_name": f"{name} (alt)"},
        headers=owner["headers"],
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == f"{name} (alt)"
    assert renamed.json()["is_archived"] is False


# ---------------------------------------------------------------------------
# Veraltete Vorschau
# ---------------------------------------------------------------------------


def test_stale_and_unknown_previews_are_rejected(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Vorschaugruppe"))
    category = _new_category(household_id, group, _unique("Vorschaukategorie"))

    unknown = client.post(
        f"/v1/categories/{category}/archive",
        json={"operation_id": str(uuid.uuid4())},
        headers=owner["headers"],
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "category_operation_not_found"

    preview = client.post(
        f"/v1/categories/{category}/archive-preview", headers=owner["headers"]
    ).json()
    # Zwischenzeitliche Umbenennung: die Kategorie ist eine andere als die,
    # die der Nutzer in der Vorschau gesehen hat.
    _db_exec(
        "update categories set name = %(n)s where id = %(c)s",
        {"n": _unique("Zwischendurch"), "c": category},
    )
    stale = client.post(
        f"/v1/categories/{category}/archive",
        json={"operation_id": preview["operation_id"]},
        headers=owner["headers"],
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "category_preview_stale"

    fresh = client.post(
        f"/v1/categories/{category}/archive-preview", headers=owner["headers"]
    ).json()
    _db_exec(
        "update category_operations set expires_at = now() - interval '1 minute' "
        "where id = %(o)s",
        {"o": fresh["operation_id"]},
    )
    expired = client.post(
        f"/v1/categories/{category}/archive",
        json={"operation_id": fresh["operation_id"]},
        headers=owner["headers"],
    )
    assert expired.status_code == 409
    assert expired.json()["detail"] == "category_preview_stale"


# ---------------------------------------------------------------------------
# Zusammenfuehren
# ---------------------------------------------------------------------------


@pytest.fixture()
def merge_setup(owner: dict[str, Any]) -> dict[str, Any]:
    """Quelle und Ziel mit Buchungen, kollidierenden Budgetzeilen und
    Uebertraegen - der vollstaendige Merge-Fall aus dem Plan."""
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Mergegruppe"))
    source = _new_category(household_id, group, _unique("Quelle"))
    target = _new_category(household_id, group, _unique("Ziel"))

    # Eine Buchung mit ZWEI Splits auf der Quelle: sie darf trotzdem nur einen
    # einzigen Versionssprung bekommen.
    split_transaction = _booking(owner, -1000, source, "2026-07-03")
    _db_exec(
        "update transaction_splits set amount_minor = -500 where transaction_id = %(t)s",
        {"t": split_transaction},
    )
    _db_exec(
        "insert into transaction_splits (household_id, transaction_id, category_id, "
        "amount_minor) values (%(hh)s, %(t)s, %(c)s, -500)",
        {"hh": household_id, "t": split_transaction, "c": source},
    )
    single_transaction = _booking(owner, -2000, source, "2026-07-04")
    untouched_transaction = _booking(owner, -3000, target, "2026-07-05")

    _allocate(
        owner,
        2026,
        7,
        [
            {"category_id": source, "planned_minor": 50000, "rollover_rule": "accumulate"},
            {"category_id": target, "planned_minor": 30000, "rollover_rule": "none"},
        ],
    )
    _allocate(owner, 2026, 10, [{"category_id": source, "planned_minor": 10000}])
    july = _budget_id(owner, 2026, 7)
    october = _budget_id(owner, 2026, 10)
    for budget_id, category_id, amount in (
        (july, source, 700),
        (july, target, 300),
        (october, source, 100),
    ):
        _db_exec(
            "insert into budget_rollovers (household_id, budget_id, category_id, "
            "amount_minor) values (%(hh)s, %(b)s, %(c)s, %(a)s)",
            {"hh": household_id, "b": budget_id, "c": category_id, "a": amount},
        )

    return {
        "group": group,
        "source": source,
        "target": target,
        "split_transaction": split_transaction,
        "single_transaction": single_transaction,
        "untouched_transaction": untouched_transaction,
    }


def _version(transaction_id: str) -> int:
    return int(_db_scalar("select version from transactions where id = %(t)s",
                          {"t": transaction_id}))


def test_merge_is_sum_neutral_and_bumps_each_transaction_once(
    owner: dict[str, Any], merge_setup: dict[str, Any]
) -> None:
    household_id = owner["household_id"]
    source = merge_setup["source"]
    target = merge_setup["target"]
    before = _sums(household_id)
    versions_before = {
        key: _version(merge_setup[key])
        for key in ("split_transaction", "single_transaction", "untouched_transaction")
    }

    preview = client.post(
        "/v1/categories/merge-preview",
        json={"source_category_id": source, "target_category_id": target},
        headers=owner["headers"],
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["references"]["transaction_splits"] == 3
    assert body["references"]["budget_allocations"] == 2
    assert body["references"]["budget_rollovers"] == 2
    # Drei Splits, aber nur zwei Buchungen.
    assert body["transactions_to_bump"] == 2
    kinds = {collision["kind"]: collision["count"] for collision in body["collisions"]}
    assert kinds["budget_allocation"] == 1
    assert kinds["rollover_rule"] == 1

    applied = client.post(
        "/v1/categories/merge",
        json={"operation_id": body["operation_id"], "request_id": _unique("merge")},
        headers=owner["headers"],
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["bumped_transactions"] == 2
    assert result["merged_budget_allocations"] == 1
    assert result["moved_references"]["transaction_splits"] == 3
    assert result["idempotent_replay"] is False

    # Genau ein Versionssprung je betroffener Buchung, auch bei zwei Splits.
    assert _version(merge_setup["split_transaction"]) == versions_before["split_transaction"] + 1
    assert (
        _version(merge_setup["single_transaction"]) == versions_before["single_transaction"] + 1
    )
    assert (
        _version(merge_setup["untouched_transaction"])
        == versions_before["untouched_transaction"]
    )

    # Summenneutral: Einnahmen, Ausgaben, Planung und Uebertraege identisch.
    assert _sums(household_id) == before
    assert (
        int(
            _db_scalar(
                "select planned_minor from budget_allocations where household_id = %(hh)s "
                "and category_id = %(c)s and budget_id = %(b)s",
                {"hh": household_id, "c": target, "b": _budget_id(owner, 2026, 7)},
            )
        )
        == 80000
    )
    assert (
        int(
            _db_scalar(
                "select amount_minor from budget_rollovers where household_id = %(hh)s "
                "and category_id = %(c)s and budget_id = %(b)s",
                {"hh": household_id, "c": target, "b": _budget_id(owner, 2026, 7)},
            )
        )
        == 1000
    )

    # Die Quelle ist ein Tombstone, der direkt auf das Ziel zeigt.
    tombstone = _db_row(
        "select is_archived, merged_into_category_id from categories where id = %(c)s",
        {"c": source},
    )
    assert tombstone[0] is True
    assert str(tombstone[1]) == target
    assert (
        _db_scalar(
            "select count(*) from transaction_splits where household_id = %(hh)s "
            "and category_id = %(c)s",
            {"hh": household_id, "c": source},
        )
        == 0
    )


def test_merge_replay_with_same_request_id_changes_nothing(
    owner: dict[str, Any], merge_setup: dict[str, Any]
) -> None:
    household_id = owner["household_id"]
    preview = client.post(
        "/v1/categories/merge-preview",
        json={
            "source_category_id": merge_setup["source"],
            "target_category_id": merge_setup["target"],
        },
        headers=owner["headers"],
    ).json()
    request_id = _unique("idempotent")
    first = client.post(
        "/v1/categories/merge",
        json={"operation_id": preview["operation_id"], "request_id": request_id},
        headers=owner["headers"],
    )
    assert first.status_code == 200, first.text
    after_first = _sums(household_id)
    version_after_first = _version(merge_setup["split_transaction"])

    second = client.post(
        "/v1/categories/merge",
        json={"operation_id": preview["operation_id"], "request_id": request_id},
        headers=owner["headers"],
    )
    assert second.status_code == 200, second.text
    assert second.json()["idempotent_replay"] is True
    assert second.json()["bumped_transactions"] == first.json()["bumped_transactions"]
    # Kein zweiter Versionssprung, keine doppelte Addition.
    assert _version(merge_setup["split_transaction"]) == version_after_first
    assert _sums(household_id) == after_first


def test_tombstone_is_not_restorable_but_is_canonicalized(
    owner: dict[str, Any], merge_setup: dict[str, Any]
) -> None:
    household_id = owner["household_id"]
    source = merge_setup["source"]
    target = merge_setup["target"]
    preview = client.post(
        "/v1/categories/merge-preview",
        json={"source_category_id": source, "target_category_id": target},
        headers=owner["headers"],
    ).json()
    assert (
        client.post(
            "/v1/categories/merge",
            json={"operation_id": preview["operation_id"], "request_id": _unique("merge")},
            headers=owner["headers"],
        ).status_code
        == 200
    )

    refused = client.post(
        f"/v1/categories/{source}/restore", json={}, headers=owner["headers"]
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "category_merged_not_restorable"

    # Verspaeteter Request mit der alten ID als Merge-Ziel: der Resolver
    # kanonisiert auf das endgueltige Ziel und flacht die Kette ab.
    late = _new_category(household_id, merge_setup["group"], _unique("Spaet"))
    late_preview = client.post(
        "/v1/categories/merge-preview",
        json={"source_category_id": late, "target_category_id": source},
        headers=owner["headers"],
    )
    assert late_preview.status_code == 200, late_preview.text
    assert late_preview.json()["target_category_id"] == target

    # Auch als Ersatzkategorie beim Archivieren wird die Tombstone-ID
    # aufgeloest, statt eine tote Referenz zu schreiben.
    orphan = _new_category(household_id, merge_setup["group"], _unique("Waise"))
    seeded = _seed_forward_references(household_id, orphan, _unique("Spaetempfaenger"))
    archive_preview = client.post(
        f"/v1/categories/{orphan}/archive-preview", headers=owner["headers"]
    ).json()
    archived = client.post(
        f"/v1/categories/{orphan}/archive",
        json={
            "operation_id": archive_preview["operation_id"],
            "replacement_category_id": source,
        },
        headers=owner["headers"],
    )
    assert archived.status_code == 200, archived.text
    assert (
        str(
            _db_scalar(
                "select action_category_id from classification_rules where id = %(r)s",
                {"r": seeded["rule"]},
            )
        )
        == target
    )


def test_merge_rejects_incompatible_types_and_self_merge(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Typgruppe"))
    expense = _new_category(household_id, group, _unique("Ausgabe"), "expense")
    income = _new_category(household_id, group, _unique("Einnahme"), "income")

    mismatch = client.post(
        "/v1/categories/merge-preview",
        json={"source_category_id": expense, "target_category_id": income},
        headers=owner["headers"],
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"] == "category_merge_type_mismatch"

    same = client.post(
        "/v1/categories/merge-preview",
        json={"source_category_id": expense, "target_category_id": expense},
        headers=owner["headers"],
    )
    assert same.status_code == 409
    assert same.json()["detail"] == "category_merge_same_category"


# ---------------------------------------------------------------------------
# Rollen und Haushaltsgrenzen
# ---------------------------------------------------------------------------


def test_merge_is_owner_only(owner: dict[str, Any]) -> None:
    household_id = owner["household_id"]
    group = _new_group(household_id, _unique("Rollengruppe"))
    source = _new_category(household_id, group, _unique("Rollenquelle"))
    target = _new_category(household_id, group, _unique("Rollenziel"))
    editor_headers = _invite_member(
        client, owner["headers"], household_id, "editor", "cat-editor"
    )

    # Die Vorschau darf ein Editor sehen ...
    preview = client.post(
        "/v1/categories/merge-preview",
        json={"source_category_id": source, "target_category_id": target},
        headers=editor_headers,
    )
    assert preview.status_code == 200, preview.text

    # ... das irreversible Anwenden nicht.
    applied = client.post(
        "/v1/categories/merge",
        json={"operation_id": preview.json()["operation_id"], "request_id": _unique("editor")},
        headers=editor_headers,
    )
    assert applied.status_code == 403
    assert applied.json()["detail"] == "household_owner_required"


def test_foreign_household_ids_are_not_found(owner: dict[str, Any]) -> None:
    other = client.post(
        "/v1/households",
        json={"name": "Fremder Haushalt"},
        headers=_auth_headers(owner["token"]),
    ).json()
    other_headers = {**_auth_headers(owner["token"]), "X-Household-Id": other["id"]}
    other_group = _new_group(str(other["id"]), _unique("Fremdgruppe"))
    other_category = _new_category(str(other["id"]), other_group, _unique("Fremdkategorie"))

    # Fremde Kategorie im eigenen Haushaltskontext: 404, keine Existenzinfo.
    assert (
        client.post(
            f"/v1/categories/{other_category}/archive-preview", headers=owner["headers"]
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/categories/groups/{other_group}/archive-preview", headers=owner["headers"]
        ).status_code
        == 404
    )
    merge = client.post(
        "/v1/categories/merge-preview",
        json={"source_category_id": other_category, "target_category_id": other_category},
        headers=owner["headers"],
    )
    assert merge.status_code == 404
    assert merge.json()["detail"] == "category_not_found"

    # Eine im Fremdhaushalt erzeugte Vorschau darf im eigenen nicht anwendbar
    # sein - sonst waere die operation_id ein Umweg um die Haushaltsgrenze.
    foreign_preview = client.post(
        f"/v1/categories/{other_category}/archive-preview", headers=other_headers
    )
    assert foreign_preview.status_code == 200, foreign_preview.text
    group = _new_group(owner["household_id"], _unique("Eigengruppe"))
    own_category = _new_category(owner["household_id"], group, _unique("Eigenkategorie"))
    leaked = client.post(
        f"/v1/categories/{own_category}/archive",
        json={"operation_id": foreign_preview.json()["operation_id"]},
        headers=owner["headers"],
    )
    assert leaked.status_code == 404
    assert leaked.json()["detail"] == "category_operation_not_found"
