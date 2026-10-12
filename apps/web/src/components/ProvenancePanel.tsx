import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useFormatDate } from "../hooks/useProfile";
import { useHousehold } from "../household/HouseholdProvider";
import { getTransactionProvenance } from "../lib/api";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type { SplitProvenance, TransactionProvenance } from "../lib/types";

// docs/ANALYSE-EXPLORER-PLAN.md, Baustein A ("Warum diese Kategorie?").
//
// Der Server liefert bewusst Felder statt eines fertigen Satzes; hier entsteht
// der Klartext. Rohe reason_code-Werte oder Modellprozente werden nicht als
// Erklaerung ausgegeben - das ist eine ausdrueckliche Vorgabe aus
// docs/KI-FINANZASSISTENT-PLAN.md ("Zielbild der Oberflaeche").

const FIELD_LABELS: Record<string, string> = {
  payee_raw: "der Zahlungsempfänger",
  purpose: "der Verwendungszweck",
  amount_min: "der Betrag mindestens",
  amount_max: "der Betrag höchstens",
  account: "das Konto",
};

const SOURCE_LABELS: Record<SplitProvenance["category_source"], string> = {
  unset: "Ohne Kategorie",
  unknown: "Herkunft nicht erfasst",
  manual: "Von Hand zugeordnet",
  import: "Beim Import gewählt",
  rule: "Durch Regel zugeordnet",
  ai: "Von der KI vorgeschlagen",
  history: "Aus der Haushaltshistorie",
};

const SOURCE_BADGE: Record<SplitProvenance["category_source"], string> = {
  unset: "status-badge--neutral",
  unknown: "status-badge--neutral",
  manual: "status-badge--positive",
  import: "status-badge--positive",
  rule: "status-badge--highlight",
  ai: "status-badge--highlight",
  history: "status-badge--highlight",
};

function ruleName(split: SplitProvenance): string | null {
  // Der zum Trefferzeitpunkt eingefrorene Name geht vor: die Regel kann
  // inzwischen umbenannt oder geloescht worden sein, die Begruendung soll
  // trotzdem den Namen zeigen, unter dem sie gegriffen hat.
  return split.matched_on?.rule_name ?? split.rule_name ?? null;
}

function describeCriteria(split: SplitProvenance): string | null {
  const criteria = split.matched_on?.criteria ?? [];
  if (criteria.length === 0) return null;

  const parts = criteria.map((criterion) => {
    const label = FIELD_LABELS[criterion.field] ?? criterion.field;
    if (criterion.field === "amount_min" || criterion.field === "amount_max") {
      return `${label} ${formatMinorUnitsAsEuro(Number(criterion.pattern))} beträgt`;
    }
    if (criterion.field === "account") return `${label} übereinstimmt`;
    return `${label} „${criterion.pattern}“ enthält`;
  });

  return `weil ${parts.join(" und ")}`;
}

