"""MT940/SWIFT-Kontoauszug-Parser.

Deutsche Banken liefern den Verwendungszweck nicht als Fliesstext, sondern im
strukturierten Mehrzweckfeld :86: (DTAUS-Feld 86): ein Geschaeftsvorfallcode,
gefolgt von Subfeldern '?NN'. Der eigentliche Verwendungszweck steckt in
?20-?29 und ?60-?63 und ist dort in Stuecke zu je 27 Zeichen zerhackt.

Diese Stuecke muessen OHNE Trennzeichen wieder aneinandergesetzt werden.
Frueher wurde hier mit Leerzeichen gejoint und das Ergebnis mitsamt aller
'?NN'-Marker als Verwendungszweck gespeichert - dabei zerriss jedes Wort und
jede IBAN an der Stueckgrenze ('IBAN: D' + 'E0000...' statt 'IBAN: DE0000...')
und in der Buchungsliste stand roher Bankschrott.

Ebenso wichtig sind zwei Felder, die frueher schlicht falsch belegt waren:

* Der Geschaeftsvorfallcode (105 = Lastschrift, 117 = Dauerauftrag, ...) steht
  am Anfang von :86:, nicht in :61:. In :61: steht der SWIFT-Buchungsschluessel
  (NDDT/NTRF/NMSC). Gespeichert wurde bisher letzterer - womit
  `contract_detection_service._mt940_score` bei jedem echten Import ins Leere
  lief, weil es 'N' + drei Ziffern erwartet.
* :61: fuehrt ZWEI Datumsangaben: sechsstellig das Valutadatum, vierstellig
  (MMTT) das Buchungsdatum. Uebernommen wurde bisher das Valutadatum als
  Buchungsdatum, das Valutadatum selbst gar nicht.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from worker.imports.canonical import (
    CanonicalTransaction,
    ImportParseError,
    parse_amount_to_minor_units,
)
from worker.imports.parsers.base import DetectionResult

# :61: nach SWIFT-Feldstruktur 6!n[4!n]2a[1!a]15d1!a3!c16x:
#   6!n     Valutadatum (JJMMTT)
#   [4!n]   Buchungsdatum (MMTT), optional
#   2a      Soll-/Habenkennzeichen (C/D bzw. RC/RD fuer Storni)
#   [1!a]   Waehrungsart (drittes Zeichen des Waehrungscodes)
#   15d     Betrag
#   1!a3!c  Buchungsschluessel (NTRF, NDDT, NMSC, ...)
#   16x     Kundenreferenz
#
# Die zweistelligen Kennzeichen stehen bewusst VOR den einstelligen: bei 'RD'
# darf nicht 'R' als Waehrungsart und 'D' als Kennzeichen gelesen werden.
_TAG_61_RE = re.compile(
    r"^:61:(?P<value_date>\d{6})(?P<entry_date>\d{4})?"
    r"(?P<sign>RC|RD|C|D)(?P<funds_code>[A-Z])?"
    r"(?P<amount>\d+[.,]\d{2})(?P<type>[A-Z][A-Z0-9]{3})(?P<ref>[^\n\r]*)"
)

# :60F:/:60M: (Anfangssaldo) und :62F:/:62M: (Schlusssaldo) eines Auszugsblocks.
_BALANCE_RE = re.compile(
    r"^:(?P<tag>60F|60M|62F|62M):(?P<sign>[CD])(?P<date>\d{6})"
    r"(?P<currency>[A-Z]{3})(?P<amount>[\d.,]+)"
)

# Subfeldmarker '?NN' innerhalb von :86:.
_SUBFIELD_RE = re.compile(r"\?(\d{2})")

# Subfelder, die zusammen den Verwendungszweck ergeben - in genau dieser
# Reihenfolge, ?60-?63 sind die Fortsetzung von ?20-?29.
_PURPOSE_SUBFIELDS: frozenset[str] = frozenset(
    [f"{n:02d}" for n in range(20, 30)] + [f"{n:02d}" for n in range(60, 64)]
)
# Name des Zahlungspflichtigen/-empfaengers, zweimal 27 Zeichen.
_PAYEE_SUBFIELDS: tuple[str, ...] = ("32", "33")
_BOOKING_TEXT_SUBFIELD = "00"

# Logische SEPA-Schluessel innerhalb des zusammengesetzten Verwendungszwecks.
# Sie stehen dort als '<TAG>+<Wert>' hintereinander, ohne Trennzeichen - erst
# der naechste Schluessel beendet den vorigen Wert. Genau daran scheiterte die
# alte Zeichenklassen-Regex: 'EREF+Beleg-Nr.100000000001' endete am Punkt und
# lieferte das nutzlose 'Beleg-Nr'.
_SEPA_TAGS: tuple[str, ...] = (
    "EREF",
    "KREF",
    "MREF",
    "CRED",
    "DEBT",
    "COAM",
    "OAMT",
    "SVWZ",
    "ABWA",
    "ABWE",
    "IBAN",
    "BIC",
    "PURP",
    "RREF",
    "RTRN",
)
_SEPA_TAG_RE = re.compile(r"(" + "|".join(_SEPA_TAGS) + r")\+")
_LEADING_SEPA_TAG_RE = re.compile(r"^(" + "|".join(_SEPA_TAGS) + r")\+")

# Werte, die formal im Referenzfeld stehen, aber keine Referenz SIND. 'KREF+'
# ist der haeufigste Fall: die Bank schreibt den Schluessel ohne Wert nach
# :61:. Wandert so etwas als external_reference in die Datenbank, haelt
# `dedup.classify_row` jede spaetere Buchung mit demselben Platzhalter fuer ein
# exaktes Duplikat - echte Buchungen verschwinden dann stillschweigend.
_NON_REFERENCES: frozenset[str] = frozenset(
    {"NONREF", "NREF", "KREF+", "EREF+", "MREF+", "CRED+", "SVWZ+", "UNKNOWN", "-"}
)


@dataclass(frozen=True)
class StatementBalance:
    """Ein Saldo aus :60F:/:62F: - Betrag in Minor Units, mit Stichtag."""

    balance_minor: int
    balance_date: date
    currency: str


@dataclass(frozen=True)
class StatementSummary:
    """Saldenrahmen einer MT940-Datei ueber alle Auszugsblocke hinweg.

    Eine Datei enthaelt in der Praxis viele Bloecke (ein Kontoauszug je
    Buchungstag). `opening` ist der Anfangssaldo des ersten, `closing` der
    Schlusssaldo des letzten Blocks. `unbalanced_blocks` nennt jeden Block, bei
    dem Anfangssaldo + Summe der Buchungen nicht auf den Schlusssaldo fuehrt -
    das ist die Saldenprobe, die beweist, dass keine Buchung verloren ging."""

    opening: StatementBalance | None = None
    closing: StatementBalance | None = None
    account_signature: str | None = None
    unbalanced_blocks: tuple[str, ...] = ()

    @property
    def reconciles(self) -> bool:
        return not self.unbalanced_blocks


@dataclass
class _Tag86:
    """Aufgeloestes Mehrzweckfeld :86:."""

    gvc: str | None = None
    booking_text: str | None = None
    purpose: str | None = None
    payee: str | None = None
    sepa: dict[str, str] = field(default_factory=dict)


def _decode(file_bytes: bytes) -> str:
    """MT940 kommt je nach Bank als UTF-8 oder Latin-1.

    UTF-8 wird zuerst versucht: Latin-1 kann JEDE Bytefolge dekodieren und
    haette eine UTF-8-Datei stillschweigend zu Mojibake gemacht."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("latin-1", errors="replace")


