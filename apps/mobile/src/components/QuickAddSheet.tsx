import * as Haptics from "expo-haptics";
import { useMemo, useState } from "react";
import { Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import type { Account, Category } from "../api/types";
import { useAccounts } from "../hooks/useAccounts";
import { useCategories } from "../hooks/useCategories";
import { useCreateTransaction } from "../hooks/useTransactionMutations";
import { space, typeScale, useTheme } from "../theme";
import { parseAmountToMinorUnits } from "../utils/money";
import { InlineQueryState } from "./InlineQueryState";
import { PrimaryButton } from "./PrimaryButton";
import { SegmentedControl } from "./SegmentedControl";
import { SheetModal } from "./SheetModal";
import { TextField } from "./TextField";
import { Chip, ChipRow, Money, Numpad, type NumpadKey } from "./ui";

type Props = {
  visible: boolean;
  onClose: () => void;
  onSuccess?: () => void;
};

const DIRECTION_OPTIONS = [
  { value: "expense" as const, label: "Ausgabe" },
  { value: "income" as const, label: "Einnahme" },
];

/**
 * Kontoarten, auf die eine Barzahlung an der Kasse ueberhaupt gehoert. Ein
 * Depot, ein Kredit- oder ein Off-Budget-Konto steht hier bewusst nicht: die
 * Buchung geht ohne Serverrueckfrage ueber die Offline-Queue raus, ein
 * Fehlgriff faellt also erst spaeter auf.
 */
const QUICK_ADD_ACCOUNT_TYPES = ["checking", "cash"] as const;

/** Konten, die fuer die Schnellerfassung in Frage kommen - engste Auswahl zuerst. */
export function quickAddAccountCandidates(accounts: Account[]): Account[] {
  const open = accounts.filter((account) => !account.is_closed);
  const preferred = open.filter(
    (account) =>
      account.on_budget &&
      (QUICK_ADD_ACCOUNT_TYPES as readonly string[]).includes(account.account_type),
  );
  if (preferred.length > 0) return preferred;
  const onBudget = open.filter((account) => account.on_budget);
  if (onBudget.length > 0) return onBudget;
  return open.length > 0 ? open : accounts;
}

const DEFAULT_POPULAR_NAMES = [
  "Lebensmittel",
  "Supermarkt",
  "Café & Bäckerei",
  "Restaurant",
  "Tanken",
  "Drogerie",
  "Freizeit",
  "Haushalt",
];

/** Startwert und Ergebnis eines langen Drucks auf die Loeschtaste. */
export const EMPTY_AMOUNT = "0";

/**
 * Eine Ziffernblocktaste auf den bisherigen Eingabetext anwenden.
 *
 * Bewusst als reine Funktion neben der Komponente: das ist die einzige Stelle
 * mit echter Logik in diesem Sheet (fuehrende Null ersetzen, hoechstens ein
 * Komma, hoechstens zwei Nachkommastellen, Rueckschritt bis auf "0") - und
 * genau die laesst sich so ohne gerenderte Oberflaeche pruefen.
 */
export function applyNumpadKey(current: string, key: NumpadKey): string {
  if (key === "back") {
    return current.length <= 1 ? EMPTY_AMOUNT : current.slice(0, -1);
  }
  if (key === ",") {
    return current.includes(",") ? current : `${current},`;
  }
  // Die fuehrende Null wird von der ersten Ziffer ersetzt, nicht ergaenzt.
  if (current === EMPTY_AMOUNT) return key;
  const fraction = current.split(",")[1];
  if (fraction !== undefined && fraction.length >= 2) return current;
  return current + key;
}

/**
 * Eingabetext in Cent. Waehrend des Tippens sind Zwischenstaende wie "12,"
 * normal - die duerfen keine Fehlermeldung ausloesen, deshalb hier 0 statt
 * einer Ausnahme.
 */
function minorUnitsFromInput(text: string): number {
  try {
    return parseAmountToMinorUnits(text);
  } catch {
    return 0;
  }
}

function todayIso(): string {
  const d = new Date();
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Schnellerfassung als Bottom-Sheet (Showcase: "Quick-Add Bottom Sheet (Touch
 * Numpad)").
 *
 * Die Begruendung aus dem Showcase ist der Bauplan: eine Ausgabe soll an der
 * Supermarktkasse in unter fuenf Sekunden erfasst sein. Deshalb kein
 * Formular mit Systemtastatur, sondern Betrag - Kategorie - Speichern in
 * genau dieser Reihenfolge, alles ohne Scrollen erreichbar.
 */
export function QuickAddSheet({ visible, onClose, onSuccess }: Props) {
  const theme = useTheme();
  const accountsQuery = useAccounts();
  const categoriesQuery = useCategories();
  const createMutation = useCreateTransaction();

  const [amountStr, setAmountStr] = useState<string>(EMPTY_AMOUNT);
  const [payee, setPayee] = useState<string>("");
  const [direction, setDirection] = useState<"expense" | "income">("expense");
  const [selectedCategoryId, setSelectedCategoryId] = useState<string | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const accounts = accountsQuery.data;

  // `accounts[0]` war das ZUERST ANGELEGTE Konto (GET /v1/accounts sortiert
  // nach created_at) - in einem Haushalt mit mehreren Konten also gut moeglich
  // ein Depot oder ein Kreditkonto. Deshalb eine echte Auswahl, vorbelegt auf
  // das erste Giro-/Bargeldkonto im Budget.
  const accountOptions = useMemo(() => quickAddAccountCandidates(accounts ?? []), [accounts]);

  const activeAccount = useMemo(() => {
    if (accountOptions.length === 0) return null;
    return (
      accountOptions.find((account) => account.id === selectedAccountId) ??
      accountOptions[0] ??
      null
    );
  }, [accountOptions, selectedAccountId]);

  const quickCategories = useMemo(() => {
    const groups = categoriesQuery.data;
    if (!groups) return [];
    const flat = groups.flatMap((g) => g.categories || []);
    const matching = flat.filter((c) =>
      DEFAULT_POPULAR_NAMES.some((name) => c.name.toLowerCase().includes(name.toLowerCase())),
    );
    return matching.length > 0 ? matching.slice(0, 6) : flat.slice(0, 6);
  }, [categoriesQuery.data]);

  const enteredMinor = minorUnitsFromInput(amountStr);
  // Vorzeichen kommt aus dem Umschalter, nicht aus der Tastatur - deshalb
  // steht es hier und nicht im Eingabetext.
  const signedMinor = direction === "expense" ? -enteredMinor : enteredMinor;

  function handleKey(key: NumpadKey) {
    setError(null);
    setAmountStr((prev) => applyNumpadKey(prev, key));
  }

  function handleClear() {
    setError(null);
    setAmountStr(EMPTY_AMOUNT);
  }

  async function handleSave() {
    if (!activeAccount) {
      setError("Kein Konto vorhanden.");
      return;
    }
    if (enteredMinor <= 0) {
      setError("Bitte einen Betrag größer als 0 eingeben.");
      return;
    }

    setError(null);
    try {
      await createMutation.mutateAsync({
        account_id: activeAccount.id,
        booked_date: todayIso(),
        amount_minor: signedMinor,
        currency: activeAccount.currency || "EUR",
        payee_label: payee.trim() || null,
        category_id: selectedCategoryId || null,
      });

      // Die Buchung laeuft ueber die Offline-Queue, es gibt also keine
      // Serverantwort zum Abwarten. Der kurze Erfolgston in der Hand ist die
      // einzige Rueckmeldung, die man an der Kasse mitbekommt, ohne
      // hinzusehen.
      if (Platform.OS !== "web") {
        void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      }

      setAmountStr(EMPTY_AMOUNT);
      setPayee("");
      setSelectedCategoryId(null);
      onSuccess?.();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Fehler beim Speichern");
    }
  }

  const accountLine = accountsQuery.isLoading
    ? "Konto wird geladen..."
    : activeAccount
      ? `Konto: ${activeAccount.name}`
      : "Kein Konto vorhanden";
  const showAccountPicker = !accountsQuery.isLoading && accountOptions.length > 1;

  return (
    <SheetModal visible={visible} title="Schnellausgabe erfassen" size="sheet" onClose={onClose}>
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <SegmentedControl
          options={DIRECTION_OPTIONS}
          value={direction}
          onChange={setDirection}
          accessibilityLabel="Ausgabe oder Einnahme"
        />

        <View style={styles.amountBlock}>
          {/* Waehrend des Tippens NIE maskieren: der Privat-Modus versteckt
              den Kontostand vor Mitlesenden, nicht die Zahl, die man gerade
              selbst eingibt. */}
          <Money
            minorUnits={signedMinor}
            currency={activeAccount?.currency ?? "EUR"}
            size="hero"
            showPlusSign
            isPrivacyMode={false}
            testID="quick-add-amount"
          />
          <Text style={[styles.accountLine, { color: theme.textSecondary }]} numberOfLines={1}>
            {accountLine}
          </Text>
        </View>

        {showAccountPicker ? (
          <View style={styles.pickerBlock}>
            <Text style={[styles.pickerLabel, { color: theme.textSecondary }]}>Konto</Text>
            <ChipRow>
              {accountOptions.map((account) => (
                <Chip
                  key={account.id}
                  label={account.name}
                  selected={account.id === activeAccount?.id}
                  accessibilityLabel={`Konto ${account.name}`}
                  onPress={() => setSelectedAccountId(account.id)}
                />
              ))}
            </ChipRow>
          </View>
        ) : null}

        {categoriesQuery.isLoading ? (
          <InlineQueryState status="loading" skeletonRows={1} skeletonRowHeight={40} />
        ) : categoriesQuery.isError ? (
          <InlineQueryState
            status="error"
            title="Kategorien nicht geladen"
            message="Die Buchung lässt sich trotzdem speichern und später zuordnen."
            onRetry={() => void categoriesQuery.refetch()}
          />
        ) : quickCategories.length > 0 ? (
          <ChipRow>
            {quickCategories.map((category: Category) => {
              const selected = selectedCategoryId === category.id;
              return (
                <Chip
                  key={category.id}
                  label={category.name}
                  selected={selected}
                  accessibilityLabel={`Kategorie ${category.name}`}
                  onPress={() => setSelectedCategoryId(selected ? null : category.id)}
                />
              );
            })}
          </ChipRow>
        ) : null}

        <Numpad onPress={handleKey} onClear={handleClear} testID="quick-add-numpad" />

        {/* Der Showcase kennt kein Empfaengerfeld. Es bleibt trotzdem: ohne
            Bezeichnung steht in der Buchungsliste spaeter nur ein Betrag, und
            die Buchung ist beim Pruefen nicht wiederzuerkennen. */}
        <TextField
          label="Empfänger (optional)"
          value={payee}
          onChangeText={setPayee}
          placeholder="z. B. Rewe"
          returnKeyType="done"
        />

        {error ? (
          <Text accessibilityRole="alert" style={[styles.errorText, { color: theme.warning }]}>
            {error}
          </Text>
        ) : null}

        <PrimaryButton
          label="Buchung speichern"
          onPress={() => void handleSave()}
          loading={createMutation.isPending}
          disabled={!activeAccount}
        />
      </ScrollView>
    </SheetModal>
  );
}

const styles = StyleSheet.create({
  content: {
    paddingHorizontal: space.xl,
    paddingBottom: space.xl,
    gap: space.md,
  },
  amountBlock: {
    alignItems: "center",
    gap: space.hair,
    paddingVertical: space.sm,
  },
  accountLine: {
    fontSize: typeScale.micro,
  },
  pickerBlock: {
    gap: space.xs,
  },
  pickerLabel: {
    fontSize: typeScale.micro,
    fontWeight: "700",
  },
  errorText: {
    fontSize: typeScale.body,
    textAlign: "center",
  },
});
