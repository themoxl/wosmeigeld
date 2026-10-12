import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import {
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import {
  startWishlistSavingsPlan,
  updateSavingsGoal,
  updateWishlistItem,
} from "../../src/api/client";
import type {
  SavingsGoal,
  TrafficLightStatus,
  WishlistItem,
  WishlistSavingsPlanCreate,
} from "../../src/api/types";
import { useAuth } from "../../src/auth/AuthProvider";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { IsoDatePickerField } from "../../src/components/IsoDatePickerField";
import { OptionList } from "../../src/components/OptionList";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { SheetModal } from "../../src/components/SheetModal";
import { TextField } from "../../src/components/TextField";
import { Avatar, Badge, Card, CardLabel, Money, PacingBar } from "../../src/components/ui";
import { useHousehold } from "../../src/household/HouseholdProvider";
import { useSavingsGoals, useWishlistItems } from "../../src/hooks/useGoals";
import { leading, radii, space, typeScale, useTheme, type AccentTone } from "../../src/theme";
import { formatDueDate, formatMonthLabel } from "../../src/utils/dates";
import { parseAmountToMinorUnits } from "../../src/utils/money";

type PlanningMode = "target_date" | "monthly_amount";

// Leistbarkeits-Ampel der Wunschliste. Der Text traegt die Aussage, der Ton
// verstaerkt sie nur - eine Pille, die allein ueber Gruen/Rot unterscheidbar
// waere, koennte ein farbfehlsichtiger Nutzer nicht lesen.
const TRAFFIC_LIGHT_LABEL: Record<TrafficLightStatus, string> = {
  green: "Jetzt leistbar",
  yellow: "Bald leistbar",
  red: "Noch sparen",
};

const TRAFFIC_LIGHT_TONE: Record<TrafficLightStatus, AccentTone> = {
  green: "positive",
  yellow: "highlight",
  red: "warning",
};

const PLANNING_STATUS_LABEL: Record<SavingsGoal["planning_status"], string> = {
  on_track: "Im Plan",
  overdue: "Zieltermin verpasst",
  incomplete: "Plan unvollständig",
  reached: "Erreicht",
  cancelled: "Archiviert",
};

// Der Planungsstatus ist die einzige Aussage des Bildschirms, die eine Farbe
// verdient - er entscheidet, ob man handeln muss. Der Text steht in jeder
// Pille daneben, die Farbe wiederholt ihn nur.
const PLANNING_STATUS_TONE: Record<SavingsGoal["planning_status"], AccentTone> = {
  on_track: "positive",
  overdue: "warning",
  incomplete: "highlight",
  reached: "positive",
  cancelled: "neutral",
};

function defaultTargetDate(): string {
  const date = new Date();
  date.setFullYear(date.getFullYear() + 1);
  return date.toISOString().slice(0, 10);
}

export default function SparzieleScreen() {
  const theme = useTheme();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const router = useRouter();
  const [noteWish, setNoteWish] = useState<WishlistItem | null>(null);
  const [note, setNote] = useState("");
  const [planWish, setPlanWish] = useState<WishlistItem | null>(null);
  const [planningMode, setPlanningMode] = useState<PlanningMode>("target_date");
  const [targetDate, setTargetDate] = useState(defaultTargetDate);
  const [monthlyAmount, setMonthlyAmount] = useState("");
  const [startingAmount, setStartingAmount] = useState("0,00");
  const [includeInPlanning, setIncludeInPlanning] = useState(true);
  const [planError, setPlanError] = useState<string | null>(null);

  const {
    data: goals,
    isLoading: isGoalsLoading,
    isError: isGoalsError,
    isFetching: isGoalsFetching,
    refetch: refetchGoals,
  } = useSavingsGoals();
  const {
    data: wishlist,
    isLoading: isWishlistLoading,
    isError: isWishlistError,
    isFetching: isWishlistFetching,
    refetch: refetchWishlist,
  } = useWishlistItems();

  function invalidatePlanning() {
    void queryClient.invalidateQueries({ queryKey: ["goals", selectedHouseholdId] });
    void queryClient.invalidateQueries({ queryKey: ["wishlist", selectedHouseholdId] });
    void queryClient.invalidateQueries({ queryKey: ["budget", selectedHouseholdId] });
    void queryClient.invalidateQueries({ queryKey: ["reserves", selectedHouseholdId] });
  }

  const archiveGoalMutation = useMutation({
    mutationFn: (goalId: string) =>
      updateSavingsGoal(accessToken as string, selectedHouseholdId as string, goalId, {
        status: "cancelled",
      }),
    onSuccess: invalidatePlanning,
  });
  const archiveWishMutation = useMutation({
    mutationFn: (itemId: string) =>
      updateWishlistItem(accessToken as string, selectedHouseholdId as string, itemId, {
        status: "archived",
      }),
    onSuccess: invalidatePlanning,
  });
  const noteMutation = useMutation({
    mutationFn: ({ itemId, value }: { itemId: string; value: string }) =>
      updateWishlistItem(accessToken as string, selectedHouseholdId as string, itemId, {
        note: value.trim() || null,
      }),
    onSuccess: () => {
      setNoteWish(null);
      invalidatePlanning();
    },
  });
  const planMutation = useMutation({
    mutationFn: ({ itemId, input }: { itemId: string; input: WishlistSavingsPlanCreate }) =>
      startWishlistSavingsPlan(accessToken as string, selectedHouseholdId as string, itemId, input),
    onSuccess: () => {
      setPlanWish(null);
      invalidatePlanning();
    },
    onError: (error: Error) => setPlanError(error.message),
  });

  const currentGoals = (goals ?? []).filter((goal) => goal.status !== "cancelled");
  const openWishlist = [...(wishlist ?? [])]
    .filter((item) => (item.status ?? "open") === "open")
    .sort((a, b) => a.priority - b.priority);
  const isLoading = isGoalsLoading || isWishlistLoading;
  const isRefreshing = isGoalsFetching || isWishlistFetching;
  const isEmpty =
    !isLoading &&
    !isGoalsError &&
    !isWishlistError &&
    currentGoals.length === 0 &&
    openWishlist.length === 0;

  function confirmArchiveGoal(goal: SavingsGoal) {
    Alert.alert("Sparziel archivieren?", `${goal.name}\nDie Historie bleibt erhalten.`, [
      { text: "Abbrechen", style: "cancel" },
      {
        text: "Archivieren",
        style: "destructive",
        onPress: () => archiveGoalMutation.mutate(goal.id),
      },
    ]);
  }

  function confirmArchiveWish(item: WishlistItem) {
    Alert.alert("Wunsch archivieren?", `${item.name}\nDie Angaben bleiben erhalten.`, [
      { text: "Abbrechen", style: "cancel" },
      {
        text: "Archivieren",
        style: "destructive",
        onPress: () => archiveWishMutation.mutate(item.id),
      },
    ]);
  }

  function openNoteEditor(item: WishlistItem) {
    setNote(item.note ?? "");
    setNoteWish(item);
  }

  function openPlanEditor(item: WishlistItem) {
    setPlanningMode("target_date");
    setTargetDate(defaultTargetDate());
    setMonthlyAmount("");
    setStartingAmount("0,00");
    setIncludeInPlanning(true);
    setPlanError(null);
    setPlanWish(item);
  }

  function submitPlan() {
    if (!planWish) return;
    let startMinor: number;
    let monthlyMinor = 0;
    try {
      startMinor = parseAmountToMinorUnits(startingAmount);
      if (planningMode === "monthly_amount") {
        monthlyMinor = parseAmountToMinorUnits(monthlyAmount);
      }
    } catch (error) {
      setPlanError(error instanceof Error ? error.message : "Bitte gültige Beträge eingeben.");
      return;
    }
    if (startMinor < 0) {
      setPlanError("Der bestätigte Startstand darf nicht negativ sein.");
      return;
    }
    if (planningMode === "target_date" && !/^\d{4}-\d{2}-\d{2}$/.test(targetDate)) {
      setPlanError("Bitte ein Ziel im Format JJJJ-MM-TT angeben.");
      return;
    }
    if (planningMode === "monthly_amount" && monthlyMinor <= 0) {
      setPlanError("Bitte eine positive Monatsrate angeben.");
      return;
    }
    setPlanError(null);
    planMutation.mutate({
      itemId: planWish.id,
      input: {
        planning_mode: planningMode,
        target_date: planningMode === "target_date" ? targetDate : null,
        monthly_contribution_minor: planningMode === "monthly_amount" ? monthlyMinor : null,
        starting_amount_minor: startMinor,
        include_in_planning: includeInPlanning,
      },
    });
  }

  return (
    <>
      <Stack.Screen
        options={{
          title: "Sparen & Wünsche",
          headerRight: () => (
            <View style={styles.headerActions}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Buchungen einem Sparziel zuordnen"
                hitSlop={8}
                onPress={() => router.push("/ziele-zuordnen")}
              >
                <Text style={[styles.headerLink, { color: theme.textSecondary }]}>Zuordnen</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Neues Sparziel oder neuen Wunsch anlegen"
                hitSlop={8}
                onPress={() => router.push("/ziele-neu")}
              >
                <Text style={[styles.headerAction, { color: theme.positive }]}>+ Neu</Text>
              </Pressable>
            </View>
          ),
        }}
      />
      <ScreenContainer>
        <ScrollView
          contentContainerStyle={isEmpty ? styles.emptyContent : styles.content}
          refreshControl={
            <RefreshControl
              refreshing={isRefreshing}
              onRefresh={() => {
                void refetchGoals();
                void refetchWishlist();
              }}
            />
          }
        >
          {isEmpty ? (
            <InlineQueryState
              status="empty"
              title="Noch keine Sparziele oder Wünsche"
              message="Erfasse erst einen Wunsch oder plane direkt eine monatliche Rücklage."
            />
          ) : (
            <>
              <View style={styles.section}>
                <SectionLabel title="SPARZIELE" hint={`${currentGoals.length} aktiv`} />
                {isGoalsLoading ? (
                  <Card>
                    <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={18} />
                  </Card>
                ) : isGoalsError ? (
                  <Card>
                    <InlineQueryState
                      status="error"
                      title="Sparziele konnten nicht geladen werden"
                      onRetry={() => void refetchGoals()}
                    />
                  </Card>
                ) : currentGoals.length === 0 ? (
                  <Card>
                    <InlineQueryState
                      status="empty"
                      title="Noch keine Sparziele."
                      message="Aus jedem Wunsch lässt sich unten ein Sparziel machen."
                    />
                  </Card>
                ) : (
                  currentGoals.map((goal) => (
                    <GoalCard
                      key={goal.id}
                      goal={goal}
                      onArchive={() => confirmArchiveGoal(goal)}
                    />
                  ))
                )}
              </View>

              <View style={styles.section}>
                <SectionLabel title="WÜNSCHE" hint={`${openWishlist.length} offen`} />
                {isWishlistLoading ? (
                  <Card>
                    <InlineQueryState status="loading" skeletonRows={2} skeletonRowHeight={18} />
                  </Card>
                ) : isWishlistError ? (
                  <Card>
                    <InlineQueryState
                      status="error"
                      title="Wünsche konnten nicht geladen werden"
                      onRetry={() => void refetchWishlist()}
                    />
                  </Card>
                ) : openWishlist.length === 0 ? (
                  <Card>
                    <InlineQueryState status="empty" title="Noch keine Wünsche." />
                  </Card>
                ) : (
                  openWishlist.map((wish) => (
                    <WishCard
                      key={wish.id}
                      wish={wish}
                      onEditNote={() => openNoteEditor(wish)}
                      onPlan={() => openPlanEditor(wish)}
                      onArchive={() => confirmArchiveWish(wish)}
                    />
                  ))
                )}
              </View>
            </>
          )}
        </ScrollView>
      </ScreenContainer>

      <SheetModal
        visible={noteWish !== null}
        title={noteWish ? `Notiz · ${noteWish.name}` : "Notiz bearbeiten"}
        size="full"
        onClose={() => setNoteWish(null)}
        footer={
          <PrimaryButton
            label="Notiz speichern"
            loading={noteMutation.isPending}
            onPress={() => noteWish && noteMutation.mutate({ itemId: noteWish.id, value: note })}
          />
        }
      >
        <View style={styles.sheetContent}>
          <TextField
            label="Notiz"
            value={note}
            onChangeText={setNote}
            multiline
            textAlignVertical="top"
            style={styles.noteInput}
            placeholder="Warum ist dir dieser Wunsch wichtig? Was soll berücksichtigt werden?"
          />
        </View>
      </SheetModal>

      <SheetModal
        visible={planWish !== null}
        title={planWish ? `${planWish.name} als Sparziel` : "Wunsch als Sparziel"}
        size="full"
        onClose={() => setPlanWish(null)}
        footer={
          <PrimaryButton
            label="Sparplan anlegen"
            loading={planMutation.isPending}
            onPress={submitPlan}
          />
        }
      >
        <ScrollView contentContainerStyle={styles.sheetContent} keyboardShouldPersistTaps="handled">
          <Text style={[styles.sheetIntro, { color: theme.textSecondary }]}>
            Aus dem Wunsch wird eine echte, monatlich berücksichtigte Rücklage. Der Wunsch bleibt
            mit dem Sparziel verbunden.
          </Text>
          {planWish ? (
            <Card>
              <CardLabel>ZIELBETRAG</CardLabel>
              <Money minorUnits={planWish.price_minor} size="display" tone="plain" />
            </Card>
          ) : null}
          <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>Planungsart</Text>
          <OptionList
            value={planningMode}
            onChange={setPlanningMode}
            options={[
              { value: "target_date", label: "Bis zu einem Zieltermin sparen" },
              { value: "monthly_amount", label: "Feste Monatsrate zurücklegen" },
            ]}
          />
          {planningMode === "target_date" ? (
            <IsoDatePickerField label="Zieltermin" value={targetDate} onChange={setTargetDate} />
          ) : (
            <TextField
              label="Monatliche Rate"
              value={monthlyAmount}
              onChangeText={setMonthlyAmount}
              keyboardType="decimal-pad"
              placeholder="z. B. 50,00"
            />
          )}
          <TextField
            label="Bereits bestätigter Startstand"
            value={startingAmount}
            onChangeText={setStartingAmount}
            keyboardType="decimal-pad"
          />
          <View style={styles.switchRow}>
            <View style={styles.switchCopy}>
              <Text style={[styles.switchTitle, { color: theme.textPrimary }]}>
                Im Plan einrechnen
              </Text>
              <Text style={[styles.switchHint, { color: theme.textSecondary }]}>
                Die Monatsrate mindert den planbar freien Betrag.
              </Text>
            </View>
            {/* Gleicher Aufbau wie SwitchRow in app/mehr/einstellungen.tsx:
                die Beschriftung steht in einem Geschwister-<Text> und muss
                deshalb ausdruecklich am Bedienelement haengen, sonst liest
                eine Vorlesehilfe nur "Schalter, aus". positiveStrong statt
                positiveMuted, weil sich ein- und ausgeschaltet bei 12-15 %
                Deckung fast nur ueber die Daumenfarbe unterschieden. */}
            <Switch
              accessibilityRole="switch"
              accessibilityLabel="Im Plan einrechnen"
              accessibilityState={{ checked: includeInPlanning }}
              value={includeInPlanning}
              onValueChange={setIncludeInPlanning}
              trackColor={{ false: theme.surfaceHover, true: theme.positiveStrong }}
              thumbColor={theme.onAccent}
            />
          </View>
          {planError ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.error, { color: theme.warning }]} accessibilityRole="alert">
                {planError}
              </Text>
            </Card>
          ) : null}
        </ScrollView>
      </SheetModal>
    </>
  );
}

