"""Excel-Vorlage fuer Preissichtungen: erzeugen und wieder einlesen.

Warum ueberhaupt eine Tabellendatei und nicht ein Formular in der App: die
BESCHAFFUNG von Preisen ist ein anderes Problem als ihre Verwaltung. Wo die
Zahlen herkommen - abgetippt, aus einem Prospekt, von einem beliebigen Modell
vorstrukturiert - geht dieses System nichts an. Es kennt nur das Format. Damit
bleiben Bilderkennung, Dateiablage und die Rechtsfragen fremder Datenbanken
vollstaendig ausserhalb, ohne dass die Faehigkeit verloren geht.

Die Datei ist ausdruecklich TRANSPORT, nicht Speicher. Jeder Import haengt
Sichtungen an, er ersetzt nichts. Wer dieselbe Datei zweimal einliest, bekommt
keine doppelten Zeilen (Fingerabdruck je Zeile), aber wer naechste Woche eine
neue Datei einliest, bekommt eine zweite Sichtung neben der ersten - genau
darauf beruht spaeter jede Aussage ueber Preisverlauf und Angebotszyklen.

Dieses Modul ist bewusst DB-frei: es baut eine Arbeitsmappe und es liest eine
Arbeitsmappe. Was davon gespeichert wird, entscheidet
grocery_observation_service nach Bestaetigung durch den Nutzer.
"""

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from app.services.grocery_pricing_service import normalize_unit, to_base_amount

SHEET_NAME: Final = "Preise"
GUIDE_SHEET_NAME: Final = "Anleitung"

# Wie viele Zeilen am Dateianfang nach der Kopfzeile durchsucht werden. Eine
# von Hand gepflegte Datei hat gern einen Titel oder eine Leerzeile darueber.
_MAX_HEADER_SCAN_ROWS: Final = 10

# Vorrat an Leerzeilen unter den vorbefuellten Zutaten: Platz fuer Angebote
# und fuer Lebensmittel, die es in der Preisliste noch gar nicht gibt.
_SPARE_ROWS: Final = 40

# Obergrenze fuer eine eingelesene Datei. Deutlich hoeher als beim
# Kostenplanimport (5.000), weil hier eine ganz andere Groessenordnung
# sinnvoll ist: ein Wochenprospekt-Auszug ueber mehrere Haendler ist schnell
# vierstellig, und ueber ein Jahr gesammelt wird daraus eine echte
# Angebotsdatenbank. Die Grenze verhindert nur, dass eine versehentlich
# hochgeladene Riesendatei den Request blockiert - gelesen wird im
# Anfrage-Thread, es gibt keinen Worker dafuer.
MAX_SHEET_ROWS: Final = 20_000


class PriceSheetError(ValueError):
    """Die Datei ist als Preisvorlage unbrauchbar - kein Blatt, keine
    Kopfzeile. Traegt einen Satz, der dem Nutzer direkt vorgelegt werden kann."""


@dataclass(frozen=True)
class SheetColumn:
    key: str
    label: str
    width: int
    hint: str
    # Weitere akzeptierte Schreibweisen in der Kopfzeile, klein und ohne Rand.
    # Eine von Hand oder von einem Modell gebaute Datei trifft den Wortlaut
    # der Vorlage selten exakt.
    synonyms: tuple[str, ...] = ()


