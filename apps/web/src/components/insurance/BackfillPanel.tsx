import { formatMinorUnitsAsEuro } from "../../lib/money";
import {
  isPolicyReady,
  FREQUENCY_LABEL,
  type BackfillPreview,
  type Frequency,
  type InsurancePolicy,
  type InsuranceSummary,
} from "../../lib/insuranceApi";

function frequencyLabel(value: string): string {
  return FREQUENCY_LABEL[value as Frequency] ?? value;
}

/** Eine Zeile der Fortschrittsliste. `open` faerbt sie als offenen Punkt -
 *  erledigte Schritte bleiben sichtbar, damit der Fortschritt nachvollziehbar
 *  ist und nicht nur der Rest. */
function Step({ label, open }: { label: string; open: boolean }) {
  return (
    <span className={`insurance-step${open ? " insurance-step--open" : ""}`}>
      <span className="insurance-step-dot" aria-hidden="true" />
      {label}
    </span>
  );
}

export interface BackfillPanelProps {
  preview: BackfillPreview | null;
  policies: InsurancePolicy[];
  summary: InsuranceSummary | null;
  canEdit: boolean;
  busy: boolean;
  error: string | null;
  onRunBackfill: () => void;
}

export function BackfillPanel({
  preview,
  policies,
  summary,
  canEdit,
  busy,
  error,
  onRunBackfill,
}: BackfillPanelProps) {
  // Ist nichts mehr zu uebernehmen, verschwindet der Abschnitt. Er ist der
  // Einstieg ins Modul, nicht sein Dauerinventar (Plan 2.2).
  if (!preview || preview.items.length === 0) {
    return null;
  }

  const ready = policies.filter(isPolicyReady).length;
  const total = policies.length + preview.items.length;
  const readyShare = total > 0 ? Math.round((ready / total) * 100) : 0;

  return (
    <section className="insurance-block" aria-labelledby="insurance-backfill-title">
      <div className="insurance-block-head">
        <h2 className="insurance-block-title" id="insurance-backfill-title">
          Bestand übernehmen
        </h2>
        <span className="insurance-block-hint">
          Aus den vorhandenen Verträgen erkannt – erst die Vorschau, dann das Anlegen
        </span>
      </div>

      <div className="card insurance-intake">
        <div className="insurance-intake-top">
          <p className="insurance-intake-count">
            <strong>{ready}</strong> von {total} Policen einsatzbereit
          </p>
          {canEdit && (
            <button type="button" className="btn-primary" disabled={busy} onClick={onRunBackfill}>
              {busy
                ? "Wird übernommen …"
                : `${preview.items.length} ${
                    preview.items.length === 1 ? "Vertrag" : "Verträge"
                  } übernehmen`}
            </button>
          )}
        </div>

        <div
          className="insurance-intake-bar"
          role="img"
          aria-label={`${ready} von ${total} Policen einsatzbereit`}
        >
          <span className="insurance-intake-bar-done" style={{ width: `${readyShare}%` }} />
        </div>

        <div className="insurance-intake-steps">
          <Step
            label={`${preview.items.length} ${
              preview.items.length === 1 ? "Vertrag" : "Verträge"
            } noch ohne Police`}
            open
          />
          {preview.already_linked_count > 0 && (
            <Step
              label={`${preview.already_linked_count} ${
                preview.already_linked_count === 1 ? "Vertrag hat" : "Verträge haben"
              } bereits eine Police`}
              open={false}
            />
          )}
          {summary && (
            <>
              <Step
                label={`${summary.unknown_line_count} ohne Sparte`}
                open={summary.unknown_line_count > 0}
              />
              <Step
                label={`${summary.missing_main_due_count} ohne Hauptfälligkeit`}
                open={summary.missing_main_due_count > 0}
              />
              <Step
                label={`${summary.unconfirmed_main_due_count} Hauptfälligkeit nur angenommen`}
                open={summary.unconfirmed_main_due_count > 0}
              />
            </>
          )}
        </div>

        <div className="insurance-table-scroll" role="region" aria-label="Vorschau" tabIndex={0}>
          <table className="txn-table insurance-table">
            <thead>
              <tr>
                <th className="txn-th">Vertrag</th>
                <th className="txn-th">Beitrag</th>
                <th className="txn-th">Vorgeschlagene Sparte</th>
                <th className="txn-th">Woher der Vorschlag kommt</th>
              </tr>
            </thead>
            <tbody>
              {preview.items.map((item) => (
                <tr key={item.contract_id} className="txn-tr">
                  <td className="txn-td">
                    <div className="insurance-insurer">{item.contract_name}</div>
                    {item.provider_label && (
                      <div className="insurance-policy-label">{item.provider_label}</div>
                    )}
                  </td>
                  <td className="txn-td">
                    <div className="insurance-amount">
                      <span className="insurance-amount-value">
                        {formatMinorUnitsAsEuro(item.amount_minor)}
                      </span>
                      <span className="insurance-amount-origin">
                        {frequencyLabel(item.frequency)} · bleibt am Vertrag
                      </span>
                    </div>
                  </td>
                  <td className="txn-td">
                    {item.suggested_line_key === "unknown" ? (
                      <span className="insurance-task-hint">
                        Sparte noch offen – wird nach der Übernahme zugeordnet
                      </span>
                    ) : (
                      <span className="insurance-line-name">{item.suggested_line_label}</span>
                    )}
                  </td>
                  <td className="txn-td insurance-reason">{item.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="card-body">
          Die Übernahme legt je Vertrag eine Police an und verknüpft sie mit ihm. Der Beitrag bleibt
          am Vertrag – Budget, Fixkosten und Vorschau ändern sich dadurch nicht. Ein übernommenes
          Datum gilt als angenommen, bis es bestätigt ist.
        </p>

        {error && (
          <p className="insurance-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </section>
  );
}
