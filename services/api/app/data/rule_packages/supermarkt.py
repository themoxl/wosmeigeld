PACKAGE = {
    "id": "supermarkt",
    "name": "Supermarkt & Lebensmittel",
    "description": "Aldi, Lidl, Edeka, Rewe und weitere Supermärkte",
    "icon": "🛒",
    "rule_count": 1,
    "rules": [
        {
            "name": "Supermarkt & Lebensmittel",
            "match_payee_raw": (
                "(?i)\\b(ALDI|LIDL|EDEKA|REWE|KAUFLAND|NETTO|PENNY|NORMA|E-CENTER|E.?CENTER)\\b"
            ),
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "groceries",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
    ]
}
