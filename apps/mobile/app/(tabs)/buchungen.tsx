import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native";
import type {
  BulkCategoryInput,
  Transaction,
  TransactionDirection,
  TransactionSort,
} from "../../src/api/types";
import { CategoryPickerModal } from "../../src/components/CategoryPickerModal";
import { EmptyState } from "../../src/components/EmptyState";
import { FilterSheet } from "../../src/components/FilterSheet";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { MonthNavigator } from "../../src/components/MonthNavigator";
import { OptionList, type Option } from "../../src/components/OptionList";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { SCREEN_EDGES_WITH_TOP, ScreenContainer } from "../../src/components/ScreenContainer";
import { SearchField } from "../../src/components/SearchField";
import { SectionHeader } from "../../src/components/SectionHeader";
import { SortSheet } from "../../src/components/SortSheet";
import { SwipeableRow, type SwipeAction } from "../../src/components/SwipeableRow";
import { TransactionRow, transactionTitle } from "../../src/components/TransactionRow";
import {
  Badge,
  Card,
  CardLabel,
  Chip,
  DataRow,
  IconButton,
  ScreenHeader,
} from "../../src/components/ui";
import { useAccounts } from "../../src/hooks/useAccounts";
import {
  useBulkCategoryApply,
  useBulkCategoryPreview,
  useQueueBulkCategory,
} from "../../src/hooks/useCategoryCatalog";
import { useCategories } from "../../src/hooks/useCategories";
import { useConnectivity } from "../../src/hooks/useConnectivity";
import { useDeleteTransaction } from "../../src/hooks/useTransactionMutations";
import { useTransactionsPage } from "../../src/hooks/useTransactions";
import { isPendingTransaction } from "../../src/offline/pendingTransactions";
import { MIN_TOUCH, radii, space, typeScale, useTheme } from "../../src/theme";
import { capSelection, selectAllLabel, summarizeBulkResult } from "../../src/utils/bulkCategory";
import { formatMonthLabel, formatTransactionDayLabel } from "../../src/utils/dates";

// Buchungen als kompletter Vertical Slice (docs/MOBILE-DESIGNSYSTEM.md und
// docs/mobile_redesign_showcase.html, Abschnitt 7 "Buchungen (Feed)"):
// mitscrollende Kopfzeile mit Filter- und Import-Knopf, Suchfeld in
// Kartenoptik, kompakter Monatsnavigator, Sortier- und Filterpillen und eine
// nach Tagen in Karten gegliederte (bei Datumssortierung) bzw. global
// sortierte (bei Betrag/Empfaenger) Buchungsliste ueber `useInfiniteQuery`
// (../../src/hooks/useTransactions.ts, `useTransactionsPage`).
//
// Manuelle Neuanlage entfaellt vollstaendig aus der sichtbaren UI UND dem
// Accessibility-Tree: kein "+ Neue Buchung" mehr, kein Empty-State-Hinweis
// darauf. Der "+"-Knopf der Kopfzeile fuehrt deshalb zum Import
// (app/mehr/import.tsx) - dem kanonischen Zufluss neuer Buchungen.

type DateScope = "month" | "all";

const ACCOUNT_FILTER_ANY = "__any_account__";
const DIRECTION_FILTER_ANY = "__any_direction__";

/**
 * Kartengroesse ohne Tagesgruppen. Die SectionList virtualisiert je Karte,
 * nicht je Zeile - ohne diese Obergrenze waere bei Betrags- oder
 * Namenssortierung die gesamte geladene Liste eine einzige Renderaufgabe.
 */
const ROWS_PER_CARD = 25;

const SORT_LABELS: Record<TransactionSort, string> = {
  date_desc: "Neueste zuerst",
  date_asc: "Älteste zuerst",
  amount_desc: "Betrag absteigend",
  amount_asc: "Betrag aufsteigend",
  payee_asc: "Empfänger A–Z",
};

const SORT_ICONS: Record<TransactionSort, React.ComponentProps<typeof Ionicons>["name"]> = {
  date_desc: "arrow-down",
  date_asc: "arrow-up",
  amount_desc: "trending-down",
  amount_asc: "trending-up",
  payee_asc: "text-outline",
};

const SORT_ORDER: TransactionSort[] = [
  "date_desc",
  "date_asc",
  "amount_desc",
  "amount_asc",
  "payee_asc",
];

const DATE_SCOPE_OPTIONS: Option<DateScope>[] = [
  { value: "month", label: "Dieser Monat" },
  { value: "all", label: "Alle Zeiträume" },
];

const DIRECTION_OPTIONS: Option<string>[] = [
  { value: DIRECTION_FILTER_ANY, label: "Einnahmen und Ausgaben" },
  { value: "income", label: "Nur Einnahmen" },
  { value: "expense", label: "Nur Ausgaben" },
];

function isoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function monthRange(year: number, month: number): { fromDate: string; toDate: string } {
  const from = new Date(year, month - 1, 1);
  const to = new Date(year, month, 0); // letzter Tag des Monats
  return { fromDate: isoDate(from), toDate: isoDate(to) };
}

/** Zielart der offenen Mehrfachauswahl bzw. der Einzelauswahl. */
type PickerTarget = { kind: "single"; transaction: Transaction } | { kind: "bulk" } | null;

/** Eine Karte der Liste: die Buchungen eines Tages bzw. ein Listenabschnitt. */
type TransactionCard = Transaction[];

type Section = { key: string; title: string; data: TransactionCard[] };

export default function BuchungenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const isConnected = useConnectivity();
  const deleteTransactionMutation = useDeleteTransaction();
  // Einzel- UND Mehrfachzuordnung laufen ueber PUT /v1/transactions/{id}/category
  // (offline: ein Queue-Eintrag je Buchung, siehe useCategoryCatalog.ts).
  const queueBulkCategory = useQueueBulkCategory();
  const bulkPreview = useBulkCategoryPreview();
  const bulkApply = useBulkCategoryApply();
  const { data: accounts } = useAccounts();
  const { data: categoryGroups } = useCategories();

  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);

  const [search, setSearch] = useState("");
  const [dateScope, setDateScope] = useState<DateScope>("month");
  const [accountId, setAccountId] = useState<string | undefined>(undefined);
  const [categoryId, setCategoryId] = useState<string | undefined>(undefined);
  const [uncategorized, setUncategorized] = useState(false);
  const [direction, setDirection] = useState<TransactionDirection | undefined>(undefined);
  const [sort, setSort] = useState<TransactionSort>("date_desc");

  const [filterSheetOpen, setFilterSheetOpen] = useState(false);
  const [sortSheetOpen, setSortSheetOpen] = useState(false);
  const [categoryFilterPickerOpen, setCategoryFilterPickerOpen] = useState(false);

  // Entwurfszustand des Filter-Sheets - erst bei "Anwenden" uebernommen,
  // beim Schliessen (Backdrop/Kopfzeile) verworfen.
  const [draftDateScope, setDraftDateScope] = useState<DateScope>("month");
  const [draftAccountId, setDraftAccountId] = useState<string | undefined>(undefined);
  const [draftCategoryId, setDraftCategoryId] = useState<string | undefined>(undefined);
  const [draftUncategorized, setDraftUncategorized] = useState(false);
  const [draftDirection, setDraftDirection] = useState<TransactionDirection | undefined>(undefined);

  const [picker, setPicker] = useState<PickerTarget>(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const isSearching = search.trim().length > 0;

  // Eine aktive Suche gilt zeitraumuebergreifend (Abnahme: "Suche findet auch
  // Buchungen ausserhalb der bisher letzten 100 Eintraege") - der
  // Monatsnavigator bleibt dabei unveraendert sichtbar/bedienbar, wirkt sich
  // aber erst wieder aus, sobald der Suchtext geloescht wird.
  const { fromDate, toDate } = useMemo(() => {
    if (isSearching || dateScope === "all") return { fromDate: undefined, toDate: undefined };
    return monthRange(year, month);
  }, [isSearching, dateScope, year, month]);

  const {
    items: transactions,
    isLoading,
    isError,
    error,
    isFetching,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    refetch,
  } = useTransactionsPage({
    accountId,
    fromDate,
    toDate,
    search,
    categoryId,
    uncategorized,
    direction,
    sort,
  });

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const account of accounts ?? []) {
      map.set(account.id, account.name);
    }
    return map;
  }, [accounts]);

  const groups = useMemo(() => categoryGroups ?? [], [categoryGroups]);

  const categoryNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const group of groups) {
      for (const category of group.categories) {
        map.set(category.id, category.name);
      }
    }
    return map;
  }, [groups]);

  /** Nur uebertragene Buchungen sind serverseitig adressierbar. */
  const selectableTransactions = useMemo(
    () => transactions.filter((transaction) => !isPendingTransaction(transaction)),
    [transactions],
  );

  const selectedTransactions = useMemo(
    () => selectableTransactions.filter((transaction) => selectedIds.includes(transaction.id)),
    [selectableTransactions, selectedIds],
  );

  // Bei Datumssortierung eine Karte je Tag ("Heute"/"Gestern"/lokalisierte
  // Daten als Ueberschrift darueber); bei Betrag/Name bleibt es eine einzige,
  // global sortierte Liste ohne irrefuehrende Gruppen - dort trennen die
  // Karten nur noch Renderbloecke, deshalb ohne Ueberschrift.
  const sections = useMemo<Section[]>(() => {
    if (transactions.length === 0) return [];
    if (sort !== "date_desc" && sort !== "date_asc") {
      const cards: TransactionCard[] = [];
      for (let index = 0; index < transactions.length; index += ROWS_PER_CARD) {
        cards.push(transactions.slice(index, index + ROWS_PER_CARD));
      }
      return [{ key: "all", title: "", data: cards }];
    }
    const dayGroups: Section[] = [];
    for (const transaction of transactions) {
      const title = formatTransactionDayLabel(transaction.booked_date);
      const current = dayGroups.at(-1);
      const currentCard = current?.data[0];
      if (current?.title === title && currentCard) {
        currentCard.push(transaction);
      } else {
        dayGroups.push({ key: `${title}-${dayGroups.length}`, title, data: [[transaction]] });
      }
    }
    return dayGroups;
  }, [transactions, sort]);

  const activeFilterCount = [
    dateScope === "all",
    !!accountId,
    !!categoryId || uncategorized,
    !!direction,
  ].filter(Boolean).length;
  const filtersActive = activeFilterCount > 0;

  type FilterChip = { key: string; label: string; onRemove: () => void };
  const chips = useMemo<FilterChip[]>(() => {
    const list: FilterChip[] = [];
    if (dateScope === "all") {
      list.push({ key: "scope", label: "Alle Zeiträume", onRemove: () => setDateScope("month") });
    }
    if (accountId) {
      list.push({
        key: "account",
        label: accountNameById.get(accountId) ?? "Konto",
        onRemove: () => setAccountId(undefined),
      });
    }
    if (uncategorized) {
      list.push({
        key: "category",
        label: "Ohne Kategorie",
        onRemove: () => setUncategorized(false),
      });
    } else if (categoryId) {
      list.push({
        key: "category",
        label: categoryNameById.get(categoryId) ?? "Kategorie",
        onRemove: () => setCategoryId(undefined),
      });
    }
    if (direction) {
      list.push({
        key: "direction",
        label: direction === "income" ? "Nur Einnahmen" : "Nur Ausgaben",
        onRemove: () => setDirection(undefined),
      });
    }
    return list;
  }, [
    dateScope,
    accountId,
    uncategorized,
    categoryId,
    direction,
    accountNameById,
    categoryNameById,
  ]);

  function openFilterSheet() {
    setDraftDateScope(dateScope);
    setDraftAccountId(accountId);
    setDraftCategoryId(categoryId);
    setDraftUncategorized(uncategorized);
    setDraftDirection(direction);
    setFilterSheetOpen(true);
  }

  function applyFilters() {
    setDateScope(draftDateScope);
    setAccountId(draftAccountId);
    setCategoryId(draftCategoryId);
    setUncategorized(draftUncategorized);
    setDirection(draftDirection);
    setFilterSheetOpen(false);
  }

  function resetFilters() {
    setDraftDateScope("month");
    setDraftAccountId(undefined);
    setDraftCategoryId(undefined);
    setDraftUncategorized(false);
    setDraftDirection(undefined);
    setDateScope("month");
    setAccountId(undefined);
    setCategoryId(undefined);
    setUncategorized(false);
    setDirection(undefined);
    setFilterSheetOpen(false);
  }

  function resetFiltersAndSearch() {
    resetFilters();
    setSearch("");
  }

  function openCategoryFilterPicker() {
    setFilterSheetOpen(false);
    setCategoryFilterPickerOpen(true);
  }

  function handleCategoryFilterPickerChange(selected: string | null) {
    setCategoryFilterPickerOpen(false);
    if (selected === null) {
      setDraftUncategorized(true);
      setDraftCategoryId(undefined);
    } else {
      setDraftUncategorized(false);
      setDraftCategoryId(selected);
    }
    setFilterSheetOpen(true);
  }

  function closeCategoryFilterPicker() {
    setCategoryFilterPickerOpen(false);
    setFilterSheetOpen(true);
  }

  const draftResetDisabled =
    draftDateScope === "month" &&
    !draftAccountId &&
    !draftCategoryId &&
    !draftUncategorized &&
    !draftDirection;

  const categoryFilterLabel = draftUncategorized
    ? "Ohne Kategorie"
    : draftCategoryId
      ? (categoryNameById.get(draftCategoryId) ?? "Kategorie")
      : "Alle Kategorien";

  const exitSelection = () => {
    setSelectionMode(false);
    setSelectedIds([]);
  };

  function toggleSelected(transactionId: string) {
    setSelectedIds((prev) =>
      prev.includes(transactionId)
        ? prev.filter((id) => id !== transactionId)
        : [...prev, transactionId],
    );
  }

  function enterSelection(transaction: Transaction) {
    if (isPendingTransaction(transaction)) return;
    setSelectionMode(true);
    setSelectedIds((prev) => (prev.includes(transaction.id) ? prev : [...prev, transaction.id]));
  }

  function selectAllVisible() {
    // Der Geltungsbereich wird ausdruecklich benannt: nur bereits geladene
    // sichtbare Treffer, keine noch nicht geladenen weiteren Seiten.
    const { applicable, droppedCount } = capSelection(
      selectableTransactions.map((transaction) => transaction.id),
    );
    setSelectedIds(applicable);
    if (droppedCount > 0) {
      Alert.alert(
        "Auswahl begrenzt",
        `Pro Vorgang sind höchstens 500 Buchungen möglich. ${droppedCount} weitere sichtbare Buchung(en) wurden nicht ausgewählt.`,
      );
    }
  }

  // --- Einzelzuordnung -----------------------------------------------------

  function handleCategorySwipe(transaction: Transaction) {
    if (transaction.is_split) {
      // Eine einzelne Schnellauswahl ergibt bei mehreren Splits keinen Sinn -
      // stattdessen direkt zur Detailseite mit dem vollen Splits-Editor.
      router.push(`/buchung/${transaction.id}`);
      return;
    }
    setPicker({ kind: "single", transaction });
  }

  async function assignSingle(transaction: Transaction, categoryId: string | null) {
    try {
      await queueBulkCategory.mutateAsync({
        categoryId,
        transactions: [{ id: transaction.id, version: transaction.version }],
      });
    } catch (cause) {
      Alert.alert(
        "Kategorie konnte nicht geändert werden",
        cause instanceof Error ? cause.message : "Unbekannter Fehler.",
      );
    }
  }

  // --- Mehrfachzuordnung ---------------------------------------------------

  async function assignBulk(categoryId: string | null) {
    const { applicable, droppedCount } = capSelection(selectedTransactions);
    if (applicable.length === 0) return;

    if (!isConnected) {
      // Offline: pro Buchung ein Queue-Eintrag. Eine Servervorschau gibt es
      // hier nicht - dafuer bleibt jede Buchung einzeln entscheidbar, falls
      // beim Uebertragen ein Konflikt auftritt (Mehr - Sync-Konflikte).
      try {
        const count = await queueBulkCategory.mutateAsync({
          categoryId,
          transactions: applicable.map((transaction) => ({
            id: transaction.id,
            version: transaction.version,
          })),
        });
        exitSelection();
        Alert.alert(
          "Offline vorgemerkt",
          `${count} Buchung(en) werden übertragen, sobald wieder eine Verbindung besteht. Split-Buchungen und zwischenzeitliche Änderungen meldet der Server dann einzeln unter "Mehr - Sync-Konflikte".`,
        );
      } catch (cause) {
        Alert.alert(
          "Zuordnung fehlgeschlagen",
          cause instanceof Error ? cause.message : "Unbekannter Fehler.",
        );
      }
      return;
    }

    const input: BulkCategoryInput = {
      transaction_ids: applicable.map((transaction) => transaction.id),
      category_id: categoryId,
      expected_versions: Object.fromEntries(
        applicable.map((transaction) => [transaction.id, transaction.version]),
      ),
    };

    try {
      // Erst die Servervorschau: "wird geaendert", "bereits so zugeordnet",
      // "Split-Buchung", "Versionskonflikt", "archiviert".
      const preview = await bulkPreview.mutateAsync(input);
      const summary = summarizeBulkResult(preview);
      const droppedHint =
        droppedCount > 0
          ? `\n\n${droppedCount} Buchung(en) über der Grenze von 500 bleiben unverändert.`
          : "";
      Alert.alert(
        "Vorschau der Zuordnung",
        `${summary.headline}` +
          (summary.reasonLines.length > 0 ? `\n\n${summary.reasonLines.join("\n")}` : "") +
          `\n\nSplit-Buchungen werden nie pauschal geändert.${droppedHint}`,
        [
          { text: "Abbrechen", style: "cancel" },
          {
            text: `${summary.applied} anwenden`,
            onPress: () => void applyBulk(input),
          },
        ],
      );
    } catch (cause) {
      Alert.alert(
        "Vorschau fehlgeschlagen",
        cause instanceof Error ? cause.message : "Unbekannter Fehler.",
      );
    }
  }

  async function applyBulk(input: BulkCategoryInput) {
    try {
      const result = await bulkApply.mutateAsync(input);
      const summary = summarizeBulkResult(result);
      exitSelection();
      Alert.alert(
        "Zuordnung abgeschlossen",
        `${summary.headline}` +
          (summary.reasonLines.length > 0 ? `\n\n${summary.reasonLines.join("\n")}` : ""),
      );
    } catch (cause) {
      Alert.alert(
        "Zuordnung fehlgeschlagen",
        cause instanceof Error ? cause.message : "Unbekannter Fehler.",
      );
    }
  }

  function handleRemoveCategory() {
    if (selectedTransactions.length === 0) return;
    Alert.alert(
      "Kategorie entfernen?",
      `${selectedTransactions.length} Buchung(en) stehen danach auf "Ohne Kategorie" und landen in der Prüfung.`,
      [
        { text: "Abbrechen", style: "cancel" },
        { text: "Entfernen", style: "destructive", onPress: () => void assignBulk(null) },
      ],
    );
  }

  // --- Loeschen ------------------------------------------------------------

  function handleDeleteSwipe(transaction: Transaction) {
    const label = transaction.payee_label || transaction.note || transaction.booked_date;
    Alert.alert(
      "Buchung löschen?",
      `"${label}" wird gelöscht. Das kann nicht rückgängig gemacht werden.`,
      [
        { text: "Abbrechen", style: "cancel" },
        {
          text: "Löschen",
          style: "destructive",
          onPress: () => void performDelete(transaction.id),
        },
      ],
    );
  }

  async function performDelete(transactionId: string) {
    try {
      await deleteTransactionMutation.mutateAsync(transactionId);
    } catch (cause) {
      Alert.alert(
        "Buchung konnte nicht gelöscht werden",
        cause instanceof Error ? cause.message : "Unbekannter Fehler.",
      );
    }
  }

  function rowActions(transaction: Transaction): SwipeAction[] {
    // Noch nicht uebertragene Neuanlagen existieren serverseitig nicht - es
    // gibt keine ID, gegen die Kategorie- oder Loeschaktionen laufen koennten.
    if (isPendingTransaction(transaction) || selectionMode) return [];
    return [
      // *Deep statt *Strong: die Beschriftung ist weiss und erreicht auf den
      // hellen Fuellfarben (amber500/coral500) nur 2,2 bzw. 3,8:1 - ausgerechnet
      // die destruktive Aktion waere damit die am schlechtesten lesbare der App.
      {
        key: "category",
        label: "Kategorie",
        backgroundColor: theme.highlightDeep,
        textColor: theme.onAccent,
        onPress: () => handleCategorySwipe(transaction),
      },
      {
        key: "delete",
        label: "Löschen",
        backgroundColor: theme.warningDeeper,
        textColor: theme.onAccent,
        onPress: () => handleDeleteSwipe(transaction),
      },
    ];
  }

  function handleEndReached() {
    if (hasNextPage && !isFetchingNextPage && !isLoading) void fetchNextPage();
  }

  const pickerValue = picker?.kind === "single" ? (picker.transaction.category_id ?? null) : null;
  const pickerAmount = picker?.kind === "single" ? picker.transaction.amount_minor : null;

  const monthLabel = formatMonthLabel(new Date(year, month - 1, 1));
  const showLoadingState = isLoading;
  const showErrorState = !isLoading && isError;
  const showEmpty = !isLoading && !isError && transactions.length === 0;
  const showFilterEmptyState = showEmpty && (filtersActive || isSearching);
  const showPlainEmptyState = showEmpty && !filtersActive && !isSearching;
  const showPopulated = !isLoading && !isError && transactions.length > 0;

  // Erklaert, warum der Monatsnavigator gerade nichts einschraenkt. Ohne
  // Hinweis wirkt eine Suche ueber alle Zeitraeume wie ein Fehler des Filters.
  const headerSubtitle = isSearching
    ? "Suche gilt für alle Zeiträume"
    : dateScope === "all"
      ? "Alle Zeiträume"
      : undefined;

  /** Eine Zeile der Liste inklusive Wischaktionen, Auswahl und Detailsprung. */
  function renderTransaction(transaction: Transaction, isLast: boolean) {
    const pending = isPendingTransaction(transaction);
    const selected = selectedIds.includes(transaction.id);
    const accountName = accountNameById.get(transaction.account_id) ?? "Unbekanntes Konto";
    const title = transactionTitle(transaction, accountName);

    const row = (
      <TransactionRow
        transaction={transaction}
        accountName={accountName}
        categoryName={
          transaction.category_id ? (categoryNameById.get(transaction.category_id) ?? null) : null
        }
        divider={!isLast}
      />
    );

    // Die Detailseite laedt ueber GET /v1/transactions/{id} - fuer eine noch
    // nicht uebertragene Neuanlage gibt es dort nichts zu holen. Den Zustand
    // benennt der Marker "Vorgemerkt" in der Zeile selbst.
    if (pending) {
      return (
        <View key={transaction.id} style={styles.pendingRow}>
          {row}
        </View>
      );
    }

    return (
      <SwipeableRow
        key={transaction.id}
        actions={rowActions(transaction)}
        backgroundColor={theme.surface}
      >
        <Pressable
          accessibilityRole={selectionMode ? "checkbox" : "button"}
          accessibilityState={selectionMode ? { checked: selected } : undefined}
          accessibilityLabel={selectionMode ? `${title} auswählen` : `${title} öffnen`}
          onPress={() => {
            if (selectionMode) toggleSelected(transaction.id);
            else router.push(`/buchung/${transaction.id}`);
          }}
          onLongPress={() => enterSelection(transaction)}
          delayLongPress={350}
          style={({ pressed }) => [
            styles.rowMain,
            selected ? { backgroundColor: theme.positiveMuted } : null,
            pressed ? { backgroundColor: theme.surfaceHover } : null,
          ]}
        >
          {selectionMode ? (
            <View
              style={[
                styles.checkboxBox,
                {
                  borderColor: selected ? theme.primaryStrong : theme.borderStrong,
                  backgroundColor: selected ? theme.primaryStrong : "transparent",
                },
              ]}
            >
              {selected ? <Ionicons name="checkmark" size={16} color={theme.onAccent} /> : null}
            </View>
          ) : null}
          <View style={styles.rowBody}>{row}</View>
        </Pressable>
      </SwipeableRow>
    );
  }

  return (
    <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
      <ScreenHeader
        title="Buchungen"
        subtitle={headerSubtitle}
        actions={
          <>
            <IconButton
              icon="options-outline"
              onPress={openFilterSheet}
              // Der Zaehler steht nur im Label - welche Filter greifen, sagen
              // die Pillen unter der Suche im Klartext.
              accessibilityLabel={
                activeFilterCount > 0 ? `Filter, ${activeFilterCount} aktiv` : "Filter"
              }
              tone={filtersActive ? "positive" : undefined}
            />
            <IconButton
              icon="add"
              onPress={() => router.push("/mehr/import")}
              accessibilityLabel="Kontoauszug importieren"
            />
            <IconButton
              icon="copy-outline"
              onPress={() => router.push("/mehr/review")}
              accessibilityLabel="Duplikate prüfen"
            />
          </>
        }
      />

      <View style={styles.toolbar}>
        <SearchField
          value={search}
          onChangeText={setSearch}
          placeholder="Empfänger, Zweck oder Notiz suchen"
          accessibilityLabel="Buchungen durchsuchen"
        />
        <MonthNavigator
          year={year}
          month={month}
          onChange={(y, m) => {
            setYear(y);
            setMonth(m);
          }}
        />
        <View style={styles.chipRow}>
          <Chip
            label={`Sortierung: ${SORT_LABELS[sort]}`}
            onPress={() => setSortSheetOpen(true)}
            accessibilityLabel={`Sortierung, aktuell ${SORT_LABELS[sort]}`}
          />
        </View>
        {filtersActive ? (
          <View style={styles.filterChipBlock}>
            <CardLabel>Aktive Filter · zum Entfernen tippen</CardLabel>
            <View style={styles.chipRow}>
              {chips.map((chip) => (
                <Chip
                  key={chip.key}
                  label={chip.label}
                  selected
                  onPress={chip.onRemove}
                  accessibilityLabel={`${chip.label}, Filter entfernen`}
                />
              ))}
            </View>
          </View>
        ) : null}
      </View>

      {selectionMode ? (
        <Card density="compact" accentEdge="info" style={styles.selectionBar}>
          <Text style={[styles.selectionCount, { color: theme.textPrimary }]}>
            {`${selectedIds.length} ausgewählt`}
          </Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={selectAllLabel(selectableTransactions.length)}
            onPress={selectAllVisible}
            hitSlop={8}
            style={({ pressed }) => [styles.textAction, { opacity: pressed ? 0.6 : 1 }]}
          >
            <Text style={[styles.textActionLabel, { color: theme.positive }]}>
              {selectAllLabel(selectableTransactions.length)}
            </Text>
          </Pressable>
        </Card>
      ) : null}

      {showLoadingState ? (
        <Card style={styles.stateCard} padding={space.sm}>
          <InlineQueryState
            status="loading"
            skeletonRows={6}
            skeletonRowHeight={44}
            testID="buchungen-loading"
          />
        </Card>
      ) : null}

      {showErrorState ? (
        <View style={styles.stateBlock}>
          <InlineQueryState
            status="error"
            title="Buchungen konnten nicht geladen werden"
            message={error instanceof Error ? error.message : undefined}
            onRetry={() => void refetch()}
            testID="buchungen-error"
          />
        </View>
      ) : null}

      {showFilterEmptyState ? (
        <View style={styles.emptyStateBlock}>
          <InlineQueryState
            status="empty"
            title="Keine Treffer für diese Filter"
            message="Passe Suche, Filter oder Sortierung an, um mehr Buchungen zu sehen."
            testID="buchungen-filter-empty"
          />
          <View style={styles.emptyStateAction}>
            <PrimaryButton
              label="Filter zurücksetzen"
              onPress={resetFiltersAndSearch}
              variant="secondary"
            />
          </View>
        </View>
      ) : null}

      {showPlainEmptyState ? (
        <View style={styles.emptyStateBlock} testID="buchungen-empty">
          <EmptyState
            title={
              dateScope === "month" ? `Keine Buchungen im ${monthLabel}` : "Noch keine Buchungen"
            }
            description="Neue Buchungen kommen über den Kontoauszug-Import in die App."
          />
          <View style={styles.emptyStateAction}>
            <PrimaryButton
              label="Kontoauszug importieren"
              onPress={() => router.push("/mehr/import")}
            />
          </View>
        </View>
      ) : null}

      {showPopulated ? (
        <SectionList<TransactionCard, Section>
          sections={sections}
          keyExtractor={(card, index) => card[0]?.id ?? `karte-${index}`}
          stickySectionHeadersEnabled
          contentContainerStyle={styles.listContent}
          renderSectionHeader={({ section }) =>
            // Die klebende Ueberschrift braucht einen eigenen, deckenden Grund -
            // sonst scrollen die Karten sichtbar durch den Text hindurch.
            section.title ? (
              <View style={{ backgroundColor: theme.background }}>
                <SectionHeader title={section.title} />
              </View>
            ) : null
          }
          onEndReached={handleEndReached}
          onEndReachedThreshold={0.4}
          ListFooterComponent={
            isFetchingNextPage ? (
              <View style={styles.loadingRow}>
                <ActivityIndicator color={theme.textSecondary} />
                <Text style={{ color: theme.textSecondary }}>Lade weitere Buchungen...</Text>
              </View>
            ) : null
          }
          renderItem={({ item: card }) => (
            <Card padding={0} style={styles.dayCard}>
              {card.map((transaction, index) =>
                renderTransaction(transaction, index === card.length - 1),
              )}
            </Card>
          )}
          refreshing={isFetching && !isLoading && !isFetchingNextPage}
          onRefresh={() => void refetch()}
        />
      ) : null}

      {selectionMode ? (
        <View
          style={[
            styles.actionBar,
            { borderTopColor: theme.border, backgroundColor: theme.chrome },
          ]}
        >
          <PrimaryButton
            label="Kategorie zuweisen"
            onPress={() => setPicker({ kind: "bulk" })}
            disabled={selectedIds.length === 0}
          />
          <View style={styles.actionBarSecondaryRow}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Kategorie der Auswahl entfernen"
              accessibilityState={{ disabled: selectedIds.length === 0 }}
              disabled={selectedIds.length === 0}
              onPress={handleRemoveCategory}
              style={({ pressed }) => [
                styles.textAction,
                styles.textActionWide,
                { opacity: selectedIds.length === 0 ? 0.5 : pressed ? 0.6 : 1 },
              ]}
            >
              <Text style={[styles.textActionLabel, { color: theme.warning }]}>
                Kategorie entfernen
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Auswahl aufheben"
              onPress={exitSelection}
              style={({ pressed }) => [
                styles.textAction,
                styles.textActionWide,
                { opacity: pressed ? 0.6 : 1 },
              ]}
            >
              <Text style={[styles.textActionLabel, { color: theme.textSecondary }]}>
                Auswahl aufheben
              </Text>
            </Pressable>
          </View>
        </View>
      ) : null}

      <CategoryPickerModal
        visible={picker !== null}
        groups={groups}
        value={pickerValue}
        amountMinor={pickerAmount}
        onChange={(categoryId) => {
          const target = picker;
          setPicker(null);
          if (!target) return;
          if (target.kind === "single") void assignSingle(target.transaction, categoryId);
          else void assignBulk(categoryId);
        }}
        onClose={() => setPicker(null)}
        title={
          picker?.kind === "bulk"
            ? `Kategorie für ${selectedIds.length} Buchung(en)`
            : "Kategorie ändern"
        }
      />

      <CategoryPickerModal
        visible={categoryFilterPickerOpen}
        groups={groups}
        value={draftUncategorized ? null : (draftCategoryId ?? null)}
        onChange={handleCategoryFilterPickerChange}
        onClose={closeCategoryFilterPicker}
        title="Kategorie filtern"
      />

      <FilterSheet
        visible={filterSheetOpen}
        title="Buchungen filtern"
        onClose={() => setFilterSheetOpen(false)}
        onReset={resetFilters}
        onApply={applyFilters}
        resetDisabled={draftResetDisabled}
      >
        <View style={styles.filterSection}>
          <CardLabel>Zeitraum</CardLabel>
          <OptionList
            options={DATE_SCOPE_OPTIONS}
            value={draftDateScope}
            onChange={setDraftDateScope}
          />
          {draftDateScope === "month" ? (
            <Text style={[styles.filterHint, { color: theme.textMuted }]}>
              {`Aktuell gewählt: ${monthLabel} (im Monatsnavigator änderbar)`}
            </Text>
          ) : null}
        </View>

        <View style={styles.filterSection}>
          <CardLabel>Konto</CardLabel>
          <OptionList
            options={[
              { value: ACCOUNT_FILTER_ANY, label: "Alle Konten" },
              ...(accounts ?? []).map((account) => ({ value: account.id, label: account.name })),
            ]}
            value={draftAccountId ?? ACCOUNT_FILTER_ANY}
            onChange={(value) =>
              setDraftAccountId(value === ACCOUNT_FILTER_ANY ? undefined : value)
            }
          />
        </View>

        <View style={styles.filterSection}>
          <CardLabel>Kategorie</CardLabel>
          <DataRow
            title={categoryFilterLabel}
            subtitle="Kategorie auswählen"
            avatarIcon="pricetag-outline"
            avatarTone="neutral"
            showChevron
            divider={false}
            onPress={openCategoryFilterPicker}
            accessibilityLabel={`Kategorie, aktuell ${categoryFilterLabel}`}
          />
        </View>

        <View style={styles.filterSection}>
          <CardLabel>Richtung</CardLabel>
          <OptionList
            options={DIRECTION_OPTIONS}
            value={draftDirection ?? DIRECTION_FILTER_ANY}
            onChange={(value) =>
              setDraftDirection(
                value === DIRECTION_FILTER_ANY ? undefined : (value as TransactionDirection),
              )
            }
          />
        </View>
      </FilterSheet>

      <SortSheet
        visible={sortSheetOpen}
        title="Buchungen sortieren"
        onClose={() => setSortSheetOpen(false)}
      >
        {SORT_ORDER.map((value, index) => (
          <DataRow
            key={value}
            title={SORT_LABELS[value]}
            avatarIcon={SORT_ICONS[value]}
            avatarTone="neutral"
            titleBadge={
              value === sort ? <Badge label="Aktiv" tone="positive" size="small" /> : undefined
            }
            divider={index < SORT_ORDER.length - 1}
            accessibilityLabel={
              value === sort
                ? `${SORT_LABELS[value]}, aktive Sortierung`
                : `Nach ${SORT_LABELS[value]} sortieren`
            }
            onPress={() => {
              setSort(value);
              setSortSheetOpen(false);
            }}
          />
        ))}
      </SortSheet>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  toolbar: {
    paddingHorizontal: space.lg,
    paddingTop: space.sm,
    paddingBottom: space.sm,
    gap: space.sm,
  },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: space.sm,
  },
  filterChipBlock: {
    gap: space.xs,
    paddingTop: space.xs,
  },
  selectionBar: {
    marginHorizontal: space.lg,
    marginBottom: space.sm,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  selectionCount: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  listContent: {
    paddingBottom: space.xxl,
  },
  dayCard: {
    marginHorizontal: space.lg,
    marginBottom: space.md,
    paddingHorizontal: space.lg,
    gap: 0,
  },
  rowMain: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  rowBody: {
    flex: 1,
  },
  checkboxBox: {
    width: 24,
    height: 24,
    borderWidth: 2,
    borderRadius: radii.xs,
    alignItems: "center",
    justifyContent: "center",
  },
  pendingRow: {
    opacity: 0.7,
  },
  loadingRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.sm,
    padding: space.lg,
  },
  stateCard: {
    marginHorizontal: space.lg,
  },
  stateBlock: {
    paddingHorizontal: space.lg,
  },
  emptyStateBlock: {
    flexGrow: 1,
    gap: space.md,
    paddingHorizontal: space.lg,
  },
  emptyStateAction: {
    paddingHorizontal: space.xl,
  },
  filterSection: {
    gap: space.sm,
  },
  filterHint: {
    fontSize: typeScale.caption,
  },
  actionBar: {
    gap: space.sm,
    padding: space.lg,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  actionBarSecondaryRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  textAction: {
    minHeight: MIN_TOUCH,
    alignItems: "center",
    justifyContent: "center",
  },
  textActionWide: {
    flex: 1,
  },
  textActionLabel: {
    fontSize: typeScale.body,
    fontWeight: "600",
    textAlign: "center",
  },
});
