"""Referenzfaelle der Fristenrechnung (docs/VERSICHERUNGEN-PLAN.md, P1).

Tagesgenau und ohne Datenbank: der Rechenkern ist eine reine Funktion, und
genau deshalb laesst er sich gegen Gesetzestext pruefen statt gegen ein
Bauchgefuehl. Die Faelle sind nach der Norm benannt, gegen die sie pruefen.
"""

from datetime import date

from app.services.insurance_deadline_service import (
    compute_insurance_deadline,
    easter_sunday,
    is_business_day,
    is_public_holiday,
)

# --- Paragraph 193 BGB: die Gegenprobe --------------------------------------


def test_kuendigungsfrist_wird_nicht_auf_den_naechsten_werktag_geschoben() -> None:
    """BGH III ZR 172/04: Paragraph 193 BGB gilt auf Kuendigungsfristen NICHT.

    Der 30.09.2029 ist ein Sonntag. Bei einer Kuendigungsfrist bleibt das
    rechtliche Fristende der Sonntag - die Kuendigung am Montag waere zu spaet.
    Wuerde hier geschoben, verlaengerte das Werkzeug eine Frist, die es nicht
    gibt, und der Nutzer verpasste sie im Vertrauen darauf.
    """
    assert date(2029, 9, 30).weekday() == 6  # Sonntag

    # Versicherungsjahr 01.01.-31.12.; drei Monate vor dem Beginn der naechsten
    # Periode endet die Frist am 30.09.
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2029, 1, 10),
        main_due_on=date(2030, 1, 1),
        notice_period_months=3,
        service_channel="email",
        main_due_source="user_confirmed",
    )
    assert ergebnis.deadline_legal_on == date(2029, 9, 30)
    # Zugehen muss sie trotzdem an einem Werktag - der liegt DAVOR.
    assert ergebnis.safe_service_on == date(2029, 9, 28)
    assert ergebnis.safe_service_on.weekday() == 4  # Freitag


def test_widerrufsfrist_wird_sehr_wohl_geschoben() -> None:
    """Die Gegenprobe zur Gegenprobe.

    Fuer Widerrufs-, Anfechtungs-, Klage- und Zahlungsfristen gilt Paragraph
    193 BGB. Waere der Fristtyp nicht unterschieden, rechnete eine dieser
    Fristen still einen Tag zu frueh - die schaedliche Fehlerrichtung.
    """
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="widerrufsfrist",
        today=date(2029, 1, 10),
        main_due_on=date(2030, 1, 1),
        notice_period_months=3,
        service_channel="email",
        main_due_source="user_confirmed",
    )
    # Sonntag 30.09. -> Montag 01.10.
    assert ergebnis.deadline_legal_on == date(2029, 10, 1)


# --- Kfz: der 30.11. ist ein Rechenergebnis ---------------------------------


def test_kfz_stichtag_entsteht_aus_der_hauptfaelligkeit_und_ist_nicht_gesetzt() -> None:
    """Der oft genannte 30.11. gilt nur bei Versicherungsjahr = Kalenderjahr.

    Paragraph 11 Abs. 3 VVG gibt einen Monat vor; faellt die Hauptfaelligkeit
    auf den 01.01., ergibt sich daraus der 30.11. - nicht umgekehrt.

    Plan 13.1, RF-KFZ-FRIST-1: "2026-11-30, certainty = confirmed, Vertragsende
    2026-12-31". Die Periode endet am 31.12., nicht am 01.01. - der 01.01. ist
    der Beginn der naechsten.
    """
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
        main_due_on=date(2027, 1, 1),
        notice_period_months=1,
        main_due_source="user_confirmed",
    )
    assert ergebnis.term_ends_on == date(2026, 12, 31)
    assert ergebnis.deadline_legal_on == date(2026, 11, 30)


def test_abweichendes_versicherungsjahr_ergibt_einen_anderen_termin() -> None:
    """Genau der Fall, den ein fester 30.11. im Katalog verschluckt haette."""
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
        main_due_on=date(2026, 10, 1),
        notice_period_months=1,
        main_due_source="user_confirmed",
    )
    assert ergebnis.deadline_legal_on == date(2026, 8, 31)


