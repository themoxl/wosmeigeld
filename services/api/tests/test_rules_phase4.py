import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.conftest import wait_for_import_processing
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

# Name aus dem deutschen Standardpaket v1
# (supabase/migrations/0001_schema.sql).
CAT_GROCERIES = "Lebensmittel"


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p4-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Phase-4-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: dict[str, str]) -> str:
    acc = client.post(
        "/v1/accounts",
        json={"name": "Hauptkonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return acc["id"]


def _lebensmittel_category_id(headers: dict[str, str]) -> str:
    groups = client.get("/v1/categories", headers=headers).json()
    for group in groups:
        for category in group["categories"]:
            if category["name"] == CAT_GROCERIES:
                return str(category["id"])
    raise AssertionError(f"Default-Kategorie '{CAT_GROCERIES}' nicht gefunden")


async def _fetch_audit_events(household_id: str, entity_id: str) -> list[dict[str, Any]]:
    """Liest die Audit-Eintraege einer Entitaet direkt aus der Datenbank - fuer
    audit_log gibt es bewusst keinen Lese-Endpunkt (die Session laeuft hier ohne
    SET ROLE authenticated, also als postgres mit BYPASSRLS)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        res = await session.execute(
            text(
                "select action, actor_user_id, entity_type, before, after "
                "from audit_log "
                "where household_id = :household_id and entity_id = :entity_id "
                "order by created_at asc"
            ),
            {"household_id": household_id, "entity_id": entity_id},
        )
        return [dict(row._mapping) for row in res]


def _rules_for_payee(headers: dict[str, str], payee: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = client.get("/v1/rules", headers=headers).json()
    return [r for r in rules if r["match_payee_raw"] == payee]


def test_rules_crud_and_matching(household_headers: dict[str, str], account_id: str) -> None:
    r = client.post(
        "/v1/rules",
        json={
            "name": "REWE Supermarkt Regel",
            "priority": 10,
            "match_payee_raw": "REWE",
            "action_set_transfer": False,
            "action_note": "Automatisch kategorisiert",
        },
        headers=household_headers,
    )
    assert r.status_code == 201
    rule_data = r.json()
    assert rule_data["name"] == "REWE Supermarkt Regel"

    res_list = client.get("/v1/rules", headers=household_headers)
    assert res_list.status_code == 200
    assert len(res_list.json()) >= 1

    del_res = client.delete(f"/v1/rules/{rule_data['id']}", headers=household_headers)
    assert del_res.status_code == 204


def test_recurring_and_subscriptions(household_headers: dict[str, str], account_id: str) -> None:
    r_rec = client.post(
        "/v1/recurring",
        json={
            "account_id": account_id,
            "expected_amount_minor": -4990,
            "frequency": "monthly",
            "next_due_date": "2026-08-15",
            "note": "Fitnessstudio",
        },
        headers=household_headers,
    )
    assert r_rec.status_code == 201
    assert r_rec.json()["expected_amount_minor"] == -4990

    rec_list = client.get("/v1/recurring", headers=household_headers)
    assert rec_list.status_code == 200
    assert len(rec_list.json()) >= 1

    r_sub = client.post(
        "/v1/subscriptions",
        json={
            "provider": "Netflix",
            "monthly_cost_minor": 1799,
            "annual_cost_minor": 21588,
            "is_active": True,
        },
        headers=household_headers,
    )
    assert r_sub.status_code == 201
    assert r_sub.json()["provider"] == "Netflix"

    sub_list = client.get("/v1/subscriptions", headers=household_headers)
    assert sub_list.status_code == 200
    assert len(sub_list.json()) >= 1


def test_the_legacy_subscription_costs_stay_positive(
    household_headers: dict[str, str],
) -> None:
    """Regressionstest: die Kompatibilitaetsschicht drehte das Vorzeichen um.

    `monthly_cost_minor` und `annual_cost_minor` sind KOSTEN - der Altpfad
    schrieb die Nutzlast unveraendert zurueck. Mit dem Vorzeichenwechsel aus
    `RecurringRule.expected_amount_minor` (das der Buchungskonvention folgt)
    zeigte eine ausgelieferte Mobile-Version alle Abos negativ und summierte
    eine negative Monatssumme.
    """
    created = client.post(
        "/v1/subscriptions",
        json={
            "provider": "Netflix",
            "plan_name": "Standard",
            "monthly_cost_minor": 1799,
            "annual_cost_minor": 21588,
        },
        headers=household_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["monthly_cost_minor"] == 1799
    assert body["annual_cost_minor"] == 21588
    assert body["is_active"] is True

    [listed] = [
        item
        for item in client.get("/v1/subscriptions", headers=household_headers).json()
        if item["id"] == body["id"]
    ]
    assert listed["monthly_cost_minor"] == 1799


def test_an_inactive_legacy_rule_stays_visible(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Regressionstest: `is_active=false` legte den Vertrag als 'ended' an.

    GET /v1/recurring blendet 'ended' aus - der gerade erzeugte Eintrag war
    damit sofort unsichtbar, und da der Altpfad weder PATCH noch DELETE kennt,
    dauerhaft unerreichbar. Fachlich richtig ist 'paused': pausiert ruht, wird
    aber weiter gefuehrt.
    """
    created = client.post(
        "/v1/recurring",
        json={
            "account_id": account_id,
            "expected_amount_minor": -4990,
            "frequency": "monthly",
            "next_due_date": "2026-09-15",
            "note": "Fitnessstudio pausiert",
            "is_active": False,
        },
        headers=household_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_active"] is False

    listed = client.get("/v1/recurring", headers=household_headers).json()
    [found] = [item for item in listed if item["id"] == created.json()["id"]]
    assert found["is_active"] is False
    assert found["expected_amount_minor"] == -4990


def test_contract_review_separates_amounts_and_saves_exact_contract(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Zwei Betragsniveaus desselben Empfaengers bleiben zwei Vertraege.

    Der Fall pruefte diese Aussage seit Phase 4 ueber die HTTP-Schicht und tut
    das weiter - nur gegen die Erkennung v2: `recurring_suggestions` ist
    dauerhaft leer, an seine Stelle tritt `contract_suggestions`.

    Zwei Aenderungen sind bedeutungstragend und deshalb hier festgehalten:
    der Empfaenger ist jetzt ein benannter Vertragspartner statt "PayPal Europe"
    (ein Zahlungsdienstleister ohne Vertragsschluessel sagt nicht, WESSEN
    Vertrag bezahlt wurde, und erzeugt nach Plan-Abschnitt 6.5 bewusst keinen
    Vorschlag mehr - siehe test_recurring_detection.py), und `amount_minor`
    ist wie in `contracts` positiv mit getrennter `direction`, waehrend das
    abgeloeste `expected_amount_minor` negativ war.
    """
    created_ids: dict[int, str] = {}
    series = (
        (-2500, ("2026-05-02", "2026-06-02", "2026-07-02")),
        (-10300, ("2026-05-14", "2026-06-14", "2026-07-14")),
    )
    for amount, booked_dates in series:
        for booked_date in booked_dates:
            response = client.post(
                "/v1/transactions",
                json={
                    "account_id": account_id,
                    "booked_date": booked_date,
                    "amount_minor": amount,
                    "payee_label": "Nordlicht Media GmbH",
                },
                headers=household_headers,
            )
            assert response.status_code == 201
            created_ids[amount] = response.json()["id"]

    inbox = client.get("/v1/review", headers=household_headers).json()
    assert inbox["unclassified_open_count"] == 6
    assert inbox["unclassified_total_count"] == 6
    # Der Altbestand ist abgeloest, nicht nur unbenutzt.
    assert inbox["recurring_suggestions"] == []
    candidates = [
        item
        for item in inbox["contract_suggestions"]
        if item["payee_label"] == "Nordlicht Media GmbH"
    ]
    assert len(candidates) == 2
    assert {item["amount_minor"] for item in candidates} == {2500, 10300}
    assert {item["direction"] for item in candidates} == {"expense"}
    # Beide Reihen laufen parallel, sind also keine Preisstufen EINES Vertrags.
    assert all(len(item["amount_steps"]) == 1 for item in candidates)
    # Kein Mittelwert: der Anker ist ein Betrag, den der Nutzer wirklich zahlte.
    assert all("avg_amount_minor" not in item for item in candidates)

    paged = client.get(
        "/v1/review?unclassified_limit=2&unclassified_offset=2&unclassified_search=Nordlicht",
        headers=household_headers,
    ).json()
    assert paged["unclassified_open_count"] == 6
    assert paged["unclassified_total_count"] == 6
    assert paged["unclassified_offset"] == 2
    assert len(paged["unclassified"]) == 2

    selected = next(item for item in candidates if item["amount_minor"] == 2500)
    assert selected["source_transaction_id"] == created_ids[-2500]
    detail_response = client.get(
        f"/v1/review/contract-suggestions/{selected['source_transaction_id']}",
        headers=household_headers,
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["suggestion"] == selected
    # Die Buchungen tragen weiter ihr echtes (negatives) Vorzeichen.
    assert [item["amount_minor"] for item in detail["transactions"]] == [-2500] * 3
    assert [item["booked_date"] for item in detail["transactions"]] == [
        "2026-07-02",
        "2026-06-02",
        "2026-05-02",
    ]
    assert all(item["account_name"] == "Hauptkonto" for item in detail["transactions"])
    assert all(item["amount_step_index"] == 0 for item in detail["transactions"])

    category_id = _lebensmittel_category_id(household_headers)
    accepted = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_suggestion",
            "source_transaction_id": selected["source_transaction_id"],
            "category_id": category_id,
        },
        headers=household_headers,
    )
    assert accepted.status_code == 200

    contracts = client.get("/v1/contracts", headers=household_headers).json()
    [saved] = [item for item in contracts if item["amount_minor"] == 2500]
    assert saved["payee_id"] is not None
    assert saved["account_id"] == account_id
    assert saved["category_id"] == category_id
    assert saved["direction"] == "expense"
    assert saved["frequency"] == "monthly"
    assert saved["next_due_date"] == "2026-08-02"
    assert saved["tolerance_amount_minor"] == 0
    # Bestaetigt heisst bestaetigt - nicht wieder als Vorschlag behandeln.
    assert saved["source"] == "confirmed"

    inbox_after = client.get("/v1/review", headers=household_headers).json()
    remaining = [
        item
        for item in inbox_after["contract_suggestions"]
        if item["payee_label"] == "Nordlicht Media GmbH"
    ]
    assert {item["amount_minor"] for item in remaining} == {10300}


def test_review_inbox_flow(household_headers: dict[str, str], account_id: str) -> None:
    tx_res = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-20",
            "amount_minor": -1500,
            "payee_label": "Unbekannter Haendler",
            "note": "Test-Einkauf",
        },
        headers=household_headers,
    )
    assert tx_res.status_code == 201
    tx_id = tx_res.json()["id"]

    inv_res = client.get("/v1/review", headers=household_headers)
    assert inv_res.status_code == 200
    data = inv_res.json()
    assert data["total_count"] >= 1
    assert len(data["unclassified"]) >= 1

    acc_res = client.post(
        "/v1/review/accept",
        json={
            "type": "unclassified",
            "transaction_id": tx_id,
            "create_rule": True,
            "rule_name": "Autoregel fuer Unbekannt",
        },
        headers=household_headers,
    )
    assert acc_res.status_code == 200
    assert acc_res.json()["status"] == "accepted"


def test_review_duplicate_accept_skips_row(
    household_headers: dict[str, str], account_id: str
) -> None:
    csv_content = (
        b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n2026-02-02;Soll;-50,00;REWE Markt\n"
    )
    r1 = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("import1.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()
    wait_for_import_processing()
    client.post(f"/v1/imports/{r1['id']}/commit", json={}, headers=household_headers)

    # Zweiter Import mit leicht anderem Empfaenger-Text (anderer Fingerabdruck) aber
    # gleichem Betrag/Datum -> "duplicate_possible" statt "duplicate_exact".
    csv_content2 = (
        b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n2026-02-02;Soll;-50,00;REWE Markt Filiale\n"
    )
    r2 = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("import2.csv", csv_content2, "text/csv")},
        headers=household_headers,
    ).json()
    wait_for_import_processing()
    rows = client.get(f"/v1/imports/{r2['id']}/rows", headers=household_headers).json()
    dup_row = next(r for r in rows if r["status"] == "duplicate_possible")

    inbox = client.get("/v1/review", headers=household_headers).json()
    assert any(d["import_row_id"] == dup_row["id"] for d in inbox["possible_duplicates"])

    acc_res = client.post(
        "/v1/review/accept",
        json={"type": "possible_duplicate", "import_row_id": dup_row["id"]},
        headers=household_headers,
    )
    assert acc_res.status_code == 200

    rows_after = client.get(f"/v1/imports/{r2['id']}/rows", headers=household_headers).json()
    assert next(r for r in rows_after if r["id"] == dup_row["id"])["status"] == "skipped"

    inbox_after = client.get("/v1/review", headers=household_headers).json()
    assert not any(d["import_row_id"] == dup_row["id"] for d in inbox_after["possible_duplicates"])


def test_review_duplicate_correct_commits_with_category(
    household_headers: dict[str, str], account_id: str
) -> None:
    csv_content = b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n2026-03-02;Soll;-30,00;Kiosk\n"
    r1 = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("import1.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()
    wait_for_import_processing()
    client.post(f"/v1/imports/{r1['id']}/commit", json={}, headers=household_headers)

    csv_content2 = (
        b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n"
        b"2026-03-02;Soll;-30,00;Kiosk am Bahnhof\n"
    )
    r2 = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("import2.csv", csv_content2, "text/csv")},
        headers=household_headers,
    ).json()
    wait_for_import_processing()
    rows = client.get(f"/v1/imports/{r2['id']}/rows", headers=household_headers).json()
    dup_row = next(r for r in rows if r["status"] == "duplicate_possible")

    category_id = _lebensmittel_category_id(household_headers)
    corr_res = client.post(
        "/v1/review/correct",
        json={
            "type": "possible_duplicate",
            "import_row_id": dup_row["id"],
            "category_id": category_id,
        },
        headers=household_headers,
    )
    assert corr_res.status_code == 200
    assert corr_res.json()["status"] == "corrected"

    rows_after = client.get(f"/v1/imports/{r2['id']}/rows", headers=household_headers).json()
    committed_row = next(r for r in rows_after if r["id"] == dup_row["id"])
    assert committed_row["status"] == "committed"

    txs = client.get(f"/v1/transactions?account_id={account_id}", headers=household_headers).json()
    committed_tx = next(t for t in txs if t["id"] == committed_row["committed_transaction_id"])
    assert committed_tx["category_id"] == category_id


def test_review_transfer_candidate_accept_sets_is_transfer_on_both_sides(
    household_headers: dict[str, str], account_id: str
) -> None:
    other_acc = client.post(
        "/v1/accounts",
        json={"name": "Sparkonto", "account_type": "savings"},
        headers=household_headers,
    ).json()["id"]

    # Bereits gebuchte Gegenbuchung auf dem anderen Konto anlegen.
    other_tx = client.post(
        "/v1/transactions",
        json={
            "account_id": other_acc,
            "booked_date": "2026-04-05",
            "amount_minor": 20000,
            "payee_label": "Uebertrag",
        },
        headers=household_headers,
    ).json()

    csv_content = (
        b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n2026-04-05;Soll;-200,00;Uebertrag\n"
    )
    run = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("transfer.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()
    wait_for_import_processing()
    rows = client.get(f"/v1/imports/{run['id']}/rows", headers=household_headers).json()
    tr_row = next(r for r in rows if r["status"] == "transfer_candidate")

    inbox = client.get("/v1/review", headers=household_headers).json()
    assert any(t["import_row_id"] == tr_row["id"] for t in inbox["transfer_candidates"])

    acc_res = client.post(
        "/v1/review/accept",
        json={"type": "transfer_candidate", "import_row_id": tr_row["id"]},
        headers=household_headers,
    )
    assert acc_res.status_code == 200

    rows_after = client.get(f"/v1/imports/{run['id']}/rows", headers=household_headers).json()
    committed_row = next(r for r in rows_after if r["id"] == tr_row["id"])
    assert committed_row["status"] == "committed"

    txs = client.get(f"/v1/transactions?account_id={account_id}", headers=household_headers).json()
    new_tx = next(t for t in txs if t["id"] == committed_row["committed_transaction_id"])
    assert new_tx["is_transfer"] is True

    other_tx_after = client.get(
        f"/v1/transactions?account_id={other_acc}", headers=household_headers
    ).json()
    assert next(t for t in other_tx_after if t["id"] == other_tx["id"])["is_transfer"] is True


def test_batch_commit_keeps_transfer_candidates_marked_as_transfers(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Auch der Stapelcommit muss einen Transfer als Transfer uebernehmen.

    Vorher zog nur die Review-Inbox nach: wer den Lauf im Ganzen uebernahm,
    bekam die Umbuchung als gewoehnliche Buchung. Die Ausgleichszahlung ans
    Kreditkartenkonto stand danach als Ausgabe im Girokonto UND die
    Kartenumsaetze einzeln daneben - in jeder Auswertung doppelt."""
    other_acc = client.post(
        "/v1/accounts",
        json={"name": "Kreditkarte", "account_type": "credit_card"},
        headers=household_headers,
    ).json()["id"]

    other_tx = client.post(
        "/v1/transactions",
        json={
            "account_id": other_acc,
            "booked_date": "2026-05-05",
            "amount_minor": 30000,
            "payee_label": "Abrechnung",
        },
        headers=household_headers,
    ).json()

    csv_content = (
        b"Datum;Soll/Haben;Betrag_EUR;Empfaenger/Zweck\n2026-05-05;Soll;-300,00;Abrechnung\n"
    )
    run = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("kk-ausgleich.csv", csv_content, "text/csv")},
        headers=household_headers,
    ).json()
    wait_for_import_processing()

    rows = client.get(f"/v1/imports/{run['id']}/rows", headers=household_headers).json()
    tr_row = next(r for r in rows if r["status"] == "transfer_candidate")

    # Stapelcommit ohne Zeilenauswahl - der Weg, den "Alles uebernehmen" geht.
    commit = client.post(f"/v1/imports/{run['id']}/commit", json={}, headers=household_headers)
    assert commit.status_code == 200

    rows_after = client.get(f"/v1/imports/{run['id']}/rows", headers=household_headers).json()
    committed_row = next(r for r in rows_after if r["id"] == tr_row["id"])
    assert committed_row["status"] == "committed"

    txs = client.get(f"/v1/transactions?account_id={account_id}", headers=household_headers).json()
    new_tx = next(t for t in txs if t["id"] == committed_row["committed_transaction_id"])
    assert new_tx["is_transfer"] is True

    # Beide Seiten - sonst zaehlt die Gegenbuchung weiter als eigener Umsatz.
    other_after = client.get(
        f"/v1/transactions?account_id={other_acc}", headers=household_headers
    ).json()
    assert next(t for t in other_after if t["id"] == other_tx["id"])["is_transfer"] is True


def _correct_unclassified(
    headers: dict[str, str], transaction_id: str, category_id: str, payee: str
) -> int:
    response = client.post(
        "/v1/review/correct",
        json={
            "type": "unclassified",
            "transaction_id": transaction_id,
            "category_id": category_id,
            "payee_label": payee,
            "create_rule_always": True,
        },
        headers=headers,
    )
    return response.status_code


def test_correction_creates_exactly_one_rule(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Frueher legte /v1/review/correct zwei identische Regeln an (einmal in
    _resolve_unclassified, einmal im nachgelagerten Block)."""
    payee = f"Baeckerei {uuid.uuid4().hex[:8]}"
    tx_id = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-05-11",
            "amount_minor": -450,
            "payee_label": payee,
        },
        headers=household_headers,
    ).json()["id"]
    category_id = _lebensmittel_category_id(household_headers)

    assert _correct_unclassified(household_headers, tx_id, category_id, payee) == 200

    rules = _rules_for_payee(household_headers, payee)
    assert len(rules) == 1
    assert rules[0]["action_category_id"] == category_id
    # Prioritaet 0 wie bei manuell angelegten Regeln - eine automatisch
    # abgeleitete Regel darf ausdrueckliche Nutzerregeln nicht ueberstimmen.
    assert rules[0]["priority"] == 0
    assert rules[0]["version"] == 1


def test_review_rule_preview_and_apply_repeated_exact_amount(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der 250-EUR-Fall laeuft komplett ohne KI: erst vergleichen, dann nach
    ausdruecklicher Bestaetigung Regel anlegen und offene Treffer anwenden."""
    payee = f"Sparplan {uuid.uuid4().hex[:8]}"
    category_id = _lebensmittel_category_id(household_headers)
    tx_ids: list[str] = []
    for day, amount in ((1, -25000), (2, -25000), (3, -30000)):
        tx_ids.append(
            client.post(
                "/v1/transactions",
                json={
                    "account_id": account_id,
                    "booked_date": f"2026-06-{day:02d}",
                    "amount_minor": amount,
                    "payee_label": payee,
                    "note": "Monatlich sparen",
                },
                headers=household_headers,
            ).json()["id"]
        )

    preview = client.post(
        "/v1/review/rule-preview",
        json={
            "transaction_id": tx_ids[0],
            "category_id": category_id,
            "payee_pattern": payee,
            "match_payee": True,
            "match_exact_amount": True,
        },
        headers=household_headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["dry_run"] is True
    assert preview.json()["applied"] == 2

    corrected = client.post(
        "/v1/review/correct",
        json={
            "type": "unclassified",
            "transaction_id": tx_ids[0],
            "category_id": category_id,
            "payee_label": payee,
            "create_rule_always": True,
            "rule_match_payee": True,
            "rule_match_exact_amount": True,
            "apply_rule_to_existing": True,
        },
        headers=household_headers,
    )
    assert corrected.status_code == 200, corrected.text

    details = [
        client.get(f"/v1/transactions/{tx_id}", headers=household_headers).json()
        for tx_id in tx_ids
    ]
    assert details[0]["splits"][0]["category_id"] == category_id
    assert details[1]["splits"][0]["category_id"] == category_id
    assert details[2]["splits"][0]["category_id"] is None

    rules = _rules_for_payee(household_headers, payee)
    assert len(rules) == 1
    assert rules[0]["match_min_amount_minor"] == -25000
    assert rules[0]["match_max_amount_minor"] == -25000


def test_review_rule_preview_can_find_matches_before_category_selection(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der deterministische Abgleich ist der erste Schritt und braucht weder
    Zielkategorie noch Modellaufruf."""
    payee = f"Vorpruefung {uuid.uuid4().hex[:8]}"
    tx_ids = [
        client.post(
            "/v1/transactions",
            json={
                "account_id": account_id,
                "booked_date": f"2026-06-{day:02d}",
                "amount_minor": -1_250,
                "payee_label": payee,
            },
            headers=household_headers,
        ).json()["id"]
        for day in (8, 9)
    ]

    preview = client.post(
        "/v1/review/rule-preview",
        json={
            "transaction_id": tx_ids[0],
            "payee_pattern": payee,
            "match_payee": True,
            "match_exact_amount": True,
        },
        headers=household_headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["matched"] == 2
    assert len(preview.json()["items"]) == 2


def test_review_history_match_reuses_a_confirmed_category_without_ai(
    household_headers: dict[str, str], account_id: str
) -> None:
    payee = f"Bekannter Empfaenger {uuid.uuid4().hex[:8]}"
    category_id = _lebensmittel_category_id(household_headers)
    transaction_ids = [
        client.post(
            "/v1/transactions",
            json={
                "account_id": account_id,
                "booked_date": f"2026-07-{day:02d}",
                "amount_minor": -990,
                "payee_label": payee,
            },
            headers=household_headers,
        ).json()["id"]
        for day in (1, 2)
    ]
    assert (
        client.post(
            "/v1/review/correct",
            json={
                "type": "unclassified",
                "transaction_id": transaction_ids[0],
                "category_id": category_id,
            },
            headers=household_headers,
        ).status_code
        == 200
    )

    other_category_id = next(
        str(category["id"])
        for group in client.get("/v1/categories", headers=household_headers).json()
        for category in group["categories"]
        if str(category["id"]) != category_id
    )
    rule = client.post(
        "/v1/rules",
        json={
            "name": "Abweichende Regel",
            "match_payee_raw": payee,
            "action_category_id": other_category_id,
        },
        headers=household_headers,
    )
    assert rule.status_code == 201, rule.text

    response = client.get(
        f"/v1/review/history-match/{transaction_ids[1]}", headers=household_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["matched"] is True
    assert response.json()["category_id"] == category_id
    assert response.json()["evidence_count"] == 1
    assert response.json()["source"] == "history"

    batch = client.get("/v1/review/history-matches", headers=household_headers)
    assert batch.status_code == 200, batch.text
    by_transaction = {item["transaction_id"]: item for item in batch.json()["items"]}
    assert by_transaction[transaction_ids[1]]["category_id"] == category_id
    assert batch.json()["matched_count"] >= 1


def test_review_history_match_checks_active_rules_before_ai(
    household_headers: dict[str, str], account_id: str
) -> None:
    category_id = _lebensmittel_category_id(household_headers)
    transaction_id = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-03",
            "amount_minor": -1_500,
            "payee_label": "AMZN Mktp DE AB12C34D5",
        },
        headers=household_headers,
    ).json()["id"]
    rule = client.post(
        "/v1/rules",
        json={
            "name": "Regel fuer AMZN",
            "match_payee_raw": "AMZN",
            "action_category_id": category_id,
        },
        headers=household_headers,
    )
    assert rule.status_code == 201, rule.text

    response = client.get(
        f"/v1/review/history-match/{transaction_id}", headers=household_headers
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["matched"] is True
    assert result["category_id"] == category_id
    assert result["category_label"].endswith(" · Lebensmittel")
    assert result["evidence_count"] == 0
    assert result["source"] == "rule"
    assert result["rule_id"] == rule.json()["id"]
    assert result["rule_name"] == "Regel fuer AMZN"

    batch = client.get("/v1/review/history-matches", headers=household_headers)
    assert batch.status_code == 200, batch.text
    by_transaction = {item["transaction_id"]: item for item in batch.json()["items"]}
    assert by_transaction[transaction_id]["source"] == "rule"
    assert by_transaction[transaction_id]["rule_name"] == "Regel fuer AMZN"


@pytest.mark.asyncio
async def test_repeated_identical_correction_creates_no_second_rule(
    household_headers: dict[str, str], account_id: str
) -> None:
    payee = f"Kiosk {uuid.uuid4().hex[:8]}"
    category_id = _lebensmittel_category_id(household_headers)
    tx_ids = [
        client.post(
            "/v1/transactions",
            json={
                "account_id": account_id,
                "booked_date": "2026-05-12",
                "amount_minor": -700,
                "payee_label": payee,
            },
            headers=household_headers,
        ).json()["id"]
        for _ in range(2)
    ]

    for tx_id in tx_ids:
        assert _correct_unclassified(household_headers, tx_id, category_id, payee) == 200

    rules = _rules_for_payee(household_headers, payee)
    assert len(rules) == 1

    # Die abgewiesene zweite Anlage darf auch kein zweites Audit-Ereignis
    # hinterlassen.
    events = await _fetch_audit_events(household_headers["X-Household-Id"], rules[0]["id"])
    assert [e["action"] for e in events] == ["create"]

    # Dieselbe Regel manuell noch einmal anzulegen scheitert am Unique-Index -
    # und zwar mit 409 statt mit einem Serverfehler.
    conflict = client.post(
        "/v1/rules",
        json={"name": "Handregel", "match_payee_raw": payee, "action_category_id": category_id},
        headers=household_headers,
    )
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_rule_changes_are_audited_and_versioned(household_headers: dict[str, str]) -> None:
    household_id = household_headers["X-Household-Id"]
    created = client.post(
        "/v1/rules",
        json={"name": "Mietregel", "priority": 5, "match_purpose": "Miete"},
        headers=household_headers,
    ).json()
    assert created["version"] == 1

    patch_res = client.patch(
        f"/v1/rules/{created['id']}",
        json={"name": "Mietregel neu", "is_active": False},
        headers=household_headers,
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["name"] == "Mietregel neu"
    assert patched["is_active"] is False
    assert patched["version"] == 2
    # Nicht mitgeschickte Felder bleiben unveraendert.
    assert patched["match_purpose"] == "Miete"
    assert patched["priority"] == 5

    assert client.delete(f"/v1/rules/{created['id']}", headers=household_headers).status_code == 204

    events = await _fetch_audit_events(household_id, created["id"])
    assert [e["action"] for e in events] == ["create", "update", "delete"]
    assert {e["entity_type"] for e in events} == {"classification_rule"}
    assert all(e["actor_user_id"] is not None for e in events)

    assert events[0]["before"] is None
    assert events[0]["after"]["version"] == 1
    assert events[1]["before"]["name"] == "Mietregel"
    assert events[1]["before"]["version"] == 1
    assert events[1]["after"]["name"] == "Mietregel neu"
    assert events[1]["after"]["version"] == 2
    assert events[2]["before"]["name"] == "Mietregel neu"
    assert events[2]["after"] is None


@pytest.mark.asyncio
async def test_patch_rule_unknown_id_and_empty_payload(
    household_headers: dict[str, str]
) -> None:
    missing = client.patch(
        f"/v1/rules/{uuid.uuid4()}", json={"name": "egal"}, headers=household_headers
    )
    assert missing.status_code == 404

    created = client.post(
        "/v1/rules",
        json={"name": "Leerpatch-Regel", "match_purpose": "Strom"},
        headers=household_headers,
    ).json()
    unchanged = client.patch(
        f"/v1/rules/{created['id']}", json={}, headers=household_headers
    )
    assert unchanged.status_code == 200
    # Leerer Patch: keine neue Version und kein Audit-Ereignis.
    assert unchanged.json()["version"] == 1
    events = await _fetch_audit_events(household_headers["X-Household-Id"], created["id"])
    assert [e["action"] for e in events] == ["create"]
