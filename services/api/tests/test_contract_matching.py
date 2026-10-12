"""Bewertungslogik des Vertragsabgleichs - reine Funktionen, ohne Datenbank.

Der Motor in `app/services/contract_matching_service.py` ist bewusst zweigeteilt:
die Formel rechnet auf fertigen Datensaetzen, nur der Schreibpfad kennt SQL.
Diese Datei nutzt genau diese Trennung - sie laeuft ohne Supabase und ohne HTTP.
Dadurch sind hier Faelle pruefbar, die als Datenbanktest zu teuer waeren:
Gleichstaende, Grenzwerte der Toleranzen und die Vorzeichenkonvention.

Die erwarteten Zahlen sind von Hand nachgerechnet und nicht aus der
Implementierung abgeleitet - ein Test, der dieselbe Formel noch einmal
aufschreibt, wuerde eine falsche Gewichtung mitschleifen statt sie zu melden.
"""

import uuid
from datetime import date
from typing import Any
from uuid import UUID

import pytest

from app.services import contract_matching_service as cms


def _uuid(suffix: int) -> UUID:
    """Aufsteigend sortierbare IDs - ihre Reihenfolge ist Teil der Zusicherung."""
    return uuid.UUID(f"22222222-2222-4222-8222-{suffix:012d}")


_HOUSEHOLD = uuid.UUID("11111111-1111-4111-8111-111111111111")
_CONTRACT_A = _uuid(101)
_CONTRACT_B = _uuid(102)
_PAYEE = _uuid(201)
_OTHER_PAYEE = _uuid(202)

# Bezugsfall aller Rechnungen: MaxEnergy, 147,00 EUR, monatlich am 15.
_DUE = date(2026, 6, 15)


def _spec(**overrides: Any) -> cms.ContractSpec:
    base: dict[str, Any] = {
        "contract_id": _CONTRACT_A,
        "household_id": _HOUSEHOLD,
        "account_id": None,
        "direction": "expense",
        "amount_minor": 14_700,
        "frequency": "monthly",
        "due_rule": "day_of_month",
        "due_day": 15,
        "next_due_date": _DUE,
        "tolerance_days": 4,
        "tolerance_amount_minor": 735,
        "status": "active",
        "contract_key": None,
        "payee_id": None,
        "identity_label_key": cms.payee_key("MaxEnergy GmbH"),
        "ends_on": None,
        "cancelled_on": None,
        "installments_total": None,
        "installments_paid": 0,
        "missed_periods": 0,
        "version": 1,
    }
    base.update(overrides)
    return cms.ContractSpec(**base)


def _tx(
    suffix: int,
    booked_date: date,
    amount_minor: int,
    *,
    payee_id: UUID | None = None,
    payee_label: str = "MaxEnergy GmbH",
    purpose: str = "",
) -> cms.CandidateTx:
    return cms.CandidateTx(
        transaction_id=_uuid(suffix),
        booked_date=booked_date,
        amount_minor=amount_minor,
        payee_id=payee_id,
        payee_key=cms.payee_key(payee_label),
        purpose_normalized=cms.normalize_purpose(purpose),
    )


def _scored(spec: cms.ContractSpec, tx: cms.CandidateTx) -> cms.ScoredCandidate:
    scored = cms.score_candidate(spec, tx, spec.next_due_date)
    assert scored is not None
    return scored


# ---------------------------------------------------------------------------
# Normalisierung
# ---------------------------------------------------------------------------


def test_payee_key_ignores_case_and_punctuation() -> None:
    assert cms.payee_key("MaxEnergy GmbH & Co. KG") == cms.payee_key("maxenergy gmbh co kg")
    assert cms.payee_key(None) == ""
    assert cms.payee_key("   ") == ""


