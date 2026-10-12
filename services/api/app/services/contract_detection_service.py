"""Erkennung v2 fuer Vertraege (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 6).

Loest `rules_service.get_recurring_candidates` ab. Vier Fehler der alten
Erkennung werden hier ausdruecklich behoben:

* Ein Empfaenger mit mehreren Vertraegen erzeugte je Betragsniveau einen
  eigenen Vorschlag ("Musterfunk steht dreimal in der Liste"). Jetzt entsteht aus
  dem Verwendungszweck ein Vertragsschluessel, und Betragsniveaus, die
  zeitlich NACHEINANDER liegen, sind Preisstufen desselben Vertrags statt
  getrennter Vertraege (Abschnitt 6.1 und 6.2).
* Die Rhythmuserkennung sortierte zuerst nach Trefferquote und bestrafte
  uebersprungene Perioden erst danach. Ein einziger vorgezogener Zahltag hat
  "monatlich" unter die Schwelle gedrueckt, waehrend "woechentlich mit vier
  ausgelassenen Wochen" bei 100 % blieb - die Miete stand als "Woechentlich"
  in der Liste. Jetzt entscheidet zuerst die Zahl der DIREKTEN Periodentreffer
  (Abschnitt 6.3).
* Ob etwas ueberhaupt ein Vertrag ist, wurde gar nicht bewertet. Der
  Vertrags-Score (Abschnitt 6.4) beantwortet diese Frage getrennt vom
  Rhythmus und wird als `contracts.detection_confidence` gespeichert.
* Zahlungsdienstleister (PayPal, Klarna, ...) wurden wie Vertragspartner
  behandelt. Ohne Vertragsschluessel entsteht dort kein Vorschlag mehr
  (Abschnitt 6.5).

Aufbau: alles Rechnende ist DB-frei und arbeitet auf `DetectionTx`. Nur die
Funktionen am Ende der Datei sprechen mit der Datenbank. Die Bestandteile des
Scores werden mitgeliefert (`ScoreBreakdown`), damit die Oberflaeche erklaeren
kann, warum etwas vorgeschlagen wird - ein unerklaerter Score ist in einem
Finanzprodukt wertlos.

Committet wird hier nichts: die Transaktion umschliesst den gesamten Request
(`auth/dependencies.get_household_session`).
"""

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Final, Literal, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.contract import (
    ContractDirection,
    ContractFrequency,
    ContractKeySource,
    ContractType,
)
from app.schemas.review import (
    ReviewItemRecurring,
    ReviewRecurringDetail,
    ReviewRecurringTransaction,
)

# Bewusst aus dem Abgleichsmotor und nicht aus `rules_service`: `payee_key` und
# `amount_tolerance_minor` sind dort die oeffentlichen Namen derselben Formeln.
# Wuerde die Erkennung eine eigene Normalisierung mitbringen, koennte der
# Abgleich einen Vertrag nicht mehr wiederfinden, den die Erkennung selbst
# vorgeschlagen hat. Die Richtung bleibt damit eindeutig:
# detection -> matching -> contract_service.
from app.services import contract_service
from app.services.contract_matching_service import (
    amount_tolerance_minor,
    normalize_purpose,
    payee_key,
)

DetectionFrequency = ContractFrequency
Confidence = Literal["suggested", "uncertain"]


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# Schwellen des Vertrags-Scores (Abschnitt 6.4). Darunter erscheint gar nichts:
# der Fall "zwei Arztzahlungen im Quartalsabstand" soll nicht einmal als
# unsicherer Vorschlag auftauchen.
SUGGEST_MIN_SCORE: Final = 0.55
UNCERTAIN_MIN_SCORE: Final = 0.40

# Abschnitt 6.3: von 0.75 auf 0.60 gesenkt, damit ein einzelner verschobener
# Zahltag einen monatlichen Vertrag nicht mehr ganz herausfiltert. Das ist nur
# zulaessig, weil die Weekly-Sperre weiter unten greift - ohne sie waeren drei
# zufaellige Zahlungen als "woechentlich" zugelassen.
MATCH_RATIO_MIN: Final = 0.60

# Mehr als ein Jahr Luecke ist kein belastbarer laufender Rhythmus.
_MAX_SKIPPED_PERIODS: Final = 12

# Untergrenze beim Vergleich mit bereits bestaetigten Vertraegen. Ein Vertrag
# mit tolerance_amount_minor = 0 wuerde sonst schon an einem Cent scheitern und
# denselben Vorschlag erneut liefern.
_MIN_TOLERANCE_MINOR: Final = 100

_NEUTRAL_AFFINITY: Final = 0.5
_NEUTRAL_PAYMENT_METHOD: Final = 0.5

# Gewichte des Vertrags-Scores (Abschnitt 6.4).
_WEIGHT_DATE_STABILITY: Final = 0.30
_WEIGHT_AMOUNT_STABILITY: Final = 0.20
_WEIGHT_OCCURRENCES: Final = 0.15
_WEIGHT_CATEGORY: Final = 0.20
_WEIGHT_PAYMENT_METHOD: Final = 0.15

# Bezugsgroessen der beiden Stabilitaetsmasse.
_DATE_STABILITY_REFERENCE_DAYS: Final = 7.0
_AMOUNT_STABILITY_FACTOR: Final = 4.0
_OCCURRENCE_SATURATION: Final = 6

# Einnahmen sind erst ab drei Vorkommen und stabilem Zahltag ein Vertrag.
# Ohne diese Zusatzbedingung wuerde jede regelmaessige Erstattung zu einem
# Vorschlag, sobald der Vorzeichenfilter der Altfassung faellt.
_INCOME_MIN_OCCURRENCES: Final = 3
_INCOME_MIN_DATE_STABILITY: Final = 0.70

# Obergrenzen der Schluesselableitung. Die laengste gemeinsame Zeichenkette ist
# O(n*m) und laeuft bei JEDEM Inbox-Aufruf.
_MAX_PURPOSES_PER_PAYEE: Final = 40
_MAX_PURPOSE_CHARS: Final = 140
_MIN_KEY_LENGTH: Final = 6
_MIN_MANDATE_KEY_LENGTH: Final = 4
# Wie oft ein Mandatswert wiederkehren muss, um als Vertragsschluessel zu
# gelten - sobald ueberhaupt mehr als ein Verwendungszweck vorliegt.
_MIN_MANDATE_KEY_REPEATS: Final = 2
_MAX_KEY_CHARS: Final = 160

# Zahlungsdienstleister sind keine Vertragspartner (Abschnitt 6.5). Die Liste
# kann keine Gleichheitsmenge sein: `payee_key` ersetzt alle Nicht-Wortzeichen
# durch Leerzeichen, aus "PayPal (Europe) S.a r.l. et Cie, S.C.A." wird
# "paypal europe s a r l et cie s c a". Verglichen wird deshalb das erste Token.
AGGREGATOR_TOKENS: Final[frozenset[str]] = frozenset(
    {"paypal", "klarna", "amazon", "amzn", "apple", "google", "stripe", "shopify"}
)

# Abschnitt 6.4, auf die tatsaechlich vergebenen `categories.semantic_key`
# abgebildet (Standardpaket v1 und v2).
CATEGORY_AFFINITY: Final[dict[str, float]] = {
    # 1.00 - ohne Vertrag gibt es diese Zahlung nicht
    "rent": 1.0,
    "health_insurance": 1.0,
    "energy": 1.0,
    "telecom": 1.0,
    "broadcast_fee": 1.0,
    "private_pension": 1.0,
    "risk_insurance": 1.0,
    # 0.80 - fast immer Vertrag, aber auch als Einzelkauf denkbar
    "streaming": 0.8,
    "installments": 0.8,
    "childcare_school": 0.8,
    "saving_investing": 0.8,
    "donations": 0.8,
    # 0.20 - fast nie Vertrag
    "electronics": 0.2,
    "books_press": 0.2,
    # 0.00 - Einzelfall, auch bei zufaellig regelmaessigem Abstand
    "doctor": 0.0,
    "groceries": 0.0,
    "restaurant": 0.0,
    "fuel_charging": 0.0,
    "travel": 0.0,
}

# Rueckfall ueber die Kategoriegruppe. Greift fuer alle Kategorien des
# Standardpakets, die oben nicht einzeln genannt sind.
GROUP_AFFINITY: Final[dict[str, float]] = {
    "housing": 0.8,
    "health": 0.7,
    "finance": 0.6,
    "transfers": 0.6,
    "work": 0.6,
    "family": 0.5,
    "income": 0.5,
    "lifestyle": 0.4,
    "mobility": 0.3,
    "food": 0.1,
}

# Vertragsart aus der Kategorie. Nur eindeutige Faelle; alles andere bleibt
# 'other' und wird beim Bestaetigen vom Nutzer gesetzt.
_CONTRACT_TYPE_BY_SEMANTIC_KEY: Final[dict[str, ContractType]] = {
    "rent": "rent",
    "service_charges": "rent",
    "energy": "utility",
    "broadcast_fee": "utility",
    "telecom": "telecom",
    "health_insurance": "insurance",
    "property_insurance": "insurance",
    "risk_insurance": "insurance",
    "car_tax_insurance": "insurance",
    "private_pension": "savings",
    "saving_investing": "savings",
    "streaming": "subscription",
    "work_software": "subscription",
    "books_press": "subscription",
    "sports_clubs": "membership",
    "donations": "donation",
    "installments": "installment",
    "loan_interest": "loan",
    "salary": "income",
    "self_employment": "income",
    "pension": "income",
    "public_benefits": "income",
    "capital_income": "income",
}


@dataclass(frozen=True)
class FrequencySpec:
    """Sollabstand, erlaubte Abweichung und Mindestzahl an Vorkommen.

    `min_dates_without_key` ist die Mindestzahl, wenn KEIN Vertragsschluessel
    vorliegt. Nur `weekly` weicht ab: dort sind sechs Datenpunkte Pflicht, weil
    der Wochenrhythmus mit ausgelassenen Perioden fast jeden Abstand erklaeren
    kann und die gesenkte Trefferquote sonst Zufallsdaten durchliesse.
    """

    period_days: float
    tolerance_days: int
    min_occurrences: int
    min_dates_without_key: int


FREQUENCY_SPECS: Final[dict[DetectionFrequency, FrequencySpec]] = {
    "weekly": FrequencySpec(7.0, 2, 3, 6),
    "monthly": FrequencySpec(30.4375, 4, 3, 3),
    "quarterly": FrequencySpec(91.3125, 10, 2, 2),
    "semiannual": FrequencySpec(182.62, 14, 2, 2),
    "yearly": FrequencySpec(365.25, 21, 2, 2),
}

# Abschnitt 6.3: woechentlich braucht immer sechs Datenpunkte, auch mit
# Vertragsschluessel. Das ist keine Feinjustierung, sondern die Voraussetzung
# dafuer, dass MATCH_RATIO_MIN ueberhaupt auf 0.60 gesenkt werden durfte.
_WEEKLY_MIN_DATES: Final = 6

