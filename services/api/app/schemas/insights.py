"""Vertrag der Finanz-Empfehlungen ("KI-Empfehlungen").

Grundregel, die dieser Vertrag erzwingt: **jede Zahl wird deterministisch
berechnet, nie von einem Modell erzeugt.** Dieselbe Zusage steht in
docs/KI-KATEGORISIERUNG.md ("ein LLM darf keine Salden oder Summen erfinden")
und in docs/KI-FINANZASSISTENT-PLAN.md ("Periodizitaet und Transferpaare werden
deterministisch berechnet. KI ordnet den fachlichen Typ ein und erklaert").

Deshalb die Trennung in zwei Felder:

- `title`, `detail`, `amount_minor`, `monthly_potential_minor` und `evidence`
  entstehen in `insights_service` aus SQL. Sie sind vollstaendig und lesbar,
  auch wenn gar kein Modell laeuft.
- `ai_advice` ist die optionale Formulierung des Modells. Sie ergaenzt einen
  Rat, darf aber keine eigenen Betraege einfuehren - der Prompt untersagt es,
  und die Oberflaeche zeigt sie als klar getrennten Zusatz.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.ai import AIWarning

InsightKind = Literal[
    "category_trend",
    "budget_overrun",
    "price_increase",
    "contract_unused",
    "contract_duplicate",
    "wants_share",
    "savings_rate",
    "small_amounts",
    "tax_deductible",
    "uncategorized",
]

# `warning` = etwas laeuft aus dem Ruder, `opportunity` = hier liegt Geld,
# `info` = Einordnung ohne Handlungsdruck. Bewusst keine Prozentskala: eine
# Empfehlung ist keine Note.
InsightSeverity = Literal["warning", "opportunity", "info"]


class InsightEvidence(BaseModel):
    """Ein berechneter Beleg. Ohne Belege waere eine Empfehlung eine
    Behauptung - und niemand koennte pruefen, wie sie zustande kam."""

    label: str
    amount_minor: int | None = None
    count: int | None = None


class InsightDrilldown(BaseModel):
    """Zeigt auf die Buchungen hinter der Zahl.

    Bewusst nur Feld, Wert und Zeitraum statt einer vollstaendigen
    `AnalyticsQuery`: die Oberflaeche baut daraus dieselbe Abfrage, die der
    Explorer verwendet, und der Vertrag bleibt unabhaengig vom Abfragemodell.
    """

    field: str | None = None
    value: str | None = None
    from_date: date
    to_date: date
    # Nur Ausgaben, nur Einnahmen oder beides - ohne diese Angabe wuerde ein
    # Drilldown auf eine Ausgabenkennzahl auch Erstattungen mitzeigen.
    direction: Literal["expense", "income"] | None = None
    # Der Vertrag hinter der Empfehlung, sofern sie aus einem entstanden ist.
    # Getrennt von `field`/`value`, weil der Explorer keine Vertragsdimension
    # kennt: die beiden Felder bleiben der Buchungsfilter, dies hier ist das
    # Sprungziel "Zum Vertrag". Ein Vertrag im Filterfeld wuerde die Abfrage
    # zerlegen, die dieselbe Karte anklickbar macht.
    contract_id: UUID | None = None


class Insight(BaseModel):
    # Stabil ueber Laeufe hinweg (z. B. "category_trend:<uuid>"), damit die
    # Oberflaeche Auswahl und KI-Formulierung zuordnen kann.
    key: str
    kind: InsightKind
    severity: InsightSeverity
    title: str
    detail: str
    # Kernzahl der Empfehlung (Minor Units, vorzeichenlos).
    amount_minor: int | None = None
    # Wiederkehrender Betrag pro Monat, um den es geht. Nur gesetzt, wenn er
    # fachlich sinnvoll ist - eine Jahressumme ist kein Monatspotenzial.
    monthly_potential_minor: int | None = None
    category_id: UUID | None = None
    category_name: str | None = None
    evidence: list[InsightEvidence] = Field(default_factory=list)
    drilldown: InsightDrilldown | None = None
    ai_advice: str | None = None


class InsightReport(BaseModel):
    from_date: date
    to_date: date
    months_covered: int
    # Monatsdurchschnitte des Betrachtungszeitraums.
    monthly_income_minor: int
    monthly_expense_minor: int
    monthly_net_minor: int
    # Summe aller `monthly_potential_minor`. Bewusst getrennt ausgewiesen:
    # "so viel koennte pro Monat frei werden" ist die Zahl, wegen der man die
    # Seite ueberhaupt oeffnet.
    monthly_potential_minor: int
    model_name: str | None = None
    # Warum Ratschlaege fehlen, wenn with_ai angefordert war: KI aus, kein
    # Modell, Fenster unbekannt, Antwort abgeschnitten oder verworfen
    # (Reason-Code des KI-Kerns samt Klartext). Der Bericht selbst ist auch
    # dann vollstaendig - nur der Zusatz bleibt weg, und das sichtbar.
    ai_warnings: list[AIWarning] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)
