"""Vorfilter fuer bekannte Empfaenger (worker/ai/known_payees.py).

Geprueft wird genau das, was den Vorfilter von einer Heuristik unterscheidet:
er nimmt NUR menschliche oder regelbasierte Vorbilder, NUR eindeutige, und er
vergleicht exakt statt aehnlich. Jede dieser drei Regeln hat hier ihren Test -
faellt eine weg, entsteht entweder eine Rueckkopplung auf eigene Fehler oder
ein Rateschritt ohne Modell.

Wie in den uebrigen Worker-Tests laeuft keine echte DB: die Session ist eine
Attrappe. Sie bildet die WHERE-Klausel auf `category_source` bewusst nach dem
Parameter nach, den der Produktivcode uebergibt, und besteht darauf, dass die
Abfrage die Klausel auch wirklich enthaelt - sonst waere der wichtigste Test
dieser Datei (KI-Vorbilder zaehlen nicht) gruen, ohne etwas zu belegen.
"""

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from worker.ai.known_payees import (
    _PRECEDENT_SOURCES,
    KNOWN_PAYEE_CONFIDENCE,
    KNOWN_PAYEE_REASON_CODE,
    KNOWN_PAYEE_SOURCE,
    _payee_compare_key,
    classify_known_payee,
    load_known_payees,
)

HOUSEHOLD_ID = uuid4()
TRANSACTION_ID = uuid4()
LEBENSMITTEL = uuid4()
GESCHENKE = uuid4()

# Die Klausel, ohne die der Vorfilter auf eigene KI-Ausgaben hoeren wuerde.
_SOURCE_FILTER_SQL = "s.category_source = any(:precedent_sources)"


def _precedent(
    payee_label: str,
    category_id: UUID,
    *,
    source: str = "manual",
    selectable: bool = True,
) -> SimpleNamespace:
    """Eine bereits kategorisierte Buchung des Haushalts, so wie sie
    _PRECEDENT_SQL liefert (`category_source` zusaetzlich, damit die Attrappe
    die WHERE-Klausel nachbilden kann)."""
    return SimpleNamespace(
        payee_label=payee_label,
        category_id=category_id,
        category_source=source,
        is_selectable=selectable,
    )


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeSession:
    def __init__(self, precedents: list[SimpleNamespace] | None = None) -> None:
        self.precedents = list(precedents or [])
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = str(getattr(statement, "text", statement))
        self.executed.append((sql, params or {}))
        if "insert into" in sql:
            return _Rows([])
        assert _SOURCE_FILTER_SQL in sql, "Vorbildabfrage ohne Herkunftsfilter"
        allowed = set((params or {}).get("precedent_sources", ()))
        return _Rows([row for row in self.precedents if row.category_source in allowed])

    def inserts(self) -> list[tuple[str, dict[str, Any]]]:
        return [(sql, params) for sql, params in self.executed if "insert into" in sql]


async def _index(precedents: list[SimpleNamespace]) -> Any:
    return await load_known_payees(_FakeSession(precedents), HOUSEHOLD_ID)  # type: ignore[arg-type]


# --- Regel 3: exakter Vergleich --------------------------------------------


def test_compare_key_normalises_only_case_and_punctuation() -> None:
    """Wortgleich zu rules_service._recurring_payee_key: Schreibweise und
    Satzzeichen fallen weg, mehr nicht."""
    assert _payee_compare_key("REWE SAGT DANKE. 12345") == "rewe sagt danke 12345"
    assert _payee_compare_key("Rewe  Sagt/Danke, 12345") == "rewe sagt danke 12345"
    # Umlaute bleiben erhalten (\w mit UNICODE), sie sind Teil des Namens.
    assert _payee_compare_key("Bäckerei Müller") == "bäckerei müller"


def test_compare_key_does_not_merge_similar_payees() -> None:
    """Aehnlichkeit ist kein Wissen. Wer hier zusammenlegt, raet - genau das
    soll der Vorfilter dem Modell ueberlassen."""
    assert _payee_compare_key("REWE") != _payee_compare_key("REWE Getraenkemarkt")


def test_empty_payee_never_matches() -> None:
    assert _payee_compare_key(None) == ""
    assert _payee_compare_key("   ") == ""


async def test_known_payee_is_found_across_spelling_differences() -> None:
    known = await _index([_precedent("REWE SAGT DANKE. 12345", LEBENSMITTEL)])

    assert known.lookup("rewe sagt danke 12345") == LEBENSMITTEL
    assert known.lookup("REWE Getraenkemarkt") is None


# --- Regel 1: keine KI-Vorbilder -------------------------------------------


def test_ai_and_history_are_not_precedent_sources() -> None:
    """Die wichtigste Sicherung als Konstante: 'ai' waere eine Rueckkopplung
    auf die eigenen Fehler, 'history' die Herkunft dieses Vorfilters selbst -
    ein Vorschlag darf sich nicht zum eigenen Beleg erklaeren."""
    assert "ai" not in _PRECEDENT_SOURCES
    assert "history" not in _PRECEDENT_SOURCES
    assert "unknown" not in _PRECEDENT_SOURCES
    assert set(_PRECEDENT_SOURCES) == {"manual", "rule"}


