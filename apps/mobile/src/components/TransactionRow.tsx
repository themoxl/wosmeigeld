import type { ReactNode } from "react";
import type { Transaction } from "../api/types";
import { isPendingTransaction } from "../offline/pendingTransactions";
import { formatTransactionDayLabel } from "../utils/dates";
import { Badge, DataRow, Money } from "./ui";

type Props = {
  transaction: Transaction;
  accountName: string;
  categoryName: string | null;
  isPrivacyMode?: boolean;
  /** Trennlinie unten - in einer Kartengruppe fuer die letzte Zeile abschalten. */
  divider?: boolean;
};

/**
 * Titel einer Buchungszeile. Liegt hier, damit Aufrufer denselben Text fuer
 * ihr accessibilityLabel verwenden koennen wie die sichtbare Zeile - sonst
 * lesen Bildschirmleser einen anderen Namen vor, als danebensteht.
 */
export function transactionTitle(transaction: Transaction, accountName: string): string {
  return transaction.payee_label || accountName || "Buchung";
}

/**
 * Buchungszeile des Showcase (Abschnitt 7): Haendlerkachel, Titel mit
 * Markern, "Heute · Supermarkt" darunter, rechts der Betrag.
 *
 * Die Kachelfarbe kommt aus dem Namen des Zahlungsempfaengers (toneForLabel in
 * <Avatar>) statt aus einer Liste bekannter Marken - so bekommt auch der
 * kleine Baecker um die Ecke eine wiedererkennbare Kachel, ohne dass jemand
 * sie pflegen muss.
 */
export function TransactionRow({
  transaction,
  accountName,
  categoryName,
  isPrivacyMode,
  divider = true,
}: Props) {
  const categoryLabel = transaction.is_split
    ? "Mehrere Kategorien"
    : (categoryName ?? "Ohne Kategorie");

  // Die Notiz haengt an dieselbe Zeile an, statt eine dritte aufzumachen: in
  // einer Liste zaehlt die gleiche Hoehe jeder Zeile mehr als die vollstaendige
  // Notiz - ganz steht sie auf der Detailseite.
  const subtitleParts = [formatTransactionDayLabel(transaction.booked_date), categoryLabel];
  if (transaction.note) subtitleParts.push(transaction.note);

  const markers: ReactNode[] = [];
  if (transaction.is_split) {
    markers.push(<Badge key="split" label="Split" tone="purple" size="small" />);
  }
  if (transaction.is_transfer) {
    markers.push(<Badge key="transfer" label="Umbuchung" tone="info" size="small" />);
  }
  if (isPendingTransaction(transaction)) {
    markers.push(<Badge key="pending" label="Vorgemerkt" tone="highlight" size="small" />);
  }

  return (
    <DataRow
      title={transactionTitle(transaction, accountName)}
      subtitle={subtitleParts.join(" · ")}
      titleBadge={markers.length > 0 ? <>{markers}</> : undefined}
      divider={divider}
      trailing={
        <Money
          minorUnits={transaction.amount_minor}
          currency={transaction.currency}
          isPrivacyMode={isPrivacyMode}
        />
      }
    />
  );
}
