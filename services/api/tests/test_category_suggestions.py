"""KI-Kategorievorschlaege (docs/KATEGORISIERUNGSPLAN.md, Entscheidung 8
"KI darf vorschlagen, nicht erfinden").

Ersetzt ist nur der Modellserver (tests/ki_modell_attrappe.py), alles andere
laeuft echt: Clustering ueber den Buchungsbestand, der Gateway des KI-Kerns
samt llm_calls-Zeile, Deduplizierung gegen vorhandene Kategorien, das Anlegen
der bestaetigten Kategorien und die Zuordnung der Buchungen. Genau dort sitzen
die Zusicherungen, die ein Mock nicht nachweisen koennte.
"""

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services import category_suggestion_service
from app.services.ai_answers import AIStepError
from tests.ki_modell_attrappe import (
    MODEL,
    ModelStub,
    answer,
    llm_calls,
    server_error,
    unavailable_target,
    user_prompt_of,
)
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


def _household_headers(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    household = client.post(
        "/v1/households",
        json={"name": "Vorschlags-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def headers() -> dict[str, str]:
    return _household_headers("sugg")


@pytest.fixture()
def account_id(headers: dict[str, str]) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return str(account["id"])


def _booking(
    headers: dict[str, str],
    account_id: str,
    payee: str,
    *,
    booked_date: str = "2026-07-01",
    amount_minor: int = -2100,
) -> str:
    response = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": booked_date,
            "amount_minor": amount_minor,
            "payee_label": payee,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _group_id(headers: dict[str, str], name: str) -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        if group["name"] == name:
            return str(group["id"])
    raise AssertionError(f"Gruppe '{name}' nicht gefunden")


def _stub_model(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, dict[str, Any] | httpx.Response]
) -> ModelStub:
    """Ersetzt den Modellserver. `answers` bildet den Empfaenger im
    Nutzerprompt auf die Antwort ab (ein dict ist der JSON-Inhalt einer
    regulaeren Antwort); ein unbekannter Empfaenger bekommt einen Serverfehler.
    `stub.user_prompts()` sind die tatsaechlich gestellten Anfragen."""

    def reply(body: dict[str, Any]) -> httpx.Response:
        prompt = user_prompt_of(body)
        for needle, value in answers.items():
            if needle in prompt:
                return value if isinstance(value, httpx.Response) else answer(value)
        return server_error()

    return ModelStub(monkeypatch, reply)


def _household_id(headers: dict[str, str]) -> str:
    return headers["X-Household-Id"]


def _new_answer(group_id: str, name: str, **overrides: Any) -> dict[str, Any]:
    return {
        "decision": "new",
        "category_id": "no_match",
        "new_name": name,
        "group_id": group_id,
        "default_type": "expense",
        "budget_bucket": "needs",
        "is_tax_deductible": True,
        "synonyms": ["saas", "lizenz"],
        "confidence": 0.9,
        "reason": "Wiederkehrendes Software-Abo ohne passende Kategorie.",
        **overrides,
    }


# ---------------------------------------------------------------------------
# Clusterschluessel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Rechtsform und Zahlungsrauschen duerfen denselben Haendler nicht
        # auf mehrere Cluster verteilen.
        ("REWE Markt GmbH", "REWE MARKT"),
        ("ALDI SUED 1234", "Aldi Sued"),
        ("LASTSCHRIFT ANTHROPIC PBC", "Anthropic PBC"),
    ],
)
def test_cluster_key_merges_the_same_merchant(left: str, right: str) -> None:
    assert category_suggestion_service.cluster_key(left) == (
        category_suggestion_service.cluster_key(right)
    )


def test_cluster_key_keeps_different_merchants_apart() -> None:
    """Bewusst keine Fuzzy-Aehnlichkeit: ein zu grosszuegiger Schluessel wuerde
    zwei Haendler zu einer erfundenen Kategorie verschmelzen."""
    assert category_suggestion_service.cluster_key("Stadtwerke Hamburg") != (
        category_suggestion_service.cluster_key("Stadtwerke Musterstadt")
    )
    assert category_suggestion_service.cluster_key("") == ""