function SplitExplanation({ split }: { split: SplitProvenance }) {
  const { formatDate } = useFormatDate();
  const source = split.category_source;
  const when = split.categorized_at ? formatDate(split.categorized_at) : null;

  const categoryLabel = split.category_name ? (
    <>
      <strong>{split.category_name}</strong>
      {split.category_group_name && (
        <span className="provenance-group"> ({split.category_group_name})</span>
      )}
      {split.category_is_archived && (
        <span className="status-badge status-badge--neutral provenance-archived">archiviert</span>
      )}
    </>
  ) : (
    <em>Ohne Kategorie</em>
  );

  return (
    <li className="provenance-split">
      <div className="provenance-split-head">
        <span className="provenance-category">{categoryLabel}</span>
        <span className="provenance-amount">{formatMinorUnitsAsEuro(split.amount_minor)}</span>
      </div>

      <span className={`status-badge ${SOURCE_BADGE[source]}`}>{SOURCE_LABELS[source]}</span>

      {source === "rule" && (
        <p className="provenance-reason">
          Gesetzt durch die Regel <strong>{ruleName(split) ?? "ohne Namen"}</strong>
          {split.rule_priority !== null && ` (Priorität ${split.rule_priority})`}
          {describeCriteria(split) ? `, ${describeCriteria(split)}` : ""}.
          {split.rule_is_active === false && " Diese Regel ist inzwischen deaktiviert."}
          {split.rule_id === null && " Diese Regel wurde inzwischen gelöscht."}
        </p>
      )}

      {source === "ai" && (
        <p className="provenance-reason">
          Vorschlag der lokalen KI
          {split.model_name && ` (Modell ${split.model_name})`}
          {split.confidence !== null && `, Sicherheit ${Math.round(split.confidence * 100)} %`}, von
          einer Person bestätigt.
        </p>
      )}

      {source === "history" && (
        <p className="provenance-reason">
          Aus dem eigenen Bestand abgeleitet: derselbe Empfänger war bereits eindeutig
          kategorisiert, deshalb hat der Klassifikationslauf gar kein Modell gefragt
          {split.confidence !== null && `, Sicherheit ${Math.round(split.confidence * 100)} %`}. Von
          einer Person bestätigt.
        </p>
      )}

      {(source === "manual" || source === "import") && (
        <p className="provenance-reason">
          {source === "import"
            ? "Beim Übernehmen des Imports ausdrücklich gewählt."
            : "Von Hand in der App zugeordnet."}
        </p>
      )}

      {source === "unknown" && (
        <p className="provenance-reason">
          Diese Zuordnung stammt aus der Zeit vor der Herkunftsaufzeichnung. Ab jetzt wird bei jeder
          Änderung festgehalten, wodurch sie entstanden ist.
        </p>
      )}

      {source === "unset" && (
        <p className="provenance-reason">
          Weder eine Regel noch die KI noch eine Person hat bisher eine Kategorie gesetzt.
        </p>
      )}

      {when && <p className="provenance-when">Zugeordnet am {when}</p>}
    </li>
  );
}

function RawDataRow({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="provenance-raw-row">
      <span className="provenance-raw-label">{label}</span>
      <span className="provenance-raw-value">{value}</span>
    </div>
  );
}

export function ProvenancePanel({ transactionId }: { transactionId: string }) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const { formatDate } = useFormatDate();

  const { data, isLoading, error } = useQuery<TransactionProvenance>({
    queryKey: ["transaction-provenance", selectedHouseholdId, transactionId],
    queryFn: () => getTransactionProvenance(accessToken!, selectedHouseholdId!, transactionId),
    enabled: Boolean(accessToken && selectedHouseholdId && transactionId),
  });

  if (isLoading) {
    return <p className="placeholder-note">Lade Herkunft...</p>;
  }
  if (error || !data) {
    return <p className="placeholder-note">Herkunft konnte nicht geladen werden.</p>;
  }

  const origin = data.import_origin;
  const hasRawData = Boolean(data.payee_raw || data.purpose_raw || data.bank_transaction_code);

  return (
    <section className="provenance">
      <h3 className="provenance-heading">Warum ist diese Buchung hier?</h3>

      <ul className="provenance-splits">
        {data.splits.map((split) => (
          <SplitExplanation key={split.split_id} split={split} />
        ))}
      </ul>

      <details className="provenance-details">
        <summary>Bankdaten und Herkunft der Buchung</summary>
        <div className="provenance-raw">
          {hasRawData ? (
            <>
              <RawDataRow label="Empfänger laut Bank" value={data.payee_raw} />
              <RawDataRow label="Verwendungszweck" value={data.purpose_raw} />
              <RawDataRow label="Buchungsart" value={data.bank_transaction_code} />
            </>
          ) : (
            <p className="placeholder-note">
              Für diese Buchung liegt kein Bankrohtext vor
              {data.source === "manual" ? " - sie wurde von Hand erfasst." : "."}
            </p>
          )}

          {origin && (
            <>
              <RawDataRow label="Importdatei" value={origin.filename} />
              <RawDataRow label="Format" value={origin.detected_format} />
              <RawDataRow
                label="Zeile in der Datei"
                value={origin.row_number !== null ? String(origin.row_number) : null}
              />
              <RawDataRow
                label="Importiert am"
                value={origin.imported_at ? formatDate(origin.imported_at) : null}
              />
            </>
          )}

          <RawDataRow label="Bankreferenz" value={data.external_reference} />
        </div>
      </details>
    </section>
  );
}
