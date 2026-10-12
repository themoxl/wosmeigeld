PACKAGE = {
    "id": "baumarkt",
    "name": "Baumarkt & Garten",
    "description": "OBI, Hornbach, Bauhaus etc.",
    "icon": "🛠️",
    "rule_count": 1,
    "rules": [
        {
            "name": "Baumarkt & Garten",
            "match_payee_raw": "(?i)\\b(OBI|BAUHAUS|HORNBACH|TOOM|HAGEBAU|GLOBUS.*BAUMARKT)\\b",
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "furnishing",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
    ]
}