# Reihenfolge = Spaltenreihenfolge in der erzeugten Datei. Beim Einlesen wird
# ueber die Kopfzeile zugeordnet, nicht ueber die Position: eine umsortierte
# oder um eigene Spalten ergaenzte Datei bleibt lesbar.
TEMPLATE_COLUMNS: Final[tuple[SheetColumn, ...]] = (
    SheetColumn(
        key="ingredient",
        label="Zutat",
        width=28,
        hint="Pflicht. Am besten genau so schreiben wie in der Preisliste.",
        synonyms=("lebensmittel", "artikel", "produkt", "name"),
    ),
    SheetColumn(
        key="store",
        label="Laden",
        width=16,
        hint="Kette: Aldi, Lidl, Rewe, Edeka. Leer = irgendwo gesehen.",
        synonyms=("geschäft", "geschaeft", "markt", "händler", "haendler", "kette"),
    ),
    SheetColumn(
        key="location",
        label="Ort",
        width=18,
        hint=(
            "Filiale, falls sie eigene Preise macht (Rewe und Edeka sind oft "
            "inhabergeführt). Sonst leer."
        ),
        synonyms=("filiale", "standort", "stadt"),
    ),
    SheetColumn(
        key="brand",
        label="Marke",
        width=16,
        hint='Markenname, z. B. Barilla. "Eigenmarke" für die Hausmarke. Leer = egal.',
        synonyms=("hersteller", "brand"),
    ),
    SheetColumn(
        key="organic",
        label="Bio",
        width=7,
        hint="ja / nein.",
        synonyms=("öko", "oeko", "organic"),
    ),
    SheetColumn(
        key="pack_quantity",
        label="Packung",
        width=10,
        hint="Menge in der Packung, wie sie im Laden steht: 500, 1, 12.",
        synonyms=("menge", "inhalt", "packungsgröße", "packungsgroesse", "größe", "groesse"),
    ),
    SheetColumn(
        key="pack_unit",
        label="Einheit",
        width=10,
        hint="g, kg, ml, l, Stk, Pkg, Dose, Glas, Bund.",
        synonyms=("einheit", "maßeinheit", "masseinheit", "unit"),
    ),
    SheetColumn(
        key="price",
        label="Preis",
        width=10,
        hint="Preis für genau diese Packung, in Euro: 1,99.",
        synonyms=("preis in €", "eur", "euro", "betrag", "kosten"),
    ),
    SheetColumn(
        key="promotion",
        label="Angebot",
        width=9,
        hint='ja / nein. Bei "ja" ist "gültig bis" Pflicht.',
        synonyms=("aktion", "sonderangebot", "rabatt", "aktionspreis"),
    ),
    SheetColumn(
        key="valid_from",
        label="gültig von",
        width=13,
        hint="Beginn des Angebots (TT.MM.JJJJ). Wichtig für Angebotszyklen.",
        synonyms=("gueltig von", "angebot von", "aktion von", "von"),
    ),
    SheetColumn(
        key="valid_to",
        label="gültig bis",
        width=13,
        hint="Letzter Angebotstag (TT.MM.JJJJ).",
        synonyms=("gueltig bis", "angebot bis", "aktion bis", "bis"),
    ),
    SheetColumn(
        key="observed_on",
        label="gesehen am",
        width=13,
        hint="Wann du den Preis notiert hast. Leer = Tag des Imports.",
        synonyms=("datum", "erfasst am", "notiert am", "stand"),
    ),
    SheetColumn(
        key="note",
        label="Notiz",
        width=30,
        hint="Freitext, z. B. „12er-Kasten, Pfand extra“.",
        synonyms=("bemerkung", "kommentar", "hinweis"),
    ),
)

_COLUMN_BY_KEY: Final = {column.key: column for column in TEMPLATE_COLUMNS}

# Kopfzeilen-Erkennung: Beschriftung und Synonyme auf dieselbe Normalform.
_HEADER_LOOKUP: Final[dict[str, str]] = {
    variant: column.key
    for column in TEMPLATE_COLUMNS
    for variant in (column.label.strip().lower(), *column.synonyms)
}

_UNIT_CHOICES: Final = ("g", "kg", "ml", "l", "Stk", "Pkg", "Dose", "Glas", "Bund")
_YES_NO_CHOICES: Final = ("ja", "nein")

_TRUE_WORDS: Final = frozenset({"ja", "j", "x", "true", "wahr", "1", "yes", "y"})
_FALSE_WORDS: Final = frozenset({"nein", "n", "false", "falsch", "0", "no", "-"})

_PRIVATE_LABEL_WORDS: Final = frozenset(
    {"eigenmarke", "hausmarke", "no name", "noname", "eigen", "discounter"}
)

_HEADER_FILL: Final = PatternFill("solid", fgColor="1F3A5F")
_HEADER_FONT: Final = Font(color="FFFFFF", bold=True, size=11)
_PREFILL_FONT: Final = Font(color="404040")
_THIN_BORDER: Final = Border(bottom=Side(style="thin", color="D0D0D0"))


