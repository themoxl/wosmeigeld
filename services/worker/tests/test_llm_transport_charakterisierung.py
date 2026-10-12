"""Charakterisierungstests fuer den LLM-Transport des Workers - seit Etappe 0
der Gateway des KI-Kerns (services/api/app/llm/gateway.py), den
worker/ai/classifier.py::classify_transaction aufruft.

Diese Tests halten fest, was ueber die Leitung geht und welche Fehlerpfade es
gibt. Frueher froren sie den eigenen Transport des Workers ein (festes Budget
AI_MAX_TOKENS, Eskalation x3 nach abgeschnittener Antwort, jeder HTTP 400 als
"kann kein Schema"); der Umzug in den Kern hat genau diese Stellen
absichtlich geaendert:

* max_tokens kommt aus dem GELADENEN Fenster (W - E - A), nie aus einer Zahl
  im Code;
* nach einer abgeschnittenen Antwort wird nicht eskaliert, sondern benannt
  (model_budget_exhausted);
* ein HTTP 400 wegen Fensterueberlauf ist keine fehlende Schema-Faehigkeit.

Abgrenzung zu test_ai_classifier_logic.py: dort wird httpx.AsyncClient.post per
unittest.mock ersetzt. Hier laeuft ein ECHTER httpx-Client gegen einen
httpx.MockTransport - URL und JSON-Serialisierung sind mit eingefroren. Kein
Netzwerk, kein LM Studio, keine Datenbank (die Session ist eine Attrappe)."""

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from app.llm import reasons
from app.llm.budget import compute
from app.llm.discovery import Target
from app.llm.gateway import CAPABILITY_REJECT_STATUS
from app.llm.settings import LLMSettings
from app.llm.window import WindowInfo

from worker.ai.classifier import (
    NO_MATCH,
    NO_MATCH_REASON_CODE,
    CategoryCandidates,
    ClassificationResult,
    build_classification_json_schema,
    classify_transaction,
)

# Der echte Client wird VOR jedem Patch festgehalten - die Attrappe ersetzt
# httpx.AsyncClient global und muss ihn selbst wieder erzeugen koennen.
_ECHTER_CLIENT = httpx.AsyncClient

BASIS_URL = "http://localhost:1234/v1"
MODELL = "test-model"
FENSTER = 8192
KATEGORIE_ID = str(uuid4())
KANDIDATEN = CategoryCandidates(
    entries=[{"id": KATEGORIE_ID, "path": "Variable Kosten · Supermarkt"}],
    valid_ids={KATEGORIE_ID},
    taxonomy_version=1,
)
LEERE_KANDIDATEN = CategoryCandidates(entries=[], valid_ids=set(), taxonomy_version=None)

_AKTIVE_ZEILE = SimpleNamespace(
    id=KATEGORIE_ID, is_archived=False, merged_into_category_id=None, group_is_archived=False
)


def _einstellungen(**abweichungen: Any) -> LLMSettings:
    """Einstellungen wie aus der Datenbank (Werte = Schemavorgaben)."""
    werte: dict[str, Any] = {
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
    werte.update(abweichungen)
    return LLMSettings(**werte)


def _ziel(basis: str = BASIS_URL, modell: str = MODELL, fenster: int = FENSTER) -> Target:
    return Target(
        base_url=basis,
        model=modell,
        window=WindowInfo(loaded=fenster, cap=None, effective=fenster, source="/api/v1/models"),
    )


def _gueltiges_json(kategorie_id: str = KATEGORIE_ID) -> str:
    return (
        f'{{"payee_name": "REWE", "category_id": "{kategorie_id}", '
        f'"confidence": 0.9, "reason_code": "merchant_and_context_match"}}'
    )


def _no_match_json() -> str:
    return (
        f'{{"payee_name": "Unbekannt", "category_id": "{NO_MATCH}", '
        f'"confidence": 0.2, "reason_code": "unclear_merchant"}}'
    )


@dataclass(frozen=True)
class _Antwort:
    """Bauplan einer Serverantwort (jeder Aufruf braucht ein frisches Objekt)."""

    status: int = 200
    inhalt: str = ""
    finish_reason: str = "stop"
    fehler: str = "abgelehnt"

    def als_response(self) -> httpx.Response:
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": self.fehler}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": self.inhalt}, "finish_reason": self.finish_reason}
                ]
            },
        )


def _abgeschnitten() -> _Antwort:
    return _Antwort(inhalt="", finish_reason="length")


