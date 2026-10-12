"""Erkennung v2 - reine Funktionstests ohne Datenbank (Abschnitt 6 des Plans).

`contract_detection_service` ist bewusst zweigeteilt: alles Rechnende arbeitet
auf `DetectionTx`, nur die Funktionen am Dateiende sprechen mit Postgres. Diese
Datei nutzt genau diese Trennung und laeuft ohne Supabase und ohne HTTP.
Dadurch sind hier Faelle pruefbar, die als Datenbanktest zu teuer waeren:
Grenzwerte der Trefferquoten, einzelne Score-Bestandteile und die
Schluesselableitung Zeichen fuer Zeichen.

Die erwarteten Zahlen sind von Hand nachgerechnet und nicht aus der
Implementierung abgeleitet - ein Test, der dieselbe Formel noch einmal
aufschreibt, wuerde eine falsche Gewichtung mitschleifen statt sie zu melden.

Der wichtigste Test dieser Datei ist
`test_the_rent_is_monthly_although_weekly_matches_every_gap`: er haelt genau
den Fehler fest, wegen dem die Miete in der Liste als "Woechentlich" stand.
"""

from datetime import date, timedelta
from uuid import UUID

import pytest

from app.services import contract_detection_service as det
from app.services.contract_matching_service import normalize_purpose, payee_key

_ACCOUNT = UUID("11111111-1111-4111-8111-111111111111")

# MT940 legt den Verwendungszweck als strukturiertes Feld :86: ab und speichert
# ihn genau so in `transactions.purpose_raw`. Der Rahmen (Buchungstext ?00,
# Primanota ?10, Bankverbindung ?30/?31) steht bei JEDEM Empfaenger gleich da.
_MT940_FRAME = (
    "105?00EINZUGSERMAECHTIGUNG?10931?20{}?30ABCDEFGHXXX?31DE12345678901234567890"
)

# Geschaeftsvorfallcodes aus parsers/mt940.py: Lastschrift und Kartenumsatz.
MT940_DIRECT_DEBIT = "N105"
MT940_CARD = "N083"


def _tx(
    suffix: int,
    booked_date: date,
    amount_minor: int,
    *,
    payee: str = "Testfirma GmbH",
    purpose: str = "",
    import_format: str | None = None,
    bank_transaction_code: str | None = None,
    category_id: UUID | None = None,
) -> det.DetectionTx:
    """Eine Buchung so, wie `load_detection_transactions` sie liefern wuerde."""
    return det.DetectionTx(
        transaction_id=UUID(f"22222222-2222-4222-8222-{suffix:012d}"),
        account_id=_ACCOUNT,
        payee_id=None,
        payee_label=payee,
        payee_key=payee_key(payee),
        amount_minor=amount_minor,
        booked_date=booked_date,
        purpose_raw=purpose or None,
        purpose_key=normalize_purpose(purpose),
        bank_transaction_code=bank_transaction_code,
        import_format=import_format,
        category_id=category_id,
    )


def _monthly(
    day: int,
    months: list[int],
    amount_minor: int,
    *,
    payee: str = "Testfirma GmbH",
    purpose: str = "",
    import_format: str | None = None,
    bank_transaction_code: str | None = None,
    category_id: UUID | None = None,
) -> list[det.DetectionTx]:
    """Monatliche Buchungen am selben Zahltag - der Normalfall eines Vertrags."""
    return [
        _tx(
            index,
            date(2026, month, day),
            amount_minor,
            payee=payee,
            purpose=purpose,
            import_format=import_format,
            bank_transaction_code=bank_transaction_code,
            category_id=category_id,
        )
        for index, month in enumerate(months, start=1)
    ]


# ---------------------------------------------------------------------------
# 6.1 Vertragsschluessel aus dem Verwendungszweck
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        ("LASTSCHRIFT MANDATSREF: M-4711/AB MUSTERFUNK", "M-4711/AB"),
        ("MUSTERFUNK KUNDENNR. 1234567890 RECHNUNG", "1234567890"),
        ("VERTRAGSNR 998877-K MONATSBEITRAG", "998877-K"),
        ("MUSTERKASSE VERSICHERUNGSSCHEIN: VS-0815-4711", "VS-0815-4711"),
        ("BEITRAG GLAEUBIGER-ID DE98ZZZ09999999999", "DE98ZZZ09999999999"),
        # Dieselbe Kennung, wie die Bank sie mit Umlaut schreibt.
        ("BEITRAG GLÄUBIGER-ID DE98ZZZ09999999999", "DE98ZZZ09999999999"),
    ],
)
def test_the_known_patterns_are_read_from_a_single_purpose(
    purpose: str, expected: str
) -> None:
    """Die fuenf im Plan genannten Muster, jedes einzeln nachgewiesen."""
    assert det.extract_mandate_key(purpose) == expected
    assert det.extract_contract_key([purpose]) == (expected, "mandate")


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        # Schreibweisen eines Telefonanbieters (Abschnitt 19.3) - fuehrende
        # Nullen bleiben Teil des Schluessels, ein numerischer Vergleich wuerde
        # 0011223344 und 11223344 gleichsetzen.
        ("MUSTERFUNK MOBILFUNK KUNDENKONTO 0011223344", "0011223344"),
        ("MUSTERFUNK MOBILFUNK KUNDENKONTO: 0011223344", "0011223344"),
        ("MUSTERFUNK MOBILFUNK KUNDEN-KONTO 0011223344", "0011223344"),
        ("MUSTERFUNK FESTNETZ VERTRAGSKONTO 1122334455", "1122334455"),
        ("MUSTERFUNK FESTNETZ VERTRAGSKONTO NR. 1122334455", "1122334455"),
        # Zeilenumbruch statt Leerzeichen zwischen Bezeichner und Wert -
        # normalize_purpose_for_key zieht ihn vor der Mustersuche zusammen.
        ("MUSTERFUNK FESTNETZ VERTRAGSKONTO\n1122334455", "1122334455"),
    ],
)
def test_the_musterfunk_key_variants_are_read(purpose: str, expected: str) -> None:
    assert det.extract_mandate_key(purpose) == expected
    assert det.extract_contract_key([purpose]) == (expected, "mandate")


def test_different_musterfunk_products_keep_different_keys_even_when_close_in_time() -> None:
    """Kundenkonto (Mobilfunk) und Vertragskonto (Festnetz) sind zwei
    verschiedene Vertraege desselben Anbieters - nie derselbe Schluessel,
    egal wie nah beieinander die Zahlungen liegen."""
    mobilfunk = "MUSTERFUNK MOBILFUNK KUNDENKONTO 0011223344"
    festnetz = "MUSTERFUNK FESTNETZ VERTRAGSKONTO 1122334455"
    assert det.extract_mandate_key(mobilfunk) != det.extract_mandate_key(festnetz)


