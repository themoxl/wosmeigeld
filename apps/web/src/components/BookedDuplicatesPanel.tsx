import { useDeferredValue, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  assessBookedDuplicates,
  resolveBookedDuplicatesBulk,
  scanBookedDuplicates,
} from "../lib/api";
import { aiReasonFromError, isUnassessedVerdict } from "../lib/aiReasons";
import { AiReasonNotice } from "./AiReasonNotice";
import type {
  BookedDuplicateAssessment,
  BookedDuplicatePairInput,
  BulkResolveBookedDuplicatesInput,
  ResolveBookedDuplicateInput,
  ReviewInboxResponse,
  ReviewItemBookedDuplicate,
} from "../lib/types";

type Props = { items: ReviewItemBookedDuplicate[]; total: number };
type Action = ResolveBookedDuplicateInput["action"];
type ActionMode = Action | "ai";
type MatchFilter = "all" | ReviewItemBookedDuplicate["match"];
type Booking = ReviewItemBookedDuplicate["first"];

// A selection refers to the versions the user actually saw, never a refreshed booking.
function pairKey(item: ReviewItemBookedDuplicate) {
  return `${item.first.transaction_id}:${item.first.version}:${item.second.transaction_id}:${item.second.version}`;
}

function pairInput(item: ReviewItemBookedDuplicate): BookedDuplicatePairInput {
  return {
    first_transaction_id: item.first.transaction_id,
    first_version: item.first.version,
    second_transaction_id: item.second.transaction_id,
    second_version: item.second.version,
  };
}

function assessmentKey(item: BookedDuplicatePairInput) {
  return `${item.first_transaction_id}:${item.first_version}:${item.second_transaction_id}:${item.second_version}`;
}

function affectedTransactions(items: ResolveBookedDuplicateInput[]) {
  return new Set(
    items
      .filter((item) => item.action !== "keep_both")
      .map((item) =>
        item.action === "archive_first" ? item.first_transaction_id : item.second_transaction_id,
      ),
  );
}

function removesWholeGroup(items: ReviewItemBookedDuplicate[], targets: Set<string>) {
  if (targets.size === 0) return false;
  const neighbors = new Map<string, Set<string>>();
  for (const item of items) {
    const first = item.first.transaction_id;
    const second = item.second.transaction_id;
    if (!neighbors.has(first)) neighbors.set(first, new Set());
    if (!neighbors.has(second)) neighbors.set(second, new Set());
    neighbors.get(first)!.add(second);
    neighbors.get(second)!.add(first);
  }
  const visited = new Set<string>();
  for (const id of neighbors.keys()) {
    if (visited.has(id)) continue;
    const pending = [id];
    let allRemoved = true;
    while (pending.length > 0) {
      const current = pending.pop()!;
      if (visited.has(current)) continue;
      visited.add(current);
      if (!targets.has(current)) allRemoved = false;
      for (const neighbor of neighbors.get(current)!) {
        if (!visited.has(neighbor)) pending.push(neighbor);
      }
    }
    if (allRemoved) return true;
  }
  return false;
}

type AssessmentRun = { cancelled: boolean; stop: boolean };
/** Abbruch oder Ausfall der KI-Pruefung: Klartext und - falls benannt - Grund des KI-Kerns. */
type AssessmentIssue = { code: string | null; text: string };
type AssessmentState = {
  householdId: string | null | undefined;
  results: Map<string, BookedDuplicateAssessment>;
  /** Paare mit echtem Urteil - "Nicht bewertet: …" zaehlt nicht mit. */
  completed: number;
  total: number;
  running: boolean;
  stopping: boolean;
  issue: AssessmentIssue | null;
};

function emptyAssessment(householdId: string | null | undefined): AssessmentState {
  return {
    householdId,
    results: new Map(),
    completed: 0,
    total: 0,
    running: false,
    stopping: false,
    issue: null,
  };
}

function isUnassessed(assessment: BookedDuplicateAssessment) {
  return isUnassessedVerdict(assessment.verdict, assessment.reason);
}

