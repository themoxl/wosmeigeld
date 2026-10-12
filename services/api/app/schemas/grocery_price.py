"""Schemas fuer Laeden, Preissichtungen und den Excel-Austausch.

Abgrenzung zu app/schemas/grocery.py: dort steht der Essensplan samt der
bestehenden Preisliste. Hier steht alles, was sich um die HERKUNFT eines
Preises dreht - wo, wann, welches Produkt, aus welcher Quelle.

Wie schon bei den KI-Vorschlaegen gilt: Vorschau ist kein Datensatz. Die
Vorschau-Modelle unten beschreiben nichts Gespeichertes. Geschrieben wird erst
ueber den Bulk-Endpunkt, nachdem der Nutzer ausgewaehlt hat.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

StoreKind = Literal["discounter", "supermarkt", "biomarkt", "markt", "online", "sonstige"]
QualityTier = Literal["basic", "standard", "organic"]
BrandTier = Literal["private_label", "branded", "unknown"]
ObservationSource = Literal["manual", "excel", "shopping_list", "migration"]
PriceSource = Literal["manual", "pinned", "derived"]
PriceStrategy = Literal["latest", "cheapest"]
QualityPreference = Literal["any", "prefer_organic", "organic_only"]


# --- Laeden ---
class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # Filiale. Noetig nur dort, wo Maerkte eigene Preise machen (Rewe, Edeka).
    location: str | None = Field(default=None, max_length=80)
    store_kind: StoreKind = "supermarkt"
    note: str | None = None


class StoreUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    location: str | None = Field(default=None, max_length=80)
    store_kind: StoreKind | None = None
    is_archived: bool | None = None
    note: str | None = None


class StoreResponse(BaseModel):
    id: UUID
    name: str
    location: str | None
    store_kind: StoreKind
    is_archived: bool
    note: str | None
    # Wie viele Sichtungen an diesem Laden haengen - zeigt, was ein Loeschen
    # kostet, und welche Laeden ueberhaupt gepflegt werden.
    observation_count: int
    last_observed_on: date | None
    created_at: datetime


# --- Sichtungen ---
class ObservationCreate(BaseModel):
    """Handeingabe einer einzelnen Sichtung.

    Abgeleitete Werte (Basiseinheit, Grundpreis, Fingerabdruck) stehen hier
    bewusst NICHT: sie entstehen auf dem Server aus denselben Funktionen wie
    beim Dateiimport. Ein Client, der Rechenergebnisse mitschicken darf, kann
    sie auch falsch mitschicken."""

    ingredient_name: str = Field(min_length=1, max_length=160)
    price_minor: int = Field(ge=0)
    pack_quantity: Decimal = Field(default=Decimal("1"), gt=0)
    pack_unit: str | None = Field(default=None, max_length=24)
    store_id: UUID | None = None
    store_name: str | None = Field(default=None, max_length=80)
    store_location: str | None = Field(default=None, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    is_organic: bool = False
    is_promotion: bool = False
    offer_valid_from: date | None = None
    offer_valid_to: date | None = None
    observed_on: date | None = None
    note: str | None = Field(default=None, max_length=400)


class ObservationResponse(BaseModel):
    id: UUID
    ingredient_key: str
    ingredient_name: str
    store_id: UUID | None
    store_label: str | None
    brand: str | None
    quality_tier: QualityTier
    brand_tier: BrandTier
    observed_on: date
    is_promotion: bool
    offer_valid_from: date | None
    offer_valid_to: date | None
    price_minor: int
    pack_quantity: Decimal
    pack_unit: str | None
    base_family: str
    # Grundpreis in Millionstel Cent je Basiseinheit. Erst er macht
    # "1,99 fuer 1 kg" und "1,29 fuer 500 g" vergleichbar; in ganzen Cent
    # waeren beide 0.
    unit_price_micro: int
    source: ObservationSource
    source_ref: str | None
    note: str | None
    # True fuer genau die Sichtung, die aktuell den Bewertungspreis stellt.
    is_current_valuation: bool
    created_at: datetime


class ObservationBulkCreate(BaseModel):
    """Uebernahme bestaetigter Vorschauzeilen. Jede Zeile wird auf dem Server
    noch einmal durch dieselbe Pruefung geschickt wie beim Einlesen."""

    observations: list[ObservationCreate] = Field(min_length=1, max_length=2000)
    source: ObservationSource = "excel"
    source_ref: str | None = Field(default=None, max_length=200)


class ObservationBulkResult(BaseModel):
    inserted: int
    # Bereits vorhanden (gleicher Fingerabdruck). Der Normalfall beim
    # versehentlichen zweiten Import derselben Datei - kein Fehler.
    skipped_duplicates: int
    # Zeilen, die die Serverpruefung nicht bestanden haben, mit Begruendung.
    rejected: list[str]
    # Neu angelegte Zeilen in der Preisliste (Zutaten, die es dort noch nicht
    # gab). Sichtbar, damit ein Tippfehler im Namen nicht unbemerkt eine
    # zweite Zutat erzeugt.
    created_price_rows: int
    # Zeilen der Preisliste, deren Bewertungspreis sich dadurch geaendert hat.
    updated_valuations: int
    # Zutaten, die trotz vorhandener Sichtungen weiter mit ihrer von Hand
    # getippten Zahl rechnen. Beim ersten Import ist das der Normalfall - alle
    # Bestandszeilen stehen auf 'manual'. Ohne diese Zahl saehe der Lauf aus,
    # als haette er nichts bewirkt, obwohl das Gedaechtnis gefuellt wurde.
    # Haushaltsweit gezaehlt, damit die Zahl bei einer in Stuecken
    # uebernommenen Datei nicht vom Zuschnitt abhaengt.
    blocked_by_manual: int


# --- Bewertungspreis steuern ---
class ValuationUpdate(BaseModel):
    """Wie der Bewertungspreis einer Zutat zustande kommt.

    price_source = 'manual' bedeutet: die getippte Zahl gilt und wird von
    keinem Import angefasst. 'pinned' nagelt eine bestimmte Sichtung fest.
    'derived' laesst die Strategie waehlen."""

    price_source: PriceSource | None = None
    price_strategy: PriceStrategy | None = None
    preferred_store_id: UUID | None = None
    quality_preference: QualityPreference | None = None
    planning_base_family: str | None = Field(default=None, max_length=24)
    # Nur bei price_source = 'pinned' ausgewertet.
    price_observation_id: UUID | None = None


class ValuationState(BaseModel):
    """Herkunft der Zahl, die gerade in der Kostenrechnung steht."""

    price_source: PriceSource
    price_strategy: PriceStrategy | None
    preferred_store_id: UUID | None
    quality_preference: QualityPreference | None
    planning_base_family: str | None
    price_observation_id: UUID | None
    price_observed_on: date | None
    # Keine Sichtung mehr innerhalb der Altersgrenze. Der Preis gilt weiter,
    # er wird nur nicht mehr als frisch ausgegeben.
    price_is_stale: bool


class PriceMatrixCell(BaseModel):
    """Eine Zelle der Kreuztabelle Laden x Qualitaet."""

    store_id: UUID | None
    store_label: str
    quality_tier: QualityTier
    brand_tier: BrandTier
    observation_id: UUID
    price_minor: int
    pack_quantity: Decimal
    pack_unit: str | None
    unit_price_micro: int
    observed_on: date
    is_promotion: bool
    offer_valid_to: date | None
    is_current_valuation: bool


class IngredientPriceDetail(BaseModel):
    ingredient_key: str
    ingredient_name: str
    valuation: ValuationState
    # Aktueller Stand je Laden und Qualitaet, jeweils die juengste Sichtung.
    matrix: list[PriceMatrixCell]
    # Vollstaendiger Verlauf, juengste zuerst.
    history: list[ObservationResponse]
    # Grundpreis-Spanne ueber alle Sichtungen derselben Einheitenfamilie -
    # die Bezugsgroesse fuer "ist das gerade ein guter Preis?".
    cheapest_unit_price_micro: int | None
    dearest_unit_price_micro: int | None
    base_family: str | None


# --- Einstellungen ---
class PriceSettingsPayload(BaseModel):
    default_strategy: PriceStrategy = "latest"
    preferred_store_id: UUID | None = None
    max_age_days: int = Field(default=180, ge=14, le=730)


# --- Excel-Austausch ---
class SheetPreviewRow(BaseModel):
    """Eine gelesene Zeile - Vorschlag, noch nichts Gespeichertes."""

    row_number: int
    ingredient_name: str
    # Steht die Zutat schon in der Preisliste? Ist sie neu, wird beim
    # Uebernehmen eine Preiszeile angelegt - das soll man vorher sehen.
    ingredient_is_new: bool
    store_name: str | None
    store_location: str | None
    brand: str | None
    quality_tier: QualityTier
    brand_tier: BrandTier
    price_minor: int
    pack_quantity: Decimal
    pack_unit: str | None
    base_family: str
    unit_price_micro: int
    is_promotion: bool
    offer_valid_from: date | None
    offer_valid_to: date | None
    observed_on: date
    note: str | None
    # Diese Sichtung liegt identisch schon vor - beim Uebernehmen passiert
    # nichts. Vorbelegt abgewaehlt, damit "N uebernehmen" nicht mehr
    # verspricht, als es tut.
    is_duplicate: bool
    # Bisheriger Grundpreis derselben Zutat, sofern bekannt. Macht einen
    # Zahlendreher sichtbar, bevor er in der Kostenrechnung landet.
    previous_unit_price_micro: int | None


class SheetProblem(BaseModel):
    row_number: int
    ingredient_name: str | None
    message: str


class SheetPreviewResult(BaseModel):
    file_name: str
    sheet_name: str
    header_row: int
    rows: list[SheetPreviewRow]
    # Zeilen mit einem echten Fehler - unlesbares Datum, Angebot ohne Ende.
    problems: list[SheetProblem]
    # Vorbefuellte Zeilen, die niemand ausgefuellt hat. Der Normalfall einer
    # exportierten Vorlage und ausdruecklich kein Fehler.
    untouched_rows: int
    # Spaltenbeschriftungen, die nicht zugeordnet werden konnten. Sichtbar,
    # damit ein Vertipper in der Kopfzeile nicht als "Spalte war leer" endet.
    unmapped_headers: list[str]
