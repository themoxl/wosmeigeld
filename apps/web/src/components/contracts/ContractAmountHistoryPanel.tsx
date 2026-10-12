import { useState } from "react";
import { ContractAssignTransactionsPanel } from "./ContractAssignTransactionsPanel";
import {
  useContractAmountEvidence,
  useRecalculateContractAmountHistory,
} from "../../hooks/useContracts";
import { useFormatDate } from "../../hooks/useProfile";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type {
  Contract,
  ContractAmountStageEvidence,
  ContractEvidenceTransaction,
} from "../../lib/types";

/**
 * Register "Betragsverlauf" des Vertragsdialogs (Plan 19.7).
 *
 * Bis hierher zeigte der Verlauf nur Betrag und Zeitraum je Stufe. Beim
 * Festnetzvertrag aus 19.1 heisst das: zwei Zeilen, beide beginnend am Tag der
 * Datenuebernahme, eine davon mit einem Zeitraum der Laenge null - und nichts
 * daran verraet, dass keine einzige Buchung diese Preise stuetzt. Genau das ist
 * der Unterschied, den dieses Register macht: jede Stufe laesst sich
 * aufklappen, und eine Stufe ohne Beleg sagt das ausdruecklich, statt eine
 * leere Liste zu zeigen.
 */

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/** Preisstufen sind wie `Contract.amount_minor` immer positiv gespeichert.
 *  Buchungen tragen dagegen ihr Originalvorzeichen und werden NICHT umgedreht. */
function signedStageAmount(contract: Contract, amountMinor: number): number {
  return contract.direction === "expense" ? -amountMinor : amountMinor;
}

function EvidenceList({ transactions }: { transactions: ContractEvidenceTransaction[] }) {
  const { formatDate } = useFormatDate();
  return (
    <ul className="contract-occurrences">
      {transactions.map((item) => (
        <li key={item.transaction_id}>
          <span className="contract-occurrences__date">{formatDate(item.booked_date)}</span>
          <span className="txn-payee">{item.payee_label ?? "Ohne Empfänger"}</span>
          <span className="contract-occurrences__amount">
            {formatMinorUnitsAsEuro(item.amount_minor)}
          </span>
          {item.is_stage_source && (
            <span
              className="status-badge status-badge--positive"
              title="Diese Buchung hat die Preisstufe eröffnet."
            >
              Auslöser
            </span>
          )}
          {item.due_date && (
            <span className="txn-note">Fälligkeit {formatDate(item.due_date)}</span>
          )}
          {item.purpose_excerpt && <span className="txn-note">{item.purpose_excerpt}</span>}
        </li>
      ))}
    </ul>
  );
}