function AssessmentCell({ assessment }: { assessment?: BookedDuplicateAssessment }) {
  if (!assessment)
    return (
      <td className="txn-td duplicates-ai-cell">
        <span className="duplicates-ai-pending">Noch nicht bewertet</span>
      </td>
    );
  // Teilergebnis: zu diesem Paar wurde das Modell nach einem Abbruch nicht
  // mehr gefragt - das ist kein Urteil "unklar".
  const unassessed = isUnassessed(assessment);
  const label = unassessed
    ? "Nicht bewertet"
    : assessment.action === "keep_both"
      ? "Beide behalten"
      : assessment.action
        ? "Doppelt"
        : "Unklar";
  const proposal =
    assessment.action === "archive_first"
      ? "Früher erfasste entfernen"
      : assessment.action === "archive_second"
        ? "Später erfasste entfernen"
        : assessment.action === "keep_both"
          ? "Zwei eigenständige Buchungen"
          : "Manuell entscheiden";
  return (
    <td className="txn-td duplicates-ai-cell">
      <span
        className={`duplicates-ai-badge duplicates-ai-badge--${assessment.action ? "ready" : "unsure"}`}
      >
        {label}
      </span>
      <span className="duplicates-ai-proposal">{proposal}</span>
      <details className="duplicates-purpose duplicates-ai-reason">
        <summary>{unassessed ? "Grund" : "Begründung"}</summary>
        <p>{assessment.reason}</p>
      </details>
    </td>
  );
}

const currencies = new Map<string, Intl.NumberFormat>();
function formatAmount(booking: Booking) {
  if (!currencies.has(booking.currency)) {
    currencies.set(
      booking.currency,
      new Intl.NumberFormat("de-DE", { style: "currency", currency: booking.currency }),
    );
  }
  return currencies.get(booking.currency)!.format((booking.amount_minor ?? 0) / 100);
}

function formatDate(date?: string | null) {
  return date ? date.split("-").reverse().join(".") : "—";
}

function BookingCell({ booking, outcome }: { booking: Booking; outcome?: "remove" | "keep" }) {
  const source =
    booking.import_filename || (booking.source === "manual" ? "Manuell erfasst" : booking.source);
  return (
    <td className={`txn-td duplicates-booking ${outcome ? `duplicates-booking--${outcome}` : ""}`}>
      <div className="duplicates-booking__identity">
        <strong title={booking.payee_label || "Ohne Empfänger"}>
          {booking.payee_label || "Ohne Empfänger"}
        </strong>
        {outcome && (
          <span className={`duplicates-outcome duplicates-outcome--${outcome}`}>
            {outcome === "remove" ? "Entfernen" : "Bleibt"}
          </span>
        )}
      </div>
      <div className="duplicates-source" title={source || "Quelle unbekannt"}>
        {source || "Quelle unbekannt"}
      </div>
      {booking.purpose && (
        <details className="duplicates-purpose">
          <summary>Verwendungszweck</summary>
          <p>{booking.purpose}</p>
        </details>
      )}
    </td>
  );
}

