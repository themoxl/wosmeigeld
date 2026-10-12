import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useMemo, useState } from "react";
import {
  Pressable,
  RefreshControl,
  ScrollView,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native";
import type { CategoryBudgetStatus, CategoryGroupBudgetStatus } from "../../src/api/types";
import { BudgetStrategyCard } from "../../src/components/BudgetStrategyCard";
import { CategoryDrilldownSheet } from "../../src/components/CategoryDrilldownSheet";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { MonthNavigator } from "../../src/components/MonthNavigator";
import { OptionList, type Option } from "../../src/components/OptionList";
import { ProgressBar } from "../../src/components/ProgressBar";
import { ScreenContainer, SCREEN_EDGES_WITH_TOP } from "../../src/components/ScreenContainer";
import { SegmentedControl } from "../../src/components/SegmentedControl";
import { SectionHeader } from "../../src/components/SectionHeader";
import { SheetModal } from "../../src/components/SheetModal";
import { ReserveCompactStrip, ReserveLedger } from "../../src/components/reserves/ReserveLedger";
import {
  Avatar,
  Badge,
  Card,
  CardLabel,
  Money,
  PacingBar,
  ScreenHeader,
} from "../../src/components/ui";
import { useBudgetSummary, useUpdateBudgetAllocations } from "../../src/hooks/useBudget";
import { useReservePlanningPreference } from "../../src/hooks/useReservePlanningPreference";
import { useReserveOverview } from "../../src/hooks/useReserves";
import { space, typeScale, useTheme, type AccentTone } from "../../src/theme";
import {
  calcProgress,
  describeBudgetOverview,
  describeExpenseCategory,
  describeExpenseGroup,
  describeIncomeCategory,
} from "../../src/utils/budgetPresentation";
import { BUDGET_REMAINING_LABEL, INCOME_STILL_EXPECTED_LABEL } from "../../src/utils/financeLabels";
import {
  formatMinorUnits,
  formatMinorUnitsForInput,
  parseAmountToMinorUnits,
} from "../../src/utils/money";

// Umschlag-Ansicht des Showcase (Abschnitt "3. SCREEN: BUDGET & ENVELOPES"):
// eine Karte je Kategorie mit Kachel, Statuspille, Fortschritt samt Soll-Nadel
// und darunter "Ausgegeben"/"Plan". Die fachliche Gliederung des Bestands
// bleibt vollstaendig erhalten - Einnahmen und Ausgaben getrennt, Gruppen mit
// Summenzeile, Uebertrag als Teil der Planbasis, geschlossene Monate
// schreibgeschuetzt.

type CategoryFilterMode = "active" | "everActive" | "all";
type BudgetView = "monthly" | "reserves";

const BUDGET_VIEW_OPTIONS = [
  { value: "monthly" as const, label: "Monatsbudget" },
  { value: "reserves" as const, label: "Rücklagen" },
];

// Wortlaut im Sheet ("Diesen Monat") vs. in der kompakten Zeile
// ("Anzeigen: Dieser Monat") unterscheidet sich grammatisch (Akkusativ vs.
// Nominativ) - beide muessen deshalb getrennt gepflegt werden.
const CATEGORY_FILTER_OPTION_LABEL: Record<CategoryFilterMode, string> = {
  active: "Diesen Monat",
  everActive: "Jemals",
  all: "Alle",
};

const CATEGORY_FILTER_DISPLAY_LABEL: Record<CategoryFilterMode, string> = {
  active: "Dieser Monat",
  everActive: "Jemals",
  all: "Alle",
};

const CATEGORY_FILTER_OPTIONS: Option<CategoryFilterMode>[] = (
  Object.keys(CATEGORY_FILTER_OPTION_LABEL) as CategoryFilterMode[]
).map((mode) => ({ value: mode, label: CATEGORY_FILTER_OPTION_LABEL[mode] }));

/**
 * Direkter Port von apps/web/src/pages/BudgetPage.tsx (hasMonthlyActivity):
 * eine Kategorie zaehlt als "aktiv", sobald irgendetwas mit ihr passiert -
 * geplant, ein Rollover, eine Buchung oder ein faelliger Vertragsforecast.
 * Bewusst inklusive contract_forecast_minor, sonst waere eine im Monat
 * faellige Jahresversicherung ausgerechnet in dem Monat unsichtbar, in dem
 * der Forecast am meisten bringt.
 */
function hasMonthlyActivity(category: CategoryBudgetStatus): boolean {
  return (
    category.planned_minor !== 0 ||
    category.rollover_minor !== 0 ||
    category.spent_minor !== 0 ||
    category.income_actual_minor !== 0 ||
    category.contract_forecast_minor !== 0
  );
}

/** "everActive" rettet saisonale Kategorien (z. B. eine jaehrliche
 * Versicherung) in den Monaten, in denen nichts passiert - has_activity_ever
 * kommt vorberechnet vom Backend (Lebenszeit, nicht nur dieses Jahr). */
function matchesCategoryFilter(
  category: CategoryBudgetStatus,
  filter: CategoryFilterMode,
): boolean {
  if (filter === "all") return true;
  if (filter === "everActive") return hasMonthlyActivity(category) || category.has_activity_ever;
  return hasMonthlyActivity(category);
}

function isIncomeCategory(category: CategoryBudgetStatus): boolean {
  return category.default_type === "income";
}

type MonthPace = { fraction: number; label: string };

/**
 * Soll-Nadel der Umschlagkarten: wie weit der Monat heute ist.
 *
 * Nur der laufende Monat bekommt eine Nadel. In einem vergangenen Monat stuende
 * sie immer bei 100 %, in einem kuenftigen bei 0 - beides ist kein Vergleich,
 * sondern nur ein Strich.
 */
function describeMonthPace(year: number, month: number, now = new Date()): MonthPace | undefined {
  if (now.getFullYear() !== year || now.getMonth() + 1 !== month) return undefined;
  const daysInMonth = new Date(year, month, 0).getDate();
  const day = now.getDate();
  return { fraction: day / daysInMonth, label: `Tag ${day} von ${daysInMonth}` };
}

/** Statuspille einer Ausgabenkategorie: Text zuerst, Farbe nur als Verstaerkung. */
function expenseBadge(
  hasPlan: boolean,
  isSurplus: boolean,
  percentUsed: number,
): { label: string; tone: AccentTone } {
  if (isSurplus) return { label: "Mehr erstattet", tone: "positive" };
  if (!hasPlan) return { label: "Kein Plan", tone: "neutral" };
  if (percentUsed > 100) return { label: `${percentUsed} % aufgebraucht`, tone: "warning" };
  if (percentUsed >= 80) return { label: `${percentUsed} % aufgebraucht`, tone: "highlight" };
  return { label: `${percentUsed} % aufgebraucht`, tone: "positive" };
}

export default function BudgetScreen() {
  const theme = useTheme();
  const router = useRouter();

  const now = new Date();
  const [selectedYear, setSelectedYear] = useState(now.getFullYear());
  const [selectedMonth, setSelectedMonth] = useState(now.getMonth() + 1);
  const [activeView, setActiveView] = useState<BudgetView>("monthly");

  const {
    data: budget,
    isLoading,
    isError,
    error,
    isFetching,
    refetch,
  } = useBudgetSummary(selectedYear, selectedMonth);
  const reserveQuery = useReserveOverview(selectedYear, selectedMonth, activeView === "reserves");
  const { includeInPlan, setIncludeInPlan } = useReservePlanningPreference();
  const updateAllocationsMutation = useUpdateBudgetAllocations(selectedYear, selectedMonth);
  const isClosed = budget?.status === "closed";

  // Edit Sheet State - immer nur eine Kategorie gleichzeitig in Bearbeitung.
  const [editingCategory, setEditingCategory] = useState<CategoryBudgetStatus | null>(null);
  const [inputAmount, setInputAmount] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  const [categoryFilter, setCategoryFilter] = useState<CategoryFilterMode>("active");
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);

  const pace = useMemo(
    () => describeMonthPace(selectedYear, selectedMonth),
    [selectedYear, selectedMonth],
  );

  const { incomeGroups, expenseGroups, hiddenCount } = useMemo(() => {
    const groups = budget?.groups ?? [];
    const buildGroups = (predicate: (cat: CategoryBudgetStatus) => boolean) =>
      groups
        .map((group) => ({
          ...group,
          categories: group.categories.filter(
            (cat) => predicate(cat) && matchesCategoryFilter(cat, categoryFilter),
          ),
        }))
        .filter((group) => group.categories.length > 0);

    const totalCount = groups.reduce((sum, group) => sum + group.categories.length, 0);
    const shownCount = groups.reduce(
      (sum, group) =>
        sum + group.categories.filter((cat) => matchesCategoryFilter(cat, categoryFilter)).length,
      0,
    );

    return {
      incomeGroups: buildGroups(isIncomeCategory),
      expenseGroups: buildGroups((cat) => !isIncomeCategory(cat)),
      hiddenCount: totalCount - shownCount,
    };
  }, [budget, categoryFilter]);

  const sections = useMemo(
    () =>
      [
        { key: "income", title: "Einnahmen", variant: "income" as const, data: incomeGroups },
        { key: "expense", title: "Ausgaben", variant: "expense" as const, data: expenseGroups },
      ].filter((section) => section.data.length > 0),
    [incomeGroups, expenseGroups],
  );

  const openEditModal = (category: CategoryBudgetStatus) => {
    if (isClosed) return;
    setEditingCategory(category);
    setInputAmount(formatMinorUnitsForInput(category.planned_minor));
    setEditError(null);
  };

  const closeEditModal = () => {
    setEditingCategory(null);
    setInputAmount("");
    setEditError(null);
  };

  const handleSaveAllocation = async (customAmount?: string) => {
    if (!editingCategory || !budget) return;

    try {
      setEditError(null);
      const strToParse = customAmount !== undefined ? customAmount : inputAmount;
      const newPlannedMinor = parseAmountToMinorUnits(strToParse);
      if (newPlannedMinor < 0) {
        setEditError("Planbetrag darf nicht negativ sein.");
        return;
      }

      // Bestehende Allokationen sammeln und die bearbeitete Kategorie ersetzen/ergaenzen.
      const existingMap = new Map<string, number>();
      for (const group of budget.groups) {
        for (const cat of group.categories) {
          existingMap.set(cat.category_id, cat.planned_minor);
        }
      }
      existingMap.set(editingCategory.category_id, newPlannedMinor);

      const allocationsPayload = Array.from(existingMap.entries()).map(([catId, planned]) => ({
        category_id: catId,
        planned_minor: planned,
      }));

      await updateAllocationsMutation.mutateAsync(allocationsPayload);
      closeEditModal();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Fehler beim Speichern des Budgets.");
    }
  };

  const overview = budget ? describeBudgetOverview(budget) : null;
  const hasUncategorized = !!budget && budget.uncategorized_spent_minor > 0;

  const monthSwitch = (
    <>
      <MonthNavigator
        year={selectedYear}
        month={selectedMonth}
        onChange={(year, month) => {
          setSelectedYear(year);
          setSelectedMonth(month);
        }}
        style={styles.screenPad}
      />
      <SegmentedControl
        accessibilityLabel="Monatsbudget oder Rücklagen"
        options={BUDGET_VIEW_OPTIONS}
        value={activeView}
        onChange={setActiveView}
        style={styles.screenPad}
      />
    </>
  );

  if (activeView === "reserves") {
    return (
      <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
        <ScrollView
          refreshControl={
            <RefreshControl
              refreshing={isFetching || reserveQuery.isFetching}
              onRefresh={() => {
                void refetch();
                void reserveQuery.refetch();
              }}
            />
          }
          contentContainerStyle={styles.reserveContent}
          keyboardShouldPersistTaps="handled"
        >
          <ScreenHeader title="Rücklagen" subtitle="Zweckgebundenes Geld je Monat" />
          {monthSwitch}
          <ReserveLedger
            year={selectedYear}
            month={selectedMonth}
            budget={budget}
            overview={reserveQuery.data}
            isLoading={reserveQuery.isLoading}
            isError={reserveQuery.isError}
            onRetry={() => void reserveQuery.refetch()}
            includeInPlan={includeInPlan}
            onIncludeInPlanChange={setIncludeInPlan}
          />
        </ScrollView>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
      <SectionList
        sections={sections}
        keyExtractor={(item, index) => `${item.group_id}_${index}`}
        stickySectionHeadersEnabled={false}
        refreshControl={<RefreshControl refreshing={isFetching} onRefresh={() => void refetch()} />}
        contentContainerStyle={styles.listContent}
        ListHeaderComponent={
          <View style={styles.headerContainer}>
            <ScreenHeader title="Monatsbudget" />
            {monthSwitch}

            {isClosed ? (
              <Card tone="warning" density="compact" style={styles.screenPad}>
                <Text
                  accessibilityLabel="Dieser Monat ist abgeschlossen und schreibgeschützt."
                  style={[styles.closedHint, { color: theme.warning }]}
                >
                  Dieser Monat ist abgeschlossen und schreibgeschützt.
                </Text>
              </Card>
            ) : null}

            {budget && overview ? (
              <Card style={styles.screenPad}>
                <View style={styles.cardHeader}>
                  <CardLabel>Monatsüberblick</CardLabel>
                  <Badge
                    label={
                      overview.isSurplus
                        ? "Mehr erstattet"
                        : `${Math.round(overview.progress * 100)} % verbraucht`
                    }
                    tone={
                      overview.progress > 1
                        ? "warning"
                        : overview.progress >= 0.8
                          ? "highlight"
                          : "positive"
                    }
                  />
                </View>

                <View style={styles.summaryRow}>
                  <View style={styles.summaryBlock}>
                    <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
                      {overview.isSurplus ? "Mehr erstattet als ausgegeben" : "Netto ausgegeben"}
                    </Text>
                    <Money
                      minorUnits={
                        overview.isSurplus
                          ? Math.abs(budget.total_spent_minor)
                          : budget.total_spent_minor
                      }
                      size="display"
                      tone={overview.isSurplus ? "positive" : "plain"}
                    />
                  </View>
                  <View style={[styles.summaryBlock, styles.alignEnd]}>
                    <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
                      {overview.planBasis.basisLabel}
                    </Text>
                    <Money minorUnits={overview.planBasis.totalMinor} tone="plain" />
                  </View>
                </View>

                <PacingBar
                  progress={overview.progress}
                  pace={pace?.fraction}
                  tone={
                    overview.progress > 1
                      ? "warning"
                      : overview.progress >= 0.8
                        ? "highlight"
                        : "positive"
                  }
                  accessibilityLabel="Budgetfortschritt insgesamt"
                />
                {/* Einmalige Legende fuer die Soll-Nadel: sie steht in jedem
                    Balken dieser Seite, muss aber nur einmal erklaert werden. */}
                {pace ? (
                  <Text style={[styles.footnote, { color: theme.textMuted }]}>
                    {`Nadel in allen Balken: ${pace.label}`}
                  </Text>
                ) : null}

                <View style={styles.inlineRow}>
                  <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
                    {BUDGET_REMAINING_LABEL}
                  </Text>
                  <Money
                    minorUnits={overview.remainingMinor}
                    tone={overview.remainingIsNegative ? "warning" : "positive"}
                  />
                </View>
              </Card>
            ) : null}

            {budget ? <BudgetStrategyCard budget={budget} /> : null}

            {budget && includeInPlan ? (
              <ReserveCompactStrip budget={budget} onOpen={() => setActiveView("reserves")} />
            ) : null}

            {hasUncategorized && budget ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${formatMinorUnits(budget.uncategorized_spent_minor)} Ausgaben ohne Kategorie. Öffnet die Buchungsliste, gefiltert auf ohne Kategorie.`}
                onPress={() => router.push("/buchungen?uncategorized=true")}
                style={({ pressed }) => [styles.screenPad, { opacity: pressed ? 0.75 : 1 }]}
              >
                <Card tone="warning" density="compact" style={styles.inlineRow}>
                  <Text
                    style={[styles.uncategorizedText, { color: theme.warning }]}
                    numberOfLines={2}
                  >
                    Ausgaben ohne Kategorie · Buchungen ansehen
                  </Text>
                  <Money
                    minorUnits={budget.uncategorized_spent_minor}
                    tone="warning"
                    numberOfLines={1}
                  />
                </Card>
              </Pressable>
            ) : null}

            {budget ? (
              <View style={styles.filterSection}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Anzeigen: ${CATEGORY_FILTER_DISPLAY_LABEL[categoryFilter]}. Öffnet die Auswahl des Anzeigezeitraums.`}
                  onPress={() => setFilterSheetOpen(true)}
                  style={({ pressed }) => [{ opacity: pressed ? 0.75 : 1 }]}
                >
                  <Card density="compact" style={styles.filterRow}>
                    <Text style={[styles.filterRowLabel, { color: theme.textPrimary }]}>
                      {`Anzeigen: ${CATEGORY_FILTER_DISPLAY_LABEL[categoryFilter]}`}
                    </Text>
                    <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
                  </Card>
                </Pressable>
                {hiddenCount > 0 ? (
                  <Text style={[styles.hiddenHint, { color: theme.textMuted }]}>
                    {hiddenCount} ausgeblendet
                  </Text>
                ) : null}
              </View>
            ) : null}
          </View>
        }
        renderSectionHeader={({ section }) => <SectionHeader title={section.title} />}
        renderItem={({ item: group, section }) => (
          <GroupBlock
            group={group}
            variant={section.variant}
            isClosed={isClosed}
            pace={pace}
            onEditCategory={openEditModal}
          />
        )}
        ListEmptyComponent={
          isLoading ? (
            <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={56} />
          ) : isError ? (
            <InlineQueryState
              status="error"
              message={
                error instanceof Error ? error.message : "Budget konnte nicht geladen werden."
              }
              onRetry={() => void refetch()}
            />
          ) : !budget ? (
            <InlineQueryState
              status="empty"
              title="Kein Budget für diesen Monat"
              message="Es sind noch keine Kategoriegruppen verfügbar."
            />
          ) : (
            <InlineQueryState
              status="empty"
              title="Keine Kategorien mit Bewegung"
              message={`Für "${CATEGORY_FILTER_DISPLAY_LABEL[categoryFilter]}" gibt es nichts zu zeigen - "Alle" zeigt auch ungenutzte Kategorien.`}
            />
          )
        }
      />

      <SheetModal
        visible={filterSheetOpen}
        title="Anzeigen"
        onClose={() => setFilterSheetOpen(false)}
      >
        <View style={styles.filterSheetBody}>
          <OptionList
            options={CATEGORY_FILTER_OPTIONS}
            value={categoryFilter}
            onChange={(mode) => {
              setCategoryFilter(mode);
              setFilterSheetOpen(false);
            }}
          />
        </View>
      </SheetModal>

      {/* editError geht als Eigenschaft INS Sheet: das Sheet bleibt bei einem
          Fehler offen, und ein <Modal> verdeckt alles, was dieser Bildschirm
          selbst zeichnet - eine Meldung hier waere nie zu sehen. */}
      <CategoryDrilldownSheet
        category={editingCategory}
        visible={!!editingCategory}
        onClose={closeEditModal}
        onSavePlan={(amountStr) => void handleSaveAllocation(amountStr)}
        isSaving={updateAllocationsMutation.isPending}
        year={selectedYear}
        month={selectedMonth}
        pace={pace?.fraction}
        error={editError}
      />
    </ScreenContainer>
  );
}