def test_the_sepa_reference_is_found_although_it_sticks_to_the_previous_digits() -> None:
    """Nach dem Aufloesen des Bankfeldes klebt `MREF+` an der Vorziffer.

    Eine Wortgrenze gibt es dort nicht - mit `\\b` vor der Kennung bliebe jede
    Mandatsreferenz unsichtbar und der Schluessel fiele auf die generische
    Ableitung zurueck.
    """
    glued = "105?00LASTSCHRIFT?10100100?20MREF+100000000001?21CRED+DE98ZZZ09999999999"
    assert det.extract_mandate_key(glued) == "100000000001"


def test_the_value_ends_at_the_next_sepa_tag_not_at_a_blank() -> None:
    """SEPA-Kennungen stehen unmittelbar aneinander.

    Ohne die Begrenzung auf die naechste Kennung waere die Glaeubiger-ID Teil
    der Mandatsreferenz - und damit fuer jeden Vertrag desselben Anbieters
    dieselbe.
    """
    assert det.extract_mandate_key("MREF+1000-KITA-0001CRED+DE12ZZZ00000012345") == (
        "1000-KITA-0001"
    )


def test_the_creditor_id_only_wins_when_nothing_more_specific_is_there() -> None:
    """Die Glaeubiger-ID benennt den ANBIETER, nicht den Vertrag.

    Zwei Vertraege desselben Anbieters teilen sie sich; sie darf deshalb nur
    greifen, wenn keine Mandats-, Kunden- oder Vertragsnummer dasteht.
    """
    both = "VERTRAGSNR 4711234 CRED+DE98ZZZ09999999999"
    assert det.extract_mandate_key(both) == "4711234"
    assert det.extract_mandate_key("BEITRAG CRED+DE98ZZZ09999999999") == (
        "DE98ZZZ09999999999"
    )


def test_a_mandate_value_ends_at_the_next_sepa_tag() -> None:
    """Regressionstest: die Bank haengt die naechste Kennung ohne Trennzeichen an.

    Mit einem reinen `\\S+` wanderte die End-to-End-Referenz in den Schluessel -
    und die wechselt bei JEDER Zahlung. Der "Vertragsschluessel" benannte dann
    genau eine Buchung.
    """
    purposes = [
        f"MANDATSREF. M4711EREF+20260{month}0100001 MUSTERFUNK RECHNUNG"
        for month in range(1, 7)
    ]
    assert det.extract_mandate_key(purposes[0]) == "M4711"
    assert det.extract_contract_key(purposes) == ("M4711", "mandate")


def test_a_mandate_value_that_appears_only_once_is_no_contract_key() -> None:
    """Regressionstest: derselbe Schutz wie in `_bucket_by_contract_key`.

    Steht die Kundennummer in genau EINEM von zwoelf Verwendungszwecken, benennt
    sie einen Beleg und keinen Vertrag. Als `contract_key` wuerde sie im
    Abgleich eine Identitaet von 1.00 behaupten, die es nicht gibt - und der
    Sollbetrag des Vertrags haenge an einer fremden Buchung.
    """
    purposes = [f"MUSTERFUNK RECHNUNG {month:02d}" for month in range(1, 12)]
    assert det.extract_contract_key([*purposes, "MUSTERFUNK KUNDENNR 4711 RECHNUNG"]) is None
    # Zweimal derselbe Wert genuegt.
    assert det.extract_contract_key(
        ["MUSTERFUNK KUNDENNR 4711 RECHNUNG", "MUSTERFUNK KUNDENNR 4711 RECHNUNG"]
    ) == ("4711", "mandate")


def test_the_generic_derivation_needs_six_characters_and_a_digit() -> None:
    """Ohne Ziffer bliebe nur der Anbietername - und den kennen wir schon."""
    with_digit = ["MIETE WOHNUNG 12 BERLIN JANUAR", "MIETE WOHNUNG 12 BERLIN FEBRUAR"]
    assert det.extract_contract_key(with_digit) == ("MIETE WOHNUNG 12 BERLIN", "purpose_pattern")

    without_digit = ["BEITRAG SPORTVEREIN JANUAR", "BEITRAG SPORTVEREIN FEBRUAR"]
    assert det.extract_contract_key(without_digit) is None

    # Eine einzelne Buchung hat keine "gemeinsame" Zeichenkette.
    assert det.extract_contract_key(["MIETE WOHNUNG 12 BERLIN"]) is None


def test_volatile_parts_are_removed_before_the_common_substring_is_built() -> None:
    """Ohne Vorreinigung gewinnt "/2026" gegen die Vertragsnummer.

    Es kommt in jedem Verwendungszweck desselben Jahres vor und ist damit
    laenger gemeinsam als alles, was den Vertrag wirklich benennt.
    """
    cleaned = det.strip_volatile_parts(
        det.normalize_purpose_for_key(
            "Musterfunk Rechnung 08/2026 vom 01.08.2026 Betrag 39,95 EUR KW32 August"
        )
    )
    assert cleaned == "MUSTERFUNK RECHNUNG VOM BETRAG EUR"

    # Bleibt nach dem Abraeumen keine Ziffer uebrig, entsteht kein Schluessel.
    assert (
        det.extract_contract_key(
            ["STADTWERKE ABSCHLAG 08/2026", "STADTWERKE ABSCHLAG 09/2026"]
        )
        is None
    )


def test_the_bank_frame_is_unwrapped_before_the_key_is_derived() -> None:
    """Der Rahmen des Feldes :86: steht bei allen Empfaengern gleich da.

    Ohne das Aufloesen waere er selbst die laengste gemeinsame Zeichenkette -
    ein Schluessel, der alle Vertraege aller Empfaenger auf einen Nenner zieht
    und damit das genaue Gegenteil dessen bewirkt, wofuer er da ist.
    """
    netflix = _MT940_FRAME.format("NETFLIX ABO 01.02.2026")
    zalando = _MT940_FRAME.format("ZALANDO SE 01.02.2026")

    assert det.unwrap_structured_purpose(netflix) == "NETFLIX ABO 01.02.2026"
    # Der gemeinsame Rahmen enthaelt Ziffern und ist lang genug - genau deshalb
    # waere er ohne das Aufloesen als Schluessel durchgegangen.
    assert "ABCDEFGHXXX" in netflix and "ABCDEFGHXXX" in zalando
    assert det.extract_contract_key([netflix, zalando]) is None


def test_a_word_broken_across_two_subfields_is_joined_without_a_blank() -> None:
    """Die Bank bricht Woerter ueber die Feldgrenze: "- MUS" + "TER Versicherungen"."""
    split_word = "105?00LASTSCHRIFT?10931?20BEITRAG - MUS?21TER VERSICHERUNGEN?30ABCDEFGH"
    assert det.unwrap_structured_purpose(split_word) == "BEITRAG - MUSTER VERSICHERUNGEN"