# Ein Paar aus genau zwei Buchungen ist bei langer Periode ohne weiteren Beleg
# zu wenig: zwei Arztrechnungen im Abstand von drei Monaten sehen genauso aus.
# Halbjaehrlich und jaehrlich stehen ausdruecklich mit in der Liste - je laenger
# der Abstand, desto duenner der Beleg, und zwei Zahlungen im Jahresabstand
# erreichen ueber Datums- und Betragsstabilitaet sonst muehelos die
# Vorschlagsschwelle.
_LONG_PERIOD_MIN_DATES_WITHOUT_PROOF: Final = 3
_LONG_PERIOD_FREQUENCIES: Final[frozenset[DetectionFrequency]] = frozenset(
    {"quarterly", "semiannual", "yearly"}
)


# ---------------------------------------------------------------------------
# Datentypen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectionTx:
    """Eine Buchung, reduziert auf das, was die Erkennung braucht.

    Bewusst kein Pydantic-Modell und ohne Session: der gesamte Kern muss ohne
    laufende Datenbank pruefbar sein.
    """

    transaction_id: UUID
    account_id: UUID
    payee_id: UUID | None
    payee_label: str
    payee_key: str
    # Vorzeichen wie in `transactions`: Ausgaben sind negativ.
    amount_minor: int
    booked_date: date
    purpose_raw: str | None
    purpose_key: str
    bank_transaction_code: str | None
    # 'mt940' | 'camt' | 'paypal_csv' | 'csv_generic' | ... | None
    import_format: str | None
    # Die tatsaechlich zugeordnete Kategorie des Splits.
    category_id: UUID | None
    category_source: str = "unset"
    # Letzter KI-Entscheid; nur Rueckfall, wenn kein Split eine Kategorie traegt.
    ai_category_id: UUID | None = None
    account_name: str = ""
    raw_payee_label: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class AmountStep:
    """Eine Preisstufe: derselbe Vertrag zu einem anderen Betrag."""

    amount_minor: int
    first_date: date
    last_date: date
    # Neueste zuerst - wie ueberall in diesem Modul.
    transactions: tuple[DetectionTx, ...]


@dataclass(frozen=True)
class ScoreBreakdown:
    """Die fuenf Bestandteile des Vertrags-Scores einzeln, nicht nur die Summe.

    Ohne die Bestandteile kann die Oberflaeche nicht begruenden, warum ein
    Vorschlag entstanden ist - und der Nutzer nicht beurteilen, ob die
    Begruendung traegt.
    """

    date_stability: float
    amount_stability: float
    occurrence_count: float
    category_affinity: float
    payment_method: float
    # Auf zwei Nachkommastellen gerundet: contracts.detection_confidence ist
    # numeric(3,2).
    total: float


@dataclass(frozen=True)
class ContractCandidate:
    """Serverinterner Vertragskandidat einschliesslich DB-Referenzen.

    Beim Annehmen wird der Kandidat anhand `source_transaction_id` frisch
    berechnet; dadurch kann der Client weder einen Betrag noch einen fremden
    Empfaenger einschleusen.
    """

    source_transaction_id: UUID
    account_id: UUID | None
    payee_id: UUID | None
    payee_key: str
    payee_label: str
    # Bei einem Sammelkonto "PayPal · <Schluessel>", sonst der Empfaenger.
    display_name: str
    contract_key: str | None
    contract_key_source: ContractKeySource | None
    direction: ContractDirection
    # Vertragskonvention: IMMER positiv, die Richtung steht in `direction`.
    amount_minor: int
    amount_tolerance_minor: int
    frequency: DetectionFrequency
    first_date: date
    last_date: date
    occurrences_count: int
    # Aelteste zuerst; die letzte Stufe ist der Sollbetrag.
    amount_steps: tuple[AmountStep, ...]
    suggested_category_id: UUID | None
    contract_type: ContractType
    installments_total: int | None
    installments_paid: int | None
    score: ScoreBreakdown
    confidence: Confidence
    transactions: tuple[DetectionTx, ...]

    @property
    def current_step(self) -> AmountStep:
        """Die juengste Preisstufe - sie traegt den Sollbetrag."""
        return self.amount_steps[-1]

    def as_legacy_item(self) -> ReviewItemRecurring | None:
        """Projektion auf das alte Review-Item fuer bereits ausgelieferte Clients.

        `None` heisst: dieser Kandidat laesst sich im Altformat nicht
        darstellen. Das gilt fuer halbjaehrliche Vertraege - `suggested_frequency`
        ist dort ein Literal aus vier Werten und der Response-Validator wuerde
        die gesamte Antwort verwerfen - und fuer unsichere Kandidaten, fuer die
        der alte Client keine Kennzeichnung hat und die er deshalb faelschlich
        als gleichwertig anzeigen wuerde.
        """
        if self.frequency == "semiannual" or self.confidence != "suggested":
            return None
        # Vorzeichenwechsel: `expected_amount_minor` folgt der Buchungs-, nicht
        # der Vertragskonvention.
        expected = -self.amount_minor if self.direction == "expense" else self.amount_minor
        return ReviewItemRecurring(
            source_transaction_id=self.source_transaction_id,
            payee_label=self.payee_label,
            occurrences_count=self.occurrences_count,
            expected_amount_minor=expected,
            amount_tolerance_minor=self.amount_tolerance_minor,
            suggested_frequency=self.frequency,
            first_date=self.first_date,
            last_date=self.last_date,
            suggested_category_id=self.suggested_category_id,
        )

    def as_legacy_detail(self) -> ReviewRecurringDetail | None:
        item = self.as_legacy_item()
        if item is None:
            return None
        return ReviewRecurringDetail(
            suggestion=item,
            transactions=[
                ReviewRecurringTransaction(
                    transaction_id=row.transaction_id,
                    account_id=row.account_id,
                    account_name=row.account_name,
                    booked_date=row.booked_date,
                    amount_minor=row.amount_minor,
                    payee_label=row.raw_payee_label,
                    note=row.note,
                )
                for row in self.transactions
            ],
        )


# ---------------------------------------------------------------------------
# 6.1 Vertragsschluessel aus dem Verwendungszweck
# ---------------------------------------------------------------------------

_WHITESPACE_RE: Final = re.compile(r"\s+")

# MT940 legt den Verwendungszweck als strukturiertes Feld :86: ab und speichert
# ihn genau so in `transactions.purpose_raw`:
#   "105?00EINZUGSERMAECHTIGUNG?10931?20EREF+...?21KREF+...?23SVWZ+...?32Name"
# Der fuehrende Geschaeftsvorfallcode, der Buchungstext (?00), die Primanota
# (?10) und die Bankverbindung (?30 bis ?33) stehen bei JEDEM Empfaenger gleich
# da. Ohne diese Zerlegung ist die laengste gemeinsame Zeichenkette genau
# dieser Rahmen - ein Schluessel, der alle Vertraege aller Empfaenger auf einen
# Nenner zieht und damit das genaue Gegenteil dessen bewirkt, wofuer er da ist.
_MT940_SUBFIELD_RE: Final = re.compile(r"\?(\d{2})")
_MT940_PURPOSE_TAGS: Final[frozenset[str]] = frozenset(
    {"20", "21", "22", "23", "24", "25", "26", "27", "28", "29"}
)

# SEPA-Kennungen im Verwendungszweck. Sie stehen direkt aneinander, deshalb
# endet ein Wert nicht erst am Leerzeichen, sondern schon an der naechsten
# Kennung ("MREF+1000-KITA-0001CRED+DE12ZZZ...").
#
# Aus demselben Grund steht vor keiner Kennung ein \b: nach dem Aufloesen des
# Bankfeldes klebt sie an der vorigen Ziffernfolge ("...100100MREF+100000000"),
# und eine Wortgrenze gibt es dort nicht. Mit \b blieb die Mandatsreferenz
# unsichtbar und der Schluessel fiel auf die generische Ableitung zurueck.
_SEPA_VALUE_END: Final = r"(?=\s|[A-Z]{4}\+|$)"

# Reihenfolge ist bedeutungstragend: erst Datumsangaben, sonst frisst das
# Betragsmuster deren Bruchstuecke ("01.02" aus "01.02.2026") und laesst den
# Rest stehen.
_VOLATILE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # End-to-End- und Kundenreferenz wechseln bei JEDER Zahlung. Sie stehen
    # zuerst, weil ihre Werte sonst als Datum oder Betrag halb abgeraeumt
    # wuerden und ihr Rest in den Schluessel geriete.
    re.compile(r"(?:EREF|KREF)\s*[:+]\s*\S+?" + _SEPA_VALUE_END),
    # 01.02.2026, 01/02/26, 01-02-2026
    re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"),
    # Zeitraeume: 08/2026, 07/26, 08-2026. Der Bindestrich ist nur mit
    # vierstelliger Jahreszahl zugelassen - "12-3456" waere sonst kein
    # Vertragsbestandteil mehr, sondern ein Zeitraum.
    re.compile(r"\b\d{1,2}/\d{2,4}\b"),
    re.compile(r"\b\d{1,2}-\d{4}\b"),
    re.compile(r"\bKW\s?\d{1,2}\b"),
    # Betragsangaben
    re.compile(r"\d+[,.]\d{2}\b"),
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    # Die uebrigen SEPA-Kennungen selbst tragen keine Information: sie stehen
    # in jeder Lastschrift und wuerden die gemeinsame Zeichenkette verlaengern,
    # ohne einen Vertrag zu benennen.
    re.compile(r"(?:SVWZ|PURP|ABWA|ABWE|IBAN|BIC|COAM|OAMT|DEBT|CRED|MREF)\+"),
    re.compile(
        r"\b(?:JANUAR|FEBRUAR|MAERZ|MÄRZ|APRIL|MAI|JUNI|JULI|AUGUST|SEPTEMBER|"
        r"OKTOBER|NOVEMBER|DEZEMBER|JAN|FEB|MRZ|MAR|APR|JUN|JUL|AUG|SEPT|SEP|"
        r"OKT|NOV|DEZ)\b"
    ),
)

