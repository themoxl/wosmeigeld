"""Regeln und Modellvertrag der Dokumentauswertung ohne Datenbank."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
from app import llm
from app.llm import reasons
from app.llm.gateway import chat_structured as _real_chat_structured

from worker.health_documents.processor import (
    _auto_confirmable,
    _deterministic_fields,
    _merge_fields,
    _model_context,
    _model_fields,
    _model_prompts,
    _ModelContext,
    _semantic_fingerprint,
    _validated_fields,
)

_LEISTUNGSABRECHNUNG = """
Allianz Krankenversicherung AG
Leistungsabrechnung
Rechnungsdatum: 04.12.2025
Rechnungsnummer: 250000003
Gesamtbetrag: 1.234,50 EUR
Erstattungsbetrag: 987,60 EUR
""".strip()

# Der Satzspiegel einer Leistungsabrechnung: die Nummer ist gruppiert gesetzt,
# das Datum klebt am Folgewort, und das Geburtsdatum steht weiter unten im
# Dokument.
_ABRECHNUNG_ROH = """
Leistungsabrechnung Nr. 25 00 00001
bitte wenden
Allianz
Krankenversicherung AG
Bitte stets angeben Unser Zeichen Durchwahl Ihr Gespraechspartner Tag
BD/Versicherungsnummer 0000 000-
0107/ 09 111 222 X 00 HLA 00000 20.12.2025Team der Kundenberatung
Guten Tag, Herr Muster,
Martin Muster, geb. 01.01.1985
01 nicht abgerechnete Kosten, siehe Erlaeuterung 45,00
Auszahlungsbetrag 0,00
""".strip()

_APP_EINREICHUNG = """
--- OCR Seite 1 ---
Einreichung: 10000042
Eingangsdatum: 02.12.2025 17:01:51
Vertrag: 09111222X00
Sparte: Kranken
""".strip()

_VOLLSTAENDIGE_RECHNUNG = {
    "document_type": "invoice",
    "provider_name": "Praxis am Park",
    "invoice_date": "2026-07-10",
    "billed_minor": 18000,
}


def test_deterministic_fields_liest_betraege_und_datum_ohne_ki() -> None:
    fields = _deterministic_fields(_LEISTUNGSABRECHNUNG)

    assert fields["document_type"] == "benefit_statement"
    assert fields["invoice_date"] == "2025-12-04"
    assert fields["invoice_number"] == "250000003"
    assert fields["billed_minor"] == 123_450
    assert fields["reimbursed_minor"] == 98_760
    assert fields["currency"] == "EUR"


def test_auto_confirmable_verlangt_konfidenz_und_vollstaendige_kernfelder() -> None:
    assert _auto_confirmable(_VOLLSTAENDIGE_RECHNUNG, [], 0.9) is True

    # Unter der Schwelle bleibt die Handpruefung.
    assert _auto_confirmable(_VOLLSTAENDIGE_RECHNUNG, [], 0.7) is False
    # Jede Warnung heisst: da war etwas nicht sauber.
    assert _auto_confirmable(_VOLLSTAENDIGE_RECHNUNG, ["model_output_invalid"], 0.9) is False
    # Ohne Betrag wuerde `confirm_extraction` mit 422 antworten.
    assert _auto_confirmable({**_VOLLSTAENDIGE_RECHNUNG, "billed_minor": None}, [], 0.9) is False
    assert _auto_confirmable({**_VOLLSTAENDIGE_RECHNUNG, "provider_name": None}, [], 0.9) is False


_MIT_SELBSTBEHALT = """
Leistungsabrechnung Nr. 26 00 00002
Bitte stets angeben Unser Zeichen Durchwahl Tag
0107/ 09 111 222 X 00 YJB 00000 14.04.2026Team der Kundenberatung
Lena Muster, geb. 01.01.2020
01 Ambulante Behandlung TAR2+ 400,00 400,00 400,00 2026
-- Selbstbehalt TAR2+ - 150,00 2026
Summe der Rechnungsbetraege / Erstattung 400,00 250,00
Auszahlungsbetrag 250,00
""".strip()

_SELBSTBEHALT_FRISST_ALLES = """
Leistungsabrechnung Nr. 26 00 00003
Jonas Muster, geb. 01.01.2022
01 Arznei- und Verbandmittel TAR1+ 30,00 30,00 30,00 2026
-- Selbstbehalt TAR1+ - 30,00 2026
Summe der Rechnungsbetraege / Erstattung 30,00 0,00
Auszahlungsbetrag 0,00
""".strip()


def test_selbstbehalt_wird_nicht_als_erstattung_gemeldet() -> None:
    """Die dritte Spalte der Belegzeile ist die Erstattung *vor* Selbstbehalt.

    Wer sie als Erstattung uebernimmt, meldet Geld als zurueckgeflossen, das
    nie auf dem Konto ankam.
    """
    fields = _deterministic_fields(_MIT_SELBSTBEHALT)

    assert fields["billed_minor"] == 40000
    assert fields["reimbursed_minor"] == 25000
    assert fields["person_name"] == "Lena Muster"


def test_voll_aufgezehrter_selbstbehalt_ergibt_null_erstattung() -> None:
    fields = _deterministic_fields(_SELBSTBEHALT_FRISST_ALLES)

    assert fields["billed_minor"] == 3000
    assert fields["reimbursed_minor"] == 0
    # Behandelt wurde das Kind, nicht der Versicherungsnehmer.
    assert fields["person_name"] == "Jonas Muster"


def test_gruppierte_abrechnungsnummer_wird_zusammengesetzt() -> None:
    """ "Nr. 25 00 00001" ist eine Nummer, nicht die Zahl 25."""
    fields = _deterministic_fields(_ABRECHNUNG_ROH)

    assert fields["statement_number"] == "250000001"
    assert fields["invoice_number"] is None


def test_geburtsdatum_wird_nicht_zum_rechnungsdatum() -> None:
    fields = _deterministic_fields(_ABRECHNUNG_ROH)

    # 20.12.2025 klebt im PDF-Text am Folgewort und darf trotzdem gewinnen;
    # 01.01.1985 ist das Geburtsdatum und scheidet aus.
    assert fields["statement_date"] == "2025-12-20"
    assert fields["invoice_date"] is None


def test_app_einreichung_wird_als_einreichungsnachweis_erkannt() -> None:
    fields = _deterministic_fields(_APP_EINREICHUNG)

    assert fields["document_type"] == "submission_receipt"
    assert fields["submission_date"] == "2025-12-02"
    assert fields["invoice_date"] is None
    # Kopfdaten der App sind Metadaten und kein Leistungserbringer.
    assert fields["provider_name"] is None


def test_modellantwort_ueberschreibt_keine_bessere_regel() -> None:
    """Die Antwort von gemma-4-26b auf eine Leistungsabrechnung.

    Sie enthaelt drei Fallen: den Typ "unknown" trotz erkannter Abrechnung,
    den *String* "null" statt JSON-null und die Nummer in Druckschreibweise.
    """
    deterministic = _deterministic_fields(_ABRECHNUNG_ROH)
    model = {
        "document_type": "unknown",
        "person_name": "null",
        "provider_name": "Allianz Krankenversicherung AG",
        "invoice_number": "25 00 00001",
        "invoice_date": "2025-12-20",
        "due_date": "null",
        "billed_minor": None,
        "expected_minor": None,
        "reimbursed_minor": None,
        "currency": "EUR",
        "external_reference": "null",
        "confidence": 1.0,
    }

    fields, _confidence, warnings = _validated_fields(deterministic, model)

    # "unknown" ist keine Erkennung und darf die Regel nicht ueberstimmen.
    assert fields["document_type"] == "benefit_statement"
    # Der Text "null" ist kein Name.
    assert fields["person_name"] == "Martin Muster"
    assert fields["due_date"] is None
    assert fields["external_reference"] is None
    # Eine Schreibweise fuer eine Nummer, sonst zerfaellt der Abgleich.
    assert fields["statement_number"] == "250000001"
    assert fields["invoice_number"] is None
    # Was das Modell offen laesst, liefert die Regel.
    assert fields["billed_minor"] == 4500
    assert "model_output_invalid" not in warnings


def test_modell_darf_die_regel_sehr_wohl_verbessern() -> None:
    """Die Sperre gilt nur fuer "unknown" - echte Erkenntnis gewinnt weiter."""
    deterministic = _deterministic_fields(_APP_EINREICHUNG)
    fields, _confidence, _warnings = _validated_fields(
        deterministic,
        {"document_type": "invoice", "provider_name": "Muster-Apotheke", "billed_minor": 4500},
    )

    assert fields["document_type"] == "invoice"
    assert fields["provider_name"] == "Muster-Apotheke"
    assert fields["billed_minor"] == 4500


def test_leistungsabrechnung_behaelt_validierte_regelwerte() -> None:
    """Auch eine falsche konkrete Einordnung ist schlechter als die Tabellenregel."""
    deterministic = _deterministic_fields(_ABRECHNUNG_ROH)
    merged, model_fields = _merge_fields(
        deterministic,
        {
            "document_type": "request_for_documents",
            "person_name": "Andere Person",
            "provider_name": "Allianz Krankenversicherung AG",
            "statement_number": "99 99 99999",
            "billed_minor": 999,
            "external_reference": "VORGANG-7",
        },
    )

    assert merged["document_type"] == "benefit_statement"
    assert merged["person_name"] == "Martin Muster"
    assert merged["statement_number"] == "250000001"
    assert merged["billed_minor"] == 4500
    # Versicherer und Briefkopf sind kein medizinischer Leistungserbringer.
    assert merged["provider_name"] is None
    # Ein Feld, das die Regel nicht kennt, darf die KI weiterhin ergaenzen.
    assert merged["external_reference"] == "VORGANG-7"
    assert model_fields == {"external_reference"}


def test_reasoning_prompt_listet_den_vertrag_ausdruecklich_auf() -> None:
    system, user = _model_prompts("Dokument", reasoning_enabled=True)

    for field in (
        "document_type",
        "person_name",
        "provider_name",
        "invoice_number",
        "invoice_date",
        "due_date",
        "billed_minor",
        "expected_minor",
        "reimbursed_minor",
        "currency",
        "external_reference",
        "confidence",
    ):
        assert field in system
    assert "nicht erneut" in system
    assert "alle Rechnungspositionen" in user
    for field in ("statement_number", "payer_name", "approved_minor", "items"):
        assert field in system


#: Modellaufruf ueber den KI-Kern: echter Gateway gegen einen falschen
#: Modellserver (httpx.MockTransport), Sitzung als Attrappe. So ist pruefbar,
#: was gesendet wird (Reasoning, Budget aus dem Fenster) und dass kein zweiter,
#: anders eingestellter Aufruf folgt.
_MODEL = "google/gemma-4-26b-a4b-qat"
_BASE = "http://localhost:1234/v1"


def _settings(reasoning: str | None, *, enabled: bool = True) -> llm.LLMSettings:
    return llm.LLMSettings(
        is_enabled=enabled,
        min_confidence=0.85,
        custom_base_url=_BASE,
        model_name=_MODEL,
        temperature=0.1,
        request_timeout_seconds=120,
        reasoning=reasoning,
        answer_reserve_share=0.25,
        result_page_share=0.1,
        run_time_limit_minutes=20,
        start_chars_per_token=3.0,
        start_estimate_error=0.15,
        context_line_enabled=True,
    )


def _target(window: int | None = 8192) -> llm.Target:
    return llm.Target(
        base_url=_BASE,
        model=_MODEL,
        window=llm.WindowInfo(
            loaded=window,
            cap=None,
            effective=window,
            source="/api/v1/models" if window else None,
            reasoning_capability=llm.ReasoningCapability(
                kind="switchable", allowed_options=("off", "on")
            ),
        ),
    )


def _session() -> AsyncMock:
    """Sitzung, die jedes Statement annimmt und keine Messung kennt."""

    async def execute(statement: Any, params: Any = None, *args: Any, **kwargs: Any) -> MagicMock:
        result = MagicMock()
        result.first.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute)
    return session


def _statements(session: AsyncMock, needle: str) -> list[Any]:
    return [call.args[1] for call in session.execute.await_args_list if needle in str(call.args[0])]


def _context(
    reasoning: str | None = "on", *, window: int | None = 8192
) -> tuple[_ModelContext, AsyncMock]:
    session = _session()
    scope = llm.CallScope(
        session=session,
        household_id=uuid4(),
        stage="health_document_extract",
        prompt_version="test",
    )
    context = _ModelContext(settings=_settings(reasoning), scope=scope, target=_target(window))
    return context, session


def _completion(
    *, content: str = "", reasoning: str = "", finish_reason: str = "stop"
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": content, "reasoning_content": reasoning},
                    "finish_reason": finish_reason,
                }
            ]
        },
    )


class _Server:
    """Falscher Modellserver; merkt sich jeden Anfragekoerper."""

    def __init__(self, answer: httpx.Response) -> None:
        self.answer = answer
        self.bodies: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return self.answer

    def patch(self) -> Any:
        async def via_server(request: llm.ChatRequest, **kwargs: Any) -> llm.ChatResult:
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.handle)) as client:
                kwargs["client"] = client
                return await _real_chat_structured(request, **kwargs)

        # Die Antwortpruefung des Kerns (llm.answers.ask) ruft den Gateway
        # ueber sein Modul - dort wird der Transport ersetzt.
        return patch("app.llm.gateway.chat_structured", via_server)


def _valid_model_json() -> str:
    return json.dumps(
        {
            "document_type": "benefit_statement",
            "person_name": "Martin Muster",
            "provider_name": None,
            "invoice_number": "250000001",
            "invoice_date": "2025-12-20",
            "due_date": None,
            "billed_minor": 4500,
            "expected_minor": 0,
            "reimbursed_minor": 0,
            "currency": "EUR",
            "external_reference": None,
            "confidence": 0.95,
        }
    )


async def test_reasoning_schleife_respektiert_budget_und_wird_ehrlich_gemeldet() -> None:
    """Verbraucht das Denken das Budget, gibt es genau EINEN Aufruf und den
    benannten Grund. Das Budget ist der freie Platz des geladenen Fensters -
    keine Zahl aus dieser Datei - und Reasoning geht so raus, wie der Haushalt
    es eingestellt hat ("on" -> "high")."""
    server = _Server(
        _completion(
            reasoning="invoice_number ist 250000001; alle Werte sind entschieden",
            finish_reason="length",
        )
    )
    context, session = _context("on")

    with server.patch():
        fields, model_name, warning = await _model_fields(context, _ABRECHNUNG_ROH)

    assert fields is None
    assert model_name == _MODEL
    assert warning == reasons.MODEL_BUDGET_EXHAUSTED
    [body] = server.bodies
    assert body["reasoning_effort"] == "high"
    assert body["max_tokens"] > 8192 - 8192 * 0.25
    assert body["response_format"]["json_schema"]["name"] == "health_document_fields"
    [row] = _statements(session, "insert into llm_calls")
    assert row["stage"] == "health_document_extract"
    assert row["reason"] == reasons.MODEL_BUDGET_EXHAUSTED
    assert row["max_tokens"] == body["max_tokens"]


async def test_json_im_reasoning_desselben_aufrufs_wird_gerettet() -> None:
    server = _Server(_completion(reasoning=_valid_model_json()))
    context, session = _context("on")

    with server.patch():
        fields, _model_name, warning = await _model_fields(context, _ABRECHNUNG_ROH)

    assert fields is not None
    assert fields["invoice_number"] == "250000001"
    assert warning is None
    assert len(server.bodies) == 1
    # Die Zeile des Gateways ("leere Antwort") wird auf Erfolg korrigiert.
    [update] = _statements(session, "update llm_calls")
    assert update["status"] == "success"
    assert update["reason"] is None


async def test_reasoning_aus_bleibt_ein_einziger_direkter_aufruf() -> None:
    server = _Server(_completion(content=_valid_model_json()))
    context, _session_mock = _context("off")

    with server.patch():
        fields, _model_name, warning = await _model_fields(context, _ABRECHNUNG_ROH)

    assert fields is not None
    assert warning is None
    [body] = server.bodies
    assert body["reasoning_effort"] == "none"


async def test_dokumenttext_wird_nicht_still_gekuerzt() -> None:
    """Frueher wurde der Text nach 80.000 Zeichen still abgeschnitten. Jetzt
    geht er ganz an das Modell, wenn er ins Fenster passt ..."""
    document = _ABRECHNUNG_ROH + "\n" + "Zeile ohne Belang\n" * 6_000 + "SCHLUSSMARKE"
    assert len(document) > 80_000
    server = _Server(_completion(content=_valid_model_json()))
    context, _session_mock = _context("off", window=131_072)

    with server.patch():
        fields, _model_name, warning = await _model_fields(context, document)

    assert fields is not None
    assert warning is None
    [body] = server.bodies
    assert body["messages"][-1]["content"].rstrip().endswith("SCHLUSSMARKE\n</document>")


async def test_zu_langer_dokumenttext_wird_benannt_und_die_regeln_bleiben() -> None:
    """... und wenn nicht, wird nichts gesendet: model_input_too_large, die
    Regelschicht bleibt mit diesem Grund stehen."""
    document = _ABRECHNUNG_ROH + "\n" + "Zeile ohne Belang\n" * 6_000
    server = _Server(_completion(content=_valid_model_json()))
    context, session = _context("off", window=8192)

    with server.patch():
        fields, model_name, warning = await _model_fields(context, document)

    assert server.bodies == []
    assert fields is None
    assert model_name == _MODEL
    assert warning == reasons.MODEL_INPUT_TOO_LARGE
    [row] = _statements(session, "insert into llm_calls")
    assert row["status"] == "refused"
    assert row["reason"] == reasons.MODEL_INPUT_TOO_LARGE
    validated, _confidence, warnings = _validated_fields(_deterministic_fields(document), fields)
    assert validated["statement_number"] == "250000001"
    assert warnings == []


async def test_ohne_modell_steht_der_grund_statt_stiller_regelschicht() -> None:
    context = _ModelContext(
        settings=_settings("on"),
        scope=llm.CallScope(
            session=_session(), household_id=uuid4(), stage="x", prompt_version="x"
        ),
        target=None,
        unavailable=reasons.MODEL_UNAVAILABLE,
    )

    assert await _model_fields(context, _ABRECHNUNG_ROH) == (
        None,
        None,
        reasons.MODEL_UNAVAILABLE,
    )


async def test_modellkontext_kennt_ki_aus_und_fehlendes_modell() -> None:
    household_id = uuid4()
    offline = llm.TargetResult(
        target=None,
        base_url=None,
        models=[],
        detailed=False,
        reason_code=reasons.MODEL_UNAVAILABLE,
        message="nicht erreichbar",
    )
    resolve = AsyncMock(return_value=offline)
    server = AsyncMock(side_effect=AssertionError("kein Serverkontakt bei KI aus"))
    with (
        patch(
            "worker.health_documents.processor.llm.load_settings",
            AsyncMock(return_value=_settings("on", enabled=False)),
        ),
        patch("httpx.AsyncClient.get", server),
    ):
        # KI aus: bewusste Wahl - kein Kontext. llm.resolve_target (echt)
        # antwortet ai_disabled, ohne einen Server zu fragen.
        assert await _model_context(_session(), household_id) is None
    server.assert_not_awaited()

    with (
        patch(
            "worker.health_documents.processor.llm.load_settings",
            AsyncMock(return_value=_settings("on")),
        ),
        patch("worker.health_documents.processor.llm.resolve_target", resolve),
    ):
        context = await _model_context(_session(), household_id)
    assert context is not None
    assert context.target is None
    assert context.unavailable == reasons.MODEL_UNAVAILABLE
    assert context.scope.stage == "health_document_extract"


def test_fingerabdruck_erkennt_denselben_beleg_trotz_anderer_bytes() -> None:
    erster = _deterministic_fields(_ABRECHNUNG_ROH)
    # Zweiter Download desselben Belegs: andere Bytes, identischer Inhalt.
    zweiter = _deterministic_fields(_ABRECHNUNG_ROH + "\n")

    assert _semantic_fingerprint(erster) is not None
    assert _semantic_fingerprint(erster) == _semantic_fingerprint(zweiter)

    anderer = {**erster, "statement_number": "251222006"}
    assert _semantic_fingerprint(anderer) != _semantic_fingerprint(erster)


def test_ohne_belastbares_merkmal_gibt_es_keinen_fingerabdruck() -> None:
    # Lieber zwei Eintraege als ein faelschlich verworfener Beleg.
    assert _semantic_fingerprint({"document_type": "unknown"}) is None
    assert _semantic_fingerprint({"document_type": "invoice", "invoice_date": "2026-01-01"}) is None
    assert (
        _semantic_fingerprint(
            {"document_type": "invoice", "invoice_date": "2026-01-01", "billed_minor": 1000}
        )
        is None
    )
    assert _semantic_fingerprint({"document_type": "invoice", "invoice_date": "2026-01-01",
                                  "billed_minor": 1000, "provider_name": "Praxis Beispiel"})


def test_auto_confirmable_haelt_unsichere_einordnung_zurueck() -> None:
    # "unknown"/"other" heisst gerade, dass die Einordnung nicht sicher ist.
    for document_type in ("unknown", "other", "request_for_documents"):
        assert _auto_confirmable({"document_type": document_type}, [], 0.95) is False

    # Eine Leistungsabrechnung legt keinen Vorgang an und braucht die
    # Rechnungs-Kernfelder deshalb nicht.
    assert _auto_confirmable({"document_type": "benefit_statement"}, [], 0.9) is False
    assert _auto_confirmable(_deterministic_fields(_ABRECHNUNG_ROH), [], 0.95) is True


def test_vollstaendige_leistungsabrechnung_bleibt_ohne_modellwert_sicher() -> None:
    fields, confidence, warnings = _validated_fields(_deterministic_fields(_ABRECHNUNG_ROH), None)
    assert confidence == 0.95
    assert warnings == []
    assert _auto_confirmable(fields, warnings, confidence) is True