def test_a_partial_token_is_not_accepted_as_a_contract_key() -> None:
    """Die gemeinsame Zeichenkette endet gern mitten in einer Zahl.

    Aus "LOHN / GEHALT 01 2026" und "... 02 2026" wird "LOHN / GEHALT 0". Das
    erfuellt die Bedingung "mindestens eine Ziffer" formal, ist aber ein
    Bruchstueck - und wuerde beim Abgleich eine Identitaet von 1.00 behaupten,
    die es nicht gibt.
    """
    assert det.extract_contract_key(["LOHN / GEHALT 01 2026", "LOHN / GEHALT 02 2026"]) is None


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        ("KLARNA RATE 3/12 SOFA", (3, 12)),
        ("KLARNA RATE 3 VON 12", (3, 12)),
        ("TEILZAHLUNG 5/24 KUECHE", (5, 24)),
        # Mehr bezahlte als vereinbarte Raten ist kein Ratenplan, sondern ein
        # Datum oder eine Belegnummer.
        ("RATE 13/12", None),
        ("KLARNA BESTELLUNG", None),
    ],
)
def test_an_installment_plan_is_read_from_the_purpose(
    purpose: str, expected: tuple[int, int] | None
) -> None:
    assert det.extract_installment_plan(purpose) == expected
    # Der Plan nennt die Funktion an einer Stelle so; der Alias haelt beide
    # Namen gueltig.
    assert det.parse_installments is det.extract_installment_plan


def test_collector_accounts_are_recognised_by_their_first_token() -> None:
    """Eine Gleichheitsmenge wuerde an Rechtsformzusaetzen scheitern."""
    assert det.is_collector_account(payee_key("PayPal (Europe) S.a r.l. et Cie, S.C.A."))
    assert det.is_collector_account(payee_key("Klarna Bank AB"))
    assert not det.is_collector_account(payee_key("Musterfunk Deutschland GmbH"))
    assert not det.is_collector_account("")


# ---------------------------------------------------------------------------
# 6.3 Rhythmuserkennung
# ---------------------------------------------------------------------------


def test_the_rent_is_monthly_although_weekly_matches_every_gap() -> None:
    """Der Regressionstest fuer den Scoring-Fehler (Abschnitt 6.3).

    Ein einziger vorgezogener Zahltag erzeugt die Abstaende 31/28/25/36/31.
    "Woechentlich mit ausgelassenen Wochen" erklaert JEDEN davon (22 bis 34
    Tage bei vier Perioden), "monatlich" nur drei von fuenf. Die alte Fassung
    sortierte zuerst nach dieser Trefferquote und filterte monatlich unterhalb
    von 75 % sogar ganz heraus - deshalb stand die Miete als "Woechentlich" in
    der Liste.

    Entscheidend ist jetzt zuerst die Zahl der DIREKTEN Periodentreffer.
    """
    rent = [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 3, 26),  # sechs Tage vorgezogen
        date(2026, 5, 1),
        date(2026, 6, 1),
    ]
    assert [(right - left).days for left, right in zip(rent, rent[1:], strict=False)] == [
        31,
        28,
        25,
        36,
        31,
    ]
    assert det.infer_frequency(rent) == "monthly"


def test_weekly_needs_six_dates_before_it_is_allowed_at_all() -> None:
    """Ohne diese Sperre durfte die Trefferquote nicht auf 0.60 sinken.

    Der Wochenrhythmus erklaert mit ausgelassenen Perioden fast jeden Abstand;
    fuenf Datenpunkte sind dafuer kein Beleg.
    """
    weekly = [date(2026, 1, 5) + timedelta(days=7 * step) for step in range(5)]
    assert det.infer_frequency(weekly) is None
    assert det.infer_frequency([*weekly, date(2026, 2, 9)]) == "weekly"


def test_weekly_loses_against_a_monthly_explanation_that_carries_itself() -> None:
    """Auch mit sechs Datenpunkten und Vertragsschluessel.

    Die Sperre setzt voraus, dass "monatlich" die Reihe ueberhaupt traegt: hier
    erklaert es vier von fuenf Abstaenden direkt, der Wochenrhythmus keinen
    einzigen. Er darf trotz seiner glatten Trefferquote (jeder Abstand ist mit
    ausgelassenen Wochen erklaerbar) nicht gewinnen.
    """
    mixed = [
        date(2026, 1, 2),
        date(2026, 2, 1),
        date(2026, 3, 3),
        date(2026, 3, 10),  # eine einzelne Zahlung eine Woche spaeter
        date(2026, 4, 2),
        date(2026, 5, 2),
    ]
    assert det.infer_frequency(mixed, has_contract_key=True) == "monthly"


def test_a_weekly_series_survives_a_single_month_long_pause() -> None:
    """Regressionstest: die Weekly-Sperre war ein absolutes Veto.

    Eine Wochenreihe mit einer Ferienpause enthaelt genau einen Abstand von 28
    Tagen. Der zaehlt fuer "monatlich" als direkter Periodentreffer - womit die
    alte Sperre "woechentlich" ersatzlos strich, obwohl "monatlich" mit einer
    Trefferquote von 0,125 selbst nie ein Kandidat war. Uebrig blieb GAR KEINE
    Frequenz, und damit kein Kandidat: jeder reale Wochenvertrag fiel im
    24-Monats-Fenster aus der Erkennung.
    """
    weekly_with_pause = [
        date(2026, 1, 5),
        date(2026, 1, 12),
        date(2026, 1, 19),
        date(2026, 1, 26),
        date(2026, 2, 23),  # vier Wochen Pause
        date(2026, 3, 2),
        date(2026, 3, 9),
        date(2026, 3, 16),
        date(2026, 3, 23),
    ]
    assert det.infer_frequency(weekly_with_pause) == "weekly"


def test_random_dates_are_not_presented_as_a_contract() -> None:
    """Bestandsfall aus `test_recurring_detection.py`: 16, 78 und 113 Tage."""
    assert (
        det.infer_frequency(
            [date(2026, 1, 3), date(2026, 1, 19), date(2026, 4, 7), date(2026, 7, 29)]
        )
        is None
    )


def test_semiannual_is_a_rhythm_of_its_own() -> None:
    """Fehlte in der Altfassung ganz - eine halbjaehrliche Versicherung wurde
    als quartalsweise mit ausgelassener Periode gelesen."""
    assert det.infer_frequency(
        [date(2025, 3, 15), date(2025, 9, 13)], has_contract_key=True
    ) == "semiannual"
    assert det.FREQUENCY_SPECS["semiannual"].period_days == 182.62
    assert det.FREQUENCY_SPECS["semiannual"].tolerance_days == 14


