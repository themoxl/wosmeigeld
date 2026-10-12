"""Zahlenpruefung des KI-Felds (Port von sia_public ki_zahlenpruefung.js)."""

from app.assistant.number_check import Source, check, readings, source_values


def _status(text: str, *sources: Source) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(item.text, item.status, item.sources) for item in check(text, list(sources)).items]


def test_german_and_json_formats_are_both_read() -> None:
    assert [r.value for r in readings("1.234,56")] == [1234.56]
    assert sorted(r.value for r in readings("412.330")) == [412.33, 412330.0]
    assert [r.value for r in readings("1234.56")] == [1234.56]
    assert sorted(r.value for r in readings("1,234")) == [1.234, 1234.0]
    assert [r.value for r in readings("−45,10")] == [-45.1]


def test_an_answer_number_is_covered_when_a_source_rounds_to_it() -> None:
    source = Source("W3", '{"sum_eur": -1234.56, "share": 0.0213, "count": 17}')
    items = _status("Du hast 1.234,56 € ausgegeben (rund 1.235 €), 2,1 % davon, 17 Mal.", source)
    assert items == [
        ("1.234,56", "belegt", ("W3",)),
        ("1.235", "belegt", ("W3",)),
        ("2,1", "belegt", ("W3",)),
        ("17", "belegt", ("W3",)),
    ]
    # Keine Toleranz: 2,2 % deckt 2,13 % nicht.
    assert _status("Anteil 2,2 %.", source) == [("2,2", "unbelegt", ())]


def test_magnitude_words_and_percent_are_converted() -> None:
    source = Source("W1", "[[1234567, 0.125]]")
    assert _status("Etwa 1,2 Mio. € und 12,5 Prozent.", source) == [
        ("1,2", "belegt", ("W1",)),
        ("12,5", "belegt", ("W1",)),
    ]
    assert _status("Rund 1.235 Tsd. €", source) == [("1.235", "belegt", ("W1",))]


def test_small_integers_dates_times_and_list_numbers_are_not_checked() -> None:
    result = check(
        "1. Punkt\n2) Punkt\nAm 03.10.2026 um 12:30 und am 2026-09-30 hast du 3 Konten.", []
    )
    assert result.items == []
    assert result.small == 1  # die 3 Konten - gesehen, bewusst ungeprueft
    assert result.unverified == 0


def test_identifiers_and_ranges_are_not_numbers() -> None:
    assert check("Siehe W12 und Vertrag V2, Jahre 2024-2025.", []).items[0].text == "2024"
    assert len(check("Siehe W12 und Vertrag V2, Jahre 2024-2025.", []).items) == 1


def test_positions_are_python_character_indexes() -> None:
    text = "😀 Miete 950 €"
    item = check(text, [Source("W1", "950")]).items[0]
    assert text[item.start : item.end] == "950"
    assert item.start == 8  # ein Codepunkt fuer das Emoji, nicht zwei UTF-16-Einheiten


def test_sources_list_every_result_that_covers_the_number() -> None:
    first = Source("W1", "[[412.3]]")
    second = Source("W4", '{"total_eur": 412.30}')
    inputs = Source("Eingabe", "Frage nach 412,30 €", "eingabe")
    assert _status("412,30 €", first, second, inputs) == [
        ("412,30", "belegt", ("W1", "W4", "Eingabe"))
    ]
    assert check("Es waren 99,99 €.", [first]).as_dict() == {
        "items": [
            {"start": 9, "end": 14, "text": "99,99", "status": "unbelegt", "sources": []}
        ],
        "unverified": 1,
    }


def test_source_values_include_every_digit_run() -> None:
    values = source_values("Buchung vom 2026-09-30, Beleg 4711")
    assert 2026.0 in values and 4711.0 in values and 30.0 in values