def test_normalize_purpose_makes_a_mandate_reference_comparable() -> None:
    """Der Mandatsschluessel steht im Vertrag mit Bindestrichen, im
    Verwendungszweck der Bank oft anders geschrieben - ohne beidseitige
    Normalisierung faende sich derselbe Vertrag nie wieder."""
    key = cms.normalize_purpose("M-4711/AB")
    purpose = cms.normalize_purpose("SEPA-LASTSCHRIFT M-4711/AB MAXENERGY")
    assert key == "m 4711 ab"
    assert key in purpose


def test_amount_tolerance_has_a_floor_and_a_cap() -> None:
    assert cms.amount_tolerance_minor(500) == 100  # 5 % waeren 25 -> Untergrenze
    assert cms.amount_tolerance_minor(14_700) == 735
    assert cms.amount_tolerance_minor(100_000) == 1_000  # 5 % waeren 5000 -> Deckel


# ---------------------------------------------------------------------------
# Identitaet
# ---------------------------------------------------------------------------


def test_identity_levels_are_ranked_from_proof_to_hint() -> None:
    with_key = _spec(contract_key="M-4711/AB", payee_id=_PAYEE)
    assert (
        cms.identity_score(
            with_key,
            _tx(1, _DUE, -14_700, payee_id=_PAYEE, purpose="LASTSCHRIFT M-4711/AB"),
        )
        == 1.00
    )
    # Ohne Mandatsreferenz im Zweck faellt derselbe Vertrag auf den Empfaenger
    # zurueck: der Schluessel darf nicht blind gelten.
    assert cms.identity_score(with_key, _tx(2, _DUE, -14_700, payee_id=_PAYEE)) == 0.80

    by_label = _spec(payee_id=_OTHER_PAYEE)
    assert cms.identity_score(by_label, _tx(3, _DUE, -14_700, payee_id=_PAYEE)) == 0.60

    stranger = _tx(4, _DUE, -14_700, payee_id=_PAYEE, payee_label="Baumarkt Sued")
    assert cms.identity_score(_spec(payee_id=_OTHER_PAYEE), stranger) == 0.0


def test_a_short_contract_key_alone_is_no_proof_of_identity() -> None:
    """Regressionstest: der Schluessel galt als reine Teilstringpruefung.

    Die Erkennung laesst schon vier Zeichen als Mandatsschluessel zu
    ("Kundennr. 1234"). Danach genuegte eine fremde Buchung mit einer zufaellig
    passenden Rechnungsnummer, um Identitaet 1.00 zu erreichen - und ueber
    `classify_hit` -> 'price_change' den Sollbetrag des Vertrags zu
    ueberschreiben.
    """
    with_short_key = _spec(contract_key="1234", payee_id=_PAYEE)

    # (a) Der Schluessel steckt nur IN einer fremden Zahl.
    inside_number = _tx(
        1, _DUE, -7_000, payee_label="Elektro Mueller GmbH", purpose="RECHNUNG 947110 KAUF"
    )
    assert cms.identity_score(with_short_key, inside_number) == 0.0

    # (b) Eigenstaendiges Token, aber fremder Empfaenger: bleibt zu wenig.
    stranger = _tx(
        2, _DUE, -7_000, payee_label="Elektro Mueller GmbH", purpose="RECHNUNG 1234 VOM 05.09."
    )
    assert cms.identity_score(with_short_key, stranger) == 0.0
    assert cms.score_candidate(with_short_key, stranger, _DUE) is None

    # (c) Beim eigenen Empfaenger belegt derselbe kurze Schluessel weiterhin.
    own = _tx(3, _DUE, -7_000, payee_id=_PAYEE, purpose="BEITRAG 1234")
    assert cms.identity_score(with_short_key, own) == 1.00

    # (d) Ein langer Schluessel traegt sich selbst - so kommt eine ueber ein
    #     Sammelkonto abgebuchte Zahlung ueberhaupt an ihren Vertrag.
    long_key = _spec(contract_key="M-4711/AB", payee_id=_PAYEE)
    via_collector = _tx(4, _DUE, -14_700, payee_label="PayPal Europe", purpose="M-4711/AB ABO")
    assert cms.identity_score(long_key, via_collector) == 1.00