/** Abschnittsueberschrift ueber einer Kartenreihe. */
function SectionLabel({ title, hint }: { title: string; hint: string }) {
  return (
    <View style={styles.sectionHead}>
      <CardLabel>{title}</CardLabel>
      <Badge label={hint} />
    </View>
  );
}

/**
 * Beschriftete Zahl in einer Zeile ("Ziel: 3.000 €", "Empfehlung 45,84 €
 * /Monat"). Der Betrag laeuft durch <Money> und wird damit im Privat-Modus
 * genauso maskiert wie jede andere Zahl der App.
 */
function AmountLine({
  label,
  minorUnits,
  suffix,
  tone = "muted",
}: {
  label: string;
  minorUnits: number;
  suffix?: string;
  tone?: "muted" | "plain" | "positive";
}) {
  const theme = useTheme();
  return (
    <View style={styles.amountLine}>
      <Text style={[styles.amountLabel, { color: theme.textSecondary }]}>{label}</Text>
      <Money minorUnits={minorUnits} tone={tone} style={styles.inlineAmount} />
      {suffix ? (
        <Text style={[styles.amountLabel, { color: theme.textSecondary }]}>{suffix}</Text>
      ) : null}
    </View>
  );
}

function GoalCard({ goal, onArchive }: { goal: SavingsGoal; onArchive: () => void }) {
  const theme = useTheme();

  const ratio =
    goal.target_amount_minor > 0 ? goal.current_amount_minor / goal.target_amount_minor : 0;
  const percent = Math.min(100, Math.round(ratio * 100));
  const reached =
    goal.target_amount_minor > 0 && goal.current_amount_minor >= goal.target_amount_minor;
  const remaining = Math.max(0, goal.target_amount_minor - goal.current_amount_minor);
  const tone: AccentTone = reached ? "positive" : PLANNING_STATUS_TONE[goal.planning_status];

  return (
    <Card testID="savings-goal-row">
      <View style={styles.cardHead}>
        <Avatar label={goal.name} />
        <View style={styles.cardHeadText}>
          <Text style={[styles.cardTitle, { color: theme.textPrimary }]} numberOfLines={2}>
            {goal.name}
          </Text>
          <View style={styles.metaRow}>
            <AmountLine label="Ziel:" minorUnits={goal.target_amount_minor} />
            <Text style={[styles.metaSeparator, { color: theme.textMuted }]}>·</Text>
            {reached ? (
              <AmountLine label="Erreicht:" minorUnits={goal.current_amount_minor} />
            ) : (
              <AmountLine label="Noch" minorUnits={remaining} />
            )}
          </View>
        </View>
        <View style={styles.cardHeadTrailing}>
          <Money minorUnits={goal.current_amount_minor} tone="plain" />
          <Badge label={reached ? "100% erreicht" : `${percent}%`} tone={tone} />
        </View>
      </View>

      <PacingBar
        progress={ratio}
        tone={tone}
        accessibilityLabel={`${goal.name}: ${percent} Prozent des Ziels bestätigt`}
      />

      {goal.planning_mode === "target_date" ? (
        <Text style={[styles.planLine, { color: theme.textPrimary }]}>
          {goal.target_date ? `Zieltermin ${formatDueDate(goal.target_date)}` : "Zieltermin fehlt"}
        </Text>
      ) : (
        <AmountLine
          label="Feste Monatsrate"
          minorUnits={goal.monthly_contribution_minor ?? 0}
          suffix="/Monat"
          tone="plain"
        />
      )}

      {goal.projected_completion_date ? (
        <Text style={[styles.footnote, { color: theme.textSecondary }]}>
          Voraussichtlich erreicht im {formatMonthLabel(goal.projected_completion_date)}
        </Text>
      ) : null}

      <AmountLine
        label="Empfehlung"
        minorUnits={goal.effective_monthly_reserve_minor}
        suffix="/Monat"
      />

      <View style={styles.badgeRow}>
        <Badge
          label={goal.include_in_planning ? "Im Plan eingerechnet" : "Nur beobachten"}
          tone={goal.include_in_planning ? "positive" : "neutral"}
        />
        <Badge label={PLANNING_STATUS_LABEL[goal.planning_status]} tone={tone} />
      </View>

      <ActionLink label="Sparziel archivieren" onPress={onArchive} warning />
    </Card>
  );
}