# Ein Treffer hieraus gewinnt gegen die generische Ableitung und setzt
# contract_key_source='mandate'. `MREF+` ist die Bankschreibweise der
# Mandatsreferenz, `CRED+` die der Glaeubiger-ID; im deutschen Zahlungsverkehr
# stehen praktisch nur diese beiden Formen im Auszug.
#
# `CRED+` steht bewusst ganz am Ende: die Glaeubiger-ID benennt den ANBIETER,
# nicht den Vertrag. Zwei Vertraege desselben Anbieters teilen sie sich - jede
# spezifischere Angabe muss deshalb zuerst greifen.
#
# JEDES Muster endet an `_SEPA_VALUE_END` und nicht erst am Leerzeichen: die
# Bank haengt die naechste Kennung ohne Trennzeichen an
# ("MANDATSREF. M4711EREF+2026010100001"). Mit einem reinen `\S+` waere die
# End-to-End-Referenz Teil des Schluessels - und die wechselt bei jeder
# Zahlung, sodass der "Vertragsschluessel" genau eine Buchung benennt.
_MANDATE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"MANDATSREF(?:ERENZ)?[.:\s]*(\S+?)" + _SEPA_VALUE_END),
    re.compile(r"MREF\s*[:+]\s*(\S+?)" + _SEPA_VALUE_END),
    re.compile(r"KUNDENN(?:R|UMMER)[.:\s]*(\S+?)" + _SEPA_VALUE_END),
    re.compile(r"VERTRAGSN(?:R|UMMER)[.:\s]*(\S+?)" + _SEPA_VALUE_END),
    # Weitere Anbieterschreibweisen (Abschnitt 19.3), nicht in den urspruenglichen
    # Referenzfaellen abgedeckt: "Kundenkonto 0011223344", "Kunden-Konto:
    # 0011223344", "Vertragskonto Nr. 1122334455". `[\s-]*` zwischen den
    # Wortteilen faengt sowohl den Bindestrich als auch einen Zeilenumbruch ab
    # (normalize_purpose_for_key hat Umbrueche bereits zu Leerzeichen
    # zusammengezogen). Ein optionales "NR."/"NUMMER" vor dem Doppelpunkt ist
    # dieselbe Variante wie bei KUNDENNR/VERTRAGSNR oben, nur nachgestellt statt
    # im Wort selbst.
    re.compile(r"KUNDEN[\s-]*KONTO(?:\s*NR\.?|\s*NUMMER)?[.:\s]*(\S+?)" + _SEPA_VALUE_END),
    re.compile(r"VERTRAGS[\s-]*KONTO(?:\s*NR\.?|\s*NUMMER)?[.:\s]*(\S+?)" + _SEPA_VALUE_END),
    re.compile(r"VERSICHERUNGSSCHEIN(?:NR|NUMMER)?[.:\s]*(\S+?)" + _SEPA_VALUE_END),
    re.compile(r"GL(?:AE|Ä)UBIGER[\s-]*ID[.:\s]*(\S+?)" + _SEPA_VALUE_END),
    re.compile(r"CRED\s*[:+]\s*(\S+?)" + _SEPA_VALUE_END),
)

# "Rate 3/12", "Rate 3 von 12", "3. Rate von 12"
_INSTALLMENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"RATE\s*(\d{1,3})\s*(?:/|VON)\s*(\d{1,3})"),
    re.compile(r"(\d{1,3})\s*(?:/|VON)\s*(\d{1,3})\s*RATE"),
    re.compile(r"(\d{1,3})\.?\s*RATE\s*(?:/|VON)\s*(\d{1,3})"),
    re.compile(r"TEILZAHLUNG\s*(\d{1,3})\s*(?:/|VON)\s*(\d{1,3})"),
)

_INSTALLMENT_MIN_TOTAL: Final = 2
_INSTALLMENT_MAX_TOTAL: Final = 240


def unwrap_structured_purpose(value: str) -> str:
    """Loest das strukturierte MT940-Feld :86: in seinen fachlichen Kern auf.

    Behalten werden nur die Verwendungszweckfelder ?20 bis ?29. Zusammengesetzt
    wird OHNE Trennzeichen: die Bank bricht Woerter mitten im Wort ueber die
    Feldgrenze ("... - AR" + "AG Versicherungen"), ein eingefuegtes Leerzeichen
    wuerde daraus zwei Bruchstuecke machen.

    Alles andere (CAMT, CSV, PayPal, von Hand erfasst) geht unveraendert durch.
    """
    if "?" not in value:
        return value
    parts = _MT940_SUBFIELD_RE.split(value)
    if len(parts) < 3:
        return value
    kept = [
        segment
        for tag, segment in zip(parts[1::2], parts[2::2], strict=False)
        if tag in _MT940_PURPOSE_TAGS
    ]
    return "".join(kept) if kept else value


def normalize_purpose_for_key(value: str | None) -> str:
    """Schritt 1 aus Abschnitt 6.1: Grossschreibung, Mehrfachleerzeichen weg.

    Die Laengenbegrenzung greift erst NACH dem Aufloesen des Bankfeldes - sonst
    faellt die Mandatsreferenz der Kuerzung zum Opfer, weil vor ihr 60 Zeichen
    Bankrahmen stehen.
    """
    if not value:
        return ""
    unwrapped = unwrap_structured_purpose(value)
    return _WHITESPACE_RE.sub(" ", unwrapped.strip()).upper()[:_MAX_PURPOSE_CHARS]


def strip_volatile_parts(purpose: str) -> str:
    """Schritt 2: Datum, Betrag, Monatsname und Zeitraum entfernen.

    Ohne diese Vorreinigung gewinnt "/2026" gegen die Vertragsnummer, weil es
    in jedem Verwendungszweck desselben Jahres vorkommt.
    """
    cleaned = purpose
    for pattern in _VOLATILE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def _clean_key(value: str) -> str:
    """Satzzeichen an den Raendern abschneiden; innen bleibt alles stehen."""
    return re.sub(r"^\W+|\W+$", "", value.strip(), flags=re.UNICODE)[:_MAX_KEY_CHARS]


def extract_mandate_key(purpose: str | None) -> str | None:
    """Bekanntes Schluesselmuster aus GENAU EINEM Verwendungszweck.

    Getrennt von `extract_contract_key`, weil die Buendelung nach Empfaenger
    diesen Schluessel bereits braucht: zwei Vertraege desselben Anbieters unterscheiden
    sich genau hier, und nur so landen sie in getrennten Buendeln.
    """
    normalized = normalize_purpose_for_key(purpose)
    if not normalized:
        return None
    for pattern in _MANDATE_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue
        value = _clean_key(match.group(1))
        if len(value) >= _MIN_MANDATE_KEY_LENGTH and any(ch.isalnum() for ch in value):
            return value
    return None


def _longest_common_substring(left: str, right: str) -> str:
    """Laengste gemeinsame Zeichenkette. O(n*m) - die Aufrufer deckeln beides."""
    if not left or not right:
        return ""
    previous = [0] * (len(right) + 1)
    best_length = 0
    best_end = 0
    for i, left_char in enumerate(left, start=1):
        current = [0] * (len(right) + 1)
        for j, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current[j] = previous[j - 1] + 1
                if current[j] > best_length:
                    best_length = current[j]
                    best_end = i
        previous = current
    return left[best_end - best_length : best_end]


def _has_clean_boundary(residue: str, common: str, *, trailing: bool) -> bool:
    """Steht `common` in `residue` mindestens einmal an einer Wortgrenze?"""
    start = residue.find(common)
    while start >= 0:
        if trailing:
            end = start + len(common)
            if end >= len(residue) or not residue[end].isalnum():
                return True
        elif start == 0 or not residue[start - 1].isalnum():
            return True
        start = residue.find(common, start + 1)
    return False


def _trim_partial_tokens(common: str, residues: Sequence[str]) -> str:
    """Schneidet ein angeschnittenes Wort an den Raendern ab.

    Die laengste gemeinsame Zeichenkette endet gern mitten in einer Zahl: aus
    "LOHN / GEHALT 01 2026" und "LOHN / GEHALT 02 2026" wird "LOHN / GEHALT 0".
    Das erfuellt die Bedingung "mindestens eine Ziffer", ist aber keine
    Vertragsnummer, sondern ein Bruchstueck - und wuerde als `contract_key`
    beim Abgleich eine Identitaet von 1.00 behaupten, die es nicht gibt.
    Abgeschnitten wird nur, wenn die Zeichenkette in KEINEM Verwendungszweck an
    einer Wortgrenze steht.
    """
    result = common
    if not all(_has_clean_boundary(item, result, trailing=True) for item in residues):
        cut = re.search(r"\W\w*$", result)
        result = result[: cut.start()] if cut else ""
    if result and not all(
        _has_clean_boundary(item, result, trailing=False) for item in residues
    ):
        cut = re.search(r"^\w*\W", result)
        result = result[cut.end() :] if cut else ""
    return result.strip()


def extract_contract_key(
    purposes: Sequence[str],
) -> tuple[str, ContractKeySource] | None:
    """Der Vertragsschluessel eines Empfaengerbuendels (Abschnitt 6.1).

    Erst die bekannten Muster (Mandatsreferenz, Kundennummer, ...), danach die
    laengste gemeinsame Zeichenkette der bereinigten Verwendungszwecke. Die
    generische Ableitung verlangt mindestens sechs Zeichen und mindestens eine
    Ziffer - ohne Ziffer waere es nur der Anbietername, und den kennen wir schon.
    """
    normalized = [normalize_purpose_for_key(item) for item in purposes]
    cleaned = [item for item in normalized if item][:_MAX_PURPOSES_PER_PAYEE]
    if not cleaned:
        return None

    for pattern in _MANDATE_PATTERNS:
        hits: Counter[str] = Counter()
        for purpose in cleaned:
            match = pattern.search(purpose)
            if match is None:
                continue
            value = _clean_key(match.group(1))
            if len(value) >= _MIN_MANDATE_KEY_LENGTH and any(ch.isalnum() for ch in value):
                hits[value] += 1
        if hits:
            # Nicht Counter.most_common: bei Gleichstand soll die Reihenfolge
            # nicht an der Einfuegereihenfolge haengen.
            best = sorted(hits.items(), key=lambda item: (-item[1], item[0]))[0][0]
            # Derselbe Schutz wie in `_bucket_by_contract_key`: ein Wert, der bei
            # mehreren Verwendungszwecken nur in EINEM steht (oder in jedem
            # anders lautet), benennt einen Beleg und keinen Vertrag. Als
            # `contract_key` wuerde er im Abgleich eine Identitaet von 1.00
            # behaupten, die es nicht gibt.
            if len(cleaned) > 1 and hits[best] < _MIN_MANDATE_KEY_REPEATS:
                continue
            return best, "mandate"

    if len(cleaned) < 2:
        return None

    residues = [strip_volatile_parts(purpose) for purpose in cleaned]
    common = residues[0]
    for residue in residues[1:]:
        common = _longest_common_substring(common, residue)
        if len(common.strip()) < _MIN_KEY_LENGTH:
            return None

    common = _clean_key(_trim_partial_tokens(common, residues))
    if len(common) < _MIN_KEY_LENGTH or not any(ch.isdigit() for ch in common):
        return None
    return common, "purpose_pattern"


def extract_installment_plan(purpose: str | None) -> tuple[int, int] | None:
    """Ratenkauf aus dem Verwendungszweck: "Rate 3/12" -> (3, 12).

    Rueckgabe ist (bezahlte Raten, Gesamtzahl) - in der Lesereihenfolge des
    Textes, damit die Zuordnung nicht verwechselt werden kann.
    """
    normalized = normalize_purpose_for_key(purpose)
    if not normalized:
        return None
    for pattern in _INSTALLMENT_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue
        paid = int(match.group(1))
        total = int(match.group(2))
        if not _INSTALLMENT_MIN_TOTAL <= total <= _INSTALLMENT_MAX_TOTAL:
            continue
        if not 1 <= paid <= total:
            continue
        return paid, total
    return None


# Der Plan nennt die Funktion an einer Stelle `parse_installments`.
parse_installments = extract_installment_plan


