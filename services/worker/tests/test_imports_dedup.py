from datetime import date

from worker.imports.canonical import CanonicalTransaction
from worker.imports.dedup import ExistingTransaction, classify_row, compute_fingerprint

ACCOUNT_A = "11111111-1111-1111-1111-111111111111"
ACCOUNT_B = "22222222-2222-2222-2222-222222222222"


def _tx(**overrides: object) -> CanonicalTransaction:
    defaults: dict[str, object] = {
        "booked_date": date(2026, 1, 5),
        "amount_minor": -4250,
        "currency": "EUR",
        "payee_raw": "Supermarkt Muster GmbH",
        "purpose": "Einkauf",
    }
    defaults.update(overrides)
    return CanonicalTransaction(**defaults)  # type: ignore[arg-type]


def test_fingerprint_is_deterministic_and_order_sensitive() -> None:
    fp1 = compute_fingerprint(ACCOUNT_A, date(2026, 1, 5), -4250, "Muster GmbH", "Einkauf")
    fp2 = compute_fingerprint(ACCOUNT_A, date(2026, 1, 5), -4250, "Muster GmbH", "Einkauf")
    fp3 = compute_fingerprint(ACCOUNT_A, date(2026, 1, 5), -4250, "Anderer Name", "Einkauf")
    assert fp1 == fp2
    assert fp1 != fp3


def test_exact_duplicate_via_external_reference() -> None:
    ctx = _tx()
    existing = [
        ExistingTransaction(
            id="tx-1",
            account_id=ACCOUNT_A,
            booked_date=date(2026, 1, 5),
            amount_minor=-4250,
            external_reference="SYNTH0001",
            fingerprint="irrelevant",
            is_transfer=False,
        )
    ]
    ctx.external_reference = "SYNTH0001"
    result = classify_row(ctx, "some-fp", ACCOUNT_A, existing, [])
    assert result.status == "duplicate_exact"
    assert result.duplicate_of_transaction_id == "tx-1"


def test_exact_duplicate_via_fingerprint() -> None:
    ctx = _tx()
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    existing = [
        ExistingTransaction(
            id="tx-2",
            account_id=ACCOUNT_A,
            booked_date=ctx.booked_date,
            amount_minor=ctx.amount_minor,
            external_reference=None,
            fingerprint=fingerprint,
            is_transfer=False,
        )
    ]
    result = classify_row(ctx, fingerprint, ACCOUNT_A, existing, [])
    assert result.status == "duplicate_exact"
    assert result.duplicate_of_transaction_id == "tx-2"


def _existing_same_day(**overrides: object) -> ExistingTransaction:
    defaults: dict[str, object] = {
        "id": "tx-3",
        "account_id": ACCOUNT_A,
        "booked_date": date(2026, 1, 5),
        "amount_minor": -4250,
        "external_reference": None,
        "fingerprint": "anderer-fingerabdruck-weil-anderer-verwendungszweck",
        "is_transfer": False,
        "payee": "Supermarkt Muster GmbH",
    }
    defaults.update(overrides)
    return ExistingTransaction(**defaults)  # type: ignore[arg-type]


def _classify(ctx: CanonicalTransaction, existing: list[ExistingTransaction]) -> object:
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    return classify_row(ctx, fingerprint, ACCOUNT_A, existing, [])


def test_possible_duplicate_same_day_same_payee() -> None:
    """Derselbe Tag, derselbe Betrag, derselbe Empfaenger - nur der
    Verwendungszweck weicht ab, weshalb Stufe 2 nicht greift."""
    ctx = _tx(purpose="Einkauf Filiale 12")
    result = _classify(ctx, [_existing_same_day()])
    assert result.status == "duplicate_possible"
    assert result.duplicate_of_transaction_id == "tx-3"


def test_possible_duplicate_accepts_truncated_payee() -> None:
    """MT940 kuerzt Namensfelder ab - der gemeinsame Anfang muss genuegen."""
    ctx = _tx(payee_raw="Supermarkt Muster", purpose="Einkauf Filiale 12")
    result = _classify(ctx, [_existing_same_day()])
    assert result.status == "duplicate_possible"


def test_no_possible_duplicate_when_payee_differs() -> None:
    """Der Fall, der die Liste frueher geflutet hat: gleicher Betrag, gleicher
    Tag, voellig anderer Empfaenger."""
    ctx = _tx(payee_raw="Jonas Muster", purpose="Uebertrag")
    result = _classify(ctx, [_existing_same_day(payee="Martin Muster")])
    assert result.status == "new"


def test_no_possible_duplicate_when_day_differs() -> None:
    """Ein Tag Abstand genuegt nicht mehr - das Fenster ist auf null Tage."""
    ctx = _tx(booked_date=date(2026, 1, 6), purpose="Einkauf Filiale 12")
    result = _classify(ctx, [_existing_same_day(booked_date=date(2026, 1, 5))])
    assert result.status == "new"


def test_possible_duplicate_when_both_payees_missing() -> None:
    """Ohne Empfaenger auf beiden Seiten entscheiden Betrag und Tag allein -
    das Feld kann dann nichts unterscheiden."""
    ctx = _tx(payee_raw=None, purpose="Einkauf Filiale 12")
    result = _classify(ctx, [_existing_same_day(payee=None)])
    assert result.status == "duplicate_possible"


