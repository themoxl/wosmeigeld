import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  EMPTY_FREE_TRANSACTION_QUERY,
  FREE_TRANSACTIONS_PAGE_SIZE,
  useAssignTransactionsToContract,
  useContractAssignmentPreview,
  useContractFreeTransactions,
  type ContractFreeTransactionQuery,
} from "../../hooks/useContracts";
import { isAiTaskWorking, useAiTask, useEnqueueAiTask } from "../../hooks/useAiTaskQueue";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import { aiReasonCodeIn } from "../../lib/aiReasons";
import { AiReasonNotice } from "../AiReasonNotice";
import type {
  Contract,
  ContractAITransactionItem,
  ContractAITransactionCheckResponse,
  ContractAmountEntry,
  ContractAmountStagePlan,
  ContractAssignmentPreview,
  ContractEvidenceTransaction,
  ContractFreeTransactionFilter,
} from "../../lib/types";

/**
 * "Fehlende Buchungen nachtragen" (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, 19.7).
 *
 * Der Fall, an dem sich das misst: ein Vertrag hat nur die juengeren Buchungen
 * zugeordnet, waehrend mehrere aeltere Buchungen derselben Mandatsreferenz
 * voellig frei danebenliegen. Ohne diesen Weg gibt es keine Moeglichkeit, sie
 * ohne Datenbankzugriff an den Vertrag zu haengen.
 *
 * Der Ablauf ist absichtlich vierschrittig und nicht abkuerzbar:
 * suchen -> auswaehlen -> Vorschau -> zuordnen und neu rechnen. Die Vorschau
 * ist kein Hoeflichkeitsschritt: sie ist die einzige Stelle, an der vor dem
 * Schreiben sichtbar wird, welche Preistreppe dabei entsteht und aus welchem
 * anderen Vertrag eine Buchung gegebenenfalls abgezogen wird.
 *
 * Was der Ablauf dem Nutzer schuldet: er muss aussehen wie ein Weg und nicht
 * wie eine Sackgasse. Der Knopf an der Auswahl heisst deshalb nach dem Ziel
 * ("... zuordnen") und nicht nach dem Zwischenschritt, die Auslassungspunkte
 * kuendigen den Bestaetigungsschritt an, und die fertige Vorschau holt den
 * Blick zu sich - sonst steht der eigentliche Knopf unsichtbar unterhalb des
 * Fensterrands und die Auswahl wirkt folgenlos.
 *
 * Das Sprachmodell (19.11) haengt daneben und nicht darin: es markiert eine
 * Auswahl und begruendet sie, ordnet aber nichts zu. Bestaetigt wird ueber
 * genau denselben Vorschau-Knopf wie eine von Hand getroffene Auswahl.
 */

interface FilterDraft {
  dateFrom: string;
  dateTo: string;
  amountMinEuro: string;
  amountMaxEuro: string;
  search: string;
  useDefaults: boolean;
}

