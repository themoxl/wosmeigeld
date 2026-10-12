import {
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
  type VisibilityState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useAuth } from "../auth/AuthProvider";
import { useFormatDate } from "../hooks/useProfile";
import { useHousehold } from "../household/HouseholdProvider";
import {
  applyBulkCategory,
  createTransaction,
  deleteTransaction,
  linkTransfer,
  listAccounts,
  listCategories,
  listGoals,
  listTransactions,
  previewBulkCategory,
  setTransactionCategory,
  unlinkTransfer,
  updateTransaction,
} from "../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import {
  bulkReimbursementMarks,
  listReimbursementMarks,
  reimbursementMarksQueryKey,
  removeReimbursementMark,
  setReimbursementMark,
  type BulkMarkResult,
} from "../lib/reimbursementsApi";
import { describeCondition } from "../lib/analytics";
import { matchesCondition, NULL_VALUE_KEY } from "../lib/gridFilters";
import type {
  AnalyticsCondition,
  BulkCategoryInput,
  BulkCategoryResult,
  Transaction,
  TransactionStatus,
} from "../lib/types";
import { bulkItemLabel, MAX_BULK_TRANSACTIONS } from "../lib/categoryTypes";
import { CategoryPicker } from "../components/CategoryPicker";
import { ColumnFilterMenu } from "../components/datagrid/ColumnFilterMenu";
import { ColumnPicker } from "../components/datagrid/ColumnPicker";
import { GridColumnHeader } from "../components/datagrid/GridColumnHeader";
import { GroupingBar } from "../components/datagrid/GroupingBar";
import type { GridColumn, GridValueOption } from "../components/datagrid/types";
import { useCategoryCatalog } from "../hooks/useCategoryCatalog";
import { useStoredState } from "../hooks/useStoredState";
import { SplitEditorModal } from "./transactions/SplitEditorModal";
import { EditTransactionModal } from "./transactions/EditTransactionModal";

type Direction = "expense" | "income";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

const STATUS_PILL: Record<TransactionStatus, { label: string; modifier: string }> = {
  pending: { label: "Vorgemerkt", modifier: "status-badge--highlight" },
  booked: { label: "Gebucht", modifier: "status-badge--neutral" },
  reconciled: { label: "Abgestimmt", modifier: "status-badge--positive" },
};

// Die fachlichen Spalten des Registers - Sortierung, Filter und Gruppierung
// beziehen sich ausschliesslich auf diese. Auswahlkaestchen und Aktionen sind
// Bedienelemente, keine Daten: sie lassen sich weder filtern noch ausblenden.
//
// Dieselbe Beschreibungsform wie der Feldkatalog des Explorers, damit beide
// Tabellen dasselbe Spaltenmenue benutzen koennen.
const REGISTER_COLUMNS: GridColumn[] = [
  { key: "date", label: "Datum", kind: "date", sortable: true, groupable: true },
  { key: "account", label: "Konto", kind: "cat", sortable: true, groupable: true, hasValues: true },
  {
    key: "payeeNote",
    label: "Zahlungsempfänger / Notiz",
    kind: "cat",
    sortable: true,
    groupable: true,
    hasValues: true,
  },
  {
    key: "category",
    label: "Kategorie",
    kind: "cat",
    sortable: true,
    groupable: true,
    hasValues: true,
  },
  { key: "amount", label: "Betrag", kind: "num", isMoney: true, sortable: true, groupable: false },
  { key: "status", label: "Status", kind: "cat", sortable: true, groupable: true, hasValues: true },
  {
    key: "transfer",
    label: "Transfer",
    kind: "bool",
    sortable: true,
    groupable: true,
    hasValues: true,
  },
  {
    key: "reimbursement",
    label: "Erstattung",
    kind: "cat",
    sortable: true,
    groupable: true,
    hasValues: true,
  },
];

const COLUMN_BY_KEY = new Map(REGISTER_COLUMNS.map((column) => [column.key, column]));
const DATA_COLUMN_IDS = REGISTER_COLUMNS.map((column) => column.key);

const COLUMNS_STORAGE_KEY = "transactions.columns";
const COLUMNS_SEEN_STORAGE_KEY = "transactions.columns.seen";
// Spalten, die es vor Einfuehrung dieses Merkers schon gab. Fuer sie gilt:
// steht der Schluessel nicht im gespeicherten Stand, hat der Nutzer die
// Spalte ausgeblendet. Nur fuer SPAETER hinzugekommene Schluessel bedeutet
// dasselbe "gab es damals noch nicht" - und nur die duerfen nachwachsen.
const LEGACY_COLUMN_IDS = [
  "date",
  "account",
  "payeeNote",
  "category",
  "amount",
  "status",
  "transfer",
];
const ROW_LIMIT_OPTIONS = [100, 200, 500];

type EditingCell = { id: string; column: "note" | "category" };
type FocusedCell = { rowIndex: number; columnId: string };
type SortState = { key: string; direction: "asc" | "desc" } | null;

/** Gruppenknoten des Registers - dieselbe Idee wie im Explorer, aber lokal. */
interface RegisterGroup {
  path: string;
  depth: number;
  columnKey: string;
  label: string;
  sum: number;
  count: number;
  children: RegisterGroup[];
  transactions: Transaction[];
}

type DisplayRow =
  { kind: "group"; group: RegisterGroup } | { kind: "transaction"; transaction: Transaction };

