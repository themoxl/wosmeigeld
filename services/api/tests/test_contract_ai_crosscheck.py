"""Gegenpruefung von Vertragspaaren durch das Sprachmodell (P5.3).

docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 19.11. Ersetzt ist ausschliesslich
der Modellaufruf; Vorfilterung, Belegsuche, Maskierung und Antwortpruefung
laufen echt. Genau dort sitzen die Zusicherungen, die ein Mock nicht nachweisen
koennte:

* zwei vorhandene, verschiedene Vertragsschluessel werden gar nicht erst
  gefragt (Leitsatz 4),
* eine gemerkte Verneinung bleibt in beide Richtungen wirksam,
* eine unbrauchbare Modellantwort ist kein Treffer,
* und die Referenz aus dem Verwendungszweck ueberlebt die PII-Maskierung -
  ohne sie waere der Musterfunk-Fall nicht entscheidbar.

Ersetzt sind seit dem relativen KI-Budget nur Discovery und Modellserver
(tests/ki_attrappe.py); der Gateway des KI-Kerns laeuft echt. Damit ist auch
pruefbar, was frueher fest im Dienst stand und jetzt nicht mehr: kein
erzwungenes reasoning="off", kein Ausgabebudget von 512 Token, und jeder
Fehlschlag steht mit seinem Grund in `meldung` und in llm_calls.

Der Referenzfall ist der aus Abschnitt 19.1: drei Legacy-Zeilen
"Musterfunk Deutschland GmbH" ohne `contract_key`, ohne Zeitraeume und mit
`payee_id is null` an jeder Buchung.
"""

import json
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.llm import reasons
from app.main import app
from app.services import contract_ai_crosscheck_service as crosscheck
from tests import ki_attrappe
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

Headers = dict[str, str]

TODAY = date.today()

FESTNETZ_PURPOSE = "MUSTERFUNK DEUTSCHLAND GMBH FESTNETZ VERTRAGSKONTO 1122334455 RECHNUNG"
MOBILFUNK_PURPOSE = "MUSTERFUNK DEUTSCHLAND GMBH MOBILFUNK KUNDENKONTO 0011223344 RECHNUNG"
PROVIDER = "Musterfunk Deutschland GmbH"


# ---------------------------------------------------------------------------
# Fixtures und Helfer
# ---------------------------------------------------------------------------