const EMPTY_DRAFT: FilterDraft = {
  dateFrom: "",
  dateTo: "",
  amountMinEuro: "",
  amountMaxEuro: "",
  search: "",
  useDefaults: true,
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/** Preisstufen sind wie `Contract.amount_minor` immer positiv gespeichert; die
 *  Richtung steht am Vertrag. Buchungen dagegen tragen ihr Originalvorzeichen
 *  und duerfen NICHT durch diese Funktion. */
function signedStageAmount(contract: Contract, amountMinor: number): number {
  return contract.direction === "expense" ? -amountMinor : amountMinor;
}

/** Der Suchtext einer Zeile - Empfaenger und Zweck sind je fuer sich
 *  gelegentlich leer, zusammen fast nie. */
function transactionLabel(item: ContractEvidenceTransaction): string {
  return item.payee_label ?? item.purpose_excerpt ?? "Ohne Empfänger";
}

/** Wonach der Server tatsaechlich gesucht hat - inklusive der Vorbelegung, die
 *  der Nutzer nie eingetippt hat. Ohne diese Zeile bliebe unklar, warum eine
 *  bekannte Buchung nicht in der Liste steht. */
function describeAppliedFilter(filter: ContractFreeTransactionFilter): string {
  const parts = [`${filter.date_from} bis ${filter.date_to}`];
  if (filter.amount_min_minor !== null || filter.amount_max_minor !== null) {
    const min =
      filter.amount_min_minor === null
        ? "beliebig"
        : formatMinorUnitsAsEuro(filter.amount_min_minor);
    const max =
      filter.amount_max_minor === null
        ? "beliebig"
        : formatMinorUnitsAsEuro(filter.amount_max_minor);
    parts.push(`Betrag ${min} bis ${max}`);
  }
  if (filter.search) parts.push(`Text „${filter.search}“`);
  parts.push(
    filter.from_contract_defaults
      ? "mit Vorbelegung aus dem Vertrag"
      : "ohne Vorbelegung aus dem Vertrag",
  );
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// Vorschau
// ---------------------------------------------------------------------------

/** Eine Zeitraumgrenze vorher/nachher. Unveraenderte Grenzen werden ruhig
 *  dargestellt, damit die Aenderung auffaellt und nicht im Gleichklang
 *  untergeht. */
function DateShift({
  label,
  before,
  after,
}: {
  label: string;
  before: string | null;
  after: string | null;
}) {
  const changed = before !== after;
  return (
    <li>
      {label}: {before ?? "—"}
      {changed && (
        <>
          {" → "}
          <strong>{after ?? "—"}</strong>
        </>
      )}
      {!changed && " (unverändert)"}
    </li>
  );
}

function StagePlanList({
  contract,
  before,
  after,
}: {
  contract: Contract;
  before: ContractAmountEntry[];
  after: ContractAmountStagePlan[];
}) {
  return (
    <div className="form-grid-2">
      <div>
        <h4 className="card-heading">Preisverlauf bisher</h4>
        {before.length === 0 ? (
          <p className="placeholder-note">Kein Verlauf erfasst.</p>
        ) : (
          <ol className="contract-timeline">
            {before.map((stage) => (
              <li key={stage.id} className="contract-timeline__step">
                <span className="contract-timeline__amount">
                  {formatMinorUnitsAsEuro(signedStageAmount(contract, stage.amount_minor))}
                </span>
                <span className="contract-timeline__range">
                  {stage.valid_from} – {stage.valid_to ?? "heute"}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
      <div>
        <h4 className="card-heading">Preisverlauf danach</h4>
        {after.length === 0 ? (
          <p className="placeholder-note">
            Aus den Buchungen lässt sich noch keine Preisstufe herleiten.
          </p>
        ) : (
          <ol className="contract-timeline">
            {after.map((stage) => (
              <li
                key={`${stage.valid_from}-${stage.source_transaction_id}`}
                className="contract-timeline__step"
              >
                <span className="contract-timeline__amount">
                  {formatMinorUnitsAsEuro(signedStageAmount(contract, stage.amount_minor))}
                </span>
                <span className="contract-timeline__range">
                  {stage.valid_from} – {stage.valid_to ?? "heute"}
                </span>
                <span className="txn-note">
                  {stage.transaction_count === 1
                    ? "1 Buchung"
                    : `${stage.transaction_count} Buchungen`}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function AssignmentPreviewBlock({
  contract,
  preview,
}: {
  contract: Contract;
  preview: ContractAssignmentPreview;
}) {
  return (
    <div className="operation-preview">
      <h3 className="card-heading">Vorschau: was sich ändert</h3>
      <ul>
        <li>
          Ziel: <strong>{preview.contract_name}</strong>
        </li>
        <li>
          {preview.transactions.length === 1
            ? "1 Buchung wird zugeordnet"
            : `${preview.transactions.length} Buchungen werden zugeordnet`}
        </li>
        {preview.sources.map((source) => (
          <li key={source.contract_id ?? "free"}>
            {source.transaction_count}{" "}
            {source.contract_id === null ? (
              "davon bisher frei"
            ) : (
              <>
                davon bisher bei <strong>{source.contract_name ?? "einem anderen Vertrag"}</strong>{" "}
                — dort werden sie abgezogen
              </>
            )}
          </li>
        ))}
        <DateShift
          label="Erste Zahlung"
          before={preview.first_seen_date_before}
          after={preview.first_seen_date_after}
        />
        <DateShift
          label="Letzte Zahlung"
          before={preview.last_seen_date_before}
          after={preview.last_seen_date_after}
        />
        {preview.stages_without_evidence_before > 0 && (
          <li>
            {preview.stages_without_evidence_before} bisherige Preisstufe(n) ohne jeden Beleg — die
            Neuberechnung ersetzt sie durch die aus den Buchungen hergeleiteten.
          </li>
        )}
      </ul>

      {preview.unknown_transaction_ids.length > 0 && (
        <p role="alert" className="status-error">
          {preview.unknown_transaction_ids.length} ausgewählte Buchung(en) gibt es nicht mehr. Sie
          werden übersprungen.
        </p>
      )}

      <StagePlanList
        contract={contract}
        before={preview.amount_stages_before}
        after={preview.amount_stages_after}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export function ContractAssignTransactionsPanel({ contract }: { contract: Contract }) {
  const [draft, setDraft] = useState<FilterDraft>(EMPTY_DRAFT);
  const [query, setQuery] = useState<ContractFreeTransactionQuery>(EMPTY_FREE_TRANSACTION_QUERY);
  const [selected, setSelected] = useState<string[]>([]);
  // Erst wenn der Nutzer den Zuordnen-Knopf an der Auswahl gedrueckt hat,
  // laeuft die Vorschau. Getrennt von `selected`, damit nicht jeder Haken eine
  // Serverabfrage ausloest - und damit "geprueft" und "ausgewaehlt"
  // auseinanderlaufen koennen, was der Knopf "Jetzt zuordnen" sichtbar macht.
  const [previewIds, setPreviewIds] = useState<string[]>([]);
  const [filterError, setFilterError] = useState<string | null>(null);
  const [assignError, setAssignError] = useState<string | null>(null);

  const freeTransactions = useContractFreeTransactions(contract.id, query);
  const preview = useContractAssignmentPreview(contract.id, previewIds);
  const assignMutation = useAssignTransactionsToContract();
  const [searchParams] = useSearchParams();
  const linkedTaskId = searchParams.get("ai_task");
  const [aiTaskId, setAiTaskId] = useState<string | null>(linkedTaskId);
  const aiTask = useAiTask(aiTaskId);
  const aiMutation = useEnqueueAiTask();

  const previewRef = useRef<HTMLDivElement | null>(null);
  const [handledAiTaskId, setHandledAiTaskId] = useState<string | null>(null);

  /**
   * Die fertige Vorschau holt den Blick zu sich. Bei einer langen Trefferliste
   * liegt sie sonst weit unterhalb des Fensterrands - mitsamt "Jetzt zuordnen",
   * dem einzigen Knopf, der ueberhaupt etwas schreibt. Genau daraus entsteht
   * der Eindruck, man koenne pruefen und aufheben, aber nichts nachtragen.
   *
   * `block: "nearest"` statt "start": ist die Vorschau schon im Bild, bleibt
   * die Seite ruhig stehen, statt bei jedem Neuladen zu ruckeln.
   */
  useEffect(() => {
    if (previewIds.length === 0 || preview.isFetching || !preview.data) return;
    previewRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [previewIds, preview.isFetching, preview.data]);

  const items = useMemo(() => freeTransactions.data?.items ?? [], [freeTransactions.data]);
  const aiResult =
    aiTask.data?.task_type === "contract_transaction_check" &&
    aiTask.data.input.contract_id === contract.id &&
    aiTask.data.result
      ? (aiTask.data.result as unknown as ContractAITransactionCheckResponse)
      : null;
  const aiWorking = isAiTaskWorking(aiTask.data);

  useEffect(() => {
    const task = aiTask.data;
    if (!aiResult || !task || handledAiTaskId === task.id) return;

    const proposed = aiResult.items
      .filter((item) => item.gehoert_zum_vertrag)
      .map((item) => item.transaction_id);
    const timeoutId = window.setTimeout(() => {
      setHandledAiTaskId(task.id);
      setSelected((previous) => Array.from(new Set([...previous, ...proposed])));
      setPreviewIds([]);
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [aiResult, aiTask.data, handledAiTaskId]);

  const aiById = useMemo(() => {
    const map = new Map<string, ContractAITransactionItem>();
    for (const item of aiResult?.items ?? []) map.set(item.transaction_id, item);
    return map;
  }, [aiResult]);

  /**
   * Angezeigte Zeilen: die aktuelle Seite der freien Buchungen, ergaenzt um
   * beurteilte Buchungen, die gerade nicht auf dieser Seite stehen.
   *
   * Ohne die Ergaenzung verschwaende ein Modellvorschlag, sobald er auf Seite 2
   * liegt - angehakt waere er trotzdem, nur unsichtbar. Eine unsichtbare
   * Auswahl ist genau die Art von stiller Zuordnung, die hier nicht passieren
   * darf.
   */
  const rows = useMemo(() => {
    const known = new Set(items.map((item) => item.transaction_id));
    const extra: ContractEvidenceTransaction[] = [];
    for (const judged of aiById.values()) {
      if (known.has(judged.transaction_id)) continue;
      extra.push({
        transaction_id: judged.transaction_id,
        booked_date: judged.booked_date,
        amount_minor: judged.amount_minor,
        payee_label: judged.payee_label,
        purpose_excerpt: null,
      });
    }
    return [...items, ...extra];
  }, [items, aiById]);

  const allRowsSelected = useMemo(
    () => rows.length > 0 && rows.every((item) => selected.includes(item.transaction_id)),
    [rows, selected],
  );

  const toggle = (transactionId: string) => {
    setSelected((previous) =>
      previous.includes(transactionId)
        ? previous.filter((id) => id !== transactionId)
        : [...previous, transactionId],
    );
    // Die geprüfte Menge ist damit nicht mehr die ausgewaehlte - die Vorschau
    // muss neu angefordert werden, statt eine veraltete zu bestaetigen.
    setPreviewIds([]);
    setAssignError(null);
  };

  /**
   * Alle angezeigten Zeilen anhaken. Bewusst ergaenzend und nicht ersetzend:
   * auf anderen Seiten und aus dem Modelllauf kann bereits etwas ausgewaehlt
   * sein, und diese Auswahl darf ein Klick hier nicht stillschweigend
   * wegwerfen. Ein Vertrag mit zwoelf nachzutragenden Lastschriften ist der
   * Normalfall, nicht die Ausnahme - zwoelf einzelne Haken sind reine Arbeit.
   */
  const selectAllRows = () => {
    setSelected((previous) =>
      Array.from(new Set([...previous, ...rows.map((item) => item.transaction_id)])),
    );
    setPreviewIds([]);
    setAssignError(null);
  };

  const applyFilter = (event: React.FormEvent) => {
    event.preventDefault();
    setFilterError(null);

    const min = draft.amountMinEuro.trim() ? parseAmountToMinorUnits(draft.amountMinEuro) : null;
    const max = draft.amountMaxEuro.trim() ? parseAmountToMinorUnits(draft.amountMaxEuro) : null;
    if (
      (draft.amountMinEuro.trim() && min === null) ||
      (draft.amountMaxEuro.trim() && max === null)
    ) {
      setFilterError("Die Betragsgrenzen sind keine gültigen Zahlen.");
      return;
    }
    if (draft.dateFrom && draft.dateTo && draft.dateFrom > draft.dateTo) {
      setFilterError("Das Startdatum liegt nach dem Enddatum.");
      return;
    }

    setQuery({
      dateFrom: draft.dateFrom || null,
      dateTo: draft.dateTo || null,
      // Der Server erwartet Betragsgrenzen ohne Vorzeichen - die Richtung
      // kommt vom Vertrag und ist bewusst nicht ueberschreibbar.
      amountMinMinor: min === null ? null : Math.abs(min),
      amountMaxMinor: max === null ? null : Math.abs(max),
      search: draft.search.trim() || null,
      useContractDefaults: draft.useDefaults,
      limit: FREE_TRANSACTIONS_PAGE_SIZE,
      offset: 0,
    });
    setPreviewIds([]);
  };

  const goToOffset = (offset: number) => {
    setQuery((previous) => ({ ...previous, offset: Math.max(0, offset) }));
  };

  const runAiCheck = () => {
    setAssignError(null);
    aiMutation.mutate(
      {
        task_type: "contract_transaction_check",
        input: { contract_id: contract.id },
      },
      {
        onSuccess: (task) => setAiTaskId(task.id),
      },
    );
  };

  const runAssignment = () => {
    setAssignError(null);
    assignMutation.mutate(
      { contractId: contract.id, transactionIds: previewIds },
      {
        onSuccess: () => {
          setSelected([]);
          setPreviewIds([]);
        },
        onError: (error) => setAssignError(errorText(error, "Das Zuordnen ist fehlgeschlagen.")),
      },
    );
  };

  const total = freeTransactions.data?.total ?? 0;
  const offset = freeTransactions.data?.offset ?? 0;
  const limit = freeTransactions.data?.limit ?? FREE_TRANSACTIONS_PAGE_SIZE;
  const appliedFilter = freeTransactions.data?.applied_filter ?? null;
  const result = assignMutation.data ?? null;

  return (
    <section className="operation-preview">
      <h3 className="card-heading">Fehlende Buchungen nachtragen</h3>
      <p className="text-sm">
        Buchungen, die noch zu keinem Vertrag gehören. Die Vorbelegung sucht im Betragsband und mit
        dem Anbieterwort dieses Vertrags — findet sie nichts, hilft es, den Haken zu entfernen und
        selbst zu suchen.
      </p>

      <form onSubmit={applyFilter} className="contract-form">
        <div className="form-grid-3">
          <div className="form-field">
            <label className="form-label" htmlFor="free-date-from">
              Zeitraum von
            </label>
            <input
              id="free-date-from"
              type="date"
              className="form-input form-input--mono"
              value={draft.dateFrom}
              onChange={(event) => setDraft((prev) => ({ ...prev, dateFrom: event.target.value }))}
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="free-date-to">
              Zeitraum bis
            </label>
            <input
              id="free-date-to"
              type="date"
              className="form-input form-input--mono"
              value={draft.dateTo}
              onChange={(event) => setDraft((prev) => ({ ...prev, dateTo: event.target.value }))}
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="free-search">
              Text in Empfänger oder Zweck
            </label>
            <input
              id="free-search"
              type="search"
              className="form-input"
              value={draft.search}
              onChange={(event) => setDraft((prev) => ({ ...prev, search: event.target.value }))}
              placeholder="z. B. Musterfunk"
            />
          </div>
        </div>
        <div className="form-grid-3">
          <div className="form-field">
            <label className="form-label" htmlFor="free-amount-min">
              Betrag ab (€)
            </label>
            <input
              id="free-amount-min"
              type="text"
              className="form-input form-input--mono"
              value={draft.amountMinEuro}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, amountMinEuro: event.target.value }))
              }
              placeholder="0,00"
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="free-amount-max">
              Betrag bis (€)
            </label>
            <input
              id="free-amount-max"
              type="text"
              className="form-input form-input--mono"
              value={draft.amountMaxEuro}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, amountMaxEuro: event.target.value }))
              }
              placeholder="100,00"
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="free-use-defaults">
              <input
                id="free-use-defaults"
                type="checkbox"
                checked={draft.useDefaults}
                onChange={(event) =>
                  setDraft((prev) => ({ ...prev, useDefaults: event.target.checked }))
                }
              />{" "}
              Vorbelegung aus dem Vertrag verwenden
            </label>
          </div>
        </div>
        {filterError && (
          <p role="alert" className="status-error">
            {filterError}
          </p>
        )}
        <div className="modal-actions">
          <button
            type="button"
            className="btn-secondary"
            disabled={aiMutation.isPending || aiWorking}
            onClick={runAiCheck}
          >
            {aiMutation.isPending
              ? "Wird eingereiht …"
              : aiWorking
                ? "KI-Aufgabe läuft …"
                : "Restliche Buchungen finden (KI)"}
          </button>
          <button type="submit" className="btn-secondary">
            Suchen
          </button>
        </div>
      </form>

      {/* Eine einzige Statusregion fuer den KI-Lauf: Wartehinweis, ruhige
          Meldung und Ergebniszeile loesen einander ab, statt sich als drei
          konkurrierende Ansagen zu ueberlagern - dasselbe Muster wie bei der
          Paar-Gegenpruefung auf der Seite. */}
      {(aiMutation.isPending ||
        aiMutation.isError ||
        aiTask.data?.status === "failed" ||
        aiWorking ||
        aiResult !== null) && (
        <div role="status">
          {(aiMutation.isPending || aiWorking) && (
            <p className="placeholder-note">
              {aiTask.data?.status_message ?? "Die Prüfung wartet in der KI-Warteschlange."} Es wird
              nichts zugeordnet; du kannst währenddessen anderswo weiterarbeiten.
            </p>
          )}
          {!aiWorking && aiMutation.isError && (
            <p className="placeholder-note">
              Die Prüfung ließ sich nicht durchführen:{" "}
              {errorText(aiMutation.error, "unbekannter Fehler")} An den Buchungen wurde nichts
              verändert.
            </p>
          )}
          {/* Ruhig und ohne rote Fehlerbox: ein nicht erreichbares Modell ist
              kein Absturz, sondern ein abgeschalteter Zusatz. Der Server
              antwortet dafuer bewusst mit 200 und legt den Klartext in
              `meldung` - bei einem Teilergebnis samt den Gruenden des
              KI-Kerns; der erste liefert die Aktion. */}
          {!aiWorking && aiResult?.meldung && (
            <AiReasonNotice
              className="placeholder-note"
              code={aiReasonCodeIn(aiResult.meldung)}
              message={aiResult.meldung}
            />
          )}
          {!aiWorking && aiTask.data?.status === "failed" && (
            <AiReasonNotice
              className="status-error"
              code={aiReasonCodeIn(aiTask.data.error_message)}
              message={aiTask.data.error_message ?? "Die KI-Prüfung ist fehlgeschlagen."}
            />
          )}
          {!aiWorking && aiResult && !aiResult.meldung && (
            <p className="placeholder-note">
              {aiResult.geprueft === 0
                ? "Das Sprachmodell hat keine Buchung gefunden, zu der es etwas sagen kann."
                : `${aiResult.geprueft} Buchung(en) beurteilt`}
              {aiResult.uebersprungen > 0 && ` · ${aiResult.uebersprungen} übersprungen`}
              {` · ${aiResult.kandidaten_gesamt} Kandidat(en) in der Vorauswahl`}
              {aiResult.modell_name && ` · Modell: ${aiResult.modell_name}`}
              {/* Niemals das interne Limit als Gesamtzahl nennen - gleiche
                  Regel wie bei den Duplikatvorschlaegen. */}
              {aiResult.truncated &&
                " · Es sind mehr Kandidaten vorhanden, als ein Lauf beurteilt. Nach dem Zuordnen erneut prüfen statt den Knopf mehrfach zu drücken."}
            </p>
          )}
          {!aiWorking && aiResult && aiResult.items.length > 0 && (
            <p className="placeholder-note">
              Vorschläge sind angehakt, aber noch nicht zugeordnet — der grüne Knopf unter der Liste
              führt zur Vorschau, geschrieben wird erst nach dem Bestätigen dort.
            </p>
          )}
        </div>
      )}

      {freeTransactions.isLoading && <p className="placeholder-note">Buchungen werden gesucht…</p>}
      {freeTransactions.isError && (
        <p role="alert" className="status-error">
          {errorText(freeTransactions.error, "Die freien Buchungen ließen sich nicht laden.")}
        </p>
      )}

      {appliedFilter && <p className="txn-note">Gesucht: {describeAppliedFilter(appliedFilter)}</p>}

      {!freeTransactions.isLoading && rows.length === 0 && (
        <p className="placeholder-note">
          Keine freie Buchung passt zu dieser Suche. Mit entfernter Vorbelegung und einem eigenen
          Zeitraum lässt sich breiter suchen.
        </p>
      )}

      {rows.length > 0 && (
        <>
          <div className="modal-actions">
            <span className="txn-note">
              {rows.length === 1 ? "1 Buchung zur Auswahl" : `${rows.length} Buchungen zur Auswahl`}
            </span>
            <button
              type="button"
              className="btn-secondary"
              disabled={allRowsSelected}
              onClick={selectAllRows}
            >
              {allRowsSelected ? "Alle ausgewählt" : `Alle ${rows.length} auswählen`}
            </button>
          </div>

          <ul className="contract-occurrences">
            {rows.map((item) => {
              const judged = aiById.get(item.transaction_id);
              return (
                <li key={item.transaction_id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(item.transaction_id)}
                    onChange={() => toggle(item.transaction_id)}
                    aria-label={`${transactionLabel(item)} vom ${item.booked_date} auswählen`}
                  />
                  <span className="contract-occurrences__date">{item.booked_date}</span>
                  <span className="txn-payee">{transactionLabel(item)}</span>
                  <span className="contract-occurrences__amount">
                    {formatMinorUnitsAsEuro(item.amount_minor)}
                  </span>
                  {item.purpose_excerpt && <span className="txn-note">{item.purpose_excerpt}</span>}
                  {judged && (
                    <>
                      {" "}
                      <span
                        className="status-badge status-badge--neutral"
                        title={
                          aiResult?.modell_name
                            ? `Beurteilt vom lokalen Sprachmodell ${aiResult.modell_name}.`
                            : "Beurteilt vom lokalen Sprachmodell."
                        }
                      >
                        Vorschlag des Sprachmodells
                      </span>{" "}
                      <span
                        className={`status-badge status-badge--${
                          judged.gehoert_zum_vertrag ? "highlight" : "neutral"
                        }`}
                      >
                        {judged.gehoert_zum_vertrag
                          ? "gehört vermutlich zu diesem Vertrag"
                          : "gehört vermutlich nicht dazu"}
                      </span>
                      <p className="card-body">{judged.begruendung}</p>
                    </>
                  )}
                </li>
              );
            })}
          </ul>

          {total > limit && (
            <div className="modal-actions">
              <span className="txn-note">
                {offset + 1}–{Math.min(offset + items.length, total)} von {total}
              </span>
              <button
                type="button"
                className="btn-secondary"
                disabled={offset === 0}
                onClick={() => goToOffset(offset - limit)}
              >
                Zurück
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={!freeTransactions.data?.truncated}
                onClick={() => goToOffset(offset + limit)}
              >
                Weiter
              </button>
            </div>
          )}
        </>
      )}

      {selected.length > 0 && (
        <div className="selection-bar">
          <span className="selection-bar-count">{selected.length} ausgewählt</span>
          <span className="selection-bar-message">
            Der nächste Schritt zeigt die Vorschau — welche Preistreppe entsteht und was einem
            anderen Vertrag entzogen wird. Geschrieben wird erst nach dem Bestätigen dort.
          </span>
          {/* Beschriftet nach dem Ziel, nicht nach dem Zwischenschritt: „Auswahl
              prüfen“ klang nach Selbstzweck und liess den Weg zum Zuordnen
              verschwinden. Die Auslassungspunkte kuendigen an, dass noch ein
              Schritt folgt - die Vorschau bleibt damit unuebersprungen. */}
          <button type="button" className="btn-primary" onClick={() => setPreviewIds(selected)}>
            {selected.length === 1
              ? "1 Buchung zuordnen …"
              : `${selected.length} Buchungen zuordnen …`}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setSelected([]);
              setPreviewIds([]);
            }}
          >
            Auswahl aufheben
          </button>
        </div>
      )}

      {preview.isFetching && <p className="placeholder-note">Auswirkung wird berechnet…</p>}
      {preview.isError && (
        <p role="alert" className="status-error">
          {errorText(preview.error, "Die Vorschau ist fehlgeschlagen.")}
        </p>
      )}

      {preview.data && !preview.isFetching && (
        <div ref={previewRef}>
          <AssignmentPreviewBlock contract={contract} preview={preview.data} />
          {assignError && (
            <p role="alert" className="status-error">
              {assignError}
            </p>
          )}
          <div className="modal-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setPreviewIds([])}
              disabled={assignMutation.isPending}
            >
              Doch nicht
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={assignMutation.isPending || preview.data.transactions.length === 0}
              onClick={runAssignment}
            >
              {assignMutation.isPending ? "Wird zugeordnet…" : "Jetzt zuordnen und neu rechnen"}
            </button>
          </div>
        </div>
      )}

      {result && (
        <div className="operation-preview" role="status">
          <h3 className="card-heading">Zugeordnet und neu gerechnet</h3>
          {result.changed ? (
            <ul>
              <li>{result.stages_written} Preisstufe(n) neu geschrieben</li>
              <li>{result.stages_removed} nicht belegte Stufe(n) entfernt</li>
              <li>{result.manual_stages_kept} von Hand gesetzte Stufe(n) unangetastet</li>
              <li>
                Zeitraum: {result.first_seen_date ?? "—"} bis {result.last_seen_date ?? "—"}
              </li>
            </ul>
          ) : (
            <p className="card-body">
              Der Betragsverlauf war bereits stimmig — es gab nichts neu zu rechnen.
            </p>
          )}
          {/* Bewusste Nicht-Entscheidung des Servers: der Sollbetrag des
              Vertrags wird von der Neuberechnung NICHT angefasst. Ein
              automatisches Nachziehen verschoebe die Fixkostensumme unbemerkt,
              deshalb entscheidet das hier der Nutzer. */}
          {result.resulting_amount_minor !== null &&
            result.resulting_amount_minor !== result.contract_amount_minor && (
              <p className="card-body">
                Die jüngste hergeleitete Preisstufe lautet{" "}
                <strong>
                  {formatMinorUnitsAsEuro(
                    signedStageAmount(contract, result.resulting_amount_minor),
                  )}
                </strong>
                , der Sollbetrag des Vertrags steht weiterhin auf{" "}
                <strong>
                  {formatMinorUnitsAsEuro(
                    signedStageAmount(contract, result.contract_amount_minor),
                  )}
                </strong>
                . Der Sollbetrag wurde absichtlich nicht mitgeändert — er geht in die Fixkostensumme
                ein. Über „Betrag korrigieren“ in der Kopfzeile lässt er sich angleichen.
              </p>
            )}
        </div>
      )}
    </section>
  );
}
