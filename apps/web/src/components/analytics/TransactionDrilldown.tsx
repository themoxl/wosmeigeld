import { useState } from "react";
import { Modal } from "../Modal";
import { ProvenancePanel } from "../ProvenancePanel";
import { TransactionTable } from "./TransactionTable";
import { useAnalyticsQuery } from "../../hooks/useAnalytics";
import type { AnalyticsQuery } from "../../lib/types";

// „Zahl anklicken, Buchungen sehen" - an einer Stelle geloest.
//
// Jede aggregierte Zahl im Produkt (Berichtszeile, Budgetzeile, Matrixzelle,
// Empfehlung) ist eine Behauptung ueber einen Buchungsbestand. Ohne Weg zu den
// einzelnen Buchungen bleibt sie unueberpruefbar. Die Komponente bekommt eine
// fertige Zeilenabfrage und uebernimmt Laden, Anzeige und den Sprung in die
// Einzelbuchung.
//
// Der Umweg ueber denselben Analytics-Endpunkt ist Absicht: die Detailliste
// entsteht aus derselben Abfrage wie die Summe darueber. Ein eigener
// Detailpfad koennte anders filtern - und dann widersprechen sich Summe und
// Liste, ohne dass es jemandem auffaellt.

interface TransactionDrilldownProps {
  title: string;
  query: AnalyticsQuery;
  /** Zusatzzeile unter dem Titel, z. B. der Zeitraum oder die Kennzahl. */
  subtitle?: string;
  onClose: () => void;
}

export function TransactionDrilldown({
  title,
  query,
  subtitle,
  onClose,
}: TransactionDrilldownProps) {
  const { data, isLoading, isError } = useAnalyticsQuery(query, true);
  const [detailTransactionId, setDetailTransactionId] = useState<string | null>(null);

  if (detailTransactionId) {
    return (
      <Modal title="Buchung" onClose={() => setDetailTransactionId(null)}>
        <ProvenancePanel transactionId={detailTransactionId} />
      </Modal>
    );
  }

  return (
    <Modal title={title} onClose={onClose} size="table">
      {subtitle && <p className="placeholder-note">{subtitle}</p>}
      {isLoading && <p role="status">Buchungen werden geladen…</p>}
      {isError && (
        <p role="alert" className="status-error">
          Die Buchungen konnten nicht geladen werden.
        </p>
      )}
      {data && (
        <TransactionTable
          result={data}
          onRowClick={(row) => {
            if (typeof row.transaction_id === "string") {
              setDetailTransactionId(row.transaction_id);
            }
          }}
        />
      )}
    </Modal>
  );
}