def test_a_quarterly_pair_needs_a_key_or_a_direct_debit() -> None:
    """Zwei Buchungen im Quartalsabstand sehen aus wie zwei Arztrechnungen.

    Erst ein Vertragsschluessel oder eine ausgewiesene Lastschrift macht daraus
    einen Beleg.
    """
    pair = [date(2026, 1, 15), date(2026, 4, 16)]
    assert det.infer_frequency(pair) is None
    assert det.infer_frequency(pair, has_contract_key=True) == "quarterly"
    assert det.infer_frequency(pair, direct_debit=True) == "quarterly"
    # Drei Vorkommen brauchen keinen zusaetzlichen Beleg.
    assert det.infer_frequency([*pair, date(2026, 7, 15)]) == "quarterly"


@pytest.mark.parametrize(
    ("first", "second", "frequency"),
    [
        (date(2025, 10, 12), date(2026, 4, 13), "semiannual"),
        (date(2025, 4, 12), date(2026, 4, 13), "yearly"),
    ],
)
def test_a_long_period_pair_needs_the_same_proof_as_a_quarterly_one(
    first: date, second: date, frequency: str
) -> None:
    """Die Bedingung galt nur fuer 'quarterly' - bei einem halben oder ganzen
    Jahr Abstand ist die Beweislage aber noch duenner.

    Zwei identische Reisezahlungen im Jahresabstand erreichten ueber Datums- und
    Betragsstabilitaet einen Score von 0.60 und standen als SICHERER Vorschlag
    in der Inbox, waehrend dasselbe Paar im Quartalsabstand bewusst gar nicht
    angezeigt wurde.
    """
    pair = [first, second]
    assert det.infer_frequency(pair) is None
    assert det.infer_frequency(pair, has_contract_key=True) == frequency
    assert det.infer_frequency(pair, direct_debit=True) == frequency

    travel = [
        _tx(1, first, -48_000, payee="Reisebuero Sued"),
        _tx(2, second, -48_000, payee="Reisebuero Sued"),
    ]
    assert det.cluster_candidates(travel) == []


def test_yearly_survives_a_payday_that_moves_by_a_few_days() -> None:
    assert (
        det.infer_frequency([date(2024, 3, 15), date(2025, 3, 15), date(2026, 3, 15)])
        == "yearly"
    )
    assert (
        det.infer_frequency([date(2024, 3, 15), date(2025, 3, 28), date(2026, 3, 12)])
        == "yearly"
    )


def test_a_single_date_has_no_rhythm() -> None:
    assert det.infer_frequency([date(2026, 5, 1)]) is None
    # Dieselbe Buchung zweimal ist ebenfalls kein Abstand.
    assert det.infer_frequency([date(2026, 5, 1), date(2026, 5, 1)]) is None


def test_the_direct_debit_flag_is_read_from_the_bank_transaction_code() -> None:
    lastschrift = _monthly(
        15, [1, 2], -1000, import_format="mt940", bank_transaction_code=MT940_DIRECT_DEBIT
    )
    assert det.is_direct_debit(lastschrift)
    karte = _monthly(15, [1, 2], -1000, import_format="mt940", bank_transaction_code=MT940_CARD)
    assert not det.is_direct_debit(karte)
    assert not det.is_direct_debit(_monthly(15, [1, 2], -1000))


# ---------------------------------------------------------------------------
# 6.2 Preisstufen statt getrennter Cluster
# ---------------------------------------------------------------------------


def test_amount_levels_that_follow_each_other_are_one_contract() -> None:
    """Musterfunk 49,95 endet, bevor 39,95 beginnt - eine Preisaenderung."""
    rows = _monthly(3, [1, 2, 3, 4, 5], -4_995) + [
        _tx(index, date(2026, month, 3), -3_995)
        for index, month in enumerate([6, 7, 8], start=6)
    ]
    [single] = det.split_amount_steps(rows, "monthly")
    assert len(single) == 8

    steps = det.amount_steps_of(rows)
    assert [step.amount_minor for step in steps] == [-4_995, -3_995]
    assert steps[0].first_date == date(2026, 1, 3)
    assert steps[0].last_date == date(2026, 5, 3)
    assert steps[1].first_date == date(2026, 6, 3)


def test_amount_levels_that_overlap_are_two_parallel_contracts() -> None:
    """Musterfunk 39,95 und 99,90 laufen gleichzeitig - zwei Vertraege."""
    rows = _monthly(3, [1, 2, 3, 4, 5, 6], -3_995) + [
        _tx(index, date(2026, month, 17), -9_990)
        for index, month in enumerate([1, 2, 3, 4, 5, 6], start=7)
    ]
    groups = det.split_amount_steps(rows, "monthly")
    assert len(groups) == 2
    assert {group[0].amount_minor for group in groups} == {-3_995, -9_990}


def test_the_newest_level_carries_the_expected_amount() -> None:
    """Anker ist immer die juengste Buchung, nicht ein historisches Mittel."""
    rows = [
        _tx(1, date(2026, 5, 1), -9_000),
        _tx(2, date(2026, 6, 1), -9_050),
        _tx(3, date(2026, 7, 1), -9_100),
    ]
    # 5 % von 9100 sind 455 - alle drei Betraege liegen in einem Niveau.
    [level] = det.cluster_by_amount(rows)
    assert level[0].amount_minor == -9_100
    [candidate] = det.cluster_candidates(rows)
    assert candidate.amount_minor == 9_100
    assert candidate.amount_tolerance_minor == 100


def test_two_contracts_within_the_amount_tolerance_are_not_merged() -> None:
    """Regressionstest: zwei parallele Vertraege wurden zu einem verschmolzen.

    39,95 und 41,95 EUR liegen 2,00 EUR auseinander, die Betragstoleranz sind
    2,10 EUR - `cluster_by_amount` warf beide in EIN Niveau, und die
    Ueberlappungspruefung von `split_amount_steps` bekam sie deshalb nie zu
    sehen. Uebrig blieb EIN Kandidat ueber 41,95 EUR mit 24 Vorkommen und der
    Frequenz "woechentlich" (die verdoppelte Kadenz), waehrend der zweite
    Vertrag spurlos verschwand.
    """
    rows: list[det.DetectionTx] = []
    for index, month in enumerate(range(1, 13), start=1):
        rows.append(_tx(index, date(2026, month, 2), -3_995, payee="Musterfunk"))
        rows.append(_tx(index + 100, date(2026, month, 20), -4_195, payee="Musterfunk"))

    assert det.amount_tolerance_minor(4_195) == 210
    assert [len(level) for level in det.cluster_by_amount(rows)] == [24]

    candidates = det.cluster_candidates(rows)
    assert len(candidates) == 2
    assert {candidate.amount_minor for candidate in candidates} == {3_995, 4_195}
    assert {candidate.frequency for candidate in candidates} == {"monthly"}
    assert {candidate.occurrences_count for candidate in candidates} == {12}