def is_collector_account(candidate_payee_key: str) -> bool:
    """Ist der Empfaenger ein Zahlungsdienstleister (Abschnitt 6.5)?

    Verglichen wird das erste Token des normalisierten Schluessels; eine
    Gleichheitsmenge wuerde an Rechtsformzusaetzen scheitern.
    """
    if not candidate_payee_key:
        return False
    return candidate_payee_key.split(" ", 1)[0] in AGGREGATOR_TOKENS


# ---------------------------------------------------------------------------
# 6.4 Zahlungsart und Kategorieaffinitaet
# ---------------------------------------------------------------------------

# MT940 liefert 'N' + den dreistelligen Geschaeftsvorfallcode aus dem Feld :86:
# (parsers/mt940.py). Lastschriften und Dauerauftraege belegen eine
# wiederkehrende Vereinbarung.
#
# ACHTUNG: nicht zu verwechseln mit dem SWIFT-Buchungsschluessel aus :61:
# (NDDT/NTRF/NMSC). Der stand hier frueher, enthaelt gar keine Ziffern und
# liess `_mt940_score` bei jedem echten Import auf den neutralen Wert fallen -
# die Zahlungsart war als Signal wirkungslos. Er ist heute nur noch der
# Rueckfall fuer Auszuege ohne GVC in :86:.
_MT940_RECURRING_CODES: Final[frozenset[str]] = frozenset(
    {"004", "005", "007", "008", "051", "052", "053", "054", "105", "107", "117",
     "152", "153", "171", "177"}
)
# Kartenumsaetze. Bewusst kurz gehalten: ein falsch zugeordneter Code bestraft
# einen echten Vertrag, ein fehlender laesst ihn nur neutral. Der Sammelcode
# 8xx der Kartenabrechnungen steht deshalb NICHT hier: unter ihm laufen der
# einmalige Einkauf und das per Karte bezahlte Abo gleichermassen, und ein
# ganzes Kreditkartenkonto auf 0,0 zu setzen wuerde jeden Kartenvertrag unter
# die Erkennungsschwelle druecken.
_MT940_CARD_CODES: Final[frozenset[str]] = frozenset({"083", "084", "106"})

# CAMT liefert Domn/Fmly/SubFmly (ISO 20022 Bank Transaction Codes).
_CAMT_RECURRING_PARTS: Final[frozenset[str]] = frozenset(
    {"RDDT", "IDDT", "DDFT", "ESDD", "BBDD", "B2BD", "COR1", "AUTT", "STDO", "UDDT"}
)
_CAMT_CARD_PARTS: Final[frozenset[str]] = frozenset(
    {"CCRD", "DCRD", "POSD", "CWDL", "SMRT", "CDPT"}
)

# PayPal-CSV traegt deutschen Klartext aus der Spalte "Typ"
# (parsers/paypal_csv.py). Fuer alle sonstigen Formate ist derselbe Texttest
# der einzige verwertbare Hinweis.
_TEXT_RECURRING_TOKENS: Final[tuple[str, ...]] = (
    "lastschrift",
    "abbuchung",
    "dauerauftrag",
    "einzug",
    "abonnement",
    "mandat",
    "folgelastschrift",
)
_TEXT_CARD_TOKENS: Final[tuple[str, ...]] = (
    "karte",
    "kreditkarte",
    "girocard",
    "maestro",
    "visa",
    "mastercard",
)


def _mt940_score(code: str) -> float:
    digits = re.sub(r"\D", "", code)[-3:]
    if digits in _MT940_RECURRING_CODES:
        return 1.0
    if digits in _MT940_CARD_CODES:
        return 0.0
    return _NEUTRAL_PAYMENT_METHOD


def _camt_score(code: str) -> float:
    parts = {part.strip().upper() for part in code.split("/") if part.strip()}
    if parts & _CAMT_RECURRING_PARTS:
        return 1.0
    if parts & _CAMT_CARD_PARTS:
        return 0.0
    return _NEUTRAL_PAYMENT_METHOD


def _text_score(code: str) -> float:
    lowered = code.casefold()
    if any(token in lowered for token in _TEXT_RECURRING_TOKENS):
        return 1.0
    if any(token in lowered for token in _TEXT_CARD_TOKENS):
        return 0.0
    return _NEUTRAL_PAYMENT_METHOD


def payment_method_value(import_format: str | None, code: str | None) -> float:
    """Zahlungsart-Index EINER Buchung.

    `bank_transaction_code` bedeutet je Quelle etwas anderes: MT940 liefert
    'N024', PayPal deutschen Klartext, CAMT 'DOMN/FMLY/SUBFMLY', CSV und XLSX
    gar nichts. Unbekannt ist deshalb ausdruecklich 0.5 und nicht 0.0 - ein
    Default von 0.0 wuerde jeden CSV-Import um 0,15 Punkte bestrafen und
    korrekte Vertraege unter die Schwelle schieben.
    """
    if not code or not code.strip():
        return _NEUTRAL_PAYMENT_METHOD
    value = code.strip()
    source = (import_format or "").strip().casefold()
    if source == "mt940":
        return _mt940_score(value)
    if source == "camt":
        return _camt_score(value)
    if source == "paypal_csv":
        return _text_score(value)
    # Unbekanntes Format: erst das CAMT-Muster versuchen, dann den Texttest.
    if "/" in value:
        camt = _camt_score(value)
        if camt != _NEUTRAL_PAYMENT_METHOD:
            return camt
    return _text_score(value)


def payment_method_score(codes: Sequence[tuple[str | None, str | None]]) -> float:
    """Mittelwert des Zahlungsart-Index ueber alle Buchungen eines Kandidaten."""
    if not codes:
        return _NEUTRAL_PAYMENT_METHOD
    values = [payment_method_value(fmt, code) for fmt, code in codes]
    return sum(values) / len(values)


def is_direct_debit(rows: Sequence[DetectionTx]) -> bool:
    """Weist mindestens eine Buchung eine Lastschrift/einen Dauerauftrag aus?"""
    return any(
        payment_method_value(row.import_format, row.bank_transaction_code) >= 1.0
        for row in rows
    )


def category_affinity(
    semantic_key: str | None, group_semantic_key: str | None
) -> float:
    """Wie vertragstypisch ist diese Kategorie (Abschnitt 6.4)?

    `semantic_key` ist nur bei Kategorien aus dem Standardpaket gesetzt. Auf
    Alt-Haushalten (origin='legacy') liefert die Affinitaet durchgaengig 0.5;
    die Schwelle muss dort allein aus Datum, Betrag, Vorkommen und Zahlungsart
    erreicht werden.
    """
    if semantic_key and semantic_key in CATEGORY_AFFINITY:
        return CATEGORY_AFFINITY[semantic_key]
    if group_semantic_key and group_semantic_key in GROUP_AFFINITY:
        return GROUP_AFFINITY[group_semantic_key]
    return _NEUTRAL_AFFINITY


# ---------------------------------------------------------------------------
# 6.3 Rhythmuserkennung
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FrequencyFit:
    direct_ratio: float
    match_ratio: float
    mean_error: float


def _fit_frequency(gaps: Sequence[int], spec: FrequencySpec) -> _FrequencyFit:
    """Bewertet EINE Frequenz gegen die realen Abstaende.

    `direct` zaehlt nur Abstaende, die ohne ausgelassene Periode passen. Genau
    diese Trennung fehlte bisher: "woechentlich mit vier ausgelassenen Wochen"
    deckt 22 bis 34 Tage ab, "monatlich" nur 26,4 bis 34,4 - und lag deshalb
    bei jedem verschobenen Zahltag hinten.
    """
    direct = 0
    matched = 0
    normalized_error = 0.0
    for gap in gaps:
        periods = max(1, round(gap / spec.period_days))
        if periods > _MAX_SKIPPED_PERIODS:
            continue
        error = abs(gap - periods * spec.period_days)
        allowed_error = spec.tolerance_days * min(periods, 3)
        if error > allowed_error:
            continue
        matched += 1
        normalized_error += error / allowed_error
        if periods == 1:
            direct += 1
    total = len(gaps)
    return _FrequencyFit(
        direct_ratio=direct / total if total else 0.0,
        match_ratio=matched / total if total else 0.0,
        mean_error=normalized_error / matched if matched else 1.0,
    )


def infer_frequency(
    booked_dates: Sequence[date],
    *,
    has_contract_key: bool = False,
    direct_debit: bool = False,
) -> DetectionFrequency | None:
    """Leitet den Rhythmus aus den realen Abstaenden ab (Abschnitt 6.3)."""
    dates = sorted(set(booked_dates))
    if len(dates) < 2:
        return None
    gaps = [(right - left).days for left, right in zip(dates, dates[1:], strict=False)]
    if not gaps or any(gap <= 0 for gap in gaps):
        return None

    fits = {
        frequency: _fit_frequency(gaps, spec)
        for frequency, spec in FREQUENCY_SPECS.items()
    }

    # Die Weekly-Sperre weiter unten wirkt nur gegen eine monatliche Erklaerung,
    # die selbst als Kandidat durchgeht. Ein absolutes Veto haette eine
    # Wochenreihe mit EINER Pause (Ferien, Betriebsurlaub - ein Abstand von 27
    # bis 34 Tagen) ersatzlos gestrichen: 'monthly' scheitert dort an der
    # eigenen Trefferquote, und uebrig bliebe gar keine Frequenz mehr.
    monthly_spec = FREQUENCY_SPECS["monthly"]
    monthly_min_dates = (
        monthly_spec.min_occurrences if has_contract_key else monthly_spec.min_dates_without_key
    )
    monthly_direct_ratio = (
        fits["monthly"].direct_ratio
        if len(dates) >= monthly_min_dates and fits["monthly"].match_ratio >= MATCH_RATIO_MIN
        else 0.0
    )

    best: tuple[tuple[float, float, float], DetectionFrequency] | None = None
    for frequency, spec in FREQUENCY_SPECS.items():
        fit = fits[frequency]
        min_dates = spec.min_occurrences if has_contract_key else spec.min_dates_without_key
        if len(dates) < min_dates:
            continue
        if fit.match_ratio < MATCH_RATIO_MIN:
            continue
        if frequency == "weekly":
            # Unabhaengig vom Vertragsschluessel: ohne diese Sperre waeren die
            # vier Zufallsdaten aus dem Bestandstest als "woechentlich"
            # zugelassen, weil zwei von drei Abstaenden ueber ausgelassene
            # Wochen erklaerbar sind.
            if len(dates) < _WEEKLY_MIN_DATES:
                continue
            if monthly_direct_ratio > 0.0:
                continue
        if (
            frequency in _LONG_PERIOD_FREQUENCIES
            and len(dates) < _LONG_PERIOD_MIN_DATES_WITHOUT_PROOF
            and not (has_contract_key or direct_debit)
        ):
            continue

        rank = (fit.direct_ratio, fit.match_ratio, -fit.mean_error)
        if best is None or rank > best[0]:
            best = (rank, frequency)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# 6.2 Preisstufen statt getrennter Cluster
# ---------------------------------------------------------------------------


def _sorted_desc(rows: Iterable[DetectionTx]) -> list[DetectionTx]:
    return sorted(
        rows,
        key=lambda row: (row.booked_date, str(row.transaction_id)),
        reverse=True,
    )


