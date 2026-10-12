import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  ApiError,
  deleteExplorerRows,
  listExplorerTables,
  previewExplorerDelete,
  previewHouseholdPurge,
  purgeHousehold,
  readExplorerRows,
} from "../lib/api";
import type { ExplorerColumn, ExplorerDeletePreview, ExplorerTableSummary } from "../lib/types";
import { Modal } from "../components/Modal";

const PAGE_SIZE = 50;

const TABLE_EXPLANATIONS: Record<string, string> = {
  classification_decisions:
    "Unveränderlicher Verlauf der KI-Kategorisierungen. Eine Buchung kann mehrere Einträge haben, wenn sie erneut geprüft wurde; für Vorschläge zählt jeweils der neueste.",
  llm_calls:
    "Technisches Modellprotokoll. Jeder echte Versuch ist eine Zeile – einschließlich Wiederholungen, Schema-Fallbacks und Fehlern. Diese Zeilen verändern keine Buchung.",
  ai_tasks:
    "Gespeicherter KI-Status: Warteschlange, aktuelle Phase, Ergebnis und Fehlergrund der sichtbaren KI-Aufgaben.",
  ai_task_events:
    "Zeitlicher Ablauf der KI-Aufgaben. Die Einträge erklären, welcher Verarbeitungsschritt wann lief oder scheiterte.",
  project_cost_ai_operations:
    "Vorschauen und bestätigte Übernahmen der KI-Kostenschätzung. Eine Vorschau verändert den Kostenplan noch nicht.",
};

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Unbekannter Fehler.";
}

/**
 * Zellenwert lesbar machen, ohne die Rohform zu verlieren.
 *
 * Der Explorer zeigt, was WIRKLICH in der Zeile steht — er ist die Stelle, an
 * die man geht, wenn die aufbereiteten Ansichten nicht weiterhelfen. Deshalb
 * wird hier bewusst kaum formatiert: Beträge bleiben Cent-Zahlen, Zeitstempel
 * werden nur gekürzt, verschachtelte Werte als JSON gezeigt. Ein hier hübsch
 * gerechneter Euro-Betrag würde genau die Frage verschleiern, wegen der man
 * die Tabelle aufgemacht hat.
 */
function formatCell(value: unknown, column: ExplorerColumn): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "ja" : "nein";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  // "2026-08-05T12:34:56.789Z" -> "2026-08-05 12:34"
  if (column.data_type.startsWith("timestamp") && text.length > 16) {
    return `${text.slice(0, 10)} ${text.slice(11, 16)}`;
  }
  return text;
}

/**
 * Datenbank-Explorer: alle Haushaltstabellen sehen und aufräumen.
 *
 * Die Anwendung hat 79 haushaltsbezogene Tabellen, aber nur für einen Teil
 * davon eine Oberfläche mit Löschfunktion. Was über einen Import, einen
 * KI-Lauf oder einen abgebrochenen Versuch hineingekommen ist, war bisher
 * teilweise nur mit einem SQL-Werkzeug von außen wieder herauszubekommen.
 *
 * Gelöscht wird nur über die Vorschau. `on delete cascade` reicht in diesem
 * Schema bis zu vier Ebenen — eine Essenswoche nimmt Mahlzeiten,
 * Einkaufslisten, deren Posten und die Kaufverknüpfungen mit.
 */
