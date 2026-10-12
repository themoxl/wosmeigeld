from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ContractForecastSource(BaseModel):
    """Ein Vertrag, der zu contract_forecast_minor beitraegt - je Faelligkeit im
    Budgetmonat ein Eintrag (ein woechentlicher Vertrag kann also mehrfach
    auftauchen). Traegt die Hover-Erklaerung "wieso wird hier etwas erwartet",
    ohne dass die Oberflaeche selbst in den Vertragsdaten suchen muss."""

    contract_id: UUID
    contract_name: str
    amount_minor: int
    due_date: date


class BudgetAllocationInput(BaseModel):
    category_id: UUID
    planned_minor: int = Field(ge=0)
    rollover_rule: Literal["none", "accumulate", "reset"] = "none"
    note: str | None = None


class BudgetUpdateInput(BaseModel):
    allocations: list[BudgetAllocationInput]


class CategoryBudgetStatus(BaseModel):
    category_id: UUID
    category_name: str
    group_id: UUID
    group_name: str
    budget_bucket: str | None
    # 'income' | 'expense' | 'transfer' | None - Einnahmekategorien werden in
    # allen Summen getrennt gefuehrt (siehe income_actual_minor).
    default_type: str | None
    rollover_rule: Literal["none", "accumulate", "reset"]
    # Archivierte Kategorien bleiben in Budget und Berichten sichtbar und in
    # allen Summen enthalten; die Oberflaeche kennzeichnet sie mit einem Badge
    # und bietet sie nicht mehr zur Neuauswahl an.
    is_archived: bool = False
    planned_minor: int
    rollover_minor: int
    # Nettoverbrauch als positive Zahl; bei Einnahmekategorien immer 0.
    spent_minor: int
    # Ist-Einnahmen als positive Zahl; bei Ausgabenkategorien immer 0.
    income_actual_minor: int
    # Ausgaben: planned + rollover - spent. Einnahmen: noch ausstehender
    # Betrag, also planned + rollover - income_actual.
    remaining_minor: int
    percent_used: float
    # Ist des VORMONATS, gleiche Seite wie oben (Einnahmekategorien: Ist-
    # Einnahme, sonst Verbrauch) - fuer die Vergleichsspalte beim Planen.
    prev_month_actual_minor: int
    # Erwarteter Betrag aus Vertraegen mit dieser Kategorie, die im
    # Budgetmonat faellig sind (voller Betrag, unabhaengig davon, ob die
    # Buchung schon gelandet ist - siehe budget_service.get_budget_summary
    # fuer die Begruendung, warum hier NICHT um bereits Gebuchtes bereinigt
    # wird).
    contract_forecast_minor: int
    # Woraus sich contract_forecast_minor zusammensetzt - leer, wenn 0. Traegt
    # die Hover-Erklaerung im Frontend ("wieso wird hier etwas erwartet").
    contract_forecast_sources: list[ContractForecastSource] = []
    # True, wenn die Kategorie IRGENDWANN (nicht nur diesen Monat oder dieses
    # Jahr) einen Planbetrag ungleich 0 oder eine Buchung hatte. Traegt den
    # dritten Filterzustand im Monatsbudget ("jemals Bewegung"), der sonst
    # saisonale Kategorien (z. B. eine nur jaehrlich faellige Versicherung) in
    # den restlichen elf Monaten aus der Liste verschwinden liesse.
    has_activity_ever: bool