function WishCard({
  wish,
  onEditNote,
  onPlan,
  onArchive,
}: {
  wish: WishlistItem;
  onEditNote: () => void;
  onPlan: () => void;
  onArchive: () => void;
}) {
  const theme = useTheme();
  return (
    <Card testID="wishlist-row">
      <View style={styles.cardHead}>
        <Avatar label={wish.name} icon="pricetag-outline" />
        <View style={styles.cardHeadText}>
          <Text style={[styles.cardTitle, { color: theme.textPrimary }]} numberOfLines={2}>
            {wish.name}
          </Text>
        </View>
        <Money minorUnits={wish.price_minor} tone="plain" />
      </View>

      {/* Leistbarkeits-Ampel wie in der Weboberflaeche - mit dem Vorbehalt IM
          Text statt als Fussnote daneben. `buy_traffic_light` ist eine
          rechnerische Orientierung aus Monatsbudget und verknuepftem Sparziel,
          keine bestaetigte Ruecklage; das gehoert dazugesagt. Die Farbe traegt
          die Aussage deshalb nicht allein: sie steht ausgeschrieben in der
          Pille. Abgesichert in
          src/components/__tests__/SavingsWishlistScreen.test.tsx. */}
      <Badge
        label={`${TRAFFIC_LIGHT_LABEL[wish.buy_traffic_light]} · rechnerisch, keine Rücklage`}
        tone={TRAFFIC_LIGHT_TONE[wish.buy_traffic_light]}
      />

      <Text style={[styles.note, { color: wish.note ? theme.textPrimary : theme.textSecondary }]}>
        {wish.note || "Noch keine Notiz."}
      </Text>

      <View style={styles.actions}>
        <ActionLink label="Notiz bearbeiten" onPress={onEditNote} />
        {!wish.goal_id ? (
          <ActionLink label="Als Sparziel planen" onPress={onPlan} emphasized />
        ) : null}
        <ActionLink label="Archivieren" onPress={onArchive} warning />
      </View>
    </Card>
  );
}