function StageRow({
  contract,
  entry,
  expanded,
  onToggle,
}: {
  contract: Contract;
  entry: ContractAmountStageEvidence;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { stage } = entry;
  const { formatDate } = useFormatDate();
  const panelId = `stage-evidence-${stage.id}`;
  return (
    <li className="contract-timeline__step">
      <button
        type="button"
        className="period-toggle"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={onToggle}
      >
        <span className="contract-timeline__amount">
          {formatMinorUnitsAsEuro(signedStageAmount(contract, stage.amount_minor))}
        </span>{" "}
        <span className="contract-timeline__range">
          {formatDate(stage.valid_from)} – {stage.valid_to ? formatDate(stage.valid_to) : "heute"}
        </span>
      </button>
      {stage.delta_minor !== null && stage.delta_minor !== 0 && (
        <span
          className={`contract-timeline__delta ${
            stage.delta_minor > 0 ? "transaction-amount--negative" : "transaction-amount--positive"
          }`}
        >
          {stage.delta_minor > 0 ? "+" : ""}
          {formatMinorUnitsAsEuro(stage.delta_minor)}
        </span>
      )}{" "}
      {entry.has_evidence ? (
        <span className="status-badge status-badge--positive">
          {entry.transaction_count === 1 ? "1 Buchung" : `${entry.transaction_count} Buchungen`}
        </span>
      ) : (
        // Der wichtigste Zustand dieser Ansicht und deshalb ein Badge und kein
        // Kleingedrucktes: die Stufe behauptet einen Preis, den nichts stuetzt.
        <span
          className="status-badge status-badge--warning"
          title="Für den Zeitraum dieser Preisstufe ist keine zugeordnete Buchung vorhanden."
        >
          Kein Beleg
        </span>
      )}
      {/* Zeitraum der Laenge null: entsteht bei der Datenuebernahme, wenn zwei
          Stufen am selben Tag beginnen. Fachlich ist das keine Preisstufe,
          sondern ein Artefakt - es zu benennen erspart die Suche nach dem
          "verschwundenen" Preis. */}
      {stage.valid_to !== null && stage.valid_to < stage.valid_from && (
        <span className="txn-note">Der Zeitraum endet vor seinem Beginn.</span>
      )}
      {expanded && (
        <div id={panelId}>
          {entry.has_evidence ? (
            <EvidenceList transactions={entry.transactions} />
          ) : (
            <p className="placeholder-note">
              Für diese Preisstufe ist keine Buchung hinterlegt.
              {entry.has_source_transaction
                ? " Die eingetragene Auslöserbuchung liegt außerhalb ihres Zeitraums."
                : " Sie stammt aus der Datenübernahme und nennt keine Quellbuchung."}{" "}
              Unten lassen sich passende Buchungen nachtragen; danach ergibt sich der Verlauf aus
              den Buchungen selbst.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function ContractAmountHistoryPanel({
  contract,
  canEdit,
  initialAssignOpen = false,
}: {
  contract: Contract;
  canEdit: boolean;
  initialAssignOpen?: boolean;
}) {
  const evidence = useContractAmountEvidence(contract.id);
  const recalculate = useRecalculateContractAmountHistory();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [assignOpen, setAssignOpen] = useState(initialAssignOpen);
  const [recalcError, setRecalcError] = useState<string | null>(null);

  const data = evidence.data;
  const stages = data?.stages ?? [];

  return (
    <section>
      <h3 className="card-heading">Betragsverlauf</h3>

      {evidence.isLoading && <p className="placeholder-note">Verlauf wird geladen…</p>}
      {evidence.isError && (
        <p role="alert" className="status-error">
          {errorText(evidence.error, "Der Betragsverlauf ließ sich nicht laden.")}
        </p>
      )}

      {data && stages.length === 0 && (
        <p className="placeholder-note">
          Noch kein Verlauf erfasst. „Jetzt abgleichen“ auf der Übersicht vergleicht die Buchungen
          mit dem Vertrag — danach steht hier, ab wann welcher Preis galt.
        </p>
      )}

      {data && stages.length > 0 && (
        <>
          <p className="txn-note">
            {data.assigned_transaction_count === 1
              ? "1 zugeordnete Buchung"
              : `${data.assigned_transaction_count} zugeordnete Buchungen`}
            {data.stages_without_evidence > 0 &&
              ` · ${data.stages_without_evidence} Preisstufe(n) ohne Beleg`}
          </p>
          <ol className="contract-timeline">
            {/* Schluessel ist die id, nicht die Position: bei gleichem
                valid_from ist die Reihenfolge der Stufen beliebig (Zustand aus
                der Datenübernahme). */}
            {stages.map((entry) => (
              <StageRow
                key={entry.stage.id}
                contract={contract}
                entry={entry}
                expanded={expanded === entry.stage.id}
                onToggle={() =>
                  setExpanded((current) => (current === entry.stage.id ? null : entry.stage.id))
                }
              />
            ))}
          </ol>
        </>
      )}

      {data && data.transactions_outside_stages.length > 0 && (
        <div className="operation-preview">
          <h4 className="card-heading">Zugeordnete Buchungen außerhalb aller Preisstufen</h4>
          <p className="text-sm">
            Diese Buchungen hängen am Vertrag, fallen aber in keinen Zeitraum des Verlaufs — ein
            sicheres Zeichen dafür, dass die Preisstufen nicht aus den Buchungen stammen.
          </p>
          <EvidenceList transactions={data.transactions_outside_stages} />
        </div>
      )}

      {canEdit && data && (
        <>
          {recalcError && (
            <p role="alert" className="status-error">
              {recalcError}
            </p>
          )}
          {/* Zwei getrennte Wege, weil es zwei verschiedene Lagen sind: die
              Buchungen fehlen (nachtragen) oder sie hängen längst am Vertrag,
              nur die Stufen stammen aus der Übernahme (neu rechnen). */}
          <div className="modal-actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={recalculate.isPending || data.assigned_transaction_count === 0}
              title={
                data.assigned_transaction_count === 0
                  ? "Ohne zugeordnete Buchung gibt es nichts, woraus sich ein Verlauf herleiten ließe."
                  : undefined
              }
              onClick={() => {
                setRecalcError(null);
                recalculate.mutate(contract.id, {
                  onError: (error) =>
                    setRecalcError(errorText(error, "Die Neuberechnung ist fehlgeschlagen.")),
                });
              }}
            >
              {recalculate.isPending ? "Wird neu gerechnet…" : "Verlauf aus Buchungen neu rechnen"}
            </button>
            <button
              type="button"
              className="btn-primary"
              aria-expanded={assignOpen}
              onClick={() => setAssignOpen((open) => !open)}
            >
              {assignOpen ? "Nachtragen schließen" : "Fehlende Buchungen nachtragen"}
            </button>
          </div>

          {recalculate.isSuccess && (
            <p role="status" className="placeholder-note">
              {recalculate.data.changed
                ? `Neu gerechnet: ${recalculate.data.stages_written} Stufe(n) geschrieben, ${recalculate.data.stages_removed} entfernt, ${recalculate.data.manual_stages_kept} von Hand gesetzte behalten.`
                : "Der Verlauf entspricht bereits den zugeordneten Buchungen."}
              {recalculate.data.resulting_amount_minor !== null &&
                recalculate.data.resulting_amount_minor !==
                  recalculate.data.contract_amount_minor &&
                ` Die jüngste hergeleitete Stufe lautet ${formatMinorUnitsAsEuro(
                  signedStageAmount(contract, recalculate.data.resulting_amount_minor),
                )}, der Sollbetrag steht weiter auf ${formatMinorUnitsAsEuro(
                  signedStageAmount(contract, recalculate.data.contract_amount_minor),
                )} — absichtlich, weil er in die Fixkostensumme eingeht.`}
            </p>
          )}
        </>
      )}

      {canEdit && assignOpen && <ContractAssignTransactionsPanel contract={contract} />}
    </section>
  );
}
