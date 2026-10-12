"""Export von Vorhaben-Szenarien (Plan Abschnitt 10.3): XLSX-Arbeitsmappe mit
acht festen Blaettern und CSV-Export der Kostenpositionen.

Reine Praesentationsschicht -- alle Werte kommen unveraendert aus
`project_service.py` (Kosten/Finanzierung/Haushaltsannahmen/Cashflow),
`debt_plan_service.py` (Tilgungsplaene) und `funding_catalog_service.py`
(Foerderprofil/-check). Dieser Dienst berechnet nichts neu, er formatiert nur
-- keine Formeln, damit der Export reproduzierbar bleibt (Plan 10.3)."""

import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.debt_plans import DebtPlan
from app.schemas.funding import EligibilityProfile, FundingCheckRunResponse
from app.schemas.projects import (
    CashflowAssumptions,
    CostActual,
    CostItem,
    CostSection,
    FinancingSource,
    GoalAssumption,
    MajorProject,
    ProjectScenario,
    RecurringImpact,
    ScenarioCalculation,
    ScenarioTimeline,
)
from app.services import debt_plan_service, funding_catalog_service, project_service

_MONEY_FORMAT: str = "#,##0.00"


@dataclass(frozen=True)
class _SheetHeader:
    """Kopfbloecke, die laut Plan 10.3 auf JEDEM Blatt stehen: Projekt,
    Szenario, Datenstand, Waehrung, Engine-Version."""

    project: MajorProject
    scenario: ProjectScenario
    as_of_date: date
    currency: str
    engine_version: str