def test_a_wandering_amount_stays_one_contract() -> None:
    """Die Gegenprobe zur Trennung paralleler Vertraege.

    Ein Abschlag, der zwischen zwei Betraegen hin und her springt, behaelt
    seinen Rhythmus auch ungetrennt - genau daran ist er von zwei nebeneinander
    laufenden Vertraegen zu unterscheiden.
    """
    alternating = [
        _tx(index, date(2026, month, 4), -10_000 if month % 2 else -10_200)
        for index, month in enumerate(range(1, 13), start=1)
    ]
    [level] = det.cluster_by_amount(alternating)
    assert det.split_parallel_amounts(level) == [level]
    [candidate] = det.cluster_candidates(alternating)
    assert candidate.frequency == "monthly"
    assert candidate.occurrences_count == 12


def test_a_price_change_within_the_tolerance_stays_a_single_contract() -> None:
    """Nacheinander statt nebeneinander: das ist eine Preisstufe."""
    rows = _monthly(6, [1, 2, 3, 4, 5, 6], -3_995) + [
        _tx(index, date(2026, month, 6), -4_195)
        for index, month in enumerate([7, 8, 9, 10, 11, 12], start=7)
    ]
    assert det.amount_tolerance_minor(4_195) >= 200
    [level] = det.cluster_by_amount(rows)
    assert det.split_parallel_amounts(level) == [level]
    [candidate] = det.cluster_candidates(rows)
    assert candidate.occurrences_count == 12
    # Innerhalb der Toleranz bleibt es EINE Stufe mit dem juengsten Betrag.
    assert [step.amount_minor for step in candidate.amount_steps] == [-4_195]


def test_a_gap_of_less_than_half_a_period_still_counts_as_overlapping() -> None:
    """Die halbe Periode ist die Grenze zwischen "danach" und "gleichzeitig"."""
    earlier = [_tx(1, date(2026, 1, 5), -5_000), _tx(2, date(2026, 2, 5), -5_000)]
    # 10 Tage spaeter: weniger als eine halbe Monatsperiode (15,2 Tage).
    overlapping = [_tx(3, date(2026, 2, 15), -9_000), _tx(4, date(2026, 3, 15), -9_000)]
    assert len(det.split_amount_steps([*earlier, *overlapping], "monthly")) == 2

    # 40 Tage spaeter: eindeutig danach.
    sequential = [_tx(5, date(2026, 3, 17), -9_000), _tx(6, date(2026, 4, 17), -9_000)]
    assert len(det.split_amount_steps([*earlier, *sequential], "monthly")) == 1


# ---------------------------------------------------------------------------
# 6.4 Vertrags-Score
# ---------------------------------------------------------------------------


def test_every_component_of_the_score_is_reported_separately() -> None:
    """Ohne die Bestandteile kann die Oberflaeche einen Vorschlag nicht
    begruenden - und ein unerklaerter Score ist in einem Finanzprodukt
    wertlos."""
    perfect = _monthly(
        15,
        [1, 2, 3, 4, 5, 6],
        -11_500,
        import_format="mt940",
        bank_transaction_code=MT940_DIRECT_DEBIT,
    )
    score = det.score_candidate(perfect, "monthly", affinity=1.0)
    assert score.date_stability == 1.0
    assert score.amount_stability == 1.0
    assert score.occurrence_count == 1.0
    assert score.category_affinity == 1.0
    assert score.payment_method == 1.0
    # 0.30 + 0.20 + 0.15 + 0.20 + 0.15
    assert score.total == 1.0


def test_the_five_weights_add_up_to_one_and_are_applied_as_specified() -> None:
    """Jede Gewichtung einzeln nachgerechnet: derselbe perfekte Fall, jeweils
    ein Bestandteil auf 0 gezogen."""
    base = _monthly(
        15,
        [1, 2, 3, 4, 5, 6],
        -11_500,
        import_format="mt940",
        bank_transaction_code=MT940_DIRECT_DEBIT,
    )
    # Kategorieaffinitaet 0 statt 1 -> 0.20 weniger.
    assert det.score_candidate(base, "monthly", affinity=0.0).total == 0.80
    # Zahlungsart 0 statt 1 (Kartenumsatz) -> 0.15 weniger.
    card = _monthly(
        15, [1, 2, 3, 4, 5, 6], -11_500, import_format="mt940", bank_transaction_code=MT940_CARD
    )
    assert det.score_candidate(card, "monthly", affinity=1.0).total == 0.85
    # Drei statt sechs Vorkommen -> Saettigung 0.5, also 0.075 weniger. Die
    # Summe wird auf zwei Nachkommastellen gerundet, weil
    # contracts.detection_confidence numeric(3,2) ist: aus 0.925 wird 0.92.
    three = _monthly(
        15, [1, 2, 3], -11_500, import_format="mt940", bank_transaction_code=MT940_DIRECT_DEBIT
    )
    assert det.score_candidate(three, "monthly", affinity=1.0).occurrence_count == 0.5
    assert det.score_candidate(three, "monthly", affinity=1.0).total == 0.92


def test_date_stability_falls_with_a_wandering_payday() -> None:
    """Bezugsgroesse sind sieben Tage Standardabweichung."""
    wandering = [
        _tx(1, date(2026, 1, 5), -4_500),
        _tx(2, date(2026, 2, 2), -4_500),
        _tx(3, date(2026, 3, 8), -4_500),
        _tx(4, date(2026, 4, 5), -4_500),
    ]
    # Zahltage 5/2/8/5, Median 5, Abweichungen 0/-3/+3/0 -> pstdev 2,1213.
    score = det.score_candidate(wandering, "monthly", affinity=0.5)
    assert score.date_stability == pytest.approx(1.0 - 2.1213 / 7.0, abs=0.001)


def test_the_payday_is_measured_cyclically_across_the_month_boundary() -> None:
    """Ohne Zyklus waeren der 31. und der 1. dreissig Tage auseinander, obwohl
    sie derselbe Zahltag in benachbarten Monaten sind."""
    around_month_end = [
        _tx(1, date(2026, 1, 31), -8_000),
        _tx(2, date(2026, 3, 1), -8_000),
        _tx(3, date(2026, 4, 1), -8_000),
        _tx(4, date(2026, 5, 1), -8_000),
    ]
    assert det.score_candidate(around_month_end, "monthly", affinity=0.5).date_stability > 0.85


