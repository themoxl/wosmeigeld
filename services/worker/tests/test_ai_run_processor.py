"""Lebenszyklus eines Batch-Klassifikationslaufs (worker/ai/processor.py).

Hintergrund (docs/KI-FINANZASSISTENT-PLAN.md, Ausbaustufe 0): seit pro
Haushalt nur noch ein aktiver Lauf erlaubt ist, ist ein Lauf, der faelschlich
auf 'pending' stehen bleibt, kein Schoenheitsfehler mehr - er blockiert jeden
weiteren Start. Getestet werden deshalb genau die Uebergaenge, die das
verhindern: die Laufzeile abwarten statt still aufzugeben, sie genau einmal
uebernehmen, in jeder Buchung ein Lebenszeichen schreiben und einen
angeforderten Abbruch zwischen zwei Buchungen sauber beenden.

Wie die uebrigen Worker-Tests laeuft hier keine echte DB und kein echtes LM
Studio: die Session ist eine Attrappe, die auf den SQL-Text antwortet.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.llm import reasons
from app.llm.classification import FAILURE_REASON_CODES, CanaryResult
from app.llm.discovery import Target, TargetResult, resolve_target
from app.llm.settings import LLMSettings
from app.llm.window import WindowInfo

from worker.ai.processor import _BREAKER_REASON_CODES, process_classification_run

RUN_ID = uuid4()
HOUSEHOLD_ID = uuid4()


def _settings(**overrides: Any) -> LLMSettings:
    values: dict[str, Any] = {
        "is_enabled": True,
        "min_confidence": 0.85,
        "custom_base_url": None,
        "model_name": None,
        "temperature": 1.0,
        "request_timeout_seconds": 120.0,
        "answer_reserve_share": 0.25,
        "start_chars_per_token": 3.0,
        "start_estimate_error": 0.15,
    }
    values.update(overrides)
    return LLMSettings(**values)


_TARGET = Target(
    base_url="http://127.0.0.1:1234/v1",
    model="test-model",
    window=WindowInfo(loaded=32768, cap=None, effective=32768, source="/api/v1/models"),
)
_READY = TargetResult(target=_TARGET, base_url=_TARGET.base_url, models=[], detailed=True)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)

    def __iter__(self) -> Any:
        # load_known_payees() iteriert direkt ueber das Ergebnis.
        return iter(self._rows)


def _precedent(
    payee_label: str, category_id: Any, *, source: str = "manual"
) -> SimpleNamespace:
    """Eine bereits kategorisierte Buchung des Haushalts - Vorbild fuer den
    Vorfilter (worker/ai/known_payees.py). Die eigenen Tests des Vorfilters
    stehen in test_ai_known_payees.py; hier geht es nur darum, ob der Lauf
    einen Treffer am Modell vorbeifuehrt."""
    return SimpleNamespace(
        payee_label=payee_label,
        category_id=category_id,
        category_source=source,
        is_selectable=True,
    )


class _FakeSession:
    """Antwortet anhand des SQL-Texts. `visibility` und `cancel_flags` werden
    der Reihe nach abgearbeitet; der jeweils letzte Wert gilt danach weiter."""

    def __init__(
        self,
        *,
        visibility: list[bool] | None = None,
        claimable: bool = True,
        transaction_count: int = 2,
        cancel_flags: list[bool] | None = None,
        run_states: list[str] | None = None,
        precedents: list[SimpleNamespace] | None = None,
    ) -> None:
        self.precedents = list(precedents or [])
        self.visibility = list(visibility or [True])
        self.claimable = claimable
        self.transactions = [
            SimpleNamespace(
                id=uuid4(),
                payee_label=f"Haendler {i}",
                note=None,
                purpose_raw=f"Bankzweck {i}",
                amount_minor=-1000,
            )
            for i in range(transaction_count)
        ]
        self.cancel_flags = list(cancel_flags or [False])
        # Status der Laufzeile bei jeder Abbruchpruefung. Ein von aussen hart
        # beendeter Lauf steht hier auf 'cancelled', ohne dass die Bitte je
        # gesetzt war.
        self.run_states = list(run_states or ["running"])
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0

    @staticmethod
    def _next(values: list[Any]) -> Any:
        return values.pop(0) if len(values) > 1 else values[0]

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(getattr(statement, "text", statement))
        self.executed.append((sql, params or {}))

        if "select 1 from classification_runs" in sql:
            return _FakeResult([SimpleNamespace(anon=1)] if self._next(self.visibility) else [])
        if "set status = 'running'" in sql:
            if not self.claimable:
                return _FakeResult([])
            return _FakeResult(
                [SimpleNamespace(household_id=HOUSEHOLD_ID, since_date="2026-05-01")]
            )
        if "s.category_source = any(:precedent_sources)" in sql:
            # Die Herkunftsklausel filtert in der echten DB; hier bildet die
            # Attrappe sie nach dem uebergebenen Parameter nach, damit ein
            # KI-Vorbild auch im Lauf nachweislich nicht zaehlt.
            allowed = set((params or {}).get("precedent_sources", ()))
            return _FakeResult(
                [row for row in self.precedents if row.category_source in allowed]
            )
        if "from transactions t" in sql:
            return _FakeResult(self.transactions)
        if "select cancel_requested" in sql:
            return _FakeResult(
                [
                    SimpleNamespace(
                        cancel_requested=self._next(self.cancel_flags),
                        status=self._next(self.run_states),
                    )
                ]
            )
        return _FakeResult([])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    # --- Auswertungshilfen ---------------------------------------------------

    def statements(self, needle: str) -> list[tuple[str, dict[str, Any]]]:
        return [(sql, params) for sql, params in self.executed if needle in sql]

    def final_status(self) -> str | None:
        for sql, params in reversed(self.executed):
            if "set status = :status" in sql:
                return str(params["status"])
        return None


def _hit() -> SimpleNamespace:
    """Antwort mit gefundener Kategorie."""
    return SimpleNamespace(category_id=uuid4())


def _no_match() -> SimpleNamespace:
    """Vertragskonformes "keine passende Kategorie" - eine richtige Antwort."""
    return SimpleNamespace(category_id=None, reason_code="no_matching_category")


def _error(reason_code: str) -> SimpleNamespace:
    """Fehlgeschlagener Modellaufruf; die reason_code-Werte stammen aus
    worker/ai/classifier.py::classify_transaction."""
    return SimpleNamespace(category_id=None, reason_code=reason_code)


def _classify_ok() -> AsyncMock:
    return AsyncMock(return_value=_hit())


def _canary(passed: bool, supports_json_schema: bool) -> CanaryResult:
    if passed:
        return CanaryResult(True, supports_json_schema, None, None)
    return CanaryResult(
        False,
        False,
        reasons.MODEL_BUDGET_EXHAUSTED,
        reasons.describe(reasons.MODEL_BUDGET_EXHAUSTED),
    )


def _patch_llm(
    classify: AsyncMock,
    canary: tuple[bool, bool],
    record: AsyncMock,
    *,
    endpoint: TargetResult = _READY,
    settings: LLMSettings | None = None,
) -> Any:
    """Alles ausserhalb der Laufsteuerung ruhigstellen - Einstellungen,
    Discovery/Fenster, Vorabtest und Kategorienliste haben eigene Tests."""
    return (
        patch(
            "worker.ai.processor.load_settings",
            AsyncMock(return_value=settings or _settings()),
        ),
        patch("worker.ai.processor.resolve_lm_studio_endpoint", _endpoint(endpoint)),
        patch("worker.ai.processor.load_categories", AsyncMock(return_value=([], set()))),
        patch(
            "worker.ai.processor.probe_model_capability",
            AsyncMock(return_value=_canary(*canary)),
        ),
        patch("worker.ai.processor.record_model_capability", record),
        patch("worker.ai.processor.classify_transaction", classify),
    )


def _endpoint(endpoint: TargetResult) -> AsyncMock:
    """Zielsuche mit dem Vertrag von llm.resolve_target: bei ausgeschalteter
    KI antwortet das Original selbst (ai_disabled, ohne Serverkontakt)."""

    async def find(settings: LLMSettings) -> TargetResult:
        if not settings.is_enabled:
            return await resolve_target(settings)
        return endpoint

    return AsyncMock(side_effect=find)


async def _run(
    session: _FakeSession,
    classify: AsyncMock | None = None,
    *,
    canary: tuple[bool, bool] = (True, True),
    record: AsyncMock | None = None,
    endpoint: TargetResult = _READY,
    settings: LLMSettings | None = None,
) -> AsyncMock:
    classify = classify or _classify_ok()
    p1, p2, p3, p4, p5, p6 = _patch_llm(
        classify, canary, record or AsyncMock(), endpoint=endpoint, settings=settings
    )
    with p1, p2, p3, p4, p5, p6:
        await process_classification_run(session, RUN_ID)  # type: ignore[arg-type]
    return classify


def _error_message(session: _FakeSession) -> str:
    return str(session.statements("set status = :status")[-1][1]["err"])


async def test_run_row_is_awaited_before_giving_up() -> None:
    """Die API reiht den Job noch vor dem Commit ihrer Request-Transaktion ein.
    Wer die Laufzeile beim ersten Blick nicht findet, darf nicht aufgeben -
    sonst bleibt der Lauf fuer immer 'pending'."""
    session = _FakeSession(visibility=[False, False, True])
    with patch("worker.ai.processor.asyncio.sleep", AsyncMock()) as sleep:
        classify = await _run(session)

    assert sleep.await_count == 2
    assert classify.await_count == 2
    assert session.final_status() == "completed"


async def test_batch_sends_real_bank_purpose_to_classifier() -> None:
    session = _FakeSession(transaction_count=1)
    classify = await _run(session)

    assert classify.await_count == 1
    # Signatur: session, household, tx, payee, purpose, amount, ...
    assert classify.await_args.args[4] == "Bankzweck 0"


async def test_run_that_never_appears_is_left_untouched() -> None:
    session = _FakeSession(visibility=[False])
    with patch("worker.ai.processor.asyncio.sleep", AsyncMock()):
        classify = await _run(session)

    assert classify.await_count == 0
    assert session.statements("set status = 'running'") == []
    assert session.final_status() is None


async def test_already_claimed_run_is_not_processed_twice() -> None:
    """Doppelte Zustellung derselben Nachricht (Worker-Neustart): nur wer
    'pending' -> 'running' drehen konnte, arbeitet."""
    session = _FakeSession(claimable=False)
    classify = await _run(session)

    assert classify.await_count == 0
    assert session.final_status() is None


async def test_every_processed_transaction_writes_a_heartbeat() -> None:
    """Ohne Lebenszeichen kann die API einen abgestuerzten Lauf nicht von einem
    langsamen unterscheiden und gaebe den Haushalt nie wieder frei."""
    session = _FakeSession(transaction_count=3)
    await _run(session)

    counter_updates = session.statements("processed_count = processed_count + 1")
    assert len(counter_updates) == 3
    assert all("heartbeat_at = now()" in sql for sql, _ in counter_updates)


async def test_cancel_request_stops_the_run_between_transactions() -> None:
    """Abgebrochen wird zwischen zwei Buchungen - eine begonnene Klassifikation
    laeuft zu Ende, damit kein halber Schreibvorgang zurueckbleibt."""
    session = _FakeSession(transaction_count=3, cancel_flags=[False, True])
    classify = await _run(session)

    # Der lokale Vorfilter prueft den Abbruch bereits vor jedem Modellaufruf.
    assert classify.await_count == 0
    assert session.final_status() == "cancelled"
    cancel_update = session.statements("set status = :status")[-1]
    assert cancel_update[1]["err"] == "Vom Nutzer abgebrochen."


async def test_externally_ended_run_stops_the_loop_and_is_not_completed() -> None:
    """Sofortabbruch: die API setzt die Laufzeile direkt auf 'cancelled', ohne
    dass jemals eine Bitte gesetzt war. Der Worker darf danach weder eine
    weitere Buchung klassifizieren noch den Lauf auf 'completed' drehen -
    sonst kaeme die gerade beendete Aufgabe als erledigt zurueck."""
    session = _FakeSession(
        transaction_count=3, run_states=["running", "cancelled"]
    )
    classify = await _run(session)

    assert classify.await_count == 0
    finish = session.statements("set status = :status")[-1]
    assert finish[1]["status"] == "cancelled"
    # Der Schutz sitzt in der WHERE-Klausel: eine bereits beendete Laufzeile
    # laesst sich nicht mehr ueberschreiben.
    assert "and status = 'running'" in finish[0]


async def test_unreachable_lm_studio_fails_the_run_with_a_reason() -> None:
    session = _FakeSession()
    unreachable = TargetResult(
        target=None,
        base_url=None,
        models=[],
        detailed=False,
        reason_code=reasons.MODEL_UNAVAILABLE,
        message=reasons.describe(reasons.MODEL_UNAVAILABLE),
    )
    classify = await _run(session, endpoint=unreachable)

    assert classify.await_count == 0
    assert session.final_status() == "failed"
    assert "LM Studio" in _error_message(session)


async def test_an_unknown_window_fails_the_whole_run_once() -> None:
    """Ohne geladenes Fenster laesst sich kein Antwortbudget rechnen. Der Lauf
    scheitert deshalb EINMAL und benannt - statt 171 einzelner
    Fehlentscheidungen mit demselben Grund."""
    session = _FakeSession(transaction_count=3)
    unknown = TargetResult(
        target=Target(
            base_url=_TARGET.base_url,
            model="test-model",
            window=WindowInfo(loaded=None, cap=None, effective=None, source=None, reason="x"),
        ),
        base_url=_TARGET.base_url,
        models=[],
        detailed=True,
        reason_code=reasons.MODEL_CONTEXT_UNKNOWN,
        message=reasons.describe(reasons.MODEL_CONTEXT_UNKNOWN, "nicht geladen"),
    )
    record = AsyncMock()
    classify = await _run(session, endpoint=unknown, record=record)

    assert classify.await_count == 0
    assert record.await_count == 0
    assert session.final_status() == "failed"
    assert "Kontextfenster" in _error_message(session)
    assert "Kontext höchstens" in _error_message(session)
    assert session.statements("total_count = :total")[-1][1]["total"] == 3


async def test_disabled_ai_fails_the_run_after_the_known_payees() -> None:
    session = _FakeSession(transaction_count=2)
    classify = await _run(session, settings=_settings(is_enabled=False))

    assert classify.await_count == 0
    assert session.final_status() == "failed"
    assert "ausgeschaltet" in _error_message(session)


# --- Notbremse 1: durchgefallener Capability-Canary -------------------------


async def test_failed_capability_canary_stops_the_run_before_the_first_call() -> None:
    """Der Canary ist der einfachste denkbare Fall. Wer daran scheitert,
    scheitert an jeder echten Buchung - frueher lief der Stapel trotzdem los
    und meldete am Ende 171 Fehler. Jetzt endet der Lauf sofort mit einer
    Meldung, aus der der Nutzer eine Entscheidung ableiten kann."""
    session = _FakeSession(transaction_count=3)
    record = AsyncMock()
    classify = await _run(session, canary=(False, False), record=record)

    assert classify.await_count == 0
    assert session.final_status() == "failed"
    # Die Statusanzeige braucht den Grund - die Faehigkeit wird deshalb VOR
    # dem Abbruch geschrieben und mit dem Abbruch committet.
    assert record.await_count == 1
    aufruf = record.await_args
    assert aufruf is not None
    assert aufruf.kwargs["canary_passed"] is False
    # Der benannte Grund des Vorabtests steht auch im Faehigkeitsvermerk.
    assert "Antwortbudget" in aufruf.kwargs["last_error"]
    # Die Gesamtzahl steht trotzdem in der Laufzeile, sonst zeigte die
    # Oberflaeche "0 von 0" statt "0 von 3".
    assert session.statements("total_count = :total")[-1][1]["total"] == 3
    assert "Vorabtest" in _error_message(session)
    assert "Antwortbudget" in _error_message(session)


async def test_passed_capability_canary_still_starts_the_run() -> None:
    """Gegenprobe zur Notbremse: ein bestandener Canary darf nichts bremsen."""
    session = _FakeSession(transaction_count=2)
    classify = await _run(session, canary=(True, False))

    assert classify.await_count == 2
    assert session.final_status() == "completed"


# --- Notbremse 2: Circuit Breaker ------------------------------------------


async def test_two_identical_errors_in_a_row_stop_the_run() -> None:
    """Nach dem ersten Timeout liefen frueher ~170 weitere, absehbar
    aussichtslose Aufrufe - bei 60s Zeitbudget je Aufruf stundenlang. Zwei
    gleichartige Fehler hintereinander beenden den Lauf jetzt."""
    session = _FakeSession(transaction_count=5)
    classify = AsyncMock(
        side_effect=[_error("llm_timeout"), _error("llm_timeout"), _hit(), _hit(), _hit()]
    )
    await _run(session, classify)

    assert classify.await_count == 2
    assert session.final_status() == "failed"
    message = _error_message(session)
    # Die Meldung nennt die bereits verarbeitete Menge, damit der Nutzer
    # weiss, dass der Teilstand steht und ein Neustart den Rest holt.
    assert "2 von 5" in message
    assert "Zeitueberschreitung" in message
    # Teil-Durabilitaet: beide Zeilen wurden gezaehlt und committet, bevor
    # der Abbruch geschrieben wurde.
    assert len(session.statements("processed_count = processed_count + 1")) == 2


async def test_no_match_answers_never_trip_the_breaker() -> None:
    """no_match ist eine richtige Antwort, kein Fehler (Leitplanke 8). Ein
    Modell, das den Vertrag sauber einhaelt, darf den Lauf nicht abwuergen."""
    session = _FakeSession(transaction_count=3)
    classify = AsyncMock(side_effect=[_no_match(), _no_match(), _no_match()])
    await _run(session, classify)

    assert classify.await_count == 3
    assert session.final_status() == "completed"


async def test_breaker_only_counts_uninterrupted_errors_of_the_same_kind() -> None:
    """Verstreute oder verschiedenartige Fehler sind kein Dauerzustand: ein
    Erfolg dazwischen zeigt, dass das Modell antwortet, und zwei verschiedene
    Ursachen rechtfertigen gemeinsam keinen Abbruch."""
    session = _FakeSession(transaction_count=5)
    classify = AsyncMock(
        side_effect=[
            _error("llm_timeout"),
            _hit(),  # setzt die Kette zurueck
            _error("llm_timeout"),
            _error("llm_schema_invalid"),  # andere Ursache, zaehlt neu ab 1
            _error("llm_timeout"),
        ]
    )
    await _run(session, classify)

    assert classify.await_count == 5
    assert session.final_status() == "completed"


def test_breaker_covers_every_failure_code_of_the_classifier() -> None:
    """Die Codes, mit denen der KI-Kern einen kaputten Modellaufruf meldet,
    stehen dort als eine Liste (FAILURE_REASON_CODES). Fehlte einer im Notaus,
    bliebe der Lauf bei genau diesem Fehler stumm und liefe ins Leere."""
    assert FAILURE_REASON_CODES <= _BREAKER_REASON_CODES
    assert reasons.MODEL_BUDGET_EXHAUSTED in _BREAKER_REASON_CODES
    # Bestandscode aus der Zeit des festen Budgets bleibt erkannt.
    assert "llm_token_budget_exceeded" in _BREAKER_REASON_CODES


async def test_an_exhausted_answer_budget_trips_the_breaker_by_name() -> None:
    session = _FakeSession(transaction_count=4)
    classify = AsyncMock(
        side_effect=[
            _error(reasons.MODEL_BUDGET_EXHAUSTED),
            _error(reasons.MODEL_BUDGET_EXHAUSTED),
            _hit(),
            _hit(),
        ]
    )
    await _run(session, classify)

    assert classify.await_count == 2
    assert session.final_status() == "failed"
    assert "Antwortbudget" in _error_message(session)


# --- Vorfilter: bekannte Empfaenger gar nicht erst fragen -------------------


def _decision_inserts(session: _FakeSession) -> list[tuple[str, dict[str, Any]]]:
    return session.statements("insert into classification_decisions")


async def test_known_payee_is_proposed_without_asking_the_model() -> None:
    """Der eigentliche Zweck des Schritts: ein Empfaenger, den der Nutzer im
    Bestand bereits eindeutig kategorisiert hat, kostet keinen einzigen
    LM-Studio-Aufruf mehr. Der Vorschlag entsteht trotzdem - und die Buchung
    zaehlt als verarbeitet, sonst meldete der Lauf am Ende weniger Buchungen,
    als er erledigt hat."""
    kategorie = uuid4()
    session = _FakeSession(
        transaction_count=3,
        # "Haendler 1" ist die zweite der drei Buchungen; die Schreibweise
        # weicht ab, damit zugleich die Normalisierung im Lauf belegt ist.
        precedents=[_precedent("HAENDLER 1!", kategorie)],
    )
    classify = await _run(session)

    assert classify.await_count == 2
    assert session.final_status() == "completed"
    # Alle drei Buchungen sind gezaehlt, keine als Fehler.
    counters = session.statements("processed_count = processed_count + 1")
    assert len(counters) == 3
    assert [params["s"] for _, params in counters] == [1, 1, 1]

    # Genau ein Vorschlag stammt aus dem Bestand - mit der Herkunft, die die
    # Oberflaeche von einem Modellvorschlag unterscheidet.
    inserts = _decision_inserts(session)
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert params["source"] == "history"
    assert params["reason"] == "known_payee"
    assert params["cat_id"] == str(kategorie)
    assert "null" in sql  # model_name - es war kein Modell beteiligt


async def test_contradicting_history_still_goes_to_the_model() -> None:
    """Zwei verschiedene Kategorien zu demselben Empfaenger sind keine
    Auskunft. Der Vorfilter schweigt, statt sich fuer eine zu entscheiden."""
    session = _FakeSession(
        transaction_count=2,
        precedents=[
            _precedent("Haendler 0", uuid4()),
            _precedent("haendler 0", uuid4()),
        ],
    )
    classify = await _run(session)

    assert classify.await_count == 2
    assert _decision_inserts(session) == []


async def test_ai_written_history_never_shortcuts_the_model() -> None:
    """Die wichtigste Sicherung: haette die KI selbst diesen Empfaenger
    zugeordnet, wuerde der Vorfilter ihren Fehler bei jeder weiteren Buchung
    desselben Empfaengers wiederholen - ohne dass das Modell je die Chance
    bekaeme, es besser zu machen."""
    session = _FakeSession(
        transaction_count=2,
        precedents=[_precedent("Haendler 0", uuid4(), source="ai")],
    )
    classify = await _run(session)

    assert classify.await_count == 2
    assert _decision_inserts(session) == []


async def test_known_payee_does_not_reset_the_error_streak() -> None:
    """Eine Buchung, die das Modell nie erreicht hat, sagt nichts darueber
    aus, ob es antwortet. Wuerde sie die Fehlerkette loeschen, koennte ein
    einzelner bekannter Empfaenger zwischen zwei Timeouts den Notaus
    aushebeln - der Lauf liefe wieder stundenlang ins Leere."""
    session = _FakeSession(
        transaction_count=4,
        precedents=[_precedent("Haendler 1", uuid4())],
    )
    classify = AsyncMock(side_effect=[_error("llm_timeout"), _error("llm_timeout"), _hit()])
    await _run(session, classify)

    # Buchung 1: Timeout. Buchung 2: Vorfilter. Buchung 3: Timeout -> Abbruch.
    assert classify.await_count == 2
    assert session.final_status() == "failed"
    assert "Zeitueberschreitung" in _error_message(session)
    assert "3 von 4" in _error_message(session)


async def test_stale_category_is_an_error_but_no_reason_to_stop() -> None:
    """Eine seit dem Laden der Kandidaten archivierte Kategorie zaehlt als
    Fehler, sagt aber nichts ueber die Erreichbarkeit des Modells aus - der
    Abbruch soll nur eine Kette kaputter Modellaufrufe stoppen."""
    session = _FakeSession(transaction_count=3)
    classify = AsyncMock(
        side_effect=[
            _error("category_no_longer_active"),
            _error("category_no_longer_active"),
            _error("category_no_longer_active"),
        ]
    )
    await _run(session, classify)

    assert classify.await_count == 3
    assert session.final_status() == "completed"
    # Als Fehler gezaehlt wurden sie trotzdem.
    counters = session.statements("error_count = error_count + :e")
    assert [params["e"] for _, params in counters] == [1, 1, 1]