# --------------------------------------------------------------------------
# Vorlage bauen
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateIngredient:
    """Eine Zutat, die in der Vorlage schon als Zeile stehen soll.

    pack_quantity/pack_unit sind die zuletzt bekannten Packungsangaben. Sie
    werden mitgegeben, damit der Nutzer nur noch die Zahl in die Preisspalte
    tippt - Namen und Einheiten abzutippen ist genau der Schritt, bei dem
    Schreibvarianten entstehen und der Preisverlauf auseinanderfaellt."""

    name: str
    pack_quantity: Decimal | None = None
    pack_unit: str | None = None
    category: str | None = None


def build_template_workbook(
    ingredients: list[TemplateIngredient],
    store_names: list[str] | None = None,
) -> bytes:
    """Vorlage als XLSX-Bytes: ein Blatt "Preise" mit Kopfzeile, vorbefuellten
    Zutaten und Auswahllisten, dazu ein Blatt "Anleitung".

    Die Zutaten stehen bereits drin, weil die Zuordnung beim Import ueber den
    NAMEN laeuft. Wer den Namen selbst tippt, erzeugt frueher oder spaeter
    "Gerolsteiner Medium" neben "Gerolsteiner med." - zwei Preisreihen, von
    denen keine einen Zyklus zeigt."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None  # frische Mappe hat immer ein Blatt
    sheet.title = SHEET_NAME

    _write_header(sheet)
    last_row = _write_prefilled_rows(sheet, ingredients)
    _apply_validation(sheet, store_names or [], last_row)

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(TEMPLATE_COLUMNS))}1"

    _write_guide_sheet(workbook.create_sheet(GUIDE_SHEET_NAME))

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_header(sheet: Worksheet) -> None:
    for index, column in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=index, value=column.label)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        # Die Erklaerung haengt als Kommentar an der Kopfzelle statt in einer
        # Hinweiszeile darunter: eine Hinweiszeile waere beim Einlesen eine
        # Datenzeile und muesste gesondert uebersprungen werden.
        cell.comment = openpyxl.comments.Comment(column.hint, "Finanzplaner", height=90, width=320)
        sheet.column_dimensions[get_column_letter(index)].width = column.width
    sheet.row_dimensions[1].height = 22


def _write_prefilled_rows(sheet: Worksheet, ingredients: list[TemplateIngredient]) -> int:
    """Bekannte Zutaten als Zeilen, Preisspalte leer. Gibt die letzte
    beschreibbare Zeilennummer zurueck (inklusive Leerzeilenvorrat)."""
    columns = {column.key: index for index, column in enumerate(TEMPLATE_COLUMNS, start=1)}
    row = 2
    for ingredient in sorted(ingredients, key=lambda item: item.name.strip().lower()):
        name_cell = sheet.cell(row=row, column=columns["ingredient"], value=ingredient.name)
        name_cell.font = _PREFILL_FONT
        name_cell.border = _THIN_BORDER
        if ingredient.pack_quantity is not None:
            quantity = sheet.cell(row=row, column=columns["pack_quantity"])
            quantity.value = float(ingredient.pack_quantity)
            quantity.font = _PREFILL_FONT
        if ingredient.pack_unit:
            unit = sheet.cell(row=row, column=columns["pack_unit"], value=ingredient.pack_unit)
            unit.font = _PREFILL_FONT
        sheet.cell(row=row, column=columns["price"]).number_format = "#,##0.00 €"
        row += 1

    for spare in range(row, row + _SPARE_ROWS):
        sheet.cell(row=spare, column=columns["price"]).number_format = "#,##0.00 €"
    return row + _SPARE_ROWS - 1


def _apply_validation(sheet: Worksheet, store_names: list[str], last_row: int) -> None:
    """Auswahllisten fuer die Spalten, in denen freie Eingabe nur schadet.

    Bewusst `showErrorMessage=False`: die Liste ist ein Angebot, keine Sperre.
    Eine Datei, die woanders erzeugt wurde, soll nicht daran scheitern, dass
    "Wahr" statt "ja" drinsteht - das Einlesen versteht beides."""
    columns = {column.key: index for index, column in enumerate(TEMPLATE_COLUMNS, start=1)}

    def add_list(key: str, choices: list[str]) -> None:
        if not choices:
            return
        # Excel-Formelgrenze: eine Inline-Liste darf nicht beliebig lang sein.
        joined = ",".join(choice.replace(",", " ") for choice in choices)
        if len(joined) > 250:
            return
        validation = DataValidation(
            type="list", formula1=f'"{joined}"', allow_blank=True, showErrorMessage=False
        )
        sheet.add_data_validation(validation)
        letter = get_column_letter(columns[key])
        validation.add(f"{letter}2:{letter}{last_row}")

    add_list("organic", list(_YES_NO_CHOICES))
    add_list("promotion", list(_YES_NO_CHOICES))
    add_list("pack_unit", list(_UNIT_CHOICES))
    add_list("store", sorted({name.strip() for name in store_names if name.strip()}))

    for key in ("valid_from", "valid_to", "observed_on"):
        letter = get_column_letter(columns[key])
        for row in range(2, last_row + 1):
            sheet[f"{letter}{row}"].number_format = "DD.MM.YYYY"


_GUIDE_TEXT: Final[tuple[tuple[str, str], ...]] = (
    ("Wofür diese Datei da ist", ""),
    (
        "",
        "Jede Zeile ist eine SICHTUNG: „am 5.8. bei Aldi, 500 g Nudeln, 0,89 €“. "
        "Beim Import wird sie angehängt, nicht ersetzt. Deshalb entsteht mit der "
        "Zeit ein Preisverlauf, aus dem sich günstigster Laden, Preisanstieg und "
        "Angebotszyklen ablesen lassen.",
    ),
    (
        "",
        "Die Datei muss nichts erinnern. Sie darf jede Woche nur die aktuellen "
        "Angebote enthalten — das Gedächtnis liegt in der App.",
    ),
    ("", ""),
    ("So füllst du sie", ""),
    ("", "Pflicht sind nur drei Spalten: Zutat, Preis und Packung + Einheit."),
    (
        "",
        "Alles andere macht die Auswertung besser, aber keine Zeile scheitert daran. "
        "Ohne Laden gilt der Preis als „irgendwo gesehen“ und zählt weiter für den "
        "Verlauf, nur nicht für den Ladenvergleich.",
    ),
    (
        "",
        "Der Preis gilt für GENAU die Packung daneben. „1,99 für 1 kg“ und "
        "„1,29 für 500 g“ werden intern auf denselben Grundpreis gebracht und sind "
        "damit vergleichbar.",
    ),
    ("", ""),
    ("Angebote", ""),
    (
        "",
        "Angebot = ja braucht ein „gültig bis“. Ein Angebot ohne Ende ist kein "
        "Angebot, sondern der neue Normalpreis — und würde nach Ablauf still "
        "weiterrechnen.",
    ),
    (
        "",
        "„gültig von“ ist die wichtigere der beiden Datumsspalten: nur mit dem "
        "Beginn lässt sich später erkennen, ob etwas alle 4 oder alle 7 Wochen "
        "im Angebot ist.",
    ),
    (
        "",
        "Angebotspreise fließen NICHT in die Gerichtskosten. Ein Gericht wird nicht "
        "dauerhaft billiger, weil Butter diese Woche im Prospekt steht. Sie wirken "
        "nur auf die Einkaufsliste der betroffenen Woche.",
    ),
    ("", ""),
    ("Zutatennamen", ""),
    (
        "",
        "Die vorbefüllten Namen bitte stehen lassen. Zugeordnet wird über den Namen "
        "(Groß-/Kleinschreibung egal). „Gerolsteiner Medium“ und „Gerolsteiner med.“ "
        "sind zwei getrennte Preisreihen, und keine davon zeigt einen Zyklus.",
    ),
    (
        "",
        "Neue Lebensmittel einfach unten anhängen — sie werden beim Import angelegt.",
    ),
    ("", ""),
    ("Mehrfach importieren", ""),
    (
        "",
        "Dieselbe Datei zweimal einzulesen ist folgenlos: identische Zeilen werden "
        "erkannt und übersprungen. Eine neue Woche mit neuen Preisen ergibt neue "
        "Sichtungen neben den alten.",
    ),
)


def _write_guide_sheet(sheet: Worksheet) -> None:
    sheet.column_dimensions["A"].width = 4
    sheet.column_dimensions["B"].width = 104
    for index, (heading, body) in enumerate(_GUIDE_TEXT, start=1):
        if heading:
            cell = sheet.cell(row=index, column=2, value=heading)
            cell.font = Font(bold=True, size=12, color="1F3A5F")
        elif body:
            cell = sheet.cell(row=index, column=2, value=body)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            sheet.row_dimensions[index].height = 15 * (len(body) // 95 + 1)


# --------------------------------------------------------------------------
# Vorlage einlesen
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedObservation:
    """Eine gelesene, vollstaendig gepruefte Zeile - noch NICHT gespeichert."""

    row_number: int
    ingredient_name: str
    ingredient_key: str
    store_name: str | None
    store_location: str | None
    brand: str | None
    quality_tier: str
    brand_tier: str
    pack_quantity: Decimal
    pack_unit: str | None
    base_quantity: Decimal
    base_family: str
    price_minor: int
    is_promotion: bool
    offer_valid_from: date | None
    offer_valid_to: date | None
    observed_on: date
    note: str | None
    # Erkennungsmerkmal derselben Sichtung ueber mehrere Importe hinweg.
    fingerprint: str

    @property
    def unit_price_minor_per_base(self) -> Decimal:
        """Grundpreis je Basiseinheit - nur fuer die Anzeige in der Vorschau.
        Verbindlich gerechnet wird er in der Datenbank."""
        return Decimal(self.price_minor) / self.base_quantity


@dataclass(frozen=True)
class RowProblem:
    """Eine Zeile, die nicht uebernommen werden kann. Sie verschwindet NICHT
    stillschweigend: eine halb gelesene Datei darf nicht wie eine ganz gelesene
    aussehen."""

    row_number: int
    ingredient_name: str | None
    message: str


@dataclass(frozen=True)
class SheetParseResult:
    sheet_name: str
    header_row: int
    observations: list[ParsedObservation]
    problems: list[RowProblem]
    # Vorbefuellte Zeilen, die der Nutzer schlicht nicht ausgefuellt hat. Sie
    # sind der Normalfall einer exportierten Vorlage und ausdruecklich KEIN
    # Problem - sie als Fehler zu melden wuerde die echten Fehler in einer
    # Liste aus 200 Nichtigkeiten begraben.
    untouched_rows: int
    # Spaltenbeschriftungen, die nicht zugeordnet werden konnten. Sichtbar,
    # damit ein Vertipper in der Kopfzeile nicht als "Spalte war leer" endet.
    unmapped_headers: list[str]


def parse_workbook(
    file_bytes: bytes,
    *,
    sheet_name: str | None = None,
    today: date | None = None,
) -> SheetParseResult:
    """Liest eine Preisvorlage. Erwartet, dass die Datei vorher durch
    project_cost_import_security.check_xlsx_structure gelaufen ist - hier wird
    nur noch der Inhalt geprueft, nicht die ZIP-Huelle."""
    reference_day = today or date.today()
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl wirft sehr verschiedenes
        raise PriceSheetError("Die Datei liess sich nicht als Excel-Mappe oeffnen.") from exc

    try:
        sheet = _select_sheet(workbook, sheet_name)
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(list(row))
            # Abbrechen statt erst alles einlesen und dann pruefen: bei einer
            # Datei mit einer Million Zeilen waere die Pruefung sonst genau
            # das, wovor sie schuetzen soll.
            if len(rows) > MAX_SHEET_ROWS:
                raise PriceSheetError(
                    f"Die Datei hat mehr als {MAX_SHEET_ROWS:,} Zeilen. Bitte aufteilen "
                    "(zum Beispiel je Woche oder je Haendler eine Datei)."
                    .replace(",", ".")
                )
    finally:
        workbook.close()

    header_row, mapping, unmapped = _locate_header(rows)

    observations: list[ParsedObservation] = []
    problems: list[RowProblem] = []
    untouched = 0
    seen_fingerprints: set[str] = set()

    for offset, raw_row in enumerate(rows[header_row:], start=header_row + 1):
        if all(_is_blank(value) for value in raw_row):
            continue
        values = {key: _cell(raw_row, index) for key, index in mapping.items()}
        # Eine exportierte Vorlage bringt jede bekannte Zutat als Zeile mit -
        # Name, Packung, Einheit vorbelegt. Wer nur zehn Preise eintraegt,
        # laesst zweihundert davon stehen. Solche Zeilen sind unberuehrt, nicht
        # fehlerhaft: gemeldet wird nur ihre Anzahl.
        if _is_untouched(values):
            untouched += 1
            continue
        try:
            observation = _build_observation(offset, values, reference_day)
        except _RowError as error:
            problems.append(
                RowProblem(
                    row_number=offset,
                    ingredient_name=_text(values.get("ingredient")),
                    message=str(error),
                )
            )
            continue
        # Doppelte Zeilen INNERHALB einer Datei: die erste gewinnt, die zweite
        # wird gemeldet statt still geschluckt.
        if observation.fingerprint in seen_fingerprints:
            problems.append(
                RowProblem(
                    row_number=offset,
                    ingredient_name=observation.ingredient_name,
                    message="Steht in dieser Datei schon einmal identisch drin.",
                )
            )
            continue
        seen_fingerprints.add(observation.fingerprint)
        observations.append(observation)

    return SheetParseResult(
        sheet_name=sheet.title,
        header_row=header_row,
        observations=observations,
        problems=problems,
        untouched_rows=untouched,
        unmapped_headers=unmapped,
    )


# Felder, deren Vorhandensein eine Zeile als "angefasst" ausweist. Packung und
# Einheit fehlen hier bewusst: die stehen in der exportierten Vorlage bereits
# drin, ohne dass jemand etwas getan haette.
_TOUCH_FIELDS: Final = (
    "price",
    "store",
    "location",
    "brand",
    "organic",
    "promotion",
    "valid_from",
    "valid_to",
    "observed_on",
    "note",
)


def _is_untouched(values: dict[str, Any]) -> bool:
    return all(_is_blank(values.get(field)) for field in _TOUCH_FIELDS)


class _RowError(ValueError):
    """Eine einzelne Zeile ist unbrauchbar - der Rest der Datei nicht."""


def make_observation(
    *,
    row_number: int = 0,
    ingredient: str | None,
    price: Any,
    pack_quantity: Any = None,
    pack_unit: str | None = None,
    store: str | None = None,
    location: str | None = None,
    brand: str | None = None,
    organic: Any = None,
    promotion: Any = None,
    valid_from: Any = None,
    valid_to: Any = None,
    observed_on: Any = None,
    note: str | None = None,
    today: date | None = None,
) -> ParsedObservation:
    """Eine Sichtung aus Einzelwerten bauen - fuer die Handeingabe und fuer die
    Uebernahme bestaetigter Vorschauzeilen.

    Wichtig: dieselbe Funktion wie beim Dateieinlesen, damit eine Zeile aus der
    Vorschau nach der Bestaetigung nicht anders geprueft wird als beim
    Einlesen. Abgeleitete Werte (Basiseinheit, Fingerabdruck) entstehen
    ausschliesslich hier und werden nie vom Aufrufer uebernommen - der Client
    darf Eingaben schicken, keine Rechenergebnisse.

    Wirft `_RowError` mit einem vorlegbaren Satz, wenn die Werte nicht taugen.
    """
    return _build_observation(
        row_number,
        {
            "ingredient": ingredient,
            "price": price,
            "pack_quantity": pack_quantity,
            "pack_unit": pack_unit,
            "store": store,
            "location": location,
            "brand": brand,
            "organic": organic,
            "promotion": promotion,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "observed_on": observed_on,
            "note": note,
        },
        today or date.today(),
    )


# Der Aufrufer soll den Fehler fangen koennen, ohne den privaten Namen zu
# kennen: dieselbe Klasse, oeffentlich benannt.
RowValidationError = _RowError


def _select_sheet(workbook: Any, sheet_name: str | None) -> Any:
    if sheet_name:
        if sheet_name not in workbook.sheetnames:
            raise PriceSheetError(
                f"Blatt „{sheet_name}“ gibt es nicht. Vorhanden: "
                f"{', '.join(workbook.sheetnames)}."
            )
        return workbook[sheet_name]
    # Ohne Vorgabe: das Blatt der Vorlage, sonst das erste. Die Anleitung
    # traegt keine Kopfzeile und wuerde sonst als leere Datei gelesen.
    if SHEET_NAME in workbook.sheetnames:
        return workbook[SHEET_NAME]
    if not workbook.sheetnames:
        raise PriceSheetError("Die Datei enthaelt kein Tabellenblatt.")
    return workbook[workbook.sheetnames[0]]


def _locate_header(rows: list[list[Any]]) -> tuple[int, dict[str, int], list[str]]:
    """Findet die Kopfzeile und ordnet Spalten ueber ihre Beschriftung zu.

    Ueber die Beschriftung statt ueber die Position, weil eine Datei aus
    fremder Hand die Spalten anders sortiert oder eigene dazwischen hat."""
    for index, row in enumerate(rows[:_MAX_HEADER_SCAN_ROWS]):
        mapping: dict[str, int] = {}
        unmapped: list[str] = []
        for position, value in enumerate(row):
            label = _text(value)
            if label is None:
                continue
            key = _HEADER_LOOKUP.get(_normalize_header(label))
            if key is None:
                unmapped.append(label)
            elif key not in mapping:
                mapping[key] = position
        if "ingredient" in mapping and "price" in mapping:
            return index + 1, mapping, unmapped

    expected = ", ".join(column.label for column in TEMPLATE_COLUMNS[:3])
    raise PriceSheetError(
        "In den ersten Zeilen wurde keine Kopfzeile gefunden. Erwartet werden "
        f"mindestens die Spalten „Zutat“ und „Preis“ (Vorlage beginnt mit: {expected})."
    )


def _normalize_header(label: str) -> str:
    """Kopfzeilen-Beschriftung auf eine vergleichbare Form: klein, ohne Rand,
    ohne Klammerzusaetze wie "Preis (€)" und ohne doppelte Leerzeichen."""
    cleaned = re.sub(r"\([^)]*\)", " ", label).strip().lower()
    cleaned = cleaned.replace("*", " ").replace(":", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _cell(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value).strip() or None


def _build_observation(
    row_number: int, values: dict[str, Any], today: date
) -> ParsedObservation:
    name = _text(values.get("ingredient"))
    if not name:
        raise _RowError("Ohne Zutat kann die Zeile niemandem zugeordnet werden.")
    if len(name) > 160:
        raise _RowError("Der Zutatenname ist laenger als 160 Zeichen.")

    price_minor = _parse_price(values.get("price"))
    if price_minor is None:
        # Der haeufigste Fall in einer vorbefuellten Vorlage: die Zeile wurde
        # schlicht nicht ausgefuellt. Das ist kein Fehler des Nutzers und wird
        # deshalb neutral formuliert.
        raise _RowError("Kein Preis eingetragen.")

    pack_quantity = _parse_decimal(values.get("pack_quantity"))
    if pack_quantity is None:
        pack_quantity = Decimal(1)
    if pack_quantity <= 0:
        raise _RowError("Die Packungsmenge muss groesser als 0 sein.")

    pack_unit = _text(values.get("pack_unit"))
    base_quantity, base_family = to_base_amount(pack_quantity, pack_unit)
    if base_quantity <= 0:
        raise _RowError("Die Packungsmenge muss groesser als 0 sein.")

    is_promotion = _parse_flag(values.get("promotion")) or False
    valid_from = _parse_date(values.get("valid_from"), "gueltig von")
    valid_to = _parse_date(values.get("valid_to"), "gueltig bis")
    if is_promotion and valid_to is None:
        raise _RowError(
            "Angebot ohne „gueltig bis“. Ein Angebot ohne Ende waere der neue "
            "Normalpreis und wuerde nach Ablauf still weiterrechnen."
        )
    if valid_from and valid_to and valid_from > valid_to:
        raise _RowError("„gueltig von“ liegt nach „gueltig bis“.")

    observed_on = _parse_date(values.get("observed_on"), "gesehen am") or valid_from or today
    if observed_on > today:
        raise _RowError("„gesehen am“ liegt in der Zukunft.")

    brand = _text(values.get("brand"))
    is_organic = _parse_flag(values.get("organic")) or False

    return ParsedObservation(
        row_number=row_number,
        ingredient_name=name,
        ingredient_key=name.strip().lower(),
        store_name=_text(values.get("store")),
        store_location=_text(values.get("location")),
        brand=brand,
        quality_tier="organic" if is_organic else "standard",
        brand_tier=_brand_tier(brand),
        pack_quantity=pack_quantity,
        pack_unit=pack_unit,
        base_quantity=base_quantity,
        base_family=base_family,
        price_minor=price_minor,
        is_promotion=is_promotion,
        offer_valid_from=valid_from,
        offer_valid_to=valid_to,
        observed_on=observed_on,
        note=_text(values.get("note")),
        fingerprint=_fingerprint(
            name.strip().lower(),
            _text(values.get("store")),
            _text(values.get("location")),
            brand,
            is_organic,
            is_promotion,
            valid_from,
            observed_on,
            pack_quantity,
            normalize_unit(pack_unit),
            price_minor,
        ),
    )


def _brand_tier(brand: str | None) -> str:
    """Leere Marke heisst NICHT Eigenmarke, sondern "nicht angegeben". Diese
    beiden gleichzusetzen wuerde jeder unausgefuellten Zeile eine Aussage
    unterschieben, die niemand getroffen hat."""
    if brand is None:
        return "unknown"
    return "private_label" if brand.strip().lower() in _PRIVATE_LABEL_WORDS else "branded"


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        # Ueber str, nicht direkt: Decimal(1.15) waere 1.14999999999999991118...
        return Decimal(str(value))
    text_value = str(value).strip()
    if not text_value:
        return None
    # Waehrungszeichen und Tausenderpunkte weg, deutsches Komma zu Punkt.
    cleaned = text_value.replace("€", "").replace("EUR", "").replace("eur", "").strip()
    cleaned = re.sub(r"[^\d,.\-]", "", cleaned)
    if "," in cleaned and "." in cleaned:
        # "1.234,56" - der Punkt ist Tausendertrenner.
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_price(value: Any) -> int | None:
    """Euro-Angabe in ganze Cent. Rundung kaufmaennisch, wie im uebrigen
    Rechenkern (grocery_pricing_service._round_minor)."""
    amount = _parse_decimal(value)
    if amount is None:
        return None
    if amount < 0:
        raise _RowError("Der Preis ist negativ.")
    if amount > Decimal(10_000):
        raise _RowError("Der Preis ist unplausibel hoch (ueber 10.000 €).")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _parse_flag(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text_value = str(value).strip().lower()
    if not text_value:
        return None
    if text_value in _TRUE_WORDS:
        return True
    if text_value in _FALSE_WORDS:
        return False
    raise _RowError(f"„{value}“ ist weder ja noch nein.")


_DATE_PATTERNS: Final = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def _parse_date(value: Any, label: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(text_value, pattern).date()
        except ValueError:
            continue
    raise _RowError(f"„{text_value}“ ist in Spalte „{label}“ kein lesbares Datum (TT.MM.JJJJ).")


def _fingerprint(*parts: Any) -> str:
    """Stabiler Erkennungswert einer Sichtung.

    Bewusst OHNE die Notiz: eine nachtraeglich ergaenzte Bemerkung macht aus
    derselben Sichtung keine zweite. Mit „gesehen am“ UND „gueltig von“, damit
    derselbe Angebotspreis in zwei aufeinanderfolgenden Wochen zwei Sichtungen
    bleibt - genau die Wiederholung ist das Signal fuer einen Zyklus."""
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