async def test_ai_written_precedent_is_ignored() -> None:
    """Von der KI gesetzte Zuordnungen taugen nicht als Vorbild: sonst
    zementiert der Vorfilter jeden Fehltreffer des Modells."""
    session = _FakeSession([_precedent("Foto Eisele", LEBENSMITTEL, source="ai")])
    known = await load_known_payees(session, HOUSEHOLD_ID)  # type: ignore[arg-type]

    assert known.category_by_key == {}
    assert known.lookup("Foto Eisele") is None
    # Und der Grund steht in der Abfrage, nicht nur in der Attrappe.
    assert _SOURCE_FILTER_SQL in session.executed[0][0]
    assert session.executed[0][1]["precedent_sources"] == list(_PRECEDENT_SOURCES)


async def test_manual_rule_import_and_contract_precedents_all_count() -> None:
    for source in _PRECEDENT_SOURCES:
        known = await _index([_precedent("Netflix", LEBENSMITTEL, source=source)])
        assert known.lookup("Netflix") == LEBENSMITTEL, source


# --- Regel 2: Eindeutigkeit -------------------------------------------------


async def test_contradicting_precedents_are_left_to_the_model() -> None:
    """Amazon als Haushalt UND als Geschenk: der Bestand weiss es nicht besser
    als das Modell und schweigt deshalb."""
    known = await _index(
        [
            _precedent("AMAZON.DE", LEBENSMITTEL),
            _precedent("Amazon.de", GESCHENKE),
        ]
    )

    assert known.lookup("amazon de") is None
    assert known.ambiguous_keys == frozenset({"amazon de"})


async def test_repeated_identical_precedents_stay_unambiguous() -> None:
    known = await _index([_precedent("REWE", LEBENSMITTEL) for _ in range(5)])

    assert known.lookup("REWE") == LEBENSMITTEL
    assert known.ambiguous_keys == frozenset()


async def test_archived_category_is_not_proposed() -> None:
    """Ein Vorschlag auf eine nicht mehr waehlbare Kategorie waere Arbeit fuer
    den Papierkorb - die Buchung geht stattdessen ans Modell."""
    known = await _index([_precedent("REWE", LEBENSMITTEL, selectable=False)])

    assert known.category_by_key == {}


async def test_ambiguity_is_decided_before_archived_categories_drop_out() -> None:
    """Sonst entstuende aus zwei widersprechenden Vorbildern durch das
    Archivieren des einen ein scheinbar eindeutiger Fall - der Widerspruch
    waere weg, ohne dass ihn jemand aufgeloest haette."""
    known = await _index(
        [
            _precedent("Amazon", LEBENSMITTEL, selectable=False),
            _precedent("Amazon", GESCHENKE),
        ]
    )

    assert known.lookup("Amazon") is None
    assert known.ambiguous_keys == frozenset({"amazon"})


# --- Der Vorschlag selbst ---------------------------------------------------


async def test_unknown_payee_writes_nothing_and_returns_none() -> None:
    """Kein Treffer heisst: keine Zeile, keine Aussage - der Aufrufer fragt
    danach wie bisher das Modell."""
    session = _FakeSession()
    known = await _index([_precedent("REWE", LEBENSMITTEL)])

    result = await classify_known_payee(
        session,  # type: ignore[arg-type]
        HOUSEHOLD_ID,
        TRANSACTION_ID,
        "REWE sagt Danke",
        known,
    )

    assert result is None
    assert session.inserts() == []


async def test_hit_writes_history_decision_with_null_model_name() -> None:
    session = _FakeSession()
    known = await _index([_precedent("REWE SAGT DANKE", LEBENSMITTEL)])

    result = await classify_known_payee(
        session,  # type: ignore[arg-type]
        HOUSEHOLD_ID,
        TRANSACTION_ID,
        "rewe sagt danke",
        known,
        None,
    )

    assert result is not None
    assert result.category_id == LEBENSMITTEL
    assert result.source == KNOWN_PAYEE_SOURCE == "history"
    assert result.reason_code == KNOWN_PAYEE_REASON_CODE == "known_payee"
    assert result.model_name is None
    assert result.confidence == KNOWN_PAYEE_CONFIDENCE

    inserts = session.inserts()
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "insert into classification_decisions" in sql
    # Kein Modell beteiligt: weder ein Modellname noch eine llm_calls-Zeile.
    assert "model_name" in sql and "null" in sql
    assert "llm_calls" not in sql
    assert params["source"] == "history"
    assert params["reason"] == "known_payee"
    assert params["cat_id"] == str(LEBENSMITTEL)
    assert params["tx_id"] == str(TRANSACTION_ID)
    assert params["payee_name"] == "rewe sagt danke"
    assert params["ai_task_id"] is None


async def test_confidence_clears_the_default_acceptance_threshold() -> None:
    """Ein Vorschlag unterhalb von auto_categorize_min_confidence (Vorgabe
    0.85) waere im selben Atemzug erzeugt und unbrauchbar gemacht - 'Alle
    sicheren uebernehmen' wuerde ihn nie anfassen. 1.0 waere andererseits eine
    behauptete Gewissheit, die die Datenlage nicht hergibt."""
    assert 0.85 <= KNOWN_PAYEE_CONFIDENCE < 1.0


async def test_task_id_is_passed_through_to_the_decision() -> None:
    session = _FakeSession()
    known = await _index([_precedent("REWE", LEBENSMITTEL)])
    task_id = uuid4()

    await classify_known_payee(
        session,  # type: ignore[arg-type]
        HOUSEHOLD_ID,
        TRANSACTION_ID,
        "REWE",
        known,
        task_id,
    )

    assert session.inserts()[0][1]["ai_task_id"] == str(task_id)