def cluster_by_amount(rows: Sequence[DetectionTx]) -> list[list[DetectionTx]]:
    """Betragsniveaus eines Buendels, jeweils neueste Buchung zuerst.

    Anker ist immer die juengste noch freie Buchung. Dadurch wird der letzte
    echte Betrag zum Sollbetrag und nicht ein historisches Mittel.
    """
    remaining = _sorted_desc(rows)
    levels: list[list[DetectionTx]] = []
    while remaining:
        anchor = remaining[0]
        tolerance = amount_tolerance_minor(anchor.amount_minor)
        level = [
            row
            for row in remaining
            if abs(row.amount_minor - anchor.amount_minor) <= tolerance
        ]
        taken = {row.transaction_id for row in level}
        remaining = [row for row in remaining if row.transaction_id not in taken]
        levels.append(level)
    return levels


# Zwei Vertraege desselben Anbieters koennen betraglich naeher beieinander
# liegen als `amount_tolerance_minor` (zwei Mobilfunkvertraege mit 39,95 und 41,95: 2,00 EUR
# Unterschied bei 2,10 EUR Toleranz). Sie landen dann in EINEM Betragsniveau,
# und `_is_sequential` eine Ebene hoeher bekommt sie nie zu sehen - der
# Empfaenger erscheint als EIN Vertrag mit doppelter Kadenz.
_PARALLEL_MIN_REPEATS: Final = 2


def split_parallel_amounts(level: Sequence[DetectionTx]) -> list[list[DetectionTx]]:
    """Zerlegt EIN Betragsniveau in parallel laufende Vertraege.

    Getrennt wird nur unter drei Bedingungen zugleich: mindestens zwei EXAKTE
    Betraege kommen mehrfach vor, ihre Zeitraeume ueberlappen sich, und jeder
    Teil hat fuer sich einen Rhythmus, den das ungetrennte Niveau nicht hat.

    Die dritte Bedingung ist die entscheidende: ein schwankender Betrag (Strom,
    Grundsteuer) behaelt seinen Rhythmus auch ungetrennt und bleibt deshalb
    zusammen, waehrend zwei parallele Monatsvertraege ungetrennt als
    "woechentlich" gelesen werden - genau diese Verschiebung ist der Beleg.
    """
    by_amount: dict[int, list[DetectionTx]] = defaultdict(list)
    for row in level:
        by_amount[row.amount_minor].append(row)
    repeated = sorted(
        amount for amount, rows in by_amount.items() if len(rows) >= _PARALLEL_MIN_REPEATS
    )
    if len(repeated) < 2:
        return [list(level)]

    spans = {
        amount: (
            min(row.booked_date for row in by_amount[amount]),
            max(row.booked_date for row in by_amount[amount]),
        )
        for amount in repeated
    }
    if not any(
        spans[left][0] <= spans[right][1] and spans[right][0] <= spans[left][1]
        for index, left in enumerate(repeated)
        for right in repeated[index + 1 :]
    ):
        # Nacheinander statt nebeneinander: das ist eine Preisaenderung und
        # bleibt als Betragstreppe desselben Vertrags zusammen.
        return [list(level)]

    # Einzelwerte (eine einmalige Nachzahlung, ein Rundungsausreisser) gehen an
    # den betraglich naechsten wiederkehrenden Betrag; ein eigener Vorschlag
    # entstuende daraus ohnehin nie.
    groups: dict[int, list[DetectionTx]] = {amount: [] for amount in repeated}
    for row in level:
        nearest = min(repeated, key=lambda amount: (abs(row.amount_minor - amount), amount))
        groups[nearest].append(row)
    parts = [groups[amount] for amount in repeated]

    part_frequencies = [
        infer_frequency([row.booked_date for row in part]) for part in parts
    ]
    if any(frequency is None for frequency in part_frequencies):
        return [list(level)]
    whole_frequency = infer_frequency([row.booked_date for row in level])
    if all(frequency == whole_frequency for frequency in part_frequencies):
        return [list(level)]
    return parts


def _is_sequential(
    earlier: Sequence[DetectionTx], later: Sequence[DetectionTx], period_days: float
) -> bool:
    """Endet `earlier`, bevor `later` beginnt (Abschnitt 6.2)?

    Ist der Abstand groesser als eine halbe Periode, sind es Preisstufen
    DESSELBEN Vertrags. Ueberlappen die Zeitraeume, laufen zwei Vertraege
    parallel - Musterfunk 39,95 und 99,90 sind zwei Vertraege, Musterfunk 49,95
    und danach 39,95 ist eine Preisaenderung.
    """
    earlier_end = max(row.booked_date for row in earlier)
    later_start = min(row.booked_date for row in later)
    return earlier_end < later_start - timedelta(days=period_days / 2.0)


def split_amount_steps(
    rows: Sequence[DetectionTx], frequency: DetectionFrequency
) -> list[list[DetectionTx]]:
    """Zerlegt ein Buendel in parallele Vertraege.

    Rueckgabe ist eine Liste je Vertrag; mehr als eine Liste heisst, dass der
    Empfaenger mehrere Vertraege gleichzeitig hat. Innerhalb einer Liste sind
    die Betragsniveaus Preisstufen und bleiben zusammen.
    """
    levels = [
        part for level in cluster_by_amount(rows) for part in split_parallel_amounts(level)
    ]
    if len(levels) <= 1:
        return [list(rows)] if rows else []

    period_days = FREQUENCY_SPECS[frequency].period_days
    ordered = sorted(levels, key=lambda level: min(row.booked_date for row in level))

    groups: list[list[list[DetectionTx]]] = []
    for level in ordered:
        for group in groups:
            if all(_is_sequential(existing, level, period_days) for existing in group):
                group.append(level)
                break
        else:
            groups.append([level])

    return [[row for level in group for row in level] for group in groups]


def amount_steps_of(rows: Sequence[DetectionTx]) -> tuple[AmountStep, ...]:
    """Preisstufen eines einzelnen Vertrags, aelteste zuerst."""
    levels = cluster_by_amount(rows)
    steps = [
        AmountStep(
            # Der Anker ist die juengste Buchung der Stufe; ihr Betrag ist der
            # Sollbetrag der Stufe, nicht der Durchschnitt.
            amount_minor=level[0].amount_minor,
            first_date=min(row.booked_date for row in level),
            last_date=max(row.booked_date for row in level),
            transactions=tuple(level),
        )
        for level in levels
    ]
    return tuple(sorted(steps, key=lambda step: (step.first_date, step.last_date)))


# ---------------------------------------------------------------------------
# 6.4 Vertrags-Score
# ---------------------------------------------------------------------------


def _payday_offsets(dates: Sequence[date], frequency: DetectionFrequency) -> list[float]:
    """Abweichung des Zahltags vom Median, zyklisch gerechnet.

    Ohne Zyklus waeren der 31. und der 1. dreissig Tage auseinander, obwohl sie
    derselbe Zahltag in benachbarten Monaten sind.
    """
    if frequency == "weekly":
        cycle = 7.0
        values = [float(item.weekday()) for item in dates]
    else:
        cycle = FREQUENCY_SPECS["monthly"].period_days
        values = [float(item.day) for item in dates]
    if not values:
        return []
    center = statistics.median(values)
    half = cycle / 2.0
    return [((value - center + half) % cycle) - half for value in values]


def score_candidate(
    rows: Sequence[DetectionTx],
    frequency: DetectionFrequency,
    *,
    affinity: float,
) -> ScoreBreakdown:
    """Der Vertrags-Score aus Abschnitt 6.4.

    Getrennt vom Rhythmus-Score, weil er eine andere Frage beantwortet: nicht
    "wie regelmaessig ist das?", sondern "ist das ueberhaupt ein Vertrag?".
    """
    dates = [row.booked_date for row in rows]
    amounts = [abs(row.amount_minor) for row in rows]

    offsets = _payday_offsets(dates, frequency)
    day_deviation = statistics.pstdev(offsets) if len(offsets) > 1 else 0.0
    date_stability = 1.0 - min(1.0, day_deviation / _DATE_STABILITY_REFERENCE_DAYS)

    mean_amount = statistics.fmean(amounts) if amounts else 0.0
    if len(amounts) > 1 and mean_amount > 0:
        variation = statistics.pstdev(amounts) / mean_amount
    else:
        variation = 0.0
    amount_stability = 1.0 - min(1.0, variation * _AMOUNT_STABILITY_FACTOR)

    occurrence_count = min(len(rows), _OCCURRENCE_SATURATION) / _OCCURRENCE_SATURATION
    method = payment_method_score(
        [(row.import_format, row.bank_transaction_code) for row in rows]
    )

    total = (
        _WEIGHT_DATE_STABILITY * date_stability
        + _WEIGHT_AMOUNT_STABILITY * amount_stability
        + _WEIGHT_OCCURRENCES * occurrence_count
        + _WEIGHT_CATEGORY * affinity
        + _WEIGHT_PAYMENT_METHOD * method
    )
    return ScoreBreakdown(
        date_stability=round(date_stability, 4),
        amount_stability=round(amount_stability, 4),
        occurrence_count=round(occurrence_count, 4),
        category_affinity=round(affinity, 4),
        payment_method=round(method, 4),
        total=round(total, 2),
    )


def confidence_for(score: ScoreBreakdown) -> Confidence | None:
    """`None` heisst: erscheint gar nicht."""
    if score.total >= SUGGEST_MIN_SCORE:
        return "suggested"
    if score.total >= UNCERTAIN_MIN_SCORE:
        return "uncertain"
    return None


# ---------------------------------------------------------------------------
# Kern: Buendelung zu Kandidaten
# ---------------------------------------------------------------------------


def _bucket_by_contract_key(
    rows: Sequence[DetectionTx],
) -> list[tuple[str | None, list[DetectionTx]]]:
    """Zerlegt die Buchungen eines Empfaengers nach Mandatsreferenz.

    Das ist der eigentliche Fix fuer "Musterfunk steht dreimal in der Liste": zwei
    Vertraege desselben Anbieters unterscheiden sich in der Mandatsreferenz und
    gehoeren deshalb in getrennte Buendel, bevor ueberhaupt ueber Betraege
    geredet wird.
    """
    keyed: dict[str, list[DetectionTx]] = defaultdict(list)
    unkeyed: list[DetectionTx] = []
    for row in rows:
        mandate = extract_mandate_key(row.purpose_raw)
        if mandate is None:
            unkeyed.append(row)
        else:
            keyed[mandate].append(row)

    # Zerfaellt der Empfaenger in lauter Einzelbuchungen, war die Referenz in
    # Wahrheit belegbezogen und nicht vertragsbezogen. Dann ist die Buendelung
    # schaedlich - sie wuerde jeden Rhythmus zerstoeren.
    if keyed and all(len(group) < 2 for group in keyed.values()):
        return [(None, list(rows))]

    # Genau EINE Mandatsreferenz und daneben Buchungen ohne: das ist der
    # Formatwechsel, nicht der zweite Vertrag. MT940 traegt den vollstaendigen
    # :86:-Rahmen samt MREF, CAMT liefert nur RmtInf/Ustrd - derselbe Anbieter
    # steht dadurch in der ersten Jahreshaelfte mit und in der zweiten ohne
    # Schluessel da. Getrennt ergaebe das zwei Vorschlaege fuer einen Vertrag,
    # und eine Preisaenderung dazwischen wuerde als zweiter Vertrag angelegt.
    # Ab zwei verschiedenen Referenzen bleibt es bei der Trennung: dann ist
    # nicht mehr entscheidbar, zu welchem der Vertraege die schluessellosen
    # Buchungen gehoeren.
    if len(keyed) == 1 and unkeyed:
        only_key = next(iter(keyed))
        keyed[only_key].extend(unkeyed)
        unkeyed = []

    buckets: list[tuple[str | None, list[DetectionTx]]] = [
        (key, group) for key, group in sorted(keyed.items())
    ]
    if unkeyed:
        buckets.append((None, unkeyed))
    return buckets