def _parse_mt940_date(yymmdd: str) -> date:
    yy = int(yymmdd[0:2])
    mm = int(yymmdd[2:4])
    dd = int(yymmdd[4:6])
    year = 2000 + yy
    try:
        return date(year, mm, dd)
    except ValueError as exc:
        raise ImportParseError(f"ungueltiges MT940-Datum: {yymmdd!r}") from exc


def _resolve_entry_date(value_date: date, mmdd: str | None) -> date:
    """Buchungsdatum aus dem vierstelligen Feld (MMTT) von :61: ableiten.

    Das Jahr fehlt dort und wird vom Valutadatum uebernommen. Um den
    Jahreswechsel liegen beide Daten in verschiedenen Jahren (Valuta 31.12.,
    Buchung 02.01.) - deshalb wird das Jahr um eins verschoben, sobald der
    Monatsabstand nur so erklaerbar ist."""
    if not mmdd:
        return value_date
    try:
        mm = int(mmdd[0:2])
        dd = int(mmdd[2:4])
    except ValueError:
        return value_date

    for year in (value_date.year, value_date.year + 1, value_date.year - 1):
        try:
            candidate = date(year, mm, dd)
        except ValueError:
            continue
        if abs((candidate - value_date).days) <= 45:
            return candidate
    return value_date


