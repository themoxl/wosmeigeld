"""Bestandsfaelle der alten Erkennung, auf die Erkennung v2 uebersetzt.

Diese Datei prueft weiterhin genau die vier Zusicherungen, die sie seit Phase 4
festhaelt - nur gegen die neuen Namen: `rules_service._cluster_recurring_transactions`
ist durch `contract_detection_service.cluster_candidates` abgeloest,
`_RecurringTransaction` durch `DetectionTx` und `routes.review._next_due_date`
durch `contract_service.next_due_date` (das im Gegensatz zum Vorgaenger auch
'semiannual' kennt).

Eine Zusicherung hat sich dabei bewusst geaendert und ist deshalb doppelt
festgehalten: der Bestandsfall benutzte "PayPal Europe" als Empfaenger. PayPal
ist ein Zahlungsdienstleister und kein Vertragspartner (Plan, Abschnitt 6.5) -
ohne Vertragsschluessel entsteht dort kein Vorschlag mehr. Die eigentliche
Aussage des Falls ("zwei Betragsniveaus desselben Empfaengers sind zwei
Vertraege") gilt unveraendert und wird an einem benannten Vertragspartner
geprueft.

Die breite Abdeckung der Erkennung steht in `test_contract_detection.py`.
"""

from datetime import date
from uuid import UUID

from app.services import contract_service
from app.services.contract_detection_service import DetectionTx, cluster_candidates
from app.services.contract_matching_service import payee_key

ACCOUNT_ID = UUID("11111111-1111-4111-8111-111111111111")


def _transaction(
    suffix: int,
    booked_date: date,
    amount_minor: int,
    payee: str = "Nordlicht Media GmbH",
) -> DetectionTx:
    return DetectionTx(
        transaction_id=UUID(f"22222222-2222-4222-8222-{suffix:012d}"),
        account_id=ACCOUNT_ID,
        payee_id=None,
        payee_label=payee,
        payee_key=payee_key(payee),
        amount_minor=amount_minor,
        booked_date=booked_date,
        purpose_raw=None,
        purpose_key="",
        bank_transaction_code=None,
        import_format=None,
        category_id=None,
    )


def test_same_payee_with_different_amounts_creates_separate_candidates() -> None:
    """Zwei Betragsniveaus mit ueberlappenden Zeitraeumen sind zwei Vertraege.

    Nur zeitlich AUFEINANDERFOLGENDE Niveaus sind Preisstufen desselben
    Vertrags; hier laufen beide Reihen parallel.
    """
    rows = [
        _transaction(1, date(2026, 5, 2), -2_500),
        _transaction(2, date(2026, 6, 2), -2_500),
        _transaction(3, date(2026, 7, 2), -2_500),
        _transaction(4, date(2026, 5, 14), -10_300),
        _transaction(5, date(2026, 6, 14), -10_300),
        _transaction(6, date(2026, 7, 14), -10_300),
    ]

    candidates = cluster_candidates(rows)

    assert len(candidates) == 2
    # Vertragskonvention: `amount_minor` ist positiv, die Richtung steht
    # getrennt daneben. Das alte `expected_amount_minor` war negativ.
    assert {item.amount_minor for item in candidates} == {2_500, 10_300}
    assert {item.direction for item in candidates} == {"expense"}
    assert {item.occurrences_count for item in candidates} == {3}
    assert all(item.amount_tolerance_minor == 0 for item in candidates)
    assert all(item.frequency == "monthly" for item in candidates)


def test_a_payment_service_provider_without_a_key_is_no_longer_a_candidate() -> None:
    """Bewusste Verhaltensaenderung gegenueber dem Bestandsfall.

    Dieselben Buchungen wie oben, nur unter "PayPal Europe": der Empfaenger
    sagt dann nichts darueber aus, WESSEN Vertrag bezahlt wurde. Frueher
    entstanden hier zwei Vorschlaege, die der Nutzer nicht zuordnen konnte.
    """
    rows = [
        _transaction(1, date(2026, 5, 2), -2_500, "PayPal Europe"),
        _transaction(2, date(2026, 6, 2), -2_500, "PayPal Europe"),
        _transaction(3, date(2026, 7, 2), -2_500, "PayPal Europe"),
    ]

    assert cluster_candidates(rows) == []


def test_latest_real_amount_wins_over_historic_average() -> None:
    """Anker ist die juengste Buchung, nicht ein Mittelwert.

    Ein Durchschnitt waere ein Betrag, den der Nutzer nie bezahlt hat.
    """
    rows = [
        _transaction(1, date(2026, 5, 1), -9_000),
        _transaction(2, date(2026, 6, 1), -9_050),
        _transaction(3, date(2026, 7, 1), -9_100),
    ]

    [candidate] = cluster_candidates(rows)

    assert candidate.amount_minor == 9_100
    assert candidate.amount_tolerance_minor == 100
    assert candidate.source_transaction_id == rows[-1].transaction_id
    assert candidate.frequency == "monthly"
    # 5 % von 91,00 EUR sind 4,55 EUR - alle drei Betraege liegen in einem
    # Niveau und ergeben deshalb keine Preistreppe.
    assert len(candidate.amount_steps) == 1


def test_random_dates_are_not_presented_as_fixkosten() -> None:
    """Abstaende von 16, 78 und 113 Tagen sind kein Rhythmus."""
    rows = [
        _transaction(1, date(2026, 1, 3), -5_000),
        _transaction(2, date(2026, 1, 19), -5_000),
        _transaction(3, date(2026, 4, 7), -5_000),
        _transaction(4, date(2026, 7, 29), -5_000),
    ]

    assert cluster_candidates(rows) == []


def test_month_end_next_due_date_stays_on_calendar_month_end() -> None:
    """Der Zieltag kommt aus der Regel, nicht aus dem Vormonat.

    Sonst wandert eine Faelligkeit am 31. ueber den Februar dauerhaft auf den
    28. `routes.review._next_due_date` ist ersatzlos entfallen; die Rechnung
    steht jetzt an einer Stelle, die auch 'semiannual' kennt.
    """
    assert (
        contract_service.next_due_date(date(2026, 1, 31), "monthly", due_rule="month_end")
        == date(2026, 2, 28)
    )
    assert (
        contract_service.next_due_date(date(2024, 2, 29), "yearly", due_rule="month_end")
        == date(2025, 2, 28)
    )
    # Der Zieltag ueberlebt den kurzen Monat: Februar kappt, Maerz nicht.
    assert (
        contract_service.next_due_date(date(2026, 2, 28), "monthly", due_rule="month_end")
        == date(2026, 3, 31)
    )
    assert (
        contract_service.next_due_date(date(2026, 3, 15), "semiannual", due_day=15)
        == date(2026, 9, 15)
    )