function GroupBlock({
  group,
  variant,
  isClosed,
  pace,
  onEditCategory,
}: {
  group: CategoryGroupBudgetStatus;
  variant: "income" | "expense";
  isClosed: boolean;
  pace: MonthPace | undefined;
  onEditCategory: (cat: CategoryBudgetStatus) => void;
}) {
  const theme = useTheme();
  const expenseDisplay = variant === "expense" ? describeExpenseGroup(group) : null;

  return (
    <View style={styles.groupBlock}>
      <View style={styles.groupHeading}>
        <Text style={[styles.groupName, { color: theme.textPrimary }]}>{group.group_name}</Text>

        {expenseDisplay ? (
          <>
            <View style={styles.groupMetaRow}>
              <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
                {expenseDisplay.isSurplus ? "Mehr erstattet" : "Ist"}
              </Text>
              <Money
                minorUnits={Math.abs(group.total_spent_minor)}
                tone={expenseDisplay.isSurplus ? "positive" : "plain"}
              />
              <Text style={[styles.metaLabel, { color: theme.textMuted }]}>·</Text>
              <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
                {expenseDisplay.planBasis.basisLabel}
              </Text>
              <Money minorUnits={expenseDisplay.planBasis.totalMinor} tone="muted" />
            </View>
            <ProgressBar
              progress={expenseDisplay.progress}
              height={6}
              accessibilityLabel={`Budget ${group.group_name}`}
            />
          </>
        ) : null}
      </View>

      {group.categories.map((cat) =>
        variant === "expense" ? (
          <ExpenseCategoryCard
            key={cat.category_id}
            category={cat}
            isClosed={isClosed}
            pace={pace}
            onPress={() => onEditCategory(cat)}
          />
        ) : (
          <IncomeCategoryCard
            key={cat.category_id}
            category={cat}
            isClosed={isClosed}
            onPress={() => onEditCategory(cat)}
          />
        ),
      )}
    </View>
  );
}

