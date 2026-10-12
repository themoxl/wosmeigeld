import { Stack } from "expo-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import type {
  ContractDirection,
  ContractFrequency,
  ContractKeySource,
  ContractType,
  ReviewContractAmountStep,
  ReviewContractScore,
  ReviewItemContractAmountChangeCheck,
  ReviewItemContractDuplicate,
  ReviewItemContractEndedCheck,
  ReviewItemContractSuggestion,
  ReviewItemDuplicate,
  ReviewItemTransfer,
  ReviewItemUnclassified,
} from "../../src/api/types";
import { CategoryPickerModal } from "../../src/components/CategoryPickerModal";
import { EmptyState } from "../../src/components/EmptyState";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { BookedDuplicatesPanel } from "../../src/components/BookedDuplicatesPanel";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { Badge, Card, CardLabel, Money } from "../../src/components/ui";
import { useCategories } from "../../src/hooks/useCategories";
import { useHousehold } from "../../src/household/HouseholdProvider";
import {
  useAcceptDuplicatesBulk,
  useAcceptReview,
  useCorrectReview,
  useReviewInbox,
} from "../../src/hooks/useReview";
import {
  isClassificationRunActive,
  useAIDecisions,
  useCancelClassificationRun,
  useClassifyWithAI,
  useLatestClassificationRun,
  useStartClassificationRun,
} from "../../src/hooks/useAI";
import { leading, radii, space, typeScale, useTheme } from "../../src/theme";
import { categoryPathById } from "../../src/utils/categorySearch";

const FREQUENCY_LABELS: Record<ContractFrequency, string> = {
  weekly: "Wöchentlich",
  monthly: "Monatlich",
  quarterly: "Vierteljährlich",
  semiannual: "Halbjährlich",
  yearly: "Jährlich",
};

const CONTRACT_TYPE_LABELS: Record<ContractType, string> = {
  subscription: "Abonnement",
  insurance: "Versicherung",
  rent: "Miete & Wohnen",
  loan: "Kredit",
  installment: "Ratenkauf",
  utility: "Energie",
  telecom: "Telekommunikation",
  membership: "Mitgliedschaft",
  donation: "Spende",
  savings: "Sparplan",
  income: "Einnahme",
  other: "Sonstiges",
};

const CONTRACT_KEY_SOURCE_LABELS: Record<ContractKeySource, string> = {
  mandate: "Mandatsreferenz",
  purpose_pattern: "Verwendungszweck",
  manual: "manuell",
};

// Bewusst eine feste Liste statt Intl.DateTimeFormat: Hermes bringt je nach
// Build keine vollstaendigen Locale-Daten mit, und ein leerer Monatsname waere
// hier schlimmer als ein fehlendes Umlautzeichen.
const MONTH_LABELS = [
  "Januar",
  "Februar",
  "März",
  "April",
  "Mai",
  "Juni",
  "Juli",
  "August",
  "September",
  "Oktober",
  "November",
  "Dezember",
];

/**
 * Die fuenf Bestandteile des Vertrags-Scores mit ihrem Gewicht
 * (docs/VERTRAEGE-FIXKOSTEN-PLAN.md Abschnitt 6.4).
 *
 * Ein unerklaerter Konfidenzwert ist in einem Finanzprodukt wertlos - deshalb
 * steht auch auf dem Telefon jeder Bestandteil einzeln da. Die Gewichte sind
 * gespiegelt, nicht gerechnet: wer sie im Server aendert, muss sie hier
 * nachziehen.
 */
const SCORE_PARTS: {
  key: Exclude<keyof ReviewContractScore, "total">;
  label: string;
  weight: number;
}[] = [
  { key: "date_stability", label: "Datumsstabilität", weight: 0.3 },
  { key: "amount_stability", label: "Betragsstabilität", weight: 0.2 },
  { key: "occurrence_count", label: "Vorkommen", weight: 0.15 },
  { key: "category_affinity", label: "Kategorie", weight: 0.2 },
  { key: "payment_method", label: "Zahlungsart", weight: 0.15 },
];

/**
 * Betraege liegen am Vorschlag - wie in `contracts` - IMMER positiv, die
 * Richtung steht getrennt daneben. Ohne diese Umrechnung stuende eine Ausgabe
 * hier als Einnahme.
 */
function signedAmountMinor(amountMinor: number, direction: ContractDirection): number {
  return direction === "expense" ? -amountMinor : amountMinor;
}

function formatMonth(isoDate: string): string {
  const monthIndex = Number.parseInt(isoDate.slice(5, 7), 10) - 1;
  return `${MONTH_LABELS[monthIndex] ?? isoDate.slice(5, 7)} ${isoDate.slice(0, 4)}`;
}

/** Tagesdatum aus derselben festen Monatsliste - siehe MONTH_LABELS. */
function formatDay(isoDate: string): string {
  const day = Number.parseInt(isoDate.slice(8, 10), 10);
  const month = MONTH_LABELS[Number.parseInt(isoDate.slice(5, 7), 10) - 1];
  if (Number.isNaN(day) || !month) return isoDate;
  return `${day}. ${month} ${isoDate.slice(0, 4)}`;
}

/**
 * Satz zur erkannten Betragsaenderung, Wortlaut wie in der Weboberflaeche
 * (apps/web/src/pages/ReviewInboxPage.tsx).
 *
 * Die Betraege stehen hier unsigniert. Sie liegen wie ueberall in `contracts`
 * positiv in der Datenbank, und bei einem Ausgabevertrag waere "von -49,95 €
 * auf -39,95 € gesunken" rechnerisch das Gegenteil dessen, was der Satz sagt.
 * Die Richtung steht stattdessen im Subjekt.
 */
function amountChangeVerb(check: ReviewItemContractAmountChangeCheck): string {
  const subject = check.direction === "income" ? "Die Einnahme ist" : "Der Betrag ist";
  const verb = check.delta_minor < 0 ? "gesunken" : "gestiegen";
  return `${subject} ${verb}`;
}

/** Vorzeichenbehaftete Prozentangabe - die gerechnete Aenderung, kein Konfidenzwert. */
function formatSignedPercent(value: number): string {
  const formatted = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(value);
  return `${value > 0 ? "+" : ""}${formatted} %`;
}

/**
 * Zeitraum einer erkannten Preisstufe ("bis Mai 2026", "seit Juni 2026").
 *
 * Der Betrag steht bewusst NICHT im Satz, sondern daneben als <Money>: nur so
 * greift der Privat-Modus, und die Stufen stehen mit gleicher Kommastelle
 * untereinander.
 */
function amountStepPeriod(step: ReviewContractAmountStep, isLast: boolean): string {
  return isLast ? `seit ${formatMonth(step.first_date)}` : `bis ${formatMonth(step.last_date)}`;
}

type ContractEndedAction = "ended" | "active" | "paused";

/**
 * Die drei moeglichen Antworten auf "laeuft dieser Vertrag noch?".
 *
 * Alle drei sind gleich gewichtet: der Abgleich weiss nur, dass nichts kam.
 * Eine hervorgehobene Antwort wuerde eine bloss verschobene Zahlung reihenweise
 * zu Vertragsenden machen. Deshalb steht in jeder Bestaetigung die Folge fuer
 * die Fixkostensumme, nicht nur der Statusname.
 */
