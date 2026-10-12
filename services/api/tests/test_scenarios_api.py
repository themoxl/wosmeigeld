"""Integrationstests der gespeicherten Haushaltsszenarien gegen das lokale
Supabase - kein Mocking (Muster: `test_reserves_api.py`).

Der Rechenkern ist in `test_household_forecast_service.py` und
`test_household_forecast_reference_cases.py` centgenau geprueft und wird hier
NICHT noch einmal nachgerechnet. Geprueft wird der Weg dorthin: dass ein
gespeichertes Szenario dieselbe Rechnung ausloest wie die ungespeicherte
Simulation (Leitsatz 2), dass abgewaehlte Zeilen vor dem Rechenlauf
herausfallen, und dass die aus dem Fixkostenplan uebernommenen Szenarien
dieselbe Monatswirkung haben wie ihr Original.
"""

import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

TODAY = date.today()

# Die Uebernahme steht als letzter Abschnitt im Schema. Der Test fuehrt genau
# dieses SQL aus, statt es nachzubauen: eine nachgebaute Uebernahme wuerde nur
# sich selbst bestaetigen.
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "supabase" / "migrations" / "0001_schema.sql"
)
_ADOPTION_MARKER = "-- Uebernahme der Fixkostenszenarien"


def _headers(name: str) -> dict[str, str]:
    token = _signup(f"scenario-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    household = client.post(
        "/v1/households", json={"name": name}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def household_headers() -> dict[str, str]:
    return _headers("Szenarien")


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _create_account(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Girokonto",
        "account_type": "checking",
        "opening_balance_minor": 0,
    }
    body.update(overrides)
    response = client.post("/v1/accounts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _create_contract(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    due = TODAY + timedelta(days=5)
    body: dict[str, Any] = {
        "name": "Miete",
        "amount_minor": 100_000,
        "frequency": "monthly",
        "next_due_date": due.isoformat(),
        "due_day": due.day,
    }
    body.update(overrides)
    response = client.post("/v1/contracts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _income_change(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "change_type": "income_add",
        "label": "Person B, Teilzeit 25 h",
        "amount_mode": "absolute",
        "amount_minor": 145_000,
        "frequency": "monthly",
        "due_day": 15,
        "income_kind": "salary",
        "starts_on": TODAY.isoformat(),
    }
    body.update(overrides)
    return body


def _expense_change(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "change_type": "expense_add",
        "label": "Kinderbetreuung",
        "amount_mode": "absolute",
        "amount_minor": -28_000,
        "frequency": "monthly",
        "due_day": 1,
        "starts_on": TODAY.isoformat(),
    }
    body.update(overrides)
    return body


def _create_scenario(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Person B arbeitet wieder"}
    body.update(overrides)
    response = client.post("/v1/scenarios", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _set_horizon(headers: dict[str, str], months: int) -> None:
    """Kurzer Haushaltshorizont - der Vergleich laedt sonst je Szenario ein
    Jahr Tagesreihe, ohne dass der Test davon etwas haette."""
    response = client.put(
        "/v1/forecast/settings",
        json={
            "safety_buffer_minor": 0,
            "variable_basis": "max",
            "variable_lookback_months": 6,
            "horizon_months": months,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_read_update_delete(household_headers: dict[str, str]) -> None:
    created = _create_scenario(
        household_headers,
        description="Teilzeit ab Maerz",
        horizon_months=12,
        color_key="teal",
        changes=[_income_change(), _expense_change()],
    )
    assert created["status"] == "draft"
    assert created["origin"] == "manual"
    assert created["version"] == 1
    assert created["change_count"] == 2
    # Ohne eigene Angabe zaehlt die Reihenfolge im Request.
    assert [item["sort_order"] for item in created["changes"]] == [0, 1]
    assert [item["label"] for item in created["changes"]] == [
        "Person B, Teilzeit 25 h",
        "Kinderbetreuung",
    ]
    assert all(item["include_in_forecast"] for item in created["changes"])

    fetched = client.get(f"/v1/scenarios/{created['id']}", headers=household_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == created
    assert fetched.headers["ETag"] == '"1"'

    listed = client.get("/v1/scenarios", headers=household_headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [created["id"]]
    # Die Liste traegt die Kopfdaten, nicht die Zeilen.
    assert "changes" not in listed.json()[0]
    assert listed.json()[0]["change_count"] == 2

    patched = client.patch(
        f"/v1/scenarios/{created['id']}",
        json={"name": "Person B arbeitet wieder (25 h)", "status": "active"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Person B arbeitet wieder (25 h)"
    assert patched.json()["status"] == "active"
    assert patched.json()["version"] == 2
    assert patched.headers["ETag"] == '"2"'
    # Die Zeilen bleiben von einer Kopfaenderung unberuehrt.
    assert patched.json()["change_count"] == 2

    deleted = client.delete(f"/v1/scenarios/{created['id']}", headers=household_headers)
    assert deleted.status_code == 204, deleted.text
    assert (
        client.get(f"/v1/scenarios/{created['id']}", headers=household_headers).status_code
        == 404
    )


def test_patch_requires_if_match_and_rejects_a_stale_version(
    household_headers: dict[str, str],
) -> None:
    """Gleiche Regel wie beim Vertrag: fehlender Header 428, veralteter
    Stand 409 - mit dem aktuellen ETag in der Antwort, damit der Client ohne
    zweiten Request neu aufsetzen kann."""
    created = _create_scenario(household_headers)

    ohne_header = client.patch(
        f"/v1/scenarios/{created['id']}", json={"name": "Neu"}, headers=household_headers
    )
    assert ohne_header.status_code == 428, ohne_header.text

    veraltet = client.patch(
        f"/v1/scenarios/{created['id']}",
        json={"name": "Neu"},
        headers={**household_headers, "If-Match": '"99"'},
    )
    assert veraltet.status_code == 409, veraltet.text
    assert veraltet.headers["ETag"] == '"1"'

    # Der abgelehnte Versuch hat nichts geschrieben.
    unveraendert = client.get(f"/v1/scenarios/{created['id']}", headers=household_headers)
    assert unveraendert.json()["name"] == "Person B arbeitet wieder"
    assert unveraendert.json()["version"] == 1


def test_put_changes_replaces_the_whole_list(household_headers: dict[str, str]) -> None:
    created = _create_scenario(
        household_headers, changes=[_income_change(), _expense_change()]
    )
    alte_ids = {item["id"] for item in created["changes"]}

    ersetzt = client.put(
        f"/v1/scenarios/{created['id']}/changes",
        json={
            "changes": [
                _expense_change(label="Nur noch Betreuung", note="Ab Kita-Start"),
            ]
        },
        headers=household_headers,
    )
    assert ersetzt.status_code == 200, ersetzt.text
    assert ersetzt.json()["change_count"] == 1
    assert [item["label"] for item in ersetzt.json()["changes"]] == ["Nur noch Betreuung"]
    assert ersetzt.json()["changes"][0]["note"] == "Ab Kita-Start"
    # Kein Zusammenfuehren ueber id: die alten Zeilen sind weg, nicht ueberschrieben.
    assert alte_ids.isdisjoint({item["id"] for item in ersetzt.json()["changes"]})
    # Die Version haengt am Plan, nicht nur an seiner Ueberschrift.
    assert ersetzt.json()["version"] == 2

    geleert = client.put(
        f"/v1/scenarios/{created['id']}/changes", json={"changes": []}, headers=household_headers
    )
    assert geleert.status_code == 200, geleert.text
    assert geleert.json()["change_count"] == 0
    assert geleert.json()["changes"] == []


def test_duplicate_copies_the_rows_but_never_the_origin(
    household_headers: dict[str, str],
) -> None:
    created = _create_scenario(
        household_headers,
        status="active",
        horizon_months=18,
        color_key="amber",
        changes=[_income_change(), _expense_change()],
    )
    copy = client.post(
        f"/v1/scenarios/{created['id']}/duplicate", headers=household_headers
    )
    assert copy.status_code == 201, copy.text
    body = copy.json()
    assert body["id"] != created["id"]
    assert body["name"] == "Person B arbeitet wieder (Kopie)"
    # Immer Entwurf und immer 'manual': eine zweite aktive Variante desselben
    # Plans waere eine Aussage, die niemand getroffen hat.
    assert body["status"] == "draft"
    assert body["origin"] == "manual"
    assert body["horizon_months"] == 18
    assert body["color_key"] == "amber"
    assert body["change_count"] == 2
    assert [item["label"] for item in body["changes"]] == [
        item["label"] for item in created["changes"]
    ]
    assert {item["id"] for item in body["changes"]}.isdisjoint(
        {item["id"] for item in created["changes"]}
    )

    # Das Original bleibt unberuehrt.
    original = client.get(f"/v1/scenarios/{created['id']}", headers=household_headers)
    assert original.json()["status"] == "active"
    assert original.json()["change_count"] == 2


def test_person_assignment_survives_saving_and_duplicating(
    household_headers: dict[str, str],
) -> None:
    """`ForecastChange.person_id` (Stufe 6) muss mitgespeichert werden.

    Ginge es beim Speichern verloren, fiele der Steuerklassenhinweis stumm auf
    "andere Person" zurueck - eine Verschlechterung, die niemand bemerkt haette.
    """
    person = client.post("/v1/people", json={"name": "Person B"}, headers=household_headers)
    assert person.status_code == 201, person.text

    created = _create_scenario(
        household_headers,
        name="Mit Person",
        changes=[_income_change(person_id=person.json()["id"])],
    )
    assert created["changes"][0]["person_id"] == person.json()["id"]

    copy = client.post(
        f"/v1/scenarios/{created['id']}/duplicate", headers=household_headers
    )
    assert copy.status_code == 201, copy.text
    assert copy.json()["changes"][0]["person_id"] == person.json()["id"]

    unbekannt = client.put(
        f"/v1/scenarios/{created['id']}/changes",
        json={"changes": [_income_change(person_id=str(uuid.uuid4()))]},
        headers=household_headers,
    )
    # Auch dieser Fremdschluessel ist aufgeschoben - ohne eigene Pruefung
    # schluege er erst beim COMMIT zu.
    assert unbekannt.status_code == 400, unbekannt.text


def test_unknown_target_contract_is_a_client_error(
    household_headers: dict[str, str],
) -> None:
    """Der Fremdschluessel ist `deferrable initially deferred` - ohne eigene
    Pruefung schluege er erst beim COMMIT zu und damit als 500."""
    response = client.post(
        "/v1/scenarios",
        json={
            "name": "Zielt ins Leere",
            "changes": [
                {
                    "change_type": "expense_end",
                    "target_contract_id": str(uuid.uuid4()),
                    "label": "Gibt es nicht",
                    "starts_on": TODAY.isoformat(),
                }
            ],
        },
        headers=household_headers,
    )
    assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# Leitsatz 2: genau ein Rechenkern
# ---------------------------------------------------------------------------


def test_scenario_without_changes_matches_the_base_forecast_field_by_field(
    household_headers: dict[str, str],
) -> None:
    """Ein Szenario ohne Aenderungen ist die Basis - Feld fuer Feld.

    Das ist der Test hinter Leitsatz 2: gaebe es einen zweiten Rechenweg fuer
    gespeicherte Szenarien, wuerde er genau hier auffallen.
    """
    _create_account(household_headers, opening_balance_minor=250_000)
    salary_due = TODAY + timedelta(days=3)
    _create_contract(
        household_headers,
        name="Gehalt Person A",
        direction="income",
        contract_type="income",
        amount_minor=320_000,
        next_due_date=salary_due.isoformat(),
        due_day=salary_due.day,
    )
    _create_contract(household_headers, name="Miete", amount_minor=110_000)

    created = _create_scenario(household_headers, name="Leerer Entwurf", horizon_months=3)

    basis = client.get("/v1/forecast?horizon_months=3", headers=household_headers)
    assert basis.status_code == 200, basis.text
    gerechnet = client.get(
        f"/v1/scenarios/{created['id']}/forecast", headers=household_headers
    )
    assert gerechnet.status_code == 200, gerechnet.text

    payload = gerechnet.json()
    assert payload["base"] == basis.json()
    assert payload["simulation"] == basis.json()
    assert payload["contributions"] == []
    assert payload["comparison"]["surplus_delta_minor"] == 0
    assert payload["comparison"]["balance_delta_24_months_minor"] == 0


def test_deselected_change_does_not_reach_the_calculation(
    household_headers: dict[str, str],
) -> None:
    """`include_in_forecast = false` wirkt im Loader, nicht im Kern: die Zeile
    bleibt sichtbar erhalten, taucht aber weder in der Simulation noch in den
    Beitraegen auf."""
    _create_account(household_headers, opening_balance_minor=250_000)
    salary_due = TODAY + timedelta(days=3)
    _create_contract(
        household_headers,
        name="Gehalt Person A",
        direction="income",
        contract_type="income",
        amount_minor=320_000,
        next_due_date=salary_due.isoformat(),
        due_day=salary_due.day,
    )
    created = _create_scenario(household_headers, name="Abgewaehlt", horizon_months=3)

    abgewaehlt = client.put(
        f"/v1/scenarios/{created['id']}/changes",
        json={"changes": [_expense_change(include_in_forecast=False)]},
        headers=household_headers,
    )
    assert abgewaehlt.status_code == 200, abgewaehlt.text
    # Die Zeile ist gespeichert und sichtbar - sie rechnet nur nicht mit.
    assert abgewaehlt.json()["change_count"] == 1
    assert abgewaehlt.json()["changes"][0]["include_in_forecast"] is False

    ohne_wirkung = client.get(
        f"/v1/scenarios/{created['id']}/forecast", headers=household_headers
    ).json()
    assert ohne_wirkung["base"] == ohne_wirkung["simulation"]
    assert ohne_wirkung["contributions"] == []
    assert ohne_wirkung["comparison"]["surplus_delta_minor"] == 0

    angewaehlt = client.put(
        f"/v1/scenarios/{created['id']}/changes",
        json={"changes": [_expense_change(include_in_forecast=True)]},
        headers=household_headers,
    )
    assert angewaehlt.status_code == 200, angewaehlt.text
    mit_wirkung = client.get(
        f"/v1/scenarios/{created['id']}/forecast", headers=household_headers
    ).json()
    assert mit_wirkung["comparison"]["surplus_delta_minor"] == -28_000
    assert [item["change_index"] for item in mit_wirkung["contributions"]] == [0]
    assert mit_wirkung["contributions"][0]["monthly_minor"] == -28_000


# ---------------------------------------------------------------------------
# Regelverletzungen: 4xx statt 500
# ---------------------------------------------------------------------------


def _put_single_change(headers: dict[str, str], scenario_id: str, change: dict[str, Any]) -> int:
    response = client.put(
        f"/v1/scenarios/{scenario_id}/changes",
        json={"changes": [change]},
        headers=headers,
    )
    return response.status_code


def test_every_check_violation_is_a_client_error(
    household_headers: dict[str, str],
) -> None:
    """Keine Regelverletzung darf als 500 ankommen.

    Zwei Instanzen greifen hintereinander, und der Test haelt fest, welche:

    * `ForecastChange` prueft dieselben XOR-Regeln wie die Tabelle und lehnt
      schon den Request-Body ab - das ist ein 422, genau wie bei
      `POST /forecast/simulate`, das dasselbe Modell benutzt. Ein eigener
      Statuscode nur fuer den gespeicherten Weg waere derselbe Fehler unter
      zwei Nummern.
    * Wo die Tabelle strenger ist als das Eingabemodell - `one_off` mit
      `ends_on` - schlaegt der CHECK zu; dieser Fall wird als 400 gefangen.
    """
    scenario = _create_scenario(household_headers, name="Regelprobe")

    # *_end mit Betrag (household_scenario_changes_amount_chk)
    assert (
        _put_single_change(
            household_headers,
            scenario["id"],
            {
                "change_type": "expense_end",
                "target_contract_id": str(uuid.uuid4()),
                "label": "Ende mit Betrag",
                "amount_mode": "absolute",
                "amount_minor": -1_000,
                "starts_on": TODAY.isoformat(),
            },
        )
        == 422
    )

    # one_off mit frequency='monthly' (household_scenario_changes_once_chk)
    assert (
        _put_single_change(
            household_headers,
            scenario["id"],
            {
                "change_type": "one_off",
                "label": "Reparatur",
                "amount_mode": "absolute",
                "amount_minor": -80_000,
                "frequency": "monthly",
                "starts_on": TODAY.isoformat(),
            },
        )
        == 422
    )

    # income_change ohne Ziel (household_scenario_changes_target_chk)
    assert (
        _put_single_change(
            household_headers,
            scenario["id"],
            {
                "change_type": "income_change",
                "label": "Ohne Ziel",
                "amount_mode": "delta",
                "amount_minor": 20_000,
                "frequency": "monthly",
                "starts_on": TODAY.isoformat(),
            },
        )
        == 422
    )

    # one_off mit ends_on: kommt durch `ForecastChange` und scheitert erst am
    # CHECK der Tabelle - der Fall, fuer den der IntegrityError-Fang existiert.
    assert (
        _put_single_change(
            household_headers,
            scenario["id"],
            {
                "change_type": "one_off",
                "label": "Einmalzahlung mit Ende",
                "amount_mode": "absolute",
                "amount_minor": -80_000,
                "frequency": "once",
                "starts_on": TODAY.isoformat(),
                "ends_on": (TODAY + timedelta(days=30)).isoformat(),
            },
        )
        == 400
    )

    # Nach vier abgelehnten Versuchen steht das Szenario unveraendert da.
    unveraendert = client.get(f"/v1/scenarios/{scenario['id']}", headers=household_headers)
    assert unveraendert.status_code == 200, unveraendert.text
    assert unveraendert.json()["change_count"] == 0


# ---------------------------------------------------------------------------
# Vergleich
# ---------------------------------------------------------------------------


def test_compare_returns_one_base_and_one_entry_per_scenario(
    household_headers: dict[str, str],
) -> None:
    _set_horizon(household_headers, 3)
    _create_account(household_headers, opening_balance_minor=250_000)
    salary_due = TODAY + timedelta(days=3)
    _create_contract(
        household_headers,
        name="Gehalt Person A",
        direction="income",
        contract_type="income",
        amount_minor=320_000,
        next_due_date=salary_due.isoformat(),
        due_day=salary_due.day,
    )

    besser = _create_scenario(household_headers, name="Mehr Einkommen", changes=[_income_change()])
    schlechter = _create_scenario(
        household_headers, name="Mehr Ausgaben", changes=[_expense_change()]
    )

    response = client.get(
        f"/v1/scenarios/compare?ids={besser['id']},{schlechter['id']}",
        headers=household_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [entry["scenario"]["id"] for entry in payload["entries"]] == [
        besser["id"],
        schlechter["id"],
    ]
    assert payload["entries"][0]["comparison"]["surplus_delta_minor"] == 145_000
    assert payload["entries"][1]["comparison"]["surplus_delta_minor"] == -28_000
    # Eine Basis fuer alle Linien - sonst waere der Vergleich keiner.
    assert payload["base"]["opening_liquidity_minor"] == 250_000
    assert payload["entries"][0]["simulation"]["opening_liquidity_minor"] == 250_000


def test_compare_refuses_more_than_four_scenarios(
    household_headers: dict[str, str],
) -> None:
    ids = ",".join(str(uuid.uuid4()) for _ in range(5))
    response = client.get(f"/v1/scenarios/compare?ids={ids}", headers=household_headers)
    assert response.status_code == 400, response.text
    assert "4" in response.json()["detail"]

    leer = client.get("/v1/scenarios/compare?ids=%20", headers=household_headers)
    assert leer.status_code == 400, leer.text

    unlesbar = client.get("/v1/scenarios/compare?ids=keine-uuid", headers=household_headers)
    assert unlesbar.status_code == 400, unlesbar.text


# ---------------------------------------------------------------------------
# Uebernahme der Fixkostenszenarien (Plan Abschnitt 11)
# ---------------------------------------------------------------------------


def _adoption_statements() -> list[str]:
    """Die beiden INSERT-Anweisungen der Uebernahme, direkt aus dem Schema.

    Bewusst NICHT bis zum Dateiende gelesen: hinter der Uebernahme stehen
    weitere Abschnitte (Nachtraege anderer Stufen), die hier nichts zu suchen
    haben - und ein `do $$ ... end $$;`-Block ueberlebt die schlichte Aufteilung
    an Semikolons ohnehin nicht. Es werden deshalb nur die fuehrenden
    Anweisungen genommen, die in die beiden Szenariotabellen schreiben.
    """
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    tail = sql[sql.index(_ADOPTION_MARKER) :]
    # Zeilenkommentare zuerst entfernen: einer von ihnen enthaelt selbst ein
    # Semikolon, an dem die Aufteilung sonst mitten in der Anweisung schnitte.
    # In diesem Abschnitt steht kein `--` innerhalb einer Zeichenkette, sonst
    # waere das zu grob.
    ohne_kommentare = "\n".join(line.split("--", 1)[0] for line in tail.splitlines())

    statements: list[str] = []
    for part in ohne_kommentare.split(";"):
        statement = part.strip()
        if not statement:
            continue
        if not statement.lower().startswith("insert into household_scenario"):
            break
        statements.append(statement)
    assert len(statements) == 2, f"Erwartet werden zwei Uebernahme-Inserts, gefunden: {statements}"
    return statements


async def _run_adoption() -> None:
    """Fuehrt die Uebernahme aus dem Schema aus.

    `exec_driver_sql` statt `text()`: die Anweisungen enthalten `::date`-Casts
    und sollen unveraendert beim Treiber ankommen, ohne dass jemand sie
    unterwegs als Bindparameter deutet.
    """
    factory = get_session_factory()
    async with factory() as session, session.begin():
        connection = await session.connection()
        for statement in _adoption_statements():
            await connection.exec_driver_sql(statement)


async def test_adopted_scenario_has_the_same_monthly_effect_as_its_original() -> None:
    """Ein uebernommenes Fixkostenszenario muss dieselbe Monatswirkung haben
    wie das Original laut `contract_target_service`.

    Andernfalls stuende "Netflix kuendigen" in zwei Systemen mit zwei
    verschiedenen Betraegen - genau der Zustand, den die Uebernahme
    ausschliessen soll.
    """
    headers = _headers("Uebernahme")
    _set_horizon(headers, 6)
    _create_account(headers, opening_balance_minor=500_000)
    salary_due = TODAY + timedelta(days=3)
    _create_contract(
        headers,
        name="Gehalt",
        direction="income",
        contract_type="income",
        amount_minor=400_000,
        next_due_date=salary_due.isoformat(),
        due_day=salary_due.day,
    )
    gekuendigt = _create_contract(headers, name="Aaa Streaming", amount_minor=1_999)
    gesenkt = _create_contract(headers, name="Bbb Mobilfunk", amount_minor=4_990)
    # Auf 0 gesenkt statt gekuendigt - das kommt vor und ist der Grund, warum
    # das Lesemodell `amount_minor = 0` zulassen muss.
    auf_null = _create_contract(
        headers, name="Ccc Zeitschrift", amount_minor=12_000, frequency="yearly"
    )

    original = client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Sparplan 2027",
            "items": [
                {"contract_id": gekuendigt["id"], "action": "cancel"},
                {
                    "contract_id": gesenkt["id"],
                    "action": "reduce",
                    "target_amount_minor": 2_490,
                },
                {
                    "contract_id": auf_null["id"],
                    "action": "reduce",
                    "target_amount_minor": 0,
                },
            ],
        },
        headers=headers,
    )
    assert original.status_code == 201, original.text
    monatliche_wirkung = original.json()["monthly_saving_minor"]
    assert monatliche_wirkung > 0

    await _run_adoption()

    uebernommen = [
        item
        for item in client.get("/v1/scenarios", headers=headers).json()
        if item["origin"] == "contract_scenario"
    ]
    assert [item["name"] for item in uebernommen] == ["Sparplan 2027"]
    assert uebernommen[0]["change_count"] == 3

    detail = client.get(f"/v1/scenarios/{uebernommen[0]['id']}", headers=headers).json()
    assert sorted(item["change_type"] for item in detail["changes"]) == [
        "expense_change",
        "expense_change",
        "expense_end",
    ]
    # Ein Senkungsplan beginnt am Monatsersten danach, nicht rueckwirkend.
    erster_naechster = (date(TODAY.year, TODAY.month, 1) + timedelta(days=32)).replace(day=1)
    assert {item["starts_on"] for item in detail["changes"]} == {
        erster_naechster.isoformat()
    }

    gerechnet = client.get(
        f"/v1/scenarios/{uebernommen[0]['id']}/forecast", headers=headers
    )
    assert gerechnet.status_code == 200, gerechnet.text
    payload = gerechnet.json()
    ersparnis = (
        payload["base"]["metrics"]["fixed_monthly_minor"]
        - payload["simulation"]["metrics"]["fixed_monthly_minor"]
    )
    assert ersparnis == monatliche_wirkung
    assert payload["comparison"]["surplus_delta_minor"] == monatliche_wirkung


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_scenarios_are_isolated_between_households() -> None:
    first = _headers("Szenarien A")
    second = _headers("Szenarien B")

    created = _create_scenario(first, name="Nur fuer A", changes=[_income_change()])

    assert client.get("/v1/scenarios", headers=second).json() == []
    assert client.get(f"/v1/scenarios/{created['id']}", headers=second).status_code == 404
    assert (
        client.get(f"/v1/scenarios/{created['id']}/forecast", headers=second).status_code == 404
    )
    assert (
        client.get(f"/v1/scenarios/compare?ids={created['id']}", headers=second).status_code
        == 404
    )
    assert client.delete(f"/v1/scenarios/{created['id']}", headers=second).status_code == 404

    # Fremder Haushaltskontext mit eigenem Token: an der Mitgliedschaftspruefung
    # gescheitert, nicht erst an RLS.
    crossed = client.get(
        "/v1/scenarios",
        headers={
            "Authorization": second["Authorization"],
            "X-Household-Id": first["X-Household-Id"],
        },
    )
    assert crossed.status_code == 403

    # Das Szenario von A steht unveraendert da.
    assert client.get(f"/v1/scenarios/{created['id']}", headers=first).status_code == 200