def test_amount_stability_uses_the_coefficient_of_variation() -> None:
    """Der Faktor 4 macht 25 % Streuung zum Totalausfall dieses Bestandteils."""
    steady = _monthly(15, [1, 2, 3], -10_000)
    assert det.score_candidate(steady, "monthly", affinity=0.5).amount_stability == 1.0

    jumping = [
        _tx(1, date(2026, 1, 15), -5_000),
        _tx(2, date(2026, 2, 15), -10_000),
        _tx(3, date(2026, 3, 15), -15_000),
    ]
    # Mittel 10000, pstdev 4082,5 -> Variationskoeffizient 0,40825 * 4 > 1.
    assert det.score_candidate(jumping, "monthly", affinity=0.5).amount_stability == 0.0


def test_the_occurrence_component_saturates_at_six() -> None:
    for count, expected in ((2, 2 / 6), (3, 0.5), (6, 1.0), (9, 1.0)):
        rows = [
            _tx(index, date(2026, 1, 1) + timedelta(days=30 * index), -1_000)
            for index in range(count)
        ]
        score = det.score_candidate(rows, "monthly", affinity=0.5)
        assert score.occurrence_count == pytest.approx(expected, abs=0.001)


def test_category_affinity_falls_back_to_the_group_and_then_to_neutral() -> None:
    """`semantic_key` ist nur bei Kategorien aus dem Standardpaket gesetzt.

    Auf Alt-Haushalten (origin='legacy') muss die Schwelle allein aus Datum,
    Betrag, Vorkommen und Zahlungsart erreichbar bleiben.
    """
    assert det.category_affinity("rent", "housing") == 1.0
    assert det.category_affinity("doctor", "health") == 0.0
    assert det.category_affinity("streaming", "lifestyle") == 0.8
    # Kategorie unbekannt, Gruppe bekannt.
    assert det.category_affinity(None, "housing") == 0.8
    # Beides unbekannt.
    assert det.category_affinity(None, None) == 0.5
    assert det.category_affinity("eigene-kategorie", "eigene-gruppe") == 0.5


@pytest.mark.parametrize(
    ("import_format", "code", "expected"),
    [
        ("mt940", "N105", 1.0),
        ("mt940", "N083", 0.0),
        ("mt940", "N999", 0.5),
        ("camt", "PMNT/RCDT/DDFT", 1.0),
        ("camt", "PMNT/CCRD/POSD", 0.0),
        ("paypal_csv", "Abbuchung", 1.0),
        ("paypal_csv", "Kreditkarte", 0.0),
        # CSV und XLSX liefern gar nichts. Unbekannt ist ausdruecklich 0.5 und
        # nicht 0.0: ein Default von 0.0 wuerde jeden CSV-Import um 0,15 Punkte
        # bestrafen und korrekte Vertraege unter die Schwelle schieben.
        ("csv_generic", None, 0.5),
        (None, None, 0.5),
        ("csv_generic", "   ", 0.5),
    ],
)
def test_the_payment_method_index_reads_every_source_format(
    import_format: str | None, code: str | None, expected: float
) -> None:
    assert det.payment_method_value(import_format, code) == expected


def test_the_payment_method_of_a_candidate_is_the_mean_of_its_bookings() -> None:
    mixed = [
        _tx(
            1,
            date(2026, 1, 15),
            -1_000,
            import_format="mt940",
            bank_transaction_code=MT940_DIRECT_DEBIT,
        ),
        _tx(2, date(2026, 2, 15), -1_000, import_format="mt940", bank_transaction_code=MT940_CARD),
    ]
    assert det.payment_method_score(
        [(row.import_format, row.bank_transaction_code) for row in mixed]
    ) == 0.5
    assert det.payment_method_score([]) == 0.5


def test_the_helmsauer_case_stays_below_the_visibility_threshold() -> None:
    """Referenzfall aus Abschnitt 14: zwei Arztzahlungen im Quartalsabstand.

    Der Score bleibt unter 0.40 und der Fall erscheint gar nicht - nicht
    einmal als unsicherer Vorschlag. Zwei Bestandteile tragen das: der Betrag
    schwankt um ein Drittel, und eine Kartenzahlung belegt keine wiederkehrende
    Vereinbarung.
    """
    doctor = UUID("33333333-3333-4333-8333-000000000099")
    rows = [
        _tx(
            1,
            date(2026, 2, 10),
            -8_500,
            payee="Mustermakler & Kollegen",
            import_format="mt940",
            bank_transaction_code=MT940_CARD,
            category_id=doctor,
        ),
        _tx(
            2,
            date(2026, 5, 13),  # 92 Tage spaeter
            -12_400,
            payee="Mustermakler & Kollegen",
            import_format="mt940",
            bank_transaction_code=MT940_CARD,
            category_id=doctor,
        ),
    ]
    score = det.score_candidate(rows, "quarterly", affinity=0.0)
    assert score.total < det.UNCERTAIN_MIN_SCORE
    assert det.confidence_for(score) is None
    assert det.cluster_candidates(rows, affinity_by_category={doctor: 0.0}) == []


def test_the_thresholds_split_suggested_uncertain_and_invisible() -> None:
    assert (det.SUGGEST_MIN_SCORE, det.UNCERTAIN_MIN_SCORE) == (0.55, 0.40)

    def _score(total: float) -> det.ScoreBreakdown:
        return det.ScoreBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, total)

    assert det.confidence_for(_score(0.55)) == "suggested"
    assert det.confidence_for(_score(0.54)) == "uncertain"
    assert det.confidence_for(_score(0.40)) == "uncertain"
    assert det.confidence_for(_score(0.39)) is None


# ---------------------------------------------------------------------------
# Kandidatenbildung (weiterhin ohne Datenbank)
# ---------------------------------------------------------------------------


def test_two_contracts_of_one_provider_are_separated_by_the_mandate_reference() -> None:
    """Der eigentliche Fix fuer "Musterfunk steht dreimal in der Liste".

    Getrennt wird VOR der Betragsanalyse - zwei Vertraege desselben Anbieters
    unterscheiden sich in der Vertragsnummer, nicht im Betrag.
    """
    rows: list[det.DetectionTx] = []
    for index, month in enumerate(range(1, 7), start=1):
        rows.append(
            _tx(
                index,
                date(2026, month, 3),
                -3_995,
                payee="Musterfunk Deutschland GmbH",
                purpose="MUSTERFUNK VERTRAGSNR 4711234 FESTNETZ",
            )
        )
        rows.append(
            _tx(
                index + 100,
                date(2026, month, 17),
                -9_990,
                payee="Musterfunk Deutschland GmbH",
                purpose="MUSTERFUNK VERTRAGSNR 8899001 MOBILFUNK",
            )
        )

    candidates = det.cluster_candidates(rows)
    assert len(candidates) == 2
    assert {item.contract_key for item in candidates} == {"4711234", "8899001"}
    assert {item.contract_key_source for item in candidates} == {"mandate"}
    assert {item.amount_minor for item in candidates} == {3_995, 9_990}
    assert {item.frequency for item in candidates} == {"monthly"}