function ActionLink({
  label,
  onPress,
  warning = false,
  emphasized = false,
}: {
  label: string;
  onPress: () => void;
  warning?: boolean;
  emphasized?: boolean;
}) {
  const theme = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      style={({ pressed }) => [
        styles.actionButton,
        {
          borderColor: emphasized ? theme.positive : theme.border,
          backgroundColor: emphasized ? theme.positiveMuted : theme.surfaceRaised,
          opacity: pressed ? 0.65 : 1,
        },
      ]}
    >
      <Text
        style={[
          styles.actionLabel,
          { color: warning ? theme.warning : emphasized ? theme.positive : theme.textPrimary },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  headerActions: { flexDirection: "row", alignItems: "center", gap: space.md },
  // Kopfzeilen-Konvention der App: die sekundaere Textaktion steht kleiner und
  // in textSecondary, die primaere "+ Neu"-Aktion in positive - genauso in
  // vorhaben.tsx, vermoegen.tsx, konten.tsx und vertraege.tsx.
  headerLink: { fontSize: typeScale.base, fontWeight: "600" },
  headerAction: { fontSize: typeScale.medium, fontWeight: "600" },
  content: { padding: space.lg, gap: space.xl },
  emptyContent: { flexGrow: 1, justifyContent: "center" },
  section: { gap: space.md },
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  cardHead: { flexDirection: "row", alignItems: "center", gap: space.md },
  cardHeadText: { flex: 1, gap: space.xs },
  cardHeadTrailing: { alignItems: "flex-end", gap: space.xs },
  cardTitle: { fontSize: typeScale.medium, fontWeight: "700" },
  metaRow: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: space.xs },
  metaSeparator: { fontSize: typeScale.caption },
  amountLine: { flexDirection: "row", alignItems: "center", gap: space.xs },
  amountLabel: { fontSize: typeScale.caption },
  inlineAmount: { fontSize: typeScale.caption, fontWeight: "700" },
  planLine: { fontSize: typeScale.body, fontWeight: "600" },
  footnote: { fontSize: typeScale.caption, lineHeight: leading(typeScale.caption) },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: space.sm },
  note: { fontSize: typeScale.body, lineHeight: leading(typeScale.body) },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  actionButton: {
    minHeight: 44,
    justifyContent: "center",
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
  },
  actionLabel: { fontSize: typeScale.body, fontWeight: "600" },
  sheetContent: {
    paddingHorizontal: space.lg,
    paddingBottom: space.xl,
    gap: space.md,
  },
  noteInput: { minHeight: 180, paddingTop: space.md },
  sheetIntro: { fontSize: typeScale.body, lineHeight: leading(typeScale.body) },
  fieldLabel: { fontSize: typeScale.body, fontWeight: "500" },
  switchRow: {
    minHeight: 56,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.md,
  },
  switchCopy: { flex: 1, gap: space.xs },
  switchTitle: { fontSize: typeScale.base, fontWeight: "600" },
  switchHint: { fontSize: typeScale.caption, lineHeight: leading(typeScale.caption) },
  error: { fontSize: typeScale.body },
});