@pytest.fixture()
def household_headers() -> Headers:
    token = _signup(f"aicross-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-3")
    household = client.post(
        "/v1/households", json={"name": "Gegenpruef-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: Headers) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return str(account["id"])


async def _execute(sql: str, **params: Any) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text(sql), params)
        await session.commit()


def _create_contract(headers: Headers, name: str, amount_minor: int) -> dict[str, Any]:
    """Legt eine Vertragszeile an, wie der Altbestand sie hinterlaesst.

    Kein `contract_key`, keine Zeitraeume - der Endpunkt `POST /v1/contracts`
    setzt weder `first_seen_date` noch `last_seen_date`, und genau so sehen
    uebernommene Zeilen aus.
    """
    response = client.post(
        "/v1/contracts",
        json={
            "name": name,
            "amount_minor": amount_minor,
            "frequency": "monthly",
            "next_due_date": TODAY.isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _set_contract_key(contract_id: str, contract_key: str) -> None:
    await _execute(
        "update contracts set contract_key = :contract_key, contract_key_source = 'mandate' "
        "where id = cast(:id as uuid)",
        id=contract_id,
        contract_key=contract_key,
    )


async def _seed_transaction(
    household_id: str,
    account_id: str,
    amount_minor: int,
    purpose_raw: str,
    months_ago: int,
) -> None:
    """Buchung per SQL statt ueber die API: nur so laesst sich `purpose_raw`
    setzen, und daran haengt die Referenz, um die es hier geht. `payee_id`
    bleibt leer - genau wie bei Buchungen aus einer Datenuebernahme."""
    await _execute(
        "insert into transactions (household_id, account_id, booked_date, amount_minor, "
        "payee_label, purpose_raw) values (cast(:household_id as uuid), "
        "cast(:account_id as uuid), :booked_date, :amount_minor, :payee_label, :purpose_raw)",
        household_id=household_id,
        account_id=account_id,
        booked_date=TODAY - timedelta(days=30 * months_ago),
        amount_minor=amount_minor,
        payee_label=PROVIDER,
        purpose_raw=purpose_raw,
    )


def _stub_model(
    monkeypatch: pytest.MonkeyPatch,
    answer: dict[str, Any] | Callable[[str], dict[str, Any]] | None,
    *,
    connected: bool = True,
) -> list[dict[str, Any]]:
    """Ersetzt Discovery und Modellserver; der Gateway laeuft echt.

    `answer` ist entweder eine feste Antwort oder eine Funktion ueber den
    Nutzerprompt. Zurueckgegeben werden die tatsaechlich gesendeten Anfragen -
    ein leerer Verlauf ist der Beweis, dass die Vorfilterung gegriffen hat,
    bevor Rechenzeit verbrannt wurde.
    """
    server = ki_attrappe.install(
        monkeypatch,
        ki_attrappe.json_server(answer),
        ki_attrappe.target_result() if connected else ki_attrappe.offline(),
    )
    return server.bodies


def _crosscheck(headers: Headers, **body: Any) -> dict[str, Any]:
    response = client.post("/v1/contracts/ai-crosscheck", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _reasons(body: dict[str, Any]) -> list[str]:
    return [entry["grund"] for entry in body["uebersprungen_gruende"]]


_YES: dict[str, Any] = {
    "gehoert_zusammen": True,
    "begruendung": "Dieselbe Vertragsreferenz, nur ein anderer Preis.",
}


# ---------------------------------------------------------------------------
# Antwortpruefung (ohne Datenbank)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        None,
        {},
        {"begruendung": "Sieht gleich aus."},
        # Kein Boolean: eine 1 ist in Python zwar wahrheitswertig, hier aber
        # kein Urteil.
        {"gehoert_zusammen": 1, "begruendung": "Sieht gleich aus."},
        {"gehoert_zusammen": "ja", "begruendung": "Sieht gleich aus."},
        {"gehoert_zusammen": True, "begruendung": ""},
        # Scheingenauigkeit: verboten, egal in welcher Schreibweise.
        {"gehoert_zusammen": True, "begruendung": "Zu 80 % derselbe Vertrag."},
        {"gehoert_zusammen": True, "begruendung": "Konfidenz hoch, gleiche Referenz."},
        {"gehoert_zusammen": True, "begruendung": "Score 0,9 fuer dieselbe Referenz."},
        {"gehoert_zusammen": False, "begruendung": "x" * 401},
    ],
)
def test_an_unusable_answer_is_no_verdict(answer: Any) -> None:
    """Ungueltig heisst "nicht beurteilt" - und ausdruecklich weder Treffer
    noch Freispruch."""
    assert crosscheck.parse_crosscheck_answer(answer) is None


def test_a_reason_may_name_the_reference() -> None:
    """Die Sperre trifft die erfundene Prozentzahl, nicht die Ziffern des
    Belegs: die Referenz zu nennen ist genau die geforderte Begruendung."""
    parsed = crosscheck.parse_crosscheck_answer(
        {
            "gehoert_zusammen": True,
            "begruendung": "Beide Buchungen nennen Vertragskonto 1122334455.",
        }
    )
    assert parsed == (True, "Beide Buchungen nennen Vertragskonto 1122334455.")


def test_the_answer_schema_has_no_confidence_field() -> None:
    """Leitsatz 8, erste Durchsetzungsebene: das Modell bekommt gar keinen Ort
    fuer eine Prozentzahl."""
    properties = crosscheck.CROSSCHECK_JSON_SCHEMA["schema"]["properties"]
    assert set(properties) == {"gehoert_zusammen", "begruendung"}
    assert crosscheck.CROSSCHECK_JSON_SCHEMA["schema"]["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Belegmaterial und Maskierung
# ---------------------------------------------------------------------------


def test_the_masked_purpose_keeps_the_contract_reference() -> None:
    """Der fachliche Hebel des ganzen Dienstes.

    `mask_pii_text` ersetzt jede 10- bis 16-stellige Ziffernfolge, also auch
    Vertrags- und Kundenkonto. Ohne die getrennt abgeleitete Referenz waeren
    Festnetz und Mobilfunk im Prompt nicht mehr zu unterscheiden.
    """
    festnetz = crosscheck._sample(TODAY, -4995, FESTNETZ_PURPOSE)
    mobilfunk = crosscheck._sample(TODAY, -9990, MOBILFUNK_PURPOSE)

    assert festnetz.reference == "1122334455"
    assert mobilfunk.reference == "0011223344"
    # Im Anzeigetext ist die Nummer maskiert - dort wurde nichts aufgeweicht.
    assert "1122334455" not in festnetz.purpose_masked
    assert "MASKED" in festnetz.purpose_masked
    # Die fachliche Unterscheidung ueberlebt trotzdem.
    assert "FESTNETZ" in festnetz.purpose_masked
    assert "MOBILFUNK" in mobilfunk.purpose_masked


def test_the_prompt_carries_both_references() -> None:
    def row(name: str, amount_minor: int) -> crosscheck.ContractRow:
        return crosscheck.ContractRow(
            id=uuid.uuid4(),
            name=name,
            provider_label=PROVIDER,
            payee_id=None,
            direction="expense",
            amount_minor=amount_minor,
            frequency="monthly",
            due_day=3,
            first_seen_date=None,
            last_seen_date=None,
            contract_key=None,
        )

    left, right = row("Musterfunk Festnetz", 4995), row("Musterfunk Mobilfunk", 9990)
    evidence = {
        left.id: [crosscheck._sample(TODAY, -4995, FESTNETZ_PURPOSE)],
        right.id: [crosscheck._sample(TODAY, -9990, MOBILFUNK_PURPOSE)],
    }
    prompt = crosscheck.build_crosscheck_user_prompt(left, right, evidence)

    assert "1122334455" in prompt
    assert "0011223344" in prompt
    assert "nicht hinterlegt" in prompt  # keine Zeitraeume im Altbestand
    assert "49,95 EUR" in prompt
    assert "99,90 EUR" in prompt


# ---------------------------------------------------------------------------
# Vorfilterung
# ---------------------------------------------------------------------------


async def test_two_different_contract_keys_are_never_asked(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leitsatz 4: verschiedene vorhandene Schluessel schliessen das Paar aus -
    das Modell bekommt es gar nicht erst zu sehen."""
    festnetz = _create_contract(household_headers, PROVIDER, 4995)
    mobilfunk = _create_contract(household_headers, PROVIDER, 9990)
    await _set_contract_key(festnetz["id"], "1122334455")
    await _set_contract_key(mobilfunk["id"], "0011223344")

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    assert asked == []
    assert body["items"] == []
    assert body["geprueft"] == 0
    assert body["uebersprungen"] == 1
    assert _reasons(body) == ["different_contract_key"]


async def test_one_missing_key_still_gets_asked(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fehlt einer der beiden Schluessel, bleibt es ein Pruefvorschlag - das
    ist die Restmenge, fuer die es diesen Dienst gibt."""
    older = _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)
    await _set_contract_key(older["id"], "1122334455")

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    assert len(asked) == 1
    assert body["geprueft"] == 1
    assert body["uebersprungen"] == 0


@pytest.mark.parametrize("dismiss_from_older", [True, False])
async def test_a_dismissed_pair_is_not_asked_again(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch, dismiss_from_older: bool
) -> None:
    """Die Verneinung wirkt symmetrisch: an welchem der beiden Vertraege das
    Ereignis haengt, entscheidet nur, wo der Nutzer geklickt hat."""
    first = _create_contract(household_headers, PROVIDER, 4995)
    second = _create_contract(household_headers, PROVIDER, 3995)
    target, source = (first, second) if dismiss_from_older else (second, first)

    dismissed = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_duplicate_suggestion",
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "dismiss": True,
        },
        headers=household_headers,
    )
    assert dismissed.status_code == 200, dismissed.text

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    assert asked == []
    assert body["items"] == []
    assert body["uebersprungen"] == 1
    assert _reasons(body) == ["dismissed_by_user"]


async def test_a_merged_contract_is_no_longer_a_candidate(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine zusammengefuehrte Zeile bleibt fuer den Undo erhalten, aber sie ist
    keine Haelfte eines Paares mehr."""
    target = _create_contract(household_headers, PROVIDER, 3995)
    source = _create_contract(household_headers, PROVIDER, 4995)
    merged = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": f"crosscheck-{uuid.uuid4().hex}",
        },
        headers=household_headers,
    )
    assert merged.status_code == 200, merged.text

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    assert asked == []
    assert body["items"] == []
    assert body["uebersprungen"] == 0
    assert body["truncated"] is False


async def test_a_different_provider_is_no_pair(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, "Stadtwerke Hamburg", 4995)

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    assert asked == []
    assert body["geprueft"] == 0
    assert body["uebersprungen"] == 0


async def test_contract_ids_narrow_the_run_to_one_pair(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Knopf an einer einzelnen Karte fragt nur dieses eine Paar."""
    first = _create_contract(household_headers, PROVIDER, 4995)
    second = _create_contract(household_headers, PROVIDER, 3995)
    _create_contract(household_headers, PROVIDER, 9990)

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers, contract_ids=[first["id"], second["id"]])

    assert len(asked) == 1
    assert body["geprueft"] == 1
    ids = {body["items"][0]["contract_a"]["id"], body["items"][0]["contract_b"]["id"]}
    assert ids == {first["id"], second["id"]}


# ---------------------------------------------------------------------------
# Begrenzung
# ---------------------------------------------------------------------------


async def test_more_pairs_than_the_limit_report_truncated(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sechs Vertraege desselben Anbieters ergeben fuenfzehn Paare. Der Lauf
    beurteilt zwoelf und sagt, dass er abgeschnitten hat - stilles Weglassen
    waere die schlimmere Variante."""
    for index in range(6):
        _create_contract(household_headers, PROVIDER, 5000 + index * 1000)

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    assert len(asked) == crosscheck.MAX_PAIRS
    assert body["geprueft"] == crosscheck.MAX_PAIRS
    assert len(body["items"]) == crosscheck.MAX_PAIRS
    assert body["truncated"] is True


# ---------------------------------------------------------------------------
# Modellantwort im Lauf
# ---------------------------------------------------------------------------


async def test_an_unusable_model_answer_is_not_a_hit(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)

    asked = _stub_model(
        monkeypatch,
        {"gehoert_zusammen": True, "begruendung": "Zu 95 % derselbe Vertrag."},
    )
    body = _crosscheck(household_headers)

    assert len(asked) == 1
    assert body["items"] == []
    assert body["geprueft"] == 0
    assert body["uebersprungen"] == 1
    assert _reasons(body) == ["no_model_answer"]
    # Keine leere Erfolgsantwort: der Grund steht in der Meldung ...
    assert reasons.MODEL_OUTPUT_UNUSABLE in body["meldung"]
    # ... und die HTTP-200-Zeile des Gateways ist fachlich als Fehler markiert.
    [call] = await ki_attrappe.llm_calls(household_headers["X-Household-Id"])
    assert call.stage == crosscheck.CROSSCHECK_STAGE
    assert call.status == "error"
    assert call.reason_code == reasons.MODEL_OUTPUT_UNUSABLE


async def test_a_hit_is_marked_as_a_model_statement(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Antwort traegt eine Herkunft und keine Zahl - die Oberflaeche darf
    eine Modellaussage nie wie einen deterministischen Beleg zeigen."""
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)

    _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    [item] = body["items"]
    assert item["quelle"] == "ai"
    assert item["gehoert_zusammen"] is True
    assert item["begruendung"] == _YES["begruendung"]
    assert "confidence" not in item
    assert body["modell_name"] == "test-model"
    assert body["meldung"] is None


async def test_without_a_model_the_answer_stays_plain(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein 500er, kein Stacktrace: ein optionaler Zusatz, der gerade nicht
    laeuft, ist ein Hinweis und kein Serverfehler."""
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)

    asked = _stub_model(monkeypatch, _YES, connected=False)
    body = _crosscheck(household_headers)

    assert asked == []
    assert body["items"] == []
    assert body["geprueft"] == 0
    assert body["modell_name"] is None
    # Klartext des Kerns (model_unavailable) samt Abhilfe, eingebettet in die
    # Aussage des Dienstes.
    assert body["meldung"].startswith(crosscheck.CROSSCHECK_SUBJECT)
    assert "LM Studio" in body["meldung"]
    assert crosscheck.CROSSCHECK_OUTCOME in body["meldung"]


async def test_switched_off_ai_says_so_instead_of_searching_for_a_model(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)
    settings = client.put(
        "/v1/ai/settings", json={"is_enabled": False}, headers=household_headers
    )
    assert settings.status_code == 200, settings.text

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    assert asked == []
    assert body["meldung"] == crosscheck.AI_DISABLED_MESSAGE


async def test_nothing_to_check_reports_no_message(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Kandidatenpaare wird das Modell nicht einmal gesucht - eine
    Erreichbarkeitsmeldung waere hier irrefuehrend."""
    _create_contract(household_headers, PROVIDER, 4995)

    asked = _stub_model(monkeypatch, _YES, connected=False)
    body = _crosscheck(household_headers)

    assert asked == []
    assert body["meldung"] is None
    assert body["geprueft"] == 0
    assert body["uebersprungen"] == 0


# ---------------------------------------------------------------------------
# Referenzfall Musterfunk (Abschnitt 19.1)
# ---------------------------------------------------------------------------


def _answer_by_reference(prompt: str) -> dict[str, Any]:
    """Stellvertreter fuer das Modell: vergleicht die beiden Referenzzeilen.

    Bewusst genau die Information, auf die der Prompt das Modell stossen soll.
    Faellt die Referenz der Maskierung zum Opfer, sind beide Zeilen gleich und
    dieser Test schlaegt fehl - er prueft also die Prompt-Aufbereitung mit,
    nicht nur die Verdrahtung.
    """
    references = [
        line.split(":", 1)[1].strip()
        for line in prompt.splitlines()
        if "Referenz im Verwendungszweck:" in line
    ]
    same = len(references) == 2 and references[0] == references[1]
    return {
        "gehoert_zusammen": same,
        "begruendung": (
            "Beide Buchungen nennen dieselbe Vertragsreferenz, nur der Betrag ändert sich."
            if same
            else "Die Verwendungszwecke nennen zwei verschiedene Vertragsreferenzen."
        ),
    }


async def test_musterfunk_reference_case_keeps_mobilfunk_apart(
    household_headers: Headers, account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Fall aus Abschnitt 19.1.

    Drei Legacy-Zeilen ohne Schluessel und ohne Zeitraeume, alle Buchungen ohne
    `payee_id`. `GET /v1/contracts/duplicate-suggestions` findet hier nichts -
    genau deshalb gibt es die Gegenpruefung. Erwartet wird ein Treffer
    (49,95 -> 39,95) und zweimal "gehoert nicht zusammen" gegen den
    Mobilfunkvertrag.
    """
    household_id = household_headers["X-Household-Id"]
    alt = _create_contract(household_headers, PROVIDER, 4995)
    neu = _create_contract(household_headers, PROVIDER, 3995)
    mobil = _create_contract(household_headers, PROVIDER, 9990)

    for months_ago in (6, 5, 4):
        await _seed_transaction(household_id, account_id, -4995, FESTNETZ_PURPOSE, months_ago)
    for months_ago in (3, 2, 1):
        await _seed_transaction(household_id, account_id, -3995, FESTNETZ_PURPOSE, months_ago)
    for months_ago in (6, 5, 4, 3, 2, 1):
        await _seed_transaction(
            household_id, account_id, -9990, MOBILFUNK_PURPOSE, months_ago
        )

    # Ausgangslage: deterministisch ist hier nichts zu holen.
    deterministic = client.get(
        "/v1/contracts/duplicate-suggestions", headers=household_headers
    ).json()
    assert deterministic["items"] == []

    asked = _stub_model(monkeypatch, _answer_by_reference)
    body = _crosscheck(household_headers)

    assert len(asked) == 3
    assert body["geprueft"] == 3
    assert body["truncated"] is False

    verdicts = {
        frozenset({item["contract_a"]["id"], item["contract_b"]["id"]}): item[
            "gehoert_zusammen"
        ]
        for item in body["items"]
    }
    assert verdicts[frozenset({alt["id"], neu["id"]})] is True
    assert verdicts[frozenset({alt["id"], mobil["id"]})] is False
    assert verdicts[frozenset({neu["id"], mobil["id"]})] is False

    # Der Lauf ist folgenlos: kein Merge, keine geaenderten Betraege.
    contracts = client.get("/v1/contracts", headers=household_headers).json()
    assert sorted(entry["amount_minor"] for entry in contracts) == [3995, 4995, 9990]


# ---------------------------------------------------------------------------
# Modellaufruf ueber den KI-Kern (relatives Budget, keine Vorgaben im Code)
# ---------------------------------------------------------------------------


def _set_reasoning(headers: Headers, reasoning: str) -> None:
    response = client.put("/v1/ai/settings", json={"reasoning": reasoning}, headers=headers)
    assert response.status_code == 200, response.text


async def test_the_household_reasoning_is_sent_unchanged_with_the_window_budget(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frueher: reasoning="off" erzwungen und hoechstens 512 Token Antwort.
    Jetzt gilt die Haushaltseinstellung, und das Budget ist der freie Platz
    des geladenen Fensters (W_frei - E - A) - gerechnet vom Gateway."""
    _set_reasoning(household_headers, "high")
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)

    asked = _stub_model(monkeypatch, _YES)
    body = _crosscheck(household_headers)

    assert body["geprueft"] == 1
    [request] = asked
    assert request["reasoning_effort"] == "high"
    # Der ganze freie Platz: weit ueber der Reserve, weit ueber 512.
    reserve = ki_attrappe.WINDOW * 0.25
    assert request["max_tokens"] > ki_attrappe.WINDOW - reserve
    assert request["response_format"]["json_schema"] == crosscheck.CROSSCHECK_JSON_SCHEMA

    [call] = await ki_attrappe.llm_calls(household_headers["X-Household-Id"])
    assert call.stage == crosscheck.CROSSCHECK_STAGE
    assert call.prompt_version == crosscheck.CROSSCHECK_PROMPT_VERSION
    assert call.status == "success"
    assert call.max_tokens_sent == request["max_tokens"]
    assert call.window_tokens == ki_attrappe.WINDOW
    assert call.reasoning_sent == "high"


async def test_an_answer_lost_in_reasoning_is_named_not_retried(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verbraucht das Denken das Budget, gibt es keinen zweiten Aufruf "ohne
    Denken" (E1), sondern den benannten Grund model_budget_exhausted."""
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)

    server = ki_attrappe.install(
        monkeypatch,
        ki_attrappe.ModelServer(
            lambda body: ki_attrappe.completion(
                reasoning="Vertrag A und B ... ich pruefe noch einmal ...",
                finish_reason="length",
            )
        ),
    )
    body = _crosscheck(household_headers)

    assert len(server.bodies) == 1
    assert body["items"] == []
    assert _reasons(body) == ["no_model_answer"]
    assert reasons.MODEL_BUDGET_EXHAUSTED in body["meldung"]
    assert "Antwortbudget" in body["meldung"]
    [call] = await ki_attrappe.llm_calls(household_headers["X-Household-Id"])
    assert call.status == "error"
    assert call.reason_code == reasons.MODEL_BUDGET_EXHAUSTED


async def test_json_only_in_the_reasoning_of_the_same_answer_counts(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manche Backends legen das fertige JSON in reasoning_content ab. Es
    stammt aus demselben Aufruf und darf gelesen werden - ohne Zusatzaufruf."""
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)

    server = ki_attrappe.install(
        monkeypatch,
        ki_attrappe.ModelServer(
            lambda body: ki_attrappe.completion(reasoning=json.dumps(_YES, ensure_ascii=False))
        ),
    )
    body = _crosscheck(household_headers)

    assert len(server.bodies) == 1
    assert body["geprueft"] == 1
    assert body["meldung"] is None
    [call] = await ki_attrappe.llm_calls(household_headers["X-Household-Id"])
    assert call.status == "success"
    assert call.reason_code is None
    assert "Denktext" in call.error_message


async def test_an_unknown_window_stops_the_run_with_its_reason(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne geladenes Fenster wird nicht gesendet (model_context_unknown). Der
    Lauf fragt dann nicht drei Mal dasselbe ab, sondern endet und sagt, wie
    viele Paare offen bleiben."""
    for amount in (4995, 3995, 9990):
        _create_contract(household_headers, PROVIDER, amount)

    server = ki_attrappe.install(
        monkeypatch, ki_attrappe.json_server(_YES), ki_attrappe.target_result(window=None)
    )
    body = _crosscheck(household_headers)

    assert server.bodies == []
    assert body["items"] == []
    assert body["truncated"] is True
    assert reasons.MODEL_CONTEXT_UNKNOWN in body["meldung"]
    assert "2 weitere Paar(e)" in body["meldung"]
    [call] = await ki_attrappe.llm_calls(household_headers["X-Household-Id"])
    assert call.status == "refused"
    assert call.reason_code == reasons.MODEL_CONTEXT_UNKNOWN


async def test_a_question_that_does_not_fit_the_window_is_refused_by_name(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passt die Frage samt Antwortreserve nicht ins Fenster, wird sie nicht
    gekuerzt und nicht gesendet: model_input_too_large mit Aufschluesselung."""
    _create_contract(household_headers, PROVIDER, 4995)
    _create_contract(household_headers, PROVIDER, 3995)

    server = ki_attrappe.install(
        monkeypatch, ki_attrappe.json_server(_YES), ki_attrappe.target_result(window=600)
    )
    body = _crosscheck(household_headers)

    assert server.bodies == []
    assert _reasons(body) == ["no_model_answer"]
    assert reasons.MODEL_INPUT_TOO_LARGE in body["meldung"]
    assert "Antwortreserve" in body["meldung"]
    [call] = await ki_attrappe.llm_calls(household_headers["X-Household-Id"])
    assert call.status == "refused"
    assert call.reason_code == reasons.MODEL_INPUT_TOO_LARGE


async def test_a_rejected_schema_is_dropped_for_the_rest_of_the_run(
    household_headers: Headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lehnt der Server response_format ab, fragt der Gateway einmal ohne
    Schema nach; die uebrigen Paare des Laufs gehen gleich ohne Schema."""
    for amount in (4995, 3995, 9990):
        _create_contract(household_headers, PROVIDER, amount)

    def reply(body: dict[str, Any]) -> Any:
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format is not supported"})
        return ki_attrappe.completion(json.dumps(_YES, ensure_ascii=False))

    server = ki_attrappe.install(monkeypatch, ki_attrappe.ModelServer(reply))
    body = _crosscheck(household_headers)

    assert body["geprueft"] == 3
    assert body["meldung"] is None
    assert ["response_format" in request for request in server.bodies] == [
        True,
        False,
        False,
        False,
    ]