def test_a_format_switch_does_not_split_one_contract_into_two() -> None:
    """Regressionstest: gemischte Importformate zerlegten EINEN Vertrag.

    MT940 legt den Verwendungszweck als vollstaendiges Feld :86: samt
    Mandatsreferenz ab, CAMT liefert nur RmtInf/Ustrd und damit keine. Dieselbe
    Musterfunk stand dadurch in der ersten Jahreshaelfte mit und in der zweiten
    ohne Schluessel da - und erschien zweimal in der Inbox. Genau der Fehler,
    den das Modul beheben soll.
    """
    rows = [
        _tx(
            index,
            date(2026, month, 3),
            -3_995,
            payee="Musterfunk Deutschland GmbH",
            purpose=_MT940_FRAME.format("MANDATSREF. M4711 MUSTERFUNK"),
            import_format="mt940",
        )
        for index, month in enumerate(range(1, 7), start=1)
    ] + [
        _tx(
            index,
            date(2026, month, 3),
            -3_995,
            payee="Musterfunk Deutschland GmbH",
            purpose="MUSTERFUNK MOBILFUNK RECHNUNG",
            import_format="camt",
        )
        for index, month in enumerate(range(7, 13), start=7)
    ]

    assert [(bucket.contract_key, len(bucket.rows)) for bucket in det._collect_buckets(rows)] == [
        ("M4711", 12)
    ]
    [candidate] = det.cluster_candidates(rows)
    assert candidate.occurrences_count == 12
    assert candidate.contract_key == "M4711"
    assert candidate.first_date == date(2026, 1, 3)
    assert candidate.last_date == date(2026, 12, 3)


def test_two_mandate_references_keep_the_unkeyed_bookings_separate() -> None:
    """Die Gegenprobe: ab zwei Referenzen ist nicht mehr entscheidbar, zu
    welchem Vertrag die schluessellosen Buchungen gehoeren."""
    rows: list[det.DetectionTx] = []
    for index, month in enumerate(range(1, 7), start=1):
        rows.append(
            _tx(
                index,
                date(2026, month, 3),
                -3_995,
                payee="Musterfunk Deutschland GmbH",
                purpose="MUSTERFUNK MANDATSREF. M4711 FESTNETZ",
            )
        )
        rows.append(
            _tx(
                index + 100,
                date(2026, month, 17),
                -9_990,
                payee="Musterfunk Deutschland GmbH",
                purpose="MUSTERFUNK MANDATSREF. M8899 MOBILFUNK",
            )
        )
    rows += [
        _tx(
            index + 200,
            date(2026, month, 24),
            -2_500,
            payee="Musterfunk Deutschland GmbH",
            purpose="MUSTERFUNK ZUSATZ",
        )
        for index, month in enumerate(range(1, 7), start=1)
    ]

    buckets = {bucket.contract_key: len(bucket.rows) for bucket in det._collect_buckets(rows)}
    assert buckets == {"M4711": 6, "M8899": 6, None: 6}


def test_a_price_ladder_stays_one_candidate_with_both_steps() -> None:
    rows = [
        _tx(
            index,
            date(2026, month, 3),
            -4_995,
            payee="Musterfunk Deutschland GmbH",
            purpose="MUSTERFUNK VERTRAGSNR 4711234",
        )
        for index, month in enumerate([1, 2, 3, 4, 5], start=1)
    ] + [
        _tx(
            index,
            date(2026, month, 3),
            -3_995,
            payee="Musterfunk Deutschland GmbH",
            purpose="MUSTERFUNK VERTRAGSNR 4711234",
        )
        for index, month in enumerate([6, 7, 8, 9], start=6)
    ]

    [candidate] = det.cluster_candidates(rows)
    assert candidate.amount_minor == 3_995
    assert candidate.occurrences_count == 9
    assert [step.amount_minor for step in candidate.amount_steps] == [-4_995, -3_995]
    # Die juengste Stufe traegt den Sollbetrag.
    assert candidate.current_step.amount_minor == -3_995


def test_a_collector_account_without_a_key_produces_nothing() -> None:
    """Zahlungsdienstleister sind keine Vertragspartner (Abschnitt 6.5)."""
    rows = [
        _tx(index, date(2026, month, 8), amount, payee="Klarna Bank AB", purpose="KLARNA")
        for index, (month, amount) in enumerate(
            [(3, -2_199), (4, -2_199), (5, -2_199)], start=1
        )
    ]
    assert det.cluster_candidates(rows) == []


def test_a_collector_account_with_a_key_names_the_paid_contract() -> None:
    """Liegt ein Schluessel vor, wird er Teil des Anzeigenamens - er ist der
    einzige Hinweis darauf, wessen Vertrag hier abgebucht wurde."""
    rows = [
        _tx(
            index,
            date(2026, month, 9),
            -1_499,
            payee="PayPal Europe",
            purpose="PAYPAL MREF+SPOT998877 SPOTIFY",
        )
        for index, month in enumerate([1, 2, 3, 4], start=1)
    ]
    [candidate] = det.cluster_candidates(rows)
    assert candidate.contract_key == "SPOT998877"
    assert candidate.display_name == "PayPal Europe · SPOT998877"


def test_an_installment_purchase_carries_its_plan_and_type() -> None:
    rows = [
        _tx(
            index,
            date(2026, month, 8),
            -4_999,
            payee="Klarna Bank AB",
            purpose=f"KLARNA MREF+KL-9988776 RATE {index}/12 SOFA",
        )
        for index, month in enumerate([3, 4, 5], start=1)
    ]
    [candidate] = det.cluster_candidates(rows)
    assert candidate.contract_type == "installment"
    assert (candidate.installments_paid, candidate.installments_total) == (3, 12)


def test_the_expense_sign_is_turned_into_a_direction() -> None:
    """Vertragskonvention: `amount_minor` immer positiv, Richtung getrennt."""
    [expense] = det.cluster_candidates(_monthly(12, [1, 2, 3, 4], -8_990, payee="Stadtwerke"))
    assert expense.direction == "expense"
    assert expense.amount_minor == 8_990


