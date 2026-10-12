import { useQueryClient } from "@tanstack/react-query";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import type { PropsWithChildren } from "react";
import { useEffect, useMemo, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { linkTransfer, unlinkTransfer } from "../../src/api/client";
import type { PutTransactionSplitInput, Transaction, TransactionDetail } from "../../src/api/types";
import { useAuth } from "../../src/auth/AuthProvider";
import { CategoryPicker } from "../../src/components/CategoryPicker";
import { CategoryPickerModal } from "../../src/components/CategoryPickerModal";
import { GoalPickerModal } from "../../src/components/GoalPickerModal";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { OptionList } from "../../src/components/OptionList";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ProvenanceCard } from "../../src/components/ProvenanceCard";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { TextField } from "../../src/components/TextField";
import { Badge, Card, CardLabel, DataRow, Money } from "../../src/components/ui";
import { useHousehold } from "../../src/household/HouseholdProvider";
import { useAccounts } from "../../src/hooks/useAccounts";
import { useAssignTransactionCategory } from "../../src/hooks/useCategoryCatalog";
import { useCategories } from "../../src/hooks/useCategories";
import { useConnectivity } from "../../src/hooks/useConnectivity";
import { useSavingsGoals } from "../../src/hooks/useGoals";
import {
  TRANSACTION_DETAIL_QUERY_KEY,
  useTransactionDetail,
} from "../../src/hooks/useTransactionDetail";
import {
  useDeleteTransaction,
  useUpdateTransaction,
  useUpdateTransactionSplits,
} from "../../src/hooks/useTransactionMutations";
import { useTransactions } from "../../src/hooks/useTransactions";
import { isPendingTransaction } from "../../src/offline/pendingTransactions";
import { radii, space, typeScale, useTheme, type AccentTone } from "../../src/theme";
import { categoryPathById } from "../../src/utils/categorySearch";
import {
  formatMinorUnits,
  formatMinorUnitsForInput,
  parseAmountToMinorUnits,
} from "../../src/utils/money";
import {
  forgetTransferLink,
  getCachedTransferLinkId,
  rememberTransferLink,
} from "../../src/utils/transferLinkCache";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

const STATUS_OPTIONS: { value: "pending" | "booked"; label: string }[] = [
  { value: "pending", label: "Vorgemerkt" },
  { value: "booked", label: "Gebucht" },
];

/** Buchungsstatus als Klartext - Farbe allein traegt die Bedeutung nie. */
function statusLabel(status: string): string {
  if (status === "pending") return "Vorgemerkt";
  if (status === "booked") return "Gebucht";
  return "Abgestimmt";
}

function statusTone(status: string): AccentTone {
  if (status === "pending") return "highlight";
  if (status === "booked") return "info";
  return "positive";
}

type EditableSplit = {
  // Lokaler Schluessel fuer React-Keys - bei bestehenden Splits die
  // Server-ID, bei neu hinzugefuegten ein generierter Platzhalter (siehe
  // nextSplitKey unten). PUT .../splits ersetzt ohnehin immer alle Splits
  // atomar, eine separate Server-ID wird fuer den Request selbst nicht
  // gebraucht.
  key: string;
  category_id: string | null;
  goal_id: string | null;
  amountText: string;
  note: string;
};

let splitKeySeq = 0;
function nextSplitKey(): string {
  splitKeySeq += 1;
  return `local-split-${splitKeySeq}`;
}

function tryParseAmount(text: string): number | null {
  try {
    return parseAmountToMinorUnits(text);
  } catch {
    return null;
  }
}

function splitsFromDetail(detail: TransactionDetail): EditableSplit[] {
  return detail.splits.map((split) => ({
    key: split.id,
    category_id: split.category_id,
    goal_id: split.goal_id,
    amountText: formatMinorUnitsForInput(split.amount_minor),
    note: split.note ?? "",
  }));
}

/** Abschnitt des Formulars: eine <Card> mit Ueberschrift. */
function FormSection({ title, children }: PropsWithChildren<{ title: string }>) {
  const theme = useTheme();
  return (
    <Card>
      <Text accessibilityRole="header" style={[styles.sectionTitle, { color: theme.textPrimary }]}>
        {title}
      </Text>
      {children}
    </Card>
  );
}

/** Beschriftung ueber einem Feld, das kein <TextField> ist (Auswahllisten). */
function FieldLabel({ children }: { children: string }) {
  const theme = useTheme();
  return <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>{children}</Text>;
}

