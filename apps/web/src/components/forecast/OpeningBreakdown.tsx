import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { ForecastOpeningAccount, ForecastResponse } from "../../lib/types";
import { excludedTotalMinor } from "./openingHelpers";

// Woraus der Startpunkt besteht (Nachtrag zu Plan 4.1 und 4.5).
//
// Der Startsaldo war eine einzelne Zahl ohne Herkunft. Steht dort ein
// unerwarteter Wert, konnte der Nutzer nicht sehen, welches Konto ihn
// verursacht - und musste der Vorschau glauben oder sie verwerfen. Beides ist
// schlecht. Ausgeschlossene Konten stehen mit ihrem Saldo dabei: ein nicht
// mitgezaehltes Kreditkartenminus laesst die Lage besser aussehen, als sie ist.

const eur = formatMinorUnitsAsEuro;

const TYPE_LABELS: Record<string, string> = {
  checking: "Girokonto",
  cash: "Bargeld",
  savings: "Sparkonto",
  credit_card: "Kreditkarte",
  brokerage: "Depot",
  loan: "Darlehen",
  real_estate: "Immobilie",
  other: "Sonstiges",
};

export function OpeningBreakdown({
  forecast,
  formatDate,
}: {
  forecast: ForecastResponse;
  formatDate: (value: string | Date) => string;
}) {
  const accounts = forecast.opening_accounts ?? [];
  if (accounts.length === 0) return null;

  const included = accounts.filter((a) => a.included);
  const excluded = accounts.filter((a) => !a.included);
  const excludedSum = excludedTotalMinor(accounts);

  const row = (account: ForecastOpeningAccount) => (
    <tr key={account.account_id} className={account.included ? "" : "opening-row--excluded"}>
      <td className="txn-td">
        {account.name}
        {account.reason === "explicit" ? (
          <span className="opening-reason"> · von dir festgelegt</span>
        ) : null}
      </td>
      <td className="txn-td">{TYPE_LABELS[account.account_type] ?? account.account_type}</td>
      <td className="txn-td">
        {account.last_booking_on ? formatDate(account.last_booking_on) : "keine Buchung"}
      </td>
      <td
        className={`txn-td txn-td--amount ${account.balance_minor < 0 ? "transaction-amount--negative" : ""}`}
      >
        {eur(account.balance_minor)}
      </td>
    </tr>
  );

  return (
    <div className="opening" data-testid="opening-breakdown">
      <div className="txn-table-wrapper">
        <table className="txn-table" data-testid="opening-accounts">
          <thead>
            <tr>
              <th className="txn-th">Konto</th>
              <th className="txn-th">Art</th>
              <th className="txn-th">Letzte Buchung</th>
              <th className="txn-th txn-th--amount">Saldo</th>
            </tr>
          </thead>
          <tbody>
            {included.map(row)}
            <tr className="opening-row--sum">
              <td className="txn-td" colSpan={3}>
                <strong>Startpunkt der Vorschau</strong>
              </td>
              <td
                className={`txn-td txn-td--amount ${forecast.opening_liquidity_minor < 0 ? "transaction-amount--negative" : ""}`}
              >
                <strong>{eur(forecast.opening_liquidity_minor)}</strong>
              </td>
            </tr>
            {excluded.length > 0 ? (
              <>
                <tr className="opening-row--divider">
                  <td className="txn-td" colSpan={4}>
                    Nicht einbezogen — diese Salden fehlen im Verlauf oben
                  </td>
                </tr>
                {excluded.map(row)}
                <tr className="opening-row--sum">
                  <td className="txn-td" colSpan={3}>
                    Summe nicht einbezogen
                  </td>
                  <td
                    className={`txn-td txn-td--amount ${excludedSum < 0 ? "transaction-amount--negative" : ""}`}
                  >
                    {eur(excludedSum)}
                  </td>
                </tr>
              </>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