def test_income_needs_three_occurrences_and_a_stable_payday() -> None:
    """Der Vorzeichenfilter der Altfassung machte Einnahmen unsichtbar.

    Ohne Ersatzbedingung wuerde jetzt jede regelmaessige Erstattung zu einem
    Vorschlag.
    """
    salary = _monthly(28, [1, 2, 3, 4, 5, 6], 350_000, payee="Musterfirma AG")
    [candidate] = det.cluster_candidates(salary)
    assert candidate.direction == "income"
    assert candidate.contract_type == "income"
    assert candidate.amount_minor == 350_000

    # Derselbe Zahltag-Verlauf: als Ausgabe ein Vorschlag, als Einnahme nicht.
    wandering_dates = [date(2026, 1, 5), date(2026, 2, 2), date(2026, 3, 8), date(2026, 4, 5)]
    outgoing = [
        _tx(index, day, -4_500, payee="Wobbel GmbH")
        for index, day in enumerate(wandering_dates, start=1)
    ]
    incoming = [
        _tx(index, day, 4_500, payee="Wobbel GmbH")
        for index, day in enumerate(wandering_dates, start=1)
    ]
    [expense] = det.cluster_candidates(outgoing)
    assert expense.score.date_stability < 0.70
    assert det.cluster_candidates(incoming) == []


def test_an_ambiguous_key_is_dropped_but_a_spelling_variant_keeps_it() -> None:
    """PayPal fuehrt EIN Mandat fuer das ganze Konto.

    Seine Referenz steht unter Netflix genauso wie unter Zalando - sie benennt
    das Zahlungskonto und keinen Vertrag. Folgenreich ist das, weil die
    Pruefung "schon bestaetigt" bei Schluesselgleichheit sofort abbricht.
    """
    shared: list[det.DetectionTx] = []
    index = 1
    for payee, amount in (("Netflix International", -1_499), ("Zalando SE", -3_999)):
        for month in range(1, 7):
            shared.append(
                _tx(
                    index,
                    date(2026, month, 9),
                    amount,
                    payee=payee,
                    purpose="PAYPAL MREF+5MUSTER00TEST1",
                )
            )
            index += 1
    assert {item.contract_key for item in det.cluster_candidates(shared)} == {None}

    # Gegenfall: dieselbe Referenz unter Schreibweisen DESSELBEN Empfaengers.
    # Dort ist der gemeinsame Schluessel das Einzige, was sie verbindet.
    variants = ["Allianz Lebensvers", "Allianz Lebensversic", "Allianz Lebensversicherung"]
    spelled: list[det.DetectionTx] = []
    for index, month in enumerate(range(1, 7), start=200):
        spelled.append(
            _tx(
                index,
                date(2026, month, 11),
                -8_900,
                payee=variants[month % 3],
                purpose="MREF+ALV-4711223",
            )
        )
    assert {item.contract_key for item in det.cluster_candidates(spelled)} == {"ALV-4711223"}


def test_a_provider_key_is_dropped_when_it_yields_two_contracts() -> None:
    """Schlaegt dasselbe Buendel MEHRERE Vertraege vor, benennt der Schluessel
    den Anbieter und nicht den Vertrag.

    Ihn trotzdem an beide zu haengen, wuerde beim Bestaetigen des einen
    Musterfunk-Vertrags den zweiten aus der Inbox nehmen.
    """
    rows: list[det.DetectionTx] = []
    for index, month in enumerate(range(1, 7), start=1):
        rows.append(
            _tx(
                index,
                date(2026, month, 3),
                -3_995,
                payee="Musterfunk Deutschland GmbH",
                purpose="MUSTERFUNK CRED+DE98ZZZ09999999999",
            )
        )
        rows.append(
            _tx(
                index + 100,
                date(2026, month, 17),
                -9_990,
                payee="Musterfunk Deutschland GmbH",
                purpose="MUSTERFUNK CRED+DE98ZZZ09999999999",
            )
        )

    candidates = det.cluster_candidates(rows)
    assert len(candidates) == 2
    assert {item.contract_key for item in candidates} == {None}
    assert {item.display_name for item in candidates} == {"Musterfunk Deutschland GmbH"}


def test_the_category_of_the_split_beats_the_last_ai_decision() -> None:
    """Eine vom Nutzer bestaetigte Zuordnung ist der staerkere Beleg."""
    confirmed = UUID("33333333-3333-4333-8333-000000000001")
    guessed = UUID("33333333-3333-4333-8333-000000000002")
    rows = [
        det.DetectionTx(
            transaction_id=UUID(f"22222222-2222-4222-8222-{index:012d}"),
            account_id=_ACCOUNT,
            payee_id=None,
            payee_label="Stadtwerke",
            payee_key=payee_key("Stadtwerke"),
            amount_minor=-8_900,
            booked_date=date(2026, month, 12),
            purpose_raw=None,
            purpose_key="",
            bank_transaction_code=None,
            import_format=None,
            category_id=confirmed,
            ai_category_id=guessed,
        )
        for index, month in enumerate([1, 2, 3, 4], start=1)
    ]
    [candidate] = det.cluster_candidates(rows, affinity_by_category={confirmed: 1.0, guessed: 0.0})
    assert candidate.suggested_category_id == confirmed
    assert candidate.score.category_affinity == 1.0


def test_the_contract_type_comes_from_the_category() -> None:
    energy = UUID("33333333-3333-4333-8333-000000000010")
    rows = _monthly(12, [1, 2, 3, 4], -8_900, payee="Stadtwerke", category_id=energy)
    [candidate] = det.cluster_candidates(
        rows,
        affinity_by_category={energy: 1.0},
        contract_type_by_category={energy: "utility"},
    )
    assert candidate.contract_type == "utility"


# ---------------------------------------------------------------------------
# Projektion auf das Altformat
# ---------------------------------------------------------------------------


def test_the_legacy_projection_flips_the_sign_back_to_the_booking_convention() -> None:
    [candidate] = det.cluster_candidates(_monthly(12, [1, 2, 3, 4], -8_990, payee="Stadtwerke"))
    legacy = candidate.as_legacy_item()
    assert legacy is not None
    assert legacy.expected_amount_minor == -8_990
    assert legacy.suggested_frequency == "monthly"
    assert legacy.payee_label == "Stadtwerke"


def test_the_legacy_projection_refuses_semiannual_and_uncertain_candidates() -> None:
    """`suggested_frequency` ist im Altmodell ein Literal aus vier Werten, und
    fuer "unsicher" hat der alte Client keine Kennzeichnung - er wuerde solche
    Kandidaten faelschlich als gleichwertig anzeigen."""
    semiannual = [
        _tx(index, day, -9_900, payee="Musterkasse", purpose="VERTRAGSNR 5566778")
        for index, day in enumerate(
            [date(2025, 3, 10), date(2025, 9, 8), date(2026, 3, 9)], start=1
        )
    ]
    [candidate] = det.cluster_candidates(semiannual)
    assert candidate.frequency == "semiannual"
    assert candidate.as_legacy_item() is None
    assert candidate.as_legacy_detail() is None