def test_saisonkennzeichen_ergibt_den_31_01_und_nicht_den_28_01() -> None:
    """Plan 13.1, RF-KFZ-FRIST-5: `main_due_on` 2027-03-01 -> 2027-01-31.

    Der Fall, an dem sich die beiden denkbaren Rechenwege trennen. Zaehlt man
    vom Periodenende (28.02.) einen Monat zurueck, kommt der 28.01. heraus und
    der Nutzer verliert drei Tage seiner Frist; zaehlt man vom Periodenbeginn
    (01.03.) zurueck und geht einen Tag davor, kommt der 31.01. heraus. Der
    Plan nennt den 31.01., und nur der kurze Februar macht den Unterschied
    ueberhaupt sichtbar - beim Kalenderjahr stimmen beide Wege ueberein.
    """
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
        main_due_on=date(2027, 3, 1),
        notice_period_months=1,
        main_due_source="user_confirmed",
    )
    assert ergebnis.term_ends_on == date(2027, 2, 28)
    assert ergebnis.deadline_legal_on == date(2027, 1, 31)


def test_fristende_am_sonntag_bleibt_der_sonntag_der_zugang_rutscht_auf_freitag() -> None:
    """Plan 13.1, RF-KFZ-FRIST-6: Fristende So 2025-11-30, Zugang Fr 2025-11-28.

    Die Gegenprobe zu Paragraph 193 BGB bei `term_end`. Sie lief bis zur
    Korrektur der Periodengrenze ins Leere: das Fristende landete auf Montag,
    dem 01.12., und damit ausgerechnet nicht auf dem Wochenende, das dieser
    Fall pruefen soll.
    """
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2025, 6, 1),
        main_due_on=date(2026, 1, 1),
        notice_period_months=1,
        main_due_source="user_confirmed",
    )
    assert ergebnis.deadline_legal_on == date(2025, 11, 30)
    assert ergebnis.deadline_legal_on.weekday() == 6  # Sonntag, und er bleibt es
    assert ergebnis.safe_service_on == date(2025, 11, 28)
    assert ergebnis.safe_service_on.weekday() == 4  # Freitag


def test_moped_rechnet_vom_ende_des_versicherungsjahres_nach_paragraph_26_fzv() -> None:
    """Plan 13.1, `fixed_date`: Versicherungsjahr 01.03.-28.02., Frist 1 Monat.

    Der einzige echte `fixed_date`-Fall des Katalogs. Anders als bei
    `main_due_on` bezeichnet der gespeicherte Stichtag hier das ENDE der
    Periode (28.02.), nicht ihren Beginn - beides ueber denselben Rechenweg zu
    schicken, ergaebe bei einem von beiden ein um ein Jahr oder einen Tag
    versetztes Fristende.
    """
    ergebnis = compute_insurance_deadline(
        anchor="fixed_date",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
        notice_fixed_month=2,
        notice_fixed_day=28,
        notice_period_months=1,
        main_due_source="user_confirmed",
    )
    assert ergebnis.term_ends_on == date(2027, 2, 28)
    assert ergebnis.deadline_legal_on == date(2027, 1, 31)


# --- Herkunft schlaegt auf das Ergebnis durch --------------------------------


def test_ohne_bestaetigte_hauptfaelligkeit_ist_die_frist_nur_angenommen() -> None:
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
        main_due_on=date(2027, 1, 1),
        notice_period_months=1,
        main_due_source="catalog_suggested",
    )
    assert ergebnis.certainty == "assumed"
    assert "Annahme" in ergebnis.reason


def test_aus_tagen_umgerechnete_frist_bleibt_angenommen_trotz_bestaetigtem_datum() -> None:
    """Zwei Unsicherheitsquellen, nicht eine.

    Der Nutzer kann die Hauptfaelligkeit bestaetigt haben und die Monatszahl
    trotzdem geraten sein - sie stammt dann aus der Umrechnung von
    contracts.cancellation_period_days.
    """
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
        main_due_on=date(2027, 1, 1),
        notice_period_months=1,
        main_due_source="user_confirmed",
        notice_terms_source="derived_from_days",
    )
    assert ergebnis.certainty == "assumed"
    assert "Bezugspunkt" in ergebnis.reason


def test_fehlende_hauptfaelligkeit_ergibt_kein_datum_sondern_eine_aussage() -> None:
    ergebnis = compute_insurance_deadline(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
        main_due_on=None,
        notice_period_months=3,
    )
    assert ergebnis.certainty == "not_computable"
    assert ergebnis.deadline_legal_on is None
    assert ergebnis.reason == "Hauptfaelligkeit fehlt."


