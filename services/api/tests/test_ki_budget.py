"""Budgetrechnung vor jedem Modellaufruf (app/llm/budget.py) - reine Funktionen.

Port von sia_public/build/ki_budget_selftest.js. Geprueft wird: max_tokens ist
der ganze freie Platz (W - E - A); "passt" erst, wenn die Eingabe nicht in die
Antwortreserve reicht; exakte Zaehlung ueber den Vorgaenger nur fuer dasselbe
Anfangsstueck und dieselben Werkzeuge; gleichzeitige Aufrufe verkleinern das
freie Fenster; Budget und Seitengroesse wachsen mit dem Fenster; die Messung je
Modell als laufende Summe; fehlende Angaben scheitern laut - kein Rueckfall.
"""

import base64
import math
from typing import Any

import pytest

from app.llm.budget import (
    Basis,
    BudgetInputError,
    MeasurementSums,
    chars_of,
    compute,
    page_chars,
    parts_of,
    tools_signature,
    update_measurement,
)

RATIO = 3.0
ERROR = 0.1


def _text(n: int) -> str:
    return "x" * n


SYSTEM = {"role": "system", "content": _text(2970)}
QUESTION = {"role": "user", "content": _text(2996)}
TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "run_sql", "parameters": {}}}
]
ANSWER = {
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "run_sql", "arguments": "{}"}}
    ],
}
RESULT = {"role": "tool", "tool_call_id": "c1", "content": _text(300)}


def _budget(window: int, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
    params: dict[str, Any] = {
        "window": window,
        "messages": messages,
        "chars_per_token": RATIO,
        "estimate_error": ERROR,
        "reserve_share": 0.25,
    }
    params.update(kwargs)
    return compute(**params)


def test_estimated_input_allowance_reserve_and_answer_budget() -> None:
    budget = _budget(25000, [SYSTEM, QUESTION])

    assert budget.E == math.ceil((2970 + 6 + 2996 + 4) / RATIO)
    assert budget.E_estimated == budget.E
    assert budget.estimated_part == budget.E
    assert budget.A == math.ceil(budget.E * ERROR)
    assert budget.R == 6250
    # Antwortbudget = der ganze freie Platz, nicht die Reserve.
    assert budget.max_tokens == 25000 - budget.E - budget.A
    assert budget.fits
    assert not budget.exact
    assert budget.chars == 2970 + 6 + 2996 + 4


def test_input_reaching_into_the_reserve_does_not_fit() -> None:
    large = {"role": "user", "content": _text(int(RATIO * 18000))}

    small_window = _budget(25000, [SYSTEM, large])
    assert not small_window.fits
    # Dann bliebe weniger als die Reserve fuer die Antwort.
    assert small_window.max_tokens < small_window.R

    assert _budget(120000, [SYSTEM, large]).fits


def test_exact_count_over_the_same_prefix_and_the_same_tools() -> None:
    basis = Basis(
        messages=(SYSTEM, QUESTION),
        tools_signature=tools_signature(TOOLS),
        prompt_tokens=2100,
    )
    budget = _budget(25000, [SYSTEM, QUESTION, ANSWER, RESULT], tools=TOOLS, basis=basis)

    new_part = math.ceil(chars_of([ANSWER, RESULT]) / RATIO)
    assert budget.exact
    assert budget.E == 2100 + new_part
    # Der Schaetzabstand gilt nur fuer den geschaetzten Teil.
    assert budget.estimated_part == new_part
    assert budget.A == math.ceil(new_part * ERROR)
    # Zum Vergleich steht die Schaetzung der ganzen Eingabe daneben.
    full = chars_of([SYSTEM, QUESTION, ANSWER, RESULT], TOOLS)
    assert budget.E_estimated == math.ceil(full / RATIO)


def test_equal_content_counts_as_the_same_prefix() -> None:
    """In Python vergleicht die Pruefung Inhalte (dict-Gleichheit), nicht
    Objektidentitaet: was gleich aussieht, hat der Server gleich gezaehlt."""
    basis = Basis(messages=(SYSTEM, QUESTION), tools_signature="", prompt_tokens=2100)
    copy = [dict(SYSTEM), dict(QUESTION), ANSWER]

    assert _budget(25000, copy, basis=basis).exact


def test_a_different_prefix_or_other_tools_fall_back_to_the_estimate() -> None:
    basis = Basis(
        messages=(SYSTEM, QUESTION),
        tools_signature=tools_signature(TOOLS),
        prompt_tokens=2100,
    )
    changed = {"role": "system", "content": _text(2969)}

    assert not _budget(25000, [changed, QUESTION, ANSWER], tools=TOOLS, basis=basis).exact
    assert not _budget(25000, [SYSTEM, QUESTION, ANSWER], basis=basis).exact
    # Ein Vorgaenger ohne gemeldete Token zaehlt nicht.
    empty = Basis(messages=(SYSTEM,), tools_signature="", prompt_tokens=0)
    assert not _budget(25000, [SYSTEM, QUESTION], basis=empty).exact


def test_calls_occupying_the_window_shrink_the_free_part() -> None:
    budget = _budget(25000, [SYSTEM, QUESTION], occupied_by_others=10000)

    assert budget.W_free == 15000
    assert budget.max_tokens == 15000 - budget.E - budget.A
    # Die Reserve bleibt ein Anteil des ganzen Fensters.
    assert budget.R == 6250


def test_budget_and_page_size_grow_with_the_window() -> None:
    windows = [4096, 25000, 56000, 120000]
    budgets = [_budget(window, [SYSTEM, QUESTION]).max_tokens for window in windows]
    pages = [page_chars(window, 0.1, RATIO) for window in windows]

    assert budgets == sorted(budgets) and len(set(budgets)) == len(budgets)
    assert pages[1] == 7500
    assert pages[3] == 36000
    assert pages == sorted(pages) and len(set(pages)) == len(pages)


def test_parts_break_the_input_down() -> None:
    now = {"role": "user", "content": "jetzt"}
    parts = parts_of([SYSTEM, QUESTION, ANSWER, RESULT, now], TOOLS, RATIO)

    assert parts.system == math.ceil(2976 / RATIO)
    # Die LETZTE Nutzernachricht ist die aktuelle Frage, die erste Verlauf.
    assert parts.current == math.ceil(len("jetzt" + "user") / RATIO)
    assert parts.history == math.ceil((2996 + 4) / RATIO)
    assert parts.tool_results > 0
    assert parts.tool_defs > 0
    assert set(parts.as_dict()) == {"system", "history", "tool_results", "tool_defs", "current"}


def test_run_budget_has_the_contract_shape() -> None:
    """Form wie RunBudget in docs/KI-FELD-SCHNITTSTELLE.md."""
    shape = _budget(25000, [SYSTEM, QUESTION]).as_run_budget()

    assert set(shape) == {
        "W",
        "W_free",
        "E",
        "E_estimated",
        "exact",
        "A",
        "R",
        "max_tokens",
        "parts",
    }


def test_measurement_is_a_running_sum_weighted_by_size() -> None:
    first = update_measurement(None, 3000, 1000, 500)
    assert first.chars_per_token == 3
    assert first.estimate_error is None  # erst ab der zweiten Messung
    assert first.largest_need_tokens == 500
    assert first.calls == 1

    second = update_measurement(first, 3600, 1000, 300)
    assert second.estimate_error == pytest.approx(0.2)
    assert second.chars_per_token == pytest.approx(3.3)
    assert second.largest_need_tokens == 500

    # Ein winziger Aufruf (Verbindungstest, 18 Token) verschiebt beides kaum.
    tiny = update_measurement(second, 24, 18, 2)
    assert tiny.chars_per_token == pytest.approx(second.chars_per_token, abs=0.03)
    assert tiny.estimate_error is not None and tiny.estimate_error < 0.21

    large = update_measurement(tiny, 33000, 10000, 0)
    assert large.chars_per_token is not None and 3.25 < large.chars_per_token < 3.31


def test_a_call_without_reported_tokens_does_not_measure() -> None:
    before = MeasurementSums(sum_chars=3000, sum_prompt_tokens=1000, calls=1)
    after = update_measurement(before, 3000, 0, 0)

    assert after.sum_chars == 3000
    assert after.calls == 1


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"window": 0}, "window"),
        ({"window": None}, "window"),
        ({"chars_per_token": None}, "chars_per_token"),
        ({"chars_per_token": 0}, "chars_per_token"),
        ({"estimate_error": None}, "estimate_error"),
        ({"estimate_error": -0.1}, "estimate_error"),
        ({"reserve_share": None}, "answer_reserve_share"),
        ({"reserve_share": 1}, "answer_reserve_share"),
        ({"reserve_share": 0}, "answer_reserve_share"),
    ],
)
def test_missing_inputs_fail_loudly(overrides: dict[str, Any], field: str) -> None:
    """Kein Rueckfall auf eine Zahl im Code: was fehlt, wird benannt."""
    params: dict[str, Any] = {
        "window": 25000,
        "messages": [SYSTEM, QUESTION],
        "chars_per_token": RATIO,
        "estimate_error": ERROR,
        "reserve_share": 0.25,
    }
    params.update(overrides)
    with pytest.raises(BudgetInputError) as caught:
        compute(**params)
    assert caught.value.field == field