// Abschnitt 10.3 des Architekturplans (Buchungsregister): dichte, virtualisierte
// Tabelle mit Inline-Bearbeitung (Kategorie/Notiz), Mehrfachauswahl +
// Bulk-Kategorisierung, Splits-Editor, Transferverknuepfung, Loeschen und einer
// Pfeiltasten-/Enter-/Escape-Tastatursteuerung.
//
// Neu gegenueber Phase 2 (docs/ANALYSE-EXPLORER-PLAN.md, "Noch offen":
// die fehlenden Filter im bestehenden Buchungsregister): serverseitige
// Volltextsuche, Zeitraum, Spaltenfilter mit vollem Operatorvorrat,
// Sortierung per Spaltenklick, Gruppierung mit Zwischensummen und Spaltenwahl.
//
// Die Arbeitsteilung mit dem Explorer bleibt bestehen und wird sichtbar
// benannt: das Register laedt eine begrenzte Zahl von Buchungen zur Erfassung
// und Korrektur, der Explorer wertet den gesamten Bestand aus.
//
// Transfer-Unlink-Einschraenkung: DELETE /v1/transfers/link/{id} braucht die
// transfer_link-ID, die weder auf der Transaction noch der TransactionDetail
// mitgeliefert wird. Pragmatische Loesung: die ID wird beim Erzeugen der
// Verknuepfung lokal in einer Map gemerkt. "Verknuepfung aufheben" ist daher
// nur innerhalb derselben Browser-Sitzung ab dem Moment der Verknuepfung
// verfuegbar; nach einem Reload zeigt eine bereits verknuepfte Buchung nur
// noch das Transfer-Badge ohne Aufheben-Button.
export function TransactionsPage() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();

  const [accountFilter, setAccountFilter] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [rowLimit, setRowLimit] = useState(200);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [editingCell, setEditingCell] = useState<EditingCell | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [focusedCell, setFocusedCell] = useState<FocusedCell | null>(null);
  const [splitEditorId, setSplitEditorId] = useState<string | null>(null);
  const [editTransactionId, setEditTransactionId] = useState<string | null>(null);
  const [transferLinkCache, setTransferLinkCache] = useState<Map<string, string>>(new Map());
  const [bulkCategoryValue, setBulkCategoryValue] = useState("");

  // Ansichtszustand der Tabelle.
  const [columnFilters, setColumnFilters] = useState<Record<string, AnalyticsCondition>>({});
  const [sort, setSort] = useState<SortState>(null);
  const [groupBy, setGroupBy] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set<string>());
  // Sichtbare Datenspalten in Anzeigereihenfolge. Sichtbarkeit UND Reihenfolge
  // in einer Liste: eine ausgeblendete Spalte hat keine Position, und eine
  // getrennt gefuehrte Reihenfolge waere die zweite Wahrheit darueber.
  const [storedLayout, setStoredLayout] = useStoredState<string[]>(
    COLUMNS_STORAGE_KEY,
    DATA_COLUMN_IDS,
  );
  // Welche Spalten der Nutzer schon zu Gesicht bekommen hat. Ohne diesen
  // Merker liessen sich "neu hinzugekommen" und "bewusst ausgeblendet" nicht
  // unterscheiden - beide sehen im gespeicherten Stand gleich aus.
  const [seenColumns, setSeenColumns] = useStoredState<string[]>(
    COLUMNS_SEEN_STORAGE_KEY,
    LEGACY_COLUMN_IDS,
  );

  /** Layout speichern und dabei festhalten, dass alle heutigen Spalten
   *  bekannt sind - sonst waechst eine gerade ausgeblendete wieder nach. */
  const persistLayout = (next: string[]) => {
    setStoredLayout(next.length > 0 ? next : DATA_COLUMN_IDS);
    setSeenColumns(DATA_COLUMN_IDS);
  };

  // Vorschau und Ergebnis tragen die Signatur (Zielkategorie + Auswahl), fuer
  // die sie berechnet wurden. Aendert sich die Auswahl, gilt die Vorschau
  // automatisch als veraltet - ohne setState in einem Effect (die
  // React-Compiler-Regeln verbieten das hier).
  const [bulkPreview, setBulkPreview] = useState<{
    signature: string;
    result: BulkCategoryResult;
  } | null>(null);
  // Eigene Signatur und eigene Zustaende: die Kategorie-Vorschau wuerde die
  // Marken-Vorschau sonst verwerfen und umgekehrt - beide haengen an
  // derselben Auswahl, meinen aber verschiedene Aktionen.
  const [markBulkState, setMarkBulkState] = useState<"expected" | "excluded">("expected");
  const [markBulkOutcome, setMarkBulkOutcome] = useState<{
    signature: string;
    kind: "Vorschau" | "Ergebnis";
    result: BulkMarkResult;
  } | null>(null);

  const [bulkResult, setBulkResult] = useState<{
    signature: string;
    result: BulkCategoryResult;
  } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const skipNextBlurCommit = useRef(false);
  const cellRefs = useRef(new Map<string, HTMLTableCellElement>());
  const tableScrollRef = useRef<HTMLDivElement>(null);

  const accountsQuery = useQuery({
    queryKey: ["accounts", selectedHouseholdId],
    queryFn: () => listAccounts(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const categoriesQuery = useQuery({
    queryKey: ["categories", selectedHouseholdId],
    queryFn: () => listCategories(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const goalsQuery = useQuery({
    queryKey: ["goals", selectedHouseholdId],
    queryFn: () => listGoals(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  // Erstattungsmarken kommen als eigener Abruf und werden hier je Buchung
  // zusammengefuehrt, statt an der Buchungsabfrage mitzuhaengen. Das haelt die
  // Aenderung auf die Erstattungsdomaene begrenzt - die Buchungsliste hat zwei
  // fast gleiche SELECT-Listen (Web und Mobile), die sonst beide haetten
  // wachsen muessen und erfahrungsgemaess auseinanderlaufen.
  const marksQuery = useQuery({
    queryKey: reimbursementMarksQueryKey(selectedHouseholdId as string, false),
    queryFn: () =>
      listReimbursementMarks(accessToken as string, selectedHouseholdId as string, {
        onlyOpen: false,
        limit: 500,
      }),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const marksByTransaction = useMemo(
    () => new Map((marksQuery.data ?? []).map((mark) => [mark.transaction_id, mark])),
    [marksQuery.data],
  );

  // Suche, Zeitraum und Kontofilter laufen SERVERSEITIG. Eine reine
  // Browsersuche haette nur die geladene Seite durchsucht und "nicht gefunden"
  // gemeldet, obwohl die Buchung existiert - genau der Fehler, der eine
  // Suchfunktion wertlos macht.
  const transactionsQuery = useQuery({
    queryKey: [
      "transactions",
      selectedHouseholdId,
      accountFilter,
      search,
      fromDate,
      toDate,
      rowLimit,
    ],
    queryFn: () =>
      listTransactions(accessToken as string, selectedHouseholdId as string, {
        limit: rowLimit,
        accountId: accountFilter || undefined,
        search: search || undefined,
        fromDate: fromDate || undefined,
        toDate: toDate || undefined,
      }),
    enabled: Boolean(accessToken && selectedHouseholdId),
    placeholderData: (previous) => previous,
  });

  const accountsById = useMemo(() => {
    const map = new Map<string, string>();
    accountsQuery.data?.forEach((account) => map.set(account.id, account.name));
    return map;
  }, [accountsQuery.data]);

  // Namensaufloesung darf NICHT nur den aktiven Picker-Baum verwenden: eine
  // archivierte Kategorie bleibt in der Historie sichtbar und wird mit Badge
  // ausgewiesen (docs/KATEGORISIERUNGSPLAN.md, "Historie, Budgets und
  // Berichte"). Der Katalog enthaelt deshalb auch archivierte Eintraege.
  const catalogQuery = useCategoryCatalog({ includeArchived: true });

  const categoriesById = useMemo(() => {
    const map = new Map<string, { name: string; isArchived: boolean }>();
    categoriesQuery.data?.forEach((group) => {
      group.categories.forEach((category) =>
        map.set(category.id, { name: category.name, isArchived: false }),
      );
    });
    // Defensiv: solange der Katalog-Endpunkt fehlt oder etwas Unerwartetes
    // liefert, bleibt der aktive Baum die Quelle - die Tabelle darf daran
    // nicht scheitern.
    catalogQuery.data?.groups?.forEach((group) => {
      group.categories.forEach((category) => {
        map.set(category.id, { name: category.name, isArchived: category.is_archived });
      });
    });
    return map;
  }, [categoriesQuery.data, catalogQuery.data]);

  const loadedTransactions = useMemo(() => {
    if (!transactionsQuery.data) {
      return [];
    }
    return [...transactionsQuery.data].sort((a, b) => b.booked_date.localeCompare(a.booked_date));
  }, [transactionsQuery.data]);

  const transactionsById = useMemo(() => {
    const map = new Map<string, Transaction>();
    loadedTransactions.forEach((tx) => map.set(tx.id, tx));
    return map;
  }, [loadedTransactions]);

  // ---------------------------------------------------------------------
  // Spaltenwerte: die eine Stelle, an der eine Spalte auf einen
  // vergleichbaren Wert abgebildet wird. Filter, Sortierung, Gruppierung und
  // die Werteliste des Menues lesen alle hier - sonst koennte "Kategorie"
  // beim Filtern etwas anderes bedeuten als beim Gruppieren.
  // ---------------------------------------------------------------------

  const cellValue = useMemo(() => {
    return (tx: Transaction, columnKey: string): string | number | boolean | null => {
      switch (columnKey) {
        case "date":
          return tx.booked_date;
        case "account":
          return accountsById.get(tx.account_id) ?? null;
        case "payeeNote":
          return [tx.payee_label, tx.note].filter(Boolean).join(" ") || null;
        case "category":
          if (tx.is_split) return "Split";
          return tx.category_id ? (categoriesById.get(tx.category_id)?.name ?? null) : null;
        case "amount":
          return tx.amount_minor;
        case "status":
          return STATUS_PILL[tx.status as TransactionStatus]?.label ?? tx.status;
        case "transfer":
          return tx.is_transfer;
        case "reimbursement": {
          const mark = marksByTransaction.get(tx.id);
          if (!mark) return null;
          if (mark.state === "excluded") return "Ausgenommen";
          if (mark.converted_at) return "Vorgang angelegt";
          if (mark.expected_minor === null) return "Erwartet, Höhe offen";
          return formatMinorUnitsAsEuro(mark.expected_minor, tx.currency);
        }
        default:
          return null;
      }
    };
  }, [accountsById, categoriesById, marksByTransaction]);

  /** Gruppierungswert einer Spalte - bewusst groeber als der Zellwert. */
  const groupValue = useMemo(() => {
    return (tx: Transaction, columnKey: string): string => {
      switch (columnKey) {
        // Nach Tag zu gruppieren erzeugt fast so viele Gruppen wie Zeilen;
        // der Monat ist die Ebene, nach der im Register tatsaechlich gefragt
        // wird.
        case "date":
          return tx.booked_date.slice(0, 7);
        case "payeeNote":
          return tx.payee_label || "Ohne Empfänger";
        case "transfer":
          return tx.is_transfer ? "Umbuchung" : "Keine Umbuchung";
        default: {
          const value = cellValue(tx, columnKey);
          if (value === null || value === undefined || value === "") return "Ohne Zuordnung";
          return String(value);
        }
      }
    };
  }, [cellValue]);

  const activeFilters = useMemo(() => Object.values(columnFilters), [columnFilters]);

  /** Wendet alle Spaltenfilter an - optional ohne den einer Spalte. */
  const applyColumnFilters = useMemo(() => {
    return (rows: Transaction[], exceptKey?: string) =>
      rows.filter((tx) =>
        activeFilters.every(
          (condition) =>
            condition.field === exceptKey ||
            matchesCondition(condition, cellValue(tx, condition.field)),
        ),
      );
  }, [activeFilters, cellValue]);

  const filteredTransactions = useMemo(
    () => applyColumnFilters(loadedTransactions),
    [applyColumnFilters, loadedTransactions],
  );

  const visibleTransactions = useMemo(() => {
    if (!sort) return filteredTransactions;
    const factor = sort.direction === "asc" ? 1 : -1;
    return [...filteredTransactions].sort((a, b) => {
      const left = cellValue(a, sort.key);
      const right = cellValue(b, sort.key);
      // Leere Werte immer ans Ende, unabhaengig von der Richtung - eine
      // Sortierung, die die Luecken nach vorn holt, verdeckt die Daten.
      if (left === null && right === null) return 0;
      if (left === null) return 1;
      if (right === null) return -1;
      if (typeof left === "number" && typeof right === "number") {
        return (left - right) * factor;
      }
      if (typeof left === "boolean" && typeof right === "boolean") {
        return (Number(left) - Number(right)) * factor;
      }
      return String(left).localeCompare(String(right), "de") * factor;
    });
  }, [filteredTransactions, sort, cellValue]);

  /** Werteliste je Spalte, kontextabhaengig wie im Explorer. */
  const columnValues = useMemo(() => {
    const map = new Map<string, GridValueOption[]>();
    for (const column of REGISTER_COLUMNS) {
      if (!column.hasValues) continue;
      const counts = new Map<string, number>();
      for (const tx of applyColumnFilters(loadedTransactions, column.key)) {
        const raw = cellValue(tx, column.key);
        const key = raw === null || raw === "" ? NULL_VALUE_KEY : String(raw);
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
      map.set(
        column.key,
        [...counts.entries()]
          .map(([key, count]) => ({
            value: key === NULL_VALUE_KEY ? null : key,
            label:
              key === NULL_VALUE_KEY
                ? "(ohne Angabe)"
                : // Ein Merkmal liest sich als "ja"/"nein", nicht als
                  // "true"/"false" - der gefilterte WERT bleibt davon
                  // unberuehrt.
                  column.kind === "bool"
                  ? key === "true"
                    ? "ja"
                    : "nein"
                  : key,
            count,
          }))
          .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "de")),
      );
    }
    return map;
  }, [applyColumnFilters, loadedTransactions, cellValue]);

  const invalidateTransactions = () => {
    void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
  };

  // ---------------------------------------------------------------------
  // Inline-Bearbeitung: Notiz (Text) und Kategorie (Dropdown, nur bei
  // Nicht-Split-Buchungen - Splits oeffnen stattdessen den Split-Editor).
  // ---------------------------------------------------------------------

  const invalidateMarks = () =>
    queryClient.invalidateQueries({
      queryKey: reimbursementMarksQueryKey(selectedHouseholdId as string, false),
    });

  const markMutation = useMutation({
    mutationFn: (vars: { transactionId: string; state: "expected" | "excluded" }) =>
      setReimbursementMark(
        accessToken as string,
        selectedHouseholdId as string,
        vars.transactionId,
        {
          state: vars.state,
          expectation_mode: "full",
        },
      ),
    onSuccess: () => void invalidateMarks(),
    onError: (error: unknown) => {
      setActionError(
        error instanceof Error ? error.message : "Markierung konnte nicht gesetzt werden.",
      );
    },
  });

  // Ausdruecklich getrennt vom Ausnehmen: das Entfernen stellt die Buchung
  // neutral, danach darf eine Regel wieder greifen. "Ausgenommen" bleibt
  // dagegen bestehen. Ein gemeinsamer Knopf fuer beides waere genau die
  // Verwechslung, an der solche Funktionen scheitern.
  const removeMarkMutation = useMutation({
    mutationFn: (transactionId: string) =>
      removeReimbursementMark(accessToken as string, selectedHouseholdId as string, transactionId),
    onSuccess: () => void invalidateMarks(),
    onError: (error: unknown) => {
      setActionError(
        error instanceof Error ? error.message : "Markierung konnte nicht entfernt werden.",
      );
    },
  });

  const updateNoteMutation = useMutation({
    mutationFn: (vars: { id: string; note: string | null }) =>
      updateTransaction(accessToken as string, selectedHouseholdId as string, vars.id, {
        note: vars.note,
      }),
    onSuccess: () => invalidateTransactions(),
    onError: (error: unknown) => {
      setActionError(
        error instanceof Error ? error.message : "Notiz konnte nicht gespeichert werden.",
      );
    },
  });

  // Einzelzuordnung ueber den dafuer vorgesehenen Endpunkt: er aendert genau
  // einen Split und lehnt Mehrfachsplits ab, statt eine vorhandene Aufteilung
  // ueber PUT .../splits still zu ueberschreiben.
  const commitCategoryMutation = useMutation({
    mutationFn: (vars: { tx: Transaction; categoryId: string }) =>
      setTransactionCategory(accessToken as string, selectedHouseholdId as string, vars.tx.id, {
        category_id: vars.categoryId || null,
        expected_version: vars.tx.version,
      }),
    onSuccess: () => invalidateTransactions(),
    onError: (error: unknown) => {
      setActionError(
        error instanceof Error ? error.message : "Kategorie konnte nicht gespeichert werden.",
      );
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      deleteTransaction(accessToken as string, selectedHouseholdId as string, id),
    onSuccess: (_void, id) => {
      setRowSelection((current) => {
        if (!(id in current)) {
          return current;
        }
        const next = { ...current };
        delete next[id];
        return next;
      });
      invalidateTransactions();
    },
    onError: (error: unknown) => {
      setActionError(
        error instanceof Error ? error.message : "Buchung konnte nicht gelöscht werden.",
      );
    },
  });

  // Mehrfachzuordnung laeuft ueber Vorschau -> Anwenden: die Vorschau zeigt
  // pro Buchung, was passieren wuerde ("wird geaendert", "bereits so
  // zugeordnet", "Split-Buchung", "Versionskonflikt", "archiviert"), und ein
  // einzelner Konflikt blockiert die uebrigen gueltigen Aenderungen nicht.
  const bulkPreviewMutation = useMutation({
    mutationFn: (vars: { signature: string; input: BulkCategoryInput }) =>
      previewBulkCategory(accessToken as string, selectedHouseholdId as string, vars.input),
    onSuccess: (result, vars) => {
      setBulkPreview({ signature: vars.signature, result });
      setBulkResult(null);
      setActionError(null);
    },
    onError: (error: unknown) => {
      setBulkPreview(null);
      setActionError(
        error instanceof Error ? error.message : "Vorschau konnte nicht berechnet werden.",
      );
    },
  });

  const bulkApplyMutation = useMutation({
    mutationFn: (vars: { signature: string; input: BulkCategoryInput }) =>
      applyBulkCategory(accessToken as string, selectedHouseholdId as string, vars.input),
    onSuccess: (result, vars) => {
      setBulkResult({ signature: vars.signature, result });
      setBulkPreview(null);
      setActionError(null);
      invalidateTransactions();
    },
    onError: (error: unknown) => {
      setBulkResult(null);
      setActionError(
        error instanceof Error ? error.message : "Mehrfachzuordnung ist fehlgeschlagen.",
      );
    },
  });

  const linkTransferMutation = useMutation({
    mutationFn: (input: { outgoing_transaction_id: string; incoming_transaction_id: string }) =>
      linkTransfer(accessToken as string, selectedHouseholdId as string, input),
    onSuccess: (link) => {
      setTransferLinkCache((current) => {
        const next = new Map(current);
        next.set(link.outgoing_transaction_id, link.id);
        next.set(link.incoming_transaction_id, link.id);
        return next;
      });
      setRowSelection({});
      setActionError(null);
      invalidateTransactions();
    },
    onError: (error: unknown) => {
      setActionError(
        error instanceof Error ? error.message : "Transfer konnte nicht verknüpft werden.",
      );
    },
  });

  const unlinkTransferMutation = useMutation({
    mutationFn: (linkId: string) =>
      unlinkTransfer(accessToken as string, selectedHouseholdId as string, linkId),
    onSuccess: (_void, linkId) => {
      setTransferLinkCache((current) => {
        const next = new Map(current);
        for (const [txId, id] of next) {
          if (id === linkId) {
            next.delete(txId);
          }
        }
        return next;
      });
      invalidateTransactions();
    },
    onError: (error: unknown) => {
      setActionError(
        error instanceof Error ? error.message : "Verknüpfung konnte nicht aufgehoben werden.",
      );
    },
  });

  function beginEditNote(tx: Transaction) {
    setNoteDraft(tx.note ?? "");
    setEditingCell({ id: tx.id, column: "note" });
  }

  function commitNote(tx: Transaction) {
    const trimmed = noteDraft.trim();
    setEditingCell(null);
    if (trimmed === (tx.note ?? "")) {
      return;
    }
    updateNoteMutation.mutate({ id: tx.id, note: trimmed || null });
  }

  function cancelInlineEdit() {
    skipNextBlurCommit.current = true;
    setEditingCell(null);
  }

  function handleDelete(tx: Transaction) {
    const label = `${formatDate(tx.booked_date)} über ${formatMinorUnitsAsEuro(tx.amount_minor, tx.currency)}`;
    if (window.confirm(`Buchung vom ${label} wirklich löschen?`)) {
      deleteMutation.mutate(tx.id);
    }
  }

  // ---------------------------------------------------------------------
  // Mehrfachauswahl / Bulk-Aktionen
  // ---------------------------------------------------------------------

  const selectedIds = useMemo(
    () => Object.keys(rowSelection).filter((id) => rowSelection[id]),
    [rowSelection],
  );
  const selectedTransactions = useMemo(
    () =>
      selectedIds
        .map((id) => transactionsById.get(id))
        .filter((tx): tx is Transaction => Boolean(tx)),
    [selectedIds, transactionsById],
  );

  const transferCandidates = useMemo<[Transaction, Transaction] | null>(() => {
    if (selectedTransactions.length !== 2) {
      return null;
    }
    const [first, second] = selectedTransactions;
    if (!first || !second) {
      return null;
    }
    if (first.account_id === second.account_id || first.is_transfer || second.is_transfer) {
      return null;
    }
    return [first, second];
  }, [selectedTransactions]);

  function handleLinkTransfer() {
    if (!transferCandidates) {
      return;
    }
    const [a, b] = transferCandidates;
    const outgoing = a.amount_minor < 0 ? a : b.amount_minor < 0 ? b : a;
    const incoming = outgoing.id === a.id ? b : a;
    linkTransferMutation.mutate({
      outgoing_transaction_id: outgoing.id,
      incoming_transaction_id: incoming.id,
    });
  }

  // Signatur aus Zielkategorie + Auswahl: nur solange sie unveraendert ist,
  // gehoert eine Vorschau noch zu dem, was tatsaechlich angewendet wuerde.
  const bulkSignature = `${bulkCategoryValue}|${selectedIds.join(",")}`;
  const bulkOverLimit = selectedIds.length > MAX_BULK_TRANSACTIONS;
  const currentBulkPreview =
    bulkPreview && bulkPreview.signature === bulkSignature ? bulkPreview.result : null;
  const currentBulkResult =
    bulkResult && bulkResult.signature === bulkSignature ? bulkResult.result : null;

  const markBulkSignature = `${markBulkState}|${selectedIds.join(",")}`;
  const currentMarkOutcome =
    markBulkOutcome && markBulkOutcome.signature === markBulkSignature ? markBulkOutcome : null;
  // Nur Abgaenge - eine Erstattungserwartung setzt voraus, dass etwas bezahlt
  // wurde. Der Server weist Eingaenge ohnehin ab, aber die Zahl in der Leiste
  // soll vorher stimmen.
  const markableIds = useMemo(
    () => selectedTransactions.filter((tx) => tx.amount_minor < 0).map((tx) => tx.id),
    [selectedTransactions],
  );

  const markBulkMutation = useMutation({
    mutationFn: (vars: { signature: string; mode: "preview" | "apply" }) =>
      bulkReimbursementMarks(
        accessToken as string,
        selectedHouseholdId as string,
        markableIds,
        { state: markBulkState, expectation_mode: "full" },
        vars.mode,
      ).then((result) => ({ ...vars, result })),
    onSuccess: ({ signature, mode, result }) => {
      setMarkBulkOutcome({
        signature,
        kind: mode === "preview" ? "Vorschau" : "Ergebnis",
        result,
      });
      if (mode === "apply") void invalidateMarks();
    },
    onError: (error: unknown) => {
      setActionError(
        error instanceof Error ? error.message : "Markierungen konnten nicht gesetzt werden.",
      );
    },
  });

  function buildBulkInput(): BulkCategoryInput {
    return {
      transaction_ids: selectedIds,
      category_id: bulkCategoryValue || null,
      expected_versions: Object.fromEntries(selectedTransactions.map((tx) => [tx.id, tx.version])),
    };
  }

  function handleBulkPreview() {
    if (selectedIds.length === 0 || bulkOverLimit) {
      return;
    }
    bulkPreviewMutation.mutate({ signature: bulkSignature, input: buildBulkInput() });
  }

  function handleBulkApply() {
    if (selectedIds.length === 0 || bulkOverLimit || !currentBulkPreview) {
      return;
    }
    bulkApplyMutation.mutate({ signature: bulkSignature, input: buildBulkInput() });
  }

  function describeTransaction(transactionId: string): string {
    const tx = transactionsById.get(transactionId);
    if (!tx) {
      return transactionId;
    }
    return `${formatDate(tx.booked_date)} · ${tx.payee_label || tx.note || "ohne Empfänger"} · ${formatMinorUnitsAsEuro(tx.amount_minor, tx.currency)}`;
  }

  function renderBulkOutcome(title: string, result: BulkCategoryResult) {
    return (
      <div className="bulk-outcome" role="status">
        <p className="bulk-outcome-summary">
          {title}: {result.applied} wird geändert / geändert, {result.unchanged} bereits so
          zugeordnet, {result.skipped} übersprungen, {result.conflict} Versionskonflikt.
        </p>
        <ul className="bulk-outcome-list">
          {result.items.map((item) => (
            <li
              key={item.transaction_id}
              className={`bulk-outcome-item bulk-outcome-item--${item.status}`}
            >
              <span className="bulk-outcome-item-label">
                {describeTransaction(item.transaction_id)}
              </span>
              <span className="bulk-outcome-item-status">{bulkItemLabel(item)}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  // ---------------------------------------------------------------------
  // Spalten (neu erzeugt bei jedem Render, damit Zell-Renderer immer den
  // aktuellen Editier-/Auswahlzustand sehen - bei reinen Anzeige-Spalten
  // ohne eigenen Column-State ist das mit @tanstack/react-table unkritisch).
  // ---------------------------------------------------------------------

  const columns: ColumnDef<Transaction>[] = [
    {
      id: "select",
      header: ({ table: t }) => (
        <input
          type="checkbox"
          // Der Geltungsbereich wird ausdruecklich benannt: ausgewaehlt wird
          // genau das, was die aktuelle Filterung sichtbar macht - niemals
          // still zusaetzliche Treffer.
          aria-label={`Alle ${t.getRowModel().rows.length} sichtbaren auswählen`}
          title={`Alle ${t.getRowModel().rows.length} sichtbaren auswählen`}
          checked={t.getIsAllRowsSelected()}
          ref={(el) => {
            if (el) {
              el.indeterminate = !t.getIsAllRowsSelected() && t.getIsSomeRowsSelected();
            }
          }}
          onChange={t.getToggleAllRowsSelectedHandler()}
        />
      ),
      cell: ({ row }) => (
        <input
          type="checkbox"
          aria-label="Buchung auswählen"
          checked={row.getIsSelected()}
          onChange={row.getToggleSelectedHandler()}
        />
      ),
    },
    {
      id: "date",
      header: "Datum",
      cell: ({ row }) => (
        <span className="transaction-date">{formatDate(row.original.booked_date)}</span>
      ),
    },
    {
      id: "account",
      header: "Konto",
      cell: ({ row }) => accountsById.get(row.original.account_id) ?? "—",
    },
    {
      id: "payeeNote",
      header: "Zahlungsempfänger / Notiz",
      cell: ({ row }) => {
        const tx = row.original;
        const isEditing = editingCell?.id === tx.id && editingCell.column === "note";
        if (isEditing) {
          return (
            <input
              autoFocus
              className="cell-input"
              value={noteDraft}
              onChange={(event) => setNoteDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  commitNote(tx);
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  cancelInlineEdit();
                }
              }}
              onBlur={() => {
                if (skipNextBlurCommit.current) {
                  skipNextBlurCommit.current = false;
                  return;
                }
                commitNote(tx);
              }}
            />
          );
        }
        return (
          <button
            type="button"
            className="cell-button cell-button--note"
            onClick={() => beginEditNote(tx)}
          >
            {tx.payee_label && <span className="txn-payee">{tx.payee_label}</span>}
            <span className="txn-note">
              {tx.note || (tx.payee_label ? "" : "– Notiz hinzufügen –")}
            </span>
          </button>
        );
      },
    },
    {
      id: "category",
      header: "Kategorie",
      cell: ({ row }) => {
        const tx = row.original;
        if (tx.is_split) {
          return (
            <button
              type="button"
              className="cell-button"
              onClick={() => setSplitEditorId(tx.id)}
              title="Splits bearbeiten"
            >
              <span className="status-badge status-badge--highlight">Split</span>
            </button>
          );
        }
        const assigned = tx.category_id ? categoriesById.get(tx.category_id) : undefined;
        const isEditing = editingCell?.id === tx.id && editingCell.column === "category";
        if (isEditing) {
          return (
            <CategoryPicker
              autoFocus
              categoryGroups={categoriesQuery.data}
              value={tx.category_id ?? ""}
              className="cell-picker"
              aria-label="Kategorie dieser Buchung"
              amountMinor={tx.amount_minor}
              archivedSelectionLabel={assigned?.isArchived ? assigned.name : null}
              onChange={(value) => {
                setEditingCell(null);
                commitCategoryMutation.mutate({ tx, categoryId: value });
              }}
              onDismiss={() => setEditingCell(null)}
            />
          );
        }
        return (
          <button
            type="button"
            className="cell-button"
            onClick={() => setEditingCell({ id: tx.id, column: "category" })}
          >
            {assigned ? assigned.name : "– Kategorie wählen –"}
            {assigned?.isArchived && (
              <span className="status-badge status-badge--warning">Archiviert</span>
            )}
          </button>
        );
      },
    },
    {
      id: "amount",
      header: "Betrag",
      cell: ({ row }) => (
        <span
          className={
            row.original.amount_minor < 0
              ? "transaction-amount transaction-amount--negative"
              : "transaction-amount transaction-amount--positive"
          }
        >
          {formatMinorUnitsAsEuro(row.original.amount_minor, row.original.currency)}
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: ({ row }) => {
        // status ist backendseitig plain string, in der DB aber ein
        // Postgres-Enum (transaction_status) - der Wert ist also immer
        // einer der drei bekannten Faelle (supabase/migrations/0001_schema.sql).
        const pill = STATUS_PILL[row.original.status as TransactionStatus];
        return <span className={`status-badge ${pill.modifier}`}>{pill.label}</span>;
      },
    },
    {
      id: "transfer",
      header: "Transfer",
      cell: ({ row }) => {
        const tx = row.original;
        if (!tx.is_transfer) {
          return null;
        }
        const linkId = transferLinkCache.get(tx.id);
        return (
          <span className="transfer-cell">
            <span
              className="status-badge status-badge--neutral"
              title="Diese Buchung ist Teil eines verknüpften Transfers"
            >
              ⇄ Transfer
            </span>
            {linkId && (
              <button
                type="button"
                className="icon-btn"
                onClick={() => unlinkTransferMutation.mutate(linkId)}
                title="Verknüpfung aufheben"
              >
                Aufheben
              </button>
            )}
          </span>
        );
      },
    },
    {
      id: "reimbursement",
      header: "Erstattung",
      cell: ({ row }) => {
        const mark = marksByTransaction.get(row.original.id);
        if (!mark) return null;
        if (mark.state === "excluded") {
          return (
            <span
              className="status-badge status-badge--neutral"
              title="Von der Erstattung ausgenommen - Regeln fassen diese Buchung nicht mehr an"
            >
              Ausgenommen
            </span>
          );
        }
        if (mark.converted_at) {
          return (
            <span className="status-badge status-badge--neutral" title="Vorgang bereits angelegt">
              Vorgang angelegt
            </span>
          );
        }
        return (
          <span
            title={
              mark.source === "manual"
                ? "Von Hand markiert"
                : mark.source === "rule"
                  ? "Durch eine Regel markiert"
                  : "Durch die Kategorie markiert"
            }
          >
            {mark.expected_minor === null
              ? "Höhe offen"
              : formatMinorUnitsAsEuro(mark.expected_minor, row.original.currency)}
          </span>
        );
      },
    },
    {
      id: "actions",
      header: "Aktionen",
      cell: ({ row }) => (
        <span className="actions-cell">
          <button
            type="button"
            className="icon-btn"
            onClick={() => setEditTransactionId(row.original.id)}
            title="Buchung bearbeiten"
            aria-label="Buchung bearbeiten"
          >
            ✎
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setSplitEditorId(row.original.id)}
            title="Splits bearbeiten"
            aria-label="Splits bearbeiten"
          >
            ⋯
          </button>
          {row.original.amount_minor < 0 &&
            (marksByTransaction.has(row.original.id) ? (
              <button
                type="button"
                className="icon-btn"
                onClick={() => removeMarkMutation.mutate(row.original.id)}
                disabled={Boolean(marksByTransaction.get(row.original.id)?.converted_at)}
                title={
                  marksByTransaction.get(row.original.id)?.converted_at
                    ? "Aus dieser Markierung ist ein Vorgang geworden - sie lässt sich nur über den Vorgang auflösen"
                    : "Markierung entfernen (Regeln dürfen danach wieder greifen)"
                }
                aria-label="Erstattungsmarkierung entfernen"
              >
                ⊘
              </button>
            ) : (
              <button
                type="button"
                className="icon-btn"
                onClick={() =>
                  markMutation.mutate({ transactionId: row.original.id, state: "expected" })
                }
                title="Als erstattungsfähig markieren (volle Erstattung erwartet)"
                aria-label="Als erstattungsfähig markieren"
              >
                ⊕
              </button>
            ))}
          <button
            type="button"
            className="icon-btn icon-btn--danger"
            onClick={() => handleDelete(row.original)}
            title="Buchung löschen"
            aria-label="Buchung löschen"
          >
            🗑
          </button>
        </span>
      ),
    },
  ];

  // Defensiv gegen alte Speicherstaende: eine inzwischen entfernte Spalte im
  // localStorage darf die Tabelle nicht zerlegen.
  //
  // Umgekehrt muessen NEUE Spalten in einen gespeicherten Stand nachwachsen.
  // Wer die Tabelle je angepasst hat, hat eine feste Schluesselliste im
  // localStorage; ein spaeter hinzugekommener Schluessel fehlt darin und
  // landete sonst dauerhaft in hiddenColumns - die Spalte waere fuer genau die
  // Nutzer unsichtbar, die die Tabelle am meisten benutzen, und zwar ohne
  // jeden Hinweis. Eingefuegt wird an der kanonischen Stelle, damit die
  // gewohnte Reihenfolge erhalten bleibt.
  const columnLayout = useMemo(() => {
    const known = storedLayout.filter((key) => COLUMN_BY_KEY.has(key));
    if (known.length === 0) return DATA_COLUMN_IDS;
    const missing = DATA_COLUMN_IDS.filter(
      (key) => !known.includes(key) && !seenColumns.includes(key),
    );
    if (missing.length === 0) return known;
    const merged = [...known];
    for (const key of missing) {
      const predecessors = DATA_COLUMN_IDS.slice(0, DATA_COLUMN_IDS.indexOf(key));
      const anchor = [...predecessors].reverse().find((candidate) => merged.includes(candidate));
      merged.splice(anchor ? merged.indexOf(anchor) + 1 : 0, 0, key);
    }
    return merged;
  }, [storedLayout, seenColumns]);

  const hiddenColumns = useMemo(
    () => DATA_COLUMN_IDS.filter((key) => !columnLayout.includes(key)),
    [columnLayout],
  );

  const columnVisibility = useMemo<VisibilityState>(
    () => Object.fromEntries(hiddenColumns.map((key) => [key, false])),
    [hiddenColumns],
  );

  const columnOrder = useMemo(
    () => ["select", ...columnLayout, ...hiddenColumns, "actions"],
    [columnLayout, hiddenColumns],
  );

  const table = useReactTable({
    data: visibleTransactions,
    columns,
    state: { rowSelection, columnVisibility, columnOrder },
    onRowSelectionChange: setRowSelection,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
  });

  const rows = table.getRowModel().rows;
  const visibleColumnIds = table.getVisibleLeafColumns().map((column) => column.id);
  const visibleDataColumns = DATA_COLUMN_IDS.filter((id) => visibleColumnIds.includes(id));

  // Reihenfolge fuer die Pfeiltasten-Navigation (Abschnitt 10.3:
  // "Tastatursteuerung"). Aus den SICHTBAREN Spalten abgeleitet - eine
  // ausgeblendete Spalte darf nicht als unsichtbare Zwischenstation im
  // Fokuspfad liegen bleiben.
  const navColumnIds = visibleColumnIds;

  // ---------------------------------------------------------------------
  // Gruppierung: aus den sichtbaren Buchungen wird ein Baum mit
  // Zwischensummen. Ohne Gruppierung bleibt die Zeilenliste unveraendert -
  // die Tabelle sieht dann exakt aus wie zuvor.
  // ---------------------------------------------------------------------

  const groupTree = useMemo(() => {
    if (groupBy.length === 0) return [];
    return buildRegisterGroups(visibleTransactions, groupBy, groupValue);
  }, [visibleTransactions, groupBy, groupValue]);

  const displayRows = useMemo<DisplayRow[]>(() => {
    if (groupBy.length === 0) {
      return visibleTransactions.map((transaction) => ({
        kind: "transaction" as const,
        transaction,
      }));
    }
    const flat: DisplayRow[] = [];
    const walk = (nodes: RegisterGroup[]) => {
      for (const node of nodes) {
        flat.push({ kind: "group", group: node });
        if (collapsed.has(node.path)) continue;
        if (node.children.length > 0) {
          walk(node.children);
        } else {
          for (const transaction of node.transactions) {
            flat.push({ kind: "transaction", transaction });
          }
        }
      }
    };
    walk(groupTree);
    return flat;
  }, [groupBy, groupTree, visibleTransactions, collapsed]);

  const rowByTransactionId = useMemo(() => {
    const map = new Map<string, (typeof rows)[number]>();
    rows.forEach((row) => map.set(row.id, row));
    return map;
  }, [rows]);

  // ---------------------------------------------------------------------
  // Tastatursteuerung: Pfeiltasten bewegen den Fokus zwischen Zellen,
  // Enter startet die Inline-Bearbeitung der aktuellen Zelle (Notiz/
  // Kategorie) bzw. klappt eine Gruppenzeile auf und zu.
  // ---------------------------------------------------------------------

  function cellKey(rowIndex: number, columnId: string): string {
    return `${rowIndex}:${columnId}`;
  }

  function setCellRef(rowIndex: number, columnId: string) {
    return (el: HTMLTableCellElement | null) => {
      const key = cellKey(rowIndex, columnId);
      if (el) {
        cellRefs.current.set(key, el);
      } else {
        cellRefs.current.delete(key);
      }
    };
  }

  /**
   * Eine Gruppenzeile hat nur EINE Zelle, die Navigation kennt aber alle
   * Spalten. Die Zelle meldet sich deshalb unter jeder Spalten-ID an - sonst
   * liefe der Fokus beim Durchtabben durch die Spalten in eine Zeile, in der
   * es die Zielzelle nicht gibt, und bliebe stehen.
   */
  function setGroupCellRef(rowIndex: number) {
    return (el: HTMLTableCellElement | null) => {
      for (const columnId of navColumnIds) {
        const key = cellKey(rowIndex, columnId);
        if (el) {
          cellRefs.current.set(key, el);
        } else {
          cellRefs.current.delete(key);
        }
      }
    };
  }

  function toggleGroupPath(path: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function handleTableKeyDown(event: ReactKeyboardEvent<HTMLTableElement>) {
    if (editingCell) {
      // Waehrend der Inline-Bearbeitung uebernimmt das jeweilige Feld selbst
      // Enter (speichern) und Escape (abbrechen) - siehe unten.
      return;
    }
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter"].includes(event.key)) {
      return;
    }
    event.preventDefault();

    const rowCount = displayRows.length;
    if (rowCount === 0) {
      return;
    }
    const current = focusedCell ?? { rowIndex: 0, columnId: navColumnIds[0] ?? "select" };

    if (event.key === "Enter") {
      const entry = displayRows[current.rowIndex];
      if (!entry) {
        return;
      }
      if (entry.kind === "group") {
        toggleGroupPath(entry.group.path);
        return;
      }
      if (current.columnId === "payeeNote") {
        beginEditNote(entry.transaction);
      } else if (current.columnId === "category") {
        if (entry.transaction.is_split) {
          setSplitEditorId(entry.transaction.id);
        } else {
          setEditingCell({ id: entry.transaction.id, column: "category" });
        }
      }
      return;
    }

    let { rowIndex } = current;
    let columnId = current.columnId;
    const colIndex = navColumnIds.indexOf(columnId);

    if (event.key === "ArrowDown") {
      rowIndex = Math.min(rowIndex + 1, rowCount - 1);
    } else if (event.key === "ArrowUp") {
      rowIndex = Math.max(rowIndex - 1, 0);
    } else if (event.key === "ArrowRight") {
      columnId = navColumnIds[Math.min(colIndex + 1, navColumnIds.length - 1)] ?? columnId;
    } else if (event.key === "ArrowLeft") {
      columnId = navColumnIds[Math.max(colIndex - 1, 0)] ?? columnId;
    }

    // Virtualisierte Zeilen ausserhalb des sichtbaren Fensters existieren noch
    // nicht im DOM - erst dorthin scrollen, das eigentliche .focus() uebernimmt
    // der Effect unten, sobald die Zielzeile gemountet ist (siehe dort).
    rowVirtualizer.scrollToIndex(rowIndex, { align: "auto" });
    setFocusedCell({ rowIndex, columnId });
  }

  // Uebernimmt das eigentliche .focus() fuer focusedCell - sowohl fuer bereits
  // sichtbare Zellen (sofort, im selben Layout-Effect-Durchlauf) als auch fuer
  // Zellen, deren Zeile durch scrollToIndex() oben erst noch gemountet werden
  // muss (per rAF-Retry, bis react-virtual den Ref-Callback ausgeloest hat).
  useLayoutEffect(() => {
    if (!focusedCell) {
      return;
    }
    const { rowIndex, columnId } = focusedCell;
    const key = cellKey(rowIndex, columnId);

    let rafId: number | null = null;
    let attempts = 0;
    const MAX_ATTEMPTS = 20; // ~320ms bei 60fps - Sicherheitsgrenze, kein erwarteter Regelfall

    const tryFocus = () => {
      const el = cellRefs.current.get(key);
      if (el) {
        el.focus();
        return;
      }
      attempts += 1;
      if (attempts < MAX_ATTEMPTS) {
        rafId = requestAnimationFrame(tryFocus);
      }
    };
    tryFocus();

    return () => {
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
      }
    };
  }, [focusedCell]);

  // Abschnitt 10.3: "dichte, virtualisierte Tabelle".
  const rowVirtualizer = useVirtualizer<HTMLDivElement, HTMLTableRowElement>({
    count: displayRows.length,
    getScrollElement: () => tableScrollRef.current,
    estimateSize: () => 40,
    overscan: 8,
    getItemKey: (index) => {
      const entry = displayRows[index];
      if (!entry) return index;
      return entry.kind === "group" ? `g:${entry.group.path}` : `t:${entry.transaction.id}`;
    },
  });

  const virtualRows = rowVirtualizer.getVirtualItems();
  const lastVirtualRow = virtualRows.at(-1);
  const paddingTop = virtualRows[0]?.start ?? 0;
  const paddingBottom = lastVirtualRow ? rowVirtualizer.getTotalSize() - lastVirtualRow.end : 0;

  const effectiveFocused = focusedCell ?? {
    rowIndex: 0,
    columnId: navColumnIds[0] ?? "select",
  };

  // ---------------------------------------------------------------------
  // Spaltenmenue / Filterzustand
  // ---------------------------------------------------------------------

  function setColumnCondition(key: string, condition: AnalyticsCondition | null) {
    setColumnFilters((current) => {
      const next = { ...current };
      if (condition) next[key] = condition;
      else delete next[key];
      return next;
    });
    setRowSelection({});
  }

  // Klickfolge je Spalte: absteigend -> aufsteigend -> keine Sortierung.
  // Der dritte Klick fuehrt zurueck zur Standardreihenfolge (Datum absteigend),
  // statt in einer der beiden Richtungen festzustecken.
  function toggleSort(key: string) {
    setSort((current) => {
      if (current?.key !== key) return { key, direction: "desc" };
      return current.direction === "desc" ? { key, direction: "asc" } : null;
    });
  }

  const hasViewState =
    activeFilters.length > 0 || sort !== null || groupBy.length > 0 || search.length > 0;

  // ---------------------------------------------------------------------
  // "Neue Buchung"-Formular (aus Phase 1 uebernommen, unveraendert nutzbar).
  // ---------------------------------------------------------------------

  const [accountId, setAccountId] = useState("");
  const [bookedDate, setBookedDate] = useState(todayIso);
  const [amountInput, setAmountInput] = useState("");
  const [direction, setDirection] = useState<Direction>("expense");
  const [categoryId, setCategoryId] = useState("");
  const [payeeLabel, setPayeeLabel] = useState("");
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const effectiveAccountId = accountId || accountsQuery.data?.[0]?.id || "";

  const createMutation = useMutation({
    mutationFn: () => {
      const minorUnits = parseAmountToMinorUnits(amountInput);
      if (minorUnits === null || minorUnits === 0) {
        throw new Error("Bitte einen gueltigen Betrag angeben.");
      }
      if (!effectiveAccountId) {
        throw new Error("Bitte ein Konto auswaehlen.");
      }
      const signedAmount = direction === "expense" ? -Math.abs(minorUnits) : Math.abs(minorUnits);
      return createTransaction(accessToken as string, selectedHouseholdId as string, {
        account_id: effectiveAccountId,
        booked_date: bookedDate,
        amount_minor: signedAmount,
        category_id: categoryId || undefined,
        payee_label: payeeLabel.trim() || undefined,
        note: note.trim() || undefined,
      });
    },
    onSuccess: () => {
      setAmountInput("");
      setCategoryId("");
      setPayeeLabel("");
      setNote("");
      setFormError(null);
      invalidateTransactions();
    },
    onError: (error: unknown) => {
      setFormError(
        error instanceof Error ? error.message : "Buchung konnte nicht angelegt werden.",
      );
    },
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    createMutation.mutate();
  };

  const editTransactionTarget = editTransactionId ? transactionsById.get(editTransactionId) : null;

  return (
    <section className="page">
      <div className="page-header">
        <h1 className="page-heading">Alle Buchungen</h1>
        <div className="explorer-header-actions">
          <Link className="btn-secondary" to="/review#booked-duplicates">
            Duplikate prüfen
          </Link>
          <ColumnPicker
            columns={REGISTER_COLUMNS.map((column) => ({ key: column.key, label: column.label }))}
            visible={columnLayout}
            onChange={persistLayout}
            onReset={() => persistLayout(DATA_COLUMN_IDS)}
          />
        </div>
      </div>

      <div className="card register-toolbar">
        <form
          className="explorer-search"
          onSubmit={(event) => {
            event.preventDefault();
            setSearch(searchDraft.trim());
            setRowSelection({});
          }}
        >
          <input
            className="form-input"
            placeholder="Empfänger, Verwendungszweck oder Notiz durchsuchen…"
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
            aria-label="Buchungen durchsuchen"
          />
          <button type="submit" className="btn-secondary">
            Suchen
          </button>
        </form>

        <div className="explorer-controls">
          <label className="form-field">
            <span className="form-label">Konto-Filter</span>
            <select
              className="form-input"
              value={accountFilter}
              onChange={(event) => {
                setAccountFilter(event.target.value);
                setRowSelection({});
              }}
            >
              <option value="">Alle Konten</option>
              {accountsQuery.data?.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </label>

          <label className="form-field">
            <span className="form-label">Von</span>
            <input
              type="date"
              className="form-input"
              value={fromDate}
              onChange={(event) => setFromDate(event.target.value)}
            />
          </label>

          <label className="form-field">
            <span className="form-label">Bis</span>
            <input
              type="date"
              className="form-input"
              value={toDate}
              onChange={(event) => setToDate(event.target.value)}
            />
          </label>

          <label className="form-field">
            <span className="form-label">Zeilen</span>
            <select
              className="form-input"
              value={rowLimit}
              onChange={(event) => setRowLimit(Number(event.target.value))}
            >
              {ROW_LIMIT_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>

        <GroupingBar
          groups={groupBy.map((key) => ({
            key,
            label: COLUMN_BY_KEY.get(key)?.label ?? key,
          }))}
          available={REGISTER_COLUMNS.filter(
            (column) => column.groupable && !groupBy.includes(column.key),
          ).map((column) => ({ key: column.key, label: column.label }))}
          onAdd={(key) => setGroupBy([...groupBy, key])}
          onRemove={(key) => setGroupBy(groupBy.filter((entry) => entry !== key))}
          onMove={(key, delta) => {
            const index = groupBy.indexOf(key);
            const target = index + delta;
            if (index < 0 || target < 0 || target >= groupBy.length) return;
            const next = [...groupBy];
            const [entry] = next.splice(index, 1);
            if (entry) next.splice(target, 0, entry);
            setGroupBy(next);
          }}
          onClear={() => setGroupBy([])}
          note={groupBy.length > 0 ? "Zwischensumme und Anzahl je Gruppe." : undefined}
        />

        {(activeFilters.length > 0 || sort) && (
          <div className="filter-chips">
            {activeFilters.map((condition) => (
              <button
                key={condition.field}
                type="button"
                className="filter-chip"
                onClick={() => setColumnCondition(condition.field, null)}
              >
                {describeCondition(
                  condition,
                  COLUMN_BY_KEY.get(condition.field)?.label ?? condition.field,
                  undefined,
                  (value) =>
                    COLUMN_BY_KEY.get(condition.field)?.isMoney && typeof value === "number"
                      ? formatMinorUnitsAsEuro(value)
                      : String(value),
                )}{" "}
                <span aria-hidden>×</span>
              </button>
            ))}
            {sort && (
              <button type="button" className="filter-chip" onClick={() => setSort(null)}>
                Sortiert nach {COLUMN_BY_KEY.get(sort.key)?.label ?? sort.key}{" "}
                {sort.direction === "asc" ? "↑" : "↓"} <span aria-hidden>×</span>
              </button>
            )}
          </div>
        )}

        <p className="placeholder-note register-scope-note">
          Suche, Konto und Zeitraum wirken auf den gesamten Bestand; Spaltenfilter, Sortierung und
          Gruppierung auf die {loadedTransactions.length} geladenen Buchungen
          {loadedTransactions.length === rowLimit ? " (Grenze erreicht)" : ""}. Für Auswertungen
          über alles: <Link to="/explorer">Explorer</Link>.
        </p>
      </div>

      {actionError && (
        <p role="alert" className="status-error">
          {actionError}
        </p>
      )}

      {selectedIds.length > 0 && (
        <div className="selection-bar">
          <div className="selection-bar-row">
            <span className="selection-bar-count">{selectedIds.length} ausgewählt</span>

            <button
              type="button"
              className="btn-secondary"
              onClick={() => table.toggleAllRowsSelected(true)}
            >
              Alle {rows.length} sichtbaren auswählen
            </button>

            <CategoryPicker
              categoryGroups={categoriesQuery.data}
              value={bulkCategoryValue}
              onChange={setBulkCategoryValue}
              noneLabel="Kategorie entfernen"
              aria-label="Kategorie für die Mehrfachzuordnung"
            />
            <button
              type="button"
              className="btn-secondary"
              onClick={handleBulkPreview}
              disabled={bulkPreviewMutation.isPending || bulkOverLimit}
            >
              {bulkPreviewMutation.isPending ? "Vorschau läuft…" : "Vorschau anzeigen"}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={handleBulkApply}
              disabled={!currentBulkPreview || bulkApplyMutation.isPending || bulkOverLimit}
            >
              {bulkApplyMutation.isPending ? "Wird angewendet…" : "Zuordnung anwenden"}
            </button>

            {markableIds.length > 0 && (
              <>
                <select
                  className="form-input"
                  value={markBulkState}
                  onChange={(event) =>
                    setMarkBulkState(event.target.value as "expected" | "excluded")
                  }
                  aria-label="Erstattung für die Mehrfachauswahl"
                >
                  <option value="expected">Erstattung erwartet (voll)</option>
                  <option value="excluded">Von der Erstattung ausnehmen</option>
                </select>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() =>
                    markBulkMutation.mutate({ signature: markBulkSignature, mode: "preview" })
                  }
                  disabled={markBulkMutation.isPending}
                >
                  Erstattung: Vorschau ({markableIds.length})
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() =>
                    markBulkMutation.mutate({ signature: markBulkSignature, mode: "apply" })
                  }
                  disabled={markBulkMutation.isPending || currentMarkOutcome?.kind !== "Vorschau"}
                >
                  Erstattung anwenden
                </button>
              </>
            )}

            {transferCandidates && (
              <button
                type="button"
                className="btn-secondary"
                onClick={handleLinkTransfer}
                disabled={linkTransferMutation.isPending}
              >
                {linkTransferMutation.isPending ? "Wird verknüpft…" : "Als Transfer verknüpfen"}
              </button>
            )}

            <button type="button" className="btn-secondary" onClick={() => setRowSelection({})}>
              Auswahl aufheben
            </button>
          </div>

          {bulkOverLimit && (
            <p role="alert" className="status-error">
              Pro Vorgang sind höchstens {MAX_BULK_TRANSACTIONS} Buchungen möglich – aktuell sind{" "}
              {selectedIds.length} ausgewählt. Bitte die Auswahl verkleinern.
            </p>
          )}

          {!currentBulkPreview && !currentBulkResult && !bulkOverLimit && (
            <p className="selection-bar-message">
              Die Vorschau zeigt vor dem Anwenden, welche Buchungen geändert und welche übersprungen
              werden.
            </p>
          )}

          {currentMarkOutcome && (
            <p className="selection-bar-message">
              Erstattung – {currentMarkOutcome.kind}: {currentMarkOutcome.result.applied} gesetzt,{" "}
              {currentMarkOutcome.result.unchanged} unverändert, {currentMarkOutcome.result.skipped}{" "}
              übersprungen, {currentMarkOutcome.result.conflict} durch Handentscheidung geschützt.
              {currentMarkOutcome.result.items
                .filter((item) => item.reason)
                .slice(0, 3)
                .map((item) => ` ${item.reason}`)}
            </p>
          )}
          {currentBulkPreview && renderBulkOutcome("Vorschau", currentBulkPreview)}
          {currentBulkResult && renderBulkOutcome("Ergebnis", currentBulkResult)}
        </div>
      )}

      {transactionsQuery.isLoading && <p role="status">Lade Buchungen…</p>}
      {transactionsQuery.isError && (
        <p role="alert" className="status-error">
          Buchungen konnten nicht geladen werden.
        </p>
      )}

      {transactionsQuery.data && (
        <div className="txn-table-wrapper" ref={tableScrollRef}>
          <table
            className="txn-table"
            onKeyDown={handleTableKeyDown}
            aria-rowcount={displayRows.length + 1}
          >
            <thead>
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id}>
                  {headerGroup.headers.map((header) => {
                    const gridColumn = COLUMN_BY_KEY.get(header.column.id);
                    if (!gridColumn) {
                      return (
                        <th key={header.id} className={`txn-th txn-th--${header.column.id}`}>
                          {header.isPlaceholder
                            ? null
                            : flexRender(header.column.columnDef.header, header.getContext())}
                        </th>
                      );
                    }
                    return (
                      <GridColumnHeader
                        key={header.id}
                        column={gridColumn}
                        className={`txn-th--${header.column.id}`}
                        sortDirection={sort?.key === gridColumn.key ? sort.direction : null}
                        onToggleSort={() => toggleSort(gridColumn.key)}
                        filtered={Boolean(columnFilters[gridColumn.key])}
                        grouped={groupBy.includes(gridColumn.key)}
                        menu={(close) => (
                          <ColumnFilterMenu
                            column={gridColumn}
                            condition={columnFilters[gridColumn.key] ?? null}
                            onConditionChange={(condition) =>
                              setColumnCondition(gridColumn.key, condition)
                            }
                            sortDirection={sort?.key === gridColumn.key ? sort.direction : null}
                            onSortChange={(direction) =>
                              setSort(direction ? { key: gridColumn.key, direction } : null)
                            }
                            grouped={groupBy.includes(gridColumn.key)}
                            onToggleGroup={
                              gridColumn.groupable
                                ? () =>
                                    setGroupBy((current) =>
                                      current.includes(gridColumn.key)
                                        ? current.filter((entry) => entry !== gridColumn.key)
                                        : [...current, gridColumn.key],
                                    )
                                : undefined
                            }
                            onHide={
                              columnLayout.length > 1
                                ? () =>
                                    persistLayout(
                                      columnLayout.filter((key) => key !== gridColumn.key),
                                    )
                                : undefined
                            }
                            values={
                              gridColumn.hasValues
                                ? (columnValues.get(gridColumn.key) ?? [])
                                : undefined
                            }
                            onClose={close}
                          />
                        )}
                      />
                    );
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {displayRows.length === 0 ? (
                <tr>
                  <td className="txn-td txn-td--empty" colSpan={visibleColumnIds.length}>
                    {loadedTransactions.length === 0
                      ? "Noch keine Buchungen vorhanden."
                      : "Keine Buchung passt zu den gesetzten Filtern."}
                  </td>
                </tr>
              ) : (
                <>
                  {paddingTop > 0 && (
                    <tr aria-hidden="true" style={{ height: paddingTop }}>
                      <td
                        style={{ padding: 0, border: "none" }}
                        colSpan={visibleColumnIds.length}
                      />
                    </tr>
                  )}
                  {virtualRows.map((virtualRow) => {
                    const entry = displayRows[virtualRow.index];
                    if (!entry) {
                      return null;
                    }
                    const rowIndex = virtualRow.index;

                    if (entry.kind === "group") {
                      const isCollapsed = collapsed.has(entry.group.path);
                      const isFocused = effectiveFocused.rowIndex === rowIndex;
                      return (
                        <tr
                          key={virtualRow.key}
                          data-index={rowIndex}
                          ref={rowVirtualizer.measureElement}
                          aria-rowindex={rowIndex + 2}
                          className={`txn-tr grid-tree-row grid-tree-row--depth-${Math.min(entry.group.depth, 3)}`}
                        >
                          <td
                            ref={setGroupCellRef(rowIndex)}
                            tabIndex={isFocused ? 0 : -1}
                            colSpan={visibleColumnIds.length}
                            className="txn-td grid-tree-detail"
                            onClick={() =>
                              setFocusedCell({ rowIndex, columnId: navColumnIds[0] ?? "select" })
                            }
                          >
                            <span
                              className="grid-tree-cell"
                              style={{ paddingLeft: `${entry.group.depth * 18}px` }}
                            >
                              <button
                                type="button"
                                className="grid-tree-toggle"
                                aria-expanded={!isCollapsed}
                                aria-label={`${entry.group.label} ${isCollapsed ? "aufklappen" : "zuklappen"}`}
                                onClick={() => toggleGroupPath(entry.group.path)}
                              >
                                {isCollapsed ? "▸" : "▾"}
                              </button>
                              <span className="grid-tree-label">{entry.group.label}</span>
                              <span className="grid-tree-count">
                                {entry.group.count}{" "}
                                {entry.group.count === 1 ? "Buchung" : "Buchungen"}
                              </span>
                              <span
                                className={
                                  entry.group.sum < 0
                                    ? "grid-tree-sum transaction-amount--negative"
                                    : "grid-tree-sum transaction-amount--positive"
                                }
                              >
                                {formatMinorUnitsAsEuro(entry.group.sum)}
                              </span>
                            </span>
                          </td>
                        </tr>
                      );
                    }

                    const row = rowByTransactionId.get(entry.transaction.id);
                    if (!row) {
                      return null;
                    }
                    return (
                      <tr
                        key={virtualRow.key}
                        data-index={rowIndex}
                        ref={rowVirtualizer.measureElement}
                        aria-rowindex={rowIndex + 2}
                        className={row.getIsSelected() ? "txn-tr txn-tr--selected" : "txn-tr"}
                      >
                        {row.getVisibleCells().map((cell) => {
                          const columnId = cell.column.id;
                          const isFocused =
                            effectiveFocused.rowIndex === rowIndex &&
                            effectiveFocused.columnId === columnId;
                          return (
                            <td
                              key={cell.id}
                              ref={setCellRef(rowIndex, columnId)}
                              tabIndex={isFocused ? 0 : -1}
                              onClick={() => {
                                // Bewusst onClick statt onFocus: ein setState im
                                // nativen "focus"-Event (das beim mousedown VOR
                                // dem click feuert) fuehrt bei simulierten Klicks
                                // (z. B. Playwright) dazu, dass der Browser den
                                // click als "verloren" behandelt, sobald sich das
                                // DOM zwischen mousedown und click aendert.
                                // onClick laeuft erst NACH mouseup und stoert den
                                // Klick daher nicht.
                                setFocusedCell((current) => {
                                  if (
                                    current &&
                                    current.rowIndex === rowIndex &&
                                    current.columnId === columnId
                                  ) {
                                    return current;
                                  }
                                  return { rowIndex, columnId };
                                });
                              }}
                              className={`txn-td txn-td--${columnId}`}
                            >
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                  {paddingBottom > 0 && (
                    <tr aria-hidden="true" style={{ height: paddingBottom }}>
                      <td
                        style={{ padding: 0, border: "none" }}
                        colSpan={visibleColumnIds.length}
                      />
                    </tr>
                  )}
                </>
              )}
            </tbody>
          </table>
        </div>
      )}

      {hasViewState && (
        <p className="placeholder-note">
          {visibleTransactions.length} von {loadedTransactions.length} geladenen Buchungen sichtbar
          {visibleDataColumns.length < DATA_COLUMN_IDS.length &&
            `, ${DATA_COLUMN_IDS.length - visibleDataColumns.length} Spalte(n) ausgeblendet`}
          .
        </p>
      )}

      <div className="card">
        <h2 className="card-heading">Neue Buchung</h2>
        <form className="inline-form" onSubmit={handleSubmit}>
          <label className="form-field">
            <span className="form-label">Konto</span>
            <select
              className="form-input"
              value={effectiveAccountId}
              onChange={(event) => setAccountId(event.target.value)}
              required
            >
              <option value="" disabled>
                Konto wählen
              </option>
              {accountsQuery.data?.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </label>

          <label className="form-field">
            <span className="form-label">Datum</span>
            <input
              type="date"
              className="form-input"
              value={bookedDate}
              onChange={(event) => setBookedDate(event.target.value)}
              required
            />
          </label>

          <div className="form-field">
            <span className="form-label">Art</span>
            <div className="direction-toggle" role="group" aria-label="Ausgabe oder Einnahme">
              <button
                type="button"
                className={
                  direction === "expense" ? "direction-btn direction-btn--active" : "direction-btn"
                }
                onClick={() => setDirection("expense")}
                aria-pressed={direction === "expense"}
              >
                Ausgabe
              </button>
              <button
                type="button"
                className={
                  direction === "income" ? "direction-btn direction-btn--active" : "direction-btn"
                }
                onClick={() => setDirection("income")}
                aria-pressed={direction === "income"}
              >
                Einnahme
              </button>
            </div>
          </div>

          <label className="form-field">
            <span className="form-label">Betrag</span>
            <input
              className="form-input form-input--mono"
              placeholder="0,00"
              value={amountInput}
              onChange={(event) => setAmountInput(event.target.value)}
              required
            />
          </label>

          <div className="form-field">
            <label className="form-label" htmlFor="new-transaction-category">
              Kategorie (optional)
            </label>
            <CategoryPicker
              id="new-transaction-category"
              categoryGroups={categoriesQuery.data}
              value={categoryId}
              onChange={setCategoryId}
              amountMinor={
                direction === "expense"
                  ? -Math.abs(parseAmountToMinorUnits(amountInput) ?? 0)
                  : Math.abs(parseAmountToMinorUnits(amountInput) ?? 0)
              }
            />
          </div>

          <label className="form-field">
            <span className="form-label">Empfänger (optional)</span>
            <input
              className="form-input"
              value={payeeLabel}
              onChange={(event) => setPayeeLabel(event.target.value)}
            />
          </label>

          <label className="form-field">
            <span className="form-label">Notiz (optional)</span>
            <input
              className="form-input"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>

          {formError && (
            <p role="alert" className="status-error">
              {formError}
            </p>
          )}

          <button
            type="submit"
            className="btn-primary"
            disabled={createMutation.isPending || !accountsQuery.data?.length}
          >
            {createMutation.isPending ? "Wird angelegt…" : "Buchung anlegen"}
          </button>
        </form>
      </div>

      {splitEditorId && (
        <SplitEditorModal
          transactionId={splitEditorId}
          categoryGroups={categoriesQuery.data}
          goals={goalsQuery.data}
          onClose={() => setSplitEditorId(null)}
        />
      )}

      {editTransactionTarget && (
        <EditTransactionModal
          transaction={editTransactionTarget}
          accounts={accountsQuery.data}
          categoryGroups={categoriesQuery.data}
          onEditSplits={() => {
            setEditTransactionId(null);
            setSplitEditorId(editTransactionTarget.id);
          }}
          onClose={() => setEditTransactionId(null)}
        />
      )}
    </section>
  );
}

/**
 * Baut den Gruppenbaum des Registers.
 *
 * Die Zwischensumme jeder Ebene ist die Summe ihrer Kinder - dieselbe
 * Konstruktion wie im Explorer und aus demselben Grund: eine Elternzeile, die
 * anders zustande kaeme als ihre Kinder, koennte ihnen widersprechen.
 */
function buildRegisterGroups(
  transactions: readonly Transaction[],
  groupBy: readonly string[],
  groupValue: (tx: Transaction, columnKey: string) => string,
): RegisterGroup[] {
  const roots: RegisterGroup[] = [];
  const index = new Map<string, RegisterGroup>();

  for (const transaction of transactions) {
    let parentPath = "";
    let siblings = roots;

    groupBy.forEach((columnKey, depth) => {
      const label = groupValue(transaction, columnKey);
      const path = `${parentPath}/${columnKey}=${label}`;
      let node = index.get(path);
      if (!node) {
        node = {
          path,
          depth,
          columnKey,
          label,
          sum: 0,
          count: 0,
          children: [],
          transactions: [],
        };
        index.set(path, node);
        siblings.push(node);
      }
      node.sum += transaction.amount_minor;
      node.count += 1;
      if (depth === groupBy.length - 1) {
        node.transactions.push(transaction);
      }
      parentPath = path;
      siblings = node.children;
    });
  }

  return roots;
}