def _pick_category(
    rows: Sequence[DetectionTx], anchor: DetectionTx
) -> UUID | None:
    """Die Kategorie des Kandidaten.

    Eine vom Nutzer bestaetigte Zuordnung im Split ist der staerkere Beleg und
    geht dem letzten KI-Entscheid vor - die Altfassung las ausschliesslich
    `classification_decisions`.
    """
    if anchor.category_id is not None:
        return anchor.category_id
    split_counts = Counter(row.category_id for row in rows if row.category_id is not None)
    if split_counts:
        return sorted(split_counts.items(), key=lambda item: (-item[1], str(item[0])))[0][0]
    if anchor.ai_category_id is not None:
        return anchor.ai_category_id
    ai_counts = Counter(row.ai_category_id for row in rows if row.ai_category_id is not None)
    if ai_counts:
        return sorted(ai_counts.items(), key=lambda item: (-item[1], str(item[0])))[0][0]
    return None


def _installment_plan(rows: Sequence[DetectionTx]) -> tuple[int, int] | None:
    """Juengste erkennbare Rate eines Ratenkaufs."""
    best: tuple[int, int] | None = None
    for row in _sorted_desc(rows):
        plan = extract_installment_plan(row.purpose_raw)
        if plan is None:
            continue
        if best is None or plan[0] > best[0]:
            best = plan
    return best


def _build_candidate(
    rows: Sequence[DetectionTx],
    *,
    payee_bucket_key: str,
    contract_key: str | None,
    contract_key_source: ContractKeySource | None,
    affinity_by_category: Mapping[UUID, float],
    contract_type_by_category: Mapping[UUID, ContractType],
) -> ContractCandidate | None:
    frequency = infer_frequency(
        [row.booked_date for row in rows],
        has_contract_key=contract_key is not None,
        direct_debit=is_direct_debit(rows),
    )
    if frequency is None:
        return None

    steps = amount_steps_of(rows)
    if not steps:
        return None
    # `split_amount_steps` nimmt ein Betragsniveau nur in eine Gruppe auf, wenn
    # es NACH allen bereits enthaltenen beginnt. Die Stufen einer Gruppe bilden
    # deshalb eine lueckenlose Zeitkette: die letzte ist die juengste, und ihre
    # erste Buchung ist die juengste des ganzen Kandidaten.
    current = steps[-1]
    anchor = current.transactions[0]

    direction: ContractDirection = "expense" if anchor.amount_minor < 0 else "income"
    category_id = _pick_category(rows, anchor)
    affinity = (
        affinity_by_category.get(category_id, _NEUTRAL_AFFINITY)
        if category_id is not None
        else _NEUTRAL_AFFINITY
    )
    score = score_candidate(rows, frequency, affinity=affinity)
    confidence = confidence_for(score)
    if confidence is None:
        return None

    # Einnahmen sind erst ab drei Vorkommen und stabilem Zahltag ein Vertrag.
    # Der Vorzeichenfilter der Altfassung machte sie strukturell unsichtbar;
    # ohne Ersatzbedingung wuerde jetzt jede Erstattung zu einem Vorschlag.
    if direction == "income" and (
        len(rows) < _INCOME_MIN_OCCURRENCES
        or score.date_stability <= _INCOME_MIN_DATE_STABILITY
    ):
        return None

    plan = _installment_plan(rows)
    contract_type: ContractType = (
        contract_type_by_category.get(category_id, "other")
        if category_id is not None
        else "other"
    )
    if direction == "income":
        contract_type = "income"
    installments_paid: int | None = None
    installments_total: int | None = None
    if plan is not None:
        installments_paid, installments_total = plan
        contract_type = "installment"

    account_ids = {row.account_id for row in rows}
    payee_ids = {row.payee_id for row in rows if row.payee_id is not None}
    display_name = anchor.payee_label
    if contract_key and is_collector_account(payee_bucket_key):
        # Bei einem Sammelkonto ist der Schluessel der einzige Hinweis darauf,
        # wessen Vertrag hier abgebucht wird.
        display_name = f"{anchor.payee_label} · {contract_key}"

    ordered = tuple(_sorted_desc(rows))
    return ContractCandidate(
        source_transaction_id=anchor.transaction_id,
        account_id=next(iter(account_ids)) if len(account_ids) == 1 else None,
        payee_id=next(iter(payee_ids)) if len(payee_ids) == 1 else None,
        payee_key=payee_bucket_key,
        payee_label=anchor.payee_label,
        display_name=display_name[:160],
        contract_key=contract_key,
        contract_key_source=contract_key_source,
        direction=direction,
        amount_minor=abs(anchor.amount_minor),
        amount_tolerance_minor=max(
            abs(row.amount_minor - anchor.amount_minor) for row in current.transactions
        ),
        frequency=frequency,
        first_date=min(row.booked_date for row in rows),
        last_date=anchor.booked_date,
        occurrences_count=len(rows),
        amount_steps=steps,
        suggested_category_id=category_id,
        contract_type=contract_type,
        installments_total=installments_total,
        installments_paid=installments_paid,
        score=score,
        confidence=confidence,
        transactions=ordered,
    )


@dataclass(frozen=True)
class _Bucket:
    """Ein Empfaenger-/Schluesselbuendel vor der Betragsanalyse."""

    payee_key: str
    rows: tuple[DetectionTx, ...]
    contract_key: str | None
    contract_key_source: ContractKeySource | None


def _collect_buckets(rows: Sequence[DetectionTx]) -> list[_Bucket]:
    """Buendelt nach Empfaenger UND Richtung.

    Eine Gutschrift und eine Abbuchung desselben Gegenuebers sind nie derselbe
    Vertrag - `contracts.direction` trennt sie ohnehin. Im selben Buendel
    zerstoert die Gutschrift aber den Rhythmus: `infer_frequency` sieht ihr
    Datum mit, und `split_amount_steps` macht aus ihr ein eigenes Betragsniveau.

    Der Anlassfall: eine einzelne grosse Gutschrift loeschte eine Reihe
    sauberer kleiner Monatsraten an denselben Empfaenger vollstaendig - aus
    sieben Buchungen wurde keine einzige Zahlungsreihe. Mit der Trennung
    entsteht im selben Bestand eine Reihe mehr, ohne dass eine bestehende
    verloren geht.
    """
    by_payee: dict[tuple[str, str], list[DetectionTx]] = defaultdict(list)
    for row in rows:
        if row.payee_key:
            direction = "expense" if row.amount_minor < 0 else "income"
            by_payee[(row.payee_key, direction)].append(row)

    buckets: list[_Bucket] = []
    for (bucket_key, _direction), payee_rows in sorted(by_payee.items()):
        for mandate_key, bucket in _bucket_by_contract_key(payee_rows):
            if mandate_key is not None:
                contract_key: str | None = mandate_key
                key_source: ContractKeySource | None = "mandate"
            else:
                derived = extract_contract_key(
                    [row.purpose_raw or "" for row in bucket][:_MAX_PURPOSES_PER_PAYEE]
                )
                contract_key = derived[0] if derived else None
                key_source = derived[1] if derived else None
            buckets.append(
                _Bucket(
                    payee_key=bucket_key,
                    rows=tuple(bucket),
                    contract_key=contract_key,
                    contract_key_source=key_source,
                )
            )
    return buckets


def _is_payee_name_variant(payee_keys: Iterable[str]) -> bool:
    """Sind das Schreibweisen DESSELBEN Empfaengers?

    Verglichen wird ohne Leerzeichen und ueber die Praefixbeziehung: die
    KI-normalisierte `payee_name` kuerzt denselben Anbieter unterschiedlich weit
    ab ("Allianz Lebensvers", "Allianz Lebensversic", "Allianz
    Lebensversicherung") und schreibt ihn mal mit, mal ohne Leerzeichen
    ("Max Energy GmbH", "MaxEnergy GmbH"). Zwei voellig verschiedene Anbieter
    stehen dagegen in keiner solchen Beziehung.
    """
    compact = sorted({key.replace(" ", "") for key in payee_keys}, key=len)
    if len(compact) <= 1:
        return True
    longest = compact[-1]
    return all(longest.startswith(item) for item in compact[:-1])


def _drop_ambiguous_keys(buckets: Sequence[_Bucket]) -> list[_Bucket]:
    """Verwirft Schluessel, die mehrere verschiedene Empfaenger teilen.

    PayPal fuehrt EIN Mandat fuer das ganze Konto. Seine Referenz steht deshalb
    unter mehreren verschiedenen Haendlern zugleich - sie
    benennt das Zahlungskonto und keinen Vertrag. Folgenreich ist das, weil die
    Pruefung "schon bestaetigt" bei Schluesselgleichheit sofort abbricht: nach
    dem ersten bestaetigten Vertrag verschwaenden alle uebrigen aus der Inbox.

    Nicht betroffen ist der haeufigere Fall, dass derselbe Empfaenger unter
    mehreren Schreibweisen auftaucht. Dort ist der gemeinsame Schluessel das
    Einzige, was die Schreibweisen ueberhaupt noch verbindet, und muss bleiben.
    """
    payees_by_key: dict[str, set[str]] = defaultdict(set)
    for bucket in buckets:
        if bucket.contract_key:
            payees_by_key[bucket.contract_key].add(bucket.payee_key)

    ambiguous = {
        key
        for key, payees in payees_by_key.items()
        if len(payees) > 1 and not _is_payee_name_variant(payees)
    }
    if not ambiguous:
        return list(buckets)
    return [
        (
            bucket
            if bucket.contract_key not in ambiguous
            else _Bucket(bucket.payee_key, bucket.rows, None, None)
        )
        for bucket in buckets
    ]


