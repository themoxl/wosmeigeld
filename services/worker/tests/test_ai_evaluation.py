"""Evaluationssuite fuer die KI-Kategorisierung (Architekturplan Abschnitt 15).

Anders als die Mechanik-Tests in test_ai_classifier_logic.py misst diese Suite
eine TREFFERQUOTE: sie schickt einen festen, versionierten Datensatz mit
realistischen deutschen Buchungstexten (fixtures/ai_eval_dataset_v1.json)
durch die echte Pipeline aus worker/ai/classifier.py und prueft das Ergebnis
gegen eine Mindestschwelle.

Warum ohne laufendes LM Studio: statt eines echten Modells antwortet ein
deterministischer Fake-Client (_DeterministicFakeModel). Er sieht ausschliess-
lich das, was die Pipeline wirklich sendet - also den fertigen, PII-maskierten
Prompt - und entscheidet ueber ein festes, vom Datensatz UNABHAENGIGES
Haendler-Lexikon. Dadurch ist der Test im CI reproduzierbar gruen und misst
trotzdem etwas Echtes: bricht die Prompt-Konstruktion, die Maskierung (zu
aggressiv -> Signal weg), die Schema-Validierung oder das Kategorien-Mapping,
faellt die Trefferquote messbar unter die Schwelle.

Der Fake ist bewusst unvollkommen (Lexikonluecken -> echte Fehltreffer), damit
die Schwelle eine Aussage hat und nicht per Konstruktion 100 % erreicht wird.

WICHTIG zur Aussagekraft: Datensatz v1 misst gegen die alte, grobe Taxonomie
(sechs Sammelkategorien inklusive 'Freizeit' und 'Sonstiges'). Er schuetzt
weiterhin die MECHANIK - Prompt-Bau, Maskierung, Schema, Mapping -, ist aber
KEIN Qualitaetsmassstab fuer das deutsche Standardpaket mit 50 Kategorien.
Dessen Trefferquote muss mit einem eigenen Datensatz (v2) neu gemessen werden;
siehe docs/KI-KATEGORISIERUNG.md."""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.llm.discovery import Target
from app.llm.settings import LLMSettings
from app.llm.window import WindowInfo

from worker.ai.classifier import (
    NO_MATCH,
    NO_MATCH_REASON_CODE,
    CategoryCandidates,
    classify_transaction,
)

# Ziel und Einstellungen eines geladenen Modells: das Budget rechnet der
# KI-Kern aus diesem Fenster und den Schemavorgaben (hier als Testdaten).
_TARGET = Target(
    base_url="http://localhost:1234/v1",
    model="fake-eval-model",
    window=WindowInfo(loaded=32768, cap=None, effective=32768, source="/api/v1/models"),
)
_SETTINGS = LLMSettings(
    is_enabled=True,
    min_confidence=0.85,
    custom_base_url=None,
    model_name=None,
    temperature=1.0,
    request_timeout_seconds=120.0,
    answer_reserve_share=0.25,
    start_chars_per_token=3.0,
    start_estimate_error=0.15,
)

DATASET_PATH = Path(__file__).parent / "fixtures" / "ai_eval_dataset_v1.json"

# Haendler-Lexikon des Fake-Modells: bewusst generisch und unabhaengig von den
# erwarteten Labels des Datensatzes formuliert (Praefix-Match auf Wortanfang).
_MODEL_LEXICON: list[tuple[str, str]] = [
    ("rewe", "Lebensmittel"),
    ("edeka", "Lebensmittel"),
    ("aldi", "Lebensmittel"),
    ("lidl", "Lebensmittel"),
    ("kaufland", "Lebensmittel"),
    ("penny", "Lebensmittel"),
    ("baeckerei", "Restaurant & Cafe"),
    ("mcdonald", "Restaurant & Cafe"),
    ("restaurant", "Restaurant & Cafe"),
    ("pizzeria", "Restaurant & Cafe"),
    ("aral", "Mobilitaet"),
    ("shell", "Mobilitaet"),
    ("tankstelle", "Mobilitaet"),
    ("db vertrieb", "Mobilitaet"),
    ("bvg", "Mobilitaet"),
    ("miete", "Wohnen & Miete"),
    ("hausverwaltung", "Wohnen & Miete"),
    ("stadtwerke", "Wohnen & Miete"),
    ("versicherung", "Versicherungen"),
    ("allianz", "Versicherungen"),
    ("huk", "Versicherungen"),
    ("apotheke", "Gesundheit"),
    ("zahnarzt", "Gesundheit"),
    ("klinik", "Gesundheit"),
    ("netflix", "Abos & Streaming"),
    ("spotify", "Abos & Streaming"),
    ("amazon prime", "Abos & Streaming"),
    ("zalando", "Kleidung"),
    ("deichmann", "Kleidung"),
    ("gehalt", "Einkommen"),
    ("lohn", "Einkommen"),
]

