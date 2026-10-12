PACKAGE = {
    "id": "versicherungen",
    "name": "Versicherungen",
    "description": "Allianz, HUK, ADAC etc.",
    "icon": "🛡️",
    "rule_count": 1,
    "rules": [
        {
            "name": "Versicherungen",
            "match_payee_raw": (
                "(?i)\\b(ALLIANZ|HUK.?COBURG|HUK24|ADAC.*VERSICHER|AXA|DEBEKA|ERGO.*VERSICHER|"
                "GENERALI|ZURICH.*VERSICHER)\\b"
            ),
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "property_insurance",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
    ]
}
