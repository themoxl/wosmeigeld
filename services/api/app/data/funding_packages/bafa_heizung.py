"""Heizungsförderung (BEG Einzelmaßnahmen).

Recherchestand 2026-08-08. Abgebildet ist ausschliesslich die Grundfoerderung
als Prozentsatz der foerderfaehigen Kosten. Die Boni (Klimageschwindigkeit,
Einkommen, Effizienz) stehen als `manual_check`, weil die Betrags-DSL keinen
Gesamtdeckel ueber kombinierte Komponenten kennt (Import-Plan Abschnitt 6) --
ohne diesen Deckel wuerde eine Summierung ueber den zulaessigen Foerdersatz
hinauslaufen und einen zu hohen Betrag behaupten.

`eligible_funding_tags` verbindet den Prozentsatz mit dem Kostenplan: gerechnet
wird ueber die Kostenpositionen, die mit `heating` oder `renewable_energy`
markiert sind.
"""

PACKAGE = {
    "format_version": "1.0",
    "id": "bafa_heizung",
    "name": "Heizungsförderung (BEG Einzelmaßnahmen)",
    "description": "Zuschuss für den Austausch der Heizung im selbstgenutzten Bestandsgebäude.",
    "icon": "🔥",
    "publisher": "Bundesamt für Wirtschaft und Ausfuhrkontrolle / KfW",
    "package_version": "2026-08-08",
    "researched_on": "2026-08-08",
    "programs": [
        {
            "semantic_key": "beg_em_heizungstausch_grundfoerderung",
            "provider_key": "beg_em",
            "provider_name": "BEG Einzelmaßnahmen",
            "program_code": "458",
            "title": "Heizungstausch — Grundförderung (BEG Einzelmaßnahmen)",
            "country_code": "DE",
            "scope": "national",
            "region_codes": [],
            "official_url": "https://www.energiewechsel.de/",
            "source_url": "https://www.energiewechsel.de/",
            "source_version": "curated-2026-08-08",
            "valid_from": "2026-01-01",
            "valid_to": None,
            "review_due_on": "2026-11-01",
            "benefit_type": "grant",
            "applicant_types": ["private_individual"],
            "project_types": ["renovation"],
            "criteria_json": {
                "all": [
                    {
                        "compare": {
                            "field": "self_occupied",
                            "op": "eq",
                            "value": True,
                            "label": "Selbstnutzung des Gebäudes",
                        }
                    },
                    {
                        "manual_check": {
                            "key": "beg_em_heizung_erneuerbar",
                            "label": (
                                "Die neue Heizung erfüllt die Anforderungen an den "
                                "Anteil erneuerbarer Energien"
                            ),
                        }
                    },
                    {
                        "manual_check": {
                            "key": "beg_em_bestandsgebaeude",
                            "label": "Es handelt sich um ein Bestandsgebäude, keinen Neubau",
                        }
                    },
                ]
            },
            "benefit_json": {
                "type": "percent_of_costs",
                "rate_bps": 3000,
                # 30 % der foerderfaehigen Kosten, gedeckelt auf 30.000 EUR
                # foerderfaehige Kosten fuer die erste Wohneinheit -> 9.000 EUR.
                "cap_minor": 900_000,
                "eligible_funding_tags": ["heating", "renewable_energy"],
            },
            "loan_terms_json": {},
            "combination_rules_json": {},
            "manual_checks_json": [
                "Klimageschwindigkeits-, Einkommens- und Effizienzbonus separat prüfen — "
                "sie sind hier nicht eingerechnet, weil der gemeinsame Höchstsatz noch "
                "nicht abbildbar ist",
                "Förderfähige Höchstkosten bei mehreren Wohneinheiten prüfen",
            ],
            "license_name": "Recherchestand, keine amtliche Lizenz",
            "attribution": "BAFA / KfW",
            "normalization_status": "rules_partial",
        },
    ],
}
