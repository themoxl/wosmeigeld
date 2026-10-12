PACKAGE = {
    "id": "tankstellen",
    "name": "Tankstellen & Mobilität",
    "description": "Tanken und Öffentliche Verkehrsmittel",
    "icon": "⛽",
    "rule_count": 2,
    "rules": [
        {
            "name": "Tankstellen & Mobilität",
            "match_payee_raw": (
                "(?i)\\b(SHELL|ARAL|TOTALENERGIES|TOTAL|JET TANKSTELLE|JET.*TANK|ESSO)\\b"
            ),
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "fuel_charging",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
        {
            "name": "Tankstellen & Mobilität",
            "match_payee_raw": (
                "(?i)\\b(DEUTSCHE BAHN|DB.*VERTRIEB|DB.*FERNVERKEHR|FLIXBUS|FLIXTRAIN)\\b"
            ),
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "public_transport",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
    ]
}
