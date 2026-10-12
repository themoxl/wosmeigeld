import uuid

from app.services.rules_service import evaluate_rule


class DummyRule:
    def __init__(self, **kwargs):
        self.match_account_id = kwargs.get("match_account_id")
        self.match_min_amount_minor = kwargs.get("match_min_amount_minor")
        self.match_max_amount_minor = kwargs.get("match_max_amount_minor")
        self.match_amount_mode = kwargs.get("match_amount_mode", "inside")
        self.match_payee_raw = kwargs.get("match_payee_raw")
        self.match_payee_exact_word = kwargs.get("match_payee_exact_word", False)
        self.match_purpose = kwargs.get("match_purpose")
        self.match_purpose_exact_word = kwargs.get("match_purpose_exact_word", False)
        self.exclude_payee_raw = kwargs.get("exclude_payee_raw")
        self.exclude_purpose = kwargs.get("exclude_purpose")


def test_evaluate_rule_exact_word_matching():
    # Rule matching 'Otto' as exact word
    rule = DummyRule(
        match_payee_raw="Otto",
        match_payee_exact_word=True,
    )
    dummy_acc = uuid.uuid4()

    # Matches 'Otto GmbH' and 'Otto'
    assert (
        evaluate_rule(
            rule,
            payee_label="Otto GmbH",
            purpose="Einkauf",
            amount_minor=-5000,
            account_id=dummy_acc,
        )
        is not None
    )
    assert (
        evaluate_rule(
            rule,
            payee_label="Otto",
            purpose="Einkauf",
            amount_minor=-5000,
            account_id=dummy_acc,
        )
        is not None
    )

    # Does NOT match 'Lotto' or 'Motto'
    assert (
        evaluate_rule(
            rule,
            payee_label="Lotto Bayern",
            purpose="Einkauf",
            amount_minor=-5000,
            account_id=dummy_acc,
        )
        is None
    )
    assert (
        evaluate_rule(
            rule,
            payee_label="Motto Party",
            purpose="Einkauf",
            amount_minor=-5000,
            account_id=dummy_acc,
        )
        is None
    )


def test_evaluate_rule_negative_exclusion():
    # Rule matching 'Amazon' but excluding 'Digital'
    rule = DummyRule(
        match_payee_raw="Amazon",
        exclude_payee_raw="Digital",
    )
    dummy_acc = uuid.uuid4()

    # Matches 'Amazon EU S.a.r.l.' and 'Amazon Markt'
    assert (
        evaluate_rule(
            rule,
            payee_label="Amazon EU S.a.r.l.",
            purpose="Bestellung",
            amount_minor=-2500,
            account_id=dummy_acc,
        )
        is not None
    )

    # Does NOT match 'Amazon Digital'
    assert (
        evaluate_rule(
            rule,
            payee_label="Amazon Digital Downloads",
            purpose="Ebook",
            amount_minor=-999,
            account_id=dummy_acc,
        )
        is None
    )


def test_evaluate_rule_outside_amount_range():
    # Rule matching 'musterberatung' but amount must NOT be between
    # 6.000 € (-600000 minor) and 8.000 € (-800000 minor)
    # Note: Minor units for expense -6000.00 € to -8000.00 €
    rule = DummyRule(
        match_purpose="musterberatung",
        match_min_amount_minor=-800000,
        match_max_amount_minor=-600000,
        match_amount_mode="outside",
    )
    dummy_acc = uuid.uuid4()

    # Amount -5000.00 € (500 €) is OUTSIDE range [-800000, -600000] -> Should MATCH
    assert (
        evaluate_rule(
            rule,
            payee_label="musterberatung",
            purpose="musterberatung Honorar",
            amount_minor=-500000,
            account_id=dummy_acc,
        )
        is not None
    )

    # Amount -9000.00 € (9000 €) is OUTSIDE range [-800000, -600000] -> Should MATCH
    assert (
        evaluate_rule(
            rule,
            payee_label="musterberatung",
            purpose="musterberatung Reisekosten",
            amount_minor=-900000,
            account_id=dummy_acc,
        )
        is not None
    )

    # Amount -7000.00 € (7000 €) is INSIDE excluded range [-800000, -600000] -> Should NOT match
    assert (
        evaluate_rule(
            rule,
            payee_label="musterberatung",
            purpose="musterberatung Projekt",
            amount_minor=-700000,
            account_id=dummy_acc,
        )
        is None
    )


def test_evaluate_rule_regex_patterns():
    # Regex rule matching ^(Amazon|AMZN)
    rule = DummyRule(match_payee_raw="^(Amazon|AMZN)")
    dummy_acc = uuid.uuid4()

    assert (
        evaluate_rule(
            rule,
            payee_label="Amazon Market",
            purpose="Einkauf",
            amount_minor=-1000,
            account_id=dummy_acc,
        )
        is not None
    )
    assert (
        evaluate_rule(
            rule,
            payee_label="AMZN EU",
            purpose="Einkauf",
            amount_minor=-1000,
            account_id=dummy_acc,
        )
        is not None
    )
    assert (
        evaluate_rule(
            rule,
            payee_label="Ebay Market",
            purpose="Einkauf",
            amount_minor=-1000,
            account_id=dummy_acc,
        )
        is None
    )

    # Test \bWord\b boundary
    rule_word = DummyRule(match_payee_raw=r"\bOtto\b")
    assert (
        evaluate_rule(
            rule_word,
            payee_label="Otto GmbH",
            purpose="Einkauf",
            amount_minor=-1000,
            account_id=dummy_acc,
        )
        is not None
    )
    assert (
        evaluate_rule(
            rule_word,
            payee_label="Lotto",
            purpose="Einkauf",
            amount_minor=-1000,
            account_id=dummy_acc,
        )
        is None
    )

