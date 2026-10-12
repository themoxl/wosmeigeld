import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { Modal } from "../components/Modal";
import { ProvenancePanel } from "../components/ProvenancePanel";
import { TransactionDrilldown } from "../components/analytics/TransactionDrilldown";
import { TransactionTable } from "../components/analytics/TransactionTable";
import { GroupedResultTable, type GroupSortKey } from "../components/analytics/GroupedResultTable";
import { AddFilterControl } from "../components/explorer/AddFilterControl";
import { ExplorerColumnMenu } from "../components/explorer/ExplorerColumnMenu";
import { ColumnPicker } from "../components/datagrid/ColumnPicker";
import { GridColumnHeader } from "../components/datagrid/GridColumnHeader";
import { GroupingBar } from "../components/datagrid/GroupingBar";
import { Chart } from "../components/charts/Chart";
import { barOption } from "../components/charts/buildOptions";
import { useAnalyticsQuery, useFieldCatalog } from "../hooks/useAnalytics";
import { useStoredState } from "../hooks/useStoredState";
import {
  collectConditions,
  describeCondition,
  dimensionKey,
  drilldownQuery,
  PERIOD_PRESETS,
  queryFromSearchParams,
  queryToSearchParams,
  removeCondition,
  upsertCondition,
} from "../lib/analytics";
import { allGroupPaths, buildGroupTree } from "../lib/groupTree";
import { gridColumnFromField, groupOptions, parseDimensionKey } from "../lib/explorerGrid";
import { downloadAnalyticsCsv } from "../lib/api";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type {
  AnalyticsCondition,
  AnalyticsQuery,
  AnalyticsResult,
  DimensionRef,
  MeasureRef,
} from "../lib/types";

// Der Buchungs-Explorer (docs/ANALYSE-EXPLORER-PLAN.md, Baustein C).
//
// Das bestehende Buchungsregister bleibt die schnelle Erfassungs- und
// Korrekturansicht; diese Seite ist die analytische Sicht: Filter je Spalte
// ueber den vollen Operatorvorrat, serverseitige Sortierung, mehrstufige
// Gruppierung mit aufklappbaren Zwischensummen und Spaltenwahl.

const BASE_QUERY: AnalyticsQuery = {
  grain: "rows",
  group_by: [],
  measures: [],
  fields: [],
  period: { kind: "last_n_months", n: 12 },
  order_by: [],
  limit: 100,
  include_transfers: false,
  include_archived_categories: true,
  only_on_budget: false,
};

// Die Kennzahlen jeder Gruppenansicht. Alle drei sind ueber Ebenen hinweg
// summierbar - genau das ist die Bedingung dafuer, dass der aufklappbare Baum
// auf jeder Ebene widerspruchsfrei bleibt (siehe lib/groupTree.ts).
const MEASURES: MeasureRef[] = [
  { field: "amount", aggregate: "sum" },
  { field: "amount", aggregate: "avg_per_month" },
  { field: "amount", aggregate: "count" },
];

const FIELDS_STORAGE_KEY = "explorer.rowFields";

function periodKeyOf(query: AnalyticsQuery): string {
  const preset = PERIOD_PRESETS.find(
    (candidate) =>
      candidate.period.kind === query.period?.kind &&
      (candidate.period.n ?? null) === (query.period?.n ?? null),
  );
  return preset?.key ?? "custom";
}