# Antwort des Fake-Modells, wenn kein Lexikoneintrag greift. Seit
# Lieferabschnitt 5 ist das no_match und nicht mehr die Sammelkategorie
# 'Sonstiges': genau dieses Ausweichen verbietet Leitplanke 8. Fuer die
# Trefferquote aendert das nichts - ein Fehltreffer bleibt ein Fehltreffer -,
# aber der Datensatz misst jetzt den Vertrag, der wirklich gilt.
_FALLBACK_ANSWER = NO_MATCH

# Ein unmaskierter IBAN/Kartenrest darf NIE im gesendeten Prompt auftauchen.
_UNMASKED_PII = re.compile(r"[A-Z]{2}\d{2}\d{12,}|\b\d{10,16}\b")


def _load_dataset() -> dict[str, Any]:
    with DATASET_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


DATASET = _load_dataset()
CATEGORY_ID_BY_NAME = {c["name"]: c["id"] for c in DATASET["categories"]}
VALID_CAT_IDS = {c["id"] for c in DATASET["categories"]}
# Kandidatenform des Prompts seit Lieferabschnitt 5: voller Pfad statt Name +
# Gruppe. Der Datensatz kennt keine semantischen Schluessel/Synonyme - er
# stammt aus der alten Taxonomie (siehe Moduldoc).
CATEGORIES: list[dict[str, Any]] = [
    {"id": c["id"], "path": f"{c['group']} · {c['name']}"} for c in DATASET["categories"]
]
CANDIDATES = CategoryCandidates(
    entries=CATEGORIES, valid_ids=VALID_CAT_IDS, taxonomy_version=None
)


class _DeterministicFakeModel:
    """Steht an der Stelle von LM Studio. Liest nur den tatsaechlich
    gesendeten Request und antwortet regelbasiert - kein Zufall, kein Netz."""

    def __init__(self, structured: bool) -> None:
        self.structured = structured
        self.request_bodies: list[dict[str, Any]] = []

    async def respond(self, url: str, **kwargs: Any) -> MagicMock:
        body: dict[str, Any] = kwargs["json"]
        self.request_bodies.append(body)

        user_prompt = str(body["messages"][-1]["content"])
        # Vertragspruefung bei jedem Aufruf: keine unmaskierten Daten, und der
        # strukturierte Pfad muss das Schema wirklich mitschicken.
        assert not _UNMASKED_PII.search(user_prompt), (
            f"Unmaskierte PII im Prompt: {user_prompt}"
        )
        if self.structured:
            assert body["response_format"]["type"] == "json_schema"
            enum_ids = body["response_format"]["json_schema"]["schema"]["properties"][
                "category_id"
            ]["enum"]
            # no_match gehoert zum Vertrag und muss im Enum stehen.
            assert set(enum_ids) == VALID_CAT_IDS | {NO_MATCH}
        else:
            assert "response_format" not in body

        haystack = user_prompt.lower()
        answer_id = _FALLBACK_ANSWER
        confidence = 0.35
        for keyword, name in _MODEL_LEXICON:
            if re.search(rf"\b{re.escape(keyword)}", haystack):
                answer_id = CATEGORY_ID_BY_NAME[name]
                confidence = 0.92
                break

        payee_match = re.search(r"Empfaenger/Haendler: '([^']*)'", user_prompt)
        assert payee_match is not None, f"Unerwartete Promptform: {user_prompt}"

        answer = {
            "payee_name": payee_match.group(1).title(),
            "category_id": answer_id,
            "confidence": confidence,
            "reason_code": "merchant_and_context_match",
        }
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}]
        }
        return resp


