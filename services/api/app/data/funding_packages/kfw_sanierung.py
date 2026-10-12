"""KfW-Sanierungsfoerderung fuer Wohngebaeude (BEG).

Recherchestand 2026-08-08, Quelle je Programm unten. Wie beim kuratierten
Katalog in `0001_schema.sql` gilt: was nicht sicher recherchiert ist, wird zu
einem `manual_check` und NICHT zu einer erfundenen Zahl.

Bewusst nicht abgebildet: der Tilgungszuschuss. Die Betrags-DSL kennt dafuer
noch keinen Typ (siehe Import-Plan Abschnitt 6). Er steht deshalb als
`manual_check` und in `manual_checks_json`, damit im Foerdercheck sichtbar
bleibt, dass ein Teil der Foerderung hier noch nicht mitgerechnet wird -- statt
ihn stillschweigend wegzulassen.

Die Zinssaetze der KfW werden taeglich neu festgesetzt und sind deshalb in
`loan_terms_json` bewusst NICHT hinterlegt. Laufzeit und tilgungsfreie Zeit
schon: sie sind stabil und ersparen beim Uebernehmen die Handarbeit.
"""

PACKAGE = {
    "format_version": "1.0",
    "id": "kfw_sanierung",
    "name": "KfW Sanierung & Effizienz",
    "description": "Förderkredite der KfW für die Sanierung selbstgenutzter Wohngebäude.",
    "icon": "🏚️",
    "publisher": "KfW",
    "package_version": "2026-08-08",
    "researched_on": "2026-08-08",
    "programs": [
        {
            "semantic_key": "kfw_261_beg_wohngebaeude",
            "provider_key": "kfw",
            "provider_name": "KfW",
            "program_code": "261",
            "title": "Bundesförderung für effiziente Gebäude — Wohngebäude Kredit (261)",
            "country_code": "DE",
            "scope": "national",
            "region_codes": [],
            "official_url": "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Bestandsimmobilie/",
            "source_url": "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Bestandsimmobilie/",
            "source_version": "curated-2026-08-08",
            "valid_from": "2026-01-01",
            "valid_to": None,
            "review_due_on": "2026-11-01",
            "benefit_type": "loan_with_grant",
            "applicant_types": ["private_individual"],
            "project_types": ["renovation"],
            "criteria_json": {
                "all": [
                    {
                        "manual_check": {
                            "key": "kfw_261_efficiency_level",
                            "label": (
                                "Das Gebäude erreicht nach der Sanierung eine "
                                "Effizienzhaus-Stufe des Programms (Nachweis durch "
                                "Energieeffizienz-Expertin oder -Experten)"
                            ),
                        }
                    },
                    {
                        "manual_check": {
                            "key": "kfw_261_expert_before_start",
                            "label": (
                                "Fachplanung und Baubegleitung wurden vor Vorhabenbeginn "
                                "beauftragt und der Antrag vor Beginn gestellt"
                            ),
                        }
                    },
                ]
            },
            # Nur der Kreditrahmen, kein Tilgungszuschuss: der Betragstyp dafuer
            # fehlt der Engine noch. Lieber ein Teilbetrag mit Hinweis als eine
            # Gesamtsumme, die eine nicht gerechnete Komponente behauptet.
            "benefit_json": {"type": "loan_fixed", "amount_minor": 15_000_000},
            "loan_terms_json": {
                "max_term_months": 420,
                "max_repayment_free_months": 60,
                "note": (
                    "Zinssatz wird von der KfW täglich neu festgesetzt und ist hier "
                    "bewusst nicht hinterlegt. Der Tilgungszuschuss ist im "
                    "geschätzten Betrag nicht enthalten."
                ),
            },
            "combination_rules_json": {},
            "manual_checks_json": [
                "Effizienzhaus-Stufe und Tilgungszuschuss aus der aktuellen Zusage übernehmen",
                "Nachweis Energieeffizienz-Experte",
            ],
            "license_name": "Recherchestand, keine amtliche Lizenz",
            "attribution": "KfW",
            # 'rules_partial', nicht 'rules_complete': der Tilgungszuschuss und die
            # Staffelung nach Effizienzhaus-Stufe sind nicht abgebildet.
            "normalization_status": "rules_partial",
        },
        {
            "semantic_key": "kfw_358_ergaenzungskredit_heizung",
            "provider_key": "kfw",
            "provider_name": "KfW",
            "program_code": "358",
            "title": "Ergänzungskredit — Wohngebäude (358)",
            "country_code": "DE",
            "scope": "national",
            "region_codes": [],
            "official_url": "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Bestandsimmobilie/",
            "source_url": "https://www.kfw.de/inlandsfoerderung/Privatpersonen/Bestandsimmobilie/",
            "source_version": "curated-2026-08-08",
            "valid_from": "2026-01-01",
            "valid_to": None,
            "review_due_on": "2026-11-01",
            "benefit_type": "loan",
            "applicant_types": ["private_individual"],
            "project_types": ["renovation"],
            "criteria_json": {
                "all": [
                    {
                        "compare": {
                            "field": "self_occupied",
                            "op": "eq",
                            "value": True,
                            "label": "Selbstnutzung des Wohngebäudes",
                        }
                    },
                    {
                        "manual_check": {
                            "key": "kfw_358_zuschusszusage",
                            "label": (
                                "Für dieselbe Maßnahme liegt bereits eine Zuschusszusage "
                                "(BAFA/KfW Einzelmaßnahme) vor"
                            ),
                        }
                    },
                ]
            },
            "benefit_json": {"type": "loan_fixed", "amount_minor": 12_000_000},
            "loan_terms_json": {
                "max_term_months": 420,
                "note": (
                    "Zinsverbilligung ist an eine Einkommensgrenze geknüpft; ohne "
                    "geprüfte Staffel hier nicht hinterlegt."
                ),
            },
            "combination_rules_json": {},
            "manual_checks_json": [
                "Einkommensgrenze für die Zinsverbilligung gegen das aktuelle Merkblatt prüfen",
            ],
            "license_name": "Recherchestand, keine amtliche Lizenz",
            "attribution": "KfW",
            "normalization_status": "rules_partial",
        },
    ],
}