def _parse_balance(line: str) -> StatementBalance | None:
    match = _BALANCE_RE.match(line)
    if not match:
        return None
    amount = match.group("amount").replace(",", ".")
    if match.group("sign") == "D":
        amount = "-" + amount
    try:
        minor = parse_amount_to_minor_units(amount)
        balance_date = _parse_mt940_date(match.group("date"))
    except ImportParseError:
        return None
    return StatementBalance(minor, balance_date, match.group("currency"))


def _split_sepa_tags(stream: str) -> tuple[dict[str, str], str]:
    """Zerlegt den zusammengesetzten Verwendungszweck in seine SEPA-Schluessel.

    Rueckgabe ist (Schluessel -> Wert, Resttext vor dem ersten Schluessel).
    Ein Schluessel endet dort, wo der naechste beginnt; steht keiner mehr an,
    reicht der Wert bis zum Ende. Mehrfach vorkommende Schluessel behalten den
    ersten Wert - der zweite ist bei deutschen Banken die Wiederholung im
    Klartextanhang."""
    if not stream:
        return {}, ""
    matches = list(_SEPA_TAG_RE.finditer(stream))
    if not matches:
        return {}, stream

    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(stream)
        result.setdefault(match.group(1), stream[match.end() : end])
    return result, stream[: matches[0].start()]


def _tidy(value: str | None) -> str | None:
    """Fuellzeichen der 27-Zeichen-Raster entfernen.

    Die Bank fuellt jedes Stueck rechts mit Leerzeichen auf; zusammengesetzt
    ergibt das Kolonnen von bis zu 20 Leerzeichen mitten im Text. Das ist
    Layout, kein Inhalt."""
    if value is None:
        return None
    collapsed = re.sub(r"\s{2,}", " ", value.replace("\t", " ")).strip()
    return collapsed or None