// Buchungsdetailseite (Auftrag Phase 2, Punkt 2): allgemeine Felder ueber
// PATCH, Betrag/Kategorien atomar als Splits ueber PUT .../splits (zentrale
// Buchungsinvariante: sum(splits) === amount_minor, siehe Architekturplan
// Abschnitt 9), Transferverknuepfung und Loeschen.
export default function BuchungDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const { data: accounts } = useAccounts();
  const { data: categoryGroups } = useCategories();
  const { data: goals } = useSavingsGoals();
  const { data: detail, isLoading, isError, error, refetch } = useTransactionDetail(id);
  // Kandidaten fuer eine neue Transferverknuepfung: die letzten Buchungen
  // des Haushalts (kontouebergreifend), client-seitig auf andere Konten und
  // noch nicht verknuepfte Buchungen gefiltert (siehe Auftrag Punkt 2: "muss
  // nicht elegant sein, aber funktional"). Transferverknuepfung ist bewusst
  // online-only (Phase 9): sie beruehrt zwei Buchungen gleichzeitig, dafuer
  // gibt es keinen konfliktfreien Offline-Weg.
  const { data: recentTransactions } = useTransactions({ limit: 50 });
  const updateTransaction = useUpdateTransaction();
  const updateTransactionSplits = useUpdateTransactionSplits();
  const deleteTransactionMutation = useDeleteTransaction();
  const assignCategory = useAssignTransactionCategory();
  const isConnected = useConnectivity();

  // ---- Allgemeine Felder (PATCH) ----
  const [bookedDate, setBookedDate] = useState("");
  const [valueDateText, setValueDateText] = useState("");
  const [accountId, setAccountId] = useState<string | null>(null);
  const [payeeLabel, setPayeeLabel] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<"pending" | "booked">("pending");
  const [generalError, setGeneralError] = useState<string | null>(null);
  // Da der Schreibvorgang jetzt ueber die Queue laeuft, gibt es keine
  // Server-Antwort mehr, an der die Nutzerin den Erfolg ablesen koennte -
  // dieser Hinweis ersetzt sie.
  const [queuedHint, setQueuedHint] = useState<string | null>(null);
  /** Rueckmeldung des eigenen Kategorieabschnitts (eigener Schreibpfad). */
  const [categoryHint, setCategoryHint] = useState<string | null>(null);

  // ---- Splits (PUT .../splits) ----
  const [totalAmountText, setTotalAmountText] = useState("");
  const [splits, setSplits] = useState<EditableSplit[]>([]);
  const [editingSplitKey, setEditingSplitKey] = useState<string | null>(null);
  const [editingGoalSplitKey, setEditingGoalSplitKey] = useState<string | null>(null);

  // ---- Transfer ----
  const [transferLinkId, setTransferLinkId] = useState<string | null>(null);
  // Buchungs-ID, fuer die transferLinkId zuletzt aus dem lokalen Cache
  // ermittelt wurde (siehe Effekt unten). "Geprueft" ergibt sich daraus rein
  // ueber einen Vergleich waehrend des Renderns (transferLinkChecked weiter
  // unten) statt ueber einen eigenen synchron in einem Effekt gesetzten
  // Status-State.
  const [transferLinkLookupId, setTransferLinkLookupId] = useState<string | null>(null);
  const [transferActionSaving, setTransferActionSaving] = useState(false);

  // Lokale Formularfelder nur einmal pro geladener Buchung initialisieren -
  // nicht bei jedem Refetch, sonst gingen unfertige Eingaben verloren (nach
  // erfolgreichem Speichern wird stattdessen direkt aus der Server-Antwort
  // synchronisiert, siehe handleSaveGeneral/handleSaveSplits). Bewusst KEIN
  // useEffect: synchrones setState direkt im Effektkoerper wird von der in
  // diesem Projekt aktiven react-hooks/set-state-in-effect-Regel abgelehnt.
  // Stattdessen das von React offiziell empfohlene Muster "State waehrend
  // des Renderns anpassen" (vgl. react.dev "You Might Not Need An Effect"):
  // ein Vergleichswert (initializedDetailId) wird gegen die aktuell
  // geladenen Daten geprueft und bei Abweichung direkt im Render-Koerper neu
  // gesetzt.
  const [initializedDetailId, setInitializedDetailId] = useState<string | null>(null);
  if (detail && detail.id !== initializedDetailId) {
    setInitializedDetailId(detail.id);
    setBookedDate(detail.booked_date);
    setValueDateText(detail.value_date ?? "");
    setAccountId(detail.account_id);
    setPayeeLabel(detail.payee_label ?? "");
    setNote(detail.note ?? "");
    setStatus(detail.status === "pending" ? "pending" : "booked");
    setTotalAmountText(formatMinorUnitsForInput(detail.amount_minor));
    setSplits(splitsFromDetail(detail));
  }

  // "Bereits fuer diese Buchung im Cache nachgeschaut?" - rein abgeleitet,
  // kein eigener State (siehe Kommentar oben).
  const transferLinkChecked = !detail?.is_transfer || transferLinkLookupId === detail.id;

  // Die eigentliche Cache-Suche bleibt ein Effekt (externes System:
  // AsyncStorage), aber setState passiert ausschliesslich asynchron in der
  // .then()-Callback - das ist der von der Regel ausdruecklich erlaubte Fall
  // ("calling setState in a callback function when external state
  // changes"), analog zu HouseholdProvider/AuthProvider in diesem Projekt.
  useEffect(() => {
    if (!id || !detail?.is_transfer || transferLinkLookupId === id) return;
    let active = true;
    void getCachedTransferLinkId(id).then((linkId) => {
      if (!active) return;
      setTransferLinkId(linkId);
      setTransferLinkLookupId(id);
    });
    return () => {
      active = false;
    };
  }, [id, detail?.is_transfer, transferLinkLookupId]);

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const account of accounts ?? []) {
      map.set(account.id, account.name);
    }
    return map;
  }, [accounts]);

  const goalNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const goal of goals ?? []) {
      map.set(goal.id, goal.name);
    }
    return map;
  }, [goals]);

  const accountOptions = (accounts ?? []).map((account) => ({
    value: account.id,
    label: account.name,
  }));

  const transferCandidates = useMemo(() => {
    if (!detail) return [];
    return (recentTransactions ?? [])
      .filter(
        (t) =>
          t.id !== detail.id &&
          t.account_id !== detail.account_id &&
          !t.is_transfer &&
          // Noch nicht uebertragene Neuanlagen haben serverseitig keine ID -
          // sie koennen nicht verknuepft werden.
          !isPendingTransaction(t),
      )
      .slice(0, 20);
  }, [recentTransactions, detail]);

  const editingSplit = splits.find((s) => s.key === editingSplitKey) ?? null;
  const editingGoalSplit = splits.find((s) => s.key === editingGoalSplitKey) ?? null;

  const totalMinorParsed = tryParseAmount(totalAmountText);
  const splitAmountsParsed = splits.map((s) => tryParseAmount(s.amountText));
  const allAmountsValid = totalMinorParsed !== null && splitAmountsParsed.every((v) => v !== null);
  const splitsSum = allAmountsValid
    ? splitAmountsParsed.reduce((sum, v) => sum + (v ?? 0), 0)
    : null;
  const remainingMinor = allAmountsValid
    ? (totalMinorParsed as number) - (splitsSum as number)
    : null;
  const canSaveSplits =
    remainingMinor === 0 && splits.length > 0 && !updateTransactionSplits.isPending;
  const canLinkTransfer = isConnected && !transferActionSaving;

  function updateSplit(key: string, patch: Partial<EditableSplit>) {
    setSplits((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));
  }

  function addSplit() {
    setSplits((prev) => [
      ...prev,
      { key: nextSplitKey(), category_id: null, goal_id: null, amountText: "0,00", note: "" },
    ]);
  }

  function removeSplit(key: string) {
    setSplits((prev) => (prev.length <= 1 ? prev : prev.filter((s) => s.key !== key)));
  }

  /**
   * Kategorie einer einfachen (Einzelsplit-)Buchung sofort setzen - ueber
   * PUT /v1/transactions/{id}/category statt ueber ein vollstaendiges
   * Splits-PUT. Der Endpunkt lehnt eine zwischenzeitlich aufgeteilte Buchung
   * mit 409 ab, statt deren Aufteilung still zu ueberschreiben; ein
   * Splits-PUT haette genau das getan.
   *
   * Der lokale Splitzustand wird mitgezogen, damit der Editor weiter unten
   * nicht den alten Stand zeigt (die Detailabfrage wird erst nach dem
   * Uebertragen invalidiert, und der Formularzustand wird bewusst nur einmal
   * je geladener Buchung initialisiert).
   */
  async function handleAssignCategory(categoryId: string | null) {
    if (!detail) return;
    const target = splits[0];
    if (!target) return;
    try {
      await assignCategory.mutateAsync({
        transactionId: detail.id,
        expectedVersion: detail.version,
        categoryId,
      });
      updateSplit(target.key, { category_id: categoryId });
      setCategoryHint(
        isConnected
          ? "Kategorie gespeichert und wird übertragen."
          : "Offline gespeichert - wird übertragen, sobald wieder eine Verbindung besteht.",
      );
    } catch (cause) {
      Alert.alert(
        "Kategorie konnte nicht geändert werden",
        cause instanceof Error ? cause.message : "Unbekannter Fehler.",
      );
    }
  }

  async function handleSaveGeneral() {
    if (!detail) return;
    if (!DATE_PATTERN.test(bookedDate)) {
      setGeneralError("Bitte das Buchungsdatum im Format JJJJ-MM-TT eingeben.");
      return;
    }
    const trimmedValueDate = valueDateText.trim();
    if (trimmedValueDate.length > 0 && !DATE_PATTERN.test(trimmedValueDate)) {
      setGeneralError(
        "Bitte das Wertstellungsdatum im Format JJJJ-MM-TT eingeben oder leer lassen.",
      );
      return;
    }
    if (!accountId) {
      setGeneralError("Bitte ein Konto auswählen.");
      return;
    }

    setGeneralError(null);
    setQueuedHint(null);
    try {
      // Phase 9: expectedVersion ist die zuletzt vom Server gelesene Version.
      // Wurde die Buchung inzwischen anderswo geaendert oder geloescht,
      // antwortet die API mit 409 und der Eintrag landet sichtbar unter
      // "Mehr - Sync-Konflikte", statt die fremde Aenderung zu ueberschreiben.
      await updateTransaction.mutateAsync({
        transactionId: detail.id,
        expectedVersion: detail.version,
        patch: {
          booked_date: bookedDate,
          value_date: trimmedValueDate.length > 0 ? trimmedValueDate : null,
          account_id: accountId,
          payee_label: payeeLabel.trim().length > 0 ? payeeLabel.trim() : null,
          note: note.trim().length > 0 ? note.trim() : null,
          status: detail.status === "reconciled" ? undefined : status,
        },
      });
      setQueuedHint(
        isConnected
          ? "Änderung gespeichert und wird übertragen."
          : "Offline gespeichert - wird übertragen, sobald wieder eine Verbindung besteht.",
      );
    } catch (cause) {
      setGeneralError(
        cause instanceof Error ? cause.message : "Buchung konnte nicht gespeichert werden.",
      );
    }
  }

  async function handleSaveSplits() {
    if (!detail) return;
    const totalMinor = tryParseAmount(totalAmountText);
    if (totalMinor === null) {
      Alert.alert("Ungültiger Betrag", "Bitte den Buchungsbetrag prüfen.");
      return;
    }

    const parsedSplits: PutTransactionSplitInput[] = [];
    for (const split of splits) {
      const amount = tryParseAmount(split.amountText);
      if (amount === null) {
        Alert.alert("Ungültiger Splitbetrag", "Bitte alle Split-Beträge prüfen.");
        return;
      }
      parsedSplits.push({
        category_id: split.category_id,
        goal_id: split.goal_id,
        amount_minor: amount,
        note: split.note.trim().length > 0 ? split.note.trim() : undefined,
      });
    }

    const sum = parsedSplits.reduce((acc, s) => acc + s.amount_minor, 0);
    if (sum !== totalMinor) {
      // Clientseitige Pruefung der zentralen Buchungsinvariante VOR dem
      // Request (Auftrag Punkt 2), zusaetzlich zur serverseitigen
      // 422-Pruefung.
      Alert.alert(
        "Splits stimmen nicht überein",
        `Die Splits ergeben ${formatMinorUnits(sum, detail.currency)}, die Buchung aber ${formatMinorUnits(
          totalMinor,
          detail.currency,
        )}.`,
      );
      return;
    }

    try {
      await updateTransactionSplits.mutateAsync({
        transactionId: detail.id,
        expectedVersion: detail.version,
        splits: { amount_minor: totalMinor, splits: parsedSplits },
      });
      setQueuedHint(
        isConnected
          ? "Betrag und Kategorien gespeichert und werden übertragen."
          : "Offline gespeichert - wird uebertragen, sobald wieder eine Verbindung besteht.",
      );
    } catch (cause) {
      Alert.alert(
        "Splits konnten nicht gespeichert werden",
        cause instanceof Error ? cause.message : "Unbekannter Fehler (evtl. Summe stimmt nicht).",
      );
    }
  }

  function handleDelete() {
    if (!detail) return;
    Alert.alert(
      "Buchung löschen?",
      "Diese Aktion kann nicht rückgängig gemacht werden. Eine bestehende Transfer-Verknüpfung wird dabei automatisch gelöst.",
      [
        { text: "Abbrechen", style: "cancel" },
        { text: "Löschen", style: "destructive", onPress: () => void performDelete() },
      ],
    );
  }

  async function performDelete() {
    if (!detail) return;
    try {
      await deleteTransactionMutation.mutateAsync(detail.id);
      await forgetTransferLink(detail.id);
      router.back();
    } catch (cause) {
      Alert.alert(
        "Buchung konnte nicht gelöscht werden",
        cause instanceof Error ? cause.message : "Unbekannter Fehler.",
      );
    }
  }

  function handleUnlinkTransfer() {
    if (!transferLinkId) return;
    Alert.alert("Verknüpfung aufheben?", "Die Transfer-Verknüpfung wird entfernt.", [
      { text: "Abbrechen", style: "cancel" },
      { text: "Aufheben", style: "destructive", onPress: () => void performUnlinkTransfer() },
    ]);
  }

  async function performUnlinkTransfer() {
    if (!accessToken || !selectedHouseholdId || !detail || !transferLinkId) return;
    setTransferActionSaving(true);
    try {
      await unlinkTransfer(accessToken, selectedHouseholdId, transferLinkId);
      await forgetTransferLink(detail.id);
      setTransferLinkId(null);
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
      await queryClient.invalidateQueries({ queryKey: [TRANSACTION_DETAIL_QUERY_KEY] });
    } catch (cause) {
      Alert.alert(
        "Verknüpfung konnte nicht aufgehoben werden",
        cause instanceof Error ? cause.message : "Unbekannter Fehler.",
      );
    } finally {
      setTransferActionSaving(false);
    }
  }

  function handleLinkTransfer(candidate: Transaction) {
    if (!detail) return;
    const candidateLabel = candidate.payee_label || candidate.note || candidate.booked_date;
    Alert.alert("Als Transfer verknüpfen?", `Mit "${candidateLabel}" verknüpfen?`, [
      { text: "Abbrechen", style: "cancel" },
      { text: "Verknüpfen", onPress: () => void performLinkTransfer(candidate) },
    ]);
  }

  async function performLinkTransfer(candidate: Transaction) {
    if (!accessToken || !selectedHouseholdId || !detail) return;
    const outgoingId = detail.amount_minor <= 0 ? detail.id : candidate.id;
    const incomingId = detail.amount_minor <= 0 ? candidate.id : detail.id;
    setTransferActionSaving(true);
    try {
      const link = await linkTransfer(accessToken, selectedHouseholdId, {
        outgoing_transaction_id: outgoingId,
        incoming_transaction_id: incomingId,
      });
      await rememberTransferLink(outgoingId, incomingId, link.id);
      // Direkt setzen statt auf den Cache-Lookup-Effekt zu warten - wir
      // kennen die Link-ID bereits aus der Response.
      setTransferLinkId(link.id);
      setTransferLinkLookupId(detail.id);
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
      await queryClient.invalidateQueries({ queryKey: [TRANSACTION_DETAIL_QUERY_KEY] });
    } catch (cause) {
      Alert.alert(
        "Verknüpfung fehlgeschlagen",
        cause instanceof Error ? cause.message : "Unbekannter Fehler.",
      );
    } finally {
      setTransferActionSaving(false);
    }
  }

  return (
    <>
      <Stack.Screen
        options={{ title: detail?.payee_label || detail?.note || "Buchung", headerShown: true }}
      />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {isLoading ? (
            <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={20} />
          ) : null}

          {isError ? (
            <InlineQueryState
              status="error"
              title="Buchung konnte nicht geladen werden"
              message={error instanceof Error ? error.message : undefined}
              onRetry={() => void refetch()}
            />
          ) : null}

          {detail ? (
            <>
              <Card accentEdge={detail.amount_minor > 0 ? "positive" : "neutral"}>
                <CardLabel>BUCHUNG</CardLabel>
                <Money
                  minorUnits={detail.amount_minor}
                  currency={detail.currency}
                  size="display"
                  showPlusSign
                />
                <View style={styles.summaryMeta}>
                  <Text style={[styles.metaText, { color: theme.textSecondary }]} numberOfLines={1}>
                    {accountNameById.get(detail.account_id) ?? "Unbekanntes Konto"}
                  </Text>
                  <Badge label={statusLabel(detail.status)} tone={statusTone(detail.status)} />
                </View>
              </Card>

              {/* Kategorie als eigener, sofort sichtbarer Abschnitt
                  (docs/KATEGORISIERUNGSPLAN.md, "Einzelbuchung Mobile"). Bis
                  hierher lag die Zuordnung nur im Splitbereich weiter unten
                  und wurde deshalb regelmaessig gar nicht gefunden. */}
              <FormSection title="Kategorie">
                {splits.length > 1 ? (
                  <>
                    <Text style={[styles.strongText, { color: theme.textPrimary }]}>
                      {`${splits.length} Kategorien`}
                    </Text>
                    <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                      Diese Buchung ist aufgeteilt. Eine Gesamtkategorie würde die Aufteilung
                      überschreiben - bitte die Splits weiter unten einzeln zuordnen.
                    </Text>
                    <PrimaryButton
                      label="Splits bearbeiten"
                      variant="secondary"
                      onPress={() => {
                        const firstSplit = splits[0];
                        if (firstSplit) setEditingSplitKey(firstSplit.key);
                      }}
                    />
                  </>
                ) : (
                  <>
                    <CategoryPicker
                      groups={categoryGroups ?? []}
                      value={splits[0]?.category_id ?? null}
                      amountMinor={detail.amount_minor}
                      onChange={(categoryId) => void handleAssignCategory(categoryId)}
                    />
                    <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                      Wird sofort gespeichert - der Betrag bleibt unverändert.
                    </Text>
                    {categoryHint ? (
                      <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                        {categoryHint}
                      </Text>
                    ) : null}
                  </>
                )}
              </FormSection>

              <FormSection title="Details">
                <TextField
                  label="Buchungsdatum (JJJJ-MM-TT)"
                  value={bookedDate}
                  onChangeText={setBookedDate}
                />
                <TextField
                  label="Wertstellungsdatum (JJJJ-MM-TT, optional)"
                  value={valueDateText}
                  onChangeText={setValueDateText}
                  placeholder="Leer lassen für kein Wertstellungsdatum"
                />
                <View style={styles.field}>
                  <FieldLabel>Konto</FieldLabel>
                  <OptionList
                    options={accountOptions}
                    value={accountId}
                    onChange={setAccountId}
                    accessibilityLabel="Konto"
                  />
                </View>
                <TextField
                  label="Empfänger/Notiz-Kurztext"
                  value={payeeLabel}
                  onChangeText={setPayeeLabel}
                  placeholder="z. B. Lidl"
                />
                <TextField label="Notiz" value={note} onChangeText={setNote} multiline />
                {detail.status === "reconciled" ? (
                  <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                    Diese Buchung ist abgestimmt und der Status kann hier nicht mehr geändert
                    werden.
                  </Text>
                ) : (
                  <View style={styles.field}>
                    <FieldLabel>Status</FieldLabel>
                    <OptionList
                      options={STATUS_OPTIONS}
                      value={status}
                      onChange={setStatus}
                      accessibilityLabel="Status"
                    />
                  </View>
                )}
                {generalError ? (
                  <Text
                    accessibilityRole="alert"
                    style={[styles.hintText, { color: theme.warning }]}
                  >
                    {generalError}
                  </Text>
                ) : null}
                {queuedHint ? (
                  <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                    {queuedHint}
                  </Text>
                ) : null}
                <PrimaryButton
                  label="Details speichern"
                  onPress={() => void handleSaveGeneral()}
                  loading={updateTransaction.isPending}
                />
              </FormSection>

              <FormSection title="Betrag & Kategorien">
                <TextField
                  label={`Betrag in ${detail.currency}`}
                  value={totalAmountText}
                  onChangeText={setTotalAmountText}
                  keyboardType="numbers-and-punctuation"
                />

                {splits.map((split, index) => (
                  <View
                    key={split.key}
                    style={[
                      styles.splitTile,
                      { borderColor: theme.borderStrong, backgroundColor: theme.surface },
                    ]}
                  >
                    <View style={styles.splitHeaderRow}>
                      <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>
                        {`Split ${index + 1}`}
                      </Text>
                      {splits.length > 1 ? (
                        <Pressable
                          onPress={() => removeSplit(split.key)}
                          hitSlop={8}
                          accessibilityRole="button"
                          accessibilityLabel={`Split ${index + 1} entfernen`}
                          style={styles.removeButton}
                        >
                          <Text style={[styles.removeLabel, { color: theme.warning }]}>
                            Entfernen
                          </Text>
                        </Pressable>
                      ) : null}
                    </View>

                    <Pressable
                      onPress={() => setEditingSplitKey(split.key)}
                      accessibilityRole="button"
                      accessibilityLabel={`Kategorie: ${categoryPathById(categoryGroups ?? [], split.category_id)}`}
                      accessibilityHint="Oeffnet die durchsuchbare Kategorieauswahl."
                      style={({ pressed }) => [
                        styles.pickerField,
                        {
                          borderColor: split.category_id ? theme.primaryStrong : theme.border,
                          backgroundColor: theme.surfaceRaised,
                          opacity: pressed ? 0.7 : 1,
                        },
                      ]}
                    >
                      <Text style={[styles.pickerValue, { color: theme.textPrimary }]}>
                        {/* Immer der vollstaendige Pfad `Gruppe · Kategorie`:
                            derselbe Name darf in mehreren Gruppen vorkommen. */}
                        {categoryPathById(categoryGroups ?? [], split.category_id)}
                      </Text>
                    </Pressable>

                    <Pressable
                      onPress={() => setEditingGoalSplitKey(split.key)}
                      accessibilityRole="button"
                      accessibilityLabel={`Sparziel: ${
                        split.goal_id
                          ? (goalNameById.get(split.goal_id) ?? "Unbekanntes Sparziel")
                          : "Kein Sparziel"
                      }`}
                      style={({ pressed }) => [
                        styles.pickerField,
                        {
                          borderColor: split.goal_id ? theme.primaryStrong : theme.border,
                          backgroundColor: theme.surfaceRaised,
                          opacity: pressed ? 0.7 : 1,
                        },
                      ]}
                    >
                      <Text style={[styles.pickerValue, { color: theme.textSecondary }]}>
                        {split.goal_id
                          ? (goalNameById.get(split.goal_id) ?? "Unbekanntes Sparziel")
                          : "Kein Sparziel"}
                      </Text>
                    </Pressable>

                    <TextField
                      label={`Betrag Split ${index + 1}`}
                      value={split.amountText}
                      onChangeText={(text) => updateSplit(split.key, { amountText: text })}
                      keyboardType="numbers-and-punctuation"
                    />
                    <TextField
                      label="Notiz (optional)"
                      value={split.note}
                      onChangeText={(text) => updateSplit(split.key, { note: text })}
                    />
                  </View>
                ))}

                <PrimaryButton label="+ Split hinzufügen" variant="secondary" onPress={addSplit} />

                {/* Die Invariante sum(splits) === amount_minor als Zahl UND
                    als ausgeschriebene Pille - die Farbe allein saehe man
                    hier sonst als einziges Signal. */}
                <View style={styles.remainderRow}>
                  <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>Rest</Text>
                  {remainingMinor === null ? (
                    <Text style={[styles.hintText, { color: theme.warning }]}>
                      Bitte gültige Beträge eingeben.
                    </Text>
                  ) : (
                    <View style={styles.remainderValue}>
                      <Money
                        minorUnits={remainingMinor}
                        currency={detail.currency}
                        tone={remainingMinor === 0 ? "positive" : "warning"}
                      />
                      <Badge
                        label={remainingMinor === 0 ? "Summe stimmt" : "Summe weicht ab"}
                        tone={remainingMinor === 0 ? "positive" : "warning"}
                      />
                    </View>
                  )}
                </View>

                <PrimaryButton
                  label="Splits speichern"
                  onPress={() => void handleSaveSplits()}
                  loading={updateTransactionSplits.isPending}
                  disabled={!canSaveSplits}
                />
              </FormSection>

              {/* Direkt nach den Kategorien: die Herkunft erklaert genau die
                  Zuordnung, die darueber steht. */}
              <ProvenanceCard transactionId={detail.id} />

              <FormSection title="Transfer">
                {!isConnected ? (
                  <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                    Transferverknüpfungen lassen sich nur online ändern.
                  </Text>
                ) : null}
                {detail.is_transfer ? (
                  !transferLinkChecked ? (
                    <InlineQueryState status="loading" skeletonRows={1} skeletonRowHeight={20} />
                  ) : transferLinkId ? (
                    <PrimaryButton
                      label="Verknüpfung aufheben"
                      variant="danger"
                      onPress={handleUnlinkTransfer}
                      disabled={!isConnected}
                      loading={transferActionSaving}
                    />
                  ) : (
                    <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                      Diese Buchung ist Teil eines Transfers. Die Verknüpfungs-ID ist auf diesem
                      Gerät nicht bekannt (z. B. weil der Transfer über die Web-App oder ein anderes
                      Gerät verknüpft wurde) - ein Aufheben ist von hier aus daher nicht möglich.
                    </Text>
                  )
                ) : (
                  <>
                    <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                      Nicht verknüpft. Mit einer Buchung eines anderen Kontos verknüpfen:
                    </Text>
                    {transferCandidates.length === 0 ? (
                      <InlineQueryState
                        status="empty"
                        title="Keine passenden Buchungen"
                        message="Aus anderen Konten liegt nichts vor, das sich verknüpfen ließe."
                      />
                    ) : (
                      transferCandidates.map((candidate, index) => (
                        <DataRow
                          key={candidate.id}
                          title={candidate.payee_label || candidate.note || candidate.booked_date}
                          subtitle={`${accountNameById.get(candidate.account_id) ?? "Unbekanntes Konto"} · ${candidate.booked_date}`}
                          trailing={
                            <Money
                              minorUnits={candidate.amount_minor}
                              currency={candidate.currency}
                            />
                          }
                          onPress={
                            canLinkTransfer ? () => handleLinkTransfer(candidate) : undefined
                          }
                          accessibilityLabel={`Mit ${
                            candidate.payee_label || candidate.note || candidate.booked_date
                          } als Transfer verknüpfen`}
                          divider={index < transferCandidates.length - 1}
                        />
                      ))
                    )}
                  </>
                )}
              </FormSection>

              <PrimaryButton
                label="Buchung löschen"
                variant="danger"
                onPress={handleDelete}
                loading={deleteTransactionMutation.isPending}
              />
            </>
          ) : null}
        </ScrollView>

        <CategoryPickerModal
          visible={editingSplit !== null}
          groups={categoryGroups ?? []}
          value={editingSplit?.category_id ?? null}
          amountMinor={editingSplit ? tryParseAmount(editingSplit.amountText) : null}
          onChange={(categoryId) => {
            if (editingSplitKey) {
              updateSplit(editingSplitKey, { category_id: categoryId });
            }
            setEditingSplitKey(null);
          }}
          onClose={() => setEditingSplitKey(null)}
          title="Kategorie für Split wählen"
        />

        <GoalPickerModal
          visible={editingGoalSplit !== null}
          goals={goals ?? []}
          value={editingGoalSplit?.goal_id ?? null}
          onChange={(goalId) => {
            if (editingGoalSplitKey) {
              updateSplit(editingGoalSplitKey, { goal_id: goalId });
            }
            setEditingGoalSplitKey(null);
          }}
          onClose={() => setEditingGoalSplitKey(null)}
          title="Sparziel für Split wählen"
        />
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.md,
    paddingBottom: space.xxxl,
  },
  sectionTitle: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  summaryMeta: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  metaText: {
    flexShrink: 1,
    fontSize: typeScale.body,
  },
  hintText: {
    fontSize: typeScale.body,
  },
  strongText: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  field: {
    gap: space.sm,
  },
  fieldLabel: {
    fontSize: typeScale.caption,
    fontWeight: "600",
  },
  splitTile: {
    borderWidth: 1,
    borderRadius: radii.md,
    padding: space.md,
    gap: space.sm,
  },
  splitHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  removeButton: {
    minHeight: 44,
    justifyContent: "center",
  },
  removeLabel: {
    fontSize: typeScale.body,
    fontWeight: "700",
  },
  pickerField: {
    minHeight: 48,
    justifyContent: "center",
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
  },
  pickerValue: {
    fontSize: typeScale.base,
    fontWeight: "600",
  },
  remainderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  remainderValue: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
});
