"""Schemas fuer die Haushaltssicherung.

Die Sicherung selbst ist eine Datei und kommt nicht durch diese Modelle - hier
steht nur, was die Oberflaeche ueber sie erfahren muss, um eine Entscheidung
treffen zu koennen.
"""

from typing import Literal

from pydantic import BaseModel, Field


class BackupTableStat(BaseModel):
    table: str
    rows: int


class BackupInfoResponse(BaseModel):
    """Was eine Sicherung dieses Haushalts jetzt enthalten wuerde."""

    household_name: str
    table_count: int
    total_rows: int
    # Nur gefuellte Tabellen, absteigend. Leere stehen nicht in der Sicherung.
    tables: list[BackupTableStat]
    # Was eine Sicherung ausdruecklich NICHT enthaelt. Sichtbar, damit sie
    # nicht mehr verspricht als sie haelt.
    excluded: list[str]


class TablePreview(BaseModel):
    table: str
    rows_in_file: int
    rows_in_household: int
    # ersetzen        = wird geleert und aus der Datei neu aufgebaut
    # ueberschreiben  = Zeile fuer Zeile ersetzt, Zusaetzliches bleibt stehen
    # ergaenzen       = anfuegendes Protokoll, Vorhandenes bleibt unberuehrt
    # uebersprungen   = kommt nie aus einer Sicherung (Zugriffsrechte)
    handling: Literal["ersetzen", "ueberschreiben", "ergaenzen", "uebersprungen"]
    note: str | None = None


class ImportPreviewResponse(BaseModel):
    """Was ein Einspielen bewirken wuerde. Pflichtschritt vor dem Einspielen."""

    household_name: str
    source_household_name: str
    source_household_id: str
    created_at: str
    # Stammt die Sicherung vom selben Schemastand? Kein Hinderungsgrund -
    # fehlende Spalten werden NULL, unbekannte ignoriert -, aber die
    # Oberflaeche soll es sagen koennen.
    schema_matches: bool
    rows_in_file: int
    rows_in_household: int
    tables: list[TablePreview]
    # Tabellen aus der Datei, die es hier nicht (mehr) gibt.
    unknown_tables: list[str]


class ImportResponse(BaseModel):
    household_name: str
    deleted_rows: int
    imported_rows: int
    imported_per_table: dict[str, int]
    skipped_tables: list[str]
    # Verweise auf Nutzerkonten, die es in dieser Instanz nicht gibt, und die
    # deshalb geleert wurden. Bei einer Sicherung aus derselben Instanz ist
    # das immer 0.
    cleared_user_references: int
    schema_matches: bool


class ImportConfirm(BaseModel):
    # Der Haushaltsname, wie er in der Oberflaeche steht. Wird serverseitig
    # verglichen - dieselbe Absicherung wie beim Leeren des Haushalts: eine
    # Verwechslung ist hier der teuerste denkbare Fehler.
    confirm_name: str = Field(min_length=1, max_length=200)