class CategoryGroupBudgetStatus(BaseModel):
    group_id: UUID
    group_name: str
    sort_order: int
    # Plan-/Ist-Summen enthalten ausschliesslich Ausgabenkategorien.
    total_planned_minor: int
    total_rollover_minor: int
    total_spent_minor: int
    total_remaining_minor: int
    total_income_actual_minor: int
    # Geplante Einnahmen der Gruppe - bewusst NICHT in total_planned_minor.
    # Ein Gehaltsplan ist kein Budgettopf, aus dem etwas ausgegeben wird:
    # weil total_remaining_minor = Plan + Uebertrag - Ist rechnet und das Ist
    # einer Einnahmekategorie immer 0 ist, schluege ein Einnahmeplan dort eins
    # zu eins als "freier Budgetrest" durch. Ohne dieses eigene Feld bliebe
    # die Gruppenzeile "Einnahmen" dagegen bei 0,00 EUR stehen, obwohl ein
    # Planbetrag gesetzt ist.
    total_planned_income_minor: int
    total_rollover_income_minor: int
    # Noch ausstehende Einnahme: planned_income + rollover_income -
    # total_income_actual_minor. Nicht gegen total_remaining_minor
    # verrechnen - das eine ist uebriges Ausgabenbudget, das andere noch
    # nicht eingegangenes Geld.
    total_income_remaining_minor: int
    categories: list[CategoryBudgetStatus]


class FixedCostReserveItem(BaseModel):
    contract_id: UUID
    contract_name: str
    frequency: Literal["quarterly", "semiannual", "yearly"]
    period_amount_minor: int
    next_due_date: date
    monthly_reserve_minor: int
    accrued_minor: int
    remaining_minor: int
    months_remaining: int
    due_this_month: bool
    is_covered: bool
    is_first_cycle: bool


