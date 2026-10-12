PACKAGE = {
    "id": "streaming",
    "name": "Streaming & Digital",
    "description": "Netflix, Spotify, Amazon Prime etc.",
    "icon": "📺",
    "rule_count": 1,
    "rules": [
        {
            "name": "Streaming & Digital",
            "match_payee_raw": (
                "(?i)\\b(NETFLIX|SPOTIFY|DISNEY.?PLUS|DISNEY\\+|YOUTUBE.*PREMIUM|APPLE\\.COM/BILL|"
                "ITUNES|AMAZON.*PRIME|PRIME VIDEO|AUDIBLE|DAZN|CRUNCHYROLL)\\b"
            ),
            "match_payee_exact_word": False,
            "match_purpose": None,
            "match_purpose_exact_word": False,
            "exclude_payee_raw": None,
            "exclude_purpose": None,
            "match_min_amount_minor": None,
            "match_max_amount_minor": None,
            "match_amount_mode": "inside",
            "action_category_key": "streaming",
            "action_set_transfer": False,
            "action_note": None,
            "priority": 10,
            "is_active": True,
        },
    ]
}