export function BookedDuplicatesPanel({ items, total }: Props) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<MatchFilter>("all");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim().toLocaleLowerCase("de"));
  const [action, setAction] = useState<ActionMode>("archive_second");
  const [selection, setSelection] = useState({
    items,
    householdId: selectedHouseholdId,
    keys: new Set<string>(),
  });
  const [notice, setNotice] = useState("");
  const [assessment, setAssessment] = useState(() => emptyAssessment(selectedHouseholdId));
  const activeAssessment = useRef<AssessmentRun | null>(null);
  const canEdit = selectedHousehold?.role !== "viewer";

  // Cleanup invalidates the captured run before any response can enter another household.
  useLayoutEffect(
    () => () => {
      if (activeAssessment.current) activeAssessment.current.cancelled = true;
      activeAssessment.current = null;
    },
    [selectedHouseholdId],
  );

  if (assessment.householdId !== selectedHouseholdId) {
    setAssessment(emptyAssessment(selectedHouseholdId));
  }

  // Changed versions are unselected before any click can submit them.
  if (selection.items !== items || selection.householdId !== selectedHouseholdId) {
    const currentKeys = new Set(items.map(pairKey));
    setSelection({
      items,
      householdId: selectedHouseholdId,
      keys: new Set(
        selection.householdId === selectedHouseholdId
          ? [...selection.keys].filter((key) => currentKeys.has(key))
          : [],
      ),
    });
  }

  const selectedItems = useMemo(
    () => items.filter((item) => selection.keys.has(pairKey(item))),
    [items, selection.keys],
  );
  const visibleItems = useMemo(
    () =>
      items.filter((item) => {
        if (filter !== "all" && item.match !== filter) return false;
        if (!deferredSearch) return true;
        return [item.first, item.second].some((booking) =>
          [booking.account_name, booking.payee_label, booking.purpose, booking.import_filename]
            .filter(Boolean)
            .join(" ")
            .toLocaleLowerCase("de")
            .includes(deferredSearch),
        );
      }),
    [items, filter, deferredSearch],
  );
  const plannedItems = useMemo(
    () =>
      selectedItems.flatMap((item) => {
        const recommendation =
          assessment.householdId === selectedHouseholdId
            ? assessment.results.get(pairKey(item))
            : undefined;
        const decision = action === "ai" ? recommendation?.action : action;
        return decision ? [{ ...pairInput(item), action: decision }] : [];
      }),
    [selectedItems, action, assessment, selectedHouseholdId],
  );
  const plannedKeys = useMemo(() => new Set(plannedItems.map(assessmentKey)), [plannedItems]);
  const targets = useMemo(() => affectedTransactions(plannedItems), [plannedItems]);
  const keepConflict = plannedItems.some(
    (item) =>
      item.action === "keep_both" &&
      (targets.has(item.first_transaction_id) || targets.has(item.second_transaction_id)),
  );
  const conflicting = useMemo(
    () =>
      removesWholeGroup(
        selectedItems.filter((item) => plannedKeys.has(pairKey(item))),
        targets,
      ),
    [selectedItems, plannedKeys, targets],
  );
  const visibleSelected = visibleItems.filter((item) => selection.keys.has(pairKey(item))).length;
  const allVisibleSelected = visibleItems.length > 0 && visibleSelected === visibleItems.length;
  const hiddenSelected = selectedItems.length - visibleSelected;
  const exactCount = items.filter((item) => item.match === "duplicate_exact").length;

  const scan = useMutation({
    mutationFn: () => scanBookedDuplicates(accessToken!, selectedHouseholdId!),
    onSuccess: (result) => {
      queryClient.setQueriesData<ReviewInboxResponse>(
        { queryKey: ["review-inbox", selectedHouseholdId] },
        (previous) =>
          previous
            ? {
                ...previous,
                total_count:
                  previous.total_count -
                  (previous.booked_duplicate_count ?? 0) +
                  result.total_count,
                booked_duplicates: result.items,
                booked_duplicate_count: result.total_count,
              }
            : previous,
      );
      setNotice(
        `${result.scanned_count} Buchungen geprüft · ${result.total_count === 0 ? "Keine möglichen Duplikate gefunden." : `${result.total_count} ${result.total_count === 1 ? "mögliches Duplikatpaar" : "mögliche Duplikatpaare"} gefunden.`}`,
      );
    },
  });
  const resolve = useMutation({
    mutationFn: (input: BulkResolveBookedDuplicatesInput) =>
      resolveBookedDuplicatesBulk(accessToken!, selectedHouseholdId!, input),
    onSuccess: async (result, input) => {
      const appliedKeys = new Set(input.items.map(assessmentKey));
      setSelection((previous) => ({
        ...previous,
        keys: new Set([...previous.keys].filter((key) => !appliedKeys.has(key))),
      }));
      setAssessment((previous) => ({
        ...previous,
        results: new Map([...previous.results].filter(([key]) => !appliedKeys.has(key))),
      }));
      setNotice(
        result.archived_count > 0
          ? `${result.archived_count} ${result.archived_count === 1 ? "Buchung entfernt" : "Buchungen entfernt"} · ${result.processed_count} Paare erledigt.`
          : `${result.kept_count} ${result.kept_count === 1 ? "Paar" : "Paare"} erledigt · Beide Buchungen bleiben erhalten.`,
      );
      scan.reset();
      // Refresh this inbox once; other household views reload only when visited.
      void queryClient.invalidateQueries({
        predicate: (query) =>
          query.queryKey[0] !== "review-inbox" && query.queryKey.includes(selectedHouseholdId),
        refetchType: "none",
      });
      await queryClient.invalidateQueries({ queryKey: ["review-inbox", selectedHouseholdId] });
    },
  });
  const busy = scan.isPending || resolve.isPending || assessment.running;

  async function assessSelection() {
    if (busy || !canEdit || !accessToken || !selectedHouseholdId || selectedItems.length === 0)
      return;
    const run: AssessmentRun = { cancelled: false, stop: false };
    activeAssessment.current = run;
    const householdId = selectedHouseholdId;
    const selected = selectedItems.map(pairInput);
    let completed = 0;
    setNotice("");
    resolve.reset();
    setAssessment((previous) => ({
      ...previous,
      householdId,
      completed: 0,
      total: selected.length,
      running: true,
      stopping: false,
      issue: null,
    }));
    try {
      for (let offset = 0; offset < selected.length; offset += 25) {
        if (run.cancelled || run.stop) break;
        const chunk = selected.slice(offset, offset + 25);
        const result = await assessBookedDuplicates(accessToken, householdId, { items: chunk });
        if (run.cancelled) return;
        if (!result.available) {
          setAssessment((previous) => ({
            ...previous,
            issue: {
              code: result.reason,
              text:
                result.message ||
                "Die KI ist derzeit nicht verfügbar. Bereits bewertete Paare bleiben nutzbar.",
            },
          }));
          break;
        }
        const requestedKeys = new Set(chunk.map(assessmentKey));
        const answered = (result.assessments ?? []).filter(
          (recommendation) =>
            requestedKeys.has(assessmentKey(recommendation)) && !isUnassessed(recommendation),
        ).length;
        completed += answered;
        // Teilergebnis: der Server hat nach einem Abbruch aufgehoert zu fragen
        // und nennt Zahl und Grund. Weitere Pakete liefen in denselben Grund.
        const partial: AssessmentIssue | null =
          result.reason !== "ok" ? { code: result.reason, text: result.message } : null;
        setAssessment((previous) => {
          if (previous.householdId !== householdId) return previous;
          const results = new Map(previous.results);
          for (const key of requestedKeys) results.delete(key);
          // Only exact identities/versions from this request can become proposals.
          for (const recommendation of result.assessments ?? []) {
            if (requestedKeys.has(assessmentKey(recommendation)))
              results.set(assessmentKey(recommendation), recommendation);
          }
          return { ...previous, completed, results, issue: partial ?? previous.issue };
        });
        if (partial) break;
      }
    } catch (error) {
      if (!run.cancelled)
        setAssessment((previous) => ({
          ...previous,
          issue: {
            code: aiReasonFromError(error)?.code ?? null,
            text: `${error instanceof Error ? error.message : "KI-Prüfung fehlgeschlagen."} Bereits bewertete Paare bleiben nutzbar.`,
          },
        }));
    } finally {
      if (!run.cancelled) {
        setAssessment((previous) => ({ ...previous, running: false, stopping: false }));
        if (completed > 0) setAction("ai");
        activeAssessment.current = null;
      }
    }
  }

  function stopAssessment() {
    if (activeAssessment.current) activeAssessment.current.stop = true;
    setAssessment((previous) => ({ ...previous, stopping: true }));
  }

  function select(keys: string[], checked: boolean) {
    setSelection((previous) => {
      const next = new Set(previous.keys);
      for (const key of keys) {
        if (checked) next.add(key);
        else next.delete(key);
      }
      return { items, householdId: selectedHouseholdId, keys: next };
    });
    resolve.reset();
    setNotice("");
  }

  function submit() {
    if (busy || !canEdit || plannedItems.length === 0 || conflicting || keepConflict) return;
    setNotice("");
    resolve.mutate({
      items: plannedItems,
    });
  }

  return (
    <section
      id="booked-duplicates"
      className="card booked-duplicates-panel"
      aria-labelledby="duplicates-heading"
    >
      <div className="duplicates-header">
        <div>
          <h2 id="duplicates-heading" className="card-heading">
            Duplikate im Buchungsbestand <span className="duplicates-total">{total}</span>
          </h2>
          <p className="duplicates-hint">
            {canEdit
              ? "Paare vergleichen, Häkchen setzen und gemeinsam bearbeiten."
              : "Mögliche Duplikate vergleichen und erneut prüfen."}
          </p>
        </div>
        <button
          className="btn-secondary"
          type="button"
          disabled={busy || !accessToken || !selectedHouseholdId}
          onClick={() => {
            setNotice("");
            resolve.reset();
            scan.mutate();
          }}
        >
          {scan.isPending ? "Prüfe Buchungen…" : "Duplikate erneut prüfen"}
        </button>
      </div>
      {notice && (
        <p className="duplicates-feedback" role="status">
          {notice}
        </p>
      )}
      {(scan.error || resolve.error) && (
        <p className="duplicates-feedback duplicates-feedback--error" role="alert">
          {(scan.error || resolve.error)?.message}
        </p>
      )}
      {items.length > 0 ? (
        <>
          <div className="duplicates-filters">
            <label className="duplicates-filter">
              <span>Übereinstimmung</span>
              <select
                className="form-input"
                value={filter}
                onChange={(event) => setFilter(event.target.value as MatchFilter)}
                disabled={busy}
              >
                <option value="all">Alle ({items.length})</option>
                <option value="duplicate_exact">Exakt ({exactCount})</option>
                <option value="duplicate_possible">Möglich ({items.length - exactCount})</option>
              </select>
            </label>
            <label className="duplicates-filter duplicates-filter--search">
              <span>In Duplikaten suchen</span>
              <input
                className="form-input"
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Empfänger, Konto, Zweck oder Importdatei"
                disabled={busy}
              />
            </label>
            <span className="duplicates-visible">
              {visibleItems.length} von {total} Paaren
            </span>
          </div>
          {canEdit && (
            <div
              className="duplicates-toolbar"
              aria-label="Duplikate gemeinsam bearbeiten"
              aria-busy={resolve.isPending || assessment.running}
            >
              <div className="duplicates-selection">
                <label className="duplicates-select-all">
                  <input
                    type="checkbox"
                    aria-label="Alle sichtbaren Paare auswählen"
                    checked={allVisibleSelected}
                    ref={(element) => {
                      if (element)
                        element.indeterminate = visibleSelected > 0 && !allVisibleSelected;
                    }}
                    onChange={(event) => select(visibleItems.map(pairKey), event.target.checked)}
                    disabled={busy || visibleItems.length === 0}
                  />
                  <span>Alle sichtbaren</span>
                </label>
                <strong>{selectedItems.length} ausgewählt</strong>
                {hiddenSelected > 0 && (
                  <span className="duplicates-hidden">davon {hiddenSelected} ausgeblendet</span>
                )}
                {selectedItems.length > 0 && (
                  <button
                    className="duplicates-clear"
                    type="button"
                    disabled={busy}
                    onClick={() => select([...selection.keys], false)}
                  >
                    Auswahl aufheben
                  </button>
                )}
              </div>
              <div className="duplicates-actions">
                <button
                  className="btn-secondary duplicates-ai-trigger"
                  type="button"
                  disabled={
                    busy || selectedItems.length === 0 || !accessToken || !selectedHouseholdId
                  }
                  onClick={() => void assessSelection()}
                >
                  {assessment.running ? "KI bewertet…" : "KI bewerten"}
                </button>
                <select
                  className="form-input"
                  aria-label="Aktion für ausgewählte Paare"
                  value={action}
                  disabled={busy}
                  onChange={(event) => {
                    setAction(event.target.value as ActionMode);
                    resolve.reset();
                  }}
                >
                  <option value="archive_second">Später erfasste entfernen</option>
                  <option value="archive_first">Früher erfasste entfernen</option>
                  <option value="keep_both">Beide behalten</option>
                  <option value="ai">KI-Entscheidungen übernehmen</option>
                </select>
                <button
                  className={targets.size === 0 ? "btn-primary" : "btn-danger"}
                  type="button"
                  disabled={
                    busy ||
                    plannedItems.length === 0 ||
                    conflicting ||
                    keepConflict ||
                    !accessToken ||
                    !selectedHouseholdId
                  }
                  onClick={submit}
                >
                  {resolve.isPending
                    ? "Bearbeite Auswahl…"
                    : action === "ai"
                      ? `${plannedItems.length} KI-Entscheidungen übernehmen`
                      : action === "keep_both"
                        ? `${selectedItems.length} ${selectedItems.length === 1 ? "Paar" : "Paare"} behalten`
                        : `${targets.size} ${targets.size === 1 ? "Buchung" : "Buchungen"} entfernen`}
                </button>
              </div>
              {(assessment.total > 0 || assessment.issue) && (
                <div className="duplicates-ai-progress">
                  <span role="status">
                    {assessment.completed} / {assessment.total} bewertet
                    {assessment.running
                      ? " · KI-Prüfung läuft"
                      : assessment.completed < assessment.total
                        ? " · KI-Prüfung beendet"
                        : " · KI-Prüfung abgeschlossen"}
                  </span>
                  {assessment.running && (
                    <button
                      className="duplicates-clear"
                      type="button"
                      onClick={stopAssessment}
                      disabled={assessment.stopping}
                    >
                      {assessment.stopping ? "Stoppt nach aktuellem Paket…" : "KI-Prüfung stoppen"}
                    </button>
                  )}
                  {assessment.running && (
                    <span className="duplicates-hint">
                      Stoppt nach dem laufenden Paket. Es werden keine Buchungen geändert.
                    </span>
                  )}
                  {assessment.issue && (
                    <AiReasonNotice
                      className="duplicates-ai-error"
                      role="alert"
                      code={assessment.issue.code}
                      message={assessment.issue.text}
                    />
                  )}
                </div>
              )}
              {action === "ai" && (
                <p className="duplicates-ai-plan">
                  <strong>
                    {plannedItems.length} von {selectedItems.length} KI-Entscheidungen anwendbar
                  </strong>
                  {" · "}
                  {targets.size} {targets.size === 1 ? "Buchung entfernen" : "Buchungen entfernen"}
                  {" · "}
                  {plannedItems.filter((item) => item.action === "keep_both").length} Paare
                  behalten.
                  {selectedItems.length > plannedItems.length &&
                    ` ${selectedItems.length - plannedItems.length} unklare oder unbewertete Paare bleiben offen.`}
                </p>
              )}
              {conflicting && (
                <p className="duplicates-conflict" role="alert">
                  Diese Auswahl würde alle Buchungen einer Duplikatgruppe entfernen. Hebe mindestens
                  ein Häkchen auf oder ändere die Aktion.
                </p>
              )}
              {keepConflict && (
                <p className="duplicates-conflict" role="alert">
                  Die KI-Vorschläge widersprechen sich: Eine Buchung soll zugleich entfernt und mit
                  „Beide behalten“ erhalten werden. Hebe ein betroffenes Häkchen auf oder wähle eine
                  gemeinsame manuelle Aktion.
                </p>
              )}
            </div>
          )}
          <div
            className="txn-table-wrapper duplicates-table-wrapper"
            tabIndex={0}
            role="region"
            aria-label="Duplikatpaare vergleichen"
          >
            <table className="txn-table duplicates-table">
              <thead>
                <tr>
                  {canEdit && (
                    <th className="txn-th duplicates-check" scope="col" aria-label="Auswahl" />
                  )}
                  <th className="txn-th duplicates-date" scope="col">
                    Datum
                  </th>
                  <th className="txn-th duplicates-account" scope="col">
                    Konto
                  </th>
                  <th className="txn-th duplicates-amount" scope="col">
                    Betrag
                  </th>
                  <th
                    className={`txn-th ${canEdit && action === "archive_first" ? "duplicates-column--remove" : ""}`}
                    scope="col"
                  >
                    Früher erfasst
                    {canEdit && action === "archive_first" && <small>Entfernen bei Auswahl</small>}
                  </th>
                  <th
                    className={`txn-th ${canEdit && action === "archive_second" ? "duplicates-column--remove" : ""}`}
                    scope="col"
                  >
                    Später erfasst
                    {canEdit && action === "archive_second" && <small>Entfernen bei Auswahl</small>}
                  </th>
                  <th className="txn-th duplicates-match" scope="col">
                    Treffer
                  </th>
                  <th className="txn-th duplicates-ai-cell" scope="col">
                    KI-Entscheidung
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((item) => {
                  const key = pairKey(item);
                  const checked = selection.keys.has(key);
                  const outcome = (booking: Booking) =>
                    targets.has(booking.transaction_id)
                      ? "remove"
                      : plannedKeys.has(key)
                        ? "keep"
                        : undefined;
                  return (
                    <tr className={`txn-tr ${checked ? "duplicates-row--selected" : ""}`} key={key}>
                      {canEdit && (
                        <td className="txn-td duplicates-check">
                          <input
                            type="checkbox"
                            aria-label={`Paar auswählen: ${item.first.payee_label || "Ohne Empfänger"}, ${formatDate(item.first.booked_date)}, ${item.first.import_filename || item.first.transaction_id} / ${item.second.import_filename || item.second.transaction_id}`}
                            checked={checked}
                            disabled={busy}
                            onChange={(event) => select([key], event.target.checked)}
                          />
                        </td>
                      )}
                      <td className="txn-td duplicates-date">
                        {formatDate(item.first.booked_date)}
                      </td>
                      <td
                        className="txn-td duplicates-account"
                        title={item.first.account_name ?? ""}
                      >
                        {item.first.account_name}
                      </td>
                      <td className="txn-td duplicates-amount">{formatAmount(item.first)}</td>
                      <BookingCell
                        booking={item.first}
                        outcome={canEdit ? outcome(item.first) : undefined}
                      />
                      <BookingCell
                        booking={item.second}
                        outcome={canEdit ? outcome(item.second) : undefined}
                      />
                      <td className="txn-td duplicates-match">
                        <span
                          className={`duplicates-match-badge duplicates-match-badge--${item.match === "duplicate_exact" ? "exact" : "possible"}`}
                          title={
                            item.match === "duplicate_exact"
                              ? "Gleiche Referenz oder gleicher Fingerabdruck"
                              : "Ähnlicher Empfänger oder gleicher Verwendungszweck"
                          }
                        >
                          {item.match === "duplicate_exact" ? "Exakt" : "Möglich"}
                        </span>
                      </td>
                      <AssessmentCell assessment={assessment.results.get(key)} />
                    </tr>
                  );
                })}
                {visibleItems.length === 0 && (
                  <tr>
                    <td className="duplicates-empty" colSpan={canEdit ? 8 : 7}>
                      Keine Paare für diese Suche. Passe den Filter oder Suchbegriff an.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="duplicates-footer">
            {total > items.length
              ? `Die ersten ${items.length} von ${total} Paaren sind geladen. Weitere rücken nach der Bearbeitung nach.`
              : "Ein Paar zeigt zwei möglicherweise doppelt erfasste Buchungen."}
            {canEdit &&
              " Entfernt wird erst mit dem Aktionsbutton. Mehrfach vorkommende Buchungen werden nur einmal gezählt."}
          </p>
        </>
      ) : (
        <p className="duplicates-empty">
          Keine offenen Duplikatpaare. Du kannst den Buchungsbestand jederzeit erneut prüfen.
        </p>
      )}
    </section>
  );
}
