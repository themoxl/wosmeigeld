// Zentrale fachliche Labels und Begriffe fuer die mobile App
// (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 2 "Fachliche Begriffe und
// Zahlen" und Abschnitt 3 "Zentrale Formatter statt weiterer
// ASCII-Labelkopien"). Frequenzen und Vertragsstatus standen bisher nur als
// lokale `Record<string, string>` in app/(tabs)/vertraege.tsx; die fuenf
// Budgetbegriffe (Budgetrest, Nach Fixkosten, Vertragssaldo, Monatssaldo,
// Ist-Ausgabenmix) werden in mehreren kommenden Screens gebraucht und duerfen
// laut Plan-Abschnitt 2 nicht synonym verwendet werden - deshalb hier als
// benannte Konstanten statt wiederholter Freitext-Strings je Screen.
import type {
  ContractDirection,
  ContractFrequency,
  ContractIncomeKind,
  ContractStatus,
  ContractType,
} from "../api/types";

// Werte entsprechen dem generierten Contract-Schema
// (packages/contracts/src/generated/api-types.ts) und den bisher in
// app/(tabs)/vertraege.tsx verwendeten Frequenzlabeln.
export const FREQUENCY_LABELS: Record<ContractFrequency, string> = {
  weekly: "Wöchentlich",
  monthly: "Monatlich",
  quarterly: "Quartalsweise",
  semiannual: "Halbjährlich",
  yearly: "Jährlich",
};

export function formatFrequency(frequency: ContractFrequency): string {
  return FREQUENCY_LABELS[frequency];
}

/**
 * Perioden je Jahr - gleiche Werte und derselbe Rechenweg wie im Backend
 * (services/api/app/services/contract_service.py, `_PERIODS_PER_YEAR`). Die
 * Monatsnormierung laeuft ueber das Jahr statt ueber Faktoren wie 52/12,
 * damit Woche und Jahr denselben Rundungsweg nehmen.
 */
const PERIODS_PER_YEAR: Record<ContractFrequency, number> = {
  weekly: 52,
  monthly: 12,
  quarterly: 4,
  semiannual: 2,
  yearly: 1,
};

/** Periodenbetrag in Cent auf einen Monatsbetrag normiert. */
export function toMonthlyMinor(amountMinor: number, frequency: ContractFrequency): number {
  return Math.round((amountMinor * PERIODS_PER_YEAR[frequency]) / 12);
}

// Werte entsprechen dem generierten Contract-Schema; "active" wird bewusst
// nicht als Textbadge angezeigt (Redesign-Plan Abschnitt 7: "Nicht aktive
// Stati erscheinen als Textbadge"), bleibt hier aber vollstaendig, damit
// jeder Aufrufer denselben Text fuer denselben Status erhaelt.
export const CONTRACT_STATUS_LABELS: Record<ContractStatus, string> = {
  active: "Aktiv",
  paused: "Pausiert",
  cancelled: "Gekündigt",
  ended: "Beendet",
  not_a_contract: "Kein Vertrag",
};

export function formatContractStatus(status: ContractStatus): string {
  return CONTRACT_STATUS_LABELS[status];
}

// Vertragsarten. Wortgleich mit CONTRACT_TYPE_LABEL der Weboberflaeche
// (apps/web/src/hooks/useContracts.ts): das Fixkostenregister der Berichte
// zeigt auf beiden Oberflaechen dieselbe Aufteilung, und zwei abweichende
// Uebersetzungen derselben zwoelf Schluessel waeren als Datenunterschied
// missverstaendlich.
export const CONTRACT_TYPE_LABELS: Record<ContractType, string> = {
  subscription: "Abonnement",
  insurance: "Versicherung",
  rent: "Miete & Wohnen",
  loan: "Kredit",
  installment: "Ratenkauf",
  utility: "Energie",
  telecom: "Telekommunikation",
  membership: "Mitgliedschaft",
  donation: "Spende",
  savings: "Sparplan",
  income: "Einnahme",
  other: "Sonstiges",
};

/**
 * `contract_type` kommt in den Berichtsantworten als blanker String (die
 * Fixkostenreihe gruppiert serverseitig), nicht als Literal-Union. Ein
 * unbekannter Schluessel wird deshalb unveraendert durchgereicht statt auf
 * "Sonstiges" abgebildet - eine neue Vertragsart soll auffallen, nicht in
 * einem Sammeltopf verschwinden.
 */
export function contractTypeLabel(contractType: string): string {
  return CONTRACT_TYPE_LABELS[contractType as ContractType] ?? contractType;
}

export const CONTRACT_DIRECTION_LABELS: Record<ContractDirection, string> = {
  expense: "Ausgaben",
  income: "Einnahmen",
};

export function formatContractDirection(direction: ContractDirection): string {
  return CONTRACT_DIRECTION_LABELS[direction];
}