def test_a_stranger_is_not_a_candidate_at_all() -> None:
    """Identitaet 0 heisst: die Buchung liegt nur zufaellig im Fenster."""
    assert (
        cms.score_candidate(_spec(), _tx(5, _DUE, -14_700, payee_label="Baumarkt Sued"), _DUE)
        is None
    )


# ---------------------------------------------------------------------------
# Bewertung
# ---------------------------------------------------------------------------


def test_perfect_hit_scores_the_maximum_for_its_identity_level() -> None:
    scored = _scored(_spec(payee_id=_PAYEE), _tx(6, _DUE, -14_700, payee_id=_PAYEE))
    assert scored.identity == 0.80
    assert scored.amount_closeness == 1.0
    assert scored.date_closeness == 1.0
    # 0.5 * 0.80 + 0.3 * 1.0 + 0.2 * 1.0
    assert scored.score == pytest.approx(0.90)
    assert scored.amount_delta_minor == 0
    assert scored.days_delta == 0


def test_date_closeness_falls_linearly_across_the_tolerance_window() -> None:
    scored = _scored(_spec(payee_id=_PAYEE), _tx(7, date(2026, 6, 17), -14_700, payee_id=_PAYEE))
    assert scored.days_delta == 2
    assert scored.date_closeness == 0.5  # 1 - 2/4
    assert scored.score == pytest.approx(0.80)  # 0.4 + 0.3 + 0.1


def test_amount_closeness_uses_half_the_expected_amount_as_reference() -> None:
    # Bezugsgroesse = max(14700 * 0.5, 500) = 7350; die Abweichung ist die Haelfte.
    scored = _scored(_spec(payee_id=_PAYEE), _tx(8, _DUE, -11_025, payee_id=_PAYEE))
    assert scored.amount_delta_minor == -3_675
    assert scored.amount_closeness == 0.5
    assert scored.score == pytest.approx(0.75)  # 0.4 + 0.15 + 0.2


def test_tiny_contracts_keep_a_usable_amount_reference() -> None:
    """Ohne Untergrenze von 5,00 EUR waere bei einem 2-EUR-Vertrag schon ein
    Cent Abweichung maximal weit entfernt."""
    small = _spec(amount_minor=200, tolerance_amount_minor=100, payee_id=_PAYEE)
    scored = _scored(small, _tx(9, _DUE, -201, payee_id=_PAYEE))
    assert scored.amount_closeness == pytest.approx(1.0 - 1 / 500)


def test_the_expense_sign_is_stripped_before_comparing() -> None:
    """`contracts.amount_minor` ist immer positiv, `transactions.amount_minor`
    traegt das Vorzeichen - verglichen wird der Betrag."""
    scored = _scored(_spec(payee_id=_PAYEE), _tx(10, _DUE, -14_700, payee_id=_PAYEE))
    assert scored.amount_delta_minor == 0


def test_an_income_contract_compares_the_same_way() -> None:
    scored = _scored(_spec(direction="income"), _tx(11, _DUE, 14_700))
    assert scored.amount_delta_minor == 0


def test_window_spans_the_tolerance_in_both_directions() -> None:
    assert cms.window_for(_spec()) == (date(2026, 6, 11), date(2026, 6, 19))
    assert cms.window_for(_spec(tolerance_days=0)) == (_DUE, _DUE)


# ---------------------------------------------------------------------------
# Treffer, Preisaenderung, Ausreisser
# ---------------------------------------------------------------------------


def test_within_the_amount_tolerance_is_a_plain_hit() -> None:
    spec = _spec(payee_id=_PAYEE)
    assert (
        cms.classify_hit(spec, _scored(spec, _tx(12, _DUE, -14_000, payee_id=_PAYEE))) == "matched"
    )