/**
 * Warum ein Pressable UM die Karte statt <Card onPress>?
 *
 * Ein geschlossener Monat ist schreibgeschuetzt: die Zeile muss dann ohne
 * onPress, aber MIT accessibilityState={{disabled:true}} und ohne Hinweistext
 * angesagt werden. Beides kennt <Card> aus components/ui nicht, und die
 * Bausteine sind gesetzt - deshalb traegt hier das Pressable die
 * Bedienbarkeit und die Karte nur noch das Aussehen.
 */
function ExpenseCategoryCard({
  category,
  isClosed,
  pace,
  onPress,
}: {
  category: CategoryBudgetStatus;
  isClosed: boolean;
  pace: MonthPace | undefined;
  onPress: () => void;
}) {
  const theme = useTheme();
  const display = describeExpenseCategory(category);
  const percentUsed = Math.round(display.progress * 100);
  const badge = expenseBadge(display.planBasis.totalMinor > 0, display.isSurplus, percentUsed);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: isClosed }}
      accessibilityLabel={`${category.category_name}, ${display.headline}, ${BUDGET_REMAINING_LABEL} ${display.remainingLabel}`}
      accessibilityHint={isClosed ? undefined : "Öffnet den Planbetrag zum Bearbeiten"}
      onPress={isClosed ? undefined : onPress}
      style={({ pressed }) => [{ opacity: isClosed ? 0.6 : pressed ? 0.85 : 1 }]}
    >
      <Card>
        <View style={styles.cardHeader}>
          <View style={styles.cardTitleGroup}>
            <Avatar label={category.category_name} size={34} />
            <Text style={[styles.categoryName, { color: theme.textPrimary }]} numberOfLines={1}>
              {category.category_name}
            </Text>
          </View>
          <Badge label={badge.label} tone={badge.tone} />
        </View>

        <PacingBar
          progress={display.progress}
          pace={pace?.fraction}
          tone={badge.tone === "neutral" ? "info" : badge.tone}
          accessibilityLabel={`Budget ${category.category_name}`}
        />

        <View style={styles.inlineRow}>
          <View style={styles.summaryBlock}>
            <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
              {display.isSurplus ? "Mehr erstattet" : "Ausgegeben"}
            </Text>
            <Money
              minorUnits={Math.abs(category.spent_minor)}
              tone={display.isSurplus ? "positive" : "plain"}
            />
          </View>
          <View style={[styles.summaryBlock, styles.alignEnd]}>
            <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
              {display.planBasis.basisLabel}
            </Text>
            <Money minorUnits={display.planBasis.totalMinor} tone="plain" />
          </View>
        </View>

        <View style={styles.inlineRow}>
          <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
            {BUDGET_REMAINING_LABEL}
          </Text>
          <Money
            minorUnits={display.remainingMinor}
            tone={display.remainingIsNegative ? "warning" : "positive"}
          />
        </View>
      </Card>
    </Pressable>
  );
}

