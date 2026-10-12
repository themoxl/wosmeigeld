"""Fingerprinting und Dublettenerkennung (Architekturplan Abschnitt 7.4)."""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class ExistingTransaction:
    id: str
    account_id: str
    booked_date: date
    amount_minor: int
    external_reference: str | None = None
    fingerprint: str | None = None
    is_transfer: bool = False
    # Empfaenger der bereits gebuchten Zeile (transactions.payee_label,
    # ersatzweise der Name aus payees). Wird fuer Stufe 4 gebraucht - siehe
    # classify_row.
    payee: str | None = None
    # Verwendungszweck der bereits gebuchten Zeile (transactions.purpose_raw).
    # Stufe 3 haengt daran; ohne ihn blieb genau die Dublette unerkannt, bei der
    # sich nur die Schreibweise des Empfaengers unterscheidet.
    purpose: str | None = None


@dataclass
class ClassificationResult:
    status: str
    duplicate_of_transaction_id: str | None = None


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    s = text.lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


# Mindestlaenge, ab der ein gemeinsamer Namensanfang als Uebereinstimmung
# zaehlt. Vier Zeichen, weil echte Empfaengernamen genau dort anfangen: Aral,
# IKEA, Rewe, Lidl, Kiosk. Bei sechs fiel "Kiosk" gegen "Kiosk am Bahnhof"
# durch, also genau der Fall, den diese Stufe fangen soll.
#
# Nach unten schuetzt die Grenze davor, dass ein zweistelliger Namensrest auf
# alles passt. Zu hoch angesetzt kostet sie nur einen Hinweis, den es nicht
# gibt - die Zeile wird dann als neue Buchung uebernommen, was im Zweifel die
# harmlosere Richtung ist.
_PAYEE_MIN_PREFIX = 4


def payees_match(a: str | None, b: str | None) -> bool:
    """Sind das plausibel dieselben Empfaenger?

    Kein exakter Vergleich, weil dieselbe Gegenseite je nach Auszugsformat
    unterschiedlich lang ankommt - MT940 kuerzt Namensfelder hart ab, CSV-
    Exporte derselben Bank nicht. "MUSTER VERSICHERUNG AG" und
    "MUSTER VERSICHERUNG AG BAUSPARKASSE" sind dieselbe Firma; ein Gleichheits-
    vergleich wuerde die Dublette uebersehen, um die es hier geht.

    Zwei leere Empfaenger gelten als Uebereinstimmung: dann traegt das Feld
    nichts zur Unterscheidung bei und die Entscheidung faellt allein ueber
    Betrag und Datum, wie bisher. Einer leer und einer gefuellt dagegen ist ein
    Unterschied und keine Dublette.
    """
    norm_a = normalize_text(a)
    norm_b = normalize_text(b)
    if not norm_a and not norm_b:
        return True
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    kurz, lang = (norm_a, norm_b) if len(norm_a) <= len(norm_b) else (norm_b, norm_a)
    return len(kurz) >= _PAYEE_MIN_PREFIX and lang.startswith(kurz)