def _parse_tag86(raw: str) -> _Tag86:
    """Loest das Mehrzweckfeld :86: in seine fachlichen Bestandteile auf."""
    if not raw:
        return _Tag86()

    parts = _SUBFIELD_RE.split(raw)
    # split() liefert [Praefix, Tag, Wert, Tag, Wert, ...] - der Praefix vor
    # dem ersten '?' traegt den Geschaeftsvorfallcode.
    #
    # '000' ist kein Geschaeftsvorfall, sondern der Platzhalter fuer 'nicht
    # angegeben'. Ihn als Code zu speichern wuerde eine Aussage behaupten, die
    # die Datei nicht macht - dann ist der SWIFT-Buchungsschluessel aus :61:
    # die ehrlichere Angabe.
    prefix = parts[0].strip()
    gvc = prefix if re.fullmatch(r"\d{3}", prefix) and prefix != "000" else None

    subfields: list[tuple[str, str]] = [
        (parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)
    ]
    if not subfields:
        return _Tag86(gvc=gvc, purpose=_tidy(raw if gvc is None else raw[len(prefix) :]))

    booking_text = next(
        (value for tag, value in subfields if tag == _BOOKING_TEXT_SUBFIELD), None
    )

    # Kern der Sache: die Stuecke OHNE Trennzeichen aneinandersetzen.
    purpose_stream = "".join(
        value for tag, value in subfields if tag in _PURPOSE_SUBFIELDS
    )
    sepa, plain = _split_sepa_tags(purpose_stream)
    purpose = _tidy(sepa.get("SVWZ")) or _tidy(plain) or _tidy(purpose_stream)

    payee = _tidy("".join(value for tag, value in subfields if tag in _PAYEE_SUBFIELDS))
    if payee is None:
        # Kartenabrechnungen (GVC 8xx) fuehren kein ?32; dort steht der
        # Haendlername im ersten Stueck des Verwendungszwecks. Ohne diesen
        # Rueckfall bleibt eine komplette Kreditkartendatei ohne Empfaenger -
        # und damit ohne Empfaengerverknuepfung und Vertragsabgleich.
        #
        # Nur, wenn dieses Stueck wirklich Zwecktext ist: beginnt es mit einer
        # Referenz (EREF+/KREF+/MREF+), waere deren Wert als Empfaengername
        # schlicht falsch.
        first_chunk = next(
            (value for tag, value in subfields if tag in _PURPOSE_SUBFIELDS), None
        )
        if first_chunk is not None:
            leading = _LEADING_SEPA_TAG_RE.match(first_chunk)
            if leading is None:
                payee = _tidy(first_chunk)
            elif leading.group(1) == "SVWZ":
                payee = _tidy(first_chunk[leading.end() :])

    return _Tag86(
        gvc=gvc,
        booking_text=_tidy(booking_text),
        purpose=purpose,
        payee=payee,
        sepa={key: _tidy(value) or "" for key, value in sepa.items()},
    )


def _clean_reference(value: str | None) -> str | None:
    """Filtert Platzhalter heraus, die keine Referenz sind (siehe
    _NON_REFERENCES)."""
    if value is None:
        return None
    cleaned = value.strip().strip("/").strip()
    if not cleaned or cleaned.upper() in _NON_REFERENCES:
        return None
    if not any(char.isalnum() for char in cleaned):
        return None
    return cleaned


