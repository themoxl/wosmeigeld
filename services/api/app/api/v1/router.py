from fastapi import APIRouter

from app.api.v1.routes import (
    accounts,
    ai,
    ai_tasks,
    analytics,
    assistant,
    backup,
    budgets,
    categories,
    category_operations,
    category_suggestions,
    contracts,
    data_explorer,
    debt_plans,
    devices,
    forecast,
    funding,
    goals,
    groceries,
    grocery,
    grocery_prices,
    households,
    imports,
    insights,
    insurance,
    me,
    people,
    project_export,
    project_import,
    projects,
    reimbursements,
    reports,
    reserves,
    review,
    rules,
    scenarios,
    system,
    transactions,
    transfers,
)

router = APIRouter()
router.include_router(system.router)
router.include_router(me.router)
router.include_router(households.router)
router.include_router(accounts.router)
router.include_router(categories.router)
# Weitere Router mit demselben Praefix /categories, bewusst getrennt gehalten:
# - category_suggestions: KI-Vorschlaege fuer fehlende Kategorien (Modellaufruf,
#   literale Pfade - deshalb vor den Musterrouten eingebunden).
# - category_operations: Lebenszyklus (Move/Archive/Restore/Merge) ueber
#   category_operations_service, mit Vorschau, Sperren und Idempotenz statt des
#   schlichten CRUD aus categories.py.
router.include_router(category_suggestions.router)
router.include_router(category_operations.router)
router.include_router(transactions.router)
router.include_router(transfers.router)
router.include_router(imports.router)
router.include_router(rules.router)
router.include_router(contracts.router)
router.include_router(people.router)
router.include_router(review.router)
router.include_router(budgets.router)
router.include_router(reports.router)
router.include_router(reserves.router)
# Liquiditaetsvorschau: leitet aus Konten, Vertraegen, Budget und Ruecklagen ab
# und haelt dafuer nur forecast_settings vor.
router.include_router(forecast.router)
router.include_router(scenarios.router)
router.include_router(reimbursements.router)
# Policenregister. Liest den Vertragsbestand, schreibt ihn aber nur ueber die
# Statuskopplung - der Beitrag bleibt Sache des Vertragsmoduls.
router.include_router(insurance.router)
router.include_router(analytics.router)
router.include_router(insights.router)
router.include_router(ai.router)
router.include_router(ai_tasks.router)
# KI-Feld: Chat zu den eigenen Finanzen; jede Frage laeuft als Aufgabe der
# KI-Warteschlange im Worker, live verfolgt ueber einen NDJSON-Strom.
router.include_router(assistant.router)
router.include_router(goals.router)
router.include_router(grocery.router)
router.include_router(groceries.router)
# Preisherkunft (Laeden, Sichtungen, Excel-Austausch) neben dem Essensplan.
# Bewusst VOR keinem Musterpfad noetig - grocery.py hat keine, die
# /ingredient-prices/{key}/detail verschlucken koennte.
router.include_router(grocery_prices.router)
router.include_router(devices.router)
router.include_router(projects.router)
router.include_router(debt_plans.router)
router.include_router(funding.router)
router.include_router(project_import.router)
router.include_router(project_export.router)
# Generischer Blick auf alle Haushaltstabellen samt Loeschen. Zuletzt
# eingebunden, weil er als einziger Router nicht einer Fachdomaene gehoert.
router.include_router(data_explorer.router)
# Derselbe Zuschnitt wie data_explorer - alle Haushaltstabellen auf einmal,
# hier aber als eine Datei zum Herunterladen und Zurueckspielen.
router.include_router(backup.router)