class _LMStudioDoppel:
    """Fake-LM-Studio hinter httpx.MockTransport: protokolliert jede Anfrage und
    antwortet nach Drehbuch (die letzte Antwort wiederholt sich)."""

    def __init__(self, antworten: list[_Antwort]) -> None:
        self._antworten = antworten
        self.anfragen: list[httpx.Request] = []

    def antworte(self, request: httpx.Request) -> httpx.Response:
        self.anfragen.append(request)
        index = min(len(self.anfragen), len(self._antworten)) - 1
        return self._antworten[index].als_response()

    @property
    def koerper(self) -> list[dict[str, Any]]:
        return [json.loads(anfrage.content) for anfrage in self.anfragen]

    @property
    def urls(self) -> list[str]:
        return [str(anfrage.url) for anfrage in self.anfragen]


@contextlib.contextmanager
def _lm_studio(doppel: _LMStudioDoppel) -> Iterator[None]:
    """Ersetzt httpx.AsyncClient global durch einen echten Client mit
    MockTransport - der Gateway erzeugt seinen Client selbst."""

    def _fabrik(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(doppel.antworte)
        return _ECHTER_CLIENT(*args, **kwargs)

    with patch("httpx.AsyncClient", _fabrik):
        yield


def _session_doppel(aufloesung: Any = _AKTIVE_ZEILE) -> AsyncMock:
    async def _execute(statement: Any, params: Any = None, *args: Any, **kwargs: Any) -> MagicMock:
        ergebnis = MagicMock()
        sql = str(statement)
        if "merged_into_category_id" in sql:
            ergebnis.first.return_value = aufloesung
        elif "from ai_model_measurements" in sql:
            ergebnis.first.return_value = None
        return ergebnis

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    return session


def _schreibvorgaenge(session: AsyncMock, tabelle: str) -> list[tuple[str, dict[str, Any]]]:
    geschrieben: list[tuple[str, dict[str, Any]]] = []
    for aufruf in session.execute.await_args_list:
        sql = str(aufruf.args[0].text)
        if sql.lstrip().lower().startswith(("insert", "update")) and tabelle in sql:
            params = aufruf.args[1]
            assert isinstance(params, dict)
            geschrieben.append((sql, params))
    return geschrieben


def _aufrufzeilen(session: AsyncMock) -> list[dict[str, Any]]:
    return [
        params
        for sql, params in _schreibvorgaenge(session, "llm_calls")
        if sql.lstrip().startswith("insert")
    ]


async def _klassifiziere(
    antworten: list[_Antwort], **ueberschreibungen: Any
) -> tuple[AsyncMock, ClassificationResult, _LMStudioDoppel]:
    doppel = _LMStudioDoppel(antworten)
    session = ueberschreibungen.pop("session", None) or _session_doppel()
    argumente: dict[str, Any] = {
        "session": session,
        "household_id": uuid4(),
        "transaction_id": None,
        "payee_raw": "REWE Markt GmbH",
        "purpose": "Einkauf Lebensmittel",
        "amount_minor": -1500,
        "target": _ziel(),
        "candidates": KANDIDATEN,
        "supports_json_schema": True,
        "settings": _einstellungen(),
    }
    argumente.update(ueberschreibungen)
    with _lm_studio(doppel):
        ergebnis = await classify_transaction(**argumente)
    return session, ergebnis, doppel


# ---------------------------------------------------------------------------
# 1. Requestbau: welche Felder landen im Koerper - und wann
# ---------------------------------------------------------------------------


async def test_koerper_traegt_ohne_profil_nur_modell_nachrichten_und_budget() -> None:
    """Ohne Schema, ohne Sampling-Profil und ohne Reasoning-Einstellung besteht
    der Koerper aus genau drei Feldern - der Server-/Modellstandard bleibt
    sonst unangetastet."""
    _, _, doppel = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json())],
        supports_json_schema=False,
        target=_ziel(modell="voellig-unbekanntes-modell"),
    )

    koerper = doppel.koerper[0]
    assert set(koerper) == {"model", "messages", "max_tokens"}
    assert koerper["model"] == "voellig-unbekanntes-modell"
    assert [nachricht["role"] for nachricht in koerper["messages"]] == ["system", "user"]


