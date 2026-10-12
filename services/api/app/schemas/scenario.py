"""API-Vertraege der gespeicherten Haushaltsszenarien
(docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 7).

Ein Szenario ist nichts anderes als eine GESPEICHERTE AENDERUNGSLISTE. Deshalb
erbt `HouseholdScenarioChange` von `ForecastChange` und definiert die sieben
Aenderungstypen, die drei Betragsmodi und ihre XOR-Regeln nicht ein zweites
Mal - zwei Definitionen derselben Regel waeren zwei Regeln (Leitsatz 7).
Hinzu kommen nur Ablageangaben: `id`, `include_in_forecast`, `sort_order`,
`note`.

Aus demselben Grund liefert `GET /scenarios/{id}/forecast` genau die Form von
`POST /forecast/simulate`: es gibt einen Rechenkern (Leitsatz 2), und ein
gespeichertes Szenario unterscheidet sich von einer ungespeicherten Simulation
allein darin, woher die Aenderungsliste kommt.
"""

from datetime import datetime as DateTime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.forecast import ForecastChange, ForecastComparison, ForecastResponse

HouseholdScenarioStatus = Literal["draft", "active", "archived"]
# 'contract_scenario' tragen die aus dem Fixkostenplan uebernommenen Szenarien.
# Die Herkunft ist nicht aenderbar - sie ist Nachweis, keine Nutzereingabe.
HouseholdScenarioOrigin = Literal["manual", "contract_scenario"]


# ---------------------------------------------------------------------------
# Aenderungszeilen
# ---------------------------------------------------------------------------


class HouseholdScenarioChange(ForecastChange):
    """Eine gespeicherte Aenderungszeile, so wie sie in der Tabelle steht."""

    id: UUID
    # Ausdruecklich abgewaehlte Zeilen bleiben erhalten und werden vor dem
    # Rechenlauf herausgefiltert - "ohne den Autokauf nochmal rechnen" darf
    # die Zeile nicht kosten.
    include_in_forecast: bool
    sort_order: int
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "HouseholdScenarioChange":
        """Ueberschreibt die Eingabepruefung von `ForecastChange` bewusst.

        Fuer eine bereits gespeicherte Zeile ist die Tabelle die Wahrheit, und
        ihre CHECK-Constraints sind an einer Stelle grosszuegiger als das
        Eingabemodell: `amount_minor = 0` ist erlaubt und entsteht regulaer bei
        der Uebernahme eines Fixkostenszenarios ("auf 0 EUR senken" statt
        "kuendigen"). Ein Lesemodell, das strenger ist als seine Tabelle,
        machte aus einer gueltigen Zeile einen Serverfehler.

        Neue Zeilen nehmen weiterhin die volle Huerde - siehe
        `HouseholdScenarioChangeInput`.
        """
        return self


class HouseholdScenarioChangeInput(ForecastChange):
    """Eingabezeile fuer `PUT /scenarios/{id}/changes`.

    Erbt die Pruefung von `ForecastChange` unveraendert: was neu gespeichert
    wird, muss dieselbe Huerde nehmen wie eine ungespeicherte Simulation -
    sonst liesse sich ueber den Umweg "speichern und rechnen lassen" eine
    Annahme eintragen, die `/forecast/simulate` ablehnt.
    """

    include_in_forecast: bool = True
    # Ohne Angabe zaehlt die Reihenfolge im Request. Die Oberflaeche soll
    # sortieren duerfen, muss aber nicht.
    sort_order: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# Szenario
# ---------------------------------------------------------------------------


class HouseholdScenario(BaseModel):
    """Kopfdaten ohne die Zeilen - das reicht fuer Liste und Vergleichslegende."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    description: str | None
    status: HouseholdScenarioStatus
    # NULL heisst "Horizont des Haushalts" (forecast_settings.horizon_months).
    # Eine Kopie des Vorgabewerts stuende sonst still fest, waehrend der
    # Haushalt seinen Horizont laengst geaendert hat.
    horizon_months: int | None
    color_key: str | None
    origin: HouseholdScenarioOrigin
    change_count: int
    created_at: DateTime
    updated_at: DateTime
    version: int


class HouseholdScenarioDetail(HouseholdScenario):
    changes: tuple[HouseholdScenarioChange, ...] = ()


class HouseholdScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: HouseholdScenarioStatus = "draft"
    horizon_months: int | None = Field(default=None, ge=1, le=60)
    color_key: str | None = Field(default=None, max_length=40)
    # Die Aenderungen duerfen gleich mitkommen, damit ein fertiger Entwurf aus
    # der Ansicht "Was waere wenn" in EINEM Zug gespeichert werden kann statt
    # in zwei Requests, zwischen denen ein leeres Szenario haengen bliebe.
    changes: list[ForecastChange] = Field(default_factory=list)


class HouseholdScenarioUpdate(BaseModel):
    """Teilaenderung der Kopfdaten.

    `origin` und die Herkunftsverknuepfung fehlen absichtlich: sie sind
    Nachweis der Uebernahme und keine Eingabe.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: HouseholdScenarioStatus | None = None
    horizon_months: int | None = Field(default=None, ge=1, le=60)
    color_key: str | None = Field(default=None, max_length=40)


class HouseholdScenarioChangesInput(BaseModel):
    """Ersetzt die Liste VOLLSTAENDIG - gleiches Muster wie
    `PUT /budgets/{jahr}/{monat}/allocations`. Eine leere Liste ist damit die
    ausdrueckliche Aussage "dieses Szenario hat keine Annahmen mehr"."""

    changes: list[HouseholdScenarioChangeInput] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Vergleich
# ---------------------------------------------------------------------------


class ScenarioComparisonEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: HouseholdScenario
    simulation: ForecastResponse
    comparison: ForecastComparison


class ScenarioComparisonResponse(BaseModel):
    """Eine Basis, mehrere Szenarien.

    Die Basis steht genau einmal da und nicht je Eintrag: alle Linien liegen
    auf derselben Zeitachse und demselben Startsaldo, sonst waere der Vergleich
    keiner.
    """

    model_config = ConfigDict(frozen=True)

    base: ForecastResponse
    entries: tuple[ScenarioComparisonEntry, ...]