function IncomeCategoryCard({
  category,
  isClosed,
  onPress,
}: {
  category: CategoryBudgetStatus;
  isClosed: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  const display = describeIncomeCategory(category);
  const receivedPercent = Math.round(
    calcProgress(category.income_actual_minor, display.planBasis.totalMinor) * 100,
  );

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: isClosed }}
      accessibilityLabel={`${category.category_name}, ${display.headline}, ${INCOME_STILL_EXPECTED_LABEL} ${display.stillExpectedLabel}`}
      accessibilityHint={isClosed ? undefined : "Öffnet den Planbetrag zum Bearbeiten"}
      onPress={isClosed ? undefined : onPress}
      style={({ pressed }) => [{ opacity: isClosed ? 0.6 : pressed ? 0.85 : 1 }]}
    >
      <Card>
        <View style={styles.cardHeader}>
          <View style={styles.cardTitleGroup}>
            <Avatar
              label={category.category_name}
              tone="positive"
              size={34}
              icon="arrow-down-circle-outline"
            />
            <Text style={[styles.categoryName, { color: theme.textPrimary }]} numberOfLines={1}>
              {category.category_name}
            </Text>
          </View>
          {display.planBasis.totalMinor > 0 ? (
            <Badge label={`${receivedPercent} % eingegangen`} tone="info" />
          ) : null}
        </View>

        <View style={styles.inlineRow}>
          <View style={styles.summaryBlock}>
            <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>Eingegangen</Text>
            <Money minorUnits={category.income_actual_minor} tone="positive" />
          </View>
          <View style={[styles.summaryBlock, styles.alignEnd]}>
            <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>Erwartet</Text>
            <Money minorUnits={display.planBasis.totalMinor} tone="plain" />
          </View>
        </View>

        <View style={styles.inlineRow}>
          <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
            {INCOME_STILL_EXPECTED_LABEL}
          </Text>
          <Money minorUnits={category.remaining_minor} tone="plain" />
        </View>
      </Card>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  listContent: {
    paddingBottom: space.xxxl,
    gap: space.md,
  },
  headerContainer: {
    gap: space.md,
    marginBottom: space.xs,
  },
  screenPad: {
    marginHorizontal: space.lg,
  },
  reserveContent: {
    paddingBottom: space.xxxl,
    gap: space.md,
  },
  closedHint: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  cardTitleGroup: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  categoryName: {
    flexShrink: 1,
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  summaryRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: space.md,
  },
  summaryBlock: {
    gap: space.hair,
  },
  alignEnd: {
    alignItems: "flex-end",
  },
  inlineRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  metaLabel: {
    fontSize: typeScale.caption,
    fontWeight: "600",
  },
  footnote: {
    fontSize: typeScale.micro,
  },
  uncategorizedText: {
    flex: 1,
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  filterSection: {
    marginHorizontal: space.lg,
    gap: space.xs,
  },
  filterRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: 44,
  },
  filterRowLabel: {
    fontSize: typeScale.base,
    fontWeight: "600",
  },
  hiddenHint: {
    fontSize: typeScale.micro,
  },
  filterSheetBody: {
    padding: space.lg,
  },
  groupBlock: {
    marginHorizontal: space.lg,
    gap: space.sm,
  },
  groupHeading: {
    gap: space.xs,
    paddingHorizontal: space.xs,
  },
  groupName: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  groupMetaRow: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: space.xs,
  },
});