// Einnahmeart (Liquiditaetsplan Stufe 6). Nicht mit dem Vertragstyp
// verwechseln: der sagt, WAS der Vertrag ist, die Art, WOHER das Geld kommt.
// Werte entsprechen dem generierten Contract-Schema.
export const INCOME_KIND_LABELS: Record<ContractIncomeKind, string> = {
  salary: "Gehalt",
  self_employment: "Selbstständigkeit",
  pension: "Rente",
  benefit: "Sozialleistung",
  rental: "Vermietung",
  capital: "Kapitalerträge",
  support: "Unterhalt",
  other: "Sonstige Einnahme",
};

/**
 * `null` heisst "noch keine Art gepflegt" - das ist der Bestand vor Stufe 6
 * und wird ausdruecklich benannt, statt still unter "Sonstige Einnahme" zu
 * verschwinden: sonst sieht eine offene Aufgabe wie eine Entscheidung aus.
 */
export function formatIncomeKind(kind: ContractIncomeKind | null | undefined): string {
  return kind ? INCOME_KIND_LABELS[kind] : "Ohne Art";
}

// -----------------------------------------------------------------------
// Fachliche Budgetbegriffe (Redesign-Plan Abschnitt 2). Die Tabellenspalte
// "Verbindliche UI-Bezeichnung" wird hier woertlich als Konstante abgebildet,
// damit kein Screen eigene Synonyme erfindet.

/**
 * Budgetrest = Plan + Uebertrag minus gebuchte Nettoausgaben.
 * Anzeige: "Budgetrest" oder "Rest im Monatsplan" - NIE nur "Verfügbar".
 */
export const BUDGET_REMAINING_LABEL = "Budgetrest";
export const BUDGET_REMAINING_LABEL_LONG = "Rest im Monatsplan";

/**
 * Nach Fixkosten = Einkommensbasis minus normierte Vertragsausgaben.
 * Anzeige: "Nach Fixkosten für variable Ausgaben".
 */
export const AFTER_FIXED_COSTS_LABEL = "Nach Fixkosten für variable Ausgaben";

/**
 * Vertragssaldo = normierte Vertragseinnahmen minus normierte
 * Vertragsausgaben. Anzeige: "Vertragssaldo" - nur dort verwenden, wo dieser
 * Vergleich fachlich gebraucht wird, nicht als Synonym fuer Budgetrest.
 */
export const CONTRACT_BALANCE_LABEL = "Vertragssaldo";

/**
 * Monatssaldo = gebuchte Einnahmen minus alle gebuchten Ausgaben.
 * Anzeige: "Monatssaldo".
 */
export const MONTH_BALANCE_LABEL = "Monatssaldo";

/**
 * 50/30/20-Felder = Anteil eines Buckets an den kategorisierten
 * Nettoausgaben. Anzeige: "Ist-Ausgabenmix" - NICHT "Budgetstatus" oder
 * Zielerreichung, denn ohne echte Zielquote gegen Einkommen ist es keine
 * Statusaussage.
 */
export const SPENDING_MIX_LABEL = "Ist-Ausgabenmix";

// Einnahmekategorien verwenden `income_actual_minor`; die Werte heissen
// bewusst nicht "Ausgegeben" oder pauschal "Rest" (Redesign-Plan Abschnitt 2).
export const INCOME_EXPECTED_LABEL = "Erwartet";
export const INCOME_RECEIVED_LABEL = "Eingegangen";
export const INCOME_STILL_EXPECTED_LABEL = "Noch erwartet";

/**
 * Formatiert den Erstattungsueberschuss-Text fuer negative `spent_minor`-
 * Werte: ein negativer `spent_minor` ist kein negativer Einkauf, sondern eine
 * Erstattung, die die Ausgaben uebersteigt (Redesign-Plan Abschnitt 2). Die UI
 * zeigt dann z. B. "36,60 EUR mehr erstattet als ausgegeben" statt eines
 * negativen Werts unter "Ausgegeben".
 *
 * @param formattedOverpayment bereits formatierter Betrag (z. B. ueber
 *   `formatMinorUnits`) des positiven Ueberschusses, etwa "36,60 EUR".
 */
export function formatReimbursementSurplus(formattedOverpayment: string): string {
  return `${formattedOverpayment} mehr erstattet als ausgegeben`;
}

/**
 * Sichtbar unterschiedene Herkunft der Fixkosten-Einkommensbasis
 * (`fixed_costs_income_basis`, Redesign-Plan Abschnitt 2): entweder auf Basis
 * gebuchter Einnahmen oder als Schaetzung aus Einnahmevertraegen.
 */
export const INCOME_BASIS_ACTUAL_LABEL = "auf Basis gebuchter Einnahmen";
export const INCOME_BASIS_ESTIMATED_LABEL = "Schätzung aus Einnahmeverträgen";

export function formatIncomeBasisSource(isEstimated: boolean): string {
  return isEstimated ? INCOME_BASIS_ESTIMATED_LABEL : INCOME_BASIS_ACTUAL_LABEL;
}