def cluster_candidates(
    rows: Sequence[DetectionTx],
    *,
    affinity_by_category: Mapping[UUID, float] | None = None,
    contract_type_by_category: Mapping[UUID, ContractType] | None = None,
) -> list[ContractCandidate]:
    """Der testbare Kern der Erkennung - ohne Datenbank, ohne Session."""
    affinities = affinity_by_category or {}
    types = contract_type_by_category or {}

    candidates: list[ContractCandidate] = []
    for bucket in _drop_ambiguous_keys(_collect_buckets(rows)):
        # Abschnitt 6.5: ein Zahlungsdienstleister ohne Vertragsschluessel sagt
        # nichts darueber aus, wessen Vertrag bezahlt wurde.
        if bucket.contract_key is None and is_collector_account(bucket.payee_key):
            continue

        provisional = infer_frequency(
            [row.booked_date for row in bucket.rows],
            has_contract_key=bucket.contract_key is not None,
            direct_debit=is_direct_debit(bucket.rows),
        )
        # Ohne erkennbaren Gesamtrhythmus fehlt die Periodenlaenge fuer die
        # Trennung "Preisstufe oder paralleler Vertrag". Monatlich ist dann die
        # vorsichtige Annahme: die kurze Periode trennt eher zu viel als zu
        # wenig, und zwei getrennte Vorschlaege sind harmloser als ein
        # erfundener gemeinsamer Vertrag.
        reference: DetectionFrequency = provisional or "monthly"

        built: list[ContractCandidate] = []
        for group in split_amount_steps(bucket.rows, reference):
            candidate = _build_candidate(
                group,
                payee_bucket_key=bucket.payee_key,
                contract_key=bucket.contract_key,
                contract_key_source=bucket.contract_key_source,
                affinity_by_category=affinities,
                contract_type_by_category=types,
            )
            if candidate is not None:
                built.append(candidate)

        # Schlaegt dasselbe Buendel MEHRERE Vertraege vor, unterscheidet der
        # Schluessel sie nicht: er benennt dann den Anbieter, nicht den Vertrag.
        # Ihn trotzdem an beide zu haengen waere Fallstrick 5.11 mit
        # umgekehrtem Vorzeichen - das Bestaetigen des einen der beiden
        # Vertraege wuerde den zweiten aus der Inbox nehmen.
        #
        # Bewusst an den fertigen Kandidaten gemessen und nicht an den
        # Betragsniveaus: eine einzelne Ausreisserzahlung bildet zwar ein
        # eigenes Niveau, wird aber mangels Rhythmus nie ein Vorschlag und darf
        # dem echten Vertrag nicht seinen Schluessel nehmen.
        if len(built) > 1:
            if is_collector_account(bucket.payee_key):
                continue
            built = [
                replace(
                    candidate,
                    contract_key=None,
                    contract_key_source=None,
                    display_name=candidate.payee_label[:160],
                )
                for candidate in built
            ]
        candidates.extend(built)

    return candidates


# ---------------------------------------------------------------------------
# Lesen aus der Datenbank
# ---------------------------------------------------------------------------

# Kein `left join lateral` je Zeile mehr, sondern zwei vorverdichtete
# Unterabfragen: die Kandidatenabfrage laeuft bei JEDEM Inbox-Aufruf und wurde
# durch die Zwecknormalisierung zusaetzlich belastet.
_DETECTION_SQL: Final = """
select t.id, t.account_id, t.payee_id, t.amount_minor, t.booked_date,
       t.purpose_raw, t.bank_transaction_code,
       coalesce(t.payee_raw, t.payee_label) as raw_payee_label,
       coalesce(t.purpose_raw, t.note) as note_text,
       a.name as account_name,
       ir.detected_format as import_format,
       coalesce(nullif(btrim(ai.payee_name), ''), p.name, t.payee_label) as canonical_payee,
       ai.category_id as ai_category_id,
       sp.category_id as split_category_id,
       coalesce(sp.category_source, 'unset') as category_source
from transactions t
join accounts a on a.id = t.account_id and a.household_id = t.household_id
left join payees p on p.id = t.payee_id and p.household_id = t.household_id
left join import_runs ir on ir.id = t.import_run_id
left join (
  select distinct on (cd.transaction_id)
         cd.transaction_id, cd.payee_name, cd.category_id
  from classification_decisions cd
  join categories c on c.id = cd.category_id
    and c.household_id = cd.household_id and not c.is_archived
  join category_groups cg on cg.id = c.group_id and not cg.is_archived
  where cd.household_id = :household_id
  order by cd.transaction_id, cd.created_at desc, cd.id desc
) ai on ai.transaction_id = t.id
left join (
  select distinct on (s.transaction_id)
         s.transaction_id, s.category_id, s.category_source
  from transaction_splits s
  where s.household_id = :household_id and s.category_id is not null
  order by s.transaction_id, s.created_at asc, s.id asc
) sp on sp.transaction_id = t.id
where t.household_id = :household_id and not t.is_archived and not t.is_transfer
  and t.amount_minor <> 0
  and t.booked_date >= :from_date
  and t.payee_label is not null and btrim(t.payee_label) <> ''
order by t.booked_date desc, t.id desc
"""

_CATEGORY_SEMANTICS_SQL: Final = """
select c.id, c.semantic_key, cg.semantic_key as group_semantic_key
from categories c
join category_groups cg on cg.id = c.group_id and cg.household_id = c.household_id
where c.household_id = :household_id and not c.is_archived
"""

_EXISTING_CONTRACTS_SQL: Final = """
select c.payee_id, coalesce(p.name, c.provider_label, c.name) as payee_name,
       c.contract_key, c.amount_minor, c.direction, c.tolerance_amount_minor,
       c.frequency, c.status
from contracts c
left join payees p on p.id = c.payee_id and p.household_id = c.household_id
where c.household_id = :household_id
  and c.status in ('active', 'paused', 'cancelled', 'ended', 'not_a_contract')
"""

# Zwei Jahre reichen fuer jeden Rhythmus bis "jaehrlich" und halten die Abfrage
# auf transactions_household_date_idx.
_DEFAULT_MONTHS_BACK: Final = 24


def _months_ago(months: int) -> date:
    today = date.today()
    total = today.month - 1 - months
    year = today.year + total // 12
    month = total % 12 + 1
    return date(year, month, 1)


async def load_detection_transactions(
    session: AsyncSession, household_id: UUID, *, months_back: int = _DEFAULT_MONTHS_BACK
) -> list[DetectionTx]:
    """Alle Buchungen, die als Vertragszahlung in Frage kommen.

    Der Vorzeichenfilter der Altfassung (`amount_minor < 0`) faellt hier weg:
    ein Gehalt ist ein Vertrag mit direction='income'. Aussortiert werden
    Einnahmen erst nach dem Vertrags-Score.
    """
    result = await session.execute(
        text(_DETECTION_SQL),
        {"household_id": str(household_id), "from_date": _months_ago(months_back)},
    )
    rows: list[DetectionTx] = []
    for row in result:
        label = str(row.canonical_payee).strip()[:160]
        purpose_raw = cast(str | None, row.purpose_raw)
        rows.append(
            DetectionTx(
                transaction_id=UUID(str(row.id)),
                account_id=UUID(str(row.account_id)),
                payee_id=UUID(str(row.payee_id)) if row.payee_id is not None else None,
                payee_label=label,
                payee_key=payee_key(label),
                amount_minor=int(row.amount_minor),
                booked_date=cast(date, row.booked_date),
                purpose_raw=purpose_raw,
                purpose_key=normalize_purpose(purpose_raw),
                bank_transaction_code=cast(str | None, row.bank_transaction_code),
                import_format=cast(str | None, row.import_format),
                category_id=(
                    UUID(str(row.split_category_id))
                    if row.split_category_id is not None
                    else None
                ),
                category_source=str(row.category_source),
                ai_category_id=(
                    UUID(str(row.ai_category_id)) if row.ai_category_id is not None else None
                ),
                account_name=str(row.account_name),
                raw_payee_label=cast(str | None, row.raw_payee_label),
                note=cast(str | None, row.note_text),
            )
        )
    return rows


async def _load_category_semantics(
    session: AsyncSession, household_id: UUID
) -> dict[UUID, tuple[str | None, str | None]]:
    result = await session.execute(
        text(_CATEGORY_SEMANTICS_SQL), {"household_id": str(household_id)}
    )
    return {
        UUID(str(row.id)): (
            cast(str | None, row.semantic_key),
            cast(str | None, row.group_semantic_key),
        )
        for row in result
    }


def _affinities_from_semantics(
    semantics: Mapping[UUID, tuple[str | None, str | None]],
) -> dict[UUID, float]:
    return {
        category_id: category_affinity(semantic_key, group_key)
        for category_id, (semantic_key, group_key) in semantics.items()
    }


async def load_category_affinity(
    session: AsyncSession, household_id: UUID
) -> dict[UUID, float]:
    return _affinities_from_semantics(
        await _load_category_semantics(session, household_id)
    )


def _contract_types_from_semantics(
    semantics: Mapping[UUID, tuple[str | None, str | None]],
) -> dict[UUID, ContractType]:
    types: dict[UUID, ContractType] = {}
    for category_id, (semantic_key, _group_key) in semantics.items():
        if semantic_key and semantic_key in _CONTRACT_TYPE_BY_SEMANTIC_KEY:
            types[category_id] = _CONTRACT_TYPE_BY_SEMANTIC_KEY[semantic_key]
    return types


def _normalized_key(value: str | None) -> str:
    """Vergleichsform eines Vertragsschluessels.

    Dieselbe Normalisierung wie im Abgleich, sonst scheitert eine
    Mandatsreferenz mit Bindestrich an ihrer eigenen Schreibweise.
    """
    return normalize_purpose(value)


def _already_accepted(candidate: ContractCandidate, existing: Sequence[Any]) -> bool:
    """Steht dieser Vertrag schon in `contracts`?"""
    candidate_key = _normalized_key(candidate.contract_key)
    for rule in existing:
        rule_key = _normalized_key(rule.contract_key)
        # Schluesselgleichheit ist der staerkste Beleg und bricht sofort ab.
        # Ohne sie wuerde derselbe Vertrag nach einer Preisaenderung erneut
        # vorgeschlagen, weil der Betragsvergleich nicht mehr passt.
        if candidate_key and candidate_key == rule_key:
            return True

        same_payee = (
            candidate.payee_id is not None
            and rule.payee_id is not None
            and UUID(str(rule.payee_id)) == candidate.payee_id
        ) or (
            rule.payee_name is not None
            and payee_key(str(rule.payee_name)) == candidate.payee_key
        )
        if not same_payee:
            continue

        if rule.status == "not_a_contract":
            # Eine Verneinung MIT Schluessel gilt nur fuer diesen einen
            # Vertrag. Empfaengerweit wuerde sie den zweiten, parallel
            # laufenden Vertrag desselben Anbieters gleich mit erschlagen.
            if not rule_key:
                return True
            continue

        if rule.frequency != candidate.frequency:
            continue
        if rule.direction != candidate.direction:
            continue
        tolerance = max(
            candidate.amount_tolerance_minor,
            int(rule.tolerance_amount_minor),
            _MIN_TOLERANCE_MINOR,
        )
        if abs(int(rule.amount_minor) - candidate.amount_minor) <= tolerance:
            return True
    return False


def _sort_key(candidate: ContractCandidate) -> tuple[int, float, int, int]:
    """Sichere Vorschlaege zuerst, unsichere ans Ende (Abschnitt 6.4)."""
    return (
        0 if candidate.confidence == "suggested" else 1,
        -candidate.score.total,
        -candidate.occurrences_count,
        -candidate.last_date.toordinal(),
    )


