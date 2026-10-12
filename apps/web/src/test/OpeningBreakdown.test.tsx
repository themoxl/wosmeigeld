/**
 * Aufschluesselung des Startpunkts.
 *
 * Der Startsaldo war eine Zahl ohne Herkunft - genau daran ist er
 * unbrauchbar geworden. Hier wird geprueft, dass jedes Konto sichtbar ist,
 * auch die bewusst ausgeschlossenen.
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OpeningBreakdown } from "../components/forecast/OpeningBreakdown";
import { excludedTotalMinor } from "../components/forecast/openingHelpers";
import type { ForecastOpeningAccount, ForecastResponse } from "../lib/types";

const accounts: ForecastOpeningAccount[] = [
  {
    account_id: "a1",
    // Der Zusatz "A" haelt den Kontonamen von der Kontoart-Beschriftung
    // ("Girokonto") auseinander, die in derselben Zeile steht.
    name: "Girokonto A",
    account_type: "checking",
    included: true,
    balance_minor: 300000,
    last_booking_on: "2026-01-15",
    reason: "heuristic",
  },
  {
    account_id: "a2",
    name: "Sparkonto",
    account_type: "checking",
    included: true,
    balance_minor: 150000,
    last_booking_on: "2026-02-01",
    reason: "heuristic",
  },
  {
    account_id: "a3",
    name: "Kreditkarte A",
    account_type: "credit_card",
    included: false,
    balance_minor: -100000,
    last_booking_on: "2026-01-31",
    reason: "heuristic",
  },
  {
    account_id: "a4",
    name: "Zweitkonto",
    account_type: "credit_card",
    included: false,
    balance_minor: -50000,
    last_booking_on: "2026-01-15",
    reason: "explicit",
  },
];

// 300000 + 150000 = 450000 - die Summe der einbezogenen Konten ist der
// Startpunkt der Vorschau.
const forecast = {
  opening_liquidity_minor: 450000,
  opening_accounts: accounts,
} as unknown as ForecastResponse;

const formatDate = (value: string | Date) => String(value);

describe("OpeningBreakdown", () => {
  it("zeigt jedes einbezogene Konto mit Saldo und Stand", () => {
    render(<OpeningBreakdown forecast={forecast} formatDate={formatDate} />);
    const table = screen.getByTestId("opening-accounts");
    expect(within(table).getByText("Girokonto A")).toBeInTheDocument();
    expect(within(table).getByText("3.000,00 €")).toBeInTheDocument();
    expect(within(table).getByText("1.500,00 €")).toBeInTheDocument();
    // Eindeutiges Datum: 2026-01-15 tragen zwei Konten.
    expect(within(table).getByText("2026-02-01")).toBeInTheDocument();
    // Die Summe ist genau der Startpunkt der Vorschau.
    expect(within(table).getByText("4.500,00 €")).toBeInTheDocument();
  });

  it("nennt ausgeschlossene Konten mit ihrem Saldo statt sie zu verschweigen", () => {
    render(<OpeningBreakdown forecast={forecast} formatDate={formatDate} />);
    const table = screen.getByTestId("opening-accounts");
    // Ein nicht mitgezaehltes Kreditkartenminus laesst die Lage besser
    // aussehen, als sie ist - deshalb steht es sichtbar dabei.
    expect(within(table).getByText("Kreditkarte A")).toBeInTheDocument();
    expect(within(table).getByText("-1.000,00 €")).toBeInTheDocument();
    expect(within(table).getByText("-1.500,00 €")).toBeInTheDocument();
    expect(within(table).getByText(/von dir festgelegt/)).toBeInTheDocument();
  });

  it("rechnet die Summe der ausgeschlossenen Konten", () => {
    expect(excludedTotalMinor(accounts)).toBe(-150000);
  });

  it("bleibt still, wenn keine Konten geliefert werden", () => {
    const leer = { opening_liquidity_minor: 0, opening_accounts: [] } as unknown as ForecastResponse;
    const { container } = render(<OpeningBreakdown forecast={leer} formatDate={formatDate} />);
    expect(container).toBeEmptyDOMElement();
  });
});