@pytest.mark.parametrize("basis", [BASIS_URL, BASIS_URL + "/", BASIS_URL + "///"])
async def test_der_pfad_wird_ohne_doppelten_schraegstrich_gebaut(basis: str) -> None:
    _, _, doppel = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json())], target=_ziel(basis=basis)
    )

    assert doppel.urls == ["http://localhost:1234/v1/chat/completions"]


async def test_response_format_nur_mit_schema() -> None:
    schema = build_classification_json_schema({KATEGORIE_ID})

    _, _, ohne = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json())], supports_json_schema=False
    )
    _, _, mit = await _klassifiziere([_Antwort(inhalt=_gueltiges_json())])

    assert "response_format" not in ohne.koerper[0]
    assert mit.koerper[0]["response_format"] == {"type": "json_schema", "json_schema": schema}


@pytest.mark.parametrize(
    ("reasoning", "erwartet"),
    [
        (None, None),
        ("off", "none"),
        ("on", "high"),
        ("medium", "medium"),
        ("low", "low"),
        ("none", "none"),
        ("", None),
    ],
)
async def test_reasoning_effort_wird_nur_bei_gesetztem_wert_gesendet(
    reasoning: str | None, erwartet: str | None
) -> None:
    _, _, doppel = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json())], settings=_einstellungen(reasoning=reasoning)
    )

    koerper = doppel.koerper[0]
    if erwartet is None:
        assert "reasoning_effort" not in koerper
    else:
        assert koerper["reasoning_effort"] == erwartet


@pytest.mark.parametrize("fenster", [4096, 8192, 32768, 131072])
async def test_max_tokens_ist_der_freie_platz_des_geladenen_fensters(fenster: int) -> None:
    """max_tokens = W - E - A mit den Startwerten der Einstellung (keine
    Messung vorhanden) - nie eine Zahl aus dem Code."""
    _, _, doppel = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json())], target=_ziel(fenster=fenster)
    )

    koerper = doppel.koerper[0]
    budget = compute(
        window=fenster,
        messages=koerper["messages"],
        chars_per_token=3.0,
        estimate_error=0.15,
        reserve_share=0.25,
    )
    assert koerper["max_tokens"] == budget.max_tokens == fenster - budget.E - budget.A


async def test_modellprofil_greift_ohne_reasoning_einstellung() -> None:
    """reasoning=None heisst Modellstandard: kein reasoning_effort, aber das
    sichtbare Herstellerprofil (Gemma) gilt im Modellmodus."""
    _, _, doppel = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json())], target=_ziel(modell="google/gemma-4-26b-a4b-qat")
    )

    koerper = doppel.koerper[0]
    assert koerper["temperature"] == 1.0
    assert koerper["top_p"] == 0.95
    assert koerper["top_k"] == 64
    assert "reasoning_effort" not in koerper


async def test_manueller_modus_sendet_die_sechs_werte_der_einstellung() -> None:
    """Die Einstellungen kommen vollstaendig aus der Datenbank; leere
    Sampling-Felder tragen die neutralen Startwerte des manuellen Modus."""
    _, _, doppel = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json())], settings=_einstellungen(sampling_mode="manual")
    )

    koerper = doppel.koerper[0]
    assert koerper["temperature"] == 1.0
    assert koerper["top_p"] == 0.95
    assert koerper["top_k"] == 64
    assert koerper["repeat_penalty"] == 1.0
    assert koerper["min_p"] == 0.0
    assert koerper["presence_penalty"] == 0.0


async def test_der_vollstaendig_besetzte_koerper_traegt_genau_diese_felder() -> None:
    _, _, doppel = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json())],
        settings=_einstellungen(
            sampling_mode="manual",
            temperature=0.42,
            top_p=0.81,
            top_k=37,
            repeat_penalty=1.07,
            min_p=0.03,
            presence_penalty=0.25,
            reasoning="on",
        ),
    )

    assert set(doppel.koerper[0]) == {
        "model",
        "messages",
        "max_tokens",
        "temperature",
        "top_p",
        "top_k",
        "repeat_penalty",
        "min_p",
        "presence_penalty",
        "response_format",
        "reasoning_effort",
    }


# ---------------------------------------------------------------------------
# 2. Capability-Fallback: welche Antworten einen zweiten Aufruf ohne Schema
#    ausloesen - und welche nicht
# ---------------------------------------------------------------------------


def test_nur_diese_vier_statuscodes_gelten_als_capability_ablehnung() -> None:
    assert CAPABILITY_REJECT_STATUS == frozenset({400, 404, 422, 501})