def _fake_client_class(fake: _DeterministicFakeModel) -> type:
    """Ersetzt httpx.AsyncClient komplett. Ein echter Client wuerde pro Zeile
    einen SSL-Kontext aufbauen - bei 30 Faellen dominiert das die Laufzeit,
    obwohl gar kein Netz im Spiel ist."""

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *exc_info: Any) -> bool:
            return False

        async def post(self, url: str, **kwargs: Any) -> MagicMock:
            return await fake.respond(url, **kwargs)

    return _FakeAsyncClient


def _fake_session() -> AsyncMock:
    """DB-Attrappe. Sie beantwortet die Vor-Schreib-Aufloesung (jede abgefragte
    Kategorie gilt als aktiv und ungemergt) und meldet keine Messung - dann
    rechnet der Kern mit den Startwerten der Einstellung."""

    async def _execute(statement: Any, params: Any = None, *a: Any, **k: Any) -> MagicMock:
        result = MagicMock()
        if "merged_into_category_id" in str(statement):
            result.first.return_value = SimpleNamespace(
                id=params["category_id"],
                is_archived=False,
                merged_into_category_id=None,
                group_is_archived=False,
            )
        elif "from ai_model_measurements" in str(statement):
            result.first.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    return session


class EvaluationReport:
    """Ergebnis eines Evaluationslaufs ueber den gesamten Datensatz."""

    def __init__(
        self,
        accuracy: float,
        misses: list[str],
        emitted_ids: list[str | None],
        reason_codes: list[str],
    ) -> None:
        self.accuracy = accuracy
        self.misses = misses
        self.emitted_ids = emitted_ids
        self.reason_codes = reason_codes


_REPORT_CACHE: dict[bool, EvaluationReport] = {}


async def _run_evaluation(structured: bool) -> EvaluationReport:
    """Klassifiziert jeden Datensatzfall ueber die echte Pipeline. Das
    Ergebnis ist deterministisch und wird je Pfad einmal berechnet."""
    cached = _REPORT_CACHE.get(structured)
    if cached is not None:
        return cached

    fake = _DeterministicFakeModel(structured=structured)
    session = _fake_session()
    household_id = uuid4()

    hits = 0
    misses: list[str] = []
    emitted_ids: list[str | None] = []
    reason_codes: list[str] = []
    with patch("httpx.AsyncClient", _fake_client_class(fake)):
        for case in DATASET["cases"]:
            result = await classify_transaction(
                session,
                household_id,
                None,
                case["payee_raw"],
                case["purpose"],
                case["amount_minor"],
                _TARGET,
                CANDIDATES,
                structured,
                _SETTINGS,
            )
            emitted_ids.append(str(result.category_id) if result.category_id else None)
            reason_codes.append(result.reason_code)
            expected_id = CATEGORY_ID_BY_NAME[case["expected_category"]]
            if result.category_id is not None and str(result.category_id) == expected_id:
                hits += 1
            else:
                misses.append(str(case["id"]))

    report = EvaluationReport(hits / len(DATASET["cases"]), misses, emitted_ids, reason_codes)
    _REPORT_CACHE[structured] = report
    return report


# ---------------------------------------------------------------------------
# Datensatz-Integritaet (schuetzt die Aussagekraft der Trefferquote)
# ---------------------------------------------------------------------------


def test_dataset_v1_measures_the_old_taxonomy_and_is_no_quality_benchmark() -> None:
    """Festgehalten als Test, damit die Einschraenkung nicht nur im Kommentar
    steht: v1 kennt Sammelkategorien wie 'Freizeit'/'Sonstiges', die das
    deutsche Standardpaket bewusst nicht mehr als Auffangbecken vorsieht.
    Fuer die neue Taxonomie braucht es einen eigenen Datensatz."""
    names = {c["name"] for c in DATASET["categories"]}
    assert "Sonstiges" in names
    assert len(names) < 20, "Datensatz v1 bildet die grobe Alt-Taxonomie ab."