def test_page_size_without_inputs_fails_loudly() -> None:
    with pytest.raises(BudgetInputError):
        page_chars(1000, 0, RATIO)
    with pytest.raises(BudgetInputError):
        page_chars(None, 0.1, RATIO)
    with pytest.raises(BudgetInputError):
        page_chars(1000, 0.1, None)


def test_describe_names_every_part_in_german_numbers() -> None:
    text = _budget(32768, [SYSTEM, QUESTION]).describe()

    assert "Fenster 32.768 Token" in text
    assert "Antwortreserve 8.192" in text
    assert "geschätzt" in text


def test_image_budget_counts_pixels_and_new_images_after_exact_prefix() -> None:
    # PNG header alone suffices for dimensions; Base64 padding/text is not
    # counted as language input. A second image must still consume tokens.
    header = b"\x89PNG\r\n\x1a\n" + b"\0" * 8
    header += (560).to_bytes(4, "big") + (560).to_bytes(4, "big")
    url = "data:image/png;base64," + base64.b64encode(header + b"\0" * 20000).decode()
    message = {"role": "user", "content": [
        {"type": "text", "text": "Bitte ablesen"},
        {"type": "image_url", "image_url": {"url": url}},
    ]}
    first = _budget(32768, [SYSTEM, message])
    text_only = _budget(32768, [SYSTEM, {"role": "user", "content": "Bitte ablesen"}])
    assert first.E - text_only.E == 400
    assert first.parts.current - text_only.parts.current == 400
    basis = Basis((SYSTEM, message), tools_signature(None), 1600)
    second = compute(window=32768, messages=[SYSTEM, message, message],
                     chars_per_token=RATIO, estimate_error=ERROR, reserve_share=0.25,
                     basis=basis)
    assert second.exact and second.E > 2000
    assert second.E == 1600 + second.estimated_part
