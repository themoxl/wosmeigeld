"""Echte digitale und gescannte PDFs; keine Nutzerunterlagen."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pymupdf as fitz
import pytest

from worker.health_documents import reading
from worker.health_documents.processor import (
    _auto_confirmable,
    _deterministic_fields,
    _field_evidence,
    _read_pages,
    _semantic_fingerprint,
    _tesseract_binary,
    _validated_fields,
    _vision_read,
)

LINES = [
    "Muster Krankenversicherung AG",
    "Leistungsabrechnung Nr. 26 00 00004",
    "Bescheiddatum: 11.10.2026",
    "Anna Muster, geb. 01.01.1990",
    "Rechnungsnummer Datum Rechnungsbetrag Bewilligt",
    "R-100 01.10.2026 200,00 125,00",
    "R-101 02.10.2026 200,00 125,00",
    "Summe der Rechnungsbetraege / Erstattung 400,00 250,00",
    "Auszahlungsbetrag 250,00",
]


def document(*, scanned: bool = False, rotation: int = 0, mixed: bool = False) -> bytes:
    with fitz.open() as source:
        page = source.new_page()
        for index, line in enumerate(LINES):
            page.insert_text((50, 60 + index * 30), line, fontsize=12)
        page.set_rotation(rotation)
        if not scanned and not mixed:
            return bytes(source.tobytes())
        png = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False).tobytes("png")
        with fitz.open() as result:
            if mixed:
                result.insert_pdf(source)
            scan = result.new_page(width=page.rect.width, height=page.rect.height)
            scan.insert_image(scan.rect, stream=png)
            return bytes(result.tobytes())


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_native_words_and_amount_have_valid_geometry(rotation: int) -> None:
    content = document(rotation=rotation)
    pages = reading.native_pages(content, "application/pdf")
    assert pages[0].method == "pdf_text"
    assert pages[0].rotation == rotation
    locations = reading.field_locations(25000, "approved_minor", pages)
    assert locations and locations[0]["page_number"] == 1
    x0, y0, x1, y1 = locations[0]["box"]
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert pages[0].text.count("R-100") == 1


def test_populated_footer_alone_is_not_a_usable_text_layer() -> None:
    words = [reading.Word("Footerwort", (0.01 * i, 0.95, 0.01 * i + 0.01, 0.96))
             for i in range(15)]
    assert not reading.text_usable(words)


def test_statement_positions_have_their_own_amounts_and_invoice_numbers() -> None:
    fields = _deterministic_fields(reading.marked_text(reading.native_pages(
        document(), "application/pdf")))
    assert fields["approved_minor"] == 25000
    assert fields["statement_number"] == "260000004"
    assert [item["invoice_number"] for item in fields["items"]] == ["R-100", "R-101"]
    assert [item["approved_minor"] for item in fields["items"]] == [12500, 12500]
    assert all(item["page_number"] == 1 for item in fields["items"])


def test_conflicting_model_amount_cannot_be_auto_confirmed() -> None:
    rule = _deterministic_fields(
        "Praxis Beispiel\nRechnung\nRechnungsdatum: 10.10.2026\nGesamtbetrag: 100,00"
    )
    fields, confidence, warnings = _validated_fields(rule, {"billed_minor": 99900, "confidence": 1})
    assert "field_conflict:billed_minor" in warnings
    assert confidence < 0.85
    assert not _auto_confirmable(fields, warnings, confidence)


def test_dates_are_not_guessed_from_birth_or_treatment_dates() -> None:
    fields = _deterministic_fields(
        "Praxis Beispiel\nRechnung\nGeburtsdatum: 01.01.1990\n"
        "Behandlung am 10.10.2026\nGesamtbetrag: 100,00"
    )
    assert fields["invoice_date"] is None


def test_corrected_payout_and_different_providers_are_not_duplicates() -> None:
    rule = _deterministic_fields(
        "Praxis Beispiel\nRechnung Nr. R-100\nRechnungsdatum: 10.10.2026\nGesamtbetrag: 100,00"
    )
    assert _semantic_fingerprint(rule) != _semantic_fingerprint(
        {**rule, "provider_name": "Praxis Andere"}
    )
    statement = _deterministic_fields("\n".join(LINES))
    assert _semantic_fingerprint(statement) != _semantic_fingerprint(
        {**statement, "approved_minor": 20000}
    )


async def test_zero_amount_has_a_fundstelle_and_no_medical_freetext_is_saved() -> None:
    content = document()
    pages = reading.native_pages(content, "application/pdf")
    fields = {"billed_minor": 40000, "approved_minor": 25000}
    evidence, warnings = await _field_evidence(content, "application/pdf", fields,
        fields, None, set(), pages, AsyncMock(), AsyncMock(return_value=False))
    assert not warnings
    assert evidence["approved_minor"]["locations"]
    assert "Muster Krankenversicherung" not in str(evidence)
    zero = reading.PageReading(1, [reading.Word("0,00", (0.2, 0.2, 0.3, 0.3))], "pdf_text")
    evidence, _ = await _field_evidence(content, "application/pdf", {"approved_minor": 0},
        {"approved_minor": 0}, None, set(), [zero], AsyncMock(), AsyncMock(return_value=False))
    assert "approved_minor" in evidence and evidence["approved_minor"]["locations"]


async def test_one_failed_page_does_not_discard_other_pages() -> None:
    pages = [reading.PageReading(1), reading.PageReading(2), reading.PageReading(3)]
    def success(number: int) -> reading.PageReading:
        return reading.PageReading(number,
            [reading.Word("Auszahlungsbetrag", (0.1, 0.1, 0.4, 0.2))], "ocr")
    def ocr(*args):
        if args[2] == 2:
            raise RuntimeError("ocr_failed")
        return success(args[2])
    with (patch.object(reading, "native_pages", return_value=pages),
          patch.object(reading, "languages_for", return_value="deu"),
          patch.object(reading, "ocr_page", side_effect=ocr),
          patch("worker.health_documents.processor._tesseract_binary", return_value="fake")):
        result, warnings = await _read_pages(b"image", "image/png", None,
                                             AsyncMock(), AsyncMock(return_value=False))
    assert result[0].method == result[2].method == "ocr"
    assert "ocr_failed:2" in warnings


async def test_reading_checks_cancellation_between_pages() -> None:
    with patch.object(reading, "native_pages", return_value=[reading.PageReading(1)]):
        with pytest.raises(asyncio.CancelledError):
            await _read_pages(
                b"image", "image/png", None, AsyncMock(), AsyncMock(return_value=True)
            )


async def test_repeated_amounts_are_highlighted_in_their_invoice_rows() -> None:
    content = document()
    pages = reading.native_pages(content, "application/pdf")
    fields = _deterministic_fields(reading.marked_text(pages))
    evidence, warnings = await _field_evidence(content, "application/pdf", fields,
        fields, None, set(), pages, AsyncMock(), AsyncMock(return_value=False))
    assert not any("items" in warning for warning in warnings)
    for index in range(2):
        amount = evidence[f"items.{index}.approved_minor"]["locations"]
        number = evidence[f"items.{index}.invoice_number"]["locations"]
        assert len(amount) == len(number) == 1
        assert abs(amount[0]["box"][1] - number[0]["box"][1]) < 0.01
    assert evidence["items.0.approved_minor"] != evidence["items.1.approved_minor"]


def test_rotated_ocr_orders_text_in_the_rendered_frame() -> None:
    # Die Fundstellen bleiben ungedreht; die Lese-Reihenfolge ist die des Bildes.
    page = reading.PageReading(1, [
        reading.Word("R-100", (0.1, 0.8, 0.2, 0.9), line=(1, 0, 1)),
        reading.Word("125,00", (0.1, 0.5, 0.2, 0.6), line=(1, 0, 1)),
        reading.Word("R-101", (0.3, 0.8, 0.4, 0.9), line=(1, 0, 2)),
        reading.Word("100,00", (0.3, 0.5, 0.4, 0.6), line=(1, 0, 2)),
    ], "ocr", rotation=90)
    assert page.text == "R-100 125,00\nR-101 100,00"


async def test_local_vision_is_blind_and_always_requires_review() -> None:
    context = SimpleNamespace(settings=SimpleNamespace(provider="local"), scope=None,
        target=SimpleNamespace(selected=SimpleNamespace(kind="vlm", capabilities=[])))
    result = SimpleNamespace(value={"lines": [
        {"text": "Auszahlungsbetrag 125,00", "box": [0.1, 0.2, 0.8, 0.3]},
        {"text": "ungueltige Geometrie", "box": [-1, 0, 1, 1]},
    ]}, reason_code=None)
    with patch("worker.health_documents.processor.llm.answers.ask",
               AsyncMock(return_value=result)) as ask:
        page, reason = await _vision_read(context, b"synthetic PNG", 2)
    assert page is not None and reason is None
    assert page.method == "vision" and "vision_requires_review" in page.warnings
    assert page.text == "Auszahlungsbetrag 125,00"
    request = ask.await_args.args[1]
    assert "125,00" not in str(request.messages)
    assert request.reasoning_override == "off"
    context.settings.provider = "anthropic"
    with patch("worker.health_documents.processor.llm.answers.ask", AsyncMock()) as ask:
        assert await _vision_read(context, b"synthetic PNG", 2) == (None, "vision_unavailable")
        ask.assert_not_awaited()


async def test_uncertain_statement_item_is_reread_in_a_crop() -> None:
    fields = {"items": [{"invoice_number": "R-100", "approved_minor": 12500}]}
    words = [reading.Word("R-100", (0.1, 0.2, 0.2, 0.22), 95),
             reading.Word("125,00", (0.4, 0.2, 0.5, 0.22), 30)]
    pages = [reading.PageReading(1, words, "ocr")]
    crop = reading.PageReading(1, [reading.Word("125,00", (0.4, 0.2, 0.5, 0.22), 90)],
                               "ocr_crop")
    with (patch("worker.health_documents.processor._tesseract_binary", return_value="fake"),
          patch.object(reading, "languages_for", return_value="deu"),
          patch.object(reading, "ocr_page", return_value=crop) as ocr):
        evidence, warnings = await _field_evidence(b"synthetic", "application/pdf", fields,
            fields, None, set(), pages, AsyncMock(), AsyncMock(return_value=False))
    assert not warnings
    assert ocr.call_args.args[-1] is not None
    assert evidence["items.0.approved_minor"]["locations"][0]["method"] == "ocr_crop"


def test_model_conflict_in_statement_items_requires_review() -> None:
    rule = _deterministic_fields("\n".join(LINES))
    changed = [{**item, "approved_minor": 10000} for item in rule["items"]]
    _, confidence, warnings = _validated_fields(rule, {"items": changed})
    assert "field_conflict:items" in warnings
    assert confidence < 0.85


async def test_real_tesseract_reads_scanned_and_mixed_documents() -> None:
    if _tesseract_binary() is None:
        pytest.skip("Tesseract nicht installiert")
    for mixed in (False, True):
        pages, warnings = await _read_pages(document(scanned=True, mixed=mixed),
            "application/pdf", None, AsyncMock(), AsyncMock(return_value=False))
        assert not warnings
        assert pages[-1].method == "ocr"
        assert reading.field_locations(25000, "approved_minor", pages)
        fields = _deterministic_fields(reading.marked_text(pages))
        # Bei einem Mischdokument ist derselbe Inhalt zu Testzwecken zweimal
        # enthalten; hier wird die gescannte Seite allein ausgewertet.
        fields = _deterministic_fields(reading.marked_text([pages[-1]]))
        assert fields["approved_minor"] == 25000
        assert len(fields["items"]) == 2
