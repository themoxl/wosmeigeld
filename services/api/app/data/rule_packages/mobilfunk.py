PACKAGE = {
    "id": "mobilfunk",
    "name": "Telekommunikation",
    "description": "Telekom, Vodafone, O2, etc.",
    "icon": "📱",
    "rule_count": 1,
    "rules": [
        {
            "name": "Telekommunikation",
            "match_payee_raw": (
                "(?i)\\b(TELEKOM|T-MOBILE|TMOBILE|VODAFONE|O2|TELEFONICA|1UND1|1&1|1U1|CONGSTAR|"
                "ALDI.?TALK)\\b"
            ),
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "telecom",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
    ]
}
