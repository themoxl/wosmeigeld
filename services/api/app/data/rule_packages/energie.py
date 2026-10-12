PACKAGE = {
    "id": "energie",
    "name": "Energie & Versorger",
    "description": "Strom, Gas, Wasser",
    "icon": "⚡",
    "rule_count": 1,
    "rules": [
        {
            "name": "Energie & Versorger",
            "match_payee_raw": (
                "(?i)\\b(RWE|E\\.ON|EON|VATTENFALL|ENBW|STADTWERKE|EWE|MAINOVA|N-ERGIE|NERGIE)\\b"
            ),
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "energy",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
    ]
}
