"""KfW 308 "Jung kauft Alt" als installierbare Foerdervorlage.

Recherchestand und Konditionen vom 09.08.2026. Die vier veröffentlichten
Konditionsmodelle bleiben als gekoppelte Alternativen erhalten, damit Laufzeit,
Anlaufzeit und Zinssatz nicht versehentlich miteinander vermischt werden.
"""

PACKAGE = {
    "format_version": "1.0",
    "id": "kfw_jung_kauft_alt",
    "name": "KfW Jung kauft Alt",
    "description": (
        "Förderkredit 308 für Familien, die eine ältere Immobilie kaufen, "
        "selbst nutzen und energetisch sanieren."
    ),
    "icon": "🏠",
    "publisher": "KfW",
    "package_version": "2026-08-09.2",
    "researched_on": "2026-08-09",
    "programs": [
        {
            "semantic_key": "kfw_308_jung_kauft_alt",
            "provider_key": "kfw",
            "provider_name": "KfW",
            "program_code": "308",
            "title": "Jung kauft Alt – Wohneigentum für Familien (308)",
            "country_code": "DE",
            "scope": "national",
            "region_codes": [],
            "official_url": (
                "https://www.kfw.de/inlandsfoerderung/Privatpersonen/"
                "Bestehende-Immobilie/F%C3%B6rderprodukte/"
                "Wohneigentum-f%C3%BCr-Familien-Bestandserwerb-%28308%29/"
            ),
            "source_url": (
                "https://www.kfw.de/inlandsfoerderung/Privatpersonen/"
                "Bestehende-Immobilie/F%C3%B6rderprodukte/"
                "Wohneigentum-f%C3%BCr-Familien-Bestandserwerb-%28308%29/"
            ),
            "source_version": "kfw-308-konditionen-2026-08-09",
            "valid_from": "2026-08-03",
            "valid_to": None,
            "review_due_on": "2026-11-03",
            "benefit_type": "loan",
            "applicant_types": ["private_individual"],
            "project_types": ["property_purchase"],
            "criteria_json": {
                "all": [
                    {
                        "compare": {
                            "field": "self_occupied",
                            "op": "eq",
                            "value": True,
                            "label": "Die erworbene Immobilie wird selbst bewohnt",
                        }
                    },
                    {
                        "compare": {
                            "field": "children_count",
                            "op": "gte",
                            "value": 1,
                            "label": "Mindestens ein Kind lebt im Haushalt",
                        }
                    },
                    {
                        "linear_threshold": {
                            "field": "household_income_year_minor",
                            "op": "lte",
                            "base_minor": 9_000_000,
                            "per_unit_field": "children_count",
                            "unit_offset": 1,
                            "per_unit_amount_minor": 1_000_000,
                            "label": (
                                "Haushaltseinkommen höchstens 90.000 Euro bei einem "
                                "Kind plus 10.000 Euro je weiterem Kind"
                            ),
                        }
                    },
                    {
                        "compare": {
                            "field": "existing_homeowner",
                            "op": "eq",
                            "value": False,
                            "label": "Bei Antragstellung besteht kein Wohneigentum",
                        }
                    },
                    {
                        "manual_check": {
                            "key": "kfw_308_minor_child",
                            "label": "Mindestens ein berücksichtigtes Kind ist unter 18 Jahre alt",
                        }
                    },
                    {
                        "manual_check": {
                            "key": "kfw_308_energy_class",
                            "label": "Der Energieausweis weist die Klasse F, G oder H aus",
                        }
                    },
                    {
                        "manual_check": {
                            "key": "kfw_308_no_prior_family_funding",
                            "label": (
                                "Keine Person im künftigen Haushalt erhielt bereits "
                                "Baukindergeld 424 oder eine Förderung 300/308"
                            ),
                        }
                    },
                    {
                        "manual_check": {
                            "key": "kfw_308_before_purchase",
                            "label": "Der Antrag wird vor Unterzeichnung des Kaufvertrags gestellt",
                        }
                    },
                    {
                        "manual_check": {
                            "key": "kfw_308_renovation_commitment",
                            "label": (
                                "Die energetische Sanierung wird innerhalb von 54 Monaten "
                                "nach KfW-Zusage abgeschlossen"
                            ),
                        }
                    },
                ]
            },
            "benefit_json": {
                "type": "loan_tiered",
                "tier_field": "children_count",
                "tiers": [
                    {"min": 1, "max": 1, "amount_minor": 14_000_000},
                    {"min": 2, "max": 2, "amount_minor": 16_000_000},
                    {"min": 3, "amount_minor": 18_000_000},
                ],
            },
            "loan_terms_json": {
                "rate_as_of": "2026-08-09",
                "variants": [
                    {
                        "id": "annuity_7_10",
                        "label": "Annuität 7–10 Jahre",
                        "repayment_type": "annuity",
                        "min_term_months": 84,
                        "max_term_months": 120,
                        "fixed_rate_months": 120,
                        "min_repayment_free_months": 12,
                        "max_repayment_free_months": 24,
                        "nominal_rate_bps_annual": 1,
                        "effective_rate_bps_annual": 1,
                    },
                    {
                        "id": "annuity_11_25",
                        "label": "Annuität 11–25 Jahre",
                        "repayment_type": "annuity",
                        "min_term_months": 132,
                        "max_term_months": 300,
                        "fixed_rate_months": 120,
                        "min_repayment_free_months": 12,
                        "max_repayment_free_months": 36,
                        "nominal_rate_bps_annual": 25,
                        "effective_rate_bps_annual": 25,
                    },
                    {
                        "id": "annuity_26_35",
                        "label": "Annuität 26–35 Jahre",
                        "repayment_type": "annuity",
                        "min_term_months": 312,
                        "max_term_months": 420,
                        "fixed_rate_months": 120,
                        "min_repayment_free_months": 12,
                        "max_repayment_free_months": 60,
                        "nominal_rate_bps_annual": 53,
                        "effective_rate_bps_annual": 53,
                    },
                    {
                        "id": "bullet_7_10",
                        "label": "Endfälliges Darlehen 7–10 Jahre",
                        "repayment_type": "bullet",
                        "min_term_months": 84,
                        "max_term_months": 120,
                        "fixed_rate_months": 120,
                        "min_repayment_free_months": 0,
                        "max_repayment_free_months": 0,
                        "nominal_rate_bps_annual": 69,
                        "effective_rate_bps_annual": 69,
                    },
                ],
                "note": (
                    "Die veröffentlichten Konditionen sind vorbelegt. Bei Laufzeiten "
                    "über die zehnjährige Zinsbindung hinaus ist ein Anschlusszins nur "
                    "eine Planungsannahme. Förderfähig ist der Kaufpreis einschließlich "
                    "Grundstückskosten, nicht jedoch Erwerbsneben- und Sanierungskosten."
                ),
            },
            "combination_rules_json": {},
            "manual_checks_json": [
                "Alter der Kinder am Tag der Antragstellung",
                "Energieeffizienzklasse F, G oder H laut gültigem Energieausweis",
                "Antrag vor Kaufvertrag",
                "Sanierungsweg und Abschluss innerhalb von 54 Monaten",
                "Keine frühere Förderung 424, 300 oder 308 im künftigen Haushalt",
            ],
            "license_name": "Recherchestand, keine amtliche Lizenz",
            "attribution": "KfW",
            "normalization_status": "rules_complete",
        }
    ],
}