def test_no_possible_duplicate_when_only_one_payee_missing() -> None:
    ctx = _tx(payee_raw=None, purpose="Einkauf Filiale 12")
    result = _classify(ctx, [_existing_same_day(payee="Supermarkt Muster GmbH")])
    assert result.status == "new"


def test_transfer_candidate_across_accounts() -> None:
    ctx = _tx(amount_minor=-10000, booked_date=date(2026, 1, 10))
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    other_account_existing = [
        ExistingTransaction(
            id="tx-4",
            account_id=ACCOUNT_B,
            booked_date=date(2026, 1, 10),
            amount_minor=10000,
            external_reference=None,
            fingerprint="unrelated",
            is_transfer=False,
        )
    ]
    result = classify_row(ctx, fingerprint, ACCOUNT_A, [], other_account_existing)
    assert result.status == "transfer_candidate"
    assert result.duplicate_of_transaction_id == "tx-4"


def test_transfer_candidate_ignored_when_already_linked() -> None:
    ctx = _tx(amount_minor=-10000, booked_date=date(2026, 1, 10))
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    other_account_existing = [
        ExistingTransaction(
            id="tx-5",
            account_id=ACCOUNT_B,
            booked_date=date(2026, 1, 10),
            amount_minor=10000,
            external_reference=None,
            fingerprint="unrelated",
            is_transfer=True,
        )
    ]
    result = classify_row(ctx, fingerprint, ACCOUNT_A, [], other_account_existing)
    assert result.status == "new"


def test_new_when_nothing_matches() -> None:
    ctx = _tx()
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    result = classify_row(ctx, fingerprint, ACCOUNT_A, [], [])
    assert result.status == "new"
    assert result.duplicate_of_transaction_id is None


# ---------------------------------------------------------------------------
# Stufe 3: gleicher Zweck schlaegt abweichende Empfaengerschreibweise
#
# Der Fall aus der Praxis: derselbe Umsatz kam zweimal herein, Datum, Betrag und
# Verwendungszweck identisch - und wurde als NEU uebernommen, weil der
# Empfaenger einmal fehlte. Der Fingerabdruck (Stufe 2) enthaelt den Empfaenger
# und bricht deshalb; Stufe 4 wertet "leer gegen gefuellt" ausdruecklich als
# Unterschied. Der Verwendungszweck wurde bis dahin ueberhaupt nie verglichen.
# ---------------------------------------------------------------------------


def _bestehend(**overrides: object) -> ExistingTransaction:
    defaults: dict[str, object] = {
        "id": "tx-alt",
        "account_id": ACCOUNT_A,
        "booked_date": date(2026, 1, 5),
        "amount_minor": -4250,
        "external_reference": None,
        "fingerprint": "passt-nicht",
        "is_transfer": False,
        "payee": "Supermarkt Muster GmbH",
        "purpose": "Einkauf",
    }
    defaults.update(overrides)
    return ExistingTransaction(**defaults)  # type: ignore[arg-type]


def test_same_purpose_is_flagged_even_without_a_payee() -> None:
    """Der gemeldete Fall: Empfaenger fehlt in der neuen Zeile."""
    ctx = _tx(payee_raw=None)
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    ergebnis = classify_row(ctx, fingerprint, ACCOUNT_A, [_bestehend()], [])
    assert ergebnis.status == "duplicate_possible"
    assert ergebnis.duplicate_of_transaction_id == "tx-alt"


def test_same_purpose_is_flagged_when_the_payee_is_written_differently() -> None:
    """Dieselbe Gegenseite, voellig andere Schreibweise - MT940 gegen CSV."""
    ctx = _tx(payee_raw="SUPERMARKT MUSTER")
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    ergebnis = classify_row(
        ctx, fingerprint, ACCOUNT_A, [_bestehend(payee="Voellig Anderer Name AG")], []
    )
    assert ergebnis.status == "duplicate_possible"


def test_an_empty_purpose_never_flags_anything() -> None:
    """Ohne Verwendungszweck traegt das Feld nichts bei. Wuerde es trotzdem
    verglichen, passte jede Buchung ohne Zweck auf jede andere mit gleichem
    Betrag und Datum - genau die Flut von Fehlalarmen, die Stufe 4 einmal
    losgeworden ist."""
    ctx = _tx(payee_raw="Voellig Anderer Empfaenger", purpose=None)
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    ergebnis = classify_row(
        ctx, fingerprint, ACCOUNT_A, [_bestehend(payee="Noch ein Name", purpose=None)], []
    )
    assert ergebnis.status == "new"


def test_a_different_purpose_on_the_same_day_stays_new() -> None:
    """Zwei echte Buchungen ueber denselben Betrag am selben Tag an
    verschiedene Empfaenger - der Zweck unterscheidet sie."""
    ctx = _tx(payee_raw="Jonas Muster", purpose="Taschengeld Jonas")
    fingerprint = compute_fingerprint(
        ACCOUNT_A, ctx.booked_date, ctx.amount_minor, ctx.payee_raw, ctx.purpose
    )
    ergebnis = classify_row(
        ctx,
        fingerprint,
        ACCOUNT_A,
        [_bestehend(payee="Martin Muster", purpose="Taschengeld Martin")],
        [],
    )
    assert ergebnis.status == "new"