@pytest.mark.parametrize("name", ["Sonstiges", "diverses", "ALLGEMEIN", "Misc", "x"])
def test_collector_names_are_rejected(name: str) -> None:
    """Der Prompt verbietet Sammelbegriffe - hier wird es durchgesetzt."""
    assert not category_suggestion_service._is_usable_new_name(name)


def test_concrete_names_are_accepted() -> None:
    assert category_suggestion_service._is_usable_new_name("Software & Online-Dienste")


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------


def test_single_bookings_never_become_a_category(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planentscheidung 8: "niemals aus einer einzelnen Buchung automatisch
    erzeugt". Ein Einzelbeleg wird gar nicht erst gefragt."""
    _booking(headers, account_id, "EINMALIGER HAENDLER")
    stub = _stub_model(monkeypatch, {})

    preview = client.post("/v1/categories/suggestions/preview", headers=headers)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["uncategorized_transactions"] == 1
    assert body["skipped_singletons"] == 1
    assert body["suggestions"] == []
    # Ohne Cluster gibt es nichts zu fragen - auch keine Suche nach dem Modell.
    assert stub.bodies == []
    assert stub.discoveries == 0
    assert body["model_name"] is None


def test_preview_proposes_a_missing_category_for_a_recurring_merchant(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for day in ("2026-05-01", "2026-06-01", "2026-07-01"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day, amount_minor=-2100)
    work_group = _group_id(headers, "Beruf & Arbeitsmittel")
    stub = _stub_model(monkeypatch, {"ANTHROPIC": _new_answer(work_group, "KI-Werkzeuge")})

    preview = client.post("/v1/categories/suggestions/preview", headers=headers)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["clustered_transactions"] == 3
    assert len(body["suggestions"]) == 1
    assert body["model_name"] == MODEL
    assert body["unassessed_clusters"] == 0
    assert body["warnings"] == []
    # Gateway: strukturierte Ausgabe angefordert, Budget aus dem Fenster statt
    # einer Zahl im Code.
    sent = stub.bodies[0]
    assert sent["response_format"]["json_schema"]["name"] == "category_suggestion"
    assert sent["max_tokens"] > 0

    suggestion = body["suggestions"][0]
    assert suggestion["kind"] == "new_category"
    assert suggestion["proposed_name"] == "KI-Werkzeuge"
    assert suggestion["proposed_group_id"] == work_group
    assert suggestion["proposed_is_tax_deductible"] is True
    assert suggestion["transaction_count"] == 3
    assert suggestion["total_amount_minor"] == -6300
    assert suggestion["first_booked_date"] == "2026-05-01"
    assert suggestion["last_booked_date"] == "2026-07-01"


def test_a_proposal_that_already_exists_becomes_an_assignment(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deduplizierung gegen den Bestand: schlaegt das Modell einen Namen vor,
    den es schon gibt, entsteht keine zweite fast gleiche Kategorie."""
    for day in ("2026-06-02", "2026-07-02"):
        _booking(headers, account_id, "DB VERTRIEB GMBH", booked_date=day)
    mobility = _group_id(headers, "Mobilität")
    _stub_model(monkeypatch, {"DB VERTRIEB": _new_answer(mobility, "ÖPNV & Bahn")})

    body = client.post("/v1/categories/suggestions/preview", headers=headers).json()
    suggestion = body["suggestions"][0]
    assert suggestion["kind"] == "existing_category"
    assert suggestion["existing_category_name"] == "ÖPNV & Bahn"
    assert suggestion["proposed_name"] is None


def test_preview_needs_a_reachable_model(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Modell ist eine leere Vorschlagsliste die Aussage "es fehlt
    nichts" - und damit schlicht falsch. Deshalb 503 mit benanntem Grund."""
    for day in ("2026-06-03", "2026-07-03"):
        _booking(headers, account_id, "IRGENDEIN HAENDLER", booked_date=day)
    stub = ModelStub(monkeypatch, lambda body: answer({}), target=unavailable_target())

    response = client.post("/v1/categories/suggestions/preview", headers=headers)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "model_unavailable"
    assert "nicht erreichbar" in detail["message"]
    assert stub.bodies == []


# ---------------------------------------------------------------------------
# Benannte Fehlschlaege statt leerer Vorschau (Befund K2)
# ---------------------------------------------------------------------------


async def test_model_errors_are_a_named_failure_not_an_empty_preview(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frueher: jeder Fehlschlag -> None -> Vorschau mit model_name und null
    Vorschlaegen, also "es fehlt nichts". Jetzt: 503 mit dem Grund des Kerns,
    und der gescheiterte Aufruf steht in llm_calls (die Fehlerantwort wird
    zurueckgegeben, nicht geworfen - sonst rollte das Protokoll mit zurueck).
    Ein HTTP-Fehler beschreibt den Server, nicht den Cluster: der zweite
    Cluster wird gar nicht mehr gefragt."""
    for day in ("2026-05-09", "2026-06-09"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
    for day in ("2026-05-10", "2026-06-10"):
        _booking(headers, account_id, "BLUMEN MUELLER", booked_date=day)
    stub = _stub_model(monkeypatch, {})  # jeder Cluster: HTTP 500

    response = client.post("/v1/categories/suggestions/preview", headers=headers)
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "model_http_error"
    assert len(stub.bodies) == 1

    calls = await llm_calls(_household_id(headers), "category_suggest")
    assert [(call["status"], call["reason_code"]) for call in calls] == [
        ("error", "model_http_error")
    ]
    assert calls[0]["prompt_version"] == category_suggestion_service.PROMPT_VERSION


async def test_unusable_answers_for_every_cluster_fail_with_their_reason(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine unbrauchbare Antwort betrifft nur ihren Cluster - die anderen
    werden weiter gefragt. Bekommt aber KEIN Cluster etwas Verwertbares, ist
    das ein Fehlschlag (model_output_unusable), keine leere Vorschau; die
    Protokollzeilen sind als verworfen markiert."""
    for day in ("2026-05-11", "2026-06-11"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
    for day in ("2026-05-12", "2026-06-12"):
        _booking(headers, account_id, "BLUMEN MUELLER", booked_date=day)
    stub = _stub_model(
        monkeypatch,
        {
            "ANTHROPIC": answer("Ich denke, das ist Software."),
            # Vertragsbruch: Sammelname statt konkreter Kategorie.
            "BLUMEN": _new_answer(_group_id(headers, "Freizeit, Konsum & Reisen"), "Sonstiges"),
        },
    )

    response = client.post("/v1/categories/suggestions/preview", headers=headers)
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "model_output_unusable"
    assert "Keiner der 2 Cluster" in detail["message"]
    assert len(stub.bodies) == 2

    calls = await llm_calls(_household_id(headers), "category_suggest")
    assert [(call["status"], call["reason_code"]) for call in calls] == [
        ("error", "model_output_unusable"),
        ("error", "model_output_unusable"),
    ]


def test_a_partial_run_names_the_unassessed_clusters(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Teilergebnis: der brauchbare Vorschlag bleibt, der andere Cluster ist
    als nicht beurteilt gezaehlt und mit Grund benannt - ein halber Lauf
    sieht nicht wie ein ganzer aus."""
    for day in ("2026-05-13", "2026-06-13"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
    for day in ("2026-05-14", "2026-06-14"):
        _booking(headers, account_id, "BLUMEN MUELLER", booked_date=day)
    _stub_model(
        monkeypatch,
        {
            "ANTHROPIC": _new_answer(_group_id(headers, "Beruf & Arbeitsmittel"), "KI-Werkzeuge"),
            "BLUMEN": answer("kein JSON"),
        },
    )

    response = client.post("/v1/categories/suggestions/preview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["proposed_name"] for item in body["suggestions"]] == ["KI-Werkzeuge"]
    assert body["unassessed_clusters"] == 1
    assert [warning["code"] for warning in body["warnings"]] == ["model_output_unusable"]
    assert "1 von 2 Clustern" in body["warnings"][0]["message"]


async def test_a_truncated_answer_is_budget_exhausted_and_ends_the_run(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finish_reason=length: die Antwort ist unvollstaendig, auch wenn sie wie
    JSON beginnt. Das ist ein Zustand der Einstellung (Denken frisst das
    Budget), kein Zufall des Clusters - kein zweiter Cluster, kein
    automatisches "ohne Denken" (E1)."""
    for day in ("2026-05-15", "2026-06-15"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
    for day in ("2026-05-16", "2026-06-16"):
        _booking(headers, account_id, "BLUMEN MUELLER", booked_date=day)
    truncated = answer(
        '{"decision": "new", "category_id": "no_match", "new_na', finish_reason="length"
    )
    stub = _stub_model(monkeypatch, {"ANTHROPIC": truncated, "BLUMEN": truncated})

    response = client.post("/v1/categories/suggestions/preview", headers=headers)
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "model_budget_exhausted"
    assert len(stub.bodies) == 1
    # Gesendet wurde einmal, mit dem Haushalts-Reasoning (hier: keins) - kein
    # zweiter Aufruf mit erzwungenem reasoning_effort.
    assert "reasoning_effort" not in stub.bodies[0]

    calls = await llm_calls(_household_id(headers), "category_suggest")
    assert [(c["status"], c["reason_code"], c["finish_reason"]) for c in calls] == [
        ("error", "model_budget_exhausted", "length")
    ]


def test_ai_switched_off_is_named_and_never_contacts_the_server(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for day in ("2026-05-17", "2026-06-17"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
    stub = _stub_model(monkeypatch, {})
    switched = client.put("/v1/ai/settings", json={"is_enabled": False}, headers=headers)
    assert switched.status_code == 200, switched.text

    response = client.post("/v1/categories/suggestions/preview", headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ai_disabled"
    assert stub.discoveries == 0
    assert stub.bodies == []


async def test_a_cancelled_task_stops_between_clusters(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Als KI-Aufgabe (Worker) wird vor jedem Cluster nach dem Abbruch
    gefragt: der laufende Aufruf endet regulaer, danach fragt niemand mehr.
    Die llm_calls-Zeile traegt die Aufgabe."""
    for day in ("2026-05-18", "2026-06-18"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day, amount_minor=-9000)
    for day in ("2026-05-19", "2026-06-19"):
        _booking(headers, account_id, "BLUMEN MUELLER", booked_date=day)
    work_group = _group_id(headers, "Beruf & Arbeitsmittel")
    stub = _stub_model(monkeypatch, {"ANTHROPIC": _new_answer(work_group, "KI-Werkzeuge")})
    household_id = uuid.UUID(_household_id(headers))
    task_id = uuid.uuid4()
    checks: list[int] = []

    async def cancelled_after_first() -> bool:
        checks.append(len(stub.bodies))
        return len(stub.bodies) >= 1

    async with get_session_factory()() as session:
        await session.execute(
            text(
                "insert into ai_tasks (id, household_id, task_type, status, route_path) "
                "values (:id, :household_id, 'category_suggestions', 'running', '/kategorien')"
            ),
            {"id": str(task_id), "household_id": str(household_id)},
        )
        preview = await category_suggestion_service.build_preview(
            session,
            household_id,
            None,
            ai_task_id=task_id,
            is_cancelled=cancelled_after_first,
        )
        ai_task_ids = (
            await session.execute(
                text(
                    "select ai_task_id from llm_calls where household_id = :household_id "
                    "and stage = 'category_suggest'"
                ),
                {"household_id": str(household_id)},
            )
        ).scalars().all()
        await session.rollback()

    assert checks == [0, 1]
    assert len(stub.bodies) == 1
    assert [item.proposed_name for item in preview.suggestions] == ["KI-Werkzeuge"]
    assert preview.unassessed_clusters == 1
    assert [warning.code for warning in preview.warnings] == ["model_cancelled"]
    assert [str(value) for value in ai_task_ids] == [str(task_id)]


async def test_the_unreachable_model_raises_the_named_error_in_the_service(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Dienst selbst (Worker-Weg) wirft den benannten Fehler, damit die
    KI-Aufgabe mit Klartext scheitert statt eine leere Vorschau abzulegen."""
    for day in ("2026-05-20", "2026-06-20"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
    ModelStub(monkeypatch, lambda body: answer({}), target=unavailable_target())

    async with get_session_factory()() as session:
        with pytest.raises(AIStepError) as raised:
            await category_suggestion_service.build_preview(
                session, uuid.UUID(_household_id(headers)), None
            )
        await session.rollback()
    assert raised.value.reason_code == "model_unavailable"
    assert raised.value.status_code == 503


# ---------------------------------------------------------------------------
# Anwenden
# ---------------------------------------------------------------------------


def test_apply_creates_the_category_and_assigns_the_cluster(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    transaction_ids = [
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
        for day in ("2026-05-04", "2026-06-04", "2026-07-04")
    ]
    work_group = _group_id(headers, "Beruf & Arbeitsmittel")
    _stub_model(monkeypatch, {"ANTHROPIC": _new_answer(work_group, "KI-Werkzeuge")})

    preview = client.post("/v1/categories/suggestions/preview", headers=headers).json()
    cluster_key = preview["suggestions"][0]["cluster_key"]

    applied = client.post(
        "/v1/categories/suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "request_id": "vorschlag-1",
            "assign_transactions": True,
            "accepted": [{"cluster_key": cluster_key}],
        },
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["created_categories"] == 1
    assert result["assigned_transactions"] == 3
    assert result["skipped_transactions"] == 0

    # Die Kategorie ist im Baum, traegt die Steuerrelevanz und die Synonyme.
    created = next(
        category
        for group in client.get("/v1/categories", headers=headers).json()
        for category in group["categories"]
        if category["name"] == "KI-Werkzeuge"
    )
    assert created["is_tax_deductible"] is True
    assert "saas" in created["synonyms"]

    # Und die Buchungen haengen wirklich daran.
    for transaction_id in transaction_ids:
        detail = client.get(f"/v1/transactions/{transaction_id}", headers=headers).json()
        assert detail["splits"][0]["category_id"] == created["id"]

    # Wiederholung mit derselben request_id liefert das gespeicherte Ergebnis.
    replay = client.post(
        "/v1/categories/suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "request_id": "vorschlag-1",
            "assign_transactions": True,
            "accepted": [{"cluster_key": cluster_key}],
        },
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True


def test_apply_only_touches_confirmed_clusters(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nicht bestaetigte Vorschlaege bleiben folgenlos - das ist der ganze
    Unterschied zwischen "vorschlagen" und "erfinden"."""
    for day in ("2026-05-05", "2026-06-05"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
    for day in ("2026-05-06", "2026-06-06"):
        _booking(headers, account_id, "BLUMEN MUELLER", booked_date=day)

    work_group = _group_id(headers, "Beruf & Arbeitsmittel")
    lifestyle = _group_id(headers, "Freizeit, Konsum & Reisen")
    _stub_model(
        monkeypatch,
        {
            "ANTHROPIC": _new_answer(work_group, "KI-Werkzeuge"),
            "BLUMEN": _new_answer(lifestyle, "Blumen & Pflanzen"),
        },
    )

    preview = client.post("/v1/categories/suggestions/preview", headers=headers).json()
    assert len(preview["suggestions"]) == 2
    accepted = next(
        item for item in preview["suggestions"] if item["proposed_name"] == "KI-Werkzeuge"
    )

    applied = client.post(
        "/v1/categories/suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "assign_transactions": True,
            "accepted": [{"cluster_key": accepted["cluster_key"]}],
        },
        headers=headers,
    )
    assert applied.status_code == 200
    assert applied.json()["created_categories"] == 1

    names = [
        category["name"]
        for group in client.get("/v1/categories", headers=headers).json()
        for category in group["categories"]
    ]
    assert "KI-Werkzeuge" in names
    assert "Blumen & Pflanzen" not in names


def test_apply_respects_the_users_overrides(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Vorschlag ist ein Entwurf: Name, Gruppe, Budgetbereich und
    Steuerrelevanz sind ueberschreibbar."""
    for day in ("2026-05-07", "2026-06-07"):
        _booking(headers, account_id, "ANTHROPIC PBC", booked_date=day)
    work_group = _group_id(headers, "Beruf & Arbeitsmittel")
    lifestyle = _group_id(headers, "Freizeit, Konsum & Reisen")
    _stub_model(monkeypatch, {"ANTHROPIC": _new_answer(work_group, "KI-Werkzeuge")})

    preview = client.post("/v1/categories/suggestions/preview", headers=headers).json()
    applied = client.post(
        "/v1/categories/suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "assign_transactions": False,
            "accepted": [
                {
                    "cluster_key": preview["suggestions"][0]["cluster_key"],
                    "name": "Digitale Werkzeuge",
                    "group_id": lifestyle,
                    "budget_bucket": "wants",
                    "is_tax_deductible": False,
                }
            ],
        },
        headers=headers,
    ).json()
    assert applied["created_categories"] == 1
    assert applied["assigned_transactions"] == 0

    created = next(
        category
        for group in client.get("/v1/categories", headers=headers).json()
        for category in group["categories"]
        if category["name"] == "Digitale Werkzeuge"
    )
    assert created["group_id"] == lifestyle
    assert created["budget_bucket"] == "wants"
    assert created["is_tax_deductible"] is False


def test_already_categorized_bookings_are_not_overwritten(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwischen Vorschau und Anwenden selbst zugeordnete Buchungen bleiben,
    wie sie sind - gezaehlt werden sie als uebersprungen."""
    first = _booking(headers, account_id, "ANTHROPIC PBC", booked_date="2026-05-08")
    _booking(headers, account_id, "ANTHROPIC PBC", booked_date="2026-06-08")
    work_group = _group_id(headers, "Beruf & Arbeitsmittel")
    _stub_model(monkeypatch, {"ANTHROPIC": _new_answer(work_group, "KI-Werkzeuge")})

    preview = client.post("/v1/categories/suggestions/preview", headers=headers).json()

    existing_category = next(
        category
        for group in client.get("/v1/categories", headers=headers).json()
        for category in group["categories"]
        if category["name"] == "ÖPNV & Bahn"
    )
    manual = client.put(
        f"/v1/transactions/{first}/category",
        json={"category_id": existing_category["id"]},
        headers=headers,
    )
    assert manual.status_code == 200, manual.text

    applied = client.post(
        "/v1/categories/suggestions/apply",
        json={
            "operation_id": preview["operation_id"],
            "assign_transactions": True,
            "accepted": [{"cluster_key": preview["suggestions"][0]["cluster_key"]}],
        },
        headers=headers,
    ).json()
    assert applied["assigned_transactions"] == 1
    assert applied["skipped_transactions"] == 1

    detail = client.get(f"/v1/transactions/{first}", headers=headers).json()
    assert detail["splits"][0]["category_id"] == existing_category["id"]