def test_the_amount_tolerance_never_falls_below_one_euro() -> None:
    """`tolerance_amount_minor` ist beim Anlegen 0 - ohne Untergrenze scheiterte
    jeder Vertrag an einem Cent Rundungsdifferenz."""
    spec = _spec(tolerance_amount_minor=0, payee_id=_PAYEE)
    assert (
        cms.classify_hit(spec, _scored(spec, _tx(13, _DUE, -14_800, payee_id=_PAYEE))) == "matched"
    )
    assert (
        cms.classify_hit(spec, _scored(spec, _tx(14, _DUE, -14_801, payee_id=_PAYEE)))
        == "price_change"
    )


def test_maxenergy_is_a_price_change_and_not_a_new_contract() -> None:
    spec = _spec(payee_id=_PAYEE)
    scored = _scored(spec, _tx(15, _DUE, -12_300, payee_id=_PAYEE))
    assert scored.amount_delta_minor == -2_400
    assert cms.classify_hit(spec, scored) == "price_change"


def test_a_price_change_needs_more_than_a_matching_name() -> None:
    """Nur der Empfaengername (0.60) reicht nicht: sonst schriebe eine
    gleichnamige Fremdbuchung den Sollbetrag um."""
    spec = _spec(payee_id=_OTHER_PAYEE)
    scored = _scored(spec, _tx(16, _DUE, -12_300, payee_id=_PAYEE))
    assert scored.identity == 0.60
    assert cms.classify_hit(spec, scored) is None


def test_a_large_one_off_payment_never_overwrites_the_expected_amount() -> None:
    """Der 60-%-Deckel: die Jahresabrechnung an denselben Versorger darf den
    Monatsbetrag nicht zerstoeren."""
    spec = _spec(payee_id=_PAYEE)
    # 14700 + 8820 = 23520, Abweichung genau 60 % des Sollbetrags.
    just_inside = _scored(spec, _tx(17, _DUE, -23_520, payee_id=_PAYEE))
    just_outside = _scored(spec, _tx(18, _DUE, -23_521, payee_id=_PAYEE))
    assert cms.classify_hit(spec, just_inside) == "price_change"
    assert cms.classify_hit(spec, just_outside) is None


# ---------------------------------------------------------------------------
# Auswahl und Gleichstand
# ---------------------------------------------------------------------------


def test_pick_best_match_prefers_the_plausible_amount_over_the_higher_score() -> None:
    """Der Ausreisser hat die bessere Identitaet und damit den hoeheren Score -
    trotzdem gewinnt die betraglich passende Buchung, weil ein Ausreisser
    ueberhaupt kein Treffer ist."""
    spec = _spec(contract_key="M-4711/AB")
    outlier = _tx(19, _DUE, -30_000, purpose="LASTSCHRIFT M-4711/AB")  # Identitaet 1.00
    plausible = _tx(20, date(2026, 6, 18), -14_700)  # Identitaet 0.60
    candidates = [outlier, plausible]

    by_formula = cms.pick_best_candidate(spec, candidates, _DUE)
    assert by_formula is not None
    assert by_formula.candidate.transaction_id == outlier.transaction_id
    assert by_formula.score == pytest.approx(0.70)

    real = cms.pick_best_match(spec, candidates, _DUE)
    assert real is not None
    assert real.candidate.transaction_id == plausible.transaction_id
    assert real.score == pytest.approx(0.65)


def test_pick_best_is_an_alias_of_pick_best_candidate() -> None:
    """Der Plan nennt die Funktion an einer Stelle so; der Alias haelt beide
    Namen gueltig, damit spaetere Aufrufer nicht ins Leere greifen."""
    assert cms.pick_best is cms.pick_best_candidate


