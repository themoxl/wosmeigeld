"""Aktuelle Eigenwohnraum-Foerderungen des Freistaats Bayern/BayernLabo.

Stand 2026-08-09. Die allgemeine Eigenwohnraumfoerderung wurde 2026 im
Bayerischen Zinsverbilligungsprogramm neu gefasst. Daneben besteht die
Foerderung zur Anpassung von Wohnraum an die Belange von Menschen mit
Behinderung fort.
"""

_ALL_RESIDENTIAL_COST_TAGS = [
    "land_purchase",
    "construction",
    "planning",
    "permits",
    "acquisition_fees",
    "energy_efficiency",
    "heating",
    "renewable_energy",
    "accessibility",
    "renovation",
    "equipment",
    "other",
]

PACKAGE = {
    "format_version": "1.0",
    "id": "bayernlabo_eigenwohnraum",
    "name": "BayernLabo Eigenwohnraum",
    "description": (
        "Zinsverbilligtes Darlehen mit Kinderzuschuss sowie Förderung für die "
        "behindertengerechte Anpassung von Wohnraum in Bayern."
    ),
    "icon": "🏡",
    "publisher": "BayernLabo / Freistaat Bayern",
    "package_version": "2026-08-09",
    "researched_on": "2026-08-09",
    "programs": [
        {
            # Ueberlagert nach Installation den zu allgemeinen, aelteren
            # kuratierten Seed mit demselben fachlichen Schluessel.
            "semantic_key": "bayernlabo_eigenwohnraumfoerderung",
            "provider_key": "bayernlabo",
            "provider_name": "BayernLabo",
            "program_code": None,
            "title": "Bayerisches Zinsverbilligungsprogramm für Eigenwohnraum",
            "country_code": "DE",
            "scope": "regional",
            "region_codes": ["DE-BY"],
            "official_url": "https://bayernlabo.de/eigenwohnraum/eigenheimfinanzierung",
            "source_url": (
                "https://bayernlabo.de/fileadmin/user_upload/eigenwohnraum/"
                "21_foerderprogramme/"
                "bayernlabo-merkblatt-bayerisches-zinsverbilligungsprogramm.pdf"
            ),
            "source_version": "bayernlabo-2026-05-01",
            "valid_from": "2026-05-01",
            "valid_to": None,
            "review_due_on": "2026-11-01",
            "benefit_type": "loan_with_grant",
            "applicant_types": ["private_individual"],
            "project_types": ["home_build", "property_purchase"],
            "criteria_json": {
                "all": [
                    {
                        "compare": {
                            "field": "self_occupied",
                            "op": "eq",
                            "value": True,
                            "label": "Der geförderte Wohnraum wird selbst genutzt",
                        }
                    },
                    {
                        "manual_check": {
                            "key": "bayernlabo_income_limit_2026",
                            "label": (
                                "Das bereinigte Haushaltseinkommen hält die Grenze nach "
                                "Art. 11 BayWoFG (Einkommensstufe III) ein"
                            ),
                        }
                    },
                    {
                        "manual_check": {
                            "key": "bayernlabo_equity_share",
                            "label": (
                                "Grundsätzlich 20 % der Gesamtkosten als Eigenleistung; "
                                "mindestens 15 % können nur im begründeten Einzelfall und "
                                "nach Prüfung durch die Bewilligungsstelle ausreichen"
                            ),
                        }
                    },
                    {
                        "manual_check": {
                            "key": "bayernlabo_before_start",
                            "label": (
                                "Antrag und Bewilligung erfolgen vor Baubeginn beziehungsweise "
                                "vor Abschluss des Kaufvertrags"
                            ),
                        }
                    },
                    {
                        "manual_check": {
                            "key": "bayernlabo_appropriate_costs",
                            "label": (
                                "Wohnungsgröße, Gesamtkosten und dauerhafte "
                                "Belastung sind angemessen"
                            ),
                        }
                    },
                ]
            },
            "benefit_json": {
                "type": "combined",
                "eligible_funding_tags": _ALL_RESIDENTIAL_COST_TAGS,
                "components": [
                    {
                        # Ein Drittel ist konservativ als 33,33 % abgebildet.
                        "type": "loan_percent_of_costs",
                        "rate_bps": 3333,
                        "label": ("Darlehen bis zu 33,33 % der förderfähigen Gesamtkosten"),
                    },
                    {
                        "type": "per_unit",
                        "unit_field": "children_count",
                        "amount_per_unit_minor": 500_000,
                        "label": "5.000 € Kinderzuschuss je Kind",
                    },
                ],
            },
            "loan_terms_json": {
                "rate_as_of": "2026-05-01",
                "max_repayment_free_months": 12,
                "initial_repayment_rate_bps": 200,
                "rate_options": [
                    {
                        "id": "fixed_10_years",
                        "label": "10 Jahre Zinsbindung",
                        "fixed_rate_months": 120,
                        "nominal_rate_bps_annual": 265,
                        "effective_rate_bps_annual": 272,
                    },
                    {
                        "id": "fixed_15_years",
                        "label": "15 Jahre Zinsbindung",
                        "fixed_rate_months": 180,
                        "nominal_rate_bps_annual": 295,
                        "effective_rate_bps_annual": 303,
                    },
                ],
                "note": (
                    "Veröffentlichte Konditionen mit Stand 01.05.2026. Nach Ende der "
                    "gewählten Zinsbindung wird der Zinssatz an den Kapitalmarktzins "
                    "angepasst; dieser Anschlusszins ist im Vorhaben eine Planungsannahme."
                ),
            },
            "combination_rules_json": {},
            "manual_checks_json": [
                "Einkommensberechnung durch die zuständige Bewilligungsstelle",
                ("Grundsätzlich 20 % Eigenleistung; 15 % nur als geprüfte Einzelfallausnahme"),
                "Aktueller Angebotszins und Wahl der Zinsbindung",
                "Kinderzahl einschließlich erwarteter Kinder zum Antragszeitpunkt",
            ],
            "license_name": "Recherchestand, keine amtliche Lizenz",
            "attribution": "BayernLabo / Bayerisches Staatsministerium für Wohnen",
            "normalization_status": "rules_partial",
        },
        {
            "semantic_key": "bayern_wohnraumanpassung_behinderung",
            "provider_key": "bayernlabo",
            "provider_name": "Freistaat Bayern / BayernLabo",
            "program_code": None,
            "title": "Anpassung von Wohnraum an die Bedürfnisse von Menschen mit Behinderung",
            "country_code": "DE",
            "scope": "regional",
            "region_codes": ["DE-BY"],
            "official_url": (
                "https://www.stmb.bayern.de/wohnen/foerderung/barrierefreieswohnen/index.php"
            ),
            "source_url": (
                "https://www.stmb.bayern.de/assets/stmi/wohnen/"
                "%C3%9Cbersicht_wohnraumf%C3%B6rderung.pdf"
            ),
            "source_version": "stmb-2026-04",
            "valid_from": "2026-01-01",
            "valid_to": None,
            "review_due_on": "2026-11-01",
            # Rechtlich leistungsfreies Darlehen, wirtschaftlich bei
            # Einhaltung der Bindung ein Zuschuss. So entsteht im Finanzplan
            # keine faelschliche Rueckzahlungsrate.
            "benefit_type": "grant",
            "applicant_types": ["private_individual"],
            "project_types": ["renovation"],
            "criteria_json": {
                "all": [
                    {
                        "any": [
                            {
                                "compare": {
                                    "field": "disability_severity_percent",
                                    "op": "gt",
                                    "value": 0,
                                    "label": "Eine begünstigte Person lebt mit einer Behinderung",
                                }
                            },
                            {
                                "manual_check": {
                                    "key": "bayern_disability_need",
                                    "label": (
                                        "Die Maßnahme erleichtert einer Person mit Behinderung "
                                        "die Nutzung der Wohnung"
                                    ),
                                }
                            },
                        ]
                    },
                    {
                        "manual_check": {
                            "key": "bayern_disability_income_limit",
                            "label": "Die Einkommensgrenze nach Art. 11 BayWoFG wird eingehalten",
                        }
                    },
                    {
                        "manual_check": {
                            "key": "bayern_disability_minimum_cost",
                            "label": (
                                "Die förderfähigen Gesamtkosten betragen mindestens 1.000 Euro"
                            ),
                        }
                    },
                    {
                        "manual_check": {
                            "key": "bayern_disability_before_start",
                            "label": (
                                "Die Maßnahme beginnt erst nach Abstimmung mit "
                                "der Bewilligungsstelle"
                            ),
                        }
                    },
                    {
                        "manual_check": {
                            "key": "bayern_disability_occupancy_binding",
                            "label": "Die fünfjährige Belegungsbindung kann eingehalten werden",
                        }
                    },
                ]
            },
            "benefit_json": {
                "type": "percent_of_costs",
                "rate_bps": 10_000,
                "cap_minor": 1_000_000,
                "eligible_funding_tags": ["accessibility"],
            },
            "loan_terms_json": {
                "note": (
                    "Leistungsfreies, zins- und tilgungsfreies Baudarlehen; nach "
                    "fünfjähriger Belegungsbindung wird die Schuld erlassen. Einmaliger "
                    "Verwaltungskostenbeitrag: 1 % bei Auszahlung."
                )
            },
            "combination_rules_json": {},
            "manual_checks_json": [
                "Soziale Dringlichkeit und verfügbare Haushaltsmittel",
                "Förderfähige behinderungsbedingte Mehrkosten",
                "Einmaliger Verwaltungskostenbeitrag von 1 %",
            ],
            "license_name": "Recherchestand, keine amtliche Lizenz",
            "attribution": "Bayerisches Staatsministerium für Wohnen / BayernLabo",
            "normalization_status": "rules_partial",
        },
    ],
}