const CONTRACT_ENDED_OPTIONS: {
  action: ContractEndedAction;
  label: string;
  confirmation: string;
}[] = [
  {
    action: "ended",
    label: "Beendet",
    confirmation: "Der Vertrag wird beendet und zählt ab sofort nicht mehr in den Fixkosten mit.",
  },
  {
    action: "active",
    label: "Läuft weiter",
    confirmation:
      "Der Vertrag bleibt aktiv, der Fehlzähler wird zurückgesetzt und die nächste Fälligkeit neu berechnet.",
  },
  {
    action: "paused",
    label: "Pausiert",
    confirmation:
      "Der Vertrag ruht vorerst und zählt nicht in den Fixkosten mit, bleibt aber erhalten.",
  },
];

/** Ueberschrift einer Fallgruppe mit Anzahl - Text UND Pille, nie nur Farbe. */
function GroupHeading({ label, count }: { label: string; count: number }) {
  return (
    <View style={styles.groupHeading}>
      <CardLabel>{label}</CardLabel>
      <Badge tone="warning" label={`${count} offen`} />
    </View>
  );
}

/**
 * Warum das System glaubt, dass es ein Vertrag ist - Bestandteil fuer
 * Bestandteil, mit Gewicht und Balken.
 */
function ScoreBreakdown({ score }: { score: ReviewContractScore }) {
  const theme = useTheme();

  return (
    <View style={styles.scoreList}>
      {SCORE_PARTS.map((part) => {
        const percent = Math.round(score[part.key] * 100);
        return (
          <View key={part.key} style={styles.scoreRow}>
            <Text style={[styles.scoreLabel, { color: theme.textSecondary }]}>
              {part.label} ({Math.round(part.weight * 100)} %)
            </Text>
            <View style={[styles.scoreTrack, { backgroundColor: theme.border }]}>
              <View
                style={[
                  styles.scoreFill,
                  { backgroundColor: theme.positiveStrong, width: `${percent}%` },
                ]}
              />
            </View>
            <Text style={[styles.scoreValue, { color: theme.textPrimary }]}>{percent} %</Text>
          </View>
        );
      })}
    </View>
  );
}

// Die Fallgruppen der Inbox als Liste - leere Gruppen kommen gar nicht erst in
// die FlatList, damit ListEmptyComponent den Lade-/Fehler-/Leerzustand
// tatsaechlich erreicht (vorher stand dort immer die Zusammenfassung drin).
type ReviewSection =
  | { key: "unclassified"; items: ReviewItemUnclassified[] }
  | { key: "duplicates"; items: ReviewItemDuplicate[] }
  | { key: "transfers"; items: ReviewItemTransfer[] }
  | { key: "contract-suggestions"; items: ReviewItemContractSuggestion[] }
  | { key: "contract-duplicates"; items: ReviewItemContractDuplicate[] }
  | { key: "contract-ended"; items: ReviewItemContractEndedCheck[] }
  | { key: "contract-amount-change"; items: ReviewItemContractAmountChangeCheck[] };