def test_a_tie_is_broken_deterministically_by_transaction_id() -> None:
    """Zwei Buchungen, symmetrisch um die Faelligkeit: gleicher Score, gleiche
    Betrags- und Datumsabweichung. Ohne festen letzten Schluessel haengt das
    Ergebnis an der Zeilenreihenfolge von Postgres - und zwei Laeufe lieferten
    verschiedene Zuordnungen."""
    spec = _spec()
    early = _tx(21, date(2026, 6, 13), -14_700)
    late = _tx(22, date(2026, 6, 17), -14_700)

    forward = cms.pick_best_match(spec, [early, late], _DUE)
    backward = cms.pick_best_match(spec, [late, early], _DUE)
    assert forward is not None and backward is not None
    assert forward.score == backward.score
    assert forward.candidate.transaction_id == early.transaction_id
    assert backward.candidate.transaction_id == early.transaction_id


def test_the_closer_amount_outranks_the_smaller_id_at_equal_score() -> None:
    """Beide Abweichungen liegen jenseits der Bezugsgroesse, die Betragsnaehe
    ist deshalb fuer beide 0 und der Score identisch. Entschieden wird dann
    nach der kleineren Betragsabweichung - erst danach nach der ID."""
    spec = _spec(tolerance_amount_minor=0, payee_id=_PAYEE)
    # Bewusst die KLEINERE ID fuer den schlechteren Betrag: entschiede die ID
    # zuerst, gewaenne diese Buchung.
    far = _tx(23, _DUE, -5_880, payee_id=_PAYEE)  # Abweichung 8820
    near = _tx(24, _DUE, -7_350, payee_id=_PAYEE)  # Abweichung 7350

    scored_far = _scored(spec, far)
    scored_near = _scored(spec, near)
    assert scored_far.amount_closeness == scored_near.amount_closeness == 0.0
    assert scored_far.score == scored_near.score

    picked = cms.pick_best_match(spec, [far, near], _DUE)
    assert picked is not None
    assert picked.candidate.transaction_id == near.transaction_id


def test_conflicting_claims_are_resolved_by_score() -> None:
    spec_a = _spec(contract_id=_CONTRACT_A)
    spec_b = _spec(contract_id=_CONTRACT_B, next_due_date=date(2026, 6, 12))
    shared = _tx(25, _DUE, -14_700)

    winners, losers = cms.resolve_conflicts(
        {_CONTRACT_A: _scored(spec_a, shared), _CONTRACT_B: _scored(spec_b, shared)}
    )
    assert list(winners) == [_CONTRACT_A]
    assert losers == frozenset({_CONTRACT_B})


def test_uncontested_claims_all_win() -> None:
    spec = _spec()
    winners, losers = cms.resolve_conflicts(
        {
            _CONTRACT_A: _scored(spec, _tx(26, _DUE, -14_700)),
            _CONTRACT_B: _scored(spec, _tx(27, _DUE, -14_700)),
        }
    )
    assert set(winners) == {_CONTRACT_A, _CONTRACT_B}
    assert losers == frozenset()


# ---------------------------------------------------------------------------
# Auswertung einer Periode
# ---------------------------------------------------------------------------


def test_a_hit_advances_exactly_one_period_and_clears_the_missed_counter() -> None:
    outcome = cms.match_contract(_spec(), [_tx(28, _DUE, -14_700)], date(2026, 6, 20))
    assert outcome.kind == "matched"
    assert outcome.occurrence_status == "matched"
    assert outcome.next_due_date == date(2026, 7, 15)
    assert outcome.missed_periods == 0
    assert outcome.events == ()


def test_a_hit_after_a_gap_reports_the_resumed_payment() -> None:
    outcome = cms.match_contract(
        _spec(missed_periods=2), [_tx(29, _DUE, -14_700)], date(2026, 6, 20)
    )
    assert outcome.missed_periods == 0
    assert [event.kind for event in outcome.events] == ["payment_resumed"]
    assert outcome.events[0].payload["missed_periods"] == 2