def test_dataset_is_versioned_and_well_formed() -> None:
    assert DATASET["dataset_version"] == "v1"
    assert 0.0 < DATASET["min_accuracy"] <= 1.0
    assert len(DATASET["cases"]) >= 30, "Zu kleiner Datensatz fuer eine Trefferquote."
    assert len({c["id"] for c in DATASET["cases"]}) == len(DATASET["cases"])
    assert len(VALID_CAT_IDS) == len(CATEGORIES)
    for case in DATASET["cases"]:
        assert case["expected_category"] in CATEGORY_ID_BY_NAME, case["id"]


def test_dataset_contains_pii_bearing_cases() -> None:
    """Ohne PII-Faelle wuerde die Maskierung im Evaluationspfad nie geprueft."""
    raw = " ".join(f"{c['payee_raw']} {c['purpose']}" for c in DATASET["cases"])
    assert _UNMASKED_PII.search(raw), "Datensatz enthaelt keine PII-Faelle mehr."


# ---------------------------------------------------------------------------
# Trefferquote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_path_meets_minimum_accuracy() -> None:
    report = await _run_evaluation(structured=True)
    assert report.accuracy >= DATASET["min_accuracy"], (
        f"Trefferquote {report.accuracy:.2%} unter Schwelle "
        f"{DATASET['min_accuracy']:.0%}; Fehltreffer: {report.misses}"
    )


@pytest.mark.asyncio
async def test_prompt_fallback_path_meets_minimum_accuracy() -> None:
    """Modelle ohne json_schema-Unterstuetzung duerfen nicht schlechter
    kategorisieren - der Fallback ist ein anderer Transportweg, kein anderer
    Klassifikationsvertrag."""
    report = await _run_evaluation(structured=False)
    assert report.accuracy >= DATASET["min_accuracy"], (
        f"Trefferquote {report.accuracy:.2%} unter Schwelle "
        f"{DATASET['min_accuracy']:.0%}; Fehltreffer: {report.misses}"
    )


@pytest.mark.asyncio
async def test_both_paths_yield_identical_results() -> None:
    structured = await _run_evaluation(structured=True)
    prompt = await _run_evaluation(structured=False)
    assert structured.accuracy == prompt.accuracy
    assert structured.misses == prompt.misses


@pytest.mark.asyncio
async def test_evaluation_is_not_trivially_perfect() -> None:
    """Wuerde der Fake jeden Fall treffen, waere die Schwelle wertlos - der
    Datensatz enthaelt bewusst Faelle ausserhalb des Lexikons."""
    report = await _run_evaluation(structured=True)
    assert 0 < len(report.misses) < len(DATASET["cases"])
    assert report.accuracy < 1.0


@pytest.mark.asyncio
async def test_evaluation_never_emits_unknown_category() -> None:
    """Invariante ueber den gesamten Datensatz: kein Ergebnis ausserhalb der
    erlaubten Kategorien (Abschnitt 8.3)."""
    for structured in (True, False):
        report = await _run_evaluation(structured=structured)
        assert len(report.emitted_ids) == len(DATASET["cases"])
        for emitted in report.emitted_ids:
            assert emitted is None or emitted in VALID_CAT_IDS


@pytest.mark.asyncio
async def test_unmatched_cases_end_as_no_match_instead_of_a_catch_all() -> None:
    """Leitplanke 8 im Datensatzmassstab: was das Modell nicht kennt, landet
    ohne Kategorie in der Pruefung - nicht in einer Sammelkategorie."""
    report = await _run_evaluation(structured=True)
    no_match_count = sum(1 for code in report.reason_codes if code == NO_MATCH_REASON_CODE)
    assert no_match_count > 0, "Datensatz enthaelt keinen Fall ausserhalb des Lexikons mehr."
    for emitted, code in zip(report.emitted_ids, report.reason_codes, strict=True):
        if code == NO_MATCH_REASON_CODE:
            assert emitted is None
