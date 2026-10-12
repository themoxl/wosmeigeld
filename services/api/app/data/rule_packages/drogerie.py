PACKAGE = {
    "id": "drogerie",
    "name": "Drogerie & Pflege",
    "description": "Rossmann, DM, Müller",
    "icon": "🧴",
    "rule_count": 1,
    "rules": [
        {
            "name": "Drogerie & Pflege",
            "match_payee_raw": (
                "(?i)\\b(ROSSMANN|DM-DROGERIE|DM DROGERIE|DM.*MARKT|MUELLER DROG|MÜLLER DROG)\\b"
            ),
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "household_supplies",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
    ]
}
