"""Schemas fuer den Datenbank-Explorer.

Zeilen kommen bewusst als `dict[str, Any]` und nicht als getyptes Modell je
Tabelle: es gibt 79 davon, sie aendern sich mit dem Schema, und der Explorer
soll zeigen was da IST - nicht was ein handgepflegtes Modell davon kennt. Die
Typinformation reist stattdessen in `columns` mit, damit die Oberflaeche
formatieren kann.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool


class TableSummary(BaseModel):
    name: str
    group: str
    row_count: int
    # Loeschen braucht einen einspaltigen Primaerschluessel. Tabellen ohne
    # bleiben lesbar - eine Zeile ohne eindeutige Adresse zu loeschen waere
    # geraten, und Raten ist an dieser Stelle besonders teuer.
    is_deletable: bool
    primary_key: str | None


class TableListResponse(BaseModel):
    tables: list[TableSummary]
    # Summe ueber alle Tabellen dieses Haushalts - die eine Zahl, die sagt
    # "so viel liegt hier".
    total_rows: int


class RowPageResponse(BaseModel):
    table: str
    columns: list[ColumnInfo]
    primary_key: str | None
    rows: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class DeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


class CascadeEffect(BaseModel):
    table: str
    rows: int
    # 'delete' = wird mitgeloescht, 'detach' = verliert nur den Verweis und
    # bleibt sonst stehen.
    effect: Literal["delete", "detach"]
    via_column: str
    depth: int


class DeletePreviewResponse(BaseModel):
    table: str
    # Wie viele der angefragten Zeilen es ueberhaupt (noch) gibt.
    direct_rows: int
    effects: list[CascadeEffect]
    # Summe der mitgeloeschten Zeilen ueber alle Ebenen, ohne die direkten.
    cascade_total: int
    # Zeilen, die nur ihren Verweis verlieren. Getrennt ausgewiesen, weil das
    # harmlos ist - aber ohne Erklaerung beunruhigend aussieht.
    detach_total: int


class PurgePreviewResponse(BaseModel):
    """Was ein Leeren des Haushalts wegnehmen wuerde."""

    household_name: str
    # Nur Tabellen mit Inhalt, absteigend nach Menge.
    tables: list[TableSummary]
    total_rows: int
    # Bleibt ausdruecklich stehen: Zugehoerigkeit, Kategorienbaum und die
    # anfuegenden Protokolle. Sichtbar, damit "leeren" nicht mehr verspricht
    # als es tut.
    kept_tables: list[str]


class PurgeRequest(BaseModel):
    # Der Haushaltsname, wie er in der Oberflaeche steht. Wird serverseitig
    # verglichen: bei hunderten Testhaushalten neben einem echten ist die
    # Verwechslung der teuerste denkbare Fehler, und eine reine
    # Oberflaechenpruefung waere dagegen keine Sicherung.
    confirm_name: str = Field(min_length=1, max_length=200)


class PurgeResponse(BaseModel):
    household_name: str
    deleted_rows: int
    # Was je Tabelle wegging - Beleg statt einer nackten Gesamtzahl.
    deleted_per_table: dict[str, int]
    audit_event_id: UUID | None


class DeleteResponse(BaseModel):
    table: str
    deleted: int
    # Was die Kaskade zusaetzlich mitgenommen hat, wie vor dem Loeschen
    # ermittelt. Nach dem Loeschen laesst es sich nicht mehr zaehlen - die
    # Zeilen sind ja weg.
    cascade_total: int
    audit_event_id: UUID | None