@pytest.mark.parametrize("status", sorted(CAPABILITY_REJECT_STATUS))
async def test_capability_ablehnung_fuehrt_zu_genau_einem_zweiten_aufruf_ohne_schema(
    status: int,
) -> None:
    session, ergebnis, doppel = await _klassifiziere(
        [_Antwort(status=status), _Antwort(inhalt=_gueltiges_json())]
    )

    assert len(doppel.anfragen) == 2
    erst, zweit = doppel.koerper
    assert "response_format" in erst
    assert "response_format" not in zweit
    # Bis auf das Schema ist der zweite Aufruf identisch - ohne Korrekturhinweis.
    assert erst["messages"] == zweit["messages"]
    assert erst["max_tokens"] == zweit["max_tokens"]
    assert ergebnis.category_id == UUID(KATEGORIE_ID)

    zeilen = _aufrufzeilen(session)
    assert [zeile["reason"] for zeile in zeilen] == [reasons.MODEL_SCHEMA_UNSUPPORTED, None]
    assert zeilen[0]["http"] == status
    faehigkeit = _schreibvorgaenge(session, "ai_model_capabilities")
    assert len(faehigkeit) == 1
    assert faehigkeit[0][1]["sjs"] is False
    assert faehigkeit[0][1]["cp"] is True


@pytest.mark.parametrize("status", [401, 403, 405, 409, 429, 502])
async def test_andere_statuscodes_loesen_keinen_zweiten_aufruf_aus(status: int) -> None:
    session, ergebnis, doppel = await _klassifiziere([_Antwort(status=status)])

    assert len(doppel.anfragen) == 1
    assert ergebnis.reason_code == "llm_execution_error"
    zeilen = _aufrufzeilen(session)
    assert len(zeilen) == 1
    assert zeilen[0]["reason"] == reasons.MODEL_HTTP_ERROR
    assert f"HTTP {status}" in zeilen[0]["err"]
    assert _schreibvorgaenge(session, "ai_model_capabilities") == []


async def test_ein_fensterueberlauf_ist_keine_capability_ablehnung() -> None:
    """Frueher galt jeder HTTP 400 als "kann kein Schema" und wurde dauerhaft
    so vermerkt - auch "Context size has been exceeded"."""
    session, ergebnis, doppel = await _klassifiziere(
        [_Antwort(status=400, fehler="Context size has been exceeded.")]
    )

    assert len(doppel.anfragen) == 1
    assert ergebnis.reason_code == reasons.MODEL_CONTEXT_OVERFLOW
    assert _schreibvorgaenge(session, "ai_model_capabilities") == []


async def test_ein_ueberlauf_mit_echter_eingabe_wird_einmal_neu_gerechnet() -> None:
    session, ergebnis, doppel = await _klassifiziere(
        [
            _Antwort(status=400, fehler="Context size has been exceeded (input has 3000 tokens)."),
            _Antwort(inhalt=_gueltiges_json()),
        ]
    )

    assert len(doppel.anfragen) == 2
    # Beim zweiten Mal ist die Eingabe exakt bekannt: W - 3000, ohne Abstand.
    assert doppel.koerper[1]["max_tokens"] == FENSTER - 3000
    assert ergebnis.category_id == UUID(KATEGORIE_ID)
    zeilen = _aufrufzeilen(session)
    assert [zeile["reason"] for zeile in zeilen] == [reasons.MODEL_CONTEXT_OVERFLOW, None]
    assert zeilen[1]["exact"] is True
    messung = _schreibvorgaenge(session, "ai_model_measurements")
    assert messung[0][1]["prompt"] == 3000


async def test_die_faehigkeit_wird_auch_dann_abgestuft_wenn_der_zweite_aufruf_scheitert() -> None:
    """Der Vermerk 'kann kein Schema' steht, BEVOR die Antwort des
    Fallback-Aufrufs geprueft wird."""
    session, ergebnis, doppel = await _klassifiziere([_Antwort(status=400), _Antwort(status=500)])

    assert len(doppel.anfragen) == 2
    assert ergebnis.reason_code == "llm_execution_error"
    assert _schreibvorgaenge(session, "ai_model_capabilities")[0][1]["sjs"] is False
    assert "HTTP 500" in _aufrufzeilen(session)[1]["err"]