class BudgetSummary(BaseModel):
    budget_id: UUID | None
    year: int
    month: int
    status: Literal["open", "closed"]
    method: str
    closed_at: datetime | None
    # Ist-Einnahmen des Monats (Einnahmekategorien plus unkategorisierte
    # Zufluesse); nie Teil von total_spent_minor.
    total_income_minor: int
    # Plan-/Ist-Summen enthalten ausschliesslich Ausgabenkategorien.
    total_planned_minor: int
    total_rollover_minor: int
    total_spent_minor: int
    total_remaining_minor: int
    # Summe der geplanten EINNAHMEN. Steht bewusst neben total_planned_minor
    # und nie darin: total_remaining_minor = Plan + Uebertrag - Ist, und das
    # Ist einer Einnahmekategorie ist immer 0 - ein Gehaltsplan von 3.000 EUR
    # wuerde dort also als 3.000 EUR freier Budgetrest erscheinen und ueber
    # goal_service.fetch_free_monthly_minor sogar die Kaufampel der
    # Wunschliste gruen faerben. Sparquote, Berichte und Monatsabschluss
    # haengen ebenfalls an der reinen Ausgabenlesart.
    # Bezugsgroesse ist die Ist-Einnahme der EINNAHMEKATEGORIEN, nicht
    # total_income_minor: dort stecken zusaetzlich unkategorisierte Zufluesse,
    # die zu keinem Plan gehoeren. Dadurch gilt sauber
    # total_*_income_* == sum(group.total_*_income_*).
    total_planned_income_minor: int
    total_rollover_income_minor: int
    total_income_remaining_minor: int
    # Unkategorisierte negative Splits (category_id is null) auf
    # On-Budget-Konten - typischerweise eine frisch importierte, noch nicht
    # zugewiesene Buchung. Bereits IN total_spent_minor/total_remaining_minor
    # enthalten (Invariante: total_spent = sum(group.total_spent) +
    # uncategorized_spent), aber in KEINER Gruppen- oder Bucket-Summe. Zeigt
    # die Oberflaeche als eigene Warnzeile, damit "wo kommt der Unterschied
    # zwischen Gruppensumme und Gesamtsumme her" beantwortbar bleibt.
    uncategorized_spent_minor: int
    # Optional fuer spaetere Auswertung: Summe der Ausgaben, die einem der
    # drei 50/30/20-Buckets zugeordnet sind (== bucket_needs_minor +
    # bucket_wants_minor + bucket_savings_minor). Nicht dasselbe wie
    # total_spent_minor, sobald budgetierte Kategorien ohne Bucket oder
    # Unkategorisiertes dazukommen.
    bucket_classified_spent_minor: int | None = None
    savings_rate_percent: float
    bucket_needs_minor: int
    bucket_wants_minor: int
    bucket_savings_minor: int
    # Gebuchte Ausgaben von Kategorien OHNE 50/30/20-Zuordnung. Eigene
    # Groesse, weil sie sonst still auf die drei Toepfe verteilt werden
    # muesste und "noch nicht zugeordnet" damit unsichtbar waere.
    # Unkategorisierte Splits gehoeren NICHT hierher - die haben ueberhaupt
    # keine Kategorie und stehen in uncategorized_spent_minor. Invariante:
    # needs + wants + savings + unassigned_spent + uncategorized_spent
    # == total_spent_minor.
    bucket_unassigned_spent_minor: int
    # Planseite derselben vier Toepfe. Bisher leitete jede Oberflaeche sie
    # selbst aus den Kategorien ab - Web und Mobile konnten damit
    # unterschiedliche Zahlen zeigen. Invariante: needs + wants + savings +
    # unassigned == total_planned_minor (Einnahmeplaene sind hier so wenig
    # enthalten wie in total_planned_minor selbst).
    bucket_needs_planned_minor: int
    bucket_wants_planned_minor: int
    bucket_savings_planned_minor: int
    bucket_unassigned_planned_minor: int
    # Anteile der drei Toepfe an den KLASSIFIZIERTEN Ausgaben, nicht am
    # Einkommen - sie summieren sich deshalb immer auf 100 % und sind kein
    # Abgleich mit dem 50/30/20-ZIEL. Wer das Ziel in Euro braucht, rechnet
    # es aus income_basis_minor (50/30/20 % davon) und stellt es den
    # bucket_*_planned_minor bzw. bucket_*_minor gegenueber.
    bucket_needs_percent: float
    bucket_wants_percent: float
    bucket_savings_percent: float
    # --- Kalkulatorische Spartoepfe ---------------------------------------
    # Additive Planungsschicht: keine dieser Zahlen fliesst in Plan/Ist,
    # Sparquote, Buckets, Kontostaende oder Vermoegen zurueck.
    surplus_before_goal_reserves_minor: int
    goal_reserves_monthly_minor: int
    free_after_goal_reserves_minor: int
    goal_reserve_count: int
    # --- Vorausschauende Planungsbasis ------------------------------------
    # Anders als Plan/Ist oben basiert diese Schicht auf erwarteten Einnahmen
    # und Verpflichtungen. Sie veraendert keine Buchung und keinen Kontostand.
    planning_expected_income_minor: int
    planning_income_basis: Literal["contract", "actual"]
    regular_fixed_costs_monthly_minor: int
    # Betrag UND Anzahl beschreiben dieselbe Menge: die Ruecklagentoepfe
    # (reserve_service.get_reserve_overview), die mit dem tatsaechlichen
    # Topfstand und den verbleibenden Einzahlungsterminen rechnen. Eine
    # Oberflaeche darf beide nebeneinander stellen.
    fixed_cost_reserves_monthly_minor: int
    fixed_cost_reserve_count: int
    # ACHTUNG, andere Quelle: die Einzelposten kommen aus der theoretischen
    # Modellrechnung (fixed_cost_reserve_service) und normieren stur ueber den
    # Zyklus. Ihre monthly_reserve_minor summieren sich deshalb NICHT auf
    # fixed_cost_reserves_monthly_minor - 120.000/12 = 10.000 gegen 10.910 bei
    # elf verbleibenden Terminen. Wer beides in einer Ansicht zeigt, muss die
    # Differenz benennen oder eine der beiden Quellen weglassen.
    fixed_cost_reserve_items: list[FixedCostReserveItem]
    variable_planned_budget_minor: int
    variable_planned_excluded_contract_minor: int
    variable_planned_excluded_goal_savings_minor: int
    total_calculational_reserves_minor: int
    planning_truly_free_minor: int
    # --- Fixkosten aus Vertraegen (Phase 4) --------------------------------
    # Die folgenden Felder stehen BEWUSST neben der Plan/Ist-Rechnung und nicht
    # in ihr. Alles oberhalb zaehlt GEBUCHTES Geld, ein Vertrag ist dagegen eine
    # Erwartung. Eine bereits abgebuchte Vertragsrate steckt laengst in
    # total_spent_minor und im passenden 50/30/20-Bucket - wuerde man die
    # Vertragssumme dort addieren, zaehlte dasselbe Geld zweimal. Deshalb
    # bleiben total_spent_minor, savings_rate_percent und die drei Buckets
    # unveraendert; wer beides zusammenrechnen will, bekommt eine falsche Zahl.
    # Summe der auf einen Monat normierten AUSGABEN-Vertraege, die im
    # Budgetmonat laufen. Ein gekuendigter Vertrag zaehlt bis ends_on mit, ein
    # beendeter nie.
    fixed_costs_monthly_minor: int
    # Dieselbe Rechnung fuer direction='income' (z. B. Gehalt als Vertrag) -
    # getrennt gefuehrt, damit ein Einnahmevertrag die Fixkostensumme nicht
    # verwaessert.
    fixed_costs_income_monthly_minor: int
    # Die Bezugsgroesse selbst, in Cent. Bisher war sie nur indirekt ueber
    # available_after_fixed_minor und fixed_costs_ratio_percent sichtbar; die
    # Oberflaeche konnte das 50/30/20-ZIEL deshalb nicht in Euro ausrechnen.
    # Woher der Wert stammt, sagt fixed_costs_income_basis - ohne dieses
    # Begleitfeld ist die Zahl nicht interpretierbar. Kann negativ sein (ein
    # Monat mit Erstattungsueberschuss ist ein Ist-Wert, kein "noch nichts
    # gebucht"), deshalb vor jeder Prozentrechnung auf > 0 pruefen.
    income_basis_minor: int
    # Bezugsgroesse minus Fixkosten: was nach den laufenden Verpflichtungen
    # ueberhaupt disponierbar ist.
    available_after_fixed_minor: int
    # Woher die Bezugsgroesse stammt: 'actual' = Ist-Einnahmen des Monats,
    # 'contract' = Ersatzwert aus Einnahmevertraegen, wenn noch nichts gebucht
    # ist (sonst stuende im laufenden Monat eine Quote von 0 %).
    fixed_costs_income_basis: Literal["actual", "contract"]
    # Fixkostenquote in Prozent der Bezugsgroesse; 0.0, wenn es keine gibt.
    # Die Ampel (< 50 gut, 50-70 angespannt, > 70 kritisch) bleibt in der
    # Oberflaeche - eine Farbe ist keine Servicezahl.
    fixed_costs_ratio_percent: float
    # Anzahl der gezaehlten Ausgabenvertraege; ohne sie ist eine ueberraschende
    # Summe nicht nachvollziehbar.
    fixed_costs_contract_count: int
    groups: list[CategoryGroupBudgetStatus]


class AnnualMatrixMonthData(BaseModel):
    month: int
    planned_minor: int
    spent_minor: int
    income_actual_minor: int


class AnnualMatrixRow(BaseModel):
    category_id: UUID
    category_name: str
    group_id: UUID
    group_name: str
    # Die Zeilen kommen bereits nach Gruppe und Kategorie sortiert. Die beiden
    # Ordnungszahlen liegen trotzdem bei, damit die Oberflaeche die flache
    # Liste ohne zweite Abfrage zu Gruppenbloecken zusammenfassen und in
    # derselben Reihenfolge wie das Monatsbudget anzeigen kann - sortiert sie
    # nach Namen, stehen Matrix und Budget verschieden.
    group_sort_order: int
    category_sort_order: int
    budget_bucket: str | None
    default_type: str | None
    is_archived: bool = False
    months: list[AnnualMatrixMonthData]
    total_planned_minor: int
    total_spent_minor: int
    total_income_actual_minor: int


class AnnualMatrixResponse(BaseModel):
    year: int
    rows: list[AnnualMatrixRow]


class CloseMonthResponse(BaseModel):
    budget_id: UUID
    status: str
    closed_at: datetime
    rollovers_created: int