def compute_fingerprint(*args: object) -> str:
    """Unterstuetzt sowohl
    compute_fingerprint(account_id, booked_date, amount_minor, payee_raw, purpose)
    als auch compute_fingerprint(booked_date, amount_minor, payee_raw, purpose)."""
    if len(args) == 5 and args[0]:
        account_id, booked_date, amount_minor, payee_raw, purpose = args
        raw_key = (
            f"{account_id!s}|{booked_date}|{amount_minor}|"
            f"{normalize_text(str(payee_raw))[:40]}|{normalize_text(str(purpose))[:60]}"
        )
    else:
        if len(args) == 5:
            _, booked_date, amount_minor, payee_raw, purpose = args
        else:
            booked_date, amount_minor, payee_raw, purpose = args[:4]
        raw_key = (
            f"{booked_date}|{amount_minor}|{normalize_text(str(payee_raw))[:40]}|"
            f"{normalize_text(str(purpose))[:60]}"
        )

    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def classify_row(
    tx: object,
    fingerprint: str,
    target_account_id: str,
    existing: Sequence[ExistingTransaction | dict[str, object]],
    other_account_existing: Sequence[ExistingTransaction | dict[str, object]],
) -> ClassificationResult:
    """Klassifiziert eine Staging-Zeile."""
    def get_val(item: object, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    ext_ref = get_val(tx, "external_reference")
    tx_booked_date = get_val(tx, "booked_date")
    tx_amount_minor = get_val(tx, "amount_minor")

    # 1. Exakte externe Referenz im Zielkonto - zusaetzlich abgesichert durch
    #    Betrag UND Buchungsdatum.
    #
    #    Die Referenz allein traegt diese Entscheidung nicht: Banken vergeben
    #    denselben Wert mehrfach (in einem MT940-Auszug tauchte
    #    'CREDITTRANSFER<ref>' 23-mal und eine Kartenreferenz 7-mal
    #    auf), und ein Dauerauftrag behaelt seine Referenz Monat fuer Monat.
    #    'duplicate_exact' fuehrt dazu, dass die Zeile nie uebernommen wird -
    #    eine Fehlentscheidung hier laesst echte Buchungen stillschweigend
    #    verschwinden. Betrag und Datum kosten nichts und schliessen genau
    #    diese Faelle aus; derselbe Beleg im selben Konto stimmt in allen drei
    #    Merkmalen ueberein.
    if ext_ref:
        for ex in existing:
            if (
                get_val(ex, "external_reference") == ext_ref
                and get_val(ex, "amount_minor") == tx_amount_minor
                and get_val(ex, "booked_date") == tx_booked_date
            ):
                return ClassificationResult("duplicate_exact", str(get_val(ex, "id")))

    # 2. Exakter Fingerabdruck im Zielkonto
    for ex in existing:
        ex_fp = get_val(ex, "fingerprint")
        if ex_fp and ex_fp == fingerprint:
            return ClassificationResult("duplicate_exact", str(get_val(ex, "id")))

    # 3. Gleicher Tag, gleicher Betrag, GLEICHER VERWENDUNGSZWECK - unabhaengig
    #    davon, wie der Empfaenger geschrieben ist.
    #
    #    Diese Stufe fehlte, und mit ihr blieb der haeufigste Fall unerkannt: der
    #    Verwendungszweck wurde ueberhaupt nie verglichen. Er steckte nur im
    #    Fingerabdruck (Stufe 2) - und der enthaelt auch den Empfaenger, bricht
    #    also, sobald derselbe Beleg einmal mit und einmal ohne Empfaengernamen
    #    ankommt. Genau das passiert im Alltag staendig: MT940 kuerzt das
    #    Namensfeld hart ab, eine von Hand erfasste Buchung hat gar keinen
    #    Empfaenger, und derselbe Umsatz aus zwei Ausgabeformaten derselben Bank
    #    steht einmal als "PAYPAL" und einmal als "PayPal Europe S.a.r.l. et Cie
    #    S.C.A" da. Stufe 4 fiel dann ebenfalls durch (leer gegen gefuellt gilt
    #    dort ausdruecklich als Unterschied), und die Zeile lief als "neu"
    #    durch - eine echte Doppelbuchung, die niemand gemeldet bekam.
    #
    #    Warum 'duplicate_possible' und nicht 'duplicate_exact': derselbe Zweck
    #    am selben Tag mit demselben Betrag KANN legitim zweimal vorkommen -
    #    "Taschengeld" 100,00 an zwei Empfaenger ist genau dieser Fall. Die
    #    Entscheidung gehoert deshalb dem Nutzer; gemeldet werden muss sie
    #    trotzdem.
    #
    #    Ein leerer Zweck traegt nichts bei und wird uebersprungen - sonst
    #    passte jede Buchung ohne Verwendungszweck auf jede andere.
    tx_purpose = normalize_text(get_val(tx, "purpose"))
    if tx_purpose:
        for ex in existing:
            if (
                get_val(ex, "booked_date") == tx_booked_date
                and get_val(ex, "amount_minor") == tx_amount_minor
                and normalize_text(get_val(ex, "purpose")) == tx_purpose
            ):
                return ClassificationResult("duplicate_possible", str(get_val(ex, "id")))

    # 4. Moegliches Duplikat im Zielkonto: gleicher Betrag, DERSELBE Tag und ein
    #    zusammenpassender Empfaenger.
    #
    #    Frueher genuegten Betrag und ein Datum innerhalb von ±3 Tagen. Das
    #    meldete in der Praxis unbrauchbar viel: an einem frisch importierten
    #    Monat standen 47 "moegliche Dubletten", nahezu alle falsch. Der Grund
    #    liegt in der Natur von Haushaltsbuchungen - runde, wiederkehrende
    #    Betraege sind der Normalfall, nicht die Ausnahme. 100,00 Taschengeld
    #    traf auf jede andere Buchung ueber 100,00 im Umkreis einer Woche, ganz
    #    gleich an wen. Eine Liste, die zu 95 Prozent aus Fehlalarmen besteht,
    #    wird nicht durchgesehen, sondern weggeklickt; damit ist auch die echte
    #    Dublette darin verloren.
    #
    #    Der Empfaenger ist das Merkmal, das die Faelle trennt, und er kostet
    #    nichts - er steht in der Staging-Zeile ohnehin. Das Zeitfenster faellt
    #    zugleich auf null Tage: eine echte Doppelbuchung traegt dasselbe
    #    Buchungsdatum. Verschiebt sich das Datum wirklich (Wertstellung vs.
    #    Buchung), unterscheiden sich in aller Regel auch Referenz oder
    #    Verwendungszweck nicht - dann greift bereits Stufe 1 oder 2.
    #
    #    Nicht angetastet bleibt Stufe 5: eine Umbuchung zwischen zwei Konten
    #    wird auf beiden Seiten oft an verschiedenen Tagen gebucht, dort ist das
    #    Fenster fachlich richtig.
    tx_payee = get_val(tx, "payee_raw")
    for ex in existing:
        ex_date = get_val(ex, "booked_date")
        ex_amt = get_val(ex, "amount_minor")
        if (
            ex_date is not None
            and tx_booked_date is not None
            and ex_date == tx_booked_date
            and ex_amt == tx_amount_minor
            and payees_match(tx_payee, get_val(ex, "payee"))
        ):
            return ClassificationResult("duplicate_possible", str(get_val(ex, "id")))

    # 5. Transfer-Kandidat auf anderem Konto (Gegenbetrag, Datum innerhalb ±3 Tage,
    # noch kein Transfer)
    for ot in other_account_existing:
        ot_amt = get_val(ot, "amount_minor")
        ot_date = get_val(ot, "booked_date")
        ot_is_tr = get_val(ot, "is_transfer")
        if ot_amt == -tx_amount_minor and not ot_is_tr and ot_date and tx_booked_date:
            d_diff = abs((ot_date - tx_booked_date).days)
            if d_diff <= 3:
                return ClassificationResult("transfer_candidate", str(get_val(ot, "id")))

    return ClassificationResult("new", None)


def evaluate_row_status(
    tx: object,
    existing_transactions: list[dict[str, object]],
    household_other_account_transactions: list[dict[str, object]],
) -> tuple[str, str | None]:
    acc_id = getattr(tx, "account_id", None)
    if acc_id:
        fp = compute_fingerprint(
            acc_id,
            getattr(tx, "booked_date", None),
            getattr(tx, "amount_minor", None),
            getattr(tx, "payee_raw", None),
            getattr(tx, "purpose", None),
        )
    else:
        fp = compute_fingerprint(
            getattr(tx, "booked_date", None),
            getattr(tx, "amount_minor", None),
            getattr(tx, "payee_raw", None),
            getattr(tx, "purpose", None),
        )
    res = classify_row(
        tx,
        fp,
        str(acc_id) if acc_id else "",
        existing_transactions,
        household_other_account_transactions,
    )
    return res.status, res.duplicate_of_transaction_id