def test_a_price_change_carries_the_new_amount_and_a_fresh_tolerance() -> None:
    outcome = cms.match_contract(
        _spec(payee_id=_PAYEE), [_tx(30, _DUE, -12_300, payee_id=_PAYEE)], date(2026, 6, 20)
    )
    assert outcome.kind == "price_change"
    # Der Treffer bleibt ein Treffer: die Faelligkeit ist erfuellt.
    assert outcome.occurrence_status == "matched"
    assert outcome.new_amount_minor == 12_300
    assert outcome.new_tolerance_amount_minor == 615
    assert outcome.next_due_date == date(2026, 7, 15)
    [event] = outcome.events
    assert event.kind == "price_change"
    assert event.payload == {
        "old_minor": 14_700,
        "new_minor": 12_300,
        "delta_minor": -2_400,
        "pct": -16.33,
    }


def test_nothing_happens_while_the_tolerance_is_still_running() -> None:
    """Am Tag nach der Faelligkeit ist die Zahlung nicht ausgeblieben, sondern
    schlicht noch nicht gebucht."""
    outcome = cms.match_contract(_spec(), [], date(2026, 6, 19))
    assert outcome.kind == "pending"
    assert outcome.occurrence_status is None
    assert outcome.next_due_date == _DUE
    assert outcome.missed_periods == 0
    assert outcome.events == ()


def test_no_missing_payment_is_claimed_beyond_the_imported_data_horizon() -> None:
    """Kalenderzeit ersetzt keinen noch nicht hochgeladenen Kontoauszug."""
    outcome = cms.match_contract(
        _spec(next_due_date=date(2026, 8, 1), due_day=1),
        [],
        date(2026, 8, 7),
        data_through=date(2026, 7, 31),
    )
    assert outcome.kind == "pending"
    assert outcome.occurrence_status is None
    assert outcome.next_due_date == date(2026, 8, 1)
    assert outcome.missed_periods == 0


def test_a_missed_period_still_advances_the_due_date() -> None:
    """Bliebe `next_due_date` stehen, liefe der Motor fuer immer gegen dieselbe
    Periode."""
    outcome = cms.match_contract(_spec(), [], date(2026, 6, 20))
    assert outcome.kind == "missing"
    assert outcome.occurrence_status == "missing"
    assert outcome.next_due_date == date(2026, 7, 15)
    assert outcome.missed_periods == 1
    assert [event.kind for event in outcome.events] == ["payment_missing"]


def test_payment_missing_is_reported_only_on_the_first_gap() -> None:
    outcome = cms.match_contract(_spec(missed_periods=1), [], date(2026, 6, 20))
    assert outcome.missed_periods == 2
    assert outcome.events == ()


def test_the_automation_ends_a_contract_after_the_configured_gaps() -> None:
    outcome = cms.match_contract(
        _spec(missed_periods=1), [], date(2026, 6, 20), auto_end_after_missed="2"
    )
    assert outcome.new_status == "ended"
    [event] = outcome.events
    assert event.kind == "ended"
    assert event.payload == {"reason": "auto_missed", "missed_periods": 2}


def test_the_automation_can_be_switched_off_entirely() -> None:
    outcome = cms.match_contract(
        _spec(missed_periods=9), [], date(2026, 6, 20), auto_end_after_missed="off"
    )
    assert outcome.missed_periods == 10
    assert outcome.new_status is None
    assert outcome.events == ()


def test_a_manual_end_date_beats_the_automation() -> None:
    """Wer ein Ende oder eine Kuendigung eingetragen hat, bekommt keinen
    automatisch beendeten Vertrag untergeschoben."""
    with_end = cms.match_contract(
        _spec(missed_periods=1, ends_on=date(2026, 12, 31)),
        [],
        date(2026, 6, 20),
        auto_end_after_missed="2",
    )
    assert with_end.kind == "missing"
    assert with_end.new_status is None

    with_cancellation = cms.match_contract(
        _spec(missed_periods=1, cancelled_on=date(2026, 5, 1)),
        [],
        date(2026, 6, 20),
        auto_end_after_missed="2",
    )
    assert with_cancellation.new_status is None


