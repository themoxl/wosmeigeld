"""Gemeinsame SQL-Regeln fuer die Cashflow-Zuordnung von Buchungssplits.

Kennzahl und Drilldown muessen dieselben Splits verwenden. Die Ausdruecke
setzen die Tabellenaliase ``s`` (Split), ``t`` (Buchung) und ``c``
(Kategorie) voraus.
"""

from typing import Final

CASHFLOW_INCOME_PREDICATE: Final = """(
    c.default_type = 'income'
    or (s.category_id is null and s.amount_minor > 0)
)"""

CASHFLOW_EXPENSE_PREDICATE: Final = """(
    (
        s.category_id is not null
        and coalesce(c.default_type, '') <> 'income'
        and c.default_type is distinct from 'transfer'
        and (
            c.is_budgeted
            or exists (
                select 1
                from budgets b
                where b.household_id = t.household_id
                  and b.year = extract(year from t.booked_date)::int
                  and b.month = extract(month from t.booked_date)::int
                  and (
                      exists (
                          select 1 from budget_allocations ba
                          where ba.budget_id = b.id and ba.category_id = c.id
                      )
                      or exists (
                          select 1 from budget_rollovers br
                          where br.budget_id = b.id and br.category_id = c.id
                      )
                  )
            )
        )
    )
    -- Unkategorisierte negative Splits (category_id is null) zaehlen als
    -- Ausgabe - deckungsgleich mit budget_service.get_budget_summary
    -- (uncategorized_spent_minor). Ohne diesen Zweig zaehlte dieselbe
    -- Importbuchung im Budget als Ausgabe, im Cashflow-/Uebersichtsbericht
    -- aber weder als Einnahme noch als Ausgabe - zwei verschiedene
    -- Ausgabensummen fuer denselben Monat.
    or (s.category_id is null and s.amount_minor < 0)
)"""

CASHFLOW_ROLE_SQL: Final = f"""(
    case
        when {CASHFLOW_INCOME_PREDICATE} then 'income'
        when {CASHFLOW_EXPENSE_PREDICATE} then 'expense'
        else null
    end
)"""
