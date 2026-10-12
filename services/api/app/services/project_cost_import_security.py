"""Sichere Vorpruefung fuer den Kostenplan-Import (Plan 10.2): Groessen-,
Zeilen- und Blattlimit, Ablehnung von Makros, externen Links, eingebetteten
Objekten und (indirekt) passwortgeschuetzten Dateien.

Eigenstaendig statt Wiederverwendung von worker/imports/security.py: das ist
eine reine ZIP-Bomb-/Zeilenpruefung fuer Bankauszuege (ein schmales Format
pro Lauf, keine Struktur-Inspektion). Hier kommt zusaetzlich generisches
XLSX mit potenziellen Makros, externen Links und eingebetteten Objekten vor
(Plan 10.2: "Makros, externe Links, eingebettete Objekte und passwort-
geschuetzte Dateien werden abgelehnt"), das eine eigene Struktur-Analyse der
ZIP-Huelle braucht, bevor openpyxl die Datei inhaltlich oeffnet."""

import io
import zipfile
from typing import Final

from defusedxml import ElementTree as safe_ET  # noqa: N812

MAX_FILE_SIZE_BYTES: Final = 5 * 1024 * 1024
MAX_DATA_ROWS: Final = 5_000
MAX_SHEETS: Final = 20

# Grosszuegiger als bei Bankauszuegen (die sind reiner Text): ein XLSX-
# Kostenplan darf Formatierung/Formeln-Cache mitbringen, ohne faelschlich als
# ZIP-Bomb zu gelten. 100 MB entpackt aus einer <=5-MB-Quelldatei bleibt ein
# eindeutiges Warnsignal.
MAX_ZIP_UNCOMPRESSED_BYTES: Final = 100 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO: Final = 100

_MACRO_EXTENSIONS: Final = (".xlsm", ".xlsb", ".xls")
_MACRO_ZIP_ENTRY: Final = "xl/vbaproject.bin"
_EXTERNAL_LINK_PREFIX: Final = "xl/externallinks/"
_EMBEDDING_PREFIX: Final = "xl/embeddings/"

# Pfadbasierte Erkennung (oben) reicht allein nicht: OOXML adressiert Teile
# ueber Beziehungen (.rels-Dateien mit Type-/TargetMode-Attributen), nicht
# ueber feste Pfade - ausser den paketweiten Pflichtteilen ist kein Pfad vom
# Standard vorgeschrieben. Ein von Hand gebautes Paket kann ein VBA-Projekt,
# ein externes OLE-Objekt oder eine externe Verknuepfung unter einem voellig
# anderen internen Pfad ablegen und damit die Pfadpruefung oben umgehen,
# waehrend Excel die Beziehung trotzdem ganz normal aufloest. Deshalb zusaetzlich
# jede .rels-Datei strukturell auswerten (mit defusedxml gegen XXE, da diese
# XML-Teile aus einer nicht vertrauenswuerdigen Datei stammen).
_DANGEROUS_RELATIONSHIP_TYPE_SUFFIXES: Final = frozenset(
    {"vbaproject", "oleobject", "externallink", "externallinkpath", "attachedtemplate", "package"}
)
# hyperlink ist der einzige verbreitete, harmlose Fall von TargetMode="External"
# (ein Weblink in einer Zelle, z. B. im Feld "Anbieter") - jede andere externe
# Zielbeziehung (Arbeitsmappenverweis, verknuepftes statt eingebettetes
# OLE-Objekt, verknuepfte Dokumentvorlage) bleibt abgelehnt.
_BENIGN_EXTERNAL_TARGET_TYPE_SUFFIXES: Final = frozenset({"hyperlink"})


def _relationship_type_suffix(rel_type: str) -> str:
    return rel_type.rstrip("/").rsplit("/", 1)[-1].lower()