def test_a_legacy_contract_the_engine_never_matched_is_not_ended_automatically() -> None:
    """Uebernommene Altvertraege erreichen ohne Schluessel nie die
    Identitaetsschwelle. Ihre "ausgebliebenen" Zahlungen beschreiben die
    Zuordnung, nicht den Vertrag - beenden darf die Automatik sie deshalb nicht.
    """
    never_had_a_chance = cms.match_contract(
        _spec(missed_periods=3, ever_matched=False, legacy_import=True),
        [],
        date(2026, 6, 20),
        auto_end_after_missed="2",
    )
    assert never_had_a_chance.kind == "missing"
    assert never_had_a_chance.missed_periods == 4
    assert never_had_a_chance.new_status is None
    assert all(event.kind != "ended" for event in never_had_a_chance.events)


@pytest.mark.parametrize(
    ("ever_matched", "legacy_import"),
    [
        # Einmal getroffen: der Fehlzaehler ist belastbar, auch im Altbestand.
        (True, True),
        # Von Hand angelegt und nie bezahlt: hier ist das Ende die richtige
        # Antwort - die Schonung gilt ausdruecklich nur dem Altbestand.
        (False, False),
    ],
)
def test_the_automation_still_ends_everything_else(
    ever_matched: bool, legacy_import: bool
) -> None:
    outcome = cms.match_contract(
        _spec(missed_periods=3, ever_matched=ever_matched, legacy_import=legacy_import),
        [],
        date(2026, 6, 20),
        auto_end_after_missed="2",
    )
    assert outcome.new_status == "ended"


def test_an_expired_term_ends_the_contract_without_writing_a_period() -> None:
    """Gegenstueck zur Ausnahme oben: sonst haengt ein Vertrag mit Enddatum fuer
    immer, weil die Automatik ihn ausdruecklich auslaesst."""
    outcome = cms.match_contract(_spec(ends_on=date(2026, 5, 31)), [], date(2026, 6, 20))
    assert outcome.kind == "term_reached"
    assert outcome.occurrence_status is None
    assert outcome.new_status == "ended"
    assert outcome.next_due_date == _DUE
    [event] = outcome.events
    assert event.kind == "ended"
    assert event.payload["reason"] == "term_reached"


def test_a_due_date_inside_the_term_is_matched_before_the_term_ends_it() -> None:
    outcome = cms.match_contract(
        _spec(ends_on=date(2026, 6, 30)), [_tx(31, _DUE, -14_700)], date(2026, 7, 5)
    )
    assert outcome.kind == "matched"


def test_the_last_installment_ends_the_contract() -> None:
    outcome = cms.match_contract(
        _spec(installments_total=12, installments_paid=11),
        [_tx(32, _DUE, -14_700)],
        date(2026, 6, 20),
    )
    assert outcome.installments_paid == 12
    assert outcome.new_status == "ended"
    assert [event.kind for event in outcome.events] == ["installment_completed"]


# ---------------------------------------------------------------------------
# Zaehler
# ---------------------------------------------------------------------------


def test_counters_do_not_overlap() -> None:
    """`matched + price_change + missing` muss die Zahl der geschriebenen
    Faelligkeiten ergeben - sonst kann der Rescan-Endpunkt keine Summe melden."""
    counters = cms._Counters()
    for outcome in (
        cms.match_contract(_spec(), [_tx(33, _DUE, -14_700)], date(2026, 6, 20)),
        cms.match_contract(
            _spec(payee_id=_PAYEE), [_tx(34, _DUE, -12_300, payee_id=_PAYEE)], date(2026, 6, 20)
        ),
        cms.match_contract(_spec(), [], date(2026, 6, 20)),
        cms.match_contract(_spec(), [], date(2026, 6, 16)),  # pending, zaehlt nirgends
    ):
        counters.add(outcome)

    assert (counters.matched, counters.price_change, counters.missing) == (1, 1, 1)
    assert counters.matched + counters.price_change + counters.missing == counters.periods