export function ExplorerPage() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const [searchParams, setSearchParams] = useSearchParams();
  const [detailTransactionId, setDetailTransactionId] = useState<string | null>(null);
  const [drilldown, setDrilldown] = useState<AnalyticsQuery | null>(null);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [treeSort, setTreeSort] = useState<GroupSortKey>({
    key: "sum_amount",
    direction: "desc",
  });

  const query = useMemo(() => queryFromSearchParams(searchParams, BASE_QUERY), [searchParams]);
  const [searchDraft, setSearchDraft] = useState(() => query.search ?? "");

  const setQuery = useCallback(
    (next: AnalyticsQuery) => {
      setSearchParams(queryToSearchParams(next), { replace: true });
    },
    [setSearchParams],
  );

  const { data: catalog } = useFieldCatalog();
  const { data, isLoading, error, isFetching } = useAnalyticsQuery(query);

  // Spaltenwahl der Zeilenansicht. Sie gehoert nicht in die URL: eine geteilte
  // Auswertung soll dieselben Zahlen zeigen, nicht die Spaltenvorlieben des
  // Absenders erzwingen.
  const [storedFields, setStoredFields] = useStoredState<string[]>(FIELDS_STORAGE_KEY, []);
  const defaultFields = catalog?.default_row_fields ?? [];
  const rowFields = storedFields.length > 0 ? storedFields : defaultFields;

  const fieldByKey = useMemo(() => {
    const map = new Map<string, NonNullable<typeof catalog>["fields"][number]>();
    catalog?.fields.forEach((field) => map.set(field.key, field));
    return map;
  }, [catalog]);

  const fieldLabels = useMemo(() => {
    const map = new Map<string, string>();
    catalog?.fields.forEach((field) => map.set(field.key, field.label));
    return map;
  }, [catalog]);

  const conditions = useMemo(() => collectConditions(query.where), [query.where]);
  const isAggregate = query.grain === "aggregate";
  const groupBy = useMemo(() => query.group_by ?? [], [query.group_by]);
  const availableGroups = useMemo(() => groupOptions(catalog), [catalog]);

  // --- Zustandsaenderungen -------------------------------------------------
  //
  // Jede Aenderung an Filter, Sortierung oder Gruppierung verwirft den Cursor:
  // eine Folgeseite gehoert zu der Abfrage, aus der sie stammt - mit einem
  // anderen Filter waere sie eine willkuerliche Teilmenge.

  const applyCondition = useCallback(
    (field: string, condition: AnalyticsCondition | null) => {
      const base = { ...query, cursor: null };
      setQuery(condition ? upsertCondition(base, condition) : removeCondition(base, field));
    },
    [query, setQuery],
  );

  const applyGroupBy = useCallback(
    (dimensions: DimensionRef[]) => {
      setExpanded(new Set<string>());
      if (dimensions.length === 0) {
        setQuery({
          ...query,
          grain: "rows",
          group_by: [],
          measures: [],
          fields: rowFields,
          top_n: null,
          order_by: [],
          limit: 100,
          cursor: null,
        });
        return;
      }
      setQuery({
        ...query,
        grain: "aggregate",
        group_by: dimensions,
        measures: MEASURES,
        // `fields` gilt nur fuer Zeilenabfragen - der Server weist eine
        // Aggregatabfrage mit gesetzten Feldern ausdruecklich zurueck.
        fields: [],
        order_by: [],
        cursor: null,
        limit: null,
        // Top-N mit sichtbarem Rest ergibt nur bei EINER Achse Sinn: die
        // Restzeile fasst genau eine Dimension zusammen. Bei mehreren Achsen
        // wuerde sie eine Kombination behaupten, die es nicht gibt.
        top_n:
          dimensions.length === 1 ? { limit: 25, include_rest: true, rest_label: "Übrige" } : null,
      });
    },
    [query, rowFields, setQuery],
  );

  const toggleRowSort = useCallback(
    (key: string) => {
      const current = query.order_by?.[0];
      const next =
        current?.key === key
          ? current.direction === "desc"
            ? ({ key, direction: "asc" } as const)
            : null
          : ({ key, direction: "desc" } as const);
      setQuery({ ...query, order_by: next ? [next] : [], cursor: null });
    },
    [query, setQuery],
  );

  const setRowSort = useCallback(
    (key: string, direction: "asc" | "desc" | null) => {
      setQuery({
        ...query,
        order_by: direction ? [{ key, direction }] : [],
        cursor: null,
      });
    },
    [query, setQuery],
  );

  function toggleGroupDimension(fieldKey: string) {
    const existing = groupBy.findIndex((dimension) => dimension.field === fieldKey);
    if (existing >= 0) {
      applyGroupBy(groupBy.filter((_, index) => index !== existing));
      return;
    }
    const field = fieldByKey.get(fieldKey);
    const dimension: DimensionRef =
      field?.kind === "date" ? { field: fieldKey, granularity: "month" } : { field: fieldKey };
    applyGroupBy([...groupBy, dimension]);
  }

  function hideColumn(key: string) {
    const next = rowFields.filter((entry) => entry !== key);
    setStoredFields(next);
    setQuery({ ...query, fields: next, cursor: null });
  }

  function changeColumns(next: string[]) {
    setStoredFields(next);
    if (!isAggregate) {
      setQuery({ ...query, fields: next, cursor: null });
    }
  }

  async function handleExport() {
    const blob = await downloadAnalyticsCsv(
      accessToken as string,
      selectedHouseholdId as string,
      query,
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "auswertung.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  // --- Ableitungen fuer die Darstellung ------------------------------------

  const dimensionKeys = useMemo(
    () => groupBy.map((dimension) => dimensionKey(dimension)),
    [groupBy],
  );
  const measureColumns = useMemo(
    () =>
      (data?.columns ?? []).filter((column) =>
        MEASURES.some((measure) => `${measure.aggregate}_${measure.field}` === column.key),
      ),
    [data],
  );
  const tree = useMemo(
    () =>
      isAggregate && data
        ? buildGroupTree(
            data.rows,
            dimensionKeys,
            measureColumns.map((column) => column.key),
          )
        : [],
    [isAggregate, data, dimensionKeys, measureColumns],
  );

  const groupingEntries = useMemo(
    () =>
      groupBy.map((dimension) => {
        const key = dimensionKey(dimension);
        return {
          key,
          label:
            availableGroups.find((option) => option.key === key)?.label ??
            fieldLabels.get(dimension.field) ??
            dimension.field,
        };
      }),
    [groupBy, availableGroups, fieldLabels],
  );

  const chartResult = useMemo<AnalyticsResult | null>(() => {
    if (!isAggregate || !data || tree.length === 0) return null;
    // Das Diagramm zeigt immer die OBERSTE Ebene. Bei mehreren Achsen waeren
    // die flachen Kombinationszeilen des Servers nicht das, was die Tabelle
    // darueber zeigt - und zwei Darstellungen derselben Auswertung duerfen
    // sich nicht widersprechen.
    return {
      ...data,
      rows: tree.map((node) => ({
        __group__: node.path,
        __group___label: node.label,
        sum_amount: node.measures.sum_amount ?? 0,
      })),
      row_count: tree.length,
    };
  }, [isAggregate, data, tree]);

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Explorer</h1>
          <p className="placeholder-note">
            Alle Buchungen filtern, gruppieren und bis zur Einzelbuchung aufklappen.
          </p>
        </div>
        <div className="explorer-header-actions">
          {!isAggregate && (
            <ColumnPicker
              columns={(catalog?.fields ?? [])
                .filter((field) => field.key !== "split_id" && field.key !== "transaction_id")
                .map((field) => ({ key: field.key, label: field.label }))}
              visible={rowFields}
              onChange={changeColumns}
              onReset={() => changeColumns(defaultFields)}
            />
          )}
          <button type="button" className="btn-secondary" onClick={() => void handleExport()}>
            ↓ CSV
          </button>
        </div>
      </div>

      {/* ---------- Filterleiste ---------- */}
      <div className="card explorer-filters">
        <form
          className="explorer-search"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery({ ...query, search: searchDraft.trim() || null, cursor: null });
          }}
        >
          <input
            className="form-input"
            placeholder="Empfänger, Verwendungszweck oder Notiz durchsuchen…"
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
            aria-label="Volltextsuche"
          />
          <button type="submit" className="btn-secondary">
            Suchen
          </button>
        </form>

        <div className="explorer-controls">
          <label className="explorer-control">
            <span className="form-label">Zeitraum</span>
            <select
              className="form-input"
              value={periodKeyOf(query)}
              onChange={(event) => {
                const preset = PERIOD_PRESETS.find((p) => p.key === event.target.value);
                if (preset) setQuery({ ...query, period: preset.period, cursor: null });
              }}
            >
              {PERIOD_PRESETS.map((preset) => (
                <option key={preset.key} value={preset.key}>
                  {preset.label}
                </option>
              ))}
            </select>
          </label>

          <AddFilterControl
            fields={catalog?.fields ?? []}
            query={query}
            onConditionChange={(condition) => {
              if (condition) applyCondition(condition.field, condition);
            }}
          />

          <label className="explorer-toggle">
            <input
              type="checkbox"
              checked={query.include_transfers ?? false}
              onChange={(event) =>
                setQuery({ ...query, include_transfers: event.target.checked, cursor: null })
              }
            />
            Umbuchungen einbeziehen
          </label>

          <label className="explorer-toggle">
            <input
              type="checkbox"
              checked={query.only_on_budget ?? false}
              onChange={(event) =>
                setQuery({ ...query, only_on_budget: event.target.checked, cursor: null })
              }
            />
            Nur On-Budget-Konten
          </label>
        </div>

        <GroupingBar
          groups={groupingEntries}
          available={availableGroups.filter(
            (option) => !groupingEntries.some((entry) => entry.key === option.key),
          )}
          onAdd={(key) => applyGroupBy([...groupBy, parseDimensionKey(key)])}
          onRemove={(key) =>
            applyGroupBy(groupBy.filter((dimension) => dimensionKey(dimension) !== key))
          }
          onMove={(key, delta) => {
            const index = groupBy.findIndex((dimension) => dimensionKey(dimension) === key);
            const target = index + delta;
            if (index < 0 || target < 0 || target >= groupBy.length) return;
            const next = [...groupBy];
            const [entry] = next.splice(index, 1);
            if (entry) next.splice(target, 0, entry);
            applyGroupBy(next);
          }}
          onClear={() => applyGroupBy([])}
          note={
            groupingEntries.length > 0
              ? "Summe, Ø/Monat und Anzahl je Ebene; ein Klick auf die unterste Ebene zeigt die Buchungen."
              : undefined
          }
        />

        {(conditions.length > 0 || query.search) && (
          <div className="filter-chips">
            {query.search && (
              <button
                type="button"
                className="filter-chip"
                onClick={() => {
                  setSearchDraft("");
                  setQuery({ ...query, search: null, cursor: null });
                }}
              >
                Suche: „{query.search}“ <span aria-hidden>×</span>
              </button>
            )}
            {conditions.map((condition) => {
              const field = fieldByKey.get(condition.field);
              return (
                <button
                  key={condition.field}
                  type="button"
                  className="filter-chip"
                  onClick={() => applyCondition(condition.field, null)}
                >
                  {describeCondition(
                    condition,
                    field?.label ?? condition.field,
                    undefined,
                    (value) =>
                      field?.is_money && typeof value === "number"
                        ? formatMinorUnitsAsEuro(value)
                        : String(value),
                  )}{" "}
                  <span aria-hidden>×</span>
                </button>
              );
            })}
            <button
              type="button"
              className="grid-menu-link"
              onClick={() => {
                setSearchDraft("");
                setQuery({ ...query, where: null, search: null, cursor: null });
              }}
            >
              Alle Filter entfernen
            </button>
          </div>
        )}
      </div>

      {error && <div className="card status-error">Die Auswertung ist fehlgeschlagen.</div>}
      {isLoading && <p className="placeholder-note">Lade Auswertung…</p>}

      {data && (
        <>
          <ResultSummary result={data} busy={isFetching} />

          {isAggregate && chartResult && chartResult.rows.length > 0 && (
            <div className="card">
              <Chart
                option={barOption(chartResult, "__group__", "sum_amount", { horizontal: true })}
                height={Math.max(240, chartResult.rows.length * 26)}
                description={`Summe je ${groupingEntries[0]?.label ?? "Gruppe"} (oberste Ebene).`}
                onSegmentClick={(index) => {
                  const node = tree[index];
                  if (node?.row) setDrilldown(drilldownQuery(query, node.row));
                }}
                tableFallback={<TransactionTable result={data} onRowClick={() => undefined} />}
              />
            </div>
          )}

          {isAggregate ? (
            <div className="card">
              <div className="grid-toolbar">
                <button
                  type="button"
                  className="grid-menu-link"
                  onClick={() => setExpanded(new Set(allGroupPaths(tree)))}
                >
                  Alles aufklappen
                </button>
                <button
                  type="button"
                  className="grid-menu-link"
                  onClick={() => setExpanded(new Set<string>())}
                  disabled={expanded.size === 0}
                >
                  Alles zuklappen
                </button>
              </div>

              <GroupedResultTable
                tree={tree}
                result={data}
                query={query}
                dimensionLabels={groupingEntries.map((entry) => entry.label)}
                measureColumns={measureColumns}
                sort={treeSort}
                onSortChange={setTreeSort}
                expanded={expanded}
                onToggle={(path) =>
                  setExpanded((current) => {
                    const next = new Set(current);
                    if (next.has(path)) next.delete(path);
                    else next.add(path);
                    return next;
                  })
                }
              />
            </div>
          ) : (
            <div className="card">
              <TransactionTable
                result={data}
                visibleKeys={rowFields.length > 0 ? rowFields : undefined}
                renderHeader={(column) => {
                  const field = fieldByKey.get(column.key);
                  const gridColumn = field
                    ? gridColumnFromField(field)
                    : {
                        key: column.key,
                        label: column.label,
                        kind: column.kind,
                        isMoney: column.is_money,
                        sortable: false,
                      };
                  const sortEntry = query.order_by?.find((entry) => entry.key === column.key);
                  return (
                    <GridColumnHeader
                      key={column.key}
                      column={gridColumn}
                      className={column.kind === "num" ? "txn-th--amount" : ""}
                      sortDirection={sortEntry?.direction ?? null}
                      onToggleSort={
                        gridColumn.sortable ? () => toggleRowSort(column.key) : undefined
                      }
                      filtered={conditions.some((entry) => entry.field === column.key)}
                      grouped={groupBy.some((dimension) => dimension.field === column.key)}
                      menu={(close) => (
                        <ExplorerColumnMenu
                          column={gridColumn}
                          query={query}
                          onConditionChange={(condition) => applyCondition(column.key, condition)}
                          sortDirection={sortEntry?.direction ?? null}
                          onSortChange={
                            gridColumn.sortable
                              ? (direction) => setRowSort(column.key, direction)
                              : undefined
                          }
                          grouped={groupBy.some((dimension) => dimension.field === column.key)}
                          onToggleGroup={
                            field?.groupable ? () => toggleGroupDimension(column.key) : undefined
                          }
                          onHide={rowFields.length > 1 ? () => hideColumn(column.key) : undefined}
                          onClose={close}
                        />
                      )}
                    />
                  );
                }}
                onRowClick={(row) => {
                  if (typeof row.transaction_id === "string") {
                    setDetailTransactionId(row.transaction_id);
                  }
                }}
              />

              {(data.next_cursor || query.cursor) && (
                <div className="grid-toolbar">
                  {query.cursor && (
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => setQuery({ ...query, cursor: null })}
                    >
                      ↺ Erste Seite
                    </button>
                  )}
                  {data.next_cursor && (
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => setQuery({ ...query, cursor: data.next_cursor })}
                    >
                      Nächste Seite →
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {drilldown && (
        <TransactionDrilldown
          title="Buchungen zu diesem Segment"
          query={drilldown}
          onClose={() => setDrilldown(null)}
        />
      )}

      {detailTransactionId && (
        <Modal title="Buchung" onClose={() => setDetailTransactionId(null)}>
          <ProvenancePanel transactionId={detailTransactionId} />
        </Modal>
      )}
    </section>
  );
}

function ResultSummary({ result, busy }: { result: AnalyticsResult; busy: boolean }) {
  const total = result.total_row?.sum_amount;
  return (
    <div className="explorer-summary">
      <span>
        <strong>{result.row_count}</strong> {result.grain === "rows" ? "Buchungen" : "Gruppen"}
      </span>
      {typeof total === "number" && (
        <span>
          Summe: <strong>{formatMinorUnitsAsEuro(total)}</strong>
        </span>
      )}
      {result.period_from && result.period_to && (
        <span className="placeholder-note">
          {result.period_from} bis {result.period_to} ({result.months_covered} Monate)
        </span>
      )}
      {/* Jede greifende Grenze wird benannt - eine stillschweigend gekuerzte
          Auswertung liest sich wie eine vollstaendige. */}
      {(result.applied_limits ?? []).map((limit) => (
        <span key={limit.kind} className="status-badge status-badge--warning">
          {limit.kind === "top_n"
            ? `Top ${limit.limit}, Rest als „${limit.dropped_label}“ zusammengefasst`
            : `Auf ${limit.limit} Zeilen begrenzt`}
        </span>
      ))}
      {busy && <span className="placeholder-note">aktualisiert…</span>}
    </div>
  );
}