def _check_relationships(zf: zipfile.ZipFile) -> None:
    for entry_name in zf.namelist():
        if not entry_name.endswith(".rels"):
            continue
        try:
            raw = zf.read(entry_name)
        except KeyError:
            continue
        try:
            root = safe_ET.fromstring(raw)
        except Exception as exc:
            raise CostImportSecurityError(
                "Datei enthaelt ungueltige Beziehungsdefinitionen (.rels)."
            ) from exc
        for rel in root:
            rel_type = rel.get("Type") or ""
            suffix = _relationship_type_suffix(rel_type)
            target_mode = (rel.get("TargetMode") or "").lower()
            if suffix in _DANGEROUS_RELATIONSHIP_TYPE_SUFFIXES:
                raise CostImportSecurityError(
                    "Datei enthaelt ein Makroprojekt, eingebettetes Objekt oder eine "
                    "externe Verknuepfung."
                )
            if target_mode == "external" and suffix not in _BENIGN_EXTERNAL_TARGET_TYPE_SUFFIXES:
                raise CostImportSecurityError("Datei enthaelt externe Verknuepfungen.")


class CostImportSecurityError(ValueError):
    """Datei verletzt eine Sicherheitsgrenze - der Import wird abgelehnt,
    bevor irgendein Parser sie inhaltlich anfasst."""


def check_file_size(file_bytes: bytes) -> None:
    if len(file_bytes) == 0:
        raise CostImportSecurityError("Datei ist leer.")
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise CostImportSecurityError(
            f"Datei ist zu gross ({len(file_bytes)} Bytes, Limit {MAX_FILE_SIZE_BYTES} Bytes)."
        )


def check_row_count(row_count: int) -> None:
    if row_count > MAX_DATA_ROWS:
        raise CostImportSecurityError(
            f"Datei hat zu viele Zeilen ({row_count}, Limit {MAX_DATA_ROWS})."
        )


def check_sheet_count(sheet_count: int) -> None:
    if sheet_count > MAX_SHEETS:
        raise CostImportSecurityError(
            f"Datei hat zu viele Blaetter ({sheet_count}, Limit {MAX_SHEETS})."
        )


def check_xlsx_structure(file_bytes: bytes, filename: str) -> None:
    """Prueft die ZIP-Huelle eines XLSX auf ZIP-Bomb, Makros, externe Links
    und eingebettete Objekte, bevor openpyxl die Datei laedt. Ein
    fehlschlagendes ZIP-Oeffnen (BadZipFile) ist typisch fuer passwort-
    geschuetzte OOXML-Dateien (die liegen als verschluesselter CFB-Container
    vor, nicht als ZIP) und wird entsprechend als solches gemeldet."""
    lower_name = filename.lower()
    if any(lower_name.endswith(ext) for ext in _MACRO_EXTENSIONS):
        raise CostImportSecurityError(
            "Makrofaehige oder Legacy-Exceldateien (.xlsm/.xlsb/.xls) werden nicht unterstuetzt."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            total_uncompressed = 0
            for info in zf.infolist():
                name_lower = info.filename.lower()
                if name_lower == _MACRO_ZIP_ENTRY:
                    raise CostImportSecurityError("Datei enthaelt ein Makroprojekt (VBA).")
                if name_lower.startswith(_EXTERNAL_LINK_PREFIX):
                    raise CostImportSecurityError("Datei enthaelt externe Verknuepfungen.")
                if name_lower.startswith(_EMBEDDING_PREFIX):
                    raise CostImportSecurityError("Datei enthaelt eingebettete Objekte.")
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise CostImportSecurityError(
                        "ZIP-Inhalt ist zu gross (moeglicher ZIP-Bomb-Versuch)."
                    )
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_ZIP_COMPRESSION_RATIO:
                        raise CostImportSecurityError(
                            "Verdaechtiges Kompressionsverhaeltnis (moeglicher ZIP-Bomb-Versuch)."
                        )
            _check_relationships(zf)
    except zipfile.BadZipFile as exc:
        raise CostImportSecurityError(
            "Datei ist kein gueltiges XLSX (moeglicherweise passwortgeschuetzt oder beschaedigt)."
        ) from exc