async def get_contract_candidates(
    session: AsyncSession,
    household_id: UUID,
    *,
    limit: int | None = 10,
    include_uncertain: bool = True,
) -> list[ContractCandidate]:
    """Nachfolger von `rules_service.get_recurring_candidates`.

    Liefert zusaetzlich Vertragsschluessel, Preisstufen, Vertragsart und den
    erklaerten Vertrags-Score.
    """
    rows = await load_detection_transactions(session, household_id)
    semantics = await _load_category_semantics(session, household_id)
    candidates = cluster_candidates(
        rows,
        affinity_by_category=_affinities_from_semantics(semantics),
        contract_type_by_category=_contract_types_from_semantics(semantics),
    )

    existing = (
        await session.execute(
            text(_EXISTING_CONTRACTS_SQL), {"household_id": str(household_id)}
        )
    ).all()
    candidates = [item for item in candidates if not _already_accepted(item, existing)]
    if not include_uncertain:
        candidates = [item for item in candidates if item.confidence == "suggested"]

    candidates.sort(key=_sort_key)
    return candidates if limit is None else candidates[:limit]


async def cluster_all_household_transactions(
    session: AsyncSession, household_id: UUID, *, months_back: int = _DEFAULT_MONTHS_BACK
) -> list[ContractCandidate]:
    """Dieselbe Buendelung wie `get_contract_candidates`, aber OHNE den
    `_already_accepted`-Filter.

    Fuer den Legacy-Reparaturlauf (Abschnitt 19.9): der buendelt bewusst auch
    Zahlungsreihen, zu denen bereits ein (unvollstaendiger) Legacy-Vertrag
    existiert - `get_contract_candidates` wuerde die wegen dieses Vertrags als
    "schon erledigt" verwerfen und genau die Faelle verstecken, die repariert
    werden sollen.
    """
    rows = await load_detection_transactions(session, household_id, months_back=months_back)
    semantics = await _load_category_semantics(session, household_id)
    candidates = cluster_candidates(
        rows,
        affinity_by_category=_affinities_from_semantics(semantics),
        contract_type_by_category=_contract_types_from_semantics(semantics),
    )
    candidates.sort(key=_sort_key)
    return candidates


async def find_candidate(
    session: AsyncSession, household_id: UUID, source_transaction_id: UUID
) -> ContractCandidate | None:
    """Findet den Kandidaten zu einer angezeigten Buchung.

    Der Anker wandert: er ist die juengste Buchung der juengsten Preisstufe, und
    ein Import zwischen Anzeige und Annehmen schiebt ihn weiter. Deshalb trifft
    auch JEDE andere Buchung der juengsten Stufe - sonst bekaeme der Nutzer beim
    Bestaetigen ein 404 fuer einen Vorschlag, den er gerade noch gesehen hat.
    """
    candidates = await get_contract_candidates(session, household_id, limit=None)
    for candidate in candidates:
        if candidate.source_transaction_id == source_transaction_id:
            return candidate
    for candidate in candidates:
        if any(
            row.transaction_id == source_transaction_id
            for row in candidate.current_step.transactions
        ):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

_FREQUENCY_TOLERANCE_DAYS: Final[dict[DetectionFrequency, int]] = {
    frequency: spec.tolerance_days for frequency, spec in FREQUENCY_SPECS.items()
}

_CONTRACT_INSERT: Final = """
insert into contracts (
  household_id, name, provider_label, contract_type, account_id, payee_id, category_id,
  contract_key, contract_key_source, direction, amount_minor, frequency,
  due_rule, due_day, next_due_date, first_seen_date, last_seen_date,
  tolerance_days, tolerance_amount_minor, installments_total, installments_paid,
  status, source, detection_confidence
) values (
  :household_id, :name, :provider_label, :contract_type, cast(:account_id as uuid),
  cast(:payee_id as uuid), cast(:category_id as uuid), :contract_key,
  cast(:contract_key_source as text), :direction, :amount_minor, :frequency,
  :due_rule, cast(:due_day as int), :next_due_date, :first_seen, :last_seen,
  :tolerance_days, :tolerance_amount, cast(:installments_total as int),
  :installments_paid, :status, :source, cast(:detection_confidence as numeric)
) returning id
"""


def _due_fields(candidate: ContractCandidate) -> tuple[str, int | None]:
    if candidate.frequency == "weekly":
        return "day_of_month", None
    # Ab dem 29. ist der Zieltag nicht in jedem Monat vorhanden; 'month_end'
    # haelt die Faelligkeit dann am jeweiligen Monatsletzten statt sie
    # dauerhaft auf den 28. wandern zu lassen.
    if candidate.last_date.day > 28:
        return "month_end", candidate.last_date.day
    return "day_of_month", candidate.last_date.day


async def _get_or_create_payee(
    session: AsyncSession, household_id: UUID, candidate: ContractCandidate
) -> UUID:
    """Verknuepft einen bestaetigten Vertrag mit einem echten Empfaenger."""
    if candidate.payee_id is not None:
        return candidate.payee_id

    existing = (
        await session.execute(
            text(
                "select id from payees where household_id = :household_id "
                "and lower(btrim(name)) = lower(btrim(:name)) limit 1"
            ),
            {"household_id": str(household_id), "name": candidate.payee_label},
        )
    ).first()
    if existing is not None:
        # Row-Attribute sind fuer mypy Any; der explizite UUID() haelt die
        # deklarierte Rueckgabe ein, statt sie stillschweigend aufzuweichen.
        return UUID(str(existing.id))

    created = (
        await session.execute(
            text(
                "insert into payees (household_id, name) values (:household_id, :name) "
                "on conflict (household_id, name) do update set is_active = true returning id"
            ),
            {"household_id": str(household_id), "name": candidate.payee_label},
        )
    ).scalar_one()
    return UUID(str(created))


async def _insert_contract(
    session: AsyncSession,
    household_id: UUID,
    candidate: ContractCandidate,
    *,
    category_id: UUID | None,
    payee_id: UUID | None,
    name: str,
    contract_type: ContractType,
    status: str,
    source: str,
) -> UUID:
    due_rule, due_day = _due_fields(candidate)
    next_due = contract_service.next_due_date(
        candidate.last_date, candidate.frequency, due_rule=due_rule, due_day=due_day
    )
    created = await session.execute(
        text(_CONTRACT_INSERT),
        {
            "household_id": str(household_id),
            "name": name[:160],
            "provider_label": candidate.payee_label[:160],
            "contract_type": contract_type,
            "account_id": str(candidate.account_id) if candidate.account_id else None,
            "payee_id": str(payee_id) if payee_id else None,
            "category_id": str(category_id) if category_id else None,
            "contract_key": candidate.contract_key,
            "contract_key_source": candidate.contract_key_source,
            "direction": candidate.direction,
            "amount_minor": candidate.amount_minor,
            "frequency": candidate.frequency,
            "due_rule": due_rule,
            "due_day": due_day,
            "next_due_date": next_due,
            "first_seen": candidate.first_date,
            "last_seen": candidate.last_date,
            "tolerance_days": _FREQUENCY_TOLERANCE_DAYS[candidate.frequency],
            "tolerance_amount": candidate.amount_tolerance_minor,
            "installments_total": candidate.installments_total,
            "installments_paid": candidate.installments_paid or 0,
            "status": status,
            "source": source,
            # Der Score wird mitgeschrieben: ohne ihn liesse sich spaeter nicht
            # mehr sagen, wie sicher die Erkennung bei diesem Vertrag war.
            "detection_confidence": candidate.score.total,
        },
    )
    return UUID(str(created.one().id))


async def create_contract_from_candidate(
    session: AsyncSession,
    household_id: UUID,
    candidate: ContractCandidate,
    *,
    category_id: UUID,
    name: str | None = None,
    contract_type: ContractType | None = None,
) -> UUID:
    """Legt aus einem bestaetigten Kandidaten den Vertrag an.

    Die erkannte Betragstreppe wandert dabei vollstaendig in
    `contract_amount_history`: die abgeschlossenen Stufen als 'backfill', die
    laufende als 'initial'. Ohne diesen Schritt begaenne der Betragsverlauf mit
    dem heutigen Preis, und eine bereits stattgefundene Preiserhoehung waere aus
    den Daten nicht mehr ablesbar.
    """
    payee_id = await _get_or_create_payee(session, household_id, candidate)
    contract_id = await _insert_contract(
        session,
        household_id,
        candidate,
        category_id=category_id,
        payee_id=payee_id,
        name=name or candidate.display_name,
        contract_type=contract_type or candidate.contract_type,
        status="active",
        source="confirmed",
    )

    for index, step in enumerate(candidate.amount_steps):
        await contract_service.open_amount_stage(
            session,
            household_id,
            contract_id,
            amount_minor=abs(step.amount_minor),
            valid_from=step.first_date,
            # Die LAUFENDE Stufe traegt 'initial', die abgeschlossenen
            # 'backfill' - dieselbe Aufteilung wie beim rueckwirkenden Erstlauf
            # eines von Hand angelegten Vertrags. Umgekehrt herum entstuenden
            # zwei Zeilen fuer denselben Zeitraum: `_write_backfill_stages`
            # loescht vor dem Neuaufbau nur 'backfill' und laesst eine
            # abgeschlossene 'initial'-Stufe stehen, baut sie aber selbst noch
            # einmal auf.
            change_reason="backfill" if index < len(candidate.amount_steps) - 1 else "initial",
            # Die aelteste Buchung der Stufe hat den Preis gesetzt.
            source_transaction_id=step.transactions[-1].transaction_id,
        )

    await contract_service.record_event(
        session,
        household_id,
        contract_id,
        "created",
        occurred_on=candidate.last_date,
        payload={
            "source": "detection_v2",
            "detection_confidence": candidate.score.total,
            "confidence": candidate.confidence,
            "amount_steps": len(candidate.amount_steps),
            "contract_key_source": candidate.contract_key_source,
        },
    )
    return contract_id


async def dismiss_candidate(
    session: AsyncSession, household_id: UUID, candidate: ContractCandidate
) -> UUID:
    """"Kein Vertrag": legt die Zeile mit status='not_a_contract' an.

    Der Vertragsschluessel wird mitgeschrieben - nur dadurch bleibt die
    Verneinung auf genau diesen einen Vertrag beschraenkt und erschlaegt nicht
    den zweiten, parallel laufenden Vertrag desselben Anbieters.
    """
    contract_id = await _insert_contract(
        session,
        household_id,
        candidate,
        category_id=candidate.suggested_category_id,
        payee_id=candidate.payee_id,
        name=candidate.display_name,
        contract_type=candidate.contract_type,
        status="not_a_contract",
        source="detected",
    )
    await contract_service.record_event(
        session,
        household_id,
        contract_id,
        "dismissed",
        occurred_on=candidate.last_date,
        payload={
            "source": "detection_v2",
            "detection_confidence": candidate.score.total,
            "contract_key": candidate.contract_key,
        },
    )
    return contract_id