export default function ReviewScreen() {
  const theme = useTheme();
  const { selectedHouseholdId } = useHousehold();

  const { data: inbox, isLoading, isError, isFetching, refetch } = useReviewInbox();
  const { data: categoryGroups } = useCategories();
  const acceptMutation = useAcceptReview();
  const correctMutation = useCorrectReview();
  const bulkDuplicatesMutation = useAcceptDuplicatesBulk();
  const classifyMutation = useClassifyWithAI();

  // Batch-KI-Kategorisierung - zusaetzlich zum bestehenden Einzel-Tap-Button
  // unten, nicht als Ersatz (siehe Plan): der Einzel-Tap treibt synchron den
  // direkt folgenden "Bestaetigen"-Schritt, der Batch-Lauf ist ein
  // "alles auf einmal vorschlagen"-Shortcut.
  //
  // Der Laufzustand kommt vom Server (useLatestClassificationRun), nicht aus
  // lokalem State: nur so findet die App den Lauf nach einem Neustart oder auf
  // einem zweiten Geraet wieder.
  const lastHandledClassifyRunStatus = useRef<string | null>(null);
  const startClassifyRun = useStartClassificationRun();
  const cancelClassifyRun = useCancelClassificationRun();
  const classifyRunQuery = useLatestClassificationRun();
  const classifyRun = classifyRunQuery.data ?? null;
  const { data: aiDecisions } = useAIDecisions();

  const aiDecisionsMap = useMemo(() => {
    const map: Record<string, number> = {};
    if (aiDecisions) {
      for (const d of aiDecisions) {
        if (d.transaction_id && !(d.transaction_id in map)) {
          map[d.transaction_id] = d.confidence;
        }
      }
    }
    return map;
  }, [aiDecisions]);

  const classifyRunStatus = classifyRun?.status;
  const isClassifyRunActive = isClassificationRunActive(classifyRun);

  useEffect(() => {
    if (!classifyRun) return;
    // Marker aus Lauf UND Status: nach einem zweiten Lauf muss dieselbe
    // Statusfolge erneut greifen.
    const marker = `${classifyRun.id}:${classifyRun.status}`;
    if (marker === lastHandledClassifyRunStatus.current) return;
    const isFirstObservation = lastHandledClassifyRunStatus.current === null;
    lastHandledClassifyRunStatus.current = marker;
    // Beim ersten Blick nichts nachladen: ein laengst beendeter Lauf ist kein
    // Ereignis, die Inbox wird beim Mount ohnehin geladen.
    if (!isFirstObservation && !isClassificationRunActive(classifyRun)) {
      void refetch();
    }
  }, [classifyRun, refetch]);

  // State for category picking per item
  const [selectedItem, setSelectedItem] = useState<{
    id: string; // transaction_id
    currentCategoryId: string | null;
    // Betrag der Buchung: priorisiert im Picker passende Arten (Ausgabe vs.
    // Einnahme), versteckt aber keine anderen Kategorien.
    amountMinor: number;
  } | null>(null);
  const [pickedCategories, setPickedCategories] = useState<Record<string, string>>({});
  const [createRuleMap, setCreateRuleMap] = useState<Record<string, boolean>>({});
  const [pickerVisible, setPickerVisible] = useState(false);
  // Gesetzt, solange fuer eine faelschlich gemeldete Dublette noch die
  // Kategorie fehlt - siehe handleCommitDuplicate.
  // Auswahl fuer die Sammelbestaetigung. Nur im Bildschirmzustand: sie gilt
  // fuer diesen Besuch der Liste, nicht darueber hinaus.
  const [gewaehlteDubletten, setGewaehlteDubletten] = useState<Set<string>>(new Set());
  const [duplicateForCategory, setDuplicateForCategory] = useState<ReviewItemDuplicate | null>(
    null,
  );
  const [contractPickerSuggestion, setContractPickerSuggestion] =
    useState<ReviewItemContractSuggestion | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // State for AI classification results per item
  const [aiConfidenceMap, setAiConfidenceMap] = useState<Record<string, number>>({});
  const [classifyingId, setClassifyingId] = useState<string | null>(null);

  const handleClassifyWithAI = async (item: ReviewItemUnclassified) => {
    try {
      setActionError(null);
      setClassifyingId(item.transaction_id);
      const result = await classifyMutation.mutateAsync({
        transaction_id: item.transaction_id,
        payee_raw: item.payee_label ?? undefined,
        purpose: item.note ?? undefined,
        amount_minor: item.amount_minor,
      });

      if (result.category_id) {
        setPickedCategories((prev) => ({
          ...prev,
          [item.transaction_id]: result.category_id!,
        }));
      }
      if (result.confidence !== null && result.confidence !== undefined) {
        setAiConfidenceMap((prev) => ({
          ...prev,
          [item.transaction_id]: result.confidence!,
        }));
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "KI-Kategorisierung fehlgeschlagen.");
    } finally {
      setClassifyingId(null);
    }
  };

  const openPicker = (transactionId: string, amountMinor: number) => {
    setSelectedItem({
      id: transactionId,
      currentCategoryId: pickedCategories[transactionId] || null,
      amountMinor,
    });
    setPickerVisible(true);
  };

  const handleCategorySelect = (catId: string | null) => {
    if (selectedItem && catId) {
      setPickedCategories((prev) => ({ ...prev, [selectedItem.id]: catId }));
    }
    setPickerVisible(false);
    setSelectedItem(null);
  };

  const toggleCreateRule = (transactionId: string) => {
    setCreateRuleMap((prev) => ({ ...prev, [transactionId]: !prev[transactionId] }));
  };

  const handleAcceptUnclassified = async (item: ReviewItemUnclassified) => {
    try {
      setActionError(null);
      const categoryId = pickedCategories[item.transaction_id];
      const createRule = !!createRuleMap[item.transaction_id];

      if (categoryId) {
        // If category is provided, call accept/correct
        await correctMutation.mutateAsync({
          type: "unclassified",
          transaction_id: item.transaction_id,
          category_id: categoryId,
          payee_label: item.payee_label,
          create_rule_always: createRule,
        });
      } else {
        await acceptMutation.mutateAsync({
          type: "unclassified",
          transaction_id: item.transaction_id,
          create_rule: createRule,
          rule_name: item.payee_label ? `Regel für ${item.payee_label}` : undefined,
        });
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Fehler beim Bestätigen.");
    }
  };

  const handleAcceptDuplicate = async (item: ReviewItemDuplicate) => {
    try {
      setActionError(null);
      await acceptMutation.mutateAsync({
        type: "possible_duplicate",
        import_row_id: item.import_row_id,
      });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Fehler beim Bearbeiten.");
    }
  };

  /** Bestaetigt die ausgewaehlten Dubletten in einem Zug.
   *
   * Schon entschiedene Zeilen zaehlt der Server, statt zu scheitern - eine
   * Liste, die seit dem Antippen veraltet ist, soll den Nutzer nicht von vorn
   * anfangen lassen. Genau das passiert auf dem Handy leicht, wenn die Ansicht
   * eine Weile offen lag. */
  const handleBulkDuplicates = async () => {
    if (gewaehlteDubletten.size === 0) return;
    try {
      setActionError(null);
      const ergebnis = await bulkDuplicatesMutation.mutateAsync(Array.from(gewaehlteDubletten));
      setGewaehlteDubletten(new Set());
      if (ergebnis.skipped > 0) {
        setActionError(
          `${ergebnis.confirmed} bestätigt. ${ergebnis.skipped} waren schon entschieden.`,
        );
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Fehler beim Bestätigen.");
    }
  };

  /** Gegenstueck zu handleAcceptDuplicate: die Zeile ist KEINE Dublette und
   * soll als eigene Buchung uebernommen werden.
   *
   * Die Schnittstelle verlangt dafuer eine Kategorie (CorrectDuplicate in
   * services/api/app/schemas/review.py) - anders als beim Bestaetigen, das die
   * Zeile nur verwirft. Deshalb der Umweg ueber die Kategorienauswahl statt
   * eines direkten Aufrufs.
   *
   * Ohne diesen Weg gab es auf dem Handy keine Moeglichkeit, einen Fehlalarm
   * aufzuloesen: bestaetigen verwirft eine echte Buchung, liegenlassen haelt
   * die Liste voll. Die Weboberflaeche hatte den Knopf von Anfang an
   * (ReviewInboxPage.tsx, "Doch uebernehmen"). */
  const handleCommitDuplicate = async (categoryId: string | null) => {
    const item = duplicateForCategory;
    setDuplicateForCategory(null);
    if (!item || !categoryId) return;
    try {
      setActionError(null);
      await correctMutation.mutateAsync({
        type: "possible_duplicate",
        import_row_id: item.import_row_id,
        category_id: categoryId,
      });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Fehler beim Übernehmen.");
    }
  };

  const handleAcceptTransfer = async (item: ReviewItemTransfer) => {
    try {
      setActionError(null);
      await acceptMutation.mutateAsync({
        type: "transfer_candidate",
        import_row_id: item.import_row_id,
      });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Fehler beim Verknüpfen.");
    }
  };

  const handleAcceptContractCategory = async (categoryId: string | null) => {
    const suggestion = contractPickerSuggestion;
    setContractPickerSuggestion(null);
    if (!suggestion || !categoryId) return;
    try {
      setActionError(null);
      // Betrag, Rhythmus und Preisstufen kommen NICHT vom Client: der Server
      // erkennt sie zur Ankerbuchung erneut. Hier geht nur die Kategorie mit.
      await acceptMutation.mutateAsync({
        type: "contract_suggestion",
        source_transaction_id: suggestion.source_transaction_id,
        category_id: categoryId,
      });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Fehler beim Uebernehmen.");
    }
  };

  /**
   * "Das ist kein Vertrag."
   *
   * Ohne die gemerkte Verneinung stuende derselbe Vorschlag nach jedem Import
   * erneut da - gerade im unsicheren Bereich, der genau deshalb sichtbar ist.
   */
  const handleDismissContractSuggestion = (item: ReviewItemContractSuggestion) => {
    Alert.alert(
      item.display_name,
      "Der Vorschlag verschwindet dauerhaft und kommt beim nächsten Import nicht zurück.",
      [
        { text: "Abbrechen", style: "cancel" },
        {
          text: "Kein Vertrag",
          style: "destructive",
          onPress: () => {
            void (async () => {
              try {
                setActionError(null);
                await acceptMutation.mutateAsync({
                  type: "contract_suggestion",
                  source_transaction_id: item.source_transaction_id,
                  dismiss: true,
                });
              } catch (err) {
                setActionError(err instanceof Error ? err.message : "Fehler beim Verwerfen.");
              }
            })();
          },
        },
      ],
    );
  };

  /**
   * Zwei Eintraege desselben Vertrags zusammenfuehren - oder die Frage
   * verneinen.
   *
   * Ziel ist immer der juengere Vertrag; die aelteren gehen mit ihren
   * Buchungen in ihn ueber. Mit Rueckfrage, weil das Zusammenfuehren nicht
   * rueckgaengig zu machen ist.
   */
  const handleContractDuplicate = (item: ReviewItemContractDuplicate, dismiss: boolean) => {
    Alert.alert(
      item.payee_label,
      dismiss
        ? "Beide Verträge bleiben getrennt bestehen. Die Frage wird nicht erneut gestellt."
        : `„${item.older.name}" wird in „${item.newer.name}" überführt - mit Buchungen und Betragsverlauf. Das lässt sich nicht rückgängig machen.`,
      [
        { text: "Abbrechen", style: "cancel" },
        {
          text: dismiss ? "Sind verschieden" : "Zusammenführen",
          style: dismiss ? "default" : "destructive",
          onPress: () => {
            void (async () => {
              try {
                setActionError(null);
                await acceptMutation.mutateAsync({
                  type: "contract_duplicate_suggestion",
                  target_id: item.target_id,
                  source_ids: item.source_ids,
                  dismiss,
                });
              } catch (err) {
                setActionError(err instanceof Error ? err.message : "Fehler beim Zusammenführen.");
              }
            })();
          },
        },
      ],
    );
  };

  /**
   * Antwort auf "laeuft dieser Vertrag noch?".
   *
   * Bewusst mit Rueckfrage: die Antwort aendert den Vertragsstatus und damit
   * die Fixkostensumme - ein Fehlgriff auf dem Telefon soll nicht sofort
   * durchschlagen.
   */
  const handleContractEndedCheck = (
    item: ReviewItemContractEndedCheck,
    option: (typeof CONTRACT_ENDED_OPTIONS)[number],
  ) => {
    Alert.alert(item.name, option.confirmation, [
      { text: "Abbrechen", style: "cancel" },
      {
        text: option.label,
        style: option.action === "ended" ? "destructive" : "default",
        onPress: () => {
          void (async () => {
            try {
              setActionError(null);
              await acceptMutation.mutateAsync({
                type: "contract_ended_check",
                contract_id: item.contract_id,
                action: option.action,
              });
            } catch (err) {
              setActionError(
                err instanceof Error ? err.message : "Fehler beim Beantworten der Vertragsfrage.",
              );
            }
          })();
        },
      },
    ]);
  };

  /**
   * "Stimmt so" auf eine erkannte Betragsaenderung.
   *
   * Bewusst OHNE Rueckfrage - anders als bei "laeuft dieser Vertrag noch?":
   * die Antwort aendert keine Zahl, sie quittiert nur die bereits uebernommene
   * Aenderung und nimmt die Zeile aus der Inbox. Stimmt der Betrag doch nicht,
   * fuehrt der Weg ueber "Betrag korrigieren" am Vertrag.
   */
  const handleAmountChangeCheck = async (item: ReviewItemContractAmountChangeCheck) => {
    try {
      setActionError(null);
      await acceptMutation.mutateAsync({
        type: "contract_amount_change_check",
        event_id: item.event_id,
        action: "confirmed",
      });
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Fehler beim Bestätigen des neuen Betrags.",
      );
    }
  };

  // Anzeigename einer Kategorie IMMER als vollstaendiger Pfad
  // `Gruppe · Kategorie` (docs/KATEGORISIERUNGSPLAN.md): derselbe Name darf in
  // mehreren Gruppen vorkommen, der reine Kategoriename waere mehrdeutig.
  const getCategoryName = (catId?: string) => {
    if (!catId) return "Kategorie wählen";
    return categoryPathById(categoryGroups ?? [], catId);
  };

  const unclassifiedItems = inbox?.unclassified ?? [];
  const duplicateItems = inbox?.possible_duplicates ?? [];
  const alleDublettenGewaehlt =
    duplicateItems.length > 0 &&
    duplicateItems.every((d) => gewaehlteDubletten.has(d.import_row_id));
  const transferItems = inbox?.transfer_candidates ?? [];
  const contractEndedChecks = inbox?.contract_ended_checks ?? [];
  // Siebte Fallgruppe: der Server zaehlt sie in `total_count` mit. Fehlte sie
  // hier, versprach die Kopfzeile "N Fälle" und die Liste zeigte "Alles
  // erledigt!".
  const contractAmountChanges = inbox?.contract_amount_change_checks ?? [];
  const contractDuplicates = inbox?.contract_duplicate_suggestions ?? [];
  const totalCount = inbox?.total_count ?? 0;

  // Unsichere Vorschlaege (0.40-0.55) ans Ende: zwischen bestaetigungsreifen
  // Vertraegen wuerde ein 0.42er mitgewinkt. sort ist stabil, die Rangfolge
  // des Servers innerhalb der Gruppen bleibt erhalten.
  const contractSuggestions = useMemo(() => {
    const items = inbox?.contract_suggestions ?? [];
    return [...items].sort((a, b) => Number(a.is_uncertain) - Number(b.is_uncertain));
  }, [inbox]);

  const firstUncertainSuggestionId =
    contractSuggestions.find((suggestion) => suggestion.is_uncertain)?.source_transaction_id ??
    null;

  const sections: ReviewSection[] = [];
  if (unclassifiedItems.length > 0)
    sections.push({ key: "unclassified", items: unclassifiedItems });
  if (duplicateItems.length > 0) sections.push({ key: "duplicates", items: duplicateItems });
  if (transferItems.length > 0) sections.push({ key: "transfers", items: transferItems });
  if (contractSuggestions.length > 0) {
    sections.push({ key: "contract-suggestions", items: contractSuggestions });
  }
  if (contractDuplicates.length > 0) {
    sections.push({ key: "contract-duplicates", items: contractDuplicates });
  }
  if (contractEndedChecks.length > 0) {
    sections.push({ key: "contract-ended", items: contractEndedChecks });
  }
  if (contractAmountChanges.length > 0) {
    sections.push({ key: "contract-amount-change", items: contractAmountChanges });
  }

  const header = (
    <View style={styles.header}>
      <View style={styles.headerRow}>
        <Text style={[styles.headerTitle, { color: theme.textPrimary }]}>Offene Fälle</Text>
        <Badge tone="warning" label={totalCount === 1 ? "1 Fall" : `${totalCount} Fälle`} />
      </View>
      <Text style={[styles.headerHint, { color: theme.textSecondary }]}>
        Unklassifizierte Umsätze, mögliche Dubletten und erkannte Verträge - jeder Fall wartet auf
        deine Bestätigung.
      </Text>

      <BookedDuplicatesPanel
        key={selectedHouseholdId}
        items={inbox?.booked_duplicates ?? []}
        total={inbox?.booked_duplicate_count ?? 0}
      />

      {actionError ? (
        <Card tone="warning" density="compact">
          <Text accessibilityRole="alert" style={[styles.bodyText, { color: theme.warning }]}>
            {actionError}
          </Text>
        </Card>
      ) : null}

      {isClassifyRunActive && classifyRun ? (
        <Card density="compact">
          <View style={styles.runRow}>
            <ActivityIndicator size="small" color={theme.positive} />
            <Text style={[styles.bodyText, { color: theme.textSecondary }]}>
              {classifyRun.processed_count} / {classifyRun.total_count} klassifiziert…
            </Text>
          </View>
          {classifyRun.is_stale ? (
            <Badge tone="warning" label="Seit über 10 Minuten keine Rückmeldung" />
          ) : null}
          <PrimaryButton
            label={classifyRun.cancel_requested ? "Abbruch angefordert…" : "Lauf abbrechen"}
            variant="secondary"
            size="compact"
            onPress={() => cancelClassifyRun.mutate(classifyRun.id)}
            disabled={cancelClassifyRun.isPending || classifyRun.cancel_requested}
          />
        </Card>
      ) : (
        <>
          {classifyRunStatus === "completed" && classifyRun ? (
            <Badge
              tone="positive"
              label={`${classifyRun.success_count} erfolgreich, ${classifyRun.error_count} ohne Kategorie`}
            />
          ) : null}
          {/* Fehlermeldungen des Dienstes koennen lang sein - als Karte statt
              als Pille, damit sie vollstaendig lesbar bleibt. */}
          {classifyRunStatus === "failed" ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.bodyText, { color: theme.warning }]}>
                Klassifizierung fehlgeschlagen: {classifyRun?.error_message ?? "Unbekannter Fehler"}
              </Text>
            </Card>
          ) : null}
          {classifyRunStatus === "cancelled" && classifyRun ? (
            <Badge
              tone="neutral"
              label={`Abgebrochen: ${classifyRun.processed_count} von ${classifyRun.total_count} verarbeitet`}
            />
          ) : null}
          {/* Auch nach einem beendeten Lauf erreichbar - vorher
              verdeckte das Ergebnis den Startknopf. */}
          {unclassifiedItems.length > 0 ? (
            <View style={styles.headerAction}>
              <PrimaryButton
                label="✨ Alle offenen Umsätze automatisch kategorisieren"
                onPress={() => startClassifyRun.mutate(undefined)}
                loading={startClassifyRun.isPending}
              />
              <Text style={[styles.footnote, { color: theme.textMuted }]}>
                Die KI schlägt Kategorien vor. Bestätigen musst du weiterhin selbst.
              </Text>
            </View>
          ) : null}
        </>
      )}
    </View>
  );

  return (
    <>
      <Stack.Screen options={{ title: "Review Inbox" }} />
      <ScreenContainer>
        <FlatList
          data={sections}
          keyExtractor={(section) => section.key}
          refreshControl={
            <RefreshControl refreshing={isFetching} onRefresh={() => void refetch()} />
          }
          contentContainerStyle={styles.listContent}
          ListHeaderComponent={header}
          renderItem={({ item: section }) => {
            if (section.key === "unclassified") {
              return (
                <View style={styles.group}>
                  <GroupHeading label="Unklassifizierte Umsätze" count={section.items.length} />
                  {section.items.map((uItem) => {
                    const pickedCatId = pickedCategories[uItem.transaction_id];
                    const createRule = !!createRuleMap[uItem.transaction_id];
                    const isPending = acceptMutation.isPending || correctMutation.isPending;
                    const isClassifying = classifyingId === uItem.transaction_id;

                    // ReviewItemUnclassified liefert vom Backend keine
                    // KI-Konfidenz mit. aiConfidenceMap deckt den Einzel-Tap-
                    // Pfad ab (siehe handleClassifyWithAI oben); aiDecisionsMap
                    // (aus GET /v1/ai/decisions) deckt zusaetzlich Buchungen ab,
                    // die per Batch-Lauf klassifiziert wurden, ohne dass hier
                    // einzeln getippt wurde.
                    const rawConfidence =
                      aiConfidenceMap[uItem.transaction_id] ??
                      aiDecisionsMap[uItem.transaction_id] ??
                      null;
                    const confidencePercent =
                      rawConfidence != null
                        ? Math.round(rawConfidence <= 1 ? rawConfidence * 100 : rawConfidence)
                        : null;

                    return (
                      <Card key={uItem.transaction_id}>
                        <View style={styles.cardHead}>
                          <View style={styles.cardHeadText}>
                            <Text
                              style={[styles.cardTitle, { color: theme.textPrimary }]}
                              numberOfLines={1}
                            >
                              {uItem.payee_label || "Unbekannter Empfänger"}
                            </Text>
                            <Text
                              style={[styles.cardMeta, { color: theme.textSecondary }]}
                              numberOfLines={1}
                            >
                              {formatDay(uItem.booked_date)}
                              {uItem.note ? ` • ${uItem.note}` : ""}
                            </Text>
                          </View>
                          <Money minorUnits={uItem.amount_minor} />
                        </View>

                        <View style={styles.suggestionRow}>
                          <Text
                            style={[
                              styles.bodyText,
                              styles.suggestionText,
                              { color: theme.textSecondary },
                            ]}
                            numberOfLines={2}
                          >
                            Vorschlag:{" "}
                            <Text style={{ color: theme.textPrimary, fontWeight: "700" }}>
                              {pickedCatId ? getCategoryName(pickedCatId) : "noch keine Kategorie"}
                            </Text>
                          </Text>
                          {confidencePercent !== null ? (
                            <Badge tone="positive" label={`KI ${confidencePercent} %`} />
                          ) : null}
                        </View>

                        <View style={styles.switchRow}>
                          <Text style={[styles.bodyText, { color: theme.textSecondary, flex: 1 }]}>
                            Künftig immer so zuordnen (Regel anlegen)
                          </Text>
                          <Switch
                            accessibilityRole="switch"
                            accessibilityLabel="Künftig immer so zuordnen"
                            value={createRule}
                            onValueChange={() => toggleCreateRule(uItem.transaction_id)}
                            trackColor={{ false: theme.surfaceHover, true: theme.positiveStrong }}
                            thumbColor={theme.onAccent}
                          />
                        </View>

                        <View style={styles.buttonRow}>
                          <View style={styles.buttonCell}>
                            <PrimaryButton
                              label="Bestätigen"
                              size="compact"
                              onPress={() => void handleAcceptUnclassified(uItem)}
                              loading={isPending}
                            />
                          </View>
                          <View style={styles.buttonCell}>
                            <PrimaryButton
                              label="Ändern"
                              variant="secondary"
                              size="compact"
                              onPress={() => openPicker(uItem.transaction_id, uItem.amount_minor)}
                            />
                          </View>
                        </View>

                        <PrimaryButton
                          label="✨ Mit KI kategorisieren"
                          variant="secondary"
                          size="compact"
                          onPress={() => void handleClassifyWithAI(uItem)}
                          loading={isClassifying}
                        />
                      </Card>
                    );
                  })}
                </View>
              );
            }

            if (section.key === "duplicates") {
              return (
                <View style={styles.group}>
                  <GroupHeading label="Mögliche Dubletten" count={section.items.length} />

                  {/* Sammelbestaetigung. Ein Import bringt Dubletten zu
                      Dutzenden; einzeln getippt bleibt die Liste liegen, und
                      dann ist auch die echte Dublette darin verloren. Auf dem
                      Handy wiegt das schwerer als am Rechner. */}
                  <View style={styles.bulkBar}>
                    <PrimaryButton
                      label={alleDublettenGewaehlt ? "Auswahl aufheben" : "Alle auswählen"}
                      variant="secondary"
                      size="compact"
                      onPress={() =>
                        setGewaehlteDubletten(
                          alleDublettenGewaehlt
                            ? new Set()
                            : new Set(section.items.map((d) => d.import_row_id)),
                        )
                      }
                    />
                    <PrimaryButton
                      label={`${gewaehlteDubletten.size} bestätigen`}
                      size="compact"
                      disabled={gewaehlteDubletten.size === 0}
                      loading={bulkDuplicatesMutation.isPending}
                      onPress={() => void handleBulkDuplicates()}
                    />
                  </View>

                  {section.items.map((dItem) => {
                    const gewaehlt = gewaehlteDubletten.has(dItem.import_row_id);
                    const gegen = dItem.duplicate_of;
                    return (
                      <Card key={dItem.import_row_id}>
                        <Pressable
                          onPress={() =>
                            setGewaehlteDubletten((bisher) => {
                              const naechste = new Set(bisher);
                              if (naechste.has(dItem.import_row_id)) {
                                naechste.delete(dItem.import_row_id);
                              } else {
                                naechste.add(dItem.import_row_id);
                              }
                              return naechste;
                            })
                          }
                          accessibilityRole="checkbox"
                          accessibilityState={{ checked: gewaehlt }}
                          accessibilityLabel={`Zeile ${dItem.payee_raw || "ohne Empfänger"} auswählen`}
                          style={styles.cardHead}
                        >
                          <View
                            style={[
                              styles.auswahlKasten,
                              {
                                borderColor: gewaehlt ? theme.accent : theme.border,
                                backgroundColor: gewaehlt ? theme.accent : "transparent",
                              },
                            ]}
                          >
                            {gewaehlt ? <Text style={styles.auswahlHaken}>✓</Text> : null}
                          </View>
                          <View style={styles.cardHeadText}>
                            <Text
                              style={[styles.cardTitle, { color: theme.textPrimary }]}
                              numberOfLines={1}
                            >
                              {dItem.payee_raw || "Import-Zeile"}
                            </Text>
                            <Text
                              style={[styles.cardMeta, { color: theme.textSecondary }]}
                              numberOfLines={2}
                            >
                              {dItem.booked_date ? formatDay(dItem.booked_date) : "Kein Datum"} •{" "}
                              {dItem.purpose || "Verwendungszweck"}
                            </Text>
                          </View>
                          {dItem.amount_minor !== null ? (
                            <Money minorUnits={dItem.amount_minor} />
                          ) : null}
                        </Pressable>

                        {/* Ohne diesen Block ist die Frage nicht zu beantworten:
                            die Liste verlangte "Dublette oder nicht", ohne zu
                            sagen, wovon. Am Telefon ist das Nachschlagen in
                            "Alle Buchungen" noch muehsamer als am Rechner. */}
                        <View style={[styles.gegenbuchung, { borderColor: theme.border }]}>
                          <CardLabel>Bereits vorhanden</CardLabel>
                          {gegen ? (
                            <>
                              <Text
                                style={[styles.cardTitle, { color: theme.textPrimary }]}
                                numberOfLines={1}
                              >
                                {gegen.payee_label || "Ohne Empfänger"}
                              </Text>
                              <Text
                                style={[styles.cardMeta, { color: theme.textSecondary }]}
                                numberOfLines={3}
                              >
                                {gegen.booked_date ? formatDay(gegen.booked_date) : "Kein Datum"}
                                {gegen.account_name ? ` • ${gegen.account_name}` : ""}
                                {gegen.purpose
                                  ? `
${gegen.purpose}`
                                  : ""}
                              </Text>
                            </>
                          ) : (
                            <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                              Die verglichene Buchung steht nicht mehr im Bestand - hier ist nichts
                              mehr doppelt.
                            </Text>
                          )}
                        </View>

                        <PrimaryButton
                          label="Als Dublette bestätigen"
                          size="compact"
                          onPress={() => void handleAcceptDuplicate(dItem)}
                          loading={acceptMutation.isPending}
                        />
                        <PrimaryButton
                          label="Doch übernehmen"
                          variant="secondary"
                          size="compact"
                          onPress={() => setDuplicateForCategory(dItem)}
                          loading={correctMutation.isPending}
                        />
                      </Card>
                    );
                  })}
                </View>
              );
            }

            if (section.key === "transfers") {
              return (
                <View style={styles.group}>
                  <GroupHeading label="Umbuchungen / Transfers" count={section.items.length} />
                  {section.items.map((tItem) => (
                    <Card key={tItem.import_row_id}>
                      <View style={styles.cardHead}>
                        <View style={styles.cardHeadText}>
                          <Text
                            style={[styles.cardTitle, { color: theme.textPrimary }]}
                            numberOfLines={1}
                          >
                            {tItem.payee_label || "Unbekannt"}
                          </Text>
                          <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                            {formatDay(tItem.booked_date)}
                          </Text>
                        </View>
                        <Money minorUnits={tItem.amount_minor} />
                      </View>

                      <PrimaryButton
                        label="Als Transfer verknüpfen"
                        size="compact"
                        onPress={() => void handleAcceptTransfer(tItem)}
                        loading={acceptMutation.isPending}
                      />
                    </Card>
                  ))}
                </View>
              );
            }

            if (section.key === "contract-suggestions") {
              return (
                <View style={styles.group}>
                  <GroupHeading label="Erkannte Verträge" count={section.items.length} />
                  <Text style={[styles.groupHint, { color: theme.textSecondary }]}>
                    Zu jedem Vorschlag steht, warum wir ihn für einen Vertrag halten. Bitte die
                    Begründung prüfen, bevor du bestätigst.
                  </Text>
                  {section.items.map((sItem) => {
                    const percent = Math.round(sItem.detection_confidence * 100);
                    const lastStepIndex = sItem.amount_steps.length - 1;
                    return (
                      <View key={sItem.source_transaction_id} style={styles.group}>
                        {/* Die Trennung steht vor dem ersten unsicheren
                            Vorschlag: eine Plakette allein uebersieht man beim
                            Durchwischen. */}
                        {sItem.source_transaction_id === firstUncertainSuggestionId ? (
                          <Card tone="warning" density="compact">
                            <Text style={[styles.bodyText, { color: theme.warning }]}>
                              Ab hier unsicher (40-55 % Erkennungsgüte): kann ein Vertrag sein -
                              oder eine Reihe zufällig ähnlicher Zahlungen.
                            </Text>
                          </Card>
                        ) : null}
                        <Card accentEdge={sItem.is_uncertain ? "warning" : "positive"}>
                          <View style={styles.cardHead}>
                            <View style={styles.cardHeadText}>
                              <Text
                                style={[styles.cardTitle, { color: theme.textPrimary }]}
                                numberOfLines={1}
                              >
                                {sItem.display_name}
                              </Text>
                              <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                                {CONTRACT_TYPE_LABELS[sItem.contract_type]} •{" "}
                                {FREQUENCY_LABELS[sItem.frequency]} • {sItem.occurrences_count}{" "}
                                Zahlungen
                              </Text>
                              {sItem.contract_key && sItem.contract_key_source ? (
                                <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                                  Vertragsnummer {sItem.contract_key} (
                                  {CONTRACT_KEY_SOURCE_LABELS[sItem.contract_key_source]})
                                </Text>
                              ) : null}
                              {sItem.installments_total !== null ? (
                                <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                                  Rate {sItem.installments_paid ?? "?"} von{" "}
                                  {sItem.installments_total}
                                </Text>
                              ) : null}
                            </View>
                            {/* Betraege liegen immer positiv vor, die Richtung
                                steht getrennt - Ausgaben hier negativ zeigen. */}
                            <Money
                              minorUnits={signedAmountMinor(sItem.amount_minor, sItem.direction)}
                            />
                          </View>

                          <Badge
                            tone={sItem.is_uncertain ? "warning" : "positive"}
                            label={
                              sItem.is_uncertain
                                ? `Unsicher • ${percent} % Erkennungsgüte`
                                : `${percent} % Erkennungsgüte`
                            }
                          />

                          {/* Preisstufen: EIN Vertrag mit Verlauf, nicht zwei. */}
                          {sItem.amount_steps.length > 1 ? (
                            <View style={styles.stepBlock}>
                              <Text style={[styles.bodyText, { color: theme.textPrimary }]}>
                                Preis geändert - daraus entsteht ein einziger Vertrag mit
                                Betragsverlauf:
                              </Text>
                              {sItem.amount_steps.map((step, index) => (
                                <View key={step.first_date} style={styles.stepRow}>
                                  <Text
                                    style={[
                                      styles.cardMeta,
                                      { color: theme.textSecondary, flex: 1 },
                                    ]}
                                  >
                                    {amountStepPeriod(step, index === lastStepIndex)}
                                  </Text>
                                  <Money
                                    minorUnits={signedAmountMinor(
                                      step.amount_minor,
                                      sItem.direction,
                                    )}
                                  />
                                </View>
                              ))}
                            </View>
                          ) : null}

                          <ScoreBreakdown score={sItem.score_breakdown} />

                          <PrimaryButton
                            label="Als Vertrag übernehmen"
                            size="compact"
                            onPress={() => setContractPickerSuggestion(sItem)}
                            loading={acceptMutation.isPending}
                          />
                          <PrimaryButton
                            label="Kein Vertrag"
                            variant="secondary"
                            size="compact"
                            onPress={() => handleDismissContractSuggestion(sItem)}
                            disabled={acceptMutation.isPending}
                          />
                        </Card>
                      </View>
                    );
                  })}
                </View>
              );
            }

            if (section.key === "contract-duplicates") {
              return (
                <View style={styles.group}>
                  <GroupHeading label="Vermutlich derselbe Vertrag" count={section.items.length} />
                  <Text style={[styles.groupHint, { color: theme.textSecondary }]}>
                    Gleicher Empfänger, gleicher Rhythmus - und der ältere Eintrag endet, bevor der
                    jüngere beginnt.
                  </Text>
                  {section.items.map((dItem) => (
                    <Card key={dItem.target_id}>
                      <View style={styles.cardHead}>
                        <View style={styles.cardHeadText}>
                          <Text
                            style={[styles.cardTitle, { color: theme.textPrimary }]}
                            numberOfLines={1}
                          >
                            {dItem.payee_label}
                          </Text>
                          <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                            {FREQUENCY_LABELS[dItem.frequency]} • {dItem.gap_days} Tage dazwischen
                          </Text>
                        </View>
                        {dItem.amount_delta_minor === 0 ? (
                          <Text style={[styles.cardTitle, { color: theme.textSecondary }]}>
                            unverändert
                          </Text>
                        ) : (
                          <Money minorUnits={dItem.amount_delta_minor} showPlusSign />
                        )}
                      </View>

                      <View style={styles.stepRow}>
                        <Text style={[styles.cardMeta, { color: theme.textSecondary, flex: 1 }]}>
                          Alt: {dItem.older.name}
                          {dItem.older.last_seen_date
                            ? ` bis ${formatDay(dItem.older.last_seen_date)}`
                            : ""}
                        </Text>
                        <Money
                          minorUnits={signedAmountMinor(dItem.older.amount_minor, dItem.direction)}
                        />
                      </View>
                      <View style={styles.stepRow}>
                        <Text style={[styles.cardMeta, { color: theme.textSecondary, flex: 1 }]}>
                          Neu: {dItem.newer.name}
                          {dItem.newer.first_seen_date
                            ? ` seit ${formatDay(dItem.newer.first_seen_date)}`
                            : ""}
                        </Text>
                        <Money
                          minorUnits={signedAmountMinor(dItem.newer.amount_minor, dItem.direction)}
                        />
                      </View>

                      <View style={styles.buttonRow}>
                        <View style={styles.buttonCell}>
                          <PrimaryButton
                            label="Zusammenführen"
                            size="compact"
                            onPress={() => handleContractDuplicate(dItem, false)}
                            loading={acceptMutation.isPending}
                          />
                        </View>
                        <View style={styles.buttonCell}>
                          <PrimaryButton
                            label="Sind verschieden"
                            variant="secondary"
                            size="compact"
                            onPress={() => handleContractDuplicate(dItem, true)}
                            disabled={acceptMutation.isPending}
                          />
                        </View>
                      </View>
                    </Card>
                  ))}
                </View>
              );
            }

            if (section.key === "contract-amount-change") {
              return (
                <View style={styles.group}>
                  <GroupHeading label="Stimmt dieser neue Betrag?" count={section.items.length} />
                  <Text style={[styles.groupHint, { color: theme.textSecondary }]}>
                    Der Abgleich hat den neuen Betrag bereits übernommen. Er kann aber nicht
                    unterscheiden, ob der Anbieter den Preis wirklich geändert hat oder ob eine
                    fremde Zahlung desselben Empfängers versehentlich auf diesen Vertrag gebucht
                    wurde. Stimmt der Betrag nicht, lässt er sich am Vertrag korrigieren.
                  </Text>
                  {section.items.map((aItem) => (
                    <Card
                      key={aItem.event_id}
                      accentEdge={aItem.delta_minor < 0 ? "positive" : "warning"}
                    >
                      <View style={styles.cardHead}>
                        <View style={styles.cardHeadText}>
                          <Text
                            style={[styles.cardTitle, { color: theme.textPrimary }]}
                            numberOfLines={1}
                          >
                            {aItem.name}
                          </Text>
                          <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                            {aItem.provider_label && aItem.provider_label !== aItem.name
                              ? `${aItem.provider_label} • `
                              : ""}
                            {FREQUENCY_LABELS[aItem.frequency] ?? aItem.frequency}
                          </Text>
                        </View>
                        {/* Betraege liegen immer positiv in der DB, die Richtung
                            steht getrennt - Ausgaben hier negativ darstellen. */}
                        <Money
                          minorUnits={signedAmountMinor(aItem.new_amount_minor, aItem.direction)}
                        />
                      </View>

                      <Badge
                        tone={aItem.delta_minor < 0 ? "positive" : "warning"}
                        label={`${amountChangeVerb(aItem)} · ${formatSignedPercent(aItem.delta_pct)}`}
                      />

                      <View style={styles.stepRow}>
                        <Text style={[styles.bodyText, { color: theme.textSecondary }]}>
                          Bisher
                        </Text>
                        <Money
                          minorUnits={signedAmountMinor(aItem.old_amount_minor, aItem.direction)}
                          tone="muted"
                        />
                      </View>
                      <View style={styles.stepRow}>
                        <Text style={[styles.bodyText, { color: theme.textSecondary }]}>Neu</Text>
                        <Money
                          minorUnits={signedAmountMinor(aItem.new_amount_minor, aItem.direction)}
                        />
                      </View>

                      <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                        {`Erstmals am ${formatDay(aItem.occurred_on)} gebucht`}
                      </Text>

                      <PrimaryButton
                        label="Stimmt so"
                        size="compact"
                        onPress={() => void handleAmountChangeCheck(aItem)}
                        disabled={acceptMutation.isPending}
                      />
                    </Card>
                  ))}
                </View>
              );
            }

            return (
              <View style={styles.group}>
                <GroupHeading label="Läuft dieser Vertrag noch?" count={section.items.length} />
                <Text style={[styles.groupHint, { color: theme.textSecondary }]}>
                  Für diese Verträge kam mehrfach keine passende Zahlung mehr an. Bis zur Antwort
                  zählen sie unverändert in den Fixkosten mit.
                </Text>
                {section.items.map((cItem) => (
                  <Card key={cItem.contract_id} accentEdge="warning">
                    <View style={styles.cardHead}>
                      <View style={styles.cardHeadText}>
                        <Text
                          style={[styles.cardTitle, { color: theme.textPrimary }]}
                          numberOfLines={1}
                        >
                          {cItem.name}
                        </Text>
                        <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                          {cItem.provider_label && cItem.provider_label !== cItem.name
                            ? `${cItem.provider_label} • `
                            : ""}
                          {FREQUENCY_LABELS[cItem.frequency] ?? cItem.frequency}
                        </Text>
                      </View>
                      {/* Betraege liegen immer positiv in der DB, die Richtung
                          steht getrennt - Ausgaben hier negativ darstellen. */}
                      <Money minorUnits={signedAmountMinor(cItem.amount_minor, cItem.direction)} />
                    </View>

                    <Badge
                      tone="warning"
                      label={`${cItem.missed_periods} Fälligkeit(en) ohne Zahlung`}
                    />
                    <Text style={[styles.cardMeta, { color: theme.textSecondary }]}>
                      {cItem.last_seen_date
                        ? `Letzte passende Zahlung am ${formatDay(cItem.last_seen_date)}`
                        : "Seit Anlage des Vertrags keine passende Zahlung gefunden"}
                      {` • nächste erwartete Fälligkeit ${formatDay(cItem.next_due_date)}`}
                    </Text>

                    <View style={styles.buttonRow}>
                      {CONTRACT_ENDED_OPTIONS.map((option) => (
                        <View key={option.action} style={styles.buttonCell}>
                          {/* Alle drei Antworten gleich gewichtet - siehe
                              CONTRACT_ENDED_OPTIONS. */}
                          <PrimaryButton
                            label={option.label}
                            variant="secondary"
                            size="compact"
                            onPress={() => handleContractEndedCheck(cItem, option)}
                            disabled={acceptMutation.isPending}
                          />
                        </View>
                      ))}
                    </View>
                  </Card>
                ))}
              </View>
            );
          }}
          ListEmptyComponent={
            isLoading ? (
              <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={56} />
            ) : isError ? (
              <InlineQueryState
                status="error"
                title="Review-Inbox konnte nicht geladen werden"
                onRetry={() => void refetch()}
              />
            ) : (inbox?.booked_duplicate_count ?? 0) > 0 ? null : (
              <EmptyState
                title="Alles erledigt!"
                description="Keine unklassifizierten Umsätze oder Dubletten in der Review-Inbox."
              />
            )
          }
        />

        {/* Category Picker Modal */}
        <CategoryPickerModal
          visible={pickerVisible}
          groups={categoryGroups ?? []}
          value={selectedItem?.currentCategoryId ?? null}
          amountMinor={selectedItem?.amountMinor ?? null}
          onChange={handleCategorySelect}
          onClose={() => setPickerVisible(false)}
        />

        {/* Kategorienauswahl fuer eine faelschlich gemeldete Dublette */}
        <CategoryPickerModal
          visible={duplicateForCategory !== null}
          groups={categoryGroups ?? []}
          value={null}
          amountMinor={duplicateForCategory?.amount_minor ?? null}
          onChange={(catId) => void handleCommitDuplicate(catId)}
          onClose={() => setDuplicateForCategory(null)}
          title="Kategorie für die Buchung wählen"
        />

        {/* Category Picker fuer erkannte Vertraege (Vertrag anlegen) */}
        <CategoryPickerModal
          visible={contractPickerSuggestion !== null}
          groups={categoryGroups ?? []}
          value={contractPickerSuggestion?.suggested_category_id ?? null}
          onChange={(catId) => void handleAcceptContractCategory(catId)}
          onClose={() => setContractPickerSuggestion(null)}
          title="Kategorie für den Vertrag wählen"
        />
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  listContent: {
    padding: space.lg,
    paddingBottom: space.xxxl,
    gap: space.xl,
  },
  header: {
    gap: space.md,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  headerTitle: {
    fontSize: typeScale.title,
    fontWeight: "800",
    letterSpacing: -0.4,
  },
  headerHint: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  headerAction: {
    gap: space.xs,
  },
  footnote: {
    fontSize: typeScale.micro,
  },
  runRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  group: {
    gap: space.md,
  },
  groupHeading: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  groupHint: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  bulkBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    marginBottom: space.sm,
  },
  auswahlKasten: {
    width: 22,
    height: 22,
    borderRadius: 6,
    borderWidth: 2,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 2,
  },
  auswahlHaken: {
    color: "#fff",
    fontSize: 14,
    fontWeight: "800",
    lineHeight: 16,
  },
  gegenbuchung: {
    borderWidth: 1,
    borderRadius: 10,
    padding: space.md,
    gap: 4,
    marginTop: space.sm,
    marginBottom: space.sm,
  },
  cardHead: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: space.md,
  },
  cardHeadText: {
    flex: 1,
    gap: space.hair,
  },
  cardTitle: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  cardMeta: {
    fontSize: typeScale.caption,
  },
  bodyText: {
    fontSize: typeScale.body,
  },
  suggestionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  suggestionText: {
    flex: 1,
  },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    minHeight: 44,
  },
  buttonRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  buttonCell: {
    flex: 1,
  },
  stepBlock: {
    gap: space.xs,
  },
  stepRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  scoreList: {
    gap: space.xs,
  },
  scoreRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  scoreLabel: {
    fontSize: typeScale.caption,
    flex: 1,
  },
  scoreTrack: {
    width: 64,
    height: 6,
    borderRadius: radii.xs,
    overflow: "hidden",
  },
  scoreFill: {
    height: "100%",
  },
  scoreValue: {
    fontSize: typeScale.caption,
    fontWeight: "600",
    width: 44,
    textAlign: "right",
  },
});