async def test_nach_der_ablehnung_bleibt_der_schemapfad_fuer_die_ganze_zeile_aus() -> None:
    """use_schema gilt fuer die ganze Buchung: auch der Korrekturversuch nach
    kaputtem JSON geht ohne response_format raus - mit Korrekturhinweis."""
    session, ergebnis, doppel = await _klassifiziere(
        [
            _Antwort(status=400),
            _Antwort(inhalt="das ist kein JSON"),
            _Antwort(inhalt=_gueltiges_json()),
        ]
    )

    assert len(doppel.anfragen) == 3
    koerper = doppel.koerper
    assert "response_format" in koerper[0]
    assert all("response_format" not in eintrag for eintrag in koerper[1:])
    assert "HINWEIS" not in koerper[1]["messages"][1]["content"]
    assert "HINWEIS" in koerper[2]["messages"][1]["content"]
    assert ergebnis.category_id == UUID(KATEGORIE_ID)
    assert len(_aufrufzeilen(session)) == 3


async def test_ohne_faehigkeit_wird_eine_ablehnung_nicht_mehr_abgefangen() -> None:
    session, ergebnis, doppel = await _klassifiziere(
        [_Antwort(status=400)], supports_json_schema=False
    )

    assert len(doppel.anfragen) == 1
    assert "response_format" not in doppel.koerper[0]
    assert ergebnis.reason_code == "llm_execution_error"
    assert len(_aufrufzeilen(session)) == 1


async def test_ohne_kandidaten_wird_trotz_faehigkeit_kein_schema_gesendet() -> None:
    session, ergebnis, doppel = await _klassifiziere(
        [_Antwort(inhalt=_no_match_json())], candidates=LEERE_KANDIDATEN, supports_json_schema=True
    )

    assert len(doppel.anfragen) == 1
    assert "response_format" not in doppel.koerper[0]
    assert ergebnis.reason_code == NO_MATCH_REASON_CODE
    assert ergebnis.category_id is None


# ---------------------------------------------------------------------------
# 3. Abgeschnittene Antworten: benannt statt eskaliert
# ---------------------------------------------------------------------------


async def test_abgeschnittene_antwort_wird_benannt_und_nicht_eskaliert() -> None:
    """Das Antwortbudget war bereits der ganze freie Platz des Fensters - ein
    groesseres gibt es nicht. Kein zweiter Aufruf, kein "ohne Denken" (E1)."""
    session, ergebnis, doppel = await _klassifiziere(
        [_abgeschnitten(), _Antwort(inhalt=_gueltiges_json())]
    )

    assert len(doppel.anfragen) == 1
    assert ergebnis.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    assert ergebnis.confidence == 0.0
    assert ergebnis.payee_name == "REWE Markt GmbH"
    zeile = _aufrufzeilen(session)[0]
    assert zeile["reason"] == reasons.MODEL_BUDGET_EXHAUSTED
    assert "Antwortbudget" in zeile["err"]


async def test_nach_einem_schemafehler_folgt_genau_ein_korrekturversuch() -> None:
    session, ergebnis, doppel = await _klassifiziere(
        [_Antwort(inhalt="kein JSON"), _abgeschnitten()]
    )

    assert len(doppel.anfragen) == 2
    assert ergebnis.reason_code == reasons.MODEL_BUDGET_EXHAUSTED
    # Die erste Zeile wird nachtraeglich als unbrauchbar markiert, die zweite
    # traegt den Grund schon vom Gateway.
    nachtraege = [
        params
        for sql, params in _schreibvorgaenge(session, "llm_calls")
        if sql.lstrip().startswith("update")
    ]
    assert [eintrag["reason"] for eintrag in nachtraege] == [reasons.MODEL_OUTPUT_UNUSABLE]
    zeilen = _aufrufzeilen(session)
    assert [zeile["reason"] for zeile in zeilen] == [None, reasons.MODEL_BUDGET_EXHAUSTED]


async def test_abgeschnitten_aber_lesbar_gilt_als_erfolg() -> None:
    """finish_reason 'length' allein loest nichts aus - steht das JSON
    vollstaendig da, zaehlt die Antwort ganz normal."""
    session, ergebnis, doppel = await _klassifiziere(
        [_Antwort(inhalt=_gueltiges_json(), finish_reason="length")]
    )

    assert len(doppel.anfragen) == 1
    assert ergebnis.category_id == UUID(KATEGORIE_ID)
    assert ergebnis.reason_code == "merchant_and_context_match"
    assert len(_aufrufzeilen(session)) == 1