def _naive_utc(value: datetime) -> datetime:
    """Excel/openpyxl kennt keine zeitzonenbewussten datetime-Werte -- unsere
    `timestamptz`-Spalten kommen aber tz-aware aus Postgres. Auf UTC
    normalisieren und die tzinfo abstreifen, statt den Zeitpunkt stillschweigend
    in der Serverzeitzone zu verschieben."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


_FORMULA_TRIGGER_CHARS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_text(value: str | None) -> str | None:
    """Verhindert CSV-/Formel-Injection (CWE-1236): ein Freitextfeld (Name,
    Anbieter, Notiz - z. B. aus einem zuvor importierten Kostenplan
    uebernommen) koennte mit einem Formel-ausloesenden Zeichen beginnen und
    wuerde beim Oeffnen dieses Exports in Excel als Formel statt als Text
    ausgewertet. Ein fuehrendes Apostroph erzwingt Textinterpretation, ohne
    den sichtbaren Wert zu veraendern."""
    if value is None or value == "":
        return value
    if value.startswith(_FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def _apply_money_cell(ws: Worksheet, row: int, col: int, value_minor: int | None) -> None:
    """Schreibt eine Geldspalte als reine Zahl (`value_minor / 100`), nie als
    lokalisierten Text -- Plan 10.3 verlangt Zahlen mit Zahlenformat, keine
    Textbetraege, und keine Formeln."""
    value = value_minor / 100 if value_minor is not None else None
    cell = ws.cell(row=row, column=col, value=value)
    if value is not None:
        cell.number_format = _MONEY_FORMAT


def _write_header_block(ws: Worksheet, header: _SheetHeader) -> None:
    ws.append(["Projekt:", _sanitize_text(header.project.name)])
    ws.append(["Szenario:", _sanitize_text(header.scenario.name)])
    ws.append(["Datenstand:", header.as_of_date])
    ws.append(["Waehrung:", header.currency])
    ws.append(["Engine-Version:", header.engine_version])
    ws.append([])


# ---------------------------------------------------------------------------
# 1. Zusammenfassung
# ---------------------------------------------------------------------------


def _write_summary_sheet(ws: Worksheet, header: _SheetHeader, calc: ScenarioCalculation) -> None:
    _write_header_block(ws, header)
    ws.append(["Kennzahl", "Wert"])

    ws.append(["Szenario-ID", str(calc.scenario_id)])
    ws.append(["Szenario-Version", calc.scenario_version])
    ws.append(["Eingabe-Fingerabdruck", calc.input_fingerprint])
    ws.append(["Engine-Version", calc.engine_version])
    ws.append(["Datenstand", calc.as_of_date])

    # per_item_estimate_minor/per_item_forecast_minor sind Dicts je
    # Kostenposition, keine einzelne Kennzahl -- diese Details stehen bereits
    # granular im Blatt "Kosten" (estimate_minor/forecast_minor je Zeile).
    money_fields: list[tuple[str, int]] = [
        ("Geschaetzte Kosten", calc.estimated_cost_minor),
        ("Angebotene Kosten", calc.quoted_cost_minor),
        ("Beauftragte Kosten", calc.committed_cost_minor),
        ("Prognose Barkosten", calc.forecast_cash_cost_minor),
        ("Nicht-Barkosten", calc.non_cash_cost_minor),
        ("Reserve (Contingency)", calc.contingency_minor),
        ("Projekt-Barkosten", calc.project_cash_cost_minor),
        ("Projekt-Wirtschaftskosten", calc.project_economic_cost_minor),
        ("Ist-Kosten", calc.actual_cost_minor),
        ("Verbleibende Prognose", calc.remaining_forecast_minor),
    ]
    for label, value_minor in money_fields:
        ws.append([label, None])
        _apply_money_cell(ws, ws.max_row, 2, value_minor)

    ws.append(["Warnungen", "; ".join(calc.warnings) if calc.warnings else ""])


# ---------------------------------------------------------------------------
# 2. Kosten
# ---------------------------------------------------------------------------

_COSTS_HEADER: list[str] = [
    "Position", "Abschnitt", "Status", "Berechnungsart", "Menge", "Einheit",
    "Einzelpreis", "Schaetzung", "Prognose", "Ist", "Angebot", "Beauftragt",
    "Anbieter", "Start geplant", "Ende geplant", "Notiz",
]


def _write_costs_sheet(
    ws: Worksheet, header: _SheetHeader, sections: list[CostSection], items: list[CostItem]
) -> None:
    _write_header_block(ws, header)
    ws.append(_COSTS_HEADER)
    section_names = {section.id: _sanitize_text(section.name) for section in sections}
    for item in items:
        ws.append(
            [
                _sanitize_text(item.name),
                section_names.get(item.section_id, ""),
                item.status,
                item.calculation_mode,
                float(item.quantity) if item.quantity is not None else None,
                _sanitize_text(item.unit_label),
                None,
                None,
                None,
                None,
                None,
                None,
                _sanitize_text(item.vendor_label),
                item.expected_start_on,
                item.expected_end_on,
                _sanitize_text(item.note),
            ]
        )
        row = ws.max_row
        _apply_money_cell(ws, row, 7, item.unit_price_minor)
        _apply_money_cell(ws, row, 8, item.estimate_minor)
        _apply_money_cell(ws, row, 9, item.forecast_minor)
        _apply_money_cell(ws, row, 10, item.actual_amount_minor)
        _apply_money_cell(ws, row, 11, item.quoted_amount_minor)
        _apply_money_cell(ws, row, 12, item.committed_amount_minor)


# ---------------------------------------------------------------------------
# 3. Finanzierung
# ---------------------------------------------------------------------------

_FINANCING_HEADER: list[str] = [
    "Name", "Art", "Status", "Geplanter Betrag", "Kreditrahmen", "Verfuegbar ab",
    "Bruttobetrag", "Abzuege", "Foerderprogramm-Version-ID", "Darlehen vorhanden", "Notiz",
]


def _write_financing_sheet(
    ws: Worksheet, header: _SheetHeader, financing_sources: list[FinancingSource]
) -> None:
    _write_header_block(ws, header)
    ws.append(_FINANCING_HEADER)
    for source in financing_sources:
        ws.append(
            [
                _sanitize_text(source.name),
                source.source_type,
                source.status,
                None,
                None,
                source.available_on,
                None,
                None,
                str(source.funding_program_version_id)
                if source.funding_program_version_id
                else "",
                "Ja" if source.has_debt_plan else "Nein",
                _sanitize_text(source.note),
            ]
        )
        row = ws.max_row
        _apply_money_cell(ws, row, 4, source.planned_amount_minor)
        _apply_money_cell(ws, row, 5, source.principal_limit_minor)
        _apply_money_cell(ws, row, 7, source.gross_amount_minor)
        _apply_money_cell(ws, row, 8, source.deductions_minor)


# ---------------------------------------------------------------------------
# 4. Tilgungsplaene
# ---------------------------------------------------------------------------

_DEBT_PLAN_HEADER: list[str] = [
    "Finanzierungsquelle", "Darlehen", "Kreditgeber", "Status", "Waehrung",
    "Kreditrahmen", "Eroeffnungssaldo", "Beginn", "Zahlintervall (Monate)",
    "Tilgungsart", "Feste Rate", "Anfangstilgung (bps)", "Zielsatzlaufzeit (Monate)",
    "Tilgungsfreie Monate", "Zahlungsfreie Monate", "Bereitstellungsfreie Monate",
    "Bereitstellungszins (bps/Monat)",
]


def _write_debt_plans_sheet(
    ws: Worksheet,
    header: _SheetHeader,
    financing_sources: list[FinancingSource],
    debt_plans_by_source: dict[UUID, list[DebtPlan]],
) -> None:
    _write_header_block(ws, header)
    ws.append(_DEBT_PLAN_HEADER)
    has_any_plan = False
    for source in financing_sources:
        for plan in debt_plans_by_source.get(source.id, []):
            has_any_plan = True
            ws.append(
                [
                    _sanitize_text(source.name),
                    _sanitize_text(plan.name),
                    _sanitize_text(plan.creditor_label),
                    plan.status,
                    plan.currency,
                    None,
                    None,
                    date.fromisoformat(plan.starts_on),
                    plan.payment_interval_months,
                    plan.amortization_mode,
                    None,
                    plan.initial_repayment_rate_bps,
                    plan.target_term_months,
                    plan.repayment_free_months,
                    plan.payment_free_months,
                    plan.commitment_free_months,
                    plan.commitment_rate_bps_per_month,
                ]
            )
            row = ws.max_row
            _apply_money_cell(ws, row, 6, plan.principal_limit_minor)
            _apply_money_cell(ws, row, 7, plan.opening_balance_minor)
            _apply_money_cell(ws, row, 11, plan.fixed_payment_minor)
    if not has_any_plan:
        ws.append(["Keine Tilgungsplaene fuer dieses Szenario hinterlegt."])


# ---------------------------------------------------------------------------
# 5. Cashflow
# ---------------------------------------------------------------------------

_CASHFLOW_HEADER: list[str] = [
    "Monat", "Basiseinkommen", "Basis-Lebenshaltung", "Aktuelles Wohnen",
    "Bestehender Schuldendienst", "Sonstige Verpflichtungen", "Projekt-Kostenabfluesse",
    "Darlehens-/Foerderzufluesse", "Neuer Schuldendienst", "Wiederkehrende Projektwirkungen",
    "Zielbeitraege", "Netto-Haushaltscashflow", "Liquiditaetsreserve Ende",
]


def _write_cashflow_sheet(ws: Worksheet, header: _SheetHeader, timeline: ScenarioTimeline) -> None:
    _write_header_block(ws, header)
    ws.append(_CASHFLOW_HEADER)
    for month_row in timeline.months:
        placeholders: list[int | None] = [None] * 12
        ws.append([month_row.month, *placeholders])
        row = ws.max_row
        _apply_money_cell(ws, row, 2, month_row.baseline_income_minor)
        _apply_money_cell(ws, row, 3, month_row.baseline_living_costs_minor)
        _apply_money_cell(ws, row, 4, month_row.current_housing_minor)
        _apply_money_cell(ws, row, 5, month_row.existing_debt_service_minor)
        _apply_money_cell(ws, row, 6, month_row.other_commitments_minor)
        _apply_money_cell(ws, row, 7, month_row.project_cost_outflows_minor)
        _apply_money_cell(ws, row, 8, month_row.loan_and_grant_inflows_minor)
        _apply_money_cell(ws, row, 9, month_row.new_debt_service_minor)
        _apply_money_cell(ws, row, 10, month_row.recurring_project_impacts_minor)
        _apply_money_cell(ws, row, 11, month_row.goal_contributions_minor)
        _apply_money_cell(ws, row, 12, month_row.net_household_cashflow_minor)
        _apply_money_cell(ws, row, 13, month_row.liquid_reserve_end_minor)
    if not timeline.months:
        ws.append(["Keine Cashflow-Monate berechnet (fehlende Haushaltsannahmen)."])


# ---------------------------------------------------------------------------
# 6. Foerderungen
# ---------------------------------------------------------------------------

_FUNDING_HEADER: list[str] = [
    "Programm", "Anbieter", "Status", "Foerderung min.", "Foerderung max.",
    "Darlehen min.", "Darlehen max.", "Katalogalter (Tage)", "Geprueft am",
]


def _write_funding_sheet(
    ws: Worksheet,
    header: _SheetHeader,
    eligibility_profile: EligibilityProfile | None,
    funding_check: FundingCheckRunResponse | None,
) -> None:
    _write_header_block(ws, header)
    if eligibility_profile is not None:
        ws.append(
            [
                "Foerderprofil hinterlegt:",
                "Ja",
                "Aktualisiert am:",
                _naive_utc(eligibility_profile.updated_at),
            ]
        )
    else:
        ws.append(["Foerderprofil hinterlegt:", "Nein"])
    ws.append([])

    if funding_check is None:
        ws.append(["Kein Foerdercheck fuer dieses Szenario durchgefuehrt."])
        return

    ws.append(_FUNDING_HEADER)
    for match in funding_check.matches:
        ws.append(
            [
                _sanitize_text(match.program_title),
                _sanitize_text(match.provider_name),
                match.status,
                None,
                None,
                None,
                None,
                match.catalog_age_days,
                _naive_utc(match.checked_at),
            ]
        )
        row = ws.max_row
        _apply_money_cell(ws, row, 4, match.estimated_grant_min_minor)
        _apply_money_cell(ws, row, 5, match.estimated_grant_max_minor)
        _apply_money_cell(ws, row, 6, match.estimated_loan_min_minor)
        _apply_money_cell(ws, row, 7, match.estimated_loan_max_minor)
    if not funding_check.matches:
        ws.append(["Keine Treffer im letzten Foerdercheck."])


# ---------------------------------------------------------------------------
# 7. Annahmen
# ---------------------------------------------------------------------------

_ASSUMPTIONS_HOUSEHOLD_HEADER: list[str] = [
    "Startliquiditaet", "Monatl. Nettoeinkommen", "Monatl. Lebenshaltung",
    "Monatl. akt. Wohnkosten", "Monatl. bestehender Schuldendienst",
    "Monatl. sonstige Verpflichtungen", "Analysezeitraum (Monate)", "Quelle", "Aktualisiert am",
]
_RECURRING_HEADER: list[str] = [
    "Name", "Art", "Betrag", "Frequenz", "Beginn", "Ende", "Konfidenz",
    "In Leistbarkeit beruecksichtigt", "Notiz",
]
_GOAL_ASSUMPTION_HEADER: list[str] = [
    "Sparziel-ID", "Entnahme", "Entnahme am", "Geschuetztes Minimum",
    "Basisbeitrag/Monat", "Projektbeitrag/Monat",
]


def _write_assumptions_sheet(
    ws: Worksheet,
    header: _SheetHeader,
    cashflow_assumptions: CashflowAssumptions | None,
    recurring_impacts: list[RecurringImpact],
    goal_assumptions: list[GoalAssumption],
) -> None:
    _write_header_block(ws, header)

    ws.append(["Haushaltsannahmen"])
    if cashflow_assumptions is None:
        ws.append(["Keine Haushaltsannahmen hinterlegt."])
    else:
        ws.append(_ASSUMPTIONS_HOUSEHOLD_HEADER)
        ws.append(
            [
                None, None, None, None, None, None,
                cashflow_assumptions.analysis_lookback_months,
                cashflow_assumptions.source,
                _naive_utc(cashflow_assumptions.updated_at),
            ]
        )
        row = ws.max_row
        _apply_money_cell(ws, row, 1, cashflow_assumptions.starting_liquidity_minor)
        _apply_money_cell(ws, row, 2, cashflow_assumptions.monthly_net_income_minor)
        _apply_money_cell(ws, row, 3, cashflow_assumptions.monthly_living_costs_minor)
        _apply_money_cell(ws, row, 4, cashflow_assumptions.monthly_current_housing_minor)
        _apply_money_cell(ws, row, 5, cashflow_assumptions.monthly_existing_debt_service_minor)
        _apply_money_cell(ws, row, 6, cashflow_assumptions.monthly_other_commitments_minor)
    ws.append([])

    ws.append(["Folgekosten und -einnahmen"])
    ws.append(_RECURRING_HEADER)
    for impact in recurring_impacts:
        ws.append(
            [
                _sanitize_text(impact.name),
                impact.impact_type,
                None,
                impact.frequency,
                impact.starts_on,
                impact.ends_on,
                impact.confidence,
                "Ja" if impact.include_in_affordability else "Nein",
                _sanitize_text(impact.note),
            ]
        )
        _apply_money_cell(ws, ws.max_row, 3, impact.amount_minor)
    if not recurring_impacts:
        ws.append(["Keine Folgekosten/-einnahmen hinterlegt."])
    ws.append([])

    ws.append(["Zielannahmen"])
    ws.append(_GOAL_ASSUMPTION_HEADER)
    for goal in goal_assumptions:
        ws.append([str(goal.savings_goal_id), None, goal.withdrawal_on, None, None, None])
        row = ws.max_row
        _apply_money_cell(ws, row, 2, goal.withdrawal_minor)
        _apply_money_cell(ws, row, 4, goal.protected_minimum_minor)
        _apply_money_cell(ws, row, 5, goal.baseline_monthly_contribution_minor)
        _apply_money_cell(ws, row, 6, goal.project_monthly_contribution_minor)
    if not goal_assumptions:
        ws.append(["Keine Zielannahmen hinterlegt."])


# ---------------------------------------------------------------------------
# 8. Ist-Zuordnungen
# ---------------------------------------------------------------------------

_ACTUALS_HEADER: list[str] = [
    "Position", "Zuordnungs-ID", "Kostenposition-ID", "Quelle", "Buchungs-ID",
    "Betrag", "Datum", "Notiz", "Archiviert", "Erstellt am",
]


def _write_actuals_sheet(
    ws: Worksheet,
    header: _SheetHeader,
    items: list[CostItem],
    actuals_by_item: dict[UUID, list[CostActual]],
) -> None:
    _write_header_block(ws, header)
    ws.append(_ACTUALS_HEADER)
    has_any = False
    for item in items:
        for actual in actuals_by_item.get(item.id, []):
            has_any = True
            ws.append(
                [
                    _sanitize_text(item.name),
                    str(actual.id),
                    str(actual.cost_item_id),
                    actual.source,
                    str(actual.transaction_id) if actual.transaction_id else "",
                    None,
                    actual.occurred_on,
                    _sanitize_text(actual.note),
                    "Ja" if actual.is_archived_link else "Nein",
                    _naive_utc(actual.created_at),
                ]
            )
            _apply_money_cell(ws, ws.max_row, 6, actual.amount_minor)
    if not has_any:
        ws.append(["Keine Ist-Zuordnungen vorhanden."])


# ---------------------------------------------------------------------------
# Oeffentliche Schnittstelle
# ---------------------------------------------------------------------------


async def export_scenario_xlsx(
    session: AsyncSession, household_id: UUID, project_id: UUID, scenario_id: UUID
) -> bytes:
    """Baut die 8-Blatt-Arbeitsmappe (Plan 10.3, in dieser Reihenfolge:
    Zusammenfassung, Kosten, Finanzierung, Tilgungsplaene, Cashflow,
    Foerderungen, Annahmen, Ist-Zuordnungen) und gibt die rohen XLSX-Bytes
    zurueck. Holt alle Werte unveraendert aus den bestehenden Diensten --
    hier wird nichts neu berechnet, nur formatiert."""
    project = await project_service.require_project(session, household_id, project_id)
    scenario = await project_service.require_scenario(
        session, household_id, project_id, scenario_id
    )
    calc = await project_service.calculate_scenario(session, household_id, project_id, scenario_id)
    sections = await project_service.list_cost_sections(session, household_id, scenario_id)
    items = await project_service.list_cost_items(session, household_id, scenario_id)
    financing_sources = await project_service.list_financing_sources(
        session, household_id, scenario_id
    )
    cashflow_assumptions = await project_service.get_cashflow_assumptions(
        session, household_id, scenario_id
    )
    recurring_impacts = await project_service.list_recurring_impacts(
        session, household_id, scenario_id
    )
    goal_assumptions = await project_service.list_goal_assumptions(
        session, household_id, scenario_id
    )
    timeline = await project_service.get_timeline(session, household_id, project_id, scenario_id)
    eligibility_profile = await funding_catalog_service.get_eligibility_profile(
        session, household_id, project_id
    )
    funding_check = await funding_catalog_service.get_latest_funding_check(
        session, household_id, scenario_id
    )

    actuals_by_item: dict[UUID, list[CostActual]] = {}
    for item in items:
        actuals_by_item[item.id] = await project_service.list_actuals_for_item(
            session, household_id, item.id
        )

    debt_plans_by_source: dict[UUID, list[DebtPlan]] = {}
    for source in financing_sources:
        debt_plans_by_source[source.id] = await debt_plan_service.list_debt_plans(
            session, household_id, project_financing_source_id=source.id
        )

    header = _SheetHeader(
        project=project,
        scenario=scenario,
        as_of_date=calc.as_of_date,
        currency=project.currency,
        engine_version=calc.engine_version,
    )

    workbook = Workbook()
    summary_ws = workbook.active
    assert summary_ws is not None
    summary_ws.title = "Zusammenfassung"
    _write_summary_sheet(summary_ws, header, calc)

    _write_costs_sheet(workbook.create_sheet("Kosten"), header, sections, items)
    _write_financing_sheet(workbook.create_sheet("Finanzierung"), header, financing_sources)
    _write_debt_plans_sheet(
        workbook.create_sheet("Tilgungsplaene"), header, financing_sources, debt_plans_by_source
    )
    _write_cashflow_sheet(workbook.create_sheet("Cashflow"), header, timeline)
    _write_funding_sheet(
        workbook.create_sheet("Foerderungen"), header, eligibility_profile, funding_check
    )
    _write_assumptions_sheet(
        workbook.create_sheet("Annahmen"),
        header,
        cashflow_assumptions,
        recurring_impacts,
        goal_assumptions,
    )
    _write_actuals_sheet(workbook.create_sheet("Ist-Zuordnungen"), header, items, actuals_by_item)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _minor_to_csv(value_minor: int | None) -> str:
    if value_minor is None:
        return ""
    return str(round(value_minor / 100, 2))


def _decimal_to_csv(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(round(float(value), 2))


async def export_cost_items_csv(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> str:
    """CSV-Export der Kostenpositionen (eine Zeile pro Position, Plan 10.3).
    Deutsches Excel-Trennzeichen (`;`) plus UTF-8-BOM, damit Excel Umlaute
    korrekt zeigt statt die Datei als ein-Spalten-Text zu interpretieren."""
    items = await project_service.list_cost_items(session, household_id, scenario_id)
    sections = await project_service.list_cost_sections(session, household_id, scenario_id)
    section_names = {section.id: _sanitize_text(section.name) or "" for section in sections}

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Abschnitt", "Position", "Status", "Berechnungsart", "Menge", "Einheit",
            "Einzelpreis", "Schaetzung", "Prognose", "Ist", "Angebot", "Beauftragt",
            "Anbieter", "Start geplant", "Ende geplant", "Notiz",
        ]
    )
    for item in items:
        writer.writerow(
            [
                section_names.get(item.section_id, ""),
                _sanitize_text(item.name),
                item.status,
                item.calculation_mode,
                _decimal_to_csv(item.quantity),
                _sanitize_text(item.unit_label) or "",
                _minor_to_csv(item.unit_price_minor),
                _minor_to_csv(item.estimate_minor),
                _minor_to_csv(item.forecast_minor),
                _minor_to_csv(item.actual_amount_minor),
                _minor_to_csv(item.quoted_amount_minor),
                _minor_to_csv(item.committed_amount_minor),
                _sanitize_text(item.vendor_label) or "",
                item.expected_start_on.isoformat() if item.expected_start_on else "",
                item.expected_end_on.isoformat() if item.expected_end_on else "",
                _sanitize_text(item.note) or "",
            ]
        )
    return "\ufeff" + buffer.getvalue()
