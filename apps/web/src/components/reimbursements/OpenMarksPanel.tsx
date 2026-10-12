import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { OpenReimbursementMark } from "../../lib/reimbursementsApi";

export interface OpenMarksPanelProps {
  marks: OpenReimbursementMark[];
  /** Versicherte Personen. Erst hier faellt die Wahl - die Marke selbst traegt
   *  bewusst keine Person (siehe Kopfkommentar der Tabelle im Schema). */
  people: { id: string; name: string }[];
  onRemove: (transactionId: string) => void;
  onConvert: (transactionId: string, insuredPersonId: string) => void;
  busy?: boolean;
}

const SOURCE_LABEL: Record<OpenReimbursementMark["source"], string> = {
  manual: "von Hand",
  rule: "durch Regel",
  category: "durch Kategorie",
};

function expectationLabel(mark: OpenReimbursementMark): string {
  if (mark.expected_minor === null) return "Höhe offen";
  if (mark.expectation_mode === "quota" && mark.expectation_rate) {
    const percent = Math.round(Number(mark.expectation_rate) * 100);
    return `${formatMinorUnitsAsEuro(mark.expected_minor)} (${percent} %)`;
  }
  return formatMinorUnitsAsEuro(mark.expected_minor);
}

/**
 * Die Bruecke zwischen Buchungsstrom und Erstattungsvorgang.
 *
 * Bis hierher entsteht ein Vorgang nur aus einem hochgeladenen Beleg. Wer die
 * Arztrechnung nie hochlaedt, hat keinen Vorgang - und damit sagt nichts, dass
 * da noch Geld kommen muesste. Diese Liste ist genau der Zwischenzustand:
 * bezahlt, Erstattung erwartet, aber noch nichts eingereicht.
 */
export function OpenMarksPanel({
  marks,
  people,
  onRemove,
  onConvert,
  busy = false,
}: OpenMarksPanelProps) {
  const total = marks.reduce((sum, mark) => sum + (mark.expected_minor ?? 0), 0);
  const unknownCount = marks.filter((mark) => mark.expected_minor === null).length;

  return (
    <section className="card" aria-labelledby="open-marks-heading">
      <div className="page-header">
        <div>
          <h2 className="card-heading" id="open-marks-heading">
            Markiert, noch kein Vorgang
          </h2>
          <p className="card-body">
            Buchungen, bei denen du eine Erstattung erwartest, für die aber noch kein Vorgang
            angelegt ist.
          </p>
        </div>
        {marks.length > 0 && (
          <div className="explorer-total">
            <span className="explorer-total__value">{formatMinorUnitsAsEuro(total)}</span>
            <span className="explorer-total__label">
              erwartet
              {/* Ohne diesen Zusatz laese sich die Summe als vollstaendig -
                  sie ist es nicht, solange Marken ohne Hoehe darunter sind. */}
              {unknownCount > 0 && ` · ${unknownCount} ohne Höhe`}
            </span>
          </div>
        )}
      </div>

      {marks.length === 0 ? (
        <p className="food-fill-note">
          Nichts offen. Markierungen entstehen in „Alle Buchungen“ – dort das ⊕ an einer Zahlung.
        </p>
      ) : (
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Datum</th>
                <th className="txn-th">Zahlungsempfänger</th>
                <th className="txn-th">Bezahlt</th>
                <th className="txn-th">Erwartet</th>
                <th className="txn-th">Herkunft</th>
                <th className="txn-th">Aktion</th>
              </tr>
            </thead>
            <tbody>
              {marks.map((mark) => (
                <tr key={mark.id} className="txn-tr">
                  <td className="txn-td">{mark.booked_date}</td>
                  <td className="txn-td">{mark.payee_label || mark.note || "—"}</td>
                  <td className="txn-td">{formatMinorUnitsAsEuro(Math.abs(mark.amount_minor))}</td>
                  <td className="txn-td">{expectationLabel(mark)}</td>
                  <td className="txn-td">{SOURCE_LABEL[mark.source]}</td>
                  <td className="txn-td">
                    <div className="food-proposal__actions">
                      {people.length > 0 && (
                        <select
                          className="form-input"
                          aria-label={`Vorgang anlegen für ${mark.payee_label ?? "Buchung"}`}
                          defaultValue=""
                          disabled={busy}
                          onChange={(event) => {
                            if (!event.target.value) return;
                            onConvert(mark.transaction_id, event.target.value);
                            event.target.value = "";
                          }}
                        >
                          <option value="">Vorgang anlegen für …</option>
                          {people.map((person) => (
                            <option key={person.id} value={person.id}>
                              {person.name}
                            </option>
                          ))}
                        </select>
                      )}
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={busy}
                        onClick={() => onRemove(mark.transaction_id)}
                      >
                        Markierung entfernen
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