class Mt940Parser:
    format_name = "mt940"

    def detect(self, file_bytes: bytes, filename: str) -> DetectionResult:
        ext = filename.lower()
        has_ext = any(ext.endswith(e) for e in (".mt940", ".sta", ".mta", ".txt"))
        text = _decode(file_bytes)
        has_tags = ":20:" in text and (":61:" in text or ":25:" in text or ":28C:" in text)
        if has_tags:
            return DetectionResult(True, self.format_name, 0.95, "MT940-SWIFT-Tags erkannt")
        if has_ext and ":61:" in text:
            return DetectionResult(True, self.format_name, 0.8, "MT940-Erweiterung + :61:-Tag")
        return DetectionResult(False, self.format_name, 0.0, "keine MT940-Tags")

    def parse(self, file_bytes: bytes) -> list[CanonicalTransaction]:
        return self.parse_statement(file_bytes)[0]

    def parse_statement(
        self, file_bytes: bytes
    ) -> tuple[list[CanonicalTransaction], StatementSummary]:
        """Wie parse(), liefert zusaetzlich den Saldenrahmen der Datei."""
        text = _decode(file_bytes)

        transactions: list[CanonicalTransaction] = []
        current_61: dict[str, str | None] | None = None
        current_86: list[str] = []
        account_signature: str | None = None

        opening: StatementBalance | None = None
        closing: StatementBalance | None = None
        block_opening: StatementBalance | None = None
        block_sum = 0
        unbalanced: list[str] = []

        def flush_current() -> None:
            nonlocal current_61, current_86, block_sum
            if current_61 is None:
                return
            tag61 = current_61
            tag86_lines = current_86
            current_61 = None
            current_86 = []

            sign = tag61["sign"] or "D"
            # RC/RD sind Storni: 'Reversal of credit' bucht aus, nicht ein.
            is_credit = sign in ("C", "RD")
            amount_str = (tag61["amount"] or "0").replace(",", ".")
            if not is_credit:
                amount_str = "-" + amount_str
            amount_minor = parse_amount_to_minor_units(amount_str)
            block_sum += amount_minor

            # Die Zeilen ohne Trennzeichen aneinandersetzen - die
            # Stueckgrenzen von :86: liegen genau auf den Zeilenenden.
            raw_86 = "".join(tag86_lines)
            tag86 = _parse_tag86(raw_86)

            value_date = _parse_mt940_date(tag61["value_date"] or "")
            booked_date = _resolve_entry_date(value_date, tag61.get("entry_date"))

            external_reference = (
                _clean_reference(tag86.sepa.get("EREF"))
                or _clean_reference(tag86.sepa.get("KREF"))
                or _clean_reference(tag61.get("ref"))
            )

            # Der Geschaeftsvorfallcode aus :86: ist der Wert, den die
            # Vertragserkennung auswertet; der SWIFT-Schluessel aus :61: ist
            # nur der Rueckfall, wenn :86: keinen GVC fuehrt.
            transaction_code = f"N{tag86.gvc}" if tag86.gvc else tag61.get("type")

            transactions.append(
                CanonicalTransaction(
                    booked_date=booked_date,
                    value_date=value_date,
                    amount_minor=amount_minor,
                    currency="EUR",
                    payee_raw=tag86.payee,
                    purpose=tag86.purpose,
                    external_reference=external_reference,
                    bank_transaction_code=transaction_code,
                    account_signature=account_signature,
                    raw={
                        "tag61": tag61.get("raw", ""),
                        "tag86": raw_86,
                        "gvc": tag86.gvc,
                        "buchungstext": tag86.booking_text,
                        "swift_code": tag61.get("type"),
                        "sepa": tag86.sepa,
                    },
                )
            )

        for line in text.splitlines():
            if not line.strip():
                continue

            if line.startswith(":61:"):
                flush_current()
                match = _TAG_61_RE.match(line)
                if match is None:
                    raise ImportParseError(
                        f"Tag :61: konnte nicht geparst werden: {line!r}"
                    )
                current_61 = {**match.groupdict(), "raw": line}
                continue

            if line.startswith(":86:"):
                if current_61 is not None:
                    current_86.append(line[4:])
                continue

            if line.startswith(":25:"):
                flush_current()
                account_signature = line[4:].strip()
                continue

            if line.startswith((":60F:", ":60M:")):
                flush_current()
                balance = _parse_balance(line)
                block_opening = balance
                block_sum = 0
                if opening is None:
                    opening = balance
                continue

            if line.startswith((":62F:", ":62M:")):
                flush_current()
                balance = _parse_balance(line)
                if balance is not None:
                    closing = balance
                    if block_opening is not None:
                        expected = balance.balance_minor - block_opening.balance_minor
                        if expected != block_sum:
                            unbalanced.append(
                                f"Auszug per {balance.balance_date.isoformat()}: "
                                f"Anfangssaldo {block_opening.balance_minor / 100:.2f} "
                                f"+ Buchungen {block_sum / 100:.2f} ergibt nicht "
                                f"Schlusssaldo {balance.balance_minor / 100:.2f}"
                            )
                block_opening = None
                block_sum = 0
                continue

            if line.startswith(":"):
                # Jedes andere Strukturfeld (:20:, :28C:, :64:, :65:, ...)
                # beendet die laufende Buchung.
                flush_current()
                continue

            if line.strip() == "-":
                # Blockabschluss eines Auszugs. Frueher landete dieser Strich
                # als Text im Verwendungszweck und sogar im Empfaengernamen
                # ('Musterhandel GmbH -') der jeweils letzten Buchung.
                flush_current()
                continue

            if current_61 is not None:
                current_86.append(line)

        flush_current()

        summary = StatementSummary(
            opening=opening,
            closing=closing,
            account_signature=account_signature,
            unbalanced_blocks=tuple(unbalanced),
        )
        return transactions, summary