export function DatenbankPage() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const token = session?.access_token ?? "";
  const householdId = selectedHouseholdId ?? "";

  const [activeTable, setActiveTable] = useState<string | null>(null);
  const [tableFilter, setTableFilter] = useState("");
  const [rowSearch, setRowSearch] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<ExplorerDeletePreview | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [purgeConfirm, setPurgeConfirm] = useState("");

  const { data: catalog, isLoading: catalogLoading } = useQuery({
    queryKey: ["explorer-tables", householdId],
    queryFn: () => listExplorerTables(token, householdId),
    enabled: Boolean(token && householdId),
  });

  const {
    data: rowPage,
    isLoading: rowsLoading,
    isError: rowsError,
    error: rowsErrorValue,
  } = useQuery({
    queryKey: ["explorer-rows", householdId, activeTable, page, rowSearch],
    queryFn: () =>
      readExplorerRows(token, householdId, activeTable!, {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        search: rowSearch || undefined,
      }),
    enabled: Boolean(token && householdId && activeTable),
  });

  const previewMutation = useMutation({
    mutationFn: (ids: string[]) => previewExplorerDelete(token, householdId, activeTable!, ids),
    onSuccess: (result) => {
      setPreview(result);
      setConfirmText("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (ids: string[]) => deleteExplorerRows(token, householdId, activeTable!, ids),
    onSuccess: () => {
      setPreview(null);
      setSelected(new Set());
      setPage(0);
      // Die Kaskade betrifft andere Tabellen mit — deren Zählstand und Zeilen
      // sind danach falsch, wenn nur die aktuelle Ansicht neu geladen wird.
      void queryClient.invalidateQueries({ queryKey: ["explorer-tables", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["explorer-rows", householdId] });
    },
  });

  const groups = useMemo(() => {
    const filter = tableFilter.trim().toLowerCase();
    const visible = (catalog?.tables ?? []).filter(
      (table) => !filter || table.name.toLowerCase().includes(filter),
    );
    const byGroup = new Map<string, ExplorerTableSummary[]>();
    for (const table of visible) {
      const bucket = byGroup.get(table.group) ?? [];
      bucket.push(table);
      byGroup.set(table.group, bucket);
    }
    return [...byGroup.entries()].sort((a, b) => a[0].localeCompare(b[0], "de"));
  }, [catalog, tableFilter]);

  const currentTable = catalog?.tables.find((table) => table.name === activeTable) ?? null;
  const primaryKey = rowPage?.primary_key ?? null;
  const totalPages = rowPage ? Math.ceil(rowPage.total / PAGE_SIZE) : 0;

  const openTable = (name: string) => {
    setActiveTable(name);
    setPage(0);
    setRowSearch("");
    setSelected(new Set());
    setPreview(null);
  };

  const toggleRow = (id: string) => {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllOnPage = () => {
    if (!rowPage || !primaryKey) return;
    const idsOnPage = rowPage.rows.map((row) => String(row[primaryKey]));
    const allChosen = idsOnPage.every((id) => selected.has(id));
    setSelected((previous) => {
      const next = new Set(previous);
      for (const id of idsOnPage) {
        if (allChosen) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  };

  // Ab dieser Menge muss der Tabellenname getippt werden. Ein Klick auf
  // "Löschen" bei drei Zeilen ist eine Korrektur, bei dreitausend ist es ein
  // anderer Vorgang — und der darf sich nicht gleich anfühlen.
  const confirmThreshold = 25;
  const totalAffected = (preview?.direct_rows ?? 0) + (preview?.cascade_total ?? 0);
  const needsTyping = totalAffected >= confirmThreshold;
  const mayDelete = !needsTyping || confirmText.trim() === activeTable;

  const purgePreviewMutation = useMutation({
    mutationFn: () => previewHouseholdPurge(token, householdId),
    onSuccess: () => setPurgeConfirm(""),
  });

  const purgeMutation = useMutation({
    mutationFn: (name: string) => purgeHousehold(token, householdId, name),
    onSuccess: () => {
      purgePreviewMutation.reset();
      setPurgeConfirm("");
      setActiveTable(null);
      setSelected(new Set());
      // Nach einem Leeren stimmt kein Zählstand mehr — und auch keine der
      // Ansichten außerhalb dieser Seite.
      void queryClient.invalidateQueries();
    },
  });

  const purgePreview = purgePreviewMutation.data;
  // Der Name muss exakt stimmen; geprüft wird zusätzlich auf dem Server. Bei
  // hunderten Testhaushalten neben einem echten wäre eine reine
  // Oberflächenprüfung keine Sicherung.
  const mayPurge = Boolean(purgePreview) && purgeConfirm.trim() === purgePreview?.household_name;

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Datenbank</h1>
          <p className="page-subtitle">
            Alle Tabellen dieses Haushalts — ansehen, durchsuchen, löschen. Was hier steht, ist der
            Rohzustand: Beträge in Cent, Zeitstempel unformatiert, Verweise als ID.
          </p>
        </div>
        {catalog && (
          <div className="explorer-total">
            <span className="explorer-total__value">
              {catalog.total_rows.toLocaleString("de-DE")}
            </span>
            <span className="explorer-total__label">Zeilen gesamt</span>
          </div>
        )}
      </div>

      <div className="explorer-layout">
        <aside className="explorer-tables card" aria-label="Tabellen">
          <input
            type="search"
            className="form-input"
            placeholder="Tabelle suchen …"
            value={tableFilter}
            onChange={(event) => setTableFilter(event.target.value)}
            aria-label="Tabelle suchen"
          />
          {catalogLoading && <p className="food-fill-note">Katalog wird geladen …</p>}
          {groups.map(([group, tables]) => (
            <div key={group} className="explorer-group">
              <h2 className="explorer-group__title">{group}</h2>
              <ul className="explorer-group__list">
                {tables.map((table) => (
                  <li key={table.name}>
                    <button
                      type="button"
                      className={`explorer-table-button ${
                        table.name === activeTable ? "explorer-table-button--active" : ""
                      } ${table.row_count === 0 ? "explorer-table-button--empty" : ""}`}
                      onClick={() => openTable(table.name)}
                    >
                      <span className="explorer-table-button__name">{table.name}</span>
                      <span className="explorer-table-button__count">{table.row_count}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </aside>

        <div className="explorer-content">
          {!activeTable && (
            <div className="card explorer-empty">
              <h2 className="card-heading">Tabelle wählen</h2>
              <p className="card-body">
                Links eine Tabelle auswählen. Gelöscht wird immer erst nach einer Vorschau, die
                zeigt, was an den gewählten Zeilen hängt — im Schema reichen Kaskaden bis zu vier
                Ebenen tief.
              </p>
            </div>
          )}

          {activeTable && (
            <>
              <div className="card explorer-toolbar">
                <div>
                  <h2 className="card-heading">{activeTable}</h2>
                  <p className="food-section-hint">
                    {rowPage ? `${rowPage.total} Zeilen` : "…"}
                    {currentTable && !currentTable.is_deletable && (
                      <> · nur lesbar (kein einspaltiger Primärschlüssel)</>
                    )}
                  </p>
                  {TABLE_EXPLANATIONS[activeTable] && (
                    <p className="food-section-hint">{TABLE_EXPLANATIONS[activeTable]}</p>
                  )}
                </div>
                <input
                  type="search"
                  className="form-input explorer-row-search"
                  placeholder="In Textspalten suchen …"
                  value={rowSearch}
                  onChange={(event) => {
                    setRowSearch(event.target.value);
                    setPage(0);
                  }}
                  aria-label="Zeilen durchsuchen"
                />
                <button
                  type="button"
                  className="btn-danger"
                  disabled={selected.size === 0 || previewMutation.isPending}
                  onClick={() => previewMutation.mutate([...selected])}
                >
                  {previewMutation.isPending ? "Wird geprüft …" : `${selected.size} löschen …`}
                </button>
              </div>

              {rowsError && (
                <p className="food-fill-note food-fill-note--error" role="status">
                  {describeError(rowsErrorValue)}
                </p>
              )}

              <div className="txn-table-wrapper explorer-rows">
                <table className="txn-table">
                  <thead>
                    <tr>
                      {primaryKey && (
                        <th className="txn-th explorer-check">
                          <input
                            type="checkbox"
                            aria-label="Alle Zeilen dieser Seite auswählen"
                            checked={
                              (rowPage?.rows.length ?? 0) > 0 &&
                              (rowPage?.rows ?? []).every((row) =>
                                selected.has(String(row[primaryKey])),
                              )
                            }
                            onChange={toggleAllOnPage}
                          />
                        </th>
                      )}
                      {(rowPage?.columns ?? []).map((column) => (
                        <th
                          key={column.name}
                          className="txn-th"
                          // Der Name steht mit im Titel, weil eine sehr lange
                          // Spaltenbeschriftung abgeschnitten wird - sonst waere
                          // gerade die Spalte nicht benennbar, die man sucht.
                          title={`${column.name} · ${column.data_type}`}
                        >
                          {column.name}
                          {column.is_primary_key && (
                            <span className="explorer-pk" aria-label="Primärschlüssel">
                              {" "}
                              ★
                            </span>
                          )}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rowsLoading && (
                      <tr>
                        <td className="food-table-message" colSpan={99}>
                          Zeilen werden geladen …
                        </td>
                      </tr>
                    )}
                    {!rowsLoading && (rowPage?.rows.length ?? 0) === 0 && (
                      <tr>
                        <td className="food-table-message" colSpan={99}>
                          {rowSearch ? "Kein Treffer." : "Diese Tabelle ist leer."}
                        </td>
                      </tr>
                    )}
                    {(rowPage?.rows ?? []).map((row, index) => {
                      const id = primaryKey ? String(row[primaryKey]) : String(index);
                      return (
                        <tr key={id} className="txn-tr">
                          {primaryKey && (
                            <td className="txn-td explorer-check">
                              <input
                                type="checkbox"
                                checked={selected.has(id)}
                                onChange={() => toggleRow(id)}
                                aria-label={`Zeile ${id} auswählen`}
                              />
                            </td>
                          )}
                          {(rowPage?.columns ?? []).map((column) => (
                            <td key={column.name} className="txn-td explorer-cell">
                              <span title={String(row[column.name] ?? "")}>
                                {formatCell(row[column.name], column)}
                              </span>
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="explorer-pager">
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={page === 0}
                    onClick={() => setPage((current) => Math.max(0, current - 1))}
                  >
                    ‹ Zurück
                  </button>
                  <span className="food-section-hint">
                    Seite {page + 1} von {totalPages}
                  </span>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={page + 1 >= totalPages}
                    onClick={() => setPage((current) => current + 1)}
                  >
                    Weiter ›
                  </button>
                </div>
              )}

              {selected.size > 0 && (
                <p className="food-fill-note">
                  {selected.size} Zeile(n) ausgewählt. Die Auswahl bleibt beim Blättern bestehen.
                </p>
              )}
            </>
          )}
        </div>
      </div>

      {preview && (
        <Modal title={`${preview.table} löschen`} onClose={() => setPreview(null)}>
          <div className="explorer-preview">
            <p className="card-body">
              <strong>{preview.direct_rows}</strong> Zeile(n) aus <code>{preview.table}</code>
              {preview.cascade_total > 0 ? (
                <>
                  {" "}
                  — und <strong>{preview.cascade_total} weitere</strong> Zeile(n) aus anderen
                  Tabellen, die daran hängen.
                </>
              ) : (
                <> — nichts hängt daran.</>
              )}
            </p>

            {preview.effects.filter((effect) => effect.effect === "delete").length > 0 && (
              <>
                <h3 className="card-heading">Wird mitgelöscht</h3>
                <ul className="explorer-effects">
                  {preview.effects
                    .filter((effect) => effect.effect === "delete")
                    .map((effect) => (
                      <li key={`${effect.table}-${effect.via_column}`}>
                        <strong>{effect.rows}</strong> × {effect.table}
                        <span className="food-section-hint">
                          {" "}
                          über {effect.via_column}, Ebene {effect.depth}
                        </span>
                      </li>
                    ))}
                </ul>
              </>
            )}

            {preview.detach_total > 0 && (
              <>
                <h3 className="card-heading">Verliert nur den Verweis</h3>
                <p className="food-section-hint">
                  Diese Zeilen bleiben stehen, ihr Verweis wird auf leer gesetzt.
                </p>
                <ul className="explorer-effects">
                  {preview.effects
                    .filter((effect) => effect.effect === "detach")
                    .map((effect) => (
                      <li key={`${effect.table}-${effect.via_column}`}>
                        <strong>{effect.rows}</strong> × {effect.table}
                        <span className="food-section-hint"> über {effect.via_column}</span>
                      </li>
                    ))}
                </ul>
              </>
            )}

            <p className="food-fill-note food-fill-note--error">
              Löschen lässt sich nicht rückgängig machen. Der Vorgang wird im Audit-Log
              festgehalten, samt Inhalt der gelöschten Zeilen — das ist ein Nachweis, keine
              Wiederherstellung.
            </p>

            {needsTyping && (
              <label className="form-field">
                <span className="form-label">
                  Zum Bestätigen <code>{preview.table}</code> eintippen
                </span>
                <input
                  className="form-input"
                  value={confirmText}
                  onChange={(event) => setConfirmText(event.target.value)}
                  placeholder={preview.table}
                  autoComplete="off"
                />
              </label>
            )}

            <div className="food-proposal__actions">
              <button
                type="button"
                className="btn-danger"
                disabled={!mayDelete || deleteMutation.isPending || preview.direct_rows === 0}
                onClick={() => deleteMutation.mutate([...selected])}
              >
                {deleteMutation.isPending
                  ? "Wird gelöscht …"
                  : `${totalAffected} Zeile(n) endgültig löschen`}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setPreview(null)}>
                Abbrechen
              </button>
              {deleteMutation.isError && (
                <span className="food-save-state food-save-state--error" role="status">
                  {describeError(deleteMutation.error)}
                </span>
              )}
            </div>
          </div>
        </Modal>
      )}

      <div className="card explorer-danger">
        <h2 className="card-heading">Haushalt leeren</h2>
        <p className="card-body">
          Entfernt alle Bewegungsdaten dieses Haushalts — Buchungen, Konten, Verträge, Ziele,
          Vorhaben, Lebensmittelplanung, Importläufe. <strong>Nicht</strong> angetastet werden deine
          Zugehörigkeit, der Kategorienbaum und die Protokolle; der Haushalt bleibt danach sofort
          benutzbar.
        </p>
        <p className="food-section-hint">
          Der Rückweg liegt außerhalb der App:{" "}
          <code>python scripts/db_snapshot.py save --name vor-versuch</code> sichert vorher den
          ganzen Stand, <code>… restore vor-versuch</code> holt ihn zurück.
        </p>

        {!purgePreview && (
          <button
            type="button"
            className="btn-danger"
            disabled={purgePreviewMutation.isPending}
            onClick={() => purgePreviewMutation.mutate()}
          >
            {purgePreviewMutation.isPending ? "Wird geprüft …" : "Leeren vorbereiten …"}
          </button>
        )}

        {purgePreviewMutation.isError && (
          <p className="food-fill-note food-fill-note--error" role="status">
            {describeError(purgePreviewMutation.error)}
          </p>
        )}

        {purgePreview && (
          <div className="explorer-preview">
            <p className="card-body">
              <strong>{purgePreview.total_rows.toLocaleString("de-DE")}</strong> Zeilen aus{" "}
              <strong>{purgePreview.tables.length}</strong> Tabellen von{" "}
              <code>{purgePreview.household_name}</code> würden gelöscht.
            </p>
            <ul className="explorer-effects">
              {purgePreview.tables.slice(0, 8).map((table) => (
                <li key={table.name}>
                  <strong>{table.row_count.toLocaleString("de-DE")}</strong> × {table.name}
                </li>
              ))}
              {purgePreview.tables.length > 8 && (
                <li className="food-section-hint">
                  … und {purgePreview.tables.length - 8} weitere Tabellen
                </li>
              )}
            </ul>
            <p className="food-section-hint">
              Bleibt stehen: {purgePreview.kept_tables.join(", ")}.
            </p>
            <p className="food-fill-note food-fill-note--error">
              Das lässt sich nicht rückgängig machen. Wenn du keinen Schnappschuss hast, jetzt
              abbrechen und erst einen anlegen.
            </p>
            <label className="form-field">
              <span className="form-label">
                Zum Bestätigen den Haushaltsnamen eintippen:{" "}
                <code>{purgePreview.household_name}</code>
              </span>
              <input
                className="form-input"
                value={purgeConfirm}
                onChange={(event) => setPurgeConfirm(event.target.value)}
                placeholder={purgePreview.household_name}
                autoComplete="off"
              />
            </label>
            <div className="food-proposal__actions">
              <button
                type="button"
                className="btn-danger"
                disabled={!mayPurge || purgeMutation.isPending}
                onClick={() => purgeMutation.mutate(purgeConfirm.trim())}
              >
                {purgeMutation.isPending
                  ? "Wird geleert …"
                  : `${purgePreview.total_rows.toLocaleString("de-DE")} Zeilen endgültig löschen`}
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => purgePreviewMutation.reset()}
              >
                Abbrechen
              </button>
              {purgeMutation.isError && (
                <span className="food-save-state food-save-state--error" role="status">
                  {describeError(purgeMutation.error)}
                </span>
              )}
            </div>
          </div>
        )}

        {purgeMutation.isSuccess && !purgePreview && (
          <p className="food-fill-note" role="status">
            {purgeMutation.data.deleted_rows.toLocaleString("de-DE")} Zeilen aus{" "}
            {Object.keys(purgeMutation.data.deleted_per_table).length} Tabellen gelöscht.
          </p>
        )}
      </div>

      {deleteMutation.isSuccess && !preview && (
        <p className="food-fill-note" role="status">
          {deleteMutation.data.deleted} Zeile(n) gelöscht
          {deleteMutation.data.cascade_total > 0 &&
            `, dazu ${deleteMutation.data.cascade_total} abhängige`}
          .
        </p>
      )}
    </section>
  );
}