def test_ohne_anker_wird_nichts_erfunden() -> None:
    ergebnis = compute_insurance_deadline(
        anchor="none",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
    )
    assert ergebnis.certainty == "not_computable"
    assert ergebnis.deadline_legal_on is None


def test_reisepolice_ohne_ordentliche_frist_sagt_das_auch_so() -> None:
    ergebnis = compute_insurance_deadline(
        anchor="per_event",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
    )
    assert ergebnis.certainty == "not_computable"
    assert "ordentliche" in ergebnis.reason


# --- Paragraph 168 VVG: zum Schluss der Versicherungsperiode -----------------


def test_paragraph_168_kennt_keine_vorgelagerte_frist() -> None:
    """BU und Risikoleben: jederzeit zum Schluss der laufenden Periode.

    Es gibt keine Frist, die verstreichen kann - die Erklaerung muss nur bis
    zum Periodenende zugehen.

    Die Periode beginnt am 01.12. und endet am 30.11. des Folgejahres. Plan
    13.1 fuehrt denselben Fall fuer das Kalenderjahr: Kuendigung am 15.03.2026
    wirkt zum 31.12.2026 - zum Schluss der laufenden Periode, nicht zu ihrem
    Beginn.
    """
    ergebnis = compute_insurance_deadline(
        anchor="period_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 8, 14),
        main_due_on=date(2026, 12, 1),
        notice_period_months=None,
        main_due_source="user_confirmed",
    )
    assert ergebnis.deadline_legal_on == date(2026, 11, 30)
    assert ergebnis.term_ends_on == date(2026, 11, 30)


# --- Zustellweg -------------------------------------------------------------


def test_der_zustellweg_verschiebt_nur_das_handlungsdatum_nicht_die_frist() -> None:
    gemeinsam = dict(
        anchor="term_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 1, 5),
        main_due_on=date(2027, 1, 1),
        notice_period_months=1,
        main_due_source="user_confirmed",
    )
    per_post = compute_insurance_deadline(**gemeinsam, service_channel="post")  # type: ignore[arg-type]
    per_mail = compute_insurance_deadline(**gemeinsam, service_channel="email")  # type: ignore[arg-type]

    assert per_post.deadline_legal_on == per_mail.deadline_legal_on
    assert per_post.safe_service_on == per_mail.safe_service_on
    # Drei Werktage Vorlauf fuer den Postweg, keiner fuer die E-Mail.
    assert per_post.recommended_action_on < per_mail.recommended_action_on


# --- Werktags- und Feiertagsrechnung ----------------------------------------


def test_ostersonntag_und_die_beweglichen_feiertage_stimmen() -> None:
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)
    assert is_public_holiday(date(2026, 4, 3))  # Karfreitag
    assert is_public_holiday(date(2026, 4, 6))  # Ostermontag
    assert is_public_holiday(date(2026, 5, 14))  # Christi Himmelfahrt
    assert is_public_holiday(date(2026, 5, 25))  # Pfingstmontag


def test_feiertage_und_wochenenden_sind_keine_werktage() -> None:
    assert not is_business_day(date(2026, 1, 1))  # Neujahr
    assert not is_business_day(date(2026, 10, 3))  # Tag der Deutschen Einheit
    assert not is_business_day(date(2026, 12, 26))  # 2. Weihnachtsfeiertag
    assert not is_business_day(date(2026, 8, 15))  # Samstag
    assert is_business_day(date(2026, 8, 14))  # Freitag


def test_neujahr_als_fristende_verschiebt_den_sicheren_zugang_ins_alte_jahr() -> None:
    """01.01. ist Feiertag; zugestellt wird zuletzt am 31.12., sofern Werktag.

    Die Periode beginnt am 02.01. und endet deshalb am 01.01. - genau darauf
    kommt es hier an: das Fristende faellt auf Neujahr.
    """
    ergebnis = compute_insurance_deadline(
        anchor="period_end",
        deadline_kind="kuendigungsfrist",
        today=date(2026, 6, 1),
        main_due_on=date(2027, 1, 2),
        main_due_source="user_confirmed",
    )
    assert ergebnis.deadline_legal_on == date(2027, 1, 1)
    assert ergebnis.safe_service_on == date(2026, 12, 31)  # Donnerstag
    assert is_business_day(ergebnis.safe_service_on)
